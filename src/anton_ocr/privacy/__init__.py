"""Motore di privacy: rilevamento, recupero errori OCR, pseudonimizzazione, vault."""

from anton_ocr.privacy.entities import EntityType, Span
from anton_ocr.privacy.screen import Screener

__all__ = ["EntityType", "Span", "Screener"]
