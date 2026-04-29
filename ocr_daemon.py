#!/usr/bin/env python3
"""
Anton OCR Daemon v3
High-performance async pipeline:
  - asyncio.gather + Semaphore(3) for concurrent OCR
  - Per-page intelligent tiering (native text vs glm-ocr vision)
  - Lazy chunked image loading (CHUNK_SIZE pages at a time)
  - Apple Silicon: MLX-VLM native inference (Neural Engine + GPU Metal)
  - NVIDIA/CPU: Ollama backend
  - Idle VRAM unload after 5 min of inactivity
  - Graceful shutdown (completes current page before stopping)
"""

import asyncio
import os, sys, time, gc, re, platform, shutil, subprocess, base64, io
import threading
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pdf2image import convert_from_path, pdfinfo_from_path
import fitz          # PyMuPDF — fast page-level analysis
import ollama        # AsyncClient for concurrent calls (NVIDIA/CPU backend)
import requests as req

# MLX-VLM — Apple Silicon native inference
_mlx_model = None
_mlx_processor = None
_MLX_HF_MODEL = "mlx-community/GLM-OCR-8bit"  # 8-bit: best quality/VRAM ratio

def _load_mlx_model():
    """Lazily load MLX model on first use."""
    global _mlx_model, _mlx_processor
    if _mlx_model is not None:
        return
    from mlx_vlm import load
    log(f"Caricamento MLX model: {_MLX_HF_MODEL} (primo avvio, attendi...)")
    _mlx_model, _mlx_processor = load(_MLX_HF_MODEL)
    log("MLX model caricato nel Neural Engine/GPU Metal.")

def _unload_mlx_model():
    """Release MLX model from unified memory."""
    global _mlx_model, _mlx_processor
    import mlx.core as mx
    _mlx_model = None
    _mlx_processor = None
    mx.clear_cache()
    log("MLX memory liberata (mx.clear_cache).")

# ─────────────────── CONFIGURATION ───────────────────
def load_config():
    cfg = {
        "INPUT_DIR":      str(Path(__file__).parent / "input"),
        "OUTPUT_DIR":     "/Users/cmdhro/Matteo/wikiAnton/Università",
        "ARCHIVE_DIR":    str(Path(__file__).parent / "elaborati"),
        "MODEL":          "glm-ocr:latest",
        "DPI":            "150",
        "NUM_CTX":        "16384",
        "OLLAMA_HOST":    "http://localhost:11434",
        "TEXT_THRESHOLD": "50",
        "CHUNK_SIZE":     "10",
        "OCR_CONCURRENCY":"3",
        "IDLE_VRAM_TIMEOUT": "300",
    }
    config_path = Path(__file__).parent / "config.env"
    if config_path.exists():
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().replace("~", str(Path.home()))
    return cfg

CFG             = load_config()
INPUT_DIR       = Path(CFG["INPUT_DIR"])
OUTPUT_DIR      = Path(CFG["OUTPUT_DIR"])
ARCHIVE_DIR     = Path(CFG["ARCHIVE_DIR"])
MODEL_ID        = CFG["MODEL"]
DPI             = int(CFG["DPI"])
NUM_CTX         = int(CFG["NUM_CTX"])
OLLAMA_HOST     = CFG["OLLAMA_HOST"]
TEXT_THRESHOLD  = int(CFG["TEXT_THRESHOLD"])
CHUNK_SIZE      = int(CFG["CHUNK_SIZE"])
OCR_CONCURRENCY = int(CFG["OCR_CONCURRENCY"])
IDLE_VRAM_TIMEOUT = int(CFG["IDLE_VRAM_TIMEOUT"])

# ─────────────────── HARDWARE DETECTION ───────────────
IS_APPLE_SILICON = platform.machine() == "arm64" and sys.platform == "darwin"
IS_NVIDIA = False
try:
    IS_NVIDIA = subprocess.run(
        ["nvidia-smi"], capture_output=True, timeout=3
    ).returncode == 0
except Exception:
    pass

# Apple Silicon → MLX native inference (Neural Engine + GPU Metal, unified memory)
# NVIDIA/CPU    → Ollama backend
USE_MLX = IS_APPLE_SILICON
KEEP_ALIVE = 300  # Only used by Ollama fallback path

