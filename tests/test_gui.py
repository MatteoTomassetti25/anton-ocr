"""Test della GUI locale.

Due proprietà valgono più di tutte le altre e sono verificate per prime:

1. il server **non** deve essere raggiungibile fuori dal loopback;
2. la pagina **non** deve caricare nulla dalla rete.

Il resto sono le API.
"""

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from anton_ocr.config import Config
from anton_ocr.gui.page import render_page
from anton_ocr.gui.server import Handler, Session

DOCUMENTO = (
    "Il sottoscritto Mario Rossi, codice fiscale RSSMRA85TIOA562S, "
    "email mario.rossi@example.it, IBAN IT60X0542811101000000123456."
)


@pytest.fixture
def server(tmp_path, monkeypatch):
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
    cfg.ensure_dirs()

    Handler.session = Session(config=cfg, csrf_token="token-di-prova")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def post(server, path, payload):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get(server, path):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode()


@pytest.mark.loopback
class TestIsolamento:
    def test_ascolta_solo_su_loopback(self, server):
        assert server.server_address[0] == "127.0.0.1"

    def test_non_raggiungibile_dall_indirizzo_di_rete(self, server):
        """Da un IP non-loopback la connessione non deve stabilirsi."""
        port = server.server_address[1]
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            pytest.skip("nessun indirizzo di rete disponibile")
        if local_ip.startswith("127."):
            pytest.skip("l'host risolve su loopback")

        with socket.socket() as s:
            s.settimeout(2)
            with pytest.raises(OSError):
                s.connect((local_ip, port))

    def test_la_pagina_non_carica_risorse_esterne(self):
        html = render_page("t", "4.0.0")
        for schema in ("http://", "https://", "//cdn", "cdnjs", "googleapis"):
            assert schema not in html, f"riferimento esterno trovato: {schema}"

    def test_csp_restrittiva(self, server):
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        with urllib.request.urlopen(url, timeout=10) as r:
            csp = r.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "form-action 'none'" in csp


