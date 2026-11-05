// STIMA360 OS — views/agenda/agenda-page.js (A30-4, AGENDA MOBILE)
//
// La pagina Agenda, `#/agenda[/<vista>/<AAAA-MM-GG>]`.
//
// AGENDA MOBILE: Giorno, Settimana, Mese e Lista su OGNI dispositivo, e la
// vista scelta e' SEMPRE quella mostrata (la regola G3 "su smartphone solo
// Lista e Giorno" e' revocata). Sullo schermo stretto (COMPACT_QUERY) la
// pagina prende la classe `agenda-compact`: testata ridotta, comandi
// secondari nel menu "⋯", filtri a comando, "+" flottante, settimana a
// colonne strette, mese a indicatori; un gesto orizzontale cambia periodo.
// L'ultima vista scelta resta nel browser (agenda/agenda-prefs.js, solo il
// nome della vista).
//
// Nessun trascinamento, nessun ridimensionamento.
// I dati vengono SOLO da /api/appointments (agenda/agenda-api.js).
//
// Ogni caricamento annota `sessionEpoch()` e la rotta: una risposta arrivata
// dopo un cambio di sessione o di pagina viene buttata, come nel router.

import { getSession, sessionEpoch } from '../../core/auth.js';
import { navigate } from '../../core/router.js';
import {
  ALL_STATUSES_FILTER,
  COMPACT_QUERY,
  LIST_CHUNK_DAYS,
  MISTAKES_FILTER,
  DEFAULT_FILTERS,
  STATUS_LABELS,
  TYPE_LABELS,
  CREATE_HIDDEN_TYPES,
  VIEW_LABELS,
  VIEW_SLUGS,
  TIMEZONE_LABEL,
  VIEWS,
  actionSuccessMessage,
  addDays,
  activeFilterCount,
  calendarFilterParams,
  canAssignRecords,
  effectiveView,
  errorMessage,
  filterListByAgent,
  formatDateTime,
  formatDayLong,
  formatMonth,
  formatRange,
  isDateKey,
  legendEntries,
  listFilterParams,
  listRange,
  loadedAtLabel,
  normalizeFilters,
  rangeFor,
  romeDateKey,
  shiftKey,
  shortcutAction,
  statusLabel,
  swipeDirection,
  todayKey,
  typeLabel,
  viewFromSlug,
} from '../../agenda/agenda-model.js';
import {
  getAgents, getCalendar, getList, syncLegacyRequests,
} from '../../agenda/agenda-api.js';
import {
  refreshNowLines, renderDay, renderList, renderMonth, renderWeek,
} from '../../components/agenda/agenda-views.js';
import { readPreferredView, savePreferredView } from '../../agenda/agenda-prefs.js';
import { openAppointmentDrawer } from '../../components/agenda/agenda-drawer.js';
import {
  openActionDialog, openAvailabilityDialog, openBookingLinksDialog, openCreateDialog,
} from '../../components/agenda/agenda-dialogs.js';
import { mountCalendarSyncPanel } from '../../components/agenda/agenda-calendar-sync-panel.js';
// STIMA Voice V8-5: lo stato letto dalla shell all'avvio (nessuna richiesta
// in piu'): con `speech_input` il dialog «Nuovo appuntamento» offre il microfono.
import { lastVoiceStatus } from '../../voice/voice-api.js';

// Toglie il listener sulla soglia compatta della pagina Agenda montata per
// ultima (uno solo alla volta, vedi `renderAgenda`).
let smettiDiSeguireLaSoglia = null;

// Gli agenti si chiedono una volta per sessione (e dopo ogni cambio di
// sessione): servono al form e ai nomi nella cronologia.
let agentiInMemoria = { epoch: null, items: null };

async function agenti() {
  if (agentiInMemoria.epoch === sessionEpoch() && agentiInMemoria.items) {
    return agentiInMemoria.items;
  }
  const esito = await getAgents();
  agentiInMemoria = { epoch: sessionEpoch(), items: (esito && esito.items) || [] };
  return agentiInMemoria.items;
}

// Filtri (piano A30-4 §11): restano in memoria per la sessione, cosi'
// cambiando settimana o vista non si perdono. Legati alla sessione come gli
// agenti: non passano mai a un altro operatore. Nessun localStorage.
let filtriInMemoria = { epoch: null, valori: DEFAULT_FILTERS };

function leggiFiltri() {
  return filtriInMemoria.epoch === sessionEpoch()
    ? filtriInMemoria.valori : { ...DEFAULT_FILTERS };
}

function salvaFiltri(valori) {
  filtriInMemoria = { epoch: sessionEpoch(), valori };
}

// A30-5: un appuntamento creato in un giorno fuori dal periodo visualizzato
// porta la pagina su quel giorno; il messaggio di conferma sopravvive a quella
// navigazione (una sola volta, poi si consuma). Legato alla sessione: un
// messaggio non passa mai a un altro operatore.
let messaggioInSospeso = { epoch: null, testo: '' };

function prendiMessaggio() {
  const { epoch, testo } = messaggioInSospeso;
  messaggioInSospeso = { epoch: null, testo: '' };
  return epoch === sessionEpoch() ? testo : '';
}

