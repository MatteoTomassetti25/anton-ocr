# Contribuire ad anton-ocr

Grazie per l'interesse. Questo documento dice come contribuire e — più
importante — **quali principi non sono negoziabili**, così non scopri a PR
inviata che l'approccio non era compatibile.

## Principi

**1. Il percorso di elaborazione non tocca la rete.**
Nessun componente fra l'ingresso del documento e la scrittura dell'output può
aprire una connessione. L'unico punto autorizzato è il download dei modelli, che
è esplicito e separato. Il job CI `no-egress` lo verifica dentro un network
namespace isolato.

**2. Le decisioni di redazione sono deterministiche.**
Stesso input + stessa policy + stessa versione ⇒ stesso output, sempre. Senza
questo l'audit trail non è verificabile. È il motivo per cui l'adjudicazione via
LLM è disattivata di default anche essendo locale.

**3. Fail-closed.**
Se il rilevamento fallisce, il documento non passa. Un PR che introduce un
percorso di degrado silenzioso non verrà accettato.

**4. La precisione conta quanto il recall.**
Redigere tutto è facile e inutile. Un PR che alza il recall va accompagnato dal
suo effetto sui falsi positivi.

**5. Non si sovrapromette.**
Niente affermazioni di conformità nel codice, nella documentazione o nei
messaggi. Lo strumento produce evidenza, non conformità. Nessuna frase del tipo
"makes you GDPR compliant".

## Preparare l'ambiente

```bash
git clone https://github.com/MatteoTomassetti25/anton-ocr.git
cd anton-ocr
python -m venv .venv && source .venv/bin/activate
pip install -e '.[pdf,dev]'

pytest -q
ruff check src tests
unshare -rn python -m pytest -q     # come in CI (Linux)
```

## Aggiungere un riconoscitore

È il contributo più utile, soprattutto per paesi diversi dall'Italia.

1. Se l'identificatore ha un checksum, aggiungi il validatore in
   `privacy/checksums.py`. **Il checksum è ciò che rende il riconoscitore
   affidabile**: senza, i falsi positivi rendono lo strumento inutilizzabile.
2. Aggiungi il pattern candidato in `privacy/recognizers.py`. Deve essere
   *permissivo*: i candidati che falliscono il checksum vanno allo strato di
   recupero errori OCR, non nel cestino.
3. Se ha una forma fissa, aggiungi la sua `SHAPE` in `privacy/ocr_recovery.py`
   così eredita gratis il recupero degli errori OCR.
4. Aggiungi il tipo in `privacy/entities.py` e l'abbreviazione in
   `privacy/pseudonymize.py`.
5. Scrivi i test: casi validi, casi invalidi **e casi di rumore che non devono
   produrre rilevamenti**.

Usa solo identificatori sintetici o di test pubblici. Mai dati reali, nemmeno tuoi.

## Test

Ogni PR che tocca il rilevamento deve dimostrare entrambe le direzioni:

```python
def test_rileva_il_caso_valido(): ...
def test_non_rileva_il_rumore(): ...      # ← quello che si dimentica sempre
```

I test di regressione portano un commento che spiega *cosa* era rotto. Esempio
reale nel repo: la classe `[\s\-]` nel pattern IBAN faceva assorbire il newline,
e il match si estendeva alla riga successiva.

## Messaggi di commit

Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`,
`chore:`. Il corpo spiega il *perché*, non il *cosa* — il diff mostra già il cosa.

## Cosa serve di più

- **riconoscitori per altri paesi europei** (DE, FR, ES: identificatori fiscali,
  documenti d'identità)
- **corpus sintetico `PII-OCR-IT`** e harness di valutazione — misurare il recall
  su testo degradato è la priorità del progetto
- **redazione delle immagini** (volti, firme) — oggi non coperta
- **benchmark su hardware diverso**, Raspberry Pi in particolare

## Codice di condotta

Sii rispettoso e costruttivo. Le critiche vanno al codice, non alle persone.
