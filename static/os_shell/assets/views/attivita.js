// STIMA360 OS — attivita.js
// Sezione "Attività": centro operativo giornaliero dell'agente.
// Distingue sempre TASK (cose da fare, tabella "tasks") da ATTIVITÀ
// (eventi passati, tabella "activities") e da VISITE (property_visits),
// senza mai confonderli in un'unica riga o in un'unica tabella.
//
// Endpoint usati (verificati nei router prima di scrivere questo file):
//   GET   /api/core/tasks?status=&limit=&offset=     (core/router.py:150-163 — status/limit/offset reali)
//   PATCH /api/core/tasks/{id}                       (core/router.py, TaskUpdate; service.py imposta/pulisce completed_at)
//   GET   /api/core/activities?limit=&offset=        (core/router.py:124-136 — nessun filtro status: le activities
//                                                       non hanno stato, sono sempre eventi passati; offset presente)
//   GET   /api/property/visits?status=&limit=&offset= (property/router.py:45-46 — status/limit/offset reali)
// Nessuna nuova API. Nessun dato inventato.
//
// MICRO-FIX paginazione/completezza (turno successivo alla prima versione di
// questo file): la prima versione scaricava GET .../tasks?limit=200 e
// GET .../visits?limit=200 SENZA filtro status, poi divideva aperti/chiusi
// lato client — con più di 200 righe totali in una di queste due tabelle,
// l'ordinamento SQL (due_at/scheduled_at) poteva escludere dalla finestra
// delle prime 200 righe dei task o delle visite realmente aperti. Verificato
// che sia /api/core/tasks sia /api/property/visits supportano status E
// offset realmente (query reali qui sopra): la strategia è stata quindi
// cambiata in "una chiamata per stato reale, paginata per offset finché il
// backend restituisce meno righe del limit richiesto" (fetchAllPages, sotto).
// Non è N+1: il numero di chiamate dipende dal numero di STATI reali (4 per
// i task, 5 per le visite — costanti, definiti negli enum backend) e dal
// numero di PAGINE per stato, mai dal numero di righe renderizzate. Ogni
// pagina ha un tetto di sicurezza (MAX_PAGES) contro loop anomali: se mai
// raggiunto, viene mostrato un avviso esplicito invece di dichiarare in
// silenzio un dataset completo che non lo è (vedi TRUNCATION_NOTICE).
// /api/core/activities NON ha alcun filtro status (le activities sono
// sempre eventi passati, non hanno bisogno di essere divise per stato):
// qui viene comunque paginata per offset fino a esaurimento, stesso motivo.
//
// Cosa NON viene usato, e perché (verificato leggendo il codice reale, non assunto):
//  - flow/router.py (/api/flow/*): l'intero router è protetto da require_owner_admin
//    (owner/router_admin.py) — stessa autenticazione dell'OS Shell, quindi
//    tecnicamente raggiungibile, MA le 7 regole attive in flow/rules/registry.py
//    hanno TUTTE action_type='create_core_task' (flow/repository.py:214-223):
//    l'automazione crea semplicemente un task CORE ordinario
//    (task_type='flow_follow_up', created_by='FLOW', metadata.source='flow').
//    Questi task sono quindi già presenti, in modo del tutto trasparente e non
//    tecnico, in GET /api/core/tasks: qui li segnaliamo con un badge
//    "Automazione" leggendo solo metadata.source, senza mai mostrare
//    flow_rule_code/execution_id o altri dettagli tecnici (richiesto dal brief:
//    FLOW non deve mai dominare la pagina né esporre internals).
//  - buy_request_interactions / buy_request_task_links come fonte diretta:
//    non esiste un endpoint GLOBALE (solo per-richiesta, buy/router.py:32-43),
//    quindi non possono alimentare un elenco cross-modulo senza N+1. Quando un
//    task porta metadata.buy_request_id (impostato da buy/repository.py:204 al
//    momento della creazione) lo leggiamo dalla riga già ottenuta — costo zero —
//    per offrire un link "Apri richiesta"; non lo usiamo mai come filtro server.
//  - match_id: nessuna colonna match_id su activities/tasks (migrations/001);
//    la cronologia dei Match ha già una propria sede dedicata (tab "Storico
//    ricalcoli"/"Feedback" in abbinamento-dettaglio.js, P4) e non va duplicata.
//  - Risoluzione contact_id/lead_id -> nome: core/repository.py non fa alcun
//    JOIN in list_activities/list_tasks (SELECT * puro) e non esiste un
//    endpoint per leggere più contatti per id in una sola chiamata: mostrare un
//    nome risolto qui richiederebbe N+1. Le righe Task/Attività mostrano quindi
//    solo un link "Apri contatto" verso #/contatti/{id} (nessuna richiesta
//    aggiuntiva). Le Visite fanno eccezione: /api/property/visits è già
//    arricchito lato SQL con property_title/contact_name, quindi qui il nome è
//    mostrato direttamente, a costo zero.
//
// Perché non esiste attivita-dettaglio.js: non esiste, nel backend, alcun
// GET per id su una singola activity o un singolo task (core/router.py espone
// solo list/create/delete per le attività e list/create/patch/delete per i
// task) e le righe restituite da list_* sono già complete (SELECT *): non c'è
// nessun dato aggiuntivo da mostrare dietro una scheda di dettaglio. Una
// pagina di dettaglio qui sarebbe quindi solo un duplicato della riga di
// elenco, non giustificato.
//
// Le due aree richieste dal brief non vengono MAI mescolate in un'unica
// tabella: sono due tab distinti, con due caricamenti indipendenti e due
// insiemi di colonne diversi (renderDaFareTab / renderCronologiaTab).
//
// P25.1: creazione/modifica/eliminazione operative per Task e Attività
// (creazione/eliminazione), tramite gli endpoint CORE già esistenti e già
// verificati sopra (POST/PATCH/DELETE /api/core/tasks, POST/DELETE
// /api/core/activities) — vedi components/activity-task-dialogs.js per i
// dettagli di contratto. Le Visite restano volutamente sola lettura qui: la
// loro gestione (creazione/modifica/eliminazione) è già interamente in
// immobile-dettaglio.js (P16) e non va duplicata in questa vista.

