"""P17-B1/P17-B2 integration tests: Seller Intelligence wired into the real
POST /api/salva_stima endpoint. P17-B1 added ``stima_richiesta``; P17-B2
(below) adds ``stima_completata`` and ``email_stima_inviata`` without
touching the P17-B1 tests' intent - only their row-count assertions, which
now must account for the two new sibling events a successful request
produces (see the updated ``install_pdf_email_whatsapp_mocks`` default of
``mail_result=True``, needed so `mail_sent` in main.py is a real bool).

These tests exercise the REAL endpoint (``main.salva_stima``), not a
reimplementation - same technique already used by
tests/test_public_stima_core_crm_bridge.py for the CORE bridge integration:
LegacyConnection/LegacyCursor stand in for the raw ``get_connection()``
calls main.py makes directly, ``core_service.bridge_public_stima`` and the
PDF/email/whatsapp side effects are monkeypatched exactly like in that
existing test, and ``seller_intelligence.service.repository.si_cursor`` is
monkeypatched with the same style of in-memory fake already used in
tests/test_seller_intelligence_repository.py.

The single most important test in this file is
``test_salva_stima_continues_when_seller_intelligence_fails_completely``:
it forces ``seller_intelligence.service.record_event`` to raise on every
call and asserts the endpoint's response, the CORE bridge outcome, and the
PDF/email/whatsapp side effects are byte-for-byte identical to the
Seller-Intelligence-disabled baseline.
"""

from __future__ import annotations

import asyncio
import copy
import re
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from integration_p2_support import import_project_module


# --- fakes for the raw get_connection()-based writes main.py performs
#     directly (INSERT/UPDATE on `stime`), copied in spirit from
#     tests/test_public_stima_core_crm_bridge.py's LegacyConnection/Cursor ---

class LegacyCursor:
    # P26-2B2B: salva_stima resolves the Default Agency on this connection
    # before inserting, so the fake answers that lookup. It honours
    # cursor_factory the way psycopg2 does - dict rows for the factory, which
    # reads row["id"], tuple rows for the INSERT, which reads fetchone()[0].
    def __init__(self, connection, *, dict_rows=False):
        self.connection = connection
        self.dict_rows = dict_rows
        self.current = None

    def execute(self, query, params=None):
        self.connection.executions.append((" ".join(query.split()), params))
        if "INSERT INTO stime" in query:
            self.connection.stima_agency_id = params[-1] if params else None
            self.current = (self.connection.stima_id,)
        elif "FROM stime WHERE id" in query:
        # P27-6: dopo il commit, il contesto che va al bridge viene RILETTO
        # dalla riga `stime`. Il doppio ricorda l'agenzia incisa dalla INSERT
        # (l'ultimo parametro) e risponde con quella: una costante direbbe che
        # due numeri scritti nel test coincidono, invece che "il bridge riceve
        # cio' che la stima porta scritto".
            incisa = getattr(self.connection, "stima_agency_id", None)
            if incisa is None:
                self.current = None
            else:
                self.current = {"agency_id": incisa} if self.dict_rows else (incisa,)
        elif "FROM agencies" in query:
            agency_id = self.connection.agency_id
            if agency_id is None:
                self.current = None
            else:
                self.current = {"id": agency_id} if self.dict_rows else (agency_id,)
        else:
            self.current = None

    def fetchone(self):
        return self.current

    def close(self):
        pass


class LegacyConnection:
    def __init__(self, stima_id=501, agency_id=1):
        self.stima_id = stima_id
        self.agency_id = agency_id
        self.executions = []
        self.commit_count = 0

    def cursor(self, **kwargs):
        return LegacyCursor(self, dict_rows="cursor_factory" in kwargs)

    def commit(self):
        self.commit_count += 1

    def close(self):
        pass


class JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def base_payload(**overrides):
    payload = {
        "comune": "Alba Adriatica",
        "microzona": "Centro",
        "mq": 90,
        "nome": "Mario",
        "cognome": "Rossi",
        "email": "mario@example.com",
        "telefono": "+39 333 123 4567",
        "prezzo_mq_base": 1500,
        "tipologia": "Appartamento",
    }
    payload.update(overrides)
    return payload