# ─────────────────── GLOBALS ──────────────────────────
_loop: asyncio.AbstractEventLoop = None
_pdf_queue: asyncio.Queue       = None
_shutdown_event: asyncio.Event  = None
_ocr_semaphore: asyncio.Semaphore = None
_last_job_time: float           = None
_model_loaded: bool             = False

# Math/table patterns that trigger vision OCR even on text-rich pages
_COMPLEX_RE = re.compile(
    r"[∫∑∂∇∈∉≤≥≠±∞∀∃√∆]|"      # math symbols
    r"\\frac|\\int|\\sum|"       # LaTeX fragments
    r"\|.*?\|"                   # matrix/determinant notation
)

def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str):
    print(f"[{ts()}] {msg}", flush=True)

# ─────────────────── NOTIFICATIONS ───────────────────
def send_notification(title: str, msg: str):
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                check=False
            )
    except Exception:
        pass

# ─────────────────── VRAM MANAGEMENT ─────────────────
def _unload_vram_sync():
    """Unload model from memory (MLX or Ollama depending on hardware)."""
    global _model_loaded
    if USE_MLX:
        try:
            _unload_mlx_model()
        except Exception as e:
            log(f"WARN MLX unload: {e}")
    else:
        try:
            req.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": MODEL_ID, "keep_alive": 0},
                timeout=10
            )
            log(f"Ollama VRAM liberata: {MODEL_ID} scaricato.")
        except Exception as e:
            log(f"WARN Ollama unload: {e}")
    _model_loaded = False

async def _unload_vram_async():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _unload_vram_sync)

# ─────────────────── TIERING LOGIC ───────────────────
def classify_pages(filepath: str) -> list[str]:
    """
    Pre-scan all pages with PyMuPDF (fast, low-RAM).
    Returns a list with "native" or "vision" per page.
    """
    doc = fitz.open(filepath)
    result = []
    for page in doc:
        text = page.get_text()
        if len(text.strip()) >= TEXT_THRESHOLD and not _COMPLEX_RE.search(text):
            result.append("native")
        else:
            result.append("vision")
    doc.close()
    return result

def extract_native_text_page(filepath: str, page_idx: int) -> str:
    """Extract native text from a single page."""
    doc = fitz.open(filepath)
    text = doc[page_idx].get_text()
    doc.close()
    return text

# ─────────────────── IMAGE UTILITIES ─────────────────
def page_to_b64(page) -> str:
    buf = io.BytesIO()
    page.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()

# ─────────────────── ASYNC OCR ───────────────────────
def _ocr_page_mlx_sync(b64: str) -> str:
    """MLX native inference — runs in thread executor (MLX is not async).
    mlx-vlm v0.4.x signature: generate(model, processor, prompt, image=path)
    """
    import tempfile, os as _os
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    _load_mlx_model()  # Lazy load on first call

    # mlx-vlm 0.4.x needs a file path for 'image', not a PIL object
    img_bytes = base64.b64decode(b64)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        tmp.write(img_bytes)
        tmp.close()

        config = load_config(_MLX_HF_MODEL)
        prompt = apply_chat_template(
            _mlx_processor, config,
            "Text Recognition:", num_images=1
        )
        # v0.4.x: (model, processor, prompt, image=path)
        result = generate(
            _mlx_model, _mlx_processor,
            prompt,
            image=tmp.name,
            max_tokens=2048,
            verbose=False
        )
    finally:
        _os.unlink(tmp.name)

    # GenerationResult object — extract .text attribute if present
    return result.text if hasattr(result, "text") else str(result)

async def ocr_page_async(client: ollama.AsyncClient, b64: str, page_num: int, total: int) -> str:
    """Single-page OCR — MLX on Apple Silicon, Ollama on NVIDIA/CPU."""
    async with _ocr_semaphore:
        log(f"  OCR pagina {page_num}/{total} ({'MLX' if USE_MLX else 'Ollama'})...")
        if USE_MLX:
            # MLX is synchronous — run in thread pool to avoid blocking event loop
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _ocr_page_mlx_sync, b64)
        else:
            resp = await client.generate(
                model=MODEL_ID,
                prompt="Text Recognition:",
                images=[b64],
                keep_alive=KEEP_ALIVE,
                options={"num_ctx": NUM_CTX, "temperature": 0}
            )
            return resp.response

