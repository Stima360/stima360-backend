// STIMA360 OS — components/sort-toggle.js
// ORDINAMENTO ALFABETICO: il comando «A–Z | Z–A» degli elenchi Contatti,
// Immobili e Incarichi. L'ordine lo fa il SERVER (gli elenchi sono paginati:
// riordinare nel browser riordinerebbe solo la pagina visibile), con la stessa
// regola per tutti (core/alpha_sort.py: accenti e maiuscole non contano, i
// nomi vuoti vanno in fondo). A–Z è l'ordine iniziale; la scelta resta per
// quell'elenco mentre si naviga nel CRM: la Shell è una sola pagina, quindi
// basta la memoria del modulo. NESSUNO storage del browser (regola della Shell:
// l'unica eccezione è la vista preferita dell'Agenda); ricaricando si riparte da A–Z.

export const ASC = 'asc';
export const DESC = 'desc';
const chosen = new Map();

/** La scelta di questa sessione per un elenco, oppure `fallback`. Mai un valore inatteso. */
export function readSort(listKey, fallback = ASC) {
  const v = chosen.get(listKey);
  return typeof v === 'string' && /^[a-z_]{1,30}$/.test(v) ? v : fallback;
}

export function saveSort(listKey, value) {
  chosen.set(listKey, value);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/**
 * Monta il comando in `host`. `current` è 'asc' | 'desc' | altro (un ordine
 * diverso, es. «Scadenza» negli Incarichi: nessuno dei due pulsanti è attivo).
 * `onChange(direction)` alla scelta. Ritorna `{ set(value) }` per aggiornare
 * lo stato quando l'ordine cambia da un altro comando.
 */
export function mountSortToggle(host, { current = ASC, label = 'Ordine alfabetico', onChange }) {
  const group = el('div', 'sort-toggle');
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', label);
  group.setAttribute('data-sort-toggle', '');
  const buttons = {};
  for (const [value, text, title] of [[ASC, 'A–Z', 'Ordine alfabetico crescente'], [DESC, 'Z–A', 'Ordine alfabetico decrescente']]) {
    const b = el('button', 'sort-toggle-btn', text);
    b.type = 'button';
    b.title = title;
    b.setAttribute('data-sort', value);
    b.addEventListener('click', () => { set(value); if (onChange) onChange(value); });
    buttons[value] = b;
    group.appendChild(b);
  }
  function set(value) {
    for (const [v, b] of Object.entries(buttons)) {
      const on = v === value;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', String(on));
    }
  }
  set(current);
  host.replaceChildren(group);
  return { set };
}
