"""P27-6 - dichiarare quali valori in ingresso appartengono a un territorio.

PERCHE' QUESTA SUPERFICIE ESISTE

P27-5 ha separato `label` e `canonical_key` di proposito, e i suoi test lo
asseriscono. Nessun vincolo obbliga la `canonical_key` di un comune a essere lo
slug della sua etichetta: un territorio con chiave '067001' ed etichetta 'Alba
Adriatica' e' perfettamente legale. Quindi il routing NON puo' dedurre quale
territorio corrisponda al testo che arriva dal funnel - deve leggerlo, e
qualcuno deve averglielo detto.

Queste tre operazioni sono quel "qualcuno". Non toccano la creazione di un
territorio (P27-5 resta invariata) e non derivano niente dalla `label`.

COSA NON FA, PER DECISIONE

Nessuna DELETE. Un alias si revoca: la riga resta e dice che quel nome ha
significato qualcosa, e fino a quando. E' la stessa scelta di
`agency_territory_assignments`, per la stessa ragione - cancellare rende
indistinguibile "mai dichiarato" da "dichiarato e poi tolto".

Nessun alias "riattivabile". `revoked` e' finale, come per le assegnazioni:
riattivare una riga vecchia cancellerebbe il periodo in cui il nome non valeva.
Si dichiara un alias nuovo.
"""

from __future__ import annotations

from typing import Any

from psycopg2 import errors

from operator_auth.context import OperatorContext

from . import aliases_repository, territories_repository
from .transaction import audit_then_commit
from .database import platform_operation_cursor
from .enums import (
    ACTION_TERRITORY_ALIAS_CREATE,
    ACTION_TERRITORY_ALIAS_UPDATE,
    ALIAS_EXISTS_MESSAGE,
    ALIAS_KIND_MESSAGE,
    ALIAS_NOT_FOUND_MESSAGE,
    ALIAS_REVOKED,
    ALIAS_REVOKED_IS_FINAL_MESSAGE,
    ALIAS_TERRITORY_KIND,
    TARGET_TYPE_TERRITORY_ALIAS,
    TERRITORY_NOT_FOUND_MESSAGE,
)
from .exceptions import AliasKindRefused, AliasNotFound, PlatformConflict, TerritoryNotFound


def _require_territory_municipality(cur, territory_id: int) -> dict[str, Any]:
    """Il territorio deve esistere ED essere un comune.

    IL `kind` E' UN VINCOLO DI DOMINIO, non un dettaglio. `stime.comune` e' un
    comune: un alias appeso a una provincia instraderebbe un'intera provincia a
    chi presidia un paese, e chi lo ha dichiarato non se ne accorgerebbe mai -
    il lead arriverebbe, semplicemente a chi non gli compete.

    La query di routing filtra comunque su `kind = 'municipality'`: e' la
    difesa vera, perche' vale anche su una riga scritta da SQL saltando questo
    servizio. Questo controllo qui esiste perche' un 422 che dice quale campo
    e' sbagliato vale piu' di un alias che non instrada mai e non dice perche'.
    """
    territory = territories_repository.get_territory(cur, territory_id)
    if territory is None:
        raise TerritoryNotFound(TERRITORY_NOT_FOUND_MESSAGE)
    if territory["kind"] != ALIAS_TERRITORY_KIND:
        raise AliasKindRefused(ALIAS_KIND_MESSAGE)
    return territory


def list_territory_aliases(actor: OperatorContext, territory_id: int) -> list[dict]:
    """Gli alias di un territorio. Non audita: e' una lettura.

    L'ammissione di P27-1 registra gia' chi e' entrato e su quale route, e per
    una lettura quello E' la traccia.
    """
    with platform_operation_cursor() as (_conn, cur):
        _require_territory_municipality(cur, territory_id)
        return aliases_repository.list_territory_aliases(cur, territory_id)


