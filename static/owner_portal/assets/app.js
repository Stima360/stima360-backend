(() => {
  'use strict';

  const API_BASE = '/api/owner/portal';
  const AUTH_LOSS_STATUSES = new Set([401, 403, 404]);

  const views = {
    loading: document.getElementById('loading-view'),
    login: document.getElementById('login-view'),
    app: document.getElementById('app-view'),
  };

  const loadingMessage = document.getElementById('loading-message');
  const loginForm = document.getElementById('login-form');
  const tokenInput = document.getElementById('token-input');
  const loginButton = document.getElementById('login-button');
  const authMessage = document.getElementById('auth-message');
  const appMessage = document.getElementById('app-message');
  const logoutButton = document.getElementById('logout-button');

  const propertyCount = document.getElementById('property-count');
  const dashboardLoading = document.getElementById('dashboard-loading');
  const dashboardEmpty = document.getElementById('shell-empty');
  const dashboardError = document.getElementById('dashboard-error');
  const dashboardErrorMessage = document.getElementById('dashboard-error-message');
  const dashboardRetry = document.getElementById('dashboard-retry');
  const dashboardContent = document.getElementById('dashboard-content');
  const propertyList = document.getElementById('property-list');

  const propertyDetailLoading = document.getElementById('property-detail-loading');
  const propertyDetailEmpty = document.getElementById('property-detail-empty');
  const propertyDetailError = document.getElementById('property-detail-error');
  const propertyDetailErrorMessage = document.getElementById('property-detail-error-message');
  const propertyDetailRetry = document.getElementById('property-detail-retry');
  const propertyDetailContent = document.getElementById('property-detail-content');
  const propertyDetailTitle = document.getElementById('property-detail-title');
  const propertySummary = document.getElementById('property-summary');

  const state = {
    session: null,
    busy: false,
    properties: [],
    selectedPropertyId: null,
    dashboardGeneration: 0,
    propertyGeneration: 0,
  };

  class PortalRequestError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.name = 'PortalRequestError';
      this.status = status;
    }
  }

  function showView(name) {
    Object.entries(views).forEach(([key, element]) => {
      element.hidden = key !== name;
    });
  }

  function setBusy(busy, message = 'Operazione in corso…') {
    state.busy = busy;
    loginButton.disabled = busy;
    logoutButton.disabled = busy;
    if (busy) {
      loadingMessage.textContent = message;
      showView('loading');
    }
  }

  function clearMessages() {
    authMessage.textContent = '';
    appMessage.textContent = '';
    authMessage.classList.remove('is-error');
    appMessage.classList.remove('is-error');
  }

  function setAuthMessage(message, isError = false) {
    authMessage.textContent = message;
    authMessage.classList.toggle('is-error', isError);
  }

  function setAppMessage(message, isError = false) {
    appMessage.textContent = message;
    appMessage.classList.toggle('is-error', isError);
  }

  function messageForStatus(status) {
    if (status === 401 || status === 403 || status === 404) {
      return 'Sessione non disponibile o scaduta.';
    }
    if (status === 422) {
      return 'I dati inviati non sono validi.';
    }
    if (status === 429) {
      return 'Troppe richieste. Riprova tra poco.';
    }
    if (status >= 500) {
      return 'Servizio temporaneamente non disponibile.';
    }
    return 'Operazione non riuscita.';
  }

  async function apiRequest(path, options = {}) {
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        credentials: 'include',
        cache: 'no-store',
        ...options,
      });
    } catch (_error) {
      throw new PortalRequestError('Connessione non disponibile. Controlla la rete e riprova.');
    }

    if (!response.ok) {
      throw new PortalRequestError(messageForStatus(response.status), response.status);
    }

    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  function readTokenFromUrl() {
    return new URL(window.location.href).searchParams.get('token');
  }

  function removeTokenFromUrl() {
    const cleanUrl = new URL(window.location.href);
    cleanUrl.searchParams.delete('token');
    const query = cleanUrl.searchParams.toString();
    const safeLocation = `${cleanUrl.pathname}${query ? `?${query}` : ''}${cleanUrl.hash}`;
    window.history.replaceState({}, '', safeLocation);
  }

  function resetPropertyDetail() {
    state.propertyGeneration += 1;
    propertyDetailLoading.hidden = true;
    propertyDetailEmpty.hidden = true;
    propertyDetailError.hidden = true;
    propertyDetailContent.hidden = true;
    propertyDetailErrorMessage.textContent = '';
    propertyDetailTitle.textContent = 'Immobile';
    propertySummary.replaceChildren();
  }

  function resetDashboardState() {
    state.dashboardGeneration += 1;
    state.propertyGeneration += 1;
    state.properties = [];
    state.selectedPropertyId = null;
    propertyCount.textContent = '';
    propertyList.replaceChildren();
    dashboardLoading.hidden = true;
    dashboardEmpty.hidden = true;
    dashboardError.hidden = true;
    dashboardContent.hidden = true;
    dashboardErrorMessage.textContent = '';
    resetPropertyDetail();
  }

  function enterLoggedOut(message = '') {
    state.session = null;
    state.busy = false;
    resetDashboardState();
    tokenInput.value = '';
    loginButton.disabled = false;
    logoutButton.disabled = false;
    clearMessages();
    if (message) {
      setAuthMessage(message, true);
    }
    showView('login');
    tokenInput.focus();
  }

  function enterAuthenticated(session) {
    state.session = session;
    state.busy = false;
    loginButton.disabled = false;
    logoutButton.disabled = false;
    clearMessages();
    showView('app');
  }

  async function loadSession() {
    const session = await apiRequest('/session');
    if (!session || session.authenticated !== true) {
      throw new PortalRequestError('Sessione non disponibile o scaduta.', 404);
    }
    return session;
  }

  async function exchangeToken(token) {
    await apiRequest('/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
  }

  function isAuthLoss(error) {
    return error instanceof PortalRequestError && AUTH_LOSS_STATUSES.has(error.status);
  }

  function dashboardErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare gli immobili. Riprova tra poco.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare gli immobili con i dati disponibili.';
    }
    return error.message;
  }

  function propertyErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare il riepilogo. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Immobile non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare i dati dell’immobile.';
    }
    return error.message;
  }

  function showDashboardState(name, message = '') {
    dashboardLoading.hidden = name !== 'loading';
    dashboardEmpty.hidden = name !== 'empty';
    dashboardError.hidden = name !== 'error';
    dashboardContent.hidden = name !== 'content';
    if (name === 'error') {
      dashboardErrorMessage.textContent = message;
    }
  }

  function showPropertyState(name, message = '') {
    propertyDetailLoading.hidden = name !== 'loading';
    propertyDetailEmpty.hidden = name !== 'empty';
    propertyDetailError.hidden = name !== 'error';
    propertyDetailContent.hidden = name !== 'content';
    if (name === 'error') {
      propertyDetailErrorMessage.textContent = message;
    }
  }

  function textOrEmpty(value) {
    return typeof value === 'string' ? value.trim() : '';
  }

  function propertyId(item) {
    const value = item && item.id;
    if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
      return value;
    }
    if (typeof value === 'string' && /^\d+$/.test(value) && Number(value) > 0) {
      return Number(value);
    }
    return null;
  }

  function roleLabel(role) {
    const labels = {
      owner: 'Proprietario',
      co_owner: 'Comproprietario',
      delegate: 'Delegato',
      legal_representative: 'Rappresentante legale',
    };
    return labels[role] || '';
  }

  function locationLabel(item) {
    const address = textOrEmpty(item && item.address);
    const city = textOrEmpty(item && item.city);
    if (address && city) {
      return `${address} · ${city}`;
    }
    return address || city;
  }

  function createTextElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    element.textContent = text;
    return element;
  }

  function setSelectedCardState() {
    Array.from(propertyList.children).forEach((listItem) => {
      const button = listItem.children[0];
      if (!button) {
        return;
      }
      const selected = Number(button.dataset.propertyId) === state.selectedPropertyId;
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
      button.classList.toggle('is-selected', selected);
    });
  }

  function createPropertyCard(item) {
    const id = propertyId(item);
    const listItem = document.createElement('div');
    listItem.className = 'property-list-item';
    listItem.setAttribute('role', 'listitem');

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'property-card';
    button.dataset.propertyId = String(id);
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-controls', 'property-detail-section');

    const topRow = document.createElement('span');
    topRow.className = 'property-card-topline';
    const title = textOrEmpty(item.title) || 'Immobile';
    topRow.append(createTextElement('span', 'property-card-title', title));
    if (item.is_primary === true) {
      topRow.append(createTextElement('span', 'primary-badge', 'Principale'));
    }
    button.append(topRow);

    const location = locationLabel(item);
    if (location) {
      button.append(createTextElement('span', 'property-card-meta', location));
    }

    const access = roleLabel(item.access_role);
    if (access) {
      button.append(createTextElement('span', 'property-card-role', access));
    }

    button.addEventListener('click', () => {
      if (state.session && id !== null) {
        void selectProperty(id);
      }
    });

    listItem.append(button);
    return listItem;
  }

  function renderPropertyList(items) {
    propertyList.replaceChildren();
    items.forEach((item) => {
      propertyList.append(createPropertyCard(item));
    });
    setSelectedCardState();
  }

  function addSummaryRow(label, value) {
    const cleanValue = textOrEmpty(value);
    if (!cleanValue) {
      return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'summary-row';
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = cleanValue;
    wrapper.append(term, description);
    propertySummary.append(wrapper);
  }

  function selectedDashboardProperty() {
    return state.properties.find((item) => propertyId(item) === state.selectedPropertyId) || null;
  }

  function renderPropertyDetail(payload) {
    const property = payload && payload.property && typeof payload.property === 'object'
      ? payload.property
      : {};
    const dashboardItem = selectedDashboardProperty() || {};

    const title = textOrEmpty(property.title) || textOrEmpty(dashboardItem.title) || 'Immobile';
    const address = textOrEmpty(property.address) || textOrEmpty(dashboardItem.address);
    const city = textOrEmpty(property.city) || textOrEmpty(dashboardItem.city);
    const role = roleLabel(property.access_role || dashboardItem.access_role);
    const primaryValue = typeof property.is_primary === 'boolean'
      ? property.is_primary
      : (typeof dashboardItem.is_primary === 'boolean' ? dashboardItem.is_primary : null);

    propertyDetailTitle.textContent = title;
    propertySummary.replaceChildren();
    addSummaryRow('Indirizzo', address);
    addSummaryRow('Città', city);
    addSummaryRow('Accesso', role);
    if (primaryValue !== null) {
      addSummaryRow('Immobile principale', primaryValue ? 'Sì' : 'No');
    }
    showPropertyState('content');
  }

  async function confirmSessionAfterPropertyNotFound(generation) {
    try {
      const session = await loadSession();
      if (generation !== state.propertyGeneration || !state.session) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (generation !== state.propertyGeneration) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      showPropertyState('error', propertyErrorText(error));
      return false;
    }
  }

  async function selectProperty(id) {
    if (!state.session) {
      return;
    }

    const available = state.properties.some((item) => propertyId(item) === id);
    if (!available) {
      return;
    }

    state.selectedPropertyId = id;
    setSelectedCardState();
    const generation = ++state.propertyGeneration;
    propertySummary.replaceChildren();
    showPropertyState('loading');

    try {
      const payload = await apiRequest(`/properties/${encodeURIComponent(String(id))}`);
      if (generation !== state.propertyGeneration || !state.session) {
        return;
      }
      renderPropertyDetail(payload);
    } catch (error) {
      if (generation !== state.propertyGeneration || !state.session) {
        return;
      }

      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }

      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterPropertyNotFound(generation);
        if (sessionValid && generation === state.propertyGeneration && state.session) {
          showPropertyState('error', 'Immobile non disponibile o accesso non più valido.');
        }
        return;
      }

      showPropertyState('error', propertyErrorText(error));
    }
  }

  function preferredPropertyId(items) {
    const primary = items.find((item) => item && item.is_primary === true && propertyId(item) !== null);
    if (primary) {
      return propertyId(primary);
    }
    return items.length ? propertyId(items[0]) : null;
  }

  async function loadDashboard() {
    if (!state.session) {
      return;
    }

    const generation = ++state.dashboardGeneration;
    state.propertyGeneration += 1;
    state.properties = [];
    state.selectedPropertyId = null;
    propertyCount.textContent = '';
    propertyList.replaceChildren();
    resetPropertyDetail();
    showDashboardState('loading');

    let payload;
    try {
      payload = await apiRequest('/dashboard');
    } catch (error) {
      if (generation !== state.dashboardGeneration || !state.session) {
        return;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      showDashboardState('error', dashboardErrorText(error));
      return;
    }

    if (generation !== state.dashboardGeneration || !state.session) {
      return;
    }

    const rawProperties = payload && Array.isArray(payload.properties) ? payload.properties : [];
    state.properties = rawProperties.filter((item) => propertyId(item) !== null);
    const apiCount = payload && Number.isInteger(payload.property_count) && payload.property_count >= 0
      ? payload.property_count
      : state.properties.length;
    propertyCount.textContent = `${apiCount} ${apiCount === 1 ? 'immobile' : 'immobili'}`;

    if (state.properties.length === 0) {
      showDashboardState('empty');
      return;
    }

    renderPropertyList(state.properties);
    showDashboardState('content');

    const initialId = preferredPropertyId(state.properties);
    if (initialId !== null) {
      await selectProperty(initialId);
    } else {
      showPropertyState('empty');
    }
  }

  async function authenticateWithToken(token) {
    setBusy(true, 'Accesso in corso…');
    await exchangeToken(token);
    const session = await loadSession();
    enterAuthenticated(session);
    await loadDashboard();
  }

  async function bootstrap() {
    clearMessages();
    setBusy(true, 'Verifica della sessione in corso…');

    const urlToken = readTokenFromUrl();
    if (urlToken !== null) {
      removeTokenFromUrl();
      const token = urlToken.trim();
      if (!token) {
        enterLoggedOut('Il codice nel link non è valido.');
        return;
      }
      try {
        await authenticateWithToken(token);
      } catch (error) {
        enterLoggedOut(error instanceof PortalRequestError ? error.message : 'Accesso non riuscito.');
      }
      return;
    }

    try {
      const session = await loadSession();
      enterAuthenticated(session);
      await loadDashboard();
    } catch (error) {
      if (isAuthLoss(error)) {
        enterLoggedOut();
        return;
      }
      enterLoggedOut(error instanceof PortalRequestError ? error.message : 'Impossibile verificare la sessione.');
    }
  }

  loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.busy) {
      return;
    }

    clearMessages();
    const token = tokenInput.value.trim();
    if (!token) {
      setAuthMessage('Inserisci il codice monouso.', true);
      tokenInput.focus();
      return;
    }

    try {
      await authenticateWithToken(token);
      tokenInput.value = '';
    } catch (error) {
      enterLoggedOut(error instanceof PortalRequestError ? error.message : 'Accesso non riuscito.');
    }
  });

  dashboardRetry.addEventListener('click', () => {
    if (state.session) {
      void loadDashboard();
    }
  });

  propertyDetailRetry.addEventListener('click', () => {
    if (state.session && state.selectedPropertyId !== null) {
      void selectProperty(state.selectedPropertyId);
    }
  });

  logoutButton.addEventListener('click', async () => {
    if (state.busy) {
      return;
    }

    clearMessages();
    setBusy(true, 'Chiusura della sessione…');
    try {
      await apiRequest('/auth/logout', { method: 'POST' });
      enterLoggedOut();
      setAuthMessage('Sessione terminata.');
    } catch (error) {
      state.busy = false;
      loginButton.disabled = false;
      logoutButton.disabled = false;
      showView('app');
      setAppMessage(
        error instanceof PortalRequestError ? error.message : 'Non è stato possibile terminare la sessione.',
        true,
      );
    }
  });

  bootstrap();
})();
