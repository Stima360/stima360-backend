"""FLOW - il catalogo globale ha una porta, e le letture non scrivono.

DUE DIFETTI, UNA SOLA SUPERFICIE

`flow_rules` e' UN catalogo per tutta la piattaforma. Non ha `agency_id`, e
non deve averlo: e' lo stesso insieme di regole per ogni agenzia. Da questa
proprieta' corretta erano discese due conseguenze sbagliate.

**1 - "senza tenant" era stato letto come "senza controllo".** Le cinque rotte
che SCRIVONO il catalogo - sync, parametri, reset, activate, deactivate -
stavano dietro al solo `require_authenticated_operator`, che e' la guardia del
mount: chiede se il chiamante e' autenticato, non chi sia. Un `agent` di una
qualunque agenzia poteva quindi cambiare i parametri di una regola, o
attivarla, PER TUTTE le agenzie. Non e' un leak di lettura: e' una scrittura
di configurazione sulla piattaforma intera fatta da un ruolo che nella propria
agenzia non amministra nulla.

**2 - le due GET scrivevano.** `list_rules` e `get_rule_row` avevano
`synchronize=True` per default, quindi una GET faceva INSERT, UPDATE e COMMIT:
creava le regole mancanti, riscriveva `updated_at` su tutte, e - su un cambio
di `code_version` - riportava `last_simulation_status` a `outdated`, cioe'
poteva invalidare il prerequisito di attivazione di una regola per il solo
fatto che qualcuno aveva aperto una lista. Il flag esisteva, ma il default era
quello sbagliato e i due chiamanti che lo spegnevano lo facevano per ragioni
loro: chi leggeva non sapeva di scrivere.

COSA PROVANO QUESTI TEST, E COME

I test 1-9 e 13-18 passano dal vero stack HTTP. Non c'e' nessun
`dependency_overrides` sulle guardie: la sessione e' sostituita al punto piu'
profondo possibile - la sola lettura di `operator_sessions` - con
`SessionDouble`, e tutto il resto (`optional_session`,
`require_authenticated_operator`, `require_platform_admin`,
`legacy_basic_agency_context`, la costruzione del contesto, il predicato di
ruolo) e' codice di produzione. Un 403 qui e' un 403 che il progetto produce
davvero.

I test 10-12 non passano dall'HTTP, e di proposito: la domanda "questa GET
scrive?" non si risponde guardando lo status code. Si risponde guardando le
istruzioni SQL che il percorso di lettura emette e il tipo di cursore che
apre. `RecordingCursor` raccoglie le une, `install` intercetta l'altro: una
GET che tornasse a sincronizzare aprirebbe `core_cursor(commit=True)` ed
emetterebbe INSERT/UPDATE, e i test lo vedrebbero anche se la risposta HTTP
restasse identica.

PERCHE' `require_platform_admin` E NON UN CONTROLLO SCRITTO QUI

Perche' e' la definizione che il progetto ha gia'. Riscriverla nel router di
FLOW avrebbe prodotto una seconda definizione di "amministratore di
piattaforma", e due definizioni della stessa cosa divergono. Riusarla porta
anche il registro append-only: di un tentativo respinto resta una riga
`denied` con l'attore e il percorso (test 5-7 la verificano).

IL PRIVILEGIO, NON L'AGENZIA ATTIVA

`is_platform_admin` viene dalla persona e sopravvive all'acting. Un platform
admin che sta operando dentro un'agenzia resta platform admin: per queste
operazioni GLOBALI conta il privilegio (test 4b), mentre sulle superfici
TENANT lo stesso chiamante resta scoped alla sua agenzia attiva (test 17) e,
se non ne ha una, non ottiene dati di nessuno (test 18).
"""
from __future__ import annotations

import inspect
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import NotFoundError
from operator_auth.enums import COOKIE_NAME
from platform_admin import dependencies as platform_deps
from platform_admin.enums import RESULT_DENIED, RESULT_SUCCESS
from tests.operator_session_helpers import SessionDouble, TEST_TOKEN

