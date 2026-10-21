"""STIMA VOICE - collaudo reale del 21/10: dettatura, orario passato, errori di rete.

  d1  dettatura lunga: il browser chiude il riconoscimento dopo una pausa, la
      dettatura si riapre da sola e il testo resta; solo «Termina dettatura» la chiude
  d2  nessun riavvio infinito: silenzi oltre il limite, diniego del microfono, errore
      di rete, stop volontario, interruzione della pagina, tetto ai riavvii
  t1  «oggi alle 10:30» quando sono le 12: orario detto conservato (mai eseguibile),
      domanda «Le 10:30 di oggi sono già passate. Vuoi scegliere un altro orario?»
  t2  la frase reale «Crea un appuntamento di vendita oggi alle 10:30 Giorgio Censori
      compra incarico Ruvolo Francesco»: visita, acquirente Giorgio, proprietario Ruvolo
  n1  una risposta che non arriva e' segnalata come rete (con traccia in console); un
      errore JavaScript nell'Agenda NON e' un errore di connessione
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_voice_6_ui import ASSETS, _node, node
from voice import planner, service
from voice.dates import PAST, ROMA
from voice.planner import build_plan
from voice.policy import Facts, RefFacts, decide
from voice.schemas import parse_plan_output

FAKE = r"""
const made = [];
class R { constructor() { made.push(this); this.res = []; } start() { this.started = true; }
  stop() { this.stopped = true; this.onend(); } abort() { this.aborted = true; this.onend && this.onend(); }
  say(t) { const r = [{ transcript: t }]; r.isFinal = true; this.res.push(r); this.onresult({ resultIndex: this.res.length - 1, results: this.res }); }
  pausa() { this.onend(); } }
