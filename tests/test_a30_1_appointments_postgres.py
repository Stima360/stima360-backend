"""A30-1 su PostgreSQL reale: il modello `appointments`, provato dove vive.

Cosa un doppio non puo' provare, e qui si prova:

  * il vincolo EXCLUDE (btree_gist): sovrapposizioni rifiutate, intervalli
    adiacenti ammessi, buffer inclusi, solo gli stati che bloccano;
  * `blocked_range` scritto dal trigger, mai dal chiamante;
  * le matrici di stato (CHECK), la tenancy dei riferimenti e dell'agente;
  * `stima_id` riferimento morbido: nessuna FK verso `stime`;
  * l'idempotenza di `source`/`source_record_id`;
  * il registro `appointment_events`: scritto dal repository (non da un
    trigger: P26-6 serie 100), append-only; nessuna DELETE sugli appuntamenti;
  * il service: lock per agente, conflitti leggibili, riserva LMC-15;
  * la CONCORRENZA, in modo deterministico: la seconda transazione aspetta
    davvero (lo si legge da `pg_stat_activity`, non da uno `sleep`).
  * la migration: up, down (che rifiuta se ci sono righe), di nuovo up.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-1")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "072_a30_1_appointments"

ROMA = timezone(timedelta(hours=2))
GIORNO = datetime(2026, 10, 5, tzinfo=ROMA)


def ore(h, m=0):
    return GIORNO.replace(hour=h, minute=m)


def _schema_e_catena():
    # Lo schema minimo e la catena LMC-15 si riusano dal loro test, non si
    # ricopiano: una copia diverge. La 072 dipende dalla 070
    # (`stima_inspections`, `lmc15_assert_operator_may_act`).
    from tests.test_lmc15_acquisition_bridge_postgres import CATENA, SCHEMA_MINIMO
    return SCHEMA_MINIMO, CATENA


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


SU = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    schema, catena = _schema_e_catena()

    nome = f"a30_1_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    from psycopg2.extras import DictCursor
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(schema)
            for versione in catena:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute(SU)
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _svuota(cur):
    for trg, tab in (("trg_appointments_refuse_delete", "appointments"),
                     ("trg_appointment_events_append_only", "appointment_events")):
        cur.execute(f"ALTER TABLE {tab} DISABLE TRIGGER {trg}")
    cur.execute("DELETE FROM appointment_events")
    cur.execute("DELETE FROM appointments")
    for trg, tab in (("trg_appointments_refuse_delete", "appointments"),
                     ("trg_appointment_events_append_only", "appointment_events")):
        cur.execute(f"ALTER TABLE {tab} ENABLE TRIGGER {trg}")


@pytest.fixture
def mondo(db):
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        _svuota(cur)
        for tabella in ("stima_inspections", "stima_acquisitions", "seller_timeline_events",
                        "leads", "stime", "properties", "contacts",
                        "agency_memberships", "operator_users", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, agenzia=None, ruolo="agent", platform=False, stato="active"):
            cur.execute("INSERT INTO operator_users (email, is_platform_admin) "
                        "VALUES (%s,%s) RETURNING id", (email, platform))
            i = cur.fetchone()[0]
            if agenzia is not None:
                cur.execute("INSERT INTO agency_memberships "
                            "(agency_id, operator_user_id, role, status) VALUES (%s,%s,%s,%s)",
                            (agenzia, i, ruolo, stato))
            return i

        giorgio = operatore("giorgio@example.it", a, "agency_owner")
        luca = operatore("luca@example.it", a, "agent")
        marta = operatore("marta@example.it", a, "agent")
        revocato = operatore("revocato@example.it", a, "agent", stato="revoked")
        estraneo = operatore("estraneo@example.it", b, "agent")
    conn.commit()

    def sql(testo, parametri=None):
        with conn.cursor() as cur:
            cur.execute(testo, parametri)
            return cur.fetchall() if cur.description else None

    def uno(testo, parametri=None):
        righe = sql(testo, parametri)
        conn.commit()
        return righe[0] if righe else None

    def appuntamento(agente, inizio, fine, *, stato="scheduled", agenzia=None, **extra):
        colonne = {"agency_id": agenzia or a, "assigned_user_id": agente,
                   "appointment_type": extra.pop("tipo", "seller_meeting"),
                   "status": stato, "start_at": inizio, "end_at": fine}
        colonne.update(extra)
        nomi = ", ".join(colonne)
        segnaposto = ", ".join("%s" for _ in colonne)
        return uno(f"INSERT INTO appointments ({nomi}) VALUES ({segnaposto}) RETURNING *",
                   tuple(colonne.values()))

    return {"conn": conn, "a": a, "b": b, "giorgio": giorgio, "luca": luca,
            "marta": marta, "revocato": revocato, "estraneo": estraneo,
            "sql": sql, "uno": uno, "app": appuntamento}


def _rifiutato(mondo, funzione, *args, codice=None, **kwargs):
    import psycopg2
    with pytest.raises(psycopg2.Error) as info:
        funzione(*args, **kwargs)
    mondo["conn"].rollback()
    if codice is not None:
        assert info.value.pgcode == codice, (info.value.pgcode, str(info.value))
    return info.value


class Ctx:
    def __init__(self, agency, user_id, *, assegna=True):
        self.agency_id = agency
        self.user_id = user_id
        self.may_assign_records = assegna

    def require_agency(self):
        return self.agency_id


@pytest.fixture
def servizio(db, monkeypatch):
    import psycopg2

    from appointments import service
    from core import database as core_database

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return service


# ---------------------------------------------------------------------------
# A - L'ESTENSIONE E LO SCHEMA
# ---------------------------------------------------------------------------

def test_01_btree_gist_installata_e_vincolo_exclude_presente(mondo):
    assert mondo["uno"]("SELECT extname FROM pg_extension WHERE extname='btree_gist'")
    riga = mondo["uno"](
        "SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='appointments_no_overlap_excl'")
    assert riga[0] == "x"
    definizione = riga[1]
    assert "gist" in definizione and "assigned_user_id WITH =" in definizione
    assert "blocked_range WITH &&" in definizione
    for stato in ("scheduled", "confirmed", "completed", "no_show"):
        assert stato in definizione
    for stato in ("requested", "cancelled", "rescheduled"):
        assert stato not in definizione


def test_02_start_end_sono_timestamptz_e_timezone_europe_rome(mondo):
    tipi = dict(mondo["sql"](
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='appointments'"))
    assert tipi["start_at"] == tipi["end_at"] == "timestamp with time zone"
    assert tipi["blocked_range"] == "tstzrange"
    r = mondo["app"](mondo["luca"], ore(10), ore(11))
    assert r["timezone"] == "Europe/Rome"
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(12), ore(13), timezone="UTC",
               codice="23514")


def test_03_stima_id_e_un_riferimento_morbido_senza_fk_verso_stime(mondo):
    fk = mondo["sql"](
        "SELECT conname FROM pg_constraint WHERE contype='f' "
        "AND conrelid='appointments'::regclass AND confrelid='stime'::regclass")
    assert fk == []
    mondo["conn"].commit()
    # Una stima che non esiste non fa fallire il database: la verifica e'
    # del service. E cancellare una stima non tocca l'appuntamento.
    r = mondo["app"](mondo["luca"], ore(10), ore(11), stima_id=987654)
    assert r["stima_id"] == 987654


def test_04_nessuna_tabella_esistente_e_stata_toccata_dal_testo(mondo):
    codice = "\n".join(r.split("--", 1)[0] for r in SU.splitlines())
    # il solo nome ammesso e' il VALORE di `source` per l'import futuro
    codice = codice.replace("'legacy_stime_dettagliate'", "'<fonte>'")
    for vietato in ("ALTER TABLE", "stime_dettagliate", "property_visits",
                    "REFERENCES stime", "UPDATE stima_inspections",
                    "INSERT INTO stima_inspections", "tasks", "activities"):
        assert vietato not in codice, vietato


# ---------------------------------------------------------------------------
# B - LA SOVRAPPOSIZIONE
# ---------------------------------------------------------------------------

def test_10_due_appuntamenti_sovrapposti_dello_stesso_agente_sono_irrappresentabili(mondo):
    mondo["app"](mondo["luca"], ore(10), ore(11))
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10, 30), ore(11, 30), codice="23P01")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(9), ore(12), codice="23P01")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10, 15), ore(10, 45), codice="23P01")


def test_11_intervalli_adiacenti_non_sono_in_conflitto(mondo):
    mondo["app"](mondo["luca"], ore(10), ore(11))
    mondo["app"](mondo["luca"], ore(11), ore(12))
    mondo["app"](mondo["luca"], ore(9), ore(10))


def test_12_agenti_diversi_non_si_bloccano(mondo):
    mondo["app"](mondo["luca"], ore(10), ore(11))
    mondo["app"](mondo["marta"], ore(10), ore(11))


def test_13_il_buffer_fa_parte_dell_intervallo_bloccato(mondo):
    # Sopralluogo 10-11 con 15' di viaggio dopo: alle 11:00 l'agente non e' libero.
    mondo["app"](mondo["luca"], ore(10), ore(11), buffer_after_minutes=15)
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(11), ore(12), codice="23P01")
    mondo["app"](mondo["luca"], ore(11, 15), ore(12))
    # e il buffer PRIMA del nuovo conta allo stesso modo
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(12, 30), ore(13),
               buffer_before_minutes=31, codice="23P01")


def test_14_solo_gli_stati_che_bloccano_occupano_l_agenda(mondo):
    mondo["app"](mondo["luca"], ore(10), ore(11), stato="requested")
    mondo["app"](mondo["luca"], ore(10), ore(11), stato="cancelled", cancelled_at=ore(8))
    mondo["app"](mondo["luca"], ore(10), ore(11), stato="rescheduled", rescheduled_at=ore(8))
    mondo["app"](mondo["luca"], ore(10), ore(11))                 # il primo che blocca passa
    for stato, extra in (("scheduled", {}), ("confirmed", {"confirmed_at": ore(8)}),
                         ("completed", {"completed_at": ore(11)}),
                         ("no_show", {"no_show_at": ore(11)})):
        _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), stato=stato,
                   codice="23P01", **extra)


def test_15_annullare_libera_lo_slot(mondo):
    r = mondo["app"](mondo["luca"], ore(10), ore(11))
    mondo["uno"]("UPDATE appointments SET status='cancelled', cancelled_at=NOW() "
                 "WHERE id=%s RETURNING id", (r["id"],))
    mondo["app"](mondo["luca"], ore(10), ore(11))


def test_16_blocked_range_lo_scrive_il_trigger_e_ignora_il_client(mondo):
    r = mondo["app"](mondo["luca"], ore(10), ore(11), buffer_before_minutes=10,
                     buffer_after_minutes=20,
                     blocked_range="[2000-01-01,2000-01-02)")
    rng = mondo["uno"]("SELECT lower(blocked_range), upper(blocked_range), "
                       "lower_inc(blocked_range), upper_inc(blocked_range) "
                       "FROM appointments WHERE id=%s", (r["id"],))
    assert rng[0] == ore(9, 50) and rng[1] == ore(11, 20)
    assert rng[2] is True and rng[3] is False
    # e segue ogni modifica di orari e buffer
    mondo["uno"]("UPDATE appointments SET buffer_after_minutes=0, end_at=%s "
                 "WHERE id=%s RETURNING id", (ore(10, 30), r["id"]))
    rng = mondo["uno"]("SELECT upper(blocked_range) FROM appointments WHERE id=%s", (r["id"],))
    assert rng[0] == ore(10, 30)


def test_17_lo_stesso_agente_e_bloccato_anche_fra_agenzie(mondo):
    """Una persona sola non puo' essere in due posti: il vincolo non guarda
    l'agenzia. (Oggi una sola membership attiva per operatore; il vincolo
    resta giusto anche quando cadra' quell'indice.)"""
    assert mondo["uno"]("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname='appointments_no_overlap_excl'")[0].count("agency_id") == 0


# ---------------------------------------------------------------------------
# C - LE MATRICI DI STATO E I VINCOLI
# ---------------------------------------------------------------------------

def test_20_uno_stato_che_occupa_tempo_richiede_un_agente(mondo):
    _rifiutato(mondo, mondo["app"], None, ore(10), ore(11), codice="23514")
    mondo["app"](None, ore(10), ore(11), stato="requested")


@pytest.mark.parametrize("stato,extra", [
    ("requested", {"confirmed_at": "2026-10-05T08:00:00+02"}),
    ("scheduled", {"cancelled_at": "2026-10-05T08:00:00+02"}),
    ("confirmed", {}),
    ("completed", {}),
    ("no_show", {}),
    ("cancelled", {}),
    ("rescheduled", {}),
    ("completed", {"completed_at": "2026-10-05T11:00:00+02",
                   "cancelled_at": "2026-10-05T11:00:00+02"}),
    ("cancelled", {"cancelled_at": "2026-10-05T08:00:00+02",
                   "completed_at": "2026-10-05T11:00:00+02"}),
])
def test_21_la_matrice_di_stato_rende_irrappresentabili_gli_stati_ambigui(mondo, stato, extra):
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), stato=stato,
               codice="23514", **extra)


def test_22_orari_tipi_e_stati_fuori_catalogo_sono_rifiutati(mondo):
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(11), ore(10), codice="23514")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(10), codice="23514")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(10) + timedelta(hours=25),
               codice="23514")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), tipo="visita", codice="23514")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), stato="booked",
               codice="23514")
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11),
               buffer_after_minutes=241, codice="23514")


def test_23_la_proiezione_lmc15_esiste_solo_per_un_sopralluogo_con_stima(mondo):
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), stato="requested",
               tipo="call", stima_id=1, stima_inspection_id=1, codice="23514")


def test_24_google_e_predisposto_e_vuoto(mondo):
    r = mondo["app"](mondo["luca"], ore(10), ore(11))
    assert r["google_sync_status"] == "not_synced"
    assert r["google_calendar_id"] is None and r["google_event_id"] is None
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(12), ore(13),
               google_event_id="evt", codice="23514")


# ---------------------------------------------------------------------------
# D - TENANCY E AGENTE
# ---------------------------------------------------------------------------

def test_30_contatto_lead_e_immobile_devono_essere_della_stessa_agenzia(mondo):
    sql, uno = mondo["sql"], mondo["uno"]
    c_a = uno("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (mondo["a"],))[0]
    c_b = uno("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (mondo["b"],))[0]
    c_a2 = uno("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (mondo["a"],))[0]
    l_a = uno("INSERT INTO leads (contact_id, agency_id) VALUES (%s,%s) RETURNING id",
              (c_a, mondo["a"]))[0]
    l_b = uno("INSERT INTO leads (contact_id, agency_id) VALUES (%s,%s) RETURNING id",
              (c_b, mondo["b"]))[0]
    p_b = uno("INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (mondo["b"],))[0]
    del sql

    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), contact_id=c_b)
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), lead_id=l_b)
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), property_id=p_b)
    # un lead e un contatto che raccontano due persone diverse
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11), lead_id=l_a,
               contact_id=c_a2)
    r = mondo["app"](mondo["luca"], ore(10), ore(11), lead_id=l_a, contact_id=c_a)
    assert r["lead_id"] == l_a


def test_31_l_agente_deve_essere_membro_attivo_di_questa_agenzia(mondo):
    # di un'altra agenzia: la FK composita
    # (il trigger arriva prima della FK composita: rifiutato in ogni caso)
    _rifiutato(mondo, mondo["app"], mondo["estraneo"], ore(10), ore(11))
    # della stessa, ma revocato: il trigger
    _rifiutato(mondo, mondo["app"], mondo["revocato"], ore(10), ore(11))


def test_32_la_revoca_dell_agente_non_rende_immodificabili_i_suoi_appuntamenti(mondo):
    r = mondo["app"](mondo["luca"], ore(10), ore(11))
    mondo["uno"]("UPDATE agency_memberships SET status='revoked' "
                 "WHERE operator_user_id=%s RETURNING id", (mondo["luca"],))
    mondo["uno"]("UPDATE appointments SET notes='nota' WHERE id=%s RETURNING id", (r["id"],))


def test_33_agenzia_provenienza_e_creazione_sono_immutabili(mondo):
    r = mondo["app"](mondo["luca"], ore(10), ore(11), created_by_user_id=mondo["giorgio"])
    for assegnazione, valore in (("agency_id", mondo["b"]), ("source", "system"),
                                 ("source_record_id", "x"),
                                 ("created_by_user_id", mondo["marta"])):
        _rifiutato(mondo, mondo["uno"],
                   f"UPDATE appointments SET {assegnazione}=%s WHERE id=%s RETURNING id",
                   (valore, r["id"]))


def test_34_chi_crea_deve_poter_agire_nell_agenzia(mondo):
    _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11),
               created_by_user_id=mondo["estraneo"])


# ---------------------------------------------------------------------------
# E - IDEMPOTENZA DEGLI IMPORT
# ---------------------------------------------------------------------------

def test_40_un_record_di_origine_produce_un_solo_appuntamento(mondo):
    mondo["app"](None, ore(10), ore(11), stato="requested", tipo="inspection",
                 source="legacy_stime_dettagliate", source_record_id="123")
    _rifiutato(mondo, mondo["app"], None, ore(15), ore(16), stato="requested",
               tipo="inspection", source="legacy_stime_dettagliate",
               source_record_id="123", codice="23505")
    # lo stesso numero in un'altra fonte e' un altro record
    mondo["app"](None, ore(10), ore(11), stato="requested", tipo="inspection",
                 source="stima_inspections_backfill", source_record_id="123")
    # e l'INSERT idempotente dell'import futuro non fa niente la seconda volta
    mondo["uno"](
        "INSERT INTO appointments (agency_id, appointment_type, status, start_at, end_at, "
        "source, source_record_id) VALUES (%s,'inspection','requested',%s,%s,"
        "'legacy_stime_dettagliate','123') "
        "ON CONFLICT (source, source_record_id) WHERE source_record_id IS NOT NULL "
        "DO NOTHING RETURNING id", (mondo["a"], ore(10), ore(11)))
    n = mondo["uno"]("SELECT count(*) FROM appointments WHERE source_record_id='123'")[0]
    assert n == 2


def test_41_una_fonte_importata_deve_dire_da_quale_record_viene(mondo):
    _rifiutato(mondo, mondo["app"], None, ore(10), ore(11), stato="requested",
               source="legacy_stime_dettagliate", codice="23514")
    _rifiutato(mondo, mondo["app"], None, ore(10), ore(11), stato="requested",
               source="google", codice="23514")


# ---------------------------------------------------------------------------
# F - IL REGISTRO
# ---------------------------------------------------------------------------

def test_50_il_registro_lo_scrive_il_service_con_l_attore(mondo, servizio):
    from appointments.schemas import AppointmentReschedule
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    r = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    n = servizio.reschedule_appointment(
        ctx, r["id"], AppointmentReschedule(start_at=ore(15), end_at=ore(16)))
    eventi = mondo["sql"]("SELECT appointment_id, event_type, from_status, to_status, "
                          "actor_user_id, db_user, changes FROM appointment_events "
                          "ORDER BY id")
    mondo["conn"].commit()
    assert [(e[0], e[1], e[2], e[3]) for e in eventi] == [
        (r["id"], "created", None, "scheduled"),
        (r["id"], "status_changed", "scheduled", "rescheduled"),
        (n["id"], "created", None, "scheduled")]
    assert {e[4] for e in eventi} == {mondo["giorgio"]}
    assert all(e[5] for e in eventi)
    assert eventi[2][6]["rescheduled_from_id"] == r["id"]
    assert eventi[1][6]["status"] == {"da": "scheduled", "a": "rescheduled"}


def test_50b_nessun_trigger_scrive_il_registro(mondo):
    """P26-6, serie 100: nessuna scrittura invisibile in una tabella di
    tenant. Una scrittura FUORI dal service non lascia eventi: e' il limite
    dichiarato, il prezzo di tenere il registro nominato in Python."""
    r = mondo["app"](mondo["luca"], ore(10), ore(11))
    mondo["uno"]("UPDATE appointments SET notes='x' WHERE id=%s RETURNING id", (r["id"],))
    assert mondo["uno"]("SELECT count(*) FROM appointment_events")[0] == 0


def test_51_version_e_updated_at_li_scrive_il_trigger(mondo):
    r = mondo["app"](mondo["luca"], ore(10), ore(11), version=99)
    assert r["version"] == 1
    v = mondo["uno"]("UPDATE appointments SET notes='x' WHERE id=%s RETURNING version",
                     (r["id"],))
    assert v[0] == 2


def test_52_un_appuntamento_non_si_cancella_e_il_registro_non_si_riscrive(mondo, servizio):
    r = servizio.create_appointment(Ctx(mondo["a"], mondo["giorgio"]),
                                    _corpo(assigned_user_id=mondo["luca"]))
    _rifiutato(mondo, mondo["uno"], "DELETE FROM appointments WHERE id=%s RETURNING id",
               (r["id"],))
    _rifiutato(mondo, mondo["uno"], "UPDATE appointment_events SET to_status='x' "
               "WHERE appointment_id=%s RETURNING id", (r["id"],))
    _rifiutato(mondo, mondo["uno"], "DELETE FROM appointment_events "
               "WHERE appointment_id=%s RETURNING id", (r["id"],))


# ---------------------------------------------------------------------------
# G - IL SERVICE
# ---------------------------------------------------------------------------

def _corpo(**kw):
    from appointments.schemas import AppointmentCreate
    base = {"appointment_type": "seller_meeting", "start_at": ore(10), "end_at": ore(11)}
    base.update(kw)
    return AppointmentCreate(**base)


def test_60_il_service_rifiuta_con_i_conflitti_leggibili(mondo, servizio):
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    primo = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    assert primo["created_by_user_id"] == mondo["giorgio"] and primo["source"] == "crm_manual"
    with pytest.raises(servizio.AppointmentConflict) as info:
        servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"],
                                                start_at=ore(10, 30), end_at=ore(11, 30)))
    assert [c["id"] for c in info.value.conflicts] == [primo["id"]]
    assert set(info.value.conflicts[0]) == {"id", "appointment_type", "status",
                                            "start_at", "end_at"}
    # e il controllo in sola lettura dice la stessa cosa
    conflitti = servizio.find_conflicts(ctx, assigned_user_id=mondo["luca"],
                                        start_at=ore(10, 30), end_at=ore(11, 30))
    assert [c["id"] for c in conflitti] == [primo["id"]]
    assert servizio.find_conflicts(ctx, assigned_user_id=mondo["luca"],
                                   start_at=ore(11), end_at=ore(12)) == []


def test_61_l_attore_del_service_finisce_nel_registro(mondo, servizio):
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    r = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    attore = mondo["uno"]("SELECT actor_user_id FROM appointment_events "
                          "WHERE appointment_id=%s", (r["id"],))[0]
    assert attore == mondo["giorgio"]


def test_62_un_agente_scrive_solo_nella_propria_agenda(mondo, servizio):
    from core.exceptions import PermissionDenied
    ctx = Ctx(mondo["a"], mondo["luca"], assegna=False)
    with pytest.raises(PermissionDenied):
        servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["marta"]))
    assert servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))


def test_63_agente_di_un_altra_agenzia_o_revocato_rifiutato_dal_service(mondo, servizio):
    from core.exceptions import ValidationError
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    for agente in (mondo["estraneo"], mondo["revocato"]):
        with pytest.raises(ValidationError):
            servizio.create_appointment(ctx, _corpo(assigned_user_id=agente))


def test_64_la_stima_si_verifica_nel_service_e_una_stima_altrui_non_esiste(mondo, servizio):
    from core.exceptions import NotFoundError
    st_b = mondo["uno"]("INSERT INTO stime (agency_id) VALUES (%s) RETURNING id",
                        (mondo["b"],))[0]
    st_a = mondo["uno"]("INSERT INTO stime (agency_id) VALUES (%s) RETURNING id",
                        (mondo["a"],))[0]
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    for stima in (st_b, 99999999):
        with pytest.raises(NotFoundError):
            servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"],
                                                    stima_id=stima))
    r = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"], stima_id=st_a))
    assert r["stima_id"] == st_a


def test_65_sopralluogo_con_stima_solo_come_richiesta_finche_manca_la_proiezione(
        mondo, servizio, monkeypatch):
    # SENTINELLA AGGIORNATA DA A30-2P: la proiezione e' accesa; l'invariante
    # di A30-1 vale ora a interruttore SPENTO (arresto d'emergenza), che qui
    # si spegne solo dentro il test.
    from appointments import projection
    monkeypatch.setattr(projection, "PROJECTION_ENABLED", False)
    st = mondo["uno"]("INSERT INTO stime (agency_id) VALUES (%s) RETURNING id",
                      (mondo["a"],))[0]
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    with pytest.raises(servizio.InspectionProjectionNotActive):
        servizio.create_appointment(ctx, _corpo(appointment_type="inspection", stima_id=st,
                                                assigned_user_id=mondo["luca"]))
    r = servizio.create_appointment(ctx, _corpo(appointment_type="inspection", stima_id=st,
                                                status="requested"))
    assert r["status"] == "requested" and r["assigned_user_id"] is None
    # e nessuna riga LMC-15 e' nata
    assert mondo["uno"]("SELECT count(*) FROM stima_inspections")[0] == 0
    # un sopralluogo SENZA stima non ha nulla da proiettare
    assert servizio.create_appointment(ctx, _corpo(appointment_type="inspection",
                                                   assigned_user_id=mondo["luca"]))


def test_66_orari_senza_fuso_sono_rifiutati_dallo_schema():
    from pydantic import ValidationError as PydanticError
    with pytest.raises(PydanticError):
        _corpo(start_at=datetime(2026, 10, 5, 10), end_at=datetime(2026, 10, 5, 11))


def test_67_spostare_crea_una_riga_nuova_e_libera_la_vecchia(mondo, servizio):
    from appointments.schemas import AppointmentReschedule
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    vecchio = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    # spostarlo di mezz'ora sovrappone il VECCHIO intervallo: non e' un conflitto
    nuovo = servizio.reschedule_appointment(
        ctx, vecchio["id"], AppointmentReschedule(start_at=ore(10, 30), end_at=ore(11, 30)))
    assert nuovo["rescheduled_from_id"] == vecchio["id"] and nuovo["status"] == "scheduled"
    stato = mondo["uno"]("SELECT status, rescheduled_at IS NOT NULL FROM appointments "
                         "WHERE id=%s", (vecchio["id"],))
    assert stato[0] == "rescheduled" and stato[1] is True
    # la vecchia riga non blocca piu'
    assert servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"],
                                                   start_at=ore(9), end_at=ore(10, 30)))
    # e una riga gia' spostata non si sposta di nuovo
    from core.exceptions import ConflictError
    with pytest.raises(ConflictError):
        servizio.reschedule_appointment(
            ctx, vecchio["id"], AppointmentReschedule(start_at=ore(15), end_at=ore(16)))


def test_68_uno_spostamento_in_conflitto_non_lascia_niente_a_meta(mondo, servizio):
    from appointments.schemas import AppointmentReschedule
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    a = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    b = servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"],
                                                start_at=ore(14), end_at=ore(15)))
    with pytest.raises(servizio.AppointmentConflict):
        servizio.reschedule_appointment(
            ctx, a["id"], AppointmentReschedule(start_at=ore(14, 30), end_at=ore(15, 30)))
    assert mondo["uno"]("SELECT status FROM appointments WHERE id=%s", (a["id"],))[0] == \
        "scheduled"
    assert mondo["uno"]("SELECT count(*) FROM appointments")[0] == 2
    # verso un altro agente libero, invece, si puo'
    n = servizio.reschedule_appointment(
        ctx, a["id"], AppointmentReschedule(start_at=ore(14, 30), end_at=ore(15, 30),
                                            assigned_user_id=mondo["marta"]))
    assert n["assigned_user_id"] == mondo["marta"]
    del b


def test_69_un_agente_non_sposta_gli_appuntamenti_altrui(mondo, servizio):
    from appointments.schemas import AppointmentReschedule
    from core.exceptions import NotFoundError
    r = servizio.create_appointment(Ctx(mondo["a"], mondo["giorgio"]),
                                    _corpo(assigned_user_id=mondo["marta"]))
    with pytest.raises(NotFoundError):
        servizio.reschedule_appointment(
            Ctx(mondo["a"], mondo["luca"], assegna=False), r["id"],
            AppointmentReschedule(start_at=ore(15), end_at=ore(16)))
    # e un'altra agenzia non lo vede nemmeno
    with pytest.raises(NotFoundError):
        servizio.reschedule_appointment(
            Ctx(mondo["b"], mondo["estraneo"]), r["id"],
            AppointmentReschedule(start_at=ore(15), end_at=ore(16)))


# ---------------------------------------------------------------------------
# H - LA CONCORRENZA, SENZA SLEEP COME PROVA
# ---------------------------------------------------------------------------

def _attendi_blocco(dsn, applicazione, tipo_attesa, timeout=10.0):
    """True quando la connessione `applicazione` e' ferma su un'attesa del
    tipo dato. E' la prova che la seconda transazione ASPETTA; il ciclo
    serve solo a darle il tempo di arrivarci."""
    import psycopg2
    osservatore = psycopg2.connect(dsn)
    osservatore.autocommit = True
    try:
        fine = time.monotonic() + timeout
        while time.monotonic() < fine:
            with osservatore.cursor() as cur:
                cur.execute("SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE application_name = %s", (applicazione,))
                riga = cur.fetchone()
                if riga and riga[0] == tipo_attesa:
                    return True
            time.sleep(0.02)
        return False
    finally:
        osservatore.close()


def test_70_due_prenotazioni_concorrenti_dello_stesso_agente_una_sola_vince(
        mondo, db, monkeypatch):
    """T1 tiene il lock dell'agente e ha scritto, senza commit. T2 (il
    service) deve FERMARSI sul lock consultivo; quando T1 fa commit, T2
    vede l'appuntamento di T1 e risponde con un conflitto leggibile."""
    import psycopg2

    from appointments import repository, service
    from core import database as core_database

    applicazione = f"a30_t2_{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(core_database, "get_connection",
                        lambda: psycopg2.connect(db["dsn"], application_name=applicazione))

    t1 = psycopg2.connect(db["dsn"])
    from psycopg2.extras import RealDictCursor
    cur1 = t1.cursor(cursor_factory=RealDictCursor)
    repository.lock_agents(cur1, [mondo["luca"]])
    cur1.execute("INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, "
                 "status, start_at, end_at) VALUES (%s,%s,'call','scheduled',%s,%s) "
                 "RETURNING id", (mondo["a"], mondo["luca"], ore(10), ore(11)))
    id_t1 = cur1.fetchone()["id"]

    esito = {}

    def t2():
        try:
            esito["ok"] = service.create_appointment(
                Ctx(mondo["a"], mondo["giorgio"]), _corpo(assigned_user_id=mondo["luca"]))
        except Exception as exc:  # noqa: BLE001 - si ispeziona sotto
            esito["errore"] = exc

    filo = threading.Thread(target=t2)
    filo.start()
    try:
        assert _attendi_blocco(db["dsn"], applicazione, "Lock"), \
            "T2 non si e' fermata sul lock dell'agente"
        assert filo.is_alive()
    finally:
        t1.commit()
        t1.close()
        filo.join(timeout=10)

    assert "ok" not in esito
    assert isinstance(esito["errore"], service.AppointmentConflict)
    assert [c["id"] for c in esito["errore"].conflicts] == [id_t1]