# IL RIFERIMENTO ALLA FUNZIONE VERA, PRESO ADESSO.
#
# La fixture `flow` sostituisce gli attributi di `flow.service` con dei
# registratori, perche' i test HTTP non devono toccare il database - e
# `flow_router.service` E' `flow.service`, lo stesso oggetto modulo. Un test
# che, sotto quella fixture, chiamasse `flow_service.scan_for_agency`
# chiamerebbe il registratore e misurerebbe zero statement: passerebbe anche
# contro il codice difettoso. Questo nome tiene la funzione originale.
from flow.service import scan_for_agency as SCAN_REALE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VISTA = ROOT / "static" / "os_shell" / "assets" / "views" / "automazione-dettaglio.js"

A, B = 71, 82          # due agenzie; deliberatamente non 1

# Le cinque scritture globali, nella forma in cui il brief le nomina.
MUTATIONS = (
    ("post", "/api/flow/sync-rules", None),
    ("patch", "/api/flow/rules/FLOW-R001/parameters", {"parameters": {}}),
    ("post", "/api/flow/rules/FLOW-R001/reset-parameters", None),
    ("post", "/api/flow/rules/FLOW-R001/activate", {}),
    ("post", "/api/flow/rules/FLOW-R001/deactivate", None),
)

RULE_ROW = {"id": 1, "code": "FLOW-R001", "is_active": False, "parameters": {}}

# La riga di catalogo come la tabella la tiene: abbastanza completa perche' la
# sincronizzazione - quella che queste letture NON devono piu' fare - possa
# arrivare in fondo se qualcuno la rimettesse. Vedi `RecordingCursor`.
BASE_ROW = {
    "id": 1, "code": "FLOW-R001", "code_version": "1.0.0",
    "name": "regola", "description": "", "event_type": "manual",
    "entity_type": "lead", "is_active": False, "priority": "normal",
    "cooldown_minutes": 0, "parameters": {}, "default_parameters": {},
    "allowed_parameters": {}, "last_simulation_status": "never_run",
    "archived_at": None,
}


# ---------------------------------------------------------------------------
# L'app: le guardie sono vere, il service e l'audit sono osservati.
# ---------------------------------------------------------------------------

class Calls(list):
    """Ogni chiamata al service, con i suoi argomenti."""

    def recorder(self, name):
        def fn(*args, **kwargs):
            self.append((name, args, kwargs))
            return RULE_ROW
        return fn

    def names(self):
        return [c[0] for c in self]

    def agencies(self):
        """Il primo argomento posizionale di ogni funzione `*_for_agency`."""
        return [c[1][0] for c in self if c[0].endswith("_for_agency")]


@pytest.fixture
def flow(monkeypatch):
    """Client, sessioni, chiamate al service e righe di audit."""
    from flow import router as flow_router

    calls = Calls()
    for name in (
        "sync_rules", "list_rules", "get_rule_row", "update_parameters",
        "reset_parameters", "activate", "deactivate",
        "list_events_for_agency", "list_executions_for_agency",
        "list_suppressions_for_agency", "dashboard_for_agency",
        "get_execution_for_agency", "simulate_for_agency",
        "scan_for_agency",
    ):
        monkeypatch.setattr(flow_router.service, name, calls.recorder(name))

    audit: list[dict] = []
    monkeypatch.setattr(
        platform_deps.audit, "record",
        lambda **kwargs: (audit.append(kwargs), len(audit))[1],
    )

    app = FastAPI()
    app.include_router(flow_router.router)
    client = TestClient(app, raise_server_exceptions=False)
    sessions = SessionDouble(monkeypatch)
    client.cookies.set(COOKIE_NAME, TEST_TOKEN)
    return client, sessions, calls, audit


def as_platform_admin(sessions, *, acting=None):
    """Il privilegio di piattaforma. `acting` e' l'agenzia in cui sta operando."""
    return sessions.login(
        user_id=9, agency_id=acting,
        role="agency_owner" if acting is not None else "platform_admin",
        is_platform_admin=True,
    )


def as_tenant(sessions, role, *, agency_id=A):
    return sessions.login(
        user_id=1, agency_id=agency_id, role=role, is_platform_admin=False,
    )


def call(client, method, path, body):
    return getattr(client, method)(path, json=body) if body is not None \
        else getattr(client, method)(path)


