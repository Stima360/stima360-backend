STIMA360 BUY 0.2 — AMBIENTE TEST

Aggiunge al modulo BUY 0.1:
- workflow operativo basato sui match reali;
- immobili proposti, scartati, interessanti e visitati;
- motivi di rifiuto strutturati;
- prossima azione e revisione finanziaria;
- storico completo della ricerca;
- creazione e collegamento di task CORE;
- KPI operativi BUY.

Migrazione test: migrations/006_buy_02.sql
Rollback: migrations/006_buy_02_down.sql

Ordine:
1. caricare i file sul branch core-0.1-test;
2. applicare 006_buy_02.sql solo a stima360-db-test;
3. distribuire solo stima360-backend-test;
4. aprire /buy-admin/.

Nessuna modifica a produzione, CORE, PROPERTY, MATCH o legacy.
