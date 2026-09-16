"""Il provider finto di P29-2.4. NON MANDA NIENTE, e non puo'.

Esiste perche' il dispatcher possa essere costruito e provato per intero -
claim, gate del consenso, finalizzazione, recovery - prima che esista un
trasporto reale. Con un provider vero al suo posto, ogni prova del gate
sarebbe anche un invio a una persona.

Dichiara `returns_message_id = False` e `distinguishes_failure_class = False`
non per pigrizia ma per onesta': un trasporto che non chiama nessuno non ha un
id da restituire e non ha un esito da distinguere. Sono le stesse capability
che avra' l'adapter SMTP legacy di P29-2.5, quindi il dispatcher viene
esercitato fin d'ora sul percorso piu' povero - quello in cui un insuccesso
diventa `unknown` e non `rejected`.
"""

from __future__ import annotations

from typing import Any

from .base import OUTCOME_ACCEPTED, ProviderCapabilities, ProviderResult

NAME = "null"

CAPABILITIES = ProviderCapabilities(
    returns_message_id=False,
    distinguishes_failure_class=False,
    reports_delivery=False,
)


def send(message: dict[str, Any]) -> ProviderResult:
    """Non fa nulla e lo dichiara.

    `accepted` senza `provider_message_id`: e' coerente con le capability - un
    trasporto che non restituisce id non ne inventa uno - ed e' cio' che rende
    il percorso completo provabile senza mandare un messaggio a nessuno.
    """
    return ProviderResult(outcome=OUTCOME_ACCEPTED)
