import { apiGet } from '../core/api-client.js';

export function bindSaleDetails(panel) {
  panel.querySelectorAll('.sale-detail-btn').forEach((button) => {
    button.addEventListener('click', () => openSaleDetail(panel, button.dataset.saleId));
  });
}

export async function openSaleDetail(container, id) {
  if (!/^[1-9]\d*$/.test(String(id))) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'modal';
  const body = document.createElement('div');
  body.className = 'modal-content';
  const title = document.createElement('h3');
  title.textContent = `Vendita #${id}`;
  const details = document.createElement('div');
  details.textContent = 'Caricamento…';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn ghost';
  close.textContent = 'Chiudi';
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => dialog.remove());
  body.append(title, details, close);
  dialog.append(body);
  container.append(dialog);
  dialog.showModal();
  try {
    const sale = await apiGet(`/api/sales/${id}`);
    details.replaceChildren();
    const statuses = { pending: 'In corso', completed: 'Completata', cancelled: 'Annullata' };
    for (const [label, value] of [
      ['Stato', statuses[sale.status] || sale.status], ['Proposta', sale.proposal_id],
      ['Immobile', sale.property_title || sale.property_id], ['Richiesta', sale.buy_title || sale.buy_request_id],
      ['Prezzo vendita', sale.sale_price], ['Completata il', sale.completed_at], ['Note', sale.notes],
    ]) {
      const row = document.createElement('p');
      row.textContent = `${label}: ${value ?? '—'}`;
      details.append(row);
    }
  } catch (error) {
    details.className = 'error-box';
    details.textContent = error.message || 'Vendita temporaneamente non disponibile.';
  }
}
