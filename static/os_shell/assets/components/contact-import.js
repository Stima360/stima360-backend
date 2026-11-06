// STIMA360 OS — components/contact-import.js
// IMPORTAZIONE RUBRICA: Contatti → «Importa rubrica». Un file .vcf (iPhone:
// iCloud.com → Esporta vCard; Android/Google Contatti: esporta vCard) si
// manda al server (POST /api/contact-imports/preview, multipart), che
// risponde con l'ANTEPRIMA: quante schede sono nuove, quante già in rubrica,
// quante da decidere (numero su più contatti) e quante senza numero.
// «Importa tutti» (POST .../commit) scrive i contatti: nuovi come riservati
// a chi importa, esistenti completati senza sovrascrivere, ambigui secondo la
// scelta fatta qui (unire / nuovo / salta). Nessun invio parte. Tutto il
// testo passa da `textContent`; il file non si legge mai nel browser.

import { apiPost } from '../core/api-client.js';

const API = '/api/contact-imports';

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

export function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) return 'Il file o i dati non sono validi.';
  return (error && error.message) || 'Operazione non riuscita.';
}

export const STATUS_LABELS = {
  new: 'Nuovo', existing: 'Già in rubrica', ambiguous: 'Da decidere', no_phone: 'Senza numero',
};

/** Il riepilogo dell'anteprima in parole. */
export function summaryText(s) {
  const parts = [`${s.entries} schede nel file`, `${s.new} nuove`, `${s.existing} già in rubrica`];
  if (s.ambiguous) parts.push(`${s.ambiguous} da decidere`);
  if (s.no_phone) parts.push(`${s.no_phone} senza numero (non importate)`);
  if (s.merged_in_file) parts.push(`${s.merged_in_file} unite perché avevano lo stesso numero`);
  if (s.invalid_phones) parts.push(`${s.invalid_phones} numeri non validi scartati`);
  return parts.join(' · ');
}

/** L'esito dell'importazione in parole. */
export function resultText(r) {
  const parts = [`${r.created} contatti creati`, `${r.updated} completati con numeri nuovi`, `${r.unchanged} già presenti e invariati`];
  if (r.undecided) parts.push(`${r.undecided} non importati perché non hai deciso`);
  if (r.skipped) parts.push(`${r.skipped} saltati`);
  if (r.no_phone) parts.push(`${r.no_phone} senza numero`);
  parts.push(`${r.conversations_linked} chat WhatsApp collegate`);
  return parts.join(' · ');
}

export function entryName(e) {
  return e.display_name || e.company_name || 'Senza nome';
}

/** Manda il file al server: la sola lettura del file la fa il server. */
async function uploadPreview(file) {
  const form = new FormData();
  form.append('file', file, file.name || 'rubrica.vcf');
  const response = await fetch(`${API}/preview`, { method: 'POST', body: form, credentials: 'include' });
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  if (!response.ok) {
    const error = new Error((data && data.detail && data.detail.message) || `Errore ${response.status}`);
    error.status = response.status; error.data = data;
    throw error;
  }
  return data;
}

/**
 * Apre la finestra di importazione nel <dialog> indicato. `onDone(result)`
 * dopo un'importazione riuscita.
 */
