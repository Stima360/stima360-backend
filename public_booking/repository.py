"""A30-12 - repository grezzo per `public_booking_links`,
`public_booking_submissions`, `public_booking_rate_limits`.

MAI un INSERT in `appointments` qui (CORREZIONE ARCHITETTURALE OBBLIGATORIA
del gate): la creazione dell'appuntamento passa SOLO da
`appointments.service.create_public_booking_appointment`, che vive nel
dominio Agenda e usa il SUO repository. Questo file scrive solo le proprie
tre tabelle.
"""
from __future__ import annotations

from .enums import LINK_STATUSES


def _riga(r):
    return dict(r) if r is not None else None

# ---------------------------------------------------------------------------
# public_booking_links - CRUD operatore
# ---------------------------------------------------------------------------

def create_link(cur, *, agency_id, assigned_user_id, token_hash, label,
                appointment_type, duration_minutes, buffer_before_minutes,
                buffer_after_minutes, created_by_user_id, expires_at) -> dict:
    cur.execute(
        """INSERT INTO public_booking_links(
            agency_id, assigned_user_id, token_hash, label, status,
            appointment_type, duration_minutes, buffer_before_minutes,
            buffer_after_minutes, created_by_user_id, expires_at
        ) VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s)
        RETURNING *""",
        (agency_id, assigned_user_id, token_hash, label, appointment_type,
         duration_minutes, buffer_before_minutes, buffer_after_minutes,
         created_by_user_id, expires_at),
    )
    return _riga(cur.fetchone())

def list_links(cur, *, agency_id, assigned_user_id=None) -> list[dict]:
    """`assigned_user_id=None` -> tutti i link dell'agenzia (owner/admin);
    valorizzato -> solo quelli di quell'agente (permesso D2, applicato dal
    service PRIMA di chiamare questa funzione, mai qui: il repository non
    decide permessi)."""
    if assigned_user_id is None:
        cur.execute(
            "SELECT * FROM public_booking_links WHERE agency_id = %s "
            "ORDER BY created_at DESC, id DESC", (agency_id,))
    else:
        cur.execute(
            "SELECT * FROM public_booking_links "
            "WHERE agency_id = %s AND assigned_user_id = %s "
            "ORDER BY created_at DESC, id DESC", (agency_id, assigned_user_id))
    return [dict(r) for r in cur.fetchall()]

def get_link(cur, *, agency_id, link_id) -> dict | None:
    cur.execute(
        "SELECT * FROM public_booking_links WHERE id = %s AND agency_id = %s",
        (link_id, agency_id))
    return _riga(cur.fetchone())

def update_link_fields(cur, *, agency_id, link_id, changes: dict) -> dict | None:
    if not changes:
        return get_link(cur, agency_id=agency_id, link_id=link_id)
    colonne = list(changes.keys())
    set_clause = ", ".join(f"{c} = %s" for c in colonne)
    cur.execute(
        f"UPDATE public_booking_links SET {set_clause} "
        f"WHERE id = %s AND agency_id = %s RETURNING *",
        [changes[c] for c in colonne] + [link_id, agency_id],
    )
    return _riga(cur.fetchone())

def rotate_token(cur, *, agency_id, link_id, new_token_hash) -> dict | None:
    return update_link_fields(cur, agency_id=agency_id, link_id=link_id,
                              changes={"token_hash": new_token_hash})

def set_status(cur, *, agency_id, link_id, status) -> dict | None:
    assert status in LINK_STATUSES
    changes = {"status": status}
    if status == "disabled":
        changes["revoked_at"] = _now_sql(cur)
    return update_link_fields(cur, agency_id=agency_id, link_id=link_id,
                              changes=changes)

def _now_sql(cur):
    cur.execute("SELECT NOW() AS now")
    return cur.fetchone()["now"]

# ---------------------------------------------------------------------------
# public_booking_links - risoluzione pubblica (per token)
# ---------------------------------------------------------------------------

