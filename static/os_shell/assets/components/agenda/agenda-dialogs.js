// STIMA360 OS — components/agenda/agenda-dialogs.js (A30-4)
//
// I dialog dell'Agenda: nuovo appuntamento e le azioni sul singolo
// appuntamento (pianifica, conferma, sposta, assegna/riassegna, completa,
// assente, annulla, modifica note e luogo). Uno alla volta, dentro lo stesso
// <dialog class="modal">, come i dialog gia' presenti nella OS Shell.
//
// I CAMPI SONO QUELLI DELLO SCHEMA REALE (appointments/schemas.py), nessuno di
// piu': tipo, inizio, fine, agente, luogo, note. Lo stato di un appuntamento
// nuovo segue la regola D2 del backend: con un agente e' "fissato", senza e'
// una "richiesta". Nessun attore e nessuna agenzia nel corpo: li decide il
// server.
//
// Prima di confermare un orario con un agente si chiede
// `POST /availability/check`; il backend resta comunque l'autorita' (un
// conflitto nato nel frattempo torna come 409 APPOINTMENT_CONFLICT, con le
// alternative).
//
// Il markup statico usa `innerHTML` SOLO con testo fisso; ogni dato
// (agenti, orari, messaggi del server) entra con `textContent` o `value`.

import {
  ACTION_LABELS,
  TYPE_LABELS,
  addDays,
  defaultDuration,
  errorMessage,
  formatDateTime,
  formatTime,
  parseTime,
  romeDateKey,
  romeIso,
  romeParts,
  statusLabel,
  typeLabel,
} from '../../agenda/agenda-model.js';
import {
  checkAvailability,
  createAppointment,
  getAvailability,
  patchAppointment,
  runAction,
} from '../../agenda/agenda-api.js';

function due(n) {
  return String(n).padStart(2, '0');
}

function oraDi(iso) {
  const p = romeParts(iso);
  return `${due(p.hour)}:${due(p.minute)}`;
}

function nuovaChiave() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  // Ripiego per browser senza randomUUID: UUID v4 da getRandomValues.
  const b = globalThis.crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function opzione(valore, testo, selezionata = false) {
  const o = document.createElement('option');
  o.value = valore;
  o.textContent = testo;
  if (selezionata) o.selected = true;
  return o;
}

function riempiAgenti(select, agents, { vuoto, selezionato } = {}) {
  select.replaceChildren();
  if (vuoto) select.appendChild(opzione('', vuoto, !selezionato));
  for (const a of agents || []) {
    select.appendChild(opzione(String(a.id), a.name || `Operatore ${a.id}`,
      Number(selezionato) === Number(a.id)));
  }
}

/** Legge data + ora inizio/fine e restituisce gli ISO con il fuso di Roma. */
function leggiIntervallo(radice) {
  const data = radice.querySelector('[data-field="date"]').value;
  const inizio = parseTime(radice.querySelector('[data-field="start"]').value);
  const fine = parseTime(radice.querySelector('[data-field="end"]').value);
  if (!data || !inizio || !fine) throw new Error('Indica data, ora di inizio e ora di fine.');
  const startAt = romeIso(data, inizio[0], inizio[1]);
  // Una fine "prima" dell'inizio vale per il giorno dopo solo se lo si scrive:
  // qui no, e' un errore.
  const endAt = romeIso(data, fine[0], fine[1]);
  if (Date.parse(endAt) <= Date.parse(startAt)) {
    throw new Error("L'ora di fine deve essere successiva all'ora di inizio.");
  }
  return { startAt, endAt };
}

const BLOCCO_ORARIO = `
  <div class="form-grid-3">
    <div class="form-field"><label>Data *</label><input type="date" class="input" data-field="date" required></div>
    <div class="form-field"><label>Ora inizio *</label><input type="time" class="input" data-field="start" step="300" required></div>
    <div class="form-field"><label>Ora fine *</label><input type="time" class="input" data-field="end" step="300" required></div>
  </div>`;

const BLOCCO_DISPONIBILITA = `
  <div class="agenda-availability" data-availability hidden>
    <div class="agenda-availability-status" data-availability-status aria-live="polite"></div>
    <ul class="agenda-conflicts" data-conflicts></ul>
    <div class="agenda-alternatives" data-alternatives></div>
  </div>`;

function impostaOrario(radice, startIso, endIso) {
  radice.querySelector('[data-field="date"]').value = romeDateKey(startIso);
  radice.querySelector('[data-field="start"]').value = oraDi(startIso);
  radice.querySelector('[data-field="end"]').value = oraDi(endIso);
}

