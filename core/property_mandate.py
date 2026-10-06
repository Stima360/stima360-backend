"""FIX-MANDATE-1 - che cos'e' un incarico, in un posto solo.

Non esiste una tabella "incarichi": un incarico vive sulla riga `properties`
(`acquisition_id`, `mandate_type`, `mandate_start`, `mandate_end`,
`commercial_status`). Fino a FIX-MANDATE-1 ogni modulo ne dava una
definizione propria (Cestino: un campo qualsiasi; Incarichi: acquisizione +
tipo + inizio; rimozione proprietario: acquisizione o tipo; scadenze e FLOW:
la sola `mandate_end`), e la stessa riga poteva essere un incarico per il
Cestino e non esserlo per la sezione Incarichi.

DEFINIZIONE CANONICA - un incarico ESISTE (e' reale) quando:

  * ORIGINE "acquisition": `acquisition_id` valorizzato. Dalla 081 e' l'unico
    modo di generarne uno (`POST /api/acquisitions/{id}/mandate`, acquisizione
    `acquired`, evento `mandate_created`) e l'origine e' permanente (trigger
    `trg_properties_mandate_origin`). Resta un incarico anche se in seguito
    tipo o data d'inizio sono stati azzerati: e' un incarico con DATI
    INCOMPLETI, non un non-incarico.
  * ORIGINE "historical": senza acquisizione, tipo E data d'inizio entrambi
    valorizzati. Sono gli incarichi registrati prima della 081
    (grandfathering, nessuna acquisizione artificiale): il dato completo e'
    la sola prova disponibile, ed e' quella che il sistema ha sempre chiesto.
  * ORIGINE "signed_link": una firma d'incarico registrata nel ponte LMC-15
    (`stima_acquisitions.mandate_signed_at`, migration 070: «il mandato dice
    "l'incarico e' stato firmato, quel giorno"»). La scrive SOLO
    `POST /api/acquisition/links/{id}/mandate` (acquisition/repository.py::
    record_mandate): data reale della firma, operatore che la registra,
    riferimento facoltativo; i tre campi stanno insieme (CHECK della 070) e
    non si azzerano mai. Revocare il collegamento corregge l'attribuzione
    alla stima, non annulla la firma (070): conta anche su un link revocato.
    Non e' sulla riga `properties`: il chiamante la legge con
    `signed_link_mandate(cur, property_id)` e la passa come
    `prop["signed_mandate"]`; nell'SQL e' l'EXISTS di `real_mandate_sql`.

DATI PARZIALI (senza acquisizione, senza tipo + inizio: solo il tipo, solo
una data, o solo lo stato `mandate`) NON sono un incarico. Restano storia
dell'immobile, e chi li vuole spostare nel Cestino da agente deve passare da
un amministratore (property/lifecycle.py::protected_history).

L'ESISTENZA e' distinta dallo STATO (`mandate_state`: in corso, scaduto,
concluso con la vendita, ritirato, su immobile archiviato). Le viste possono
filtrare per stato o per origine (la sezione Incarichi elenca quelli nati da
un'acquisizione); un incarico storico o scaduto non perde la protezione
perche' una vista non lo mostra.

Modulo foglia (nessun import applicativo), come `core/property_trash.py`:
lo usano `property/` e `flow/` senza nuove dipendenze fra i domini.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ORIGIN_ACQUISITION = "acquisition"
ORIGIN_HISTORICAL = "historical"
ORIGIN_SIGNED_LINK = "signed_link"

STATE_ACTIVE = "active"
STATE_EXPIRED = "expired"
STATE_SOLD = "sold"
STATE_WITHDRAWN = "withdrawn"
STATE_ARCHIVED = "archived"

_ROMA = ZoneInfo("Europe/Rome")


def real_mandate_sql(alias: str = "p") -> str:
    """Predicato SQL: sulla riga `alias` di `properties` esiste un incarico reale
    (le tre origini). La firma LMC-15 e' legata all'immobile per id: il
    collegamento non ha un'agenzia propria, e la 070 impone che stima e
    immobile siano della stessa agenzia; la riga `alias` e' gia' nello scope."""
    a = alias
    return (f"({a}.acquisition_id IS NOT NULL OR "
            f"(NULLIF(BTRIM({a}.mandate_type), '') IS NOT NULL AND {a}.mandate_start IS NOT NULL) OR "
            f"EXISTS (SELECT 1 FROM stima_acquisitions sa_m WHERE sa_m.property_id = {a}.id "
            f"AND sa_m.mandate_signed_at IS NOT NULL))")


def acquisition_mandate_sql(alias: str = "p") -> str:
    """Predicato SQL dell'origine "acquisition" da sola: e' un incarico reale
    (vedi `real_mandate_sql`) qualunque siano tipo e date. E' quello della
    sezione Incarichi, che gestisce solo questa origine."""
    return f"{alias}.acquisition_id IS NOT NULL"


def signed_link_mandate(cur, property_id) -> dict | None:
    """La firma d'incarico piu' recente registrata nel ponte LMC-15 per
    l'immobile, o None. Senza la 070 (schemi ridotti) non c'e' firma."""
    cur.execute("SELECT to_regclass('public.stima_acquisitions') IS NOT NULL AS c")
    riga = cur.fetchone()
    if not (riga["c"] if hasattr(riga, "get") else riga[0]):
        return None
    cur.execute("SELECT id, stima_id_snapshot, link_status, mandate_signed_at, mandate_reference "
                "FROM stima_acquisitions WHERE property_id = %s AND mandate_signed_at IS NOT NULL "
                "ORDER BY mandate_signed_at DESC, id DESC LIMIT 1", (property_id,))
    riga = cur.fetchone()
    return dict(riga) if riga is not None else None


