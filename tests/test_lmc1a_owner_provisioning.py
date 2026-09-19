"""LMC-1A - il collegamento automatico PRE-INCARICO fra owner e stima.

Senza database: i doppi qui provano le DECISIONI del servizio - quando si
procede e quando no, che cosa viene passato al repository, che il wrapper
`safe_*` non lascia uscire niente verso il funnel pubblico, e che `main.py`
chiama il wrapper nel punto giusto e in modo fail-open.

Le cose che solo un database sa dire - la UNIQUE, il trigger, l'idempotenza
reale su due esecuzioni, la migration up/down - stanno in
tests/test_lmc1a_owner_provisioning_postgres.py.

Mappa:
    A  i guardiani: bridge assente, conflict, skipped, contact_id mancante
    B  il contesto: solo un SystemAgencyContext public_stima, tenant coerente
    C  safe_*: niente esce
    D  il funnel: main.py chiama il wrapper dopo il bridge, e un errore owner
       non tocca la risposta
    E  la migration 066 e' conforme al runner
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
from pathlib import Path

import pytest

from operator_auth.context import OperatorContext, SystemAgencyContext
from owner import provisioning
from owner import repository as owner_repository

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
VERSION = "066_lmc1_owner_stima_access"


def ctx(agency_id: int = 7) -> SystemAgencyContext:
    return SystemAgencyContext(agency_id=agency_id, origin="public_stima")


def linked(contact_id=31, lead_id=41, status="linked") -> dict:
    return {"status": status, "stima_id": 501, "contact_id": contact_id, "lead_id": lead_id,
            "contact_created": status == "linked", "lead_created": status == "linked"}


@pytest.fixture
def repository_spy(monkeypatch):
    calls = []

    def fake(agency_id, *, contact_id, stima_id, granted_by):
        calls.append({"agency_id": agency_id, "contact_id": contact_id,
                      "stima_id": stima_id, "granted_by": granted_by})
        return {"status": "provisioned", "owner_account_id": 9, "access_id": 3,
                "account_created": True, "access_created": True}

    monkeypatch.setattr(owner_repository, "provision_stima_access", fake)
    return calls


# ---------------------------------------------------------------------------
# A - i guardiani
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bridge_result, reason", [
    (None, "no_bridge_result"),
    ({"status": "conflict", "stima_id": 501, "reason": "identity_conflict"}, "bridge_conflict"),
    ({"status": "skipped", "stima_id": 501, "reason": "archived_contact", "contact_id": 31}, "bridge_skipped"),
    ({"status": "linked", "stima_id": 501, "contact_id": None, "lead_id": 41}, "no_contact_id"),
])
def test_a1_senza_un_collegamento_valido_non_si_scrive_niente(repository_spy, bridge_result, reason):
    esito = provisioning.provision_for_public_stima(ctx(), stima_id=501, bridge_result=bridge_result)
    assert esito["status"] == "skipped"
    assert esito["reason"] == reason
    assert repository_spy == []


@pytest.mark.parametrize("status", ["linked", "already_linked"])
def test_a2_linked_e_already_linked_provisionano(repository_spy, status):
    esito = provisioning.provision_for_public_stima(
        ctx(7), stima_id=501, bridge_result=linked(status=status))
    assert esito["status"] == "provisioned"
    assert repository_spy == [{
        "agency_id": 7, "contact_id": 31, "stima_id": 501,
        "granted_by": provisioning.PROVISIONING_ACTOR,
    }]


def test_a3_l_attore_tecnico_e_riconoscibile():
    assert provisioning.PROVISIONING_ACTOR == "LMC_PROVISIONING"
    assert provisioning.PROVISIONABLE_BRIDGE_STATUSES == frozenset({"linked", "already_linked"})


# ---------------------------------------------------------------------------
# B - il contesto
# ---------------------------------------------------------------------------

def test_b1_un_operator_context_e_rifiutato_prima_di_toccare_il_repository(repository_spy):
    operatore = OperatorContext(user_id=1, agency_id=7, role="admin", is_platform_admin=False,
                                session_id=1, auth_channel="session")
    with pytest.raises(provisioning.ProgrammingError):
        provisioning.provision_for_public_stima(operatore, stima_id=501, bridge_result=linked())
    assert repository_spy == []


def test_b2_un_contesto_di_sistema_di_altra_origine_e_rifiutato(repository_spy):
    dispatch = SystemAgencyContext(agency_id=7, origin="communication_dispatch")
    with pytest.raises(provisioning.ProgrammingError):
        provisioning.provision_for_public_stima(dispatch, stima_id=501, bridge_result=linked())
    assert repository_spy == []


def test_b3_l_agenzia_arriva_dal_contesto_e_da_nient_altro(repository_spy):
    provisioning.provision_for_public_stima(ctx(12), stima_id=501, bridge_result=linked())
    assert repository_spy[0]["agency_id"] == 12


def test_b4_il_servizio_non_accetta_un_agency_id_esplicito():
    import inspect
    firma = inspect.signature(provisioning.provision_for_public_stima)
    assert "agency_id" not in firma.parameters
    firma = inspect.signature(provisioning.safe_provision_for_public_stima)
    assert "agency_id" not in firma.parameters


# ---------------------------------------------------------------------------
# C - safe_*: niente esce
# ---------------------------------------------------------------------------

def test_c1_safe_inghiotte_un_errore_del_repository(monkeypatch, caplog):
    def esplode(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(owner_repository, "provision_stima_access", esplode)
    with caplog.at_level(logging.ERROR, logger="owner.provisioning"):
        esito = provisioning.safe_provision_for_public_stima(
            ctx(), stima_id=501, bridge_result=linked())
    assert esito is None
    assert any("owner_provisioning_failed" in r.getMessage() for r in caplog.records)
    assert any("stima_id=501" in r.getMessage() for r in caplog.records)


def test_c2_safe_inghiotte_anche_un_errore_di_programmazione(monkeypatch):
    operatore = OperatorContext(user_id=1, agency_id=7, role="admin", is_platform_admin=False,
                                session_id=1, auth_channel="session")
    assert provisioning.safe_provision_for_public_stima(
        operatore, stima_id=501, bridge_result=linked()) is None


def test_c3_safe_restituisce_l_esito_quando_va_bene(repository_spy):
    esito = provisioning.safe_provision_for_public_stima(ctx(), stima_id=501, bridge_result=linked())
    assert esito["status"] == "provisioned"
    assert esito["owner_account_id"] == 9


def test_c4_safe_non_logga_errori_per_uno_skip_ordinario(repository_spy, caplog):
    with caplog.at_level(logging.INFO, logger="owner.provisioning"):
        esito = provisioning.safe_provision_for_public_stima(
            ctx(), stima_id=501,
            bridge_result={"status": "conflict", "stima_id": 501, "reason": "identity_conflict"})
    assert esito["status"] == "skipped"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# D - il funnel
# ---------------------------------------------------------------------------

def _main_source() -> str:
    return (ROOT / "main.py").read_text(encoding="utf-8")


def test_d1_main_chiama_solo_il_wrapper_safe_e_dopo_il_bridge():
    source = _main_source()
    handler = source[source.index('@app.post("/api/salva_stima")'):]
    handler = handler[: handler.index("@app.", 10)]
    assert "owner_provisioning.safe_provision_for_public_stima(" in handler
    assert "provision_for_public_stima(" not in handler.replace(
        "safe_provision_for_public_stima(", "")
    bridge = handler.index("bridge_result = core_service.bridge_public_stima")
    hook = handler.index("owner_provisioning.safe_provision_for_public_stima(")
    p17 = handler.index("safe_record_event(")
    assert bridge < hook < p17, "il provisioning sta fra il bridge e gli eventi P17"


def test_d2_main_non_passa_un_agency_id_e_non_ramifica_sull_esito():
    source = _main_source()
    call = source[source.index("owner_provisioning.safe_provision_for_public_stima("):]
    call = call[: call.index(")") + 1]
    assert "agency_id" not in call, call
    assert "bridge_ctx" in call and "bridge_result=bridge_result" in call and "stima_id=new_id" in call
    handler = source[source.index('@app.post("/api/salva_stima")'):]
    assert "provision_result[" not in handler and "provisioning_result[" not in handler


def test_d3_nessuna_logica_owner_dentro_main():
    """Guarda il codice, non i commenti: il commento sopra la chiamata nomina
    le tabelle di proposito, per dire dove sta la logica."""
    source = _main_source()
    handler = source[source.index('@app.post("/api/salva_stima")'):]
    handler = handler[: handler.index("@app.", 10)]
    codice = "\n".join(l for l in handler.splitlines() if not l.strip().startswith("#"))
    assert "owner_accounts" not in codice
    assert "owner_stima_access" not in codice
    assert "INSERT INTO owner" not in codice
    assert "owner_repository" not in codice and "owner.repository" not in codice


def _import_main():
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("main")


def _run_funnel(monkeypatch, main_module, *, bridge_result, provisioning_impl):
    """Il funnel fino a `compute_from_payload`, che qui esplode di proposito:
    tutto cio' che sta PRIMA - stima, bridge, provisioning, P17, P18 - deve
    essere gia' passato. Stessi doppi di tests/test_p20_property_watch.py."""
    calls = []

    class Request:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"comune": "Alba Adriatica", "microzona": "Centro", "mq": 90,
                    "nome": "Mario", "email": "mario@example.com",
                    "telefono": "+39 333 123 4567", "prezzo_mq_base": 1500}

    class Cursor:
        def __init__(self, connessione, dict_rows=False):
            self.connessione = connessione
            self.dict_rows = dict_rows

        def execute(self, query, _params=None):
            self.senza_righe = "network_territory_aliases" in query
            if "insert into stime" in query.lower():
                self.connessione.agenzia_incisa = _params[-1] if _params else None
            self.legge_la_stima = "FROM stime WHERE id" in query

        def fetchone(self):
            if getattr(self, "senza_righe", False):
                return None
            if getattr(self, "legge_la_stima", False):
                incisa = getattr(self.connessione, "agenzia_incisa", None)
                return None if incisa is None else {"agency_id": incisa}
            return {"id": 1} if self.dict_rows else (501,)

        def close(self):
            pass

    class Connection:
        def cursor(self, **kwargs):
            return Cursor(self, dict_rows="cursor_factory" in kwargs)

        def commit(self):
            pass

        def close(self):
            pass

    def spy(bridge_ctx, *, stima_id, bridge_result):
        calls.append({"ctx": bridge_ctx, "stima_id": stima_id, "bridge_result": bridge_result})
        return provisioning_impl(bridge_ctx, stima_id=stima_id, bridge_result=bridge_result)

    monkeypatch.setattr(main_module, "get_connection", Connection)
    monkeypatch.setattr(main_module.core_service, "bridge_public_stima",
                        lambda *_a, **_k: bridge_result)
    monkeypatch.setattr(main_module.owner_provisioning, "safe_provision_for_public_stima", spy)
    monkeypatch.setattr(main_module.seller_intelligence_service, "safe_record_event",
                        lambda **_k: None)
    monkeypatch.setattr(main_module.followup_service, "safe_run_followup", lambda **_k: None)
    monkeypatch.setattr(main_module, "compute_from_payload",
                        lambda _p: (_ for _ in ()).throw(RuntimeError("calculation failed")))

    with pytest.raises(RuntimeError, match="calculation failed"):
        asyncio.run(main_module.salva_stima(Request()))
    return calls


