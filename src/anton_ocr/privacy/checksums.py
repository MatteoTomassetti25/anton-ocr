"""Validazione tramite checksum degli identificatori italiani ed europei.

La validazione con checksum è ciò che rende lo strato regex quasi privo di falsi
positivi: una sequenza che ha la forma di un codice fiscale ma non ne supera il
carattere di controllo quasi certamente non è un codice fiscale — oppure è un
codice fiscale letto male dall'OCR, ed è questo il caso che ``ocr_recovery``
sfrutta per recuperare i falsi negativi.

Nessuna funzione qui dentro ha effetti collaterali o accede alla rete.
"""

from __future__ import annotations

import re

# ─────────────────────── Codice Fiscale ───────────────────────
# Tabelle ufficiali per il carattere di controllo (CIN), D.M. 23/12/1976.

_CF_ODD = {
    "0": 1, "1": 0, "2": 5, "3": 7, "4": 9, "5": 13, "6": 15, "7": 17, "8": 19, "9": 21,
    "A": 1, "B": 0, "C": 5, "D": 7, "E": 9, "F": 13, "G": 15, "H": 17, "I": 19, "J": 21,
    "K": 2, "L": 4, "M": 18, "N": 20, "O": 11, "P": 3, "Q": 6, "R": 8, "S": 12, "T": 14,
    "U": 16, "V": 10, "W": 22, "X": 25, "Y": 24, "Z": 23,
}

_CF_EVEN = {
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5, "G": 6, "H": 7, "I": 8, "J": 9,
    "K": 10, "L": 11, "M": 12, "N": 13, "O": 14, "P": 15, "Q": 16, "R": 17, "S": 18,
    "T": 19, "U": 20, "V": 21, "W": 22, "X": 23, "Y": 24, "Z": 25,
}

# Mesi validi nel codice fiscale
_CF_MONTHS = set("ABCDEHLMPRST")

CF_PATTERN = re.compile(r"^[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]$")

# Sostituzioni di omocodia: cifra -> lettera, per le posizioni numeriche.
_OMOCODIA_TO_DIGIT = {
    "L": "0", "M": "1", "N": "2", "P": "3", "Q": "4",
    "R": "5", "S": "6", "T": "7", "U": "8", "V": "9",
}
# Posizioni (0-indexed) che in origine contengono cifre e che l'omocodia può
# sostituire con lettere: anno (6,7), giorno (9,10), codice catastale (12,13,14).
_OMOCODIA_POSITIONS = (6, 7, 9, 10, 12, 13, 14)


def cf_check_char(first15: str) -> str:
    """Calcola il carattere di controllo dai primi 15 caratteri."""
    if len(first15) != 15:
        raise ValueError("Servono esattamente 15 caratteri")
    total = 0
    for i, ch in enumerate(first15.upper()):
        table = _CF_ODD if i % 2 == 0 else _CF_EVEN  # posizione 1-based dispari => indice pari
        if ch not in table:
            raise ValueError(f"Carattere non valido nel codice fiscale: {ch!r}")
        total += table[ch]
    return chr(ord("A") + total % 26)


def normalize_cf(value: str) -> str:
    return re.sub(r"[\s\-.]", "", value).upper()


def validate_codice_fiscale(value: str) -> bool:
    """True se ``value`` è un codice fiscale formalmente valido.

    Verifica forma, mese, giorno e carattere di controllo. I vincoli semantici su
    mese e giorno non sono un dettaglio: senza di essi una stringa alfanumerica
    casuale di 16 caratteri supera il solo CIN circa una volta su 26, e lo strato
    di recupero OCR trasformerebbe quel rumore in falsi positivi.
    """
    cf = normalize_cf(value)
    if not CF_PATTERN.match(cf):
        return False
    if cf[8] not in _CF_MONTHS:
        return False
    # Giorno: 01–31 per i maschi, 41–71 per le femmine (+40).
    day = int(cf[9:11])
    if not (1 <= day <= 31 or 41 <= day <= 71):
        return False
    try:
        return cf_check_char(cf[:15]) == cf[15]
    except ValueError:
        return False


