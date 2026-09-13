"""Motore di policy: decide cosa fare di ogni entità e di ogni documento.

La policy è un file YAML versionabile in git accanto ai documenti. Questa non è
una comodità: **la policy stessa è evidenza**. Poter dimostrare quale regola era
in vigore al momento dell'elaborazione è metà del valore dell'audit trail, e
l'hash del file di policy finisce nel manifest.

Due scelte di default che vanno controcorrente e sono deliberate:

* le categorie particolari (art. 9 GDPR) hanno azione ``block``, non ``redact``:
  un referto medico privato dei nomi resta un referto medico;
* ``fail_closed`` è ``true``: se il rilevamento fallisce, il documento non passa.
  In un sistema di privacy degradare in silenzio significa perdere dati.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from anton_ocr.privacy.entities import EntityType, Span


class Action(str, Enum):
    PSEUDONYMIZE = "pseudonymize"
    GENERALIZE = "generalize"
    BLOCK = "block"
    REVIEW = "review"
    KEEP = "keep"


class Decision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


@dataclass
class EntityRule:
    entity_type: str
    action: Action = Action.PSEUDONYMIZE
    generalize_to: str | None = None
    reason: str | None = None
    min_confidence: float = 0.0


@dataclass
class Policy:
    version: int = 1
    profile: str = "gdpr-strict"
    mode: str = "pseudonymize"  # pseudonymize | anonymize
    rules: dict[str, EntityRule] = field(default_factory=dict)
    default_action: Action = Action.PSEUDONYMIZE

    review_below_confidence: float = 0.75
    block_on_detection_error: bool = True
    fail_closed: bool = True

    salt_scope: str = "document"
    vault_enabled: bool = True
    vault_ttl_days: int = 90

    audit_retention_days: int = 2555  # ~7 anni
    sealed: bool = True

    source_path: Path | None = None
    raw: dict = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        """Hash del contenuto della policy, da riportare nel manifest."""
        canonical = yaml.safe_dump(self.raw, sort_keys=True, allow_unicode=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def rule_for(self, entity_type: EntityType) -> EntityRule:
        rule = self.rules.get(entity_type.value)
        if rule is not None:
            return rule
        # Le categorie particolari non ereditano il default permissivo.
        if entity_type.is_special_category:
            return EntityRule(
                entity_type=entity_type.value,
                action=Action.BLOCK,
                reason="Categoria particolare art. 9 GDPR (default)",
            )
        return EntityRule(entity_type=entity_type.value, action=self.default_action)

    def actions_map(self) -> dict[str, str]:
        """Mappa tipo → azione, nel formato atteso dal pseudonimizzatore."""
        out: dict[str, str] = {}
        for entity_type in EntityType:
            rule = self.rule_for(entity_type)
            if rule.action in (Action.PSEUDONYMIZE, Action.GENERALIZE, Action.KEEP):
                out[entity_type.value] = rule.action.value
            else:
                # block e review non modificano il testo: la decisione è a livello
                # di documento, ma l'entità va comunque sostituita se il documento
                # prosegue.
                out[entity_type.value] = Action.PSEUDONYMIZE.value
        return out

    def generalizations_map(self) -> dict[str, str]:
        return {
            name: rule.generalize_to
            for name, rule in self.rules.items()
            if rule.action is Action.GENERALIZE and rule.generalize_to
        }


DEFAULT_POLICY_YAML = """\
version: 1
profile: gdpr-strict
mode: pseudonymize

entities:
  - { type: IT_CODICE_FISCALE,    action: pseudonymize }
  - { type: IT_PARTITA_IVA,       action: pseudonymize }
  - { type: IT_TESSERA_SANITARIA, action: pseudonymize }
  - { type: IBAN,                 action: pseudonymize }
  - { type: CREDIT_CARD,          action: pseudonymize }
  - { type: IT_TARGA,             action: pseudonymize }
  - { type: EMAIL,                action: pseudonymize }
  - { type: PHONE,                action: pseudonymize }
  - { type: IP_ADDRESS,           action: pseudonymize }
  - { type: MAC_ADDRESS,          action: pseudonymize }
  - { type: PERSON,               action: pseudonymize }
  - { type: ORGANIZATION,         action: keep }
  - { type: ADDRESS,              action: generalize, to: municipality }
  - { type: DATE_OF_BIRTH,        action: generalize, to: year }
  - { type: DATE,                 action: keep }
  - { type: LOCATION,             action: keep }
  - { type: GDPR_ART9_HEALTH,     action: block, reason: "Categoria particolare art. 9 GDPR" }
  - { type: GDPR_ART9_BIOMETRIC,  action: block, reason: "Categoria particolare art. 9 GDPR" }
  - { type: GDPR_ART9_GENETIC,    action: block, reason: "Categoria particolare art. 9 GDPR" }
  - { type: GDPR_ART9_RELIGION,   action: block, reason: "Categoria particolare art. 9 GDPR" }
  - { type: GDPR_ART9_POLITICAL,  action: block, reason: "Categoria particolare art. 9 GDPR" }
  - { type: GDPR_ART9_SEXUAL,     action: block, reason: "Categoria particolare art. 9 GDPR" }
  - { type: GDPR_ART9_UNION,      action: block, reason: "Categoria particolare art. 9 GDPR" }