# ─────────────────── PDF PROCESSOR ───────────────────
async def process_pdf(filepath: str):
    global _last_job_time, _model_loaded

    file_obj = Path(filepath)
    stem     = file_obj.stem
    log(f"Elaborazione: {file_obj.name}")

    try:
        info      = pdfinfo_from_path(filepath)
        num_pages = info["Pages"]

        # 1. Pre-classify all pages (fast PyMuPDF scan)
        log(f"Pre-scansione {num_pages} pagine...")
        classifications = classify_pages(filepath)
        native_count = classifications.count("native")
        vision_count = classifications.count("vision")
        log(f"  Testo nativo: {native_count} pag | OCR vision: {vision_count} pag")

        # ETA estimate (native ~instant, vision ~20s/pag)
        if vision_count > 0:
            est_sec = max(1, vision_count * 20 // OCR_CONCURRENCY)
            est_str = f"~{est_sec//60}m {est_sec%60}s (parallel x{OCR_CONCURRENCY})" if est_sec>=60 else f"~{est_sec}s"
        else:
            est_str = "< 1s (fast path)"
        send_notification(f"OCR: {file_obj.name}", f"{num_pages} pag | {est_str}")

        # 2. Build Markdown via chunked lazy processing
        client         = ollama.AsyncClient(host=OLLAMA_HOST)
        full_markdown  = f"# {stem}\n\n"
        t0             = time.time()
        pages_done     = 0

        for chunk_start in range(0, num_pages, CHUNK_SIZE):
            chunk_end    = min(chunk_start + CHUNK_SIZE, num_pages)
            chunk_slice  = list(range(chunk_start, chunk_end))
            chunk_class  = classifications[chunk_start:chunk_end]

            vision_in_chunk = [i for i, c in zip(chunk_slice, chunk_class) if c == "vision"]

            # Lazy load images only for pages that need vision OCR
            page_images = {}
            if vision_in_chunk:
                first_v = vision_in_chunk[0] + 1
                last_v  = vision_in_chunk[-1] + 1
                imgs = convert_from_path(filepath, dpi=DPI,
                                         first_page=first_v, last_page=last_v)
                for rel_i, img in enumerate(imgs):
                    abs_i = vision_in_chunk[rel_i]
                    page_images[abs_i] = img

            # Build async tasks for vision pages in this chunk
            vision_tasks = {
                idx: ocr_page_async(client, page_to_b64(page_images[idx]),
                                     idx + 1, num_pages)
                for idx in vision_in_chunk
            }

            # Await all vision tasks concurrently (semaphore-bounded)
            vision_results = {}
            if vision_tasks:
                results = await asyncio.gather(*vision_tasks.values())
                vision_results = dict(zip(vision_tasks.keys(), results))

            # Assemble chunk output in page order
            for idx in chunk_slice:
                if classifications[idx] == "native":
                    text = extract_native_text_page(filepath, idx)
                else:
                    text = vision_results.get(idx, "")
                if text.strip():
                    full_markdown += text.strip() + "\n\n---\n\n"

            pages_done += len(chunk_slice)
            elapsed     = time.time() - t0
            speed       = elapsed / pages_done if pages_done else 0
            log(f"  Chunk {chunk_start+1}–{chunk_end}/{num_pages} | {speed:.2f}s/pag medio | RAM liberata")

            # Release chunk images from memory
            del page_images, vision_results
            gc.collect()

        # 3. Save Markdown
        elapsed = time.time() - t0
        speed   = elapsed / num_pages
        output_path = OUTPUT_DIR / f"{stem}.md"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_markdown)
        log(f"✓ Completato in {elapsed:.1f}s | Velocità media: {speed:.2f}s/pag | → {output_path}")

        # 4. Archive original
        shutil.move(filepath, ARCHIVE_DIR / file_obj.name)

        _last_job_time = time.time()
        _model_loaded  = True

        # Libera immediatamente la memoria (MLX unified memory o Ollama VRAM)
        await _unload_vram_async()

        send_notification("OCR Completato ✓",
                          f"{stem}.md | {speed:.1f}s/pag | {elapsed:.0f}s totali")

    except asyncio.CancelledError:
        log(f"Elaborazione interrotta (shutdown): {file_obj.name}")
        await _unload_vram_async()
        raise
    except Exception as e:
        log(f"ERRORE {file_obj.name}: {e}")
        await _unload_vram_async()
        send_notification("Errore OCR ✗", f"{file_obj.name}: {str(e)[:80]}")

