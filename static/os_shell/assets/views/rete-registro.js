// STIMA360 OS — views/rete-registro.js
// ADM-7 — la scheda «Registro» della Rete: le operazioni amministrative, in
// SOLA LETTURA, dal piu' recente.
//
// ENDPOINT USATI (gia' esistenti, ADM-5A):
//   GET /api/platform/audit?action&result&agency_id&target_type&target_id&since&until&limit&offset
//   GET /api/platform/agencies   (solo per dare un nome alle agenzie)
//
// Le righe sono mostrate come il backend le ha scritte: chi ha agito e' l'id e
// l'etichetta originali. Le ammissioni alla superficie (`platform.admission`)
// non compaiono, salvo sceglierle nel filtro «Azione». Ogni dato del backend
// passa da `escapeHtml`.

import { apiGet } from '../core/api-client.js';
import { renderTable, renderBadge, formatDateTime } from '../components/st-table.js';
import { PLATFORM, errorBox, escapeHtml, labelOf, selectOptions } from '../components/network.js';

const PAGINA = 50;

const AZIONI = {
  'platform.agency.create': 'Agenzia creata',
  'platform.agency.update': 'Agenzia modificata',
  'platform.agency.configuration.update': 'Configurazione agenzia',
  'platform.owner.transfer': 'Titolare trasferito',
  'platform.operator.create': 'Operatore creato',
  'platform.operator.update': 'Operatore modificato',
  'platform.operator.platform_admin.update': 'Ruolo di piattaforma',
  'platform.membership.create': 'Membership creata',
  'platform.membership.update': 'Membership modificata',
  'platform.territory.create': 'Territorio creato',
  'platform.territory.transfer': 'Territorio trasferito',
  'platform.territory.assignment.create': 'Assegnazione creata',
  'platform.territory.assignment.update': 'Assegnazione modificata',
  'platform.territory.alias.create': 'Alias dichiarato',
  'platform.territory.alias.update': 'Alias modificato',
  'platform.acting.enter': 'Ingresso in agenzia',
  'platform.acting.exit': 'Uscita da agenzia',
  'platform.super_admin.required': 'Operazione negata (non designato)',
  'platform.admission': 'Accesso alla piattaforma',
  // Fase B: le informative privacy.
  'platform.consent_notice.create': 'Informativa pubblicata',
  'platform.consent_notice.close': 'Informativa chiusa',
};

const ESITI = { success: 'Riuscita', denied: 'Negata', error: 'Errore' };
const TONO_ESITO = { success: 'ok', denied: 'danger', error: 'warn' };

const OGGETTI = {
  agency: 'Agenzia',
  operator: 'Operatore',
  territory: 'Territorio',
  territory_assignment: 'Assegnazione',
  territory_alias: 'Alias',
  consent_notice: 'Informativa',
};

const STATI = {
  active: 'attivo', suspended: 'sospeso', archived: 'archiviato', disabled: 'disabilitato',
  invited: 'invitato', revoked: 'revocato',
};

function dettagli(riga) {
  const m = riga.metadata || {};
  const parti = [];
  if (m.status_change) {
    parti.push(`stato: ${labelOf(STATI, m.status_change.from)} → ${labelOf(STATI, m.status_change.to)}`);
  }
  if (m.platform_admin) {
    parti.push(`ruolo di piattaforma: ${m.platform_admin.from ? 'si’' : 'no'} → ${m.platform_admin.to ? 'si’' : 'no'}`);
  }
  if (m.read_only === true) parti.push('sola lettura');
  if (Array.isArray(m.changed_fields) && !m.status_change && !m.platform_admin) {
    parti.push(`campi: ${m.changed_fields.join(', ')}`);
  }
  return parti.length ? escapeHtml(parti.join(' · ')) : '<span class="muted">—</span>';
}

// Una data del campo «dal»/«al» diventa un istante con fuso orario: l'inizio
// del giorno locale. «al» e' escluso, quindi si usa il giorno DOPO.
function inizioGiorno(valore, giorniDopo = 0) {
  if (!valore) return '';
  const d = new Date(`${valore}T00:00:00`);
  if (Number.isNaN(d.getTime())) return '';
  d.setDate(d.getDate() + giorniDopo);
  return d.toISOString();
}

