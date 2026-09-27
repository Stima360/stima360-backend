"""A30-10 - le fondamenta INBOUND, SENZA database.

D2 (sentinella statica): l'`auth_channel` interno `calendar_inbound` nasce
SOLO dalla factory server-side approvata (`operator_auth.calendar_inbound`) e
nessuna route/dependency HTTP lo puo' produrre. Il contratto `RemoteEvent`/
`get_event`, la whitelist delle proprieta' private e il provider finto sono
verificati qui; il comportamento su PostgreSQL vero e' in
`test_a30_10_calendar_inbound_postgres.py`.
"""
from __future__ import annotations

import ast
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "calendar_sync"
OPERATOR_AUTH = ROOT / "operator_auth"
FACTORY = OPERATOR_AUTH / "calendar_inbound.py"
CANALE = "calendar_inbound"


def _tutti_i_file_python_del_dominio():
    """Ogni file .py dell'applicazione, ESCLUSI test, migrazioni, venv e
    static: la superficie che potrebbe, per errore, costruire un
    OperatorContext con questo canale fuori dalla factory approvata."""
    esclusi = {"tests", "migrations", "static", "__pycache__", "venv", ".git"}
    for file in ROOT.rglob("*.py"):
        if any(parte in esclusi for parte in file.relative_to(ROOT).parts):
            continue
        yield file


# ---------------------------------------------------------------------------
# D2 - IL CANALE `calendar_inbound` NASCE SOLO DALLA FACTORY (sentinella)
# ---------------------------------------------------------------------------

#: L'assegnazione REALE che vale come "costruire il canale": sia la forma
#: letterale (`auth_channel="calendar_inbound"`) sia quella tramite la
#: costante che la factory usa davvero (`auth_channel=AUTH_CHANNEL_CALENDAR_INBOUND`).
_ASSEGNAZIONE_CANALE = re.compile(
    r'auth_channel\s*=\s*(AUTH_CHANNEL_CALENDAR_INBOUND|["\']' + CANALE + r'["\'])')


def test_d2_01_il_canale_calendar_inbound_e_scritto_solo_nella_factory():
    """Nessun altro file del dominio (route, dependency, service, worker)
    ASSEGNA `auth_channel` al valore/alla costante `calendar_inbound`: l'UNICA
    riga che lo fa e' dentro `operator_auth/calendar_inbound.py`."""
    trovati = []
    for file in _tutti_i_file_python_del_dominio():
        testo = file.read_text(encoding="utf-8")
        if _ASSEGNAZIONE_CANALE.search(testo):
            trovati.append(file)
    assert trovati == [FACTORY], (
        f"'{CANALE}' costruito fuori dalla factory approvata: {trovati}")


def test_d2_02_la_factory_costruisce_un_operator_context_reale():
    """La parola `SystemAgencyContext` PUO' apparire nel docstring (spiega
    perche' non si usa); un USO REALE - un import o una costruzione - non
    deve esserci mai."""
    codice = FACTORY.read_text(encoding="utf-8")
    assert "OperatorContext(" in codice
    assert not re.search(r"\bSystemAgencyContext\s*\(", codice)
    assert not re.search(r"^\s*from .*import.*SystemAgencyContext", codice, re.MULTILINE)
    assert "is_platform_admin=False" in codice
    assert "session_id=None" in codice


def test_d2_03_nessuna_route_o_dependency_http_importa_la_factory():
    """`require_operator`/`optional_session` (e qualunque altra dependency o
    router FastAPI) non devono cambiare comportamento: nessun file "router"
    o "dependencies" del dominio referenzia il canale inbound. Il worker
    inbound (`calendar_sync/inbound.py`, unico chiamante legittimo) e'
    l'eccezione esplicita, e la stessa factory."""
    worker = PACCHETTO / "inbound.py"
    assert worker.exists()
    ammessi = {FACTORY, worker}
    for file in _tutti_i_file_python_del_dominio():
        if file in ammessi:
            continue
        if "router" not in file.name and "dependencies" not in file.name:
            continue
        testo = file.read_text(encoding="utf-8")
        assert "calendar_inbound" not in testo, f"{file} referenzia il canale inbound"


def test_d2_04_calendar_inbound_e_registrato_ma_non_raggiungibile_da_http():
    """SENTINELLA AGGIORNATA DA A30-10B REVIEW FIX GATE (FIX 1): il modello
    autorevole ora REGISTRA `calendar_inbound` come terzo channel - il gate
    lo richiede esplicitamente, cosi' il set dei channel possibili resta
    onesto invece di nasconderne uno che esiste davvero.

    Registrarlo non lo rende raggiungibile da una richiesta: il dataclass
    non ha (e non deve avere) un `__post_init__` che chiuda l'insieme dei
    canali validi - nessuna validazione qui impedirebbe comunque a una route
    di costruirne uno. Cio' che rende `calendar_inbound` irraggiungibile via
    HTTP e' interamente strutturale, e lo provano `test_d2_01`
    (nessun altro file scrive quella assegnazione) e `test_d2_03` (nessuna
    route/dependency lo referenzia): questo test si limita a confermare che
    la registrazione non ha aggiunto un secondo modo di costruirlo."""
    from operator_auth import context

    assert context.AUTH_CHANNELS == ("operator_session", "legacy_basic", CANALE)
    assert not hasattr(context.OperatorContext, "__post_init__")


