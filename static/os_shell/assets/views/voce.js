// STIMA360 OS — views/voce.js
// STIMA Voice Fase 6: la pagina «Voce» (`#/voce`), mobile-first.
//
// Il percorso: scrivere o dettare -> «Invia» -> il piano (cosa ho capito) ->
// scelte, domande, conferme -> esecuzione -> risultato, con «Annulla» solo
// dove il server lo consente. Ogni decisione resta del server: la pagina non
// filtra, non deduce permessi, non sceglie id. Una conferma qui e' solo una
// richiesta: il backend ricontrolla tutto.
//
// Spenta per costruzione: senza `GET /api/voice/status` -> enabled (router non
// montato, interruttori spenti, fornitore assente) la pagina mostra solo un
// avviso, senza pulsanti. Il microfono compare solo se il server consente il
// riconoscimento del browser (`speech_input`) E il browser lo supporta.
//
// Nessuno storage del browser: la chiave del comando vive in memoria. I dati
// del server passano sempre da `escapeHtml` o `textContent`.

import { escapeHtml, renderBadge } from '../components/st-table.js';
import * as api from '../voice/voice-api.js';
import {
  SPEECH_ERRORS, UNDO_TEXT, appointmentHref, appointmentSummary, canReply, choicesNeeded, commandStatus, errorMessage,
  hasRunnableSteps, intentLabel, openQuestions, referenceNotFound, resultText, spokenCommand, stepDescription,
  stepStatus, stepsToConfirm,
} from '../voice/voice-model.js';
import { createDictation, speechSupport } from '../voice/voice-speech.js';

const RIF = { contact: 'il contatto', property: 'l’immobile', building: 'la palazzina', agent: 'l’agente' };
const AGGIORNA = ['planned', 'awaiting_answers', 'executing', 'partial'];

function badge(s) { return renderBadge(s.label, s.tone); }

function passoHtml(step) {
  const n = Number(step.ordinal);
  const motivi = (step.state === 'pending' || step.state === 'failed') ? (step.reasons || []) : [];
  const scelte = step.state === 'pending' ? choicesNeeded(step) : [];
  const doppioni = step.state === 'pending' && step.duplicates && step.duplicates.length
    ? `<p class="voice-dupes" data-voice-dupes>Possibili doppioni: ${step.duplicates.map((d) => escapeHtml(d.label)).join(', ')}</p>`
    : (step.state === 'pending' && step.hidden_duplicate
      ? '<p class="voice-dupes" data-voice-dupes>Un collega segue già un contatto con questi recapiti.</p>' : '');
  const risultato = resultText(step);
  let azioni = '';
  if (step.state === 'succeeded' && step.undo === 'undoable') {
    azioni = `<button type="button" class="btn" data-voice-undo="${n}">Annulla questo passo</button>`;
  } else if (step.state === 'failed') {
    azioni = `<button type="button" class="btn" data-voice-retry="${n}">Riprova</button>`;
  } else if (step.state === 'indeterminate') {
    azioni = `<button type="button" class="btn" data-voice-verify="${n}">Verifica cosa è successo</button>`;
  }
  const annullo = step.state === 'succeeded' && step.undo && step.undo !== 'undoable' && UNDO_TEXT[step.undo]
    ? `<p class="muted voice-undo-note" data-voice-undo-note>${escapeHtml(UNDO_TEXT[step.undo])}</p>` : '';
  // V8-4: la scheda riepilogativa dell'appuntamento e, a cose fatte, «Apri in Agenda»
  const riepilogo = appointmentSummary(step);
  const scheda = riepilogo.length ? `<dl class="voice-summary" data-voice-summary="${n}">${riepilogo.map(([k, v]) =>
    `<div class="voice-summary-row"><dt>${escapeHtml(k)}</dt><dd data-voice-summary-field="${escapeHtml(k.toLowerCase())}">${escapeHtml(v)}</dd></div>`).join('')}</dl>` : '';
  const apri = step.state === 'succeeded' && appointmentHref(step)
    ? `<a class="btn" href="${escapeHtml(appointmentHref(step))}" data-voice-open-appointment="${n}">Apri in Agenda</a>` : '';
  return `
    <li class="voice-step" data-voice-step="${n}">
      <div class="voice-step-head">
        <strong>${escapeHtml(intentLabel(step.intent))}</strong>
        <span data-voice-step-status>${badge(stepStatus(step))}</span>
      </div>
      <p class="voice-step-desc" data-voice-step-desc>${escapeHtml(stepDescription(step))}</p>
      ${scheda}
      ${motivi.length ? `<ul class="voice-reasons" data-voice-reasons>${motivi.map((m) =>
        `<li>${escapeHtml(m.message)}</li>`).join('')}</ul>` : ''}
      ${scelte.map(([ref, lista]) => `
        <fieldset class="voice-choice" data-voice-choice="${n}:${escapeHtml(ref)}">
          <legend>Scegli ${escapeHtml(RIF[ref] || ref)}</legend>
          ${lista.map((c) => `<label class="voice-option"><input type="radio" name="voice-${n}-${escapeHtml(ref)}"
            value="${Number(c.id)}" data-voice-option="${n}:${escapeHtml(ref)}"> ${escapeHtml(c.label)}</label>`).join('')}
        </fieldset>`).join('')}
      ${doppioni}
      ${risultato ? `<p class="voice-step-result" data-voice-step-result>${escapeHtml(risultato)}</p>` : ''}
      ${step.state === 'failed' && step.error ? `<p class="field-error" data-voice-step-error>Motivo: ${escapeHtml(step.error)}</p>` : ''}
      ${annullo}
      <div class="voice-step-actions">${azioni}${apri}</div>
      <div class="voice-verify" data-voice-verify-panel="${n}" hidden></div>
    </li>`;
}

