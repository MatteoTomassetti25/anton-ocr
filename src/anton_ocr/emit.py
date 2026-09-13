"""Resa del documento sicuro in Markdown.

Il file prodotto è l'artefatto che si consegna all'agente di frontiera. È
autoconsistente per una ragione pratica: chi lo incolla in una chat non porta con
sé il manifest, l'audit log o la documentazione. Tutto ciò che serve per usarlo
correttamente deve stare dentro il file.

Struttura:

    ---                          ← frontmatter YAML: provenienza e decisione
    anton_ocr: {...}
    ---
    > istruzione per l'agente    ← preservare i token verbatim
    ## Dati rimossi              ← solo conteggi, mai valori
    ---
    <testo pseudonimizzato>
    ---
    <limiti + come re-idratare>

Il frontmatter è YAML valido, quindi Obsidian, Jekyll e la maggior parte dei
parser Markdown lo leggono come metadato invece di mostrarlo come testo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

from anton_ocr.privacy.entities import EntityType
from anton_ocr.privacy.rehydrate import SYSTEM_PROMPT_HINT

# Etichette leggibili per la legenda. L'utente non deve dover conoscere i nomi
# interni delle entità per capire cosa è stato tolto.
LABELS: dict[str, str] = {
    "IT_CODICE_FISCALE": "Codici fiscali",
    "IT_PARTITA_IVA": "Partite IVA",
    "IT_TESSERA_SANITARIA": "Tessere sanitarie",
    "IBAN": "Coordinate bancarie (IBAN)",
    "CREDIT_CARD": "Carte di pagamento",
    "IT_TARGA": "Targhe veicoli",
    "EMAIL": "Indirizzi email",
    "PHONE": "Numeri di telefono",
    "IP_ADDRESS": "Indirizzi IP",
    "MAC_ADDRESS": "Indirizzi MAC",
    "PERSON": "Nomi di persona",
    "ORGANIZATION": "Organizzazioni",
    "ADDRESS": "Indirizzi",
    "LOCATION": "Località",
    "DATE": "Date",
    "DATE_OF_BIRTH": "Date di nascita",
    "GDPR_ART9_HEALTH": "Dati sulla salute (art. 9)",
    "GDPR_ART9_BIOMETRIC": "Dati biometrici (art. 9)",
    "GDPR_ART9_GENETIC": "Dati genetici (art. 9)",
    "GDPR_ART9_RELIGION": "Convinzioni religiose (art. 9)",
    "GDPR_ART9_POLITICAL": "Opinioni politiche (art. 9)",
    "GDPR_ART9_SEXUAL": "Orientamento sessuale (art. 9)",
    "GDPR_ART9_UNION": "Appartenenza sindacale (art. 9)",
}

PREFIXES: dict[str, str] = {
    "IT_CODICE_FISCALE": "CF",
    "IT_PARTITA_IVA": "PIVA",
    "IT_TESSERA_SANITARIA": "TS",
    "IBAN": "IBAN",
    "CREDIT_CARD": "CARD",
    "IT_TARGA": "TARGA",
    "EMAIL": "EML",
    "PHONE": "TEL",
    "IP_ADDRESS": "IP",
    "MAC_ADDRESS": "MAC",
    "PERSON": "PER",
    "ORGANIZATION": "ORG",
    "ADDRESS": "ADR",
    "LOCATION": "LOC",
    "DATE": "DAT",
    "DATE_OF_BIRTH": "DOB",
}

ACTION_LABELS = {
    "pseudonymize": "sostituito con token",
    "generalize": "reso meno preciso",
    "keep": "lasciato invariato",
    "block": "bloccato",
    "review": "in revisione",
}


@dataclass
class SafeDocument:
    """Il documento sicuro, pronto per l'agente."""

    markdown: str
    doc_uuid: str
    decision: str
    token_count: int

    def __str__(self) -> str:  # pragma: no cover - comodità
        return self.markdown


def _frontmatter(result, policy) -> str:
    manifest = result.manifest
    data = {
        "anton_ocr": {
            "version": manifest.pipeline.get("tool_version"),
            "doc_uuid": result.doc_uuid,
            "source": manifest.document.source_name,
            "source_sha256": manifest.document.source_sha256,
            "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "decision": result.decision.value,
            "mode": policy.mode,
            "policy_profile": policy.profile,
            "policy_sha256": policy.sha256[:16],
            "tokens_issued": len(result.mapping),
            "ocr_confidence": round(result.ocr_confidence, 3),
            "egress": "none",
            "content_origin": "machine-generated",
        }
    }
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{body}---\n"


