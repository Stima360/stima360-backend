# BLOCCHI 4D–7 — runbook TEST: migrazioni 119 → 122

Solo **TEST** (`stima360_db_test`, servizio Render TEST `srv-d9agc4vaqgkc739jkjhg`). PROD esclusa.
Stesso metodo della 118: pacchetto temporaneo verificato, copia del runner, controlli in sola
lettura, apply separato, verifica finale. Nessun SQL manuale: il registro è quello del runner.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Codice online su TEST | `539780e` (migrazioni fino alla 118, 118 applicata) |
| 119–122 (+ 4 DOWN) | **preparate, non applicate** |
| Codice 4D–7 | commit locali su `core-0.1-test`, **nessun push** |
| Runner | SHA-256 `0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a` (identico in `539780e` e nel codice nuovo) |
| Pacchetto | `tar.gz` di 8 file, SHA-256 `366236d7e11ed518c660f409b675e2acbf53446fa18b2af9df03e3d7d745ad77` |

Checksum dei file:

```
bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab  119_whatsapp_auto_reply_simulation.sql
098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a  119_whatsapp_auto_reply_simulation_down.sql
08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6  120_property_visit_booking.sql
7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88  120_property_visit_booking_down.sql
04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962  121_visit_sheets.sql
10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d  121_visit_sheets_down.sql
c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03  122_signature_requests.sql
21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809  122_signature_requests_down.sql
```

## L'ordine

1. Controllo della variabile (sotto), trasferimento, **A** (sola lettura).
2. **B** (apply) dopo aver letto A; **C** (verifica finale).
3. Solo con «VERIFICA FINALE SUPERATA»: push di `core-0.1-test` e auto-deploy TEST.

Se il codice nuovo arrivasse prima delle migrazioni: le nuove sezioni rispondono «non installato»
(503) o restano vuote; nessun dato perso, ma la funzione non va dichiarata pronta.

## 0. Variabile `PUBLIC_BOOKING_IP_PEPPER` (stampa solo PRESENTE/ASSENTE)

```bash
python3 -c 'import os; print("PUBLIC_BOOKING_IP_PEPPER:", "PRESENTE" if (os.environ.get("PUBLIC_BOOKING_IP_PEPPER") or "").strip() else "ASSENTE")'
```

Se ASSENTE, le pagine pubbliche della visita (`/visita/...`) non funzionano: va impostata sul servizio
TEST prima della prova (è la stessa variabile della prenotazione pubblica già esistente).

## 1. Trasferimento (Shell Render TEST, dalla radice dell'app)

Si ferma se il runner o le 115–118 dell'app non sono quelli attesi, se l'app contiene già
migrazioni oltre la 118, se il pacchetto è alterato. Non esegue SQL e non modifica i file dell'app.