/**
 * Fase 7: rispondere o correggere senza perdere il comando. Visibile finche'
 * il comando non ha eseguito nulla; la risposta crea un comando nuovo sul
 * server (stesso percorso, nuove conferme) che sostituisce questo.
 */
function rispostaHtml(view, conMicrofono) {
  if (!canReply(view)) return '';
  const domande = openQuestions(view);
  const nonTrovato = referenceNotFound(view);
  return `
    <form class="voice-reply" data-voice-reply-form novalidate>
      <label for="voice-reply-text" class="voice-label">${domande.length ? 'La tua risposta' : 'Vuoi correggere qualcosa?'}</label>
      <textarea id="voice-reply-text" class="voice-text" data-voice-reply-text rows="2" maxlength="1500"
        placeholder="Es. «Conferma», «Annulla», «Cambia orario alle 18:30», «Cambia cliente: Fabio Rossi»"></textarea>
      <p class="muted voice-hint" data-voice-reply-hint>Puoi dire «Conferma» per eseguire, «Annulla», oppure correggere: «Cambia orario…», «Cambia cliente…», «Cambia immobile…».</p>
      ${nonTrovato ? `<p class="muted voice-hint" data-voice-notfound-hint>Non lo trovo fra i record che puoi vedere.
        Aggiungi un dettaglio per cercarlo meglio (cognome, telefono, indirizzo) oppure chiedi di crearlo.</p>` : ''}
      <div class="voice-compose-actions">
        ${conMicrofono ? `<button type="button" class="btn voice-mic voice-big" data-voice-reply-mic aria-pressed="false"
          aria-label="Parla: detta la risposta">🎤 Parla</button>` : ''}
        <button type="submit" class="btn primary voice-big" data-voice-reply-send>Rispondi</button>
        ${nonTrovato ? '<button type="button" class="btn" data-voice-new-contact>Crea nuovo contatto</button>' : ''}
      </div>
    </form>`;
}

function conversazioneHtml(storia) {
  if (!storia || storia.length < 2) return '';
  return `<ol class="voice-history" data-voice-history aria-label="Conversazione">${storia.map((t, i) =>
    `<li><span class="muted">${i === 0 ? 'Comando' : 'Risposta'}:</span> ${escapeHtml(t)}</li>`).join('')}</ol>`;
}