# --- fake for seller_intelligence.service.repository.si_cursor, same
#     design as tests/test_seller_intelligence_repository.py ---

def _unwrap_json_adapter(value):
    """Mirrors what a real Postgres round-trip does for a JSONB column.

    repository.py wraps the payload with psycopg2.extras.Json(...) before
    passing it to cur.execute(). Against a real connection, psycopg2 itself
    serializes that adapter when the query runs, and RealDictCursor
    deserializes the JSONB column back into a plain dict when the row is
    read back - the fake here never executes a real query, so without this
    it would store (and later return) the raw Json wrapper object instead
    of the dict it wraps. `.adapted` is the exact original value the
    adapter holds (psycopg2.extras.Json.__init__ sets self.adapted =
    adapted), so unwrapping it reproduces the real round-trip for any
    JSON-safe structure - which is all this module ever stores. Falls back
    to the value unchanged when it isn't a Json wrapper (e.g. under the
    psycopg2 stub tests/conftest.py installs when the real driver is
    unavailable, where Json(x) already returns x directly).
    """
    return getattr(value, "adapted", value)


class SICursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.database.sql.append((sql, params))
        # P26-6A: the system write path derives the event's agency from its
        # references before inserting - there is no operator in the public
        # funnel - so the fake answers those lookups. Every reference here
        # belongs to one agency, which is what makes the derivation
        # unambiguous; the ambiguous and orphan cases are covered by migration
        # 044 and tests/test_p26_6a_seller_engine_isolation.py.
        if re.match(r"select agency_id from (contacts|leads|stime|properties) where id = %s", sql):
            self.rows = [{"agency_id": self.database.agency_id}]
            return
        if "insert into seller_timeline_events" in sql:
            self._handle_insert(params)
            return
        if "from seller_timeline_events where idempotency_key" in sql:
            key = params[0]
            match = next((r for r in self.database.rows if r["idempotency_key"] == key), None)
            self.rows = [copy.deepcopy(match)] if match else []
            return
        raise AssertionError(f"unexpected seller_intelligence SQL in P17-B1 test: {sql}")

    def _handle_insert(self, params):
        idempotency_key = params.get("idempotency_key")
        if idempotency_key is not None:
            existing = next(
                (r for r in self.database.rows if r["idempotency_key"] == idempotency_key), None,
            )
            if existing is not None:
                self.rows = []
                return
        row = {
            "id": self.database.next_id,
            "contact_id": params.get("contact_id"),
            "lead_id": params.get("lead_id"),
            "stima_id": params.get("stima_id"),
            "property_id": params.get("property_id"),
            "event_type": params.get("event_type"),
            "event_source": params.get("event_source"),
            "occurred_at": params.get("occurred_at"),
            "payload": copy.deepcopy(_unwrap_json_adapter(params.get("payload"))),
            "idempotency_key": idempotency_key,
            "created_by": params.get("created_by"),
            "created_at": datetime.now(timezone.utc),
        }
        self.database.next_id += 1
        self.database.rows.append(row)
        self.rows = [copy.deepcopy(row)]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class SIDatabase:
    def __init__(self, agency_id=1):
        self.agency_id = agency_id
        self.rows = []
        self.next_id = 1
        self.sql = []

    @contextmanager
    def cursor(self, *, commit=False):
        yield self, SICursor(self)