```bash
( set -eu
APP="$PWD"
RUNNER_SHA=0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a
test -f "$APP/scripts/p26_migrate.py" || { echo "STOP: eseguire dalla radice dell'app (manca scripts/p26_migrate.py)"; exit 1; }
echo "$RUNNER_SHA  $APP/scripts/p26_migrate.py" | sha256sum -c --quiet - || { echo "STOP: runner dell'app diverso da quello verificato"; exit 1; }
( cd "$APP/migrations" && sha256sum -c --quiet - <<'PRE'
793bc413333ffa74a9602fcc53d08084dfaca001ae1579622621f9d32cf86c8e  115_agency_logos.sql
4ac259c83dbfa9aaf9a8fa956cb5d40d08428551028d796ff71a5a2e2919860c  116_property_valuations.sql
558432a719968a17b4b7ad8f16c7191fe990c4486a0786e50e76e9432f8c0ed2  117_property_valuation_pdfs.sql
6abf4103a6d97eaa7aa253483a22a147281b17953bc145ef88c7cf0017a0234e  118_whatsapp_inbound.sql
PRE
) || { echo "STOP: le migrazioni 115-118 dell'app non sono quelle attese"; exit 1; }
test -f "$APP/migrations/118_whatsapp_inbound_down.sql" || { echo "STOP: manca la down della 118"; exit 1; }
if ls "$APP/migrations" | grep -Eq '^(119|1[2-9][0-9]|[2-9][0-9]{2})_'; then echo "STOP: l'app contiene gia' migrazioni oltre la 118 (commit online diverso da 539780e?)"; exit 1; fi
APP_BEFORE="$(cd "$APP" && find scripts migrations -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
D="$(mktemp -d /tmp/stima360_r47.XXXXXX)"
mkdir -p "$D/new" "$D/runner/scripts" "$D/runner/migrations"
base64 -d > "$D/pkg.tar.gz" <<'PKG'
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
PKG
echo "366236d7e11ed518c660f409b675e2acbf53446fa18b2af9df03e3d7d745ad77  $D/pkg.tar.gz" | sha256sum -c --quiet - || { echo "STOP: pacchetto alterato"; exit 1; }
tar -xzf "$D/pkg.tar.gz" -C "$D/new"
test "$(ls "$D/new" | wc -l)" -eq 8 || { echo "STOP: il pacchetto non contiene esattamente 8 file"; exit 1; }
( cd "$D/new" && sha256sum -c - <<'SUMS'
bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab  119_whatsapp_auto_reply_simulation.sql
098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a  119_whatsapp_auto_reply_simulation_down.sql
08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6  120_property_visit_booking.sql
7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88  120_property_visit_booking_down.sql
04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962  121_visit_sheets.sql
10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d  121_visit_sheets_down.sql
c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03  122_signature_requests.sql
21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809  122_signature_requests_down.sql
SUMS
)
cp "$APP/scripts/p26_migrate.py" "$D/runner/scripts/p26_migrate.py"
cp "$APP"/migrations/*.sql "$D/runner/migrations/"
cp "$D"/new/*.sql "$D/runner/migrations/"
echo "$RUNNER_SHA  $D/runner/scripts/p26_migrate.py" | sha256sum -c --quiet -
APP_AFTER="$(cd "$APP" && find scripts migrations -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
test "$APP_BEFORE" = "$APP_AFTER" || { echo "STOP: i file dell'app sono cambiati"; exit 1; }
echo "$D" > /tmp/stima360_r47_last
echo "OK: 119-122 (up + down) verificate in $D/new"
echo "OK: runner verificato e cartella di prova in $D/runner"
echo "OK: scripts/ e migrations/ dell'app invariati; nessun SQL eseguito"
)
```

**Atteso:** otto righe `…: OK` e tre righe `OK:`.

## A. Sola lettura: identità, status, plan

