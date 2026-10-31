# BLOCCO-6B — Firma elettronica: stato e cosa manca

## Cosa c'è (commit del 6B)

- Migrazione `122_signature_requests` (UP/DOWN): richieste di firma dei fogli
  visita con fornitore, firmatario, scadenza, stato, riferimento del
  fornitore, documento ricevuto con SHA-256, chiave di idempotenza per
  agenzia e registro append-only delle transizioni.
- Interfaccia del fornitore `esign.providers.SignatureProvider`
  (`create_request`, `cancel_request`, `fetch_document`).
- Un solo adattatore: `SimulatedProvider`, locale, riferimenti `SIM-…`,
  nessuna rete. Il suo «documento ricevuto» è una ricevuta che dichiara
  «SIMULAZIONE — NESSUNA FIRMA VALIDA».
- Transizioni controllate (`sent` → `simulated_completed` / `declined` /
  `expired` / `cancelled`; `signed` solo per un fornitore reale). Il database
  vieta `signed` per le richieste simulate e qualsiasi modifica a una
  richiesta chiusa. La scadenza si applica alla lettura.
- Interfaccia operatore: nei «Fogli visita», «Richiedi firma (simulazione)»,
  annullamento, esiti simulati, download della ricevuta.

## Cosa NON c'è e serve per una firma reale

1. **Scelta del fornitore** (non fatta, nessun acquisto): firma elettronica
   avanzata o qualificata secondo eIDAS/CAD, con identificazione del
   firmatario adeguata al livello scelto. Un tratto sullo schermo o un OTP
   non sono considerati prova di firma avanzata.
2. **Adattatore reale** che implementi `SignatureProvider`, con credenziali
   in variabili d'ambiente (mai nel repository) e un valore di
   `ESIGN_PROVIDER` dedicato (oggi ogni valore diverso da `simulated` risponde
   «non configurato»).
3. **Ricezione degli esiti**: un endpoint di callback del fornitore con
   verifica della firma del messaggio, idempotenza sugli eventi e transizione
   `sent` → `signed`/`declined`/`expired`, più un giro di riconciliazione per
   gli esiti persi.
4. **Documento firmato**: scaricare dal fornitore il PDF firmato e le prove
   (audit trail, certificati), verificarne integrità e firma, conservarli
   (oggi la colonna `received_document` è in database; per volumi reali va
   valutato uno storage dedicato e la conservazione a norma).
5. **Invio al firmatario**: il fornitore o il CRM devono recapitare il
   documento; oggi nessun messaggio parte.
6. **Testo del foglio visita**: è una bozza di modello da revisionare
   legalmente prima di qualsiasi firma.
7. **Informativa privacy e consenso** del firmatario al trattamento presso il
   fornitore.