# ─────────────────── QUEUE WORKER ────────────────────
async def queue_worker():
    log("Queue worker avviato.")
    while not _shutdown_event.is_set():
        try:
            filepath = await asyncio.wait_for(_pdf_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        try:
            await process_pdf(filepath)
        finally:
            _pdf_queue.task_done()
    log("Queue worker terminato (graceful shutdown completato).")

# ─────────────────── IDLE VRAM MONITOR ───────────────
async def idle_vram_monitor():
    """Unload model from VRAM after IDLE_VRAM_TIMEOUT seconds of inactivity."""
    global _model_loaded
    log(f"Idle VRAM monitor avviato (timeout: {IDLE_VRAM_TIMEOUT}s).")
    while not _shutdown_event.is_set():
        await asyncio.sleep(60)
        if _model_loaded and _last_job_time:
            idle = time.time() - _last_job_time
            if idle >= IDLE_VRAM_TIMEOUT and _pdf_queue.empty():
                log(f"Idle da {idle:.0f}s → VRAM unload...")
                await _unload_vram_async()

# ─────────────────── WATCHDOG ─────────────────────────
class PDFHandler(FileSystemEventHandler):
    def __init__(self, loop):
        self._loop = loop

    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith(".pdf"):
            return
        time.sleep(1)
        path = event.src_path
        if not _wait_for_file(path):
            log(f"Timeout attesa file: {path}")
            return
        q_size = _pdf_queue.qsize()
        name   = Path(path).name
        log(f"PDF rilevato: {name} | Coda: {q_size + 1} doc")
        if q_size > 0:
            send_notification(f"PDF in coda: {name}", f"{q_size + 1} in attesa")
        # Bridge thread → asyncio event loop
        self._loop.call_soon_threadsafe(_pdf_queue.put_nowait, path)

def _wait_for_file(filepath: str, timeout: int = 30) -> bool:
    start = time.time()
    last  = -1
    while time.time() - start < timeout:
        if not os.path.exists(filepath):
            return False
        sz = os.path.getsize(filepath)
        if sz == last and sz > 0:
            return True
        last = sz
        time.sleep(1)
    return False

# ─────────────────── MAIN ─────────────────────────────
async def amain():
    global _pdf_queue, _shutdown_event, _ocr_semaphore

    _pdf_queue      = asyncio.Queue()
    _shutdown_event = asyncio.Event()
    _ocr_semaphore  = asyncio.Semaphore(OCR_CONCURRENCY)

    for d in [INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    hw = f"Apple Silicon (MLX Native — {_MLX_HF_MODEL})" if USE_MLX else ("NVIDIA GPU (Ollama)" if IS_NVIDIA else "CPU (Ollama)")
    log(f"Anton OCR Daemon v3 — Online")
    log(f"Hardware    : {hw} | keep_alive={KEEP_ALIVE}s")
    log(f"Modello OCR : {MODEL_ID} | concurrency={OCR_CONCURRENCY} | chunk={CHUNK_SIZE} pag | DPI={DPI}")
    log(f"Tiering     : testo nativo (≥{TEXT_THRESHOLD} chars/pag) → fast | sparse → glm-ocr")
    log(f"VRAM idle   : unload dopo {IDLE_VRAM_TIMEOUT}s di inattività")
    log(f"Input       : {INPUT_DIR}")
    log(f"Output      : {OUTPUT_DIR}")

    loop = asyncio.get_running_loop()
    handler  = PDFHandler(loop)
    observer = Observer()
    observer.schedule(handler, str(INPUT_DIR), recursive=False)
    observer.start()

    tasks = [
        asyncio.create_task(queue_worker(), name="queue_worker"),
        asyncio.create_task(idle_vram_monitor(), name="idle_vram"),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        log("Shutdown: attendo completamento job corrente...")
        _shutdown_event.set()
        # Graceful: wait for queue to drain (current page finishes)
        await asyncio.wait_for(_pdf_queue.join(), timeout=300)
        observer.stop()
        observer.join()
        # Unload VRAM on stop
        if _model_loaded:
            await _unload_vram_async()
        log("Daemon terminato correttamente.")

def start_daemon():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    start_daemon()
