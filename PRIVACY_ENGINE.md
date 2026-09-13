# Privacy Engine — Anonimizzazione e Pseudonimizzazione Locale

> Progettazione del motore che protegge i dati estratti dall'OCR **prima** che raggiungano
> qualunque modello esterno. Tutto in locale, nessun byte in uscita, decisioni riproducibili.
>
> Documento di design. Complementare a [`FEASIBILITY.md`](./FEASIBILITY.md).
> **Autore:** Matteo Tomassetti (cmdhro) — **Data:** 27 luglio 2026 — **Stato:** proposta

---

## 1. Obiettivo e modello di minaccia

### 1.1 Cosa deve garantire

```
  ┌──────────────────────── CONFINE LOCALE (nessun egress) ────────────────────────┐
  │                                                                                │
  │   PDF ──▶ OCR ──▶ DETECT ──▶ PSEUDONIMIZZA ──▶ [TESTO SICURO] ─────────────────┼──▶ modello
  │    │                            │                                              │    di frontiera
  │    │                            ▼                                              │        │
  │    │                     vault cifrato                                         │        │
  │    │                     (token → valore)                                      │        │
  │    │                            │                                              │        │
  │  originale                      ▼                                              │        │
  │  mai spedito         RE-IDRATA ◀── risposta con token ◀────────────────────────┼────────┘
  │                            │                                                   │
  │                            ▼                                                   │
  │                     risposta completa                                          │
  └────────────────────────────────────────────────────────────────────────────────┘
```

Il modello di frontiera vede `⟦PER_7f3a⟧ ha presentato ricorso il ⟦DATE_2c81⟧`.
L'utente legge `Mario Rossi ha presentato ricorso il 14/03/2024`.
La mappatura non esiste al di fuori della macchina.

### 1.2 Modello di minaccia

| # | Avversario | Cosa può vedere | Difesa |
|---|---|---|---|
| M1 | Provider del modello di frontiera | Solo il prompt pseudonimizzato | Sostituzione pre-invio, vault locale |
| M2 | Intercettazione di rete | Traffico verso il provider | Come M1 + TLS del provider |
| M3 | Chi legge l'output archiviato (repo, Obsidian, NAS, backup) | I file `.md` prodotti | Output redatto di default; originale in area separata |
| M4 | Chi ottiene accesso al disco | Vault + originali | Vault cifrato, chiave in keychain OS, mai su disco in chiaro |
| M5 | **Il tool stesso che "telefona a casa"** | Tutto | **Zero-egress verificato in CI** (§6) |
| M6 | Re-identificazione per inferenza dal contesto residuo | Testo pseudonimizzato | Parzialmente mitigabile (§8) — **limite dichiarato** |

M6 è l'unico che non si risolve. Va dichiarato, non nascosto.

---

## 2. Anonimizzazione ≠ pseudonimizzazione

Questa distinzione non è pedanteria: sbagliarla rende il tool legalmente scorretto.

| | Pseudonimizzazione | Anonimizzazione |
|---|---|---|
| Definizione | GDPR **Art. 4(5)** | GDPR **Considerando 26** |
| Reversibile | Sì, con informazioni tenute separate | No, per definizione |
| È ancora dato personale? | **Sì** — GDPR si applica integralmente | **No** — GDPR non si applica |
| Criterio | Le info aggiuntive sono separate e protette | Deve resistere a *singling out*, *linkability*, *inference* (WP29 Op. 05/2014) |
| Difficoltà reale | Media | **Molto alta** |

**Conseguenza progettuale:** il tool offre due modalità e non confonde mai i nomi.

- **`pseudonymize`** (default) — token reversibile, vault cifrato. Il risultato **resta dato personale**. È la modalità corretta per il round-trip verso il modello di frontiera.
- **`anonymize`** — sostituzione irreversibile, nessun vault, più generalizzazione dei quasi-identificatori. Si avvicina all'anonimizzazione ma **il tool non deve dichiarare che il risultato è anonimo**: dichiara *cosa ha fatto*, non *quale status giuridico ha prodotto*.