# ---------------------------------------------------------------------------
# 1-4 - il platform admin puo' amministrare il catalogo
# ---------------------------------------------------------------------------

def test_1_platform_admin_can_sync_rules(flow):
    client, sessions, calls, audit = flow
    as_platform_admin(sessions)

    response = client.post("/api/flow/sync-rules")

    assert response.status_code == 200, response.text
    assert calls.names() == ["sync_rules"]
    assert audit[0]["result"] == RESULT_SUCCESS


def test_2_platform_admin_can_update_parameters(flow):
    client, sessions, calls, _ = flow
    as_platform_admin(sessions)

    response = client.patch(
        "/api/flow/rules/FLOW-R001/parameters",
        json={"parameters": {"cooldown_minutes": 30}},
    )

    assert response.status_code == 200, response.text
    assert calls.names() == ["update_parameters"]


def test_3_platform_admin_can_reset_parameters(flow):
    client, sessions, calls, _ = flow
    as_platform_admin(sessions)

    response = client.post("/api/flow/rules/FLOW-R001/reset-parameters")

    assert response.status_code == 200, response.text
    assert calls.names() == ["reset_parameters"]


def test_4_platform_admin_can_activate_and_deactivate(flow):
    client, sessions, calls, _ = flow
    as_platform_admin(sessions)

    assert client.post(
        "/api/flow/rules/FLOW-R001/activate", json={}
    ).status_code == 200
    assert client.post(
        "/api/flow/rules/FLOW-R001/deactivate"
    ).status_code == 200
    assert calls.names() == ["activate", "deactivate"]


def test_4b_the_privilege_survives_acting_because_it_belongs_to_the_person(flow):
    """Un platform admin che opera DENTRO un'agenzia resta platform admin.

    `is_platform_admin` viene dalla riga utente, non dall'acting: per una
    operazione globale conta il privilegio, non l'agenzia attiva. Se questo
    test fallisse, amministrare il catalogo richiederebbe di uscire
    dall'agenzia - e la via d'uscita sarebbe reintrodurre un controllo piu'
    debole.
    """
    client, sessions, calls, _ = flow
    as_platform_admin(sessions, acting=A)

    for method, path, body in MUTATIONS:
        assert call(client, method, path, body).status_code == 200, path
    assert len(calls) == len(MUTATIONS)


# ---------------------------------------------------------------------------
# 5-7 - i tre ruoli tenant sono respinti su tutte e cinque
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["agency_owner", "agency_admin", "agent"])
def test_5_6_7_a_tenant_role_is_refused_on_every_global_mutation(flow, role):
    client, sessions, calls, audit = flow
    as_tenant(sessions, role)

    for method, path, body in MUTATIONS:
        response = call(client, method, path, body)
        assert response.status_code == 403, f"{role} {method} {path}: {response.text}"

    # Nessuna delle cinque ha raggiunto il service: il rifiuto e' PRIMA.
    assert calls == [], calls.names()
    # E ogni tentativo ha lasciato la sua riga nel registro append-only.
    assert len(audit) == len(MUTATIONS)
    assert {entry["result"] for entry in audit} == {RESULT_DENIED}


def test_7b_an_anonymous_caller_is_401_and_writes_no_audit_row(flow):
    """Il mount risponde per primo, e una richiesta anonima non e' un atto."""
    client, sessions, calls, audit = flow
    client.cookies.clear()

    for method, path, body in MUTATIONS:
        assert call(client, method, path, body).status_code == 401, path
    assert audit == []
    assert calls == []


# ---------------------------------------------------------------------------
# 8-9 - leggere il catalogo resta di tutti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "role", ["agency_owner", "agency_admin", "agent", "platform_admin"]
)
def test_8_listing_the_catalogue_keeps_working_for_every_role(flow, role):
    """La Shell mostra la lista delle regole a chiunque la apra.

    Chiudere anche le GET avrebbe rotto una schermata senza chiudere nessun
    buco: leggere un catalogo uguale per tutte le agenzie non concede niente.
    """
    client, sessions, calls, _ = flow
    if role == "platform_admin":
        as_platform_admin(sessions)
    else:
        as_tenant(sessions, role)

    response = client.get("/api/flow/rules")

    assert response.status_code == 200, response.text
    assert calls.names() == ["list_rules"]


