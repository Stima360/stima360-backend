// STIMA360 OS — components/agenda/agenda-dialogs.js (A30-4)
//
// I dialog dell'Agenda: nuovo appuntamento e le azioni sul singolo
// appuntamento (pianifica, conferma, sposta, assegna/riassegna, completa,
// assente, annulla, modifica note e luogo). Uno alla volta, dentro lo stesso
// <dialog class="modal">, come i dialog gia' presenti nella OS Shell.
//
// I CAMPI SONO QUELLI DELLO SCHEMA REALE (appointments/schemas.py), nessuno di
// piu': tipo, inizio, fine, agente, luogo, note e - da A30-5 - i collegamenti
// facoltativi a cliente, lead del cliente, stima e immobile (`contact_id`,
// `lead_id`, `stima_id`, `property_id`), scelti con una ricerca e mai
// digitati come ID. Lo stato di
// un appuntamento nuovo segue la regola D2 del backend: con un agente e' "fissato", senza e'
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
  canAssignRecords,
  canFollowUp,
  defaultDuration,
  durationMinutes,
  errorMessage,
  WEEKDAY_LABELS,
  bookingLinkStatus,
  formatDateTime,
  formatDayLong,
  formatDuration,
  formatTime,
  groupWeeklyHours,
  intervalLabel,
  minutesToTime,
  parseTime,
  publicBookingUrl,
  romeDateKey,
  romeIso,
  romeParts,
  statusLabel,
  timeToMinutes,
  todayKey,
  typeLabel,
  weeklySlotsFromDays,
} from '../../agenda/agenda-model.js';
import {
  checkAvailability,
  createAppointment,
  createAvailabilityException,
  createBookingLink,
  createClosure,
  deleteAvailabilityException,
  deleteClosure,
  disableBookingLink,
  getAvailability,
  getAvailabilityExceptions,
  getBookingLinks,
  getClosures,
  getWorkingHours,
  lookupStime,
  patchAppointment,
  patchBookingLink,
  putWorkingHours,
  rotateBookingLink,
  runAction,
} from '../../agenda/agenda-api.js';
import {
  leadLabel, leadsOfContact, propertyLabel, searchProperties, stimaLabel,
} from '../../agenda/agenda-lookup.js';
import { createContactPicker } from '../contact-picker.js';

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

// A30-5: la durata e' una LETTURA di inizio e fine, non un terzo dato. Il
// selettore riscrive la fine; la fine digitata a mano riscrive il selettore.
const DURATE_MINUTI = Object.freeze([15, 30, 45, 60, 90, 120, 180, 240]);

const BLOCCO_DURATA = `
  <div class="form-field">
    <label>Durata</label>
    <select class="input" data-field="duration"></select>
    <small class="muted" data-duration-text aria-live="polite"></small>
  </div>`;

// A30-5: i collegamenti CRM. Markup fisso; i dati entrano solo via DOM.
// A31-4: il blocco e' composto da due parti, cosi' un chiamante puo' chiedere
// solo cliente + lead (`crmMode: 'contact_lead'`) o nessun collegamento
// (`crmMode: 'none'`). Il default (`'full'`) e' il blocco di sempre.
const CRM_CLIENTE_LEAD = `
    <div class="form-field"><label>Cliente</label><div data-contact-picker></div></div>
    <div class="form-field">
      <label>Lead del cliente</label>
      <select class="input" data-field="lead" disabled></select>
      <small class="muted" data-lead-hint></small>
    </div>`;

const CRM_STIMA_IMMOBILE = `
    <div class="form-field">
      <label>Stima</label>
      <input type="search" class="input" data-stima-search placeholder="Cerca per nominativo, comune o via…" autocomplete="off">
      <small class="muted" data-stima-hint></small>
      <div class="agenda-lookup-results" data-stima-results></div>
      <div class="agenda-lookup-selected" data-stima-selected hidden></div>
    </div>
    <div class="form-field">
      <label>Immobile</label>
      <input type="search" class="input" data-property-search placeholder="Cerca per titolo, codice o indirizzo…" autocomplete="off">
      <div class="agenda-lookup-results" data-property-results></div>
      <div class="agenda-lookup-selected" data-property-selected hidden></div>
    </div>`;

function bloccoCrm(modo) {
  if (modo === 'none') return '';
  const parti = modo === 'contact_lead' ? CRM_CLIENTE_LEAD : CRM_CLIENTE_LEAD + CRM_STIMA_IMMOBILE;
  return `
  <fieldset class="agenda-links">
    <legend>Collegamento CRM (facoltativo)</legend>${parti}
  </fieldset>`;
}

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
  stato.replaceChildren();
  if (available) {
    stato.textContent = "Orario libero per l'agente.";
  } else {
    const titolo = document.createElement('strong');
    titolo.textContent = 'ORARIO NON DISPONIBILE';
    const spiegazione = document.createElement('div');
    spiegazione.textContent = "L'agente è occupato in questo orario.";
    stato.append(titolo, spiegazione);
  }
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

// ---------------------------------------------------------------------------
// A30-7 - PIANIFICA / FISSA SOPRALLUOGO: gli slot della giornata
// ---------------------------------------------------------------------------

//: La fascia mostrata all'apertura: SOLO la posizione iniziale della vista
//: (come la griglia A30-4), NON un orario di lavoro - quelli arrivano con
//: A30-11. "Mostra tutta la giornata" toglie il filtro.
const FASCIA_SLOT = Object.freeze({ da: 8, a: 20 });
//: Il passo di SCELTA degli slot. La durata dell'appuntamento e' un'altra cosa.
const PASSO_SLOT = 30;
const DURATA_SOPRALLUOGO = 60;
const FONTE_SITO = 'legacy_stime_dettagliate';

const MSG_PASSATO = "L'orario scelto è già passato: scegli un orario futuro.";
const MSG_ALTRO_SOPRALLUOGO = 'Esiste già un sopralluogo aperto per questa stima.';

/**
 * Tutti gli slot del giorno per l'agente scelto (GET /availability, passo 30):
 * liberi = selezionabili; occupati = visibili ma disabilitati; passati = non
 * mostrati. Un errore del server NON produce slot: nessuno slot e' "libero"
 * senza la risposta del motore Agenda. Le risposte arrivate dopo una
 * richiesta piu' recente si scartano.
 */
async function mostraSlotGiornata(radice, { agente, durata, escluso, tuttaGiornata }) {
  const box = radice.querySelector('[data-slots]');
  box.hidden = false;
  box._giro = (box._giro || 0) + 1;
  const giro = box._giro;
  const data = radice.querySelector('[data-field="date"]').value;
  if (!agente) {
    box.textContent = 'Scegli un agente per vedere gli slot.';
    return;
  }
  if (!data) {
    box.textContent = 'Scegli un giorno.';
    return;
  }
  box.textContent = 'Caricamento disponibilità…';
  let esito;
  try {
    esito = await getAvailability({
      userId: agente, from: romeIso(data), to: romeIso(addDays(data, 1)),
      duration: durata, step: PASSO_SLOT, excludeAppointmentId: escluso || undefined,
    });
  } catch (errore) {
    if (box._giro === giro) box.textContent = errorMessage(errore);
    return;
  }
  if (box._giro !== giro) return;
  box.replaceChildren();
  const adesso = Date.now();
  const futuri = (esito && Array.isArray(esito.slots) ? esito.slots : [])
    .filter((s) => Date.parse(s.start_at) > adesso);
  const visibili = tuttaGiornata ? futuri : futuri.filter((s) => {
    const ora = romeParts(s.start_at).hour;
    return ora >= FASCIA_SLOT.da && ora < FASCIA_SLOT.a;
  });
  if (!visibili.length) {
    box.textContent = tuttaGiornata
      ? 'Nessuno slot in questo giorno: scegli un altro giorno.'
      : 'Nessuno slot nella fascia 08:00–20:00: prova tutta la giornata o un altro giorno.';
    return;
  }
  const titolo = document.createElement('div');
  titolo.className = 'muted';
  titolo.textContent = 'Slot del giorno (clic su uno libero per usarlo):';
  box.appendChild(titolo);
  for (const s of visibili) {
    const chip = document.createElement('button');
    chip.type = 'button';
    const libero = s.available === true;
    chip.className = `btn agenda-chip agenda-slot ${libero ? 'is-free' : 'is-busy'}`;
    chip.textContent = `${formatTime(s.start_at)}–${formatTime(s.end_at)}${libero ? '' : ' · Occupato'}`;
    chip.dataset.slot = libero ? 'free' : 'busy';
    if (libero) {
      chip.addEventListener('click', () => {
        impostaOrario(radice, s.start_at, s.end_at);
        nascondiDisponibilita(radice);
      });
    } else {
      chip.disabled = true;
      chip.setAttribute('aria-disabled', 'true');
    }
    box.appendChild(chip);
  }
}

/** A30-7 D5: il link all'altro sopralluogo aperto, solo se il server l'ha dato. */
function mostraAltroSopralluogo(form, errore, onOpenAppointment) {
  const box = form.querySelector('[data-open-other]');
  if (!box) return;
  box.replaceChildren();
  box.hidden = true;
  if (!errore || errore.code !== 'STIMA_INSPECTION_ALREADY_OPEN') return;
  const altro = Number(errore.existingAppointmentId);
  if (!Number.isInteger(altro) || altro <= 0 || !onOpenAppointment) return;
  const apri = document.createElement('button');
  apri.type = 'button';
  apri.className = 'btn';
  apri.textContent = 'Apri appuntamento';
  apri.addEventListener('click', () => onOpenAppointment(altro));
  box.appendChild(apri);
  box.hidden = false;
}

function messaggioPianifica(errore) {
  if (!errore) return null;
  if (errore.code === 'STIMA_INSPECTION_ALREADY_OPEN') return MSG_ALTRO_SOPRALLUOGO;
  if (errore.code === 'SCHEDULE_IN_PAST') return MSG_PASSATO;
  return null;
}

/** Il dialog "Pianifica" di una richiesta (A30-7): agente, giorno, slot,
 *  controllo, conferma. Stessa riga: `POST /{id}/schedule` con `version`. */
