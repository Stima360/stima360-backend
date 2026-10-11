"""STIMA Voice Fase 6 - l'interfaccia, ESEGUITA in node.

Moduli veri (`views/voce.js`, `voice/*.js`, `main.js`) dentro lo stub di DOM di
P26-4, con `fetch` instradato (A30-5) e un riconoscimento vocale FINTO. Nessun
backend vero, nessun modello, nessuna rete.

NOTA sull'ambiente di prova: lo stub di DOM condiviso non ha
`document.createElementNS`, che `main.js` usa per le icone della sidebar; per
questo OGNI test che avvia `main.js` con lo stub condiviso fallisce gia' al
boot (problema preesistente, non una regressione). Qui, e SOLO nel driver di
questo file, si aggiunge uno shim minimo di `createElementNS`: lo stub condiviso
non cambia.

Cosa prova:
  * spento = invisibile: senza `enabled` nessun modulo, nessun pulsante, nessuna
    voce di menu; il microfono solo con `speech_input` E supporto del browser;
  * microfono: nessuna richiesta prima del tocco, stop volontario, testo
    modificabile prima dell'invio, permesso negato, stop quando la pagina si
    nasconde, nessun audio inviato (solo testo, solo con «Invia»);
  * invio: chiave UUID v4, doppio tocco = una richiesta, stesso testo dopo un
    errore = stessa chiave, testo corretto = chiave nuova;
  * piano: scelte, domande, conferme (con le scelte), esecuzione; «parziale»
    mai mostrato come completato; verifica dei passi incerti; annullamento
    solo dove il server lo consente; errori comprensibili; testo dal server
    sempre come testo (nessun HTML eseguito);
  * main.js: la voce «Voce» compare solo con un si' del server.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

NODE = a30_5.NODE
node = pytest.mark.skipif(NODE is None, reason="node non disponibile: prove UI non eseguite (BLOCKED, non PASS)")
ASSETS = a30_5.ASSETS


def _rt():
    return a30_5._rt()


# Solo per questo file: l'API che lo stub condiviso non ha (vedi docstring).
SHIM_SVG = r"""
document.createElementNS = (_ns, tag) => new __dom.El(tag);
document.hidden = false;
"""

UI_HELPERS = r"""
const wait = async (ms = 0) => { await new Promise((r) => setTimeout(r, ms)); await __settle(40); };
function chiamate() {
  return __calls.map((c) => ({
    m: String(c.options.method || 'GET').toUpperCase(), url: c.url,
    body: c.options.body ? JSON.parse(c.options.body) : null,
    headers: Object.keys(c.options.headers || {}), cred: c.options.credentials || null,
  }));
}
class FakeRec {
  constructor() { FakeRec.made.push(this); }
  start() { this.started = true; if (FakeRec.throwOnStart) throw new Error('blocked'); }
  stop() { this.stopped = true; if (this.onend) this.onend(); }
  abort() { this.aborted = true; if (this.onend) this.onend(); }
  say(text, isFinal = true) {
    const r = [{ transcript: text }]; r.isFinal = isFinal;
    this.onresult({ resultIndex: 0, results: [r] });
  }
}
FakeRec.made = [];
const winListeners = {};
const fakeWin = (speech) => ({
  addEventListener: (t, fn) => { (winListeners[t] ||= []).push(fn); },
  ...(speech ? { webkitSpeechRecognition: FakeRec } : {}),
});
const docListeners = {};
document.addEventListener = (t, fn) => { (docListeners[t] ||= []).push(fn); };
"""


def _ui(staged, scenario: str, rotte: str, *, speech_in_window: bool = True) -> dict:  # noqa: F811
    rt = _rt()
    driver = staged.parent / "driver-voice-6.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + rt.EXTRA_DOM + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH + SHIM_SVG + UI_HELPERS
        + f"\n{rotte}\n"
        + f"const {{ renderVoce }} = await import('{(staged / 'views' / 'voce.js').as_posix()}');\n"
        + "const host = document.createElement('div'); __dom.main.appendChild(host);\n"
        + f"const win = fakeWin({'true' if speech_in_window else 'false'});\n"
        + "const Q = (s) => host.querySelector(s); const QQ = (s) => host.querySelectorAll(s);\n"
        + "function report(extra = {}) { console.log(JSON.stringify({ calls: chiamate(), text: host.visibleText(), "
          "html: host.innerHTML, error: Q('[data-voice-error]') ? Q('[data-voice-error]').textContent : null, ...extra })); }\n"
        + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _node(script: str) -> dict:
    esito = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30,
                           env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


def route(metodo, prefisso, *risposte):
    return f"__route({json.dumps(metodo)}, {json.dumps(prefisso)}, {', '.join(json.dumps(r) for r in risposte)});"


def ok(body):
    return {"status": 200, "body": body}


ACCESA = ok({"enabled": True, "speech_input": True})
SOLO_TESTO = ok({"enabled": True, "speech_input": False})


def passo(n, intent, *, state="pending", needs=None, reasons=(), candidates=None, quote="", result=None, undo="not_applicable",
          error=None, decision="ask", start_at=None, end_at=None):
    return {"ordinal": n, "intent": intent, "quote": quote, "state": state, "decision": decision, "needs": needs,
            "reasons": [{"code": c, "message": m, "confirmable": k} for c, m, k in reasons],
            "candidates": candidates or {}, "duplicates": [], "hidden_duplicate": False, "start_at": start_at,
            "end_at": end_at, "result": result or {}, "error": error, "undo": undo}


def comando(status, steps, *, cid=7, notice=None, concluded=False):
    return {"command_id": cid, "status": status, "mode": "auto", "recorded_at": "2026-10-11T09:00:00+02:00",
            "steps": steps, "clarifications": [], "all_steps_concluded": concluded, "notice": notice}


FABIO = [{"id": 12, "label": "Fabio Verdi"}]
COMPOSTO = ("composite_command_confirmation", "Comando in più passi: conferma prima di qualsiasi scrittura.", True)
CONFERMA_CONTATTO = ("contact_confirmation_required", "Trovato per nome: confermi che è questo contatto?", True)
PROPRIETARIO = ("owner_link_confirmation_required", "Confermi il collegamento del proprietario?", True)
A_PIANO = comando("awaiting_answers", [
    passo(1, "create_unit", needs="needs_confirmation", reasons=[COMPOSTO], quote="un trilocale in via Duca d'Aosta"),
    passo(2, "link_owner", needs="needs_confirmation", reasons=[CONFERMA_CONTATTO, PROPRIETARIO, COMPOSTO],
          candidates={"contact": FABIO}, quote="un trilocale in via Duca d'Aosta"),
    passo(3, "activate_seller", needs="needs_confirmation", reasons=[CONFERMA_CONTATTO, COMPOSTO],
          candidates={"contact": FABIO}, quote="vuole vendere"),
])
A_FATTO = comando("completed", [
    passo(1, "create_unit", state="succeeded", result={"entity": "property", "id": 90, "created": True}, undo="undoable",
          quote="un trilocale in via Duca d'Aosta"),
    passo(2, "link_owner", state="succeeded", result={"created": True}, undo="not_undoable"),
    passo(3, "activate_seller", state="succeeded", result={"origin": "created"}, undo="undoable"),
])


# ---------------------------------------------------------------------------
# Moduli puri
# ---------------------------------------------------------------------------

@node
def test_m1_model_never_calls_partial_completed_and_undo_follows_the_server():
    out = _node(f"""
      const m = await import('{(ASSETS / 'voice' / 'voice-model.js').as_posix()}');
      const st = (s) => m.commandStatus({{ status: s }}).label;
      const p = (o) => m.stepStatus(o).label;
      console.log(JSON.stringify({{
        completed: st('completed'), partial: st('partial'),
        incerto: p({{ state: 'indeterminate' }}), fallito: p({{ state: 'failed' }}),
        conferma: p({{ state: 'pending', needs: 'needs_confirmation' }}), dati: p({{ state: 'pending', needs: 'needs_answer' }}),
        undo: m.UNDO_TEXT,
        quando: m.formatWhen('2026-10-12T13:00:00Z'),
        errori: ['VOICE_PROVIDER_UNAVAILABLE', 'VOICE_MODEL_OUTPUT_INVALID', 'VOICE_DAILY_LIMIT', 'XYZ']
          .map((code) => m.errorMessage({{ code, message: 'grezzo' }})),
      }}));
    """)
    assert out["completed"] == "Completato" and out["partial"] == "Completato in parte"
    assert out["incerto"].startswith("Esito incerto") and out["fallito"] == "Non eseguito"
    assert out["conferma"] == "Da confermare" and out["dati"] == "Servono dati"
    assert "15 minuti" in out["undo"]["expired"] and out["undo"]["not_undoable"] == "Non annullabile con la voce"
    assert out["quando"] == "lunedì 12 ottobre alle ore 15:00"
    assert "non verrà duplicato" in out["errori"][0] and "Nulla è stato eseguito" in out["errori"][1]
    assert "limite giornaliero" in out["errori"][2] and out["errori"][3] == "grezzo"


@node
def test_m2_dictation_starts_only_on_demand_and_maps_errors():
    out = _node(f"""
      const s = await import('{(ASSETS / 'voice' / 'voice-speech.js').as_posix()}');
      const made = [];
      class R {{ constructor() {{ made.push(this); }} start() {{}} stop() {{ this.onend(); }} abort() {{ this.aborted = true; }} }}
      const errori = [], testi = [], stati = [];
      const d = s.createDictation({{ win: {{ SpeechRecognition: R }}, onError: (e) => errori.push(e),
        onText: (t, f) => testi.push([t, f]), onState: (x) => stati.push(x) }});
      const primaDelTocco = made.length;
      d.start();
      const r = made[0];
      const a = [{{ transcript: 'domani alle ' }}]; a.isFinal = true;
      const b = [{{ transcript: '15' }}]; b.isFinal = false;
      r.onresult({{ resultIndex: 0, results: [a, b] }});
      for (const e of ['not-allowed', 'service-not-allowed', 'audio-capture', 'no-speech', 'network', 'aborted', 'boh'])
        r.onerror({{ error: e }});
      d.stop();
      console.log(JSON.stringify({{
        primaDelTocco, config: [r.lang, r.continuous, r.interimResults], testi, errori, stati,
        senza: s.speechSupport({{}}), con: s.speechSupport({{ webkitSpeechRecognition: R }}).supported,
      }}));
    """)
    assert out["primaDelTocco"] == 0
    assert out["config"] == ["it-IT", False, True]
    assert out["testi"] == [["domani alle 15", False]]
    assert out["errori"] == ["permission_denied", "permission_denied", "no_microphone", "no_speech", "network", "error"]
    assert out["stati"] == ["listening", "idle"]
    assert out["senza"] == {"supported": False, "ctor": None} and out["con"] is True


@node
def test_m3_status_fails_closed_and_keys_are_uuid4():
    out = _node(f"""
      globalThis.window = {{ location: {{ hash: '' }}, addEventListener() {{}} }};
      globalThis.document = {{ addEventListener() {{}} }};
      const a = await import('{(ASSETS / 'voice' / 'voice-api.js').as_posix()}');
      const casi = [];
      for (const get of [
        async () => {{ const e = new Error('Errore 404'); e.status = 404; throw e; }},
        async () => {{ throw new Error('rete'); }},
        async () => ({{ enabled: false, speech_input: true }}),
        async () => ({{ enabled: 'true' }}),
        async () => ({{ enabled: true }}),
        async () => ({{ enabled: true, speech_input: true }}),
      ]) casi.push(await a.voiceStatus(get));
      const chiavi = [a.newCommandKey(), a.newCommandKey({{ getRandomValues: (b) => {{ b.fill(7); return b; }} }})];
      console.log(JSON.stringify({{ casi, chiavi }}));
    """)
    spento = {"enabled": False, "speechInput": False}
    assert out["casi"][:4] == [spento] * 4
    assert out["casi"][4] == {"enabled": True, "speechInput": False}
    assert out["casi"][5] == {"enabled": True, "speechInput": True}
    assert all(a30_5.UUID4.match(k) for k in out["chiavi"])


# ---------------------------------------------------------------------------
# Spento = invisibile; microfono condizionato
# ---------------------------------------------------------------------------

@node
@pytest.mark.parametrize("stato", [{"status": 404, "body": {"detail": "Not Found"}}, ok({"enabled": False}),
                                   {"status": 500, "body": {}}])
def test_v1_disabled_shows_no_controls(staged, stato):  # noqa: F811
    out = _ui(staged, "await renderVoce(host, [], { win }); await wait(); report({ buttons: QQ('button').length, "
                      "form: !!Q('[data-voice-form]') });", route("GET", "/api/voice/status", stato))
    assert out["buttons"] == 0 and out["form"] is False
    assert "non è attivo" in out["text"]
    assert [c["url"] for c in out["calls"]] == ["/api/voice/status"]


@node
def test_v2_text_only_when_speech_not_allowed_or_not_supported(staged):  # noqa: F811
    scena = "await renderVoce(host, [], { win }); await wait(); report({ mic: !!Q('[data-voice-mic]'), send: !!Q('[data-voice-send]'), made: FakeRec.made.length });"
    vietato = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO))
    assert vietato["mic"] is False and vietato["send"] is True and "tastiera" in vietato["text"]
    assente = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA), speech_in_window=False)
    assert assente["mic"] is False and assente["send"] is True
    presente = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA))
    assert presente["mic"] is True and presente["made"] == 0          # nessun permesso chiesto prima del tocco


@node
def test_v3_dictation_fills_editable_text_and_sends_only_on_submit(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-mic]').dispatch('click'); await wait();
      const ascolto = [Q('[data-voice-mic]').getAttribute('aria-pressed'), Q('[data-voice-live]').textContent];
      FakeRec.made[0].say('Domani alle 15 appuntamento con Fabbio');
      const dettato = Q('[data-voice-text]').value;
      const postPrima = chiamate().filter((c) => c.m === 'POST').length;
      Q('[data-voice-mic]').dispatch('click'); await wait();
      const fermato = [FakeRec.made[0].stopped, Q('[data-voice-mic]').getAttribute('aria-pressed')];
      Q('[data-voice-text]').value = 'Domani alle 15 appuntamento con Fabio';      // correzione a mano
      Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ ascolto, dettato, postPrima, fermato });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA) + route("POST", "/api/voice/commands", ok(A_PIANO)))
    assert out["ascolto"][0] == "true" and "In ascolto" in out["ascolto"][1]
    assert out["dettato"] == "Domani alle 15 appuntamento con Fabbio" and out["postPrima"] == 0
    assert out["fermato"] == [True, "false"]
    post = [c for c in out["calls"] if c["m"] == "POST"]
    assert len(post) == 1 and post[0]["body"]["text"] == "Domani alle 15 appuntamento con Fabio"
    assert set(post[0]["body"]) == {"client_command_id", "text"}                 # solo testo, nessun audio
    assert post[0]["cred"] == "include" and "Authorization" not in post[0]["headers"]


@node
def test_v4_microphone_permission_denied_keeps_text_fallback(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-mic]').dispatch('click'); await wait();
      FakeRec.made[0].onerror({ error: 'not-allowed' }); FakeRec.made[0].onend(); await wait();
      report({ pressed: Q('[data-voice-mic]').getAttribute('aria-pressed'), send: !Q('[data-voice-send]').disabled });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA))
    assert "Permesso del microfono negato" in out["error"] and out["pressed"] == "false" and out["send"] is True


@node
def test_v5_dictation_stops_when_page_is_hidden_or_left(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-mic]').dispatch('click'); await wait();
      document.hidden = true; for (const fn of docListeners.visibilitychange || []) fn();
      const nascosta = !!FakeRec.made[0].aborted;
      document.hidden = false;
      Q('[data-voice-mic]').dispatch('click'); await wait();
      for (const fn of winListeners.pagehide || []) fn();
      const uscita = !!FakeRec.made[1].aborted;
      Q('[data-voice-mic]').dispatch('click'); await wait();
      for (const fn of winListeners.hashchange || []) fn();
      report({ nascosta, uscita, cambio: !!FakeRec.made[2].aborted });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA))
    assert out["nascosta"] and out["uscita"] and out["cambio"]


# ---------------------------------------------------------------------------
# Invio: doppio tocco, retry, chiave
# ---------------------------------------------------------------------------

@node
def test_s1_double_tap_sends_once_and_retry_reuses_the_key(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani alle 15 appuntamento con Fabio';
      Q('[data-voice-form]').dispatch('submit'); Q('[data-voice-form]').dispatch('submit'); await wait();
      const dopoDoppio = chiamate().filter((c) => c.m === 'POST').length;
      const errore = Q('[data-voice-error]').textContent;
      Q('[data-voice-form]').dispatch('submit'); await wait();              // stesso testo: stessa chiave
      Q('[data-voice-text]').value = 'Domani alle 16 appuntamento con Fabio';
      Q('[data-voice-form]').dispatch('submit'); await wait();              // testo corretto: chiave nuova
      report({ dopoDoppio, errore });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA)
              + route("POST", "/api/voice/commands", {"throw": True}, ok(A_PIANO)))
    post = [c for c in out["calls"] if c["m"] == "POST"]
    assert out["dopoDoppio"] == 1
    assert "connessione" in out["errore"]
    chiavi = [p["body"]["client_command_id"] for p in post]
    assert len(chiavi) == 3 and chiavi[0] == chiavi[1] and chiavi[2] != chiavi[1]
    assert all(a30_5.UUID4.match(k) for k in chiavi)


@node
@pytest.mark.parametrize("risposta,atteso", [
    ({"status": 503, "body": {"code": "VOICE_PROVIDER_UNAVAILABLE", "detail": "x"}}, "non verrà duplicato"),
    ({"status": 502, "body": {"code": "VOICE_MODEL_OUTPUT_INVALID", "detail": "x"}}, "riformularlo"),
    ({"status": 429, "body": {"code": "VOICE_DAILY_LIMIT", "detail": "x"}}, "limite giornaliero"),
    ({"status": 409, "body": {"code": "VOICE_IDEMPOTENCY_KEY_REUSED", "detail": "x"}}, "nuovo comando"),
    ({"status": 403, "body": {"code": "VOICE_DISABLED", "detail": "x"}}, "non è attivo"),
])
def test_s2_errors_are_understandable(staged, risposta, atteso):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Nota per Mario'; Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ result: Q('[data-voice-result]').innerHTML });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA) + route("POST", "/api/voice/commands", risposta))
    assert atteso in out["error"] and out["result"] == ""


# ---------------------------------------------------------------------------
# Piano, scelte, conferme, risultato
# ---------------------------------------------------------------------------

@node
def test_p1_scenario_a_shows_what_will_change_then_confirms_and_executes(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = "Fabio vuole vendere un trilocale in via Duca d'Aosta";
      Q('[data-voice-form]').dispatch('submit'); await wait();
      const piano = { testo: Q('[data-voice-result]').visibleText(), nota: !!Q('[data-voice-confirm-note]'),
        stati: QQ('[data-voice-step-status]').map((e) => e.visibleText()) };
      Q('[data-voice-confirm]').dispatch('click'); await wait();
      report({ piano, fatto: Q('[data-voice-result]').visibleText(), undo: QQ('[data-voice-undo]').map((b) => b.dataset.voiceUndo) });
    """
    # ROUTED_FETCH confronta per prefisso e vince la prima rotta: le specifiche prima della generica
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA)
              + route("POST", "/api/voice/commands/7/confirm", ok(A_PIANO))
              + route("POST", "/api/voice/commands/7/execute", ok(A_FATTO))
              + route("POST", "/api/voice/commands", ok(A_PIANO)))
    p = out["piano"]
    assert "Nuova scheda immobile" in p["testo"] and "Collega il proprietario" in p["testo"] and "Attiva «Vende»" in p["testo"]
    assert "Fabio Verdi" in p["testo"] and "Confermi il collegamento del proprietario?" in p["testo"]
    assert p["nota"] is True and p["stati"] == ["Da confermare"] * 3
    post = [c for c in out["calls"] if c["m"] == "POST"]
    assert [c["url"] for c in post] == ["/api/voice/commands", "/api/voice/commands/7/confirm", "/api/voice/commands/7/execute"]
    assert post[1]["body"] == {"all": False, "steps": [{"ordinal": 1, "choices": None}, {"ordinal": 2, "choices": None},
                                                       {"ordinal": 3, "choices": None}]}
    assert "Completato" in out["fatto"] and "Non annullabile con la voce" in out["fatto"]
    assert out["undo"] == ["1", "3"]                                            # solo dove il server lo consente


