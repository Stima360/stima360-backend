"""STIMA VOICE V9 - incarichi a voce in BROWSER (Chromium) con backend, PostgreSQL e Shell VERI.

Il fornitore AI e' quello finto: ogni sua chiamata si conta (`finto.calls`).

  b1  «Crea un incarico di vendita per l'appartamento di Francesco Ruvolo, prezzo
      185.000 euro» -> scheda (proprietario, immobile, prezzo concordato, tipologia,
      stato effettivo, dati mancanti) -> «Da quando decorre?» -> «oggi» capito senza
      il modello -> l'operazione detta per esteso («INCARICO EFFETTIVO», nessuna
      firma) -> DOPPIO tocco su «Conferma ed esegui» -> UN solo incarico reale ->
      «Apri incarico» porta alla scheda dell'incarico
  b2  «Portami alla scheda dell'incarico di Luca Gialli» -> la pagina apre la scheda
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_8_0_browser_postgres import pagina, server  # noqa: F401
from tests.test_voice_9_mandates_postgres import FRASE, RUVOLO, _seme  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def test_b1_dalla_frase_all_incarico_reale(pagina):  # noqa: F811
    page, finto, ids = pagina["page"], pagina["finto"], pagina["ids"]
    # un solo immobile di Ruvolo pronto: via Dante (prima del sopralluogo) qui non c'entra
    q(pagina, "DELETE FROM property_contacts WHERE property_id = %s AND contact_id = %s", (ids["dante"], ids["ruvolo"]))
    try:
        finto.register(FRASE, {"commands": [{"intent": "mandate_prepare", "quote": FRASE, "owner": RUVOLO,
                                             "price_text": "185.000 euro"}], "clarifications": []})
        page.fill("[data-voice-text]", FRASE)
        page.click("[data-voice-send]")
        page.wait_for_selector("[data-voice-summary='1']")
        scheda = page.inner_text("[data-voice-summary='1']")
        for atteso in ("Francesco Ruvolo", "Via Sanzio 14", "Prezzo concordato (incarico)", "185.000 €", "Vendita",
                       "Stato effettivo", "Sopralluogo effettuato", "Data di inizio"):
            assert atteso in scheda, (atteso, scheda)
        assert "Da quando decorre l'incarico?" in page.inner_text("[data-voice-result]")
        assert page.query_selector("[data-voice-confirm]") is None                 # manca un dato: nessuna conferma
        chiamate = len(finto.calls)
        cid = page.get_attribute("[data-voice-command]", "data-voice-command")
        page.fill("[data-voice-reply-text]", "oggi")
        page.click("[data-voice-reply-send]")
        page.wait_for_function(f"document.querySelector('[data-voice-command]').dataset.voiceCommand !== '{cid}'")
        assert len(finto.calls) == chiamate                                        # «oggi» senza il modello
        risultato = page.inner_text("[data-voice-result]")
        assert "INCARICO EFFETTIVO" in risultato and "Vuoi utilizzare questo immobile?" in risultato
        assert "firmato" not in risultato.lower()
        assert date.today().strftime("%d/%m/%Y") in page.inner_text("[data-voice-summary='1']")
        assert q(pagina, "SELECT acquisition_id FROM properties WHERE id = %s", (ids["sanzio"],))[0][0] is None
        # doppio tocco: una sola richiesta, un solo incarico
        page.evaluate("() => { const b = document.querySelector('[data-voice-confirm]'); b.click(); b.click(); }")
        page.wait_for_selector("[data-voice-open-mandate]", timeout=15000)
        href = page.get_attribute("[data-voice-open-mandate]", "href")
        assert href == f"#/incarichi/{ids['sanzio']}"
        assert list(q(pagina, "SELECT acquisition_id, commercial_status FROM properties WHERE id = %s",
                      (ids["sanzio"],))[0]) == [ids["acq_sanzio"], "mandate"]
        assert q(pagina, "SELECT count(*) FROM acquisition_events WHERE acquisition_id = %s "
                         "AND event_type = 'mandate_created'", (ids["acq_sanzio"],))[0][0] == 1
        assert "Incarico generato." in page.inner_text("[data-voice-result]")
        assert page.inner_text("[data-voice-error]") == "" and pagina["errori"] == [], pagina["errori"]
    finally:
        q(pagina, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner') "
                  "ON CONFLICT DO NOTHING", (ids["dante"], ids["ruvolo"]))


def test_b2_portami_alla_scheda(pagina):  # noqa: F811
    page, finto, ids = pagina["page"], pagina["finto"], pagina["ids"]
    testo = "Portami alla scheda dell'incarico di Luca Gialli"
    finto.register(testo, {"commands": [{"intent": "mandate_info", "quote": testo, "purpose": "open",
                                         "owner": {"first_name": "Luca", "last_name": "Gialli"}}],
                           "clarifications": []})
    page.fill("[data-voice-text]", testo)
    page.click("[data-voice-send]")
    page.wait_for_function(f"location.hash === '#/incarichi/{ids['mare']}'", timeout=10000)
