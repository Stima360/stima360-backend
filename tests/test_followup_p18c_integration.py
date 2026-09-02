"""P18-C integration tests: the FOLLOWUP_STIMA_RICHIESTA rule wired into the
real POST /api/salva_stima endpoint.

Same technique already used by
tests/test_seller_intelligence_p17b1_integration.py and
tests/test_public_stima_core_crm_bridge.py: LegacyConnection/LegacyCursor
stand in for the raw get_connection() calls main.py makes directly,
core_service.bridge_public_stima and the PDF/email/whatsapp side effects
are monkeypatched, and P17's seller_intelligence.service is exercised for
real (not mocked) so its own non-blocking guarantee can be asserted
alongside P18's.

Two mocking depths are used on purpose:
- Most tests monkeypatch main_module.followup_service.run_followup
  directly (a spy / a raiser), leaving safe_run_followup() itself real and
  unmocked - this is what actually proves the wrapper's non-blocking
  contract, exactly mirroring how the P17 tests monkeypatch
  seller_intelligence_service.record_event while leaving
  safe_record_event() real.
- test_retry_with_same_stima_id_does_not_create_a_second_task goes one
  layer deeper (fakes followup.repository.followup_cursor and
  core_repository.create_task_with_cursor) because it has to prove
  idempotency end-to-end through the real repository logic, not just that
  the wrapper was called.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from integration_p2_support import import_project_module


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


def install_pdf_email_whatsapp_mocks(monkeypatch, main_module, *, pdf_calls, emails, whatsapp, mail_result=True):
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
    return {
        "success": True,
        "id": 501,
        "pdf_url": f"{main_module.PUBLIC_BASE_URL}/reports/stima_501.pdf",
        "price_exact": 180000,
        "eur_mq_finale": 2000,
        "valore_pertinenze": 5000,
        "base_mq": 1500,
    }


def _unwrap_json_adapter(value):
    """See the identical helper in
    tests/test_seller_intelligence_p17b1_integration.py for the full
    explanation of why this is needed against the fake SI cursor."""
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
        raise AssertionError(f"unexpected seller_intelligence SQL in P18-C test: {sql}")

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


def _setup_happy_path(monkeypatch, *, mail_result=True):
    """Real bridge, real P17 (unmocked record_event, backed by a fake
    si_cursor), real followup_service.run_followup (unmocked) - only the
    lowest-level DB/PDF/email/whatsapp side effects are faked. Returns
    (main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls,
    followup_calls)."""
    main_module = import_project_module("main")

    connection = LegacyConnection(stima_id=501)
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)

    bridge_calls = []

    def working_bridge(stima_id, **data):
        bridge_calls.append((stima_id, data))
        return {
            "status": "linked", "stima_id": stima_id,
            "contact_id": 16, "lead_id": 12,
            "contact_created": True, "lead_created": True,
        }

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", working_bridge)

    pdf_calls, emails, whatsapp = [], [], []
    install_pdf_email_whatsapp_mocks(
        monkeypatch, main_module, pdf_calls=pdf_calls, emails=emails, whatsapp=whatsapp, mail_result=mail_result,
    )

    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository, "si_cursor", si_db.cursor)

    return main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls


def _spy_run_followup(monkeypatch, main_module, *, result=None, raises=None):
    """Monkeypatches followup_service.run_followup itself (not
    safe_run_followup, which stays real) so the wrapper's own catch/log/
    swallow contract is what's actually being exercised - same technique
    the P17 tests use on seller_intelligence_service.record_event."""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return result if result is not None else {"task_id": 1, "followup_action_id": 1, "status": "completed"}

    monkeypatch.setattr(main_module.followup_service, "run_followup", _fake)
    return calls


# --- TEST 1 & 2: called once, with the correct arguments --------------------

def test_stima_richiesta_calls_safe_run_followup_exactly_once_with_correct_arguments(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    followup_calls = _spy_run_followup(monkeypatch, main_module)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(followup_calls) == 1, "safe_run_followup deve essere chiamato esattamente una volta"

    call = followup_calls[0]
    # rule_code="FOLLOWUP_STIMA_RICHIESTA" e' l'espressione concreta di
    # "evento stima_richiesta" nel design P18-B gia' approvato e deployato
    # (vedi followup/rules.py: FOLLOWUP_STIMA_RICHIESTA.event_type ==
    # "stima_richiesta") - run_followup()/safe_run_followup() non
    # accettano un parametro event_type diretto, quindi questa e' la
    # verifica equivalente sul contratto reale della foundation.
    assert call["rule_code"] == "FOLLOWUP_STIMA_RICHIESTA"
    assert call["trigger_type"] == "event"
    assert call["stima_id"] == 501
    assert call["contact_id"] == 16
    assert call["lead_id"] == 12
    assert call["created_by"] == "FOLLOWUP"


def test_stima_richiesta_followup_uses_bridge_result_or_empty_dict_when_bridge_fails(monkeypatch):
    main_module = import_project_module("main")
    connection = LegacyConnection(stima_id=501)
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)

    def failing_bridge(stima_id, **data):
        raise RuntimeError("simulated CORE bridge outage")

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", failing_bridge)

    pdf_calls, emails, whatsapp = [], [], []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=pdf_calls, emails=emails, whatsapp=whatsapp)

    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository, "si_cursor", si_db.cursor)

    followup_calls = _spy_run_followup(monkeypatch, main_module)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(followup_calls) == 1
    assert followup_calls[0]["contact_id"] is None
    assert followup_calls[0]["lead_id"] is None
    assert followup_calls[0]["stima_id"] == 501


# --- TEST 3 & 4: P18 total failure never blocks the funnel or P17 -----------

def test_salva_stima_continues_when_followup_fails_completely(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    followup_calls = _spy_run_followup(
        monkeypatch, main_module, raises=RuntimeError("simulated total P18 outage"),
    )

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module), (
        "la response pubblica non deve cambiare di una virgola per un guasto P18"
    )
    assert len(followup_calls) == 1, "il tentativo deve comunque avvenire"
    assert bridge_calls and bridge_calls[0][0] == 501
    assert len(pdf_calls) == 1
    assert len(emails) == 2
    assert len(whatsapp) == 1


def test_p17_stima_richiesta_still_recorded_when_followup_fails_completely(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    _spy_run_followup(monkeypatch, main_module, raises=RuntimeError("simulated total P18 outage"))

    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    event_types = [row["event_type"] for row in si_db.rows]
    assert "stima_richiesta" in event_types, "P17 deve continuare a funzionare anche se P18 fallisce completamente"


def test_salva_stima_continues_when_repository_task_creation_fails(monkeypatch):
    """Failure isolation test obbligatorio: il fallimento simulato avviene
    un livello piu' in profondita' (dentro repository.execute_followup_action,
    non nel wrapper stesso), per dimostrare che l'intera catena
    run_followup -> repository -> core_repository.create_task_with_cursor
    e' coperta da safe_run_followup, non solo la chiamata piu' esterna."""
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)

    def failing_create_task(cur, data):
        raise RuntimeError("simulated CORE task creation failure")

    monkeypatch.setattr(
        main_module.followup_service.repository.core_repository,
        "create_task_with_cursor",
        failing_create_task,
    )

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(pdf_calls) == 1
    assert len(emails) == 2
    assert len(whatsapp) == 1
    event_types = [row["event_type"] for row in si_db.rows]
    assert "stima_richiesta" in event_types


def test_salva_stima_does_not_run_temporal_scan_logic(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    followup_calls = _spy_run_followup(monkeypatch, main_module)
    temporal_calls = []

    def _temporal_scan(**kwargs):
        temporal_calls.append(kwargs)
        return {"status": "completed", "processed": 0, "escalated": 0, "skipped": 0, "failed": 0, "items": []}

    monkeypatch.setattr(main_module.followup_service, "run_temporal_escalation_scan", _temporal_scan)

    response = asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert response == expected_success_response(main_module)
    assert len(followup_calls) == 1
    assert temporal_calls == []


# --- TEST 5 & 6: never called for stima_completata / email_stima_inviata ---

def test_followup_is_never_called_for_stima_completata_or_email_stima_inviata(monkeypatch):
    """P18-A decision: NO TASK on stima_completata (redundant with the task
    already created from stima_richiesta), and none on email_stima_inviata
    either. run_followup must be called exactly once for the whole request
    - if it were also wired to those two P17 events, this count would be
    2 or 3."""
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    followup_calls = _spy_run_followup(monkeypatch, main_module)

    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    # I tre eventi P17 devono comunque essere tutti registrati (comportamento
    # P17-B2 invariato)...
    event_types = [row["event_type"] for row in si_db.rows]
    assert event_types == ["stima_richiesta", "stima_completata", "email_stima_inviata"]
    # ...ma il motore di follow-up viene invocato una sola volta in totale.
    assert len(followup_calls) == 1
    assert followup_calls[0]["rule_code"] == "FOLLOWUP_STIMA_RICHIESTA"


# --- TEST 7: no customer outbound is added by P18 ---------------------------

def test_followup_adds_no_customer_outbound(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    _spy_run_followup(monkeypatch, main_module)

    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    # Stessa baseline di prima di P18: 2 email (cliente + admin) e 1
    # WhatsApp, esattamente come nei test P17-B1/B2 - nessun invio extra
    # generato dal follow-up engine.
    assert len(emails) == 2
    assert len(whatsapp) == 1


# --- TEST 8: idempotency end-to-end through the real endpoint --------------

class FollowupFakeCursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()

        if "insert into followup_actions" in sql:
            key = params["idempotency_key"]
            existing = next((r for r in self.database.actions if r["idempotency_key"] == key), None)
            if existing is not None:
                self.rows = []
                return
            row = {
                "id": self.database.next_id, "task_id": None, "status": "pending",
                "idempotency_key": key, "error_message": None,
            }
            self.database.next_id += 1
            self.database.actions.append(row)
            self.rows = [copy.deepcopy(row)]
            return

        if "from followup_actions where idempotency_key" in sql:
            key = params[0] if not isinstance(params, dict) else params["idempotency_key"]
            match = next((r for r in self.database.actions if r["idempotency_key"] == key), None)
            self.rows = [copy.deepcopy(match)] if match else []
            return

        if sql.startswith("update followup_actions") and "completed" in sql:
            task_id, action_id = params
            for row in self.database.actions:
                if row["id"] == action_id:
                    row["status"] = "completed"
                    row["task_id"] = task_id
            self.rows = []
            return

        if "select id from tasks where metadata->>'idempotency_key'" in sql:
            key = params[0]
            match = next((t for t in self.database.tasks if t["metadata"].get("idempotency_key") == key), None)
            self.rows = [{"id": match["id"]}] if match else []
            return

        raise AssertionError(f"unexpected followup SQL in P18-C integration test: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FollowupFakeDatabase:
    def __init__(self):
        self.actions = []
        self.tasks = []
        self.next_id = 1

    @contextmanager
    def cursor(self, *, commit=False):
        yield self, FollowupFakeCursor(self)


def test_retry_with_same_stima_id_does_not_create_a_second_task(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)

    followup_db = FollowupFakeDatabase()
    monkeypatch.setattr(main_module.followup_service.repository, "followup_cursor", followup_db.cursor)

    def fake_create_task(cur, data):
        new_id = len(followup_db.tasks) + 1
        followup_db.tasks.append({"id": new_id, "metadata": data["metadata"]})
        return {"id": new_id}

    monkeypatch.setattr(
        main_module.followup_service.repository.core_repository,
        "create_task_with_cursor",
        fake_create_task,
    )

    # run_followup/safe_run_followup restano REALI (non mockati) qui: e'
    # proprio la catena reale service -> repository -> core_repository che
    # deve dimostrare l'idempotenza, chiamata due volte dal vero endpoint
    # con la stessa stima_id (stesso pattern gia' usato da
    # test_salva_stima_retry_with_same_stima_id_does_not_duplicate_the_event
    # in tests/test_seller_intelligence_p17b1_integration.py).
    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))
    asyncio.run(main_module.salva_stima(JsonRequest(base_payload())))

    assert len(followup_db.actions) == 1, "massimo una followup_action valida per la stessa idempotency key"
    assert followup_db.actions[0]["idempotency_key"] == "followup:stima_richiesta:501"
    assert followup_db.actions[0]["status"] == "completed"
    assert len(followup_db.tasks) == 1, "massimo un task CORE per quella key"
