<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>STIMA360 Admin</title>
  <link rel="stylesheet" href="/owner-admin/assets/app.css">
</head>
<body>
  <main class="admin-root">
    <section id="login-view" class="login-shell" aria-labelledby="login-title">
      <div class="login-card">
        <div class="brand-row">
          <div>
            <p class="eyebrow">STIMA360</p>
            <h1 id="login-title">Admin</h1>
          </div>
          <span class="release-badge">OWNER 0.2</span>
        </div>
        <p class="muted">Accesso riservato all'amministrazione OWNER.</p>
        <form id="admin-login-form" novalidate>
          <div class="field">
            <label for="admin-username">Username</label>
            <input id="admin-username" name="username" type="text" autocomplete="username" required>
          </div>
          <div class="field">
            <label for="admin-password">Password</label>
            <input id="admin-password" name="password" type="password" autocomplete="current-password" required>
          </div>
          <button id="admin-login-submit" class="button primary" type="submit">Accedi</button>
        </form>
        <p id="admin-login-status" class="status-message" role="status" aria-live="polite"></p>
      </div>
    </section>

    <section id="admin-app" class="app-shell" hidden aria-label="STIMA360 Admin OWNER">
      <aside class="sidebar">
        <div class="sidebar-brand">
          <div>
            <p class="eyebrow">STIMA360</p>
            <h1>Admin</h1>
          </div>
          <span class="release-badge">OWNER 0.2</span>
        </div>
        <nav class="admin-nav" aria-label="Navigazione OWNER Admin">
          <button id="nav-dashboard" class="nav-button" type="button" aria-controls="section-dashboard">Dashboard</button>
          <button id="nav-accounts" class="nav-button" type="button" aria-controls="section-accounts">Proprietari</button>
          <button id="nav-access" class="nav-button" type="button" aria-controls="section-access">Accessi</button>
          <button id="nav-publications" class="nav-button" type="button" aria-controls="section-publications">Pubblicazioni</button>
          <button id="nav-requests" class="nav-button" type="button" aria-controls="section-requests">Richieste</button>
          <button id="nav-documents" class="nav-button" type="button" aria-controls="section-documents">Documenti</button>
          <button id="nav-visit-feedback" class="nav-button" type="button" aria-controls="section-visit-feedback">Feedback visite</button>
          <button id="nav-token-access" class="nav-button" type="button" aria-controls="section-token-access">Inviti e accessi</button>
          <button id="nav-audit" class="nav-button" type="button" aria-controls="section-audit">Audit</button>
        </nav>
        <button id="admin-logout" class="button secondary logout-button" type="button">Esci</button>
      </aside>

      <div class="workspace">
        <header class="workspace-header">
          <div>
            <p class="eyebrow">OWNER 0.2</p>
            <h2 id="section-title">Dashboard</h2>
          </div>
          <p id="admin-global-status" class="status-message compact" role="status" aria-live="polite"></p>
        </header>

        <section id="section-dashboard" class="admin-section" aria-labelledby="dashboard-heading">
          <div class="section-heading">
            <div>
              <h3 id="dashboard-heading">Dashboard OWNER</h3>
              <p class="muted">Stato operativo delle funzioni OWNER già disponibili.</p>
            </div>
            <button id="dashboard-reload" class="button secondary" type="button">Aggiorna</button>
          </div>
          <div id="dashboard-loading" class="state-card" role="status">Caricamento dashboard…</div>
          <div id="dashboard-error" class="state-card error-state" hidden>
            <p id="dashboard-error-message"></p>
            <button id="dashboard-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="dashboard-content" class="kpi-grid" hidden aria-live="polite"></div>
        </section>

        <section id="section-accounts" class="admin-section" hidden aria-labelledby="accounts-heading">
          <div class="section-heading">
            <div>
              <h3 id="accounts-heading">Proprietari</h3>
              <p class="muted">Account OWNER collegati ai contatti esistenti.</p>
            </div>
            <button id="accounts-reload" class="button secondary" type="button">Aggiorna</button>
          </div>

          <div class="panel">
            <h4>Crea account proprietario</h4>
            <p class="muted">Cerca un contatto CORE e selezionalo senza esporre il record CRM completo.</p>
            <form id="account-create-form" class="form-grid" novalidate>
              <div class="field field-span-2">
                <label for="account-contact-search">Cerca contatto</label>
                <div class="lookup-row">
                  <input id="account-contact-search" type="search" maxlength="200" autocomplete="off" placeholder="Nome o email">
                  <button id="account-contact-search-button" class="button secondary" type="button">Cerca</button>
                </div>
                <p id="account-contact-lookup-status" class="status-message compact" role="status" aria-live="polite"></p>
              </div>
              <div class="field field-span-2">
                <label for="account-contact-id">Contatto CORE</label>
                <select id="account-contact-id" name="contact_id" required disabled>
                  <option value="">Cerca e seleziona un contatto</option>
                </select>
              </div>
              <div class="field">
                <label for="account-language">Lingua preferita</label>
                <input id="account-language" name="preferred_language" type="text" value="it" maxlength="10" required>
              </div>
              <div class="form-actions">
                <button id="account-create-submit" class="button primary" type="submit">Crea account</button>
              </div>
            </form>
            <p id="account-form-status" class="status-message" role="status" aria-live="polite"></p>
          </div>

          <div id="accounts-loading" class="state-card" hidden role="status">Caricamento proprietari…</div>
          <div id="accounts-empty" class="state-card" hidden>Nessun account proprietario disponibile.</div>
          <div id="accounts-error" class="state-card error-state" hidden>
            <p id="accounts-error-message"></p>
            <button id="accounts-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="accounts-content" class="entity-list" hidden aria-live="polite"></div>
        </section>

        <section id="section-access" class="admin-section" hidden aria-labelledby="access-heading">
          <div class="section-heading">
            <div>
              <h3 id="access-heading">Accessi immobili</h3>
              <p class="muted">Associa un account OWNER solo a immobili per cui il contatto collegato ha ruolo PROPERTY <strong>owner</strong>.</p>
            </div>
            <button id="access-reload" class="button secondary" type="button">Aggiorna</button>
          </div>

          <div class="panel">
            <h4>Nuovo accesso</h4>
            <p class="muted">Carica gli account OWNER e scegli poi uno degli immobili OWNER-eligible restituiti dal backend.</p>
            <form id="access-create-form" class="form-grid" novalidate>
              <div class="field">
                <label for="access-owner-account-id">Account proprietario</label>
                <div class="lookup-row lookup-row-select">
                  <select id="access-owner-account-id" name="owner_account_id" required>
                    <option value="">Carica e seleziona un account</option>
                  </select>
                  <button id="access-accounts-load" class="button secondary" type="button">Carica account</button>
                </div>
              </div>
              <div class="field">
                <label for="access-property-id">Immobile OWNER-eligible</label>
                <select id="access-property-id" name="property_id" required disabled>
                  <option value="">Seleziona prima un account</option>
                </select>
                <p id="access-property-lookup-status" class="status-message compact" role="status" aria-live="polite"></p>
              </div>
              <div class="field">
                <label for="access-role">Ruolo accesso</label>
                <select id="access-role" name="access_role" required>
                  <option value="owner">Proprietario</option>
                  <option value="co_owner">Comproprietario</option>
                  <option value="delegate">Delegato</option>
                  <option value="legal_representative">Rappresentante legale</option>
                </select>
              </div>
              <div class="field">
                <label for="access-valid-until">Valido fino al</label>
                <input id="access-valid-until" name="valid_until" type="datetime-local">
              </div>
              <div class="field checkbox-field">
                <input id="access-primary" name="is_primary" type="checkbox">
                <label for="access-primary">Immobile principale</label>
              </div>
              <div class="form-actions">
                <button id="access-create-submit" class="button primary" type="submit">Crea accesso</button>
              </div>
            </form>
            <p id="access-form-status" class="status-message" role="status" aria-live="polite"></p>
          </div>

          <div id="access-loading" class="state-card" hidden role="status">Caricamento accessi…</div>
          <div id="access-empty" class="state-card" hidden>Nessun accesso OWNER disponibile.</div>
          <div id="access-error" class="state-card error-state" hidden>
            <p id="access-error-message"></p>
            <button id="access-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="access-content" class="entity-list" hidden aria-live="polite"></div>
        </section>

        <section id="section-publications" class="admin-section" hidden aria-labelledby="publications-heading">
          <div class="section-heading">
            <div>
              <h3 id="publications-heading">Pubblicazioni</h3>
              <p class="muted">Gestisci gli aggiornamenti OWNER nel rispetto dello stato e del versionamento backend.</p>
            </div>
            <button id="publications-reload" class="button secondary" type="button">Aggiorna</button>
          </div>

          <div class="panel">
            <h4>Nuova pubblicazione draft</h4>
            <p class="muted">L'ID immobile viene inserito manualmente. Nessuna API PROPERTY viene chiamata.</p>
            <form id="publication-create-form" class="form-grid form-grid-wide" novalidate>
              <div class="field">
                <label for="publication-property-id">ID immobile</label>
                <input id="publication-property-id" name="property_id" type="number" min="1" step="1" inputmode="numeric" required>
              </div>
              <div class="field">
                <label for="publication-type">Tipo pubblicazione</label>
                <select id="publication-type" name="publication_type" required>
                  <option value="general_update">Aggiornamento generale</option>
                  <option value="marketing_update">Aggiornamento marketing</option>
                  <option value="visit_update">Aggiornamento visite</option>
                  <option value="feedback_summary">Sintesi feedback</option>
                  <option value="strategy_update">Aggiornamento strategia</option>
                  <option value="milestone">Traguardo</option>
                </select>
              </div>
              <div class="field field-span-2">
                <label for="publication-title">Titolo</label>
                <input id="publication-title" name="title" type="text" maxlength="200" required>
              </div>
              <div class="field field-span-2">
                <label for="publication-summary">Sintesi</label>
                <textarea id="publication-summary" name="summary" maxlength="1000" rows="3"></textarea>
              </div>
              <div class="field field-span-2">
                <label for="publication-body">Contenuto</label>
                <textarea id="publication-body" name="body" maxlength="20000" rows="7" required></textarea>
              </div>
              <div class="field checkbox-field">
                <input id="publication-ack-required" name="acknowledgement_required" type="checkbox">
                <label for="publication-ack-required">Richiedi presa visione</label>
              </div>
              <div class="form-actions">
                <button id="publication-create-submit" class="button primary" type="submit">Crea draft</button>
              </div>
            </form>
            <p id="publication-form-status" class="status-message" role="status" aria-live="polite"></p>
          </div>

          <div id="publications-loading" class="state-card" hidden role="status">Caricamento pubblicazioni…</div>
          <div id="publications-empty" class="state-card" hidden>Nessuna pubblicazione OWNER disponibile.</div>
          <div id="publications-error" class="state-card error-state" hidden>
            <p id="publications-error-message"></p>
            <button id="publications-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="publications-content" class="entity-list" hidden aria-live="polite"></div>
        </section>

        <section id="section-requests" class="admin-section" hidden aria-labelledby="requests-heading">
          <div class="section-heading">
            <div>
              <h3 id="requests-heading">Richieste</h3>
              <p class="muted">Richieste inviate dai proprietari tramite il portale OWNER.</p>
            </div>
            <button id="requests-reload" class="button secondary" type="button">Aggiorna</button>
          </div>

          <div id="requests-loading" class="state-card" hidden role="status">Caricamento richieste…</div>
          <div id="requests-empty" class="state-card" hidden>Nessuna richiesta proprietario disponibile.</div>
          <div id="requests-error" class="state-card error-state" hidden>
            <p id="requests-error-message"></p>
            <button id="requests-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="requests-content" class="entity-list" hidden aria-live="polite"></div>
        </section>

        <section id="section-documents" class="admin-section" hidden aria-labelledby="documents-heading">
          <div class="section-heading">
            <div>
              <h3 id="documents-heading">Documenti</h3>
              <p class="muted">Gestisci documenti OWNER condivisi mantenendo lo storage privato dietro il backend.</p>
            </div>
            <button id="documents-reload" class="button secondary" type="button">Aggiorna</button>
          </div>

          <div class="panel compact-panel">
            <div class="panel-heading-row">
              <div>
                <h4>Storage documentale</h4>
                <p class="muted small-copy">Verifica disponibilità del servizio senza esporre provider o locator.</p>
              </div>
              <button id="document-storage-health-check" class="button secondary small" type="button">Verifica storage</button>
            </div>
            <p id="document-storage-health-status" class="status-message" role="status" aria-live="polite"></p>
          </div>

          <div class="admin-two-column">
            <div class="panel">
              <h4>Collega documento esistente</h4>
              <p class="muted">Seleziona account, immobile OWNER-eligible e poi un documento appartenente esclusivamente a quell'immobile.</p>
              <form id="document-link-form" class="form-grid form-grid-wide" novalidate>
                <div class="field">
                  <label for="document-owner-account-id">Account di riferimento</label>
                  <div class="lookup-row lookup-row-select">
                    <select id="document-owner-account-id" required>
                      <option value="">Carica e seleziona un account</option>
                    </select>
                    <button id="document-accounts-load" class="button secondary" type="button">Carica account</button>
                  </div>
                </div>
                <div class="field">
                  <label for="document-property-id">Immobile OWNER-eligible</label>
                  <select id="document-property-id" required disabled>
                    <option value="">Seleziona prima un account</option>
                  </select>
                  <p id="document-property-lookup-status" class="status-message compact" role="status" aria-live="polite"></p>
                </div>
                <div class="field field-span-2">
                  <label for="document-property-document-id">Documento PROPERTY</label>
                  <select id="document-property-document-id" required disabled>
                    <option value="">Seleziona prima un immobile</option>
                  </select>
                  <p id="document-source-lookup-status" class="status-message compact" role="status" aria-live="polite"></p>
                </div>
                <div class="field checkbox-field field-span-2">
                  <input id="document-all-authorized" type="checkbox">
                  <label for="document-all-authorized">Condividi con tutti gli account OWNER autorizzati sull'immobile</label>
                </div>
                <div class="field field-span-2">
                  <label for="document-public-title">Titolo pubblico</label>
                  <input id="document-public-title" type="text" maxlength="200" required>
                </div>
                <div class="field">
                  <label for="document-public-type">Tipo pubblico</label>
                  <select id="document-public-type" required>
                    <option value="mandate">Mandato</option>
                    <option value="floor_plan">Planimetria</option>
                    <option value="ape">APE</option>
                    <option value="cadastral_extract">Documento catastale</option>
                    <option value="photo_report">Report fotografico</option>
                    <option value="activity_report">Report attività</option>
                    <option value="information">Documento informativo</option>
                  </select>
                </div>
                <div class="field">
                  <label for="document-expires-at">Scadenza condivisione</label>
                  <input id="document-expires-at" type="datetime-local">
                </div>
                <div class="field">
                  <label for="document-created-by">Operatore</label>
                  <input id="document-created-by" type="text" maxlength="200">
                </div>
                <div class="field checkbox-field">
                  <input id="document-ack-required" type="checkbox">
                  <label for="document-ack-required">Richiedi presa visione</label>
                </div>
                <div class="form-actions">
                  <button id="document-link-submit" class="button primary" type="submit">Crea collegamento</button>
                </div>
              </form>
              <p id="document-link-status" class="status-message" role="status" aria-live="polite"></p>
            </div>

            <div class="panel">
              <h4>Upload file privato</h4>
              <p class="muted">PDF, JPEG o PNG. Il file viene inviato al backend OWNER senza conversione base64.</p>
              <form id="document-upload-form" class="form-grid form-grid-wide" novalidate>
                <div class="field field-span-2">
                  <label for="document-upload-file">File</label>
                  <input id="document-upload-file" type="file" accept="application/pdf,image/jpeg,image/png" required>
                </div>
                <div class="field">
                  <label for="document-upload-property-id">ID immobile</label>
                  <input id="document-upload-property-id" type="number" min="1" step="1" inputmode="numeric" required>
                </div>
                <div class="field">
                  <label for="document-upload-document-type">Tipo documento sorgente</label>
                  <input id="document-upload-document-type" type="text" maxlength="80" required>
                </div>
                <div class="field field-span-2">
                  <label for="document-upload-source-title">Titolo sorgente</label>
                  <input id="document-upload-source-title" type="text" maxlength="200" required>
                </div>
                <div class="field field-span-2">
                  <label for="document-upload-public-title">Titolo pubblico</label>
                  <input id="document-upload-public-title" type="text" maxlength="200" required>
                </div>
                <div class="field">
                  <label for="document-upload-public-type">Tipo pubblico</label>
                  <select id="document-upload-public-type" required>
                    <option value="mandate">Mandato</option>
                    <option value="floor_plan">Planimetria</option>
                    <option value="ape">APE</option>
                    <option value="cadastral_extract">Documento catastale</option>
                    <option value="photo_report">Report fotografico</option>
                    <option value="activity_report">Report attività</option>
                    <option value="information">Documento informativo</option>
                  </select>
                </div>
                <div class="field">
                  <label for="document-upload-owner-account-id">ID account destinatario</label>
                  <input id="document-upload-owner-account-id" type="number" min="1" step="1" inputmode="numeric">
                </div>
                <div class="field">
                  <label for="document-upload-supersedes-id">ID condivisione sostituita</label>
                  <input id="document-upload-supersedes-id" type="number" min="1" step="1" inputmode="numeric">
                </div>
                <div class="field">
                  <label for="document-upload-expires-at">Scadenza condivisione</label>
                  <input id="document-upload-expires-at" type="datetime-local">
                </div>
                <div class="field">
                  <label for="document-upload-created-by">Operatore</label>
                  <input id="document-upload-created-by" type="text" maxlength="200">
                </div>
                <div class="field checkbox-field">
                  <input id="document-upload-ack-required" type="checkbox">
                  <label for="document-upload-ack-required">Richiedi presa visione</label>
                </div>
                <div class="form-actions">
                  <button id="document-upload-submit" class="button primary" type="submit">Carica documento</button>
                </div>
              </form>
              <p id="document-upload-status" class="status-message" role="status" aria-live="polite"></p>
            </div>
          </div>

          <div id="document-detail-panel" class="panel detail-panel" hidden aria-labelledby="document-detail-heading">
            <div class="panel-heading-row">
              <h4 id="document-detail-heading">Dettaglio documento</h4>
              <button id="document-detail-close" class="button secondary small" type="button">Chiudi</button>
            </div>
            <p id="document-detail-status" class="status-message" role="status" aria-live="polite"></p>
            <div id="document-detail-content" class="detail-content"></div>
          </div>

          <div id="document-reads-panel" class="panel detail-panel" hidden aria-labelledby="document-reads-heading">
            <div class="panel-heading-row">
              <h4 id="document-reads-heading">Letture e prese visione</h4>
              <button id="document-reads-close" class="button secondary small" type="button">Chiudi</button>
            </div>
            <p id="document-reads-status" class="status-message" role="status" aria-live="polite"></p>
            <div id="document-reads-content" class="entity-list compact-list"></div>
          </div>

          <div id="documents-loading" class="state-card" hidden role="status">Caricamento documenti…</div>
          <div id="documents-empty" class="state-card" hidden>Nessun documento condiviso disponibile.</div>
          <div id="documents-error" class="state-card error-state" hidden>
            <p id="documents-error-message"></p>
            <button id="documents-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="documents-content" class="entity-list" hidden aria-live="polite"></div>
        </section>

        <section id="section-visit-feedback" class="admin-section" hidden aria-labelledby="visit-feedback-heading">
          <div class="section-heading">
            <div>
              <h3 id="visit-feedback-heading">Feedback visite</h3>
              <p class="muted">Sintesi anonimizzate delle visite. La privacy validation OWNER resta obbligatoria.</p>
            </div>
            <button id="visit-feedback-reload" class="button secondary" type="button">Aggiorna</button>
          </div>

          <div class="panel">
            <h4>Nuovo feedback visita draft</h4>
            <p class="muted">Gli identificativi visita e account sono inseriti manualmente. Nessuna API PROPERTY viene chiamata.</p>
            <form id="visit-feedback-create-form" class="form-grid form-grid-wide" novalidate>
              <div class="field">
                <label for="visit-feedback-owner-account-id">Account di riferimento</label>
                <div class="lookup-row lookup-row-select">
                  <select id="visit-feedback-owner-account-id" required>
                    <option value="">Carica e seleziona un account</option>
                  </select>
                  <button id="visit-feedback-accounts-load" class="button secondary" type="button">Carica account</button>
                </div>
              </div>
              <div class="field">
                <label for="visit-feedback-property-id">Immobile OWNER-eligible</label>
                <select id="visit-feedback-property-id" required disabled>
                  <option value="">Seleziona prima un account</option>
                </select>
                <p id="visit-feedback-property-lookup-status" class="status-message compact" role="status" aria-live="polite"></p>
              </div>
              <div class="field field-span-2">
                <label for="visit-feedback-property-visit-id">Visita PROPERTY</label>
                <select id="visit-feedback-property-visit-id" required disabled>
                  <option value="">Seleziona prima un immobile</option>
                </select>
                <p id="visit-feedback-source-lookup-status" class="status-message compact" role="status" aria-live="polite"></p>
              </div>
              <div class="field checkbox-field field-span-2">
                <input id="visit-feedback-all-authorized" type="checkbox">
                <label for="visit-feedback-all-authorized">Pubblica per tutti gli account OWNER autorizzati sull'immobile</label>
              </div>
              <div class="field">
                <label for="visit-feedback-category">Categoria</label>
                <select id="visit-feedback-category" required>
                  <option value="price">Posizionamento economico</option>
                  <option value="state">Stato e presentazione</option>
                  <option value="layout">Distribuzione degli spazi</option>
                  <option value="location">Posizione</option>
                  <option value="accessories">Accessori e pertinenze</option>
                  <option value="general">Osservazione generale</option>
                </select>
              </div>
              <div class="field">
                <label for="visit-feedback-sentiment">Valutazione</label>
                <select id="visit-feedback-sentiment">
                  <option value="">Non specificata</option>
                  <option value="positive">Positivo</option>
                  <option value="neutral">Neutro</option>
                  <option value="negative">Critico</option>
                  <option value="mixed">Misto</option>
                </select>
              </div>
              <div class="field field-span-2">
                <label for="visit-feedback-summary">Sintesi pubblica anonimizzata</label>
                <textarea id="visit-feedback-summary" maxlength="5000" rows="5" required></textarea>
              </div>
              <div class="field">
                <label for="visit-feedback-created-by">Operatore</label>
                <input id="visit-feedback-created-by" type="text" maxlength="200">
              </div>
              <div class="form-actions grouped-actions">
                <button id="visit-feedback-privacy-check" class="button secondary" type="button">Verifica privacy</button>
                <button id="visit-feedback-create-submit" class="button primary" type="submit">Crea draft</button>
              </div>
            </form>
            <p id="visit-feedback-form-status" class="status-message" role="status" aria-live="polite"></p>
            <div id="visit-feedback-privacy-issues" class="privacy-issues" hidden aria-live="polite"></div>
          </div>

          <div id="visit-feedback-detail-panel" class="panel detail-panel" hidden aria-labelledby="visit-feedback-detail-heading">
            <div class="panel-heading-row">
              <h4 id="visit-feedback-detail-heading">Dettaglio feedback visita</h4>
              <button id="visit-feedback-detail-close" class="button secondary small" type="button">Chiudi</button>
            </div>
            <p id="visit-feedback-detail-status" class="status-message" role="status" aria-live="polite"></p>
            <div id="visit-feedback-detail-content" class="detail-content"></div>
          </div>

          <div id="visit-feedback-loading" class="state-card" hidden role="status">Caricamento feedback visite…</div>
          <div id="visit-feedback-empty" class="state-card" hidden>Nessun feedback visita disponibile.</div>
          <div id="visit-feedback-error" class="state-card error-state" hidden>
            <p id="visit-feedback-error-message"></p>
            <button id="visit-feedback-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="visit-feedback-content" class="entity-list" hidden aria-live="polite"></div>
        </section>

        <section id="section-token-access" class="admin-section" hidden aria-labelledby="token-access-heading">
          <div class="section-heading">
            <div>
              <h3 id="token-access-heading">Inviti e accessi</h3>
              <p class="muted">Genera un invito o un accesso temporaneo per un account proprietario esistente.</p>
            </div>
          </div>

          <div id="token-form-panel" class="panel token-form-panel">
            <h4>Genera credenziale temporanea</h4>
            <p class="muted">Inserisci l'ID account OWNER. Il token sarà mostrato una sola volta in questa schermata.</p>
            <form id="token-create-form" class="form-grid" novalidate>
              <div class="field">
                <label for="token-owner-account-id">ID account proprietario</label>
                <input id="token-owner-account-id" type="number" min="1" step="1" inputmode="numeric" required>
              </div>
              <div class="field">
                <label for="token-type">Tipo accesso</label>
                <select id="token-type" required>
                  <option value="invitation">Invito</option>
                  <option value="login">Accesso</option>
                </select>
              </div>
              <div class="field">
                <label for="token-expires-minutes">Durata (minuti)</label>
                <input id="token-expires-minutes" type="number" min="5" max="1440" step="1" value="30" inputmode="numeric" required>
              </div>
              <div class="field">
                <label for="token-created-by">Operatore</label>
                <input id="token-created-by" type="text">
              </div>
              <div class="form-actions">
                <button id="token-create-submit" class="button primary" type="submit">Genera token</button>
              </div>
            </form>
            <p id="token-form-status" class="status-message" role="status" aria-live="polite"></p>
          </div>

          <div id="token-result-panel" class="panel token-result-panel" hidden aria-labelledby="token-result-heading">
            <h4 id="token-result-heading">Token generato</h4>
            <p class="token-warning" role="status">Visibile solo ora. Conservalo in modo sicuro.</p>
            <div id="token-result-meta" class="entity-meta token-result-meta"></div>
            <div class="token-secret">
              <span class="meta-label">Token</span>
              <code id="token-result-value" class="token-value"></code>
            </div>
            <div class="token-result-actions">
              <button id="token-copy" class="button secondary" type="button">Copia</button>
              <button id="token-close" class="button primary" type="button">Ho copiato il token</button>
            </div>
            <p id="token-copy-status" class="status-message" role="status" aria-live="polite"></p>
          </div>
        </section>

        <section id="section-audit" class="admin-section" hidden aria-labelledby="audit-heading">
          <div class="section-heading">
            <div>
              <h3 id="audit-heading">Audit</h3>
              <p class="muted">Ultime operazioni OWNER registrate dal backend. Vista esclusivamente in lettura.</p>
            </div>
            <button id="audit-reload" class="button secondary" type="button">Aggiorna</button>
          </div>
          <div id="audit-loading" class="state-card" hidden role="status">Caricamento audit…</div>
          <div id="audit-empty" class="state-card" hidden>Nessun evento audit disponibile.</div>
          <div id="audit-error" class="state-card error-state" hidden>
            <p id="audit-error-message"></p>
            <button id="audit-retry" class="button secondary" type="button">Riprova</button>
          </div>
          <div id="audit-content" class="entity-list audit-list" hidden aria-live="polite"></div>
        </section>
      </div>
    </section>
  </main>
  <script src="/owner-admin/assets/app.js"></script>
</body>
</html>