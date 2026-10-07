"""CENSIMENTO-1 Fase 3 - EDIFICI, UNITA' DI CENSIMENTO, PERTINENZE, ACCESSORI.

Il backend del censimento sul modello della migration 083 (progetto
CENSIMENTO-0 REV 3.1: §0 p.5-6, §2, §4, §6, §7). Service e repository in un
solo modulo, come `property/mandates.py` e `property/interactions.py`.

Regole che questo modulo FA rispettare (quelle che il database non puo'):
  * ogni identificativo ricevuto (edificio, unita', pertinenza, accessorio)
    e' cercato NELL'AGENZIA dello scope: altrove = inesistente (404). Mai un
    `agency_id` dal client (gli schemi lo rifiutano con 422);
  * ogni operazione composta e' UNA transazione (`core_cursor(commit=True)`):
    commit alla fine, rollback completo su qualunque errore, mai uno stato a
    meta'. Un collegamento per transazione (i lock advisory della 083 valgono
    dentro la singola istruzione): un deadlock (40P01) si ritenta da capo,
    al massimo `MAX_RETRY` volte; nient'altro si ritenta;
  * idempotenza: ogni creazione porta `client_request_id` + impronta del
    payload. Stessa chiave e stesso payload -> la stessa riga (replica);
    stessa chiave e payload diverso -> 409 IDEMPOTENCY_KEY_REUSED. Due
    richieste concorrenti con la stessa chiave: l'indice UNIQUE parziale della
    083 ne fa vincere una, l'altra rilegge e restituisce la stessa riga;
  * duplicati: BLOCCO solo sull'identita' catastale completa (409
    CADASTRAL_DUPLICATE, con il codice della scheda esistente, nessun
    "salva comunque"); tutto il resto e' un AVVISO: stessa posizione
    (edificio+scala+piano+interno), stessi identificativi con sezione non
    conosciuta, edifici con la stessa chiave catastale o lo stesso indirizzo
    -> 409 SIMILAR_FOUND con i candidati, superabile con `confirm_similar`;
  * indirizzo ereditato: un'unita' creata in palazzina senza un indirizzo
    proprio nasce `address_inherited = TRUE` con l'indirizzo MATERIALIZZATO
    (copiato nelle colonne che MATCH, promemoria e titoli leggono gia');
    l'indirizzo dell'edificio si propaga SOLO alle unita' ereditate, nella
    stessa transazione, e la risposta dice quante ha toccato e quante no;
  * separazione censimento/commerciale (§7): la presa in carico e' la SOLA
    via da `census` a `crm` e cambia solo `record_kind` (il trigger della 083
    rifiuta ogni altra combinazione); `crm -> census` non esiste;
  * `parent_property_id` e' la relazione CORRENTE: scollegamento, vendita
    autonoma o archiviazione della pertinenza lo azzerano e scrivono lo
    storico come interazione di sistema (`activities`, tipo `system`) su
    entrambe le schede.

Cosa NON fa: nessuna UI (Fase 4), nessun filtro degli elenchi o conteggio di
dashboard per `record_kind` (Fase 5), nessuna modifica alla 083.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from psycopg2 import errors as pg_errors
from psycopg2.extras import Json

from core import repository as core_repository
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError, ValidationError

from core import property_trash as _property_trash

from . import repository
from .catalog import PERTINENZA_PROPERTY_TYPE, generated_title, validate_cadastral_category, validate_location

# ---------------------------------------------------------------------------
# codici e messaggi (stesso idioma di acquisitions/errors.py: `code` + `extra`)
# ---------------------------------------------------------------------------

CENSUS_LOCKED = "Immobile in censimento: usa Prendi in carico"
NOT_CENSUS = "L'immobile e' gia' nel lavoro commerciale"
IDEMPOTENCY_REUSED = "Questa richiesta risulta gia' inviata con dati diversi: ricontrolla e conferma"
UNDO_NOT_POSSIBLE = "Non piu' annullabile: apri la scheda"
MAX_RETRY = 3

_RICHIESTA_IMMUTABILE = ("client_request_id", "confirm_similar")
_INDIRIZZO = ("region", "province", "city", "microzone", "address", "civic_number", "postal_code")
_CATASTO_UNITA = ("cadastral_municipality_code", "cadastral_section", "cadastral_sheet",
                  "cadastral_parcel", "cadastral_subunit")
_CATASTO_EDIFICIO = ("cadastral_municipality_code", "cadastral_section", "cadastral_sheet",
                     "cadastral_parcel")
#: Quale tipologia nasce da un accessorio che "e' separata" quando il client
#: non la indica: la natura la decide la risposta dell'agente (progetto §4),
#: qui solo il valore di partenza, sempre sovrascrivibile con `property_type`.
#: PERTINENZE-1: un solo elenco, nel catalogo (form-options lo espone).
ACCESSORY_TO_TYPE = PERTINENZA_PROPERTY_TYPE
#: Le due tabelle figlie che nascono CON l'immobile (storico prezzi e stati):
#: non sono "collegamenti" ai fini dell'annullamento.
_STORICI_PROPRI = ("property_price_history", "property_status_history")


class _ConCodice:
    code = None

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class CensusConflict(_ConCodice, ConflictError):
    def __init__(self, message, code, **extra):
        super().__init__(message, **extra)
        self.code = code


class CensusInvalid(_ConCodice, ValidationError):
    def __init__(self, message, code="VALIDATION_ERROR", **extra):
        super().__init__(message, **extra)
        self.code = code


class CensusNotInstalled(_ConCodice, ConflictError):
    """Il database non ha la 083: il modulo non e' installato (503, stessa
    chiusura leggibile di CRM-OPS-3 RC-1 per la 081). Non e' un errore
    dell'operatore ne' dei dati."""
    code = "CENSUS_NOT_INSTALLED"


CENSUS_NOT_INSTALLED_MESSAGE = ("Il modulo Censimento non e' installato su questo database "
                                "(migration 083 non applicata).")


# ---------------------------------------------------------------------------
# PERTINENZE-1: la NATURA di pertinenza, indipendente dal collegamento (089)
#
#   * una scheda e' PERTINENZA se e' collegata a un'unita' principale
#     (`parent_property_id`, relazione corrente della 083) OPPURE se e'
#     marcata `is_pertinenza` (089): censita ma non ancora collegata, o
#     scollegata. Lo scollegamento non la riporta a principale;
#   * `pertinenza_kind`: il tipo, con il catalogo degli accessori (box e posto
#     auto distinti). NULL = non indicato;
#   * il collegamento all'EDIFICIO (`building_id`) e' un'altra cosa: mai
#     dedotto dall'unita' principale, ne' dall'indirizzo.
#
# Codice prima dello schema: le letture passano da `to_jsonb(riga)`, valide
# anche senza la 089 (tutto come prima: nessuna pertinenza «da collegare»);
# le scritture della natura si fanno solo con la 089 (`_ha_089`). Creare una
# pertinenza dichiarata senza la 089 si rifiuta in modo leggibile, perche'
# la natura andrebbe persa.
# ---------------------------------------------------------------------------

PERTINENZE_NOT_INSTALLED_MESSAGE = ("Le pertinenze autonome non sono ancora disponibili su questo database "
                                    "(migration 089 non applicata).")


def _ha_089(cur) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'properties' AND column_name = 'pertinenza_kind') AS pronta")
    return bool(cur.fetchone()["pronta"])


def _marcata(alias: str) -> str:
    return f"coalesce((to_jsonb({alias})->>'is_pertinenza')::boolean, FALSE)"


def pertinenza_sql(alias: str) -> str:
    """La regola, in SQL: collegata a una principale o marcata pertinenza."""
    return f"({alias}.parent_property_id IS NOT NULL OR {_marcata(alias)})"


def da_collegare_sql(alias: str) -> str:
    """Pertinenza autonoma censita ma senza unita' principale."""
    return f"({alias}.parent_property_id IS NULL AND {_marcata(alias)})"


def is_pertinenza(riga: dict) -> bool:
    return riga.get("parent_property_id") is not None or bool(riga.get("is_pertinenza"))


def _accessorio_istantanea(accessorio: dict) -> dict:
    """Cio' che un accessorio era, quando diventa unita' autonoma: tipo,
    stato, provenienza (`source`, 087: «Dal sito»), superficie, quantita',
    note. Conservato nei `metadata` della scheda e nello storico."""
    def j(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, datetime):
            return v.isoformat()
        return v
    return {"accessory_id": accessorio["id"], **{k: j(accessorio.get(k)) for k in (
        "kind", "cadastral_status", "source", "surface_sqm", "quantity", "notes", "created_at")}}


def _assicura_083(cur) -> None:
    """Ogni operazione del censimento comincia da qui: se la 083 manca si
    risponde 503 PRIMA di leggere righe che non hanno le colonne attese
    (niente KeyError travestiti da 500, niente eccezioni catturate alla
    cieca). Una SELECT sul catalogo, nella stessa transazione."""
    cur.execute("SELECT to_regclass('public.buildings') IS NOT NULL"
                "   AND to_regclass('public.property_accessories') IS NOT NULL"
                "   AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                "                 AND table_name = 'properties' AND column_name = 'record_kind') AS pronto")
    if not cur.fetchone()["pronto"]:
        raise CensusNotInstalled(CENSUS_NOT_INSTALLED_MESSAGE)


class _ReplicaRace(Exception):
    """La stessa `client_request_id` e' stata scritta da un'altra transazione
    mentre questa lavorava: si rilegge in una transazione nuova."""


