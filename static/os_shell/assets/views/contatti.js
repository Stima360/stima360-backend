// STIMA360 OS — contatti.js
// Lista Contatti: ricerca (nome/telefono/email) + apertura scheda dettaglio.
// Endpoint reale usato (verificato in core/router.py:40-47, prefix /api/core
// da core/router.py:21): GET /api/core/contacts?search=&limit=&offset=
// La ricerca è gia' server-side su display_name, first_name, last_name,
// company_name, email_normalized, phone_normalized (core/repository.py:45-63)
// quindi un solo campo di ricerca copre nome/telefono/email.
//
// P13: aggiunta creazione contatto CORE + assegnazione ruoli CORE.
// Contratto verificato in core/router.py, core/schemas.py, core/repository.py,
// migrations/001_core_contacts_leads.sql:
//  - POST /api/core/contacts (core/router.py:37-39) body ContactCreate
//    (core/schemas.py): contact_type ('person'|'company', default 'person'),
//    first_name, last_name, company_name, email, phone, secondary_phone,
//    source, status (default 'active'), notes. display_name NON va inviato:
//    il backend lo deriva da solo (core/service.py:create_contact). Vincoli
//    reali (root_validator + CHECK contacts_identity_chk in migration 001):
//    company richiede company_name; person richiede first_name O last_name
//    (o display_name, che qui non esponiamo mai in input). Nessun vincolo di
//    unicita' su email/telefono (nessuna UniqueViolation gestita in
//    repository.create_contact, nessun UNIQUE su email/phone in migration
//    001) quindi duplicati email/telefono sono ammessi dal backend: nessun
//    controllo preventivo lato frontend viene inventato per questo campo.
//  - POST /api/core/contacts/{id}/roles (core/router.py:63-65) body
//    ContactRoleCreate: role (enum CONTACT_ROLES, core/enums.py), is_primary
//    (default false), valid_from/valid_to (non esposti, non necessari per la
//    creazione), metadata (oggetto, inviato vuoto). Vincolo reale: UNIQUE
//    (contact_id, role) in contact_roles (migration 001) -> 409 se il ruolo
//    e' gia' assegnato (core/repository.py:94-106, gestito da
//    core/router.py:_translate). CONTACT_ROLES (core/enums.py) e' un enum
//    DISTINTO da property_contacts.role (property/router.py, P12): stesso
//    nome di campo "role" ma valori e tabella diversi. Qui usiamo solo
//    contact_roles.
//  - GET /api/core/contacts (usato anche qui per ricaricare la lista dopo la
//    creazione) NON include i ruoli nella risposta (core/repository.py:45-63);
//    GET /api/core/contacts/{contact_id} li include (core/repository.py:67-75)
//    ma non e' necessario qui: la scheda dettaglio (contatto-dettaglio.js)
//    li legge gia' da /api/crm/contacts/{id}/360, che legge le stesse tabelle.
//
// Nessun nuovo endpoint, nessun campo non previsto dallo schema backend.

import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, escapeHtml, formatDate } from '../components/st-table.js';

const PAGE_SIZE = 50;

// Stessa mappa gia' usata in contatto-dettaglio.js per i ruoli CORE
// (contact_roles.role, core/enums.py: CONTACT_ROLES). Duplicata qui
// deliberatamente: ogni vista della shell tiene le proprie label, come gia'
// avviene per PROPERTY_ROLE_LABELS in immobile-dettaglio.js vs
// contatto-dettaglio.js.
const CONTACT_ROLES = ['owner', 'seller', 'buyer', 'prospect', 'referrer', 'agency', 'professional', 'other'];
const ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', buyer: 'Acquirente', prospect: 'Potenziale cliente',
  referrer: 'Segnalatore', agency: 'Agenzia', professional: 'Professionista', other: 'Altro',
};

