'use strict';

const ADMIN_API = '/api/owner/admin';

const ACCOUNT_STATUS_LABELS = Object.freeze({
  invited: 'Invitato',
  active: 'Attivo',
  disabled: 'Disabilitato',
});

const ACCESS_ROLE_LABELS = Object.freeze({
  owner: 'Proprietario',
  co_owner: 'Comproprietario',
  delegate: 'Delegato',
  legal_representative: 'Rappresentante legale',
});

const ACCESS_STATUS_LABELS = Object.freeze({
  active: 'Attivo',
  revoked: 'Revocato',
  expired: 'Scaduto',
});

const PUBLICATION_TYPE_LABELS = Object.freeze({
  general_update: 'Aggiornamento generale',
  marketing_update: 'Aggiornamento marketing',
  visit_update: 'Aggiornamento visite',
  feedback_summary: 'Sintesi feedback',
  strategy_update: 'Aggiornamento strategia',
  milestone: 'Traguardo',
});

const PUBLICATION_STATUS_LABELS = Object.freeze({
  draft: 'Draft',
  published: 'Pubblicata',
  archived: 'Archiviata',
});

const REQUEST_TYPE_LABELS = Object.freeze({
  contact_request: 'Richiesta di contatto',
  correction_request: 'Segnalazione correzione',
  general_message: 'Messaggio generale',
  strategy_feedback: 'Confronto sulla strategia',
  price_review: 'Revisione del prezzo',
  availability_update: 'Aggiornamento disponibilità',
  document_question: 'Domanda sui documenti',
});

const REQUEST_STATUS_LABELS = Object.freeze({
  new: 'Nuova',
  in_review: 'In gestione',
  handled: 'Gestita',
  closed: 'Chiusa',
});

const state = {
  credentials: null,
  authenticated: false,
  activeSection: 'dashboard',
  sessionGeneration: 0,
  loginGeneration: 0,
  dashboardGeneration: 0,
  accountsGeneration: 0,
  accessGeneration: 0,
  publicationsGeneration: 0,
  requestsGeneration: 0,
  mutationsInFlight: new Set(),
};

const el = {
  loginView: document.getElementById('login-view'),
  app: document.getElementById('admin-app'),
  loginForm: document.getElementById('admin-login-form'),
  username: document.getElementById('admin-username'),
  password: document.getElementById('admin-password'),
  loginSubmit: document.getElementById('admin-login-submit'),
  loginStatus: document.getElementById('admin-login-status'),
  logout: document.getElementById('admin-logout'),
  globalStatus: document.getElementById('admin-global-status'),
  sectionTitle: document.getElementById('section-title'),
  navDashboard: document.getElementById('nav-dashboard'),
  navAccounts: document.getElementById('nav-accounts'),
  navAccess: document.getElementById('nav-access'),
  navPublications: document.getElementById('nav-publications'),
  navRequests: document.getElementById('nav-requests'),
  dashboardSection: document.getElementById('section-dashboard'),
  accountsSection: document.getElementById('section-accounts'),
  accessSection: document.getElementById('section-access'),
  publicationsSection: document.getElementById('section-publications'),
  requestsSection: document.getElementById('section-requests'),
  dashboardLoading: document.getElementById('dashboard-loading'),
  dashboardError: document.getElementById('dashboard-error'),
  dashboardErrorMessage: document.getElementById('dashboard-error-message'),
  dashboardRetry: document.getElementById('dashboard-retry'),
  dashboardReload: document.getElementById('dashboard-reload'),
  dashboardContent: document.getElementById('dashboard-content'),
  accountForm: document.getElementById('account-create-form'),
  accountContactId: document.getElementById('account-contact-id'),
  accountLanguage: document.getElementById('account-language'),
  accountSubmit: document.getElementById('account-create-submit'),
  accountFormStatus: document.getElementById('account-form-status'),
  accountsLoading: document.getElementById('accounts-loading'),
  accountsEmpty: document.getElementById('accounts-empty'),
  accountsError: document.getElementById('accounts-error'),
  accountsErrorMessage: document.getElementById('accounts-error-message'),
  accountsRetry: document.getElementById('accounts-retry'),
  accountsReload: document.getElementById('accounts-reload'),
  accountsContent: document.getElementById('accounts-content'),
  accessForm: document.getElementById('access-create-form'),
  accessOwnerAccountId: document.getElementById('access-owner-account-id'),
  accessPropertyId: document.getElementById('access-property-id'),
  accessRole: document.getElementById('access-role'),
  accessPrimary: document.getElementById('access-primary'),
  accessValidUntil: document.getElementById('access-valid-until'),
  accessSubmit: document.getElementById('access-create-submit'),
  accessFormStatus: document.getElementById('access-form-status'),
  accessLoading: document.getElementById('access-loading'),
  accessEmpty: document.getElementById('access-empty'),
  accessError: document.getElementById('access-error'),
  accessErrorMessage: document.getElementById('access-error-message'),
  accessRetry: document.getElementById('access-retry'),
  accessReload: document.getElementById('access-reload'),
  accessContent: document.getElementById('access-content'),
  publicationForm: document.getElementById('publication-create-form'),
  publicationPropertyId: document.getElementById('publication-property-id'),
  publicationType: document.getElementById('publication-type'),
  publicationTitle: document.getElementById('publication-title'),
  publicationSummary: document.getElementById('publication-summary'),
  publicationBody: document.getElementById('publication-body'),
  publicationAckRequired: document.getElementById('publication-ack-required'),
  publicationSubmit: document.getElementById('publication-create-submit'),
  publicationFormStatus: document.getElementById('publication-form-status'),
  publicationsLoading: document.getElementById('publications-loading'),
  publicationsEmpty: document.getElementById('publications-empty'),
  publicationsError: document.getElementById('publications-error'),
  publicationsErrorMessage: document.getElementById('publications-error-message'),
  publicationsRetry: document.getElementById('publications-retry'),
  publicationsReload: document.getElementById('publications-reload'),
  publicationsContent: document.getElementById('publications-content'),
  requestsLoading: document.getElementById('requests-loading'),
  requestsEmpty: document.getElementById('requests-empty'),
  requestsError: document.getElementById('requests-error'),
  requestsErrorMessage: document.getElementById('requests-error-message'),
  requestsRetry: document.getElementById('requests-retry'),
  requestsReload: document.getElementById('requests-reload'),
  requestsContent: document.getElementById('requests-content'),
};

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function encodeBasic(username, password) {
  const bytes = new TextEncoder().encode(`${username}:${password}`);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `Basic ${btoa(binary)}`;
}