def find_link_by_token_hash(cur, token_hash: str) -> dict | None:
    """Nessun filtro di agenzia: il token E' l'identificatore, come
    `owner_access_tokens` (LMC-1B). Chi chiama decide se e' attivo/valido -
    questa funzione restituisce la riga com'e', anche disabilitata/scaduta,
    cosi' il chiamante puo' loggare internamente PERCHE' senza mai
    rivelarlo al pubblico (D privacy: stessa risposta generica in ogni
    caso)."""
    cur.execute(
        "SELECT * FROM public_booking_links WHERE token_hash = %s", (token_hash,))
    return _riga(cur.fetchone())

# ---------------------------------------------------------------------------
# public_booking_submissions (D7)
# ---------------------------------------------------------------------------

def create_pending_submission(cur, *, link_id, submission_hash, client_ip_hash,
                              payload_fingerprint) -> dict:
    """Idempotente all'INSERT: se `submission_hash` esiste gia', non
    inserisce una seconda riga - la restituisce cosi' com'e' (il chiamante
    decide se e' un retry legittimo o un payload diverso, confrontando
    `payload_fingerprint`)."""
    cur.execute(
        """INSERT INTO public_booking_submissions(
            link_id, submission_hash, client_ip_hash, payload_fingerprint, status
        ) VALUES (%s, %s, %s, %s, 'pending')
        ON CONFLICT (submission_hash) DO NOTHING
        RETURNING *""",
        (link_id, submission_hash, client_ip_hash, payload_fingerprint),
    )
    riga = cur.fetchone()
    if riga is not None:
        return _riga(riga)
    return find_submission(cur, submission_hash)

def find_submission(cur, submission_hash: str) -> dict | None:
    cur.execute(
        "SELECT * FROM public_booking_submissions WHERE submission_hash = %s",
        (submission_hash,))
    return _riga(cur.fetchone())

def mark_submission_failed(cur, submission_hash: str) -> None:
    """Solo se ancora 'pending': un tentativo gia' riuscito ('succeeded')
    non torna mai 'failed' per un errore successivo e scorrelato."""
    cur.execute(
        "UPDATE public_booking_submissions SET status = 'failed', completed_at = NOW() "
        "WHERE submission_hash = %s AND status = 'pending'",
        (submission_hash,))

# ---------------------------------------------------------------------------
# public_booking_rate_limits (D9) - contatore a finestra fissa, atomico.
# ---------------------------------------------------------------------------

def increment_and_check(cur, *, link_id, scope, window_start, limit,
                        client_ip_hash="") -> bool:
    """Incrementa il bucket `(link_id, scope, client_ip_hash, window_start)`
    e restituisce `True` se la richiesta E' AMMESSA (il conteggio DOPO
    l'incremento e' entro `limit`), `False` se il limite e' superato.

    Un solo INSERT ... ON CONFLICT ... DO UPDATE: l'incremento e il
    confronto sono la stessa istruzione, cosi' due richieste concorrenti
    per lo stesso bucket non possono leggere lo stesso conteggio "vecchio"
    ed entrare entrambe.
    """
    cur.execute(
        """INSERT INTO public_booking_rate_limits(
            link_id, scope, client_ip_hash, window_start, request_count
        ) VALUES (%s, %s, %s, %s, 1)
        ON CONFLICT (link_id, scope, client_ip_hash, window_start)
        DO UPDATE SET request_count = public_booking_rate_limits.request_count + 1
        RETURNING request_count""",
        (link_id, scope, client_ip_hash, window_start),
    )
    conteggio = cur.fetchone()["request_count"]
    return conteggio <= limit


# ---------------------------------------------------------------------------
# metadata pubblica minima (D privacy: SOLO nome agenzia e nome agente)
# ---------------------------------------------------------------------------

def public_names(cur, *, agency_id, user_id) -> dict:
    """SOLO `agencies.name` e `operator_users.first_name`/`last_name` - mai
    email, telefono, slug o altra colonna di nessuna delle due tabelle."""
    cur.execute("SELECT name FROM agencies WHERE id = %s", (agency_id,))
    agenzia = cur.fetchone()
    cur.execute("SELECT first_name, last_name FROM operator_users WHERE id = %s", (user_id,))
    agente = cur.fetchone()
    nome_agente = " ".join(
        p for p in ((agente or {}).get("first_name"), (agente or {}).get("last_name")) if p
    ) or None
    return {
        "agency_name": None if agenzia is None else agenzia["name"],
        "agent_name": nome_agente,
    }
