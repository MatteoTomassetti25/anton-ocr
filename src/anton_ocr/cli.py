"""Interfaccia a riga di comando.

    anton-ocr ingest documento.pdf        elabora e produce testo sicuro + manifest
    anton-ocr scan --text "..."           mostra cosa verrebbe rilevato, senza scrivere
    anton-ocr rehydrate risposta.txt      reinserisce i valori reali nella risposta
    anton-ocr verify output.manifest.json verifica la firma del manifest
    anton-ocr audit --verify              verifica l'integrità della catena di log
    anton-ocr forget <uuid>               distrugge la chiave: token irreversibili
    anton-ocr doctor                      stato dell'ambiente e dei backend
    anton-ocr gui                         interfaccia web locale (127.0.0.1)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from anton_ocr import __version__
from anton_ocr.compliance.audit import AuditLog
from anton_ocr.compliance.manifest import verify_manifest_file
from anton_ocr.config import Config
from anton_ocr.pipeline import Pipeline
from anton_ocr.policy.engine import Decision, load_policy
from anton_ocr.privacy.rehydrate import SYSTEM_PROMPT_HINT, rehydrate
from anton_ocr.privacy.screen import Screener, summarize_layers
from anton_ocr.privacy.vault import Vault, VaultError

DISCLAIMER = (
    "anton-ocr produce artefatti di evidenza tecnica. Non rende conformi all'AI Act "
    "o al GDPR, non è una valutazione di conformità e non è consulenza legale."
)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )


def _load(args) -> Pipeline:
    config = Config.load(args.config)
    if args.policy:
        config.policy_file = Path(args.policy)
    policy = load_policy(config.policy_file)
    return Pipeline(config, policy)


# ─────────────────────── comandi ───────────────────────


def cmd_ingest(args) -> int:
    pipeline = _load(args)
    path = Path(args.path).expanduser()

    if path.suffix.lower() == ".pdf":
        result = pipeline.process_pdf(path)
    else:
        result = pipeline.process_text(
            path.read_text(encoding="utf-8"), source_name=path.name
        )

    icon = {Decision.ALLOW: "✓", Decision.REVIEW: "⚠", Decision.BLOCK: "✗"}[result.decision]
    print(f"{icon} {result.decision.value.upper()}  {path.name}")
    print(f"  uuid            {result.doc_uuid}")
    print(f"  qualità testo   {result.ocr_confidence:.2f}")
    print(f"  rilevamento     {summarize_layers(result.screen)}")
    print(f"  token emessi    {len(result.mapping)}")
    if result.output_path:
        print(f"  output          {result.output_path}")
        print(f"  manifest        {result.manifest_path}")
    if result.vault_path:
        print(f"  vault           {result.vault_path}")

    details = result.manifest.decision_details
    for reason in details.get("reasons", []):
        print(f"  → {reason}")

    if args.json:
        print(json.dumps(result.summary(), indent=2, ensure_ascii=False))

    return 0 if result.decision is not Decision.BLOCK else 2


def cmd_scan(args) -> int:
    """Mostra cosa verrebbe rilevato senza scrivere nulla. Utile per tarare la policy."""
    text = args.text if args.text else Path(args.path).read_text(encoding="utf-8")
    result = Screener().scan(text)

    print(f"Rilevamento: {summarize_layers(result)}")
    print()
    for span in result.spans:
        marker = ""
        if span.ocr_corrected:
            marker = f"  ← recuperato da OCR: {span.corrected_text}"
        print(
            f"  {span.entity_type.value:24} {span.layer.value:13} "
            f"conf={span.confidence:.2f}  {span.text!r}{marker}"
        )
    print()
    print(json.dumps(result.stats, indent=2, ensure_ascii=False))
    return 0


def cmd_rehydrate(args) -> int:
    response = (
        Path(args.response).read_text(encoding="utf-8")
        if args.response != "-"
        else sys.stdin.read()
    )

    config = Config.load(args.config)
    vault = Vault(config.vault_dir)
    try:
        record = vault.load(args.uuid)
    except VaultError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    report = rehydrate(response, record.mapping, strict=args.strict)

    if args.output:
        Path(args.output).write_text(report.text, encoding="utf-8")
        print(f"✓ scritto in {args.output}")
    else:
        print(report.text)

    summary = report.summary()
    print(
        f"\n— sostituiti {summary['substituted']}/{summary['tokens_issued']} token "
        f"(copertura {summary['coverage']:.0%})",
        file=sys.stderr,
    )
    for warning in report.warnings:
        print(f"⚠  {warning}", file=sys.stderr)
    return 0


def cmd_verify(args) -> int:
    result = verify_manifest_file(args.manifest)
    if result.valid:
        print(f"✓ firma valida  key_id={result.key_id}  decisione={result.decision}")
        print(
            "  Nota: la chiave pubblica è inclusa nel manifest. Questo prova "
            "l'integrità, non l'origine: per autenticare il firmatario confronta "
            "la chiave con una fonte fidata."
        )
        return 0
    print(f"✗ {result.reason}", file=sys.stderr)
    return 1


def cmd_audit(args) -> int:
    config = Config.load(args.config)
    log = AuditLog(config.audit_path)

    if args.verify:
        result = log.verify()
        if result.valid:
            print(f"✓ catena integra — {result.entries} voci")
            return 0
        print(
            f"✗ catena compromessa alla voce {result.broken_at}: {result.reason}",
            file=sys.stderr,
        )
        return 1

    entries = log.for_document(args.doc) if args.doc else list(log.entries())
    for entry in entries[-args.limit :]:
        print(f"{entry.seq:5}  {entry.timestamp}  {entry.event:10}  {json.dumps(entry.payload, ensure_ascii=False)}")
    return 0


def cmd_forget(args) -> int:
    config = Config.load(args.config)
    vault = Vault(config.vault_dir)
    report = vault.forget(args.uuid)

    AuditLog(config.audit_path).append("forget", {"doc_uuid": args.uuid, **report})

    if report["key_destroyed"] or report["file_removed"]:
        print(f"✓ documento {args.uuid} dimenticato")
        print("  I token emessi per questo documento sono ora irreversibili,")
        print("  incluse le copie già presenti nei backup (cancellazione crittografica).")
        return 0
    print(f"⚠ nessun vault trovato per {args.uuid}", file=sys.stderr)
    return 1


def cmd_doctor(args) -> int:
    from anton_ocr.ocr.extract import available_backends

    config = Config.load(args.config)
    policy = load_policy(config.policy_file)

    print(f"anton-ocr {__version__}")
    print()
    print("Estrazione PDF:")
    for name, present in available_backends().items():
        note = "  (AGPL-3.0)" if name == "pymupdf" else ""
        print(f"  {'✓' if present else '·'} {name}{note}")

    print("\nRilevamento entità:")
    for module, label in (("gliner", "GLiNER"), ("presidio_analyzer", "Presidio")):
        try:
            __import__(module)
            print(f"  ✓ {label}")
        except ImportError:
            print(f"  · {label} (non installato)")

    print("\nBackend OCR:")
    for module, label in (("mlx_vlm", "MLX-VLM (Apple Silicon)"), ("ollama", "Ollama")):
        try:
            __import__(module)
            print(f"  ✓ {label}")
        except ImportError:
            print(f"  · {label} (non installato)")

    print("\nConfigurazione:")
    print(f"  policy          {policy.profile} (mode={policy.mode})")
    print(f"  policy sha256   {policy.sha256[:16]}…")
    print(f"  sealed          {config.sealed}")
    print(f"  output          {config.output_dir}")
    print(f"  vault           {config.vault_dir}")
    print(f"  audit           {config.audit_path}")

    audit = AuditLog(config.audit_path)
    if config.audit_path.exists():
        result = audit.verify()
        state = "integra" if result.valid else f"COMPROMESSA alla voce {result.broken_at}"
        print(f"  catena audit    {state} ({result.entries} voci)")

    print(f"\n{DISCLAIMER}")
    return 0


def cmd_prompt(args) -> int:
    """Stampa l'istruzione da anteporre al prompt del modello di frontiera."""
    print(SYSTEM_PROMPT_HINT)
    return 0


