"""STIMA VOICE - collaudo iPhone del 21/10: trascrizione, nomi, comprensione.

Regole pure (node per la dettatura, Python per il piano):
  s1  l'unione dei pezzi riconosciuti: sempre uno spazio («EhiCrea» mai), il
      risultato cumulativo sostituisce, il pezzo gia' detto si scarta («RuvoRuvo»
      mai), la sovrapposizione si scrive una volta, le parole incollate si separano
  s2  la sessione del browser: finali e provvisori in ordine, senza ripetizioni
  s3  iPhone e iPad (anche iPadOS «Mac» con il tocco) riconosciuti; Mac e Android no
  s4  su iPhone la pausa NON riapre il riconoscimento (ogni apertura puo' chiedere di
      nuovo il permesso): la dettatura si ferma con `paused` e il testo; riprendere
      dal tocco aggiunge senza doppioni. Sul computer la riapertura resta automatica
  p1  il ruolo DAVANTI al nome: «Compratore Giorgio censori venditore Francesco Ruvo»
  p2  la frase del collaudo con ogni output del modello (venditore come cliente,
      cliente assente, nome invertito, tipo sbagliato): visita immobile, cliente
      Giorgio Censori (buyer), immobile cercato per il proprietario Francesco Ruvo,
      domani alle 10:30, nessuna domanda superflua
  p3  «alle ore 10:30» e «verso le ore 11» sono orari
  p4  un altro motivo detto (proposta, rogito) prevale su compratore + venditore;
      senza «compratore» il ruolo non si inventa
  n1  un nome detto a meta' («Ruvo», «Cens») e' simile al nome registrato; meno di
      3 lettere o un inizio diverso no (l'iniziale diversa la esclude gia' la query)
"""
from __future__ import annotations

from datetime import datetime, time

import pytest

from tests.test_voice_6_ui import ASSETS, _node, node
from voice import planner
from voice.dates import ROMA, fold, resolve_time
from voice.planner import build_plan
from voice.repository import NAME_SIMILARITY, name_similarity
from voice.schemas import parse_plan_output

SPEECH = (ASSETS / "voice" / "voice-speech.js").as_posix()
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=ROMA)
DETTATO = "Ehi Crea un appuntamento rivendita Domani alle ore 10:30 Compratore Giorgio censori venditore Francesco Ruvo"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1"


@node
def test_s1_unione_dei_pezzi():
    out = _node(f"""
      const s = await import('{SPEECH}');
      const u = s.unisciPezzi;
      console.log(JSON.stringify({{
        spazio: ['Ehi', 'Crea un appuntamento', 'rivendita', 'Domani alle ore 10:30', 'Compratore Giorgio censori',
                 'venditore Francesco Ruvo', 'Ruvo'].reduce(u, ''),
        cumulativo: u('crea un appuntamento', 'crea un appuntamento di vendita domani'),
        giaDetto: u('venditore Francesco Ruvo', 'Francesco Ruvo'),
        sovrapposto: u('domani alle ore 10:30', '10:30 Compratore Giorgio'),
        maiuscole: u('Giorgio Censori', 'censori compra'),
        incollate: s.separaIncollate('EhiCrea un appuntamento rivenditaDomani alle ore 10:30Compratore Giorgio'),
        accenti: s.separaIncollate('Così èDomani'),
        vuoti: [u('', 'ciao'), u('ciao', ''), u('', '')],
        diverse: u('alle 10', 'Giorgio Censori'),
      }}));
    """)
    assert out["spazio"] == "Ehi Crea un appuntamento rivendita Domani alle ore 10:30 Compratore Giorgio censori venditore Francesco Ruvo"
    assert out["cumulativo"] == "crea un appuntamento di vendita domani"
    assert out["giaDetto"] == "venditore Francesco Ruvo"
    assert out["sovrapposto"] == "domani alle ore 10:30 Compratore Giorgio"
    assert out["maiuscole"] == "Giorgio Censori compra"
    assert out["incollate"] == "Ehi Crea un appuntamento rivendita Domani alle ore 10:30 Compratore Giorgio"
    assert out["accenti"] == "Così è Domani"
    assert out["vuoti"] == ["ciao", "ciao", ""] and out["diverse"] == "alle 10 Giorgio Censori"


