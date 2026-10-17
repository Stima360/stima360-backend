"""ADM-1 - supporto per i test della Rete che girano senza database.

Dal ADM-1 ogni modifica della Rete:
  * e' ammessa solo per il Super Admin generale designato
    (`STIMA360_SUPER_ADMIN_USER_ID`);
  * rilegge e blocca la riga dell'attore nella propria transazione
    (`super_admin.lock_super_admin`);
  * blocca la riga dell'agenzia o dell'operatore che cambia
    (`lock_agency`, `lock_operator`);
  * revoca le sessioni interessate (`operator_auth.repository`).

I test in memoria di P27/P28 usano un cursore finto che non esegue SQL. Questo
modulo designa il loro platform admin come Super Admin e sostituisce le sole
letture con lock e le revoche con registratori, lasciando VERO il controllo
dell'identita' designata. Il comportamento SQL reale e' provato su PostgreSQL
in `tests/test_adm1_super_admin_postgres.py`.
"""
from __future__ import annotations


def super_admin_designato(monkeypatch, user_id: int) -> dict:
    from operator_auth import repository as session_repository
    from platform_admin import agencies_repository, operators_repository, super_admin

    monkeypatch.setenv(super_admin.SUPER_ADMIN_ENV, str(user_id))
    registro: dict = {"locks": [], "revoked_operators": [], "revoked_agencies": [], "acting_cleared": []}

    def _lock(cur, ctx, *, audit_action, error_class=super_admin.SuperAdminDenied):
        super_admin.require_super_admin(ctx, audit_action=audit_action, error_class=error_class)
        registro["locks"].append(audit_action)

    monkeypatch.setattr(super_admin, "lock_super_admin", _lock)
    monkeypatch.setattr(agencies_repository, "lock_agency",
                        lambda cur, agency_id: agencies_repository.get_agency(cur, agency_id))
    monkeypatch.setattr(operators_repository, "lock_operator",
                        lambda cur, operator_user_id: operators_repository.get_operator(cur, operator_user_id))
    monkeypatch.setattr(session_repository, "revoke_operator_sessions",
                        lambda cur, operator_user_id: registro["revoked_operators"].append(operator_user_id) or 0)
    monkeypatch.setattr(session_repository, "revoke_agency_member_sessions",
                        lambda cur, agency_id: registro["revoked_agencies"].append(agency_id) or 0)
    monkeypatch.setattr(session_repository, "clear_acting_for_agency",
                        lambda cur, agency_id: registro["acting_cleared"].append(agency_id) or 0)
    return registro