def test_71_anche_scavalcando_il_service_il_database_ne_lascia_passare_uno(mondo, db):
    """Nessun lock consultivo: due INSERT grezze sovrapposte. La seconda
    aspetta la prima sul vincolo EXCLUDE e, al commit della prima, fallisce
    con 23P01. E' il livello 1, da solo."""
    import psycopg2

    applicazione = f"a30_raw_{uuid.uuid4().hex[:6]}"
    t1 = psycopg2.connect(db["dsn"])
    t2 = psycopg2.connect(db["dsn"], application_name=applicazione)
    inserisci = ("INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, "
                 "status, start_at, end_at) VALUES (%s,%s,'call','scheduled',%s,%s)")
    with t1.cursor() as c:
        c.execute(inserisci, (mondo["a"], mondo["marta"], ore(10), ore(11)))

    esito = {}

    def secondo():
        try:
            with t2.cursor() as c:
                c.execute(inserisci, (mondo["a"], mondo["marta"], ore(10, 30), ore(11, 30)))
            t2.commit()
            esito["ok"] = True
        except psycopg2.Error as exc:
            esito["codice"] = exc.pgcode
            t2.rollback()

    filo = threading.Thread(target=secondo)
    filo.start()
    try:
        assert _attendi_blocco(db["dsn"], applicazione, "Lock")
    finally:
        t1.commit()
        filo.join(timeout=10)
        t1.close()
        t2.close()
    assert esito == {"codice": "23P01"}