def test_d4_il_funnel_consegna_al_wrapper_il_contesto_letto_dalla_stima(monkeypatch):
    main_module = _import_main()
    bridge = {"status": "linked", "stima_id": 501, "contact_id": 31, "lead_id": 41}
    calls = _run_funnel(monkeypatch, main_module, bridge_result=bridge,
                        provisioning_impl=lambda *_a, **_k: {"status": "provisioned"})
    assert len(calls) == 1
    assert calls[0]["stima_id"] == 501
    assert calls[0]["bridge_result"] is bridge
    assert type(calls[0]["ctx"]) is SystemAgencyContext
    assert calls[0]["ctx"].origin == "public_stima"
    assert calls[0]["ctx"].agency_id == 1


def test_d5_un_bridge_fallito_arriva_al_wrapper_come_none(monkeypatch):
    """Il doppio del bridge risponde None: in `main.py` il log dell'esito
    esplode dentro il `try` e `bridge_result` resta None - lo stesso stato del
    percorso d'errore reale. Il wrapper deve riceverlo cosi', non un dict."""
    main_module = _import_main()
    calls = _run_funnel(monkeypatch, main_module, bridge_result=None,
                        provisioning_impl=lambda _c, *, stima_id, bridge_result: None)
    assert len(calls) == 1
    assert calls[0]["bridge_result"] is None


