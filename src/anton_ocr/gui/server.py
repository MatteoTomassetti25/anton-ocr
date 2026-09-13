"""Server della GUI locale.

Scelte progettuali, tutte conseguenza del vincolo local-first:

* **solo libreria standard** — nessun framework, nessuna CDN, nessun asset
  remoto. Coerente con il resto del progetto e con il test zero-egress;
* **si lega esclusivamente a 127.0.0.1** — e il binding è verificato in modo
  esplicito. Un'interfaccia che espone documenti pseudonimizzati e il loro
  contenuto non deve essere raggiungibile dalla rete, nemmeno per errore, nemmeno
  via Tailscale;
* **nessuna persistenza lato server oltre a quella della pipeline** — la GUI è
  una facciata, non un secondo sistema con una sua copia dei dati;
* **token CSRF per le richieste di stato** — anche in locale, una pagina web
  ostile aperta nello stesso browser potrebbe inviare richieste a localhost.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import tempfile
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from anton_ocr import __version__
from anton_ocr.compliance.audit import AuditLog
from anton_ocr.config import Config
from anton_ocr.gui.page import render_page
from anton_ocr.pipeline import Pipeline
from anton_ocr.policy.engine import load_policy
from anton_ocr.privacy.rehydrate import rehydrate
from anton_ocr.privacy.screen import ScreenConfig, Screener
from anton_ocr.privacy.vault import Vault, VaultError

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # 64 MB
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}


@dataclass
class Session:
    """Stato condiviso fra le richieste. Vive in memoria, non su disco."""

    config: Config
    csrf_token: str


class Handler(BaseHTTPRequestHandler):
    session: Session = None  # iniettato da serve()

    # ── utilità ──

    def log_message(self, fmt, *args):  # pragma: no cover - silenzia access log
        log.debug(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # La pagina non carica nulla dall'esterno: la CSP lo rende esplicito e
        # blocca eventuali iniezioni di contenuto remoto.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_UPLOAD_BYTES:
            raise ValueError(f"Richiesta troppo grande ({length} byte)")
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def _check_csrf(self, payload: dict) -> bool:
        return secrets.compare_digest(
            str(payload.get("csrf", "")), self.session.csrf_token
        )

    def _pipeline(self) -> Pipeline:
        config = self.session.config
        return Pipeline(config, load_policy(config.policy_file))

    # ── routing ──

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = render_page(self.session.csrf_token, __version__)
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/api/status":
            self._json(200, self._status())
        else:
            self._json(404, {"error": "non trovato"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._json(413, {"error": str(exc)})
            return

        if not self._check_csrf(payload):
            self._json(403, {"error": "token CSRF mancante o non valido"})
            return

        try:
            if path == "/api/scan":
                self._json(200, self._scan(payload))
            elif path == "/api/process":
                self._json(200, self._process(payload))
            elif path == "/api/process-pdf":
                self._json(200, self._process_pdf(payload))
            elif path == "/api/rehydrate":
                self._json(200, self._rehydrate(payload))
            elif path == "/api/forget":
                self._json(200, self._forget(payload))
            else:
                self._json(404, {"error": "non trovato"})
        except Exception as exc:  # pragma: no cover - percorso difensivo
            log.exception("Errore su %s", path)
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    # ── azioni ──

    def _status(self) -> dict:
        config = self.session.config
        policy = load_policy(config.policy_file)
        screener = Screener()

        from anton_ocr.ocr.extract import available_backends

        audit = AuditLog(config.audit_path)
        chain = audit.verify() if config.audit_path.exists() else None

        return {
            "version": __version__,
            "policy": {
                "profile": policy.profile,
                "mode": policy.mode,
                "sha256": policy.sha256[:16],
                "source": str(policy.source_path) if policy.source_path else "predefinita",
            },
            "ner_enabled": screener.config.ner.backend != "none",
            "ner_backend": screener.config.ner.backend,
            "backends": available_backends(),
            "paths": {
                "output": str(config.output_dir),
                "review": str(config.review_dir),
                "quarantine": str(config.quarantine_dir),
                "vault": str(config.vault_dir),
            },
            "audit": chain.as_dict() if chain else None,
        }

    def _scan(self, payload: dict) -> dict:
        """Anteprima del rilevamento. Non scrive nulla, non crea vault."""
        text = payload.get("text", "")
        if not text.strip():
            return {"spans": [], "stats": {}}

        result = Screener(ScreenConfig(layers=("regex", "ocr_recovery"))).scan(text)
        return {
            "spans": [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "type": s.entity_type.value,
                    "layer": s.layer.value,
                    "confidence": round(s.confidence, 2),
                    "recovered": s.ocr_corrected,
                    "corrected": s.corrected_text,
                }
                for s in result.spans
            ],
            "stats": result.stats,
        }

    def _process(self, payload: dict) -> dict:
        pipeline = self._pipeline()
        name = payload.get("filename") or "documento.txt"
        text = payload.get("text", "")

        suffix = Path(name).suffix.lower()
        if suffix and suffix not in ALLOWED_SUFFIXES:
            return {"error": f"Estensione non supportata: {suffix}"}
        if not text.strip():
            return {"error": "Documento vuoto"}

        # `write=False`: dalla GUI il documento si guarda e si scarica dal
        # browser. Scriverlo comunque su disco lascerebbe copie che l'utente non
        # ha chiesto, in un tool il cui scopo è ridurre le copie dei dati.
        # Restano scritti l'audit log (registro degli eventi) e il vault (serve
        # alla re-idratazione).
        result = pipeline.process_text(text, source_name=name, write=False)
        import json as _json

        return {
            "uuid": result.doc_uuid,
            "decision": result.decision.value,
            "safe_markdown": result.safe_markdown,
            "manifest_json": _json.dumps(
                result.manifest.to_dict(), indent=2, ensure_ascii=False
            ),
            "tokens": len(result.mapping),
            "ocr_confidence": round(result.ocr_confidence, 3),
            "entities": result.screen.by_type(),
            "layers": result.screen.by_layer(),
            "reasons": result.manifest.decision_details.get("reasons", []),
            "ner_enabled": result.screen.stats.get("ner_enabled", False),
            "persisted": False,
        }

    def _process_pdf(self, payload: dict) -> dict:
        """Elabora un PDF inviato come base64. Il file temporaneo viene cancellato
        subito dopo l'elaborazione — coerente con il principio di non lasciare copie
        non richieste."""
        data_b64 = payload.get("data", "")
        name = payload.get("filename") or "documento.pdf"
        if not data_b64:
            return {"error": "Nessun contenuto PDF ricevuto"}

        try:
            raw = base64.b64decode(data_b64)
        except Exception:
            return {"error": "Contenuto base64 non valido"}

        if len(raw) > MAX_UPLOAD_BYTES:
            return {"error": f"File troppo grande ({len(raw)} byte, max {MAX_UPLOAD_BYTES})"}

        pipeline = self._pipeline()
        tmp = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(raw)
            tmp.close()

            result = pipeline.process_pdf(Path(tmp.name), write=False)
            import json as _json

            return {
                "uuid": result.doc_uuid,
                "decision": result.decision.value,
                "safe_markdown": result.safe_markdown,
                "manifest_json": _json.dumps(
                    result.manifest.to_dict(), indent=2, ensure_ascii=False
                ),
                "tokens": len(result.mapping),
                "ocr_confidence": round(result.ocr_confidence, 3),
                "entities": result.screen.by_type(),
                "layers": result.screen.by_layer(),
                "reasons": result.manifest.decision_details.get("reasons", []),
                "ner_enabled": result.screen.stats.get("ner_enabled", False),
                "persisted": False,
            }
        finally:
            if tmp:
                Path(tmp.name).unlink(missing_ok=True)

    def _rehydrate(self, payload: dict) -> dict:
        uuid = payload.get("uuid", "")
        response = payload.get("response", "")
        vault = Vault(self.session.config.vault_dir)
        try:
            record = vault.load(uuid)
        except VaultError as exc:
            return {"error": str(exc)}

        report = rehydrate(response, record.mapping)
        return {"text": report.text, **report.summary()}

    def _forget(self, payload: dict) -> dict:
        uuid = payload.get("uuid", "")
        vault = Vault(self.session.config.vault_dir)
        report = vault.forget(uuid)
        AuditLog(self.session.config.audit_path).append(
            "forget", {"doc_uuid": uuid, **report, "via": "gui"}
        )
        return report


def serve(
    config: Config | None = None,
    *,
    port: int = 8731,
    open_browser: bool = True,
) -> int:
    """Avvia la GUI su 127.0.0.1.

    L'host non è configurabile di proposito: esporre questa interfaccia oltre il
    loopback renderebbe raggiungibili in rete i documenti e i loro contenuti, che
    è esattamente ciò che l'intero progetto esiste per evitare.
    """
    config = config or Config.load()
    config.ensure_dirs()

    session = Session(config=config, csrf_token=secrets.token_urlsafe(32))
    Handler.session = session

    host = "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)

    bound_host, bound_port = server.server_address[:2]
    if bound_host != host:  # pragma: no cover - salvaguardia
        server.server_close()
        raise RuntimeError(f"Binding inatteso su {bound_host}: interrotto per sicurezza")

    url = f"http://{host}:{bound_port}/"
    print(f"anton-ocr GUI {__version__}")
    print(f"  {url}")
    print(f"  policy: {load_policy(config.policy_file).profile}")
    print("  in ascolto solo su loopback — non raggiungibile dalla rete")
    print("  Ctrl-C per fermare")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto della GUI.")
    finally:
        server.server_close()
    return 0
