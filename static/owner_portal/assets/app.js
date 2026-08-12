(() => {
  'use strict';

  const API_BASE = '/api/owner/portal';

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

  const state = {
    session: null,
    busy: false,
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
      return 'Il codice inserito non è valido.';
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

  function enterLoggedOut(message = '') {
    state.session = null;
    state.busy = false;
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

  async function authenticateWithToken(token) {
    setBusy(true, 'Accesso in corso…');
    await exchangeToken(token);
    const session = await loadSession();
    enterAuthenticated(session);
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
    } catch (error) {
      if (error instanceof PortalRequestError && [401, 403, 404].includes(error.status)) {
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