```bash
( set -eu
D="$(cat /tmp/stima360_r47_last)"; test -f "$D/runner/scripts/p26_migrate.py"
PY="$(command -v python || command -v python3)"
echo "0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a  $D/runner/scripts/p26_migrate.py" | sha256sum -c --quiet - || { echo "STOP: runner temporaneo alterato"; exit 1; }
( cd "$D/runner/migrations" && sha256sum -c --quiet - <<'SUMS'
bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab  119_whatsapp_auto_reply_simulation.sql
098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a  119_whatsapp_auto_reply_simulation_down.sql
08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6  120_property_visit_booking.sql
7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88  120_property_visit_booking_down.sql
04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962  121_visit_sheets.sql
10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d  121_visit_sheets_down.sql
c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03  122_signature_requests.sql
21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809  122_signature_requests_down.sql
SUMS
) || { echo "STOP: file 119-122 temporanei alterati"; exit 1; }
echo "== identita del database (sessione READ ONLY, nessuna credenziale stampata)"
echo "DB_HOST (env): $DB_HOST"
"$PY" - info <<'CHK' || true
import os, sys, psycopg2
MODE = sys.argv[1]                       # "info" | "pre" | "post"
EXPECTED_HOST = os.environ.get("ATTESO_HOST", "")
PRE = {'115_agency_logos': '793bc413333ffa74a9602fcc53d08084dfaca001ae1579622621f9d32cf86c8e', '116_property_valuations': '4ac259c83dbfa9aaf9a8fa956cb5d40d08428551028d796ff71a5a2e2919860c', '117_property_valuation_pdfs': '558432a719968a17b4b7ad8f16c7191fe990c4486a0786e50e76e9432f8c0ed2', '118_whatsapp_inbound': '6abf4103a6d97eaa7aa253483a22a147281b17953bc145ef88c7cf0017a0234e'}
NEW = {'119_whatsapp_auto_reply_simulation': ('bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab', '098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a'), '120_property_visit_booking': ('08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6', '7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88'), '121_visit_sheets': ('04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962', '10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d'), '122_signature_requests': ('c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03', '21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809')}
TABLES = ["whatsapp_auto_reply_settings", "whatsapp_auto_reply_pauses", "whatsapp_auto_reply_decisions",
          "property_visit_booking_links", "property_visit_bookings", "property_visit_booking_rate_limits",
          "visit_sheets", "signature_requests", "signature_request_events"]
TRIGGERS = ["trg_whatsapp_auto_reply_decisions_guard", "trg_property_visit_booking_links_guard",
            "trg_property_visit_bookings_guard", "trg_visit_sheets_guard", "trg_signature_requests_guard",
            "trg_signature_request_events_append_only"]
problems = []
def need(ok, msg):
    print(("OK   " if ok else "STOP ") + msg)
    if not ok:
        problems.append(msg)
host = os.getenv("DB_HOST")
need(os.getenv("DB_NAME") == "stima360_db_test", "DB_NAME del servizio = stima360_db_test")
if MODE in ("pre", "post"):
    need(bool(EXPECTED_HOST) and "INCOLLA" not in EXPECTED_HOST, "ATTESO_HOST impostato")
    need(host == EXPECTED_HOST, "DB_HOST del servizio = host TEST verificato")
conn = psycopg2.connect(host=host, port=os.getenv("DB_PORT", "5432"), dbname=os.getenv("DB_NAME"),
                        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
conn.set_session(readonly=True)
cur = conn.cursor()
cur.execute("SELECT current_database(), pg_is_in_recovery(), current_setting('transaction_read_only')")
db, recovery, ro = cur.fetchone()
need(db == "stima360_db_test", f"current_database = {db}")
need(recovery is False, "database primario (non in recovery)")
need(ro == "on", "sessione di controllo in sola lettura")
cur.execute("SELECT version, checksum_up, checksum_down, down_available, applied_by_operator, applied_at, "
            "rolled_back_at FROM schema_migrations WHERE version >= '115' ORDER BY version")
ledger = {r[0]: r for r in cur.fetchall()}
for v, s in PRE.items():
    row = ledger.get(v)
    need(row is not None and row[1].strip() == s and row[6] is None, f"{v} applicata con checksum atteso")
others = sorted(v for v in ledger if v not in PRE and v not in NEW)
need(others == [], "nessuna migrazione estranea registrata oltre la 118")
cur.execute("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL" for t in TABLES))
present = cur.fetchone()
if MODE == "pre":
    need(all(v not in ledger for v in NEW), "119-122 non ancora registrate")
    need(not any(present), "tabelle di 119-122 assenti")
elif MODE == "post":
    for v, (up, down) in NEW.items():
        row = ledger.get(v)
        need(row is not None and row[1].strip() == up and (row[2] or "").strip() == down and row[3] is True
             and row[6] is None, f"{v} registrata con checksum up/down attesi e down disponibile")
        if row is not None:
            print("     applicata da:", row[4], "il", row[5].isoformat())
    need(all(present), "nove tabelle di 119-122 presenti")
    cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(%s)", (TRIGGERS,))
    need(cur.fetchone()[0] == len(TRIGGERS), "sei trigger di guardia presenti")
    cur.execute("SELECT count(*) FROM pg_constraint WHERE conname IN ('whatsapp_auto_reply_decisions_unq', "
                "'property_visit_bookings_submission_unq', 'visit_sheets_version_unq', "
                "'signature_requests_simulated_never_signed_chk')")
    need(cur.fetchone()[0] == 4, "vincoli di unicita' e di simulazione presenti")
else:
    print("     119-122 registrate:", [v for v in NEW if v in ledger], "| tabelle presenti:", sum(present), "su", len(TABLES))
conn.rollback(); conn.close()
if problems:
    print("ESITO: CONTROLLI NON SUPERATI"); sys.exit(1)
print("ESITO: CONTROLLI SUPERATI")
CHK
echo "== status del runner (sola lettura)"
( cd "$D/runner" && "$PY" scripts/p26_migrate.py status ) || echo "(status ha segnalato problemi)"
echo "== plan del runner (solo validazione statica)"
( cd "$D/runner" && "$PY" scripts/p26_migrate.py plan --operator stefano.maggiorini | tail -3 ) || echo "(plan NON superato)"
)
```