export function openContactImportDialog(dialogEl, onDone) {
  dialogEl.replaceChildren();
  const form = el('form', 'contact-import');
  form.setAttribute('data-contact-import', '');
  form.addEventListener('submit', (e) => e.preventDefault());
  form.appendChild(el('h3', 'section-title', 'Importa rubrica'));
  form.appendChild(el('p', 'muted', 'Scegli il file .vcf esportato dal telefono (iPhone: iCloud.com → Contatti → Esporta vCard; Android: Contatti → Esporta). Vedrai un’anteprima prima di importare: i contatti importati sono riservati a te, i numeri già in rubrica non creano doppioni e non parte nessun messaggio.'));
  const fileField = el('label', 'form-field');
  fileField.appendChild(el('span', null, 'File della rubrica (.vcf)'));
  const input = el('input', 'input');
  input.type = 'file'; input.name = 'file'; input.accept = '.vcf,text/vcard,text/x-vcard';
  input.setAttribute('data-contact-import-file', '');
  fileField.appendChild(input);
  const status = el('div');
  status.setAttribute('role', 'status');
  status.setAttribute('data-contact-import-status', '');
  const preview = el('div', 'contact-import-preview');
  preview.setAttribute('data-contact-import-preview', '');
  const actions = el('div', 'modal-actions');
  const cancel = button('Chiudi', 'ghost', 'data-contact-import-close');
  const analyze = button('Analizza il file', 'primary', 'data-contact-import-analyze');
  const importAll = button('Importa tutti', 'primary', 'data-contact-import-commit');
  importAll.hidden = true;
  actions.append(cancel, analyze, importAll);
  form.append(fileField, status, preview, actions);
  dialogEl.appendChild(form);
  if (typeof dialogEl.showModal === 'function') dialogEl.showModal(); else dialogEl.setAttribute('open', '');
  const close = () => { if (typeof dialogEl.close === 'function') dialogEl.close(); else dialogEl.removeAttribute('open'); };
  cancel.addEventListener('click', close);

  let current = null;          // l'anteprima salvata dal server
  const decisions = {};        // index -> { action, contact_id }

  function renderPreview(data) {
    preview.replaceChildren();
    const s = data.summary;
    const sum = el('p', null, summaryText(s));
    sum.setAttribute('data-contact-import-summary', '');
    preview.appendChild(sum);
    const ambiguous = data.entries.filter((e) => e.status === 'ambiguous');
    if (ambiguous.length) {
      preview.appendChild(el('p', 'muted', 'Queste schede hanno un numero presente su più contatti: scegli a quale unirle, se crearle come nuove o saltarle. Senza una scelta vengono saltate.'));
      const list = el('div', 'contact-import-ambiguous');
      for (const e of ambiguous) {
        const row = el('div', 'contact-import-row');
        row.setAttribute('data-contact-import-ambiguous', String(e.index));
        row.appendChild(el('strong', null, `${entryName(e)} · ${e.phones.map((p) => p.raw).join(', ')}`));
        const select = el('select', 'input');
        select.setAttribute('data-contact-import-decision', String(e.index));
        select.appendChild(el('option', null, '— salta questa scheda —')).value = 'skip';
        for (const c of e.candidates) {
          const o = el('option', null, `Unisci a: ${c.name}${c.phone ? ` (${c.phone})` : ''}${c.private ? ' · riservato' : ''}`);
          o.value = `merge:${c.id}`;
          select.appendChild(o);
        }
        select.appendChild(el('option', null, 'Crea un nuovo contatto')).value = 'create';
        select.addEventListener('change', () => {
          const v = select.value;
          if (v === 'create' || v === 'skip') decisions[e.index] = { action: v };
          else decisions[e.index] = { action: 'merge', contact_id: Number(v.split(':')[1]) };
        });
        row.appendChild(select);
        list.appendChild(row);
      }
      preview.appendChild(list);
    }
    const shown = data.entries.filter((e) => e.status !== 'ambiguous').slice(0, 200);
    if (shown.length) {
      const table = el('table', 'table contact-import-table');
      const head = el('tr');
      for (const h of ['Scheda', 'Numeri', 'Esito']) head.appendChild(el('th', null, h));
      table.appendChild(head);
      for (const e of shown) {
        const tr = el('tr');
        tr.setAttribute('data-contact-import-entry', e.status);
        tr.append(el('td', null, entryName(e)), el('td', null, e.phones.map((p) => p.raw).join(', ') || '—'),
          el('td', null, e.status === 'existing' && e.candidates[0] ? `${STATUS_LABELS.existing}: ${e.candidates[0].name}` : STATUS_LABELS[e.status] || e.status));
        table.appendChild(tr);
      }
      preview.appendChild(table);
      if (data.entries.length > shown.length) preview.appendChild(el('p', 'muted', `Mostrate le prime ${shown.length} schede.`));
    }
  }

  analyze.addEventListener('click', async () => {
    const file = input.files && input.files[0];
    if (!file) { status.replaceChildren(el('div', 'error-box', 'Scegli prima il file .vcf.')); return; }
    analyze.disabled = true;
    status.replaceChildren(el('p', 'muted', 'Analisi del file in corso…'));
    try {
      current = await uploadPreview(file);
      status.replaceChildren();
      renderPreview(current);
      importAll.hidden = false;
      analyze.hidden = true;
    } catch (error) {
      status.replaceChildren(el('div', 'error-box', errorMessage(error)));
      analyze.disabled = false;
    }
  });

  importAll.addEventListener('click', async () => {
    if (!current) return;
    importAll.disabled = true;
    status.replaceChildren(el('p', 'muted', 'Importazione in corso…'));
    try {
      const out = await apiPost(`${API}/${encodeURIComponent(String(current.import_id))}/commit`, { decisions });
      const ok = el('div', 'success-box', resultText(out.result));
      ok.setAttribute('data-contact-import-result', '');
      status.replaceChildren(ok);
      preview.replaceChildren();
      importAll.hidden = true;
      cancel.textContent = 'Chiudi';
      if (onDone) await onDone(out);
    } catch (error) {
      status.replaceChildren(el('div', 'error-box', errorMessage(error)));
      importAll.disabled = false;
    }
  });
}
