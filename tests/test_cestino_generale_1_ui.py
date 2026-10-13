"""CESTINO-GENERALE-1 (CG-1) - UI del Cestino unico (Shell OS), senza database.

  M  funzioni pure di trash/trash-unified.js: percorsi (mai un'agenzia sulla
     superficie tenant), contatori, card (testo sempre escaped; «Gestisci»
     solo per l'agenzia; agenzia solo nella vista globale), collegamenti,
     richieste edifici, scheda iniziale dall'indirizzo;
  S  statici: le rotte del Cestino unico solo in trash-api.js; nessuna
     scrittura nuova; la Rete porta al Cestino senza duplicare la gestione;
  D  Shell VERA nello stub DOM:
     01 «Tutti» predefinita: elenco e contatori, ricerca, pastiglia di
        categoria, collegamenti; nessuna scrittura;
     02 server senza il Cestino unico (404): avviso e scheda Immobili come prima;
     03 amministratore di piattaforma senza agenzia: voce Cestino nel menu,
        vista globale con selettore d'agenzia; nessuna rotta tenant;
     04 Richieste edifici dell'agenzia in sola lettura; un 403 della vista
        globale e' solo un avviso.

Senza node le prove M/D sono SKIPPED (BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
UNIFIED = ASSETS / "trash" / "trash-unified.js"
CESTINO = ASSETS / "views" / "cestino.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
rt = a30_5._rt()
_codice = t2b3._codice


def _modello(expr, modulo=UNIFIED):
    script = f"import * as m from '{modulo.as_posix()}';\nconsole.log(JSON.stringify({expr}));"
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True,
                           text=True, timeout=30, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


VOCI = [
    {"kind": "property", "id": 31, "agency_id": 7, "agency_name": "Agenzia A", "label": "IMM-31", "line": "Bilocale · Via Roma 1",
     "status": "draft", "deleted_at": "2026-10-12T10:00:00+02:00", "deleted_reason": "duplicate", "deleted_by_name": "Olga",
     "deleted_note": "doppione", "extra": {"archived": False}, "href": None, "kind_label": "Immobili"},
    {"kind": "contact", "id": 41, "agency_id": 7, "agency_name": "Agenzia A", "label": "Gino <b>Cestino</b>",
     "line": "gino@x.test", "status": "active", "deleted_at": "2026-10-11T10:00:00+02:00",
     "deleted_reason": "invalid_data", "deleted_by_name": "Olga", "deleted_note": None, "extra": {},
     "href": "#/contatti/41", "kind_label": "Contatti"},
    {"kind": "building", "id": 9, "agency_id": 7, "agency_name": "Agenzia A", "label": "Palazzina 9", "line": "Via Roma 9",
     "status": "completed", "deleted_at": "2026-10-10T10:00:00+02:00", "deleted_reason": "created_by_mistake",
     "deleted_by_name": "Super", "deleted_note": None, "extra": {"units_total": 3}, "href": "#/edifici/9",
     "kind_label": "Edifici"},
]
CONTI = {"total": 3, "building_requests_pending": 1, "scope": "agency", "categories": [
    {"kind": "property", "label": "Immobili", "count": 1, "installed": True},
    {"kind": "contact", "label": "Contatti", "count": 1, "installed": True},
    {"kind": "building", "label": "Edifici", "count": 1, "installed": True},
    {"kind": "buy_request", "label": "Richieste acquirente", "count": 0, "installed": True}]}
RELAZIONI = {"kind": "contact", "id": 41, "label": "Gino", "agency_id": 7,
             "relations": [{"table": "consent_events", "column": "contact_id", "count": 2, "label": "Consensi (registro)"}]}
RICHIESTA = {"id": 55, "agency_id": 7, "building_id": 7, "building_label": "Palazzina 7", "status": "pending",
             "reason_code": "duplicate", "request_note": "doppia", "requested_by_name": "Anna",
             "requested_at": "2026-10-12T09:00:00+02:00", "units_total": 2, "building_exists": True}


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_percorsi_contatori_card():
    out = _modello(f"""({{
      tenant: m.unifiedTrashPath('', {{ type: 'contact', q: ' Gino ', reason: '', agency: 2, deleted_by: 3 }}, {{ offset: 50, limit: 50 }}),
      tutti: m.unifiedTrashPath('', {{ type: 'all' }}, {{}}),
      conti: m.unifiedTrashPath('/counts', {{ q: 'a&b' }}, {{}}),
      globale: m.unifiedTrashPath('', {{ agency: 2, date_from: '2026-10-01' }}, {{ scope: 'platform', limit: 50, offset: 0 }}),
      pastiglie: m.countsHtml({json.dumps(CONTI)}, 'contact'),
      immobile: m.unifiedCardHtml({json.dumps(VOCI[0])}),
      contatto: m.unifiedCardHtml({json.dumps(VOCI[1])}),
      contattoGlobale: m.unifiedCardHtml({json.dumps(VOCI[1])}, {{ scope: 'platform' }}),
      edificio: m.unifiedCardHtml({json.dumps(VOCI[2])}, {{ scope: 'platform' }}),
      relazioni: m.relationsHtml({json.dumps(RELAZIONI)}),
      nessuna: m.relationsHtml({{ relations: [] }}),
      richiesta: m.buildingRequestRowHtml({json.dumps(RICHIESTA)}),
      etichetta: m.kindLabel('buy_request'),
    }})""")
    assert out["tenant"] == "/api/trash?type=contact&q=Gino&deleted_by=3&limit=50&offset=50"     # mai l'agenzia
    assert out["tutti"] == "/api/trash"
    assert out["conti"] == "/api/trash/counts?q=a%26b"
    assert out["globale"] == "/api/platform/trash?date_from=2026-10-01&agency=2&limit=50&offset=0"
    p = out["pastiglie"]
    assert 'data-trash-count="all"' in p and "Tutti" in p and 'data-trash-count="contact"' in p
    assert re.search(r'class="chip trash-chip active"\s+data-trash-count="contact"', p)
    i = out["immobile"]
    assert 'data-unified-item="property:31"' in i and "data-trash-open" not in i          # la scheda immobile non si apre
    assert 'href="#/cestino/immobili" data-trash-manage' in i and "Duplicato — doppione" in i and "Olga" in i
    c = out["contatto"]
    assert "Gino &lt;b&gt;Cestino&lt;/b&gt;" in c and "<b>Cestino</b>" not in c               # sempre escaped
    assert 'href="#/contatti/41" data-trash-open' in c and 'data-trash-relations="contact:41"' in c
    e = out["edificio"]
    assert "data-trash-agency" in e and "Agenzia A" in e and "3 unità con l’edificio" in e
    assert "data-trash-manage" not in e                                                       # vista globale: nessun «Gestisci»
    assert "data-trash-open" not in e and "data-trash-open" not in out["contattoGlobale"]    # ... ne' «Apri scheda»
    assert "Consensi (registro)" in out["relazioni"] and "(2)" in out["relazioni"]
    assert "Nessun collegamento" in out["nessuna"]
    assert "Palazzina 7" in out["richiesta"] and "In attesa" in out["richiesta"] and 'href="#/edifici/7"' in out["richiesta"]
    assert out["etichetta"] == "Richieste acquirente"


@node
def test_m02_scheda_iniziale():
    out = _modello("""({
      vuoto: m.initialTrashTab('#/cestino'), tutti: m.initialTrashTab('#/cestino/tutti'),
      immobili: m.initialTrashTab('#/cestino/immobili'), richieste: m.initialTrashTab('#/cestino/richieste'),
      re: m.initialTrashTab('#/cestino/richieste-edifici'),
      platform: m.initialTrashTab('#/cestino/immobili', { platformOnly: true }),
      platformRe: m.initialTrashTab('#/cestino/richieste-edifici', { platformOnly: true }),
    })""", CESTINO)
    assert out == {"vuoto": "tutti", "tutti": "tutti", "immobili": "immobili", "richieste": "richieste",
                   "re": "richieste-edifici", "platform": "tutti", "platformRe": "richieste-edifici"}


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

def test_s01_rotte_e_nessuna_scrittura_nuova():
    rotte = re.compile(r"/api/(platform/)?trash\b")
    fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js")
                   if rotte.search(_codice(p)) and p.name != "trash-api.js")
    assert fuori == ["trash/trash-unified.js"], fuori              # solo il costruttore del percorso
    api = _codice(ASSETS / "trash" / "trash-api.js")
    nuove = api[api.index("export function listUnifiedTrash"):]
    assert "apiPost" not in nuove and "apiPatch" not in nuove and "apiDelete" not in nuove
    for p in (UNIFIED, CESTINO):
        testo = _codice(p)
        for vietata in ("Svuota", "purge", "Elimina definitivamente", "agency_id", "getSession", "is_platform_admin"):
            assert vietata not in testo, (p.name, vietata)


def test_s02_rete_porta_al_cestino_senza_duplicare():
    rete = _codice(ASSETS / "views" / "rete.js")
    assert "renderEliminazioni" not in rete and "#/cestino/richieste-edifici" in rete
    cestino = _codice(CESTINO)
    assert cestino.count("renderEliminazioni(") == 1
    main = _codice(ASSETS / "main.js")
    assert "const SEZIONI_ANCHE_PLATFORM = new Set(['cestino']);" in main
    assert "if (SEZIONI_ANCHE_PLATFORM.has(name)) return null;" in main


def test_s04_app_reale_solo_letture_e_mai_anonimo():
    """L'app VERA (main.py con le due righe autorizzate): sotto /api/trash e
    /api/platform/trash solo GET; senza sessione, o con solo HTTP Basic, 401."""
    import base64

    from fastapi.testclient import TestClient

    import main
    rotte = sorted((m.upper(), p) for p, ops in main.app.openapi()["paths"].items()
                   if p == "/api/trash" or p.startswith(("/api/trash/", "/api/platform/trash")) for m in ops)
    assert rotte == [("GET", "/api/platform/trash"), ("GET", "/api/platform/trash/counts"),
                     ("GET", "/api/platform/trash/{kind}/{entity_id}/relations"),
                     ("GET", "/api/trash"), ("GET", "/api/trash/building-requests"),
                     ("GET", "/api/trash/counts"), ("GET", "/api/trash/{kind}/{entity_id}/relations")], rotte
    client = TestClient(main.app)
    basic = {"Authorization": "Basic " + base64.b64encode(b"x:y").decode()}
    for _m, percorso in rotte:
        url = percorso.replace("{kind}", "property").replace("{entity_id}", "1")
        assert client.get(url).status_code == 401, url
        assert client.get(url, headers=basic).status_code == 401, url


@node
def test_s03_node_check():
    for p in (UNIFIED, CESTINO, ASSETS / "trash" / "trash-api.js", ASSETS / "views" / "rete.js", ASSETS / "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(p)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (p.name, esito.stderr)


# ---------------------------------------------------------------------------
# D - Shell vera nello stub DOM
# ---------------------------------------------------------------------------

def _rotte(sessione="agency_owner", *, lista=None, conti=None, extra=()):
    lista = lista if lista is not None else [rt.ok({"items": VOCI, "has_more": False, "limit": 50, "offset": 0})]
    conti = conti if conti is not None else [rt.ok(CONTI)]
    sess = rt.ok(rt.PLATFORM_ADMIN) if sessione == "platform" else rt.ok(a30_13b._sessione(sessione))
    voci = [
        ("GET", "/api/operator-auth/me", [sess]),
        *extra,
        ("GET", "/api/trash/counts", conti),
        ("GET", "/api/trash/contact/41/relations", [rt.ok(RELAZIONI)]),
        ("GET", "/api/trash/building-requests", [rt.ok({"items": [RICHIESTA], "has_more": False})]),
        ("GET", "/api/trash", lista),
        ("GET", "/api/property/trash", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const P = (n) => C().querySelector(`[data-trash-panel="${n}"]`);
const getUrls = () => __calls.map((c) => c.url);
"""