default_action: pseudonymize

thresholds:
  review_below_confidence: 0.75
  block_on_detection_error: true
  fail_closed: true

pseudonymization:
  salt_scope: document
  vault:
    enabled: true
    ttl_days: 90

retention:
  audit_log_days: 2555

egress:
  sealed: true
"""


def load_policy(path: Path | str | None = None) -> Policy:
    """Carica una policy da file. Senza argomento restituisce il profilo predefinito."""
    if path is None:
        raw = yaml.safe_load(DEFAULT_POLICY_YAML)
        source = None
    else:
        source = Path(path).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"File di policy non trovato: {source}")
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}

    return _build(raw, source)


def _build(raw: dict, source: Path | None) -> Policy:
    known = {e.value for e in EntityType}
    rules: dict[str, EntityRule] = {}

    for item in raw.get("entities", []) or []:
        etype = item.get("type")
        if etype not in known:
            raise ValueError(
                f"Tipo di entità sconosciuto nella policy: {etype!r}. "
                f"Tipi ammessi: {', '.join(sorted(known))}"
            )
        rules[etype] = EntityRule(
            entity_type=etype,
            action=Action(item.get("action", "pseudonymize")),
            generalize_to=item.get("to"),
            reason=item.get("reason"),
            min_confidence=float(item.get("min_confidence", 0.0)),
        )

    thresholds = raw.get("thresholds", {}) or {}
    pseudo = raw.get("pseudonymization", {}) or {}
    vault = pseudo.get("vault", {}) or {}
    retention = raw.get("retention", {}) or {}
    egress = raw.get("egress", {}) or {}

    return Policy(
        version=int(raw.get("version", 1)),
        profile=raw.get("profile", "custom"),
        mode=raw.get("mode", "pseudonymize"),
        rules=rules,
        default_action=Action(raw.get("default_action", "pseudonymize")),
        review_below_confidence=float(thresholds.get("review_below_confidence", 0.75)),
        block_on_detection_error=bool(thresholds.get("block_on_detection_error", True)),
        fail_closed=bool(thresholds.get("fail_closed", True)),
        salt_scope=pseudo.get("salt_scope", "document"),
        vault_enabled=bool(vault.get("enabled", True)),
        vault_ttl_days=int(vault.get("ttl_days", 90)),
        audit_retention_days=int(retention.get("audit_log_days", 2555)),
        sealed=bool(egress.get("sealed", True)),
        source_path=source,
        raw=raw,
    )


@dataclass
class PolicyOutcome:
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    blocked_entities: list[str] = field(default_factory=list)
    review_entities: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "reasons": self.reasons,
            "blocked_entities": sorted(set(self.blocked_entities)),
            "review_entities": sorted(set(self.review_entities)),
        }


class PolicyEngine:
    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or load_policy()

    def evaluate(
        self,
        spans: list[Span],
        *,
        ocr_confidence: float | None = None,
        detection_error: bool = False,
    ) -> PolicyOutcome:
        """Decide l'esito per il documento a partire dagli span rilevati."""
        outcome = PolicyOutcome(decision=Decision.ALLOW)

        if detection_error:
            if self.policy.block_on_detection_error:
                outcome.decision = Decision.BLOCK
                outcome.reasons.append(
                    "Errore durante il rilevamento e policy fail-closed: documento bloccato."
                )
                return outcome
            outcome.decision = Decision.REVIEW
            outcome.reasons.append("Errore durante il rilevamento: documento inviato a revisione.")

        for span in spans:
            rule = self.policy.rule_for(span.entity_type)

            if rule.action is Action.BLOCK:
                outcome.decision = Decision.BLOCK
                outcome.blocked_entities.append(span.entity_type.value)
                reason = rule.reason or f"Entità {span.entity_type.value} soggetta a blocco"
                if reason not in outcome.reasons:
                    outcome.reasons.append(reason)
                continue

            if rule.action is Action.REVIEW:
                outcome.review_entities.append(span.entity_type.value)
                if outcome.decision is Decision.ALLOW:
                    outcome.decision = Decision.REVIEW
                continue

            if span.confidence < self.policy.review_below_confidence:
                outcome.review_entities.append(span.entity_type.value)
                if outcome.decision is Decision.ALLOW:
                    outcome.decision = Decision.REVIEW
                    outcome.reasons.append(
                        f"Rilevamento a bassa confidenza ({span.confidence:.2f} < "
                        f"{self.policy.review_below_confidence:.2f}) su {span.entity_type.value}."
                    )

        if ocr_confidence is not None and ocr_confidence < self.policy.review_below_confidence:
            if outcome.decision is Decision.ALLOW:
                outcome.decision = Decision.REVIEW
            outcome.reasons.append(
                f"Qualità OCR bassa ({ocr_confidence:.2f}): il rilevamento potrebbe "
                "aver perso entità presenti nel documento."
            )

        return outcome
