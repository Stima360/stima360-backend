"""A30-2P - migration 073 (facade LMC-15) su PostgreSQL usa-e-getta.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Riusa il database usa-e-getta e
il mondo di prova di `test_a30_2_appointments_postgres.py` (catena LMC-15 +
072); questo modulo non apre connessioni proprie. La 073 e la sua down si
applicano DENTRO il test, sul database del modulo: mai su TEST o PROD.

Si prova: prima della 073 una riga facade senza agente e' rifiutata; dopo, le
sole righe ammesse sono quelle previste; la DELETE della riga LMC-15 collegata
fallisce; una riga del backfill resta valida; la down rifiuta con righe
facade e, senza, riporta i CHECK ESATTAMENTE come nella 072.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_a30_2_appointments_postgres import DSN, db, mondo, ore  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la 073")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
SU = (MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8")
GIU = (MIGRAZIONI / "073_a30_2p_lmc15_facade_down.sql").read_text(encoding="utf-8")
CHECK = ("appointments_source_chk", "appointments_agent_required_chk",
         "appointments_lmc15_facade_chk")


# ---------------------------------------------------------------------------
# strumenti
# ---------------------------------------------------------------------------

def _definizioni(mondo):
    righe = mondo["sql"](
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'appointments'::regclass AND conname = ANY(%s)", (list(CHECK),))
    return dict(righe)


def _applicata(mondo):
    return "appointments_lmc15_facade_chk" in _definizioni(mondo)


def _su(mondo, forza=False):
    """Applica la 073 come il runner: corpo + riga del ledger, una transazione.
    `forza`: la applica anche se lo schema sembra gia' alla 073 (deriva)."""
    if _applicata(mondo) and not forza:
        return
    with mondo["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(SU)
        cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES ('073_a30_2p_lmc15_facade', NOW())")
        cur.execute("COMMIT")


def _giu(mondo):
    with mondo["conn"].cursor() as cur:
        try:
            cur.execute(GIU)
        except Exception:
            cur.execute("ROLLBACK")
            raise


@pytest.fixture
def prima_della_073(mondo):
    """Lo schema della 072: se una prova precedente ha applicato la 073, la
    down (senza righe facade: il mondo e' appena stato svuotato) la toglie."""
    if _applicata(mondo):
        _giu(mondo)
    return mondo


@pytest.fixture
def con_la_073(mondo):
    _su(mondo)
    return mondo


def _stima_inspection(mondo):
    return mondo["sql"](
        "INSERT INTO stima_inspections (stima_id, stima_id_snapshot, status, scheduled_for, "
        "created_by_operator_user_id) VALUES (%s, %s, 'scheduled', %s, %s) RETURNING id",
        (mondo["stima"], mondo["stima"], ore(9), mondo["giorgio"]))[0][0]


def _riga(mondo, *, source="lmc15_facade", status="scheduled", tipo="inspection",
          agente=None, stima=True, collegata=True, chiave=True):
    """INSERT diretta in `appointments` (sotto il service: prova i CHECK)."""
    ispezione = _stima_inspection(mondo) if collegata else None
    colonne = {
        "agency_id": mondo["a"], "assigned_user_id": agente, "appointment_type": tipo,
        "status": status, "start_at": ore(9), "end_at": ore(10),
        "stima_id": mondo["stima"] if stima else None,
        "stima_inspection_id": ispezione, "source": source,
        "source_record_id": (f"stima_inspections:{ispezione or 'x'}" if chiave else None),
        "created_by_user_id": mondo["giorgio"],
    }
    if status == "confirmed":
        colonne["confirmed_at"] = ore(8)
    if status == "completed":
        colonne["completed_at"] = ore(9, 50)
    nomi = list(colonne)
    return mondo["sql"](
        f"INSERT INTO appointments ({', '.join(nomi)}) VALUES "
        f"({', '.join('%s' for _ in nomi)}) RETURNING id", [colonne[n] for n in nomi])[0][0]


def _rifiutata(mondo, vincoli, **kw):
    """Rifiutata da uno dei CHECK attesi. PostgreSQL non garantisce l'ordine
    in cui valuta i CHECK: quando una riga ne viola piu' d'uno, vale il primo
    che trova. Le prove "isolate" sotto violano UN solo CHECK."""
    import psycopg2
    if isinstance(vincoli, str):
        vincoli = (vincoli,)
    with pytest.raises(psycopg2.errors.CheckViolation) as preso:
        _riga(mondo, **kw)
    assert any(v in str(preso.value) for v in vincoli), str(preso.value)
    return str(preso.value)


# ---------------------------------------------------------------------------
# A - PRIMA DELLA 073
# ---------------------------------------------------------------------------

def test_01_prima_della_073_la_facade_senza_agente_e_rifiutata(prima_della_073):
    mondo = prima_della_073
    _rifiutata(mondo, ("appointments_source_chk", "appointments_agent_required_chk"),
               status="scheduled")
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 0


# ---------------------------------------------------------------------------
# B - DOPO LA 073: ammesse e rifiutate
# ---------------------------------------------------------------------------

def test_10_ammesse_scheduled_e_completed_senza_agente(con_la_073):
    mondo = con_la_073
    a = _riga(mondo, status="scheduled")
    b = _riga(mondo, status="completed")
    righe = mondo["sql"]("SELECT status, assigned_user_id, source FROM appointments "
                         "WHERE id = ANY(%s) ORDER BY id", ([a, b],))
    assert [tuple(r) for r in righe] == [("scheduled", None, "lmc15_facade"),
                                         ("completed", None, "lmc15_facade")]


AGENTE = "appointments_agent_required_chk"
FACADE = "appointments_lmc15_facade_chk"
LINK = "appointments_inspection_link_chk"          # della 072: link solo con inspection+stima


@pytest.mark.parametrize("caso, vincoli, kw", [
    ("facade confirmed senza agente", (AGENTE,), {"status": "confirmed"}),
    ("facade senza stima_inspection_id", (AGENTE, FACADE), {"collegata": False}),
    ("facade senza stima_id", (FACADE, LINK), {"stima": False}),
    ("facade senza source_record_id", (FACADE,), {"chiave": False}),
    ("facade di tipo diverso da inspection", (AGENTE, FACADE, LINK), {"tipo": "seller_meeting"}),
    ("crm_manual scheduled senza agente", (AGENTE,),
     {"source": "crm_manual", "status": "scheduled", "chiave": False}),
    ("crm_manual completed senza agente", (AGENTE,),
     {"source": "crm_manual", "status": "completed", "chiave": False}),
])
def test_11_rifiutate(con_la_073, caso, vincoli, kw):
    _rifiutata(con_la_073, vincoli, **kw)


@pytest.mark.parametrize("caso, kw", [
    # con un agente e senza collegamento, violano SOLO il CHECK della facade
    ("facade con agente senza stima_inspection_id", {"collegata": False}),
    ("facade con agente senza stima_id", {"collegata": False, "stima": False}),
    ("facade con agente senza source_record_id", {"chiave": False}),
    ("facade con agente di tipo diverso da inspection",
     {"collegata": False, "tipo": "seller_meeting"}),
])
def test_12_isolate_il_check_della_facade_regge_anche_con_un_agente(con_la_073, caso, kw):
    mondo = con_la_073
    messaggio = _rifiutata(mondo, (FACADE,), agente=mondo["luca"], **kw)
    assert AGENTE not in messaggio and LINK not in messaggio


def test_13_confirmed_con_agente_resta_ammesso(con_la_073):
    mondo = con_la_073
    assert _riga(mondo, status="confirmed", agente=mondo["luca"])


def test_14_delete_della_riga_lmc15_collegata_fallisce(con_la_073):
    import psycopg2
    mondo = con_la_073
    a = _riga(mondo, status="scheduled")
    ispezione = mondo["sql"]("SELECT stima_inspection_id FROM appointments WHERE id=%s",
                             (a,))[0][0]
    with pytest.raises(psycopg2.errors.CheckViolation):
        mondo["sql"]("DELETE FROM stima_inspections WHERE id = %s", (ispezione,))
    assert mondo["sql"]("SELECT count(*) FROM stima_inspections WHERE id=%s",
                        (ispezione,))[0][0] == 1


def test_15_la_riga_del_backfill_resta_valida_dopo_la_073(prima_della_073):
    from acquisition import repository as lmc15
    from appointments import backfill
    from core.database import core_cursor
    mondo = prima_della_073
    lmc15.create_inspection(mondo["a"], stima_id=mondo["stima"], scheduled_for=ore(9),
                            actor_user_id=mondo["giorgio"])
    with core_cursor(commit=True) as (_, cur):
        assert backfill.run_backfill(cur, apply=True)["inserted"] == 1
    _su(mondo)                                    # la 073 valida anche le righe esistenti
    stato, fonte, agente = mondo["sql"](
        "SELECT status, source, assigned_user_id FROM appointments")[0]
    assert (stato, fonte, agente) == ("requested", "stima_inspections_backfill", None)
    # la riga si puo' ancora aggiornare (i CHECK si rivalutano)
    mondo["sql"]("UPDATE appointments SET notes = 'dopo la 073'")
    assert mondo["sql"]("SELECT notes FROM appointments")[0][0] == "dopo la 073"


# ---------------------------------------------------------------------------
# C - LA DOWN
# ---------------------------------------------------------------------------

def test_20_down_con_righe_facade_rifiuta_e_non_cambia_nulla(con_la_073):
    mondo = con_la_073
    _riga(mondo, status="scheduled")
    prima = _definizioni(mondo)
    with pytest.raises(Exception) as preso:
        _giu(mondo)
    assert "lmc15_facade appointment(s) would become unrepresentable" in str(preso.value)
    assert _definizioni(mondo) == prima
    assert mondo["sql"]("SELECT count(*) FROM schema_migrations "
                        "WHERE version = '073_a30_2p_lmc15_facade'")[0][0] == 1


def test_21_down_senza_righe_facade_riporta_esattamente_la_072(prima_della_073):
    mondo = prima_della_073
    del072 = _definizioni(mondo)
    assert set(del072) == {"appointments_source_chk", "appointments_agent_required_chk"}
    _su(mondo)
    con073 = _definizioni(mondo)
    assert set(con073) == set(CHECK)
    assert con073["appointments_source_chk"] != del072["appointments_source_chk"]
    assert con073["appointments_agent_required_chk"] != del072["appointments_agent_required_chk"]
    # una riga del backfill non blocca la down: solo lmc15_facade la blocca
    _giu(mondo)
    assert _definizioni(mondo) == del072          # identiche, come le scrive PostgreSQL
    assert mondo["sql"]("SELECT count(*) FROM schema_migrations "
                        "WHERE version = '073_a30_2p_lmc15_facade'")[0][0] == 0
    # e di nuovo su: la 073 si riapplica pulita
    _su(mondo)
    assert _definizioni(mondo) == con073


def test_22_dopo_la_down_la_facade_torna_irrappresentabile(prima_della_073):
    mondo = prima_della_073
    _su(mondo)
    _giu(mondo)
    _rifiutata(mondo, ("appointments_source_chk", "appointments_agent_required_chk"),
               status="scheduled")
    # anche con un agente: la fonte non esiste piu'
    _rifiutata(mondo, ("appointments_source_chk",), status="scheduled", agente=mondo["luca"])


# ---------------------------------------------------------------------------
# D - FAIL-CLOSED: una deriva di schema fa fallire up e down, senza lasciare
#     nulla a meta'
# ---------------------------------------------------------------------------

def test_30_up_fallisce_se_il_check_della_facade_esiste_gia(prima_della_073):
    import psycopg2
    mondo = prima_della_073
    mondo["sql"]("ALTER TABLE appointments ADD CONSTRAINT appointments_lmc15_facade_chk "
                 "CHECK (true)")                  # deriva simulata
    prima = _definizioni(mondo)
    with pytest.raises(psycopg2.errors.DuplicateObject):
        _su(mondo, forza=True)
    mondo["sql"]("ROLLBACK")
    assert _definizioni(mondo) == prima          # il DROP/ADD precedenti annullati
    assert mondo["sql"]("SELECT count(*) FROM schema_migrations "
                        "WHERE version = '073_a30_2p_lmc15_facade'")[0][0] == 0
    mondo["sql"]("ALTER TABLE appointments DROP CONSTRAINT appointments_lmc15_facade_chk")


def test_31_up_fallisce_se_manca_un_check_della_072(prima_della_073):
    import psycopg2
    mondo = prima_della_073
    definizione = _definizioni(mondo)["appointments_source_chk"]
    mondo["sql"]("ALTER TABLE appointments DROP CONSTRAINT appointments_source_chk")
    with pytest.raises(psycopg2.errors.UndefinedObject):
        _su(mondo, forza=True)
    mondo["sql"]("ROLLBACK")
    assert "appointments_lmc15_facade_chk" not in _definizioni(mondo)
    mondo["sql"](f"ALTER TABLE appointments ADD CONSTRAINT appointments_source_chk {definizione}")


def test_32_down_fallisce_e_annulla_tutto_se_lo_schema_non_e_quello_della_073(con_la_073):
    import psycopg2
    mondo = con_la_073
    mondo["sql"]("ALTER TABLE appointments DROP CONSTRAINT appointments_lmc15_facade_chk")
    prima = _definizioni(mondo)
    with pytest.raises(psycopg2.errors.UndefinedObject):
        _giu(mondo)
    assert _definizioni(mondo) == prima          # nessun CHECK toccato
    assert mondo["sql"]("SELECT count(*) FROM schema_migrations "
                        "WHERE version = '073_a30_2p_lmc15_facade'")[0][0] == 1
    # ripristino dello stato 073 per le prove successive
    mondo["sql"]("""ALTER TABLE appointments ADD CONSTRAINT appointments_lmc15_facade_chk CHECK (
        source <> 'lmc15_facade' OR (appointment_type = 'inspection' AND stima_id IS NOT NULL
        AND stima_inspection_id IS NOT NULL AND source_record_id IS NOT NULL))""")
