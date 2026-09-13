// STIMA360 OS — views/rete-agenzia.js
// P27-7 — la scheda di un'agenzia affiliata: quattro schede, una per ciascuna
// delle superfici che P27-2, P27-4, P27-3 e P27-5 hanno costruito.
//
// ENDPOINT USATI (tutti gia' esistenti):
//   GET   /api/platform/agencies/{id}
//   PATCH /api/platform/agencies/{id}                     (name, status - MAI settings)
//   GET   /api/platform/agencies/{id}/configuration
//   PATCH /api/platform/agencies/{id}/configuration
//   GET   /api/platform/agencies/{id}/operators
//   POST  /api/platform/agencies/{id}/operators
//   PATCH /api/platform/operators/{operator_user_id}
//   PATCH /api/platform/agencies/{id}/operators/{operator_user_id}/membership
//   PUT   /api/platform/agencies/{id}/owner
//   GET   /api/platform/agencies/{id}/territories
//   POST  /api/platform/agencies/{id}/territories
//   PATCH /api/platform/agencies/{id}/territories/{assignment_id}
//   GET   /api/platform/territories                       (per scegliere cosa assegnare)
//
// TRE REGOLE DI P27 CHE QUESTA SCHERMATA DEVE RENDERE VISIBILI, NON AGGIRARE
//
// 1. LO SLUG NON SI MODIFICA. `AgencyUpdateRequest` accetta solo `name` e
//    `status`, e la PATCH generica non tocca piu' `settings` (P27-4 ha chiuso
//    quella strada). Qui lo slug si mostra come testo, mai come campo: un input
//    disabilitato suggerirebbe che da qualche parte si possa abilitare.
//
// 2. `revoked` E' TERMINALE, ovunque compaia - membership e assegnazioni. Su
//    una riga revocata le azioni che la riporterebbero in vita NON VENGONO
//    DISEGNATE: mostrarle e poi spiegare un 409 insegna che i pulsanti mentono.
//
// 3. NESSUNA CANCELLAZIONE. Non c'e' una sola DELETE in questo file, perche'
//    non c'e' una sola DELETE nel backend: si sospende, si revoca, si
//    trasferisce, e la storia resta.
//
// LA PASSWORD, E IL PERCHE' DI DUE STRADE DIVERSE
//
// `POST /operators` serve due casi con lo stesso corpo: email nuova (serve la
// password, senza la quale 422) ed email gia' nota (la password NON va inviata,
// altrimenti 409). Il modulo qui chiede esplicitamente quale dei due casi si
// sta facendo, invece di provarne uno e ricadere sull'altro: mandare una
// password a un'identita' esistente sarebbe un reimposta-password mascherato,
// ed e' esattamente cio' che il backend rifiuta. `password_hash` non compare
// in nessuna risposta di P27 e non compare qui.

import { apiGet, apiPatch, apiPost, apiPut } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, renderBadge, formatDateTime } from '../components/st-table.js';
import {
  AGENCY_STATUS_LABELS,
  AGENCY_STATUS_TONE,
  ASSIGNMENT_STATUS_LABELS,
  ASSIGNMENT_STATUS_TONE,
  MEMBERSHIP_STATUS_LABELS,
  MEMBERSHIP_STATUS_TONE,
  OPERATOR_STATUS_LABELS,
  OPERATOR_STATUS_TONE,
  PLATFORM,
  ROLE_LABELS,
  TERRITORY_KIND_LABELS,
  confirmAction,
  describeError,
  errorBox,
  escapeHtml,
  labelOf,
  selectOptions,
} from '../components/network.js';

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'configurazione', label: 'Configurazione' },
  { key: 'operatori', label: 'Operatori' },
  { key: 'territori', label: 'Territori' },
];

