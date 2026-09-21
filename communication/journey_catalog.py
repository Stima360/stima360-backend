"""P29-3D - il CATALOGO: le journey che il prodotto conosce, e come nascono.

UNA JOURNEY NON SI CREA CON UNA MIGRATION, E QUESTO E' IL MOTIVO

Una `INSERT` dentro la 071 avrebbe messo la sequenza commerciale nello stesso
posto in cui vivono le tabelle: applicata a tutte le agenzie insieme,
irripetibile, e modificabile solo con un'altra migration. Una journey invece e'
una DECISIONE DI UNA AGENZIA - questa la vuole, quella no, quella la vuole fra
un mese - e va creata quando qualcuno lo chiede, per l'agenzia che lo chiede.

Qui sta la definizione, in Python, versionata con il codice; il provisioning e'
una funzione idempotente che si puo' chiamare quante volte si vuole.

PROVISIONING E ATTIVAZIONE SONO DUE ATTI DIVERSI

`ensure_stima_lead_v1` crea la journey in `draft` e i suoi cinque passi. Non
attiva niente: una journey in bozza non iscrive nessuno e non manda niente.
L'attivazione e' un secondo gesto, esplicito, che porta con se' il cutoff
storico (P29-3A.1 §C: da quel momento in avanti, mai indietro). Tenerli
separati e' cio' che permette di preparare la sequenza, leggerla, e decidere
DOPO se e quando accenderla.

COSA FA SE LA v1 ESISTE GIA': niente. Non la aggiorna, non la riattiva se e'
stata ritirata, non aggiunge passi. Una versione pubblicata e' immutabile come
i suoi template: se serve cambiarla, si crea la v2. Questa funzione la si puo'
chiamare mille volte e la mille-e-unesima trova quello che ha trovato la prima.
"""
from __future__ import annotations

from typing import Any

from . import journey_repository as repo
from . import journey_service
from .journey_enums import JOURNEY_ACTIVE, TRIGGER_STIMA_PDF_SENT

#: La chiave della sequenza della stima, e la sua prima versione.
STIMA_LEAD_KEY = "stima_lead"
STIMA_LEAD_VERSION = 1

#: Il fuso in cui si leggono gli orari della finestra. Non e' quello del
#: server, e non deve esserlo: vedi `communication/send_window.py`.
STIMA_LEAD_TIMEZONE = "Europe/Rome"

#: LA FINESTRA: lunedi'-sabato, 09:00-19:00 ora di Roma. La domenica non si
#: manda: un passo che scade di domenica parte al primo orario valido del
#: lunedi', e lo calcola `send_window`, non un ramo scritto a mano.
FINESTRA_LAVORATIVA = {"days": [1, 2, 3, 4, 5, 6], "from": "09:00", "to": "19:00"}

_GIORNO = 86400

#: I CINQUE PASSI. I ritardi sono quelli del mandato, e ciascuno si misura da
#: un `sent_at`: M1 dalla mail della stima, gli altri dall'invio del passo
#: precedente. Mai dalla nascita dell'iscrizione - un tick in ritardo
#: sposterebbe tutta la sequenza.
PASSI_STIMA_LEAD: tuple[dict[str, Any], ...] = (
    {"step_no": 1, "step_key": "M1", "reason_code": "m1",
     "delay_from": "trigger", "delay_seconds": 1 * _GIORNO,
     "default_mode": "automatic", "template_key": "stima_lead_m1"},
    {"step_no": 2, "step_key": "M2", "reason_code": "m2",
     "delay_from": "previous_step_sent", "delay_seconds": 4 * _GIORNO,
     "default_mode": "automatic", "template_key": "stima_lead_m2"},
    # M3 e' ASSISTITO: propone un sopralluogo, cioe' impegna il tempo di una
    # persona. Nasce come attesa, e parte solo quando un operatore lo manda.
    {"step_no": 3, "step_key": "M3", "reason_code": "m3",
     "delay_from": "previous_step_sent", "delay_seconds": 7 * _GIORNO,
     "default_mode": "assisted", "template_key": "stima_lead_m3"},
    {"step_no": 4, "step_key": "M4", "reason_code": "m4",
     "delay_from": "previous_step_sent", "delay_seconds": 14 * _GIORNO,
     "default_mode": "automatic", "template_key": "stima_lead_m4"},
    {"step_no": 5, "step_key": "M5", "reason_code": "m5",
     "delay_from": "previous_step_sent", "delay_seconds": 30 * _GIORNO,
     "default_mode": "automatic", "template_key": "stima_lead_m5"},
)


def _passi_completi() -> list[dict[str, Any]]:
    """I passi, con i campi che tutti condividono aggiunti una volta sola."""
    return [
        {**passo, "channel": "email", "communication_type": "marketing",
         "template_version": 1, "send_window": dict(FINESTRA_LAVORATIVA),
         # `stop_on` resta vuoto: le condizioni di stop sono quelle GLOBALI
         # del motore (incarico, sopralluogo, consulenza, lead chiuso,
         # contatto inattivo, consenso) e valgono per tutti i passi. Un
         # elenco per passo qui darebbe l'impressione che ce ne siano altre.
         "stop_on": []}
        for passo in PASSI_STIMA_LEAD
    ]


def ensure_stima_lead_v1(ctx, *, cur=None) -> dict[str, Any]:
    """La journey della stima, v1, per l'agenzia del chiamante. Idempotente.

    Restituisce ``{'journey': riga, 'created': bool}``. Se la v1 esiste gia' -
    in bozza, attiva o ritirata - la restituisce com'e': `created=False` e
    nessuna scrittura. Non attiva mai niente.
    """
    with journey_service.cursore(cur) as (_, c):
        esistente = repo.select_journey_version(
            c, ctx, journey_key=STIMA_LEAD_KEY, version=STIMA_LEAD_VERSION)
        if esistente is not None:
            esistente["steps"] = repo.list_steps(c, ctx, esistente["id"])
            return {"journey": esistente, "created": False}

        journey = journey_service.provision_journey(
            ctx, journey_key=STIMA_LEAD_KEY, version=STIMA_LEAD_VERSION,
            trigger_type=TRIGGER_STIMA_PDF_SENT,
            name="Sequenza stima", steps=_passi_completi(),
            send_timezone=STIMA_LEAD_TIMEZONE, cur=c)
        journey["steps"] = repo.list_steps(c, ctx, journey["id"])
        return {"journey": journey, "created": True}


def stima_lead_attiva(ctx, *, cur=None) -> dict[str, Any] | None:
    """La versione ATTIVA della sequenza della stima, se ce n'e' una."""
    with journey_service.cursore(cur) as (_, c):
        journey = repo.select_active_journey(c, ctx, STIMA_LEAD_KEY)
        if journey is not None and journey["status"] == JOURNEY_ACTIVE:
            journey["steps"] = repo.list_steps(c, ctx, journey["id"])
        return journey
