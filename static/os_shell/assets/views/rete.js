// STIMA360 OS — views/rete.js
// P27-7 — la sezione RETE: l'ingresso dell'amministrazione di piattaforma.
//
// Due elenchi, perche' la rete ha due cose dentro: le AGENZIE che la
// compongono e i TERRITORI che si distribuiscono fra loro. Tutto il resto -
// configurazione, operatori, assegnazioni, alias - vive nel dettaglio
// dell'una o dell'altro, dove ha un contesto.
//
// IL CONTROLLO D'ACCESSO, E PERCHE' NON BASTA NASCONDERE LA VOCE DI MENU
//
// La voce "Rete" compare in sidebar solo per `is_platform_admin` (main.js), ma
// una voce nascosta e' una cortesia, non una difesa: la rotta `#/rete` si
// raggiunge scrivendola. Per questo la prima cosa che questa view fa e'
// chiedere `GET /api/platform/me`. Se il backend risponde 403, qui compare un
// avviso e nient'altro - nessun elenco viene nemmeno richiesto.
//
// E anche questo non e' la sicurezza: l'autorita' e' il backend, che protegge
// OGNI route con `require_platform_admin` (P27-1). Se qualcuno bypassasse
// questa schermata non otterrebbe dati, otterrebbe 403 su ogni chiamata. Il
// controllo qui serve a dire subito "non ti compete" invece di mostrare
// un'interfaccia che fallisce a ogni clic.
//
// ENDPOINT USATI (tutti gia' esistenti, nessuno nuovo):
//   GET  /api/platform/me
//   GET  /api/platform/agencies                      (elenco intero: P27-2 non
//        pagina, e l'elenco delle agenzie di una rete non e' una tabella di
//        milioni di righe. Il filtro qui e' locale e dichiarato tale.)
//   POST /api/platform/agencies
//   GET  /api/platform/territories?kind&agency_id&assignment_status&limit&offset
//   POST /api/platform/territories

import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, renderBadge, formatDateTime } from '../components/st-table.js';
import {
  AGENCY_STATUS_LABELS,
  AGENCY_STATUS_TONE,
  ASSIGNMENT_STATUS_LABELS,
  PLATFORM,
  TERRITORY_KIND_LABELS,
  describeError,
  errorBox,
  escapeHtml,
  labelOf,
  selectOptions,
} from '../components/network.js';

const TERRITORI_PAGE = 50;

const TABS = [
  { key: 'agenzie', label: 'Agenzie' },
  { key: 'territori', label: 'Territori' },
];

