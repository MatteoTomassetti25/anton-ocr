"""Test di manifest firmato, audit log a catena di hash e vault cifrato."""

import json

import pytest

from anton_ocr.compliance.audit import AuditLog
from anton_ocr.compliance.manifest import (
    DocumentInfo,
    Manifest,
    ModelRef,
    SigningKey,
    verify_manifest_file,
)
from anton_ocr.privacy.vault import Vault, VaultError


@pytest.fixture
def key():
    return SigningKey.generate()


@pytest.fixture
def manifest():
    return Manifest(
        document=DocumentInfo(
            uuid="test-uuid",
            source_name="doc.pdf",
            source_sha256="a" * 64,
            pages=3,
        ),
        models=[ModelRef(role="ocr", id="glm-ocr", license="MIT", params="0.9B")],
    )


class TestManifest:
    def test_firma_e_verifica(self, tmp_path, manifest, key):
        path = manifest.sign(key).write(tmp_path / "doc.manifest.json")
        result = verify_manifest_file(path)
        assert result.valid
        assert result.key_id == key.key_id

    def test_manomissione_rilevata(self, tmp_path, manifest, key):
        path = manifest.sign(key).write(tmp_path / "doc.manifest.json")

        data = json.loads(path.read_text())
        data["decision"] = "allow" if data["decision"] != "allow" else "block"
        path.write_text(json.dumps(data))

        result = verify_manifest_file(path)
        assert not result.valid
        assert "alterato" in result.reason

    def test_manifest_senza_firma(self, tmp_path, manifest):
        path = tmp_path / "unsigned.json"
        path.write_text(json.dumps(manifest.to_dict()))
        assert not verify_manifest_file(path).valid

    def test_contiene_sempre_i_limiti_dichiarati(self, manifest, key):
        from anton_ocr.pipeline import STANDARD_LIMITATIONS

        manifest.limitations = STANDARD_LIMITATIONS
        data = manifest.sign(key).to_dict()
        assert data["limitations"]
        assert "non costituisce" in data["disclaimer"].lower()

    def test_chiave_persistente(self, tmp_path):
        path = tmp_path / "key.pem"
        a = SigningKey.load_or_create(path)
        b = SigningKey.load_or_create(path)
        assert a.key_id == b.key_id


class TestAuditLog:
    def test_catena_valida(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        for i in range(5):
            log.append("ingest", {"doc_uuid": f"doc-{i}"})
        result = log.verify()
        assert result.valid
        assert result.entries == 5

    def test_alterazione_del_contenuto_rilevata(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        for i in range(3):
            log.append("ingest", {"doc_uuid": f"doc-{i}"})

        lines = path.read_text().strip().split("\n")
        entry = json.loads(lines[1])
        entry["payload"]["doc_uuid"] = "manomesso"
        lines[1] = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")

        result = log.verify()
        assert not result.valid
        assert result.broken_at == 2

    def test_rimozione_di_una_riga_rilevata(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(path)
        for i in range(4):
            log.append("ingest", {"doc_uuid": f"doc-{i}"})

        lines = path.read_text().strip().split("\n")
        del lines[1]
        path.write_text("\n".join(lines) + "\n")

        result = log.verify()
        assert not result.valid
        assert "non contigua" in result.reason

    def test_filtro_per_documento(self, tmp_path):
        log = AuditLog(tmp_path / "audit.jsonl")
        log.append("ingest", {"doc_uuid": "a"})
        log.append("screen", {"doc_uuid": "b"})
        log.append("decision", {"doc_uuid": "a"})
        assert len(log.for_document("a")) == 2


class TestVault:
    @pytest.fixture
    def vault(self, tmp_path, monkeypatch):
        # Nell'ambiente di test il portachiavi di sistema non è disponibile:
        # si forza la sorgente 'env', che non scrive la chiave su disco.
        monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)
        return Vault(tmp_path / "vault")

    def test_scrittura_e_lettura(self, vault):
        mapping = {"⟦CF_1234⟧": "RSSMRA85T10A562S"}
        salt = b"\x02" * 32
        vault.store("doc-1", mapping, salt)

        record = vault.load("doc-1")
        assert record.mapping == mapping
        assert record.token_salt == salt

    def test_il_file_non_contiene_il_valore_in_chiaro(self, vault):
        path = vault.store("doc-2", {"⟦CF_1234⟧": "RSSMRA85T10A562S"}, b"\x00" * 32)
        assert "RSSMRA85T10A562S" not in path.read_text()

    def test_forget_rende_irreversibile(self, vault):
        vault.store("doc-3", {"⟦CF_1234⟧": "RSSMRA85T10A562S"}, b"\x00" * 32)
        report = vault.forget("doc-3")
        assert report["file_removed"]
        with pytest.raises(VaultError):
            vault.load("doc-3")

    def test_passphrase(self, tmp_path, monkeypatch):
        monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)
        vault = Vault(tmp_path / "v", key_source="passphrase", passphrase="segreto")
        vault.store("doc-4", {"⟦PER_aaaa⟧": "Mario Rossi"}, b"\x00" * 32)

        assert Vault(
            tmp_path / "v", key_source="passphrase", passphrase="segreto"
        ).load("doc-4").mapping

        sbagliata = Vault(tmp_path / "v", key_source="passphrase", passphrase="errata")
        with pytest.raises(VaultError):
            sbagliata.load("doc-4")
