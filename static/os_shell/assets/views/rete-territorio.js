// STIMA360 OS — views/rete-territorio.js
// P27-7 — la scheda di un territorio: identita', presidio e ALIAS in ingresso.
//
// ENDPOINT USATI (tutti gia' esistenti):
//   GET   /api/platform/territories/{id}
//   POST  /api/platform/territories/{id}/transfer
//   GET   /api/platform/territories/{id}/aliases
//   POST  /api/platform/territories/{id}/aliases
//   PATCH /api/platform/aliases/{alias_id}
//   GET   /api/platform/agencies                     (per i nomi e il trasferimento)
//
// LA COSA PIU' IMPORTANTE DI QUESTA SCHERMATA
//
// Un territorio ha DUE nomi che non vanno confusi, e P27-5 li ha separati
// apposta:
//
//   canonical_key  l'identita' amministrativa STABILE. Non si ricava
//                  dall'etichetta e non e' uno slug di cortesia: puo' essere
//                  un codice ISTAT come '067001'.
//   label          il nome leggibile. Si corregge senza che il territorio
//                  diventi un altro.
//
// Da qui segue la cosa che P27-6 ha dovuto imparare a caro prezzo: il comune
// che arriva dal modulo Stima360 NON viene confrontato con nessuno dei due.
// Viene confrontato con un ALIAS DICHIARATO. "Alba Adriatica" raggiunge questo
// territorio solo se qualcuno lo ha scritto qui dentro - nessuna
// slugificazione, nessuna traslitterazione, nessun accento rimosso, nessuna
// deduzione dall'etichetta. Questa schermata lo dice e lo fa fare
// esplicitamente, perche' l'alternativa - dedurlo - e' l'errore che la
// migration 059 esiste per chiudere.
//
// IL "ROUTING ATTIVO" E' UNA LETTURA, NON UN CALCOLO
//
// La catena mostrata in fondo - alias attivo -> territorio -> assegnazione
// attiva -> agenzia attiva - e' composta ESCLUSIVAMENTE da campi che il
// backend restituisce. Nessuna riga di questo file decide chi riceverebbe un
// lead: non esiste un endpoint di simulazione del routing, e ricostruirlo in
// JavaScript sarebbe una seconda implementazione della regola che, il giorno
// in cui divergesse, mentirebbe all'amministratore con l'aria di informarlo.

import { apiGet, apiPatch, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, renderBadge, formatDateTime } from '../components/st-table.js';
import {
  ALIAS_SOURCE_LABELS,
  ALIAS_SOURCE_PUBLIC_STIMA,
  ALIAS_STATUS_LABELS,
  ALIAS_STATUS_TONE,
  ASSIGNMENT_STATUS_LABELS,
  ASSIGNMENT_STATUS_TONE,
  PLATFORM,
  TERRITORY_KIND_LABELS,
  confirmAction,
  describeError,
  errorBox,
  escapeHtml,
  labelOf,
} from '../components/network.js';

