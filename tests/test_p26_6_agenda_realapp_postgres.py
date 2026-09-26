"""P26-6 x A30 - la sezione APPOINTMENTS della matrice ostile contro l'APP VERA.

Il prover offline (`test_p26_6_live_cert_script.py`) esercita la sezione su un
doppio che riproduce il contratto. Qui la stessa funzione,
`certify_appointments`, interroga `main.app` - mount, dipendenze di
autenticazione, router, service e SQL veri - su un PostgreSQL usa-e-getta
(opt-in: senza `P29_TEST_DSN` si salta tutto). Non si esegue sul TEST.

Solo la risoluzione del cookie e' sostituita (`session_from_token`: token ->
contesto dell'operatore), perche' lo schema minimo non porta le sessioni
operatore. Tutto il resto - `APIKeyCookie`, `optional_session`,
`require_authenticated_operator`, `require_operator` - e' quello di produzione.

Si prova che ogni sonda riceve DAVVERO il rifiuto atteso e che, a sezione
finita, `appointments` e `appointment_events` sono identiche byte per byte.
Nessuna connessione propria: il database e' quello del modulo A30-2.
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401 - fixture
    DSN, db, mondo)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare")

TOKEN = {"A": "p26-6-agenda-token-a", "B": "p26-6-agenda-token-b"}


class _AppVera:
    """Un `HttpProbe` che parla con `main.app` in-process. I "jar" sono i
    token di sessione: nessuno per l'anonimo."""

    def __init__(self, client):
        self.client = client
        self.scambi = []

    def request(self, method, path, *, jar=None, payload=None, headers=None):
        from scripts import p26_6_live_cert as cert

        cookies = {cert.COOKIE_NAME: jar} if jar else None
        self.client.cookies.clear()
        risposta = self.client.request(method, path, json=payload, headers=headers or {},
                                       cookies=cookies)
        self.scambi.append((method, path, jar, risposta.status_code))
        return cert.Response(risposta.status_code, dict(risposta.headers), risposta.content)


def _fotografia(conn):
    with conn.cursor() as cur:
        esito = {}
        for tabella in ("appointments", "appointment_events", "stima_inspections"):
            cur.execute(f"SELECT coalesce(json_agg(to_jsonb(x) ORDER BY x.id), '[]') "
                        f"FROM {tabella} x")
            esito[tabella] = cur.fetchone()[0]
    conn.commit()
    return esito


@pytest.fixture
def banco(mondo, monkeypatch):
    """Due agenzie con un'identita' `agency_admin` ciascuna (come la matrice),
    una stima per agenzia e - salvo richiesta - un appuntamento per agenzia."""
    import main
    from fastapi.testclient import TestClient
    from operator_auth import service as sessioni
    from operator_auth.context import OperatorContext

    conn = mondo["conn"]
    with conn.cursor() as cur:
        ids = {}
        for etichetta, agenzia in (("A", mondo["a"]), ("B", mondo["b"])):
            cur.execute("INSERT INTO operator_users (email, first_name, last_name) "
                        "VALUES (%s,'Cert','Test') RETURNING id",
                        (f"p26-6-cert-{etichetta.lower()}@certification.invalid",))
            ids[etichetta] = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role) "
                        "VALUES (%s,%s,'agency_admin')", (agenzia, ids[etichetta]))
        cur.execute("INSERT INTO stime (agency_id, comune, via, mq) VALUES "
                    "(%s,'Teramo','Via Roma',80) RETURNING id", (mondo["b"],))
        stima_b = cur.fetchone()[0]
    conn.commit()

    contesti = {TOKEN[e]: OperatorContext(
        user_id=ids[e], agency_id=mondo["a"] if e == "A" else mondo["b"],
        role="agency_admin", is_platform_admin=False, session_id=None,
        auth_channel="session") for e in ("A", "B")}
    scadenza = datetime.now(timezone.utc) + timedelta(hours=1)
    monkeypatch.setattr(sessioni, "session_from_token", lambda raw: (
        {"context": contesti[raw], "agency_name": "x", "expires_at": scadenza}
        if raw in contesti else None))

    http = _AppVera(TestClient(main.app, raise_server_exceptions=False))

    def crea(etichetta, **extra):
        import uuid
        corpo = {"appointment_type": "seller_meeting", "status": "requested",
                 "start_at": "2026-10-05T10:00:00+02:00",
                 "end_at": "2026-10-05T11:00:00+02:00",
                 "client_request_id": str(uuid.uuid4()), **extra}
        r = http.request("POST", "/api/appointments", jar=TOKEN[etichetta], payload=corpo)
        assert r.status == 201, r.text()
        return r.json()["id"]

    from core import database as core_database
    from psycopg2.extras import RealDictCursor
    from scripts import p26_6_live_cert as cert

    @contextmanager
    def cursori(*, commit=False):
        c = core_database.get_connection()      # quella del modulo A30-2
        try:
            cur = c.cursor(cursor_factory=RealDictCursor)
            yield c, cur
            c.rollback()                        # la sezione legge e basta
        finally:
            c.close()

    return {"http": http, "ids": ids, "crea": crea, "conn": conn,
            "database": cert.Database(cursori),
            "context": {"database": cert.Database(cursori),
                        "agencies": {"A": {"id": mondo["a"]}, "B": {"id": mondo["b"]}},
                        "operators": dict(ids),
                        "stime": {"A": mondo["stima"], "B": stima_b}}}


