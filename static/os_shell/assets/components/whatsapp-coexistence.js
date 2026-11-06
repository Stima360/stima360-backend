// STIMA360 OS — components/whatsapp-coexistence.js
// WHATSAPP COEXISTENCE: Impostazioni → «Caselle WhatsApp» → «Collega il numero
// WhatsApp Business». Il titolare apre la procedura UFFICIALE di Meta
// (Embedded Signup, SDK di Facebook): sceglie il proprio account, inquadra il QR
// con l'app WhatsApp Business sul telefono e decide se condividere lo storico.
// Alla fine Meta restituisce un codice monouso e gli identificativi del WABA e
// del numero; qui si inoltrano al server (POST /api/whatsapp-coexistence/onboardings),
// che li verifica con Meta. Nessun token passa dal browser e nessuno resta salvato.
//
// Tre stati distinti, mai confusi: ciò che il CRM ha CHIESTO a Meta (i passi),
// ciò che Meta CONFERMA («Confermato da Meta») e ciò che è ARRIVATO davvero
// (messaggi, eco dal telefono, storico, rubrica). «Collegato» solo con la
// conferma di Meta. Tutto il testo passa da `textContent`.

import { apiGet, apiPost } from '../core/api-client.js';

const API = '/api/whatsapp-coexistence';
const SDK_URL = 'https://connect.facebook.net/en_US/sdk.js';
// Solo questi mittenti possono consegnare l'esito della procedura.
export const META_ORIGINS = Object.freeze(['https://www.facebook.com', 'https://web.facebook.com']);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className, attr) {
  const b = el('button', className ? `btn ${className}` : 'btn', label);
  b.type = 'button';
  if (attr) b.setAttribute(attr, '');
  return b;
}

function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Operazione non riuscita.';
}

function dateText(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('it-IT', { dateStyle: 'short', timeStyle: 'short' });
}

const STEP_LABELS = {
  code_exchange: 'Codice della procedura accettato da Meta',
  waba_numbers: 'Numero trovato nell’account WhatsApp Business',
  callback_override: 'Indirizzo dei messaggi spostato sul CRM (solo per un account con il solo numero personale)',
  subscribe_webhooks: 'CRM iscritto ai messaggi del numero',
  sync_contacts: 'Richiesta della rubrica del telefono',
  sync_history: 'Richiesta dello storico delle chat',
  meta_check: 'Verifica dello stato presso Meta',
};

/** L'esito della procedura, dal messaggio della finestra di Meta (o null). */
export function sessionInfoFrom(event) {
  if (!event || !META_ORIGINS.includes(event.origin)) return null;
  let data = event.data;
  if (typeof data === 'string') {
    try { data = JSON.parse(data); } catch (_e) { return null; }
  }
  if (!data || data.type !== 'WA_EMBEDDED_SIGNUP') return null;
  const info = data.data || {};
  if (String(data.event || '').startsWith('FINISH')) {
    return { finished: true, waba_id: info.waba_id || null, phone_number_id: info.phone_number_id || null,
      business_id: info.business_id || null };
  }
  if (data.event === 'CANCEL') return { finished: false, cancelled: true, step: info.current_step || null };
  if (data.event === 'ERROR') return { finished: false, error: info.error_message || 'Errore nella procedura di Meta' };
  return null;
}

/** I tre stati distinti di un collegamento, in parole. */
export function onboardingLines(o) {
  const lines = [];
  lines.push(o.connected ? `Confermato da Meta il ${dateText(o.meta_confirmed_at)}` : o.status_label);
  const meta = o.meta || {};
  if (meta.platform_type) {
    const app = meta.is_on_biz_app === true ? 'app WhatsApp Business attiva sul telefono'
      : (meta.is_on_biz_app === false ? 'Meta non indica l’app sul telefono' : 'Meta non indica lo stato dell’app');
    lines.push(`Meta: ${meta.platform_type === 'CLOUD_API' ? 'Cloud API attiva' : meta.platform_type} · ${app}`);
  }
  const ev = o.evidence || {};
  lines.push(`Arrivati: ${ev.live_messages || 0} messaggi in diretta · ${ev.app_echoes || 0} dal telefono · `
    + `${ev.history_messages || 0} dallo storico · ${ev.app_contacts_present || 0} contatti in rubrica`);
  if (ev.history && ev.history.errors && ev.history.errors.length) lines.push('Storico: non condiviso o non disponibile.');
  else if (ev.history) lines.push(`Storico: ${ev.history.progress != null ? `${ev.history.progress}%` : 'in arrivo'}`);
  if (!o.routed) lines.push('Attenzione: il numero non è ancora instradato a questa agenzia (WHATSAPP_INBOUND_ROUTES).');
  if (!o.token_present) lines.push('Attenzione: manca il token di sistema del numero (WHATSAPP_TOKEN__…).');
  return lines;
}

function loadSdk(appId, version) {
  if (window.FB) return Promise.resolve(window.FB);
  return new Promise((resolve, reject) => {
    window.fbAsyncInit = () => {
      window.FB.init({ appId, autoLogAppEvents: true, xfbml: false, version });
      resolve(window.FB);
    };
    const s = document.createElement('script');
    s.src = SDK_URL;
    s.async = true;
    s.defer = true;
    s.crossOrigin = 'anonymous';
    s.onerror = () => reject(new Error('Impossibile caricare la procedura di Meta.'));
    document.head.appendChild(s);
  });
}