export async function renderReteTerritorio(container, territoryId) {
  container.innerHTML = '<p class="muted">Caricamento…</p>';

  try {
    await apiGet(`${PLATFORM}/me`);
  } catch (error) {
    container.innerHTML = errorBox(error);
    return;
  }

  let territorio;
  try {
    territorio = await apiGet(`${PLATFORM}/territories/${encodeURIComponent(territoryId)}`);
  } catch (error) {
    container.innerHTML = errorBox(error, 'territorio-assegna');
    return;
  }

  let agenzie = [];
  try {
    const elenco = await apiGet(`${PLATFORM}/agencies`);
    agenzie = Array.isArray(elenco) ? elenco : [];
  } catch (_ignorato) {
    agenzie = [];
  }
  const perId = new Map(agenzie.map((a) => [a.id, a]));

  container.innerHTML = `
    <div class="action-bar"><a href="#/rete" id="torna-rete">← Rete</a></div>
    <h2>${escapeHtml(territorio.label)}</h2>
    <div class="detail-grid">
      <div class="detail-item"><label>Tipo</label>${escapeHtml(labelOf(TERRITORY_KIND_LABELS, territorio.kind))}</div>
      <div class="detail-item"><label>Chiave canonica</label><code>${escapeHtml(territorio.canonical_key)}</code></div>
      <div class="detail-item"><label>Creato il</label>${escapeHtml(formatDateTime(territorio.created_at))}</div>
    </div>
    <p class="muted">La <strong>chiave canonica</strong> e’ l’identita’ stabile del territorio. L’etichetta e’ il nome leggibile e non identifica nulla: correggerla non cambia il territorio, e il modulo Stima360 non la usa per instradare.</p>

    <h3 class="section-title">Presidio</h3>
    <div id="presidio"></div>

    <h3 class="section-title">Valori in ingresso (alias)</h3>
    <p class="muted">I valori che, arrivando dal modulo pubblico, portano a questo territorio. Vanno <strong>dichiarati</strong> uno per uno: il sistema non li deduce dall’etichetta e non li normalizza oltre maiuscole e spazi.</p>
    <div class="list-toolbar">
      <button type="button" class="btn primary" id="alias-nuovo">Dichiara valore</button>
    </div>
    <div id="alias-lista"><p class="muted">Caricamento…</p></div>

    <h3 class="section-title">Catena di instradamento</h3>
    <div id="catena"></div>
    <div id="territorio-dialog-host"></div>
  `;

  const presidioEl = container.querySelector('#presidio');
  const aliasEl = container.querySelector('#alias-lista');
  const catenaEl = container.querySelector('#catena');
  const hostEl = container.querySelector('#territorio-dialog-host');

  const indietro = container.querySelector('#torna-rete');
  if (indietro) indietro.addEventListener('click', (e) => { e.preventDefault(); navigate('rete'); });
  container.querySelector('#alias-nuovo').addEventListener('click', () => dialogoNuovoAlias());

  let alias = [];

  function disegnaPresidio() {
    const assegnazione = territorio.active_assignment;
    if (!assegnazione) {
      presidioEl.innerHTML = `
        <p class="muted">Nessuna assegnazione attiva: nessuna agenzia presidia questo territorio. I lead con un valore in ingresso che porta qui finiscono all’agenzia predefinita.</p>
        <p class="muted">Per assegnarlo, aprilo dalla scheda dell’agenzia che deve presidiarlo.</p>
      `;
      return;
    }
    const agenzia = perId.get(assegnazione.agency_id);
    presidioEl.innerHTML = `
      <div class="detail-grid">
        <div class="detail-item"><label>Agenzia</label>${escapeHtml(agenzia ? agenzia.name : `Agenzia #${assegnazione.agency_id}`)}</div>
        <div class="detail-item"><label>Assegnazione</label>${renderBadge(labelOf(ASSIGNMENT_STATUS_LABELS, assegnazione.status), ASSIGNMENT_STATUS_TONE[assegnazione.status] || 'gray')}</div>
        <div class="detail-item"><label>Dal</label>${escapeHtml(formatDateTime(assegnazione.created_at))}</div>
      </div>
      <div class="list-toolbar">
        <button type="button" class="btn" id="apri-agenzia">Apri l’agenzia</button>
        <button type="button" class="btn" id="trasferisci">Trasferisci territorio</button>
      </div>
    `;
    presidioEl.querySelector('#apri-agenzia').addEventListener('click', () => navigate('rete', ['agenzie', assegnazione.agency_id]));
    presidioEl.querySelector('#trasferisci').addEventListener('click', () => dialogoTrasferimento(assegnazione));
  }

  function disegnaCatena() {
    const assegnazione = territorio.active_assignment;
    const aliasAttivi = alias.filter((a) => a.status === 'active');
    const agenzia = assegnazione ? perId.get(assegnazione.agency_id) : null;
    // Ogni anello e' un dato che il backend ha restituito. Nessuna decisione
    // viene presa qui: si dice cosa manca, non chi riceverebbe cosa.
    const anelli = [
      {
        ok: aliasAttivi.length > 0,
        testo: aliasAttivi.length
          ? `${aliasAttivi.length} valore/i in ingresso dichiarato/i`
          : 'Nessun valore in ingresso dichiarato: dal modulo Stima360 nessun comune arriva a questo territorio',
      },
      {
        ok: territorio.kind === 'municipality',
        testo: territorio.kind === 'municipality'
          ? 'Territorio di tipo Comune'
          : 'Il territorio non e’ un Comune: il modulo Stima360 instrada solo sui comuni',
      },
      {
        ok: Boolean(assegnazione && assegnazione.status === 'active'),
        testo: assegnazione && assegnazione.status === 'active'
          ? 'Assegnazione attiva'
          : 'Nessuna assegnazione attiva',
      },
      {
        ok: Boolean(agenzia && agenzia.status === 'active'),
        testo: agenzia
          ? (agenzia.status === 'active' ? `Agenzia “${agenzia.name}” attiva` : `Agenzia “${agenzia.name}” non attiva`)
          : 'Nessuna agenzia',
      },
    ];
    const completa = anelli.every((a) => a.ok);
    catenaEl.innerHTML = `
      <div class="card">
        <p>${completa
          ? renderBadge('Instradamento completo', 'ok')
          : renderBadge('Instradamento incompleto', 'warn')}</p>
        <ul class="list">
          ${anelli.map((a) => `<li class="list-item">${a.ok ? '✓' : '✗'} ${escapeHtml(a.testo)}</li>`).join('')}
        </ul>
        <p class="muted">Questa e’ una lettura dello stato registrato, non una simulazione: la decisione su quale agenzia riceva un lead la prende il server al momento della stima.</p>
      </div>
    `;
  }

  async function caricaAlias() {
    aliasEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      const righe = await apiGet(`${PLATFORM}/territories/${encodeURIComponent(territoryId)}/aliases`);
      alias = Array.isArray(righe) ? righe : [];
    } catch (error) {
      aliasEl.innerHTML = errorBox(error, 'alias-crea');
      return;
    }
    aliasEl.innerHTML = renderTable(
      [
        { label: 'Valore dichiarato', render: (a) => `<strong>${escapeHtml(a.match_value)}</strong>` },
        { label: 'Provenienza', render: (a) => escapeHtml(labelOf(ALIAS_SOURCE_LABELS, a.source)) },
        { label: 'Stato', render: (a) => renderBadge(labelOf(ALIAS_STATUS_LABELS, a.status), ALIAS_STATUS_TONE[a.status] || 'gray') },
        { label: '', render: (a) => azioniAlias(a) },
      ],
      alias,
      { emptyMessage: 'Nessun valore dichiarato per questo territorio.' },
    );
    aliasEl.querySelectorAll('[data-alias]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const riga = alias.find((a) => String(a.id) === String(btn.dataset.alias));
        if (riga) revocaAlias(riga);
      });
    });
    disegnaCatena();
  }

  function azioniAlias(a) {
    // Revocato: nessuna azione. Non si riattiva - e' definitivo - e non si
    // cancella: la riga dice che quel valore ha voluto dire qualcosa, e fino a
    // quando. Per riusare il valore si dichiara un alias nuovo.
    if (a.status !== 'active') return '<span class="muted">Definitivo</span>';
    return `<button type="button" class="btn" data-alias="${escapeHtml(a.id)}">Revoca</button>`;
  }

  async function revocaAlias(riga) {
    const ok = await confirmAction(hostEl, {
      titolo: 'Revocare il valore dichiarato?',
      testo: `“${riga.match_value}” smette di portare a ${territorio.label}: da quel momento i lead con quel comune andranno all’agenzia predefinita, finche’ qualcuno non lo dichiara di nuovo. La revoca e’ definitiva e la riga resta nello storico.`,
      conferma: 'Revoca',
      tono: 'danger',
    });
    if (!ok) return;
    try {
      await apiPatch(`${PLATFORM}/aliases/${encodeURIComponent(riga.id)}`, { status: 'revoked' });
      await caricaAlias();
    } catch (error) {
      aliasEl.insertAdjacentHTML('afterbegin', errorBox(error, 'alias-aggiorna'));
    }
  }

  function dialogoNuovoAlias() {
    const dialog = document.createElement('dialog');
    dialog.className = 'modal';
    dialog.innerHTML = `
      <form id="form-alias">
        <h3>Dichiara un valore in ingresso</h3>
        <p class="muted">Scrivi il valore <em>esattamente come arriva</em> dal modulo pubblico, per esempio <code>Alba Adriatica</code>. Il confronto ignora maiuscole e spazi in eccesso, e nient’altro: accenti e apostrofi contano, e non viene applicata nessuna trasformazione in slug.</p>
        <div class="form-field">
          <label for="al-source">Provenienza</label>
          <select class="input" id="al-source">
            <option value="${escapeHtml(ALIAS_SOURCE_PUBLIC_STIMA)}">${escapeHtml(ALIAS_SOURCE_LABELS[ALIAS_SOURCE_PUBLIC_STIMA])}</option>
          </select>
        </div>
        <div class="form-field">
          <label for="al-valore">Valore</label>
          <input class="input" id="al-valore" required maxlength="200" placeholder="Alba Adriatica">
        </div>
        <div class="field-error" id="al-errore" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="al-annulla">Annulla</button>
          <button type="submit" class="btn primary">Dichiara</button>
        </div>
      </form>
    `;
    hostEl.appendChild(dialog);
    const chiudi = () => { if (typeof dialog.close === 'function') dialog.close(); dialog.remove(); };
    dialog.querySelector('#al-annulla').addEventListener('click', chiudi);
    dialog.querySelector('#form-alias').addEventListener('submit', async (event) => {
      event.preventDefault();
      const erroreEl = dialog.querySelector('#al-errore');
      erroreEl.textContent = '';
      try {
        // Il valore viaggia COM'E' STATO SCRITTO. Nessuna trasformazione lato
        // client: il solo ritocco - spazi ai lati e ripetuti - lo fa il
        // backend, che e' anche quello che poi confronta.
        await apiPost(`${PLATFORM}/territories/${encodeURIComponent(territoryId)}/aliases`, {
          source: dialog.querySelector('#al-source').value,
          match_value: dialog.querySelector('#al-valore').value,
        });
        chiudi();
        await caricaAlias();
      } catch (error) {
        erroreEl.textContent = describeError(error, 'alias-crea');
      }
    });
    if (typeof dialog.showModal === 'function') dialog.showModal();
  }

  function dialogoTrasferimento(assegnazione) {
    const candidate = agenzie.filter((a) => a.id !== assegnazione.agency_id && a.status === 'active');
    const dialog = document.createElement('dialog');
    dialog.className = 'modal';
    dialog.innerHTML = candidate.length
      ? `
        <form id="form-trasferimento">
          <h3>Trasferisci territorio</h3>
          <p class="muted">L’assegnazione attuale viene revocata e ne nasce una nuova, nella stessa operazione.</p>
          <div class="form-field">
            <label for="tr-agenzia">Nuova agenzia</label>
            <select class="input" id="tr-agenzia">
              ${candidate.map((a) => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}</option>`).join('')}
            </select>
          </div>
          <div class="field-error" id="tr-errore" role="alert"></div>
          <div class="modal-actions">
            <button type="button" class="btn ghost" id="tr-annulla">Annulla</button>
            <button type="submit" class="btn primary">Trasferisci</button>
          </div>
        </form>
      `
      : `
        <h3>Trasferisci territorio</h3>
        <p>Nessun’altra agenzia attiva a cui trasferirlo.</p>
        <div class="modal-actions"><button type="button" class="btn ghost" id="tr-annulla">Chiudi</button></div>
      `;
    hostEl.appendChild(dialog);
    const chiudi = () => { if (typeof dialog.close === 'function') dialog.close(); dialog.remove(); };
    dialog.querySelector('#tr-annulla').addEventListener('click', chiudi);
    const form = dialog.querySelector('#form-trasferimento');
    if (form) {
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const erroreEl = dialog.querySelector('#tr-errore');
        erroreEl.textContent = '';
        const scelta = dialog.querySelector('#tr-agenzia');
        const nuova = perId.get(Number(scelta.value));
        const attuale = perId.get(assegnazione.agency_id);
        const ok = await confirmAction(hostEl, {
          titolo: 'Trasferire il territorio?',
          testo: `${territorio.label} passa da “${attuale ? attuale.name : 'l’agenzia attuale'}” a “${nuova ? nuova.name : 'la nuova agenzia'}”. L’assegnazione attuale viene revocata - e la revoca e’ definitiva - e i lead di questo territorio andranno alla nuova agenzia.`,
          conferma: 'Trasferisci',
        });
        if (!ok) return;
        try {
          await apiPost(`${PLATFORM}/territories/${encodeURIComponent(territoryId)}/transfer`, {
            agency_id: Number(scelta.value),
          });
          chiudi();
          territorio = await apiGet(`${PLATFORM}/territories/${encodeURIComponent(territoryId)}`);
          disegnaPresidio();
          disegnaCatena();
        } catch (error) {
          erroreEl.textContent = describeError(error, 'territorio-trasferisci');
        }
      });
    }
    if (typeof dialog.showModal === 'function') dialog.showModal();
  }

  disegnaPresidio();
  await caricaAlias();
}