def _testo(valore) -> bool:
    return valore is not None and str(valore).strip() != ""


def mandate_origin(prop: dict | None) -> str | None:
    """'acquisition', 'historical', 'signed_link' o None (nessun incarico)."""
    if not prop:
        return None
    if prop.get("acquisition_id") is not None:
        return ORIGIN_ACQUISITION
    if _testo(prop.get("mandate_type")) and prop.get("mandate_start") is not None:
        return ORIGIN_HISTORICAL
    if prop.get("signed_mandate"):
        return ORIGIN_SIGNED_LINK
    return None


def is_real_mandate(prop: dict | None) -> bool:
    return mandate_origin(prop) is not None


def has_partial_mandate_data(prop: dict | None) -> bool:
    """Qualche dato d'incarico senza che l'incarico esista."""
    if not prop or is_real_mandate(prop):
        return False
    return (_testo(prop.get("mandate_type")) or prop.get("mandate_start") is not None
            or prop.get("mandate_end") is not None or prop.get("commercial_status") == "mandate")


def missing_fields(prop: dict | None) -> list[str]:
    """I dati essenziali (tipo, inizio) che mancano a un incarico nato da
    un'acquisizione: e' l'origine che li porta sempre con se'."""
    if mandate_origin(prop) != ORIGIN_ACQUISITION:
        return []
    return [c for c in ("mandate_type", "mandate_start")
            if (not _testo(prop.get(c)) if c == "mandate_type" else prop.get(c) is None)]


def as_date(valore) -> date | None:
    if valore is None:
        return None
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    return date.fromisoformat(str(valore)[:10])


def today_rome() -> date:
    return datetime.now(_ROMA).date()


def mandate_state(prop: dict, oggi: date | None = None) -> str:
    """Lo stato di un incarico reale (non dice se esiste: vedi is_real_mandate)."""
    if prop.get("archived_at") is not None or prop.get("commercial_status") == "archived":
        return STATE_ARCHIVED
    if prop.get("commercial_status") == "sold":
        return STATE_SOLD
    if prop.get("commercial_status") == "withdrawn":
        return STATE_WITHDRAWN
    fine = as_date(prop.get("mandate_end"))
    if fine is not None and fine < (oggi or today_rome()):
        return STATE_EXPIRED
    return STATE_ACTIVE
