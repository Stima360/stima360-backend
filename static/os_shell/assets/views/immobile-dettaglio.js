// STIMA360 OS — immobile-dettaglio.js
// Scheda Immobile: riusa GET /api/property/properties/{id} (property/router.py:19-20 ->
// property/repository.py:62-96, get_property) come fonte unica per Panoramica,
// Proprietari, Foto, Documenti, Visite (tutti gia' embedded nella risposta:
// p.contacts, p.photos, p.documents, p.visits). Nessuna riaggregazione lato
// frontend di dati che il backend fornisce gia'.
//
// Proposte: caricate al primo rendering insieme all'immobile, stesso pattern
// gia' usato da property_admin (static/property_admin/assets/app.js, openDetail:
// Promise.all([properties/{id}, /api/proposals?property_id=...])) — endpoint
// verificato in proposal/router.py:30-49 (property_id: int|None = Query(None,gt=0)).
//
// P9: la tab Proposte diventa operativa (creazione, modifica in bozza,
// transizioni di stato) sul contratto gia' verificato in fase di audit P9
// (proposal/router.py, proposal/repository.py). Nessuna nuova entita': fonte
// unica property_proposals. La creazione e' sempre contestuale a un
// abbinamento (match) reale gia' esistente — mai un match_id digitato
// manualmente — riusando loadMatchesLazy, la stessa funzione di rete gia'
// usata dalla tab Abbinamenti (nessun nuovo endpoint, nessuna nuova
// chiamata oltre a quella gia' presente). Le transizioni di stato rispettano
// esattamente proposal/enums.py:PROPOSAL_TRANSITIONS: nessun selettore di
// stato generico. Accettare una proposta non tocca mai
// properties.commercial_status ne' buy_requests (verificato in repository.py:
// transition_proposal — unica UPDATE su property_proposals).
//
// Abbinamenti: tab a caricamento posticipato (al primo click, non al caricamento
// iniziale) verso GET /api/match/matches?property_id={id} (match/router.py:94-104,
// property_id: int|None=None). Lettura pura (list_matches), nessun calcolo/refresh
// innescato: NON viene mai chiamato POST /api/match/properties/{id}/calculate.
//
// Acquirenti compatibili: NON esiste oggi un'API che risponda a "quali acquirenti
// sono compatibili con questo immobile" in modo pulito senza duplicare la logica
// di calcolo MATCH (che e' vietato reinvocare/reinventare qui). Si mostra un
// messaggio esplicito di rimando alla tab Abbinamenti, senza riusare gli stessi
// dati sotto un nome diverso.
//
// Attivita: core/repository.py:list_activities(limit,offset,contact_id,lead_id,
// stima_id) NON ha un parametro property_id — verificato per grep sulla firma
// reale. Nessuna relazione attivita<->immobile esiste oggi: si mostra stato
// neutro, nessuna chiamata viene fatta.
//
// Documenti: property_documents (SELECT * FROM property_documents WHERE
// property_id=%s, property/repository.py:68) non include alcun campo di
// collegamento a owner_shared_documents: lo stato di condivisione con il
// Proprietario (Owner Portal) richiederebbe una query aggiuntiva non esistente
// in questa risposta, quindi NON viene mostrato (nessun badge "condiviso"
// inventato). Nessuna azione di condivisione presente in questa vista.
//
// Proprietari (P12): collegamento/rimozione di referenti (property_contacts)
// tramite gli endpoint gia' esistenti property/router.py:25-28 (POST/DELETE
// .../contacts), nessuna creazione di contatto qui (il contatto va sempre
// scelto tra quelli CORE gia' esistenti, via GET /api/core/contacts?search=,
// stesso endpoint gia' usato in acquirenti.js e contatti.js) e nessun PATCH
// inventato (non esiste lato backend: per cambiare ruolo/quota di un
// collegamento esistente serve un task separato).
//
// Foto/Documenti (P25.6): aggiunta ed eliminazione operative tramite gli
// endpoint gia' esistenti property/router.py:33-44 (POST/DELETE
// .../documents, .../photos). IMPORTANTE: property/schemas.py::PhotoCreate.url
// e DocumentCreate.url/storage_key sono stringhe (un link a un file gia'
// ospitato altrove) — non esiste alcun endpoint di upload binario/multipart
// in questo backend (verificato: nessun UploadFile in property/router.py,
// nessun servizio di storage file altrove). "Aggiungi" qui significa quindi
// registrare il collegamento a un URL gia' esistente, mai un vero upload dal
// computer dell'agente. Nessuna "Modifica documento/foto" (solo
// aggiunta/eliminazione, come richiesto dal brief "base"): is_cover si
// imposta gia' in fase di aggiunta, il backend gestisce da solo
// l'esclusivita' (property/repository.py:199,221-224).
//
// Attivita resta a sola lettura (nessuna relazione Attivita CORE <->
// Immobile esiste oggi, vedi commento sopra).

import { apiDelete, apiGet, apiPatch, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';

const STATUS_LABELS = {
  draft: 'Bozza', evaluation: 'In valutazione', mandate: 'Mandato', active: 'Attivo',
  reserved: 'Riservato', under_offer: 'Sotto offerta', sold: 'Venduto',
  withdrawn: 'Ritirato', archived: 'Archiviato',
};

// P25.3: transizioni manuali di commercial_status disponibili in questa UI.
// 'sold' e' deliberatamente escluso (property/enums.py::PROPERTY_STATUSES lo
// contiene, ma e' raggiunto solo come side-effect di sale/repository.py::
// complete_sale - vedi soldMismatch piu' sotto, gia' trattato come debito
// noto quando disallineato: impostarlo qui a mano creerebbe lo stesso
// disallineamento). Tutte le altre transizioni sono ammesse dal backend
// senza macchina a stati (PropertyUpdate.commercial_status valida solo
// l'appartenenza a PROPERTY_STATUSES, nessun vincolo di sequenza).
const MANUAL_COMMERCIAL_STATUSES = ['draft', 'evaluation', 'mandate', 'active', 'reserved', 'under_offer', 'withdrawn', 'archived'];
// 'withdrawn' e 'archived' sono transizioni significative (un immobile
// ritirato/archiviato esce dai filtri operativi standard - vedi
// property/repository.py: mandate_expiring, KPI, alerts escludono sempre
// questi due stati): richiedono conferma inline a due click, mai
// window.confirm().
const CONFIRM_REQUIRED_STATUSES = new Set(['withdrawn', 'archived']);

const PROPERTY_ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', tenant: 'Inquilino', contact: 'Referente',
  professional: 'Professionista', other: 'Altro',
};

// P12: valori ammessi da property_contacts_role_check (migrations/002_property_01.sql:58),
// stesso ordine di PROPERTY_ROLE_LABELS sopra.
const PROPERTY_CONTACT_ROLES = ['owner', 'seller', 'tenant', 'contact', 'professional', 'other'];

const DOCUMENT_STATUS_LABELS = {
  missing: 'Mancante', requested: 'Richiesto', available: 'Disponibile',
  expired: 'Scaduto', rejected: 'Rifiutato', archived: 'Archiviato',
};

// P16: valori ammessi da VISIT_STATUSES (property/enums.py:7), stesso ordine
// gia' usato dal form legacy (static/property_admin/assets/app.js:visitForm).
const VISIT_STATUSES = ['scheduled', 'confirmed', 'completed', 'cancelled', 'no_show'];
const VISIT_STATUS_LABELS = {
  scheduled: 'Programmata', confirmed: 'Confermata', completed: 'Completata',
  cancelled: 'Annullata', no_show: 'Mancata presentazione',
};

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'proprietari', label: 'Proprietari' },
  { key: 'foto', label: 'Foto' },
  { key: 'documenti', label: 'Documenti' },
  { key: 'visite', label: 'Visite' },
  { key: 'acquirenti', label: 'Acquirenti compatibili' },
  { key: 'abbinamenti', label: 'Abbinamenti' },
  { key: 'proposte', label: 'Proposte' },
  { key: 'attivita', label: 'Attività' },
];

