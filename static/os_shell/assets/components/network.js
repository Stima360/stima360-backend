// STIMA360 OS — components/network.js
// P27-7 — il vocabolario condiviso della sezione Rete, e nient'altro.
//
// COSA C'E' QUI
// Etichette italiane per i valori che il backend usa, traduzione degli errori
// HTTP in frasi comprensibili, e un dialogo di conferma che dice COSA cambia.
// Tre cose che le tre view della Rete userebbero altrimenti in tre copie
// leggermente diverse - ed e' proprio sugli errori che tre copie diventano tre
// comportamenti diversi davanti allo stesso 409.
//
// COSA NON C'E', DELIBERATAMENTE
// Nessuna logica di dominio. In particolare NESSUNA riproduzione del routing:
// quale agenzia riceva un lead lo decide il server (P27-6), e questa sezione
// si limita a mostrare la catena che il backend dichiara nelle sue risposte.
// Un "simulatore" in JavaScript sarebbe una seconda implementazione della
// regola, che il giorno in cui diverge mente all'amministratore.
//
// PERCHE' GLI ERRORI NON RIPETONO IL TESTO DEL SERVER
// I messaggi 4xx del backend sono curati e in italiano, ma non tutti: un 500
// puo' portare testo tecnico, e nessuno garantisce che un domani un vincolo
// del database non finisca in `detail`. La regola qui e' una sola e vale
// sempre: il testo mostrato e' SCRITTO IN QUESTO FILE, scelto in base allo
// stato HTTP e all'azione tentata. Niente stack trace, niente SQL, niente nomi
// di vincoli - non perche' li filtriamo, ma perche' non li leggiamo.

export const PLATFORM = '/api/platform';

// --- vocabolario ------------------------------------------------------------

export const AGENCY_STATUS_LABELS = {
  active: 'Attiva',
  suspended: 'Sospesa',
  archived: 'Archiviata',
};

export const AGENCY_STATUS_TONE = { active: 'ok', suspended: 'warn', archived: 'gray' };

export const OPERATOR_STATUS_LABELS = {
  invited: 'Invitato',
  active: 'Attivo',
  disabled: 'Disabilitato',
};

export const OPERATOR_STATUS_TONE = { active: 'ok', invited: 'warn', disabled: 'gray' };

export const MEMBERSHIP_STATUS_LABELS = {
  active: 'Attiva',
  suspended: 'Sospesa',
  revoked: 'Revocata',
};

export const MEMBERSHIP_STATUS_TONE = { active: 'ok', suspended: 'warn', revoked: 'gray' };

export const ROLE_LABELS = {
  agency_owner: 'Titolare',
  agency_admin: 'Amministratore agenzia',
  agent: 'Agente',
};

export const TERRITORY_KIND_LABELS = {
  province: 'Provincia',
  municipality: 'Comune',
  postal_code: 'CAP',
};

export const ASSIGNMENT_STATUS_LABELS = {
  active: 'Attiva',
  suspended: 'Sospesa',
  revoked: 'Revocata',
};

export const ASSIGNMENT_STATUS_TONE = { active: 'ok', suspended: 'warn', revoked: 'gray' };

// La sorgente alias, con il nome che un amministratore riconosce. La CHIAVE e'
// il valore che viaggia nel payload - `public_stima_comune`, l'unico che la
// 059 accetta - e l'etichetta non lo sostituisce mai: si mostra l'una e si
// invia l'altro.
export const ALIAS_SOURCE_PUBLIC_STIMA = 'public_stima_comune';
export const ALIAS_SOURCE_LABELS = {
  [ALIAS_SOURCE_PUBLIC_STIMA]: 'Comune ricevuto dal modulo Stima360',
};

export const ALIAS_STATUS_LABELS = { active: 'Attivo', revoked: 'Revocato' };
export const ALIAS_STATUS_TONE = { active: 'ok', revoked: 'gray' };

export function labelOf(map, value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  return map[value] || String(value);
}

// --- errori -----------------------------------------------------------------

// I 409 che questa sezione puo' davvero incontrare, ognuno con la frase che
// spiega PERCHE' l'operazione non e' passata. La chiave e' l'azione tentata:
// lo stesso 409 significa cose diverse a seconda di cosa si stava facendo, e
// un messaggio unico ("conflitto") non aiuterebbe nessuno a capire cosa fare.
const CONFLITTI = {
  'agenzia-crea': 'Esiste gia’ un’agenzia con questo identificativo (slug). Scegline un altro: lo slug non si puo’ cambiare dopo la creazione.',
  'operatore-crea': 'Operazione in conflitto: l’email appartiene gia’ a un operatore con una membership attiva altrove, oppure l’agenzia ha gia’ un titolare attivo. Un operatore puo’ avere una sola membership attiva.',
  'membership-aggiorna': 'Operazione in conflitto: una membership revocata non si riattiva, e il ruolo di titolare si assegna solo con il trasferimento del titolare.',
  'titolare-trasferisci': 'Trasferimento non possibile: il nuovo titolare deve avere una membership attiva in questa agenzia.',
  'territorio-crea': 'Esiste gia’ un territorio con questo tipo e questa chiave canonica.',
  'territorio-assegna': 'Il territorio ha gia’ un’assegnazione attiva. Sospendila o revocala prima di assegnarlo a un’altra agenzia, oppure usa il trasferimento.',
  'assegnazione-aggiorna': 'Operazione in conflitto: un’assegnazione revocata e’ definitiva e non si riattiva; per ridare il territorio serve una nuova assegnazione.',
  'territorio-trasferisci': 'Trasferimento non possibile: il territorio non ha un’assegnazione attiva da trasferire, oppure appartiene gia’ a quell’agenzia.',
  'alias-crea': 'Questo valore e’ gia’ dichiarato come alias attivo, per questo territorio o per un altro. Ogni valore in ingresso appartiene a un solo territorio.',
  'alias-aggiorna': 'Operazione in conflitto: un alias revocato e’ definitivo. Per usare di nuovo quel valore, dichiarane uno nuovo.',
};