def install_pdf_email_whatsapp_mocks(monkeypatch, main_module, *, pdf_calls, emails,
                                     whatsapp, mail_result=True, accodate=None):
    """P29 cutover: `emails` raccoglie ora SOLO l'alert amministratore.

    La mail al cliente non passa piu' da `invia_mail`. Viene accodata nel ledger
    delle comunicazioni, e `accodate` e' la spia di quell'accodamento.

    `mail_result` resta perche' l'alert amministratore passa ancora da
    `invia_mail`, ma non governa piu' nessun evento: `email_stima_inviata` non
    nasce piu' qui. Nasce alla finalizzazione `sent` del messaggio accodato, che
    e' il momento in cui la mail e' davvero partita - vedi
    `communication/integrations.py` e
    `tests/test_p29_cutover_email_cliente.py`.
    """
    accodate = [] if accodate is None else accodate

    def finto_enqueue(ctx, **kwargs):
        accodate.append((ctx, kwargs))
        return {"message": {"id": 900 + len(accodate), "status": "queued"},
                "created": True}

    monkeypatch.setattr(main_module.communication_service, "enqueue", finto_enqueue)
    monkeypatch.setattr(
        main_module,
        "compute_from_payload",
        lambda _payload: {
            "price_exact": 180000, "eur_mq_finale": 2000,
            "valore_pertinenze": 5000, "base_mq": 1500,
        },
    )
    monkeypatch.setattr(
        main_module,
        "genera_pdf_stima",
        lambda payload, nome_file: pdf_calls.append((payload, nome_file)) or "reports/stima_501.pdf",
    )
    monkeypatch.setattr(main_module, "invia_mail", lambda *args: emails.append(args) or mail_result)
    monkeypatch.setattr(main_module, "invia_whatsapp", lambda *args: whatsapp.append(args))


def expected_success_response(main_module):
    # Rispecchia esattamente il return statement reale di salva_stima
    # (main.py, blocco "11. Risposta JSON al frontend"): success, id,
    # pdf_url, price_exact, eur_mq_finale, valore_pertinenze, base_mq.
    # genera_pdf_stima e' mockata per restituire "reports/stima_501.pdf",
    # che non inizia con "http" - main.py lo antepone con PUBLIC_BASE_URL
    # esattamente come fa gia' tests/test_public_stima_core_crm_bridge.py.
    return {
        "success": True,
        "id": 501,
        "pdf_url": f"{main_module.PUBLIC_BASE_URL}/reports/stima_501.pdf",
        "price_exact": 180000,
        "eur_mq_finale": 2000,
        "valore_pertinenze": 5000,
        "base_mq": 1500,
    }


# --- router registration -----------------------------------------------

def test_seller_intelligence_router_is_registered_and_admin_protected():
    main_module = import_project_module("main")
    paths = main_module.app.openapi()["paths"]

    assert "/api/seller-intelligence/events" in paths, "il router P17 deve essere registrato in main.py"
    assert "/api/seller-intelligence/timeline" in paths

    for path in ("/api/seller-intelligence/events", "/api/seller-intelligence/timeline"):
        for method, operation in paths[path].items():
            if method in {"get", "post"}:
                assert operation.get("security"), f"{method.upper()} {path} deve richiedere autenticazione admin"

    # Contratto pubblico esistente invariato: /api/salva_stima resta anonimo.
    assert not paths["/api/salva_stima"]["post"].get("security")


def test_seller_intelligence_router_does_not_replace_core_activities():
    main_module = import_project_module("main")
    paths = main_module.app.openapi()["paths"]
    assert "/api/core/activities" in paths, "l'endpoint CORE esistente deve restare presente e invariato"


# --- IL TEST PIU' IMPORTANTE: non-blocco su fallimento totale ----------

def test_salva_stima_continues_when_seller_intelligence_fails_completely(monkeypatch):
    main_module = import_project_module("main")

    connection = LegacyConnection(stima_id=501)
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)

    bridge_calls = []

    def working_bridge(stima_id, **data):
        bridge_calls.append((stima_id, data))
        return {
            "status": "linked", "stima_id": stima_id,
            "contact_id": 42, "lead_id": 99,
            "contact_created": True, "lead_created": True,
        }

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", working_bridge)

    pdf_calls, emails, whatsapp = [], [], []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=pdf_calls, emails=emails, whatsapp=whatsapp)

    def always_failing_record_event(**kwargs):
        raise RuntimeError("simulated total Seller Intelligence outage")

    monkeypatch.setattr(main_module.seller_intelligence_service, "record_event", always_failing_record_event)

    # Nessuna eccezione deve propagarsi da qui: l'endpoint deve completare
    # esattamente come se Seller Intelligence non esistesse.
    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module), (
        "la response pubblica non deve cambiare di una virgola per un guasto Seller Intelligence"
    )
    assert bridge_calls and bridge_calls[0][0] == 501, "il bridge CORE deve essere eseguito normalmente"
    assert len(pdf_calls) == 1, "la generazione PDF non deve essere impedita"
    assert len(emails) == 1, "l'alert amministratore non deve essere impedito"
    assert len(whatsapp) == 1, "l'invio WhatsApp non deve essere impedito"


