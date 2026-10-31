"""BLOCCO-5A - prenotazione di una visita sul percorso ORDINARIO completo.

Nessun trigger disattivato. Immobile e contatti inseriti normalmente; incarico
generato con le route reali dell'acquisizione:

    POST /api/property/properties/{id}/contacts  proprietario collegato
    POST /api/acquisitions                  acquisizione con appuntamento (Agenda)
    POST /api/appointments/{id}/complete    sopralluogo svolto (Agenda)
    POST /api/acquisitions/{id}/mandate     incarico (CRM-OPS-3)

L'unico ritocco diretto ai dati e' l'orario del sopralluogo, spostato nel
passato con un UPDATE (trigger attivi), come in `test_crm_ops_3_acquisitions_postgres`:
l'Agenda completa solo un appuntamento gia' iniziato. Poi: collegamento con
autorizzazione del proprietario, orari, prenotazione pubblica, agenda e scheda
immobile, annullamento e riprogrammazione, rifiuti dopo revoca del link,
scadenza dell'incarico e ritiro dell'autorizzazione (che nel modello attuale
coincide con la disattivazione del collegamento: l'autorizzazione e' registrata
sul link e non si modifica).
"""
from __future__ import annotations

import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_5a_visit_booking_postgres import _book, _slots, vb  # noqa: F401 - fixture

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

OPS = "/api/visit-booking-links"
_N = itertools.count(1)
PUB = "/api/public/visit-booking"


def _mandated_property(b, *, mandate_start=None, mandate_end=None):
    """Immobile e proprietario inseriti normalmente, incarico dalle route reali."""
    ids = b["ids"]
    owner = b["login"]("owner_a")
    pid = _q(b, "INSERT INTO properties (agency_id, code, title, property_type, commercial_status, city, address, "
                "asking_price) VALUES (%s, %s, 'Trilocale ordinario', 'apartment', 'evaluation', 'Comune Sintetico', "
                "'Via Ordinaria', 190000) RETURNING id", (ids["a"], f"ORD-{uuid.uuid4().int % 10**7}"))[0][0]
    seller = _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized) "
                   "VALUES (%s, 'Venditore Sintetico', 'Venditore', 'Sintetico', '3330009988', '393330009988') "
                   "RETURNING id", (ids["a"],))[0][0]
    r = owner.post(f"/api/property/properties/{pid}/contacts", json={"contact_id": seller, "role": "owner",
                                                                     "is_primary": True})
    assert r.status_code == 201, r.text
    n = next(_N)
    start = (datetime.now(timezone.utc) + timedelta(days=20 + n)).replace(minute=0, second=0, microsecond=0)
    r = owner.post("/api/acquisitions", json={
        "property_id": pid, "owner_contact_id": seller, "asking_price": "190000.00", "sale_timing": "within_3_months",
        "source": "referral", "appointment": {"start_at": start.isoformat(), "client_request_id": str(uuid.uuid4()),
                                              "assigned_user_id": ids["agent_a"]}})
    assert r.status_code == 201, r.text
    acq = r.json()
    past = datetime.now(timezone.utc) - timedelta(hours=30 + 3 * n)
    _q(b, "UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
       (past, past + timedelta(hours=1), acq["appointment_id"]))
    version = _q(b, "SELECT version FROM appointments WHERE id = %s", (acq["appointment_id"],))[0][0]
    r = owner.post(f"/api/appointments/{acq['appointment_id']}/complete", json={"version": version})
    assert r.status_code == 200, r.text
    acq = owner.get(f"/api/acquisitions/{acq['id']}").json()
    r = owner.post(f"/api/acquisitions/{acq['id']}/mandate", json={
        "version": acq["version"], "mandate_type": "Esclusiva",
        "mandate_start": (mandate_start or date.today() - timedelta(days=10)).isoformat(),
        "mandate_end": (mandate_end or date.today() + timedelta(days=170)).isoformat(), "agreed_price": "185000.00"})
    assert r.status_code == 200, r.text
    row = _q(b, "SELECT acquisition_id, commercial_status, mandate_type FROM properties WHERE id = %s", (pid,))[0]
    assert row == (acq["id"], "mandate", "Esclusiva")                   # incarico vero, nato dall'acquisizione
    return pid


def _link(b, pid, owner="preauthorized"):
    r = b["login"]("owner_a").post(OPS, json={
        "property_id": pid, "assigned_user_id": b["ids"]["agent_a"], "duration_minutes": 45, "step_minutes": 30,
        "travel_minutes": 15, "min_notice_minutes": 120, "horizon_days": 14, "owner_authorization": owner,
        "owner_authorization_note": "Assenso scritto del proprietario del 31/10 (sintetico)"})
    assert r.status_code == 201, r.text
    return r.json()