def test_d6_un_errore_owner_non_ferma_il_funnel(monkeypatch):
    """Il wrapper vero, con il repository che esplode: il funnel prosegue fino
    a `compute_from_payload` come se niente fosse."""
    main_module = _import_main()

    def esplode(*_a, **_k):
        raise RuntimeError("owner database is down")

    monkeypatch.setattr(owner_repository, "provision_stima_access", esplode)
    bridge = {"status": "linked", "stima_id": 501, "contact_id": 31, "lead_id": 41}
    calls = _run_funnel(monkeypatch, main_module, bridge_result=bridge,
                        provisioning_impl=provisioning.safe_provision_for_public_stima)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# E - la migration 066 e' conforme al runner
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runner():
    from scripts import p26_migrate
    return p26_migrate


def test_e1_la_066_esiste_con_il_proprio_down():
    assert (MIGRATIONS / f"{VERSION}.sql").is_file()
    assert (MIGRATIONS / f"{VERSION}_down.sql").is_file()


def test_e2_il_runner_non_solleva_violazioni_e_la_serie_resta_valida(runner):
    scoperte = {m.version: m for m in runner.discover_migrations()}
    assert VERSION in scoperte
    assert runner.validate_migration(scoperte[VERSION]) == []
    violazioni = []
    for migration in runner.discover_migrations():
        violazioni.extend(runner.validate_migration(migration))
    assert violazioni == []
    numeri = [m.number for m in runner.discover_migrations()]
    assert numeri[-1] == 66 and numeri[-2] == 65


