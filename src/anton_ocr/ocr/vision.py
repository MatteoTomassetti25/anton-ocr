"""Backend OCR vision per le pagine senza testo nativo.

Due percorsi, scelti in base all'hardware:

* **MLX** su Apple Silicon — inferenza nativa su Neural Engine e GPU Metal.
  Vincolo importante: lo stream Metal in MLX è per-thread, quindi tutte le
  chiamate devono passare da un unico thread dedicato. Un executor con
  ``max_workers=1`` lo garantisce; senza, gli stream entrano in conflitto.
* **Ollama** su NVIDIA/CPU.

Entrambi i backend sono opzionali: se non installati, ``VisionOCR.available`` è
``False`` e la pipeline prosegue con il solo testo nativo invece di interrompersi.
"""

from __future__ import annotations

import base64
import io
import logging
import platform
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

log = logging.getLogger(__name__)

# GLM-OCR espone modalità di comprensione diverse a seconda del prompt.
TASK_PROMPTS = {
    "text": "Text Recognition:",
    "formula": "Formula Recognition:",
    "table": "Table Recognition:",
    "visual": "Text Recognition:",
}

# Frasi che indicano che il modello ha restituito il prompt invece del contenuto.
_PROMPT_ECHO_MARKERS = (
    "text recognition:",
    "formula recognition:",
    "table recognition:",
    "output only extracted",
    "describe in detail",
    "<|begin_of_image|>",
)


def clean_ocr_output(raw: str) -> str:
    """Rimuove il wrapper markdown e scarta l'eco del prompt.

    L'eco va scartata, non salvata: se finisse nel documento, il testo del prompt
    verrebbe scambiato per contenuto e il rilevamento PII lo analizzerebbe come
    tale.
    """
    text = re.sub(r"^```(?:markdown)?\n?", "", raw.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\n?```$", "", text.strip()).strip()
    head = text[:400].lower()
    if any(marker in head for marker in _PROMPT_ECHO_MARKERS):
        return ""
    return text


def is_apple_silicon() -> bool:
    return platform.machine() == "arm64" and sys.platform == "darwin"


def has_nvidia() -> bool:
    try:
        return subprocess.run(["nvidia-smi"], capture_output=True, timeout=3).returncode == 0
    except Exception:
        return False


@dataclass
class VisionConfig:
    mlx_model: str = "mlx-community/GLM-OCR-8bit"
    ollama_model: str = "glm-ocr:latest"
    ollama_host: str = "http://localhost:11434"
    num_ctx: int = 16384
    max_tokens: int = 2048
    backend: str = "auto"  # auto | mlx | ollama | none


class VisionOCR:
    def __init__(self, config: VisionConfig | None = None) -> None:
        self.config = config or VisionConfig()
        self.backend = self._resolve(self.config.backend)
        self._model = None
        self._processor = None
        self._load_lock = threading.Lock()
        # Un solo worker: lo stream Metal di MLX è per-thread.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx_worker")

    @staticmethod
    def _resolve(backend: str) -> str:
        if backend != "auto":
            return backend
        if is_apple_silicon():
            try:
                import mlx_vlm  # noqa: F401

                return "mlx"
            except ImportError:
                pass
        try:
            import ollama  # noqa: F401

            return "ollama"
        except ImportError:
            return "none"

    @property
    def available(self) -> bool:
        return self.backend in ("mlx", "ollama")

    # ── MLX ──

    def _load_mlx(self) -> None:
        with self._load_lock:
            if self._model is not None:
                return
            from mlx_vlm import load

            log.info("Caricamento modello MLX: %s", self.config.mlx_model)
            self._model, self._processor = load(self.config.mlx_model)

    def _ocr_mlx(self, image_b64: str, task: str) -> str:
        import os
        import tempfile

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        self._load_mlx()
        prompt_text = TASK_PROMPTS.get(task, TASK_PROMPTS["visual"])

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        try:
            tmp.write(base64.b64decode(image_b64))
            tmp.close()
            config = load_config(self.config.mlx_model)
            prompt = apply_chat_template(self._processor, config, prompt_text, num_images=1)
            result = generate(
                self._model,
                self._processor,
                prompt,
                image=tmp.name,
                max_tokens=self.config.max_tokens,
                verbose=False,
            )
        finally:
            os.unlink(tmp.name)

        raw = result.text if hasattr(result, "text") else str(result)
        return clean_ocr_output(raw)

    # ── Ollama ──

    def _ocr_ollama(self, image_b64: str, task: str) -> str:
        import ollama

        client = ollama.Client(host=self.config.ollama_host)
        response = client.generate(
            model=self.config.ollama_model,
            prompt=TASK_PROMPTS.get(task, TASK_PROMPTS["visual"]),
            images=[image_b64],
            options={"num_ctx": self.config.num_ctx, "temperature": 0},
        )
        return clean_ocr_output(response.response)

    # ── API ──

    def ocr_image(self, image_b64: str, task: str = "visual") -> str:
        if not self.available:
            return ""
        if self.backend == "mlx":
            return self._executor.submit(self._ocr_mlx, image_b64, task).result()
        return self._ocr_ollama(image_b64, task)

    def unload(self) -> None:
        """Libera la memoria del modello.

        Su Apple Silicon la memoria è unificata: non liberarla dopo il job
        significa sottrarla al resto del sistema.
        """
        if self.backend == "mlx" and self._model is not None:
            import mlx.core as mx

            self._model = None
            self._processor = None
            mx.clear_cache()
            log.info("Memoria MLX liberata")
        elif self.backend == "ollama":
            try:
                import requests

                requests.post(
                    f"{self.config.ollama_host}/api/generate",
                    json={"model": self.config.ollama_model, "keep_alive": 0},
                    timeout=10,
                )
                log.info("Modello Ollama scaricato dalla VRAM")
            except Exception as exc:
                log.warning("Scaricamento Ollama non riuscito: %s", exc)

    def shutdown(self) -> None:
        self.unload()
        self._executor.shutdown(wait=True)


def page_to_b64(image, quality: int = 85) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode()
