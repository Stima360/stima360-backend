// STIMA360 OS — components/match-labels.js (SEMPLIFICAZIONE-CRM-1)
//
// Le parole italiane per i valori tecnici del motore MATCH (match/enums.py).
// Solo etichette: i valori persistiti non cambiano. Usate dalla scheda
// Abbinamento e dalla scheda Immobile («Acquirenti compatibili»).

export const MATCH_CLASS_LABELS = Object.freeze({
  excellent: 'Eccellente', strong: 'Forte', good: 'Buono', possible: 'Possibile',
  weak: 'Debole', poor: 'Scarso', incompatible: 'Incompatibile',
});
export const COMPATIBILITY_STATUS_LABELS = Object.freeze({
  compatible: 'Compatibile', exception: 'Compatibile con eccezioni', incompatible: 'Incompatibile',
});
export const MATCH_COMMERCIAL_STATUS_LABELS = Object.freeze({
  new: 'Nuovo', to_review: 'Da valutare', approved: 'Approvato', rejected: 'Rifiutato',
  suggested: 'Suggerito', interested: 'Interessato', visit_requested: 'Visita richiesta',
  visit_scheduled: 'Visita programmata', visited: 'Visitato', offer_candidate: 'Candidato offerta',
  archived: 'Archiviato',
});
export const FRESHNESS_LABELS = Object.freeze({
  fresh: 'Aggiornato', stale: 'Da ricalcolare', recalculating: 'Ricalcolo in corso',
  failed: 'Errore', excluded: 'Escluso',
});
