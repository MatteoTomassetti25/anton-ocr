"""Tipi di entità e rappresentazione degli span rilevati."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EntityType(str, Enum):
    """Entità riconosciute.

    I membri con prefisso ``GDPR_ART9_`` sono categorie particolari ai sensi
    dell'art. 9 GDPR: il default della policy per queste è ``block``, non ``redact``.
    """

    # Identificatori strutturati (validabili con checksum)
    IT_CODICE_FISCALE = "IT_CODICE_FISCALE"
    IT_PARTITA_IVA = "IT_PARTITA_IVA"
    IBAN = "IBAN"
    CREDIT_CARD = "CREDIT_CARD"
    IT_TARGA = "IT_TARGA"
    IT_TESSERA_SANITARIA = "IT_TESSERA_SANITARIA"

    # Identificatori di contatto
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IP_ADDRESS = "IP_ADDRESS"
    MAC_ADDRESS = "MAC_ADDRESS"

    # Quasi-identificatori
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    ADDRESS = "ADDRESS"
    LOCATION = "LOCATION"
    DATE = "DATE"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"

    # Art. 9 GDPR — categorie particolari
    GDPR_ART9_HEALTH = "GDPR_ART9_HEALTH"
    GDPR_ART9_BIOMETRIC = "GDPR_ART9_BIOMETRIC"
    GDPR_ART9_GENETIC = "GDPR_ART9_GENETIC"
    GDPR_ART9_RELIGION = "GDPR_ART9_RELIGION"
    GDPR_ART9_POLITICAL = "GDPR_ART9_POLITICAL"
    GDPR_ART9_SEXUAL = "GDPR_ART9_SEXUAL"
    GDPR_ART9_UNION = "GDPR_ART9_UNION"

    @property
    def is_special_category(self) -> bool:
        return self.value.startswith("GDPR_ART9_")

    @property
    def is_checksum_validated(self) -> bool:
        return self in _CHECKSUM_VALIDATED


_CHECKSUM_VALIDATED = frozenset(
    {
        EntityType.IT_CODICE_FISCALE,
        EntityType.IT_PARTITA_IVA,
        EntityType.IBAN,
        EntityType.CREDIT_CARD,
    }
)


class DetectionLayer(str, Enum):
    """Strato che ha prodotto il rilevamento. Serve all'audit trail."""

    REGEX = "regex"
    CHECKSUM = "checksum"
    OCR_RECOVERY = "ocr_recovery"
    NER = "ner"
    LLM_ADJUDICATION = "llm_adjudication"
    IMAGE = "image"


@dataclass(frozen=True)
class Span:
    """Un'occorrenza rilevata nel testo.

    ``start``/``end`` sono offset di carattere nel testo analizzato: preservarli
    è ciò che permette di ricondurre la redazione alle coordinate del documento.
    """

    start: int
    end: int
    text: str
    entity_type: EntityType
    layer: DetectionLayer
    confidence: float = 1.0
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None

    # Valorizzati quando lo strato di recupero OCR ha corretto la sequenza.
    ocr_corrected: bool = False
    corrected_text: str | None = None
    correction_note: str | None = None

    metadata: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"Span con offset non validi: {self.start}..{self.end}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence fuori range: {self.confidence}")

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def canonical_value(self) -> str:
        """Valore da usare per la generazione del token.

        Se lo strato di recupero OCR ha corretto la sequenza, il token si basa
        sul valore *corretto*: due occorrenze dello stesso codice fiscale, una
        letta bene e una letta male, ricevono così lo stesso token.
        """
        return self.corrected_text or self.text

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end


def resolve_overlaps(spans: list[Span]) -> list[Span]:
    """Risolve le sovrapposizioni fra span.

    Precedenza: (1) validato da checksum, (2) span più lungo, (3) confidence più alta.

    La regola "checksum vince" è deliberata: un rilevamento con checksum valido è
    quasi certo, e non deve mai essere scavalcato da un rilevamento probabilistico.
    """

    def priority(s: Span) -> tuple[int, int, float]:
        checksum_ok = 1 if s.entity_type.is_checksum_validated else 0
        return (checksum_ok, s.length, s.confidence)

    ordered = sorted(spans, key=priority, reverse=True)
    kept: list[Span] = []
    for span in ordered:
        if not any(span.overlaps(k) for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)