import { apiGet, apiPatch } from '../core/api-client.js';
import { renderTable, renderBadge, escapeHtml, formatDateTime } from '../components/st-table.js';
// P25.1: creazione/modifica/eliminazione task e creazione/eliminazione
// attività, dialog condivisi con contatto-dettaglio.js (vedi
// components/activity-task-dialogs.js per il razionale e i contratti
// backend verificati). Nessun nuovo endpoint: stesso CORE già usato sopra
// per il completamento task (PATCH /api/core/tasks/{id}).
import {
  openNewActivityDialog, deleteActivity,
  openNewTaskDialog, openEditTaskDialog, deleteTask,
} from '../components/activity-task-dialogs.js';

const TASK_STATUS_LABELS = { open: 'Da fare', in_progress: 'In corso', completed: 'Completato', cancelled: 'Annullato' };
const PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };
const ACTIVITY_TYPE_LABELS = {
  note: 'Nota', call: 'Chiamata', email: 'Email', whatsapp: 'WhatsApp',
  meeting: 'Appuntamento', valuation: 'Valutazione', status_change: 'Cambio stato', system: 'Sistema',
};
const VISIT_STATUS_LABELS = { scheduled: 'Programmata', confirmed: 'Confermata', completed: 'Completata', cancelled: 'Annullata', no_show: 'Assente' };

// Stati reali (core/enums.py TASK_STATUSES, property/enums.py VISIT_STATUSES).
// Ogni stato qui elencato corrisponde a UNA chiamata server-side filtrata
// (?status=...), mai a un filtro lato client su un dataset non filtrato.
const OPEN_TASK_STATUSES = ['open', 'in_progress'];
const CLOSED_TASK_STATUSES = ['completed', 'cancelled'];
const UPCOMING_VISIT_STATUSES = ['scheduled', 'confirmed'];
const PAST_VISIT_STATUSES = ['completed', 'cancelled', 'no_show'];

// Tetto di sicurezza contro loop di paginazione anomali: 25 pagine da 200/500
// righe sono già un volume molto superiore a quanto un'agenzia reale accumula
// per un singolo stato. Se mai raggiunto, viene dichiarato (mai nascosto).
const MAX_PAGES = 25;
const TASK_PAGE_LIMIT = 200;
const VISIT_PAGE_LIMIT = 500;
const ACTIVITY_PAGE_LIMIT = 200;

