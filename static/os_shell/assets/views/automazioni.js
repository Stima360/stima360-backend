// STIMA360 OS — automazioni.js
// Sezione "Automazioni": vista operativa di FLOW (motore di automazione),
// mai un pannello tecnico/editor di regole. FLOW resta proprietario di
// registry/condizioni/esecuzione/storico: questo file legge solo le API
// FLOW già esistenti (flow/router.py), nessuna condizione o azione viene
// ricostruita lato browser (nessuna duplicazione di flow/engine.py in JS).
//
// Endpoint reali verificati (flow/router.py, prefix /api/flow, TUTTI protetti
// da require_owner_admin — stessa auth Basic già usata da tutta la OS Shell,
// nessun lavoro di autenticazione aggiuntivo):
//   GET  /api/flow/dashboard                         (router.py:61 — query
//        singola aggregata, vedi flow/repository.py:dashboard: active_rules,
//        total_events, executed, failed, skipped, tasks_created,
//        active_suppressions. Sono conteggi TOTALI storici, mai "oggi": la UI
//        li etichetta sempre come tali.)
//   GET  /api/flow/rules                              (router.py:19 — { items }
//        SENZA paginazione: 12 regole reali e fisse in flow/rules/registry.py,
//        elenco intero sempre, nessun limit/offset da gestire)
//   GET  /api/flow/executions?limit&offset&status      (router.py:47 — { items },
//        limit reale 1-500, offset reale, status reale filtro singolo valore;
//        flow/repository.py:list_executions fa già JOIN su flow_rules per
//        rule_code/rule_name: NESSUNA chiamata N+1 per riga per mostrare il
//        nome dell'automazione)
//
// Cosa NON viene esposto qui, e perché (letto flow/router.py e flow/service.py
// per intero prima di decidere, non assunto):
//  - POST /rules/{code}/simulate: endpoint di test genuinamente sicuro
//    (flow/service.py:simulate non chiama mai execute_live, verificato) ma
//    richiede un selettore entità (entity_type/entity_id) per funzionare:
//    costruire quella UI assomiglierebbe a una console di test/debug, uno
//    degli anti-obiettivi espliciti di P6. Rimandato a un blocco futuro,
//    riportato come gap nel report.
//  - POST /evaluate (mode=live), POST /scan, POST /executions/{id}/retry:
//    eseguono azioni REALI (creano task veri, flow/repository.py:execute_live)
//    e /scan in particolare opera in batch su più entità: nessun pulsante
//    "esegui ora" viene esposto, come richiesto esplicitamente dal brief.
//  - PATCH /rules/{code}/parameters, POST /rules/{code}/reset-parameters:
//    editor di parametri regola — esplicitamente fuori scope ("P6 non è un
//    editor di regole"). I parametri correnti (JSON tecnico) non vengono
//    mai mostrati in questa vista né nella scheda dettaglio.
//  - error_message: flow_executions.error_message contiene la stringa grezza
//    dell'eccezione Python (str(exc), verificato in flow/repository.py:
//    execute_live/record_failure, nessuna distinzione sicuro/tecnico trovata
//    nello schema). Non viene MAI mostrato: per le esecuzioni fallite si
//    mostra solo un'etichetta operativa generica ("Non riuscita").
//  - conditions_result.reasons: MOSTRATO, perché sono stringhe italiane
//    sicure e statiche già definite in flow/engine.py (es. "giorni alla
//    scadenza: 12", "criticità documentali: 3"), mai testo tecnico o
//    interpolazione di dati arbitrari dell'utente.
//  - actions_result.task_id: letto SOLO dalla riga già ottenuta (nessuna
//    chiamata aggiuntiva: flow/repository.py:execute_live scrive
//    actions_result={'task_id':...} direttamente sulla riga flow_executions
//    al momento dell'esecuzione, verificato). task_id non ha una rotta
//    dedicata nella OS Shell (nessuna vista "task singolo"): viene mostrato
//    come indicazione testuale ("Task creato"), mai come link inventato.
//  - Filtro per singola regola in Cronologia: GET /api/flow/executions non
//    supporta un filtro rule_code (solo status). Filtrarlo lato client su un
//    elenco già paginato mostrerebbe un sottoinsieme presentato come
//    completo, quindi non è stato aggiunto: unico filtro reale è "stato".
//
// Collegamenti a entità (regola N+1 applicata rigorosamente): flow_executions
// ha SOLO entity_type/entity_id generici (migrations/008_flow_01.sql), non
// contact_id/property_id/buy_request_id dedicati. entity_id coincide con
// l'id reale della entità solo per property/buy_request/match (verificato in
// flow/adapters.py:load_entity): per questi si genera un link diretto
// (#/immobili/{id}, #/acquirenti/{id}, #/abbinamenti/{id}), zero chiamate
// aggiuntive. Per "lead" l'id in flow_executions è l'id del lead, non del
// contatto (leads.contact_id esiste ma richiederebbe una lookup per riga):
// mostrato come testo semplice, mai un link inventato. Stesso trattamento
// per "property_visit" e "owner_feedback" (nessuna rotta OS dedicata).
//
// Resilienza: Panoramica/Regole/Cronologia sono tre tab caricate in modo
// indipendente (mai un Promise.all rigido che le lega); un fallimento in una
// tab non impedisce la consultazione delle altre.

