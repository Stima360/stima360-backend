// STIMA360 OS — st-table.js
// Componente condiviso minimo: tabella HTML da un elenco di colonne + righe.
// Nessun framework, nessuna libreria: una funzione pura che produce una stringa
// HTML, coerente con lo stile già usato in P0 (oggi.js). Introdotta in P1 perché
// riusata da almeno 3 punti (lista Contatti, e piu' liste dentro la scheda
// Contatto: immobili, richieste, abbinamenti, visite, attivita, task).

export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// columns: [{ label: string, render: (row) => string (HTML già escapato dal chiamante) }]
// rows: array di oggetti dato
// options.emptyMessage: testo mostrato se rows è vuoto/nullo
// options.onRowClick: se presente, marca le righe come cliccabili (data-row-id)
export function renderTable(columns, rows, options = {}) {
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    return `<p class="muted">${escapeHtml(options.emptyMessage || 'Nessun elemento.')}</p>`;
  }
  const clickable = options.onRowClick === true || typeof options.onRowClick === 'function';
  const head = `<tr>${columns.map((c) => `<th>${escapeHtml(c.label)}</th>`).join('')}</tr>`;
  const body = items.map((row) => {
    const cells = columns.map((c) => `<td>${c.render(row)}</td>`).join('');
    return `<tr class="${clickable ? 'row-clickable' : ''}" data-row-id="${escapeHtml(row.id)}">${cells}</tr>`;
  }).join('');
  return `<div class="table-wrap"><table class="st-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

// Collega il click riga dopo l'inserimento in DOM (deve essere chiamato dal
// chiamante subito dopo aver impostato innerHTML, passando lo stesso container).
export function bindTableRowClicks(container, onRowClick) {
  if (typeof onRowClick !== 'function') return;
  container.querySelectorAll('tr.row-clickable[data-row-id]').forEach((tr) => {
    tr.addEventListener('click', () => onRowClick(tr.dataset.rowId));
  });
}

export function renderBadge(text, tone = 'gray') {
  return `<span class="badge badge-${escapeHtml(tone)}">${escapeHtml(text)}</span>`;
}

export function formatDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('it-IT', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function formatDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