@node
def test_s2_risultati_di_una_sessione():
    out = _node(f"""
      const s = await import('{SPEECH}');
      const r = (t, fin) => {{ const x = [{{ transcript: t }}]; x.isFinal = fin; return x; }};
      console.log(JSON.stringify({{
        webkit: s.testoDeiRisultati([r('Ehi', true), r('Crea un appuntamento', true), r('Crea un appuntamento rivendita', false)]),
        chrome: s.testoDeiRisultati([r('crea un appuntamento', true), r(' di vendita', true), r(' domani', false)]),
        doppio: s.testoDeiRisultati([r('venditore Francesco Ruvo', true), r('Ruvo', false)]),
        vuoto: s.testoDeiRisultati(null),
      }}));
    """)
    assert out["webkit"]["testo"] == "Ehi Crea un appuntamento rivendita"
    assert out["webkit"]["provvisorio"] == "Crea un appuntamento rivendita"
    assert out["chrome"] == {"finale": "crea un appuntamento di vendita", "provvisorio": "domani",
                             "testo": "crea un appuntamento di vendita domani"}
    assert out["doppio"]["testo"] == "venditore Francesco Ruvo"
    assert out["vuoto"] == {"finale": "", "provvisorio": "", "testo": ""}


@node
def test_s3_iphone_riconosciuto():
    out = _node(f"""
      const s = await import('{SPEECH}');
      const w = (n) => ({{ navigator: n }});
      console.log(JSON.stringify([
        s.isAppleMobile(w({{ userAgent: '{IPHONE}' }})),
        s.isAppleMobile(w({{ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) CriOS/126.0 Mobile Safari' }})),
        s.isAppleMobile(w({{ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari', platform: 'MacIntel', maxTouchPoints: 5 }})),
        s.isAppleMobile(w({{ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126', platform: 'MacIntel', maxTouchPoints: 0 }})),
        s.isAppleMobile(w({{ userAgent: 'Mozilla/5.0 (Linux; Android 14) Chrome/126 Mobile' }})),
        s.isAppleMobile({{}}), s.isAppleMobile(null),
      ]));
    """)
    assert out == [True, True, True, False, False, False, False]


FAKE = r"""
const made = [];
class R { constructor() { made.push(this); this.res = []; } start() { this.started = true; }
  stop() { this.stopped = true; this.onend(); } abort() { this.aborted = true; this.onend && this.onend(); }
  say(t, fin = true) { const r = [{ transcript: t }]; r.isFinal = fin; this.res.push(r); this.onresult({ resultIndex: this.res.length - 1, results: this.res }); }
  pausa() { this.onend(); } }
"""


@node
def test_s4_su_iphone_la_pausa_non_riapre():
    out = _node(f"""
      const s = await import('{SPEECH}');
      {FAKE}
      const fine = [], stati = [];
      const d = s.createDictation({{ win: {{ webkitSpeechRecognition: R, navigator: {{ userAgent: '{IPHONE}' }} }},
        onEnd: (t, m) => fine.push([t, m]), onState: (x) => stati.push(x) }});
      d.start('');
      made[0].say('Ehi'); made[0].say('Crea un appuntamento rivendita'); made[0].say('Domani alle ore 10:30');
      made[0].pausa();                                     // iOS chiude l'ascolto alla pausa
      const inPausa = {{ creati: made.length, ascolta: d.listening, fine: fine.slice() }};
      d.start(fine[0][0]);                                 // «Continua a dettare»: dal tocco, il testo in testa
      made[1].say('10:30 Compratore Giorgio censori'); made[1].say('venditore Francesco Ruvo'); made[1].say('Ruvo', false);
      d.stop();                                            // «Termina dettatura»
      // sul computer (stesso codice) la pausa riapre da sola
      const fineMac = [];
      const mac = s.createDictation({{ win: {{ webkitSpeechRecognition: R, navigator: {{ userAgent: 'Macintosh Chrome' }} }},
        onEnd: (t, m) => fineMac.push(m) }});
      mac.start(''); made[2].say('ciao'); made[2].pausa();
      const riaperta = made.length === 4 && mac.listening;
      mac.stop();
      console.log(JSON.stringify({{ inPausa, fine, stati, creati: made.length, riaperta, fineMac }}));
    """)
    assert out["inPausa"] == {"creati": 1, "ascolta": False,
                              "fine": [["Ehi Crea un appuntamento rivendita Domani alle ore 10:30", "paused"]]}
    assert out["fine"][1] == [DETTATO, "user"]
    assert out["stati"] == ["listening", "idle", "listening", "idle"]
    assert out["riaperta"] is True and out["fineMac"] == ["user"] and out["creati"] == 4


