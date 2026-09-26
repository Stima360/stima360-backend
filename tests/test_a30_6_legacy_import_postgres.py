"""A30-6 - import legacy `stime_dettagliate.sopralluogo` -> Agenda su PostgreSQL vero.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta, MAI PROD:
il fixture crea un database dal nome casuale, ci applica lo schema minimo
condiviso, la catena LMC-15 (066, 070), l'Agenda (072, 073) e la tabella
legacy `stime_dettagliate` con le migration VERE 049-051 (agency_id, NOT NULL
e trigger di integrita'), e lo cancella alla fine. I dati sono sintetici.

Si prova: mapping, DST, orfani, lead, idempotenza (anche fra due corse
concorrenti), atomicita' riga+evento, assenza di side effect, dry-run e
census senza persistenza, rollback selettivo, isolamento per agenzia e
report senza dati personali.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_a30_2_appointments_postgres import DSN, db, mondo  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-6")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
ROMA = ZoneInfo("Europe/Rome")

#: Le colonne vere di `stime_dettagliate` che contano qui: il DDL base di
#: `database.py` (id SERIAL, stima_id FK NO ACTION, sopralluogo TIMESTAMP senza
#: fuso, data) piu' i recapiti che il form scrive - presenti SOLO per provare
#: che non finiscono mai nel report, nelle note o negli eventi.
LEGACY_DDL = """
CREATE TABLE stime_dettagliate (
    id SERIAL PRIMARY KEY,
    stima_id INTEGER REFERENCES stime(id),
    note TEXT,
    contatto VARCHAR(8),
    sopralluogo TIMESTAMP,
    data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100), telefono VARCHAR(30),
    indirizzo TEXT);
CREATE TABLE lead_stime (
    id BIGSERIAL PRIMARY KEY,
    lead_id BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    stima_id INTEGER NOT NULL REFERENCES stime(id) ON DELETE CASCADE,
    relation_type VARCHAR(20) NOT NULL DEFAULT 'related',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT lead_stime_relation_chk CHECK (relation_type IN ('origin', 'related', 'follow_up')),
    CONSTRAINT lead_stime_unq UNIQUE (lead_id, stima_id));