@pytest.mark.parametrize(
    "role", ["agency_owner", "agency_admin", "agent", "platform_admin"]
)
def test_9_reading_one_rule_keeps_working_for_every_role(flow, role):
    client, sessions, calls, _ = flow
    if role == "platform_admin":
        as_platform_admin(sessions)
    else:
        as_tenant(sessions, role)

    response = client.get("/api/flow/rules/FLOW-R001")

    assert response.status_code == 200, response.text
    assert calls.names() == ["get_rule_row"]


# ---------------------------------------------------------------------------
# 10-12 - le due letture LEGGONO
#
# Qui si scende sotto l'HTTP. Uno status code 200 e' identico prima e dopo il
# fix: cio' che cambia sono le istruzioni emesse e il cursore aperto.
# ---------------------------------------------------------------------------

class RecordingCursor:
    """Registra ogni statement, e si comporta come la tabella reale.

    Non e' un mock che risponde sempre la stessa cosa: tiene le righe in un
    dizionario per `code`, un SELECT trova o non trova, un INSERT ... RETURNING
    crea e restituisce. La fedelta' serve alla LEGGIBILITA' della prova.
    Contro il codice difettoso la sincronizzazione arriva in fondo, e il test
    fallisce sull'asserzione che conta - "questi statement non sono tutti
    SELECT", con l'UPDATE stampato accanto - invece di schiantarsi su una
    chiave mancante lasciando il lettore a indovinare il perche'.
    """

    def __init__(self, rows=None):
        self.table = {row["code"]: dict(row) for row in (rows or [])}
        self.statements: list[str] = []
        self.result = None

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        self.statements.append(flat)
        verb = flat.split(None, 1)[0].upper()
        values = list(params.values()) if isinstance(params, dict) else list(params or ())
        code = next(
            (v for v in values if isinstance(v, str) and v.startswith("FLOW-")), None
        )

        if verb == "SELECT":
            self.result = self.table.get(code) if code else list(self.table.values())
        elif verb in ("INSERT", "UPDATE"):
            row = self.table.get(code) or dict(BASE_ROW, code=code or "FLOW-R000")
            self.table[row["code"]] = row
            self.result = row
        else:
            self.result = None

    def fetchone(self):
        if isinstance(self.result, list):
            return dict(self.result[0]) if self.result else None
        return dict(self.result) if self.result else None

    def fetchall(self):
        if isinstance(self.result, list):
            return [dict(row) for row in self.result]
        return [dict(self.result)] if self.result else []

    def verbs(self):
        return {s.split(None, 1)[0].upper() for s in self.statements if s}


def install_cursor(monkeypatch, cursor):
    """Sostituisce `core_cursor` in flow.repository, registrando i `commit=`.

    Il flag e' meta' della prova: una funzione di lettura che aprisse un
    cursore transazionale avrebbe gia' dichiarato l'intenzione di scrivere,
    anche prima di emettere una sola istruzione.
    """
    from contextlib import contextmanager

    from flow import repository as flow_repository

    opened: list[bool] = []

    @contextmanager
    def fake(*_args, commit=False, **_kwargs):
        opened.append(commit)
        yield None, cursor

    monkeypatch.setattr(flow_repository, "core_cursor", fake)
    return opened


def test_10_the_reads_issue_selects_and_nothing_else(monkeypatch):
    """Nessun INSERT, nessun UPDATE, nessun cursore transazionale."""
    from flow import repository as flow_repository

    cursor = RecordingCursor(rows=[dict(BASE_ROW)])
    opened = install_cursor(monkeypatch, cursor)

    flow_repository.list_rules()
    flow_repository.get_rule_row("FLOW-R001")

    assert cursor.verbs() == {"SELECT"}, cursor.statements
    assert opened == [False, False], opened
    assert not any(
        re.search(r"\b(INSERT|UPDATE|DELETE|COMMIT)\b", s, re.IGNORECASE)
        for s in cursor.statements
    ), cursor.statements


