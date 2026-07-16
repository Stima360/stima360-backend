STIMA360 PROPERTY 0.2 — SOLO AMBIENTE TEST

Novità:
- dashboard operativa PROPERTY
- avvisi su incarichi, documenti e visite
- storico prezzi
- storico stato commerciale e classificazione
- modifica documenti
- modifica/ordinamento/copertina foto
- aggiornamento visite e follow-up
- filtri estesi e indicatori di completezza immobile

Ordine test:
1. caricare i file nel branch core-0.1-test
2. applicare migrations/003_property_02.sql esclusivamente su stima360-db-test
3. deploy esclusivamente di stima360-backend-test
4. aprire /property-admin/

Non eseguire 003_property_02_down.sql salvo rollback esplicito.
Produzione, database produzione e flussi legacy non devono essere modificati.
