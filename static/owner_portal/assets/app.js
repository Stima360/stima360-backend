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

  // LMC6_START - "La Mia Casa" (PRE-INCARICO). Superficie ADDITIVA: tutto
  // cio' che sta sotto convive con il portale POST-INCARICO, che non viene
  // ne' spostato ne' riscritto. Le decisioni su cosa mostrare non sono qui:
  // stanno in `home-view-model.js`, che e' puro e si puo' eseguire nei test.
  const VM = window.OwnerHomeViewModel;

  const emailLoginForm = document.getElementById('email-login-form');
  const emailInput = document.getElementById('email-input');
  const emailLoginButton = document.getElementById('email-login-button');
  const emailLoginMessage = document.getElementById('email-login-message');

  const homesSection = document.getElementById('homes-section');
  const homeCount = document.getElementById('home-count');
  const homesLoading = document.getElementById('homes-loading');
  const homesError = document.getElementById('homes-error');
  const homesErrorMessage = document.getElementById('homes-error-message');
  const homesRetry = document.getElementById('homes-retry');
  const homesContent = document.getElementById('homes-content');
  const homeList = document.getElementById('home-list');

  const homeDetailLoading = document.getElementById('home-detail-loading');
  const homeDetailEmpty = document.getElementById('home-detail-empty');
  const homeDetailError = document.getElementById('home-detail-error');
  const homeDetailErrorMessage = document.getElementById('home-detail-error-message');
  const homeDetailRetry = document.getElementById('home-detail-retry');
  const homeDetailContent = document.getElementById('home-detail-content');
  const homeDetailAddress = document.getElementById('home-detail-address');
  const homeDetailSummary = document.getElementById('home-detail-summary');
  const homeValueList = document.getElementById('home-value-list');
  const homeValueNote = document.getElementById('home-value-note');
  const homeHistoryMessage = document.getElementById('home-history-message');
  const homeHistoryChart = document.getElementById('home-history-chart');
  const homeHistoryRange = document.getElementById('home-history-range');
  const homeHistoryChanges = document.getElementById('home-history-changes');
  const homeDemandUnavailable = document.getElementById('home-demand-unavailable');
  const homeDemandContent = document.getElementById('home-demand-content');
  const homeDemandLabel = document.getElementById('home-demand-label');
  const homeDemandMessage = document.getElementById('home-demand-message');
  const homeDemandCounts = document.getElementById('home-demand-counts');
  const homeDemandDisclaimer = document.getElementById('home-demand-disclaimer');
  const homeHistoryToggle = document.getElementById('home-history-toggle');
  const homeDemandToggle = document.getElementById('home-demand-toggle');
  const homeConsultationCta = document.getElementById('home-consultation-cta');
  const homeConsultationConfirm = document.getElementById('home-consultation-confirm');
  const homeConsultationCancel = document.getElementById('home-consultation-cancel');
  const homeConsultationSend = document.getElementById('home-consultation-send');
  const homeConsultationStatus = document.getElementById('home-consultation-status');
  const homeProfilePercent = document.getElementById('home-profile-percent');
  const homeProfileBarFill = document.getElementById('home-profile-bar-fill');
  const homeProfileKnown = document.getElementById('home-profile-known');
  const homeProfileMissing = document.getElementById('home-profile-missing');
  const homeProfileNote = document.getElementById('home-profile-note');
  // LMC6_END

  // LMC10_START
  const homeProfileUpdated = document.getElementById('home-profile-updated');
  const homeProfileEdit = document.getElementById('home-profile-edit');
  const homeProfileForm = document.getElementById('home-profile-form');
  const homeProfileFields = document.getElementById('home-profile-fields');
  const homeProfilePertinenzeList = document.getElementById('home-profile-pertinenze-list');
  const homeProfileAltro = document.getElementById('home-profile-altro');
  const homeProfileCancel = document.getElementById('home-profile-cancel');
  const homeProfileSave = document.getElementById('home-profile-save');
  const homeProfileStatus = document.getElementById('home-profile-status');
  // LMC10_END

  const dashboardSection = document.getElementById('dashboard-section');
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

  const notificationsUnreadOnly = document.getElementById('notifications-unread-only');
  const notificationsLoading = document.getElementById('notifications-loading');
  const notificationsEmpty = document.getElementById('notifications-empty');
  const notificationsEmptyMessage = document.getElementById('notifications-empty-message');
  const notificationsError = document.getElementById('notifications-error');
  const notificationsErrorMessage = document.getElementById('notifications-error-message');
  const notificationsRetry = document.getElementById('notifications-retry');
  const notificationsContent = document.getElementById('notifications-content');
  const notificationsList = document.getElementById('notifications-list');
  const notificationsPagination = document.getElementById('notifications-pagination');
  const notificationsLoadMore = document.getElementById('notifications-load-more');
  const notificationsPaginationStatus = document.getElementById('notifications-pagination-status');

  const notificationPreferencesLoading = document.getElementById('notification-preferences-loading');
  const notificationPreferencesError = document.getElementById('notification-preferences-error');
  const notificationPreferencesErrorMessage = document.getElementById('notification-preferences-error-message');
  const notificationPreferencesRetry = document.getElementById('notification-preferences-retry');
  const notificationPreferencesForm = document.getElementById('notification-preferences-form');
  const preferenceInApp = document.getElementById('preference-in-app');
  const preferencePublication = document.getElementById('preference-publication');
  const preferenceVisitFeedback = document.getElementById('preference-visit-feedback');
  const preferenceDocument = document.getElementById('preference-document');
  const preferenceRequestUpdate = document.getElementById('preference-request-update');
  const notificationPreferencesSave = document.getElementById('notification-preferences-save');
  const notificationPreferencesStatus = document.getElementById('notification-preferences-status');

  // LMC12_START - "Novita' sulla tua casa": lo stream PRE-INCARICO, dentro
  // la sezione LMC delle case. Storage, API e stato sono SEPARATI da quelli
  // delle notifiche P5 qui sopra; le classi delle card sono le stesse.
  const homeNotificationsSection = document.getElementById('home-notifications-section');
  const homeNotificationsUnreadOnly = document.getElementById('home-notifications-unread-only');
  const homeNotificationsLoading = document.getElementById('home-notifications-loading');
  const homeNotificationsEmpty = document.getElementById('home-notifications-empty');
  const homeNotificationsEmptyMessage = document.getElementById('home-notifications-empty-message');
  const homeNotificationsError = document.getElementById('home-notifications-error');
  const homeNotificationsErrorMessage = document.getElementById('home-notifications-error-message');
  const homeNotificationsRetry = document.getElementById('home-notifications-retry');
  const homeNotificationsContent = document.getElementById('home-notifications-content');
  const homeNotificationsList = document.getElementById('home-notifications-list');
  const homeNotificationsPagination = document.getElementById('home-notifications-pagination');
  const homeNotificationsLoadMore = document.getElementById('home-notifications-load-more');
  const homeNotificationsPaginationStatus = document.getElementById('home-notifications-pagination-status');
  // LMC12_END

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


  const NOTIFICATION_TYPE_LABELS = {
    publication_published: 'Nuovo aggiornamento',
    visit_feedback_published: 'Nuovo feedback visita',
    shared_document_published: 'Nuovo documento',
    request_handled: 'Aggiornamento richiesta',
  };
  const NOTIFICATIONS_LIMIT = 50;

  // LMC12_START
  const HOME_NOTIFICATION_TYPE_LABELS = {
    home_value_changed: 'Valore stimato',
    home_demand_changed: 'Domanda',
    home_method_changed: 'Metodo di stima',
  };
  const HOME_NOTIFICATIONS_LIMIT = 20;
  // LMC12_END

  // LMC6_START
  const EMAIL_LINK_NEUTRAL_MESSAGE =
    'Se l\u2019indirizzo è associato a un accesso STIMA360, riceverai a breve un\u2019email.';
  // Il namespace SVG NON si scrive qui. `test_owner_06_p6` vieta qualunque
  // URL nel sorgente del portale per impedire una dipendenza remota: un URI
  // di namespace non e' una dipendenza - non viene mai scaricato - ma il
  // controllo lavora per sottostringa e non puo' distinguerli. Invece di
  // allentare una sentinella che protegge da un CDN nascosto, il namespace
  // si prende dal DOM: un <svg> scritto nel markup ce l'ha gia'.
  const svgSeed = document.getElementById('home-chart-seed');
  const SVG_NS = svgSeed ? svgSeed.namespaceURI : null;
  // LMC6_END

  const state = {
    session: null,
    busy: false,
    // LMC6_START
    homes: [],
    selectedStimaId: null,
    homesGeneration: 0,
    homeDetailGeneration: 0,
    emailLinkInFlight: false,
    // LMC6_END
    // LMC7_START
    openedSections: new Set(),
    // LMC7_END
    // LMC9_START
    consultationInFlight: false,
    consultationSent: new Set(),
    // LMC9_END
    // LMC10_START
    profileSaveInFlight: false,
    profileEditable: false,
    profileVersion: 0,
    profileEditableFields: [],
    profileValues: {},
    // I nodi creati dal form, tenuti per riferimento invece di essere
    // ricercati con getElementById: sono figli nostri, e cercarli per id
    // nel documento e' un giro inutile attraverso il DOM.
    profileInputs: {},
    // LMC10_END
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

    notificationItems: [],
    notificationGeneration: 0,
    notificationOffset: 0,
    notificationHasMore: false,
    notificationUnreadOnly: false,
    notificationLoadInFlight: false,
    notificationReadInFlight: new Set(),
    notificationPreferencesGeneration: 0,
    notificationPreferencesSaving: false,

    // LMC12_START
    homeNotificationItems: [],
    homeNotificationGeneration: 0,
    homeNotificationOffset: 0,
    homeNotificationHasMore: false,
    homeNotificationUnreadOnly: false,
    homeNotificationLoadInFlight: false,
    homeNotificationReadInFlight: new Set(),
    // LMC12_END
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


  function resetNotificationsState({ preserveFilter = false } = {}) {
    state.notificationGeneration += 1;
    state.notificationItems = [];
    state.notificationOffset = 0;
    state.notificationHasMore = false;
    state.notificationLoadInFlight = false;
    state.notificationReadInFlight.clear();
    if (!preserveFilter) {
      state.notificationUnreadOnly = false;
      notificationsUnreadOnly.checked = false;
    }
    notificationsList.replaceChildren();
    notificationsLoading.hidden = true;
    notificationsEmpty.hidden = true;
    notificationsError.hidden = true;
    notificationsContent.hidden = true;
    notificationsErrorMessage.textContent = '';
    notificationsEmptyMessage.textContent = 'Non ci sono notifiche da mostrare.';
    notificationsPagination.hidden = true;
    notificationsLoadMore.disabled = false;
    notificationsLoadMore.textContent = 'Carica altre';
    notificationsPaginationStatus.textContent = '';
  }

  function clearNotificationPreferencesStatus() {
    notificationPreferencesStatus.textContent = '';
    notificationPreferencesStatus.classList.remove('is-error');
  }

  function resetNotificationPreferencesState() {
    state.notificationPreferencesGeneration += 1;
    state.notificationPreferencesSaving = false;
    notificationPreferencesLoading.hidden = true;
    notificationPreferencesError.hidden = true;
    notificationPreferencesForm.hidden = true;
    notificationPreferencesErrorMessage.textContent = '';
    notificationPreferencesSave.disabled = false;
    notificationPreferencesSave.textContent = 'Salva preferenze';
    clearNotificationPreferencesStatus();
    for (const input of [
      preferenceInApp,
      preferencePublication,
      preferenceVisitFeedback,
      preferenceDocument,
      preferenceRequestUpdate,
    ]) {
      input.checked = false;
    }
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
    // LMC6_START
    resetHomesState();
    emailLoginMessage.textContent = '';
    // LMC6_END
    resetNotificationsState();
    resetNotificationPreferencesState();
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


  function notificationsErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare le notifiche. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare le notifiche con i dati disponibili.';
    }
    return error.message;
  }

  function notificationReadErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile aggiornare la notifica. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile aggiornare la notifica.';
    }
    return error.message;
  }

  function notificationPreferencesErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare le preferenze. Riprova tra poco.';
    }
    if (error.status === 404) {
      return 'Contenuto non disponibile o accesso non più valido.';
    }
    if (error.status === 422) {
      return 'Impossibile caricare le preferenze con i dati disponibili.';
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


  function showNotificationsState(name, message = '') {
    notificationsLoading.hidden = name !== 'loading';
    notificationsEmpty.hidden = name !== 'empty';
    notificationsError.hidden = name !== 'error';
    notificationsContent.hidden = name !== 'content';
    if (name === 'error') {
      notificationsErrorMessage.textContent = message || 'Riprova tra poco.';
    }
    if (name === 'empty') {
      notificationsEmptyMessage.textContent = state.notificationUnreadOnly
        ? 'Non ci sono notifiche non lette.'
        : 'Non ci sono notifiche da mostrare.';
    }
  }

  function notificationTypeLabel(type) {
    return NOTIFICATION_TYPE_LABELS[type] || 'Notifica';
  }

  function notificationId(item) {
    const id = Number(item && item.id);
    return Number.isInteger(id) && id > 0 ? id : null;
  }

  function renderNotificationCard(item) {
    const id = notificationId(item);
    const card = document.createElement('article');
    card.className = 'notification-card';
    card.setAttribute('role', 'listitem');
    if (id !== null) card.dataset.notificationId = String(id);
    card.classList.toggle('is-unread', !item.read_at);

    const top = document.createElement('div');
    top.className = 'notification-card-topline';
    const type = document.createElement('span');
    type.className = 'notification-type-label';
    type.textContent = notificationTypeLabel(item.type);
    const readState = document.createElement('span');
    readState.className = 'notification-read-badge';
    readState.textContent = item.read_at ? 'Letta' : 'Non letta';
    top.append(type, readState);

    const title = document.createElement('h3');
    title.className = 'notification-title';
    title.textContent = typeof item.title === 'string' && item.title.trim() ? item.title : 'Notifica';
    const body = document.createElement('p');
    body.className = 'notification-body';
    body.textContent = typeof item.body === 'string' ? item.body : '';
    const date = document.createElement('p');
    date.className = 'notification-date';
    date.textContent = item.created_at ? formatPublishedAt(item.created_at) : 'Data non disponibile';

    const actions = document.createElement('div');
    actions.className = 'notification-actions';
    const actionStatus = document.createElement('p');
    actionStatus.className = 'status-message notification-action-status';
    actionStatus.setAttribute('role', 'status');
    actionStatus.setAttribute('aria-live', 'polite');
    if (id !== null) actionStatus.dataset.notificationStatusId = String(id);

    const button = document.createElement('button');
    button.className = 'secondary-button notification-read-button';
    button.type = 'button';
    button.textContent = item.read_at ? 'Letta' : 'Segna come letta';
    button.disabled = Boolean(item.read_at) || id === null;
    if (id !== null) button.dataset.notificationId = String(id);
    button.addEventListener('click', () => {
      if (id !== null) void markNotificationRead(id);
    });
    actions.append(button, actionStatus);
    card.append(top, title, body, date, actions);
    return card;
  }

  function renderNotifications() {
    notificationsList.replaceChildren();
    for (const item of state.notificationItems) {
      if (item && typeof item === 'object') {
        notificationsList.append(renderNotificationCard(item));
      }
    }
  }

  function notificationPageUrl(offset) {
    const params = new URLSearchParams({
      limit: String(NOTIFICATIONS_LIMIT),
      offset: String(offset),
      unread_only: state.notificationUnreadOnly ? 'true' : 'false',
    });
    return `/notifications?${params.toString()}`;
  }

  function updateNotificationPagination() {
    notificationsPagination.hidden = !state.notificationHasMore;
    notificationsLoadMore.disabled = state.notificationLoadInFlight || !state.notificationHasMore;
    notificationsLoadMore.textContent = state.notificationLoadInFlight ? 'Caricamento…' : 'Carica altre';
    notificationsPaginationStatus.textContent = state.notificationHasMore
      ? `${state.notificationItems.length} notifiche caricate.`
      : '';
  }

  async function confirmNotificationSessionAfterNotFound(generation) {
    try {
      const session = await loadSession();
      if (generation !== state.notificationGeneration || !state.session) return false;
      state.session = session;
      return true;
    } catch (error) {
      if (generation === state.notificationGeneration && state.session && isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
      }
      return false;
    }
  }

  async function loadNotifications({ reset = false } = {}) {
    if (!state.session) return;

    if (reset) {
      resetNotificationsState({ preserveFilter: true });
    }
    if (state.notificationLoadInFlight || (!reset && !state.notificationHasMore && state.notificationOffset > 0)) {
      return;
    }

    const generation = state.notificationGeneration;
    const filterAtStart = state.notificationUnreadOnly;
    const offsetAtStart = state.notificationOffset;
    state.notificationLoadInFlight = true;
    if (offsetAtStart === 0) showNotificationsState('loading');
    updateNotificationPagination();

    let payload;
    try {
      payload = await apiRequest(notificationPageUrl(offsetAtStart));
    } catch (error) {
      if (
        generation !== state.notificationGeneration
        || filterAtStart !== state.notificationUnreadOnly
        || !state.session
      ) return;
      state.notificationLoadInFlight = false;
      updateNotificationPagination();
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmNotificationSessionAfterNotFound(generation);
        if (sessionValid && generation === state.notificationGeneration && state.session) {
          showNotificationsState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }
      showNotificationsState('error', notificationsErrorText(error));
      return;
    }

    if (
      generation !== state.notificationGeneration
      || filterAtStart !== state.notificationUnreadOnly
      || !state.session
    ) return;

    state.notificationLoadInFlight = false;
    const items = payload && Array.isArray(payload.items)
      ? payload.items.filter((item) => item && typeof item === 'object')
      : [];
    state.notificationItems = offsetAtStart === 0 ? items : state.notificationItems.concat(items);
    state.notificationHasMore = payload && payload.has_more === true;
    const payloadLimit = payload && Number.isInteger(payload.limit) && payload.limit > 0
      ? payload.limit
      : NOTIFICATIONS_LIMIT;
    const payloadOffset = payload && Number.isInteger(payload.offset) && payload.offset >= 0
      ? payload.offset
      : offsetAtStart;
    state.notificationOffset = payloadOffset + payloadLimit;

    if (state.notificationItems.length === 0) {
      showNotificationsState('empty');
      updateNotificationPagination();
      return;
    }
    renderNotifications();
    showNotificationsState('content');
    updateNotificationPagination();
  }

  function notificationCardById(id) {
    return notificationsList.children.find
      ? notificationsList.children.find((card) => Number(card.dataset.notificationId) === id)
      : Array.from(notificationsList.children).find((card) => Number(card.dataset.notificationId) === id);
  }

  function updateNotificationItemFromRead(id, payload) {
    const index = state.notificationItems.findIndex((item) => notificationId(item) === id);
    if (index < 0) return;
    const card = notificationCardById(id);
    if (state.notificationUnreadOnly) {
      state.notificationItems.splice(index, 1);
      state.notificationOffset = Math.max(0, state.notificationOffset - 1);
      if (card) {
        const remaining = Array.from(notificationsList.children).filter((item) => item !== card);
        notificationsList.replaceChildren(...remaining);
      }
    } else {
      state.notificationItems[index] = {
        ...state.notificationItems[index],
        ...(payload && typeof payload === 'object' ? payload : {}),
        read_at: payload && payload.read_at ? payload.read_at : new Date().toISOString(),
      };
      if (card) {
        card.classList.remove('is-unread');
        const readState = card.children[0]?.children?.[1];
        const button = card.children[4]?.children?.[0];
        const status = card.children[4]?.children?.[1];
        if (readState) readState.textContent = 'Letta';
        if (button) {
          button.disabled = true;
          button.textContent = 'Letta';
        }
        if (status) {
          status.classList.remove('is-error');
          status.textContent = 'Notifica segnata come letta.';
        }
      }
    }
    if (state.notificationItems.length === 0) {
      showNotificationsState('empty');
      notificationsList.replaceChildren();
    } else {
      showNotificationsState('content');
    }
    updateNotificationPagination();
  }

  async function markNotificationRead(id) {
    if (!state.session || state.notificationReadInFlight.has(id)) return;
    const item = state.notificationItems.find((entry) => notificationId(entry) === id);
    if (!item || item.read_at) return;

    const generation = state.notificationGeneration;
    state.notificationReadInFlight.add(id);
    const card = notificationCardById(id);
    const button = card && card.children.length ? card.children[4]?.children?.[0] : null;
    const status = card && card.children.length ? card.children[4]?.children?.[1] : null;
    if (button) {
      button.disabled = true;
      button.textContent = 'Aggiornamento…';
    }
    if (status) status.textContent = 'Aggiornamento della notifica in corso…';

    let payload;
    try {
      payload = await apiRequest(`/notifications/${encodeURIComponent(String(id))}/read`, { method: 'POST' });
    } catch (error) {
      if (generation !== state.notificationGeneration || !state.session) return;
      state.notificationReadInFlight.delete(id);
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmNotificationSessionAfterNotFound(generation);
        if (!sessionValid || generation !== state.notificationGeneration || !state.session) return;
      }
      if (button) {
        button.disabled = false;
        button.textContent = 'Segna come letta';
      }
      if (status) {
        status.classList.add('is-error');
        status.textContent = notificationReadErrorText(error);
      }
      return;
    }

    if (generation !== state.notificationGeneration || !state.session) return;
    state.notificationReadInFlight.delete(id);
    updateNotificationItemFromRead(id, payload);
  }

  function showNotificationPreferencesState(name, message = '') {
    notificationPreferencesLoading.hidden = name !== 'loading';
    notificationPreferencesError.hidden = name !== 'error';
    notificationPreferencesForm.hidden = name !== 'content';
    if (name === 'error') {
      notificationPreferencesErrorMessage.textContent = message || 'Riprova tra poco.';
    }
  }

  function applyNotificationPreferences(payload) {
    const values = payload && typeof payload === 'object' ? payload : {};
    preferenceInApp.checked = values.in_app_enabled === true;
    preferencePublication.checked = values.publication_enabled === true;
    preferenceVisitFeedback.checked = values.visit_feedback_enabled === true;
    preferenceDocument.checked = values.document_enabled === true;
    preferenceRequestUpdate.checked = values.request_update_enabled === true;
  }

  function notificationPreferencesPayload() {
    return {
      in_app_enabled: Boolean(preferenceInApp.checked),
      publication_enabled: Boolean(preferencePublication.checked),
      visit_feedback_enabled: Boolean(preferenceVisitFeedback.checked),
      document_enabled: Boolean(preferenceDocument.checked),
      request_update_enabled: Boolean(preferenceRequestUpdate.checked),
    };
  }

  async function confirmNotificationPreferencesSessionAfterNotFound(generation) {
    try {
      const session = await loadSession();
      if (generation !== state.notificationPreferencesGeneration || !state.session) return false;
      state.session = session;
      return true;
    } catch (error) {
      if (generation === state.notificationPreferencesGeneration && state.session && isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
      }
      return false;
    }
  }

  async function loadNotificationPreferences() {
    if (!state.session) return;
    const generation = ++state.notificationPreferencesGeneration;
    state.notificationPreferencesSaving = false;
    notificationPreferencesSave.disabled = false;
    notificationPreferencesSave.textContent = 'Salva preferenze';
    clearNotificationPreferencesStatus();
    showNotificationPreferencesState('loading');

    let payload;
    try {
      payload = await apiRequest('/notification-preferences');
    } catch (error) {
      if (generation !== state.notificationPreferencesGeneration || !state.session) return;
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmNotificationPreferencesSessionAfterNotFound(generation);
        if (sessionValid && generation === state.notificationPreferencesGeneration && state.session) {
          showNotificationPreferencesState('error', 'Contenuto non disponibile o accesso non più valido.');
        }
        return;
      }
      showNotificationPreferencesState('error', notificationPreferencesErrorText(error));
      return;
    }

    if (generation !== state.notificationPreferencesGeneration || !state.session) return;
    applyNotificationPreferences(payload);
    showNotificationPreferencesState('content');
  }

  async function saveNotificationPreferences() {
    if (!state.session || state.notificationPreferencesSaving) return;
    const generation = state.notificationPreferencesGeneration;
    const preferencesBody = notificationPreferencesPayload();
    state.notificationPreferencesSaving = true;
    notificationPreferencesSave.disabled = true;
    notificationPreferencesSave.textContent = 'Salvataggio…';
    notificationPreferencesStatus.classList.remove('is-error');
    notificationPreferencesStatus.textContent = 'Salvataggio delle preferenze in corso…';

    let responsePayload;
    try {
      responsePayload = await apiRequest('/notification-preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(preferencesBody),
      });
    } catch (error) {
      if (generation !== state.notificationPreferencesGeneration || !state.session) return;
      state.notificationPreferencesSaving = false;
      notificationPreferencesSave.disabled = false;
      notificationPreferencesSave.textContent = 'Salva preferenze';
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        const sessionValid = await confirmNotificationPreferencesSessionAfterNotFound(generation);
        if (!sessionValid || generation !== state.notificationPreferencesGeneration || !state.session) return;
      }
      notificationPreferencesStatus.classList.add('is-error');
      notificationPreferencesStatus.textContent = notificationPreferencesErrorText(error);
      return;
    }

    if (generation !== state.notificationPreferencesGeneration || !state.session) return;
    state.notificationPreferencesSaving = false;
    notificationPreferencesSave.disabled = false;
    notificationPreferencesSave.textContent = 'Salva preferenze';
    applyNotificationPreferences(responsePayload);
    notificationPreferencesStatus.classList.remove('is-error');
    notificationPreferencesStatus.textContent = 'Preferenze salvate.';
  }

  function startP68DataLoads() {
    if (!state.session) return;
    state.notificationUnreadOnly = notificationsUnreadOnly.checked === true;
    void loadNotifications({ reset: true });
    void loadNotificationPreferences();
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

  // LMC6_START - il rendering di "La Mia Casa".
  //
  // Una regola sola, e vale per ogni funzione qui sotto: si scrive
  // `textContent`, mai HTML. I testi arrivano dall'API e dal view model, e
  // il modo sicuro di stamparli non e' ripulirli ma non interpretarli.

  function homeText(tag, className, value) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    element.textContent = value === null || value === undefined ? '' : String(value);
    return element;
  }

  function appendPair(list, label, value) {
    if (value === null || value === undefined || value === '') {
      return;
    }
    list.append(homeText('dt', null, label), homeText('dd', null, value));
  }

  function showHomesState(name, message = '') {
    homesLoading.hidden = name !== 'loading';
    homesError.hidden = name !== 'error';
    homesContent.hidden = name !== 'content';
    homesErrorMessage.textContent = name === 'error' ? message : '';
  }

  function showHomeDetailState(name, message = '') {
    homeDetailLoading.hidden = name !== 'loading';
    homeDetailEmpty.hidden = name !== 'empty';
    homeDetailError.hidden = name !== 'error';
    homeDetailContent.hidden = name !== 'content';
    homeDetailErrorMessage.textContent = name === 'error'
      ? (message || 'Casa non disponibile o accesso non più valido.')
      : '';
  }

  function resetHomesState() {
    state.homesGeneration += 1;
    state.homeDetailGeneration += 1;
    state.homes = [];
    state.selectedStimaId = null;
    homeCount.textContent = '';
    homeList.replaceChildren();
    homesSection.hidden = true;
    showHomesState('idle');
    showHomeDetailState('empty');
    // LMC12_START
    resetHomeNotificationsState();
    // LMC12_END
  }

  function setSelectedHomeCardState() {
    Array.from(homeList.children).forEach((card) => {
      const selected = Number(card.dataset.stimaId) === state.selectedStimaId;
      card.classList.toggle('is-selected', selected);
      card.setAttribute('aria-current', selected ? 'true' : 'false');
    });
  }

  function createHomeCard(home) {
    const view = VM.homeCard(home);
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'home-card';
    card.setAttribute('role', 'listitem');
    card.dataset.stimaId = String(view.stimaId);

    card.append(homeText('span', 'home-card-title', view.title));
    if (view.subtitle) {
      card.append(homeText('span', 'home-card-subtitle', view.subtitle));
    }
    if (view.initialValue) {
      const riga = document.createElement('span');
      riga.className = 'home-card-value';
      riga.append(homeText('small', null, view.initialLabel),
                  homeText('strong', null, view.initialValue));
      card.append(riga);
    }
    if (view.statusLabel) {
      card.append(homeText('span', 'home-card-status', view.statusLabel));
    }
    card.append(homeText('span', 'home-card-cta', 'Apri'));
    card.addEventListener('click', () => {
      void selectHome(view.stimaId);
    });
    return card;
  }

  function renderHomeList(items) {
    homeList.replaceChildren(...items.map(createHomeCard));
    setSelectedHomeCardState();
  }

  function renderHomeValue(value) {
    homeValueList.replaceChildren();
    appendPair(homeValueList, value.initialLabel, value.initialValue);
    if (value.hasCurrent) {
      appendPair(homeValueList, value.currentLabel, value.currentValue);
      appendPair(homeValueList, value.computedAtLabel, value.computedAt);
    }
    // Nessun valore monitorato: si dice che lo storico si sta formando, non
    // si ripiega sul valore iniziale spacciandolo per quello di oggi.
    homeValueNote.textContent = value.buildingHistory ? value.buildingMessage : '';
    homeValueNote.hidden = !value.buildingHistory;
  }

  function renderHistoryChart(points) {
    homeHistoryChart.replaceChildren();
    if (points.length < 2) {
      // Un punto solo non e' un andamento, e unirlo a qualcosa
      // significherebbe inventare il secondo.
      homeHistoryChart.hidden = points.length === 0;
      if (points.length === 1) {
        homeHistoryChart.hidden = false;
        homeHistoryChart.append(
          homeText('p', 'home-chart-single',
                   `${points[0].dateLabel} · ${points[0].valueText}`));
      }
      return;
    }
    homeHistoryChart.hidden = false;

    const width = 320;
    const height = 120;
    const pad = 10;
    const valori = points.map((punto) => punto.value);
    const minimo = Math.min(...valori);
    const massimo = Math.max(...valori);
    const span = massimo - minimo || 1;
    const passo = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
    const coordinate = points.map((punto, indice) => ({
      x: pad + passo * indice,
      y: height - pad - ((punto.value - minimo) / span) * (height - pad * 2),
    }));

    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label',
      `Andamento del valore su ${points.length} rilevazioni`);

    const linea = document.createElementNS(SVG_NS, 'polyline');
    linea.setAttribute('class', 'home-chart-line');
    linea.setAttribute('points',
      coordinate.map((c) => `${c.x.toFixed(2)},${c.y.toFixed(2)}`).join(' '));
    svg.append(linea);

    coordinate.forEach((c, indice) => {
      const punto = document.createElementNS(SVG_NS, 'circle');
      punto.setAttribute('class', 'home-chart-dot');
      punto.setAttribute('cx', c.x.toFixed(2));
      punto.setAttribute('cy', c.y.toFixed(2));
      punto.setAttribute('r', '3.5');
      const titolo = document.createElementNS(SVG_NS, 'title');
      titolo.textContent = `${points[indice].dateLabel} · ${points[indice].valueText}`;
      punto.append(titolo);
      svg.append(punto);
    });

    homeHistoryChart.append(svg);
  }

  function renderHistoryChanges(changes) {
    homeHistoryChanges.replaceChildren();
    changes.forEach((change) => {
      const riga = document.createElement('div');
      riga.className = 'home-change';
      riga.append(homeText('span', 'home-change-label', change.label));
      if (change.percentText) {
        riga.append(homeText('strong', 'home-change-percent', change.percentText));
      }
      const estremi = [change.fromValue, change.toValue].filter(Boolean).join(' → ');
      if (estremi) {
        riga.append(homeText('span', 'home-change-values', estremi));
      }
      if (change.note) {
        // Metodo cambiato: la frase PRENDE IL POSTO della percentuale.
        riga.append(homeText('span', 'home-change-note', change.note));
      }
      homeHistoryChanges.append(riga);
    });
  }

  function renderHomeHistory(history) {
    homeHistoryMessage.textContent = history.available ? '' : history.message;
    homeHistoryMessage.hidden = history.available;
    homeHistoryChart.hidden = true;
    homeHistoryRange.hidden = true;
    homeHistoryChanges.replaceChildren();
    // LMC7: contenuto costruito subito, mostrato solo su richiesta.
    homeHistoryChanges.hidden = true;
    setSectionOpen(homeHistoryToggle, false);
    homeHistoryToggle.hidden = !history.available;
    if (!history.available) {
      return;
    }
    renderHistoryChart(history.points);
    if (history.points.length >= 2) {
      const primo = history.points[0];
      const ultimo = history.points[history.points.length - 1];
      homeHistoryRange.textContent =
        `${primo.dateLabel} · ${primo.valueText} → ${ultimo.dateLabel} · ${ultimo.valueText}`;
      homeHistoryRange.hidden = false;
    }
    renderHistoryChanges(history.changes);
    // Quali parti esistono davvero: `revealHistory` non deve mostrare un
    // contenitore vuoto (un solo snapshot non ha una fascia da mostrare).
    state.historyChartEmpty = homeHistoryChart.children.length === 0;
    state.historyRangeEmpty = homeHistoryRange.textContent === '';
    homeHistoryChart.hidden = true;
    homeHistoryRange.hidden = true;
  }

  // LMC7_START - LE DUE SEZIONI CHE SI APRONO CON UN GESTO.
  //
  // Prima stavano aperte, e il caricamento della pagina sarebbe bastato a
  // dire "ha guardato l'andamento": un segnale che descrive qualcosa che non
  // e' successo. Ora c'e' un pulsante, e solo premerlo racconta qualcosa.
  //
  // Il CONTENUTO non cambia di una virgola rispetto a LMC-6: cambia quando
  // compare. E il proprietario non vede niente del radar - nessun livello,
  // nessuna motivazione, nessun messaggio: il pulsante dice "Vedi andamento"
  // e fa esattamente quello.

  function setSectionOpen(toggle, open) {
    toggle.hidden = open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function revealHistory({ track = true } = {}) {
    setSectionOpen(homeHistoryToggle, true);
    homeHistoryChart.hidden = state.historyChartEmpty === true;
    homeHistoryRange.hidden = state.historyRangeEmpty === true;
    homeHistoryChanges.hidden = false;
    if (track) {
      void trackAction('value_history_viewed');
    }
  }

  function revealDemand({ track = true } = {}) {
    setSectionOpen(homeDemandToggle, true);
    homeDemandContent.hidden = false;
    if (track) {
      void trackAction('buyer_demand_viewed');
    }
  }

  async function trackAction(action) {
    const stimaId = state.selectedStimaId;
    if (stimaId === null || stimaId === undefined) {
      return;
    }
    const memoria = `${stimaId}:${action}`;
    if (state.openedSections.has(memoria)) {
      return;
    }
    state.openedSections.add(memoria);
    try {
      await apiRequest(`/homes/${encodeURIComponent(stimaId)}/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
    } catch (_error) {
      // Il tracciamento non e' un servizio per il proprietario: se non passa,
      // la sezione resta aperta e lui non deve sapere che esisteva.
    }
  }
  // LMC7_END

  function renderHomeDemand(demand) {
    homeDemandUnavailable.textContent = demand.available ? '' : demand.message;
    homeDemandUnavailable.hidden = demand.available;
    // LMC7: il contenuto c'e', ma si apre con un gesto.
    homeDemandContent.hidden = true;
    setSectionOpen(homeDemandToggle, false);
    homeDemandToggle.hidden = !demand.available;
    homeDemandLabel.textContent = '';
    homeDemandMessage.textContent = '';
    homeDemandCounts.replaceChildren();
    homeDemandDisclaimer.textContent = '';
    if (!demand.available) {
      return;
    }
    homeDemandContent.dataset.status = demand.status || '';
    homeDemandLabel.textContent = demand.label || '';
    homeDemandMessage.textContent = demand.message || '';
    appendPair(homeDemandCounts, demand.compatibleLabel, demand.compatibleText);
    appendPair(homeDemandCounts, demand.recentLabel, demand.recentText);
    appendPair(homeDemandCounts, demand.updatedAtLabel, demand.updatedAt);
    homeDemandDisclaimer.textContent = demand.disclaimer || '';
  }

  function renderChips(container, items, variante) {
    container.replaceChildren();
    items.forEach((campo) => {
      container.append(homeText('span', `home-chip ${variante}`, campo));
    });
  }

  function renderHomeProfile(profile) {
    homeProfilePercent.textContent = profile.percentText
      ? `Completezza ${profile.percentText}`
      : '';
    homeProfileBarFill.style.width = profile.percentText || '0%';
    renderChips(homeProfileKnown, profile.known, 'is-known');
    renderChips(homeProfileMissing, profile.missing, 'is-missing');
    homeProfileNote.textContent = profile.note;
    // LMC10: il form vive o non vive secondo `editable`, che e' la
    // capability dichiarata dal backend.
    renderHomeProfileUpdate(profile);
  }

  // LMC10_START - AGGIORNA I DATI DELLA CASA.
  //
  // Il form mostra SOLO i campi che il backend dichiara modificabili
  // (`profile.editable_fields`), precompilati con il profilo effettivo -
  // cioe' con gli stessi numeri che il proprietario ha appena letto sopra.
  // Nessuna whitelist scritta qui: se il server ne togliesse uno, la casella
  // sparirebbe da sola invece di restare a proporre una modifica che
  // verrebbe rifiutata.
  //
  // TRE COSE CHE QUESTO CODICE NON FA.
  //
  // Non promette precisione. Il messaggio di successo dice che il valore e'
  // stato ricalcolato SOLO se il server dice che un nuovo calcolo e' nato
  // davvero; in tutti gli altri casi dice che i dati sono aggiornati e tace
  // sul valore. Mai "ora la stima e' piu' precisa": nessuno lo sa.
  //
  // Non sovrascrive. La versione su cui il form e' stato aperto torna
  // indietro come `expected_version`, e un 409 diventa un invito a
  // ricaricare, non un secondo tentativo automatico che cancellerebbe il
  // lavoro dell'altra sessione.
  //
  // Non perde quello che la persona ha scritto. Su errore il form resta
  // aperto con i suoi valori: chiuderlo significherebbe far ribattere tutto.

  const PROFILE_FIELD_SPECS = {
    mq: { label: 'Superficie (mq)', type: 'number', min: 1 },
    locali: { label: 'Locali', type: 'number', min: 1 },
    bagni: { label: 'Bagni', type: 'number', min: 0 },
    piano: { label: 'Piano', type: 'text', hint: 'terra, ultimo o il numero del piano' },
    ascensore: { label: 'Ascensore', type: 'checkbox' },
    anno: { label: 'Anno di costruzione', type: 'number', min: 1500 },
    stato: {
      label: 'Stato',
      type: 'select',
      options: ['nuovo', 'ristrutturato', 'buono', 'scarso', 'grezzo'],
    },
    mqgiardino: { label: 'Giardino (mq)', type: 'number', min: 0 },
    mqgarage: { label: 'Garage (mq)', type: 'number', min: 0 },
    mqcantina: { label: 'Cantina (mq)', type: 'number', min: 0 },
    mqpostoauto: { label: 'Posto auto (mq)', type: 'number', min: 0 },
    mqtaverna: { label: 'Taverna (mq)', type: 'number', min: 0 },
    mqsoffitta: { label: 'Soffitta (mq)', type: 'number', min: 0 },
    mqterrazzo: { label: 'Terrazzo (mq)', type: 'number', min: 0 },
    numbalconi: { label: 'Balconi', type: 'number', min: 0 },
  };

  const PROFILE_PERTINENZE = [
    'garage', 'posto auto', 'cantina', 'soffitta', 'taverna',
    'balconi', 'terrazzo', 'giardino', 'piscina', 'posto moto', 'posto bici',
  ];

  const PROFILE_TRUTHY = ['si', 'sì', 'true', '1', 'yes', 'y'];

  const PROFILE_SAVED_MESSAGE = 'Dati della casa aggiornati.';
  const PROFILE_SAVED_RECALCULATED_MESSAGE =
    'Dati aggiornati e valore ricalcolato.';
  const PROFILE_UNCHANGED_MESSAGE = 'Non ci sono modifiche da salvare.';
  const PROFILE_CONFLICT_MESSAGE =
    'I dati della casa sono stati aggiornati da un’altra sessione. Ricarica i dati e riprova.';
  const PROFILE_INVALID_MESSAGE = 'Controlla i dati inseriti e riprova.';
  const PROFILE_ERROR_MESSAGE =
    'Non siamo riusciti a salvare le modifiche. Riprova.';
  const PROFILE_SAVING_MESSAGE = 'Salvataggio…';

  function profileFieldId(campo) {
    return `home-profile-field-${campo}`;
  }

  function profileIsTruthy(valore) {
    if (typeof valore === 'boolean') {
      return valore;
    }
    return PROFILE_TRUTHY.indexOf(String(valore === null || valore === undefined
      ? '' : valore).trim().toLowerCase()) !== -1;
  }

  function profileTokens(valore) {
    const testo = String(valore === null || valore === undefined ? '' : valore)
      .toLowerCase().replace(/[;|/\\]/g, ',');
    const pezzi = testo.split(',').map((p) => p.trim()).filter((p) => p !== '');
    return PROFILE_PERTINENZE.filter((token) => pezzi.indexOf(token) !== -1
      || testo.indexOf(token) !== -1);
  }

  function profileAppendField(campo, valore) {
    const spec = PROFILE_FIELD_SPECS[campo];
    if (!spec) {
      return;
    }
    const riga = document.createElement('div');
    riga.className = 'home-profile-field';

    const etichetta = document.createElement('label');
    etichetta.setAttribute('for', profileFieldId(campo));
    etichetta.textContent = spec.label;

    let campoInput;
    if (spec.type === 'select') {
      campoInput = document.createElement('select');
      // Il valore corrente entra come opzione anche se non e' fra quelle
      // previste: una casa nata con uno stato che l'elenco non contiene deve
      // poter restare com'e' finche' il proprietario non ne sceglie un altro.
      const correnti = spec.options.slice();
      const attuale = valore === null || valore === undefined ? '' : String(valore).trim();
      if (attuale !== '' && correnti.indexOf(attuale) === -1) {
        correnti.unshift(attuale);
      }
      correnti.forEach((opzione) => {
        const elemento = document.createElement('option');
        elemento.value = opzione;
        elemento.textContent = opzione;
        if (opzione === attuale) {
          elemento.selected = true;
        }
        campoInput.append(elemento);
      });
    } else {
      campoInput = document.createElement('input');
      campoInput.type = spec.type;
      if (spec.type === 'checkbox') {
        campoInput.checked = profileIsTruthy(valore);
      } else {
        campoInput.value = valore === null || valore === undefined ? '' : String(valore);
      }
      if (spec.type === 'number') {
        campoInput.inputMode = 'numeric';
        if (typeof spec.min === 'number') {
          campoInput.min = String(spec.min);
        }
      }
    }
    campoInput.id = profileFieldId(campo);
    campoInput.name = campo;
    state.profileInputs[campo] = campoInput;

    riga.append(etichetta, campoInput);
    if (spec.hint) {
      riga.append(homeText('p', 'home-profile-hint', spec.hint));
    }
    homeProfileFields.append(riga);
  }

  function profileRenderPertinenze(valore) {
    homeProfilePertinenzeList.replaceChildren();
    const attive = profileTokens(valore);
    PROFILE_PERTINENZE.forEach((token) => {
      const etichetta = document.createElement('label');
      etichetta.className = 'home-chip home-profile-chip';
      const casella = document.createElement('input');
      casella.type = 'checkbox';
      casella.value = token;
      casella.checked = attive.indexOf(token) !== -1;
      casella.setAttribute('data-pertinenza', token);
      etichetta.append(casella, document.createTextNode(` ${token}`));
      homeProfilePertinenzeList.append(etichetta);
    });
  }

  function profileBuildForm() {
    homeProfileFields.replaceChildren();
    state.profileInputs = {};
    state.profileEditableFields.forEach((campo) => {
      if (campo === 'pertinenze' || campo === 'altrodescrizione') {
        return;
      }
      profileAppendField(campo, state.profileValues[campo]);
    });
    profileRenderPertinenze(state.profileValues.pertinenze);
    const altro = state.profileValues.altrodescrizione;
    homeProfileAltro.value = altro === null || altro === undefined ? '' : String(altro);
  }

  function setProfileFormOpen(aperto) {
    homeProfileForm.hidden = !aperto;
    homeProfileEdit.hidden = aperto || !state.profileEditable;
  }

  function setProfileStatus(messaggio) {
    homeProfileStatus.textContent = messaggio || '';
    homeProfileStatus.hidden = !messaggio;
  }

  function profileCollectPatch() {
    const patch = {};
    state.profileEditableFields.forEach((campo) => {
      if (campo === 'pertinenze' || campo === 'altrodescrizione') {
        return;
      }
      const spec = PROFILE_FIELD_SPECS[campo];
      const elemento = state.profileInputs[campo];
      if (!spec || !elemento) {
        return;
      }
      if (spec.type === 'checkbox') {
        patch[campo] = elemento.checked === true;
        return;
      }
      const grezzo = String(elemento.value === null || elemento.value === undefined
        ? '' : elemento.value).trim();
      if (grezzo === '') {
        // Una casella lasciata vuota non e' "cancella questo campo": in
        // LMC-10 quel gesto non esiste, quindi il campo semplicemente non
        // parte.
        return;
      }
      if (spec.type === 'number') {
        const numero = Number(grezzo);
        if (!Number.isFinite(numero) || !Number.isInteger(numero)) {
          return;
        }
        patch[campo] = numero;
        return;
      }
      patch[campo] = grezzo;
    });

    if (state.profileEditableFields.indexOf('pertinenze') !== -1) {
      patch.pertinenze = Array.prototype.slice
        .call(homeProfilePertinenzeList.querySelectorAll('input[type="checkbox"]'))
        .filter((casella) => casella.checked)
        .map((casella) => casella.value);
    }
    if (state.profileEditableFields.indexOf('altrodescrizione') !== -1) {
      const testo = String(homeProfileAltro.value || '').trim();
      if (testo !== '') {
        patch.altrodescrizione = testo;
      }
    }
    return patch;
  }

  async function saveHomeProfile() {
    const stimaId = state.selectedStimaId;
    if (stimaId === null || stimaId === undefined || state.profileSaveInFlight) {
      return;
    }
    const patch = profileCollectPatch();
    if (Object.keys(patch).length === 0) {
      setProfileStatus(PROFILE_UNCHANGED_MESSAGE);
      return;
    }

    state.profileSaveInFlight = true;
    homeProfileSave.disabled = true;
    setProfileStatus(PROFILE_SAVING_MESSAGE);

    let esito;
    try {
      esito = await apiRequest(`/homes/${encodeURIComponent(stimaId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({ expected_version: state.profileVersion },
                                           patch)),
      });
    } catch (error) {
      const stato = error instanceof PortalRequestError ? error.status : 0;
      if (stato === 409) {
        setProfileStatus(PROFILE_CONFLICT_MESSAGE);
      } else if (stato === 422) {
        setProfileStatus(PROFILE_INVALID_MESSAGE);
      } else {
        setProfileStatus(PROFILE_ERROR_MESSAGE);
      }
      // Il form resta aperto con i valori digitati: riprovare non deve
      // costare la ribattitura.
      return;
    } finally {
      state.profileSaveInFlight = false;
      homeProfileSave.disabled = false;
    }

    if (esito && esito.status === 'unchanged') {
      setProfileStatus(PROFILE_UNCHANGED_MESSAGE);
      return;
    }
    if (esito && esito.home) {
      // La casa aggiornata arriva nella risposta: si ridisegna con quella,
      // senza una seconda GET e senza una finestra in cui la scheda mostra
      // ancora i numeri di prima.
      //
      // PRIMA del messaggio, non dopo: ridisegnare rifa' anche questo
      // blocco e azzera lo stato, quindi l'esito va scritto quando la
      // scheda e' gia' quella nuova. (Scritto al contrario, il
      // proprietario non vedeva alcuna conferma - un test se n'e'
      // accorto.)
      renderHomeDetail(esito.home);
    }
    setProfileFormOpen(false);
    setProfileStatus(esito && esito.value_recalculated === true
      ? PROFILE_SAVED_RECALCULATED_MESSAGE
      : PROFILE_SAVED_MESSAGE);
  }

  function renderHomeProfileUpdate(profile) {
    state.profileEditable = profile.editable === true;
    state.profileVersion = typeof profile.version === 'number' ? profile.version : 0;
    state.profileEditableFields = Array.isArray(profile.editableFields)
      ? profile.editableFields.slice() : [];
    state.profileValues = profile.values || {};

    const aggiornato = profile.updatedAt;
    homeProfileUpdated.textContent = aggiornato
      ? `Aggiornato da te il ${aggiornato}` : '';
    homeProfileUpdated.hidden = !aggiornato;

    if (!state.profileEditable || state.profileEditableFields.length === 0) {
      homeProfileEdit.hidden = true;
      homeProfileForm.hidden = true;
      setProfileStatus('');
      return;
    }
    profileBuildForm();
    setProfileFormOpen(false);
    setProfileStatus('');
  }
  // LMC10_END

  function renderHomeDetail(payload) {
    const view = VM.homeDetail(payload);
    homeDetailAddress.textContent = view.header.address || '';
    homeDetailSummary.textContent = view.header.summary || '';
    renderHomeValue(view.value);
    renderHomeHistory(view.history);
    renderHomeDemand(view.demand);
    renderHomeProfile(view.profile);
    // Nessuna sezione comparabili: LMC-5 ha chiuso la fonte dati.
  }

  function homeErrorText(error) {
    if (!(error instanceof PortalRequestError)) {
      return 'Impossibile caricare la casa. Riprova tra poco.';
    }
    return error.message;
  }

  // LMC9_START - LA RICHIESTA DI VERIFICA GRATUITA.
  //
  // E' la prima cosa in questo portale che non osserva ma CHIEDE, e cambia
  // due regole rispetto a LMC-7.
  //
  // La prima: si conferma prima di inviare. Un tocco accidentale mentre si
  // scorre non deve diventare "questa persona vuole essere richiamata" - un
  // segnale commerciale forte nato da uno scroll e' un segnale falso, e
  // qualcuno si metterebbe a richiamare una persona che non ha chiesto
  // niente.
  //
  // La seconda: il successo si dichiara solo se il server lo conferma. Un
  // "Richiesta inviata" mostrato su un errore lascerebbe una persona ad
  // aspettare una telefonata che nessuno fara'.

  const CONSULTATION_SENT_MESSAGE = 'Richiesta inviata. Ti ricontatteremo.';
  const CONSULTATION_ERROR_MESSAGE =
    'Non siamo riusciti a inviare la richiesta. Riprova.';
  const CONSULTATION_SENDING_MESSAGE = 'Invio…';

  function setConsultationState(name, message = '') {
    homeConsultationConfirm.hidden = name !== 'confirming';
    homeConsultationCta.hidden = name === 'confirming' || name === 'sent';
    homeConsultationCta.disabled = name === 'sending';
    homeConsultationSend.disabled = name === 'sending';
    homeConsultationStatus.textContent = message;
    homeConsultationStatus.hidden = message === '';
  }

  function resetConsultation(stimaId) {
    // Gia' inviata per questa casa in questa sessione: la CTA non torna
    // attiva, perche' chiedere due volte la stessa cosa non aggiunge
    // niente e fa sembrare che la prima non sia arrivata.
    if (state.consultationSent.has(stimaId)) {
      setConsultationState('sent', CONSULTATION_SENT_MESSAGE);
      return;
    }
    setConsultationState('idle');
  }

  async function sendConsultationRequest() {
    const stimaId = state.selectedStimaId;
    if (stimaId === null || stimaId === undefined || state.consultationInFlight) {
      return;
    }
    state.consultationInFlight = true;
    setConsultationState('sending', CONSULTATION_SENDING_MESSAGE);
    try {
      await apiRequest(`/homes/${encodeURIComponent(stimaId)}/consultation-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      state.consultationSent.add(stimaId);
      setConsultationState('sent', CONSULTATION_SENT_MESSAGE);
    } catch (_error) {
      // Nessun dettaglio tecnico, e soprattutto nessun falso successo: la
      // CTA torna disponibile perche' riprovare e' la cosa giusta da fare.
      setConsultationState('idle', CONSULTATION_ERROR_MESSAGE);
    } finally {
      state.consultationInFlight = false;
    }
  }
  // LMC9_END

  async function selectHome(stimaId) {
    if (!state.session || stimaId === null || stimaId === undefined) {
      return;
    }
    state.selectedStimaId = stimaId;
    setSelectedHomeCardState();
    // Cambiando casa le sezioni tornano chiuse: l'apertura vale per la casa
    // che si sta guardando, non per il portale.
    setSectionOpen(homeHistoryToggle, false);
    setSectionOpen(homeDemandToggle, false);
    resetConsultation(stimaId);
    const generation = ++state.homeDetailGeneration;
    showHomeDetailState('loading');

    let payload;
    try {
      payload = await apiRequest(`/homes/${encodeURIComponent(stimaId)}`);
    } catch (error) {
      if (generation !== state.homeDetailGeneration || !state.session) {
        return;
      }
      if (error instanceof PortalRequestError && error.status === 404) {
        // 404 neutro: non si dice se la casa non esiste o non e' tua.
        showHomeDetailState('error', 'Casa non disponibile o accesso non più valido.');
        return;
      }
      if (isAuthLoss(error)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      showHomeDetailState('error', homeErrorText(error));
      return;
    }

    if (generation !== state.homeDetailGeneration || !state.session) {
      return;
    }
    renderHomeDetail(payload);
    showHomeDetailState('content');
  }

  async function requestLoginLink(email) {
    if (state.emailLinkInFlight) {
      return;
    }
    state.emailLinkInFlight = true;
    emailLoginButton.disabled = true;
    try {
      await apiRequest('/auth/request-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      emailLoginMessage.textContent = EMAIL_LINK_NEUTRAL_MESSAGE;
    } catch (_error) {
      // LA STESSA RISPOSTA, SEMPRE. Indirizzo sconosciuto, account chiuso,
      // limite raggiunto, guasto: distinguerli qui rimetterebbe in piedi
      // proprio l'enumerazione che il 204 uniforme del backend impedisce.
      emailLoginMessage.textContent = EMAIL_LINK_NEUTRAL_MESSAGE;
    } finally {
      state.emailLinkInFlight = false;
      emailLoginButton.disabled = false;
    }
  }
  // LMC6_END

  // LMC6_START
  function renderHomesSection(sections) {
    homesSection.hidden = !sections.showHomes;
    if (!sections.showHomes) {
      homeList.replaceChildren();
      homeCount.textContent = '';
      showHomesState('idle');
      showHomeDetailState('empty');
      // LMC12_START - senza case niente novita', e nessuna richiesta parte.
      resetHomeNotificationsState();
      // LMC12_END
      return;
    }
    const numero = sections.homeCount;
    homeCount.textContent = `${numero} ${numero === 1 ? 'casa' : 'case'}`;
    renderHomeList(state.homes);
    showHomesState('content');
    showHomeDetailState('empty');
    // LMC12_START - le novita' si caricano solo quando c'e' almeno una casa:
    // senza case la sezione non esiste e nessuna richiesta parte.
    homeNotificationsSection.hidden = false;
    void loadHomeNotifications({ reset: true });
    // LMC12_END
  }

  async function selectFirstHome() {
    const prima = state.homes.length ? state.homes[0].stima_id : null;
    if (prima !== null) {
      await selectHome(prima);
    }
  }
  // LMC6_END

  // LMC12_START - "Novita' sulla tua casa".
  //
  // Stesso modello delle notifiche P5 (paginazione, filtro non lette, segna
  // come letta con conferma nella card), ma su `/home-notifications`: uno
  // stream diverso, uno stato diverso, nessuna riga condivisa con P5. In
  // piu' ogni card sa a quale casa si riferisce (`stima_id`) e la apre.
  function resetHomeNotificationsState({ preserveFilter = false } = {}) {
    state.homeNotificationGeneration += 1;
    state.homeNotificationItems = [];
    state.homeNotificationOffset = 0;
    state.homeNotificationHasMore = false;
    state.homeNotificationLoadInFlight = false;
    state.homeNotificationReadInFlight.clear();
    if (!preserveFilter) {
      state.homeNotificationUnreadOnly = false;
      homeNotificationsUnreadOnly.checked = false;
    }
    homeNotificationsList.replaceChildren();
    homeNotificationsSection.hidden = true;
    homeNotificationsLoading.hidden = true;
    homeNotificationsEmpty.hidden = true;
    homeNotificationsError.hidden = true;
    homeNotificationsContent.hidden = true;
    homeNotificationsErrorMessage.textContent = '';
    homeNotificationsEmptyMessage.textContent = 'Per ora non ci sono novità sulla tua casa.';
    homeNotificationsPagination.hidden = true;
    homeNotificationsLoadMore.disabled = false;
    homeNotificationsLoadMore.textContent = 'Carica altre';
    homeNotificationsPaginationStatus.textContent = '';
  }

  function showHomeNotificationsState(name, message = '') {
    homeNotificationsLoading.hidden = name !== 'loading';
    homeNotificationsEmpty.hidden = name !== 'empty';
    homeNotificationsError.hidden = name !== 'error';
    homeNotificationsContent.hidden = name !== 'content';
    if (name === 'error') {
      homeNotificationsErrorMessage.textContent = message || 'Riprova tra poco.';
    }
    if (name === 'empty') {
      homeNotificationsEmptyMessage.textContent = state.homeNotificationUnreadOnly
        ? 'Non ci sono novità non lette.'
        : 'Per ora non ci sono novità sulla tua casa.';
    }
  }

  function homeNotificationTypeLabel(type) {
    return HOME_NOTIFICATION_TYPE_LABELS[type] || 'Novità';
  }

  function homeNotificationStimaId(item) {
    const id = Number(item && item.stima_id);
    return Number.isInteger(id) && id > 0 ? id : null;
  }

  function homeNotificationHomeName(stimaId) {
    const casa = state.homes.find((home) => home && home.stima_id === stimaId);
    if (!casa) return '';
    const via = [casa.via, casa.civico].filter((v) => typeof v === 'string' && v.trim()).join(' ');
    const parti = [via, casa.comune].filter((v) => typeof v === 'string' && v.trim());
    return parti.join(', ');
  }

  function renderHomeNotificationCard(item) {
    const id = notificationId(item);
    const stimaId = homeNotificationStimaId(item);
    const card = document.createElement('article');
    card.className = 'notification-card home-notification-card';
    card.setAttribute('role', 'listitem');
    if (id !== null) card.dataset.homeNotificationId = String(id);
    if (stimaId !== null) card.dataset.stimaId = String(stimaId);
    card.classList.toggle('is-unread', !item.read_at);

    const top = document.createElement('div');
    top.className = 'notification-card-topline';
    const type = document.createElement('span');
    type.className = 'notification-type-label';
    type.textContent = homeNotificationTypeLabel(item.type);
    const readState = document.createElement('span');
    readState.className = 'notification-read-badge';
    readState.textContent = item.read_at ? 'Letta' : 'Non letta';
    top.append(type, readState);

    const title = document.createElement('h4');
    title.className = 'notification-title';
    title.textContent = typeof item.title === 'string' && item.title.trim() ? item.title : 'Novità';
    const body = document.createElement('p');
    body.className = 'notification-body';
    body.textContent = typeof item.body === 'string' ? item.body : '';
    const date = document.createElement('p');
    date.className = 'notification-date';
    const casa = stimaId !== null ? homeNotificationHomeName(stimaId) : '';
    date.textContent = (item.created_at ? formatPublishedAt(item.created_at) : 'Data non disponibile')
      + (casa ? ` · ${casa}` : '');

    const actions = document.createElement('div');
    actions.className = 'notification-actions home-notification-actions';
    const button = document.createElement('button');
    button.className = 'secondary-button notification-read-button';
    button.type = 'button';
    button.textContent = item.read_at ? 'Letta' : 'Segna come letta';
    button.disabled = Boolean(item.read_at) || id === null;
    if (id !== null) button.dataset.homeNotificationId = String(id);
    button.addEventListener('click', () => {
      if (id !== null) void markHomeNotificationRead(id);
    });
    const open = document.createElement('button');
    open.className = 'secondary-button home-notification-open';
    open.type = 'button';
    open.textContent = 'Vedi la casa';
    open.disabled = stimaId === null || !state.homes.some((home) => home && home.stima_id === stimaId);
    if (stimaId !== null) open.dataset.stimaId = String(stimaId);
    open.addEventListener('click', () => {
      if (stimaId !== null) void selectHome(stimaId);
    });
    const actionStatus = document.createElement('p');
    actionStatus.className = 'status-message notification-action-status';
    actionStatus.setAttribute('role', 'status');
    actionStatus.setAttribute('aria-live', 'polite');
    actions.append(button, open, actionStatus);
    card.append(top, title, body, date, actions);
    return card;
  }

  function renderHomeNotifications() {
    homeNotificationsList.replaceChildren();
    for (const item of state.homeNotificationItems) {
      if (item && typeof item === 'object') {
        homeNotificationsList.append(renderHomeNotificationCard(item));
      }
    }
  }

  function homeNotificationPageUrl(offset) {
    const params = new URLSearchParams({
      limit: String(HOME_NOTIFICATIONS_LIMIT),
      offset: String(offset),
      unread_only: state.homeNotificationUnreadOnly ? 'true' : 'false',
    });
    return `/home-notifications?${params.toString()}`;
  }

  function updateHomeNotificationPagination() {
    homeNotificationsPagination.hidden = !state.homeNotificationHasMore;
    homeNotificationsLoadMore.disabled = state.homeNotificationLoadInFlight || !state.homeNotificationHasMore;
    homeNotificationsLoadMore.textContent = state.homeNotificationLoadInFlight ? 'Caricamento…' : 'Carica altre';
    homeNotificationsPaginationStatus.textContent = state.homeNotificationHasMore
      ? `${state.homeNotificationItems.length} novità caricate.`
      : '';
  }

  async function loadHomeNotifications({ reset = false } = {}) {
    if (!state.session) return;
    if (reset) {
      resetHomeNotificationsState({ preserveFilter: true });
      homeNotificationsSection.hidden = false;
    }
    if (state.homeNotificationLoadInFlight
      || (!reset && !state.homeNotificationHasMore && state.homeNotificationOffset > 0)) {
      return;
    }

    const generation = state.homeNotificationGeneration;
    const filterAtStart = state.homeNotificationUnreadOnly;
    const offsetAtStart = state.homeNotificationOffset;
    state.homeNotificationLoadInFlight = true;
    if (offsetAtStart === 0) showHomeNotificationsState('loading');
    updateHomeNotificationPagination();

    let payload;
    try {
      payload = await apiRequest(homeNotificationPageUrl(offsetAtStart));
    } catch (error) {
      if (generation !== state.homeNotificationGeneration
        || filterAtStart !== state.homeNotificationUnreadOnly
        || !state.session) return;
      state.homeNotificationLoadInFlight = false;
      updateHomeNotificationPagination();
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      showHomeNotificationsState('error', error instanceof PortalRequestError && error.status === 404
        ? 'Contenuto non disponibile o accesso non più valido.'
        : 'Impossibile caricare le novità. Riprova tra poco.');
      return;
    }

    if (generation !== state.homeNotificationGeneration
      || filterAtStart !== state.homeNotificationUnreadOnly
      || !state.session) return;

    state.homeNotificationLoadInFlight = false;
    const items = payload && Array.isArray(payload.items)
      ? payload.items.filter((item) => item && typeof item === 'object')
      : [];
    state.homeNotificationItems = offsetAtStart === 0 ? items : state.homeNotificationItems.concat(items);
    state.homeNotificationHasMore = payload && payload.has_more === true;
    const payloadLimit = payload && Number.isInteger(payload.limit) && payload.limit > 0
      ? payload.limit
      : HOME_NOTIFICATIONS_LIMIT;
    const payloadOffset = payload && Number.isInteger(payload.offset) && payload.offset >= 0
      ? payload.offset
      : offsetAtStart;
    state.homeNotificationOffset = payloadOffset + payloadLimit;

    if (state.homeNotificationItems.length === 0) {
      showHomeNotificationsState('empty');
      updateHomeNotificationPagination();
      return;
    }
    renderHomeNotifications();
    showHomeNotificationsState('content');
    updateHomeNotificationPagination();
  }

  function homeNotificationCardById(id) {
    return Array.from(homeNotificationsList.children)
      .find((card) => Number(card.dataset.homeNotificationId) === id) || null;
  }

  function updateHomeNotificationItemFromRead(id, payload) {
    const index = state.homeNotificationItems.findIndex((item) => notificationId(item) === id);
    if (index < 0) return;
    const card = homeNotificationCardById(id);
    if (state.homeNotificationUnreadOnly) {
      state.homeNotificationItems.splice(index, 1);
      state.homeNotificationOffset = Math.max(0, state.homeNotificationOffset - 1);
      if (card) {
        const remaining = Array.from(homeNotificationsList.children).filter((item) => item !== card);
        homeNotificationsList.replaceChildren(...remaining);
      }
    } else {
      state.homeNotificationItems[index] = {
        ...state.homeNotificationItems[index],
        ...(payload && typeof payload === 'object' ? payload : {}),
        read_at: payload && payload.read_at ? payload.read_at : new Date().toISOString(),
      };
      if (card) {
        card.classList.remove('is-unread');
        const readState = card.children[0]?.children?.[1];
        const button = card.children[4]?.children?.[0];
        const status = card.children[4]?.children?.[2];
        if (readState) readState.textContent = 'Letta';
        if (button) {
          button.disabled = true;
          button.textContent = 'Letta';
        }
        if (status) {
          status.classList.remove('is-error');
          status.textContent = 'Novità segnata come letta.';
        }
      }
    }
    if (state.homeNotificationItems.length === 0) {
      showHomeNotificationsState('empty');
      homeNotificationsList.replaceChildren();
    } else {
      showHomeNotificationsState('content');
    }
    updateHomeNotificationPagination();
  }

  async function markHomeNotificationRead(id) {
    if (!state.session || state.homeNotificationReadInFlight.has(id)) return;
    const item = state.homeNotificationItems.find((entry) => notificationId(entry) === id);
    if (!item || item.read_at) return;

    const generation = state.homeNotificationGeneration;
    state.homeNotificationReadInFlight.add(id);
    const card = homeNotificationCardById(id);
    const button = card && card.children.length ? card.children[4]?.children?.[0] : null;
    const status = card && card.children.length ? card.children[4]?.children?.[2] : null;
    if (button) {
      button.disabled = true;
      button.textContent = 'Aggiornamento…';
    }
    if (status) status.textContent = 'Aggiornamento in corso…';

    let payload;
    try {
      payload = await apiRequest(`/home-notifications/${encodeURIComponent(String(id))}/read`, { method: 'POST' });
    } catch (error) {
      if (generation !== state.homeNotificationGeneration || !state.session) return;
      state.homeNotificationReadInFlight.delete(id);
      if (error instanceof PortalRequestError && (error.status === 401 || error.status === 403)) {
        enterLoggedOut('Sessione non disponibile o scaduta.');
        return;
      }
      if (button) {
        button.disabled = false;
        button.textContent = 'Segna come letta';
      }
      if (status) {
        status.classList.add('is-error');
        status.textContent = error instanceof PortalRequestError && error.status === 404
          ? 'Novità non disponibile o accesso non più valido.'
          : 'Impossibile aggiornare la novità. Riprova tra poco.';
      }
      return;
    }

    if (generation !== state.homeNotificationGeneration || !state.session) return;
    state.homeNotificationReadInFlight.delete(id);
    updateHomeNotificationItemFromRead(id, payload);
  }
  // LMC12_END

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

    // LMC6_START - le due superfici arrivano dalla STESSA risposta, che LMC-2
    // ha reso additiva. `showEmpty` e' vero solo quando non c'e' ne' una casa
    // ne' un immobile: prima bastava zero immobili per dire "niente qui" a chi
    // aveva una casa in monitoraggio.
    const rawHomes = payload && Array.isArray(payload.homes) ? payload.homes : [];
    state.homes = rawHomes.filter((item) => item && Number.isInteger(item.stima_id));
    const sections = VM.dashboardSections({
      homes: state.homes,
      properties: state.properties,
    });
    renderHomesSection(sections);
    dashboardSection.hidden = !sections.showProperties && !sections.showEmpty;

    if (state.properties.length === 0) {
      // L'ospite senza immobili vede la schermata vuota SOLO se non ha
      // nemmeno una casa; altrimenti la sezione legacy resta nascosta.
      if (sections.showEmpty) {
        showDashboardState('empty');
      }
      if (sections.showHomes) {
        await selectFirstHome();
      }
      return;
    }

    renderPropertyList(state.properties);
    showDashboardState('content');
    if (sections.showHomes) {
      await selectFirstHome();
    }
    // LMC6_END

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
    startP68DataLoads();
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
      startP68DataLoads();
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

  // LMC6_START
  emailLoginForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const email = emailInput.value.trim();
    if (!email) {
      emailInput.focus();
      return;
    }
    void requestLoginLink(email);
  });

  homesRetry.addEventListener('click', () => {
    if (state.session) {
      void loadDashboard();
    }
  });

  homeHistoryToggle.addEventListener('click', () => {
    revealHistory();
  });

  homeDemandToggle.addEventListener('click', () => {
    revealDemand();
  });

  // LMC9_START
  homeConsultationCta.addEventListener('click', () => {
    setConsultationState('confirming');
  });

  homeConsultationCancel.addEventListener('click', () => {
    // Annulla e basta: nessuna richiesta parte, e non resta traccia di un
    // ripensamento.
    setConsultationState('idle');
  });

  homeConsultationSend.addEventListener('click', () => sendConsultationRequest());
  // LMC9_END

  // LMC10_START
  homeProfileEdit.addEventListener('click', () => {
    // Si riparte sempre dai valori con cui la scheda e' stata disegnata: se
    // il form era stato aperto, modificato e annullato, quelle modifiche non
    // devono riaffiorare alla riapertura.
    profileBuildForm();
    setProfileFormOpen(true);
    setProfileStatus('');
  });

  homeProfileCancel.addEventListener('click', () => {
    // ANNULLA NON CHIAMA NIENTE. Nessuna PATCH, nessun evento, nessuna
    // traccia: chi ha cambiato idea non ha aggiornato la propria casa.
    setProfileFormOpen(false);
    setProfileStatus('');
  });

  homeProfileForm.addEventListener('submit', (event) => {
    event.preventDefault();
    return saveHomeProfile();
  });
  // LMC10_END

  homeDetailRetry.addEventListener('click', () => {
    if (state.session && state.selectedStimaId !== null) {
      void selectHome(state.selectedStimaId);
    }
  });
  // LMC6_END

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


  notificationsUnreadOnly.addEventListener('change', () => {
    if (!state.session) return;
    state.notificationUnreadOnly = notificationsUnreadOnly.checked === true;
    void loadNotifications({ reset: true });
  });

  notificationsRetry.addEventListener('click', () => {
    if (state.session) void loadNotifications({ reset: true });
  });

  notificationsLoadMore.addEventListener('click', () => {
    if (state.session && state.notificationHasMore && !state.notificationLoadInFlight) {
      void loadNotifications();
    }
  });

  // LMC12_START
  homeNotificationsUnreadOnly.addEventListener('change', () => {
    if (!state.session) return;
    state.homeNotificationUnreadOnly = homeNotificationsUnreadOnly.checked === true;
    void loadHomeNotifications({ reset: true });
  });

  homeNotificationsRetry.addEventListener('click', () => {
    if (state.session) void loadHomeNotifications({ reset: true });
  });

  homeNotificationsLoadMore.addEventListener('click', () => {
    if (state.session && state.homeNotificationHasMore && !state.homeNotificationLoadInFlight) {
      void loadHomeNotifications();
    }
  });
  // LMC12_END

  notificationPreferencesRetry.addEventListener('click', () => {
    if (state.session) void loadNotificationPreferences();
  });

  notificationPreferencesForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await saveNotificationPreferences();
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