def test_11_a_missing_rule_is_not_created_by_a_read(monkeypatch):
    """Prima nasceva da sola; adesso e' un 404 e un atto esplicito.

    Una riga di catalogo creata come effetto collaterale della lettura di un
    agente era esattamente il modo in cui nessuno si accorgeva che mancava.
    """
    from flow import repository as flow_repository

    cursor = RecordingCursor(rows=[])          # la regola non c'e'
    opened = install_cursor(monkeypatch, cursor)

    with pytest.raises(NotFoundError):
        flow_repository.get_rule_row("FLOW-R999")

    assert cursor.verbs() == {"SELECT"}, cursor.statements
    assert opened == [False], opened

    # E la lista, su tabella vuota, resta vuota invece di popolarla.
    empty = RecordingCursor(rows=[])
    install_cursor(monkeypatch, empty)
    assert flow_repository.list_rules() == []
    assert empty.verbs() == {"SELECT"}, empty.statements


def test_12_updated_at_is_never_touched_by_a_read(monkeypatch):
    """E la sincronizzazione non e' piu' raggiungibile da un percorso di lettura.

    Le due asserzioni sono complementari: la prima guarda il SQL emesso, la
    seconda toglie di mezzo il modo in cui il difetto era nato - un parametro
    booleano che decideva, dentro la funzione di lettura, se scrivere. Il
    parametro non esiste piu', quindi nessun chiamante puo' riaccenderlo per
    distrazione.
    """
    from flow import repository as flow_repository

    cursor = RecordingCursor(rows=[dict(BASE_ROW)])
    install_cursor(monkeypatch, cursor)

    flow_repository.list_rules()
    flow_repository.get_rule_row("FLOW-R001")

    assert not any("updated_at" in s.lower() for s in cursor.statements), \
        cursor.statements

    for name in ("list_rules", "get_rule_row"):
        parameters = inspect.signature(getattr(flow_repository, name)).parameters
        assert "synchronize" not in parameters, name

    source = inspect.getsource(flow_repository.list_rules) + \
        inspect.getsource(flow_repository.get_rule_row)
    assert "sync_rules" not in source, source


# ---------------------------------------------------------------------------
# 13-16 - le superfici tenant restano scoped
#
# Il fix tocca il catalogo globale. Queste quattro sono la prova che non ha
# toccato nient'altro: ogni superficie tenant riceve l'agenzia del chiamante,
# e un secondo chiamante riceve la propria.
# ---------------------------------------------------------------------------

TENANT_SURFACES = (
    ("/api/flow/events", "list_events_for_agency"),
    ("/api/flow/executions", "list_executions_for_agency"),
    ("/api/flow/suppressions", "list_suppressions_for_agency"),
    ("/api/flow/dashboard", "dashboard_for_agency"),
)


@pytest.mark.parametrize("path,function", TENANT_SURFACES)
def test_13_14_15_16_a_tenant_surface_carries_the_callers_agency(flow, path, function):
    client, sessions, calls, _ = flow

    as_tenant(sessions, "agency_owner", agency_id=A)
    assert client.get(path).status_code == 200

    as_tenant(sessions, "agency_owner", agency_id=B)
    assert client.get(path).status_code == 200

    assert calls.names() == [function, function]
    assert calls.agencies() == [A, B]


# ---------------------------------------------------------------------------
# 17-18 - il platform admin sulle superfici tenant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,function", TENANT_SURFACES)
def test_17_a_platform_admin_acting_stays_inside_the_agency(flow, path, function):
    """Il privilegio apre il catalogo globale, non i dati degli altri.

    Lo stesso chiamante che in test 4b amministra la piattaforma, qui vede
    l'agenzia in cui sta operando e nient'altro.
    """
    client, sessions, calls, _ = flow
    as_platform_admin(sessions, acting=B)

    assert client.get(path).status_code == 200
    assert calls.names() == [function]
    assert calls.agencies() == [B]


