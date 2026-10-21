"""STIMA VOICE - collaudo 3 (21/10) in BROWSER (Chromium) con backend, PostgreSQL e Shell VERI.

Il fornitore AI e' quello finto: ogni sua chiamata si conta (`finto.calls`).

  s1  cambio di schermata: comando con una domanda aperta -> si esce dalla pagina
      e si rientra -> stesso comando, stessa domanda, nessuna nuova
      interpretazione (zero chiamate al fornitore, nessun POST); la risposta
      «domani» si capisce senza il modello; si esce e si rientra di nuovo -> la
      conversazione (comando + risposta) e' quella; «Conferma ed esegui» ->
      appuntamento in Agenda; rientrando non c'e' piu' nulla in corso
  s2  «Nuovo comando» su un comando aperto lo annulla davvero: rientrando non riappare
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_8_0_browser_postgres import pagina, server  # noqa: F401
from tests.test_voice_8_0_conversations_postgres import _persone  # noqa: F401
from tests.test_voice_8_2_visits_postgres import _incarichi  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

TESTO = "Telefonata con Giorgio Censori prossimamente alle 10:30"
OUTPUT = {"commands": [{"intent": "create_appointment", "quote": TESTO, "appointment_type": "call",
                        "when": {"date_text": "prossimamente", "time_text": "alle 10:30"},
                        "contact": {"first_name": "Giorgio", "last_name": "Censori"}}], "clarifications": []}


def _rientra(pagina):
    """Esce dalla Shell (altra pagina) e rientra nella sezione Voce."""
    page = pagina["page"]
    page.goto("about:blank")
    page.goto(f"{pagina['url']}/os/?n={uuid.uuid4().hex[:8]}#/voce")
    page.wait_for_selector("[data-voice-form]")


def test_s1_cambio_schermata_e_ripresa_senza_il_modello(pagina):  # noqa: F811
    from voice.dates import ROMA
    page, finto = pagina["page"], pagina["finto"]
    finto.register(TESTO, OUTPUT)
    post = []
    page.on("request", lambda r: post.append(r.url) if r.method == "POST" else None)
    page.fill("[data-voice-text]", TESTO)
    page.click("[data-voice-send]")
    page.wait_for_selector("[data-voice-command]")
    cid = page.get_attribute("[data-voice-command]", "data-voice-command")
    assert "Non ho capito il giorno" in page.inner_text("[data-voice-result]")
    chiamate, inviati = len(finto.calls), len(post)

    _rientra(pagina)
    page.wait_for_selector(f"[data-voice-command='{cid}']")
    assert "Non ho capito il giorno" in page.inner_text("[data-voice-result]")
    assert "Ho ripreso il comando in corso" in page.inner_text("[data-voice-live]")
    assert len(finto.calls) == chiamate and len(post) == inviati            # nessuna nuova interpretazione

    page.fill("[data-voice-reply-text]", "domani")
    page.click("[data-voice-reply-send]")
    page.wait_for_function(f"document.querySelector('[data-voice-command]').dataset.voiceCommand !== '{cid}'")
    nuovo = page.get_attribute("[data-voice-command]", "data-voice-command")
    assert len(finto.calls) == chiamate                                       # «domani» capito senza il modello
    domani = (datetime.now(ROMA) + timedelta(days=1)).date()
    assert page.inner_text("[data-voice-summary-field='ora']").startswith("10:30")

    _rientra(pagina)
    page.wait_for_selector(f"[data-voice-command='{nuovo}']")
    storia = page.inner_text("[data-voice-history]")
    assert TESTO in storia and "domani" in storia
    assert len(finto.calls) == chiamate

    page.click("[data-voice-confirm]")
    page.wait_for_selector("[data-voice-open-appointment]", timeout=10000)
    riga = q(pagina, "SELECT appointment_type, contact_id, start_at, source FROM appointments ORDER BY id DESC LIMIT 1")[0]
    assert riga[0] == "call" and riga[1] == pagina["ids"]["giorgio"] and riga[3] == "crm_manual"
    inizio = riga[2].astimezone(ROMA)
    assert (inizio.date(), inizio.hour, inizio.minute) == (domani, 10, 30)
    assert q(pagina, "SELECT status FROM voice_commands WHERE id = %s", (int(cid),))[0][0] == "cancelled"

    _rientra(pagina)
    page.wait_for_timeout(300)
    assert page.query_selector("[data-voice-command]") is None               # nulla in corso
    assert len(finto.calls) == chiamate and pagina["errori"] == [], pagina["errori"]


def test_s2_nuovo_comando_annulla_davvero(pagina):  # noqa: F811
    page, finto = pagina["page"], pagina["finto"]
    finto.register(TESTO, OUTPUT)
    page.fill("[data-voice-text]", TESTO)
    page.click("[data-voice-send]")
    page.wait_for_selector("[data-voice-command]")
    cid = int(page.get_attribute("[data-voice-command]", "data-voice-command"))
    page.click("[data-voice-new]")
    page.wait_for_function("!document.querySelector('[data-voice-command]')")
    assert q(pagina, "SELECT status FROM voice_commands WHERE id = %s", (cid,))[0][0] == "cancelled"
    _rientra(pagina)
    page.wait_for_timeout(300)
    assert page.query_selector("[data-voice-command]") is None and pagina["errori"] == []
