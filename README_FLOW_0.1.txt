STIMA360 FLOW 0.1 — PACCHETTO AMBIENTE TEST

AMBIENTI AUTORIZZATI
- Database: stima360-db-test
- Backend: stima360-backend-test
- Branch: core-0.1-test
- Produzione: NON TOCCARE

FUNZIONI MVP
- 7 regole predefinite e versionate nel codice (FLOW-R001 ... FLOW-R007)
- nessuna creazione libera di regole dalla UI/API
- modifica limitata ai parametri in allowlist
- simulazione positiva obbligatoria prima dell'attivazione
- invalidazione della simulazione dopo modifica parametri/versione
- creazione di task CORE interni e identificabili
- idempotenza e cooldown
- soppressioni temporanee
- audit di eventi, esecuzioni e azioni
- massimo 3 retry; quarto retry rifiutato con HTTP 409
- scan sincrono: default 50, massimo 200
- nessun WhatsApp, email, AI, FLOW esterno o scheduler obbligatorio

FILE DA CARICARE
- flow/
- static/flow_admin/
- migrations/008_flow_01.sql
- migrations/008_flow_01_down.sql
- tests/test_flow_*.py
- run_flow_01_e2e.py

MAIN.PY
Non è incluso per evitare sovrascritture del main cumulativo.
Applicare soltanto le righe contenute in INTEGRAZIONE_MAIN_CUMULATIVA.txt.

ORDINE DI DISTRIBUZIONE
1. Caricare il pacchetto nel branch core-0.1-test.
2. Applicare esclusivamente le integrazioni cumulative al main.py test.
3. Deployare soltanto stima360-backend-test.
4. Aprire la Shell di stima360-backend-test.
5. Applicare la migrazione 008 soltanto a stima360-db-test.
6. Eseguire i test automatici.
7. Aprire /flow-admin/.
8. Eseguire il test E2E live.

COMANDO MIGRAZIONE (SOLO SHELL BACKEND TEST)
python - <<'PY'
import os, psycopg2
name=os.environ['DB_NAME']
if 'test' not in name.lower():
    raise SystemExit('BLOCCATO: database non test')
conn=psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ.get('DB_PORT','5432'),dbname=name,user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
try:
    with open('migrations/008_flow_01.sql',encoding='utf-8') as f: sql=f.read()
    with conn:
        with conn.cursor() as cur: cur.execute(sql)
    print('MIGRAZIONE FLOW 0.1 COMPLETATA SU',name)
finally:
    conn.close()
PY

TEST UNITARI/INTEGRAZIONE
Se pytest non è disponibile nella Shell:
pip install pytest

Poi:
python -m pytest -q tests/test_flow_rules.py tests/test_flow_engine.py tests/test_flow_schemas.py tests/test_flow_isolation.py tests/test_flow_service.py tests/test_flow_ui.py tests/test_flow_e2e_harness.py

Esito locale del pacchetto: 32 passed.

TEST E2E LIVE (SOLO DOPO DEPLOY E MIGRAZIONE TEST)
python run_flow_01_e2e.py

Il test E2E:
- blocca endpoint e database non test;
- crea un contatto e un lead con prefisso E2E_FLOW01_;
- verifica il blocco di attivazione senza simulazione;
- verifica simulazione positiva senza task;
- attiva FLOW-R001;
- crea un evento e un solo task CORE;
- verifica idempotenza;
- verifica invalidazione dopo modifica parametri;
- verifica il blocco del quarto retry;
- ripristina la configurazione della regola;
- elimina esclusivamente i dati E2E creati.

UI
https://stima360-backend-test.onrender.com/flow-admin/

ROLLBACK
1. Disattivare tutte le regole FLOW.
2. Ripristinare il main.py cumulativo senza router/mount FLOW.
3. Ripristinare il codice precedente.
4. Applicare migrations/008_flow_01_down.sql soltanto sul DB test.

Il rollback non elimina i task CORE già creati da FLOW. Sono riconoscibili nei metadata:
- source = flow
- flow_rule_code
- flow_execution_id

ISOLAMENTO
La migrazione crea esclusivamente tabelle flow_* e non altera CORE, PROPERTY, BUY, MATCH o legacy.