const TABS = [
  { key: 'dafare', label: 'Da fare' },
  { key: 'cronologia', label: 'Cronologia' },
];

// Paginazione server-side generica: NON è N+1 (il numero di chiamate dipende
// dal numero di pagine del dataset per un singolo filtro fisso, mai dal
// numero di righe/renderizzazioni). Si ferma alla prima pagina che restituisce
// meno righe del limit richiesto (fine dataset reale), oppure a MAX_PAGES per
// sicurezza — nel qual caso `truncated` viene impostato a true e la UI lo deve
// dichiarare esplicitamente, mai presentare il risultato come completo.
async function fetchAllPages(path, params, pageLimit) {
  const items = [];
  let offset = 0;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const qs = new URLSearchParams({ ...params, limit: String(pageLimit), offset: String(offset) });
    const result = await apiGet(`${path}?${qs.toString()}`);
    const pageItems = Array.isArray(result?.items) ? result.items : [];
    items.push(...pageItems);
    if (pageItems.length < pageLimit) {
      return { items, truncated: false };
    }
    offset += pageLimit;
  }
  return { items, truncated: true };
}

function fetchAllTasksByStatus(status) {
  return fetchAllPages('/api/core/tasks', { status }, TASK_PAGE_LIMIT);
}

function fetchAllVisitsByStatus(status) {
  return fetchAllPages('/api/property/visits', { status }, VISIT_PAGE_LIMIT);
}

function fetchAllActivities() {
  // Nessun filtro status: le activities non hanno stato (sono sempre eventi
  // passati). Paginazione completa via offset comunque, stesso principio.
  return fetchAllPages('/api/core/activities', {}, ACTIVITY_PAGE_LIMIT);
}

// Combina più fetch per-stato (già risolti via Promise.allSettled) in un
// unico elenco, mantenendo la resilienza per FONTE (non per singolo stato):
// se un solo stato fallisce ma un altro riesce, il fallimento viene comunque
// segnalato in `failedSections` ma i dati riusciti restano visibili — non è
// un Promise.all rigido, ogni ramo fallisce/riesce per conto proprio.
function combineStatusResults(settledResults, label, failedSections) {
  let items = [];
  let truncated = false;
  let anyFailed = false;
  for (const settled of settledResults) {
    if (settled.status === 'fulfilled') {
      items = items.concat(settled.value.items);
      truncated = truncated || settled.value.truncated;
    } else {
      anyFailed = true;
    }
  }
  if (anyFailed) failedSections.push(label);
  return { items, truncated };
}