import { apiGet, apiPost } from '../core/api-client.js';
import { getCredentials } from '../core/auth.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, renderBadge, escapeHtml, formatDateTime } from '../components/st-table.js';

const PAGE_SIZE = 50;

const ENTITY_TYPE_LABELS = {
  lead: 'Lead',
  property: 'Immobile',
  buy_request: 'Richiesta acquirente',
  match: 'Abbinamento',
  property_visit: 'Visita immobile',
  owner_feedback: 'Richiesta proprietario',
};

// Solo questi tre entity_type hanno un id compatibile con una rotta OS reale
// (verificato in flow/adapters.py: per gli altri l'id in flow_executions non
// coincide con l'id della rotta corrispondente, o non esiste alcuna rotta).
const ENTITY_LINK_ROUTES = {
  property: 'immobili',
  buy_request: 'acquirenti',
  match: 'abbinamenti',
};

const PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };

const SIMULATION_STATUS_LABELS = {
  never_run: 'Mai verificata',
  success: 'Verifica superata',
  failed: 'Verifica non superata',
  outdated: 'Verifica non più valida (regola aggiornata)',
};

const EXECUTION_STATUS_LABELS = {
  matched: 'Condizioni soddisfatte (simulazione)',
  not_matched: 'Condizioni non soddisfatte',
  executed: 'Eseguita',
  skipped: 'Saltata (duplicata o in raffreddamento)',
  failed: 'Non riuscita',
};

const EXECUTION_STATUS_TONE = {
  matched: 'warn',
  not_matched: 'gray',
  executed: 'ok',
  skipped: 'warn',
  failed: 'danger',
};

const EXECUTION_STATUS_FILTERS = ['', 'matched', 'not_matched', 'executed', 'skipped', 'failed'];

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'regole', label: 'Regole' },
  { key: 'cronologia', label: 'Cronologia' },
];

