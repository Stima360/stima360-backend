"""STIMA VOICE - collaudo iPhone del 21/10, in BROWSER (Chromium) con backend,
PostgreSQL e Shell VERI, finestra e user agent di un iPhone (390 px).

Il fornitore AI e' quello finto (nessuna rete). Il riconoscimento vocale e' un
finto `webkitSpeechRecognition` che si comporta come WebKit su iPhone:
risultati SENZA spazio iniziale, un pezzo gia' detto riconsegnato (anche dalla
sessione successiva), fine dell'ascolto a ogni pausa. Cio' che resta da provare
su un iPhone vero (permesso del microfono, qualita' del riconoscimento di Apple,
tastiera) NON e' coperto qui.

  i1  dettatura a pezzi come su iPhone: spazi giusti, nessun doppione, alla pausa
      nessuna riapertura automatica («Continua a dettare» la riapre), testo
      modificabile, nulla inviato da solo
  i2  dall'interpretazione all'Agenda: «...rivendita Domani alle ore 10:30
      Compratore Giorgio censori venditore Francesco Ruvo» con l'output SBAGLIATO
      del modello (il venditore come cliente) -> scheda Visita immobile, cliente
      Giorgio Censori, incarico di Francesco RUVOLO (non inventato), domani 10:30;
      solo dopo «Conferma ed esegui» la riga in `appointments` (crm_manual)
  i3  impaginazione a 390 e 320 px: nessuno scorrimento orizzontale, la pagina usa
      la larghezza del telefono, pulsanti alti almeno 44 px, testo del campo a
      16 px (sotto, Safari ingrandisce la pagina al tocco)
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_8_0_browser_postgres import server  # noqa: F401
from tests.test_voice_8_0_conversations_postgres import _persone  # noqa: F401
from tests.test_voice_8_2_visits_postgres import _incarichi  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")

#: Un finto riconoscimento «come WebKit su iPhone»: `say(testo, finale)` aggiunge un
#: risultato SENZA spazio iniziale; `pausa()` chiude l'ascolto come fa iOS.
FINTO_WEBKIT = r"""
(() => {
  class Rec {
    constructor() { Rec.made.push(this); this.risultati = []; }
    start() { this.started = true; }
    stop() { this.stopped = true; setTimeout(() => this._fine(), 0); }
    abort() { this.aborted = true; setTimeout(() => this._fine(), 0); }
    _fine() { if (this.finito) return; this.finito = true; if (this.onend) this.onend(); }
    say(testo, finale = true) {
      const r = [{ transcript: testo }]; r.isFinal = finale; this.risultati.push(r);
      this.onresult({ resultIndex: this.risultati.length - 1, results: this.risultati });
    }
    pausa() { this._fine(); }
  }
  Rec.made = [];
  window.SpeechRecognition = undefined;
  window.webkitSpeechRecognition = Rec;
  window.__Rec = Rec;
})();
"""

DETTATO = "Ehi Crea un appuntamento rivendita Domani alle ore 10:30 Compratore Giorgio censori venditore Francesco Ruvo"


@pytest.fixture
def iphone(server):  # noqa: F811
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        locale = "/opt/pw-browsers/chromium"
        browser = p.chromium.launch(**({"executable_path": locale} if os.path.exists(locale) else {}))
        contesto = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=IPHONE_UA,
                                       device_scale_factor=3, is_mobile=True, has_touch=True)
        contesto.add_init_script(FINTO_WEBKIT)
        page = contesto.new_page()
        errori, console = [], []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.goto(f"{server['url']}/os/?n={uuid.uuid4().hex[:8]}#/voce")
        page.wait_for_selector("[data-voice-form]")
        yield {"page": page, "errori": errori, "console": console, **server}
        browser.close()


def _detta_come_iphone(page):
    page.click("[data-voice-mic]")
    page.wait_for_function("window.__Rec.made.length === 1")
    # prima sessione: pezzi senza spazio iniziale, il primo riconsegnato
    page.evaluate("""() => { const r = window.__Rec.made[0];
      r.say('Ehi'); r.say('Crea un appuntamento rivendita', false); r.risultati.pop();
      r.say('Crea un appuntamento rivendita'); r.say('Domani alle ore 10:30'); }""")
    assert page.input_value("[data-voice-text]") == "Ehi Crea un appuntamento rivendita Domani alle ore 10:30"
    page.evaluate("window.__Rec.made[0].pausa()")                   # iOS chiude l'ascolto alla pausa
    page.wait_for_function("document.querySelector('[data-voice-mic]').textContent.includes('Continua a dettare')")
    time.sleep(0.3)
    assert page.evaluate("window.__Rec.made.length") == 1            # nessuna riapertura automatica
    assert "Pausa: il testo è salvo" in page.inner_text("[data-voice-live]")
    # seconda sessione, solo dal tocco: ripete l'ultimo pezzo e raddoppia «Ruvo»
    page.click("[data-voice-mic]")
    page.wait_for_function("window.__Rec.made.length === 2")
    assert page.inner_text("[data-voice-mic]").strip() == "■ Termina dettatura"
    page.evaluate("""() => { const r = window.__Rec.made[1];
      r.say('10:30 Compratore Giorgio censori'); r.say('venditore Francesco Ruvo'); r.say('Ruvo', false); }""")
    page.click("[data-voice-mic]")                                     # «Termina dettatura»
    page.wait_for_function("document.querySelector('[data-voice-mic]').textContent.includes('Parla')")


def test_i1_dettatura_a_pezzi_come_su_iphone(iphone):
    page = iphone["page"]
    prima = q(iphone, "SELECT count(*) FROM voice_commands")[0][0]
    assert page.query_selector("[data-voice-iphone]") is not None    # la spiegazione del permesso
    assert "regola di iOS" in page.inner_text("[data-voice-iphone]")
    _detta_come_iphone(page)
    assert page.input_value("[data-voice-text]") == DETTATO
    assert "Dettatura terminata" in page.inner_text("[data-voice-live]")
    assert page.evaluate("window.__Rec.made.length") == 2
    # il testo resta modificabile prima dell'invio
    assert page.get_attribute("[data-voice-text]", "readonly") is None
    page.fill("[data-voice-text]", DETTATO.replace("Ehi ", ""))
    assert page.input_value("[data-voice-text]").startswith("Crea un appuntamento")
    assert q(iphone, "SELECT count(*) FROM voice_commands")[0][0] == prima              # nulla inviato da solo
    assert iphone["errori"] == []


def test_i2_dalla_dettatura_all_agenda(iphone):
    page, finto = iphone["page"], iphone["finto"]
    # l'output SBAGLIATO del modello: il venditore come cliente, `sale_appointment`
    finto.register(DETTATO, {"commands": [{
        "intent": "create_appointment", "quote": DETTATO, "appointment_type": "sale_appointment",
        "when": {"date_text": "Domani", "time_text": "alle ore 10:30"},
        "contact": {"first_name": "Francesco", "last_name": "Ruvo"}}], "clarifications": []})
    prima = q(iphone, "SELECT count(*) FROM appointments")[0][0]
    _detta_come_iphone(page)
    page.click("[data-voice-send]")
    page.wait_for_selector("[data-voice-summary='1']")
    scheda = {k: page.inner_text(f"[data-voice-summary-field='{k}']") for k in ("tipo", "cliente", "incarico", "data", "ora")}
    assert scheda["tipo"] == "Visita immobile", scheda
    assert scheda["cliente"] == "Giorgio Censori", scheda
    assert "Francesco Ruvolo" in scheda["incarico"] and scheda["incarico"].startswith("Via Roma 10"), scheda
    assert scheda["ora"].startswith("10:30"), scheda
    assert q(iphone, "SELECT count(*) FROM appointments")[0][0] == prima               # nulla senza conferma
    # «Ruvo» -> «Ruvolo» e' un candidato: si conferma, mai da solo
    for scelta in page.query_selector_all("[data-voice-choice] input[type=radio]"):
        if not page.evaluate("(el) => document.querySelector(`input[name='${el.name}']:checked`) !== null", scelta):
            scelta.check()
    page.click("[data-voice-confirm]")
    page.wait_for_selector("[data-voice-open-appointment]", timeout=10000)
    href = page.get_attribute("[data-voice-open-appointment]", "href")
    riga = q(iphone, "SELECT id, appointment_type, contact_id, property_id, source, start_at FROM appointments "
                     "ORDER BY id DESC LIMIT 1")[0]
    assert q(iphone, "SELECT count(*) FROM appointments")[0][0] == prima + 1
    assert list(riga[1:5]) == ["buyer_visit", iphone["ids"]["giorgio"], iphone["ids"]["roma10"], "crm_manual"]
    from voice.dates import ROMA
    domani = (datetime.now(ROMA) + timedelta(days=1)).date()
    inizio = riga[5].astimezone(ROMA)
    assert (inizio.date(), inizio.hour, inizio.minute) == (domani, 10, 30)
    assert "#/agenda" in href and domani.isoformat() in href, href
    assert page.inner_text("[data-voice-error]") == "" and iphone["errori"] == []


MISURA = """() => {
  const w = window.innerWidth;
  const pag = document.querySelector('[data-voice-page]').getBoundingClientRect();
  const campo = document.querySelector('[data-voice-text]');
  const bottoni = [...document.querySelectorAll('[data-voice-mic], [data-voice-send]')].map((b) => {
    const r = b.getBoundingClientRect(); return { h: r.height, l: r.left, r: r.right, t: b.textContent.trim() }; });
  return { w, scroll: document.documentElement.scrollWidth, pl: pag.left, pr: pag.right,
           campo: campo.getBoundingClientRect().width, font: parseFloat(getComputedStyle(campo).fontSize), bottoni };
}"""


@pytest.mark.parametrize("larghezza", [390, 320])
def test_i3_impaginazione_su_iphone(iphone, larghezza):
    page = iphone["page"]
    page.set_viewport_size({"width": larghezza, "height": 800})
    page.wait_for_timeout(200)
    m = page.evaluate(MISURA)
    assert m["scroll"] <= m["w"], m                                    # nessuno scorrimento orizzontale
    assert m["pl"] <= 24 and m["w"] - m["pr"] <= 24, m                 # la pagina usa la larghezza del telefono
    assert m["campo"] >= m["w"] - 64, m
    assert m["font"] >= 16, m                                          # niente ingrandimento al tocco
    for b in m["bottoni"]:
        assert b["h"] >= 44 and b["l"] >= 0 and b["r"] <= m["w"], m
    assert iphone["errori"] == []
