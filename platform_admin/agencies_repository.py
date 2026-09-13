"""SQL per la tabella `agencies`. P27-2.

SEPARATO DA `repository.py` DI PROPOSITO.

`platform_admin/repository.py` resta dedicato a UNA cosa: la INSERT su
`platform_audit_log`. Quel modulo non ha funzioni di lettura, di UPDATE o di
DELETE, e l'assenza e' una garanzia leggibile a colpo d'occhio - il registro e'
append-only anche a livello di applicazione, non solo di database. Mettere qui
dentro un `update_agency` costerebbe quella leggibilita' per sempre.

Le regole di questo modulo, tutte verificate strutturalmente dai test:

* riceve un cursore GIA' APERTO e non ne apre mai uno. Il confine
  transazionale lo decide il service, che e' l'unico posto in cui l'ordine
  "scrivi, audita, committa" puo' essere espresso;
* nessuna HTTPException e nessun import di framework web. La traduzione in uno
  status appartiene al router;
* nessuna chiamata all'audit. Un repository che audita da solo produce una
  riga anche quando il chiamante poi annulla tutto;
* nessuna decisione. `update_agency` non sceglie cosa aggiornare, riceve i
  campi gia' decisi dal service.

La tabella non e' creata qui e non e' modificata da P27-2: `agencies` esiste
dalla migration 027 con i suoi CHECK su nome, slug e stato, e P27-2 non ha
nessuna ragione tecnica di aggiungere una migration.
"""
from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

# Le colonne che una risposta puo' contenere, in un posto solo.
#
# Elencate e non `SELECT *`: una colonna aggiunta domani ad `agencies` - da
# P27-4, che su `settings` ci lavorera' davvero - non deve poter raggiungere un
# client per il solo fatto di esistere. E' la stessa disciplina con cui
# operator_auth costruisce `/me` campo per campo.
AGENCY_COLUMNS = (
    "id",
    "name",
    "slug",
    "status",
    "settings",
    "created_at",
    "updated_at",
)

_SELECT = ", ".join(AGENCY_COLUMNS)

# I campi che una UPDATE puo' scrivere, e come. `settings` passa da Json(...)
# perche' la colonna e' JSONB; gli altri due sono scalari.
#
# Questa tupla e' la sola definizione di quali nomi sono scrivibili: un nome
# che non compare qui non finisce in una istruzione, che e' la difesa
# strutturale contro un campo arrivato da un corpo di richiesta.
#
# `slug` E' DELIBERATAMENTE ASSENTE, E NON E' UNA DIMENTICANZA.
#
# La migration 027 lo definisce come chiave stabile ("slug is the stable key")
# e ne vincola la forma proprio perche' viene RISOLTO: il bridge del funnel
# pubblico trova la Default Agency per slug (`core.scope.resolve_default_agency_id`),
# e il backfill della 029 ci ha attaccato ogni record legacy. Rinominarlo da
# una PATCH romperebbe quei lookup lasciando intatta la riga - il tipo di
# guasto che non si vede finche' un'estimazione pubblica non finisce
# nell'agenzia sbagliata, o in nessuna.
#
# Uno slug si sceglie quindi alla creazione e non si cambia piu'. Se un giorno
# servira' davvero cambiarlo, sara' un'operazione sua, con i suoi lookup da
# aggiornare nello stesso atto - non un campo in piu' in questa tupla.
UPDATABLE_COLUMNS = ("name", "status", "settings")


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def list_agencies(cur) -> list[dict[str, Any]]:
    """Tutte le agenzie della rete, in ordine deterministico.

    Nessun filtro e nessuna paginazione: P27-2 consegna l'elenco, non una
    ricerca. Un ordinamento c'e' comunque, perche' un elenco che cambia ordine
    fra due chiamate e' illeggibile per una persona e instabile per un test.

    `ORDER BY id` e non per nome: e' l'ordine di apertura degli affiliati, che
    non cambia quando un'agenzia viene rinominata.
    """
    cur.execute(f"SELECT {_SELECT} FROM agencies ORDER BY id")
    return [dict(row) for row in cur.fetchall()]


def get_agency(cur, agency_id: int) -> dict[str, Any] | None:
    """Una agenzia, o None. Il 404 lo decide il service."""
    cur.execute(f"SELECT {_SELECT} FROM agencies WHERE id = %s", (agency_id,))
    return _row(cur.fetchone())


def slug_exists(cur, slug: str) -> bool:
    """True se lo slug e' gia' di un'agenzia. Un solo chiamante: la creazione.

    Aveva un parametro `exclude_agency_id`, che serviva a una cosa sola:
    permettere a una PATCH di riscrivere lo slug invariato senza ricevere 409.
    Con lo slug non piu' modificabile quel caso non esiste, e il parametro e'
    stato tolto invece di restare come un'opzione che nessuno usa - un
    argomento inutilizzato e' un invito a trovargli un uso.

    Confronta su tutte le agenzie, qualunque sia il loro stato. Uno slug e'
    unico per vincolo di database (`agencies_slug_unq`) e quel vincolo non
    guarda lo stato: escludere le archiviate qui produrrebbe un "libero" a cui
    la INSERT risponderebbe con una violazione.
    """
    cur.execute("SELECT 1 FROM agencies WHERE slug = %s", (slug,))
    return cur.fetchone() is not None


def create_agency(
    cur, *, name: str, slug: str, status: str, settings: dict[str, Any]
) -> dict[str, Any]:
    """Inserisce una agenzia e restituisce la riga completa.

    Keyword-only: `name`, `slug` e `status` sono tre stringhe adiacenti, e in
    posizionale uno scambio produrrebbe una riga plausibile che nessun vincolo
    intercetta - `status` fallirebbe il CHECK, ma `name` e `slug` scambiati no.

    NON committa. Vedi `platform_admin/database.platform_operation_cursor`.
    """
    cur.execute(
        f"""
        INSERT INTO agencies (name, slug, status, settings)
        VALUES (%s, %s, %s, %s)
        RETURNING {_SELECT}
        """,
        (name, slug, status, Json(settings)),
    )
    return dict(cur.fetchone())


def update_agency(cur, agency_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Aggiorna SOLO i campi presenti in `fields`. None se l'id non esiste.

    `updated_at` e' scritto qui e non e' un campo che il chiamante possa
    fornire: e' la data della modifica, non un dato dell'agenzia. `agencies`
    non ha un trigger che lo mantenga (la 027 non ne installa nessuno), quindi
    senza questa riga la colonna resterebbe alla data di creazione per sempre.

    Un nome fuori da `UPDATABLE_COLUMNS` e' un difetto del chiamante, non un
    input: il service passa campi gia' validati. Viene rifiutato con un errore
    rumoroso invece di essere ignorato in silenzio, perche' un campo scartato
    senza dirlo produce una PATCH che risponde 200 e non ha fatto nulla.
    """
    unknown = [key for key in fields if key not in UPDATABLE_COLUMNS]
    if unknown:
        raise ValueError(f"colonne non aggiornabili: {sorted(unknown)}")
    if not fields:
        raise ValueError("update_agency richiede almeno un campo")

    assignments = []
    params: list[Any] = []
    for column in UPDATABLE_COLUMNS:
        if column not in fields:
            continue
        assignments.append(f"{column} = %s")
        value = fields[column]
        params.append(Json(value) if column == "settings" else value)

    cur.execute(
        f"""
        UPDATE agencies
           SET {', '.join(assignments)}, updated_at = NOW()
         WHERE id = %s
        RETURNING {_SELECT}
        """,
        params + [agency_id],
    )
    return _row(cur.fetchone())