> Formula da usare nella documentazione: *"applies irreversible substitution and quasi-identifier
> generalisation. Whether the result qualifies as anonymous data under Recital 26 depends on your
> context and must be assessed by you."*

Nota sul contesto normativo: la proposta di Digital Omnibus sui dati introdurrebbe la soglia dello
"sforzo sproporzionato" per la re-identificazione. **Non è in vigore** ed è contestata da EDPB ed
EDPS. Il tool non deve costruirci sopra assunzioni: deve solo **misurare e documentare** quanto è
stato rimosso, lasciando la qualificazione giuridica all'utente.

---

## 3. Il problema vero: fare PII detection su testo OCR

Questo è il punto tecnico centrale, ed è dove il progetto può contribuire qualcosa di nuovo.

Presidio, GLiNER e simili assumono **testo pulito**. L'output OCR non lo è. La letteratura è
esplicita: gli errori OCR — font stilizzati, scansioni degradate — sono **la causa primaria dei
mancati oscuramenti** (falsi negativi). Un falso negativo è una fuga di dati.

Quattro modi in cui l'OCR rompe il rilevamento:

### 3.1 Confusione di caratteri
`RSSMRA85M01H501Z` → `RSSMRA85MO1H5O1Z` (O al posto di 0). La regex non matcha. Il codice fiscale
passa in chiaro al modello.

Confusabili tipici: `0↔O↔Q↔D`, `1↔I↔l↔|`, `5↔S`, `8↔B`, `2↔Z`, `6↔G`, `rn↔m`, `cl↔d`, `vv↔w`.

### 3.2 Frammentazione di layout
Un nome spezzato su due colonne o due righe diventa due token separati. Il NER perde l'entità.
Il tuo pipeline è già avvantaggiato qui: la classificazione layout per pagina esiste già.

### 3.3 Tabelle
PII in celle (anagrafiche, buste paga, referti). Il testo linearizzato perde l'associazione
colonna→significato: `Rossi | 12/05/1980 | Fiuggi` senza intestazioni non dice al NER che la seconda
colonna è una data di nascita.

### 3.4 ⚠️ Il buco più grave: le immagini
Il repo ha già un commit `feat: embed visual pages as images in Obsidian notes`. **Un'immagine di
pagina incorporata contiene volti, firme, timbri e testo che la redazione testuale non tocca mai.**
Se quell'immagine finisce in un prompt multimodale, tutta la redazione testuale è inutile.

Deve essere trattato come un canale di prima classe, non come un caso limite.

---

## 4. Architettura del motore

Cinque stadi. Nessuno usa un modello di frontiera. Nessuno fa chiamate di rete.

```
  testo OCR + bbox + confidence
            │
    ┌───────▼────────┐  ① regex + checksum       deterministico, ~0 ms
    │  STRATO 1      │     CF, IBAN, P.IVA, carte, targhe, email, tel
    └───────┬────────┘
    ┌───────▼────────┐  ② recupero errori OCR    deterministico
    │  STRATO 1.5    │     checksum fallito → correzione confusabili
    └───────┬────────┘
    ┌───────▼────────┐  ③ NER locale             GLiNER-multi 209M, CPU
    │  STRATO 2      │     PERSONA, ORG, LUOGO, DATA, salute, ecc.
    └───────┬────────┘
    ┌───────▼────────┐  ④ adjudicazione          solo span ambigui (<5%)
    │  STRATO 3      │     LLM locale T=0 seed fisso — OPZIONALE
    └───────┬────────┘
    ┌───────▼────────┐  ⑤ redazione immagini     YuNet ~85 KB + bbox testo
    │  STRATO IMG    │     volti, firme, regioni con PII
    └───────┬────────┘
            ▼
    span identificati → POLICY → pseudonimizzazione / blocco / review
```

### 4.1 Strato 1 — regex con validazione checksum

Non è "solo regex". La validazione tramite checksum è ciò che rende questo strato **preciso quasi
quanto un umano** sugli identificatori strutturati:

