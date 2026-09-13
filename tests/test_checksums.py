"""Test dei validatori. Gli identificatori usati sono sintetici o pubblici."""

import pytest

from anton_ocr.privacy.checksums import (
    cf_check_char,
    decode_omocodia,
    validate_codice_fiscale,
    validate_iban,
    validate_luhn,
    validate_partita_iva,
    validate_targa,
    validate_tessera_sanitaria,
)


class TestCodiceFiscale:
    def test_cin_noto(self):
        assert cf_check_char("RSSMRA85T10A562") == "S"

    @pytest.mark.parametrize(
        "cf",
        ["RSSMRA85T10A562S", "rssmra85t10a562s", "RSS MRA 85T10 A562S", "RSSMRA85T10A562S "],
    )
    def test_validi_con_normalizzazione(self, cf):
        assert validate_codice_fiscale(cf)

    @pytest.mark.parametrize(
        "cf,motivo",
        [
            ("RSSMRA85T10A562A", "carattere di controllo errato"),
            ("RSSMRA85T10A562", "troppo corto"),
            ("RSSMRA85Z10A562S", "mese inesistente"),
            ("RSSMRA85T99A562S", "giorno fuori range"),
            ("RSSMRA85T10A562SX", "troppo lungo"),
            ("", "vuoto"),
        ],
    )
    def test_invalidi(self, cf, motivo):
        assert not validate_codice_fiscale(cf), motivo

    def test_giorno_femminile_ammesso(self):
        # Le donne hanno il giorno maggiorato di 40: 41–71.
        base = "RSSMRA85T50A562"
        assert validate_codice_fiscale(base + cf_check_char(base))

    def test_giorno_oltre_71_rifiutato(self):
        base = "RSSMRA85T72A562"
        assert not validate_codice_fiscale(base + cf_check_char(base))

    def test_stringa_casuale_non_valida(self):
        """Il vincolo su mese e giorno è ciò che evita i falsi positivi."""
        assert not validate_codice_fiscale("ABCDEF12C34H567I")

    def test_omocodia(self):
        assert decode_omocodia("RSSMRA85T1MA562S") == "RSSMRA85T11A562S"
        assert decode_omocodia("RSSMRA85T10A562S") == "RSSMRA85T10A562S"


class TestPartitaIva:
    def test_valida(self):
        assert validate_partita_iva("00743110157")

    def test_con_separatori(self):
        assert validate_partita_iva("00743110157")
        assert validate_partita_iva("IT 00743110157".replace("IT ", ""))

    @pytest.mark.parametrize("piva", ["00743110158", "1234567890", "abcdefghijk", ""])
    def test_invalide(self, piva):
        assert not validate_partita_iva(piva)


class TestIban:
    @pytest.mark.parametrize(
        "iban",
        [
            "IT60X0542811101000000123456",
            "DE89370400440532013000",
            "GB82WEST12345698765432",
            "IT60 X054 2811 1010 0000 0123 456",
        ],
    )
    def test_validi(self, iban):
        assert validate_iban(iban)

    @pytest.mark.parametrize(
        "iban",
        [
            "IT60X0542811101000000123457",  # checksum errato
            "IT60X05428111010000001234",  # lunghezza errata per IT
            "XX00ABCDEFGHIJKLMNOP",
            "",
        ],
    )
    def test_invalidi(self, iban):
        assert not validate_iban(iban)


class TestLuhn:
    def test_valido(self):
        assert validate_luhn("4539578763621486")

    def test_con_separatori(self):
        assert validate_luhn("4539 5787 6362 1486")

    @pytest.mark.parametrize("card", ["4539578763621487", "1234567812345678", "123", ""])
    def test_invalidi(self, card):
        assert not validate_luhn(card)


class TestTarga:
    @pytest.mark.parametrize("targa", ["AB123CD", "ab123cd", "AB 123 CD"])
    def test_valide(self, targa):
        assert validate_targa(targa)

    @pytest.mark.parametrize(
        "targa,motivo",
        [
            ("AI123CD", "la I non è ammessa"),
            ("AO123CD", "la O non è ammessa"),
            ("AQ123CD", "la Q non è ammessa"),
            ("AU123CD", "la U non è ammessa"),
            ("AB12CD", "cifre insufficienti"),
        ],
    )
    def test_invalide(self, targa, motivo):
        assert not validate_targa(targa), motivo


class TestTesseraSanitaria:
    def test_valida(self):
        assert validate_tessera_sanitaria("80380000000000000001")

    def test_prefisso_errato(self):
        assert not validate_tessera_sanitaria("12380000000000000001")
