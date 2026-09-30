"""A31-2 - VISITE ACQUIRENTE: la proiezione `appointments` -> `property_visits`.

Gate A31-1 (DESIGN FREEZE, D1-D8): una visita acquirente nasce in
`appointments` (`appointment_type = 'buyer_visit'`, fonte autorevole,
`source = 'crm_manual'`), e `property_visits` ne e' la PROIEZIONE, scritta
nella STESSA transazione della mutazione dell'Agenda.

Perche' un package a parte: la sentinella A30-2 (`test_32`) vieta a
`appointments/` di conoscere la tabella legacy. Qui vive l'UNICO codice nuovo
che la nomina; `appointments/service.py` chiama soltanto gli hook di
`buyer_visits.integration`, come fa con `calendar_sync.integration` (A30-9B).

Regole del package:
  * lavora SOLO sul cursore del chiamante: nessuna connessione propria,
    nessun commit, nessun rollback (se qualcosa fallisce, l'eccezione risale
    e la transazione dell'Agenda si annulla tutta);
  * non scrive MAI `outcome`, `feedback`, `rating` (D4: restano legacy);
  * non tocca le righe legacy (`appointment_id IS NULL`): nessun backfill,
    nessun import, nessuna mutazione automatica;
  * non tocca Google, BUY, owner feedback, FLOW.
"""