@pytest.mark.parametrize("path,_function", TENANT_SURFACES)
def test_18_an_unbound_platform_admin_gets_no_tenant_data(flow, path, _function):
    """Nessuna agenzia attiva, nessun dato: il service non viene mai chiamato.

    E' la decisione D1: non esiste un ramo cross-agency da cui un platform
    admin senza membership ottenga i dati di tutti. Il rifiuto arriva da
    `require_agency()`, prima di qualunque query - e cio' che questo test
    certifica e' proprio quello: nessuna riga esce, nessun repository viene
    interrogato, la risposta non e' un 200.

    SULLO STATUS CODE, E PERCHE' QUI NON LO SI FISSA A 403

    Oggi la risposta e' 500, non 403, e non e' una conseguenza di questo fix:
    le rotte tenant non sono state toccate (si veda il diff). La causa e' che
    `ctx.require_agency()` sta fra gli ARGOMENTI di `tr(...)`, quindi viene
    valutato prima che `tr` esista sullo stack e la sua
    `except PlatformAdminAgencyRequired` non lo vede. Il commento dentro `tr`
    dice che la condizione "non puo' scattare su questo canale, perche' la
    dipendenza di compatibilita' risolve sempre la Default Agency": era vero
    fino a P26-4, e P26-5 - sessione e nient'altro - lo ha reso falso senza
    che nessuno ripassasse di qui.

    E' un difetto di forma della risposta, non di tenuta: nessun dato di
    nessuna agenzia raggiunge il chiamante, e il 500 non ne rivela l'esistenza.
    Sta fuori dal perimetro di questa fase ed e' riportato come rilievo, non
    corretto qui. Questo test asserisce la proprieta' di sicurezza, che vale
    in entrambi i casi, e fissa a parte lo stato di fatto: se un domani il 500
    diventasse 403 la riga da cambiare e' una sola, e se diventasse 200
    fallirebbe l'asserzione che conta.
    """
    client, sessions, calls, _ = flow
    as_platform_admin(sessions)          # acting=None

    response = client.get(path)

    # Cio' che conta: nessun dato tenant, e nessuna query tentata.
    assert response.status_code != 200, response.text
    assert calls == [], calls.names()

    # Lo stato di fatto, fissato perche' non cambi senza che qualcuno se ne accorga.
    assert response.status_code == 500, response.status_code


# ---------------------------------------------------------------------------
# 19-20 - l'unica schermata che premeva quei bottoni
#
# La Shell chiama due delle cinque rotte, da `automazione-dettaglio.js`:
# activate e deactivate. Adesso rispondono 403 a un ruolo tenant, quindi i due
# bottoni sarebbero controlli che falliscono sempre. Nasconderli non e' una
# difesa - quella e' `require_platform_admin`, lato server - ed e' la stessa
# scelta che la Shell fa gia' per la voce "Rete": una richiesta che si sa gia'
# rifiutata non si manda.
# ---------------------------------------------------------------------------

def test_19_the_detail_view_gates_the_two_buttons_on_the_platform_privilege():
    sorgente = VISTA.read_text(encoding="utf-8")

    # I bottoni esistono ancora, e stanno dentro il ramo condizionale.
    assert 'id="rule-activate"' in sorgente
    assert 'id="rule-deactivate"' in sorgente
    assert "is_platform_admin" in sorgente
    assert "${amministra ?" in sorgente

    # E la lettura resta di tutti: nessuna GET e' stata messa dietro un
    # controllo di ruolo insieme alle due scritture.
    assert "apiGet(`/api/flow/rules/" in sorgente
    prima_del_gate = sorgente.split("${amministra ?")[0]
    assert "apiGet(" in prima_del_gate


def test_20_the_view_still_compiles():
    """La lezione di P29-3D: un errore di sintassi qui rompe tutta la Shell."""
    if shutil.which("node") is None:
        pytest.skip("node non disponibile")

    esito = subprocess.run(
        ["node", "--check", str(VISTA)], capture_output=True, text=True
    )
    assert esito.returncode == 0, esito.stderr


# ---------------------------------------------------------------------------
# 21-27 - la terza porta di servizio: scan
#
# `POST /api/flow/scan` e' una superficie TENANT - `legacy_basic_agency_context`,
# quindi `agency_owner`, `agency_admin` e `agent` - e `service.scan` cominciava
# con `repository.sync_rules()`. Le stesse INSERT e UPDATE sul catalogo di
# tutta la piattaforma che i test 5-7 chiudono sulle cinque rotte di
# amministrazione, raggiunte per un'altra strada.
#
# I test qui sotto guardano il SQL, non lo status code: una scansione che
# tornasse a sincronizzare risponderebbe 200 esattamente come adesso.
# ---------------------------------------------------------------------------

