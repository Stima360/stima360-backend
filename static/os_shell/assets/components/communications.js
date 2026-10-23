// P29-3D — la tab "Comunicazioni" del Contact 360.
//
// COSA MOSTRA, E PERCHE' IN QUEST'ORDINE
//
//   1. lo stato delle AUTOMAZIONI del contatto (attive o in pausa)
//   2. la card della JOURNEY aperta, se c'e': a che passo e', quando parte
//      il prossimo, e i pulsanti per intervenire
//   3. lo STORICO, una lista cronologica sola: la mail della stima, i passi
//      della sequenza e i messaggi scritti a mano, insieme
//   4. il pulsante per scrivere un messaggio
//
// Prima lo stato, poi la storia: chi apre questa tab quasi sempre vuole
// sapere "sta partendo qualcosa?" prima di "cos'e' partito".
//
// COSA NON MOSTRA. Il token di claim, la chiave di idempotenza, il testo
// grezzo di un errore del provider: non arrivano nemmeno dall'API (li toglie
// `communication/contact_view.py`), e questa pagina non li chiede.
//
// LE RISPOSTE IN ARRIVO NON SONO MODELLATE. La nota lo dice in una riga. Non
// si scrive "nessuna risposta", perche' il sistema non lo sa: scriverlo
// sarebbe un'informazione falsa travestita da interfaccia vuota.
//
// SENZA LA MIGRATION 071 la parte journey non esiste: l'API risponde
// `available: false`, e qui si mostra lo storico con una riga che spiega
// perche' l'automazione non e' disponibile. Nessun errore, nessuna pagina
// bianca.

import { apiGet, apiPost } from '../core/api-client.js';
import { escapeHtml, formatDateTime, renderBadge } from './st-table.js';

const TONI_STATO = {
  queued: 'blue', sending: 'blue', sent: 'green', failed: 'red',
  indeterminate: 'orange', suppressed: 'gray', cancelled: 'gray',
};

function badgeStato(messaggio) {
  return renderBadge(messaggio.status_label || messaggio.status,
                     TONI_STATO[messaggio.status] || 'gray');
}

function renderNotaInbound(nota) {
  return `<p class="muted" data-inbound-note style="margin-top:12px">${escapeHtml(nota)}</p>`;
}

function renderAutomazione(journey) {
  if (!journey || journey.available === false) {
    return `<div class="panel" data-automation-panel>
      <p class="muted">Automazioni non disponibili su questo ambiente.</p>
    </div>`;
  }
  const automazione = journey.automation || { paused: false };
  const stato = automazione.paused
    ? renderBadge('in pausa', 'orange')
    : renderBadge('attive', 'green');
  const quando = automazione.paused && automazione.paused_at
    ? ` <span class="muted">dal ${escapeHtml(formatDateTime(automazione.paused_at))}</span>` : '';
  const motivo = automazione.paused && automazione.pause_reason
    ? ` <span class="muted">— ${escapeHtml(automazione.pause_reason)}</span>` : '';
  const azione = automazione.paused
    ? '<button type="button" class="btn" data-action="automation-resume">Riprendi automazioni</button>'
    : '<button type="button" class="btn" data-action="automation-pause">Metti in pausa le automazioni</button>';
  return `<div class="panel" data-automation-panel>
    <div><strong>Automazioni contatto:</strong> ${stato}${quando}${motivo}</div>
    <p class="muted">La pausa ferma le sequenze automatiche. I messaggi scritti a mano partono comunque.</p>
    <div class="actions">${azione}</div>
  </div>`;
}