| Entità | Validazione | Effetto sui falsi positivi |
|---|---|---|
| Codice Fiscale | Carattere di controllo CIN (algoritmo dispari/pari) | ~0 FP |
| Partita IVA | Checksum Luhn-variante su 11 cifre | ~0 FP |
| IBAN | ISO 7064 mod-97-10 | ~0 FP |
| Carta di credito | Luhn | ~0 FP |
| Tessera sanitaria / TEAM | Struttura 20 cifre + CF incorporato | basso |
| Targa | `AA000AA` + gazetteer province storiche | basso |

Presidio ha un `ItFiscalCodeRecognizer` ma la copertura italiana/EU si ferma lì. **Un set completo
di riconoscitori IT/EU con checksum è un contributo utile di per sé**, e volendo è donabile upstream
a Presidio — ottimo modo di farsi conoscere nella community senza dover competere.

### 4.2 Strato 1.5 — recupero degli errori OCR *(il pezzo originale)*

L'idea: **un checksum fallito non è un rifiuto, è un segnale di errore OCR.**

```
sequenza che ha la FORMA di un codice fiscale ma checksum invalido
        │
        ├─▶ identifica posizioni con caratteri confusabili
        ├─▶ genera correzioni candidate (limite: ≤ 6 posizioni ambigue → ≤ 3^6 varianti)
        │
        ├─▶ una sola variante valida il checksum  → è un CF reale con errore OCR
        │                                            REDIGI + registra la correzione
        ├─▶ più varianti valide                    → REDIGI (prudenza) + review
        └─▶ nessuna variante valida                → non è un CF, oppure OCR troppo degradato
                                                     profilo strict: review
```

Effetto: recupera esattamente la classe di falsi negativi che la letteratura indica come principale
causa di mancata redazione. Ed è **completamente deterministico** — verificabile, testabile, senza
modelli. Il numero di correzioni applicate finisce nel manifest come indicatore di qualità OCR.

### 4.3 Strato 2 — NER locale

`GLiNER-multi` (~209 M parametri, Apache-2.0, gira in CPU). Zero-shot su etichette arbitrarie:
si dichiarano le entità nella policy, senza fine-tuning.

Alternative interoperabili: Presidio + spaCy `it_core_news_sm` come backend opzionale.
**Integrare Presidio invece di competerci** è la scelta strategicamente corretta.

Le categorie particolari **GDPR Art. 9** (salute, dati genetici, biometrici, convinzioni religiose,
opinioni politiche, orientamento sessuale, appartenenza sindacale) sono una classe a sé, con default
**`BLOCK`, non `REDACT`**: un referto medico privato dei nomi resta un referto medico, e la sua
trasmissione a terzi richiede una base giuridica che il tool non può presumere.

### 4.4 Strato 3 — adjudicazione locale *(opzionale, spento di default)*

Solo per gli span che i primi strati marcano incerti. Un LLM **locale** piccolo
(Qwen3-0.6B / Gemma-3-1B via MLX o llama.cpp, ~0.5–1 GB), mai un modello di frontiera.

Vincoli non negoziabili:

- `temperature = 0`, seed fissato, hash del modello nel manifest
- Riceve **solo lo span più una finestra di contesto minima**, mai il documento
- Il suo verdetto non può mai *declassare* un rilevamento dello Strato 1 (checksum > LLM)

Argomento a favore dello spegnimento di default: **una decisione di redazione deve essere
riproducibile**. Stesso input + stessa policy + stessa versione ⇒ stesso output, sempre. Un
componente stocastico nel percorso di redazione indebolisce l'audit trail. Il determinismo qui è una
funzionalità di compliance, non una limitazione.

### 4.5 Strato immagini

- **Volti**: YuNet (OpenCV) — **~85 KB**, CPU, real-time. Perfetto per il vincolo "leggero".
- **Firme / timbri**: rilevatore di blob leggero o classificatore piccolo su regioni non testuali.
- **Testo nelle immagini**: si riusano le bbox già prodotte dall'OCR — le regioni corrispondenti agli
  span redatti vengono coperte nel rendering.

Comportamento di default quando la redazione è attiva: **non incorporare mai il raster originale**.
Si incorpora il rendering redatto, oppure niente. L'originale resta nell'area locale non condivisa.

---

## 5. Pseudonimizzazione e round-trip

### 5.1 Generazione dei token