@node
def test_p2_ambiguous_contact_requires_an_explicit_choice(staged):  # noqa: F811
    ambiguo = comando("awaiting_answers", [passo(1, "create_appointment", needs="needs_confirmation",
                      reasons=[("reference_ambiguous", "Ci sono più record possibili: scegli quello giusto.", True)],
                      candidates={"contact": [{"id": 12, "label": "Fabio Verdi"}, {"id": 13, "label": "Fabio Bianchi"}]},
                      start_at="2026-10-12T13:00:00+00:00", end_at="2026-10-12T14:00:00+00:00")])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani alle 15 appuntamento con Fabio'; Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-confirm]').dispatch('click'); await wait();
      const senzaScelta = { post: chiamate().filter((c) => c.m === 'POST').length, errore: Q('[data-voice-error]').textContent };
      const opzioni = QQ('[data-voice-option]'); opzioni[1].checked = true;
      Q('[data-voice-confirm]').dispatch('click'); await wait();
      report({ senzaScelta, desc: Q('[data-voice-step-desc]') && Q('[data-voice-step-desc]').textContent,
               etichette: opzioni.map((o) => o.parentNode.visibleText().trim()) });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA)
              + route("POST", "/api/voice/commands/7/confirm", ok(ambiguo))
              + route("POST", "/api/voice/commands/7/execute", ok(comando("completed", [])))
              + route("POST", "/api/voice/commands", ok(ambiguo)))
    assert out["senzaScelta"]["post"] == 1 and "Scegli" in out["senzaScelta"]["errore"]
    assert out["etichette"] == ["Fabio Verdi", "Fabio Bianchi"]
    conferma = [c for c in out["calls"] if c["url"].endswith("/confirm")][0]
    assert conferma["body"]["steps"] == [{"ordinal": 1, "choices": {"contact": 13}}]


