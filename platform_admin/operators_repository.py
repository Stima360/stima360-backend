"""SQL per `operator_users` e `agency_memberships`. P27-3.

Stesse regole di `agencies_repository`: cursore ricevuto e mai aperto, nessuna
HTTPException, nessun audit, nessuna decisione. Il service decide, questo
modulo esegue.

DUE TABELLE IN UN MODULO SOLO, DI PROPOSITO.

Una persona e la sua appartenenza a un'agenzia sono due righe che P27-3 scrive
quasi sempre insieme e nella stessa transazione: creare un operatore senza
membership lascerebbe un conto che non appartiene a nessuno, e creare una
membership senza operatore e' impossibile. Separarle in due file avrebbe
suggerito due confini transazionali dove ce n'e' uno.

COSA QUESTO MODULO NON FA, E CHI LO FA GIA'

* Non normalizza email: lo fa `core.normalization.normalize_email`, che e' la
  stessa funzione che CORE usa sui contatti. Una seconda normalizzazione
  significherebbe due idee di quando due indirizzi sono lo stesso indirizzo, e
  `operator_users.email_normalized` e' UNIQUE GLOBALE proprio su quell'idea.
* Non calcola hash: lo fa `operator_auth.security.hash_password`, che produce
  il formato che il CHECK `operator_users_hash_chk` impone. Il service passa
  qui un hash gia' fatto, e una password in chiaro non entra mai in questo
  file.
* Non cancella nulla. `revoked` e `disabled` sono stati, non DELETE: la
  relazione fra una persona e un'agenzia e' un fatto storico.
"""
from __future__ import annotations

from typing import Any

# Le colonne di identita' che una risposta puo' contenere. `password_hash` non
# c'e', e non e' una svista da correggere aggiungendolo: e' il motivo per cui
# questa tupla esiste invece di un `SELECT *`.
OPERATOR_COLUMNS = (
    "id",
    "email",
    "first_name",
    "last_name",
    "status",
    "is_platform_admin",
    "last_login_at",
    "created_at",
    "updated_at",
)

MEMBERSHIP_COLUMNS = (
    "id",
    "agency_id",
    "operator_user_id",
    "role",
    "status",
    "created_at",
    "updated_at",
)

# I campi di identita' che una PATCH puo' scrivere.
#
# `email` e `email_normalized` non ci sono: sono la chiave di identita' globale
# (UNIQUE `operator_users_email_unq`) su cui il login risolve la persona, e
# cambiarla e' un'operazione di identita', non una modifica anagrafica. Stessa
# ragione per cui P27-2 ha reso immutabile lo slug dell'agenzia.
#
# `is_platform_admin` non c'e': concedere l'amministrazione della rete da una
# route che si chiama "aggiorna operatore" e' un'escalation nascosta dentro una
# modifica di routine. Se servira', sara' un'operazione sua.
OPERATOR_UPDATABLE_COLUMNS = ("first_name", "last_name", "status")

# I campi di membership che una PATCH puo' scrivere.
MEMBERSHIP_UPDATABLE_COLUMNS = ("role", "status")

_OPERATOR_SELECT = ", ".join(f"u.{c}" for c in OPERATOR_COLUMNS)
_MEMBERSHIP_SELECT = ", ".join(f"m.{c}" for c in MEMBERSHIP_COLUMNS)


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Letture
# ---------------------------------------------------------------------------

def get_operator(cur, operator_user_id: int) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT {_OPERATOR_SELECT} FROM operator_users u WHERE u.id = %s",
        (operator_user_id,),
    )
    return _row(cur.fetchone())


def find_operator_by_normalized_email(cur, email_normalized: str) -> dict[str, Any] | None:
    """L'operatore con quell'email, o None. La chiave e' GLOBALE.

    Non c'e' un `agency_id` in questa firma e non deve essercene uno: due
    agenzie non possono avere due persone diverse con la stessa email, perche'
    `operator_users_email_unq` e' su tutta la tabella. E' cio' che rende
    deterministico il caso "email gia' esistente" nella creazione.
    """
    cur.execute(
        f"SELECT {_OPERATOR_SELECT} FROM operator_users u WHERE u.email_normalized = %s",
        (email_normalized,),
    )
    return _row(cur.fetchone())


