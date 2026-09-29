// STIMA360 OS — components/agenda/agenda-views.js (A30-4)
//
// Le viste dell'Agenda: SETTIMANA e MESE (desktop e tablet), GIORNO e LISTA
// (smartphone, disponibili anche su desktop). Nessun trascinamento, nessun
// ridimensionamento: un blocco si apre con un click (o Invio) e le modifiche
// passano dai dialog. Nel MESE una cella porta solo alla vista Giorno: niente
// si modifica da li' (piano §4.3).
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
  isOnlineAppointment,
  itemTitle,
  itemsForDay,
  MONTH_CELL_ROWS,
  monthGrid,
  overflowLayout,
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
  // A30-12: prenotato dal cliente con il link pubblico (source='booking_link').
  if (isOnlineAppointment(item)) riga.appendChild(el('span', 'agenda-badge agenda-badge-online', 'Online'));
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

function colonnaGiorno(key, items, onOpen, onSlotClick, apriAltri) {
  const colonna = el('div', 'agenda-day-column');
  colonna.dataset.day = key;
  colonna.style.height = `${24 * HOUR_HEIGHT}px`;
  for (let h = 0; h < 24; h += 1) {
    const riga = el('div', 'agenda-hour-slot');
    riga.style.top = `${h * HOUR_HEIGHT}px`;
    riga.style.height = `${HOUR_HEIGHT}px`;
    // A30-13B.1: un click (o Invio/Spazio) su un'ora VUOTA apre il dialog di
    // creazione precompilato su questo giorno e questa ora. Le card degli
    // appuntamenti sono sopra (assolute, aggiunte dopo) e intercettano il
    // click prima che arrivi qui: uno slot occupato non apre mai questo
    // percorso.
    if (onSlotClick) {
      riga.classList.add('agenda-hour-slot-clickable');
      riga.tabIndex = 0;
      riga.setAttribute('role', 'button');
      riga.setAttribute('aria-label',
        `Nuovo appuntamento alle ${String(h).padStart(2, '0')}:00`);
      riga.addEventListener('click', () => onSlotClick(key, h));
      riga.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          onSlotClick(key, h);
        }
      });
    }
    colonna.appendChild(riga);
  }
  const delGiorno = itemsForDay(items, key);
  // A30-13C: oltre MAX_OVERLAP_COLUMNS eventi sovrapposti le card non si
  // impilano piu' una sull'altra (solo quella sopra restava cliccabile):
  // l'ultima corsia porta un "+N" che apre l'elenco dei nascosti.
  const { placement, overflow } = overflowLayout(delGiorno);
  const posiziona = (nodo, { top, height }, { column, columns }) => {
    nodo.classList.add('agenda-block');
    nodo.style.top = `${top}px`;
    nodo.style.height = `${height}px`;
    nodo.style.left = `calc(${(100 / columns) * column}% + 2px)`;
    nodo.style.width = `calc(${100 / columns}% - 4px)`;
  };
  delGiorno.forEach((item, i) => {
    if (!placement[i]) return;                   // dietro un "+N"
    const geometria = blockGeometry(item, key);
    const card = renderCard(item, { onOpen, compact: geometria.height < 40 });
    posiziona(card, geometria, placement[i]);
    colonna.appendChild(card);
  });
  for (const gruppo of overflow) {
    const nascosti = gruppo.indices.map((i) => delGiorno[i]);
    const orario = `${formatTime(gruppo.start_at)}–${formatTime(gruppo.end_at)}`;
    const altri = el('button', 'btn agenda-more', `+${nascosti.length}`);
    altri.type = 'button';
    altri.dataset.overflowCount = String(nascosti.length);
    altri.setAttribute('aria-label', `Altri ${nascosti.length} appuntamenti, ${orario}`);
    altri.title = `Altri ${nascosti.length} appuntamenti, ${orario}`;
    posiziona(altri, blockGeometry(gruppo, key), gruppo);
    if (apriAltri) altri.addEventListener('click', () => apriAltri({ day: key, items: nascosti, orario }));
    colonna.appendChild(altri);
  }
  return colonna;
}

/**
 * A30-13C: il pannello degli appuntamenti nascosti dietro un "+N", uno per
 * griglia e creato solo al primo uso. Le card sono le stesse della Lista;
 * aprirne una chiude il pannello e apre il suo dettaglio. Esc chiude (dialog
 * nativo).
 */
function pannelloAltri(radice, onOpen) {
  let dialogo = null;
  return ({ day, items, orario }) => {
    if (!dialogo) {
      dialogo = el('dialog', 'modal agenda-overflow');
      dialogo.setAttribute('aria-modal', 'true');
      radice.appendChild(dialogo);
    }
    dialogo.setAttribute('aria-label', `Altri ${items.length} appuntamenti, ${formatDayLong(day)}, ${orario}`);
    const gruppo = el('section', 'agenda-list-day');
    gruppo.appendChild(el('h3', 'agenda-list-day-title',
      `Altri ${items.length} appuntamenti · ${formatDayLong(day)} · ${orario}`));
    const apri = onOpen ? (item) => { dialogo.close(); onOpen(item); } : undefined;
    for (const item of items) gruppo.appendChild(renderCard(item, { onOpen: apri }));
    const chiudi = el('button', 'btn', 'Chiudi');
    chiudi.type = 'button';
    chiudi.addEventListener('click', () => dialogo.close());
    dialogo.replaceChildren(gruppo, chiudi);
    dialogo.showModal();
  };
}