def test_72_il_service_traduce_la_violazione_del_vincolo_in_conflitto(mondo, servizio,
                                                                      monkeypatch):
    """Se il controllo applicativo venisse saltato (qui: forzato a vuoto),
    il vincolo resta, e il chiamante riceve lo stesso tipo di errore."""
    from appointments import repository
    ctx = Ctx(mondo["a"], mondo["giorgio"])
    servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    monkeypatch.setattr(repository, "find_conflicts", lambda *a, **k: [])
    with pytest.raises(servizio.AppointmentConflict):
        servizio.create_appointment(ctx, _corpo(assigned_user_id=mondo["luca"]))
    assert mondo["uno"]("SELECT count(*) FROM appointments")[0] == 1


# ---------------------------------------------------------------------------
# J - Q4: L'AGENTE NON SI INFERISCE
# ---------------------------------------------------------------------------

def test_90_scheduled_e_confirmed_senza_agente_sono_rifiutati_anche_se_storici(mondo):
    for stato, extra in (("scheduled", {}), ("confirmed", {"confirmed_at": ore(8)})):
        _rifiutato(mondo, mondo["app"], None, ore(10), ore(11), stato=stato,
                   source="stima_inspections_backfill", source_record_id=f"x-{stato}",
                   codice="23514", **extra)