def list_memberships_of_operator(cur, operator_user_id: int) -> list[dict[str, Any]]:
    """TUTTE le membership di un operatore, in ogni stato.

    Anche quelle revocate: sono la sua storia nella rete, ed e' esattamente
    cio' che un amministratore guarda prima di riassegnarlo.
    """
    cur.execute(
        f"SELECT {_MEMBERSHIP_SELECT} FROM agency_memberships m "
        "WHERE m.operator_user_id = %s ORDER BY m.id",
        (operator_user_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def get_membership(cur, agency_id: int, operator_user_id: int) -> dict[str, Any] | None:
    """La membership fra quell'operatore e quell'agenzia, in QUALUNQUE stato.

    Non filtra su `status='active'` - a differenza di
    `operator_auth.repository.membership_exists`, che risponde a una domanda
    diversa ("puo' operare adesso?"). Qui la domanda e' "esiste una riga?", e
    la risposta governa il vincolo UNIQUE (agency_id, operator_user_id): una
    riga revocata occupa comunque quella coppia.
    """
    cur.execute(
        f"SELECT {_MEMBERSHIP_SELECT} FROM agency_memberships m "
        "WHERE m.agency_id = %s AND m.operator_user_id = %s",
        (agency_id, operator_user_id),
    )
    return _row(cur.fetchone())


def get_membership_by_id(cur, membership_id: int) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT {_MEMBERSHIP_SELECT} FROM agency_memberships m WHERE m.id = %s",
        (membership_id,),
    )
    return _row(cur.fetchone())


def active_membership_elsewhere(
    cur, operator_user_id: int, *, excluding_agency_id: int
) -> dict[str, Any] | None:
    """La membership ATTIVA dell'operatore in un'altra agenzia, se c'e'.

    E' il controllo applicativo che rende leggibile il vincolo
    `uq_agency_memberships_single_active`: senza, la violazione arriverebbe dal
    database come un errore di indice parziale, e il chiamante leggerebbe il
    nome dell'indice invece del motivo.

    Il vincolo resta comunque l'autorita': fra questa lettura e la scrittura
    c'e' una finestra, e la corsa la chiude PostgreSQL. Vedi il service.
    """
    cur.execute(
        f"SELECT {_MEMBERSHIP_SELECT} FROM agency_memberships m "
        "WHERE m.operator_user_id = %s AND m.status = 'active' "
        "AND m.agency_id <> %s LIMIT 1",
        (operator_user_id, excluding_agency_id),
    )
    return _row(cur.fetchone())


def list_agency_operators(cur, agency_id: int) -> list[dict[str, Any]]:
    """Gli operatori di un'agenzia, ciascuno con la sua membership LI'.

    Una JOIN e non due letture: il chiamante vuole coppie identita'/membership,
    e comporle in Python da due elenchi significherebbe scegliere cosa fare
    quando non combaciano - una domanda che la JOIN non pone.

    `u.id` NON e' nella proiezione, ed e' deliberato. Le due tabelle hanno
    entrambe una colonna `id`, e un RealDictCursor che ne riceve due con lo
    stesso nome tiene l'ultima: la riga direbbe che l'operatore ha l'id della
    sua membership, in silenzio e in modo plausibile. L'identita' arriva quindi
    da `m.operator_user_id`, che e' lo stesso valore per definizione della JOIN
    e non collide con niente.

    Include ogni stato di membership, revocate comprese. Chi guarda l'organico
    di un'agenzia deve vedere anche chi ne e' uscito; filtrare qui renderebbe
    invisibile la differenza fra "non c'e' mai stato" e "non c'e' piu'".
    """
    identity = ", ".join(f"u.{c}" for c in OPERATOR_COLUMNS if c != "id")
    cur.execute(
        f"""
        SELECT {identity}, {_MEMBERSHIP_SELECT}
          FROM agency_memberships m
          JOIN operator_users u ON u.id = m.operator_user_id
         WHERE m.agency_id = %s
         ORDER BY m.id
        """,
        (agency_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def active_owner_of(cur, agency_id: int) -> dict[str, Any] | None:
    """Il titolare attivo dell'agenzia, se c'e'.

    Al massimo una riga per costruzione: `uq_agency_memberships_single_owner`
    e' un indice unico parziale su (agency_id) dove role='agency_owner' e
    status='active'.
    """
    cur.execute(
        f"SELECT {_MEMBERSHIP_SELECT} FROM agency_memberships m "
        "WHERE m.agency_id = %s AND m.role = 'agency_owner' "
        "AND m.status = 'active'",
        (agency_id,),
    )
    return _row(cur.fetchone())


# ---------------------------------------------------------------------------
# Scritture
# ---------------------------------------------------------------------------

def create_operator(
    cur,
    *,
    email: str,
    email_normalized: str,
    password_hash: str,
    first_name: str | None,
    last_name: str | None,
    status: str,
) -> dict[str, Any]:
    """Inserisce un operatore e restituisce la sua identita'.

    `password_hash` arriva gia' calcolato da `operator_auth.security`. Questo
    modulo non vede mai una password in chiaro, e il RETURNING non riporta
    l'hash: la riga che esce di qui e' la stessa proiezione che una lettura
    produce, quindi non esiste un percorso in cui l'hash raggiunga un chiamante
    perche' "era gia' li'".
    """
    cur.execute(
        f"""
        INSERT INTO operator_users (
            email, email_normalized, password_hash, first_name, last_name, status
        ) VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING {", ".join(OPERATOR_COLUMNS)}
        """,
        (email, email_normalized, password_hash, first_name, last_name, status),
    )
    return dict(cur.fetchone())


def create_membership(
    cur, *, agency_id: int, operator_user_id: int, role: str, status: str
) -> dict[str, Any]:
    cur.execute(
        f"""
        INSERT INTO agency_memberships (agency_id, operator_user_id, role, status)
        VALUES (%s, %s, %s, %s)
        RETURNING {", ".join(MEMBERSHIP_COLUMNS)}
        """,
        (agency_id, operator_user_id, role, status),
    )
    return dict(cur.fetchone())


def update_operator(cur, operator_user_id: int, fields: dict[str, Any]):
    """Aggiorna SOLO i campi presenti. None se l'id non esiste."""
    _reject_unknown(fields, OPERATOR_UPDATABLE_COLUMNS, "operator_users")
    assignments = [f"{c} = %s" for c in OPERATOR_UPDATABLE_COLUMNS if c in fields]
    params = [fields[c] for c in OPERATOR_UPDATABLE_COLUMNS if c in fields]
    cur.execute(
        f"""
        UPDATE operator_users
           SET {', '.join(assignments)}, updated_at = NOW()
         WHERE id = %s
        RETURNING {", ".join(OPERATOR_COLUMNS)}
        """,
        params + [operator_user_id],
    )
    return _row(cur.fetchone())


def update_membership(cur, membership_id: int, fields: dict[str, Any]):
    """Aggiorna SOLO i campi presenti. None se l'id non esiste."""
    _reject_unknown(fields, MEMBERSHIP_UPDATABLE_COLUMNS, "agency_memberships")
    assignments = [f"{c} = %s" for c in MEMBERSHIP_UPDATABLE_COLUMNS if c in fields]
    params = [fields[c] for c in MEMBERSHIP_UPDATABLE_COLUMNS if c in fields]
    cur.execute(
        f"""
        UPDATE agency_memberships
           SET {', '.join(assignments)}, updated_at = NOW()
         WHERE id = %s
        RETURNING {", ".join(MEMBERSHIP_COLUMNS)}
        """,
        params + [membership_id],
    )
    return _row(cur.fetchone())


def demote_active_owner(cur, agency_id: int, *, to_role: str) -> dict[str, Any] | None:
    """Toglie il ruolo di titolare a chi ce l'ha, se c'e'. None se non c'era.

    PRIMA META' DEL TRASFERIMENTO, E L'ORDINE NON E' NEGOZIABILE.

    `uq_agency_memberships_single_owner` e' un indice unico parziale, verificato
    a ogni istruzione e non a fine transazione. Promuovere B prima di degradare
    A lo violerebbe subito, dentro la transazione, e nessun riordino successivo
    potrebbe rimediarlo.

    Degradando per primo, la sequenza attraversa uno stato con ZERO titolari -
    che l'indice ammette - e non uno con due, che non ammette. Quello stato non
    viene mai committato: le due istruzioni stanno nella stessa transazione, e
    chi legge da fuori vede l'agenzia passare da A a B senza passi intermedi.

    Non revoca A e non lo sospende: lo riporta a `to_role`, che il service
    fissa a `agency_admin`. Un titolare sostituito resta una persona che lavora
    li'.
    """
    cur.execute(
        f"""
        UPDATE agency_memberships
           SET role = %s, updated_at = NOW()
         WHERE agency_id = %s AND role = 'agency_owner' AND status = 'active'
        RETURNING {", ".join(MEMBERSHIP_COLUMNS)}
        """,
        (to_role, agency_id),
    )
    return _row(cur.fetchone())


def _reject_unknown(fields: dict[str, Any], allowed: tuple[str, ...], table: str) -> None:
    """Rumorosamente, non ignorando.

    Un campo scartato in silenzio produce una PATCH che risponde 200 e non ha
    fatto quel che le e' stato chiesto - e su `is_platform_admin` o su `email`,
    che sono deliberatamente fuori da `allowed`, sarebbe il modo peggiore di
    far fallire una richiesta.
    """
    unknown = [key for key in fields if key not in allowed]
    if unknown:
        raise ValueError(f"colonne non aggiornabili su {table}: {sorted(unknown)}")
    if not fields:
        raise ValueError(f"un aggiornamento su {table} richiede almeno un campo")
