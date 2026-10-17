"""ELIMINAZIONE-EDIFICIO-2 - Super Admin generale DESIGNATO e cancellazione
definitiva di edifici con storico STIMA Voice, su PostgreSQL VERO (schema
completo dal runner, migration 101 compresa; database locale usa-e-getta).
Stesso banco di ELIMINAZIONE-EDIFICIO-1 (rotte vere Immobili e Platform).

  01  solo l'account designato: un altro amministratore di piattaforma, un
      account con l'email designata ma senza il ruolo, un ruolo revocato nel
      database, i ruoli d'agenzia: 403 su TUTTE le rotte riservate, nulla
      cambia, il rifiuto di un platform admin e' registrato;
  02  configurazione assente o non valida: operazioni riservate negate in
      sicurezza, il resto del CRM (richieste, altre rotte Platform) funziona;
  03  «Annulla creazione» (storico `draft -> archived`, `undo-create`) non
      blocca e si COPIA nell'evento `purge` con i collegamenti ai proprietari;
      una vera transizione di stato blocca ancora;
  04  registro STIMA Voice: un comando IN CORSO (running, indeterminate,
      pending con riferimenti congelati) blocca; uno concluso no, e il
      registro resta identico e citato nell'evento `purge`;
  05  i vincoli restano: una nota (anche dettata a Voice e segnata «per
      errore») e un documento bloccano;
  06  atomicita': copia dello storico diversa dalle tabelle, verifica dopo il
      DELETE non riuscita, audit non scritto -> nulla cancellato;
  07  sentinelle: STIMA Voice non cancella e non nomina questo modulo; ogni
      funzione riservata verifica il Super Admin prima di tutto.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, _unita, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_postgres import (  # noqa: F401
    _approva, _approvato, _codice, _edificio, _eventi, _purge, _richiedi, _stato, banco,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]


def _ctx(user_id, *, platform=True, agency_id=None, role=None):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=platform,
                           session_id=None, auth_channel="operator_session")


def _operatore(m, email, *, platform):
    riga = _q(m, "SELECT id FROM operator_users WHERE email_normalized = %s", (email,))
    if riga:
        _q(m, "UPDATE operator_users SET is_platform_admin = %s, status = 'active' WHERE id = %s", (platform, riga[0][0]))
        return riga[0][0]
    return _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, is_platform_admin) "
                 "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', 'Prova', %s) RETURNING id", (email, email, platform))[0][0]


def _riservate(m, rid, bid):
    """Le 8 rotte riservate al Super Admin generale."""
    from property import building_deletion as bd
    sa = m["sa"]
    return {
        "elenco": lambda: sa.get("/api/platform/building-deletion-requests"),
        "dettaglio": lambda: sa.get(f"/api/platform/building-deletion-requests/{rid}"),
        "approva": lambda: sa.post(f"/api/platform/building-deletion-requests/{rid}/approve",
                                   json={"fingerprint": "0" * 64}),
        "rifiuta": lambda: sa.post(f"/api/platform/building-deletion-requests/{rid}/reject", json={"note": "no"}),
        "cestino": lambda: sa.get("/api/platform/building-deletions/trash"),
        "ripristina": lambda: sa.post(f"/api/platform/building-deletions/{bid}/restore"),
        "verifica": lambda: sa.get(f"/api/platform/building-deletions/{bid}/purge-check"),
        "cancella": lambda: sa.post(f"/api/platform/building-deletions/{bid}/purge",
                                    json={"confirm_text": bd.PURGE_CONFIRM_TEXT, "fingerprint": "0" * 64}),
    }


def _foto(m, bid, rid):
    return (_q(m, "SELECT to_jsonb(b)::text FROM buildings b WHERE id = %s", (bid,)),
            _q(m, "SELECT id, to_jsonb(p)::text FROM properties p WHERE building_id = %s ORDER BY id", (bid,)),
            _q(m, "SELECT to_jsonb(r)::text FROM building_deletion_requests r WHERE id = %s", (rid,)),
            _q(m, "SELECT count(*) FROM record_lifecycle_events")[0][0])


def _negati(m, rid, bid, codice):
    for nome, chiamata in _riservate(m, rid, bid).items():
        r = chiamata()
        assert r.status_code == 403, (nome, r.status_code, r.text)
        assert r.json()["code"] == codice, (nome, r.json())


# ---------------------------------------------------------------------------
# 01 solo l'account designato
# ---------------------------------------------------------------------------

def test_01_solo_il_super_admin_designato(banco):
    m = banco
    from property import building_deletion as bd
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"]), 201)
    prima = _foto(m, e["id"], r["id"])
    negati_prima = _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = %s AND result = 'denied'",
                      (bd.AUDIT_DENIED,))[0][0]
    # 1. un ALTRO amministratore di piattaforma (ruolo vero, nel database): 403 ovunque, ogni rifiuto registrato
    altro = _operatore(m, "altro.admin@x.test", platform=True)
    m["sa_ctx"]["ctx"] = _ctx(altro)
    _negati(m, r["id"], e["id"], "SUPER_ADMIN_REQUIRED")
    negati = _q(m, "SELECT actor_user_id, metadata->>'reason' FROM platform_audit_log WHERE action = %s "
                   "AND result = 'denied' ORDER BY id DESC LIMIT 8", (bd.AUDIT_DENIED,))
    assert len(negati) == 8 and {tuple(x) for x in negati} == {(altro, "not_designated")}
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = %s AND result = 'denied'",
              (bd.AUDIT_DENIED,))[0][0] == negati_prima + 8
    # 2. l'email designata NON concede nulla: senza il ruolo, e anche con il ruolo ma senza designazione
    info = _operatore(m, "info@stima360.it", platform=False)
    for contesto in (_ctx(info, platform=False), _ctx(info, platform=True)):
        m["sa_ctx"]["ctx"] = contesto
        _negati(m, r["id"], e["id"], "SUPER_ADMIN_REQUIRED")
    _q(m, "UPDATE operator_users SET is_platform_admin = TRUE WHERE id = %s", (info,))
    _negati(m, r["id"], e["id"], "SUPER_ADMIN_REQUIRED")
    # 3. titolare, admin, agente d'agenzia (anche con l'id designato ma senza il ruolo nella sessione)
    for uid, ruolo in ((m["ids"]["owner_a"], "agency_owner"), (m["ids"]["owner_a"], "agency_admin"),
                       (m["ids"]["agent_a"], "agent"), (m["ids"]["super"], "agency_owner")):
        m["sa_ctx"]["ctx"] = _ctx(uid, platform=False, agency_id=1, role=ruolo)
        _negati(m, r["id"], e["id"], "SUPER_ADMIN_REQUIRED")
    # 4. l'account designato con il ruolo REVOCATO o disattivato nel database: la sessione non basta
    m["sa_ctx"]["ctx"] = _ctx(m["ids"]["super"])
    for revoca in ("is_platform_admin = FALSE", "status = 'disabled'"):
        _q(m, f"UPDATE operator_users SET {revoca} WHERE id = %s", (m["ids"]["super"],))
        try:
            _negati(m, r["id"], e["id"], "SUPER_ADMIN_REQUIRED")
        finally:
            _q(m, "UPDATE operator_users SET is_platform_admin = TRUE, status = 'active' WHERE id = %s",
               (m["ids"]["super"],))
    # nulla e' cambiato
    assert _foto(m, e["id"], r["id"])[:3] == prima[:3]
    assert _q(m, "SELECT count(*) FROM record_lifecycle_events")[0][0] == prima[3]
    # 5. l'account designato: si'
    ok = _codice(_approva(m, r["id"]), 200)
    assert ok["status"] == "approved" and ok["decided_by_user_id"] == m["ids"]["super"]
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (info,))


# ---------------------------------------------------------------------------
# 02 configurazione assente o non valida
# ---------------------------------------------------------------------------

def test_02_configurazione_assente_o_non_valida(banco, monkeypatch):
    m = banco
    from property import building_deletion as bd
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"]), 201)
    prima = _foto(m, e["id"], r["id"])
    sa = m["ids"]["super"]
    for valore in (None, "", "   ", "abc", "0", "-5", "1e3", "12.0", f"{sa},{sa}", "9" * 25, f"0{sa}"):
        if valore is None:
            monkeypatch.delenv(bd.SUPER_ADMIN_ENV, raising=False)
        else:
            monkeypatch.setenv(bd.SUPER_ADMIN_ENV, valore)
        assert bd.designated_super_admin_id() is None, valore
        _negati(m, r["id"], e["id"], "SUPER_ADMIN_NOT_CONFIGURED")
        # il resto del CRM funziona: stato e richieste dell'agenzia
        assert _stato(m, e["id"])["request"]["status"] == "pending"
        # ADM-1: anche la gestione della Rete e' del solo Super Admin designato:
        # senza configurazione nessuno la gestisce (fail-closed)
        assert m["sa"].get("/api/platform/agencies").status_code == 403
    altro = _edificio(m)
    assert _codice(_richiedi(m, altro["id"]), 201)["status"] == "pending"
    assert _foto(m, e["id"], r["id"])[:3] == prima[:3]
    # spazi intorno a un id valido: ammessi; la configurazione si rilegge a ogni chiamata
    monkeypatch.setenv(bd.SUPER_ADMIN_ENV, f"  {sa} ")
    assert bd.designated_super_admin_id() == sa
    _codice(_approva(m, r["id"]), 200)


# ---------------------------------------------------------------------------
# 03 «Annulla creazione» e copia dello storico
# ---------------------------------------------------------------------------

def test_03_annulla_creazione_non_blocca_e_lo_storico_si_copia(banco):
    m = banco
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    annullata = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    _codice(m["api"]().post(f"/api/property/properties/{annullata['id']}/undo-create"), 200)
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (u["id"], m["mario"]))
    storico = _q(m, "SELECT old_value, new_value, note FROM property_status_history WHERE property_id = %s ORDER BY id",
                 (annullata["id"],))
    assert ["draft", "archived", "undo-create"] in storico
    righe_storico = _q(m, "SELECT id, property_id FROM property_status_history WHERE property_id = ANY(%s) ORDER BY id",
                       ([u["id"], annullata["id"]],))
    _approvato(m, e["id"])
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e['id']}/purge-check"), 200)
    assert chk["eligible"] is True, chk["blockers"]
    conservati = {x["code"]: x for x in chk["preserved"]}
    assert conservati["STATUS_HISTORY"]["count"] == len(righe_storico)
    assert conservati["OWNER_LINKS"]["count"] == 1
    esito = _codice(_purge(m, e["id"]), 200)
    assert {x["code"] for x in esito["preserved"]} >= {"STATUS_HISTORY", "OWNER_LINKS"}
    # le righe (CASCADE) non ci sono piu', la loro copia si'
    assert _q(m, "SELECT count(*) FROM property_status_history WHERE property_id = ANY(%s)",
              ([u["id"], annullata["id"]],))[0][0] == 0
    copia = _q(m, "SELECT before_state FROM record_lifecycle_events WHERE entity_type = 'property' AND entity_id = %s "
                  "AND action = 'purge'", (annullata["id"],))[0][0]
    assert ["draft", "archived", "undo-create"] in [[x["old_value"], x["new_value"], x["note"]]
                                                    for x in copia["status_history"]]
    copiate = []
    for pid in (u["id"], annullata["id"]):
        prima = _q(m, "SELECT before_state FROM record_lifecycle_events WHERE entity_type = 'property' "
                      "AND entity_id = %s AND action = 'purge'", (pid,))[0][0]
        copiate += [[x["id"], pid] for x in prima["status_history"]]
        if pid == u["id"]:
            assert [x["contact_id"] for x in prima["owner_links"]] == [m["mario"]]
    assert sorted(copiate) == sorted(righe_storico)
    assert _q(m, "SELECT count(*) FROM contacts WHERE id = %s", (m["mario"],))[0][0] == 1
    edificio = _q(m, "SELECT metadata FROM record_lifecycle_events WHERE entity_type = 'building' AND entity_id = %s "
                     "AND action = 'purge'", (e["id"],))[0][0]
    assert edificio["copied_history"] == {"status_history_rows": len(righe_storico), "owner_links": 1}
    # una VERA transizione dopo l'annullamento (riaperta e poi in valutazione): blocca ancora
    e2 = _edificio(m)
    x = _unita(m, building_id=e2["id"], floor="1")
    _codice(m["api"]().post(f"/api/property/properties/{x['id']}/undo-create"), 200)
    _q(m, "INSERT INTO property_status_history (property_id, field_name, old_value, new_value, note) "
          "VALUES (%s, 'commercial_status', 'archived', 'draft', 'riaperta')", (x["id"],))
    _approvato(m, e2["id"])
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e2['id']}/purge-check"), 200)
    assert {b["code"] for b in chk["blockers"]} == {"FK_property_status_history"}
    assert _codice(_purge(m, e2["id"], impronta=chk["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _q(m, "SELECT count(*) FROM properties WHERE id = %s", (x["id"],))[0][0] == 1


# ---------------------------------------------------------------------------
# 04 registro STIMA Voice
# ---------------------------------------------------------------------------

def _comando(m, *passi, frozen=None):
    """Un comando vocale nel registro con i suoi passi (nascono `pending`, poi
    le transizioni ammesse dalla guardia della 098)."""
    cid = _q(m, "INSERT INTO voice_commands (agency_id, actor_user_id, client_command_id, request_sha256, input_kind, "
                "recorded_at) VALUES (1, %s, %s, %s, 'text', NOW()) RETURNING id",
             (m["ids"]["owner_a"], str(uuid.uuid4()), "b" * 64))[0][0]
    if frozen:
        _q(m, "UPDATE voice_commands SET frozen = %s::jsonb WHERE id = %s", (json.dumps(frozen), cid))
    for n, (intent, stati, risultato) in enumerate(passi, start=1):
        _q(m, "INSERT INTO voice_command_steps (command_id, ordinal, agency_id, intent, decision, step_class, "
              "client_request_id) VALUES (%s, %s, 1, %s, 'auto', 'replayable', %s)", (cid, n, intent, str(uuid.uuid4())))
        for stato in stati:
            _q(m, "UPDATE voice_command_steps SET state = %s, result = %s::jsonb WHERE command_id = %s AND ordinal = %s",
               (stato, json.dumps(risultato if stato in ("succeeded", "indeterminate") else {}), cid, n))
    return cid


def _registro_voice(m):
    return (_q(m, "SELECT to_jsonb(s)::text FROM voice_command_steps s ORDER BY command_id, ordinal"),
            _q(m, "SELECT to_jsonb(u)::text FROM voice_step_undos u ORDER BY id"),
            _q(m, "SELECT to_jsonb(c)::text FROM voice_commands c ORDER BY id"))


def test_04_voice_in_corso_blocca_concluso_si_conserva(banco):
    m = banco
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    # creato con Voice (concluso) + un «Annulla» registrato sull'edificio (scaduto)
    creato = _comando(m, ("create_building", ("running", "succeeded"), {"entity": "building", "id": e["id"],
                                                                         "created": True}))
    _q(m, "INSERT INTO voice_step_undos (agency_id, command_id, ordinal, actor_user_id, entity_type, entity_id, outcome, "
          "code) VALUES (1, %s, 1, %s, 'building', %s, 'expired', 'VOICE_UNDO_WINDOW_EXPIRED')",
       (creato, m["ids"]["owner_a"], e["id"]))
    # un comando su un'unita' con riferimenti congelati: running
    nota = _comando(m, ("add_note", ("running",), {}), frozen={"exec:1:refs": {"property": u["id"]}})
    _approvato(m, e["id"])

    def verifica():
        return _codice(m["sa"].get(f"/api/platform/building-deletions/{e['id']}/purge-check"), 200)

    chk = verifica()
    assert [b["code"] for b in chk["blockers"]] == ["VOICE_IN_PROGRESS"] and chk["blockers"][0]["count"] == 1
    assert "in corso" in chk["blockers"][0]["label"]
    assert _codice(_purge(m, e["id"], impronta=chk["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    # esito da verificare: blocca; ripresa (pending con riferimenti congelati): blocca
    _q(m, "UPDATE voice_command_steps SET state = 'indeterminate' WHERE command_id = %s", (nota,))
    assert [b["code"] for b in verifica()["blockers"]] == ["VOICE_IN_PROGRESS"]
    _q(m, "UPDATE voice_command_steps SET state = 'skipped' WHERE command_id = %s", (nota,))
    ripreso = _comando(m, ("create_unit", ("running", "pending"), {}), frozen={"exec:1:refs": {"building": e["id"]}})
    assert [b["code"] for b in verifica()["blockers"]] == ["VOICE_IN_PROGRESS"]
    _q(m, "UPDATE voice_command_steps SET state = 'skipped' WHERE command_id = %s", (ripreso,))
    # un comando non ancora partito senza riferimenti a questi dati: non c'entra
    _comando(m, ("create_unit", (), {}))
    # tutto concluso: si puo', e il registro di Voice resta identico
    chk = verifica()
    assert chk["eligible"] is True, chk["blockers"]
    conservati = {x["code"]: x for x in chk["preserved"]}
    assert conservati["VOICE_COMMANDS"]["count"] == 3 and conservati["VOICE_UNDOS"]["count"] == 1
    assert any("Crea edificio" in i["label"] for i in conservati["VOICE_COMMANDS"]["items"])
    registro = _registro_voice(m)
    _codice(_purge(m, e["id"], impronta=chk["fingerprint"]), 200)
    assert _registro_voice(m) == registro
    assert _q(m, "SELECT count(*) FROM buildings WHERE id = %s", (e["id"],))[0][0] == 0
    meta = _q(m, "SELECT metadata FROM record_lifecycle_events WHERE entity_type = 'building' AND entity_id = %s "
                 "AND action = 'purge'", (e["id"],))[0][0]
    passi = {(x["command_id"], x["ordinal"]) for x in meta["preserved_references"]["voice_steps"]}
    assert passi == {(creato, 1), (nota, 1), (ripreso, 1)}
    assert [x["outcome"] for x in meta["preserved_references"]["voice_undos"]] == ["expired"]
    unita = _q(m, "SELECT metadata FROM record_lifecycle_events WHERE entity_type = 'property' AND entity_id = %s "
                  "AND action = 'purge'", (u["id"],))[0][0]
    assert [(x["command_id"], x["intent"]) for x in unita["preserved_voice_steps"]] == [(nota, "add_note")]


# ---------------------------------------------------------------------------
# 05 i vincoli restano
# ---------------------------------------------------------------------------

def test_05_note_incarichi_documenti_bloccano_ancora(banco):
    m = banco
    e = _edificio(m)
    a = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    b = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    # nota dettata a Voice e poi segnata «creata per errore»: e' storia commerciale
    cid = _comando(m, ("add_note", ("running", "succeeded"), {"entity": "activity", "property_id": a["id"]}))
    _q(m, "INSERT INTO activities (agency_id, property_id, activity_type, description, metadata) "
          "VALUES (1, %s, 'note', 'dettata', %s::jsonb)", (a["id"], json.dumps({"voice_command_id": cid,
                                                                               "mistake": True})))
    _q(m, "INSERT INTO property_documents (property_id, document_type, title, url) VALUES (%s, 'deed', 'Atto', "
          "'https://cdn.example/atto.pdf')", (b["id"],))
    _approvato(m, e["id"])
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e['id']}/purge-check"), 200)
    codici = {x["code"] for x in chk["blockers"]}
    assert {"FK_activities", "FK_property_documents"} <= codici and chk["eligible"] is False
    assert "VOICE_COMMANDS" in {x["code"] for x in chk["preserved"]}       # il comando concluso non e' il blocco
    assert _codice(_purge(m, e["id"], impronta=chk["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 2


# ---------------------------------------------------------------------------
# 06 atomicita' e verifiche
# ---------------------------------------------------------------------------

def _pronto(m):
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    v = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    _codice(m["api"]().post(f"/api/property/properties/{v['id']}/undo-create"), 200)
    _comando(m, ("create_building", ("running", "succeeded"), {"entity": "building", "id": e["id"], "created": True}))
    r = _approvato(m, e["id"])
    return e, [u["id"], v["id"]], r


def _intatto(m, e, ids, r, storico):
    assert _q(m, "SELECT count(*) FROM buildings WHERE id = %s", (e["id"],))[0][0] == 1
    assert _q(m, "SELECT count(*) FROM properties WHERE id = ANY(%s)", (ids,))[0][0] == len(ids)
    assert _q(m, "SELECT id FROM property_status_history WHERE property_id = ANY(%s) ORDER BY id", (ids,)) == storico
    assert "purge" not in _eventi(m, "building", e["id"])
    assert _q(m, "SELECT purged_at FROM building_deletion_requests WHERE id = %s", (r["id"],))[0][0] is None
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.building_deletion.purge' "
                 "AND target_id = %s", (str(e["id"]),))[0][0] == 0


def test_06_copia_e_verifiche_atomiche(banco, monkeypatch):
    m = banco
    from platform_admin import audit as paudit
    from platform_admin.exceptions import PlatformAuditUnavailable
    from property import building_deletion as bd
    e, ids, r = _pronto(m)
    storico = _q(m, "SELECT id FROM property_status_history WHERE property_id = ANY(%s) ORDER BY id", (ids,))
    # 1. la copia scritta nell'evento non corrisponde alle tabelle: annullato PRIMA del DELETE
    originale = bd._evento

    def copia_monca(cur, ctx, agency_id, tipo, eid, azione, **k):
        if azione == "purge" and tipo == "property" and k.get("before", {}).get("status_history"):
            k["before"] = {**k["before"], "status_history": k["before"]["status_history"][:-1]}
        return originale(cur, ctx, agency_id, tipo, eid, azione, **k)
    monkeypatch.setattr(bd, "_evento", copia_monca)
    no = _codice(_purge(m, e["id"]), 409)
    assert no["code"] == "PURGE_VERIFICATION_FAILED" and no["check"] == "storico_stati"
    monkeypatch.setattr(bd, "_evento", originale)
    _intatto(m, e, ids, r, storico)
    # 2. la verifica DOPO il DELETE non torna (registro Voice cambiato): tutto annullato, DELETE compreso
    impronta = _codice(m["sa"].get(f"/api/platform/building-deletions/{e['id']}/purge-check"), 200)["fingerprint"]
    vero, chiamate = bd.riferimenti_voice, {"n": 0}

    def voce_cambiata(cur, building_id, unita):
        chiamate["n"] += 1
        esito = vero(cur, building_id, unita)
        if chiamate["n"] == 4:                           # blocchi, raccolta, copia, poi DOPO il DELETE
            esito = {**esito, "undos": esito["undos"] + [{"id": -1}]}
        return esito
    monkeypatch.setattr(bd, "riferimenti_voice", voce_cambiata)
    no = _codice(_purge(m, e["id"], impronta=impronta), 409)
    assert no["code"] == "PURGE_VERIFICATION_FAILED" and no["check"] == "riferimenti_voice_dopo", no
    assert chiamate["n"] == 4
    monkeypatch.setattr(bd, "riferimenti_voice", vero)
    _intatto(m, e, ids, r, storico)
    # 3. l'audit di piattaforma non si scrive: 503, nulla cancellato
    def senza_audit(**_k):
        raise PlatformAuditUnavailable("giu'")
    monkeypatch.setattr(paudit, "record", senza_audit)
    assert _purge(m, e["id"]).status_code == 503
    monkeypatch.undo()                                   # toglie anche connessione e designazione: si rimettono
    import platform_admin.database as pdb
    monkeypatch.setattr(pdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    monkeypatch.setenv(bd.SUPER_ADMIN_ENV, str(m["ids"]["super"]))
    _intatto(m, e, ids, r, storico)
    # 4. poi riesce: eventi con la copia, nessun riferimento rimasto
    _codice(_purge(m, e["id"]), 200)
    assert _q(m, "SELECT count(*) FROM properties WHERE id = ANY(%s)", (ids,))[0][0] == 0
    assert _q(m, "SELECT count(*) FROM record_lifecycle_events WHERE action = 'purge' AND entity_type = 'property' "
                 "AND entity_id = ANY(%s)", (ids,))[0][0] == 2
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.building_deletion.purge' "
                 "AND target_id = %s", (str(e["id"]),))[0][0] == 1


# ---------------------------------------------------------------------------
# 07 sentinelle statiche
# ---------------------------------------------------------------------------

RISERVATE = ("admin_list_requests", "admin_get_request", "approve", "reject", "admin_list_trash", "restore",
             "purge_check", "purge")


def test_07_sentinelle_voice_e_super_admin():
    # STIMA Voice: nessuna cancellazione fisica, nessun accesso a questo modulo o alla designazione
    for f in sorted((ROOT / "voice").glob("*.py")):
        testo = f.read_text(encoding="utf-8")
        assert "building_deletion" not in testo, f.name
        assert "STIMA360_SUPER_ADMIN" not in testo, f.name
        assert not re.search(r"DELETE\s+FROM\s+(properties|buildings)\b", testo, re.I), f.name
        assert "/purge" not in testo, f.name
    # ogni funzione riservata: prima istruzione `_super_admin(ctx)`, poi il ruolo riletto nel database
    sorgente = (ROOT / "property" / "building_deletion.py").read_text(encoding="utf-8")
    albero = {n.name: n for n in ast.parse(sorgente).body if isinstance(n, ast.FunctionDef)}
    for nome in RISERVATE:
        corpo = albero[nome].body
        primo = corpo[0].value if isinstance(corpo[0], ast.Expr) else None
        assert isinstance(primo, ast.Call) and getattr(primo.func, "id", None) == "_super_admin", nome
        assert "_super_admin_db(cur, ctx)" in ast.get_source_segment(sorgente, albero[nome]), nome
    # la designazione: un id dalla configurazione, mai l'email (nel codice: nomi, attributi, stringhe)
    for nome in ("_super_admin", "_super_admin_db", "designated_super_admin_id"):
        funzione = albero[nome]
        corpo = funzione.body[1:] if isinstance(funzione.body[0], ast.Expr) else funzione.body   # senza docstring
        usati = {getattr(n, "id", None) or getattr(n, "attr", None) or str(getattr(n, "value", ""))
                 for nodo in corpo for n in ast.walk(nodo) if isinstance(n, (ast.Name, ast.Attribute, ast.Constant))}
        assert not any("email" in str(x).lower() for x in usati), (nome, usati)
    assert "info@" not in sorgente and "email_normalized" not in sorgente
    # le rotte riservate esistono solo sulla superficie Platform
    tenant = (ROOT / "property" / "router.py").read_text(encoding="utf-8")
    for nome in RISERVATE:
        assert f"building_deletion.{nome}(" not in tenant and f"building_deletion.{nome}," not in tenant, nome
