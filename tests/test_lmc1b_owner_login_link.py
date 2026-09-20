"""LMC-1B - il magic link di accesso a "La Mia Casa", senza database.

Qui si provano le DECISIONI: quando si emette un token e quando no, che cosa
finisce nel ledger, che la risposta HTTP sia la stessa in ogni caso, e che il
raw token non compaia dove non deve. Le cose che solo un database sa dire - il
CHECK del reason code, la tenancy, il rate limit su righe vere, la migration -
stanno in tests/test_lmc1b_owner_login_link_postgres.py.

Mappa:
    A  la route: risposta neutra, sempre, e nessun dato in uscita
    B  il servizio: lookup, multi-agenzia, rate limit, failure
    C  il contesto di sistema `owner_login`
    D  l'email: cosa contiene e cosa NON contiene
    E  P29: reason code, enum e migration 067
    F  il perimetro: cosa LMC-1B non tocca
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from communication import enums as communication_enums
from operator_auth.context import SystemAgencyContext
from owner import login_email, login_service
from owner import repository as owner_repository
from owner.router_portal import router as portal_router

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSION = "067_lmc1b_owner_login_reason"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(portal_router)
    return TestClient(app)


class Candidato(dict):
    """Una riga di `find_login_candidates`, nella forma che il repository rende."""

    def __init__(self, owner_account_id=9, contact_id=31, agency_id=7,
                 email="mario@example.it", display_name="Mario Rossi"):
        super().__init__(owner_account_id=owner_account_id, contact_id=contact_id,
                         agency_id=agency_id, email=email, display_name=display_name)


@pytest.fixture
def mondo(monkeypatch):
    """Il servizio con repository e ledger sostituiti da spie."""
    stato = {"candidati": [Candidato()], "token": [], "enqueue": [], "audit": [],
             "rate_limited": set(), "enqueue_esplode": False, "lookup_esplode": False}

    def find_login_candidates(email_normalized):
        stato["lookup_email"] = email_normalized
        if stato["lookup_esplode"]:
            raise RuntimeError("database down")
        return list(stato["candidati"])

    def issue_login_token_with_cursor(cur, *, owner_account_id, agency_id, minutes, created_by,
                                      max_recent=3, window_minutes=15):
        stato["limiti"] = (max_recent, window_minutes)
        if owner_account_id in stato["rate_limited"]:
            return None, None
        raw = f"raw-token-{owner_account_id}-{len(stato['token'])}"
        riga = {"id": 100 + len(stato["token"]), "owner_account_id": owner_account_id,
                "token_type": "login", "expires_at": "2026-01-01T00:30:00Z"}
        stato["token"].append({"owner_account_id": owner_account_id, "agency_id": agency_id,
                               "minutes": minutes, "created_by": created_by, "raw": raw,
                               "row": riga})
        return riga, raw

    def enqueue(ctx, **kwargs):
        if stato["enqueue_esplode"]:
            raise RuntimeError("ledger down")
        stato["enqueue"].append({"ctx": ctx, **kwargs})
        return {"message": {"id": 1}, "created": True}

    class Cursore:
        def execute(self, *_a, **_k):
            pass

    class Contesto:
        def __enter__(self):
            return None, Cursore()

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(owner_repository, "find_login_candidates", find_login_candidates)
    monkeypatch.setattr(owner_repository, "issue_login_token_with_cursor",
                        issue_login_token_with_cursor)
    monkeypatch.setattr(login_service, "core_cursor", lambda **_k: Contesto())
    monkeypatch.setattr(login_service.communication_service, "enqueue", enqueue)
    monkeypatch.setattr(login_service.owner_repository, "audit_with_cursor",
                        lambda cur, *a, **k: stato["audit"].append((a, k)))
    return stato


# ---------------------------------------------------------------------------
# A - la route
# ---------------------------------------------------------------------------

def test_a1_la_route_esiste_ed_e_pubblica(client, mondo):
    risposta = client.post("/api/owner/portal/auth/request-link",
                           json={"email": "mario@example.it"})
    assert risposta.status_code == 204
    assert risposta.content == b""


def test_a2_la_risposta_e_identica_per_email_presente_e_assente(client, mondo):
    presente = client.post("/api/owner/portal/auth/request-link",
                           json={"email": "mario@example.it"})
    mondo["candidati"] = []
    assente = client.post("/api/owner/portal/auth/request-link",
                          json={"email": "nessuno@example.it"})
    assert presente.status_code == assente.status_code == 204
    assert presente.content == assente.content == b""
    assert dict(presente.headers) .get("content-length") == dict(assente.headers).get("content-length")


@pytest.mark.parametrize("caso", ["nessun_candidato", "rate_limited", "enqueue_rotto", "lookup_rotto"])
def test_a3_ogni_esito_interno_da_la_stessa_risposta(client, mondo, caso):
    if caso == "nessun_candidato":
        mondo["candidati"] = []
    elif caso == "rate_limited":
        mondo["rate_limited"] = {9}
    elif caso == "enqueue_rotto":
        mondo["enqueue_esplode"] = True
    elif caso == "lookup_rotto":
        mondo["lookup_esplode"] = True

    risposta = client.post("/api/owner/portal/auth/request-link",
                           json={"email": "mario@example.it"})
    assert risposta.status_code == 204
    assert risposta.content == b""


def test_a4_un_email_malformata_non_distingue_nulla(client, mondo):
    for valore in ("", "   ", "non-una-email", "a" * 400):
        risposta = client.post("/api/owner/portal/auth/request-link", json={"email": valore})
        assert risposta.status_code in (204, 422), valore
        if risposta.status_code == 204:
            assert risposta.content == b""


def test_a5_la_route_non_prende_niente_dal_client_oltre_l_email():
    import ast
    tree = ast.parse((ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8"))
    funzione = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "request_link")
    nomi = {a.arg for a in funzione.args.args}
    assert not nomi & {"agency_id", "owner_account_id", "account_id", "contact_id", "token"}


def test_a6_il_router_non_contiene_logica(client, mondo):
    import inspect
    from owner import router_portal
    corpo = inspect.getsource(router_portal.request_link)
    for vietato in ("SELECT", "INSERT", "enqueue", "hash_secret", "owner_access_tokens"):
        assert vietato not in corpo, vietato


# ---------------------------------------------------------------------------
# B - il servizio
# ---------------------------------------------------------------------------

def test_b1_l_email_viene_normalizzata_prima_del_lookup(mondo):
    login_service.request_login_link("  Mario@Example.IT  ")
    assert mondo["lookup_email"] == "mario@example.it"


def test_b2_un_candidato_produce_un_token_e_un_messaggio(mondo):
    esito = login_service.request_login_link("mario@example.it")
    assert len(mondo["token"]) == 1
    assert mondo["token"][0]["owner_account_id"] == 9
    assert mondo["token"][0]["minutes"] == login_service.TOKEN_TTL_MINUTES == 30
    assert mondo["token"][0]["created_by"] == login_service.LOGIN_ACTOR
    assert len(mondo["enqueue"]) == 1
    assert esito["requested"] == 1 and esito["sent"] == 1


def test_b3_il_messaggio_ha_la_forma_richiesta_dal_design(mondo):
    login_service.request_login_link("mario@example.it")
    messaggio = mondo["enqueue"][0]
    assert messaggio["channel"] == "email"
    assert messaggio["communication_type"] == "service"
    assert messaggio["mode"] == "automatic"
    assert messaggio["reason_code"] == "owner_login_link"
    assert messaggio["template_key"] == "owner_login_link"
    assert messaggio["template_version"] == 1
    assert messaggio["contact_id"] == 31
    assert messaggio["destination_snapshot"] == "mario@example.it"
    assert messaggio["idempotency_key"] == "owner_login_link:100"
    assert messaggio["subject_snapshot"]
    assert messaggio["cur"] is not None, "token e messaggio nella stessa transazione"


def test_b4_il_raw_token_viaggia_solo_nel_corpo_dell_email(mondo):
    login_service.request_login_link("mario@example.it")
    messaggio = mondo["enqueue"][0]
    raw = mondo["token"][0]["raw"]
    assert raw in messaggio["rendered_body"]
    assert raw not in str(messaggio.get("metadata") or {})
    assert raw not in messaggio["subject_snapshot"]
    assert raw not in messaggio["idempotency_key"]


def test_b5_due_agenzie_due_token_e_due_messaggi_separati(mondo):
    mondo["candidati"] = [Candidato(owner_account_id=9, contact_id=31, agency_id=7),
                          Candidato(owner_account_id=10, contact_id=32, agency_id=8)]
    esito = login_service.request_login_link("mario@example.it")

    assert esito["requested"] == 2 and esito["sent"] == 2
    assert [t["owner_account_id"] for t in mondo["token"]] == [9, 10]
    assert [m["ctx"].agency_id for m in mondo["enqueue"]] == [7, 8]
    assert [m["contact_id"] for m in mondo["enqueue"]] == [31, 32]
    # Nessun incrocio: il link di un'agenzia non finisce nel messaggio dell'altra.
    assert mondo["token"][0]["raw"] in mondo["enqueue"][0]["rendered_body"]
    assert mondo["token"][0]["raw"] not in mondo["enqueue"][1]["rendered_body"]
    assert mondo["token"][1]["raw"] in mondo["enqueue"][1]["rendered_body"]


def test_b6_il_rate_limit_ferma_solo_l_account_che_lo_ha_raggiunto(mondo):
    mondo["candidati"] = [Candidato(owner_account_id=9, agency_id=7),
                          Candidato(owner_account_id=10, contact_id=32, agency_id=8)]
    mondo["rate_limited"] = {9}
    esito = login_service.request_login_link("mario@example.it")

    assert [t["owner_account_id"] for t in mondo["token"]] == [10]
    assert len(mondo["enqueue"]) == 1
    assert esito["requested"] == 2 and esito["sent"] == 1 and esito["rate_limited"] == 1


def test_b7_un_enqueue_fallito_non_ferma_gli_altri_account(mondo, caplog):
    mondo["candidati"] = [Candidato(owner_account_id=9, agency_id=7),
                          Candidato(owner_account_id=10, contact_id=32, agency_id=8)]
    chiamate = {"n": 0}
    originale = login_service.communication_service.enqueue

    def a_volte_esplode(ctx, **kwargs):
        chiamate["n"] += 1
        if chiamate["n"] == 1:
            raise RuntimeError("ledger down")
        return originale(ctx, **kwargs)

    login_service.communication_service.enqueue = a_volte_esplode
    try:
        with caplog.at_level(logging.ERROR, logger="owner.login_service"):
            esito = login_service.request_login_link("mario@example.it")
    finally:
        login_service.communication_service.enqueue = originale

    assert esito["sent"] == 1 and esito["failed"] == 1
    assert any("owner_login_link_failed" in r.getMessage() for r in caplog.records)
    assert not any("mario@example.it" in r.getMessage() for r in caplog.records), \
        "nessun indirizzo nel log"


def test_b8_safe_non_solleva_mai(monkeypatch):
    def esplode(_email):
        raise RuntimeError("tutto rotto")

    monkeypatch.setattr(login_service, "request_login_link", esplode)
    assert login_service.safe_request_login_link("mario@example.it") is None


def test_b9_un_email_vuota_non_arriva_nemmeno_al_lookup(mondo):
    for valore in (None, "", "   "):
        esito = login_service.request_login_link(valore)
        assert esito["requested"] == 0
    assert mondo["token"] == [] and mondo["enqueue"] == []


def test_b10_il_servizio_non_accetta_un_agency_id_dal_chiamante():
    import inspect
    for funzione in (login_service.request_login_link, login_service.safe_request_login_link):
        parametri = inspect.signature(funzione).parameters
        assert set(parametri) == {"email"}, funzione.__name__


# ---------------------------------------------------------------------------
# C - il contesto di sistema
# ---------------------------------------------------------------------------

def test_c1_owner_login_e_un_origin_ammesso():
    from operator_auth import context
    assert "owner_login" in context.SYSTEM_CONTEXT_ORIGINS
    ctx = SystemAgencyContext(agency_id=7, origin="owner_login")
    assert ctx.require_agency() == 7
    assert ctx.user_id is None and ctx.role is None and ctx.is_platform_admin is False


def test_c2_l_insieme_degli_origin_resta_chiuso():
    from operator_auth import context
    assert context.SYSTEM_CONTEXT_ORIGINS == (
        "public_stima", "communication_dispatch", "owner_login")
    with pytest.raises(ValueError):
        SystemAgencyContext(agency_id=1, origin="owner_portal")
    with pytest.raises(ValueError):
        SystemAgencyContext(agency_id=1, origin="qualunque_altra")


def test_c3_il_contesto_lo_costruisce_il_servizio_con_l_agenzia_del_candidato(mondo):
    mondo["candidati"] = [Candidato(owner_account_id=9, agency_id=12)]
    login_service.request_login_link("mario@example.it")
    ctx = mondo["enqueue"][0]["ctx"]
    assert type(ctx) is SystemAgencyContext
    assert ctx.origin == login_service.LOGIN_ORIGIN == "owner_login"
    assert ctx.agency_id == 12


def test_c4_il_nuovo_origin_non_entra_nel_bridge_del_funnel_pubblico():
    """`bridge_public_stima` continua ad ammettere solo `public_stima`."""
    from core import repository as core_repository
    from core.scope import ProgrammingError

    ctx = SystemAgencyContext(agency_id=7, origin="owner_login")
    with pytest.raises(ProgrammingError):
        core_repository.bridge_public_stima(1, {}, {}, "related", system_ctx=ctx)


def test_c5_il_nuovo_origin_non_e_quello_del_dispatcher():
    from communication import dispatcher
    assert dispatcher.DISPATCH_ORIGIN == "communication_dispatch"
    assert dispatcher.DISPATCH_ORIGIN != login_service.LOGIN_ORIGIN


# ---------------------------------------------------------------------------
# D - l'email
# ---------------------------------------------------------------------------

def test_d1_l_email_porta_il_link_e_la_scadenza():
    oggetto, corpo = login_email.render(token="RAW123", minutes=30)
    assert oggetto and "La Mia Casa" in oggetto
    assert f"/owner/?token=RAW123" in corpo
    assert "30" in corpo
    assert "ignora" in corpo.lower()


def test_d2_l_email_non_contiene_dati_della_stima():
    _oggetto, corpo = login_email.render(token="RAW123", minutes=30)
    for vietato in ("prezzo", "valore", "€", "stima_id", "microzona", "buyer", "mq"):
        assert vietato.lower() not in corpo.lower(), vietato


def test_d3_il_link_usa_la_base_pubblica_e_non_stime_token(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://esempio.test")
    import importlib
    modulo = importlib.reload(login_email)
    try:
        _oggetto, corpo = modulo.render(token="RAW123", minutes=30)
        assert "https://esempio.test/owner/?token=RAW123" in corpo
        assert "stima_dettagliata" not in corpo and "dati_personali" not in corpo
    finally:
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        importlib.reload(login_email)


def test_d4_il_token_e_percent_encoded_nel_link():
    _oggetto, corpo = login_email.render(token="a b&c", minutes=30)
    assert "a b&c" not in corpo
    assert "a%20b%26c" in corpo or "a+b%26c" in corpo


# ---------------------------------------------------------------------------
# E - P29
# ---------------------------------------------------------------------------

def test_e1_il_reason_code_esiste_nell_enum():
    assert communication_enums.REASON_OWNER_LOGIN_LINK == "owner_login_link"
    assert "owner_login_link" in communication_enums.REASON_CODES
    assert len(communication_enums.REASON_CODES) == 9


def test_e2_gli_altri_reason_code_sono_intatti():
    for atteso in ("stima_pdf", "operator_manual", "operator_reply",
                   "m1", "m2", "m3", "m4", "m5"):
        assert atteso in communication_enums.REASON_CODES
    assert "admin_lead_alert" not in communication_enums.REASON_CODES


@pytest.fixture(scope="module")
def runner():
    from scripts import p26_migrate
    return p26_migrate


def test_e3_la_067_esiste_ed_e_conforme_al_runner(runner):
    assert (MIGRAZIONI / f"{VERSION}.sql").is_file()
    assert (MIGRAZIONI / f"{VERSION}_down.sql").is_file()
    scoperte = {m.version: m for m in runner.discover_migrations()}
    assert VERSION in scoperte
    assert runner.validate_migration(scoperte[VERSION]) == []
    violazioni = []
    for migration in runner.discover_migrations():
        violazioni.extend(runner.validate_migration(migration))
    assert violazioni == []
    numeri = [m.number for m in runner.discover_migrations()]
    # LMC-10 (collisione autorizzata): la 068 e' arrivata, approvata dallo
    # STORAGE GATE. Cio' che questo test sorveglia non e' che la 067 sia
    # l'ultima per sempre - sarebbe una sentinella che scade a ogni fase -
    # ma che sia al suo posto, valida per il runner, e che la numerazione
    # non abbia buchi ne' doppioni: un numero saltato o ripetuto e' il modo
    # in cui due migration finiscono per applicarsi in ordine diverso su
    # due ambienti.
    assert 67 in numeri and numeri.index(67) == numeri.index(66) + 1
    assert numeri == sorted(numeri), numeri
    assert len(numeri) == len(set(numeri)), "numeri di migration duplicati"
    # LMC-12 (collisione autorizzata, stessa natura): la 069 e' lo stream di
    # notifiche PRE-INCARICO `owner_home_notifications`, approvato dal DESIGN
    # GATE. La piu' alta si nomina, cosi' una migration inattesa fa ancora
    # fallire il test.
    assert numeri[-1] == 69, numeri[-3:]


def test_e4_la_up_altera_solo_il_check_del_reason_code(runner):
    sql = runner.strip_sql_comments((MIGRAZIONI / f"{VERSION}.sql").read_text(encoding="utf-8"))
    assert not re.search(r"^\s*BEGIN\s*;", sql, re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", sql, re.MULTILINE)
    assert "schema_migrations" not in sql.lower()
    assert "communication_messages_reason_code_chk" in sql
    assert "'owner_login_link'" in sql
    assert "CREATE TABLE" not in sql and "DROP TABLE" not in sql
    corpo = re.sub(r"\$do\$.*?\$do\$", "", sql, flags=re.S)
    assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE)\b", corpo, re.I)
    # Nessun'altra tabella, nessun altro vincolo.
    alterate = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert alterate == {"communication_messages"}, alterate


def test_e5_la_down_si_bracketta_e_rifiuta_se_ci_sono_messaggi(runner):
    sql = runner.strip_sql_comments((MIGRAZIONI / f"{VERSION}_down.sql").read_text(encoding="utf-8"))
    assert re.search(r"^\s*BEGIN\s*;", sql, re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", sql, re.MULTILINE)
    assert f"DELETE FROM schema_migrations WHERE version = '{VERSION}'" in sql
    assert "RAISE EXCEPTION" in sql, "la down deve rifiutare se esistono messaggi owner_login_link"
    assert "owner_login_link" in sql
    assert "DELETE FROM communication_messages" not in sql, \
        "il ledger non si cancella per far tornare un vincolo"


def test_e6_l_enum_e_il_check_effettivo_coincidono(runner):
    """L'insieme Python contro il CHECK che il database ha DAVVERO dopo la 067."""
    up_064 = (MIGRAZIONI / "064_p29_communication_foundation.sql").read_text(encoding="utf-8")
    up_067 = (MIGRAZIONI / f"{VERSION}.sql").read_text(encoding="utf-8")
    ultimo = re.findall(
        r"CONSTRAINT communication_messages_reason_code_chk\s*CHECK \(([^)]*\))",
        runner.strip_sql_comments(up_064) + runner.strip_sql_comments(up_067))[-1]
    assert set(re.findall(r"'([a-z_0-9]+)'", ultimo)) == communication_enums.REASON_CODES


