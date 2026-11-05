// STIMA360 OS — scrivania/scrivania-model.js
//
// LA SCRIVANIA (#/oggi): le regole di presentazione, senza DOM e senza rete.
// Tutto qui e' una funzione pura sui dati gia' restituiti dalle API esistenti:
// nessuna scrittura, nessuno stato cambiato, nessun dato inventato. La vista
// (views/oggi.js) chiede i dati, passa di qui e disegna.
//
// LE CAUSE DELLE ANOMALIE DELLA VECCHIA PAGINA «OGGI», E COSA FA QUESTO FILE
//
//  * «Nessuna priorita' da segnalare» con sei task prioritari: quel riquadro
//    leggeva SOLO /api/next-best-action (P23), un motore che non conosce i
//    task. Qui i task urgenti vengono dai task (`tasksToDo`); i suggerimenti
//    P23 restano, come suggerimenti, e non duplicano un cliente gia' presente
//    fra i task (`nbaSuggestions`).
//  * «Prossime visite» con visite passate e completate: la pagina leggeva
//    /api/property/visits senza data ne' stato. Qui l'agenda viene
//    dall'Agenda (GET /api/appointments/calendar) e i giorni sono quelli di
//    Roma (`appointmentsOfDay`).
//  * Appuntamenti passati ancora «Fissato»: non si chiudono da soli. Finiscono
//    in «Da verificare» (`toVerify`): la decisione resta all'operatore.
//  * FLOW-R001, system, lead #87: sono la descrizione tecnica che la regola
//    automatica scrive sul task. Qui il titolo resta quello del task, la
//    descrizione tecnica non e' mostrata come testo principale e il task e'
//    marcato «Creato automaticamente» (`taskView`). Il dato non cambia ed e'
//    sempre leggibile nella scheda «Attivita'».
//  * Task senza assegnatario: il CRM non ha un agente strutturato sui task
//    (solo il testo libero `assigned_to`). Il responsabile mostrato e' quel
//    testo, oppure l'agente del cliente o del lead, se noto; altrimenti
//    «Nessun responsabile».

export const TIMEZONE = 'Europe/Rome';

export const OPEN_TASK_STATUSES = Object.freeze(['open', 'in_progress']);
export const PRIORITY_RANK = Object.freeze({ urgent: 0, high: 1, normal: 2, low: 3 });
export const PRIORITY_LABELS = Object.freeze({ urgent: 'Urgente', high: 'Alta', normal: 'Normale', low: 'Bassa' });
export const TASK_STATUS_LABELS = Object.freeze({ open: 'Da fare', in_progress: 'In corso' });

/** Gli stati di un appuntamento ancora «aperto»: senza esito registrato. */
export const OPEN_APPOINTMENT_STATUSES = Object.freeze(['requested', 'scheduled', 'confirmed']);
/** Gli stati che non sono piu' un impegno: mai fra i prossimi appuntamenti. */
export const CLOSED_APPOINTMENT_STATUSES = Object.freeze(['completed', 'cancelled', 'no_show', 'rescheduled']);

/** Le fonti dei task scritti da un'automazione (stesso elenco di views/attivita.js). */
export const AUTOMATIC_TASK_SOURCES = Object.freeze(['flow', 'followup', 'automated']);

/** Titoli scritti dalle regole automatiche, resi in parole dell'agente. */
const AUTOMATIC_TITLES = Object.freeze({
  'Primo contatto lead': 'Primo contatto con il cliente',
  'Ricontattare acquirente: azione scaduta': 'Ricontattare l’acquirente',
  'Revisionare match forte non proposto': 'Proporre un abbinamento forte',
  'Revisionare variazione MATCH': 'Controllare un abbinamento cambiato',
});

/** Descrizioni tecniche: non sono testo per l'agente. */
const TECHNICAL_DESCRIPTION = /^Task generato da FLOW-R\d+|\b(?:lead|stima|match|buy_request) #\d+\b/i;

export const LEAD_SOURCE_LABELS = Object.freeze({
  public_stima: 'Richiesta di valutazione dal sito',
  property_seller: 'Proprietario che vende',
  seller_lead: 'Proprietario che vende',
});

export const LEAD_PIPELINE_LABELS = Object.freeze({ sell: 'Vendita', buy: 'Acquisto', general: 'Contatto' });

// ---------------------------------------------------------------------------
// date e fuso (Roma)
// ---------------------------------------------------------------------------