function confermaCreazione(creato) {
  if (!creato || !creato.start_at) return 'Appuntamento creato.';
  const cosa = [typeLabel(creato.appointment_type), statusLabel(creato.status)]
    .filter(Boolean).join(' · ');
  return `Appuntamento creato: ${formatDateTime(creato.start_at)}${cosa ? ` · ${cosa}` : ''}.`;
}

// A30-7: chi smista la coda delle richieste. La regola (`may_assign_records`)
// e' la STESSA che decide, in A30-13B.1, se il campo Agente del form di
// creazione e' libero o bloccato su se stessi: vive una volta sola, in
// agenda-model.js (`canAssignRecords`), non duplicata qui. Solo per NON
// mostrare un bottone che il server rifiuterebbe; l'autorita' resta il
// server (403).
function gestisceRichieste(sessione) {
  return canAssignRecords(sessione);
}

function numero(valore) {
  const n = Number(valore);
  return Number.isInteger(n) && n >= 0 ? n : 0;
}

function esitoSincronizzazione(esito) {
  return `Richieste dal sito aggiornate: nuove ${numero(esito && esito.imported)} · `
    + `già presenti ${numero(esito && esito.already_present)} · `
    + `escluse ${numero(esito && esito.excluded)}.`;
}

// A30-9B: dopo il callback OAuth, Google riporta qui con un errore in query
// string PRIMA del `#` (mai nel fragment: il router leggerebbe la rotta
// sbagliata, vedi calendar_sync/router.py::_AGENDA_BASE). Si legge una sola
// volta e si pulisce l'URL, cosi' un ricaricamento della pagina non la
// ripete.
const MESSAGGI_ERRORE_GOOGLE = {
  GOOGLE_OAUTH_DENIED: 'Autorizzazione Google annullata.',
  GOOGLE_OAUTH_STATE_EXPIRED: 'La richiesta di collegamento e\' scaduta. Riprova.',
  GOOGLE_OAUTH_STATE_USED: 'Questa richiesta di collegamento e\' gia\' stata usata. Riprova.',
  GOOGLE_OAUTH_STATE_INVALID: 'Richiesta di collegamento non valida. Riprova.',
  GOOGLE_MEMBERSHIP_INACTIVE: 'Solo un membro attivo dell\'agenzia puo\' collegare Google Calendar.',
  GOOGLE_NOT_CONFIGURED: 'Google Calendar non e\' configurato per questo ambiente.',
  GOOGLE_OPERATOR_REQUIRED: 'Serve un operatore autenticato per collegare Google Calendar.',
  GOOGLE_REFRESH_TOKEN_MISSING: 'Google non ha concesso un accesso permanente. Riprova e accetta il consenso.',
  GOOGLE_ID_TOKEN_MISSING: 'Risposta di Google incompleta. Riprova.',
  GOOGLE_ID_TOKEN_INVALID: 'Risposta di Google non verificabile. Riprova.',
  GOOGLE_TOKEN_EXCHANGE_FAILED: 'Impossibile completare il collegamento con Google. Riprova.',
  GOOGLE_SCOPE_INSUFFICIENT: 'Serve accettare tutte le autorizzazioni richieste da Google.',
};

function erroreGoogleInSospeso() {
  if (!window.location.search) return '';
  const parametri = new URLSearchParams(window.location.search);
  const codice = parametri.get('google_calendar_error');
  if (!codice) return '';
  parametri.delete('google_calendar_error');
  const resto = parametri.toString();
  const nuovoUrl = `${window.location.pathname}${resto ? `?${resto}` : ''}${window.location.hash}`;
  window.history.replaceState(null, '', nuovoUrl);
  return MESSAGGI_ERRORE_GOOGLE[codice] || 'Collegamento con Google Calendar non riuscito.';
}

function isCompact() {
  return typeof window.matchMedia === 'function' && window.matchMedia(COMPACT_QUERY).matches;
}

// Aggiorna l'indirizzo senza ricostruire la pagina (giorno scelto nel Mese):
// un ricaricamento ritrova lo stesso giorno, la cronologia non si allunga.
function sostituisciIndirizzo(view, giorno) {
  try {
    const h = window.history;
    if (h && typeof h.replaceState === 'function') {
      h.replaceState(null, '', `#/agenda/${VIEW_SLUGS[view]}/${giorno}`);
    }
  } catch (_e) {
    // nessuna cronologia disponibile: resta l'indirizzo di partenza.
  }
}

function due(n) {
  return String(n).padStart(2, '0');
}

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function etichettaPeriodo(view, range) {
  if (view === 'day') return formatDayLong(range.days[0]);
  if (view === 'month') return formatMonth(range.days[0]);
  return formatRange(range.days[0], range.days[range.days.length - 1]);
}

