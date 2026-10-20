// STIMA360 OS — components/lead-labels.js (SEMPLIFICAZIONE-CRM-1)
//
// Le parole italiane per i valori TECNICI dei lead CORE (core/enums.py:
// LEAD_PIPELINES, LEAD_STAGES, LEAD_STATUSES). Solo etichette: i valori
// salvati e inviati restano quelli del backend. Stesse parole gia' usate
// dalla scheda Contatto (views/contatto-dettaglio.js), qui in un modulo
// condiviso per l'Agenda e per chi verra'.

export const LEAD_PIPELINE_LABELS = Object.freeze({ sell: 'Vendita', buy: 'Acquisto', general: 'Generico' });
export const LEAD_STAGE_LABELS = Object.freeze({
  new: 'Nuovo', contacted: 'Contattato', qualified: 'Qualificato', appointment: 'Appuntamento',
  proposal: 'Proposta', won: 'Convertito', lost: 'Da recuperare',
});
export const LEAD_STATUS_LABELS = Object.freeze({ open: 'Aperto', paused: 'In pausa', closed: 'Chiuso' });
