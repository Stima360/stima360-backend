"""STIMA Voice V8-4 - la scheda riepilogativa, la conferma detta e il collegamento.

Moduli puri (node) e interfaccia con lo stub di DOM e le API simulate:
  m1  il modello: righe della scheda, collegamento all'Agenda, parole di conferma
  u1  la scheda mostra tipo, cliente, incarico, data, ora, agente, note
  u2  «Conferma» detto = il pulsante: POST confirm + execute; poi «Apri in Agenda»
  u3  «Annulla» detto annulla il comando; «Cambia orario alle 16» e' una precisazione
  u4  senza nulla da confermare, «Conferma» non esegue niente e lo dice
"""
from __future__ import annotations

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_voice_6_ui import ACCESA, ASSETS, SOLO_TESTO, _node, _ui, comando, node, ok, passo, route

RIEPILOGO = {"type": "buyer_visit", "type_label": "Visita immobile", "contact": "Mario Rossi",
             "property": "Via Roma 10, Pineto (commerciale)", "needs_mandate": True, "agent": "Anna Neri",
             "start_at": "2026-10-11T15:00:00+02:00", "end_at": "2026-10-11T16:00:00+02:00", "notes": "porta i documenti"}


def visita(state="pending", **kw):
    p = passo(1, "create_appointment", state=state, quote="Mario Rossi vuole vedere casa in via Roma 10",
              start_at=RIEPILOGO["start_at"], end_at=RIEPILOGO["end_at"], **kw)
    p["summary"] = RIEPILOGO
    return p


DA_CONFERMARE = comando("awaiting_answers", [visita(needs="needs_confirmation",
    reasons=[("contact_confirmation_required", "Trovato per nome: confermi che è questo contatto?", True)],
    candidates={"contact": [{"id": 1, "label": "Mario Rossi"}]})], cid=7)
FATTO = comando("completed", [visita(state="succeeded", undo="undoable",
    result={"entity": "appointment", "id": 55, "created": True, "start_at": RIEPILOGO["start_at"]})], cid=7, concluded=True)
DOMANDA = comando("awaiting_answers", [visita(needs="needs_answer",
    reasons=[("visit_mandate_required", "Quale immobile deve visitare il cliente?", False)])], cid=7)


@node
def test_m1_model_summary_href_and_spoken_words():
    out = _node(f"""
      const m = await import('{(ASSETS / 'voice' / 'voice-model.js').as_posix()}');
      const step = {{ summary: {RIEPILOGO!r}, result: {{ entity: 'appointment', id: 55, start_at: '2026-10-11T15:00:00+02:00' }} }};
      console.log(JSON.stringify({{
        righe: m.appointmentSummary(step), href: m.appointmentHref(step),
        senza: m.appointmentSummary({{}}), hrefSenza: m.appointmentHref({{ result: {{ entity: 'contact', id: 1 }} }}),
        mezzanotte: m.romeDayKey('2026-07-25T22:30:00Z'),
        parole: ['Conferma', 'confermo.', 'ok conferma', 'Annulla', 'lascia stare', 'Cambia orario alle 16',
                 'cambia cliente: Luca Bianchi', 'conferma alle 16'].map((t) => m.spokenCommand(t)),
        manca: m.appointmentSummary({{ summary: {{ type: 'buyer_visit', type_label: 'Visita immobile', needs_mandate: true }} }}),
      }}));
    """.replace("True", "true").replace("False", "false").replace("None", "null"))
    assert out["righe"] == [["Tipo", "Visita immobile"], ["Cliente", "Mario Rossi"], ["Incarico", "Via Roma 10, Pineto (commerciale)"],
                            ["Data", "domenica 11 ottobre 2026"], ["Ora", "15:00–16:00"], ["Agente", "Anna Neri"],
                            ["Note", "porta i documenti"]]
    assert out["href"] == "#/agenda/giorno/2026-10-11" and out["senza"] == [] and out["hrefSenza"] == ""
    assert out["mezzanotte"] == "2026-07-26"                                   # ora di Roma (estate, +2), non UTC
    assert [p["kind"] for p in out["parole"]] == ["confirm", "confirm", "confirm", "cancel", "cancel", "reply", "reply", "reply"]
    assert out["parole"][5]["text"] == "Cambia orario alle 16"
    assert out["manca"] == [["Tipo", "Visita immobile"], ["Cliente", "da indicare"], ["Incarico", "da indicare"],
                            ["Data", "da indicare"], ["Ora", "da indicare"], ["Agente", "da indicare"]]