function renderCardJourney(journey) {
  if (!journey || journey.available === false) return '';
  const iscrizione = journey.enrollment;
  if (!iscrizione) {
    return `<div class="panel" data-journey-card>
      <p class="muted">Nessuna automazione in corso per questo contatto.</p>
    </div>`;
  }
  const passo = iscrizione.current_step;
  const azioni = [];
  if (iscrizione.can_send_current) {
    azioni.push('<button type="button" class="btn btn-primary" data-action="send-current">Approva e invia</button>');
    azioni.push('<button type="button" class="btn" data-action="skip-current">Salta questo passo</button>');
  }
  if (iscrizione.can_pause) {
    azioni.push('<button type="button" class="btn" data-action="enrollment-pause">Metti in pausa</button>');
  }
  if (iscrizione.can_resume) {
    azioni.push('<button type="button" class="btn" data-action="enrollment-resume">Riprendi</button>');
  }
  azioni.push('<button type="button" class="btn btn-danger" data-action="enrollment-stop">Interrompi</button>');

  return `<div class="panel" data-journey-card data-enrollment-id="${iscrizione.id}">
    <div><strong>${escapeHtml(iscrizione.journey_name || 'Automazione')} attiva</strong>
      ${renderBadge(iscrizione.status_label, iscrizione.awaiting_operator ? 'orange'
        : iscrizione.status === 'paused' ? 'gray' : 'green')}</div>
    <dl class="kv">
      <dt>Passo corrente</dt>
      <dd>${passo ? `${escapeHtml(passo.step_key)} — ${escapeHtml(passo.mode_label)}` : '—'}
        ${passo ? `<span class="muted">(${iscrizione.current_step.step_no} di ${iscrizione.total_steps})</span>` : ''}</dd>
      <dt>${iscrizione.awaiting_operator ? 'In attesa dal' : 'Prossimo invio previsto'}</dt>
      <dd>${escapeHtml(formatDateTime(iscrizione.awaiting_operator
        ? iscrizione.awaiting_since : iscrizione.next_action_at))}</dd>
    </dl>
    ${iscrizione.awaiting_operator
      ? '<p class="muted">Questo passo non parte da solo: lo manda una persona.</p>' : ''}
    <div class="actions">${azioni.join(' ')}</div>
  </div>`;
}

// Fase B — il CONSENSO MARKETING, come lo vede la guardia, e l'azione
// «Registra consenso marketing» per chi lo ha raccolto di persona (modulo
// cartaceo o digitale, con un riferimento alla prova e l'informativa
// presentata). Nessun consenso si registra senza prova e senza informativa:
// lo rifiuta il server, qui si chiede tutto quello che serve.
const ETICHETTE_CONSENSO = {
  allow_explicit_grant: 'concesso',
  deny_revoked: 'revocato',
  deny_never_given: 'mai dato',
  deny_inconsistent_state: 'stato incoerente: verificare',
  deny_platform_optout: 'disiscritto da tutto STIMA360',
};

// 109 (C1/C2): il consenso e' PER CANALE. Nessuna casella preselezionata:
// l'operatore spunta ogni canale che la persona ha autorizzato davvero.
const CANALI = { email: 'Email', sms: 'SMS', whatsapp: 'WhatsApp', phone: 'Telefonate commerciali' };
const ORDINE_CANALI = ['email', 'sms', 'whatsapp', 'phone'];

function caselleCanali(nome, disponibili) {
  return `<fieldset class="consent-channels" data-${nome}-channels>
    <legend>Canali</legend>
    ${ORDINE_CANALI.map((c) => {
      const ammesso = !disponibili || disponibili.includes(c);
      return `<label><input type="checkbox" name="channels" value="${c}" ${ammesso ? '' : 'disabled'}> ${escapeHtml(CANALI[c])}</label>`;
    }).join('')}
  </fieldset>`;
}

