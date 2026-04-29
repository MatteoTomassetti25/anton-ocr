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
import pymupdf4llm
import fitz  # PyMuPDF

# ---------------- CONFIGURATION ----------------
def load_config():
    config = {
        "INPUT_DIR":     str(Path(__file__).parent / "input"),
        "OUTPUT_DIR":    "/Users/cmdhro/Matteo/wikiAnton/Università",
        "ARCHIVE_DIR":   str(Path(__file__).parent / "elaborati"),
        "MODEL":         "glm-ocr:latest",
        "DPI":           "200",
        "NUM_CTX":       "16384",
        "OLLAMA_HOST":   "http://localhost:11434",
        "SECS_PER_PAGE": "22",
        # Minimum text chars per page to consider it "native text"
        "TEXT_THRESHOLD": "50",
    }
    config_path = Path(__file__).parent / "config.env"
    if config_path.exists():
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip().replace("~", str(Path.home()))
    return config

CFG = load_config()
INPUT_DIR      = Path(CFG["INPUT_DIR"])
OUTPUT_DIR     = Path(CFG["OUTPUT_DIR"])
ARCHIVE_DIR    = Path(CFG["ARCHIVE_DIR"])
MODEL_ID       = CFG["MODEL"]
DPI            = int(CFG["DPI"])
NUM_CTX        = int(CFG["NUM_CTX"])
OLLAMA_HOST    = CFG["OLLAMA_HOST"]
SECS_PER_PAGE  = int(CFG["SECS_PER_PAGE"])
TEXT_THRESHOLD = int(CFG.get("TEXT_THRESHOLD", "50"))

# Thread-safe queue + stop event
pdf_queue: queue.Queue = queue.Queue()
stop_event = threading.Event()
# -----------------------------------------------

def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str):
    print(f"[{ts()}] {msg}", flush=True)

def send_notification(title: str, message: str):
    try:
        if sys.platform == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass

def unload_ollama_model():
    """Free VRAM immediately via keep_alive=0."""
    try:
        req.post(f"{OLLAMA_HOST}/api/generate",
                 json={"model": MODEL_ID, "keep_alive": 0}, timeout=10)
        log(f"VRAM deallocata: {MODEL_ID} scaricato.")
    except Exception as e:
        log(f"WARN: impossibile deallocare VRAM: {e}")

