"""ELIMINAZIONE-EDIFICIO-2 - cancellazione definitiva di edifici creati con
STIMA Voice, con il percorso VERO di Voice (piano, esecuzione, «Annulla») su
PostgreSQL VERO (database locale usa-e-getta dei test Voice).

  A  edificio creato con Voice + unita' censita dalla scheda: richiesta,
     approvazione e cancellazione definitiva del Super Admin designato; il
     registro Voice resta identico; «Annulla» dopo: «scaduto», nessun record
     ricreato;
  B  edificio Voice + unita' Voice annullata con «Annulla» (storico
     `undo-create`): cancellabile, storico stati copiato nell'evento `purge`,
     tentativi di «Annulla» conservati;
  C  edificio vuoto annullato con Voice (annullamento tecnico, gia' nel
     Cestino): solo il Super Admin designato lo cancella;
  D  Voice non ha nessuna via di cancellazione fisica: un utente ordinario
     con Voice non cancella nulla, le funzioni riservate rifiutano i suoi
     contesti.
"""
from __future__ import annotations

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import _voce_accesa  # noqa: F401  (Voice abilitata)
from tests.test_voice_4_undo_postgres import _annulla, _eseguito, esito, q

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def sa(db, monkeypatch):
    """Il Super Admin generale designato (amministratore di piattaforma, id
    nella configurazione del servizio) sullo stesso database."""
    import psycopg2
    import platform_admin.database as pdb
    from operator_auth.context import OperatorContext
    from property import building_deletion as bd
    monkeypatch.setattr(pdb, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    riga = q(db, "SELECT id FROM operator_users WHERE email_normalized = 'super.voice@x.test'")
    uid = riga[0][0] if riga else q(
        db, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, is_platform_admin) "
            "VALUES ('super.voice@x.test', 'super.voice@x.test', 'pbkdf2_sha256$1$x$y', 'Super', TRUE) "
            "RETURNING id")[0][0]
    monkeypatch.setenv(bd.SUPER_ADMIN_ENV, str(uid))
    return OperatorContext(user_id=uid, agency_id=None, role=None, is_platform_admin=True, session_id=None,
                           auth_channel="operator_session")


def _registro(db):
    return (q(db, "SELECT to_jsonb(s)::text FROM voice_command_steps s ORDER BY command_id, ordinal"),
            q(db, "SELECT to_jsonb(u)::text FROM voice_step_undos u ORDER BY id"),
            q(db, "SELECT id, status, plan_sha256 FROM voice_commands ORDER BY id"))


def _approvato(db, sa, bid, chi="owner_a"):
    from property import building_deletion as bd
    r = bd.create_request(ctx(db, chi), bid)
    bd.approve(sa, r["id"], bd.admin_get_request(sa, r["id"])["fingerprint"])
    return r


def _edificio_voce(db, chi, civico):
    cid, r = _eseguito(db, chi, {"intent": "create_building", "quote": f"palazzina via Purge {civico} Tortoreto",
                                 "address": "Via Purge", "civic_number": civico, "city": "Tortoreto"},
                       transcript=f"censisci la palazzina di via Purge {civico}")
    return cid, esito(r, 1)["result"]["id"]


def test_a_edificio_voce_con_unita_censita(db, sa):
    from property import building_deletion as bd
    from property import census
    from property.schemas import CensusUnitCreate
    cid, bid = _edificio_voce(db, "owner_a", "11")
    u = census.create_unit(ctx(db, "owner_a"), CensusUnitCreate(building_id=bid, floor="1"))
    _approvato(db, sa, bid)
    chk = bd.purge_check(sa, bid)
    assert chk["eligible"] is True, chk["blockers"]
    conservati = {x["code"]: x for x in chk["preserved"]}
    assert conservati["VOICE_COMMANDS"]["count"] == 1 and "Crea edificio" in conservati["VOICE_COMMANDS"]["items"][0]["label"]
    registro = _registro(db)
    fatto = bd.purge(sa, bid, bd.PURGE_CONFIRM_TEXT, chk["fingerprint"])
    assert fatto["purged_unit_ids"] == [u["id"]]
    assert q(db, "SELECT count(*) FROM buildings WHERE id = %s", (bid,))[0][0] == 0
    assert q(db, "SELECT count(*) FROM properties WHERE id = %s", (u["id"],))[0][0] == 0
    assert _registro(db) == registro                       # il registro di Voice non si tocca
    meta = q(db, "SELECT metadata FROM record_lifecycle_events WHERE entity_type = 'building' AND entity_id = %s "
                 "AND action = 'purge'", (bid,))[0][0]
    assert [(x["command_id"], x["intent"], x["state"]) for x in meta["preserved_references"]["voice_steps"]] == \
        [(cid, "create_building", "succeeded")]
    # «Annulla» di Voice dopo: scaduto, nessun errore, niente ricreato (si registra solo il tentativo)
    e = _annulla(db, "owner_a", cid, 1)
    assert e["outcome"] == "expired", e
    assert q(db, "SELECT count(*) FROM buildings WHERE id = %s", (bid,))[0][0] == 0
    assert q(db, "SELECT outcome FROM voice_step_undos WHERE command_id = %s ORDER BY id DESC LIMIT 1", (cid,))[0][0] \
        == "expired"