export async function renderRete(container) {
  container.innerHTML = '<p class="muted">Caricamento…</p>';

  // Il cancello. Nessuna altra chiamata parte prima che questa risponda.
  try {
    await apiGet(`${PLATFORM}/me`);
  } catch (error) {
    container.innerHTML = errorBox(error);
    return;
  }

  const state = {
    tab: 'agenzie',
    agenzie: [],
    territori: { kind: '', assignment_status: '', offset: 0, items: [] },
  };

  container.innerHTML = `
    <p class="muted">Amministrazione della rete STIMA360: agenzie affiliate, operatori, territori presidiati e valori in ingresso che li raggiungono.</p>
    <div class="tabs" id="rete-tabs">
      ${TABS.map((t) => `<button type="button" class="tab-btn" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('')}
    </div>
    <div id="rete-tab-content"><p class="muted">Caricamento…</p></div>
    <div id="rete-dialog-host"></div>
  `;

  const tabsEl = container.querySelector('#rete-tabs');
  const contentEl = container.querySelector('#rete-tab-content');
  const hostEl = container.querySelector('#rete-dialog-host');

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => mostraTab(btn.dataset.tab));
  });

  async function mostraTab(key) {
    state.tab = key;
    tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === key);
    });
    if (key === 'agenzie') return caricaAgenzie();
    return caricaTerritori();
  }

  // --- agenzie --------------------------------------------------------------

  async function caricaAgenzie() {
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let righe;
    try {
      righe = await apiGet(`${PLATFORM}/agencies`);
    } catch (error) {
      contentEl.innerHTML = errorBox(error);
      return;
    }
    state.agenzie = Array.isArray(righe) ? righe : [];
    contentEl.innerHTML = `
      <div class="list-toolbar">
        <button type="button" class="btn primary" id="agenzia-nuova">Nuova agenzia</button>
      </div>
      <div id="agenzie-lista"></div>
    `;
    const listaEl = contentEl.querySelector('#agenzie-lista');
    listaEl.innerHTML = tabellaAgenzie(state.agenzie);
    bindTableRowClicks(listaEl, (id) => navigate('rete', ['agenzie', id]));
    contentEl.querySelector('#agenzia-nuova').addEventListener('click', () => dialogoNuovaAgenzia());
  }

  function tabellaAgenzie(righe) {
    return renderTable(
      [
        { label: 'Agenzia', render: (a) => `<strong>${escapeHtml(a.name)}</strong>` },
        // Lo slug e' l'identificativo stabile: si mostra sempre, e nel dettaglio
        // si dice esplicitamente che non si cambia.
        { label: 'Identificativo (slug)', render: (a) => `<code>${escapeHtml(a.slug)}</code>` },
        { label: 'Stato', render: (a) => renderBadge(labelOf(AGENCY_STATUS_LABELS, a.status), AGENCY_STATUS_TONE[a.status] || 'gray') },
        { label: 'Creata', render: (a) => escapeHtml(formatDateTime(a.created_at)) },
        { label: '', render: () => '<span class="muted">Apri →</span>' },
      ],
      righe,
      { emptyMessage: 'Nessuna agenzia nella rete.', onRowClick: true },
    );
  }

  function dialogoNuovaAgenzia() {
    const dialog = document.createElement('dialog');
    dialog.className = 'modal';
    dialog.innerHTML = `
      <h3>Nuova agenzia</h3>
      <form id="form-agenzia">
        <div class="form-field">
          <label for="ag-name">Nome</label>
          <input class="input" id="ag-name" name="name" required maxlength="200">
        </div>
        <div class="form-field">
          <label for="ag-slug">Identificativo (slug)</label>
          <input class="input" id="ag-slug" name="slug" required maxlength="100" placeholder="agenzia-esempio">
          <small class="muted">Minuscole, numeri e trattini. NON e’ modificabile dopo la creazione: identifica l’agenzia in modo stabile.</small>
        </div>
        <div class="form-grid-2">
          <div class="form-field">
            <label for="ag-timezone">Fuso orario</label>
            <input class="input" id="ag-timezone" name="timezone" value="Europe/Rome">
          </div>
          <div class="form-field">
            <label for="ag-locale">Lingua</label>
            <input class="input" id="ag-locale" name="locale" value="it-IT">
          </div>
        </div>
        <div class="field-error" id="ag-errore" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="ag-annulla">Annulla</button>
          <button type="submit" class="btn primary">Crea agenzia</button>
        </div>
      </form>
    `;
    hostEl.appendChild(dialog);
    const chiudi = () => { if (typeof dialog.close === 'function') dialog.close(); dialog.remove(); };
    dialog.querySelector('#ag-annulla').addEventListener('click', chiudi);
    dialog.querySelector('#form-agenzia').addEventListener('submit', async (event) => {
      event.preventDefault();
      const erroreEl = dialog.querySelector('#ag-errore');
      erroreEl.textContent = '';
      const corpo = {
        name: dialog.querySelector('#ag-name').value,
        slug: dialog.querySelector('#ag-slug').value,
        settings: {
          timezone: dialog.querySelector('#ag-timezone').value,
          locale: dialog.querySelector('#ag-locale').value,
        },
      };
      try {
        const creata = await apiPost(`${PLATFORM}/agencies`, corpo);
        chiudi();
        // Si riparte dal backend: lo stato locale non viene aggiornato a mano.
        // Una lista "aggiornata" senza rileggere mostrerebbe cio' che il client
        // crede di aver creato, non cio' che esiste.
        await caricaAgenzie();
        if (creata && creata.id) navigate('rete', ['agenzie', creata.id]);
      } catch (error) {
        erroreEl.textContent = describeError(error, 'agenzia-crea');
      }
    });
    if (typeof dialog.showModal === 'function') dialog.showModal();
  }

  // --- territori ------------------------------------------------------------

  async function caricaTerritori() {
    contentEl.innerHTML = `
      <p class="muted">Il catalogo dei territori della rete. La <strong>chiave canonica</strong> e’ l’identita’ amministrativa stabile del territorio; l’etichetta e’ solo il nome leggibile e si puo’ correggere senza cambiare il territorio.</p>
      <div class="list-toolbar">
        <select class="input" id="filtro-kind">
          <option value="">Tutti i tipi</option>
          ${selectOptions(TERRITORY_KIND_LABELS, state.territori.kind)}
        </select>
        <select class="input" id="filtro-assegnazione">
          <option value="">Assegnati e non</option>
          ${selectOptions(ASSIGNMENT_STATUS_LABELS, state.territori.assignment_status)}
        </select>
        <button type="button" class="btn primary" id="territorio-nuovo">Nuovo territorio</button>
      </div>
      <div id="territori-lista"><p class="muted">Caricamento…</p></div>
      <div id="territori-pager" class="list-pager"></div>
    `;
    contentEl.querySelector('#filtro-kind').addEventListener('change', (e) => {
      state.territori.kind = e.target.value;
      state.territori.offset = 0;
      paginaTerritori();
    });
    contentEl.querySelector('#filtro-assegnazione').addEventListener('change', (e) => {
      state.territori.assignment_status = e.target.value;
      state.territori.offset = 0;
      paginaTerritori();
    });
    contentEl.querySelector('#territorio-nuovo').addEventListener('click', () => dialogoNuovoTerritorio());
    await paginaTerritori();
  }

  async function paginaTerritori() {
    const listaEl = contentEl.querySelector('#territori-lista');
    const pagerEl = contentEl.querySelector('#territori-pager');
    if (!listaEl) return;
    listaEl.innerHTML = '<p class="muted">Caricamento…</p>';
    const params = new URLSearchParams({
      limit: String(TERRITORI_PAGE),
      offset: String(state.territori.offset),
    });
    if (state.territori.kind) params.set('kind', state.territori.kind);
    if (state.territori.assignment_status) params.set('assignment_status', state.territori.assignment_status);

    let righe = [];
    try {
      righe = await apiGet(`${PLATFORM}/territories?${params.toString()}`);
    } catch (error) {
      listaEl.innerHTML = errorBox(error);
      if (pagerEl) pagerEl.innerHTML = '';
      return;
    }
    righe = Array.isArray(righe) ? righe : [];
    state.territori.items = righe;

    // I nomi delle agenzie servono a mostrare CHI presidia un territorio. Si
    // prendono dall'elenco gia' in memoria, o con UNA chiamata: mai una per
    // riga.
    if (!state.agenzie.length) {
      try {
        const elenco = await apiGet(`${PLATFORM}/agencies`);
        state.agenzie = Array.isArray(elenco) ? elenco : [];
      } catch (_ignorato) {
        state.agenzie = [];
      }
    }
    const nomi = new Map(state.agenzie.map((a) => [a.id, a.name]));

    listaEl.innerHTML = renderTable(
      [
        { label: 'Tipo', render: (t) => escapeHtml(labelOf(TERRITORY_KIND_LABELS, t.kind)) },
        { label: 'Etichetta', render: (t) => `<strong>${escapeHtml(t.label)}</strong>` },
        { label: 'Chiave canonica', render: (t) => `<code>${escapeHtml(t.canonical_key)}</code>` },
        {
          label: 'Presidiato da',
          render: (t) => (t.active_agency_id
            ? escapeHtml(nomi.get(t.active_agency_id) || `Agenzia #${t.active_agency_id}`)
            : '<span class="muted">Nessuna assegnazione attiva</span>'),
        },
        { label: '', render: () => '<span class="muted">Apri →</span>' },
      ],
      righe,
      { emptyMessage: 'Nessun territorio con questi filtri.', onRowClick: true },
    );
    bindTableRowClicks(listaEl, (id) => navigate('rete', ['territori', id]));

    pagerEl.innerHTML = `
      <button class="btn" id="terr-prev" ${state.territori.offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${righe.length ? state.territori.offset + 1 : 0} a ${state.territori.offset + righe.length}</span>
      <button class="btn" id="terr-next" ${righe.length < TERRITORI_PAGE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prev = pagerEl.querySelector('#terr-prev');
    const next = pagerEl.querySelector('#terr-next');
    if (prev) prev.addEventListener('click', () => {
      state.territori.offset = Math.max(0, state.territori.offset - TERRITORI_PAGE);
      paginaTerritori();
    });
    if (next) next.addEventListener('click', () => {
      state.territori.offset += TERRITORI_PAGE;
      paginaTerritori();
    });
  }

  function dialogoNuovoTerritorio() {
    const dialog = document.createElement('dialog');
    dialog.className = 'modal';
    dialog.innerHTML = `
      <h3>Nuovo territorio</h3>
      <form id="form-territorio">
        <div class="form-field">
          <label for="te-kind">Tipo</label>
          <select class="input" id="te-kind" name="kind">${selectOptions(TERRITORY_KIND_LABELS, 'municipality')}</select>
        </div>
        <div class="form-field">
          <label for="te-key">Chiave canonica</label>
          <input class="input" id="te-key" name="canonical_key" required maxlength="120" placeholder="es. 067001">
          <small class="muted">Identita’ amministrativa STABILE del territorio: minuscole, numeri e trattini. Non e’ il nome, e non viene ricavata dall’etichetta.</small>
        </div>
        <div class="form-field">
          <label for="te-label">Etichetta</label>
          <input class="input" id="te-label" name="label" required maxlength="200" placeholder="es. Alba Adriatica">
          <small class="muted">Il nome leggibile. Correggerlo non cambia l’identita’ del territorio.</small>
        </div>
        <div class="field-error" id="te-errore" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="te-annulla">Annulla</button>
          <button type="submit" class="btn primary">Crea territorio</button>
        </div>
      </form>
    `;
    hostEl.appendChild(dialog);
    const chiudi = () => { if (typeof dialog.close === 'function') dialog.close(); dialog.remove(); };
    dialog.querySelector('#te-annulla').addEventListener('click', chiudi);
    dialog.querySelector('#form-territorio').addEventListener('submit', async (event) => {
      event.preventDefault();
      const erroreEl = dialog.querySelector('#te-errore');
      erroreEl.textContent = '';
      try {
        const creato = await apiPost(`${PLATFORM}/territories`, {
          kind: dialog.querySelector('#te-kind').value,
          canonical_key: dialog.querySelector('#te-key').value,
          label: dialog.querySelector('#te-label').value,
        });
        chiudi();
        await paginaTerritori();
        if (creato && creato.id) navigate('rete', ['territori', creato.id]);
      } catch (error) {
        erroreEl.textContent = describeError(error, 'territorio-crea');
      }
    });
    if (typeof dialog.showModal === 'function') dialog.showModal();
  }

  await mostraTab('agenzie');
}