function setStatus(node, message, kind = '') {
  node.textContent = message || '';
  node.classList.toggle('error', kind === 'error');
  node.classList.toggle('success', kind === 'success');
}

function statusMessage(error) {
  if (!(error instanceof ApiError)) return 'Errore di connessione. Riprova.';
  if (error.status === 401) return 'Credenziali non valide.';
  if (error.status === 403) return 'Accesso non autorizzato.';
  if (error.status === 404) return 'Risorsa non disponibile.';
  if (error.status === 409) return 'Operazione non più valida nello stato corrente.';
  if (error.status === 422) return 'Controlla i dati inseriti e riprova.';
  if (error.status === 429) return 'Troppe richieste. Riprova tra poco.';
  if (error.status === 503) return 'Servizio amministrativo non disponibile.';
  if (error.status >= 500) return 'Servizio temporaneamente non disponibile.';
  return 'Operazione non riuscita. Riprova.';
}

async function request(path, options = {}, credentials = state.credentials) {
  if (!credentials || !credentials.username || !credentials.password) {
    throw new ApiError(401, 'Credenziali mancanti');
  }

  const headers = Object.assign({}, options.headers || {}, {
    Authorization: encodeBasic(credentials.username, credentials.password),
  });
  if (options.json !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(ADMIN_API + path, {
      method: options.method || 'GET',
      headers,
      body: options.json === undefined ? undefined : JSON.stringify(options.json),
      cache: 'no-store',
      credentials: 'same-origin',
    });
  } catch (_error) {
    throw new ApiError(0, 'Network error');
  }

  if (!response.ok) throw new ApiError(response.status, 'HTTP error');
  if (response.status === 204) return null;
  return response.json();
}

function invalidatePending() {
  state.sessionGeneration += 1;
  state.loginGeneration += 1;
  state.dashboardGeneration += 1;
  state.accountsGeneration += 1;
  state.accessGeneration += 1;
  state.publicationsGeneration += 1;
  state.requestsGeneration += 1;
  state.mutationsInFlight.clear();
}

function resetDynamicUi() {
  el.dashboardContent.replaceChildren();
  el.accountsContent.replaceChildren();
  el.accessContent.replaceChildren();
  el.publicationsContent.replaceChildren();
  el.requestsContent.replaceChildren();
  setStatus(el.globalStatus, '');
  setStatus(el.accountFormStatus, '');
  setStatus(el.accessFormStatus, '');
  setStatus(el.publicationFormStatus, '');
  el.accountForm.reset();
  el.accountLanguage.value = 'it';
  el.accessForm.reset();
  el.accessRole.value = 'owner';
  el.publicationForm.reset();
  el.publicationType.value = 'general_update';
}

function logout(message = '') {
  invalidatePending();
  if (state.credentials) {
    state.credentials.username = '';
    state.credentials.password = '';
  }
  state.credentials = null;
  state.authenticated = false;
  state.activeSection = 'dashboard';
  el.username.value = '';
  el.password.value = '';
  el.app.hidden = true;
  el.loginView.hidden = false;
  el.loginSubmit.disabled = false;
  resetDynamicUi();
  setStatus(el.loginStatus, message);
  el.username.focus();
}

function forceLoginAfterUnauthorized() {
  logout('Credenziali non valide.');
}

function isCurrent(section, generation, sessionGeneration) {
  if (!state.authenticated) return false;
  if (sessionGeneration !== state.sessionGeneration) return false;
  if (section === 'dashboard') return generation === state.dashboardGeneration && state.activeSection === 'dashboard';
  if (section === 'accounts') return generation === state.accountsGeneration && state.activeSection === 'accounts';
  if (section === 'access') return generation === state.accessGeneration && state.activeSection === 'access';
  if (section === 'publications') return generation === state.publicationsGeneration && state.activeSection === 'publications';
  if (section === 'requests') return generation === state.requestsGeneration && state.activeSection === 'requests';
  return false;
}

function handleApiError(error, targetMessage) {
  if (error instanceof ApiError && error.status === 401) {
    forceLoginAfterUnauthorized();
    return true;
  }
  setStatus(targetMessage, statusMessage(error), 'error');
  return false;
}

function createMeta(label, value) {
  const item = document.createElement('div');
  item.className = 'meta-item';
  const labelNode = document.createElement('span');
  labelNode.className = 'meta-label';
  labelNode.textContent = label;
  const valueNode = document.createElement('span');
  valueNode.className = 'meta-value';
  valueNode.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  item.append(labelNode, valueNode);
  return item;
}

