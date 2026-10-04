// STIMA360 OS — sellers/seller-model.js (VENDITORI-1)
//
// Funzioni pure dell'area Venditori e dell'interruttore «Vende» della scheda
// immobile. Nessun DOM, nessuna rete: si provano in node.
//
// Il modello (crm/sellers.py): Opportunita' Venditore = lead SELL del
// contatto + property_leads(relation_type='seller') verso QUELL'immobile. Lo
// stato si legge da `property.leads` (GET /api/property/properties/{id}, che
// porta gia' relation_type, pipeline, stage, status e contact_id di ogni lead
// collegato): nessuna chiamata in piu' per disegnare gli interruttori.

export const OWNER_ROLES = ['owner', 'seller'];

const STAGE_LABELS = {
  new: 'Nuovo', contacted: 'Contattato', qualified: 'Qualificato', appointment: 'Appuntamento',
  proposal: 'Proposta', won: 'Acquisito', lost: 'Perso',
};
const TYPE_LABELS = {
  call: 'Telefonata', meeting: 'Incontro', note: 'Nota', email: 'Email', whatsapp: 'WhatsApp',
  status_change: 'Cambio di stato',
};

export function stageLabel(stage) {
  return STAGE_LABELS[stage] || stage || '—';
}

export function interactionLabel(type) {
  return TYPE_LABELS[type] || type || '—';
}

/** I proprietari che possono «vendere»: una riga per contatto (owner|seller),
 *  nell'ordine della scheda. */
export function sellableOwners(contacts) {
  const visti = new Set();
  const esito = [];
  for (const c of contacts || []) {
    if (!OWNER_ROLES.includes(c.role) || visti.has(String(c.contact_id))) continue;
    visti.add(String(c.contact_id));
    esito.push(c);
  }
  return esito;
}

/** Lo stato «Vende» di un contatto su questo immobile, da `property.leads`:
 *  'none' | 'open' | 'paused' | 'closed', con il lead. Se ce n'e' piu' d'uno
 *  vince lo stesso ordine del server: aperto, sospeso, il piu' recente. */
export function sellerState(leads, contactId) {
  const miei = (leads || []).filter((l) => l.relation_type === 'seller' && l.pipeline === 'sell'
    && String(l.contact_id) === String(contactId));
  if (!miei.length) return { state: 'none', leadId: null, stage: null };
  const peso = (l) => (l.status === 'open' ? 2 : (l.status === 'paused' ? 1 : 0));
  miei.sort((a, b) => peso(b) - peso(a) || Number(b.lead_id) - Number(a.lead_id));
  const l = miei[0];
  const state = l.status === 'open' ? 'open' : (l.status === 'paused' ? 'paused' : 'closed');
  return { state, leadId: Number(l.lead_id), stage: l.stage };
}

/** I parametri di GET /api/crm/sellers: solo quelli valorizzati. */
export function worklistParams(filters = {}, offset = 0, limit = 30) {
  const p = new URLSearchParams();
  if (filters.view && filters.view !== 'all') p.set('view', filters.view);
  if (filters.status && filters.status !== 'active') p.set('status', filters.status);
  if (filters.agentId !== undefined && filters.agentId !== null && filters.agentId !== '') p.set('agent_id', String(filters.agentId));
  if (filters.city && filters.city.trim()) p.set('city', filters.city.trim());
  if (filters.search && filters.search.trim()) p.set('search', filters.search.trim());
  p.set('limit', String(limit));
  p.set('offset', String(offset));
  return p.toString();
}

/** «Via Roma 10, Fermo» */
export function propertyLine(p) {
  if (!p) return '—';
  const via = [p.address, p.civic_number].filter(Boolean).join(' ');
  return [via, p.city].filter(Boolean).join(', ') || p.title || `Immobile #${p.id}`;
}

/** Il link dell'acquisizione esistente, precompilato: immobile, proprietario
 *  e lead (CRM-OPS-3 `#/acquisizioni/nuova/<property_id>` esteso). */
export function acquisitionHref(item) {
  return `#/acquisizioni/nuova/${encodeURIComponent(item.property.id)}/${encodeURIComponent(item.contact.id)}/${encodeURIComponent(item.lead_id)}`;
}

/** Il telefono per un link `tel:` (solo cifre e +). */
export function telHref(phone) {
  const n = String(phone || '').replace(/[^\d+]/g, '');
  return n ? `tel:${n}` : null;
}

/** La frase per l'operatore, dal codice del backend. Mai un nome tecnico. */
export function sellerErrorMessage(error) {
  const code = error && error.code ? error.code : '';
  switch (code) {
    case 'SELLER_NOT_OWNER': return 'Il contatto non è collegato a questo immobile come proprietario.';
    case 'PROPERTY_IN_CENSUS': return 'Immobile in censimento: prendilo prima in carico.';
    case 'SELLER_PROPERTY_CLOSED': return 'L’immobile è venduto, ritirato o archiviato.';
    case 'SELLER_CONTACT_NOT_ASSIGNED': return 'Il proprietario è assegnato a un altro agente: chiedi a un amministratore.';
    case 'SELLER_LEAD_NOT_VISIBLE': return 'Esiste già un lead venditore di questo contatto che non è assegnato a te: chiedi a un amministratore di assegnartelo.';
    case 'SELLER_LEAD_AMBIGUOUS': return 'Il contatto ha più lead venditore senza immobile: scegli quale collegare.';
    case 'SELLER_LEAD_OTHER_PROPERTY': return 'Il lead scelto riguarda un altro immobile.';
    case 'SELLER_ALREADY_ACTIVE': return 'Per questo proprietario e questo immobile c’è già un’opportunità venditore.';
    case 'NETWORK': return 'Connessione assente. Riprova.';
    default:
      if (error && error.status === 404) return 'Non trovato: potrebbe appartenere a un’altra agenzia o non essere più disponibile.';
      return (error && (error.detail || error.message)) || 'Errore imprevisto.';
  }
}

/** «Scaduta da 2 giorni» / «Oggi» / «Tra 3 giorni», sul giorno di Roma. */
export function dueText(isoDate, now = new Date()) {
  if (!isoDate) return '';
  const giorno = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Rome' }).format(d);
  const a = new Date(`${giorno(new Date(isoDate))}T00:00:00Z`);
  const b = new Date(`${giorno(now)}T00:00:00Z`);
  const diff = Math.round((a - b) / 86400000);
  if (diff === 0) return 'Oggi';
  if (diff === 1) return 'Domani';
  if (diff === -1) return 'Ieri';
  return diff < 0 ? `Scaduta da ${-diff} giorni` : `Tra ${diff} giorni`;
}
