"""DELETE-ARCH Fase 2B1 - fondamenta del Cestino Immobili, su PostgreSQL VERO.

Banco: la fixture `completo` (tutte le migration vere, 085 compresa, applicate
dal runner) e il `mondo` della Fase 0 (router veri, client per ruolo).
Sezioni del brief 2B1 (§10):

  A  trash semplice: stesso id, deleted_at, proprietario collegato, fuori dalla
     lista, dettaglio 404
  B  restore: torna visibile, relazioni intatte, commercial_status invariato
  C  blocker: venduto, acquisizione aperta, appuntamento futuro, Venditore
     aperto (temporaneo), incarico -> 409 TRASH_BLOCKED, nessuna modifica
  D  sicurezza: altra agenzia 404, agent non assegnato 403, agent che
     ripristina un record eliminato da altri 403, platform admin fuori acting
  E  PATCH (e archivia/riattiva) su un immobile nel Cestino -> PROPERTY_IN_TRASH
  F  restore in conflitto sull'indice parziale -> 409 RESTORE_CONFLICT, nulla
     cambia
  M  migration: colonne, vincoli, indici, registro append-only, down sicura

Prima della Fase 2B1 ogni test di questo modulo fallisce (fail-before):
le colonne, il registro e le rotte non esistono.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _acquisizione, _appuntamento, _assegna, _catena_vendita, _codice, _collega, _contatto, _futuro,
    _immobile, _lead, _property_lead, _q, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

BASE = "/api/property/properties"


def _trash(m, pid, chi="owner_a", reason="created_by_mistake", note=None):
    corpo = {"reason_code": reason}
    if note is not None:
        corpo["note"] = note
    return m["api"](chi).post(f"{BASE}/{pid}/trash", json=corpo)


def _restore(m, pid, chi="owner_a"):
    return m["api"](chi).post(f"{BASE}/{pid}/restore")


def _check(m, pid, chi="owner_a"):
    return m["api"](chi).get(f"{BASE}/{pid}/deletion-check")


def _riga(m, pid):
    r = _q(m, "SELECT to_jsonb(p) FROM properties p WHERE id = %s", (pid,))
    return r[0][0] if r else None


def _lista_ids(m, chi="owner_a", **params):
    r = m["api"](chi).get(BASE, params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _eventi(m, pid):
    return [dict(zip(("action", "reason_code", "note", "actor_user_id", "agency_id", "before_state", "metadata"), r))
            for r in _q(m, "SELECT action, reason_code, note, actor_user_id, agency_id, before_state, metadata "
                           "FROM record_lifecycle_events WHERE entity_type = 'property' AND entity_id = %s ORDER BY id",
                        (pid,))]


def _con_proprietario(m, chi="owner_a"):
    p = _immobile(m, chi)
    cid = _contatto(m, "Piero Proprietario")
    _collega(m, p["id"], cid, "owner", True)
    return p, cid


def _incarico_sql(m, pid):
    """Un incarico sull'immobile senza passare dal flusso acquisizione (che
    qui non interessa): la guardia 081 si spegne solo per questa UPDATE."""
    _q(m, "ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    try:
        _q(m, "UPDATE properties SET commercial_status = 'mandate', mandate_type = 'esclusivo', "
              "mandate_start = CURRENT_DATE, mandate_end = CURRENT_DATE + 90 WHERE id = %s", (pid,))
    finally:
        _q(m, "ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")


CATASTO = {"cadastral_municipality_code": "E058", "cadastral_section": "", "cadastral_sheet": "12",
           "cadastral_parcel": "345", "cadastral_subunit": "7"}


def _con_catasto(m, pid):
    _q(m, "UPDATE properties SET cadastral_municipality_code = %s, cadastral_section = %s, cadastral_sheet = %s, "
          "cadastral_parcel = %s, cadastral_subunit = %s WHERE id = %s", (*CATASTO.values(), pid))


# ---------------------------------------------------------------------------
# A - trash semplice
# ---------------------------------------------------------------------------

def test_a_trash_stesso_id_relazioni_intatte_fuori_da_lista_e_dettaglio(mondo):
    m = mondo
    p, cid = _con_proprietario(m)
    pid = p["id"]
    prima = _riga(m, pid)
    assert pid in _lista_ids(m)
    controllo = _codice(_check(m, pid), 200)
    assert controllo == {"can_trash": True, "blockers": []}

    esito = _codice(_trash(m, pid, note="doppione dell'altro"), 200)
    assert esito["id"] == pid and esito["deleted_at"] is not None
    assert esito["deleted_by_user_id"] == m["ids"]["owner_a"] and esito["deleted_reason"] == "created_by_mistake"

    dopo = _riga(m, pid)
    assert dopo["id"] == pid and dopo["code"] == prima["code"]
    assert dopo["commercial_status"] == prima["commercial_status"] and dopo["archived_at"] is None
    # il proprietario resta collegato, il contatto esiste
    assert _q(m, "SELECT count(*) FROM property_contacts WHERE property_id = %s AND contact_id = %s",
              (pid, cid))[0][0] == 1
    assert _q(m, "SELECT count(*) FROM contacts WHERE id = %s", (cid,))[0][0] == 1
    # fuori dalla lista principale (anche con include_archived / tutti i tipi / per contatto)
    assert pid not in _lista_ids(m)
    assert pid not in _lista_ids(m, include_archived=True, record_kind="all")
    assert pid not in _lista_ids(m, contact_id=cid)
    # dettaglio operativo: 404
    assert m["api"]().get(f"{BASE}/{pid}").status_code == 404
    # registro
    ev = _eventi(m, pid)
    assert len(ev) == 1 and ev[0]["action"] == "trash" and ev[0]["reason_code"] == "created_by_mistake"
    assert ev[0]["note"] == "doppione dell'altro" and ev[0]["actor_user_id"] == m["ids"]["owner_a"]
    assert ev[0]["agency_id"] == 1
    assert ev[0]["before_state"]["commercial_status"] == prima["commercial_status"]
    assert "title" not in ev[0]["before_state"] and "address" not in ev[0]["before_state"]  # niente snapshot
    # deletion-check legge esplicitamente il record nel Cestino
    controllo = _codice(_check(m, pid), 200)
    assert controllo["can_trash"] is False and [b["code"] for b in controllo["blockers"]] == ["ALREADY_DELETED"]
    # di nuovo -> 409 ALREADY_DELETED, nulla cambia
    _codice(_trash(m, pid), 409, "ALREADY_DELETED")
    assert _riga(m, pid) == dopo and len(_eventi(m, pid)) == 1


def test_a2_trash_non_tocca_figli_ne_storico(mondo):
    m = mondo
    p, cid = _con_proprietario(m)
    pid = p["id"]
    lid = _lead(m, cid, pipeline="sell", status="closed")
    _property_lead(m, pid, lid)
    _q(m, "INSERT INTO property_photos (property_id, url, sort_order) VALUES (%s, 'https://x.test/a.jpg', 1)", (pid,))
    _q(m, "INSERT INTO property_documents (property_id, document_type, title, status) VALUES (%s, 'other', 'Doc', 'missing')", (pid,))
    _q(m, "INSERT INTO property_price_history (property_id, old_price, new_price) VALUES (%s, 100, 200)", (pid,))
    conta = ("SELECT (SELECT count(*) FROM property_contacts WHERE property_id = %(p)s), "
             "(SELECT count(*) FROM property_leads WHERE property_id = %(p)s), "
             "(SELECT count(*) FROM property_photos WHERE property_id = %(p)s), "
             "(SELECT count(*) FROM property_documents WHERE property_id = %(p)s), "
             "(SELECT count(*) FROM property_price_history WHERE property_id = %(p)s), "
             "(SELECT count(*) FROM property_status_history WHERE property_id = %(p)s), "
             "(SELECT count(*) FROM leads WHERE id = %(l)s)")
    prima = tuple(_q(m, conta, {"p": pid, "l": lid})[0])
    _codice(_trash(m, pid, reason="duplicate"), 200)
    assert tuple(_q(m, conta, {"p": pid, "l": lid})[0]) == prima


# ---------------------------------------------------------------------------
# B - restore
# ---------------------------------------------------------------------------

def test_b_restore_torna_visibile_relazioni_e_stato_invariati(mondo):
    m = mondo
    p, cid = _con_proprietario(m)
    pid = p["id"]
    _q(m, "UPDATE properties SET commercial_status = 'active' WHERE id = %s", (pid,))
    _codice(_trash(m, pid, reason="invalid_data"), 200)
    _codice(m["api"]().patch(f"{BASE}/{pid}", json={"title": "x"}), 409, "PROPERTY_IN_TRASH")

    esito = _codice(_restore(m, pid), 200)
    assert esito["id"] == pid and esito["deleted_at"] is None
    assert esito["deleted_by_user_id"] is None and esito["deleted_reason"] is None
    assert esito["commercial_status"] == "active"
    assert pid in _lista_ids(m)
    dettaglio = _codice(m["api"]().get(f"{BASE}/{pid}"), 200)
    assert [c["contact_id"] for c in dettaglio["contacts"]] == [cid]
    ev = _eventi(m, pid)
    assert [e["action"] for e in ev] == ["trash", "restore"]
    assert ev[1]["before_state"]["deleted_reason"] == "invalid_data"
    assert ev[1]["actor_user_id"] == m["ids"]["owner_a"]
    # restore di un record non nel Cestino -> 409 NOT_DELETED
    _codice(_restore(m, pid), 409, "NOT_DELETED")
    assert len(_eventi(m, pid)) == 2


def test_b2_archiviato_nel_cestino_torna_archiviato(mondo):
    m = mondo
    pid = _immobile(m)["id"]
    _codice(m["api"]().post(f"{BASE}/{pid}/archive"), 200)
    _codice(_trash(m, pid, reason="test_record"), 200)
    esito = _codice(_restore(m, pid), 200)
    assert esito["commercial_status"] == "archived" and esito["archived_at"] is not None
    assert pid in _lista_ids(m, include_archived=True) and pid not in _lista_ids(m)


# ---------------------------------------------------------------------------
# C - blocker
# ---------------------------------------------------------------------------

def _blocca_sold(m, pid, cid):
    _q(m, "UPDATE properties SET commercial_status = 'sold' WHERE id = %s", (pid,))
    return "PROPERTY_SOLD"


def _blocca_acquisizione(m, pid, cid):
    _acquisizione(m, pid, cid)
    return "ACQUISITION_OPEN"


def _blocca_appuntamento(m, pid, cid):
    _appuntamento(m, pid)
    return "FUTURE_APPOINTMENT"


def _blocca_venditore(m, pid, cid):
    _property_lead(m, pid, _lead(m, cid, pipeline="sell", status="open"))
    return "SELLER_OPPORTUNITY_OPEN"


def _blocca_venditore_paused(m, pid, cid):
    _property_lead(m, pid, _lead(m, cid, pipeline="sell", status="paused"))
    return "SELLER_OPPORTUNITY_OPEN"


def _blocca_incarico(m, pid, cid):
    _incarico_sql(m, pid)
    return "MANDATE_PRESENT"


def _blocca_proposta(m, pid, cid):
    _catena_vendita(m, pid, _contatto(m, "Bruno Compratore"), proposta="submitted")
    return "PROPOSAL_OPEN"


def _blocca_vendita(m, pid, cid):
    _catena_vendita(m, pid, _contatto(m, "Carla Compratrice"), proposta="accepted", vendita="pending")
    return "SALE_PENDING"


@pytest.mark.parametrize("blocca", [_blocca_sold, _blocca_acquisizione, _blocca_appuntamento, _blocca_venditore,
                                    _blocca_venditore_paused, _blocca_incarico, _blocca_proposta, _blocca_vendita],
                         ids=lambda f: f.__name__.removeprefix("_blocca_"))
def test_c_blocker_409_trash_blocked_nessuna_modifica(mondo, blocca):
    m = mondo
    p, cid = _con_proprietario(m)
    pid = p["id"]
    codice = blocca(m, pid, cid)
    controllo = _codice(_check(m, pid), 200)
    assert controllo["can_trash"] is False and codice in [b["code"] for b in controllo["blockers"]], controllo
    prima = _riga(m, pid)
    corpo = _codice(_trash(m, pid), 409, "TRASH_BLOCKED")
    assert codice in [b["code"] for b in corpo["blockers"]]
    assert _riga(m, pid) == prima and _eventi(m, pid) == []


def test_c2_venditore_chiuso_e_appuntamento_passato_non_bloccano(mondo):
    m = mondo
    p, cid = _con_proprietario(m)
    pid = p["id"]
    _property_lead(m, pid, _lead(m, cid, pipeline="sell", status="closed"))
    inizio = _futuro(-10, 10)
    _q(m, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, property_id) "
          "VALUES (1, %s, 'buyer_visit', 'scheduled', %s, %s, %s)",
       (m["ids"]["owner_a"], inizio, inizio + timedelta(hours=1), pid))
    assert _codice(_check(m, pid), 200) == {"can_trash": True, "blockers": []}
    _codice(_trash(m, pid), 200)
    # la riga lead/opportunita' non e' stata toccata (nessuna chiusura automatica in 2B1)
    assert _q(m, "SELECT l.status FROM leads l JOIN property_leads pl ON pl.lead_id = l.id WHERE pl.property_id = %s",
              (pid,))[0][0] == "closed"


# ---------------------------------------------------------------------------
# D - sicurezza
# ---------------------------------------------------------------------------

def test_d_altra_agenzia_404_senza_leak(mondo):
    m = mondo
    pid = _immobile(m)["id"]
    for r in (_check(m, pid, "owner_b"), _trash(m, pid, "owner_b"), _restore(m, pid, "owner_b")):
        assert r.status_code == 404, r.text
        assert "Trilocale" not in r.text
    assert _riga(m, pid)["deleted_at"] is None
    _codice(_trash(m, pid), 200)
    r = _restore(m, pid, "owner_b")
    assert r.status_code == 404 and "deleted" not in r.text.lower()
    assert _riga(m, pid)["deleted_at"] is not None


def test_d_agent_trash_solo_assegnato_restore_solo_se_eliminato_da_lui(mondo):
    m = mondo
    del_collega = _immobile(m, "agent_a2")["id"]
    non_assegnato = _immobile(m, "owner_a")["id"]
    proprio = _immobile(m, "agent_a")["id"]
    _codice(_trash(m, del_collega, "agent_a"), 403, "NOT_ASSIGNED")
    _codice(_trash(m, non_assegnato, "agent_a"), 403, "NOT_ASSIGNED")
    _codice(_check(m, del_collega, "agent_a"), 403, "NOT_ASSIGNED")
    assert _riga(m, del_collega)["deleted_at"] is None and _riga(m, non_assegnato)["deleted_at"] is None
    # l'agent elimina il proprio e lo ripristina
    _codice(_trash(m, proprio, "agent_a"), 200)
    _codice(_restore(m, proprio, "agent_a"), 200)
    # eliminato dall'owner, anche se assegnato all'agent: l'agent non lo ripristina
    _codice(_trash(m, proprio, "owner_a"), 200)
    _codice(_restore(m, proprio, "agent_a"), 403, "NOT_DELETED_BY_YOU")
    assert _riga(m, proprio)["deleted_by_user_id"] == m["ids"]["owner_a"]
    # eliminato dall'agent: il collega non lo ripristina, l'admin si'
    _codice(_restore(m, proprio, "owner_a"), 200)
    _codice(_trash(m, proprio, "agent_a"), 200)
    _codice(_restore(m, proprio, "agent_a2"), 403)
    _codice(_restore(m, proprio, "admin_a"), 200)


def test_d_platform_admin_solo_in_acting(mondo):
    m = mondo
    pid = _immobile(m)["id"]
    for r in (_check(m, pid, "platform_none"), _trash(m, pid, "platform_none")):
        assert r.status_code == 403, r.text
    assert _riga(m, pid)["deleted_at"] is None
    esito = _codice(_trash(m, pid, "platform_acting"), 200)
    assert esito["deleted_by_user_id"] == m["ids"]["platform"]
    assert _restore(m, pid, "platform_none").status_code == 403
    _codice(_restore(m, pid, "platform_acting"), 200)


def test_d_motivo_obbligatorio_e_nel_catalogo(mondo):
    m = mondo
    pid = _immobile(m)["id"]
    for corpo in ({}, {"reason_code": "boh"}, {"reason_code": None}, {"reason_code": "other", "note": "x" * 501}):
        r = m["api"]().post(f"{BASE}/{pid}/trash", json=corpo)
        assert r.status_code in (400, 422), (corpo, r.status_code, r.text)
    assert _riga(m, pid)["deleted_at"] is None and _eventi(m, pid) == []
    for motivo in ("created_by_mistake", "duplicate", "invalid_data", "test_record", "other"):
        _codice(_trash(m, pid, reason=motivo), 200)
        _codice(_restore(m, pid), 200)


# ---------------------------------------------------------------------------
# E - mutazioni ordinarie rifiutate nel Cestino
# ---------------------------------------------------------------------------

def test_e_patch_e_archivia_rifiutati_nel_cestino(mondo):
    m = mondo
    pid = _immobile(m)["id"]
    _codice(_trash(m, pid), 200)
    prima = _riga(m, pid)
    for corpo in ({}, {"title": "Nuovo titolo"}, {"asking_price": 123456}, {"commercial_status": "active"},
                  {"assigned_agent_id": m["ids"]["agent_a"]}):
        _codice(m["api"]().patch(f"{BASE}/{pid}", json=corpo), 409, "PROPERTY_IN_TRASH")
    _codice(m["api"]().post(f"{BASE}/{pid}/archive"), 409, "PROPERTY_IN_TRASH")
    _codice(m["api"]().post(f"{BASE}/{pid}/unarchive"), 409, "PROPERTY_IN_TRASH")
    # la DELETE legacy (= archivia) non cambia significato e non tocca il Cestino
    r = m["api"]().delete(f"{BASE}/{pid}")
    assert r.status_code == 409, r.text
    assert _riga(m, pid) == prima


def test_e2_guardia_anche_sotto_lock_nel_repository(mondo):
    """La guardia vive anche nel repository, sulla riga FOR UPDATE: un
    chiamante che salta il service non scrive su un immobile nel Cestino."""
    from operator_auth.context import OperatorContext
    from property import repository
    from property.lifecycle import LifecycleConflict
    m = mondo
    pid = _immobile(m)["id"]
    _codice(_trash(m, pid), 200)
    ctx = OperatorContext(user_id=m["ids"]["owner_a"], agency_id=1, role="agency_owner", is_platform_admin=False,
                          session_id=None, auth_channel="operator_session")
    with pytest.raises(LifecycleConflict) as e:
        repository.update_property(ctx, pid, {"title": "di nascosto"})
    assert e.value.code == "PROPERTY_IN_TRASH"
    assert _riga(m, pid)["title"] != "di nascosto"


# ---------------------------------------------------------------------------
# F - restore in conflitto sull'indice parziale
# ---------------------------------------------------------------------------

def test_f_restore_conflict_su_identita_catastale_nessuna_modifica(mondo):
    m = mondo
    a = _immobile(m)["id"]
    _con_catasto(m, a)
    # finche' A e' vivo, B con la stessa identita' e' rifiutato dall'indice
    b = _immobile(m)["id"]
    r = m["api"]().patch(f"{BASE}/{b}", json=CATASTO)
    assert r.status_code == 409, r.text
    _codice(_trash(m, a, reason="duplicate"), 200)
    # A nel Cestino: B prende la stessa identita' catastale
    _codice(m["api"]().patch(f"{BASE}/{b}", json=CATASTO), 200)
    prima_a, prima_b = _riga(m, a), _riga(m, b)
    corpo = _codice(_restore(m, a), 409, "RESTORE_CONFLICT")
    assert [c["id"] for c in corpo["conflicts"]] == [b] and corpo["conflicts"][0]["index"] == "cadastral_identity"
    assert _riga(m, a) == prima_a and _riga(m, b) == prima_b
    assert [e["action"] for e in _eventi(m, a)] == ["trash"]
    # liberata l'identita' su B (dato dell'utente, non del restore), A torna
    _q(m, "UPDATE properties SET cadastral_subunit = '8' WHERE id = %s", (b,))
    _codice(_restore(m, a), 200)


CHIAVE = "11111111-1111-1111-1111-111111111111"


def _con_chiave(m, pid, chiave=CHIAVE, impronta="a" * 64):
    _q(m, "UPDATE properties SET client_request_id = %s, client_request_fingerprint = %s WHERE id = %s",
       (chiave, impronta, pid))


def test_f2_restore_conflict_su_client_request_id_nessuna_modifica(mondo):
    """REVIEW 1 (R1): un immobile nel Cestino NON occupa l'unicita' di
    `client_request_id`; il ripristino verifica il conflitto PRIMA dell'UPDATE."""
    m = mondo
    a = _immobile(m)["id"]
    _con_chiave(m, a)
    # finche' A e' vivo la chiave e' occupata
    altro = _immobile(m)["id"]
    with pytest.raises(Exception) as e:
        _con_chiave(m, altro)
    assert "uq_properties_client_request" in str(e.value)
    # 1-2. A nel Cestino
    _codice(_trash(m, a, reason="duplicate"), 200)
    # 3. B nasce con la stessa client_request_id (la chiave e' libera)
    b = _q(m, "INSERT INTO properties (agency_id, title, commercial_status, client_request_id, client_request_fingerprint) "
              "VALUES (1, 'B stessa chiave', 'draft', %s, %s) RETURNING id", (CHIAVE, "b" * 64))[0][0]
    prima_a, prima_b = _riga(m, a), _riga(m, b)
    assert prima_a["deleted_at"] is not None and prima_a["client_request_id"] == CHIAVE
    # 4-6. restore A -> 409 RESTORE_CONFLICT che indica B
    corpo = _codice(_restore(m, a), 409, "RESTORE_CONFLICT")
    assert corpo["conflicts"] == [{"id": b, "code": prima_b["code"], "index": "client_request"}], corpo
    # 7. A integralmente nel Cestino, B intatto, nessun evento di restore
    assert _riga(m, a) == prima_a and _riga(m, b) == prima_b
    assert [e["action"] for e in _eventi(m, a)] == ["trash"]
    assert pid_nella_lista(m, a) is False
    # B nel Cestino: la chiave torna libera e A si ripristina
    _codice(_trash(m, b, reason="duplicate"), 200)
    esito = _codice(_restore(m, a), 200)
    assert esito["deleted_at"] is None and esito["client_request_id"] == CHIAVE
    # ...e ora e' B a non potersi ripristinare
    corpo = _codice(_restore(m, b), 409, "RESTORE_CONFLICT")
    assert [c["id"] for c in corpo["conflicts"]] == [a]


