"""Pipeline: dal documento al testo sicuro, con evidenza.

    INGEST → EXTRACT → SCREEN → POLICY → REDACT → ATTEST → EMIT

Il testo prodotto è ciò che può essere inviato a un modello esterno. L'originale
e la mappatura dei token restano sulla macchina.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from anton_ocr import __version__
from anton_ocr.compliance.audit import AuditLog
from anton_ocr.compliance.manifest import (
    DocumentInfo,
    ExtractionInfo,
    Manifest,
    ModelRef,
    RedactionInfo,
    SigningKey,
    sha256_file,
    sha256_text,
)
from anton_ocr.config import Config
from anton_ocr.emit import render_safe_markdown
from anton_ocr.ocr.confidence import aggregate, assess_text_quality
from anton_ocr.ocr.extract import TextExtractor
from anton_ocr.policy.engine import Decision, Policy, PolicyEngine, load_policy
from anton_ocr.privacy.ner import NERConfig
from anton_ocr.privacy.pseudonymize import Pseudonymizer, new_salt
from anton_ocr.privacy.screen import ScreenConfig, Screener, ScreenResult
from anton_ocr.privacy.vault import Vault

log = logging.getLogger(__name__)

# Limiti dichiarati in ogni manifest. Sono i limiti reali dello strumento: ometterli
# significherebbe lasciar credere che il risultato sia più forte di quanto sia.
STANDARD_LIMITATIONS = [
    "Il rilevamento PII non ha recall del 100%: alcune entità possono sfuggire, "
    "soprattutto su testo con qualità OCR bassa.",
    "La pseudonimizzazione non impedisce la re-identificazione per inferenza dal "
    "contesto residuo (quasi-identificatori).",
    "Il dato pseudonimizzato resta dato personale ai sensi dell'art. 4(5) GDPR.",
    "Il testo contenuto nelle immagini incorporate non è coperto dalla redazione "
    "testuale.",
]


@dataclass
class IngestResult:
    doc_uuid: str
    decision: Decision
    safe_text: str
    original_text: str
    manifest: Manifest
    screen: ScreenResult
    mapping: dict[str, str] = field(default_factory=dict)
    output_path: Path | None = None
    manifest_path: Path | None = None
    vault_path: Path | None = None
    ocr_confidence: float = 1.0
    pages_below_threshold: list[int] = field(default_factory=list)

    # Documento sicuro completo (frontmatter + legenda + contenuto): è ciò che
    # va consegnato all'agente. ``safe_text`` è solo il testo pseudonimizzato.
    safe_markdown: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def summary(self) -> dict:
        return {
            "doc_uuid": self.doc_uuid,
            "decision": self.decision.value,
            "entities": self.screen.by_type(),
            "layers": self.screen.by_layer(),
            "tokens": len(self.mapping),
            "ocr_confidence": round(self.ocr_confidence, 3),
            "pages_below_threshold": self.pages_below_threshold,
        }


class Pipeline:
    def __init__(
        self,
        config: Config | None = None,
        policy: Policy | None = None,
        *,
        signing_key: SigningKey | None = None,
    ) -> None:
        self.config = config or Config()
        self.policy = policy or load_policy(self.config.policy_file)
        self.engine = PolicyEngine(self.policy)
        self.screener = Screener(
            ScreenConfig(
                ner=NERConfig(
                    backend=self.config.ner_backend,
                    model=self.config.ner_model,
                    sealed=self.config.sealed,
                )
            )
        )
        self.audit = AuditLog(self.config.audit_path)
        self._signing_key = signing_key

    @property
    def signing_key(self) -> SigningKey:
        if self._signing_key is None:
            self._signing_key = SigningKey.load_or_create(self.config.signing_key_path)
        return self._signing_key

    # ─────────────────── ingresso ───────────────────

    def process_text(
        self,
        text: str,
        *,
        source_name: str = "inline.txt",
        pages: int = 1,
        ocr_confidence: float | None = None,
        pages_below: list[int] | None = None,
        extraction: ExtractionInfo | None = None,
        models: list[ModelRef] | None = None,
        write: bool = True,
    ) -> IngestResult:
        """Elabora testo già estratto. È il nucleo: ``process_pdf`` ci passa sopra."""
        doc_uuid = str(uuid.uuid4())
        source_hash = sha256_text(text)

        self.audit.append(
            "ingest",
            {
                "doc_uuid": doc_uuid,
                "source_name": source_name,
                "source_sha256": source_hash,
                "policy_sha256": self.policy.sha256,
                "tool_version": __version__,
            },
        )

        # ── SCREEN ──
        detection_error = False
        try:
            screen = self.screener.scan(text)
        except Exception as exc:  # pragma: no cover - percorso difensivo
            log.exception("Rilevamento fallito")
            detection_error = True
            screen = ScreenResult(spans=[], stats={"error": str(exc)})

        if ocr_confidence is None:
            ocr_confidence = assess_text_quality(text).score
        pages_below = pages_below or []

        # ── POLICY ──
        outcome = self.engine.evaluate(
            screen.spans,
            ocr_confidence=ocr_confidence,
            detection_error=detection_error,
        )

        self.audit.append(
            "screen",
            {
                "doc_uuid": doc_uuid,
                "entities": screen.by_type(),
                "layers": screen.by_layer(),
                "ocr_confidence": round(ocr_confidence, 3),
                "decision": outcome.decision.value,
            },
        )

        # ── REDACT ──
        anonymize = self.policy.mode == "anonymize"
        salt = new_salt()
        pseudonymizer = Pseudonymizer(salt=salt, keep_mapping=not anonymize)
        redaction = pseudonymizer.apply(
            text,
            screen.spans,
            actions=self.policy.actions_map(),
            generalizations=self.policy.generalizations_map(),
        )

        # ── VAULT ──
        vault_path = None
        if not anonymize and self.policy.vault_enabled and redaction.mapping:
            vault = Vault(self.config.vault_dir)
            vault_path = vault.store(
                doc_uuid,
                redaction.mapping,
                salt,
                ttl_days=self.policy.vault_ttl_days,
            )

        # ── ATTEST ──
        manifest = self._build_manifest(
            doc_uuid=doc_uuid,
            source_name=source_name,
            source_hash=source_hash,
            pages=pages,
            screen=screen,
            redaction=redaction,
            outcome=outcome,
            ocr_confidence=ocr_confidence,
            pages_below=pages_below,
            extraction=extraction,
            models=models,
            anonymize=anonymize,
            vault_path=vault_path,
        )
        manifest.sign(self.signing_key)

        result = IngestResult(
            doc_uuid=doc_uuid,
            decision=outcome.decision,
            safe_text=redaction.text,
            original_text=text,
            manifest=manifest,
            screen=screen,
            mapping=redaction.mapping,
            vault_path=vault_path,
            ocr_confidence=ocr_confidence,
            pages_below_threshold=pages_below,
        )

        # Il documento sicuro viene sempre reso: è il risultato dell'elaborazione.
        # La scrittura su disco è un'altra cosa e resta opzionale — dalla GUI il
        # documento si guarda e si scarica solo se lo si vuole davvero, senza
        # lasciare copie in giro.
        result.safe_markdown = render_safe_markdown(result, self.policy).markdown

        if write:
            self._emit(result, source_name)

        self.audit.append(
            "decision",
            {
                "doc_uuid": doc_uuid,
                **outcome.as_dict(),
                "tokens_issued": redaction.tokens_issued,
                "output": str(result.output_path) if result.output_path else None,
            },
        )
        return result

    def process_pdf(self, path: Path | str, *, write: bool = True) -> IngestResult:
        path = Path(path).expanduser()
        extractor = TextExtractor(self.config.extractor_backend)
        pages = extractor.extract(path, text_threshold=self.config.text_threshold)

        qualities = [p.quality for p in pages]
        mean_confidence, below = aggregate(qualities)

        needs_ocr = [p.page_number for p in pages if p.task == "needs_ocr"]
        if needs_ocr:
            log.warning(
                "%d pagine hanno poco testo nativo e richiederebbero il modello vision: %s. "
                "Vengono elaborate con il solo testo disponibile.",
                len(needs_ocr),
                needs_ocr,
            )

        text = "\n\n---\n\n".join(p.text.strip() for p in pages if p.text.strip())

        extraction = ExtractionInfo(
            native_pages=sum(1 for p in pages if p.task == "text"),
            ocr_pages=len(needs_ocr),
            mean_confidence=round(mean_confidence, 3),
            pages_below_threshold=below,
            task_summary={
                "text": sum(1 for p in pages if p.task == "text"),
                "needs_ocr": len(needs_ocr),
            },
        )

        result = self.process_text(
            text,
            source_name=path.name,
            pages=len(pages),
            ocr_confidence=mean_confidence,
            pages_below=below,
            extraction=extraction,
            models=[
                ModelRef(
                    role="extractor",
                    id=extractor.backend,
                    license="Apache-2.0" if extractor.backend == "pypdfium2" else "AGPL-3.0",
                    backend=extractor.backend,
                )
            ],
            write=write,
        )
        # L'hash del file sorgente è più informativo dell'hash del testo estratto.
        result.manifest.document.source_sha256 = sha256_file(path)
        result.manifest.sign(self.signing_key)
        if write and result.manifest_path:
            result.manifest.write(result.manifest_path)
        return result

    # ─────────────────── uscita ───────────────────

    def _emit(self, result: IngestResult, source_name: str) -> None:
        """Scrive il documento sicuro e il manifest.

        L'output non è il testo grezzo pseudonimizzato ma un Markdown
        autoconsistente: chi lo incolla in una chat non porta con sé il manifest,
        quindi tutto ciò che serve per usarlo correttamente sta nel file.
        """
        self.config.ensure_dirs()
        stem = Path(source_name).stem

        if result.decision is Decision.BLOCK:
            target_dir = self.config.quarantine_dir
        elif result.decision is Decision.REVIEW:
            target_dir = self.config.review_dir
        else:
            target_dir = self.config.output_dir

        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / f"{stem}.safe.md"
        output_path.write_text(result.safe_markdown, encoding="utf-8")

        manifest_path = target_dir / f"{stem}.manifest.json"
        result.manifest.write(manifest_path)

        result.output_path = output_path
        result.manifest_path = manifest_path

    def _build_manifest(
        self,
        *,
        doc_uuid: str,
        source_name: str,
        source_hash: str,
        pages: int,
        screen: ScreenResult,
        redaction,
        outcome,
        ocr_confidence: float,
        pages_below: list[int],
        extraction: ExtractionInfo | None,
        models: list[ModelRef] | None,
        anonymize: bool,
        vault_path: Path | None,
    ) -> Manifest:
        stats = screen.stats
        return Manifest(
            document=DocumentInfo(
                uuid=doc_uuid,
                source_name=source_name,
                source_sha256=source_hash,
                pages=pages,
            ),
            pipeline={
                "tool": "anton-ocr",
                "tool_version": __version__,
                "policy_profile": self.policy.profile,
                "policy_sha256": self.policy.sha256,
                "policy_source": str(self.policy.source_path) if self.policy.source_path else "built-in",
                "mode": self.policy.mode,
                "stages": ["ingest", "extract", "screen", "policy", "redact", "attest", "emit"],
                "detection_layers": list(self.screener.config.layers),
                "egress": "none",
                "sealed": self.config.sealed,
            },
            models=models or [],
            extraction=extraction
            or ExtractionInfo(
                native_pages=pages,
                ocr_pages=0,
                mean_confidence=round(ocr_confidence, 3),
                pages_below_threshold=pages_below,
            ),
            redaction=RedactionInfo(
                by_type=screen.by_type(),
                by_layer=screen.by_layer(),
                tokens_issued=redaction.tokens_issued,
                unique_tokens=len(redaction.mapping),
                ocr_recovered=stats.get("ocr_recovered", 0),
                ocr_recovered_ambiguous=stats.get("ocr_recovered_ambiguous", 0),
                reversible=not anonymize,
                vault_ref=str(vault_path.name) if vault_path else None,
                mode=self.policy.mode,
            ),
            decision=outcome.decision.value,
            decision_details=outcome.as_dict(),
            ai_act={
                "art50_marked": True,
                "content_note": "Testo estratto e trasformato automaticamente da anton-ocr.",
                "art12_audit_log": str(self.config.audit_path),
                "art10_provenance": "document.source_sha256 + pipeline + models",
            },
            limitations=STANDARD_LIMITATIONS,
        )
