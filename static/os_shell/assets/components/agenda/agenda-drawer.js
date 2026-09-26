// STIMA360 OS — components/agenda/agenda-drawer.js (A30-4)
//
// Il pannello di un appuntamento: si apre sopra la pagina (un <dialog>, cosi'
// Esc chiude e il focus resta dentro), legge `GET /{id}` e la cronologia
// `GET /{id}/events`.
//
// COSA MOSTRA, E COSA NO
//
//   * i dati della riga e i riepiloghi collegati che il SERVER ha incluso
//     (contatto, immobile, stima, lead, agente) - niente di ricostruito;
//   * i pulsanti delle sole azioni in `allowed_actions`: la macchina a stati
//     e i permessi li decide il backend;
//   * i collegamenti in `allowed_links` verso pagine che la OS Shell ha davvero
//     (#/contatti/<id>, #/immobili/<id>). Stima e lead restano testo in sola
//     lettura: nessun "Apri stima", nessun "Apri lead" (D9 rev. 2) - non
//     esistono pagine a cui portare.

import {
  ACTION_LABELS,
  ACTION_ORDER,
  EVENT_LABELS,
  availableFrom,
  durationMinutes,
  errorMessage,
  formatDateTime,
  formatDuration,
  formatTime,
  outcomeNote,
  statusLabel,
  typeLabel,
} from '../../agenda/agenda-model.js';
import { getAppointment, getEvents } from '../../agenda/agenda-api.js';

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function voce(etichetta, valore) {
  if (valore === undefined || valore === null || valore === '') return null;
  const riga = el('div', 'detail-item');
  riga.appendChild(el('label', '', etichetta));
  riga.appendChild(el('div', '', valore));
  return riga;
}

function aggiungi(padre, ...figli) {
  for (const f of figli) if (f) padre.appendChild(f);
}

/** I collegamenti ammessi dal server verso pagine che esistono davvero. */
const COLLEGAMENTI = Object.freeze({
  contact: { etichetta: 'Apri cliente', rotta: (d) => `#/contatti/${Number(d.contact.id)}` },
  property: { etichetta: 'Apri immobile', rotta: (d) => `#/immobili/${Number(d.property.id)}` },
});

function riepilogoImmobile(p) {
  if (!p) return null;
  const indirizzo = [p.address, p.civic_number].filter(Boolean).join(' ');
  return [p.title, indirizzo, p.city].filter(Boolean).join(' · ');
}

function riepilogoStima(s) {
  if (!s) return null;
  const luogo = [s.via, s.civico].filter(Boolean).join(' ');
  return [`#${s.id}`, [luogo, s.comune].filter(Boolean).join(', '), s.tipologia,
    s.mq ? `${s.mq} m²` : ''].filter(Boolean).join(' · ');
}

function riepilogoLead(l) {
  if (!l) return null;
  return [`#${l.id}`, l.pipeline ? String(l.pipeline).toUpperCase() : '', l.stage, l.status]
    .filter(Boolean).join(' · ');
}

function descriviEvento(evento, nomi) {
  const tipo = EVENT_LABELS[evento.event_type] || evento.event_type;
  const passaggio = evento.from_status && evento.to_status && evento.from_status !== evento.to_status
    ? `${statusLabel(evento.from_status)} → ${statusLabel(evento.to_status)}`
    : statusLabel(evento.to_status);
  const chi = evento.actor_user_id === null || evento.actor_user_id === undefined
    ? 'Sistema'
    : (nomi.get(Number(evento.actor_user_id)) || `Operatore #${evento.actor_user_id}`);
  return { tipo, passaggio, chi, quando: evento.occurred_at ? formatDateTime(evento.occurred_at) : '' };
}

