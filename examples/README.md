# Esempi

Tutti i dati in questa cartella sono **sintetici** e non si riferiscono a persone
reali. Codici fiscali, IBAN e partite IVA sono valori di test che superano i
rispettivi checksum ma non sono assegnati a nessuno.

## `esempio_verbale.txt`

```bash
anton-ocr scan   examples/esempio_verbale.txt    # anteprima, non scrive nulla
anton-ocr ingest examples/esempio_verbale.txt    # produce il .safe.md
```

Due dettagli sono inseriti apposta.

**1. Un codice fiscale rovinato dall'OCR.** Alla riga 4 compare
`RSSMRA85TIOA562S`: lettera `I` al posto della cifra `1`, lettera `O` al posto
dello `0` — la confusione tipica di un motore OCR. Non supera il checksum, e una
pipeline classica lo scarterebbe lasciando il dato **in chiaro** nel prompt.

Alla riga 16 compare lo stesso codice scritto correttamente, `RSSMRA85T10A562S`.

Nel `.safe.md` prodotto entrambi ricevono **lo stesso token**: la
canonicalizzazione avviene sul valore ricostruito, quindi l'agente a valle capisce
che si tratta della stessa persona.

```console
$ anton-ocr scan examples/esempio_verbale.txt
  IT_CODICE_FISCALE   ocr_recovery  conf=0.76  'RSSMRA85TIOA562S'  ← recuperato: RSSMRA85T10A562S
  IT_CODICE_FISCALE   checksum      conf=1.00  'RSSMRA85T10A562S'
```

**2. I nomi restano in chiaro se il NER è spento.** Con la configurazione
predefinita, "Mario Rossi" e "Anna Bianchi" **non vengono cercati**: sono trattati
solo gli identificatori strutturati. Il documento prodotto lo dichiara in rosso
nella sezione *Avvertenze*, e la GUI mostra un banner.

Per attivare il riconoscimento dei nomi:

```bash
pip install 'anton-ocr[ner]'
echo 'NER_BACKEND=gliner' >> config.env
```

Il primo avvio scarica il modello (~200 MB, una volta sola). Dopodiché l'avvertenza
sparisce e nella legenda compare la voce «Nomi di persona».
