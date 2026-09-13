# Politica di sicurezza

## Segnalare una vulnerabilità

Non aprire una issue pubblica per vulnerabilità di sicurezza.

Usa [GitHub Security Advisories](https://github.com/MatteoTomassetti25/anton-ocr/security/advisories/new)
oppure scrivi a **matteotomassetti25@gmail.com**.

Tempi indicativi: riscontro entro 72 ore, valutazione entro 7 giorni. Questo è un
progetto portato avanti da una persona sola — i tempi sono un impegno realistico,
non un SLA aziendale.

## Cosa consideriamo una vulnerabilità

Questo è uno strumento di privacy: la definizione è più ampia del solito.

**Sì, è una vulnerabilità:**

- un dato personale che finisce nel testo "sicuro" (falso negativo sistematico,
  non un singolo caso limite)
- un valore in chiaro nel vault, nel manifest o nell'audit log
- un componente del percorso di elaborazione che apre una connessione di rete
- la chiave del vault che finisce su disco in forma non protetta
- una firma non valida accettata come valida
- una manomissione della catena di audit non rilevata
- `forget` che lascia recuperabile la mappatura

**No, non è una vulnerabilità** (ma è comunque un bug utile da segnalare):

- un'entità non rilevata in un caso specifico — il recall non è 100% ed è
  dichiarato
- la re-identificazione per inferenza dal contesto residuo — limite noto e
  documentato
- il testo dentro le immagini incorporate — non ancora coperto, vedi il README

## Modello di minaccia

| Avversario | Difesa |
|---|---|
| Provider del modello di frontiera | Vede solo testo pseudonimizzato; il vault non lascia la macchina |
| Chi legge l'output archiviato | L'output è redatto per default; l'originale sta altrove |
| Chi ottiene accesso al disco | Vault cifrato XChaCha20-Poly1305; chiave nel portachiavi o da passphrase |
| **Lo strumento stesso** | Zero-egress verificato in CI dentro un network namespace isolato |
| Re-identificazione per inferenza | **Non risolto.** Limite dichiarato. |

## Note sulla gestione delle chiavi

La chiave del vault viene cercata in quest'ordine:

1. **passphrase** — derivata con scrypt. Protezione a riposo più forte.
2. **portachiavi di sistema** — Keychain su macOS, secret-service su Linux.
3. **file di chiave** (ripiego) — permessi `0600` accanto al vault.

Il terzo caso ha un compromesso da conoscere: protegge dalla condivisione
accidentale del solo file `.vault` (backup, sync, allegato) ma **non** da chi
ottiene accesso completo alla cartella. Su sistemi senza portachiavi è l'unica
opzione che mantiene funzionante il giro di ritorno fra processi diversi. Per una
protezione reale a riposo, usa una passphrase.

## Dipendenze

Il nucleo privacy dipende solo da `cryptography` e `PyYAML`. Ogni dipendenza
aggiuntiva è un extra opzionale ed esplicito: in uno strumento di privacy, un
pacchetto che raccoglie telemetria è a tutti gli effetti una vulnerabilità.