@pytest.mark.loopback
class TestCsrf:
    def test_richiesta_senza_token_rifiutata(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            post(server, "/api/process", {"text": DOCUMENTO})
        assert exc.value.code == 403

    def test_token_errato_rifiutato(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            post(server, "/api/scan", {"text": "x", "csrf": "sbagliato"})
        assert exc.value.code == 403


@pytest.mark.loopback
class TestApi:
    def test_status(self, server):
        data = json.loads(get(server, "/api/status"))
        assert data["policy"]["profile"]
        assert "ner_enabled" in data

    def test_scan_non_scrive_nulla(self, server):
        cfg = Handler.session.config
        prima = list(cfg.output_dir.iterdir()) + list(cfg.vault_dir.iterdir())
        data = post(server, "/api/scan", {"text": DOCUMENTO, "csrf": "token-di-prova"})
        dopo = list(cfg.output_dir.iterdir()) + list(cfg.vault_dir.iterdir())

        assert data["spans"]
        assert prima == dopo, "l'anteprima non deve produrre file"

    def test_scan_riporta_il_recupero_ocr(self, server):
        data = post(server, "/api/scan", {"text": DOCUMENTO, "csrf": "token-di-prova"})
        recuperati = [s for s in data["spans"] if s["recovered"]]
        assert recuperati
        assert recuperati[0]["corrected"] == "RSSMRA85T10A562S"

    def test_process_produce_il_documento_sicuro(self, server):
        data = post(
            server, "/api/process",
            {"text": DOCUMENTO, "filename": "doc.txt", "csrf": "token-di-prova"},
        )
        assert data["decision"] in ("allow", "review")
        assert data["safe_markdown"].startswith("---\n")
        for valore in ["RSSMRA85TIOA562S", "mario.rossi@example.it",
                       "IT60X0542811101000000123456"]:
            assert valore not in data["safe_markdown"]

    def test_process_non_scrive_il_documento_su_disco(self, server):
        """Dalla GUI il documento si guarda e si scarica dal browser.

        Scriverlo comunque lascerebbe copie che l'utente non ha chiesto, in uno
        strumento il cui scopo è ridurre le copie dei dati.
        """
        cfg = Handler.session.config
        post(
            server, "/api/process",
            {"text": DOCUMENTO, "filename": "doc.txt", "csrf": "token-di-prova"},
        )
        assert list(cfg.output_dir.iterdir()) == []
        assert list(cfg.review_dir.iterdir()) == []
        assert list(cfg.quarantine_dir.iterdir()) == []

    def test_audit_e_vault_vengono_comunque_registrati(self, server):
        """Il registro degli eventi e il vault servono a funzionare e a tracciare."""
        cfg = Handler.session.config
        data = post(
            server, "/api/process",
            {"text": DOCUMENTO, "filename": "doc.txt", "csrf": "token-di-prova"},
        )
        assert cfg.audit_path.exists()
        assert (cfg.vault_dir / f"{data['uuid']}.vault").exists()

    def test_manifest_scaricabile_dalla_risposta(self, server):
        data = post(
            server, "/api/process",
            {"text": DOCUMENTO, "filename": "doc.txt", "csrf": "token-di-prova"},
        )
        manifest = json.loads(data["manifest_json"])
        assert manifest["schema"] == "anton-ocr/manifest/v1"
        assert manifest["signature"]["alg"] == "ed25519"
        assert data["persisted"] is False

    def test_round_trip_dalla_gui(self, server):
        proc = post(
            server, "/api/process",
            {"text": DOCUMENTO, "filename": "doc.txt", "csrf": "token-di-prova"},
        )
        import re

        token = re.search(r"⟦CF_[0-9a-f]+⟧", proc["safe_markdown"]).group(0)
        reidr = post(
            server, "/api/rehydrate",
            {"uuid": proc["uuid"], "response": f"Il soggetto {token} è idoneo.",
             "csrf": "token-di-prova"},
        )
        assert "RSSMRA85TIOA562S" in reidr["text"]
        assert reidr["substituted"] == 1

    def test_forget_dalla_gui(self, server):
        proc = post(
            server, "/api/process",
            {"text": DOCUMENTO, "filename": "doc.txt", "csrf": "token-di-prova"},
        )
        post(server, "/api/forget", {"uuid": proc["uuid"], "csrf": "token-di-prova"})
        reidr = post(
            server, "/api/rehydrate",
            {"uuid": proc["uuid"], "response": "x", "csrf": "token-di-prova"},
        )
        assert "error" in reidr

    def test_estensione_non_supportata_rifiutata(self, server):
        data = post(
            server, "/api/process",
            {"text": "x", "filename": "malware.exe", "csrf": "token-di-prova"},
        )
        assert "error" in data

    def test_percorso_sconosciuto(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(server, "/api/inesistente")
        assert exc.value.code == 404


class TestPagina:
    def test_placeholder_sostituiti(self):
        html = render_page("segreto-csrf", "4.0.0")
        assert "segreto-csrf" in html
        assert "$csrf" not in html
        assert "$version" not in html

    def test_nessuna_sentinella_residua(self):
        html = render_page("t", "4.0.0")
        assert "__ANTON_" not in html

    def test_il_javascript_col_simbolo_dollaro_sopravvive(self):
        """Regressione: string.Template interpretava ogni `$` come segnaposto.

        La pagina contiene template literal e regex con `$`: la sostituzione deve
        avvenire per sentinelle esplicite, non tramite Template.
        """
        html = render_page("t", "4.0.0")
        assert "'<span class=\"tok\">$1</span>'" in html
        assert "/\\.[^.]+$/" in html

    def test_token_csrf_sanificato(self):
        html = render_page('"><script>alert(1)</script>', "4.0.0")
        assert "<script>alert(1)</script>" not in html
