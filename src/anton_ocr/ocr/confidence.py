"""Valutazione euristica della qualità del testo estratto.

Perché serve: senza una misura di qualità non si può decidere quando mandare una
pagina a revisione umana, e soprattutto non si può sapere *quando non fidarsi del
rilevamento PII*. Su una pagina letta male il rilevamento perde entità, e se
nessuno se ne accorge il risultato è una fuga di dati silenziosa.

Cosa questo modulo **non** è: non è la confidenza del modello. È un'euristica
sul testo prodotto, calcolata senza modelli e senza costo. Ha il pregio di
funzionare identica su ogni backend (testo nativo, MLX, Ollama) e di essere
deterministica, quindi verificabile.

I segnali sono deliberatamente semplici e ognuno cattura un modo tipico in cui
l'OCR fallisce; il punteggio finale è la loro media pesata.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Un testo italiano normale ha una frazione elevata di caratteri alfabetici,
# parole di lunghezza plausibile e poche sequenze anomale.

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
_TOKEN_RE = re.compile(r"\S+")
_REPEATED_RE = re.compile(r"(.)\1{4,}")
_ISOLATED_RE = re.compile(r"(?<!\S)[A-Za-zÀ-ÿ](?!\S)")

# Caratteri che un motore OCR produce quando non riesce a decidere.
_SUSPECT_CHARS = set("�□■▪◆♦¶§~^`¬")

# Vocali italiane: un testo senza vocali è quasi sempre spazzatura.
_VOWELS = set("aeiouàèéìòùAEIOUÀÈÉÌÒÙ")


@dataclass
class PageQuality:
    score: float
    signals: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        return self.score >= 0.75

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "signals": {k: round(v, 3) for k, v in self.signals.items()},
            "notes": self.notes,
        }


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def assess_text_quality(text: str, *, min_chars: int = 20) -> PageQuality:
    """Assegna un punteggio 0..1 alla plausibilità del testo estratto."""
    stripped = text.strip()

    if not stripped:
        return PageQuality(
            score=0.0,
            signals={},
            notes=["Nessun testo estratto dalla pagina"],
        )

    if len(stripped) < min_chars:
        return PageQuality(
            score=0.35,
            signals={"length": float(len(stripped))},
            notes=[f"Testo molto breve ({len(stripped)} caratteri): valutazione poco affidabile"],
        )

    total = len(stripped)
    notes: list[str] = []

    # ① Densità alfabetica — l'OCR degradato produce simboli e cifre sparse.
    letters = sum(1 for c in stripped if c.isalpha())
    digits = sum(1 for c in stripped if c.isdigit())
    spaces = sum(1 for c in stripped if c.isspace())
    punctuation = sum(1 for c in stripped if unicodedata.category(c).startswith("P"))
    meaningful = letters + digits + spaces + punctuation
    alpha_density = _ratio(meaningful, total)

    # ② Caratteri sospetti — segnale diretto di fallimento del riconoscimento.
    suspect = sum(1 for c in stripped if c in _SUSPECT_CHARS)
    suspect_penalty = 1.0 - min(1.0, _ratio(suspect, total) * 20)
    if suspect > 0:
        notes.append(f"{suspect} carattere/i di sostituzione o simboli anomali")

    # ③ Struttura delle parole — parole di 2+ lettere su token totali.
    tokens = _TOKEN_RE.findall(stripped)
    words = _WORD_RE.findall(stripped)
    word_ratio = _ratio(len(words), max(len(tokens), 1))

    # ④ Lunghezza media delle parole — l'OCR frammentato produce parole di 1-2
    # caratteri, quello incollato parole lunghissime.
    if words:
        avg_len = sum(len(w) for w in words) / len(words)
        if avg_len < 2.5:
            length_score = 0.4
            notes.append(f"Parole molto corte in media ({avg_len:.1f}): testo frammentato")
        elif avg_len > 14:
            length_score = 0.5
            notes.append(f"Parole molto lunghe in media ({avg_len:.1f}): spazi persi")
        else:
            length_score = 1.0
    else:
        avg_len = 0.0
        length_score = 0.0
        notes.append("Nessuna parola riconoscibile")

    # ⑤ Caratteri isolati — "l a p a r o l a" è un fallimento tipico.
    isolated = len(_ISOLATED_RE.findall(stripped))
    isolated_penalty = 1.0 - min(1.0, _ratio(isolated, max(len(tokens), 1)) * 3)
    if isolated > len(tokens) * 0.2:
        notes.append(f"{isolated} caratteri isolati: spaziatura non affidabile")

    # ⑥ Ripetizioni anomale — "aaaaaa", "......." indicano rumore.
    repeated = len(_REPEATED_RE.findall(stripped))
    repeated_penalty = 1.0 - min(1.0, repeated / 10)
    if repeated:
        notes.append(f"{repeated} sequenza/e di caratteri ripetuti")

    # ⑦ Presenza di vocali — un testo alfabetico senza vocali non è linguaggio.
    if letters:
        vowel_ratio = _ratio(sum(1 for c in stripped if c in _VOWELS), letters)
        vowel_score = 1.0 if 0.2 <= vowel_ratio <= 0.65 else 0.45
        if vowel_score < 1.0:
            notes.append(f"Proporzione di vocali anomala ({vowel_ratio:.0%})")
    else:
        vowel_score = 0.0

    signals = {
        "alpha_density": alpha_density,
        "suspect_chars": suspect_penalty,
        "word_ratio": word_ratio,
        "word_length": length_score,
        "isolated_chars": isolated_penalty,
        "repetitions": repeated_penalty,
        "vowels": vowel_score,
        "avg_word_length": avg_len,
    }

    weights = {
        "alpha_density": 0.20,
        "suspect_chars": 0.15,
        "word_ratio": 0.20,
        "word_length": 0.15,
        "isolated_chars": 0.10,
        "repetitions": 0.05,
        "vowels": 0.15,
    }

    score = sum(signals[k] * w for k, w in weights.items())
    score = max(0.0, min(1.0, score))

    if score < 0.75:
        notes.append(
            "Qualità sotto soglia: il rilevamento PII su questa pagina potrebbe "
            "aver perso entità presenti nell'originale."
        )

    return PageQuality(score=score, signals=signals, notes=notes)


def aggregate(qualities: list[PageQuality]) -> tuple[float, list[int]]:
    """Confidenza media del documento e indici (1-based) delle pagine sotto soglia."""
    if not qualities:
        return 1.0, []
    mean = sum(q.score for q in qualities) / len(qualities)
    below = [i + 1 for i, q in enumerate(qualities) if not q.is_reliable]
    return mean, below
