// STIMA360 OS — automazione-dettaglio.js
// Scheda operativa di una singola regola FLOW (#/automazioni/{code}).
// Giustificazione per l'esistenza di questo file (il brief P6 lo richiede
// "solo se genuinamente giustificato"): GET /api/flow/rules/{code} espone
// informazioni reali (descrizione completa, priorità, raffreddamento, stato
// della verifica di sicurezza, chi/quando ha attivato) che non entrano in
// modo leggibile in una riga di tabella, e l'azione Attiva/Disattiva richiede
// un punto di conferma unico per evitare click accidentali dentro l'elenco.
//
// Endpoint reali verificati (flow/router.py, prefix /api/flow):
//   GET  /api/flow/rules/{code}                (router.py:21 — riga singola,
//        stesso shape di ogni elemento di GET /rules, nessuna chiamata extra)
//   POST /api/flow/rules/{code}/deactivate      (router.py:29 — nessun corpo,
//        sempre sicura: flow/repository.py:deactivate non ha precondizioni)
//   POST /api/flow/rules/{code}/activate        {activated_by} (router.py:27
//        — flow/repository.py:activate ha una precondizione REALE: la regola
//        deve avere last_simulation_status=='success' con hash parametri e
//        versione correnti, altrimenti solleva ConflictError con un messaggio
//        italiano già pronto e sicuro, es. "La regola deve essere simulata
//        con successo usando versione e parametri correnti prima
//        dell'attivazione." — mostrato qui VERBATIM in caso di rifiuto, non è
//        un errore tecnico/stack ma un messaggio applicativo scritto apposta)
//
// Cosa NON viene mostrato qui, e perché:
//  - parameters/default_parameters/allowed_parameters (JSONB tecnico): mai
//    mostrati. Editarli è esplicitamente fuori scope P6 ("non è un editor di
//    regole"); mostrarli anche in sola lettura rischierebbe di far percepire
//    questa scheda come un pannello di configurazione tecnico, uno degli
//    anti-obiettivi espliciti del brief. Chi deve modificarli usa la
//    FLOW Admin legacy (invariata).
//  - event_type (stringa tecnica tipo "lead.created"): non mostrato come
//    tale; la descrizione della regola (già scritta in linguaggio operativo
//    in flow/rules/registry.py) copre lo stesso concetto in modo leggibile.
//  - Nessun pulsante "Simula"/"Esegui ora": vedi automazioni.js per il
//    ragionamento (endpoint di test reale ma richiede un selettore entità,
//    rimandato).
//  - Nessuna cronologia esecuzioni filtrata per questa regola: GET
//    /api/flow/executions non supporta un filtro rule_code, quindi non è
//    possibile mostrare "le esecuzioni di questa regola" in modo
//    genuinamente completo senza filtrare lato client un elenco già
//    paginato (che sarebbe un sottoinsieme presentato come completo). La
//    cronologia generale resta consultabile in Automazioni → Cronologia.

import { apiGet, apiPost } from '../core/api-client.js';
import { getCredentials } from '../core/auth.js';
import { navigate } from '../core/router.js';
import { renderBadge, escapeHtml, formatDateTime } from '../components/st-table.js';

const ENTITY_TYPE_LABELS = {
  lead: 'Lead',
  property: 'Immobile',
  buy_request: 'Richiesta acquirente',
  match: 'Abbinamento',
  property_visit: 'Visita immobile',
  owner_feedback: 'Richiesta proprietario',
};

const PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };

const SIMULATION_STATUS_LABELS = {
  never_run: 'Mai verificata',
  success: 'Verifica superata',
  failed: 'Verifica non superata',
  outdated: 'Verifica non più valida (regola aggiornata)',
};

