// STIMA360 OS — voice/voice-model.js
// STIMA Voice Fase 6: dalla risposta del server a cio' che si mostra.
// Funzioni pure (nessun DOM, nessuna rete): si provano da sole.
//
// Regole:
//   * un comando `partial` non si chiama mai «completato»;
//   * un passo `indeterminate` non si mostra mai come riuscito;
//   * «Annulla» solo quando il server dice `undoable` (finestra di 15 minuti,
//     blocchi del CRM e permessi li decide lui);
//   * una domanda che chiede dati non ha un «Conferma»: si riscrive il comando.

export const INTENT_LABELS = {
  create_contact: 'Nuovo contatto',
  create_unit: 'Nuova scheda immobile',
  create_building: 'Nuova palazzina',
  link_owner: 'Collega il proprietario',
  activate_seller: 'Attiva «Vende»',
  create_appointment: 'Nuovo appuntamento',
  add_note: 'Nota',
  add_task: 'Promemoria',
  unsupported: 'Richiesta non prevista',
};

export const COMMAND_STATUS = {
  received: { label: 'In elaborazione', tone: 'gray' },
  planned: { label: 'Pronto', tone: 'gray' },
  awaiting_answers: { label: 'Servono risposte o conferme', tone: 'warn' },
  executing: { label: 'In esecuzione', tone: 'gray' },
  completed: { label: 'Completato', tone: 'ok' },
  partial: { label: 'Completato in parte', tone: 'warn' },
  failed: { label: 'Non eseguito', tone: 'danger' },
  cancelled: { label: 'Annullato prima dell’esecuzione', tone: 'gray' },
};

const NEEDS = {
  needs_confirmation: { label: 'Da confermare', tone: 'warn' },
  needs_answer: { label: 'Servono dati', tone: 'warn' },
  blocked: { label: 'Bloccato', tone: 'danger' },
  waiting_dependency: { label: 'In attesa del passo precedente', tone: 'gray' },
};

const STATES = {
  succeeded: { label: 'Eseguito', tone: 'ok' },
  failed: { label: 'Non eseguito', tone: 'danger' },
  indeterminate: { label: 'Esito incerto: da verificare', tone: 'warn' },
  running: { label: 'In corso', tone: 'gray' },
};

export const UNDO_TEXT = {
  undoable: 'Annullabile entro 15 minuti',
  undone: 'Annullato',
  expired: 'Non più annullabile (oltre 15 minuti)',
  blocked: 'Non annullabile ora: ci sono attività collegate',
  not_undoable: 'Non annullabile con la voce',
};

export function intentLabel(intent) {
  return INTENT_LABELS[intent] || 'Operazione';
}

export function commandStatus(view) {
  // Fase 7: un piano senza operazioni non e' «Pronto». Se STIMA ha fatto una
  // domanda aspetta una risposta; altrimenti non ha riconosciuto nulla.
  const vivo = view && ['received', 'planned', 'awaiting_answers'].includes(view.status);
  if (vivo && !(view.steps || []).length) {
    return (view.clarifications || []).length
      ? { label: 'Serve una tua risposta', tone: 'warn' }
      : { label: 'Nessuna operazione riconosciuta', tone: 'gray' };
  }
  return COMMAND_STATUS[view && view.status] || { label: 'Stato sconosciuto', tone: 'gray' };
}

/**
 * Fase 7: si puo' rispondere (o correggere) finche' il comando non ha eseguito
 * nulla. Il server lo ricontrolla (VOICE_REPLY_NOT_ALLOWED).
 */
export function canReply(view) {
  return Boolean(view) && ['received', 'planned', 'awaiting_answers'].includes(view.status)
    && (view.steps || []).every((s) => s.state === 'pending' || s.state === 'skipped');
}

