// STIMA360 OS — views/oggi.js — LA SCRIVANIA VIVA (#/oggi)
//
// La prima pagina dopo l'accesso. In ordine: il benvenuto con la frase del
// giorno, il briefing, il Radar opportunita' accanto all'Agenda, «STIMA360 ti
// consiglia» e, in secondo piano ed espandibili, attivita' e verifiche. Le
// regole stanno in scrivania/scrivania-model.js e scrivania/viva-model.js
// (pure, testate); qui si chiedono i dati e si disegna, con le API GIA'
// esistenti e i loro permessi. Nessuna API nuova, nessun modello esterno,
// nessun automatismo, nessun dato inventato.
//
// LETTURE (tutte in parallelo; una che fallisce non spegne le altre)
//   GET /api/core/tasks?status=open|in_progress&mistakes=false  task aperti
//   GET /api/appointments/calendar (oggi e domani)              via agenda-api.js
//   GET /api/appointments (passati ancora aperti, richieste)    via agenda-api.js
//   GET /api/core/leads?status=open                             lead aperti
//   GET venditori attivi (sellers/sellers-api.js)               immobile dei venditori
//   GET venditori «overdue» e «no_action» (sellers-api.js)      Radar: ricontatti
//   GET /api/acquisitions?statuses=<aperti>                     Radar: acquisizioni ferme
//   GET /api/core/contacts (+ /contacts/{id} per i mancanti)    nome e telefono
//   GET /api/next-best-action?limit=20                          suggerimenti P23
//   GET /api/core/activities?limit=20&mistakes=false            storico, a richiesta
//   GET /api/signature-requests/notifications?days=14           firme dei fogli visita
//
// SCRITTURE (solo su un gesto esplicito dell'operatore)
//   PATCH /api/core/tasks/{id} {status:'completed'}   «Completa» (e «Annulla»)
//   PATCH /api/core/tasks/{id} {due_at}               «Riprogramma»
// Gli appuntamenti si aprono e si gestiscono nell'Agenda (rotta agenda, giorno, data, id),
// con le sue azioni: la Scrivania non cambia stati di appuntamenti.
//
// NON si leggono: la lista delle valutazioni (dati dell'intera agenzia per un
// agente), le conversazioni WhatsApp (la lettura scrive), il ricalcolo dei
// suggerimenti automatici. Il punteggio di intenzione di vendita non si usa.
//
// «Prendi in carico» NON c'e': il task ha solo un testo libero
// `assigned_to`, e scriverci il proprio nome dal browser vorrebbe dire
// accettare un attore dal client. Serve un'operazione del server.

import { apiGet, apiPatch } from '../core/api-client.js';
import { getSession, sessionEpoch } from '../core/auth.js';
import { navigate } from '../core/router.js';
import { getCalendar, getList } from '../agenda/agenda-api.js';
import { listSellers } from '../sellers/sellers-api.js';
import { addDays, canAssignRecords, formatDateTime, formatDayLong, formatTime, romeIso, statusLabel, todayKey, typeLabel } from '../agenda/agenda-model.js';
import {
  PRIORITY_LABELS, TASK_STATUS_LABELS, appointmentsOfDay, contactName, contactPhone, dueState,
  effectiveAppointmentState, friendlyTaskTitle, groupByClient, isAutomaticTask, isMyAppointment,
  isMyTask, isTechnicalActivity, isUnassignedTask, leadOrigin, nbaSuggestions, newContacts, overdueTasks,
  propertyLabel, readableDescription, taskAssignedNote, taskOwner, taskPropertyId, tasksToDo, tasksWithoutDue,
  telHref, toVerify, upcomingOfDay,
} from '../scrivania/scrivania-model.js';
import {
  OPEN_ACQUISITION_STATUSES, advice, briefing, buildRadar, greeting, idleAcquisitionItem, noActionSellerItem,
  overdueSellerItem, quoteOfDay, reliableName, unassignedValuationItem,
} from '../scrivania/viva-model.js';

const PAGE = 200;
const MAX_PAGES = 5;
const MAX_CONTACT_LOOKUPS = 30;
const PAST_DAYS = 60;
const REQUEST_DAYS = 60;

/** L'indirizzo di un giorno (e, se dato, di un appuntamento) nell'Agenda. */
function agendaHref(giorno, id = null) {
  return `#/${['agenda', 'giorno', giorno, ...(id ? [encodeURIComponent(id)] : [])].join('/')}`;
}

// ---------------------------------------------------------------------------
// piccoli aiuti DOM
// ---------------------------------------------------------------------------

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function link(href, text, className = 'btn btn-small') {
  const a = el('a', className, text);
  a.setAttribute('href', href);
  return a;
}

function button(text, className = 'btn btn-small') {
  const b = el('button', className, text);
  b.type = 'button';
  return b;
}

function badge(text, tone = 'gray') {
  return el('span', `badge badge-${tone}`, text);
}

function errorText(errore) {
  return (errore && errore.message) || 'Operazione non riuscita.';
}

