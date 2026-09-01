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
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from integration_p2_support import import_project_module


# --- fakes for the raw get_connection()-based writes main.py performs
#     directly (INSERT/UPDATE on `stime`), copied in spirit from
#     tests/test_public_stima_core_crm_bridge.py's LegacyConnection/Cursor ---

class LegacyCursor:
    def __init__(self, connection):
        self.connection = connection
        self.current = None

    def execute(self, query, params=None):
        self.connection.executions.append((" ".join(query.split()), params))
        if "INSERT INTO stime" in query:
            self.current = (self.connection.stima_id,)
        else:
            self.current = None

    def fetchone(self):
        return self.current

    def close(self):
        pass


class LegacyConnection:
    def __init__(self, stima_id=501):
        self.stima_id = stima_id
        self.executions = []
        self.commit_count = 0

    def cursor(self, **_kwargs):
        return LegacyCursor(self)

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
    def __init__(self):
        self.rows = []
        self.next_id = 1
        self.sql = []

    @contextmanager
    def cursor(self, *, commit=False):
        yield self, SICursor(self)


def install_pdf_email_whatsapp_mocks(monkeypatch, main_module, *, pdf_calls, emails, whatsapp, mail_result=True):
    """mail_result controls what the CLIENT invia_mail() call returns (P17-B2:
    main.py now captures this as `mail_sent` to gate email_stima_inviata).
    Both the client and the admin email go through the same mocked
    invia_mail, so both calls return mail_result - this only matters for
    email_stima_inviata, which is keyed off the client call specifically
    (the first one main.py makes) and never off the admin one."""
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
    assert len(emails) == 2, "gli invii email (cliente + admin) non devono essere impediti"
    assert len(whatsapp) == 1, "l'invio WhatsApp non deve essere impedito"


