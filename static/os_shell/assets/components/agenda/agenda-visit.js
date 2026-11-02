// STIMA360 OS — components/agenda/agenda-visit.js (ROADMAP VISITE)
//
// Le sezioni della scheda di una VISITA all'immobile in Agenda, sotto i
// pulsanti di azione:
//
//   * «Esito della visita»      interesse dell'acquirente e feedback (fase B)
//
// Riusano i dati che il server include nel dettaglio dell'appuntamento
// (`detail.visit`) e le API esistenti: nessun dato inserito due volte, nessuna
// rotta nuova oltre `PUT /api/appointments/{id}/visit-outcome`. Tutto il testo
// dei dati passa da `textContent`: mai `innerHTML` con dati.
//
// Regole che la UI rispetta e il SERVER impone:
//   * una visita non si chiude da sola: l'esito si registra dopo che l'operatore
//     l'ha segnata «effettuata», «cliente assente» o «non effettuata»;
//   * l'interesse solo per una visita effettuata;
//   * il feedback e' una nota INTERNA: non e' mai pubblicato al proprietario
//     senza l'approvazione dell'agente.

import { INTEREST_LEVELS, errorMessage } from '../../agenda/agenda-model.js';
import { putVisitOutcome } from '../../agenda/agenda-api.js';

export function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function riga(etichetta, valore) {
  const r = el('div', 'detail-item');
  r.appendChild(el('label', '', etichetta));
  r.appendChild(el('div', '', valore));
  return r;
}

/** La visita e' una visita acquirente collegata a un immobile con una riga `visit`. */
export function isVisit(detail) {
  return Boolean(detail && detail.appointment
    && detail.appointment.appointment_type === 'buyer_visit' && detail.visit);
}

/**
 * «Esito della visita»: il riepilogo e, se la visita e' chiusa, il modulo per
 * interesse e feedback. `onSaved()` ricarica la scheda.
 */
export function mountVisitOutcome(container, { detail, onSaved }) {
  if (!isVisit(detail)) return null;
  const visita = detail.visit;
  const sezione = el('section', 'agenda-visit-outcome');
  sezione.dataset.visitOutcome = '';
  sezione.appendChild(el('h3', 'section-title', 'Esito della visita'));

  const dati = el('div', 'detail-grid agenda-drawer-grid');
  dati.appendChild(riga('Interesse', visita.interest_label || (visita.outcome ? `${visita.outcome}` : 'Non registrato')));
  dati.appendChild(riga('Feedback e osservazioni', visita.feedback || 'Nessuno'));
  sezione.appendChild(dati);

  if (!visita.can_record_feedback) {
    sezione.appendChild(el('p', 'muted',
      'Registra prima la visita con «Visita effettuata», «Cliente assente» o «Visita non effettuata»: '
      + 'poi potrai aggiungere interesse e feedback. La visita non si chiude da sola quando passa l’orario.'));
    container.appendChild(sezione);
    return sezione;
  }

  const form = el('form', 'agenda-form agenda-visit-outcome-form');
  form.noValidate = true;
  const campoInteresse = el('div', 'form-field');
  campoInteresse.appendChild(el('label', '', 'Interesse dell’acquirente'));
  const select = el('select', 'input');
  select.dataset.field = 'visit-interest';
  const vuoto = el('option', '', '— non indicato —');
  vuoto.value = '';
  select.appendChild(vuoto);
  for (const livello of INTEREST_LEVELS) {
    const o = el('option', '', livello.label);
    o.value = livello.code;
    if (livello.code === visita.interest) o.selected = true;
    select.appendChild(o);
  }
  select.disabled = !visita.can_record_interest;
  campoInteresse.appendChild(select);
  if (!visita.can_record_interest) {
    campoInteresse.appendChild(el('small', 'muted', 'L’interesse si registra solo per una visita effettuata.'));
  }
  form.appendChild(campoInteresse);

  const campoFeedback = el('div', 'form-field');
  campoFeedback.appendChild(el('label', '', 'Feedback e osservazioni'));
  const area = el('textarea', 'input');
  area.dataset.field = 'visit-feedback';
  area.maxLength = 4000;
  area.value = visita.feedback || '';
  campoFeedback.appendChild(area);
  campoFeedback.appendChild(el('small', 'muted',
    'Nota interna: non viene mai pubblicata al proprietario senza la tua approvazione.'));
  form.appendChild(campoFeedback);

  const errore = el('div', 'field-error');
  errore.setAttribute('role', 'alert');
  errore.dataset.error = '';
  form.appendChild(errore);
  const barra = el('div', 'action-bar');
  const salva = el('button', 'btn primary', 'Salva esito');
  salva.type = 'submit';
  salva.dataset.submit = '';
  barra.appendChild(salva);
  form.appendChild(barra);

  let occupato = false;
  form.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    if (occupato) return;
    occupato = true;
    salva.disabled = true;
    errore.textContent = '';
    try {
      const corpo = { feedback: area.value.trim() || null };
      if (visita.can_record_interest) corpo.interest = select.value || null;
      await putVisitOutcome(detail.appointment.id, corpo);
      if (onSaved) await onSaved();
    } catch (e) {
      errore.textContent = errorMessage(e);
    } finally {
      occupato = false;
      salva.disabled = false;
    }
  });
  sezione.appendChild(form);
  container.appendChild(sezione);
  return sezione;
}
