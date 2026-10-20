"""Fase B - il PREFILTRO del consenso per la platea del follow-up.

La decisione resta UNA e sta in `guard._decidi`: questo modulo non decide
niente. Serve al motore delle comunicazioni per non leggere migliaia di
contatti che la guardia rifiuterebbe comunque: un frammento SQL sulla
proiezione (063) che tiene solo chi RISULTA concesso e non revocato, e che
la guardia poi conferma o smentisce riga per riga (evento piu' recente,
coerenza, opt-out di piattaforma).

Vive qui, nel pacchetto del consenso, perche' nomina le colonne della
proiezione: il dominio delle comunicazioni non le conosce per progetto
(sentinella S2 di P29-2.4) e riceve solo il testo del predicato.
"""
from __future__ import annotations

from .enums import PROJECTION_COLUMNS, PURPOSE_MARKETING

_COLONNE = PROJECTION_COLUMNS[PURPOSE_MARKETING]


def marketing_prefilter_sql(alias: str) -> str:
    """`(alias.<flag> IS TRUE AND alias.<revoked_at> IS NULL)`."""
    return f"({alias}.{_COLONNE['flag']} IS TRUE AND {alias}.{_COLONNE['revoked_at']} IS NULL)"


def marketing_granted_at_sql(alias: str) -> str:
    """L'istante della concessione proiettata, per ordinare i candidati."""
    return f"{alias}.{_COLONNE['granted_at']}"