function rigaCanale(canale, stato, raccolta, sito) {
  // C3.2: la spunta del sito su un canale che non ha ancora un consenso: si
  // vede come scelta registrata, distinta da un consenso che autorizza invii.
  // Una scelta digitale piu' recente (email da confermare, numero da
  // verificare) prevale sulla spunta del sito.
  const inAttesa = Boolean(raccolta && raccolta.pending_confirmation) && !stato.allowed;
  const daVerificare = Boolean(raccolta && raccolta.declared_unverified) && !stato.allowed;
  const daSito = Boolean(sito) && !stato.allowed && stato.reason === 'deny_never_given' && !inAttesa && !daVerificare;
  let etichetta = ETICHETTE_CONSENSO[stato.reason] || stato.reason || '—';
  if (inAttesa) etichetta = 'in attesa di conferma';
  else if (daVerificare) etichetta = 'scelto · numero da verificare';
  else if (daSito) etichetta = 'scelto sul sito · da confermare';
  const quando = stato.decided_at
    ? ` <span class="muted">dal ${escapeHtml(formatDateTime(stato.decided_at))}</span>` : '';
  const optout = stato.platform_optout_at
    ? ` <span class="muted" data-platform-optout="${canale}">— disiscritto da tutto STIMA360 il ${escapeHtml(formatDateTime(stato.platform_optout_at))}</span>` : '';
  // C3: cio' che il cliente ha chiesto dalla pagina e che NON e' ancora attivo.
  const attesa = raccolta && raccolta.pending_confirmation
    ? ` <span class="muted" data-consent-pending="${canale}">— in attesa della conferma del cliente fino al ${escapeHtml(formatDateTime(raccolta.pending_confirmation.expires_at))}</span>` : '';
  const dichiarato = raccolta && raccolta.declared_unverified
    ? ` <span class="muted" data-consent-declared="${canale}">— scelto dal cliente, non attivo finche\u0027 il numero non sara\u0027 verificato</span>` : '';
  const notaSito = daSito
    ? ` <span class="muted" data-site-choice="${canale}">— spuntato su stima360.it il ${escapeHtml(formatDateTime(sito.decided_at))}${sito.stima_id ? ` (richiesta di valutazione n. ${escapeHtml(String(sito.stima_id))})` : ''}: non autorizza invii finche\u0027 il cliente non conferma</span>` : '';
  return `<li data-consent-channel="${canale}"><strong>${escapeHtml(CANALI[canale])}:</strong> ${renderBadge(etichetta, stato.allowed ? 'green' : (daSito || inAttesa || daVerificare ? 'warn' : 'red'))}${quando}${optout}${attesa}${dichiarato}${notaSito}</li>`;
}

// C3.2: cio' che il CRM ha ricevuto dal sito stima360.it. Due cose diverse
// dette in modo diverso: la Privacy (il modulo non parte senza la spunta, ma
// il sito non ne manda la prova: NON documentata) e la spunta marketing
// (registrata: data, richiesta, fonte; senza canale ne' testo: NON autorizza).
function renderSito(consenso) {
  const sito = consenso.site_record;
  if (!sito || !sito.from_site) return '';
  const scelte = (sito.marketing_choices || []).filter((s) => s.decision === 'granted');
  const canaliCasella = (sito.checkbox_channels || []).map((c) => CANALI[c] || c).join(' + ');
  const righe = scelte.map((s) => `<li data-site-marketing-choice="${Number(s.event_id)}">
      <strong>Scelta registrata dal sito</strong> — casella marketing (${escapeHtml(canaliCasella)}) spuntata il ${escapeHtml(formatDateTime(s.decided_at))}
      ${s.stima_id ? ` · richiesta di valutazione n. ${escapeHtml(String(s.stima_id))}` : ''}
      · registrata nel CRM il ${escapeHtml(formatDateTime(s.recorded_at))}
    </li>`).join('');
  const marketing = scelte.length
    ? `<ul class="consent-site-list">${righe}</ul>
      <p class="muted" data-site-proofs>Prove conservate: la spunta, la data registrata dal server del sito, la richiesta di valutazione.
        Non conservati dal sito: il testo e la versione dell\u0027informativa mostrati.
        Per questo la scelta non e\u0027 un consenso autorizzato per gli invii: il cliente puo\u0027 confermarla con il link o il QR qui sotto.</p>`
    : (consenso.legacy
      // Il vecchio flag senza evento: una spunta c'e', ma senza data della
      // richiesta ne' riferimento; la si mostra per quello che e'.
      ? `<p class="muted" data-site-legacy-flag><strong>Spunta marketing storica</strong> presente nel CRM${consenso.legacy_granted_at ? ` (dal ${escapeHtml(formatDateTime(consenso.legacy_granted_at))})` : ''}, senza riferimento alla richiesta ne\u0027 canale: non autorizza invii. Il cliente puo\u0027 confermarla con il link o il QR qui sotto.</p>`
      : '<p class="muted" data-site-no-marketing>Nessuna scelta marketing ricevuta dal sito per questo contatto.</p>');
  return `<div class="panel" data-site-record>
    <div><strong>Dal sito stima360.it</strong></div>
    <p class="muted" data-site-privacy><strong>Privacy e Termini:</strong> il modulo del sito non si invia senza la spunta,
      ma il sito non trasmette al CRM la prova dell\u0027accettazione: <strong>non documentata nel CRM</strong>.</p>
    ${marketing}
  </div>`;
}

