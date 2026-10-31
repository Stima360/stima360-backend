"""BLOCCO-5A - prenotazione online delle visite su PostgreSQL VERO.

Stesso banco di BLOCCO-2C (cluster LOCALE usa-e-getta, migrazioni dal runner,
compresa la 120), app vera e login vero. Gate: `P29_TEST_DSN` locale e
`BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`. Dati solo sintetici.

    L  collegamenti: incarico, autorizzazione del proprietario, agente, permessi, agenzie
    O  orari prenotabili: orario dell'agente, preavviso, passo, spostamento, cambio d'ora
    B  prenotazione: confermata / richiesta, agenda e immobile, idempotenza, rifiuti
    C  due persone sullo stesso orario: una sola conferma
    G  gestione: annullamento e riprogrammazione con il collegamento protetto
    P  pagina pubblica: nessun dato riservato
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

ROME = ZoneInfo("Europe/Rome")
OPS = "/api/visit-booking-links"
PUB = "/api/public/visit-booking"
SECRETS = ("Via Segreta", "Civico-S77", "99999", "NOTA-RISERVATA", "IMM-")


@pytest.fixture
def vb(app, monkeypatch):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setenv("PUBLIC_BOOKING_IP_PEPPER", "pepe-sintetico-di-prova")
    ids = app["ids"]
    for who, agency in (("agent_a", "a"), ("owner_a", "a"), ("agent2_a", "a")):
        if who == "agent2_a":
            continue                                           # agent2 resta SENZA orario
        _q(app, "DELETE FROM agent_working_hours WHERE user_id = %s", (ids[who],))
        for day in range(1, 8):
            _q(app, "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, start_minute, end_minute) "
                    "VALUES (%s, %s, %s, 480, 1200)", (ids[agency], ids[who], day))
    public = TestClient(main.app, base_url="https://testserver")
    return {**app, "public": public}


def _property(b, agency="a", *, mandate=True, end=None, status="active"):
    code = f"IMM-{uuid.uuid4().int % 10**7}"
    cols = "agency_id, code, title, property_type, commercial_status, city, address, civic_number, minimum_price, " \
           "internal_notes"
    vals = [b["ids"][agency], code, "Trilocale Via Segreta", "apartment", status, "Comune Sintetico", "Via Segreta",
            "Civico-S77", 99999, "NOTA-RISERVATA"]
    if mandate:
        cols += ", mandate_type, mandate_start, mandate_end"
        vals += ["Esclusiva", date.today() - timedelta(days=30), end]
    marks = ", ".join(["%s"] * len(vals))
    # incarico «storico»: il trigger CRM-OPS-3 lo vieta fuori da un'acquisizione,
    # quindi la riga di prova si inserisce senza trigger (solo nel banco locale)
    return _q(b, f"SET session_replication_role = replica; INSERT INTO properties ({cols}) VALUES ({marks}) "
                 f"RETURNING id", vals)[0][0]


def _link(b, pid, *, who="owner_a", agent="agent_a", owner="preauthorized", **extra):
    body = {"property_id": pid, "assigned_user_id": b["ids"][agent], "duration_minutes": 45, "step_minutes": 30,
            "travel_minutes": 15, "min_notice_minutes": 120, "horizon_days": 14, "owner_authorization": owner,
            "owner_authorization_note": "Assenso scritto del proprietario (sintetico)", **extra}
    return b["login"](who).post(OPS, json=body)


def _token(b, pid, **kw):
    r = _link(b, pid, **kw)
    assert r.status_code == 201, r.text
    return r.json()["url"].rsplit("/", 1)[1]


def _slots(b, token, days_ahead=1):
    start = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    r = b["public"].get(f"{PUB}/{token}/slots", params={"date_from": start.isoformat(),
                                                        "date_to": (start + timedelta(days=2)).isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["slots"]


def _book(b, token, start_at, key=None, name="Visitatore Sintetico", phone="3330001122"):
    return b["public"].post(f"{PUB}/{token}/book", json={"name": name, "phone": phone, "email": None,
                                                        "start_at": start_at, "key": key or str(uuid.uuid4()),
                                                        "privacy_accepted": True})


def _appointment(b, start_at, pid):
    return _q(b, "SELECT id, appointment_type, status, property_id, contact_id, assigned_user_id, source, "
                 "buffer_before_minutes, buffer_after_minutes, rescheduled_from_id FROM appointments "
                 "WHERE property_id = %s AND start_at = %s ORDER BY id", (pid, start_at))


# ---------------------------------------------------------------------------
# L - collegamenti
# ---------------------------------------------------------------------------

def test_l_a_link_needs_an_open_mandate_owner_authorization_and_agent_hours(vb):
    ids = vb["ids"]
    no_mandate = _property(vb, mandate=False)
    r = _link(vb, no_mandate)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "MANDATE_REQUIRED"
    expired = _property(vb, end=date.today() - timedelta(days=1))
    assert _link(vb, expired).json()["detail"]["code"] == "MANDATE_REQUIRED"
    pid = _property(vb)
    body = {"property_id": pid, "assigned_user_id": ids["agent_a"]}
    assert vb["login"]("owner_a").post(OPS, json=body).status_code == 422          # autorizzazione obbligatoria
    assert _link(vb, pid, owner="tacita").status_code == 422
    assert _link(vb, pid, agent="agent2_a").json()["detail"]["code"] == "NO_WORKING_HOURS"
    assert _link(vb, pid, who="agent_a", agent="owner_a").status_code == 403       # agente: solo la sua agenda
    r = _link(vb, pid)
    assert r.status_code == 201 and r.json()["url"].startswith("/visita/")
    link = r.json()
    assert link["owner_authorization_recorded_by"] == ids["owner_a"] and "token_hash" not in link
    listed = vb["login"]("owner_a").get(OPS, params={"property_id": pid}).json()["items"]
    assert [x["id"] for x in listed] == [link["id"]] and "url" not in listed[0]


def test_l_agencies_are_separated(vb):
    pid = _property(vb)
    link = _link(vb, pid).json()
    owner_b = vb["login"]("owner_b")
    assert owner_b.get(OPS, params={"property_id": pid}).status_code == 404
    assert owner_b.post(f"{OPS}/{link['id']}/disable").status_code == 404
    assert _link(vb, pid, who="owner_b", agent="owner_b").status_code in (403, 404)


def test_l_only_agents_with_working_hours_are_offered(vb):
    """La scelta dell'agente mostra solo chi il server accetta: membri attivi
    con orario settimanale; un agente vede solo se stesso; altra agenzia 404."""
    ids = vb["ids"]
    _q(vb, "DELETE FROM agent_working_hours WHERE user_id = ANY(%s)", ([ids["agent2_a"], ids["admin_a"]],))
    pid = _property(vb)
    r = vb["login"]("owner_a").get(f"{OPS}/agents", params={"property_id": pid})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert sorted(a["id"] for a in items) == sorted([ids["agent_a"], ids["owner_a"]])
    assert [a["is_me"] for a in items if a["id"] == ids["owner_a"]] == [True]
    assert set(items[0]) == {"id", "name", "is_me"}
    for a in items:                                    # ognuno e' davvero assegnabile
        assert _link(vb, pid, agent=next(k for k in ("agent_a", "owner_a") if ids[k] == a["id"])).status_code == 201
    other = _property(vb)                              # immobile non suo: l'agente non lo gestisce
    assert vb["login"]("agent_a").get(f"{OPS}/agents", params={"property_id": other}).status_code == 403
    _q(vb, "SET session_replication_role = replica; UPDATE properties SET assigned_agent_id = %s WHERE id = %s",
       (ids["agent_a"], pid))
    mine = vb["login"]("agent_a").get(f"{OPS}/agents", params={"property_id": pid}).json()["items"]
    assert [a["id"] for a in mine] == [ids["agent_a"]]
    _q(vb, "UPDATE agency_memberships SET status = 'suspended' WHERE operator_user_id = %s", (ids["agent_a"],))
    try:
        left = vb["login"]("owner_a").get(f"{OPS}/agents", params={"property_id": pid}).json()["items"]
        assert [a["id"] for a in left] == [ids["owner_a"]]
    finally:
        _q(vb, "UPDATE agency_memberships SET status = 'active' WHERE operator_user_id = %s", (ids["agent_a"],))
    assert vb["login"]("owner_b").get(f"{OPS}/agents", params={"property_id": pid}).status_code == 404
    assert vb["login"]("owner_a").get(f"{OPS}/agents").status_code == 422


# ---------------------------------------------------------------------------
# O - orari prenotabili
# ---------------------------------------------------------------------------

def test_o_only_bookable_times_inside_hours_after_notice_on_the_grid(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    slots = _slots(vb, token)
    assert slots
    now = datetime.now(timezone.utc)
    for s in slots:
        start = datetime.fromisoformat(s["start_at"]).astimezone(ROME)
        end = datetime.fromisoformat(s["end_at"]).astimezone(ROME)
        assert start >= now + timedelta(minutes=120)
        assert (start.hour, start.minute) >= (8, 0) and (end.hour * 60 + end.minute) <= 20 * 60 or end.date() > start.date()
        assert start.minute in (0, 30) and (end - start) == timedelta(minutes=45)
        assert set(s) == {"start_at", "end_at"}                        # nient'altro esce


def test_o_an_existing_appointment_and_travel_time_remove_slots(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    first = _slots(vb, token)[3]["start_at"]
    assert _book(vb, token, first).status_code == 200
    start = datetime.fromisoformat(first)
    after = {datetime.fromisoformat(s["start_at"]) for s in _slots(vb, token)}
    # 45' di visita + 15' di spostamento prima e dopo: nessun inizio entro 60' prima / 60' dopo
    assert not any(start - timedelta(minutes=60) < s < start + timedelta(minutes=60) for s in after)


def test_o_daylight_saving_change_keeps_local_hours(vb):
    """Domenica 28 marzo 2027 (ora legale): gli orari restano 08:00-20:00 locali."""
    from appointments import property_visit_writes as writes
    from core.database import core_cursor

    pid = _property(vb)
    link_id = _link(vb, pid).json()["id"]
    with core_cursor() as (_, cur):
        cur.execute("SELECT * FROM property_visit_booking_links WHERE id = %s", (link_id,))
        link = dict(cur.fetchone())
        link["horizon_days"] = 60
        now = datetime(2027, 3, 26, 12, 0, tzinfo=timezone.utc)
        before = writes.bookable_slots(cur, link, datetime(2027, 3, 27, 0, 0, tzinfo=ROME),
                                       datetime(2027, 3, 28, 0, 0, tzinfo=ROME), now=now)
        after = writes.bookable_slots(cur, link, datetime(2027, 3, 28, 0, 0, tzinfo=ROME),
                                      datetime(2027, 3, 29, 0, 0, tzinfo=ROME), now=now)
    assert before[0]["start_at"].astimezone(ROME).hour == 8 and before[0]["start_at"].utcoffset() is not None
    assert before[0]["start_at"].astimezone(timezone.utc).hour == 7                 # CET, +1
    assert after[0]["start_at"].astimezone(ROME).hour == 8
    assert after[0]["start_at"].astimezone(timezone.utc).hour == 6                  # CEST, +2
    assert after[-1]["end_at"].astimezone(ROME).hour * 60 + after[-1]["end_at"].astimezone(ROME).minute <= 1200


# ---------------------------------------------------------------------------
# B - prenotazione
# ---------------------------------------------------------------------------

def test_b_preauthorized_booking_is_confirmed_in_agenda_and_on_the_property(vb):
    ids = vb["ids"]
    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[0]["start_at"]
    r = _book(vb, token, start)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["state"] == "confirmed" and out["manage_url"].startswith("/visita-gestione/")
    ((aid, kind, status, prop, contact, agent, source, before, after, _),) = _appointment(vb, start, pid)
    assert (kind, status, prop, agent, source, before, after) == ("buyer_visit", "scheduled", pid, ids["agent_a"],
                                                                  "booking_link", 15, 15)
    assert contact is not None
    assert _q(vb, "SELECT count(*) FROM property_visits WHERE appointment_id = %s", (aid,)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM property_visit_bookings WHERE appointment_id = %s", (aid,)) == [(1,)]
    agenda = vb["login"]("owner_a").get(f"/api/appointments/{aid}")
    assert agenda.status_code == 200
    detail = agenda.json()
    assert (detail.get("appointment") or detail)["property_id"] == pid


def test_b_owner_consent_per_visit_is_never_confirmed(vb):
    pid = _property(vb)
    token = _token(vb, pid, owner="per_visit")
    info = vb["public"].get(f"{PUB}/{token}").json()
    assert info["owner_approval_required"] is True
    start = _slots(vb, token)[0]["start_at"]
    out = _book(vb, token, start).json()
    assert out["state"] == "pending_owner" and "NON è ancora confermata" in out["message"]
    ((_aid, _k, status, *_rest),) = _appointment(vb, start, pid)
    assert status == "requested"
    # la richiesta occupa comunque l'orario: un secondo prenotante non lo vede e non lo prende
    assert start not in [s["start_at"] for s in _slots(vb, token)]
    r = _book(vb, token, start)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "SLOT_UNAVAILABLE"


def test_b_same_submission_twice_is_one_booking(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[1]["start_at"]
    key = str(uuid.uuid4())
    first, second = _book(vb, token, start, key=key), _book(vb, token, start, key=key)
    assert first.status_code == second.status_code == 200
    assert first.json()["manage_url"] == second.json()["manage_url"]
    assert len(_appointment(vb, start, pid)) == 1


def test_b_server_refuses_times_not_offered_and_naive_times(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    start = datetime.fromisoformat(_slots(vb, token)[0]["start_at"])
    for bad in (start + timedelta(minutes=10),                                   # fuori dal passo
                datetime.now(timezone.utc) + timedelta(minutes=30),              # dentro il preavviso
                start.astimezone(ROME).replace(hour=21, minute=0)):              # fuori orario
        r = _book(vb, token, bad.isoformat())
        assert r.status_code == 400 and r.json()["detail"]["code"] == "SLOT_UNAVAILABLE", bad
    assert _book(vb, token, start.replace(tzinfo=None).isoformat()).status_code == 422
    r = vb["public"].post(f"{PUB}/{token}/book", json={"name": "X", "phone": "333", "start_at": start.isoformat(),
                                                       "key": str(uuid.uuid4()), "privacy_accepted": True,
                                                       "property_id": 1})
    assert r.status_code == 422                                                  # corpo chiuso
    assert _book(vb, token, start.isoformat(), phone="").status_code in (400, 422)


def test_b_disabled_link_or_closed_mandate_is_a_neutral_refusal(vb):
    pid = _property(vb)
    link = _link(vb, pid).json()
    token = link["url"].rsplit("/", 1)[1]
    assert vb["public"].get(f"{PUB}/{token}").status_code == 200
    assert vb["login"]("owner_a").post(f"{OPS}/{link['id']}/disable").status_code == 200
    a = vb["public"].get(f"{PUB}/{token}")
    b = vb["public"].get(f"{PUB}/{'x' * 43}")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    pid2 = _property(vb)
    token2 = _token(vb, pid2)
    _q(vb, "SET session_replication_role = replica; UPDATE properties SET commercial_status = 'sold' WHERE id = %s",
       (pid2,))
    assert vb["public"].get(f"{PUB}/{token2}").status_code == 404


# ---------------------------------------------------------------------------
# C - concorrenza
# ---------------------------------------------------------------------------

def test_c_two_people_book_the_same_time_only_one_is_confirmed(vb):
    from fastapi.testclient import TestClient

    import main

    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[2]["start_at"]
    clients = [TestClient(main.app, base_url="https://testserver") for _ in range(2)]
    barrier = threading.Barrier(2)
    results = []

    def go(c, n):
        barrier.wait(5)
        results.append(c.post(f"{PUB}/{token}/book", json={
            "name": f"Persona {n}", "phone": f"33300000{n}{n}", "email": None, "start_at": start,
            "key": str(uuid.uuid4()), "privacy_accepted": True}))

    threads = [threading.Thread(target=go, args=(c, i)) for i, c in enumerate(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status_code for r in results) == [200, 400]
    assert len([a for a in _appointment(vb, start, pid) if a[2] == "scheduled"]) == 1


# ---------------------------------------------------------------------------
# G - gestione
# ---------------------------------------------------------------------------

def test_g_cancel_and_reschedule_with_the_protected_link(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    slots = _slots(vb, token)
    out = _book(vb, token, slots[0]["start_at"]).json()
    key = out["manage_url"].rsplit("/", 1)[1]
    state = vb["public"].get(f"{PUB}/manage/{key}").json()
    assert state["can_cancel"] and state["can_reschedule"] and "manage_url" not in state
    new_slots = vb["public"].get(f"{PUB}/manage/{key}/slots", params={
        "date_from": slots[0]["start_at"], "date_to": (datetime.fromisoformat(slots[0]["start_at"])
                                                       + timedelta(days=1)).isoformat()}).json()["slots"]
    target = next(s["start_at"] for s in new_slots if s["start_at"] != slots[0]["start_at"])
    moved = vb["public"].post(f"{PUB}/manage/{key}/reschedule", json={"start_at": target, "key": str(uuid.uuid4())})
    assert moved.status_code == 200, moved.text
    assert moved.json()["state"] == "confirmed"
    old = _appointment(vb, slots[0]["start_at"], pid)
    new = _appointment(vb, target, pid)
    assert old[0][2] == "cancelled" and new[0][2] == "scheduled" and new[0][9] == old[0][0]
    assert vb["public"].get(f"{PUB}/manage/{key}").json()["state"] == "replaced"
    new_key = moved.json()["manage_url"].rsplit("/", 1)[1]
    assert vb["public"].post(f"{PUB}/manage/{new_key}/cancel").json()["state"] == "cancelled"
    assert _appointment(vb, target, pid)[0][2] == "cancelled"
    assert vb["public"].post(f"{PUB}/manage/{new_key}/cancel").status_code == 400
    assert vb["public"].get(f"{PUB}/manage/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------------------
# P - pagina pubblica
# ---------------------------------------------------------------------------

def test_p_public_responses_expose_nothing_reserved(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    info = vb["public"].get(f"{PUB}/{token}")
    raw = info.text + vb["public"].get(f"{PUB}/{token}/slots", params={
        "date_from": datetime.now(timezone.utc).isoformat(),
        "date_to": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}).text
    out = _book(vb, token, _slots(vb, token)[0]["start_at"])
    raw += out.text + vb["public"].get(f"{PUB}/manage/{out.json()['manage_url'].rsplit('/', 1)[1]}").text
    for casuale in (token, out.json()["manage_url"]):  # token e chiave casuali: non sono dati
        raw = raw.replace(casuale, "")
    for secret in SECRETS:
        assert secret not in raw, secret
    assert info.json()["property"] == {"type_label": "Appartamento", "city": "Comune Sintetico"} or \
        info.json()["property"]["city"] == "Comune Sintetico"
    page = vb["public"].get(f"/visita/{token}")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer" and token not in page.text
    assert vb["public"].get(f"/visita-gestione/{uuid.uuid4()}").status_code == 200