def test_b_unita_voce_annullata(db, sa):
    from property import building_deletion as bd
    cid, bid = _edificio_voce(db, "owner_a", "12")
    cid2, r2 = _eseguito(db, "owner_a", {"intent": "create_unit", "quote": "censisci il bilocale di via Purge 12",
                                         "record_kind": "census", "address": "Via Purge", "civic_number": "12"},
                         transcript="censisci il bilocale")
    pid = esito(r2, 1)["result"]["id"]
    q(db, "UPDATE properties SET building_id = %s WHERE id = %s", (bid, pid))
    assert _annulla(db, "owner_a", cid2, 1)["outcome"] == "undone"
    storico = q(db, "SELECT id, old_value, new_value, note FROM property_status_history WHERE property_id = %s "
                    "ORDER BY id", (pid,))
    assert [x[1:] for x in storico if x[3] == "undo-create"] == [["draft", "archived", "undo-create"]]
    _approvato(db, sa, bid)
    chk = bd.purge_check(sa, bid)
    assert chk["eligible"] is True, chk["blockers"]
    codici = {x["code"]: x["count"] for x in chk["preserved"]}
    assert codici["VOICE_COMMANDS"] == 2 and codici["VOICE_UNDOS"] == 1 and codici["STATUS_HISTORY"] == len(storico)
    registro = _registro(db)
    bd.purge(sa, bid, bd.PURGE_CONFIRM_TEXT, chk["fingerprint"])
    assert _registro(db) == registro
    assert q(db, "SELECT count(*) FROM property_status_history WHERE property_id = %s", (pid,))[0][0] == 0
    copia = q(db, "SELECT before_state, metadata FROM record_lifecycle_events WHERE entity_type = 'property' "
                  "AND entity_id = %s AND action = 'purge'", (pid,))[0]
    assert [[x["id"], x["old_value"], x["new_value"], x["note"]] for x in copia[0]["status_history"]] == storico
    assert [x["outcome"] for x in copia[1]["preserved_voice_undos"]] == ["undone"]
    assert [(x["command_id"], x["intent"]) for x in copia[1]["preserved_voice_steps"]] == [(cid2, "create_unit")]
    # «Annulla» di nuovo: gia' annullato, nessuna scrittura
    assert _annulla(db, "owner_a", cid2, 1)["outcome"] == "already_undone"
    assert _registro(db) == registro


def test_c_edificio_vuoto_annullato_da_voice(db, sa):
    from operator_auth.context import OperatorContext
    from property import building_deletion as bd
    cid, bid = _edificio_voce(db, "agent_a1", "13")
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"           # annullamento tecnico: nel Cestino
    assert q(db, "SELECT deleted_at IS NOT NULL FROM buildings WHERE id = %s", (bid,))[0][0] is True
    chk = bd.purge_check(sa, bid)
    assert chk["eligible"] is True and chk["units"]["total"] == 0, chk["blockers"]
    # nessun altro: utente che l'ha creato, titolare, un altro amministratore di piattaforma
    altro = q(db, "INSERT INTO operator_users (email, email_normalized, password_hash, is_platform_admin) "
                  "VALUES ('altro.voice@x.test', 'altro.voice@x.test', 'pbkdf2_sha256$1$x$y', TRUE) "
                  "ON CONFLICT (email_normalized) DO UPDATE SET is_platform_admin = TRUE RETURNING id")[0][0]
    for contesto in (ctx(db, "agent_a1"), ctx(db, "owner_a"),
                     OperatorContext(user_id=altro, agency_id=None, role=None, is_platform_admin=True,
                                     session_id=None, auth_channel="operator_session")):
        with pytest.raises(bd.PermissionDenied):
            bd.purge(contesto, bid, bd.PURGE_CONFIRM_TEXT, chk["fingerprint"])
    assert q(db, "SELECT count(*) FROM buildings WHERE id = %s", (bid,))[0][0] == 1
    registro = _registro(db)
    bd.purge(sa, bid, bd.PURGE_CONFIRM_TEXT, chk["fingerprint"])
    assert q(db, "SELECT count(*) FROM buildings WHERE id = %s", (bid,))[0][0] == 0
    assert _registro(db) == registro
    meta = q(db, "SELECT metadata FROM record_lifecycle_events WHERE entity_type = 'building' AND entity_id = %s "
                 "AND action = 'purge'", (bid,))[0][0]
    assert [x["outcome"] for x in meta["preserved_references"]["voice_undos"]] == ["undone"]


def test_d_voice_non_cancella(db, sa):
    """Un utente ordinario, tutto il percorso Voice (crea, annulla tutto): nessuna DELETE fisica."""
    from property import building_deletion as bd
    prima = q(db, "SELECT count(*) FROM buildings")[0][0], q(db, "SELECT count(*) FROM properties")[0][0]
    cid, bid = _edificio_voce(db, "agent_a1", "14")
    _annulla(db, "agent_a1", cid)
    dopo = q(db, "SELECT count(*) FROM buildings")[0][0], q(db, "SELECT count(*) FROM properties")[0][0]
    assert dopo == (prima[0] + 1, prima[1])                 # l'edificio resta (nel Cestino), non sparisce
    with pytest.raises(bd.PermissionDenied):
        bd.purge(ctx(db, "agent_a1"), bid, bd.PURGE_CONFIRM_TEXT, "0" * 64)
