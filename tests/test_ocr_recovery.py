"""Test dello strato di recupero degli errori OCR.

Questi test proteggono le due proprietà che rendono lo strato utile:
il **recall** (recuperare gli identificatori danneggiati) e la **precisione**
(non inventarne dove non ce ne sono). La seconda è la più facile da perdere.
"""

import pytest

from anton_ocr.privacy.entities import EntityType
from anton_ocr.privacy.ocr_recovery import has_context_support, recover, recover_spans

CF_VALIDO = "RSSMRA85T10A562S"


class TestRecuperoFase1:
    """Fase 1: deduzione dalla forma. Sempre attiva, nessuna ricerca."""

    @pytest.mark.parametrize(
        "danneggiato,descrizione",
        [
            ("RSSMRA85T1OA562S", "O al posto di 0"),
            ("RSSMRA85TIOA562S", "I→1 e O→0"),
            ("RSSMRA8ST10A562S", "S al posto di 5"),
            ("RSSMRA85TlOA562S", "minuscole l e O"),
            ("RS5MRA85T10A562S", "5 al posto di S"),
            ("RSSMRAB5T10A562S", "B al posto di 8"),
            ("RSSMRA85T1OA562S", "O al posto di 0 nel giorno"),
        ],
    )
    def test_recupera_il_codice_corretto(self, danneggiato, descrizione):
        result = recover(danneggiato, EntityType.IT_CODICE_FISCALE)
        assert result is not None, f"non recuperato: {descrizione}"
        assert result.corrected == CF_VALIDO
        assert result.confidence > 0.6

    def test_codice_gia_valido_non_viene_toccato(self):
        assert recover(CF_VALIDO, EntityType.IT_CODICE_FISCALE) is None

    def test_confidenza_cala_con_il_numero_di_correzioni(self):
        una = recover("RSSMRA85T1OA562S", EntityType.IT_CODICE_FISCALE)
        due = recover("RSSMRA85TIOA562S", EntityType.IT_CODICE_FISCALE)
        assert una.confidence > due.confidence


class TestPrecisione:
    """Il rumore non deve produrre identificatori."""

    @pytest.mark.parametrize(
        "rumore",
        [
            "ABCDEF12G34H567I",
            "XYZWQK99A88B123C",
            "1234567890ABCDEF",
            "AAAAAAAAAAAAAAAA",
        ],
    )
    def test_nessun_recupero_da_rumore(self, rumore):
        assert recover(rumore, EntityType.IT_CODICE_FISCALE) is None

    def test_fase2_disattivata_di_default(self):
        """La ricerca intra-classe non parte senza sostegno dal contesto."""
        assert recover("RSSMRA85T10A562Z", EntityType.IT_CODICE_FISCALE) is None

    def test_fase2_con_contesto_trova_ma_segnala_ambiguita(self):
        result = recover(
            "RSSMRA85T10A562Z", EntityType.IT_CODICE_FISCALE, allow_intra_class=True
        )
        assert result is not None
        assert result.ambiguous
        assert result.confidence < 0.6, "un recupero ambiguo deve finire in revisione"

    def test_troppe_posizioni_ambigue_interrompe(self):
        result = recover(
            "OOOOOOOOOOOOOOOO",
            EntityType.IT_CODICE_FISCALE,
            max_ambiguous_positions=2,
        )
        assert result is None


class TestContesto:
    def test_etichetta_vicina_abilita_la_fase2(self):
        testo = "Codice fiscale: RSSMRA85T10A562Z rilasciato in data..."
        start = testo.index("RSSMRA")
        assert has_context_support(testo, start, start + 16, EntityType.IT_CODICE_FISCALE)

    def test_senza_etichetta_non_abilita(self):
        testo = "Riferimento pratica RSSMRA85T10A562Z del protocollo."
        start = testo.index("RSSMRA")
        assert not has_context_support(testo, start, start + 16, EntityType.IT_CODICE_FISCALE)


class TestPartitaIva:
    def test_recupera_lettera_al_posto_di_cifra(self):
        # 00743110157 valida; sostituiamo lo 0 iniziale con O
        result = recover("O0743110157", EntityType.IT_PARTITA_IVA)
        assert result is not None
        assert result.corrected == "00743110157"


class TestRecoverSpans:
    def test_produce_span_con_valore_corretto(self):
        testo = "Il codice fiscale è RSSMRA85TIOA562S."
        start = testo.index("RSSMRA")
        unvalidated = [(EntityType.IT_CODICE_FISCALE, start, start + 16, "RSSMRA85TIOA562S")]
        spans = recover_spans(testo, unvalidated)
        assert len(spans) == 1
        assert spans[0].ocr_corrected
        assert spans[0].corrected_text == CF_VALIDO
        assert spans[0].canonical_value == CF_VALIDO