function createStatusBadge(rawStatus, labels) {
  const badge = document.createElement('span');
  badge.className = `status-badge ${labels[rawStatus] ? rawStatus : ''}`.trim();
  badge.textContent = labels[rawStatus] || 'Stato non disponibile';
  return badge;
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('it-IT', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function numberValue(value) {
  return Number.isFinite(Number(value)) ? String(Number(value)) : '—';
}

function dashboardCard(label, value) {
  const card = document.createElement('article');
  card.className = 'kpi-card';
  const number = document.createElement('p');
  number.className = 'kpi-value';
  number.textContent = numberValue(value);
  const text = document.createElement('p');
  text.className = 'kpi-label';
  text.textContent = label;
  card.append(number, text);
  return card;
}

function renderDashboard(data) {
  el.dashboardContent.replaceChildren(
    dashboardCard('Account attivi', data && data.active_accounts),
    dashboardCard('Accessi attivi', data && data.active_access),
    dashboardCard('Pubblicazioni online', data && data.published),
    dashboardCard('Richieste nuove', data && data.new_feedback),
  );
  el.dashboardLoading.hidden = true;
  el.dashboardError.hidden = true;
  el.dashboardContent.hidden = false;
}

function setDashboardState(mode, message = '') {
  el.dashboardLoading.hidden = mode !== 'loading';
  el.dashboardError.hidden = mode !== 'error';
  el.dashboardContent.hidden = mode !== 'content';
  if (mode === 'error') el.dashboardErrorMessage.textContent = message;
}

async function loadDashboard() {
  if (!state.authenticated || state.activeSection !== 'dashboard') return;
  const generation = ++state.dashboardGeneration;
  const sessionGeneration = state.sessionGeneration;
  setDashboardState('loading');
  try {
    const data = await request('/dashboard');
    if (!isCurrent('dashboard', generation, sessionGeneration)) return;
    renderDashboard(data || {});
  } catch (error) {
    if (!isCurrent('dashboard', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setDashboardState('error', statusMessage(error));
  }
}

function accountTitle(account) {
  if (account && account.display_name) return String(account.display_name);
  if (account && account.id !== undefined) return `Account #${account.id}`;
  return 'Account proprietario';
}

function renderInlineConfirmation(container, message, onConfirm) {
  container.replaceChildren();
  const box = document.createElement('div');
  box.className = 'inline-confirm';
  const text = document.createElement('p');
  text.textContent = message;
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = 'button danger small';
  confirm.textContent = 'Conferma';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'button secondary small';
  cancel.textContent = 'Annulla';
  confirm.addEventListener('click', async () => {
    confirm.disabled = true;
    cancel.disabled = true;
    await onConfirm();
  });
  cancel.addEventListener('click', () => container.replaceChildren());
  box.append(text, confirm, cancel);
  container.append(box);
}

async function mutateAccount(accountId, action, actionArea) {
  const key = `account:${action}:${accountId}`;
  if (state.mutationsInFlight.has(key) || !state.authenticated) return;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  setStatus(el.accountFormStatus, action === 'enable' ? 'Abilitazione in corso…' : 'Disabilitazione in corso…');
  try {
    await request(`/accounts/${encodeURIComponent(accountId)}/${action}`, { method: 'POST' });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    setStatus(el.accountFormStatus, action === 'enable' ? 'Account abilitato.' : 'Account disabilitato.', 'success');
    if (state.activeSection === 'accounts') await loadAccounts();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.accountFormStatus);
    if (actionArea) actionArea.replaceChildren();
  } finally {
    state.mutationsInFlight.delete(key);
  }
}

function renderAccount(account) {
  const card = document.createElement('article');
  card.className = 'entity-card';
  const header = document.createElement('div');
  header.className = 'entity-card-header';
  const title = document.createElement('h4');
  title.className = 'entity-title';
  title.textContent = accountTitle(account);
  header.append(title, createStatusBadge(account && account.status, ACCOUNT_STATUS_LABELS));

  const meta = document.createElement('div');
  meta.className = 'entity-meta';
  meta.append(
    createMeta('ID account', account && account.id),
    createMeta('Email', account && account.email),
    createMeta('Lingua', account && account.preferred_language),
  );

  const actions = document.createElement('div');
  actions.className = 'entity-actions';
  const confirmArea = document.createElement('div');
  confirmArea.className = 'confirm-area';

  if (account && (account.status === 'disabled' || account.status === 'invited')) {
    const enable = document.createElement('button');
    enable.type = 'button';
    enable.className = 'button secondary small';
    enable.textContent = 'Abilita';
    enable.addEventListener('click', async () => {
      enable.disabled = true;
      await mutateAccount(account.id, 'enable', confirmArea);
      enable.disabled = false;
    });
    actions.append(enable);
  }

  if (account && account.status !== 'disabled') {
    const disable = document.createElement('button');
    disable.type = 'button';
    disable.className = 'button danger small';
    disable.textContent = 'Disabilita';
    disable.addEventListener('click', () => {
      renderInlineConfirmation(
        confirmArea,
        'Confermi la disabilitazione di questo account?',
        () => mutateAccount(account.id, 'disable', confirmArea),
      );
    });
    actions.append(disable);
  }

  card.append(header, meta, actions, confirmArea);
  return card;
}

function setAccountsState(mode, message = '') {
  el.accountsLoading.hidden = mode !== 'loading';
  el.accountsEmpty.hidden = mode !== 'empty';
  el.accountsError.hidden = mode !== 'error';
  el.accountsContent.hidden = mode !== 'content';
  if (mode === 'error') el.accountsErrorMessage.textContent = message;
}

function renderAccounts(items) {
  el.accountsContent.replaceChildren();
  if (!Array.isArray(items) || items.length === 0) {
    setAccountsState('empty');
    return;
  }
  for (const account of items) el.accountsContent.append(renderAccount(account || {}));
  setAccountsState('content');
}

async function loadAccounts() {
  if (!state.authenticated || state.activeSection !== 'accounts') return;
  const generation = ++state.accountsGeneration;
  const sessionGeneration = state.sessionGeneration;
  setAccountsState('loading');
  try {
    const data = await request('/accounts');
    if (!isCurrent('accounts', generation, sessionGeneration)) return;
    renderAccounts(data && Array.isArray(data.items) ? data.items : []);
  } catch (error) {
    if (!isCurrent('accounts', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setAccountsState('error', statusMessage(error));
  }
}

function parsePositiveInt(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

async function submitAccount(event) {
  event.preventDefault();
  if (!state.authenticated || state.mutationsInFlight.has('account:create')) return;
  const contactId = parsePositiveInt(el.accountContactId.value);
  const language = el.accountLanguage.value.trim();
  if (contactId === null) {
    setStatus(el.accountFormStatus, 'Inserisci un ID contatto valido.', 'error');
    el.accountContactId.focus();
    return;
  }
  if (!language || language.length > 10) {
    setStatus(el.accountFormStatus, 'Inserisci una lingua preferita valida.', 'error');
    el.accountLanguage.focus();
    return;
  }

  state.mutationsInFlight.add('account:create');
  const sessionGeneration = state.sessionGeneration;
  el.accountSubmit.disabled = true;
  setStatus(el.accountFormStatus, 'Creazione in corso…');
  try {
    await request('/accounts', {
      method: 'POST',
      json: { contact_id: contactId, preferred_language: language },
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    el.accountForm.reset();
    el.accountLanguage.value = 'it';
    setStatus(el.accountFormStatus, 'Account proprietario creato.', 'success');
    if (state.activeSection === 'accounts') await loadAccounts();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.accountFormStatus);
  } finally {
    state.mutationsInFlight.delete('account:create');
    if (sessionGeneration === state.sessionGeneration) el.accountSubmit.disabled = false;
  }
}

function renderAccessRow(access) {
  const card = document.createElement('article');
  card.className = 'entity-card';
  const header = document.createElement('div');
  header.className = 'entity-card-header';
  const title = document.createElement('h4');
  title.className = 'entity-title';
  title.textContent = `Accesso #${access && access.id !== undefined ? access.id : '—'}`;
  header.append(title, createStatusBadge(access && access.access_status, ACCESS_STATUS_LABELS));

  const meta = document.createElement('div');
  meta.className = 'entity-meta';
  meta.append(
    createMeta('ID account', access && access.owner_account_id),
    createMeta('ID immobile', access && access.property_id),
    createMeta('Ruolo', ACCESS_ROLE_LABELS[access && access.access_role] || 'Ruolo non disponibile'),
    createMeta('Principale', access && access.is_primary === true ? 'Sì' : 'No'),
    createMeta('Valido fino al', formatDate(access && access.valid_until)),
  );

  const actions = document.createElement('div');
  actions.className = 'entity-actions';
  const confirmArea = document.createElement('div');
  confirmArea.className = 'confirm-area';
  if (access && access.access_status === 'active') {
    const revoke = document.createElement('button');
    revoke.type = 'button';
    revoke.className = 'button danger small';
    revoke.textContent = 'Revoca accesso';
    revoke.addEventListener('click', () => {
      renderInlineConfirmation(
        confirmArea,
        'Confermi la revoca di questo accesso?',
        () => revokeAccess(access.id, confirmArea),
      );
    });
    actions.append(revoke);
  }

  card.append(header, meta, actions, confirmArea);
  return card;
}

function setAccessState(mode, message = '') {
  el.accessLoading.hidden = mode !== 'loading';
  el.accessEmpty.hidden = mode !== 'empty';
  el.accessError.hidden = mode !== 'error';
  el.accessContent.hidden = mode !== 'content';
  if (mode === 'error') el.accessErrorMessage.textContent = message;
}

function renderAccess(items) {
  el.accessContent.replaceChildren();
  if (!Array.isArray(items) || items.length === 0) {
    setAccessState('empty');
    return;
  }
  for (const access of items) el.accessContent.append(renderAccessRow(access || {}));
  setAccessState('content');
}

async function loadAccess() {
  if (!state.authenticated || state.activeSection !== 'access') return;
  const generation = ++state.accessGeneration;
  const sessionGeneration = state.sessionGeneration;
  setAccessState('loading');
  try {
    const data = await request('/access');
    if (!isCurrent('access', generation, sessionGeneration)) return;
    renderAccess(data && Array.isArray(data.items) ? data.items : []);
  } catch (error) {
    if (!isCurrent('access', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setAccessState('error', statusMessage(error));
  }
}

function isoFromLocal(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

async function submitAccess(event) {
  event.preventDefault();
  if (!state.authenticated || state.mutationsInFlight.has('access:create')) return;
  const ownerAccountId = parsePositiveInt(el.accessOwnerAccountId.value);
  const propertyId = parsePositiveInt(el.accessPropertyId.value);
  const role = el.accessRole.value;
  if (ownerAccountId === null) {
    setStatus(el.accessFormStatus, 'Inserisci un ID account proprietario valido.', 'error');
    el.accessOwnerAccountId.focus();
    return;
  }
  if (propertyId === null) {
    setStatus(el.accessFormStatus, 'Inserisci un ID immobile valido.', 'error');
    el.accessPropertyId.focus();
    return;
  }
  if (!Object.prototype.hasOwnProperty.call(ACCESS_ROLE_LABELS, role)) {
    setStatus(el.accessFormStatus, 'Seleziona un ruolo accesso valido.', 'error');
    el.accessRole.focus();
    return;
  }
  let validUntil = null;
  if (el.accessValidUntil.value) {
    validUntil = isoFromLocal(el.accessValidUntil.value);
    if (!validUntil) {
      setStatus(el.accessFormStatus, 'Inserisci una data di validità corretta.', 'error');
      el.accessValidUntil.focus();
      return;
    }
  }

  state.mutationsInFlight.add('access:create');
  const sessionGeneration = state.sessionGeneration;
  el.accessSubmit.disabled = true;
  setStatus(el.accessFormStatus, 'Creazione accesso in corso…');
  try {
    await request('/access', {
      method: 'POST',
      json: {
        owner_account_id: ownerAccountId,
        property_id: propertyId,
        access_role: role,
        is_primary: el.accessPrimary.checked === true,
        valid_until: validUntil,
      },
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    el.accessForm.reset();
    el.accessRole.value = 'owner';
    setStatus(el.accessFormStatus, 'Accesso creato.', 'success');
    if (state.activeSection === 'access') await loadAccess();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.accessFormStatus);
  } finally {
    state.mutationsInFlight.delete('access:create');
    if (sessionGeneration === state.sessionGeneration) el.accessSubmit.disabled = false;
  }
}

async function revokeAccess(accessId, actionArea) {
  const key = `access:revoke:${accessId}`;
  if (state.mutationsInFlight.has(key) || !state.authenticated) return;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  setStatus(el.accessFormStatus, 'Revoca in corso…');
  try {
    await request(`/access/${encodeURIComponent(accessId)}/revoke`, { method: 'POST' });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    setStatus(el.accessFormStatus, 'Accesso revocato.', 'success');
    if (state.activeSection === 'access') await loadAccess();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.accessFormStatus);
    if (actionArea) actionArea.replaceChildren();
  } finally {
    state.mutationsInFlight.delete(key);
  }
}


function setPublicationsState(mode, message = '') {
  el.publicationsLoading.hidden = mode !== 'loading';
  el.publicationsEmpty.hidden = mode !== 'empty';
  el.publicationsError.hidden = mode !== 'error';
  el.publicationsContent.hidden = mode !== 'content';
  if (mode === 'error') el.publicationsErrorMessage.textContent = message;
}

function publicationTypeLabel(value) {
  return PUBLICATION_TYPE_LABELS[value] || 'Tipo non disponibile';
}

function publicationStatusBadge(status) {
  return createStatusBadge(status, PUBLICATION_STATUS_LABELS);
}

function publicationPayloadFromValues(propertyId, type, title, summary, body, acknowledgementRequired) {
  return {
    property_id: propertyId,
    publication_type: type,
    title,
    summary: summary || null,
    body,
    acknowledgement_required: acknowledgementRequired === true,
  };
}

function validatePublicationValues(values, propertyRequired = true) {
  if (propertyRequired && parsePositiveInt(values.propertyId) === null) return 'Inserisci un ID immobile valido.';
  if (!Object.prototype.hasOwnProperty.call(PUBLICATION_TYPE_LABELS, values.type)) return 'Seleziona un tipo pubblicazione valido.';
  if (!values.title || values.title.length > 200) return 'Inserisci un titolo da 1 a 200 caratteri.';
  if (values.summary.length > 1000) return 'La sintesi non può superare 1000 caratteri.';
  if (!values.body || values.body.length > 20000) return 'Inserisci un contenuto da 1 a 20000 caratteri.';
  return '';
}

function publicationEditor(publication, mode, onSave) {
  const form = document.createElement('form');
  form.className = 'inline-editor';
  form.setAttribute('data-publication-editor', mode);

  const typeField = document.createElement('div');
  typeField.className = 'field';
  const typeLabel = document.createElement('label');
  typeLabel.textContent = 'Tipo pubblicazione';
  const type = document.createElement('select');
  const editorKey = `${mode}-${publication && publication.id !== undefined ? publication.id : 'new'}`;
  type.id = `publication-editor-type-${editorKey}`;
  typeLabel.setAttribute('for', type.id);
  type.setAttribute('data-field', 'publication_type');
  const typePairs = [
    ['general_update', 'Aggiornamento generale'],
    ['marketing_update', 'Aggiornamento marketing'],
    ['visit_update', 'Aggiornamento visite'],
    ['feedback_summary', 'Sintesi feedback'],
    ['strategy_update', 'Aggiornamento strategia'],
    ['milestone', 'Traguardo'],
  ];
  for (const pair of typePairs) {
    const option = document.createElement('option');
    option.value = pair[0];
    option.textContent = pair[1];
    type.append(option);
  }
  type.value = publication.publication_type || 'general_update';
  typeField.append(typeLabel, type);

  const titleField = document.createElement('div');
  titleField.className = 'field';
  const titleLabel = document.createElement('label');
  titleLabel.textContent = 'Titolo';
  const title = document.createElement('input');
  title.type = 'text';
  title.id = `publication-editor-title-${editorKey}`;
  titleLabel.setAttribute('for', title.id);
  title.value = publication.title || '';
  title.setAttribute('maxlength', '200');
  title.setAttribute('data-field', 'title');
  titleField.append(titleLabel, title);

  const summaryField = document.createElement('div');
  summaryField.className = 'field field-span-2';
  const summaryLabel = document.createElement('label');
  summaryLabel.textContent = 'Sintesi';
  const summary = document.createElement('textarea');
  summary.id = `publication-editor-summary-${editorKey}`;
  summaryLabel.setAttribute('for', summary.id);
  summary.value = publication.summary || '';
  summary.setAttribute('maxlength', '1000');
  summary.setAttribute('rows', '3');
  summary.setAttribute('data-field', 'summary');
  summaryField.append(summaryLabel, summary);

  const bodyField = document.createElement('div');
  bodyField.className = 'field field-span-2';
  const bodyLabel = document.createElement('label');
  bodyLabel.textContent = 'Contenuto';
  const body = document.createElement('textarea');
  body.id = `publication-editor-body-${editorKey}`;
  bodyLabel.setAttribute('for', body.id);
  body.value = publication.body || '';
  body.setAttribute('maxlength', '20000');
  body.setAttribute('rows', '6');
  body.setAttribute('data-field', 'body');
  bodyField.append(bodyLabel, body);

  const ackField = document.createElement('div');
  ackField.className = 'field checkbox-field';
  const ack = document.createElement('input');
  const ackLabel = document.createElement('label');
  ack.type = 'checkbox';
  ack.id = `publication-editor-ack-${editorKey}`;
  ackLabel.setAttribute('for', ack.id);
  ack.checked = publication.acknowledgement_required === true;
  ack.setAttribute('data-field', 'acknowledgement_required');
  ackLabel.textContent = 'Richiedi presa visione';
  ackField.append(ack, ackLabel);

  const actions = document.createElement('div');
  actions.className = 'editor-actions';
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'button primary small';
  save.textContent = mode === 'edit' ? 'Salva modifiche' : 'Crea nuova versione';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'button secondary small';
  cancel.textContent = 'Annulla';
  actions.append(save, cancel);
  const status = document.createElement('p');
  status.className = 'status-message editor-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  form.append(typeField, titleField, summaryField, bodyField, ackField, actions, status);
  let saving = false;
  cancel.addEventListener('click', () => { form.hidden = true; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving) return;
    const values = {
      propertyId: publication.property_id,
      type: type.value,
      title: title.value.trim(),
      summary: summary.value.trim(),
      body: body.value.trim(),
      acknowledgementRequired: ack.checked === true,
    };
    const problem = validatePublicationValues(values, mode === 'supersede');
    if (problem) {
      setStatus(status, problem, 'error');
      return;
    }
    saving = true;
    save.disabled = true;
    cancel.disabled = true;
    setStatus(status, mode === 'edit' ? 'Salvataggio in corso…' : 'Creazione versione in corso…');
    const ok = await onSave(values, status);
    if (!ok) {
      saving = false;
      save.disabled = false;
      cancel.disabled = false;
    }
  });
  return form;
}

function renderPublication(publication) {
  const card = document.createElement('article');
  card.className = 'entity-card publication-card';
  const header = document.createElement('div');
  header.className = 'entity-card-header';
  const title = document.createElement('h4');
  title.className = 'entity-title';
  title.textContent = publication && publication.title ? String(publication.title) : `Pubblicazione #${publication && publication.id !== undefined ? publication.id : '—'}`;
  header.append(title, publicationStatusBadge(publication && publication.status));

  const meta = document.createElement('div');
  meta.className = 'entity-meta';
  meta.append(
    createMeta('ID', publication && publication.id),
    createMeta('ID immobile', publication && publication.property_id),
    createMeta('Tipo', publicationTypeLabel(publication && publication.publication_type)),
    createMeta('Versione', publication && publication.version_number),
    createMeta('Creata', formatDate(publication && publication.created_at)),
    createMeta('Pubblicata', formatDate(publication && publication.published_at)),
    createMeta('Presa visione', publication && publication.acknowledgement_required === true ? 'Richiesta' : 'Non richiesta'),
  );

  if (publication && publication.summary) {
    const summary = document.createElement('p');
    summary.className = 'entity-copy';
    summary.textContent = String(publication.summary);
    card.append(header, meta, summary);
  } else {
    card.append(header, meta);
  }

  const body = document.createElement('p');
  body.className = 'entity-copy body-copy';
  body.textContent = publication && publication.body ? String(publication.body) : '—';
  card.append(body);

  const actions = document.createElement('div');
  actions.className = 'entity-actions';
  const actionArea = document.createElement('div');
  actionArea.className = 'action-area';

  if (publication && publication.status === 'draft') {
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'button secondary small';
    edit.textContent = 'Modifica draft';
    edit.addEventListener('click', () => {
      actionArea.replaceChildren(publicationEditor(publication, 'edit', (values, status) => updatePublication(publication.id, values, status)));
    });
    const publish = document.createElement('button');
    publish.type = 'button';
    publish.className = 'button primary small';
    publish.textContent = 'Pubblica';
    publish.addEventListener('click', () => {
      renderInlineConfirmation(actionArea, 'Confermi la pubblicazione? L’aggiornamento diventerà visibile nel portale proprietario.', () => mutatePublication(publication.id, 'publish', actionArea));
    });
    actions.append(edit, publish);
  }

  if (publication && publication.status === 'published') {
    const supersede = document.createElement('button');
    supersede.type = 'button';
    supersede.className = 'button secondary small';
    supersede.textContent = 'Nuova versione';
    supersede.addEventListener('click', () => {
      actionArea.replaceChildren(publicationEditor(publication, 'supersede', (values, status) => supersedePublication(publication, values, status)));
    });
    const archive = document.createElement('button');
    archive.type = 'button';
    archive.className = 'button danger small';
    archive.textContent = 'Archivia';
    archive.addEventListener('click', () => {
      renderInlineConfirmation(actionArea, 'Confermi l’archiviazione di questa pubblicazione?', () => mutatePublication(publication.id, 'archive', actionArea));
    });
    actions.append(supersede, archive);
  }

  card.append(actions, actionArea);
  return card;
}

function renderPublications(items) {
  el.publicationsContent.replaceChildren();
  if (!Array.isArray(items) || items.length === 0) {
    setPublicationsState('empty');
    return;
  }
  for (const publication of items) el.publicationsContent.append(renderPublication(publication || {}));
  setPublicationsState('content');
}

async function loadPublications() {
  if (!state.authenticated || state.activeSection !== 'publications') return;
  const generation = ++state.publicationsGeneration;
  const sessionGeneration = state.sessionGeneration;
  setPublicationsState('loading');
  try {
    const data = await request('/publications');
    if (!isCurrent('publications', generation, sessionGeneration)) return;
    renderPublications(data && Array.isArray(data.items) ? data.items : []);
  } catch (error) {
    if (!isCurrent('publications', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setPublicationsState('error', statusMessage(error));
  }
}

async function submitPublication(event) {
  event.preventDefault();
  if (!state.authenticated || state.mutationsInFlight.has('publication:create')) return;
  const values = {
    propertyId: el.publicationPropertyId.value,
    type: el.publicationType.value,
    title: el.publicationTitle.value.trim(),
    summary: el.publicationSummary.value.trim(),
    body: el.publicationBody.value.trim(),
    acknowledgementRequired: el.publicationAckRequired.checked === true,
  };
  const problem = validatePublicationValues(values, true);
  if (problem) {
    setStatus(el.publicationFormStatus, problem, 'error');
    return;
  }

  state.mutationsInFlight.add('publication:create');
  const sessionGeneration = state.sessionGeneration;
  state.publicationsGeneration += 1;
  el.publicationSubmit.disabled = true;
  setStatus(el.publicationFormStatus, 'Creazione draft in corso…');
  try {
    await request('/publications', {
      method: 'POST',
      json: publicationPayloadFromValues(parsePositiveInt(values.propertyId), values.type, values.title, values.summary, values.body, values.acknowledgementRequired),
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    el.publicationForm.reset();
    el.publicationType.value = 'general_update';
    setStatus(el.publicationFormStatus, 'Draft creato.', 'success');
    if (state.activeSection === 'publications') await loadPublications();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.publicationFormStatus);
  } finally {
    state.mutationsInFlight.delete('publication:create');
    if (sessionGeneration === state.sessionGeneration) el.publicationSubmit.disabled = false;
  }
}

async function updatePublication(publicationId, values, statusNode) {
  const key = `publication:edit:${publicationId}`;
  if (state.mutationsInFlight.has(key) || !state.authenticated) return false;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  const expectedSection = state.activeSection;
  state.publicationsGeneration += 1;
  try {
    await request(`/publications/${encodeURIComponent(publicationId)}`, {
      method: 'PATCH',
      json: {
        publication_type: values.type,
        title: values.title,
        summary: values.summary,
        body: values.body,
        acknowledgement_required: values.acknowledgementRequired,
      },
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return true;
    if (expectedSection === 'publications' && state.activeSection === 'publications') {
      setStatus(el.publicationFormStatus, 'Draft aggiornato.', 'success');
      await loadPublications();
    }
    return true;
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return true;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return true;
    }
    setStatus(statusNode, statusMessage(error), 'error');
    return false;
  } finally {
    state.mutationsInFlight.delete(key);
  }
}

async function mutatePublication(publicationId, action, actionArea) {
  const key = `publication:${action}:${publicationId}`;
  if (state.mutationsInFlight.has(key) || !state.authenticated) return;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  const expectedSection = state.activeSection;
  state.publicationsGeneration += 1;
  setStatus(el.publicationFormStatus, action === 'publish' ? 'Pubblicazione in corso…' : 'Archiviazione in corso…');
  try {
    await request(`/publications/${encodeURIComponent(publicationId)}/${action}`, { method: 'POST' });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    if (expectedSection === 'publications' && state.activeSection === 'publications') {
      setStatus(el.publicationFormStatus, action === 'publish' ? 'Pubblicazione completata.' : 'Pubblicazione archiviata.', 'success');
      await loadPublications();
    }
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.publicationFormStatus);
    if (actionArea) actionArea.replaceChildren();
  } finally {
    state.mutationsInFlight.delete(key);
  }
}

async function supersedePublication(publication, values, statusNode) {
  const publicationId = publication && publication.id;
  const propertyId = parsePositiveInt(publication && publication.property_id);
  const key = `publication:supersede:${publicationId}`;
  if (propertyId === null) {
    setStatus(statusNode, 'ID immobile non disponibile per il versionamento.', 'error');
    return false;
  }
  if (state.mutationsInFlight.has(key) || !state.authenticated) return false;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  const expectedSection = state.activeSection;
  state.publicationsGeneration += 1;
  try {
    await request(`/publications/${encodeURIComponent(publicationId)}/supersede`, {
      method: 'POST',
      json: publicationPayloadFromValues(propertyId, values.type, values.title, values.summary, values.body, values.acknowledgementRequired),
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return true;
    if (expectedSection === 'publications' && state.activeSection === 'publications') {
      setStatus(el.publicationFormStatus, 'Nuova versione draft creata.', 'success');
      await loadPublications();
    }
    return true;
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return true;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return true;
    }
    setStatus(statusNode, statusMessage(error), 'error');
    return false;
  } finally {
    state.mutationsInFlight.delete(key);
  }
}

function setRequestsState(mode, message = '') {
  el.requestsLoading.hidden = mode !== 'loading';
  el.requestsEmpty.hidden = mode !== 'empty';
  el.requestsError.hidden = mode !== 'error';
  el.requestsContent.hidden = mode !== 'content';
  if (mode === 'error') el.requestsErrorMessage.textContent = message;
}

function requestTypeLabel(value) {
  return REQUEST_TYPE_LABELS[value] || 'Tipo richiesta non disponibile';
}

function requestEditor(item) {
  const form = document.createElement('form');
  form.className = 'inline-editor request-editor';
  form.setAttribute('data-request-editor', String(item && item.id !== undefined ? item.id : ''));

  const statusField = document.createElement('div');
  statusField.className = 'field';
  const statusLabel = document.createElement('label');
  statusLabel.textContent = 'Stato richiesta';
  const status = document.createElement('select');
  const requestKey = item && item.id !== undefined ? String(item.id) : 'unknown';
  status.id = `request-editor-status-${requestKey}`;
  statusLabel.setAttribute('for', status.id);
  status.setAttribute('data-field', 'request_status');
  const statuses = [
    ['new', 'Nuova'],
    ['in_review', 'In gestione'],
    ['handled', 'Gestita'],
    ['closed', 'Chiusa'],
  ];
  for (const pair of statuses) {
    const option = document.createElement('option');
    option.value = pair[0];
    option.textContent = pair[1];
    status.append(option);
  }
  status.value = Object.prototype.hasOwnProperty.call(REQUEST_STATUS_LABELS, item && item.status) ? item.status : 'new';
  statusField.append(statusLabel, status);

  const responseField = document.createElement('div');
  responseField.className = 'field field-span-2';
  const responseLabel = document.createElement('label');
  responseLabel.textContent = 'Risposta pubblica';
  const response = document.createElement('textarea');
  response.id = `request-editor-response-${requestKey}`;
  responseLabel.setAttribute('for', response.id);
  response.setAttribute('data-field', 'public_response');
  response.setAttribute('maxlength', '5000');
  response.setAttribute('rows', '5');
  response.value = item && item.public_response ? String(item.public_response) : '';
  responseField.append(responseLabel, response);

  const actions = document.createElement('div');
  actions.className = 'editor-actions';
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'button primary small';
  save.textContent = 'Salva gestione';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'button secondary small';
  cancel.textContent = 'Annulla';
  actions.append(save, cancel);
  const message = document.createElement('p');
  message.className = 'status-message editor-status';
  message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite');
  form.append(statusField, responseField, actions, message);

  let saving = false;
  cancel.addEventListener('click', () => { form.hidden = true; });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving) return;
    if (!Object.prototype.hasOwnProperty.call(REQUEST_STATUS_LABELS, status.value)) {
      setStatus(message, 'Seleziona uno stato richiesta valido.', 'error');
      return;
    }
    if (response.value.length > 5000) {
      setStatus(message, 'La risposta pubblica non può superare 5000 caratteri.', 'error');
      return;
    }
    saving = true;
    save.disabled = true;
    cancel.disabled = true;
    setStatus(message, 'Salvataggio in corso…');
    const ok = await updateRequest(item.id, status.value, response.value, message);
    if (!ok) {
      saving = false;
      save.disabled = false;
      cancel.disabled = false;
    }
  });
  return form;
}

function renderRequest(item) {
  const card = document.createElement('article');
  card.className = 'entity-card request-card';
  const header = document.createElement('div');
  header.className = 'entity-card-header';
  const title = document.createElement('h4');
  title.className = 'entity-title';
  title.textContent = item && item.subject ? String(item.subject) : `Richiesta #${item && item.id !== undefined ? item.id : '—'}`;
  header.append(title, createStatusBadge(item && item.status, REQUEST_STATUS_LABELS));

  const meta = document.createElement('div');
  meta.className = 'entity-meta';
  meta.append(
    createMeta('ID', item && item.id),
    createMeta('Tipo', requestTypeLabel(item && item.feedback_type)),
    createMeta('Inviata', formatDate(item && item.submitted_at)),
    createMeta('Disponibilità da', formatDate(item && item.availability_from)),
    createMeta('Disponibilità a', formatDate(item && item.availability_to)),
    createMeta('Gestita il', formatDate(item && item.handled_at)),
  );

  const message = document.createElement('p');
  message.className = 'entity-copy';
  message.textContent = item && item.message ? String(item.message) : '—';
  card.append(header, meta, message);

  if (item && item.public_response !== null && item.public_response !== undefined && item.public_response !== '') {
    const responseBox = document.createElement('div');
    responseBox.className = 'public-response';
    const label = document.createElement('span');
    label.className = 'meta-label';
    label.textContent = 'Risposta pubblica';
    const text = document.createElement('p');
    text.textContent = String(item.public_response);
    responseBox.append(label, text);
    card.append(responseBox);
  }

  const actions = document.createElement('div');
  actions.className = 'entity-actions';
  const manage = document.createElement('button');
  manage.type = 'button';
  manage.className = 'button secondary small';
  manage.textContent = 'Gestisci richiesta';
  const actionArea = document.createElement('div');
  actionArea.className = 'action-area';
  manage.addEventListener('click', () => actionArea.replaceChildren(requestEditor(item || {})));
  actions.append(manage);
  card.append(actions, actionArea);
  return card;
}

function renderRequests(items) {
  el.requestsContent.replaceChildren();
  if (!Array.isArray(items) || items.length === 0) {
    setRequestsState('empty');
    return;
  }
  for (const item of items) el.requestsContent.append(renderRequest(item || {}));
  setRequestsState('content');
}

async function loadRequests() {
  if (!state.authenticated || state.activeSection !== 'requests') return;
  const generation = ++state.requestsGeneration;
  const sessionGeneration = state.sessionGeneration;
  setRequestsState('loading');
  try {
    const data = await request('/feedback');
    if (!isCurrent('requests', generation, sessionGeneration)) return;
    renderRequests(data && Array.isArray(data.items) ? data.items : []);
  } catch (error) {
    if (!isCurrent('requests', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setRequestsState('error', statusMessage(error));
  }
}

async function updateRequest(requestId, status, publicResponse, statusNode) {
  const key = `request:update:${requestId}`;
  if (state.mutationsInFlight.has(key) || !state.authenticated) return false;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  const expectedSection = state.activeSection;
  state.requestsGeneration += 1;
  try {
    await request(`/feedback/${encodeURIComponent(requestId)}`, {
      method: 'PATCH',
      json: { status, public_response: publicResponse },
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return true;
    if (expectedSection === 'requests' && state.activeSection === 'requests') {
      setStatus(el.globalStatus, 'Richiesta aggiornata.', 'success');
      await loadRequests();
    }
    return true;
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return true;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return true;
    }
    setStatus(statusNode, statusMessage(error), 'error');
    return false;
  } finally {
    state.mutationsInFlight.delete(key);
  }
}

function sectionConfig(name) {
  if (name === 'accounts') return { node: el.accountsSection, nav: el.navAccounts, title: 'Proprietari' };
  if (name === 'access') return { node: el.accessSection, nav: el.navAccess, title: 'Accessi' };
  if (name === 'publications') return { node: el.publicationsSection, nav: el.navPublications, title: 'Pubblicazioni' };
  if (name === 'requests') return { node: el.requestsSection, nav: el.navRequests, title: 'Richieste' };
  return { node: el.dashboardSection, nav: el.navDashboard, title: 'Dashboard' };
}

function activateSection(name, options = {}) {
  if (!state.authenticated) return;
  state.dashboardGeneration += 1;
  state.accountsGeneration += 1;
  state.accessGeneration += 1;
  state.publicationsGeneration += 1;
  state.requestsGeneration += 1;
  state.activeSection = name;

  for (const sectionName of ['dashboard', 'accounts', 'access', 'publications', 'requests']) {
    const config = sectionConfig(sectionName);
    const active = sectionName === name;
    config.node.hidden = !active;
    if (active) config.nav.setAttribute('aria-current', 'page');
    else config.nav.removeAttribute('aria-current');
  }
  el.sectionTitle.textContent = sectionConfig(name).title;
  setStatus(el.globalStatus, '');

  if (name === 'dashboard') {
    if (options.dashboardData) {
      renderDashboard(options.dashboardData);
      return Promise.resolve();
    }
    return loadDashboard();
  }
  if (name === 'accounts') return loadAccounts();
  if (name === 'access') return loadAccess();
  if (name === 'publications') return loadPublications();
  if (name === 'requests') return loadRequests();
  return Promise.resolve();
}

async function login(event) {
  event.preventDefault();
  const username = el.username.value.trim();
  const password = el.password.value;
  if (!username || !password) {
    setStatus(el.loginStatus, 'Inserisci username e password.', 'error');
    return;
  }

  const loginGeneration = ++state.loginGeneration;
  const sessionGeneration = state.sessionGeneration;
  el.loginSubmit.disabled = true;
  setStatus(el.loginStatus, 'Verifica credenziali…');
  const candidate = { username, password };
  try {
    const dashboardData = await request('/dashboard', {}, candidate);
    el.password.value = '';
    if (loginGeneration !== state.loginGeneration || sessionGeneration !== state.sessionGeneration) return;
    state.credentials = candidate;
    state.authenticated = true;
    el.username.value = '';
    el.loginView.hidden = true;
    el.app.hidden = false;
    setStatus(el.loginStatus, '');
    activateSection('dashboard', { dashboardData: dashboardData || {} });
  } catch (error) {
    candidate.username = '';
    candidate.password = '';
    el.password.value = '';
    if (loginGeneration !== state.loginGeneration || sessionGeneration !== state.sessionGeneration) return;
    setStatus(el.loginStatus, statusMessage(error), 'error');
    el.loginSubmit.disabled = false;
    el.password.focus();
  }
}

el.loginForm.addEventListener('submit', login);
el.logout.addEventListener('click', () => logout('Sessione amministrativa chiusa.'));
el.navDashboard.addEventListener('click', () => activateSection('dashboard'));
el.navAccounts.addEventListener('click', () => activateSection('accounts'));
el.navAccess.addEventListener('click', () => activateSection('access'));
el.navPublications.addEventListener('click', () => activateSection('publications'));
el.navRequests.addEventListener('click', () => activateSection('requests'));
el.dashboardRetry.addEventListener('click', loadDashboard);
el.dashboardReload.addEventListener('click', loadDashboard);
el.accountsRetry.addEventListener('click', loadAccounts);
el.accountsReload.addEventListener('click', loadAccounts);
el.accessRetry.addEventListener('click', loadAccess);
el.accessReload.addEventListener('click', loadAccess);
el.publicationsRetry.addEventListener('click', loadPublications);
el.publicationsReload.addEventListener('click', loadPublications);
el.requestsRetry.addEventListener('click', loadRequests);
el.requestsReload.addEventListener('click', loadRequests);
el.accountForm.addEventListener('submit', submitAccount);
el.accessForm.addEventListener('submit', submitAccess);
el.publicationForm.addEventListener('submit', submitPublication);

logout('');
