"""STIMA360 ZONA-6 - scheda rapida e filtri in BROWSER (Chromium) con backend e
PostgreSQL VERI (crm, core, property; mappa simulata come in T4).

Dati veri dalle rotte vere: una palazzina VERIFICATA con un venditore la cui
prossima azione e' scaduta e una telefonata registrata; una palazzina con
coordinate NON verificate; una palazzina verificata senza venditori.

  1. tocco sulla palazzina: scheda rapida con indirizzo, «Ultimo contatto»
     (la telefonata), «Prossima azione» (scaduta), «Apri scheda completa»
     -> #/edifici/{id}; nessuna scrittura;
  2. filtri: «Da ricontattare» lascia sulla mappa SOLO la palazzina del
     venditore in ritardo; «Da verificare» mostra il punto grigio della
     palazzina non verificata -> «Verifica posizione» -> pagina di
     posizionamento; «Tutti» torna alla mappa di sempre;
  3. a 390 px: filtri >= 44 px, nessuno scroll orizzontale; nessun errore JS.
"""
from __future__ import annotations

import threading
import time

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _edificio, _q  # noqa: F401
from tests.test_censimento_3_backend_postgres import completo, mondo  # noqa: F401  (fixture)
from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera
from tests.test_territorio_4_browser_postgres import FINTA, MAPPA

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

# la mappa finta impara i livelli (visibilita' e filtro), come MapLibre
LIVELLI = r"""
(() => {
  const P = window.maplibregl.Map.prototype;
  P.addLayer = function (l) { (this.layers ||= []).push({ ...l }); };
  P.getLayer = function (id) { return (this.layers || []).find((l) => l.id === id); };
  P.setLayoutProperty = function (id, k, v) { const l = this.getLayer(id); l.layout = { ...(l.layout || {}), [k]: v }; };
  P.setFilter = function (id, f) { this.getLayer(id).filter = f; };
})();
"""


@pytest.fixture
def server(mondo):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from acquisitions.router import router as acquisizioni
    from core.router import router as core_router
    from crm.router import router as crm_router
    from property.router import router as immobili

    sessione = {"user_id": mondo["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                "role": "agency_owner", "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}

    def contesto():
        mondo["stato"]["chi"] = "owner_a"
        return mondo["ctx"]()

    app = FastAPI()
    for r in (immobili, acquisizioni, core_router, crm_router):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto

    @app.get("/api/operator-auth/me")
    def me():
        return sessione

    app.mount("/os", StaticFiles(directory=str(SHELL), html=True), name="os-shell")
    porta = _porta_libera()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning"))
    filo = threading.Thread(target=srv.run, daemon=True)
    filo.start()
    scadenza = time.monotonic() + 20
    while not srv.started and time.monotonic() < scadenza:
        time.sleep(0.05)
    assert srv.started, "uvicorn non partito"
    try:
        yield {"url": f"http://127.0.0.1:{porta}", "http": TestClient(app), **mondo}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)
        # i venditori creati qui (il resto lo pulisce `mondo`)
        _q(mondo, "ALTER TABLE activities DISABLE TRIGGER trg_activities_property_history")
        _q(mondo, "DELETE FROM activities")
        _q(mondo, "ALTER TABLE activities ENABLE TRIGGER trg_activities_property_history")
        _q(mondo, "DELETE FROM tasks")
        _q(mondo, "DELETE FROM property_leads")
        _q(mondo, "DELETE FROM leads")
        _q(mondo, "DELETE FROM property_contacts")


def _dati(m):
    h = m["http"]
    a = _edificio(m, city="Alba Adriatica", address="Via Ricontatto", civic_number="5", name="Palazzina Ritardo")
    r = h.post(f"/api/property/buildings/{a['id']}/position", json={"latitude": "42.8341000", "longitude": "13.9262000"})
    assert r.status_code == 200 and r.json()["status"] == "verified", r.text
    b = _edificio(m, city="Alba Adriatica", address="Via Dubbia", civic_number="9", name="Casa Dubbia",
                  latitude="42.8300000", longitude="13.9300000", confirm_similar=True)
    c = _edificio(m, city="Alba Adriatica", address="Via Quieta", civic_number="1", name="Palazzina Quieta")
    r = h.post(f"/api/property/buildings/{c['id']}/position", json={"latitude": "42.8350000", "longitude": "13.9270000"})
    assert r.status_code == 200, r.text
    # un'unita' della palazzina, presa in carico, con il suo venditore in ritardo e una telefonata
    u = h.post("/api/property/census/units", json={"building_id": a["id"], "floor": "1", "internal_number": "1"})
    assert u.status_code == 201, u.text
    u = h.post(f"/api/property/properties/{u.json()['id']}/take-in-charge", json={"include_pertinenze": True}).json()
    mario = _q(m, "INSERT INTO contacts (agency_id, display_name, phone) VALUES (1, 'Mario Ritardo', '+39 333 1111111') RETURNING id")[0][0]
    r = h.post(f"/api/property/properties/{u['id']}/contacts", json={"contact_id": mario, "role": "owner"})
    assert r.status_code in (200, 201), r.text
    lead = h.post("/api/crm/sellers", json={"property_id": u["id"], "contact_id": mario})
    assert lead.status_code == 201, lead.text
    lead = lead.json()["lead_id"]
    r = h.post("/api/core/tasks", json={"lead_id": lead, "contact_id": mario, "title": "Richiamare Mario", "due_at": "2020-01-01T09:00:00+01:00"})
    assert r.status_code in (200, 201), r.text
    r = h.post(f"/api/property/properties/{u['id']}/interactions",
               json={"interaction_type": "call", "note": "Sentito", "contact_id": mario, "lead_id": lead, "context": "seller"})
    assert r.status_code == 201, r.text
    oggi = _q(m, "SELECT to_char(occurred_at AT TIME ZONE 'Europe/Rome', 'DD/MM/YYYY') FROM activities WHERE id = %s", (r.json()["id"],))[0][0]
    return a, b, c, oggi


