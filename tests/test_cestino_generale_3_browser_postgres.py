"""CESTINO-GENERALE-3 (CG-3) - cancellazione definitiva dal Cestino unico, in
BROWSER (Chromium) con backend e PostgreSQL VERI (database locale usa-e-getta).

La Shell vera servita da uvicorn con i router veri (Immobili, CORE,
Acquirenti, Platform: lo stesso server di CG-2); sostituita solo
l'autenticazione: titolare dell'agenzia 1 e Super Admin generale designato.

A 390 px e 1280 px, vista globale del Super Admin:
  f01  immobile semplice: «Elimina definitivamente…» -> riepilogo -> conferma
       disabilitata finche' il testo non e' esatto -> cancellato; la card
       sparisce, la riga non c'e' piu';
  f02  contatto senza dipendenze: idem;
  f03  richiesta acquirente: idem; il suo contatto resta;
  f04  contatto proprietario di un immobile: blocco con «come risolvere»,
       nessuna conferma, nulla cambia.
Nessuno scorrimento orizzontale della pagina, nessun errore di pagina.
Screenshot in `CG3_SHOTS` (o /tmp/cg3_shots).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests.test_cestino_generale_2_browser_postgres import _vai, server  # noqa: F401
from tests.test_censimento_3_backend_postgres import _q
from tests.test_edifici_1_browser_postgres import DSN, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_browser_postgres import _sfora

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

CONFERMA = "ELIMINA DEFINITIVAMENTE"


def _esiste(m, tabella, ident):
    return bool(_q(m, f"SELECT count(*) FROM {tabella} WHERE id = %s", (ident,))[0][0])


def _ok(r):
    assert r.status_code in (200, 201), r.text
    return r.json()


def _post(s, percorso, corpo):
    """Dal server avviato (titolare): le rotte vere di Immobili, CORE e Acquirenti."""
    import httpx
    return _ok(httpx.post(f"{s['url']}{percorso}", json=corpo, timeout=20))


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_f01_f04_cancellazione_definitiva_in_chromium(server, larghezza):  # noqa: F811
    from playwright.sync_api import sync_playwright
    s = server
    sfx = uuid.uuid4().hex[:6]
    api = s["api"]
    s["come"]["chi"] = "titolare"
    # dati propri (mai dati reali), spostati nel Cestino con le rotte vere dell'agenzia
    immobile = _ok(api().post("/api/property/properties", json={
        "title": f"Bilocale Purga{sfx}", "property_type": "apartment", "city": "Fermo",
        "address": f"Via Purga{sfx}", "civic_number": "1"}))["id"]
    contatto = _q(s, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (1, %s, 'Gino', %s) "
                     "RETURNING id", (f"Gino Purga{sfx}", f"Purga{sfx}"))[0][0]
    acquirente = _q(s, "INSERT INTO contacts (agency_id, display_name) VALUES (1, %s) RETURNING id",
                    (f"Bruna Purga{sfx}",))[0][0]
    richiesta = _q(s, "INSERT INTO buy_requests (agency_id, contact_id, title) VALUES (1, %s, %s) RETURNING id",
                   (acquirente, f"Trilocale Purga{sfx}"))[0][0]
    proprietario = _q(s, "INSERT INTO contacts (agency_id, display_name) VALUES (1, %s) RETURNING id",
                      (f"Piero Purga{sfx}",))[0][0]
    casa = _ok(api().post("/api/property/properties", json={
        "title": f"Casa di Piero {sfx}", "property_type": "apartment", "city": "Fermo",
        "address": f"Via Piero{sfx}", "civic_number": "2"}))["id"]
    _q(s, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (casa, proprietario))
    _post(s, f"/api/property/properties/{immobile}/trash", {"reason_code": "test_record"})
    for cid in (contatto, proprietario):
        _post(s, f"/api/core/contacts/{cid}/trash", {"reason_code": "invalid_data"})
    _post(s, f"/api/buy/requests/{richiesta}/trash", {"reason_code": "duplicate"})
    cartella = Path(os.environ.get("CG3_SHOTS") or (Path("/tmp") / "cg3_shots"))
    cartella.mkdir(parents=True, exist_ok=True)
    misure = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": larghezza, "height": 860})
        errori = []
        page.on("pageerror", lambda x: errori.append(str(x)))
        s["come"]["chi"] = "super"
        _vai(page, s["url"], "#/cestino")
        page.wait_for_selector("[data-unified-filters] [name='q']")
        page.fill("[data-unified-filters] [name='q']", f"Purga{sfx}")
        page.click("[data-unified-filters] button[type='submit']")
        carta = lambda k, i: f"[data-unified-item='{k}:{i}']"  # noqa: E731
        page.wait_for_selector(f"{carta('property', immobile)} [data-item-purge]")
        page.wait_for_load_state("networkidle")
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg3_elenco_{larghezza}.png"), full_page=True)
        # la zona e' separata dalle azioni della card (nessun «Ripristina» accanto)
        assert "Elimina definitivamente" not in page.inner_text(f"{carta('property', immobile)} .trash-card-actions")

        def elimina(kind, ident, foto):
            c = carta(kind, ident)
            page.click(f"{c} [data-item-purge]")
            page.wait_for_selector(f"{c} [data-item-purge-ready]")
            assert page.is_disabled(f"{c} [data-item-purge-confirm]")
            page.fill(f"{c} [data-item-purge-text]", "elimina")
            assert page.is_disabled(f"{c} [data-item-purge-confirm]")
            page.fill(f"{c} [data-item-purge-text]", CONFERMA)
            assert not page.is_disabled(f"{c} [data-item-purge-confirm]")
            misure.append(_sfora(page))
            page.screenshot(path=str(cartella / f"cg3_{foto}_{larghezza}.png"), full_page=True)
            page.click(f"{c} [data-item-purge-confirm]")
            page.wait_for_function(f"() => !document.querySelector(\"{c}\")")
            page.wait_for_load_state("networkidle")

        # --- f01: immobile semplice ---------------------------------------------------
        elimina("property", immobile, "immobile_riepilogo")
        assert not _esiste(s, "properties", immobile)
        assert "Eliminazione definitiva completata" in page.inner_text("body")
        # --- f02: contatto senza dipendenze ------------------------------------------
        page.wait_for_selector(f"{carta('contact', contatto)} [data-item-purge]")
        elimina("contact", contatto, "contatto_riepilogo")
        assert not _esiste(s, "contacts", contatto)
        # --- f03: richiesta acquirente; il contatto resta --------------------------------
        page.wait_for_selector(f"{carta('buy_request', richiesta)} [data-item-purge]")
        elimina("buy_request", richiesta, "richiesta_riepilogo")
        assert not _esiste(s, "buy_requests", richiesta) and _esiste(s, "contacts", acquirente)
        # --- f04: contatto proprietario: blocco con «come risolvere» -------------------
        c = carta("contact", proprietario)
        page.wait_for_selector(f"{c} [data-item-purge]")
        page.click(f"{c} [data-item-purge]")
        page.wait_for_selector(f"{c} [data-item-purge-blocked]")
        testo = page.inner_text(f"{c} [data-item-purge-host]")
        assert "Non si può eliminare definitivamente" in testo and "Come risolvere" in testo, testo
        assert page.query_selector(f"{c} [data-item-purge-confirm]") is None
        misure.append(_sfora(page))
        page.screenshot(path=str(cartella / f"cg3_bloccato_{larghezza}.png"), full_page=True)
        page.click(f"{c} [data-item-purge-close]")
        assert _esiste(s, "contacts", proprietario) and _esiste(s, "properties", casa)
        assert _q(s, "SELECT count(*) FROM property_contacts WHERE contact_id = %s", (proprietario,))[0][0] == 1
        browser.close()

    assert errori == [], errori
    for x in misure:
        assert x["scroll"] <= x["inner"] and x["fuori"] == [], x
    # il registro: un evento purge per ciascuno, nessun dato personale del contatto
    for kind, ident in (("property", immobile), ("contact", contatto), ("buy_request", richiesta)):
        assert _q(s, "SELECT count(*) FROM record_lifecycle_events WHERE entity_type = %s AND entity_id = %s "
                     "AND action = 'purge'", (kind, ident))[0][0] == 1
        assert _q(s, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.trash.purge' "
                     "AND target_type = %s AND target_id = %s AND result = 'success'", (kind, str(ident)))[0][0] == 1
    evento = _q(s, "SELECT before_state::text || metadata::text FROM record_lifecycle_events WHERE entity_type = "
                   "'contact' AND entity_id = %s AND action = 'purge'", (contatto,))[0][0]
    assert f"Purga{sfx}" not in evento and "Gino" not in evento