// C3: la raccolta DIGITALE. L'operatore genera un link personale (30 giorni)
// con il suo QR; il cliente legge l'informativa e sceglie da solo i canali.
// Il link e il QR compaiono SOLO alla generazione: il server ne conserva
// l'impronta, non il link. Nessun invio parte da qui.
function renderRaccolta(consenso) {
  const raccolta = consenso.capture;
  if (!raccolta || raccolta.available === false || !consenso.can_record) return '';
  const link = raccolta.link;
  const aperto = Boolean(link && link.collecting);
  const stato = aperto
    ? `Link attivo: raccoglie consensi fino al ${escapeHtml(formatDateTime(link.expires_at))}.`
    : 'Nessun link attivo.';
  // C3.2: senza informativa marketing corrente la pagina permette solo di
  // revocare. Lo si dice qui, prima di consegnare il QR al cliente.
  const senzaInformativa = raccolta.notice === null
    ? '<p class="error-box" data-capture-no-notice>Nessuna informativa marketing corrente: il cliente potra\u0027 solo revocare. Pubblicala in Super Admin › Rete › Informative privacy; il link gia\u0027 generato funzionera\u0027 subito dopo.</p>'
    : '';
  return `<div class="panel" data-consent-capture data-link-open="${aperto ? '1' : '0'}">
    <div><strong>Raccolta digitale del consenso</strong></div>
    <p class="muted">Il cliente apre il link o inquadra il QR, legge l\u0027informativa e sceglie da solo i canali.
      L\u0027email diventa attiva solo dopo la sua conferma; SMS, WhatsApp e telefonate restano non attivi finche\u0027 il numero non sara\u0027 verificato.</p>
    ${senzaInformativa}
    <p class="muted" data-consent-link-status>${stato}</p>
    <div class="actions">
      <button type="button" class="btn btn-primary" data-consent-link-generate>${aperto ? 'Genera nuovo link consenso' : 'Genera link consenso'}</button>
      ${aperto ? '<button type="button" class="btn" data-consent-link-disable>Disattiva link</button>' : ''}
    </div>
    <div data-consent-link-output hidden>
      <p class="muted">Il link e il QR si vedono solo adesso. Consegnali al cliente di persona o in una conversazione avviata da lui: non inviarli in massa.</p>
      <label>Link personale <input type="text" class="input" readonly data-consent-link-url></label>
      <div class="actions"><button type="button" class="btn" data-consent-link-copy>Copia link</button></div>
      <img data-consent-link-qr alt="QR code del link consenso" width="220" height="220">
      <p class="muted" data-consent-link-expiry></p>
    </div>
    <p class="error-box" data-consent-link-error hidden></p>
  </div>`;
}