```
token = "⟦" + TIPO + "_" + HMAC-SHA256(salt_documento, forma_canonica)[:4] + "⟧"
```

Proprietà:

- **Stabile nel documento** — "Mario Rossi", "M. Rossi" e "ROSSI Mario" si normalizzano alla stessa
  forma canonica e ricevono lo stesso token. Il modello capisce che è la stessa persona.
- **Non stabile tra documenti, di default.** Salt per documento. Token stabili su un intero corpus
  permettono il *linkage* tra documenti, che è di per sé un rischio di re-identificazione. La
  stabilità di corpus è opt-in esplicito (`salt_scope: corpus`), da usare consapevolmente.
- **Deterministico** — stesso salt, stessa entità ⇒ stesso token. Sempre.
- Collisioni: 4 esadecimali = 65 536 valori per tipo per documento; alla collisione si estende a 6.

Formato dei delimitatori: `⟦…⟧` (U+27E6/27E7) di default perché non compare praticamente mai in
testo naturale; fallback ASCII `[[…]]` per i modelli che normalizzano male l'unicode.

### 5.2 Il vault

- Cifratura **XChaCha20-Poly1305** (via `age` o libsodium)
- Chiave in **Keychain macOS** / `secret-service` su Linux; mai su disco in chiaro
- File separato dall'output, escluso da git e dai backup per default
- TTL configurabile (default 90 giorni), cancellazione crittografica alla scadenza
- `anton-ocr forget <uuid>` distrugge la chiave del documento → i token diventano irreversibili:
  è il percorso pulito per l'**Art. 17 GDPR (diritto alla cancellazione)**

### 5.3 Re-idratazione e i suoi modi di fallire

Il modello restituisce testo contenente i token; localmente si sostituiscono con i valori reali.
Tre modi di fallire, tutti da gestire esplicitamente:

| Fallimento | Sintomo | Gestione |
|---|---|---|
| Il modello parafrasa il token | `⟦PER_7f3a⟧` → "la persona indicata" | Istruzione di sistema: preservare i token verbatim. Validare in uscita e avvisare. |
| Il modello **inventa** un token | `⟦PER_9999⟧` mai emesso | Token sconosciuto ⇒ **non sostituire**, lasciare letterale, segnalare. Mai indovinare. |
| Il modello scompone il token | `⟦PER_ 7f3a⟧` | Matching tollerante agli spazi in fase di re-idratazione, con log |

Va inoltre esposto un contatore: *token emessi / token restituiti*. Una discrepanza ampia significa
che la risposta è meno affidabile di quanto sembri, e l'utente deve saperlo.

### 5.4 Un avvertimento onesto

Il round-trip protegge dalla **trasmissione** dei dati personali. Non protegge dall'**inferenza**.

`⟦PER_7f3a⟧, direttore sanitario di ⟦ORG_2c81⟧ a Fiuggi, nel 2019...` — il numero di persone che
soddisfano questa descrizione può essere uno. La pseudonimizzazione dei nomi non risolve il problema
dei quasi-identificatori. Vedi §8.

---

## 6. Garanzia di località: dimostrarla, non prometterla

"Gira in locale" è un'affermazione che tutti fanno. Va resa verificabile.

1. **Test CI zero-egress.** La pipeline completa viene eseguita in un network namespace senza rotta
   (`unshare -rn`). Se un qualunque componente tenta una connessione, il test fallisce. Questo è il
   badge che conta.
2. **Separazione del download modelli.** `anton-ocr fetch-models` è l'*unico* comando autorizzato a
   usare la rete. Il percorso di elaborazione non costruisce mai un client HTTP.
3. **Modalità `--sealed`.** Rifiuta di partire se i modelli non sono già presenti in cache; nessun
   fallback di rete, in nessuna circostanza.
4. **Audit delle dipendenze.** Versioni pinnate, nessun pacchetto con telemetria, SBOM generato in
   CI. In un tool di privacy, una dipendenza che fa analytics è una vulnerabilità.
5. **Il manifest lo registra.** `"egress": "none"`, `"sealed_mode": true`, `"models_source": "local-cache"`.

