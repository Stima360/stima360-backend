STIMA360 MATCH 0.2 — PACCHETTO AMBIENTE TEST

PERIMETRO
- Solo branch core-0.1-test.
- Solo stima360-db-test.
- Solo stima360-backend-test.
- Produzione invariata.
- Nessuna modifica a CORE, PROPERTY, BUY, legacy o main.py cumulativo.

FUNZIONI
- rilevazione match stale usando buy_requests.updated_at e properties.updated_at;
- refresh sincrono di singolo match, richiesta BUY, immobile o stale massivi;
- refresh/stale: limit predefinito 50, minimo 1, massimo 200;
- preservazione di stato commerciale, override, priorità, interazioni e feedback;
- storico differenze tra ricalcoli;
- feedback essenziale agent/buyer;
- dashboard fresh, stale, failed e review_required;
- nessuna coda persistente, AI, FLOW o automazione di invio.

FILE DA CARICARE
match/
static/match_admin/
migrations/007_match_02.sql
migrations/007_match_02_down.sql
tests/

Non sostituire main.py. Il router MATCH e /match-admin/ sono già presenti nel main cumulativo validato.

ORDINE DI DISTRIBUZIONE
1. Caricare i file del pacchetto nel branch core-0.1-test.
2. Nella Shell di stima360-backend-test applicare 007_match_02.sql esclusivamente a stima360-db-test.
3. Eseguire i test automatici.
4. Distribuire esclusivamente stima360-backend-test.
5. Verificare /match-admin/ e /api/match/dashboard.

COMANDO MIGRAZIONE — SOLO SHELL stima360-backend-test
python - <<'PY'
import os, psycopg2
name = os.environ['DB_NAME']
if 'test' not in name.lower():
    raise SystemExit('BLOCCATO: DB_NAME non è test')
conn = psycopg2.connect(
    host=os.environ['DB_HOST'],
    port=os.environ.get('DB_PORT','5432'),
    dbname=name,
    user=os.environ['DB_USER'],
    password=os.environ['DB_PASSWORD'],
)
try:
    with open('migrations/007_match_02.sql', encoding='utf-8') as f:
        sql=f.read()
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print('MIGRAZIONE MATCH 0.2 COMPLETATA SU', name)
finally:
    conn.close()
PY

COMANDO TEST
python -m pytest -q tests/test_match_engine.py tests/test_match_02_schemas.py tests/test_match_02_helpers.py tests/test_match_02_isolation.py

VERIFICA DATABASE
python - <<'PY'
import os, psycopg2
name=os.environ['DB_NAME']
if 'test' not in name.lower():
    raise SystemExit('BLOCCATO: DB_NAME non è test')
conn=psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ.get('DB_PORT','5432'),dbname=name,user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
try:
    with conn.cursor() as cur:
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public' AND table_name IN ('match_refresh_history','match_feedback')
                       ORDER BY table_name""")
        print('TABELLE:', [x[0] for x in cur.fetchall()])
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='matches'
                         AND column_name IN ('freshness_status','stale_reason','stale_since','last_successful_run_at','last_failed_run_at','recalculation_error','buy_version_at_calculation','property_version_at_calculation','review_required')
                       ORDER BY column_name""")
        print('COLONNE:', [x[0] for x in cur.fetchall()])
finally:
    conn.close()
PY

ROLLBACK — SOLO stima360-db-test, SE NECESSARIO
Applicare migrations/007_match_02_down.sql con lo stesso metodo della migrazione.
Il rollback elimina solo strutture MATCH 0.2. Preserva matches, match_runs, esclusioni e risultati MATCH 0.1.
Gli stati visit_scheduled e offer_candidate vengono ricondotti rispettivamente a visit_requested e visited prima di ripristinare il vincolo MATCH 0.1.
