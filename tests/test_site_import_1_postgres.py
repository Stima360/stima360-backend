"""SITE-IMPORT-1 su PostgreSQL reale: DUE database usa-e-getta.

  * il "sito": lo schema del backend PROD (`database.py` + 018/019 + le colonne
    che `main` PROD scrive in `stime_dettagliate`), letto in SOLA LETTURA;
  * il CRM: lo schema completo fino all'ultima migration (fixture `completo`).

Nessuna email, nessun WhatsApp, nessuna rete: `invia_mail` esplode se chiamata,
l'archivio dei PDF e' un doppio con gli esiti veri del modulo.

Opt-in: senza `P29_TEST_DSN` (cluster LOCALE) si salta tutto.
"""
from __future__ import annotations

import hashlib
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, completo  # noqa: F401 - fixture
from tests.test_censimento_1_fullschema_postgres import _dsn_per

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: serve PostgreSQL isolato")

ROOT = Path(__file__).resolve().parents[1]
SITO_DB = "site_import_sito_test"
PDF = b"%PDF-1.4\n% PDF ORIGINALE DEL SITO\n%%EOF\n"

#: Le colonne che `main` PROD scrive in `stime_dettagliate` (salva_stima_dettagliata).
COLONNE_DETTAGLIO_PROD = {
    "nome": "VARCHAR(50)", "cognome": "VARCHAR(50)", "email": "VARCHAR(100)", "telefono": "VARCHAR(30)",
    "indirizzo": "VARCHAR(200)", "stato": "VARCHAR(40)", "anno": "INTEGER", "ascensore": "VARCHAR(10)",
    "pertinenze": "VARCHAR(200)", "tipologia": "VARCHAR(50)", "mq": "INTEGER", "piano": "VARCHAR(30)",
    "locali": "INTEGER", "bagni": "INTEGER", "microzona": "VARCHAR(100)", "posizionemare": "VARCHAR(40)",
    "distanzamare": "VARCHAR(40)", "barrieramare": "VARCHAR(40)", "mqgiardino": "INTEGER",
    "mqgarage": "INTEGER", "vistamare": "VARCHAR(100)", "altrodescrizione": "TEXT", "mqcantina": "INTEGER",
    "mqpostoauto": "INTEGER", "mqtaverna": "INTEGER", "mqsoffitta": "INTEGER", "mqterrazzo": "INTEGER",
    "numbalconi": "INTEGER",
}


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sito_db(completo):
    psycopg2 = completo["psycopg2"]
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{SITO_DB}"')
        cur.execute(f'CREATE DATABASE "{SITO_DB}"')
    dsn = _dsn_per(SITO_DB)
    import database as legacy
    originale = legacy.get_connection
    legacy.get_connection = lambda: psycopg2.connect(dsn)
    try:
        legacy.crea_tabella_stime()
        legacy.crea_tabella_stime_dettagliate()
        legacy.crea_tabella_zone_valori()
        legacy.migrazione_allinea_stime()
    finally:
        legacy.get_connection = originale
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        for nome in ("018_stime_consenso_marketing", "019_stime_vistamaredettaglio"):
            cur.execute((ROOT / "migrations" / f"{nome}.sql").read_text(encoding="utf-8"))
        for colonna, tipo in COLONNE_DETTAGLIO_PROD.items():
            cur.execute(f"ALTER TABLE stime_dettagliate ADD COLUMN IF NOT EXISTS {colonna} {tipo}")
        # Lo schema PROD vero conserva locali e anno della stima come TEXT,
        # e anno della dettagliata come TEXT: la fixture deve rifletterlo.
        cur.execute("ALTER TABLE stime ALTER COLUMN locali TYPE text USING locali::text")
        cur.execute("ALTER TABLE stime ALTER COLUMN anno TYPE text USING anno::text")
        cur.execute("ALTER TABLE stime_dettagliate ALTER COLUMN anno TYPE text USING anno::text")
        # gli id del sito partono lontano da quelli locali del CRM di prova
        cur.execute("SELECT setval('stime_id_seq', 5000)")
        cur.execute("SELECT setval('stime_dettagliate_id_seq', 7000)")
    try:
        yield {"dsn": dsn, "conn": conn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (SITO_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{SITO_DB}"')
        servizio.close()


class FakeArchive:
    """Gli esiti VERI di `pdf_archive.Esito`, senza rete."""

    configured = True

    def __init__(self):
        self.pdf = {}
        self.calls = []

    def fetch(self, stima_id, created_at):
        from site_import.pdf_archive import Esito
        self.calls.append(stima_id)
        if stima_id in self.pdf:
            dati = self.pdf[stima_id]
            return Esito("ready", pdf=dati, provenance={
                "origin": "site_archive", "path": f"stima_{stima_id}.pdf", "commit": "abc",
                "sha256": hashlib.sha256(dati).hexdigest()})
        return Esito("missing", reason="archive_path_missing")


@pytest.fixture
def mondo(completo, sito_db, monkeypatch):
    psycopg2 = completo["psycopg2"]
    crm_dsn = completo["dsn"]

    def crm():
        return psycopg2.connect(crm_dsn)

    # Ogni modulo che ha importato `get_connection` parla col CRM usa-e-getta.
    for modulo in list(sys.modules.values()):
        nome = getattr(modulo, "__name__", "") or ""
        if nome.startswith("tests."):
            continue
        if getattr(modulo, "get_connection", None) is not None:
            try:
                monkeypatch.setattr(modulo, "get_connection", crm)
            except (AttributeError, TypeError):
                pass
    import database as legacy
    from core import database as core_db
    from consent import database as consent_db
    from communication import database as comm_db
    from seller_intelligence import database as si_db
    for aiutante in (core_db.core_cursor, consent_db.consent_cursor, comm_db.communication_cursor,
                     si_db.si_cursor):
        globali = getattr(aiutante, "__wrapped__", aiutante).__globals__
        if "get_connection" in globali:
            monkeypatch.setitem(globali, "get_connection", crm)

    def nessuna_email(*a, **k):
        raise AssertionError("SITE-IMPORT-1 non deve mandare email")

    monkeypatch.setattr(legacy, "invia_mail", nessuna_email)
    from communication.providers import email_smtp
    monkeypatch.setattr(email_smtp, "invia_mail", nessuna_email)

    sito = sito_db["conn"]

    def q_sito(sql, p=None):
        with sito.cursor() as cur:
            cur.execute(sql, p)
            return cur.fetchall() if cur.description else None

    def q_crm(sql, p=None):
        c = crm()
        try:
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute(sql, p)
                return cur.fetchall() if cur.description else None
        finally:
            c.close()

    # pulizia del sito e del registro del CRM (lo schema resta)
    # Gli id del sito NON ripartono: le stime importate dai test precedenti
    # restano nel CRM, e un id riusato sarebbe (giustamente) un conflitto.
    q_sito("TRUNCATE stime_dettagliate, stime CASCADE")

    def stima(*, minuti_fa=60 * 24 * 30, **campi):
        base = {"comune": "Alba Adriatica", "microzona": "Villa Fiore", "via": "Via Roma", "civico": "1",
                "tipologia": "Appartamento", "mq": 85, "piano": "2", "locali": 3, "bagni": 1,
                "pertinenze": "garage", "ascensore": "Sì", "nome": "Mario", "cognome": "Rossi",
                "email": f"cliente{len(q_sito('SELECT id FROM stime'))}@example.invalid",
                "telefono": "3330000000", "fascia_mare": "oltre_800m", "stato": "buono", "anno": 1995,
                "mqgarage": 18, "consenso_marketing": True}
        base.update(campi)
        colonne = list(base)
        rid = q_sito(f"INSERT INTO stime ({', '.join(colonne)}, data) VALUES "
                     f"({', '.join(['%s'] * len(colonne))}, LOCALTIMESTAMP - make_interval(mins => %s)) RETURNING id",
                     (*base.values(), minuti_fa))[0][0]
        return rid

    def dettaglio(stima_id, *, minuti_fa=60 * 24 * 29, **campi):
        base = {"stima_id": stima_id, "classe": "C", "riscaldamento": "autonomo", "esposizione": "sud",
                "note": "Disponibile il pomeriggio", "contatto": "email",
                "sopralluogo": datetime(2027, 1, 15, 10, 30), "nome": "Mario", "cognome": "Rossi"}
        base.update(campi)
        colonne = list(base)
        return q_sito(f"INSERT INTO stime_dettagliate ({', '.join(colonne)}, data) VALUES "
                      f"({', '.join(['%s'] * len(colonne))}, LOCALTIMESTAMP - make_interval(mins => %s)) RETURNING id",
                      (*base.values(), minuti_fa))[0][0]

    archivio = FakeArchive()

    def giro(**kw):
        from site_import.config import Config
        from site_import.service import Importer
        from site_import.source import SiteSource
        config = Config(site_db_url=sito_db["dsn"], settle_minutes=10, batch=kw.pop("batch", 100),
                        followup_max_age_hours=72)
        with SiteSource(sito_db["dsn"]) as source:
            return Importer(config, source=source, archive=kw.pop("archivio", archivio),
                            connect=crm).run(**kw)

    def ledger(table="stime"):
        return {r[0]: r[1:] for r in q_crm(
            "SELECT source_id, status, steps, last_error FROM site_import_records WHERE source_table = %s", (table,))}

    yield {"stima": stima, "dettaglio": dettaglio, "giro": giro, "q_sito": q_sito, "q_crm": q_crm,
           "archivio": archivio, "ledger": ledger, "crm": crm, "sito_dsn": sito_db["dsn"]}

    # pulizia del CRM: solo cio' che l'importazione ha creato (id >= 5000)
    for sql in (
        "DELETE FROM appointment_events WHERE appointment_id IN (SELECT id FROM appointments WHERE source = 'legacy_stime_dettagliate')",
        "ALTER TABLE appointments DISABLE TRIGGER trg_appointments_refuse_delete",
        "DELETE FROM appointments WHERE source = 'legacy_stime_dettagliate'",
        "ALTER TABLE appointments ENABLE TRIGGER trg_appointments_refuse_delete",
        "DELETE FROM site_import_records",
    ):
        try:
            q_crm(sql)
        except Exception:  # noqa: BLE001 - pulizia best effort di un DB usa-e-getta
            pass


def conta(m, tabella, dove="TRUE", p=None):
    return m["q_crm"](f"SELECT count(*) FROM {tabella} WHERE {dove}", p)[0][0]


# ===========================================================================
# 1. LO STORICO, CON L'IDENTITA' ORIGINALE
# ===========================================================================

def test_01_storico_stima_contatto_lead_immobile_pdf_dettaglio_agenda(mondo):
    a = mondo["stima"]()
    b = mondo["stima"](email=None, telefono="3339999999", mqgarage=None, pertinenze="")
    d = mondo["dettaglio"](a)
    mondo["archivio"].pdf[a] = PDF

    esito = mondo["giro"]()
    assert esito["status"] == "completed", esito
    assert esito["stime_imported"] == 2 and esito["dettagliate_imported"] == 1, esito

    # stessa identita': id del CRM = id del sito
    crm = dict(mondo["q_crm"]("SELECT id, email FROM stime WHERE id IN (%s, %s)", (a, b)))
    assert set(crm) == {a, b}
    assert mondo["q_crm"]("SELECT stima_id FROM stime_dettagliate WHERE id = %s", (d,))[0][0] == a

    # contatto + lead + collegamento
    for sid in (a, b):
        assert conta(mondo, "lead_stime", "stima_id = %s", (sid,)) == 1
    # immobile con pertinenza (garage 18 mq)
    pid = mondo["q_crm"]("SELECT property_id FROM property_site_sources WHERE stima_id = %s AND status = 'active'", (a,))[0][0]
    assert conta(mondo, "property_accessories", "property_id = %s", (pid,)) >= 1

    # PDF originale, byte per byte; per B segnalato mancante, nessun PDF inventato
    stato, dati, payload = mondo["q_crm"](
        "SELECT status, pdf_bytes, render_payload FROM stima_pdf_artifacts WHERE stima_id = %s", (a,))[0]
    assert stato == "ready" and bytes(dati) == PDF and payload["origin"] == "site_archive"
    stato_b, dati_b, errore_b = mondo["q_crm"](
        "SELECT status, pdf_bytes, last_error FROM stima_pdf_artifacts WHERE stima_id = %s", (b,))[0]
    assert stato_b == "failed" and dati_b is None and errore_b == "archive_path_missing"
    assert mondo["ledger"]()[b][1]["pdf"]["state"] == "missing"

    # richiesta di sopralluogo in Agenda (funzione di import esistente)
    assert conta(mondo, "appointments", "source = 'legacy_stime_dettagliate' AND source_record_id = %s",
                 (f"stime_dettagliate:{d}",)) == 1
    assert mondo["ledger"]("stime_dettagliate")[d][0] == "imported"


def test_02_secondo_giro_nessun_doppione(mondo):
    a = mondo["stima"]()
    mondo["dettaglio"](a)
    mondo["giro"]()
    prima = {t: conta(mondo, t) for t in ("stime", "stime_dettagliate", "contacts", "leads", "lead_stime",
                                          "properties", "appointments", "stima_pdf_artifacts", "tasks")}
    esito = mondo["giro"]()
    assert esito["stime_imported"] == 0 and esito["dettagliate_imported"] == 0
    dopo = {t: conta(mondo, t) for t in prima}
    assert dopo == prima


def test_03_una_riga_appena_scritta_aspetta_che_il_sito_finisca(mondo):
    fresca = mondo["stima"](minuti_fa=1)
    assert mondo["giro"]()["stime_imported"] == 0
    assert fresca not in mondo["ledger"]()
    mondo["q_sito"]("UPDATE stime SET data = data - interval '20 minutes' WHERE id = %s", (fresca,))
    assert mondo["giro"]()["stime_imported"] == 1


def test_04_sincronizzazione_delle_nuove_richieste(mondo):
    mondo["stima"]()
    mondo["giro"]()
    nuova = mondo["stima"](minuti_fa=30)
    esito = mondo["giro"]()
    assert esito["stime_imported"] == 1 and nuova in mondo["ledger"]()


def test_05_interruzione_e_ripresa_senza_perdite_ne_doppioni(mondo, monkeypatch):
    from property import site_sync
    a = mondo["stima"]()
    originale = site_sync.sync_public_stima
    monkeypatch.setattr(site_sync, "sync_public_stima", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("giu'")))
    esito = mondo["giro"]()
    assert esito["partial"] == 1
    stato, steps, errore = mondo["ledger"]()[a]
    assert stato == "partial" and steps["property"]["state"] == "error" and steps["bridge"]["state"] == "done"
    monkeypatch.setattr(site_sync, "sync_public_stima", originale)
    esito = mondo["giro"]()
    assert esito["stime_imported"] == 1
    assert mondo["ledger"]()[a][0] == "imported"
    assert conta(mondo, "lead_stime", "stima_id = %s", (a,)) == 1
    assert conta(mondo, "property_site_sources", "stima_id = %s", (a,)) == 1


def test_06_id_gia_usato_nel_crm_non_si_sovrascrive(mondo):
    a = mondo["stima"]()
    mondo["q_crm"]("INSERT INTO stime (id, agency_id, comune, email) VALUES (%s, 1, 'Locale', 'locale@x.invalid')", (a,))
    try:
        esito = mondo["giro"]()
        assert esito["conflict"] == 1
        assert mondo["ledger"]()[a][0] == "conflict"
        assert mondo["q_crm"]("SELECT email FROM stime WHERE id = %s", (a,))[0][0] == "locale@x.invalid"
        assert mondo["giro"]()["conflict"] == 0   # non si ritenta all'infinito
    finally:
        mondo["q_crm"]("DELETE FROM stime WHERE id = %s", (a,))


def test_07_dettaglio_orfano_aspetta_il_genitore(mondo):
    a = mondo["stima"](minuti_fa=2)               # ancora "fresca": non si importa
    d = mondo["dettaglio"](a, minuti_fa=60)
    esito = mondo["giro"]()
    assert esito["orphan"] == 1 and mondo["ledger"]("stime_dettagliate")[d][0] == "orphan"
    mondo["q_sito"]("UPDATE stime SET data = data - interval '1 hour' WHERE id = %s", (a,))
    esito = mondo["giro"]()
    assert esito["stime_imported"] == 1 and esito["dettagliate_imported"] == 1


def test_08_il_sito_e_davvero_in_sola_lettura(mondo):
    import psycopg2
    from site_import.source import SiteSource
    with SiteSource(mondo["sito_dsn"]) as s:
        with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            with s._conn.cursor() as cur:
                cur.execute("UPDATE stime SET nome = 'X'")
        s._conn.rollback()
    # Sentinella sul testo: tolte docstring e commenti, nel modulo della
    # sorgente non compare nessuna istruzione che scriva.
    testo = (ROOT / "site_import" / "source.py").read_text(encoding="utf-8")
    codice = re.sub(r'"""[\s\S]*?"""', "", testo)
    codice = "\n".join(r.split("#", 1)[0] for r in codice.splitlines())
    trovati = re.findall(r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|CREATE|GRANT|COPY)\b", codice.upper())
    assert trovati == [], trovati


def test_09_nessuna_comunicazione_e_nessuna_sequenza(mondo):
    mondo["stima"]()
    mondo["stima"](minuti_fa=30)
    mondo["giro"]()
    assert conta(mondo, "communication_messages") == 0
    assert conta(mondo, "communication_enrollments") == 0


def test_10_task_solo_per_le_stime_recenti(mondo):
    vecchia = mondo["stima"]()
    recente = mondo["stima"](minuti_fa=30)
    mondo["giro"]()
    led = mondo["ledger"]()
    assert led[vecchia][1]["followup"] == {"state": "skipped", "reason": "history"}
    assert led[recente][1]["followup"]["state"] == "done"
    assert conta(mondo, "tasks", "metadata->>'rule_code' = 'FOLLOWUP_STIMA_RICHIESTA' AND stima_id = %s", (vecchia,)) == 0
    assert conta(mondo, "tasks", "metadata->>'rule_code' = 'FOLLOWUP_STIMA_RICHIESTA' AND stima_id = %s", (recente,)) == 1


def test_11_il_pdf_mancante_non_viene_mai_rigenerato(mondo, monkeypatch):
    from fastapi import HTTPException
    import stima_pdf
    a = mondo["stima"]()
    b = mondo["stima"]()
    mondo["archivio"].pdf[a] = PDF
    mondo["giro"]()
    monkeypatch.setattr(stima_pdf, "get_connection", mondo["crm"])
    assert stima_pdf.download(a, agency_id=1, connection_factory=mondo["crm"]) == PDF
    disegnato = []
    with pytest.raises(HTTPException) as e:
        stima_pdf.generate(b, renderer=lambda *x, **k: disegnato.append(1) or PDF, agency_id=1,
                           connection_factory=mondo["crm"])
    assert e.value.status_code == 503 and disegnato == []
    with pytest.raises(HTTPException):
        stima_pdf.download(b, agency_id=1, connection_factory=mondo["crm"])
    # e il token del sito NON apre il PDF del CRM: la capability non e' copiata
    assert mondo["q_crm"]("SELECT token FROM stime WHERE id = %s", (a,))[0][0] is None


def test_12_guardie_e_prova_a_secco(mondo):
    from site_import.config import Config
    from site_import.service import ImportRefused, Importer
    from site_import.source import SiteSource
    mondo["stima"]()
    secco = mondo["giro"](dry_run=True)
    assert secco == {"status": "dry_run", "stime_to_process": 1, "dettagliate_to_process": 0}
    assert mondo["ledger"]() == {}
    # la "sorgente" che e' il CRM stesso viene rifiutata
    with SiteSource(_dsn_per("stima360_db_test")) as s:
        with pytest.raises(ImportRefused):
            Importer(Config(site_db_url="x"), source=s, archive=FakeArchive(), connect=mondo["crm"]).run()


def test_13_due_giri_simultanei_un_solo_import(mondo):
    ids = [mondo["stima"]() for _ in range(5)]
    esiti = []

    def giro():
        esiti.append(mondo["giro"]())

    fili = [threading.Thread(target=giro) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(120)
    totale = sum(e.get("stime_imported", 0) for e in esiti)
    assert totale == 5, esiti
    assert conta(mondo, "stime", "id = ANY(%s)", (ids,)) == 5
    assert conta(mondo, "lead_stime", "stima_id = ANY(%s)", (ids,)) == 5
    assert sorted(e["status"] for e in esiti) in (["completed", "completed"], ["completed", "skipped_overlap"])


def test_14_lotto_limitato_e_ripresa_dello_storico(mondo):
    ids = [mondo["stima"]() for _ in range(5)]
    primo = mondo["giro"](limit=2)
    assert primo["stime_imported"] == 2 and primo["pending_after_batch"] == 3
    while mondo["giro"](limit=2)["stime_imported"]:
        pass
    assert {k for k, v in mondo["ledger"]().items() if v[0] == "imported"} == set(ids)



def test_15_stima_locali_non_specificato_del_sito_prod(mondo):
    """Caso reale 230: il sito salva 'Non specificato' nel campo TEXT."""
    sid = mondo["stima"](locali="Non specificato", anno="2010")
    esito = mondo["giro"](limit=1)
    assert esito["failed"] == 0 and esito["conflict"] == 0, esito
    assert mondo["q_crm"]("SELECT locali, anno FROM stime WHERE id = %s", (sid,))[0] == (None, 2010)
    assert mondo["ledger"]()[sid][0] in ("imported", "partial")


def test_16_stima_locali_numerici_come_testo(mondo):
    """Il testo '3' deve restare il numero 3 nel CRM."""
    sid = mondo["stima"](locali="3", anno="1995")
    esito = mondo["giro"](limit=1)
    assert esito["failed"] == 0 and esito["conflict"] == 0, esito
    assert mondo["q_crm"]("SELECT locali, anno FROM stime WHERE id = %s", (sid,))[0] == (3, 1995)