/** Mostra l'esito di un controllo di disponibilita' o di un 409 di conflitto. */
function mostraDisponibilita(radice, { available, conflicts, alternatives }, onAlternativa) {
  const box = radice.querySelector('[data-availability]');
  const stato = radice.querySelector('[data-availability-status]');
  const elenco = radice.querySelector('[data-conflicts]');
  const alternative = radice.querySelector('[data-alternatives]');
  box.hidden = false;
  elenco.replaceChildren();
  alternative.replaceChildren();
  stato.className = `agenda-availability-status ${available ? 'is-free' : 'is-busy'}`;
  stato.textContent = available ? "Orario libero per l'agente." : "L'agente è occupato in questo orario.";
  for (const c of conflicts || []) {
    const voce = document.createElement('li');
    // Per un agent che guarda un collega il server manda solo "Occupato".
    const cosa = c.label || [typeLabel(c.appointment_type), statusLabel(c.status)]
      .filter(Boolean).join(' · ') || 'Occupato';
    voce.textContent = `${formatDateTime(c.start_at)}–${formatTime(c.end_at)} · ${cosa}`;
    elenco.appendChild(voce);
  }
  if ((alternatives || []).length) {
    const titolo = document.createElement('div');
    titolo.className = 'muted';
    titolo.textContent = 'Orari alternativi liberi:';
    alternative.appendChild(titolo);
    for (const a of alternatives) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'btn agenda-chip';
      chip.textContent = `${formatDateTime(a.start_at)}–${formatTime(a.end_at)}`;
      chip.addEventListener('click', () => onAlternativa(a));
      alternative.appendChild(chip);
    }
  }
}

function nascondiDisponibilita(radice) {
  const box = radice.querySelector('[data-availability]');
  if (box) box.hidden = true;
}

/** "Slot liberi": GET /availability per l'agente nel giorno scelto. */
async function mostraSlot(radice, agente, durata, escluso) {
  const box = radice.querySelector('[data-slots]');
  box.hidden = false;
  box.textContent = 'Caricamento disponibilità…';
  const data = radice.querySelector('[data-field="date"]').value;
  if (!agente || !data) {
    box.textContent = 'Scegli un agente e una data.';
    return;
  }
  try {
    const esito = await getAvailability({
      userId: agente, from: romeIso(data), to: romeIso(addDays(data, 1)),
      duration: Math.max(5, Math.min(durata, 480)), step: 30,
      excludeAppointmentId: escluso || undefined,
    });
    box.replaceChildren();
    const liberi = (esito.slots || []).filter((s) => s.available);
    if (!liberi.length) {
      box.textContent = 'Nessuno slot libero in questo giorno.';
      return;
    }
    const titolo = document.createElement('div');
    titolo.className = 'muted';
    titolo.textContent = 'Slot liberi (clic per usarlo):';
    box.appendChild(titolo);
    for (const s of liberi) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'btn agenda-chip';
      chip.textContent = `${formatTime(s.start_at)}–${formatTime(s.end_at)}`;
      chip.addEventListener('click', () => impostaOrario(radice, s.start_at, s.end_at));
      box.appendChild(chip);
    }
  } catch (errore) {
    box.textContent = errorMessage(errore);
  }
}

function preparaDialog(dialogEl, titolo, corpo) {
  dialogEl.innerHTML = `
    <form class="agenda-form" novalidate>
      <h3 class="section-title" data-title></h3>
      ${corpo}
      <div class="field-error" data-error role="alert" aria-live="assertive"></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Chiudi</button>
        <button type="submit" class="btn primary" data-submit>Conferma</button>
      </div>
    </form>`;
  dialogEl.querySelector('[data-title]').textContent = titolo;
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
  return dialogEl.querySelector('form');
}

/**
 * Il ciclo di invio comune: blocca il pulsante, esegue, mostra l'errore del
 * server in chiaro. Un 409 APPOINTMENT_CONFLICT mostra conflitti e
 * alternative; gli altri errori mostrano il messaggio leggibile.
 */
function collegaInvio(dialogEl, form, esegui, { onDone, onConflict, onError } = {}) {
  const errore = form.querySelector('[data-error]');
  const invio = form.querySelector('[data-submit]');
  let occupato = false;
  form.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    if (occupato) return;
    errore.textContent = '';
    occupato = true;
    invio.disabled = true;
    try {
      const esito = await esegui();
      if (esito === false) return;              // fermato prima di scrivere
      dialogEl.close();
      if (onDone) await onDone(esito);
    } catch (e) {
      errore.textContent = e && e.status !== undefined ? errorMessage(e) : (e.message || errorMessage(e));
      if (e && e.code === 'APPOINTMENT_CONFLICT' && onConflict) onConflict(e);
      if (onError) onError(e);
    } finally {
      occupato = false;
      invio.disabled = false;
    }
  });
}

