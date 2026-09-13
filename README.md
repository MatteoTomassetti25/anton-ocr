<div align="center">

# 🔒 anton-ocr

**Il tuo agente AI ingerisce documenti. Chi può dimostrare cosa c'era dentro?**

Pipeline documentale **interamente locale** che pseudonimizza i dati personali
*prima* che raggiungano un modello esterno — e produce un audit trail firmato.

[![CI](https://github.com/MatteoTomassetti25/anton-ocr/actions/workflows/ci.yml/badge.svg)](https://github.com/MatteoTomassetti25/anton-ocr/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)](LICENSE)
[![Zero egress](https://img.shields.io/badge/network-zero%20egress%20(CI--verified)-000?style=flat-square)](#zero-egress-verificato-non-promesso)

</div>

---

```
  ┌───────────────── CONFINE LOCALE (nessuna connessione di rete) ──────────────┐
  │                                                                             │
  │  PDF ──▶ estrazione ──▶ rilevamento ──▶ pseudonimizzazione ──▶ testo sicuro ┼──▶ modello
  │   │                          │                    │                         │   di frontiera
  │   │                          │                    ▼                         │        │
  │   │                     checksum +          vault cifrato                   │        │
  │   │                  recupero errori OCR    (token → valore)                │        │
  │   │                                               │                         │        │
  │ originale                                         ▼                         │        │
  │ mai spedito                            re-idratazione ◀── risposta ◀────────┼────────┘
  │                                               │                             │
  │                                               ▼                             │
  │                                      risposta completa                      │
  └─────────────────────────────────────────────────────────────────────────────┘
```

Il provider del modello vede `⟦PER_7f3a⟧ ha presentato ricorso il ⟦DOB_2c81⟧`.
Tu leggi `Mario Rossi ha presentato ricorso il 1985`.
La mappatura non esiste al di fuori della tua macchina.

---

## Perché esiste

Mandare documenti a un modello di frontiera è ormai normale. Quello che manca è
tutto il resto: nessuno sa **cosa** è stato spedito, **cosa** è stato tolto, con
**quale** modello e **quando**. Se serve dimostrarlo — a un cliente, a un
revisore, a un'autorità — non c'è nulla da mostrare.

`anton-ocr` produce quel qualcosa da mostrare, senza che i dati escano dalla
macchina.

---

## Quick start

**Documento dentro, `.safe.md` fuori.** È tutto qui.

```bash
pip install 'anton-ocr[pdf]'
anton-ocr ingest contratto.pdf     # → contratto.safe.md
```

```console
✓ ALLOW  contratto.pdf
  uuid            fca5e608-1926-4b1e-beb8-8127f0ebb6ee
  qualità testo   0.99
  rilevamento     checksum=3 | ocr_recovery=1 | regex=3 | recuperati_da_ocr=1
  token emessi    6
  output          ~/.anton-ocr/output/contratto.safe.md
  manifest        ~/.anton-ocr/output/contratto.manifest.json
```

Il `.safe.md` è **autoconsistente**: chi lo incolla in una chat non porta con sé il
manifest, quindi tutto ciò che serve per usarlo correttamente sta dentro il file.

```markdown
---
anton_ocr:
  doc_uuid: fca5e608-1926-4b1e-beb8-8127f0ebb6ee
  source_sha256: 3b66c020c9e3c2d7…
  decision: allow
  policy_profile: gdpr-strict
  egress: none
  content_origin: machine-generated
---

# contratto

> [!IMPORTANT] Istruzione per l'agente
> Il testo contiene segnaposto nella forma ⟦TIPO_XXXX⟧ che sostituiscono dati
> personali. Riportali SEMPRE identici, carattere per carattere…

## Dati rimossi

| Categoria      | Occorrenze | Trattamento          | Segnaposto    |
|----------------|-----------:|----------------------|---------------|
| Codici fiscali |          2 | sostituito con token | `⟦CF_····⟧`   |
| Indirizzi      |          1 | reso meno preciso    | `⟦ADR_····⟧`  |

1 identificatore era danneggiato dall'OCR ed è stato ricostruito tramite checksum.

---

## Contenuto

Il sottoscritto Mario Rossi, nato il 1985, codice fiscale ⟦CF_b5a5⟧,
residente in Via Giuseppe Garibaldi, email ⟦EML_d0ec⟧…
```

Poi il giro di ritorno:

```bash
anton-ocr rehydrate <uuid> risposta.txt   # ⟦CF_b5a5⟧ → RSSMRA85T10A562S
```

La legenda riporta **solo conteggi, mai valori** — scriverne anche uno
vanificherebbe tutto il resto.

---

## Interfaccia grafica

```bash
anton-ocr gui        # → http://127.0.0.1:8731
```

Trascina un documento, vedi cosa è stato trovato, copia il `.safe.md`, incolla la
risposta dell'agente e ottieni i valori reali. Quattro passaggi, una pagina.

**Dalla GUI nulla viene salvato su disco.** Il documento vive nella pagina finché
non lo scarichi tu — `.safe.md` e manifest, entrambi con un bottone. In uno
strumento che esiste per ridurre le copie dei dati personali, produrne una che
nessuno ha chiesto sarebbe una contraddizione. Restano scritti solo l'audit log
(il registro degli eventi) e il vault delle chiavi, che serve alla re-idratazione.

Da riga di comando vale l'opposto: `anton-ocr ingest` scrive in `OUTPUT_DIR`,
perché è quello che ci si aspetta da un comando batch.

L'implementazione segue le stesse regole del resto:

- **sola libreria standard** — nessun framework, nessuna CDN, nessun font remoto.
  Chi vuole verificare che i dati non escano legge un file solo;
- **si lega esclusivamente a 127.0.0.1**, e il binding è verificato: un'interfaccia
  che mostra il contenuto dei documenti non deve essere raggiungibile dalla rete,
  nemmeno per errore, nemmeno via Tailscale;
- **CSP `default-src 'none'`** e token CSRF: anche in locale, una pagina ostile
  aperta nello stesso browser potrebbe bussare a localhost;
- se il riconoscimento dei nomi è spento, **te lo dice in rosso** — un documento che
  *sembra* sicuro è peggio di uno palesemente non trattato.

---

## Cosa lo distingue

### 1. Recupera gli identificatori che l'OCR ha rovinato

Questo è il contributo tecnico centrale, e nessun altro strumento lo fa.

La letteratura indica gli errori OCR come **causa primaria dei mancati
oscuramenti**. Un codice fiscale letto come `RSSMRA85TIOA562S` (lettera `I` al
posto di `1`, `O` al posto di `0`) non supera il checksum, viene scartato dalle
pipeline classiche e finisce **in chiaro** nel prompt.

Qui il ragionamento è rovesciato:

> **Un checksum fallito non è un rifiuto. È il segnale di un errore OCR.**

Gli identificatori strutturati hanno una forma nota — il codice fiscale è
`LLLLLL DD L DD L DDD L`. Se in una posizione che deve contenere una cifra c'è
una lettera, la classe attesa dice già in cosa correggerla. Non è una ricerca: è
una deduzione, deterministica e istantanea.

```console
$ anton-ocr scan --text "Codice fiscale RSSMRA85TIOA562S"
  IT_CODICE_FISCALE  ocr_recovery  conf=0.76  'RSSMRA85TIOA562S'  ← recuperato: RSSMRA85T10A562S
```

E — dettaglio che conta — il codice letto male e quello letto bene ricevono
**lo stesso token**, perché la canonicalizzazione avviene sul valore corretto.

Il rovescio della medaglia è governato con altrettanta cura: la ricerca più
aggressiva (confusioni interne alla stessa classe) si attiva **solo** se il
contesto la sostiene, cioè se accanto compare l'etichetta "Codice fiscale:".
Senza quel vincolo, su testo qualunque lo strumento inventerebbe identificatori
che non esistono. La precisione qui conta quanto il recall.

### 2. Zero egress *verificato*, non promesso

Tutti scrivono "100% locale". Qui la CI esegue l'intera suite dentro un network
namespace privo di rotta (`unshare -rn`): **se un qualunque componente tenta di
raggiungere la rete, la build fallisce**.

C'è anche un controllo del controllo — un job che verifica che l'isolamento sia
davvero attivo, perché altrimenti il test non proverebbe nulla.

### 3. Audit trail a prova di manomissione

Ogni voce del log contiene l'hash della precedente. Alterare o rimuovere una
riga rompe la catena, e `verify` indica **il punto esatto**:

```console
$ anton-ocr audit --verify
✗ catena compromessa alla voce 2: Contenuto alterato: l'hash ricalcolato non coincide
```

Non impedisce la manomissione — la rende *evidente*. Per l'art. 12 dell'AI Act
è esattamente ciò che serve, e non richiede un database: resta un file leggibile
con `cat`.

### 4. La policy è evidenza, non configurazione

`policy.yaml` è versionabile in git e il suo SHA-256 finisce in ogni manifest.
Poter dimostrare **quale regola era in vigore** al momento dell'elaborazione è
metà del valore dell'audit trail.

```yaml
entities:
  - { type: IT_CODICE_FISCALE, action: pseudonymize }
  - { type: ADDRESS,           action: generalize, to: municipality }
  - { type: DATE_OF_BIRTH,     action: generalize, to: year }
  - { type: GDPR_ART9_HEALTH,  action: block, reason: "Categoria particolare art. 9" }

thresholds:
  review_below_confidence: 0.75
  block_on_detection_error: true   # fail-closed
```

Due default deliberatamente controcorrente:

- le **categorie particolari** (art. 9 GDPR) sono `block`, non `redact` — un
  referto medico privato dei nomi resta un referto medico;
- **fail-closed** — se il rilevamento fallisce, il documento non passa. In un
  sistema di privacy, degradare in silenzio significa perdere dati.

### 5. Cancellazione crittografica (art. 17 GDPR)

```console
$ anton-ocr forget fca5e608-1926-4b1e-beb8-8127f0ebb6ee
✓ documento dimenticato
  I token emessi sono ora irreversibili, incluse le copie già nei backup.
```

Si distrugge la chiave, non il file: più forte, perché raggiunge anche le copie
che non controlli più.

---

## Leggero per scelta

Il modello migliore è quello che non gira. Selezione a cascata, dal più
economico al più costoso:

| Livello | Componente | Modello | Parametri | Licenza | Quando |
|---|---|---|---|---|---|
| 0 | Testo nativo | pypdfium2 | — | Apache-2.0 | ~70% delle pagine reali |
| 1 | PII strutturata | regex + checksum | — | — | sempre, costo nullo |
| 2 | PII nominata | GLiNER-multi | 209 M | Apache-2.0 | opzionale, CPU |
| 3 | OCR pagine sparse | GLM-OCR | 0.9 B | MIT | solo se necessario |

**Ingombro minimo ~250 MB, massimo ~2 GB.** Il nucleo privacy dipende solo da
`cryptography` e `PyYAML`: gira ovunque, Raspberry Pi compreso.

> ℹ️ **Nota sulle licenze.** `pypdfium2` (Apache-2.0/BSD) è il motore
> predefinito. **PyMuPDF è AGPL-3.0** ed è quindi un extra opzionale: renderlo
> obbligatorio in un progetto MIT costringerebbe ogni utilizzatore a fare i conti
> con l'AGPL. Chi lo installa sceglie consapevolmente.

---

## Installazione

```bash
pip install 'anton-ocr[pdf]'              # nucleo + estrazione PDF
pip install 'anton-ocr[pdf,ner]'          # + riconoscimento entità nominate
pip install 'anton-ocr[pdf,mlx]'          # + OCR su Apple Silicon
pip install 'anton-ocr[pdf,ollama]'       # + OCR via Ollama (NVIDIA/CPU)
pip install 'anton-ocr[pdf,daemon]'       # + watchfolder

anton-ocr doctor                          # verifica cosa è attivo
```

```bash
cp config.env.example config.env          # `config.env` è ignorato da git
```

---

## Comandi

| Comando | Cosa fa |
|---|---|
| `gui` | Interfaccia web locale su 127.0.0.1 |
| `ingest <file>` | Elabora il documento, produce `.safe.md` + manifest firmato |
| `scan <file>` \| `--text` | Mostra il rilevamento senza scrivere nulla — utile per tarare la policy |
| `rehydrate <uuid> <file>` | Reinserisce i valori reali nella risposta del modello |
| `verify <manifest>` | Verifica la firma Ed25519 |
| `audit --verify` | Verifica l'integrità della catena di hash |
| `forget <uuid>` | Distrugge la chiave: i token diventano irreversibili |
| `doctor` | Stato dell'ambiente, dei backend e della policy |
| `prompt` | Stampa l'istruzione di sistema per il modello di frontiera |

---

## Uso come libreria

```python
from anton_ocr.pipeline import Pipeline
from anton_ocr.privacy.rehydrate import rehydrate

pipeline = Pipeline()
result = pipeline.process_pdf("contratto.pdf")

if result.allowed:
    # safe_markdown = documento completo (istruzione + legenda + contenuto)
    # safe_text     = solo il testo pseudonimizzato
    risposta = mio_modello(result.safe_markdown)

    finale = rehydrate(risposta, result.mapping)
    print(finale.text)
    print(f"copertura token: {finale.coverage:.0%}")
```

---

## Entità riconosciute

**Con validazione checksum** (falsi positivi ≈ 0): codice fiscale (CIN + vincoli
su mese e giorno), partita IVA, IBAN (ISO 7064 mod-97), carte di pagamento
(Luhn), targhe, tessera sanitaria.

**Con pattern**: email, telefono (IT), IPv4, MAC, indirizzi, date, date di nascita.

**Con NER locale** *(opzionale)*: persone, organizzazioni, luoghi, e le sette
categorie particolari dell'art. 9 GDPR.

---

## Cosa questo strumento NON fa

Sezione obbligatoria. La credibilità di uno strumento di privacy si misura sulla
franchezza dei suoi limiti.

| Limite | Perché |
|---|---|
| **Il recall non è 100%** | Nessun sistema PII lo raggiunge. L'OCR degradato peggiora la situazione. Per questo esiste la modalità `review`. |
| **Non protegge dall'inferenza** | `⟦PER_7f3a⟧, direttore sanitario a Fiuggi nel 2019` può identificare una sola persona. Pseudonimizzare i nomi non risolve i quasi-identificatori. |
| **Pseudonimo ≠ anonimo** | Il dato pseudonimizzato **resta dato personale** (art. 4(5) GDPR). L'anonimizzazione (Cons. 26) deve resistere a singling out, linkability e inference. |
| **Le immagini non sono coperte** | La redazione testuale non tocca volti, firme e timbri nei raster incorporati. |
| **Non ti rende conforme** | Produce **artefatti di evidenza**, non conformità. Non è una valutazione di conformità e non è consulenza legale. |

Lo strumento dichiara *cosa ha fatto*, mai *quale status giuridico ha prodotto*.
Ogni manifest include questi limiti in modo esplicito.

---

## Sviluppo

```bash
git clone https://github.com/MatteoTomassetti25/anton-ocr.git
cd anton-ocr
pip install -e '.[pdf,dev]'
pytest -q
unshare -rn python -m pytest -q       # come in CI: senza rete (Linux)
```

Contributi benvenuti — vedi [CONTRIBUTING.md](CONTRIBUTING.md).
Vulnerabilità: [SECURITY.md](SECURITY.md).

**Documenti di progetto:** [FEASIBILITY.md](FEASIBILITY.md) (analisi normativa e
strategica) · [PRIVACY_ENGINE.md](PRIVACY_ENGINE.md) (design del motore).

---

## Licenza

MIT © [Matteo Tomassetti](https://github.com/MatteoTomassetti25)

> ⚖️ Questo software produce artefatti tecnici di evidenza. Non costituisce una
> valutazione di conformità né consulenza legale. Prima di dichiarazioni
> pubbliche di conformità, fai verificare il mapping norma→funzionalità a un
> professionista qualificato.
