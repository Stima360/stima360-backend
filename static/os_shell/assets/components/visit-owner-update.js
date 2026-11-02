// STIMA360 OS — components/visit-owner-update.js (ROADMAP VISITE, fase D)
//
// «Aggiornamento al proprietario» sulla scheda di una visita effettuata: bozza,
// anteprima, APPROVAZIONE dell'agente, pubblicazione nel portale. E' un componente
// condiviso, come il foglio visita: lo monta la scheda dell'appuntamento in Agenda, ma i
// percorsi API del report proprietario (`/api/owner-reports/visits`) non stanno nei file
// dell'Agenda. Tutto il testo dei dati passa da `textContent`.
//
// Il server impone le regole (privacy del testo, ruolo, visita effettuata, approvazione
// esplicita); qui la UI le rende visibili e non offre cio' che il server rifiuterebbe.

import { apiGet, apiPost, apiPut } from '../core/api-client.js';

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

// ---------------------------------------------------------------------------
// ROADMAP VISITE (fase D) - «Aggiornamento al proprietario»
// ---------------------------------------------------------------------------
// Il testo che il proprietario leggera' nel portale, per una visita EFFETTUATA:
// 1. si propone un testo dall'interesse registrato (mai dalle note interne);
// 2. l'agente lo modifica e lo salva come BOZZA (il proprietario non la vede);
// 3. si vede l'ANTEPRIMA, cosi' come la leggera' il proprietario;
// 4. l'agente APPROVA con un gesto esplicito (casella di conferma + pulsante).
// Le regole le impone il server (`/api/owner-reports/visits/{id}`): la privacy del
// testo (nominativi, recapiti, importi, citazioni), il ruolo, la visita effettuata.

const OWNER_API = '/api/owner-reports/visits';

function erroreOwner(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Operazione non riuscita.';
}

function giorno(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: 'long', year: 'numeric' });
}

function selezione(opzioni, valore, vuoto) {
  const select = el('select', 'input');
  if (vuoto) {
    const o = el('option', '', vuoto);
    o.value = '';
    select.appendChild(o);
  }
  for (const x of opzioni) {
    const o = el('option', '', x.label);
    o.value = x.code;
    if (x.code === valore) o.selected = true;
    select.appendChild(o);
  }
  return select;
}

function anteprima(current) {
  const box = el('div', 'owner-update-preview');
  box.dataset.ownerUpdatePreview = '';
  box.appendChild(el('strong', '', 'Anteprima: così la leggerà il proprietario'));
  box.appendChild(el('div', 'muted', [current.category_label, current.sentiment_label].filter(Boolean).join(' · ')));
  box.appendChild(el('p', 'owner-update-text', current.public_summary));
  return box;
}

