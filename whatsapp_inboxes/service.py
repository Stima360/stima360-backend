"""WHATSAPP DUE CASELLE - le caselle (numeri riceventi) dell'agenzia.

Una casella e' un `phone_number_id` instradato all'agenzia dal server
(`WHATSAPP_INBOUND_ROUTES`, BLOCCO-4A). Le sue IMPOSTAZIONI stanno nella
tabella `whatsapp_inboxes` (migrazione 130): etichetta, tipo, automazioni e
accesso. Un numero instradato ma senza riga si comporta come sempre: accesso
di agenzia (titolare/amministratori tutto, un agente i contatti assegnati) e
automazioni attive. Una casella `restricted` la vedono il titolare e gli
operatori elencati in `whatsapp_inbox_access`; una casella `disabled` non la
vede nessuno (i messaggi restano salvati).

Configura solo il titolare dell'agenzia (o il Super Admin che vi opera).
Nessuna rotta invia messaggi o parla con Meta.
"""
from __future__ import annotations

from core.database import core_cursor
from inbound_messages import routing
from operator_auth.permissions import sees_all_agency_records

from . import repository
from .enums import (
    ACCESS_AGENCY,
    ACCESS_MODES,
    FORBIDDEN_MESSAGE,
    KIND_CLOUD_API,
    KINDS,
    LABEL_MAX,
    LABEL_TAKEN_MESSAGE,
    MEMBER_NOT_FOUND_MESSAGE,
    NOT_INSTALLED_MESSAGE,
    NOT_ROUTED_MESSAGE,
    STATUS_ACTIVE,
    STATUSES,
)

OWNER_ROLE = "agency_owner"


class InboxError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotInstalled(InboxError):
    status = 503


class Forbidden(InboxError):
    status = 403


class Conflict(InboxError):
    status = 409


def sees_every_inbox(ctx) -> bool:
    """Chi vede anche le caselle riservate senza esservi autorizzato: il
    titolare dell'agenzia e il Super Admin che vi opera."""
    return bool(getattr(ctx, "is_platform_admin", False)) or getattr(ctx, "role", None) == OWNER_ROLE


def may_configure(ctx) -> bool:
    return sees_every_inbox(ctx)


def hidden_numbers(cur, ctx) -> list[str]:
    """I numeri riceventi dell'agenzia che l'operatore della sessione NON vede."""
    return repository.hidden_numbers(cur, ctx.require_agency(), getattr(ctx, "user_id", None),
                                     sees_every_inbox=sees_every_inbox(ctx))


def routed_numbers(agency_id: int) -> list[str]:
    """I numeri che la configurazione del server instrada a QUESTA agenzia (e a nessun'altra)."""
    return sorted(n for n, agencies in routing.configured_routes().items() if agencies == {agency_id})


def default_label(phone_number_id: str, display_phone_number: str | None) -> str:
    return f"+{display_phone_number}" if display_phone_number else f"Numero …{phone_number_id[-4:]}"


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def describe(cur, agency_id: int, phone_number_ids: list[str]) -> dict[str, dict]:
    """{phone_number_id: {label, display_phone_number, configured, automations_enabled, access_mode}}
    per i numeri indicati: dalla riga della casella se c'e', altrimenti i
    valori predefiniti (come il numero 1 di oggi)."""
    numbers = sorted(set(n for n in phone_number_ids if n))
    rows = repository.by_numbers(cur, numbers) if repository.installed(cur) else {}
    shown = repository.latest_display_numbers(cur, agency_id, numbers)
    out = {}
    for n in numbers:
        row = rows.get(n)
        display = (row or {}).get("display_phone_number") or shown.get(n)
        out[n] = {"phone_number_id": n, "display_phone_number": display,
                  "label": row["label"] if row else default_label(n, display),
                  "kind": row["kind"] if row else KIND_CLOUD_API,
                  "configured": row is not None,
                  "automations_enabled": bool(row["automations_enabled"]) if row else True,
                  "access_mode": row["access_mode"] if row else ACCESS_AGENCY,
                  "status": row["status"] if row else STATUS_ACTIVE}
    return out