def test_zona6_scheda_rapida_e_filtri_sul_server_vero(server):
    from playwright.sync_api import sync_playwright
    m = server
    a, b, c, oggi = _dati(m)
    url = m["url"]
    scritture = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 390, "height": 820}).new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("request", lambda r: scritture.append((r.method, r.url)) if r.method in ("POST", "PATCH", "PUT", "DELETE") else None)
        page.add_init_script(FINTA)
        page.add_init_script(LIVELLI)
        page.goto(f"{url}/os/#/edifici/mappa")
        page.wait_for_selector("canvas.finta")
        page.wait_for_function(f"{MAPPA}.sources['stima-buildings'] && document.querySelector('[data-zone-filter=recontact]').textContent.includes('·')")
        filtri = page.evaluate("""() => ({ testi: [...document.querySelectorAll('[data-zone-filter]')].map((b) => b.textContent),
            tocco: Math.min(...[...document.querySelectorAll('[data-zone-filter]')].map((b) => b.getBoundingClientRect().height)),
            scroll: document.documentElement.scrollWidth <= innerWidth })""")

        # 1. scheda rapida della palazzina del venditore in ritardo
        page.evaluate(f"{MAPPA}.fire('click:stima-buildings-points', {{ features: [{{ id: {a['id']} }}] }})")
        page.wait_for_selector(f".riquadro-finto[data-popup-building='{a['id']}']")
        scheda = page.evaluate("""() => { const r = document.querySelector('.riquadro-finto');
            return { dove: r.querySelector('.territorio-popup-where').textContent,
                     fatti: [...r.querySelectorAll('[data-quick-fact]')].map((d) => [d.querySelector('dt').textContent, d.querySelector('dd').textContent]),
                     bottoni: [...r.querySelectorAll('button')].map((b) => b.textContent) }; }""")
        # la palazzina quieta: nessun contatto, nessuna azione -> solo indirizzo
        page.evaluate(f"{MAPPA}.fire('click:stima-buildings-points', {{ features: [{{ id: {c['id']} }}] }})")
        page.wait_for_selector(f".riquadro-finto[data-popup-building='{c['id']}']")
        quieta = page.evaluate("() => document.querySelectorAll('.riquadro-finto [data-quick-fact]').length")
        page.click(f".riquadro-finto [data-open-building='{c['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/{c['id']}'")

        # 2. filtri
        page.goto(f"{url}/os/#/edifici/mappa")
        page.wait_for_function(f"{MAPPA} && {MAPPA}.sources['stima-buildings'] && document.querySelector('[data-zone-filter=recontact]').textContent.includes('·')")
        page.click("[data-zone-filter='recontact']")
        ricontatto = page.evaluate(f"""() => ({{ filtro: {MAPPA}.getLayer('stima-buildings-points').filter,
            nota: document.querySelector('[data-zone-filter-note]').textContent }})""")
        page.click("[data-zone-filter='verify']")
        page.wait_for_function(f"!!{MAPPA}.sources['stima-buildings-unverified']")
        verifica = page.evaluate(f"""() => ({{ punti: {MAPPA}.sources['stima-buildings-unverified'].data.features.map((f) => f.id),
            grigio: {MAPPA}.getLayer('stima-buildings-unverified-points').layout.visibility,
            blu: {MAPPA}.getLayer('stima-buildings-points').layout.visibility }})""")
        page.evaluate(f"{MAPPA}.fire('click:stima-buildings-unverified-points', {{ features: [{{ id: {b['id']} }}] }})")
        page.wait_for_selector(f".riquadro-finto [data-verify-building='{b['id']}']")
        page.click(f".riquadro-finto [data-verify-building='{b['id']}']")
        page.wait_for_function(f"location.hash === '#/edifici/mappa/posiziona/{b['id']}'")
        page.goto(f"{url}/os/#/edifici/mappa")
        page.wait_for_function(f"{MAPPA} && {MAPPA}.sources['stima-buildings']")
        page.click("[data-zone-filter='verify']")
        page.click("[data-zone-filter='all']")
        tutti = page.evaluate(f"""() => ({{ filtro: {MAPPA}.getLayer('stima-buildings-points').filter || null,
            blu: ({MAPPA}.getLayer('stima-buildings-points').layout || {{}}).visibility,
            nota: document.querySelector('[data-zone-filter-note]').hidden }})""")
        browser.close()

    assert errori == [], errori
    assert filtri["testi"] == ["Tutti · 2", "Da verificare · 1", "Da ricontattare · 1"]
    assert filtri["tocco"] >= 43.5 and filtri["scroll"] is True
    assert scheda["dove"] == "Via Ricontatto 5 · Alba Adriatica"
    assert scheda["fatti"] == [["Ultimo contatto", f"Telefonata, {oggi}"], ["Prossima azione", "Richiamare Mario, 01/01/2020 (scaduta)"]]
    assert scheda["bottoni"] == ["Apri scheda completa"] and quieta == 0
    assert ricontatto["filtro"] == ["in", ["get", "building_id"], ["literal", [a["id"]]]]
    assert "1 da ricontattare sulla mappa" in ricontatto["nota"]
    assert verifica == {"punti": [b["id"]], "grigio": "visible", "blu": "none"}
    assert tutti == {"filtro": None, "blu": "visible", "nota": True}
    assert scritture == []                                                              # solo letture dal browser
