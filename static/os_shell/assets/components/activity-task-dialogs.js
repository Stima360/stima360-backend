// STIMA360 OS — activity-task-dialogs.js
// P25.1 — dialog condivisi per creare un'Attivita' (core.activities) o un Task
// (core.tasks), riusati sia da attivita.js (Sezione Attivita', contatto non
// preselezionato) sia da contatto-dettaglio.js (azioni rapide, contatto gia'
// noto). Estratti qui per evitare di duplicare due dialog quasi identici in
// due file diversi (principio esplicito P25: "estrai component/helper
// condiviso se il codice diventerebbe copiato in piu' viste").
//
// Contratti backend usati, VERIFICATI su core/router.py + core/schemas.py +
// core/enums.py prima di scrivere questo file (nessun valore/endpoint
// inventato):
//   POST   /api/core/activities   (ActivityCreate, extra="forbid")
//   DELETE /api/core/activities/{id}   (hard delete, nessun soft-delete)
//   POST   /api/core/tasks        (TaskCreate, extra="forbid")
//   PATCH  /api/core/tasks/{id}   (TaskUpdate, extra="forbid")
//   DELETE /api/core/tasks/{id}   (hard delete, nessun soft-delete)
//   GET    /api/core/leads?contact_id=&limit=   (core/router.py:76-89, usato
//          SOLO quando non e' gia' disponibile un elenco lead preesistente
//          per il contatto selezionato — vedi presetLeads sotto)
//
// NON esiste ActivityUpdate ne' PATCH /api/core/activities/{id} nel backend
// (verificato: core/router.py non lo espone, core/schemas.py non lo definisce):
// coerentemente questo file non offre "Modifica attivita'", solo creazione ed
// eliminazione, come da vincolo esplicito del brief P25.1.
//
// activity_type: core/enums.py::ACTIVITY_TYPES ha 8 valori, ma "valuation",
// "status_change" e "system" sono chiaramente generati da processi automatici
// (stima, cambio stato pipeline, eventi di sistema), non da un agente che
// registra manualmente un'interazione. Il selettore qui espone solo i 5 tipi
// che un agente crea davvero a mano (note/call/email/whatsapp/meeting): sono
// comunque valori reali e validi dell'enum, non e' un sottoinsieme inventato.

import { apiGet, apiDelete, apiPatch, apiPost } from '../core/api-client.js';
import { escapeHtml } from './st-table.js';
import { createContactPicker } from './contact-picker.js';

export const ACTIVITY_TYPE_LABELS = {
  note: 'Nota', call: 'Telefonata', email: 'Email', whatsapp: 'WhatsApp', meeting: 'Appuntamento',
};
export const ACTIVITY_DIRECTION_LABELS = { in: 'In entrata', out: 'In uscita', internal: 'Interna' };
export const TASK_PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };
export const TASK_STATUS_LABELS = { open: 'Da fare', in_progress: 'In corso', completed: 'Completato', cancelled: 'Annullato' };

function leadOptionLabel(lead) {
  const pipeline = lead.pipeline ? lead.pipeline.toUpperCase() : '—';
  return `Lead #${lead.id} — ${pipeline}${lead.stage ? ` · ${lead.stage}` : ''}`;
}

// Monta il blocco "Contatto" del dialog: se presetContact e' dato, il
// contatto e' fisso (mostrato come testo, nessuna ricerca); altrimenti monta
// il picker di ricerca condiviso. Ritorna { getContactId(), getLeadOptionsHtml
// aggiornato via onContactChange }.
function mountContactAndLeadFields(rootEl, { presetContact, presetLeads }) {
  const contactFieldEl = rootEl.querySelector('[data-contact-field]');
  const leadFieldEl = rootEl.querySelector('[data-lead-field]');
  const leadSelectEl = rootEl.querySelector('[data-lead-select]');
  let contactId = presetContact ? presetContact.id : null;

  function renderLeadOptions(leads) {
    const options = Array.isArray(leads) ? leads : [];
    leadSelectEl.innerHTML = '<option value="">— Nessun lead collegato —</option>'
      + options.map((l) => `<option value="${escapeHtml(l.id)}">${escapeHtml(leadOptionLabel(l))}</option>`).join('');
    leadFieldEl.hidden = options.length === 0;
  }

  if (presetContact) {
    contactFieldEl.innerHTML = `<div class="selected-contact-card"><div><strong>${escapeHtml(presetContact.label)}</strong></div></div>`;
    renderLeadOptions(presetLeads || []);
  } else {
    leadFieldEl.hidden = true;
    createContactPicker(contactFieldEl, {
      onChange: async (contact) => {
        contactId = contact ? contact.id : null;
        if (!contact) { renderLeadOptions([]); return; }
        leadSelectEl.innerHTML = '<option value="">Caricamento lead…</option>';
        leadFieldEl.hidden = false;
        try {
          const data = await apiGet(`/api/core/leads?contact_id=${contact.id}&limit=50`);
          renderLeadOptions(Array.isArray(data?.items) ? data.items : []);
        } catch (_error) {
          renderLeadOptions([]);
        }
      },
    });
  }

  return {
    getContactId: () => contactId,
    getLeadId: () => {
      const raw = leadSelectEl.value;
      return raw ? Number(raw) : null;
    },
  };
}

