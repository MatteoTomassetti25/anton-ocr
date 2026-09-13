# Studio di Fattibilità — anton-ocr → Compliance-First Document Ingestion

> Analisi tecnica, legislativa e di posizionamento per la trasformazione di `anton-ocr`
> da daemon OCR personale a tool open source di riferimento per l'immissione
> conforme di documenti in pipeline AI.
>
> **Autore:** Matteo Tomassetti (cmdhro) — **Data:** 27 luglio 2026 — **Stato:** proposta, non implementato

---

## 0. TL;DR

| Domanda | Risposta |
|---|---|
| È fattibile? | **Sì.** L'architettura v3 esistente (tiering per pagina, MLX, async) è già il 60% del lavoro. Manca il layer di compliance, non il motore OCR. |
| Il timing è giusto? | **Sì, ma non per il motivo che pensi.** Le regole high-risk sono slittate al 2027/2028. Ciò che scatta il **2 agosto 2026 (fra 6 giorni)** è l'Art. 50 (trasparenza). La finestra utile è di ~17 mesi, non di 6 giorni: è un vantaggio, non una perdita. |
| Serve hardware grosso? | **No.** Lo stack proposto sta sotto i **2 GB di footprint** e gira su Raspberry Pi 5. È il tuo argomento di vendita più forte. |
| Cosa manca per pubblicare? | 9 blocker (§6), di cui 3 gravi: **manca il file LICENSE**, il **README descrive un'architettura che non esiste più**, `config.env` **espone path personali**. |
| Rischio principale | Over-claiming. Un tool non rende nessuno "AI Act compliant". Vende **evidenza di conformità**, non conformità. Vedi §8. |

---

## 1. Quadro legislativo aggiornato (luglio 2026)

### 1.1 Cosa è realmente cambiato

Il **Digital Omnibus on AI** è stato adottato dal Parlamento il **16 giugno 2026** e dal Consiglio il
**29 giugno 2026**, con entrata in vigore a luglio 2026. Ha modificato l'AI Act in modo mirato:

| Obbligo | Data originale | Data attuale |
|---|---|---|
| Sistemi high-risk **Allegato III** (stand-alone) | 2 ago 2026 | **2 dic 2027** |
| Sistemi high-risk **Allegato I** (embedded in prodotti regolati) | 2 ago 2027 | **2 ago 2028** |
| **Art. 50 — trasparenza** (disclosure interazione AI, marcatura contenuti) | 2 ago 2026 | **2 ago 2026 — NON differito** |
| Art. 50(2) marcatura machine-readable, sistemi già sul mercato | — | grace period fino al **2 dic 2026** |
| GPAI (modelli general-purpose) | 2 ago 2025 | in vigore |

Motivo del rinvio: ritardo nella designazione delle autorità nazionali competenti e nella
finalizzazione degli standard armonizzati. L'Omnibus ha anche introdotto in Art. 5 un nuovo
divieto su NCII generata da AI e CSAM.

**Il pacchetto "Digital Omnibus" su dati/GDPR è separato e NON è ancora in vigore.** Le proposte
più rilevanti per noi — la riclassificazione dei dati pseudonimizzati fuori dall'ambito GDPR quando
la re-identificazione richiede "sforzo sproporzionato", e il nuovo Art. 88c (legittimo interesse per
training/testing/validazione di sistemi AI) — sono in negoziazione e sono state **contestate da EDPB,
EDPS e noyb** proprio sulla vaghezza dello standard "sforzo sproporzionato".

### 1.2 Perché questo è una buona notizia per il progetto

Tre implicazioni strategiche:

1. **Hai 17 mesi di runway invece di 6 giorni.** Un progetto lanciato a settembre 2026 arriva maturo,
   testato e con community quando i budget di compliance si aprono davvero (Q2–Q3 2027).
2. **Il differimento non ha cancellato la domanda, l'ha spostata in fase pilota.** Le aziende stanno
   facendo proof-of-concept adesso, con tooling che non esiste ancora. È esattamente la finestra in
   cui un progetto OSS può diventare lo standard de facto.
3. **La pseudonimizzazione è il campo di battaglia normativo.** Se l'Art. 88c passa così com'è, il
   valore di uno strumento che *documenta* rigorosamente il grado di pseudonimizzazione applicato
   sale, perché sposta l'onere della prova. Se non passa, sale ancora di più. **In entrambi gli
   scenari il tool guadagna rilevanza** — questa è una rara asimmetria favorevole.

### 1.3 Dove si colloca esattamente anton-ocr nell'AI Act

Questo è il punto più importante di tutto il documento, ed è il punto su cui la maggior parte dei
progetti simili sbaglia.

**anton-ocr non è un sistema AI ad alto rischio.** È un tool di preparazione dati a monte. Non prende
decisioni su persone. Non è nemmeno un "AI system" ai sensi dell'Art. 3(1) nella maggior parte degli
usi (è un pre-processore deterministico che *usa* un modello).