async function caricaCronologia(sezione, appointmentId, nomi, isStale, onEventi) {
  const corpo = sezione.querySelector('[data-history-body]');
  corpo.replaceChildren(el('p', 'muted', 'Caricamento cronologia…'));
  try {
    const esito = await getEvents(appointmentId);
    if (isStale()) return;
    const eventi = (esito && esito.items) || [];
    // A30-8: la nota di esito vive nell'evento: si legge da QUESTI eventi,
    // senza un'altra richiesta.
    if (onEventi) onEventi(eventi);
    corpo.replaceChildren();
    if (!eventi.length) {
      corpo.appendChild(el('p', 'muted', 'Nessun evento registrato.'));
      return;
    }
    const lista = el('ol', 'agenda-history');
    for (const evento of eventi) {
      const d = descriviEvento(evento, nomi);
      const riga = el('li');
      riga.appendChild(el('strong', '', d.tipo));
      if (d.passaggio) riga.appendChild(el('span', '', ` · ${d.passaggio}`));
      riga.appendChild(el('div', 'muted', [d.quando, d.chi].filter(Boolean).join(' · ')));
      lista.appendChild(riga);
    }
    corpo.appendChild(lista);
  } catch (errore) {
    if (isStale()) return;
    corpo.replaceChildren(el('div', 'error-box', errorMessage(errore)));
  }
}

/**
 * Apre il pannello dell'appuntamento `appointmentId` in `drawerEl`.
 * `onAction(azione, detail)` apre il dialog dell'azione; `agents` serve solo a
 * dare un nome agli autori degli eventi (dati del server, /agents).
 */