export async function renderReteAgenzia(container, agencyId) {
  container.innerHTML = '<p class="muted">Caricamento…</p>';

  try {
    await apiGet(`${PLATFORM}/me`);
  } catch (error) {
    container.innerHTML = errorBox(error);
    return;
  }

  let agenzia;
  try {
    agenzia = await apiGet(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}`);
  } catch (error) {
    container.innerHTML = errorBox(error, 'agenzia-crea');
    return;
  }

  container.innerHTML = `
    <div class="action-bar">
      <a href="#/rete" id="torna-rete">← Rete</a>
    </div>
    <h2 id="agenzia-nome">${escapeHtml(agenzia.name)}</h2>
    <p class="muted">Identificativo <code>${escapeHtml(agenzia.slug)}</code> · ${escapeHtml(labelOf(AGENCY_STATUS_LABELS, agenzia.status))}</p>
    <div class="tabs" id="agenzia-tabs">
      ${TABS.map((t) => `<button type="button" class="tab-btn" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('')}
    </div>
    <div id="agenzia-tab-content"><p class="muted">Caricamento…</p></div>
    <div id="agenzia-dialog-host"></div>
  `;

  const tabsEl = container.querySelector('#agenzia-tabs');
  const contentEl = container.querySelector('#agenzia-tab-content');
  const hostEl = container.querySelector('#agenzia-dialog-host');

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => mostraTab(btn.dataset.tab));
  });

  async function ricaricaAgenzia() {
    agenzia = await apiGet(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}`);
    container.querySelector('#agenzia-nome').textContent = agenzia.name;
  }

  async function mostraTab(key) {
    tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === key);
    });
    if (key === 'panoramica') return panoramica();
    if (key === 'configurazione') return configurazione();
    if (key === 'operatori') return operatori();
    return territori();
  }

  // --- panoramica -----------------------------------------------------------

  async function panoramica() {
    contentEl.innerHTML = `
      <div class="detail-grid">
        <div class="detail-item"><label>Identificativo interno</label>#${escapeHtml(agenzia.id)}</div>
        <div class="detail-item"><label>Identificativo stabile (slug)</label><code>${escapeHtml(agenzia.slug)}</code></div>
        <div class="detail-item"><label>Creata il</label>${escapeHtml(formatDateTime(agenzia.created_at))}</div>
        <div class="detail-item"><label>Ultima modifica</label>${escapeHtml(formatDateTime(agenzia.updated_at))}</div>
      </div>
      <p class="muted">Lo slug non e’ modificabile: e’ l’identificativo con cui il resto del sistema riconosce questa agenzia.</p>
      <form id="form-panoramica" class="card">
        <div class="form-field">
          <label for="pa-name">Nome</label>
          <input class="input" id="pa-name" value="${escapeHtml(agenzia.name)}" maxlength="200">
        </div>
        <div class="form-field">
          <label for="pa-status">Stato</label>
          <select class="input" id="pa-status">${selectOptions(AGENCY_STATUS_LABELS, agenzia.status)}</select>
          <small class="muted">Sospesa: gli operatori dell’agenzia non possono piu’ accedere. Archiviata: l’agenzia esce dall’operativita’ della rete.</small>
        </div>
        <div class="field-error" id="pa-errore" role="alert"></div>
        <div class="modal-actions">
          <button type="submit" class="btn primary">Salva modifiche</button>
        </div>
      </form>
    `;
    contentEl.querySelector('#form-panoramica').addEventListener('submit', async (event) => {
      event.preventDefault();
      const erroreEl = contentEl.querySelector('#pa-errore');
      erroreEl.textContent = '';
      const nuovoNome = contentEl.querySelector('#pa-name').value;
      const nuovoStato = contentEl.querySelector('#pa-status').value;

      // Una PATCH manda SOLO cio' che cambia: `model_fields_set` decide cosa
      // viene scritto, quindi rimandare un campo invariato lo registrerebbe
      // nell'audit come una modifica che nessuno ha fatto.
      const corpo = {};
      if (nuovoNome !== agenzia.name) corpo.name = nuovoNome;
      if (nuovoStato !== agenzia.status) corpo.status = nuovoStato;
      if (!Object.keys(corpo).length) {
        erroreEl.textContent = 'Nessuna modifica da salvare.';
        return;
      }

      if (corpo.status === 'suspended' || corpo.status === 'archived') {
        const testo = corpo.status === 'suspended'
          ? `Gli operatori di “${agenzia.name}” non potranno piu’ accedere finche’ l’agenzia resta sospesa. I dati restano, le assegnazioni territoriali restano, ma l’agenzia non e’ piu’ instradabile dal modulo Stima360.`
          : `“${agenzia.name}” esce dall’operativita’ della rete: nessun accesso per i suoi operatori e nessun lead instradato. I dati e la storia restano, ma l’agenzia non viene piu’ usata.`;
        const ok = await confirmAction(hostEl, {
          titolo: corpo.status === 'suspended' ? 'Sospendere l’agenzia?' : 'Archiviare l’agenzia?',
          testo,
          conferma: corpo.status === 'suspended' ? 'Sospendi' : 'Archivia',
          tono: 'danger',
        });
        if (!ok) return;
      }

      try {
        await apiPatch(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}`, corpo);
        await ricaricaAgenzia();
        await panoramica();
      } catch (error) {
        erroreEl.textContent = describeError(error, 'agenzia-crea');
      }
    });
  }

  // --- configurazione -------------------------------------------------------

  async function configurazione() {
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let config;
    try {
      config = await apiGet(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/configuration`);
    } catch (error) {
      // Una configurazione persistita incoerente produce un 500 dal backend, e
      // il messaggio che arriva qui e' volutamente generico: chi lo legge non
      // ha sbagliato niente e non puo' farci niente. Nessun dettaglio interno.
      contentEl.innerHTML = errorBox(error);
      return;
    }
    contentEl.innerHTML = `
      <p class="muted">Questi valori sono sempre presenti: se l’agenzia non ne ha di propri, valgono quelli predefiniti della piattaforma.</p>
      <form id="form-config" class="card">
        <div class="form-grid-2">
          <div class="form-field">
            <label for="cf-timezone">Fuso orario</label>
            <input class="input" id="cf-timezone" value="${escapeHtml(config.timezone)}">
            <small class="muted">Identificativo IANA, es. <code>Europe/Rome</code>.</small>
          </div>
          <div class="form-field">
            <label for="cf-locale">Lingua</label>
            <input class="input" id="cf-locale" value="${escapeHtml(config.locale)}">
          </div>
        </div>
        <div class="field-error" id="cf-errore" role="alert"></div>
        <div class="modal-actions">
          <button type="submit" class="btn primary">Salva configurazione</button>
        </div>
      </form>
    `;
    contentEl.querySelector('#form-config').addEventListener('submit', async (event) => {
      event.preventDefault();
      const erroreEl = contentEl.querySelector('#cf-errore');
      erroreEl.textContent = '';
      const corpo = {};
      const tz = contentEl.querySelector('#cf-timezone').value;
      const loc = contentEl.querySelector('#cf-locale').value;
      if (tz !== config.timezone) corpo.timezone = tz;
      if (loc !== config.locale) corpo.locale = loc;
      if (!Object.keys(corpo).length) {
        erroreEl.textContent = 'Nessuna modifica da salvare.';
        return;
      }
      try {
        // La rotta DEDICATA di P27-4. La PATCH generica dell'agenzia non
        // accetta piu' `settings`, e non deve tornare a farlo passando da qui.
        await apiPatch(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/configuration`, corpo);
        await configurazione();
      } catch (error) {
        erroreEl.textContent = describeError(error);
      }
    });
  }

  // --- operatori ------------------------------------------------------------

  async function operatori() {
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let righe;
    try {
      righe = await apiGet(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/operators`);
    } catch (error) {
      contentEl.innerHTML = errorBox(error);
      return;
    }
    righe = Array.isArray(righe) ? righe : [];
    contentEl.innerHTML = `
      <div class="list-toolbar">
        <button type="button" class="btn primary" id="op-nuovo">Aggiungi operatore</button>
        <button type="button" class="btn" id="op-titolare">Trasferisci titolare</button>
      </div>
      <div id="operatori-lista"></div>
    `;
    const listaEl = contentEl.querySelector('#operatori-lista');
    listaEl.innerHTML = renderTable(
      [
        {
          label: 'Operatore',
          render: (r) => `<strong>${escapeHtml(nomeOperatore(r.operator))}</strong>${r.membership.role === 'agency_owner' && r.membership.status === 'active' ? ' ' + renderBadge('Titolare', 'ok') : ''}`,
        },
        { label: 'Email', render: (r) => escapeHtml(r.operator.email) },
        { label: 'Ruolo', render: (r) => escapeHtml(labelOf(ROLE_LABELS, r.membership.role)) },
        { label: 'Accesso', render: (r) => renderBadge(labelOf(OPERATOR_STATUS_LABELS, r.operator.status), OPERATOR_STATUS_TONE[r.operator.status] || 'gray') },
        { label: 'Membership', render: (r) => renderBadge(labelOf(MEMBERSHIP_STATUS_LABELS, r.membership.status), MEMBERSHIP_STATUS_TONE[r.membership.status] || 'gray') },
        { label: '', render: (r) => azioniOperatore(r) },
      ],
      righe,
      { emptyMessage: 'Nessun operatore in questa agenzia.' },
    );

    listaEl.querySelectorAll('[data-azione]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const riga = righe.find((r) => String(r.operator.id) === String(btn.dataset.operatore));
        if (!riga) return;
        if (btn.dataset.azione === 'accesso') return dialogoAccesso(riga);
        if (btn.dataset.azione === 'membership') return dialogoMembership(riga);
      });
    });

    contentEl.querySelector('#op-nuovo').addEventListener('click', () => dialogoNuovoOperatore());
    contentEl.querySelector('#op-titolare').addEventListener('click', () => dialogoTitolare(righe));
  }

  function nomeOperatore(op) {
    const nome = [op.first_name, op.last_name].filter(Boolean).join(' ').trim();
    return nome || op.email;
  }

  function azioniOperatore(riga) {
    const bottoni = [
      `<button type="button" class="btn" data-azione="accesso" data-operatore="${escapeHtml(riga.operator.id)}">Accesso</button>`,
    ];
    // Su una membership REVOCATA non si disegna il pulsante che la
    // modificherebbe: revoked e' terminale, e l'unica strada e' una nuova
    // membership. Un pulsante che porta a un 409 certo e' una trappola.
    if (riga.membership.status !== 'revoked') {
      bottoni.push(`<button type="button" class="btn" data-azione="membership" data-operatore="${escapeHtml(riga.operator.id)}">Membership</button>`);
    }
    return bottoni.join(' ');
  }

  function dialogoAccesso(riga) {
    apriDialogo(`
      <h3>Accesso di ${escapeHtml(nomeOperatore(riga.operator))}</h3>
      <p class="muted">Riguarda la persona, non questa agenzia: un operatore disabilitato non accede da nessuna parte.</p>
      <div class="form-field">
        <label for="op-stato">Stato dell’accesso</label>
        <select class="input" id="op-stato">${selectOptions(OPERATOR_STATUS_LABELS, riga.operator.status)}</select>
      </div>
    `, 'Salva', async (dialog) => {
      const stato = dialog.querySelector('#op-stato').value;
      if (stato === riga.operator.status) return 'Nessuna modifica da salvare.';
      try {
        await apiPatch(`${PLATFORM}/operators/${encodeURIComponent(riga.operator.id)}`, { status: stato });
        await operatori();
        return null;
      } catch (error) {
        return describeError(error, 'membership-aggiorna');
      }
    });
  }

  function dialogoMembership(riga) {
    // `agency_owner` NON compare fra i ruoli assegnabili qui: il titolare si
    // cambia con il trasferimento, che chiude la membership precedente nella
    // stessa transazione. Offrirlo qui significherebbe offrire un 409.
    const ruoliAssegnabili = { agency_admin: ROLE_LABELS.agency_admin, agent: ROLE_LABELS.agent };
    const statiAssegnabili = { active: MEMBERSHIP_STATUS_LABELS.active, suspended: MEMBERSHIP_STATUS_LABELS.suspended, revoked: MEMBERSHIP_STATUS_LABELS.revoked };
    apriDialogo(`
      <h3>Membership di ${escapeHtml(nomeOperatore(riga.operator))}</h3>
      <p class="muted">Ruolo e stato di questa persona <em>in questa agenzia</em>. Il ruolo di titolare si assegna solo con il trasferimento del titolare.</p>
      <div class="form-grid-2">
        <div class="form-field">
          <label for="mb-ruolo">Ruolo</label>
          <select class="input" id="mb-ruolo">${selectOptions(ruoliAssegnabili, riga.membership.role)}</select>
        </div>
        <div class="form-field">
          <label for="mb-stato">Stato</label>
          <select class="input" id="mb-stato">${selectOptions(statiAssegnabili, riga.membership.status)}</select>
        </div>
      </div>
      <p class="muted">Revocare e’ definitivo: una membership revocata non si riattiva, e per far rientrare la persona serve una nuova membership.</p>
    `, 'Salva', async (dialog) => {
      const ruolo = dialog.querySelector('#mb-ruolo').value;
      const stato = dialog.querySelector('#mb-stato').value;
      const corpo = {};
      if (ruolo !== riga.membership.role) corpo.role = ruolo;
      if (stato !== riga.membership.status) corpo.status = stato;
      if (!Object.keys(corpo).length) return 'Nessuna modifica da salvare.';
      if (corpo.status === 'revoked') {
        const ok = await confirmAction(hostEl, {
          titolo: 'Revocare la membership?',
          testo: `${nomeOperatore(riga.operator)} perde l’accesso ai dati di “${agenzia.name}”. La revoca e’ definitiva: per farlo rientrare servira’ una nuova membership.`,
          conferma: 'Revoca',
          tono: 'danger',
        });
        if (!ok) return 'annullato';
      }
      try {
        await apiPatch(
          `${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/operators/${encodeURIComponent(riga.operator.id)}/membership`,
          corpo,
        );
        await operatori();
        return null;
      } catch (error) {
        return describeError(error, 'membership-aggiorna');
      }
    });
  }

  function dialogoNuovoOperatore() {
    apriDialogo(`
      <h3>Aggiungi operatore</h3>
      <div class="form-field">
        <label for="no-email">Email</label>
        <input class="input" id="no-email" type="email" required maxlength="320">
      </div>
      <div class="form-field">
        <label class="checkbox-label"><input type="checkbox" id="no-esistente"> L’email appartiene a un operatore gia’ registrato</label>
        <small class="muted">In quel caso viene creata solo la membership in questa agenzia, e la credenziale della persona non viene toccata da qui.</small>
      </div>
      <div class="form-grid-2">
        <div class="form-field">
          <label for="no-nome">Nome</label>
          <input class="input" id="no-nome" maxlength="100">
        </div>
        <div class="form-field">
          <label for="no-cognome">Cognome</label>
          <input class="input" id="no-cognome" maxlength="100">
        </div>
      </div>
      <div class="form-field" id="no-password-campo">
        <label for="no-password">Password iniziale</label>
        <input class="input" id="no-password" type="password" autocomplete="new-password">
        <small class="muted">Serve solo per una persona nuova: senza credenziale non potrebbe accedere.</small>
      </div>
      <div class="form-field">
        <label for="no-ruolo">Ruolo in questa agenzia</label>
        <select class="input" id="no-ruolo">${selectOptions({ agency_admin: ROLE_LABELS.agency_admin, agent: ROLE_LABELS.agent }, 'agent')}</select>
      </div>
    `, 'Aggiungi', async (dialog) => {
      const esistente = dialog.querySelector('#no-esistente').checked;
      const corpo = {
        email: dialog.querySelector('#no-email').value,
        role: dialog.querySelector('#no-ruolo').value,
      };
      const nome = dialog.querySelector('#no-nome').value;
      const cognome = dialog.querySelector('#no-cognome').value;
      if (nome) corpo.first_name = nome;
      if (cognome) corpo.last_name = cognome;
      if (!esistente) {
        const password = dialog.querySelector('#no-password').value;
        if (!password) return 'Per una persona nuova serve una password iniziale.';
        corpo.password = password;
      }
      // Se l'email e' gia' nota, `password` non viene inviato affatto: mandarlo
      // sarebbe un reimposta-password implicito, e il backend risponde 409.
      try {
        await apiPost(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/operators`, corpo);
        await operatori();
        return null;
      } catch (error) {
        return describeError(error, 'operatore-crea');
      }
    }, (dialog) => {
      const spunta = dialog.querySelector('#no-esistente');
      const campo = dialog.querySelector('#no-password-campo');
      spunta.addEventListener('change', () => { campo.hidden = spunta.checked; });
    });
  }

  function dialogoTitolare(righe) {
    // Solo membership ATTIVE della stessa agenzia: e' la regola di P27-3, e
    // offrire gli altri significherebbe offrire un 409 garantito.
    const candidati = righe.filter((r) => r.membership.status === 'active' && r.membership.role !== 'agency_owner');
    if (!candidati.length) {
      apriDialogo(
        `<h3>Trasferisci titolare</h3><p>Nessun candidato: il nuovo titolare deve gia’ avere una membership attiva in questa agenzia.</p>`,
        null, null,
      );
      return;
    }
    const opzioni = candidati
      .map((r) => `<option value="${escapeHtml(r.operator.id)}">${escapeHtml(nomeOperatore(r.operator))} — ${escapeHtml(r.operator.email)}</option>`)
      .join('');
    apriDialogo(`
      <h3>Trasferisci titolare</h3>
      <p class="muted">Il titolare attuale, se c’e’, viene retrocesso ad amministratore agenzia nella stessa operazione.</p>
      <div class="form-field">
        <label for="tt-operatore">Nuovo titolare</label>
        <select class="input" id="tt-operatore">${opzioni}</select>
      </div>
    `, 'Trasferisci', async (dialog) => {
      const scelto = dialog.querySelector('#tt-operatore');
      const etichetta = scelto.options && scelto.options.length
        ? (scelto.options[scelto.selectedIndex] || {}).textContent
        : scelto.value;
      const ok = await confirmAction(hostEl, {
        titolo: 'Trasferire il titolare?',
        testo: `${etichetta || 'L’operatore scelto'} diventa titolare di “${agenzia.name}”. Il titolare attuale, se presente, viene retrocesso ad amministratore agenzia: entrambe le modifiche avvengono insieme.`,
        conferma: 'Trasferisci',
      });
      if (!ok) return 'annullato';
      try {
        await apiPut(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/owner`, {
          operator_user_id: Number(scelto.value),
        });
        await operatori();
        return null;
      } catch (error) {
        return describeError(error, 'titolare-trasferisci');
      }
    });
  }

  // --- territori ------------------------------------------------------------

  async function territori() {
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let righe;
    try {
      righe = await apiGet(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/territories`);
    } catch (error) {
      contentEl.innerHTML = errorBox(error);
      return;
    }
    righe = Array.isArray(righe) ? righe : [];
    contentEl.innerHTML = `
      <p class="muted">La copertura territoriale di questa agenzia, nella sua storia intera: le assegnazioni sospese e revocate restano visibili.</p>
      <div class="list-toolbar">
        <button type="button" class="btn primary" id="te-assegna">Assegna territorio</button>
      </div>
      <div id="agenzia-territori-lista"></div>
    `;
    const listaEl = contentEl.querySelector('#agenzia-territori-lista');
    listaEl.innerHTML = renderTable(
      [
        { label: 'Tipo', render: (a) => escapeHtml(labelOf(TERRITORY_KIND_LABELS, a.territory_kind)) },
        { label: 'Territorio', render: (a) => `<strong>${escapeHtml(a.territory_label)}</strong>` },
        { label: 'Chiave canonica', render: (a) => `<code>${escapeHtml(a.territory_canonical_key)}</code>` },
        { label: 'Assegnazione', render: (a) => renderBadge(labelOf(ASSIGNMENT_STATUS_LABELS, a.status), ASSIGNMENT_STATUS_TONE[a.status] || 'gray') },
        { label: '', render: (a) => azioniAssegnazione(a) },
      ],
      righe,
      { emptyMessage: 'Nessun territorio assegnato a questa agenzia.' },
    );

    listaEl.querySelectorAll('[data-assegnazione]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const riga = righe.find((a) => String(a.id) === String(btn.dataset.assegnazione));
        if (riga) cambiaAssegnazione(riga, btn.dataset.verso);
      });
    });
    listaEl.querySelectorAll('[data-territorio]').forEach((a) => {
      a.addEventListener('click', (event) => {
        event.preventDefault();
        navigate('rete', ['territori', a.dataset.territorio]);
      });
    });
    contentEl.querySelector('#te-assegna').addEventListener('click', () => dialogoAssegna());
  }

  function azioniAssegnazione(a) {
    const apri = `<a href="#/rete/territori/${escapeHtml(a.territory_id)}" data-territorio="${escapeHtml(a.territory_id)}">Apri territorio</a>`;
    // REVOCATA: nessuna azione di stato. Non "riattiva" (revoked e' terminale)
    // e non "sospendi" (non c'e' piu' niente da sospendere). Per ridare il
    // territorio si crea una NUOVA assegnazione.
    if (a.status === 'revoked') return apri;
    const bottoni = [];
    if (a.status === 'active') {
      bottoni.push(`<button type="button" class="btn" data-assegnazione="${escapeHtml(a.id)}" data-verso="suspended">Sospendi</button>`);
    }
    if (a.status === 'suspended') {
      bottoni.push(`<button type="button" class="btn" data-assegnazione="${escapeHtml(a.id)}" data-verso="active">Riattiva</button>`);
    }
    bottoni.push(`<button type="button" class="btn" data-assegnazione="${escapeHtml(a.id)}" data-verso="revoked">Revoca</button>`);
    return `${bottoni.join(' ')} ${apri}`;
  }

  async function cambiaAssegnazione(riga, verso) {
    if (verso === 'revoked') {
      const ok = await confirmAction(hostEl, {
        titolo: 'Revocare l’assegnazione?',
        testo: `“${agenzia.name}” smette di presidiare ${riga.territory_label}. I lead con quel comune in ingresso andranno all’agenzia predefinita finche’ il territorio non viene assegnato a qualcun altro. La revoca e’ definitiva: per ridare il territorio servira’ una nuova assegnazione.`,
        conferma: 'Revoca',
        tono: 'danger',
      });
      if (!ok) return;
    }
    try {
      await apiPatch(
        `${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/territories/${encodeURIComponent(riga.id)}`,
        { status: verso },
      );
      await territori();
    } catch (error) {
      const listaEl = contentEl.querySelector('#agenzia-territori-lista');
      if (listaEl) listaEl.insertAdjacentHTML('afterbegin', errorBox(error, 'assegnazione-aggiorna'));
    }
  }

  async function dialogoAssegna() {
    let disponibili = [];
    try {
      // I territori SENZA assegnazione attiva: `assignment_status` filtra le
      // assegnazioni, quindi si chiede l'elenco e si scartano quelli gia'
      // presidiati leggendo `active_agency_id`, che l'elenco gia' porta.
      const elenco = await apiGet(`${PLATFORM}/territories?limit=200&offset=0`);
      disponibili = (Array.isArray(elenco) ? elenco : []).filter((t) => !t.active_agency_id);
    } catch (error) {
      apriDialogo(`<h3>Assegna territorio</h3>${errorBox(error)}`, null, null);
      return;
    }
    if (!disponibili.length) {
      apriDialogo(
        `<h3>Assegna territorio</h3><p>Nessun territorio libero: tutti quelli esistenti hanno gia’ un’assegnazione attiva. Creane uno nuovo dal catalogo, oppure trasferisci un territorio gia’ assegnato.</p>`,
        null, null,
      );
      return;
    }
    const opzioni = disponibili
      .map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(labelOf(TERRITORY_KIND_LABELS, t.kind))} — ${escapeHtml(t.label)} (${escapeHtml(t.canonical_key)})</option>`)
      .join('');
    apriDialogo(`
      <h3>Assegna territorio</h3>
      <p class="muted">Solo i territori senza assegnazione attiva: un territorio e’ presidiato da una sola agenzia per volta.</p>
      <div class="form-field">
        <label for="as-territorio">Territorio</label>
        <select class="input" id="as-territorio">${opzioni}</select>
      </div>
    `, 'Assegna', async (dialog) => {
      try {
        await apiPost(`${PLATFORM}/agencies/${encodeURIComponent(agencyId)}/territories`, {
          territory_id: Number(dialog.querySelector('#as-territorio').value),
        });
        await territori();
        return null;
      } catch (error) {
        return describeError(error, 'territorio-assegna');
      }
    });
  }

  // --- dialogo generico -----------------------------------------------------

  function apriDialogo(html, etichettaConferma, onConferma, onMount) {
    const dialog = document.createElement('dialog');
    dialog.className = 'modal';
    dialog.innerHTML = `
      <form id="dialogo-form">
        ${html}
        <div class="field-error" id="dialogo-errore" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="dialogo-chiudi">${etichettaConferma ? 'Annulla' : 'Chiudi'}</button>
          ${etichettaConferma ? `<button type="submit" class="btn primary">${escapeHtml(etichettaConferma)}</button>` : ''}
        </div>
      </form>
    `;
    hostEl.appendChild(dialog);
    const chiudi = () => { if (typeof dialog.close === 'function') dialog.close(); dialog.remove(); };
    dialog.querySelector('#dialogo-chiudi').addEventListener('click', chiudi);
    if (typeof onMount === 'function') onMount(dialog);
    if (typeof onConferma === 'function') {
      dialog.querySelector('#dialogo-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const erroreEl = dialog.querySelector('#dialogo-errore');
        erroreEl.textContent = '';
        const esito = await onConferma(dialog);
        if (esito === null) { chiudi(); return; }
        if (esito === 'annullato') return;
        erroreEl.textContent = esito;
      });
    }
    if (typeof dialog.showModal === 'function') dialog.showModal();
  }

  const indietro = container.querySelector('#torna-rete');
  if (indietro) {
    indietro.addEventListener('click', (event) => { event.preventDefault(); navigate('rete'); });
  }

  await mostraTab('panoramica');
}
