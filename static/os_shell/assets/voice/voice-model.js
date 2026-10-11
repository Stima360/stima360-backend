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
  return COMMAND_STATUS[view && view.status] || { label: 'Stato sconosciuto', tone: 'gray' };
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
    VOICE_PLATFORM_ADMIN_BLOCKED: 'STIMA Voice non è disponibile per l’amministrazione della piattaforma.',
    VOICE_DAILY_LIMIT: 'Hai raggiunto il limite giornaliero dei comandi vocali.',
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