export async function renderAutomazioneDettaglio(container, params = []) {
  const code = params[0];
  if (!code) {
    container.innerHTML = '<div class="error-box">Identificativo automazione non valido.</div>';
    return;
  }

  container.innerHTML = '<p class="muted">Caricamento scheda automazione…</p>';

  let rule;
  try {
    rule = await apiGet(`/api/flow/rules/${encodeURIComponent(code)}`);
  } catch (error) {
    const notFound = /non trovato|not found/i.test(error.message || '');
    container.innerHTML = `<div class="error-box">${notFound ? 'Automazione non trovata.' : `Errore nel caricamento dell'automazione: ${escapeHtml(error.message)}`}</div>`;
    return;
  }

  render();

  function render() {
    container.innerHTML = `
      <p><a href="#/automazioni" id="back-link">← Torna alle automazioni</a></p>
      <div class="contact-header card">
        <h2>${escapeHtml(rule.name)}</h2>
        <div class="muted">${escapeHtml(rule.description || '—')}</div>
        <div class="badge-row" id="rule-badges"></div>
      </div>
      <div id="rule-action-feedback"></div>
      <div class="card panel" id="rule-detail-panel"></div>
    `;

    container.querySelector('#back-link').addEventListener('click', (event) => {
      event.preventDefault();
      navigate('automazioni');
    });

    container.querySelector('#rule-badges').innerHTML = `
      ${rule.is_active ? renderBadge('Attiva', 'ok') : renderBadge('Non attiva', 'gray')}
      ${renderBadge(ENTITY_TYPE_LABELS[rule.entity_type] || rule.entity_type, 'gray')}
      ${renderBadge(PRIORITY_LABELS[rule.priority] || rule.priority || '—', 'gray')}
    `;

    container.querySelector('#rule-detail-panel').innerHTML = renderDetailPanel(rule);
    bindActions();
  }

  function showFeedback(message, isError) {
    const el = container.querySelector('#rule-action-feedback');
    if (!el) return;
    el.innerHTML = isError
      ? `<div class="error-box">${escapeHtml(message)}</div>`
      : `<div class="success-box">${escapeHtml(message)}</div>`;
  }

  async function reload() {
    try {
      rule = await apiGet(`/api/flow/rules/${encodeURIComponent(code)}`);
    } catch (error) {
      showFeedback(`Impossibile aggiornare l'automazione: ${error.message || 'errore sconosciuto'}`, true);
      return;
    }
    render();
  }

  function bindActions() {
    const activateBtn = container.querySelector('#rule-activate');
    if (activateBtn) {
      activateBtn.addEventListener('click', async () => {
        activateBtn.disabled = true;
        activateBtn.textContent = 'Attivazione…';
        const activatedBy = getCredentials()?.username || null;
        try {
          await apiPost(`/api/flow/rules/${encodeURIComponent(code)}/activate`, { activated_by: activatedBy });
          await reload();
          showFeedback('Automazione attivata.', false);
        } catch (error) {
          activateBtn.disabled = false;
          activateBtn.textContent = 'Attiva';
          showFeedback(error.message || 'Errore nell\'attivazione.', true);
        }
      });
    }

    const deactivateBtn = container.querySelector('#rule-deactivate');
    if (deactivateBtn) {
      deactivateBtn.addEventListener('click', async () => {
        deactivateBtn.disabled = true;
        deactivateBtn.textContent = 'Disattivazione…';
        try {
          await apiPost(`/api/flow/rules/${encodeURIComponent(code)}/deactivate`, {});
          await reload();
          showFeedback('Automazione disattivata.', false);
        } catch (error) {
          deactivateBtn.disabled = false;
          deactivateBtn.textContent = 'Disattiva';
          showFeedback(error.message || 'Errore nella disattivazione.', true);
        }
      });
    }
  }
}

function renderDetailPanel(rule) {
  const cooldown = Number(rule.cooldown_minutes) || 0;
  return `
    <div class="list">
      <div class="list-item"><span class="muted">Ambito</span><span>${escapeHtml(ENTITY_TYPE_LABELS[rule.entity_type] || rule.entity_type)}</span></div>
      <div class="list-item"><span class="muted">Tempo minimo tra due esecuzioni sulla stessa pratica</span><span>${cooldown > 0 ? `${escapeHtml(cooldown)} minuti` : 'Nessuno'}</span></div>
      <div class="list-item"><span class="muted">Verifica di sicurezza</span><span>${renderBadge(SIMULATION_STATUS_LABELS[rule.last_simulation_status] || rule.last_simulation_status || '—', simulationTone(rule.last_simulation_status))}${rule.last_simulation_at ? ` <small class="muted">(${escapeHtml(formatDateTime(rule.last_simulation_at))})</small>` : ''}</span></div>
      <div class="list-item"><span class="muted">Stato</span><span>${rule.is_active ? `Attiva dal ${escapeHtml(formatDateTime(rule.activated_at))}${rule.activated_by ? ` (da ${escapeHtml(rule.activated_by)})` : ''}` : 'Non attiva: nessuna esecuzione reale finché non viene attivata'}</span></div>
    </div>
    <div class="action-bar" style="margin-top:16px">
      ${rule.is_active
        ? '<button type="button" id="rule-deactivate" class="btn ghost">Disattiva</button>'
        : '<button type="button" id="rule-activate" class="btn primary">Attiva</button>'}
    </div>
    ${!rule.is_active && rule.last_simulation_status !== 'success' ? '<p class="muted" style="margin-top:8px">L\'attivazione richiede una verifica di sicurezza superata con la versione e i parametri correnti della regola (eseguita dalla FLOW Admin). Se l\'attivazione viene rifiutata, il messaggio del backend viene mostrato qui sopra.</p>' : ''}
  `;
}

function simulationTone(status) {
  if (status === 'success') return 'ok';
  if (status === 'failed' || status === 'outdated') return 'danger';
  return 'gray';
}
