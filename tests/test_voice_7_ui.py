"""STIMA Voice Fase 7 - l'interfaccia della conversazione (stub di DOM, API simulate).

Cosa prova:
  * solo un chiarimento: stato «Serve una tua risposta», la domanda in vista,
    nessun «Nessuna operazione riconosciuta», un campo per rispondere;
  * la risposta va a POST /commands/{id}/reply con una chiave nuova, il nuovo
    piano sostituisce il vecchio e la conversazione resta visibile;
  * doppio tocco = una richiesta; stessa risposta reinviata dopo un errore = stessa chiave;
  * contatto non trovato: suggerimento e «Crea nuovo contatto» che prepara la risposta;
  * correzione anche su un piano da confermare; nessuna risposta dopo l'esecuzione;
  * la risposta si puo' dettare (stesso riconoscimento, microfono solo con il permesso del server);
  * «Annulla il comando» e «Nuovo comando» restano, ma non servono per rispondere;
  * i testi del server passano come testo, mai come HTML.
"""
from __future__ import annotations

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_voice_6_ui import ACCESA, SOLO_TESTO, _ui, comando, node, ok, passo, route


def chiarimento(domanda, *, cid=7):
    v = comando("planned", [], cid=cid)
    v["clarifications"] = [domanda]
    return v


DOMANDA = "Preferisci fissare l'appuntamento domani alle 15 o alle 18:30 con Fabio?"
NUOVO = comando("awaiting_answers", [
    passo(1, "create_appointment", needs="needs_confirmation", quote="appuntamento con Fabio",
          reasons=[("contact_confirmation_required", "Trovato per nome: confermi che è questo contatto?", True)],
          candidates={"contact": [{"id": 12, "label": "Fabio Verdi"}]},
          start_at="2026-10-12T18:30:00+02:00", end_at="2026-10-12T19:30:00+02:00")], cid=8)


@node
def test_u1_a_question_is_answered_in_place_and_the_plan_is_replaced(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani appuntamento con Fabio'; Q('[data-voice-form]').dispatch('submit'); await wait();
      const prima = { stato: Q('[data-voice-command-status]').visibleText(), testo: Q('[data-voice-result]').visibleText(),
        vuoto: !!Q('[data-voice-empty]'), campo: !!Q('[data-voice-reply-text]'), etichetta: Q('[for="voice-reply-text"]').textContent,
        annulla: !!Q('[data-voice-cancel]'), nuovo: !!Q('[data-voice-new]') };
      Q('[data-voice-reply-text]').value = 'alle 18:30';
      Q('[data-voice-reply-form]').dispatch('submit'); Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      report({ prima, dopo: Q('[data-voice-result]').visibleText(), comando: Q('[data-voice-command]').dataset.voiceCommand,
               storia: Q('[data-voice-history]') ? Q('[data-voice-history]').visibleText() : null });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands/7/reply", ok(NUOVO))
              + route("POST", "/api/voice/commands", ok(chiarimento(DOMANDA))))
    p = out["prima"]
    assert p["stato"] == "Serve una tua risposta" and "Preferisci fissare" in p["testo"] and "STIMA chiede" in p["testo"]
    assert p["vuoto"] is False and "Nessuna operazione" not in p["testo"] and "Pronto" not in p["stato"]
    assert p["campo"] is True and p["etichetta"] == "La tua risposta"
    assert p["annulla"] is True and p["nuovo"] is True                       # restano, ma non servono per rispondere
    risposte = [c for c in out["calls"] if c["url"].endswith("/reply")]
    assert len(risposte) == 1                                                # doppio tocco: una sola richiesta
    assert risposte[0]["m"] == "POST" and risposte[0]["body"]["text"] == "alle 18:30"
    chiave_comando = [c for c in out["calls"] if c["url"] == "/api/voice/commands"][0]["body"]["client_command_id"]
    assert risposte[0]["body"]["client_command_id"] != chiave_comando        # la risposta e' un comando nuovo
    assert out["comando"] == "8" and "lunedì 12 ottobre" in out["dopo"] and "18:30" in out["dopo"]
    storia = out["storia"].replace(" ", "")
    assert "Comando:DomaniappuntamentoconFabio" in storia and "Risposta:alle18:30" in storia
    assert storia.index("Comando:") < storia.index("Risposta:")


@node
def test_u2_same_answer_after_an_error_reuses_the_key(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani appuntamento con Fabio'; Q('[data-voice-form]').dispatch('submit'); await wait();
      Q('[data-voice-reply-text]').value = 'alle 18:30'; Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      const errore = Q('[data-voice-error]').textContent;
      Q('[data-voice-reply-form]').dispatch('submit'); await wait();
      report({ errore });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands/7/reply", {"throw": True}, ok(NUOVO))
              + route("POST", "/api/voice/commands", ok(chiarimento(DOMANDA))))
    chiavi = [c["body"]["client_command_id"] for c in out["calls"] if c["url"].endswith("/reply")]
    assert len(chiavi) == 2 and chiavi[0] == chiavi[1]
    assert out["errore"]