def test_f2b_restore_conflict_su_entrambi_gli_indici_li_elenca_tutti(mondo):
    m = mondo
    a = _immobile(m)["id"]
    _con_chiave(m, a)
    _con_catasto(m, a)
    _codice(_trash(m, a), 200)
    b = _q(m, "INSERT INTO properties (agency_id, title, commercial_status, client_request_id, client_request_fingerprint) "
              "VALUES (1, 'B chiave', 'draft', %s, %s) RETURNING id", (CHIAVE, "b" * 64))[0][0]
    c = _immobile(m)["id"]
    _con_catasto(m, c)
    prima_a = _riga(m, a)
    corpo = _codice(_restore(m, a), 409, "RESTORE_CONFLICT")
    assert sorted((x["index"], x["id"]) for x in corpo["conflicts"]) == [("cadastral_identity", c), ("client_request", b)]
    assert _riga(m, a) == prima_a and [e["action"] for e in _eventi(m, a)] == ["trash"]


def pid_nella_lista(m, pid):
    return pid in _lista_ids(m, include_archived=True, record_kind="all")


def test_f3_codice_resta_univoco_anche_nel_cestino(mondo):
    m = mondo
    a = _immobile(m)
    _codice(_trash(m, a["id"]), 200)
    b = _immobile(m)["id"]
    r = m["api"]().patch(f"{BASE}/{b}", json={"code": a["code"]})
    assert r.status_code == 409, r.text
    assert _riga(m, a["id"])["code"] == a["code"]