function renderConsenso(consenso, informative) {
  if (!consenso || consenso.available === false) {
    return `<div class="panel" data-consent-panel>
      <p class="muted">Consenso marketing non disponibile su questo ambiente.</p>
    </div>`;
  }
  const canali = consenso.channels || {};
  const raccolta = (consenso.capture && consenso.capture.channels) || {};
  const sitoRec = consenso.site_record || {};
  const scelteSito = (sitoRec.marketing_choices || []).filter((s) => s.decision === 'granted');
  const ultimaSito = scelteSito.length ? scelteSito[scelteSito.length - 1] : null;
  const canaliSito = new Set(sitoRec.checkbox_channels || []);
  const righe = ORDINE_CANALI.filter((c) => canali[c])
    .map((c) => rigaCanale(c, canali[c], raccolta[c], canaliSito.has(c) ? ultimaSito : null)).join('');
  // Con una scelta del sito lo storico lo racconta il blocco «Dal sito».
  const storico = consenso.legacy && !scelteSito.length && !sitoRec.from_site
    ? '<p class="muted" data-consent-legacy>Esiste un consenso storico senza canale: non autorizza nessun invio finche\u0027 non viene documentato per canale.</p>' : '';
  const coperti = (informative || []).flatMap((n) => n.channels || []);
  const opzioniInformative = (informative || []).map((n) =>
    `<option value="${Number(n.id)}">${escapeHtml(n.version)} — ${escapeHtml(n.context)}${(n.channels || []).length ? ` (${escapeHtml((n.channels || []).map((c) => CANALI[c] || c).join(', '))})` : ''}</option>`).join('');
  const senzaInformative = !(informative || []).length
    ? '<p class="error-box" data-no-notice>Nessuna informativa marketing valida: il consenso non si puo\u0027 registrare finche\u0027 il Super Admin non ne pubblica una.</p>' : '';
  const form = consenso.can_record ? `<details class="panel" data-consent-grant>
    <summary>Registra consenso marketing</summary>
    <form data-consent-grant-form>
      <p class="muted">Solo un consenso raccolto davvero, con una prova che si puo\u0027 ritrovare.
        Un indirizzo in rubrica o una telefonata non sono una prova.</p>
      ${senzaInformative}
      ${caselleCanali('grant', coperti)}
      <label>Data e ora della raccolta <input type="datetime-local" name="decided_at" required></label>
      <label>Modalita\u0027
        <select name="evidence_type">
          <option value="paper_form">Modulo cartaceo</option>
          <option value="digital_form">Modulo digitale</option>
          <option value="other_documented">Altra modalita\u0027 documentata</option>
        </select>
      </label>
      <label>Riferimento alla prova (protocollo, archivio, documento)
        <input type="text" name="evidence_ref" minlength="6" maxlength="100" required></label>
      <label>Informativa presentata
        <select name="notice_id" required>${opzioniInformative}</select>
      </label>
      <label>Note <textarea name="note" rows="2" maxlength="2000"></textarea></label>
      <div class="actions"><button type="submit" class="btn btn-primary" ${(informative || []).length ? '' : 'disabled'}>Registra consenso</button></div>
      <p class="error-box" data-consent-error hidden></p>
    </form>
  </details>
  <details class="panel" data-consent-refusal>
    <summary>Registra rifiuto o revoca espressi al personale</summary>
    <form data-consent-refusal-form>
      <p class="muted">Blocca subito gli invii di marketing sui canali che spunti. L\u0027ambito lo dichiari tu: questa agenzia, oppure tutto STIMA360.</p>
      ${caselleCanali('refusal', null)}
      <label>Data e ora <input type="datetime-local" name="decided_at" required></label>
      <label>Ambito
        <select name="scope">
          <option value="agency">Questa agenzia</option>
          <option value="platform">Tutto STIMA360 (ogni agenzia)</option>
        </select>
      </label>
      <label>Come e\u0027 stato espresso <textarea name="note" rows="2" minlength="10" maxlength="2000" required></textarea></label>
      <div class="actions"><button type="submit" class="btn btn-danger">Registra revoca</button></div>
      <p class="error-box" data-refusal-error hidden></p>
    </form>
  </details>` : '';
  return `<div class="panel" data-consent-panel>
    <div><strong>Consenso marketing per canale</strong></div>
    <ul class="consent-channel-list">${righe}</ul>
    ${storico}
    ${renderSito(consenso)}
    ${renderRaccolta(consenso)}
    ${form}
  </div>`;
}