def test_e3_la_up_e_runner_owned_e_non_tocca_il_ledger(runner):
    sql = runner.strip_sql_comments((MIGRATIONS / f"{VERSION}.sql").read_text(encoding="utf-8"))
    assert not re.search(r"^\s*BEGIN\s*;", sql, re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", sql, re.MULTILINE)
    assert "schema_migrations" not in sql.lower()
    assert not re.search(r"\bCONCURRENTLY\b", sql, re.I)


def test_e4_la_up_crea_la_tabella_senza_agency_id_e_con_il_trigger(runner):
    sql = runner.strip_sql_comments((MIGRATIONS / f"{VERSION}.sql").read_text(encoding="utf-8"))
    tabella = sql[sql.index("CREATE TABLE IF NOT EXISTS owner_stima_access"):]
    tabella = tabella[: tabella.index(");") + 2]
    assert "agency_id" not in tabella, "la tenancy e' derivata, non fisica"
    for colonna in ("owner_account_id", "stima_id", "access_role", "access_status", "is_primary",
                    "valid_from", "valid_until", "created_at", "updated_at", "revoked_at", "granted_by"):
        assert re.search(rf"^\s*{colonna}\s", tabella, re.MULTILINE), colonna
    assert "UNIQUE (owner_account_id, stima_id)" in tabella
    assert "REFERENCES owner_accounts(id) ON DELETE CASCADE" in tabella
    assert "REFERENCES stime(id) ON DELETE CASCADE" in tabella
    assert "CREATE OR REPLACE FUNCTION owner_stima_access_agency_integrity()" in sql
    assert "CREATE TRIGGER trg_owner_stima_access_agency_integrity" in sql
    assert re.search(r"BEFORE INSERT OR UPDATE OF owner_account_id, stima_id\s+ON owner_stima_access", sql)


def test_e5_il_down_si_bracketta_libera_il_ledger_e_rimuove_tutto(runner):
    sql = runner.strip_sql_comments((MIGRATIONS / f"{VERSION}_down.sql").read_text(encoding="utf-8"))
    assert re.search(r"^\s*BEGIN\s*;", sql, re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", sql, re.MULTILINE)
    assert f"DELETE FROM schema_migrations WHERE version = '{VERSION}'" in sql
    assert "DROP TRIGGER IF EXISTS trg_owner_stima_access_agency_integrity ON owner_stima_access" in sql
    assert "DROP FUNCTION IF EXISTS owner_stima_access_agency_integrity()" in sql
    assert "DROP TABLE IF EXISTS owner_stima_access" in sql
    assert "CASCADE" not in sql.upper().replace("ON DELETE CASCADE", "")


def test_e6_la_066_non_tocca_altre_tabelle(runner):
    sql = runner.strip_sql_comments((MIGRATIONS / f"{VERSION}.sql").read_text(encoding="utf-8"))
    for tabella in ("owner_accounts", "owner_property_access", "stime", "contacts", "properties",
                    "communication_messages", "seller_timeline_events", "property_watches"):
        assert not re.search(rf"ALTER TABLE\s+{tabella}\b", sql), tabella
        assert not re.search(rf"CREATE TABLE\s+(IF NOT EXISTS\s+)?{tabella}\s*\(", sql), tabella
    # Nessuna riga scritta o tolta: `ON DELETE CASCADE` e' una clausola di FK,
    # non uno statement, e `UPDATE OF` e' l'evento del trigger.
    corpo = re.sub(r"\$do\$.*?\$do\$", "", sql, flags=re.S)
    assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE)\b", corpo, re.I)