def test_salva_stima_continues_when_seller_intelligence_fails_even_if_bridge_also_fails(monkeypatch):
    """Caso ancora piu' avverso: bridge CORE E Seller Intelligence falliscono
    entrambi. L'endpoint deve comunque completare (comportamento del bridge
    gia' garantito da tests/test_public_stima_core_crm_bridge.py; qui si
    verifica che le integrazioni P17-B1/P17-B2 non introducano un nuovo modo
    di rompere questo invariante).

    Dopo il cutover P29 il funnel tenta DUE eventi Seller Intelligence
    indipendenti per ogni stima completata: stima_richiesta e stima_completata.
    Il terzo, email_stima_inviata, non e' scomparso: si e' spostato dove il suo
    significato e' vero, cioe' alla finalizzazione `sent` del messaggio.
    Qui record_event() e' forzato a fallire sempre: entrambi i tentativi devono
    avvenire comunque, in modo indipendente (nessuno dei due deve impedire
    l'altro - vedi safe_record_event), tutti con contact_id/lead_id None
    (bridge_result e' rimasto None perche' il bridge CORE e' stato forzato a
    fallire), e nessuna delle eccezioni deve raggiungere l'endpoint: risposta,
    PDF, email e WhatsApp devono comportarsi come nel flusso legacy."""
    main_module = import_project_module("main")

    connection = LegacyConnection(stima_id=501)
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)

    def failing_bridge(stima_id, **data):
        raise RuntimeError("simulated CORE bridge outage")

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", failing_bridge)

    pdf_calls, emails, whatsapp = [], [], []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=pdf_calls, emails=emails, whatsapp=whatsapp)

    seller_intelligence_calls = []

    def failing_record_event(**kwargs):
        seller_intelligence_calls.append(kwargs)
        raise RuntimeError("simulated total Seller Intelligence outage")

    monkeypatch.setattr(main_module.seller_intelligence_service, "record_event", failing_record_event)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    # Il funnel pubblico e' invariato: risposta, PDF, alert amministratore e
    # WhatsApp proseguono esattamente come nel comportamento legacy, nonostante
    # Seller Intelligence sia completamente KO.
    assert response == expected_success_response(main_module)
    assert len(pdf_calls) == 1
    assert len(emails) == 1  # solo l'alert amministratore
    assert len(whatsapp) == 1

    # I due tentativi Seller Intelligence sono avvenuti entrambi, in ordine,
    # ognuno indipendentemente dal fallimento dell'altro.
    assert [call["event_type"] for call in seller_intelligence_calls] == [
        "stima_richiesta",
        "stima_completata",
    ]
    for call in seller_intelligence_calls:
        assert call["stima_id"] == 501
        # bridge_result e' rimasto None: nessun crash, nessun contact_id/lead_id.
        assert call["contact_id"] is None
        assert call["lead_id"] is None


# --- caso di successo: una riga corretta, idempotenza sui retry --------