def test_91_un_record_storico_chiuso_puo_non_avere_agente(mondo):
    mondo["app"](None, ore(10), ore(11), stato="completed", completed_at=ore(11),
                 tipo="inspection", source="stima_inspections_backfill", source_record_id="7")
    mondo["app"](None, ore(12), ore(13), stato="no_show", no_show_at=ore(13),
                 tipo="inspection", source="legacy_stime_dettagliate", source_record_id="8")


def test_92_un_appuntamento_crm_chiuso_senza_agente_e_rifiutato(mondo):
    for stato, extra in (("completed", {"completed_at": ore(11)}),
                         ("no_show", {"no_show_at": ore(11)})):
        _rifiutato(mondo, mondo["app"], None, ore(10), ore(11), stato=stato,
                   codice="23514", **extra)
    # annullare una richiesta mai attribuita resta possibile
    mondo["app"](None, ore(10), ore(11), stato="cancelled", cancelled_at=ore(9))


# ---------------------------------------------------------------------------
# K - Q7: DATI DI PROVA E PURGE PROTETTA, IN ALLOWLIST TEST
#
# Un dato di prova nasce, e muore, SOLO sul TEST certificato
# (`stima360_db_test`). Queste prove girano quindi su database usa-e-getta
# che portano DAVVERO quel nome, o un nome PROD, o un nome simile: e' il solo
# modo di provare che le guardie leggono `current_database()`.
# ---------------------------------------------------------------------------