def _run(staged, scenario, rotte, hash="#/cestino"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_tutti_ricerca_pastiglie_collegamenti(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelectorAll('[data-trash-tab]').map((b) => b.dataset.trashTab);
      const visibile = !P('tutti').hidden && P('immobili').hidden;
      const carte = C().querySelectorAll('[data-unified-item]').map((c) => c.dataset.unifiedItem);
      const conti = C().querySelector('[data-unified-counts]').visibleText();
      const form = C().querySelector('[data-unified-filters]');
      form.querySelector('[name="q"]').value = 'gino';
      form.dispatch('submit'); await wait(); await wait();
      C().querySelector('[data-trash-count="contact"]').dispatch('click'); await wait(); await wait();
      C().querySelector('[data-trash-relations="contact:41"]').dispatch('click'); await wait(); await wait();
      const carta = C().querySelectorAll('[data-unified-item]').find((c) => c.dataset.unifiedItem === 'contact:41');
      const rel = carta.querySelector('[data-trash-relations-host]').visibleText();
      report({ tabs, visibile, carte, conti, rel, urls: getUrls() });
    """
    out = _run(staged, scenario, _rotte())
    assert out["tabs"] == ["tutti", "immobili", "contatti", "edifici", "richieste", "richieste-edifici"]
    assert out["visibile"] is True
    assert out["carte"] == ["property:31", "contact:41", "building:9"]
    assert "Tutti" in out["conti"] and "Contatti" in out["conti"]
    assert "/api/trash?q=gino&limit=50&offset=0" in out["urls"]
    assert "/api/trash?type=contact&q=gino&limit=50&offset=0" in out["urls"]
    assert "Consensi (registro)" in out["rel"]
    assert not [u for u in out["urls"] if u.startswith("/api/platform")]
    assert not any("agency" in u for u in out["urls"] if u.startswith("/api/trash"))
    assert _scritture(out) == []


@node
def test_d02_server_senza_cestino_unico(staged):  # noqa: F811
    assente = {"status": 404, "body": {"detail": "Not Found"}}
    scenario = r"""
      await wait(); await wait(); await wait();
      report({ avviso: P('tutti').visibleText(), immobili: !P('immobili').hidden,
               urls: getUrls() });
    """
    out = _run(staged, scenario, _rotte(lista=[assente], conti=[assente]))
    assert "non è ancora attiva" in out["avviso"] and out["immobili"] is True
    assert any(u.startswith("/api/property/trash") for u in out["urls"])
    assert _scritture(out) == []


@node
def test_d03_vista_globale_super_admin(staged):  # noqa: F811
    globale = [
        ("GET", "/api/platform/me", [rt.ok(rt.PLATFORM_ME)]),
        ("GET", "/api/platform/agencies", [rt.ok(rt.AGENZIE)]),
        ("GET", "/api/platform/trash/counts", [rt.ok({**CONTI, "scope": "platform"})]),
        ("GET", "/api/platform/trash", [rt.ok({"items": VOCI, "has_more": False})]),
    ]
    scenario = r"""
      await wait(); await wait(); await wait();
      const nav = __dom.byId['nav'].querySelectorAll('[data-route]').map((b) => b.dataset.route);
      const tabs = C().querySelectorAll('[data-trash-tab]').map((b) => b.dataset.trashTab);
      const agenzia = C().querySelector('[data-unified-agency]');
      const opzioni = agenzia.querySelectorAll('option').map((o) => o.visibleText());
      agenzia.value = '7';
      agenzia.dispatch('change'); await wait(); await wait();
      const carta = C().querySelector('[data-unified-item="building:9"]').visibleText();
      report({ nav, tabs, opzioni, carta, urls: getUrls(), hash: window.location.hash });
    """
    out = _run(staged, scenario, _rotte("platform", extra=globale))
    assert out["nav"] == ["cestino", "rete"], out["nav"]
    assert out["hash"].startswith("#/cestino")
    assert out["tabs"] == ["tutti", "richieste-edifici"]                  # le schede di categoria richiedono un'agenzia
    assert out["opzioni"] == ["Tutte le agenzie", "STIMA360", "Agenzia Alba"]
    assert "/api/platform/trash?agency=7&limit=50&offset=0" in out["urls"]
    assert "Agenzia A" in out["carta"]
    assert not [u for u in out["urls"] if u.startswith("/api/trash") or u.startswith("/api/property")]
    assert _scritture(out) == []


@node
def test_d04_richieste_edifici_agenzia_e_403_globale(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const testo = P('richieste-edifici').visibleText();
      report({ testo, carte: C().querySelectorAll('[data-building-request]').length, urls: getUrls() });
    """
    out = _run(staged, scenario, _rotte("agent"), "#/cestino/richieste-edifici")
    assert out["carte"] == 1 and "Palazzina 7" in out["testo"] and "In attesa" in out["testo"]
    assert "decide il Super Admin" in out["testo"]
    assert "/api/trash/building-requests?status=pending&limit=50&offset=0" in out["urls"]
    assert not [u for u in out["urls"] if u.startswith("/api/platform")]
    # un amministratore di piattaforma (in acting) che sceglie la vista globale e non e' il designato: solo l'avviso
    vietato = {"status": 403, "body": {"detail": "Operazione riservata al Super Admin generale designato della piattaforma",
                                       "code": "SUPER_ADMIN_REQUIRED"}}
    scenario = r"""
      await wait(); await wait();
      C().querySelector('[data-unified-scope-choice="platform"]').dispatch('click'); await wait(); await wait(); await wait();
      report({ errore: C().querySelector('[data-unified-error]').visibleText(),
               carte: C().querySelectorAll('[data-unified-item]').length });
    """
    out = _run(staged, scenario, _rotte("supreme", extra=[
        ("GET", "/api/platform/agencies", [rt.ok(rt.AGENZIE)]),
        ("GET", "/api/platform/trash", [vietato])]))
    assert "Super Admin generale designato" in out["errore"] and out["carte"] == 0
    assert _scritture(out) == []