def test_salva_stima_records_exactly_one_stima_richiesta_event_on_success(monkeypatch):
    main_module = import_project_module("main")

    connection = LegacyConnection(stima_id=501)
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)

    def working_bridge(stima_id, **data):
        return {
            "status": "linked", "stima_id": stima_id,
            "contact_id": 42, "lead_id": 99,
            "contact_created": True, "lead_created": True,
        }

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", working_bridge)
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=[], emails=[], whatsapp=[])

    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository, "si_cursor", si_db.cursor)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload(comune="Alba Adriatica", tipologia="Appartamento", mq=90))))

    assert response == expected_success_response(main_module)
    # P29 cutover: dal producer nascono DUE eventi, stima_richiesta e
    # stima_completata. `email_stima_inviata` non nasce piu' qui perche' qui la
    # mail non e' ancora partita - e' in coda. Qui si verifica, come in P17-B1,
    # che esista esattamente una riga stima_richiesta con i campi corretti.
    assert len(si_db.rows) == 2, (
        "P29 cutover: il producer produce 2 eventi (richiesta + completata)")
    stima_richiesta_rows = [r for r in si_db.rows if r["event_type"] == "stima_richiesta"]
    assert len(stima_richiesta_rows) == 1, "deve esistere esattamente una riga stima_richiesta"
    row = stima_richiesta_rows[0]
    assert row["event_type"] == "stima_richiesta"
    assert row["event_source"] == "stima360_it"
    assert row["stima_id"] == 501
    assert row["contact_id"] == 42
    assert row["lead_id"] == 99
    assert row["payload"] == {"comune": "Alba Adriatica", "tipologia": "Appartamento", "mq": 90.0}
    assert row["idempotency_key"] == "stima_richiesta:501"


def test_salva_stima_retry_with_same_stima_id_does_not_duplicate_the_event(monkeypatch):
    main_module = import_project_module("main")

    def working_bridge(stima_id, **data):
        return {"status": "linked", "stima_id": stima_id, "contact_id": 42, "lead_id": 99,
                "contact_created": False, "lead_created": True}

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", working_bridge)
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=[], emails=[], whatsapp=[])

    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository, "si_cursor", si_db.cursor)

    # Due "richieste" indipendenti che, per qualunque motivo (retry HTTP,
    # doppio click, doppia esecuzione), finiscono per generare la STESSA
    # stima_id lato DB (qui simulato: entrambe le connessioni fake tornano
    # id=501). L'idempotency_key deterministica deve impedire il duplicato.
    monkeypatch.setattr(main_module, "get_connection", lambda: LegacyConnection(stima_id=501))
    first_response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    monkeypatch.setattr(main_module, "get_connection", lambda: LegacyConnection(stima_id=501))
    second_response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert first_response == second_response == expected_success_response(main_module)
    # P29 cutover: 2 richieste identiche devono produrre 2 righe totali (una per
    # ciascuno dei 2 event_type del producer), non 4 - l'idempotency_key
    # deterministica deduplica ciascun tipo di evento indipendentemente.
    assert len(si_db.rows) == 2, "un retry con la stessa stima_id non deve duplicare NESSUNO dei 2 eventi"
    event_types = sorted(r["event_type"] for r in si_db.rows)
    assert event_types == ["stima_completata", "stima_richiesta"]


# =========================================================================
# P17-B2: stima_completata (+ email_stima_inviata, fino al cutover P29)
#
# `email_stima_inviata` non nasce piu' in questo endpoint. Il suo significato -
# "la mail al cliente e' partita" - non e' cambiato di una virgola; e' cambiato
# il punto in cui quel fatto accade, che ora e' la finalizzazione `sent` del
# messaggio accodato. Cio' che era provato qui (l'evento non esiste se la mail
# non e' partita; un guasto Seller Intelligence non blocca il funnel) e' provato
# la', su PostgreSQL, in `tests/test_p29_cutover_email_cliente.py`.
# =========================================================================

def _selective_failure(real_record_event, failing_event_type):
    """Wraps the REAL record_event: raises only for one event_type, delegates
    everything else to the real function untouched. Used for test D to
    prove the producer's events are independent of one another - a failure in
    one must never affect whether the others are attempted or recorded."""
    def wrapper(**kwargs):
        if kwargs.get("event_type") == failing_event_type:
            raise RuntimeError(f"simulated Seller Intelligence outage for {failing_event_type}")
        return real_record_event(**kwargs)
    return wrapper