"""

LEGACY_MIGRATIONS = ("049_p26_stima_dettagliata_agency_columns",
                     "050_p26_stima_dettagliata_agency_backfill",
                     "051_p26_stima_dettagliata_agency_enforce")

#: Recapiti sintetici che NON devono comparire da nessuna parte nell'output.
PII = ("Mariangela", "Pellegrini", "mariangela.pellegrini@example.it", "3471234567",
       "Via Riservata 17", "citofono rotto")


#: IL DATABASE E LE CONNESSIONI SONO QUELLI DI A30-2. Nessuna connessione
#: nuova nasce qui (P26 H11: l'insieme dei punti che aprono una connessione
#: resta noto): il database usa-e-getta e' il fixture `db` di
#: `test_a30_2_appointments_postgres.py`, il mondo di prova e' il suo `mondo`,
#: che instrada `core.database.get_connection()` su quel database. Questo
#: modulo aggiunge soltanto, una volta, la 073 e la tabella legacy.
@pytest.fixture(scope="module")
def db_legacy(db):
    conn = db["conn"]
    nome = db["dsn"].rsplit("/", 1)[1].split("?", 1)[0]
    assert nome.startswith("a30_2_probe_"), nome          # mai TEST reale, mai PROD
    with conn.cursor() as cur:
        cur.execute((MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8"))
        cur.execute(LEGACY_DDL)
        for versione in LEGACY_MIGRATIONS:
            cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
    return {**db, "nome": nome}


@pytest.fixture
def legacy_pulito(db_legacy):
    """Le righe legacy tengono `stime` e `leads`: vanno via PRIMA che il
    `mondo` di A30-2 svuoti le sue tabelle."""
    with db_legacy["conn"].cursor() as cur:
        cur.execute("DELETE FROM stime_dettagliate")
        cur.execute("DELETE FROM lead_stime")
    return db_legacy


@pytest.fixture
def m(legacy_pulito, mondo):
    from psycopg2.extras import RealDictCursor

    conn = legacy_pulito["conn"]
    ids = {k: mondo[k] for k in ("a", "b", "giorgio", "mario", "bruno", "contatto_b")}
    ids["carla"] = mondo["contatto_b"]
    with conn.cursor() as cur:
        def stima(agenzia):
            cur.execute("INSERT INTO stime (agency_id, comune, mq) VALUES (%s,'Tortoreto',90) "
                        "RETURNING id", (agenzia,))
            return cur.fetchone()[0]

        def lead(agenzia, contatto_id, stima_id):
            cur.execute("INSERT INTO leads (contact_id, agency_id, pipeline) "
                        "VALUES (%s,%s,'sell') RETURNING id", (contatto_id, agenzia))
            lid = cur.fetchone()[0]
            cur.execute("INSERT INTO lead_stime (lead_id, stima_id, relation_type) "
                        "VALUES (%s,%s,'origin')", (lid, stima_id))
            return lid

        ids["s_un_lead"] = stima(ids["a"])
        ids["s_due_lead"] = stima(ids["a"])
        ids["s_zero_lead"] = stima(ids["a"])
        ids["s_b"] = stima(ids["b"])
        ids["lead_mario"] = lead(ids["a"], ids["mario"], ids["s_un_lead"])
        ids["lead_bruno_1"] = lead(ids["a"], ids["bruno"], ids["s_due_lead"])
        ids["lead_bruno_2"] = lead(ids["a"], ids["bruno"], ids["s_due_lead"])
        ids["lead_carla"] = lead(ids["b"], ids["carla"], ids["s_b"])
        # un lead di UN'ALTRA agenzia legato alla stima senza lead di A: non conta
        ids["lead_estraneo"] = lead(ids["b"], ids["carla"], ids["s_zero_lead"])
        cur.execute("SELECT (NOW() AT TIME ZONE 'Europe/Rome')::date")
        ids["oggi"] = cur.fetchone()[0]
    conn.commit()

    def sql(testo, par=None):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(testo, par)
            righe = [dict(r) for r in cur.fetchall()] if cur.description else None
        conn.commit()
        return righe

    def dettaglio(stima_id, sopralluogo, *, agenzia=None, pii=False):
        agenzia = agenzia if agenzia is not None else ids["a"]
        valori = {"stima_id": stima_id, "agency_id": agenzia, "sopralluogo": sopralluogo,
                  "contatto": "Si"}
        if pii:
            valori.update(nome=PII[0], cognome=PII[1], email=PII[2], telefono=PII[3],
                          indirizzo=PII[4], note=PII[5])
        colonne = sorted(valori)
        riga = sql(f"INSERT INTO stime_dettagliate ({', '.join(colonne)}) "
                   f"VALUES ({', '.join('%s' for _ in colonne)}) RETURNING id",
                   [valori[c] for c in colonne])
        return riga[0]["id"]

    def giorno(delta, h=10, m=30):
        return datetime.combine(ids["oggi"] + timedelta(days=delta),
                                datetime.min.time()).replace(hour=h, minute=m)

    return {**ids, "sql": sql, "dettaglio": dettaglio, "giorno": giorno,
            "nome_db": legacy_pulito["nome"]}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# strumenti
# ---------------------------------------------------------------------------

def _legacy():
    from appointments_legacy import stime_dettagliate_import as legacy
    return legacy


def _connessione(mondo):
    """Una connessione nuova al database di prova, dal punto unico
    `core.database.get_connection` (instradato dal `mondo` di A30-2)."""
    from core import database as core_database
    return core_database.get_connection()


def _import(mondo, *, apply=True, commit=True):
    from psycopg2.extras import RealDictCursor
    conn = _connessione(mondo)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            esito = _legacy().run_import(cur, apply=apply)
        conn.commit() if commit else conn.rollback()
        return esito
    finally:
        conn.close()


def _census(mondo):
    from psycopg2.extras import RealDictCursor
    conn = _connessione(mondo)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return _legacy().census(cur)
    finally:
        conn.rollback()
        conn.close()


def _rollback(mondo, *, apply=True):
    from psycopg2.extras import RealDictCursor
    conn = _connessione(mondo)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            esito = _legacy().rollback_untouched(cur, apply=apply)
        conn.commit()
        return esito
    finally:
        conn.close()


def _appuntamento(mondo, record_id):
    righe = mondo["sql"]("SELECT * FROM appointments WHERE source = 'legacy_stime_dettagliate' "
                         "AND source_record_id = %s", (f"stime_dettagliate:{record_id}",))
    assert len(righe) <= 1
    return righe[0] if righe else None


def _conta(mondo, tabella, where="TRUE", par=None):
    return mondo["sql"](f"SELECT count(*) AS n FROM {tabella} WHERE {where}", par)[0]["n"]


def _fotografia(mondo, *, escluse=()):
    """Ogni tabella del database, riga per riga (hash del contenuto)."""
    tabelle = [r["tablename"] for r in mondo["sql"](
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
    foto = {}
    for t in tabelle:
        if t in escluse:
            continue
        foto[t] = mondo["sql"](
            f"SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) "
            f"AS h FROM {t} x")[0]
    return foto


def _istante(valore):
    return valore if isinstance(valore, datetime) else datetime.fromisoformat(valore)


# ---------------------------------------------------------------------------
# A - MAPPING (1-11)
# ---------------------------------------------------------------------------

def test_01_record_futuro_valido(m):
    wall = m["giorno"](+20, 10, 30)
    rid = m["dettaglio"](m["s_un_lead"], wall)
    esito = _import(m)
    assert (esito["eligible"], esito["inserted"], esito["future"]) == (1, 1, 1)
    a = _appuntamento(m, rid)
    assert a["status"] == "requested" and a["appointment_type"] == "inspection"
    assert a["start_at"] == wall.replace(tzinfo=ROMA)
    assert a["timezone"] == "Europe/Rome" and a["version"] == 1


def test_02_record_passato_valido_resta_requested(m):
    wall = m["giorno"](-400, 9, 0)
    rid = m["dettaglio"](m["s_un_lead"], wall)
    esito = _import(m)
    assert (esito["past"], esito["inserted"]) == (1, 1)
    a = _appuntamento(m, rid)
    assert a["status"] == "requested"
    for campo in ("completed_at", "cancelled_at", "no_show_at", "confirmed_at",
                  "cancelled_reason"):
        assert a[campo] is None, campo


def test_02b_record_di_oggi(m):
    m["dettaglio"](m["s_un_lead"], m["giorno"](0, 12, 0))
    esito = _import(m)
    assert (esito["today"], esito["past"], esito["future"]) == (1, 0, 0)


def test_03_durata_esattamente_60_minuti(m):
    rid = m["dettaglio"](m["s_un_lead"], m["giorno"](+3, 23, 30))
    _import(m)
    a = _appuntamento(m, rid)
    assert a["end_at"] - a["start_at"] == timedelta(minutes=60)


def test_04_source_e_source_record_id(m):
    rid = m["dettaglio"](m["s_un_lead"], m["giorno"](+3))
    _import(m)
    a = _appuntamento(m, rid)
    assert a["source"] == "legacy_stime_dettagliate"
    assert a["source_record_id"] == f"stime_dettagliate:{rid}"


def test_05_06_stima_e_agenzia_corrette(m):
    ra = m["dettaglio"](m["s_un_lead"], m["giorno"](+3))
    rb = m["dettaglio"](m["s_b"], m["giorno"](+4), agenzia=m["b"])
    _import(m)
    a, b = _appuntamento(m, ra), _appuntamento(m, rb)
    assert (a["stima_id"], a["agency_id"]) == (m["s_un_lead"], m["a"])
    assert (b["stima_id"], b["agency_id"]) == (m["s_b"], m["b"])


def test_07_08_nessun_agente_immobile_luogo_o_autore_inventati(m):
    rid = m["dettaglio"](m["s_un_lead"], m["giorno"](+3), pii=True)
    _import(m)
    a = _appuntamento(m, rid)
    for campo in ("assigned_user_id", "property_id", "location_text", "created_by_user_id",
                  "stima_inspection_id", "rescheduled_from_id", "test_run_id"):
        assert a[campo] is None, campo
    assert a["buffer_before_minutes"] == 0 and a["buffer_after_minutes"] == 0


def test_09_lead_unico_collegato_col_suo_contatto(m):
    rid = m["dettaglio"](m["s_un_lead"], m["giorno"](+3))
    esito = _import(m)
    assert esito["one_lead"] == 1
    a = _appuntamento(m, rid)
    assert (a["lead_id"], a["contact_id"]) == (m["lead_mario"], m["mario"])


def test_10_zero_lead_nulla(m):
    # la stima ha un lead, ma di UN'ALTRA agenzia: non conta
    rid = m["dettaglio"](m["s_zero_lead"], m["giorno"](+3))
    esito = _import(m)
    assert esito["zero_lead"] == 1 and esito["one_lead"] == 0
    a = _appuntamento(m, rid)
    assert a["lead_id"] is None and a["contact_id"] is None


def test_11_piu_lead_nulla_senza_scegliere(m):
    rid = m["dettaglio"](m["s_due_lead"], m["giorno"](+3))
    esito = _import(m)
    assert esito["multiple_leads"] == 1
    a = _appuntamento(m, rid)
    assert a["lead_id"] is None and a["contact_id"] is None


# ---------------------------------------------------------------------------
# B - ESCLUSIONI (12-15)
# ---------------------------------------------------------------------------

def test_12_orfani_esclusi_e_contati(m):
    senza_stima = m["dettaglio"](None, m["giorno"](+3))
    # stima di un'altra agenzia: il trigger 051 lo impedirebbe; si simula una
    # deriva storica disattivandolo solo per l'inserimento
    m["sql"]("ALTER TABLE stime_dettagliate DISABLE TRIGGER trg_stima_dettagliata_agency_integrity")
    try:
        incrociato = m["dettaglio"](m["s_b"], m["giorno"](+3), agenzia=m["a"])
    finally:
        m["sql"]("ALTER TABLE stime_dettagliate ENABLE TRIGGER trg_stima_dettagliata_agency_integrity")
    esito = _import(m)
    assert esito["orphan"] == 2 and esito["eligible"] == 0 and esito["inserted"] == 0
    assert _appuntamento(m, senza_stima) is None
    assert _appuntamento(m, incrociato) is None
    assert _conta(m, "appointments") == 0


def test_13_sopralluogo_null_escluso(m):
    m["dettaglio"](m["s_un_lead"], None)
    esito = _import(m)
    assert esito["without_sopralluogo"] == 1 and esito["with_sopralluogo"] == 0
    assert esito["eligible"] == 0 and _conta(m, "appointments") == 0


def test_14_15_dst_inesistente_e_ambigua_esclusi(m):
    inesistente = m["dettaglio"](m["s_un_lead"], datetime(2026, 3, 29, 2, 30))
    ambigua = m["dettaglio"](m["s_un_lead"], datetime(2026, 10, 25, 2, 30))
    bordo = m["dettaglio"](m["s_un_lead"], datetime(2026, 10, 25, 3, 0))
    esito = _import(m)
    assert (esito["dst_nonexistent"], esito["dst_ambiguous"]) == (1, 1)
    assert esito["inserted"] == 1
    assert _appuntamento(m, inesistente) is None and _appuntamento(m, ambigua) is None
    b = _appuntamento(m, bordo)
    assert b["start_at"] == datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# C - IDEMPOTENZA, ATOMICITA', CONCORRENZA (16-19)
# ---------------------------------------------------------------------------

def test_16_17_secondo_run_nessun_duplicato_ne_evento(m):
    for delta in (-10, +2, +5):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta))
    primo = _import(m)
    assert (primo["inserted"], primo["already_imported"]) == (3, 0)
    assert _conta(m, "appointment_events") == 3
    eventi = m["sql"]("SELECT event_type, actor_user_id, to_status, changes "
                          "FROM appointment_events ORDER BY id")
    for e in eventi:
        assert e["event_type"] == "created" and e["actor_user_id"] is None
        assert e["to_status"] == "requested"
        assert e["changes"]["source"] == "legacy_stime_dettagliate"
    secondo = _import(m)
    assert (secondo["inserted"], secondo["already_imported"], secondo["eligible"]) == (0, 3, 0)
    assert _conta(m, "appointments") == 3
    assert _conta(m, "appointment_events") == 3


def test_18_nuovo_legacy_fra_i_due_run_solo_quello_inserito(m):
    for delta in (+1, +2):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta))
    assert _import(m)["inserted"] == 2
    nuovo = m["dettaglio"](m["s_zero_lead"], m["giorno"](+9))
    secondo = _import(m)
    assert (secondo["already_imported"], secondo["inserted"]) == (2, 1)
    assert secondo["inserted_ids"] == [_appuntamento(m, nuovo)["id"]]
    assert _conta(m, "appointments") == 3 and _conta(m, "appointment_events") == 3


def test_19_due_import_concorrenti_al_massimo_un_appuntamento(m):
    from psycopg2.extras import RealDictCursor
    for delta in (+1, +2, +3):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta))
    legacy = _legacy()
    primo = _connessione(m)
    esiti = {}
    try:
        with primo.cursor(cursor_factory=RealDictCursor) as cur:
            esiti["primo"] = legacy.run_import(cur, apply=True)   # NON ancora committato

        def secondo():
            conn = _connessione(m)
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    esiti["secondo"] = legacy.run_import(cur, apply=True)
                conn.commit()
            finally:
                conn.close()

        filo = threading.Thread(target=secondo)
        filo.start()
        time.sleep(1.0)            # il secondo e' fermo sull'indice unico
        assert filo.is_alive()
        primo.commit()
        filo.join(timeout=30)
        assert not filo.is_alive()
    finally:
        primo.close()
    assert esiti["primo"]["inserted"] == 3
    assert esiti["secondo"]["inserted"] == 0 and esiti["secondo"]["already_imported"] == 3
    assert esiti["secondo"]["errors"] == 0
    assert _conta(m, "appointments") == 3
    assert _conta(m, "appointment_events") == 3


def test_19b_riga_ed_evento_atomici_un_errore_non_lascia_nulla(m, monkeypatch):
    from appointments import repository
    buono = m["dettaglio"](m["s_un_lead"], m["giorno"](+1))
    cattivo = m["dettaglio"](m["s_un_lead"], m["giorno"](+2))
    originale = repository.record_event

    def evento_che_fallisce(cur, **kw):
        if kw["changes"].get("source_record_id") == f"stime_dettagliate:{cattivo}":
            cur.execute("SELECT 1/0")
        return originale(cur, **kw)

    monkeypatch.setattr(repository, "record_event", evento_che_fallisce)
    esito = _import(m)
    assert (esito["inserted"], esito["errors"]) == (1, 1)
    assert esito["error_details"] == [{"record_id": cattivo, "kind": "DivisionByZero",
                                       "sqlstate": "22012"}]
    assert _appuntamento(m, buono) is not None
    # la riga dell'errore non esiste senza il suo evento, ne' viceversa
    assert _appuntamento(m, cattivo) is None
    assert _conta(m, "appointments") == 1 and _conta(m, "appointment_events") == 1
    # risolto il problema, il record rientra alla corsa successiva
    monkeypatch.setattr(repository, "record_event", originale)
    assert _import(m)["inserted"] == 1


# ---------------------------------------------------------------------------
# D - NESSUN SIDE EFFECT (20-24)
# ---------------------------------------------------------------------------

def _con_lmc15_e_timeline(mondo):
    from acquisition import repository as lmc15
    lmc15.create_inspection(mondo["a"], stima_id=mondo["s_un_lead"],
                            scheduled_for=datetime.now(timezone.utc) + timedelta(days=40),
                            actor_user_id=mondo["giorgio"])


def test_20_21_22_nessuna_stima_inspections_timeline_o_altro(m):
    _con_lmc15_e_timeline(m)
    for delta in (-3, +3):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta), pii=True)
    prima = _fotografia(m, escluse=("appointments", "appointment_events"))
    esito = _import(m)
    assert esito["inserted"] == 2
    dopo = _fotografia(m, escluse=("appointments", "appointment_events"))
    # stime_dettagliate, stime, stima_inspections, seller_timeline_events,
    # leads, contatti... identici byte per byte
    assert dopo == prima
    assert _conta(m, "stima_inspections") == 1
    assert _conta(m, "appointments", "stima_inspection_id IS NOT NULL") == 0
    # un solo evento per riga, solo `created`, nessun attore umano
    assert m["sql"]("SELECT DISTINCT event_type, actor_user_id FROM appointment_events") \
        == [{"event_type": "created", "actor_user_id": None}]


def test_23_dry_run_zero_persistenza(m):
    for delta in (-3, +3):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta))
    prima = _fotografia(m)
    esito = _import(m, apply=True, commit=False)
    assert esito["inserted"] == 2
    assert _fotografia(m) == prima


def test_24_census_zero_scritture_e_sessione_read_only(m):
    for delta in (-3, +3):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta))
    prima = _fotografia(m)
    census = _census(m)
    assert census["plan"]["eligible"] == 2 and census["plan"]["inserted"] == 0
    assert census["drift"]["imported_total"] == 0
    assert _fotografia(m) == prima
    # via script: la sessione e' READ ONLY per il server stesso
    script, conn = _script(m)
    try:
        esito = script.run(conn, m["nome_db"], mode="census")
    finally:
        conn.close()
    assert esito["census"]["plan"]["eligible"] == 2
    assert _fotografia(m) == prima


def test_24b_census_misura_la_deriva_senza_correggerla(m):
    cancellato = m["dettaglio"](m["s_un_lead"], m["giorno"](+1))
    azzerato = m["dettaglio"](m["s_un_lead"], m["giorno"](+2))
    spostato = m["dettaglio"](m["s_un_lead"], m["giorno"](+3))
    m["dettaglio"](m["s_un_lead"], m["giorno"](+4))
    _import(m)
    prima = {r: _appuntamento(m, r) for r in (cancellato, azzerato, spostato)}
    m["sql"]("DELETE FROM stime_dettagliate WHERE id = %s", (cancellato,))
    m["sql"]("UPDATE stime_dettagliate SET sopralluogo = NULL WHERE id = %s", (azzerato,))
    m["sql"]("UPDATE stime_dettagliate SET sopralluogo = sopralluogo + interval '1 hour' "
                 "WHERE id = %s", (spostato,))
    deriva = _census(m)["drift"]
    assert deriva == {"imported_total": 4, "imported_by_status": {"requested": 4},
                      "drift_source_deleted": 1, "drift_source_cleared": 1,
                      "drift_time_changed": 1}
    # INSERT-ONCE: l'apply non tocca gli appuntamenti gia' importati
    esito = _import(m)
    assert esito["inserted"] == 0
    for r, a in prima.items():
        assert _appuntamento(m, r) == a


# ---------------------------------------------------------------------------
# E - ROLLBACK SELETTIVO (25-27)
# ---------------------------------------------------------------------------

def _manuale(mondo):
    from psycopg2.extras import RealDictCursor

    from appointments import repository
    conn = _connessione(mondo)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            riga = repository.insert_appointment(cur, {
                "agency_id": mondo["a"], "appointment_type": "inspection",
                "status": "requested", "start_at": datetime(2030, 1, 10, 9, tzinfo=ROMA),
                "end_at": datetime(2030, 1, 10, 10, tzinfo=ROMA),
                "stima_id": mondo["s_un_lead"], "source": "crm_manual",
                "source_record_id": str(uuid.uuid4()),
                "created_by_user_id": mondo["giorgio"]}, actor_user_id=mondo["giorgio"])
        conn.commit()
        return riga
    finally:
        conn.close()


def _lavora(mondo, appuntamento_id):
    """Un operatore tocca la richiesta importata (nota): version 2."""
    from psycopg2.extras import RealDictCursor

    from appointments import repository
    conn = _connessione(mondo)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            repository.update_appointment(
                cur, appointment_id=appuntamento_id,
                changes={"notes": "Richiamato il cliente"}, actor_user_id=mondo["giorgio"],
                event_type="updated", from_status="requested", azione="patch")
        conn.commit()
    finally:
        conn.close()


def test_25_26_27_rollback_solo_legacy_intatti(m):
    intatto = m["dettaglio"](m["s_un_lead"], m["giorno"](+1))
    lavorato = m["dettaglio"](m["s_un_lead"], m["giorno"](+2))
    _import(m)
    manuale = _manuale(m)
    _lavora(m, _appuntamento(m, lavorato)["id"])
    foto_manuale = m["sql"]("SELECT * FROM appointments WHERE id = %s", (manuale["id"],))
    foto_lavorato = _appuntamento(m, lavorato)
    prima_legacy = _fotografia(m, escluse=("appointments", "appointment_events"))

    anteprima = _rollback(m, apply=False)
    assert (anteprima["untouched"], anteprima["worked_skipped"], anteprima["cancelled"]) == \
        (1, 1, 0)
    assert _appuntamento(m, intatto)["status"] == "requested"

    esito = _rollback(m)
    assert (esito["cancelled"], esito["worked_skipped"]) == (1, 1)
    assert esito["keys_remain_occupied"] is True
    a = _appuntamento(m, intatto)
    assert a["status"] == "cancelled" and a["cancelled_reason"] == "A30-6 rollback import legacy"
    assert a["cancelled_at"] is not None and a["version"] == 2
    ultimo = m["sql"]("SELECT event_type, actor_user_id, from_status, to_status FROM "
                          "appointment_events WHERE appointment_id = %s ORDER BY id DESC "
                          "LIMIT 1", (a["id"],))[0]
    assert ultimo == {"event_type": "status_changed", "actor_user_id": None,
                      "from_status": "requested", "to_status": "cancelled"}
    # il manuale e il lavorato: identici
    assert m["sql"]("SELECT * FROM appointments WHERE id = %s",
                        (manuale["id"],)) == foto_manuale
    assert _appuntamento(m, lavorato) == foto_lavorato
    # legacy, stime, LMC-15: intatti
    assert _fotografia(m, escluse=("appointments", "appointment_events")) == prima_legacy
    # nessuna DELETE: le righe ci sono tutte
    assert _conta(m, "appointments") == 3
    # la chiave resta occupata: il record annullato NON si reimporta
    di_nuovo = _import(m)
    assert (di_nuovo["inserted"], di_nuovo["already_imported"]) == (0, 2)
    # un secondo rollback non trova piu' nulla da annullare
    assert _rollback(m)["cancelled"] == 0


def test_27b_rollback_non_tocca_una_richiesta_fissata(m):
    rid = m["dettaglio"](m["s_un_lead"], m["giorno"](+1))
    _import(m)
    a = _appuntamento(m, rid)
    m["sql"]("UPDATE appointments SET assigned_user_id = %s, status = 'scheduled' "
                 "WHERE id = %s", (m["giorgio"], a["id"]))
    assert _rollback(m)["cancelled"] == 0
    assert _appuntamento(m, rid)["status"] == "scheduled"


# ---------------------------------------------------------------------------
# F - ISOLAMENTO E PRIVACY (28-29)
# ---------------------------------------------------------------------------

def test_28_isolamento_per_agenzia(m):
    ra = m["dettaglio"](m["s_un_lead"], m["giorno"](+1))
    rb = m["dettaglio"](m["s_b"], m["giorno"](+1), agenzia=m["b"])
    esito = _import(m)
    assert esito["inserted"] == 2
    a, b = _appuntamento(m, ra), _appuntamento(m, rb)
    assert a["agency_id"] == m["a"] and a["lead_id"] == m["lead_mario"]
    # B: il suo lead (di B), mai quello di A, e il contatto di B
    assert b["agency_id"] == m["b"]
    assert (b["lead_id"], b["contact_id"]) == (m["lead_carla"], m["carla"])
    eventi = m["sql"]("SELECT e.agency_id, a.agency_id AS propria FROM appointment_events e "
                          "JOIN appointments a ON a.id = e.appointment_id")
    assert all(e["agency_id"] == e["propria"] for e in eventi)
    # un record di B collegato (per deriva) a una stima di A non passa in A
    m["sql"]("ALTER TABLE stime_dettagliate DISABLE TRIGGER trg_stima_dettagliata_agency_integrity")
    try:
        incrociato = m["dettaglio"](m["s_un_lead"], m["giorno"](+2),
                                        agenzia=m["b"])
    finally:
        m["sql"]("ALTER TABLE stime_dettagliate ENABLE TRIGGER trg_stima_dettagliata_agency_integrity")
    assert _import(m)["orphan"] == 1
    assert _appuntamento(m, incrociato) is None


def test_29_report_note_ed_eventi_senza_dati_personali(m):
    for delta in (-3, +3):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta), pii=True)
    m["dettaglio"](None, m["giorno"](+5), pii=True)
    m["dettaglio"](m["s_un_lead"], datetime(2026, 10, 25, 2, 30), pii=True)
    testi = [json.dumps(_import(m), default=str), json.dumps(_census(m), default=str)]
    script, conn = _script(m)
    try:
        testi.append(json.dumps(script.run(conn, m["nome_db"], mode="dry-run"), default=str))
    finally:
        conn.close()
    testi += [json.dumps(r, default=str) for r in m["sql"](
        "SELECT notes, location_text FROM appointments")]
    testi += [json.dumps(r, default=str) for r in m["sql"](
        "SELECT changes FROM appointment_events")]
    for testo in testi:
        for dato in PII:
            assert dato not in testo, dato


# ---------------------------------------------------------------------------
# G - LO SCRIPT sul database usa-e-getta
# ---------------------------------------------------------------------------

def _script(mondo):
    """Lo script con la verifica d'identita' neutralizzata: il database di
    prova non e' (volutamente) un TEST certificato. La guardia vera e' provata
    a parte in `test_40`."""
    import importlib
    script = importlib.import_module("scripts.a30_6_legacy_import")
    script._identita_vera = getattr(script, "_identita_vera", script.assert_database_identity)
    script.assert_database_identity = lambda cur, nome: None
    return script, _connessione(mondo)


@pytest.fixture(autouse=True)
def _ripristina_identita():
    yield
    import sys
    script = sys.modules.get("scripts.a30_6_legacy_import")
    if script is not None and hasattr(script, "_identita_vera"):
        script.assert_database_identity = script._identita_vera


def test_40_la_guardia_d_identita_rifiuta_un_database_non_certificato(m):
    import importlib

    from psycopg2.extras import RealDictCursor

    from scripts.p26_migrate import GuardFailure
    script = importlib.import_module("scripts.a30_6_legacy_import")
    conn = _connessione(m)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            with pytest.raises(GuardFailure):
                script.assert_database_identity(cur, m["nome_db"])
            with pytest.raises(GuardFailure):
                script.assert_database_identity(cur, "stima360_db_test")
    finally:
        conn.close()


def test_41_script_dry_run_apply_rollback(m):
    for delta in (-2, +2):
        m["dettaglio"](m["s_un_lead"], m["giorno"](delta))
    prima = _fotografia(m)

    script, conn = _script(m)
    try:
        prova = script.run(conn, m["nome_db"], mode="dry-run")
    finally:
        conn.close()
    assert prova["committed"] is False and prova["import"]["inserted"] == 2
    assert prova["census_after"]["drift"]["imported_total"] == 2
    assert _fotografia(m) == prima

    script, conn = _script(m)
    try:
        vero = script.run(conn, m["nome_db"], mode="apply")
    finally:
        conn.close()
    assert vero["committed"] is True and vero["import"]["inserted"] == 2
    assert _conta(m, "appointments") == 2

    script, conn = _script(m)
    try:
        anteprima = script.run(conn, m["nome_db"], mode="rollback-dry-run")
    finally:
        conn.close()
    assert anteprima["committed"] is False and anteprima["rollback"]["cancelled"] == 2
    assert _conta(m, "appointments", "status = 'requested'") == 2

    script, conn = _script(m)
    try:
        annullo = script.run(conn, m["nome_db"], mode="rollback")
    finally:
        conn.close()
    assert annullo["committed"] is True and annullo["rollback"]["cancelled"] == 2
    assert _conta(m, "appointments", "status = 'cancelled'") == 2
    assert _conta(m, "appointments") == 2