const PARTI = new Intl.DateTimeFormat('en-CA', {
  timeZone: TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit',
});

/** 'YYYY-MM-DD' del giorno di Roma che contiene l'istante, o '' se non e' una data. */
export function romeDayKey(value) {
  const ms = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
  if (!Number.isFinite(ms)) return '';
  const p = {};
  for (const x of PARTI.formatToParts(new Date(ms))) p[x.type] = x.value;
  return `${p.year}-${p.month}-${p.day}`;
}

function ms(value) {
  if (value === null || value === undefined || value === '') return null;
  const t = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
  return Number.isFinite(t) ? t : null;
}

// ---------------------------------------------------------------------------
// task
// ---------------------------------------------------------------------------

export function isOpenTask(task) {
  return !!task && OPEN_TASK_STATUSES.includes(task.status);
}

export function isAutomaticTask(task) {
  const fonte = task && task.metadata && task.metadata.source;
  return AUTOMATIC_TASK_SOURCES.includes(fonte) || TECHNICAL_DESCRIPTION.test(String((task && task.description) || ''));
}

/**
 * Lo stato di scadenza di un task aperto, nel giorno di Roma:
 * 'overdue' (scadenza passata), 'today' (oggi, non ancora passata),
 * 'later', oppure 'none' senza scadenza.
 */
export function dueState(task, now = new Date()) {
  const t = ms(task && task.due_at);
  if (t === null) return 'none';
  if (t < now.getTime()) return 'overdue';
  return romeDayKey(t) === romeDayKey(now) ? 'today' : 'later';
}

/** Prima gli scaduti, poi la priorita', poi la scadenza piu' vecchia. */
export function compareTasks(a, b, now = new Date()) {
  const stato = { overdue: 0, today: 1, later: 2, none: 3 };
  const d = stato[dueState(a, now)] - stato[dueState(b, now)];
  if (d) return d;
  const p = (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9);
  if (p) return p;
  const ta = ms(a.due_at);
  const tb = ms(b.due_at);
  if (ta !== tb) return (ta ?? Infinity) - (tb ?? Infinity);
  return (Number(a.id) || 0) - (Number(b.id) || 0);
}

/** I task da fare prima: aperti, scaduti o in scadenza oggi, in ordine di urgenza. */
export function tasksToDo(tasks, now = new Date()) {
  return (tasks || [])
    .filter((t) => isOpenTask(t) && ['overdue', 'today'].includes(dueState(t, now)))
    .sort((a, b) => compareTasks(a, b, now));
}

export function overdueTasks(tasks, now = new Date()) {
  return (tasks || []).filter((t) => isOpenTask(t) && dueState(t, now) === 'overdue');
}

export function tasksWithoutDue(tasks) {
  return (tasks || []).filter((t) => isOpenTask(t) && ms(t.due_at) === null);
}

/**
 * Raggruppa visivamente per cliente, nell'ordine dei task: un gruppo per
 * contatto (o per lead, o il task da solo). Ogni task resta un elemento suo.
 */
export function groupByClient(sortedTasks) {
  const gruppi = [];
  const indice = new Map();
  for (const t of sortedTasks || []) {
    const chiave = t.contact_id ? `c${t.contact_id}` : (t.lead_id ? `l${t.lead_id}` : `t${t.id}`);
    if (!indice.has(chiave)) {
      indice.set(chiave, gruppi.length);
      gruppi.push({ key: chiave, contactId: t.contact_id || null, leadId: t.lead_id || null, tasks: [] });
    }
    gruppi[indice.get(chiave)].tasks.push(t);
  }
  return gruppi;
}

export function friendlyTaskTitle(task) {
  const titolo = String((task && task.title) || '').trim();
  if (!titolo) return 'Attività da svolgere';
  return AUTOMATIC_TITLES[titolo] || titolo;
}

/** La descrizione da mostrare all'agente, o '' se e' solo testo tecnico. */
export function readableDescription(task) {
  const testo = String((task && task.description) || '').trim();
  if (!testo || TECHNICAL_DESCRIPTION.test(testo)) return '';
  return testo;
}

