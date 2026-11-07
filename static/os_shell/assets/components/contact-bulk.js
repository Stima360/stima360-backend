// STIMA360 OS — components/contact-bulk.js
// SELEZIONE MULTIPLA dei Contatti: la selezione, la conferma, l'esecuzione a
// blocchi con l'avanzamento e il riepilogo. La usano l'elenco Contatti
// («Elimina selezionati») e il Cestino («Ripristina selezionati», «Ripristina
// questa operazione»).
//
// NESSUNA regola qui: permessi, riservatezza, agenzia, blocchi e storico li
// decide il server, contatto per contatto (core/contact_bulk.py). Il browser
// manda solo id (al piu' 100 per richiesta) e mostra cosa e' successo. Mai una
// cancellazione definitiva: si sposta nel Cestino e si ripristina.
// Tutto il testo che arriva dal server passa da escapeHtml.
import { escapeHtml } from './st-table.js';
import { TRASH_REASONS } from '../trash/trash-model.js';
import { bulkRestoreContacts, bulkTrashContacts } from '../trash/trash-api.js';

export const BLOCK = 100;
/** Da quanti contatti in su la conferma chiede di scrivere il numero. */
export const TYPE_THRESHOLD = 100;
/** Quante voci per gruppo mostra il riepilogo (il resto e' «e altri N»). */
const SUMMARY_SHOWN = 50;

export const fmt = (n) => Number(n || 0).toLocaleString('it-IT');
const contatti = (n) => `${fmt(n)} ${Number(n) === 1 ? 'contatto' : 'contatti'}`;

/**
 * La selezione: singoli id, oppure «tutti i risultati» (gli id della ricerca,
 * meno quelli tolti a mano). Sopravvive a pagine e ordinamento; chi la usa la
 * azzera quando cambiano ricerca o filtri.
 */
export function createSelection() {
  let mode = 'ids';
  const ids = new Set();
  let all = [];
  let allSet = new Set();
  const excluded = new Set();
  const api = {
    get mode() { return mode; },
    get totalAll() { return all.length; },
    count() { return mode === 'all' ? all.length - excluded.size : ids.size; },
    has(id) {
      const n = Number(id);
      return mode === 'all' ? allSet.has(n) && !excluded.has(n) : ids.has(n);
    },
    set(id, on) {
      const n = Number(id);
      if (!Number.isInteger(n) || n <= 0) return;
      if (mode === 'all') {
        if (!allSet.has(n)) { if (!on) return; all.push(n); allSet.add(n); }
        if (on) excluded.delete(n); else excluded.add(n);
      } else if (on) ids.add(n); else ids.delete(n);
    },
    setMany(list, on) { for (const id of list) api.set(id, on); },
    selectAll(list) {
      mode = 'all';
      all = (list || []).map(Number).filter((n) => Number.isInteger(n) && n > 0);
      allSet = new Set(all);
      excluded.clear();
      ids.clear();
    },
    clear() { mode = 'ids'; ids.clear(); all = []; allSet = new Set(); excluded.clear(); },
    list() {
      const out = mode === 'all' ? all.filter((n) => !excluded.has(n)) : Array.from(ids);
      return out.sort((a, b) => a - b);
    },
  };
  return api;
}

const GRUPPI = ['blocked', 'needs_admin', 'not_yours', 'not_available', 'busy', 'already'];

/**
 * Esegue `call(blocco, operationId)` a blocchi di BLOCK id, rimandando i
 * `remaining` del server. Si ferma su errore, su `shouldStop()` o se il server
 * non avanza. Ritorna il riepilogo aggregato e gli id non ancora fatti.
 */
