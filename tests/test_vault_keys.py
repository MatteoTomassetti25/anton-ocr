"""Test delle sorgenti di chiave del vault.

Regressione importante: il ripiego originale salvava la chiave in una variabile
d'ambiente, che non sopravvive al processo. Il giro di ritorno avviene quasi
sempre in un secondo momento (un altro comando, un'altra sessione), quindi la
mappatura risultava irrecuperabile proprio nel caso d'uso principale.
"""

import pytest

from anton_ocr.privacy.vault import Vault, VaultError

MAPPING = {"⟦CF_1234⟧": "RSSMRA85T10A562S", "⟦PER_abcd⟧": "Mario Rossi"}
SALT = b"\x07" * 32


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)


class TestKeyfile:
    def test_sopravvive_a_una_nuova_istanza(self, tmp_path, no_keyring):
        """Il caso che conta: scrittura e lettura da processi diversi."""
        Vault(tmp_path / "v").store("doc-1", MAPPING, SALT)

        # Nuova istanza: simula un secondo comando della CLI.
        record = Vault(tmp_path / "v").load("doc-1")
        assert record.mapping == MAPPING
        assert record.token_salt == SALT

    def test_permessi_restrittivi(self, tmp_path, no_keyring):
        vault = Vault(tmp_path / "v")
        vault.store("doc-2", MAPPING, SALT)
        keyfile = vault.keyfile_for("doc-2")
        assert keyfile.exists()
        assert oct(keyfile.stat().st_mode)[-3:] == "600"
        assert oct(keyfile.parent.stat().st_mode)[-3:] == "700"

    def test_chiave_separata_dal_vault(self, tmp_path, no_keyring):
        """Il file .vault da solo non basta a decifrare: protegge dalla
        condivisione accidentale del solo vault (backup, sync, allegato)."""
        vault = Vault(tmp_path / "v")
        path = vault.store("doc-3", MAPPING, SALT)
        assert "RSSMRA85T10A562S" not in path.read_text()
        assert vault.keyfile_for("doc-3").parent != path.parent

    def test_forget_distrugge_il_file_di_chiave(self, tmp_path, no_keyring):
        vault = Vault(tmp_path / "v")
        vault.store("doc-4", MAPPING, SALT)
        keyfile = vault.keyfile_for("doc-4")
        assert keyfile.exists()

        report = vault.forget("doc-4")
        assert report["key_destroyed"]
        assert not keyfile.exists()
        with pytest.raises(VaultError):
            vault.load("doc-4")


class TestPassphrase:
    def test_ha_precedenza_sulle_altre_sorgenti(self, tmp_path, no_keyring):
        vault = Vault(tmp_path / "v", passphrase="segreto")
        vault.store("doc-5", MAPPING, SALT)
        # Con passphrase non deve essere scritto alcun file di chiave.
        assert not vault.keyfile_for("doc-5").exists()

    def test_passphrase_errata_rifiutata(self, tmp_path, no_keyring):
        Vault(tmp_path / "v", passphrase="giusta").store("doc-6", MAPPING, SALT)
        with pytest.raises(VaultError):
            Vault(tmp_path / "v", passphrase="sbagliata").load("doc-6")


class TestScadenza:
    def test_purge_rimuove_i_vault_scaduti(self, tmp_path, no_keyring):
        vault = Vault(tmp_path / "v")
        vault.store("vecchio", MAPPING, SALT, ttl_days=0)
        vault.store("recente", MAPPING, SALT, ttl_days=90)

        removed = vault.purge_expired()
        assert "vecchio" in removed
        assert vault.load("recente").mapping == MAPPING