"""


@node
def test_d1_le_pause_non_chiudono_la_dettatura():
    out = _node(f"""
      const s = await import('{(ASSETS / 'voice' / 'voice-speech.js').as_posix()}');
      {FAKE}
      const testi = [], fine = [], stati = [];
      const d = s.createDictation({{ win: {{ webkitSpeechRecognition: R }}, onText: (t) => testi.push(t),
        onEnd: (t, m) => fine.push([t, m]), onState: (x) => stati.push(x) }});
      d.start('Nota:');
      made[0].say('crea un appuntamento di vendita');
      made[0].pausa();                                   // Chrome chiude dopo il silenzio
      const dopoPausa = {{ ascolta: d.listening, creati: made.length, fine: fine.length }};
      made[1].say('oggi alle 10 e 30 Giorgio Censori');
      made[1].pausa();
      made[2].say('compra incarico Ruvolo Francesco');
      d.stop();                                          // «Termina dettatura»
      console.log(JSON.stringify({{ dopoPausa, ultimo: testi[testi.length - 1], fine, stati, creati: made.length,
        continua: made[0].continuous, ascoltaDopo: d.listening }}));
    """)
    assert out["dopoPausa"] == {"ascolta": True, "creati": 2, "fine": 0}
    atteso = "Nota: crea un appuntamento di vendita oggi alle 10 e 30 Giorgio Censori compra incarico Ruvolo Francesco"
    assert out["ultimo"] == atteso and out["fine"] == [[atteso, "user"]]
    assert out["stati"] == ["listening", "idle"] and out["creati"] == 3 and out["continua"] is True
    assert out["ascoltaDopo"] is False


@node
@pytest.mark.parametrize("scena, motivo, errore, creati, limite", [
    ("for (let i = 0; i < 10; i++) { const r = made[made.length - 1]; r.onerror({ error: 'no-speech' }); r.onend(); }",
     "limit", ["no_speech"], 7, 60),                               # 6 silenzi di fila tollerati, poi basta
    ("made[0].onerror({ error: 'not-allowed' }); made[0].onend();", "error", ["permission_denied"], 1, 60),
    ("made[0].onerror({ error: 'audio-capture' }); made[0].onend();", "error", ["no_microphone"], 1, 60),
    ("made[0].onerror({ error: 'network' }); made[0].onend();", "error", ["network"], 1, 60),
    ("made[0].say('ciao'); d.stop(); made[0].onend && made[0].onend();", "user", [], 1, 60),
    ("for (let i = 0; i < 10; i++) { const r = made[made.length - 1]; r.say('parola ' + i); r.onend(); }", "limit", [], 4, 3),
])
def test_d2_nessun_riavvio_infinito(scena, motivo, errore, creati, limite):
    out = _node(f"""
      const s = await import('{(ASSETS / 'voice' / 'voice-speech.js').as_posix()}');
      {FAKE}
      const fine = [], errori = [];
      const d = s.createDictation({{ win: {{ webkitSpeechRecognition: R }}, maxRestarts: {limite},
        onEnd: (t, m) => fine.push(m), onError: (e) => errori.push(e) }});
      d.start();
      {scena}
      console.log(JSON.stringify({{ fine, errori, creati: made.length, ascolta: d.listening }}));
    """)
    assert out["fine"] == [motivo] and out["errori"] == errore and out["creati"] == creati and out["ascolta"] is False


@node
def test_d3_interruzione_della_pagina_senza_riavvio():
    out = _node(f"""
      const s = await import('{(ASSETS / 'voice' / 'voice-speech.js').as_posix()}');
      {FAKE}
      const fine = [];
      const d = s.createDictation({{ win: {{ webkitSpeechRecognition: R }}, onEnd: (t, m) => fine.push(m) }});
      d.start(); made[0].say('mezza frase'); d.abort();
      console.log(JSON.stringify({{ fine, creati: made.length, ascolta: d.listening, abortito: !!made[0].aborted }}));
    """)
    assert out == {"fine": [], "creati": 1, "ascolta": False, "abortito": True}


ORA = datetime(2026, 10, 21, 12, 15, tzinfo=ROMA)
FRASE = "Crea un appuntamento di vendita oggi alle 10:30 Giorgio Censori compra incarico Ruvolo Francesco"


def _piano(comando, testo=FRASE):
    return build_plan(parse_plan_output({"commands": [comando]}), testo, ORA)


def test_t1_orario_passato_conservato_e_chiesto():
    p = _piano({"intent": "create_appointment", "quote": FRASE, "when": {"date_text": "oggi", "time_text": "alle 10:30"},
                "contact": {"first_name": "Giorgio", "last_name": "Censori"}})
    passo = p.step(1)
    assert passo.has(PAST) and "start_at" not in passo.payload and passo.start_at is None     # mai eseguibile
    assert passo.payload["heard_start_at"] == datetime(2026, 10, 21, 10, 30, tzinfo=ROMA)
    d = decide(p, Facts(refs={(1, "contact"): RefFacts(1, True), (1, "property"): RefFacts(1, False)})).of(1)
    assert d.decision == "ask" and PAST in d.reasons
    from voice.executor import CONFIRMABLE
    assert PAST not in CONFIRMABLE
    assert service.messaggio_orario_passato("2026-10-21T10:30:00+02:00", ORA) == \
        "Le 10:30 di oggi sono già passate. Vuoi scegliere un altro orario?"
    assert service.messaggio_orario_passato("2026-10-20T18:00:00+02:00", ORA) == \
        "Le 18:00 di ieri sono già passate. Vuoi scegliere un altro orario?"
    assert service.messaggio_orario_passato("2026-10-21T01:15:00+02:00", ORA) == \
        "L'1:15 di oggi è già passata. Vuoi scegliere un altro orario?"


def test_t2_la_frase_reale():
    p = _piano({"intent": "create_appointment", "quote": FRASE, "appointment_type": "sale_appointment",
                "when": {"date_text": "oggi", "time_text": "alle 10:30"},
                "contact": {"first_name": "Giorgio", "last_name": "Censori"}})
    passo = p.step(1)
    assert passo.payload["appointment_type"] == "buyer_visit"
    assert passo.refs["contact"].description["role"] == "buyer"
    assert passo.refs["property"].description == {"owner": {"first_name": "Ruvolo", "last_name": "Francesco"}}
    assert not passo.has(planner.VISIT_CLIENT_IS_OWNER) and not passo.has(planner.VISIT_MANDATE_REQUIRED)
    assert planner.role_from(" preliminare di compravendita con mario ") is None      # «compravendita» non e' «compra»


@node
def test_n1_rete_e_errori_dell_interfaccia():
    out = _node(f"""
      const avvisi = [];
      globalThis.console.warn = (...a) => avvisi.push(a.join(' '));
      globalThis.fetch = async () => {{ throw new TypeError('Failed to fetch'); }};
      const c = await import('{(ASSETS / 'core' / 'api-client.js').as_posix()}');
      let errore;
      try {{ await c.apiPost('/api/voice/commands', {{ text: 'x' }}); }} catch (e) {{ errore = e; }}
      const v = await import('{(ASSETS / 'voice' / 'voice-model.js').as_posix()}');
      const a = await import('{(ASSETS / 'agenda' / 'agenda-model.js').as_posix()}');
      console.log(JSON.stringify({{ rete: errore.network === true, causa: String(errore.cause), avvisi,
        voce: v.errorMessage(errore),
        agendaRete: a.errorMessage({{ status: 0, message: 'x' }}),
        agendaJs: a.errorMessage(new TypeError("Cannot read properties of null (reading 'value')")) }}));
    """)
    assert out["rete"] is True and "Failed to fetch" in out["causa"]
    assert out["avvisi"] and "POST /api/voice/commands" in out["avvisi"][0] and "Failed to fetch" in out["avvisi"][0]
    assert "non si crea un doppione" in out["voce"]
    assert out["agendaRete"].startswith("Impossibile contattare il server")
    assert out["agendaJs"].startswith("Errore imprevisto nell’interfaccia") and "Impossibile contattare" not in out["agendaJs"]