function apriPianifica(dialogEl, { riga, titolo, versione, agents, onDone, onOpenAppointment }) {
  const dalSito = riga.source === FONTE_SITO && riga.status === 'requested';
  const minuti = durationMinutes(riga.start_at, riga.end_at);
  const durata = Number.isInteger(minuti) && minuti >= 5 && minuti <= 480 ? minuti : DURATA_SOPRALLUOGO;
  const form = preparaDialog(dialogEl, titolo, `
    <p class="agenda-preference" data-preference hidden></p>
    <div class="form-field"><label>Agente *</label><select class="input" data-field="agent"></select></div>
    ${BLOCCO_ORARIO}
    <p class="muted" data-slot-hint></p>
    <div class="action-bar">
      <button type="button" class="btn" data-show-slots>Mostra slot</button>
      <button type="button" class="btn ghost" data-all-day>Mostra tutta la giornata</button>
    </div>
    <div class="agenda-slots" data-slots hidden></div>
    ${BLOCCO_DISPONIBILITA}
    <div class="action-bar agenda-open-other" data-open-other hidden></div>`);
  if (dalSito) {
    const preferenza = form.querySelector('[data-preference]');
    preferenza.textContent = `Preferenza cliente: ${formatDateTime(riga.start_at)} (richiesta dal sito, non ancora fissata)`;
    preferenza.hidden = false;
  }
  form.querySelector('[data-slot-hint]').textContent =
    `Durata: ${formatDuration(durata)} · slot ogni ${PASSO_SLOT} minuti · fascia mostrata 08:00–20:00 (non è un orario di lavoro).`;
  impostaOrario(form, riga.start_at, riga.end_at);
  if (!(Date.parse(riga.start_at) > Date.now())) {
    // Preferenza gia' passata: il giorno proposto e' oggi, l'ora si sceglie.
    form.querySelector('[data-field="date"]').value = todayKey();
  }
  const agente = form.querySelector('[data-field="agent"]');
  riempiAgenti(agente, agents, { vuoto: 'Scegli un agente', selezionato: riga.assigned_user_id });
  let tuttaGiornata = false;
  const tutta = form.querySelector('[data-all-day]');
  const aggiornaSlot = () => mostraSlotGiornata(form, {
    agente: agente.value ? Number(agente.value) : null, durata, escluso: riga.id, tuttaGiornata,
  });
  form.querySelector('[data-show-slots]').addEventListener('click', aggiornaSlot);
  tutta.addEventListener('click', () => {
    tuttaGiornata = !tuttaGiornata;
    tutta.textContent = tuttaGiornata ? 'Mostra solo 08:00–20:00' : 'Mostra tutta la giornata';
    aggiornaSlot();
  });
  for (const campo of [agente, form.querySelector('[data-field="date"]')]) {
    campo.addEventListener('change', aggiornaSlot);
  }
  collegaInvio(dialogEl, form, async () => {
    const { startAt, endAt } = leggiIntervallo(form);
    const scelto = agente.value ? Number(agente.value) : null;
    if (!scelto) throw new Error('Per pianificare scegli un agente.');
    if (!(Date.parse(startAt) > Date.now())) throw new Error(MSG_PASSATO);
    mostraAltroSopralluogo(form, null, null);
    if (!(await disponibilePrima(form, {
      agente: scelto, startAt, endAt, escluso: riga.id,
    }))) return false;
    return runAction(riga.id, 'schedule', {
      ...versione, assigned_user_id: scelto, start_at: startAt, end_at: endAt,
    });
  }, {
    onDone,
    onConflict: conflittoDalServer(form),
    messaggio: messaggioPianifica,
    onError: (e) => mostraAltroSopralluogo(form, e, onOpenAppointment),
  });
  dialogEl.showModal();
  if (agente.value) aggiornaSlot();
}

// A30-8 - la nota di esito (solo complete e no-show) e il follow-up
// facoltativo (complete, no-show, cancel). Il follow-up parte SPENTO: nessun
// task nasce se l'operatore non lo chiede. Contatto, lead, stima e agente del
// task li decide il server dall'appuntamento: qui solo scadenza, titolo, nota.
const BLOCCO_NOTA_ESITO = `
  <div class="form-field"><label>Nota esito</label>
    <textarea class="input" data-field="outcome-note" maxlength="1000"></textarea>
    <small class="muted">Facoltativa. Resta nella cronologia dell'appuntamento.</small></div>`;

const BLOCCO_FOLLOW_UP = `
  <fieldset class="agenda-follow-up" data-follow-up>
    <label class="agenda-follow-up-toggle"><input type="checkbox" data-field="follow-up"> Crea follow-up</label>
    <div class="agenda-follow-up-fields" data-follow-up-fields hidden>
      <div class="form-grid-2">
        <div class="form-field"><label>Data follow-up *</label><input type="date" class="input" data-field="follow-up-date"></div>
        <div class="form-field"><label>Ora *</label><input type="time" class="input" data-field="follow-up-time" step="60"></div>
      </div>
      <div class="form-field"><label>Titolo</label><input type="text" class="input" data-field="follow-up-title" maxlength="200" placeholder="Follow-up appuntamento"></div>
      <div class="form-field"><label>Nota follow-up</label><textarea class="input" data-field="follow-up-note" maxlength="2000"></textarea></div>
      <small class="muted">Crea un'attività nel CRM collegata al cliente dell'appuntamento. Nessun messaggio al cliente.</small>
    </div>
  </fieldset>`;

/** Il blocco follow-up solo se l'appuntamento ha un contatto, un lead o una
 *  stima (D4): senza, il server risponderebbe FOLLOW_UP_REQUIRES_LINK. */
function bloccoFollowUp(riga) {
  return canFollowUp(riga) ? BLOCCO_FOLLOW_UP : '';
}

function montaFollowUp(form) {
  const interruttore = form.querySelector('[data-field="follow-up"]');
  if (!interruttore) return;
  interruttore.checked = false;                    // D2: sempre spento all'apertura
  const campi = form.querySelector('[data-follow-up-fields]');
  interruttore.addEventListener('change', () => { campi.hidden = !interruttore.checked; });
}

/** Il corpo `follow_up`, o null se l'interruttore e' spento. Errori leggibili
 *  PRIMA di scrivere; il server ricontrolla (FOLLOW_UP_IN_PAST). */
function leggiFollowUp(form) {
  const interruttore = form.querySelector('[data-field="follow-up"]');
  if (!interruttore || !interruttore.checked) return null;
  const data = form.querySelector('[data-field="follow-up-date"]').value;
  const ora = parseTime(form.querySelector('[data-field="follow-up-time"]').value);
  if (!data || !ora) throw new Error('Indica data e ora del follow-up.');
  const scadenza = romeIso(data, ora[0], ora[1]);
  if (Date.parse(scadenza) <= Date.now()) {
    throw new Error('La scadenza del follow-up deve essere nel futuro.');
  }
  const corpo = { due_at: scadenza };
  const titolo = form.querySelector('[data-field="follow-up-title"]').value.trim();
  const nota = form.querySelector('[data-field="follow-up-note"]').value.trim();
  if (titolo) corpo.title = titolo;
  if (nota) corpo.note = nota;
  return corpo;
}

