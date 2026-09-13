"""SQL per `network_territories` e `agency_territory_assignments`. P27-5.

Le stesse regole degli altri repository di questo package, tutte verificate
strutturalmente dai test:

* riceve un cursore GIA' APERTO e non ne apre mai uno. Il confine
  transazionale lo decide il service, che e' l'unico posto in cui l'ordine
  "scrivi, audita, committa" puo' essere espresso;
* nessuna HTTPException e nessun import di framework web;
* nessuna chiamata all'audit. Un repository che audita da solo produce una
  riga anche quando il chiamante poi annulla tutto;
* nessuna decisione: `update_assignment` non sceglie cosa aggiornare, riceve i
  campi gia' decisi dal service.

NESSUNA DELETE, IN NESSUNA DELLE DUE TABELLE.

Non e' una dimenticanza da colmare piu' avanti. Un territorio revocato e' un
fatto storico - un affiliato lo ha presidiato per un periodo - e cancellarlo
toglierebbe la differenza fra "non ce l'ha mai avuto" e "non ce l'ha piu'".
Gli stati `suspended` e `revoked` sono il modo in cui la rete smette di usare
un'assegnazione, ed e' un modo che lascia la riga leggibile per sempre.
"""
from __future__ import annotations

from typing import Any

# Le colonne che una risposta puo' contenere, in un posto solo.
#
# Elencate e non `SELECT *`: una colonna aggiunta domani a una di queste
# tabelle non deve poter raggiungere un client per il solo fatto di esistere.
# E' la stessa disciplina di `agencies_repository.AGENCY_COLUMNS`.
TERRITORY_COLUMNS = (
    "id",
    "kind",
    "canonical_key",
    "label",
    "created_at",
    "updated_at",
)

ASSIGNMENT_COLUMNS = (
    "id",
    "territory_id",
    "agency_id",
    "status",
    "created_at",
    "updated_at",
)

# I campi che una UPDATE puo' scrivere su un'assegnazione. UNO SOLO.
#
# `territory_id` e `agency_id` sono deliberatamente assenti. Cambiare
# `agency_id` con una PATCH SAREBBE un trasferimento, eseguito da una route che
# dice di aggiornare uno stato: il titolare precedente perderebbe il territorio
# senza che il registro riporti un `platform.territory.transfer`, e senza che
# la riga della sua assegnazione dica mai di essere finita. Il trasferimento ha
# il suo endpoint e il suo atto, ed e' l'unico posto in cui `agency_id` cambia
# - anzi non cambia nemmeno li': nasce una riga nuova.
#
# `territory_id` non e' aggiornabile per la stessa ragione al contrario:
# spostarlo trasformerebbe la storia di un posto nella storia di un altro.
ASSIGNMENT_UPDATABLE_COLUMNS = ("status",)

_SELECT_TERRITORY = ", ".join(TERRITORY_COLUMNS)
_SELECT_ASSIGNMENT = ", ".join(ASSIGNMENT_COLUMNS)

# Le colonne dell'assegnazione qualificate, per le query che fanno JOIN: senza
# il prefisso, `id`, `created_at` e `updated_at` sono ambigui.
_SELECT_ASSIGNMENT_A = ", ".join(f"a.{c}" for c in ASSIGNMENT_COLUMNS)


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# network_territories
# ---------------------------------------------------------------------------

def get_territory(cur, territory_id: int) -> dict[str, Any] | None:
    """Un territorio, o None. Il 404 lo decide il service."""
    cur.execute(
        f"SELECT {_SELECT_TERRITORY} FROM network_territories WHERE id = %s",
        (territory_id,),
    )
    return _row(cur.fetchone())


def territory_identity_exists(cur, *, kind: str, canonical_key: str) -> bool:
    """True se quella identita' e' gia' di un territorio. Un solo chiamante:
    la creazione.

    La coppia e non la sola chiave: e' l'identita' come la definisce
    `network_territories_identity_unq` nella 058, e un controllo piu' largo
    qui produrrebbe un 409 su una INSERT che il database avrebbe accettato.

    `label` non compare, ed e' il motivo per cui due territori possono
    chiamarsi uguale senza essere lo stesso territorio - e per cui correggere
    un'etichetta non ne crea mai uno nuovo.
    """
    cur.execute(
        """
        SELECT 1 FROM network_territories
         WHERE kind = %s AND canonical_key = %s
        """,
        (kind, canonical_key),
    )
    return cur.fetchone() is not None


