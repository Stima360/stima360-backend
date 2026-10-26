"""V3 - il limite dei destinatari MARKETING per il collaudo (ambiente TEST).

Il TEST contiene anche contatti reali. Con la variabile d'ambiente
`COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS` impostata, il dispatcher spedisce
messaggi di marketing SOLO agli indirizzi elencati:

  * variabile ASSENTE      -> nessun limite (comportamento di sempre);
  * variabile PRESENTE     -> solo gli indirizzi elencati (separati da virgola,
                              punto e virgola o spazi); vuota o senza indirizzi
                              validi = nessun messaggio di marketing parte
                              (chiuso per difetto, mai aperto per errore).

Il filtro sta nella SELEZIONE dei messaggi da spedire: quelli verso altri
indirizzi non vengono nemmeno presi, restano in coda intatti (nessuno stato
cambiato, nessun tentativo consumato). Il dispatcher ricontrolla subito prima
del provider. Le email di SERVIZIO (la valutazione) non passano di qui.
"""
from __future__ import annotations

import os
import re

ENV = "COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS"
#: Il motivo scritto se un messaggio gia' preso risulta fuori elenco.
REASON = "test_recipient_not_allowed"


def allowed() -> frozenset[str] | None:
    """Gli indirizzi ammessi (minuscoli), o None se il limite non e' attivo."""
    grezzo = os.getenv(ENV)
    if grezzo is None:
        return None
    return frozenset(v.strip().lower() for v in re.split(r"[,;\s]+", grezzo) if "@" in v)


def blocked(message: dict, consentiti: frozenset[str] | None) -> bool:
    """Questo messaggio di marketing va verso un indirizzo fuori elenco?"""
    if consentiti is None or message.get("communication_type") != "marketing":
        return False
    return (message.get("destination_snapshot") or "").strip().lower() not in consentiti


__all__ = ["ENV", "REASON", "allowed", "blocked"]
