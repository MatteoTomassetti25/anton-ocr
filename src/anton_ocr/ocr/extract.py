"""Estrazione del testo nativo dal PDF.

Astrazione sul motore di estrazione per una ragione di licenza, non di stile:

* **pypdfium2** (Apache-2.0 / BSD-3) è il motore predefinito;
* **PyMuPDF** è **AGPL-3.0** ed è quindi opzionale. Renderlo obbligatorio in un
  progetto MIT costringerebbe di fatto ogni utilizzatore a fare i conti con
  l'AGPL — un problema reale, non formale.

PyMuPDF resta disponibile come extra perché il rilevamento delle tabelle è
migliore; chi lo installa sceglie consapevolmente la sua licenza.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from anton_ocr.ocr.confidence import PageQuality, assess_text_quality

log = logging.getLogger(__name__)


class ExtractionError(RuntimeError):
    pass


@dataclass
class ExtractedPage:
    index: int  # 0-based
    text: str
    quality: PageQuality
    source: str  # native | vlm
    task: str = "text"
    char_count: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def page_number(self) -> int:
        return self.index + 1


def available_backends() -> dict[str, bool]:
    backends = {}
    try:
        import pypdfium2  # noqa: F401

        backends["pypdfium2"] = True
    except ImportError:
        backends["pypdfium2"] = False
    try:
        import fitz  # noqa: F401

        backends["pymupdf"] = True
    except ImportError:
        backends["pymupdf"] = False
    return backends


class TextExtractor:
    """Estrae il testo nativo pagina per pagina."""

    def __init__(self, backend: str = "auto") -> None:
        self.backend = self._resolve(backend)

    @staticmethod
    def _resolve(backend: str) -> str:
        available = available_backends()
        if backend == "auto":
            if available.get("pypdfium2"):
                return "pypdfium2"
            if available.get("pymupdf"):
                log.warning(
                    "pypdfium2 non disponibile: uso PyMuPDF (AGPL-3.0). "
                    "Verifica che la licenza sia compatibile con il tuo uso."
                )
                return "pymupdf"
            raise ExtractionError(
                "Nessun motore di estrazione PDF disponibile. "
                "Installa con: pip install 'anton-ocr[pdf]'"
            )
        if not available.get(backend):
            raise ExtractionError(f"Backend '{backend}' non installato")
        return backend

    def page_count(self, path: Path | str) -> int:
        path = Path(path)
        if self.backend == "pypdfium2":
            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(path))
            try:
                return len(doc)
            finally:
                doc.close()
        import fitz

        with fitz.open(str(path)) as doc:
            return doc.page_count

    def extract_page(self, path: Path | str, index: int) -> str:
        path = Path(path)
        if self.backend == "pypdfium2":
            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(path))
            try:
                page = doc[index]
                textpage = page.get_textpage()
                try:
                    return textpage.get_text_range()
                finally:
                    textpage.close()
                    page.close()
            finally:
                doc.close()

        import fitz

        with fitz.open(str(path)) as doc:
            return doc[index].get_text()

    def extract(
        self,
        path: Path | str,
        *,
        text_threshold: int = 50,
        cleaner=None,
    ) -> list[ExtractedPage]:
        """Estrae tutte le pagine, valutando la qualità di ciascuna.

        Le pagine con meno di ``text_threshold`` caratteri nativi sono marcate
        come candidate all'OCR: è il tiering che evita di far girare il modello
        vision sulle pagine che non ne hanno bisogno.
        """
        path = Path(path)
        if not path.exists():
            raise ExtractionError(f"File non trovato: {path}")

        pages: list[ExtractedPage] = []
        for index in range(self.page_count(path)):
            raw = self.extract_page(path, index)
            text = cleaner(raw) if cleaner else raw
            stripped = text.strip()

            if len(stripped) >= text_threshold:
                pages.append(
                    ExtractedPage(
                        index=index,
                        text=text,
                        quality=assess_text_quality(text),
                        source="native",
                        task="text",
                        char_count=len(stripped),
                    )
                )
            else:
                # Pagina sparsa: serve il modello vision. Qui non lo invochiamo,
                # ci limitiamo a segnalarlo, così l'estrazione resta utilizzabile
                # anche su macchine senza backend OCR installato.
                pages.append(
                    ExtractedPage(
                        index=index,
                        text=text,
                        quality=assess_text_quality(text),
                        source="native",
                        task="needs_ocr",
                        char_count=len(stripped),
                        metadata={"reason": f"solo {len(stripped)} caratteri nativi"},
                    )
                )
        return pages