export async function renderImmobileDettaglio(container, params = []) {
  const propertyId = params[0];
  if (!propertyId || !/^\d+$/.test(String(propertyId))) {
    container.innerHTML = '<div class="error-box">Identificativo immobile non valido.</div>';
    return;
  }

  container.innerHTML = '<p class="muted">Caricamento scheda immobile…</p>';

  let property;
  let proposals = [];
  let sales = [];
  try {
    const [propertyData, proposalsData, salesData] = await Promise.all([
      apiGet(`/api/property/properties/${propertyId}`),
      apiGet(`/api/proposals?property_id=${propertyId}`),
      apiGet(`/api/sales?property_id=${propertyId}`),
    ]);
    property = propertyData;
    proposals = Array.isArray(proposalsData?.items) ? proposalsData.items : [];
    sales = Array.isArray(salesData?.items) ? salesData.items : [];
  } catch (error) {
    const notFound = /non trovato|not found/i.test(error.message || '');
    container.innerHTML = `<div class="error-box">${notFound ? 'Immobile non trovato.' : `Errore nel caricamento dell'immobile: ${escapeHtml(error.message)}`}</div>`;
    return;
  }

  // Cache locale per la tab Abbinamenti (caricamento posticipato al primo click).
  const lazyCache = { matches: null };

  // P9: ricarica proposte dopo creazione/modifica/transizione. Stessa fonte
  // gia' usata al caricamento iniziale (/api/proposals?property_id=...),
  // nessun nuovo endpoint.
  async function reloadProposals() {
    const data = await apiGet(`/api/proposals?property_id=${property.id}`);
    proposals = Array.isArray(data?.items) ? data.items : [];
  }

  // P11: ricarica sale dopo creazione/completamento/annullamento. Stessa
  // fonte del caricamento iniziale (/api/sales?property_id=...), nessun
  // nuovo endpoint (contratto sale/router.py:list_sales gia' verificato).
  async function reloadSales() {
    const data = await apiGet(`/api/sales?property_id=${property.id}`);
    sales = Array.isArray(data?.items) ? data.items : [];
  }

  // P11: aggiorna solo commercial_status dopo il completamento di una
  // vendita (side-effect di complete_sale su properties, sale/repository.py)
  // e riflette il nuovo badge nell'header senza reload completo pagina.
  // Best-effort: la vendita e' gia' stata completata lato backend, un
  // errore qui non deve nascondere il feedback di successo gia' mostrato.
  async function reloadPropertyStatus() {
    try {
      const updated = await apiGet(`/api/property/properties/${property.id}`);
      property.commercial_status = updated.commercial_status;
      const badgeEl = container.querySelector('#property-status-badge');
      if (badgeEl) badgeEl.innerHTML = headerBadgeHtml();
    } catch (_error) {
      // ignorato volutamente, vedi commento sopra
    }
  }

  // P12: ricarica i referenti dopo collegamento/rimozione di un
  // property_contacts. Stessa fonte del caricamento iniziale (GET
  // /api/property/properties/{id}, property/repository.py:62-96, che
  // include gia' p.contacts), nessun nuovo endpoint. Aggiorna solo
  // property.contacts, senza toccare il resto dell'oggetto property.
  async function reloadPropertyContacts() {
    const updated = await apiGet(`/api/property/properties/${property.id}`);
    property.contacts = updated.contacts;
  }

  // P16: ricarica le visite dopo creazione/modifica/eliminazione. Stessa
  // fonte del caricamento iniziale (GET /api/property/properties/{id},
  // property/repository.py:62-96 -> get_property, che include gia' p.visits
  // arricchito in sola lettura con buy_request_id/match_id derivati da
  // buy_request_interactions), nessun nuovo endpoint. Aggiorna solo
  // property.visits, senza toccare il resto dell'oggetto property.
  async function reloadPropertyVisits() {
    const updated = await apiGet(`/api/property/properties/${property.id}`);
    property.visits = updated.visits;
  }

  // P25.6: ricarica foto/documenti dopo aggiunta/eliminazione. Stessa fonte
  // del caricamento iniziale (GET /api/property/properties/{id}, che
  // include gia' p.photos/p.documents), nessun nuovo endpoint - stesso
  // principio di reloadPropertyContacts/reloadPropertyVisits sopra.
  async function reloadPropertyPhotos() {
    const updated = await apiGet(`/api/property/properties/${property.id}`);
    property.photos = updated.photos;
  }

  async function reloadPropertyDocuments() {
    const updated = await apiGet(`/api/property/properties/${property.id}`);
    property.documents = updated.documents;
  }

  // P8: stato locale di modifica per la sezione Incarico dentro Panoramica.
  // Nessuna nuova entita': mandate_type/mandate_start/mandate_end restano
  // colonne di properties, scritte tramite PATCH /api/property/properties/{id}
  // gia' esistente (property/schemas.py:PropertyUpdate).
  let incaricoEditMode = false;

  // P25.3: stato locale della sezione "Stato commerciale" in Panoramica
  // (stesso principio di incaricoEditMode sopra). commercialStatusPendingConfirm
  // e' il secondo stadio della conferma inline a due click, attivo solo
  // quando il target scelto e' in CONFIRM_REQUIRED_STATUSES.
  let commercialStatusEditMode = false;
  let commercialStatusPendingConfirm = false;

  // P11: badge di stato commerciale nell'header, isolato in una funzione
  // cosi' da poter essere ri-renderizzato dopo il completamento di una
  // vendita (reloadPropertyStatus) senza toccare il resto dell'header.
  function headerBadgeHtml() {
    return renderBadge(STATUS_LABELS[property.commercial_status] || property.commercial_status || '—', statusTone(property.commercial_status));
  }

  // P11: stato locale "conferma annullamento vendita" (secondo click prima
  // di eseguire la cancel reale). Nessun window.confirm(): pattern inline
  // pilotato da re-render, stesso principio di incaricoEditMode sopra.
  const saleCancelConfirm = new Set();

  // P12: stato locale "conferma rimozione referente" (secondo click prima
  // della DELETE reale su property_contacts). Stesso principio di
  // saleCancelConfirm sopra: chiave "{contact_id}:{role}" perche' un
  // contatto puo' comparire con piu' ruoli sullo stesso immobile.
  const contactRemoveConfirm = new Set();

  // P16: stato locale "conferma eliminazione visita" (secondo click prima
  // della DELETE reale su property_visits). Stesso principio di
  // contactRemoveConfirm sopra.
  const visitRemoveConfirm = new Set();

  // P25.6: Foto/Documenti — aggiunta/rimozione operative. IMPORTANTE:
  // property/schemas.py::PhotoCreate.url e DocumentCreate.url/storage_key
  // sono stringhe (un link a un file gia' ospitato altrove), NON esiste
  // alcun endpoint di upload binario/multipart nel backend (verificato:
  // nessun UploadFile in property/router.py, nessun servizio di storage
  // file in property/service.py o property/repository.py). "Aggiungi
  // foto/documento" qui significa quindi registrare il collegamento a un
  // URL gia' esistente, non caricare un file dal computer dell'agente -
  // questo e' esattamente cio' che il backend supporta oggi, nulla di
  // meno e nulla di piu' inventato. Stato locale toggle "+ Aggiungi" (stesso
  // principio di criteriaAddMode in acquirente-dettaglio.js) + conferma
  // inline a due click per l'eliminazione (stesso principio di
  // contactRemoveConfirm sopra).
  let photoAddMode = false;
  let documentAddMode = false;
  const photoRemoveConfirm = new Set();
  const documentRemoveConfirm = new Set();

  const title = property.title || property.code || `Immobile #${property.id}`;

  container.innerHTML = `
    <div class="contact-header card">
      <h2>${escapeHtml(title)}</h2>
      <div class="muted">Immobile #${escapeHtml(property.id)} · ${escapeHtml(property.code || '—')} · ${escapeHtml([property.address, property.city].filter(Boolean).join(', ') || '—')}</div>
      <div class="badge-row" id="property-status-badge">${headerBadgeHtml()}</div>
    </div>
    <div class="tabs" id="property-tabs"></div>
    <div id="property-tab-content" class="card panel"></div>
    <dialog id="proposal-dialog" class="modal"></dialog>
    <dialog id="sale-dialog" class="modal"></dialog>
    <dialog id="contact-dialog" class="modal"></dialog>
    <dialog id="visit-dialog" class="modal"></dialog>
  `;

  const tabsEl = container.querySelector('#property-tabs');
  tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');

  const contentEl = container.querySelector('#property-tab-content');

  async function showTab(key) {
    tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      switch (key) {
        case 'panoramica':
          contentEl.innerHTML = renderPanoramica(property, incaricoEditMode, commercialStatusEditMode, commercialStatusPendingConfirm);
          bindIncaricoSection(contentEl);
          bindCommercialStatusSection(contentEl);
          break;
        case 'proprietari': contentEl.innerHTML = renderProprietari(property.contacts, contactRemoveConfirm); bindProprietariSection(contentEl); break;
        case 'foto': contentEl.innerHTML = renderFoto(property.photos, photoAddMode, photoRemoveConfirm); bindFotoSection(contentEl); break;
        case 'documenti': contentEl.innerHTML = renderDocumenti(property.documents, documentAddMode, documentRemoveConfirm); bindDocumentiSection(contentEl); break;
        case 'visite': contentEl.innerHTML = renderVisite(property.visits, visitRemoveConfirm); bindVisiteSection(contentEl); break;
        case 'proposte': contentEl.innerHTML = renderProposte(proposals, sales, property, saleCancelConfirm); bindProposteSection(contentEl); break;
        case 'acquirenti': contentEl.innerHTML = renderAcquirentiCompatibili(); break;
        case 'attivita': contentEl.innerHTML = renderAttivita(); break;
        case 'abbinamenti': {
          const matches = await loadMatchesLazy(property.id, lazyCache);
          contentEl.innerHTML = renderAbbinamenti(matches);
          bindOpenMatchLinks(contentEl);
          break;
        }
        default: contentEl.innerHTML = '<p class="muted">Sezione non disponibile.</p>';
      }
    } catch (error) {
      contentEl.innerHTML = `<div class="error-box">Errore nel caricamento della sezione: ${escapeHtml(error.message)}</div>`;
    }
  }

  // P8: azioni della sezione Incarico (Panoramica). Nessuna chiamata al
  // caricamento pagina: la PATCH parte solo al click esplicito su "Salva".
  function bindIncaricoSection(panelEl) {
    const editBtn = panelEl.querySelector('#incarico-edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        incaricoEditMode = true;
        showTab('panoramica');
      });
    }

    const cancelBtn = panelEl.querySelector('#incarico-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        incaricoEditMode = false;
        showTab('panoramica');
      });
    }

    const saveBtn = panelEl.querySelector('#incarico-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const errorEl = panelEl.querySelector('#incarico-error');
        const typeInput = panelEl.querySelector('#incarico-type');
        const startInput = panelEl.querySelector('#incarico-start');
        const endInput = panelEl.querySelector('#incarico-end');
        if (errorEl) errorEl.textContent = '';

        const typeVal = typeInput.value.trim();
        const startVal = startInput.value;
        const endVal = endInput.value;

        // Validazione UX preventiva: il backend resta source of truth
        // (property/schemas.py:PropertyUpdate.validate_update applica la
        // stessa regola su cio' che viene effettivamente inviato).
        if (startVal && endVal && endVal < startVal) {
          if (errorEl) errorEl.textContent = 'La data di scadenza non pu\u00f2 precedere la data di inizio.';
          return;
        }

        // Solo i campi realmente modificati entrano nel payload: PropertyUpdate
        // usa exclude_unset, quindi un campo omesso resta invariato lato server,
        // mentre null lo azzera esplicitamente (verificato su property/schemas.py,
        // property/service.py:dump(p,True) e property/repository.py:update_property).
        const payload = {};
        if (typeVal !== (property.mandate_type || '')) payload.mandate_type = typeVal === '' ? null : typeVal;
        if (startVal !== toDateInputValue(property.mandate_start)) payload.mandate_start = startVal === '' ? null : startVal;
        if (endVal !== toDateInputValue(property.mandate_end)) payload.mandate_end = endVal === '' ? null : endVal;

        if (!Object.keys(payload).length) {
          incaricoEditMode = false;
          showTab('panoramica');
          return;
        }

        saveBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        saveBtn.textContent = 'Salvataggio\u2026';
        try {
          const updated = await apiPatch(`/api/property/properties/${property.id}`, payload);
          property.mandate_type = updated.mandate_type;
          property.mandate_start = updated.mandate_start;
          property.mandate_end = updated.mandate_end;
          incaricoEditMode = false;
          showTab('panoramica');
        } catch (error) {
          saveBtn.disabled = false;
          if (cancelBtn) cancelBtn.disabled = false;
          saveBtn.textContent = 'Salva';
          if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio.';
        }
      });
    }
  }

  // P25.3: sezione "Stato commerciale" in Panoramica. 'archived' passa
  // sempre da DELETE /api/property/properties/{id} (archive_property),
  // mai da una PATCH diretta - vedi commento su CONFIRM_REQUIRED_STATUSES
  // e su MANUAL_COMMERCIAL_STATUSES per il motivo. Tutte le altre
  // transizioni ammesse usano la PATCH generica gia' esistente
  // (PropertyUpdate.commercial_status).
  function bindCommercialStatusSection(panelEl) {
    const editBtn = panelEl.querySelector('#commercial-status-edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        commercialStatusEditMode = true;
        commercialStatusPendingConfirm = false;
        showTab('panoramica');
      });
    }

    const cancelBtn = panelEl.querySelector('#commercial-status-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        commercialStatusEditMode = false;
        commercialStatusPendingConfirm = false;
        showTab('panoramica');
      });
    }

    const saveBtn = panelEl.querySelector('#commercial-status-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const select = panelEl.querySelector('#commercial-status-select');
        const errorEl = panelEl.querySelector('#commercial-status-error');
        if (errorEl) errorEl.textContent = '';
        const target = select.value;

        if (target === property.commercial_status) {
          commercialStatusEditMode = false;
          commercialStatusPendingConfirm = false;
          showTab('panoramica');
          return;
        }

        if (CONFIRM_REQUIRED_STATUSES.has(target) && !commercialStatusPendingConfirm) {
          commercialStatusPendingConfirm = true;
          showTab('panoramica');
          return;
        }

        saveBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        saveBtn.textContent = 'Salvataggio…';
        try {
          const updated = target === 'archived'
            ? await apiDelete(`/api/property/properties/${property.id}`)
            : await apiPatch(`/api/property/properties/${property.id}`, { commercial_status: target });
          property.commercial_status = updated.commercial_status;
          property.archived_at = updated.archived_at;
          commercialStatusEditMode = false;
          commercialStatusPendingConfirm = false;
          const badgeEl = container.querySelector('#property-status-badge');
          if (badgeEl) badgeEl.innerHTML = headerBadgeHtml();
          showTab('panoramica');
        } catch (error) {
          commercialStatusPendingConfirm = false;
          saveBtn.disabled = false;
          if (cancelBtn) cancelBtn.disabled = false;
          saveBtn.textContent = 'Salva';
          if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio.';
        }
      });
    }
  }

  // P9: sezione Proposte (Panoramica gestisce Incarico sopra, vedi
  // bindIncaricoSection). Creazione sempre contestuale a un abbinamento
  // reale gia' esistente (mai un match_id digitato), modifica diretta solo
  // in stato draft, transizioni secondo la macchina a stati reale.
  function bindProposteSection(panelEl) {
    const newBtn = panelEl.querySelector('#proposal-new-btn');
    if (newBtn) {
      newBtn.addEventListener('click', () => { openProposalCreateDialog(); });
    }
    panelEl.querySelectorAll('.proposal-action-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const proposalId = Number(btn.dataset.proposalId);
        const action = btn.dataset.action;
        const proposal = proposals.find((p) => p.id === proposalId);
        if (!proposal) return;
        if (action === 'edit') {
          openProposalEditDialog(proposal);
        } else {
          runProposalTransition(btn, proposalId, action);
        }
      });
    });
    // P11: azioni vendita, stesso #proposal-feedback della sezione Proposte.
    panelEl.querySelectorAll('.sale-create-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const proposalId = Number(btn.dataset.proposalId);
        const proposal = proposals.find((p) => p.id === proposalId);
        if (!proposal) return;
        openSaleCreateDialog(proposal);
      });
    });
    panelEl.querySelectorAll('.sale-complete-btn').forEach((btn) => {
      btn.addEventListener('click', () => { runSaleComplete(btn, Number(btn.dataset.saleId)); });
    });
    panelEl.querySelectorAll('.sale-cancel-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        saleCancelConfirm.add(Number(btn.dataset.saleId));
        showTab('proposte');
      });
    });
    panelEl.querySelectorAll('.sale-cancel-back-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        saleCancelConfirm.delete(Number(btn.dataset.saleId));
        showTab('proposte');
      });
    });
    panelEl.querySelectorAll('.sale-cancel-confirm-btn').forEach((btn) => {
      btn.addEventListener('click', () => { runSaleCancel(btn, Number(btn.dataset.saleId)); });
    });
  }

  // P12: azioni Proprietari (property_contacts). Il collegamento e' sempre
  // verso un contatto CORE gia' esistente (mai un contact_id digitato a
  // mano, stesso principio gia' applicato al match_id delle Proposte),
  // nessuna creazione di contatto in questa vista.
  function bindProprietariSection(panelEl) {
    const linkBtn = panelEl.querySelector('#contact-link-btn');
    if (linkBtn) {
      linkBtn.addEventListener('click', () => { openContactLinkDialog(); });
    }
    panelEl.querySelectorAll('.contact-remove-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        contactRemoveConfirm.add(btn.dataset.contactKey);
        showTab('proprietari');
      });
    });
    panelEl.querySelectorAll('.contact-remove-back-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        contactRemoveConfirm.delete(btn.dataset.contactKey);
        showTab('proprietari');
      });
    });
    panelEl.querySelectorAll('.contact-remove-confirm-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        runContactRemove(btn, Number(btn.dataset.contactId), btn.dataset.role);
      });
    });
  }

  async function openProposalCreateDialog() {
    const dialogEl = container.querySelector('#proposal-dialog');
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (!dialogEl) return;
    const newBtn = contentEl.querySelector('#proposal-new-btn');
    if (newBtn) { newBtn.disabled = true; newBtn.textContent = 'Caricamento\u2026'; }
    let matches;
    try {
      matches = await loadMatchesLazy(property.id, lazyCache);
    } catch (error) {
      matches = { error: error.message };
    }
    if (newBtn) { newBtn.disabled = false; newBtn.textContent = 'Nuova proposta'; }
    if (!Array.isArray(matches)) {
      if (feedbackEl) feedbackEl.innerHTML = `<div class="error-box">Impossibile caricare gli abbinamenti per la creazione della proposta: ${escapeHtml(matches?.error || 'errore sconosciuto')}</div>`;
      return;
    }
    const openMatchIds = new Set(proposals.filter((p) => ['draft', 'submitted'].includes(p.status)).map((p) => p.match_id));
    const eligible = matches.filter((m) => !openMatchIds.has(m.id));
    if (!eligible.length) {
      if (feedbackEl) feedbackEl.innerHTML = '<div class="error-box">Nessun abbinamento disponibile: tutti gli abbinamenti di questo immobile hanno gi\u00e0 una proposta aperta, oppure non esiste ancora alcun abbinamento (vedi tab Abbinamenti).</div>';
      return;
    }
    openProposalDialog(dialogEl, { mode: 'create', eligible });
  }

  function openProposalEditDialog(proposal) {
    const dialogEl = container.querySelector('#proposal-dialog');
    if (!dialogEl) return;
    openProposalDialog(dialogEl, { mode: 'edit', proposal });
  }

  function openProposalDialog(dialogEl, opts) {
    const isEdit = opts.mode === 'edit';
    const proposal = opts.proposal || null;
    // Chiave di idempotenza stabile per l'intera vita di QUESTA apertura del
    // dialog in modalita' create: generata una sola volta qui (mai dentro
    // buildProposalCreatePayload), cosi' un retry dopo un errore di rete sullo
    // stesso dialog riusa la stessa chiave invece di generarne una nuova.
    const createIdempotencyKey = isEdit ? null : crypto.randomUUID();
    dialogEl.innerHTML = `
      <form id="proposal-form">
        <h3 class="section-title">${isEdit ? 'Modifica proposta' : 'Nuova proposta'}</h3>
        ${isEdit ? '' : `
          <div class="form-field">
            <label>Abbinamento</label>
            <select id="proposal-match" class="input" required>
              ${opts.eligible.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(matchOptionLabel(m))}</option>`).join('')}
            </select>
          </div>
        `}
        <div class="form-grid-2">
          <div class="form-field"><label>Importo (\u20ac)</label><input type="number" id="proposal-amount" class="input" min="0.01" step="0.01" required value="${proposal ? escapeHtml(proposal.amount) : ''}"></div>
          <div class="form-field"><label>Scadenza</label><input type="datetime-local" id="proposal-expiry" class="input" required value="${proposal ? proposalDateTimeLocal(proposal.expires_at) : ''}"></div>
        </div>
        <div class="form-field"><label>Note</label><textarea id="proposal-notes" class="input">${proposal ? escapeHtml(proposal.notes || '') : ''}</textarea></div>
        <div id="proposal-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="proposal-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="proposal-form-submit" class="btn primary">Salva</button>
        </div>
      </form>
    `;

    dialogEl.querySelector('#proposal-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#proposal-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#proposal-form-error');
      if (errorEl) errorEl.textContent = '';

      let payload;
      try {
        payload = isEdit ? buildProposalUpdatePayload(dialogEl) : buildProposalCreatePayload(dialogEl, createIdempotencyKey);
      } catch (error) {
        if (errorEl) errorEl.textContent = error.message || 'Dati non validi.';
        return;
      }

      submitting = true;
      const submitBtn = dialogEl.querySelector('#proposal-form-submit');
      const cancelBtn = dialogEl.querySelector('#proposal-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio\u2026';
      try {
        if (isEdit) {
          await apiPatch(`/api/proposals/${proposal.id}`, payload);
        } else {
          await apiPost('/api/proposals', payload);
        }
        dialogEl.close();
        await reloadProposals();
        showTab('proposte');
        const fb = contentEl.querySelector('#proposal-feedback');
        if (fb) fb.innerHTML = `<div class="success-box">${isEdit ? 'Proposta aggiornata.' : 'Proposta creata.'}</div>`;
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio.';
      }
    });

    dialogEl.showModal();
  }

  async function runProposalTransition(btn, proposalId, targetStatus) {
    const allButtons = Array.from(contentEl.querySelectorAll('.proposal-action-btn'));
    const originalText = btn.textContent;
    allButtons.forEach((b) => { b.disabled = true; });
    btn.textContent = 'Attendere\u2026';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/proposals/${proposalId}/transition`, { target_status: targetStatus });
      await reloadProposals();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Stato proposta aggiornato.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nella transizione di stato.')}</div>`;
    }
  }

  // P11: elenco pulsanti azione vendita/proposta usato per disabilitare tutto
  // durante una mutazione in corso, stesso principio di "allButtons" in
  // runProposalTransition sopra (nessun doppio submit).
  function allSaleAndProposalButtons() {
    return Array.from(contentEl.querySelectorAll(
      '.proposal-action-btn, .sale-create-btn, .sale-complete-btn, .sale-cancel-btn, .sale-cancel-confirm-btn, .sale-cancel-back-btn',
    ));
  }

  // Dialog "Nuova vendita", stesso pattern del dialog proposta sopra
  // (openProposalDialog): un solo campo obbligatorio (prezzo vendita,
  // precompilato con l'importo della proposta), idempotency_key generata
  // una sola volta all'apertura. property_id/buy_request_id/created_by non
  // vengono mai chiesti: sono derivati dal backend a partire da proposal_id
  // (sale/repository.py:create_sale -> _proposal_for_sale).
  function openSaleCreateDialog(proposal) {
    const dialogEl = container.querySelector('#sale-dialog');
    if (!dialogEl) return;
    const idempotencyKey = crypto.randomUUID();
    dialogEl.innerHTML = `
      <form id="sale-form">
        <h3 class="section-title">Nuova vendita</h3>
        <div class="form-field"><label>Prezzo vendita (€)</label><input type="number" id="sale-price" class="input" min="0.01" step="0.01" required value="${proposal.amount != null ? escapeHtml(proposal.amount) : ''}"></div>
        <div class="form-field"><label>Note</label><textarea id="sale-notes" class="input"></textarea></div>
        <div id="sale-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="sale-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="sale-form-submit" class="btn primary">Salva</button>
        </div>
      </form>
    `;

    dialogEl.querySelector('#sale-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#sale-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#sale-form-error');
      if (errorEl) errorEl.textContent = '';

      let salePrice;
      try {
        // Stessa validazione (numero finito, > 0) di proposalAmountValue,
        // riusata qui senza duplicare la regola.
        salePrice = proposalAmountValue(dialogEl.querySelector('#sale-price').value);
      } catch (error) {
        if (errorEl) errorEl.textContent = error.message || 'Prezzo vendita non valido.';
        return;
      }
      const notes = dialogEl.querySelector('#sale-notes').value.trim();

      submitting = true;
      const submitBtn = dialogEl.querySelector('#sale-form-submit');
      const cancelBtn = dialogEl.querySelector('#sale-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      try {
        await apiPost('/api/sales', {
          proposal_id: proposal.id,
          sale_price: salePrice,
          notes: notes || null,
          idempotency_key: idempotencyKey,
        });
        dialogEl.close();
        await reloadSales();
        showTab('proposte');
        const fb = contentEl.querySelector('#proposal-feedback');
        if (fb) fb.innerHTML = '<div class="success-box">Vendita creata.</div>';
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nella creazione della vendita.';
      }
    });

    dialogEl.showModal();
  }

  async function runSaleComplete(btn, saleId) {
    const allButtons = allSaleAndProposalButtons();
    allButtons.forEach((b) => { b.disabled = true; });
    const originalText = btn.textContent;
    btn.textContent = 'Attendere…';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/sales/${saleId}/complete`);
      await reloadSales();
      // Side-effect backend di complete_sale su properties.commercial_status
      // (sale/repository.py:complete_sale): riflesso nel badge header senza
      // reload completo pagina.
      await reloadPropertyStatus();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Vendita completata.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nel completamento della vendita.')}</div>`;
    }
  }

  // Conferma inline a due click (nessun window.confirm(), non esiste nella
  // shell): il primo click su "Annulla vendita" aggiunge il sale_id a
  // saleCancelConfirm e ri-renderizza la tab (vedi bindProposteSection),
  // il secondo click su "Conferma annullamento" esegue davvero la POST.
  async function runSaleCancel(btn, saleId) {
    const allButtons = allSaleAndProposalButtons();
    allButtons.forEach((b) => { b.disabled = true; });
    const originalText = btn.textContent;
    btn.textContent = 'Attendere…';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/sales/${saleId}/cancel`);
      saleCancelConfirm.delete(saleId);
      await reloadSales();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Vendita annullata.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nell’annullamento della vendita.')}</div>`;
    }
  }

  // P12: elenco pulsanti azione Proprietari, stesso principio di
  // allSaleAndProposalButtons sopra (nessun doppio submit durante una
  // mutazione in corso).
  function allProprietariButtons() {
    return Array.from(contentEl.querySelectorAll(
      '#contact-link-btn, .contact-remove-btn, .contact-remove-confirm-btn, .contact-remove-back-btn',
    ));
  }

  // P12: dialog "Collega contatto", stesso pattern di openSaleCreateDialog/
  // openProposalDialog sopra. Il contatto e' sempre selezionato da una
  // ricerca su contatti CORE gia' esistenti (GET /api/core/contacts?search=,
  // stesso endpoint gia' usato in acquirenti.js:263 e contatti.js:43), mai
  // un contact_id digitato a mano e mai la creazione di un nuovo contatto
  // qui. property_id e' sempre quello dell'immobile corrente, mai chiesto.
  function openContactLinkDialog() {
    const dialogEl = container.querySelector('#contact-dialog');
    if (!dialogEl) return;
    let selectedContact = null;
    let searchDebounce = null;

    dialogEl.innerHTML = `
      <form id="contact-link-form">
        <h3 class="section-title">Collega contatto</h3>
        <div class="form-field">
          <label>Contatto</label>
          <input type="search" id="contact-search-input" class="input" placeholder="Cerca per nome, email o telefono…" autocomplete="off">
          <div id="contact-search-results"></div>
          <div id="contact-selected" hidden></div>
        </div>
        <div class="form-field">
          <label>Ruolo</label>
          <select id="contact-role" class="input">
            ${PROPERTY_CONTACT_ROLES.map((r) => `<option value="${r}">${escapeHtml(PROPERTY_ROLE_LABELS[r] || r)}</option>`).join('')}
          </select>
        </div>
        <div class="form-field"><label><input type="checkbox" id="contact-is-primary"> Contatto principale</label></div>
        <div class="form-field"><label>Quota proprietà (%)</label><input type="number" id="contact-share" class="input" min="0" max="100" step="0.01"></div>
        <div class="form-field"><label>Note</label><textarea id="contact-notes" class="input"></textarea></div>
        <div id="contact-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="contact-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="contact-form-submit" class="btn primary" disabled>Salva</button>
        </div>
      </form>
    `;

    const searchInput = dialogEl.querySelector('#contact-search-input');
    const resultsEl = dialogEl.querySelector('#contact-search-results');
    const selectedEl = dialogEl.querySelector('#contact-selected');
    const submitBtn = dialogEl.querySelector('#contact-form-submit');

    function selectContact(contact) {
      selectedContact = contact;
      resultsEl.innerHTML = '';
      searchInput.value = '';
      searchInput.hidden = true;
      selectedEl.hidden = false;
      selectedEl.innerHTML = `
        <div class="selected-contact-card">
          <div><strong>${escapeHtml(contactLabel(contact))}</strong><br><small class="muted">${escapeHtml([contact.phone, contact.email].filter(Boolean).join(' · ') || '—')}</small></div>
          <button type="button" class="btn ghost" id="contact-change-btn">Cambia</button>
        </div>
      `;
      selectedEl.querySelector('#contact-change-btn').addEventListener('click', () => {
        selectedContact = null;
        selectedEl.hidden = true;
        selectedEl.innerHTML = '';
        searchInput.hidden = false;
        searchInput.value = '';
        searchInput.focus();
        submitBtn.disabled = true;
      });
      submitBtn.disabled = false;
    }

    searchInput.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      const term = searchInput.value.trim();
      if (!term) { resultsEl.innerHTML = ''; return; }
      searchDebounce = setTimeout(async () => {
        resultsEl.innerHTML = '<p class="muted">Ricerca…</p>';
        try {
          const data = await apiGet(`/api/core/contacts?search=${encodeURIComponent(term)}&limit=10`);
          const contacts = Array.isArray(data?.items) ? data.items : [];
          resultsEl.innerHTML = contacts.length
            ? `<div class="list">${contacts.map((c) => `<div class="list-item contact-result" data-contact-id="${escapeHtml(c.id)}" style="cursor:pointer"><span><strong>${escapeHtml(contactLabel(c))}</strong><br><small class="muted">${escapeHtml([c.phone, c.email].filter(Boolean).join(' · ') || '—')}</small></span></div>`).join('')}</div>`
            : '<p class="muted">Nessun contatto trovato.</p>';
          resultsEl.querySelectorAll('.contact-result').forEach((el) => {
            el.addEventListener('click', () => {
              const contact = contacts.find((c) => String(c.id) === el.dataset.contactId);
              if (contact) selectContact(contact);
            });
          });
        } catch (error) {
          resultsEl.innerHTML = `<div class="error-box">Errore nella ricerca: ${escapeHtml(error.message)}</div>`;
        }
      }, 300);
    });

    dialogEl.querySelector('#contact-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#contact-link-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#contact-form-error');
      if (errorEl) errorEl.textContent = '';

      if (!selectedContact) {
        if (errorEl) errorEl.textContent = 'Seleziona un contatto.';
        return;
      }

      let ownershipShare = null;
      const shareRaw = dialogEl.querySelector('#contact-share').value.trim();
      if (shareRaw !== '') {
        const parsed = Number(shareRaw);
        if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
          if (errorEl) errorEl.textContent = 'La quota deve essere un numero tra 0 e 100.';
          return;
        }
        ownershipShare = parsed;
      }

      const payload = {
        contact_id: selectedContact.id,
        role: dialogEl.querySelector('#contact-role').value,
        is_primary: dialogEl.querySelector('#contact-is-primary').checked,
        ownership_share: ownershipShare,
        notes: dialogEl.querySelector('#contact-notes').value.trim() || null,
      };

      submitting = true;
      const cancelBtn = dialogEl.querySelector('#contact-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      try {
        await apiPost(`/api/property/properties/${property.id}/contacts`, payload);
        dialogEl.close();
        await reloadPropertyContacts();
        showTab('proprietari');
        const fb = contentEl.querySelector('#proprietari-feedback');
        if (fb) fb.innerHTML = '<div class="success-box">Contatto collegato.</div>';
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nel collegamento del contatto.';
      }
    });

    dialogEl.showModal();
  }

  // P12: rimozione property_contacts. Conferma inline a due click (nessun
  // window.confirm(), stesso principio di runSaleCancel sopra).
  async function runContactRemove(btn, contactId, role) {
    const allButtons = allProprietariButtons();
    allButtons.forEach((b) => { b.disabled = true; });
    const originalText = btn.textContent;
    btn.textContent = 'Attendere…';
    const feedbackEl = contentEl.querySelector('#proprietari-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiDelete(`/api/property/properties/${property.id}/contacts/${contactId}/${role}`);
      contactRemoveConfirm.delete(`${contactId}:${role}`);
      await reloadPropertyContacts();
      showTab('proprietari');
      const fb = contentEl.querySelector('#proprietari-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Collegamento rimosso.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proprietari-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nella rimozione del collegamento.')}</div>`;
    }
  }

  // P16: azioni Visite (property_visits). Stesso principio del blocco
  // Proprietari sopra: nessun window.confirm(), conferma inline a due click
  // per l'eliminazione (runVisitRemove).
  function bindVisiteSection(panelEl) {
    const newBtn = panelEl.querySelector('#visit-new-btn');
    if (newBtn) {
      newBtn.addEventListener('click', () => { openVisitCreateDialog(); });
    }
    panelEl.querySelectorAll('.visit-edit-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const visitId = Number(btn.dataset.visitId);
        const visit = (property.visits || []).find((v) => v.id === visitId);
        if (visit) openVisitEditDialog(visit);
      });
    });
    panelEl.querySelectorAll('.visit-remove-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        visitRemoveConfirm.add(Number(btn.dataset.visitId));
        showTab('visite');
      });
    });
    panelEl.querySelectorAll('.visit-remove-back-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        visitRemoveConfirm.delete(Number(btn.dataset.visitId));
        showTab('visite');
      });
    });
    panelEl.querySelectorAll('.visit-remove-confirm-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        runVisitRemove(btn, Number(btn.dataset.visitId));
      });
    });
  }

  function allVisiteButtons() {
    return Array.from(contentEl.querySelectorAll(
      '#visit-new-btn, .visit-edit-btn, .visit-remove-btn, .visit-remove-confirm-btn, .visit-remove-back-btn',
    ));
  }

  // P25.6: Foto — aggiunta (POST /api/property/properties/{id}/photos,
  // PhotoCreate) ed eliminazione (DELETE /api/property/photos/{id}) a due
  // click. Nessun "imposta come copertina" separato: is_cover si imposta
  // gia' in fase di aggiunta (checkbox nel form), il backend gestisce da
  // solo l'esclusivita' (property/repository.py:199 - un solo is_cover=TRUE
  // per immobile), nessuna logica duplicata qui.
  function bindFotoSection(panelEl) {
    const toggleBtn = panelEl.querySelector('#photo-add-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        photoAddMode = true;
        showTab('foto');
      });
    }
    const cancelBtn = panelEl.querySelector('#photo-add-cancel');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        photoAddMode = false;
        showTab('foto');
      });
    }
    const form = panelEl.querySelector('#photo-add-form');
    if (form) {
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitBtn = form.querySelector('button[type="submit"]');
        const errorEl = panelEl.querySelector('#photo-add-error');
        if (errorEl) errorEl.textContent = '';
        const formData = new FormData(form);
        const url = String(formData.get('url') || '').trim();
        if (!url) {
          if (errorEl) errorEl.textContent = 'Indica l\'URL della foto.';
          return;
        }
        const payload = {
          url,
          title: String(formData.get('title') || '').trim() || null,
          sort_order: Number(formData.get('sort_order')) || 0,
          is_cover: formData.get('is_cover') === 'on',
        };
        submitBtn.disabled = true;
        submitBtn.textContent = 'Aggiunta…';
        try {
          await apiPost(`/api/property/properties/${property.id}/photos`, payload);
          photoAddMode = false;
          await reloadPropertyPhotos();
          showTab('foto');
        } catch (error) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Aggiungi';
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'aggiunta della foto.';
        }
      });
    }
    panelEl.querySelectorAll('[data-photo-remove]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const photoId = Number(btn.dataset.photoRemove);
        if (!photoRemoveConfirm.has(photoId)) {
          photoRemoveConfirm.add(photoId);
          showTab('foto');
          return;
        }
        btn.disabled = true;
        btn.textContent = 'Eliminazione…';
        try {
          await apiDelete(`/api/property/photos/${photoId}`);
          photoRemoveConfirm.delete(photoId);
          await reloadPropertyPhotos();
          showTab('foto');
        } catch (error) {
          photoRemoveConfirm.delete(photoId);
          btn.disabled = false;
          btn.textContent = 'Elimina';
          const errorEl = panelEl.querySelector('#photo-add-error');
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'eliminazione della foto.';
        }
      });
    });
  }

  // P25.6: Documenti — aggiunta (POST /api/property/properties/{id}/documents,
  // DocumentCreate) ed eliminazione (DELETE /api/property/documents/{id}) a
  // due click. Nessuna "Modifica" qui: il brief P25.6 chiede esplicitamente
  // solo upload/view/delete (base), non un editor completo del documento.
  function bindDocumentiSection(panelEl) {
    const toggleBtn = panelEl.querySelector('#document-add-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        documentAddMode = true;
        showTab('documenti');
      });
    }
    const cancelBtn = panelEl.querySelector('#document-add-cancel');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        documentAddMode = false;
        showTab('documenti');
      });
    }
    const form = panelEl.querySelector('#document-add-form');
    if (form) {
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitBtn = form.querySelector('button[type="submit"]');
        const errorEl = panelEl.querySelector('#document-add-error');
        if (errorEl) errorEl.textContent = '';
        const formData = new FormData(form);
        const documentType = String(formData.get('document_type') || '').trim();
        const docTitle = String(formData.get('title') || '').trim();
        if (!documentType || !docTitle) {
          if (errorEl) errorEl.textContent = 'Tipo e titolo del documento sono obbligatori.';
          return;
        }
        const url = String(formData.get('url') || '').trim();
        const status = formData.get('status');
        const payload = {
          document_type: documentType,
          title: docTitle,
          url: url || null,
          status,
          expires_at: formData.get('expires_at') || null,
          notes: String(formData.get('notes') || '').trim() || null,
        };
        submitBtn.disabled = true;
        submitBtn.textContent = 'Aggiunta…';
        try {
          await apiPost(`/api/property/properties/${property.id}/documents`, payload);
          documentAddMode = false;
          await reloadPropertyDocuments();
          showTab('documenti');
        } catch (error) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Aggiungi';
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'aggiunta del documento.';
        }
      });
    }
    panelEl.querySelectorAll('[data-document-remove]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const documentId = Number(btn.dataset.documentRemove);
        if (!documentRemoveConfirm.has(documentId)) {
          documentRemoveConfirm.add(documentId);
          showTab('documenti');
          return;
        }
        btn.disabled = true;
        btn.textContent = 'Eliminazione…';
        try {
          await apiDelete(`/api/property/documents/${documentId}`);
          documentRemoveConfirm.delete(documentId);
          await reloadPropertyDocuments();
          showTab('documenti');
        } catch (error) {
          documentRemoveConfirm.delete(documentId);
          btn.disabled = false;
          btn.textContent = 'Elimina';
          const errorEl = panelEl.querySelector('#document-add-error');
          if (errorEl) errorEl.textContent = error.message || 'Errore nell\'eliminazione del documento.';
        }
      });
    });
  }

  function openVisitCreateDialog() {
    const dialogEl = container.querySelector('#visit-dialog');
    if (!dialogEl) return;
    openVisitDialog(dialogEl, { mode: 'create' });
  }

  function openVisitEditDialog(visit) {
    const dialogEl = container.querySelector('#visit-dialog');
    if (!dialogEl) return;
    openVisitDialog(dialogEl, { mode: 'edit', visit });
  }

  // P16: dialog unico creazione/modifica visita (property_visits), stesso
  // pattern di openProposalDialog sopra. Campi limitati a quelli reali di
  // VisitCreate/VisitUpdate (property/schemas.py:164-192): scheduled_at,
  // status (VISIT_STATUSES, property/enums.py), contact_id, lead_id,
  // outcome, rating, feedback, assigned_to. Nessun campo match_id: non
  // esiste sullo schema (property_visits non ha una colonna match_id — il
  // collegamento con un abbinamento e' derivato in sola lettura da
  // buy_request_interactions, dominio BUY non modificabile qui). Il
  // contatto e' sempre scelto da una ricerca sui contatti CORE gia'
  // esistenti (stesso endpoint e stessa logica di openContactLinkDialog
  // sopra, GET /api/core/contacts?search=), mai un contact_id digitato a
  // mano. Il lead e' scelto tra i lead gia' collegati a questo immobile
  // (property.leads, gia' incluso nella risposta di get_property, nessuna
  // nuova chiamata): non esiste oggi in os_shell un selettore lead
  // riusabile piu' ampio, e non e' nello scope di P16 introdurne uno.
  function openVisitDialog(dialogEl, opts) {
    const isEdit = opts.mode === 'edit';
    const visit = opts.visit || null;
    let selectedContact = visit && visit.contact_id
      ? { id: visit.contact_id, display_name: visit.contact_name || `Contatto #${visit.contact_id}` }
      : null;
    let searchDebounce = null;

    const leadOptions = (property.leads || []).map((l) => `<option value="${escapeHtml(l.lead_id)}" ${visit && visit.lead_id === l.lead_id ? 'selected' : ''}>Lead #${escapeHtml(l.lead_id)}${l.stage ? ` \u2014 ${escapeHtml(l.stage)}` : ''}</option>`).join('');

    dialogEl.innerHTML = `
      <form id="visit-form">
        <h3 class="section-title">${isEdit ? 'Aggiorna visita' : 'Nuova visita'}</h3>
        <div class="form-grid-2">
          <div class="form-field"><label>Data e ora *</label><input type="datetime-local" id="visit-scheduled-at" class="input" required value="${visit ? visitDateTimeLocal(visit.scheduled_at) : ''}"></div>
          <div class="form-field">
            <label>Stato</label>
            <select id="visit-status" class="input">
              ${VISIT_STATUSES.map((s) => `<option value="${s}" ${(visit ? visit.status : 'scheduled') === s ? 'selected' : ''}>${escapeHtml(VISIT_STATUS_LABELS[s] || s)}</option>`).join('')}
            </select>
          </div>
        </div>
        <div class="form-field">
          <label>Contatto (acquirente/visitatore)</label>
          <input type="search" id="visit-contact-search-input" class="input" placeholder="Cerca per nome, email o telefono…" autocomplete="off" ${selectedContact ? 'hidden' : ''}>
          <div id="visit-contact-search-results"></div>
          <div id="visit-contact-selected" ${selectedContact ? '' : 'hidden'}></div>
        </div>
        ${leadOptions ? `
        <div class="form-field">
          <label>Lead collegato</label>
          <select id="visit-lead" class="input">
            <option value="">—</option>
            ${leadOptions}
          </select>
        </div>` : ''}
        <div class="form-grid-2">
          <div class="form-field"><label>Esito</label><input type="text" id="visit-outcome" class="input" maxlength="80" value="${visit ? escapeHtml(visit.outcome || '') : ''}"></div>
          <div class="form-field"><label>Valutazione (1-5)</label><input type="number" id="visit-rating" class="input" min="1" max="5" step="1" value="${visit && visit.rating != null ? escapeHtml(visit.rating) : ''}"></div>
        </div>
        <div class="form-field"><label>Assegnata a</label><input type="text" id="visit-assigned-to" class="input" value="${visit ? escapeHtml(visit.assigned_to || '') : ''}"></div>
        <div class="form-field"><label>Feedback / note</label><textarea id="visit-feedback" class="input">${visit ? escapeHtml(visit.feedback || '') : ''}</textarea></div>
        <div id="visit-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="visit-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="visit-form-submit" class="btn primary">Salva</button>
        </div>
      </form>
    `;

    const searchInput = dialogEl.querySelector('#visit-contact-search-input');
    const resultsEl = dialogEl.querySelector('#visit-contact-search-results');
    const selectedEl = dialogEl.querySelector('#visit-contact-selected');

    function renderSelectedContact() {
      if (!selectedContact) { selectedEl.hidden = true; selectedEl.innerHTML = ''; return; }
      selectedEl.hidden = false;
      selectedEl.innerHTML = `
        <div class="selected-contact-card">
          <div><strong>${escapeHtml(selectedContact.display_name)}</strong></div>
          <button type="button" class="btn ghost" id="visit-contact-change-btn">Cambia</button>
        </div>
      `;
      selectedEl.querySelector('#visit-contact-change-btn').addEventListener('click', () => {
        selectedContact = null;
        renderSelectedContact();
        searchInput.hidden = false;
        searchInput.value = '';
        searchInput.focus();
      });
    }
    function selectContact(contact) {
      selectedContact = contact;
      resultsEl.innerHTML = '';
      searchInput.value = '';
      searchInput.hidden = true;
      renderSelectedContact();
    }
    renderSelectedContact();

    searchInput.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      const term = searchInput.value.trim();
      if (!term) { resultsEl.innerHTML = ''; return; }
      searchDebounce = setTimeout(async () => {
        resultsEl.innerHTML = '<p class="muted">Ricerca…</p>';
        try {
          const data = await apiGet(`/api/core/contacts?search=${encodeURIComponent(term)}&limit=10`);
          const contacts = Array.isArray(data?.items) ? data.items : [];
          resultsEl.innerHTML = contacts.length
            ? `<div class="list">${contacts.map((c) => `<div class="list-item visit-contact-result" data-contact-id="${escapeHtml(c.id)}" style="cursor:pointer"><span><strong>${escapeHtml(contactLabel(c))}</strong><br><small class="muted">${escapeHtml([c.phone, c.email].filter(Boolean).join(' · ') || '—')}</small></span></div>`).join('')}</div>`
            : '<p class="muted">Nessun contatto trovato.</p>';
          resultsEl.querySelectorAll('.visit-contact-result').forEach((el) => {
            el.addEventListener('click', () => {
              const contact = contacts.find((c) => String(c.id) === el.dataset.contactId);
              if (contact) selectContact({ id: contact.id, display_name: contactLabel(contact) });
            });
          });
        } catch (error) {
          resultsEl.innerHTML = `<div class="error-box">Errore nella ricerca: ${escapeHtml(error.message)}</div>`;
        }
      }, 300);
    });

    dialogEl.querySelector('#visit-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#visit-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#visit-form-error');
      if (errorEl) errorEl.textContent = '';

      let payload;
      try {
        payload = buildVisitPayload(dialogEl, selectedContact, isEdit);
      } catch (error) {
        if (errorEl) errorEl.textContent = error.message || 'Dati non validi.';
        return;
      }

      submitting = true;
      const submitBtn = dialogEl.querySelector('#visit-form-submit');
      const cancelBtn = dialogEl.querySelector('#visit-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      try {
        if (isEdit) {
          await apiPatch(`/api/property/visits/${visit.id}`, payload);
        } else {
          await apiPost(`/api/property/properties/${property.id}/visits`, payload);
        }
        dialogEl.close();
        await reloadPropertyVisits();
        showTab('visite');
        const fb = contentEl.querySelector('#visite-feedback');
        if (fb) fb.innerHTML = `<div class="success-box">${isEdit ? 'Visita aggiornata.' : 'Visita creata.'}</div>`;
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio della visita.';
      }
    });

    dialogEl.showModal();
  }

  // P16: eliminazione property_visits (DELETE /api/property/visits/{id},
  // property/router.py:52 -> service.delete_visit -> repository.delete_child,
  // gia' verificato in fase di audit). Conferma inline a due click, stesso
  // principio di runContactRemove sopra.
  async function runVisitRemove(btn, visitId) {
    const allButtons = allVisiteButtons();
    allButtons.forEach((b) => { b.disabled = true; });
    const originalText = btn.textContent;
    btn.textContent = 'Attendere…';
    const feedbackEl = contentEl.querySelector('#visite-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiDelete(`/api/property/visits/${visitId}`);
      visitRemoveConfirm.delete(visitId);
      await reloadPropertyVisits();
      showTab('visite');
      const fb = contentEl.querySelector('#visite-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Visita eliminata.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#visite-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nell\'eliminazione della visita.')}</div>`;
    }
  }

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  await showTab('panoramica');
}

// --- Panoramica -------------------------------------------------------

function renderPanoramica(p, editMode, commercialStatusEditMode, commercialStatusPendingConfirm) {
  const fields = [
    ['Tipologia', p.property_type], ['Classificazione', p.classification],
    ['Indirizzo', [p.address, p.civic_number].filter(Boolean).join(' ')],
    ['Comune', p.city], ['Provincia', p.province], ['CAP', p.postal_code], ['Microzona', p.microzone],
    ['Superficie (mq)', p.surface_sqm], ['Superficie commerciale (mq)', p.commercial_surface_sqm],
    ['Locali', p.rooms], ['Camere', p.bedrooms], ['Bagni', p.bathrooms],
    ['Piano', p.floor], ['Piani totali', p.total_floors],
    ['Ascensore', p.elevator === null || p.elevator === undefined ? null : (p.elevator ? 'Sì' : 'No')],
    ['Anno costruzione', p.year_built], ['Condizione', p.condition], ['Classe energetica', p.energy_class],
    ['Prezzo richiesto', formatPrice(p.asking_price)], ['Prezzo minimo', formatPrice(p.minimum_price)],
    ['Assegnato a', p.assigned_to], ['Fonte', p.source],
    ['Punteggio completezza', p.readiness_score != null ? `${p.readiness_score}%` : null],
  ];
  return `
    <h3 class="section-title">Dati immobile</h3>
    <div class="detail-grid">
      ${fields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</div>`).join('')}
    </div>
    ${renderCommercialStatusSection(p, commercialStatusEditMode, commercialStatusPendingConfirm)}
    ${renderIncaricoSection(p, editMode)}
    <h3 class="section-title">Note</h3>
    <p>${escapeHtml(p.public_notes || p.internal_notes || 'Nessuna nota.')}</p>
  `;
}

// --- Stato commerciale (P25.3) ---------------------------------------------
// Source of truth: properties.commercial_status (nessuna nuova colonna).
// Vedi commenti su MANUAL_COMMERCIAL_STATUSES/CONFIRM_REQUIRED_STATUSES in
// testa al file per il motivo dell'esclusione di 'sold' e del trattamento
// speciale di 'archived'.
function renderCommercialStatusSection(p, editMode, pendingConfirm) {
  if (p.commercial_status === 'sold') {
    return `
      <h3 class="section-title">Stato commerciale</h3>
      <div class="detail-grid">
        <div class="detail-item"><label>Stato attuale</label>${escapeHtml(STATUS_LABELS.sold)}</div>
      </div>
      <p class="muted">Lo stato "Venduto" è gestito automaticamente dal completamento della vendita (tab Proposte) e non è modificabile manualmente da qui.</p>
    `;
  }
  if (!editMode) {
    return `
      <h3 class="section-title">Stato commerciale</h3>
      <div class="detail-grid">
        <div class="detail-item"><label>Stato attuale</label>${escapeHtml(STATUS_LABELS[p.commercial_status] || p.commercial_status || '—')}</div>
      </div>
      <div class="action-bar" style="margin-top:12px">
        <button type="button" id="commercial-status-edit-btn" class="btn ghost">Cambia stato</button>
      </div>
    `;
  }
  const options = MANUAL_COMMERCIAL_STATUSES.map((s) => `<option value="${s}" ${s === p.commercial_status ? 'selected' : ''}>${escapeHtml(STATUS_LABELS[s] || s)}</option>`).join('');
  return `
    <h3 class="section-title">Stato commerciale</h3>
    <div class="form-grid-3">
      <div class="form-field"><label>Nuovo stato</label><select id="commercial-status-select" class="input">${options}</select></div>
    </div>
    ${pendingConfirm ? '<p class="field-error">Transizione significativa: premi di nuovo per confermare.</p>' : ''}
    <div id="commercial-status-error" class="field-error"></div>
    <div class="action-bar" style="margin-top:4px">
      <button type="button" id="commercial-status-cancel-btn" class="btn ghost">Annulla</button>
      <button type="button" id="commercial-status-save-btn" class="btn primary">${pendingConfirm ? 'Conferma' : 'Salva'}</button>
    </div>
  `;
}

// --- Incarico (mandate_type/mandate_start/mandate_end su properties; P8) ---
// Source of truth: properties.mandate_type/mandate_start/mandate_end (nessuna
// nuova entita' introdotta). Scrittura tramite il contratto generico gia'
// esistente PATCH /api/property/properties/{id} (PropertyUpdate) — nessun
// endpoint dedicato. commercial_status non viene mai incluso nel payload di
// questa sezione: resta indipendente (property/schemas.py non impone alcun
// accoppiamento tra i campi mandate_* e commercial_status).
function renderIncaricoSection(p, editMode) {
  if (!editMode) {
    return `
      <h3 class="section-title">Incarico</h3>
      <div class="detail-grid">
        <div class="detail-item"><label>Tipo incarico</label>${escapeHtml(p.mandate_type || '—')}</div>
        <div class="detail-item"><label>Data inizio</label>${escapeHtml(formatDate(p.mandate_start))}</div>
        <div class="detail-item"><label>Data scadenza</label>${escapeHtml(formatDate(p.mandate_end))}</div>
      </div>
      <div class="action-bar" style="margin-top:12px">
        <button type="button" id="incarico-edit-btn" class="btn ghost">Modifica</button>
      </div>
    `;
  }
  return `
    <h3 class="section-title">Incarico</h3>
    <div class="form-grid-3">
      <div class="form-field"><label>Tipo incarico</label><input type="text" id="incarico-type" class="input" maxlength="80" value="${escapeHtml(p.mandate_type || '')}"></div>
      <div class="form-field"><label>Data inizio</label><input type="date" id="incarico-start" class="input" value="${toDateInputValue(p.mandate_start)}"></div>
      <div class="form-field"><label>Data scadenza</label><input type="date" id="incarico-end" class="input" value="${toDateInputValue(p.mandate_end)}"></div>
    </div>
    <div id="incarico-error" class="field-error"></div>
    <div class="action-bar" style="margin-top:4px">
      <button type="button" id="incarico-cancel-btn" class="btn ghost">Annulla</button>
      <button type="button" id="incarico-save-btn" class="btn primary">Salva</button>
    </div>
  `;
}

function toDateInputValue(value) {
  if (!value) return '';
  return String(value).slice(0, 10);
}

// --- Proprietari (property_contacts, ruolo reale, non owner_accounts) ------
// P12: collegamento/rimozione operativi (vedi bindProprietariSection,
// openContactLinkDialog, runContactRemove sopra). Il contatto e' sempre
// scelto tra quelli CORE gia' esistenti: nessuna creazione qui.

function contactLabel(contact) {
  if (contact.display_name) return contact.display_name;
  if (contact.contact_type === 'company') return contact.company_name || `Contatto #${contact.id}`;
  const parts = [contact.first_name, contact.last_name].filter(Boolean);
  return parts.length ? parts.join(' ') : `Contatto #${contact.id}`;
}

// P12: azioni Rimuovi/Conferma rimozione per una riga property_contacts,
// stesso pattern a due click gia' usato per Annulla vendita
// (renderVenditaCell). Chiave "{contact_id}:{role}" perche' la UNIQUE di
// property_contacts e' su (property_id, contact_id, role): lo stesso
// contatto puo' comparire piu' volte con ruoli diversi.
function renderContactActions(c, contactRemoveConfirm) {
  const key = `${c.contact_id}:${c.role}`;
  const confirming = contactRemoveConfirm ? contactRemoveConfirm.has(key) : false;
  if (confirming) {
    return `<button type="button" class="btn ghost contact-remove-confirm-btn" data-contact-id="${escapeHtml(c.contact_id)}" data-role="${escapeHtml(c.role)}">Conferma rimozione</button> <button type="button" class="btn ghost contact-remove-back-btn" data-contact-key="${escapeHtml(key)}">Indietro</button>`;
  }
  return `<button type="button" class="btn ghost contact-remove-btn" data-contact-key="${escapeHtml(key)}">Rimuovi</button>`;
}

function renderProprietari(items, contactRemoveConfirm) {
  const note = '<p class="muted">Elenco dai referenti collegati all\'immobile (property_contacts). Non riflette gli account di accesso all\'Owner Portal.</p>';
  const table = renderTable(
    [
      { label: 'Nominativo', render: (c) => escapeHtml(c.display_name || `Contatto #${c.contact_id}`) },
      { label: 'Ruolo', render: (c) => renderBadge(PROPERTY_ROLE_LABELS[c.role] || c.role || '—', c.role === 'owner' ? 'role' : 'gray') },
      { label: 'Principale', render: (c) => c.is_primary ? renderBadge('Principale', 'ok') : '' },
      { label: 'Quota (%)', render: (c) => c.ownership_share != null ? escapeHtml(c.ownership_share) : '—' },
      { label: 'Email', render: (c) => escapeHtml(c.email || '—') },
      { label: 'Telefono', render: (c) => escapeHtml(c.phone || '—') },
      { label: 'Azioni', render: (c) => renderContactActions(c, contactRemoveConfirm) },
    ],
    items,
    { emptyMessage: 'Nessun referente collegato a questo immobile.' },
  );
  return `
    <div class="action-bar" style="margin-bottom:12px">
      <button type="button" id="contact-link-btn" class="btn primary">Collega contatto</button>
    </div>
    <div id="proprietari-feedback"></div>
    ${note}
    ${table}
  `;
}

// --- Foto (sola lettura: nessun upload/elimina/riordina) -------------------

function renderFoto(items, addMode, removeConfirm) {
  const list = Array.isArray(items) ? items : [];
  const sorted = [...list].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  const grid = sorted.length
    ? `<div class="photo-grid">${sorted.map((photo) => `
        <figure class="photo-item">
          <img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.title || 'Foto immobile')}" loading="lazy">
          <figcaption>
            ${photo.is_cover ? renderBadge('Copertina', 'role') + ' ' : ''}${escapeHtml(photo.title || '—')}
            <button type="button" class="btn ghost" data-photo-remove="${escapeHtml(photo.id)}">${removeConfirm.has(photo.id) ? 'Conferma' : 'Elimina'}</button>
          </figcaption>
        </figure>
      `).join('')}</div>`
    : '<p class="muted">Nessuna foto disponibile per questo immobile.</p>';
  return `${grid}<div id="photo-add-error" class="field-error"></div>${renderMediaAddSection('photo', addMode)}`;
}

