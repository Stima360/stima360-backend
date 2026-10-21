"""INFORMATIVE PRIVACY (Super Admin › Rete) - prove nel BROWSER e via API,
su PostgreSQL usa-e-getta.

Riusa `browser_server` di ADM-3 (sessioni vere, router Platform vero,
Super Admin designato via `STIMA360_SUPER_ADMIN_USER_ID`), con la Shell
servita da `/os/`. Chromium a 390 px (smartphone) e 1280 px.

  01  la scheda «Informative privacy»: senza versione corrente l'avviso; la
      pubblicazione di una versione (testo scritto a mano, con markup che
      NON viene interpretato); il rifiuto comprensibile di una seconda
      corrente; la lettura del testo integrale; la chiusura con conferma
      (prima annullata, poi confermata); l'audit nel Registro; nessun
      errore di pagina e nessuno sforamento orizzontale.
  02  le API dicono di no a chi non e' il Super Admin designato: un altro
      platform admin (403 + riga `denied`) e un agente (403); nulla scritto.

Nessun consenso viene registrato e nessuna email parte: le informative sono
solo il testo che i consensi citano.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from tests.test_adm1_super_admin_postgres import _impronta, adm1  # noqa: F401
from tests.test_adm2_impact_postgres import adm2  # noqa: F401
from tests.test_adm3_continuity_postgres import browser_server  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

TESTO = ("Informativa marketing di prova, scritta a mano dal Super Admin.\n"
         "Seconda riga con <b>markup</b> che deve restare testo.\n"
         "Terza riga: il consenso e' revocabile in ogni momento.")


@pytest.fixture
def informative(browser_server):
    """Nessuna versione marketing corrente all'inizio (come nelle prove della
    Fase B). `consent_notices` e' un REGISTRO (061): niente DELETE, niente
    riapertura; l'unico UPDATE ammesso e' il ritiro. Le versioni di prova
    portano un prefisso unico e, se una resta aperta, alla fine si ritira."""
    m = browser_server
    _q(m, "UPDATE consent_notices SET valid_to = NOW() WHERE purpose = 'marketing' AND valid_to IS NULL")
    prefisso = f"ui-{uuid.uuid4().hex[:6]}"
    yield {**m, "prefisso": prefisso}
    _q(m, "UPDATE consent_notices SET valid_to = NOW() WHERE version LIKE %s AND valid_to IS NULL", (prefisso + "%",))


def _riga(m, versione):
    return _q(m, "SELECT id, content, content_hash, created_by, valid_to, content_available FROM consent_notices "
                 "WHERE version = %s", (versione,))


@pytest.mark.parametrize("larghezza", [390, 1280])
def test_01_la_scheda_informative_in_chromium(informative, larghezza):
    from playwright.sync_api import sync_playwright
    from tests.test_eliminazione_edificio_1_browser_postgres import _sfora
    m = informative
    versione = f"{m['prefisso']}-{larghezza}"
    prima = _impronta(m)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": larghezza, "height": 900})
        ctx.add_cookies([{"name": m["cookie"], "value": m["token"], "url": m["url"]}])
        page = ctx.new_page()
        errori = []
        page.on("pageerror", lambda e: errori.append(str(e)))

        page.goto(f"{m['url']}/os/?n={uuid.uuid4().hex[:8]}#/rete")
        page.click("#rete-tabs [data-tab='informative']")
        page.wait_for_selector("#informative-lista table, #informative-lista .muted:not(:has-text('Caricamento'))")
        page.wait_for_selector("[data-nessuna-corrente]")
        assert "Nessuna versione corrente" in page.inner_text("[data-nessuna-corrente]")
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)

        # --- pubblicazione: il testo e' scritto a mano, niente e' precompilato
        page.click("#informativa-nuova")
        page.wait_for_selector("[data-informativa-form]")
        assert page.input_value("[data-informativa-form] [name='content']") == ""
        assert page.input_value("[data-informativa-form] [name='version']") == ""
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)
        page.fill("[data-informativa-form] [name='version']", versione)
        page.fill("[data-informativa-form] [name='context']", "modulo cartaceo in agenzia")
        page.fill("[data-informativa-form] [name='content']", TESTO)
        page.click("[data-informativa-form] button[type='submit']")
        page.wait_for_selector(f"#informative-lista tr:has-text('{versione}')")
        assert page.query_selector("[data-informativa-form]") is None
        assert page.query_selector("[data-nessuna-corrente]") is None
        riga = page.inner_text(f"#informative-lista tr:has-text('{versione}')")
        assert "Corrente" in riga and "Chiudi versione" in riga, riga
        db = _riga(m, versione)
        assert len(db) == 1 and db[0][1] == TESTO and db[0][5] is True
        assert db[0][2] == hashlib.sha256(TESTO.encode("utf-8")).hexdigest()
        assert db[0][3] == f"operator:{m['persone']['super']}", db[0][3]
        assert db[0][4] is None
        notice_id = db[0][0]

        # --- una seconda corrente: rifiuto comprensibile, nulla scritto
        page.click("#informativa-nuova")
        page.wait_for_selector("[data-informativa-form]")
        page.fill("[data-informativa-form] [name='version']", versione + "-bis")
        page.fill("[data-informativa-form] [name='context']", "sito")
        page.fill("[data-informativa-form] [name='content']", TESTO)
        page.click("[data-informativa-form] button[type='submit']")
        page.wait_for_selector("[data-informativa-errore]:not(:empty)")
        errore = page.inner_text("[data-informativa-errore]")
        assert "versione corrente" in errore and "chiudila prima" in errore, errore
        assert not _riga(m, versione + "-bis")
        page.click("[data-informativa-form] [data-azione='annulla']")
        assert page.query_selector("[data-informativa-form]") is None

        # --- lettura del testo integrale: il markup resta testo
        page.click(f"#informative-lista tr:has-text('{versione}') td")
        page.wait_for_selector(f"[data-informativa-dettaglio='{notice_id}']")
        assert page.text_content("[data-informativa-testo]") == TESTO
        assert page.query_selector("[data-informativa-testo] b") is None
        assert "&lt;b&gt;" in page.inner_html("[data-informativa-testo]")
        assert db[0][2] in page.inner_text(f"[data-informativa-dettaglio='{notice_id}']")
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)
        page.click("[data-azione='chiudi-dettaglio']")
        assert page.query_selector("[data-informativa-dettaglio]") is None

        # --- chiusura: conferma annullata, poi confermata
        page.click(f"[data-azione='chiudi'][data-informativa='{notice_id}']")
        page.wait_for_selector("dialog.modal [data-conferma='si']")
        dialogo = page.inner_text("dialog.modal")
        assert f"Chiudere la versione {versione}?" in dialogo
        assert "non sara’ piu’ valida per nuovi consensi" in dialogo, dialogo
        assert "Chiudi la versione" in dialogo
        page.click("dialog.modal [data-conferma='no']")
        page.wait_for_selector("dialog.modal", state="detached")
        assert _riga(m, versione)[0][4] is None, "annullare non chiude"
        assert "Corrente" in page.inner_text(f"#informative-lista tr:has-text('{versione}')")

        page.click(f"[data-azione='chiudi'][data-informativa='{notice_id}']")
        page.wait_for_selector("dialog.modal [data-conferma='si']")
        page.click("dialog.modal [data-conferma='si']")
        page.wait_for_selector("dialog.modal", state="detached")
        page.wait_for_selector(f"#informative-lista tr:has-text('{versione}'):has-text('Chiusa')")
        page.wait_for_selector("[data-nessuna-corrente]")
        assert _riga(m, versione)[0][4] is not None
        assert page.query_selector(f"[data-azione='chiudi'][data-informativa='{notice_id}']") is None

        # --- il Registro mostra le due operazioni, con l'etichetta
        page.click("#rete-tabs [data-tab='registro']")
        page.wait_for_selector("#registro-lista table")
        registro = page.inner_text("#registro-lista")
        assert "Informativa pubblicata" in registro and "Informativa chiusa" in registro, registro[:500]
        assert f"Informativa #{notice_id}" in registro, registro[:500]
        assert _sfora(page)["scroll"] <= larghezza + 1, _sfora(page)

        browser.close()
    assert errori == [], errori
    audit = _q(m, "SELECT action, actor_user_id, result FROM platform_audit_log "
                  "WHERE target_type = 'consent_notice' AND target_id = %s ORDER BY id", (str(notice_id),))
    assert [(a, u, r) for a, u, r in audit] == [
        ("platform.consent_notice.create", m["persone"]["super"], "success"),
        ("platform.consent_notice.close", m["persone"]["super"], "success"),
    ], audit
    assert _impronta(m) == prima, "nessun dato operativo toccato"
    assert not _q(m, "SELECT 1 FROM consent_events WHERE notice_id = %s", (notice_id,)), "nessun consenso registrato"


def test_02_le_api_dicono_di_no_a_chi_non_e_il_super_admin_designato(informative):
    m = informative
    versione = f"{m['prefisso']}-api"
    corpo = {"purpose": "marketing", "version": versione, "context": "crm", "content": TESTO,
             "valid_from": "2026-10-20T10:00:00+02:00", "valid_to": None}
    for chi, atteso in (("altro", 403), ("agente", 403)):
        client = m["accedi"](chi)
        assert client.get("/api/platform/consent-notices").status_code == atteso, chi
        r = client.post("/api/platform/consent-notices", json=corpo)
        assert r.status_code == atteso, (chi, r.text)
        assert "Super Admin" in r.text or "platform" in r.text.lower() or "riservat" in r.text.lower(), r.text
    assert not _riga(m, versione), "nessuna informativa scritta da chi non puo'"
    negate = _q(m, "SELECT count(*) FROM platform_audit_log WHERE actor_user_id = %s AND result = 'denied' "
                   "AND action = 'platform.super_admin.required'", (m["persone"]["altro"],))[0][0]
    assert negate >= 1, "l'altro platform admin lascia una riga `denied`"

    sa = m["accedi"]("super")
    r = sa.post("/api/platform/consent-notices", json=corpo)
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == f"operator:{m['persone']['super']}"
    r2 = sa.post("/api/platform/consent-notices", json={**corpo, "version": versione + "-2"})
    assert r2.status_code == 409 and "corrente" in r2.json()["detail"], r2.text
    r3 = sa.post(f"/api/platform/consent-notices/{r.json()['id']}/close", json={})
    assert r3.status_code == 200 and r3.json()["valid_to"] is not None
    r4 = sa.post(f"/api/platform/consent-notices/{r.json()['id']}/close", json={})
    assert r4.status_code == 409, r4.text
