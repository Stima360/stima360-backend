"""STIMA VOICE - prova in BROWSER (Chromium) con backend, PostgreSQL e Shell VERI.

Il fornitore AI e' quello finto (`FakeVoiceProvider`, nessuna rete); il
riconoscimento vocale del browser e' sostituito da un finto `webkitSpeechRecognition`
che si comporta come Chrome su Mac (fine automatica dopo una pausa, risultati
cumulativi in modalita' continua). Tutto il resto e' vero: router Voce, servizio,
registro, risolutore, Agenda, HTTP, fetch del browser.

  b1  comando vero -> 200 -> piano «awaiting_answers» a schermo, nessun messaggio
      di connessione; risposta -> nuovo piano; nessun appuntamento duplicato
  b2  dettatura lunga con pause: il browser chiude il riconoscimento da solo, la
      dettatura riparte e il testo resta; solo «Termina dettatura» la conclude; nulla
      si invia da solo
  b3  orario gia' passato: data e ora riconosciute a schermo e la domanda
      «Le 10:30 di oggi sono gia' passate. Vuoi scegliere un altro orario?»
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from tests.test_edifici_1_browser_postgres import SHELL, _porta_libera
from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_8_0_conversations_postgres import FRASE, GIUSTO, SBAGLIATO, _persone  # noqa: F401
from tests.test_voice_8_2_visits_postgres import _incarichi  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

#: Un finto riconoscimento «come Chrome»: continuous rispettato, risultati
#: cumulativi nella sessione, `onend` da solo quando il test lo chiede (pausa).
FINTO_RICONOSCIMENTO = r"""
(() => {
  class Rec {
    constructor() { Rec.made.push(this); this.risultati = []; }
    start() { this.started = true; Rec.attivi += 1; }
    stop() { this.stopped = true; setTimeout(() => this._fine(), 0); }
    abort() { this.aborted = true; setTimeout(() => this._fine(), 0); }
    _fine() { if (this.finito) return; this.finito = true; Rec.attivi -= 1; if (this.onend) this.onend(); }
    say(testo) {
      const r = [{ transcript: testo }]; r.isFinal = true; this.risultati.push(r);
      this.onresult({ resultIndex: this.risultati.length - 1, results: this.risultati });
    }
    pausa() { this._fine(); }                                  // Chrome chiude da solo dopo il silenzio
  }
  Rec.made = []; Rec.attivi = 0;
  // Chromium recente espone anche `SpeechRecognition` senza prefisso: si sostituiscono entrambi
  window.SpeechRecognition = Rec;
  window.webkitSpeechRecognition = Rec;
  window.__Rec = Rec;
})();
"""


@pytest.fixture
def server(db, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("playwright.sync_api")
    from fastapi import Depends, FastAPI
    from fastapi.staticfiles import StaticFiles
    from operator_auth.dependencies import require_operator
    from voice import router as voice_router
    from voice.fake_provider import FakeVoiceProvider

    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    monkeypatch.setenv("STIMA_VOICE_BROWSER_SPEECH", "1")
    monkeypatch.setenv("STIMA_VOICE_AI_URL", "https://finto.invalid/v1/chat/completions")
    monkeypatch.setenv("STIMA_VOICE_AI_MODEL", "finto")
    monkeypatch.setenv("STIMA_VOICE_AI_KEY", "finta")
    finto = FakeVoiceProvider()
    monkeypatch.setattr(voice_router, "get_provider", lambda: finto)
    sessione = {"user_id": db["ids"]["agent_a1"], "agency_id": 1, "agency_name": "Agenzia Uno", "role": "agent",
                "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}
    app = FastAPI()
    app.include_router(voice_router.router, dependencies=[Depends(require_operator)])
    app.dependency_overrides[require_operator] = lambda: ctx(db, "agent_a1")

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
        yield {"url": f"http://127.0.0.1:{porta}", "finto": finto, **db}
    finally:
        srv.should_exit = True
        filo.join(timeout=10)


@pytest.fixture
def pagina(server):
    from playwright.sync_api import sync_playwright
    import os
    with sync_playwright() as p:
        # il Chromium preinstallato dell'ambiente, se il client Playwright ne cerca un'altra versione
        locale = "/opt/pw-browsers/chromium"
        browser = p.chromium.launch(**({"executable_path": locale} if os.path.exists(locale) else {}))
        contesto = browser.new_context(viewport={"width": 1280, "height": 900})
        contesto.add_init_script(FINTO_RICONOSCIMENTO)
        page = contesto.new_page()
        errori, console = [], []
        page.on("pageerror", lambda e: errori.append(str(e)))
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.goto(f"{server['url']}/os/?n={uuid.uuid4().hex[:8]}#/voce")
        page.wait_for_selector("[data-voice-form]")
        yield {"page": page, "errori": errori, "console": console, **server}
        browser.close()


def test_b1_comando_vero_senza_falso_errore_di_connessione(pagina):
    page, finto = pagina["page"], pagina["finto"]
    finto.register(FRASE, {"commands": SBAGLIATO, "clarifications": []})
    prima = q(pagina, "SELECT count(*) FROM appointments")[0][0]
    page.fill("[data-voice-text]", FRASE)
    page.click("[data-voice-send]")
    page.wait_for_selector("[data-voice-command]")
    testo = page.inner_text("[data-voice-page]")
    assert "Impossibile contattare" not in testo, (testo, pagina["console"])
    assert page.inner_text("[data-voice-error]") == "", pagina["console"]
    assert "il cliente che lo visita" in testo
    cid = page.get_attribute("[data-voice-command]", "data-voice-command")
    assert q(pagina, "SELECT status FROM voice_commands WHERE id = %s", (int(cid),))[0][0] == "awaiting_answers"
    from voice import service
    finto.register(f"{FRASE}\n{service.REPLY_PREFIX}Il cliente è Giorgio Censori", {"commands": GIUSTO, "clarifications": []})
    page.fill("[data-voice-reply-text]", "Il cliente è Giorgio Censori")
    page.click("[data-voice-reply-send]")
    page.wait_for_function(f"document.querySelector('[data-voice-command]').dataset.voiceCommand !== '{cid}'")
    assert page.inner_text("[data-voice-error]") == "", pagina["console"]
    assert "Giorgio Censori" in page.inner_text("[data-voice-summary='1']")
    assert q(pagina, "SELECT count(*) FROM appointments")[0][0] == prima          # nulla salvato senza conferma
    assert pagina["errori"] == [], pagina["errori"]


def test_b2_dettatura_lunga_con_pause(pagina):
    page = pagina["page"]
    prima = q(pagina, "SELECT count(*) FROM voice_commands")[0][0]
    page.click("[data-voice-mic]")
    page.wait_for_function("window.__Rec.made.length === 1")
    assert page.inner_text("[data-voice-mic]").strip() == "■ Termina dettatura"
    assert "puoi fare pause" in page.inner_text("[data-voice-live]")
    page.evaluate("window.__Rec.made[0].say('Crea un appuntamento di vendita')")
    page.evaluate("window.__Rec.made[0].pausa()")                    # Chrome chiude dopo il silenzio
    page.wait_for_function("window.__Rec.made.length === 2")         # ...e la dettatura si riapre
    assert page.inner_text("[data-voice-mic]").strip() == "■ Termina dettatura"
    page.evaluate("window.__Rec.made[1].say('oggi alle 10:30 Giorgio Censori')")
    page.evaluate("window.__Rec.made[1].pausa()")
    page.wait_for_function("window.__Rec.made.length === 3")
    page.evaluate("window.__Rec.made[2].say('compra incarico Ruvolo Francesco')")
    assert page.input_value("[data-voice-text]") == \
        "Crea un appuntamento di vendita oggi alle 10:30 Giorgio Censori compra incarico Ruvolo Francesco"
    # premere «Invia» mentre si detta non invia: chiude la dettatura e chiede di ricontrollare
    page.click("[data-voice-send]")
    page.wait_for_function("document.querySelector('[data-voice-mic]').textContent.includes('Parla')")
    assert "tocca di nuovo «Invia»" in page.inner_text("[data-voice-error]")
    assert "Dettatura terminata" in page.inner_text("[data-voice-live]")
    time.sleep(0.5)
    assert page.evaluate("window.__Rec.made.length") == 3                         # nessun riavvio dopo lo stop
    assert q(pagina, "SELECT count(*) FROM voice_commands")[0][0] == prima         # nulla inviato da solo
    assert pagina["errori"] == []


def test_b3_orario_passato_riconosciuto_e_chiesto(pagina):
    from datetime import datetime, timedelta
    from voice.dates import ROMA
    page, finto = pagina["page"], pagina["finto"]
    adesso = datetime.now(ROMA)
    if adesso.hour >= 2:
        passato, giorno, testo_giorno = adesso - timedelta(hours=1), "oggi", "di oggi"
    else:                                                                # a cavallo della mezzanotte
        passato, giorno, testo_giorno = (adesso - timedelta(days=1)).replace(hour=10, minute=30), "ieri", "di ieri"
    ora = f"{passato.hour}:{passato.minute:02d}"
    frase = f"Crea un appuntamento di vendita {giorno} alle {ora} Giorgio Censori compra incarico Ruvolo Francesco"
    finto.register(frase, {"commands": [{"intent": "create_appointment", "quote": frase, "appointment_type": "sale_appointment",
                                         "when": {"date_text": giorno, "time_text": f"alle {ora}"},
                                         "contact": {"first_name": "Giorgio", "last_name": "Censori"}}],
                            "clarifications": []})
    prima = q(pagina, "SELECT count(*) FROM appointments")[0][0]
    page.fill("[data-voice-text]", frase)
    page.click("[data-voice-send]")
    page.wait_for_selector("[data-voice-summary='1']")
    scheda = {k: page.inner_text(f"[data-voice-summary-field='{k}']") for k in ("tipo", "cliente", "incarico", "data", "ora")}
    assert scheda["tipo"] == "Visita immobile" and scheda["cliente"] == "Giorgio Censori"
    assert "Ruvolo" in scheda["incarico"]                                          # «incarico Ruvolo Francesco»
    assert scheda["data"] != "da indicare" and scheda["ora"].startswith(f"{passato.hour:02d}:{passato.minute:02d}")
    assert "(già passata)" in scheda["ora"]
    attesa = f"Le {ora} {testo_giorno} sono già passate. Vuoi scegliere un altro orario?" if passato.hour != 1 else \
        f"L'{ora} {testo_giorno} è già passata. Vuoi scegliere un altro orario?"
    assert attesa in page.inner_text("[data-voice-result]")
    assert page.query_selector("[data-voice-confirm]") is None                    # non si conferma un orario passato
    assert q(pagina, "SELECT count(*) FROM appointments")[0][0] == prima
    assert page.inner_text("[data-voice-error]") == "" and pagina["errori"] == []


def test_b4_risposta_persa_ripresa_senza_doppioni(pagina):
    """La risposta del server non arriva (connessione chiusa dopo che il server ha
    gia' registrato il comando, come durante il cambio d'istanza di un deploy):
    la pagina riprende lo STESSO comando, nessun messaggio di connessione, nessun doppione."""
    page, finto = pagina["page"], pagina["finto"]
    finto.register(FRASE, {"commands": SBAGLIATO, "clarifications": []})
    prima = q(pagina, "SELECT count(*) FROM voice_commands")[0][0]
    stato = {"tagliate": 0}

    def taglia_la_prima(route):
        if stato["tagliate"] == 0:
            stato["tagliate"] += 1
            risposta = route.fetch()                       # il server esegue e risponde...
            assert risposta.status == 200
            route.abort("connectionreset")                 # ...ma il browser non riceve nulla
        else:
            route.continue_()
    page.route("**/api/voice/commands", taglia_la_prima)
    page.fill("[data-voice-text]", FRASE)
    page.click("[data-voice-send]")
    page.wait_for_selector("[data-voice-command]", timeout=10000)
    assert stato["tagliate"] == 1
    assert page.inner_text("[data-voice-error]") == ""
    assert "Comando ripreso" in page.inner_text("[data-voice-live]")
    assert q(pagina, "SELECT count(*) FROM voice_commands")[0][0] == prima + 1     # uno solo
    assert any("risposta non arrivata" in c for c in pagina["console"]), pagina["console"]
