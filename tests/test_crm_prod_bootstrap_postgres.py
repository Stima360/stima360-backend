"""CRM PROD bootstrap (scripts/crm_prod_migrate.py) su PostgreSQL reale.

Due database usa-e-getta sul cluster LOCALE di P29_TEST_DSN:

  * `stima360_crm_prova`: nasce VUOTO e viene portato dal bootstrap fino
    all'ultima migration, come nascera' il CRM PROD su Render. Il nome, di
    proposito, non contiene 'test': questo canale rifiuta i nomi TEST, e il
    runner TEST rifiuta questo nome. I due canali non si sovrappongono.
  * `crm_prod_sito_prova`: lo schema del sito PROD, usato per provare che il
    bootstrap lo RIFIUTA e che il cron di SITE-IMPORT-1 gira davvero contro il
    database appena bootstrappato.

Nessuna email, nessuna rete, nessun database Render. Opt-in: senza
P29_TEST_DSN si salta tutto.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_crm_ops_3_acquisitions_postgres import _dsn_locale
from tests.test_site_import_1_postgres import COLONNE_DETTAGLIO_PROD

DSN = os.getenv("P29_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: serve PostgreSQL isolato")

ROOT = Path(__file__).resolve().parents[1]
CRM_DB = "stima360_crm_prova"
SITO_DB = "crm_prod_sito_prova"
OPERATORE = "bootstrap.prova"


def _dsn_per(nome):
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _canale():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import crm_prod_migrate
    return crm_prod_migrate


@pytest.fixture(scope="module")
def cluster():
    psycopg2 = pytest.importorskip("psycopg2")
    _dsn_locale(DSN)
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True

    def ricrea(nome):
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
            cur.execute(f'CREATE DATABASE "{nome}"')
        return _dsn_per(nome)

    crm_dsn = ricrea(CRM_DB)
    sito_dsn = ricrea(SITO_DB)
    # il sito: le tabelle legacy di database.py + 018/019 + le colonne PROD
    import database as legacy
    originale = legacy.get_connection
    legacy.get_connection = lambda: psycopg2.connect(sito_dsn)
    try:
        legacy.crea_tabella_stime()
        legacy.crea_tabella_stime_dettagliate()
        legacy.crea_tabella_zone_valori()
        legacy.migrazione_allinea_stime()
    finally:
        legacy.get_connection = originale
    sito = psycopg2.connect(sito_dsn)
    sito.autocommit = True
    with sito.cursor() as cur:
        for nome in ("018_stime_consenso_marketing", "019_stime_vistamaredettaglio"):
            cur.execute((ROOT / "migrations" / f"{nome}.sql").read_text(encoding="utf-8"))
        for colonna, tipo in COLONNE_DETTAGLIO_PROD.items():
            cur.execute(f"ALTER TABLE stime_dettagliate ADD COLUMN IF NOT EXISTS {colonna} {tipo}")
    # il certificato che il bootstrap scrive sotto reports/ e' della prova: via alla fine
    certificati_prima = set((ROOT / "reports").glob("p26_baseline_CRM_PROD_*"))
    try:
        yield {"psycopg2": psycopg2, "crm_dsn": crm_dsn, "sito_dsn": sito_dsn, "sito": sito}
    finally:
        for p in set((ROOT / "reports").glob("p26_baseline_CRM_PROD_*")) - certificati_prima:
            p.unlink()
        sito.close()
        for nome in (CRM_DB, SITO_DB):
            with servizio.cursor() as cur:
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
                cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _q(psycopg2, dsn, sql, p=None):
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, p)
            return cur.fetchall() if cur.description else None
    finally:
        conn.close()


def _env_db(monkeypatch, dsn):
    p = urlparse(dsn)
    monkeypatch.setenv("DB_HOST", p.hostname or (parse_qs(p.query).get("host") or [""])[0])
    monkeypatch.setenv("DB_PORT", str(p.port or 5432))
    monkeypatch.setenv("DB_USER", p.username or "postgres")
    monkeypatch.setenv("DB_PASSWORD", p.password or "")
    monkeypatch.setenv("DB_NAME", p.path.lstrip("/"))


# ---------------------------------------------------------------------------
# 1. le guardie: niente sito, niente TEST, niente nomi di comodo
# ---------------------------------------------------------------------------

def test_01_il_canale_rifiuta_il_sito_prod_il_test_e_i_nomi_sbagliati(cluster, monkeypatch, capsys):
    canale = _canale()
    monkeypatch.setenv("CRM_PROD_DATABASE_URL", cluster["crm_dsn"])
    # i nomi del sito PROD e i nomi TEST: rifiutati prima di qualunque connessione
    for nome in ("stima360_db", "stima360", "STIMA360_DB", "stima360_db_test", "crm_test", CRM_DB.upper()):
        assert canale.main(["bootstrap", "--database", nome, "--operator", OPERATORE]) == 1
        assert "BLOCKED" in capsys.readouterr().out
    # URL e nome che non coincidono
    assert canale.main(["bootstrap", "--database", "altro_nome", "--operator", OPERATORE]) == 1
    assert "but --database says" in capsys.readouterr().out
    # l'URL del CRM PROD che e' quello del sito (SITE_DB_URL): rifiutato
    monkeypatch.setenv("CRM_PROD_DATABASE_URL", cluster["sito_dsn"])
    monkeypatch.setenv("SITE_DB_URL", cluster["sito_dsn"])
    assert canale.main(["bootstrap", "--database", SITO_DB, "--operator", OPERATORE]) == 1
    assert "public site's database (SITE_DB_URL)" in capsys.readouterr().out
    # senza SITE_DB_URL il sito viene riconosciuto dal suo schema e rifiutato
    monkeypatch.delenv("SITE_DB_URL")
    assert canale.main(["bootstrap", "--database", SITO_DB, "--operator", OPERATORE]) == 1
    assert "SITE's database" in capsys.readouterr().out
    assert _q(cluster["psycopg2"], cluster["sito_dsn"],
              "SELECT to_regclass('public.crm_instance'), to_regclass('public.agencies')") == [(None, None)]
    # senza operatore, senza variabile
    monkeypatch.setenv("CRM_PROD_DATABASE_URL", cluster["crm_dsn"])
    assert canale.main(["bootstrap", "--database", CRM_DB]) == 1
    assert "--operator is required" in capsys.readouterr().out
    monkeypatch.delenv("CRM_PROD_DATABASE_URL")
    assert canale.main(["bootstrap", "--database", CRM_DB, "--operator", OPERATORE]) == 1
    assert "CRM_PROD_DATABASE_URL is not set" in capsys.readouterr().out
    # upgrade e status su un database vuoto: non e' CRM PROD
    monkeypatch.setenv("CRM_PROD_DATABASE_URL", cluster["crm_dsn"])
    assert canale.main(["upgrade", "--database", CRM_DB, "--operator", OPERATORE]) == 1
    assert "has not been bootstrapped" in capsys.readouterr().out
    assert canale.main(["status", "--database", CRM_DB]) == 1
    assert "classification  : empty" in capsys.readouterr().out
    # il database e' rimasto vuoto
    assert _q(cluster["psycopg2"], cluster["crm_dsn"],
              "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'") == [(0,)]


def test_02_il_runner_test_resta_intatto_e_rifiuta_il_nome_crm_prod():
    canale = _canale()
    runner = canale.runner
    for nome in ("stima360_db", "stima360", CRM_DB, "stima360_crm"):
        with pytest.raises(runner.GuardFailure):
            runner.assert_test_database_name(nome)
    assert runner.assert_test_database_name("stima360_db_test") == "stima360_db_test"
    # il canale importa il runner e non lo tocca: nessuna funzione di guardia riassegnata
    import inspect
    sorgente = (ROOT / "scripts" / "crm_prod_migrate.py").read_text(encoding="utf-8")
    assert "runner.assert_test_database_name =" not in sorgente
    assert "runner.PROD_DATABASE_NAMES =" not in sorgente
    assert "runner.REQUIRED_NAME_MARKER =" not in sorgente
    assert "monkeypatch" not in sorgente and "setattr(runner" not in sorgente
    assert inspect.getsourcefile(runner).endswith("scripts/p26_migrate.py")


# ---------------------------------------------------------------------------
# 2. il bootstrap vero, su un database vuoto
# ---------------------------------------------------------------------------

def test_03_bootstrap_da_vuoto_fino_all_ultima_migration(cluster, monkeypatch, capsys):
    canale = _canale()
    psycopg2 = cluster["psycopg2"]
    monkeypatch.setenv("CRM_PROD_DATABASE_URL", cluster["crm_dsn"])
    monkeypatch.setenv("SITE_DB_URL", cluster["sito_dsn"])
    assert canale.main(["bootstrap", "--database", CRM_DB, "--operator", OPERATORE]) == 0
    uscita = capsys.readouterr().out
    assert "010_owner_02_p1.sql  [gate -> stima360_crm_prova]" in uscita
    assert "= TEST baseline" in uscita
    assert "applied 026_p26_baseline" in uscita and "applied 095_site_import_ledger" in uscita
    assert "bootstrap       : ready" in uscita

    ultima = canale.runner.discover_migrations()[-1]
    q = lambda sql, p=None: _q(psycopg2, cluster["crm_dsn"], sql, p)
    # il marcatore
    assert q("SELECT instance_kind, database_name, bootstrap_state, created_by_operator, site_id_offset "
             "FROM crm_instance") == [("crm_prod", CRM_DB, "ready", OPERATORE, 1_000_000_000)]
    manifesto = q("SELECT pre_baseline_manifest FROM crm_instance")[0][0]
    assert [m["filename"] for m in manifesto][:3] == ["001_core_contacts_leads.sql", "002_property_01.sql",
                                                       "003_property_02.sql"]
    assert {m["filename"] for m in manifesto if m["gate_rewritten_to"]} == {"010_owner_02_p1.sql",
                                                                           "011_owner_02_p5.sql"}
    assert not any("_prod" in m["filename"] for m in manifesto)
    # il registro: 027..ultima (la 026 vive in schema_baseline, regola del runner),
    # tutte applicate dal nome di questo operatore
    versioni = [r[0] for r in q("SELECT version FROM schema_migrations ORDER BY version")]
    assert versioni[0] == "027_p26_agency_identity" and versioni[-1] == ultima.version
    assert len(versioni) == ultima.number - 27 + 1
    assert q("SELECT DISTINCT applied_by_operator, database_name FROM schema_migrations") == [(OPERATORE, CRM_DB)]
    base = q("SELECT baseline_version, snapshot_artifact, certified_by_operator, pre_baseline_tracked FROM schema_baseline")
    assert base[0][0] == "P26-BASELINE-001-CRM-PROD" and base[0][1].startswith("reports/p26_baseline_CRM_PROD_")
    assert base[0][2] == OPERATORE and base[0][3] is False
    assert (ROOT / base[0][1]).exists()
    # lo schema c'e' tutto: le tabelle di SITE-IMPORT-1, l'agenzia di default, le sequenze spostate
    assert q("SELECT to_regclass('public.site_import_records') IS NOT NULL")[0][0]
    assert q("SELECT slug FROM agencies ORDER BY id") == [("stima360",)]
    assert q("SELECT nextval('stime_id_seq'), nextval('stime_dettagliate_id_seq')") == [(1_000_000_000, 1_000_000_000)]
    # il marcatore non si cancella
    with pytest.raises(psycopg2.Error):
        q("DELETE FROM crm_instance")
    assert q("SELECT count(*) FROM crm_instance") == [(1,)]


def test_04_secondo_bootstrap_rifiutato_e_upgrade_senza_nulla_da_fare(cluster, monkeypatch, capsys):
    canale = _canale()
    monkeypatch.setenv("CRM_PROD_DATABASE_URL", cluster["crm_dsn"])
    assert canale.main(["bootstrap", "--database", CRM_DB, "--operator", OPERATORE]) == 1
    assert "already a bootstrapped CRM PROD database" in capsys.readouterr().out
    assert canale.main(["upgrade", "--database", CRM_DB, "--operator", OPERATORE]) == 0
    assert "nothing to apply" in capsys.readouterr().out
    assert canale.main(["status", "--database", CRM_DB]) == 0
    uscita = capsys.readouterr().out
    assert "classification  : crm_prod" in uscita and "pending" not in uscita
    # un file registrato non si modifica: il piano lo dice e l'upgrade si ferma
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    assert _q(cluster["psycopg2"], cluster["crm_dsn"],
              "SELECT checksum_up FROM schema_migrations WHERE version = %s",
              (tutte[-1].version,)) == [(tutte[-1].checksum_up,)]


# ---------------------------------------------------------------------------
# 3. il cron di SITE-IMPORT-1 sul database bootstrappato, come su Render
# ---------------------------------------------------------------------------

def test_05_il_cron_di_importazione_gira_sul_crm_prod_bootstrappato(cluster, monkeypatch):
    psycopg2 = cluster["psycopg2"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", SMTP_HOST="", SMTP_USER="", SMTP_PASS="",
               WHATSAPP_SERVICE_URL="", SITE_DB_URL=cluster["sito_dsn"])
    for chiave in ("SITE_PDF_GITHUB_REPO", "SITE_PDF_GITHUB_TOKEN"):
        env.pop(chiave, None)
    p = urlparse(cluster["crm_dsn"])
    env.update(DB_HOST=p.hostname or (parse_qs(p.query).get("host") or [""])[0], DB_PORT=str(p.port or 5432),
               DB_USER=p.username or "postgres", DB_PASSWORD=p.password or "", DB_NAME=CRM_DB)

    def cron(*argomenti):
        return subprocess.run([sys.executable, "-B", "run_site_import_cron.py", *argomenti], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=120)

    # La baseline precede ogni richiesta nuova anche sul CRM appena creato.
    iniziale = cron("--initialize-baseline")
    assert iniziale.returncode == 0, iniziale.stdout + iniziale.stderr
    with cluster["sito"].cursor() as cur:
        cur.execute("INSERT INTO stime (comune, microzona, via, civico, tipologia, mq, piano, locali, bagni, "
                    "nome, cognome, email, telefono, consenso_marketing, data) VALUES "
                    "('Alba Adriatica', 'Villa Fiore', 'Via Roma', '1', 'Appartamento', 85, '2', 3, 1, "
                    "'Mario', 'Rossi', 'bootstrap@example.invalid', '3330000000', TRUE, "
                    "LOCALTIMESTAMP - interval '1 day') RETURNING id")
        stima_id = cur.fetchone()[0]

    secco = cron("--dry-run")
    assert secco.returncode == 0, secco.stdout + secco.stderr
    assert "status=dry_run stime_to_process=1 dettagliate_to_process=0" in secco.stdout
    assert _q(psycopg2, cluster["crm_dsn"], "SELECT count(*) FROM stime") == [(0,)]

    vero = cron()
    # senza archivio PDF configurato il PDF resta da ritentare: la stima entra
    # (contatto, lead, immobile) ma il record e' `partial`, codice 2
    assert vero.returncode == 2, vero.stdout + vero.stderr
    assert "status=completed" in vero.stdout and "partial=1" in vero.stdout and "failed=0" in vero.stdout
    q = lambda sql, p=None: _q(psycopg2, cluster["crm_dsn"], sql, p)
    assert q("SELECT id, agency_id, email FROM stime") == [(stima_id, 1, "bootstrap@example.invalid")]
    assert q("SELECT count(*) FROM contacts") == [(1,)] and q("SELECT count(*) FROM leads") == [(1,)]
    assert q("SELECT status, crm_id, steps->'stima'->>'state', steps->'pdf'->>'state' FROM site_import_records") \
        == [("partial", stima_id, "done", "error")]
    # il sito non e' stato toccato e non ha ricevuto nulla del CRM
    assert _q(psycopg2, cluster["sito_dsn"], "SELECT to_regclass('public.site_import_records')") == [(None,)]
    # nessuna email: il CRM PROD non ha SMTP, e il cron non accoda messaggi
    assert q("SELECT count(*) FROM communication_messages") == [(0,)]
    # un secondo giro non duplica
    ancora = cron()
    assert "stime_imported=0" in ancora.stdout and "conflict=0" in ancora.stdout
    assert q("SELECT count(*) FROM stime") == [(1,)] and q("SELECT count(*) FROM contacts") == [(1,)]
    assert q("SELECT attempts FROM site_import_records") == [(2,)]
