"""Caricamento della configurazione.

I default puntano a ``~/.anton-ocr/``: nessun percorso personale è codificato nel
sorgente, e il repository non contiene percorsi della macchina di sviluppo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOME = Path.home() / ".anton-ocr"


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


@dataclass
class Config:
    input_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "input")
    output_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "output")
    archive_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "elaborati")
    vault_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "vault")
    review_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "review")
    quarantine_dir: Path = field(default_factory=lambda: DEFAULT_HOME / "quarantine")
    audit_path: Path = field(default_factory=lambda: DEFAULT_HOME / "audit.jsonl")
    signing_key_path: Path = field(default_factory=lambda: DEFAULT_HOME / "signing_key.pem")

    model: str = "glm-ocr:latest"
    mlx_model: str = "mlx-community/GLM-OCR-8bit"
    ollama_host: str = "http://localhost:11434"
    num_ctx: int = 16384
    dpi: int = 150
    text_threshold: int = 50
    ocr_concurrency: int = 3
    chunk_size: int = 10
    idle_vram_timeout: int = 300

    policy_file: Path | None = None
    sealed: bool = True
    ner_backend: str = "none"
    ner_model: str = "urchade/gliner_multi-v2.1"
    extractor_backend: str = "auto"

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        cfg = cls()
        candidates = [Path(path)] if path else [Path.cwd() / "config.env", DEFAULT_HOME / "config.env"]
        source = next((p for p in candidates if p and p.exists()), None)
        if source is None:
            return cfg

        raw: dict[str, str] = {}
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            raw[key.strip()] = value.strip()

        path_fields = {
            "INPUT_DIR": "input_dir",
            "OUTPUT_DIR": "output_dir",
            "ARCHIVE_DIR": "archive_dir",
            "VAULT_DIR": "vault_dir",
            "REVIEW_DIR": "review_dir",
            "QUARANTINE_DIR": "quarantine_dir",
            "AUDIT_PATH": "audit_path",
            "SIGNING_KEY_PATH": "signing_key_path",
        }
        for env_key, attr in path_fields.items():
            if env_key in raw:
                setattr(cfg, attr, _expand(raw[env_key]))

        int_fields = {
            "NUM_CTX": "num_ctx",
            "DPI": "dpi",
            "TEXT_THRESHOLD": "text_threshold",
            "OCR_CONCURRENCY": "ocr_concurrency",
            "CHUNK_SIZE": "chunk_size",
            "IDLE_VRAM_TIMEOUT": "idle_vram_timeout",
        }
        for env_key, attr in int_fields.items():
            if env_key in raw:
                setattr(cfg, attr, int(raw[env_key]))

        str_fields = {
            "MODEL": "model",
            "MLX_MODEL": "mlx_model",
            "OLLAMA_HOST": "ollama_host",
            "NER_BACKEND": "ner_backend",
            "NER_MODEL": "ner_model",
            "EXTRACTOR_BACKEND": "extractor_backend",
        }
        for env_key, attr in str_fields.items():
            if env_key in raw:
                setattr(cfg, attr, raw[env_key])

        if "POLICY_FILE" in raw:
            cfg.policy_file = _expand(raw["POLICY_FILE"])
        if "SEALED" in raw:
            cfg.sealed = raw["SEALED"].strip().lower() in ("1", "true", "yes", "on")

        return cfg

    def ensure_dirs(self) -> None:
        for directory in (
            self.input_dir,
            self.output_dir,
            self.archive_dir,
            self.vault_dir,
            self.review_dir,
            self.quarantine_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