@node
def test_p3_missing_data_is_a_question_without_confirm_button(staged):  # noqa: F811
    domanda = comando("awaiting_answers", [passo(1, "create_building", needs="needs_answer", quote="una palazzina con 12 appartamenti",
                      reasons=[("building_location_missing", "Dove si trova la palazzina? Serve l'indirizzo o un nome.", False)])])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Censisci una palazzina con 12 appartamenti'; Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ conferma: !!Q('[data-voice-confirm]'), esegui: !!Q('[data-voice-execute]'), stato: Q('[data-voice-step-status]').visibleText() });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA) + route("POST", "/api/voice/commands", ok(domanda)))
    assert out["conferma"] is False and out["esegui"] is False and out["stato"] == "Servono dati"
    assert "Dove si trova la palazzina" in out["text"]   # lo stub non decodifica &#39;


@node
def test_p4_partial_is_never_completed_and_uncertain_is_verified_by_a_human(staged):  # noqa: F811
    parziale = comando("partial", [
        passo(1, "create_contact", state="indeterminate", quote="Rita Valli 333 5151515"),
        passo(2, "add_task", needs="waiting_dependency", quote="richiamarla"),
    ])
    riconciliato = comando("partial", [passo(1, "create_contact", state="succeeded", result={"reconciled": "human", "id": 5},
                                             undo="not_undoable"), passo(2, "add_task", state="skipped")],
                           notice="Tutti i passi sono conclusi, ma lo stato complessivo resta «parziale».", concluded=True)
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Nuovo contatto Rita Valli'; Q('[data-voice-form]').dispatch('submit'); await wait();
      const prima = { stato: Q('[data-voice-command-status]').visibleText(), passo: Q('[data-voice-step-status]').visibleText() };
      Q('[data-voice-verify]').dispatch('click'); await wait();
      const pannello = Q('[data-voice-verify-panel="1"]').visibleText();
      Q('[data-voice-absent]').dispatch('click'); await wait();
      report({ prima, pannello, dopo: Q('[data-voice-command-status]').visibleText(), nota: Q('[data-voice-notice]').textContent });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA)
              + route("GET", "/api/voice/commands/7/steps/1/reconcile",
                      ok({"outcome": "not_provable", "candidates": [], "basis": "no_idempotency_key"}))
              + route("POST", "/api/voice/commands/7/steps/1/reconcile", ok(riconciliato))
              + route("POST", "/api/voice/commands", ok(parziale)))
    assert out["prima"] == {"stato": "Completato in parte", "passo": "Esito incerto: da verificare"}
    assert "non prova che non sia stato creato" in out["pannello"]
    decisione = [c for c in out["calls"] if c["m"] == "POST" and c["url"].endswith("/reconcile")][0]
    assert decisione["body"] == {"outcome": "absent", "record_id": None}
    assert out["dopo"] == "Completato in parte" and "parziale" in out["nota"]