def test_salva_stima_continues_when_seller_intelligence_fails_even_if_bridge_also_fails(monkeypatch):
    """Caso ancora piu' avverso: bridge CORE E Seller Intelligence falliscono
    entrambi. L'endpoint deve comunque completare (comportamento del bridge
    gia' garantito da tests/test_public_stima_core_crm_bridge.py; qui si
    verifica che le integrazioni P17-B1/P17-B2 non introducano un nuovo modo
    di rompere questo invariante).

    Dopo P17-B2 il funnel tenta TRE eventi Seller Intelligence indipendenti
    per ogni stima completata con successo: stima_richiesta, stima_completata
    ed email_stima_inviata (l'ultimo perche' install_pdf_email_whatsapp_mocks
    di default simula un invio email cliente riuscito, mail_result=True).
    Qui record_event() e' forzato a fallire sempre: ognuno dei tre tentativi
    deve avvenire comunque, in modo indipendente (nessuno dei tre deve
    impedire i successivi - vedi safe_record_event), tutti con contact_id/
    lead_id None (bridge_result e' rimasto None perche' il bridge CORE e'
    stato forzato a fallire), e nessuna delle tre eccezioni deve raggiungere
    l'endpoint: risposta, PDF, email e WhatsApp devono comportarsi come nel
    flusso legacy."""
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

    # Il funnel pubblico e' invariato: risposta, PDF, email (cliente + admin)
    # e WhatsApp proseguono esattamente come nel comportamento legacy,
    # nonostante Seller Intelligence sia completamente KO.
    assert response == expected_success_response(main_module)
    assert len(pdf_calls) == 1
    assert len(emails) == 2  # cliente + admin
    assert len(whatsapp) == 1

    # I tre tentativi Seller Intelligence sono avvenuti tutti, in ordine,
    # ognuno indipendentemente dal fallimento dei precedenti.
    assert [call["event_type"] for call in seller_intelligence_calls] == [
        "stima_richiesta",
        "stima_completata",
        "email_stima_inviata",
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
    # Dal P17-B2 una richiesta di successo produce anche stima_completata ed
    # email_stima_inviata (test dedicati piu' sotto per il dettaglio
    # completo) - qui verifichiamo solo che continui a esistere esattamente
    # una riga stima_richiesta con i campi corretti, invariata rispetto a
    # P17-B1.
    assert len(si_db.rows) == 3, "P17-B2: una richiesta di successo produce 3 eventi (richiesta+completata+email)"
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
    # P17-B2: 2 richieste identiche devono produrre 3 righe totali (una per
    # ciascuno dei 3 event_type), non 6 - l'idempotency_key deterministica
    # deduplica ciascun tipo di evento indipendentemente.
    assert len(si_db.rows) == 3, "un retry con la stessa stima_id non deve duplicare NESSUNO dei 3 eventi"
    event_types = sorted(r["event_type"] for r in si_db.rows)
    assert event_types == ["email_stima_inviata", "stima_completata", "stima_richiesta"]


# =========================================================================
# P17-B2: stima_completata + email_stima_inviata
# =========================================================================

def _selective_failure(real_record_event, failing_event_type):
    """Wraps the REAL record_event: raises only for one event_type, delegates
    everything else to the real function untouched. Used for test D/E to
    prove the three events are independent of one another - a failure in
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


# --- A. Happy path: 1+1+1, nessun duplicato -----------------------------

def test_p17b2_happy_path_produces_exactly_one_of_each_of_the_three_events(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(si_db.rows) == 3
    by_type = {r["event_type"]: r for r in si_db.rows}
    assert set(by_type) == {"stima_richiesta", "stima_completata", "email_stima_inviata"}
    for row in si_db.rows:
        assert row["stima_id"] == 501
        assert row["event_source"] == "stima360_it"
        assert row["contact_id"] == 42
        assert row["lead_id"] == 99
    assert by_type["stima_richiesta"]["idempotency_key"] == "stima_richiesta:501"
    assert by_type["stima_completata"]["idempotency_key"] == "stima_completata:501"
    assert by_type["email_stima_inviata"]["idempotency_key"] == "email_stima_inviata:501"


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


def test_p17b2_email_stima_inviata_payload_contains_only_pdf_url(monkeypatch):
    main_module, si_db, *_ = _setup_happy_path(monkeypatch)

    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    row = next(r for r in si_db.rows if r["event_type"] == "email_stima_inviata")
    assert row["payload"] == {"pdf_url": f"{main_module.PUBLIC_BASE_URL}/reports/stima_501.pdf"}


# --- C. Email fallita: email_stima_inviata assente, funnel invariato ----

def test_p17b2_failed_client_email_skips_email_stima_inviata_but_not_the_rest(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch, mail_result=False)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    # Il funnel esistente non cambia comportamento: risposta identica,
    # tentativi di invio email (2: cliente + admin) e WhatsApp comunque
    # eseguiti come sempre - invia_mail() che ritorna False e' un esito
    # normale gia' gestito dal codice esistente, P17 non lo altera.
    assert response == expected_success_response(main_module)
    assert len(emails) == 2
    assert len(whatsapp) == 1

    event_types = {r["event_type"] for r in si_db.rows}
    assert "stima_richiesta" in event_types
    assert "stima_completata" in event_types
    assert "email_stima_inviata" not in event_types, (
        "invia_mail(cliente) ha ritornato False: l'evento non deve essere scritto"
    )
    assert len(si_db.rows) == 2


# --- D. Seller Intelligence KO su stima_completata -----------------------

def test_p17b2_stima_completata_failure_does_not_block_funnel_or_email_event(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    real_record_event = main_module.seller_intelligence_service.record_event
    monkeypatch.setattr(
        main_module.seller_intelligence_service, "record_event",
        _selective_failure(real_record_event, "stima_completata"),
    )

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module), "nessun HTTP 500, response invariata"
    assert len(pdf_calls) == 1, "il PDF continua"
    assert len(emails) == 2, "le email continuano"
    assert len(whatsapp) == 1, "il WhatsApp continua"

    event_types = {r["event_type"] for r in si_db.rows}
    assert "stima_completata" not in event_types, "l'evento fallito non deve comparire"
    assert "stima_richiesta" in event_types, "l'evento precedente non e' influenzato"
    assert "email_stima_inviata" in event_types, (
        "email_stima_inviata deve essere comunque tentato indipendentemente dal fallimento di stima_completata"
    )


# --- E. Seller Intelligence KO su email_stima_inviata --------------------

def test_p17b2_email_stima_inviata_failure_does_not_block_funnel(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp = _setup_happy_path(monkeypatch)

    real_record_event = main_module.seller_intelligence_service.record_event
    monkeypatch.setattr(
        main_module.seller_intelligence_service, "record_event",
        _selective_failure(real_record_event, "email_stima_inviata"),
    )

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module), "nessun HTTP 500, response invariata"
    assert len(pdf_calls) == 1
    assert len(emails) == 2
    assert len(whatsapp) == 1

    event_types = {r["event_type"] for r in si_db.rows}
    assert event_types == {"stima_richiesta", "stima_completata"}, (
        "stima_richiesta e stima_completata devono essere gia' stati scritti correttamente prima del fallimento"
    )


# --- Semantica: stima_completata esiste anche se il PDF fallisce ---------

def test_p17b2_stima_completata_exists_even_if_pdf_generation_fails(monkeypatch):
    """Certifica lo spostamento richiesto nella correzione pre-test:
    stima_completata deve significare esclusivamente "il motore di
    valutazione ha calcolato con successo", non "e' stato anche generato un
    PDF". Il calcolo riesce (compute_from_payload mockato, come sempre),
    ma genera_pdf_stima() viene forzata a fallire: il comportamento legacy
    esistente (HTTPException 500 "Errore PDF: ...") non deve cambiare -
    verifichiamo che sia ancora quello, E che stima_completata sia
    comunque gia' stata scritta PRIMA che il PDF fallisse, mentre
    email_stima_inviata non puo' esistere perche' il flusso non arriva mai
    all'invio email."""
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
