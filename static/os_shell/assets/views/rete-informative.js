// INFORMATIVE PRIVACY — la scheda della Rete riservata al Super Admin.
//
// Le informative (061 `consent_notices`) sono testi VERSIONATI con una
// validita': un consenso marketing raccolto dall'operatore cita la versione
// presentata, e senza una versione valida nessun consenso si registra. Qui
// il Super Admin le vede, le legge per intero, ne pubblica una nuova e ne
// chiude la validita'. Il testo di una versione pubblicata NON si modifica:
// si chiude e se ne pubblica un'altra, ed e' la ragione per cui qui non
// esiste nessun «Modifica».
//
// NESSUN TESTO PRECOMPILATO. Il campo del testo nasce vuoto: l'informativa
// la scrive (o incolla) una persona, ogni volta. Niente viene pubblicato da
// solo, e la chiusura chiede conferma spiegando l'effetto.
//
// Rotte usate (tutte dietro `require_super_admin_context`, tutte con audit):
//   GET  /api/platform/consent-notices?purpose=...
//   GET  /api/platform/consent-notices/{id}
//   POST /api/platform/consent-notices
//   POST /api/platform/consent-notices/{id}/close
//
// Il corpo che parte porta SOLO: purpose, version, context, content,
// valid_from, valid_to. Hash e autore li scrive il server.

import { apiGet, apiPost } from '../core/api-client.js';
import { renderTable, bindTableRowClicks, renderBadge, formatDateTime } from '../components/st-table.js';
import { PLATFORM, confirmAction, describeError, errorBox, escapeHtml, labelOf, selectOptions } from '../components/network.js';

const FINALITA = { marketing: 'Marketing', privacy_terms: 'Termini privacy' };
// 109: i canali che il TESTO copre. Nessuno preselezionato: li dichiara il Super Admin.
const CANALI = { email: 'Email', sms: 'SMS', whatsapp: 'WhatsApp', phone: 'Telefonate commerciali' };
const STATO = { corrente: 'Corrente', programmata: 'Programmata', chiusa: 'Chiusa' };
const TONO_STATO = { corrente: 'ok', programmata: 'warn', chiusa: 'gray' };

// Messaggi comprensibili per i rifiuti del server, oltre a quelli generici
// di `describeError`. Il 409 ha due cause diverse e il testo le distingue.
function spiega(error, azione) {
  // `apiRequest` costruisce `new Error(detail)`: il testo del server e' in `message`.
  const dettaglio = error && error.message;
  const testo = typeof dettaglio === 'string' ? dettaglio : '';
  if (error && error.status === 409 && /corrente/.test(testo)) {
    return 'C’e’ gia’ una versione corrente per questa finalita’: chiudila prima di pubblicarne un’altra.';
  }
  if (error && error.status === 409 && /esiste gia/.test(testo)) {
    return 'Esiste gia’ una versione con questo nome per questa finalita’: scegli un nome diverso.';
  }
  if (error && error.status === 409 && /chiusa/.test(testo)) {
    return 'Questa versione e’ gia’ chiusa.';
  }
  if (error && error.status === 422 && testo) {
    return `Dati non validi: ${testo}`;
  }
  return describeError(error, azione);
}

function statoDi(n, adesso = new Date()) {
  if (n.valid_to && new Date(n.valid_to) <= adesso) return 'chiusa';
  if (new Date(n.valid_from) > adesso) return 'programmata';
  return 'corrente';
}

function isoConFuso(valoreLocale) {
  // `datetime-local` non porta il fuso: si interpreta nell’ora del browser e
  // si manda in ISO con offset, come il server pretende.
  if (!valoreLocale) return null;
  const data = new Date(valoreLocale);
  return Number.isNaN(data.getTime()) ? null : data.toISOString();
}