const NON_TROVATO = {
  'agenzia-crea': 'Agenzia non trovata.',
  'operatore-crea': 'Agenzia od operatore non trovati.',
  'membership-aggiorna': 'Membership non trovata per questa agenzia.',
  'titolare-trasferisci': 'Agenzia od operatore non trovati.',
  'territorio-assegna': 'Agenzia o territorio non trovati.',
  'assegnazione-aggiorna': 'Assegnazione non trovata per questa agenzia.',
  'territorio-trasferisci': 'Territorio o agenzia non trovati.',
  'alias-crea': 'Territorio non trovato, o non e’ un comune: gli alias del modulo Stima360 si dichiarano solo sui comuni.',
  'alias-aggiorna': 'Alias non trovato.',
};

/**
 * La frase da mostrare per un errore, scelta da stato HTTP e azione.
 *
 * Non interpola MAI `error.message`. Il testo del server puo' essere curato -
 * spesso lo e' - ma questa funzione non ha modo di distinguere una frase
 * pensata per una persona da un messaggio tecnico, e sbagliare in quella
 * direzione significa mostrare un nome di vincolo a un amministratore.
 */
export function describeError(error, azione = '') {
  const stato = error && error.status;
  if (stato === 401) {
    return 'Sessione scaduta. Effettua di nuovo l’accesso.';
  }
  if (stato === 403) {
    return 'Questa sezione e’ riservata all’amministrazione di piattaforma.';
  }
  if (stato === 404) {
    return NON_TROVATO[azione] || 'Elemento non trovato: potrebbe essere stato modificato da un altro amministratore.';
  }
  if (stato === 409) {
    return CONFLITTI[azione] || 'Operazione in conflitto con lo stato attuale. Ricarica la pagina e riprova.';
  }
  if (stato === 422) {
    return 'Dati non validi: controlla i campi e riprova.';
  }
  if (stato === 503) {
    return 'Servizio temporaneamente non disponibile. Riprova fra poco.';
  }
  if (stato === 500) {
    return 'Errore interno del server. L’operazione non e’ stata eseguita.';
  }
  if (error && error.status === undefined) {
    // Errore di rete: `api-client.js` lo trasforma gia' in una frase per
    // l'utente, e quella non viene dal server.
    return error.message || 'Impossibile contattare il server.';
  }
  return 'Operazione non riuscita. Riprova.';
}

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export function errorBox(error, azione = '') {
  return `<div class="error-box">${escapeHtml(describeError(error, azione))}</div>`;
}

// --- conferme ---------------------------------------------------------------

/**
 * Un dialogo di conferma che SPIEGA cosa cambia.
 *
 * Non un `confirm()` generico: "Sei sicuro?" non da' nessuna informazione a
 * chi sta per revocare il territorio di un affiliato, e chi lo legge impara a
 * cliccare "Ok" senza guardare. Qui ogni conferma dichiara l'effetto e le sue
 * conseguenze, e le azioni irreversibili lo dicono.
 *
 * Ritorna una promessa che si risolve a true/false. Il dialogo viene rimosso
 * dal documento in ogni caso: restare nel DOM dopo un cambio di sessione e'
 * esattamente cio' che P26-4 ha chiuso.
 */
export function confirmAction(host, { titolo, testo, conferma = 'Conferma', tono = '' }) {
  return new Promise((resolve) => {
    const dialog = document.createElement('dialog');
    dialog.className = 'modal';
    dialog.innerHTML = `
      <h3>${escapeHtml(titolo)}</h3>
      <p>${escapeHtml(testo)}</p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-conferma="no">Annulla</button>
        <button type="button" class="btn ${tono === 'danger' ? 'danger' : 'primary'}" data-conferma="si">${escapeHtml(conferma)}</button>
      </div>
    `;
    host.appendChild(dialog);
    const chiudi = (esito) => {
      if (typeof dialog.close === 'function') dialog.close();
      dialog.remove();
      resolve(esito);
    };
    dialog.querySelectorAll('[data-conferma]').forEach((btn) => {
      btn.addEventListener('click', () => chiudi(btn.dataset.conferma === 'si'));
    });
    if (typeof dialog.showModal === 'function') dialog.showModal();
  });
}

// --- piccole utilita' di form ----------------------------------------------

export function selectOptions(map, selected) {
  return Object.entries(map)
    .map(([value, label]) => `<option value="${escapeHtml(value)}"${value === selected ? ' selected="selected"' : ''}>${escapeHtml(label)}</option>`)
    .join('');
}