@contextmanager
def _database_chiamato(nome):
    """Un database usa-e-getta con un NOME preciso, nel cluster di prova.
    Se un database con quel nome esiste gia', NON lo si tocca: si salta."""
    psycopg2 = pytest.importorskip("psycopg2")
    schema, catena = _schema_e_catena()
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nome,))
        if cur.fetchone():
            servizio.close()
            pytest.skip(f"esiste gia' un database {nome!r} in questo cluster: non lo tocco")
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(schema)
            for versione in catena:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute(SU)
            cur.execute("INSERT INTO agencies (slug) VALUES ('p') RETURNING id")
            agenzia = cur.fetchone()[0]
            operatori = {}
            for chiave, ruolo in (("giorgio", "agency_owner"), ("luca", "agent"),
                                  ("marta", "agent")):
                cur.execute("INSERT INTO operator_users (email) VALUES (%s) RETURNING id",
                            (f"{chiave}@example.it",))
                operatori[chiave] = cur.fetchone()[0]
                cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role) "
                            "VALUES (%s,%s,%s)", (agenzia, operatori[chiave], ruolo))
        yield {"conn": conn, "dsn": dsn, "agency": agenzia, "nome": nome, **operatori}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _riga_grezza(conn, agenzia, agente, inizio, fine, *, run=None, **extra):
    colonne = {"agency_id": agenzia, "assigned_user_id": agente,
               "appointment_type": "call", "status": "scheduled",
               "start_at": inizio, "end_at": fine,
               "source": "a30_test" if run else "crm_manual", "test_run_id": run}
    colonne.update(extra)
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO appointments ({', '.join(colonne)}) "
                    f"VALUES ({', '.join('%s' for _ in colonne)}) RETURNING id",
                    tuple(colonne.values()))
        return cur.fetchone()[0]


