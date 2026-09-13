"""Strato 2 — riconoscimento di entità nominate con modello locale.

Backend supportati, tutti opzionali e tutti locali:

* ``gliner``   — GLiNER multilingue (~209 M parametri, Apache-2.0, gira in CPU)
* ``presidio`` — Microsoft Presidio, se già presente nell'ambiente
* ``none``     — nessun NER: restano attivi solo i riconoscitori a pattern

Il modulo non installa né scarica nulla: se il backend non è disponibile lo
dichiara e la pipeline prosegue senza. In modalità ``sealed`` un modello non
presente in cache è un errore, non un motivo per andare in rete.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anton_ocr.privacy.entities import DetectionLayer, EntityType, Span

log = logging.getLogger(__name__)

# Etichette in linguaggio naturale → tipo di entità.
# GLiNER è zero-shot: le etichette si dichiarano, non si addestrano.
GLINER_LABELS: dict[str, EntityType] = {
    "person": EntityType.PERSON,
    "organization": EntityType.ORGANIZATION,
    "address": EntityType.ADDRESS,
    "location": EntityType.LOCATION,
    "date of birth": EntityType.DATE_OF_BIRTH,
    "medical condition": EntityType.GDPR_ART9_HEALTH,
    "medication": EntityType.GDPR_ART9_HEALTH,
    "religious belief": EntityType.GDPR_ART9_RELIGION,
    "political affiliation": EntityType.GDPR_ART9_POLITICAL,
    "trade union": EntityType.GDPR_ART9_UNION,
    "sexual orientation": EntityType.GDPR_ART9_SEXUAL,
    "biometric data": EntityType.GDPR_ART9_BIOMETRIC,
    "genetic data": EntityType.GDPR_ART9_GENETIC,
}

PRESIDIO_MAP: dict[str, EntityType] = {
    "PERSON": EntityType.PERSON,
    "LOCATION": EntityType.LOCATION,
    "ORGANIZATION": EntityType.ORGANIZATION,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "PHONE_NUMBER": EntityType.PHONE,
    "IBAN_CODE": EntityType.IBAN,
    "CREDIT_CARD": EntityType.CREDIT_CARD,
    "IP_ADDRESS": EntityType.IP_ADDRESS,
    "IT_FISCAL_CODE": EntityType.IT_CODICE_FISCALE,
    "IT_VAT_CODE": EntityType.IT_PARTITA_IVA,
    "DATE_TIME": EntityType.DATE,
}


class NERUnavailable(RuntimeError):
    """Il backend NER richiesto non è utilizzabile."""


@dataclass
class NERConfig:
    backend: str = "none"  # none | gliner | presidio
    model: str = "urchade/gliner_multi-v2.1"
    language: str = "it"
    threshold: float = 0.5
    sealed: bool = True


class NERecognizer:
    """Wrapper con caricamento pigro attorno al backend NER scelto."""

    def __init__(self, config: NERConfig | None = None) -> None:
        self.config = config or NERConfig()
        self._model = None
        self._loaded = False
        self._unavailable_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.config.backend != "none"

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            self._ensure_loaded()
        except NERUnavailable:
            return False
        return self._model is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def _ensure_loaded(self) -> None:
        if self._loaded:
            if self._unavailable_reason:
                raise NERUnavailable(self._unavailable_reason)
            return
        self._loaded = True
        try:
            if self.config.backend == "gliner":
                self._model = self._load_gliner()
            elif self.config.backend == "presidio":
                self._model = self._load_presidio()
            else:
                self._unavailable_reason = f"backend NER sconosciuto: {self.config.backend}"
        except NERUnavailable as exc:
            self._unavailable_reason = str(exc)
            raise
        except Exception as exc:  # pragma: no cover - dipende dall'ambiente
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            raise NERUnavailable(self._unavailable_reason) from exc

    def _load_gliner(self):
        try:
            from gliner import GLiNER
        except ImportError as exc:
            raise NERUnavailable(
                "GLiNER non installato. Installa con: pip install 'anton-ocr[ner]'"
            ) from exc

        if self.config.sealed:
            import os

            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        log.info("Caricamento GLiNER: %s", self.config.model)
        return GLiNER.from_pretrained(self.config.model)

    def _load_presidio(self):
        try:
            from presidio_analyzer import AnalyzerEngine
        except ImportError as exc:
            raise NERUnavailable(
                "Presidio non installato. Installa con: pip install 'anton-ocr[presidio]'"
            ) from exc
        return AnalyzerEngine()

    def scan(self, text: str) -> list[Span]:
        """Rileva le entità. Restituisce lista vuota se il backend non è disponibile."""
        if not self.enabled or not text.strip():
            return []
        try:
            self._ensure_loaded()
        except NERUnavailable as exc:
            log.warning("NER non disponibile, si prosegue senza: %s", exc)
            return []

        if self.config.backend == "gliner":
            return self._scan_gliner(text)
        return self._scan_presidio(text)

    def _scan_gliner(self, text: str) -> list[Span]:
        labels = list(GLINER_LABELS)
        results = self._model.predict_entities(text, labels, threshold=self.config.threshold)
        spans: list[Span] = []
        for item in results:
            entity_type = GLINER_LABELS.get(item["label"])
            if entity_type is None:
                continue
            spans.append(
                Span(
                    start=item["start"],
                    end=item["end"],
                    text=item["text"],
                    entity_type=entity_type,
                    layer=DetectionLayer.NER,
                    confidence=float(item.get("score", self.config.threshold)),
                )
            )
        return spans

    def _scan_presidio(self, text: str) -> list[Span]:
        results = self._model.analyze(text=text, language=self.config.language)
        spans: list[Span] = []
        for item in results:
            entity_type = PRESIDIO_MAP.get(item.entity_type)
            if entity_type is None:
                continue
            spans.append(
                Span(
                    start=item.start,
                    end=item.end,
                    text=text[item.start : item.end],
                    entity_type=entity_type,
                    layer=DetectionLayer.NER,
                    confidence=float(item.score),
                )
            )
        return spans
