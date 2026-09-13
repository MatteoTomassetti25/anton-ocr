"""Pseudonimizzazione: sostituzione delle entità con token deterministici.

Il token è ``⟦TIPO_XXXX⟧`` dove ``XXXX`` sono i primi 4 esadecimali di
``HMAC-SHA256(salt_documento, forma_canonica)``.

Tre proprietà, tutte deliberate:

* **stabile nel documento** — "Mario Rossi", "ROSSI Mario" e "Rossi  Mario" hanno
  la stessa forma canonica e quindi lo stesso token: il modello a valle capisce
  che si tratta della stessa persona e la risposta resta coerente;
* **non stabile fra documenti** — il salt è per documento. Token stabili su un
  intero corpus permetterebbero di collegare fra loro documenti diversi, e il
  collegamento è di per sé un vettore di re-identificazione. La stabilità di
  corpus esiste ma va chiesta esplicitamente;
* **deterministico** — stesso salt e stessa entità producono sempre lo stesso
  token. Senza questo l'audit trail non sarebbe verificabile.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from anton_ocr.privacy.checksums import decode_omocodia
from anton_ocr.privacy.entities import EntityType, Span

# Delimitatori: U+27E6 / U+27E7. Praticamente assenti dal testo naturale, quindi
# non collidono con il contenuto del documento.
TOKEN_OPEN = "⟦"
TOKEN_CLOSE = "⟧"

# Abbreviazioni usate nei token: più corte del nome del tipo, e più difficili da
# parafrasare per il modello a valle.
TYPE_ABBREV: dict[EntityType, str] = {
    EntityType.PERSON: "PER",
    EntityType.ORGANIZATION: "ORG",
    EntityType.ADDRESS: "ADR",
    EntityType.LOCATION: "LOC",
    EntityType.DATE: "DAT",
    EntityType.DATE_OF_BIRTH: "DOB",
    EntityType.EMAIL: "EML",
    EntityType.PHONE: "TEL",
    EntityType.IP_ADDRESS: "IP",
    EntityType.MAC_ADDRESS: "MAC",
    EntityType.IT_CODICE_FISCALE: "CF",
    EntityType.IT_PARTITA_IVA: "PIVA",
    EntityType.IBAN: "IBAN",
    EntityType.CREDIT_CARD: "CARD",
    EntityType.IT_TARGA: "TARGA",
    EntityType.IT_TESSERA_SANITARIA: "TS",
    EntityType.GDPR_ART9_HEALTH: "A9H",
    EntityType.GDPR_ART9_BIOMETRIC: "A9B",
    EntityType.GDPR_ART9_GENETIC: "A9G",
    EntityType.GDPR_ART9_RELIGION: "A9R",
    EntityType.GDPR_ART9_POLITICAL: "A9P",
    EntityType.GDPR_ART9_SEXUAL: "A9S",
    EntityType.GDPR_ART9_UNION: "A9U",
}

ABBREV_TO_TYPE = {v: k for k, v in TYPE_ABBREV.items()}

# Riconosce un token già emesso, sia in forma unicode sia in fallback ASCII.
# Tollera spazi interni: alcuni modelli spezzano i token in fase di generazione.
TOKEN_RE = re.compile(
    rf"(?:{TOKEN_OPEN}|\[\[)\s*([A-Z0-9]+)\s*_\s*([0-9a-f]{{4,8}})\s*(?:{TOKEN_CLOSE}|\]\])"
)


class SaltScope(str, Enum):
    DOCUMENT = "document"
    CORPUS = "corpus"


def new_salt() -> bytes:
    return secrets.token_bytes(32)


# ─────────────────────── Canonicalizzazione ───────────────────────


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def canonical_form(text: str, entity_type: EntityType) -> str:
    """Forma canonica usata per generare il token.

    Due occorrenze della stessa entità scritte in modo diverso devono collassare
    sulla stessa forma, altrimenti la stessa persona riceve token diversi e il
    testo pseudonimizzato diventa incomprensibile per il modello a valle.
    """
    value = text.strip()

    if entity_type is EntityType.IT_CODICE_FISCALE:
        return decode_omocodia(re.sub(r"[\s\-.]", "", value).upper())

    if entity_type in (
        EntityType.IT_PARTITA_IVA,
        EntityType.IBAN,
        EntityType.CREDIT_CARD,
        EntityType.IT_TARGA,
        EntityType.IT_TESSERA_SANITARIA,
    ):
        return re.sub(r"[\s\-.]", "", value).upper()

    if entity_type is EntityType.EMAIL:
        return value.lower()

    if entity_type is EntityType.PHONE:
        digits = re.sub(r"\D", "", value)
        # Il prefisso internazionale non deve produrre token diversi per lo
        # stesso numero: si confrontano le ultime 10 cifre.
        return digits[-10:] if len(digits) > 10 else digits

    if entity_type in (EntityType.PERSON, EntityType.ORGANIZATION):
        cleaned = _strip_accents(value).upper()
        cleaned = re.sub(r"[^\w\s]", " ", cleaned)
        parts = [p for p in cleaned.split() if p]
        # Ordinamento alfabetico: "Mario Rossi" e "Rossi Mario" collassano.
        # Limite noto: non gestisce le abbreviazioni ("M. Rossi").
        return " ".join(sorted(parts))

    if entity_type in (EntityType.ADDRESS, EntityType.LOCATION):
        cleaned = _strip_accents(value).upper()
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", cleaned)).strip()

    if entity_type in (EntityType.DATE, EntityType.DATE_OF_BIRTH):
        digits = re.sub(r"\D", "", value)
        return digits

    return _strip_accents(value).upper().strip()


def make_token(
    text: str,
    entity_type: EntityType,
    salt: bytes,
    *,
    length: int = 4,
    ascii_fallback: bool = False,
) -> str:
    canonical = canonical_form(text, entity_type)
    digest = hmac.new(
        salt, f"{entity_type.value}\x00{canonical}".encode(), hashlib.sha256
    ).hexdigest()
    abbrev = TYPE_ABBREV.get(entity_type, entity_type.value[:4])
    body = f"{abbrev}_{digest[:length]}"
    if ascii_fallback:
        return f"[[{body}]]"
    return f"{TOKEN_OPEN}{body}{TOKEN_CLOSE}"


# ─────────────────────── Risultato ───────────────────────


@dataclass
class PseudonymizationResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # token -> valore originale
    counts: dict[str, int] = field(default_factory=dict)  # tipo entità -> occorrenze
    tokens_issued: int = 0
    generalized: dict[str, str] = field(default_factory=dict)  # token -> valore generalizzato

    def summary(self) -> dict:
        return {
            "tokens_issued": self.tokens_issued,
            "unique_tokens": len(self.mapping),
            "by_type": dict(sorted(self.counts.items())),
        }


# ─────────────────────── Generalizzazione ───────────────────────


def generalize(text: str, entity_type: EntityType, to: str) -> str:
    """Riduce la precisione di un quasi-identificatore invece di rimuoverlo.

    Serve a limitare la re-identificazione per combinazione (età + comune + data)
    mantenendo il testo utilizzabile. Non è k-anonimato: è una riduzione di
    precisione, e va dichiarata come tale.
    """
    if to == "year":
        match = re.search(r"(19|20)\d{2}", text)
        return match.group(0) if match else "[data]"
    if to == "month":
        digits = re.findall(r"\d+", text)
        if len(digits) >= 3:
            return f"{digits[1]}/{digits[2]}"
        return "[data]"
    if to == "municipality":
        # Rimuove il numero civico, conserva il toponimo.
        return re.sub(r"[,\s]+n?\.?\s*\d+[/\w]*\s*$", "", text).strip()
    if to == "region":
        return "[località]"
    if to == "domain":
        if "@" in text:
            return "[utente]@" + text.split("@", 1)[1]
        return "[email]"
    return "[omissis]"


# ─────────────────────── Motore ───────────────────────


class Pseudonymizer:
    """Applica la sostituzione agli span rilevati.

    Le sostituzioni avvengono da destra a sinistra: così gli offset degli span
    ancora da trattare restano validi mentre il testo cambia lunghezza.
    """

    def __init__(
        self,
        salt: bytes | None = None,
        *,
        token_length: int = 4,
        ascii_fallback: bool = False,
        keep_mapping: bool = True,
    ) -> None:
        self.salt = salt or new_salt()
        self.token_length = token_length
        self.ascii_fallback = ascii_fallback
        self.keep_mapping = keep_mapping

    def apply(
        self,
        text: str,
        spans: list[Span],
        *,
        actions: dict[str, str] | None = None,
        generalizations: dict[str, str] | None = None,
    ) -> PseudonymizationResult:
        """Sostituisce gli span nel testo.

        ``actions`` associa il tipo di entità a ``pseudonymize`` | ``generalize``
        | ``keep``. Gli span con azione ``keep`` restano invariati.
        """
        actions = actions or {}
        generalizations = generalizations or {}

        result = PseudonymizationResult(text=text)
        ordered = sorted(spans, key=lambda s: s.start, reverse=True)
        buffer = text

        # Le collisioni di token sono possibili ma rarissime (4 esadecimali per
        # tipo per documento). Quando accadono si allunga il digest.
        canonical_by_token: dict[str, str] = {}

        for span in ordered:
            action = actions.get(span.entity_type.value, "pseudonymize")
            if action == "keep":
                continue

            original = span.canonical_value

            if action == "generalize":
                target = generalizations.get(span.entity_type.value, "year")
                replacement = generalize(span.text, span.entity_type, target)
                result.generalized[replacement] = span.text
            else:
                length = self.token_length
                replacement = make_token(
                    original,
                    span.entity_type,
                    self.salt,
                    length=length,
                    ascii_fallback=self.ascii_fallback,
                )
                canonical = canonical_form(original, span.entity_type)
                while (
                    replacement in canonical_by_token
                    and canonical_by_token[replacement] != canonical
                    and length < 16
                ):
                    length += 2
                    replacement = make_token(
                        original,
                        span.entity_type,
                        self.salt,
                        length=length,
                        ascii_fallback=self.ascii_fallback,
                    )
                canonical_by_token[replacement] = canonical
                if self.keep_mapping:
                    result.mapping[replacement] = span.text

            buffer = buffer[: span.start] + replacement + buffer[span.end :]
            result.tokens_issued += 1
            key = span.entity_type.value
            result.counts[key] = result.counts.get(key, 0) + 1

        result.text = buffer
        return result