def _errore(conn, funzione, *args, match=None, codice=None, **kwargs):
    """Una scrittura rifiutata, lasciando la connessione utilizzabile."""
    import psycopg2
    with pytest.raises(psycopg2.Error, match=match) as info:
        funzione(*args, **kwargs)
    if not conn.autocommit:
        conn.rollback()
    if codice is not None:
        assert info.value.pgcode == codice, (info.value.pgcode, str(info.value))
    return info.value


#: Il nome TEST certificato dal progetto (allowlist della 072).
NOME_TEST_CERTIFICATO = "stima360_db_test"

#: Nomi che NON sono il TEST certificato, pur somigliandogli o somigliando a
#: PROD: devono fallire chiusi tutti.
NOMI_SIMILI = ("stima360_db_test2", "stima360_db_tes", "STIMA360_DB_TEST",
               "stima360_db_test_copy", "stima360_db2", "stima360_prod")


@pytest.fixture(scope="module")
def db_certificato(db):
    with _database_chiamato(NOME_TEST_CERTIFICATO) as info:
        yield info


@pytest.fixture
def certificato(db_certificato):
    """Il TEST certificato, vuoto a ogni prova."""
    conn = db_certificato["conn"]
    conn.autocommit = True
    with conn.cursor() as cur:
        _svuota(cur)
    return db_certificato


