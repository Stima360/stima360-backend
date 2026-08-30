// STIMA360 OS — placeholder.js
// Vista generica "Sezione in preparazione", riusata dalle 7 sezioni non
// ancora implementate in P0 (Contatti, Immobili, Acquirenti, Abbinamenti,
// Attivita, Automazioni, Impostazioni).

export function makePlaceholderView(title, description) {
  return async function renderPlaceholder(container) {
    container.innerHTML = `
      <div class="placeholder">
        <h2>${escapeHtml(title)}</h2>
        <p class="muted">Sezione in preparazione.</p>
        ${description ? `<p class="muted">${escapeHtml(description)}</p>` : ''}
      </div>
    `;
  };
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
