"""A30 - MOUNT dell'API Agenda (`/api/appointments`) su `main.py`.

Il router dell'Agenda (A30-2, gia' certificato su un'app di prova) entra
nell'applicazione vera con lo stesso modello dei domini operatore (LMC-15 in
testa): mount con `require_authenticated_operator` (ammissione, nessun DB),
scope su ogni rotta da `require_operator`. Nessuna UI, nessun menu.

Si prova, SENZA database:
  * l'applicazione si importa e l'OpenAPI espone ESATTAMENTE le rotte Agenda;
  * un anonimo non entra in nessuna rotta (401, prima di qualunque query);
  * nessuna collisione: ogni operazione Agenda e' servita solo dal router
    Agenda, e il router Agenda non intercetta nessun'altra operazione;
  * `main.py` contiene per l'Agenda SOLO l'import del router e il mount.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"

#: Le operazioni che il mount espone: (metodo, percorso). Una in piu' o in
#: meno e' una modifica del contratto A30-2, non del mount.
OPERAZIONI_AGENDA = frozenset({
    ("GET", "/api/appointments/calendar"),
    ("GET", "/api/appointments/agents"),
    ("GET", "/api/appointments/availability"),
    ("POST", "/api/appointments/availability/check"),
    ("GET", "/api/appointments"),
    ("POST", "/api/appointments"),
    ("GET", "/api/appointments/{appointment_id}"),
    ("PATCH", "/api/appointments/{appointment_id}"),
    ("GET", "/api/appointments/{appointment_id}/events"),
    ("POST", "/api/appointments/{appointment_id}/schedule"),
    ("POST", "/api/appointments/{appointment_id}/confirm"),
    ("POST", "/api/appointments/{appointment_id}/reschedule"),
    ("POST", "/api/appointments/{appointment_id}/reassign"),
    ("POST", "/api/appointments/{appointment_id}/cancel"),
    ("POST", "/api/appointments/{appointment_id}/complete"),
    ("POST", "/api/appointments/{appointment_id}/no-show"),
})

METODI_HTTP = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def app():
    import main
    return main.app


def _operazioni(app):
    return {(m.upper(), p) for p, voce in app.openapi()["paths"].items()
            for m in voce if m in METODI_HTTP}


def _concreto(percorso):
    return percorso.replace("{appointment_id}", "1")


def _scope(metodo, percorso):
    return {"type": "http", "method": metodo, "path": percorso, "root_path": "",
            "query_string": b"", "headers": []}


def _chi_serve(app, metodo, percorso):
    """Le rotte di primo livello dell'app che servono DAVVERO la richiesta."""
    from starlette.routing import Match
    return [r for r in app.routes if r.matches(_scope(metodo, percorso))[0] == Match.FULL]


def _e_il_router_agenda(rotta):
    from appointments.router import router
    return getattr(rotta, "original_router", None) is router


# ---------------------------------------------------------------------------
# import e OpenAPI
# ---------------------------------------------------------------------------

def test_01_l_applicazione_si_importa_e_monta_il_router_agenda(app):
    agenda = [r for r in app.routes if _e_il_router_agenda(r)]
    assert len(agenda) == 1


def test_02_l_openapi_espone_esattamente_le_rotte_agenda(app):
    operazioni = _operazioni(app)
    agenda = {o for o in operazioni if o[1].startswith("/api/appointments")}
    assert agenda == OPERAZIONI_AGENDA
    tag = {m: v["tags"] for p, voce in app.openapi()["paths"].items()
           if p.startswith("/api/appointments") for m, v in voce.items()}
    assert all(t == ["appointments"] for t in tag.values()), tag


# ---------------------------------------------------------------------------
# anonimo: fuori, prima del database
# ---------------------------------------------------------------------------

@pytest.fixture
def anonimo(app, monkeypatch):
    """Un client senza sessione. Qualunque connessione al database fa fallire
    la prova: il rifiuto deve arrivare dal mount, prima di ogni query."""
    import database
    from core import database as core_database
    from fastapi.testclient import TestClient

    def vietata(*_a, **_k):
        raise AssertionError("una richiesta anonima ha raggiunto il database")

    monkeypatch.setattr(database, "get_connection", vietata)
    monkeypatch.setattr(core_database, "get_connection", vietata)
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.parametrize("metodo,percorso", sorted(OPERAZIONI_AGENDA))
def test_10_anonimo_riceve_401_su_ogni_rotta(anonimo, metodo, percorso):
    kw = {"json": {}} if metodo in ("POST", "PATCH") else {}
    r = anonimo.request(metodo, _concreto(percorso), **kw)
    assert r.status_code == 401, (metodo, percorso, r.status_code, r.text)


@pytest.mark.parametrize("metodo,percorso", [("GET", "/api/appointments"),
                                             ("POST", "/api/appointments")])
def test_11_il_vecchio_canale_basic_non_apre_l_agenda(anonimo, metodo, percorso):
    """P26-5: Basic non e' piu' un canale operatore. Anche sull'Agenda."""
    kw = {"json": {}} if metodo == "POST" else {}
    r = anonimo.request(metodo, percorso, auth=("admin", "admin"), **kw)
    assert r.status_code == 401