@pytest.fixture(scope="module")
def db_prod(db):
    with _database_chiamato("stima360") as info:
        yield info


def test_93_il_marcatore_di_prova_e_la_corsa_vanno_insieme(certificato):
    d = certificato
    conn = d["conn"]
    # marcatore senza corsa, corsa senza marcatore, corsa malformata: CHECK
    _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(10), ore(11),
            source="a30_test", codice="23514")
    _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(10), ore(11),
            test_run_id="r1", codice="23514")
    _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(10), ore(11),
            run="run con spazi", codice="23514")
    r = _riga_grezza(conn, d["agency"], d["luca"], ore(10), ore(11), run="run-1")
    with conn.cursor() as cur:
        _errore(conn, cur.execute,
                "UPDATE appointments SET test_run_id='run-2' WHERE id=%s", (r,),
                match="immutable")


def test_94_fuori_dalla_purge_anche_sul_test_certificato_niente_delete(certificato):
    d = certificato
    conn = d["conn"]
    r = _riga_grezza(conn, d["agency"], d["luca"], ore(10), ore(11), run="run-1")
    vera = _riga_grezza(conn, d["agency"], d["marta"], ore(10), ore(11))
    with conn.cursor() as cur:
        _errore(conn, cur.execute, "DELETE FROM appointments WHERE id=%s", (r,),
                match="refused")
        # dichiarare la corsa a mano non sblocca una riga che di prova non e'
        conn.autocommit = False
        with pytest.raises(Exception, match="refused"):
            cur.execute("SELECT set_config('stima360.a30_test_purge', 'run-1', true)")
            cur.execute("DELETE FROM appointments WHERE id=%s", (vera,))
        conn.rollback()
        conn.autocommit = True


def test_95_nome_sconosciuto_nessun_dato_di_prova_e_nessuna_purge(mondo):
    """Il database del modulo si chiama `a30_1_probe_*`: non e' PROD, ma non
    e' un TEST certificato. Il dato di prova non nasce, la purge fallisce."""
    assert mondo["uno"]("SELECT a30_is_certified_test_database()")[0] is False
    err = _rifiutato(mondo, mondo["app"], mondo["luca"], ore(10), ore(11),
                     source="a30_test", test_run_id="run-1")
    assert "not a certified TEST database" in str(err)
    err = _rifiutato(mondo, mondo["uno"], "SELECT * FROM a30_test_purge('run-1')")
    assert "not a certified TEST database" in str(err)
    assert mondo["uno"]("SELECT count(*) FROM appointments WHERE source='a30_test'")[0] == 0


def test_96_uno_spostamento_non_cambia_mondo(certificato):
    d = certificato
    conn = d["conn"]
    a = _riga_grezza(conn, d["agency"], d["luca"], ore(10), ore(11), run="run-1")
    _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(15), ore(16),
            rescheduled_from_id=a, match="test marker")
    v = _riga_grezza(conn, d["agency"], d["marta"], ore(10), ore(11))
    _errore(conn, _riga_grezza, conn, d["agency"], d["marta"], ore(15), ore(16),
            run="run-1", rescheduled_from_id=v, match="test marker")
    # e cambiare corsa nello spostamento non e' ammesso
    _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(15), ore(16),
            run="run-2", rescheduled_from_id=a, match="test marker")


def test_97_la_purge_rifiuta_una_corsa_non_valida(certificato):
    conn = certificato["conn"]
    with conn.cursor() as cur:
        for run in (None, "", "a b", "x" * 65):
            _errore(conn, cur.execute, "SELECT * FROM a30_test_purge(%s)", (run,),
                    match="valid test run id")


def test_98a_test_certificato_insert_purge_e_spostamento(certificato, monkeypatch):
    """TEST noto -> dato di prova ammesso, purge consentita, e SOLO per la sua
    corsa; lo spostamento via service resta nella stessa corsa."""
    import psycopg2

    from appointments import service
    from appointments.schemas import AppointmentCreate, AppointmentReschedule
    from core import database as core_database

    d = certificato
    conn = d["conn"]
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), a30_is_certified_test_database()")
        assert cur.fetchone() == (NOME_TEST_CERTIFICATO, True)

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(d["dsn"]))
    ctx = Ctx(d["agency"], d["giorgio"])
    a = _riga_grezza(conn, d["agency"], d["luca"], ore(10), ore(11), run="run-1")
    b = service.reschedule_appointment(
        ctx, a, AppointmentReschedule(start_at=ore(15), end_at=ore(16)))
    assert b["source"] == "a30_test" and b["test_run_id"] == "run-1"
    altra = _riga_grezza(conn, d["agency"], d["marta"], ore(10), ore(11), run="run-2")
    vera = service.create_appointment(ctx, AppointmentCreate(
        appointment_type="call", assigned_user_id=d["marta"],
        start_at=ore(12), end_at=ore(13)))

    with conn.cursor() as cur:
        _errore(conn, cur.execute, "DELETE FROM appointment_events WHERE appointment_id=%s",
                (vera["id"],), match="append-only")
        cur.execute("SELECT appointments_deleted, events_deleted FROM a30_test_purge('run-1')")
        assert cur.fetchone() == (2, 2)   # a e b; eventi: a 'status_changed', b 'created'
        cur.execute("SELECT id FROM appointments ORDER BY id")
        assert [r[0] for r in cur.fetchall()] == [altra, vera["id"]]
        cur.execute("SELECT count(*) FROM appointment_events WHERE appointment_id=%s",
                    (vera["id"],))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT current_setting('stima360.a30_test_purge', true)")
        assert cur.fetchone()[0] in (None, "")
        cur.execute("SELECT * FROM a30_test_purge('run-1')")
        assert cur.fetchone() == (0, 0)


