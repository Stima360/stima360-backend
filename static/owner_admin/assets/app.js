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

const state = {
  credentials: null,
  authenticated: false,
  activeSection: 'dashboard',
  sessionGeneration: 0,
  loginGeneration: 0,
  dashboardGeneration: 0,
  accountsGeneration: 0,
  accessGeneration: 0,
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
  dashboardSection: document.getElementById('section-dashboard'),
  accountsSection: document.getElementById('section-accounts'),
  accessSection: document.getElementById('section-access'),
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
  state.mutationsInFlight.clear();
}

function resetDynamicUi() {
  el.dashboardContent.replaceChildren();
  el.accountsContent.replaceChildren();
  el.accessContent.replaceChildren();
  setStatus(el.globalStatus, '');
  setStatus(el.accountFormStatus, '');
  setStatus(el.accessFormStatus, '');
  el.accountForm.reset();
  el.accountLanguage.value = 'it';
  el.accessForm.reset();
  el.accessRole.value = 'owner';
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

function sectionConfig(name) {
  if (name === 'accounts') return { node: el.accountsSection, nav: el.navAccounts, title: 'Proprietari' };
  if (name === 'access') return { node: el.accessSection, nav: el.navAccess, title: 'Accessi' };
  return { node: el.dashboardSection, nav: el.navDashboard, title: 'Dashboard' };
}

function activateSection(name, options = {}) {
  if (!state.authenticated) return;
  state.dashboardGeneration += 1;
  state.accountsGeneration += 1;
  state.accessGeneration += 1;
  state.activeSection = name;

  for (const sectionName of ['dashboard', 'accounts', 'access']) {
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
el.dashboardRetry.addEventListener('click', loadDashboard);
el.dashboardReload.addEventListener('click', loadDashboard);
el.accountsRetry.addEventListener('click', loadAccounts);
el.accountsReload.addEventListener('click', loadAccounts);
el.accessRetry.addEventListener('click', loadAccess);
el.accessReload.addEventListener('click', loadAccess);
el.accountForm.addEventListener('submit', submitAccount);
el.accessForm.addEventListener('submit', submitAccess);

logout('');
