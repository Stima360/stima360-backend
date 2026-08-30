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

const SHARED_DOCUMENT_TYPE_LABELS = Object.freeze({
  mandate: 'Mandato',
  floor_plan: 'Planimetria',
  ape: 'APE',
  cadastral_extract: 'Documento catastale',
  photo_report: 'Report fotografico',
  activity_report: 'Report attività',
  information: 'Documento informativo',
});

const SHARED_DOCUMENT_STATUS_LABELS = Object.freeze({
  draft: 'Bozza',
  published: 'Pubblicato',
  revoked: 'Revocato',
  archived: 'Archiviato',
});

const VISIT_FEEDBACK_CATEGORY_LABELS = Object.freeze({
  price: 'Posizionamento economico',
  state: 'Stato e presentazione',
  layout: 'Distribuzione degli spazi',
  location: 'Posizione',
  accessories: 'Accessori e pertinenze',
  general: 'Osservazione generale',
});

const VISIT_FEEDBACK_SENTIMENT_LABELS = Object.freeze({
  positive: 'Positivo',
  neutral: 'Neutro',
  negative: 'Critico',
  mixed: 'Misto',
});

const VISIT_FEEDBACK_STATUS_LABELS = Object.freeze({
  draft: 'Bozza',
  published: 'Pubblicato',
  archived: 'Archiviato',
});