def test_full_ordinary_path_book_see_cancel_reschedule(vb):
    ids = vb["ids"]
    assert _q(vb, "SELECT current_setting('session_replication_role')") == [("origin",)]
    pid = _mandated_property(vb)
    link = _link(vb, pid)
    assert link["owner_authorization"] == "preauthorized" and link["owner_authorization_recorded_by"] == ids["owner_a"]
    token = link["url"].rsplit("/", 1)[1]
    slots = _slots(vb, token)
    out = _book(vb, token, slots[0]["start_at"]).json()
    assert out["state"] == "confirmed"
    aid = _q(vb, "SELECT id FROM appointments WHERE property_id = %s AND appointment_type = 'buyer_visit' "
                 "AND start_at = %s", (pid, slots[0]["start_at"]))[0][0]
    owner = vb["login"]("owner_a")
    # stessa visita in Agenda (dettaglio e calendario) e nella scheda immobile
    detail = owner.get(f"/api/appointments/{aid}").json()
    assert (detail.get("appointment") or detail)["property_id"] == pid
    start = datetime.fromisoformat(slots[0]["start_at"])
    cal = owner.get("/api/appointments/calendar", params={"from": (start - timedelta(hours=1)).isoformat(),
                                                          "to": (start + timedelta(hours=2)).isoformat()})
    assert cal.status_code == 200 and str(aid) in cal.text
    scheda = owner.get(f"/api/property/properties/{pid}").json()
    assert any(v.get("appointment_id") == aid for v in scheda["visits"])
    # riprogrammazione e annullamento con il collegamento di gestione
    key = out["manage_url"].rsplit("/", 1)[1]
    target = next(s["start_at"] for s in slots if s["start_at"] != slots[0]["start_at"])
    moved = vb["public"].post(f"{PUB}/manage/{key}/reschedule", json={"start_at": target, "key": str(uuid.uuid4())})
    assert moved.status_code == 200, moved.text
    new_aid = _q(vb, "SELECT id FROM appointments WHERE property_id = %s AND start_at = %s", (pid, target))[0][0]
    assert _q(vb, "SELECT status FROM appointments WHERE id = %s", (aid,)) == [("cancelled",)]
    assert _q(vb, "SELECT rescheduled_from_id, status FROM appointments WHERE id = %s", (new_aid,)) == [(aid, "scheduled")]
    assert any(v.get("appointment_id") == new_aid for v in owner.get(f"/api/property/properties/{pid}").json()["visits"])
    new_key = moved.json()["manage_url"].rsplit("/", 1)[1]
    assert vb["public"].post(f"{PUB}/manage/{new_key}/cancel").json()["state"] == "cancelled"
    assert _q(vb, "SELECT status FROM appointments WHERE id = %s", (new_aid,)) == [("cancelled",)]


def test_a_manage_key_acts_only_on_its_own_booking(vb):
    pid = _mandated_property(vb)
    token = _link(vb, pid)["url"].rsplit("/", 1)[1]
    slots = _slots(vb, token)
    a = _book(vb, token, slots[0]["start_at"], name="Persona A", phone="3330000101").json()
    b = _book(vb, token, slots[3]["start_at"], name="Persona B", phone="3330000202").json()
    key_a, key_b = a["manage_url"].rsplit("/", 1)[1], b["manage_url"].rsplit("/", 1)[1]
    assert vb["public"].get(f"{PUB}/manage/{key_a}").json()["start_at"] == a["start_at"]
    assert vb["public"].post(f"{PUB}/manage/{key_a}/cancel").json()["state"] == "cancelled"
    assert vb["public"].get(f"{PUB}/manage/{key_b}").json()["state"] == "confirmed"      # B intatta
    statuses = dict(_q(vb, "SELECT start_at, status FROM appointments WHERE property_id = %s", (pid,)))
    assert statuses[datetime.fromisoformat(a["start_at"])] == "cancelled"
    assert statuses[datetime.fromisoformat(b["start_at"])] == "scheduled"
    # la chiave di A non sposta B, e nessuna chiave inventata agisce
    assert vb["public"].post(f"{PUB}/manage/{key_a}/reschedule",
                             json={"start_at": slots[5]["start_at"], "key": str(uuid.uuid4())}).status_code == 400
    assert vb["public"].get(f"{PUB}/manage/{key_b}").json()["start_at"] == b["start_at"]
    assert vb["public"].post(f"{PUB}/manage/{uuid.uuid4()}/cancel").status_code == 404


@pytest.mark.parametrize("case", ["link_revoked", "mandate_expired", "owner_authorization_withdrawn"])
def test_booking_is_refused_after_revocation_expiry_or_withdrawal(vb, case):
    pid = _mandated_property(vb, mandate_start=date.today() - timedelta(days=20))
    link = _link(vb, pid)
    token = link["url"].rsplit("/", 1)[1]
    slot = _slots(vb, token)[0]["start_at"]
    owner = vb["login"]("owner_a")
    if case in ("link_revoked", "owner_authorization_withdrawn"):
        assert owner.post(f"{OPS}/{link['id']}/disable").status_code == 200
    else:
        version = owner.get(f"/api/property/properties/{pid}").json().get("version")
        body = {"mandate_end": (date.today() - timedelta(days=1)).isoformat()}
        if version is not None:
            body["version"] = version
        r = owner.patch(f"/api/property/properties/{pid}", json=body)
        assert r.status_code == 200, r.text
        assert _q(vb, "SELECT mandate_end FROM properties WHERE id = %s", (pid,)) == [(date.today() - timedelta(days=1),)]
    assert vb["public"].get(f"{PUB}/{token}").status_code == 404
    assert vb["public"].get(f"{PUB}/{token}/slots", params={
        "date_from": datetime.now(timezone.utc).isoformat(),
        "date_to": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}).status_code == 404
    assert _book(vb, token, slot).status_code == 404
    assert _q(vb, "SELECT count(*) FROM appointments WHERE property_id = %s AND appointment_type = 'buyer_visit'",
              (pid,)) == [(0,)]
    if case == "mandate_expired":                  # e nessun nuovo collegamento su un incarico scaduto
        r = owner.post(OPS, json={"property_id": pid, "assigned_user_id": vb["ids"]["agent_a"],
                                  "owner_authorization": "per_visit"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "MANDATE_REQUIRED"