**Atteso:** `ESITO: CONTROLLI SUPERATI` con «119-122 registrate: [] | tabelle presenti: 0 su 9»;
in `status` pendenti **solo** le quattro 119–122; `static validation: OK`. Annotare `DB_HOST (env)`
e confrontarlo con l'host TEST già identificato: va incollato in B e C.

## B. Apply (operatore `stefano.maggiorini`)

Sostituire `INCOLLA_QUI_L_HOST_TEST_VERIFICATO`. Prima dell'apply rifiuta: segnaposto non
compilato, database o host diversi, database in recovery, 115–118 mancanti o con checksum diverso,
migrazioni estranee registrate o **pendenti** (qualunque cosa oltre 119–122), 119–122 già
registrate, file temporanei alterati, `status` con problemi, `plan` non superato.

```bash
( set -eu
ATTESO_HOST="INCOLLA_QUI_L_HOST_TEST_VERIFICATO"; export ATTESO_HOST
D="$(cat /tmp/stima360_r47_last)"; test -f "$D/runner/scripts/p26_migrate.py"
PY="$(command -v python || command -v python3)"
echo "0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a  $D/runner/scripts/p26_migrate.py" | sha256sum -c --quiet - || { echo "STOP: runner temporaneo alterato"; exit 1; }
( cd "$D/runner/migrations" && sha256sum -c --quiet - <<'SUMS'
bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab  119_whatsapp_auto_reply_simulation.sql
098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a  119_whatsapp_auto_reply_simulation_down.sql
08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6  120_property_visit_booking.sql
7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88  120_property_visit_booking_down.sql
04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962  121_visit_sheets.sql
10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d  121_visit_sheets_down.sql
c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03  122_signature_requests.sql
21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809  122_signature_requests_down.sql
SUMS
) || { echo "STOP: file 119-122 temporanei alterati"; exit 1; }
echo "== controlli prima dell apply"
"$PY" - pre <<'CHK' || { echo "STOP: apply NON eseguito"; exit 1; }
import os, sys, psycopg2
MODE = sys.argv[1]                       # "info" | "pre" | "post"
EXPECTED_HOST = os.environ.get("ATTESO_HOST", "")
PRE = {'115_agency_logos': '793bc413333ffa74a9602fcc53d08084dfaca001ae1579622621f9d32cf86c8e', '116_property_valuations': '4ac259c83dbfa9aaf9a8fa956cb5d40d08428551028d796ff71a5a2e2919860c', '117_property_valuation_pdfs': '558432a719968a17b4b7ad8f16c7191fe990c4486a0786e50e76e9432f8c0ed2', '118_whatsapp_inbound': '6abf4103a6d97eaa7aa253483a22a147281b17953bc145ef88c7cf0017a0234e'}
NEW = {'119_whatsapp_auto_reply_simulation': ('bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab', '098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a'), '120_property_visit_booking': ('08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6', '7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88'), '121_visit_sheets': ('04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962', '10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d'), '122_signature_requests': ('c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03', '21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809')}
TABLES = ["whatsapp_auto_reply_settings", "whatsapp_auto_reply_pauses", "whatsapp_auto_reply_decisions",
          "property_visit_booking_links", "property_visit_bookings", "property_visit_booking_rate_limits",
          "visit_sheets", "signature_requests", "signature_request_events"]
TRIGGERS = ["trg_whatsapp_auto_reply_decisions_guard", "trg_property_visit_booking_links_guard",
            "trg_property_visit_bookings_guard", "trg_visit_sheets_guard", "trg_signature_requests_guard",
            "trg_signature_request_events_append_only"]
problems = []
def need(ok, msg):
    print(("OK   " if ok else "STOP ") + msg)
    if not ok:
        problems.append(msg)
host = os.getenv("DB_HOST")
need(os.getenv("DB_NAME") == "stima360_db_test", "DB_NAME del servizio = stima360_db_test")
if MODE in ("pre", "post"):
    need(bool(EXPECTED_HOST) and "INCOLLA" not in EXPECTED_HOST, "ATTESO_HOST impostato")
    need(host == EXPECTED_HOST, "DB_HOST del servizio = host TEST verificato")
conn = psycopg2.connect(host=host, port=os.getenv("DB_PORT", "5432"), dbname=os.getenv("DB_NAME"),
                        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
conn.set_session(readonly=True)
cur = conn.cursor()
cur.execute("SELECT current_database(), pg_is_in_recovery(), current_setting('transaction_read_only')")
db, recovery, ro = cur.fetchone()
need(db == "stima360_db_test", f"current_database = {db}")
need(recovery is False, "database primario (non in recovery)")
need(ro == "on", "sessione di controllo in sola lettura")
cur.execute("SELECT version, checksum_up, checksum_down, down_available, applied_by_operator, applied_at, "
            "rolled_back_at FROM schema_migrations WHERE version >= '115' ORDER BY version")
ledger = {r[0]: r for r in cur.fetchall()}
for v, s in PRE.items():
    row = ledger.get(v)
    need(row is not None and row[1].strip() == s and row[6] is None, f"{v} applicata con checksum atteso")
others = sorted(v for v in ledger if v not in PRE and v not in NEW)
need(others == [], "nessuna migrazione estranea registrata oltre la 118")
cur.execute("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL" for t in TABLES))
present = cur.fetchone()
if MODE == "pre":
    need(all(v not in ledger for v in NEW), "119-122 non ancora registrate")
    need(not any(present), "tabelle di 119-122 assenti")
elif MODE == "post":
    for v, (up, down) in NEW.items():
        row = ledger.get(v)
        need(row is not None and row[1].strip() == up and (row[2] or "").strip() == down and row[3] is True
             and row[6] is None, f"{v} registrata con checksum up/down attesi e down disponibile")
        if row is not None:
            print("     applicata da:", row[4], "il", row[5].isoformat())
    need(all(present), "nove tabelle di 119-122 presenti")
    cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(%s)", (TRIGGERS,))
    need(cur.fetchone()[0] == len(TRIGGERS), "sei trigger di guardia presenti")
    cur.execute("SELECT count(*) FROM pg_constraint WHERE conname IN ('whatsapp_auto_reply_decisions_unq', "
                "'property_visit_bookings_submission_unq', 'visit_sheets_version_unq', "
                "'signature_requests_simulated_never_signed_chk')")
    need(cur.fetchone()[0] == 4, "vincoli di unicita' e di simulazione presenti")
else:
    print("     119-122 registrate:", [v for v in NEW if v in ledger], "| tabelle presenti:", sum(present), "su", len(TABLES))
conn.rollback(); conn.close()
if problems:
    print("ESITO: CONTROLLI NON SUPERATI"); sys.exit(1)
print("ESITO: CONTROLLI SUPERATI")
CHK
STATUS="$(cd "$D/runner" && "$PY" scripts/p26_migrate.py status)" || { echo "$STATUS"; echo "STOP: status non superato, apply NON eseguito"; exit 1; }
echo "$STATUS" | tail -6
PENDING="$(echo "$STATUS" | awk '$1 == "pending" {print $2}' | tr "\n" " ")"
echo "$STATUS" | grep -Eq "PROBLEM|rolled back" && { echo "STOP: il registro segnala problemi o migrazioni annullate, apply NON eseguito"; exit 1; }
test "$PENDING" = "119_whatsapp_auto_reply_simulation 120_property_visit_booking 121_visit_sheets 122_signature_requests " || { echo "STOP: pendenti diverse da 119-122 (trovate: $PENDING), apply NON eseguito"; exit 1; }
( cd "$D/runner" && "$PY" scripts/p26_migrate.py plan --operator stefano.maggiorini | grep -q "static validation: OK" ) || { echo "STOP: plan non superato, apply NON eseguito"; exit 1; }
echo "== apply (runner normale, registro migrazioni)"
( cd "$D/runner" && "$PY" scripts/p26_migrate.py apply --operator stefano.maggiorini )
echo "== apply terminato: eseguire ora il comando di verifica finale"
)
```

