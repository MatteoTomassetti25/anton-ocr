"""Test del documento sicuro.

La proprietà da difendere è una sola e vale più di tutte le altre: **nel file
consegnato all'agente non deve comparire alcun valore originale**, né nel testo,
né nel frontmatter, né nella legenda.
"""

import pytest
import yaml

from anton_ocr.config import Config
from anton_ocr.pipeline import Pipeline
from anton_ocr.policy.engine import Decision

VALORI_ORIGINALI = [
    "RSSMRA85TIOA562S",
    "RSSMRA85T10A562S",
    "00743110157",
    "IT60X0542811101000000123456",
    "mario.rossi@example.it",
    "AB123CD",
    "192.168.1.44",
    "3481234567",
]

DOCUMENTO = """RICHIESTA DI RIMBORSO SPESE

Il sottoscritto Mario Rossi, nato il 10/12/1985, codice fiscale RSSMRA85TIOA562S,
residente in Via Giuseppe Garibaldi, 42, email mario.rossi@example.it,
tel. +39 348 1234567, partita IVA 00743110157.
Rimborso su IBAN IT60X0542811101000000123456. Targa AB123CD. IP 192.168.1.44.
Controparte: medesimo soggetto, C.F. RSSMRA85T10A562S.
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)
    cfg = Config()
    for attr, name in [
        ("output_dir", "output"), ("review_dir", "review"),
        ("quarantine_dir", "quarantine"), ("vault_dir", "vault"),
        ("archive_dir", "archive"), ("input_dir", "input"),
    ]:
        setattr(cfg, attr, tmp_path / name)
    cfg.audit_path = tmp_path / "audit.jsonl"
    cfg.signing_key_path = tmp_path / "key.pem"
    return cfg


@pytest.fixture
def result(config):
    return Pipeline(config).process_text(DOCUMENTO, source_name="richiesta.pdf")


class TestNessunaFuga:
    def test_nessun_valore_originale_nel_documento_sicuro(self, result):
        for valore in VALORI_ORIGINALI:
            assert valore not in result.safe_markdown, f"{valore} è finito nel file consegnato"

    def test_nessun_valore_originale_nel_file_su_disco(self, result):
        contenuto = result.output_path.read_text(encoding="utf-8")
        for valore in VALORI_ORIGINALI:
            assert valore not in contenuto

    def test_la_legenda_riporta_conteggi_non_valori(self, result):
        # NB: non si può spezzare su "---": il separatore di tabella Markdown
        # (|---|) lo contiene. Si delimita sulla sezione successiva.
        legenda = result.safe_markdown.split("## Dati rimossi")[1].split("\n## ")[0]
        for valore in VALORI_ORIGINALI:
            assert valore not in legenda
        assert "Codici fiscali" in legenda
        assert "Coordinate bancarie (IBAN)" in legenda


class TestStruttura:
    def test_estensione_safe_md(self, result):
        assert result.output_path.name.endswith(".safe.md")

    def test_frontmatter_yaml_valido(self, result):
        assert result.safe_markdown.startswith("---\n")
        blocco = result.safe_markdown.split("---\n")[1]
        data = yaml.safe_load(blocco)["anton_ocr"]
        assert data["doc_uuid"] == result.doc_uuid
        assert data["egress"] == "none"
        assert data["content_origin"] == "machine-generated"
        assert data["decision"] in ("allow", "review", "block")
        assert len(data["source_sha256"]) == 64

    def test_contiene_istruzione_per_agente(self, result):
        assert "Istruzione per l'agente" in result.safe_markdown
        assert "carattere per carattere" in result.safe_markdown

    def test_contiene_comando_di_reidratazione(self, result):
        assert f"anton-ocr rehydrate {result.doc_uuid}" in result.safe_markdown

    def test_dichiara_i_limiti(self, result):
        assert "recall del 100%" in result.safe_markdown
        assert "art. 4(5) GDPR" in result.safe_markdown
        assert "non costituisce una valutazione di conformità" in result.safe_markdown

    def test_i_token_sono_presenti_nel_contenuto(self, result):
        contenuto = result.safe_markdown.split("## Contenuto")[1]
        assert "⟦CF_" in contenuto
        assert "⟦IBAN_" in contenuto


class TestAvvertenze:
    def test_avverte_se_il_ner_e_spento(self, result):
        """Senza NER i nomi restano in chiaro: il file DEVE dirlo.

        Un documento che sembra sicuro è peggio di uno palesemente non trattato,
        perché induce a fidarsi.
        """
        assert "Riconoscimento dei nomi non attivo" in result.safe_markdown
        assert "Mario Rossi" in result.safe_markdown  # conferma il perché dell'avviso

    def test_avverte_su_qualita_bassa(self, config):
        testo = "l a p a r o l a s p e z z a t a RSSMRA85T10A562S c o s i m a l e"
        r = Pipeline(config).process_text(testo, source_name="degradato.pdf")
        assert "Qualità del testo bassa" in r.safe_markdown


class TestDocumentoBloccato:
    def test_il_contenuto_non_viene_scritto(self, config):
        from anton_ocr.policy.engine import PolicyOutcome

        pipeline = Pipeline(config)
        pipeline.engine.evaluate = lambda spans, **kw: PolicyOutcome(
            decision=Decision.BLOCK, reasons=["Categoria particolare art. 9 GDPR"]
        )
        r = pipeline.process_text(DOCUMENTO, source_name="referto.pdf")

        assert r.decision is Decision.BLOCK
        assert "BLOCCATO" in r.safe_markdown
        assert "RICHIESTA DI RIMBORSO" not in r.safe_markdown
        assert "Mario Rossi" not in r.safe_markdown
        for valore in VALORI_ORIGINALI:
            assert valore not in r.safe_markdown
        assert r.output_path.parent == config.quarantine_dir

    def test_riporta_il_motivo(self, config):
        from anton_ocr.policy.engine import PolicyOutcome

        pipeline = Pipeline(config)
        pipeline.engine.evaluate = lambda spans, **kw: PolicyOutcome(
            decision=Decision.BLOCK, reasons=["Motivo di prova"]
        )
        r = pipeline.process_text(DOCUMENTO, source_name="x.pdf")
        assert "Motivo di prova" in r.safe_markdown
        assert r.doc_uuid in r.safe_markdown