export async function runInBlocks(ids, call, { onProgress = null, shouldStop = null } = {}) {
  const totale = ids.length;
  let coda = ids.slice();
  const agg = { done: [], operation_id: null, stopped: false, error: null, remaining: [] };
  for (const g of GRUPPI) agg[g] = [];
  while (coda.length) {
    if (shouldStop && shouldStop()) { agg.stopped = true; break; }
    const pezzo = coda.slice(0, BLOCK);
    let r;
    try {
      r = await call(pezzo, agg.operation_id);
    } catch (error) {
      agg.error = error;
      break;
    }
    agg.operation_id = (r && r.operation_id) || agg.operation_id;
    agg.done.push(...((r && (r.trashed || r.restored)) || []));
    for (const g of GRUPPI) agg[g].push(...((r && r[g]) || []));
    const rimasti = (r && Array.isArray(r.remaining)) ? r.remaining : [];
    if (rimasti.length >= pezzo.length) {          // il server non avanza: mai un giro infinito
      agg.error = new Error('Il server non riesce a procedere: riprova più tardi.');
      break;
    }
    coda = rimasti.concat(coda.slice(pezzo.length));
    if (onProgress) onProgress({ processed: totale - coda.length, total: totale, done: agg.done.length });
  }
  agg.remaining = coda;
  return agg;
}

function voceHtml(v, extra = '') {
  const nome = v.label ? escapeHtml(v.label) : `Contatto #${Number(v.id)}`;
  return `<li><a href="#/contatti/${Number(v.id)}">${nome}</a>${extra}</li>`;
}

function gruppoHtml(titolo, voci, { attr, spiega = '', dettaglio = null, link = true } = {}) {
  if (!voci.length) return '';
  const mostrate = voci.slice(0, SUMMARY_SHOWN);
  const altre = voci.length - mostrate.length;
  const righe = link
    ? mostrate.map((v) => voceHtml(v, dettaglio ? dettaglio(v) : '')).join('')
    : '';
  return `
    <section class="bulk-group" data-bulk-group="${attr}">
      <h4>${escapeHtml(titolo)} <span class="bulk-group-count">${fmt(voci.length)}</span></h4>
      ${spiega ? `<p class="muted">${escapeHtml(spiega)}</p>` : ''}
      ${righe ? `<ul class="bulk-list">${righe}</ul>` : ''}
      ${link && altre > 0 ? `<p class="muted">e altri ${fmt(altre)}</p>` : ''}
    </section>`;
}

/** Il riepilogo finale (esportato per le prove). `kind`: 'trash' | 'restore'. */
export function bulkSummaryHtml(esito, kind = 'trash') {
  const spostare = kind === 'trash';
  const fatti = esito.done.length;
  const titolo = spostare
    ? `${contatti(fatti)} ${fatti === 1 ? 'spostato' : 'spostati'} nel Cestino`
    : `${contatti(fatti)} ${fatti === 1 ? 'ripristinato' : 'ripristinati'}`;
  const nonFatti = GRUPPI.reduce((s, g) => s + esito[g].length, 0);
  const motivi = (v) => {
    const testi = (v.blockers || []).map((b) => b.label).filter(Boolean);
    return testi.length ? ` <span class="muted">— ${escapeHtml(testi.join(' · '))}</span>` : '';
  };
  const avvisi = [];
  if (esito.stopped) avvisi.push(`Interrotto: ${contatti(esito.remaining.length)} non ${spostare ? 'spostati' : 'ripristinati'}.`);
  if (esito.error) avvisi.push(`Errore: ${esito.error.message || 'operazione non completata'}. ${contatti(esito.remaining.length)} non ancora ${spostare ? 'spostati' : 'ripristinati'}.`);
  return `
    <div class="bulk-summary" data-bulk-summary="${kind}">
      <p class="bulk-summary-head" data-bulk-done="${fatti}"><strong>${escapeHtml(titolo)}</strong>${nonFatti ? `, ${escapeHtml(contatti(nonFatti))} no` : ''}.</p>
      ${avvisi.map((a) => `<p class="field-error" role="alert">${escapeHtml(a)}</p>`).join('')}
      ${gruppoHtml('Bloccati da pratiche aperte', esito.blocked, { attr: 'blocked', dettaglio: motivi,
    spiega: 'Restano dove sono: chiudi prima le pratiche indicate (come per il singolo contatto).' })}
      ${gruppoHtml('Serve un amministratore', esito.needs_admin, { attr: 'needs_admin',
    spiega: 'Hanno uno storico operativo: può spostarli nel Cestino solo un amministratore o il titolare.' })}
      ${gruppoHtml('Non spostati da te', esito.not_yours, { attr: 'not_yours',
    spiega: 'Puoi ripristinare solo i contatti che hai spostato tu: chiedi a un amministratore.' })}
      ${gruppoHtml(spostare ? 'Già nel Cestino' : 'Già fuori dal Cestino', esito.already, { attr: 'already' })}
      ${gruppoHtml('Occupati', esito.busy, { attr: 'busy', spiega: 'In modifica da un’altra operazione in quel momento: riprova.' })}
      ${gruppoHtml('Non disponibili', esito.not_available, { attr: 'not_available', link: false,
    spiega: 'Non più visibili per te (rimossi, riservati o fuori dalla tua agenzia): non sono stati toccati.' })}
    </div>`;
}

