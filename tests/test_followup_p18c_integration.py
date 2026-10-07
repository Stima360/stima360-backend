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

SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1 (contratto F04, ricevute
pubbliche). L'endpoint resta quello vero, raggiunto con l'identita'
dell'invio; il follow-up e' il passo `followup` della ricevuta
(`run_followup(..., recover=True, due_at_override=<scadenza congelata>)`),
non piu' il wrapper fail-open `safe_run_followup`. Un guasto P18 non costa la
stima, il bridge ne' P17 (che lo precedono), ma non e' piu' "fail-open" con
successo e consegne: 202 `partial` senza consegne, e la STESSA identita'
riprende dal passo `followup` senza duplicare nulla. I passi degli altri
domini (owner, immobile, watch) sono doppi che riescono.
"""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from urllib.parse import urlencode
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from integration_p2_support import import_project_module
from tests.public_submission_fakes import ReceiptStore, identity_fields, response_body


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
        # F04: identita' dell'invio. Una chiamata = una richiesta nuova; chi
        # vuole un retry riusa lo stesso dizionario.
        **identity_fields(),
    }
    payload.update(overrides)
    return payload


def submit(main_module, payload):
    """POST reale su /api/salva_stima: (HTTP status, corpo JSON della ricevuta)."""
    response = asyncio.run(main_module.salva_stima(JsonRequest(payload)))
    return response.status_code, response_body(response)


def connect(monkeypatch, main_module, connection_factory):
    """Le connessioni del doppio `stime` passano dalla tabella ricevute emulata."""
    store = ReceiptStore()
    monkeypatch.setattr(main_module, "get_connection", store.factory(connection_factory))
    return store


def install_receipt_step_doubles(monkeypatch, main_module):
    """I passi della ricevuta estranei a P17/P18 riescono; `followup` resta reale."""
    monkeypatch.setattr(main_module.owner_provisioning, "provision_for_public_stima",
                        lambda ctx, **k: {"status": "provisioned"})
    monkeypatch.setattr(main_module.property_site_sync, "sync_public_stima",
                        lambda ctx, **k: {"property_id": 71})
    monkeypatch.setattr(main_module.property_watch_service, "ensure_watch_for_stima",
                        lambda stima_id: {"watch_id": 1})
    for name, value in {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": "587", "SMTP_USER": "synthetic@example.invalid",
                        "SMTP_PASS": "synthetic-fixture"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(main_module, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:55888/fake-whatsapp-provider")


def install_pdf_email_whatsapp_mocks(monkeypatch, main_module, *, pdf_calls, emails,
                                     whatsapp, mail_result=True, accodate=None):
    """P29 cutover: `emails` raccoglie ormai SOLO l'alert amministratore.

    La mail al cliente non passa piu' da `invia_mail` - viene accodata nel
    ledger delle comunicazioni e la manda il dispatcher. `accodate` e' la spia
    di quell'accodamento; senza, va in una lista che nessuno guarda, cosi' i
    test che non se ne occupano non devono nominarla.
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
        lambda payload, nome_file: pdf_calls.append((payload, nome_file)) or b"%PDF-synthetic\n%%EOF",
    )
    from tests.private_pdf_fakes import install_private_pdf_fake
    install_private_pdf_fake(monkeypatch, main_module)
    monkeypatch.setattr(main_module, "invia_mail", lambda *args: emails.append(args) or mail_result)
    # `invia_whatsapp` reale ritorna un bool: True e' "accettato dal provider".
    monkeypatch.setattr(main_module, "invia_whatsapp", lambda *args: whatsapp.append(args) or True)
    install_receipt_step_doubles(monkeypatch, main_module)


def expected_success_response(main_module, response):
    token = str(uuid.UUID(response["token"]))
    pdf_url = f"{main_module.PUBLIC_BASE_URL.rstrip('/')}/api/stime/501/pdf?t={response['token']}"
    return {
        "success": True,
        "id": 501,
        "pdf_url": pdf_url,
        "pdf_status": "ready",
        "price_exact": 180000,
        "eur_mq_finale": 2000,
        "valore_pertinenze": 5000,
        "base_mq": 1500,
        "token": token,
        "detail_url": main_module.PUBLIC_SITE_BASE_URL + "/stima_dettagliata.html?" + urlencode({"token": token}),
        "pdf_redirect_url": main_module.PUBLIC_SITE_BASE_URL + "/pdf_redirect.html?" + urlencode({"token": token}),
        # F04: la ricevuta viaggia con la risposta.
        "receipt": response["receipt"],
    }


def assert_completed(status, body, main_module):
    assert status == 200, body
    assert body == expected_success_response(main_module, body)
    assert body["receipt"]["status"] == "completed" and body["receipt"]["stima_id"] == 501


