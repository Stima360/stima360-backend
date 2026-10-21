"""STIMA VOICE - collaudo reale del 21/10: 429 del fornitore e chi visita / chi vende.

Regole pure e interfaccia (stub di DOM, API simulate):
  01  il tempo d'attesa di un 429: Retry-After e i reset di Groq, fra 1 e 300 secondi
  02  la frase reale: «Crea un appuntamento di vendita Giorgio Censori, che va a vedere
      la casa di Francesco Ruvolo». Con l'output SBAGLIATO del modello (Francesco come
      cliente, `sale_appointment`): visita immobile, immobile cercato per proprietario,
      domanda «chi visita?», nessun salvataggio possibile
  03  con l'output corretto: visita immobile, cliente Giorgio, immobile di Francesco
  04  `sale_appointment` dal modello non si crea mai: diventa la visita immobile
  05  «la casa di X»: il proprietario, non le vie, le zone o gli aggettivi
  06  la scelta fra candidati gia' mostrati si risolve senza il modello
  u1  un 429 su una risposta: messaggio con i secondi, conversazione e testo intatti,
      «Rispondi» fermo, un secondo invio non parte
  u2  «il secondo» seleziona il candidato senza chiamare il server
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_a30_5_create_ui import staged  # noqa: F401
from tests.test_voice_6_ui import ASSETS, SOLO_TESTO, _node, _ui, comando, node, ok, passo, route
from voice import planner
from voice.dates import ROMA, fold
from voice.planner import build_plan
from voice.policy import Facts, RefFacts, decide
from voice.provider import MAX_RETRY_AFTER, retry_after_seconds
from voice.schemas import parse_plan_output

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=ROMA)
FRASE = "Crea un appuntamento di vendita Giorgio Censori, che va a vedere la casa di Francesco Ruvolo"


@pytest.mark.parametrize("intestazioni, atteso", [
    ({"Retry-After": "7"}, 7), ({"retry-after": "2.2"}, 3), ({"x-ratelimit-reset-tokens": "1m3s"}, 63),
    ({"x-ratelimit-reset-requests": "2.5s", "x-ratelimit-reset-tokens": "350ms"}, 3),
    ({}, 20), ({"retry-after": "abc"}, 20), ({"retry-after": "9999"}, MAX_RETRY_AFTER),
])
def test_01_attesa_dopo_un_429(intestazioni, atteso):
    assert retry_after_seconds(intestazioni) == atteso


def _piano(comando, testo=FRASE):
    return build_plan(parse_plan_output({"commands": [comando]}), testo, NOW)


def test_02_output_sbagliato_del_modello():
    sbagliato = {"intent": "create_appointment", "quote": FRASE, "appointment_type": "sale_appointment",
                 "when": {"date_text": "domani", "time_text": "alle 10"},
                 "contact": {"first_name": "Francesco", "last_name": "Ruvolo"}}
    passo1 = _piano(sbagliato).step(1)
    assert passo1.payload["appointment_type"] == "buyer_visit"
    assert passo1.refs["property"].description == {"owner": {"first_name": "Francesco", "last_name": "Ruvolo"}}
    assert passo1.has(planner.VISIT_CLIENT_IS_OWNER) and not passo1.has(planner.VISIT_MANDATE_REQUIRED)
    d = decide(_piano(sbagliato), Facts(refs={(1, "contact"): RefFacts(1, True), (1, "property"): RefFacts(1, False)})).of(1)
    assert d.decision == "ask" and planner.VISIT_CLIENT_IS_OWNER in d.reasons
    from voice.executor import CONFIRMABLE
    assert planner.VISIT_CLIENT_IS_OWNER not in CONFIRMABLE                 # non si supera con «Conferma»
    # anche detto solo per nome
    solo_nome = {**sbagliato, "contact": {"first_name": "Francesco"}}
    assert _piano(solo_nome).step(1).has(planner.VISIT_CLIENT_IS_OWNER)


def test_03_output_corretto():
    giusto = {"intent": "create_appointment", "quote": FRASE, "appointment_type": "buyer_visit",
              "when": {"date_text": "domani", "time_text": "alle 10"},
              "contact": {"first_name": "Giorgio", "last_name": "Censori", "role": "buyer"},
              "property": {"owner": {"first_name": "Francesco", "last_name": "Ruvolo"}}}
    passo1 = _piano(giusto).step(1)
    assert passo1.payload["appointment_type"] == "buyer_visit"
    assert passo1.refs["contact"].description["last_name"] == "Censori"
    assert passo1.refs["property"].description["owner"] == {"first_name": "Francesco", "last_name": "Ruvolo"}
    assert not passo1.has(planner.VISIT_CLIENT_IS_OWNER) and not passo1.has(planner.VISIT_MANDATE_REQUIRED)
    # senza immobile proposto dal modello, lo si ricava dalle parole («la casa di ...»)
    senza = {k: v for k, v in giusto.items() if k != "property"}
    assert _piano(senza).step(1).refs["property"].description == {"owner": {"first_name": "Francesco", "last_name": "Ruvolo"}}


def test_04_sale_appointment_diventa_visita():
    testo = "Appuntamento di vendita con Mario Rossi domani alle 10"
    p = _piano({"intent": "create_appointment", "quote": testo, "appointment_type": "sale_appointment",
                "when": {"date_text": "domani", "time_text": "alle 10"}, "contact": {"first_name": "Mario", "last_name": "Rossi"}}, testo)
    assert p.step(1).payload["appointment_type"] == "buyer_visit" and p.step(1).has(planner.VISIT_MANDATE_REQUIRED)
    assert planner.HIDDEN_TYPE_REPLACEMENTS == {"sale_appointment": "buyer_visit"}
    assert planner.appointment_type_from(fold(testo)) == "buyer_visit"


@pytest.mark.parametrize("testo, atteso", [
    (FRASE, {"first_name": "Francesco", "last_name": "Ruvolo"}),
    ("la villa del signor Rossi domani alle 10", {"last_name": "Rossi"}),
    ("l'appartamento di Bianchi in via Roma", {"last_name": "Bianchi"}),
    ("visita all'appartamento di via Roma 15", None), ("una casa di mare a Pineto", None),
    ("la casa di lusso in centro", None), ("telefonata con Mario", None),
])
def test_05_il_proprietario_dalle_parole(testo, atteso):
    assert planner.owner_from(fold(testo)) == atteso


CANDIDATI = [{"ordinal": 1, "ref": "contact", "candidates": [{"id": 41, "label": "Mario Rossi"}, {"id": 42, "label": "Mario Rossini"},
                                                             {"id": 43, "label": "Maria Rossi"}]}]


@node
def test_06_scelta_senza_modello():
    import json
    out = _node(f"""
      const m = await import('{(ASSETS / 'voice' / 'voice-model.js').as_posix()}');
      const c = {json.dumps(CANDIDATI)};
      console.log(JSON.stringify(['il secondo', '3', 'Mario Rossini', 'rossini', 'Mario', 'alle 16', 'il primo alle 16', '']
        .map((t) => (m.choiceFromText(t, c) || {{}}).id || null)));
    """)
    assert out == [42, 43, 42, 42, None, None, None, None]


SCELTA = comando("awaiting_answers", [passo(1, "create_appointment", needs="needs_confirmation", quote="visita",
    reasons=[("reference_ambiguous", "Ci sono piu' record possibili: scegli quello giusto.", True)],
    candidates={"contact": [{"id": 41, "label": "Mario Rossi"}, {"id": 42, "label": "Mario Rossini"}]},
    start_at="2026-10-22T10:00:00+02:00", end_at="2026-10-22T11:00:00+02:00")], cid=11)
DOMANDA = comando("awaiting_answers", [passo(1, "create_appointment", needs="needs_answer", quote=FRASE,
    reasons=[("visit_client_is_owner", "Il cliente indicato e' il proprietario dell'immobile: chi e' il cliente che lo visita?", False)],
    start_at="2026-10-22T10:00:00+02:00", end_at="2026-10-22T11:00:00+02:00")], cid=11)
OCCUPATO = {"status": 429, "body": {"detail": "Interpretazione momentaneamente satura: riprova fra 37 secondi",
                                    "code": "VOICE_PROVIDER_BUSY", "retry_after": 37}}


@node
def test_u1_un_429_lascia_la_conversazione_e_ferma_i_reinvii(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Crea un appuntamento di vendita Giorgio Censori, che va a vedere la casa di Francesco Ruvolo';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'Il cliente è Giorgio Censori';
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      const dopo = { errore: Q('[data-voice-error]').textContent, live: Q('[data-voice-live]').textContent,
        fermo: !!Q('[data-voice-reply-send]').disabled, testo: Q('[data-voice-reply-text]').value,
        comando: Q('[data-voice-command]').dataset.voiceCommand, domanda: Q('[data-voice-result]').visibleText() };
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      report({ dopo });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands/11/reply", OCCUPATO)
              + route("POST", "/api/voice/commands", ok(DOMANDA)))
    d = out["dopo"]
    assert "Riprova fra 37 secondi" in d["errore"] and "la conversazione restano" in d["errore"]
    assert d["live"] == "Riprova fra 37 secondi." and d["fermo"] is True
    assert d["testo"] == "Il cliente è Giorgio Censori" and d["comando"] == "11"            # niente si perde
    assert "il cliente che lo visita" in d["domanda"]
    risposte = [c for c in out["calls"] if c["url"].endswith("/reply")]
    assert len(risposte) == 1                                                                # il secondo invio non parte


@node
def test_u2_scelta_fra_candidati_senza_server(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Visita con Mario domani alle 10';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'il secondo';
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      report({ scelto: QQ('[data-voice-option="1:contact"]').filter((o) => o.checked).map((o) => o.value),
               live: Q('[data-voice-live]').textContent });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO) + route("POST", "/api/voice/commands", ok(SCELTA)))
    assert out["scelto"] == ["42"] and "Mario Rossini" in out["live"]
    assert [c["url"] for c in out["calls"] if c["m"] == "POST"] == ["/api/voice/commands"]