function isoConFuso(valore) {
  // `datetime-local` non porta il fuso: si interpreta nell\u0027ora locale del
  // browser e si manda in ISO con offset, come il server pretende.
  if (!valore) return '';
  const data = new Date(valore);
  if (Number.isNaN(data.getTime())) return '';
  return data.toISOString();
}

function renderStorico(messaggi) {
  if (!messaggi.length) {
    return '<p class="muted">Nessun messaggio inviato o programmato per questo contatto.</p>';
  }
  const righe = messaggi.map((m) => {
    const quando = m.sent_at || m.scheduled_at || m.created_at;
    const azioni = [];
    if (m.can_send_now) azioni.push(`<button type="button" class="btn btn-sm" data-action="send-now" data-message-id="${m.id}">Invia ora</button>`);
    if (m.can_cancel) azioni.push(`<button type="button" class="btn btn-sm" data-action="cancel" data-message-id="${m.id}">Annulla</button>`);
    return `<tr data-message-row="${m.id}">
      <td>${escapeHtml(formatDateTime(quando))}</td>
      <td>${escapeHtml(m.channel || '—')}</td>
      <td>${escapeHtml(m.reason_label || '—')}</td>
      <td>${escapeHtml(m.mode_label || '—')}</td>
      <td>${badgeStato(m)}${m.next_send_at
        ? `<div class="muted">invio previsto ${escapeHtml(formatDateTime(m.next_send_at))}</div>` : ''}</td>
      <td><div>${escapeHtml(m.subject || '—')}</div>
        <div class="muted">${escapeHtml(m.preview || '')}</div></td>
      <td>${azioni.join(' ')}</td>
    </tr>`;
  }).join('');
  return `<table class="st-table" data-messages-table>
    <thead><tr><th>Data</th><th>Canale</th><th>Motivo</th><th>Tipo</th>
      <th>Stato</th><th>Oggetto</th><th></th></tr></thead>
    <tbody>${righe}</tbody></table>`;
}

function renderNuovoMessaggio() {
  return `<details class="panel" data-new-message>
    <summary>Nuovo messaggio</summary>
    <form data-manual-form>
      <p class="muted">Il destinatario e' l'indirizzo del contatto. Un messaggio
        di marketing parte solo se il consenso c'e'.</p>
      <label>Oggetto <input type="text" name="subject" maxlength="200" required></label>
      <label>Testo <textarea name="body" rows="6" maxlength="20000" required></textarea></label>
      <label>Tipo
        <select name="communication_type">
          <option value="marketing">Marketing</option>
          <option value="service">Servizio</option>
        </select>
      </label>
      <div class="actions">
        <button type="submit" class="btn btn-primary">Metti in coda</button>
      </div>
      <p class="error-box" data-manual-error hidden></p>
    </form>
  </details>`;
}

export function renderCommunications(stato) {
  if (stato.status === 'loading') return '<p class="muted">Caricamento…</p>';
  if (stato.status === 'error') {
    return `<div class="error-box">Errore nel caricamento delle comunicazioni: ${escapeHtml(stato.message || '')}</div>`;
  }
  return [
    renderAutomazione(stato.journey),
    renderConsenso(stato.consent, stato.notices),
    renderCardJourney(stato.journey),
    '<h3>Storico comunicazioni</h3>',
    renderStorico(stato.messages || []),
    renderNotaInbound(stato.inboundNote),
    renderNuovoMessaggio(),
  ].join('\n');
}

export async function loadCommunications(contactId) {
  const [messaggi, journey, consent, informative] = await Promise.all([
    apiGet(`/api/communication/contacts/${contactId}/messages`),
    // La journey puo' non esserci (migration non applicata): non deve far
    // fallire lo storico, che e' la parte che esiste sempre.
    apiGet(`/api/communication/contacts/${contactId}/journey`).catch(() => ({ available: false })),
    apiGet(`/api/communication/contacts/${contactId}/consent`).catch(() => ({ available: false })),
    apiGet('/api/communication/consent/notices').catch(() => ({ notices: [] })),
  ]);
  return {
    status: 'ready',
    messages: messaggi.messages || [],
    inboundNote: messaggi.inbound_note,
    journey,
    consent,
    notices: informative.notices || [],
  };
}