def _setup_happy_path(monkeypatch, *, mail_result=True):
    main_module = import_project_module("main")
    monkeypatch.setattr(main_module, "get_connection", lambda: LegacyConnection(stima_id=501))

    def working_bridge(stima_id, **data):
        return {"status": "linked", "stima_id": stima_id, "contact_id": 42, "lead_id": 99,
                "contact_created": True, "lead_created": True}

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", working_bridge)
    pdf_calls, emails, whatsapp = [], [], []
    install_pdf_email_whatsapp_mocks(
        monkeypatch, main_module, pdf_calls=pdf_calls, emails=emails, whatsapp=whatsapp, mail_result=mail_result,
    )
    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository, "si_cursor", si_db.cursor)
    return main_module, si_db, pdf_calls, emails, whatsapp


# --- A. Happy path: 1+1, nessun duplicato -------------------------------

def test_p17b2_happy_path_produces_exactly_one_of_each_producer_event(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(si_db.rows) == 2
    by_type = {r["event_type"]: r for r in si_db.rows}
    assert set(by_type) == {"stima_richiesta", "stima_completata"}
    assert "email_stima_inviata" not in by_type, (
        "l'evento e' tornato nel producer: accodare non e' aver mandato")
    for row in si_db.rows:
        assert row["stima_id"] == 501
        assert row["event_source"] == "stima360_it"
        assert row["contact_id"] == 42
        assert row["lead_id"] == 99
    assert by_type["stima_richiesta"]["idempotency_key"] == "stima_richiesta:501"
    assert by_type["stima_completata"]["idempotency_key"] == "stima_completata:501"


# --- B. Payload esatto di stima_completata -------------------------------

def test_p17b2_stima_completata_payload_contains_only_the_three_specified_fields(monkeypatch):
    main_module, si_db, *_ = _setup_happy_path(monkeypatch)

    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    row = next(r for r in si_db.rows if r["event_type"] == "stima_completata")
    # Deve combaciare esattamente con quanto restituito dal motore reale
    # (qui mockato con gli stessi nomi di chiave usati da valuation.py e
    # gia' verificati in tests/test_public_stima_core_crm_bridge.py):
    # price_exact, eur_mq_finale, base_mq - NON valore_pertinenze (non
    # richiesto), NON dati personali.
    assert row["payload"] == {"price_exact": 180000, "eur_mq_finale": 2000, "base_mq": 1500}


def test_p29_il_pdf_url_dellevento_viaggia_nei_metadata_del_messaggio(monkeypatch):
    """Il `pdf_url` che finiva nel payload dell'evento non e' andato perduto.

    Adesso viaggia nei metadata del messaggio accodato, ed e' da li' che la
    finalizzazione `sent` lo rimette nel payload dell'evento - identico. Se il
    producer smettesse di scriverlo, l'evento nascerebbe con un payload vuoto e
    nessuno se ne accorgerebbe fino a leggere una timeline.
    """
    main_module = import_project_module("main")
    monkeypatch.setattr(main_module, "get_connection", lambda: LegacyConnection(stima_id=501))
    monkeypatch.setattr(
        main_module.core_service, "bridge_public_stima",
        lambda stima_id, **data: {"status": "linked", "stima_id": stima_id,
                                  "contact_id": 42, "lead_id": 99,
                                  "contact_created": True, "lead_created": True})
    accodate = []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=[], emails=[],
                                    whatsapp=[], accodate=accodate)
    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository,
                        "si_cursor", si_db.cursor)

    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert len(accodate) == 1
    _ctx, accodato = accodate[0]
    assert accodato["metadata"] == {
        "pdf_url": f"{main_module.PUBLIC_BASE_URL}/reports/stima_501.pdf"}


# --- C. Il cliente non riceve piu' un invio diretto: si accoda ----------

