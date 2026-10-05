// STIMA360 OS — views/agenda/agenda-page.js (A30-4)
//
// La pagina Agenda, `#/agenda[/<vista>/<AAAA-MM-GG>]`. Raggiungibile SOLO per
// indirizzo diretto durante il gate A30-4: nessuna voce nella barra laterale.
//
//   desktop  SETTIMANA (lunedi' -> domenica), piu' Giorno e Lista
//   tablet   SETTIMANA completa, con scorrimento orizzontale se serve
//   mobile   LISTA e GIORNO: la settimana non si comprime
//
// Nessun Mese, nessun trascinamento, nessun ridimensionamento, nessun Google.
// I dati vengono SOLO da /api/appointments (agenda/agenda-api.js).
//
// Ogni caricamento annota `sessionEpoch()` e la rotta: una risposta arrivata
// dopo un cambio di sessione o di pagina viene buttata, come nel router.

import { getSession, sessionEpoch } from '../../core/auth.js';
import { navigate } from '../../core/router.js';
import {
  ALL_STATUSES_FILTER,
  MISTAKES_FILTER,
  DEFAULT_FILTERS,
  MOBILE_MAX_WIDTH,
  STATUS_LABELS,
  TYPE_LABELS,
  VIEW_LABELS,
  VIEW_SLUGS,
  TIMEZONE_LABEL,
  VIEWS,
  MOBILE_VIEWS,
  actionSuccessMessage,
  activeFilterCount,
  calendarFilterParams,
  canAssignRecords,
  effectiveView,
  errorMessage,
  formatDateTime,
  formatDayLong,
  formatMonth,
  formatRange,
  isDateKey,
  legendEntries,
  listFilterParams,
  loadedAtLabel,
  normalizeFilters,
  rangeFor,
  romeDateKey,
  shiftKey,
  shortcutAction,
  statusLabel,
  todayKey,
  typeLabel,
  viewFromSlug,
} from '../../agenda/agenda-model.js';
import {
  getAgents, getCalendar, getList, syncLegacyRequests,
} from '../../agenda/agenda-api.js';
import { renderDay, renderList, renderMonth, renderWeek } from '../../components/agenda/agenda-views.js';
import { openAppointmentDrawer } from '../../components/agenda/agenda-drawer.js';
import {
  openActionDialog, openAvailabilityDialog, openBookingLinksDialog, openCreateDialog,
} from '../../components/agenda/agenda-dialogs.js';
import { mountCalendarSyncPanel } from '../../components/agenda/agenda-calendar-sync-panel.js';

