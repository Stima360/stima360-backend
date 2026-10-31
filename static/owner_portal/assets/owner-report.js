// STIMA360 — Area proprietario: «Report attività» (BLOCCO-7).
//
// Il report dell'immobile selezionato, dal server
// (`/api/owner/portal/properties/{id}/report`), per il periodo scelto: contatti
// interessati, visite, proposte (solo conteggi), storico del prezzo richiesto.
// «Dato non disponibile» dove la fonte manca. Il PDF si scarica con la stessa
// sessione; si carica solo quando il proprietario lo chiede. Se l'autorizzazione all'immobile viene revocata, il server
// risponde «non trovato» e la sezione si svuota. Tutto via `textContent`.
(function () {
  'use strict';

  const API = '/api/owner/portal/properties';
  const NOT_AVAILABLE = 'Dato non disponibile';
  const section = document.getElementById('owner-report-section');
  const form = document.getElementById('owner-report-form');
  const from = document.getElementById('owner-report-from');
  const to = document.getElementById('owner-report-to');
  const pdf = document.getElementById('owner-report-pdf');
  const status = document.getElementById('owner-report-status');
  const content = document.getElementById('owner-report-content');
  if (!section || !form) return;
  let propertyId = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function money(value) {
    if (value === null || value === undefined || value === '') return NOT_AVAILABLE;
    const n = Number(value);
    return Number.isFinite(n) ? `€ ${n.toLocaleString('it-IT', { maximumFractionDigits: 0 })}` : NOT_AVAILABLE;
  }

  function day(value) {
    if (!value) return NOT_AVAILABLE;
    const [y, m, d] = String(value).slice(0, 10).split('-');
    return d && m && y ? `${d}/${m}/${y}` : NOT_AVAILABLE;
  }

  function query() {
    const p = new URLSearchParams();
    if (from.value) p.set('date_from', from.value);
    if (to.value) p.set('date_to', to.value);
    const q = p.toString();
    return q ? `?${q}` : '';
  }

  function row(label, value) {
    const r = el('div', 'owner-report-row');
    r.append(el('span', 'owner-report-label', label), el('strong', null, value));
    return r;
  }

  function render(rep) {
    content.replaceChildren();
    content.appendChild(el('p', 'owner-report-period', `Periodo: ${day(rep.period.from)} – ${day(rep.period.to)}`));
    const grid = el('div', 'owner-report-grid');
    grid.append(
      row('Prezzo richiesto attuale', money(rep.property.asking_price)),
      row('Contatti interessati', String(rep.interested_contacts.total)),
      row('Richieste ricevute', rep.requests.available ? String(rep.requests.total) : NOT_AVAILABLE),
      row('Visite svolte', String(rep.visits.completed)),
      row('Visite in programma', String(rep.visits.upcoming.count)),
      row('Proposte (situazione attuale)', String(rep.proposals.total)),
    );
    content.appendChild(grid);
    content.appendChild(el('h4', null, 'Storico del prezzo richiesto'));
    if (!rep.price_history.length) {
      content.appendChild(el('p', 'muted', 'Nessuna variazione del prezzo nel periodo.'));
    } else {
      const list = el('ul', 'owner-report-prices');
      for (const p of rep.price_history) {
        // Senza prezzo precedente e' la prima impostazione, non un dato mancante.
        const text = (p.old_price === null || p.old_price === undefined || p.old_price === '')
          ? `${day(p.date)}: prezzo iniziale ${money(p.new_price)}`
          : `${day(p.date)}: ${money(p.old_price)} → ${money(p.new_price)}`;
        list.appendChild(el('li', null, text));
      }
      content.appendChild(list);
    }
    content.appendChild(el('p', 'muted', 'I feedback delle visite pubblicati per te sono nella sezione «Feedback visite».'));
  }

  async function load() {
    if (propertyId === null) return;
    status.textContent = 'Caricamento del report…';
    try {
      const response = await fetch(`${API}/${encodeURIComponent(String(propertyId))}/report${query()}`,
        { credentials: 'include', cache: 'no-store' });
      if (response.status === 404) {
        section.hidden = true;
        content.replaceChildren();
        return;
      }
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const d = data && data.detail;
        status.textContent = (d && d.message) || 'Report non disponibile. Riprova.';
        return;
      }
      section.hidden = false;
      status.textContent = '';
      render(data);
    } catch (_e) {
      status.textContent = 'Connessione non disponibile. Riprova.';
    }
  }

  // Nessuna richiesta alla sola selezione dell'immobile: il report si carica
  // quando il proprietario lo chiede («Mostra report»).
  document.addEventListener('owner-report:property', (event) => {
    propertyId = event.detail && event.detail.propertyId;
    section.hidden = propertyId === null || propertyId === undefined;
    content.replaceChildren();
    status.textContent = '';
  });
  form.addEventListener('submit', (event) => { event.preventDefault(); load(); });
  pdf.addEventListener('click', async () => {
    if (propertyId === null) return;
    pdf.disabled = true;
    try {
      const response = await fetch(`${API}/${encodeURIComponent(String(propertyId))}/report.pdf${query()}`,
        { credentials: 'include', cache: 'no-store' });
      if (!response.ok) throw new Error('download');
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const a = el('a');
      a.href = href; a.download = `report-immobile-${propertyId}.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (_e) {
      status.textContent = 'Download non riuscito. Riprova.';
    } finally {
      pdf.disabled = false;
    }
  });
}());