function adessoLocale() {
  const d = new Date();
  d.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export async function renderInformative(container, hostEl) {
  const state = { purpose: 'marketing', righe: [], aperta: null };

  container.innerHTML = `
    <p class="muted">Le informative privacy presentate a chi concede un consenso. Una versione pubblicata non si modifica: si chiude e se ne pubblica un’altra. Senza una versione corrente per il marketing, gli operatori non possono registrare consensi.</p>
    <div class="list-toolbar">
      <select class="input" id="informative-finalita" aria-label="Finalita’">
        ${selectOptions(FINALITA, state.purpose)}
      </select>
      <button type="button" class="btn primary" id="informativa-nuova">Nuova versione</button>
    </div>
    <div id="informative-lista"><p class="muted">Caricamento…</p></div>
    <div id="informativa-dettaglio"></div>
    <div id="informativa-form"></div>
  `;
  const listaEl = container.querySelector('#informative-lista');
  const dettaglioEl = container.querySelector('#informativa-dettaglio');
  const formEl = container.querySelector('#informativa-form');
  const host = hostEl || container;

  container.querySelector('#informative-finalita').addEventListener('change', (e) => {
    state.purpose = e.target.value;
    state.aperta = null;
    dettaglioEl.innerHTML = '';
    carica();
  });
  container.querySelector('#informativa-nuova').addEventListener('click', () => mostraForm());

  async function carica() {
    listaEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let righe;
    try {
      righe = await apiGet(`${PLATFORM}/consent-notices?purpose=${encodeURIComponent(state.purpose)}`);
    } catch (error) {
      listaEl.innerHTML = errorBox(error);
      return;
    }
    state.righe = Array.isArray(righe) ? righe : [];
    const corrente = state.righe.some((n) => statoDi(n) === 'corrente');
    listaEl.innerHTML = `
      ${corrente ? '' : `<p class="error-box" data-nessuna-corrente>Nessuna versione corrente per «${escapeHtml(labelOf(FINALITA, state.purpose))}».${state.purpose === 'marketing' ? ' Gli operatori non possono registrare consensi marketing finche’ non ne pubblichi una.' : ''}</p>`}
      ${renderTable(
        [
          { label: 'Versione', render: (n) => `<strong>${escapeHtml(n.version)}</strong><div class="muted">${escapeHtml(n.context)}</div>` },
          { label: 'Finalita’', render: (n) => escapeHtml(labelOf(FINALITA, n.purpose)) },
          { label: 'Canali', render: (n) => ((n.channels || []).length ? escapeHtml(n.channels.map((c) => labelOf(CANALI, c)).join(', ')) : '<span class="muted">—</span>') },
          { label: 'Valida dal', render: (n) => escapeHtml(formatDateTime(n.valid_from)) },
          { label: 'Fino al', render: (n) => (n.valid_to ? escapeHtml(formatDateTime(n.valid_to)) : '<span class="muted">—</span>') },
          { label: 'Stato', render: (n) => renderBadge(labelOf(STATO, statoDi(n)), TONO_STATO[statoDi(n)]) },
          { label: 'Pubblicata', render: (n) => `${escapeHtml(formatDateTime(n.created_at))}<div class="muted">${escapeHtml(n.created_by)}</div>` },
          {
            label: '',
            render: (n) => (n.valid_to
              ? ''
              : `<button type="button" class="btn ghost btn-sm" data-azione="chiudi" data-informativa="${Number(n.id)}">Chiudi versione</button>`),
          },
        ],
        state.righe,
        { emptyMessage: 'Nessuna informativa per questa finalita’.', onRowClick: true },
      )}`;
    bindTableRowClicks(listaEl, (id) => apri(Number(id)));
    listaEl.querySelectorAll('[data-azione="chiudi"]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        chiudi(Number(btn.dataset.informativa));
      });
    });
  }

  async function apri(id) {
    dettaglioEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let n;
    try {
      n = await apiGet(`${PLATFORM}/consent-notices/${id}`);
    } catch (error) {
      dettaglioEl.innerHTML = errorBox(error);
      return;
    }
    state.aperta = n;
    dettaglioEl.innerHTML = `
      <div class="card" data-informativa-dettaglio="${Number(n.id)}">
        <h3>Versione ${escapeHtml(n.version)} — ${escapeHtml(labelOf(FINALITA, n.purpose))} ${renderBadge(labelOf(STATO, statoDi(n)), TONO_STATO[statoDi(n)])}</h3>
        <div class="detail-grid">
          <div class="detail-item"><label>Contesto</label>${escapeHtml(n.context)}</div>
          <div class="detail-item"><label>Canali coperti</label>${(n.channels || []).length ? escapeHtml(n.channels.map((c) => labelOf(CANALI, c)).join(', ')) : 'nessuno'}</div>
          <div class="detail-item"><label>Validita’</label>dal ${escapeHtml(formatDateTime(n.valid_from))}${n.valid_to ? ` al ${escapeHtml(formatDateTime(n.valid_to))}` : ', senza scadenza'}</div>
          <div class="detail-item"><label>Impronta del testo</label><code>${escapeHtml(n.content_hash)}</code></div>
          <div class="detail-item"><label>Pubblicata da</label>${escapeHtml(n.created_by)} il ${escapeHtml(formatDateTime(n.created_at))}</div>
        </div>
        <h4>Testo integrale</h4>
        <pre class="informativa-testo" data-informativa-testo></pre>
        <div class="modal-actions"><button type="button" class="btn ghost" data-azione="chiudi-dettaglio">Chiudi lettura</button></div>
      </div>`;
    // Il testo passa da `textContent`: nessun markup dell’informativa viene interpretato.
    dettaglioEl.querySelector('[data-informativa-testo]').textContent = n.content;
    dettaglioEl.querySelector('[data-azione="chiudi-dettaglio"]').addEventListener('click', () => {
      dettaglioEl.innerHTML = '';
      state.aperta = null;
    });
    dettaglioEl.scrollIntoView({ block: 'nearest' });
  }

  async function chiudi(id) {
    const n = state.righe.find((r) => r.id === id);
    if (!n) return;
    const ok = await confirmAction(host, {
      titolo: `Chiudere la versione ${n.version}?`,
      testo: `Da questo momento la versione «${n.version}» (${labelOf(FINALITA, n.purpose)}) non sara’ piu’ valida per nuovi consensi. I consensi gia’ registrati che la citano restano validi e il testo resta consultabile. Finche’ non pubblichi una nuova versione, gli operatori non potranno registrare consensi per questa finalita’.`,
      conferma: 'Chiudi la versione',
      tono: 'danger',
    });
    if (!ok) return;
    try {
      await apiPost(`${PLATFORM}/consent-notices/${id}/close`, {});
    } catch (error) {
      listaEl.insertAdjacentHTML('afterbegin', `<div class="error-box" data-errore-chiusura>${escapeHtml(spiega(error, 'informativa'))}</div>`);
      return;
    }
    await carica();
  }

  function mostraForm() {
    formEl.innerHTML = `
      <form class="card" id="informativa-nuova-form" data-informativa-form>
        <h3>Nuova versione dell’informativa</h3>
        <p class="muted">Il testo va inserito per intero, ogni volta: niente viene precompilato o copiato dalla versione precedente. Se esiste gia’ una versione corrente per la stessa finalita’, chiudila prima.</p>
        <div class="form-field">
          <label for="inf-purpose">Finalita’</label>
          <select class="input" id="inf-purpose" name="purpose">${selectOptions(FINALITA, state.purpose)}</select>
        </div>
        <div class="form-grid-2">
          <div class="form-field">
            <label for="inf-version">Nome della versione (es. 2026-10, v3)</label>
            <input class="input" id="inf-version" name="version" maxlength="60" required>
          </div>
          <div class="form-field">
            <label for="inf-context">Contesto in cui viene presentata (es. modulo cartaceo in agenzia)</label>
            <input class="input" id="inf-context" name="context" maxlength="200" required>
          </div>
        </div>
        <fieldset class="form-field" data-informativa-canali>
          <legend>Canali descritti dal testo (almeno uno per il marketing)</legend>
          ${Object.entries(CANALI).map(([k, v]) => `<label><input type="checkbox" name="channels" value="${k}"> ${escapeHtml(v)}</label>`).join(' ')}
        </fieldset>
        <div class="form-field">
          <label for="inf-content">Testo integrale dell’informativa</label>
          <textarea class="input" id="inf-content" name="content" rows="14" minlength="20" maxlength="200000" required></textarea>
        </div>
        <div class="form-grid-2">
          <div class="form-field">
            <label for="inf-valid-from">Valida dal</label>
            <input class="input" id="inf-valid-from" type="datetime-local" name="valid_from" value="${adessoLocale()}" required>
          </div>
          <div class="form-field">
            <label for="inf-valid-to">Valida fino al (facoltativo)</label>
            <input class="input" id="inf-valid-to" type="datetime-local" name="valid_to">
          </div>
        </div>
        <div class="field-error" data-informativa-errore role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-azione="annulla">Annulla</button>
          <button type="submit" class="btn primary">Pubblica la versione</button>
        </div>
      </form>`;
    const form = formEl.querySelector('form');
    form.querySelector('[data-azione="annulla"]').addEventListener('click', () => { formEl.innerHTML = ''; });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const errore = form.querySelector('[data-informativa-errore]');
      errore.textContent = '';
      const dati = new FormData(form);
      const corpo = {
        purpose: String(dati.get('purpose') || 'marketing'),
        version: String(dati.get('version') || '').trim(),
        context: String(dati.get('context') || '').trim(),
        content: String(dati.get('content') || ''),
        valid_from: isoConFuso(String(dati.get('valid_from') || '')),
        valid_to: isoConFuso(String(dati.get('valid_to') || '')),
        channels: dati.getAll('channels').map((v) => String(v)),
      };
      if (!corpo.content.trim() || corpo.content.trim().length < 20) {
        errore.textContent = 'Il testo dell’informativa e’ troppo corto: va inserito per intero.';
        return;
      }
      if (corpo.purpose === 'marketing' && !corpo.channels.length) {
        errore.textContent = 'Indica i canali che il testo descrive: un consenso potra’ citare questa versione solo per quei canali.';
        return;
      }
      if (!corpo.valid_from) {
        errore.textContent = 'Indica da quando la versione e’ valida.';
        return;
      }
      const invia = form.querySelector('button[type="submit"]');
      invia.disabled = true;
      try {
        await apiPost(`${PLATFORM}/consent-notices`, corpo);
      } catch (error) {
        invia.disabled = false;
        errore.textContent = spiega(error, 'informativa');
        return;
      }
      formEl.innerHTML = '';
      state.purpose = corpo.purpose;
      container.querySelector('#informative-finalita').value = corpo.purpose;
      await carica();
    });
    form.querySelector('[name="version"]').focus();
  }

  await carica();
}