def test_p1_ruolo_davanti_al_nome():
    assert planner.persons_with_role(fold(DETTATO)) == {
        "buyer": {"first_name": "Giorgio", "last_name": "Censori"},
        "seller": {"first_name": "Francesco", "last_name": "Ruvo"}}
    assert planner.persons_with_role(fold("visita con l'acquirente Rossi domani alle 10")) == {"buyer": {"last_name": "Rossi"}}
    assert planner.persons_with_role(fold("il proprietario dell'immobile domani")) == {}
    assert planner.persons_with_role(fold("la proprietaria signora Bianchi Anna alle 9")) == {
        "seller": {"first_name": "Bianchi", "last_name": "Anna"}}
    assert planner.persons_with_role(fold("telefonata con Mario Rossi")) == {}


def _passo(contact, tipo="sale_appointment", testo=DETTATO, quando=None):
    cmd = {"intent": "create_appointment", "quote": testo,
           "when": quando or {"date_text": "Domani", "time_text": "alle ore 10:30"}}
    if contact:
        cmd["contact"] = contact
    if tipo:
        cmd["appointment_type"] = tipo
    return build_plan(parse_plan_output({"commands": [cmd]}), testo, NOW).step(1)


@pytest.mark.parametrize("contact, tipo", [
    ({"first_name": "Francesco", "last_name": "Ruvo"}, "sale_appointment"),     # il venditore come cliente
    ({"first_name": "Giorgio", "last_name": "Censori", "role": "buyer"}, "buyer_visit"),
    ({"first_name": "Giorgio", "last_name": "Censori"}, "seller_meeting"),      # «venditore» ingannava il tipo
    ({"first_name": "Censori", "last_name": "Giorgio"}, None),                  # nome invertito, tipo assente
    (None, None),                                                                # cliente omesso
])
def test_p2_la_frase_del_collaudo(contact, tipo):
    s = _passo(contact, tipo)
    assert s.payload["appointment_type"] == "buyer_visit"
    assert s.start_at == datetime(2026, 10, 22, 10, 30, tzinfo=ROMA)
    cliente = s.refs["contact"].description
    assert {cliente.get("first_name"), cliente.get("last_name")} == {"Giorgio", "Censori"} and cliente["role"] == "buyer"
    assert s.refs["property"].description == {"owner": {"first_name": "Francesco", "last_name": "Ruvo"}}
    assert [i.code for i in s.issues] == []                                        # nessuna domanda superflua


def test_p2_manca_solo_l_orario():
    s = _passo({"first_name": "Giorgio", "last_name": "Censori"}, quando={"date_text": "Domani", "time_text": None},
               testo=DETTATO.replace(" alle ore 10:30", ""))
    assert [i.code for i in s.issues] == ["time_unrecognized"]                      # chiede solo l'orario
    assert s.payload["appointment_type"] == "buyer_visit" and "property" in s.refs and "contact" in s.refs


@pytest.mark.parametrize("testo, atteso", [
    ("alle ore 10:30", time(10, 30)), ("verso le ore 11", time(11, 0)), ("ore 15", time(15, 0)),
    ("alle 15", time(15, 0)), ("alle ore 9 e mezza", time(9, 30)),
])
def test_p3_alle_ore(testo, atteso):
    assert resolve_time(testo).at == atteso


def test_p4_il_motivo_detto_prevale_e_il_ruolo_non_si_inventa():
    testo = "Proposta domani alle ore 10:30 compratore Giorgio Censori venditore Francesco Ruvo"
    s = _passo({"first_name": "Giorgio", "last_name": "Censori"}, "proposal", testo)
    assert s.payload["appointment_type"] == "proposal"
    testo = "Appuntamento domani alle ore 10:30 con Giorgio Censori e Francesco Ruvo"
    s = _passo({"first_name": "Giorgio", "last_name": "Censori"}, None, testo)
    assert s.payload["appointment_type"] == "other" and "role" not in s.refs["contact"].description


@pytest.mark.parametrize("detto, registrato, simile", [
    ("Ruvo", "Ruvolo", True), ("Cens", "Censori", True), ("ruvolo", "Ruvolo", True), ("Rosi", "Rossi", True),
    ("Ru", "Ruvolo", False), ("Bianchi", "Ruvolo", False), ("Rusc", "Ruvolo", False),
])
def test_n1_nome_detto_a_meta(detto, registrato, simile):
    assert (name_similarity(detto, registrato) >= NAME_SIMILARITY) is simile
