// STIMA360 OS — components/agenda/agenda-views.js (A30-4)
//
// Le tre viste dell'Agenda: SETTIMANA (desktop e tablet), GIORNO e LISTA
// (smartphone, disponibili anche su desktop). Nessun Mese, nessun
// trascinamento, nessun ridimensionamento: un blocco si apre con un click (o
// Invio) e le modifiche passano dai dialog.
//
// Tutto e' costruito con le API del DOM e `textContent`: nessun dato arrivato
// dal server finisce in `innerHTML`.
//
// Si mostra SOLO cio' che il server restituisce. Un impegno di un collega
// arriva come `kind: "busy"` con orario, nome dell'agente e "Occupato", e cosi'
// si disegna: niente cliente, niente tipo, niente click verso un dettaglio che
// il server rifiuterebbe.

import {
  HOUR_HEIGHT,
  blockGeometry,
  durationMinutes,
  formatDayLong,
  formatDayShort,
  formatDuration,
  formatTime,
  initialScrollTop,
  itemTitle,
  itemsForDay,
  overlapLayout,
  statusLabel,
  todayKey,
  typeLabel,
} from '../../agenda/agenda-model.js';

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function isBusy(item) {
  return item && item.kind === 'busy';
}

/** L'etichetta completa per chi usa uno screen reader. */
export function itemAriaLabel(item) {
  const orario = `${formatTime(item.start_at)}–${formatTime(item.end_at)}`;
  if (isBusy(item)) {
    return [item.label || 'Occupato', item.agent_name, orario].filter(Boolean).join(', ');
  }
  return [typeLabel(item.type || item.appointment_type), statusLabel(item.status), orario,
    itemTitle(item), item.agent_name ? `agente ${item.agent_name}` : '']
    .filter(Boolean).join(', ');
}

/**
 * La card di un appuntamento: ora, tipo, titolo, stato, agente, durata -
 * ciascuno solo se il server lo ha dato.
 */
export function renderCard(item, { onOpen, compact = false } = {}) {
  const busy = isBusy(item);
  const stato = busy ? 'busy' : (item.status || '');
  const card = el(busy ? 'div' : 'button',
    `agenda-card agenda-status-${stato}${compact ? ' agenda-card-compact' : ''}`);
  card.setAttribute('aria-label', itemAriaLabel(item));
  if (!busy) {
    card.type = 'button';
    card.dataset.appointmentId = String(item.id);
    if (onOpen) card.addEventListener('click', () => onOpen(item));
  }

  const minuti = durationMinutes(item.start_at, item.end_at);
  const testa = el('div', 'agenda-card-head');
  testa.appendChild(el('span', 'agenda-card-time',
    `${formatTime(item.start_at)}–${formatTime(item.end_at)}`));
  const etichetta = busy ? '' : statusLabel(item.status);
  if (etichetta) testa.appendChild(el('span', `agenda-badge agenda-badge-${item.status}`, etichetta));
  card.appendChild(testa);
  if (busy) {
    card.appendChild(el('div', 'agenda-card-type', item.label || 'Occupato'));
    if (item.agent_name) card.appendChild(el('div', 'agenda-card-agent', item.agent_name));
    return card;
  }
  const tipo = typeLabel(item.type || item.appointment_type);
  if (tipo) card.appendChild(el('div', 'agenda-card-type', tipo));
  const titolo = itemTitle(item);
  if (titolo) card.appendChild(el('div', 'agenda-card-title', titolo));
  const riga = el('div', 'agenda-card-meta');
  if (item.agent_name) riga.appendChild(el('span', 'agenda-card-agent', item.agent_name));
  const durata = formatDuration(minuti);
  if (durata) riga.appendChild(el('span', 'agenda-card-duration', durata));
  if (item.is_test) riga.appendChild(el('span', 'agenda-badge agenda-badge-test', 'TEST'));
  if (riga.childNodes.length) card.appendChild(riga);
  return card;
}

