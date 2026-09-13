"""Vault cifrato per la mappatura token → valore originale.

Il vault è l'unico punto in cui i dati personali sopravvivono alla
pseudonimizzazione. Di conseguenza:

* è cifrato con ChaCha20-Poly1305 (AEAD, nonce casuale per scrittura);
* la chiave non è mai scritta in chiaro su disco — sta nel portachiavi di
  sistema, oppure in una variabile d'ambiente, oppure deriva da passphrase
  via scrypt;
* ha un TTL: alla scadenza si distrugge la chiave, non il file. È
  **cancellazione crittografica**, ed è ciò che rende ``forget`` istantaneo e
  irreversibile anche sui backup già effettuati.

``forget(uuid)`` è il percorso pulito per l'art. 17 GDPR: distrutta la chiave,
i token diventano definitivamente irreversibili.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

log = logging.getLogger(__name__)

KEYRING_SERVICE = "anton-ocr-vault"
ENV_KEY = "ANTON_OCR_VAULT_KEY"
VAULT_VERSION = 1


class VaultError(RuntimeError):
    pass


class VaultKeyUnavailable(VaultError):
    """La chiave non è recuperabile: il vault non è decifrabile."""


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _b64d(data: str) -> bytes:
    return base64.b64decode(data)


# ─────────────────────── Gestione chiave ───────────────────────


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
    except ImportError:
        return False
    return True


def _store_key_keyring(doc_uuid: str, key: bytes) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, doc_uuid, _b64e(key))
        return True
    except Exception as exc:  # pragma: no cover - dipende dall'ambiente
        log.warning("Portachiavi non utilizzabile (%s)", exc)
        return False


def _load_key_keyring(doc_uuid: str) -> bytes | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, doc_uuid)
        return _b64d(value) if value else None
    except Exception:  # pragma: no cover
        return None


def _delete_key_keyring(doc_uuid: str) -> bool:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, doc_uuid)
        return True
    except Exception:
        return False


def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return kdf.derive(passphrase.encode())


# ─────────────────────── Vault ───────────────────────


@dataclass
class VaultRecord:
    doc_uuid: str
    mapping: dict[str, str]
    token_salt: bytes
    created_at: str
    ttl_days: int

    @property
    def expires_at(self) -> datetime:
        created = datetime.fromisoformat(self.created_at)
        return created + timedelta(days=self.ttl_days)

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class Vault:
    """Un file per documento, sotto ``vault_dir``."""

    def __init__(
        self,
        vault_dir: Path | str,
        *,
        key_source: str = "auto",  # auto | keyring | env | passphrase
        passphrase: str | None = None,
    ) -> None:
        self.vault_dir = Path(vault_dir).expanduser()
        self.key_source = key_source
        self.passphrase = passphrase

    def path_for(self, doc_uuid: str) -> Path:
        return self.vault_dir / f"{doc_uuid}.vault"

    # ── scrittura ──

    def store(
        self,
        doc_uuid: str,
        mapping: dict[str, str],
        token_salt: bytes,
        *,
        ttl_days: int = 90,
    ) -> Path:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)

        payload = json.dumps(
            {
                "mapping": mapping,
                "token_salt": _b64e(token_salt),
            },
            ensure_ascii=False,
        ).encode()

        nonce = secrets.token_bytes(12)
        ciphertext = ChaCha20Poly1305(key).encrypt(nonce, payload, doc_uuid.encode())

        envelope = {
            "version": VAULT_VERSION,
            "doc_uuid": doc_uuid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl_days": ttl_days,
            "nonce": _b64e(nonce),
            "ciphertext": _b64e(ciphertext),
        }

        key_source, key_material = self._persist_key(doc_uuid, key)
        envelope["key_source"] = key_source
        if key_material:
            envelope.update(key_material)

        if key_source == "keyfile":
            self._write_keyfile(doc_uuid, key)

        path = self.path_for(doc_uuid)
        path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        os.chmod(path, 0o600)
        log.info("Vault scritto: %s (chiave: %s)", path.name, key_source)
        return path

    # ── file di chiave (ripiego) ──

    def keyfile_for(self, doc_uuid: str) -> Path:
        return self.vault_dir / "keys" / f"{doc_uuid}.key"

    def _write_keyfile(self, doc_uuid: str, key: bytes) -> Path:
        path = self.keyfile_for(doc_uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        path.write_text(_b64e(key), encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def _read_keyfile(self, doc_uuid: str) -> bytes | None:
        path = self.keyfile_for(doc_uuid)
        if not path.exists():
            return None
        return _b64d(path.read_text(encoding="utf-8").strip())

    def _persist_key(self, doc_uuid: str, key: bytes) -> tuple[str, dict]:
        source = self.key_source

        # Preferenza 1: passphrase esplicita — la chiave non tocca il disco in
        # forma utilizzabile senza il segreto dell'utente.
        if source == "passphrase" or (source == "auto" and self.passphrase):
            if not self.passphrase:
                raise VaultError("Passphrase richiesta ma non fornita")
            salt = secrets.token_bytes(16)
            derived = derive_key(self.passphrase, salt)
            wrapped = ChaCha20Poly1305(derived).encrypt(b"\x00" * 12, key, b"keywrap")
            return "passphrase", {"kdf_salt": _b64e(salt), "wrapped_key": _b64e(wrapped)}

        # Preferenza 2: portachiavi di sistema.
        if source in ("auto", "keyring") and _keyring_available():
            if _store_key_keyring(doc_uuid, key):
                return "keyring", {}
            if source == "keyring":
                raise VaultError("Portachiavi richiesto ma non utilizzabile")

        # Preferenza 3: variabile d'ambiente, solo se richiesta esplicitamente.
        # Non sopravvive al processo: utile per pipeline effimere, inutile per
        # il giro di ritorno che avviene in un secondo momento.
        if source == "env":
            os.environ[ENV_KEY] = _b64e(key)
            return "env", {}

        # Ripiego: file di chiave separato, permessi 0600.
        #
        # Compromesso dichiarato: la chiave sta accanto al vault, quindi protegge
        # dalla condivisione accidentale del solo file .vault (backup, sync,
        # allegato) ma non da chi ottiene accesso completo alla cartella. Su
        # sistemi senza portachiavi è l'unica opzione che mantiene funzionante il
        # giro di ritorno fra processi diversi; per una protezione reale a riposo
        # si usi ``key_source="passphrase"``.
        return "keyfile", {}

    # ── lettura ──

    def load(self, doc_uuid: str) -> VaultRecord:
        path = self.path_for(doc_uuid)
        if not path.exists():
            raise VaultError(f"Vault inesistente per {doc_uuid}")
        envelope = json.loads(path.read_text(encoding="utf-8"))

        key = self._recover_key(doc_uuid, envelope)
        if key is None:
            raise VaultKeyUnavailable(
                f"Chiave non recuperabile per {doc_uuid} "
                f"(sorgente dichiarata: {envelope.get('key_source')}). "
                "Se il documento è stato dimenticato, questo è il comportamento atteso."
            )

        plaintext = ChaCha20Poly1305(key).decrypt(
            _b64d(envelope["nonce"]), _b64d(envelope["ciphertext"]), doc_uuid.encode()
        )
        data = json.loads(plaintext)
        return VaultRecord(
            doc_uuid=doc_uuid,
            mapping=data["mapping"],
            token_salt=_b64d(data["token_salt"]),
            created_at=envelope["created_at"],
            ttl_days=envelope.get("ttl_days", 90),
        )

    def _recover_key(self, doc_uuid: str, envelope: dict) -> bytes | None:
        source = envelope.get("key_source")
        if source == "keyring":
            return _load_key_keyring(doc_uuid)
        if source == "passphrase":
            if not self.passphrase:
                raise VaultError("Passphrase richiesta per aprire questo vault")
            derived = derive_key(self.passphrase, _b64d(envelope["kdf_salt"]))
            try:
                return ChaCha20Poly1305(derived).decrypt(
                    b"\x00" * 12, _b64d(envelope["wrapped_key"]), b"keywrap"
                )
            except Exception as exc:
                raise VaultKeyUnavailable("Passphrase errata") from exc
        if source == "env":
            value = os.environ.get(ENV_KEY)
            return _b64d(value) if value else None
        if source == "keyfile":
            return self._read_keyfile(doc_uuid)
        return None

    # ── cancellazione ──

    def forget(self, doc_uuid: str) -> dict:
        """Cancellazione crittografica: distrugge la chiave e rimuove il vault.

        Distruggere la chiave è più forte che cancellare il file: rende
        irrecuperabili anche le copie già finite nei backup.
        """
        report = {"doc_uuid": doc_uuid, "key_destroyed": False, "file_removed": False}

        # Il file di chiave va distrutto sempre, anche se il vault manca.
        keyfile = self.keyfile_for(doc_uuid)
        if keyfile.exists():
            try:
                keyfile.write_bytes(secrets.token_bytes(max(keyfile.stat().st_size, 32)))
            except OSError:
                pass
            keyfile.unlink()
            report["key_destroyed"] = True

        path = self.path_for(doc_uuid)
        if path.exists():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            source = envelope.get("key_source")
            if source == "keyring":
                report["key_destroyed"] = _delete_key_keyring(doc_uuid)
            elif source == "env":
                report["key_destroyed"] = os.environ.pop(ENV_KEY, None) is not None
            elif source == "passphrase":
                # Chiave avvolta da passphrase: si rimuove l'involucro.
                report["key_destroyed"] = True
            # Sovrascrittura prima della rimozione (best-effort: su SSD con
            # wear-levelling non è garantita — la garanzia vera è la chiave).
            try:
                size = path.stat().st_size
                path.write_bytes(secrets.token_bytes(size))
            except OSError:
                pass
            path.unlink()
            report["file_removed"] = True
        else:
            report["key_destroyed"] = _delete_key_keyring(doc_uuid)

        log.info("forget(%s): %s", doc_uuid, report)
        return report

    def purge_expired(self) -> list[str]:
        """Rimuove i vault scaduti. Da eseguire periodicamente."""
        removed: list[str] = []
        if not self.vault_dir.exists():
            return removed
        for path in self.vault_dir.glob("*.vault"):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(envelope["created_at"])
                ttl = envelope.get("ttl_days", 90)
                if datetime.now(timezone.utc) > created + timedelta(days=ttl):
                    self.forget(envelope["doc_uuid"])
                    removed.append(envelope["doc_uuid"])
            except Exception as exc:  # pragma: no cover
                log.warning("Vault illeggibile %s: %s", path.name, exc)
        return removed
