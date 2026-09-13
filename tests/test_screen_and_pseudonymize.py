"""Test del rilevamento, della pseudonimizzazione e del giro di ritorno."""

import re

import pytest

from anton_ocr.privacy.entities import EntityType
from anton_ocr.privacy.pseudonymize import (
    Pseudonymizer,
    canonical_form,
    generalize,
    make_token,
)
from anton_ocr.privacy.rehydrate import rehydrate
from anton_ocr.privacy.screen import ScreenConfig, Screener

DOCUMENTO = """VERBALE

Il sottoscritto Mario Rossi, nato il 10/12/1985, codice fiscale RSSMRA85TIOA562S,
residente in Via Giuseppe Garibaldi, 42, email mario.rossi@example.it,
tel. +39 348 1234567, partita IVA 00743110157.

IBAN IT60X0542811101000000123456 — veicolo targa AB123CD — IP 192.168.1.44.

Controparte: medesimo soggetto, C.F. RSSMRA85T10A562S.
"""

SALT = b"\x00" * 32


@pytest.fixture
def screener():
    return Screener(ScreenConfig(layers=("regex", "ocr_recovery")))


class TestRilevamento:
    def test_trova_tutte_le_categorie_attese(self, screener):
        found = {s.entity_type for s in screener.scan(DOCUMENTO).spans}
        attese = {
            EntityType.IT_CODICE_FISCALE,
            EntityType.IT_PARTITA_IVA,
            EntityType.IBAN,
            EntityType.IT_TARGA,
            EntityType.EMAIL,
            EntityType.PHONE,
            EntityType.IP_ADDRESS,
            EntityType.ADDRESS,
            EntityType.DATE_OF_BIRTH,
        }
        assert attese <= found, f"mancanti: {attese - found}"

    def test_iban_non_assorbe_la_riga_successiva(self, screener):
        """Regressione: il separatore \\s faceva inghiottire il newline."""
        spans = [s for s in screener.scan(DOCUMENTO).spans if s.entity_type is EntityType.IBAN]
        assert len(spans) == 1
        assert "\n" not in spans[0].text
        assert spans[0].text == "IT60X0542811101000000123456"

    def test_ip_seguito_da_punto_viene_rilevato(self, screener):
        """Regressione: il lookahead (?![0-9.]) falliva sul punto di fine frase."""
        spans = [
            s for s in screener.scan("Server 192.168.1.44.").spans
            if s.entity_type is EntityType.IP_ADDRESS
        ]
        assert len(spans) == 1
        assert spans[0].text == "192.168.1.44"

    def test_partita_iva_non_confusa_con_telefono(self, screener):
        spans = screener.scan("Partita IVA 00743110157").spans
        tipi = {s.entity_type for s in spans}
        assert EntityType.IT_PARTITA_IVA in tipi
        assert EntityType.PHONE not in tipi

    def test_nessuna_sovrapposizione_residua(self, screener):
        spans = screener.scan(DOCUMENTO).spans
        for a, b in zip(spans, spans[1:], strict=False):
            assert a.end <= b.start, f"sovrapposizione fra {a.text!r} e {b.text!r}"

    def test_cf_danneggiato_viene_recuperato(self, screener):
        spans = [
            s for s in screener.scan(DOCUMENTO).spans
            if s.entity_type is EntityType.IT_CODICE_FISCALE
        ]
        assert len(spans) == 2
        assert any(s.ocr_corrected for s in spans)


class TestCanonicalizzazione:
    def test_ordine_del_nome_irrilevante(self):
        assert canonical_form("Mario Rossi", EntityType.PERSON) == canonical_form(
            "ROSSI Mario", EntityType.PERSON
        )

    def test_accenti_normalizzati(self):
        assert canonical_form("Niccolò Rossi", EntityType.PERSON) == canonical_form(
            "NICCOLO ROSSI", EntityType.PERSON
        )

    def test_prefisso_telefonico_irrilevante(self):
        assert canonical_form("+39 348 1234567", EntityType.PHONE) == canonical_form(
            "3481234567", EntityType.PHONE
        )

    def test_email_case_insensitive(self):
        assert canonical_form("Mario.Rossi@Example.IT", EntityType.EMAIL) == canonical_form(
            "mario.rossi@example.it", EntityType.EMAIL
        )