function colonnaOre() {
  const colonna = el('div', 'agenda-hours');
  for (let h = 0; h < 24; h += 1) {
    const cella = el('div', 'agenda-hour-label', `${String(h).padStart(2, '0')}:00`);
    cella.style.height = `${HOUR_HEIGHT}px`;
    colonna.appendChild(cella);
  }
  return colonna;
}

function colonnaGiorno(key, items, onOpen) {
  const colonna = el('div', 'agenda-day-column');
  colonna.dataset.day = key;
  colonna.style.height = `${24 * HOUR_HEIGHT}px`;
  for (let h = 0; h < 24; h += 1) {
    const riga = el('div', 'agenda-hour-slot');
    riga.style.top = `${h * HOUR_HEIGHT}px`;
    riga.style.height = `${HOUR_HEIGHT}px`;
    colonna.appendChild(riga);
  }
  const delGiorno = itemsForDay(items, key);
  const layout = overlapLayout(delGiorno);
  delGiorno.forEach((item, i) => {
    const { top, height } = blockGeometry(item, key);
    const { column, columns } = layout[i];
    const card = renderCard(item, { onOpen, compact: height < 40 });
    card.classList.add('agenda-block');
    card.style.top = `${top}px`;
    card.style.height = `${height}px`;
    card.style.left = `calc(${(100 / columns) * column}% + 2px)`;
    card.style.width = `calc(${100 / columns}% - 4px)`;
    colonna.appendChild(card);
  });
  return colonna;
}

function griglia(days, items, onOpen, classe) {
  const oggi = todayKey();
  const radice = el('div', `agenda-grid ${classe}`);
  radice.style.setProperty('--agenda-days', String(days.length));

  const testata = el('div', 'agenda-grid-head');
  testata.appendChild(el('div', 'agenda-hours-head'));
  for (const key of days) {
    const cella = el('div', `agenda-day-head${key === oggi ? ' agenda-today' : ''}`,
      days.length === 1 ? formatDayLong(key) : formatDayShort(key));
    cella.dataset.day = key;
    testata.appendChild(cella);
  }
  radice.appendChild(testata);

  const corpo = el('div', 'agenda-grid-body');
  corpo.appendChild(colonnaOre());
  for (const key of days) corpo.appendChild(colonnaGiorno(key, items, onOpen));
  radice.appendChild(corpo);
  return radice;
}

/** Porta lo scroll su 08:00 (e' solo la posizione iniziale). */
function scrollIniziale(scroller) {
  requestAnimationFrame(() => { scroller.scrollTop = initialScrollTop(); });
}

/** SETTIMANA: lunedi' -> domenica, sette colonne, domenica sempre visibile. */
export function renderWeek(target, { days, items, onOpen }) {
  const scroller = el('div', 'agenda-scroll agenda-scroll-week');
  scroller.appendChild(griglia(days, items, onOpen, 'agenda-grid-week'));
  target.appendChild(scroller);
  scrollIniziale(scroller);
}

/** GIORNO: una colonna. */
export function renderDay(target, { days, items, onOpen }) {
  const scroller = el('div', 'agenda-scroll agenda-scroll-day');
  scroller.appendChild(griglia(days.slice(0, 1), items, onOpen, 'agenda-grid-day'));
  target.appendChild(scroller);
  scrollIniziale(scroller);
}

/** LISTA: gli appuntamenti dei giorni richiesti, raggruppati per giorno. */
export function renderList(target, { days, items, onOpen }) {
  const lista = el('div', 'agenda-list');
  const oggi = todayKey();
  let qualcosa = false;
  for (const key of days) {
    const delGiorno = itemsForDay(items, key)
      .slice().sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
    if (!delGiorno.length) continue;
    qualcosa = true;
    const gruppo = el('section', 'agenda-list-day');
    gruppo.appendChild(el('h3', 'agenda-list-day-title',
      key === oggi ? `Oggi · ${formatDayLong(key)}` : formatDayLong(key)));
    for (const item of delGiorno) gruppo.appendChild(renderCard(item, { onOpen }));
    lista.appendChild(gruppo);
  }
  if (!qualcosa) lista.appendChild(el('p', 'muted', 'Nessun appuntamento in questo periodo.'));
  target.appendChild(lista);
}