def test_12_ammissione_al_mount_e_scope_su_ogni_rotta(app):
    from operator_auth.dependencies import require_authenticated_operator, require_operator

    (agenda,) = [r for r in app.routes if _e_il_router_agenda(r)]
    for rotta in agenda.original_router.routes:
        chiamate = [d.call for d in rotta.dependant.dependencies]
        assert require_operator in chiamate, rotta.path
    sorgente = MAIN.read_text(encoding="utf-8")
    assert ("app.include_router(appointments_router, "
            "dependencies=[Depends(require_authenticated_operator)])") in sorgente
    assert require_authenticated_operator is not None


# ---------------------------------------------------------------------------
# collisioni
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metodo,percorso", sorted(OPERAZIONI_AGENDA))
def test_20_ogni_operazione_agenda_e_servita_solo_dal_router_agenda(app, metodo, percorso):
    chi = _chi_serve(app, metodo, _concreto(percorso))
    assert len(chi) == 1 and _e_il_router_agenda(chi[0]), (metodo, percorso, chi)


def test_21_il_router_agenda_non_intercetta_nessun_altra_operazione(app):
    (agenda,) = [r for r in app.routes if _e_il_router_agenda(r)]
    from starlette.routing import Match

    altre = _operazioni(app) - OPERAZIONI_AGENDA
    assert altre, "OpenAPI vuota: la prova non proverebbe nulla"
    rubate = []
    for metodo, percorso in altre:
        concreto = re.sub(r"\{[^}]+\}", "1", percorso)
        if agenda.matches(_scope(metodo, concreto))[0] == Match.FULL:
            rubate.append((metodo, percorso))
    assert rubate == []


def test_22_nessun_altro_percorso_sotto_il_prefisso_agenda(app):
    fuori = {o for o in _operazioni(app) - OPERAZIONI_AGENDA
             if o[1].startswith("/api/appointments")}
    assert fuori == set()


# ---------------------------------------------------------------------------
# sentinella: main.py contiene SOLO import + mount
# ---------------------------------------------------------------------------

def _righe_codice_agenda(sorgente):
    return [r.strip() for r in sorgente.splitlines()
            if "appointments" in r.split("#", 1)[0]]


def test_30_main_nomina_l_agenda_solo_per_import_e_mount():
    sorgente = MAIN.read_text(encoding="utf-8")
    assert _righe_codice_agenda(sorgente) == [
        "from appointments.router import router as appointments_router",
        "app.include_router(appointments_router, "
        "dependencies=[Depends(require_authenticated_operator)])",
    ]


def test_31_main_non_importa_altro_dal_dominio_agenda():
    albero = ast.parse(MAIN.read_text(encoding="utf-8"))
    importati = []
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith("appointments"):
            importati.append((nodo.module, [(a.name, a.asname) for a in nodo.names]))
        elif isinstance(nodo, ast.Import):
            assert not any(a.name.startswith("appointments") for a in nodo.names)
    assert importati == [("appointments.router", [("router", "appointments_router")])]


def test_32_appointments_router_usato_una_volta_sola_e_solo_nel_mount():
    albero = ast.parse(MAIN.read_text(encoding="utf-8"))
    usi = [n for n in ast.walk(albero)
           if isinstance(n, ast.Name) and n.id == "appointments_router"]
    assert len(usi) == 1
    chiamate = [n for n in ast.walk(albero) if isinstance(n, ast.Call)
                and any(isinstance(a, ast.Name) and a.id == "appointments_router"
                        for a in n.args)]
    assert len(chiamate) == 1
    (mount,) = chiamate
    assert isinstance(mount.func, ast.Attribute) and mount.func.attr == "include_router"
    assert isinstance(mount.func.value, ast.Name) and mount.func.value.id == "app"
    assert [k.arg for k in mount.keywords] == ["dependencies"]


def test_33_solo_il_client_agenda_chiama_l_api():
    """SENTINELLA AGGIORNATA DA A30-4: il mount non portava UI; A30-4 aggiunge
    la pagina `#/agenda`. L'invariante che resta: un solo modulo del frontend
    nomina `/api/appointments` (`agenda/agenda-api.js`), e nessuna voce di
    menu porta all'Agenda (tests/test_a30_4_agenda_ui.py)."""
    import re

    def codice(testo):                      # i commenti possono NOMINARE l'API
        testo = "\n".join(re.sub(r"(^|[^:'\"])//.*$", r"\1", r) for r in testo.splitlines())
        return re.sub(r"/\*.*?\*/", "", testo, flags=re.S)

    trovati = sorted(p.relative_to(ROOT).as_posix()
                     for p in (ROOT / "static").rglob("*")
                     if p.is_file() and p.suffix in {".js", ".html", ".css"}
                     and "/api/appointments" in codice(
                         p.read_text(encoding="utf-8", errors="replace")))
    assert trovati == ["static/os_shell/assets/agenda/agenda-api.js"]
