// STIMA360 OS — components/agenda/agenda-calendar-sync-panel.js (A30-9B)
//
// Il pannello minimale "Google Calendar" nella pagina Agenda: NON una nuova
// pagina Impostazioni, un riquadro dentro la Agenda stessa (§27).
//
// Stati mostrati (§28), uno solo alla volta, mai un'inferenza sul client -
// tutti letti da `GET /status`:
//
//   Non configurato        configured=false
//   Non collegato           connection_status='not_connected'
//   Collegato                connection_status='connected'
//   Richiede nuovo accesso   connection_status='needs_reauth'
//   Errore                   ultima richiesta finita in errore di rete/server
//
// "Sincronizzazione in corso" non e' un quinto stato del server: e' il
// pannello stesso, mentre aspetta la risposta di una delle sue azioni.
//
// Azioni (§29): Collega Google (POST /connect, poi SOLO
// `window.location.assign(authorization_url)` - mai un URL costruito qui),
// Ricollega (la stessa azione, quando needs_reauth), Riprova sincronizzazione
// (POST /resync), Disconnetti (conferma nativa, poi POST /disconnect).
//
// COSA NON MOSTRA MAI: nessun badge Google sulle singole card
// dell'appuntamento (il progetto e badge per badge, agenda-model.js/
// agenda-views.js non cambiano), e nessun `provider_subject` (l'account
// Google collegato non e' mai scritto in pagina - solo lo STATO della
// connessione).

import {
  connectGoogleCalendar,
  disconnectGoogleCalendar,
  getGoogleCalendarStatus,
  resyncGoogleCalendar,
} from '../../agenda/calendar-sync-api.js';
import { errorMessage } from '../../agenda/agenda-model.js';

const ETICHETTA_STATO = {
  not_configured: 'Non configurato',
  not_connected: 'Non collegato',
  connected: 'Collegato',
  needs_reauth: 'Richiede nuovo accesso',
  disconnected: 'Non collegato',
};

const CLASSE_STATO = {
  not_configured: 'gcal-status-muted',
  not_connected: 'gcal-status-muted',
  connected: 'gcal-status-ok',
  needs_reauth: 'gcal-status-warn',
  disconnected: 'gcal-status-muted',
};

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function contatori(stato) {
  const pendenti = Number(stato.pending_sync_count) || 0;
  const falliti = Number(stato.failed_sync_count) || 0;
  if (!pendenti && !falliti) return '';
  const parti = [];
  if (pendenti) parti.push(`${pendenti} in coda`);
  if (falliti) parti.push(`${falliti} da riprovare`);
  return parti.join(' · ');
}

/** Monta il pannello in `container` (un nodo vuoto, es. `<div>`) e lo carica.
 *  `isStale()` come nel resto dell'Agenda: nessun aggiornamento dopo un
 *  cambio di pagina o di sessione. */
export function mountCalendarSyncPanel(container, { isStale } = { isStale: () => false }) {
  const staleCheck = typeof isStale === 'function' ? isStale : () => false;
  const pannello = el('div', 'gcal-panel');
  const riga = el('div', 'gcal-panel-row');
  const etichetta = el('span', 'gcal-panel-label', 'Google Calendar');
  const badge = el('span', 'gcal-badge-stato', '…');
  const dettaglio = el('span', 'gcal-panel-detail muted', '');
  const azioni = el('div', 'gcal-panel-actions');
  riga.append(etichetta, badge, dettaglio);
  pannello.append(riga, azioni);
  container.replaceChildren(pannello);

  function bottone(testo, gestore, classe = 'btn') {
    const b = el('button', classe, testo);
    b.type = 'button';
    b.addEventListener('click', gestore);
    return b;
  }

  function avviso(messaggio, tipo = 'error') {
    dettaglio.className = `gcal-panel-detail ${tipo === 'error' ? 'gcal-panel-error' : 'muted'}`;
    dettaglio.textContent = messaggio;
  }

  async function carica() {
    badge.textContent = '…';
    badge.className = 'gcal-badge-stato gcal-status-muted';
    azioni.replaceChildren();
    dettaglio.className = 'gcal-panel-detail muted';
    dettaglio.textContent = '';
    let stato;
    try {
      stato = await getGoogleCalendarStatus();
    } catch (errore) {
      if (staleCheck()) return;
      badge.textContent = 'Errore';
      badge.className = 'gcal-badge-stato gcal-status-warn';
      avviso(errorMessage(errore));
      azioni.appendChild(bottone('Riprova', () => carica()));
      return;
    }
    if (staleCheck()) return;
    if (!stato || stato.enabled !== true || stato.configured !== true) {
      badge.textContent = ETICHETTA_STATO.not_configured;
      badge.className = `gcal-badge-stato ${CLASSE_STATO.not_configured}`;
      // (§28) non configurato: nessun'azione da offrire, non c'e' nulla che
      // l'operatore possa fare per cambiarlo dall'Agenda.
      return;
    }
    const connectionStatus = stato.connection_status || 'not_connected';
    badge.textContent = ETICHETTA_STATO[connectionStatus] || connectionStatus;
    badge.className = `gcal-badge-stato ${CLASSE_STATO[connectionStatus] || 'gcal-status-muted'}`;
    dettaglio.textContent = contatori(stato);

    async function collega(pulsante) {
      pulsante.disabled = true;
      avviso('', 'muted');
      try {
        const esito = await connectGoogleCalendar();
        if (staleCheck() || !esito || typeof esito.authorization_url !== 'string') return;
        // (§29) SEMPRE l'URL del server: mai una costruzione lato client.
        window.location.assign(esito.authorization_url);
      } catch (errore) {
        if (staleCheck()) return;
        avviso(errorMessage(errore));
        pulsante.disabled = false;
      }
    }

    if (connectionStatus === 'not_connected' || connectionStatus === 'disconnected') {
      azioni.appendChild(bottone('Collega Google', (e) => collega(e.target), 'btn primary'));
    } else if (connectionStatus === 'needs_reauth') {
      azioni.appendChild(bottone('Ricollega', (e) => collega(e.target), 'btn primary'));
    } else if (connectionStatus === 'connected') {
      const riprova = bottone('Riprova sincronizzazione', async (e) => {
        const pulsante = e.target;
        pulsante.disabled = true;
        avviso('Sincronizzazione in corso…', 'muted');
        try {
          const esito = await resyncGoogleCalendar();
          if (staleCheck()) return;
          const n = Number(esito && esito.requeued) || 0;
          avviso(n ? `${n} appuntamenti rimessi in coda.` : 'Nessun appuntamento da rimettere in coda.', 'muted');
        } catch (errore) {
          if (staleCheck()) return;
          avviso(errorMessage(errore));
        } finally {
          if (!staleCheck()) pulsante.disabled = false;
        }
      });
      const disconnetti = bottone('Disconnetti', async (e) => {
        if (!window.confirm('Disconnettere questo account Google Calendar?')) return;
        const pulsante = e.target;
        pulsante.disabled = true;
        try {
          await disconnectGoogleCalendar();
          if (staleCheck()) return;
          await carica();
        } catch (errore) {
          if (staleCheck()) return;
          avviso(errorMessage(errore));
          pulsante.disabled = false;
        }
      });
      azioni.append(riprova, disconnetti);
    }
  }

  carica();
  return { refresh: carica };
}
