"""PARTENZA IMMEDIATA del follow-up V3 dopo un consenso email (regola del titolare).

Al salvataggio di un consenso email valido - registrato dall'operatore,
confermato dalla pagina del consenso o riconosciuto dal sito - non si aspetta
il cron: il contatto, se idoneo, entra nel V3 e la PRIMA email parte subito,
anche fuori dalla finestra d'invio. Le successive seguono il calendario.

Chi fa cosa, come sempre:
  * il motore (`journey_tick.prepara_partenza`) iscrive e accoda, con le
    stesse esclusioni del giro (consenso, revoche, disiscrizioni, blocchi,
    doppioni, tetto di 20 al giorno); non spedisce;
  * il dispatcher spedisce: ricontrolla il consenso subito prima del provider
    e applica il limite dei destinatari TEST
    (`COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS`);
  * qui si legge l'esito e lo si dice alla scheda, con il motivo esatto se
    l'email non e' partita.

Idempotente: chiamarla due volte non crea una seconda iscrizione ne' una
seconda email. Non solleva mai verso chi registra il consenso: il consenso
resta registrato comunque, e il cron riprova.
"""
from __future__ import annotations

import logging
from typing import Any

from operator_auth.context import SystemAgencyContext

from . import audience, dispatcher, journey_tick, test_recipients
from .database import communication_cursor
from .enums import CHANNEL_EMAIL

logger = logging.getLogger(__name__)

INVIATA, IN_CODA, NON_PARTITA, NON_ISCRITTO, ERRORE = "sent", "queued", "not_sent", "not_started", "error"

#: I motivi di un'email rimasta in coda, in italiano per la scheda.
MOTIVI_IN_CODA = {
    "test_recipient_not_allowed": "indirizzo non nell'elenco dei destinatari autorizzati del TEST: "
                                  "l'email resta in coda e non parte",
    "deferred_48h": "un'altra email marketing e' partita meno di 48 ore fa: la prima email slitta",
    "not_claimed": "in coda: la spedira' il prossimo giro del cron",
}


def avvia(ctx, contact_id: int, *, provider=None) -> dict[str, Any]:
    """La partenza immediata per un contatto. Esito per la scheda, mai un'eccezione."""
    try:
        sistema = SystemAgencyContext(agency_id=ctx.require_agency(), origin=dispatcher.DISPATCH_ORIGIN)
        preparata = journey_tick.prepara_partenza(sistema, contact_id)
        iscrizione = preparata.get("enrollment_id")
        if iscrizione is None:
            return _esito(NON_ISCRITTO, preparata["reason"])
        if preparata.get("reason"):
            return _esito(IN_CODA, preparata["reason"], enrollment_id=iscrizione)
        dispatcher.dispatch_batch(sistema, channel=CHANNEL_EMAIL,
                                  provider=provider or dispatcher.adapter_per(CHANNEL_EMAIL), limit=10)
        messaggio = _prima_email(sistema, iscrizione)
    except Exception as exc:  # noqa: BLE001 - il consenso e' gia' registrato: si dice, non si rompe
        logger.exception("avvio_immediato_failed contact_id=%s", contact_id)
        return _esito(ERRORE, type(exc).__name__)
    if messaggio is None:
        return _esito(IN_CODA, "not_claimed", enrollment_id=iscrizione)
    dati = {"enrollment_id": iscrizione, "message_id": messaggio["id"]}
    if messaggio["status"] == "sent":
        return _esito(INVIATA, None, **dati)
    if messaggio["status"] == "queued":
        motivo = ("test_recipient_not_allowed"
                  if test_recipients.blocked(messaggio, test_recipients.allowed()) else "not_claimed")
        return _esito(IN_CODA, motivo, **dati)
    return _esito(NON_PARTITA, messaggio["suppressed_reason"] or messaggio["error_code"] or messaggio["status"],
                  **dati)


def _prima_email(ctx, enrollment_id: int) -> dict[str, Any] | None:
    with communication_cursor() as (_conn, cur):
        cur.execute(
            """SELECT id, status, communication_type, destination_snapshot, suppressed_reason, error_code
                 FROM communication_messages
                WHERE agency_id = %s AND enrollment_id = %s
                ORDER BY step_no, id LIMIT 1""",
            (ctx.require_agency(), enrollment_id))
        riga = cur.fetchone()
    return dict(riga) if riga else None


def _esito(stato: str, motivo: str | None, **dati) -> dict[str, Any]:
    etichetta = None
    if motivo is not None:
        etichetta = (audience.MOTIVI_ESCLUSIONE.get(motivo) or MOTIVI_IN_CODA.get(motivo)
                     or f"non partita: {motivo}")
    return {"status": stato, "reason": motivo, "reason_label": etichetta, **dati}


__all__ = ["avvia", "INVIATA", "IN_CODA", "NON_PARTITA", "NON_ISCRITTO", "ERRORE", "MOTIVI_IN_CODA"]
