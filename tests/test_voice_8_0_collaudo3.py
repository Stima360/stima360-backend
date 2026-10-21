"""STIMA VOICE - collaudo 3 (21/10): regole pure.

  n1  le parole di un nome: senza accenti, in qualunque ordine, anche dal nome completo
  n2  cognomi storpiati: «Ruvo», «Rivo», «Rivolo» -> Ruvolo; «Bianchi», «Ru» no
  n3  corrispondenza di persona: copertura e media; il solo nome «Francesco» e' parziale
  w1  una risposta che e' SOLO un giorno, un orario o la parte del giorno
  w2  «10»: giorno o ora secondo la domanda; senza domanda va al modello
  w3  nomi, correzioni e frasi non sono date: vanno al modello
  w4  la risposta si applica all'UNICO appuntamento con la domanda; con due
      appuntamenti senza domanda va al modello; le domande del modello sul
      quando si tolgono, le altre restano
  u1  la scelta locale fra candidati: per nome anche con piu' scelte aperte;
      il numero solo con una scelta aperta
  u2  il candidato unico proposto: «sì», «Ruvolo» lo riconoscono (senza confermare)
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_voice_6_ui import ASSETS, _node, node
from voice import planner
from voice.dates import AMBIGUOUS_HOUR, ROMA, UNRECOGNIZED_DATE, UNRECOGNIZED_TIME
from voice.repository import NAME_SIMILARITY, name_tokens, person_match, token_similarity
from voice.schemas import parse_plan_output

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=ROMA)
MODEL = (ASSETS / "voice" / "voice-model.js").as_posix()


def test_n1_parole_del_nome():
    assert name_tokens("Francesco", "Ruvolo") == ["francesco", "ruvolo"]
    assert name_tokens("RUVOLO Francesco") == ["ruvolo", "francesco"]
    assert name_tokens("Nicolò D'Amico", None) == ["nicolo", "amico"]
    assert name_tokens(None, "") == []


@pytest.mark.parametrize("detto, registrato, simile", [
    ("ruvo", "ruvolo", True), ("rivo", "ruvolo", True), ("rivolo", "ruvolo", True), ("rosi", "rossi", True),
    ("censori", "censori", True), ("ru", "ruvolo", False), ("bianchi", "ruvolo", False), ("mario", "ruvolo", False),
    ("rusc", "ruvolo", False),
])
def test_n2_cognomi_storpiati(detto, registrato, simile):
    assert (token_similarity(detto, registrato) >= NAME_SIMILARITY) is simile


def test_n3_corrispondenza_di_persona():
    assert person_match(["francesco", "rivo"], ["francesco", "ruvolo"])[0] == 1.0
    assert person_match(["rivo", "francesco"], [], ["francesco", "ruvolo"])[0] == 1.0      # dal nome completo
    assert person_match(["francesco", "ruvo"], ["francesco"]) == (0.5, 1.0)                # parziale
    assert person_match(["mario", "bianchi"], ["francesco", "ruvolo"]) is None
    assert person_match(["francesco", "ruvolo"], ["francesco", "ruvolo", "maria"])[0] == 1.0  # secondo nome


@pytest.mark.parametrize("risposta, chiesto, atteso", [
    ("domani", {UNRECOGNIZED_DATE}, {"date_text": "domani"}),
    ("Domani.", {UNRECOGNIZED_DATE}, {"date_text": "domani"}),
    ("sì, domani grazie", {UNRECOGNIZED_DATE}, {"date_text": "domani"}),
    ("facciamo giovedì", {UNRECOGNIZED_DATE}, {"date_text": "giovedi"}),
    ("il 22 ottobre", {UNRECOGNIZED_DATE}, {"date_text": "22 ottobre"}),
    ("10 e 30", {UNRECOGNIZED_TIME}, {"time_text": "10 e 30"}),
    ("alle 10:30", {UNRECOGNIZED_TIME}, {"time_text": "alle 10:30"}),
    ("cambia orario alle 18:30", set(), {"time_text": "alle 18:30"}),
    ("domani alle 10 e mezza", set(), {"date_text": "domani", "time_text": "alle 10 e mezza"}),
    ("pomeriggio", {AMBIGUOUS_HOUR}, {"time_text": "alle tre del pomeriggio"}),
    ("di sera", {AMBIGUOUS_HOUR}, {"time_text": "alle tre di sera"}),
    ("domani pomeriggio", {UNRECOGNIZED_DATE}, {"date_text": "domani pomeriggio"}),
])
def test_w1_giorno_ora_o_parte_del_giorno(risposta, chiesto, atteso):
    assert planner.when_from_reply(risposta, chiesto, NOW, "alle tre") == atteso


def test_w2_dieci_dipende_dalla_domanda():
    assert planner.when_from_reply("10", {UNRECOGNIZED_TIME}, NOW) == {"time_text": "10"}
    assert planner.when_from_reply("10", {UNRECOGNIZED_DATE}, NOW) == {"date_text": "10"}
    assert planner.when_from_reply("10", set(), NOW) is None
    assert planner.when_from_reply("pomeriggio", set(), NOW, "alle tre") is None          # nessuna domanda sull'ora


@pytest.mark.parametrize("risposta", [
    "Giorgio Censori", "il cliente è Mario Rossi", "no, la casa di Bianchi", "cambia cliente: Fabio Rossi",
    "annulla", "conferma", "", "  ", "in via Roma 15",
])
def test_w3_non_sono_date(risposta):
    assert planner.when_from_reply(risposta, {UNRECOGNIZED_DATE, UNRECOGNIZED_TIME}, NOW) is None


def _output(*quando, clarifications=()):
    return parse_plan_output({"commands": [
        {"intent": "create_appointment", "quote": "q", "appointment_type": "call", "when": w,
         "contact": {"first_name": "Giorgio", "last_name": "Censori"}} for w in quando],
        "clarifications": list(clarifications)})


def test_w4_un_solo_appuntamento_bersaglio():
    out = _output({"date_text": "prossimamente", "time_text": "alle 10"},
                  clarifications=["Quale giorno?", "Il cliente e' Giorgio Censori di Pineto?"])
    nuovo = planner.apply_local_reply(out, {1: {UNRECOGNIZED_DATE}}, "domani", NOW)
    assert nuovo.commands[0].when.date_text == "domani" and nuovo.commands[0].when.time_text == "alle 10"
    assert nuovo.clarifications == ["Il cliente e' Giorgio Censori di Pineto?"]
    assert nuovo.commands[0].contact == out.commands[0].contact
    due = _output({"date_text": "domani", "time_text": "alle 10"}, {"date_text": "x", "time_text": "alle 11"})
    assert planner.apply_local_reply(due, {2: {UNRECOGNIZED_DATE}}, "giovedì", NOW).commands[1].when.date_text == "giovedi"
    assert planner.apply_local_reply(due, {}, "alle 12", NOW) is None                      # quale dei due? il modello
    assert planner.apply_local_reply(out, {1: {UNRECOGNIZED_DATE}}, "Mario Rossi", NOW) is None


@node
def test_u1_scelta_locale_fra_candidati():
    out = _node(f"""
      const m = await import('{MODEL}');
      const due = [
        {{ ordinal: 1, ref: 'contact', candidates: [{{ id: 1, label: 'Francesco Ruvolo' }}, {{ id: 2, label: 'Ernesto Ruvolo' }}] }},
        {{ ordinal: 1, ref: 'property', candidates: [{{ id: 7, label: 'Via Roma 10 — proprietario Francesco Ruvolo' }},
                                                   {{ id: 8, label: 'Via Po 3 — proprietario Anna Bianchi' }}] }},
      ];
      console.log(JSON.stringify({{
        nome: (m.choiceFromText('Ernesto', due) || {{}}).id || null,
        via: (m.choiceFromText('via Po', due) || {{}}).id || null,
        ambiguo: m.choiceFromText('Francesco Ruvolo', due),
        numeroDue: m.choiceFromText('il secondo', due),
        numeroUno: (m.choiceFromText('il secondo', [due[0]]) || {{}}).id || null,
      }}));
    """)
    assert out == {"nome": 2, "via": 8, "ambiguo": None, "numeroDue": None, "numeroUno": 2}


@node
def test_u2_candidato_unico_proposto():
    out = _node(f"""
      const m = await import('{MODEL}');
      const uno = [{{ ordinal: 1, ref: 'contact', id: 183, label: 'Francesco Ruvolo' }}];
      const due = [...uno, {{ ordinal: 1, ref: 'property', id: 7, label: 'Via Roma 10' }}];
      const id = (x) => (x || {{}}).id || null;
      console.log(JSON.stringify([
        id(m.proposedFromText('sì', uno)), id(m.proposedFromText('Sì, è lui', uno)), id(m.proposedFromText('Ruvolo', uno)),
        id(m.proposedFromText('sì Francesco Ruvolo', uno)), id(m.proposedFromText('sì', due)),
        id(m.proposedFromText('via Roma', due)), id(m.proposedFromText('Mario Rossi', uno)), id(m.proposedFromText('sì', [])),
      ]));
    """)
    assert out == [183, 183, 183, 183, None, 7, None, None]
