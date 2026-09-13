"""P27-6 - le righe di `network_territory_aliases`. Solo SQL, nessuna decisione.

Stessa forma dei repository di P27-2/3/4/5: funzioni che ricevono un cursore,
non lo aprono e non lo chiudono, e non decidono niente. Chi decide e' il
service, che tiene la transazione e l'audit.

NESSUNA `DELETE` IN QUESTO FILE, ed e' una scelta. Un alias revocato resta:
dice che quel nome ha significato qualcosa, e quando. Cancellarlo renderebbe
indistinguibile "non e' mai stato dichiarato" da "e' stato dichiarato e poi
tolto" - la stessa ragione per cui `agency_territory_assignments` conserva le
revocate.
"""

from __future__ import annotations

from typing import Any

from .enums import ALIAS_ACTIVE

#: Le colonne che una route puo' restituire. `updated_at` c'e' perche' e'
#: l'unico istante che dice quando un alias e' stato revocato: la 059 non ha un
#: `ended_at`, per la stessa ragione della 058 - due sorgenti per un solo fatto
#: si contraddicono alla prima divergenza.
ALIAS_FIELDS = (
    "id",
    "territory_id",
    "source",
    "match_value",
    "status",
    "created_at",
    "updated_at",
)

_SELECT = ", ".join(f"a.{c}" for c in ALIAS_FIELDS)

#: LA NORMALIZZAZIONE, IDENTICA a quella dell'indice unico della 059 e a quella
#: di `network_routing.repository.NORMALISED`. Tre copie della stessa
#: espressione e un test che le confronta: se divergessero, il controllo di
#: esistenza qui direbbe "libero" su un valore che l'indice rifiuta, e
#: l'amministratore riceverebbe un 500 al posto di un 409.
NORMALISED = "lower(btrim(regexp_replace({}, '\\s+', ' ', 'g')))"


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_alias(cur, alias_id: int) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT {_SELECT} FROM network_territory_aliases a WHERE a.id = %s",
        (alias_id,),
    )
    return _row(cur.fetchone())


def active_alias_for_value(cur, *, source: str, match_value: str) -> dict[str, Any] | None:
    """L'alias ATTIVO che gia' dichiara questo valore, se c'e'.

    Il confronto e' sul valore normalizzato da entrambe le parti: e' la stessa
    domanda che l'indice unico pone, posta prima perche' un 409 spiegato e'
    piu' utile di una violazione di vincolo tradotta.
    """
    cur.execute(
        f"""
        SELECT {_SELECT}
          FROM network_territory_aliases a
         WHERE a.source = %s
           AND a.status = %s
           AND {NORMALISED.format('a.match_value')} = {NORMALISED.format('%s')}
        """,
        (source, ALIAS_ACTIVE, match_value),
    )
    return _row(cur.fetchone())


def create_alias(
    cur, *, territory_id: int, source: str, match_value: str
) -> dict[str, Any]:
    """Inserisce un alias attivo. Keyword-only: tre stringhe e due interi.

    Posizionale, `source` e `match_value` si scambierebbero senza che nulla se
    ne accorga - il CHECK su `source` intercetterebbe solo meta' degli scambi.
    """
    cur.execute(
        f"""
        INSERT INTO network_territory_aliases (territory_id, source, match_value)
        VALUES (%s, %s, %s)
        RETURNING {", ".join(ALIAS_FIELDS)}
        """,
        (territory_id, source, match_value),
    )
    return dict(cur.fetchone())


def update_alias(
    cur, alias_id: int, *, territory_id: int | None = None, status: str | None = None
) -> dict[str, Any] | None:
    """Ripunta l'alias, lo revoca, o entrambi. `updated_at` sempre.

    Nessun campo da aggiornare non e' un errore da sollevare qui: il service
    rifiuta gia' una richiesta vuota, e un repository che inventasse una
    UPDATE senza SET produrrebbe SQL non valido.
    """
    assegnazioni = ["updated_at = NOW()"]
    valori: list[Any] = []
    if territory_id is not None:
        assegnazioni.append("territory_id = %s")
        valori.append(territory_id)
    if status is not None:
        assegnazioni.append("status = %s")
        valori.append(status)
    cur.execute(
        f"""
        UPDATE network_territory_aliases a
           SET {", ".join(assegnazioni)}
         WHERE a.id = %s
        RETURNING {", ".join(ALIAS_FIELDS)}
        """,
        (*valori, alias_id),
    )
    return _row(cur.fetchone())


def list_territory_aliases(cur, territory_id: int) -> list[dict[str, Any]]:
    """Gli alias di un territorio, attivi e revocati.

    Ordinati per stato e poi per valore: chi amministra guarda prima quelli che
    contano. L'id in coda rende l'ordine totale, cosi' due righe che si
    somigliano non si scambiano di posto fra due letture.
    """
    cur.execute(
        f"""
        SELECT {_SELECT}
          FROM network_territory_aliases a
         WHERE a.territory_id = %s
         ORDER BY a.status, a.match_value, a.id
        """,
        (territory_id,),
    )
    return [dict(r) for r in cur.fetchall()]
