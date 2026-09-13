"""Strato 1.5 — recupero degli identificatori danneggiati dall'OCR.

Premessa: la letteratura indica gli errori OCR come causa primaria dei mancati
oscuramenti. Una sequenza come ``RSSMRA85MO1H5O1Z`` (lettera O al posto della
cifra 0) non supera il checksum del codice fiscale e, in una pipeline classica,
viene semplicemente scartata: il codice fiscale finisce in chiaro nel prompt.

Qui il ragionamento è rovesciato:

    un checksum fallito non è un rifiuto, è il segnale di un probabile errore OCR.

L'algoritmo procede in due fasi, entrambe **deterministiche**:

Fase 1 — correzione guidata dalla forma.
    Gli identificatori strutturati hanno una forma nota (il codice fiscale è
    LLLLLL DD L DD L DDD L). Se in una posizione che deve contenere una cifra
    troviamo una lettera, la classe attesa ci dice già in cosa va corretta.
    Nessuna ricerca: è una deduzione.

Fase 2 — ricerca limitata sulle confusioni interne alla classe.
    Se dopo la fase 1 il checksum ancora non torna, si esplorano le confusioni
    all'interno della stessa classe (O↔Q, 8↔0, ...) con al più ``max_edits``
    sostituzioni. Lo spazio di ricerca resta nell'ordine delle migliaia di
    varianti, quindi il costo è trascurabile.

Esiti possibili:
    * una sola variante valida  → recupero con alta confidenza
    * più varianti valide       → si redige comunque (prudenza) e si marca per review
    * nessuna variante valida   → non era un identificatore, oppure l'OCR è
                                  troppo degradato: in profilo strict va a review
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

from anton_ocr.privacy import checksums as ck
from anton_ocr.privacy.entities import DetectionLayer, EntityType, Span

# ─────────────────────── Tabelle di confusione ───────────────────────
# Derivate dalle confusioni tipiche dei motori OCR su documenti stampati.

# Carattere letto → cifre plausibili che l'originale poteva essere.
TO_DIGIT: dict[str, tuple[str, ...]] = {
    "O": ("0",), "Q": ("0",), "D": ("0",), "U": ("0",),
    "I": ("1",), "L": ("1",), "T": ("7", "1"),
    "S": ("5",), "B": ("8",), "Z": ("2",), "G": ("6", "9"),
    "A": ("4",), "R": ("2",), "E": ("3",), "J": ("1",),
    "|": ("1",), "!": ("1",), "?": ("7",),
}

# Carattere letto → lettere plausibili che l'originale poteva essere.
TO_LETTER: dict[str, tuple[str, ...]] = {
    "0": ("O", "Q", "D"),
    "1": ("I", "L", "T"),
    "2": ("Z", "R"),
    "3": ("E", "B"),
    "4": ("A",),
    "5": ("S",),
    "6": ("G", "C"),
    "7": ("T", "I"),
    "8": ("B",),
    "9": ("G", "Q"),
    "|": ("I",),
}

# Confusioni interne alla classe: cifra letta → altre cifre plausibili.
DIGIT_DIGIT: dict[str, tuple[str, ...]] = {
    "0": ("8", "6", "9"),
    "1": ("7", "4"),
    "2": ("7",),
    "3": ("8", "9", "5"),
    "4": ("9", "1"),
    "5": ("6", "8", "3"),
    "6": ("8", "5", "0"),
    "7": ("1", "2"),
    "8": ("0", "6", "3", "9"),
    "9": ("0", "8", "4", "3"),
}

# Confusioni interne alla classe: lettera letta → altre lettere plausibili.
LETTER_LETTER: dict[str, tuple[str, ...]] = {
    "A": ("R", "H"), "B": ("R", "P", "E"), "C": ("G", "O", "E"),
    "D": ("O", "B", "P"), "E": ("F", "B", "C"), "F": ("E", "P", "T"),
    "G": ("C", "O", "Q"), "H": ("N", "M", "A"), "I": ("L", "J", "T"),
    "J": ("I", "L"), "K": ("R", "X"), "L": ("I", "C"),
    "M": ("N", "H", "W"), "N": ("M", "H", "W"), "O": ("Q", "D", "C", "G"),
    "P": ("R", "F", "D"), "Q": ("O", "G"), "R": ("B", "P", "K"),
    "S": ("G",), "T": ("I", "Y", "F"), "U": ("V", "O"),
    "V": ("U", "Y", "W"), "W": ("V", "M", "N"), "X": ("K", "Y"),
    "Y": ("V", "T", "X"), "Z": ("S",),
}

# ─────────────────────── Forme degli identificatori ───────────────────────
# 'L' = lettera, 'D' = cifra, '*' = alfanumerico (nessun vincolo di classe)

SHAPES: dict[EntityType, str] = {
    EntityType.IT_CODICE_FISCALE: "LLLLLLDDLDDLDDDL",
    EntityType.IT_PARTITA_IVA: "DDDDDDDDDDD",
    EntityType.IT_TARGA: "LLDDDLL",
}

VALIDATORS = {
    EntityType.IT_CODICE_FISCALE: ck.validate_codice_fiscale,
    EntityType.IT_PARTITA_IVA: ck.validate_partita_iva,
    EntityType.IT_TARGA: ck.validate_targa,
    EntityType.IBAN: ck.validate_iban,
    EntityType.CREDIT_CARD: ck.validate_luhn,
}

_SEPARATORS = re.compile(r"[\s\-.]")


@dataclass(frozen=True)
class Recovery:
    """Esito di un tentativo di recupero."""

    original: str
    corrected: str
    edits: int
    ambiguous: bool
    note: str

    @property
    def confidence(self) -> float:
        """Confidenza del recupero.

        Cala con il numero di correzioni e crolla se esistono più soluzioni valide:
        in quel caso il valore corretto non è determinato, anche se la presenza di
        un identificatore lo è.
        """
        if self.ambiguous:
            return 0.55
        return max(0.6, 1.0 - 0.12 * self.edits)


def _matches_class(ch: str, cls: str) -> bool:
    if cls == "L":
        return ch.isalpha()
    if cls == "D":
        return ch.isdigit()
    return ch.isalnum()


def _cross_class_options(ch: str, cls: str) -> tuple[str, ...]:
    """Alternative plausibili per portare ``ch`` nella classe ``cls``."""
    if cls == "D":
        return TO_DIGIT.get(ch, ())
    if cls == "L":
        return TO_LETTER.get(ch, ())
    return ()


def _same_class_options(ch: str) -> tuple[str, ...]:
    if ch.isdigit():
        return DIGIT_DIGIT.get(ch, ())
    return LETTER_LETTER.get(ch, ())


def _bounded_product(options: list[tuple[int, tuple[str, ...]]], limit: int):
    """Genera le combinazioni di sostituzione, fermandosi a ``limit`` varianti."""
    if not options:
        yield {}
        return
    positions = [p for p, _ in options]
    choices = [alts for _, alts in options]
    count = 0
    for combo in product(*choices):
        if count >= limit:
            return
        count += 1
        yield dict(zip(positions, combo, strict=False))


def _apply(chars: list[str], subs: dict[int, str]) -> str:
    out = list(chars)
    for pos, ch in subs.items():
        out[pos] = ch
    return "".join(out)


def recover(
    candidate: str,
    entity_type: EntityType,
    *,
    max_ambiguous_positions: int = 6,
    max_edits: int = 2,
    variant_limit: int = 50_000,
    allow_intra_class: bool = False,
) -> Recovery | None:
    """Tenta di ricostruire un identificatore danneggiato dall'OCR.

    ``allow_intra_class`` abilita la fase 2. È disattivata di default e va accesa
    solo quando il contesto testuale sostiene l'ipotesi (es. l'etichetta "Codice
    fiscale:" accanto al candidato). Motivo: la fase 1 è una deduzione dalla forma
    e non inventa nulla, mentre la fase 2 è una ricerca e su testo qualunque
    finirebbe per "recuperare" identificatori che non esistono. La precisione qui
    conta quanto il recall: uno strumento che redige a caso è inutilizzabile.

    Restituisce ``None`` se il candidato non è recuperabile — il che è, di per sé,
    l'informazione che con ogni probabilità non era un identificatore.
    """
    validator = VALIDATORS.get(entity_type)
    if validator is None:
        return None

    normalized = _SEPARATORS.sub("", candidate).upper()
    if validator(normalized):
        # Il candidato era già valido a meno dei separatori: nessun recupero necessario.
        return None

    shape = SHAPES.get(entity_type)
    if shape is None or len(normalized) != len(shape):
        return None

    chars = list(normalized)

    # ── Fase 1: correzione guidata dalla forma ──
    cross_options: list[tuple[int, tuple[str, ...]]] = []
    for i, (ch, cls) in enumerate(zip(chars, shape, strict=False)):
        if _matches_class(ch, cls):
            continue
        alts = _cross_class_options(ch, cls)
        if not alts:
            # Carattere fuori classe e non riconducibile: candidato non recuperabile.
            return None
        cross_options.append((i, alts))

    if len(cross_options) > max_ambiguous_positions:
        return None

    solutions: set[str] = set()
    for subs in _bounded_product(cross_options, variant_limit):
        variant = _apply(chars, subs)
        if validator(variant):
            solutions.add(variant)

    if solutions:
        best = sorted(solutions)[0]
        return Recovery(
            original=normalized,
            corrected=best,
            edits=len(cross_options),
            ambiguous=len(solutions) > 1,
            note=(
                f"forma: {len(cross_options)} carattere/i fuori classe corretto/i"
                + (f"; {len(solutions)} soluzioni valide" if len(solutions) > 1 else "")
            ),
        )

    # ── Fase 2: ricerca limitata sulle confusioni interne alla classe ──
    # Attiva solo se il contesto sostiene l'ipotesi (vedi ``allow_intra_class``).
    if not allow_intra_class:
        return None

    # Si parte sempre dalla correzione di forma (obbligatoria) e si aggiungono
    # da 1 a max_edits sostituzioni intra-classe.
    base_subs_list = list(_bounded_product(cross_options, 512))

    for extra in range(1, max_edits + 1):
        found: set[str] = set()
        for base in base_subs_list:
            base_applied = _apply(chars, base)
            free_positions = [i for i in range(len(chars)) if i not in base]
            same_options = [
                (i, _same_class_options(base_applied[i]))
                for i in free_positions
                if _same_class_options(base_applied[i])
            ]
            for combo in _combinations_of_size(same_options, extra, variant_limit):
                variant = _apply(list(base_applied), combo)
                if validator(variant):
                    found.add(variant)
        if found:
            best = sorted(found)[0]
            return Recovery(
                original=normalized,
                corrected=best,
                edits=len(cross_options) + extra,
                ambiguous=len(found) > 1,
                note=(
                    f"forma + {extra} sostituzione/i intra-classe"
                    + (f"; {len(found)} soluzioni valide" if len(found) > 1 else "")
                ),
            )

    return None


def _combinations_of_size(
    options: list[tuple[int, tuple[str, ...]]], size: int, limit: int
):
    """Tutte le sostituzioni che coinvolgono esattamente ``size`` posizioni."""
    from itertools import combinations

    count = 0
    for positions in combinations(options, size):
        choices = [alts for _, alts in positions]
        idxs = [p for p, _ in positions]
        for combo in product(*choices):
            if count >= limit:
                return
            count += 1
            yield dict(zip(idxs, combo, strict=False))


# Etichette che, se presenti accanto al candidato, autorizzano la ricerca di fase 2.
CONTEXT_HINTS: dict[EntityType, tuple[str, ...]] = {
    EntityType.IT_CODICE_FISCALE: (
        "codice fiscale", "cod. fiscale", "cod.fiscale", "cod. fisc", "c.f.", "cf ", "cf:", "codfisc",
    ),
    EntityType.IT_PARTITA_IVA: ("partita iva", "p. iva", "p.iva", "partita i.v.a", "vat"),
    EntityType.IT_TARGA: ("targa", "veicolo", "autoveicolo", "immatricolazione"),
}

_CONTEXT_WINDOW = 60


def has_context_support(text: str, start: int, end: int, entity_type: EntityType) -> bool:
    hints = CONTEXT_HINTS.get(entity_type, ())
    if not hints:
        return False
    window = (
        text[max(0, start - _CONTEXT_WINDOW) : start] + " " + text[end : end + _CONTEXT_WINDOW]
    ).lower()
    return any(h in window for h in hints)


def recover_spans(
    text: str,
    unvalidated: list[tuple[EntityType, int, int, str]],
    *,
    max_ambiguous_positions: int = 6,
    max_edits: int = 2,
) -> list[Span]:
    """Applica il recupero a tutti i candidati non validati e produce gli span.

    La fase 2 viene abilitata per singolo candidato in base al contesto testuale.
    """
    spans: list[Span] = []
    for entity_type, start, end, raw in unvalidated:
        result = recover(
            raw,
            entity_type,
            max_ambiguous_positions=max_ambiguous_positions,
            max_edits=max_edits,
            allow_intra_class=has_context_support(text, start, end, entity_type),
        )
        if result is None:
            continue
        spans.append(
            Span(
                start=start,
                end=end,
                text=raw,
                entity_type=entity_type,
                layer=DetectionLayer.OCR_RECOVERY,
                confidence=result.confidence,
                ocr_corrected=True,
                corrected_text=result.corrected,
                correction_note=result.note,
                metadata={"edits": result.edits, "ambiguous": result.ambiguous},
            )
        )
    return spans