function leggiNotaEsito(form) {
  const campo = form.querySelector('[data-field="outcome-note"]');
  const nota = campo ? campo.value.trim() : '';
  return nota || null;
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
function collegaInvio(dialogEl, form, esegui, { onDone, onConflict, onError, messaggio } = {}) {
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
      const proprio = messaggio ? messaggio(e) : null;
      if (proprio) errore.textContent = proprio;
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
async function disponibilePrima(form, { agente, startAt, endAt, escluso, dopoOrario }) {
  if (!agente) return true;
  const corpo = { assigned_user_id: Number(agente), start_at: startAt, end_at: endAt };
  if (escluso) corpo.exclude_appointment_id = Number(escluso);
  const esito = await checkAvailability(corpo);
  if (esito && esito.available) {
    nascondiDisponibilita(form);
    return true;
  }
  mostraDisponibilita(form, esito || {}, usaAlternativa(form, dopoOrario));
  form.querySelector('[data-error]').textContent =
    "Orario non disponibile: scegli un'alternativa o un altro orario.";
  return false;
}

/** La scelta di un'alternativa: nuovo orario nel form, nessun salvataggio.
 *  Il controllo si rifa' alla prossima Conferma (il backend resta l'autorita'). */
function usaAlternativa(form, dopoOrario) {
  return (a) => {
    impostaOrario(form, a.start_at, a.end_at);
    nascondiDisponibilita(form);
    if (dopoOrario) dopoOrario(a);
  };
}

function conflittoDalServer(form, dopoOrario) {
  return (e) => mostraDisponibilita(form, {
    available: false, conflicts: e.conflicts, alternatives: e.alternatives,
  }, usaAlternativa(form, dopoOrario));
}

function rigaTesto(classe, testo) {
  const nodo = document.createElement('div');
  nodo.className = classe;
  nodo.textContent = testo;
  return nodo;
}

/**
 * A30-5: una ricerca con scelta (immobile, stima). `cerca(testo)` restituisce
 * gli elementi; `etichetta(x)` -> { title, detail }. Tutto via DOM e
 * textContent. Le risposte arrivate dopo una ricerca piu' recente si
 * scartano. Ritorna { get scelto(), azzera(), mostra(elementi|null) }.
 */
function montaRicerca(form, nome, { cerca, etichetta, vuoto, onChange }) {
  const input = form.querySelector(`[data-${nome}-search]`);
  const risultati = form.querySelector(`[data-${nome}-results]`);
  const scelta = form.querySelector(`[data-${nome}-selected]`);
  let scelto = null;
  let attesa = null;
  let giro = 0;

  const azzera = () => {
    giro += 1;
    scelto = null;
    scelta.hidden = true;
    scelta.replaceChildren();
    risultati.replaceChildren();
    input.value = '';
    input.hidden = false;
  };
  const scegli = (x) => {
    scelto = x;
    risultati.replaceChildren();
    input.value = '';
    input.hidden = true;
    const { title, detail } = etichetta(x);
    const titolo = document.createElement('strong');
    titolo.textContent = title;
    const cambia = document.createElement('button');
    cambia.type = 'button';
    cambia.className = 'btn ghost';
    cambia.textContent = 'Cambia';
    cambia.addEventListener('click', () => { azzera(); if (onChange) onChange(null); });
    scelta.replaceChildren(titolo, rigaTesto('muted', detail || '—'), cambia);
    scelta.hidden = false;
    if (onChange) onChange(x);
  };
  const mostra = (elementi) => {
    risultati.replaceChildren();
    if (elementi === null) return;
    if (!elementi.length) {
      risultati.appendChild(rigaTesto('muted', vuoto));
      return;
    }
    for (const x of elementi) {
      const { title, detail } = etichetta(x);
      const voce = document.createElement('button');
      voce.type = 'button';
      voce.className = 'btn agenda-lookup-item';
      voce.textContent = detail ? `${title} — ${detail}` : title;
      voce.addEventListener('click', () => scegli(x));
      risultati.appendChild(voce);
    }
  };
  const esegui = async (testo) => {
    const mio = ++giro;
    risultati.replaceChildren(rigaTesto('muted', 'Ricerca…'));
    try {
      const trovati = await cerca(testo);
      if (mio !== giro) return;                    // superata da una ricerca piu' recente
      mostra(trovati);
    } catch (e) {
      if (mio !== giro) return;
      risultati.replaceChildren(rigaTesto('error-box', `Errore nella ricerca: ${e.message || errorMessage(e)}`));
    }
  };
  input.addEventListener('input', () => {
    clearTimeout(attesa);
    const testo = input.value.trim();
    attesa = setTimeout(() => esegui(testo), 300);
  });
  return {
    get scelto() { return scelto; },
    azzera,
    cerca: esegui,
  };
}

// ---------------------------------------------------------------------------
// NUOVO APPUNTAMENTO
// ---------------------------------------------------------------------------

// A31-4: le modalita' del blocco CRM e i messaggi dei vincoli chiesti dal
// chiamante. Nessuna conoscenza di BUY o di immobili: solo opzioni generiche.
const MODI_CRM = Object.freeze(['full', 'contact_lead', 'none']);
const MSG_AGENTE_OBBLIGATORIO = 'Scegli l’agente: è obbligatorio.';

const BLOCCO_LUOGO = `
    <div class="form-field"><label>Luogo</label><input type="text" class="input" data-field="location" maxlength="500"></div>`;
const BLOCCO_NOTE = `
    <div class="form-field"><label>Note</label><textarea class="input" data-field="notes" maxlength="5000"></textarea></div>`;

/**
 * "Nuovo appuntamento". Senza le opzioni A31-4 il dialog e' identico a prima.
 *
 * Opzioni A31-4 (tutte facoltative, generiche):
 *   title                titolo del dialog
 *   appointmentType      tipo iniziale (una chiave di TYPE_LABELS)
 *   lockAppointmentType  il tipo non si cambia
 *   durationMinutes      durata iniziale (5-480)
 *   lockDuration         la durata non si cambia (richiede durationMinutes)
 *   requireAgent         l'agente e' obbligatorio: nessun "Nessuno", nessuna
 *                        richiesta; senza agente nessuna scrittura
 *   crmMode              'full' (default) | 'contact_lead' | 'none'
 *   showLocation         mostra "Luogo" (default true)
 *   showNotes            mostra "Note" (default true)
 *   submitAppointment    riceve il corpo GIA' validato (disponibilita'
 *                        compresa) al posto di `createAppointment`: e' la
 *                        sola scrittura, fatta dal chiamante
 */
export function openCreateDialog(dialogEl, {
  agents, dateKey, startTime, session, onDone,
  title = 'Nuovo appuntamento',
  appointmentType = null,
  lockAppointmentType = false,
  durationMinutes: durataFissa = null,
  lockDuration = false,
  requireAgent = false,
  crmMode = 'full',
  showLocation = true,
  showNotes = true,
  submitAppointment = null,
}) {
  if (!MODI_CRM.includes(crmMode)) throw new Error(`crmMode non valido: ${crmMode}`);
  if (appointmentType !== null && !Object.prototype.hasOwnProperty.call(TYPE_LABELS, appointmentType)) {
    throw new Error(`appointmentType non valido: ${appointmentType}`);
  }
  if (durataFissa !== null && !(Number.isInteger(durataFissa) && durataFissa >= 5 && durataFissa <= 480)) {
    throw new Error('durationMinutes deve essere un intero fra 5 e 480.');
  }
  if (lockDuration && durataFissa === null) throw new Error('lockDuration richiede durationMinutes.');
  if (submitAppointment !== null && typeof submitAppointment !== 'function') {
    throw new Error('submitAppointment deve essere una funzione.');
  }
  const tipi = Object.keys(TYPE_LABELS);
  const form = preparaDialog(dialogEl, title, `
    <div class="form-field"><label>Tipo *</label><select class="input" data-field="type"></select></div>
    ${bloccoCrm(crmMode)}
    <div class="form-field">
      <label>${requireAgent ? 'Agente *' : 'Agente'}</label>
      <select class="input" data-field="agent"></select>
      <small class="muted" data-status-hint></small>
    </div>
    ${BLOCCO_ORARIO}
    ${BLOCCO_DURATA}
    <div class="action-bar">
      <button type="button" class="btn" data-check>Verifica disponibilità</button>
      <button type="button" class="btn" data-show-slots>Mostra slot liberi</button>
    </div>
    <div class="agenda-slots" data-slots hidden></div>
    ${BLOCCO_DISPONIBILITA}${showLocation ? BLOCCO_LUOGO : ''}${showNotes ? BLOCCO_NOTE : ''}`);

  const erroreBox = form.querySelector('[data-error]');
  const tipo = form.querySelector('[data-field="type"]');
  const tipoIniziale = appointmentType || 'seller_meeting';
  for (const t of tipi) tipo.appendChild(opzione(t, TYPE_LABELS[t], t === tipoIniziale));
  if (lockAppointmentType) tipo.disabled = true;
  const agente = form.querySelector('[data-field="agent"]');
  // A30-13B.1 - Requisito 6: un `agent` non vede il vero selettore (non puo'
  // assegnare colleghi: matrice P26-1, `_controlla_agente` sul server), solo
  // "Io" bloccato su se stesso; titolare/amministratore/Supreme "acting"
  // vedono il selettore intero, invariato. `canAssignRecords` e' la STESSA
  // regola usata per "Aggiorna richieste dal sito" (A30-7): non duplicata.
  const puoAssegnare = canAssignRecords(session);
  const io = puoAssegnare ? null : (agents || []).find((a) => a.is_me === true) || null;
  if (puoAssegnare) {
    // A31-4 `requireAgent`: nessuna opzione "Nessuno" e nessun agente scelto
    // al posto dell'operatore: la prima voce chiede la scelta e non vale.
    riempiAgenti(agente, agents, {
      vuoto: requireAgent ? 'Scegli un agente' : 'Nessuno: salva come richiesta',
    });
    if (requireAgent && agente.firstChild) agente.firstChild.disabled = true;
  } else {
    // Nessuna opzione "Nessuno": un agente non puo' lasciare l'appuntamento
    // senza assegnatario per aggirare il blocco, ne' scegliere un collega -
    // il server rifiuterebbe comunque (ForbiddenRole), ma qui non si offre
    // nemmeno la possibilita' di provarci.
    riempiAgenti(agente, io ? [{ id: io.id, name: `Io — ${io.name || 'operatore'}` }] : [],
      { selezionato: io ? io.id : undefined });
    agente.disabled = true;
  }
  const suggerimento = form.querySelector('[data-status-hint]');
  const aggiornaSuggerimento = () => {
    if (requireAgent) {
      suggerimento.textContent = agente.value
        ? 'L’appuntamento nasce “Fissato” nell’agenda dell’agente, dopo la verifica della disponibilità.'
        : MSG_AGENTE_OBBLIGATORIO;
      return;
    }
    suggerimento.textContent = agente.value
      ? 'Con un agente l’appuntamento nasce “Fissato”, dopo la verifica della disponibilità.'
      : 'Senza agente l’appuntamento si salva come “Richiesta”.';
  };
  aggiornaSuggerimento();

  // -- collegamenti CRM (facoltativi) ---------------------------------------
  let cliente = null;
  let selLead = null;
  let immobile = null;
  let stima = null;
  // assegnata sotto, dopo il montaggio della ricerca stime (solo 'full')
  let aggiornaStime = () => {};
  if (crmMode !== 'none') {
    let giroLead = 0;
    selLead = form.querySelector('[data-field="lead"]');
    const hintLead = form.querySelector('[data-lead-hint]');
    const azzeraLead = (testo) => {
      selLead.replaceChildren(opzione('', 'Nessun lead'));
      selLead.value = '';                          // nessun lead del cliente precedente
      selLead.disabled = true;
      hintLead.textContent = testo;
    };
    azzeraLead('Scegli prima un cliente: si possono collegare solo i suoi lead.');
    createContactPicker(form.querySelector('[data-contact-picker]'), {
      onChange: async (scelto) => {
        cliente = scelto;
        const giro = ++giroLead;
        if (!scelto) {
          azzeraLead('Scegli prima un cliente: si possono collegare solo i suoi lead.');
          aggiornaStime();
          return;
        }
        azzeraLead('Caricamento lead…');         // prima: le stime filtrano sul lead
        aggiornaStime();
        try {
          const leads = await leadsOfContact(scelto.id);
          if (giro !== giroLead) return;            // nel frattempo e' cambiato il cliente
          selLead.replaceChildren(opzione('', 'Nessun lead'));
          for (const l of leads) selLead.appendChild(opzione(String(l.id), leadLabel(l)));
          selLead.value = '';
          selLead.disabled = leads.length === 0;
          hintLead.textContent = leads.length ? '' : 'Questo cliente non ha lead.';
        } catch (e) {
          if (giro !== giroLead) return;
          azzeraLead(`Lead non disponibili: ${e.message || errorMessage(e)}`);
        }
      },
    });
  }

  if (crmMode === 'full') {
    immobile = montaRicerca(form, 'property', {
      // Senza testo non si elenca l'archivio immobili.
      cerca: (testo) => (testo ? searchProperties(testo) : Promise.resolve(null)),
      etichetta: propertyLabel,
      vuoto: 'Nessun immobile trovato.',
    });

    // La stima: per testo, e - se c'e' - dentro la relazione CORE del lead
    // scelto (o dei lead del cliente scelto). Il server restituisce solo stime
    // di questa agenzia; una relazione che non esiste non si inventa.
    const hintStima = form.querySelector('[data-stima-hint]');
    const perStima = () => (selLead.value
      ? { leadId: Number(selLead.value) }
      : (cliente && cliente.id ? { contactId: Number(cliente.id) } : {}));
    stima = montaRicerca(form, 'stima', {
      cerca: async (testo) => {
        const filtro = perStima();
        if (!testo && !filtro.leadId && !filtro.contactId) return null;
        const esito = await lookupStime({ search: testo || undefined, ...filtro });
        return (esito && esito.items) || [];
      },
      etichetta: stimaLabel,
      vuoto: 'Nessuna stima trovata.',
    });
    aggiornaStime = () => {
      // Cambiato il cliente o il lead: una stima scelta prima potrebbe non
      // appartenergli piu'. Si azzera e si mostrano quelle collegate.
      stima.azzera();
      const filtro = perStima();
      if (filtro.leadId) hintStima.textContent = 'Solo le stime collegate al lead scelto (la ricerca resta al loro interno).';
      else if (filtro.contactId) hintStima.textContent = 'Solo le stime collegate ai lead del cliente (la ricerca resta al loro interno).';
      else hintStima.textContent = 'Cerca fra le stime dell’agenzia.';
      if (filtro.leadId || filtro.contactId) stima.cerca('');
    };
    hintStima.textContent = 'Cerca fra le stime dell’agenzia.';
    selLead.addEventListener('change', aggiornaStime);
  }

  // -- orario e durata ------------------------------------------------------
  // La verita' e' la coppia inizio/fine che viaggia nel corpo. La durata e'
  // una lettura: il selettore riscrive la fine, la fine riscrive il selettore.
  // A31-4 `lockDuration`: la durata e' quella del chiamante e la fine segue
  // sempre l'inizio.
  const campoData = form.querySelector('[data-field="date"]');
  const campoInizio = form.querySelector('[data-field="start"]');
  const campoFine = form.querySelector('[data-field="end"]');
  const campoDurata = form.querySelector('[data-field="duration"]');
  const testoDurata = form.querySelector('[data-duration-text]');
  campoData.value = dateKey;
  // A30-13B.1 - Requisito 1: click su uno slot vuoto -> ora precompilata;
  // dal pulsante "+ Nuovo appuntamento" (nessun `startTime`) resta 09:00
  // come prima.
  campoInizio.value = startTime || '09:00';
  let durataScelta = false;                        // l'operatore ha deciso la durata
  let durataMostrata = null;                       // l'ultima letta da inizio/fine
  const durataBase = () => durataFissa || defaultDuration(tipo.value);

  const minutiDi = (hhmm) => {
    const t = parseTime(hhmm);
    return t ? t[0] * 60 + t[1] : null;
  };
  const durataAttuale = () => {
    const a = minutiDi(campoInizio.value);
    const b = minutiDi(campoFine.value);
    return a !== null && b !== null && b > a ? b - a : null;
  };
  const mostraDurata = () => {
    const d = durataAttuale();
    durataMostrata = d;
    campoDurata.replaceChildren();
    for (const m of DURATE_MINUTI) campoDurata.appendChild(opzione(String(m), formatDuration(m), m === d));
    if (d !== null && !DURATE_MINUTI.includes(d)) {
      campoDurata.appendChild(opzione(String(d), `${formatDuration(d)} (personalizzata)`, true));
    }
    testoDurata.textContent = d !== null
      ? `Durata: ${formatDuration(d)}`
      : "L'ora di fine deve essere successiva all'ora di inizio.";
  };
  const fineDopo = (minuti) => {
    const a = minutiDi(campoInizio.value);
    if (a === null) return;
    const totale = a + minuti;
    campoFine.value = totale >= 24 * 60 ? '23:55' : `${due(Math.floor(totale / 60))}:${due(totale % 60)}`;
  };
  const dopoOrario = () => {
    durataScelta = true;
    mostraDurata();
  };
  fineDopo(durataBase());
  mostraDurata();
  if (lockDuration) {
    campoDurata.disabled = true;
    campoFine.disabled = true;
  }
  campoInizio.addEventListener('input', () => {
    // Spostare l'inizio sposta la fine: la durata resta quella che si
    // leggeva PRIMA della modifica (quella di adesso e' gia' falsata).
    if (lockDuration) fineDopo(durataFissa);
    else fineDopo(durataScelta && durataMostrata ? durataMostrata : durataBase());
    mostraDurata();
    nascondiDisponibilita(form);
  });
  campoFine.addEventListener('input', () => {
    durataScelta = true;
    mostraDurata();
    nascondiDisponibilita(form);
  });
  campoDurata.addEventListener('change', () => {
    durataScelta = true;
    fineDopo(Number(campoDurata.value));
    mostraDurata();
    nascondiDisponibilita(form);
  });
  campoData.addEventListener('input', () => nascondiDisponibilita(form));
  tipo.addEventListener('change', () => {
    if (!durataScelta && !lockDuration) {
      fineDopo(durataBase());
      mostraDurata();
    }
  });
  agente.addEventListener('change', () => { aggiornaSuggerimento(); nascondiDisponibilita(form); });

  form.querySelector('[data-show-slots]').addEventListener('click', () => {
    const durata = durataAttuale() || durataBase();
    mostraSlot(form, agente.value, durata);
  });

  // "Verifica disponibilità": lo stesso controllo della Conferma, senza
  // scrivere. Serve a sapere prima; la Conferma lo rifa' comunque.
  form.querySelector('[data-check]').addEventListener('click', async () => {
    erroreBox.textContent = '';
    if (!agente.value) {
      erroreBox.textContent = requireAgent
        ? MSG_AGENTE_OBBLIGATORIO
        : 'Senza agente non c’è un’agenda da verificare: '
          + 'l’appuntamento si salverà come “Richiesta”.';
      return;
    }
    try {
      const { startAt, endAt } = leggiIntervallo(form);
      const esito = await checkAvailability({
        assigned_user_id: Number(agente.value), start_at: startAt, end_at: endAt,
      });
      mostraDisponibilita(form, esito || {}, usaAlternativa(form, dopoOrario));
    } catch (e) {
      erroreBox.textContent = e && e.status !== undefined ? errorMessage(e) : (e.message || errorMessage(e));
    }
  });

  // La chiave di idempotenza vive quanto la compilazione: un nuovo tentativo
  // dopo un timeout riusa la stessa, e il server risponde con la riga gia'
  // creata invece di crearne una seconda. A31-4: con `submitAppointment` e'
  // la STESSA chiave che arriva al chiamante (nessuna seconda chiave).
  let chiave = nuovaChiave();

  collegaInvio(dialogEl, form, async () => {
    const { startAt, endAt } = leggiIntervallo(form);
    const idAgente = agente.value ? Number(agente.value) : null;
    // A31-4: vincoli del chiamante, PRIMA di qualunque richiesta.
    if (requireAgent && !idAgente) throw new Error(MSG_AGENTE_OBBLIGATORIO);
    if (lockDuration && durationMinutes(startAt, endAt) !== durataFissa) {
      throw new Error(`La durata è fissa (${formatDuration(durataFissa)}): scegli un orario di inizio che la consenta.`);
    }
    if (!(await disponibilePrima(form, { agente: idAgente, startAt, endAt, dopoOrario }))) return false;
    const corpo = {
      appointment_type: tipo.value,
      status: idAgente ? 'scheduled' : 'requested',
      start_at: startAt,
      end_at: endAt,
      assigned_user_id: idAgente,
      client_request_id: chiave,
    };
    if (cliente && cliente.id) corpo.contact_id = Number(cliente.id);
    if (cliente && selLead && selLead.value) corpo.lead_id = Number(selLead.value);
    if (stima && stima.scelto && stima.scelto.id) corpo.stima_id = Number(stima.scelto.id);
    if (immobile && immobile.scelto && immobile.scelto.id) corpo.property_id = Number(immobile.scelto.id);
    const campoLuogo = form.querySelector('[data-field="location"]');
    const campoNote = form.querySelector('[data-field="notes"]');
    const luogo = campoLuogo ? campoLuogo.value.trim() : '';
    const note = campoNote ? campoNote.value.trim() : '';
    if (luogo) corpo.location_text = luogo;
    if (note) corpo.notes = note;
    // A31-4: una sola scrittura - quella dell'Agenda, oppure quella del
    // chiamante con lo stesso corpo. Mai tutte e due.
    return submitAppointment ? submitAppointment({ ...corpo }) : createAppointment(corpo);
  }, {
    onDone,
    onConflict: conflittoDalServer(form, dopoOrario),
    onError: (e) => { if (e && e.code === 'IDEMPOTENCY_KEY_REUSED') chiave = nuovaChiave(); },
    // Il 404 della creazione e' generico ("Risorsa non trovata"): qui puo'
    // riguardare solo cio' che si e' scelto nel form. Con un chiamante
    // (`submitAppointment`) il 404 e' suo: resta il messaggio del server.
    messaggio: (e) => (!submitAppointment && e && e.status === 404
      ? 'Il cliente, il lead, la stima o l’immobile scelto non è più disponibile in questa agenzia: rifai la selezione.'
      : null),
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
export function openActionDialog(dialogEl, { action, detail, agents, onDone, onOpenAppointment }) {
  const riga = detail.appointment;
  const titolo = `${ACTION_LABELS[action] || action} · ${typeLabel(riga.appointment_type) || 'Appuntamento'}`;
  const versione = { version: riga.version };
  const ammesse = detail.allowed_actions || [];

  if (action === 'confirm') {
    const form = preparaDialog(dialogEl, titolo, `<p data-question></p>`);
    form.querySelector('[data-question]').textContent =
      `Confermare l'appuntamento del ${formatDateTime(riga.start_at)}?`;
    collegaInvio(dialogEl, form, () => runAction(riga.id, action, versione), { onDone });
    dialogEl.showModal();
    return;
  }

  // A30-8: gli esiti. Nessun successo prima del 2xx (collegaInvio); il corpo
  // porta solo `version`, i campi dell'esito e l'eventuale `follow_up`.
  if (action === 'no_show') {
    const form = preparaDialog(dialogEl, 'Cliente non presentato', `
      <p data-question></p>
      ${BLOCCO_NOTA_ESITO}
      ${bloccoFollowUp(riga)}`);
    form.querySelector('[data-question]').textContent =
      `Registrare che il cliente non si è presentato all'appuntamento del ${formatDateTime(riga.start_at)}?`;
    form.querySelector('[data-submit]').textContent = 'Segna non presentato';
    montaFollowUp(form);
    collegaInvio(dialogEl, form, () => {
      const corpo = { ...versione };
      const nota = leggiNotaEsito(form);
      const followUp = leggiFollowUp(form);
      if (nota) corpo.outcome_note = nota;
      if (followUp) corpo.follow_up = followUp;
      return runAction(riga.id, 'no_show', corpo);
    }, { onDone });
    dialogEl.showModal();
    return;
  }

  if (action === 'cancel') {
    const form = preparaDialog(dialogEl, titolo, `
      <p data-question></p>
      <div class="form-field"><label>Motivo</label><textarea class="input" data-field="reason" maxlength="300"></textarea>
      <small class="muted">Obbligatorio per un sopralluogo collegato a una stima: se manca, il server lo segnala.</small></div>
      ${bloccoFollowUp(riga)}`);
    form.querySelector('[data-question]').textContent =
      `Annullare l'appuntamento del ${formatDateTime(riga.start_at)}?`;
    form.querySelector('[data-submit]').textContent = 'Annulla appuntamento';
    montaFollowUp(form);
    collegaInvio(dialogEl, form, () => {
      const motivo = form.querySelector('[data-field="reason"]').value.trim();
      const corpo = { ...versione, reason: motivo || null };
      const followUp = leggiFollowUp(form);
      if (followUp) corpo.follow_up = followUp;
      return runAction(riga.id, 'cancel', corpo);
    }, { onDone });
    dialogEl.showModal();
    return;
  }

  if (action === 'complete') {
    const form = preparaDialog(dialogEl, 'Registra esito: appuntamento svolto', `
      <p class="muted">Lascia vuoto per registrare l'orario attuale del server.</p>
      <div class="form-grid-2">
        <div class="form-field"><label>Svolto il</label><input type="date" class="input" data-field="done-date"></div>
        <div class="form-field"><label>Alle</label><input type="time" class="input" data-field="done-time" step="60"></div>
      </div>
      ${BLOCCO_NOTA_ESITO}
      ${bloccoFollowUp(riga)}`);
    form.querySelector('[data-submit]').textContent = 'Completa appuntamento';
    montaFollowUp(form);
    collegaInvio(dialogEl, form, () => {
      const data = form.querySelector('[data-field="done-date"]').value;
      const ora = parseTime(form.querySelector('[data-field="done-time"]').value);
      const corpo = { ...versione };
      if (data || ora) {
        if (!data || !ora) throw new Error('Indica sia la data sia l’ora, oppure lascia entrambe vuote.');
        corpo.completed_at = romeIso(data, ora[0], ora[1]);
      }
      const nota = leggiNotaEsito(form);
      const followUp = leggiFollowUp(form);
      if (nota) corpo.outcome_note = nota;
      if (followUp) corpo.follow_up = followUp;
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

  if (action === 'schedule') {
    apriPianifica(dialogEl, { riga, titolo, versione, agents, onDone, onOpenAppointment });
    return;
  }

  if (action === 'reschedule') {
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
      // A30-8 D6: indicazione preventiva; il server risponde RESCHEDULE_IN_PAST.
      if (action === 'reschedule' && Date.parse(startAt) < Date.now()) {
        throw new Error('Il nuovo orario è già passato: scegli un orario futuro.');
      }
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

// ---------------------------------------------------------------------------
// DISPONIBILITA' - la UI delle rotte A30-11 gia' esistenti (orari settimanali,
// eccezioni per agente, chiusure agenzia). Nessuna regola nuova:
//
//   * PERMESSI (D6, `appointments/working_hours_service.py`): titolare,
//     amministratore e Supreme "acting" gestiscono qualunque agente ATTIVO
//     della propria agenzia; un `agent` solo se stesso, quindi niente
//     selettore; le chiusure le LEGGONO tutti, le scrivono solo titolare e
//     amministratore. La UI non offre cio' che il server rifiuterebbe, ma e'
//     il server a decidere (403 / 422).
//   * FASCE: minuti [start, end) dalla mezzanotte di Roma, nessuna fascia
//     oltre la mezzanotte; le sovrapposizioni le rifiuta il database (076).
//   * Gli orari NON bloccano il CRM (A30-11 SOFT): un appuntamento si puo'
//     sempre fissare a mano fuori orario. Contano per i suggerimenti e per
//     le prenotazioni online (A30-12 HARD).
//   * Dopo ogni scrittura si rilegge dal server: nessun aggiornamento
//     ottimistico. Cancellare chiede conferma. Nessun id interno a schermo.
// ---------------------------------------------------------------------------

/** Finestra mostrata per eccezioni e chiusure: da oggi a 12 mesi. */
const GIORNI_FINESTRA_DISPONIBILITA = 365;

function nodo(tag, classe, testo) {
  const n = document.createElement(tag);
  if (classe) n.className = classe;
  if (testo !== undefined && testo !== null && testo !== '') n.textContent = String(testo);
  return n;
}

function bottoneTesto(testo, classe = 'btn') {
  const b = nodo('button', classe, testo);
  b.type = 'button';
  return b;
}

/** Un orario `HH:MM` come testo: a differenza di `type="time"` accetta
 *  anche `24:00`, la fine giornata del contratto (end_minute 1440). */
function campoOra({ nome, valore = '', etichetta }) {
  const input = nodo('input', 'input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.maxLength = 5;
  input.placeholder = 'HH:MM';
  input.value = valore;
  input.dataset.field = nome;
  input.setAttribute('aria-label', etichetta);
  return input;
}

function etichettato(testo, controllo) {
  const campo = nodo('label', 'form-field');
  campo.append(nodo('span', 'muted', testo), controllo);
  return campo;
}

/** Conferma in linea (nessun `confirm()` del browser): un riquadro con il
 *  testo e due pulsanti; l'azione parte solo dal secondo click. */
function confermaInLinea(contenitore, { testo, etichetta, azione }) {
  const riquadro = nodo('div', 'error-box');
  riquadro.setAttribute('role', 'alertdialog');
  riquadro.dataset.confirm = '';
  riquadro.appendChild(nodo('p', '', testo));
  const si = bottoneTesto(etichetta, 'btn danger');
  si.dataset.confirmYes = '';
  const no = bottoneTesto('Annulla', 'btn ghost');
  no.dataset.confirmNo = '';
  riquadro.append(si, no);
  contenitore.appendChild(riquadro);
  no.addEventListener('click', () => riquadro.remove());
  si.addEventListener('click', async () => {
    si.disabled = true;
    no.disabled = true;
    try { await azione(); } finally { riquadro.remove(); }
  });
}

/**
 * Apre il pannello Disponibilita'. `agents` e' la risposta di /agents
 * (attivi di questa agenzia, con `is_me`); `session` e' la sessione della
 * Shell, usata SOLO per non mostrare comandi che il server rifiuterebbe.
 */
export function openAvailabilityDialog(dialogEl, { agents, session }) {
  const puoGestire = canAssignRecords(session);
  const lista = agents || [];
  const io = lista.find((a) => a.is_me === true) || null;
  let agenteId = puoGestire ? ((io || lista[0] || {}).id ?? null) : (io ? io.id : null);
  let scheda = 'weekly';
  let giro = 0;                                   // vince l'ultima lettura

  dialogEl.replaceChildren();
  dialogEl.setAttribute('aria-label', 'Disponibilità');
  const radice = nodo('div', 'agenda-form agenda-availability');
  radice.appendChild(nodo('h3', 'section-title', 'Disponibilità'));
  radice.appendChild(nodo('p', 'muted',
    'Gli orari guidano gli slot suggeriti e le prenotazioni online. Nel CRM un appuntamento '
    + 'si può sempre fissare anche fuori orario.'));

  const testata = nodo('div', 'agenda-toolbar');
  let selAgente = null;
  if (puoGestire) {
    selAgente = nodo('select', 'input');
    selAgente.dataset.field = 'availability-agent';
    for (const a of lista) {
      selAgente.appendChild(opzione(String(a.id), a.name || 'Operatore', a.id === agenteId));
    }
    if (agenteId !== null) selAgente.value = String(agenteId);
    testata.appendChild(etichettato('Agente', selAgente));
  } else {
    testata.appendChild(nodo('p', 'muted', io && io.name ? `I tuoi orari · ${io.name}` : 'I tuoi orari'));
  }
  radice.appendChild(testata);

  const schede = nodo('div', 'tabs');
  schede.setAttribute('role', 'tablist');
  const voci = [['weekly', 'Orari settimanali'], ['exceptions', 'Eccezioni'], ['closures', 'Chiusure agenzia']];
  const pulsantiScheda = voci.map(([chiave, testo]) => {
    const b = bottoneTesto(testo, 'tab-btn');
    b.dataset.tab = chiave;
    b.setAttribute('role', 'tab');
    schede.appendChild(b);
    return b;
  });
  radice.appendChild(schede);

  const avviso = nodo('div', '');
  avviso.setAttribute('role', 'status');
  avviso.setAttribute('aria-live', 'polite');
  avviso.dataset.notice = '';
  const errore = nodo('div', 'field-error');
  errore.dataset.error = '';
  errore.setAttribute('role', 'alert');
  const pannello = nodo('div', '');
  pannello.setAttribute('role', 'tabpanel');
  pannello.dataset.tabPanel = '';
  radice.append(avviso, errore, pannello);

  const azioni = nodo('div', 'modal-actions');
  const chiudi = bottoneTesto('Chiudi', 'btn ghost');
  chiudi.addEventListener('click', () => dialogEl.close());
  azioni.appendChild(chiudi);
  radice.appendChild(azioni);
  dialogEl.appendChild(radice);

  const pulisci = () => { avviso.replaceChildren(); errore.textContent = ''; };
  const mostraErrore = (e) => {
    // Un 404 qui e' un'eccezione/chiusura gia' tolta (o di un agente non piu'
    // attivo), non un appuntamento: il `detail` del server lo dice meglio.
    if (e && Number(e.status) === 404) {
      const dettaglio = typeof e.detail === 'string' ? e.detail.trim() : '';
      errore.textContent = dettaglio ? `${dettaglio}.`.replace(/\.\.$/, '.')
        : 'Elemento non trovato o già eliminato.';
      return;
    }
    errore.textContent = e && e.status !== undefined ? errorMessage(e) : ((e && e.message) || errorMessage(e));
  };
  const successo = (testo) => { avviso.replaceChildren(nodo('div', 'success-box', testo)); };
  const inizio = () => {
    const mio = ++giro;
    pannello.replaceChildren(nodo('p', 'muted', 'Caricamento…'));
    return () => mio === giro;
  };
  const finestra = () => {
    const oggi = todayKey();
    return { from: oggi, to: addDays(oggi, GIORNI_FINESTRA_DISPONIBILITA) };
  };
  const senzaAgente = () => {
    pannello.replaceChildren(nodo('p', 'muted', 'Nessun agente attivo da gestire.'));
  };

  // -- ORARI SETTIMANALI ----------------------------------------------------
  function rigaFascia(contenitore, { start = '', end = '' } = {}) {
    const riga = nodo('div', 'agenda-toolbar');
    riga.dataset.interval = '';
    const inizioF = campoOra({ nome: 'start', valore: start, etichetta: 'Ora inizio' });
    const fineF = campoOra({ nome: 'end', valore: end, etichetta: 'Ora fine' });
    const togli = bottoneTesto('Rimuovi fascia', 'btn ghost');
    togli.addEventListener('click', () => riga.remove());
    riga.append(inizioF, nodo('span', '', '–'), fineF, togli);
    contenitore.appendChild(riga);
  }

  async function mostraOrari() {
    const vivo = inizio();
    if (agenteId === null) { senzaAgente(); return; }
    let righe;
    try {
      righe = ((await getWorkingHours(agenteId)) || {}).items || [];
    } catch (e) {
      if (!vivo()) return;
      pannello.replaceChildren();
      mostraErrore(e);
      return;
    }
    if (!vivo()) return;
    const giorni = groupWeeklyHours(righe);
    const corpo = nodo('div', '');
    if (!righe.length) {
      const vuoto = nodo('p', 'muted',
        'Nessun orario impostato. Nel CRM l’agente resta prenotabile a qualunque ora; '
        + 'le prenotazioni online non mostrano slot finché non ci sono orari.');
      vuoto.dataset.empty = '';
      corpo.appendChild(vuoto);
    }
    for (let d = 1; d <= 7; d += 1) {
      const giorno = nodo('div', 'agenda-list-day');
      giorno.dataset.day = String(d);
      const testa = nodo('label', 'agenda-filter');
      const attivo = nodo('input');
      attivo.type = 'checkbox';
      attivo.dataset.field = 'active';
      attivo.checked = giorni[d].length > 0;
      // Un giorno senza fasce e' CHIUSO: lo si scrive, sulla STESSA riga del
      // giorno (sotto, sembrerebbe l'etichetta del giorno seguente).
      const chiuso = nodo('span', 'muted', ' · Chiuso');
      chiuso.dataset.closed = '';
      testa.append(attivo, nodo('span', '', WEEKDAY_LABELS[d]), chiuso);
      const fasce = nodo('div', '');
      fasce.dataset.intervals = '';
      for (const f of giorni[d]) rigaFascia(fasce, { start: minutesToTime(f.start), end: minutesToTime(f.end) });
      const aggiungi = bottoneTesto('Aggiungi fascia', 'btn ghost');
      aggiungi.dataset.addInterval = '';
      aggiungi.addEventListener('click', () => rigaFascia(fasce, { start: '09:00', end: '13:00' }));
      attivo.addEventListener('change', () => {
        fasce.hidden = !attivo.checked;
        aggiungi.hidden = !attivo.checked;
        chiuso.hidden = attivo.checked;
        if (attivo.checked && !fasce.querySelectorAll('[data-interval]').length) {
          rigaFascia(fasce, { start: '09:00', end: '18:00' });
        }
      });
      fasce.hidden = !attivo.checked;
      aggiungi.hidden = !attivo.checked;
      chiuso.hidden = attivo.checked;
      giorno.append(testa, fasce, aggiungi);
      corpo.appendChild(giorno);
    }
    const salva = bottoneTesto('Salva orari', 'btn primary');
    salva.dataset.save = '';
    const conferme = nodo('div', '');
    corpo.append(salva, conferme);
    pannello.replaceChildren(corpo);

    const leggi = () => {
      const stato = {};
      for (const g of corpo.querySelectorAll('[data-day]')) {
        stato[Number(g.dataset.day)] = {
          active: g.querySelector('[data-field="active"]').checked,
          // Array.from: nel browser querySelectorAll e' una NodeList, senza .map
          intervals: Array.from(g.querySelectorAll('[data-interval]')).map((r) => ({
            start: r.querySelector('[data-field="start"]').value,
            end: r.querySelector('[data-field="end"]').value,
          })),
        };
      }
      return weeklySlotsFromDays(stato);
    };
    const scrivi = async (slots) => {
      salva.disabled = true;
      try {
        await putWorkingHours(agenteId, slots);
        await mostraOrari();                       // rilettura, niente ottimismo
        successo('Orari salvati.');
      } catch (e) {
        mostraErrore(e);
      } finally {
        salva.disabled = false;
      }
    };
    salva.addEventListener('click', () => {
      pulisci();
      conferme.replaceChildren();
      const { slots, error } = leggi();
      if (error) { errore.textContent = error; return; }
      if (!slots.length && righe.length) {
        // Togliere TUTTI gli orari e' una cancellazione: si conferma.
        confermaInLinea(conferme, {
          testo: 'Salvare senza orari? Le prenotazioni online non avranno slot per questo agente.',
          etichetta: 'Sì, salva senza orari',
          azione: () => scrivi(slots),
        });
        return;
      }
      scrivi(slots);
    });
  }

  // -- ECCEZIONI E CHIUSURE: parti comuni -------------------------------------
  function campiFascia(contenitore) {
    const intero = nodo('input');
    intero.type = 'checkbox';
    intero.checked = true;
    intero.dataset.field = 'all-day';
    const inizioF = campoOra({ nome: 'start', valore: '09:00', etichetta: 'Ora inizio' });
    const fineF = campoOra({ nome: 'end', valore: '13:00', etichetta: 'Ora fine' });
    const orari = nodo('div', 'agenda-toolbar');
    orari.append(inizioF, nodo('span', '', '–'), fineF);
    orari.hidden = true;
    intero.addEventListener('change', () => { orari.hidden = intero.checked; });
    const riga = nodo('label', 'agenda-filter');
    riga.append(intero, nodo('span', '', 'Tutto il giorno'));
    contenitore.append(riga, orari);
    return () => {
      if (intero.checked) return { start_minute: 0, end_minute: 1440 };
      const start = timeToMinutes(inizioF.value);
      const end = timeToMinutes(fineF.value);
      if (start === null || end === null) throw new Error('Indica ora di inizio e di fine.');
      if (end <= start) {
        throw new Error("L'ora di fine deve essere dopo l'ora di inizio (nessuna fascia attraversa la mezzanotte).");
      }
      return { start_minute: start, end_minute: end };
    };
  }

  function campiDataMotivo(contenitore, altri = []) {
    const data = nodo('input', 'input');
    data.type = 'date';
    data.value = todayKey();
    data.dataset.field = 'date';
    const motivo = nodo('input', 'input');
    motivo.type = 'text';
    motivo.maxLength = 40;
    motivo.dataset.field = 'reason';
    const griglia = nodo('div', 'agenda-toolbar');
    griglia.append(etichettato('Data', data), ...altri, etichettato('Motivo (facoltativo)', motivo));
    contenitore.appendChild(griglia);
    return { data, motivo };
  }

  function rigaElenco(testi, { onDelete, etichettaConferma, testoConferma } = {}) {
    const riga = nodo('div', 'agenda-card');
    riga.dataset.row = '';
    for (const t of testi) if (t) riga.appendChild(nodo('span', 'agenda-card-meta', t));
    if (onDelete) {
      const elimina = bottoneTesto('Elimina', 'btn ghost');
      elimina.dataset.delete = '';
      elimina.addEventListener('click', () => {
        pulisci();
        if (riga.querySelector('[data-confirm]')) return;
        confermaInLinea(riga, { testo: testoConferma, etichetta: etichettaConferma, azione: onDelete });
      });
      riga.appendChild(elimina);
    }
    return riga;
  }

  /** Crea, poi rilegge l'elenco; nessun aggiornamento ottimistico. */
  function collegaAggiunta(pulsante, { corpo, invia, rileggi, messaggio }) {
    pulsante.addEventListener('click', async () => {
      pulisci();
      let dati;
      try { dati = corpo(); } catch (e) { errore.textContent = e.message; return; }
      pulsante.disabled = true;
      try {
        await invia(dati);
        await rileggi();
        successo(messaggio);
      } catch (e) {
        mostraErrore(e);
      } finally {
        pulsante.disabled = false;
      }
    });
  }

  async function leggiElenco(vivo, carica) {
    try {
      return ((await carica()) || {}).items || [];
    } catch (e) {
      if (vivo()) { pannello.replaceChildren(); mostraErrore(e); }
      return null;
    }
  }

  // -- ECCEZIONI ------------------------------------------------------------
  async function mostraEccezioni() {
    const vivo = inizio();
    if (agenteId === null) { senzaAgente(); return; }
    const righe = await leggiElenco(vivo, () => getAvailabilityExceptions(agenteId, finestra()));
    if (righe === null || !vivo()) return;
    const nomeAgente = (lista.find((a) => a.id === agenteId) || {}).name || '';
    const corpo = nodo('div', '');
    const elenco = nodo('div', 'agenda-list-day');
    elenco.appendChild(nodo('h4', 'agenda-list-day-title', 'Prossimi 12 mesi'));
    if (!righe.length) {
      const vuoto = nodo('p', 'muted', 'Nessuna eccezione nei prossimi 12 mesi.');
      vuoto.dataset.empty = '';
      elenco.appendChild(vuoto);
    }
    for (const r of righe) {
      elenco.appendChild(rigaElenco([
        formatDayLong(r.exception_date), intervalLabel(r.start_minute, r.end_minute),
        r.is_available ? 'Disponibile' : 'Non disponibile', r.reason_code || '', nomeAgente,
      ], {
        testoConferma: `Eliminare l’eccezione del ${formatDayLong(r.exception_date)}?`,
        etichettaConferma: 'Sì, elimina',
        onDelete: async () => {
          try {
            await deleteAvailabilityException(agenteId, r.id);
            await mostraEccezioni();
            successo('Eccezione eliminata.');
          } catch (e) {
            if (Number(e && e.status) === 404) await mostraEccezioni();   // gia' tolta: si rilegge
            mostraErrore(e);
          }
        },
      }));
    }
    const nuovo = nodo('div', 'agenda-list-day');
    nuovo.appendChild(nodo('h4', 'agenda-list-day-title', 'Nuova eccezione'));
    const tipo = nodo('select', 'input');
    tipo.dataset.field = 'is-available';
    tipo.append(opzione('false', 'Non disponibile', true), opzione('true', 'Disponibile'));
    const { data, motivo } = campiDataMotivo(nuovo, [etichettato('Tipo', tipo)]);
    const fascia = campiFascia(nuovo);
    const aggiungi = bottoneTesto('Aggiungi eccezione', 'btn primary');
    aggiungi.dataset.save = '';
    nuovo.appendChild(aggiungi);
    collegaAggiunta(aggiungi, {
      corpo: () => {
        if (!data.value) throw new Error('Indica la data.');
        const dati = { exception_date: data.value, is_available: tipo.value === 'true', ...fascia() };
        if (motivo.value.trim()) dati.reason_code = motivo.value.trim();
        return dati;
      },
      invia: (dati) => createAvailabilityException(agenteId, dati),
      rileggi: mostraEccezioni,
      messaggio: 'Eccezione aggiunta.',
    });
    corpo.append(elenco, nuovo);
    pannello.replaceChildren(corpo);
  }

  // -- CHIUSURE AGENZIA -------------------------------------------------------
  async function mostraChiusure() {
    const vivo = inizio();
    const righe = await leggiElenco(vivo, () => getClosures(finestra()));
    if (righe === null || !vivo()) return;
    const corpo = nodo('div', '');
    const elenco = nodo('div', 'agenda-list-day');
    elenco.appendChild(nodo('h4', 'agenda-list-day-title', 'Prossimi 12 mesi'));
    if (!righe.length) {
      const vuoto = nodo('p', 'muted', 'Nessuna chiusura nei prossimi 12 mesi.');
      vuoto.dataset.empty = '';
      elenco.appendChild(vuoto);
    }
    for (const r of righe) {
      elenco.appendChild(rigaElenco([
        formatDayLong(r.closure_date), intervalLabel(r.start_minute, r.end_minute), r.reason_code || '',
      ], puoGestire ? {
        testoConferma: `Eliminare la chiusura del ${formatDayLong(r.closure_date)}?`,
        etichettaConferma: 'Sì, elimina',
        onDelete: async () => {
          try {
            await deleteClosure(r.id);
            await mostraChiusure();
            successo('Chiusura eliminata.');
          } catch (e) {
            if (Number(e && e.status) === 404) await mostraChiusure();    // gia' tolta: si rilegge
            mostraErrore(e);
          }
        },
      } : {}));
    }
    corpo.appendChild(elenco);
    if (!puoGestire) {
      corpo.appendChild(nodo('p', 'muted', 'Solo il titolare e gli amministratori gestiscono le chiusure dell’agenzia.'));
      pannello.replaceChildren(corpo);
      return;
    }
    const nuovo = nodo('div', 'agenda-list-day');
    nuovo.appendChild(nodo('h4', 'agenda-list-day-title', 'Nuova chiusura'));
    const { data, motivo } = campiDataMotivo(nuovo);
    const fascia = campiFascia(nuovo);
    const aggiungi = bottoneTesto('Aggiungi chiusura', 'btn primary');
    aggiungi.dataset.save = '';
    nuovo.appendChild(aggiungi);
    collegaAggiunta(aggiungi, {
      corpo: () => {
        if (!data.value) throw new Error('Indica la data.');
        const dati = { closure_date: data.value, ...fascia() };
        if (motivo.value.trim()) dati.reason_code = motivo.value.trim();
        return dati;
      },
      invia: createClosure,
      rileggi: mostraChiusure,
      messaggio: 'Chiusura aggiunta.',
    });
    corpo.appendChild(nuovo);
    pannello.replaceChildren(corpo);
  }

  const mostra = { weekly: mostraOrari, exceptions: mostraEccezioni, closures: mostraChiusure };
  const apriScheda = (chiave) => {
    scheda = chiave;
    for (const b of pulsantiScheda) {
      const attiva = b.dataset.tab === chiave;
      b.classList.toggle('active', attiva);
      b.setAttribute('aria-selected', attiva ? 'true' : 'false');
      b.tabIndex = attiva ? 0 : -1;
    }
    pulisci();
    mostra[chiave]();
  };
  for (const b of pulsantiScheda) b.addEventListener('click', () => apriScheda(b.dataset.tab));
  // Frecce sinistra/destra fra le schede (pattern tablist).
  schede.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
    const i = voci.findIndex(([k]) => k === scheda);
    const j = (i + (ev.key === 'ArrowRight' ? 1 : voci.length - 1)) % voci.length;
    apriScheda(voci[j][0]);
    if (typeof pulsantiScheda[j].focus === 'function') pulsantiScheda[j].focus();
  });
  if (selAgente) {
    selAgente.addEventListener('change', () => {
      agenteId = Number(selAgente.value);
      apriScheda(scheda);
    });
  }

  apriScheda('weekly');
  dialogEl.showModal();
}

// ---------------------------------------------------------------------------
// LINK DI PRENOTAZIONE - la UI delle rotte operatore A30-12 gia' esistenti
// (`/booking-links`). Nessuna regola nuova:
//
//   * PERMESSI (D2, `public_booking/service.py`): titolare, amministratore e
//     Supreme "acting" vedono e gestiscono i link di qualunque agente attivo
//     dell'agenzia; un `agent` solo i propri, quindi niente selettore. Un
//     link fuori portata e' "non trovato" (404), come uno inesistente.
//   * IL TOKEN GREZZO esiste SOLO nella risposta di create e rotate (il
//     server conserva l'hash). Qui vive in una variabile di questa funzione e
//     nel valore del campo del riquadro "mostrato una volta"; mai in
//     localStorage/sessionStorage, mai in un attributo o `data-*`, mai nei
//     log. Chiudere il riquadro o il pannello lo dimentica. Per un link gia'
//     esistente l'indirizzo non si puo' piu' mostrare: si ruota.
//   * Il link non si riattiva e l'agente non si cambia: l'API non lo prevede.
//     L'etichetta si puo' cambiare ma non svuotare (il PATCH ignora `null`).
//   * Dopo ogni scrittura si rilegge l'elenco; ruotare e disattivare chiedono
//     conferma. Nessun id interno a schermo.
// ---------------------------------------------------------------------------

function campoNumero({ nome, valore, min, max, etichetta }) {
  const input = nodo('input', 'input');
  input.type = 'number';
  input.min = String(min);
  input.max = String(max);
  input.step = '1';
  input.value = String(valore);
  input.dataset.field = nome;
  input.setAttribute('aria-label', etichetta);
  return input;
}

function interoNelRango(testo, min, max) {
  const n = Number(String(testo).trim());
  return Number.isInteger(n) && n >= min && n <= max ? n : null;
}

/**
 * Apre il pannello dei link di prenotazione. `agents` e' la risposta di
 * /agents (attivi di questa agenzia, con `is_me`); `session` serve SOLO a non
 * offrire comandi che il server rifiuterebbe.
 */
export function openBookingLinksDialog(dialogEl, { agents, session }) {
  const puoGestire = canAssignRecords(session);
  const lista = agents || [];
  const io = lista.find((a) => a.is_me === true) || null;
  const nomeAgente = (id) => {
    const a = lista.find((x) => Number(x.id) === Number(id));
    return a ? (a.name || 'Operatore') : 'Agente non più attivo';
  };
  let giro = 0;                                    // vince l'ultima lettura
  let indirizzoUnaVolta = null;                    // SOLO dopo create/rotate

  dialogEl.replaceChildren();
  dialogEl.setAttribute('aria-label', 'Link prenotazione');
  const radice = nodo('div', 'agenda-form agenda-booking-links');
  radice.appendChild(nodo('h3', 'section-title', 'Link prenotazione'));
  radice.appendChild(nodo('p', 'muted',
    'Con un link il cliente prenota da solo, solo negli orari di lavoro dell’agente '
    + '(Disponibilità) e mai sopra un altro impegno.'));

  const avviso = nodo('div', '');
  avviso.setAttribute('role', 'status');
  avviso.setAttribute('aria-live', 'polite');
  avviso.dataset.notice = '';
  const errore = nodo('div', 'field-error');
  errore.dataset.error = '';
  errore.setAttribute('role', 'alert');
  const unaVolta = nodo('div', '');
  unaVolta.dataset.once = '';
  const barra = nodo('div', 'agenda-toolbar');
  const crea = bottoneTesto('Crea link', 'btn primary');
  crea.dataset.create = '';
  barra.appendChild(crea);
  const modulo = nodo('div', '');
  modulo.dataset.form = '';
  const elenco = nodo('div', '');
  elenco.dataset.list = '';
  radice.append(avviso, errore, unaVolta, barra, modulo, elenco);

  const azioni = nodo('div', 'modal-actions');
  const chiudi = bottoneTesto('Chiudi', 'btn ghost');
  azioni.appendChild(chiudi);
  radice.appendChild(azioni);
  dialogEl.appendChild(radice);

  const pulisci = () => { avviso.replaceChildren(); errore.textContent = ''; };
  const mostraErrore = (e) => {
    errore.textContent = e && e.status !== undefined ? errorMessage(e) : ((e && e.message) || errorMessage(e));
  };
  const successo = (testo) => { avviso.replaceChildren(nodo('div', 'success-box', testo)); };

  // Il token si dimentica: variabile azzerata, riquadro e campo rimossi.
  const dimentica = () => {
    indirizzoUnaVolta = null;
    unaVolta.replaceChildren();
  };
  chiudi.addEventListener('click', () => { dimentica(); dialogEl.close(); });
  dialogEl.addEventListener('close', dimentica, { once: true });   // anche con Esc

  // P30: l'URL e' la pagina pubblica `/prenota/<token>`, pronta per il cliente.
  const LINK_CLIENTE = 'Link da condividere con il cliente';

  function mostraUnaVolta(token, { ruotato }) {
    indirizzoUnaVolta = publicBookingUrl(window.location.origin, token);
    const riquadro = nodo('div', 'success-box');
    riquadro.setAttribute('role', 'status');
    riquadro.appendChild(nodo('h4', '', ruotato ? 'Nuovo link generato' : 'Link creato'));
    const avvertenza = nodo('p', '', 'Questo link viene mostrato solo ora. Se lo perdi, dovrai rigenerarlo.');
    avvertenza.dataset.onceWarning = '';
    const campo = nodo('input', 'input');
    campo.type = 'text';
    campo.readOnly = true;
    campo.setAttribute('aria-label', LINK_CLIENTE);
    campo.value = indirizzoUnaVolta;               // proprieta', non attributo
    const copia = bottoneTesto('Copia', 'btn primary');
    copia.dataset.copy = '';
    const fatto = bottoneTesto('Ho copiato il link', 'btn ghost');
    fatto.dataset.done = '';
    const nota = nodo('p', 'muted', LINK_CLIENTE);
    nota.dataset.clientLinkLabel = '';
    riquadro.append(avvertenza, nota, campo, copia, fatto);
    unaVolta.replaceChildren(riquadro);
    copia.addEventListener('click', async () => {
      pulisci();
      try {
        const appunti = globalThis.navigator && globalThis.navigator.clipboard;
        if (!appunti || typeof appunti.writeText !== 'function' || !indirizzoUnaVolta) {
          throw new Error('non disponibile');
        }
        await appunti.writeText(indirizzoUnaVolta);
        successo('Link copiato.');
      } catch (_e) {
        if (typeof campo.select === 'function') campo.select();
        errore.textContent = 'Copia automatica non disponibile: seleziona il link e copialo a mano.';
      }
    });
    fatto.addEventListener('click', () => { dimentica(); pulisci(); });
  }

  // -- elenco ---------------------------------------------------------------
  async function carica() {
    const mio = ++giro;
    elenco.replaceChildren(nodo('p', 'muted', 'Caricamento…'));
    let righe;
    try {
      righe = ((await getBookingLinks()) || {}).items || [];
    } catch (e) {
      if (mio !== giro) return;
      elenco.replaceChildren();
      mostraErrore(e);
      return;
    }
    if (mio !== giro) return;
    elenco.replaceChildren();
    if (!righe.length) {
      const vuoto = nodo('p', 'muted', 'Nessun link di prenotazione.');
      vuoto.dataset.empty = '';
      elenco.appendChild(vuoto);
      return;
    }
    for (const link of righe) elenco.appendChild(riga(link));
  }

  function riga(link) {
    const stato = bookingLinkStatus(link);
    const scheda = nodo('div', 'agenda-card');
    scheda.dataset.row = '';
    const testi = [
      link.label || '', nomeAgente(link.assigned_user_id), typeLabel(link.appointment_type),
      `Durata ${link.duration_minutes} min`,
      `Buffer prima ${link.buffer_before_minutes} min · dopo ${link.buffer_after_minutes} min`,
      stato, link.expires_at && stato !== 'Disattivato' ? `Scade ${formatDateTime(link.expires_at)}` : '',
    ];
    for (const t of testi) if (t) scheda.appendChild(nodo('span', 'agenda-card-meta', t));
    const comandi = nodo('div', 'agenda-toolbar');
    const conferme = nodo('div', '');
    if (stato === 'Attivo') {
      const modifica = bottoneTesto('Modifica', 'btn ghost');
      modifica.dataset.edit = '';
      modifica.addEventListener('click', () => { pulisci(); apriModulo(link); });
      const ruota = bottoneTesto('Ruota link', 'btn ghost');
      ruota.dataset.rotate = '';
      ruota.addEventListener('click', () => {
        pulisci();
        conferme.replaceChildren();
        confermaInLinea(conferme, {
          testo: 'Il link attuale smetterà subito di funzionare: chi l’ha già ricevuto non potrà più prenotare. Generare un nuovo link?',
          etichetta: 'Sì, genera nuovo link',
          azione: async () => {
            try {
              const esito = await rotateBookingLink(link.id);
              mostraUnaVolta(esito && esito.token, { ruotato: true });
              await carica();
            } catch (e) { mostraErrore(e); }
          },
        });
      });
      comandi.append(modifica, ruota);
    }
    if (stato !== 'Disattivato') {
      const disattiva = bottoneTesto('Disattiva', 'btn ghost');
      disattiva.dataset.disable = '';
      disattiva.addEventListener('click', () => {
        pulisci();
        conferme.replaceChildren();
        confermaInLinea(conferme, {
          testo: 'Il link smetterà di funzionare e non potrà essere riattivato. Disattivarlo?',
          etichetta: 'Sì, disattiva',
          azione: async () => {
            try {
              await disableBookingLink(link.id);
              await carica();
              successo('Link disattivato.');
            } catch (e) { mostraErrore(e); }
          },
        });
      });
      comandi.appendChild(disattiva);
    }
    if (comandi.childNodes.length) scheda.appendChild(comandi);
    scheda.appendChild(conferme);
    return scheda;
  }

  // -- crea / modifica -----------------------------------------------------
  function apriModulo(link) {
    const nuovo = !link;
    const corpo = nodo('div', 'agenda-list-day');
    corpo.appendChild(nodo('h4', 'agenda-list-day-title', nuovo ? 'Nuovo link' : 'Modifica link'));
    const griglia = nodo('div', 'agenda-toolbar');
    let selAgente = null;
    let agenteFisso = null;
    if (nuovo && puoGestire) {
      selAgente = nodo('select', 'input');
      selAgente.dataset.field = 'agent';
      const scelto = (io || lista[0] || {}).id;
      for (const a of lista) selAgente.appendChild(opzione(String(a.id), a.name || 'Operatore', a.id === scelto));
      if (scelto !== undefined) selAgente.value = String(scelto);
      griglia.appendChild(etichettato('Agente', selAgente));
    } else {
      agenteFisso = nuovo ? (io ? io.id : null) : link.assigned_user_id;
      const testo = nuovo ? (io ? `Io — ${io.name || ''}`.trim() : '') : nomeAgente(link.assigned_user_id);
      griglia.appendChild(etichettato('Agente', nodo('span', '', testo || '—')));
    }
    const tipo = nodo('select', 'input');
    tipo.dataset.field = 'type';
    if (nuovo) tipo.appendChild(opzione('', 'Scegli un tipo', true));
    for (const [t, testo] of Object.entries(TYPE_LABELS)) {
      tipo.appendChild(opzione(t, testo, !nuovo && t === link.appointment_type));
    }
    tipo.value = nuovo ? '' : link.appointment_type;
    const durata = campoNumero({ nome: 'duration', valore: nuovo ? '' : link.duration_minutes,
      min: 1, max: 1440, etichetta: 'Durata in minuti' });
    const prima = campoNumero({ nome: 'buffer-before', valore: nuovo ? 0 : link.buffer_before_minutes,
      min: 0, max: 1440, etichetta: 'Buffer prima in minuti' });
    const dopo = campoNumero({ nome: 'buffer-after', valore: nuovo ? 0 : link.buffer_after_minutes,
      min: 0, max: 1440, etichetta: 'Buffer dopo in minuti' });
    const etichetta = nodo('input', 'input');
    etichetta.type = 'text';
    etichetta.maxLength = 200;
    etichetta.value = nuovo ? '' : (link.label || '');
    etichetta.dataset.field = 'label';
    griglia.append(etichettato('Tipo', tipo), etichettato('Durata (min)', durata),
      etichettato('Buffer prima (min)', prima), etichettato('Buffer dopo (min)', dopo),
      etichettato('Etichetta (facoltativa)', etichetta));
    corpo.appendChild(griglia);
    // La durata proposta e' quella di default del tipo (enums del server),
    // finche' l'operatore non la sceglie lui.
    let durataToccata = !nuovo;
    durata.addEventListener('input', () => { durataToccata = true; });
    tipo.addEventListener('change', () => {
      if (!durataToccata && tipo.value) durata.value = String(defaultDuration(tipo.value));
    });

    const salva = bottoneTesto(nuovo ? 'Crea link' : 'Salva modifiche', 'btn primary');
    salva.dataset.save = '';
    const annulla = bottoneTesto('Annulla', 'btn ghost');
    annulla.addEventListener('click', () => { modulo.replaceChildren(); pulisci(); });
    corpo.append(salva, annulla);
    modulo.replaceChildren(corpo);

    const leggi = () => {
      if (!tipo.value) throw new Error('Scegli il tipo di appuntamento.');
      const d = interoNelRango(durata.value, 1, 1440);
      if (d === null) throw new Error('La durata deve essere un numero intero di minuti fra 1 e 1440.');
      const bp = interoNelRango(prima.value, 0, 1440);
      const bd = interoNelRango(dopo.value, 0, 1440);
      if (bp === null || bd === null) throw new Error('I buffer devono essere numeri interi di minuti fra 0 e 1440.');
      const testo = etichetta.value.trim();
      if (!nuovo && link.label && !testo) {
        throw new Error('L’etichetta non si può svuotare: scrivine una nuova o lasciala com’è.');
      }
      return { appointment_type: tipo.value, duration_minutes: d, buffer_before_minutes: bp,
        buffer_after_minutes: bd, label: testo };
    };

    salva.addEventListener('click', async () => {
      pulisci();
      let v;
      try { v = leggi(); } catch (e) { errore.textContent = e.message; return; }
      salva.disabled = true;
      try {
        if (nuovo) {
          const agente = selAgente ? Number(selAgente.value) : agenteFisso;
          if (!agente) throw new Error('Il tuo profilo non risulta fra gli agenti attivi di questa agenzia.');
          const richiesta = { assigned_user_id: agente, appointment_type: v.appointment_type,
            duration_minutes: v.duration_minutes, buffer_before_minutes: v.buffer_before_minutes,
            buffer_after_minutes: v.buffer_after_minutes };
          if (v.label) richiesta.label = v.label;
          const esito = await createBookingLink(richiesta);
          modulo.replaceChildren();
          mostraUnaVolta(esito && esito.token, { ruotato: false });
          await carica();
        } else {
          const cambi = {};
          for (const campo of ['appointment_type', 'duration_minutes', 'buffer_before_minutes', 'buffer_after_minutes']) {
            if (v[campo] !== link[campo]) cambi[campo] = v[campo];
          }
          if (v.label && v.label !== (link.label || '')) cambi.label = v.label;
          if (!Object.keys(cambi).length) { successo('Nessuna modifica da salvare.'); return; }
          await patchBookingLink(link.id, cambi);
          modulo.replaceChildren();
          await carica();
          successo('Link aggiornato.');
        }
      } catch (e) {
        mostraErrore(e);
      } finally {
        salva.disabled = false;
      }
    });
  }

  crea.addEventListener('click', () => { pulisci(); apriModulo(null); });
  carica();
  dialogEl.showModal();
}
