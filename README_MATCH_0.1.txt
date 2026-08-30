STIMA360 MATCH 0.1 — AMBIENTE TEST

Contenuto:
- modulo API match/
- UI /match-admin/
- migrazione 005_match_01.sql
- rollback 005_match_01_down.sql
- main.py aggiornato solo con router e mount MATCH
- test deterministici del motore

Ordine ambiente test:
1. caricare i file sul branch core-0.1-test
2. applicare migrations/005_match_01.sql esclusivamente a stima360-db-test
3. distribuire esclusivamente stima360-backend-test
4. aprire /match-admin/

Non eseguire il rollback dopo una migrazione riuscita. Nessuna modifica a produzione.