/** Le domande di STIMA: chiarimenti del modello e domande di dati dei passi. */
export function openQuestions(view) {
  const domande = [...((view && view.clarifications) || [])];
  for (const s of (view && view.steps) || []) {
    if (s.state === 'pending' || s.decision === 'blocked') {
      for (const r of s.reasons || []) if (!r.confirmable && r.message) domande.push(r.message);
    }
  }
  return [...new Set(domande)];
}

/** Un riferimento non trovato fra i record visibili (contatto, immobile...). */
export function referenceNotFound(view) {
  return ((view && view.steps) || []).some((s) => (s.state === 'pending' || s.decision === 'blocked')
    && (s.reasons || []).some((r) => r.code === 'reference_not_found'));
}

/** Etichetta e tono di un passo, dal suo stato e da cio' che gli manca. */
export function stepStatus(step) {
  if (step.state === 'skipped') {
    const bloccato = (step.reasons || []).some((r) => !r.confirmable) || step.decision === 'blocked';
    return bloccato ? { label: 'Non eseguibile', tone: 'danger' } : { label: 'Saltato', tone: 'gray' };
  }
  if (STATES[step.state]) return STATES[step.state];
  if (step.needs && NEEDS[step.needs]) return NEEDS[step.needs];
  if (step.decision === 'blocked') return NEEDS.blocked;
  return { label: 'Pronto', tone: 'gray' };
}

/** I riferimenti dove l'operatore deve SCEGLIERE (piu' di un candidato). */
export function choicesNeeded(step) {
  return Object.entries(step.candidates || {}).filter(([, lista]) => Array.isArray(lista) && lista.length > 1);
}

/** L'unico candidato proposto (da confermare), per ciascun riferimento. */
export function proposedTargets(step) {
  return Object.entries(step.candidates || {})
    .filter(([, lista]) => Array.isArray(lista) && lista.length === 1)
    .map(([ref, lista]) => ({ ref, ...lista[0] }));
}

export function stepsToConfirm(view) {
  return (view.steps || []).filter((s) => s.state === 'pending' && s.needs === 'needs_confirmation'
    && (s.reasons || []).every((r) => r.confirmable));
}

export function stepsNeedingAnswers(view) {
  return (view.steps || []).filter((s) => s.state === 'pending' && (s.needs === 'needs_answer' || s.needs === 'blocked'));
}

/** Si puo' eseguire qualcosa adesso? Passi pronti senza conferme mancanti. */
export function hasRunnableSteps(view) {
  return (view.steps || []).some((s) => (s.state === 'pending' || s.state === 'running') && !s.needs);
}

const ROMA = { timeZone: 'Europe/Rome', weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' };

export function formatWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('it-IT', ROMA);
}

/** Una frase su cosa verra' fatto (o e' stato fatto) da quel passo. */
export function stepDescription(step) {
  const bersagli = proposedTargets(step).map((t) => t.label);
  const chi = bersagli.length ? ` — ${bersagli.join(', ')}` : '';
  if (step.intent === 'create_appointment' && step.start_at) {
    const minuti = step.end_at ? Math.round((new Date(step.end_at) - new Date(step.start_at)) / 60000) : null;
    return `${formatWhen(step.start_at)}${minuti ? ` (${minuti} minuti)` : ''}${chi}`;
  }
  return `${step.quote || ''}${chi}`.trim();
}

/** Il risultato di un passo eseguito, in parole. */
export function resultText(step) {
  const r = step.result || {};
  if (step.state !== 'succeeded') return '';
  if (step.intent === 'link_owner' && r.created === false) return 'Era già collegato: nessuna modifica.';
  if (step.intent === 'activate_seller') {
    return { created: '«Vende» attivato.', reopened: '«Vende» riattivato.', reused: '«Vende» era già attivo.' }[r.origin]
      || '«Vende» attivo.';
  }
  if (r.reconciled) return 'Verificato a mano dopo un’interruzione.';
  return 'Fatto.';
}

