"""A31-2 - le scritture della proiezione su `property_visits`.

Solo SQL, solo sul cursore del chiamante. Nessuna connessione, nessun
commit. Colonne scritte (contratto A31-1 congelato):

  appointment_id, property_id, contact_id, lead_id, scheduled_at, status,
  assigned_to (snapshot D3), created_by (convenzione P26-5), updated_at.

MAI `outcome`, `feedback`, `rating` (D4: restano del percorso legacy).
Un errore del database (guardia 078, FK, UNIQUE) diventa
`BuyerVisitProjectionIntegrity` (409): la transazione del chiamante e' gia'
abortita e si annulla tutta risalendo.
"""
from __future__ import annotations

from . import errors

#: Gli stati di `property_visits` (CHECK di 002): coincidono con quelli di
#: `appointments` che una proiezione puo' assumere. `requested` (D1) e
#: `rescheduled` (la riga vecchia di uno spostamento) non si proiettano mai.
STATI_PROIETTATI = ("scheduled", "confirmed", "completed", "cancelled", "no_show")

_COLONNE = ("id", "property_id", "contact_id", "lead_id", "scheduled_at", "status",
            "outcome", "feedback", "rating", "assigned_to", "created_by", "appointment_id",
            "created_at", "updated_at")


def _riga(r):
    return None if r is None else {c: r[c] for c in _COLONNE}


def _scrivi(cur, sql, parametri):
    try:
        cur.execute(sql, parametri)
    except Exception as exc:  # noqa: BLE001 - errori del driver, tradotti
        raise errors.BuyerVisitProjectionIntegrity(
            "La visita collegata a questo appuntamento non e' coerente: "
            "l'operazione e' stata annullata") from exc
    return _riga(cur.fetchone())


def for_appointment(cur, appointment_id: int):
    """La proiezione di un appuntamento, bloccata per la transazione, o None."""
    cur.execute(
        f"SELECT {', '.join(_COLONNE)} FROM property_visits "
        "WHERE appointment_id = %s FOR UPDATE",
        (appointment_id,))
    return _riga(cur.fetchone())


def insert(cur, *, appointment: dict, status: str, assigned_to, created_by) -> dict:
    return _scrivi(
        cur,
        f"""INSERT INTO property_visits
                (property_id, contact_id, lead_id, scheduled_at, status,
                 assigned_to, created_by, appointment_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {', '.join(_COLONNE)}""",
        (appointment["property_id"], appointment["contact_id"], appointment["lead_id"],
         appointment["start_at"], status, assigned_to, created_by, appointment["id"]))


def update(cur, visit_id: int, changes: dict) -> dict:
    """UPDATE di sole colonne della proiezione (mai outcome/feedback/rating)."""
    vietate = {"outcome", "feedback", "rating", "id", "created_at", "created_by"} & changes.keys()
    if vietate:  # pragma: no cover - difesa del contratto D4
        raise ValueError(f"A31-2: la proiezione non scrive {sorted(vietate)}")
    colonne = sorted(changes)
    assegnazioni = ", ".join(f"{c} = %s" for c in colonne)
    return _scrivi(
        cur,
        f"UPDATE property_visits SET {assegnazioni}, updated_at = NOW() "
        f"WHERE id = %s RETURNING {', '.join(_COLONNE)}",
        tuple(changes[c] for c in colonne) + (visit_id,))