def test_p29_la_mail_cliente_e_accodata_e_non_spedita_dallendpoint(monkeypatch):
    """IL test del cutover, dal lato del producer.

    Prima c'era `invia_mail(data["email"], ...)` e, subito sotto, l'evento se
    quella funzione ritornava True. Adesso l'endpoint accoda e non spedisce: il
    solo invio diretto che resta e' l'alert amministratore, e l'evento non
    nasce qui perche' qui la mail non e' partita.

    `mail_result=False` e' rimasto nella firma dell'helper e ora non cambia
    niente per il cliente - e' il modo piu' diretto di provare che quel bool
    non governa piu' nessun evento.
    """
    main_module = import_project_module("main")
    monkeypatch.setattr(main_module, "get_connection", lambda: LegacyConnection(stima_id=501))
    monkeypatch.setattr(
        main_module.core_service, "bridge_public_stima",
        lambda stima_id, **data: {"status": "linked", "stima_id": stima_id,
                                  "contact_id": 42, "lead_id": 99,
                                  "contact_created": True, "lead_created": True})
    pdf_calls, emails, whatsapp, accodate = [], [], [], []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=pdf_calls,
                                    emails=emails, whatsapp=whatsapp,
                                    mail_result=False, accodate=accodate)
    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository,
                        "si_cursor", si_db.cursor)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(whatsapp) == 1

    # UN solo invio diretto, e non e' al cliente.
    assert len(emails) == 1
    destinatario, oggetto, _corpo = emails[0][0], emails[0][1], emails[0][2]
    assert destinatario == "info@stima360.it"
    assert "NUOVO LEAD" in oggetto
    assert destinatario != base_payload()["email"]

    # La mail al cliente e' UNA riga accodata, con i campi che il dominio chiede.
    assert len(accodate) == 1
    ctx, accodato = accodate[0]
    assert accodato["channel"] == "email"
    assert accodato["communication_type"] == "service"
    assert accodato["mode"] == "automatic"
    assert accodato["reason_code"] == "stima_pdf"
    assert accodato["destination_snapshot"] == base_payload()["email"]
    assert accodato["stima_id"] == 501
    assert accodato["contact_id"] == 42 and accodato["lead_id"] == 99
    assert accodato["idempotency_key"] == "stima_email_cliente:501"
    assert accodato["subject_snapshot"] and accodato["rendered_body"]
    # L'agenzia viene dal contesto LETTO dalla stima, non dal payload.
    assert ctx.agency_id is not None
    assert getattr(ctx, "origin", None) == "public_stima"

    # E l'evento NON esiste: la mail e' in coda, non partita.
    event_types = {r["event_type"] for r in si_db.rows}
    assert event_types == {"stima_richiesta", "stima_completata"}
    assert "email_stima_inviata" not in event_types


def test_p29_un_replay_non_accoda_due_mail(monkeypatch):
    """La chiave e' deterministica sulla stima: due giri, una mail.

    L'helper qui e' quello vero - `enqueue` non e' sostituito - perche' cio' che
    si prova e' la CHIAVE che il producer costruisce, e la dedup vera e' del
    dominio (provata su PostgreSQL). Due salvataggi che finiscono sulla stessa
    `stima_id` devono chiedere la stessa chiave, non due chiavi diverse.
    """
    main_module = import_project_module("main")
    monkeypatch.setattr(
        main_module.core_service, "bridge_public_stima",
        lambda stima_id, **data: {"status": "linked", "stima_id": stima_id,
                                  "contact_id": 42, "lead_id": 99,
                                  "contact_created": False, "lead_created": True})
    accodate = []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=[], emails=[],
                                    whatsapp=[], accodate=accodate)
    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository,
                        "si_cursor", si_db.cursor)

    for _ in range(2):
        monkeypatch.setattr(main_module, "get_connection",
                            lambda: LegacyConnection(stima_id=501))
        asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    chiavi = {accodato["idempotency_key"] for _ctx, accodato in accodate}
    assert chiavi == {"stima_email_cliente:501"}, (
        "due giri sulla stessa stima hanno chiesto due chiavi diverse")


# --- D. Seller Intelligence KO su stima_completata -----------------------