def decode_omocodia(value: str) -> str:
    """Riporta un codice fiscale con omocodia alla sua forma numerica di base.

    Utile per la canonicalizzazione: due varianti omocodiche della stessa persona
    condividono la stessa forma di base.
    """
    cf = normalize_cf(value)
    if len(cf) != 16:
        return cf
    chars = list(cf)
    for pos in _OMOCODIA_POSITIONS:
        if chars[pos] in _OMOCODIA_TO_DIGIT:
            chars[pos] = _OMOCODIA_TO_DIGIT[chars[pos]]
    return "".join(chars)


# ─────────────────────── Partita IVA ───────────────────────

PIVA_PATTERN = re.compile(r"^\d{11}$")


def validate_partita_iva(value: str) -> bool:
    """Checksum della partita IVA italiana (variante di Luhn su 11 cifre)."""
    piva = re.sub(r"[\s\-.]", "", value)
    if not PIVA_PATTERN.match(piva):
        return False
    total = 0
    for i, ch in enumerate(piva[:10]):
        digit = int(ch)
        if i % 2 == 1:  # posizioni pari in notazione 1-based
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    check = (10 - total % 10) % 10
    return check == int(piva[10])


# ─────────────────────── IBAN ───────────────────────

IBAN_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")

# Lunghezze IBAN per i paesi SEPA più comuni. Se il paese non è in tabella,
# si valida comunque il mod-97 senza vincolo di lunghezza.
IBAN_LENGTHS = {
    "IT": 27, "SM": 27, "FR": 27, "DE": 22, "ES": 24, "PT": 25, "NL": 18,
    "BE": 16, "AT": 20, "IE": 22, "LU": 20, "FI": 18, "GR": 27, "CH": 21,
    "GB": 22, "PL": 28, "SE": 24, "DK": 18, "NO": 15, "CZ": 24, "HR": 21,
    "SI": 19, "SK": 24, "RO": 24, "BG": 22, "HU": 28, "LT": 20, "LV": 21,
    "EE": 20, "MT": 31, "CY": 28,
}


def normalize_iban(value: str) -> str:
    return re.sub(r"[\s\-]", "", value).upper()


def validate_iban(value: str) -> bool:
    """Validazione ISO 7064 mod-97-10."""
    iban = normalize_iban(value)
    if not IBAN_PATTERN.match(iban):
        return False
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected is not None and len(iban) != expected:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        elif "A" <= ch <= "Z":
            digits.append(str(ord(ch) - ord("A") + 10))
        else:
            return False
    return int("".join(digits)) % 97 == 1


# ─────────────────────── Luhn (carte di pagamento) ───────────────────────


def validate_luhn(value: str) -> bool:
    digits = re.sub(r"[\s\-]", "", value)
    if not digits.isdigit() or not 12 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ─────────────────────── Targa italiana ───────────────────────

# Formato in vigore dal 1994: due lettere, tre cifre, due lettere.
# Le lettere I, O, Q, U non sono ammesse per evitare confusione con le cifre.
TARGA_PATTERN = re.compile(r"^[ABCDEFGHJKLMNPRSTVWXYZ]{2}\d{3}[ABCDEFGHJKLMNPRSTVWXYZ]{2}$")


def normalize_targa(value: str) -> str:
    return re.sub(r"[\s\-.]", "", value).upper()


def validate_targa(value: str) -> bool:
    return bool(TARGA_PATTERN.match(normalize_targa(value)))


# ─────────────────────── Tessera sanitaria (TEAM) ───────────────────────

# Numero identificativo della tessera: 20 cifre, prefisso nazionale italiano 80380.
TESSERA_PATTERN = re.compile(r"^80380\d{15}$")


def validate_tessera_sanitaria(value: str) -> bool:
    return bool(TESSERA_PATTERN.match(re.sub(r"[\s\-]", "", value)))


# ─────────────────────── Registro ───────────────────────

VALIDATORS = {
    "IT_CODICE_FISCALE": validate_codice_fiscale,
    "IT_PARTITA_IVA": validate_partita_iva,
    "IBAN": validate_iban,
    "CREDIT_CARD": validate_luhn,
    "IT_TARGA": validate_targa,
    "IT_TESSERA_SANITARIA": validate_tessera_sanitaria,
}