def create_territory(
    cur, *, kind: str, canonical_key: str, label: str
) -> dict[str, Any]:
    """Inserisce un territorio e restituisce la riga completa.

    Keyword-only: `kind`, `canonical_key` e `label` sono tre stringhe
    adiacenti, e in posizionale uno scambio produrrebbe una riga che i CHECK
    intercettano solo a meta' - `kind` fallirebbe, `canonical_key` e `label`
    scambiati no, e il risultato sarebbe un territorio la cui identita' e' la
    sua etichetta.

    NON committa. Vedi `platform_admin/database.platform_operation_cursor`.
    """
    cur.execute(
        f"""
        INSERT INTO network_territories (kind, canonical_key, label)
        VALUES (%s, %s, %s)
        RETURNING {_SELECT_TERRITORY}
        """,
        (kind, canonical_key, label),
    )
    return dict(cur.fetchone())


def list_territories(
    cur,
    *,
    kind: str | None = None,
    agency_id: int | None = None,
    assignment_status: str | None = None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """I territori della rete, con l'assegnazione ATTIVA di ciascuno.

    `active_agency_id` e `active_assignment_id` sono NULL per un territorio che
    nessuno presidia. E' la lettura che serve davvero - "chi ha cosa" - e
    ottenerla altrimenti significherebbe una query per territorio.

    Il join e' filtrato su `status='active'` DENTRO la ON e non in una WHERE:
    in una WHERE trasformerebbe il LEFT JOIN in un INNER, e i territori liberi
    - quelli che si va a cercare quando si apre un affiliato nuovo -
    sparirebbero dall'elenco.

    Regge perche' `uq_agency_territory_single_active` garantisce al massimo una
    riga attiva per territorio: senza quel vincolo questo join duplicherebbe il
    territorio una volta per assegnazione.

    I FILTRI SONO COMPOSTI DA UNA WHITELIST, NON DAL CHIAMANTE.

    Ogni frammento e' un letterale di questo modulo e ogni valore passa da un
    segnaposto. Nessuna stringa che arriva da una richiesta entra in
    un'istruzione.

    `limit` e `offset` sono obbligatori e keyword-only: un default qui sarebbe
    il posto in cui la paginazione smette di essere applicata il giorno in cui
    qualcuno chiama questa funzione senza passarli.
    """
    where: list[str] = []
    params: list[Any] = []

    if kind is not None:
        where.append("t.kind = %s")
        params.append(kind)

    # Il filtro per agenzia e per stato di assegnazione guarda TUTTE le
    # assegnazioni del territorio, non solo quella attiva: "quali territori ha
    # mai presidiato l'agenzia A" e "quali le sono stati revocati" sono
    # entrambe domande legittime, e con il solo join attivo la seconda non
    # avrebbe risposta.
    exists: list[str] = []
    if agency_id is not None:
        exists.append("x.agency_id = %s")
    if assignment_status is not None:
        exists.append("x.status = %s")
    if exists:
        where.append(
            "EXISTS (SELECT 1 FROM agency_territory_assignments x "
            f"WHERE x.territory_id = t.id AND {' AND '.join(exists)})"
        )
        if agency_id is not None:
            params.append(agency_id)
        if assignment_status is not None:
            params.append(assignment_status)

    clause = f"WHERE {' AND '.join(where)}" if where else ""

    cur.execute(
        f"""
        SELECT {', '.join(f't.{c}' for c in TERRITORY_COLUMNS)},
               a.agency_id AS active_agency_id,
               a.id        AS active_assignment_id
          FROM network_territories t
          LEFT JOIN agency_territory_assignments a
                 ON a.territory_id = t.id
                AND a.status = 'active'
          {clause}
         ORDER BY t.kind, t.canonical_key, t.id
         LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# agency_territory_assignments
# ---------------------------------------------------------------------------

def get_active_assignment(cur, territory_id: int) -> dict[str, Any] | None:
    """L'assegnazione attiva del territorio, o None.

    Restituisce una riga sola senza LIMIT e senza ordinamento perche'
    `uq_agency_territory_single_active` garantisce che ce ne sia al massimo
    una. Un `LIMIT 1` qui nasconderebbe la rottura di quel vincolo invece di
    farla emergere.
    """
    cur.execute(
        f"""
        SELECT {_SELECT_ASSIGNMENT} FROM agency_territory_assignments
         WHERE territory_id = %s AND status = 'active'
        """,
        (territory_id,),
    )
    return _row(cur.fetchone())


def get_assignment(cur, assignment_id: int, agency_id: int) -> dict[str, Any] | None:
    """Un'assegnazione DI QUELL'AGENZIA, o None.

    `agency_id` e' nella WHERE e non e' un controllo successivo: la route e'
    `/agencies/{agency_id}/territories/{assignment_id}`, e un'assegnazione di
    un'altra agenzia non e' amministrabile da li'. Filtrare qui significa che
    non esiste un percorso in cui la riga sbagliata viene letta e poi scartata
    - il caso in cui il controllo successivo si dimentica.
    """
    cur.execute(
        f"""
        SELECT {_SELECT_ASSIGNMENT} FROM agency_territory_assignments
         WHERE id = %s AND agency_id = %s
        """,
        (assignment_id, agency_id),
    )
    return _row(cur.fetchone())


def list_agency_assignments(
    cur,
    agency_id: int,
    *,
    status: str | None = None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Le assegnazioni di un'agenzia, con il territorio di ciascuna.

    INNER JOIN e non LEFT: una assegnazione senza territorio non puo' esistere
    - `territory_id` e' NOT NULL con una chiave esterna - quindi un LEFT
    prometterebbe un caso che lo schema esclude.

    Ordine deterministico e stabile fra due pagine: `kind`, poi
    `canonical_key`, poi l'id dell'assegnazione. L'ultimo non e' decorativo -
    un'agenzia puo' avere piu' righe storiche sullo stesso territorio, e senza
    di esso due pagine consecutive potrebbero ripetere una riga e saltarne
    un'altra.
    """
    where = ["a.agency_id = %s"]
    params: list[Any] = [agency_id]

    if status is not None:
        where.append("a.status = %s")
        params.append(status)

    cur.execute(
        f"""
        SELECT {_SELECT_ASSIGNMENT_A},
               t.kind          AS territory_kind,
               t.canonical_key AS territory_canonical_key,
               t.label         AS territory_label
          FROM agency_territory_assignments a
          JOIN network_territories t ON t.id = a.territory_id
         WHERE {' AND '.join(where)}
         ORDER BY t.kind, t.canonical_key, a.id
         LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    return [dict(row) for row in cur.fetchall()]


def create_assignment(
    cur, *, territory_id: int, agency_id: int, status: str
) -> dict[str, Any]:
    """Inserisce un'assegnazione e restituisce la riga completa.

    Puo' sollevare `UniqueViolation` su `uq_agency_territory_single_active`, ed
    e' il punto: il vincolo e' cio' che chiude la corsa fra due richieste
    simultanee. Tradurla e' compito del service.

    NON committa.
    """
    cur.execute(
        f"""
        INSERT INTO agency_territory_assignments (territory_id, agency_id, status)
        VALUES (%s, %s, %s)
        RETURNING {_SELECT_ASSIGNMENT}
        """,
        (territory_id, agency_id, status),
    )
    return dict(cur.fetchone())


def update_assignment(
    cur, assignment_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    """Aggiorna SOLO i campi presenti in `fields`. None se l'id non esiste.

    `updated_at` e' scritto qui e non e' un campo che il chiamante possa
    fornire: e' la data della modifica, non un dato dell'assegnazione. La 058
    non installa un trigger che lo mantenga, quindi senza questa riga la
    colonna resterebbe alla data di creazione per sempre - e siccome non esiste
    un `ended_at`, e' l'unica cosa che dice QUANDO un'assegnazione ha smesso di
    essere attiva.

    Un nome fuori da `ASSIGNMENT_UPDATABLE_COLUMNS` e' un difetto del
    chiamante, non un input: viene rifiutato rumorosamente invece di essere
    ignorato in silenzio, perche' un campo scartato senza dirlo produce una
    PATCH che risponde 200 e non ha fatto nulla.
    """
    unknown = [key for key in fields if key not in ASSIGNMENT_UPDATABLE_COLUMNS]
    if unknown:
        raise ValueError(f"colonne non aggiornabili: {sorted(unknown)}")
    if not fields:
        raise ValueError("update_assignment richiede almeno un campo")

    assignments = []
    params: list[Any] = []
    for column in ASSIGNMENT_UPDATABLE_COLUMNS:
        if column not in fields:
            continue
        assignments.append(f"{column} = %s")
        params.append(fields[column])

    cur.execute(
        f"""
        UPDATE agency_territory_assignments
           SET {', '.join(assignments)}, updated_at = NOW()
         WHERE id = %s
        RETURNING {_SELECT_ASSIGNMENT}
        """,
        params + [assignment_id],
    )
    return _row(cur.fetchone())
