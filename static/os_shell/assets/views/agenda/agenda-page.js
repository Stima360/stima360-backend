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
  MOBILE_MAX_WIDTH,
  VIEW_LABELS,
  VIEW_SLUGS,
  VIEWS,
  MOBILE_VIEWS,
  addDays,
  effectiveView,
  errorMessage,
  formatDateTime,
  formatDayLong,
  formatRange,
  isDateKey,
  rangeFor,
  romeDateKey,
  statusLabel,
  stepDays,
  todayKey,
  typeLabel,
  viewFromSlug,
} from '../../agenda/agenda-model.js';
import {
  getAgents, getCalendar, getList, syncLegacyRequests,
} from '../../agenda/agenda-api.js';
import { renderDay, renderList, renderWeek } from '../../components/agenda/agenda-views.js';
import { openAppointmentDrawer } from '../../components/agenda/agenda-drawer.js';
import { openActionDialog, openCreateDialog } from '../../components/agenda/agenda-dialogs.js';

const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH}px)`;

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

// A30-7: chi smista la coda delle richieste. Specchio della permission del
// dominio (`operator_auth.permissions.may_assign_records`: titolare,
// amministratore, platform admin dentro un'agenzia) solo per NON mostrare un
// bottone che il server rifiuterebbe; l'autorita' resta il server (403).
function gestisceRichieste(sessione) {
  if (!sessione) return false;
  if (sessione.is_platform_admin === true) {
    return sessione.acting !== null && sessione.acting !== undefined;
  }
  return sessione.role === 'agency_owner' || sessione.role === 'agency_admin';
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

function isMobile() {
  return typeof window.matchMedia === 'function' && window.matchMedia(MOBILE_QUERY).matches;
}

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function etichettaPeriodo(view, range) {
  if (view === 'day') return formatDayLong(range.days[0]);
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
  indietro.setAttribute('aria-label', view === 'day' ? 'Giorno precedente' : 'Settimana precedente');
  indietro.addEventListener('click', () => vai(view, addDays(key, -stepDays(view))));
  const oggi = el('button', 'btn', 'Oggi');
  oggi.type = 'button';
  oggi.addEventListener('click', () => vai(view, todayKey()));
  const avanti = el('button', 'btn', '▶');
  avanti.type = 'button';
  avanti.setAttribute('aria-label', view === 'day' ? 'Giorno successivo' : 'Settimana successiva');
  avanti.addEventListener('click', () => vai(view, addDays(key, stepDays(view))));
  navigazione.append(indietro, oggi, avanti);
  navigazione.appendChild(el('h2', 'agenda-period', etichettaPeriodo(view, range)));
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
  comandi.append(aggiorna);
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

  // Se la finestra passa sotto la soglia mobile con la settimana aperta, si
  // torna alla Lista: la settimana non si comprime. Il listener si toglie da
  // solo quando la pagina non c'e' piu'.
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia(MOBILE_QUERY);
    const cambio = () => {
      if (!pagina.isConnected) {
        if (mq.removeEventListener) mq.removeEventListener('change', cambio);
        return;
      }
      if (effectiveView(view, mq.matches) !== view) vai(effectiveView(view, mq.matches), key);
    };
    if (mq.addEventListener) mq.addEventListener('change', cambio);
  }

  let listaAgenti = [];

  async function carica(messaggio = '') {
    avviso.replaceChildren(el('span', 'muted', 'Caricamento…'));
    try {
      listaAgenti = await agenti();
      if (stale()) return;
      let items;
      if (view === 'list') {
        const esito = await getList({ from: range.from, to: range.to, limit: 200 });
        // La lista porta le righe, senza il nome dell'agente: lo si prende
        // dall'elenco /agents del server, mai inventato.
        const nomi = new Map(listaAgenti.map((a) => [Number(a.id), a.name]));
        items = ((esito && esito.items) || []).map((r) => ({
          ...r, agent_name: nomi.get(Number(r.assigned_user_id)) || null,
        }));
      } else {
        const esito = await getCalendar({ from: range.from, to: range.to });
        items = (esito && esito.items) || [];
      }
      if (stale()) return;
      area.replaceChildren();
      const argomenti = { days: range.days, items, onOpen: (item) => apri(item.id) };
      if (view === 'week') renderWeek(area, argomenti);
      else if (view === 'day') renderDay(area, argomenti);
      else renderList(area, argomenti);
      avviso.replaceChildren();
      if (messaggio) avviso.appendChild(el('div', 'success-box', messaggio));
    } catch (errore) {
      if (stale()) return;
      avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
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
            await carica(fissato ? 'Sopralluogo fissato.' : 'Operazione completata.');
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
  nuovo.addEventListener('click', async () => {
    try {
      listaAgenti = await agenti();
    } catch (errore) {
      avviso.replaceChildren(el('div', 'error-box', errorMessage(errore)));
      return;
    }
    if (stale()) return;
    openCreateDialog(dialogo, {
      agents: listaAgenti,
      dateKey: key,
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
  });

  await carica(prendiMessaggio());
}