// --- Documenti (property_documents; nessuno stato di condivisione OWNER) ---

function renderDocumenti(items, addMode, removeConfirm) {
  const note = '<p class="muted">Documenti dell\'immobile (sistema PROPERTY). Lo stato di condivisione con il Proprietario (Owner Portal) non è incluso in questa risposta e non è mostrato qui.</p>';
  const table = renderTable(
    [
      { label: 'Documento', render: (d) => escapeHtml(d.title || `Documento #${d.id}`) },
      { label: 'Tipo', render: (d) => escapeHtml(d.document_type || '—') },
      { label: 'Stato', render: (d) => renderBadge(DOCUMENT_STATUS_LABELS[d.status] || d.status || '—', documentStatusTone(d.status)) },
      { label: 'Scadenza', render: (d) => escapeHtml(formatDate(d.expires_at)) },
      { label: 'Note', render: (d) => escapeHtml(d.notes || '—') },
      { label: '', render: (d) => `<button type="button" class="btn ghost" data-document-remove="${escapeHtml(d.id)}">${removeConfirm.has(d.id) ? 'Conferma' : 'Elimina'}</button>` },
    ],
    items,
    { emptyMessage: 'Nessun documento presente per questo immobile.' },
  );
  return `${note}${table}<div id="document-add-error" class="field-error"></div>${renderMediaAddSection('document', addMode)}`;
}