/** Messaggi d'errore comprensibili, per codice del server. */
export function errorMessage(error) {
  const code = error && error.code;
  const messaggi = {
    VOICE_GLOBALLY_DISABLED: 'STIMA Voice non è attivo.',
    VOICE_DISABLED: 'STIMA Voice non è attivo per la tua agenzia.',
    VOICE_REPLY_NOT_ALLOWED: 'Questo comando ha già eseguito delle operazioni: usa «Annulla» o inizia un nuovo comando.',
    VOICE_CONVERSATION_EXPIRED: 'Il testo di questo comando non è più disponibile: detta di nuovo il comando.',
    VOICE_PILOT_USER_REQUIRED: 'STIMA Voice è attivo solo per gli operatori della prova pilota.',
    VOICE_PLATFORM_ADMIN_BLOCKED: 'STIMA Voice non è disponibile per l’amministrazione della piattaforma.',
    VOICE_DAILY_LIMIT: 'Hai raggiunto il limite giornaliero dei comandi vocali.',
    VOICE_RATE_LIMITED: 'Troppe richieste vocali in un minuto: attendi qualche secondo e riprova.',
    VOICE_PROVIDER_BUSY: 'Il servizio di interpretazione è momentaneamente saturo.',
    VOICE_PROVIDER_NOT_CONFIGURED: 'L’interpretazione dei comandi non è disponibile.',
    VOICE_PROVIDER_UNAVAILABLE: 'Interpretazione non disponibile in questo momento. Riprova: il comando non verrà duplicato.',
    VOICE_MODEL_OUTPUT_INVALID: 'Non sono riuscito a interpretare il comando. Nulla è stato eseguito: prova a riformularlo.',
    VOICE_IDEMPOTENCY_KEY_REUSED: 'Questo comando risulta già inviato con un testo diverso. Inizia un nuovo comando.',
    VOICE_COMMAND_BUSY: 'Il comando è già in elaborazione. Attendi qualche secondo.',
    VOICE_CHOICE_REQUIRED: 'Scegli una delle opzioni proposte.',
    VOICE_CHOICE_INVALID: 'La scelta non è più valida: aggiorna e riprova.',
    VOICE_CONFIRMATION_NOT_ALLOWED: 'Questo passo non si sblocca con una conferma: servono dati diversi.',
    VOICE_TEXT_INVALID: 'Scrivi un comando (al massimo 2000 caratteri).',
  };
  if (error && error.network) {
    return 'La risposta del server non è arrivata al browser (connessione interrotta). Il comando può essere già '
      + 'stato registrato: tocca di nuovo il pulsante, la richiesta è la stessa e non si crea un doppione.';
  }
  if (code === 'VOICE_PROVIDER_BUSY') {
    return `${messaggi[code]} Riprova fra ${retryAfter(error)} secondi: il comando e la conversazione restano qui, nulla è stato eseguito.`;
  }
  if (code && messaggi[code]) return messaggi[code];
  if (error && error.status === 401) return 'Sessione scaduta. Accedi di nuovo.';
  return (error && error.message) || 'Si è verificato un errore.';
}

/** Gli errori del microfono, in parole. */
export const SPEECH_ERRORS = {
  permission_denied: 'Permesso del microfono negato. Puoi scrivere il comando, oppure consentire il microfono nelle impostazioni del browser.',
  no_microphone: 'Nessun microfono disponibile. Scrivi il comando.',
  no_speech: 'Non ho sentito nulla. Riprova o scrivi il comando.',
  network: 'Riconoscimento vocale non raggiungibile. Scrivi il comando.',
  error: 'Il riconoscimento vocale si è interrotto. Scrivi il comando o riprova.',
};