export function mountCommunications(mount, contactId, { readOnly = false } = {}) {
  if (!mount) return;

  async function ridisegna() {
    mount.innerHTML = renderCommunications({ status: 'loading' });
    try {
      mount.innerHTML = renderCommunications(await loadCommunications(contactId));
    } catch (errore) {
      mount.innerHTML = renderCommunications({ status: 'error', message: errore.message });
    }
    if (readOnly) soloLettura();
  }

  // CESTINO-CONTATTI-1: per un contatto nel Cestino lo storico resta
  // leggibile, ma nessun comando (il backend li rifiuterebbe tutti): niente
  // pulsanti d'azione, niente «Nuovo messaggio».
  function soloLettura() {
    for (const el of Array.from(mount.querySelectorAll('button[data-action]'))) el.remove();
    for (const el of Array.from(mount.querySelectorAll('[data-new-message]'))) el.remove();
    for (const el of Array.from(mount.querySelectorAll('[data-consent-grant], [data-consent-refusal]'))) el.remove();
    mount.insertAdjacentHTML('afterbegin', '<p class="muted" data-comm-readonly>Contatto nel Cestino: automazioni sospese, nessun messaggio nuovo. Si riattivano a mano dopo il ripristino.</p>');
  }

  const ROTTE = {
    'automation-pause': () => `/api/communication/contacts/${contactId}/automation/pause`,
    'automation-resume': () => `/api/communication/contacts/${contactId}/automation/resume`,
  };

  // C3: genera / copia / disattiva il link personale del consenso.
  mount.addEventListener('click', async (evento) => {
    const genera = evento.target.closest('button[data-consent-link-generate]');
    const disattiva = evento.target.closest('button[data-consent-link-disable]');
    const copia = evento.target.closest('button[data-consent-link-copy]');
    if (!genera && !disattiva && !copia) return;
    const box = mount.querySelector('[data-consent-capture]');
    const errore = box.querySelector('[data-consent-link-error]');
    errore.hidden = true;
    if (copia) {
      const campo = box.querySelector('[data-consent-link-url]');
      campo.select();
      try {
        await navigator.clipboard.writeText(campo.value);
        copia.textContent = 'Copiato';
      } catch {
        copia.textContent = 'Seleziona e copia il link';
      }
      return;
    }
    if (genera && box.dataset.linkOpen === '1'
        && !window.confirm('Il link precedente non raccogliera\u0027 piu\u0027 consensi (le revoche restano possibili). Generare un nuovo link?')) return;
    if (disattiva && !window.confirm('Il link non raccogliera\u0027 piu\u0027 consensi; le revoche restano possibili. Disattivarlo?')) return;
    const bottone = genera || disattiva;
    bottone.disabled = true;
    try {
      if (disattiva) {
        await apiPost(`/api/communication/contacts/${contactId}/consent/link/disable`, {});
        await ridisegna();
        return;
      }
      const esito = await apiPost(`/api/communication/contacts/${contactId}/consent/link`, {});
      const scadenza = `Raccoglie consensi fino al ${formatDateTime(esito.expires_at)}.`;
      box.querySelector('[data-consent-link-url]').value = String(esito.url || '');
      const qr = String(esito.qr_svg_data_uri || '');
      if (qr.startsWith('data:image/svg+xml')) box.querySelector('[data-consent-link-qr]').src = qr;
      box.querySelector('[data-consent-link-expiry]').textContent = scadenza;
      box.querySelector('[data-consent-link-status]').textContent = `Link attivo: ${scadenza.charAt(0).toLowerCase()}${scadenza.slice(1)}`;
      box.dataset.linkOpen = '1';
      genera.textContent = 'Genera nuovo link consenso';
      if (!box.querySelector('[data-consent-link-disable]')) {
        const spegni = document.createElement('button');
        spegni.type = 'button';
        spegni.className = 'btn';
        spegni.dataset.consentLinkDisable = '';
        spegni.textContent = 'Disattiva link';
        genera.parentElement.appendChild(spegni);
      }
      box.querySelector('[data-consent-link-output]').hidden = false;
      bottone.disabled = false;
    } catch (guasto) {
      bottone.disabled = false;
      errore.hidden = false;
      errore.textContent = guasto.message || 'Operazione non riuscita.';
    }
  });

  mount.addEventListener('click', async (evento) => {
    const bottone = evento.target.closest('button[data-action]');
    if (!bottone) return;
    const azione = bottone.dataset.action;
    const card = mount.querySelector('[data-journey-card]');
    const iscrizione = card && card.dataset.enrollmentId;
    let percorso = null;
    if (ROTTE[azione]) percorso = ROTTE[azione]();
    else if (azione === 'send-now' || azione === 'cancel') {
      percorso = `/api/communication/messages/${bottone.dataset.messageId}/`
        + (azione === 'send-now' ? 'send-now' : 'cancel');
    } else if (iscrizione) {
      const coda = {
        'send-current': 'send-current', 'skip-current': 'skip-current',
        'enrollment-pause': 'pause', 'enrollment-resume': 'resume',
        'enrollment-stop': 'stop',
      }[azione];
      if (coda) percorso = `/api/communication/journeys/enrollments/${iscrizione}/${coda}`;
    }
    if (!percorso) return;
    bottone.disabled = true;
    try {
      await apiPost(percorso, {});
      await ridisegna();
    } catch (errore) {
      bottone.disabled = false;
      const box = document.createElement('p');
      box.className = 'error-box';
      box.textContent = errore.message || 'Operazione non riuscita.';
      bottone.parentElement.appendChild(box);
    }
  });

  mount.addEventListener('submit', async (evento) => {
    const grant = evento.target.closest('form[data-consent-grant-form]');
    const refusal = evento.target.closest('form[data-consent-refusal-form]');
    if (grant || refusal) {
      evento.preventDefault();
      const modulo = grant || refusal;
      const errore = modulo.querySelector(grant ? '[data-consent-error]' : '[data-refusal-error]');
      errore.hidden = true;
      const dati = new FormData(modulo);
      const canaliScelti = dati.getAll('channels').map((v) => v.toString());
      if (!canaliScelti.length) {
        errore.hidden = false;
        errore.textContent = 'Spunta almeno un canale: un consenso o una revoca valgono solo per i canali indicati.';
        return;
      }
      const corpo = grant ? {
        channels: canaliScelti,
        decided_at: isoConFuso((dati.get('decided_at') || '').toString()),
        evidence_type: (dati.get('evidence_type') || '').toString(),
        evidence_ref: (dati.get('evidence_ref') || '').toString(),
        notice_id: Number(dati.get('notice_id') || 0),
        note: (dati.get('note') || '').toString() || null,
      } : {
        channels: canaliScelti,
        decided_at: isoConFuso((dati.get('decided_at') || '').toString()),
        scope: (dati.get('scope') || 'agency').toString(),
        note: (dati.get('note') || '').toString(),
      };
      try {
        await apiPost(`/api/communication/contacts/${contactId}/consent/${grant ? 'grant' : 'refusal'}`, corpo);
        await ridisegna();
      } catch (guasto) {
        errore.hidden = false;
        errore.textContent = guasto.message || 'Registrazione non riuscita.';
      }
      return;
    }
    const form = evento.target.closest('form[data-manual-form]');
    if (!form) return;
    evento.preventDefault();
    const errore = form.querySelector('[data-manual-error]');
    errore.hidden = true;
    const dati = new FormData(form);
    try {
      await apiPost(`/api/communication/contacts/${contactId}/messages`, {
        subject: (dati.get('subject') || '').toString(),
        body: (dati.get('body') || '').toString(),
        communication_type: (dati.get('communication_type') || 'marketing').toString(),
      });
      await ridisegna();
    } catch (guasto) {
      errore.hidden = false;
      errore.textContent = guasto.message || 'Il messaggio non e\u0027 stato messo in coda.';
    }
  });

  return ridisegna();
}