# ---------------------------------------------------------------------------
# M - migration 085
# ---------------------------------------------------------------------------

def test_m_schema_085(mondo):
    m = mondo
    colonne = {r[0]: (r[1], r[2]) for r in _q(
        m, "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
           "WHERE table_name = 'properties' AND column_name LIKE 'deleted%%'")}
    assert colonne == {"deleted_at": ("timestamp with time zone", "YES"), "deleted_by_user_id": ("bigint", "YES"),
                       "deleted_reason": ("character varying", "YES")}
    fk = _q(m, "SELECT confdeltype, confrelid::regclass::text FROM pg_constraint "
               "WHERE conrelid = 'properties'::regclass AND contype = 'f' AND conname = 'properties_deleted_by_user_fk'")
    assert fk == [["n", "operator_users"]] or [tuple(x) for x in fk] == [("n", "operator_users")]
    indici = {r[0]: r[1] for r in _q(m, "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'properties'")}
    assert "deleted_at IS NULL" in indici["uq_properties_cadastral_identity"]
    # REVIEW 1 (R1): anche la chiave di idempotenza vale solo fuori dal Cestino
    assert "deleted_at IS NULL" in indici["uq_properties_client_request"]
    assert "client_request_id IS NOT NULL" in indici["uq_properties_client_request"]
    codice = [d for n, d in indici.items() if "UNIQUE" in d and "(code)" in d]
    assert codice and all("WHERE" not in d for d in codice)
    # catalogo motivi e coerenza dei tre campi
    pid = _immobile(m)["id"]
    for sql in ("UPDATE properties SET deleted_at = NOW(), deleted_reason = 'boh' WHERE id = %s",
                "UPDATE properties SET deleted_at = NOW() WHERE id = %s",
                "UPDATE properties SET deleted_reason = 'other' WHERE id = %s"):
        with pytest.raises(Exception):
            _q(m, sql, (pid,))
    # registro append-only
    _codice(_trash(m, pid), 200)
    for sql in ("UPDATE record_lifecycle_events SET note = 'x' WHERE entity_id = %s",
                "DELETE FROM record_lifecycle_events WHERE entity_id = %s"):
        with pytest.raises(Exception) as e:
            _q(m, sql, (pid,))
        assert "append-only" in str(e.value)
    for sql in ("INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action) VALUES (1, 'contact', 1, 'trash')",
                "INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action) VALUES (1, 'property', 1, 'purge')"):
        with pytest.raises(Exception):
            _q(m, sql)


