"""A30-8 - esito dell'appuntamento e follow-up, su PostgreSQL vero, via HTTP.

Opt-in: senza `P29_TEST_DSN` si salta tutto. MAI PROD: il database e' quello
usa-e-getta di A30-2 (`test_a30_2_appointments_postgres.db`), a cui questo
modulo aggiunge la tabella CORE `tasks` presa dalle migration VERE (001: la
tabella; 028: agenzia e autore; 030: NOT NULL e trigger; 033: la funzione
`core_agency_integrity` attuale). Nessuna connessione nuova nasce qui (P26
H11): il service passa da `core.database.get_connection()`, i controlli dal
`sql` del fixture `mondo`.

Il router dell'Agenda e' montato su un'applicazione di prova con
`require_operator` sostituito dalla stessa `OperatorContext` della produzione.
"""
from __future__ import annotations

import re
import threading
from datetime import timedelta

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401 - fixture
    DSN, MIGRAZIONI, chiave, db, futuro, mondo, ore, proiezione_accesa)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-8")


# ---------------------------------------------------------------------------
# banco: `tasks` dalle migration vere
# ---------------------------------------------------------------------------

def _estrai(file, schema):
    testo = (MIGRAZIONI / file).read_text(encoding="utf-8")
    trovato = re.search(schema, testo, re.S)
    assert trovato, (file, schema)
    return trovato.group(0)


def _ddl_tasks() -> str:
    return "\n".join((
        _estrai("001_core_contacts_leads.sql", r"CREATE TABLE IF NOT EXISTS tasks \(.*?\n\);"),
        _estrai("028_p26_core_agency_columns.sql", r"ALTER TABLE tasks\n.*?;"),
        _estrai("030_p26_core_agency_enforce.sql",
                r"ALTER TABLE tasks\s+ALTER COLUMN agency_id SET NOT NULL;"),
        _estrai("033_p26_stima_agency_enforce.sql",
                r"CREATE OR REPLACE FUNCTION core_agency_integrity\(\).*?\$fn\$ LANGUAGE plpgsql;"),
        _estrai("030_p26_core_agency_enforce.sql",
                r"DO \$do\$\nBEGIN\n    IF NOT EXISTS \((?:(?!\$do\$;).)*?"
                r"trg_tasks_agency_integrity.*?\$do\$;"),
        # la colonna che lo scope agent di CORE legge (028): serve a provare
        # che il percorso generico agent-scoped NON e' quello del follow-up
        "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS assigned_agent_id BIGINT;",
        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS assigned_agent_id BIGINT;",
    ))


@pytest.fixture(scope="module")
def db_task(db):
    with db["conn"].cursor() as cur:
        cur.execute(_ddl_tasks())
    return db


@pytest.fixture
def senza_task(db_task):
    """Prima di `mondo`, che svuota contatti e stime: i task li referenziano."""
    with db_task["conn"].cursor() as cur:
        cur.execute("DELETE FROM tasks")
    return db_task


@pytest.fixture
def w(senza_task, mondo):
    return mondo