## C. Verifica finale

Sostituire di nuovo il segnaposto con lo stesso host.

```bash
( set -eu
ATTESO_HOST="INCOLLA_QUI_L_HOST_TEST_VERIFICATO"; export ATTESO_HOST
D="$(cat /tmp/stima360_r47_last)"; test -f "$D/runner/scripts/p26_migrate.py"
PY="$(command -v python || command -v python3)"
echo "0a4fce2ec08d5bdc8dfb50a2312313bd51089c2f043aecc87f33dec73b72249a  $D/runner/scripts/p26_migrate.py" | sha256sum -c --quiet - || { echo "STOP: runner temporaneo alterato"; exit 1; }
( cd "$D/runner/migrations" && sha256sum -c --quiet - <<'SUMS'
bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab  119_whatsapp_auto_reply_simulation.sql
098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a  119_whatsapp_auto_reply_simulation_down.sql
08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6  120_property_visit_booking.sql
7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88  120_property_visit_booking_down.sql
04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962  121_visit_sheets.sql
10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d  121_visit_sheets_down.sql
c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03  122_signature_requests.sql
21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809  122_signature_requests_down.sql
SUMS
) || { echo "STOP: file 119-122 temporanei alterati"; exit 1; }
echo "== verifica finale (sessione READ ONLY)"
"$PY" - post <<'CHK' || { echo "VERIFICA NON SUPERATA"; exit 1; }
import os, sys, psycopg2
MODE = sys.argv[1]                       # "info" | "pre" | "post"
EXPECTED_HOST = os.environ.get("ATTESO_HOST", "")
PRE = {'115_agency_logos': '793bc413333ffa74a9602fcc53d08084dfaca001ae1579622621f9d32cf86c8e', '116_property_valuations': '4ac259c83dbfa9aaf9a8fa956cb5d40d08428551028d796ff71a5a2e2919860c', '117_property_valuation_pdfs': '558432a719968a17b4b7ad8f16c7191fe990c4486a0786e50e76e9432f8c0ed2', '118_whatsapp_inbound': '6abf4103a6d97eaa7aa253483a22a147281b17953bc145ef88c7cf0017a0234e'}
NEW = {'119_whatsapp_auto_reply_simulation': ('bc6d057be6ddbb6c6a530a7be861831db510067d1361b85054f82d9d57a16fab', '098385ff7634d39f7d56fd9835b21292889b626a665960cde0ffc308c5ad8c8a'), '120_property_visit_booking': ('08e659361a0feeec2d95d4e7ff42484535298db5a7e45bafe566bcc40de8d9a6', '7c6888579cb634816af071bc56c6c9b13ebaa540f5ed2b3b95c516afce271c88'), '121_visit_sheets': ('04065b278e82af7aa8e1a1d5bc438ce4231e0b0a14b03bdae6999ce3f1ac3962', '10a2439ae21440a54863dc302a286766e0e4c9953daece06a05a79e79fff6c6d'), '122_signature_requests': ('c81361bc4b3a9e5d2b4c977d1315d08e7a43bbddefc633f83cb29ba037bc4c03', '21432b973d1fa1b27bd26aed51c0bcc2db6bb4cbf60066a02d49afb6115f7809')}
TABLES = ["whatsapp_auto_reply_settings", "whatsapp_auto_reply_pauses", "whatsapp_auto_reply_decisions",
          "property_visit_booking_links", "property_visit_bookings", "property_visit_booking_rate_limits",
          "visit_sheets", "signature_requests", "signature_request_events"]
TRIGGERS = ["trg_whatsapp_auto_reply_decisions_guard", "trg_property_visit_booking_links_guard",
            "trg_property_visit_bookings_guard", "trg_visit_sheets_guard", "trg_signature_requests_guard",
            "trg_signature_request_events_append_only"]
problems = []
def need(ok, msg):
    print(("OK   " if ok else "STOP ") + msg)
    if not ok:
        problems.append(msg)
host = os.getenv("DB_HOST")
need(os.getenv("DB_NAME") == "stima360_db_test", "DB_NAME del servizio = stima360_db_test")
if MODE in ("pre", "post"):
    need(bool(EXPECTED_HOST) and "INCOLLA" not in EXPECTED_HOST, "ATTESO_HOST impostato")
    need(host == EXPECTED_HOST, "DB_HOST del servizio = host TEST verificato")
conn = psycopg2.connect(host=host, port=os.getenv("DB_PORT", "5432"), dbname=os.getenv("DB_NAME"),
                        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))
conn.set_session(readonly=True)
cur = conn.cursor()
cur.execute("SELECT current_database(), pg_is_in_recovery(), current_setting('transaction_read_only')")
db, recovery, ro = cur.fetchone()
need(db == "stima360_db_test", f"current_database = {db}")
need(recovery is False, "database primario (non in recovery)")
need(ro == "on", "sessione di controllo in sola lettura")
cur.execute("SELECT version, checksum_up, checksum_down, down_available, applied_by_operator, applied_at, "
            "rolled_back_at FROM schema_migrations WHERE version >= '115' ORDER BY version")
ledger = {r[0]: r for r in cur.fetchall()}
for v, s in PRE.items():
    row = ledger.get(v)
    need(row is not None and row[1].strip() == s and row[6] is None, f"{v} applicata con checksum atteso")
others = sorted(v for v in ledger if v not in PRE and v not in NEW)
need(others == [], "nessuna migrazione estranea registrata oltre la 118")
cur.execute("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL" for t in TABLES))
present = cur.fetchone()
if MODE == "pre":
    need(all(v not in ledger for v in NEW), "119-122 non ancora registrate")
    need(not any(present), "tabelle di 119-122 assenti")
elif MODE == "post":
    for v, (up, down) in NEW.items():
        row = ledger.get(v)
        need(row is not None and row[1].strip() == up and (row[2] or "").strip() == down and row[3] is True
             and row[6] is None, f"{v} registrata con checksum up/down attesi e down disponibile")
        if row is not None:
            print("     applicata da:", row[4], "il", row[5].isoformat())
    need(all(present), "nove tabelle di 119-122 presenti")
    cur.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(%s)", (TRIGGERS,))
    need(cur.fetchone()[0] == len(TRIGGERS), "sei trigger di guardia presenti")
    cur.execute("SELECT count(*) FROM pg_constraint WHERE conname IN ('whatsapp_auto_reply_decisions_unq', "
                "'property_visit_bookings_submission_unq', 'visit_sheets_version_unq', "
                "'signature_requests_simulated_never_signed_chk')")
    need(cur.fetchone()[0] == 4, "vincoli di unicita' e di simulazione presenti")
else:
    print("     119-122 registrate:", [v for v in NEW if v in ledger], "| tabelle presenti:", sum(present), "su", len(TABLES))
conn.rollback(); conn.close()
if problems:
    print("ESITO: CONTROLLI NON SUPERATI"); sys.exit(1)
print("ESITO: CONTROLLI SUPERATI")
CHK
STATUS="$(cd "$D/runner" && "$PY" scripts/p26_migrate.py status)"
echo "$STATUS" | tail -5
echo "$STATUS" | grep -Eq "pending|PROBLEM" && { echo "VERIFICA NON SUPERATA: il runner segnala pendenti o problemi"; exit 1; }
echo "VERIFICA FINALE SUPERATA: 119-122 applicate e registrate"
)
```

