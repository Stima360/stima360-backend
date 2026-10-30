"""BLOCCO-4B - letture proprie della risposta WhatsApp manuale.

Solo cio' che il registro delle comunicazioni e la ricezione (4A) non dicono:
se l'operatore che sta per inviare e' ancora, adesso, una persona attiva e
autorizzata nell'agenzia. Nessuna scrittura.
"""
from __future__ import annotations


def operator_still_authorized(cur, ctx) -> bool:
    """True se utente, sessione, ruolo e agenzia permettono ancora l'invio.

    Riletto dal database al momento dell'invio, non preso dal contesto della
    richiesta: un operatore disattivato, rimosso dall'agenzia, con un ruolo
    cambiato o con la sessione revocata mentre la risposta attendeva non la
    manda. Un amministratore di piattaforma deve essere ancora tale, con la
    sessione ancora dentro QUESTA agenzia.
    """
    agency_id = ctx.require_agency()
    if ctx.user_id is None or ctx.read_only:
        return False
    cur.execute(
        """
        SELECT u.status AS user_status,
               u.is_platform_admin,
               (SELECT a.status FROM agencies a WHERE a.id = %s) AS agency_status,
               (SELECT m.role FROM agency_memberships m
                 WHERE m.operator_user_id = u.id AND m.agency_id = %s AND m.status = 'active') AS role,
               s.id IS NOT NULL AS session_live,
               s.acting_agency_id
          FROM operator_users u
          LEFT JOIN operator_sessions s
                 ON s.id = %s AND s.operator_user_id = u.id
                AND s.revoked_at IS NULL AND s.expires_at > NOW()
         WHERE u.id = %s
        """,
        (agency_id, agency_id, ctx.session_id, ctx.user_id),
    )
    row = cur.fetchone()
    if row is None or row["user_status"] != "active" or row["agency_status"] != "active":
        return False
    if ctx.session_id is not None and not row["session_live"]:
        return False
    if ctx.is_platform_admin:
        return bool(row["is_platform_admin"]) and (
            row["role"] is not None or row["acting_agency_id"] == agency_id)
    return row["role"] is not None and row["role"] == ctx.role