@pytest.fixture
def http(w):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    stato = {"ctx": w["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi, **kw):
        stato["ctx"] = w["ctx"](chi, **kw)
        return client

    return come


def _crea(http, chi="giorgio", **kw):
    corpo = {"appointment_type": "seller_meeting", "assigned_user_id": None,
             "start_at": ore(10).isoformat(), "end_at": ore(11).isoformat(),
             "client_request_id": chiave()}
    corpo.update(kw)
    r = http(chi).post("/api/appointments", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _passato(http, w, **kw):
    """GIORNO 10-11: nel passato del database, completabile e 'assente'."""
    base = {"assigned_user_id": w["luca"], "contact_id": w["mario"], "lead_id": w["lead_mario"]}
    base.update(kw)
    return _crea(http, **base)


def _fu(giorni=3, **kw):
    corpo = {"due_at": (futuro(9) + timedelta(days=giorni)).isoformat()}
    corpo.update(kw)
    return corpo


def _post(http, a, azione, chi="giorgio", **corpo):
    percorso = {"no_show": "no-show"}.get(azione, azione)
    corpo.setdefault("version", a["version"])
    return http(chi).post(f"/api/appointments/{a['id']}/{percorso}", json=corpo)


def _task(w):
    return [dict(r) for r in w["sql"]("SELECT * FROM tasks ORDER BY id")]


def _eventi(w, appointment_id):
    return [dict(r) for r in w["sql"](
        "SELECT event_type, from_status, to_status, actor_user_id, changes "
        "FROM appointment_events WHERE appointment_id=%s ORDER BY id", (appointment_id,))]


def _riga(w, appointment_id):
    return dict(w["sql"]("SELECT * FROM appointments WHERE id=%s", (appointment_id,))[0])


def _fotografia(w):
    return {t: w["sql"](f"SELECT count(*), md5(coalesce(string_agg(x::text, '|' "
                        f"ORDER BY x::text), '')) FROM {t} x")[0][:]
            for t in ("appointments", "appointment_events", "tasks", "stima_inspections",
                      "seller_timeline_events", "contacts", "leads", "stime")}


# ---------------------------------------------------------------------------
# A - ESITI (1-8, 11, 14-16)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("confermato", [False, True])
def test_01_02_completed_da_scheduled_e_confirmed(http, w, confermato):
    a = _passato(http, w)
    if confermato:
        r = _post(http, a, "confirm")
        assert r.status_code == 200, r.text
        a = r.json()
    r = _post(http, a, "complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed" and r.json()["id"] == a["id"]
    ultimo = _eventi(w, a["id"])[-1]
    assert (ultimo["event_type"], ultimo["to_status"], ultimo["actor_user_id"]) == (
        "status_changed", "completed", w["giorgio"])
    assert "outcome_note" not in ultimo["changes"]                       # 16
    assert _task(w) == []                                                # 20: facoltativo


def test_03_complete_troppo_presto_422_con_available_from(http, w):
    a = _crea(http, assigned_user_id=w["luca"], start_at=futuro(15).isoformat(),
              end_at=futuro(16).isoformat())
    prima = _fotografia(w)
    r = _post(http, a, "complete", follow_up=_fu())
    assert r.status_code == 422 and r.json()["code"] == "COMPLETE_TOO_EARLY"
    assert r.json()["available_from"].startswith(futuro(15).date().isoformat())
    assert _fotografia(w) == prima


@pytest.mark.parametrize("confermato", [False, True])
def test_04_05_no_show_da_scheduled_e_confirmed(http, w, confermato):
    a = _passato(http, w)
    if confermato:
        a = _post(http, a, "confirm").json()
    r = _post(http, a, "no_show", outcome_note="  Citofono muto  ")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "no_show"
    ultimo = _eventi(w, a["id"])[-1]
    assert ultimo["changes"]["outcome_note"] == "Citofono muto"          # 15
    assert ultimo["changes"]["azione"] == "no_show"
    assert _riga(w, a["id"])["notes"] is None                            # mai in notes


def test_06_no_show_troppo_presto_422(http, w):
    db_now = w["sql"]("SELECT NOW()")[0][0]
    a = _crea(http, assigned_user_id=w["luca"],
              start_at=(db_now - timedelta(minutes=30)).isoformat(),
              end_at=(db_now + timedelta(minutes=30)).isoformat())
    r = _post(http, a, "no_show")
    assert r.status_code == 422 and r.json()["code"] == "NO_SHOW_TOO_EARLY"
    assert "available_from" in r.json()
    assert _riga(w, a["id"])["status"] == "scheduled"


def test_07_08_cancel_anche_di_un_appuntamento_passato_ancora_aperto(http, w):
    a = _passato(http, w)                                      # GIORNO 10-11: passato
    r = _post(http, a, "cancel", reason="  Cliente irraggiungibile ")
    assert r.status_code == 200, r.text
    riga = _riga(w, a["id"])
    assert riga["status"] == "cancelled" and riga["cancelled_reason"] == "Cliente irraggiungibile"
    ultimo = _eventi(w, a["id"])[-1]
    assert ultimo["changes"]["cancelled_reason"]["a"] == "Cliente irraggiungibile"
    assert "outcome_note" not in ultimo["changes"]


def test_07b_cancel_non_accetta_outcome_note(http, w):
    a = _passato(http, w)
    r = _post(http, a, "cancel", outcome_note="doppione")
    assert r.status_code == 422
    assert _riga(w, a["id"])["status"] == "scheduled"


def test_11_terminali_immutabili_restano_409_invalid_transition(http, w):
    a = _passato(http, w)
    fatto = _post(http, a, "complete").json()
    for azione, corpo in (("complete", {}), ("no_show", {}), ("cancel", {"reason": "x"}),
                          ("confirm", {}),
                          ("reschedule", {"start_at": futuro(9).isoformat(),
                                          "end_at": futuro(10).isoformat()})):
        r = _post(http, fatto, azione, **corpo)
        assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION", azione
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": fatto["version"], "notes": "dopo"})
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"


# ---------------------------------------------------------------------------
# B - RESCHEDULE (9-10)
# ---------------------------------------------------------------------------

def test_09_reschedule_futuro_nuova_riga(http, w):
    a = _passato(http, w)
    r = _post(http, a, "reschedule", start_at=futuro(9).isoformat(), end_at=futuro(10).isoformat())
    assert r.status_code == 201, r.text
    assert r.json()["rescheduled_from_id"] == a["id"]
    assert _riga(w, a["id"])["status"] == "rescheduled"


def test_10_reschedule_nel_passato_422_nessuna_scrittura(http, w):
    a = _passato(http, w)
    prima = _fotografia(w)
    r = _post(http, a, "reschedule", start_at=ore(9).isoformat(), end_at=ore(10).isoformat())
    assert r.status_code == 422 and r.json()["code"] == "RESCHEDULE_IN_PAST"
    assert _fotografia(w) == prima


def test_10b_reschedule_non_accetta_follow_up(http, w):
    a = _passato(http, w)
    r = _post(http, a, "reschedule", start_at=futuro(9).isoformat(),
              end_at=futuro(10).isoformat(), follow_up=_fu())
    assert r.status_code == 422
    assert _riga(w, a["id"])["status"] == "scheduled" and _task(w) == []


# ---------------------------------------------------------------------------
# C - VERSION E CONCORRENZA (12, 13, 26)
# ---------------------------------------------------------------------------

def test_12_version_vecchia_409_nessun_task_nessun_evento(http, w):
    a = _passato(http, w)
    a2 = _post(http, a, "confirm").json()
    eventi = len(_eventi(w, a["id"]))
    r = _post(http, a, "complete", version=a["version"], follow_up=_fu())
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    assert r.json()["current_version"] == a2["version"]
    assert _task(w) == [] and len(_eventi(w, a["id"])) == eventi


def test_26_retry_dello_stesso_esito_nessun_secondo_task_ne_evento(http, w):
    a = _passato(http, w)
    corpo = {"version": a["version"], "outcome_note": "ok", "follow_up": _fu()}
    primo = http("giorgio").post(f"/api/appointments/{a['id']}/complete", json=corpo)
    assert primo.status_code == 200, primo.text
    secondo = http("giorgio").post(f"/api/appointments/{a['id']}/complete", json=corpo)
    assert secondo.status_code == 409 and secondo.json()["code"] == "VERSION_CONFLICT"
    corpo["version"] = primo.json()["version"]
    terzo = http("giorgio").post(f"/api/appointments/{a['id']}/complete", json=corpo)
    assert terzo.status_code == 409 and terzo.json()["code"] == "INVALID_TRANSITION"
    assert len(_task(w)) == 1
    terminali = [e for e in _eventi(w, a["id"]) if e["to_status"] == "completed"]
    assert len(terminali) == 1


def _gara(http, w, a, prima, seconda):
    """Due richieste su DUE connessioni, con la riga tenuta bloccata da una
    terza finche' entrambe non sono in attesa: poi si mettono in fila."""
    # P26 H11: nessuna connessione nuova nel test; `mondo` punta
    # `get_connection()` al database usa-e-getta.
    from core import database as core_database
    blocco = core_database.get_connection()
    esiti = {}
    try:
        with blocco.cursor() as cur:
            cur.execute("SELECT id FROM appointments WHERE id=%s FOR UPDATE", (a["id"],))

            def invia(nome, azione, corpo):
                r = _post(http, a, azione, **corpo)
                esiti[nome] = (r.status_code, r.json().get("code"), r.json())

            fili = [threading.Thread(target=invia, args=(n, az, c))
                    for n, (az, c) in (("A", prima), ("B", seconda))]
            for f in fili:
                f.start()
            attesa = 0
            while attesa < 100:          # entrambe in coda sul lock della riga
                n = w["sql"]("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = "
                             "'Lock' AND datname = current_database()")[0][0]
                if n >= 2:
                    break
                threading.Event().wait(0.05)
                attesa += 1
            assert attesa < 100, "le due richieste non sono arrivate al lock"
        blocco.commit()
        for f in fili:
            f.join(timeout=30)
    finally:
        blocco.close()
    return esiti


@pytest.mark.parametrize("seconda", [
    ("no_show", {"follow_up": {}}),
    ("cancel", {"reason": "annullato", "follow_up": {}}),
])
def test_13_due_esiti_concorrenti_uno_solo_vince(http, w, proiezione_accesa, seconda):
    a = _passato(http, w, appointment_type="inspection", stima_id=w["stima"])
    assert a["stima_inspection_id"] is not None
    fu = _fu()
    prima = ("complete", {"follow_up": fu})
    seconda = (seconda[0], {**seconda[1], "follow_up": fu})
    esiti = _gara(http, w, a, prima, seconda)
    codici = sorted(e[0] for e in esiti.values())
    assert codici == [200, 409], esiti
    perdente = next(e for e in esiti.values() if e[0] == 409)
    assert perdente[1] in ("VERSION_CONFLICT", "INVALID_TRANSITION")
    riga = _riga(w, a["id"])
    assert riga["status"] in ("completed", "no_show", "cancelled")
    terminali = [e for e in _eventi(w, a["id"]) if e["event_type"] == "status_changed"
                 and e["to_status"] in ("completed", "no_show", "cancelled")]
    assert len(terminali) == 1
    task = _task(w)
    assert len(task) == 1 and task[0]["metadata"]["outcome"] == riga["status"]
    atteso = "completed" if riga["status"] == "completed" else "cancelled"
    assert w["sql"]("SELECT status FROM stima_inspections WHERE id=%s",
                    (a["stima_inspection_id"],))[0][0] == atteso
    assert w["sql"]("SELECT count(*) FROM stima_inspections")[0][0] == 1


# ---------------------------------------------------------------------------
# D - FOLLOW-UP (17-25, 27-31)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("azione,corpo", [
    ("complete", {"outcome_note": "Interessato"}),
    ("no_show", {}),
    ("cancel", {"reason": "Rinviato dal cliente"}),
])
def test_17_18_19_follow_up_per_ogni_esito(http, w, azione, corpo):
    a = _passato(http, w)
    fu = _fu(title="  Richiamare  ", note=" Portare planimetria ")
    r = _post(http, a, azione, follow_up=fu, **corpo)
    assert r.status_code == 200, r.text
    (task,) = _task(w)
    esito = {"complete": "completed", "no_show": "no_show", "cancel": "cancelled"}[azione]
    assert task["task_type"] == "appointment_followup"
    assert task["title"] == "Richiamare" and task["description"] == "Portare planimetria"
    assert task["status"] == "open" and task["priority"] == "normal"
    assert task["metadata"] == {"appointment_id": a["id"], "outcome": esito}
    # 23 riferimenti dall'appuntamento; 28 stessa agenzia; D3 autore reale
    assert (task["contact_id"], task["lead_id"], task["stima_id"]) == (
        w["mario"], w["lead_mario"], None)
    assert task["agency_id"] == w["a"]
    assert task["created_by_user_id"] == w["giorgio"]
    # 25 assigned_to = nome dell'agente, solo informativo
    assert task["assigned_to"] == "Luca Test"
    assert task["due_at"] == (futuro(9) + timedelta(days=3))
    ultimo = _eventi(w, a["id"])[-1]
    assert ultimo["changes"]["follow_up_task_id"] == task["id"]
    assert ultimo["actor_user_id"] == w["giorgio"]


def test_17b_titolo_di_default_neutro_e_assigned_to_vuoto_senza_agente(http, w):
    a = _crea(http, status="requested", contact_id=w["mario"])
    r = _post(http, a, "cancel", follow_up=_fu())
    assert r.status_code == 200, r.text
    (task,) = _task(w)
    assert task["title"] == "Follow-up appuntamento"
    assert task["assigned_to"] is None and task["description"] is None


def test_21_scadenza_passata_422_nessuna_scrittura(http, w, proiezione_accesa):
    a = _passato(http, w, appointment_type="inspection", stima_id=w["stima"])
    prima = _fotografia(w)
    db_now = w["sql"]("SELECT NOW()")[0][0]
    for scadenza in (db_now - timedelta(minutes=1), ore(9)):
        r = _post(http, a, "complete", follow_up={"due_at": scadenza.isoformat()})
        assert r.status_code == 422 and r.json()["code"] == "FOLLOW_UP_IN_PAST"
    r = _post(http, a, "complete", follow_up={"due_at": "2030-01-01T10:00:00"})
    assert r.status_code == 422 and r.json()["code"] == "TIMEZONE_REQUIRED"
    r = _post(http, a, "complete", follow_up={"title": "x"})
    assert r.status_code == 422                                   # due_at obbligatorio
    assert _fotografia(w) == prima


def test_22_senza_collegamenti_422_ma_l_esito_senza_follow_up_funziona(http, w):
    a = _crea(http, assigned_user_id=w["luca"])                 # nessun contatto/lead/stima
    r = _post(http, a, "complete", follow_up=_fu())
    assert r.status_code == 422 and r.json()["code"] == "FOLLOW_UP_REQUIRES_LINK"
    assert _riga(w, a["id"])["status"] == "scheduled" and _task(w) == []
    r = _post(http, a, "complete")
    assert r.status_code == 200 and r.json()["status"] == "completed"


def test_22b_solo_la_stima_basta(http, w):
    a = _crea(http, assigned_user_id=w["luca"], stima_id=w["stima"])
    r = _post(http, a, "no_show", follow_up=_fu())
    assert r.status_code == 200, r.text
    (task,) = _task(w)
    assert (task["contact_id"], task["lead_id"], task["stima_id"]) == (None, None, w["stima"])
    assert task["agency_id"] == w["a"]


@pytest.mark.parametrize("campo,valore", [
    ("contact_id", "bruno"), ("lead_id", "lead_mario"), ("stima_id", "stima"),
    ("agency_id", "b"), ("assigned_to", None), ("created_by_user_id", "marta"),
    ("priority", None), ("task_type", None), ("metadata", None),
])
def test_24_il_browser_non_puo_mandare_riferimenti_nel_follow_up(http, w, campo, valore):
    a = _passato(http, w)
    fu = _fu()
    fu[campo] = w[valore] if isinstance(valore, str) else "x"
    r = _post(http, a, "complete", follow_up=fu)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert _riga(w, a["id"])["status"] == "scheduled" and _task(w) == []


def test_27_rollback_totale_se_il_task_fallisce(http, w, proiezione_accesa, monkeypatch):
    from appointments import service
    from core.exceptions import ValidationError
    a = _passato(http, w, appointment_type="inspection", stima_id=w["stima"])
    prima = _fotografia(w)
    vero = service.create_task_with_cursor

    def rotto(cur, data, **kw):
        vero(cur, data, **kw)                     # il task viene SCRITTO...
        raise ValidationError("task rifiutato")   # ...e poi la creazione fallisce

    monkeypatch.setattr(service, "create_task_with_cursor", rotto)
    r = _post(http, a, "complete", outcome_note="n", follow_up=_fu())
    assert r.status_code == 422
    # nessun completed senza task, nessun task senza esito, nessuna proiezione
    assert _fotografia(w) == prima
    assert _riga(w, a["id"])["version"] == a["version"]


def test_29_altro_tenant_404_nessuna_scrittura(http, w):
    a = _passato(http, w)
    prima = _fotografia(w)
    r = _post(http, a, "complete", chi="estraneo", follow_up=_fu())
    assert r.status_code == 404
    assert _fotografia(w) == prima


def test_30_agent_follow_up_sul_proprio_appuntamento_anche_senza_assegnazione_crm(http, w):
    """D5: il contatto e il lead NON sono assegnati a Luca (assigned_agent_id
    NULL), quindi lo scope generico di CORE per un agent li rifiuterebbe; il
    follow-up nasce lo stesso, perche' i riferimenti vengono dall'appuntamento
    che Luca puo' modificare."""
    from core import repository as core_repository
    from core.exceptions import NotFoundError
    from core.database import core_cursor
    assert w["sql"]("SELECT assigned_agent_id FROM contacts WHERE id=%s",
                    (w["mario"],))[0][0] is None
    # il percorso GENERICO agent-scoped rifiuta: e' la regola che resta intatta
    with pytest.raises(NotFoundError):
        with core_cursor(commit=False) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, {"contact_id": w["mario"], "lead_id": None, "stima_id": None,
                      "title": "x", "description": None, "task_type": None,
                      "priority": "normal", "status": "open", "due_at": None,
                      "completed_at": None, "assigned_to": None, "created_by": None,
                      "metadata": {}}, ctx=w["ctx"]("luca"))
    a = _passato(http, w)
    r = _post(http, a, "complete", chi="luca", follow_up=_fu())
    assert r.status_code == 200, r.text
    (task,) = _task(w)
    assert task["created_by_user_id"] == w["luca"] and task["contact_id"] == w["mario"]
    assert _eventi(w, a["id"])[-1]["actor_user_id"] == w["luca"]


def test_30b_agent_non_puo_chiudere_l_appuntamento_di_un_collega(http, w):
    a = _passato(http, w)                                        # di Luca
    prima = _fotografia(w)
    r = _post(http, a, "complete", chi="marta", follow_up=_fu())
    assert r.status_code == 404
    assert _fotografia(w) == prima


def test_31_nessun_cambio_di_lead_contatto_stima(http, w, proiezione_accesa):
    a = _passato(http, w, appointment_type="inspection", stima_id=w["stima"])
    crm = {t: w["sql"](f"SELECT md5(string_agg(x::text, '|' ORDER BY x::text)) FROM {t} x")[0][0]
           for t in ("contacts", "leads", "stime")}
    r = _post(http, a, "complete", outcome_note="Incarico probabile", follow_up=_fu())
    assert r.status_code == 200, r.text
    assert crm == {t: w["sql"](f"SELECT md5(string_agg(x::text, '|' ORDER BY x::text)) "
                               f"FROM {t} x")[0][0] for t in crm}


# ---------------------------------------------------------------------------
# E - PROIEZIONE (32-34) e disponibilita'
# ---------------------------------------------------------------------------

def test_32_completed_proietta_come_prima_una_sola_inspection(http, w, proiezione_accesa):
    a = _passato(http, w, appointment_type="inspection", stima_id=w["stima"])
    r = _post(http, a, "complete", follow_up=_fu())
    assert r.status_code == 200, r.text
    assert w["sql"]("SELECT status FROM stima_inspections")[0][0] == "completed"
    assert w["sql"]("SELECT count(*) FROM stima_inspections")[0][0] == 1
    assert [t[0] for t in w["sql"]("SELECT event_type FROM seller_timeline_events ORDER BY id")] \
        == ["inspection_scheduled", "inspection_completed"]


def test_33_34_no_show_proiezione_invariata(http, w, proiezione_accesa):
    """D8: no_show -> stima_inspections cancelled con motivo 'no_show' e
    `inspection_cancelled`: la journey (che si ferma solo su scheduled o
    completed) non viene fermata. Semantica invariata."""
    a = _passato(http, w, appointment_type="inspection", stima_id=w["stima"])
    r = _post(http, a, "no_show", follow_up=_fu())
    assert r.status_code == 200, r.text
    stato, motivo = w["sql"]("SELECT status, cancelled_reason FROM stima_inspections")[0]
    assert (stato, motivo) == ("cancelled", "no_show")
    assert [t[0] for t in w["sql"]("SELECT event_type FROM seller_timeline_events ORDER BY id")] \
        == ["inspection_scheduled", "inspection_cancelled"]


def test_35_disponibilita_dopo_gli_stati_terminali(http, w):
    """Completed e no_show continuano a occupare (072); cancelled libera."""
    for ora, azione, corpo, libero in ((13, "complete", {}, False), (15, "no_show", {}, False),
                                       (17, "cancel", {"reason": "x"}, True)):
        a = _passato(http, w, start_at=ore(ora).isoformat(), end_at=ore(ora + 1).isoformat())
        assert _post(http, a, azione, **corpo).status_code == 200
        r = http("giorgio").post("/api/appointments/availability/check", json={
            "assigned_user_id": w["luca"], "start_at": ore(ora).isoformat(),
            "end_at": ore(ora + 1).isoformat()})
        assert r.status_code == 200 and r.json()["available"] is libero, azione


# ---------------------------------------------------------------------------
# F - IL NUOVO CONTRATTO CORE su PostgreSQL (percorso R-4 + autore esplicito)
# ---------------------------------------------------------------------------

def _dati(**kw):
    base = {"contact_id": None, "lead_id": None, "stima_id": None, "title": "t",
            "description": None, "task_type": None, "priority": "normal", "status": "open",
            "due_at": None, "completed_at": None, "assigned_to": None, "created_by": None,
            "metadata": {}}
    base.update(kw)
    return base


def test_40_core_autore_esplicito_agenzia_derivata_dal_trigger(w):
    from core import repository as core_repository
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        t = core_repository.create_task_with_cursor(
            cur, _dati(contact_id=w["mario"]), created_by_user_id=w["luca"])
    assert (t["agency_id"], t["created_by_user_id"]) == (w["a"], w["luca"])


def test_41_core_autore_di_un_altra_agenzia_rifiutato_e_rollback(w):
    from core import repository as core_repository
    from core.database import core_cursor
    from core.exceptions import ValidationError
    with pytest.raises(ValidationError):
        with core_cursor(commit=True) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, _dati(contact_id=w["mario"]), created_by_user_id=w["estraneo"])
    assert _task(w) == []