Il suo valore è che rende possibile ai suoi utenti — che *sì* possono essere provider o deployer di
sistemi high-risk — di soddisfare obblighi che altrimenti dovrebbero implementare da zero:

| Norma | Obbligo | Cosa può fornire il tool |
|---|---|---|
| **AI Act Art. 10** | Data governance: provenienza, qualità, rappresentatività dei dataset documentate | Manifest firmato per documento: hash sorgente, pipeline applicata, modello + versione + hash pesi, parametri, esito per pagina |
| **AI Act Art. 12** | Logging automatico degli eventi per tutta la vita del sistema | Audit log append-only (`.audit.jsonl`), timestamped, con event schema stabile |
| **AI Act Art. 11 / All. IV** | Documentazione tecnica | Export automatico della sezione "data preparation" della doc tecnica |
| **AI Act Art. 14** | Sorveglianza umana | Review queue per pagine sotto soglia di confidenza |
| **AI Act Art. 15** | Accuratezza, robustezza | Confidence score per pagina + benchmark riproducibile |
| **AI Act Art. 26** | Obblighi del deployer, incl. conservazione log ≥6 mesi | Retention policy configurabile, log esportabili |
| **AI Act Art. 50** | Trasparenza / marcatura contenuti generati o manipolati da AI | Marcatura dell'output OCR come *machine-generated*, opzionalmente via **C2PA Content Credentials** |
| **GDPR Art. 5(1)(c)** | Minimizzazione | Redazione/pseudonimizzazione PII prima dell'ingestione nell'agente |
| **GDPR Art. 9** | Categorie particolari di dati | Rilevamento e blocco/quarantena a livello di policy |
| **GDPR Art. 25** | Privacy by design/by default | Architettura local-first, zero egress, default restrittivi |
| **GDPR Art. 30** | Registro dei trattamenti | Derivabile dall'audit log |
| **GDPR Art. 32** | Sicurezza del trattamento | Cifratura del vault di de-tokenizzazione, no cloud |

> **Il pitch in una riga:** *"Il tuo agente AI ingerisce documenti. Chi può dimostrare cosa c'era
> dentro, cosa è stato tolto, con quale modello, quando e da chi? anton-ocr produce quella prova."*

---

## 2. Analisi dello stato attuale

### 2.1 Cosa c'è già ed è buono

Il codice v3 è tecnicamente più avanzato di quanto il README suggerisca. Punti di forza reali:

- **Tiering per pagina** (`classify_pages`) con routing a 4 task GLM-OCR (text/formula/table/visual).
  È la scelta architetturale giusta: la pagina con testo nativo non tocca mai il modello.
- **Doppio backend** MLX (Apple Silicon, Neural Engine + Metal) e Ollama (NVIDIA/CPU), con detection
  automatica dell'hardware.
- **Gestione memoria seria**: unload esplicito post-job, idle monitor a 300 s, chunked lazy loading,
  `gc.collect()` per chunk. Raro in progetti hobbisti.
- **Thread-safety MLX corretta**: executor single-thread dedicato perché lo stream Metal è per-thread.
  Questo è un dettaglio che il 90% dei progetti sbaglia.
- **Sanitizzazione output** (`_clean_ocr_output`, anti prompt-echo) e un normalizzatore di artefatti
  di font matematici Beamer/LaTeXiT sorprendentemente sofisticato.
- **Shutdown graceful** con drain della coda.
- Già emette un JSON di layout per documento — **è l'embrione del manifest**.

### 2.2 Gap tecnici (esclusa compliance)

| # | Gap | Impatto | Sforzo |
|---|---|---|---|
| T1 | **Nessun confidence score.** Non sai quali pagine sono venute male. `extracted = bool(text.strip())` è l'unico segnale. | Alto — blocca Art. 14 e Art. 15 | M |
| T2 | **Nessun retry / fallback**. Se GLM-OCR restituisce eco del prompt, la pagina viene silenziosamente persa. | Alto — perdita dati silenziosa | S |
| T3 | **Nessun test.** Zero. Nessuna suite, nessun fixture PDF. | Alto — blocker per contributi esterni | M |
| T4 | `_MLX_HF_MODEL` **hardcoded** nel sorgente, non in `config.env`. | Medio | XS |
| T5 | **Nessuna gestione dei limiti**: `MAX_PDF_PAGES=100` scarta il file invece di splittarlo. | Medio | S |
| T6 | **Perdita struttura**: l'output concatena pagine con `---`. Niente heading hierarchy, niente reading order multi-colonna, niente bounding box. | Alto per RAG a valle | M |
| T7 | **Nessuna dedup / idempotenza**: lo stesso PDF rielaborato produce un secondo output senza rilevare il duplicato. | Medio | S |
| T8 | Watchdog usa `time.sleep(1)` nel thread di callback e un polling di stabilità del file: funziona ma è fragile su NFS/SMB (rilevante: il tuo NAS Samba). | Medio | S |
| T9 | **Notifiche solo macOS**. Linux è "silent mode" per ammissione del README. | Basso | S |