class TestPseudonimizzazione:
    def test_token_deterministico(self):
        a = make_token("RSSMRA85T10A562S", EntityType.IT_CODICE_FISCALE, SALT)
        b = make_token("RSSMRA85T10A562S", EntityType.IT_CODICE_FISCALE, SALT)
        assert a == b

    def test_salt_diverso_token_diverso(self):
        a = make_token("RSSMRA85T10A562S", EntityType.IT_CODICE_FISCALE, SALT)
        b = make_token("RSSMRA85T10A562S", EntityType.IT_CODICE_FISCALE, b"\x01" * 32)
        assert a != b, "salt per documento: i token non devono essere collegabili fra documenti"

    def test_cf_letto_bene_e_letto_male_stesso_token(self, screener):
        """Il punto: la canonicalizzazione avviene sul valore corretto."""
        result = screener.scan(DOCUMENTO)
        out = Pseudonymizer(salt=SALT).apply(DOCUMENTO, result.spans)
        tokens = re.findall(r"⟦CF_[0-9a-f]+⟧", out.text)
        assert len(tokens) == 2
        assert len(set(tokens)) == 1, "lo stesso CF deve ricevere un solo token"

    def test_il_testo_sicuro_non_contiene_gli_originali(self, screener):
        result = screener.scan(DOCUMENTO)
        out = Pseudonymizer(salt=SALT).apply(DOCUMENTO, result.spans)
        for valore in [
            "RSSMRA85T10A562S",
            "RSSMRA85TIOA562S",
            "00743110157",
            "IT60X0542811101000000123456",
            "mario.rossi@example.it",
            "AB123CD",
            "192.168.1.44",
        ]:
            assert valore not in out.text, f"{valore} è rimasto in chiaro"

    def test_azione_keep_lascia_invariato(self, screener):
        result = screener.scan(DOCUMENTO)
        out = Pseudonymizer(salt=SALT).apply(
            DOCUMENTO, result.spans, actions={"EMAIL": "keep"}
        )
        assert "mario.rossi@example.it" in out.text

    def test_modalita_anonima_non_conserva_la_mappa(self, screener):
        result = screener.scan(DOCUMENTO)
        out = Pseudonymizer(salt=SALT, keep_mapping=False).apply(DOCUMENTO, result.spans)
        assert out.mapping == {}
        assert out.tokens_issued > 0


class TestGeneralizzazione:
    def test_data_ridotta_all_anno(self):
        assert generalize("10/12/1985", EntityType.DATE_OF_BIRTH, "year") == "1985"

    def test_indirizzo_perde_il_civico(self):
        out = generalize("Via Giuseppe Garibaldi, 42", EntityType.ADDRESS, "municipality")
        assert "42" not in out
        assert "Garibaldi" in out


class TestGiroDiRitorno:
    def test_round_trip_completo(self, screener):
        result = screener.scan(DOCUMENTO)
        out = Pseudonymizer(salt=SALT).apply(DOCUMENTO, result.spans)
        report = rehydrate(out.text, out.mapping)
        # Ogni valore originale deve tornare al suo posto.
        for originale in out.mapping.values():
            assert originale in report.text
        assert not report.unknown_tokens

    def test_token_inventato_non_viene_sostituito(self):
        mapping = {"⟦PER_aaaa⟧": "Mario Rossi"}
        report = rehydrate("Il soggetto ⟦PER_9999⟧ risulta assente.", mapping)
        assert "⟦PER_9999⟧" in report.text
        assert report.unknown_tokens == ["⟦PER_9999⟧"]
        assert report.warnings

    def test_token_spezzato_dal_modello_viene_recuperato(self):
        mapping = {"⟦PER_aaaa⟧": "Mario Rossi"}
        report = rehydrate("Il soggetto ⟦ PER _ aaaa ⟧ è presente.", mapping)
        assert "Mario Rossi" in report.text

    def test_copertura_bassa_segnalata(self):
        mapping = {f"⟦PER_{i:04x}⟧": f"Tizio {i}" for i in range(10)}
        report = rehydrate("Solo ⟦PER_0000⟧ compare.", mapping)
        assert report.coverage < 0.5
        assert any("parafrasati" in w for w in report.warnings)

    def test_strict_solleva_su_token_sconosciuto(self):
        with pytest.raises(ValueError):
            rehydrate("⟦PER_9999⟧", {"⟦PER_aaaa⟧": "x"}, strict=True)