@node
def test_u1_the_summary_card_shows_every_field(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Mario Rossi vuole vedere casa in via Roma 10 domani alle 15';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      const campi = {};
      for (const e of QQ('[data-voice-summary-field]')) campi[e.dataset.voiceSummaryField] = e.visibleText();
      report({ campi, scheda: !!Q('[data-voice-summary="1"]'), apri: !!Q('[data-voice-open-appointment]'),
               suggerimento: Q('[data-voice-reply-hint]') ? Q('[data-voice-reply-hint]').visibleText() : null });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO) + route("POST", "/api/voice/commands", ok(DA_CONFERMARE)))
    assert out["scheda"] is True and out["apri"] is False
    assert out["campi"] == {"tipo": "Visita immobile", "cliente": "Mario Rossi", "incarico": "Via Roma 10, Pineto (commerciale)",
                            "data": "domenica 11 ottobre 2026", "ora": "15:00–16:00", "agente": "Anna Neri", "note": "porta i documenti"}
    assert "«Conferma»" in out["suggerimento"] and "Cambia orario" in out["suggerimento"]
    assert "<" not in out["campi"]["note"]


@node
def test_u2_spoken_confirm_equals_the_button_then_open_in_agenda(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Mario Rossi vuole vedere casa in via Roma 10 domani alle 15';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'Conferma';
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      const link = Q('[data-voice-open-appointment="1"]');
      report({ stato: Q('[data-voice-command-status]').visibleText(), link: link ? link.getAttribute('href') : null,
               testoLink: link ? link.visibleText() : null });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands/7/confirm", ok(DA_CONFERMARE))
              + route("POST", "/api/voice/commands/7/execute", ok(FATTO))
              + route("POST", "/api/voice/commands", ok(DA_CONFERMARE)))
    post = [c["url"] for c in out["calls"] if c["m"] == "POST"]
    assert post == ["/api/voice/commands", "/api/voice/commands/7/confirm", "/api/voice/commands/7/execute"]
    assert not any(u.endswith("/reply") for u in post)                          # «Conferma» non e' una precisazione
    assert out["stato"] == "Completato" and out["link"] == "#/agenda/giorno/2026-10-11" and out["testoLink"] == "Apri in Agenda"


@node
def test_u3_spoken_cancel_and_spoken_corrections(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Visita con Mario Rossi domani alle 15';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'Cambia orario alle 16';
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'Annulla';
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      report({ stato: Q('[data-voice-command-status]').visibleText() });
    """
    annullato = comando("cancelled", [visita(state="skipped")], cid=7)
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands/7/reply", ok(DA_CONFERMARE))
              + route("POST", "/api/voice/commands/7/cancel", ok(annullato))
              + route("POST", "/api/voice/commands", ok(DOMANDA)))
    post = [c for c in out["calls"] if c["m"] == "POST"]
    assert [c["url"] for c in post] == ["/api/voice/commands", "/api/voice/commands/7/reply", "/api/voice/commands/7/cancel"]
    assert post[1]["body"]["text"] == "Cambia orario alle 16"
    assert out["stato"].startswith("Annullato")


@node
def test_u4_spoken_confirm_with_nothing_to_confirm_does_nothing(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Visita con Mario Rossi domani alle 15';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'conferma';
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      report({ incarico: Q('[data-voice-summary-field="incarico"]').visibleText() });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO) + route("POST", "/api/voice/commands", ok(DOMANDA)))
    assert [c["url"] for c in out["calls"] if c["m"] == "POST"] == ["/api/voice/commands"]
    assert "nulla da confermare" in out["error"] and out["incarico"] == "Via Roma 10, Pineto (commerciale)"