export async function renderAttivita(container) {
  container.innerHTML = '<p class="muted">Caricamento…</p>';

  const state = {
    activeTab: 'dafare',
    cache: { dafare: null, cronologia: null },
    cronologiaVisibleCount: 30,
    // P25.1: conferma inline a due click prima di eliminare un task o
    // un'attività (nessun window.confirm(), stesso principio già usato in
    // immobile-dettaglio.js per referenti/visite/vendite). Chiave
    // "{kind}:{id}" perché task e attività condividono lo stesso set.
    deleteConfirm: new Set(),
  };

  container.innerHTML = `
    <div class="list-toolbar">
      <div class="tabs" id="attivita-tabs">
        ${TABS.map((t) => `<button type="button" class="tab-btn" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('')}
      </div>
      <button type="button" id="attivita-new-activity" class="btn ghost">+ Nuova attività</button>
      <button type="button" id="attivita-new-task" class="btn primary">+ Nuovo task</button>
    </div>
    <div id="attivita-action-feedback"></div>
    <div id="attivita-tab-content"><p class="muted">Caricamento…</p></div>
    <dialog id="activity-dialog" class="modal"></dialog>
    <dialog id="task-dialog" class="modal"></dialog>
  `;

  const tabsEl = container.querySelector('#attivita-tabs');
  const contentEl = container.querySelector('#attivita-tab-content');
  const activityDialogEl = container.querySelector('#activity-dialog');
  const taskDialogEl = container.querySelector('#task-dialog');

  // P25.1: dopo qualunque creazione/modifica/eliminazione entrambe le cache
  // vengono invalidate (un task nuovo/cambiato può spostarsi tra "Da fare" e
  // "Cronologia") e viene ricaricata solo la tab attiva, stesso principio
  // già usato altrove (es. reloadProposals in immobile-dettaglio.js).
  async function reloadAfterMutation() {
    state.cache.dafare = null;
    state.cache.cronologia = null;
    await showTab(state.activeTab);
  }

  container.querySelector('#attivita-new-activity').addEventListener('click', () => {
    openNewActivityDialog(activityDialogEl, { onSuccess: reloadAfterMutation });
  });
  container.querySelector('#attivita-new-task').addEventListener('click', () => {
    openNewTaskDialog(taskDialogEl, { onSuccess: reloadAfterMutation });
  });

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  async function showTab(key) {
    state.activeTab = key;
    tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === key);
    });
    if (key === 'dafare') return loadDaFare();
    if (key === 'cronologia') return loadCronologia();
  }

  async function loadDaFare(force = false) {
    if (state.cache.dafare && !force) {
      contentEl.innerHTML = renderDaFareTab(state.cache.dafare, state.deleteConfirm);
      bindDaFareActions();
      return;
    }
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    // Una chiamata (paginata) per ciascuno stato realmente "aperto": 2 stati
    // task + 2 stati visita = 4 rami indipendenti, mai una per riga.
    const [openTasksR, inProgressTasksR, scheduledVisitsR, confirmedVisitsR] = await Promise.allSettled([
      fetchAllTasksByStatus('open'),
      fetchAllTasksByStatus('in_progress'),
      fetchAllVisitsByStatus('scheduled'),
      fetchAllVisitsByStatus('confirmed'),
    ]);
    const failedSections = [];
    const { items: tasks, truncated: tasksTruncated } = combineStatusResults(
      [openTasksR, inProgressTasksR], 'task', failedSections,
    );
    const { items: visits, truncated: visitsTruncated } = combineStatusResults(
      [scheduledVisitsR, confirmedVisitsR], 'visite', failedSections,
    );
    state.cache.dafare = { tasks, visits, failedSections, truncated: tasksTruncated || visitsTruncated };
    contentEl.innerHTML = renderDaFareTab(state.cache.dafare);
    bindDaFareActions();
  }

  async function loadCronologia(force = false) {
    if (state.cache.cronologia && !force) {
      contentEl.innerHTML = renderCronologiaTab(state.cache.cronologia, state.cronologiaVisibleCount, state.deleteConfirm);
      bindCronologiaActions();
      return;
    }
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    // Una chiamata (paginata) per ciascuno stato realmente "concluso": 2 stati
    // task + 3 stati visita + le activities (nessuno stato, solo paginate) =
    // 6 rami indipendenti, mai uno per riga.
    const [
      completedTasksR, cancelledTasksR,
      completedVisitsR, cancelledVisitsR, noShowVisitsR,
      activitiesR,
    ] = await Promise.allSettled([
      fetchAllTasksByStatus('completed'),
      fetchAllTasksByStatus('cancelled'),
      fetchAllVisitsByStatus('completed'),
      fetchAllVisitsByStatus('cancelled'),
      fetchAllVisitsByStatus('no_show'),
      fetchAllActivities(),
    ]);
    const failedSections = [];
    const { items: tasks, truncated: tasksTruncated } = combineStatusResults(
      [completedTasksR, cancelledTasksR], 'task', failedSections,
    );
    const { items: visits, truncated: visitsTruncated } = combineStatusResults(
      [completedVisitsR, cancelledVisitsR, noShowVisitsR], 'visite', failedSections,
    );
    const { items: activities, truncated: activitiesTruncated } = combineStatusResults(
      [activitiesR], 'attività', failedSections,
    );
    state.cache.cronologia = {
      tasks, activities, visits, failedSections,
      truncated: tasksTruncated || visitsTruncated || activitiesTruncated,
    };
    state.cronologiaVisibleCount = 30;
    contentEl.innerHTML = renderCronologiaTab(state.cache.cronologia, state.cronologiaVisibleCount, state.deleteConfirm);
    bindCronologiaActions();
  }

  function bindDaFareActions() {
    contentEl.querySelectorAll('[data-complete-task]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const taskId = btn.dataset.completeTask;
        btn.disabled = true;
        btn.textContent = 'Salvataggio…';
        try {
          await apiPatch(`/api/core/tasks/${taskId}`, { status: 'completed' });
          await loadDaFare(true);
        } catch (err) {
          btn.disabled = false;
          btn.textContent = 'Completa';
          // P25.7: sostituito window.alert() con lo stesso pattern
          // error-box inline già usato per gli altri errori di questa vista
          // (vedi bindTaskEditDeleteActions/bindActivityDeleteActions sotto).
          const feedbackEl = container.querySelector('#attivita-action-feedback');
          if (feedbackEl) feedbackEl.innerHTML = `<div class="error-box">Impossibile completare il task: ${escapeHtml(err && err.message ? err.message : 'errore sconosciuto')}</div>`;
        }
      });
    });
    bindTaskEditDeleteActions(state.cache.dafare.tasks, loadDaFare);
  }

  // P25.1: azioni Modifica/Elimina task, condivise tra "Da fare" e
  // "Cronologia" (un task chiuso resta modificabile/eliminabile anche in
  // Cronologia). `tasks` è l'elenco già caricato per la tab corrente (nessuna
  // nuova chiamata per risolvere l'id in un oggetto task). Conferma inline a
  // due click prima della DELETE reale, stesso principio già usato in
  // immobile-dettaglio.js (nessun window.confirm()).
  function bindTaskEditDeleteActions(tasks, reload) {
    contentEl.querySelectorAll('[data-edit-task]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const task = tasks.find((t) => String(t.id) === btn.dataset.editTask);
        if (task) openEditTaskDialog(taskDialogEl, task, { onSuccess: reloadAfterMutation });
      });
    });
    contentEl.querySelectorAll('[data-delete-task]').forEach((btn) => {
      btn.addEventListener('click', () => {
        // Solo toggle dello stato di conferma: `reload` senza force=true
        // re-renderizza dalla cache già presente (nessuna nuova chiamata di
        // rete), esattamente come contactRemoveConfirm in
        // immobile-dettaglio.js.
        state.deleteConfirm.add(`task:${btn.dataset.deleteTask}`);
        reload();
      });
    });
    contentEl.querySelectorAll('[data-delete-task-back]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.deleteConfirm.delete(`task:${btn.dataset.deleteTaskBack}`);
        reload();
      });
    });
    contentEl.querySelectorAll('[data-delete-task-confirm]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const taskId = btn.dataset.deleteTaskConfirm;
        btn.disabled = true;
        btn.textContent = 'Eliminazione…';
        try {
          await deleteTask(taskId);
          state.deleteConfirm.delete(`task:${taskId}`);
          await reloadAfterMutation();
        } catch (err) {
          btn.disabled = false;
          btn.textContent = 'Conferma eliminazione';
          const feedbackEl = container.querySelector('#attivita-action-feedback');
          if (feedbackEl) feedbackEl.innerHTML = `<div class="error-box">Impossibile eliminare il task: ${escapeHtml(err && err.message ? err.message : 'errore sconosciuto')}</div>`;
        }
      });
    });
  }

  // P25.1: azioni Elimina attività (activities non ha PATCH lato backend,
  // vedi components/activity-task-dialogs.js: solo creazione ed eliminazione).
  function bindActivityDeleteActions(activities) {
    contentEl.querySelectorAll('[data-delete-activity]').forEach((btn) => {
      btn.addEventListener('click', () => {
        // Solo toggle: re-render dalla cache, nessuna chiamata di rete
        // (stesso principio del toggle task sopra).
        state.deleteConfirm.add(`attivita:${btn.dataset.deleteActivity}`);
        loadCronologia();
      });
    });
    contentEl.querySelectorAll('[data-delete-activity-back]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.deleteConfirm.delete(`attivita:${btn.dataset.deleteActivityBack}`);
        loadCronologia();
      });
    });
    contentEl.querySelectorAll('[data-delete-activity-confirm]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const activityId = btn.dataset.deleteActivityConfirm;
        btn.disabled = true;
        btn.textContent = 'Eliminazione…';
        try {
          await deleteActivity(activityId);
          state.deleteConfirm.delete(`attivita:${activityId}`);
          await reloadAfterMutation();
        } catch (err) {
          btn.disabled = false;
          btn.textContent = 'Conferma eliminazione';
          const feedbackEl = container.querySelector('#attivita-action-feedback');
          if (feedbackEl) feedbackEl.innerHTML = `<div class="error-box">Impossibile eliminare l'attività: ${escapeHtml(err && err.message ? err.message : 'errore sconosciuto')}</div>`;
        }
      });
    });
  }

  function bindCronologiaActions() {
    const loadMoreBtn = contentEl.querySelector('#cronologia-load-more');
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', () => {
        state.cronologiaVisibleCount += 30;
        contentEl.innerHTML = renderCronologiaTab(state.cache.cronologia, state.cronologiaVisibleCount, state.deleteConfirm);
        bindCronologiaActions();
      });
    }
    bindTaskEditDeleteActions(state.cache.cronologia.tasks, loadCronologia);
    bindActivityDeleteActions(state.cache.cronologia.activities);
  }

  await showTab('dafare');
}