def _agent_instruction() -> str:
    return (
        "> [!IMPORTANT] Istruzione per l'agente\n"
        f"> {SYSTEM_PROMPT_HINT}\n"
        ">\n"
        "> Non tentare di dedurre i valori reali dietro i segnaposto e non chiedere "
        "all'utente di rivelarli.\n"
    )


def _legend(result, policy) -> str:
    """Legenda delle rimozioni. **Solo conteggi: mai i valori.**

    Scrivere qui anche un solo valore originale vanificherebbe tutto il resto.
    """
    counts = result.screen.by_type()
    if not counts:
        return (
            "## Dati rimossi\n\n"
            "Nessun dato personale rilevato in questo documento.\n\n"
            "> Attenzione: nessun rilevamento non significa nessun dato presente. "
            "Il recall non è del 100%, in particolare su testo con qualità OCR bassa.\n"
        )

    rows = ["| Categoria | Occorrenze | Trattamento | Segnaposto |", "|---|---|---|---|"]
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        try:
            rule = policy.rule_for(EntityType(name))
            action_value = rule.action.value
        except ValueError:  # pragma: no cover - tipo non mappato
            action_value = "pseudonymize"
        action = ACTION_LABELS.get(action_value, action_value)

        # Solo la pseudonimizzazione emette un token. Mostrare un segnaposto
        # accanto a "lasciato invariato" o "reso meno preciso" farebbe credere
        # che il dato sia stato sostituito, quando invece è ancora nel testo.
        prefix = PREFIXES.get(name)
        if action_value == "pseudonymize" and prefix:
            placeholder = f"`⟦{prefix}_····⟧`"
        elif action_value == "generalize":
            placeholder = "*valore ridotto*"
        elif action_value == "keep":
            placeholder = "*ancora nel testo*"
        else:
            placeholder = "—"

        rows.append(f"| {LABELS.get(name, name)} | {count} | {action} | {placeholder} |")

    recovered = result.screen.stats.get("ocr_recovered", 0)
    note = ""
    if recovered:
        note = (
            f"\n{recovered} identificatore/i era/no danneggiato/i dall'OCR ed è/sono stato/i "
            "ricostruito/i tramite checksum prima della sostituzione.\n"
        )

    return "## Dati rimossi\n\n" + "\n".join(rows) + "\n" + note


def _warnings(result) -> str:
    lines: list[str] = []
    stats = result.screen.stats

    # La più importante di tutte: senza NER i nomi di persona non vengono
    # rilevati. Un documento che *sembra* sicuro è peggio di uno palesemente
    # non trattato, perché induce a fidarsi.
    if not stats.get("ner_enabled"):
        lines.append(
            "- 🔴 **Riconoscimento dei nomi non attivo.** Nomi di persona, organizzazioni e "
            "categorie particolari (art. 9 GDPR) **non sono stati cercati** e con ogni "
            "probabilità sono ancora presenti nel testo qui sotto. Sono stati trattati solo "
            "gli identificatori strutturati (codice fiscale, IBAN, email, telefono…). "
            "Per attivarlo: `pip install 'anton-ocr[ner]'` e `NER_BACKEND=gliner` in `config.env`."
        )
    elif stats.get("ner_unavailable"):
        lines.append(
            "- 🔴 **Il riconoscimento dei nomi era attivo ma non ha funzionato** "
            f"({stats['ner_unavailable']}). Nomi e organizzazioni potrebbero essere "
            "rimasti in chiaro."
        )

    if result.ocr_confidence < 0.75:
        lines.append(
            f"- ⚠️ **Qualità del testo bassa ({result.ocr_confidence:.2f}).** Il rilevamento "
            "potrebbe aver perso dati personali presenti nell'originale."
        )
    if result.pages_below_threshold:
        pagine = ", ".join(str(p) for p in result.pages_below_threshold[:10])
        lines.append(f"- ⚠️ Pagine sotto soglia di qualità: {pagine}")
    if stats.get("ocr_recovered_ambiguous"):
        lines.append(
            f"- ⚠️ {stats['ocr_recovered_ambiguous']} recupero/i OCR ambiguo/i: "
            "l'identificatore è stato sostituito ma il valore ricostruito non è certo."
        )
    if result.decision.value == "review":
        lines.append("- ⚠️ Documento marcato per **revisione umana**: verifica prima dell'uso.")

    if not lines:
        return ""
    return "## Avvertenze\n\n" + "\n".join(lines) + "\n"