def list_inboxes(ctx) -> dict:
    """Le caselle dell'agenzia: tutte per chi configura (anche quelle non
    ancora configurate, dalla configurazione del server); per gli altri solo
    quelle che possono vedere. Gli accessi si mostrano solo a chi configura."""
    agency_id = ctx.require_agency()
    manager = may_configure(ctx)
    with core_cursor() as (_, cur):
        installed = repository.installed(cur)
        rows = {r["phone_number_id"]: r for r in repository.list_for_agency(cur, agency_id)} if installed else {}
        numbers = sorted(set(routed_numbers(agency_id)) | set(rows))
        info = describe(cur, agency_id, numbers)
        hidden = set(hidden_numbers(cur, ctx))
        names = {}
        if manager:
            user_ids = sorted({u for r in rows.values() for u in (r.get("access_user_ids") or [])})
            if user_ids:
                cur.execute("SELECT id, first_name, last_name, email FROM operator_users WHERE id = ANY(%s)", (user_ids,))
                names = {r["id"]: " ".join(x for x in (r["first_name"], r["last_name"]) if x) or r["email"]
                         for r in cur.fetchall()}
    items = []
    routed = set(routed_numbers(agency_id))
    for n in numbers:
        if not manager and n in hidden:
            continue
        item = {**info[n], "routed": n in routed}
        if manager:
            row = rows.get(n)
            item["access"] = [{"user_id": u, "name": names.get(u)} for u in (row or {}).get("access_user_ids") or []]
            item["id"] = (row or {}).get("id")
        items.append(item)
    return {"items": items, "installed": installed, "can_configure": manager and installed,
            "sees_every_inbox": sees_every_inbox(ctx)}


def _require_manager(ctx) -> None:
    if not may_configure(ctx):
        raise Forbidden("INBOX_FORBIDDEN", FORBIDDEN_MESSAGE)


def _require_installed(cur) -> None:
    if not repository.installed(cur):
        raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)


def save_inbox(ctx, phone_number_id: str, *, label: str | None, kind: str | None, automations_enabled: bool | None,
               access_mode: str | None, status: str | None, display_phone_number: str | None = None) -> dict:
    """Crea o aggiorna le impostazioni della casella di un numero instradato
    all'agenzia. I campi assenti restano come sono (o ai valori predefiniti)."""
    _require_manager(ctx)
    agency_id = ctx.require_agency()
    if phone_number_id not in routed_numbers(agency_id):
        raise Conflict("INBOX_NOT_ROUTED", NOT_ROUTED_MESSAGE)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        current = repository.get(cur, agency_id, phone_number_id, lock=True)
        info = describe(cur, agency_id, [phone_number_id])[phone_number_id]
        data = {
            "label": (label if label is not None else info["label"]).strip()[:LABEL_MAX],
            "kind": kind if kind is not None else info["kind"],
            "automations_enabled": automations_enabled if automations_enabled is not None else info["automations_enabled"],
            "access_mode": access_mode if access_mode is not None else info["access_mode"],
            "status": status if status is not None else info["status"],
            "display_phone_number": display_phone_number if display_phone_number is not None
            else (current or {}).get("display_phone_number") or info["display_phone_number"],
        }
        if not data["label"]:
            raise InboxError("INVALID_LABEL", "L'etichetta della casella non può essere vuota.")
        if data["kind"] not in KINDS or data["access_mode"] not in ACCESS_MODES or data["status"] not in STATUSES:
            raise InboxError("INVALID_VALUE", "Valore non ammesso per tipo, accesso o stato della casella.")
        cur.execute("SELECT 1 FROM whatsapp_inboxes WHERE agency_id = %s AND label = %s AND phone_number_id <> %s",
                    (agency_id, data["label"], phone_number_id))
        if cur.fetchone() is not None:
            raise Conflict("LABEL_TAKEN", LABEL_TAKEN_MESSAGE)
        row = repository.upsert(cur, agency_id=agency_id, phone_number_id=phone_number_id, user_id=ctx.user_id, **data)
        if row is None:          # il numero e' configurato da un'altra agenzia: la configurazione del server e' cambiata
            raise Conflict("INBOX_NOT_ROUTED", NOT_ROUTED_MESSAGE)
    return list_inboxes(ctx)


def set_access(ctx, phone_number_id: str, user_ids: list[int]) -> dict:
    """Gli operatori autorizzati su una casella riservata (sostituisce l'elenco).
    Il titolare non va elencato: vede sempre."""
    _require_manager(ctx)
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        row = repository.get(cur, agency_id, phone_number_id, lock=True)
        if row is None:
            raise InboxError("INBOX_NOT_CONFIGURED", "Configura prima la casella (etichetta e accesso).")
        wanted = sorted({int(u) for u in user_ids})
        if repository.active_members(cur, agency_id, wanted) != set(wanted):
            raise InboxError("MEMBER_NOT_FOUND", MEMBER_NOT_FOUND_MESSAGE)
        repository.set_access(cur, row["id"], wanted, ctx.user_id)
    return list_inboxes(ctx)


def members(ctx) -> dict:
    """I membri attivi dell'agenzia, per scegliere chi autorizzare."""
    _require_manager(ctx)
    from appointments import repository as appointment_repository

    with core_cursor() as (_, cur):
        rows = appointment_repository.agents(cur, ctx.require_agency())
    return {"items": [{"id": r["id"], "name": r["name"], "role": r["role"]} for r in rows]}