// P25.6: sezione "+ Aggiungi foto/documento" — pulsante che rivela un
// piccolo form inline (stesso principio toggle di renderCriteriaAddSection
// in acquirente-dettaglio.js), mai un intero dialog per un'aggiunta cosi'
// piccola. url e' sempre un collegamento a un file gia' ospitato altrove:
// vedi il commento in testa a renderImmobileDettaglio sul perche' non
// esiste un vero upload binario in questo backend.
function renderMediaAddSection(kind, isOpen) {
  if (!isOpen) {
    const label = kind === 'photo' ? 'foto' : 'documento';
    return `<div class="action-bar" style="margin:12px 0"><button type="button" id="${kind}-add-toggle" class="btn ghost">+ Aggiungi ${label}</button></div>`;
  }
  if (kind === 'photo') {
    return `
      <form id="photo-add-form" class="form-grid-3" style="margin:12px 0;align-items:end">
        <div class="form-field"><label>URL foto *</label><input type="url" name="url" class="input" placeholder="https://…" required></div>
        <div class="form-field"><label>Titolo</label><input type="text" name="title" class="input" maxlength="200"></div>
        <div class="form-field"><label>Ordine</label><input type="number" name="sort_order" class="input" min="0" value="0"></div>
        <div class="form-field"><label><input type="checkbox" name="is_cover"> Imposta come copertina</label></div>
        <div class="modal-actions">
          <button type="button" id="photo-add-cancel" class="btn ghost">Annulla</button>
          <button type="submit" class="btn primary">Aggiungi</button>
        </div>
      </form>
    `;
  }
  return `
    <form id="document-add-form" class="form-grid-3" style="margin:12px 0;align-items:end">
      <div class="form-field"><label>Tipo documento *</label><input type="text" name="document_type" class="input" maxlength="80" placeholder="Es. planimetria" required></div>
      <div class="form-field"><label>Titolo *</label><input type="text" name="title" class="input" maxlength="200" required></div>
      <div class="form-field"><label>Stato</label>
        <select name="status" class="input">
          ${Object.entries(DOCUMENT_STATUS_LABELS).map(([v, l]) => `<option value="${v}" ${v === 'available' ? 'selected' : ''}>${escapeHtml(l)}</option>`).join('')}
        </select>
      </div>
      <div class="form-field"><label>URL documento</label><input type="url" name="url" class="input" placeholder="https://… (obbligatorio salvo stato Mancante/Richiesto)"></div>
      <div class="form-field"><label>Scadenza</label><input type="date" name="expires_at" class="input"></div>
      <div class="form-field"><label>Note</label><input type="text" name="notes" class="input" maxlength="500"></div>
      <div class="modal-actions">
        <button type="button" id="document-add-cancel" class="btn ghost">Annulla</button>
        <button type="submit" class="btn primary">Aggiungi</button>
      </div>
    </form>
  `;
}

