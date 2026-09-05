import { bindSaleDetails } from '../components/sale-detail.js';
// STIMA360 OS — acquirente-dettaglio.js
// Scheda Richiesta BUY. L'identita' resta il CORE Contact: questa scheda
// rappresenta CONTATTO -> RICHIESTA BUY, non una seconda anagrafica.
//
// Fonte principale: GET /api/buy/requests/{id}/workflow (buy/router.py:24,
// buy/repository.py:workflow) che aggrega in un'unica chiamata: dati
// richiesta + Contatto/Lead (via get_request), locations/typologies/features
// (criteri raw), interactions (buy_request_interactions), history
// (buy_request_history), matches (buy/repository.py:list_matches, gia'
// arricchiti con last_interaction/last_reason) e tasks (via
// buy_request_task_links). La lista Proposte proviene da un fetch separato
// (/api/proposals?buy_request_id=...), NON da un Promise.all che le lega:
// il workflow e' indispensabile (un suo errore impedisce l'apertura della
// scheda), le Proposte sono secondarie (un loro errore isola solo la tab
// Proposte — vedi renderProposte/proposalsError — senza bloccare Panoramica,
// Criteri, Immobili compatibili, Abbinamenti, Visite, Task o Storico).
//
// P9: la tab Proposte diventa operativa (creazione, modifica in bozza,
// transizioni di stato) sul contratto gia' verificato in fase di audit P9
// (proposal/router.py, proposal/repository.py). Nessuna nuova entita': fonte
// unica property_proposals. La creazione e' sempre contestuale a un
// abbinamento (match) reale gia' presente in workflow.matches — mai un
// match_id digitato manualmente — nessuna nuova chiamata di rete oltre a
// quella gia' esistente per il ricaricamento delle proposte stesse. Le
// transizioni di stato rispettano esattamente proposal/enums.py:
// PROPOSAL_TRANSITIONS: nessun selettore di stato generico. Accettare una
// proposta non tocca mai properties.commercial_status ne' buy_requests
// (verificato in repository.py:transition_proposal — unica UPDATE su
// property_proposals).
//
// Verifica CRITERI vs NORMALIZZATI (richiesta esplicitamente dal brief P3):
// GET /api/buy/requests/{id}/normalized (buy/repository.py:normalized) e'
// stato letto per intero: si limita a raggruppare in oggetti nidificati
// (budget/finance/dimensions) GLI STESSI campi gia' presenti in get_request()
// (quindi gia' dentro workflow()), piu' gli stessi array locations/typologies/
// features passati as-is. NON esiste alcuna differenza di valore tra "criteri
// inseriti" e "criteri normalizzati": e' una pura re-forma per la BUY Admin
// legacy, senza calcolo aggiuntivo. Di conseguenza la tab Criteri usa i campi
// gia' presenti in workflow() senza una seconda chiamata di rete: nessuna
// logica di normalizzazione viene ricostruita lato browser, si mostrano solo
// gli stessi valori che il backend BUY possiede.
//
// Immobili compatibili / Abbinamenti: entrambe le tab leggono lo stesso
// array workflow.matches (autorizzato esplicitamente dal brief P3, a
// differenza di P2 dove il riuso sotto altro nome era vietato). Nessun
// calculate/refresh viene mai invocato entrando in queste tab.
//
// Visite: derivate da workflow.interactions filtrando interaction_type in
// (visit_requested, visit_scheduled, visited) — relazione certa (colonna
// buy_request_id su buy_request_interactions), non dedotta. I dettagli del
// singolo appuntamento (property_visits: stato, esito, valutazione) NON sono
// mostrati: property/router.py non espone un GET /api/property/visits/{id}
// ne' un filtro per buy_request_id/match_id su GET /api/property/visits,
// quindi recuperarli richiederebbe N+1 non autorizzato. Gap riportato.
//
// Attivita: core/repository.py:list_activities non ha parametro
// buy_request_id (stesso gap gia' verificato in P2 per property_id):
// nessuna relazione Attivita CORE <-> Richiesta BUY esiste oggi. Le
// buy_request_interactions/history (eventi BUY) sono mostrate nelle tab
// Visite/Storico e NON vengono fuse semanticamente con le Attivita CORE.
//
// Task: fonte CORE `tasks`, relazione via buy_request_task_links (gia'
// risolta server-side da list_tasks). Sola lettura in P3.
//
// Prossima azione: mostrata in Panoramica da next_action_at/next_action_note
// (colonne dirette su buy_requests). Nessuna scrittura/registrazione azione
// in P3 (rimane funzione della BUY Admin legacy, vedi report finale).
//
// Vista interamente in sola lettura salvo la creazione (gestita in
// acquirenti.js): nessuna modifica criteri, nessuna transizione proposta,
// nessun calcolo/refresh match in questo file.

import { apiGet, apiPatch, apiPost, apiDelete } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';
// P25.5: azioni rapide "Nuova attività"/"Nuovo task" (contact_id/lead_id
// della richiesta precompilati), stessi dialog condivisi gia' usati da
// attivita.js e contatto-dettaglio.js — vedi components/activity-task-dialogs.js.
import { openNewActivityDialog, openNewTaskDialog } from '../components/activity-task-dialogs.js';

const STATUS_LABELS = { draft: 'Bozza', active: 'Attiva', paused: 'In pausa', satisfied: 'Soddisfatta', closed: 'Chiusa', archived: 'Archiviata' };
const PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };
const URGENCY_LABELS = { exploratory: 'Esplorativa', flexible: 'Flessibile', within_6_months: 'Entro 6 mesi', within_3_months: 'Entro 3 mesi', immediate: 'Immediata' };
const FINANCE_STATUS_LABELS = { unknown: 'Da definire', cash: 'Liquidità propria', mortgage_to_assess: 'Mutuo da valutare', mortgage_in_progress: 'Mutuo in corso', mortgage_preapproved: 'Mutuo pre-approvato', sale_dependent: 'Subordinata a vendita' };
const REQUIREMENT_LEVEL_LABELS = { required: 'Obbligatorio', preferred: 'Preferito', optional: 'Opzionale', excluded: 'Escluso' };
const LOCATION_TYPE_LABELS = { region: 'Regione', province: 'Provincia', municipality: 'Comune', microzone: 'Microzona', radius: 'Raggio' };
const INTERACTION_TYPE_LABELS = { proposed: 'Proposto', discarded: 'Scartato', interested: 'Interessato', visit_requested: 'Visita richiesta', visit_scheduled: 'Visita programmata', visited: 'Visitato', offer_candidate: 'Candidato per offerta', other: 'Altro' };
const HISTORY_EVENT_LABELS = {
  request_created: 'Richiesta creata', request_updated: 'Richiesta aggiornata', status_changed: 'Stato cambiato',
  finance_updated: 'Finanza aggiornata', next_action_updated: 'Prossima azione aggiornata',
  match_proposed: 'Match proposto', match_discarded: 'Match scartato', match_interested: 'Interesse su match',
  visit_requested: 'Visita richiesta', visit_scheduled: 'Visita programmata', visited: 'Visita effettuata',
  offer_candidate: 'Candidato per offerta', note: 'Nota', task_created: 'Task creato', task_unlinked: 'Task scollegato',
};
const PROPERTY_STATUS_LABELS = { draft: 'Bozza', evaluation: 'In valutazione', mandate: 'Mandato', active: 'Attivo', reserved: 'Riservato', under_offer: 'Sotto offerta', sold: 'Venduto', withdrawn: 'Ritirato', archived: 'Archiviato' };

const VISIT_INTERACTION_TYPES = new Set(['visit_requested', 'visit_scheduled', 'visited']);

// P25.5 — buy/schemas.py::MatchDecision.action deve appartenere a
// INTERACTION_TYPES - {'other'} (verificato: `if v.get('action') not in
// INTERACTION_TYPES - {'other'}`). Derivato da INTERACTION_TYPE_LABELS
// sopra, nessun secondo elenco duplicato.
const MATCH_DECISION_ACTIONS = Object.keys(INTERACTION_TYPE_LABELS).filter((k) => k !== 'other');

// buy/schemas.py::REJECTION_REASONS — richiesto quando interaction_type/
// action è 'discarded' (validate_interaction/validate_action).
const REJECTION_REASON_LABELS = {
  price_too_high: 'Prezzo troppo alto', wrong_location: 'Zona non adatta', too_small: 'Troppo piccolo',
  too_large: 'Troppo grande', missing_elevator: 'Manca ascensore', wrong_floor: 'Piano non adatto',
  poor_condition: 'Condizioni scadenti', no_parking: 'Manca posto auto', no_outdoor_space: 'Manca spazio esterno',
  already_seen: 'Già visto', not_available: 'Non disponibile', buyer_decision: 'Decisione acquirente',
  agent_decision: 'Decisione agente', other: 'Altro',
};

// buy/enums.py::FEATURE_VALUE_TYPES — per il form "+ Aggiungi caratteristica".
const FEATURE_VALUE_TYPE_LABELS = { boolean: 'Sì/No', number: 'Numero', range: 'Intervallo', text: 'Testo' };

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'criteri', label: 'Criteri' },
  { key: 'immobili', label: 'Immobili compatibili' },
  { key: 'abbinamenti', label: 'Abbinamenti' },
  { key: 'visite', label: 'Visite' },
  { key: 'proposte', label: 'Proposte' },
  { key: 'attivita', label: 'Attività' },
  { key: 'task', label: 'Task' },
  { key: 'storico', label: 'Storico' },
];

