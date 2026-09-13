"""Daemon watchfolder: i PDF depositati nella cartella di ingresso vengono
elaborati dalla pipeline completa, privacy inclusa.

Differenza rispetto al daemon v3: l'output non è più il testo grezzo, ma il testo
**pseudonimizzato** accompagnato dal manifest firmato. Il documento originale
resta nella cartella di archivio, sulla macchina.
"""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from anton_ocr.config import Config
from anton_ocr.pipeline import Pipeline
from anton_ocr.policy.engine import Decision

log = logging.getLogger(__name__)


def notify(title: str, message: str) -> None:
    """Notifica di sistema. Silenziosa dove non disponibile."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def wait_for_stable_file(path: Path, timeout: int = 30, interval: float = 1.0) -> bool:
    """Attende che il file smetta di crescere.

    Serve su cartelle di rete (SMB/NFS): l'evento di creazione arriva prima che
    la scrittura sia completa, e leggere troppo presto produce un PDF troncato.
    """
    start = time.time()
    last_size = -1
    while time.time() - start < timeout:
        if not path.exists():
            return False
        size = path.stat().st_size
        if size == last_size and size > 0:
            return True
        last_size = size
        time.sleep(interval)
    return False


class Daemon:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.load()
        self.config.ensure_dirs()
        self.pipeline = Pipeline(self.config)
        self._queue: queue.Queue[Path] = queue.Queue()
        self._stop = threading.Event()

    # ── elaborazione ──

    def process(self, path: Path) -> None:
        log.info("Elaborazione: %s", path.name)
        try:
            if path.suffix.lower() == ".pdf":
                result = self.pipeline.process_pdf(path)
            else:
                result = self.pipeline.process_text(
                    path.read_text(encoding="utf-8"), source_name=path.name
                )
        except Exception as exc:
            log.exception("Elaborazione fallita: %s", path.name)
            notify("anton-ocr — errore", f"{path.name}: {str(exc)[:80]}")
            return

        icon = {Decision.ALLOW: "✓", Decision.REVIEW: "⚠", Decision.BLOCK: "✗"}[result.decision]
        log.info(
            "%s %s | %s | %d token | qualità %.2f",
            icon,
            result.decision.value.upper(),
            path.name,
            len(result.mapping),
            result.ocr_confidence,
        )

        notify(
            f"anton-ocr {icon} {result.decision.value}",
            f"{path.name} — {len(result.mapping)} entità pseudonimizzate",
        )

        destination = self.config.archive_dir / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                path = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.process(path)
            finally:
                self._queue.task_done()

    # ── avvio ──

    def run(self) -> int:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.error(
                "watchdog non installato. Installa con: pip install 'anton-ocr[daemon]'"
            )
            return 1

        daemon = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                path = Path(event.src_path)
                if path.suffix.lower() not in (".pdf", ".txt", ".md"):
                    return
                if not wait_for_stable_file(path):
                    log.warning("File non stabilizzato entro il timeout: %s", path.name)
                    return
                log.info("Rilevato: %s (coda: %d)", path.name, daemon._queue.qsize() + 1)
                daemon._queue.put(path)

        observer = Observer()
        observer.schedule(Handler(), str(self.config.input_dir), recursive=False)
        observer.start()

        worker = threading.Thread(target=self._worker, name="pipeline_worker", daemon=True)
        worker.start()

        log.info("anton-ocr daemon avviato")
        log.info("  policy    %s (%s)", self.pipeline.policy.profile, self.pipeline.policy.mode)
        log.info("  ingresso  %s", self.config.input_dir)
        log.info("  uscita    %s", self.config.output_dir)
        log.info("  revisione %s", self.config.review_dir)
        log.info("  quarantena %s", self.config.quarantine_dir)

        # I file già presenti all'avvio non vanno persi.
        for existing in sorted(self.config.input_dir.glob("*")):
            if existing.suffix.lower() in (".pdf", ".txt", ".md"):
                self._queue.put(existing)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Arresto: completo il documento in corso…")
        finally:
            self._stop.set()
            observer.stop()
            observer.join()
            self._queue.join()
            log.info("Daemon terminato")
        return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    return Daemon().run()


if __name__ == "__main__":
    sys.exit(main())