function toIsoOrNull(datetimeLocalValue) {
  const trimmed = String(datetimeLocalValue || '').trim();
  if (!trimmed) return null;
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) throw new Error('Data non valida.');
  return parsed.toISOString();
}

export function openNewActivityDialog(dialogEl, { presetContact = null, presetLeads = null, onSuccess } = {}) {
  dialogEl.innerHTML = `
    <form id="activity-form">
      <h3 class="section-title">Nuova attività</h3>
      <div class="form-field"><label>Contatto${presetContact ? '' : ' *'}</label><div data-contact-field></div></div>
      <div class="form-field" data-lead-field hidden><label>Lead collegato</label><select class="input" data-lead-select></select></div>
      <div class="form-grid-2">
        <div class="form-field">
          <label>Tipo *</label>
          <select id="activity-type" class="input" required>
            ${Object.keys(ACTIVITY_TYPE_LABELS).map((t) => `<option value="${t}">${escapeHtml(ACTIVITY_TYPE_LABELS[t])}</option>`).join('')}
          </select>
        </div>
        <div class="form-field">
          <label>Direzione</label>
          <select id="activity-direction" class="input">
            <option value="">—</option>
            ${Object.keys(ACTIVITY_DIRECTION_LABELS).map((d) => `<option value="${d}">${escapeHtml(ACTIVITY_DIRECTION_LABELS[d])}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Canale</label><input type="text" id="activity-channel" class="input" maxlength="50"></div>
        <div class="form-field"><label>Quando</label><input type="datetime-local" id="activity-occurred-at" class="input"></div>
      </div>
      <div class="form-field"><label>Oggetto</label><input type="text" id="activity-subject" class="input" maxlength="200"></div>
      <div class="form-field"><label>Descrizione</label><textarea id="activity-description" class="input"></textarea></div>
      <div class="form-field"><label>Esito</label><input type="text" id="activity-outcome" class="input" maxlength="100"></div>
      <div id="activity-form-error" class="field-error"></div>
      <div class="modal-actions">
        <button type="button" id="activity-form-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="activity-form-submit" class="btn primary">Salva</button>
      </div>
    </form>
  `;

  const fields = mountContactAndLeadFields(dialogEl, { presetContact, presetLeads });
  dialogEl.querySelector('#activity-form-cancel').addEventListener('click', () => dialogEl.close());

  let submitting = false;
  dialogEl.querySelector('#activity-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    const errorEl = dialogEl.querySelector('#activity-form-error');
    errorEl.textContent = '';

    const contactId = fields.getContactId();
    if (!contactId) {
      errorEl.textContent = 'Seleziona un contatto.';
      return;
    }

    let occurredAt;
    try {
      occurredAt = toIsoOrNull(dialogEl.querySelector('#activity-occurred-at').value);
    } catch (error) {
      errorEl.textContent = error.message;
      return;
    }

    const payload = {
      contact_id: contactId,
      lead_id: fields.getLeadId(),
      activity_type: dialogEl.querySelector('#activity-type').value,
      direction: dialogEl.querySelector('#activity-direction').value || null,
      channel: dialogEl.querySelector('#activity-channel').value.trim() || null,
      subject: dialogEl.querySelector('#activity-subject').value.trim() || null,
      description: dialogEl.querySelector('#activity-description').value.trim() || null,
      outcome: dialogEl.querySelector('#activity-outcome').value.trim() || null,
      occurred_at: occurredAt,
    };

    submitting = true;
    const submitBtn = dialogEl.querySelector('#activity-form-submit');
    const cancelBtn = dialogEl.querySelector('#activity-form-cancel');
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    submitBtn.textContent = 'Salvataggio…';
    try {
      await apiPost('/api/core/activities', payload);
      dialogEl.close();
      if (onSuccess) await onSuccess();
    } catch (error) {
      submitting = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      submitBtn.textContent = 'Salva';
      errorEl.textContent = error.message || 'Errore nel salvataggio.';
    }
  });

  dialogEl.showModal();
}

export async function deleteActivity(activityId) {
  await apiDelete(`/api/core/activities/${activityId}`);
}

export function openNewTaskDialog(dialogEl, { presetContact = null, presetLeads = null, onSuccess } = {}) {
  dialogEl.innerHTML = `
    <form id="task-form">
      <h3 class="section-title">Nuovo task</h3>
      <div class="form-field"><label>Contatto${presetContact ? '' : ' *'}</label><div data-contact-field></div></div>
      <div class="form-field" data-lead-field hidden><label>Lead collegato</label><select class="input" data-lead-select></select></div>
      <div class="form-field"><label>Titolo *</label><input type="text" id="task-title" class="input" maxlength="200" required></div>
      <div class="form-field"><label>Descrizione</label><textarea id="task-description" class="input"></textarea></div>
      <div class="form-grid-3">
        <div class="form-field">
          <label>Priorità</label>
          <select id="task-priority" class="input">
            ${Object.keys(TASK_PRIORITY_LABELS).map((p) => `<option value="${p}" ${p === 'normal' ? 'selected' : ''}>${escapeHtml(TASK_PRIORITY_LABELS[p])}</option>`).join('')}
          </select>
        </div>
        <div class="form-field"><label>Scadenza</label><input type="datetime-local" id="task-due-at" class="input"></div>
        <div class="form-field"><label>Tipo</label><input type="text" id="task-type" class="input" maxlength="50"></div>
      </div>
      <div class="form-field"><label>Assegnato a</label><input type="text" id="task-assigned-to" class="input" maxlength="200"></div>
      <div id="task-form-error" class="field-error"></div>
      <div class="modal-actions">
        <button type="button" id="task-form-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="task-form-submit" class="btn primary">Salva</button>
      </div>
    </form>
  `;

  const fields = mountContactAndLeadFields(dialogEl, { presetContact, presetLeads });
  dialogEl.querySelector('#task-form-cancel').addEventListener('click', () => dialogEl.close());

  let submitting = false;
  dialogEl.querySelector('#task-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    const errorEl = dialogEl.querySelector('#task-form-error');
    errorEl.textContent = '';

    const contactId = fields.getContactId();
    if (!contactId) {
      errorEl.textContent = 'Seleziona un contatto.';
      return;
    }
    const title = dialogEl.querySelector('#task-title').value.trim();
    if (!title) {
      errorEl.textContent = 'Il titolo è obbligatorio.';
      return;
    }

    let dueAt;
    try {
      dueAt = toIsoOrNull(dialogEl.querySelector('#task-due-at').value);
    } catch (error) {
      errorEl.textContent = error.message;
      return;
    }

    const payload = {
      contact_id: contactId,
      lead_id: fields.getLeadId(),
      title,
      description: dialogEl.querySelector('#task-description').value.trim() || null,
      task_type: dialogEl.querySelector('#task-type').value.trim() || null,
      priority: dialogEl.querySelector('#task-priority').value,
      due_at: dueAt,
      assigned_to: dialogEl.querySelector('#task-assigned-to').value.trim() || null,
    };

    submitting = true;
    const submitBtn = dialogEl.querySelector('#task-form-submit');
    const cancelBtn = dialogEl.querySelector('#task-form-cancel');
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    submitBtn.textContent = 'Salvataggio…';
    try {
      await apiPost('/api/core/tasks', payload);
      dialogEl.close();
      if (onSuccess) await onSuccess();
    } catch (error) {
      submitting = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      submitBtn.textContent = 'Salva';
      errorEl.textContent = error.message || 'Errore nel salvataggio.';
    }
  });

  dialogEl.showModal();
}

// Modifica task esistente: titolo/descrizione/tipo/priorità/scadenza/stato/
// assegnatario, tutti campi reali di TaskUpdate (core/schemas.py). completed_at
// NON viene mai inviato dal client: core/service.py::update_task lo deriva
// automaticamente da status (impostato a NOW() quando status=='completed',
// azzerato per open/in_progress/cancelled) — stesso principio gia' verificato
// per create_task, nessuna logica duplicata qui.
export function openEditTaskDialog(dialogEl, task, { onSuccess } = {}) {
  dialogEl.innerHTML = `
    <form id="task-edit-form">
      <h3 class="section-title">Modifica task</h3>
      <div class="form-field"><label>Titolo *</label><input type="text" id="task-edit-title" class="input" maxlength="200" required value="${escapeHtml(task.title || '')}"></div>
      <div class="form-field"><label>Descrizione</label><textarea id="task-edit-description" class="input">${escapeHtml(task.description || '')}</textarea></div>
      <div class="form-grid-3">
        <div class="form-field">
          <label>Priorità</label>
          <select id="task-edit-priority" class="input">
            ${Object.keys(TASK_PRIORITY_LABELS).map((p) => `<option value="${p}" ${p === task.priority ? 'selected' : ''}>${escapeHtml(TASK_PRIORITY_LABELS[p])}</option>`).join('')}
          </select>
        </div>
        <div class="form-field">
          <label>Stato</label>
          <select id="task-edit-status" class="input">
            ${Object.keys(TASK_STATUS_LABELS).map((s) => `<option value="${s}" ${s === task.status ? 'selected' : ''}>${escapeHtml(TASK_STATUS_LABELS[s])}</option>`).join('')}
          </select>
        </div>
        <div class="form-field"><label>Scadenza</label><input type="datetime-local" id="task-edit-due-at" class="input" value="${task.due_at ? toDatetimeLocalValue(task.due_at) : ''}"></div>
      </div>
      <div class="form-field"><label>Assegnato a</label><input type="text" id="task-edit-assigned-to" class="input" maxlength="200" value="${escapeHtml(task.assigned_to || '')}"></div>
      <div id="task-edit-form-error" class="field-error"></div>
      <div class="modal-actions">
        <button type="button" id="task-edit-form-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="task-edit-form-submit" class="btn primary">Salva</button>
      </div>
    </form>
  `;

  dialogEl.querySelector('#task-edit-form-cancel').addEventListener('click', () => dialogEl.close());

  let submitting = false;
  dialogEl.querySelector('#task-edit-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    const errorEl = dialogEl.querySelector('#task-edit-form-error');
    errorEl.textContent = '';

    const title = dialogEl.querySelector('#task-edit-title').value.trim();
    if (!title) {
      errorEl.textContent = 'Il titolo è obbligatorio.';
      return;
    }

    let dueAt;
    try {
      dueAt = toIsoOrNull(dialogEl.querySelector('#task-edit-due-at').value);
    } catch (error) {
      errorEl.textContent = error.message;
      return;
    }

    const payload = {
      title,
      description: dialogEl.querySelector('#task-edit-description').value.trim() || null,
      priority: dialogEl.querySelector('#task-edit-priority').value,
      status: dialogEl.querySelector('#task-edit-status').value,
      due_at: dueAt,
      assigned_to: dialogEl.querySelector('#task-edit-assigned-to').value.trim() || null,
    };

    submitting = true;
    const submitBtn = dialogEl.querySelector('#task-edit-form-submit');
    const cancelBtn = dialogEl.querySelector('#task-edit-form-cancel');
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    submitBtn.textContent = 'Salvataggio…';
    try {
      await apiPatch(`/api/core/tasks/${task.id}`, payload);
      dialogEl.close();
      if (onSuccess) await onSuccess();
    } catch (error) {
      submitting = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      submitBtn.textContent = 'Salva';
      errorEl.textContent = error.message || 'Errore nel salvataggio.';
    }
  });

  dialogEl.showModal();
}

export async function deleteTask(taskId) {
  await apiDelete(`/api/core/tasks/${taskId}`);
}

function toDatetimeLocalValue(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
