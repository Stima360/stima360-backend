// STIMA360 OS — views/agenzia-dati.js
// BLOCCO-1B: «Dati agenzia e logo» (`#/agenzia`), aperta dal menu utente.
//
// Legge e salva i dati dell'agenzia della SESSIONE (GET/PUT /api/agency-profile)
// e il suo logo (GET/PUT/DELETE /api/agency-profile/logo). Nessun agency_id
// esce da qui: l'agenzia la decide il server. Il server decide anche chi puo'
// modificare (`can_edit`); la pagina si limita a non offrire i comandi a chi
// riceverebbe comunque 403.
//
// Due anteprime distinte: quella dei dati SALVATI e, solo quando il modulo e'
// stato cambiato, quella NON SALVATA. Tutto il testo passa da `textContent`.

import { apiGet, apiPut, apiDelete } from '../core/api-client.js';
import { sessionExpired } from '../core/auth.js';

const API = '/api/agency-profile';
const LOGO_MAX_BYTES = 2 * 1024 * 1024;
const LOGO_TYPES = ['image/png', 'image/jpeg'];

// Etichette e limiti: gli stessi del server (agency_profile/enums.py).
export const FIELDS = [
  { name: 'display_name', label: 'Nome da mostrare nei report', max: 120, required: true },
  { name: 'legal_name', label: 'Ragione sociale', max: 200 },
  { name: 'vat_number', label: 'Partita IVA', max: 32 },
  { name: 'tax_code', label: 'Codice fiscale', max: 32 },
  { name: 'legal_address', label: 'Indirizzo della sede legale', max: 300 },
  { name: 'phone', label: 'Telefono', max: 40, type: 'tel' },
  { name: 'email', label: 'Email', max: 254, type: 'email' },
  { name: 'website', label: 'Sito web', max: 300, type: 'url', placeholder: 'https://' },
];

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

/** I valori del modulo come li salverebbe il server: spazi tolti, vuoti assenti. */
export function formValues(inputs) {
  const values = {};
  for (const f of FIELDS) {
    const v = String(inputs[f.name].value || '').trim();
    values[f.name] = v || null;
  }
  return values;
}

export function sameProfile(a, b) {
  return FIELDS.every((f) => (a[f.name] || null) === (b[f.name] || null));
}

/** Le righe dell'intestazione, nell'ordine in cui la mostrera' un report. */
export function headerLines(profile) {
  const righe = [];
  if (profile.legal_name) righe.push(profile.legal_name);
  if (profile.legal_address) righe.push(`Sede legale: ${profile.legal_address}`);
  const fiscali = [];
  if (profile.vat_number) fiscali.push(`P.IVA ${profile.vat_number}`);
  if (profile.tax_code) fiscali.push(`C.F. ${profile.tax_code}`);
  if (fiscali.length) righe.push(fiscali.join(' · '));
  const recapiti = [profile.phone, profile.email, profile.website].filter(Boolean);
  if (recapiti.length) righe.push(recapiti.join(' · '));
  return righe;
}

/** Il messaggio di un errore del server, leggibile. */
export function errorText(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return 'Alcuni campi non sono validi: controlla i messaggi sotto ai campi.';
  return (error && error.message) || 'Operazione non riuscita.';
}

/** {campo: messaggio} da un 422 di validazione. */
export function fieldErrors(error) {
  const d = error && error.data && error.data.detail;
  const out = {};
  if (!Array.isArray(d)) return out;
  for (const item of d) {
    const campo = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
    if (campo && FIELDS.some((f) => f.name === campo)) {
      out[campo] = String(item.msg || 'Valore non valido').replace(/^Value error, /, '');
    }
  }
  return out;
}

function renderHeader(target, { name, lines, logoUrl, logoAlt }) {
  target.replaceChildren();
  const box = el('div', 'agency-header');
  if (logoUrl) {
    const img = el('img', 'agency-header-logo');
    img.src = logoUrl;
    img.alt = logoAlt || '';
    box.appendChild(img);
  }
  const testi = el('div', 'agency-header-text');
  testi.appendChild(el('strong', 'agency-header-name', name));
  for (const r of lines) testi.appendChild(el('span', 'agency-header-line', r));
  box.appendChild(testi);
  target.appendChild(box);
}

async function uploadLogo(file) {
  let response;
  try {
    response = await fetch(`${API}/logo`, {
      method: 'PUT', body: file, credentials: 'include',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
    });
  } catch (_rete) {
    const e = new Error('Impossibile contattare il server. Verifica la connessione.');
    e.network = true;
    throw e;
  }
  if (response.status === 401) {
    sessionExpired();
    throw new Error('Sessione scaduta. Effettua di nuovo il login.');
  }
  let data = null;
  try { data = await response.json(); } catch (_vuoto) { data = null; }
  if (!response.ok) {
    const e = new Error('Caricamento non riuscito.');
    e.status = response.status;
    e.data = data;
    throw e;
  }
  return data;
}

