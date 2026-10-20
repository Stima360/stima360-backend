"""FASE A (follow-up 180 giorni) - i metadata di spedizione di una email.

Chi accoda un corpo di TESTO SEMPLICE (le sequenze, gli invii manuali) lo
dichiara qui: l'adapter lo rende in html conservando a capo ed elenchi, e
allega la versione testo. Per il marketing si aggiungono il Reply-To ufficiale
e l'indirizzo di disiscrizione per le intestazioni `List-Unsubscribe`.

PERCHE' NEI METADATA E NON NELL'ADAPTER. L'adapter non legge configurazione
(sentinella W3 di P29-2.5E) e non conosce il tipo di messaggio oltre la riga.
Scrivere qui, al momento dell'accodamento, rende il ledger autosufficiente:
la riga dice con quale Reply-To e quale link e' stata spedita.

Il Reply-To vale SOLO per il marketing: le email di servizio restano quelle
di sempre (decisione del proprietario, Fase A).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from .enums import (BODY_FORMAT_KEY, BODY_FORMAT_TEXT, REPLY_TO_KEY,
                    TYPE_MARKETING, UNSUBSCRIBE_URL_KEY)

logger = logging.getLogger(__name__)

REPLY_TO_ENV = "COMMUNICATION_REPLY_TO"
DEFAULT_REPLY_TO = "info@stima360.it"

#: Un indirizzo semplice, su una riga: niente nomi visualizzati, niente
#: virgole, niente a capo. Un valore strano NON diventa un Reply-To strano:
#: diventa nessun Reply-To, con una riga di log.
_INDIRIZZO = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def reply_to_marketing() -> str | None:
    """L'indirizzo di risposta delle email di marketing, dalla configurazione."""
    valore = (os.getenv(REPLY_TO_ENV) or "").strip() or DEFAULT_REPLY_TO
    if not _INDIRIZZO.match(valore):
        logger.warning("communication_reply_to_invalid env=%s", REPLY_TO_ENV)
        return None
    return valore


def metadata_testo(*, communication_type: str,
                   unsubscribe_url: str | None = None) -> dict[str, Any]:
    """I metadata di un corpo di testo semplice, pronti per `enqueue`."""
    metadata: dict[str, Any] = {BODY_FORMAT_KEY: BODY_FORMAT_TEXT}
    if communication_type != TYPE_MARKETING:
        return metadata
    risposta = reply_to_marketing()
    if risposta is not None:
        metadata[REPLY_TO_KEY] = risposta
    if unsubscribe_url:
        metadata[UNSUBSCRIBE_URL_KEY] = unsubscribe_url
    return metadata
