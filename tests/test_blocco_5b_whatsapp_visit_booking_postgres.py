"""BLOCCO-5B - il motore 4D propone il collegamento di prenotazione (SOLO SIMULAZIONE).

Stesso banco di 2C/4A/4D/5A su PostgreSQL VERO. Una richiesta di visita riceve
il collegamento solo se l'immobile del messaggio e' identificato e la
prenotazione e' davvero abilitata; il testo non conferma mai un appuntamento;
nessun invio, nessuna accodamento, nessuna visita creata.
"""
from __future__ import annotations

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_4a_whatsapp_inbound_postgres import wa  # noqa: F401 - fixture
from tests.test_blocco_4d_auto_replies_postgres import _decision, _incoming, _ledger_counts, _link, _tick, ar  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _link as _booking_link
from tests.test_blocco_5a_visit_booking_postgres import _property

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

PLACEHOLDER = "[collegamento di prenotazione della visita]"


@pytest.fixture
def vb5(ar, monkeypatch):
    ids = ar["ids"]
    monkeypatch.setenv("PUBLIC_BOOKING_IP_PEPPER", "pepe-sintetico-di-prova")
    _q(ar, "DELETE FROM agent_working_hours WHERE user_id = %s", (ids["agent_a"],))
    for day in range(1, 8):
        _q(ar, "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, start_minute, end_minute) "
               "VALUES (%s, %s, %s, 480, 1200)", (ids["a"], ids["agent_a"], day))
    return ar


def _visit(b, pid, text="Buongiorno, posso venire a vederlo?"):
    mid, sender, contact = _incoming(b, text)
    _link(b, mid, contact, pid)
    return mid


def test_a_visit_request_on_a_bookable_property_gets_the_link_and_no_confirmation(vb5):
    pid = _property(vb5)
    link = _booking_link(vb5, pid).json()
    mid = _visit(vb5, pid)
    before = _ledger_counts(vb5)
    appointments_before = _q(vb5, "SELECT count(*) FROM appointments")[0][0]
    _tick(vb5)
    decision, reason, text, version, mode, data, handoff = _decision(vb5, mid)
    assert (decision, reason, version, mode, handoff) == ("answer", "visit_booking_link", "5b-2", "simulate", None)
    assert PLACEHOLDER in text and "fissato solo quando la pagina conferma" in text
    assert "confermat" not in text.replace("conferma la prenotazione", "")
    assert "/visita/" not in text and data["booking_link_id"] == link["id"]
    assert data["owner_approval_required"] is False
    assert _ledger_counts(vb5) == before                                           # nessun invio ne' accodamento
    assert _q(vb5, "SELECT count(*) FROM appointments")[0][0] == appointments_before   # nessuna visita creata
    # il token non e' mai nel registro
    assert _q(vb5, "SELECT count(*) FROM whatsapp_auto_reply_decisions WHERE proposed_text LIKE '%/visita/%'") == [(0,)]


def test_owner_consent_per_visit_is_stated(vb5):
    pid = _property(vb5)
    _booking_link(vb5, pid, owner="per_visit")
    mid = _visit(vb5, pid)
    _tick(vb5)
    _d, _r, text, _v, _m, data, _h = _decision(vb5, mid)
    assert "assenso del proprietario" in text and data["owner_approval_required"] is True


@pytest.mark.parametrize("case", ["no_link", "disabled", "closed_mandate", "no_property"])
def test_without_a_bookable_link_the_visit_goes_to_the_agent(vb5, case):
    pid = _property(vb5)
    if case != "no_link":
        link = _booking_link(vb5, pid).json()
        if case == "disabled":
            assert vb5["login"]("owner_a").post(f"/api/visit-booking-links/{link['id']}/disable").status_code == 200
        if case == "closed_mandate":
            _q(vb5, "SET session_replication_role = replica; UPDATE properties SET commercial_status = 'withdrawn' "
                    "WHERE id = %s", (pid,))
    if case == "no_property":
        mid, _s, _c = _incoming(vb5, "Posso visitarlo domani?")
    else:
        mid = _visit(vb5, pid)
    _tick(vb5)
    decision, reason, text, *_ = _decision(vb5, mid)
    assert (decision, reason) == ("handoff", "visit") and PLACEHOLDER not in text
    assert "nessun appuntamento è fissato" in text


@pytest.mark.parametrize("status", ["mandate", "reserved", "under_offer"])
def test_the_link_is_offered_only_for_an_active_property(vb5, status):
    """Incarico in corso e collegamento attivo, ma l'immobile non e' «Attivo»:
    nessun collegamento automatico, la richiesta va all'agente."""
    pid = _property(vb5)
    _booking_link(vb5, pid).json()
    _q(vb5, "SET session_replication_role = replica; UPDATE properties SET commercial_status = %s WHERE id = %s",
       (status, pid))
    from visit_booking import service as visit_booking
    from core.database import core_cursor
    with core_cursor() as (_conn, cur):
        assert visit_booking.booking_link_for_visit(cur, vb5["ids"]["a"], pid) is not None   # prenotabile
    mid = _visit(vb5, pid)
    _tick(vb5)
    decision, reason, text, _v, _m, data, handoff = _decision(vb5, mid)
    assert (decision, reason) == ("handoff", "visit") and PLACEHOLDER not in text
    assert "booking_link_id" not in data and handoff["task"] == "planned"
