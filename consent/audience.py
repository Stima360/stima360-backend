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

from .enums import CHANNELS, PHONE_CHANNELS, PROJECTION_COLUMNS, PURPOSE_MARKETING

_COLONNE = PROJECTION_COLUMNS[PURPOSE_MARKETING]


def _canale(channel: str) -> str:
    if channel not in CHANNELS:
        raise ValueError(f"unknown consent channel {channel!r}")
    return channel


def channel_prefilter_sql(alias: str, channel: str) -> str:
    """109: `EXISTS` di una proiezione per canale concessa per `alias`.

    Il VECCHIO flag di consenso del contatto non compare: da C2 non e' piu' una
    autorizzazione per nessun canale, e un prefiltro che lo leggesse
    riporterebbe in platea i contatti storici senza consenso documentato.
    """
    return (f"EXISTS (SELECT 1 FROM contact_channel_consents cc "
            f"WHERE cc.contact_id = {alias}.id AND cc.channel = '{_canale(channel)}' "
            f"AND cc.status = 'granted')")


def channel_granted_at_sql(alias: str, channel: str) -> str:
    """L'istante della concessione sul canale (NULL se non concesso)."""
    return (f"(SELECT cc.granted_at FROM contact_channel_consents cc "
            f"WHERE cc.contact_id = {alias}.id AND cc.channel = '{_canale(channel)}' "
            f"AND cc.status = 'granted')")


def channel_address_sql(alias: str, channel: str) -> str:
    """L'indirizzo del contatto su cui si confronta l'opt-out di piattaforma."""
    colonna = "phone_normalized" if _canale(channel) in PHONE_CHANNELS else "email_normalized"
    return f"{alias}.{colonna}"


def channel_optout_after_grant_sql(alias: str, channel: str) -> str:
    """`EXISTS` di un opt-out di piattaforma su (canale, indirizzo) non
    precedente alla concessione sul canale."""
    return (f"EXISTS (SELECT 1 FROM communication_optouts o "
            f"WHERE o.channel = '{_canale(channel)}' "
            f"AND o.address_normalized = {channel_address_sql(alias, channel)} "
            f"AND o.opted_out_at >= COALESCE({channel_granted_at_sql(alias, channel)}, o.opted_out_at))")


def marketing_prefilter_sql(alias: str) -> str:
    """`(alias.<flag> IS TRUE AND alias.<revoked_at> IS NULL)`."""
    return f"({alias}.{_COLONNE['flag']} IS TRUE AND {alias}.{_COLONNE['revoked_at']} IS NULL)"


def marketing_granted_at_sql(alias: str) -> str:
    """L'istante della concessione proiettata, per ordinare i candidati."""
    return f"{alias}.{_COLONNE['granted_at']}"