# ---------------------------------------------------------------------------
# utilita'
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _json_default(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    return str(v)


def _fingerprint(payload: dict) -> str:
    """L'impronta del payload SENZA la chiave e le conferme: la stessa
    richiesta ripetuta ha la stessa impronta, una diversa no."""
    pulito = {k: v for k, v in payload.items() if k not in _RICHIESTA_IMMUTABILE}
    return hashlib.sha256(json.dumps(pulito, sort_keys=True, default=_json_default).encode()).hexdigest()


def _norm(valore):
    """La normalizzazione catastale della 083 (`cadastral_norm`), per
    confrontare in Python cio' che il database salva in forma canonica."""
    if valore is None:
        return None
    v = re.sub(r"\s+", "", str(valore)).upper()
    if v == "":
        return ""
    if re.fullmatch(r"0+[0-9]+", v):
        v = v.lstrip("0")
    return v


def _check_cadastral(data: dict) -> None:
    """Formato del Belfiore e catalogo della categoria, PRIMA del database
    (la 083 ha un CHECK di formato; l'appartenenza al catalogo e' qui)."""
    if "cadastral_category" in data:
        try:
            data["cadastral_category"] = validate_cadastral_category(data["cadastral_category"])
        except ValueError as exc:
            raise CensusInvalid(str(exc)) from exc
    belfiore = data.get("cadastral_municipality_code")
    if belfiore is not None:
        canonico = _norm(belfiore)
        if canonico and not re.fullmatch(r"[A-Z][0-9]{3}", canonico):
            raise CensusInvalid("Codice catastale del comune non valido: una lettera e tre cifre (es. A125)")
        data["cadastral_municipality_code"] = canonico or None


def _check_location(data: dict) -> None:
    """La regola CRM-OPS-2: il territorio si giudica solo quando arriva
    `region` (il form OS manda la cascata intera)."""
    if "region" in data:
        try:
            validate_location(data.get("region"), data.get("province"), data.get("city"),
                              data.get("microzone"))
        except ValueError as exc:
            raise CensusInvalid(str(exc)) from exc


def _tradotto(exc):
    """Un errore del database tradotto in un errore API leggibile, oppure
    None se non e' uno dei casi del censimento."""
    if isinstance(exc, pg_errors.DeadlockDetected):
        return None
    vincolo = getattr(getattr(exc, "diag", None), "constraint_name", None) or ""
    testo = str(exc)
    if isinstance(exc, pg_errors.UniqueViolation):
        if vincolo == "uq_properties_cadastral_identity":
            return CensusConflict("Questo subalterno e' gia' censito nella tua agenzia",
                                  "CADASTRAL_DUPLICATE")
        if vincolo.startswith("uq_") and vincolo.endswith("_client_request"):
            return _ReplicaRace()
        return ConflictError("Valore gia' presente")
    if isinstance(exc, pg_errors.CheckViolation) or isinstance(exc, pg_errors.RaiseException):
        if "cannot have commercial status" in testo or "cannot carry a mandate" in testo \
                or "cannot go back to census" in testo or "cannot change its commercial fields" in testo:
            return CensusConflict(CENSUS_LOCKED, "CENSUS_LOCKED")
        if "is itself a pertinenza" in testo:
            return CensusInvalid("L'unita' scelta e' gia' una pertinenza: non puo' avere pertinenze", "LINK_INVALID")
        if "has pertinenze and cannot become a pertinenza" in testo:
            return CensusInvalid("L'immobile ha pertinenze collegate: non puo' diventare una pertinenza", "LINK_INVALID")
        if "does not belong to agency" in testo:
            return NotFoundError("Immobile o edificio non trovato")
        if "cannot change agency" in testo:
            return CensusInvalid("L'immobile ha collegamenti: non puo' cambiare agenzia", "LINK_INVALID")
        if "immutable once set" in testo:
            return CensusInvalid("La chiave di idempotenza non si modifica", "IDEMPOTENCY_IMMUTABLE")
        if vincolo in ("properties_belfiore_chk", "buildings_belfiore_chk"):
            return CensusInvalid("Codice catastale del comune non valido: una lettera e tre cifre (es. A125)")
        if vincolo == "properties_cadastral_category_chk":
            return CensusInvalid("Categoria catastale non valida")
        if vincolo == "properties_parent_not_self_chk":
            return CensusInvalid("Un immobile non puo' essere pertinenza di se stesso", "LINK_INVALID")
        if vincolo == "properties_whole_building_chk":
            return CensusInvalid("'Stabile intero' richiede la palazzina")
        if vincolo:
            return CensusInvalid("Dati non validi per il database")
    if isinstance(exc, pg_errors.ForeignKeyViolation):
        return NotFoundError("Immobile o edificio non trovato")
    if isinstance(exc, pg_errors.StringDataRightTruncation):
        return CensusInvalid("Valore troppo lungo")
    return None


def _esegui(operazione):
    """Una transazione intera, con la traduzione degli errori e il retry sul
    solo deadlock (40P01), ripetendo TUTTA l'operazione in una transazione
    nuova. Validazione, autorizzazione e unicita' non si ritentano mai."""
    tentativo = 0
    while True:
        tentativo += 1
        try:
            return operazione()
        except pg_errors.DeadlockDetected:
            if tentativo >= MAX_RETRY:
                raise CensusConflict("Operazione in conflitto con un'altra in corso: riprova",
                                     "RETRY_EXHAUSTED")
            continue
        except pg_errors.Error as exc:
            tradotto = _tradotto(exc)
            if tradotto is None:
                raise
            raise tradotto from exc


def _con_replica(ctx, chiave, leggi_replica, operazione, impronta):
    """L'idempotenza: prima la replica gia' scritta, poi l'operazione; se una
    transazione concorrente ha scritto la stessa chiave nel frattempo, si
    rilegge e si restituisce la sua riga."""
    try:
        return _esegui(operazione)
    except _ReplicaRace:
        riga = leggi_replica()
        if riga is None:
            raise CensusConflict(IDEMPOTENCY_REUSED, "IDEMPOTENCY_KEY_REUSED")
        if riga.get("client_request_fingerprint") != impronta:
            raise CensusConflict(IDEMPOTENCY_REUSED, "IDEMPOTENCY_KEY_REUSED")
        return {**riga, "replica": True}


def _replica_in(cur, tabella, agency_id, chiave, impronta, *, property_id=None):
    """La riga gia' scritta per questa chiave (nell'agenzia; per gli
    accessori nell'immobile), o None. Chiave uguale e impronta diversa = 409."""
    if chiave is None:
        return None
    if tabella == "property_accessories":
        cur.execute("SELECT * FROM property_accessories WHERE property_id = %s AND client_request_id = %s",
                    (property_id, str(chiave)))
    else:
        # DELETE-ARCH 2B2: un immobile nel Cestino non e' la replica di nessuna
        # richiesta (la sua client_request_id e' libera dalla 085).
        filtro = f" AND {_property_trash.live(tabella)}" if tabella == "properties" else ""
        cur.execute(f"SELECT * FROM {tabella} WHERE agency_id = %s AND client_request_id = %s{filtro}",
                    (agency_id, str(chiave)))
    riga = repository.row(cur.fetchone())
    if riga is None:
        return None
    if riga.get("client_request_fingerprint") != impronta:
        raise CensusConflict(IDEMPOTENCY_REUSED, "IDEMPOTENCY_KEY_REUSED")
    return riga


def _attivita_sistema(ctx, cur, property_id, testo, **metadata):
    """Lo storico come interazione di sistema sulla scheda (CRM-OPS-4:
    `activities` con property_id, tipo `system`)."""
    core_repository.create_activity_with_cursor(cur, {
        "contact_id": None, "lead_id": None, "stima_id": None, "property_id": property_id,
        "activity_type": "system", "direction": None, "channel": None, "subject": None,
        "description": testo, "outcome": None, "occurred_at": None, "created_by": None,
        "metadata": {"context": "census", **metadata},
    }, ctx=ctx)


def _etichetta(riga) -> str:
    return riga.get("code") or f"#{riga['id']}"


# ---------------------------------------------------------------------------
# letture nello scope
# ---------------------------------------------------------------------------

def _edificio(cur, agency_id, building_id, *, lock=False, share=False):
    """`lock`: FOR UPDATE (chi modifica l'edificio). `share`: FOR SHARE (chi
    ne copia l'indirizzo in una unita' nuova): cosi' la PATCH dell'edificio
    aspetta la creazione e la propaga anche alla riga nuova, oppure la
    creazione aspetta la PATCH e copia l'indirizzo nuovo - mai un'unita'
    ereditata con l'indirizzo vecchio. E' lo stesso FOR SHARE che il trigger
    della 083 prende sull'edificio al momento del collegamento."""
    cur.execute(f"SELECT * FROM buildings WHERE id = %s AND agency_id = %s AND archived_at IS NULL"
                f"{' FOR UPDATE' if lock else (' FOR SHARE' if share else '')}", (building_id, agency_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"building {building_id} not found")
    return riga


def _unita(cur, agency_id, property_id, *, lock=False, archiviata_ok=False):
    cur.execute(f"SELECT * FROM properties WHERE id = %s AND agency_id = %s"
                f"{'' if archiviata_ok else ' AND archived_at IS NULL'}"
                f"{' FOR UPDATE' if lock else ''}", (property_id, agency_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"property {property_id} not found")
    if riga.get("deleted_at") is not None:
        # DELETE-ARCH 2B2: un'unita' nel Cestino non si legge (404) e non si
        # modifica (409 PROPERTY_IN_TRASH) dal censimento.
        if lock:
            raise _property_trash.PropertyInTrash()
        raise NotFoundError(f"property {property_id} not found")
    return riga


def _accessorio(cur, property_id, accessory_id, *, lock=False):
    cur.execute(f"SELECT * FROM property_accessories WHERE id = %s AND property_id = %s"
                f"{' FOR UPDATE' if lock else ''}", (accessory_id, property_id))
    riga = repository.row(cur.fetchone())
    if riga is None:
        raise NotFoundError(f"accessory {accessory_id} not found")
    return riga


def _contatori_edificio(cur, building_id) -> dict:
    # PERTINENZE-1: principale/pertinenza dalla NATURA (collegata o marcata),
    # piu' quante pertinenze sono ancora da collegare
    cur.execute(f"""
        SELECT count(*) AS units_census,
               count(*) FILTER (WHERE NOT {pertinenza_sql('p')}) AS units_main,
               count(*) FILTER (WHERE {pertinenza_sql('p')}) AS units_pertinenze,
               count(*) FILTER (WHERE {da_collegare_sql('p')}) AS units_pertinenze_unlinked,
               count(*) FILTER (WHERE address_inherited) AS units_address_inherited,
               count(*) FILTER (WHERE NOT address_inherited) AS units_address_custom,
               (SELECT count(*) FROM property_accessories a JOIN properties u ON u.id = a.property_id
                 WHERE u.building_id = p.building_id AND u.archived_at IS NULL AND (to_jsonb(u)->>'deleted_at') IS NULL
                   AND a.cadastral_status = 'unknown') AS accessories_unknown
          FROM properties p WHERE p.building_id = %s AND p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
         GROUP BY p.building_id""", (building_id,))
    riga = cur.fetchone()
    if riga is None:
        return {"units_census": 0, "units_main": 0, "units_pertinenze": 0, "units_pertinenze_unlinked": 0,
                "units_address_inherited": 0, "units_address_custom": 0, "accessories_unknown": 0}
    return {k: int(v) for k, v in dict(riga).items()}


_UNITA_COLONNE = ("id, code, title, property_type, commercial_status, record_kind, floor, staircase, "
                  "internal_number, surface_sqm, rooms, bathrooms, cadastral_category, "
                  "cadastral_municipality_code, cadastral_section, cadastral_sheet, cadastral_parcel, "
                  "cadastral_subunit, parent_property_id, building_id, whole_building, address_inherited, "
                  "address, civic_number, city, created_at, updated_at, assigned_agent_id, archived_at")


def _colonne_unita(alias: str) -> str:
    """Le colonne dell'elenco unita' e, PERTINENZE-1, la natura (valida anche
    senza la 089) e la provenienza da un accessorio."""
    return (", ".join(f"{alias}.{c.strip()}" for c in _UNITA_COLONNE.split(",")) +
            f", {_marcata(alias)} AS is_pertinenza, to_jsonb({alias})->>'pertinenza_kind' AS pertinenza_kind, "
            f"{pertinenza_sql(alias)} AS pertinenza, {alias}.metadata->'from_accessory' AS from_accessory")


def _unita_dell_edificio(cur, building_id) -> list[dict]:
    """Le unita' della palazzina, ordinate per piano (numerico dove possibile,
    con Seminterrato / Terra / Rialzato al loro posto; poi testuale), scala,
    interno. Il raggruppamento per piano lo fa la UI."""
    cur.execute(f"""
        SELECT {_colonne_unita('p')},
               (SELECT count(*) FROM property_accessories a WHERE a.property_id = p.id
                   AND a.cadastral_status = 'unknown') AS accessories_unknown
          FROM properties p WHERE p.building_id = %s AND p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL
           AND p.commercial_status IS DISTINCT FROM 'archived'
         ORDER BY CASE WHEN p.floor ~ '^-?[0-9]+$' THEN p.floor::numeric
                       -- EDIFICI-1: i piani a lettera dei chips (Sem, T, R) al loro posto
                       WHEN upper(btrim(p.floor)) = 'S' THEN -0.5
                       WHEN upper(btrim(p.floor)) IN ('T', 'PT') THEN 0
                       WHEN upper(btrim(p.floor)) = 'R' THEN 0.5 END NULLS LAST,
                  p.floor NULLS LAST, p.staircase NULLS FIRST,
                  CASE WHEN p.internal_number ~ '^[0-9]+$' THEN p.internal_number::int END NULLS LAST,
                  p.internal_number NULLS FIRST, p.id""", (building_id,))
    return [dict(r) for r in cur.fetchall()]


def _dettaglio_edificio(cur, edificio) -> dict:
    unita = _unita_dell_edificio(cur, edificio["id"])
    _relazioni(cur, unita)
    archiviate = _unita_archiviate(cur, edificio["id"])
    _relazioni(cur, archiviate)
    riepilogo = _riepiloghi(cur, [edificio])[edificio["id"]]
    return {**edificio, "counters": _contatori_edificio(cur, edificio["id"]),
            "census_summary": riepilogo, "units": unita, "archived_units": archiviate,
            "staircases": sorted({str(u["staircase"]) for u in unita if u.get("staircase")}, key=str.casefold)}


# ---------------------------------------------------------------------------
# EDIFICI-1 - IL RIEPILOGO DEL CENSIMENTO DI UN EDIFICIO
#
# Le regole, in un posto solo (lista Edifici e scheda edificio le leggono da
# qui, con le stesse query; nessun conteggio nel browser):
#
#   * UNITA' = una riga `properties` con `building_id` = l'edificio. Solo la
#     relazione reale: mai dedotta dall'indirizzo. Gli ACCESSORI
#     (`property_accessories`, compresi o «da chiarire») non sono righe di
#     `properties` e non contano MAI come unita'. Una riga si conta una volta
#     sola: principale o pertinenza lo dice `parent_property_id` (relazione
#     corrente), non un secondo conteggio;
#   * DICHIARATE = `buildings.units_declared` (principali + pertinenze con
#     subalterno proprio: decisione 5 di CENSIMENTO-0). NULL = NON NOTO,
#     diverso da 0;
#   * CESTINO: un'unita' nel Cestino non conta e non si elenca; i suoi
#     collegamenti (`building_id`, `parent_property_id`) restano intatti per
#     il ripristino;
#   * ARCHIVIATE: archiviata non vuol dire fisicamente inesistente (un
#     appartamento venduto e archiviato sta ancora nella palazzina). Restano
#     CENSITE, contate a parte («di cui archiviate») ed elencate a parte.
#     Fa eccezione la sola unita' annullata subito dopo la creazione
#     («Annulla» del toast, undo-create: CENSIMENTO-0 S5, «contatore
#     corretto»): era un inserimento sbagliato, non un'unita';
#   * CENSITE = attive + archiviate (eccezione sopra);
#   * DA COMPLETARE = dichiarate - censite, solo se le dichiarate sono note
#     (altrimenti NON NOTO, mai 0) e mai negativo: se le censite superano le
#     dichiarate lo dice `units_over_declared`, senza correggere nulla.
# ---------------------------------------------------------------------------

_ARCHIVIATA = "(u.archived_at IS NOT NULL OR u.commercial_status = 'archived')"
_ANNULLATA = ("(SELECT h.note FROM property_status_history h WHERE h.property_id = u.id "
              "AND h.field_name = 'commercial_status' ORDER BY h.id DESC LIMIT 1) = 'undo-create'")


def summary_from_counts(declared, active: int, main: int, pertinenze: int, archived: int,
                        pertinenze_unlinked: int = 0) -> dict:
    """Il riepilogo dai conteggi grezzi (funzione pura: le regole sopra).
    PERTINENZE-1: una pertinenza da collegare e' una pertinenza (mai una
    principale) e si conta una volta sola; `units_pertinenze_unlinked` dice
    quante delle pertinenze attive non hanno ancora un'unita' principale."""
    censite = active + archived
    noto = declared is not None
    return {
        "units_declared": declared,
        "units_declared_known": noto,
        "units_counted": censite,
        "units_active": active,
        "units_main": main,
        "units_pertinenze": pertinenze,
        "units_pertinenze_unlinked": pertinenze_unlinked,
        "units_archived": archived,
        "units_to_complete": max(declared - censite, 0) if noto else None,
        "units_over_declared": max(censite - declared, 0) if noto else 0,
    }


def _riepiloghi(cur, edifici: list[dict]) -> dict:
    """Il riepilogo di PIU' edifici con due query aggregate (mai una per
    edificio): {building_id: riepilogo}."""
    ids = [e["id"] for e in edifici]
    conteggi = {i: {"active": 0, "main": 0, "pertinenze": 0, "unlinked": 0, "archived": 0, "census": 0,
                    "category_missing": 0, "accessories_unknown": 0} for i in ids}
    if ids:
        cur.execute(f"""
            SELECT u.building_id,
                   count(*) FILTER (WHERE NOT {_ARCHIVIATA}) AS active,
                   count(*) FILTER (WHERE NOT {_ARCHIVIATA} AND NOT {pertinenza_sql('u')}) AS main,
                   count(*) FILTER (WHERE NOT {_ARCHIVIATA} AND {pertinenza_sql('u')}) AS pertinenze,
                   count(*) FILTER (WHERE NOT {_ARCHIVIATA} AND {da_collegare_sql('u')}) AS unlinked,
                   count(*) FILTER (WHERE {_ARCHIVIATA} AND NOT coalesce({_ANNULLATA}, FALSE)) AS archived,
                   count(*) FILTER (WHERE NOT {_ARCHIVIATA} AND u.record_kind = 'census') AS census,
                   count(*) FILTER (WHERE NOT {_ARCHIVIATA} AND u.cadastral_category IS NULL) AS category_missing
              FROM properties u
             WHERE u.building_id = ANY(%s) AND {_property_trash.live('u')}
             GROUP BY u.building_id""", (ids,))
        for r in cur.fetchall():
            conteggi[r["building_id"]].update({k: int(r[k]) for k in
                                               ("active", "main", "pertinenze", "unlinked", "archived", "census",
                                                "category_missing")})
        cur.execute(f"""
            SELECT u.building_id, count(*) AS n
              FROM property_accessories a JOIN properties u ON u.id = a.property_id
             WHERE u.building_id = ANY(%s) AND NOT {_ARCHIVIATA} AND {_property_trash.live('u')}
               AND a.cadastral_status = 'unknown'
             GROUP BY u.building_id""", (ids,))
        for r in cur.fetchall():
            conteggi[r["building_id"]]["accessories_unknown"] = int(r["n"])
    esito = {}
    for e in edifici:
        c = conteggi[e["id"]]
        esito[e["id"]] = {**summary_from_counts(e.get("units_declared"), c["active"], c["main"],
                                                 c["pertinenze"], c["archived"], c["unlinked"]),
                          "units_declared_source": e.get("units_declared_source"),
                          "units_in_census": c["census"], "category_to_verify": c["category_missing"],
                          "accessories_unknown": c["accessories_unknown"]}
    return esito


def _relazioni(cur, unita: list[dict]) -> None:
    """Sulle righe dell'elenco: di chi e' pertinenza (codice del principale,
    se vivo) e quante pertinenze correnti ha un principale (ovunque si
    trovino: il garage nella palazzina di fronte resta suo). Due query."""
    ids = [u["id"] for u in unita]
    genitori = sorted({u["parent_property_id"] for u in unita if u.get("parent_property_id")})
    codici, figlie = {}, {}
    if genitori:
        cur.execute(f"SELECT id, code, building_id FROM properties u WHERE id = ANY(%s) AND {_property_trash.live('u')}",
                    (genitori,))
        codici = {r["id"]: dict(r) for r in cur.fetchall()}
    if ids:
        cur.execute(f"SELECT parent_property_id AS id, count(*) AS n FROM properties u WHERE parent_property_id = ANY(%s) "
                    f"AND NOT {_ARCHIVIATA} AND {_property_trash.live('u')} GROUP BY parent_property_id", (ids,))
        figlie = {r["id"]: int(r["n"]) for r in cur.fetchall()}
    for u in unita:
        g = codici.get(u.get("parent_property_id"))
        u["parent"] = None if g is None else {"id": g["id"], "code": g.get("code"),
                                              "same_building": g.get("building_id") == u.get("building_id")}
        u["pertinenze_count"] = figlie.get(u["id"], 0)


def _unita_archiviate(cur, building_id) -> list[dict]:
    """Le unita' archiviate della palazzina (non nel Cestino, non annullate
    alla creazione): censite, elencate a parte."""
    cur.execute(f"""
        SELECT {_colonne_unita('u')}, 0 AS accessories_unknown
          FROM properties u WHERE u.building_id = %s AND {_ARCHIVIATA} AND {_property_trash.live('u')}
           AND NOT coalesce({_ANNULLATA}, FALSE)
         ORDER BY u.archived_at DESC NULLS LAST, u.id""", (building_id,))
    return [dict(r) for r in cur.fetchall()]


def building_summary(agency_id: int, building_id: int) -> dict | None:
    """L'edificio di appartenenza per la scheda immobile (collegamento e
    indirizzo), nell'agenzia dell'immobile. None se non esiste."""
    with core_cursor() as (_, cur):
        cur.execute("SELECT id, name, building_type, city, microzone, address, civic_number, archived_at "
                    "FROM buildings WHERE id = %s AND agency_id = %s", (building_id, agency_id))
        return repository.row(cur.fetchone())


# ---------------------------------------------------------------------------
# EDIFICI
# ---------------------------------------------------------------------------

def _simili_edificio(cur, agency_id, data, escluso=None) -> list[dict]:
    """Avvisi (mai blocchi, §6.1 REV 3.1): stessa chiave catastale
    (Belfiore+foglio+particella) o stesso indirizzo (comune+via+civico)."""
    condizioni, params = [], []
    belfiore, foglio, particella = (_norm(data.get(k)) for k in
                                    ("cadastral_municipality_code", "cadastral_sheet", "cadastral_parcel"))
    if belfiore and foglio and particella:
        condizioni.append("(cadastral_municipality_code = %s AND cadastral_sheet = %s AND cadastral_parcel = %s)")
        params += [belfiore, foglio, particella]
    citta, via, civico = (data.get(k) for k in ("city", "address", "civic_number"))
    if citta and via:
        condizioni.append("(lower(city) = lower(%s) AND lower(btrim(address)) = lower(btrim(%s))"
                          " AND coalesce(lower(btrim(civic_number)), '') = coalesce(lower(btrim(%s)), ''))")
        params += [citta, via, civico]
    if not condizioni:
        return []
    cur.execute("SELECT id, name, city, address, civic_number, cadastral_municipality_code, cadastral_sheet, "
                "cadastral_parcel FROM buildings WHERE agency_id = %s AND archived_at IS NULL AND id IS DISTINCT FROM %s "
                f"AND ({' OR '.join(condizioni)}) ORDER BY id LIMIT 10", [agency_id, escluso] + params)
    return [dict(r) for r in cur.fetchall()]


def _unita_ereditate(cur, building_id) -> list[dict]:
    """Le unita' con indirizzo ereditato, bloccate FOR UPDATE: una
    personalizzazione concorrente («Ingresso diverso?») aspetta, e una riga
    personalizzata nel frattempo non e' piu' nell'insieme (READ COMMITTED
    rivaluta il predicato dopo l'attesa)."""
    cur.execute("SELECT id, title, property_type, city, microzone, address, civic_number "
                "FROM properties WHERE building_id = %s AND address_inherited AND archived_at IS NULL AND (to_jsonb(properties)->>'deleted_at') IS NULL "
                "ORDER BY id FOR UPDATE", (building_id,))
    return [dict(r) for r in cur.fetchall()]


#: Ordinamenti della lista: `recent` (il default storico, per aggiornamento)
#: e `address` (comune, via, civico: la lista Edifici di EDIFICI-1).
BUILDING_SORTS = {
    "recent": "b.updated_at DESC, b.id DESC",
    "address": ("lower(coalesce(b.city, '')), lower(coalesce(b.address, '')), "
                "CASE WHEN b.civic_number ~ '^[0-9]+' THEN substring(b.civic_number FROM '^[0-9]+')::int END NULLS LAST, "
                "lower(coalesce(b.civic_number, '')), b.id"),
}


def _like(parola: str) -> str:
    """Una parola cercata come testo, non come pattern (% e _ letterali)."""
    return "%" + parola.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_buildings(ctx, *, search=None, city=None, microzone=None, sort="recent", limit=50, offset=0) -> dict:
    """La lista Edifici. Ricerca per PAROLE (ciascuna su nome, via, civico,
    comune o microzona: «roma 10» trova via Roma 10); Comune e Microzona sono
    filtri esatti sui valori del catalogo territoriale. Paginata, con il
    totale e il riepilogo del censimento di ogni riga calcolati con query
    aggregate sull'intera pagina."""
    agency_id = ctx.require_agency()
    if not 1 <= int(limit) <= 200 or int(offset) < 0:
        raise ValidationError("Paginazione non valida")
    if sort not in BUILDING_SORTS:
        raise ValidationError("Ordinamento non valido")
    with core_cursor() as (_, cur):
        _assicura_083(cur)
        condizioni, params = ["b.agency_id = %s", "b.archived_at IS NULL"], [agency_id]
        for parola in (search or "").split()[:8]:
            condizioni.append("concat_ws(' ', b.name, b.address, b.civic_number, b.city, b.microzone) ILIKE %s")
            params.append(_like(parola))
        if city:
            condizioni.append("b.city = %s")
            params.append(city)
        if microzone:
            condizioni.append("b.microzone = %s")
            params.append(microzone)
        dove = " AND ".join(condizioni)
        cur.execute(f"SELECT count(*) AS n FROM buildings b WHERE {dove}", params)
        totale = int(cur.fetchone()["n"])
        cur.execute(f"""
            SELECT b.*,
                   (SELECT count(*) FROM properties p WHERE p.building_id = b.id AND p.archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL) AS units_census,
                   (SELECT count(*) FROM property_accessories a JOIN properties u ON u.id = a.property_id
                     WHERE u.building_id = b.id AND u.archived_at IS NULL AND (to_jsonb(u)->>'deleted_at') IS NULL AND a.cadastral_status = 'unknown') AS accessories_unknown
              FROM buildings b WHERE {dove}
             ORDER BY {BUILDING_SORTS[sort]} LIMIT %s OFFSET %s""", params + [limit, offset])
        righe = [dict(r) for r in cur.fetchall()]
        riepiloghi = _riepiloghi(cur, righe)
        for r in righe:
            r["census_summary"] = riepiloghi[r["id"]]
        return {"items": righe, "total": totale, "limit": int(limit), "offset": int(offset)}


def get_building(ctx, building_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _assicura_083(cur)
        return _dettaglio_edificio(cur, _edificio(cur, agency_id, building_id))


def create_building(ctx, body) -> dict:
    agency_id = ctx.require_agency()
    data = body.model_dump()
    chiave = data.pop("client_request_id", None)
    conferma = data.pop("confirm_similar", False)
    impronta = _fingerprint(data) if chiave is not None else None
    _check_location(data)
    _check_cadastral(data)

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            replica = _replica_in(cur, "buildings", agency_id, chiave, impronta)
            if replica is not None:
                return {**_dettaglio_edificio(cur, replica), "replica": True}
            simili = _simili_edificio(cur, agency_id, data)
            if simili and not conferma:
                raise CensusConflict("Esiste gia' una palazzina simile: apri quella o salva comunque",
                                     "SIMILAR_FOUND", similar=simili)
            colonne = {**data, "agency_id": agency_id, "metadata": Json(data.get("metadata") or {}),
                       "client_request_id": str(chiave) if chiave else None,
                       "client_request_fingerprint": impronta}
            cols = list(colonne)
            cur.execute(f"INSERT INTO buildings ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) RETURNING *",
                        [colonne[c] for c in cols])
            riga = repository.row(cur.fetchone())
            return {**_dettaglio_edificio(cur, riga), "replica": False, "similar": simili}

    def leggi_replica():
        with core_cursor() as (_, cur):
            cur.execute("SELECT * FROM buildings WHERE agency_id = %s AND client_request_id = %s", (agency_id, str(chiave)))
            riga = repository.row(cur.fetchone())
            return None if riga is None else _dettaglio_edificio(cur, riga) | {
                "client_request_fingerprint": riga["client_request_fingerprint"]}

    return _con_replica(ctx, chiave, leggi_replica, operazione, impronta)


def update_building(ctx, building_id: int, body) -> dict:
    """Modifica dell'edificio. Un cambio di indirizzo si propaga SOLO alle
    unita' con `address_inherited = TRUE`, nella stessa transazione; le
    personalizzate non si toccano e la risposta lo dice."""
    agency_id = ctx.require_agency()
    data = body.model_dump(exclude_unset=True)
    _check_location(data)
    _check_cadastral(data)
    if "metadata" in data:
        data["metadata"] = Json(data.get("metadata") or {})

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            prima = _edificio(cur, agency_id, building_id, lock=True)
            propagate, custom = 0, 0
            if data:
                cur.execute(f"UPDATE buildings SET {','.join(f'{k}=%s' for k in data)}, updated_at = NOW() "
                            f"WHERE id = %s AND agency_id = %s RETURNING *", list(data.values()) + [building_id, agency_id])
                dopo = repository.row(cur.fetchone())
                if any(k in data and data[k] != prima.get(k) for k in _INDIRIZZO):
                    # le unita' ereditate seguono l'edificio (indirizzo materializzato);
                    # la descrizione generata segue, quella scritta a mano no
                    for u in _unita_ereditate(cur, building_id):
                        nuova = {**u, **{k: dopo.get(k) for k in _INDIRIZZO}}
                        titolo = generated_title(nuova) if u["title"] == generated_title(u) else u["title"]
                        cur.execute("UPDATE properties SET region=%s, province=%s, city=%s, microzone=%s, address=%s, "
                                    "civic_number=%s, postal_code=%s, title=%s, updated_at=NOW() WHERE id = %s",
                                    [dopo.get(k) for k in _INDIRIZZO] + [titolo, u["id"]])
                        propagate += 1
                    cur.execute("SELECT count(*) AS n FROM properties WHERE building_id = %s AND NOT address_inherited "
                                "AND archived_at IS NULL AND (to_jsonb(properties)->>'deleted_at') IS NULL", (building_id,))
                    custom = int(cur.fetchone()["n"])
            else:
                dopo = prima
            return {**_dettaglio_edificio(cur, dopo), "propagated_units": propagate, "custom_units": custom}

    return _esegui(operazione)


# ---------------------------------------------------------------------------
# UNITA' DI CENSIMENTO E PERTINENZE
# ---------------------------------------------------------------------------

def _identita_completa(data) -> bool:
    return all(_norm(data.get(k)) not in (None, "") for k in
               ("cadastral_municipality_code", "cadastral_sheet", "cadastral_parcel", "cadastral_subunit")) \
        and data.get("cadastral_section") is not None


def _duplicato_catastale(cur, agency_id, data, escluso=None):
    """L'identita' catastale COMPLETA gia' censita: il solo blocco vero."""
    if not _identita_completa(data):
        return None
    # DELETE-ARCH 2B2: un immobile nel Cestino non occupa l'identita' (indice 085)
    cur.execute("SELECT id, code, title FROM properties WHERE agency_id = %s AND id IS DISTINCT FROM %s "
                "AND (to_jsonb(properties)->>'deleted_at') IS NULL "
                "AND cadastral_municipality_code = %s AND cadastral_section = %s AND cadastral_sheet = %s "
                "AND cadastral_parcel = %s AND cadastral_subunit = %s LIMIT 1",
                (agency_id, escluso, _norm(data["cadastral_municipality_code"]), _norm(data["cadastral_section"]),
                 _norm(data["cadastral_sheet"]), _norm(data["cadastral_parcel"]), _norm(data["cadastral_subunit"])))
    return repository.row(cur.fetchone())


def _simili_unita(cur, agency_id, data, escluso=None) -> list[dict]:
    """Avvisi: stessa posizione nella palazzina (scala+piano+interno) o gli
    stessi identificativi con sezione NON conosciuta (NULL)."""
    simili = []
    if data.get("building_id") and (data.get("floor") or data.get("internal_number")):
        cur.execute("SELECT id, code, title, floor, staircase, internal_number FROM properties "
                    "WHERE agency_id = %s AND building_id = %s AND archived_at IS NULL AND (to_jsonb(properties)->>'deleted_at') IS NULL AND id IS DISTINCT FROM %s "
                    "AND coalesce(upper(btrim(staircase)), '') = coalesce(upper(btrim(%s)), '') "
                    "AND coalesce(upper(btrim(floor)), '') = coalesce(upper(btrim(%s)), '') "
                    "AND coalesce(upper(btrim(internal_number)), '') = coalesce(upper(btrim(%s)), '') ORDER BY id LIMIT 10",
                    (agency_id, data["building_id"], escluso, data.get("staircase"), data.get("floor"),
                     data.get("internal_number")))
        simili += [{**dict(r), "reason": "position"} for r in cur.fetchall()]
    if data.get("cadastral_section") is None and all(
            _norm(data.get(k)) not in (None, "") for k in
            ("cadastral_municipality_code", "cadastral_sheet", "cadastral_parcel", "cadastral_subunit")):
        cur.execute("SELECT id, code, title, cadastral_section FROM properties WHERE agency_id = %s AND id IS DISTINCT FROM %s "
                    "AND (to_jsonb(properties)->>'deleted_at') IS NULL "
                    "AND cadastral_municipality_code = %s AND cadastral_sheet = %s AND cadastral_parcel = %s "
                    "AND cadastral_subunit = %s ORDER BY id LIMIT 10",
                    (agency_id, escluso, _norm(data["cadastral_municipality_code"]), _norm(data["cadastral_sheet"]),
                     _norm(data["cadastral_parcel"]), _norm(data["cadastral_subunit"])))
        simili += [{**dict(r), "reason": "cadastral_section_unknown"} for r in cur.fetchall()]
    return simili


def _inserisci_unita(ctx, cur, agency_id, data, *, chiave, impronta, conferma) -> dict:
    """L'INSERT di una scheda census con tutte le regole, su un cursore gia'
    aperto (riusato da «E' separata»). Edificio e genitore DEVONO essere gia'
    stati verificati nello scope da chi chiama."""
    duplicato = _duplicato_catastale(cur, agency_id, data)
    if duplicato is not None:
        raise CensusConflict(f"Questo subalterno e' gia' censito come {_etichetta(duplicato)}",
                             "CADASTRAL_DUPLICATE", existing={"id": duplicato["id"], "code": duplicato.get("code")})
    simili = _simili_unita(cur, agency_id, data)
    if simili and not conferma:
        raise CensusConflict("Esiste gia' un'unita' simile: aprila o salva comunque", "SIMILAR_FOUND", similar=simili)
    tipo_scheda = data.pop("record_kind", None) or "census"            # CREAZIONE-GUIDATA-1
    colonne = {**data, "agency_id": agency_id, "record_kind": tipo_scheda, "commercial_status": "draft",
               "metadata": Json(data.get("metadata") or {}),
               "client_request_id": str(chiave) if chiave else None, "client_request_fingerprint": impronta}
    colonne["title"] = generated_title(colonne)
    colonne.pop("confirm_similar", None)
    cols = list(colonne)
    cur.execute(f"INSERT INTO properties ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) RETURNING *",
                [colonne[c] for c in cols])
    riga = repository.row(cur.fetchone())
    riga = repository._assign_generated_code(cur, riga["id"], agency_id) or riga
    cur.execute("INSERT INTO property_status_history(property_id,field_name,new_value,note) VALUES(%s,'commercial_status',%s,%s)",
                (riga["id"], "draft", "initial status (census)" if tipo_scheda == "census" else "initial status"))
    return {**riga, "similar": simili}


def _assegnazione_commerciale(ctx, data) -> None:
    """CREAZIONE-GUIDATA-1: una scheda COMMERCIALE nata dal censimento segue la
    regola di `POST /api/property/properties` (property/service.py::
    create_property): un agente se la assegna da se' (mai dal payload); chi puo'
    assegnare sceglie un agente attivo della stessa agenzia. Una scheda di
    censimento non porta assegnazione (lo rifiuta lo schema)."""
    from . import service as _service
    if data.get("record_kind") != "crm":
        data.pop("assigned_agent_id", None)
        return
    if getattr(ctx, "role", None) == "agent" and not getattr(ctx, "is_platform_admin", False):
        data["assigned_agent_id"] = ctx.user_id
        data["assigned_to"] = repository.assignable_agent_name(ctx, ctx.user_id)
        return
    if data.get("assigned_agent_id") is None:
        data.pop("assigned_agent_id", None)
        return
    _service._apply_assignment(ctx, data)


def _prepara_unita(cur, agency_id, data) -> None:
    """Edificio e genitore nello scope; indirizzo ereditato o proprio."""
    if data.get("building_id") is not None:
        edificio = _edificio(cur, agency_id, data["building_id"], share=True)
        if not any(data.get(k) is not None for k in _INDIRIZZO):
            for k in _INDIRIZZO:
                data[k] = edificio.get(k)
            data["address_inherited"] = True
        else:
            data["address_inherited"] = False
    else:
        data["address_inherited"] = False
        if data.get("whole_building"):
            raise CensusInvalid("'Stabile intero' richiede la palazzina")
    if data.get("parent_property_id") is not None:
        genitore = _unita(cur, agency_id, data["parent_property_id"])
        if genitore.get("parent_property_id") is not None or genitore.get("is_pertinenza"):
            raise CensusInvalid("L'unita' scelta e' gia' una pertinenza: non puo' avere pertinenze", "LINK_INVALID")
    _natura(cur, data)


def _natura(cur, data) -> None:
    """PERTINENZE-1: la natura della scheda nuova. Collegata a una principale
    o dichiarata pertinenza -> `is_pertinenza`; il tipo solo su una
    pertinenza. Senza la 089: una pertinenza DICHIARATA (tipo o «da
    collegare») si rifiuta, perche' la natura andrebbe persa; una collegata
    come prima (la relazione basta a dirla pertinenza)."""
    dichiarata = bool(data.get("is_pertinenza") or data.get("pertinenza_kind"))
    if dichiarata and data.get("whole_building"):
        raise CensusInvalid("Uno stabile intero non e' una pertinenza", "LINK_INVALID")
    if not _ha_089(cur):
        if dichiarata:
            raise CensusConflict(PERTINENZE_NOT_INSTALLED_MESSAGE, "PERTINENZE_NOT_INSTALLED")
        data.pop("is_pertinenza", None)
        data.pop("pertinenza_kind", None)
        return
    if dichiarata or data.get("parent_property_id") is not None:
        data["is_pertinenza"] = True
    else:
        data.pop("is_pertinenza", None)
        data.pop("pertinenza_kind", None)


def create_unit(ctx, body) -> dict:
    agency_id = ctx.require_agency()
    data = body.model_dump()
    chiave = data.pop("client_request_id", None)
    conferma = data.pop("confirm_similar", False)
    # PERTINENZE-1: i campi nuovi entrano nell'impronta solo se indicati (un
    # client di prima ha la stessa impronta di prima)
    if not data.get("is_pertinenza"):
        data.pop("is_pertinenza", None)
    if data.get("pertinenza_kind") is None:
        data.pop("pertinenza_kind", None)
    impronta = _fingerprint(data) if chiave is not None else None
    _check_location(data)
    _check_cadastral(data)
    _assegnazione_commerciale(ctx, data)          # 403/400 PRIMA di scrivere, come POST /properties

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            replica = _replica_in(cur, "properties", agency_id, chiave, impronta)
            if replica is not None:
                return {**replica, "replica": True}
            dati = dict(data)
            _prepara_unita(cur, agency_id, dati)
            try:
                riga = _inserisci_unita(ctx, cur, agency_id, dati, chiave=chiave, impronta=impronta, conferma=conferma)
            except CensusConflict as exc:
                # fra il controllo della replica e i controlli di duplicato una
                # richiesta gemella puo' essere stata committata: la stessa
                # chiave vince sull'avviso, e la risposta e' la sua riga
                replica = _replica_in(cur, "properties", agency_id, chiave, impronta)
                if replica is None:
                    raise
                return {**replica, "replica": True}
            return {**riga, "replica": False}

    def leggi_replica():
        with core_cursor() as (_, cur):
            cur.execute("SELECT * FROM properties WHERE agency_id = %s AND client_request_id = %s "
                        "AND (to_jsonb(properties)->>'deleted_at') IS NULL", (agency_id, str(chiave)))
            return repository.row(cur.fetchone())

    return _con_replica(ctx, chiave, leggi_replica, operazione, impronta)


def get_census(ctx, property_id: int) -> dict:
    """La parte di censimento della scheda: edificio, unita' principale,
    pertinenze collegate (relazione corrente) e accessori."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _assicura_083(cur)
        unita = _unita(cur, agency_id, property_id, archiviata_ok=True)
        edificio = genitore = None
        if unita.get("building_id") is not None:
            cur.execute("SELECT id, name, building_type, city, address, civic_number, units_declared FROM buildings WHERE id = %s",
                        (unita["building_id"],))
            edificio = repository.row(cur.fetchone())
        if unita.get("parent_property_id") is not None:
            cur.execute("SELECT id, code, title, property_type, record_kind, commercial_status, archived_at, building_id "
                        "FROM properties WHERE id = %s AND (to_jsonb(properties)->>'deleted_at') IS NULL",
                        (unita["parent_property_id"],))
            genitore = repository.row(cur.fetchone())
            if genitore is not None:
                genitore["same_building"] = genitore.get("building_id") == unita.get("building_id")
        cur.execute(f"SELECT {_colonne_unita('p')} FROM properties p WHERE parent_property_id = %s AND archived_at IS NULL AND (to_jsonb(p)->>'deleted_at') IS NULL ORDER BY id",
                    (property_id,))
        pertinenze = [dict(r) for r in cur.fetchall()]
        for x in pertinenze:
            x["same_building"] = x.get("building_id") == unita.get("building_id")
        cur.execute("SELECT * FROM property_accessories WHERE property_id = %s ORDER BY id", (property_id,))
        accessori = [dict(r) for r in cur.fetchall()]
        pertinenza = is_pertinenza(unita)
        # PERTINENZE-1: per una pertinenza da collegare, le unita' principali
        # della STESSA palazzina (relazione reale, mai l'indirizzo) come prima
        # scelta; le altre si cercano per codice o indirizzo
        candidate = []
        if pertinenza and unita.get("parent_property_id") is None and unita.get("building_id") is not None:
            cur.execute(f"SELECT {_colonne_unita('p')} FROM properties p WHERE p.building_id = %s AND p.id <> %s "
                        f"AND p.archived_at IS NULL AND p.commercial_status IS DISTINCT FROM 'archived' "
                        f"AND {_property_trash.live('p')} AND NOT {pertinenza_sql('p')} AND NOT p.whole_building "
                        "ORDER BY p.floor NULLS LAST, p.internal_number NULLS LAST, p.id", (unita["building_id"], property_id))
            candidate = [dict(r) for r in cur.fetchall()]
        metadata = unita.get("metadata") or {}
        return {"id": unita["id"], "code": unita.get("code"), "record_kind": unita["record_kind"],
                "address_inherited": unita["address_inherited"], "whole_building": unita["whole_building"],
                "building": edificio, "parent": genitore, "pertinenze": pertinenze, "accessories": accessori,
                "accessories_unknown": sum(1 for a in accessori if a["cadastral_status"] == "unknown"),
                # PERTINENZE-1
                "is_pertinenza": pertinenza, "pertinenza_kind": unita.get("pertinenza_kind"),
                "pertinenza_unlinked": pertinenza and unita.get("parent_property_id") is None,
                "from_accessory": metadata.get("from_accessory") if isinstance(metadata, dict) else None,
                "link_candidates": candidate}


def link_pertinenza(ctx, property_id: int, body) -> dict:
    """«Collega esistente»: `parent_property_id` sull'immobile gia' censito.
    Un collegamento per transazione; la 083 garantisce agenzia, profondita' 1
    e niente cicli, qui i messaggi leggibili."""
    agency_id = ctx.require_agency()
    pertinenza_id = int(body.pertinenza_id)
    if pertinenza_id == property_id:
        raise CensusInvalid("Un immobile non puo' essere pertinenza di se stesso", "LINK_INVALID")

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            genitore = _unita(cur, agency_id, property_id, lock=True)
            figlia = _unita(cur, agency_id, pertinenza_id, lock=True)
            if genitore.get("parent_property_id") is not None or genitore.get("is_pertinenza"):
                raise CensusInvalid("L'unita' scelta e' gia' una pertinenza: non puo' avere pertinenze", "LINK_INVALID")
            if figlia.get("whole_building"):
                raise CensusInvalid("Uno stabile intero non puo' essere una pertinenza", "LINK_INVALID")
            if figlia.get("parent_property_id") == property_id:
                return {"property_id": property_id, "pertinenza": figlia, "linked": False}
            if figlia.get("parent_property_id") is not None:
                raise CensusConflict(f"{_etichetta(figlia)} e' gia' pertinenza di un'altra unita': scollegala prima",
                                     "ALREADY_LINKED")
            cur.execute("SELECT 1 FROM properties WHERE parent_property_id = %s AND archived_at IS NULL AND (to_jsonb(properties)->>'deleted_at') IS NULL LIMIT 1", (pertinenza_id,))
            if cur.fetchone():
                raise CensusInvalid("L'immobile ha pertinenze collegate: non puo' diventare una pertinenza", "LINK_INVALID")
            # PERTINENZE-1: collegata = pertinenza, anche dopo uno scollegamento.
            # Edificio, proprietari, documenti: invariati.
            natura = ", is_pertinenza = TRUE" if _ha_089(cur) else ""
            cur.execute(f"UPDATE properties SET parent_property_id = %s{natura}, updated_at = NOW() WHERE id = %s RETURNING *",
                        (property_id, pertinenza_id))
            figlia = repository.row(cur.fetchone())
            _attivita_sistema(ctx, cur, property_id, f"Pertinenza {_etichetta(figlia)} collegata", pertinenza_id=pertinenza_id)
            _attivita_sistema(ctx, cur, pertinenza_id, f"Collegata come pertinenza di {_etichetta(genitore)}", parent_id=property_id)
            return {"property_id": property_id, "pertinenza": figlia, "linked": True}

    return _esegui(operazione)


def _scollega(ctx, cur, genitore, figlia, motivo: str) -> dict:
    """Azzera la relazione corrente e scrive lo storico su entrambe.

    DELETE-ARCH 2B2 (REVIEW 1): un genitore nel Cestino e' congelato e non
    riceve attivita' nuove; lo storico dello scollegamento resta sulla
    pertinenza, che e' viva."""
    # PERTINENZE-1: resta una pertinenza («da collegare»), non diventa una
    # principale; record, edificio, proprietari e documenti invariati
    natura = ", is_pertinenza = TRUE" if _ha_089(cur) else ""
    cur.execute(f"UPDATE properties SET parent_property_id = NULL{natura}, updated_at = NOW() WHERE id = %s RETURNING *",
                (figlia["id"],))
    aggiornata = repository.row(cur.fetchone())
    if genitore.get("deleted_at") is None:
        _attivita_sistema(ctx, cur, genitore["id"], f"Pertinenza {_etichetta(figlia)} scollegata: {motivo}",
                          pertinenza_id=figlia["id"], reason=motivo)
    _attivita_sistema(ctx, cur, figlia["id"], f"Scollegata da {_etichetta(genitore)}: {motivo}",
                      parent_id=genitore["id"], reason=motivo)
    return aggiornata


def unlink_pertinenza(ctx, property_id: int, pertinenza_id: int) -> dict:
    agency_id = ctx.require_agency()

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            genitore = _unita(cur, agency_id, property_id, lock=True, archiviata_ok=True)
            figlia = _unita(cur, agency_id, pertinenza_id, lock=True, archiviata_ok=True)
            if figlia.get("parent_property_id") != property_id:
                raise NotFoundError(f"property {pertinenza_id} is not a pertinenza of {property_id}")
            return {"property_id": property_id, "pertinenza": _scollega(ctx, cur, genitore, figlia, "scollegamento manuale")}

    return _esegui(operazione)


def detach_on_close(ctx, cur, riga: dict, nuovo_stato: str) -> None:
    """Chiamata da property/repository.update_property, SULLA STESSA
    transazione, quando una pertinenza collegata viene venduta da sola o
    archiviata (§0 p.6): la relazione corrente non mente mai."""
    if riga.get("parent_property_id") is None:
        return
    # Anche un genitore nel Cestino: la pertinenza viva si scollega comunque
    # (DELETE-ARCH 2B2, REVIEW 1), senza scrivere sul genitore congelato.
    cur.execute(f"SELECT id, code, {_property_trash.deleted_at_sql('properties')} AS deleted_at "
                "FROM properties WHERE id = %s", (riga["parent_property_id"],))
    genitore = repository.row(cur.fetchone())
    if genitore is None:
        return
    motivo = "venduta autonomamente" if nuovo_stato == "sold" else "archiviata"
    _scollega(ctx, cur, genitore, riga, motivo)


# ---------------------------------------------------------------------------
# PRESA IN CARICO E ANNULLAMENTO
# ---------------------------------------------------------------------------

def take_in_charge(ctx, property_id: int, body) -> dict:
    """census -> crm: stessa riga, stesso codice, SOLO `record_kind` (il
    trigger della 083 rifiuta ogni altro cambio nello stesso UPDATE). Con
    `include_pertinenze` anche le pertinenze census collegate."""
    agency_id = ctx.require_agency()

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            unita = _unita(cur, agency_id, property_id, lock=True)
            if unita["record_kind"] != "census":
                raise CensusConflict(NOT_CENSUS, "NOT_CENSUS")
            cur.execute("UPDATE properties SET record_kind = 'crm', updated_at = NOW() WHERE id = %s RETURNING *", (property_id,))
            aggiornata = repository.row(cur.fetchone())
            _attivita_sistema(ctx, cur, property_id, "Presa in carico: dal censimento al lavoro commerciale")
            prese = []
            if body.include_pertinenze:
                cur.execute("SELECT id, code FROM properties WHERE parent_property_id = %s AND record_kind = 'census' "
                            "AND archived_at IS NULL AND (to_jsonb(properties)->>'deleted_at') IS NULL ORDER BY id FOR UPDATE", (property_id,))
                for figlia in [dict(r) for r in cur.fetchall()]:
                    cur.execute("UPDATE properties SET record_kind = 'crm', updated_at = NOW() WHERE id = %s", (figlia["id"],))
                    _attivita_sistema(ctx, cur, figlia["id"], f"Presa in carico insieme a {_etichetta(aggiornata)}",
                                      parent_id=property_id)
                    prese.append(figlia["id"])
            return {**aggiornata, "pertinenze_taken": prese}

    return _esegui(operazione)


def _collegamenti(cur, property_id: int) -> list[str]:
    """Le tabelle che puntano all'immobile e hanno almeno una riga: lette dal
    catalogo delle FK, cosi' un collegamento futuro non sfugge."""
    cur.execute("""
        SELECT c.conrelid::regclass::text AS tabella, a.attname AS colonna
          FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
         WHERE c.contype = 'f' AND c.confrelid = 'properties'::regclass ORDER BY 1, 2""")
    trovati = []
    for tabella, colonna in [(r["tabella"], r["colonna"]) for r in cur.fetchall()]:
        if tabella in _STORICI_PROPRI:
            continue
        cur.execute(f"SELECT 1 FROM {tabella} WHERE {colonna} = %s LIMIT 1", (property_id,))
        if cur.fetchone():
            trovati.append(tabella)
    return trovati


def undo_create(ctx, property_id: int) -> dict:
    """«Annulla» del toast (§0 p.5): archiviazione CONDIZIONATA sotto lock di
    riga - solo se ancora census, mai modificata e senza collegamenti."""
    agency_id = ctx.require_agency()

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            unita = _unita(cur, agency_id, property_id, lock=True)
            if unita["record_kind"] != "census" or unita["updated_at"] != unita["created_at"]:
                raise CensusConflict(UNDO_NOT_POSSIBLE, "UNDO_NOT_POSSIBLE")
            collegati = _collegamenti(cur, property_id)
            if collegati:
                raise CensusConflict(UNDO_NOT_POSSIBLE, "UNDO_NOT_POSSIBLE", linked=collegati)
            cur.execute("UPDATE properties SET commercial_status = 'archived', archived_at = NOW(), updated_at = NOW() "
                        "WHERE id = %s RETURNING *", (property_id,))
            riga = repository.row(cur.fetchone())
            cur.execute("INSERT INTO property_status_history(property_id,field_name,old_value,new_value,note) "
                        "VALUES(%s,'commercial_status','draft','archived','undo-create')", (property_id,))
            return riga

    return _esegui(operazione)


# ---------------------------------------------------------------------------
# ACCESSORI («Si' / No / Non lo so», «Chiarisci»)
# ---------------------------------------------------------------------------

def _accessori(cur, property_id) -> list[dict]:
    cur.execute("SELECT * FROM property_accessories WHERE property_id = %s ORDER BY id", (property_id,))
    return [dict(r) for r in cur.fetchall()]


def create_accessory(ctx, property_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    data = body.model_dump()
    if data.get("quantity") is None:
        data.pop("quantity", None)          # CATALOGO-CANONICO-1: impronta invariata senza quantita'
    chiave = data.pop("client_request_id", None)
    impronta = _fingerprint(data) if chiave is not None else None

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            _unita(cur, agency_id, property_id)
            replica = _replica_in(cur, "property_accessories", agency_id, chiave, impronta, property_id=property_id)
            if replica is not None:
                return {**replica, "replica": True}
            # CATALOGO-CANONICO-1: `quantity` (087) entra nella statement solo se indicata.
            extra_col, extra_val = ((", quantity", [data["quantity"]]) if data.get("quantity") is not None else ("", []))
            cur.execute("INSERT INTO property_accessories (property_id, kind, cadastral_status, surface_sqm, notes, "
                        f"client_request_id, client_request_fingerprint{extra_col}) "
                        f"VALUES (%s,%s,%s,%s,%s,%s,%s{',%s' * len(extra_val)}) RETURNING *",
                        [property_id, data["kind"], data["cadastral_status"], data.get("surface_sqm"), data.get("notes"),
                         str(chiave) if chiave else None, impronta, *extra_val])
            return {**repository.row(cur.fetchone()), "replica": False}

    def leggi_replica():
        with core_cursor() as (_, cur):
            cur.execute("SELECT * FROM property_accessories WHERE property_id = %s AND client_request_id = %s",
                        (property_id, str(chiave)))
            return repository.row(cur.fetchone())

    return _con_replica(ctx, chiave, leggi_replica, operazione, impronta)


def update_accessory(ctx, property_id: int, accessory_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    data = body.model_dump(exclude_unset=True)

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            _unita(cur, agency_id, property_id)
            _accessorio(cur, property_id, accessory_id, lock=True)
            if not data:
                return _accessorio(cur, property_id, accessory_id)
            cur.execute(f"UPDATE property_accessories SET {','.join(f'{k}=%s' for k in data)}, updated_at = NOW() "
                        f"WHERE id = %s RETURNING *", list(data.values()) + [accessory_id])
            return repository.row(cur.fetchone())

    return _esegui(operazione)


def delete_accessory(ctx, property_id: int, accessory_id: int) -> None:
    agency_id = ctx.require_agency()

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            unita = _unita(cur, agency_id, property_id)
            # DELETE-ARCH Fase 0: stesso accesso dell'immobile (D10).
            from . import lifecycle as _lifecycle
            _lifecycle.require_manage(ctx, unita)
            _accessorio(cur, property_id, accessory_id, lock=True)
            cur.execute("DELETE FROM property_accessories WHERE id = %s", (accessory_id,))

    return _esegui(operazione)


def resolve_accessory(ctx, property_id: int, accessory_id: int, body) -> dict:
    """«Chiarisci»: `included` -> resta accessorio, badge via; `separate` ->
    nella STESSA transazione nasce la pertinenza collegata (tipo, mq e note
    travasati; o si collega un immobile gia' censito) e l'accessorio viene
    rimosso. Se la creazione fallisce, l'accessorio resta com'era.

    IDEMPOTENZA, distinta per ramo:
      * `separate` con creazione: `client_request_id` + impronta sulla riga
        creata. La replica si cerca PRIMA dell'accessorio (che dopo il primo
        successo non esiste piu') e sempre DOPO aver verificato che l'unita'
        sia nell'agenzia dello scope;
      * `separate` con `existing_property_id`: nessuna riga nuova porta una
        chiave, quindi la chiave NON e' ammessa (422). La ripetizione e'
        riconosciuta da una PROVA PERSISTENTE della conversione specifica,
        scritta nella stessa transazione come interazione di sistema
        (`activities`: agenzia, immobile principale, `accessory_id`,
        immobile collegato, operazione `accessory_resolve_link`). Un
        `accessory_id` inventato, di un'altra conversione, o un accessorio
        eliminato senza conversione -> 404; una pertinenza poi scollegata
        NON viene ricollegata dal retry (409 ALREADY_RESOLVED);
      * `included`: la chiave non e' ammessa; ripetuto riscrive lo stesso
        stato (l'accessorio esiste ancora).
    """
    agency_id = ctx.require_agency()
    data = body.model_dump()
    chiave = data.pop("client_request_id", None)
    esito = data.pop("outcome")
    esistente_id = data.pop("existing_property_id", None)
    tipo = data.pop("property_type", None)
    impronta = _fingerprint({**data, "property_id": property_id, "accessory_id": accessory_id,
                             "property_type": tipo, "outcome": esito}) if chiave is not None else None
    _check_cadastral(data)

    def prova_di_conversione(cur, figlia):
        """La conversione SPECIFICA (questa unita', questo accessorio, questo
        immobile) e' gia' avvenuta? Lo dice lo storico scritto nella stessa
        transazione della conversione, mai il solo `parent_property_id`."""
        cur.execute("SELECT 1 FROM activities WHERE agency_id = %s AND property_id = %s AND activity_type = 'system'"
                    "   AND metadata ->> 'operation' = 'accessory_resolve_link'"
                    "   AND metadata ->> 'accessory_id' = %s AND metadata ->> 'pertinenza_id' = %s LIMIT 1",
                    (agency_id, property_id, str(accessory_id), str(figlia["id"])))
        return cur.fetchone() is not None

    def operazione():
        with core_cursor(commit=True) as (_, cur):
            _assicura_083(cur)
            unita = _unita(cur, agency_id, property_id, lock=True)          # autorizzazione: prima di tutto
            if esito == "separate" and esistente_id is None:
                replica = _replica_in(cur, "properties", agency_id, chiave, impronta)
                if replica is not None and replica.get("parent_property_id") == property_id:
                    return {"accessory": None, "pertinenza": replica, "replica": True}
            if esito == "separate" and esistente_id is not None:
                figlia = _unita(cur, agency_id, int(esistente_id), lock=True)
                cur.execute("SELECT 1 FROM property_accessories WHERE id = %s AND property_id = %s", (accessory_id, property_id))
                if cur.fetchone() is None and prova_di_conversione(cur, figlia):
                    if figlia.get("parent_property_id") != property_id:
                        # conversione avvenuta, pertinenza poi scollegata: il retry non ricollega nulla
                        raise CensusConflict("Conversione gia' eseguita: la pertinenza e' stata scollegata in seguito",
                                             "ALREADY_RESOLVED")
                    return {"accessory": None, "pertinenza": figlia, "replica": True}
                # accessorio assente senza prova della conversione: 404 (sotto, da _accessorio)
            accessorio = _accessorio(cur, property_id, accessory_id, lock=True)
            istantanea = _accessorio_istantanea(accessorio)
            if esito == "included":
                cur.execute("UPDATE property_accessories SET cadastral_status = 'included', updated_at = NOW() "
                            "WHERE id = %s RETURNING *", (accessory_id,))
                return {"accessory": repository.row(cur.fetchone()), "pertinenza": None,
                        "replica": accessorio["cadastral_status"] == "included"}
            if unita.get("parent_property_id") is not None:
                raise CensusInvalid("L'unita' e' gia' una pertinenza: non puo' avere pertinenze", "LINK_INVALID")
            if esistente_id is not None:
                if figlia.get("parent_property_id") not in (None, property_id):
                    raise CensusConflict(f"{_etichetta(figlia)} e' gia' pertinenza di un'altra unita': scollegala prima",
                                         "ALREADY_LINKED")
                cur.execute("SELECT 1 FROM properties WHERE parent_property_id = %s AND archived_at IS NULL AND (to_jsonb(properties)->>'deleted_at') IS NULL LIMIT 1", (figlia["id"],))
                if cur.fetchone():
                    raise CensusInvalid("L'immobile ha pertinenze collegate: non puo' diventare una pertinenza", "LINK_INVALID")
                if figlia.get("whole_building"):
                    raise CensusInvalid("Uno stabile intero non puo' essere una pertinenza", "LINK_INVALID")
                note = "\n".join(x for x in (figlia.get("internal_notes"), accessorio.get("notes")) if x) or None
                mq = figlia.get("surface_sqm") if figlia.get("surface_sqm") is not None else accessorio.get("surface_sqm")
                # PERTINENZE-1: natura, tipo (se non ce l'ha gia') e provenienza
                # dell'accessorio conservati sulla scheda collegata
                natura = (", is_pertinenza = TRUE, pertinenza_kind = coalesce(pertinenza_kind, %s)"
                          if _ha_089(cur) else "")
                cur.execute(f"UPDATE properties SET parent_property_id = %s, internal_notes = %s, surface_sqm = %s, "
                            f"metadata = coalesce(metadata, '{{}}'::jsonb) || %s{natura}, "
                            "updated_at = NOW() WHERE id = %s RETURNING *",
                            [property_id, note, mq, Json({"from_accessory": istantanea}),
                             *([accessorio["kind"]] if natura else []), figlia["id"]])
                figlia = repository.row(cur.fetchone())
                # la PROVA della conversione: operazione, accessorio e immobile collegato
                _attivita_sistema(ctx, cur, property_id, f"Pertinenza {_etichetta(figlia)} collegata (da accessorio «da chiarire»)",
                                  operation="accessory_resolve_link", accessory_id=accessory_id, pertinenza_id=figlia["id"],
                                  accessory=istantanea)
                _attivita_sistema(ctx, cur, figlia["id"], f"Collegata come pertinenza di {_etichetta(unita)}",
                                  operation="accessory_resolve_link", accessory_id=accessory_id, parent_id=property_id,
                                  accessory=istantanea)
            else:
                dati = {**data, "property_type": tipo or ACCESSORY_TO_TYPE.get(accessorio["kind"], "other"),
                        "parent_property_id": property_id, "building_id": unita.get("building_id"),
                        "surface_sqm": accessorio.get("surface_sqm"), "internal_notes": accessorio.get("notes"),
                        "floor": unita.get("floor"), "staircase": unita.get("staircase"),
                        # PERTINENZE-1: la provenienza dell'accessorio (anche «Dal sito») resta
                        "metadata": {"from_accessory": istantanea}}
                if _ha_089(cur):
                    dati.update({"is_pertinenza": True, "pertinenza_kind": accessorio["kind"]})
                _prepara_unita(cur, agency_id, dati)
                figlia = _inserisci_unita(ctx, cur, agency_id, dati, chiave=chiave, impronta=impronta, conferma=True)
                _attivita_sistema(ctx, cur, property_id, f"Pertinenza {_etichetta(figlia)} creata da accessorio «da chiarire»",
                                  operation="accessory_resolve_create", accessory_id=accessory_id, pertinenza_id=figlia["id"],
                                  accessory=istantanea)
            cur.execute("DELETE FROM property_accessories WHERE id = %s", (accessory_id,))
            return {"accessory": None, "pertinenza": figlia, "replica": False}

    def leggi_replica():
        with core_cursor() as (_, cur):
            _unita(cur, agency_id, property_id)                                 # autorizzazione anche qui
            cur.execute("SELECT * FROM properties WHERE agency_id = %s AND client_request_id = %s "
                        "AND (to_jsonb(properties)->>'deleted_at') IS NULL", (agency_id, str(chiave)))
            riga = repository.row(cur.fetchone())
            return None if riga is None else {"accessory": None, "pertinenza": riga,
                                              "client_request_fingerprint": riga["client_request_fingerprint"]}

    return _con_replica(ctx, chiave, leggi_replica, operazione, impronta)