def test_42_core_platform_admin_senza_membership_ammesso_inesistente_no(w):
    from core import repository as core_repository
    from core.database import core_cursor
    from core.exceptions import ValidationError
    admin = w["sql"]("INSERT INTO operator_users (email, is_platform_admin) "
                     "VALUES ('pa@example.it', TRUE) RETURNING id")[0][0]
    with core_cursor(commit=True) as (_, cur):
        t = core_repository.create_task_with_cursor(
            cur, _dati(lead_id=w["lead_mario"]), created_by_user_id=admin)
    assert t["created_by_user_id"] == admin
    # un operatore inesistente: lo ferma gia' la FK del database (028)
    import psycopg2
    with pytest.raises(psycopg2.Error):
        with core_cursor(commit=True) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, _dati(lead_id=w["lead_mario"]), created_by_user_id=10 ** 9)
    # un operatore esistente ma sospeso nella membership: rifiutato dal controllo
    w["sql"]("UPDATE agency_memberships SET status = 'suspended' WHERE operator_user_id = %s",
             (w["marta"],))
    with pytest.raises(ValidationError):
        with core_cursor(commit=True) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, _dati(lead_id=w["lead_mario"]), created_by_user_id=w["marta"])
    assert len(_task(w)) == 1
    w["sql"]("DELETE FROM tasks")
    w["sql"]("DELETE FROM operator_users WHERE id=%s", (admin,))


