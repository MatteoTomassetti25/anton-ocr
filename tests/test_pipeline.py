"""Test end-to-end della pipeline e del motore di policy."""

import json

import pytest
import yaml

from anton_ocr.compliance.manifest import verify_manifest_file
from anton_ocr.config import Config
from anton_ocr.ocr.confidence import assess_text_quality
from anton_ocr.pipeline import Pipeline
from anton_ocr.policy.engine import Decision, load_policy
from anton_ocr.privacy.rehydrate import rehydrate
from anton_ocr.privacy.vault import Vault

DOCUMENTO = """RICHIESTA DI RIMBORSO

Il sottoscritto Mario Rossi, codice fiscale RSSMRA85TIOA562S,
residente in Via Giuseppe Garibaldi, 42, email mario.rossi@example.it,
chiede il rimborso sull'IBAN IT60X0542811101000000123456.
Partita IVA 00743110157. Targa AB123CD.
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)
    cfg = Config()
    cfg.output_dir = tmp_path / "output"
    cfg.review_dir = tmp_path / "review"
    cfg.quarantine_dir = tmp_path / "quarantine"
    cfg.vault_dir = tmp_path / "vault"
    cfg.archive_dir = tmp_path / "archive"
    cfg.input_dir = tmp_path / "input"
    cfg.audit_path = tmp_path / "audit.jsonl"
    cfg.signing_key_path = tmp_path / "key.pem"
    return cfg


@pytest.fixture
def pipeline(config):
    return Pipeline(config)


class TestPipeline:
    def test_documento_pulito_viene_ammesso(self, pipeline):
        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")
        assert result.decision in (Decision.ALLOW, Decision.REVIEW)
        assert result.output_path.exists()
        assert result.manifest_path.exists()

    def test_il_testo_sicuro_e_privo_di_identificatori(self, pipeline):
        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")
        for valore in [
            "RSSMRA85TIOA562S",
            "00743110157",
            "IT60X0542811101000000123456",
            "mario.rossi@example.it",
            "AB123CD",
        ]:
            assert valore not in result.safe_text
        assert valore not in result.output_path.read_text()

    def test_manifest_firmato_e_verificabile(self, pipeline):
        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")
        assert verify_manifest_file(result.manifest_path).valid

    def test_manifest_registra_il_recupero_ocr(self, pipeline):
        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")
        data = json.loads(result.manifest_path.read_text())
        assert data["redaction"]["ocr_recovered"] >= 1
        assert data["redaction"]["by_layer"].get("ocr_recovery", 0) >= 1

    def test_manifest_dichiara_i_limiti(self, pipeline):
        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")
        data = json.loads(result.manifest_path.read_text())
        assert data["limitations"]
        assert data["pipeline"]["egress"] == "none"
        assert data["ai_act"]["art50_marked"] is True

    def test_audit_log_integro_dopo_elaborazione(self, pipeline):
        pipeline.process_text(DOCUMENTO, source_name="a.txt")
        pipeline.process_text(DOCUMENTO, source_name="b.txt")
        result = pipeline.audit.verify()
        assert result.valid
        assert result.entries == 6  # 3 eventi per documento

    def test_vault_permette_il_giro_di_ritorno(self, pipeline, config):
        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")

        record = Vault(config.vault_dir).load(result.doc_uuid)
        token = next(iter(record.mapping))
        risposta = f"Il richiedente {token} ha diritto al rimborso."

        report = rehydrate(risposta, record.mapping)
        assert record.mapping[token] in report.text
        assert not report.unknown_tokens

    def test_forget_interrompe_il_giro_di_ritorno(self, pipeline, config):
        from anton_ocr.privacy.vault import VaultError

        result = pipeline.process_text(DOCUMENTO, source_name="richiesta.txt")
        Vault(config.vault_dir).forget(result.doc_uuid)
        with pytest.raises(VaultError):
            Vault(config.vault_dir).load(result.doc_uuid)


class TestPolicy:
    def test_categoria_art9_blocca_di_default(self, config, tmp_path):
        policy_file = tmp_path / "p.yaml"
        policy_file.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "profile": "test",
                    "entities": [
                        {"type": "GDPR_ART9_HEALTH", "action": "block", "reason": "art. 9"}
                    ],
                }
            )
        )
        policy = load_policy(policy_file)
        from anton_ocr.policy.engine import PolicyEngine
        from anton_ocr.privacy.entities import DetectionLayer, EntityType, Span

        span = Span(
            start=0, end=5, text="asma",
            entity_type=EntityType.GDPR_ART9_HEALTH,
            layer=DetectionLayer.NER, confidence=0.9,
        )
        outcome = PolicyEngine(policy).evaluate([span])
        assert outcome.decision is Decision.BLOCK

    def test_documento_bloccato_non_scrive_il_contenuto(self, config):

        pipeline = Pipeline(config)

        def sempre_bloccante(spans, **kwargs):
            from anton_ocr.policy.engine import PolicyOutcome

            return PolicyOutcome(decision=Decision.BLOCK, reasons=["test"])

        pipeline.engine.evaluate = sempre_bloccante
        result = pipeline.process_text(DOCUMENTO, source_name="bloccato.txt")

        assert result.decision is Decision.BLOCK
        contenuto = result.output_path.read_text()
        assert "BLOCCATO" in contenuto
        assert "mario.rossi@example.it" not in contenuto
        assert result.output_path.parent == config.quarantine_dir

    def test_bassa_confidenza_manda_in_revisione(self, config):
        from anton_ocr.policy.engine import PolicyEngine
        from anton_ocr.privacy.entities import DetectionLayer, EntityType, Span

        span = Span(
            start=0, end=5, text="x",
            entity_type=EntityType.PERSON,
            layer=DetectionLayer.NER, confidence=0.4,
        )
        outcome = PolicyEngine().evaluate([span])
        assert outcome.decision is Decision.REVIEW

    def test_fail_closed_su_errore_di_rilevamento(self):
        from anton_ocr.policy.engine import PolicyEngine

        outcome = PolicyEngine().evaluate([], detection_error=True)
        assert outcome.decision is Decision.BLOCK

    def test_policy_sha256_cambia_col_contenuto(self, tmp_path):
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text(yaml.safe_dump({"version": 1, "profile": "x", "entities": []}))
        b.write_text(yaml.safe_dump({"version": 1, "profile": "y", "entities": []}))
        assert load_policy(a).sha256 != load_policy(b).sha256

    def test_tipo_entita_sconosciuto_rifiutato(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.safe_dump({"entities": [{"type": "NON_ESISTE", "action": "block"}]}))
        with pytest.raises(ValueError, match="sconosciuto"):
            load_policy(p)


class TestConfidence:
    def test_testo_pulito_alta_confidenza(self):
        testo = (
            "Il sottoscritto dichiara di essere residente nel comune indicato "
            "e di accettare le condizioni riportate nel presente documento."
        )
        assert assess_text_quality(testo).score > 0.8

    def test_testo_vuoto(self):
        q = assess_text_quality("")
        assert q.score == 0.0
        assert not q.is_reliable

    def test_testo_frammentato_bassa_confidenza(self):
        assert assess_text_quality("l a p a r o l a s p e z z a t a c o s i").score < 0.75

    def test_rumore_bassa_confidenza(self):
        assert assess_text_quality("### ~~~ ��� §§§ ¬¬¬ ^^^ ``` □□□ ■■■").score < 0.75

    def test_note_esplicative_presenti(self):
        q = assess_text_quality("aaaaaaaaaa bbbbbbbbbb cccccccccc")
        assert q.notes