// --- Visite (property_visits; P16: create/modifica/elimina operativi) -----
// Fonte dati: p.visits da get_property (property/repository.py:62-96),
// gia' arricchito in sola lettura con buy_request_id/match_id derivati da
// buy_request_interactions (dominio BUY, migrations/006_buy_02.sql) quando
// la visita e' nata da un abbinamento. Questi due campi sono SOLO
// visualizzati (badge "Origine"): non esiste alcuna colonna match_id su
// property_visits e nessuna scrittura viene mai fatta verso
// buy_request_interactions o verso /api/buy/* da questa vista — la
// registrazione dell'esito commerciale BUY (legacy property_admin
// openVisitOutcome/submitVisitOutcome, che scrive su
// /api/buy/requests/{id}/interactions) resta esplicitamente fuori scope
// P16 (vincolo assoluto: non modificare BUY).

function visitDateTimeLocal(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

// Costruisce il payload reale per POST /properties/{id}/visits o PATCH
// /visits/{id} (VisitCreate/VisitUpdate, property/schemas.py:164-192).
// Nessun campo inventato: solo scheduled_at, status, contact_id, lead_id,
// outcome, rating, feedback, assigned_to. scheduled_at e' obbligatorio solo
// in creazione (VisitCreate); in modifica viene comunque inviato se
// valorizzato, dato che il campo esiste anche su VisitUpdate.
function buildVisitPayload(dialogEl, selectedContact, isEdit) {
  const scheduledRaw = String(dialogEl.querySelector('#visit-scheduled-at').value || '').trim();
  if (!scheduledRaw && !isEdit) throw new Error('Data e ora visita obbligatorie.');
  const payload = {
    status: dialogEl.querySelector('#visit-status').value,
    contact_id: selectedContact ? selectedContact.id : null,
    outcome: dialogEl.querySelector('#visit-outcome').value.trim() || null,
    feedback: dialogEl.querySelector('#visit-feedback').value.trim() || null,
    assigned_to: dialogEl.querySelector('#visit-assigned-to').value.trim() || null,
  };
  const leadEl = dialogEl.querySelector('#visit-lead');
  if (leadEl) {
    const leadRaw = leadEl.value;
    payload.lead_id = leadRaw ? Number(leadRaw) : null;
  }
  if (scheduledRaw) {
    const scheduledAt = new Date(scheduledRaw);
    if (Number.isNaN(scheduledAt.getTime())) throw new Error('Data e ora visita non valide.');
    payload.scheduled_at = scheduledAt.toISOString();
  }
  const ratingRaw = dialogEl.querySelector('#visit-rating').value.trim();
  if (ratingRaw !== '') {
    const rating = Number(ratingRaw);
    if (!Number.isInteger(rating) || rating < 1 || rating > 5) throw new Error('La valutazione deve essere un numero intero tra 1 e 5.');
    payload.rating = rating;
  } else {
    payload.rating = null;
  }
  return payload;
}

function renderVisitActionButtons(v, visitRemoveConfirm) {
  const confirming = visitRemoveConfirm ? visitRemoveConfirm.has(v.id) : false;
  if (confirming) {
    return `<div class="action-bar"><button type="button" class="btn ghost visit-remove-confirm-btn" data-visit-id="${escapeHtml(v.id)}">Conferma eliminazione</button> <button type="button" class="btn ghost visit-remove-back-btn" data-visit-id="${escapeHtml(v.id)}">Indietro</button></div>`;
  }
  return `<div class="action-bar"><button type="button" class="btn ghost visit-edit-btn" data-visit-id="${escapeHtml(v.id)}">Aggiorna</button> <button type="button" class="btn ghost visit-remove-btn" data-visit-id="${escapeHtml(v.id)}">Elimina</button></div>`;
}

function renderVisite(items, visitRemoveConfirm) {
  const table = renderTable(
    [
      { label: 'Data', render: (v) => escapeHtml(formatDateTime(v.scheduled_at)) },
      { label: 'Stato', render: (v) => renderBadge(VISIT_STATUS_LABELS[v.status] || v.status || '—', statusTone(v.status)) },
      { label: 'Esito', render: (v) => escapeHtml(v.outcome || '—') },
      { label: 'Valutazione', render: (v) => v.rating != null ? `${escapeHtml(v.rating)}/5` : '—' },
      { label: 'Assegnata a', render: (v) => escapeHtml(v.assigned_to || '—') },
      { label: 'Origine', render: (v) => {
        const badges = [];
        if (v.buy_request_id) badges.push(renderBadge(`Richiesta BUY #${v.buy_request_id}`, 'buy'));
        if (v.match_id) badges.push(renderBadge(`Match #${v.match_id}`, 'gray'));
        return badges.length ? badges.join(' ') : '<span class="muted">—</span>';
      } },
      { label: '', render: (v) => renderVisitActionButtons(v, visitRemoveConfirm) },
    ],
    items,
    { emptyMessage: 'Nessuna visita registrata per questo immobile.' },
  );
  return `
    <div class="action-bar" style="margin-bottom:12px">
      <button type="button" id="visit-new-btn" class="btn primary">Nuova visita</button>
    </div>
    <div id="visite-feedback"></div>
    ${table}
  `;
}

// --- Acquirenti compatibili: gap esplicito, nessun riuso dati Abbinamenti ---

function renderAcquirentiCompatibili() {
  return '<p class="muted">Funzione disponibile nella fase Abbinamenti.</p>';
}

// --- Abbinamenti (lazy: GET /api/match/matches?property_id={id}) -----------

async function loadMatchesLazy(propertyId, cache) {
  if (cache.matches) return cache.matches;
  try {
    const data = await apiGet(`/api/match/matches?property_id=${propertyId}`);
    cache.matches = Array.isArray(data?.items) ? data.items : [];
  } catch (error) {
    cache.matches = { error: error.message };
  }
  return cache.matches;
}

function renderAbbinamenti(matches) {
  if (matches && matches.error) {
    return `<div class="error-box">Impossibile caricare gli abbinamenti: ${escapeHtml(matches.error)}</div>`;
  }
  return renderTable(
    [
      { label: 'Match', render: (m) => `#${escapeHtml(m.id)}` },
      { label: 'Acquirente', render: (m) => escapeHtml(m.buyer_name || `Richiesta #${m.buy_request_id}`) },
      { label: 'Punteggio', render: (m) => escapeHtml(m.effective_score ?? m.score_total ?? '—') },
      { label: 'Classe', render: (m) => renderBadge(m.match_class || '—', 'gray') },
      { label: 'Stato commerciale', render: (m) => escapeHtml(m.commercial_status || '—') },
      { label: 'Compatibilità', render: (m) => renderBadge(m.compatibility_status || '—', m.compatibility_status === 'incompatible' ? 'danger' : 'ok') },
      { label: '', render: (m) => `<button type="button" class="btn ghost open-match-btn" data-match-id="${escapeHtml(m.id)}">Apri match</button>` },
    ],
    matches,
    { emptyMessage: 'Nessun abbinamento presente per questo immobile.' },
  );
}

// P4: colonna "Apri match" -> #/abbinamenti/{match_id} (scheda MATCH nella
// nuova App Shell, mai match-admin legacy). Questa tabella non ha oggi altra
// navigazione di riga da preservare.
function bindOpenMatchLinks(contentEl) {
  contentEl.querySelectorAll('.open-match-btn').forEach((btn) => {
    btn.addEventListener('click', () => navigate('abbinamenti', [btn.dataset.matchId]));
  });
}

// --- Proposte (P9: operative — creazione, modifica in bozza, transizioni) --

const PROPOSAL_STATUS_LABELS = {
  draft: 'Bozza', submitted: 'Inviata', accepted: 'Accettata',
  rejected: 'Rifiutata', expired: 'Scaduta', withdrawn: 'Ritirata',
};

const PROPOSAL_ACTION_LABELS = {
  edit: 'Modifica', submitted: 'Invia', accepted: 'Accetta',
  rejected: 'Rifiuta', withdrawn: 'Ritira', expired: 'Segna scaduta',
};

// P11: chiusura frontend P10 SALE. Nessuna nuova entita': fonte unica
// property_sales via il contratto gia' verificato (sale/router.py,
// sale/repository.py). GET /api/sales non ha un filtro proposal_id
// (verificato in sale/repository.py:list_sales), quindi l'associazione
// vendita<->proposta e' fatta qui, lato client, filtrando l'elenco gia'
// caricato per property_id.
const SALE_STATUS_LABELS = { pending: 'In corso', completed: 'Completata', cancelled: 'Annullata' };

// Una proposta puo' avere piu' sale nel tempo (es. una cancelled seguita da
// una nuova creazione: il partial unique index di property_sales si applica
// solo a status IN ('pending','completed'), quindi una cancelled non blocca
// mai una nuova vendita). Priorita' nella scelta della sale da mostrare:
// completed > pending > cancelled piu' recente.
function saleForProposal(proposalId, sales) {
  const related = (sales || []).filter((s) => s.proposal_id === proposalId);
  if (!related.length) return null;
  const completed = related.find((s) => s.status === 'completed');
  if (completed) return completed;
  const pending = related.find((s) => s.status === 'pending');
  if (pending) return pending;
  const cancelled = related
    .filter((s) => s.status === 'cancelled')
    .sort((a, b) => (new Date(b.created_at || 0) - new Date(a.created_at || 0)) || (b.id - a.id));
  return cancelled[0] || null;
}

// Azioni vendita valide per la coppia (proposta, sale associata), lette
// direttamente dal contratto backend: nessuna macchina a stati reinventata
// qui (sale/enums.py:SALE_TRANSITIONS resta l'unica fonte, il backend
// ricontrolla sempre). soldMismatch e' il solo caso in cui "Crea vendita"
// viene nascosto anche a proposta accettata e sale non attiva (vedi
// renderProposte: debito noto commercial_status='sold' senza sale completed).
function saleActions(proposal, sale, soldMismatch) {
  if (proposal.status !== 'accepted') return [];
  if (sale && sale.status === 'completed') return [];
  if (sale && sale.status === 'pending') return ['complete', 'cancel'];
  if (soldMismatch) return [];
  return ['create'];
}

// Colonna "Vendita" nella tab Proposte: badge di stato (se esiste una sale)
// piu' le azioni pertinenti. Nessun window.confirm(): l'annullamento usa una
// conferma inline a due click pilotata da saleCancelConfirm (Set di sale_id
// in attesa di conferma), passato dal chiamante e non ricreato qui.
function renderVenditaCell(pr, sale, saleCancelConfirm, soldMismatch) {
  if (pr.status !== 'accepted') return '<span class="muted">—</span>';
  const actions = saleActions(pr, sale, soldMismatch);
  const statusBadge = sale
    ? renderBadge(SALE_STATUS_LABELS[sale.status] || sale.status || '—', sale.status === 'completed' ? 'ok' : sale.status === 'pending' ? 'warn' : 'gray')
    : '';
  const confirming = sale ? saleCancelConfirm.has(sale.id) : false;
  const buttonsHtml = actions.map((a) => {
    if (a === 'create') return `<button type="button" class="btn ghost sale-create-btn" data-proposal-id="${escapeHtml(pr.id)}">Crea vendita</button>`;
    if (a === 'complete') return `<button type="button" class="btn ghost sale-complete-btn" data-sale-id="${escapeHtml(sale.id)}"${confirming ? ' disabled' : ''}>Completa vendita</button>`;
    if (a === 'cancel') {
      if (confirming) {
        return `<button type="button" class="btn ghost sale-cancel-confirm-btn" data-sale-id="${escapeHtml(sale.id)}">Conferma annullamento</button><button type="button" class="btn ghost sale-cancel-back-btn" data-sale-id="${escapeHtml(sale.id)}">Indietro</button>`;
      }
      return `<button type="button" class="btn ghost sale-cancel-btn" data-sale-id="${escapeHtml(sale.id)}">Annulla vendita</button>`;
    }
    return '';
  }).join('');
  if (!statusBadge && !buttonsHtml) return '<span class="muted">—</span>';
  return `${statusBadge}${buttonsHtml ? `<div class="action-bar" style="margin-top:6px">${buttonsHtml}</div>` : ''}`;
}

// Stessa logica di static/buy_admin/assets/app.js:proposalActions (riferimento
// comportamentale P9), riletta sullo stato reale della proposta. "expired" e'
// solo un suggerimento client-side: il backend ricontrolla sempre database_now.
function proposalActions(proposal) {
  if (proposal.status === 'draft') return ['edit', 'submitted', 'withdrawn'];
  if (proposal.status === 'submitted') {
    const actions = ['accepted', 'rejected', 'withdrawn'];
    const expiresAt = new Date(proposal.expires_at);
    if (!Number.isNaN(expiresAt.getTime()) && expiresAt.getTime() <= Date.now()) actions.push('expired');
    return actions;
  }
  return [];
}

function renderProposalActionButtons(pr) {
  const actions = proposalActions(pr);
  if (!actions.length) return '';
  return `<div class="action-bar">${actions.map((a) => `<button type="button" class="btn ghost proposal-action-btn" data-proposal-id="${escapeHtml(pr.id)}" data-action="${escapeHtml(a)}">${escapeHtml(PROPOSAL_ACTION_LABELS[a] || a)}</button>`).join('')}</div>`;
}

function renderProposte(items, sales, property, saleCancelConfirm) {
  const hasCompletedSale = (sales || []).some((s) => s.status === 'completed');
  const soldMismatch = property.commercial_status === 'sold' && !hasCompletedSale;
  const table = renderTable(
    [
      { label: 'Proposta', render: (pr) => `#${escapeHtml(pr.id)}` },
      { label: 'Acquirente', render: (pr) => escapeHtml(pr.contact_name || `Contatto #${pr.contact_id}`) },
      { label: 'Importo', render: (pr) => formatPrice(pr.amount) },
      { label: 'Stato', render: (pr) => renderBadge(PROPOSAL_STATUS_LABELS[pr.status] || pr.status || '—', statusTone(pr.status)) },
      { label: 'Scadenza', render: (pr) => escapeHtml(formatDateTime(pr.expires_at)) },
      { label: 'Vendita', render: (pr) => renderVenditaCell(pr, saleForProposal(pr.id, sales), saleCancelConfirm, soldMismatch) },
      { label: '', render: (pr) => renderProposalActionButtons(pr) },
    ],
    items,
    { emptyMessage: 'Nessuna proposta presente per questo immobile.' },
  );
  return `
    <div class="action-bar" style="margin-bottom:12px">
      <button type="button" id="proposal-new-btn" class="btn primary">Nuova proposta</button>
    </div>
    ${soldMismatch ? '<div class="error-box" style="margin-bottom:12px">Immobile già segnato come venduto. Verificare lo stato prima di creare una nuova vendita.</div>' : ''}
    <div id="proposal-feedback"></div>
    ${table}
  `;
}

// Etichetta di un abbinamento eleggibile nel selettore "Nuova proposta": mai
// un match_id digitato manualmente, solo scelta da un elenco di abbinamenti
// reali gia' presenti (stesso principio gia' applicato in P3 per il Contatto).
function matchOptionLabel(m) {
  const buyer = m.buyer_name || `Richiesta #${m.buy_request_id}`;
  const scoreText = m.effective_score ?? m.score_total;
  return `${buyer}${scoreText != null ? ` \u2014 punteggio ${scoreText}` : ''}${m.match_class ? ` (${m.match_class})` : ''}`;
}

function proposalDateTimeLocal(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

// Converte il valore di un input datetime-local (orario locale del browser,
// senza timezone) in un ISO string con offset esplicito, come richiesto da
// proposal/schemas.py (expiry_requires_timezone). Stesso pattern di
// static/buy_admin/assets/app.js:proposalExpiry.
function proposalExpiryToIso(raw) {
  const trimmed = String(raw || '').trim();
  if (!trimmed) throw new Error('Data di scadenza non valida.');
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) throw new Error('Data di scadenza non valida.');
  return parsed.toISOString();
}

function proposalAmountValue(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) throw new Error('Importo deve essere maggiore di zero.');
  return value;
}

