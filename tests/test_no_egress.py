"""Verifica che il percorso di elaborazione non apra connessioni di rete.

Questo è il test che sostiene l'affermazione "100% locale". Senza di esso quella
frase è una promessa; con esso è una proprietà verificata a ogni commit.

Il meccanismo: si sostituisce ``socket.socket`` con un tipo che solleva
un'eccezione alla connessione, poi si esegue la pipeline completa. Se un qualsiasi
componente prova a raggiungere la rete, il test fallisce e indica il colpevole.

In CI il test gira anche dentro un network namespace privo di rotta
(``unshare -rn``), che è una garanzia più forte perché non dipende dal
monkeypatching.
"""

import socket

import pytest

from anton_ocr.config import Config
from anton_ocr.pipeline import Pipeline

DOCUMENTO = """CONTRATTO

Cliente: Mario Rossi, codice fiscale RSSMRA85T10A562S,
email mario.rossi@example.it, IBAN IT60X0542811101000000123456.
Recapito: +39 348 1234567. Partita IVA 00743110157.
"""


class NetworkAccessAttempted(AssertionError):
    """Sollevata quando un componente prova ad aprire una connessione."""


@pytest.fixture
def no_network(monkeypatch):
    """Rende ogni tentativo di connessione un errore immediato e tracciabile."""

    def _blocked(*args, **kwargs):
        raise NetworkAccessAttempted(
            "Un componente della pipeline ha tentato di accedere alla rete. "
            "Il percorso di elaborazione deve restare interamente locale."
        )

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)
    cfg = Config()
    for attr, name in [
        ("output_dir", "output"),
        ("review_dir", "review"),
        ("quarantine_dir", "quarantine"),
        ("vault_dir", "vault"),
        ("archive_dir", "archive"),
        ("input_dir", "input"),
    ]:
        setattr(cfg, attr, tmp_path / name)
    cfg.audit_path = tmp_path / "audit.jsonl"
    cfg.signing_key_path = tmp_path / "key.pem"
    return cfg


def test_pipeline_completa_senza_rete(no_network, config):
    """L'elaborazione end-to-end non deve toccare la rete."""
    pipeline = Pipeline(config)
    result = pipeline.process_text(DOCUMENTO, source_name="contratto.txt")

    assert result.safe_text
    assert "RSSMRA85T10A562S" not in result.safe_text
    assert result.manifest_path.exists()
    assert result.manifest.pipeline["egress"] == "none"


def test_rilevamento_senza_rete(no_network):
    from anton_ocr.privacy.screen import ScreenConfig, Screener

    result = Screener(ScreenConfig(layers=("regex", "ocr_recovery"))).scan(DOCUMENTO)
    assert result.spans


def test_firma_e_vault_senza_rete(no_network, tmp_path, monkeypatch):
    monkeypatch.setattr("anton_ocr.privacy.vault._keyring_available", lambda: False)
    from anton_ocr.compliance.manifest import SigningKey
    from anton_ocr.privacy.vault import Vault

    key = SigningKey.load_or_create(tmp_path / "k.pem")
    assert key.key_id

    vault = Vault(tmp_path / "vault")
    vault.store("doc", {"⟦CF_1111⟧": "RSSMRA85T10A562S"}, b"\x00" * 32)
    assert vault.load("doc").mapping


def test_il_blocco_di_rete_funziona_davvero(no_network):
    """Controllo del controllo: se questo non fallisce, i test sopra non provano nulla."""
    with pytest.raises(NetworkAccessAttempted):
        socket.socket()