/**
 * Controlla la disponibilita' prima di scrivere. Restituisce true se si puo'
 * procedere; altrimenti mostra conflitti e alternative e restituisce false.
 */
async function disponibilePrima(form, { agente, startAt, endAt, escluso }) {
  if (!agente) return true;
  const corpo = { assigned_user_id: Number(agente), start_at: startAt, end_at: endAt };
  if (escluso) corpo.exclude_appointment_id = Number(escluso);
  const esito = await checkAvailability(corpo);
  if (esito && esito.available) {
    nascondiDisponibilita(form);
    return true;
  }
  mostraDisponibilita(form, esito || {}, (a) => {
    impostaOrario(form, a.start_at, a.end_at);
    nascondiDisponibilita(form);
  });
  form.querySelector('[data-error]').textContent =
    "Orario non disponibile: scegli un'alternativa o un altro orario.";
  return false;
}

function conflittoDalServer(form) {
  return (e) => mostraDisponibilita(form, {
    available: false, conflicts: e.conflicts, alternatives: e.alternatives,
  }, (a) => {
    impostaOrario(form, a.start_at, a.end_at);
    nascondiDisponibilita(form);
  });
}

// ---------------------------------------------------------------------------
// NUOVO APPUNTAMENTO
// ---------------------------------------------------------------------------

export function openCreateDialog(dialogEl, { agents, dateKey, onDone }) {
  const tipi = Object.keys(TYPE_LABELS);
  const form = preparaDialog(dialogEl, 'Nuovo appuntamento', `
    <div class="form-field"><label>Tipo *</label><select class="input" data-field="type"></select></div>
    ${BLOCCO_ORARIO}
    <div class="form-field">
      <label>Agente</label>
      <select class="input" data-field="agent"></select>
      <small class="muted" data-status-hint></small>
    </div>
    <div class="action-bar"><button type="button" class="btn" data-show-slots>Mostra slot liberi</button></div>
    <div class="agenda-slots" data-slots hidden></div>
    ${BLOCCO_DISPONIBILITA}
    <div class="form-field"><label>Luogo</label><input type="text" class="input" data-field="location" maxlength="500"></div>
    <div class="form-field"><label>Note</label><textarea class="input" data-field="notes" maxlength="5000"></textarea></div>`);

  const tipo = form.querySelector('[data-field="type"]');
  for (const t of tipi) tipo.appendChild(opzione(t, TYPE_LABELS[t], t === 'seller_meeting'));
  const agente = form.querySelector('[data-field="agent"]');
  riempiAgenti(agente, agents, { vuoto: 'Nessuno: salva come richiesta' });
  const suggerimento = form.querySelector('[data-status-hint]');
  const aggiornaSuggerimento = () => {
    suggerimento.textContent = agente.value
      ? 'Con un agente l’appuntamento nasce “Fissato”.'
      : 'Senza agente l’appuntamento si salva come “Richiesta”.';
  };
  aggiornaSuggerimento();

  // Orario proposto: il giorno visualizzato, 09:00, durata standard del tipo.
  const campoData = form.querySelector('[data-field="date"]');
  const campoInizio = form.querySelector('[data-field="start"]');
  const campoFine = form.querySelector('[data-field="end"]');
  campoData.value = dateKey;
  campoInizio.value = '09:00';
  let fineToccata = false;
  const proponiFine = () => {
    if (fineToccata) return;
    const inizio = parseTime(campoInizio.value);
    if (!inizio) return;
    const totale = inizio[0] * 60 + inizio[1] + defaultDuration(tipo.value);
    campoFine.value = totale >= 24 * 60 ? '23:55' : `${due(Math.floor(totale / 60))}:${due(totale % 60)}`;
  };
  proponiFine();
  campoFine.addEventListener('input', () => { fineToccata = true; });
  campoInizio.addEventListener('input', proponiFine);
  tipo.addEventListener('change', proponiFine);
  agente.addEventListener('change', () => { aggiornaSuggerimento(); nascondiDisponibilita(form); });

  form.querySelector('[data-show-slots]').addEventListener('click', () => {
    const inizio = parseTime(campoInizio.value);
    const fine = parseTime(campoFine.value);
    const durata = inizio && fine ? (fine[0] * 60 + fine[1]) - (inizio[0] * 60 + inizio[1])
      : defaultDuration(tipo.value);
    mostraSlot(form, agente.value, durata > 0 ? durata : defaultDuration(tipo.value));
  });

  // La chiave di idempotenza vive quanto la compilazione: un nuovo tentativo
  // dopo un timeout riusa la stessa, e il server risponde con la riga gia'
  // creata invece di crearne una seconda.
  let chiave = nuovaChiave();

  collegaInvio(dialogEl, form, async () => {
    const { startAt, endAt } = leggiIntervallo(form);
    const idAgente = agente.value ? Number(agente.value) : null;
    if (!(await disponibilePrima(form, { agente: idAgente, startAt, endAt }))) return false;
    const corpo = {
      appointment_type: tipo.value,
      status: idAgente ? 'scheduled' : 'requested',
      start_at: startAt,
      end_at: endAt,
      assigned_user_id: idAgente,
      client_request_id: chiave,
    };
    const luogo = form.querySelector('[data-field="location"]').value.trim();
    const note = form.querySelector('[data-field="notes"]').value.trim();
    if (luogo) corpo.location_text = luogo;
    if (note) corpo.notes = note;
    return createAppointment(corpo);
  }, {
    onDone,
    onConflict: conflittoDalServer(form),
    onError: (e) => { if (e && e.code === 'IDEMPOTENCY_KEY_REUSED') chiave = nuovaChiave(); },
  });

  dialogEl.showModal();
}