def _footer(result, policy) -> str:
    reversible = policy.mode == "pseudonymize"
    rehydrate_block = (
        f"```bash\nanton-ocr rehydrate {result.doc_uuid} risposta.txt\n```\n"
        if reversible
        else "Modalità irreversibile: non esiste alcuna mappatura da ripristinare.\n"
    )

    return (
        "## Come usare questo file\n\n"
        "1. Consegna **questo file** all'agente, non l'originale.\n"
        "2. L'agente lavora sui segnaposto e li riporta invariati nella risposta.\n"
        "3. Riporta i valori reali in locale:\n\n"
        f"{rehydrate_block}\n"
        "## Limiti\n\n"
        "- Il rilevamento non ha recall del 100%: alcuni dati possono essere sfuggiti.\n"
        "- La pseudonimizzazione **non impedisce la re-identificazione per inferenza** dal "
        "contesto residuo (età, ruolo, luogo, date in combinazione).\n"
        "- Il dato pseudonimizzato **resta dato personale** ai sensi dell'art. 4(5) GDPR.\n"
        "- Il testo contenuto in eventuali immagini non è coperto dalla redazione testuale.\n\n"
        "---\n\n"
        f"*Prodotto da anton-ocr {result.manifest.pipeline.get('tool_version')} — elaborazione "
        "interamente locale, nessun dato inviato in rete. Questo file è un artefatto di "
        "evidenza tecnica: non costituisce una valutazione di conformità né consulenza legale.*\n"
    )


def render_safe_markdown(result, policy) -> SafeDocument:
    """Compone il documento sicuro a partire dall'esito della pipeline."""
    if result.decision.value == "block":
        return _render_blocked(result, policy)

    title = result.manifest.document.source_name.rsplit(".", 1)[0]

    sections = [
        _frontmatter(result, policy),
        f"\n# {title}\n",
        f"\n{_agent_instruction()}",
        f"\n{_legend(result, policy)}",
    ]

    warnings = _warnings(result)
    if warnings:
        sections.append(f"\n{warnings}")

    sections.append("\n---\n\n## Contenuto\n\n")
    sections.append(result.safe_text.strip() + "\n")
    sections.append(f"\n---\n\n{_footer(result, policy)}")

    return SafeDocument(
        markdown="".join(sections),
        doc_uuid=result.doc_uuid,
        decision=result.decision.value,
        token_count=len(result.mapping),
    )


def _render_blocked(result, policy) -> SafeDocument:
    """Documento bloccato: si scrive il motivo, **mai il contenuto**.

    Scrivere il testo qui, anche redatto, contraddirebbe la decisione di blocco.
    """
    details = result.manifest.decision_details
    reasons = details.get("reasons", [])
    blocked = details.get("blocked_entities", [])

    body = [
        _frontmatter(result, policy),
        f"\n# {result.manifest.document.source_name} — BLOCCATO\n\n",
        "> [!CAUTION]\n"
        "> Questo documento **non è stato reso disponibile** per l'invio a un agente.\n"
        "> Il contenuto non è riportato in questo file.\n\n",
        "## Motivo\n\n",
    ]
    body.extend(f"- {reason}\n" for reason in reasons)

    if blocked:
        etichette = ", ".join(LABELS.get(b, b) for b in blocked)
        body.append(f"\n**Categorie che hanno determinato il blocco:** {etichette}\n")

    body.append(
        "\n## Cosa puoi fare\n\n"
        "- Verificare che il rilevamento sia corretto: `anton-ocr scan <file>`\n"
        "- Se il trattamento ha una base giuridica adeguata, adeguare `policy.yaml` "
        "**consapevolmente** e documentare la decisione.\n"
        "- Le categorie particolari (art. 9 GDPR) richiedono una base giuridica dedicata: "
        "il blocco predefinito esiste perché lo strumento non può presumerla.\n\n"
        f"UUID documento: `{result.doc_uuid}`\n"
    )

    return SafeDocument(
        markdown="".join(body),
        doc_uuid=result.doc_uuid,
        decision="block",
        token_count=0,
    )
