"""A31-4 - il percorso legacy scoped owner/admin e' SPENTO, su PostgreSQL VERO.

Banco: quello di A31-3 (fixture `b`: mondo A30-2/A30-13B + 077/078 + dominio
BUY, router veri BUY/PROPERTY/Agenda montati con il contesto dell'operatore).

I corpi sono ESATTAMENTE quelli che la OS Shell manda dopo A31-4 (costruiti
dal dialog Agenda: `visitDecisionPayload` / `propertyVisitPayload`):

  BUY       {action, scheduled_at, assigned_user_id, client_request_id[, notes]}
  PROPERTY  {scheduled_at, status, assigned_user_id, client_request_id
             [, contact_id, lead_id]}

Cosa si prova (A31-4 §"TEST BACKEND OBBLIGATORI"):
  1-2  BUY owner/admin senza agente -> errore, nessuna scrittura
  3    BUY agent senza agente -> se stesso, appuntamento + proiezione
  4    BUY owner con agente valido -> appuntamento + proiezione
  5-6  PROPERTY futura scheduled/confirmed owner/admin senza agente -> errore
  7    PROPERTY futura con agente -> Agenda + proiezione
  8-9  PROPERTY storica (passata, completata) -> legacy invariato
  10   agente di un'altra agenzia -> sempre rifiutato
  +    stessa `client_request_id` (doppio invio / retry) -> UNA visita
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_2_appointments_postgres import db, http, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa, w  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31, v  # noqa: F401
from tests.test_a31_3_buyer_visits_facade_postgres import (  # noqa: F401
    _conta, _istantanea, b, schema_buy)

pytestmark = pytest.mark.skipif(
    not os.getenv("P29_TEST_DSN"),
    reason="P29_TEST_DSN non impostata: nessun PostgreSQL per A31-4")

ore, futuro = a30_2.ore, a30_2.futuro


def _decisione(b, chi, quando, **extra):
    corpo = {"action": "visit_scheduled", "scheduled_at": quando.isoformat(),
             "client_request_id": str(uuid.uuid4())}
    corpo.update(extra)
    return b["api"](chi).post(
        f"/api/buy/requests/{b['richiesta']}/matches/{b['match']}/decision", json=corpo)


def _visita(b, chi, quando, status="scheduled", **extra):
    corpo = {"scheduled_at": quando.isoformat(), "status": status,
             "client_request_id": str(uuid.uuid4())}
    corpo.update(extra)
    return b["api"](chi).post(f"/api/property/properties/{b['casa']}/visits", json=corpo)


def _reset_match(b):
    b["sql"]("UPDATE matches SET commercial_status='interested' WHERE id=%s", (b["match"],))


# ---------------------------------------------------------------------------
# BUY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chi", ["giorgio", "anna", "supremo"])
def test_01_02_buy_owner_admin_supreme_senza_agente_errore_zero_scritture(b, chi):
    prima = _istantanea(b)
    r = _decisione(b, chi, futuro(10))
    assert r.status_code == 400, r.text
    assert "agente" in r.json()["detail"]
    assert _istantanea(b) == prima
    assert _conta(b, "property_visits") == 0


def test_03_buy_agent_senza_agente_e_se_stesso(b):
    r = _decisione(b, "luca", futuro(10), notes="citofono B")
    assert r.status_code == 201, r.text
    a = b["sql"]("SELECT * FROM appointments")
    assert len(a) == 1 and a[0]["assigned_user_id"] == b["luca"]
    assert a[0]["appointment_type"] == "buyer_visit"
    assert a[0]["end_at"] - a[0]["start_at"] == timedelta(minutes=60)
    pv = b["sql"]("SELECT * FROM property_visits")
    assert len(pv) == 1 and pv[0]["appointment_id"] == a[0]["id"]
    assert r.json()["property_visit_id"] == pv[0]["id"] and r.json()["notes"] == "citofono B"


@pytest.mark.parametrize("chi", ["giorgio", "anna", "supremo"])
def test_04_buy_owner_admin_supreme_con_agente_valido(b, chi):
    r = _decisione(b, chi, futuro(10), assigned_user_id=b["marta"])
    assert r.status_code == 201, r.text
    a = b["sql"]("SELECT assigned_user_id, created_by_user_id FROM appointments")
    assert [tuple(x) for x in a] == [(b["marta"], b[chi])]
    assert _conta(b, "property_visits", "appointment_id IS NOT NULL") == 1
    assert _conta(b, "property_visits", "appointment_id IS NULL") == 0


def test_04b_buy_stessa_client_request_id_una_sola_visita(b):
    """Doppio invio / retry con la chiave del dialog: 1 appuntamento, 1
    proiezione, 1 interazione, 1 storico, 1 riga di sync Google."""
    corpo = {"action": "visit_scheduled", "scheduled_at": futuro(10).isoformat(),
             "assigned_user_id": b["marta"], "client_request_id": str(uuid.uuid4())}
    url = f"/api/buy/requests/{b['richiesta']}/matches/{b['match']}/decision"
    primo = b["api"]("giorgio").post(url, json=corpo)
    secondo = b["api"]("giorgio").post(url, json=corpo)
    assert primo.status_code == 201 and secondo.status_code == 201, secondo.text
    assert primo.json()["id"] == secondo.json()["id"]
    for tabella in ("appointments", "property_visits", "buy_request_interactions",
                    "buy_request_history"):
        assert _conta(b, tabella) == 1, tabella
    assert _conta(b, "appointment_calendar_sync") <= 1


# ---------------------------------------------------------------------------
# PROPERTY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chi,stato", [("giorgio", "scheduled"), ("anna", "confirmed"),
                                       ("supremo", "scheduled")])
def test_05_06_property_futura_aperta_senza_agente_errore_zero_righe(b, chi, stato):
    prima = _istantanea(b)
    r = _visita(b, chi, futuro(10), status=stato, contact_id=b["mario"])
    assert r.status_code == 400, r.text
    assert "agente" in r.json()["detail"]
    assert _istantanea(b) == prima
    assert _conta(b, "property_visits") == 0


def test_06b_property_futura_senza_fuso_senza_agente_errore(b):
    r = b["api"]("giorgio").post(f"/api/property/properties/{b['casa']}/visits",
                                 json={"scheduled_at": "2031-01-10T10:00:00",
                                       "status": "scheduled"})
    assert r.status_code == 400 and _conta(b, "property_visits") == 0


def test_07_property_futura_con_agente_agenda_e_proiezione(b):
    r = _visita(b, "giorgio", futuro(10), assigned_user_id=b["marta"],
                contact_id=b["mario"], lead_id=b["lead_mario"])
    assert r.status_code == 201, r.text
    a = b["sql"]("SELECT * FROM appointments")
    assert len(a) == 1
    assert (a[0]["appointment_type"], a[0]["assigned_user_id"], a[0]["property_id"],
            a[0]["contact_id"], a[0]["lead_id"]) == ("buyer_visit", b["marta"], b["casa"],
                                                     b["mario"], b["lead_mario"])
    assert r.json()["appointment_id"] == a[0]["id"]


def test_07b_property_stessa_client_request_id_una_sola_visita(b):
    corpo = {"scheduled_at": futuro(10).isoformat(), "status": "scheduled",
             "assigned_user_id": b["marta"], "client_request_id": str(uuid.uuid4())}
    url = f"/api/property/properties/{b['casa']}/visits"
    primo = b["api"]("giorgio").post(url, json=corpo)
    secondo = b["api"]("giorgio").post(url, json=corpo)
    assert primo.status_code == 201 and secondo.status_code == 201, secondo.text
    assert primo.json()["id"] == secondo.json()["id"]
    assert _conta(b, "appointments") == 1 and _conta(b, "property_visits") == 1


def test_07c_property_agent_senza_agente_e_se_stesso(b):
    r = _visita(b, "luca", futuro(10))
    assert r.status_code == 201, r.text
    a = b["sql"]("SELECT assigned_user_id FROM appointments")
    assert [x[0] for x in a] == [b["luca"]]


@pytest.mark.parametrize("chi", ["giorgio", "anna", "luca"])
def test_08_property_storica_passata_resta_legacy(b, chi):
    passato = datetime.now(timezone.utc) - timedelta(days=2)
    r = _visita(b, chi, passato, status="scheduled", contact_id=b["mario"])
    assert r.status_code == 201, r.text
    assert r.json()["appointment_id"] is None
    assert _conta(b, "appointments") == 0


@pytest.mark.parametrize("stato", ["completed", "cancelled", "no_show"])
def test_09_property_conclusa_resta_legacy(b, stato):
    r = _visita(b, "giorgio", ore(10), status=stato, contact_id=b["mario"])
    assert r.status_code == 201, r.text
    assert r.json()["appointment_id"] is None and r.json()["status"] == stato
    assert _conta(b, "appointments") == 0


# ---------------------------------------------------------------------------
# CROSS-AGENCY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chi", ["giorgio", "anna", "supremo"])
def test_10_agente_di_un_altra_agenzia_sempre_rifiutato(b, chi):
    prima = _istantanea(b)
    r = _decisione(b, chi, futuro(10), assigned_user_id=b["estraneo"])
    assert r.status_code in (400, 403, 404, 409), r.text
    r = _visita(b, chi, futuro(11), assigned_user_id=b["estraneo"])
    assert r.status_code in (400, 403, 404, 409), r.text
    assert _istantanea(b) == prima