const TRUNCATION_NOTICE = `Il volume dei dati ha superato il tetto di sicurezza della paginazione
  (${MAX_PAGES} pagine per stato): l'elenco sottostante potrebbe NON essere completo.`.replace(/\s+/g, ' ').trim();

// --- Tab "Da fare" ----------------------------------------------------------

function renderDaFareTab(data, deleteConfirm) {
  const { tasks, visits, failedSections, truncated } = data;
  const now = new Date();

  const openTasks = tasks.filter((t) => OPEN_TASK_STATUSES.includes(t.status));
  const upcomingVisits = visits.filter((v) => UPCOMING_VISIT_STATUSES.includes(v.status));

  const items = [
    ...openTasks.map((t) => ({ kind: 'task', data: t, when: t.due_at })),
    ...upcomingVisits.map((v) => ({ kind: 'visita', data: v, when: v.scheduled_at })),
  ];

  const scadute = items.filter((i) => i.when && new Date(i.when) < now).sort(byWhenAsc);
  const oggi = items.filter((i) => i.when && sameDay(new Date(i.when), now)).sort(byWhenAsc);
  const scaduteIds = new Set(scadute.map(itemKey));
  const oggiIds = new Set(oggi.map(itemKey));
  const prossime = items
    .filter((i) => i.when && !scaduteIds.has(itemKey(i)) && !oggiIds.has(itemKey(i)))
    .sort(byWhenAsc);
  const senzaScadenza = items.filter((i) => !i.when);

  const errorBanner = failedSections.length
    ? `<div class="error-box">Alcuni dati non sono disponibili al momento (${escapeHtml(failedSections.join(', '))}). Le altre sezioni restano utilizzabili.</div>`
    : '';
  const truncationBanner = truncated ? `<div class="error-box">${escapeHtml(TRUNCATION_NOTICE)}</div>` : '';

  const kpis = [
    ['Scadute', scadute.length],
    ['Oggi', oggi.length],
    ['Prossime', prossime.length],
    ['Senza scadenza', senzaScadenza.length],
  ];

  return `
    ${errorBanner}
    ${truncationBanner}
    <div class="kpi-grid">
      ${kpis.map(([label, value]) => `
        <div class="card kpi">
          <span class="kpi-label">${escapeHtml(label)}</span>
          <strong class="kpi-value">${value}</strong>
        </div>
      `).join('')}
    </div>
    ${renderBucket('Scadute', scadute, 'Nessun task o visita scaduti.', deleteConfirm)}
    ${renderBucket('Oggi', oggi, 'Nessun task o visita in programma oggi.', deleteConfirm)}
    ${renderBucket('Prossime', prossime, 'Nessun task o visita futuri in programma.', deleteConfirm)}
    ${renderBucket('Senza scadenza', senzaScadenza, 'Nessun task senza scadenza.', deleteConfirm)}
  `;
}