/** «Aggiornamento al proprietario»: solo per una visita effettuata. */
export function mountOwnerUpdate(container, { detail }) {
  const riga = detail && detail.appointment;
  if (!riga || riga.appointment_type !== 'buyer_visit' || !detail.visit || riga.status !== 'completed') return null;
  const idAppuntamento = detail.appointment.id;
  const sezione = el('section', 'agenda-owner-update');
  sezione.dataset.ownerUpdate = '';
  container.appendChild(sezione);

  async function carica(messaggio) {
    sezione.replaceChildren(el('h3', 'section-title', 'Aggiornamento al proprietario'));
    let stato;
    try {
      stato = await apiGet(`${OWNER_API}/${encodeURIComponent(String(idAppuntamento))}`);
    } catch (error) {
      if (error && error.status === 404) { sezione.remove(); return; }
      sezione.appendChild(el('div', 'error-box', erroreOwner(error)));
      return;
    }
    sezione.appendChild(el('p', 'muted', stato.notice));
    if (messaggio) sezione.appendChild(el('div', 'success-box', messaggio));
    if (!stato.can_prepare) {
      sezione.appendChild(el('p', 'muted', stato.reason));
      return;
    }
    const corrente = stato.current;
    if (corrente && corrente.status === 'published') {
      sezione.appendChild(el('p', 'owner-update-state', `Pubblicato al proprietario il ${giorno(corrente.published_at)} (versione ${corrente.version_number}).`));
      sezione.appendChild(anteprima(corrente));
      const barra = el('div', 'action-bar');
      const rettifica = el('button', 'btn', 'Rettifica (nuova versione)');
      rettifica.type = 'button';
      rettifica.dataset.ownerUpdateRevise = '';
      const ritira = el('button', 'btn danger', 'Ritira dal portale');
      ritira.type = 'button';
      ritira.dataset.ownerUpdateWithdraw = '';
      barra.append(rettifica, ritira);
      sezione.appendChild(barra);
      const area = el('div');
      sezione.appendChild(area);
      rettifica.addEventListener('click', () => modulo(stato, corrente, area));
      ritira.addEventListener('click', () => confermaRitiro(stato, corrente, area));
      return;
    }
    if (corrente && corrente.status === 'draft') {
      sezione.appendChild(el('p', 'owner-update-state', 'Bozza: il proprietario non la vede finché non la approvi.'));
      sezione.appendChild(anteprima(corrente));
      approvazione(stato, corrente);
      const barra = el('div', 'action-bar');
      const modifica = el('button', 'btn', 'Modifica bozza');
      modifica.type = 'button';
      modifica.dataset.ownerUpdateEdit = '';
      barra.appendChild(modifica);
      sezione.appendChild(barra);
      const area = el('div');
      sezione.appendChild(area);
      modifica.addEventListener('click', () => modulo(stato, corrente, area));
      return;
    }
    modulo(stato, null, sezione);
  }

  /** Il modulo del testo: proposta (o testo corrente), categoria, esito, controllo privacy, salva bozza. */
  function modulo(stato, base, dove) {
    dove.querySelectorAll('[data-owner-update-form]').forEach((n) => n.remove());
    const partenza = base || stato.suggestion || { category: 'general', sentiment: null, public_summary: '' };
    const form = el('form', 'agenda-form owner-update-form');
    form.noValidate = true;
    form.dataset.ownerUpdateForm = '';
    if (!base) {
      form.appendChild(el('p', 'muted', 'Testo proposto dall’interesse registrato: modificalo come preferisci. Le note interne non vengono copiate.'));
    }
    const campoCategoria = el('div', 'form-field');
    campoCategoria.appendChild(el('label', '', 'Argomento'));
    const categoria = selezione(stato.categories, partenza.category);
    campoCategoria.appendChild(categoria);
    const campoEsito = el('div', 'form-field');
    campoEsito.appendChild(el('label', '', 'Esito per il proprietario'));
    const esito = selezione(stato.sentiments, partenza.sentiment, '— non indicato —');
    campoEsito.appendChild(esito);
    const campoTesto = el('div', 'form-field');
    campoTesto.appendChild(el('label', '', 'Testo per il proprietario (feedback, osservazioni, prossimi passi)'));
    const testo = el('textarea', 'input');
    testo.dataset.field = 'owner-update-text';
    testo.rows = 5;
    testo.maxLength = 5000;
    testo.value = partenza.public_summary || '';
    campoTesto.appendChild(testo);
    campoTesto.appendChild(el('small', 'muted', 'Scrivi senza nomi, recapiti, importi o citazioni dell’acquirente: il controllo li blocca.'));
    const errore = el('div', 'field-error');
    errore.setAttribute('role', 'alert');
    errore.dataset.error = '';
    const barra = el('div', 'action-bar');
    const controlla = el('button', 'btn', 'Controlla il testo');
    controlla.type = 'button';
    const salva = el('button', 'btn primary', 'Salva bozza e vedi l’anteprima');
    salva.type = 'submit';
    salva.dataset.submit = '';
    barra.append(controlla, salva);
    form.append(campoCategoria, campoEsito, campoTesto, errore, barra);

    function mostraProblemi(error) {
      const d = error && error.data && error.data.detail;
      const issues = d && Array.isArray(d.issues) ? d.issues : null;
      errore.replaceChildren();
      if (issues && issues.length) {
        errore.appendChild(el('div', '', 'Il testo non può essere pubblicato:'));
        const ul = el('ul');
        for (const i of issues) ul.appendChild(el('li', '', i.message));
        errore.appendChild(ul);
      } else {
        errore.textContent = erroreOwner(error);
      }
    }

    controlla.addEventListener('click', async () => {
      errore.replaceChildren();
      try {
        const r = await apiPost(`${OWNER_API}/${encodeURIComponent(String(idAppuntamento))}/check`, { public_summary: testo.value });
        if (r.valid) errore.appendChild(el('div', 'success-box', 'Il testo rispetta le regole di privacy.'));
        else mostraProblemi({ data: { detail: { issues: r.issues } } });
      } catch (error) { mostraProblemi(error); }
    });
    let occupato = false;
    form.addEventListener('submit', async (evento) => {
      evento.preventDefault();
      if (occupato) return;
      occupato = true;
      salva.disabled = true;
      errore.replaceChildren();
      try {
        await apiPut(`${OWNER_API}/${encodeURIComponent(String(idAppuntamento))}`, {
          category: categoria.value, sentiment: esito.value || null, public_summary: testo.value });
        await carica('Bozza salvata: controlla l’anteprima e approvala per pubblicarla.');
      } catch (error) {
        mostraProblemi(error);
        salva.disabled = false;
      } finally {
        occupato = false;
      }
    });
    dove.appendChild(form);
  }

  /** L'approvazione: un gesto esplicito (casella + pulsante), mai un clic solo. */
  function approvazione(stato, corrente) {
    const box = el('div', 'owner-update-approval');
    box.dataset.ownerUpdateApproval = '';
    const etichetta = el('label', 'owner-update-confirm');
    const casella = el('input');
    casella.type = 'checkbox';
    casella.dataset.ownerUpdateConfirm = '';
    etichetta.append(casella, el('span', '', ' Ho controllato il testo: non contiene note interne né dati dell’acquirente.'));
    box.appendChild(etichetta);
    box.appendChild(el('small', 'muted', stato.owners_with_access
      ? `Sarà visibile ${stato.owners_with_access === 1 ? 'al proprietario autorizzato' : `ai ${stato.owners_with_access} proprietari autorizzati`} nel portale. Nessun messaggio viene inviato.`
      : 'Nessun proprietario ha ancora accesso al portale per questo immobile: il testo resta salvato e comparirà quando avrà accesso. Nessun messaggio viene inviato.'));
    const errore = el('div', 'field-error');
    errore.setAttribute('role', 'alert');
    const approva = el('button', 'btn primary', 'Approva e pubblica al proprietario');
    approva.type = 'button';
    approva.disabled = true;
    approva.dataset.ownerUpdateApprove = '';
    casella.addEventListener('change', () => { approva.disabled = !casella.checked; });
    approva.addEventListener('click', async () => {
      if (!casella.checked) return;
      approva.disabled = true;
      try {
        await apiPost(`${OWNER_API}/${encodeURIComponent(String(idAppuntamento))}/approve`, { publication_id: corrente.id, confirm: true });
        await carica('Approvato e pubblicato: il proprietario lo legge nel portale.');
      } catch (error) {
        errore.textContent = erroreOwner(error);
        approva.disabled = !casella.checked;
      }
    });
    box.append(approva, errore);
    sezione.appendChild(box);
  }

  function confermaRitiro(stato, corrente, dove) {
    dove.replaceChildren();
    const box = el('div', 'owner-update-withdraw');
    box.appendChild(el('p', '', 'Il testo sparirà dal portale del proprietario e resterà nello storico. Confermi?'));
    const errore = el('div', 'field-error');
    errore.setAttribute('role', 'alert');
    const si = el('button', 'btn danger', 'Sì, ritira dal portale');
    si.type = 'button';
    si.dataset.ownerUpdateWithdrawConfirm = '';
    const no = el('button', 'btn ghost', 'Annulla');
    no.type = 'button';
    no.addEventListener('click', () => dove.replaceChildren());
    si.addEventListener('click', async () => {
      si.disabled = true;
      try {
        await apiPost(`${OWNER_API}/${encodeURIComponent(String(idAppuntamento))}/withdraw`, { publication_id: corrente.id });
        await carica('Ritirato dal portale.');
      } catch (error) {
        errore.textContent = erroreOwner(error);
        si.disabled = false;
      }
    });
    box.append(si, no, errore);
    dove.appendChild(box);
  }

  carica().catch(() => {});
  return sezione;
}