function griglia(days, items, onOpen, classe, onSlotClick) {
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
  const apriAltri = pannelloAltri(radice, onOpen);
  for (const key of days) {
    corpo.appendChild(colonnaGiorno(key, items, onOpen, onSlotClick, apriAltri));
  }
  radice.appendChild(corpo);
  return radice;
}

/** Porta lo scroll su 08:00 (e' solo la posizione iniziale). */
function scrollIniziale(scroller) {
  requestAnimationFrame(() => { scroller.scrollTop = initialScrollTop(); });
}

/** SETTIMANA: lunedi' -> domenica, sette colonne, domenica sempre visibile. */
export function renderWeek(target, { days, items, onOpen, onSlotClick }) {
  const scroller = el('div', 'agenda-scroll agenda-scroll-week');
  scroller.appendChild(griglia(days, items, onOpen, 'agenda-grid-week', onSlotClick));
  target.appendChild(scroller);
  scrollIniziale(scroller);
}

/** GIORNO: una colonna. */
export function renderDay(target, { days, items, onOpen, onSlotClick }) {
  const scroller = el('div', 'agenda-scroll agenda-scroll-day');
  scroller.appendChild(griglia(days.slice(0, 1), items, onOpen, 'agenda-grid-day', onSlotClick));
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

const INTESTAZIONE_MESE = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];

/** Una riga compatta del Mese: ora, tipo (o "Occupato"), "Online". Solo testo:
 *  nessun click, nessun dettaglio - la cella porta al Giorno (piano §4.3). */
function rigaMese(item) {
  const riga = el('span', `agenda-month-chip agenda-status-${isBusy(item) ? 'busy' : (item.status || '')}`);
  riga.title = itemAriaLabel(item);
  const tipo = isBusy(item) ? (item.label || 'Occupato') : typeLabel(item.type || item.appointment_type);
  riga.appendChild(el('span', 'agenda-month-chip-text', `${formatTime(item.start_at)} ${tipo || ''}`.trim()));
  if (isOnlineAppointment(item)) riga.appendChild(el('span', 'agenda-badge agenda-badge-online', 'Online'));
  return riga;
}

/**
 * MESE (piano §4.3, desktop/tablet): settimane da lunedi', una cella per
 * giorno. Nelle celle del mese: fino a MONTH_CELL_ROWS righe, poi "+N altri",
 * e il conteggio nell'etichetta accessibile. Un click (o Invio) sulla cella
 * porta alla vista Giorno di quella data. I giorni dei mesi vicini sono
 * contorno: attenuati, senza conteggi (i loro dati sono nel loro mese).
 */
export function renderMonth(target, { dayKey, items, onDay }) {
  const oggi = todayKey();
  const mese = el('div', 'agenda-month');
  mese.setAttribute('role', 'grid');
  const testa = el('div', 'agenda-month-row agenda-month-head');
  testa.setAttribute('role', 'row');
  for (const g of INTESTAZIONE_MESE) {
    const c = el('div', 'agenda-month-weekday', g);
    c.setAttribute('role', 'columnheader');
    testa.appendChild(c);
  }
  mese.appendChild(testa);
  for (const settimana of monthGrid(dayKey)) {
    const riga = el('div', 'agenda-month-row');
    riga.setAttribute('role', 'row');
    for (const { key, inMonth } of settimana) {
      const cella = el('button', `agenda-month-cell${inMonth ? '' : ' agenda-month-out'}${key === oggi ? ' agenda-today' : ''}`);
      cella.type = 'button';
      cella.setAttribute('role', 'gridcell');
      cella.dataset.day = key;
      const numero = el('span', 'agenda-month-day', String(Number(key.slice(8, 10))));
      cella.appendChild(numero);
      if (inMonth) {
        const delGiorno = itemsForDay(items, key)
          .slice().sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
        cella.dataset.count = String(delGiorno.length);
        for (const item of delGiorno.slice(0, MONTH_CELL_ROWS)) cella.appendChild(rigaMese(item));
        if (delGiorno.length > MONTH_CELL_ROWS) {
          const altri = el('span', 'agenda-month-more', `+${delGiorno.length - MONTH_CELL_ROWS} altri`);
          altri.dataset.more = '';
          cella.appendChild(altri);
        }
        const conteggio = delGiorno.length === 1 ? '1 appuntamento'
          : `${delGiorno.length} appuntamenti`;
        cella.setAttribute('aria-label', `${formatDayLong(key)}, ${delGiorno.length ? conteggio : 'nessun appuntamento'}. Apri il giorno`);
      } else {
        cella.setAttribute('aria-label', `${formatDayLong(key)} (mese vicino). Apri il giorno`);
      }
      if (onDay) cella.addEventListener('click', () => onDay(key));
      riga.appendChild(cella);
    }
    mese.appendChild(riga);
  }
  target.appendChild(mese);
}