def init_directories():
    for d in [INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def wait_for_file_ready(filepath: str, timeout: int = 30) -> bool:
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

# ---- OCR Strategy Detection ----
def is_text_based_pdf(filepath: str) -> bool:
    """
    Returns True if the PDF has enough native text to use pymupdf4llm (fast path).
    Falls back to glm-ocr (slow path) for scanned/image-only PDFs.
    """
    try:
        doc = fitz.open(filepath)
        total_chars = sum(len(page.get_text()) for page in doc)
        doc.close()
        avg_chars_per_page = total_chars / max(doc.page_count, 1)
        return avg_chars_per_page >= TEXT_THRESHOLD
    except Exception:
        return False

# ---- Fast Path: pymupdf4llm ----
def process_pdf_fast(filepath: str, filename_stem: str) -> str:
    """Extract text from native PDF using pymupdf4llm. ~0.05s/page."""
    log("Rilevato PDF testuale → modalità FAST (pymupdf4llm)")
    markdown = pymupdf4llm.to_markdown(filepath)
    return markdown

# ---- Slow Path: glm-ocr via Ollama ----
def page_to_base64(page) -> str:
    buf = io.BytesIO()
    page.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def call_glm_ocr(b64_image: str) -> str:
    response = ollama.generate(
        model=MODEL_ID,
        prompt="Text Recognition:",
        images=[b64_image],
        options={"num_ctx": NUM_CTX, "temperature": 0}
    )
    return response["response"]

def process_pdf_ocr(filepath: str, num_pages: int) -> str:
    """Process image-based PDF with glm-ocr. ~20s/page."""
    log("Rilevato PDF con immagini → modalità OCR (glm-ocr)")
    pages = convert_from_path(filepath, dpi=DPI)

    # Pre-encode pages to base64 concurrently
    with ThreadPoolExecutor(max_workers=4) as ex:
        b64_list = list(ex.map(page_to_base64, pages))

    full_markdown = ""
    for i, b64 in enumerate(b64_list):
        log(f"OCR pagina {i+1}/{num_pages}...")
        text = call_glm_ocr(b64)
        if text.strip():
            full_markdown += text.strip() + "\n\n---\n\n"

    unload_ollama_model()
    return full_markdown

# ---- Main Processing ----
def process_pdf(filepath: str):
    file_path_obj = Path(filepath)
    filename_stem = file_path_obj.stem
    log(f"Inizio elaborazione: {file_path_obj.name}")

    try:
        info = pdfinfo_from_path(filepath)
        num_pages = info["Pages"]

        # Choose strategy
        if is_text_based_pdf(filepath):
            # FAST PATH — native text
            est_str = f"< 1s (modalità fast)"
            send_notification(
                f"OCR Avviato: {file_path_obj.name}",
                f"{num_pages} pagine · {est_str}"
            )
            t0 = time.time()
            markdown = process_pdf_fast(filepath, filename_stem)
            elapsed = time.time() - t0
            log(f"Conversione completata in {elapsed:.2f}s ({elapsed/num_pages:.3f}s/pagina)")
        else:
            # SLOW PATH — glm-ocr vision model
            est_sec = num_pages * SECS_PER_PAGE
            est_str = f"{est_sec // 60}m {est_sec % 60}s (modalità OCR)"
            send_notification(
                f"OCR Avviato: {file_path_obj.name}",
                f"{num_pages} pagine · Stima: {est_str}"
            )
            t0 = time.time()
            markdown = process_pdf_ocr(filepath, num_pages)
            elapsed = time.time() - t0
            log(f"OCR completato in {elapsed:.2f}s ({elapsed/num_pages:.1f}s/pagina)")

        # Save Markdown
        output_path = OUTPUT_DIR / f"{filename_stem}.md"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# {filename_stem}\n\n")
            f.write(markdown)
        log(f"Markdown salvato: {output_path}")

        # Archive original
        shutil.move(filepath, ARCHIVE_DIR / file_path_obj.name)
        log("File archiviato.")

        send_notification("OCR Completato ✓", f"{filename_stem}.md salvato")

    except Exception as e:
        log(f"ERRORE su {file_path_obj.name}: {e}")
        send_notification("Errore OCR ✗", f"{file_path_obj.name}: {str(e)[:80]}")


# ---- Queue Worker ----
def queue_worker():
    log("Queue worker avviato.")
    while not stop_event.is_set():
        try:
            filepath = pdf_queue.get(timeout=1)
            if filepath is None:
                break
            process_pdf(filepath)
            pdf_queue.task_done()
        except queue.Empty:
            continue
    log("Queue worker terminato.")


# ---- Watchdog Handler ----
class PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith(".pdf"):
            return
        time.sleep(1)
        if wait_for_file_ready(event.src_path):
            q_size = pdf_queue.qsize()
            name = Path(event.src_path).name
            log(f"PDF rilevato: {name} · Coda: {q_size + 1} doc")
            if q_size > 0:
                send_notification(f"PDF in coda: {name}", f"{q_size + 1} documenti in attesa")
            pdf_queue.put(event.src_path)
        else:
            log(f"Timeout attesa file: {event.src_path}")


# ---- Main ----
def start_daemon():
    init_directories()
    log("Anton OCR Daemon v2 - Online")
    log(f"Modello OCR : {MODEL_ID} (num_ctx={NUM_CTX})")
    log(f"Fast path   : pymupdf4llm (PDF testuali, soglia {TEXT_THRESHOLD} chars/pagina)")
    log(f"Input       : {INPUT_DIR}")
    log(f"Output      : {OUTPUT_DIR}")
    log(f"Archivio    : {ARCHIVE_DIR}")

    worker = threading.Thread(target=queue_worker, daemon=True)
    worker.start()

    event_handler = PDFHandler()
    observer = Observer()
    observer.schedule(event_handler, str(INPUT_DIR), recursive=False)
    observer.start()

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    log("Shutdown ricevuto. Arresto in corso...")
    stop_event.set()
    observer.stop()
    pdf_queue.put(None)
    worker.join(timeout=5)
    observer.join()
    log("Daemon arrestato.")

if __name__ == "__main__":
    start_daemon()