def _scan_payload(**overrides):
    from flow.schemas import ScanRequest

    return ScanRequest(**{"limit": 5, "simulation": True, **overrides})


def _catalogo_completo():
    """Una riga per ogni regola del registro: il catalogo gia' sincronizzato."""
    from flow.rules.registry import ALL_RULES

    return [dict(BASE_ROW, id=n, code=code, is_active=True)
            for n, code in enumerate(sorted(ALL_RULES), start=1)]


def _scansione_senza_effetti(monkeypatch, *, righe, agency_id=A, codes=None):
    """Esegue una scansione vera e restituisce gli statement che ha emesso.

    Del percorso si sostituisce SOLO il cursore e gli adattatori che leggono
    le entita' di business: `service.scan` - la funzione in esame - e ogni
    chiamata che fa al repository restano codice di produzione, ed e' li' che
    il difetto viveva.
    """
    from flow import service as flow_service

    cursor = RecordingCursor(rows=righe)
    install_cursor(monkeypatch, cursor)
    # Con un'agenzia `_adapters` sceglie lo scanner SCOPED: e' quello che il
    # percorso tenant usa davvero, ed e' quello che si sostituisce.
    monkeypatch.setattr(
        flow_service, "scan_candidates_for_agency",
        lambda agency_id, code, parameters, limit: [],
    )
    esito = SCAN_REALE(agency_id, _scan_payload(rule_codes=codes))

    # ANTI-VACUITA'. Se il percorso vero non fosse stato eseguito - una
    # fixture che lo ha sostituito, un adattatore che ha interrotto prima -
    # il cursore sarebbe muto, e "nessuna scrittura" sarebbe vera per il
    # motivo sbagliato. Una scansione LEGGE il catalogo: lo si pretende.
    assert any("flow_rules" in s.lower() for s in cursor.statements), \
        "la scansione non ha nemmeno letto il catalogo: il percorso vero non e' stato eseguito"
    return cursor, esito


def _scritture_su_flow_rules(cursor):
    return [s for s in cursor.statements
            if re.match(r"^\s*(INSERT|UPDATE|DELETE)\b", s, re.IGNORECASE)
            and "flow_rules" in s.lower()]


@pytest.mark.parametrize("role", ["agency_owner", "agency_admin", "agent"])
def test_21_22_23_a_tenant_scan_leaves_the_global_catalogue_untouched(flow, monkeypatch, role):
    """La rotta risponde, e il catalogo e' identico prima e dopo.

    Due meta': la prima prova che `POST /api/flow/scan` resta aperta al ruolo
    - chiuderla non era il rimedio; la seconda che la scansione vera non
    emette una sola scrittura su `flow_rules`.
    """
    client, sessions, calls, _ = flow
    as_tenant(sessions, role)

    risposta = client.post("/api/flow/scan", json={"limit": 5, "simulation": True})
    assert risposta.status_code == 200, risposta.text
    assert calls.agencies() == [A]

    cursor, _esito = _scansione_senza_effetti(monkeypatch, righe=_catalogo_completo())

    assert _scritture_su_flow_rules(cursor) == [], cursor.statements
    assert cursor.verbs() <= {"SELECT"}, sorted(cursor.verbs())


def test_24_a_cron_cycle_does_not_synchronise_either(monkeypatch):
    """Il cron elabora il catalogo, non lo modifica.

    `scan_for_all_agencies` e' cio' che il runner chiama una volta per
    agenzia. La sincronizzazione non e' stata SPOSTATA qui: spostarla avrebbe
    conservato la scrittura automatica togliendole anche il chiamante a cui
    attribuirla.
    """
    from flow import repository as flow_repository
    from flow import service as flow_service

    cursor = RecordingCursor(rows=_catalogo_completo())
    install_cursor(monkeypatch, cursor)
    monkeypatch.setattr(flow_repository, "list_active_agency_ids", lambda: [A, B])
    monkeypatch.setattr(flow_service, "scan_candidates", lambda code, parameters, limit: [])

    esito = flow_service.scan_for_all_agencies(_scan_payload())

    assert esito["agencies"] == 2
    assert _scritture_su_flow_rules(cursor) == [], cursor.statements

    # E il runner non la chiama per conto suo: nel suo sorgente non compare.
    sorgente = (ROOT / "run_flow_p2b_cron.py").read_text(encoding="utf-8")
    assert "sync_rules" not in sorgente, "il cron non deve sincronizzare"