const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH}px)`;

// Toglie il listener sulla soglia mobile della pagina Agenda montata per
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

function isMobile() {
  return typeof window.matchMedia === 'function' && window.matchMedia(MOBILE_QUERY).matches;
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
  const mobile = isMobile();
  const view = effectiveView(viewFromSlug(params[0]), mobile);
  const key = isDateKey(params[1]) ? params[1] : todayKey();
  const range = rangeFor(view, key);
  const epoca = sessionEpoch();
  // La pagina e' viva finche' la sessione e' la stessa e il suo nodo e'
  // ancora nel documento (il router svuota il contenitore, non lo toglie).
  let pagina = null;
  const stale = () => sessionEpoch() !== epoca || !pagina || !pagina.isConnected;

  const vai = (nuovaVista, nuovaData) => navigate('agenda', [VIEW_SLUGS[nuovaVista], nuovaData]);

  container.replaceChildren();
  pagina = el('div', `agenda-page agenda-view-${view}`);
  container.appendChild(pagina);

  // -- barra ---------------------------------------------------------------
  const barra = el('div', 'agenda-toolbar');
  const navigazione = el('div', 'agenda-nav');
  const indietro = el('button', 'btn', '◀');
  indietro.type = 'button';
  const periodo = { day: ['Giorno precedente', 'Giorno successivo'], month: ['Mese precedente', 'Mese successivo'] }[view]
    || ['Settimana precedente', 'Settimana successiva'];
  indietro.setAttribute('aria-label', periodo[0]);
  indietro.addEventListener('click', () => vai(view, shiftKey(view, key, -1)));
  const oggi = el('button', 'btn', 'Oggi');
  oggi.type = 'button';
  oggi.addEventListener('click', () => vai(view, todayKey()));
  const avanti = el('button', 'btn', '▶');
  avanti.type = 'button';
  avanti.setAttribute('aria-label', periodo[1]);
  avanti.addEventListener('click', () => vai(view, shiftKey(view, key, 1)));
  // §12: le scorciatoie di tastiera (← → T) dichiarate sui pulsanti che imitano.
  indietro.setAttribute('aria-keyshortcuts', 'ArrowLeft');
  avanti.setAttribute('aria-keyshortcuts', 'ArrowRight');
  oggi.setAttribute('aria-keyshortcuts', 'T');
  navigazione.append(indietro, oggi, avanti);
  navigazione.appendChild(el('h2', 'agenda-period', etichettaPeriodo(view, range)));
  // §12: il fuso e' scritto, non sottinteso - Roma, qualunque sia il dispositivo.
  const fuso = el('span', 'agenda-tz muted', TIMEZONE_LABEL);
  fuso.dataset.timezone = '';
  fuso.title = 'Date e orari dell’Agenda sono sempre nel fuso di Roma, qualunque sia il fuso del dispositivo.';
  navigazione.appendChild(fuso);
  barra.appendChild(navigazione);

  const viste = el('div', 'agenda-views');
  viste.setAttribute('role', 'group');
  viste.setAttribute('aria-label', 'Vista');
  for (const v of VIEWS) {
    const b = el('button', `btn agenda-view-btn${v === view ? ' active' : ''}`, VIEW_LABELS[v]);
    b.type = 'button';
    b.dataset.view = v;
    b.setAttribute('aria-pressed', v === view ? 'true' : 'false');
    // La settimana non si offre su smartphone.
    if (!MOBILE_VIEWS.includes(v)) b.classList.add('agenda-desktop-only');
    b.addEventListener('click', () => vai(v, key));
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
  comandi.append(nuovo);
  barra.appendChild(comandi);
  pagina.appendChild(barra);

  // -- filtri (piano A30-4 congelato: §3 AgendaFilters, §4.1, §7) ------------
  // Agente: solo a chi assegna (owner, admin, Supreme in acting) e solo dove
  // l'API lo accetta (Settimana, Giorno). Un agent vede comunque solo i propri
  // appuntamenti: per lui c'e' l'interruttore dei colleghi "Occupato".
  // Tipo e Stato in ogni vista. "Stati predefiniti" non manda nulla: ogni
  // vista tiene il default del server.
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
  const selAgente = puoAssegnare && griglia
    ? selezione('agent', [['', 'Tutti gli agenti']], filtri.agent) : null;
  let spuntaColleghi = null;
  if (!puoAssegnare && griglia) {
    spuntaColleghi = el('input');
    spuntaColleghi.type = 'checkbox';
    spuntaColleghi.dataset.filter = 'colleagues';
    spuntaColleghi.checked = filtri.colleagues;
  }
  const selTipo = selezione('type', [['', 'Tutti i tipi'], ...Object.entries(TYPE_LABELS)], filtri.type);
  const selStato = selezione('status', [
    ['', 'Stati predefiniti'], [ALL_STATUSES_FILTER, 'Tutti gli stati'],
    ...Object.entries(STATUS_LABELS), [MISTAKES_FILTER, 'Creati per errore']], filtri.status);
  const azzera = el('button', 'btn', 'Azzera filtri');
  azzera.type = 'button';
  const aggiornaAzzera = () => {
    azzera.hidden = activeFilterCount(filtri, { canAssign: puoAssegnare, view }) === 0;
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
  pagina.append(drawer, dialogo);

  // Se la finestra passa sotto la soglia mobile con la settimana (o il mese)
  // aperta, si torna alla Lista: la settimana non si comprime. Ma tornare alla
  // Lista ricostruisce la pagina, e con lei i dialog: con un pannello o un
  // dialog aperto (dettaglio, nuovo appuntamento, Disponibilita', Link
  // prenotazione, "+N") non si ricostruisce nulla - il pannello resta vivo coi
  // suoi dati non salvati - e la vista si adegua quando l'ultimo si chiude
  // (`close` non risale: lo si ascolta in cattura sulla pagina). Un solo
  // listener sulla soglia per volta: quello della pagina precedente si toglie
  // quando se ne monta una nuova.
  if (smettiDiSeguireLaSoglia) smettiDiSeguireLaSoglia();
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia(MOBILE_QUERY);
    const pannelloAperto = () => drawer.open === true || dialogo.open === true
      || (typeof pagina.querySelector === 'function' && !!pagina.querySelector('dialog[open]'));
    const adegua = () => {
      if (!pagina.isConnected) {
        smetti();
        return;
      }
      if (pannelloAperto()) return;
      const giusta = effectiveView(view, mq.matches);
      if (giusta !== view) vai(giusta, key);
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
        const esito = await getList({
          from: range.from, to: range.to, limit: 200, ...listFilterParams(filtri),
        });
        // La lista porta le righe, senza il nome dell'agente: lo si prende
        // dall'elenco /agents del server, mai inventato.
        const nomi = new Map(listaAgenti.map((a) => [Number(a.id), a.name]));
        items = ((esito && esito.items) || []).map((r) => ({
          ...r, agent_name: nomi.get(Number(r.assigned_user_id)) || null,
        }));
      } else {
        const esito = await getCalendar({
          from: range.from, to: range.to,
          ...calendarFilterParams(filtri, { canAssign: puoAssegnare }),
        });
        items = (esito && esito.items) || [];
      }
      if (superata()) return;
      area.replaceChildren();
      const argomenti = {
        days: range.days,
        items,
        onOpen: (item) => apri(item.id),
        // A30-13B.1: un click su un'ora vuota apre lo stesso dialog del
        // pulsante "+ Nuovo appuntamento", precompilato su quel giorno e ora.
        onSlotClick: (giorno, ora) => apriCreazione({ dateKey: giorno, startTime: `${due(ora)}:00` }),
      };
      if (view === 'week') renderWeek(area, argomenti);
      else if (view === 'day') renderDay(area, argomenti);
      // Piano §4.3: una cella del Mese porta al Giorno, niente altro.
      else if (view === 'month') renderMonth(area, { dayKey: key, items, onDay: (giorno) => vai('day', giorno) });
      else renderList(area, argomenti);
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
      dateKey: giornoIniziale || key,
      startTime,
      session: getSession(),
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
}