function avanzamentoHtml(testo) {
  return `
    <div class="bulk-progress" data-bulk-progress="">
      <p data-bulk-progress-text="">${escapeHtml(testo)}</p>
      <div class="bulk-progress-track"><div class="bulk-progress-fill" data-bulk-progress-fill="" style="width:0%"></div></div>
      <div class="modal-actions"><button type="button" class="btn ghost" data-bulk-stop="">Interrompi</button></div>
    </div>`;
}

/** Il numero scritto per confermare: «5214», «5.214» o «5 214». */
export function typedMatches(testo, n) {
  return String(testo || '').replace(/[\s.']/g, '') === String(n);
}

function apri(dialog) {
  if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
}

/**
 * Esegue a blocchi dentro `dialog` (avanzamento, «Interrompi») e mostra il
 * riepilogo con i comandi finali. `verbo` per il testo dell'avanzamento.
 */
async function esegui(dialog, ids, call, { kind, onFinished, afterSummary }) {
  let fermo = false;
  let inCorso = true;
  const blocca = (e) => { if (inCorso) e.preventDefault(); };
  dialog.addEventListener('cancel', blocca);
  const verbo = kind === 'trash' ? 'Spostati' : 'Ripristinati';
  dialog.querySelector('[data-bulk-body]').innerHTML = avanzamentoHtml(`${verbo} 0 di ${fmt(ids.length)}…`);
  const stop = dialog.querySelector('[data-bulk-stop]');
  stop.addEventListener('click', () => { fermo = true; stop.disabled = true; stop.textContent = 'Interruzione dopo questo blocco…'; });
  const esito = await runInBlocks(ids, call, {
    shouldStop: () => fermo,
    onProgress: ({ processed, total, done }) => {
      const t = dialog.querySelector('[data-bulk-progress-text]');
      const f = dialog.querySelector('[data-bulk-progress-fill]');
      if (t) t.textContent = `${verbo} ${fmt(done)} · esaminati ${fmt(processed)} di ${fmt(total)}…`;
      if (f) f.style.width = `${Math.round((processed / Math.max(1, total)) * 100)}%`;
    },
  });
  inCorso = false;
  dialog.removeEventListener('cancel', blocca);
  const body = dialog.querySelector('[data-bulk-body]');
  body.innerHTML = `${bulkSummaryHtml(esito, kind)}<div class="modal-actions" data-bulk-final=""></div>`;
  const azioni = body.querySelector('[data-bulk-final]');
  if (afterSummary) afterSummary(azioni, esito);
  const chiudi = document.createElement('button');
  chiudi.type = 'button';
  chiudi.className = 'btn primary';
  chiudi.textContent = 'Chiudi';
  chiudi.setAttribute('data-bulk-close', '');
  chiudi.addEventListener('click', () => dialog.close());
  azioni.appendChild(chiudi);
  if (onFinished) await onFinished(esito);
  return esito;
}

/**
 * «Elimina selezionati»: conferma con il numero esatto (motivo obbligatorio; da
 * TYPE_THRESHOLD in su si scrive il numero), poi lo spostamento a blocchi e il
 * riepilogo, con «Annulla: ripristina i N spostati». Nulla parte senza conferma.
 */
export function openBulkTrashDialog(dialog, { ids, onFinished = null } = {}) {
  const n = ids.length;
  const scrivere = n >= TYPE_THRESHOLD;
  dialog.innerHTML = `
    <form method="dialog" class="trash-form bulk-dialog" data-bulk-dialog="trash">
      <h3 class="trash-title">Sposta nel Cestino ${escapeHtml(contatti(n))}</h3>
      <div data-bulk-body="">
        <ul class="bulk-effects">
          <li>Vanno nel <strong>Cestino</strong>, non vengono cancellati: puoi ripristinarli quando vuoi.</li>
          <li>Spariscono da elenchi, ricerche e selettori; le automazioni vengono sospese e i messaggi in coda annullati.</li>
          <li>I contatti con pratiche aperte o storico protetto <strong>restano dove sono</strong>: te li elenco alla fine.</li>
        </ul>
        <label class="field">Motivo
          <select class="input" name="reason" data-bulk-reason="" required>
            <option value="">Scegli il motivo…</option>
            ${TRASH_REASONS.map((r) => `<option value="${escapeHtml(r.value)}">${escapeHtml(r.label)}</option>`).join('')}
          </select>
        </label>
        <label class="field">Nota (facoltativa)
          <textarea class="input" name="note" maxlength="500" rows="2" data-bulk-note=""></textarea>
        </label>
        ${scrivere ? `<label class="field">Per confermare scrivi <strong data-bulk-expected="">${escapeHtml(fmt(n))}</strong>
          <input class="input" name="typed" inputmode="numeric" autocomplete="off" data-bulk-typed=""></label>` : ''}
        <p class="field-error" data-bulk-error="" role="alert"></p>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-bulk-cancel="">Annulla</button>
          <button type="button" class="btn danger" data-bulk-confirm="" disabled>Sposta nel Cestino ${escapeHtml(contatti(n))}</button>
        </div>
      </div>
    </form>`;
  const motivo = dialog.querySelector('[data-bulk-reason]');
  const scritto = dialog.querySelector('[data-bulk-typed]');
  const conferma = dialog.querySelector('[data-bulk-confirm]');
  const valido = () => Boolean(motivo.value) && (!scrivere || typedMatches(scritto.value, n));
  const aggiorna = () => { conferma.disabled = !valido(); };
  motivo.addEventListener('change', aggiorna);
  if (scritto) scritto.addEventListener('input', aggiorna);
  dialog.querySelector('[data-bulk-cancel]').addEventListener('click', () => dialog.close());
  conferma.addEventListener('click', async () => {
    if (!valido()) return;
    const reason = motivo.value;
    const note = dialog.querySelector('[data-bulk-note]').value;
    await esegui(dialog, ids, (pezzo, op) => bulkTrashContacts(pezzo, reason, note, op), {
      kind: 'trash',
      onFinished,
      afterSummary: (azioni, esito) => {
        if (!esito.done.length) return;
        const annulla = document.createElement('button');
        annulla.type = 'button';
        annulla.className = 'btn ghost';
        annulla.setAttribute('data-bulk-undo', '');
        annulla.textContent = `Annulla: ripristina ${esito.done.length === 1 ? 'il contatto spostato' : `i ${fmt(esito.done.length)} spostati`}`;
        annulla.addEventListener('click', () => esegui(dialog, esito.done.slice(),
          (pezzo, op) => bulkRestoreContacts(pezzo, op), { kind: 'restore', onFinished }));
        azioni.appendChild(annulla);
      },
    });
  });
  apri(dialog);
}

/** «Ripristina selezionati» / «Ripristina questa operazione»: conferma con il numero, poi a blocchi. */
export function openBulkRestoreDialog(dialog, { ids, title = null, onFinished = null } = {}) {
  const n = ids.length;
  dialog.innerHTML = `
    <form method="dialog" class="trash-form bulk-dialog" data-bulk-dialog="restore">
      <h3 class="trash-title">${escapeHtml(title || `Ripristina ${contatti(n)}`)}</h3>
      <div data-bulk-body="">
        <ul class="bulk-effects">
          <li>Tornano negli elenchi con gli stessi dati e collegamenti.</li>
          <li>Automazioni sospese e messaggi annullati <strong>restano tali</strong>: si riattivano a mano dalla scheda.</li>
          <li>Ripristini solo ciò che puoi: il resto te lo elenco alla fine.</li>
        </ul>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-bulk-cancel="">Annulla</button>
          <button type="button" class="btn primary" data-bulk-confirm="">Ripristina ${escapeHtml(contatti(n))}</button>
        </div>
      </div>
    </form>`;
  dialog.querySelector('[data-bulk-cancel]').addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-bulk-confirm]').addEventListener('click', () => esegui(dialog, ids,
    (pezzo, op) => bulkRestoreContacts(pezzo, op), { kind: 'restore', onFinished }));
  apri(dialog);
}