export async function renderAgenda(container, params = []) {
  let compatto = isCompact();
  // La vista chiesta e' quella mostrata; senza vista nell'indirizzo, l'ultima
  // scelta dall'utente (o la partenza di sempre).
  const chiesta = viewFromSlug(params[0]);
  const view = effectiveView(chiesta, compatto, chiesta ? null : readPreferredView());
  if (chiesta) savePreferredView(view);
  const key = isDateKey(params[1]) ? params[1] : todayKey();
  // Il giorno "scelto": nel Mese cambia toccando una data; passando a un'altra
  // vista (o creando un appuntamento) si parte da li'.
  let chiave = key;
  const range = rangeFor(view, key);
  const epoca = sessionEpoch();
  // La pagina e' viva finche' la sessione e' la stessa e il suo nodo e'
  // ancora nel documento (il router svuota il contenitore, non lo toglie).
  let pagina = null;
  const stale = () => sessionEpoch() !== epoca || !pagina || !pagina.isConnected;

  const vai = (nuovaVista, nuovaData) => navigate('agenda', [VIEW_SLUGS[nuovaVista], nuovaData]);

  container.replaceChildren();
  pagina = el('div', `agenda-page agenda-view-${view}${compatto ? ' agenda-compact' : ''}`);
  pagina.dataset.view = view;
  container.appendChild(pagina);

  // -- barra ---------------------------------------------------------------
  const barra = el('div', 'agenda-toolbar agenda-main-toolbar');
  const navigazione = el('div', 'agenda-nav');
  const indietro = el('button', 'btn agenda-prev', '◀');
  indietro.type = 'button';
  const periodo = { day: ['Giorno precedente', 'Giorno successivo'], month: ['Mese precedente', 'Mese successivo'] }[view]
    || ['Settimana precedente', 'Settimana successiva'];
  indietro.setAttribute('aria-label', periodo[0]);
  indietro.addEventListener('click', () => vai(view, shiftKey(view, key, -1)));
  const oggi = el('button', 'btn agenda-today-btn', 'Oggi');
  oggi.type = 'button';
  oggi.addEventListener('click', () => vai(view, todayKey()));
  const avanti = el('button', 'btn agenda-next', '▶');
  avanti.type = 'button';
  avanti.setAttribute('aria-label', periodo[1]);
  avanti.addEventListener('click', () => vai(view, shiftKey(view, key, 1)));
  // §12: le scorciatoie di tastiera (← → T) dichiarate sui pulsanti che imitano.
  indietro.setAttribute('aria-keyshortcuts', 'ArrowLeft');
  avanti.setAttribute('aria-keyshortcuts', 'ArrowRight');
  oggi.setAttribute('aria-keyshortcuts', 'T');
  navigazione.append(indietro, oggi, avanti);
  const titoloPeriodo = el('h2', 'agenda-period', etichettaPeriodo(view, range));
  navigazione.appendChild(titoloPeriodo);
  // §12: il fuso e' scritto, non sottinteso - Roma, qualunque sia il dispositivo.
  const fuso = el('span', 'agenda-tz muted', TIMEZONE_LABEL);
  fuso.dataset.timezone = '';
  fuso.title = 'Date e orari dell’Agenda sono sempre nel fuso di Roma, qualunque sia il fuso del dispositivo.';
  navigazione.appendChild(fuso);
  // AGENDA MOBILE: sullo schermo stretto filtri e comandi secondari stanno
  // dietro due pulsanti (visibili solo con `agenda-compact`).
  const rapidi = el('div', 'agenda-quick');
  const apriFiltri = el('button', 'btn agenda-filters-toggle', 'Filtri');
  apriFiltri.type = 'button';
  apriFiltri.setAttribute('aria-expanded', 'false');
  const apriMenu = el('button', 'btn agenda-more-toggle', '⋯');
  apriMenu.type = 'button';
  apriMenu.setAttribute('aria-label', 'Altri comandi');
  apriMenu.setAttribute('aria-haspopup', 'true');
  apriMenu.setAttribute('aria-expanded', 'false');
  rapidi.append(apriFiltri, apriMenu);
  navigazione.appendChild(rapidi);
  barra.appendChild(navigazione);

  const viste = el('div', 'agenda-views');
  viste.setAttribute('role', 'group');
  viste.setAttribute('aria-label', 'Vista');
  for (const v of VIEWS) {
    const b = el('button', `btn agenda-view-btn${v === view ? ' active' : ''}`, VIEW_LABELS[v]);
    b.type = 'button';
    b.dataset.view = v;
    b.setAttribute('aria-pressed', v === view ? 'true' : 'false');
    // Tutte le viste, su ogni dispositivo; si parte dal giorno scelto.
    b.addEventListener('click', () => vai(v, chiave));
    viste.appendChild(b);
  }
  barra.appendChild(viste);

  const comandi = el('div', 'agenda-commands');
  const aggiorna = el('button', 'btn', 'Aggiorna');
  aggiorna.type = 'button';
  const nuovo = el('button', 'btn primary', '+ Nuovo appuntamento');
  nuovo.type = 'button';
  // Orari di lavoro, eccezioni e chiusure (API A30-11): per tutti gli
  // operatori; cosa si puo' gestire lo decide il server (D6).
  const disponibilita = el('button', 'btn', 'Disponibilità');
  disponibilita.type = 'button';
  // Link di prenotazione pubblica (API A30-12): anche qui decide il server
  // chi gestisce quali link (D2).
  const linkPrenotazione = el('button', 'btn', 'Link prenotazione');
  linkPrenotazione.type = 'button';
  comandi.append(aggiorna, disponibilita, linkPrenotazione);
  // A30-7: solo su richiesta esplicita, mai al caricamento della pagina.
  const sincronizza = gestisceRichieste(getSession())
    ? el('button', 'btn', 'Aggiorna richieste dal sito') : null;
  if (sincronizza) {
    sincronizza.type = 'button';
    comandi.append(sincronizza);
  }
  // AGENDA MOBILE: sullo schermo stretto il riquadro Google Calendar non
  // occupa stabilmente la pagina; si apre da qui (solo `agenda-compact`).
  const mostraGoogle = el('button', 'btn agenda-gcal-toggle', 'Google Calendar');
  mostraGoogle.type = 'button';
  mostraGoogle.setAttribute('aria-expanded', 'false');
  comandi.append(mostraGoogle, nuovo);
  comandi.setAttribute('role', 'group');
  comandi.setAttribute('aria-label', 'Comandi');
  barra.appendChild(comandi);
  pagina.appendChild(barra);

  const alterna = (classe, pulsante) => {
    const aperto = !pagina.classList.contains(classe);
    pagina.classList.toggle(classe, aperto);
    if (pulsante) pulsante.setAttribute('aria-expanded', aperto ? 'true' : 'false');
    return aperto;
  };
  apriFiltri.addEventListener('click', () => alterna('agenda-filters-open', apriFiltri));
  apriMenu.addEventListener('click', () => alterna('agenda-menu-open', apriMenu));
  mostraGoogle.addEventListener('click', () => alterna('agenda-gcal-open', mostraGoogle));
  // Un comando scelto dal menu lo chiude; anche un tocco altrove sulla pagina.
  const chiudiMenu = () => {
    if (!pagina.classList.contains('agenda-menu-open')) return;
    pagina.classList.toggle('agenda-menu-open', false);
    apriMenu.setAttribute('aria-expanded', 'false');
  };
  comandi.addEventListener('click', chiudiMenu);
  pagina.addEventListener('click', (evento) => {
    const bersaglio = evento && evento.target;
    if (!bersaglio || bersaglio === apriMenu || comandi.contains(bersaglio)) return;
    chiudiMenu();
  });

  // -- filtri (piano A30-4 congelato: §3 AgendaFilters, §4.1, §7) ------------
  // Agente: solo a chi assegna (owner, admin, Supreme in acting), in OGNI
  // vista (AGENDA MOBILE: nella Lista lo applica il client sulle righe). Un
  // agent vede comunque solo i propri appuntamenti: per lui c'e'
  // l'interruttore dei colleghi "Occupato" nelle viste calendario.
  // Tipo e Stato in ogni vista, con lo STESSO significato: "Stati
  // predefiniti" = senza annullati e spostati, ovunque.
  const puoAssegnare = canAssignRecords(getSession());
  const griglia = view !== 'list';
  let filtri = normalizeFilters(leggiFiltri());
  const barraFiltri = el('div', 'agenda-toolbar agenda-filters');
  barraFiltri.setAttribute('role', 'group');
  barraFiltri.setAttribute('aria-label', 'Filtri');
  const campoFiltro = (etichetta, controllo) => {
    const campo = el('label', 'agenda-filter');
    campo.append(el('span', 'muted', etichetta), controllo);
    return campo;
  };
  const selezione = (nome, voci, valore) => {
    const s = el('select', 'input');
    s.dataset.filter = nome;
    for (const [v, t] of voci) {
      const o = el('option', '', t);
      o.value = v;
      if (v === valore) o.selected = true;
      s.appendChild(o);
    }
    s.value = valore;
    return s;
  };
  const selAgente = puoAssegnare
    ? selezione('agent', [['', 'Tutti gli agenti']], filtri.agent) : null;
  let spuntaColleghi = null;
  if (!puoAssegnare && griglia) {
    spuntaColleghi = el('input');
    spuntaColleghi.type = 'checkbox';
    spuntaColleghi.dataset.filter = 'colleagues';
    spuntaColleghi.checked = filtri.colleagues;
  }
  // SEMPLIFICAZIONE-CRM-1: un solo tipo di visita nei filtri (la visita immobile).
  const selTipo = selezione('type', [['', 'Tutti i tipi'],
    ...Object.entries(TYPE_LABELS).filter(([t]) => !CREATE_HIDDEN_TYPES.includes(t))], filtri.type);
  const selStato = selezione('status', [
    ['', 'Stati predefiniti'], [ALL_STATUSES_FILTER, 'Tutti gli stati'],
    ...Object.entries(STATUS_LABELS), [MISTAKES_FILTER, 'Creati per errore']], filtri.status);
  const azzera = el('button', 'btn', 'Azzera filtri');
  azzera.type = 'button';
  const aggiornaAzzera = () => {
    const attivi = activeFilterCount(filtri, { canAssign: puoAssegnare, view });
    azzera.hidden = attivi === 0;
    apriFiltri.textContent = attivi ? `Filtri (${attivi})` : 'Filtri';
  };
  if (selAgente) barraFiltri.appendChild(campoFiltro('Agente', selAgente));
  if (spuntaColleghi) {
    const campo = el('label', 'agenda-filter');
    campo.append(spuntaColleghi, el('span', '', 'Mostra gli impegni dei colleghi'));
    barraFiltri.appendChild(campo);
  }
  barraFiltri.append(campoFiltro('Tipo', selTipo), campoFiltro('Stato', selStato), azzera);
  aggiornaAzzera();
  pagina.appendChild(barraFiltri);

  // §3/§12 Legenda sempre visibile: gli stati veri, con le classi delle card.
  const legenda = el('div', 'agenda-legend');
  legenda.dataset.legend = '';
  legenda.setAttribute('aria-label', 'Legenda degli stati');
  legenda.appendChild(el('span', 'muted', 'Legenda:'));
  for (const voce of legendEntries({ withBusy: !puoAssegnare && griglia })) {
    const segno = el('span', voce.className, voce.label);
    segno.dataset.legendKey = voce.key;
    legenda.appendChild(segno);
  }
  pagina.appendChild(legenda);

  // Il filtro agente si riempie con l'elenco /agents del server (attivi,
  // di questa agenzia). Un agente che non c'e' piu' non resta scelto.
  const riempiFiltroAgenti = (lista) => {
    if (!selAgente) return;
    if (filtri.agent && !lista.some((a) => String(a.id) === filtri.agent)) {
      filtri = normalizeFilters({ ...filtri, agent: '' });
      salvaFiltri(filtri);
      aggiornaAzzera();
    }
    selAgente.replaceChildren();
    for (const [v, t] of [['', 'Tutti gli agenti'],
      ...lista.map((a) => [String(a.id), a.name || `Operatore ${a.id}`])]) {
      const o = el('option', '', t);
      o.value = v;
      if (v === filtri.agent) o.selected = true;
      selAgente.appendChild(o);
    }
    selAgente.value = filtri.agent;
  };
  const cambiaFiltri = (modifica) => {
    filtri = normalizeFilters({ ...filtri, ...modifica });
    salvaFiltri(filtri);
    aggiornaAzzera();
    carica();
  };
  if (selAgente) selAgente.addEventListener('change', () => cambiaFiltri({ agent: selAgente.value }));
  if (spuntaColleghi) {
    spuntaColleghi.addEventListener('change', () => cambiaFiltri({ colleagues: spuntaColleghi.checked }));
  }
  selTipo.addEventListener('change', () => cambiaFiltri({ type: selTipo.value }));
  selStato.addEventListener('change', () => cambiaFiltri({ status: selStato.value }));
  azzera.addEventListener('click', () => {
    if (selAgente) selAgente.value = '';
    if (spuntaColleghi) spuntaColleghi.checked = true;
    selTipo.value = '';
    selStato.value = '';
    cambiaFiltri({ ...DEFAULT_FILTERS });
  });

  // A30-9B: il pannello Google Calendar, sotto la barra e sopra l'elenco -
  // niente di nuovo nella barra laterale, e' parte dell'Agenda stessa (§27).
  const gcalContenitore = el('div', 'gcal-panel-slot');
  pagina.appendChild(gcalContenitore);
  mountCalendarSyncPanel(gcalContenitore, { isStale: stale });

  const avviso = el('div', 'agenda-notice');
  avviso.setAttribute('role', 'status');
  avviso.setAttribute('aria-live', 'polite');
  pagina.appendChild(avviso);
  const area = el('div', 'agenda-area');
  pagina.appendChild(area);

  const drawer = el('dialog', 'modal agenda-drawer');
  drawer.setAttribute('aria-modal', 'true');
  const dialogo = el('dialog', 'modal modal-wide agenda-dialog');
  dialogo.setAttribute('aria-modal', 'true');
  // AGENDA MOBILE: il "+" flottante dello schermo stretto (stesso dialog del
  // pulsante "+ Nuovo appuntamento", sul giorno scelto).
  const piu = el('button', 'agenda-fab', '+');
  piu.type = 'button';
  piu.setAttribute('aria-label', 'Nuovo appuntamento');
  piu.dataset.fab = '';
  pagina.append(drawer, dialogo, piu);

  const pannelloAperto = () => drawer.open === true || dialogo.open === true
    || (typeof pagina.querySelector === 'function' && !!pagina.querySelector('dialog[open]'));

  // AGENDA MOBILE: attraversando la soglia compatta la vista NON cambia mai.
  // Cambia solo la disposizione (classe `agenda-compact`) e l'area si
  // ridisegna con i dati gia' caricati - nessuna navigazione, nessuna
  // richiesta. Con un pannello o un dialog aperto (dettaglio, nuovo
  // appuntamento, Disponibilita', Link prenotazione, "+N") non si ridisegna
  // nulla - il pannello resta vivo coi suoi dati non salvati - e l'area si
  // adegua quando l'ultimo si chiude (`close` non risale: lo si ascolta in
  // cattura sulla pagina). Un solo listener sulla soglia per volta: quello
  // della pagina precedente si toglie quando se ne monta una nuova.
  if (smettiDiSeguireLaSoglia) smettiDiSeguireLaSoglia();
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia(COMPACT_QUERY);
    const adegua = () => {
      if (!pagina.isConnected) {
        smetti();
        return;
      }
      if (pannelloAperto()) return;
      const ora = mq.matches === true;
      if (ora === compatto) return;
      compatto = ora;
      pagina.classList.toggle('agenda-compact', ora);
      disegna();
    };
    const smetti = () => {
      if (mq.removeEventListener) mq.removeEventListener('change', adegua);
      if (smettiDiSeguireLaSoglia === smetti) smettiDiSeguireLaSoglia = null;
    };
    if (mq.addEventListener) mq.addEventListener('change', adegua);
    pagina.addEventListener('close', adegua, true);
    smettiDiSeguireLaSoglia = smetti;
  }

  let listaAgenti = [];
  // Gli ultimi elementi caricati (per ridisegnare senza richieste) e, per la
  // Lista, i giorni gia' caricati (crescono con "Mostra altri giorni").
  let ultimiItems = null;
  let giorniLista = view === 'list' ? range.days.slice() : null;

  // Piano A30-4 §1.6: vince l'ultima richiesta. Due filtri cambiati in fretta
  // fanno partire due caricamenti; una risposta superata non si disegna.
  let giroCarica = 0;
  // §10: l'ora dell'ultimo caricamento riuscito di QUESTA vista (i dati che
  // restano a schermo se il successivo fallisce).
  let caricatoAlle = null;

  async function carica(messaggio = '') {
    const giro = ++giroCarica;
    const superata = () => stale() || giro !== giroCarica;
    avviso.replaceChildren(el('span', 'muted', 'Caricamento…'));
    try {
      listaAgenti = await agenti();
      if (superata()) return;
      riempiFiltroAgenti(listaAgenti);
      let items;
      if (view === 'list') {
        giorniLista = range.days.slice();
        items = await righeLista(range.from, range.to);
      } else {
        const esito = await getCalendar({
          from: range.from, to: range.to,
          ...calendarFilterParams(filtri, { canAssign: puoAssegnare }),
        });
        items = (esito && esito.items) || [];
      }
      if (superata()) return;
      ultimiItems = items;
      disegna();
      caricatoAlle = new Date();
      avviso.replaceChildren();
      if (messaggio) avviso.appendChild(el('div', 'success-box', messaggio));
    } catch (errore) {
      if (superata()) return;
      // §10: errore leggibile + [Riprova], che ripete la STESSA richiesta
      // (vista, periodo e filtri attuali) passando dal gettone "vince l'ultima";
      // i dati gia' a schermo restano, con l'ora a cui risalgono.
      const box = el('div', 'error-box');
      box.dataset.loadError = '';
      box.appendChild(el('span', '', errorMessage(errore)));
      if (caricatoAlle) {
        box.appendChild(el('span', 'agenda-stale', ` Sono mostrati i dati delle ${loadedAtLabel(caricatoAlle)}.`));
      }
      const riprova = el('button', 'btn', 'Riprova');
      riprova.type = 'button';
      riprova.dataset.retry = '';
      riprova.addEventListener('click', () => carica(messaggio));
      box.appendChild(riprova);
      avviso.replaceChildren(box);
    }
  }

  // La Lista: le righe dell'API lista con gli STESSI stati predefiniti delle
  // altre viste, il filtro Agente applicato qui, il nome dell'agente preso
  // dall'elenco /agents del server (mai inventato).
  async function righeLista(from, to) {
    const esito = await getList({ from, to, limit: 200, ...listFilterParams(filtri) });
    const nomi = new Map(listaAgenti.map((a) => [Number(a.id), a.name]));
    const righe = ((esito && esito.items) || []).map((r) => ({
      ...r, agent_name: nomi.get(Number(r.assigned_user_id)) || r.agent_name || null,
    }));
    return filterListByAgent(righe, filtri, { canAssign: puoAssegnare });
  }

  // "Mostra altri giorni": i LIST_CHUNK_DAYS successivi, aggiunti in fondo.
  async function altriGiorni(pulsante) {
    if (!giorniLista || !giorniLista.length || pulsante.disabled) return;
    pulsante.disabled = true;
    const giro = giroCarica;
    const da = addDays(giorniLista[giorniLista.length - 1], 1);
    const altro = listRange(da, LIST_CHUNK_DAYS);
    try {
      const righe = await righeLista(altro.from, altro.to);
      if (stale() || giro !== giroCarica) return;
      const viste = new Set((ultimiItems || []).map((r) => r.id));
      ultimiItems = [...(ultimiItems || []), ...righe.filter((r) => !viste.has(r.id))];
      giorniLista = [...giorniLista, ...altro.days];
      disegna();
    } catch (errore) {
      if (stale()) return;
      pulsante.disabled = false;
      avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
    }
  }

  // Disegna l'area con gli elementi gia' caricati (anche dopo un cambio di
  // disposizione, senza nuove richieste).
  function disegna() {
    if (!ultimiItems) return;
    const items = ultimiItems;
    area.replaceChildren();
    const argomenti = {
      days: range.days,
      items,
      compact: compatto,
      onOpen: (item) => apri(item.id),
      // A30-13B.1: un click su un'ora vuota apre lo stesso dialog del
      // pulsante "+ Nuovo appuntamento", precompilato su quel giorno e ora.
      onSlotClick: (giorno, ora) => apriCreazione({ dateKey: giorno, startTime: `${due(ora)}:00` }),
    };
    if (view === 'week') {
      // SETTIMANA LEGGIBILE: un giorno si sceglie senza ricaricare e senza
      // cambiare settimana; il riepilogo sotto la griglia ne mostra gli
      // appuntamenti. "Apri giorno" porta alla vista Giorno.
      renderWeek(area, {
        ...argomenti,
        selectedKey: chiave,
        onSelect: (giorno) => { chiave = giorno; sostituisciIndirizzo('week', giorno); },
        onDay: (giorno) => vai('day', giorno),
        onCreate: (giorno) => apriCreazione({ dateKey: giorno }),
      });
    }
    else if (view === 'day') renderDay(area, argomenti);
    else if (view === 'month') {
      // AGENDA MOBILE: un tocco su una data la sceglie e mostra i suoi
      // appuntamenti sotto la griglia; "Apri giorno" porta al Giorno.
      renderMonth(area, {
        dayKey: key,
        items,
        selectedKey: chiave,
        onSelect: (giorno) => { chiave = giorno; sostituisciIndirizzo('month', giorno); },
        onOpen: (item) => apri(item.id),
        onDay: (giorno) => vai('day', giorno),
        onCreate: (giorno) => apriCreazione({ dateKey: giorno }),
        onOutside: (giorno) => vai('month', giorno),
      });
    } else {
      renderList(area, {
        ...argomenti,
        days: giorniLista || range.days,
        onMore: altriGiorni,
        moreLabel: `Mostra altri ${LIST_CHUNK_DAYS} giorni`,
      });
      const giorni = giorniLista || range.days;
      titoloPeriodo.textContent = formatRange(giorni[0], giorni[giorni.length - 1]);
    }
    misuraTestata();
  }

  // Sullo schermo stretto la testata resta in alto, SOTTO le barre fisse
  // orizzontali della Shell (la barra in alto dello smartphone, la barra del
  // titolo e quella Superadmin del desktop; mai la colonna laterale), e la
  // griglia oraria occupa il resto dello schermo: due misure in variabili
  // CSS, nient'altro.
  function misuraTestata() {
    if (!compatto || typeof requestAnimationFrame !== 'function') return;
    requestAnimationFrame(() => {
      if (stale() || !pagina.style || typeof pagina.style.setProperty !== 'function') return;
      if (typeof document.querySelector !== 'function' || typeof getComputedStyle !== 'function') return;
      let alto = 0;
      for (const selettore of ['.acting-bar', '.sidebar', '.topbar']) {
        const barraShell = document.querySelector(selettore);
        if (!barraShell || barraShell.hidden) continue;
        const stile = getComputedStyle(barraShell);
        if ((stile.position !== 'sticky' && stile.position !== 'fixed') || stile.display === 'none') continue;
        const r = barraShell.getBoundingClientRect();
        if (r.height <= 0 || r.width < window.innerWidth * 0.5) continue;
        alto = Math.max(alto, Math.ceil((parseFloat(stile.top) || 0) + r.height));
      }
      pagina.style.setProperty('--agenda-sticky-top', `${alto}px`);
      pagina.style.setProperty('--agenda-bar-h', `${Math.ceil(barra.getBoundingClientRect().height)}px`);
    });
  }

  async function apri(appointmentId) {
    await openAppointmentDrawer(drawer, {
      appointmentId,
      agents: listaAgenti,
      isStale: stale,
      onGone: () => carica(),
      onAction: (azione, detail) => {
        let fatto = false;
        dialogo.addEventListener('close', () => {
          // Chiuso senza successo (per esempio un conflitto di versione): il
          // pannello si rilegge, cosi' mostra lo stato vero.
          if (!fatto && !stale()) apri(detail.appointment.id);
        }, { once: true });
        openActionDialog(dialogo, {
          action: azione,
          detail,
          agents: listaAgenti,
          // A30-7 D5: "Apri appuntamento" verso l'altro sopralluogo aperto
          // della stessa stima. Si segna come fatto, cosi' la chiusura del
          // dialog non riapre il pannello di partenza.
          onOpenAppointment: (altroId) => {
            fatto = true;
            dialogo.close();
            if (!stale()) apri(altroId);
          },
          onDone: async (esito) => {
            fatto = true;
            if (stale()) return;
            const fissato = azione === 'schedule'
              && detail.appointment.appointment_type === 'inspection';
            // A30-8: un messaggio proprio per ogni esito, dopo il 2xx.
            await carica(fissato ? 'Sopralluogo fissato.'
              : (actionSuccessMessage(azione) || 'Operazione completata.'));
            // Dopo uno spostamento l'appuntamento "vivo" e' la riga nuova.
            const prossimo = esito && esito.id ? esito.id : detail.appointment.id;
            if (!stale()) apri(prossimo);
          },
        });
      },
    });
  }

  aggiorna.addEventListener('click', () => carica());
  if (sincronizza) {
    sincronizza.addEventListener('click', async () => {
      if (sincronizza.disabled) return;
      sincronizza.disabled = true;
      avviso.replaceChildren(el('span', 'muted', 'Aggiornamento delle richieste dal sito…'));
      try {
        const esito = await syncLegacyRequests();
        if (stale()) return;
        await carica(esitoSincronizzazione(esito));
      } catch (errore) {
        if (stale()) return;
        avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
      } finally {
        sincronizza.disabled = false;
      }
    });
  }
  // A30-13B.1: aperta sia dal pulsante "+ Nuovo appuntamento" sia da un
  // click su uno slot vuoto della griglia — UNA sola implementazione,
  // parametrizzata sul giorno e sull'ora di partenza da precompilare, cosi'
  // le due entrate restano sempre in sincronia (permessi, ricarico, salto al
  // giorno dell'appuntamento creato).
  async function apriCreazione({ dateKey: giornoIniziale, startTime } = {}) {
    try {
      listaAgenti = await agenti();
    } catch (errore) {
      avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
      return;
    }
    if (stale()) return;
    openCreateDialog(dialogo, {
      agents: listaAgenti,
      dateKey: giornoIniziale || chiave,
      startTime,
      session: getSession(),
      voice: lastVoiceStatus(),
      onDone: async (creato) => {
        if (stale()) return;
        const messaggio = confermaCreazione(creato);
        const giorno = creato && creato.start_at ? romeDateKey(creato.start_at) : null;
        if (giorno && !range.days.includes(giorno)) {
          // Nella vista attuale non si vedrebbe: si va al suo giorno.
          messaggioInSospeso = { epoch: sessionEpoch(), testo: messaggio };
          vai(view, giorno);
          return;
        }
        await carica(messaggio);
      },
    });
  }
  nuovo.addEventListener('click', () => apriCreazione());
  piu.addEventListener('click', () => apriCreazione());

  // AGENDA MOBILE: un gesto orizzontale sull'area cambia periodo (dito verso
  // sinistra = successivo). Ascoltatori passivi: lo scorrimento verticale
  // delle ore e della pagina non viene mai trattenuto, e un gesto
  // soprattutto verticale non e' mai uno swipe (`swipeDirection`).
  let tocco = null;
  area.addEventListener('touchstart', (evento) => {
    const t = evento.touches && evento.touches.length === 1 ? evento.touches[0] : null;
    tocco = t ? { x: t.clientX, y: t.clientY, t: Date.now() } : null;
  }, { passive: true });
  area.addEventListener('touchcancel', () => { tocco = null; }, { passive: true });
  area.addEventListener('touchend', (evento) => {
    const inizio = tocco;
    tocco = null;
    const t = evento.changedTouches && evento.changedTouches[0];
    if (!inizio || !t || pannelloAperto()) return;
    const verso = swipeDirection({
      dx: t.clientX - inizio.x, dy: t.clientY - inizio.y, dt: Date.now() - inizio.t,
      startX: inizio.x, width: window.innerWidth,
    });
    if (verso) vai(view, shiftKey(view, key, verso));
  }, { passive: true });

  // La linea dell'ora corrente segue l'orologio (un controllo al minuto, che
  // si ferma da solo quando la pagina non c'e' piu').
  if (view === 'day' || view === 'week') {
    const orologio = setInterval(() => {
      if (!pagina.isConnected) {
        clearInterval(orologio);
        return;
      }
      refreshNowLines(area);
    }, 60000);
    if (orologio && typeof orologio.unref === 'function') orologio.unref();
  }
  disponibilita.addEventListener('click', async () => {
    try {
      listaAgenti = await agenti();
    } catch (errore) {
      avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
      return;
    }
    if (stale()) return;
    openAvailabilityDialog(dialogo, { agents: listaAgenti, session: getSession() });
  });
  linkPrenotazione.addEventListener('click', async () => {
    try {
      listaAgenti = await agenti();
    } catch (errore) {
      avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
      return;
    }
    if (stale()) return;
    openBookingLinksDialog(dialogo, { agents: listaAgenti, session: getSession() });
  });

  // §12 tastiera: ← → T come i pulsanti della barra, solo con il fuoco sulla
  // barra (o su nessun elemento), mai in un campo in scrittura o con un
  // dialog/pannello aperto. Il listener si toglie da solo quando la pagina
  // non c'e' piu' (stesso schema del cambio mobile qui sopra).
  const inScrittura = (nodo) => !!nodo && (['INPUT', 'TEXTAREA', 'SELECT'].includes(nodo.tagName)
    || nodo.isContentEditable === true
    || (typeof nodo.closest === 'function' && !!nodo.closest('[contenteditable="true"], [contenteditable=""]')));
  const tasti = (evento) => {
    if (!pagina.isConnected) {
      document.removeEventListener('keydown', tasti);
      return;
    }
    const attivo = document.activeElement;
    const azione = shortcutAction(evento, {
      editing: inScrittura(evento.target) || inScrittura(attivo),
      dialogOpen: drawer.open === true || dialogo.open === true
        || (typeof document.querySelector === 'function' && !!document.querySelector('dialog[open]')),
      inToolbar: !attivo || attivo === document.body || attivo === document.documentElement
        || barra.contains(attivo),
    });
    if (!azione) return;
    evento.preventDefault();
    if (azione === 'prev') vai(view, shiftKey(view, key, -1));
    else if (azione === 'next') vai(view, shiftKey(view, key, 1));
    else vai(view, todayKey());
  };
  document.addEventListener('keydown', tasti);

  const erroreGoogle = erroreGoogleInSospeso();
  await carica(prendiMessaggio());
  if (erroreGoogle && !stale()) avviso.appendChild(el('div', 'error-box', erroreGoogle));

  // SCRIVANIA: `#/agenda/<vista>/<data>/<id>` apre quell'appuntamento e
  // `#/agenda/<vista>/<data>/nuovo` la creazione, con le stesse funzioni dei
  // pulsanti. L'indirizzo torna subito `#/agenda/<vista>/<data>`: un
  // ricaricamento non riapre nulla.
  const richiesta = params[2];
  if (richiesta && !stale()) {
    sostituisciIndirizzo(view, key);
    if (richiesta === 'nuovo') apriCreazione({ dateKey: key });
    else if (/^[1-9]\d*$/.test(String(richiesta))) apri(Number(richiesta));
  }
}