def create_alias(
    actor: OperatorContext,
    territory_id: int,
    *,
    source: str,
    match_value: str,
    created_fields: list[str],
) -> dict[str, Any]:
    """Dichiara che un valore in ingresso appartiene a questo territorio.

    Il conflitto viene chiesto PRIMA e catturato DOPO. Prima, perche' "quel
    nome e' gia' di un altro territorio" e' un'informazione e merita un 409
    scritto; dopo, perche' fra il controllo e la INSERT c'e' una finestra e chi
    perde la corsa deve ricevere lo stesso 409, non un 500 con dentro il nome
    dell'indice. E' la forma che P27-5 usa per le assegnazioni.
    """
    with platform_operation_cursor() as (conn, cur):
        _require_territory_municipality(cur, territory_id)

        esistente = aliases_repository.active_alias_for_value(
            cur, source=source, match_value=match_value
        )
        if esistente is not None:
            raise PlatformConflict(ALIAS_EXISTS_MESSAGE)

        try:
            alias = aliases_repository.create_alias(
                cur,
                territory_id=territory_id,
                source=source,
                match_value=match_value,
            )
        except errors.UniqueViolation as exc:
            raise PlatformConflict(ALIAS_EXISTS_MESSAGE) from exc

        audit_then_commit(
            conn,
            actor,
            action=ACTION_TERRITORY_ALIAS_CREATE,
            target_type=TARGET_TYPE_TERRITORY_ALIAS,
            target_id=alias["id"],
            # `None`, come per `create_territory` e per la stessa ragione: un
            # alias dichiara a quale TERRITORIO appartiene un nome, non a quale
            # agenzia. Chi presidia quel territorio oggi e' un fatto separato,
            # che puo' cambiare domani senza che questo atto cambi - scriverlo
            # qui lo congelerebbe dentro una riga di registro che descrive
            # tutt'altro.
            target_agency_id=None,
            metadata={
                "territory_id": territory_id,
                "source": source,
                "created_fields": sorted(created_fields),
            },
        )
        return alias


def update_alias(
    actor: OperatorContext,
    alias_id: int,
    *,
    territory_id: int | None = None,
    status: str | None = None,
    updated_fields: list[str],
) -> dict[str, Any]:
    """Ripunta un alias a un altro territorio, oppure lo revoca.

    Le due cose insieme sono ammesse e significano una cosa sola: "questo nome
    non e' piu' di nessuno, e non lo era di chi credevi". Separarle
    obbligherebbe a due richieste e a due righe di audit per un unico atto.

    `revoked` e' FINALE. Riattivare una riga revocata cancellerebbe il periodo
    in cui quel nome non valeva - e in quel periodo i lead sono andati altrove,
    il che e' precisamente la cosa che l'audit serve a poter ricostruire.
    """
    # "Almeno un campo" lo rifiuta `TerritoryAliasUpdateRequest`: e' una
    # richiesta malformata, non un fatto di dominio, e il 422 deve nominare il
    # campo. Ripeterlo qui darebbe due messaggi diversi per lo stesso errore.

    with platform_operation_cursor() as (conn, cur):
        corrente = aliases_repository.get_alias(cur, alias_id)
        if corrente is None:
            raise AliasNotFound(ALIAS_NOT_FOUND_MESSAGE)
        if corrente["status"] == ALIAS_REVOKED:
            raise PlatformConflict(ALIAS_REVOKED_IS_FINAL_MESSAGE)

        if territory_id is not None:
            _require_territory_municipality(cur, territory_id)

        try:
            alias = aliases_repository.update_alias(
                cur, alias_id, territory_id=territory_id, status=status
            )
        except errors.UniqueViolation as exc:
            # Ripuntare non puo' violare l'unicita' - la chiave e'
            # (source, valore normalizzato) e nessuno dei due cambia qui - ma
            # il driver ha l'ultima parola e un 500 non e' una risposta.
            raise PlatformConflict(ALIAS_EXISTS_MESSAGE) from exc

        audit_then_commit(
            conn,
            actor,
            action=ACTION_TERRITORY_ALIAS_UPDATE,
            target_type=TARGET_TYPE_TERRITORY_ALIAS,
            target_id=alias_id,
            target_agency_id=None,
            metadata={
                "from_territory_id": corrente["territory_id"],
                "from_status": corrente["status"],
                "updated_fields": sorted(updated_fields),
            },
        )
        return alias