export async function renderAgenziaDati(container) {
  container.replaceChildren(el('p', 'muted', 'Caricamento…'));
  let stato;
  try {
    stato = await apiGet(API);
  } catch (error) {
    container.replaceChildren(el('div', 'error-box', errorText(error)));
    return;
  }

  const pagina = el('div', 'agency-profile-page');
  pagina.setAttribute('data-agency-profile', '');
  const avviso = el('div');
  avviso.setAttribute('role', 'status');
  avviso.setAttribute('data-agency-notice', '');

  // --- intestazione salvata e non salvata --------------------------------
  const anteprime = el('section', 'card agency-previews');
  anteprime.appendChild(el('h2', 'section-title', 'Anteprima dell’intestazione'));
  const salvataTitolo = el('p', 'agency-preview-label', 'Configurazione salvata');
  const salvata = el('div', 'agency-preview agency-preview-saved');
  salvata.setAttribute('data-preview-saved', '');
  const bozzaBlocco = el('div', 'agency-preview-draft-block');
  bozzaBlocco.hidden = true;
  const bozzaTitolo = el('p', 'agency-preview-label');
  bozzaTitolo.append(el('span', 'badge badge-warn', 'Non salvata'), document.createTextNode(' Anteprima con le modifiche in corso'));
  const bozza = el('div', 'agency-preview agency-preview-draft');
  bozza.setAttribute('data-preview-draft', '');
  bozzaBlocco.append(bozzaTitolo, bozza);
  const notaSalvata = el('p', 'muted agency-preview-note');
  anteprime.append(salvataTitolo, salvata, notaSalvata, bozzaBlocco);

  // --- dati -----------------------------------------------------------
  const dati = el('section', 'card');
  dati.appendChild(el('h2', 'section-title', 'Dati agenzia'));
  dati.appendChild(el('p', 'muted', 'Solo il nome da mostrare nei report è richiesto ora; gli altri dati sono facoltativi '
    + 'e restano vuoti finché non vengono inseriti. Questa scelta non verifica gli obblighi dei futuri documenti.'));
  const form = el('form', 'agency-profile-form');
  form.noValidate = true;
  const inputs = {};
  const errori = {};
  for (const f of FIELDS) {
    const campo = el('div', 'form-field');
    const label = el('label', null, f.required ? `${f.label} *` : f.label);
    label.htmlFor = `ap-${f.name}`;
    const input = el('input', 'input');
    input.id = `ap-${f.name}`;
    input.name = f.name;
    input.type = f.type || 'text';
    input.maxLength = f.max;
    if (f.placeholder) input.placeholder = f.placeholder;
    if (f.required) input.required = true;
    const err = el('p', 'field-error');
    err.hidden = true;
    err.setAttribute('data-field-error', f.name);
    campo.append(label, input, err);
    form.appendChild(campo);
    inputs[f.name] = input;
    errori[f.name] = err;
  }
  const azioni = el('div', 'agency-actions');
  const salva = button('Salva dati', 'primary', 'data-agency-save');
  salva.type = 'submit';
  const annulla = button('Annulla modifiche', 'ghost', 'data-agency-reset');
  azioni.append(salva, annulla);
  form.appendChild(azioni);
  dati.appendChild(form);

  // --- logo -----------------------------------------------------------
  const logo = el('section', 'card');
  logo.appendChild(el('h2', 'section-title', 'Logo'));
  logo.appendChild(el('p', 'muted', 'PNG o JPEG, al massimo 2 MB e 4096 pixel per lato. Il logo si salva subito al caricamento, in una copia normalizzata: orientamento corretto, senza metadati.'));
  const logoAttuale = el('div', 'agency-logo-current');
  logoAttuale.setAttribute('data-logo-current', '');
  const file = el('input');
  file.type = 'file';
  file.accept = 'image/png,image/jpeg';
  file.id = 'ap-logo-file';
  file.setAttribute('data-logo-file', '');
  const fileLabel = el('label', 'sr-only', 'File del logo');
  fileLabel.htmlFor = file.id;
  const logoNonAttivo = el('p', 'muted', 'Il logo non è ancora attivo su questo ambiente: i dati agenzia si possono comunque salvare.');
  logoNonAttivo.setAttribute('data-logo-unavailable', '');
  logoNonAttivo.hidden = true;
  const logoAzioni = el('div', 'agency-actions');
  const carica = button('Carica logo', 'primary', 'data-logo-upload');
  const rimuovi = button('Rimuovi logo', 'danger', 'data-logo-remove');
  logoAzioni.append(carica, rimuovi);
  logo.append(logoNonAttivo, logoAttuale, fileLabel, file, logoAzioni);

  pagina.append(avviso, anteprime, dati, logo);
  container.replaceChildren(pagina);

  function notifica(testo, tipo) {
    avviso.replaceChildren();
    if (!testo) return;
    avviso.appendChild(el('div', tipo === 'ok' ? 'success-box' : 'error-box', testo));
  }

  function logoUrl() {
    return stato.logo ? stato.logo.url : null;
  }

  function nomeIntestazione(profilo) {
    return profilo.display_name || stato.agency_name;
  }

  function disegnaSalvata() {
    const p = stato.profile || {};
    renderHeader(salvata, { name: nomeIntestazione(p), lines: headerLines(p), logoUrl: logoUrl(), logoAlt: `Logo di ${nomeIntestazione(p)}` });
    notaSalvata.textContent = stato.configured
      ? `Dati salvati${stato.updated_at ? ` il ${new Date(stato.updated_at).toLocaleString('it-IT', { timeZone: 'Europe/Rome' })}` : ''}.`
      : 'Dati non ancora configurati: l’intestazione mostra il nome dell’agenzia in anagrafica.';
    if (!stato.logo) notaSalvata.textContent += ' Nessun logo: l’intestazione mostra solo il nome.';
  }

  function disegnaBozza() {
    const valori = formValues(inputs);
    const cambiato = !sameProfile(valori, stato.profile || {});
    bozzaBlocco.hidden = !cambiato;
    if (cambiato) {
      renderHeader(bozza, { name: nomeIntestazione(valori), lines: headerLines(valori), logoUrl: logoUrl(), logoAlt: 'Logo' });
    }
    return cambiato;
  }

  function disegnaLogo() {
    logoAttuale.replaceChildren();
    if (stato.logo) {
      const img = el('img', 'agency-logo-preview');
      img.src = stato.logo.url;
      img.alt = 'Logo attuale';
      img.setAttribute('data-logo-img', '');
      logoAttuale.append(img, el('p', 'muted', `${stato.logo.width} × ${stato.logo.height} px · ${Math.ceil(stato.logo.size_bytes / 1024)} KB`));
    } else {
      logoAttuale.appendChild(el('p', 'muted', 'Nessun logo caricato.'));
    }
    carica.textContent = stato.logo ? 'Sostituisci logo' : 'Carica logo';
    rimuovi.hidden = !stato.logo || !stato.can_edit;
    // Migrazione 115 non applicata su questo ambiente: niente comandi del logo.
    const attivo = stato.logo_available !== false;
    logoNonAttivo.hidden = attivo;
    if (!attivo) {
      file.hidden = true;
      logoAzioni.hidden = true;
      logoAttuale.replaceChildren();
    }
  }

  function riempi() {
    const p = stato.profile || {};
    for (const f of FIELDS) inputs[f.name].value = p[f.name] || '';
  }

  function mostraErrori(map) {
    for (const f of FIELDS) {
      errori[f.name].textContent = map[f.name] || '';
      errori[f.name].hidden = !map[f.name];
      inputs[f.name].setAttribute('aria-invalid', map[f.name] ? 'true' : 'false');
    }
  }

  function tutto() {
    disegnaSalvata();
    disegnaBozza();
    disegnaLogo();
  }

  if (!stato.can_edit) {
    for (const f of FIELDS) inputs[f.name].disabled = true;
    azioni.hidden = true;
    file.hidden = true;
    logoAzioni.hidden = true;
    dati.appendChild(el('p', 'muted', 'Solo il titolare o un amministratore dell’agenzia può modificare questi dati.'));
  }

  riempi();
  tutto();

  form.addEventListener('input', () => disegnaBozza());
  annulla.addEventListener('click', () => {
    riempi();
    mostraErrori({});
    disegnaBozza();
    notifica('');
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const valori = formValues(inputs);
    if (!valori.display_name) {
      mostraErrori({ display_name: 'Il nome da mostrare nei report è obbligatorio.' });
      notifica('Dati non salvati: indicare il nome da mostrare nei report.');
      return;
    }
    salva.disabled = true;
    try {
      stato = await apiPut(API, valori);
      mostraErrori({});
      riempi();
      tutto();
      notifica('Dati agenzia salvati.', 'ok');
    } catch (error) {
      mostraErrori(fieldErrors(error));
      notifica(`Dati non salvati. ${errorText(error)}`);
    } finally {
      salva.disabled = false;
    }
  });

  carica.addEventListener('click', async () => {
    const scelto = file.files && file.files[0];
    if (!scelto) {
      notifica('Scegli prima un file PNG o JPEG.');
      return;
    }
    // Controlli di cortesia: quello che conta e' la verifica del server.
    if (scelto.size > LOGO_MAX_BYTES) {
      notifica('Il logo supera 2 MB.');
      return;
    }
    if (scelto.type && !LOGO_TYPES.includes(scelto.type)) {
      notifica('Formato non ammesso: caricare un file PNG o JPEG.');
      return;
    }
    carica.disabled = true;
    try {
      stato = { ...stato, logo: await uploadLogo(scelto) };
      file.value = '';
      tutto();
      notifica('Logo salvato.', 'ok');
    } catch (error) {
      notifica(`Logo non salvato${stato.logo ? ': resta quello precedente' : ''}. ${errorText(error)}`);
    } finally {
      carica.disabled = false;
    }
  });

  rimuovi.addEventListener('click', async () => {
    if (!window.confirm('Rimuovere il logo dell’agenzia?')) return;
    rimuovi.disabled = true;
    try {
      await apiDelete(`${API}/logo`);
      stato = { ...stato, logo: null };
      tutto();
      notifica('Logo rimosso.', 'ok');
    } catch (error) {
      notifica(`Logo non rimosso. ${errorText(error)}`);
    } finally {
      rimuovi.disabled = false;
    }
  });
}
