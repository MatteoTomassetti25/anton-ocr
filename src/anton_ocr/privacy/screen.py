"""Orchestratore del rilevamento: unisce gli strati e risolve i conflitti."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from anton_ocr.privacy import ocr_recovery, recognizers
from anton_ocr.privacy.entities import DetectionLayer, EntityType, Span, resolve_overlaps
from anton_ocr.privacy.ner import NERConfig, NERecognizer

log = logging.getLogger(__name__)


@dataclass
class ScreenResult:
    spans: list[Span]
    stats: dict = field(default_factory=dict)

    @property
    def has_special_category(self) -> bool:
        return any(s.entity_type.is_special_category for s in self.spans)

    @property
    def min_confidence(self) -> float:
        return min((s.confidence for s in self.spans), default=1.0)

    def by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.spans:
            counts[s.entity_type.value] = counts.get(s.entity_type.value, 0) + 1
        return counts

    def by_layer(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.spans:
            counts[s.layer.value] = counts.get(s.layer.value, 0) + 1
        return counts


@dataclass
class ScreenConfig:
    layers: tuple[str, ...] = ("regex", "ocr_recovery", "ner")
    max_ambiguous_positions: int = 6
    max_edits: int = 2
    ner: NERConfig = field(default_factory=NERConfig)


class Screener:
    """Esegue il rilevamento PII su testo estratto da OCR.

    Nessuno stadio contatta la rete e nessuno stadio invia dati a un modello
    esterno: l'intero rilevamento avviene sulla macchina locale.
    """

    def __init__(self, config: ScreenConfig | None = None) -> None:
        self.config = config or ScreenConfig()
        self._ner = NERecognizer(self.config.ner)

    def scan(self, text: str) -> ScreenResult:
        if not text:
            return ScreenResult(spans=[], stats={"chars": 0})

        collected: list[Span] = []
        stats: dict = {"chars": len(text)}

        # ── Strato 1: pattern + checksum ──
        unvalidated: list[tuple[EntityType, int, int, str]] = []
        if "regex" in self.config.layers:
            regex_spans, unvalidated = recognizers.scan(text)
            collected.extend(regex_spans)
            stats["regex_hits"] = len(regex_spans)
            stats["unvalidated_candidates"] = len(unvalidated)

        # ── Strato 1.5: recupero errori OCR ──
        if "ocr_recovery" in self.config.layers and unvalidated:
            recovered = ocr_recovery.recover_spans(
                text,
                unvalidated,
                max_ambiguous_positions=self.config.max_ambiguous_positions,
                max_edits=self.config.max_edits,
            )
            collected.extend(recovered)
            stats["ocr_recovered"] = len(recovered)
            stats["ocr_recovered_ambiguous"] = sum(
                1 for s in recovered if s.metadata.get("ambiguous")
            )

        # ── Strato 2: NER locale ──
        # Distinguere "spento" da "guasto" non è pedanteria: in entrambi i casi
        # i nomi di persona restano in chiaro, e chi riceve il documento deve
        # saperlo. È l'unica differenza che conta fra un file sicuro e uno che
        # sembra sicuro.
        stats["ner_enabled"] = self._ner.enabled
        if "ner" in self.config.layers and self._ner.enabled:
            ner_spans = self._ner.scan(text)
            collected.extend(ner_spans)
            stats["ner_hits"] = len(ner_spans)
            if not self._ner.available:
                stats["ner_unavailable"] = self._ner.unavailable_reason

        spans = resolve_overlaps(collected)
        stats["total_spans"] = len(spans)
        stats["dropped_overlapping"] = len(collected) - len(spans)

        return ScreenResult(spans=spans, stats=stats)


def summarize_layers(result: ScreenResult) -> str:
    """Riga di riepilogo leggibile, usata nei log e nella CLI."""
    layers = result.by_layer()
    parts = [f"{k}={v}" for k, v in sorted(layers.items())]
    recovered = result.stats.get("ocr_recovered", 0)
    if recovered:
        parts.append(f"recuperati_da_ocr={recovered}")
    return " | ".join(parts) if parts else "nessuna entità"


__all__ = [
    "Screener",
    "ScreenConfig",
    "ScreenResult",
    "DetectionLayer",
    "summarize_layers",
]
