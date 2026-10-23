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
import { escapeHtml, formatDate, formatDateTime, renderBadge } from './st-table.js';

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
//
// SEMPLIFICAZIONE (C3.3): in cima quattro righe, una per canale, con uno
// stato breve. L'origine della scelta in un riepilogo compatto, con lo
// storico completo dentro «Mostra dettagli». La raccolta digitale con i
// soli comandi: QR/link, consenso manuale, revoca. Le regole di
// autorizzazione restano quelle del server: qui cambia solo la lettura.
const ETICHETTE_CONSENSO = {
  allow_explicit_grant: 'Concesso',
  deny_revoked: 'Revocato',
  deny_never_given: 'Mai dato',
  deny_inconsistent_state: 'Stato incoerente',
  deny_platform_optout: 'Disiscritto',
};
const TONI_CONSENSO = {
  allow_explicit_grant: 'ok', deny_revoked: 'danger', deny_never_given: 'gray',
  deny_inconsistent_state: 'warn', deny_platform_optout: 'danger',
};

// 109 (C1/C2): il consenso e' PER CANALE. Nessuna casella preselezionata:
// l'operatore spunta ogni canale che la persona ha autorizzato davvero.
const CANALI = { email: 'Email', sms: 'SMS', whatsapp: 'WhatsApp', phone: 'Telefonate' };
const ORDINE_CANALI = ['email', 'sms', 'whatsapp', 'phone'];