export async function renderAcquirenteDettaglio(container, params = []) {
  const requestId = params[0];
  if (!requestId || !/^\d+$/.test(String(requestId))) {
    container.innerHTML = '<div class="error-box">Identificativo richiesta non valido.</div>';
    return;
  }

  container.innerHTML = '<p class="muted">Caricamento scheda richiesta…</p>';

  // Il workflow (dati richiesta + Contatto + criteri + match + task + storico)
  // e' indispensabile: se fallisce, la scheda non puo' aprirsi. Le Proposte
  // sono invece secondarie (fonte esterna al dominio BUY, /api/proposals):
  // un loro errore non deve impedire l'apertura della scheda ne' bloccare le
  // altre tab, quindi vengono caricate con un fetch separato e un fallimento
  // viene isolato solo alla tab Proposte (nessun Promise.all che le lega).
  let data;
  try {
    data = await apiGet(`/api/buy/requests/${requestId}/workflow`);
  } catch (error) {
    const notFound = /non trovato|not found/i.test(error.message || '');
    container.innerHTML = `<div class="error-box">${notFound ? 'Richiesta non trovata.' : `Errore nel caricamento della richiesta: ${escapeHtml(error.message)}`}</div>`;
    return;
  }

  let proposals = [];
  let proposalsError = null;
  try {
    const proposalsData = await apiGet(`/api/proposals?buy_request_id=${requestId}`);
    proposals = Array.isArray(proposalsData?.items) ? proposalsData.items : [];
  } catch (error) {
    proposalsError = error.message || 'errore sconosciuto';
  }

  // P11: sale associate alla richiesta, fonte secondaria come le Proposte
  // sopra (stesso principio: un errore qui isola solo la colonna Vendita
  // della tab Proposte, non blocca l'apertura della scheda). GET /api/sales
  // non ha filtro proposal_id (sale/repository.py:list_sales verificato in
  // audit): l'associazione a ciascuna proposta e' fatta lato client.
  let sales = [];
  let salesError = null;
  try {
    const salesData = await apiGet(`/api/sales?buy_request_id=${requestId}`);
    sales = Array.isArray(salesData?.items) ? salesData.items : [];
  } catch (error) {
    salesError = error.message || 'errore sconosciuto';
  }

  // P9: ricarica proposte dopo creazione/modifica/transizione. Stessa fonte
  // gia' usata al caricamento iniziale (/api/proposals?buy_request_id=...),
  // nessun nuovo endpoint.
  async function reloadProposals() {
    try {
      const proposalsData = await apiGet(`/api/proposals?buy_request_id=${requestId}`);
      proposals = Array.isArray(proposalsData?.items) ? proposalsData.items : [];
      proposalsError = null;
    } catch (error) {
      proposalsError = error.message || 'errore sconosciuto';
    }
  }

  async function reloadSales() {
    try {
      const salesData = await apiGet(`/api/sales?buy_request_id=${requestId}`);
      sales = Array.isArray(salesData?.items) ? salesData.items : [];
      salesError = null;
    } catch (error) {
      salesError = error.message || 'errore sconosciuto';
    }
  }

  // P11: aggiorna solo lo stato della richiesta dopo il completamento di una
  // vendita (side-effect di complete_sale su buy_requests.status='satisfied',
  // sale/repository.py) e riflette il nuovo badge nell'header senza reload
  // completo pagina. Riusa lo stesso endpoint workflow del caricamento
  // iniziale (nessun nuovo endpoint). Best-effort: la vendita e' gia' stata
  // completata lato backend, un errore qui non deve nascondere il feedback
  // di successo gia' mostrato.
  async function reloadRequestStatus() {
    try {
      const updated = await apiGet(`/api/buy/requests/${requestId}/workflow`);
      data.status = updated.status;
      const badgeEl = container.querySelector('#request-status-badge');
      if (badgeEl) badgeEl.innerHTML = headerStatusBadgeHtml();
    } catch (_error) {
      // ignorato volutamente, vedi commento sopra
    }
  }

  const contactId = data.contact_id;
  const contactName = data.contact_name || `Contatto #${contactId}`;

  // P11: badge di stato isolato in una funzione cosi' da poter essere
  // ri-renderizzato dopo il completamento di una vendita
  // (reloadRequestStatus) senza toccare priorita'/urgenza ne' il resto
  // dell'header.
  function headerStatusBadgeHtml() {
    return renderBadge(STATUS_LABELS[data.status] || data.status || '—', statusTone(data.status));
  }

  // P11: stato locale "conferma annullamento vendita" (secondo click prima
  // di eseguire la cancel reale). Nessun window.confirm(): pattern inline
  // pilotato da re-render.
  const saleCancelConfirm = new Set();

  const leadId = data.lead_id;

  container.innerHTML = `
    <div class="contact-header card">
      <h2 id="acquirente-header-title">${escapeHtml(data.title || `Richiesta #${data.id}`)}</h2>
      <div class="muted">
        Contatto: <a href="#/contatti/${escapeHtml(contactId)}" id="acquirente-contact-link"><strong>${escapeHtml(contactName)}</strong></a>
        · Richiesta #${escapeHtml(data.id)}
      </div>
      <div class="badge-row" id="acquirente-header-badges">
        <span id="request-status-badge">${headerStatusBadgeHtml()}</span>
        ${renderBadge(PRIORITY_LABELS[data.priority] || data.priority || '—', priorityTone(data.priority))}
        ${renderBadge(URGENCY_LABELS[data.urgency] || data.urgency || '—', 'gray')}
      </div>
      <div class="action-bar" style="margin-top:8px">
        <button type="button" id="request-edit-btn" class="btn ghost">Modifica richiesta</button>
        <button type="button" id="request-quick-activity" class="btn ghost">+ Nuova attività</button>
        <button type="button" id="request-quick-task" class="btn ghost">+ Nuovo task</button>
      </div>
      <div id="request-quick-feedback"></div>
    </div>
    <div class="tabs" id="request-tabs"></div>
    <div id="request-tab-content" class="card panel"></div>
    <dialog id="proposal-dialog" class="modal"></dialog>
    <dialog id="sale-dialog" class="modal"></dialog>
    <dialog id="request-edit-dialog" class="modal modal-wide"></dialog>
    <dialog id="contact-activity-dialog" class="modal"></dialog>
    <dialog id="contact-task-dialog" class="modal"></dialog>
    <dialog id="match-decision-dialog" class="modal"></dialog>
  `;

  const contactLink = container.querySelector('#acquirente-contact-link');
  contactLink.addEventListener('click', (event) => {
    event.preventDefault();
    navigate('contatti', [contactId]);
  });

  // P25.5: ricarica la richiesta (workflow) dopo una modifica (Modifica
  // richiesta, aggiunta/rimozione criterio, ricalcolo abbinamenti, esito
  // registrato su un match). Stessa fonte del caricamento iniziale, nessun
  // nuovo endpoint. Aggiorna header (titolo, badge) senza reload pagina.
  async function reloadRequest() {
    try {
      const fresh = await apiGet(`/api/buy/requests/${requestId}/workflow`);
      Object.assign(data, fresh);
      const titleEl = container.querySelector('#acquirente-header-title');
      if (titleEl) titleEl.textContent = data.title || `Richiesta #${data.id}`;
      const badgesEl = container.querySelector('#acquirente-header-badges');
      if (badgesEl) {
        badgesEl.innerHTML = `
          <span id="request-status-badge">${headerStatusBadgeHtml()}</span>
          ${renderBadge(PRIORITY_LABELS[data.priority] || data.priority || '—', priorityTone(data.priority))}
          ${renderBadge(URGENCY_LABELS[data.urgency] || data.urgency || '—', 'gray')}
        `;
      }
    } catch (_error) {
      // best-effort, stesso principio di reloadRequestStatus sopra.
    }
  }

  function showQuickFeedback(message) {
    const fb = container.querySelector('#request-quick-feedback');
    if (fb) fb.innerHTML = `<div class="success-box">${escapeHtml(message)}</div>`;
  }

  container.querySelector('#request-quick-activity').addEventListener('click', () => {
    openNewActivityDialog(container.querySelector('#contact-activity-dialog'), {
      presetContact: { id: contactId, label: contactName },
      presetLeads: leadId ? [{ id: leadId, pipeline: data.lead_pipeline, stage: data.lead_stage }] : [],
      onSuccess: async () => { showQuickFeedback('Attività registrata.'); },
    });
  });
  container.querySelector('#request-quick-task').addEventListener('click', () => {
    openNewTaskDialog(container.querySelector('#contact-task-dialog'), {
      presetContact: { id: contactId, label: contactName },
      presetLeads: leadId ? [{ id: leadId, pipeline: data.lead_pipeline, stage: data.lead_stage }] : [],
      onSuccess: async () => { showQuickFeedback('Task creato.'); },
    });
  });

  container.querySelector('#request-edit-btn').addEventListener('click', () => {
    openEditRequestDialog();
  });

  // --- P25.5: Modifica richiesta (BuyRequestUpdate) -------------------------
  // Copre i campi realmente mostrati in Panoramica (title/status/priority/
  // urgency/assigned_to/budget_*/surface_*/rooms_min/bedrooms_min/
  // bathrooms_min/finance_status/search_start_date/target_purchase_date/
  // next_action_at/next_action_note/notes) - vedi renderPanoramica sotto.
  // Campi esclusi deliberatamente: archived_at (nessuna archiviazione
  // dedicata inventata, come in P25.4 per i contatti - nessun endpoint
  // DELETE /buy/requests/{id} viene chiamato da qui), metadata (nessun uso
  // in questa UI), lead_id (relazione stabilita alla creazione, non
  // riassegnabile qui), mortgage_required/mortgage_preapproved/
  // available_cash/maximum_monthly_payment/budget_flexibility_percent/
  // includes_agency_fees/includes_renovation/property_to_sell_first/
  // finance_review_at/finance_notes (mostrati solo nel tab Criteri come dati
  // di contesto, non hanno qui un editor dedicato - stesso principio "nessun
  // grande refactor non necessario" del brief P25). Solo i campi realmente
  // modificati entrano nel payload (buy/service.py::update_request usa
  // dump(p,True)=exclude_unset), stesso principio di P25.3/P25.4.
  function openEditRequestDialog() {
    const dialog = container.querySelector('#request-edit-dialog');
    dialog.innerHTML = `
      <div class="modal-content">
        <h3>Modifica richiesta</h3>
        <div class="error-box" hidden></div>
        <form id="request-edit-form">
          <div class="form-field"><label>Titolo</label><input type="text" name="title" class="input" maxlength="200" value="${escapeHtml(data.title || '')}" required></div>
          <div class="form-grid-3">
            <div class="form-field"><label>Stato</label><select name="status" class="input">${Object.entries(STATUS_LABELS).map(([v, l]) => `<option value="${v}" ${v === data.status ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div>
            <div class="form-field"><label>Priorità</label><select name="priority" class="input">${Object.entries(PRIORITY_LABELS).map(([v, l]) => `<option value="${v}" ${v === data.priority ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div>
            <div class="form-field"><label>Urgenza</label><select name="urgency" class="input">${Object.entries(URGENCY_LABELS).map(([v, l]) => `<option value="${v}" ${v === data.urgency ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div>
          </div>
          <div class="form-grid-3">
            <div class="form-field"><label>Assegnata a</label><input type="text" name="assigned_to" class="input" maxlength="200" value="${escapeHtml(data.assigned_to || '')}"></div>
            <div class="form-field"><label>Situazione finanziaria</label><select name="finance_status" class="input">${Object.entries(FINANCE_STATUS_LABELS).map(([v, l]) => `<option value="${v}" ${v === data.finance_status ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select></div>
          </div>
          <h4 class="section-title">Budget (€)</h4>
          <div class="form-grid-3">
            <div class="form-field"><label>Minimo</label><input type="number" name="budget_min" class="input" min="0" step="1000" value="${data.budget_min != null ? escapeHtml(data.budget_min) : ''}"></div>
            <div class="form-field"><label>Target</label><input type="number" name="budget_target" class="input" min="0" step="1000" value="${data.budget_target != null ? escapeHtml(data.budget_target) : ''}"></div>
            <div class="form-field"><label>Massimo</label><input type="number" name="budget_max" class="input" min="0" step="1000" value="${data.budget_max != null ? escapeHtml(data.budget_max) : ''}"></div>
          </div>
          <h4 class="section-title">Superficie (mq)</h4>
          <div class="form-grid-3">
            <div class="form-field"><label>Minima</label><input type="number" name="surface_min" class="input" min="0" step="1" value="${data.surface_min != null ? escapeHtml(data.surface_min) : ''}"></div>
            <div class="form-field"><label>Target</label><input type="number" name="surface_target" class="input" min="0" step="1" value="${data.surface_target != null ? escapeHtml(data.surface_target) : ''}"></div>
            <div class="form-field"><label>Massima</label><input type="number" name="surface_max" class="input" min="0" step="1" value="${data.surface_max != null ? escapeHtml(data.surface_max) : ''}"></div>
          </div>
          <h4 class="section-title">Minimi</h4>
          <div class="form-grid-3">
            <div class="form-field"><label>Locali</label><input type="number" name="rooms_min" class="input" min="0" step="1" value="${data.rooms_min != null ? escapeHtml(data.rooms_min) : ''}"></div>
            <div class="form-field"><label>Camere</label><input type="number" name="bedrooms_min" class="input" min="0" step="1" value="${data.bedrooms_min != null ? escapeHtml(data.bedrooms_min) : ''}"></div>
            <div class="form-field"><label>Bagni</label><input type="number" name="bathrooms_min" class="input" min="0" step="1" value="${data.bathrooms_min != null ? escapeHtml(data.bathrooms_min) : ''}"></div>
          </div>
          <h4 class="section-title">Prossima azione</h4>
          <div class="form-grid-3">
            <div class="form-field"><label>Ricerca avviata il</label><input type="date" name="search_start_date" class="input" value="${toDateInputValue(data.search_start_date)}"></div>
            <div class="form-field"><label>Acquisto entro</label><input type="date" name="target_purchase_date" class="input" value="${toDateInputValue(data.target_purchase_date)}"></div>
            <div class="form-field"><label>Prossima azione</label><input type="datetime-local" name="next_action_at" class="input" value="${toDatetimeLocalValue(data.next_action_at)}"></div>
          </div>
          <div class="form-field"><label>Nota prossima azione</label><input type="text" name="next_action_note" class="input" maxlength="500" value="${escapeHtml(data.next_action_note || '')}"></div>
          <div class="form-field"><label>Note</label><textarea name="notes" rows="3" class="input">${escapeHtml(data.notes || '')}</textarea></div>
          <div class="modal-actions">
            <button type="button" class="btn ghost" data-cancel>Annulla</button>
            <button type="submit" class="btn primary">Salva</button>
          </div>
        </form>
      </div>
    `;
    const errorBox = dialog.querySelector('.error-box');
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.querySelector('#request-edit-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitBtn = dialog.querySelector('button[type="submit"]');
      const formData = new FormData(event.target);
      submitBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      errorBox.hidden = true;

      const payload = {};
      const textField = (name, current) => {
        const raw = formData.get(name);
        const value = raw === null ? '' : String(raw).trim();
        if (value !== (current || '')) payload[name] = value === '' ? null : value;
      };
      const selectField = (name, current) => {
        const value = formData.get(name);
        if (value !== current) payload[name] = value;
      };
      // Normalizza entrambi i lati al confronto (Number|null): il valore
      // corrente arriva dal backend come stringa decimale (es. "150000.00"),
      // quindi un confronto diretto value!==current lo segnalerebbe SEMPRE
      // come "cambiato" anche quando l'operatore non ha toccato il campo.
      const numberField = (name, current) => {
        const raw = formData.get(name);
        const trimmed = raw === null ? '' : String(raw).trim();
        const value = trimmed === '' ? null : Number(trimmed);
        const currentValue = current === undefined || current === null ? null : Number(current);
        if (value !== currentValue) payload[name] = value;
      };
      // Confronto su rappresentazione stringa (YYYY-MM-DD) su entrambi i
      // lati, cosi' "campo vuoto" == "campo vuoto" anche quando current e'
      // null/undefined e raw e' stringa vuota (tipi diversi altrimenti).
      const dateField = (name, current) => {
        const raw = formData.get(name) || '';
        const currentStr = toDateInputValue(current);
        if (raw !== currentStr) payload[name] = raw === '' ? null : raw;
      };

      textField('title', data.title);
      selectField('status', data.status);
      selectField('priority', data.priority);
      selectField('urgency', data.urgency);
      textField('assigned_to', data.assigned_to);
      selectField('finance_status', data.finance_status);
      numberField('budget_min', data.budget_min);
      numberField('budget_target', data.budget_target);
      numberField('budget_max', data.budget_max);
      numberField('surface_min', data.surface_min);
      numberField('surface_target', data.surface_target);
      numberField('surface_max', data.surface_max);
      numberField('rooms_min', data.rooms_min);
      numberField('bedrooms_min', data.bedrooms_min);
      numberField('bathrooms_min', data.bathrooms_min);
      dateField('search_start_date', data.search_start_date);
      dateField('target_purchase_date', data.target_purchase_date);
      // Confronto a precisione di minuto (stesso valore mostrato
      // nell'input datetime-local): un round-trip Date->toISOString() su un
      // valore invariato con secondi/millisecondi originali diversi da zero
      // altrimenti risulterebbe sempre "cambiato".
      const nextActionRaw = formData.get('next_action_at') || '';
      if (nextActionRaw !== toDatetimeLocalValue(data.next_action_at)) {
        payload.next_action_at = nextActionRaw ? new Date(nextActionRaw).toISOString() : null;
      }
      textField('next_action_note', data.next_action_note);
      textField('notes', data.notes);

      if (!Object.keys(payload).length) {
        dialog.close();
        return;
      }

      try {
        await apiPatch(`/api/buy/requests/${requestId}`, payload);
        dialog.close();
        await reloadRequest();
        showTab('panoramica');
      } catch (error) {
        errorBox.hidden = false;
        errorBox.textContent = `Errore nel salvataggio della richiesta: ${error.message}`;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Salva';
      }
    });
    dialog.showModal();
  }

  const tabsEl = container.querySelector('#request-tabs');
  tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');

  const contentEl = container.querySelector('#request-tab-content');

  function showTab(key) {
    tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
    try {
      switch (key) {
        case 'panoramica': contentEl.innerHTML = renderPanoramica(data, proposals, proposalsError); break;
        case 'criteri': contentEl.innerHTML = renderCriteri(data, criteriaAddMode, criteriaRemoveConfirm); bindCriteriSection(contentEl); break;
        case 'immobili': contentEl.innerHTML = renderImmobiliCompatibili(data.matches); break;
        case 'abbinamenti': contentEl.innerHTML = renderAbbinamenti(data.matches); bindAbbinamentiSection(contentEl); break;
        case 'visite': contentEl.innerHTML = renderVisite(data.interactions); break;
        case 'proposte': contentEl.innerHTML = renderProposte(proposals, proposalsError, sales, saleCancelConfirm); bindProposteSection(contentEl); break;
        case 'attivita': contentEl.innerHTML = renderAttivita(); break;
        case 'task': contentEl.innerHTML = renderTask(data.tasks); break;
        case 'storico': contentEl.innerHTML = renderStorico(data.history); break;
        default: contentEl.innerHTML = '<p class="muted">Sezione non disponibile.</p>';
      }
    } catch (error) {
      contentEl.innerHTML = `<div class="error-box">Errore nel caricamento della sezione: ${escapeHtml(error.message)}</div>`;
    }
    contentEl.querySelectorAll('.visit-outcome-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const match = (data.matches || []).find((m) => String(m.id) === btn.dataset.matchId);
        if (match) openMatchDecisionDialog(match, btn.dataset.visitId);
      });
    });
    bindMatchRowClicks(contentEl);
  }

  // --- P25.5: Abbinamenti — ricalcolo MATCH + registrazione esito ----------
  // Ricalcolo: POST /api/match/buy-requests/{id}/calculate (BatchMatchRequest,
  // match/router.py:56-58), isolato in un proprio feedback box
  // (#abbinamenti-feedback) - un suo errore non tocca ne' la richiesta
  // (Modifica richiesta sopra) ne' le altre tab, come richiesto dal brief
  // ("error isolation from BUY save"). Esito: POST /api/buy/requests/{id}/
  // matches/{match_id}/decision (MatchDecision, buy/router.py:30-31), che
  // scrive su buy_request_interactions (service.py::match_decision) con gli
  // stessi valori reali di INTERACTION_TYPES - {'other'} (MATCH_DECISION_ACTIONS).
  function bindAbbinamentiSection(panelEl) {
    const recalcBtn = panelEl.querySelector('#match-recalc-btn');
    if (recalcBtn) {
      recalcBtn.addEventListener('click', async () => {
        const feedbackEl = panelEl.querySelector('#abbinamenti-feedback');
        recalcBtn.disabled = true;
        recalcBtn.textContent = 'Ricalcolo…';
        if (feedbackEl) feedbackEl.innerHTML = '';
        try {
          await apiPost(`/api/match/buy-requests/${requestId}/calculate`, {});
          await reloadRequest();
          showTab('abbinamenti');
          const fb = contentEl.querySelector('#abbinamenti-feedback');
          if (fb) fb.innerHTML = '<div class="success-box">Abbinamenti ricalcolati.</div>';
        } catch (error) {
          recalcBtn.disabled = false;
          recalcBtn.textContent = 'Ricalcola abbinamenti';
          if (feedbackEl) feedbackEl.innerHTML = `<div class="error-box">Errore nel ricalcolo: ${escapeHtml(error.message)}</div>`;
        }
      });
    }
    panelEl.querySelectorAll('.match-decision-btn').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const matchId = Number(btn.dataset.matchId);
        const match = (data.matches || []).find((m) => m.id === matchId);
        if (match) openMatchDecisionDialog(match);
      });
    });
  }

  function openMatchDecisionDialog(match, visitId = null) {
    const visits = [...new Set((data.interactions || []).filter((i) =>
      i.interaction_type === 'visit_scheduled' && String(i.match_id) === String(match.id) && i.property_visit_id
    ).map((i) => i.property_visit_id))];
    const outcomeActions = ['visited', 'interested', 'discarded', 'offer_candidate'];
    const dialog = container.querySelector('#match-decision-dialog');
    const propertyLabel = match.property_title || match.property_code || `Immobile #${match.property_id}`;
    dialog.innerHTML = `
      <div class="modal-content">
        <h3>Registra esito — ${escapeHtml(propertyLabel)}</h3>
        <div class="error-box" hidden></div>
        <form id="match-decision-form">
          <div class="form-field"><label>Esito</label>
            <select name="action" id="match-decision-action" class="input">
              ${(visitId ? outcomeActions : MATCH_DECISION_ACTIONS).map((a) => `<option value="${a}">${escapeHtml(INTERACTION_TYPE_LABELS[a] || a)}</option>`).join('')}
            </select>
          </div>
          <div class="form-field" id="match-decision-visit-field" hidden><label>Visita collegata</label>
            <select name="property_visit_id" class="input">
              ${visitId ? '' : '<option value="">Nessuna visita — esito sul match</option>'}
              ${visits.filter((id) => !visitId || String(id) === String(visitId)).map((id) => `<option value="${escapeHtml(id)}">Visita #${escapeHtml(id)}</option>`).join('')}
            </select>
          </div>
          <div class="form-field" id="match-decision-reason-field" hidden><label>Motivo</label>
            <select name="reason_code" class="input">
              ${Object.entries(REJECTION_REASON_LABELS).map(([v, l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join('')}
            </select>
          </div>
          <div class="form-field" id="match-decision-schedule-field" hidden><label>Data e ora visita</label>
            <input type="datetime-local" name="scheduled_at" class="input">
          </div>
          <div class="form-field"><label>Note</label><textarea name="notes" rows="2" class="input"></textarea></div>
          <div class="modal-actions">
            <button type="button" class="btn ghost" data-cancel>Annulla</button>
            <button type="submit" class="btn primary">Salva</button>
          </div>
        </form>
      </div>
    `;
    const errorBox = dialog.querySelector('.error-box');
    const actionSelect = dialog.querySelector('#match-decision-action');
    const reasonField = dialog.querySelector('#match-decision-reason-field');
    const scheduleField = dialog.querySelector('#match-decision-schedule-field');
    function syncFields() {
      dialog.querySelector('#match-decision-visit-field').hidden = !outcomeActions.includes(actionSelect.value);
      reasonField.hidden = actionSelect.value !== 'discarded';
      scheduleField.hidden = actionSelect.value !== 'visit_scheduled';
    }
    actionSelect.addEventListener('change', syncFields);
    syncFields();
    dialog.querySelector('[data-cancel]').addEventListener('click', () => dialog.close());
    dialog.querySelector('#match-decision-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitBtn = dialog.querySelector('button[type="submit"]');
      const formData = new FormData(event.target);
      errorBox.hidden = true;
      const action = formData.get('action');
      const payload = { action };
      const selectedVisit = formData.get('property_visit_id');
      if (outcomeActions.includes(action) && selectedVisit) payload.property_visit_id = Number(selectedVisit);
      if (visitId && !payload.property_visit_id) {
        errorBox.hidden = false;
        errorBox.textContent = 'Visita collegata non disponibile. Ricaricare la richiesta.';
        return;
      }
      const notes = String(formData.get('notes') || '').trim();
      if (notes) payload.notes = notes;
      if (action === 'discarded') payload.reason_code = formData.get('reason_code');
      if (action === 'visit_scheduled') {
        const scheduledRaw = formData.get('scheduled_at');
        if (!scheduledRaw) {
          errorBox.hidden = false;
          errorBox.textContent = 'Data e ora visita obbligatorie per "Visita programmata".';
          return;
        }
        payload.scheduled_at = new Date(scheduledRaw).toISOString();
      }
      submitBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      try {
        await apiPost(`/api/buy/requests/${requestId}/matches/${match.id}/decision`, payload);
        dialog.close();
        await reloadRequest();
        showTab('abbinamenti');
        const fb = contentEl.querySelector('#abbinamenti-feedback');
        if (fb) fb.innerHTML = '<div class="success-box">Esito registrato.</div>';
      } catch (error) {
        errorBox.hidden = false;
        errorBox.textContent = error.message || 'Errore nella registrazione dell\'esito.';
        submitBtn.disabled = false;
        submitBtn.textContent = 'Salva';
      }
    });
    dialog.showModal();
  }

  // --- P25.5: Criteri — aggiunta/rimozione zone/tipologie/caratteristiche --
  // Sostituisce la nota "Modifica criteri non disponibile" (P3): usa gli
  // stessi endpoint additivi gia' verificati in buy/router.py (POST/DELETE
  // .../locations, .../typologies, .../features). Conferma inline a due
  // click per la rimozione (mai window.confirm()), stesso principio delle
  // altre sezioni P25.
  const criteriaAddMode = { location: false, typology: false, feature: false };
  const criteriaRemoveConfirm = { location: new Set(), typology: new Set(), feature: new Set() };

  async function reloadCriteria() {
    try {
      const fresh = await apiGet(`/api/buy/requests/${requestId}/workflow`);
      data.locations = fresh.locations;
      data.typologies = fresh.typologies;
      data.features = fresh.features;
    } catch (_error) {
      // best-effort, stesso principio delle altre reload di questa vista.
    }
  }

  function bindCriteriSection(panelEl) {
    panelEl.querySelectorAll('[data-criteria-add-toggle]').forEach((btn) => {
      btn.addEventListener('click', () => {
        criteriaAddMode[btn.dataset.criteriaAddToggle] = true;
        showTab('criteri');
      });
    });
    panelEl.querySelectorAll('[data-criteria-add-cancel]').forEach((btn) => {
      btn.addEventListener('click', () => {
        criteriaAddMode[btn.dataset.criteriaAddCancel] = false;
        showTab('criteri');
      });
    });
    const locationForm = panelEl.querySelector('#location-add-form');
    if (locationForm) {
      locationForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(event.target);
        const errorEl = panelEl.querySelector('#location-add-error');
        if (errorEl) errorEl.textContent = '';
        const locationType = formData.get('location_type');
        const payload = { location_type: locationType, priority: Number(formData.get('priority')) || 1 };
        const value = String(formData.get('value') || '').trim();
        if (!value) {
          if (errorEl) errorEl.textContent = 'Indica un valore per la zona.';
          return;
        }
        payload[locationType === 'radius' ? 'municipality' : locationType] = value;
        try {
          await apiPost(`/api/buy/requests/${requestId}/locations`, payload);
          criteriaAddMode.location = false;
          await reloadCriteria();
          showTab('criteri');
        } catch (error) {
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'aggiunta della zona.';
        }
      });
    }
    const typologyForm = panelEl.querySelector('#typology-add-form');
    if (typologyForm) {
      typologyForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(event.target);
        const errorEl = panelEl.querySelector('#typology-add-error');
        if (errorEl) errorEl.textContent = '';
        const propertyType = String(formData.get('property_type') || '').trim();
        if (!propertyType) {
          if (errorEl) errorEl.textContent = 'Indica il tipo immobile.';
          return;
        }
        try {
          await apiPost(`/api/buy/requests/${requestId}/typologies`, {
            property_type: propertyType,
            requirement_level: formData.get('requirement_level'),
          });
          criteriaAddMode.typology = false;
          await reloadCriteria();
          showTab('criteri');
        } catch (error) {
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'aggiunta della tipologia.';
        }
      });
    }
    const featureForm = panelEl.querySelector('#feature-add-form');
    if (featureForm) {
      featureForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(event.target);
        const errorEl = panelEl.querySelector('#feature-add-error');
        if (errorEl) errorEl.textContent = '';
        const featureCode = String(formData.get('feature_code') || '').trim();
        if (!featureCode) {
          if (errorEl) errorEl.textContent = 'Indica il codice caratteristica.';
          return;
        }
        const valueType = formData.get('value_type');
        const payload = { feature_code: featureCode, requirement_level: formData.get('requirement_level'), value_type: valueType };
        if (valueType === 'boolean') payload.value_boolean = formData.get('value_boolean') === 'true';
        if (valueType === 'text') payload.value_text = String(formData.get('value_text') || '').trim() || null;
        try {
          await apiPost(`/api/buy/requests/${requestId}/features`, payload);
          criteriaAddMode.feature = false;
          await reloadCriteria();
          showTab('criteri');
        } catch (error) {
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'aggiunta della caratteristica.';
        }
      });
    }
    panelEl.querySelectorAll('[data-criteria-remove]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const [kind, id] = btn.dataset.criteriaRemove.split(':');
        const confirmSet = criteriaRemoveConfirm[kind];
        if (!confirmSet.has(id)) {
          confirmSet.add(id);
          showTab('criteri');
          return;
        }
        btn.disabled = true;
        btn.textContent = 'Rimozione…';
        const endpoint = kind === 'location' ? 'locations' : kind === 'typology' ? 'typologies' : 'features';
        try {
          await apiDelete(`/api/buy/${endpoint}/${id}`);
          confirmSet.delete(id);
          await reloadCriteria();
          showTab('criteri');
        } catch (error) {
          confirmSet.delete(id);
          btn.disabled = false;
          btn.textContent = 'Elimina';
          const errorEl = panelEl.querySelector(`#${kind}-add-error`);
          if (errorEl) errorEl.textContent = error.message || 'Errore nella rimozione.';
        }
      });
    });
  }

  // P9: sezione Proposte. Creazione sempre contestuale a un abbinamento
  // reale gia' presente in data.matches (mai un match_id digitato), modifica
  // diretta solo in stato draft, transizioni secondo la macchina a stati
  // reale.
  function bindProposteSection(panelEl) {
    bindSaleDetails(panelEl);
    const newBtn = panelEl.querySelector('#proposal-new-btn');
    if (newBtn) {
      newBtn.addEventListener('click', () => { openProposalCreateDialog(); });
    }
    panelEl.querySelectorAll('.proposal-action-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const proposalId = Number(btn.dataset.proposalId);
        const action = btn.dataset.action;
        const proposal = proposals.find((p) => p.id === proposalId);
        if (!proposal) return;
        if (action === 'edit') {
          openProposalEditDialog(proposal);
        } else {
          runProposalTransition(btn, proposalId, action);
        }
      });
    });
    // P11: azioni vendita, stesso #proposal-feedback della sezione Proposte.
    panelEl.querySelectorAll('.sale-create-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const proposalId = Number(btn.dataset.proposalId);
        const proposal = proposals.find((p) => p.id === proposalId);
        if (!proposal) return;
        openSaleCreateDialog(proposal);
      });
    });
    panelEl.querySelectorAll('.sale-complete-btn').forEach((btn) => {
      btn.addEventListener('click', () => { runSaleComplete(btn, Number(btn.dataset.saleId)); });
    });
    panelEl.querySelectorAll('.sale-cancel-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        saleCancelConfirm.add(Number(btn.dataset.saleId));
        showTab('proposte');
      });
    });
    panelEl.querySelectorAll('.sale-cancel-back-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        saleCancelConfirm.delete(Number(btn.dataset.saleId));
        showTab('proposte');
      });
    });
    panelEl.querySelectorAll('.sale-cancel-confirm-btn').forEach((btn) => {
      btn.addEventListener('click', () => { runSaleCancel(btn, Number(btn.dataset.saleId)); });
    });
  }

  function openProposalCreateDialog() {
    const dialogEl = container.querySelector('#proposal-dialog');
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (!dialogEl) return;
    const matches = Array.isArray(data.matches) ? data.matches : [];
    const openMatchIds = new Set(proposals.filter((p) => ['draft', 'submitted'].includes(p.status)).map((p) => p.match_id));
    const eligible = matches.filter((m) => !openMatchIds.has(m.id));
    if (!eligible.length) {
      if (feedbackEl) feedbackEl.innerHTML = '<div class="error-box">Nessun abbinamento disponibile: tutti gli abbinamenti di questa richiesta hanno gi\u00e0 una proposta aperta, oppure non esiste ancora alcun abbinamento (vedi tab Abbinamenti).</div>';
      return;
    }
    openProposalDialog(dialogEl, { mode: 'create', eligible });
  }

  function openProposalEditDialog(proposal) {
    const dialogEl = container.querySelector('#proposal-dialog');
    if (!dialogEl) return;
    openProposalDialog(dialogEl, { mode: 'edit', proposal });
  }

  function openProposalDialog(dialogEl, opts) {
    const isEdit = opts.mode === 'edit';
    const proposal = opts.proposal || null;
    // Chiave di idempotenza stabile per l'intera vita di QUESTA apertura del
    // dialog in modalita' create: generata una sola volta qui (mai dentro
    // buildProposalCreatePayload), cosi' un retry dopo un errore di rete sullo
    // stesso dialog riusa la stessa chiave invece di generarne una nuova.
    const createIdempotencyKey = isEdit ? null : crypto.randomUUID();
    dialogEl.innerHTML = `
      <form id="proposal-form">
        <h3 class="section-title">${isEdit ? 'Modifica proposta' : 'Nuova proposta'}</h3>
        ${isEdit ? '' : `
          <div class="form-field">
            <label>Abbinamento</label>
            <select id="proposal-match" class="input" required>
              ${opts.eligible.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(matchOptionLabel(m))}</option>`).join('')}
            </select>
          </div>
        `}
        <div class="form-grid-2">
          <div class="form-field"><label>Importo (\u20ac)</label><input type="number" id="proposal-amount" class="input" min="0.01" step="0.01" required value="${proposal ? escapeHtml(proposal.amount) : ''}"></div>
          <div class="form-field"><label>Scadenza</label><input type="datetime-local" id="proposal-expiry" class="input" required value="${proposal ? proposalDateTimeLocal(proposal.expires_at) : ''}"></div>
        </div>
        <div class="form-field"><label>Note</label><textarea id="proposal-notes" class="input">${proposal ? escapeHtml(proposal.notes || '') : ''}</textarea></div>
        <div id="proposal-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="proposal-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="proposal-form-submit" class="btn primary">Salva</button>
        </div>
      </form>
    `;

    dialogEl.querySelector('#proposal-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#proposal-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#proposal-form-error');
      if (errorEl) errorEl.textContent = '';

      let payload;
      try {
        payload = isEdit ? buildProposalUpdatePayload(dialogEl) : buildProposalCreatePayload(dialogEl, createIdempotencyKey);
      } catch (error) {
        if (errorEl) errorEl.textContent = error.message || 'Dati non validi.';
        return;
      }

      submitting = true;
      const submitBtn = dialogEl.querySelector('#proposal-form-submit');
      const cancelBtn = dialogEl.querySelector('#proposal-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio\u2026';
      try {
        if (isEdit) {
          await apiPatch(`/api/proposals/${proposal.id}`, payload);
        } else {
          await apiPost('/api/proposals', payload);
        }
        dialogEl.close();
        await reloadProposals();
        showTab('proposte');
        const fb = contentEl.querySelector('#proposal-feedback');
        if (fb) fb.innerHTML = `<div class="success-box">${isEdit ? 'Proposta aggiornata.' : 'Proposta creata.'}</div>`;
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio.';
      }
    });

    dialogEl.showModal();
  }

  async function runProposalTransition(btn, proposalId, targetStatus) {
    const allButtons = Array.from(contentEl.querySelectorAll('.proposal-action-btn'));
    const originalText = btn.textContent;
    allButtons.forEach((b) => { b.disabled = true; });
    btn.textContent = 'Attendere\u2026';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/proposals/${proposalId}/transition`, { target_status: targetStatus });
      await reloadProposals();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Stato proposta aggiornato.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nella transizione di stato.')}</div>`;
    }
  }

  // P11: elenco pulsanti azione vendita/proposta usato per disabilitare tutto
  // durante una mutazione in corso, stesso principio di "allButtons" in
  // runProposalTransition sopra (nessun doppio submit).
  function allSaleAndProposalButtons() {
    return Array.from(contentEl.querySelectorAll(
      '.proposal-action-btn, .sale-create-btn, .sale-complete-btn, .sale-cancel-btn, .sale-cancel-confirm-btn, .sale-cancel-back-btn',
    ));
  }

  // Dialog "Nuova vendita", stesso pattern del dialog proposta sopra
  // (openProposalDialog): un solo campo obbligatorio (prezzo vendita,
  // precompilato con l'importo della proposta), idempotency_key generata
  // una sola volta all'apertura. property_id/buy_request_id/created_by non
  // vengono mai chiesti: sono derivati dal backend a partire da proposal_id
  // (sale/repository.py:create_sale -> _proposal_for_sale).
  function openSaleCreateDialog(proposal) {
    const dialogEl = container.querySelector('#sale-dialog');
    if (!dialogEl) return;
    const idempotencyKey = crypto.randomUUID();
    dialogEl.innerHTML = `
      <form id="sale-form">
        <h3 class="section-title">Nuova vendita</h3>
        <div class="form-field"><label>Prezzo vendita (€)</label><input type="number" id="sale-price" class="input" min="0.01" step="0.01" required value="${proposal.amount != null ? escapeHtml(proposal.amount) : ''}"></div>
        <div class="form-field"><label>Note</label><textarea id="sale-notes" class="input"></textarea></div>
        <div id="sale-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="sale-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="sale-form-submit" class="btn primary">Salva</button>
        </div>
      </form>
    `;

    dialogEl.querySelector('#sale-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#sale-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#sale-form-error');
      if (errorEl) errorEl.textContent = '';

      let salePrice;
      try {
        // Stessa validazione (numero finito, > 0) di proposalAmountValue,
        // riusata qui senza duplicare la regola.
        salePrice = proposalAmountValue(dialogEl.querySelector('#sale-price').value);
      } catch (error) {
        if (errorEl) errorEl.textContent = error.message || 'Prezzo vendita non valido.';
        return;
      }
      const notes = dialogEl.querySelector('#sale-notes').value.trim();

      submitting = true;
      const submitBtn = dialogEl.querySelector('#sale-form-submit');
      const cancelBtn = dialogEl.querySelector('#sale-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      try {
        await apiPost('/api/sales', {
          proposal_id: proposal.id,
          sale_price: salePrice,
          notes: notes || null,
          idempotency_key: idempotencyKey,
        });
        dialogEl.close();
        await reloadSales();
        showTab('proposte');
        const fb = contentEl.querySelector('#proposal-feedback');
        if (fb) fb.innerHTML = '<div class="success-box">Vendita creata.</div>';
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nella creazione della vendita.';
      }
    });

    dialogEl.showModal();
  }

  async function runSaleComplete(btn, saleId) {
    const allButtons = allSaleAndProposalButtons();
    allButtons.forEach((b) => { b.disabled = true; });
    const originalText = btn.textContent;
    btn.textContent = 'Attendere…';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/sales/${saleId}/complete`);
      await reloadSales();
      // Side-effect backend di complete_sale su buy_requests.status
      // (sale/repository.py:complete_sale): riflesso nel badge header senza
      // reload completo pagina.
      await reloadRequestStatus();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Vendita completata.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nel completamento della vendita.')}</div>`;
    }
  }

  // Conferma inline a due click (nessun window.confirm(), non esiste nella
  // shell): il primo click su "Annulla vendita" aggiunge il sale_id a
  // saleCancelConfirm e ri-renderizza la tab (vedi bindProposteSection),
  // il secondo click su "Conferma annullamento" esegue davvero la POST.
  async function runSaleCancel(btn, saleId) {
    const allButtons = allSaleAndProposalButtons();
    allButtons.forEach((b) => { b.disabled = true; });
    const originalText = btn.textContent;
    btn.textContent = 'Attendere…';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/sales/${saleId}/cancel`);
      saleCancelConfirm.delete(saleId);
      await reloadSales();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Vendita annullata.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nell’annullamento della vendita.')}</div>`;
    }
  }

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  showTab('panoramica');
}