def assert_partial_at(status, body, step, error_type="RuntimeError"):
    assert status == 202, body
    assert body["success"] is False and body["ok"] is False and body["id"] == 501
    assert body["receipt"]["status"] == "partial" and body["receipt"]["resumable"] is True
    assert body["receipt"]["steps"][step] == "failed"
    if error_type is not None:
        assert body["receipt"]["errors"][step]["error_type"] == error_type
    assert "token" not in body and "pdf_url" not in body, "nessun collegamento prima del completamento"


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
        # P26-6A: the system write path derives the event's agency from its
        # references before inserting, so the fake answers those lookups.
        if re.match(r"select agency_id from (contacts|leads|stime|properties) where id = %s", sql):
            self.rows = [{"agency_id": getattr(self.database, "agency_id", 1)}]
            return
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
    connect(monkeypatch, main_module, lambda: connection)

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

    status, body = submit(main_module, base_payload())

    assert_completed(status, body, main_module)
    assert len(followup_calls) == 1, "run_followup deve essere chiamato esattamente una volta"

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
    # F04: il passo e' ripetibile e la scadenza e' congelata sulla ricevuta.
    assert call["recover"] is True
    due_hours = main_module.followup_service.get_rule("FOLLOWUP_STIMA_RICHIESTA").due_hours
    # Congelata sulla ricevuta in forma JSON (ISO 8601): `run_followup` accetta
    # datetime o stringa ISO con fuso.
    scadenza = datetime.fromisoformat(call["due_at_override"])
    assert scadenza.tzinfo is not None
    assert scadenza == ReceiptStore().now + timedelta(hours=due_hours), "scadenza = ricezione + regola"
    assert body["receipt"]["steps"]["followup"] == "succeeded"


def test_followup_waits_for_the_bridge_and_uses_its_result_on_resume(monkeypatch):
    """Prima di F04 un bridge guasto lasciava correre il follow-up con
    contact/lead vuoti. Con F04 il bridge e' un passo che precede il
    follow-up: se fallisce, la ricevuta si ferma li' e il follow-up NON parte
    senza riferimenti; alla ripresa, bridge rientrato, parte una volta con i
    riferimenti veri."""
    main_module = import_project_module("main")
    connection = LegacyConnection(stima_id=501)
    connect(monkeypatch, main_module, lambda: connection)

    outage = {"on": True}

    def bridge(stima_id, **data):
        if outage["on"]:
            raise RuntimeError("simulated CORE bridge outage")
        return {"status": "linked", "stima_id": stima_id, "contact_id": 16, "lead_id": 12,
                "contact_created": True, "lead_created": True}

    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", bridge)

    pdf_calls, emails, whatsapp = [], [], []
    install_pdf_email_whatsapp_mocks(monkeypatch, main_module, pdf_calls=pdf_calls, emails=emails, whatsapp=whatsapp)

    si_db = SIDatabase()
    monkeypatch.setattr(main_module.seller_intelligence_service.repository, "si_cursor", si_db.cursor)

    followup_calls = _spy_run_followup(monkeypatch, main_module)
    payload = base_payload()

    status, body = submit(main_module, payload)
    assert_partial_at(status, body, "bridge")
    assert followup_calls == [], "nessun follow-up con riferimenti vuoti"
    assert si_db.rows == []

    outage["on"] = False
    assert_completed(*submit(main_module, payload), main_module)
    assert len(followup_calls) == 1
    assert followup_calls[0]["contact_id"] == 16
    assert followup_calls[0]["lead_id"] == 12
    assert followup_calls[0]["stima_id"] == 501
    assert len(pdf_calls) == 1 and len(emails) == 1 and len(whatsapp) == 1


# --- TEST 3 & 4: P18 total failure never blocks the funnel or P17 -----------

def test_followup_outage_yields_a_partial_receipt_then_the_resume_completes(monkeypatch):
    """F04: un guasto totale P18 ferma la ricevuta a `followup` dopo stima,
    bridge e P17 (202 `partial`, nessuna consegna), e la stessa identita'
    riprende da li' senza rieseguire il bridge ne' duplicare consegne."""
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    outage = {"on": True}
    followup_calls = []

    def run_followup(**kwargs):
        followup_calls.append(kwargs)
        if outage["on"]:
            raise RuntimeError("simulated total P18 outage")
        return {"task_id": 1, "followup_action_id": 1, "status": "completed"}

    monkeypatch.setattr(main_module.followup_service, "run_followup", run_followup)
    payload = base_payload()

    status, body = submit(main_module, payload)
    assert_partial_at(status, body, "followup")
    assert len(followup_calls) == 1, "il tentativo deve comunque avvenire"
    assert bridge_calls and bridge_calls[0][0] == 501
    assert body["receipt"]["steps"]["bridge"] == "succeeded"
    assert body["receipt"]["steps"]["event_requested"] == "succeeded"
    assert pdf_calls == [] and emails == [] and whatsapp == [], "nessuna consegna con la ricevuta a meta'"

    outage["on"] = False
    assert_completed(*submit(main_module, payload), main_module)
    assert len(followup_calls) == 2 and len(bridge_calls) == 1
    assert followup_calls[1]["due_at_override"] == followup_calls[0]["due_at_override"], "scadenza congelata"
    assert len(pdf_calls) == 1
    # P29 cutover: UN solo invio diretto, ed e' l'alert amministratore.
    assert len(emails) == 1
    assert len(whatsapp) == 1


