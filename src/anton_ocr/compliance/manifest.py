"""Manifest di provenienza firmato.

Un sidecar ``.manifest.json`` per ogni output, firmato Ed25519. Risponde alla
domanda che nessuna pipeline documentale risponde di solito:

    *chi può dimostrare cosa c'era nel documento, cosa è stato tolto, con quale
    modello, quando e da chi?*

Contenuto rilevante per l'AI Act:

* ``document`` + ``pipeline`` → provenienza e tracciabilità dei dati (art. 10)
* ``models``                  → identificazione dei modelli usati, con hash
* ``redaction``               → evidenza di minimizzazione (art. 5(1)(c) GDPR)
* ``ai_act.art50_marked``     → marcatura del contenuto prodotto da AI (art. 50)
* ``signature``               → integrità verificabile del manifest stesso
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MANIFEST_SCHEMA = "anton-ocr/manifest/v1"


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ─────────────────────── Chiavi di firma ───────────────────────


class SigningKey:
    """Chiave Ed25519 per la firma dei manifest."""

    def __init__(self, private: Ed25519PrivateKey) -> None:
        self._private = private

    @classmethod
    def generate(cls) -> SigningKey:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load(cls, path: Path | str) -> SigningKey:
        data = Path(path).expanduser().read_bytes()
        private = serialization.load_pem_private_key(data, password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise ValueError("La chiave fornita non è Ed25519")
        return cls(private)

    @classmethod
    def load_or_create(cls, path: Path | str) -> SigningKey:
        path = Path(path).expanduser()
        if path.exists():
            return cls.load(path)
        key = cls.generate()
        key.save(path)
        return key

    def save(self, path: Path | str) -> Path:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.write_bytes(pem)
        os.chmod(path, 0o600)
        return path

    @property
    def public_pem(self) -> str:
        return (
            self._private.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

    @property
    def key_id(self) -> str:
        raw = self._private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return hashlib.sha256(raw).hexdigest()[:16]

    def sign(self, data: bytes) -> bytes:
        return self._private.sign(data)


def verify_signature(public_pem: str, data: bytes, signature: bytes) -> bool:
    public = serialization.load_pem_public_key(public_pem.encode())
    if not isinstance(public, Ed25519PublicKey):
        return False
    try:
        public.verify(signature, data)
    except InvalidSignature:
        return False
    return True


# ─────────────────────── Sezioni del manifest ───────────────────────


@dataclass
class ModelRef:
    role: str  # ocr | pii | adjudication | image
    id: str
    params: str | None = None
    license: str | None = None
    sha256: str | None = None
    pages_processed: int | None = None
    backend: str | None = None


@dataclass
class DocumentInfo:
    uuid: str
    source_name: str
    source_sha256: str
    pages: int
    ingested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    operator: str = field(
        default_factory=lambda: f"{os.environ.get('USER', 'unknown')}@{platform.node()}"
    )


@dataclass
class ExtractionInfo:
    native_pages: int = 0
    ocr_pages: int = 0
    mean_confidence: float = 1.0
    pages_below_threshold: list[int] = field(default_factory=list)
    task_summary: dict = field(default_factory=dict)


@dataclass
class RedactionInfo:
    by_type: dict = field(default_factory=dict)
    by_layer: dict = field(default_factory=dict)
    tokens_issued: int = 0
    unique_tokens: int = 0
    ocr_recovered: int = 0
    ocr_recovered_ambiguous: int = 0
    reversible: bool = True
    vault_ref: str | None = None
    mode: str = "pseudonymize"


# ─────────────────────── Manifest ───────────────────────


@dataclass
class Manifest:
    document: DocumentInfo
    pipeline: dict = field(default_factory=dict)
    models: list[ModelRef] = field(default_factory=list)
    extraction: ExtractionInfo = field(default_factory=ExtractionInfo)
    redaction: RedactionInfo = field(default_factory=RedactionInfo)
    decision: str = "allow"
    decision_details: dict = field(default_factory=dict)
    ai_act: dict = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    signature: dict | None = None

    # Avvertenza inclusa in ogni manifest: impedisce che il documento venga
    # letto come una dichiarazione di conformità, che non è.
    disclaimer: str = (
        "Questo manifest è un artefatto di evidenza tecnica. Non costituisce una "
        "valutazione di conformità né una dichiarazione di conformità ai sensi "
        "dell'AI Act o del GDPR, e non è consulenza legale."
    )

    def payload(self) -> dict:
        """Il contenuto firmato: tutto tranne la firma stessa."""
        return {
            "schema": MANIFEST_SCHEMA,
            "document": asdict(self.document),
            "pipeline": self.pipeline,
            "models": [asdict(m) for m in self.models],
            "extraction": asdict(self.extraction),
            "redaction": asdict(self.redaction),
            "decision": self.decision,
            "decision_details": self.decision_details,
            "ai_act": self.ai_act,
            "limitations": self.limitations,
            "disclaimer": self.disclaimer,
        }

    def sign(self, key: SigningKey) -> Manifest:
        data = canonical_json(self.payload())
        self.signature = {
            "alg": "ed25519",
            "key_id": key.key_id,
            "public_key": key.public_pem,
            "value": key.sign(data).hex(),
            "signed_at": datetime.now(timezone.utc).isoformat(),
        }
        return self

    def to_dict(self) -> dict:
        out = self.payload()
        out["signature"] = self.signature
        return out

    def write(self, path: Path | str) -> Path:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path


@dataclass
class ManifestVerification:
    valid: bool
    reason: str | None = None
    key_id: str | None = None
    decision: str | None = None

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "key_id": self.key_id,
            "decision": self.decision,
        }


def verify_manifest_file(path: Path | str) -> ManifestVerification:
    """Verifica la firma di un manifest su disco.

    La chiave pubblica è inclusa nel manifest: questo consente la verifica di
    integrità senza scambi preliminari, ma **non** autentica l'origine. Per
    l'autenticazione la chiave pubblica va confrontata con una fonte fidata.
    """
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    signature = data.pop("signature", None)
    if not signature:
        return ManifestVerification(valid=False, reason="Manifest privo di firma")

    public_pem = signature.get("public_key")
    if not public_pem:
        return ManifestVerification(valid=False, reason="Firma priva di chiave pubblica")

    try:
        raw_signature = bytes.fromhex(signature["value"])
    except (KeyError, ValueError):
        return ManifestVerification(valid=False, reason="Firma malformata")

    ok = verify_signature(public_pem, canonical_json(data), raw_signature)
    return ManifestVerification(
        valid=ok,
        reason=None if ok else "Firma non valida: il manifest è stato alterato",
        key_id=signature.get("key_id"),
        decision=data.get("decision"),
    )