@node
def test_u3_contact_not_found_offers_search_or_new_contact(staged):  # noqa: F811
    nonTrovato = comando("awaiting_answers", [
        passo(1, "create_appointment", needs="needs_answer", decision="blocked", quote="appuntamento con Fabio acquisizione",
              reasons=[("reference_not_found", "Non trovo il record indicato fra quelli che puoi vedere.", False)],
              start_at="2026-10-12T18:30:00+02:00", end_at="2026-10-12T19:30:00+02:00")])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani appuntamento con Fabio alle 18:30, acquisizione';
      Q('[data-voice-form]').dispatch('submit'); await wait();
      const suggerimento = Q('[data-voice-notfound-hint]') && Q('[data-voice-notfound-hint]').visibleText();
      Q('[data-voice-new-contact]').dispatch('click');
      report({ suggerimento, precompilato: Q('[data-voice-reply-text]').value, conferma: !!Q('[data-voice-confirm]'),
               testo: Q('[data-voice-result]').visibleText() });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands", ok(nonTrovato)))
    assert "cercarlo meglio" in out["suggerimento"] and "crearlo" in out["suggerimento"]
    assert out["precompilato"] == "Crea un nuovo contatto: "
    assert out["conferma"] is False                                           # niente da confermare finche' manca il dato
    assert "18:00" not in out["testo"] and "18:30" in out["testo"]            # data e ora restano visibili


@node
def test_u4_corrections_are_allowed_before_execution_never_after(staged):  # noqa: F811
    fatto = comando("completed", [passo(1, "create_appointment", state="succeeded", result={"id": 3}, undo="undoable")])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani alle 15 appuntamento con Fabio'; Q('[data-voice-form]').dispatch('submit'); await wait();
      const prima = { campo: !!Q('[data-voice-reply-text]'), etichetta: Q('[for="voice-reply-text"]').textContent,
                      conferma: !!Q('[data-voice-confirm]') };
      Q('[data-voice-confirm]').dispatch('click'); await wait();
      report({ prima, dopo: !!Q('[data-voice-reply-form]') });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands/8/confirm", ok(NUOVO))
              + route("POST", "/api/voice/commands/8/execute", ok(fatto))
              + route("POST", "/api/voice/commands", ok(NUOVO)))
    assert out["prima"] == {"campo": True, "etichetta": "Vuoi correggere qualcosa?", "conferma": True}
    assert out["dopo"] is False


@node
def test_u5_the_answer_can_be_dictated_with_the_same_recognizer(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'Domani appuntamento con Fabio'; Q('[data-voice-form]').dispatch('submit'); await wait();
      const prima = FakeRec.made.length;
      Q('[data-voice-reply-mic]').dispatch('click');
      FakeRec.made[FakeRec.made.length - 1].say('alle diciotto e trenta');
      FakeRec.made[FakeRec.made.length - 1].stop();
      report({ prima, creati: FakeRec.made.length, risposta: Q('[data-voice-reply-text]').value,
               comando: Q('[data-voice-text]').value, inviate: chiamate().filter((c) => c.url.endsWith('/reply')).length });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", ACCESA)
              + route("POST", "/api/voice/commands", ok(chiarimento(DOMANDA))))
    assert out["prima"] == 0 and out["creati"] == 1                         # microfono solo al tocco
    assert out["risposta"] == "alle diciotto e trenta"                       # nel campo della risposta
    assert out["comando"] == "Domani appuntamento con Fabio"                 # il comando originale resta
    assert out["inviate"] == 0                                               # parte solo con «Rispondi»


@node
def test_u6_no_mic_without_server_permission_and_server_text_stays_text(staged):  # noqa: F811
    cattivo = chiarimento("<img src=x onerror=alert(1)>")
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'x'; Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ mic: !!Q('[data-voice-reply-mic]'), img: QQ('img').length, html: Q('[data-voice-result]').innerHTML });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO) + route("POST", "/api/voice/commands", ok(cattivo)))
    assert out["mic"] is False and out["img"] == 0 and "&lt;img" in out["html"]


@node
def test_u7_nothing_recognized_still_lets_you_clarify(staged):  # noqa: F811
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'boh'; Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ stato: Q('[data-voice-command-status]').visibleText(), vuoto: Q('[data-voice-empty]').visibleText(),
               campo: !!Q('[data-voice-reply-text]') });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO)
              + route("POST", "/api/voice/commands", ok(comando("planned", []))))
    assert out["stato"] == "Nessuna operazione riconosciuta" and "precisa qui sotto" in out["vuoto"] and out["campo"] is True


@node
def test_u8_open_data_question_means_answer_first_no_half_execution(staged):  # noqa: F811
    misto = comando("awaiting_answers", [
        passo(1, "create_contact", needs="needs_confirmation", quote="Fabio Bianchi 333 6060606",
              reasons=[("composite_command_confirmation", "Comando in più passi: conferma prima di qualsiasi scrittura.", True)]),
        passo(2, "create_appointment", needs="needs_answer", quote="appuntamento acquisizione",
              reasons=[("agenda_conflict", "In quell'orario hai già un impegno: scegli un altro orario.", False)],
              start_at="2026-10-12T18:30:00+02:00", end_at="2026-10-12T19:30:00+02:00")])
    scena = r"""
      await renderVoce(host, [], { win }); await wait();
      Q('[data-voice-text]').value = 'x'; Q('[data-voice-form]').dispatch('submit'); await wait();
      report({ conferma: !!Q('[data-voice-confirm]'), esegui: !!Q('[data-voice-execute]'),
               prima: !!Q('[data-voice-answer-first]'), campo: !!Q('[data-voice-reply-text]') });
    """
    out = _ui(staged, scena, route("GET", "/api/voice/status", SOLO_TESTO) + route("POST", "/api/voice/commands", ok(misto)))
    assert {k: out[k] for k in ("conferma", "esegui", "prima", "campo")} == \
        {"conferma": False, "esegui": False, "prima": True, "campo": True}