def test_p17_stima_richiesta_still_recorded_when_followup_fails_completely(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    _spy_run_followup(monkeypatch, main_module, raises=RuntimeError("simulated total P18 outage"))

    status, body = submit(main_module, base_payload())
    assert_partial_at(status, body, "followup")

    event_types = [row["event_type"] for row in si_db.rows]
    assert "stima_richiesta" in event_types, "P17 deve continuare a funzionare anche se P18 fallisce completamente"


def test_salva_stima_continues_when_repository_task_creation_fails(monkeypatch):
    """Failure isolation test obbligatorio: il fallimento simulato avviene
    un livello piu' in profondita' (dentro repository.execute_followup_action,
    non nel wrapper stesso), per dimostrare che l'intera catena
    run_followup -> repository -> core_repository.create_task_with_cursor
    e' coperta dal passo `followup` della ricevuta (F04), non solo la chiamata
    piu' esterna: stima, bridge e P17 restano, nessuna consegna a meta'."""
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)

    def failing_create_task(cur, data):
        raise RuntimeError("simulated CORE task creation failure")

    monkeypatch.setattr(
        main_module.followup_service.repository.core_repository,
        "create_task_with_cursor",
        failing_create_task,
    )

    status, body = submit(main_module, base_payload())

    assert_partial_at(status, body, "followup", error_type=None)
    assert pdf_calls == [] and emails == [] and whatsapp == []
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

    assert_completed(*submit(main_module, base_payload()), main_module)
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

    assert_completed(*submit(main_module, base_payload()), main_module)

    # P29 cutover: dal producer nascono DUE eventi, non tre.
    #
    # `email_stima_inviata` non e' scomparso e non ha cambiato significato -
    # significa ancora "la mail al cliente e' partita" - ma quel fatto adesso
    # accade altrove e piu' tardi, alla finalizzazione `sent` del messaggio.
    # Vederlo qui vorrebbe dire che qualcuno lo scrive all'accodamento, cioe'
    # che dice "e' partita" quando e' solo in coda.
    event_types = [row["event_type"] for row in si_db.rows]
    assert event_types == ["stima_richiesta", "stima_completata"]
    assert "email_stima_inviata" not in event_types, (
        "l'evento e' tornato nel producer: accodare non e' aver mandato")
    # ...e il motore di follow-up viene invocato una sola volta in totale.
    assert len(followup_calls) == 1
    assert followup_calls[0]["rule_code"] == "FOLLOWUP_STIMA_RICHIESTA"


# --- TEST 7: no customer outbound is added by P18 ---------------------------

def test_followup_adds_no_customer_outbound(monkeypatch):
    main_module, si_db, pdf_calls, emails, whatsapp, bridge_calls = _setup_happy_path(monkeypatch)
    _spy_run_followup(monkeypatch, main_module)

    assert_completed(*submit(main_module, base_payload()), main_module)

    # Stessa baseline dei test P17-B1/B2, aggiornata al cutover P29: UN invio
    # diretto (l'alert amministratore) e 1 WhatsApp. Cio' che questo test prova
    # resta identico - il follow-up engine non genera invii extra - e il
    # conteggio piu' basso lo rende anzi piu' stretto: se il cutover tornasse
    # indietro, o se il motore mandasse qualcosa, il numero cambierebbe.
    assert len(emails) == 1
    assert len(whatsapp) == 1


# --- TEST 8: idempotency end-to-end through the real endpoint --------------

class FollowupFakeCursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()

        if sql.startswith("select agency_id from ") and "for share" in sql:
            self.rows = [{"agency_id": 1}]
            return

        if "insert into followup_actions" in sql:
            key = params["idempotency_key"]
            existing = next((r for r in self.database.actions if r["idempotency_key"] == key), None)
            if existing is not None:
                self.rows = []
                return
            row = {
                "id": self.database.next_id, "task_id": None, "status": "pending",
                "idempotency_key": key, "error_message": None,
                "agency_id": params["agency_id"],
                # F04 (`recover=True`): la ripresa confronta i riferimenti
                # originali dell'azione, come la riga vera li conserva.
                **{k: params.get(k) for k in ("rule_code", "trigger_type", "contact_id", "lead_id", "stima_id")},
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

        if "from followup_actions where id =" in sql and "for update" in sql:
            match = next((r for r in self.database.actions if r["id"] == params[0]), None)
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
    #
    # F04: la stessa identita' due volte e' un solo giro di pipeline (la
    # seconda risposta e' la ricevuta completata); una identita' NUOVA che il
    # doppio fa finire sulla stessa `stima_id` e' il caso in cui la chiave del
    # follow-up deve fare il suo lavoro da sola.
    payload = base_payload()
    assert_completed(*submit(main_module, payload), main_module)
    assert_completed(*submit(main_module, payload), main_module)
    assert_completed(*submit(main_module, base_payload()), main_module)

    assert len(followup_db.actions) == 1, "massimo una followup_action valida per la stessa idempotency key"
    assert followup_db.actions[0]["idempotency_key"] == "followup:stima_richiesta:501"
    assert followup_db.actions[0]["status"] == "completed"
    assert len(followup_db.tasks) == 1, "massimo un task CORE per quella key"
