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

  const timelineLoading = document.getElementById('timeline-loading');
  const timelineEmpty = document.getElementById('timeline-empty');
  const timelineError = document.getElementById('timeline-error');
  const timelineErrorMessage = document.getElementById('timeline-error-message');
  const timelineRetry = document.getElementById('timeline-retry');
  const timelineContent = document.getElementById('timeline-content');
  const timelineList = document.getElementById('timeline-list');

  const publicationDetailLoading = document.getElementById('publication-detail-loading');
  const publicationDetailEmpty = document.getElementById('publication-detail-empty');
  const publicationDetailError = document.getElementById('publication-detail-error');
  const publicationDetailErrorMessage = document.getElementById('publication-detail-error-message');
  const publicationDetailRetry = document.getElementById('publication-detail-retry');
  const publicationDetailContent = document.getElementById('publication-detail-content');
  const publicationDetailTitle = document.getElementById('publication-detail-title');
  const publicationDetailMeta = document.getElementById('publication-detail-meta');
  const publicationDetailSummary = document.getElementById('publication-detail-summary');
  const publicationDetailBody = document.getElementById('publication-detail-body');
  const acknowledgeStatus = document.getElementById('acknowledge-status');
  const acknowledgeButton = document.getElementById('acknowledge-button');

  const visitFeedbackLoading = document.getElementById('visit-feedback-loading');
  const visitFeedbackEmpty = document.getElementById('visit-feedback-empty');
  const visitFeedbackError = document.getElementById('visit-feedback-error');
  const visitFeedbackErrorMessage = document.getElementById('visit-feedback-error-message');
  const visitFeedbackRetry = document.getElementById('visit-feedback-retry');
  const visitFeedbackContent = document.getElementById('visit-feedback-content');
  const visitFeedbackList = document.getElementById('visit-feedback-list');
  const visitFeedbackPagination = document.getElementById('visit-feedback-pagination');
  const visitFeedbackLoadMore = document.getElementById('visit-feedback-load-more');
  const visitFeedbackPaginationStatus = document.getElementById('visit-feedback-pagination-status');
  const visitFeedbackDetailLoading = document.getElementById('visit-feedback-detail-loading');
  const visitFeedbackDetailEmpty = document.getElementById('visit-feedback-detail-empty');
  const visitFeedbackDetailError = document.getElementById('visit-feedback-detail-error');
  const visitFeedbackDetailErrorMessage = document.getElementById('visit-feedback-detail-error-message');
  const visitFeedbackDetailRetry = document.getElementById('visit-feedback-detail-retry');
  const visitFeedbackDetailContent = document.getElementById('visit-feedback-detail-content');
  const visitFeedbackDetailTitle = document.getElementById('visit-feedback-detail-title');
  const visitFeedbackDetailMeta = document.getElementById('visit-feedback-detail-meta');
  const visitFeedbackDetailSummary = document.getElementById('visit-feedback-detail-summary');

  const documentsLoading = document.getElementById('documents-loading');
  const documentsEmpty = document.getElementById('documents-empty');
  const documentsError = document.getElementById('documents-error');
  const documentsErrorMessage = document.getElementById('documents-error-message');
  const documentsRetry = document.getElementById('documents-retry');
  const documentsContent = document.getElementById('documents-content');
  const documentsList = document.getElementById('documents-list');
  const documentDetailLoading = document.getElementById('document-detail-loading');
  const documentDetailEmpty = document.getElementById('document-detail-empty');
  const documentDetailError = document.getElementById('document-detail-error');
  const documentDetailErrorMessage = document.getElementById('document-detail-error-message');
  const documentDetailRetry = document.getElementById('document-detail-retry');
  const documentDetailContent = document.getElementById('document-detail-content');
  const documentDetailTitle = document.getElementById('document-detail-title');
  const documentDetailMeta = document.getElementById('document-detail-meta');
  const documentDownloadStatus = document.getElementById('document-download-status');
  const documentDownloadLink = document.getElementById('document-download-link');
  const documentAcknowledgeStatus = document.getElementById('document-acknowledge-status');
  const documentAcknowledgeButton = document.getElementById('document-acknowledge-button');

  const requestForm = document.getElementById('request-form');
  const requestType = document.getElementById('request-type');
  const requestSubject = document.getElementById('request-subject');
  const requestMessage = document.getElementById('request-message');
  const requestAvailabilityFields = document.getElementById('request-availability-fields');
  const requestAvailabilityFrom = document.getElementById('request-availability-from');
  const requestAvailabilityTo = document.getElementById('request-availability-to');
  const requestSubmit = document.getElementById('request-submit');
  const requestFormStatus = document.getElementById('request-form-status');
  const requestsLoading = document.getElementById('requests-loading');
  const requestsEmpty = document.getElementById('requests-empty');
  const requestsError = document.getElementById('requests-error');
  const requestsErrorMessage = document.getElementById('requests-error-message');
  const requestsRetry = document.getElementById('requests-retry');
  const requestsContent = document.getElementById('requests-content');
  const requestsList = document.getElementById('requests-list');

  const REQUEST_TYPE_LABELS = {
    contact_request: 'Essere ricontattato',
    correction_request: 'Segnalare una correzione',
    general_message: 'Messaggio generale',
    strategy_feedback: 'Confronto sulla strategia',
    price_review: 'Revisione del prezzo',
    availability_update: 'Aggiornare la disponibilità',
    document_question: 'Domanda sui documenti',
  };
  const REQUEST_STATUS_LABELS = {
    new: 'Inviata',
    in_review: 'In lavorazione',
    handled: 'Gestita',
    closed: 'Chiusa',
  };
  const REQUEST_SUBJECT_MAX = 150;
  const REQUEST_MESSAGE_MAX = 5000;

  const state = {
    session: null,
    busy: false,
    properties: [],
    selectedPropertyId: null,
    dashboardGeneration: 0,
    propertyGeneration: 0,
    timelineItems: [],
    timelineGeneration: 0,
    selectedPublicationId: null,
    publicationGeneration: 0,
    selectedPublicationRequiresAck: false,
    acknowledgedPublicationIds: new Set(),
    acknowledgeInFlight: new Set(),
    visitFeedbackItems: [],
    visitFeedbackGeneration: 0,
    visitFeedbackOffset: 0,
    visitFeedbackHasMore: false,
    visitFeedbackLoadInFlight: false,
    selectedVisitFeedbackId: null,
    visitFeedbackDetailGeneration: 0,
    documentItems: [],
    documentGeneration: 0,
    selectedDocumentId: null,
    documentDetailGeneration: 0,
    documentAcknowledgeInFlight: new Set(),
    requestItems: [],
    requestGeneration: 0,
    requestSubmitInFlight: false,
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

  function clearPublicationContent() {
    publicationDetailTitle.textContent = 'Aggiornamento';
    publicationDetailMeta.replaceChildren();
    publicationDetailSummary.textContent = '';
    publicationDetailSummary.hidden = true;
    publicationDetailBody.textContent = '';
    acknowledgeStatus.textContent = '';
    acknowledgeStatus.classList.remove('is-error');
    acknowledgeButton.hidden = true;
    acknowledgeButton.disabled = false;
    acknowledgeButton.textContent = 'Conferma presa visione';
    acknowledgeButton.dataset.publicationId = '';
    state.selectedPublicationRequiresAck = false;
  }

  function resetPublicationDetail() {
    state.publicationGeneration += 1;
    state.selectedPublicationId = null;
    publicationDetailLoading.hidden = true;
    publicationDetailEmpty.hidden = false;
    publicationDetailError.hidden = true;
    publicationDetailContent.hidden = true;
    publicationDetailErrorMessage.textContent = '';
    clearPublicationContent();
    setSelectedPublicationCardState();
  }

  function resetTimelineState() {
    state.timelineGeneration += 1;
    state.timelineItems = [];
    timelineList.replaceChildren();
    timelineLoading.hidden = true;
    timelineEmpty.hidden = true;
    timelineError.hidden = true;
    timelineContent.hidden = true;
    timelineErrorMessage.textContent = '';
    resetPublicationDetail();
  }

  function clearVisitFeedbackDetailContent() {
    visitFeedbackDetailTitle.textContent = 'Feedback anonimizzato';
    visitFeedbackDetailMeta.replaceChildren();
    visitFeedbackDetailSummary.textContent = '';
  }

  function resetVisitFeedbackDetail() {
    state.visitFeedbackDetailGeneration += 1;
    state.selectedVisitFeedbackId = null;
    visitFeedbackDetailLoading.hidden = true;
    visitFeedbackDetailEmpty.hidden = false;
    visitFeedbackDetailError.hidden = true;
    visitFeedbackDetailContent.hidden = true;
    visitFeedbackDetailErrorMessage.textContent = '';
    clearVisitFeedbackDetailContent();
    setSelectedVisitFeedbackCardState();
  }

  function resetVisitFeedbackState() {
    state.visitFeedbackGeneration += 1;
    state.visitFeedbackItems = [];
    state.visitFeedbackOffset = 0;
    state.visitFeedbackHasMore = false;
    state.visitFeedbackLoadInFlight = false;
    visitFeedbackList.replaceChildren();
    visitFeedbackLoading.hidden = true;
    visitFeedbackEmpty.hidden = true;
    visitFeedbackError.hidden = true;
    visitFeedbackContent.hidden = true;
    visitFeedbackErrorMessage.textContent = '';
    visitFeedbackPagination.hidden = true;
    visitFeedbackLoadMore.disabled = false;
    visitFeedbackLoadMore.textContent = 'Carica altri';
    visitFeedbackPaginationStatus.textContent = '';
    resetVisitFeedbackDetail();
  }

  function clearDocumentDetailContent() {
    documentDetailTitle.textContent = 'Documento';
    documentDetailMeta.replaceChildren();
    documentDownloadStatus.textContent = '';
    documentDownloadStatus.classList.remove('is-error');
    documentDownloadLink.hidden = true;
    documentDownloadLink.setAttribute('href', '#');
    documentDownloadLink.removeAttribute('download');
    documentAcknowledgeStatus.textContent = '';
    documentAcknowledgeStatus.classList.remove('is-error');
    documentAcknowledgeButton.hidden = true;
    documentAcknowledgeButton.disabled = false;
    documentAcknowledgeButton.textContent = 'Conferma presa visione';
    documentAcknowledgeButton.dataset.documentId = '';
  }

  function resetDocumentDetail() {
    state.documentDetailGeneration += 1;
    state.selectedDocumentId = null;
    documentDetailLoading.hidden = true;
    documentDetailEmpty.hidden = false;
    documentDetailError.hidden = true;
    documentDetailContent.hidden = true;
    documentDetailErrorMessage.textContent = '';
    clearDocumentDetailContent();
    setSelectedDocumentCardState();
  }

  function resetDocumentsState() {
    state.documentGeneration += 1;
    state.documentItems = [];
    documentsList.replaceChildren();
    documentsLoading.hidden = true;
    documentsEmpty.hidden = true;
    documentsError.hidden = true;
    documentsContent.hidden = true;
    documentsErrorMessage.textContent = '';
    state.documentAcknowledgeInFlight.clear();
    resetDocumentDetail();
  }

  function setRequestAvailabilityVisibility() {
    const visible = requestType.value === 'availability_update';
    requestAvailabilityFields.hidden = !visible;
    if (!visible) {
      requestAvailabilityFrom.value = '';
      requestAvailabilityTo.value = '';
    }
  }

  function clearRequestFormStatus() {
    requestFormStatus.textContent = '';
    requestFormStatus.classList.remove('is-error');
  }

  function resetRequestForm() {
    requestType.value = '';
    requestSubject.value = '';
    requestMessage.value = '';
    requestAvailabilityFrom.value = '';
    requestAvailabilityTo.value = '';
    requestSubmit.disabled = false;
    requestSubmit.textContent = 'Invia richiesta';
    setRequestAvailabilityVisibility();
  }

  function resetRequestsState() {
    state.requestGeneration += 1;
    state.requestItems = [];
    state.requestSubmitInFlight = false;
    requestsList.replaceChildren();
    requestsLoading.hidden = true;
    requestsEmpty.hidden = true;
    requestsError.hidden = true;
    requestsContent.hidden = true;
    requestsErrorMessage.textContent = '';
    resetRequestForm();
    clearRequestFormStatus();
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
    resetTimelineState();
    resetVisitFeedbackState();
    resetDocumentsState();
    resetRequestsState();
  }

  function resetDashboardState() {
    state.dashboardGeneration += 1;
    state.propertyGeneration += 1;
    state.properties = [];
    state.selectedPropertyId = null;
    state.acknowledgedPublicationIds.clear();
    state.acknowledgeInFlight.clear();
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

  function timelineErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare gli aggiornamenti. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare gli aggiornamenti con i dati disponibili.';
    }
    return error.message;
  }

  function publicationErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare l’aggiornamento. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare il contenuto dell’aggiornamento.';
    }
    return error.message;
  }

  function visitFeedbackErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare i feedback. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare i feedback con i dati disponibili.';
    }
    return error.message;
  }

  function visitFeedbackDetailErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare il feedback. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare il contenuto del feedback.';
    }
    return error.message;
  }

  function documentsErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare i documenti. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare i documenti con i dati disponibili.';
    }
    return error.message;
  }

  function documentDetailErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare il documento. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Documento non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare il contenuto del documento.';
    }
    return error.message;
  }

  function requestsErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare le richieste. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare le richieste con i dati disponibili.';
    }
    return error.message;
  }

  function requestSubmitErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Invio non riuscito. Controlla la connessione e riprova.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Controlla i campi della richiesta e riprova.';
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

  function showTimelineState(name, message = '') {
    timelineLoading.hidden = name !== 'loading';
    timelineEmpty.hidden = name !== 'empty';
    timelineError.hidden = name !== 'error';
    timelineContent.hidden = name !== 'content';
    if (name === 'error') {
      timelineErrorMessage.textContent = message;
    }
  }

  function showPublicationState(name, message = '') {
    publicationDetailLoading.hidden = name !== 'loading';
    publicationDetailEmpty.hidden = name !== 'empty';
    publicationDetailError.hidden = name !== 'error';
    publicationDetailContent.hidden = name !== 'content';
    if (name === 'error') {
      publicationDetailErrorMessage.textContent = message;
    }
  }

  function showVisitFeedbackState(name, message = '') {
    visitFeedbackLoading.hidden = name !== 'loading';
    visitFeedbackEmpty.hidden = name !== 'empty';
    visitFeedbackError.hidden = name !== 'error';
    visitFeedbackContent.hidden = name !== 'content';
    if (name === 'error') {
      visitFeedbackErrorMessage.textContent = message;
    }
  }

  function showVisitFeedbackDetailState(name, message = '') {
    visitFeedbackDetailLoading.hidden = name !== 'loading';
    visitFeedbackDetailEmpty.hidden = name !== 'empty';
    visitFeedbackDetailError.hidden = name !== 'error';
    visitFeedbackDetailContent.hidden = name !== 'content';
    if (name === 'error') {
      visitFeedbackDetailErrorMessage.textContent = message;
    }
  }

  function showDocumentsState(name, message = '') {
    documentsLoading.hidden = name !== 'loading';
    documentsEmpty.hidden = name !== 'empty';
    documentsError.hidden = name !== 'error';
    documentsContent.hidden = name !== 'content';
    if (name === 'error') {
      documentsErrorMessage.textContent = message;
    }
  }

  function showDocumentDetailState(name, message = '') {
    documentDetailLoading.hidden = name !== 'loading';
    documentDetailEmpty.hidden = name !== 'empty';
    documentDetailError.hidden = name !== 'error';
    documentDetailContent.hidden = name !== 'content';
    if (name === 'error') {
      documentDetailErrorMessage.textContent = message;
    }
  }

  function showRequestsState(name, message = '') {
    requestsLoading.hidden = name !== 'loading';
    requestsEmpty.hidden = name !== 'empty';
    requestsError.hidden = name !== 'error';
    requestsContent.hidden = name !== 'content';
    if (name === 'error') {
      requestsErrorMessage.textContent = message;
    }
  }

  function textOrEmpty(value) {
    return typeof value === 'string' ? value.trim() : '';
  }

  function positiveId(value) {
    if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
      return value;
    }
    if (typeof value === 'string' && /^\d+$/.test(value) && Number(value) > 0) {
      return Number(value);
    }
    return null;
  }

  function propertyId(item) {
    return positiveId(item && item.id);
  }

  function publicationId(item) {
    return positiveId(item && item.id);
  }

  function visitFeedbackId(item) {
    return positiveId(item && item.visit_feedback_publication_id);
  }

  function documentId(item) {
    return positiveId(item && item.id);
  }

  function requestTypeLabel(type) {
    return REQUEST_TYPE_LABELS[type] || 'Richiesta';
  }

  function requestStatusLabel(status) {
    return REQUEST_STATUS_LABELS[status] || 'Stato non disponibile';
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

  function publicationTypeLabel(type) {
    const labels = {
      general_update: 'Aggiornamento generale',
      marketing_update: 'Marketing',
      visit_update: 'Aggiornamento visite',
      feedback_summary: 'Sintesi feedback',
      strategy_update: 'Strategia',
      milestone: 'Traguardo',
    };
    return labels[type] || 'Aggiornamento';
  }

  function locationLabel(item) {
    const address = textOrEmpty(item && item.address);
    const city = textOrEmpty(item && item.city);
    if (address && city) {
      return `${address} · ${city}`;
    }
    return address || city;
  }

  function formatPublishedAt(value) {
    const raw = textOrEmpty(value);
    if (!raw) {
      return '';
    }
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) {
      return raw;
    }
    return new Intl.DateTimeFormat('it-IT', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(parsed);
  }

  function formatFileSize(value) {
    if (!Number.isInteger(value) || value < 0) {
      return '';
    }
    if (value < 1024) {
      return `${value} B`;
    }
    const units = ['KB', 'MB', 'GB'];
    let size = value / 1024;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    const digits = size >= 10 ? 0 : 1;
    return `${size.toFixed(digits)} ${units[unitIndex]}`;
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

  function setSelectedPublicationCardState() {
    Array.from(timelineList.children).forEach((listItem) => {
      const button = listItem.children[0];
      if (!button) {
        return;
      }
      const selected = Number(button.dataset.publicationId) === state.selectedPublicationId;
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
      button.classList.toggle('is-selected', selected);
    });
  }

  function setSelectedVisitFeedbackCardState() {
    Array.from(visitFeedbackList.children).forEach((listItem) => {
      const button = listItem.children[0];
      if (!button) {
        return;
      }
      const selected = Number(button.dataset.visitFeedbackId) === state.selectedVisitFeedbackId;
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
      button.classList.toggle('is-selected', selected);
    });
  }

  function setSelectedDocumentCardState() {
    Array.from(documentsList.children).forEach((listItem) => {
      const button = listItem.children[0];
      if (!button) {
        return;
      }
      const selected = Number(button.dataset.documentId) === state.selectedDocumentId;
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

  function createTimelineCard(item) {
    const id = publicationId(item);
    const listItem = document.createElement('div');
    listItem.className = 'timeline-list-item';
    listItem.setAttribute('role', 'listitem');

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'timeline-card';
    button.dataset.publicationId = String(id);
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-controls', 'publication-detail-section');

    const title = textOrEmpty(item.title) || 'Aggiornamento';
    button.append(createTextElement('span', 'timeline-card-title', title));

    const meta = document.createElement('span');
    meta.className = 'timeline-card-meta';
    meta.append(createTextElement('span', '', publicationTypeLabel(item.publication_type)));
    const publishedAt = formatPublishedAt(item.published_at);
    if (publishedAt) {
      meta.append(createTextElement('span', '', publishedAt));
    }
    button.append(meta);

    const summary = textOrEmpty(item.summary);
    if (summary) {
      button.append(createTextElement('span', 'timeline-card-summary', summary));
    }

    if (item.acknowledgement_required === true) {
      button.append(createTextElement('span', 'timeline-ack-badge', 'Presa visione richiesta'));
    }

    button.addEventListener('click', () => {
      if (state.session && state.selectedPropertyId !== null && id !== null) {
        void openPublication(id);
      }
    });

    listItem.append(button);
    return listItem;
  }

  function renderTimelineList(items) {
    timelineList.replaceChildren();
    items.forEach((item) => {
      timelineList.append(createTimelineCard(item));
    });
    setSelectedPublicationCardState();
  }

  function createVisitFeedbackCard(item) {
    const id = visitFeedbackId(item);
    const listItem = document.createElement('div');
    listItem.className = 'visit-feedback-list-item';
    listItem.setAttribute('role', 'listitem');

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'visit-feedback-card';
    button.dataset.visitFeedbackId = String(id);
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-controls', 'visit-feedback-detail-section');

    const category = textOrEmpty(item.category_label) || textOrEmpty(item.category_code) || 'Feedback visita';
    button.append(createTextElement('span', 'visit-feedback-card-title', category));

    const meta = document.createElement('span');
    meta.className = 'visit-feedback-card-meta';
    const sentiment = textOrEmpty(item.sentiment_label);
    if (sentiment) {
      meta.append(createTextElement('span', '', sentiment));
    }
    const publishedAt = formatPublishedAt(item.published_at);
    if (publishedAt) {
      meta.append(createTextElement('span', '', publishedAt));
    }
    if (meta.children.length > 0) {
      button.append(meta);
    }

    const summary = textOrEmpty(item.public_summary);
    if (summary) {
      button.append(createTextElement('span', 'visit-feedback-card-summary', summary));
    }

    button.addEventListener('click', () => {
      if (state.session && state.selectedPropertyId !== null && id !== null) {
        void openVisitFeedback(id);
      }
    });

    listItem.append(button);
    return listItem;
  }

  function renderVisitFeedbackList(items, append = false) {
    const nodes = items.map((item) => createVisitFeedbackCard(item));
    if (append) {
      visitFeedbackList.append(...nodes);
    } else {
      visitFeedbackList.replaceChildren(...nodes);
    }
    setSelectedVisitFeedbackCardState();
  }

  function renderVisitFeedbackPagination() {
    visitFeedbackPagination.hidden = !state.visitFeedbackHasMore;
    visitFeedbackLoadMore.disabled = state.visitFeedbackLoadInFlight;
    visitFeedbackLoadMore.textContent = state.visitFeedbackLoadInFlight ? 'Caricamento…' : 'Carica altri';
    visitFeedbackPaginationStatus.textContent = state.visitFeedbackLoadInFlight
      ? 'Caricamento di altri feedback…'
      : '';
  }

  function addVisitFeedbackMeta(label, value) {
    const cleanValue = textOrEmpty(value);
    if (!cleanValue) {
      return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'visit-feedback-meta-row';
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = cleanValue;
    wrapper.append(term, description);
    visitFeedbackDetailMeta.append(wrapper);
  }

  function renderVisitFeedbackDetail(payload, id) {
    const item = payload && payload.visit_feedback && typeof payload.visit_feedback === 'object'
      ? payload.visit_feedback
      : {};
    const category = textOrEmpty(item.category_label) || textOrEmpty(item.category_code) || 'Feedback visita';
    const sentiment = textOrEmpty(item.sentiment_label);
    const publishedAt = formatPublishedAt(item.published_at);
    const version = Number.isInteger(item.version_number) && item.version_number > 0
      ? String(item.version_number)
      : '';
    const summary = textOrEmpty(item.public_summary);

    state.selectedVisitFeedbackId = id;
    visitFeedbackDetailTitle.textContent = category;
    visitFeedbackDetailMeta.replaceChildren();
    addVisitFeedbackMeta('Sentiment', sentiment);
    addVisitFeedbackMeta('Pubblicato', publishedAt);
    addVisitFeedbackMeta('Versione', version);
    visitFeedbackDetailSummary.textContent = summary;
    setSelectedVisitFeedbackCardState();
    showVisitFeedbackDetailState('content');
  }

  function createDocumentCard(item) {
    const id = documentId(item);
    const listItem = document.createElement('div');
    listItem.className = 'document-list-item';
    listItem.setAttribute('role', 'listitem');

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'document-card';
    button.dataset.documentId = String(id);
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-controls', 'document-detail-section');

    const title = textOrEmpty(item.public_title) || 'Documento';
    button.append(createTextElement('span', 'document-card-title', title));

    const meta = document.createElement('span');
    meta.className = 'document-card-meta';
    const type = textOrEmpty(item.public_document_type_label) || textOrEmpty(item.public_document_type);
    if (type) {
      meta.append(createTextElement('span', '', type));
    }
    if (Number.isInteger(item.version_number) && item.version_number > 0) {
      meta.append(createTextElement('span', '', `Versione ${item.version_number}`));
    }
    const publishedAt = formatPublishedAt(item.published_at);
    if (publishedAt) {
      meta.append(createTextElement('span', '', publishedAt));
    }
    if (meta.children.length > 0) {
      button.append(meta);
    }

    const filename = textOrEmpty(item.download_filename);
    if (filename) {
      button.append(createTextElement('span', 'document-card-file', filename));
    }

    if (textOrEmpty(item.acknowledged_at)) {
      button.append(createTextElement('span', 'document-card-status', 'Presa visione confermata'));
    } else if (item.acknowledgement_required === true) {
      const required = createTextElement('span', 'document-card-status is-required', 'Presa visione richiesta');
      button.append(required);
    }

    button.addEventListener('click', () => {
      if (state.session && state.selectedPropertyId !== null && id !== null) {
        void openDocument(id);
      }
    });

    listItem.append(button);
    return listItem;
  }

  function renderDocumentsList(items) {
    documentsList.replaceChildren();
    items.forEach((item) => {
      documentsList.append(createDocumentCard(item));
    });
    setSelectedDocumentCardState();
  }

  function addDocumentMeta(label, value) {
    const cleanValue = textOrEmpty(value);
    if (!cleanValue) {
      return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'document-meta-row';
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = cleanValue;
    wrapper.append(term, description);
    documentDetailMeta.append(wrapper);
  }

  function updateDocumentItemAcknowledgement(id, acknowledgedAt) {
    state.documentItems = state.documentItems.map((item) => {
      if (documentId(item) !== id) {
        return item;
      }
      return { ...item, acknowledged_at: acknowledgedAt };
    });
    renderDocumentsList(state.documentItems);
  }

  function renderDocumentAcknowledgeState(item, id) {
    const required = item.acknowledgement_required === true;
    const acknowledgedAt = textOrEmpty(item.acknowledged_at);
    documentAcknowledgeStatus.classList.remove('is-error');
    documentAcknowledgeButton.dataset.documentId = String(id);

    if (!required) {
      documentAcknowledgeButton.hidden = true;
      documentAcknowledgeButton.disabled = false;
      documentAcknowledgeButton.textContent = 'Conferma presa visione';
      documentAcknowledgeStatus.textContent = 'Nessuna presa visione richiesta per questo documento.';
      return;
    }

    documentAcknowledgeButton.hidden = false;
    if (acknowledgedAt) {
      documentAcknowledgeButton.disabled = true;
      documentAcknowledgeButton.textContent = 'Presa visione confermata';
      const formatted = formatPublishedAt(acknowledgedAt);
      documentAcknowledgeStatus.textContent = formatted
        ? `Presa visione confermata il ${formatted}.`
        : 'Presa visione già confermata.';
      return;
    }

    if (state.documentAcknowledgeInFlight.has(id)) {
      documentAcknowledgeButton.disabled = true;
      documentAcknowledgeButton.textContent = 'Conferma in corso…';
      documentAcknowledgeStatus.textContent = 'Registrazione della presa visione in corso…';
      return;
    }

    documentAcknowledgeButton.disabled = false;
    documentAcknowledgeButton.textContent = 'Conferma presa visione';
    documentAcknowledgeStatus.textContent = 'Aprire o scaricare il documento non equivale a confermare la presa visione.';
  }

  function renderDocumentDownload(item, id) {
    const available = item.download_available === true;
    const filename = textOrEmpty(item.download_filename);
    documentDownloadStatus.classList.remove('is-error');
    documentDownloadLink.hidden = !available;
    documentDownloadLink.setAttribute('href', available
      ? `${API_BASE}/documents/${encodeURIComponent(String(id))}/download`
      : '#');
    documentDownloadLink.setAttribute('target', '_blank');
    documentDownloadLink.setAttribute('rel', 'noopener');
    if (available && filename) {
      documentDownloadLink.setAttribute('download', filename);
    } else {
      documentDownloadLink.removeAttribute('download');
    }
    documentDownloadStatus.textContent = available
      ? 'Il file viene scaricato direttamente tramite il portale autenticato.'
      : 'Download non disponibile per questo documento.';
  }

  function renderDocumentDetail(payload, id) {
    const item = payload && payload.document && typeof payload.document === 'object'
      ? payload.document
      : {};
    const title = textOrEmpty(item.public_title) || 'Documento';
    const type = textOrEmpty(item.public_document_type_label) || textOrEmpty(item.public_document_type);
    const version = Number.isInteger(item.version_number) && item.version_number > 0
      ? String(item.version_number)
      : '';
    const publishedAt = formatPublishedAt(item.published_at);
    const expiresAt = formatPublishedAt(item.expires_at);
    const mimeType = textOrEmpty(item.mime_type);
    const size = formatFileSize(item.size_bytes);
    const filename = textOrEmpty(item.download_filename);

    state.selectedDocumentId = id;
    const acknowledgedAt = textOrEmpty(item.acknowledged_at);
    if (acknowledgedAt) {
      updateDocumentItemAcknowledgement(id, acknowledgedAt);
    }
    documentDetailTitle.textContent = title;
    documentDetailMeta.replaceChildren();
    addDocumentMeta('Tipo', type);
    addDocumentMeta('Versione', version);
    addDocumentMeta('Pubblicato', publishedAt);
    addDocumentMeta('Scadenza', expiresAt);
    addDocumentMeta('Formato', mimeType);
    addDocumentMeta('Dimensione', size);
    addDocumentMeta('File', filename);
    renderDocumentDownload(item, id);
    renderDocumentAcknowledgeState(item, id);
    setSelectedDocumentCardState();
    showDocumentDetailState('content');
  }

  function addPublicationMeta(label, value) {
    const cleanValue = textOrEmpty(value);
    if (!cleanValue) {
      return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'publication-meta-row';
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = cleanValue;
    wrapper.append(term, description);
    publicationDetailMeta.append(wrapper);
  }

  function requestPublicView(item) {
    return {
      feedback_type: textOrEmpty(item && item.feedback_type),
      subject: textOrEmpty(item && item.subject),
      message: textOrEmpty(item && item.message),
      status: textOrEmpty(item && item.status),
      submitted_at: textOrEmpty(item && item.submitted_at),
      availability_from: textOrEmpty(item && item.availability_from),
      availability_to: textOrEmpty(item && item.availability_to),
      handled_at: textOrEmpty(item && item.handled_at),
      public_response: textOrEmpty(item && item.public_response),
    };
  }

  function appendRequestMeta(container, label, value) {
    if (!value) {
      return;
    }
    const row = document.createElement('div');
    row.className = 'request-meta-row';
    row.append(createTextElement('dt', '', label));
    row.append(createTextElement('dd', '', value));
    container.append(row);
  }

  function createRequestCard(item) {
    const publicItem = requestPublicView(item);
    const card = document.createElement('article');
    card.className = 'request-card';
    card.setAttribute('role', 'listitem');

    const top = document.createElement('div');
    top.className = 'request-card-topline';
    top.append(createTextElement('span', 'request-card-category', requestTypeLabel(publicItem.feedback_type)));
    const status = createTextElement('span', 'request-status-badge', requestStatusLabel(publicItem.status));
    if (publicItem.status) {
      status.classList.add(`is-${publicItem.status}`);
    }
    top.append(status);
    card.append(top);

    if (publicItem.subject) {
      card.append(createTextElement('h5', 'request-card-subject', publicItem.subject));
    }
    if (publicItem.message) {
      card.append(createTextElement('p', 'request-card-message', publicItem.message));
    }

    const meta = document.createElement('dl');
    meta.className = 'request-meta';
    appendRequestMeta(meta, 'Inviata', formatPublishedAt(publicItem.submitted_at));
    appendRequestMeta(meta, 'Disponibile da', formatPublishedAt(publicItem.availability_from));
    appendRequestMeta(meta, 'Disponibile fino a', formatPublishedAt(publicItem.availability_to));
    appendRequestMeta(meta, 'Gestita il', formatPublishedAt(publicItem.handled_at));
    if (meta.children.length) {
      card.append(meta);
    }

    if (publicItem.public_response) {
      const response = document.createElement('div');
      response.className = 'request-public-response';
      response.append(createTextElement('h6', '', 'Risposta del consulente'));
      response.append(createTextElement('p', '', publicItem.public_response));
      card.append(response);
    }
    return card;
  }

  function renderRequests(items) {
    requestsList.replaceChildren();
    items.forEach((item) => requestsList.append(createRequestCard(item)));
  }

  function requestDateTimeValue(raw) {
    const value = textOrEmpty(raw);
    if (!value) {
      return null;
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    return parsed.toISOString();
  }

  function requestValidationResult() {
    const feedbackType = textOrEmpty(requestType.value);
    if (!Object.prototype.hasOwnProperty.call(REQUEST_TYPE_LABELS, feedbackType)) {
      return { message: 'Seleziona un tipo di richiesta valido.', focus: requestType };
    }

    const subject = textOrEmpty(requestSubject.value);
    if (!subject) {
      return { message: 'Inserisci l’oggetto della richiesta.', focus: requestSubject };
    }
    if (subject.length > REQUEST_SUBJECT_MAX) {
      return { message: `L’oggetto non può superare ${REQUEST_SUBJECT_MAX} caratteri.`, focus: requestSubject };
    }

    const message = textOrEmpty(requestMessage.value);
    if (!message) {
      return { message: 'Inserisci il messaggio della richiesta.', focus: requestMessage };
    }
    if (message.length > REQUEST_MESSAGE_MAX) {
      return { message: 'Il messaggio non può superare 5.000 caratteri.', focus: requestMessage };
    }

    const payload = { feedback_type: feedbackType, subject, message };
    if (feedbackType === 'availability_update') {
      const rawFrom = textOrEmpty(requestAvailabilityFrom.value);
      const rawTo = textOrEmpty(requestAvailabilityTo.value);
      if (!rawFrom && !rawTo) {
        return { message: 'Indica almeno una data o un orario di disponibilità.', focus: requestAvailabilityFrom };
      }
      const from = rawFrom ? requestDateTimeValue(rawFrom) : null;
      const to = rawTo ? requestDateTimeValue(rawTo) : null;
      if (rawFrom && !from) {
        return { message: 'La data iniziale non è valida.', focus: requestAvailabilityFrom };
      }
      if (rawTo && !to) {
        return { message: 'La data finale non è valida.', focus: requestAvailabilityTo };
      }
      if (from && to && new Date(to).getTime() <= new Date(from).getTime()) {
        return { message: 'La disponibilità finale deve essere successiva a quella iniziale.', focus: requestAvailabilityTo };
      }
      if (from) payload.availability_from = from;
      if (to) payload.availability_to = to;
    }
    return { payload };
  }

  function renderAcknowledgeState(id, required) {
    acknowledgeStatus.classList.remove('is-error');
    acknowledgeButton.dataset.publicationId = String(id);

    if (!required) {
      acknowledgeButton.hidden = true;
      acknowledgeButton.disabled = false;
      acknowledgeButton.textContent = 'Conferma presa visione';
      acknowledgeStatus.textContent = 'Nessuna presa visione richiesta per questo aggiornamento.';
      return;
    }

    acknowledgeButton.hidden = false;
    if (state.acknowledgedPublicationIds.has(id)) {
      acknowledgeButton.disabled = true;
      acknowledgeButton.textContent = 'Presa visione confermata';
      acknowledgeStatus.textContent = 'Hai già confermato la presa visione in questa sessione.';
      return;
    }

    if (state.acknowledgeInFlight.has(id)) {
      acknowledgeButton.disabled = true;
      acknowledgeButton.textContent = 'Conferma in corso…';
      acknowledgeStatus.textContent = 'Registrazione della presa visione in corso…';
      return;
    }

    acknowledgeButton.disabled = false;
    acknowledgeButton.textContent = 'Conferma presa visione';
    acknowledgeStatus.textContent = 'Aprire l’aggiornamento non equivale a confermare la presa visione.';
  }

  function renderPublicationDetail(payload, id) {
    const item = payload && typeof payload === 'object' ? payload : {};
    const title = textOrEmpty(item.title) || 'Aggiornamento';
    const summary = textOrEmpty(item.summary);
    const body = textOrEmpty(item.body);
    const type = publicationTypeLabel(item.publication_type);
    const publishedAt = formatPublishedAt(item.published_at);
    const version = Number.isInteger(item.version_number) && item.version_number > 0
      ? String(item.version_number)
      : '';
    const required = item.acknowledgement_required === true;

    publicationDetailTitle.textContent = title;
    publicationDetailMeta.replaceChildren();
    addPublicationMeta('Tipo', type);
    addPublicationMeta('Pubblicato', publishedAt);
    addPublicationMeta('Versione', version);
    publicationDetailSummary.textContent = summary;
    publicationDetailSummary.hidden = !summary;
    publicationDetailBody.textContent = body;
    state.selectedPublicationRequiresAck = required;
    renderAcknowledgeState(id, required);
    showPublicationState('content');
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

  async function confirmSessionAfterTimelineNotFound(generation, propertyAtStart) {
    try {
      const session = await loadSession();
      if (
        generation !== state.timelineGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.timelineGeneration
        || state.selectedPropertyId !== propertyAtStart
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      showTimelineState('error', timelineErrorText(error));
      return false;
    }
  }

  async function confirmSessionAfterPublicationNotFound(generation, propertyAtStart, id) {
    try {
      const session = await loadSession();
      if (
        generation !== state.publicationGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedPublicationId !== id
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.publicationGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedPublicationId !== id
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      clearPublicationContent();
      showPublicationState('error', publicationErrorText(error));
      return false;
    }
  }

  async function confirmSessionAfterVisitFeedbackNotFound(generation, propertyAtStart) {
    try {
      const session = await loadSession();
      if (
        generation !== state.visitFeedbackGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.visitFeedbackGeneration
        || state.selectedPropertyId !== propertyAtStart
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      showVisitFeedbackState('error', visitFeedbackErrorText(error));
      return false;
    }
  }

  async function confirmSessionAfterVisitFeedbackDetailNotFound(generation, propertyAtStart, id) {
    try {
      const session = await loadSession();
      if (
        generation !== state.visitFeedbackDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedVisitFeedbackId !== id
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.visitFeedbackDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedVisitFeedbackId !== id
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      clearVisitFeedbackDetailContent();
      showVisitFeedbackDetailState('error', visitFeedbackDetailErrorText(error));
      return false;
    }
  }

  async function confirmSessionAfterDocumentsNotFound(generation, propertyAtStart) {
    try {
      const session = await loadSession();
      if (
        generation !== state.documentGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.documentGeneration
        || state.selectedPropertyId !== propertyAtStart
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      showDocumentsState('error', documentsErrorText(error));
      return false;
    }
  }

  async function confirmSessionAfterDocumentDetailNotFound(generation, propertyAtStart, id) {
    try {
      const session = await loadSession();
      if (
        generation !== state.documentDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedDocumentId !== id
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.documentDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedDocumentId !== id
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      clearDocumentDetailContent();
      showDocumentDetailState('error', documentDetailErrorText(error));
      return false;
    }
  }

  async function confirmSessionAfterRequestsNotFound(generation, propertyAtStart) {
    try {
      const session = await loadSession();
      if (
        generation !== state.requestGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return false;
      }
      state.session = session;
      return true;
    } catch (error) {
      if (
        generation !== state.requestGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return false;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return false;
      }
      return true;
    }
  }

  async function loadTimeline(propertyAtStart) {
    if (!state.session || state.selectedPropertyId !== propertyAtStart) {
      return;
    }

    const generation = ++state.timelineGeneration;
    state.timelineItems = [];
    timelineList.replaceChildren();
    resetPublicationDetail();
    showTimelineState('loading');

    let payload;
    try {
      payload = await apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/timeline`);
    } catch (error) {
      if (
        generation !== state.timelineGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return;
      }

      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }

      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterTimelineNotFound(generation, propertyAtStart);
        if (
          sessionValid
          && generation === state.timelineGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.session
        ) {
          showTimelineState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }

      showTimelineState('error', timelineErrorText(error));
      return;
    }

    if (
      generation !== state.timelineGeneration
      || state.selectedPropertyId !== propertyAtStart
      || !state.session
    ) {
      return;
    }

    const rawItems = payload && Array.isArray(payload.items) ? payload.items : [];
    state.timelineItems = rawItems.filter((item) => publicationId(item) !== null);

    if (state.timelineItems.length === 0) {
      showTimelineState('empty');
      return;
    }

    renderTimelineList(state.timelineItems);
    showTimelineState('content');
    showPublicationState('empty');
  }

  async function openPublication(id) {
    if (!state.session || state.selectedPropertyId === null) {
      return;
    }

    const available = state.timelineItems.some((item) => publicationId(item) === id);
    if (!available) {
      return;
    }

    const propertyAtStart = state.selectedPropertyId;
    state.selectedPublicationId = id;
    state.selectedPublicationRequiresAck = false;
    setSelectedPublicationCardState();
    const generation = ++state.publicationGeneration;
    clearPublicationContent();
    showPublicationState('loading');

    let payload;
    try {
      payload = await apiRequest(`/publications/${encodeURIComponent(String(id))}`);
    } catch (error) {
      if (
        generation !== state.publicationGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedPublicationId !== id
        || !state.session
      ) {
        return;
      }

      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }

      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterPublicationNotFound(generation, propertyAtStart, id);
        if (
          sessionValid
          && generation === state.publicationGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.selectedPublicationId === id
          && state.session
        ) {
          clearPublicationContent();
          showPublicationState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }

      clearPublicationContent();
      showPublicationState('error', publicationErrorText(error));
      return;
    }

    if (
      generation !== state.publicationGeneration
      || state.selectedPropertyId !== propertyAtStart
      || state.selectedPublicationId !== id
      || !state.session
    ) {
      return;
    }

    renderPublicationDetail(payload, id);
  }

  async function acknowledgeCurrentPublication() {
    const id = state.selectedPublicationId;
    const propertyAtStart = state.selectedPropertyId;
    if (
      !state.session
      || propertyAtStart === null
      || id === null
      || !state.selectedPublicationRequiresAck
      || state.acknowledgedPublicationIds.has(id)
      || state.acknowledgeInFlight.has(id)
    ) {
      return;
    }

    const generation = state.publicationGeneration;
    state.acknowledgeInFlight.add(id);
    renderAcknowledgeState(id, true);

    try {
      await apiRequest(`/publications/${encodeURIComponent(String(id))}/acknowledge`, { method: 'POST' });
    } catch (error) {
      state.acknowledgeInFlight.delete(id);
      if (
        generation !== state.publicationGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedPublicationId !== id
        || !state.session
      ) {
        return;
      }

      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }

      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterPublicationNotFound(generation, propertyAtStart, id);
        if (
          sessionValid
          && generation === state.publicationGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.selectedPublicationId === id
          && state.session
        ) {
          clearPublicationContent();
          showPublicationState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }

      acknowledgeStatus.textContent = publicationErrorText(error);
      acknowledgeStatus.classList.add('is-error');
      renderAcknowledgeState(id, true);
      acknowledgeStatus.textContent = publicationErrorText(error);
      acknowledgeStatus.classList.add('is-error');
      return;
    }

    state.acknowledgeInFlight.delete(id);
    state.acknowledgedPublicationIds.add(id);
    if (
      generation !== state.publicationGeneration
      || state.selectedPropertyId !== propertyAtStart
      || state.selectedPublicationId !== id
      || !state.session
    ) {
      return;
    }

    renderAcknowledgeState(id, true);
  }

  async function loadVisitFeedback(propertyAtStart, append = false) {
    if (
      !state.session
      || state.selectedPropertyId !== propertyAtStart
      || state.visitFeedbackLoadInFlight
    ) {
      return;
    }

    const limit = 50;
    const offset = append ? state.visitFeedbackOffset : 0;
    const generation = ++state.visitFeedbackGeneration;
    state.visitFeedbackLoadInFlight = true;

    if (!append) {
      state.visitFeedbackItems = [];
      state.visitFeedbackOffset = 0;
      state.visitFeedbackHasMore = false;
      visitFeedbackList.replaceChildren();
      resetVisitFeedbackDetail();
      showVisitFeedbackState('loading');
    } else {
      renderVisitFeedbackPagination();
    }

    let payload;
    try {
      payload = await apiRequest(
        `/properties/${encodeURIComponent(String(propertyAtStart))}/visit-feedback?limit=${limit}&offset=${offset}`,
      );
    } catch (error) {
      if (
        generation !== state.visitFeedbackGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return;
      }

      state.visitFeedbackLoadInFlight = false;
      renderVisitFeedbackPagination();

      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }

      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterVisitFeedbackNotFound(generation, propertyAtStart);
        if (
          sessionValid
          && generation === state.visitFeedbackGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.session
        ) {
          showVisitFeedbackState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }

      if (append && state.visitFeedbackItems.length > 0) {
        showVisitFeedbackState('content');
        visitFeedbackPagination.hidden = false;
        visitFeedbackPaginationStatus.textContent = visitFeedbackErrorText(error);
        return;
      }

      showVisitFeedbackState('error', visitFeedbackErrorText(error));
      return;
    }

    if (
      generation !== state.visitFeedbackGeneration
      || state.selectedPropertyId !== propertyAtStart
      || !state.session
    ) {
      return;
    }

    state.visitFeedbackLoadInFlight = false;
    const rawItems = payload && Array.isArray(payload.items) ? payload.items : [];
    const validItems = rawItems.filter((item) => visitFeedbackId(item) !== null);

    if (append) {
      const existingIds = new Set(state.visitFeedbackItems.map((item) => visitFeedbackId(item)));
      const newItems = validItems.filter((item) => !existingIds.has(visitFeedbackId(item)));
      state.visitFeedbackItems = state.visitFeedbackItems.concat(newItems);
      renderVisitFeedbackList(newItems, true);
    } else {
      state.visitFeedbackItems = validItems;
      renderVisitFeedbackList(validItems);
    }

    state.visitFeedbackOffset = offset + rawItems.length;
    state.visitFeedbackHasMore = rawItems.length === limit;

    if (state.visitFeedbackItems.length === 0) {
      showVisitFeedbackState('empty');
      return;
    }

    showVisitFeedbackState('content');
    renderVisitFeedbackPagination();
    if (!append) {
      showVisitFeedbackDetailState('empty');
    }
  }

  async function openVisitFeedback(id) {
    if (!state.session || state.selectedPropertyId === null) {
      return;
    }

    const available = state.visitFeedbackItems.some((item) => visitFeedbackId(item) === id);
    if (!available) {
      return;
    }

    const propertyAtStart = state.selectedPropertyId;
    state.selectedVisitFeedbackId = id;
    setSelectedVisitFeedbackCardState();
    const generation = ++state.visitFeedbackDetailGeneration;
    clearVisitFeedbackDetailContent();
    showVisitFeedbackDetailState('loading');

    let payload;
    try {
      payload = await apiRequest(`/visit-feedback/${encodeURIComponent(String(id))}`);
    } catch (error) {
      if (
        generation !== state.visitFeedbackDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedVisitFeedbackId !== id
        || !state.session
      ) {
        return;
      }

      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }

      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterVisitFeedbackDetailNotFound(
          generation,
          propertyAtStart,
          id,
        );
        if (
          sessionValid
          && generation === state.visitFeedbackDetailGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.selectedVisitFeedbackId === id
          && state.session
        ) {
          clearVisitFeedbackDetailContent();
          showVisitFeedbackDetailState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }

      clearVisitFeedbackDetailContent();
      showVisitFeedbackDetailState('error', visitFeedbackDetailErrorText(error));
      return;
    }

    if (
      generation !== state.visitFeedbackDetailGeneration
      || state.selectedPropertyId !== propertyAtStart
      || state.selectedVisitFeedbackId !== id
      || !state.session
    ) {
      return;
    }

    renderVisitFeedbackDetail(payload, id);
  }

  async function loadDocuments(propertyAtStart) {
    if (!state.session || state.selectedPropertyId !== propertyAtStart) {
      return;
    }

    const generation = ++state.documentGeneration;
    state.documentItems = [];
    documentsList.replaceChildren();
    resetDocumentDetail();
    showDocumentsState('loading');

    let payload;
    try {
      payload = await apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/documents`);
    } catch (error) {
      if (
        generation !== state.documentGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return;
      }
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterDocumentsNotFound(generation, propertyAtStart);
        if (
          sessionValid
          && generation === state.documentGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.session
        ) {
          showDocumentsState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }
      showDocumentsState('error', documentsErrorText(error));
      return;
    }

    if (
      generation !== state.documentGeneration
      || state.selectedPropertyId !== propertyAtStart
      || !state.session
    ) {
      return;
    }

    const rawItems = payload && Array.isArray(payload.items) ? payload.items : [];
    state.documentItems = rawItems.filter((item) => documentId(item) !== null);
    if (state.documentItems.length === 0) {
      showDocumentsState('empty');
      return;
    }
    renderDocumentsList(state.documentItems);
    showDocumentsState('content');
    showDocumentDetailState('empty');
  }

  async function openDocument(id) {
    if (!state.session || state.selectedPropertyId === null) {
      return;
    }

    const available = state.documentItems.some((item) => documentId(item) === id);
    if (!available) {
      return;
    }

    const propertyAtStart = state.selectedPropertyId;
    state.selectedDocumentId = id;
    setSelectedDocumentCardState();
    const generation = ++state.documentDetailGeneration;
    clearDocumentDetailContent();
    showDocumentDetailState('loading');

    let payload;
    try {
      payload = await apiRequest(`/documents/${encodeURIComponent(String(id))}`);
    } catch (error) {
      if (
        generation !== state.documentDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedDocumentId !== id
        || !state.session
      ) {
        return;
      }
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterDocumentDetailNotFound(
          generation,
          propertyAtStart,
          id,
        );
        if (
          sessionValid
          && generation === state.documentDetailGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.selectedDocumentId === id
          && state.session
        ) {
          clearDocumentDetailContent();
          showDocumentDetailState('error', 'Documento non disponibile o accesso non più valido.');
        }
        return;
      }
      clearDocumentDetailContent();
      showDocumentDetailState('error', documentDetailErrorText(error));
      return;
    }

    if (
      generation !== state.documentDetailGeneration
      || state.selectedPropertyId !== propertyAtStart
      || state.selectedDocumentId !== id
      || !state.session
    ) {
      return;
    }

    renderDocumentDetail(payload, id);
  }

  async function acknowledgeCurrentDocument() {
    const id = state.selectedDocumentId;
    if (
      !state.session
      || state.selectedPropertyId === null
      || id === null
      || state.documentAcknowledgeInFlight.has(id)
    ) {
      return;
    }

    const propertyAtStart = state.selectedPropertyId;
    const generation = state.documentDetailGeneration;
    state.documentAcknowledgeInFlight.add(id);
    const selectedItem = state.documentItems.find((item) => documentId(item) === id) || {};
    renderDocumentAcknowledgeState(selectedItem, id);

    let receipt;
    try {
      receipt = await apiRequest(`/documents/${encodeURIComponent(String(id))}/acknowledge`, { method: 'POST' });
    } catch (error) {
      state.documentAcknowledgeInFlight.delete(id);
      if (
        generation !== state.documentDetailGeneration
        || state.selectedPropertyId !== propertyAtStart
        || state.selectedDocumentId !== id
        || !state.session
      ) {
        return;
      }
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterDocumentDetailNotFound(
          generation,
          propertyAtStart,
          id,
        );
        if (
          sessionValid
          && generation === state.documentDetailGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.selectedDocumentId === id
          && state.session
        ) {
          clearDocumentDetailContent();
          showDocumentDetailState('error', 'Documento non disponibile o accesso non più valido.');
        }
        return;
      }
      documentAcknowledgeStatus.classList.add('is-error');
      documentAcknowledgeStatus.textContent = documentDetailErrorText(error);
      documentAcknowledgeButton.disabled = false;
      documentAcknowledgeButton.textContent = 'Riprova presa visione';
      return;
    }

    state.documentAcknowledgeInFlight.delete(id);
    if (
      generation !== state.documentDetailGeneration
      || state.selectedPropertyId !== propertyAtStart
      || state.selectedDocumentId !== id
      || !state.session
    ) {
      return;
    }

    const acknowledgedAt = textOrEmpty(receipt && receipt.acknowledged_at) || new Date().toISOString();
    updateDocumentItemAcknowledgement(id, acknowledgedAt);
    const updatedItem = state.documentItems.find((item) => documentId(item) === id) || {
      acknowledgement_required: true,
      acknowledged_at: acknowledgedAt,
    };
    renderDocumentAcknowledgeState(updatedItem, id);
  }

  async function loadRequests(propertyAtStart) {
    if (!state.session || state.selectedPropertyId !== propertyAtStart) {
      return;
    }

    const generation = ++state.requestGeneration;
    state.requestItems = [];
    requestsList.replaceChildren();
    showRequestsState('loading');

    let payload;
    try {
      payload = await apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/feedback`);
    } catch (error) {
      if (
        generation !== state.requestGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return;
      }
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterRequestsNotFound(generation, propertyAtStart);
        if (
          sessionValid
          && generation === state.requestGeneration
          && state.selectedPropertyId === propertyAtStart
          && state.session
        ) {
          showRequestsState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }
      showRequestsState('error', requestsErrorText(error));
      return;
    }

    if (
      generation !== state.requestGeneration
      || state.selectedPropertyId !== propertyAtStart
      || !state.session
    ) {
      return;
    }

    const rawItems = payload && Array.isArray(payload.items) ? payload.items : [];
    state.requestItems = rawItems.filter((item) => item && typeof item === 'object');
    if (state.requestItems.length === 0) {
      showRequestsState('empty');
      return;
    }
    renderRequests(state.requestItems);
    showRequestsState('content');
  }

  async function submitRequest() {
    if (
      !state.session
      || state.selectedPropertyId === null
      || state.requestSubmitInFlight
    ) {
      return;
    }

    clearRequestFormStatus();
    const validation = requestValidationResult();
    if (!validation.payload) {
      requestFormStatus.classList.add('is-error');
      requestFormStatus.textContent = validation.message || 'Controlla i campi della richiesta.';
      if (validation.focus) validation.focus.focus();
      return;
    }

    const propertyAtStart = state.selectedPropertyId;
    const generation = state.requestGeneration;
    state.requestSubmitInFlight = true;
    requestSubmit.disabled = true;
    requestSubmit.textContent = 'Invio in corso…';
    requestFormStatus.textContent = 'Invio della richiesta in corso…';

    try {
      await apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(validation.payload),
      });
    } catch (error) {
      if (
        generation !== state.requestGeneration
        || state.selectedPropertyId !== propertyAtStart
        || !state.session
      ) {
        return;
      }
      state.requestSubmitInFlight = false;
      requestSubmit.disabled = false;
      requestSubmit.textContent = 'Invia richiesta';
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmSessionAfterRequestsNotFound(generation, propertyAtStart);
        if (!sessionValid) {
          return;
        }
      }
      requestFormStatus.classList.add('is-error');
      requestFormStatus.textContent = requestSubmitErrorText(error);
      return;
    }

    if (
      generation !== state.requestGeneration
      || state.selectedPropertyId !== propertyAtStart
      || !state.session
    ) {
      return;
    }

    state.requestSubmitInFlight = false;
    resetRequestForm();
    requestFormStatus.classList.remove('is-error');
    requestFormStatus.textContent = 'Richiesta inviata correttamente.';
    await loadRequests(propertyAtStart);
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
    resetTimelineState();
    resetVisitFeedbackState();
    resetDocumentsState();
    resetRequestsState();
    showPropertyState('loading');

    try {
      const payload = await apiRequest(`/properties/${encodeURIComponent(String(id))}`);
      if (generation !== state.propertyGeneration || !state.session) {
        return;
      }
      renderPropertyDetail(payload);
      await loadTimeline(id);
      await loadDocuments(id);
      await loadVisitFeedback(id);
      await loadRequests(id);
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

  timelineRetry.addEventListener('click', () => {
    if (state.session && state.selectedPropertyId !== null) {
      void loadTimeline(state.selectedPropertyId);
    }
  });

  publicationDetailRetry.addEventListener('click', () => {
    if (state.session && state.selectedPublicationId !== null) {
      void openPublication(state.selectedPublicationId);
    }
  });

  acknowledgeButton.addEventListener('click', () => {
    void acknowledgeCurrentPublication();
  });

  visitFeedbackRetry.addEventListener('click', () => {
    if (state.session && state.selectedPropertyId !== null) {
      void loadVisitFeedback(state.selectedPropertyId);
    }
  });

  visitFeedbackLoadMore.addEventListener('click', () => {
    if (
      state.session
      && state.selectedPropertyId !== null
      && state.visitFeedbackHasMore
      && !state.visitFeedbackLoadInFlight
    ) {
      void loadVisitFeedback(state.selectedPropertyId, true);
    }
  });

  visitFeedbackDetailRetry.addEventListener('click', () => {
    if (state.session && state.selectedVisitFeedbackId !== null) {
      void openVisitFeedback(state.selectedVisitFeedbackId);
    }
  });

  documentsRetry.addEventListener('click', () => {
    if (state.session && state.selectedPropertyId !== null) {
      void loadDocuments(state.selectedPropertyId);
    }
  });

  documentDetailRetry.addEventListener('click', () => {
    if (state.session && state.selectedDocumentId !== null) {
      void openDocument(state.selectedDocumentId);
    }
  });

  documentAcknowledgeButton.addEventListener('click', () => {
    void acknowledgeCurrentDocument();
  });

  documentDownloadLink.addEventListener('click', () => {
    if (!documentDownloadLink.hidden) {
      documentDownloadStatus.textContent = 'Download richiesto tramite il portale autenticato. Se il file non è più disponibile, il portale ne impedirà l’accesso.';
    }
  });

  requestType.addEventListener('change', () => {
    setRequestAvailabilityVisibility();
    clearRequestFormStatus();
  });

  requestForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await submitRequest();
  });

  requestsRetry.addEventListener('click', () => {
    if (state.session && state.selectedPropertyId !== null) {
      void loadRequests(state.selectedPropertyId);
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
