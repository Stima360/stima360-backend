# STIMA360 OS — CORE Admin 0.1

Interfaccia amministrativa additiva per l'ambiente di test.

## URL

`/core-admin/`

## Funzioni

- Panoramica CORE
- Lista, ricerca, creazione e modifica contatti
- Scheda contatto e assegnazione ruoli
- Lista, creazione e aggiornamento lead
- Scheda lead con attività, task e stime collegate
- Creazione e consultazione attività
- Creazione, consultazione e cambio stato task
- Collegamento manuale lead → stima legacy tramite ID

## Sicurezza operativa

- Nessun backfill automatico
- Nessuna modifica alle tabelle legacy
- Nessuna modifica a PDF, WhatsApp, valutazione o flussi pubblici
- Distribuire esclusivamente su `stima360-backend-test`