// ---------------------------------------------------------------------------
// AZIONI SU UN APPUNTAMENTO
// ---------------------------------------------------------------------------

/**
 * Apre il dialog di un'azione. `detail` e' la risposta di GET /{id}: la
 * `version` viene da li', e le azioni offerte sono solo quelle che il server
 * ha messo in `allowed_actions`.
 */
export function openActionDialog(dialogEl, { action, detail, agents, onDone }) {
  const riga = detail.appointment;
  const titolo = `${ACTION_LABELS[action] || action} · ${typeLabel(riga.appointment_type) || 'Appuntamento'}`;
  const versione = { version: riga.version };
  const ammesse = detail.allowed_actions || [];

  if (action === 'confirm' || action === 'no_show') {
    const form = preparaDialog(dialogEl, titolo, `<p data-question></p>`);
    form.querySelector('[data-question]').textContent = action === 'confirm'
      ? `Confermare l'appuntamento del ${formatDateTime(riga.start_at)}?`
      : `Registrare che il cliente non si è presentato all'appuntamento del ${formatDateTime(riga.start_at)}?`;
    collegaInvio(dialogEl, form, () => runAction(riga.id, action, versione), { onDone });
    dialogEl.showModal();
    return;
  }

  if (action === 'cancel') {
    const form = preparaDialog(dialogEl, titolo, `
      <p data-question></p>
      <div class="form-field"><label>Motivo</label><textarea class="input" data-field="reason" maxlength="300"></textarea>
      <small class="muted">Obbligatorio per un sopralluogo collegato a una stima: se manca, il server lo segnala.</small></div>`);
    form.querySelector('[data-question]').textContent =
      `Annullare l'appuntamento del ${formatDateTime(riga.start_at)}?`;
    form.querySelector('[data-submit]').textContent = 'Annulla appuntamento';
    collegaInvio(dialogEl, form, () => {
      const motivo = form.querySelector('[data-field="reason"]').value.trim();
      return runAction(riga.id, 'cancel', { ...versione, reason: motivo || null });
    }, { onDone });
    dialogEl.showModal();
    return;
  }

  if (action === 'complete') {
    const form = preparaDialog(dialogEl, titolo, `
      <p class="muted">Lascia vuoto per registrare l'orario attuale del server.</p>
      <div class="form-grid-2">
        <div class="form-field"><label>Svolto il</label><input type="date" class="input" data-field="done-date"></div>
        <div class="form-field"><label>Alle</label><input type="time" class="input" data-field="done-time" step="60"></div>
      </div>`);
    collegaInvio(dialogEl, form, () => {
      const data = form.querySelector('[data-field="done-date"]').value;
      const ora = parseTime(form.querySelector('[data-field="done-time"]').value);
      const corpo = { ...versione };
      if (data || ora) {
        if (!data || !ora) throw new Error('Indica sia la data sia l’ora, oppure lascia entrambe vuote.');
        corpo.completed_at = romeIso(data, ora[0], ora[1]);
      }
      return runAction(riga.id, 'complete', corpo);
    }, { onDone });
    dialogEl.showModal();
    return;
  }

  if (action === 'patch') {
    const form = preparaDialog(dialogEl, titolo, `
      <div class="form-field"><label>Luogo</label><input type="text" class="input" data-field="location" maxlength="500"></div>
      <div class="form-field"><label>Note</label><textarea class="input" data-field="notes" maxlength="5000"></textarea></div>`);
    const luogo = form.querySelector('[data-field="location"]');
    const note = form.querySelector('[data-field="notes"]');
    luogo.value = riga.location_text || '';
    note.value = riga.notes || '';
    collegaInvio(dialogEl, form, () => {
      const corpo = { ...versione };
      const nuovoLuogo = luogo.value.trim() || null;
      const nuoveNote = note.value.trim() || null;
      if (nuovoLuogo !== (riga.location_text || null)) corpo.location_text = nuovoLuogo;
      if (nuoveNote !== (riga.notes || null)) corpo.notes = nuoveNote;
      if (Object.keys(corpo).length === 1) throw new Error('Nessuna modifica da salvare.');
      return patchAppointment(riga.id, corpo);
    }, { onDone });
    dialogEl.showModal();
    return;
  }

  if (action === 'reassign') {
    const form = preparaDialog(dialogEl, titolo, `
      <p class="muted" data-when></p>
      <div class="form-field"><label>Agente *</label><select class="input" data-field="agent" required></select></div>
      ${BLOCCO_DISPONIBILITA}`);
    form.querySelector('[data-when]').textContent =
      `${formatDateTime(riga.start_at)}–${formatTime(riga.end_at)}: data e ora non cambiano.`;
    const agente = form.querySelector('[data-field="agent"]');
    riempiAgenti(agente, agents, { vuoto: 'Scegli un agente', selezionato: riga.assigned_user_id });
    collegaInvio(dialogEl, form, async () => {
      if (!agente.value) throw new Error('Scegli un agente.');
      const nuovo = Number(agente.value);
      if (!(await disponibilePrima(form, {
        agente: nuovo, startAt: riga.start_at, endAt: riga.end_at, escluso: riga.id,
      }))) return false;
      return runAction(riga.id, 'reassign', { ...versione, assigned_user_id: nuovo });
    }, { onDone, onConflict: conflittoDalServer(form) });
    dialogEl.showModal();
    return;
  }

  if (action === 'schedule' || action === 'reschedule') {
    // Sposta: l'agente si cambia qui solo se il server permette anche la
    // riassegnazione a questo operatore (owner/admin). Pianifica: l'agente e'
    // obbligatorio ed esplicito (D2).
    const conAgente = action === 'schedule' || ammesse.includes('reassign');
    const form = preparaDialog(dialogEl, titolo, `
      ${BLOCCO_ORARIO}
      ${conAgente ? `<div class="form-field"><label>Agente${action === 'schedule' ? ' *' : ''}</label>
        <select class="input" data-field="agent"></select></div>
        <div class="action-bar"><button type="button" class="btn" data-show-slots>Mostra slot liberi</button></div>
        <div class="agenda-slots" data-slots hidden></div>` : ''}
      ${BLOCCO_DISPONIBILITA}
      ${action === 'reschedule' ? '<p class="muted">Lo spostamento crea un nuovo appuntamento fissato; quello attuale resta nello storico come “Spostato”.</p>' : ''}`);
    impostaOrario(form, riga.start_at, riga.end_at);
    const agente = form.querySelector('[data-field="agent"]');
    if (agente) {
      riempiAgenti(agente, agents, {
        vuoto: action === 'schedule' ? 'Scegli un agente' : 'Agente attuale',
        selezionato: action === 'schedule' ? riga.assigned_user_id : null,
      });
      form.querySelector('[data-show-slots]').addEventListener('click', () => {
        const scelto = agente.value || riga.assigned_user_id;
        const durata = Math.round((Date.parse(riga.end_at) - Date.parse(riga.start_at)) / 60000);
        mostraSlot(form, scelto, durata > 0 ? durata : 60, riga.id);
      });
    }
    collegaInvio(dialogEl, form, async () => {
      const { startAt, endAt } = leggiIntervallo(form);
      const scelto = agente && agente.value ? Number(agente.value) : null;
      if (action === 'schedule' && !scelto) throw new Error('Per pianificare scegli un agente.');
      const perControllo = scelto || riga.assigned_user_id;
      if (!(await disponibilePrima(form, {
        agente: perControllo, startAt, endAt, escluso: riga.id,
      }))) return false;
      const corpo = { ...versione, start_at: startAt, end_at: endAt };
      if (scelto) corpo.assigned_user_id = scelto;
      return runAction(riga.id, action, corpo);
    }, { onDone, onConflict: conflittoDalServer(form) });
    dialogEl.showModal();
    return;
  }

  throw new Error(`Azione non supportata: ${action}`);
}