def test_d2_05_calendar_inbound_context_rilegge_i_tre_stati_dal_db(monkeypatch):
    """Offline (cursore finto): dimostra l'ORDINE di verifica e che nessuno
    dei tre controlli e' saltato, senza aprire una connessione vera (quella
    e' nel test PostgreSQL)."""
    from operator_auth.calendar_inbound import (
        CalendarInboundContextUnavailable,
        calendar_inbound_context,
    )

    class CursoreFinto:
        def __init__(self, risposte):
            self._risposte = list(risposte)
            self.query = []

        def execute(self, sql, parametri=None):
            self.query.append(sql)

        def fetchone(self):
            return self._risposte.pop(0)

    # operatore non attivo -> STOP prima di guardare agenzia/membership
    cur = CursoreFinto([{"status": "disabled"}])
    with pytest.raises(CalendarInboundContextUnavailable):
        calendar_inbound_context(cur, agency_id=1, user_id=1)
    assert len(cur.query) == 1

    # operatore attivo, agenzia suspended -> STOP prima della membership
    cur = CursoreFinto([{"status": "active"}, {"status": "suspended"}])
    with pytest.raises(CalendarInboundContextUnavailable):
        calendar_inbound_context(cur, agency_id=1, user_id=1)
    assert len(cur.query) == 2

    # operatore attivo, agenzia attiva, nessuna membership attiva -> STOP
    cur = CursoreFinto([{"status": "active"}, {"status": "active"}, None])
    with pytest.raises(CalendarInboundContextUnavailable):
        calendar_inbound_context(cur, agency_id=1, user_id=1)
    assert len(cur.query) == 3

    # tutti e tre validi -> un vero OperatorContext, col ruolo REALE letto
    cur = CursoreFinto([{"status": "active"}, {"status": "active"},
                        {"role": "agent"}])
    ctx = calendar_inbound_context(cur, agency_id=7, user_id=42)
    assert ctx.user_id == 42 and ctx.agency_id == 7 and ctx.role == "agent"
    assert ctx.is_platform_admin is False and ctx.session_id is None
    assert ctx.auth_channel == CANALE


# ---------------------------------------------------------------------------
# CONTRATTO get_event / RemoteEvent (A30-10)
# ---------------------------------------------------------------------------

def test_remote_event_whitelist_proprieta_private():
    from calendar_sync.provider import PRIVATE_PROPERTIES_WHITELIST

    assert PRIVATE_PROPERTIES_WHITELIST == frozenset({
        "stima360_chain_id", "stima360_appointment_id", "stima360_origin"})


def test_provider_protocol_dichiara_get_event():
    from calendar_sync.fake_provider import FakeCalendarProvider
    from calendar_sync.google_provider import GoogleCalendarProvider
    from calendar_sync.provider import CalendarProvider

    assert "get_event" in dir(CalendarProvider)
    assert callable(getattr(FakeCalendarProvider, "get_event", None))
    assert callable(getattr(GoogleCalendarProvider, "get_event", None))


def test_fake_provider_get_event_assente_e_zero_rete(monkeypatch):
    from calendar_sync.fake_provider import FakeCalendarProvider
    from calendar_sync.provider import ProviderAuth

    def niente_rete(*a, **kw):
        raise AssertionError("il provider finto ha aperto un socket")

    monkeypatch.setattr(socket, "socket", niente_rete)
    monkeypatch.setattr(socket, "create_connection", niente_rete)
    fake, auth = FakeCalendarProvider(), ProviderAuth(1, "t")
    assert fake.get_event(auth, "primary", "s360inesistente") is None


def test_fake_provider_get_event_riflette_ensure_e_simulazioni():
    from calendar_sync.fake_provider import FakeCalendarProvider
    from calendar_sync.provider import EventPayload, ProviderAuth

    fake, auth = FakeCalendarProvider(), ProviderAuth(1, "t")
    payload = EventPayload(
        event_id="s360test0000000000000000000000000000000000000000000001",
        summary="Sopralluogo", start_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 1, 10, tzinfo=timezone.utc), timezone="Europe/Rome",
        description="Stima360",
        private_properties={"stima360_chain_id": "11", "stima360_appointment_id": "11",
                            "stima360_origin": "stima360_crm"},
    )
    fake.ensure_event(auth, "primary", payload)
    letto = fake.get_event(auth, "primary", payload.event_id)
    assert letto.status == "confirmed"
    assert letto.start_at == payload.start_at and letto.end_at == payload.end_at
    assert letto.private_properties == payload.private_properties

    fake.simulate_remote_reschedule(1, "primary", payload.event_id,
                                    start_at=datetime(2026, 10, 1, 11, tzinfo=timezone.utc),
                                    end_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
    dopo = fake.get_event(auth, "primary", payload.event_id)
    assert dopo.start_at.hour == 11 and dopo.etag != letto.etag

    fake.simulate_remote_cancelled_tombstone(1, "primary", payload.event_id)
    tomba = fake.get_event(auth, "primary", payload.event_id)
    assert tomba.status == "cancelled"
    assert tomba.start_at is None and tomba.private_properties == {}

    fake.vanish(1, "primary", payload.event_id)
    assert fake.get_event(auth, "primary", payload.event_id) is None