function bindMatchRowClicks(contentEl) {
  contentEl.querySelectorAll('tr.row-clickable[data-row-id]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const propertyId = tr.dataset.propertyId;
      if (propertyId) navigate('immobili', [propertyId]);
    });
  });
  // P4: colonna "Apri match" -> #/abbinamenti/{match_id} (solo tab Abbinamenti,
  // vedi renderAbbinamenti). stopPropagation per non attivare anche il click
  // di riga verso l'immobile gestito sopra.
  contentEl.querySelectorAll('.open-match-btn').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.stopPropagation();
      navigate('abbinamenti', [btn.dataset.matchId]);
    });
  });
}

// --- Panoramica -------------------------------------------------------

function renderPanoramica(x, proposals, proposalsError) {
  const fields = [
    ['Contatto', x.contact_name || `Contatto #${x.contact_id}`],
    ['Email contatto', x.contact_email], ['Telefono contatto', x.contact_phone],
    ['Stato', STATUS_LABELS[x.status] || x.status], ['Priorità', PRIORITY_LABELS[x.priority] || x.priority],
    ['Urgenza', URGENCY_LABELS[x.urgency] || x.urgency],
    ['Creata il', formatDateTime(x.created_at)], ['Aggiornata il', formatDateTime(x.updated_at)],
    ['Budget minimo', formatPrice(x.budget_min)], ['Budget target', formatPrice(x.budget_target)], ['Budget massimo', formatPrice(x.budget_max)],
    ['Superficie minima (mq)', x.surface_min], ['Superficie target (mq)', x.surface_target], ['Superficie massima (mq)', x.surface_max],
    ['Locali minimi', x.rooms_min], ['Camere minime', x.bedrooms_min], ['Bagni minimi', x.bathrooms_min],
    ['Situazione finanziaria', FINANCE_STATUS_LABELS[x.finance_status] || x.finance_status],
    ['Ricerca avviata il', formatDate(x.search_start_date)], ['Acquisto entro', formatDate(x.target_purchase_date)],
    ['Assegnata a', x.assigned_to],
  ];
  const counts = [
    ['Match', (x.matches || []).length],
    ['Proposte', proposalsError ? 'n/d' : proposals.length],
    ['Task', (x.tasks || []).length],
    ['Eventi storico', (x.history || []).length],
  ];
  return `
    <h3 class="section-title">Dati richiesta</h3>
    <div class="detail-grid">
      ${fields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</div>`).join('')}
    </div>
    <h3 class="section-title">Prossima azione</h3>
    <p>${x.next_action_at || x.next_action_note ? `${escapeHtml(formatDateTime(x.next_action_at))} — ${escapeHtml(x.next_action_note || 'Nessuna nota')}` : 'Nessuna prossima azione impostata.'}</p>
    <h3 class="section-title">Note</h3>
    <p>${escapeHtml(x.notes || 'Nessuna nota.')}</p>
    <h3 class="section-title">Riepilogo</h3>
    <div class="stat-chip-row">
      ${counts.map(([label, value]) => `<div class="stat-chip"><span>${value}</span><small>${escapeHtml(label)}</small></div>`).join('')}
    </div>
  `;
}

// --- Criteri (raw = normalizzato: nessuna seconda chiamata, vedi header) --

function renderCriteri(x, addMode, removeConfirm) {
  const budgetGrid = [
    ['Minimo', formatPrice(x.budget_min)], ['Target', formatPrice(x.budget_target)], ['Massimo', formatPrice(x.budget_max)],
    ['Flessibilità', x.budget_flexibility_percent != null ? `${x.budget_flexibility_percent}%` : null],
    ['Include spese agenzia', x.includes_agency_fees ? 'Sì' : 'No'], ['Include ristrutturazione', x.includes_renovation ? 'Sì' : 'No'],
  ];
  const dimGrid = [
    ['Superficie minima', x.surface_min], ['Superficie target', x.surface_target], ['Superficie massima', x.surface_max],
    ['Locali minimi', x.rooms_min], ['Camere minime', x.bedrooms_min], ['Bagni minimi', x.bathrooms_min],
  ];

  const locationsTable = renderTable(
    [
      { label: 'Livello', render: (l) => renderBadge(LOCATION_TYPE_LABELS[l.location_type] || l.location_type || '—', 'gray') },
      { label: 'Valore', render: (l) => escapeHtml([l.region, l.province, l.municipality, l.microzone].filter(Boolean).join(' / ') || '—') },
      { label: 'Priorità', render: (l) => escapeHtml(l.priority ?? '—') },
      { label: 'Raggio (km)', render: (l) => l.radius_km != null ? escapeHtml(l.radius_km) : '—' },
      { label: 'Vincolo', render: (l) => l.is_required ? renderBadge('Obbligatoria', 'ok') : (l.is_excluded ? renderBadge('Esclusa', 'danger') : '') },
      { label: '', render: (l) => criteriaRemoveButton('location', l.id, removeConfirm.location) },
    ],
    x.locations,
    { emptyMessage: 'Nessuna zona impostata.' },
  );

  const typologiesTable = renderTable(
    [
      { label: 'Tipo immobile', render: (t) => escapeHtml(t.property_type || '—') },
      { label: 'Livello', render: (t) => renderBadge(REQUIREMENT_LEVEL_LABELS[t.requirement_level] || t.requirement_level || '—', requirementTone(t.requirement_level)) },
      { label: 'Priorità', render: (t) => escapeHtml(t.priority ?? '—') },
      { label: '', render: (t) => criteriaRemoveButton('typology', t.id, removeConfirm.typology) },
    ],
    x.typologies,
    { emptyMessage: 'Nessuna tipologia impostata.' },
  );

  const featuresTable = renderTable(
    [
      { label: 'Caratteristica', render: (f) => escapeHtml(f.feature_code || '—') },
      { label: 'Livello', render: (f) => renderBadge(REQUIREMENT_LEVEL_LABELS[f.requirement_level] || f.requirement_level || '—', requirementTone(f.requirement_level)) },
      { label: 'Valore', render: (f) => escapeHtml(formatFeatureValue(f)) },
      { label: '', render: (f) => criteriaRemoveButton('feature', f.id, removeConfirm.feature) },
    ],
    x.features,
    { emptyMessage: 'Nessuna caratteristica impostata.' },
  );

  return `
    <h3 class="section-title">Budget</h3>
    <div class="detail-grid">${budgetGrid.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</div>`).join('')}</div>
    <h3 class="section-title">Dimensioni</h3>
    <div class="detail-grid">${dimGrid.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</div>`).join('')}</div>
    <h3 class="section-title">Zone</h3>
    ${locationsTable}
    ${renderCriteriaAddSection('location', addMode.location)}
    <h3 class="section-title">Tipologie</h3>
    ${typologiesTable}
    ${renderCriteriaAddSection('typology', addMode.typology)}
    <h3 class="section-title">Caratteristiche</h3>
    ${featuresTable}
    ${renderCriteriaAddSection('feature', addMode.feature)}
  `;
}

// P25.5: pulsante di rimozione con conferma inline a due click (Set di id
// stringa in attesa di conferma, passato dal chiamante - stesso principio
// di roleRemoveConfirm in contatto-dettaglio.js).
function criteriaRemoveButton(kind, id, confirmSet) {
  const key = String(id);
  return `<button type="button" class="btn ghost" data-criteria-remove="${kind}:${escapeHtml(key)}">${confirmSet.has(key) ? 'Conferma' : 'Elimina'}</button>`;
}

// P25.5: sezione "+ Aggiungi zona/tipologia/caratteristica" - un pulsante
// che rivela un piccolo form inline (stesso principio toggle di
// incaricoEditMode in immobile-dettaglio.js), mai un intero dialog per
// un'aggiunta cosi' piccola.
function renderCriteriaAddSection(kind, isOpen) {
  if (!isOpen) {
    return `<div class="action-bar" style="margin:8px 0 16px"><button type="button" class="btn ghost" data-criteria-add-toggle="${kind}">+ Aggiungi ${CRITERIA_ADD_LABELS[kind]}</button></div>`;
  }
  if (kind === 'location') {
    return `
      <form id="location-add-form" class="form-grid-3" style="margin:8px 0 16px;align-items:end">
        <div class="form-field"><label>Livello</label>
          <select name="location_type" class="input">
            ${Object.entries(LOCATION_TYPE_LABELS).map(([v, l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join('')}
          </select>
        </div>
        <div class="form-field"><label>Valore</label><input type="text" name="value" class="input" maxlength="150" placeholder="Es. Milano" required></div>
        <div class="form-field"><label>Priorità (1-10)</label><input type="number" name="priority" class="input" min="1" max="10" value="1"></div>
        <div id="location-add-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-criteria-add-cancel="location">Annulla</button>
          <button type="submit" class="btn primary">Aggiungi</button>
        </div>
      </form>
    `;
  }
  if (kind === 'typology') {
    return `
      <form id="typology-add-form" class="form-grid-3" style="margin:8px 0 16px;align-items:end">
        <div class="form-field"><label>Tipo immobile</label><input type="text" name="property_type" class="input" maxlength="80" placeholder="Es. apartment" required></div>
        <div class="form-field"><label>Livello</label>
          <select name="requirement_level" class="input">
            ${Object.entries(REQUIREMENT_LEVEL_LABELS).map(([v, l]) => `<option value="${v}" ${v === 'preferred' ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}
          </select>
        </div>
        <div id="typology-add-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-criteria-add-cancel="typology">Annulla</button>
          <button type="submit" class="btn primary">Aggiungi</button>
        </div>
      </form>
    `;
  }
  return `
    <form id="feature-add-form" class="form-grid-3" style="margin:8px 0 16px;align-items:end">
      <div class="form-field"><label>Codice caratteristica</label><input type="text" name="feature_code" class="input" maxlength="100" placeholder="Es. balcony" required></div>
      <div class="form-field"><label>Livello</label>
        <select name="requirement_level" class="input">
          ${Object.entries(REQUIREMENT_LEVEL_LABELS).map(([v, l]) => `<option value="${v}" ${v === 'preferred' ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}
        </select>
      </div>
      <div class="form-field"><label>Tipo valore</label>
        <select name="value_type" class="input">
          ${Object.entries(FEATURE_VALUE_TYPE_LABELS).map(([v, l]) => `<option value="${v}" ${v === 'boolean' ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}
        </select>
      </div>
      <div class="form-field"><label>Valore (Sì/No)</label>
        <select name="value_boolean" class="input"><option value="true">Sì</option><option value="false">No</option></select>
      </div>
      <div class="form-field"><label>Valore testo (se tipo Testo)</label><input type="text" name="value_text" class="input" maxlength="200"></div>
      <div id="feature-add-error" class="field-error"></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-criteria-add-cancel="feature">Annulla</button>
        <button type="submit" class="btn primary">Aggiungi</button>
      </div>
    </form>
  `;
}

const CRITERIA_ADD_LABELS = { location: 'zona', typology: 'tipologia', feature: 'caratteristica' };

function formatFeatureValue(f) {
  if (f.value_type === 'boolean') return f.value_boolean === true ? 'Sì' : f.value_boolean === false ? 'No' : '—';
  if (f.value_type === 'text') return f.value_text || '—';
  const min = f.value_min ?? '—'; const max = f.value_max ?? '—';
  return `${min} – ${max}`;
}

// --- Immobili compatibili (workflow.matches, nessun calcolo) --------------

function renderImmobiliCompatibili(matches) {
  const list = Array.isArray(matches) ? matches : [];
  if (!list.length) return '<p class="muted">Nessun abbinamento calcolato.</p>';
  return renderMatchTable(list, [
    { label: 'Immobile', render: (m) => escapeHtml(m.property_title || m.property_code || `Immobile #${m.property_id}`) },
    { label: 'Comune', render: (m) => escapeHtml(m.city || '—') },
    { label: 'Prezzo', render: (m) => formatPrice(m.asking_price) },
    { label: 'Stato immobile', render: (m) => renderBadge(PROPERTY_STATUS_LABELS[m.property_status] || m.property_status || '—', 'gray') },
  ], 'Nessun abbinamento calcolato.');
}

// --- Abbinamenti (stesso array di Immobili compatibili, vista dettagliata) --

function renderAbbinamenti(matches) {
  // P25.5: azione "Ricalcola abbinamenti" (POST /api/match/buy-requests/{id}/
  // calculate) sempre visibile qui, anche a lista vuota - e' proprio il caso
  // in cui serve di piu'. Feedback isolato in #abbinamenti-feedback (mai
  // condiviso con #proposal-feedback o con l'errore del dialog Modifica
  // richiesta - vedi bindAbbinamentiSection).
  const actionBar = `
    <div class="action-bar" style="margin-bottom:12px">
      <button type="button" id="match-recalc-btn" class="btn primary">Ricalcola abbinamenti</button>
    </div>
    <div id="abbinamenti-feedback"></div>
  `;
  const list = Array.isArray(matches) ? matches : [];
  if (!list.length) return `${actionBar}<p class="muted">Nessun abbinamento calcolato.</p>`;
  const table = renderMatchTable(list, [
    { label: 'Immobile', render: (m) => escapeHtml(m.property_title || m.property_code || `Immobile #${m.property_id}`) },
    { label: 'Punteggio', render: (m) => escapeHtml(m.effective_score ?? m.score_total ?? '—') },
    { label: 'Classe', render: (m) => renderBadge(m.match_class || '—', matchClassTone(m.match_class)) },
    { label: 'Stato commerciale', render: (m) => escapeHtml(m.commercial_status || '—') },
    { label: 'Aggiornamento', render: (m) => renderBadge(m.freshness_status || '—', m.freshness_status === 'stale' ? 'warn' : 'gray') },
    { label: 'Revisione richiesta', render: (m) => m.review_required ? renderBadge('Sì', 'warn') : '' },
    { label: 'Ultima interazione', render: (m) => escapeHtml(INTERACTION_TYPE_LABELS[m.last_interaction] || m.last_interaction || '—') },
    { label: '', render: (m) => `
      <button type="button" class="btn ghost open-match-btn" data-match-id="${escapeHtml(m.id)}">Apri match</button>
      <button type="button" class="btn ghost match-decision-btn" data-match-id="${escapeHtml(m.id)}">Registra esito</button>
    ` },
  ], 'Nessun abbinamento calcolato.');
  return actionBar + table;
}

// Tabella match con click-through a #/immobili/{property_id} (non property-admin).
function renderMatchTable(matches, columns, emptyMessage) {
  const table = renderTable(columns, matches, { emptyMessage, onRowClick: true });
  // renderTable usa row.id come data-row-id: qui serve property_id per la navigazione,
  // quindi il container viene marcato con data-property-id via post-processing minimo.
  return table.replace(/data-row-id="(\d+)"/g, (full, matchId) => {
    const match = matches.find((m) => String(m.id) === matchId);
    return match ? `${full} data-property-id="${match.property_id}"` : full;
  });
}

// --- Visite (da buy_request_interactions, relazione certa) ----------------

function renderVisite(interactions) {
  const list = (Array.isArray(interactions) ? interactions : []).filter((i) => VISIT_INTERACTION_TYPES.has(i.interaction_type));
  const note = '<p class="muted">Elenco dagli eventi di visita registrati sulla richiesta (buy_request_interactions). Stato, esito e valutazione della singola visita non sono disponibili qui: nessuna API espone il dettaglio property_visits per id o filtrato su questa richiesta.</p>';
  const table = renderTable(
    [
      { label: 'Immobile', render: (i) => escapeHtml(i.property_title || i.property_code || (i.property_id ? `Immobile #${i.property_id}` : '—')) },
      { label: 'Tipo evento', render: (i) => renderBadge(INTERACTION_TYPE_LABELS[i.interaction_type] || i.interaction_type || '—', 'gray') },
      { label: 'Quando', render: (i) => escapeHtml(formatDateTime(i.occurred_at)) },
      { label: 'Note', render: (i) => escapeHtml(i.notes || '—') },
      { label: 'Esito', render: (i) => i.interaction_type === 'visit_scheduled' && i.property_visit_id && i.match_id
        ? `<button type="button" class="btn ghost visit-outcome-btn" data-match-id="${escapeHtml(i.match_id)}" data-visit-id="${escapeHtml(i.property_visit_id)}">Registra esito visita #${escapeHtml(i.property_visit_id)}</button>` : '—' },
    ],
    list,
    { emptyMessage: 'Nessuna visita registrata per questa richiesta.' },
  );
  return note + table;
}

// --- Proposte (P9: operative — creazione, modifica in bozza, transizioni) --

const PROPOSAL_STATUS_LABELS = {
  draft: 'Bozza', submitted: 'Inviata', accepted: 'Accettata',
  rejected: 'Rifiutata', expired: 'Scaduta', withdrawn: 'Ritirata',
};

const PROPOSAL_ACTION_LABELS = {
  edit: 'Modifica', submitted: 'Invia', accepted: 'Accetta',
  rejected: 'Rifiuta', withdrawn: 'Ritira', expired: 'Segna scaduta',
};

// P11: chiusura frontend P10 SALE. Nessuna nuova entita': fonte unica
// property_sales via il contratto gia' verificato (sale/router.py,
// sale/repository.py). GET /api/sales non ha un filtro proposal_id
// (verificato in sale/repository.py:list_sales), quindi l'associazione
// vendita<->proposta e' fatta qui, lato client, filtrando l'elenco gia'
// caricato per buy_request_id. Nessun precheck "immobile gia' venduto" in
// questa vista: il dato property.commercial_status non e' disponibile qui
// senza una fetch aggiuntiva dedicata, che il brief P11 vieta esplicitamente
// di introdurre.
const SALE_STATUS_LABELS = { pending: 'In corso', completed: 'Completata', cancelled: 'Annullata' };

// Una proposta puo' avere piu' sale nel tempo (es. una cancelled seguita da
// una nuova creazione: il partial unique index di property_sales si applica
// solo a status IN ('pending','completed'), quindi una cancelled non blocca
// mai una nuova vendita). Priorita' nella scelta della sale da mostrare:
// completed > pending > cancelled piu' recente.
function saleForProposal(proposalId, sales) {
  const related = (sales || []).filter((s) => s.proposal_id === proposalId);
  if (!related.length) return null;
  const completed = related.find((s) => s.status === 'completed');
  if (completed) return completed;
  const pending = related.find((s) => s.status === 'pending');
  if (pending) return pending;
  const cancelled = related
    .filter((s) => s.status === 'cancelled')
    .sort((a, b) => (new Date(b.created_at || 0) - new Date(a.created_at || 0)) || (b.id - a.id));
  return cancelled[0] || null;
}

// Azioni vendita valide per la coppia (proposta, sale associata), lette
// direttamente dal contratto backend: nessuna macchina a stati reinventata
// qui (sale/enums.py:SALE_TRANSITIONS resta l'unica fonte, il backend
// ricontrolla sempre).
function saleActions(proposal, sale) {
  if (proposal.status !== 'accepted') return [];
  if (sale && sale.status === 'completed') return [];
  if (sale && sale.status === 'pending') return ['complete', 'cancel'];
  return ['create'];
}

// Colonna "Vendita" nella tab Proposte: badge di stato (se esiste una sale)
// piu' le azioni pertinenti. Nessun window.confirm(): l'annullamento usa una
// conferma inline a due click pilotata da saleCancelConfirm (Set di sale_id
// in attesa di conferma), passato dal chiamante e non ricreato qui.
function renderVenditaCell(pr, sale, saleCancelConfirm) {
  if (pr.status !== 'accepted') return '<span class="muted">—</span>';
  const actions = saleActions(pr, sale);
  const statusBadge = sale
    ? renderBadge(SALE_STATUS_LABELS[sale.status] || sale.status || '—', sale.status === 'completed' ? 'ok' : sale.status === 'pending' ? 'warn' : 'gray')
    : '';
  const confirming = sale ? saleCancelConfirm.has(sale.id) : false;
  const buttonsHtml = actions.map((a) => {
    if (a === 'create') return `<button type="button" class="btn ghost sale-create-btn" data-proposal-id="${escapeHtml(pr.id)}">Crea vendita</button>`;
    if (a === 'complete') return `<button type="button" class="btn ghost sale-complete-btn" data-sale-id="${escapeHtml(sale.id)}"${confirming ? ' disabled' : ''}>Completa vendita</button>`;
    if (a === 'cancel') {
      if (confirming) {
        return `<button type="button" class="btn ghost sale-cancel-confirm-btn" data-sale-id="${escapeHtml(sale.id)}">Conferma annullamento</button><button type="button" class="btn ghost sale-cancel-back-btn" data-sale-id="${escapeHtml(sale.id)}">Indietro</button>`;
      }
      return `<button type="button" class="btn ghost sale-cancel-btn" data-sale-id="${escapeHtml(sale.id)}">Annulla vendita</button>`;
    }
    return '';
  }).join('');
  if (!statusBadge && !buttonsHtml) return '<span class="muted">—</span>';
  return `${statusBadge}${sale ? `<button type="button" class="btn ghost sale-detail-btn" data-sale-id="${escapeHtml(sale.id)}">Apri vendita</button>` : ''}${buttonsHtml ? `<div class="action-bar" style="margin-top:6px">${buttonsHtml}</div>` : ''}`;
}

// Stessa logica di static/buy_admin/assets/app.js:proposalActions (riferimento
// comportamentale P9), riletta sullo stato reale della proposta. "expired" e'
// solo un suggerimento client-side: il backend ricontrolla sempre database_now.
function proposalActions(proposal) {
  if (proposal.status === 'draft') return ['edit', 'submitted', 'withdrawn'];
  if (proposal.status === 'submitted') {
    const actions = ['accepted', 'rejected', 'withdrawn'];
    const expiresAt = new Date(proposal.expires_at);
    if (!Number.isNaN(expiresAt.getTime()) && expiresAt.getTime() <= Date.now()) actions.push('expired');
    return actions;
  }
  return [];
}

function renderProposalActionButtons(pr) {
  const actions = proposalActions(pr);
  if (!actions.length) return '';
  return `<div class="action-bar">${actions.map((a) => `<button type="button" class="btn ghost proposal-action-btn" data-proposal-id="${escapeHtml(pr.id)}" data-action="${escapeHtml(a)}">${escapeHtml(PROPOSAL_ACTION_LABELS[a] || a)}</button>`).join('')}</div>`;
}

function renderProposte(items, proposalsError, sales, saleCancelConfirm) {
  if (proposalsError) {
    return `<div class="error-box">Proposte temporaneamente non disponibili: ${escapeHtml(proposalsError)}</div>`;
  }
  const table = renderTable(
    [
      { label: 'Immobile', render: (p) => escapeHtml(p.property_title || p.property_code || `Immobile #${p.property_id}`) },
      { label: 'Importo', render: (p) => formatPrice(p.amount) },
      { label: 'Stato', render: (p) => renderBadge(PROPOSAL_STATUS_LABELS[p.status] || p.status || '—', statusTone(p.status)) },
      { label: 'Scadenza', render: (p) => escapeHtml(formatDateTime(p.expires_at)) },
      { label: 'Note', render: (p) => escapeHtml(p.notes || '—') },
      { label: 'Vendita', render: (p) => renderVenditaCell(p, saleForProposal(p.id, sales), saleCancelConfirm) },
      { label: '', render: (p) => renderProposalActionButtons(p) },
    ],
    items,
    { emptyMessage: 'Nessuna proposta presente per questa richiesta.' },
  );
  return `
    <div class="action-bar" style="margin-bottom:12px">
      <button type="button" id="proposal-new-btn" class="btn primary">Nuova proposta</button>
    </div>
    <div id="proposal-feedback"></div>
    ${table}
  `;
}

// Etichetta di un abbinamento eleggibile nel selettore "Nuova proposta": mai
// un match_id digitato manualmente, solo scelta da un elenco di abbinamenti
// reali gia' presenti (stesso principio gia' applicato in P3 per il Contatto).
function matchOptionLabel(m) {
  const propertyLabel = m.property_title || m.property_code || `Immobile #${m.property_id}`;
  const scoreText = m.effective_score ?? m.score_total;
  return `${propertyLabel}${scoreText != null ? ` \u2014 punteggio ${scoreText}` : ''}${m.match_class ? ` (${m.match_class})` : ''}`;
}

// P25.5: stessa utility pura di 3 righe gia' duplicata localmente in
// immobile-dettaglio.js (toDateInputValue) e in activity-task-dialogs.js/
// contatto-dettaglio.js (toDatetimeLocalValue) - pattern di duplicazione
// deliberata gia' stabilito nel progetto per queste conversioni.
function toDateInputValue(value) {
  if (!value) return '';
  return String(value).slice(0, 10);
}

function toDatetimeLocalValue(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function proposalDateTimeLocal(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

// Converte il valore di un input datetime-local (orario locale del browser,
// senza timezone) in un ISO string con offset esplicito, come richiesto da
// proposal/schemas.py (expiry_requires_timezone). Stesso pattern di
// static/buy_admin/assets/app.js:proposalExpiry.
function proposalExpiryToIso(raw) {
  const trimmed = String(raw || '').trim();
  if (!trimmed) throw new Error('Data di scadenza non valida.');
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) throw new Error('Data di scadenza non valida.');
  return parsed.toISOString();
}

function proposalAmountValue(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) throw new Error('Importo deve essere maggiore di zero.');
  return value;
}

function buildProposalCreatePayload(dialogEl, idempotencyKey) {
  const matchSelect = dialogEl.querySelector('#proposal-match');
  const matchId = matchSelect ? Number(matchSelect.value) : NaN;
  if (!Number.isInteger(matchId) || matchId <= 0) throw new Error('Seleziona un abbinamento valido.');
  const notes = dialogEl.querySelector('#proposal-notes').value.trim();
  return {
    match_id: matchId,
    amount: proposalAmountValue(dialogEl.querySelector('#proposal-amount').value),
    expires_at: proposalExpiryToIso(dialogEl.querySelector('#proposal-expiry').value),
    notes: notes || null,
    idempotency_key: idempotencyKey,
  };
}

function buildProposalUpdatePayload(dialogEl) {
  const notes = dialogEl.querySelector('#proposal-notes').value.trim();
  return {
    amount: proposalAmountValue(dialogEl.querySelector('#proposal-amount').value),
    expires_at: proposalExpiryToIso(dialogEl.querySelector('#proposal-expiry').value),
    notes: notes || null,
  };
}

// --- Attività: nessuna relazione Attivita CORE <-> Richiesta BUY oggi -----

function renderAttivita() {
  return '<p class="muted">Non è disponibile oggi un collegamento tra Attività CORE e Richiesta BUY nelle API esistenti (core/repository.py: list_activities non filtra per richiesta). Gli eventi specifici BUY sono visibili nelle tab Visite e Storico.</p>';
}

// --- Task (CORE tasks via buy_request_task_links, sola lettura) -----------

function renderTask(items) {
  return renderTable(
    [
      { label: 'Titolo', render: (t) => escapeHtml(t.title || `Task #${t.id}`) },
      { label: 'Stato', render: (t) => renderBadge(t.status || '—', statusTone(t.status)) },
      { label: 'Priorità', render: (t) => escapeHtml(PRIORITY_LABELS[t.priority] || t.priority || '—') },
      { label: 'Scadenza', render: (t) => escapeHtml(formatDateTime(t.due_at)) },
      { label: 'Descrizione', render: (t) => escapeHtml(t.description || '—') },
    ],
    items,
    { emptyMessage: 'Nessun task collegato a questa richiesta.' },
  );
}

// --- Storico (buy_request_history) -----------------------------------------

function renderStorico(items) {
  const table = renderTable(
    [
      { label: 'Evento', render: (h) => renderBadge(HISTORY_EVENT_LABELS[h.event_type] || h.event_type || '—', 'gray') },
      { label: 'Quando', render: (h) => escapeHtml(formatDateTime(h.created_at)) },
      { label: 'Immobile', render: (h) => escapeHtml(h.property_title || '—') },
      { label: 'Task', render: (h) => escapeHtml(h.task_title || '—') },
      { label: 'Descrizione', render: (h) => escapeHtml(h.description || h.reason_code || '—') },
    ],
    items,
    { emptyMessage: 'Nessun evento registrato per questa richiesta.' },
  );
  return table;
}

// --- utility ---------------------------------------------------------------

function formatPrice(value) {
  // Ritorna sempre una stringa sicura da inserire in un template literal
  // (mai null/undefined): usato sia in celle di renderTable — dove un valore
  // non-stringa verrebbe interpolato letteralmente come "null" — sia nei
  // campi di detail-grid.
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}

function statusTone(status) {
  if (['active', 'satisfied', 'completed', 'accepted', 'open'].includes(status)) return 'ok';
  if (['paused', 'in_progress', 'submitted'].includes(status)) return 'warn';
  if (['closed', 'archived', 'rejected', 'expired', 'withdrawn', 'cancelled'].includes(status)) return 'danger';
  return 'gray';
}

function priorityTone(priority) {
  if (priority === 'urgent') return 'danger';
  if (priority === 'high') return 'warn';
  return 'gray';
}

function requirementTone(level) {
  if (level === 'required') return 'ok';
  if (level === 'excluded') return 'danger';
  return 'gray';
}

function matchClassTone(matchClass) {
  if (['excellent', 'strong'].includes(matchClass)) return 'ok';
  if (['good', 'possible'].includes(matchClass)) return 'warn';
  if (['weak', 'poor', 'incompatible'].includes(matchClass)) return 'danger';
  return 'gray';
}
