"""EDIFICI-1 - prova in BROWSER (Chromium) con backend e PostgreSQL VERI.

Non e' uno stub: la Shell vera (`static/os_shell`) e servita da uvicorn
insieme al router vero degli immobili, sul database usa-e-getta della
fixture del censimento (schema completo). Sostituita SOLO l'autenticazione:
il contesto operatore e' quello del titolare dell'agenzia 1, e
`/api/operator-auth/me` risponde con la sua sessione. I dati si creano con le
API vere (edifici, unita', pertinenza, accessorio, archiviazione, Cestino).

Si misura, a 1280 px (desktop) e a 390 / 320 px (smartphone):
  * voce «Edifici» accanto a «Immobili» (sopra), lista con filtri e contatori veri;
  * filtro Comune -> Microzona e ricerca, ritorno dalla scheda con i filtri;
  * scheda edificio: dati, contatori, unita', archiviate; link all'unita';
  * scheda immobile: collegamento evidente all'edificio;
  * nessuno scorrimento orizzontale, controlli toccabili (>= 44 px) su mobile.
Gli screenshot finiscono in `EDIFICI1_SHOTS` (se impostata) o in tmp.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _edificio, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
SHELL = Path(__file__).resolve().parents[1] / "static" / "os_shell"


def _porta_libera():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(mondo):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from acquisitions.router import router as acquisizioni
    from core.router import router as core_router
    from match.router import router as abbinamenti
    from property.router import router as immobili
    from proposal.router import router as proposte
    from sale.router import router as vendite

    sessione = {"user_id": mondo["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                "role": "agency_owner", "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}

    def contesto():
        mondo["stato"]["chi"] = "owner_a"
        return mondo["ctx"]()

    app = FastAPI()
    for r in (immobili, acquisizioni, core_router, abbinamenti, proposte, vendite):   # cio' che la scheda immobile legge
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
        yield {"url": f"http://127.0.0.1:{porta}", **mondo}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


def _dati(m):
    """Due palazzine a Tortoreto (microzone diverse) e una ad Alba Adriatica,
    con unita', una pertinenza, un accessorio da chiarire, un'archiviata e
    una nel Cestino: tutto dalle API vere."""
    terr = {"region": "Abruzzo", "province": "TE"}
    a = _edificio(m, **terr, city="Tortoreto", microzone="Lido Nord", address="Via Roma", civic_number="10",
                  name="Residenza Gabbiano", units_declared=6)
    _edificio(m, **terr, city="Tortoreto", microzone="Alto", address="Contrada San Giovanni Battista Vecchia Strada Provinciale",
              civic_number="122/B", name=None, units_declared=None, units_declared_source=None, confirm_similar=True)
    c = _edificio(m, **terr, city="Alba Adriatica", microzone="Nord", address="Via Nazionale", civic_number="3",
                  name="Condominio Sole", units_declared=2)
    api = m["api"]()
    u1 = _unita(m, building_id=a["id"], floor="T", property_type="commercial", surface_sqm=45, cadastral_category="C/1")
    u2 = _unita(m, building_id=a["id"], floor="1", internal_number="1", staircase="A", surface_sqm=80)
    u3 = _unita(m, building_id=a["id"], floor="2", internal_number="2", staircase="B", surface_sqm=95, cadastral_category="A/2")
    _unita(m, building_id=a["id"], parent_property_id=u3["id"], property_type="garage", floor="-1", surface_sqm=18)
    arch = _unita(m, building_id=a["id"], floor="3", internal_number="3", staircase="A")
    cest = _unita(m, building_id=a["id"], floor="4", internal_number="4", staircase="A")
    assert api.post(f"/api/property/properties/{u2['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown"}).status_code == 201
    assert api.post(f"/api/property/properties/{arch['id']}/archive").status_code == 200
    assert api.post(f"/api/property/properties/{cest['id']}/trash", json={"reason_code": "created_by_mistake"}).status_code == 200
    for piano in ("1", "2", "3"):
        _unita(m, building_id=c["id"], floor=piano)
    return a, u1, u3


def _misura(page):
    return page.evaluate("""() => {
      const doc = document.documentElement;
      const fuori = [...document.querySelectorAll('.building-card, .building-facts, .building-counts, .census-unit-row, .buildings-toolbar, .property-building-link')]
        .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > window.innerWidth + 0.5)
        .map((e) => e.className);
      return { scroll: doc.scrollWidth, inner: window.innerWidth, fuori };
    }""")


@pytest.mark.parametrize("larghezza", [1280, 390, 320])
def test_b01_edifici_in_chromium_con_backend_vero(server, larghezza):
    from playwright.sync_api import sync_playwright
    a, u1, u3 = _dati(server)
    cartella = Path(os.environ.get("EDIFICI1_SHOTS") or (Path("/tmp") / "edifici1_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    mobile = larghezza < 768
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": larghezza, "height": 900 if not mobile else 780})
            errori = []
            page.on("pageerror", lambda e: errori.append(str(e)))
            page.goto(f"{server['url']}/os/#/edifici")
            page.wait_for_selector(".building-card")
            assert page.locator(".building-card").count() == 3
            voci = page.eval_on_selector_all("#nav [data-route]", "(els) => els.map((e) => e.dataset.route)")
            assert voci.index("edifici") == voci.index("immobili") - 1
            lista = _misura(page)
            testo = page.inner_text("#buildings-area")
            assert "Residenza Gabbiano" in testo and "Non note" in testo
            page.screenshot(path=str(cartella / f"edifici_lista_{larghezza}.png"), full_page=True)
            if mobile:
                altezze = page.evaluate("""() => ['#buildings-search', '#buildings-city', '#buildings-microzone', '#buildings-new']
                    .map((s) => document.querySelector(s).getBoundingClientRect().height)""")
                assert all(44 <= h <= 64 for h in altezze), altezze      # toccabili, e non giganti
            # filtri: Comune -> Microzona, poi ricerca
            page.select_option("#buildings-city", "Tortoreto")
            page.wait_for_function("() => !document.querySelector('#buildings-microzone').disabled")
            page.wait_for_function("() => document.querySelectorAll('.building-card').length === 2")
            page.select_option("#buildings-microzone", "Lido Nord")
            page.wait_for_function("() => document.querySelectorAll('.building-card').length === 1")
            page.fill("#buildings-search", "roma 10")
            page.wait_for_timeout(500)
            page.wait_for_function("() => document.querySelectorAll('.building-card').length === 1")
            page.screenshot(path=str(cartella / f"edifici_filtri_{larghezza}.png"), full_page=True)
            # scheda edificio
            page.click(f"[data-building-id='{a['id']}']")
            page.wait_for_selector("#building-facts dt")
            scheda = _misura(page)
            contatori = page.inner_text("#building-counters")
            assert "6" in contatori and "di cui 1 archiviata" in contatori
            assert page.locator("#building-units .census-unit-row").count() == 4      # Cestino escluso
            assert "IMM-" in page.text_content("#building-archived")       # chiusa: il testo c'e'
            page.screenshot(path=str(cartella / f"edificio_scheda_{larghezza}.png"), full_page=True)
            # ritorno: i filtri restano
            page.click("#building-back")
            page.wait_for_selector(".building-card")
            assert page.input_value("#buildings-search") == "roma 10"
            assert page.input_value("#buildings-city") == "Tortoreto"
            assert page.input_value("#buildings-microzone") == "Lido Nord"
            # unita' -> scheda immobile -> edificio
            page.click(f"[data-building-id='{a['id']}']")
            page.wait_for_selector(f"[data-open-unit='{u3['id']}']")
            page.click(f"[data-open-unit='{u3['id']}']")
            page.wait_for_selector("#property-building-link")
            immobile = _misura(page)
            page.screenshot(path=str(cartella / f"immobile_link_edificio_{larghezza}.png"))
            page.click("#property-building-link")
            page.wait_for_selector("#building-facts dt")
            assert page.url.endswith(f"#/edifici/{a['id']}")
            assert errori == [], errori
            browser.close()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium non disponibile: prova in browser NON eseguita (BLOCKED)")
        raise
    for misura in (lista, scheda, immobile):
        assert misura["scroll"] <= misura["inner"], misura
        assert misura["fuori"] == [], misura