def cmd_gui(args) -> int:
    from anton_ocr.gui import serve

    config = Config.load(args.config)
    if args.policy:
        config.policy_file = Path(args.policy)
    return serve(config, port=args.port, open_browser=not args.no_browser)


# ─────────────────────── parser ───────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anton-ocr",
        description="Ingestione documentale locale con pseudonimizzazione e audit trail firmato.",
        epilog=DISCLAIMER,
    )
    parser.add_argument("--version", action="version", version=f"anton-ocr {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", help="percorso di config.env")
    parser.add_argument("--policy", help="percorso di policy.yaml")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="elabora un documento")
    p.add_argument("path")
    p.add_argument("--json", action="store_true", help="stampa anche il riepilogo JSON")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("scan", help="mostra il rilevamento senza scrivere nulla")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("path", nargs="?")
    group.add_argument("--text")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("rehydrate", help="reinserisce i valori reali nella risposta del modello")
    p.add_argument("uuid")
    p.add_argument("response", help="file con la risposta, oppure '-' per stdin")
    p.add_argument("-o", "--output")
    p.add_argument("--strict", action="store_true", help="errore se compaiono token sconosciuti")
    p.set_defaults(func=cmd_rehydrate)

    p = sub.add_parser("verify", help="verifica la firma di un manifest")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("audit", help="ispeziona o verifica l'audit log")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--doc", help="filtra per UUID documento")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("forget", help="distrugge la chiave del vault (art. 17 GDPR)")
    p.add_argument("uuid")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("doctor", help="stato dell'ambiente")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("prompt", help="istruzione di sistema per il modello di frontiera")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("gui", help="interfaccia web locale (solo 127.0.0.1)")
    p.add_argument("--port", type=int, default=8731)
    p.add_argument("--no-browser", action="store_true", help="non aprire il browser")
    p.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if args.verbose:
            raise
        print(f"✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
