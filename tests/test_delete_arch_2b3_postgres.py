"""DELETE-ARCH Fase 2B3 - GET /api/property/trash (l'elenco della pagina
«Cestino»), su PostgreSQL VERO con la fixture `banco` della 2B2.

  E1  admin/owner: tutto il Cestino dell'agenzia, dal piu' recente, con chi
      ha eliminato, motivo, nota e stato commerciale; nessun immobile vivo;
  E2  agent: solo cio' che ha spostato lui; un altro agent: niente;
  E3  multi-agenzia: l'altra agenzia non vede nulla;
  E4  restore: l'immobile esce dall'elenco; paginazione con has_more;
  E5  sola lettura: nessuna riga cambia leggendo l'elenco.

Prima della Fase 2B3 la rotta non esiste (404): fail-before.
"""
from __future__ import annotations

import pytest

from tests.test_delete_arch_0_postgres import DSN, _codice, _immobile, _q  # noqa: F401
from tests.test_delete_arch_2b2_postgres import (  # noqa: F401  (fixture riusate)
    _restore, _trash, banco, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ELENCO = "/api/property/trash"


def _ids(r):
    return [x["id"] for x in _codice(r, 200)["items"]]


def test_e1_admin_vede_il_cestino_dell_agenzia(banco):
    b = banco
    vivo = _immobile(b)["id"]
    del_owner = _immobile(b)["id"]
    del_agent = _immobile(b, "agent_a")["id"]
    _codice(b["post"](f"/api/property/properties/{del_owner}/trash", {"reason_code": "duplicate", "note": "Doppione"}), 200)
    _codice(_trash(b, del_agent, "agent_a", reason="test_record"), 200)
    for chi in ("owner_a", "admin_a"):
        corpo = _codice(b["get"](ELENCO, chi), 200)
        ids = [x["id"] for x in corpo["items"]]
        assert ids[:2] == [del_agent, del_owner] and vivo not in ids          # dal piu' recente
        voce = next(x for x in corpo["items"] if x["id"] == del_owner)
        assert voce["deleted_reason"] == "duplicate" and voce["deleted_note"] == "Doppione"
        assert voce["deleted_by_user_id"] == b["ids"]["owner_a"] and voce["deleted_by_name"]
        assert voce["commercial_status"] == "draft" and voce["code"] and voce["deleted_at"]
        assert corpo["has_more"] is False and corpo["limit"] == 50 and corpo["offset"] == 0
    assert next(x for x in _codice(b["get"](ELENCO, "owner_a"), 200)["items"] if x["id"] == del_agent)["deleted_note"] is None


def test_e2_agent_vede_solo_i_propri(banco):
    b = banco
    del_owner = _immobile(b)["id"]
    del_agent = _immobile(b, "agent_a")["id"]
    _codice(_trash(b, del_owner), 200)
    _codice(_trash(b, del_agent, "agent_a"), 200)
    assert _ids(b["get"](ELENCO, "agent_a")) == [del_agent]
    assert _ids(b["get"](ELENCO, "agent_a2")) == []


def test_e3_multi_agenzia(banco):
    b = banco
    pid = _immobile(b)["id"]
    _codice(_trash(b, pid), 200)
    assert pid not in _ids(b["get"](ELENCO, "owner_b"))
    assert pid in _ids(b["get"](ELENCO, "platform_acting"))          # superadmin dentro l'agenzia 1
    assert b["get"](ELENCO, "platform_none").status_code == 403       # nessuna agenzia: nessun Cestino


def test_e4_restore_esce_dall_elenco_e_paginazione(banco):
    b = banco
    ids = [_immobile(b)["id"] for _ in range(3)]
    for pid in ids:
        _codice(_trash(b, pid), 200)
    pagina = _codice(b["get"](ELENCO, "owner_a", limit=2, offset=0), 200)
    assert [x["id"] for x in pagina["items"]] == [ids[2], ids[1]] and pagina["has_more"] is True
    seconda = _codice(b["get"](ELENCO, "owner_a", limit=2, offset=2), 200)
    assert ids[0] in [x["id"] for x in seconda["items"]]
    _codice(_restore(b, ids[1]), 200)
    assert ids[1] not in _ids(b["get"](ELENCO, "owner_a", limit=200))
    assert b["get"](ELENCO, "owner_a", limit=500).status_code == 422


def test_e5_sola_lettura(banco):
    b = banco
    pid = _immobile(b)["id"]
    _codice(_trash(b, pid), 200)
    prima = _q(b, "SELECT to_jsonb(p) FROM properties p WHERE p.id = %s", (pid,))
    eventi = _q(b, "SELECT count(*) FROM record_lifecycle_events WHERE entity_id = %s", (pid,))
    _codice(b["get"](ELENCO, "owner_a"), 200)
    assert _q(b, "SELECT to_jsonb(p) FROM properties p WHERE p.id = %s", (pid,)) == prima
    assert _q(b, "SELECT count(*) FROM record_lifecycle_events WHERE entity_id = %s", (pid,)) == eventi