def _esegui(banco):
    from scripts import p26_6_live_cert as cert

    report = cert.Report(stream=io.StringIO())
    banco["http"].scambi.clear()            # solo le richieste della sezione
    dominio = next(d for d in cert.DOMAINS if d.name == "APPOINTMENTS")
    try:
        cert.certify_appointments(report, banco["http"], None, dominio,
                                  {"A": TOKEN["A"], "B": TOKEN["B"]}, {}, banco["context"])
    except cert.CheckFailed:
        pass
    return report


def test_01_ogni_sonda_riceve_il_rifiuto_vero_e_nulla_cambia(banco):
    from scripts import p26_6_live_cert as cert

    banco["crea"]("A", stima_id=None)
    banco["crea"]("B")
    prima = _fotografia(banco["conn"])
    report = _esegui(banco)
    righe = [(k, i, t) for k, i, t in report.rows]
    assert [r for r in righe if r[0] != cert.PASS] == [], righe
    assert len(righe) >= 60, len(righe)
    assert _fotografia(banco["conn"]) == prima
    # e nessuna scrittura dell'Agenda e' stata accettata dall'app vera
    riuscite = [s for s in banco["http"].scambi
                if s[0] in ("POST", "PATCH") and 200 <= s[3] < 300]
    assert riuscite == []


def test_02_senza_appuntamento_nell_altra_agenzia_resta_BLOCKED(banco):
    from scripts import p26_6_live_cert as cert

    banco["crea"]("A")
    prima = _fotografia(banco["conn"])
    report = _esegui(banco)
    esiti = {i: k for k, i, _t in report.rows}
    assert esiti["APPOINTMENTS-altrui-A-B"] == cert.BLOCKED
    assert esiti["APPOINTMENTS-dettaglio-B-A"] == cert.PASS
    assert cert.FAIL not in esiti.values()
    assert _fotografia(banco["conn"]) == prima


def test_03_anonimo_e_basic_si_fermano_al_mount(banco):
    """Le sonde senza sessione non raggiungono nemmeno lo scope: la prova e'
    che con il database vietato rispondono comunque 401."""
    from core import database as core_database
    from scripts import p26_6_live_cert as cert

    banco["crea"]("A")
    banco["crea"]("B")
    report = _esegui(banco)
    anonime = [i for k, i, _t in report.rows
               if i.startswith(("APPOINTMENTS-anonimo-", "APPOINTMENTS-basic-"))
               and k == cert.PASS]
    # Una sonda anonima per ogni operazione di AGENDA_OPERAZIONI piu' le tre
    # Basic fisse del certificatore. A30-5 (+ GET /lookups/stime): 17 + 3.
    assert len(anonime) == len(cert.AGENDA_OPERAZIONI) + 3 == 20

    vero = core_database.get_connection
    try:
        core_database.get_connection = lambda: (_ for _ in ()).throw(
            AssertionError("un anonimo ha raggiunto il database"))
        for metodo, suffisso in cert.AGENDA_OPERAZIONI:
            r = banco["http"].request(metodo, "/api/appointments" + suffisso.replace("{id}", "1"),
                                      payload={} if metodo in ("POST", "PATCH") else None)
            assert r.status == 401, (metodo, suffisso, r.status)
    finally:
        core_database.get_connection = vero


def test_04_uno_scope_rotto_nelle_scritture_e_visto_e_non_scrive(banco, monkeypatch):
    """Mutazione: il lock delle scritture dimentica l'agenzia. La sezione deve
    FALLIRE (niente 404) e - grazie alla `version` impossibile - la riga
    dell'altra agenzia resta comunque intatta: il rifiuto arriva prima della
    scrittura anche con lo scope rotto."""
    from appointments import repository
    from scripts import p26_6_live_cert as cert

    banco["crea"]("A")
    banco["crea"]("B")
    prima = _fotografia(banco["conn"])

    def lock_senza_agenzia(cur, agency_id, appointment_id):
        cur.execute("SELECT * FROM appointments WHERE id = %s FOR UPDATE", (appointment_id,))
        riga = cur.fetchone()
        return None if riga is None else dict(riga)

    monkeypatch.setattr(repository, "lock_appointment", lock_senza_agenzia)
    report = _esegui(banco)
    falliti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert falliti and falliti[0][0].startswith("APPOINTMENTS-write-"), falliti
    assert "409" in falliti[0][1]
    assert _fotografia(banco["conn"]) == prima