export async function renderAutomazioni(container) {
  const state = {
    activeTab: 'panoramica',
    cache: { panoramica: null, regole: null },
    cronologia: { offset: 0, status: '', items: [] },
  };

  container.innerHTML = `
    <div class="tabs" id="automazioni-tabs">
      ${TABS.map((t) => `<button type="button" class="tab-btn" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('')}
    </div>
    <div id="automazioni-tab-content"><p class="muted">Caricamento…</p></div>
  `;

  const tabsEl = container.querySelector('#automazioni-tabs');
  const contentEl = container.querySelector('#automazioni-tab-content');

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  async function showTab(key) {
    state.activeTab = key;
    tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === key);
    });
    if (key === 'panoramica') return loadPanoramica();
    if (key === 'regole') return loadRegole();
    if (key === 'cronologia') return loadCronologia();
  }

  async function loadPanoramica(force = false) {
    if (state.cache.panoramica && !force) {
      contentEl.innerHTML = renderPanoramicaTab(state.cache.panoramica);
      return;
    }
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      const dashboard = await apiGet('/api/flow/dashboard');
      state.cache.panoramica = dashboard;
      contentEl.innerHTML = renderPanoramicaTab(dashboard);
    } catch (error) {
      contentEl.innerHTML = `<div class="error-box">Riepilogo automazioni temporaneamente non disponibile: ${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadRegole(force = false) {
    if (state.cache.regole && !force) {
      contentEl.innerHTML = renderRegoleTab(state.cache.regole);
      bindRegoleActions(state.cache.regole);
      return;
    }
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      const data = await apiGet('/api/flow/rules');
      const rules = Array.isArray(data?.items) ? data.items : [];
      state.cache.regole = rules;
      contentEl.innerHTML = renderRegoleTab(rules);
      bindRegoleActions(rules);
    } catch (error) {
      contentEl.innerHTML = `<div class="error-box">Impossibile caricare l'elenco delle automazioni: ${escapeHtml(error.message)}</div>`;
    }
  }

  function bindRegoleActions(rules) {
    bindTableRowClicks(contentEl, (id) => {
      const rule = rules.find((r) => String(r.id) === String(id));
      if (rule) navigate('automazioni', [rule.code]);
    });
  }

  async function loadCronologia() {
    contentEl.innerHTML = renderCronologiaShell(state.cronologia.status);
    await fetchCronologiaPage();
    bindCronologiaFilter();
  }

  function bindCronologiaFilter() {
    const filterSelect = contentEl.querySelector('#cronologia-status-filter');
    if (filterSelect) {
      filterSelect.addEventListener('change', async () => {
        state.cronologia.status = filterSelect.value;
        state.cronologia.offset = 0;
        await fetchCronologiaPage();
      });
    }
  }

  async function fetchCronologiaPage() {
    const listArea = contentEl.querySelector('#cronologia-list-area');
    const pagerArea = contentEl.querySelector('#cronologia-pager');
    if (!listArea || !pagerArea) return;
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.cronologia.offset) });
    if (state.cronologia.status) params.set('status', state.cronologia.status);
    let items = [];
    try {
      const data = await apiGet(`/api/flow/executions?${params.toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare la cronologia: ${escapeHtml(error.message)}</div>`;
      return;
    }
    state.cronologia.items = items;
    // Nessuna scheda "esecuzione singola" dedicata (GET /executions/{id} non
    // aggiunge nulla di operativamente utile rispetto a questa riga: solo
    // JSON tecnico raw, esplicitamente fuori scope). Le righe non sono quindi
    // cliccabili; solo i link "Riferimento" verso l'entità lo sono.
    listArea.innerHTML = renderCronologiaTable(items, Boolean(state.cronologia.status));
    listArea.querySelectorAll('a[data-entity-route]').forEach((a) => {
      a.addEventListener('click', (event) => {
        event.preventDefault();
        navigate(a.dataset.entityRoute, [a.dataset.entityId]);
      });
    });
    pagerArea.innerHTML = `
      <button class="btn" id="cronologia-prev" ${state.cronologia.offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? state.cronologia.offset + 1 : 0} a ${state.cronologia.offset + items.length}</span>
      <button class="btn" id="cronologia-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prevBtn = pagerArea.querySelector('#cronologia-prev');
    const nextBtn = pagerArea.querySelector('#cronologia-next');
    if (prevBtn) prevBtn.onclick = () => { state.cronologia.offset = Math.max(0, state.cronologia.offset - PAGE_SIZE); fetchCronologiaPage(); };
    if (nextBtn) nextBtn.onclick = () => { state.cronologia.offset += PAGE_SIZE; fetchCronologiaPage(); };
  }

  showTab('panoramica');
}

function renderPanoramicaTab(d) {
  const kpis = [
    ['Regole attive', d.active_rules],
    ['Eventi ricevuti (totale)', d.total_events],
    ['Esecuzioni riuscite (totale)', d.executed],
    ['Esecuzioni non riuscite (totale)', d.failed],
    ['Esecuzioni saltate (totale)', d.skipped],
    ['Task creati dalle automazioni (totale)', d.tasks_created],
    ['Soppressioni attive', d.active_suppressions],
  ];
  return `
    <p class="muted">Conteggi complessivi da quando FLOW è in funzione (non solo di oggi).</p>
    <div class="kpi-grid">
      ${kpis.map(([label, value]) => `
        <div class="card kpi">
          <span class="kpi-label">${escapeHtml(label)}</span>
          <strong class="kpi-value">${escapeHtml(value ?? 0)}</strong>
        </div>
      `).join('')}
    </div>
  `;
}

function renderRegoleTab(rules) {
  return renderTable(
    [
      { label: 'Automazione', render: (r) => `<strong>${escapeHtml(r.name)}</strong><br><small class="muted">${escapeHtml(r.description || '—')}</small>` },
      { label: 'Ambito', render: (r) => escapeHtml(ENTITY_TYPE_LABELS[r.entity_type] || r.entity_type) },
      { label: 'Stato', render: (r) => r.is_active ? renderBadge('Attiva', 'ok') : renderBadge('Non attiva', 'gray') },
      { label: 'Priorità', render: (r) => escapeHtml(PRIORITY_LABELS[r.priority] || r.priority || '—') },
      { label: 'Verifica di sicurezza', render: (r) => renderBadge(SIMULATION_STATUS_LABELS[r.last_simulation_status] || r.last_simulation_status || '—', simulationTone(r.last_simulation_status)) },
    ],
    rules,
    { emptyMessage: 'Nessuna automazione disponibile.', onRowClick: true },
  );
}

function simulationTone(status) {
  if (status === 'success') return 'ok';
  if (status === 'failed' || status === 'outdated') return 'danger';
  return 'gray';
}

function renderCronologiaShell(currentStatus) {
  return `
    <div class="list-toolbar">
      <select id="cronologia-status-filter" class="input">
        ${EXECUTION_STATUS_FILTERS.map((s) => `<option value="${s}" ${s === currentStatus ? 'selected' : ''}>${s ? escapeHtml(EXECUTION_STATUS_LABELS[s]) : 'Tutti gli esiti'}</option>`).join('')}
      </select>
    </div>
    <div id="cronologia-list-area"><p class="muted">Caricamento…</p></div>
    <div id="cronologia-pager" class="list-pager"></div>
  `;
}

function renderCronologiaTable(items, filtered) {
  return renderTable(
    [
      { label: 'Automazione', render: (e) => `<strong>${escapeHtml(e.rule_name || e.rule_code)}</strong>` },
      { label: 'Riferimento', render: (e) => renderEntityCell(e) },
      { label: 'Modalità', render: (e) => e.execution_mode === 'live' ? renderBadge('Reale', 'ok') : renderBadge('Simulazione', 'gray') },
      { label: 'Esito', render: (e) => renderBadge(EXECUTION_STATUS_LABELS[e.status] || e.status || '—', EXECUTION_STATUS_TONE[e.status] || 'gray') },
      { label: 'Motivo', render: (e) => renderReasonsCell(e) },
      { label: 'Task generato', render: (e) => (e.actions_result && e.actions_result.task_id) ? renderBadge('Task creato', 'ok') : '' },
      { label: 'Quando', render: (e) => escapeHtml(formatDateTime(e.completed_at || e.created_at)) },
    ],
    items,
    { emptyMessage: filtered ? 'Nessuna esecuzione per questo esito.' : 'Nessuna esecuzione registrata.' },
  );
}

function renderEntityCell(e) {
  const label = ENTITY_TYPE_LABELS[e.entity_type] || e.entity_type;
  const route = ENTITY_LINK_ROUTES[e.entity_type];
  if (route) {
    return `<a href="#/${escapeHtml(route)}/${escapeHtml(e.entity_id)}" data-entity-route="${escapeHtml(route)}" data-entity-id="${escapeHtml(e.entity_id)}">${escapeHtml(label)} #${escapeHtml(e.entity_id)}</a>`;
  }
  return `${escapeHtml(label)} #${escapeHtml(e.entity_id)}`;
}

function renderReasonsCell(e) {
  const reasons = e.conditions_result && Array.isArray(e.conditions_result.reasons) ? e.conditions_result.reasons : [];
  if (!reasons.length) return e.status === 'failed' ? escapeHtml('Non riuscita') : '—';
  return escapeHtml(reasons.join('; '));
}