def test_43_core_riferimenti_incoerenti_restano_rifiutati_dal_trigger(w):
    import psycopg2

    from core import repository as core_repository
    from core.database import core_cursor
    with pytest.raises(psycopg2.Error):
        with core_cursor(commit=True) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, _dati(contact_id=w["contatto_b"], lead_id=w["lead_mario"]),
                created_by_user_id=w["giorgio"])
    assert _task(w) == []


@pytest.mark.parametrize("dati,kw", [
    ({"agency_id": 1}, {"created_by_user_id": 1}),
    ({"created_by_user_id": 1}, {"created_by_user_id": 1}),
    ({}, {"created_by_user_id": True}),
    ({}, {"created_by_user_id": "1"}),
])
def test_44_core_nessun_bypass_ne_agenzia_dal_chiamante(w, dati, kw):
    from core import repository as core_repository
    from core.database import core_cursor
    from core.scope import ProgrammingError
    with pytest.raises(ProgrammingError):
        with core_cursor(commit=True) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, _dati(contact_id=w["mario"], **dati), **kw)
    assert _task(w) == []


def test_45_core_ctx_e_autore_esplicito_non_si_combinano(w):
    from core import repository as core_repository
    from core.database import core_cursor
    from core.scope import ProgrammingError
    with pytest.raises(ProgrammingError):
        with core_cursor(commit=True) as (_, cur):
            core_repository.create_task_with_cursor(
                cur, _dati(contact_id=w["mario"]), ctx=w["ctx"]("giorgio"),
                created_by_user_id=w["giorgio"])