export async function renderContatti(container) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar">
        <input id="contatti-search" class="input" type="search" placeholder="Cerca per nome, telefono o email…">
        <button type="button" id="contatti-new-btn" class="btn primary">+ Nuovo contatto</button>
      </div>
      <div id="contatti-feedback"></div>
      <div id="contatti-list-area"><p class="muted">Caricamento…</p></div>
      <div id="contatti-pager" class="list-pager"></div>
    </div>
    <dialog id="contact-create-dialog" class="modal"></dialog>
  `;

  const searchInput = container.querySelector('#contatti-search');
  const listArea = container.querySelector('#contatti-list-area');
  const pagerArea = container.querySelector('#contatti-pager');
  const feedbackArea = container.querySelector('#contatti-feedback');
  const dialogEl = container.querySelector('#contact-create-dialog');

  let offset = 0;
  let debounceHandle = null;

  async function load() {
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const term = searchInput.value.trim();
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (term) params.set('search', term);

    let items = [];
    try {
      const data = await apiGet(`/api/core/contacts?${params.toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare i contatti: ${escapeHtml(error.message)}</div>`;
      return;
    }

    listArea.innerHTML = renderTable(
      [
        { label: 'Nome', render: (c) => `<strong>${escapeHtml(c.display_name || contactFallbackName(c))}</strong>` },
        { label: 'Email', render: (c) => escapeHtml(c.email || '—') },
        { label: 'Telefono', render: (c) => escapeHtml(c.phone || '—') },
        { label: 'Stato', render: (c) => escapeHtml(c.status || '—') },
        { label: 'Creato il', render: (c) => escapeHtml(formatDate(c.created_at)) },
      ],
      items,
      { emptyMessage: term ? 'Nessun contatto trovato per questa ricerca.' : 'Nessun contatto presente.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('contatti', [id]));

    pagerArea.innerHTML = `
      <button class="btn" id="contatti-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="contatti-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prevBtn = pagerArea.querySelector('#contatti-prev');
    const nextBtn = pagerArea.querySelector('#contatti-next');
    if (prevBtn) prevBtn.onclick = () => { offset = Math.max(0, offset - PAGE_SIZE); load(); };
    if (nextBtn) nextBtn.onclick = () => { offset += PAGE_SIZE; load(); };
  }

  searchInput.addEventListener('input', () => {
    offset = 0;
    clearTimeout(debounceHandle);
    debounceHandle = setTimeout(load, 300);
  });

  container.querySelector('#contatti-new-btn').addEventListener('click', () => {
    openCreateContactDialog(dialogEl, async (createdContact, roleFailures) => {
      offset = 0;
      await load();
      const name = escapeHtml(createdContact.display_name || contactFallbackName(createdContact));
      feedbackArea.innerHTML = roleFailures.length
        ? `<div class="success-box">Contatto creato (${name}), ma non è stato possibile assegnare tutti i ruoli.</div>`
        : `<div class="success-box">Contatto creato: ${name}.</div>`;
    });
  });

  await load();
}

function contactFallbackName(contact) {
  if (contact.contact_type === 'company') return contact.company_name || `Contatto #${contact.id}`;
  const parts = [contact.first_name, contact.last_name].filter(Boolean);
  return parts.length ? parts.join(' ') : `Contatto #${contact.id}`;
}

// --- P13: creazione contatto + ruoli CORE ----------------------------------