// Icone a tratto, inline: nessuna dipendenza esterna, nessun testo dentro.
const SVG = (corpo, classe = '') => `<svg class="${classe}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${corpo}</svg>`;
const ICONE = {
  email: SVG('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>'),
  sms: SVG('<path d="M4 5h16v11H9l-5 4z"/>'),
  whatsapp: SVG('<path d="M4.5 19.5l1.2-3.6A8 8 0 1 1 8.6 18.4z"/><path d="M9.3 9.2c.2 2.3 2.2 4.3 4.5 4.5l1.1-1.1 1.9.8c0 1.1-.9 1.9-2 1.9-3.3 0-6.2-2.9-6.2-6.2 0-1.1.8-2 1.9-2l.8 1.9z"/>', 'comm-ico-wa'),
  phone: SVG('<path d="M5 4h3.5l1.7 4.3-2.1 1.5a11.5 11.5 0 0 0 6.1 6.1l1.5-2.1L20 15.5V19a2 2 0 0 1-2 2A15 15 0 0 1 3 6a2 2 0 0 1 2-2z"/>'),
  sito: SVG('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  doc: SVG('<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M9.5 12h5M9.5 16h5"/>'),
  qr: SVG('<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM19 14h2M14 19h2M19 19h2v2"/>'),
  penna: SVG('<path d="M4 20h4l10.5-10.5a2 2 0 0 0-4-4L4 16z"/><path d="M13 7l4 4"/>'),
  avviso: SVG('<path d="M12 3l10 18H2z"/><path d="M12 10v4M12 17.5h.01"/>'),
};

function caselleCanali(nome, disponibili) {
  return `<fieldset class="consent-channels" data-${nome}-channels>
    <legend>Canali</legend>
    ${ORDINE_CANALI.map((c) => {
      const ammesso = !disponibili || disponibili.includes(c);
      return `<label><input type="checkbox" name="channels" value="${c}" ${ammesso ? '' : 'disabled'}> ${escapeHtml(CANALI[c])}</label>`;
    }).join('')}
  </fieldset>`;
}

// Lo stato breve di un canale. Una scelta digitale piu' recente (email da
// confermare, numero da verificare) prevale sulla spunta del sito; un
// consenso o una revoca registrati prevalgono su tutto.
function statoCanale(canale, stato, raccolta, sito, ricon) {
  const inAttesa = Boolean(raccolta && raccolta.pending_confirmation) && !stato.allowed;
  const daVerificare = Boolean(raccolta && raccolta.declared_unverified) && !stato.allowed;
  const daSito = Boolean(sito) && !stato.allowed && stato.reason === 'deny_never_given' && !inAttesa && !daVerificare;
  const ultimo = stato.last_event || {};
  // C3.4: un consenso per canale che cita la richiesta del sito come prova.
  if (stato.allowed && ultimo.source === 'public_stima' && ultimo.evidence_type === 'stima') {
    return { etichetta: 'Concesso dal sito', tono: 'ok', attr: `data-site-recognized="${canale}"`,
      nota: stato.decided_at ? `dal ${formatDate(stato.decided_at)}` : '' };
  }
  if (inAttesa) {
    return { etichetta: 'Da confermare', tono: 'warn', attr: `data-consent-pending="${canale}"`,
      nota: `conferma entro il ${formatDate(raccolta.pending_confirmation.expires_at)}` };
  }
  if (daVerificare) {
    return { etichetta: 'Numero da verificare', tono: 'warn', attr: `data-consent-declared="${canale}"`,
      nota: "scelto dal cliente, non attivo finché il numero non è verificato" };
  }
  if (daSito) {
    // C3.4: la spunta c'e' ma le prove non bastano: si dice cosa manca.
    const manca = ricon && ricon.missing && ricon.missing.length ? ricon.missing.join('; ')
      : (ricon && ricon.status === 'not_covered' ? ricon.label : '');
    if (manca) {
      return { etichetta: "Consenso raccolto dal sito – prova incompleta", tono: 'warn',
        attr: `data-site-choice="${canale}" data-site-incomplete="${canale}"`, nota: `manca: ${manca}` };
    }
    return { etichetta: "Scelto sul sito · da confermare", tono: 'warn', attr: `data-site-choice="${canale}"`,
      nota: `sul sito il ${formatDate(sito.decided_at)}` };
  }
  let nota = '';
  if (stato.platform_optout_at) nota = `disiscritto da tutto STIMA360 il ${formatDate(stato.platform_optout_at)}`;
  else if ((stato.allowed || stato.reason === 'deny_revoked') && stato.decided_at) nota = `dal ${formatDate(stato.decided_at)}`;
  return { etichetta: ETICHETTE_CONSENSO[stato.reason] || stato.reason || '—',
    tono: stato.allowed ? 'ok' : (TONI_CONSENSO[stato.reason] || 'gray'), attr: '', nota };
}

function rigaCanale(canale, stato, raccolta, sito, ricon) {
  const s = statoCanale(canale, stato, raccolta, sito, ricon);
  const nota = s.nota ? `<small class="muted">${escapeHtml(s.nota)}</small>` : '';
  return `<li class="comm-row" data-consent-channel="${canale}">`
    + `<span class="comm-ico">${ICONE[canale]}</span>`
    + `<span class="comm-name">${escapeHtml(CANALI[canale])}${nota}</span>`
    + `<span class="comm-badge" ${s.attr}>${renderBadge(s.etichetta, s.tono)}</span></li>`;
}

// C3.2: cio' che il CRM ha ricevuto dal sito stima360.it. Due cose diverse
// dette in modo diverso: la Privacy (il modulo non parte senza la spunta, ma
// il sito non ne manda la prova: NON documentata) e la spunta marketing
// (registrata: data, richiesta, fonte; senza canale ne' testo: NON autorizza).
// In vista: due righe e «Mostra dettagli». Nei dettagli: tutto lo storico.
function renderOrigine(consenso) {
  const sito = consenso.site_record || {};
  const scelte = (sito.marketing_choices || []).filter((s) => s.decision === 'granted');
  const ultima = scelte.length ? scelte[scelte.length - 1] : null;
  const legacy = Boolean(consenso.legacy) && !scelte.length;
  if (!sito.from_site && !legacy) return '';
  const canaliCasella = (sito.checkbox_channels || []).map((c) => CANALI[c] || c).join(' + ');
  const ricon = (sito.recognition && sito.recognition.ticks) || [];
  const riconosciuta = ricon.some((t) => Object.values(t.channels || {}).some((c) => c.status === 'recognized'));
  const rigaSito = ultima
    ? `Sito stima360.it: ${riconosciuta ? 'consenso riconosciuto, spuntato' : 'scelta registrata'} il ${formatDate(ultima.decided_at)}`
    : legacy
      ? `Spunta marketing storica${consenso.legacy_granted_at ? ` del ${formatDate(consenso.legacy_granted_at)}` : ''}`
      : 'Sito stima360.it: nessuna scelta marketing ricevuta';
  const valutazioni = new Map(ricon.map((t) => [t.event_id, t]));
  const storico = scelte.map((s) => {
    const t = valutazioni.get(s.event_id);
    const prove = t ? ` <div class="muted" data-site-proof-check>Prove: spunta ✓ · data ${t.missing.includes('decided_at') ? '✗' : '✓'} · richiesta di valutazione ${t.missing.includes('stima') ? '✗' : '✓'} · informativa del sito ${t.notice ? `✓ (versione ${escapeHtml(t.notice.version)}${t.proof ? ` · prova: ${escapeHtml(t.proof.label)}${t.proof.evidence_label ? ` (${escapeHtml(t.proof.evidence_label)} ${escapeHtml(t.proof.window[0] === t.proof.window[1] ? t.proof.window[0] : `${t.proof.window[0]} - ${t.proof.window[1]}`)})` : ''}` : ''})` : '✗'}${t.missing_labels.length ? ` — manca: ${escapeHtml(t.missing_labels.join('; '))}` : ''}</div>
      <div class="muted">${Object.entries(t.channels).map(([c, v]) => `${escapeHtml(CANALI[c] || c)}: ${escapeHtml(v.label)}`).join(' · ')}</div>` : '';
    return `<li data-site-marketing-choice="${Number(s.event_id)}">
      <strong>Scelta registrata dal sito</strong> — casella marketing (${escapeHtml(canaliCasella)}) spuntata il ${escapeHtml(formatDateTime(s.decided_at))}${s.stima_id ? ` · richiesta di valutazione n. ${escapeHtml(String(s.stima_id))}` : ''} · registrata nel CRM il ${escapeHtml(formatDateTime(s.recorded_at))}${prove}
    </li>`;
  }).join('');
  const prove = scelte.length
    ? (riconosciuta
      ? `<p class="muted" data-site-proofs>Prove conservate: la spunta, la data registrata dal server del sito, la richiesta di valutazione e l'informativa del sito registrata nel CRM e valida a quella data. Per i canali coperti il consenso e' riconosciuto senza conferma.</p>`
      : `<p class="muted" data-site-proofs>Prove conservate: la spunta, la data registrata dal server del sito, la richiesta di valutazione.
        Non conservati dal sito: il testo e la versione dell'informativa mostrati; serve un'informativa del sito registrata nel CRM e valida alla data della spunta.
        Finche' manca, non e' un consenso autorizzato per gli invii: il cliente lo conferma dal link o dal QR.</p>`)
    : legacy
      ? `<p class="muted" data-site-legacy-flag><strong>Spunta marketing storica</strong> presente nel CRM${consenso.legacy_granted_at ? ` (dal ${escapeHtml(formatDateTime(consenso.legacy_granted_at))})` : ''}, senza riferimento alla richiesta ne' canale: prova incompleta, non autorizza invii. Manca: ${escapeHtml(((sito.recognition || {}).legacy_missing_labels || []).join('; '))}. Il cliente puo' confermarla dal link o dal QR.</p>`
      : '<p class="muted" data-site-no-marketing>Nessuna scelta marketing ricevuta dal sito per questo contatto.</p>';
  return `<div class="card comm-consent" data-site-record>
    <div class="comm-head"><strong>Origine del consenso</strong></div>
    <div class="comm-row comm-row-plain" data-site-summary><span class="comm-ico">${ICONE.sito}</span><span>${escapeHtml(rigaSito)}</span></div>
    <div class="comm-row comm-row-plain" data-site-privacy><span class="comm-ico">${ICONE.doc}</span><span>Privacy del sito: non documentata nel CRM</span></div>
    <details class="comm-details" data-site-details>
      <summary>Mostra dettagli</summary>
      <div class="comm-details-body">
        ${storico ? `<ul>${storico}</ul>` : ''}
        ${prove}
        <p class="muted">Privacy e Termini: il modulo del sito non si invia senza la spunta, ma il sito non trasmette al CRM la prova dell'accettazione.</p>
      </div>
    </details>
  </div>`;
}

// C3: la raccolta DIGITALE. L'operatore genera un link personale (30 giorni)
// con il suo QR; il cliente legge l'informativa e sceglie da solo i canali.
// Il link e il QR compaiono SOLO alla generazione: il server ne conserva
// l'impronta, non il link. Nessun invio parte da qui.
// In vista: tre comandi. Il riquadro con QR e link si apre su «Mostra QR».
function renderRaccolta(consenso) {
  const raccolta = consenso.capture;
  if (!raccolta || raccolta.available === false || !consenso.can_record) return '';
  const link = raccolta.link;
  const aperto = Boolean(link && link.collecting);
  // C3.2: senza informativa marketing corrente la pagina permette solo di
  // revocare. Un solo avviso, breve, prima di consegnare il QR.
  const senzaInformativa = raccolta.notice === null
    ? `<p class="error-box comm-avviso" data-capture-no-notice>${ICONE.avviso}<span>Manca un'informativa Marketing corrente. Pubblicala in Super Admin › Rete › Informative privacy.</span></p>`
    : '';
  const etichettaQr = aperto ? 'Mostra QR' : 'Genera link consenso';
  return `<div class="card comm-consent" data-consent-capture data-link-open="${aperto ? '1' : '0'}" data-link-id="${aperto ? Number(link.id) : ''}">
    <div class="comm-head"><strong>Raccolta digitale</strong></div>
    <p class="muted">Il cliente può confermare i canali dal link o dal QR.</p>
    ${senzaInformativa}
    <div class="comm-actions">
      <button type="button" class="btn primary" data-consent-link-generate>${ICONE.qr}<span data-consent-link-label>${etichettaQr}</span></button>
      <button type="button" class="btn" data-consent-manual-open>${ICONE.penna}<span>Registra consenso manuale</span></button>
    </div>
    <button type="button" class="comm-link" data-consent-refusal-open>Revoca o rifiuto</button>
    <div class="comm-qr" data-consent-link-output hidden>
      <img data-consent-link-qr alt="QR code del link consenso" width="200" height="200">
      <div class="comm-qr-info">
        <label>Link personale <input type="text" class="input" readonly data-consent-link-url></label>
        <p class="muted" data-consent-link-expiry></p>
        <p class="muted">Consegnalo al cliente di persona o in una conversazione avviata da lui.</p>
        <div class="comm-actions">
          <button type="button" class="btn" data-consent-link-copy>Copia link</button>
          <button type="button" class="btn" data-consent-link-new>Genera nuovo link</button>
          <button type="button" class="btn danger" data-consent-link-disable>Disattiva link</button>
          <button type="button" class="btn ghost" data-consent-link-close>Chiudi</button>
        </div>
      </div>
    </div>
    <p class="error-box" data-consent-link-error hidden></p>
  </div>`;
}

function renderConsenso(consenso, informative) {
  if (!consenso || consenso.available === false) {
    return `<div class="card" data-consent-panel>
      <p class="muted">Consenso marketing non disponibile su questo ambiente.</p>
    </div>`;
  }
  const canali = consenso.channels || {};
  const raccolta = (consenso.capture && consenso.capture.channels) || {};
  const sitoRec = consenso.site_record || {};
  const scelteSito = (sitoRec.marketing_choices || []).filter((s) => s.decision === 'granted');
  const ultimaSito = scelteSito.length ? scelteSito[scelteSito.length - 1] : null;
  const canaliSito = new Set(sitoRec.checkbox_channels || []);
  // C3.4: la valutazione delle prove dell'ultima spunta, per canale.
  const ultimaValutazione = ultimaSito
    ? ((sitoRec.recognition && sitoRec.recognition.ticks) || []).find((t) => t.event_id === ultimaSito.event_id) : null;
  const riconDi = (c) => (ultimaValutazione && ultimaValutazione.channels[c]
    ? { ...ultimaValutazione.channels[c], missing: ultimaValutazione.missing_labels } : null);
  const righe = ORDINE_CANALI.filter((c) => canali[c])
    .map((c) => rigaCanale(c, canali[c], raccolta[c], canaliSito.has(c) ? ultimaSito : null, riconDi(c))).join('');
  const coperti = (informative || []).flatMap((n) => n.channels || []);
  const opzioniInformative = (informative || []).map((n) =>
    `<option value="${Number(n.id)}">${escapeHtml(n.version)} — ${escapeHtml(n.context)}${(n.channels || []).length ? ` (${escapeHtml((n.channels || []).map((c) => CANALI[c] || c).join(', '))})` : ''}</option>`).join('');
  const sceltaInformativa = (informative || []).length
    ? `<label>Informativa presentata <select name="notice_id" required>${opzioniInformative}</select></label>`
    : '<p class="muted">Nessuna informativa Marketing corrente: il consenso manuale non si può registrare.</p>';
  const form = consenso.can_record ? `<details class="card comm-consent" data-consent-grant>
    <summary>Registra consenso marketing di persona</summary>
    <form data-consent-grant-form>
      <p class="muted">Solo un consenso raccolto davvero, con una prova che si può ritrovare.</p>
      ${caselleCanali('grant', coperti)}
      <label>Data e ora della raccolta <input type="datetime-local" name="decided_at" required></label>
      <label>Modalità
        <select name="evidence_type">
          <option value="paper_form">Modulo cartaceo</option>
          <option value="digital_form">Modulo digitale</option>
          <option value="other_documented">Altra modalità documentata</option>
        </select>
      </label>
      <label>Riferimento alla prova (protocollo, archivio, documento)
        <input type="text" name="evidence_ref" minlength="6" maxlength="100" required></label>
      ${sceltaInformativa}
      <label>Note <textarea name="note" rows="2" maxlength="2000"></textarea></label>
      <div class="actions"><button type="submit" class="btn primary" ${(informative || []).length ? '' : 'disabled'}>Registra consenso</button></div>
      <p class="error-box" data-consent-error hidden></p>
    </form>
  </details>
  <details class="card comm-consent" data-consent-refusal>
    <summary>Rifiuto o revoca espressi al personale</summary>
    <form data-consent-refusal-form>
      <p class="muted">Blocca subito gli invii di marketing sui canali che spunti.</p>
      ${caselleCanali('refusal', null)}
      <label>Data e ora <input type="datetime-local" name="decided_at" required></label>
      <label>Ambito
        <select name="scope">
          <option value="agency">Questa agenzia</option>
          <option value="platform">Tutto STIMA360 (ogni agenzia)</option>
        </select>
      </label>
      <label>Come è stato espresso <textarea name="note" rows="2" minlength="10" maxlength="2000" required></textarea></label>
      <div class="actions"><button type="submit" class="btn danger">Registra revoca</button></div>
      <p class="error-box" data-refusal-error hidden></p>
    </form>
  </details>` : '';
  return `<div class="card comm-consent" data-consent-panel>
    <div class="comm-head"><strong>Consenso marketing</strong></div>
    <ul class="comm-rows">${righe}</ul>
  </div>
  ${renderOrigine(consenso)}
  ${renderRaccolta(consenso)}
  ${form}`;
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

// I link personali generati in questa pagina, per contatto (solo in memoria).
const LINK_GENERATI = new Map();

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
  //
  // «Mostra QR»: il server conserva SOLO l'impronta del link, quindi il QR si
  // puo' rivedere solo se e' stato generato in questa pagina della Shell
  // (memoria del modulo, per contatto: niente storage del browser, come per
  // ogni altro segreto). Altrimenti si rigenera, con conferma: il link
  // precedente smette di raccogliere consensi, le revoche restano.
  function linkInSessione() {
    return LINK_GENERATI.get(contactId) || null;
  }
  function ricordaLink(dati) {
    LINK_GENERATI.set(contactId, dati);
  }
  function mostraLink(box, dati) {
    const scadenza = `Raccoglie consensi fino al ${formatDateTime(dati.expires_at)}.`;
    box.querySelector('[data-consent-link-url]').value = String(dati.url || '');
    const qr = String(dati.qr || '');
    if (qr.startsWith('data:image/svg+xml')) box.querySelector('[data-consent-link-qr]').src = qr;
    box.querySelector('[data-consent-link-expiry]').textContent = scadenza;
    box.querySelector('[data-consent-link-label]').textContent = 'Mostra QR';
    box.dataset.linkOpen = '1';
    box.dataset.linkId = String(dati.id || '');
    box.querySelector('[data-consent-link-output]').hidden = false;
  }
  mount.addEventListener('click', async (evento) => {
    const genera = evento.target.closest('button[data-consent-link-generate]');
    const nuovo = evento.target.closest('button[data-consent-link-new]');
    const disattiva = evento.target.closest('button[data-consent-link-disable]');
    const copia = evento.target.closest('button[data-consent-link-copy]');
    const chiudi = evento.target.closest('button[data-consent-link-close]');
    const manuale = evento.target.closest('button[data-consent-manual-open]');
    const rifiuto = evento.target.closest('button[data-consent-refusal-open]');
    if (!genera && !nuovo && !disattiva && !copia && !chiudi && !manuale && !rifiuto) return;
    if (manuale || rifiuto) {
      const modulo = mount.querySelector(manuale ? '[data-consent-grant]' : '[data-consent-refusal]');
      if (!modulo) return;
      modulo.open = true;
      modulo.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const primo = modulo.querySelector('input[type="checkbox"]');
      if (primo) primo.focus();
      return;
    }
    const box = mount.querySelector('[data-consent-capture]');
    const errore = box.querySelector('[data-consent-link-error]');
    errore.hidden = true;
    if (chiudi) {
      box.querySelector('[data-consent-link-output]').hidden = true;
      return;
    }
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
    if (genera && box.dataset.linkOpen === '1') {
      const ricordato = linkInSessione();
      if (ricordato && String(ricordato.id) === box.dataset.linkId) {
        mostraLink(box, ricordato);
        return;
      }
      if (!window.confirm("Il QR di questo link non è più visualizzabile: il CRM ne conserva solo l'impronta. Generare un nuovo link? Il precedente non raccoglierà più consensi (le revoche restano possibili).")) return;
    }
    if (nuovo && !window.confirm("Il link precedente non raccoglierà più consensi (le revoche restano possibili). Generare un nuovo link?")) return;
    if (disattiva && !window.confirm("Il link non raccoglierà più consensi; le revoche restano possibili. Disattivarlo?")) return;
    const bottone = genera || nuovo || disattiva;
    bottone.disabled = true;
    try {
      if (disattiva) {
        await apiPost(`/api/communication/contacts/${contactId}/consent/link/disable`, {});
        await ridisegna();
        return;
      }
      const esito = await apiPost(`/api/communication/contacts/${contactId}/consent/link`, {});
      const dati = { id: esito.link_id, url: esito.url, qr: esito.qr_svg_data_uri, expires_at: esito.expires_at };
      ricordaLink(dati);
      mostraLink(box, dati);
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