> Un `README` che dice *"il test CI fallisce se qualsiasi componente apre un socket"* è
> immensamente più credibile di uno che dice *"100% local, no cloud"*. È la differenza tra una
> promessa e una prova — ed è esattamente il tipo di dettaglio che fa girare un progetto.

---

## 7. Configurazione

```yaml
# policy.yaml
version: 1
profile: gdpr-strict
mode: pseudonymize          # pseudonymize | anonymize

detection:
  layers: [regex, ocr_recovery, ner]   # 'llm_adjudication' opt-in
  ocr_recovery:
    enabled: true
    max_ambiguous_positions: 6
  ner:
    backend: gliner          # gliner | presidio | both
    model: urchade/gliner_multi-v2.1
  images:
    faces: true              # YuNet
    signatures: true
    embed_original: false    # mai il raster originale con redazione attiva

entities:
  - { type: IT_CODICE_FISCALE, action: pseudonymize, validate: checksum }
  - { type: IBAN,              action: pseudonymize, validate: mod97 }
  - { type: IT_PARTITA_IVA,    action: pseudonymize, validate: checksum }
  - { type: PERSON,            action: pseudonymize }
  - { type: EMAIL,             action: pseudonymize }
  - { type: PHONE,             action: pseudonymize }
  - { type: ADDRESS,           action: generalize, to: municipality }
  - { type: DATE_OF_BIRTH,     action: generalize, to: year }
  - { type: GDPR_ART9_HEALTH,  action: block, reason: "Categoria particolare Art. 9" }
  - { type: GDPR_ART9_BIOMETRIC, action: block }

pseudonymization:
  salt_scope: document       # document | corpus
  token_format: "⟦{TYPE}_{HASH4}⟧"
  vault: { enabled: true, ttl_days: 90, key_source: os_keychain }

thresholds:
  review_below_confidence: 0.75
  block_on_detection_error: true    # fail-closed

egress:
  sealed: true
```

**`block_on_detection_error: true` è la scelta di default corretta.** Se il rilevamento fallisce, il
documento non passa. Fail-closed, non fail-open: in un sistema di privacy, degradare silenziosamente
significa perdere dati.

---

## 8. Cosa questo motore NON risolve

Sezione obbligatoria. La credibilità di un tool di privacy si misura sulla franchezza dei suoi
limiti, e chi promette recall perfetto viene smontato pubblicamente entro una settimana dal lancio.

| Limite | Perché | Mitigazione parziale |
|---|---|---|
| **Recall non è 100%** | Nessun sistema PII lo raggiunge. OCR degradato lo peggiora. | Misurarlo e pubblicare i numeri per tipo di entità. Modalità `review` come default in strict. |
| **Quasi-identificatori** | Età + comune + professione + data possono identificare univocamente. Rimuovere i nomi non basta. | Generalizzazione (data→anno, indirizzo→comune), punteggio di rischio da esporre. Non è k-anonimato. |
| **Re-identificazione per inferenza** | Un modello capace può dedurre l'identità dal contesto residuo. | Profilo `aggressive` che generalizza di più, a costo dell'utilità del testo. |
| **PII non strutturata in prosa** | "il figlio del sindaco di Fiuggi" non è un'entità nominata. | Fuori portata. Dichiararlo. |
| **Documenti manoscritti** | Recall OCR crolla, e con esso il rilevamento. | Confidence score basso → `review` obbligatorio. |
| **Lingue non coperte** | Riconoscitori con checksum e gazetteer sono IT/EU-specifici. | Dichiarare la copertura per lingua nel README. |
| **Il tool non rende conformi** | Produce artefatti di evidenza, non conformità. | Dichiarazione esplicita in README, `--help` e manifest. |

---

## 9. Misurare: il benchmark che manca

Non esiste un benchmark pubblico di PII detection su testo **degradato dall'OCR** per l'italiano.
Costruirlo è fattibile, è utile e non ha problemi di privacy perché è **interamente sintetico**.

**`PII-OCR-IT` — protocollo:**

1. Generare documenti italiani realistici con PII in posizioni note: referti, buste paga, contratti,
   certificati anagrafici, fatture, verbali.
