// STIMA360 OS — views/fogli-visita.js (FOGLI VISITA)
//
// La sezione «Fogli visita»: TUTTI i fogli visita dell'agenzia (per un agente: le sue visite),
// con data della visita, cliente, immobile, agente, stato della firma e ultima attività; ricerca,
// filtri per agente, periodo e stato; dal più recente. Aprendo un foglio (`#/fogli-visita/<id>`)
// si gestisce l'intera firma con LO STESSO riquadro dell'Agenda (`mountSignaturePanel`): stesso
// motore OTP, stesse API, stessi permessi, nessun secondo sistema e nessun documento duplicato.
//
// Dati SOLO da `GET /api/visit-sheets/overview` e dalle API già usate dall'Agenda. Tutto via
// DOM e `textContent`.

import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { mountSignaturePanel } from '../components/visit-sheets.js';

const API = '/api/visit-sheets';
const ESIGN = '/api/signature-requests';
const PAGINA = 30;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null && text !== '') node.textContent = String(text);
  return node;
}

function button(label, className, attr) {
  const b = el('button', className ? `btn ${className}` : 'btn', label);
  b.type = 'button';
  if (attr) b.setAttribute(attr, '');
  return b;
}

function when(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function giornoRoma(iso) {
  return new Date(iso).toLocaleDateString('sv-SE', { timeZone: 'Europe/Rome' });
}

function errorText(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Operazione non riuscita.';
}

async function scarica(url, filename, holder) {
  try {
    const response = await fetch(url, { credentials: 'include' });
    if (!response.ok) throw new Error('Download non riuscito.');
    const href = URL.createObjectURL(await response.blob());
    const a = el('a');
    a.href = href; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (error) {
    holder.appendChild(el('div', 'error-box', errorText(error)));
  }
}

// I filtri restano in memoria finché la pagina è aperta (nessuno storage del browser).
let filtri = { q: '', agent: '', period: '', state: '' };

function periodo(valore) {
  const oggi = new Date();
  const giorni = (n) => new Date(oggi.getTime() + n * 86400000).toISOString();
  return {
    '': {},
    past30: { from: giorni(-30), to: oggi.toISOString() },
    next30: { from: oggi.toISOString(), to: giorni(30) },
    past90: { from: giorni(-90), to: oggi.toISOString() },
  }[valore] || {};
}

export async function renderFogliVisita(container, params = []) {
  const id = Number(params[0]);
  if (Number.isInteger(id) && id > 0) return renderDettaglio(container, id);
  return renderElenco(container);
}

async function renderElenco(container) {
  container.replaceChildren();
  const pagina = el('div', 'sheets-page');
  pagina.setAttribute('data-sheets-page', '');
  container.appendChild(pagina);
  pagina.appendChild(el('p', 'muted sheets-intro', 'Tutti i fogli visita: chi li deve firmare, chi ha firmato, cosa è in attesa.'));

  const barra = el('div', 'sheets-filters');
  barra.setAttribute('role', 'search');
  const cerca = el('input', 'input');
  cerca.type = 'search'; cerca.placeholder = 'Cerca cliente, immobile o indirizzo'; cerca.value = filtri.q;
  cerca.setAttribute('aria-label', 'Cerca'); cerca.setAttribute('data-sheets-search', '');
  const selezione = (nome, voci, valore, etichetta) => {
    const s = el('select', 'input');
    s.setAttribute('aria-label', etichetta); s.setAttribute(`data-sheets-${nome}`, '');
    for (const [v, t] of voci) { const o = el('option', null, t); o.value = v; s.appendChild(o); }
    s.value = valore;
    return s;
  };
  const selPeriodo = selezione('period', [['', 'Tutto il periodo'], ['past30', 'Ultimi 30 giorni'], ['past90', 'Ultimi 90 giorni'],
    ['next30', 'Prossimi 30 giorni']], filtri.period, 'Periodo');
  const selStato = selezione('state', [['', 'Tutti gli stati']], filtri.state, 'Stato');
  const selAgente = selezione('agent', [['', 'Tutti gli agenti']], filtri.agent, 'Agente');
  selAgente.hidden = true;
  barra.append(cerca, selStato, selPeriodo, selAgente);
  pagina.appendChild(barra);
  const avviso = el('div', 'sheets-notice');
  avviso.setAttribute('aria-live', 'polite');
  const elenco = el('ul', 'sheets-list');
  elenco.setAttribute('data-sheets-list', '');
  const altri = button('Mostra altri', 'ghost', 'data-sheets-more');
  altri.hidden = true;
  pagina.append(avviso, elenco, altri);

  let giro = 0;
  let offset = 0;
  async function carica(aggiungi = false) {
    const mio = ++giro;
    if (!aggiungi) offset = 0;
    avviso.replaceChildren(el('span', 'muted', 'Caricamento…'));
    const q = new URLSearchParams();
    if (filtri.q.trim()) q.set('q', filtri.q.trim());
    if (filtri.agent) q.set('agent_id', filtri.agent);
    if (filtri.state) q.set('state', filtri.state);
    const p = periodo(filtri.period);
    if (p.from) q.set('from', p.from);
    if (p.to) q.set('to', p.to);
    q.set('limit', String(PAGINA)); q.set('offset', String(offset));
    let data;
    try { data = await apiGet(`${API}/overview?${q.toString()}`); } catch (error) {
      if (mio !== giro) return;
      avviso.replaceChildren(el('div', 'error-box', errorText(error)));
      return;
    }
    if (mio !== giro || !pagina.isConnected) return;
    avviso.replaceChildren();
    if (selStato.options.length <= 1 && Array.isArray(data.states)) {
      for (const s of data.states) { const o = el('option', null, s.label); o.value = s.value; selStato.appendChild(o); }
      selStato.value = filtri.state;
    }
    if (Array.isArray(data.agents) && data.agents.length && selAgente.options.length <= 1) {
      for (const a of data.agents) { const o = el('option', null, a.name); o.value = String(a.id); selAgente.appendChild(o); }
      selAgente.value = filtri.agent;
      selAgente.hidden = false;
    }
    if (!aggiungi) elenco.replaceChildren();
    for (const item of data.items) elenco.appendChild(riga(item));
    offset += data.items.length;
    altri.hidden = offset >= data.total;
    if (!data.total) avviso.appendChild(el('p', 'muted', 'Nessun foglio visita con questi criteri.'));
    else avviso.appendChild(el('p', 'muted sheets-count', data.total === 1 ? '1 foglio visita' : `${data.total} fogli visita`));
  }

  function riga(item) {
    const li = el('li', `sheets-row sheets-state-${item.signature.state}`);
    li.setAttribute('data-sheets-row', String(item.appointment_id));
    li.setAttribute('data-sheets-row-state', item.signature.state);
    const apri = el('a', 'sheets-row-main');
    apri.href = `#/fogli-visita/${item.appointment_id}`;
    const testa = el('div', 'sheets-row-head');
    testa.append(el('strong', 'sheets-client', item.client_name || 'Cliente non indicato'),
      el('span', `sign-state sign-state-${item.signature.state}`, item.signature.list_label));
    apri.appendChild(testa);
    apri.appendChild(el('span', 'sheets-line', `Visita del ${when(item.visit_start_at)}`));
    const immobile = [item.property_code, item.property_address].filter(Boolean).join(' · ');
    if (immobile) apri.appendChild(el('span', 'sheets-line', immobile));
    if (item.agent_name) apri.appendChild(el('span', 'sheets-line muted', `Agente: ${item.agent_name}`));
    if (item.signature.state === 'completed' && item.signature.signed_at) {
      apri.appendChild(el('span', 'sheets-line', `Firmato da ${item.signature.signer_name || 'cliente'} il ${when(item.signature.signed_at)}`));
    }
    if (item.last_activity_at) apri.appendChild(el('span', 'sheets-line muted', `Ultima attività: ${when(item.last_activity_at)}`));
    else apri.appendChild(el('span', 'sheets-line muted', 'Foglio non ancora preparato'));
    li.appendChild(apri);
    if (item.signature.state === 'completed' && item.signature.request_id) {
      const azioni = el('div', 'sheets-row-actions');
      const pdf = button('Scarica PDF firmato', 'primary btn-small', 'data-sheets-download');
      pdf.addEventListener('click', () => scarica(`${ESIGN}/${item.signature.request_id}/document`, `foglio-visita-firmato-${item.appointment_id}.pdf`, li));
      azioni.appendChild(pdf);
      if (item.signature.has_audit_trail) {
        const prove = button('Rapporto delle prove', 'ghost btn-small', 'data-sheets-audit');
        prove.addEventListener('click', () => scarica(`${ESIGN}/${item.signature.request_id}/audit-trail`, `rapporto-prove-foglio-${item.appointment_id}.pdf`, li));
        azioni.appendChild(prove);
      }
      li.appendChild(azioni);
    }
    return li;
  }

  let attesa = null;
  cerca.addEventListener('input', () => {
    filtri = { ...filtri, q: cerca.value };
    clearTimeout(attesa);
    attesa = setTimeout(() => carica(), 300);
  });
  selStato.addEventListener('change', () => { filtri = { ...filtri, state: selStato.value }; carica(); });
  selPeriodo.addEventListener('change', () => { filtri = { ...filtri, period: selPeriodo.value }; carica(); });
  selAgente.addEventListener('change', () => { filtri = { ...filtri, agent: selAgente.value }; carica(); });
  altri.addEventListener('click', () => carica(true));
  await carica();
}

async function renderDettaglio(container, appointmentId) {
  container.replaceChildren();
  const pagina = el('div', 'sheets-page sheets-detail');
  pagina.setAttribute('data-sheets-detail', String(appointmentId));
  container.appendChild(pagina);
  const indietro = el('a', 'sheets-back', '← Tutti i fogli visita');
  indietro.href = '#/fogli-visita';
  pagina.appendChild(indietro);
  let data;
  try {
    data = await apiGet(`${API}/appointments/${appointmentId}`);
  } catch (error) {
    pagina.appendChild(el('div', 'error-box', error && error.status === 404 ? 'Foglio visita non trovato.' : errorText(error)));
    return;
  }
  const testa = el('section', 'card sheets-detail-head');
  testa.appendChild(el('h2', null, (data.client && data.client.name) || 'Foglio visita'));
  const righe = [
    ['Visita', when(data.start_at)],
    ['Immobile', [data.property && data.property.code, data.property && data.property.address].filter(Boolean).join(' · ')],
    ['Agente', data.agent && data.agent.name],
  ];
  const dl = el('dl', 'sheets-facts');
  for (const [k, v] of righe) {
    if (!v) continue;
    dl.append(el('dt', null, k), el('dd', null, v));
  }
  testa.appendChild(dl);
  const agenda = button('Apri l’appuntamento in Agenda', 'ghost', 'data-sheets-open-agenda');
  agenda.addEventListener('click', () => navigate('agenda', ['giorno', giornoRoma(data.start_at)]));
  testa.appendChild(agenda);
  pagina.appendChild(testa);

  // Il documento in vista (schermi larghi); sul telefono si apre dal riquadro («Visualizza PDF»).
  const ultimo = data.sheets[0];
  if (ultimo && typeof window.matchMedia === 'function' && window.matchMedia('(min-width: 1000px)').matches) {
    try {
      const response = await fetch(`${API}/${ultimo.id}/pdf`, { credentials: 'include' });
      if (response.ok) {
        const frame = el('iframe', 'sheets-pdf');
        frame.title = 'Foglio visita';
        frame.src = URL.createObjectURL(await response.blob());
        frame.setAttribute('data-sheets-pdf', '');
        pagina.appendChild(frame);
      }
    } catch (error) { /* il riquadro offre comunque «Visualizza documento» */ }
  }
  const pannello = el('section', 'card sheets-panel');
  pagina.appendChild(pannello);
  if (!data.can_generate && !data.sheets.length) {
    pannello.appendChild(el('p', 'muted', 'Il foglio si prepara per una visita fissata, confermata o svolta.'));
    return;
  }
  await mountSignaturePanel(pannello, { appointmentId });
}