export async function openAppointmentDrawer(drawerEl, {
  appointmentId, agents, onAction, isStale = () => false, onGone,
}) {
  drawerEl.replaceChildren();
  const pannello = el('div', 'agenda-drawer-body');
  const chiudi = el('button', 'btn ghost agenda-drawer-close', 'Chiudi');
  chiudi.type = 'button';
  chiudi.setAttribute('aria-label', 'Chiudi il pannello');
  chiudi.addEventListener('click', () => drawerEl.close());
  drawerEl.appendChild(chiudi);
  drawerEl.appendChild(pannello);
  pannello.appendChild(el('p', 'muted', 'Caricamento appuntamento…'));
  if (!drawerEl.open) drawerEl.showModal();

  let detail;
  try {
    detail = await getAppointment(appointmentId);
  } catch (errore) {
    if (isStale()) return null;
    pannello.replaceChildren(el('div', 'error-box', errorMessage(errore)));
    if (errore && errore.status === 404 && onGone) onGone();
    return null;
  }
  if (isStale()) return null;

  const riga = detail.appointment;
  pannello.replaceChildren();
  drawerEl.setAttribute('aria-label',
    `${typeLabel(riga.appointment_type) || 'Appuntamento'} ${formatDateTime(riga.start_at)}`);

  const testata = el('header', 'agenda-drawer-head');
  testata.appendChild(el('h2', '', typeLabel(riga.appointment_type) || 'Appuntamento'));
  const stato = statusLabel(riga.status);
  if (stato) testata.appendChild(el('span', `agenda-badge agenda-badge-${riga.status}`, stato));
  if (riga.source === 'a30_test') testata.appendChild(el('span', 'agenda-badge agenda-badge-test', 'TEST'));
  // A30-7: una richiesta arrivata dal form pubblico del sito (import A30-6).
  const dalSito = riga.source === 'legacy_stime_dettagliate';
  if (dalSito) testata.appendChild(el('span', 'agenda-badge agenda-badge-site', 'Richiesta dal sito'));
  pannello.appendChild(testata);

  // Finche' e' una richiesta, l'orario e' la PREFERENZA del cliente, non un
  // appuntamento fissato: si dice cosi'. Dopo "Pianifica" e' l'orario vero.
  const preferenza = dalSito && riga.status === 'requested';
  const dati = el('div', 'detail-grid agenda-drawer-grid');
  aggiungi(dati,
    preferenza
      ? voce('Preferenza cliente', formatDateTime(riga.start_at))
      : voce('Quando', `${formatDateTime(riga.start_at)}–${formatTime(riga.end_at)}`),
    voce('Durata', formatDuration(durationMinutes(riga.start_at, riga.end_at))),
    voce('Agente', detail.agent ? detail.agent.name : (riga.assigned_user_id ? '' : 'Nessuno (richiesta)')),
    voce('Cliente', detail.contact ? detail.contact.display_name : null),
    voce('Telefono', detail.contact ? detail.contact.phone : null),
    voce('Email', detail.contact ? detail.contact.email : null),
    voce('Immobile', riepilogoImmobile(detail.property)),
    voce('Stima (sola lettura)', riepilogoStima(detail.stima)),
    voce('Lead (sola lettura)', riepilogoLead(detail.lead)),
    voce('Luogo', riga.location_text),
    voce('Note', riga.notes),
    voce('Confermato il', riga.confirmed_at ? formatDateTime(riga.confirmed_at) : null),
    voce('Completato il', riga.completed_at ? formatDateTime(riga.completed_at) : null),
    voce('Non presentato il', riga.no_show_at ? formatDateTime(riga.no_show_at) : null),
    voce('Annullato il', riga.cancelled_at ? formatDateTime(riga.cancelled_at) : null),
    voce('Motivo annullamento', riga.cancelled_reason),
  );
  pannello.appendChild(dati);

  const collegamenti = (detail.allowed_links || [])
    .filter((chiave) => COLLEGAMENTI[chiave] && detail[chiave] && detail[chiave].id);
  if (collegamenti.length) {
    const barra = el('div', 'action-bar agenda-drawer-links');
    for (const chiave of collegamenti) {
      const link = el('a', 'btn', COLLEGAMENTI[chiave].etichetta);
      link.href = COLLEGAMENTI[chiave].rotta(detail);
      link.addEventListener('click', () => drawerEl.close());
      barra.appendChild(link);
    }
    pannello.appendChild(barra);
  }

  const ammesse = ACTION_ORDER.filter((a) => (detail.allowed_actions || []).includes(a));
  const azioni = el('div', 'action-bar agenda-drawer-actions');
  if (!ammesse.length) {
    azioni.appendChild(el('p', 'muted', 'Nessuna azione disponibile per questo appuntamento.'));
  }
  for (const azione of ammesse) {
    const pulsante = el('button', `btn${azione === 'cancel' || azione === 'no_show' ? ' danger' : ''}`,
      ACTION_LABELS[azione]);
    pulsante.type = 'button';
    pulsante.dataset.action = azione;
    pulsante.addEventListener('click', () => onAction(azione, detail));
    azioni.appendChild(pulsante);
  }
  // A30-8: un esito non ancora registrabile NON e' un pulsante eseguibile.
  // Chi puo' agire sull'appuntamento (il server gli offre "Annulla") vede da
  // quando lo sara'; l'orario e' solo indicativo, decide il server.
  if (ammesse.includes('cancel')) {
    for (const azione of ['complete', 'no_show']) {
      if (ammesse.includes(azione)) continue;
      const da = availableFrom(riga, azione);
      if (!da) continue;
      const inAttesa = el('button', 'btn', ACTION_LABELS[azione]);
      inAttesa.type = 'button';
      inAttesa.disabled = true;
      inAttesa.dataset.pendingAction = azione;
      inAttesa.title = `Disponibile dal ${formatDateTime(da)}`;
      azioni.appendChild(inAttesa);
      azioni.appendChild(el('span', 'muted agenda-available-from',
        `${ACTION_LABELS[azione]}: disponibile dal ${formatDateTime(da)}`));
    }
  }
  pannello.appendChild(azioni);

  const cronologia = el('section', 'agenda-drawer-history');
  cronologia.appendChild(el('h3', 'section-title', 'Cronologia'));
  const corpo = el('div');
  corpo.dataset.historyBody = '';
  cronologia.appendChild(corpo);
  pannello.appendChild(cronologia);
  const nomi = new Map((agents || []).map((a) => [Number(a.id), a.name]));
  caricaCronologia(cronologia, riga.id, nomi, isStale, (eventi) => {
    const nota = outcomeNote(eventi);
    if (nota) aggiungi(dati, voce('Nota esito', nota));
  });

  return detail;
}
