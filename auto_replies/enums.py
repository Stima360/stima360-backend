"""BLOCCO-4D - costanti del motore delle risposte automatiche."""
from __future__ import annotations

#: Arresto generale: il motore gira SOLO se questa variabile vale esattamente
#: `simulate`. Assente, vuota o con ogni altro valore: fermo, per tutte le
#: agenzie, qualunque sia la loro impostazione. Non esiste un valore d'invio.
GLOBAL_ENV = "WHATSAPP_AUTO_REPLY_MODE"
GLOBAL_SIMULATE = "simulate"

MODE_OFF = "off"
MODE_SIMULATE = "simulate"
MODES = (MODE_OFF, MODE_SIMULATE)

#: Versione delle regole: una decisione per (messaggio, versione). Cambiare
#: regole o testi (anche quelli del 4C) vuol dire cambiare questa stringa.
RULES_VERSION = "4d-1"

#: Quanti messaggi al massimo per giro e per agenzia.
TICK_LIMIT = 50

#: Una risposta manuale dell'operatore (4B) entro queste ore mette in pausa la
#: conversazione: la sta seguendo una persona.
OPERATOR_PAUSE_HOURS = 24

SKIPPED = "skipped"
SKIP_REASONS = {
    "window_closed": "Finestra di 24 ore chiusa: nessuna risposta automatica possibile.",
    "stop_in_conversation": "Il cliente ha scritto STOP in questa conversazione: nessuna risposta automatica.",
    "platform_optout": "Il numero ha chiesto di non ricevere comunicazioni STIMA360: nessuna risposta automatica.",
    "conversation_paused": "Conversazione in pausa: la segue un operatore.",
    "operator_replied": "Un operatore ha risposto a mano nelle ultime 24 ore: la conversazione la segue una persona.",
    "no_contact": "Mittente non associato a un contatto: serve un operatore.",
    "attachment": "Allegato o messaggio non di testo: non viene interpretato, serve un operatore.",
}
DECISION_LABELS = {
    "answer": "Risposta con dati certi",
    "ask_reference": "Domanda di chiarimento sull'immobile",
    "handoff": "Intervento dell'agente",
    "none": "Nessuna risposta",
    SKIPPED: "Nessuna risposta automatica",
}
SIMULATION_LABEL = "Simulazione — non inviato"
