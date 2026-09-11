import { apiGet } from '../core/api-client.js';
import { searchGlobal } from '../core/global-search.js';
import { onAuthChange } from '../core/auth.js';

export function searchResultHref(item) {
  if (!Number.isSafeInteger(Number(item.id)) || Number(item.id) <= 0) return null;
  const routes = { contact: 'contatti', property: 'immobili', buy: 'acquirenti', match: 'abbinamenti' };
  if (item.type === 'lead') {
    if (!Number.isSafeInteger(Number(item.contact_id)) || Number(item.contact_id) <= 0) return null;
    return `#/contatti/${Number(item.contact_id)}/lead/${Number(item.id)}`;
  }
  return routes[item.type] ? `#/${routes[item.type]}/${Number(item.id)}` : null;
}

export function mountGlobalSearch(container) {
  const wrap = document.createElement('div');
  wrap.className = 'card';
  const label = document.createElement('label');
  label.textContent = 'Cerca ovunque';
  const input = document.createElement('input');
  input.type = 'search';
  input.className = 'input';
  input.maxLength = 200;
  input.placeholder = 'Contatti, immobili, richieste; lead tramite contatto';
  const results = document.createElement('div');
  results.setAttribute('aria-live', 'polite');
  results.hidden = true;
  label.append(input);
  wrap.append(label, results);
  container.prepend(wrap);
  let timer;
  let sequence = 0;
  function clear() {
    clearTimeout(timer);
    sequence += 1;
    results.replaceChildren();
    results.hidden = true;
  }
  input.addEventListener('input', () => {
    clear();
    const query = input.value.trim().slice(0, 200);
    if (query.length < 2) return;
    const requestSequence = sequence;
    timer = setTimeout(async () => {
      results.hidden = false;
      results.textContent = 'Ricerca…';
      try {
        const result = await searchGlobal(query, apiGet, { includeLeads: true });
        if (requestSequence !== sequence) return;
        results.replaceChildren();
        for (const item of result.items) {
          const href = searchResultHref(item);
          if (!href) continue;
          const link = document.createElement('a');
          link.className = 'list-item';
          link.href = href;
          link.textContent = `${item.typeLabel}: ${item.title} — ${item.subtitle || ''}`;
          link.addEventListener('click', clear);
          results.append(link);
        }
        if (!results.childNodes.length) results.textContent = result.unavailable ? 'Ricerca temporaneamente non disponibile.' : 'Nessun risultato';
        else if (result.failed) {
          const warning = document.createElement('p');
          warning.textContent = 'Alcune sezioni non sono disponibili.';
          results.append(warning);
        }
      } catch (error) {
        if (requestSequence === sequence) results.textContent = 'Ricerca temporaneamente non disponibile.';
      }
    }, 300);
  });
  input.addEventListener('keydown', (event) => { if (event.key === 'Escape') clear(); });
  document.addEventListener('click', (event) => { if (!wrap.contains(event.target)) clear(); });
  // P26-4: questo pannello vive FUORI dal container delle view, quindi il
  // router non lo svuota. Si ripulisce da solo a ogni transizione di sessione -
  // logout, scadenza, cambio di operatore - ed e' gia' cosi' da prima di P26-4.
  // Provato in tests/test_p26_4_shell_runtime.py: senza questa riga la query
  // digitata e i risultati dell'agenzia uscita resterebbero sullo schermo.
  onAuthChange(() => { clear(); input.value = ''; });
}