def test_m_registro_append_only_insert_si_update_delete_no(mondo):
    """REVIEW 1 (R3): sul database vero. INSERT consentito; UPDATE e DELETE
    rifiutati dal trigger, riga intatta; l'attore e' FK RESTRICT verso
    operator_users (un operatore con eventi non si cancella, si disattiva)."""
    m = mondo
    uid = m["ids"]["owner_a"]
    eid = _q(m, "INSERT INTO record_lifecycle_events (agency_id, entity_type, entity_id, action, reason_code, "
                "actor_user_id) VALUES (1, 'property', 999999, 'trash', 'other', %s) RETURNING id", (uid,))[0][0]
    prima = _q(m, "SELECT to_jsonb(e) FROM record_lifecycle_events e WHERE id = %s", (eid,))[0][0]
    print(f"\nR3 INSERT -> ok id={eid}")
    for sql in ("UPDATE record_lifecycle_events SET note = 'riscritto' WHERE id = %s",
                "UPDATE record_lifecycle_events SET actor_user_id = NULL WHERE id = %s",
                "DELETE FROM record_lifecycle_events WHERE id = %s"):
        with pytest.raises(Exception) as e:
            _q(m, sql, (eid,))
        assert "append-only" in str(e.value), str(e.value)
        print(f"R3 {sql.split()[0]} -> rifiutato: {str(e.value).splitlines()[0]}")
    assert _q(m, "SELECT to_jsonb(e) FROM record_lifecycle_events e WHERE id = %s", (eid,))[0][0] == prima
    fk = _q(m, "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'record_lifecycle_events'::regclass "
               "AND contype = 'f' AND confrelid = 'operator_users'::regclass")
    assert [r[0] for r in fk] == ["r"]
    # il ciclo di vita reale di un operatore e' la disattivazione: consentita
    _q(m, "UPDATE operator_users SET status = 'disabled' WHERE id = %s", (uid,))
    _q(m, "UPDATE operator_users SET status = 'active' WHERE id = %s", (uid,))
    print("R3 operator_users.status disabled/active con eventi -> ok")
    # la cancellazione fisica di un operatore con eventi e' rifiutata (RESTRICT)
    with pytest.raises(Exception) as e:
        _q(m, "DELETE FROM operator_users WHERE id = %s", (uid,))
    assert "record_lifecycle_events" in str(e.value), str(e.value)
    print(f"R3 DELETE operator_users con eventi -> rifiutato: {str(e.value).splitlines()[0]}")


