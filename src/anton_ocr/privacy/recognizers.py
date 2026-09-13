"""Strato 1 — riconoscitori a pattern con validazione tramite checksum.

Ogni riconoscitore dichiara un pattern *candidato* volutamente permissivo e un
validatore. Il pattern permissivo serve a due scopi:

1. catturare le occorrenze scritte in modo irregolare (spazi, trattini, minuscole);
2. produrre candidati che, se falliscono la validazione, vengono passati allo
   strato di recupero errori OCR (``ocr_recovery``) invece di essere scartati.

Scartare subito un candidato non validato è esattamente il comportamento che
genera falsi negativi su testo OCR.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from anton_ocr.privacy import checksums as ck
from anton_ocr.privacy.entities import DetectionLayer, EntityType, Span


@dataclass(frozen=True)
class Recognizer:
    entity_type: EntityType
    pattern: re.Pattern
    validator: Callable[[str], bool] | None = None
    # Parole che, se presenti nelle vicinanze, alzano la confidenza di un
    # rilevamento non validabile con checksum.
    context_words: tuple[str, ...] = ()
    # Confidenza quando non esiste un checksum che confermi il rilevamento.
    base_confidence: float = 0.6
    # Se True, un candidato non validato viene comunque emesso (con confidenza
    # ridotta) invece di essere scartato.
    emit_unvalidated: bool = False


CONTEXT_WINDOW = 60


# ─────────────────────── Pattern ───────────────────────

# Codice fiscale: candidato volutamente largo (16 caratteri alfanumerici).
# La distinzione fra "è un CF" e "non lo è" la fa il checksum, non la regex.
_CF_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{16}(?![A-Za-z0-9])")

_PIVA_CANDIDATE = re.compile(
    r"(?<![0-9])(?:IT[\s\-]?)?\d{11}(?![0-9])", re.IGNORECASE
)

# NB: il separatore ammesso è ``[ \-]``, non ``\s``. Usare ``\s`` farebbe
# assorbire il newline al quantificatore, e il match si estenderebbe alla riga
# successiva inghiottendo la parola che segue.
_IBAN_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[ \-]?[A-Za-z0-9]){11,30}(?![A-Za-z0-9])"
)

_CARD_CANDIDATE = re.compile(r"(?<![0-9])(?:\d[ \-]?){12,19}(?![0-9])")

_TARGA_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2}[ \-]?\d{3}[ \-]?[A-Za-z]{2}(?![A-Za-z0-9])"
)

_TESSERA_CANDIDATE = re.compile(r"(?<![0-9])80380\d{15}(?![0-9])")

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z])"
)

# Telefono italiano: fisso e mobile, con o senza prefisso internazionale.
# Il fisso senza prefisso internazionale richiede un separatore fra prefisso e
# numero: senza questo vincolo qualunque sequenza di 11 cifre (una partita IVA,
# un numero di protocollo) verrebbe scambiata per un telefono.
_PHONE = re.compile(
    r"(?<![0-9])(?:"
    r"(?:\+|00)39[ .\-]?(?:0\d{1,3}[ .\-]?\d{5,8}|3\d{2}[ .\-]?\d{3}[ .\-]?\d{3,4})"
    r"|0\d{1,3}[ .\-]\d{5,8}"
    r"|3\d{2}[ .\-]?\d{3}[ .\-]?\d{3,4}"
    r")(?![0-9])"
)

# Il lookahead esclude un ulteriore ottetto ma non il punto di fine frase:
# ``(?![0-9.])`` fallirebbe su "192.168.1.44." lasciando l'IP in chiaro.
_IPV4 = re.compile(
    r"(?<![0-9.])(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(?!\.?\d)"
)

_MAC = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")

# Indirizzo italiano: toponimo + denominazione + civico.
_ADDRESS = re.compile(
    r"\b(?:via|viale|v\.le|piazza|p\.zza|corso|c\.so|largo|vicolo|strada|località|localita|contrada)"
    r"\s+[A-ZÀ-Ü][\w'À-ü.\-]*(?:\s+(?:[A-ZÀ-Ü][\w'À-ü.\-]*|d[ei']|degli|della|delle|del)){0,4}"
    r"[,\s]+n?\.?\s*\d+[/\w]*\b",
    re.IGNORECASE,
)

_DATE = re.compile(
    r"(?<![0-9])(?:[0-3]?\d[/\-.][01]?\d[/\-.](?:19|20)\d{2}"
    r"|(?:19|20)\d{2}[/\-.][01]?\d[/\-.][0-3]?\d"
    r"|[0-3]?\d\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+(?:19|20)\d{2})"
    r"(?![0-9])",
    re.IGNORECASE,
)


RECOGNIZERS: tuple[Recognizer, ...] = (
    Recognizer(
        entity_type=EntityType.IT_CODICE_FISCALE,
        pattern=_CF_CANDIDATE,
        validator=ck.validate_codice_fiscale,
        context_words=("codice fiscale", "c.f.", "cod. fisc", "cf:", "cod.fisc"),
        base_confidence=0.5,
        emit_unvalidated=False,  # gestito da ocr_recovery
    ),
    Recognizer(
        entity_type=EntityType.IT_PARTITA_IVA,
        pattern=_PIVA_CANDIDATE,
        validator=ck.validate_partita_iva,
        context_words=("partita iva", "p. iva", "p.iva", "vat", "partita i.v.a"),
        base_confidence=0.5,
    ),
    Recognizer(
        entity_type=EntityType.IBAN,
        pattern=_IBAN_CANDIDATE,
        validator=ck.validate_iban,
        context_words=("iban", "coordinate bancarie", "bonifico", "conto corrente"),
        base_confidence=0.5,
    ),
    Recognizer(
        entity_type=EntityType.CREDIT_CARD,
        pattern=_CARD_CANDIDATE,
        validator=ck.validate_luhn,
        context_words=("carta", "card", "visa", "mastercard", "pagamento"),
        base_confidence=0.4,
    ),
    Recognizer(
        entity_type=EntityType.IT_TESSERA_SANITARIA,
        pattern=_TESSERA_CANDIDATE,
        validator=ck.validate_tessera_sanitaria,
        context_words=("tessera sanitaria", "team", "assistito"),
        base_confidence=0.7,
        emit_unvalidated=True,
    ),
    Recognizer(
        entity_type=EntityType.IT_TARGA,
        pattern=_TARGA_CANDIDATE,
        validator=ck.validate_targa,
        context_words=("targa", "veicolo", "autoveicolo", "immatricolazione"),
        base_confidence=0.45,
        emit_unvalidated=False,
    ),
    Recognizer(
        entity_type=EntityType.EMAIL,
        pattern=_EMAIL,
        base_confidence=0.95,
        emit_unvalidated=True,
    ),
    Recognizer(
        entity_type=EntityType.PHONE,
        pattern=_PHONE,
        context_words=("tel", "telefono", "cell", "cellulare", "fax", "mobile"),
        base_confidence=0.6,
        emit_unvalidated=True,
    ),
    Recognizer(
        entity_type=EntityType.IP_ADDRESS,
        pattern=_IPV4,
        base_confidence=0.85,
        emit_unvalidated=True,
    ),
    Recognizer(
        entity_type=EntityType.MAC_ADDRESS,
        pattern=_MAC,
        base_confidence=0.9,
        emit_unvalidated=True,
    ),
    Recognizer(
        entity_type=EntityType.ADDRESS,
        pattern=_ADDRESS,
        context_words=("residente", "domicilio", "sede", "abitazione", "indirizzo"),
        base_confidence=0.7,
        emit_unvalidated=True,
    ),
    Recognizer(
        entity_type=EntityType.DATE,
        pattern=_DATE,
        context_words=("nato", "nata", "data di nascita", "nascita"),
        base_confidence=0.8,
        emit_unvalidated=True,
    ),
)


def _has_context(text: str, start: int, end: int, words: tuple[str, ...]) -> bool:
    if not words:
        return False
    left = text[max(0, start - CONTEXT_WINDOW) : start].lower()
    right = text[end : end + CONTEXT_WINDOW].lower()
    window = left + " " + right
    return any(w in window for w in words)


def _refine_date(text: str, start: int, end: int, matched: str) -> EntityType:
    """Distingue una data di nascita da una data qualunque usando il contesto."""
    left = text[max(0, start - CONTEXT_WINDOW) : start].lower()
    if any(k in left for k in ("nato il", "nata il", "data di nascita", "nato a", "nata a", "n. il")):
        return EntityType.DATE_OF_BIRTH
    return EntityType.DATE


def scan(text: str) -> tuple[list[Span], list[tuple[EntityType, int, int, str]]]:
    """Esegue tutti i riconoscitori sul testo.

    Restituisce ``(spans, unvalidated)`` dove ``unvalidated`` contiene i candidati
    che hanno la forma giusta ma non superano il checksum: sono l'input dello
    strato di recupero errori OCR.
    """
    spans: list[Span] = []
    unvalidated: list[tuple[EntityType, int, int, str]] = []

    for rec in RECOGNIZERS:
        for match in rec.pattern.finditer(text):
            raw = match.group(0)
            start, end = match.span()

            if rec.validator is not None:
                if rec.validator(raw):
                    confidence = 1.0
                    layer = DetectionLayer.CHECKSUM
                else:
                    unvalidated.append((rec.entity_type, start, end, raw))
                    if not rec.emit_unvalidated:
                        continue
                    confidence = rec.base_confidence
                    layer = DetectionLayer.REGEX
            else:
                confidence = rec.base_confidence
                layer = DetectionLayer.REGEX

            entity_type = rec.entity_type
            if entity_type is EntityType.DATE:
                entity_type = _refine_date(text, start, end, raw)

            if layer is DetectionLayer.REGEX and _has_context(text, start, end, rec.context_words):
                confidence = min(1.0, confidence + 0.25)

            spans.append(
                Span(
                    start=start,
                    end=end,
                    text=raw,
                    entity_type=entity_type,
                    layer=layer,
                    confidence=confidence,
                )
            )

    return spans, unvalidated