### 2.3 Gap di compliance (il cuore del lavoro)

Attualmente **assenti al 100%**:

- Rilevamento e redazione PII
- Classificazione di sensibilità del documento
- Policy engine (cosa può/non può entrare nell'agente)
- Manifest di provenienza firmato
- Audit log append-only
- Retention / right-to-erasure
- Marcatura Art. 50 dell'output
- Human-in-the-loop review queue

---

## 3. Architettura proposta (v4)

### 3.1 Pipeline

```
                                    ┌── policy.yaml (dichiarativo, versionato)
                                    ▼
  ┌────────┐   ┌─────────┐   ┌───────────┐   ┌─────────┐   ┌────────┐   ┌────────┐
  │ INGEST │──▶│ CLASSIFY│──▶│  EXTRACT  │──▶│ SCREEN  │──▶│ REDACT │──▶│ ATTEST │
  └────────┘   └─────────┘   └───────────┘   └─────────┘   └────────┘   └────────┘
   SHA-256      layout        native / VLM     PII + cat.    pseudonim.   manifest
   doc UUID     per pagina    per pagina       art.9 GDPR    reversibile  firmato
   metadata     task routing  confidence       risk score    o hard-del
      │              │             │                │             │           │
      └──────────────┴─────────────┴────────────────┴─────────────┴───────────┘
                                   ▼
                        audit.jsonl (append-only, hash-chained)
                                   ▼
                    ┌──────────────┴──────────────┐
              ALLOW │              REVIEW         │ BLOCK
                    ▼                ▼            ▼
              output/*.md      review-queue/  quarantine/
              output/*.manifest.json
```

### 3.2 I cinque componenti nuovi

**① SCREEN + ② REDACT — motore di privacy**

> 📄 Progettazione completa in **[`PRIVACY_ENGINE.md`](./PRIVACY_ENGINE.md)**. Sintesi qui.

Rilevamento in strati, **interamente locale, nessun modello di frontiera coinvolto**:

1. **Regex + validazione checksum** — CF (carattere CIN), P.IVA, IBAN (mod-97), carte (Luhn), targhe.
   Deterministico, falsi positivi ≈ 0.
2. **Recupero errori OCR** — un checksum fallito non è un rifiuto ma il segnale di un errore OCR:
   si generano correzioni sui caratteri confusabili (`0↔O`, `1↔I`, `5↔S`…) e si verifica il checksum.
   Recupera la classe di falsi negativi che la letteratura indica come causa primaria di mancata
   redazione. **È il contributo tecnico originale del progetto.**
3. **NER locale** — `GLiNER-multi` (209 M, Apache-2.0, CPU) o Presidio come backend opzionale.
4. **Adjudicazione** — opzionale e **spenta di default**: un LLM *locale* piccolo (Qwen3-0.6B) a
   `T=0` con seed fisso, solo sugli span ambigui. Spenta perché una decisione di redazione deve
   essere riproducibile: il determinismo qui è una feature di compliance.
5. **Redazione immagini** — YuNet (~85 KB) per i volti + bbox OCR. Necessaria: una pagina raster
   incorporata contiene volti, firme e testo che la redazione testuale non tocca.

Pseudonimizzazione con token `⟦PER_7f3a⟧` = HMAC-SHA256(salt_documento, forma canonica), stabile
nel documento ma **non tra documenti** (i token stabili su un corpus abilitano il linkage). Vault
XChaCha20-Poly1305 con chiave nel keychain OS.

Il valore centrale è il **round-trip**: si invia al modello di frontiera solo testo pseudonimizzato,
si re-idrata la risposta in locale. Il provider non vede mai un dato personale, l'utente legge la
risposta completa. `anton-ocr forget <uuid>` distrugge la chiave → percorso pulito per l'Art. 17.

Categorie particolari GDPR **Art. 9** come classe separata con default **`BLOCK`, non `REDACT`**.

⚠️ **Anonimizzazione ≠ pseudonimizzazione.** Il dato pseudonimizzato resta dato personale (Art. 4(5));
l'anonimizzazione (Cons. 26) deve resistere a singling out, linkability e inference. Il tool dichiara
*cosa ha fatto*, mai *quale status giuridico ha prodotto*.

**③ POLICY — motore dichiarativo**

```yaml
# policy.yaml
version: 1
profile: gdpr-strict
rules:
  - match: { entity: "GDPR_ART9_HEALTH" }
    action: block
    reason: "Categoria particolare — richiede base giuridica dedicata"
  - match: { entity: ["CF", "IBAN", "EMAIL", "PHONE"] }
    action: redact
    reversible: false
  - match: { confidence_below: 0.75 }
    action: review
  - default: allow
retention:
  audit_log_days: 2555   # 7 anni — allineato ad Art. 11/18 AI Act
  vault_days: 90
```

Il file è versionato in git accanto ai documenti: **la policy stessa diventa evidenza**.

**④ ATTEST — manifest di provenienza firmato**

Un sidecar `.manifest.json` per output, firmato con `minisign` o `sigstore`:

```json
{
  "schema": "anton-ocr/manifest/v1",
  "document": { "uuid": "...", "source_sha256": "...", "pages": 42,
                "ingested_at": "2026-07-27T14:22:03Z", "operator": "operatore@macbook" },
  "pipeline": { "tool_version": "4.0.0", "policy_sha256": "...",
                "stages": ["ingest","classify","extract","screen","redact","attest"] },
  "models": [
    { "role": "ocr", "id": "mlx-community/GLM-OCR-8bit", "sha256": "...",
      "license": "MIT", "params": "0.9B", "pages_processed": 11 },
    { "role": "pii", "id": "urchade/gliner_multi-v2.1", "sha256": "...",
      "license": "Apache-2.0", "params": "209M" }
  ],
  "extraction": { "native_pages": 31, "vlm_pages": 11,
                  "mean_confidence": 0.91, "pages_below_threshold": [17, 33] },
  "redaction": { "CF": 3, "EMAIL": 7, "PERSON": 12, "GDPR_ART9": 0,
                 "reversible": false, "vault_ref": null },
  "decision": "allow",
  "ai_act": { "art50_marked": true, "content_credentials": "c2pa:urn:..." },
  "signature": { "alg": "ed25519", "key_id": "...", "value": "..." }
}
```

Allineare lo schema a **C2PA Content Credentials** dove possibile: è lo standard che l'industria sta
convergendo per l'Art. 50 e ti dà interoperabilità gratis.

**⑤ AUDIT — log append-only hash-chained**

Ogni riga contiene l'hash della precedente (stile Merkle chain). Rende il log tamper-evident senza
bisogno di un database. Semplice, elegante, molto "dimostrabile" — ottimo materiale per il README.

### 3.3 Strategia modelli — il vincolo "leggero" preso sul serio

Selezione a cascata, dal più economico al più costoso. **Il modello migliore è quello che non gira.**

| Tier | Componente | Modello | Param | Licenza | Quando |
|---|---|---|---|---|---|
| 0 | Estrazione testo | PyMuPDF | — | AGPL⚠ | Testo nativo ≥ soglia (~70% pagine reali) |
| 1 | Layout + OCR semplice | `granite-docling-258M` (MLX) | 258 M | Apache-2.0 | Pagine sparse, struttura semplice |
| 2 | OCR complesso | `GLM-OCR` | 0.9 B | **MIT** | Formule, tabelle, layout difficili |
| 2b | OCR multilingua | `PaddleOCR-VL` 0.9 B | 0.9 B | Apache-2.0 | Opzionale: 109 lingue |
| — | PII NER | `GLiNER-multi` | 209 M | Apache-2.0 | Sempre, su CPU |

**Footprint totale worst-case: ~2 GB.** Best-case (tier 0 + PII): ~250 MB.

Osservazioni:

- La scelta attuale di **GLM-OCR è già ottima**: 0.9 B, licenza **MIT** (la più permissiva del
  gruppo), primo su OmniDocBench V1.5. Non cambiarla, **affiancala**.
- ⚠️ **PyMuPDF è AGPL-3.0.** Se il progetto è MIT, questo è un problema di licenza reale che un
  reviewer su Hacker News noterà entro 20 minuti. Valuta `pypdfium2` (BSD-3/Apache) come default con
  PyMuPDF opzionale, oppure dichiara esplicitamente la dual-license. **Da risolvere prima del lancio.**
- Aggiungere Granite-Docling permette un **profilo "Pi 5"**: 258 M in MLX/ONNX gira su ARM con 8 GB.

### 3.4 Il benchmark che nessun altro ha

Hai tre classi di hardware sotto Tailscale: **Raspberry Pi 5**, **server i7-9700K**, **MacBook Apple
Silicon**. Nessun progetto OCR OSS pubblica una tabella s/pagina sulle tre. Falla:

| Hardware | Profilo | s/pagina (native) | s/pagina (VLM) | RAM picco |
|---|---|---|---|---|
| Raspberry Pi 5 (8 GB) | `lite` | ? | ? | ? |
| i7-9700K (CPU, Ollama) | `standard` | ? | ? | ? |
| MacBook Apple Silicon (MLX) | `standard` | ? | ? | ? |

Questa tabella, da sola, vale più di 500 righe di README. È verificabile, è onesta, e risponde alla
domanda che ogni lettore si pone davvero ("gira sulla mia roba?").

---

## 4. Fattibilità: verdetto

| Dimensione | Valutazione | Note |
|---|---|---|
| **Tecnica** | 🟢 Alta | Nessun componente richiede ricerca. Tutto è integrazione di pezzi esistenti e maturi. |
| **Vincolo "leggero"** | 🟢 Rispettato | ~2 GB worst-case, 250 MB in profilo lite. Gira su Pi 5. |
| **Effort** | 🟡 Medio | ~120–160 h per la v4.0 pubblicabile. Vedi §7. |
| **Legale/reputazionale** | 🟡 Attenzione | Il framing conta più del codice. Vedi §8. |
| **Differenziazione** | 🟢 Alta | Presidio fa PII ma non provenienza. Docling fa parsing ma non policy. Nessuno fa *tutto local + manifest firmato + policy gate*. |
| **Timing** | 🟢 Ottimo | 17 mesi di runway, mercato in fase pilota. |

### 4.1 Panorama competitivo

| Progetto | Cosa fa | Cosa NON fa |
|---|---|---|
| **Microsoft Presidio** | PII detection/anonymization, image redactor, structured data | Nessun OCR proprio, nessun manifest, nessuna policy per documento, nessun audit trail |
| **Docling** (IBM) | Parsing documenti eccellente, gerarchia, tabelle, reading order | Zero compliance |
| **Unstructured.io** | Ingestion multi-formato | Cloud-first, zero compliance |
| **MinerU / olmOCR** | Estrazione ad alta qualità | Zero compliance, modelli grossi |
| **Piattaforme sovereign AI** (BearingPoint, Mistral…) | Infrastruttura | Costose, enterprise, non self-hostable dal singolo |

**Lo spazio bianco:** `Docling-quality parsing` × `Presidio-grade redaction` × `manifest firmato` ×
`gira sul mio laptop`. Nessuno lo occupa. Il pattern "Docling + Presidio" esiste come *tutorial su
Medium*, non come prodotto. **Trasformare un tutorial ricorrente in un tool è la definizione di
un'opportunità OSS.**

---

## 5. Contesto economico-politico

Elementi rilevanti per il posizionamento (tutti verificabili, tutti da citare nel README/blog post):

- **Gartner**: spesa sovereign cloud IaaS a **80 mld $ nel 2026, +35,6% YoY**.
- **52%** delle aziende dell'Europa occidentale prevede di accelerare gli investimenti in data
  sovereignty; **47%** sta rivalutando le dipendenze da cloud non europei.
- **InvestAI**: 200 mld € mobilitati, di cui 20 mld € per AI gigafactories.
- **CLOUD Act**: la giurisdizione extraterritoriale USA sui provider americani è il driver numero uno
  del local-first in EU. È un argomento politico, non tecnico, e per questo è più persuasivo.
- **Tensione politica interna**: EDPB, EDPS e noyb hanno criticato pubblicamente il Digital Omnibus
  come deregolamentazione. Il dibattito sulla privacy è *acceso*, non risolto. Un tool che sta dalla
  parte della minimizzazione ha un vento favorevole narrativo.
- **Fatica da compliance**: il rinvio è stato motivato dall'impossibilità pratica di rispettare le
  scadenze. Questo dice esplicitamente che **manca il tooling**. È l'ammissione ufficiale del gap che
  il progetto vuole colmare.

**Sintesi:** il denaro c'è, la scadenza è slittata quanto basta per costruire, e le istituzioni hanno
dichiarato per iscritto che gli strumenti non sono pronti.

---

## 6. Blocker per la pubblicazione

Ordinati per gravità. I primi tre vanno risolti **prima** di qualsiasi promozione.

### 🔴 Critici

**B1 — Manca il file `LICENSE`.**
Il README mostra un badge MIT e dichiara "MIT © Matteo Tomassetti", ma il file non esiste nel repo.
Senza `LICENSE`, il codice è legalmente **all rights reserved** e nessuna azienda lo toccherà.
*Fix: 2 minuti. Impatto: totale.*

**B2 — Il README descrive un'architettura che non esiste più.**
Il README documenta: ThreadPool a 4 worker, `keep_alive=0`, nessun tiering, nessun MLX, nessun task
routing. Il codice fa: async con Semaphore(3), MLX nativo su Apple Silicon, classificazione layout a
4 task, idle VRAM monitor, chunked loading. **Il diagramma di architettura è di due generazioni fa.**
Chi apre il repo e legge il codice perde immediatamente fiducia.

**B3 — `config.env` committato con path personali.**
`OUTPUT_DIR=~/vault/Universita`, `INPUT_DIR=~/pdf/input`.
Idem `ocr_daemon.py` L55. Espone la struttura del tuo filesystem e rompe l'installazione per chiunque
altro. *Fix: `config.env.example` + `config.env` in `.gitignore` + default relativi.*

### 🟠 Alti

**B4 — Zero test.** Nessuna suite significa che nessuno può contribuire con fiducia e che tu non puoi
rifattorizzare. Servono almeno: fixture PDF (nativo, scansionato, formule, tabella), test dei
normalizzatori regex (`_clean_native_text` ha ~15 regole non testate, alcune aggressive), test del
policy engine, test golden dell'output.

**B5 — Nessuna CI.** GitHub Actions: lint (ruff), test, build. Il badge verde è segnale sociale.

**B6 — Non installabile come pacchetto.** Solo `install.sh`. Servono `pyproject.toml`, pubblicazione
PyPI, immagine Docker. Un `pipx install` o `docker run` abbassa la barriera d'ingresso di un ordine
di grandezza.

**B7 — Conflitto di licenza PyMuPDF (AGPL) vs progetto MIT.** Vedi §3.3.

### 🟡 Medi

**B8 — Il nome non comunica.** `anton-ocr` è un nome personale: non dice cosa fa né perché è diverso.
Candidati che comunicano il posizionamento:

| Nome | Angolo |
|---|---|
| **Provenance** / **Provenire** | Il manifest è la feature centrale |
| **Cartaceo** | Italiano, memorabile, "carta" — buon carattere europeo |
| **Custodia** | Custodia dei dati, latino/italiano, leggibile in EN |
| **Redakt** | Diretto, tecnico, cercabile |
| **Airlock** | Metafora perfetta: camera di compensazione tra documenti e agente |

`Airlock` è il migliore semanticamente (il documento entra, viene decontaminato, esce sicuro), ma è
molto usato. **`Cartaceo`** o **`Custodia`** hanno identità europea e sono liberi. Mantieni
`anton-ocr` come nome del daemon interno / alias storico.

**B9 — Nessuna demo visiva.** Serve un GIF asciinema nel README (drop PDF → manifest firmato in 8 s)
e idealmente uno Space su Hugging Face. Il primo screenshot è ciò che decide se qualcuno legge oltre.

---

## 7. Roadmap

### Fase 1 — Igiene del repo (1 settimana, ~15 h)
Sblocca tutto il resto. Nessuna feature nuova.

- [ ] `LICENSE` (MIT) — **fallo oggi**
- [ ] Risolvere PyMuPDF AGPL: `pypdfium2` default oppure dual-license dichiarata
- [ ] `config.env.example`, `config.env` in `.gitignore`, default relativi a `~/.anton-ocr/`
- [ ] Riscrivere README sull'architettura v3 reale
- [ ] `pyproject.toml` + `requirements.txt` pinnati
- [ ] GitHub Actions: ruff + pytest
- [ ] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue/PR template
- [ ] `SECURITY.md` (obbligatorio per credibilità in un tool di compliance)

### Fase 2 — Fondamenta tecniche (2 settimane, ~30 h)

- [ ] Suite di test + fixture PDF (T3)
- [ ] Confidence score per pagina (T1) — perplexity del modello o euristica su densità/coerenza
- [ ] Retry con task alternativo su fallimento/eco (T2)
- [ ] Idempotenza via SHA-256 del sorgente (T7)
- [ ] Modello configurabile da `config.env` (T4)
- [ ] Preservazione struttura: heading, reading order, bounding box opzionali (T6)

### Fase 3 — Motore di privacy + compliance (4 settimane, ~84 h) ← *il cuore*

> Dettaglio completo in **[`PRIVACY_ENGINE.md § 10`](./PRIVACY_ENGINE.md#10-roadmap-implementativa)**.

- [ ] Riconoscitori IT/EU con checksum + recupero errori OCR (Strati 1 e 1.5)
- [ ] Corpus sintetico `PII-OCR-IT` + harness di valutazione ← **fare presto: misura tutto il resto**
- [ ] GLiNER + backend Presidio opzionale + tassonomia Art. 9
- [ ] Motore token + vault cifrato + re-idratazione round-trip
- [ ] Redazione immagini (YuNet + bbox)
- [ ] `policy.py` + schema `policy.yaml` (profili `gdpr-strict` / `standard` / `permissive`)
- [ ] `attest.py` — manifest v1 + firma ed25519
- [ ] `audit.py` — JSONL append-only hash-chained
- [ ] Test CI zero-egress + modalità `--sealed`
- [ ] Review queue (Art. 14) + `forget` / TTL (Art. 17)

### Fase 4 — Confezionamento e lancio (2 settimane, ~35 h)

- [ ] CLI oltre al daemon: `anton-ocr ingest`, `verify`, `audit`, `forget`
- [ ] Immagine Docker multi-arch (amd64 + arm64 → il Pi 5 diventa dimostrabile)
- [ ] Pubblicazione PyPI
- [ ] Benchmark sulle tre macchine (§3.4)
- [ ] GIF demo + Hugging Face Space
- [ ] Documentazione: mapping norma→feature come pagina dedicata
- [ ] Blog post tecnico
- [ ] Lancio: r/LocalLLaMA, r/selfhosted, Hacker News (Show HN), Lobsters, LinkedIn EU privacy/AI-governance

**Totale: ~8 settimane part-time, ~130 h.** Compatibile con il carico esami se concentrato in agosto
e ripreso a gennaio.

### Sequenza di lancio suggerita

1. **Settembre 2026** — v4.0 su GitHub, silenziosa. Solo repo pulito.
2. **Ottobre 2026** — blog post + Show HN. Titolo che funziona:
   *"I built a local-first document pipeline that produces a signed audit trail for the EU AI Act"*.
   L'angolo "local-first" attira r/LocalLLaMA; l'angolo "AI Act" attira LinkedIn compliance.
   **Sono due pubblici distinti: due post distinti.**
3. **Novembre 2026** — reagire al feedback, v4.1. Il momento in cui i progetti muoiono è quando
   l'autore sparisce dopo il lancio.
4. **Q1 2027** — con dati d'uso reali, valutare l'aggancio alla tesi magistrale.

---

## 8. Rischi

| Rischio | Gravità | Mitigazione |
|---|---|---|
| **Over-claiming di conformità** | 🔴 Critico | Non scrivere mai "makes you AI Act compliant". Scrivere: *"produces the evidence artifacts required by Art. 10, 12 and 50. It is not legal advice and does not constitute conformity assessment."* Metterlo nel README, nel `--help` e nel manifest. Un claim eccessivo su un tool legale è l'unico errore che può danneggiare la reputazione invece di essere semplicemente ignorato. |
| **Falsi negativi PII** | 🔴 Critico | Nessun sistema PII ha recall 100%. Documentarlo esplicitamente con numeri misurati. La modalità `review` deve essere il default nei profili strict. Chi promette recall perfetto viene smontato pubblicamente. |
| **Deriva normativa** | 🟠 Alto | Il Digital Omnibus su dati/GDPR è ancora aperto. Isolare il mapping norma→feature in un file dati (`regulations/eu-ai-act.yaml`) invece che nel codice. Così un cambio di legge è un PR di config. |
| **Licenza PyMuPDF** | 🟠 Alto | Vedi B7. Risolvere prima del lancio. |
| **Abbandono post-lancio** | 🟠 Alto | Il rischio statisticamente più probabile. Definire in anticipo un impegno minimo (es. 4 h/settimana per 3 mesi) e dichiararlo nel README: è onesto e riduce la delusione. |
| **Un big player rilascia lo stesso tool** | 🟡 Medio | Probabile entro 12–18 mesi (IBM è il candidato: ha già Docling e Granite). Mitigazione: essere primi, essere pieno-locale (loro spingeranno il cloud), essere europei. |
| **Sovrapposizione con Presidio** | 🟡 Medio | Non competere: **integrare**. Presidio come backend PII opzionale è una feature, non una resa. Fa guadagnare credibilità e potenzialmente una menzione da parte loro. |
| **Conflitto con carico accademico** | 🟡 Medio | 130 h non sono poche. Le fasi 1–2 sono comunque utili anche se il progetto si ferma lì. |

---

## 9. Sinergie con il percorso accademico

Il grafo Anton mostra tre punti di aggancio diretti, e non sono decorativi:

- **Diritto dei Dati** (esame futuro) — questo progetto *è* il caso di studio. Fare l'esame dopo
  aver costruito il tool significa arrivarci con una comprensione operativa che quasi nessuno ha.
  Considera di anticiparne lo studio in parallelo alla Fase 3.
- **Cybersecurity** (esame futuro) — vault cifrato, firma ed25519, hash chain, threat model del
  pipeline. Materiale sovrapponibile.
- **Prova Finale (26 CFU)** — un tool OSS con utenti reali, benchmark riproducibili e un
  inquadramento normativo è materiale da tesi solido, e con contributi esterni misurabili diventa
  materiale da **110L**. L'elemento che distingue una tesi eccellente non è la complessità tecnica
  ma l'evidenza di impatto: le stelle, le issue e i fork sono evidenza di impatto.

L'infrastruttura esistente (Pi 5 + i7-9700K + MacBook via Tailscale) non è un dettaglio: è un banco
di prova multi-architettura che la maggior parte dei singoli sviluppatori non ha.

---

## 10. Prossimi tre passi concreti

1. **Oggi**: creare `LICENSE`, spostare `config.env` → `config.env.example`, rimuovere i path
   personali. *20 minuti, sblocca tutto.*
2. **Questa settimana**: decidere il nome e riscrivere il README sull'architettura reale.
3. **Prima di scrivere codice compliance**: implementare il confidence score (T1). È il prerequisito
   di tutto il resto — senza una misura di qualità, `review` e `Art. 15` non sono implementabili.

---

## Fonti

**Quadro normativo**

- [The Digital AI Omnibus: Proposed deferral of high risk AI obligations under the AI Act — DLA Piper](https://knowledge.dlapiper.com/dlapiperknowledge/globalemploymentlatestdevelopments/2026/The-Digital-AI-Omnibus-Proposed-deferral-of-high-risk-AI-obligations-under-the-AI-Act)
- [EU AI Act Omnibus Agreement — Postponed High-Risk Deadlines and Other Key Changes — Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/)
- [The final Digital Omnibus on AI — Freshfields](https://www.freshfields.com/en/our-thinking/blogs/technology-quotient/eu-ai-act-unpacked-34-the-final-digital-omnibus-on-ai-key-amendments-to-the-a-102nber)
- [Article 50 transparency obligations: the AI Act deadline of 2 August 2026 that has not been postponed](https://www.aiactblog.nl/en/posts/article-50-transparency-deadline-2-august-2026)
- [The EU AI Act's Transparency Rules: A Practical Guide to Article 50](https://artificialintelligenceact.eu/transparency-rules-article-50/)
- [Article 12: Record-keeping — AI Act](https://rgpd.com/ai-act/chapter-3-high-risk-ai-systems/article-12-record-keeping/)
- [EU AI Act Articles 12 & 13 Explained: Decision Traceability & Audit Compliance](https://aigovernancedesk.com/eu-ai-act-articles-12-13-decision-traceability/)
- [EU Digital Omnibus amendments to GDPR to facilitate AI training miss the mark — IAPP](https://iapp.org/news/a/eu-digital-omnibus-amendments-to-gdpr-to-facilitate-ai-training-miss-the-mark)
- [Digital Omnibus: EU DPAs reject many proposed changes to the GDPR — noyb](https://noyb.eu/en/digital-omnibus-eu-dpas-reject-many-proposed-changes-gdpr)
- [The EU's Digital and AI Omnibus is Heading in the Wrong Direction — Jacques Delors Centre](https://www.delorscentre.eu/en/publications/detail/publication/the-eus-digital-and-ai-omnibus)

**Modelli e stato dell'arte**

- [zai-org/GLM-OCR — GitHub](https://github.com/zai-org/GLM-OCR)
- [PaddlePaddle/PaddleOCR-VL — Hugging Face](https://huggingface.co/PaddlePaddle/PaddleOCR-VL)
- [ibm-granite/granite-docling-258M — Hugging Face](https://huggingface.co/ibm-granite/granite-docling-258M)
- [Best Open-Source OCR and Document VLMs to Self-Host in 2026 — Spheron](https://www.spheron.network/blog/best-open-source-ocr-vlm-self-host-gpu-cloud-2026/)
- [OCR Benchmark Leaderboard 2026](https://instavar.com/blog/ai-production-stack/OCR_SOTA_Feb_2026_Open_Document_AI_Leaderboard)

**Compliance tooling e provenienza**

- [Microsoft Presidio: PII Detection Guide 2026](https://explainx.ai/blog/microsoft-presidio-pii-detection-anonymization-guide-2026)
- [Securing Your GenAI Pipeline: Automated PII Obfuscation with Docling & Presidio](https://dev.to/aairom/securing-your-genai-pipeline-automated-pii-obfuscation-with-docling-microsoft-presidio-17mn)
- [PII Redaction Pipeline for LLM Workloads (2026 Architecture)](https://appscale.blog/en/blog/pii-redaction-pipeline-llm-presidio-ner-reversible-tokenisation-2026)
- [C2PA And The AI Supply Chain: Verifying Authenticity](https://aicompetence.org/c2pa-ai-supply-chain-verifying-authenticity/)
- [What is C2PA? Content Provenance Explained (2026)](https://c2paviewer.com/articles/what-is-c2pa)

**Contesto economico**

- [Sovereign On-Premise AI Infrastructure for Europe — BearingPoint](https://www.bearingpoint.com/en/about-us/news-and-media/press-releases/sovereign-ai-infrastructure-europe/)
- [Europe AI Landscape 2026: EU Act, Mistral, Sovereign Compute](https://explainx.ai/blog/europe-ai-landscape-sovereign-compute-eu-act-2026)
- [EU Sovereign AI Infrastructure Stack: The Complete 2026 Guide](https://techplustrends.com/eu-sovereign-ai-infrastructure-stack-2026-guide/)

---

> ⚖️ **Disclaimer.** Questo documento è un'analisi tecnica e strategica, non consulenza legale.
> Le scadenze e gli obblighi citati riflettono lo stato normativo al 27 luglio 2026 e sono soggetti
> a modifica. Prima di dichiarazioni pubbliche di conformità, far verificare il mapping
> norma→feature a un professionista qualificato.