export function commandHtml(view, opzioni = {}) {
  const stato = commandStatus(view);
  const daConfermare = stepsToConfirm(view);
  // Fase 7: anche un comando con sola domanda si puo' annullare.
  const aperto = canReply(view) || (AGGIORNA.includes(view.status) && (view.steps || []).some((s) => s.state === 'pending'));
  let azioni = '';
  // Fase 7: se un passo aspetta un DATO (orario, persona, indirizzo...) si
  // risponde prima: la risposta aggiorna il piano e solo dopo si conferma.
  // Confermare il resto adesso eseguirebbe mezzo comando.
  const domandeAperte = (view.steps || []).some((s) => s.state === 'pending' && s.needs === 'needs_answer');
  if (domandeAperte && canReply(view)) {
    azioni += '<p class="voice-confirm-note" data-voice-answer-first>Rispondi alla domanda qui sopra: il piano si aggiorna e poi potrai confermare.</p>';
  } else if (daConfermare.length) {
    azioni += `<p class="voice-confirm-note" data-voice-confirm-note>Confermando, STIMA Voice eseguirà i passi «Da confermare» qui sopra. Il server ricontrolla dati e permessi.</p>
      <button type="button" class="btn primary voice-big" data-voice-confirm>Conferma ed esegui</button>`;
  } else if (hasRunnableSteps(view) && view.status !== 'completed') {
    azioni += '<button type="button" class="btn primary voice-big" data-voice-execute>Esegui</button>';
  }
  if (aperto) azioni += '<button type="button" class="btn ghost" data-voice-cancel>Annulla il comando</button>';
  azioni += '<button type="button" class="btn ghost" data-voice-new>Nuovo comando</button>';
  return `
    <section class="card voice-result" data-voice-command="${Number(view.command_id)}">
      <div class="voice-result-head">
        <strong>Cosa ho capito</strong>
        <span data-voice-command-status>${badge(stato)}</span>
      </div>
      ${view.notice ? `<p class="voice-notice" data-voice-notice role="note">${escapeHtml(view.notice)}</p>` : ''}
      ${conversazioneHtml(opzioni.storia)}
      ${(view.clarifications || []).length ? `<div class="voice-question" data-voice-clarifications>
        <strong>STIMA chiede:</strong>
        <ul class="voice-reasons">${view.clarifications.map((c) => `<li>${escapeHtml(c)}</li>`).join('')}</ul></div>` : ''}
      ${(view.steps || []).length ? `<ol class="voice-steps">${view.steps.map(passoHtml).join('')}</ol>`
        : ((view.clarifications || []).length ? ''
          : '<p class="muted" data-voice-empty>Non ho riconosciuto un’operazione: precisa qui sotto o riformula.</p>')}
      ${rispostaHtml(view, opzioni.conMicrofono)}
      <div class="voice-command-actions">${azioni}</div>
    </section>`;
}

function paginaHtml(conMicrofono, avviso) {
  return `
    <section class="voice-page" data-voice-page>
      <form class="card voice-compose" data-voice-form novalidate>
        <label for="voice-text" class="voice-label">Il tuo comando</label>
        <textarea id="voice-text" class="voice-text" data-voice-text rows="3" maxlength="2000"
          placeholder="Es. «Domani alle 15 appuntamento con Fabio»"></textarea>
        <div class="voice-compose-actions">
          ${conMicrofono ? `<button type="button" class="btn voice-mic voice-big" data-voice-mic aria-pressed="false"
            aria-label="Parla: avvia la dettatura">🎤 Parla</button>` : ''}
          <button type="submit" class="btn primary voice-big" data-voice-send>Invia</button>
        </div>
        <p class="muted voice-hint" data-voice-hint>${escapeHtml(avviso)}</p>
        <div class="voice-live muted" data-voice-live role="status" aria-live="polite"></div>
        <div class="field-error" data-voice-error role="alert"></div>
      </form>
      <div data-voice-result></div>
    </section>`;
}

