"""ADM-6 - concessione e revoca del ruolo di amministratore di piattaforma, e
sostituzione del Super Admin generale.

PostgreSQL usa-e-getta e sessioni vere (fixture `adm1`).

  01  solo il Super Admin designato usa la rotta (403 agli altri, 401 anonimo)
  02  promozione: solo account attivo e con almeno un login riuscito; nessun
      no-op; membership invariata; registro con prima e dopo
  03  revoca: sessioni dell'operatore revocate; membership invariata; mai al
      designato; mai l'ultimo amministratore attivo
  04  sostituzione A -> B: promozione, cambio di `STIMA360_SUPER_ADMIN_USER_ID`,
      verifica di B, revoca di A da parte di B; lo storico di A resta
"""
from __future__ import annotations

import pytest

from tests.test_adm1_super_admin_postgres import _token, _viva, adm1  # noqa: F401
from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

AZIONE = "platform.operator.platform_admin.update"


def _url(uid):
    return f"/api/platform/operators/{uid}/platform-admin"


def _flag(m, uid):
    return tuple(_q(m, "SELECT is_platform_admin, status FROM operator_users WHERE id = %s", (uid,))[0])


def _membership(m, uid):
    return [tuple(r) for r in _q(m, "SELECT id, agency_id, role, status FROM agency_memberships "
                                    "WHERE operator_user_id = %s ORDER BY id", (uid,))]


def _ultima(m, uid):
    righe = _q(m, "SELECT actor_user_id, result, metadata FROM platform_audit_log "
                  "WHERE action = %s AND target_id = %s ORDER BY id DESC LIMIT 1", (AZIONE, str(uid)))
    return righe[0] if righe else None


@pytest.fixture
def adm6(adm1):
    m = adm1
    membro = m["persone"]["membro"]
    yield m
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE, status = 'active' WHERE id = %s", (membro,))
    _q(m, "UPDATE operator_users SET status = 'active' WHERE id = %s", (m["persone"]["agente"],))


def test_01_solo_il_super_admin_designato(adm6):
    from fastapi.testclient import TestClient
    m = adm6
    membro = m["persone"]["membro"]
    for chi in ("altro", "agente"):
        r = m["accedi"](chi).patch(_url(membro), json={"is_platform_admin": True})
        assert r.status_code == 403, (chi, r.text)
    anonimo = TestClient(m["app"], base_url="https://testserver")
    assert anonimo.patch(_url(membro), json={"is_platform_admin": True}).status_code == 401
    assert m["accedi"]("super").patch(_url(membro), json={"is_platform_admin": "true"}).status_code == 422
    assert _flag(m, membro)[0] is False


def test_02_promozione_e_sue_guardie(adm6):
    m = adm6
    sa = m["accedi"]("super")
    membro, agente, altro = m["persone"]["membro"], m["persone"]["agente"], m["persone"]["altro"]
    membership = _membership(m, membro)

    _q(m, "UPDATE operator_users SET last_login_at = NULL WHERE id = %s", (membro,))
    r = sa.patch(_url(membro), json={"is_platform_admin": True})
    assert r.status_code == 409 and "login" in r.json()["detail"], r.text
    assert _flag(m, membro)[0] is False

    assert sa.patch(f"/api/platform/operators/{agente}", json={"status": "disabled"}).status_code == 200
    r = sa.patch(_url(agente), json={"is_platform_admin": True})
    assert r.status_code == 409 and "attivo" in r.json()["detail"], r.text

    r = sa.patch(_url(altro), json={"is_platform_admin": True})
    assert r.status_code == 409 and "gia'" in r.json()["detail"], r.text

    m["accedi"]("membro")  # il login riuscito che la promozione richiede
    r = sa.patch(_url(membro), json={"is_platform_admin": True})
    assert r.status_code == 200 and r.json()["is_platform_admin"] is True, r.text
    assert _flag(m, membro) == (True, "active")
    assert _membership(m, membro) == membership
    attore, esito, meta = _ultima(m, membro)
    assert (attore, esito) == (m["persone"]["super"], "success")
    assert meta["platform_admin"] == {"from": False, "to": True}


def test_03_revoca_e_sue_guardie(adm6, monkeypatch):
    from platform_admin import operators_repository
    m = adm6
    sa = m["accedi"]("super")
    membro, designato = m["persone"]["membro"], m["persone"]["super"]
    m["accedi"]("membro")
    assert sa.patch(_url(membro), json={"is_platform_admin": True}).status_code == 200
    sessione = _token(m["accedi"]("membro"))
    assert _viva(sessione)
    membership = _membership(m, membro)

    r = sa.patch(_url(designato), json={"is_platform_admin": False})
    assert r.status_code == 409 and "designato" in r.json()["detail"], r.text
    assert _flag(m, designato)[0] is True

    # l'ultimo amministratore attivo: difesa in profondita' (con il designato
    # attivo non si raggiunge per via ordinaria, quindi si simula il conteggio)
    originale = operators_repository.count_active_platform_admins
    monkeypatch.setattr(operators_repository, "count_active_platform_admins", lambda *a, **k: 0)
    r = sa.patch(_url(membro), json={"is_platform_admin": False})
    assert r.status_code == 409 and "senza amministratori" in r.json()["detail"], r.text
    assert _flag(m, membro)[0] is True and _viva(sessione)
    monkeypatch.setattr(operators_repository, "count_active_platform_admins", originale)

    r = sa.patch(_url(membro), json={"is_platform_admin": False})
    assert r.status_code == 200 and r.json()["is_platform_admin"] is False, r.text
    assert not _viva(sessione), "la sessione di chi perde il ruolo deve essere revocata"
    assert _membership(m, membro) == membership
    assert _ultima(m, membro)[2]["platform_admin"] == {"from": True, "to": False}


def test_04_sostituzione_del_super_admin_da_a_a_b(adm6, monkeypatch):
    from platform_admin.super_admin import SUPER_ADMIN_ENV
    m = adm6
    a, b = m["persone"]["super"], m["persone"]["membro"]
    sa = m["accedi"]("super")
    righe_di_a = _q(m, "SELECT count(*) FROM platform_audit_log WHERE actor_user_id = %s", (a,))[0][0]

    # 1-2. B esiste, ha fatto login; A lo promuove
    m["accedi"]("membro")
    assert sa.patch(_url(b), json={"is_platform_admin": True}).status_code == 200
    # 3. il proprietario cambia la designazione (qui: la variabile d'ambiente)
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(b))
    # 4. B verifica con un'operazione riservata di sola lettura; A non e' piu' il designato
    sb = m["accedi"]("membro")
    assert sb.get("/api/platform/me").json()["is_super_admin"] is True
    assert sb.get("/api/platform/audit").status_code == 200
    assert sa.get("/api/platform/audit").status_code == 403
    assert sa.get("/api/platform/me").json()["is_super_admin"] is False
    # 5. B toglie il ruolo ad A; A e' fuori, il suo storico resta
    sessione_a = _token(sa)
    r = sb.patch(_url(a), json={"is_platform_admin": False})
    assert r.status_code == 200, r.text
    assert _flag(m, a) == (False, "active")
    assert not _viva(sessione_a)
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE actor_user_id = %s", (a,))[0][0] >= righe_di_a
    attore, _esito, meta = _ultima(m, a)
    assert attore == b and meta["platform_admin"] == {"from": True, "to": False}
    # e B, ora designato, non si toglie il ruolo da solo
    r = sb.patch(_url(b), json={"is_platform_admin": False})
    assert r.status_code == 409, r.text