**Atteso:** `VERIFICA FINALE SUPERATA: 119-122 applicate e registrate`.

## Prova locale dei comandi (eseguita)

App al commit online `539780e`, database locale `stima360_db_test` costruito come il TEST
(tabelle storiche, 001–025, runner reale fino alla 118):
- trasferimento: 8 file integri, app invariata;
- A: identità corretta, 115–118 con checksum attesi, pendenti solo 119–122, plan OK;
- B rifiutato, senza scrivere, con: segnaposto, host diverso, migrazione estranea pendente
  (123 di prova), file 121 alterato; poi apply riuscito; un secondo apply rifiutato;
- C: quattro migrazioni registrate con checksum up/down e down disponibile, 9 tabelle, 6 trigger,
  vincoli presenti;
- il runner del codice nuovo vede 119–122 «applied» senza differenze di checksum.

## Rientro

Il codice `539780e` ignora le tabelle nuove. Le DOWN (122 → 119, in ordine inverso) **rifiutano** se
esistono dati (decisioni, pause, collegamenti, prenotazioni, fogli, richieste di firma): conservarli o
ripristinare da un backup verificato.

## Schermate da provare dopo il deploy (dati di prova)

1. **Messaggi WhatsApp** → una conversazione: pannello «Risposte automatiche» con «arresto generale
   attivo» (la variabile `WHATSAPP_AUTO_REPLY_MODE` resta assente: nessuna automazione accesa).
2. **Immobile con incarico in corso** → **Visite** → «Prenotazione online delle visite» →
   «Nuovo collegamento» (agente con orario settimanale, autorizzazione del proprietario) → copiare il
   collegamento.
3. In una finestra privata: `/visita/<token>` → orario → dati → esito a schermo → collegamento di
   gestione `/visita-gestione/<chiave>` → «Sposta» e «Annulla».
4. **Agenda** e **Immobile → Visite**: la stessa visita, poi annullata/spostata.
5. **Immobile → Visite → Fogli visita** → «Genera foglio visita» → «Scarica PDF» (dice «prenotata»,
   non «svolta») → «Richiedi firma (simulazione)» → «Simula completata» → ricevuta
   «SIMULAZIONE — NESSUNA FIRMA VALIDA».
6. **Portale proprietario** (`/owner`, account di prova) → immobile → «Report attività» →
   «Mostra report» → «Scarica PDF»; revocare l'accesso dall'amministrazione proprietari →
   ricaricare: report e PDF non più disponibili.
7. Anche a 390 px di larghezza: pagina `/visita/...` e portale proprietario.