def test_98b_prod_nota_dati_di_prova_e_purge_rifiutati(db_prod):
    """PROD noto -> rifiutata. E prima ancora: nessun dato di prova entra."""
    d = db_prod
    conn = d["conn"]
    with conn.cursor() as cur:
        cur.execute("SELECT a30_is_certified_test_database()")
        assert cur.fetchone()[0] is False
        _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(10), ore(11),
                run="run-1", match="not a certified TEST database")
        _errore(conn, cur.execute, "SELECT * FROM a30_test_purge('run-1')",
                match="not a certified TEST database")
        vera = _riga_grezza(conn, d["agency"], d["luca"], ore(10), ore(11))
        conn.autocommit = False
        with pytest.raises(Exception, match="refused"):
            cur.execute("SELECT set_config('stima360.a30_test_purge', 'run-1', true)")
            cur.execute("DELETE FROM appointments WHERE id=%s", (vera,))
        conn.rollback()
        conn.autocommit = True


@pytest.mark.parametrize("nome", NOMI_SIMILI)
def test_98c_nome_simile_nessun_dato_di_prova_e_nessuna_purge(db, nome):
    """Nome simile (a PROD o al TEST certificato) -> il dato di prova non
    entra, la purge fallisce, e dichiarare a mano la corsa non sblocca la
    DELETE di nessuna riga."""
    with _database_chiamato(nome) as d:
        conn = d["conn"]
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), a30_is_certified_test_database()")
            assert cur.fetchone() == (nome, False)
            _errore(conn, _riga_grezza, conn, d["agency"], d["luca"], ore(10), ore(11),
                    run="run-1", match="not a certified TEST database")
            _errore(conn, cur.execute, "SELECT * FROM a30_test_purge('run-1')",
                    match="not a certified TEST database")
            vera = _riga_grezza(conn, d["agency"], d["luca"], ore(10), ore(11))
            conn.autocommit = False
            with pytest.raises(Exception, match="refused"):
                cur.execute("SELECT set_config('stima360.a30_test_purge', 'run-1', true)")
                cur.execute("DELETE FROM appointments WHERE id=%s", (vera,))
            conn.rollback()
            conn.autocommit = True
            cur.execute("SELECT count(*) FROM appointments")
            assert cur.fetchone()[0] == 1


def test_99_lo_script_di_pulizia_e_in_allowlist(certificato, monkeypatch):
    import psycopg2

    from scripts import a30_test_cleanup as pulizia
    from scripts.p26_migrate import GuardFailure

    d = certificato
    _riga_grezza(d["conn"], d["agency"], d["luca"], ore(18), ore(19), run="run-9")
    conn = psycopg2.connect(d["dsn"])
    try:
        conta = pulizia.purge_run(conn, "run-9", apply=False)
        assert conta == {"run_id": "run-9", "appointments": 1, "events": 0, "applied": False}
        fatto = pulizia.purge_run(conn, "run-9", apply=True)
        assert fatto["applied"] and fatto["appointments_deleted"] == 1
    finally:
        conn.close()

    assert pulizia.assert_certified_test_database(NOME_TEST_CERTIFICATO) == NOME_TEST_CERTIFICATO
    for nome in ("stima360_db", "stima360", "", "a30_1_probe", *NOMI_SIMILI):
        with pytest.raises(GuardFailure):
            pulizia.assert_certified_test_database(nome)
    monkeypatch.setattr(pulizia, "connect",
                        lambda *_: pytest.fail("non deve nemmeno connettersi"))
    for nome in ("stima360_db", "stima360_db_test2"):
        monkeypatch.setenv("DB_NAME", nome)
        assert pulizia.main(["--run-id", "run-9", "--apply"]) == 2
    with pytest.raises(GuardFailure):
        pulizia.assert_run_id("a b")


# ---------------------------------------------------------------------------
# I - LA MIGRATION: DOWN CHE RIFIUTA, DOWN, DI NUOVO UP
# ---------------------------------------------------------------------------

def test_80_down_rifiuta_con_righe_poi_down_e_up_sono_puliti(mondo):
    conn = mondo["conn"]
    mondo["app"](mondo["luca"], ore(10), ore(11))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            with pytest.raises(Exception, match="would be destroyed"):
                cur.execute(GIU)
            cur.execute("ROLLBACK")
            assert cur.connection.closed == 0
            cur.execute("SELECT count(*) FROM appointments")
            assert cur.fetchone()[0] == 1

            _svuota(cur)
            cur.execute(GIU)
            cur.execute("SELECT to_regclass('public.appointments'), "
                        "to_regclass('public.appointment_events')")
            assert cur.fetchone() == [None, None]
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname LIKE 'appointment%' "
                        "OR proname LIKE 'a30\\_%'")
            assert cur.fetchone()[0] == 0
            # la 070 e' intatta
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname = "
                        "'lmc15_assert_operator_may_act'")
            assert cur.fetchone()[0] == 1
            # btree_gist resta (dichiarato nella down)
            cur.execute("SELECT count(*) FROM pg_extension WHERE extname='btree_gist'")
            assert cur.fetchone()[0] == 1

            cur.execute(SU)   # di nuovo su
            cur.execute(SU)   # e rieseguita: idempotente
            cur.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='appointments'::regclass "
                        "AND NOT tgisinternal")
            assert cur.fetchone()[0] == 2
    finally:
        conn.autocommit = False