function scrollTo(node) {
  if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function capitalize(testo) {
  return testo ? testo.charAt(0).toUpperCase() + testo.slice(1) : '';
}

/** «Scaduto da 3 giorni», «Scaduto alle 10:00», «Oggi alle 15:30». */
function dueText(task, now) {
  if (!task.due_at) return 'Senza scadenza';
  const quando = Date.parse(task.due_at);
  const stato = dueState(task, now);
  if (stato === 'today') return `Oggi alle ${formatTime(task.due_at)}`;
  if (stato === 'overdue') {
    const giorni = Math.floor((now.getTime() - quando) / 86400000);
    if (giorni >= 2) return `Scaduto da ${giorni} giorni`;
    if (giorni === 1) return 'Scaduto da 1 giorno';
    return `Scaduto alle ${formatTime(task.due_at)}`;
  }
  return formatDateTime(task.due_at);
}

// ---------------------------------------------------------------------------
// letture
// ---------------------------------------------------------------------------

async function allPages(base) {
  const righe = [];
  for (let pagina = 0; pagina < MAX_PAGES; pagina += 1) {
    const sep = base.includes('?') ? '&' : '?';
    const esito = await apiGet(`${base}${sep}limit=${PAGE}&offset=${pagina * PAGE}`);
    const items = Array.isArray(esito && esito.items) ? esito.items : [];
    righe.push(...items);
    if (items.length < PAGE) break;
  }
  return righe;
}

async function openTasks() {
  const [aperti, inCorso] = await Promise.all([
    allPages('/api/core/tasks?status=open&mistakes=false'),
    allPages('/api/core/tasks?status=in_progress&mistakes=false'),
  ]);
  return [...aperti, ...inCorso];
}

async function loadAll(now) {
  const oggi = todayKey(now);
  const richieste = {
    tasks: openTasks(),
    calendar: getCalendar({ from: romeIso(oggi, 0, 0), to: romeIso(addDays(oggi, 2), 0, 0) }),
    pastOpen: getList({
      from: romeIso(addDays(oggi, -PAST_DAYS), 0, 0), to: now.toISOString(),
      statuses: ['requested', 'scheduled', 'confirmed'], limit: 200,
    }),
    requested: getList({
      from: now.toISOString(), to: romeIso(addDays(oggi, REQUEST_DAYS), 0, 0),
      statuses: ['requested'], limit: 200,
    }),
    leads: allPages('/api/core/leads?status=open'),
    sellers: listSellers('status=active&limit=200'),
    sellersOverdue: listSellers('view=overdue&status=active&limit=50'),
    sellersNoAction: listSellers('view=no_action&status=active&limit=50'),
    acquisitions: apiGet(`/api/acquisitions?statuses=${OPEN_ACQUISITION_STATUSES.join(',')}&limit=100`),
    contacts: apiGet('/api/core/contacts?limit=200'),
    nba: apiGet('/api/next-best-action?limit=20'),
  };
  const chiavi = Object.keys(richieste);
  const esiti = await Promise.allSettled(chiavi.map((k) => richieste[k]));
  const dati = {};
  const errori = {};
  chiavi.forEach((k, i) => {
    if (esiti[i].status === 'fulfilled') dati[k] = esiti[i].value;
    else errori[k] = esiti[i].reason;
  });
  return { dati, errori };
}

async function fillMissingContacts(contactsById, ids, isStale) {
  const mancanti = [...new Set(ids)].filter((id) => id && !contactsById.has(id)).slice(0, MAX_CONTACT_LOOKUPS);
  const esiti = await Promise.allSettled(mancanti.map((id) => apiGet(`/api/core/contacts/${encodeURIComponent(id)}`)));
  if (isStale()) return;
  esiti.forEach((e) => {
    if (e.status === 'fulfilled' && e.value && e.value.id) contactsById.set(e.value.id, e.value);
  });
}

// ---------------------------------------------------------------------------
// P23 — Next Best Action: suggerimenti gia' ordinati dall'API.
// ---------------------------------------------------------------------------

// P25.7 (Gap C fix): il backend fornisce subject_label (nome reale del
// contatto, LEFT JOIN su contacts). Se assente resta il fallback
// "{Tipo} #{id}", come prima.
function nbaSubjectLabel(n) {
  if (n.subject_label) return n.subject_label;
  const labels = { lead: 'Venditore', buy_request: 'Acquirente', stima: 'Stima', match: 'Match' };
  const label = labels[n.subject_type] || n.subject_type || 'Soggetto';
  return `${label} #${n.subject_id}`;
}

function nbaCtaHref(n) {
  if (!n.cta_route || !Array.isArray(n.cta_params) || !n.cta_params.length) return null;
  const params = n.cta_params.filter((p) => p !== null && p !== undefined);
  if (!params.length) return null;
  return `#/${n.cta_route}/${params.map((p) => encodeURIComponent(p)).join('/')}`;
}

// ---------------------------------------------------------------------------
// FIRMA SEMPLIFICATA: le firme dei fogli visita da guardare
// ---------------------------------------------------------------------------
// Completate, rifiutate, scadute, invii non riusciti: una voce per visita, dalla stessa API dei
// fogli visita (agenzia della sessione; l'agente vede le sue visite). Ogni voce apre il foglio
// visita. Se l'API non risponde (firma non attiva) il riquadro semplicemente non c'e'.
async function mountAvvisiFirme(slot, superato) {
  let data;
  try { data = await apiGet('/api/signature-requests/notifications?days=14'); } catch (errore) { data = null; }
  if (superato()) return;
  const items = (data && Array.isArray(data.items)) ? data.items : [];
  if (!items.length) { slot.replaceChildren(); return; }
  const card = el('section', 'card panel sign-alerts');
  card.setAttribute('data-sign-alerts', '');
  card.appendChild(el('h2', '', 'Firme dei fogli visita'));
  const lista = el('ul', 'sign-alerts-list');
  for (const it of items) {
    const a = link(`#/fogli-visita/${encodeURIComponent(it.appointment_id)}`, '', `sign-alert sign-alert-${it.state}`);
    a.setAttribute('data-sign-alert', it.state);
    a.append(el('span', `sign-state sign-state-${it.state}`, it.label),
      el('span', '', [it.client, it.property, it.visit_text ? `visita del ${it.visit_text}` : null].filter(Boolean).join(' · ')));
    const li = el('li');
    li.appendChild(a);
    lista.appendChild(li);
  }
  card.appendChild(lista);
  slot.replaceChildren(card);
}

// ---------------------------------------------------------------------------
// la pagina
// ---------------------------------------------------------------------------

export async function renderOggi(container) {
  const epoca = sessionEpoch();
  const sessione = getSession();
  const vedeAgenzia = canAssignRecords(sessione);
  // La Shell non usa la memoria del browser (una sola eccezione, l'Agenda):
  // si parte sempre da «Agenzia» e la scelta vale per questa pagina.
  let modo = 'agency';
  let giro = 0;
  let ultimi = null;
  // SCRIVANIA VIVA: attivita' e verifiche partono chiuse; il consiglio si apre a richiesta.
  let attivitaAperte = false;
  let consigliAperti = false;
  let mostraAttivita = () => {};

  const root = el('div', 'scrivania');
  root.setAttribute('data-scrivania', '');
  container.replaceChildren(root);
  const stale = () => sessionEpoch() !== epoca || root.isConnected === false;

  // -- 1. benvenuto -----------------------------------------------------------
  // Il saluto segue l'ora di Roma; il nome arriva con l'elenco degli operatori
  // (riga «sono io») e si mostra solo se e' un nome di persona affidabile.
  const testata = el('header', 'scrivania-head scrivania-hero');
  testata.setAttribute('data-scrivania-hero', '');
  const titoli = el('div', 'scrivania-titles');
  const saluto = el('h2', 'scrivania-title scrivania-greeting', `${greeting()}!`);
  saluto.setAttribute('data-scrivania-greeting', '');
  const frase = el('figure', 'scrivania-quote');
  frase.setAttribute('data-scrivania-quote', '');
  frase.append(el('figcaption', 'scrivania-quote-label', 'La frase del giorno'),
    el('blockquote', 'scrivania-quote-text', quoteOfDay()));
  titoli.append(saluto, el('p', 'scrivania-date', capitalize(formatDayLong(todayKey()))), frase);
  const comandi = el('div', 'scrivania-commands');
  const aggiorna = button('Aggiorna', 'btn');
  aggiorna.setAttribute('data-scrivania-refresh', '');
  const nuovo = button('Nuovo appuntamento', 'btn primary');
  nuovo.setAttribute('data-scrivania-new-appointment', '');
  nuovo.addEventListener('click', () => navigate('agenda', ['giorno', todayKey(), 'nuovo']));
  comandi.append(aggiorna, nuovo);
  testata.append(titoli, comandi);

  let selettore = null;
  const pulsantiModo = {};
  if (vedeAgenzia) {
    selettore = el('div', 'scrivania-mode');
    selettore.setAttribute('role', 'group');
    selettore.setAttribute('aria-label', 'Quali attività mostrare');
    for (const [valore, etichetta] of [['mine', 'Le mie attività'], ['agency', 'Agenzia']]) {
      const b = button(etichetta, 'scrivania-mode-btn');
      b.setAttribute('data-scrivania-mode', valore);
      b.addEventListener('click', () => {
        if (modo === valore) return;
        modo = valore;
        aggiornaModo();
        if (ultimi) disegna(ultimi);
      });
      pulsantiModo[valore] = b;
      selettore.appendChild(b);
    }
    testata.appendChild(selettore);
  }
  function aggiornaModo() {
    for (const [valore, b] of Object.entries(pulsantiModo)) {
      b.className = `scrivania-mode-btn${valore === modo ? ' active' : ''}`;
      b.setAttribute('aria-pressed', String(valore === modo));
    }
  }
  aggiornaModo();

  const avvisi = el('div', 'scrivania-messages');
  avvisi.setAttribute('aria-live', 'polite');
  // FIRMA SEMPLIFICATA: lo spazio del riquadro «Firme dei fogli visita» (vuoto se non c'e' nulla).
  const firme = el('div', 'scrivania-sign-alerts');
  const corpo = el('div', 'scrivania-body');
  corpo.appendChild(el('p', 'muted scrivania-loading', 'Caricamento della scrivania…'));
  root.append(testata, avvisi, firme, corpo);

  const dialogo = el('dialog', 'scrivania-dialog');
  root.appendChild(dialogo);

  aggiorna.addEventListener('click', () => carica());

  async function carica(messaggio = '', azioneMessaggio = null) {
    const questo = ++giro;
    const superato = () => stale() || questo !== giro;
    aggiorna.disabled = true;
    aggiorna.textContent = 'Aggiornamento…';
    const now = new Date();
    mountAvvisiFirme(firme, superato);
    try {
      const { dati, errori } = await loadAll(now);
      if (superato()) return;
      const contactsById = new Map();
      for (const c of (dati.contacts && dati.contacts.items) || []) contactsById.set(c.id, c);
      const sellers = (dati.sellers && dati.sellers.items) || [];
      const sellersByLead = new Map(sellers.map((s) => [s.lead_id, s]));
      for (const s of sellers) {
        if (s.contact && s.contact.id && !contactsById.has(s.contact.id)) {
          contactsById.set(s.contact.id, { id: s.contact.id, display_name: s.contact.name, phone: s.contact.phone });
        }
      }
      const tasks = dati.tasks || [];
      const leads = dati.leads || [];
      const servono = [
        ...tasksToDo(tasks, now).map((t) => t.contact_id),
        ...newContacts(leads, tasks, { sellersByLead }).map((x) => x.lead.contact_id),
        // Radar del titolare: i nomi delle richieste di valutazione senza agente
        ...(vedeAgenzia ? leads.filter((l) => l.source === 'public_stima' && !l.assigned_agent_id)
          .map((l) => l.contact_id) : []),
      ];
      await fillMissingContacts(contactsById, servono, superato);
      if (superato()) return;
      ultimi = {
        now, errori, tasks, leads, sellersByLead, contactsById,
        leadsById: new Map(leads.map((l) => [l.id, l])),
        calendar: dati.calendar || null,
        pastOpen: (dati.pastOpen && dati.pastOpen.items) || [],
        requested: (dati.requested && dati.requested.items) || [],
        nba: (dati.nba && dati.nba.items) || [],
        sellersOverdue: (dati.sellersOverdue && dati.sellersOverdue.items) || [],
        sellersNoAction: (dati.sellersNoAction && dati.sellersNoAction.items) || [],
        acquisitions: (dati.acquisitions && dati.acquisitions.items) || [],
      };
      disegna(ultimi);
      avvisi.replaceChildren();
      if (messaggio) avvisi.appendChild(messaggioConAzione(messaggio, azioneMessaggio));
    } catch (errore) {
      if (superato()) return;
      avvisi.replaceChildren(el('div', 'error-box', errorText(errore)));
    } finally {
      if (!superato()) {
        aggiorna.disabled = false;
        aggiorna.textContent = 'Aggiorna';
      }
    }
  }

  function messaggioConAzione(testo, azione) {
    const box = el('div', 'success-box scrivania-success', testo);
    if (azione) {
      const b = button(azione.label, 'btn btn-small ghost');
      b.setAttribute('data-scrivania-undo', '');
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          await azione.run();
          if (!stale()) await carica(azione.done || 'Fatto.');
        } catch (errore) {
          if (stale()) return;
          b.disabled = false;
          avvisi.replaceChildren(el('div', 'error-box', errorText(errore)));
        }
      });
      box.appendChild(b);
    }
    return box;
  }

  // -- disegno ---------------------------------------------------------------
  function disegna(d) {
    const now = d.now;
    const oggi = todayKey(now);
    const domani = addDays(oggi, 1);
    const agenti = (d.calendar && d.calendar.agents) || [];
    const agentsById = new Map(agenti.map((a) => [a.id, a.name]));
    const io = agenti.find((a) => a.is_me);
    const meId = io ? io.id : (sessione && sessione.user_id) || null;
    const contesto = { agentsById, meId };
    const mie = modo === 'mine' && vedeAgenzia;

    const tutti = d.tasks.filter((t) => !mie || isMyTask(t, contesto));
    const daFare = tasksToDo(tutti, now);
    const scaduti = overdueTasks(tutti, now);
    const senzaScadenza = tasksWithoutDue(tutti);
    const voci = (d.calendar && d.calendar.items) || [];
    const vociMie = mie ? voci.filter((x) => isMyAppointment(x, meId)) : voci;
    const agendaOggi = appointmentsOfDay(vociMie, oggi);
    const agendaDomani = upcomingOfDay(vociMie, domani);
    const nuovi = newContacts(d.leads, d.tasks, { sellersByLead: d.sellersByLead })
      .filter((x) => !mie || x.lead.assigned_agent_id === meId);
    const verifiche = toVerify(d.pastOpen, d.requested, now)
      .filter((v) => !mie || isMyAppointment(v.appointment, meId));
    const suggerimenti = nbaSuggestions(d.nba, d.tasks);

    corpo.replaceChildren();
    const mancanti = [];
    const nomi = { tasks: 'attività', calendar: 'agenda', pastOpen: 'appuntamenti da verificare',
      requested: 'richieste di appuntamento', leads: 'nuovi contatti', sellers: 'immobili dei venditori',
      contacts: 'contatti', nba: 'suggerimenti', sellersOverdue: 'venditori da ricontattare',
      sellersNoAction: 'venditori senza prossima azione', acquisitions: 'acquisizioni' };
    for (const k of Object.keys(d.errori)) mancanti.push(nomi[k] || k);
    if (mancanti.length) {
      corpo.appendChild(el('div', 'error-box',
        `Alcuni dati non sono disponibili al momento (${mancanti.join(', ')}). Il resto della scrivania è utilizzabile.`));
    }

    // Il radar: solo fonti gia' ristrette dal server; il titolare in «Le mie»
    // vede solo le sue voci; le valutazioni senza agente solo in «Agenzia».
    const mio = (agentId) => !mie || agentId === meId;
    const radar = buildRadar({
      overdue: d.sellersOverdue.map((x) => overdueSellerItem(x, now)).filter((x) => x && mio(x.agentId)),
      valuations: vedeAgenzia && !mie
        ? d.leads.map((l) => unassignedValuationItem(l, d.contactsById.get(l.contact_id), now)).filter(Boolean) : [],
      acquisitions: d.acquisitions.map((x) => idleAcquisitionItem(x, now)).filter((x) => x && mio(x.agentId)),
      noAction: d.sellersNoAction.map((x) => noActionSellerItem(x, now)).filter((x) => x && mio(x.agentId)),
    });
    const radarNonDisponibile = d.errori.sellersOverdue && d.errori.sellersNoAction && d.errori.acquisitions;
    const oggiInScadenza = daFare.filter((t) => dueState(t, now) === 'today');
    const urgente = daFare.find((t) => dueState(t, now) === 'overdue') || null;
    const nomeIo = io ? reliableName(io.name) : '';
    saluto.textContent = nomeIo ? `${greeting(now)}, ${nomeIo}!` : `${greeting(now)}!`;
    const sezioni = {};

    // -- 2. briefing -------------------------------------------------------------
    const brief = el('section', 'card scrivania-briefing');
    brief.setAttribute('data-scrivania-briefing', '');
    const frasi = briefing({
      agendaToday: d.errori.calendar ? [] : agendaOggi, radar: radarNonDisponibile ? { items: [], total: 0 } : radar,
      overdue: d.errori.tasks ? 0 : scaduti.length, today: d.errori.tasks ? 0 : oggiInScadenza.length,
      toVerify: verifiche.length, agency: vedeAgenzia && !mie, now, typeLabel, timeLabel: formatTime,
    });
    if (d.errori.calendar) frasi[0] = 'L’agenda non è disponibile in questo momento.';
    if (radarNonDisponibile) frasi[1] = 'Il radar non è disponibile in questo momento.';
    const testoBrief = el('div', 'scrivania-briefing-text');
    for (const f of frasi) testoBrief.appendChild(el('p', '', f));
    brief.appendChild(testoBrief);
    const riepilogo = el('div', 'scrivania-summary');
    const kpi = [
      ['radar', 'Opportunità', radarNonDisponibile ? '—' : radar.total, radar.total ? 'brand' : 'ok'],
      ['agenda', 'Appuntamenti oggi', d.errori.calendar ? '—' : agendaOggi.length, 'info'],
      ['recuperare', 'Attività scadute', d.errori.tasks ? '—' : scaduti.length, scaduti.length ? 'danger' : 'ok'],
      ['nuovi', 'Nuovi contatti', d.errori.leads ? '—' : nuovi.length, nuovi.length ? 'warn' : 'ok'],
    ];
    for (const [chiave, etichetta, valore, tono] of kpi) {
      const b = button('', `scrivania-kpi scrivania-kpi-${tono}`);
      b.setAttribute('data-scrivania-kpi', chiave);
      b.append(el('span', 'scrivania-kpi-value', valore), el('span', 'scrivania-kpi-label', etichetta));
      b.addEventListener('click', () => {
        if (['recuperare', 'nuovi'].includes(chiave) && !attivitaAperte) apriAttivita(true);
        scrollTo(sezioni[chiave]);
      });
      riepilogo.appendChild(b);
    }
    brief.appendChild(riepilogo);
    corpo.appendChild(brief);

    // -- 3. radar opportunita' e agenda, affiancati ------------------------------
    const griglia = el('div', 'scrivania-viva-grid');
    const colonnaRadar = el('div', 'scrivania-viva-main');
    const colonnaAgenda = el('div', 'scrivania-viva-side');
    griglia.append(colonnaRadar, colonnaAgenda);
    corpo.appendChild(griglia);

    const radarSez = sezione('Radar opportunità', 'Clienti e trattative che aspettano una tua mossa, con il motivo.', 'radar');
    radarSez.box.className += ' scrivania-radar';
    sezioni.radar = radarSez.box;
    if (radarNonDisponibile) {
      radarSez.body.appendChild(el('p', 'muted', 'Il radar non è disponibile in questo momento. Premi «Aggiorna» per riprovare.'));
    } else if (!radar.items.length) {
      const vuoto = el('div', 'scrivania-empty scrivania-radar-empty');
      vuoto.append(el('strong', '', 'Nessun cliente in attesa: tutto in ordine!'),
        el('p', 'muted', 'I tuoi venditori hanno una prossima azione e le acquisizioni sono in movimento. '
          + 'È il momento giusto per coltivare nuovi contatti.'));
      vuoto.appendChild(link('#/venditori', 'Apri Venditori'));
      radarSez.body.appendChild(vuoto);
    } else {
      const lista = el('div', 'scrivania-radar-list');
      for (const voce of radar.items) lista.appendChild(schedaRadar(voce));
      radarSez.body.appendChild(lista);
      const altri = radar.total - radar.items.length;
      const piede = el('div', 'scrivania-radar-foot');
      piede.appendChild(el('span', 'muted', altri > 1 ? `Altre ${altri} opportunità nelle pagine dedicate.`
        : (altri === 1 ? 'Un’altra opportunità nelle pagine dedicate.' : 'Sono tutte le opportunità trovate.')));
      piede.append(link('#/venditori', 'Venditori', 'scrivania-link'), link('#/acquisizioni', 'Acquisizioni', 'scrivania-link'));
      radarSez.body.appendChild(piede);
    }
    colonnaRadar.appendChild(radarSez.box);

    // -- D. agenda della giornata -------------------------------------------
    const agendaSez = sezione('Agenda di oggi', '', 'agenda');
    sezioni.agenda = agendaSez.box;
    agendaSez.actions.appendChild(link(agendaHref(oggi), 'Apri Agenda'));
    if (d.errori.calendar) {
      agendaSez.body.appendChild(el('p', 'muted', 'L’agenda non è disponibile in questo momento.'));
    } else {
      if (!agendaOggi.length) {
        const vuoto = el('div', 'scrivania-empty');
        vuoto.append(el('strong', '', 'Nessun appuntamento oggi.'));
        const crea = button('Nuovo appuntamento', 'btn btn-small');
        crea.addEventListener('click', () => navigate('agenda', ['giorno', oggi, 'nuovo']));
        vuoto.appendChild(crea);
        agendaSez.body.appendChild(vuoto);
      } else {
        const lista = el('div', 'scrivania-list');
        for (const a of agendaOggi) lista.appendChild(rigaAppuntamento(a, oggi, now, vedeAgenzia));
        agendaSez.body.appendChild(lista);
      }
      const domaniBox = el('div', 'scrivania-tomorrow');
      const testa = el('div', 'scrivania-tomorrow-head');
      testa.append(el('h4', '', `Domani · ${agendaDomani.length} ${agendaDomani.length === 1 ? 'appuntamento' : 'appuntamenti'}`),
        link(agendaHref(domani), 'Vedi domani', 'scrivania-link'));
      domaniBox.appendChild(testa);
      for (const a of agendaDomani.slice(0, 4)) {
        const r = link(agendaHref(domani, a.id), '', 'scrivania-mini');
        r.append(el('span', 'scrivania-time', formatTime(a.start_at)),
          el('span', '', [typeLabel(a.type) || 'Appuntamento', a.contact_name].filter(Boolean).join(' · ')));
        domaniBox.appendChild(r);
      }
      if (agendaDomani.length > 4) domaniBox.appendChild(el('p', 'muted', `e altri ${agendaDomani.length - 4}.`));
      agendaSez.body.appendChild(domaniBox);
    }
    // FIRMA SEMPLIFICATA: le firme dei fogli visita stanno con l'Agenda (riguardano le visite).
    colonnaAgenda.append(agendaSez.box, firme);


    // -- 4. STIMA360 ti consiglia ---------------------------------------------------
    const consigli = advice({
      agendaToday: d.errori.calendar ? [] : agendaOggi, radar, now, typeLabel, timeLabel: formatTime,
      agendaHref: (a) => agendaHref(oggi, a.id),
      urgentTask: urgente ? {
        title: friendlyTaskTitle(urgente),
        reason: [dueText(urgente, now), urgente.priority ? `priorità ${(PRIORITY_LABELS[urgente.priority] || urgente.priority).toLowerCase()}` : '']
          .filter(Boolean).join(' · '),
        href: urgente.contact_id && d.contactsById.get(urgente.contact_id) ? `#/contatti/${encodeURIComponent(urgente.contact_id)}` : '',
      } : null,
      verify: verifiche.length ? {
        title: [typeLabel(verifiche[0].appointment.appointment_type) || 'Appuntamento', verifiche[0].appointment.contact_name].filter(Boolean).join(' con '),
        reason: verifiche[0].appointment.start_at ? `Era previsto il ${formatDateTime(verifiche[0].appointment.start_at)}` : '',
        href: agendaHref(todayKey(new Date(verifiche[0].appointment.start_at || now)), verifiche[0].appointment.id),
      } : null,
    });
    corpo.appendChild(pannelloConsigli(consigli));

    // -- 5. attivita' e verifiche, in secondo piano ------------------------------
    const secondaria = el('section', 'scrivania-secondary');
    secondaria.setAttribute('data-scrivania-secondary', '');
    const apri = button('', 'scrivania-secondary-toggle');
    apri.setAttribute('data-scrivania-toggle-tasks', '');
    apri.setAttribute('aria-expanded', String(attivitaAperte));
    const testaApri = el('span', 'scrivania-secondary-head');
    testaApri.append(el('strong', '', 'Attività e verifiche'), el('span', 'scrivania-secondary-action', attivitaAperte ? 'Nascondi' : 'Mostra'));
    const sintesi = el('span', 'scrivania-secondary-chips');
    const chip = (testo, tono) => sintesi.appendChild(el('span', `scrivania-chip scrivania-chip-${tono}`, testo));
    if (d.errori.tasks) chip('Attività non disponibili', 'gray');
    else {
      if (scaduti.length) chip(`${scaduti.length} ${scaduti.length === 1 ? 'scaduta' : 'scadute'}`, 'danger');
      if (urgente && ['urgent', 'high'].includes(urgente.priority)) chip('priorità alta', 'danger');
      if (oggiInScadenza.length) chip(`${oggiInScadenza.length} di oggi`, 'warn');
      if (nuovi.length) chip(`${nuovi.length} ${nuovi.length === 1 ? 'nuovo contatto' : 'nuovi contatti'}`, 'gray');
      if (verifiche.length) chip(`${verifiche.length} da verificare`, 'gray');
      if (!scaduti.length && !oggiInScadenza.length && !nuovi.length && !verifiche.length) chip('tutto in ordine', 'ok');
    }
    apri.append(testaApri, sintesi);
    const contenuto = el('div', 'scrivania-secondary-body');
    contenuto.hidden = !attivitaAperte;
    apri.addEventListener('click', () => apriAttivita(!attivitaAperte));
    function apriAttivita(aperte) {
      attivitaAperte = aperte;
      contenuto.hidden = !aperte;
      apri.setAttribute('aria-expanded', String(aperte));
      testaApri.lastChild.textContent = aperte ? 'Nascondi' : 'Mostra';
    }
    mostraAttivita = () => { apriAttivita(true); scrollTo(sezioni.recuperare); };
    secondaria.append(apri, contenuto);
    const grigliaSec = el('div', 'scrivania-grid');
    const principale = el('div', 'scrivania-main');
    const secondaria2 = el('div', 'scrivania-side');
    grigliaSec.append(principale, secondaria2);
    contenuto.appendChild(grigliaSec);
    corpo.appendChild(secondaria);

    // -- C. da fare prima ----------------------------------------------------
    const daFareSez = sezione('Da fare prima', 'Attività scadute e di oggi, dalla più urgente.', 'da-fare');
    sezioni.recuperare = daFareSez.box;
    if (d.errori.tasks) {
      daFareSez.body.appendChild(el('p', 'muted', 'Le attività non sono disponibili in questo momento. Premi «Aggiorna» per riprovare.'));
    } else if (!daFare.length) {
      const vuoto = el('div', 'scrivania-empty');
      vuoto.append(el('strong', '', 'Nessuna attività scaduta o in scadenza oggi.'),
        el('p', 'muted', mie ? 'Fra le tue attività non c’è nulla di urgente. Puoi passare all’Agenda o ai nuovi contatti.'
          : 'Puoi passare all’Agenda o ai nuovi contatti.'));
      daFareSez.body.appendChild(vuoto);
    } else {
      for (const gruppo of groupByClient(daFare)) daFareSez.body.appendChild(gruppoTask(gruppo, d, contesto));
    }
    const daAssegnare = vedeAgenzia && !mie ? tutti.filter((t) => isUnassignedTask(t)) : [];
    if (!d.errori.tasks && daAssegnare.length) {
      daFareSez.body.appendChild(el('p', 'muted scrivania-note', daAssegnare.length === 1
        ? 'Un’attività aperta non ha un responsabile: si assegna assegnando il suo cliente o il suo lead.'
        : `${daAssegnare.length} attività aperte non hanno un responsabile: si assegnano assegnando il loro cliente o il loro lead.`));
    }
    if (!d.errori.tasks && senzaScadenza.length) {
      const nota = el('p', 'muted scrivania-note');
      nota.append(`${senzaScadenza.length === 1 ? 'Un’attività aperta non ha' : `${senzaScadenza.length} attività aperte non hanno`} una scadenza: `,
        link('#/oggi/attivita', 'vedile in «Attività»', 'scrivania-link'));
      daFareSez.body.appendChild(nota);
    }
    principale.appendChild(daFareSez.box);

    // -- E. nuovi contatti ---------------------------------------------------
    const nuoviSez = sezione('Nuovi contatti da gestire', 'Nuovi proprietari e richieste senza un’attività aperta.', 'nuovi');
    sezioni.nuovi = nuoviSez.box;
    if (d.errori.leads) {
      nuoviSez.body.appendChild(el('p', 'muted', 'I nuovi contatti non sono disponibili in questo momento.'));
    } else if (!nuovi.length) {
      const vuoto = el('div', 'scrivania-empty');
      vuoto.append(el('strong', '', 'Nessun nuovo contatto in attesa.'),
        el('p', 'muted', 'I nuovi contatti che hanno già un’attività aperta sono in «Da fare prima».'));
      nuoviSez.body.appendChild(vuoto);
    } else {
      const lista = el('div', 'scrivania-list');
      for (const voce of nuovi) lista.appendChild(rigaNuovoContatto(voce, d, agentsById));
      nuoviSez.body.appendChild(lista);
    }
    principale.appendChild(nuoviSez.box);


    {
      // dentro l'area secondaria, la colonna di destra: verifiche e storico
      const secondaria = secondaria2;
      // -- F. da verificare ------------------------------------------------------
      const verificaSez = sezione('Da verificare', 'Situazioni che aspettano una tua decisione.', 'verificare');
      const errVerifica = d.errori.pastOpen || d.errori.requested;
      if (errVerifica) verificaSez.body.appendChild(el('p', 'muted', 'Alcune verifiche non sono disponibili in questo momento.'));
      if (verifiche.length) {
        const lista = el('div', 'scrivania-list');
        for (const v of verifiche) lista.appendChild(rigaVerifica(v));
        verificaSez.body.appendChild(lista);
      } else if (!errVerifica) {
        verificaSez.body.appendChild(el('p', 'muted', 'Nessun appuntamento passato senza esito e nessuna richiesta da confermare.'));
      }
      if (suggerimenti.length) {
        verificaSez.body.appendChild(el('h4', 'scrivania-subtitle', 'Suggerimenti del sistema'));
        const lista = el('div', 'scrivania-list');
        for (const n of suggerimenti.slice(0, 5)) lista.appendChild(rigaSuggerimento(n));
        verificaSez.body.appendChild(lista);
      }
      secondaria.appendChild(verificaSez.box);

      secondaria.appendChild(storico());
    }
  }

  function sezione(titolo, sottotitolo, chiave) {
    const box = el('section', 'card scrivania-section');
    box.setAttribute('data-scrivania-section', chiave);
    const testa = el('div', 'scrivania-section-head');
    const t = el('div', '');
    t.appendChild(el('h3', 'scrivania-section-title', titolo));
    if (sottotitolo) t.appendChild(el('p', 'muted scrivania-section-sub', sottotitolo));
    const actions = el('div', 'scrivania-section-actions');
    testa.append(t, actions);
    const body = el('div', 'scrivania-section-body');
    box.append(testa, body);
    return { box, body, actions };
  }

  // -- radar e consigli ------------------------------------------------------
  const ETICHETTE_RADAR = {
    seller_overdue: 'Ricontatto scaduto',
    seller_no_action: 'Senza prossima azione',
    acquisition_idle: 'Acquisizione da riprendere',
    valuation_unassigned: 'Valutazione da assegnare',
  };

  function schedaRadar(v) {
    const card = el('article', `scrivania-radar-item scrivania-radar-${v.kind}`);
    card.setAttribute('data-scrivania-radar', v.key);
    const testo = el('div', 'scrivania-radar-text');
    testo.append(el('span', 'scrivania-radar-kind', ETICHETTE_RADAR[v.kind] || 'Opportunità'),
      el('strong', 'scrivania-radar-title', v.title));
    if (v.place) testo.appendChild(el('span', 'muted scrivania-radar-place', v.place));
    testo.appendChild(el('span', 'scrivania-radar-reason', v.reason));
    if (v.since) testo.appendChild(el('span', 'scrivania-radar-since', v.since));
    const azioni = el('div', 'scrivania-actions');
    const tel = telHref(v.phone);
    if (tel) {
      const chiama = link(tel, 'Chiama', 'btn btn-small primary');
      chiama.setAttribute('data-scrivania-call', '');
      azioni.appendChild(chiama);
    }
    azioni.appendChild(link(v.href, v.hrefLabel, tel ? 'btn btn-small' : 'btn btn-small primary'));
    if (v.propertyHref) azioni.appendChild(link(v.propertyHref, 'Apri immobile'));
    card.append(testo, azioni);
    return card;
  }

  function pannelloConsigli(consigli) {
    const box = el('section', 'card scrivania-advice');
    box.setAttribute('data-scrivania-advice', '');
    const testa = el('div', 'scrivania-advice-head');
    testa.append(el('span', 'scrivania-advice-mark', '★'), el('h3', 'scrivania-section-title', 'STIMA360 ti consiglia'));
    const domanda = button('Cosa mi consigli di fare oggi?', 'btn primary scrivania-advice-ask');
    domanda.setAttribute('data-scrivania-ask', '');
    domanda.setAttribute('aria-expanded', String(consigliAperti));
    const risposta = el('div', 'scrivania-advice-answer');
    risposta.setAttribute('aria-live', 'polite');
    risposta.hidden = !consigliAperti;
    if (!consigli.length) {
      risposta.append(el('p', '', 'Oggi la tua scrivania è in ordine: nessun appuntamento a breve, nessun cliente in attesa, '
        + 'nessuna attività scaduta.'), el('p', 'muted', 'Un’idea: richiama un proprietario che non senti da tempo.'),
      link('#/venditori', 'Apri Venditori'));
    } else {
      const lista = el('ol', 'scrivania-advice-list');
      for (const c of consigli) {
        const voce = el('li', `scrivania-advice-item scrivania-advice-${c.kind}`);
        voce.setAttribute('data-scrivania-advice-item', c.kind);
        const testo = el('div', 'scrivania-advice-text');
        testo.appendChild(el('strong', '', c.title));
        if (c.reason) testo.appendChild(el('span', 'muted', `Perché: ${c.reason}`));
        voce.appendChild(testo);
        if (c.href) voce.appendChild(link(c.href, c.label));
        else {
          const b = button(c.label);
          b.addEventListener('click', () => mostraAttivita());
          voce.appendChild(b);
        }
        lista.appendChild(voce);
      }
      risposta.appendChild(lista);
    }
    domanda.addEventListener('click', () => {
      consigliAperti = !consigliAperti;
      risposta.hidden = !consigliAperti;
      domanda.setAttribute('aria-expanded', String(consigliAperti));
    });
    box.append(testa, domanda, risposta);
    return box;
  }

  // -- righe ----------------------------------------------------------------
  function gruppoTask(gruppo, d, contesto) {
    const contatto = gruppo.contactId ? d.contactsById.get(gruppo.contactId) : null;
    const primo = gruppo.tasks[0];
    const immobileId = gruppo.tasks.map((t) => taskPropertyId(t, d.sellersByLead)).find(Boolean) || null;
    const venditore = primo.lead_id ? d.sellersByLead.get(primo.lead_id) : null;
    const card = el('article', 'scrivania-group');
    card.setAttribute('data-scrivania-group', gruppo.key);

    const testa = el('div', 'scrivania-group-head');
    const chi = el('div', 'scrivania-who');
    chi.appendChild(el('strong', 'scrivania-client', contactName(contatto) || (gruppo.contactId ? 'Cliente' : 'Senza cliente collegato')));
    const dettagli = [];
    if (venditore && venditore.property) dettagli.push(propertyLabel(venditore.property));
    const telefono = contactPhone(contatto);
    if (telefono) dettagli.push(telefono);
    if (dettagli.length) chi.appendChild(el('span', 'muted', dettagli.join(' · ')));
    testa.appendChild(chi);

    const azioni = el('div', 'scrivania-actions');
    const tel = telHref(telefono);
    if (tel) {
      const chiama = link(tel, 'Chiama', 'btn btn-small primary');
      chiama.setAttribute('data-scrivania-call', '');
      azioni.appendChild(chiama);
    }
    if (gruppo.contactId && contatto) azioni.appendChild(link(`#/contatti/${encodeURIComponent(gruppo.contactId)}`, 'Apri contatto'));
    if (immobileId) azioni.appendChild(link(`#/immobili/${encodeURIComponent(immobileId)}`, 'Apri immobile'));
    testa.appendChild(azioni);
    card.appendChild(testa);

    for (const t of gruppo.tasks) card.appendChild(rigaTask(t, d, contesto));
    return card;
  }

  function rigaTask(t, d, contesto) {
    const now = d.now;
    const stato = dueState(t, now);
    const riga = el('div', `scrivania-task scrivania-task-${stato}`);
    riga.setAttribute('data-scrivania-task', String(t.id));
    const testo = el('div', 'scrivania-task-text');
    testo.appendChild(el('span', 'scrivania-task-title', friendlyTaskTitle(t)));
    const descrizione = readableDescription(t);
    if (descrizione) testo.appendChild(el('span', 'muted scrivania-task-desc', descrizione));
    const etichette = el('div', 'scrivania-badges');
    etichette.appendChild(badge(dueText(t, now), stato === 'overdue' ? 'danger' : 'warn'));
    if (['urgent', 'high'].includes(t.priority)) etichette.appendChild(badge(`Priorità ${PRIORITY_LABELS[t.priority].toLowerCase()}`, 'danger'));
    else if (t.priority) etichette.appendChild(badge(`Priorità ${(PRIORITY_LABELS[t.priority] || t.priority).toLowerCase()}`, 'gray'));
    etichette.appendChild(badge(TASK_STATUS_LABELS[t.status] || t.status, 'gray'));
    if (isAutomaticTask(t)) etichette.appendChild(badge('Creato automaticamente', 'role'));
    if (isUnassignedTask(t)) etichette.appendChild(badge('Da assegnare', 'warn'));
    testo.appendChild(etichette);
    const responsabile = taskOwner(t, contesto);
    const nota = taskAssignedNote(t);
    const chi = responsabile ? `Responsabile: ${responsabile}`
      : (isUnassignedTask(t) ? 'Da assegnare: assegna il cliente o il lead dalla sua scheda' : 'Responsabile non disponibile');
    testo.appendChild(el('span', 'muted scrivania-task-meta',
      `Scadenza ${t.due_at ? formatDateTime(t.due_at) : '—'} · ${chi}${nota ? ` · indicato a mano: ${nota}` : ''}`));
    riga.appendChild(testo);

    const azioni = el('div', 'scrivania-actions');
    const completa = button('Completa', 'btn btn-small');
    completa.setAttribute('data-scrivania-complete', String(t.id));
    completa.addEventListener('click', () => completaTask(t, completa));
    const riprogramma = button('Riprogramma', 'btn btn-small ghost');
    riprogramma.setAttribute('data-scrivania-reschedule', String(t.id));
    riprogramma.addEventListener('click', () => apriRiprogramma(t));
    azioni.append(completa, riprogramma);
    riga.appendChild(azioni);
    return riga;
  }

  function rigaNuovoContatto({ lead, seller }, d, agentsById) {
    const contatto = d.contactsById.get(lead.contact_id)
      || (seller && seller.contact ? { display_name: seller.contact.name, phone: seller.contact.phone } : null);
    const riga = el('article', 'scrivania-row');
    riga.setAttribute('data-scrivania-lead', String(lead.id));
    const testo = el('div', 'scrivania-row-text');
    testo.appendChild(el('strong', '', contactName(contatto) || 'Nuovo contatto'));
    const righe = [leadOrigin(lead, seller)];
    if (seller && seller.property) righe.push(propertyLabel(seller.property));
    if (lead.created_at) righe.push(`arrivato il ${formatDateTime(lead.created_at)}`);
    testo.appendChild(el('span', 'muted', righe.filter(Boolean).join(' · ')));
    const agente = lead.assigned_agent_id ? agentsById.get(lead.assigned_agent_id) : '';
    testo.appendChild(el('span', 'muted scrivania-task-meta', agente ? `Responsabile: ${agente}` : 'Nessun responsabile'));
    riga.appendChild(testo);
    const azioni = el('div', 'scrivania-actions');
    const tel = telHref(contactPhone(contatto));
    if (tel) azioni.appendChild(link(tel, 'Chiama', 'btn btn-small primary'));
    if (lead.contact_id) azioni.appendChild(link(`#/contatti/${encodeURIComponent(lead.contact_id)}`, 'Apri contatto'));
    if (seller && seller.property && seller.property.id) {
      azioni.appendChild(link(`#/immobili/${encodeURIComponent(seller.property.id)}`, 'Apri immobile'));
    }
    riga.appendChild(azioni);
    return riga;
  }

  function rigaAppuntamento(a, giorno, now, mostraAgente) {
    const r = link(agendaHref(giorno, a.id), '', 'scrivania-appointment');
    r.setAttribute('data-scrivania-appointment', String(a.id));
    const ore = el('span', 'scrivania-time');
    ore.append(el('strong', '', formatTime(a.start_at)), el('span', 'muted', a.end_at ? formatTime(a.end_at) : ''));
    const testo = el('span', 'scrivania-row-text');
    testo.appendChild(el('strong', '', typeLabel(a.type) || 'Appuntamento'));
    const righe = [a.contact_name, a.place].filter(Boolean);
    if (righe.length) testo.appendChild(el('span', 'muted', righe.join(' · ')));
    if (mostraAgente && a.agent_name) testo.appendChild(el('span', 'muted scrivania-task-meta', `Agente: ${a.agent_name}`));
    const stato = effectiveAppointmentState(a, now);
    const etichetta = stato === 'to_verify' ? 'Da verificare' : (statusLabel(a.status) || a.status);
    const tono = { to_verify: 'danger', completed: 'ok', confirmed: 'ok', requested: 'warn', no_show: 'danger' }[stato] || 'gray';
    r.append(ore, testo, badge(etichetta, tono));
    return r;
  }

  function rigaVerifica(v) {
    const a = v.appointment;
    const giorno = a.start_at ? todayKey(new Date(a.start_at)) : todayKey();
    const r = el('article', 'scrivania-row');
    r.setAttribute('data-scrivania-verify', String(a.id));
    const testo = el('div', 'scrivania-row-text');
    const titolo = { visit_no_outcome: 'Visita senza esito', past_open: 'Appuntamento passato senza esito',
      to_confirm: 'Richiesta da confermare' }[v.kind];
    testo.appendChild(el('strong', '', titolo));
    testo.appendChild(el('span', 'muted', [typeLabel(a.appointment_type) || 'Appuntamento', a.contact_name,
      a.start_at ? formatDateTime(a.start_at) : ''].filter(Boolean).join(' · ')));
    testo.appendChild(el('span', 'muted scrivania-task-meta', `Stato in Agenda: ${statusLabel(a.status) || a.status}`));
    r.appendChild(testo);
    const azioni = el('div', 'scrivania-actions');
    azioni.appendChild(link(agendaHref(giorno, a.id),
      v.kind === 'to_confirm' ? 'Apri richiesta' : 'Registra esito'));
    r.appendChild(azioni);
    return r;
  }

  function rigaSuggerimento(n) {
    const r = el('article', 'scrivania-row');
    const testo = el('div', 'scrivania-row-text');
    testo.appendChild(el('strong', '', nbaSubjectLabel(n)));
    if (n.reason) testo.appendChild(el('span', 'muted', n.reason));
    r.appendChild(testo);
    const href = nbaCtaHref(n);
    if (href) {
      const azioni = el('div', 'scrivania-actions');
      azioni.appendChild(link(href, 'Apri'));
      r.appendChild(azioni);
    }
    return r;
  }

  // -- G. storico (a richiesta) ---------------------------------------------
  function storico() {
    const box = el('section', 'card scrivania-section scrivania-history');
    box.setAttribute('data-scrivania-section', 'storico');
    const testa = el('div', 'scrivania-section-head');
    testa.appendChild(el('h3', 'scrivania-section-title', 'Storico recente'));
    const apri = button('Mostra', 'btn btn-small ghost');
    apri.setAttribute('aria-expanded', 'false');
    testa.appendChild(apri);
    const corpoStorico = el('div', 'scrivania-section-body');
    corpoStorico.hidden = true;
    box.append(testa, corpoStorico);
    let tecniche = false;
    let righe = null;

    async function mostra() {
      corpoStorico.replaceChildren(el('p', 'muted', 'Caricamento…'));
      try {
        const esito = await apiGet('/api/core/activities?limit=20&mistakes=false');
        if (stale()) return;
        righe = Array.isArray(esito && esito.items) ? esito.items : [];
        disegnaStorico();
      } catch (errore) {
        if (stale()) return;
        corpoStorico.replaceChildren(el('p', 'muted', `Storico non disponibile: ${errorText(errore)}`));
      }
    }
    function disegnaStorico() {
      corpoStorico.replaceChildren();
      const visibili = righe.filter((a) => tecniche || !isTechnicalActivity(a));
      const nascoste = righe.length - righe.filter((a) => !isTechnicalActivity(a)).length;
      if (!visibili.length) corpoStorico.appendChild(el('p', 'muted', 'Nessuna attività recente registrata da un operatore.'));
      const lista = el('div', 'scrivania-list');
      for (const a of visibili.slice(0, 10)) {
        const r = el('div', 'scrivania-mini');
        r.append(el('span', 'scrivania-time', a.occurred_at ? formatDateTime(a.occurred_at) : ''),
          el('span', '', a.description || a.activity_type || 'Attività'));
        lista.appendChild(r);
      }
      corpoStorico.appendChild(lista);
      const piede = el('div', 'scrivania-tomorrow-head');
      if (nascoste) {
        const alterna = button(tecniche ? 'Nascondi operazioni automatiche' : `Mostra anche ${nascoste} operazioni automatiche`, 'btn btn-small ghost');
        alterna.addEventListener('click', () => { tecniche = !tecniche; disegnaStorico(); });
        piede.appendChild(alterna);
      }
      piede.appendChild(link('#/oggi/attivita', 'Apri «Attività»', 'scrivania-link'));
      corpoStorico.appendChild(piede);
    }
    apri.addEventListener('click', () => {
      const aperto = corpoStorico.hidden;
      corpoStorico.hidden = !aperto;
      apri.textContent = aperto ? 'Nascondi' : 'Mostra';
      apri.setAttribute('aria-expanded', String(aperto));
      if (aperto && righe === null) mostra();
    });
    return box;
  }

  // -- azioni sui task -------------------------------------------------------
  async function completaTask(t, pulsante) {
    pulsante.disabled = true;
    pulsante.textContent = 'Completamento…';
    try {
      await apiPatch(`/api/core/tasks/${encodeURIComponent(t.id)}`, { status: 'completed' });
      if (stale()) return;
      const prima = t.status;
      await carica(`Attività completata: «${friendlyTaskTitle(t)}».`, {
        label: 'Annulla',
        done: 'Attività riaperta.',
        run: () => apiPatch(`/api/core/tasks/${encodeURIComponent(t.id)}`, { status: prima }),
      });
    } catch (errore) {
      if (stale()) return;
      pulsante.disabled = false;
      pulsante.textContent = 'Completa';
      avvisi.replaceChildren(el('div', 'error-box', `Non è stato possibile completare l’attività: ${errorText(errore)}`));
    }
  }

  function apriRiprogramma(t) {
    const now = new Date();
    const oggi = todayKey(now);
    dialogo.replaceChildren();
    const form = el('form', 'scrivania-form');
    form.appendChild(el('h3', 'section-title', 'Riprogramma'));
    form.appendChild(el('p', 'muted', friendlyTaskTitle(t)));
    const rapide = el('div', 'scrivania-quick');
    const opzioni = [];
    const oraRoma = Number(formatTime(now).slice(0, 2));
    if (oraRoma < 17) opzioni.push(['Oggi alle 18:00', oggi, 18, 0]);
    opzioni.push(['Domani alle 9:00', addDays(oggi, 1), 9, 0]);
    opzioni.push(['Fra 3 giorni', addDays(oggi, 3), 9, 0]);
    opzioni.push(['Fra una settimana', addDays(oggi, 7), 9, 0]);
    const data = el('input', 'input');
    data.type = 'date';
    data.required = true;
    const ora = el('input', 'input');
    ora.type = 'time';
    ora.required = true;
    data.value = addDays(oggi, 1);
    ora.value = '09:00';
    for (const [etichetta, giorno, h, m] of opzioni) {
      const b = button(etichetta, 'btn btn-small');
      b.addEventListener('click', () => {
        data.value = giorno;
        ora.value = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
      });
      rapide.appendChild(b);
    }
    form.appendChild(rapide);
    const campi = el('div', 'scrivania-fields');
    const campoData = el('label', 'form-field');
    campoData.append(el('span', '', 'Giorno'), data);
    const campoOra = el('label', 'form-field');
    campoOra.append(el('span', '', 'Ora'), ora);
    campi.append(campoData, campoOra);
    form.appendChild(campi);
    const errore = el('div', 'field-error');
    form.appendChild(errore);
    const piede = el('div', 'modal-actions');
    const annulla = button('Annulla', 'btn ghost');
    const salva = el('button', 'btn primary', 'Salva');
    salva.type = 'submit';
    piede.append(annulla, salva);
    form.appendChild(piede);
    dialogo.appendChild(form);
    annulla.addEventListener('click', () => dialogo.close());
    form.addEventListener('submit', async (evento) => {
      evento.preventDefault();
      errore.textContent = '';
      const [h, m] = String(ora.value || '').split(':').map(Number);
      let quando;
      try {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(data.value) || !Number.isInteger(h) || !Number.isInteger(m)) {
          throw new Error('Scegli un giorno e un’ora.');
        }
        quando = romeIso(data.value, h, m);
      } catch (e) {
        errore.textContent = errorText(e);
        return;
      }
      salva.disabled = true;
      annulla.disabled = true;
      salva.textContent = 'Salvataggio…';
      try {
        await apiPatch(`/api/core/tasks/${encodeURIComponent(t.id)}`, { due_at: quando });
        if (stale()) return;
        dialogo.close();
        await carica(`Nuova scadenza per «${friendlyTaskTitle(t)}»: ${formatDateTime(quando)}.`);
      } catch (e) {
        if (stale()) return;
        salva.disabled = false;
        annulla.disabled = false;
        salva.textContent = 'Salva';
        errore.textContent = `Non è stato possibile salvare: ${errorText(e)}`;
      }
    });
    if (typeof dialogo.showModal === 'function') dialogo.showModal();
  }

  await carica();
}