export async function renderRegistro(container) {
  const filtri = { action: '', result: '', agency_id: '', target_type: '', target_id: '', dal: '', al: '' };
  let offset = 0;
  let agenzie = new Map();

  container.innerHTML = '<p class="muted">Caricamento…</p>';
  try {
    const elenco = await apiGet(`${PLATFORM}/agencies`);
    agenzie = new Map((Array.isArray(elenco) ? elenco : []).map((a) => [String(a.id), a.name]));
  } catch (_ignorato) {
    agenzie = new Map();
  }
  const opzioniAgenzie = Object.fromEntries([...agenzie.entries()]);

  container.innerHTML = `
    <p class="muted">Le operazioni amministrative della rete, dalla piu’ recente. Sola lettura: il registro non si modifica e non si cancella.</p>
    <form id="registro-filtri" class="list-toolbar registro-filtri">
      <select class="input" id="reg-azione" aria-label="Azione">
        <option value="">Tutte le operazioni</option>
        ${selectOptions(AZIONI, '')}
      </select>
      <select class="input" id="reg-esito" aria-label="Esito">
        <option value="">Ogni esito</option>
        ${selectOptions(ESITI, '')}
      </select>
      <select class="input" id="reg-agenzia" aria-label="Agenzia">
        <option value="">Ogni agenzia</option>
        ${selectOptions(opzioniAgenzie, '')}
      </select>
      <select class="input" id="reg-oggetto" aria-label="Oggetto">
        <option value="">Ogni oggetto</option>
        ${selectOptions(OGGETTI, '')}
      </select>
      <input class="input" id="reg-id" inputmode="numeric" placeholder="Id oggetto" aria-label="Id oggetto" maxlength="100">
      <label class="muted">dal <input class="input" type="date" id="reg-dal"></label>
      <label class="muted">al <input class="input" type="date" id="reg-al"></label>
      <button type="submit" class="btn primary">Filtra</button>
    </form>
    <div id="registro-lista"><p class="muted">Caricamento…</p></div>
    <div class="list-pager">
      <button type="button" class="btn ghost" id="reg-prec">← Precedenti</button>
      <span class="muted" id="reg-pagina"></span>
      <button type="button" class="btn ghost" id="reg-succ">Successivi →</button>
    </div>
  `;

  const listaEl = container.querySelector('#registro-lista');
  const precBtn = container.querySelector('#reg-prec');
  const succBtn = container.querySelector('#reg-succ');
  const paginaEl = container.querySelector('#reg-pagina');

  container.querySelector('#registro-filtri').addEventListener('submit', (event) => {
    event.preventDefault();
    const val = (id) => container.querySelector(id).value.trim();
    Object.assign(filtri, {
      action: val('#reg-azione'), result: val('#reg-esito'), agency_id: val('#reg-agenzia'),
      target_type: val('#reg-oggetto'), target_id: val('#reg-id'), dal: val('#reg-dal'), al: val('#reg-al'),
    });
    offset = 0;
    carica();
  });
  precBtn.addEventListener('click', () => { offset = Math.max(0, offset - PAGINA); carica(); });
  succBtn.addEventListener('click', () => { offset += PAGINA; carica(); });

  async function carica() {
    listaEl.innerHTML = '<p class="muted">Caricamento…</p>';
    const params = new URLSearchParams({ limit: String(PAGINA), offset: String(offset) });
    for (const chiave of ['action', 'result', 'agency_id', 'target_type', 'target_id']) {
      if (filtri[chiave]) params.set(chiave, filtri[chiave]);
    }
    const since = inizioGiorno(filtri.dal);
    const until = inizioGiorno(filtri.al, 1);
    if (since) params.set('since', since);
    if (until) params.set('until', until);

    let righe;
    try {
      righe = await apiGet(`${PLATFORM}/audit?${params.toString()}`);
    } catch (error) {
      listaEl.innerHTML = errorBox(error);
      precBtn.disabled = offset === 0;
      succBtn.disabled = true;
      return;
    }
    righe = Array.isArray(righe) ? righe : [];
    listaEl.innerHTML = renderTable(
      [
        { label: 'Quando', render: (r) => escapeHtml(formatDateTime(r.created_at)) },
        { label: 'Chi', render: (r) => `${escapeHtml(r.actor_label)}${r.actor_user_id != null ? ` <span class="muted">#${escapeHtml(r.actor_user_id)}</span>` : ''}` },
        { label: 'Operazione', render: (r) => escapeHtml(AZIONI[r.action] || r.action) },
        { label: 'Esito', render: (r) => renderBadge(labelOf(ESITI, r.result), TONO_ESITO[r.result] || 'gray') },
        {
          label: 'Oggetto',
          render: (r) => (r.target_type
            ? `${escapeHtml(labelOf(OGGETTI, r.target_type))}${r.target_id != null ? ` #${escapeHtml(r.target_id)}` : ''}`
            : '<span class="muted">—</span>'),
        },
        {
          label: 'Agenzia',
          render: (r) => (r.target_agency_id != null
            ? escapeHtml(agenzie.get(String(r.target_agency_id)) || `#${r.target_agency_id}`)
            : '<span class="muted">—</span>'),
        },
        { label: 'Dettagli', render: (r) => dettagli(r) },
      ],
      righe,
      { emptyMessage: 'Nessuna operazione con questi filtri.' },
    );
    precBtn.disabled = offset === 0;
    succBtn.disabled = righe.length < PAGINA;
    paginaEl.textContent = righe.length ? `${offset + 1}–${offset + righe.length}` : '';
  }

  await carica();
}
