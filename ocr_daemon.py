import os
import sys
import time
import queue
import base64
import shutil
import subprocess
import threading
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pdf2image import convert_from_path, pdfinfo_from_path
import ollama
import requests as req

# ---------------- CONFIGURATION ----------------
def load_config():
    config = {
        "INPUT_DIR": str(Path.home() / "anton-ocr" / "input"),
        "OUTPUT_DIR": str(Path.home() / "anton-ocr" / "output"),
        "ARCHIVE_DIR": str(Path.home() / "anton-ocr" / "elaborati"),
        "MODEL": "glm-ocr:latest",
        "DPI": "200",
        "NUM_CTX": "16384",
        "OLLAMA_HOST": "http://localhost:11434",
        "SECS_PER_PAGE": "22",
    }
    config_path = Path(__file__).parent / "config.env"
    if config_path.exists():
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip()
    return config

CFG = load_config()

INPUT_DIR     = Path(CFG["INPUT_DIR"])
OUTPUT_DIR    = Path(CFG["OUTPUT_DIR"])
ARCHIVE_DIR   = Path(CFG["ARCHIVE_DIR"])
MODEL_ID      = CFG["MODEL"]
DPI           = int(CFG["DPI"])
NUM_CTX       = int(CFG["NUM_CTX"])
OLLAMA_HOST   = CFG["OLLAMA_HOST"]
SECS_PER_PAGE = int(CFG["SECS_PER_PAGE"])

# Thread-safe queue for PDF processing
pdf_queue: queue.Queue = queue.Queue()
# -----------------------------------------------

def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str):
    print(f"[{ts()}] {msg}", flush=True)

def send_notification(title: str, message: str):
    """macOS native notification. No-op on Linux."""
    try:
        if sys.platform == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass

def unload_ollama_model():
    """Immediately free VRAM by setting keep_alive=0."""
    try:
        req.post(f"{OLLAMA_HOST}/api/generate",
                 json={"model": MODEL_ID, "keep_alive": 0}, timeout=10)
        log(f"VRAM deallocata: {MODEL_ID} scaricato da Ollama.")
    except Exception as e:
        log(f"WARN: impossibile deallocare VRAM: {e}")

def init_directories():
    for d in [INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def wait_for_file_ready(filepath: str, timeout: int = 30) -> bool:
    """Wait until file size is stable (fully written to disk)."""
    start = time.time()
    last_size = -1
    while time.time() - start < timeout:
        if not os.path.exists(filepath):
            return False
        size = os.path.getsize(filepath)
        if size == last_size and size > 0:
            return True
        last_size = size
        time.sleep(1)
    return False

def page_to_base64(page) -> str:
    """Convert a PIL image page to JPEG base64 string."""
    buf = io.BytesIO()
    page.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def call_glm_ocr(b64_image: str) -> str:
    """Call glm-ocr via Ollama with correct parameters."""
    response = ollama.generate(
        model=MODEL_ID,
        prompt="Text Recognition:",
        images=[b64_image],
        options={"num_ctx": NUM_CTX, "temperature": 0}
    )
    return response["response"]

def process_pdf(filepath: str):
    file_path_obj = Path(filepath)
    filename_stem = file_path_obj.stem
    log(f"Inizio elaborazione: {file_path_obj.name}")

    try:
        # --- Page count & ETA notification ---
        info = pdfinfo_from_path(filepath)
        num_pages = info["Pages"]
        est_sec = num_pages * SECS_PER_PAGE
        est_str = f"{est_sec // 60}m {est_sec % 60}s" if est_sec >= 60 else f"{est_sec}s"
        log(f"Pagine rilevate: {num_pages} · Stima: {est_str}")
        send_notification(
            f"OCR Avviato: {file_path_obj.name}",
            f"{num_pages} pagine · Stima: {est_str}"
        )

        # --- Convert PDF pages to images (parallel with ThreadPoolExecutor) ---
        log("Conversione PDF → immagini...")
        pages = convert_from_path(filepath, dpi=DPI)
        log(f"Pagine estratte: {len(pages)}")

        # Pre-encode all pages to base64 concurrently to overlap with OCR calls
        with ThreadPoolExecutor(max_workers=4) as executor:
            b64_futures = list(executor.map(page_to_base64, pages))

        # --- OCR: sequential calls to Ollama (single model instance) ---
        full_markdown = f"# {filename_stem}\n\n"
        for i, b64_img in enumerate(b64_futures):
            log(f"OCR pagina {i+1}/{len(pages)}...")
            text = call_glm_ocr(b64_img)
            if text.strip():
                full_markdown += text.strip() + "\n\n---\n\n"

        # --- Save Markdown ---
        output_path = OUTPUT_DIR / f"{filename_stem}.md"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_markdown)
        log(f"Completato! Markdown salvato in: {output_path}")

        # --- Archive original PDF ---
        shutil.move(filepath, ARCHIVE_DIR / file_path_obj.name)
        log("File archiviato in elaborati.")

        # --- Free VRAM immediately ---
        unload_ollama_model()

        send_notification("OCR Completato ✓", f"{filename_stem}.md salvato")

    except Exception as e:
        log(f"ERRORE su {file_path_obj.name}: {e}")
        unload_ollama_model()
        send_notification("Errore OCR ✗", f"{file_path_obj.name}: {str(e)[:80]}")


# ---- Queue Worker ----
def queue_worker():
    """Dedicated thread: pulls PDFs from queue and processes them one by one."""
    log("Queue worker avviato.")
    while True:
        filepath = pdf_queue.get()
        if filepath is None:
            break
        try:
            process_pdf(filepath)
        finally:
            pdf_queue.task_done()


# ---- Watchdog Handler ----
class PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith(".pdf"):
            return
        time.sleep(1)
        if wait_for_file_ready(event.src_path):
            q_size = pdf_queue.qsize()
            name = Path(event.src_path).name
            log(f"PDF rilevato: {name} · Coda: {q_size + 1} documento/i")
            if q_size > 0:
                send_notification(f"PDF in coda: {name}", f"{q_size + 1} documenti in attesa")
            pdf_queue.put(event.src_path)
        else:
            log(f"Timeout attesa file: {event.src_path}")


# ---- Main ----
def start_daemon():
    init_directories()
    log(f"Anton OCR Daemon v2 - Online")
    log(f"Modello  : {MODEL_ID} (num_ctx={NUM_CTX})")
    log(f"Input    : {INPUT_DIR}")
    log(f"Output   : {OUTPUT_DIR}")
    log(f"Archivio : {ARCHIVE_DIR}")

    # Start the single worker thread for the processing queue
    worker = threading.Thread(target=queue_worker, daemon=True)
    worker.start()

    # Start the filesystem observer
    event_handler = PDFHandler()
    observer = Observer()
    observer.schedule(event_handler, str(INPUT_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Shutdown ricevuto. Arresto in corso...")
        observer.stop()
        pdf_queue.put(None)  # Signal worker to stop
        worker.join(timeout=5)
    observer.join()
    log("Daemon arrestato.")

if __name__ == "__main__":
    start_daemon()