function openCreateContactDialog(dialogEl, onSuccess) {
  dialogEl.innerHTML = `
    <form id="contact-create-form">
      <h3 class="section-title">Nuovo contatto</h3>
      <div class="form-grid-2">
        <div class="form-field">
          <label>Tipo</label>
          <select id="cc-type" class="input">
            <option value="person" selected>Persona</option>
            <option value="company">Azienda</option>
          </select>
        </div>
        <div class="form-field">
          <label>Stato</label>
          <select id="cc-status" class="input">
            <option value="active" selected>Attivo</option>
            <option value="inactive">Non attivo</option>
            <option value="archived">Archiviato</option>
          </select>
        </div>
      </div>
      <div class="form-grid-2" id="cc-person-fields">
        <div class="form-field"><label>Nome</label><input type="text" id="cc-first" class="input" maxlength="100"></div>
        <div class="form-field"><label>Cognome</label><input type="text" id="cc-last" class="input" maxlength="100"></div>
      </div>
      <div class="form-field" id="cc-company-field" hidden>
        <label>Ragione sociale</label>
        <input type="text" id="cc-company" class="input" maxlength="200">
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Email</label><input type="email" id="cc-email" class="input" maxlength="320"></div>
        <div class="form-field"><label>Telefono</label><input type="text" id="cc-phone" class="input" maxlength="50"></div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Secondo telefono</label><input type="text" id="cc-phone2" class="input" maxlength="50"></div>
        <div class="form-field"><label>Fonte</label><input type="text" id="cc-source" class="input" maxlength="100"></div>
      </div>
      <div class="form-field"><label>Note</label><textarea id="cc-notes" class="input"></textarea></div>
      <div class="form-field">
        <label>Ruoli</label>
        <div class="action-bar">
          ${CONTACT_ROLES.map((r) => `
            <label class="checkbox-label"><input type="checkbox" class="cc-role" value="${r}"> ${escapeHtml(ROLE_LABELS[r] || r)}</label>
          `).join('')}
        </div>
      </div>
      <div id="contact-create-error" class="field-error"></div>
      <div class="modal-actions">
        <button type="button" id="contact-create-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="contact-create-submit" class="btn primary">Crea contatto</button>
      </div>
    </form>
  `;

  const typeSelect = dialogEl.querySelector('#cc-type');
  const personFields = dialogEl.querySelector('#cc-person-fields');
  const companyField = dialogEl.querySelector('#cc-company-field');

  function syncTypeFields() {
    const isCompany = typeSelect.value === 'company';
    personFields.hidden = isCompany;
    companyField.hidden = !isCompany;
  }
  typeSelect.addEventListener('change', syncTypeFields);
  syncTypeFields();

  dialogEl.querySelector('#contact-create-cancel').addEventListener('click', () => dialogEl.close());

  let submitting = false;
  const submitBtn = dialogEl.querySelector('#contact-create-submit');
  const cancelBtn = dialogEl.querySelector('#contact-create-cancel');
  const errorEl = dialogEl.querySelector('#contact-create-error');

  dialogEl.querySelector('#contact-create-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submitting) return;
    errorEl.textContent = '';

    const contactType = typeSelect.value;
    const firstName = dialogEl.querySelector('#cc-first').value.trim();
    const lastName = dialogEl.querySelector('#cc-last').value.trim();
    const companyName = dialogEl.querySelector('#cc-company').value.trim();

    // Stessa regola del root_validator backend (core/schemas.py:
    // ContactCreate.validate_contact) e del CHECK contacts_identity_chk
    // (migrations/001_core_contacts_leads.sql): azienda richiede ragione
    // sociale; persona richiede nome o cognome (qui non esponiamo
    // display_name come campo separato, quindi non e' un'alternativa
    // disponibile lato UI).
    if (contactType === 'company' && !companyName) {
      errorEl.textContent = 'La ragione sociale è obbligatoria per un contatto azienda.';
      return;
    }
    if (contactType === 'person' && !firstName && !lastName) {
      errorEl.textContent = 'Inserisci almeno nome o cognome per un contatto persona.';
      return;
    }

    const selectedRoles = Array.from(dialogEl.querySelectorAll('.cc-role:checked')).map((el) => el.value);

    const payload = {
      contact_type: contactType,
      first_name: firstName || null,
      last_name: lastName || null,
      company_name: companyName || null,
      email: dialogEl.querySelector('#cc-email').value.trim() || null,
      phone: dialogEl.querySelector('#cc-phone').value.trim() || null,
      secondary_phone: dialogEl.querySelector('#cc-phone2').value.trim() || null,
      source: dialogEl.querySelector('#cc-source').value.trim() || null,
      status: dialogEl.querySelector('#cc-status').value,
      notes: dialogEl.querySelector('#cc-notes').value.trim() || null,
    };

    submitting = true;
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    submitBtn.textContent = 'Creazione…';

    let createdContact;
    try {
      createdContact = await apiPost('/api/core/contacts', payload);
    } catch (error) {
      submitting = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      submitBtn.textContent = 'Crea contatto';
      errorEl.textContent = error.message || 'Errore nella creazione del contatto.';
      return;
    }

    // Contatto creato: da qui in avanti non si finge piu' un fallimento
    // totale. L'assegnazione ruoli e' un'operazione backend separata
    // (POST /api/core/contacts/{id}/roles, una chiamata per ruolo: il
    // backend non espone un endpoint multi-ruolo) quindi un eventuale
    // errore su un ruolo non invalida il contatto gia' creato.
    const roleFailures = [];
    for (const role of selectedRoles) {
      try {
        await apiPost(`/api/core/contacts/${createdContact.id}/roles`, {
          role,
          is_primary: false,
          metadata: {},
        });
      } catch (error) {
        roleFailures.push({ role, message: error.message });
      }
    }

    dialogEl.close();
    await onSuccess(createdContact, roleFailures);
  });

  dialogEl.showModal();
}