const TOKEN_TYPE_LABELS = Object.freeze({
  invitation: 'Invito',
  login: 'Accesso',
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
  documentsGeneration: 0,
  documentDetailGeneration: 0,
  documentReadsGeneration: 0,
  visitFeedbackGeneration: 0,
  visitFeedbackDetailGeneration: 0,
  privacyGeneration: 0,
  tokenGeneration: 0,
  auditGeneration: 0,
  contactLookupGeneration: 0,
  accessAccountLookupGeneration: 0,
  accessPropertyLookupGeneration: 0,
  documentAccountLookupGeneration: 0,
  documentPropertyLookupGeneration: 0,
  documentSourceLookupGeneration: 0,
  visitAccountLookupGeneration: 0,
  visitPropertyLookupGeneration: 0,
  visitSourceLookupGeneration: 0,
  oneTimeToken: null,
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
  navDocuments: document.getElementById('nav-documents'),
  navVisitFeedback: document.getElementById('nav-visit-feedback'),
  navTokenAccess: document.getElementById('nav-token-access'),
  navAudit: document.getElementById('nav-audit'),
  dashboardSection: document.getElementById('section-dashboard'),
  accountsSection: document.getElementById('section-accounts'),
  accessSection: document.getElementById('section-access'),
  publicationsSection: document.getElementById('section-publications'),
  requestsSection: document.getElementById('section-requests'),
  documentsSection: document.getElementById('section-documents'),
  visitFeedbackSection: document.getElementById('section-visit-feedback'),
  tokenAccessSection: document.getElementById('section-token-access'),
  auditSection: document.getElementById('section-audit'),
  dashboardLoading: document.getElementById('dashboard-loading'),
  dashboardError: document.getElementById('dashboard-error'),
  dashboardErrorMessage: document.getElementById('dashboard-error-message'),
  dashboardRetry: document.getElementById('dashboard-retry'),
  dashboardReload: document.getElementById('dashboard-reload'),
  dashboardContent: document.getElementById('dashboard-content'),
  accountForm: document.getElementById('account-create-form'),
  accountContactSearch: document.getElementById('account-contact-search'),
  accountContactSearchButton: document.getElementById('account-contact-search-button'),
  accountContactLookupStatus: document.getElementById('account-contact-lookup-status'),
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
  accessAccountsLoad: document.getElementById('access-accounts-load'),
  accessPropertyId: document.getElementById('access-property-id'),
  accessPropertyLookupStatus: document.getElementById('access-property-lookup-status'),
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
  documentStorageHealthCheck: document.getElementById('document-storage-health-check'),
  documentStorageHealthStatus: document.getElementById('document-storage-health-status'),
  documentLinkForm: document.getElementById('document-link-form'),
  documentOwnerAccountId: document.getElementById('document-owner-account-id'),
  documentAccountsLoad: document.getElementById('document-accounts-load'),
  documentPropertyId: document.getElementById('document-property-id'),
  documentPropertyLookupStatus: document.getElementById('document-property-lookup-status'),
  documentPropertyDocumentId: document.getElementById('document-property-document-id'),
  documentSourceLookupStatus: document.getElementById('document-source-lookup-status'),
  documentAllAuthorized: document.getElementById('document-all-authorized'),
  documentPublicTitle: document.getElementById('document-public-title'),
  documentPublicType: document.getElementById('document-public-type'),
  documentExpiresAt: document.getElementById('document-expires-at'),
  documentCreatedBy: document.getElementById('document-created-by'),
  documentAckRequired: document.getElementById('document-ack-required'),
  documentLinkSubmit: document.getElementById('document-link-submit'),
  documentLinkStatus: document.getElementById('document-link-status'),
  documentUploadForm: document.getElementById('document-upload-form'),
  documentUploadFile: document.getElementById('document-upload-file'),
  documentUploadPropertyId: document.getElementById('document-upload-property-id'),
  documentUploadDocumentType: document.getElementById('document-upload-document-type'),
  documentUploadSourceTitle: document.getElementById('document-upload-source-title'),
  documentUploadPublicTitle: document.getElementById('document-upload-public-title'),
  documentUploadPublicType: document.getElementById('document-upload-public-type'),
  documentUploadOwnerAccountId: document.getElementById('document-upload-owner-account-id'),
  documentUploadSupersedesId: document.getElementById('document-upload-supersedes-id'),
  documentUploadExpiresAt: document.getElementById('document-upload-expires-at'),
  documentUploadCreatedBy: document.getElementById('document-upload-created-by'),
  documentUploadAckRequired: document.getElementById('document-upload-ack-required'),
  documentUploadSubmit: document.getElementById('document-upload-submit'),
  documentUploadStatus: document.getElementById('document-upload-status'),
  documentDetailPanel: document.getElementById('document-detail-panel'),
  documentDetailClose: document.getElementById('document-detail-close'),
  documentDetailStatus: document.getElementById('document-detail-status'),
  documentDetailContent: document.getElementById('document-detail-content'),
  documentReadsPanel: document.getElementById('document-reads-panel'),
  documentReadsClose: document.getElementById('document-reads-close'),
  documentReadsStatus: document.getElementById('document-reads-status'),
  documentReadsContent: document.getElementById('document-reads-content'),
  documentsLoading: document.getElementById('documents-loading'),
  documentsEmpty: document.getElementById('documents-empty'),
  documentsError: document.getElementById('documents-error'),
  documentsErrorMessage: document.getElementById('documents-error-message'),
  documentsRetry: document.getElementById('documents-retry'),
  documentsReload: document.getElementById('documents-reload'),
  documentsContent: document.getElementById('documents-content'),
  visitFeedbackForm: document.getElementById('visit-feedback-create-form'),
  visitFeedbackOwnerAccountId: document.getElementById('visit-feedback-owner-account-id'),
  visitFeedbackAccountsLoad: document.getElementById('visit-feedback-accounts-load'),
  visitFeedbackPropertyId: document.getElementById('visit-feedback-property-id'),
  visitFeedbackPropertyLookupStatus: document.getElementById('visit-feedback-property-lookup-status'),
  visitFeedbackPropertyVisitId: document.getElementById('visit-feedback-property-visit-id'),
  visitFeedbackSourceLookupStatus: document.getElementById('visit-feedback-source-lookup-status'),
  visitFeedbackAllAuthorized: document.getElementById('visit-feedback-all-authorized'),
  visitFeedbackCategory: document.getElementById('visit-feedback-category'),
  visitFeedbackSentiment: document.getElementById('visit-feedback-sentiment'),
  visitFeedbackSummary: document.getElementById('visit-feedback-summary'),
  visitFeedbackCreatedBy: document.getElementById('visit-feedback-created-by'),
  visitFeedbackPrivacyCheck: document.getElementById('visit-feedback-privacy-check'),
  visitFeedbackSubmit: document.getElementById('visit-feedback-create-submit'),
  visitFeedbackFormStatus: document.getElementById('visit-feedback-form-status'),
  visitFeedbackPrivacyIssues: document.getElementById('visit-feedback-privacy-issues'),
  visitFeedbackDetailPanel: document.getElementById('visit-feedback-detail-panel'),
  visitFeedbackDetailClose: document.getElementById('visit-feedback-detail-close'),
  visitFeedbackDetailStatus: document.getElementById('visit-feedback-detail-status'),
  visitFeedbackDetailContent: document.getElementById('visit-feedback-detail-content'),
  visitFeedbackLoading: document.getElementById('visit-feedback-loading'),
  visitFeedbackEmpty: document.getElementById('visit-feedback-empty'),
  visitFeedbackError: document.getElementById('visit-feedback-error'),
  visitFeedbackErrorMessage: document.getElementById('visit-feedback-error-message'),
  visitFeedbackRetry: document.getElementById('visit-feedback-retry'),
  visitFeedbackReload: document.getElementById('visit-feedback-reload'),
  visitFeedbackContent: document.getElementById('visit-feedback-content'),
  tokenFormPanel: document.getElementById('token-form-panel'),
  tokenForm: document.getElementById('token-create-form'),
  tokenOwnerAccountId: document.getElementById('token-owner-account-id'),
  tokenType: document.getElementById('token-type'),
  tokenExpiresMinutes: document.getElementById('token-expires-minutes'),
  tokenCreatedBy: document.getElementById('token-created-by'),
  tokenSubmit: document.getElementById('token-create-submit'),
  tokenFormStatus: document.getElementById('token-form-status'),
  tokenResultPanel: document.getElementById('token-result-panel'),
  tokenResultMeta: document.getElementById('token-result-meta'),
  tokenResultValue: document.getElementById('token-result-value'),
  tokenCopy: document.getElementById('token-copy'),
  tokenClose: document.getElementById('token-close'),
  tokenCopyStatus: document.getElementById('token-copy-status'),
  auditLoading: document.getElementById('audit-loading'),
  auditEmpty: document.getElementById('audit-empty'),
  auditError: document.getElementById('audit-error'),
  auditErrorMessage: document.getElementById('audit-error-message'),
  auditRetry: document.getElementById('audit-retry'),
  auditReload: document.getElementById('audit-reload'),
  auditContent: document.getElementById('audit-content'),
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
  if (error.status === 0) return 'Errore di connessione. Riprova.';
  if (error.status === 400) return 'Richiesta non valida. Controlla i dati e riprova.';
  if (error.status === 401) return 'Credenziali non valide.';
  if (error.status === 403) return 'Accesso non autorizzato.';
  if (error.status === 404) return 'Risorsa non disponibile.';
  if (error.status === 409) return 'Operazione non più valida nello stato corrente.';
  if (error.status === 413) return 'File troppo grande per il caricamento.';
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
  const body = options.formData !== undefined
    ? options.formData
    : (options.json === undefined ? undefined : JSON.stringify(options.json));

  let response;
  try {
    response = await fetch(ADMIN_API + path, {
      method: options.method || 'GET',
      headers,
      body,
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

function safeDownloadFilename(value) {
  const fallback = 'documento';
  if (!value) return fallback;
  const cleaned = String(value).replace(/[\\/\u0000-\u001f\u007f]+/g, '_').trim();
  return cleaned ? cleaned.slice(0, 180) : fallback;
}

function filenameFromDisposition(value) {
  if (!value) return 'documento';
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(value);
  if (encoded) {
    try { return safeDownloadFilename(decodeURIComponent(encoded[1])); } catch (_error) { return safeDownloadFilename(encoded[1]); }
  }
  const quoted = /filename="([^"]+)"/i.exec(value);
  if (quoted) return safeDownloadFilename(quoted[1]);
  const plain = /filename=([^;]+)/i.exec(value);
  return plain ? safeDownloadFilename(plain[1]) : 'documento';
}

async function downloadAdminDocument(documentId, statusNode) {
  if (!state.credentials) return false;
  const key = `document-download:${documentId}`;
  if (state.mutationsInFlight.has(key)) return false;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  setStatus(statusNode, 'Download in preparazione…');
  try {
    let response;
    try {
      response = await fetch(`${ADMIN_API}/documents/${documentId}/download`, {
        method: 'GET',
        headers: { Authorization: encodeBasic(state.credentials.username, state.credentials.password) },
        cache: 'no-store',
        credentials: 'same-origin',
      });
    } catch (_error) {
      throw new ApiError(0, 'Network error');
    }
    if (!response.ok) throw new ApiError(response.status, 'HTTP error');
    const blob = await response.blob();
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return false;
    const filename = filenameFromDisposition(response.headers.get('Content-Disposition'));
    const objectUrl = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement('a');
      anchor.setAttribute('href', objectUrl);
      anchor.setAttribute('download', filename);
      anchor.setAttribute('rel', 'noopener');
      anchor.click();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    setStatus(statusNode, 'Download avviato.', 'success');
    return true;
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return false;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return false;
    }
    setStatus(statusNode, statusMessage(error), 'error');
    return false;
  } finally {
    state.mutationsInFlight.delete(key);
  }
}

function invalidatePending() {
  state.sessionGeneration += 1;
  state.loginGeneration += 1;
  state.dashboardGeneration += 1;
  state.accountsGeneration += 1;
  state.accessGeneration += 1;
  state.publicationsGeneration += 1;
  state.requestsGeneration += 1;
  state.documentsGeneration += 1;
  state.documentDetailGeneration += 1;
  state.documentReadsGeneration += 1;
  state.visitFeedbackGeneration += 1;
  state.visitFeedbackDetailGeneration += 1;
  state.privacyGeneration += 1;
  state.tokenGeneration += 1;
  state.auditGeneration += 1;
  state.contactLookupGeneration += 1;
  state.accessAccountLookupGeneration += 1;
  state.accessPropertyLookupGeneration += 1;
  state.documentAccountLookupGeneration += 1;
  state.documentPropertyLookupGeneration += 1;
  state.documentSourceLookupGeneration += 1;
  state.visitAccountLookupGeneration += 1;
  state.visitPropertyLookupGeneration += 1;
  state.visitSourceLookupGeneration += 1;
  state.mutationsInFlight.clear();
}

function clearOneTimeToken(invalidateGeneration = true) {
  if (invalidateGeneration) state.tokenGeneration += 1;
  if (state.oneTimeToken && typeof state.oneTimeToken.raw === 'string') {
    state.oneTimeToken.raw = '';
  }
  state.oneTimeToken = null;
  el.tokenResultValue.textContent = '';
  el.tokenResultMeta.replaceChildren();
  setStatus(el.tokenCopyStatus, '');
  el.tokenResultPanel.hidden = true;
  el.tokenFormPanel.hidden = false;
  el.tokenSubmit.disabled = false;
  el.tokenSubmit.textContent = 'Genera token';
}

function resetDynamicUi() {
  el.dashboardContent.replaceChildren();
  el.accountsContent.replaceChildren();
  el.accessContent.replaceChildren();
  el.publicationsContent.replaceChildren();
  el.requestsContent.replaceChildren();
  el.documentsContent.replaceChildren();
  el.documentDetailContent.replaceChildren();
  el.documentReadsContent.replaceChildren();
  el.visitFeedbackContent.replaceChildren();
  el.visitFeedbackDetailContent.replaceChildren();
  el.auditContent.replaceChildren();
  clearOneTimeToken(false);
  setStatus(el.globalStatus, '');
  setStatus(el.accountFormStatus, '');
  setStatus(el.accountContactLookupStatus, '');
  setStatus(el.accessFormStatus, '');
  setStatus(el.accessPropertyLookupStatus, '');
  setStatus(el.publicationFormStatus, '');
  setStatus(el.documentLinkStatus, '');
  setStatus(el.documentPropertyLookupStatus, '');
  setStatus(el.documentSourceLookupStatus, '');
  setStatus(el.documentUploadStatus, '');
  setStatus(el.documentStorageHealthStatus, '');
  setStatus(el.documentDetailStatus, '');
  setStatus(el.documentReadsStatus, '');
  setStatus(el.visitFeedbackFormStatus, '');
  setStatus(el.visitFeedbackPropertyLookupStatus, '');
  setStatus(el.visitFeedbackSourceLookupStatus, '');
  setStatus(el.visitFeedbackDetailStatus, '');
  setStatus(el.tokenFormStatus, '');
  setStatus(el.auditErrorMessage, '');
  el.auditLoading.hidden = true;
  el.auditEmpty.hidden = true;
  el.auditError.hidden = true;
  el.auditContent.hidden = true;
  el.visitFeedbackPrivacyIssues.replaceChildren();
  el.visitFeedbackPrivacyIssues.hidden = true;
  el.documentDetailPanel.hidden = true;
  el.documentReadsPanel.hidden = true;
  el.visitFeedbackDetailPanel.hidden = true;
  el.accountForm.reset();
  el.accountLanguage.value = 'it';
  resetLookupSelect(el.accountContactId, 'Cerca e seleziona un contatto', true);
  el.accessForm.reset();
  el.accessRole.value = 'owner';
  resetLookupSelect(el.accessOwnerAccountId, 'Carica e seleziona un account', false);
  resetLookupSelect(el.accessPropertyId, 'Seleziona prima un account', true);
  el.publicationForm.reset();
  el.publicationType.value = 'general_update';
  el.documentLinkForm.reset();
  el.documentPublicType.value = 'mandate';
  resetLookupSelect(el.documentOwnerAccountId, 'Carica e seleziona un account', false);
  resetLookupSelect(el.documentPropertyId, 'Seleziona prima un account', true);
  resetLookupSelect(el.documentPropertyDocumentId, 'Seleziona prima un immobile', true);
  el.documentUploadForm.reset();
  el.documentUploadPublicType.value = 'mandate';
  el.visitFeedbackForm.reset();
  resetLookupSelect(el.visitFeedbackOwnerAccountId, 'Carica e seleziona un account', false);
  resetLookupSelect(el.visitFeedbackPropertyId, 'Seleziona prima un account', true);
  resetLookupSelect(el.visitFeedbackPropertyVisitId, 'Seleziona prima un immobile', true);
  el.visitFeedbackCategory.value = 'price';
  el.visitFeedbackSentiment.value = '';
  el.tokenForm.reset();
  el.tokenType.value = 'invitation';
  el.tokenExpiresMinutes.value = '30';
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
  if (section === 'documents') return generation === state.documentsGeneration && state.activeSection === 'documents';
  if (section === 'visit-feedback') return generation === state.visitFeedbackGeneration && state.activeSection === 'visit-feedback';
  if (section === 'token-access') return generation === state.tokenGeneration && state.activeSection === 'token-access';
  if (section === 'audit') return generation === state.auditGeneration && state.activeSection === 'audit';
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

// OWNER 0.2 P8.1 — read-only CORE / PROPERTY reference resolution ---------
function resetLookupSelect(select, placeholder, disabled = true) {
  if (!select) return;
  const option = document.createElement('option');
  option.value = '';
  option.textContent = placeholder;
  select.replaceChildren(option);
  select.value = '';
  select.disabled = disabled;
}

function appendLookupOption(select, value, label) {
  const option = document.createElement('option');
  option.value = String(value);
  option.textContent = label || `#${value}`;
  select.append(option);
}

function lookupStillCurrent(generationKey, generation, sessionGeneration, parentNode = null, parentValue = null) {
  if (!state.authenticated || state.sessionGeneration !== sessionGeneration) return false;
  if (state[generationKey] !== generation) return false;
  if (parentNode && String(parentNode.value) !== String(parentValue)) return false;
  return true;
}

function contactLookupLabel(item) {
  const name = item && item.display_name ? String(item.display_name) : `Contatto #${item && item.id !== undefined ? item.id : '—'}`;
  const email = item && item.email ? String(item.email) : '';
  return email ? `${name} — ${email}` : name;
}

function ownerAccountLookupLabel(item) {
  const name = item && item.display_name ? String(item.display_name) : `Account #${item && item.id !== undefined ? item.id : '—'}`;
  const email = item && item.email ? String(item.email) : '';
  return email ? `${name} — ${email}` : `${name} (#${item && item.id !== undefined ? item.id : '—'})`;
}

function propertyLookupLabel(item) {
  const title = item && item.title ? String(item.title) : `Immobile #${item && item.id !== undefined ? item.id : '—'}`;
  const code = item && item.code ? String(item.code) : '';
  const locality = [item && item.address ? String(item.address) : '', item && item.city ? String(item.city) : ''].filter(Boolean).join(', ');
  return [code ? `${code} — ${title}` : title, locality].filter(Boolean).join(' · ');
}

function documentLookupLabel(item) {
  const title = item && item.title ? String(item.title) : `Documento #${item && item.id !== undefined ? item.id : '—'}`;
  const type = item && item.document_type ? String(item.document_type) : '';
  const status = item && item.status ? String(item.status) : '';
  return [title, type, status].filter(Boolean).join(' · ');
}

function visitLookupLabel(item) {
  const when = item && item.scheduled_at ? formatDate(item.scheduled_at) : `Visita #${item && item.id !== undefined ? item.id : '—'}`;
  const status = item && item.status ? String(item.status) : '';
  return [when, status].filter(Boolean).join(' · ');
}

function handleLookupError(error, statusNode) {
  if (error instanceof ApiError && error.status === 401) {
    forceLoginAfterUnauthorized();
    return;
  }
  setStatus(statusNode, statusMessage(error), 'error');
}

async function searchAccountContacts() {
  if (!state.authenticated) return;
  const generation = ++state.contactLookupGeneration;
  const sessionGeneration = state.sessionGeneration;
  const search = el.accountContactSearch.value.trim();
  resetLookupSelect(el.accountContactId, 'Ricerca in corso…', true);
  setStatus(el.accountContactLookupStatus, 'Ricerca contatti…');
  try {
    const query = search ? `?search=${encodeURIComponent(search)}&limit=50` : '?limit=50';
    const data = await request(`/lookups/contacts${query}`);
    if (!lookupStillCurrent('contactLookupGeneration', generation, sessionGeneration)) return;
    if (el.accountContactSearch.value.trim() !== search) return;
    const items = data && Array.isArray(data.items) ? data.items : [];
    resetLookupSelect(el.accountContactId, items.length ? 'Seleziona un contatto' : 'Nessun contatto trovato', items.length === 0);
    for (const item of items) appendLookupOption(el.accountContactId, item.id, contactLookupLabel(item));
    setStatus(el.accountContactLookupStatus, items.length ? `${items.length} contatti disponibili.` : 'Nessun contatto trovato.', items.length ? 'success' : '');
  } catch (error) {
    if (!lookupStillCurrent('contactLookupGeneration', generation, sessionGeneration)) return;
    resetLookupSelect(el.accountContactId, 'Ricerca non disponibile', true);
    handleLookupError(error, el.accountContactLookupStatus);
  }
}

async function loadOwnerAccountChoices(select, statusNode, generationKey) {
  if (!state.authenticated) return;
  const generation = ++state[generationKey];
  const sessionGeneration = state.sessionGeneration;
  resetLookupSelect(select, 'Caricamento account…', true);
  setStatus(statusNode, 'Caricamento account OWNER…');
  try {
    const data = await request('/accounts');
    if (!lookupStillCurrent(generationKey, generation, sessionGeneration)) return;
    const items = data && Array.isArray(data.items) ? data.items : [];
    resetLookupSelect(select, items.length ? 'Seleziona un account' : 'Nessun account disponibile', items.length === 0);
    for (const item of items) appendLookupOption(select, item.id, ownerAccountLookupLabel(item));
    setStatus(statusNode, items.length ? `${items.length} account disponibili.` : 'Nessun account disponibile.', items.length ? 'success' : '');
  } catch (error) {
    if (!lookupStillCurrent(generationKey, generation, sessionGeneration)) return;
    resetLookupSelect(select, 'Account non disponibili', true);
    handleLookupError(error, statusNode);
  }
}

async function loadEligibleProperties(accountSelect, propertySelect, statusNode, generationKey, onReset = null) {
  const accountId = parsePositiveInt(accountSelect.value);
  const generation = ++state[generationKey];
  const sessionGeneration = state.sessionGeneration;
  if (onReset) onReset();
  if (accountId === null) {
    resetLookupSelect(propertySelect, 'Seleziona prima un account', true);
    setStatus(statusNode, '');
    return;
  }
  resetLookupSelect(propertySelect, 'Caricamento immobili…', true);
  setStatus(statusNode, 'Caricamento immobili OWNER-eligible…');
  const parentValue = accountSelect.value;
  try {
    const data = await request(`/lookups/accounts/${encodeURIComponent(accountId)}/properties`);
    if (!lookupStillCurrent(generationKey, generation, sessionGeneration, accountSelect, parentValue)) return;
    const items = data && Array.isArray(data.items) ? data.items : [];
    resetLookupSelect(propertySelect, items.length ? 'Seleziona un immobile' : 'Nessun immobile OWNER-eligible', items.length === 0);
    for (const item of items) appendLookupOption(propertySelect, item.id, propertyLookupLabel(item));
    setStatus(statusNode, items.length ? `${items.length} immobili disponibili.` : 'Nessun immobile associato con ruolo owner.', items.length ? 'success' : '');
  } catch (error) {
    if (!lookupStillCurrent(generationKey, generation, sessionGeneration, accountSelect, parentValue)) return;
    resetLookupSelect(propertySelect, 'Immobili non disponibili', true);
    handleLookupError(error, statusNode);
  }
}

async function loadPropertyDocumentsForAccount() {
  const accountId = parsePositiveInt(el.documentOwnerAccountId.value);
  const propertyId = parsePositiveInt(el.documentPropertyId.value);
  const generation = ++state.documentSourceLookupGeneration;
  const sessionGeneration = state.sessionGeneration;
  resetLookupSelect(el.documentPropertyDocumentId, propertyId === null ? 'Seleziona prima un immobile' : 'Caricamento documenti…', true);
  setStatus(el.documentSourceLookupStatus, propertyId === null ? '' : 'Caricamento documenti PROPERTY…');
  if (accountId === null || propertyId === null) return;
  const accountValue = el.documentOwnerAccountId.value;
  const propertyValue = el.documentPropertyId.value;
  try {
    const data = await request(`/lookups/accounts/${encodeURIComponent(accountId)}/properties/${encodeURIComponent(propertyId)}/documents`);
    if (!lookupStillCurrent('documentSourceLookupGeneration', generation, sessionGeneration, el.documentOwnerAccountId, accountValue)) return;
    if (String(el.documentPropertyId.value) !== String(propertyValue)) return;
    const items = data && Array.isArray(data.items) ? data.items : [];
    resetLookupSelect(el.documentPropertyDocumentId, items.length ? 'Seleziona un documento' : 'Nessun documento disponibile', items.length === 0);
    for (const item of items) appendLookupOption(el.documentPropertyDocumentId, item.id, documentLookupLabel(item));
    setStatus(el.documentSourceLookupStatus, items.length ? `${items.length} documenti disponibili.` : 'Nessun documento PROPERTY per questo immobile.', items.length ? 'success' : '');
  } catch (error) {
    if (!lookupStillCurrent('documentSourceLookupGeneration', generation, sessionGeneration, el.documentOwnerAccountId, accountValue)) return;
    if (String(el.documentPropertyId.value) !== String(propertyValue)) return;
    resetLookupSelect(el.documentPropertyDocumentId, 'Documenti non disponibili', true);
    handleLookupError(error, el.documentSourceLookupStatus);
  }
}

async function loadPropertyVisitsForAccount() {
  const accountId = parsePositiveInt(el.visitFeedbackOwnerAccountId.value);
  const propertyId = parsePositiveInt(el.visitFeedbackPropertyId.value);
  const generation = ++state.visitSourceLookupGeneration;
  const sessionGeneration = state.sessionGeneration;
  resetLookupSelect(el.visitFeedbackPropertyVisitId, propertyId === null ? 'Seleziona prima un immobile' : 'Caricamento visite…', true);
  setStatus(el.visitFeedbackSourceLookupStatus, propertyId === null ? '' : 'Caricamento visite PROPERTY…');
  if (accountId === null || propertyId === null) return;
  const accountValue = el.visitFeedbackOwnerAccountId.value;
  const propertyValue = el.visitFeedbackPropertyId.value;
  try {
    const data = await request(`/lookups/accounts/${encodeURIComponent(accountId)}/properties/${encodeURIComponent(propertyId)}/visits`);
    if (!lookupStillCurrent('visitSourceLookupGeneration', generation, sessionGeneration, el.visitFeedbackOwnerAccountId, accountValue)) return;
    if (String(el.visitFeedbackPropertyId.value) !== String(propertyValue)) return;
    const items = data && Array.isArray(data.items) ? data.items : [];
    resetLookupSelect(el.visitFeedbackPropertyVisitId, items.length ? 'Seleziona una visita' : 'Nessuna visita disponibile', items.length === 0);
    for (const item of items) appendLookupOption(el.visitFeedbackPropertyVisitId, item.id, visitLookupLabel(item));
    setStatus(el.visitFeedbackSourceLookupStatus, items.length ? `${items.length} visite disponibili.` : 'Nessuna visita PROPERTY per questo immobile.', items.length ? 'success' : '');
  } catch (error) {
    if (!lookupStillCurrent('visitSourceLookupGeneration', generation, sessionGeneration, el.visitFeedbackOwnerAccountId, accountValue)) return;
    if (String(el.visitFeedbackPropertyId.value) !== String(propertyValue)) return;
    resetLookupSelect(el.visitFeedbackPropertyVisitId, 'Visite non disponibili', true);
    handleLookupError(error, el.visitFeedbackSourceLookupStatus);
  }
}

async function submitAccount(event) {
  event.preventDefault();
  if (!state.authenticated || state.mutationsInFlight.has('account:create')) return;
  const contactId = parsePositiveInt(el.accountContactId.value);
  const language = el.accountLanguage.value.trim();
  if (contactId === null) {
    setStatus(el.accountFormStatus, 'Cerca e seleziona un contatto CORE.', 'error');
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
    setStatus(el.accessFormStatus, 'Seleziona un account proprietario.', 'error');
    el.accessOwnerAccountId.focus();
    return;
  }
  if (propertyId === null) {
    setStatus(el.accessFormStatus, 'Seleziona un immobile OWNER-eligible.', 'error');
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
    resetLookupSelect(el.accessOwnerAccountId, 'Carica e seleziona un account', false);
    resetLookupSelect(el.accessPropertyId, 'Seleziona prima un account', true);
    state.accessPropertyLookupGeneration += 1;
    setStatus(el.accessPropertyLookupStatus, '');
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


// OWNER 0.2 P7.3 — Documenti condivisi -------------------------------------
function setDocumentsState(mode, message = '') {
  el.documentsLoading.hidden = mode !== 'loading';
  el.documentsEmpty.hidden = mode !== 'empty';
  el.documentsError.hidden = mode !== 'error';
  el.documentsContent.hidden = mode !== 'content';
  if (mode === 'error') el.documentsErrorMessage.textContent = message;
}

function sharedDocumentTypeLabel(value) {
  return SHARED_DOCUMENT_TYPE_LABELS[value] || 'Tipo non disponibile';
}

function sharedDocumentStatusBadge(status) {
  return createStatusBadge(status, SHARED_DOCUMENT_STATUS_LABELS);
}

function validateSharedDocumentValues(values, requirePropertyDocument = true) {
  if (requirePropertyDocument && parsePositiveInt(values.propertyDocumentId) === null) return 'Inserisci un ID documento PROPERTY valido.';
  if (values.ownerAccountId && parsePositiveInt(values.ownerAccountId) === null) return 'Inserisci un ID account destinatario valido.';
  if (!values.publicTitle || values.publicTitle.length > 200) return 'Inserisci un titolo pubblico da 1 a 200 caratteri.';
  if (!Object.prototype.hasOwnProperty.call(SHARED_DOCUMENT_TYPE_LABELS, values.publicType)) return 'Seleziona un tipo documento pubblico valido.';
  if (values.expiresAt && !isoFromLocal(values.expiresAt)) return 'Inserisci una scadenza valida.';
  if (values.createdBy && values.createdBy.length > 200) return 'Il nome operatore non può superare 200 caratteri.';
  return '';
}

function sharedDocumentPayload(values, includePropertyDocument = true) {
  const payload = {
    owner_account_id: values.ownerAccountId ? parsePositiveInt(values.ownerAccountId) : null,
    public_title: values.publicTitle,
    public_document_type: values.publicType,
    expires_at: values.expiresAt ? isoFromLocal(values.expiresAt) : null,
    acknowledgement_required: values.acknowledgementRequired === true,
    created_by: values.createdBy || null,
  };
  if (includePropertyDocument) payload.property_document_id = parsePositiveInt(values.propertyDocumentId);
  return payload;
}

function setStorageHealthResult(data) {
  el.documentStorageHealthStatus.className = 'status-message';
  if (data && data.configured === true && data.available === true) {
    el.documentStorageHealthStatus.classList.add('storage-health', 'ok');
    el.documentStorageHealthStatus.textContent = 'Storage documentale disponibile.';
    return;
  }
  el.documentStorageHealthStatus.classList.add('storage-health', 'error');
  el.documentStorageHealthStatus.textContent = 'Storage documentale non disponibile.';
}

async function checkDocumentStorageHealth() {
  if (!state.authenticated || state.activeSection !== 'documents') return;
  const key = 'documents:health';
  if (state.mutationsInFlight.has(key)) return;
  state.mutationsInFlight.add(key);
  const generation = state.documentsGeneration;
  const sessionGeneration = state.sessionGeneration;
  el.documentStorageHealthCheck.disabled = true;
  setStatus(el.documentStorageHealthStatus, 'Verifica storage in corso…');
  try {
    const data = await request('/document-storage/health');
    if (!isCurrent('documents', generation, sessionGeneration)) return;
    setStorageHealthResult(data || {});
  } catch (error) {
    if (!isCurrent('documents', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setStatus(el.documentStorageHealthStatus, statusMessage(error), 'error');
  } finally {
    state.mutationsInFlight.delete(key);
    if (sessionGeneration === state.sessionGeneration) el.documentStorageHealthCheck.disabled = false;
  }
}

function documentDetailMeta(documentItem) {
  const grid = document.createElement('div');
  grid.className = 'detail-meta';
  grid.append(
    createMeta('ID condivisione', documentItem.id),
    createMeta('ID immobile', documentItem.property_id),
    createMeta('ID documento PROPERTY', documentItem.property_document_id),
    createMeta('ID account destinatario', documentItem.owner_account_id),
    createMeta('Tipo pubblico', sharedDocumentTypeLabel(documentItem.public_document_type)),
    createMeta('Versione', documentItem.version_number),
    createMeta('Stato', SHARED_DOCUMENT_STATUS_LABELS[documentItem.status] || 'Non disponibile'),
    createMeta('Pubblicato', formatDate(documentItem.published_at)),
    createMeta('Scadenza', formatDate(documentItem.expires_at)),
    createMeta('Presa visione richiesta', documentItem.acknowledgement_required === true ? 'Sì' : 'No'),
    createMeta('Titolo sorgente', documentItem.source_title),
    createMeta('Tipo sorgente', documentItem.source_document_type),
    createMeta('Stato sorgente', documentItem.source_status),
    createMeta('File disponibile', documentItem.file_present === true ? 'Sì' : 'No'),
    createMeta('Creato', formatDate(documentItem.created_at)),
    createMeta('Revocato', formatDate(documentItem.revoked_at)),
    createMeta('Archiviato', formatDate(documentItem.archived_at)),
  );
  return grid;
}

function renderDocumentDetail(documentItem) {
  el.documentDetailContent.replaceChildren();
  const title = document.createElement('h5');
  title.className = 'entity-title';
  title.textContent = documentItem.public_title || 'Documento condiviso';
  el.documentDetailContent.append(title, documentDetailMeta(documentItem));
}

async function loadDocumentDetail(documentId) {
  if (!state.authenticated || state.activeSection !== 'documents') return;
  const generation = ++state.documentDetailGeneration;
  const sectionGeneration = state.documentsGeneration;
  const sessionGeneration = state.sessionGeneration;
  el.documentDetailPanel.hidden = false;
  el.documentDetailContent.replaceChildren();
  setStatus(el.documentDetailStatus, 'Caricamento dettaglio…');
  try {
    const data = await request(`/documents/${encodeURIComponent(documentId)}`);
    if (!isCurrent('documents', sectionGeneration, sessionGeneration) || generation !== state.documentDetailGeneration) return;
    renderDocumentDetail(data || {});
    setStatus(el.documentDetailStatus, '');
  } catch (error) {
    if (!isCurrent('documents', sectionGeneration, sessionGeneration) || generation !== state.documentDetailGeneration) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setStatus(el.documentDetailStatus, statusMessage(error), 'error');
  }
}

function renderDocumentReads(items) {
  el.documentReadsContent.replaceChildren();
  if (!Array.isArray(items) || items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'state-card';
    empty.textContent = 'Nessuna lettura registrata.';
    el.documentReadsContent.append(empty);
    return;
  }
  for (const item of items) {
    const card = document.createElement('article');
    card.className = 'entity-card';
    const meta = document.createElement('div');
    meta.className = 'entity-meta';
    meta.append(
      createMeta('ID account', item.owner_account_id),
      createMeta('Prima visualizzazione', formatDate(item.first_viewed_at)),
      createMeta('Ultima visualizzazione', formatDate(item.last_viewed_at)),
      createMeta('Visualizzazioni', item.view_count),
      createMeta('Presa visione', formatDate(item.acknowledged_at)),
    );
    card.append(meta);
    el.documentReadsContent.append(card);
  }
}

async function loadDocumentReads(documentId) {
  if (!state.authenticated || state.activeSection !== 'documents') return;
  const generation = ++state.documentReadsGeneration;
  const sectionGeneration = state.documentsGeneration;
  const sessionGeneration = state.sessionGeneration;
  el.documentReadsPanel.hidden = false;
  el.documentReadsContent.replaceChildren();
  setStatus(el.documentReadsStatus, 'Caricamento letture…');
  try {
    const data = await request(`/documents/${encodeURIComponent(documentId)}/reads`);
    if (!isCurrent('documents', sectionGeneration, sessionGeneration) || generation !== state.documentReadsGeneration) return;
    renderDocumentReads(data && Array.isArray(data.items) ? data.items : []);
    setStatus(el.documentReadsStatus, '');
  } catch (error) {
    if (!isCurrent('documents', sectionGeneration, sessionGeneration) || generation !== state.documentReadsGeneration) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setStatus(el.documentReadsStatus, statusMessage(error), 'error');
  }
}

function documentEditor(documentItem, mode, onSave) {
  const form = document.createElement('form');
  form.className = 'inline-editor document-editor';
  form.setAttribute('data-document-editor', mode);

  let propertyDocument = null;
  if (mode === 'supersede') {
    const field = document.createElement('div');
    field.className = 'field';
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'number'; input.min = '1'; input.step = '1';
    input.id = `document-editor-property-${documentItem.id}`;
    label.setAttribute('for', input.id); label.textContent = 'Nuovo ID documento PROPERTY (opzionale)';
    input.value = '';
    input.setAttribute('data-field', 'property_document_id');
    field.append(label, input);
    propertyDocument = input;
    form.append(field);
  }

  const titleField = document.createElement('div');
  titleField.className = 'field';
  const titleLabel = document.createElement('label');
  const title = document.createElement('input');
  title.type = 'text'; title.maxLength = 200; title.value = documentItem.public_title || '';
  title.id = `document-editor-title-${mode}-${documentItem.id}`;
  titleLabel.setAttribute('for', title.id); titleLabel.textContent = 'Titolo pubblico';
  title.setAttribute('data-field', 'public_title');
  titleField.append(titleLabel, title);

  const typeField = document.createElement('div');
  typeField.className = 'field';
  const typeLabel = document.createElement('label');
  const type = document.createElement('select');
  type.id = `document-editor-type-${mode}-${documentItem.id}`;
  typeLabel.setAttribute('for', type.id); typeLabel.textContent = 'Tipo pubblico';
  type.setAttribute('data-field', 'public_document_type');
  for (const pair of [
    ['mandate','Mandato'],['floor_plan','Planimetria'],['ape','APE'],['cadastral_extract','Documento catastale'],
    ['photo_report','Report fotografico'],['activity_report','Report attività'],['information','Documento informativo'],
  ]) {
    const option = document.createElement('option'); option.value = pair[0]; option.textContent = pair[1]; type.append(option);
  }
  type.value = documentItem.public_document_type || 'mandate';
  typeField.append(typeLabel, type);

  const expiryField = document.createElement('div');
  expiryField.className = 'field';
  const expiryLabel = document.createElement('label');
  const expiry = document.createElement('input');
  expiry.type = 'datetime-local'; expiry.id = `document-editor-expiry-${mode}-${documentItem.id}`;
  expiryLabel.setAttribute('for', expiry.id); expiryLabel.textContent = 'Scadenza condivisione';
  expiry.setAttribute('data-field', 'expires_at');
  expiryField.append(expiryLabel, expiry);

  const createdField = document.createElement('div');
  createdField.className = 'field';
  const createdLabel = document.createElement('label');
  const created = document.createElement('input');
  created.type = 'text'; created.maxLength = 200; created.id = `document-editor-created-${mode}-${documentItem.id}`;
  createdLabel.setAttribute('for', created.id); createdLabel.textContent = 'Operatore';
  created.setAttribute('data-field', 'created_by');
  createdField.append(createdLabel, created);
  if (mode !== 'supersede') createdField.hidden = true;

  const ackField = document.createElement('div');
  ackField.className = 'field checkbox-field';
  const ack = document.createElement('input'); ack.type = 'checkbox'; ack.checked = documentItem.acknowledgement_required === true;
  ack.id = `document-editor-ack-${mode}-${documentItem.id}`; ack.setAttribute('data-field', 'acknowledgement_required');
  const ackLabel = document.createElement('label'); ackLabel.setAttribute('for', ack.id); ackLabel.textContent = 'Richiedi presa visione';
  ackField.append(ack, ackLabel);

  const actions = document.createElement('div');
  actions.className = 'editor-actions';
  const save = document.createElement('button'); save.type = 'submit'; save.className = 'button primary small'; save.textContent = mode === 'supersede' ? 'Crea nuova versione' : 'Salva modifica';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button secondary small'; cancel.textContent = 'Annulla';
  actions.append(save, cancel);
  const status = document.createElement('p'); status.className = 'status-message editor-status'; status.setAttribute('role', 'status');
  cancel.addEventListener('click', () => form.remove());
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const values = {
      propertyDocumentId: propertyDocument ? propertyDocument.value.trim() : String(documentItem.property_document_id || ''),
      ownerAccountId: documentItem.owner_account_id ? String(documentItem.owner_account_id) : '',
      publicTitle: title.value.trim(), publicType: type.value, expiresAt: expiry.value,
      acknowledgementRequired: ack.checked === true, createdBy: created.value.trim(),
    };
    const error = validateSharedDocumentValues(values, mode !== 'supersede' || Boolean(values.propertyDocumentId));
    if (error) { setStatus(status, error, 'error'); return; }
    save.disabled = true; cancel.disabled = true;
    await onSave(values, status);
    if (form.parentNode) { save.disabled = false; cancel.disabled = false; }
  });
  form.append(titleField, typeField, expiryField, createdField, ackField, actions, status);
  return form;
}

async function updateDocument(documentId, values, statusNode) {
  const key = `document:update:${documentId}`;
  if (!state.authenticated || state.mutationsInFlight.has(key)) return false;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  setStatus(statusNode, 'Salvataggio in corso…');
  try {
    await request(`/documents/${encodeURIComponent(documentId)}`, {
      method: 'PATCH',
      json: {
        public_title: values.publicTitle,
        public_document_type: values.publicType,
        expires_at: values.expiresAt ? isoFromLocal(values.expiresAt) : null,
        acknowledgement_required: values.acknowledgementRequired === true,
      },
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return false;
    setStatus(statusNode, 'Documento aggiornato.', 'success');
    if (state.activeSection === 'documents') await loadDocuments();
    return true;
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return false;
    handleApiError(error, statusNode);
    return false;
  } finally { state.mutationsInFlight.delete(key); }
}

async function supersedeDocument(documentItem, values, statusNode) {
  const key = `document:supersede:${documentItem.id}`;
  if (!state.authenticated || state.mutationsInFlight.has(key)) return false;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  setStatus(statusNode, 'Creazione nuova versione…');
  try {
    const propertyDocumentId = values.propertyDocumentId ? parsePositiveInt(values.propertyDocumentId) : null;
    await request(`/documents/${encodeURIComponent(documentItem.id)}/supersede`, {
      method: 'POST',
      json: {
        property_document_id: propertyDocumentId,
        public_title: values.publicTitle,
        public_document_type: values.publicType,
        expires_at: values.expiresAt ? isoFromLocal(values.expiresAt) : null,
        acknowledgement_required: values.acknowledgementRequired === true,
        created_by: values.createdBy || null,
      },
    });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return false;
    setStatus(statusNode, 'Nuova versione draft creata.', 'success');
    if (state.activeSection === 'documents') await loadDocuments();
    return true;
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return false;
    handleApiError(error, statusNode); return false;
  } finally { state.mutationsInFlight.delete(key); }
}

async function mutateDocument(documentId, action, actionArea, extra = null) {
  const key = `document:${action}:${documentId}`;
  if (!state.authenticated || state.mutationsInFlight.has(key)) return;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  const statusNode = document.createElement('p'); statusNode.className = 'status-message';
  actionArea.append(statusNode);
  setStatus(statusNode, action === 'publish' ? 'Pubblicazione in corso…' : action === 'revoke' ? 'Revoca in corso…' : 'Archiviazione in corso…');
  try {
    const options = { method: 'POST' };
    if (action === 'revoke') options.json = { actor: extra && extra.actor ? extra.actor : null, reason: extra && extra.reason ? extra.reason : null };
    await request(`/documents/${encodeURIComponent(documentId)}/${action}`, options);
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    setStatus(statusNode, action === 'publish' ? 'Documento pubblicato.' : action === 'revoke' ? 'Documento revocato.' : 'Documento archiviato.', 'success');
    if (state.activeSection === 'documents') await loadDocuments();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, statusNode);
  } finally { state.mutationsInFlight.delete(key); }
}

function renderRevokeDocumentConfirmation(container, documentItem) {
  container.replaceChildren();
  const form = document.createElement('form'); form.className = 'inline-editor revoke-editor'; form.setAttribute('data-document-revoke', String(documentItem.id));
  const actorField = document.createElement('div'); actorField.className = 'field';
  const actorLabel = document.createElement('label'); const actor = document.createElement('input'); actor.type='text'; actor.maxLength=200; actor.id=`document-revoke-actor-${documentItem.id}`; actorLabel.setAttribute('for',actor.id); actorLabel.textContent='Operatore'; actor.setAttribute('data-field','actor'); actorField.append(actorLabel,actor);
  const reasonField = document.createElement('div'); reasonField.className='field';
  const reasonLabel = document.createElement('label'); const reason=document.createElement('input'); reason.type='text'; reason.maxLength=500; reason.id=`document-revoke-reason-${documentItem.id}`; reasonLabel.setAttribute('for',reason.id); reasonLabel.textContent='Motivo revoca'; reason.setAttribute('data-field','reason'); reasonField.append(reasonLabel,reason);
  const warning=document.createElement('p'); warning.className='entity-copy field-span-2'; warning.textContent='La revoca rende il documento non disponibile nel portale proprietario.';
  const actions=document.createElement('div'); actions.className='editor-actions'; const submit=document.createElement('button'); submit.type='submit'; submit.className='button danger small'; submit.textContent='Conferma revoca'; const cancel=document.createElement('button'); cancel.type='button'; cancel.className='button secondary small'; cancel.textContent='Annulla'; actions.append(submit,cancel);
  const status=document.createElement('p'); status.className='status-message editor-status'; status.setAttribute('role','status');
  cancel.addEventListener('click',()=>container.replaceChildren());
  form.addEventListener('submit',async event=>{ event.preventDefault(); if (actor.value.length>200 || reason.value.length>500) { setStatus(status,'Controlla operatore e motivo della revoca.','error'); return; } submit.disabled=true; cancel.disabled=true; await mutateDocument(documentItem.id,'revoke',container,{actor:actor.value.trim(),reason:reason.value.trim()}); });
  form.append(actorField,reasonField,warning,actions,status); container.append(form);
}

function renderDocument(documentItem) {
  const card = document.createElement('article'); card.className = 'entity-card document-card'; card.setAttribute('data-document-id', String(documentItem.id ?? ''));
  const header = document.createElement('div'); header.className='entity-card-header';
  const title = document.createElement('h4'); title.className='entity-title'; title.textContent=documentItem.public_title || `Documento #${documentItem.id ?? '—'}`;
  header.append(title, sharedDocumentStatusBadge(documentItem.status));
  const meta=document.createElement('div'); meta.className='entity-meta'; meta.append(
    createMeta('ID',documentItem.id), createMeta('Immobile',documentItem.property_id), createMeta('Documento PROPERTY',documentItem.property_document_id),
    createMeta('Destinatario',documentItem.owner_account_id), createMeta('Tipo',sharedDocumentTypeLabel(documentItem.public_document_type)), createMeta('Versione',documentItem.version_number),
    createMeta('Pubblicato',formatDate(documentItem.published_at)), createMeta('Scadenza',formatDate(documentItem.expires_at)), createMeta('File',documentItem.file_present===true?'Disponibile':'Non disponibile')
  );
  const actions=document.createElement('div'); actions.className='entity-actions';
  const actionArea=document.createElement('div'); actionArea.className='action-area';
  const downloadStatus=document.createElement('p'); downloadStatus.className='status-message download-status'; downloadStatus.setAttribute('role','status');
  const detail=document.createElement('button'); detail.type='button'; detail.className='button secondary small'; detail.textContent='Dettaglio'; detail.addEventListener('click',()=>loadDocumentDetail(documentItem.id)); actions.append(detail);
  if (documentItem.file_present === true) { const download=document.createElement('button'); download.type='button'; download.className='button secondary small'; download.textContent='Download'; download.addEventListener('click',()=>downloadAdminDocument(documentItem.id,downloadStatus)); actions.append(download); }
  if (documentItem.status !== 'draft') { const reads=document.createElement('button'); reads.type='button'; reads.className='button secondary small'; reads.textContent='Letture'; reads.addEventListener('click',()=>loadDocumentReads(documentItem.id)); actions.append(reads); }
  if (documentItem.status === 'draft') {
    const edit=document.createElement('button'); edit.type='button'; edit.className='button secondary small'; edit.textContent='Modifica'; edit.addEventListener('click',()=>{ actionArea.replaceChildren(documentEditor(documentItem,'edit',(values,status)=>updateDocument(documentItem.id,values,status))); }); actions.append(edit);
    const publish=document.createElement('button'); publish.type='button'; publish.className='button primary small'; publish.textContent='Pubblica'; publish.addEventListener('click',()=>renderInlineConfirmation(actionArea,'Pubblicare questo documento nel portale proprietario?',()=>mutateDocument(documentItem.id,'publish',actionArea))); actions.append(publish);
  } else if (documentItem.status === 'published') {
    const revoke=document.createElement('button'); revoke.type='button'; revoke.className='button danger small'; revoke.textContent='Revoca'; revoke.addEventListener('click',()=>renderRevokeDocumentConfirmation(actionArea,documentItem)); actions.append(revoke);
    const archive=document.createElement('button'); archive.type='button'; archive.className='button secondary small'; archive.textContent='Archivia'; archive.addEventListener('click',()=>renderInlineConfirmation(actionArea,'Archiviare questo documento pubblicato?',()=>mutateDocument(documentItem.id,'archive',actionArea))); actions.append(archive);
    if (documentItem.superseded_by_shared_document_id === null || documentItem.superseded_by_shared_document_id === undefined) { const supersede=document.createElement('button'); supersede.type='button'; supersede.className='button secondary small'; supersede.textContent='Nuova versione'; supersede.addEventListener('click',()=>{ actionArea.replaceChildren(documentEditor(documentItem,'supersede',(values,status)=>supersedeDocument(documentItem,values,status))); }); actions.append(supersede); }
  } else if (documentItem.status === 'revoked') {
    const archive=document.createElement('button'); archive.type='button'; archive.className='button secondary small'; archive.textContent='Archivia'; archive.addEventListener('click',()=>renderInlineConfirmation(actionArea,'Archiviare questo documento revocato?',()=>mutateDocument(documentItem.id,'archive',actionArea))); actions.append(archive);
  }
  card.append(header,meta,actions,actionArea,downloadStatus); return card;
}

function renderDocuments(items) {
  el.documentsContent.replaceChildren();
  if (!Array.isArray(items) || items.length === 0) { setDocumentsState('empty'); return; }
  for (const item of items) el.documentsContent.append(renderDocument(item || {}));
  setDocumentsState('content');
}

async function loadDocuments() {
  if (!state.authenticated || state.activeSection !== 'documents') return;
  const generation=++state.documentsGeneration; state.documentDetailGeneration+=1; state.documentReadsGeneration+=1;
  const sessionGeneration=state.sessionGeneration;
  el.documentDetailPanel.hidden=true; el.documentReadsPanel.hidden=true; el.documentDetailContent.replaceChildren(); el.documentReadsContent.replaceChildren();
  setDocumentsState('loading');
  try {
    const data=await request('/documents?limit=100&offset=0');
    if (!isCurrent('documents',generation,sessionGeneration)) return;
    renderDocuments(data && Array.isArray(data.items)?data.items:[]);
  } catch(error) {
    if (!isCurrent('documents',generation,sessionGeneration)) return;
    if (error instanceof ApiError && error.status===401) { forceLoginAfterUnauthorized(); return; }
    setDocumentsState('error',statusMessage(error));
  }
}

async function submitDocumentLink(event) {
  event.preventDefault();
  const key = 'document:create-link';
  if (!state.authenticated || state.mutationsInFlight.has(key)) return;
  const referenceAccountId = parsePositiveInt(el.documentOwnerAccountId.value);
  const propertyId = parsePositiveInt(el.documentPropertyId.value);
  const propertyDocumentId = parsePositiveInt(el.documentPropertyDocumentId.value);
  if (referenceAccountId === null) { setStatus(el.documentLinkStatus, 'Seleziona un account di riferimento.', 'error'); return; }
  if (propertyId === null) { setStatus(el.documentLinkStatus, 'Seleziona un immobile OWNER-eligible.', 'error'); return; }
  if (propertyDocumentId === null) { setStatus(el.documentLinkStatus, 'Seleziona un documento PROPERTY.', 'error'); return; }
  const values = {
    propertyDocumentId: String(propertyDocumentId),
    ownerAccountId: el.documentAllAuthorized.checked ? '' : String(referenceAccountId),
    publicTitle: el.documentPublicTitle.value.trim(),
    publicType: el.documentPublicType.value,
    expiresAt: el.documentExpiresAt.value,
    acknowledgementRequired: el.documentAckRequired.checked === true,
    createdBy: el.documentCreatedBy.value.trim(),
  };
  const error = validateSharedDocumentValues(values, true);
  if (error) { setStatus(el.documentLinkStatus, error, 'error'); return; }
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  el.documentLinkSubmit.disabled = true;
  setStatus(el.documentLinkStatus, 'Creazione collegamento…');
  try {
    await request('/documents', { method: 'POST', json: sharedDocumentPayload(values, true) });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    el.documentLinkForm.reset();
    el.documentPublicType.value = 'mandate';
    resetLookupSelect(el.documentOwnerAccountId, 'Carica e seleziona un account', false);
    resetLookupSelect(el.documentPropertyId, 'Seleziona prima un account', true);
    resetLookupSelect(el.documentPropertyDocumentId, 'Seleziona prima un immobile', true);
    state.documentPropertyLookupGeneration += 1;
    state.documentSourceLookupGeneration += 1;
    setStatus(el.documentPropertyLookupStatus, '');
    setStatus(el.documentSourceLookupStatus, '');
    setStatus(el.documentLinkStatus, 'Documento collegato come draft.', 'success');
    if (state.activeSection === 'documents') await loadDocuments();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.documentLinkStatus);
  } finally {
    state.mutationsInFlight.delete(key);
    if (sessionGeneration === state.sessionGeneration) el.documentLinkSubmit.disabled = false;
  }
}

async function submitDocumentUpload(event) {
  event.preventDefault();
  const key='document:upload'; if (!state.authenticated || state.mutationsInFlight.has(key)) return;
  const file=el.documentUploadFile.files && el.documentUploadFile.files[0];
  const propertyId=parsePositiveInt(el.documentUploadPropertyId.value); const ownerAccountId=el.documentUploadOwnerAccountId.value.trim()?parsePositiveInt(el.documentUploadOwnerAccountId.value):null; const supersedesId=el.documentUploadSupersedesId.value.trim()?parsePositiveInt(el.documentUploadSupersedesId.value):null;
  const sourceType=el.documentUploadDocumentType.value.trim(); const sourceTitle=el.documentUploadSourceTitle.value.trim(); const publicTitle=el.documentUploadPublicTitle.value.trim(); const publicType=el.documentUploadPublicType.value; const expires=el.documentUploadExpiresAt.value; const createdBy=el.documentUploadCreatedBy.value.trim();
  if(!file){setStatus(el.documentUploadStatus,'Seleziona un file PDF, JPEG o PNG.','error');return;} if(propertyId===null){setStatus(el.documentUploadStatus,'Inserisci un ID immobile valido.','error');return;} if(ownerAccountId===null&&el.documentUploadOwnerAccountId.value.trim()){setStatus(el.documentUploadStatus,'Inserisci un ID account destinatario valido.','error');return;} if(supersedesId===null&&el.documentUploadSupersedesId.value.trim()){setStatus(el.documentUploadStatus,'Inserisci un ID condivisione sostituita valido.','error');return;} if(!sourceType||sourceType.length>80){setStatus(el.documentUploadStatus,'Inserisci un tipo documento sorgente da 1 a 80 caratteri.','error');return;} if(!sourceTitle||sourceTitle.length>200||!publicTitle||publicTitle.length>200){setStatus(el.documentUploadStatus,'Controlla titolo sorgente e titolo pubblico.','error');return;} if(!Object.prototype.hasOwnProperty.call(SHARED_DOCUMENT_TYPE_LABELS,publicType)){setStatus(el.documentUploadStatus,'Seleziona un tipo documento pubblico valido.','error');return;} if(expires&&!isoFromLocal(expires)){setStatus(el.documentUploadStatus,'Inserisci una scadenza valida.','error');return;} if(createdBy.length>200){setStatus(el.documentUploadStatus,'Il nome operatore non può superare 200 caratteri.','error');return;}
  const data=new FormData(); data.append('file',file); data.append('property_id',String(propertyId)); data.append('document_type',sourceType); data.append('source_title',sourceTitle); data.append('public_title',publicTitle); data.append('public_document_type',publicType); data.append('acknowledgement_required',el.documentUploadAckRequired.checked?'true':'false'); if(ownerAccountId!==null)data.append('owner_account_id',String(ownerAccountId)); if(supersedesId!==null)data.append('supersedes_shared_document_id',String(supersedesId)); if(expires)data.append('expires_at',isoFromLocal(expires)); if(createdBy)data.append('created_by',createdBy);
  state.mutationsInFlight.add(key); const sessionGeneration=state.sessionGeneration; el.documentUploadSubmit.disabled=true; setStatus(el.documentUploadStatus,'Upload in corso…');
  try { await request('/documents/upload',{method:'POST',formData:data}); if(sessionGeneration!==state.sessionGeneration||!state.authenticated)return; el.documentUploadForm.reset(); el.documentUploadPublicType.value='mandate'; setStatus(el.documentUploadStatus,'Upload completato: documento draft creato.','success'); if(state.activeSection==='documents')await loadDocuments(); }
  catch(error){ if(sessionGeneration!==state.sessionGeneration)return; handleApiError(error,el.documentUploadStatus); }
  finally { state.mutationsInFlight.delete(key); if(sessionGeneration===state.sessionGeneration)el.documentUploadSubmit.disabled=false; }
}

// OWNER 0.2 P7.3 — Feedback visite -----------------------------------------
function setVisitFeedbackState(mode,message='') {
  el.visitFeedbackLoading.hidden=mode!=='loading'; el.visitFeedbackEmpty.hidden=mode!=='empty'; el.visitFeedbackError.hidden=mode!=='error'; el.visitFeedbackContent.hidden=mode!=='content'; if(mode==='error')el.visitFeedbackErrorMessage.textContent=message;
}
function visitFeedbackCategoryLabel(value){return VISIT_FEEDBACK_CATEGORY_LABELS[value]||'Categoria non disponibile';}
function visitFeedbackSentimentLabel(value){return value?VISIT_FEEDBACK_SENTIMENT_LABELS[value]||'Valutazione non disponibile':'Non specificata';}
function visitFeedbackStatusBadge(status){return createStatusBadge(status,VISIT_FEEDBACK_STATUS_LABELS);}

function renderPrivacyResult(node,data){
  node.replaceChildren(); node.hidden=false; node.className='privacy-issues';
  if(data&&data.valid===true){node.classList.add('ok'); const p=document.createElement('p'); p.textContent='Privacy validation superata.'; node.append(p); return;}
  const title=document.createElement('p'); title.textContent='Sintesi non conforme. Correggi i punti indicati:'; node.append(title);
  const list=document.createElement('ul'); const issues=data&&Array.isArray(data.issues)?data.issues:[];
  if(issues.length===0){const li=document.createElement('li');li.textContent='Contenuto non conforme alle regole privacy.';list.append(li);} else {for(const issue of issues){const li=document.createElement('li');li.textContent=issue&&issue.message?String(issue.message):'Regola privacy non rispettata.';list.append(li);}}
  node.append(list);
}

async function validateVisitFeedbackPrivacy(summary,statusNode,issuesNode,scope='generic'){
  const key=`privacy:${scope}`; if(!state.authenticated||state.mutationsInFlight.has(key))return false; if(!summary||summary.length>5000){setStatus(statusNode,'Inserisci una sintesi da 1 a 5000 caratteri.','error');return false;}
  state.mutationsInFlight.add(key); const generation=++state.privacyGeneration; const sectionGeneration=state.visitFeedbackGeneration; const sessionGeneration=state.sessionGeneration; setStatus(statusNode,'Verifica privacy in corso…');
  try{const data=await request('/visit-feedback/validate-privacy',{method:'POST',json:{public_summary:summary}}); if(sessionGeneration!==state.sessionGeneration||generation!==state.privacyGeneration||!state.authenticated)return false; if(state.activeSection==='visit-feedback'&&sectionGeneration!==state.visitFeedbackGeneration)return false; renderPrivacyResult(issuesNode,data||{}); if(data&&data.valid===true){setStatus(statusNode,'Privacy validation superata.','success');return true;} setStatus(statusNode,'La sintesi non supera la privacy validation.','error');return false;}
  catch(error){if(sessionGeneration!==state.sessionGeneration||generation!==state.privacyGeneration)return false;if(error instanceof ApiError&&error.status===401){forceLoginAfterUnauthorized();return false;}setStatus(statusNode,statusMessage(error),'error');return false;}
  finally{state.mutationsInFlight.delete(key);}
}

function visitFeedbackDetailMeta(item){const grid=document.createElement('div');grid.className='detail-meta';grid.append(createMeta('ID',item.id),createMeta('ID visita',item.property_visit_id),createMeta('ID immobile',item.property_id),createMeta('ID account',item.owner_account_id),createMeta('Categoria',visitFeedbackCategoryLabel(item.category)),createMeta('Valutazione',visitFeedbackSentimentLabel(item.sentiment)),createMeta('Versione',item.version_number),createMeta('Stato',VISIT_FEEDBACK_STATUS_LABELS[item.status]||'Non disponibile'),createMeta('Creato',formatDate(item.created_at)),createMeta('Pubblicato',formatDate(item.published_at)),createMeta('Archiviato',formatDate(item.archived_at)));return grid;}
function renderVisitFeedbackDetail(item){el.visitFeedbackDetailContent.replaceChildren();const summary=document.createElement('p');summary.className='detail-copy';summary.textContent=item.public_summary||'—';el.visitFeedbackDetailContent.append(visitFeedbackDetailMeta(item),summary);}
async function loadVisitFeedbackDetail(id){if(!state.authenticated||state.activeSection!=='visit-feedback')return;const generation=++state.visitFeedbackDetailGeneration;const sectionGeneration=state.visitFeedbackGeneration;const sessionGeneration=state.sessionGeneration;el.visitFeedbackDetailPanel.hidden=false;el.visitFeedbackDetailContent.replaceChildren();setStatus(el.visitFeedbackDetailStatus,'Caricamento dettaglio…');try{const data=await request(`/visit-feedback/${encodeURIComponent(id)}`);if(!isCurrent('visit-feedback',sectionGeneration,sessionGeneration)||generation!==state.visitFeedbackDetailGeneration)return;renderVisitFeedbackDetail(data||{});setStatus(el.visitFeedbackDetailStatus,'');}catch(error){if(!isCurrent('visit-feedback',sectionGeneration,sessionGeneration)||generation!==state.visitFeedbackDetailGeneration)return;if(error instanceof ApiError&&error.status===401){forceLoginAfterUnauthorized();return;}setStatus(el.visitFeedbackDetailStatus,statusMessage(error),'error');}}

function visitFeedbackEditor(item,mode,onSave){
  const form=document.createElement('form');form.className='inline-editor visit-feedback-editor';form.setAttribute('data-visit-feedback-editor',mode);
  const categoryField=document.createElement('div');categoryField.className='field';const categoryLabel=document.createElement('label');const category=document.createElement('select');category.id=`vf-editor-category-${mode}-${item.id}`;categoryLabel.setAttribute('for',category.id);categoryLabel.textContent='Categoria';category.setAttribute('data-field','category');for(const pair of [['price','Posizionamento economico'],['state','Stato e presentazione'],['layout','Distribuzione degli spazi'],['location','Posizione'],['accessories','Accessori e pertinenze'],['general','Osservazione generale']]){const option=document.createElement('option');option.value=pair[0];option.textContent=pair[1];category.append(option);}category.value=item.category||'general';categoryField.append(categoryLabel,category);
  const sentimentField=document.createElement('div');sentimentField.className='field';const sentimentLabel=document.createElement('label');const sentiment=document.createElement('select');sentiment.id=`vf-editor-sentiment-${mode}-${item.id}`;sentimentLabel.setAttribute('for',sentiment.id);sentimentLabel.textContent='Valutazione';sentiment.setAttribute('data-field','sentiment');for(const pair of [['','Non specificata'],['positive','Positivo'],['neutral','Neutro'],['negative','Critico'],['mixed','Misto']]){const option=document.createElement('option');option.value=pair[0];option.textContent=pair[1];sentiment.append(option);}sentiment.value=item.sentiment||'';sentimentField.append(sentimentLabel,sentiment);
  const summaryField=document.createElement('div');summaryField.className='field field-span-2';const summaryLabel=document.createElement('label');const summary=document.createElement('textarea');summary.id=`vf-editor-summary-${mode}-${item.id}`;summaryLabel.setAttribute('for',summary.id);summaryLabel.textContent='Sintesi pubblica anonimizzata';summary.value=item.public_summary||'';summary.maxLength=5000;summary.setAttribute('rows','5');summary.setAttribute('data-field','public_summary');summaryField.append(summaryLabel,summary);
  const createdField=document.createElement('div');createdField.className='field';const createdLabel=document.createElement('label');const created=document.createElement('input');created.type='text';created.maxLength=200;created.id=`vf-editor-created-${mode}-${item.id}`;createdLabel.setAttribute('for',created.id);createdLabel.textContent='Operatore';created.setAttribute('data-field','created_by');createdField.append(createdLabel,created);if(mode!=='supersede')createdField.hidden=true;
  const actions=document.createElement('div');actions.className='editor-actions';const privacy=document.createElement('button');privacy.type='button';privacy.className='button secondary small';privacy.textContent='Verifica privacy';const save=document.createElement('button');save.type='submit';save.className='button primary small';save.textContent=mode==='supersede'?'Crea nuova versione':'Salva modifica';const cancel=document.createElement('button');cancel.type='button';cancel.className='button secondary small';cancel.textContent='Annulla';actions.append(privacy,save,cancel);const status=document.createElement('p');status.className='status-message editor-status';status.setAttribute('role','status');const issues=document.createElement('div');issues.className='privacy-issues field-span-2';issues.hidden=true;issues.setAttribute('data-privacy-issues',mode);
  privacy.addEventListener('click',()=>validateVisitFeedbackPrivacy(summary.value.trim(),status,issues,`${mode}:${item.id}:manual`));cancel.addEventListener('click',()=>form.remove());form.addEventListener('submit',async event=>{event.preventDefault();const values={category:category.value,summary:summary.value.trim(),sentiment:sentiment.value,createdBy:created.value.trim()};if(!Object.prototype.hasOwnProperty.call(VISIT_FEEDBACK_CATEGORY_LABELS,values.category)){setStatus(status,'Seleziona una categoria valida.','error');return;}if(values.sentiment&&!Object.prototype.hasOwnProperty.call(VISIT_FEEDBACK_SENTIMENT_LABELS,values.sentiment)){setStatus(status,'Seleziona una valutazione valida.','error');return;}if(!values.summary||values.summary.length>5000){setStatus(status,'Inserisci una sintesi da 1 a 5000 caratteri.','error');return;}if(values.createdBy.length>200){setStatus(status,'Il nome operatore non può superare 200 caratteri.','error');return;}save.disabled=true;privacy.disabled=true;cancel.disabled=true;const valid=await validateVisitFeedbackPrivacy(values.summary,status,issues,`${mode}:${item.id}:submit`);if(valid)await onSave(values,status);if(form.parentNode){save.disabled=false;privacy.disabled=false;cancel.disabled=false;}});form.append(categoryField,sentimentField,summaryField,createdField,actions,status,issues);return form;
}

async function updateVisitFeedback(id,values,statusNode){const key=`visit-feedback:update:${id}`;if(!state.authenticated||state.mutationsInFlight.has(key))return false;state.mutationsInFlight.add(key);const sessionGeneration=state.sessionGeneration;setStatus(statusNode,'Salvataggio in corso…');try{await request(`/visit-feedback/${encodeURIComponent(id)}`,{method:'PATCH',json:{category:values.category,public_summary:values.summary,sentiment:values.sentiment||null}});if(sessionGeneration!==state.sessionGeneration||!state.authenticated)return false;setStatus(statusNode,'Feedback visita aggiornato.','success');if(state.activeSection==='visit-feedback')await loadVisitFeedback();return true;}catch(error){if(sessionGeneration!==state.sessionGeneration)return false;handleApiError(error,statusNode);return false;}finally{state.mutationsInFlight.delete(key);}}
async function supersedeVisitFeedback(item,values,statusNode){const key=`visit-feedback:supersede:${item.id}`;if(!state.authenticated||state.mutationsInFlight.has(key))return false;state.mutationsInFlight.add(key);const sessionGeneration=state.sessionGeneration;setStatus(statusNode,'Creazione nuova versione…');try{await request(`/visit-feedback/${encodeURIComponent(item.id)}/supersede`,{method:'POST',json:{category:values.category,public_summary:values.summary,sentiment:values.sentiment||null,created_by:values.createdBy||null}});if(sessionGeneration!==state.sessionGeneration||!state.authenticated)return false;setStatus(statusNode,'Nuova versione draft creata.','success');if(state.activeSection==='visit-feedback')await loadVisitFeedback();return true;}catch(error){if(sessionGeneration!==state.sessionGeneration)return false;handleApiError(error,statusNode);return false;}finally{state.mutationsInFlight.delete(key);}}

async function mutateVisitFeedback(item,action,actionArea){const key=`visit-feedback:${action}:${item.id}`;if(!state.authenticated||state.mutationsInFlight.has(key))return;state.mutationsInFlight.add(key);const sessionGeneration=state.sessionGeneration;const status=document.createElement('p');status.className='status-message';actionArea.append(status);const issues=document.createElement('div');issues.className='privacy-issues';issues.hidden=true;actionArea.append(issues);try{if(action==='publish'){const valid=await validateVisitFeedbackPrivacy(String(item.public_summary||'').trim(),status,issues,`publish:${item.id}`);if(!valid)return;}setStatus(status,action==='publish'?'Pubblicazione in corso…':'Archiviazione in corso…');await request(`/visit-feedback/${encodeURIComponent(item.id)}/${action}`,{method:'POST'});if(sessionGeneration!==state.sessionGeneration||!state.authenticated)return;setStatus(status,action==='publish'?'Feedback visita pubblicato.':'Feedback visita archiviato.','success');if(state.activeSection==='visit-feedback')await loadVisitFeedback();}catch(error){if(sessionGeneration!==state.sessionGeneration)return;handleApiError(error,status);}finally{state.mutationsInFlight.delete(key);}}

function renderVisitFeedbackCard(item){const card=document.createElement('article');card.className='entity-card visit-feedback-card';card.setAttribute('data-visit-feedback-id',String(item.id??''));const header=document.createElement('div');header.className='entity-card-header';const title=document.createElement('h4');title.className='entity-title';title.textContent=`Feedback visita #${item.id??'—'}`;header.append(title,visitFeedbackStatusBadge(item.status));const meta=document.createElement('div');meta.className='entity-meta';meta.append(createMeta('ID visita',item.property_visit_id),createMeta('Immobile',item.property_id),createMeta('Destinatario',item.owner_account_id),createMeta('Categoria',visitFeedbackCategoryLabel(item.category)),createMeta('Valutazione',visitFeedbackSentimentLabel(item.sentiment)),createMeta('Versione',item.version_number),createMeta('Creato',formatDate(item.created_at)),createMeta('Pubblicato',formatDate(item.published_at)));const summary=document.createElement('p');summary.className='entity-copy';summary.textContent=item.public_summary||'—';const actions=document.createElement('div');actions.className='entity-actions';const actionArea=document.createElement('div');actionArea.className='action-area';const detail=document.createElement('button');detail.type='button';detail.className='button secondary small';detail.textContent='Dettaglio';detail.addEventListener('click',()=>loadVisitFeedbackDetail(item.id));actions.append(detail);if(item.status==='draft'){const edit=document.createElement('button');edit.type='button';edit.className='button secondary small';edit.textContent='Modifica';edit.addEventListener('click',()=>{actionArea.replaceChildren(visitFeedbackEditor(item,'edit',(values,status)=>updateVisitFeedback(item.id,values,status)));});actions.append(edit);const publish=document.createElement('button');publish.type='button';publish.className='button primary small';publish.textContent='Pubblica';publish.addEventListener('click',()=>renderInlineConfirmation(actionArea,'Validare la privacy e pubblicare questo feedback visita?',()=>mutateVisitFeedback(item,'publish',actionArea)));actions.append(publish);}else if(item.status==='published'){const archive=document.createElement('button');archive.type='button';archive.className='button secondary small';archive.textContent='Archivia';archive.addEventListener('click',()=>renderInlineConfirmation(actionArea,'Archiviare questo feedback visita?',()=>mutateVisitFeedback(item,'archive',actionArea)));actions.append(archive);if(item.superseded_by_feedback_publication_id===null||item.superseded_by_feedback_publication_id===undefined){const supersede=document.createElement('button');supersede.type='button';supersede.className='button secondary small';supersede.textContent='Nuova versione';supersede.addEventListener('click',()=>{actionArea.replaceChildren(visitFeedbackEditor(item,'supersede',(values,status)=>supersedeVisitFeedback(item,values,status)));});actions.append(supersede);}}card.append(header,meta,summary,actions,actionArea);return card;}
function renderVisitFeedback(items){el.visitFeedbackContent.replaceChildren();if(!Array.isArray(items)||items.length===0){setVisitFeedbackState('empty');return;}for(const item of items)el.visitFeedbackContent.append(renderVisitFeedbackCard(item||{}));setVisitFeedbackState('content');}
async function loadVisitFeedback(){if(!state.authenticated||state.activeSection!=='visit-feedback')return;const generation=++state.visitFeedbackGeneration;state.visitFeedbackDetailGeneration+=1;state.privacyGeneration+=1;const sessionGeneration=state.sessionGeneration;el.visitFeedbackDetailPanel.hidden=true;el.visitFeedbackDetailContent.replaceChildren();setVisitFeedbackState('loading');try{const data=await request('/visit-feedback?limit=50&offset=0');if(!isCurrent('visit-feedback',generation,sessionGeneration))return;renderVisitFeedback(data&&Array.isArray(data.items)?data.items:[]);}catch(error){if(!isCurrent('visit-feedback',generation,sessionGeneration))return;if(error instanceof ApiError&&error.status===401){forceLoginAfterUnauthorized();return;}setVisitFeedbackState('error',statusMessage(error));}}

async function submitVisitFeedback(event) {
  event.preventDefault();
  const key = 'visit-feedback:create';
  if (!state.authenticated || state.mutationsInFlight.has(key)) return;
  const referenceAccountId = parsePositiveInt(el.visitFeedbackOwnerAccountId.value);
  const propertyId = parsePositiveInt(el.visitFeedbackPropertyId.value);
  const visitId = parsePositiveInt(el.visitFeedbackPropertyVisitId.value);
  const category = el.visitFeedbackCategory.value;
  const sentiment = el.visitFeedbackSentiment.value;
  const summary = el.visitFeedbackSummary.value.trim();
  const createdBy = el.visitFeedbackCreatedBy.value.trim();
  if (referenceAccountId === null) { setStatus(el.visitFeedbackFormStatus, 'Seleziona un account di riferimento.', 'error'); return; }
  if (propertyId === null) { setStatus(el.visitFeedbackFormStatus, 'Seleziona un immobile OWNER-eligible.', 'error'); return; }
  if (visitId === null) { setStatus(el.visitFeedbackFormStatus, 'Seleziona una visita PROPERTY.', 'error'); return; }
  if (!Object.prototype.hasOwnProperty.call(VISIT_FEEDBACK_CATEGORY_LABELS, category)) { setStatus(el.visitFeedbackFormStatus, 'Seleziona una categoria valida.', 'error'); return; }
  if (sentiment && !Object.prototype.hasOwnProperty.call(VISIT_FEEDBACK_SENTIMENT_LABELS, sentiment)) { setStatus(el.visitFeedbackFormStatus, 'Seleziona una valutazione valida.', 'error'); return; }
  if (!summary || summary.length > 5000) { setStatus(el.visitFeedbackFormStatus, 'Inserisci una sintesi da 1 a 5000 caratteri.', 'error'); return; }
  if (createdBy.length > 200) { setStatus(el.visitFeedbackFormStatus, 'Il nome operatore non può superare 200 caratteri.', 'error'); return; }
  const targetAccountId = el.visitFeedbackAllAuthorized.checked ? null : referenceAccountId;
  state.mutationsInFlight.add(key);
  const sessionGeneration = state.sessionGeneration;
  el.visitFeedbackSubmit.disabled = true;
  el.visitFeedbackPrivacyCheck.disabled = true;
  try {
    const valid = await validateVisitFeedbackPrivacy(summary, el.visitFeedbackFormStatus, el.visitFeedbackPrivacyIssues, 'create-submit');
    if (!valid) return;
    setStatus(el.visitFeedbackFormStatus, 'Creazione draft in corso…');
    await request('/visit-feedback', { method: 'POST', json: { property_visit_id: visitId, owner_account_id: targetAccountId, category, public_summary: summary, sentiment: sentiment || null, created_by: createdBy || null } });
    if (sessionGeneration !== state.sessionGeneration || !state.authenticated) return;
    el.visitFeedbackForm.reset();
    resetLookupSelect(el.visitFeedbackOwnerAccountId, 'Carica e seleziona un account', false);
    resetLookupSelect(el.visitFeedbackPropertyId, 'Seleziona prima un account', true);
    resetLookupSelect(el.visitFeedbackPropertyVisitId, 'Seleziona prima un immobile', true);
    state.visitPropertyLookupGeneration += 1;
    state.visitSourceLookupGeneration += 1;
    el.visitFeedbackCategory.value = 'price';
    el.visitFeedbackSentiment.value = '';
    el.visitFeedbackPrivacyIssues.replaceChildren();
    el.visitFeedbackPrivacyIssues.hidden = true;
    setStatus(el.visitFeedbackPropertyLookupStatus, '');
    setStatus(el.visitFeedbackSourceLookupStatus, '');
    setStatus(el.visitFeedbackFormStatus, 'Feedback visita draft creato.', 'success');
    if (state.activeSection === 'visit-feedback') await loadVisitFeedback();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration) return;
    handleApiError(error, el.visitFeedbackFormStatus);
  } finally {
    state.mutationsInFlight.delete(key);
    if (sessionGeneration === state.sessionGeneration) {
      el.visitFeedbackSubmit.disabled = false;
      el.visitFeedbackPrivacyCheck.disabled = false;
    }
  }
}


function tokenTypeLabel(value) {
  return TOKEN_TYPE_LABELS[value] || 'Tipo non disponibile';
}

function renderOneTimeToken() {
  const secret = state.oneTimeToken;
  if (!secret || !secret.raw) {
    clearOneTimeToken(false);
    return;
  }
  el.tokenResultMeta.replaceChildren(
    createMeta('Tipo', tokenTypeLabel(secret.tokenType)),
    createMeta('Scadenza', formatDate(secret.expiresAt)),
    createMeta('ID token', secret.tokenId),
  );
  el.tokenResultValue.textContent = secret.raw;
  setStatus(el.tokenFormStatus, '');
  setStatus(el.tokenCopyStatus, '');
  el.tokenFormPanel.hidden = true;
  el.tokenResultPanel.hidden = false;
}

async function submitToken(event) {
  event.preventDefault();
  const key = 'token:create';
  if (!state.authenticated || state.mutationsInFlight.has(key)) return;

  const accountId = parsePositiveInt(el.tokenOwnerAccountId.value);
  const tokenType = el.tokenType.value;
  const expiresMinutes = Number(el.tokenExpiresMinutes.value);
  const createdBy = el.tokenCreatedBy.value.trim();
  if (accountId === null) {
    setStatus(el.tokenFormStatus, 'Seleziona un account proprietario.', 'error');
    return;
  }
  if (!Object.prototype.hasOwnProperty.call(TOKEN_TYPE_LABELS, tokenType)) {
    setStatus(el.tokenFormStatus, 'Seleziona un tipo di accesso valido.', 'error');
    return;
  }
  if (!Number.isInteger(expiresMinutes) || expiresMinutes < 5 || expiresMinutes > 1440) {
    setStatus(el.tokenFormStatus, 'La durata deve essere compresa tra 5 e 1440 minuti.', 'error');
    return;
  }

  clearOneTimeToken(false);
  const generation = ++state.tokenGeneration;
  const sessionGeneration = state.sessionGeneration;
  state.mutationsInFlight.add(key);
  el.tokenSubmit.disabled = true;
  el.tokenSubmit.textContent = 'Generazione…';
  setStatus(el.tokenFormStatus, 'Generazione in corso…');

  try {
    const data = await request(`/accounts/${encodeURIComponent(String(accountId))}/tokens`, {
      method: 'POST',
      json: {
        token_type: tokenType,
        expires_minutes: expiresMinutes,
        created_by: createdBy || null,
      },
    });
    if (!isCurrent('token-access', generation, sessionGeneration)) {
      if (data && typeof data.token === 'string') data.token = '';
      return;
    }
    if (!data || typeof data.token !== 'string' || !data.token) {
      throw new ApiError(500, 'Token response missing');
    }
    const raw = data.token;
    data.token = '';
    state.oneTimeToken = {
      raw,
      tokenType,
      tokenId: data.token_id === undefined ? null : data.token_id,
      expiresAt: data.expires_at || null,
    };
    renderOneTimeToken();
  } catch (error) {
    if (sessionGeneration !== state.sessionGeneration || generation !== state.tokenGeneration) return;
    handleApiError(error, el.tokenFormStatus);
  } finally {
    state.mutationsInFlight.delete(key);
    if (sessionGeneration === state.sessionGeneration && generation === state.tokenGeneration) {
      el.tokenSubmit.disabled = false;
      el.tokenSubmit.textContent = 'Genera token';
    }
  }
}

async function copyOneTimeToken() {
  if (!state.oneTimeToken || !state.oneTimeToken.raw) return;
  try {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') {
      throw new Error('Clipboard unavailable');
    }
    await navigator.clipboard.writeText(state.oneTimeToken.raw);
    setStatus(el.tokenCopyStatus, 'Token copiato.', 'success');
  } catch (_error) {
    setStatus(el.tokenCopyStatus, 'Copia non riuscita. Seleziona il token e copialo manualmente.', 'error');
  }
}

function auditPublicView(item) {
  return {
    created_at: item && item.created_at,
    owner_account_id: item && item.owner_account_id,
    property_id: item && item.property_id,
    action: item && item.action,
    entity_type: item && item.entity_type,
    entity_id: item && item.entity_id,
    result: item && item.result,
    metadata: item && item.metadata,
  };
}

function appendAuditMetadata(container, metadata) {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return;
  if (metadata.version_number !== undefined && metadata.version_number !== null) {
    container.append(createMeta('Versione', metadata.version_number));
  }
  if (metadata.previous !== undefined && metadata.previous !== null) {
    container.append(createMeta('Versione precedente', metadata.previous));
  }
  if (metadata.supersedes !== undefined && metadata.supersedes !== null) {
    container.append(createMeta('Sostituisce', metadata.supersedes));
  }
  if (typeof metadata.reason === 'string' && metadata.reason.trim()) {
    container.append(createMeta('Motivo', metadata.reason));
  }
}

function createAuditCard(item) {
  const audit = auditPublicView(item);
  const card = document.createElement('article');
  card.className = 'entity-card audit-card';
  card.setAttribute('role', 'listitem');

  const header = document.createElement('div');
  header.className = 'entity-card-header';
  const title = document.createElement('h4');
  title.className = 'entity-title audit-action';
  title.textContent = audit.action || 'Azione audit';
  header.append(title);
  if (audit.result) {
    const result = document.createElement('span');
    result.className = 'status-badge audit-result';
    result.textContent = String(audit.result);
    header.append(result);
  }

  const meta = document.createElement('div');
  meta.className = 'entity-meta audit-meta';
  meta.append(
    createMeta('Data', formatDate(audit.created_at)),
    createMeta('Account', audit.owner_account_id),
    createMeta('Immobile', audit.property_id),
    createMeta('Tipo entità', audit.entity_type),
    createMeta('ID entità', audit.entity_id),
  );
  appendAuditMetadata(meta, audit.metadata);
  card.append(header, meta);
  return card;
}

function setAuditState(mode, message = '') {
  el.auditLoading.hidden = mode !== 'loading';
  el.auditEmpty.hidden = mode !== 'empty';
  el.auditError.hidden = mode !== 'error';
  el.auditContent.hidden = mode !== 'content';
  if (mode === 'error') el.auditErrorMessage.textContent = message;
  else el.auditErrorMessage.textContent = '';
}

function renderAudit(items) {
  el.auditContent.replaceChildren();
  if (!items.length) {
    setAuditState('empty');
    return;
  }
  for (const item of items) el.auditContent.append(createAuditCard(item || {}));
  setAuditState('content');
}

async function loadAudit() {
  const generation = ++state.auditGeneration;
  const sessionGeneration = state.sessionGeneration;
  setAuditState('loading');
  try {
    const data = await request('/audit');
    if (!isCurrent('audit', generation, sessionGeneration)) return;
    renderAudit(data && Array.isArray(data.items) ? data.items : []);
  } catch (error) {
    if (!isCurrent('audit', generation, sessionGeneration)) return;
    if (error instanceof ApiError && error.status === 401) {
      forceLoginAfterUnauthorized();
      return;
    }
    setAuditState('error', statusMessage(error));
  }
}

function sectionConfig(name) {
  if (name === 'accounts') return { node: el.accountsSection, nav: el.navAccounts, title: 'Proprietari' };
  if (name === 'access') return { node: el.accessSection, nav: el.navAccess, title: 'Accessi' };
  if (name === 'publications') return { node: el.publicationsSection, nav: el.navPublications, title: 'Pubblicazioni' };
  if (name === 'requests') return { node: el.requestsSection, nav: el.navRequests, title: 'Richieste' };
  if (name === 'documents') return { node: el.documentsSection, nav: el.navDocuments, title: 'Documenti' };
  if (name === 'visit-feedback') return { node: el.visitFeedbackSection, nav: el.navVisitFeedback, title: 'Feedback visite' };
  if (name === 'token-access') return { node: el.tokenAccessSection, nav: el.navTokenAccess, title: 'Inviti e accessi' };
  if (name === 'audit') return { node: el.auditSection, nav: el.navAudit, title: 'Audit' };
  return { node: el.dashboardSection, nav: el.navDashboard, title: 'Dashboard' };
}

function activateSection(name, options = {}) {
  if (!state.authenticated) return;
  state.dashboardGeneration += 1;
  state.accountsGeneration += 1;
  state.accessGeneration += 1;
  state.publicationsGeneration += 1;
  state.requestsGeneration += 1;
  state.documentsGeneration += 1;
  state.documentDetailGeneration += 1;
  state.documentReadsGeneration += 1;
  state.visitFeedbackGeneration += 1;
  state.visitFeedbackDetailGeneration += 1;
  state.privacyGeneration += 1;
  state.tokenGeneration += 1;
  state.auditGeneration += 1;
  state.contactLookupGeneration += 1;
  state.accessAccountLookupGeneration += 1;
  state.accessPropertyLookupGeneration += 1;
  state.documentAccountLookupGeneration += 1;
  state.documentPropertyLookupGeneration += 1;
  state.documentSourceLookupGeneration += 1;
  state.visitAccountLookupGeneration += 1;
  state.visitPropertyLookupGeneration += 1;
  state.visitSourceLookupGeneration += 1;
  state.activeSection = name;

  for (const sectionName of ['dashboard', 'accounts', 'access', 'publications', 'requests', 'documents', 'visit-feedback', 'token-access', 'audit']) {
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
  if (name === 'documents') return Promise.all([loadDocuments(), checkDocumentStorageHealth()]);
  if (name === 'visit-feedback') return loadVisitFeedback();
  if (name === 'token-access') return Promise.resolve();
  if (name === 'audit') return loadAudit();
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
el.navDocuments.addEventListener('click', () => activateSection('documents'));
el.navVisitFeedback.addEventListener('click', () => activateSection('visit-feedback'));
el.navTokenAccess.addEventListener('click', () => activateSection('token-access'));
el.navAudit.addEventListener('click', () => activateSection('audit'));
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
el.documentsRetry.addEventListener('click', loadDocuments);
el.documentsReload.addEventListener('click', () => Promise.all([loadDocuments(), checkDocumentStorageHealth()]));
el.documentStorageHealthCheck.addEventListener('click', checkDocumentStorageHealth);
el.documentDetailClose.addEventListener('click', () => { state.documentDetailGeneration += 1; el.documentDetailPanel.hidden = true; el.documentDetailContent.replaceChildren(); setStatus(el.documentDetailStatus, ''); });
el.documentReadsClose.addEventListener('click', () => { state.documentReadsGeneration += 1; el.documentReadsPanel.hidden = true; el.documentReadsContent.replaceChildren(); setStatus(el.documentReadsStatus, ''); });
el.visitFeedbackRetry.addEventListener('click', loadVisitFeedback);
el.visitFeedbackReload.addEventListener('click', loadVisitFeedback);
el.auditRetry.addEventListener('click', loadAudit);
el.auditReload.addEventListener('click', loadAudit);
el.tokenCopy.addEventListener('click', copyOneTimeToken);
el.tokenClose.addEventListener('click', () => clearOneTimeToken(true));
el.visitFeedbackDetailClose.addEventListener('click', () => { state.visitFeedbackDetailGeneration += 1; el.visitFeedbackDetailPanel.hidden = true; el.visitFeedbackDetailContent.replaceChildren(); setStatus(el.visitFeedbackDetailStatus, ''); });
el.visitFeedbackPrivacyCheck.addEventListener('click', () => validateVisitFeedbackPrivacy(el.visitFeedbackSummary.value.trim(), el.visitFeedbackFormStatus, el.visitFeedbackPrivacyIssues, 'create-manual'));
el.accountContactSearchButton.addEventListener('click', searchAccountContacts);
el.accountContactSearch.addEventListener('input', () => {
  state.contactLookupGeneration += 1;
  resetLookupSelect(el.accountContactId, 'Avvia una nuova ricerca', true);
  setStatus(el.accountContactLookupStatus, '');
});
el.accessAccountsLoad.addEventListener('click', () => loadOwnerAccountChoices(el.accessOwnerAccountId, el.accessPropertyLookupStatus, 'accessAccountLookupGeneration'));
el.accessOwnerAccountId.addEventListener('change', () => loadEligibleProperties(el.accessOwnerAccountId, el.accessPropertyId, el.accessPropertyLookupStatus, 'accessPropertyLookupGeneration'));
el.documentAccountsLoad.addEventListener('click', () => loadOwnerAccountChoices(el.documentOwnerAccountId, el.documentPropertyLookupStatus, 'documentAccountLookupGeneration'));
el.documentOwnerAccountId.addEventListener('change', () => loadEligibleProperties(el.documentOwnerAccountId, el.documentPropertyId, el.documentPropertyLookupStatus, 'documentPropertyLookupGeneration', () => {
  state.documentSourceLookupGeneration += 1;
  resetLookupSelect(el.documentPropertyDocumentId, 'Seleziona prima un immobile', true);
  setStatus(el.documentSourceLookupStatus, '');
}));
el.documentPropertyId.addEventListener('change', loadPropertyDocumentsForAccount);
el.visitFeedbackAccountsLoad.addEventListener('click', () => loadOwnerAccountChoices(el.visitFeedbackOwnerAccountId, el.visitFeedbackPropertyLookupStatus, 'visitAccountLookupGeneration'));
el.visitFeedbackOwnerAccountId.addEventListener('change', () => loadEligibleProperties(el.visitFeedbackOwnerAccountId, el.visitFeedbackPropertyId, el.visitFeedbackPropertyLookupStatus, 'visitPropertyLookupGeneration', () => {
  state.visitSourceLookupGeneration += 1;
  resetLookupSelect(el.visitFeedbackPropertyVisitId, 'Seleziona prima un immobile', true);
  setStatus(el.visitFeedbackSourceLookupStatus, '');
}));
el.visitFeedbackPropertyId.addEventListener('change', loadPropertyVisitsForAccount);
el.accountForm.addEventListener('submit', submitAccount);
el.accessForm.addEventListener('submit', submitAccess);
el.publicationForm.addEventListener('submit', submitPublication);
el.documentLinkForm.addEventListener('submit', submitDocumentLink);
el.documentUploadForm.addEventListener('submit', submitDocumentUpload);
el.visitFeedbackForm.addEventListener('submit', submitVisitFeedback);
el.tokenForm.addEventListener('submit', submitToken);

logout('');