def test_p17b2_stima_completata_failure_does_not_block_funnel(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    real_record_event = main_module.seller_intelligence_service.record_event
    monkeypatch.setattr(
        main_module.seller_intelligence_service, "record_event",
        _selective_failure(real_record_event, "stima_completata"),
    )

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module), "nessun HTTP 500, response invariata"
    assert len(pdf_calls) == 1, "il PDF continua"
    assert len(emails) == 1, "l'alert amministratore continua"
    assert len(whatsapp) == 1, "il WhatsApp continua"

    event_types = {r["event_type"] for r in si_db.rows}
    assert "stima_completata" not in event_types, "l'evento fallito non deve comparire"
    assert "stima_richiesta" in event_types, "l'evento precedente non e' influenzato"


# --- E. Il producer non chiede MAI quell'evento --------------------------

def test_p29_il_producer_non_chiede_mai_email_stima_inviata(monkeypatch):
    """Piu' forte di "la riga non c'e'": la CHIAMATA non avviene.

    Il test che stava qui provava che un guasto Seller Intelligence su
    `email_stima_inviata` non bloccasse il funnel. Dopo il cutover quel guasto
    non e' raggiungibile da questo endpoint, e provare che un percorso morto non
    faccia danni non prova niente. Si prova invece cio' che deve restare vero:
    fra tutti gli eventi che `/api/salva_stima` chiede, quello non c'e'.

    Si guarda ogni `record_event`, non le righe scritte: un `event_type`
    richiesto e poi rifiutato dall'idempotenza sarebbe invisibile nelle righe e
    sarebbe comunque un producer che dice "e' partita" troppo presto.
    """
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    richiesti = []
    real_record_event = main_module.seller_intelligence_service.record_event

    def spia(**kwargs):
        richiesti.append(kwargs.get("event_type"))
        return real_record_event(**kwargs)

    monkeypatch.setattr(main_module.seller_intelligence_service, "record_event", spia)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert richiesti == ["stima_richiesta", "stima_completata"], richiesti
    assert "email_stima_inviata" not in richiesti


# --- Semantica: stima_completata esiste anche se il PDF fallisce ---------

def test_p17b2_stima_completata_exists_even_if_pdf_generation_fails(monkeypatch):
    """Certifica lo spostamento richiesto nella correzione pre-test:
    stima_completata deve significare esclusivamente "il motore di
    valutazione ha calcolato con successo", non "e' stato anche generato un
    PDF". Il calcolo riesce (compute_from_payload mockato, come sempre),
    ma genera_pdf_stima() viene forzata a fallire: il comportamento legacy
    esistente (HTTPException 500 "Errore PDF: ...") non deve cambiare -
    verifichiamo che sia ancora quello, E che stima_completata sia
    comunque gia' stata scritta PRIMA che il PDF fallisse, mentre la mail al
    cliente non puo' nemmeno essere accodata perche' il flusso non arriva mai
    fino la'."""
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    def failing_pdf(payload, nome_file):
        raise RuntimeError("simulated PDF generation outage")

    monkeypatch.setattr(main_module, "genera_pdf_stima", failing_pdf)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    # Comportamento legacy invariato: main.py cattura l'eccezione del PDF e
    # rilancia HTTPException 500 esattamente come faceva prima di P17.
    assert exc_info.value.status_code == 500
    assert "Errore PDF" in exc_info.value.detail
    assert len(emails) == 0, "il flusso non deve mai arrivare all'invio email"
    assert len(whatsapp) == 0

    event_types = {r["event_type"] for r in si_db.rows}
    assert "stima_completata" in event_types, (
        "il calcolo era riuscito prima del fallimento PDF: l'evento deve esistere comunque"
    )
    assert "email_stima_inviata" not in event_types, (
        "il flusso non e' mai arrivato all'invio email: l'evento non deve esistere"
    )
    row = next(r for r in si_db.rows if r["event_type"] == "stima_completata")
    assert row["payload"] == {"price_exact": 180000, "eur_mq_finale": 2000, "base_mq": 1500}