def test_m_down_rifiuta_con_cestino_o_registro_poi_reversibile(mondo):
    from tests.test_censimento_3_backend_postgres import MIGRAZIONI
    m = mondo
    su = (MIGRAZIONI / "085_delete_arch_2b1_property_trash.sql").read_text(encoding="utf-8")
    giu = (MIGRAZIONI / "085_delete_arch_2b1_property_trash_down.sql").read_text(encoding="utf-8")
    pid = _immobile(m)["id"]
    _codice(_trash(m, pid), 200)
    with pytest.raises(Exception) as e:
        _q(m, giu)
    assert "Nothing has been changed" in str(e.value)
    _q(m, "ROLLBACK")          # la down apre BEGIN: la sessione resta in transazione abortita
    assert _riga(m, pid)["deleted_at"] is not None
    # svuotati Cestino e registro (solo nel banco), la down passa e la up si riapplica
    _codice(_restore(m, pid), 200)
    _q(m, "ALTER TABLE record_lifecycle_events DISABLE TRIGGER trg_record_lifecycle_events_append_only")
    _q(m, "DELETE FROM record_lifecycle_events")
    _q(m, "ALTER TABLE record_lifecycle_events ENABLE TRIGGER trg_record_lifecycle_events_append_only")
    _q(m, giu)
    assert _q(m, "SELECT to_regclass('public.record_lifecycle_events')")[0][0] is None
    assert "deleted_at" not in _riga(m, pid)
    for nome in ("uq_properties_cadastral_identity", "uq_properties_client_request"):
        idx = _q(m, "SELECT indexdef FROM pg_indexes WHERE indexname = %s", (nome,))[0][0]
        assert "deleted_at" not in idx and idx.startswith("CREATE UNIQUE INDEX"), idx
    # codice deployato prima della 085: il Cestino risponde 503 leggibile, le
    # superfici ordinarie (lista, dettaglio, PATCH, archivia) funzionano
    for r in (_check(m, pid), _trash(m, pid), _restore(m, pid)):
        assert r.status_code == 503 and r.json()["code"] == "TRASH_NOT_INSTALLED", r.text
    assert pid in _lista_ids(m)
    _codice(m["api"]().get(f"{BASE}/{pid}"), 200)
    _codice(m["api"]().patch(f"{BASE}/{pid}", json={"title": "Senza 085"}), 200)
    _codice(m["api"]().patch(f"{BASE}/{pid}", json={}), 200)
    with m["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(su)
        cur.execute("COMMIT")
    assert "deleted_at" in _riga(m, pid)
    assert _q(m, "SELECT to_regclass('public.record_lifecycle_events')")[0][0] is not None


# ---------------------------------------------------------------------------
# REVIEW 2 - AGENT + STORICO PROTETTO
# ---------------------------------------------------------------------------
# Un agent manda nel Cestino solo un immobile assegnato a se', senza processi
# aperti E senza storico operativo reale: altrimenti 403 HISTORY_REQUIRES_ADMIN.
# Owner/admin: lo stesso storico non blocca. Le righe «per errore» non contano.

def _dell_agente(m):
    p = _immobile(m, "agent_a")
    assert p["assigned_agent_id"] == m["ids"]["agent_a"]
    return p["id"]


def _attivita_immobile(m, pid, tipo="call", metadata=None):
    return _q(m, "INSERT INTO activities (agency_id, property_id, activity_type, description, created_by_user_id, metadata) "
                 "VALUES (1, %s, %s, 'Telefonata al proprietario', %s, %s::jsonb) RETURNING id",
              (pid, tipo, m["ids"]["agent_a"], json.dumps(metadata or {})))[0][0]


def _appuntamento_storico(m, pid, stato="scheduled", kind=None, giorni=-10):
    inizio = _futuro(giorni, 9)
    extra_col, extra_val = "", ()
    if stato == "cancelled":
        extra_col, extra_val = ", cancelled_at, cancelled_kind", (inizio - timedelta(days=1), kind)
    return _q(m, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                 f"property_id{extra_col}) VALUES (1, %s, 'buyer_visit', %s, %s, %s, %s"
                 f"{', %s, %s' if extra_val else ''}) RETURNING id",
              (m["ids"]["agent_a"], stato, inizio, inizio + timedelta(hours=1), pid, *extra_val))[0][0]


def _acquisizione_chiusa(m, pid, per_errore):
    cid = _contatto(m, "Olga Venditrice")
    _collega(m, pid, cid, "owner", True)
    acq = _acquisizione(m, pid, cid)
    if per_errore:
        r = m["api"]().post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    else:
        r = m["api"]().post(f"/api/acquisitions/{acq['id']}/lost",
                            json={"version": acq["version"], "lost_reason": "commission", "cancel_appointment": True})
    assert r.status_code == 200, r.text


def _storia(r):
    corpo = _codice(r, 403, "HISTORY_REQUIRES_ADMIN")
    return {h["code"] for h in corpo["history"]}


def test_h1_agent_con_attivita_reale_403_admin_puo(mondo):
    m = mondo
    pid = _dell_agente(m)
    _attivita_immobile(m, pid)
    prima = _riga(m, pid)
    assert _storia(_trash(m, pid, "agent_a")) == {"PROPERTY_ACTIVITY"}
    assert _riga(m, pid) == prima and _eventi(m, pid) == []
    controllo = _codice(_check(m, pid, "agent_a"), 200)
    assert controllo["can_trash"] is False and [b["code"] for b in controllo["blockers"]] == ["HISTORY_REQUIRES_ADMIN"]
    # owner/admin: lo stesso storico non blocca
    assert _codice(_check(m, pid, "admin_a"), 200) == {"can_trash": True, "blockers": []}
    _codice(_trash(m, pid, "admin_a"), 200)


@pytest.mark.parametrize("stato,kind", [("scheduled", None), ("cancelled", "client"), ("cancelled", "agency"),
                                        ("cancelled", None)], ids=["passato", "annullato_cliente",
                                                                   "annullato_agenzia", "annullato_storico"])
def test_h2_agent_con_appuntamento_storico_reale_403_owner_puo(mondo, stato, kind):
    m = mondo
    pid = _dell_agente(m)
    _appuntamento_storico(m, pid, stato, kind)
    assert _storia(_trash(m, pid, "agent_a")) == {"APPOINTMENT_HISTORY"}
    assert _riga(m, pid)["deleted_at"] is None
    _codice(_trash(m, pid, "owner_a"), 200)


@pytest.mark.parametrize("prepara,codice", [
    (lambda m, pid: _acquisizione_chiusa(m, pid, per_errore=False), "ACQUISITION_HISTORY"),
    (lambda m, pid: _catena_vendita(m, pid, _contatto(m, "Bruno Compratore"), proposta="rejected"), "PROPOSAL_HISTORY"),
    (lambda m, pid: _q(m, "INSERT INTO property_visits (property_id, scheduled_at, status) VALUES (%s, NOW() - INTERVAL '5 days', 'completed')", (pid,)), "VISIT_HISTORY"),
    (lambda m, pid: _property_lead(m, pid, _lead(m, _contatto(m, "Sara Venditrice"), pipeline="sell", status="closed")), "SELLER_HISTORY"),
], ids=["acquisizione_terminale", "proposta_storica", "visita_legacy", "venditore_chiuso"])
def test_h2b_agent_altro_storico_reale_403_admin_puo(mondo, prepara, codice):
    m = mondo
    pid = _dell_agente(m)
    prepara(m, pid)
    assert codice in _storia(_trash(m, pid, "agent_a"))
    _codice(_trash(m, pid, "admin_a"), 200)


def test_h4_righe_per_errore_e_tecniche_non_sono_storico(mondo):
    m = mondo
    pid = _dell_agente(m)
    # attivita' segnata per errore e attivita' generata dal sistema
    _attivita_immobile(m, pid, metadata={"mistake": True})
    _attivita_immobile(m, pid, tipo="status_change")
    # appuntamento annullato «creato per errore»
    _appuntamento_storico(m, pid, "cancelled", "mistake")
    # acquisizione «creata per errore» (con il suo appuntamento annullato mistake)
    _acquisizione_chiusa(m, pid, per_errore=True)
    # opportunita' Venditore chiusa come «inserito per errore»
    lid = _lead(m, _contatto(m, "Ugo Errore"), pipeline="sell", status="closed")
    _q(m, "UPDATE leads SET lost_reason = 'created_by_mistake' WHERE id = %s", (lid,))
    _property_lead(m, pid, lid)
    # righe tecniche normali del sistema
    _q(m, "INSERT INTO property_price_history (property_id, old_price, new_price) VALUES (%s, 100, 200)", (pid,))
    _q(m, "INSERT INTO property_status_history (property_id, field_name, old_value, new_value) "
          "VALUES (%s, 'commercial_status', 'draft', 'active')", (pid,))
    assert _codice(_check(m, pid, "agent_a"), 200) == {"can_trash": True, "blockers": []}
    _codice(_trash(m, pid, "agent_a"), 200)


def test_h5_immobile_vuoto_dell_agent_resta_cestinabile(mondo):
    m = mondo
    p, cid = _con_proprietario(m, "agent_a")
    pid = p["id"]
    _q(m, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (m["ids"]["agent_a"], pid))
    assert _codice(_check(m, pid, "agent_a"), 200) == {"can_trash": True, "blockers": []}
    _codice(_trash(m, pid, "agent_a"), 200)
    _codice(_restore(m, pid, "agent_a"), 200)


def test_h6_blocker_assoluti_prima_dello_storico(mondo):
    """Processi aperti + storico: 409 TRASH_BLOCKED per tutti (anche l'agent),
    i blocchi assoluti restano quelli di prima."""
    m = mondo
    pid = _dell_agente(m)
    _attivita_immobile(m, pid)
    _appuntamento(m, pid)
    corpo = _codice(_trash(m, pid, "agent_a"), 409, "TRASH_BLOCKED")
    assert [b["code"] for b in corpo["blockers"]] == ["FUTURE_APPOINTMENT"]
    _codice(_trash(m, pid, "owner_a"), 409, "TRASH_BLOCKED")