2. Renderizzare in PDF con font, layout e strutture di tabella variabili.
3. Degradare a tre livelli: `clean` (PDF nativo) · `scan-300dpi` · `scan-150dpi + skew + rumore`.
4. Passare attraverso la pipeline OCR reale.
5. Misurare **recall** (che è la metrica che conta: un falso negativo è una fuga) e **precision**
   (che conta per l'usabilità), per tipo di entità e per livello di degrado.
6. Confrontare: regex-only · +recupero OCR · +NER · Presidio baseline.

Ricadute: numeri onesti nel README, la dimostrazione quantificata del valore dello Strato 1.5,
un artefatto riutilizzabile dalla community, e **materiale da tesi immediatamente pubblicabile**.

Con ogni probabilità è la parte del progetto con il più alto rapporto impatto/sforzo.

---

## 10. Roadmap implementativa

Sostituisce e dettaglia la Fase 3 di `FEASIBILITY.md`.

| # | Passo | Sforzo | Dipendenze |
|---|---|---|---|
| 1 | Riconoscitori IT/EU con checksum (CF, P.IVA, IBAN, carte, targhe, TS) | 8 h | — |
| 2 | Recupero errori OCR sui confusabili (Strato 1.5) | 6 h | 1 |
| 3 | Corpus sintetico `PII-OCR-IT` + harness di valutazione | 12 h | 1 |
| 4 | Integrazione GLiNER + backend Presidio opzionale | 8 h | — |
| 5 | Tassonomia Art. 9 + azione `block` | 4 h | 4 |
| 6 | Motore token + vault cifrato (keychain OS) | 10 h | — |
| 7 | Re-idratazione + validazione + contatori | 6 h | 6 |
| 8 | Redazione immagini (YuNet + bbox) | 8 h | — |
| 9 | Motore di policy (`policy.yaml`) | 8 h | 1,4,6 |
| 10 | Test CI zero-egress + modalità sealed | 4 h | — |
| 11 | Coda di review (Art. 14) | 6 h | 9 |
| 12 | `forget` / TTL / cancellazione crittografica | 4 h | 6 |

**Totale ≈ 84 h.**

Ordine consigliato: **1 → 2 → 3** prima di tutto il resto. Costruire il benchmark presto significa
che ogni passo successivo si misura invece di essere solo dichiarato — ed è ciò che distingue un
progetto serio da una demo.

---

## Fonti

- [An Evaluation Study of Hybrid Methods for Multilingual PII Detection — arXiv](https://arxiv.org/html/2510.07551v1)
- [A Comparative Study of Light-weight Language Models for PII Masking — arXiv](https://arxiv.org/html/2512.18608v1)
- [Automated Redaction of PII Using OCR and LLMs (errori OCR come causa primaria di mancata redazione) — MDPI](https://www.mdpi.com/2076-3417/15/9/4923)
- [When Good OCR Is Not Enough: Benchmarking OCR Robustness for RAG — arXiv](https://arxiv.org/html/2605.00911v1)
- [Presidio — predefined recognizers (`ItFiscalCodeRecognizer`)](https://tessl.io/registry/tessl/pypi-presidio-analyzer/2.2.0/files/docs/predefined-recognizers.md)
- [Presidio — adding custom recognizers](https://microsoft.github.io/presidio/analyzer/adding_recognizers/)
- [PII Redaction Pipeline for LLM Workloads (2026 Architecture)](https://appscale.blog/en/blog/pii-redaction-pipeline-llm-presidio-ner-reversible-tokenisation-2026)
- [Digital Omnibus: EU DPAs reject many proposed changes to the GDPR — noyb](https://noyb.eu/en/digital-omnibus-eu-dpas-reject-many-proposed-changes-gdpr)
- [EU Digital Omnibus amendments to GDPR to facilitate AI training miss the mark — IAPP](https://iapp.org/news/a/eu-digital-omnibus-amendments-to-gdpr-to-facilitate-ai-training-miss-the-mark)

---

> ⚖️ **Disclaimer.** Documento tecnico, non consulenza legale. La qualificazione giuridica del
> risultato (dato pseudonimizzato vs anonimo) dipende dal contesto d'uso e va valutata da un
> professionista qualificato.