/** L'immobile collegato a un task: dai metadati del task o dal lead venditore. */
export function taskPropertyId(task, sellersByLead) {
  const daMeta = task && task.metadata && Number(task.metadata.property_id);
  if (Number.isInteger(daMeta) && daMeta > 0) return daMeta;
  const venditore = task && task.lead_id && sellersByLead ? sellersByLead.get(task.lead_id) : null;
  const id = venditore && venditore.property && Number(venditore.property.id);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/**
 * Il responsabile di un task: `responsible_agent_id`, calcolato dal SERVER con
 * la stessa regola che restringe la lettura di un agente (agente del lead,
 * altrimenti del contatto). Il testo libero `assigned_to` non e' un'identita':
 * non decide mai nulla, si mostra solo come annotazione (`taskAssignedNote`).
 */
export function taskResponsibleId(task) {
  const id = task && Number(task.responsible_agent_id);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export function taskOwner(task, { agentsById } = {}) {
  const id = taskResponsibleId(task);
  const nome = id && agentsById ? agentsById.get(id) : null;
  return nome ? String(nome) : '';
}

/** Il testo scritto a mano nel campo «Assegnato a», se c'e' (solo da leggere). */
export function taskAssignedNote(task) {
  const testo = String((task && task.assigned_to) || '').trim();
  return testo && testo.toLowerCase() !== 'system' ? testo : '';
}

/** Senza responsabile: nessun agente sul lead ne' sul contatto. Si assegna dalla loro scheda. */
export function isUnassignedTask(task) {
  return taskResponsibleId(task) === null;
}

/**
 * «Le mie attivita'»: la stessa regola con cui il server mostra i task a un
 * agente. Mio se ne sono il responsabile; se non ha responsabile, mio se l'ho
 * creato io (`created_by_user_id`, scritto dal server). Nessun confronto di nomi.
 */
export function isMyTask(task, { meId } = {}) {
  if (!task || !meId) return false;
  const responsabile = taskResponsibleId(task);
  if (responsabile !== null) return responsabile === meId;
  return Number(task.created_by_user_id) === meId;
}

// ---------------------------------------------------------------------------
// contatti
// ---------------------------------------------------------------------------

export function contactName(contact) {
  if (!contact) return '';
  const nome = String(contact.display_name || '').trim()
    || [contact.first_name, contact.last_name].filter(Boolean).join(' ').trim()
    || String(contact.company_name || '').trim()
    || String(contact.name || '').trim();
  return nome;
}

export function contactPhone(contact) {
  if (!contact) return '';
  return String(contact.phone || contact.secondary_phone || '').trim();
}

/** `tel:` solo con cifre e un «+» iniziale; '' se il numero non e' utilizzabile. */
export function telHref(phone) {
  const pulito = String(phone || '').trim().replace(/[^\d+]/g, '');
  const numero = pulito.startsWith('+') ? `+${pulito.slice(1).replace(/\+/g, '')}` : pulito.replace(/\+/g, '');
  return numero.replace(/\D/g, '').length >= 6 ? `tel:${numero}` : '';
}

export function propertyLabel(property) {
  if (!property) return '';
  const indirizzo = [property.address, property.civic_number].filter(Boolean).join(' ').trim();
  const dove = [indirizzo, property.city].filter(Boolean).join(', ');
  return dove || String(property.title || '').trim() || (property.code ? `Immobile ${property.code}` : '');
}

/**
 * I nuovi contatti da gestire: lead aperti in fase «Nuovo» che NON hanno gia'
 * un task aperto (per lo stesso lead o per lo stesso cliente). Chi ha un task
 * e' gia' in «Da fare prima»: non diventa una seconda attivita'.
 */
export function newContacts(leads, openTasks, { sellersByLead } = {}) {
  const leadConTask = new Set();
  const contattiConTask = new Set();
  for (const t of openTasks || []) {
    if (!isOpenTask(t)) continue;
    if (t.lead_id) leadConTask.add(t.lead_id);
    if (t.contact_id) contattiConTask.add(t.contact_id);
  }
  return (leads || [])
    .filter((l) => l && l.status === 'open' && l.stage === 'new')
    .filter((l) => !leadConTask.has(l.id) && !contattiConTask.has(l.contact_id))
    .map((l) => ({ lead: l, seller: sellersByLead ? sellersByLead.get(l.id) || null : null }))
    .sort((a, b) => (ms(b.lead.created_at) ?? 0) - (ms(a.lead.created_at) ?? 0));
}

export function leadOrigin(lead, seller) {
  if (lead && LEAD_SOURCE_LABELS[lead.source]) return LEAD_SOURCE_LABELS[lead.source];
  if (seller) return LEAD_SOURCE_LABELS.property_seller;
  return LEAD_PIPELINE_LABELS[lead && lead.pipeline] || 'Nuovo contatto';
}

// ---------------------------------------------------------------------------
// appuntamenti
// ---------------------------------------------------------------------------

/** Gli appuntamenti (non gli «Occupato» dei colleghi) di un giorno di Roma, per orario. */
export function appointmentsOfDay(items, dayKey) {
  return (items || [])
    .filter((x) => x && x.kind === 'appointment' && romeDayKey(x.start_at) === dayKey)
    .filter((x) => x.cancelled_kind !== 'mistake' && x.status !== 'cancelled' && x.status !== 'rescheduled')
    .sort((a, b) => (ms(a.start_at) ?? 0) - (ms(b.start_at) ?? 0) || (a.id || 0) - (b.id || 0));
}

/** Domani: solo cio' che e' ancora da fare (niente completati o chiusi). */
export function upcomingOfDay(items, dayKey) {
  return appointmentsOfDay(items, dayKey).filter((x) => !CLOSED_APPOINTMENT_STATUSES.includes(x.status));
}

/** Uno stato «effettivo»: un appuntamento aperto gia' finito e' da verificare. */
export function effectiveAppointmentState(item, now = new Date()) {
  const fine = ms(item && (item.end_at || item.start_at));
  if (item && OPEN_APPOINTMENT_STATUSES.includes(item.status) && fine !== null && fine < now.getTime()) {
    return 'to_verify';
  }
  if (item && item.status === 'requested') return 'to_confirm';
  return item ? item.status : '';
}

/**
 * Da verificare: appuntamenti gia' finiti e ancora aperti (nessun esito) e
 * richieste ancora da confermare. Niente si chiude da solo: e' un elenco.
 */
export function toVerify(pastOpen, requested, now = new Date()) {
  const visti = new Set();
  const out = [];
  for (const a of pastOpen || []) {
    if (!a || visti.has(a.id) || a.cancelled_kind === 'mistake') continue;
    if (!OPEN_APPOINTMENT_STATUSES.includes(a.status)) continue;
    const fine = ms(a.end_at || a.start_at);
    if (fine === null || fine >= now.getTime()) continue;
    visti.add(a.id);
    out.push({ kind: a.appointment_type === 'buyer_visit' ? 'visit_no_outcome' : 'past_open', appointment: a });
  }
  for (const a of requested || []) {
    if (!a || visti.has(a.id) || a.status !== 'requested' || a.cancelled_kind === 'mistake') continue;
    visti.add(a.id);
    out.push({ kind: 'to_confirm', appointment: a });
  }
  return out.sort((x, y) => (ms(x.appointment.start_at) ?? 0) - (ms(y.appointment.start_at) ?? 0));
}

export function isMyAppointment(item, meId) {
  const agente = item && (item.agent_id ?? item.assigned_user_id);
  return !!meId && agente === meId;
}

// ---------------------------------------------------------------------------
// suggerimenti (P23)
// ---------------------------------------------------------------------------

/** I suggerimenti P23 che non riguardano un cliente gia' presente fra i task. */
export function nbaSuggestions(items, openTasks) {
  const lead = new Set();
  const contatti = new Set();
  for (const t of openTasks || []) {
    if (!isOpenTask(t)) continue;
    if (t.lead_id) lead.add(t.lead_id);
    if (t.contact_id) contatti.add(t.contact_id);
  }
  return (items || []).filter((n) => n
    && !(n.lead_id && lead.has(n.lead_id))
    && !(n.contact_id && contatti.has(n.contact_id))
    && !(n.subject_type === 'lead' && lead.has(n.subject_id)));
}

// ---------------------------------------------------------------------------
// storico
// ---------------------------------------------------------------------------

export const TECHNICAL_ACTIVITY_TYPES = Object.freeze(['system', 'status_change']);

/** Un'attivita' tecnica automatica: fuori dallo storico principale. */
export function isTechnicalActivity(activity) {
  if (!activity) return true;
  if (TECHNICAL_ACTIVITY_TYPES.includes(activity.activity_type)) return true;
  return TECHNICAL_DESCRIPTION.test(String(activity.description || ''))
    || /\bFLOW-R\d+\b/.test(String(activity.description || ''));
}