function itemKey(item) {
  return `${item.kind}:${item.data.id}`;
}

function byWhenAsc(a, b) {
  return new Date(a.when) - new Date(b.when);
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function renderBucket(title, items, emptyMessage, deleteConfirm) {
  return `
    <div class="card panel" style="margin-top:16px">
      <h2 class="section-title">${escapeHtml(title)} (${items.length})</h2>
      ${items.length ? `<div class="list">${items.map((item) => renderDaFareItem(item, deleteConfirm)).join('')}</div>` : `<p class="muted">${escapeHtml(emptyMessage)}</p>`}
    </div>
  `;
}

function renderDaFareItem(item, deleteConfirm) {
  if (item.kind === 'task') return renderTaskItem(item.data, deleteConfirm);
  return renderVisitItem(item.data);
}

// P25.1: azioni Modifica/Elimina aggiunte accanto a "Completa" (già
// esistente). Conferma inline a due click per l'eliminazione (nessun
// window.confirm()), chiave "task:{id}" nel Set deleteConfirm condiviso.
function renderTaskActions(t, deleteConfirm) {
  const confirming = deleteConfirm ? deleteConfirm.has(`task:${t.id}`) : false;
  if (confirming) {
    return `
      <button type="button" class="btn ghost" data-delete-task-confirm="${escapeHtml(t.id)}">Conferma eliminazione</button>
      <button type="button" class="btn ghost" data-delete-task-back="${escapeHtml(t.id)}">Indietro</button>
    `;
  }
  const completeBtn = OPEN_TASK_STATUSES.includes(t.status)
    ? `<button type="button" class="btn ghost" data-complete-task="${escapeHtml(t.id)}">Completa</button>`
    : '';
  return `
    ${completeBtn}
    <button type="button" class="btn ghost" data-edit-task="${escapeHtml(t.id)}">Modifica</button>
    <button type="button" class="btn ghost" data-delete-task="${escapeHtml(t.id)}">Elimina</button>
  `;
}

function renderTaskItem(t, deleteConfirm) {
  const isAutomation = t.metadata && t.metadata.source === 'flow';
  const buyId = t.metadata && t.metadata.buy_request_id;
  const priorityTone = ['high', 'urgent'].includes(t.priority) ? 'warn' : 'gray';
  return `
    <div class="list-item">
      <div>
        <strong>${escapeHtml(t.title || `Task #${t.id}`)}</strong>
        ${renderBadge('Task', 'gray')}
        ${isAutomation ? renderBadge('Automazione', 'buy') : ''}
        ${renderBadge(PRIORITY_LABELS[t.priority] || t.priority || '—', priorityTone)}
        <div class="muted">${escapeHtml(t.description || '')}</div>
        <div class="muted">
          ${t.contact_id ? `<a href="#/contatti/${escapeHtml(t.contact_id)}">Apri contatto</a>` : ''}
          ${buyId ? ` · <a href="#/acquirenti/${escapeHtml(buyId)}">Apri richiesta</a>` : ''}
        </div>
      </div>
      <div style="text-align:right">
        <div class="muted">${escapeHtml(formatDateTime(t.due_at))}</div>
        <div class="action-bar">${renderTaskActions(t, deleteConfirm)}</div>
      </div>
    </div>
  `;
}

function renderVisitItem(v) {
  return `
    <div class="list-item">
      <div>
        <strong>${escapeHtml(v.property_title || `Immobile #${v.property_id}`)}</strong>
        ${renderBadge('Visita', 'gray')}
        ${renderBadge(VISIT_STATUS_LABELS[v.status] || v.status || '—', 'gray')}
        <div class="muted">${escapeHtml(v.contact_name || '—')}</div>
        <div class="muted"><a href="#/immobili/${escapeHtml(v.property_id)}">Apri immobile</a></div>
      </div>
      <div style="text-align:right">
        <div class="muted">${escapeHtml(formatDateTime(v.scheduled_at))}</div>
      </div>
    </div>
  `;
}

// --- Tab "Cronologia" --------------------------------------------------------

function renderCronologiaTab(data, visibleCount, deleteConfirm) {
  const { tasks, activities, visits, failedSections, truncated } = data;

  const closedTasks = tasks.filter((t) => CLOSED_TASK_STATUSES.includes(t.status));
  const pastVisits = visits.filter((v) => PAST_VISIT_STATUSES.includes(v.status));

  const items = [
    ...closedTasks.map((t) => ({ kind: 'task', data: t, when: t.completed_at || t.due_at || t.created_at })),
    ...activities.map((a) => ({ kind: 'attivita', data: a, when: a.occurred_at })),
    ...pastVisits.map((v) => ({ kind: 'visita', data: v, when: v.scheduled_at })),
  ].filter((i) => i.when);

  items.sort((a, b) => new Date(b.when) - new Date(a.when));

  const errorBanner = failedSections.length
    ? `<div class="error-box">Alcuni dati non sono disponibili al momento (${escapeHtml(failedSections.join(', '))}). Le altre sezioni restano utilizzabili.</div>`
    : '';
  const truncationBanner = truncated ? `<div class="error-box">${escapeHtml(TRUNCATION_NOTICE)}</div>` : '';

  const visible = items.slice(0, visibleCount);
  const hasMore = items.length > visible.length;

  const columns = [
    { label: 'Tipo', render: (i) => renderCronologiaTypeBadge(i) },
    { label: 'Descrizione', render: (i) => renderCronologiaDescription(i) },
    { label: 'Collegamenti', render: (i) => renderCronologiaLinks(i) },
    { label: 'Quando', render: (i) => escapeHtml(formatDateTime(i.when)) },
    { label: '', render: (i) => renderCronologiaActions(i, deleteConfirm) },
  ];

  return `
    ${errorBanner}
    ${truncationBanner}
    <div class="card panel">
      <h2 class="section-title">Cronologia (${items.length})</h2>
      ${renderTable(columns, visible, { emptyMessage: 'Nessuna attività, task concluso o visita in cronologia.' })}
      ${hasMore ? '<div style="margin-top:12px"><button type="button" class="btn ghost" id="cronologia-load-more">Carica altri</button></div>' : ''}
    </div>
  `;
}

// P25.1: azioni Modifica/Elimina per un task chiuso, Elimina per un'attività
// (nessuna azione per le visite: la loro gestione resta in
// immobile-dettaglio.js, P16, non duplicata qui). Stesso principio di
// conferma inline a due click di renderTaskActions sopra.
function renderCronologiaActions(item, deleteConfirm) {
  if (item.kind === 'task') return renderTaskActions(item.data, deleteConfirm);
  if (item.kind === 'attivita') {
    const confirming = deleteConfirm ? deleteConfirm.has(`attivita:${item.data.id}`) : false;
    if (confirming) {
      return `
        <button type="button" class="btn ghost" data-delete-activity-confirm="${escapeHtml(item.data.id)}">Conferma eliminazione</button>
        <button type="button" class="btn ghost" data-delete-activity-back="${escapeHtml(item.data.id)}">Indietro</button>
      `;
    }
    return `<button type="button" class="btn ghost" data-delete-activity="${escapeHtml(item.data.id)}">Elimina</button>`;
  }
  return '';
}

function renderCronologiaTypeBadge(item) {
  if (item.kind === 'task') return renderBadge(`Task · ${TASK_STATUS_LABELS[item.data.status] || item.data.status}`, item.data.status === 'completed' ? 'ok' : 'danger');
  if (item.kind === 'attivita') return renderBadge(ACTIVITY_TYPE_LABELS[item.data.activity_type] || item.data.activity_type || 'Attività', 'gray');
  return renderBadge(`Visita · ${VISIT_STATUS_LABELS[item.data.status] || item.data.status}`, item.data.status === 'completed' ? 'ok' : 'danger');
}

function renderCronologiaDescription(item) {
  if (item.kind === 'task') return escapeHtml(item.data.title || `Task #${item.data.id}`);
  if (item.kind === 'attivita') return escapeHtml(item.data.subject || item.data.description || '—');
  return escapeHtml(item.data.property_title ? `Visita: ${item.data.property_title}` : `Visita #${item.data.id}`);
}

function renderCronologiaLinks(item) {
  const parts = [];
  if (item.kind === 'task' || item.kind === 'attivita') {
    if (item.data.contact_id) parts.push(`<a href="#/contatti/${escapeHtml(item.data.contact_id)}">Contatto</a>`);
    const buyId = item.data.metadata && item.data.metadata.buy_request_id;
    if (buyId) parts.push(`<a href="#/acquirenti/${escapeHtml(buyId)}">Richiesta</a>`);
  }
  if (item.kind === 'visita') {
    parts.push(`<a href="#/immobili/${escapeHtml(item.data.property_id)}">Immobile</a>`);
    if (item.data.contact_id) parts.push(`<a href="#/contatti/${escapeHtml(item.data.contact_id)}">Contatto</a>`);
  }
  return parts.length ? parts.join(' · ') : '<span class="muted">—</span>';
}
