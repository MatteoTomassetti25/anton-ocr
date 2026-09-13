"""Re-idratazione: reinserisce i valori reali nella risposta del modello.

È il completamento del giro:

    documento → pseudonimizzazione → modello di frontiera → risposta con token
              → re-idratazione locale → risposta leggibile

Il provider del modello non vede mai un dato personale; l'utente legge comunque
una risposta completa.

Tre modi di fallire, tutti gestiti esplicitamente perché il silenzio qui sarebbe
peggio dell'errore:

1. il modello **parafrasa** il token (⟦PER_7f3a⟧ → "la persona indicata"):
   il token non torna indietro e va segnalato;
2. il modello **inventa** un token mai emesso: non si sostituisce nulla, mai si
   tira a indovinare;
3. il modello **spezza** il token con spazi: il matching è tollerante.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from anton_ocr.privacy.pseudonymize import TOKEN_RE


@dataclass
class RehydrationReport:
    """Diagnostica del giro di ritorno.

    ``coverage`` è l'informazione che l'utente deve vedere: se il modello ha
    restituito pochi dei token emessi, la risposta copre solo una parte del
    documento e va letta con quella consapevolezza.
    """

    text: str
    substituted: int = 0
    tokens_issued: int = 0
    tokens_returned: int = 0
    unknown_tokens: list[str] = field(default_factory=list)
    missing_tokens: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        if not self.tokens_issued:
            return 1.0
        return self.tokens_returned / self.tokens_issued

    @property
    def clean(self) -> bool:
        return not self.unknown_tokens and not self.warnings

    def summary(self) -> dict:
        return {
            "substituted": self.substituted,
            "tokens_issued": self.tokens_issued,
            "tokens_returned": self.tokens_returned,
            "coverage": round(self.coverage, 3),
            "unknown_tokens": self.unknown_tokens,
            "missing_tokens": self.missing_tokens,
            "warnings": self.warnings,
        }


def _normalize_token(abbrev: str, digest: str) -> str:
    return f"⟦{abbrev}_{digest}⟧"


def rehydrate(
    response: str,
    mapping: dict[str, str],
    *,
    strict: bool = False,
) -> RehydrationReport:
    """Sostituisce i token con i valori originali.

    Con ``strict=True`` la presenza di un token sconosciuto solleva un'eccezione
    invece di limitarsi a segnalarlo: utile nelle pipeline automatiche, dove un
    token inventato dal modello indica che qualcosa è andato storto a monte.
    """
    report = RehydrationReport(text=response, tokens_issued=len(mapping))

    # Indice tollerante: chiave = (abbreviazione, digest), indipendente dai
    # delimitatori usati e da eventuali spazi introdotti dal modello.
    index: dict[tuple[str, str], str] = {}
    for token, value in mapping.items():
        match = TOKEN_RE.search(token)
        if match:
            index[(match.group(1), match.group(2))] = value

    seen: set[tuple[str, str]] = set()
    unknown: list[str] = []

    def _replace(match) -> str:
        key = (match.group(1), match.group(2))
        if key in index:
            seen.add(key)
            return index[key]
        raw = match.group(0)
        if raw not in unknown:
            unknown.append(raw)
        # Token mai emesso: si lascia il testo com'è. Indovinare sarebbe peggio
        # che non sostituire.
        return raw

    text = TOKEN_RE.sub(_replace, response)

    report.text = text
    report.substituted = len(seen)
    report.tokens_returned = len(seen)
    report.unknown_tokens = unknown

    missing = [
        _normalize_token(abbrev, digest)
        for (abbrev, digest) in index
        if (abbrev, digest) not in seen
    ]
    report.missing_tokens = sorted(missing)

    if unknown:
        report.warnings.append(
            f"{len(unknown)} token non emessi da questo documento sono stati lasciati invariati: "
            "il modello potrebbe averli inventati."
        )
        if strict:
            raise ValueError(f"Token sconosciuti nella risposta: {unknown}")

    if report.tokens_issued and report.coverage < 0.5:
        report.warnings.append(
            f"Solo il {report.coverage:.0%} dei token emessi compare nella risposta: "
            "il modello potrebbe averli parafrasati o ignorati."
        )

    return report


SYSTEM_PROMPT_HINT = (
    "Il testo contiene segnaposto nella forma ⟦TIPO_XXXX⟧ che sostituiscono dati "
    "personali. Riportali SEMPRE identici, carattere per carattere, senza tradurli, "
    "spiegarli, riformularli o aggiungerne di nuovi. Trattali come nomi propri opachi."
)
"""Istruzione da anteporre al prompt del modello di frontiera.

Non è una garanzia — è una riduzione del tasso di parafrasi. La verifica vera la
fa ``rehydrate`` contando i token effettivamente restituiti.
"""