/**
 * La barra della selezione (fissa in basso, sopra la barra del telefono):
 * «N selezionati · Deseleziona tutti · <azione>». Ritorna `{ update(n) }`.
 * La barra vive in `document.body`: dentro la Shell un antenato con effetti
 * grafici fa da riferimento ai `position: fixed` e la barra finirebbe in fondo
 * alla pagina invece che allo schermo. In `host` resta il segnaposto che lascia
 * lo spazio in fondo all'elenco. Quando la vista cambia (host staccato), la barra
 * se ne va con lei.
 */
export function mountBulkBar(host, { actionLabel, actionClass = 'btn danger', onAction, onClear }) {
  const bar = document.createElement('div');
  bar.className = 'bulk-bar';
  bar.hidden = true;
  bar.setAttribute('data-bulk-bar', '');
  bar.setAttribute('role', 'region');
  bar.setAttribute('aria-label', 'Contatti selezionati');
  bar.innerHTML = `
    <span class="bulk-bar-count" data-bulk-count="" aria-live="polite"></span>
    <button type="button" class="btn ghost" data-bulk-clear="">Deseleziona tutti</button>
    <button type="button" class="${escapeHtml(actionClass)}" data-bulk-action=""></button>`;
  const azione = bar.querySelector('[data-bulk-action]');
  bar.querySelector('[data-bulk-clear]').addEventListener('click', () => onClear && onClear());
  azione.addEventListener('click', () => onAction && onAction());
  host.replaceChildren();
  if (host.id) bar.setAttribute('data-bulk-bar-for', host.id);
  document.body.appendChild(bar);
  // Quando la vista lascia la pagina, la barra se ne va con lei. NON si ascolta
  // `hashchange`: e' del router della Shell (e della sua guardia), e basta un
  // osservatore del documento.
  let osservatore = null;
  const pulisci = () => {
    if (host.isConnected === false) {
      bar.remove();
      if (osservatore) osservatore.disconnect();
    }
  };
  if (typeof MutationObserver === 'function') {
    osservatore = new MutationObserver(pulisci);
    osservatore.observe(document.body, { childList: true, subtree: true });
  }
  return {
    element: bar,
    update(n) {
      if (host.isConnected === false) { bar.remove(); return; }
      bar.hidden = !n;
      host.classList.toggle('bulk-bar-on', Boolean(n));
      bar.querySelector('[data-bulk-count]').textContent = `${fmt(n)} ${n === 1 ? 'selezionato' : 'selezionati'}`;
      azione.textContent = `${actionLabel} (${fmt(n)})`;
    },
  };
}
