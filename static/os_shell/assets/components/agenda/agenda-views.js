// STIMA360 OS — components/agenda/agenda-views.js (A30-4, AGENDA MOBILE)
//
// Le quattro viste dell'Agenda - GIORNO, SETTIMANA, MESE, LISTA - su OGNI
// dispositivo. Con `compact` (smartphone, telefono in orizzontale) le stesse
// viste si dispongono per lo schermo stretto: settimana a sette colonne
// strette con blocchi sintetici, mese a indicatori con l'elenco del giorno
// scelto sotto la griglia. Nessun trascinamento, nessun ridimensionamento: un
// blocco si apre con un tocco (o Invio) e le modifiche passano dai dialog.
//
// Tutto e' costruito con le API del DOM e `textContent`: nessun dato arrivato
// dal server finisce in `innerHTML`.
//
// Si mostra SOLO cio' che il server restituisce. Un impegno di un collega
// arriva come `kind: "busy"` con orario, nome dell'agente e "Occupato", e cosi'
// si disegna: niente cliente, niente tipo, niente click verso un dettaglio che
// il server rifiuterebbe.

import {
  addDays,
  COMPACT_OVERLAP_COLUMNS,
  HOUR_HEIGHT,
  MAX_OVERLAP_COLUMNS,
  blockGeometry,
  clusterLayout,
  dayTimeLabel,
  durationMinutes,
  emptyRangeLabel,
  formatDayLong,
  formatDayShort,
  formatDuration,
  formatTime,
  initialScrollTop,
  isOnlineAppointment,
  isVisit,
  itemTitle,
  itemsForDay,
  listGroups,
  MONTH_CELL_ROWS,
  monthDayMarkers,
  monthGrid,
  nowLineTop,
  overflowLayout,
  romeDateKey,
  sortedItemsForDay,
  statusLabel,
  todayKey,
  TYPE_CATEGORY_LABELS,
  typeCategory,
  typeLabel,
  typeShortLabel,
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
export function renderCard(item, { onOpen, compact = false, dayKey = null } = {}) {
  const busy = isBusy(item);
  const stato = busy ? 'busy' : (item.status || '');
  const card = el(busy ? 'div' : 'button',
    `agenda-card agenda-status-${stato} agenda-type-cat-${typeCategory(item)}${compact ? ' agenda-card-compact' : ''}${isVisit(item) ? ' agenda-is-visit' : ''}`);
  card.setAttribute('aria-label', itemAriaLabel(item));
  if (!busy) {
    card.type = 'button';
    card.dataset.appointmentId = String(item.id);
    if (onOpen) card.addEventListener('click', () => onOpen(item));
  }

  const minuti = durationMinutes(item.start_at, item.end_at);
  const testa = el('div', 'agenda-card-head');
  // AGENDA MOBILE: visto da un giorno, un appuntamento che attraversa la
  // mezzanotte dice da dove viene e dove va ("ieri 23:30–02:00").
  const orario = dayKey ? dayTimeLabel(item, dayKey)
    : { text: `${formatTime(item.start_at)}–${formatTime(item.end_at)}`, startsBefore: false, endsAfter: false };
  // Inizio e fine in due parti (il testo resta "inizio–fine"): la settimana
  // stretta mostra solo l'inizio.
  const ora = el('span', 'agenda-card-time');
  const trattino = orario.text.indexOf('–');
  if (trattino > 0) {
    ora.append(el('span', 'agenda-card-from', orario.text.slice(0, trattino)),
      el('span', 'agenda-card-to', orario.text.slice(trattino)));
  } else {
    ora.textContent = orario.text;
  }
  if (orario.startsBefore) card.dataset.startsBefore = '';
  if (orario.endsAfter) card.dataset.endsAfter = '';
  testa.appendChild(ora);
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
  // Il luogo (o il comune dell'immobile), quando il titolo e' il cliente.
  const luogo = item.place || item.location_text || '';
  if (luogo && luogo !== titolo) card.appendChild(el('div', 'agenda-card-place', luogo));
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

/**
 * SETTIMANA LEGGIBILE: il blocco della settimana stretta. Due righe, mai
 * sovrapposte e mai sotto gli 11px: l'ora d'inizio (o "ieri" / "3/11" se e'
 * cominciato in un giorno precedente: bordo superiore tratteggiato, orari
 * completi nel riepilogo e nell'etichetta accessibile) e il tipo abbreviato ("Visita", "Sopr.", "PdA"). Il colore di
 * fondo e' la famiglia del tipo, il bordo e' lo stato; tutto il resto (cliente,
 * luogo, agente, stato per esteso) e' nel riepilogo del giorno e nella scheda.
 */
function giornoDiInizio(item, key) {
  const inizio = romeDateKey(item.start_at);
  if (inizio === addDays(key, -1)) return 'ieri';
  return `${Number(inizio.slice(8, 10))}/${Number(inizio.slice(5, 7))}`;
}

export function renderWeekBlock(item, key, { onOpen } = {}) {
  const busy = isBusy(item);
  const stato = busy ? 'busy' : (item.status || '');
  const blocco = el(busy ? 'div' : 'button',
    `agenda-card agenda-week-block agenda-status-${stato} agenda-type-cat-${typeCategory(item)}`);
  blocco.setAttribute('aria-label', itemAriaLabel(item));
  blocco.title = itemAriaLabel(item);
  const orario = dayTimeLabel(item, key);
  const ora = orario.startsBefore ? giornoDiInizio(item, key) : formatTime(item.start_at);
  blocco.append(el('span', 'agenda-week-block-time', ora));
  const tipo = typeShortLabel(item);
  if (tipo) blocco.append(el('span', 'agenda-week-block-type', tipo));
  if (orario.startsBefore) blocco.dataset.startsBefore = '';
  if (orario.endsAfter) blocco.dataset.endsAfter = '';
  if (!busy) {
    blocco.type = 'button';
    blocco.dataset.appointmentId = String(item.id);
    if (onOpen) blocco.addEventListener('click', () => onOpen(item));
  }
  return blocco;
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

function colonnaGiorno(key, items, onOpen, onSlotClick, apriAltri, {
  maxColumns = MAX_OVERLAP_COLUMNS, compact = false, cluster = false, selected = false, onPick = null,
} = {}) {
  const colonna = el('div', `agenda-day-column${selected ? ' agenda-day-selected' : ''}`);
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
      riga.addEventListener('click', () => { if (onPick) onPick(key); onSlotClick(key, h); });
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
  // Nella settimana stretta (a gruppi) il blocco sta a 1px dai bordi della
  // colonna, per lasciare spazio al testo; altrove 2px come sempre.
  const margine = cluster ? 1 : 2;
  const posiziona = (nodo, { top, height }, { column, columns }) => {
    nodo.classList.add('agenda-block');
    nodo.style.top = `${top}px`;
    nodo.style.height = `${height}px`;
    nodo.style.left = `calc(${(100 / columns) * column}% + ${margine}px)`;
    nodo.style.width = `calc(${100 / columns}% - ${2 * margine}px)`;
  };
  const apriEScegli = onOpen ? (item) => { if (onPick) onPick(key); onOpen(item); } : undefined;
  if (cluster) {
    // SETTIMANA LEGGIBILE: niente blocchi affiancati nella colonna stretta.
    // Un appuntamento solo = un blocco; piu' appuntamenti sovrapposti = UN
    // blocco "10:00 · 3 app." che li elenca tutti (e il riepilogo del giorno).
    for (const gruppo of clusterLayout(delGiorno)) {
      const tutta = { column: 0, columns: 1 };
      if (gruppo.indices.length === 1) {
        const item = delGiorno[gruppo.indices[0]];
        const blocco = renderWeekBlock(item, key, { onOpen: apriEScegli });
        posiziona(blocco, blockGeometry(item, key), tutta);
        colonna.appendChild(blocco);
        continue;
      }
      const elenco = gruppo.indices.map((i) => delGiorno[i]);
      const orario = `${formatTime(gruppo.start_at)}–${formatTime(gruppo.end_at)}`;
      const insieme = el('button', 'btn agenda-more agenda-week-cluster');
      insieme.type = 'button';
      insieme.dataset.overflowCount = String(elenco.length);
      insieme.dataset.clusterIds = elenco.map((x) => x.id).filter((x) => x !== undefined).join(',');
      insieme.setAttribute('aria-label', `${elenco.length} appuntamenti sovrapposti, ${orario}`);
      insieme.title = `${elenco.length} appuntamenti sovrapposti, ${orario}`;
      const primo = dayTimeLabel(elenco[0], key).startsBefore ? giornoDiInizio(elenco[0], key) : formatTime(gruppo.start_at);
      insieme.append(el('span', 'agenda-week-block-time', primo),
        el('span', 'agenda-week-block-type', `${elenco.length} app.`));
      posiziona(insieme, blockGeometry(gruppo, key), tutta);
      if (apriAltri) {
        insieme.addEventListener('click', () => {
          if (onPick) onPick(key);
          apriAltri({ day: key, items: elenco, orario });
        });
      }
      colonna.appendChild(insieme);
    }
  }
  // A30-13C: oltre MAX_OVERLAP_COLUMNS eventi sovrapposti le card non si
  // impilano piu' una sull'altra (solo quella sopra restava cliccabile):
  // l'ultima corsia porta un "+N" che apre l'elenco dei nascosti.
  const { placement, overflow } = cluster ? { placement: [], overflow: [] } : overflowLayout(delGiorno, maxColumns);
  delGiorno.forEach((item, i) => {
    if (!placement[i]) return;                   // dietro un "+N"
    const geometria = blockGeometry(item, key);
    const card = renderCard(item, { onOpen: apriEScegli, compact: compact || geometria.height < 40, dayKey: key });
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
  // AGENDA MOBILE: la linea dell'ora corrente, solo nella colonna di oggi.
  const top = nowLineTop(key);
  if (top !== null) {
    const linea = el('div', 'agenda-now');
    linea.dataset.nowLine = key;
    linea.setAttribute('aria-hidden', 'true');
    linea.style.top = `${top}px`;
    colonna.appendChild(linea);
  }
  return colonna;
}

/** Riporta all'ora attuale le linee "adesso" gia' disegnate (ogni minuto). */
export function refreshNowLines(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return;
  for (const linea of root.querySelectorAll('[data-now-line]')) {
    const top = nowLineTop(linea.dataset.nowLine);
    if (top === null) linea.remove();
    else linea.style.top = `${top}px`;
  }
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
    for (const item of items) gruppo.appendChild(renderCard(item, { onOpen: apri, dayKey: day }));
    const chiudi = el('button', 'btn', 'Chiudi');
    chiudi.type = 'button';
    chiudi.addEventListener('click', () => dialogo.close());
    dialogo.replaceChildren(gruppo, chiudi);
    dialogo.showModal();
  };
}

function griglia(days, items, onOpen, classe, onSlotClick, {
  compact = false, maxColumns = MAX_OVERLAP_COLUMNS, onDayOpen = null, cluster = false, selectedKey = null, onPick = null,
} = {}) {
  const oggi = todayKey();
  const radice = el('div', `agenda-grid ${classe}${compact ? ' agenda-grid-compact' : ''}`);
  radice.style.setProperty('--agenda-days', String(days.length));

  const testata = el('div', 'agenda-grid-head');
  testata.appendChild(el('div', 'agenda-hours-head'));
  for (const key of days) {
    const testo = days.length === 1 ? formatDayLong(key) : formatDayShort(key);
    // Nella settimana l'intestazione di un giorno e' un pulsante: con
    // `onPick` lo SCEGLIE (riepilogo sotto la griglia, nessun caricamento),
    // altrimenti con `onDayOpen` apre la vista Giorno.
    const scelto = !!onPick && key === selectedKey;
    const cliccabile = days.length > 1 && (onPick || onDayOpen);
    const classi = `agenda-day-head${key === oggi ? ' agenda-today' : ''}${scelto ? ' agenda-day-selected' : ''}`;
    const cella = cliccabile ? el('button', classi, testo) : el('div', classi, testo);
    cella.dataset.day = key;
    if (key === oggi) cella.setAttribute('aria-current', 'date');
    if (cliccabile) {
      cella.type = 'button';
      if (onPick) {
        cella.setAttribute('aria-pressed', scelto ? 'true' : 'false');
        cella.setAttribute('aria-label', `Mostra ${formatDayLong(key)}`);
        cella.addEventListener('click', () => onPick(key));
      } else {
        cella.setAttribute('aria-label', `Apri ${formatDayLong(key)}`);
        cella.addEventListener('click', () => onDayOpen(key));
      }
    }
    testata.appendChild(cella);
  }
  radice.appendChild(testata);

  const corpo = el('div', 'agenda-grid-body');
  corpo.appendChild(colonnaOre());
  const apriAltri = pannelloAltri(radice, onOpen);
  for (const key of days) {
    corpo.appendChild(colonnaGiorno(key, items, onOpen, onSlotClick, apriAltri, {
      maxColumns, compact, cluster, selected: !!onPick && key === selectedKey, onPick,
    }));
  }
  radice.appendChild(corpo);
  return radice;
}

/** Porta lo scroll su 08:00 (e' solo la posizione iniziale). */
function scrollIniziale(scroller) {
  requestAnimationFrame(() => { scroller.scrollTop = initialScrollTop(); });
}

/**
 * SETTIMANA: lunedi' -> domenica, sette colonne, domenica sempre visibile.
 * Con `compact` le colonne sono strette: blocchi colorati con l'ora, al
 * massimo COMPACT_OVERLAP_COLUMNS.week affiancati e poi "+N"; il dettaglio e'
 * nel pannello, con un tocco.
 */
export function renderWeek(target, {
  days, items, onOpen, onSlotClick, compact = false, onDayOpen = null,
  selectedKey = null, onSelect = null, onDay = null, onCreate = null,
}) {
  const scroller = el('div', 'agenda-scroll agenda-scroll-week');
  // SETTIMANA LEGGIBILE: con `onSelect` un giorno si SCEGLIE (intestazione,
  // un blocco, un'ora vuota) e il riepilogo sotto la griglia mostra i suoi
  // appuntamenti. Cambiare giorno non ricarica nulla e non cambia settimana:
  // si aggiornano solo le classi e il riepilogo.
  const giorno = onSelect ? (days.includes(selectedKey) ? selectedKey : days[0]) : null;
  let riepilogo = null;
  const contenitore = el('div', 'agenda-week-layout');
  const scegli = onSelect ? (key) => {
    if (!days.includes(key)) return;
    for (const n of scroller.querySelectorAll('[data-day]')) {
      const si = n.dataset.day === key;
      n.classList.toggle('agenda-day-selected', si);
      if (n.tagName === 'BUTTON') n.setAttribute('aria-pressed', si ? 'true' : 'false');
    }
    const nuovo = pannelloGiorno(key, items, { onOpen, onDay, onCreate });
    nuovo.classList.add('agenda-week-day-panel');
    if (riepilogo) {
      contenitore.insertBefore(nuovo, riepilogo);
      riepilogo.remove();
    } else {
      contenitore.appendChild(nuovo);
    }
    riepilogo = nuovo;
    onSelect(key);
  } : null;
  scroller.appendChild(griglia(days, items, onOpen, 'agenda-grid-week', onSlotClick, {
    compact,
    onDayOpen,
    cluster: compact,
    maxColumns: compact ? COMPACT_OVERLAP_COLUMNS.week : MAX_OVERLAP_COLUMNS,
    selectedKey: giorno,
    onPick: scegli,
  }));
  contenitore.appendChild(scroller);
  if (compact) contenitore.appendChild(chiaveTipi());
  target.appendChild(contenitore);
  if (scegli) {
    riepilogo = pannelloGiorno(giorno, items, { onOpen, onDay, onCreate });
    riepilogo.classList.add('agenda-week-day-panel');
    contenitore.appendChild(riepilogo);
  }
  scrollIniziale(scroller);
}

/** La chiave dei colori delle famiglie di tipo (sotto la settimana stretta). */
function chiaveTipi() {
  const chiave = el('p', 'agenda-type-key');
  chiave.setAttribute('aria-label', 'Colori dei tipi di appuntamento');
  for (const [famiglia, nome] of Object.entries(TYPE_CATEGORY_LABELS)) {
    const voce = el('span', `agenda-type-key-item agenda-type-cat-${famiglia}`, nome);
    chiave.appendChild(voce);
  }
  return chiave;
}

/** GIORNO: una colonna, con la linea dell'ora corrente. */
export function renderDay(target, { days, items, onOpen, onSlotClick, compact = false }) {
  const scroller = el('div', 'agenda-scroll agenda-scroll-day');
  scroller.appendChild(griglia(days.slice(0, 1), items, onOpen, 'agenda-grid-day', onSlotClick, {
    maxColumns: compact ? COMPACT_OVERLAP_COLUMNS.day : MAX_OVERLAP_COLUMNS,
  }));
  target.appendChild(scroller);
  scrollIniziale(scroller);
}

/**
 * LISTA: agenda cronologica dalla data scelta. Un gruppo per giorno con
 * appuntamenti (in ordine di orario dentro il giorno); i giorni vuoti
 * consecutivi diventano una riga discreta. `onMore` aggiunge in fondo il
 * pulsante per caricare i giorni successivi.
 */
export function renderList(target, { days, items, onOpen, onMore = null, moreLabel = 'Mostra altri giorni' }) {
  const lista = el('div', 'agenda-list');
  const oggi = todayKey();
  const gruppi = listGroups(days, items);
  const qualcosa = gruppi.some((g) => g.kind === 'day');
  for (const g of gruppi) {
    if (g.kind === 'empty') {
      if (!qualcosa) continue;
      const vuoto = el('p', 'agenda-list-empty muted', `Nessun appuntamento · ${emptyRangeLabel(g.from, g.to)}`);
      vuoto.dataset.emptyFrom = g.from;
      vuoto.dataset.emptyTo = g.to;
      lista.appendChild(vuoto);
      continue;
    }
    const gruppo = el('section', `agenda-list-day${g.key === oggi ? ' agenda-today' : ''}`);
    gruppo.dataset.day = g.key;
    gruppo.appendChild(el('h3', 'agenda-list-day-title',
      g.key === oggi ? `Oggi · ${formatDayLong(g.key)}` : formatDayLong(g.key)));
    for (const item of g.items) gruppo.appendChild(renderCard(item, { onOpen, dayKey: g.key }));
    lista.appendChild(gruppo);
  }
  if (!qualcosa) lista.appendChild(el('p', 'muted', 'Nessun appuntamento in questo periodo.'));
  if (onMore) {
    const altri = el('button', 'btn agenda-list-more', moreLabel);
    altri.type = 'button';
    altri.dataset.listMore = '';
    altri.addEventListener('click', () => onMore(altri));
    lista.appendChild(altri);
  }
  target.appendChild(lista);
}

const INTESTAZIONE_MESE = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];

/** Una riga compatta del Mese: ora, tipo (o "Occupato"), "Online". Solo testo:
 *  nessun click, nessun dettaglio - la cella porta al Giorno (piano §4.3). */
function rigaMese(item, key) {
  const riga = el('span', `agenda-month-chip agenda-status-${isBusy(item) ? 'busy' : (item.status || '')}`);
  riga.title = itemAriaLabel(item);
  const tipo = isBusy(item) ? (item.label || 'Occupato') : typeLabel(item.type || item.appointment_type);
  // Un appuntamento iniziato il giorno prima non mostra l'ora di ieri come
  // se fosse di oggi: "↳ fino 02:00".
  const orario = key && dayTimeLabel(item, key).startsBefore
    ? `↳ fino ${formatTime(item.end_at)}` : formatTime(item.start_at);
  riga.appendChild(el('span', 'agenda-month-chip-text', `${orario} ${tipo || ''}`.trim()));
  if (isOnlineAppointment(item)) riga.appendChild(el('span', 'agenda-badge agenda-badge-online', 'Online'));
  return riga;
}

/**
 * Gli indicatori di una cella del Mese: puntini colorati per stato (un
 * quadratino per le visite immobile) e il numero vero di appuntamenti.
 */
function indicatoriMese(items, key) {
  const m = monthDayMarkers(items, key, MONTH_CELL_ROWS);
  const box = el('span', 'agenda-month-markers');
  box.setAttribute('aria-hidden', 'true');
  for (const d of m.dots) {
    box.appendChild(el('span', `agenda-dot agenda-dot-${d.status}${d.visit ? ' agenda-dot-visit' : ''}`));
  }
  if (m.total) box.appendChild(el('span', 'agenda-month-count', String(m.total)));
  return { box, markers: m };
}

/** Il riquadro sotto (o accanto) alla griglia: gli appuntamenti del giorno scelto. */
function pannelloGiorno(key, items, { onOpen, onDay, onCreate }) {
  const box = el('section', 'agenda-month-day-panel');
  box.dataset.selectedDay = key;
  box.setAttribute('aria-live', 'polite');
  const testa = el('div', 'agenda-month-day-panel-head');
  testa.appendChild(el('h3', 'agenda-list-day-title',
    key === todayKey() ? `Oggi · ${formatDayLong(key)}` : formatDayLong(key)));
  const azioni = el('div', 'agenda-month-day-panel-actions');
  if (onDay) {
    const apri = el('button', 'btn btn-small', 'Apri giorno');
    apri.type = 'button';
    apri.dataset.openDay = key;
    apri.addEventListener('click', () => onDay(key));
    azioni.appendChild(apri);
  }
  if (onCreate) {
    const nuovo = el('button', 'btn btn-small', '+ Nuovo');
    nuovo.type = 'button';
    nuovo.setAttribute('aria-label', `Nuovo appuntamento il ${formatDayLong(key)}`);
    nuovo.addEventListener('click', () => onCreate(key));
    azioni.appendChild(nuovo);
  }
  testa.appendChild(azioni);
  box.appendChild(testa);
  const delGiorno = sortedItemsForDay(items, key);
  if (!delGiorno.length) {
    box.appendChild(el('p', 'agenda-list-empty muted', 'Nessun appuntamento in questo giorno.'));
  } else {
    const elenco = el('div', 'agenda-list-day');
    for (const item of delGiorno) elenco.appendChild(renderCard(item, { onOpen, dayKey: key }));
    box.appendChild(elenco);
  }
  return box;
}

/**
 * MESE: settimane da lunedi' (4, 5 o 6 righe), una cella per giorno, oggi
 * evidenziato. In ogni cella del mese: gli indicatori (puntini e numero) e,
 * dove c'e' spazio, fino a MONTH_CELL_ROWS righe di testo e "+N altri". Il
 * conteggio e' anche nell'etichetta accessibile.
 *
 * AGENDA MOBILE: con `onSelect` un tocco su un giorno del mese lo SCEGLIE e
 * mostra i suoi appuntamenti nel riquadro del giorno (tocco su un
 * appuntamento = la sua scheda; "Apri giorno" = vista Giorno). Un giorno di un
 * mese vicino porta a quel mese (`onOutside`). Senza `onSelect` la cella porta
 * al Giorno (comportamento A30-4). I giorni vicini sono contorno: attenuati,
 * senza dati (i loro dati sono nel loro mese).
 */
export function renderMonth(target, {
  dayKey, items, onDay, onSelect = null, onOpen = null, onCreate = null, onOutside = null, selectedKey = null,
}) {
  const oggi = todayKey();
  let scelto = selectedKey || dayKey;
  const contenitore = el('div', `agenda-month-layout${onSelect ? ' agenda-month-selectable' : ''}`);
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
  const celle = [];
  let pannello = null;
  const mostra = (key) => {
    scelto = key;
    for (const c of celle) {
      const si = c.dataset.day === key;
      c.classList.toggle('agenda-month-selected', si);
      c.setAttribute('aria-selected', si ? 'true' : 'false');
    }
    const nuovo = pannelloGiorno(key, items, { onOpen, onDay, onCreate });
    if (pannello) {
      contenitore.insertBefore(nuovo, pannello);
      pannello.remove();
    } else {
      contenitore.appendChild(nuovo);
    }
    pannello = nuovo;
  };
  for (const settimana of monthGrid(dayKey)) {
    const riga = el('div', 'agenda-month-row');
    riga.setAttribute('role', 'row');
    for (const { key, inMonth } of settimana) {
      const cella = el('button', `agenda-month-cell${inMonth ? '' : ' agenda-month-out'}${key === oggi ? ' agenda-today' : ''}`);
      cella.type = 'button';
      cella.setAttribute('role', 'gridcell');
      cella.dataset.day = key;
      if (key === oggi) cella.setAttribute('aria-current', 'date');
      const numero = el('span', 'agenda-month-day', String(Number(key.slice(8, 10))));
      cella.appendChild(numero);
      if (inMonth) {
        const delGiorno = sortedItemsForDay(items, key);
        cella.dataset.count = String(delGiorno.length);
        if (delGiorno.length) cella.classList.add('agenda-month-busy');
        const { box, markers } = indicatoriMese(items, key);
        if (markers.visits) cella.dataset.visits = String(markers.visits);
        cella.appendChild(box);
        for (const item of delGiorno.slice(0, MONTH_CELL_ROWS)) cella.appendChild(rigaMese(item, key));
        if (delGiorno.length > MONTH_CELL_ROWS) {
          const altri = el('span', 'agenda-month-more', `+${delGiorno.length - MONTH_CELL_ROWS} altri`);
          altri.dataset.more = '';
          cella.appendChild(altri);
        }
        const conteggio = delGiorno.length === 1 ? '1 appuntamento'
          : `${delGiorno.length} appuntamenti`;
        const visite = markers.visits ? `, di cui ${markers.visits === 1 ? '1 visita' : `${markers.visits} visite`}` : '';
        cella.setAttribute('aria-label', `${formatDayLong(key)}, ${delGiorno.length ? conteggio + visite : 'nessun appuntamento'}. ${onSelect ? 'Mostra il giorno' : 'Apri il giorno'}`);
        if (onSelect) {
          cella.addEventListener('click', () => { mostra(key); onSelect(key); });
        } else if (onDay) {
          cella.addEventListener('click', () => onDay(key));
        }
      } else {
        cella.setAttribute('aria-label', `${formatDayLong(key)} (mese vicino). ${onOutside ? 'Vai a quel mese' : 'Apri il giorno'}`);
        const vai = onOutside || onDay;
        if (vai) cella.addEventListener('click', () => vai(key));
      }
      celle.push(cella);
      riga.appendChild(cella);
    }
    mese.appendChild(riga);
  }
  contenitore.appendChild(mese);
  // La chiave degli indicatori (mostrata nella disposizione compatta).
  const chiave = el('p', 'agenda-month-key');
  for (const [classe, testo] of [['agenda-dot agenda-dot-scheduled', 'appuntamento'],
    ['agenda-dot agenda-dot-scheduled agenda-dot-visit', 'visita immobile'],
    ['agenda-dot agenda-dot-requested', 'richiesta']]) {
    const voce = el('span', 'agenda-month-key-item');
    voce.append(el('span', classe), el('span', '', testo));
    chiave.appendChild(voce);
  }
  contenitore.appendChild(chiave);
  target.appendChild(contenitore);
  if (onSelect) {
    const nelMese = scelto && scelto.slice(0, 7) === dayKey.slice(0, 7) ? scelto : dayKey;
    mostra(nelMese);
  }
}