# ---------------------------------------------------------------------------
# F - il perimetro
# ---------------------------------------------------------------------------

def test_f1_lmc1b_non_tocca_il_funnel_pubblico():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "login_service" not in main_py
    assert "request_link" not in main_py


def test_f2_nessun_hook_post_invio_per_il_nuovo_reason_code():
    from communication import integrations
    sorgente = (ROOT / "communication" / "integrations.py").read_text(encoding="utf-8")
    assert "owner_login" not in sorgente
    for si_applica, _esegui in integrations.HOOK_DOPO_INVIO:
        assert not si_applica({"communication_type": "service",
                               "reason_code": "owner_login_link"})


def test_f3_il_servizio_non_usa_il_token_della_stima():
    """Guarda il CODICE, non la prosa: la docstring nomina `stime.token` di
    proposito, per dire che non lo si usa."""
    import ast

    albero = ast.parse((ROOT / "owner" / "login_service.py").read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nodo.value = ""
    codice = ast.unparse(albero)
    for vietato in ("stime", "stima_dettagliata", "prefill", "token_expires"):
        assert vietato not in codice, vietato
    # E il modulo non importa nulla del dominio stima.
    assert "valuation" not in codice and "property_watch" not in codice


def test_f4_il_dispatcher_e_l_adapter_non_sono_stati_toccati():
    import subprocess
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--name-only", "--",
         "communication/dispatcher.py", "communication/providers/", "communication/router.py"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == "", diff