def test_25_a_rule_missing_from_the_database_is_not_created_by_a_scan(monkeypatch):
    """Chiesta per nome, diventa un esito fallito. Mai una riga nuova.

    Prima la scansione la creava per conto suo, e nessuno si accorgeva che
    mancava. Adesso il rimedio e' `POST /api/flow/sync-rules`, cioe' il gesto
    di chi amministra la piattaforma.
    """
    from flow.rules.registry import ALL_RULES

    assente = sorted(ALL_RULES)[0]
    catalogo = [r for r in _catalogo_completo() if r["code"] != assente]

    cursor, esito = _scansione_senza_effetti(
        monkeypatch, righe=catalogo, codes=[assente]
    )

    assert _scritture_su_flow_rules(cursor) == [], cursor.statements
    assert [v["code"] for v in catalogo].count(assente) == 0
    # Non e' silenziosa: l'assenza esce come fallimento di stage `adapter`.
    falliti = [x for x in esito["items"] if x.get("status") == "failed"]
    assert [x["rule_code"] for x in falliti] == [assente], esito["items"]
    assert falliti[0]["stage"] == "adapter"


def test_26_the_explicit_platform_action_still_synchronises(monkeypatch):
    """`sync_rules` non e' stata rimossa: e' rimasta l'unica che scrive."""
    from flow import repository as flow_repository

    cursor = RecordingCursor(rows=[])          # tabella vuota
    opened = install_cursor(monkeypatch, cursor)

    creati = flow_repository.sync_rules()

    from flow.rules.registry import ALL_RULES
    assert len(creati) == len(ALL_RULES)
    assert "INSERT" in cursor.verbs(), sorted(cursor.verbs())
    # E lo fa dentro una transazione che si impegna, a differenza di ogni
    # lettura (test 10).
    assert opened == [True], opened


def test_27_one_http_surface_synchronises_and_it_is_the_platform_one():
    """Provato seguendo le chiamate nel sorgente, non un elenco scritto qui.

    Delle funzioni di `flow.service`, una sola arriva a
    `repository.sync_rules`; nel router una sola rotta la nomina, ed e' quella
    che porta `require_platform_admin`. Nessuna GET, non `/scan`, non
    `/evaluate`, non `/simulate`, non il recupero eventi.
    """
    import ast

    from flow import router as flow_router
    from flow import service as flow_service

    albero = ast.parse(inspect.getsource(flow_service))
    chiamanti = {
        nodo.name
        for nodo in ast.walk(albero)
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "repository.sync_rules" in ast.get_source_segment(
            inspect.getsource(flow_service), nodo
        )
    }
    assert chiamanti == {"sync_rules"}, sorted(chiamanti)

    sorgente_router = (ROOT / "flow" / "router.py").read_text(encoding="utf-8")
    router_ast = ast.parse(sorgente_router)
    rotte = [
        nodo for nodo in ast.walk(router_ast)
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "service.sync_rules" in (ast.get_source_segment(sorgente_router, nodo) or "")
    ]
    assert [r.name for r in rotte] == ["sync_rules"], [r.name for r in rotte]

    # E quella rotta dichiara la dipendenza di piattaforma sul decoratore.
    dipendenze = [
        kw.value.id
        for dec in rotte[0].decorator_list if isinstance(dec, ast.Call)
        for kw in dec.keywords
        if kw.arg == "dependencies" and isinstance(kw.value, ast.Name)
    ]
    assert dipendenze == ["SOLO_PLATFORM"], dipendenze
    assert "sync_rules" in flow_router.PLATFORM_WRITE_ROUTES