// Collaudo iPhone del 21/10: su iPhone (Safari e Chrome usano WebKit) l'ascolto
// si chiude a ogni pausa e una nuova sessione puo' far chiedere di nuovo il
// permesso del microfono. E' una regola di iOS: qui la si spiega e si indica
// l'alternativa affidabile, senza promettere di aggirarla.
export const SPEECH_IPHONE = {
  hint: 'Su iPhone l’ascolto si ferma a ogni pausa: tocca «Continua a dettare» per aggiungere il resto.',
  paused: 'Pausa: il testo è salvo. Tocca «Continua a dettare» per aggiungere, oppure controlla il testo.',
  permission: 'Se l’iPhone chiede ogni volta il permesso del microfono, è una regola di iOS che STIMA360 non può cambiare. '
    + 'In Safari puoi consentirlo per questo sito (menu «aA» → «Impostazioni sito web» → Microfono → «Consenti»); '
    + 'in alternativa tocca il campo di testo e usa il microfono della tastiera.',
};

// ---------------------------------------------------------------------------
// V8-4: la scheda riepilogativa dell'appuntamento, il collegamento e le
// parole di conferma. Funzioni pure.
// ---------------------------------------------------------------------------

const ROMA_DATA = { timeZone: 'Europe/Rome', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
const ROMA_ORA = { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit' };
const ROMA_CHIAVE = { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' };

export function formatDay(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('it-IT', ROMA_DATA) : '';
}

export function formatHour(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleTimeString('it-IT', ROMA_ORA) : '';
}

/** La chiave del giorno in ora di Roma (AAAA-MM-GG), per la vista Giorno dell'Agenda. */
export function romeDayKey(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  const parti = new Intl.DateTimeFormat('en-CA', ROMA_CHIAVE).formatToParts(d);
  const v = (t) => (parti.find((p) => p.type === t) || {}).value || '';
  return `${v('year')}-${v('month')}-${v('day')}`;
}

/**
 * Le righe della scheda riepilogativa (etichetta, valore) di un passo
 * «Nuovo appuntamento», dal `summary` del server: tipo, cliente, incarico
 * quando serve, data, ora, agente, note. Un valore mancante e' «da indicare».
 */
export function appointmentSummary(step) {
  const r = step && step.summary;
  if (!r) return [];
  const manca = 'da indicare';
  const righe = [
    ['Tipo', r.type_label || r.type || manca],
    ['Cliente', r.contact || manca],
  ];
  if (r.needs_mandate || r.property) righe.push([r.needs_mandate ? 'Incarico' : 'Immobile', r.property || manca]);
  righe.push(['Data', formatDay(r.start_at) || manca]);
  const ora = formatHour(r.start_at);
  // collaudo 21/10: l'orario detto resta a schermo anche se e' gia' passato
  const passata = r.time_in_past ? ' (già passata)' : '';
  righe.push(['Ora', ora ? `${ora}${formatHour(r.end_at) ? `–${formatHour(r.end_at)}` : ''}${passata}` : manca]);
  righe.push(['Agente', r.agent || manca]);
  if (r.notes) righe.push(['Note', r.notes]);
  return righe;
}

/** Il collegamento all'appuntamento salvato: la vista Giorno dell'Agenda. */
export function appointmentHref(step) {
  const r = step && step.result;
  if (!r || r.entity !== 'appointment' || !r.id) return '';
  const giorno = romeDayKey(r.start_at);
  return giorno ? `#/agenda/giorno/${giorno}` : '#/agenda';
}

/**
 * Le parole dette (o scritte) nella risposta: «Conferma» esegue cio' che e'
 * da confermare, «Annulla» annulla il comando, tutto il resto (anche «Cambia
 * orario alle 16», «Cambia cliente: Luca Bianchi») e' una precisazione che
 * aggiorna il piano. Nessuna scelta di id: il server ricontrolla sempre.
 */
export function spokenCommand(text) {
  const t = String(text || '').trim().toLowerCase().replace(/[.!]+$/, '').trim();
  if (/^(conferma|confermo|confermato|ok conferma|s[iì],? conferma|va bene,? conferma|procedi)$/.test(t)) {
    return { kind: 'confirm', text: '' };
  }
  if (/^(annulla|annullo|cancella|lascia stare|no,? annulla)$/.test(t)) return { kind: 'cancel', text: '' };
  return { kind: 'reply', text: String(text || '').trim() };
}

// ---------------------------------------------------------------------------
// V8-0: il fornitore saturo (429) e le scelte risolte senza il modello
// ---------------------------------------------------------------------------

/** I secondi da attendere dopo un 429 del fornitore (dal server), fra 1 e 300. */
export function retryAfter(error) {
  const n = Number(error && error.data && error.data.retry_after);
  return Number.isFinite(n) && n > 0 ? Math.min(300, Math.ceil(n)) : 20;
}

const piega = (t) => String(t || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
  .replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
const ORDINALI = { primo: 1, prima: 1, uno: 1, secondo: 2, seconda: 2, due: 2, terzo: 3, terza: 3, tre: 3,
  quarto: 4, quarta: 4, quattro: 4, quinto: 5, quinta: 5, cinque: 5 };

/**
 * Una risposta che sceglie fra i candidati GIA' mostrati («il secondo», «Mario
 * Rossi di via Roma») si risolve qui, senza chiamare il modello: solo se
 * indica UN candidato di UNA sola scelta aperta. `scelte` = [{ ordinal, ref,
 * candidates: [{id, label}] }]. Altrimenti null (si risponde al server).
 */
export function choiceFromText(text, scelte) {
  const aperte = (scelte || []).filter((s) => Array.isArray(s.candidates) && s.candidates.length > 1);
  if (!aperte.length) return null;
  const t = piega(text).replace(/^(e |scegli |prendi |seleziona |quello |quella |il |la |l )+/, '').trim();
  if (!t) return null;
  if (aperte.length === 1) {
    const { ordinal, ref, candidates } = aperte[0];
    const numero = /^\d+$/.test(t) ? Number(t) : ORDINALI[t.split(' ')[0]] && t.split(' ').length <= 2 ? ORDINALI[t.split(' ')[0]] : null;
    if (numero && numero >= 1 && numero <= candidates.length) return { ordinal, ref, ...candidates[numero - 1] };
  }
  // collaudo 3: per nome, anche con piu' scelte aperte (contatto e incarico),
  // purche' le parole indichino UN solo candidato fra tutti
  const parole = t.split(' ').filter((p) => p.length > 1);
  const trovati = aperte.flatMap(({ ordinal, ref, candidates }) => candidates
    .filter((c) => parole.length > 0 && parole.every((p) => piega(c.label).includes(p)))
    .map((c) => ({ ordinal, ref, ...c })));
  return trovati.length === 1 ? trovati[0] : null;
}

const SI = /^(si|esatto|giusto|corretto|e lui|e lei|proprio lui|proprio lei|lui|lei|quello|quella|va bene|ok)$/;

/**
 * Collaudo 3: la risposta che riconosce il candidato UNICO gia' proposto
 * («Francesco Ruvolo?» -> «sì», «Ruvolo») si capisce qui, senza il modello.
 * Non conferma nulla: la conferma resta il pulsante «Conferma ed esegui».
 * `proposti` = [{ ordinal, ref, id, label }]. Altrimenti null.
 */
export function proposedFromText(text, proposti) {
  const lista = proposti || [];
  const t = piega(text).replace(/^(si |sì )(?=\S)/, '').replace(/^(e |il |la |l )+/, '').trim();
  if (!t || !lista.length) return null;
  if (SI.test(piega(text)) || SI.test(t)) return lista.length === 1 ? lista[0] : null;
  const parole = t.split(' ').filter((p) => p.length > 1);
  const trovati = lista.filter((c) => parole.length > 0 && parole.every((p) => piega(c.label).includes(p)));
  return trovati.length === 1 ? trovati[0] : null;
}