export async function renderVoce(container, _params = [], deps = {}) {
  const win = deps.win || globalThis.window;
  const stato = await (deps.status || api.voiceStatus)();
  if (!stato.enabled) {
    container.innerHTML = `<section class="voice-page" data-voice-off>
      <p class="muted">STIMA Voice non è attivo.</p></section>`;
    return;
  }
  const supporto = speechSupport(win);
  const conMicrofono = stato.speechInput && supporto.supported;
  const avviso = conMicrofono
    ? 'Tocca «Parla», detta il comando e ricontrollalo prima di inviarlo.'
    : 'Scrivi il comando. Sul telefono puoi usare anche il microfono della tastiera.';
  container.innerHTML = paginaHtml(conMicrofono, avviso);

  const q = (sel) => container.querySelector(sel);
  const testo = q('[data-voice-text]');
  const invia = q('[data-voice-send]');
  const errore = q('[data-voice-error]');
  const live = q('[data-voice-live]');
  const area = q('[data-voice-result]');
  const mic = q('[data-voice-mic]');
  // La chiave appartiene al TESTO: stesso testo reinviato = stessa chiave.
  const s = { busy: false, key: null, keyText: null, view: null, storia: [], replyKey: null, replyFor: null };

  const mostraErrore = (msg) => { errore.textContent = msg || ''; };
  const occupato = (si) => {
    s.busy = si;
    invia.disabled = si;
    for (const b of area.querySelectorAll('button')) b.disabled = si;
    for (const t of area.querySelectorAll('textarea')) t.readOnly = si;
    live.textContent = si ? 'Elaborazione…' : '';
  };

  function disegna(view) {
    s.view = view;
    area.innerHTML = commandHtml(view, { storia: s.storia, conMicrofono });
    collega();
  }

  async function azione(fn) {
    if (s.busy) return;                       // doppio tocco: una sola richiesta
    mostraErrore('');
    occupato(true);
    try {
      const view = await fn();
      if (view) disegna(view);
    } catch (e) {
      mostraErrore(errorMessage(e));
    } finally {
      occupato(false);
    }
  }

  const id = () => s.view && s.view.command_id;

  function scelteDi(step) {
    const scelte = {};
    for (const [ref] of choicesNeeded(step)) {
      const opzioni = [...area.querySelectorAll(`[data-voice-option="${step.ordinal}:${ref}"]`)];
      const scelta = opzioni.find((o) => o.checked);
      if (!scelta) return null;
      scelte[ref] = Number(scelta.value);
    }
    return scelte;
  }

  function confermaEdEsegui() {
    const passi = [];
    for (const step of stepsToConfirm(s.view)) {
      const scelte = scelteDi(step);
      if (scelte === null) { mostraErrore(`Scegli un’opzione per «${intentLabel(step.intent)}».`); return; }
      passi.push({ ordinal: step.ordinal, choices: Object.keys(scelte).length ? scelte : null });
    }
    azione(async () => {
      await api.confirmSteps(id(), passi);
      return api.executeCommand(id());
    });
  }

  function collega() {
    const conferma = area.querySelector('[data-voice-confirm]');
    if (conferma) conferma.addEventListener('click', confermaEdEsegui);
    const esegui = area.querySelector('[data-voice-execute]');
    if (esegui) esegui.addEventListener('click', () => azione(() => api.executeCommand(id())));
    const annullaComando = area.querySelector('[data-voice-cancel]');
    if (annullaComando) annullaComando.addEventListener('click', () => azione(() => api.cancelCommand(id())));
    const risposta = area.querySelector('[data-voice-reply-form]');
    if (risposta) {
      const campo = area.querySelector('[data-voice-reply-text]');
      risposta.addEventListener('submit', (event) => {
        event.preventDefault();
        const testoRisposta = String(campo.value || '').trim();
        if (!testoRisposta) { mostraErrore('Scrivi o detta la risposta.'); campo.focus(); return; }
        if (dettatura && dettatura.listening) dettatura.stop();
        // V8-4: «Conferma» e «Annulla» detti valgono come i pulsanti; il server ricontrolla tutto.
        const detto = spokenCommand(testoRisposta);
        if (detto.kind === 'confirm') {
          if (area.querySelector('[data-voice-confirm]')) { confermaEdEsegui(); return; }
          if (area.querySelector('[data-voice-execute]')) { azione(() => api.executeCommand(id())); return; }
          mostraErrore('Non c’è nulla da confermare: rispondi prima alla domanda qui sopra.');
          return;
        }
        if (detto.kind === 'cancel') { azione(() => api.cancelCommand(id())); return; }
        const per = `${id()}\n${testoRisposta}`;          // la chiave appartiene a (comando, risposta)
        if (s.replyFor !== per) { s.replyKey = api.newCommandKey(); s.replyFor = per; }
        const chiave = s.replyKey;
        azione(async () => {
          const view = await api.replyCommand(id(), chiave, testoRisposta);
          s.storia.push(testoRisposta);
          s.replyKey = null; s.replyFor = null;
          return view;
        });
      });
      const nuovoContatto = area.querySelector('[data-voice-new-contact]');
      if (nuovoContatto) nuovoContatto.addEventListener('click', () => {
        if (!campo.value.trim()) campo.value = 'Crea un nuovo contatto: ';
        campo.focus();
      });
      const micRisposta = area.querySelector('[data-voice-reply-mic]');
      if (micRisposta && dettatura) micRisposta.addEventListener('click', () => avviaDettatura(campo, micRisposta));
    }
    const nuovo = area.querySelector('[data-voice-new]');
    if (nuovo) nuovo.addEventListener('click', () => {
      if (s.busy) return;
      area.innerHTML = '';
      s.view = null; s.key = null; s.keyText = null; s.storia = []; s.replyKey = null; s.replyFor = null;
      testo.value = '';
      mostraErrore('');
      testo.focus();
    });
    for (const b of area.querySelectorAll('[data-voice-undo]')) {
      b.addEventListener('click', () => azione(async () => {
        await api.undoStep(id(), b.dataset.voiceUndo);
        return api.getCommand(id());
      }));
    }
    for (const b of area.querySelectorAll('[data-voice-retry]')) {
      b.addEventListener('click', () => azione(async () => {
        await api.retryStep(id(), b.dataset.voiceRetry);
        return api.executeCommand(id());
      }));
    }
    for (const b of area.querySelectorAll('[data-voice-verify]')) {
      b.addEventListener('click', () => verifica(Number(b.dataset.voiceVerify)));
    }
  }

  async function verifica(n) {
    if (s.busy) return;
    const pannello = area.querySelector(`[data-voice-verify-panel="${n}"]`);
    occupato(true);
    try {
      const analisi = await api.reconcileInspect(id(), n);
      const presente = analisi.outcome === 'present';
      const candidati = analisi.candidates || [];
      pannello.innerHTML = `
        <p data-voice-verify-text>${presente
          ? 'Il collegamento risulta presente.'
          : (candidati.length ? 'Possibili record creati in quel momento: scegli quello giusto, se c’è.'
            : 'Non trovo un record corrispondente, ma questo non prova che non sia stato creato.')}</p>
        ${candidati.map((c) => `<label class="voice-option"><input type="radio" name="voice-verify-${n}"
          value="${Number(c.id)}" data-voice-verify-option> ${escapeHtml(c.label)}</label>`).join('')}
        <div class="voice-step-actions">
          ${presente || candidati.length ? '<button type="button" class="btn" data-voice-present>Sì, è stato fatto</button>' : ''}
          <button type="button" class="btn ghost" data-voice-absent>No, non è stato fatto</button>
        </div>`;
      pannello.hidden = false;
      const si = pannello.querySelector('[data-voice-present]');
      if (si) si.addEventListener('click', () => {
        const scelta = [...pannello.querySelectorAll('[data-voice-verify-option]')].find((o) => o.checked);
        if (!presente && !scelta) { mostraErrore('Scegli il record creato.'); return; }
        azione(() => api.reconcileDecide(id(), n, 'present', scelta ? Number(scelta.value) : null));
      });
      pannello.querySelector('[data-voice-absent]').addEventListener('click',
        () => azione(() => api.reconcileDecide(id(), n, 'absent')));
    } catch (e) {
      mostraErrore(errorMessage(e));
    } finally {
      occupato(false);
    }
  }

  q('[data-voice-form]').addEventListener('submit', (event) => {
    event.preventDefault();
    const comando = String(testo.value || '').trim();
    if (!comando) { mostraErrore('Scrivi o detta un comando.'); return; }
    if (dettatura && dettatura.listening) dettatura.stop();
    if (s.keyText !== comando) { s.key = api.newCommandKey(); s.keyText = comando; }
    azione(async () => {
      const view = await api.submitCommand(s.key, comando);
      s.storia = [comando];
      return view;
    });
  });

  // Una sola dettatura per pagina: scrive nel campo da cui e' partita (il
  // comando o la risposta) e aggiorna il SUO pulsante.
  let dettatura = null;
  let bersaglio = testo;
  let pulsante = mic;
  function avviaDettatura(campo, bottone) {
    mostraErrore('');
    if (dettatura.listening) { dettatura.stop(); return; }
    bersaglio = campo; pulsante = bottone;
    dettatura.start();
  }
  if (mic) {
    dettatura = createDictation({
      win,
      onText: (t) => { if (bersaglio && bersaglio.isConnected !== false) bersaglio.value = t; },
      onState: (st) => {
        const ascolta = st === 'listening';
        if (!pulsante) return;
        pulsante.setAttribute('aria-pressed', ascolta ? 'true' : 'false');
        pulsante.setAttribute('aria-label', ascolta ? 'Ferma la dettatura' : 'Parla: avvia la dettatura');
        pulsante.textContent = ascolta ? '■ Ferma' : '🎤 Parla';
        pulsante.classList.toggle('listening', ascolta);
        live.textContent = ascolta ? 'In ascolto… parla ora, poi tocca «Ferma».' : '';
      },
      onError: (codice) => mostraErrore(SPEECH_ERRORS[codice] || SPEECH_ERRORS.error),
    });
    mic.addEventListener('click', () => avviaDettatura(testo, mic));
    // Niente in sottofondo: pagina nascosta, uscita o cambio di sezione = stop.
    const interrompi = () => dettatura.abort();
    if (win && typeof win.addEventListener === 'function') {
      win.addEventListener('pagehide', interrompi);
      win.addEventListener('hashchange', interrompi);
    }
    if (typeof document !== 'undefined' && document && typeof document.addEventListener === 'function') {
      document.addEventListener('visibilitychange', () => { if (document.hidden) interrompi(); });
    }
  }
}
