"""Audit log append-only con catena di hash.

Ogni riga contiene l'hash della riga precedente. Alterare o rimuovere una riga
rompe la catena a partire da quel punto, e ``verify()`` lo rileva. È una
proprietà molto più forte di un semplice file di log, e non richiede un database:
il file resta leggibile, versionabile e ispezionabile con ``cat``.

Non impedisce la manomissione — la rende **evidente**. Per l'art. 12 AI Act
(registrazione degli eventi per la tracciabilità) è esattamente ciò che serve.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canonical(payload: dict) -> bytes:
    """Serializzazione deterministica: senza di essa l'hash non è riproducibile."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def entry_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256(prev_hash.encode() + _canonical(payload)).hexdigest()


@dataclass
class AuditEntry:
    seq: int
    timestamp: str
    event: str
    payload: dict
    prev_hash: str
    hash: str

    @classmethod
    def from_line(cls, line: str) -> AuditEntry:
        data = json.loads(line)
        return cls(
            seq=data["seq"],
            timestamp=data["timestamp"],
            event=data["event"],
            payload=data["payload"],
            prev_hash=data["prev_hash"],
            hash=data["hash"],
        )

    def to_line(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "timestamp": self.timestamp,
                "event": self.event,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "hash": self.hash,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def recompute(self) -> str:
        return entry_hash(
            self.prev_hash,
            {
                "seq": self.seq,
                "timestamp": self.timestamp,
                "event": self.event,
                "payload": self.payload,
            },
        )


@dataclass
class VerificationResult:
    valid: bool
    entries: int
    broken_at: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "entries": self.entries,
            "broken_at": self.broken_at,
            "reason": self.reason,
        }


class AuditLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    # ── scrittura ──

    def append(self, event: str, payload: dict) -> AuditEntry:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prev_hash, seq = self._tail()

        body = {
            "seq": seq + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "payload": payload,
        }
        digest = entry_hash(prev_hash, body)
        entry = AuditEntry(**body, prev_hash=prev_hash, hash=digest)

        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(entry.to_line() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def _tail(self) -> tuple[str, int]:
        if not self.path.exists():
            return GENESIS, 0
        last_hash, last_seq = GENESIS, 0
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                last_hash, last_seq = data["hash"], data["seq"]
        return last_hash, last_seq

    # ── lettura ──

    def entries(self) -> Iterator[AuditEntry]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield AuditEntry.from_line(line)

    def verify(self) -> VerificationResult:
        """Ricalcola la catena e individua il punto esatto della rottura."""
        prev_hash = GENESIS
        count = 0
        expected_seq = 1

        for entry in self.entries():
            count += 1
            if entry.seq != expected_seq:
                return VerificationResult(
                    valid=False,
                    entries=count,
                    broken_at=entry.seq,
                    reason=f"Numerazione non contigua: attesa {expected_seq}, trovata {entry.seq} "
                    "(una riga è stata rimossa o inserita)",
                )
            if entry.prev_hash != prev_hash:
                return VerificationResult(
                    valid=False,
                    entries=count,
                    broken_at=entry.seq,
                    reason="prev_hash non corrisponde all'hash della riga precedente",
                )
            if entry.recompute() != entry.hash:
                return VerificationResult(
                    valid=False,
                    entries=count,
                    broken_at=entry.seq,
                    reason="Contenuto alterato: l'hash ricalcolato non coincide",
                )
            prev_hash = entry.hash
            expected_seq += 1

        return VerificationResult(valid=True, entries=count)

    def for_document(self, doc_uuid: str) -> list[AuditEntry]:
        return [e for e in self.entries() if e.payload.get("doc_uuid") == doc_uuid]