@node
def test_p5_undo_only_when_allowed_then_rereads(staged):  # noqa: F811
    annullato = comando("completed", [passo(1, "add_note", state="succeeded", result={"id": 3}, undo="undone"),
                                      passo(2, "add_task", state="succeeded", result={"id": 4}, undo="expired")])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Nota e promemoria per Mario'; Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-undo]').dispatch('click'); await wait();
      report({ bottoni: QQ('[data-voice-undo]').length, note: QQ('[data-voice-undo-note]').map((e) => e.textContent) });
    """
    prima = comando("completed", [passo(1, "add_note", state="succeeded", result={"id": 3}, undo="undoable"),
                                  passo(2, "add_task", state="succeeded", result={"id": 4}, undo="expired")])
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA)
              + route("POST", "/api/voice/commands/7/steps/1/undo", ok({"ordinal": 1, "outcome": "undone"}))
              + route("GET", "/api/voice/commands/7", ok(annullato))
              + route("POST", "/api/voice/commands", ok(prima)))
    assert out["bottoni"] == 0
    assert out["note"] == ["Annullato", "Non più annullabile (oltre 15 minuti)"]
    assert [c["url"] for c in out["calls"]][-2:] == ["/api/voice/commands/7/steps/1/undo", "/api/voice/commands/7"]


@node
def test_p6_server_text_is_rendered_as_text_never_as_html(staged):  # noqa: F811
    cattivo = comando("awaiting_answers", [passo(1, "add_note", needs="needs_confirmation",
                      reasons=[("contact_confirmation_required", "<img src=x onerror=alert(1)>", True)],
                      candidates={"contact": [{"id": 1, "label": "<script>alert(2)</script>"}]},
                      quote="<b>grassetto</b>")])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'x'; Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ img: QQ('img').length, script: QQ('script').length, b: QQ('b').length,
               risultato: Q('[data-voice-result]').innerHTML });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA) + route("POST", "/api/voice/commands", ok(cattivo)))
    assert out["img"] == 0 and out["script"] == 0 and out["b"] == 0
    assert "&lt;script&gt;" in out["risultato"] and "&lt;img" in out["risultato"] and "&lt;b&gt;" in out["risultato"]


# ---------------------------------------------------------------------------
# main.js: la voce di menu
# ---------------------------------------------------------------------------

def _shell(staged, stato_voce, sessione="agent", hash_="#/voce"):  # noqa: F811
    rt = _rt()
    from tests import test_a30_13b_quick_booking_ui as a30_13b
    utente = sessione if isinstance(sessione, dict) else a30_13b._sessione(sessione)
    rotte = (f"__route('GET', '/api/operator-auth/me', {json.dumps(ok(utente))});"
             + route("GET", "/api/voice/status", stato_voce))
    driver = staged.parent / "driver-voice-6-shell.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + rt.EXTRA_DOM + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH + SHIM_SVG
        + f"\n{rotte}\nwindow.location.hash = '{hash_}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\nawait __settle(60);\n"
        + "await new Promise((r) => setTimeout(r, 20)); await __settle(60);\n"
        + "console.log(JSON.stringify({ nav: __dom.byId['nav'].children.map((b) => b.dataset.route || ''), "
          "content: __dom.byId['content'].visibleText(), title: __dom.byId['page-title'].textContent, "
          "voce: __calls.filter((c) => c.url === '/api/voice/status').length }));\n",
        encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_n1_menu_entry_appears_only_when_the_server_says_yes(staged):  # noqa: F811
    spento = _shell(staged, {"status": 404, "body": {"detail": "Not Found"}})
    assert "voce" not in spento["nav"] and "non è attivo" in spento["content"]
    acceso = _shell(staged, ACCESA)
    assert "voce" in acceso["nav"] and acceso["nav"].index("voce") > acceso["nav"].index("cestino")
    assert acceso["title"] == "Voce"
    # le altre voci restano quelle di prima, nello stesso ordine
    assert [r for r in acceso["nav"] if r != "voce"] == spento["nav"]
    assert spento["nav"][:3] == ["oggi", "agenda", "contatti"]


@node
def test_n2_platform_admin_never_gets_the_entry_nor_the_probe(staged):  # noqa: F811
    # amministratore della piattaforma SENZA agenzia: nessuna sonda e nessuna voce,
    # anche se il server (per ipotesi) rispondesse si'
    solo_platform = _shell(staged, ACCESA, sessione=_rt().PLATFORM_ADMIN, hash_="#/rete")
    assert solo_platform["voce"] == 0 and "voce" not in solo_platform["nav"]
    # in «acting» su un'agenzia la shell chiede al server, che per D-E risponde no
    acting = _shell(staged, {"status": 200, "body": {"enabled": False, "speech_input": False}},
                    sessione="supreme", hash_="#/oggi")
    assert acting["voce"] == 1 and "voce" not in acting["nav"]