function buildProposalCreatePayload(dialogEl, idempotencyKey) {
  const matchSelect = dialogEl.querySelector('#proposal-match');
  const matchId = matchSelect ? Number(matchSelect.value) : NaN;
  if (!Number.isInteger(matchId) || matchId <= 0) throw new Error('Seleziona un abbinamento valido.');
  const notes = dialogEl.querySelector('#proposal-notes').value.trim();
  return {
    match_id: matchId,
    amount: proposalAmountValue(dialogEl.querySelector('#proposal-amount').value),
    expires_at: proposalExpiryToIso(dialogEl.querySelector('#proposal-expiry').value),
    notes: notes || null,
    idempotency_key: idempotencyKey,
  };
}

function buildProposalUpdatePayload(dialogEl) {
  const notes = dialogEl.querySelector('#proposal-notes').value.trim();
  return {
    amount: proposalAmountValue(dialogEl.querySelector('#proposal-amount').value),
    expires_at: proposalExpiryToIso(dialogEl.querySelector('#proposal-expiry').value),
    notes: notes || null,
  };
}

// --- Attività: nessuna relazione attivita<->immobile nelle API esistenti ---

function renderAttivita() {
  return '<p class="muted">Non è disponibile oggi un collegamento tra Attività e Immobile nelle API esistenti (core/repository.py: list_activities non filtra per immobile).</p>';
}

// --- utility ---------------------------------------------------------------

function formatPrice(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}

function statusTone(status) {
  if (['active', 'sold', 'mandate', 'confirmed', 'completed', 'accepted'].includes(status)) return 'ok';
  if (['withdrawn', 'archived', 'cancelled', 'rejected', 'expired', 'no_show'].includes(status)) return 'danger';
  if (['reserved', 'under_offer', 'evaluation', 'submitted'].includes(status)) return 'warn';
  return 'gray';
}

function documentStatusTone(status) {
  if (status === 'available') return 'ok';
  if (['missing', 'expired', 'rejected'].includes(status)) return 'danger';
  if (status === 'requested') return 'warn';
  return 'gray';
}