export async function mountCoexistencePanel(root) {
  const box = el('section', 'card coex-panel');
  box.setAttribute('data-coexistence', '');
  box.appendChild(el('h3', 'section-title', 'Collega il numero WhatsApp Business (app + CRM)'));
  box.appendChild(el('p', 'muted', 'Il numero resta sull’app WhatsApp Business del telefono: i messaggi ricevuti, quelli che scrivi '
    + 'dall’app e lo storico che scegli di condividere arrivano anche qui. Serve il telefono con l’app aperta.'));
  const msg = el('div', 'coex-messages');
  msg.setAttribute('aria-live', 'polite');
  const list = el('div', 'coex-list');
  box.append(msg, list);
  root.appendChild(box);

  let config = null;
  try { config = await apiGet(`${API}/config`); } catch (error) {
    if (error && error.status === 403) { box.remove(); return; }
    msg.replaceChildren(el('div', 'error-box', errorMessage(error)));
    return;
  }

  function note(text, kind = 'success-box') { msg.replaceChildren(el('div', kind, text)); }

  function renderItems(data) {
    list.replaceChildren();
    for (const o of data.items || []) {
      const row = el('article', `coex-item${o.connected ? ' is-connected' : ''}`);
      row.setAttribute('data-coexistence-item', String(o.id));
      row.setAttribute('data-coexistence-status', o.status);
      row.appendChild(el('strong', null, `${o.display_phone_number ? `+${o.display_phone_number}` : `Numero …${String(o.phone_number_id).slice(-4)}`}`
        + `${o.verified_name ? ` · ${o.verified_name}` : ''}`));
      for (const line of onboardingLines(o)) row.appendChild(el('p', 'coex-line', line));
      const steps = el('ul', 'coex-steps');
      for (const [key, label] of Object.entries(STEP_LABELS)) {
        const st = (o.steps || {})[key];
        if (!st) continue;
        const why = st.error === 'not_routed_yet' ? 'da chiedere dopo aver instradato il numero' : (st.error || 'non riuscito');
        const li = el('li', st.ok ? 'ok' : 'ko', `${st.ok ? '✓' : '✗'} ${label}${st.ok ? '' : ` (${why})`}`);
        steps.appendChild(li);
      }
      row.appendChild(steps);
      const actions = el('div', 'coex-actions');
      const verify = button('Verifica con Meta', 'btn-small', 'data-coexistence-verify');
      verify.addEventListener('click', async () => {
        verify.disabled = true;
        try { await apiPost(`${API}/onboardings/${o.id}/verify`, {}); note('Stato aggiornato da Meta.'); await refresh(); }
        catch (error) { note(errorMessage(error), 'error-box'); verify.disabled = false; }
      });
      actions.appendChild(verify);
      if (data.can_connect) {
        for (const [kind, label] of [['contacts', 'Richiedi di nuovo la rubrica'], ['history', 'Richiedi di nuovo lo storico']]) {
          const b = button(label, 'btn-small ghost', 'data-coexistence-sync');
          b.setAttribute('data-kind', kind);
          b.addEventListener('click', async () => {
            b.disabled = true;
            try { await apiPost(`${API}/onboardings/${o.id}/sync`, { kind }); note('Richiesta inviata a Meta.'); await refresh(); }
            catch (error) { note(errorMessage(error), 'error-box'); b.disabled = false; }
          });
          actions.appendChild(b);
        }
      }
      row.appendChild(actions);
      list.appendChild(row);
    }
  }

  async function refresh() {
    try { renderItems(await apiGet(`${API}/onboardings`)); } catch (error) { note(errorMessage(error), 'error-box'); }
  }

  if (!config.installed) {
    note('Il collegamento non è ancora attivo su questo ambiente (migrazione 132).', 'warning-box');
  } else if (!config.enabled) {
    note(`La procedura di Meta non è configurata su questo ambiente. Mancano: ${(config.missing || []).join(', ')}.`, 'warning-box');
  }
  if (config.can_connect && config.enabled) {
    const start = button('Collega con Meta', 'primary', 'data-coexistence-start');
    box.insertBefore(start, msg);
    let pending = null;
    let code = null;
    async function finish() {
      if (!pending || !code) return;
      const body = { code, waba_id: String(pending.waba_id) };
      if (pending.phone_number_id) body.phone_number_id = String(pending.phone_number_id);
      if (pending.business_id) body.business_id = String(pending.business_id);
      pending = null; code = null;
      note('Verifica con Meta in corso…', 'muted');
      try {
        const out = await apiPost(`${API}/onboardings`, body);
        note(out.connected ? 'Confermato da Meta.' : `Procedura completata. ${out.status_label}.`);
      } catch (error) { note(errorMessage(error), 'error-box'); }
      start.disabled = false;
      await refresh();
    }
    window.addEventListener('message', (event) => {
      const info = sessionInfoFrom(event);
      if (!info) return;
      if (info.finished && info.waba_id) { pending = info; finish(); }
      else if (info.cancelled) { note(`Procedura interrotta${info.step ? ` al passo «${info.step}»` : ''}.`, 'warning-box'); start.disabled = false; }
      else if (info.error) { note(info.error, 'error-box'); start.disabled = false; }
    });
    start.addEventListener('click', async () => {
      start.disabled = true;
      try {
        const FB = await loadSdk(config.app_id, config.graph_version);
        FB.login((response) => {
          if (response && response.authResponse && response.authResponse.code) { code = response.authResponse.code; finish(); }
          else { note('Procedura chiusa senza collegamento.', 'warning-box'); start.disabled = false; }
        }, {
          config_id: config.config_id, response_type: 'code', override_default_response_type: true,
          extras: { setup: {}, featureType: 'whatsapp_business_app_onboarding', sessionInfoVersion: '3' },
        });
      } catch (error) { note(errorMessage(error), 'error-box'); start.disabled = false; }
    });
  }
  if (config.installed) await refresh();
}
