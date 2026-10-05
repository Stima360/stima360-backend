"""DELETE-ARCH Fase 1C - task e attivita' «creati per errore», su PostgreSQL VERO.

Banco di DELETE-ARCH Fase 0 (`mondo`: router veri, ruoli owner/admin/agent,
agenzia B, platform admin in acting e fuori). Sezioni del brief:

  Task      T1 normale invariato · T2 cancelled + metadata.mistake · T3 fuori
            dai task operativi · T4 recuperabile nello storico/filtro ·
            T5 annullamento normale != errore · T6 nessuna hard delete
  Attivita' A1 normale invariata · A2 stessa riga, metadata.mistake ·
            A3 testo originale invariato · A4 nessuna hard delete ·
            A5 storico la mostra come errore · A6 contatori operativi la escludono
  Sicurezza S1 agenzie · S2 agente solo sui propri · S3 owner/admin ·
            S4 platform admin solo in acting
"""
from __future__ import annotations

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture e helper riusati)
    DSN, _contatto, _lead, _q, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _task(m, cid, chi="agent_a", title="Richiamare", description=None, priority="normal", status="open",
          lead_id=None, metadata="{}"):
    """Riga reale scritta come la scrive il CRM, con l'autore del ruolo `chi`
    (un agente vede solo i propri contatti: crearla via API non e' il punto)."""
    rid = _q(m, "INSERT INTO tasks (agency_id, title, description, priority, status, contact_id, lead_id, "
                "metadata, created_by_user_id, completed_at) VALUES (1, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, "
                "CASE WHEN %s = 'completed' THEN NOW() END) RETURNING id",
             (title, description, priority, status, cid, lead_id, metadata, m["ruoli"][chi][0], status))[0][0]
    return {"id": rid}


def _attivita(m, cid, chi="agent_a", activity_type="call", description="Ha chiesto il prezzo", lead_id=None):
    rid = _q(m, "INSERT INTO activities (agency_id, activity_type, subject, description, outcome, contact_id, "
                "lead_id, created_by_user_id) VALUES (1, %s, 'Telefonata', %s, 'interessato', %s, %s, %s) "
                "RETURNING id", (activity_type, description, cid, lead_id, m["ruoli"][chi][0]))[0][0]
    return {"id": rid}


def _riga(m, tabella, rid):
    righe = _q(m, f"SELECT * FROM {tabella} WHERE id = %s", (rid,))
    return dict(righe[0]) if righe else None


def _ids(r):
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _segna(m, tipo, rid, chi="agent_a", nota=None):
    corpo = {} if nota is None else {"note": nota}
    return m["api"](chi).post(f"/api/core/{tipo}/{rid}/mark-mistake", json=corpo)


# ---------------------------------------------------------------------------
# TASK
# ---------------------------------------------------------------------------

def test_t1_t2_t6_task_per_errore_cancelled_con_flag_stessa_riga(mondo):
    cid = _contatto(mondo, "Tina Task")
    normale = _task(mondo, cid)
    errore = _task(mondo, cid, title="Doppione", description="creato due volte", priority="high")
    prima_normale = _riga(mondo, "tasks", normale["id"])
    totale = _q(mondo, "SELECT count(*) FROM tasks")[0][0]
    r = _segna(mondo, "tasks", errore["id"], nota="doppio click")
    assert r.status_code == 200, r.text
    dopo = _riga(mondo, "tasks", errore["id"])
    assert dopo["id"] == errore["id"] and dopo["status"] == "cancelled" and dopo["completed_at"] is None
    meta = dopo["metadata"]
    assert meta["mistake"] is True and meta["mistake_by_user_id"] == mondo["ids"]["agent_a"]
    assert meta["mistake_note"] == "doppio click" and meta["mistake_previous_status"] == "open"
    assert meta["mistake_at"]
    # contenuto originale invariato
    assert (dopo["title"], dopo["description"], dopo["priority"]) == ("Doppione", "creato due volte", "high")
    # T1: l'altro task non cambia; T6: nessuna riga sparita
    assert _riga(mondo, "tasks", normale["id"]) == prima_normale
    assert _q(mondo, "SELECT count(*) FROM tasks")[0][0] == totale
    assert r.json()["metadata"]["mistake"] is True


def test_t3_t4_t5_liste_operative_storico_e_annullamento_normale(mondo):
    cid = _contatto(mondo, "Tea Liste")
    aperto = _task(mondo, cid, title="Aperto")
    errore = _task(mondo, cid, title="Errore")
    annullato = _task(mondo, cid, title="Annullato davvero")
    assert _segna(mondo, "tasks", errore["id"]).status_code == 200
    r = mondo["api"]("agent_a").patch(f"/api/core/tasks/{annullato['id']}", json={"status": "cancelled"})
    assert r.status_code == 200 and not r.json()["metadata"].get("mistake")
    api = mondo["api"]("agent_a")
    # T3: fuori dai task operativi (aperti / in corso)
    operativi = _ids(api.get("/api/core/tasks", params={"status": "open"})) | \
        _ids(api.get("/api/core/tasks", params={"status": "in_progress"}))
    assert aperto["id"] in operativi and errore["id"] not in operativi
    from flow.adapters import load_entity
    lid = _lead(mondo, cid)
    _q(mondo, "UPDATE tasks SET lead_id = %s WHERE id IN (%s, %s)", (lid, aperto["id"], errore["id"]))
    assert load_entity("lead", lid)["open_task_count"] == 1
    # T4: recuperabile nello storico (annullati) e col filtro esplicito
    annullati = _ids(api.get("/api/core/tasks", params={"status": "cancelled"}))
    assert {errore["id"], annullato["id"]} <= annullati
    assert _ids(api.get("/api/core/tasks", params={"mistakes": "true"})) == {errore["id"]}
    # T5: annullamento normale != errore
    senza = _ids(api.get("/api/core/tasks", params={"status": "cancelled", "mistakes": "false"}))
    assert senza == {annullato["id"]}
    voce = [x for x in api.get("/api/core/tasks", params={"status": "cancelled"}).json()["items"]
            if x["id"] == errore["id"]][0]
    assert voce["metadata"]["mistake"] is True


def test_t7_regole_completato_annullato_generato_idempotenza_patch(mondo):
    cid = _contatto(mondo, "Ugo Regole")
    api = mondo["api"]("agent_a")
    completato = _task(mondo, cid, status="completed")
    r = _segna(mondo, "tasks", completato["id"])
    assert r.status_code == 409 and _riga(mondo, "tasks", completato["id"])["status"] == "completed"
    annullato = _task(mondo, cid)
    api.patch(f"/api/core/tasks/{annullato['id']}", json={"status": "cancelled"})
    assert _segna(mondo, "tasks", annullato["id"]).status_code == 409
    assert not _riga(mondo, "tasks", annullato["id"])["metadata"].get("mistake")
    generato = _q(mondo, "INSERT INTO tasks (agency_id, title, contact_id, created_by_user_id, metadata) "
                         "VALUES (1, 'Auto', %s, %s, '{\"source\": \"flow\"}') RETURNING id",
                  (cid, mondo["ids"]["agent_a"]))[0][0]
    assert _segna(mondo, "tasks", generato).status_code == 409
    # idempotenza: la seconda volta nulla cambia (nemmeno l'istante)
    errore = _task(mondo, cid)
    assert _segna(mondo, "tasks", errore["id"], nota="prima").status_code == 200
    primo = _riga(mondo, "tasks", errore["id"])
    r = _segna(mondo, "tasks", errore["id"], nota="seconda")
    assert r.status_code == 200 and _riga(mondo, "tasks", errore["id"]) == primo
    # un task segnato non si modifica piu' via PATCH (niente riapertura silenziosa)
    r = api.patch(f"/api/core/tasks/{errore['id']}", json={"status": "open"})
    assert r.status_code == 409 and _riga(mondo, "tasks", errore["id"]) == primo
    # il flag e' del server: ne' creazione ne' PATCH possono scriverlo
    r = mondo["api"]("owner_a").post("/api/core/tasks", json={"contact_id": cid, "title": "X", "metadata": {"mistake": True}})
    assert r.status_code == 400
    normale = _task(mondo, cid)
    r = api.patch(f"/api/core/tasks/{normale['id']}", json={"metadata": {"mistake": True}})
    assert r.status_code == 400 and not _riga(mondo, "tasks", normale["id"])["metadata"].get("mistake")
    r = api.patch(f"/api/core/tasks/{normale['id']}", json={"metadata": {"nota": "ok"}})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# ATTIVITA'
# ---------------------------------------------------------------------------

def test_a1_a4_attivita_per_errore_stessa_riga_testo_invariato(mondo):
    cid = _contatto(mondo, "Ada Attivita")
    normale = _attivita(mondo, cid)
    errore = _attivita(mondo, cid, description="Registrata sul contatto sbagliato")
    prima_normale = _riga(mondo, "activities", normale["id"])
    prima = _riga(mondo, "activities", errore["id"])
    totale = _q(mondo, "SELECT count(*) FROM activities")[0][0]
    r = _segna(mondo, "activities", errore["id"], nota="contatto sbagliato")
    assert r.status_code == 200, r.text
    dopo = _riga(mondo, "activities", errore["id"])
    assert dopo["id"] == errore["id"]
    for campo in ("activity_type", "subject", "description", "outcome", "occurred_at", "contact_id",
                  "lead_id", "created_by_user_id", "created_at"):
        assert dopo[campo] == prima[campo], campo
    meta = dopo["metadata"]
    assert meta["mistake"] is True and meta["mistake_by_user_id"] == mondo["ids"]["agent_a"]
    assert meta["mistake_note"] == "contatto sbagliato" and meta["mistake_at"]
    assert _riga(mondo, "activities", normale["id"]) == prima_normale
    assert _q(mondo, "SELECT count(*) FROM activities")[0][0] == totale
    # idempotenza
    assert _segna(mondo, "activities", errore["id"], nota="altro").status_code == 200
    assert _riga(mondo, "activities", errore["id"]) == dopo


def test_a5_a6_storico_la_mostra_contatori_la_escludono(mondo):
    cid = _contatto(mondo, "Elsa Storico")
    lid = _lead(mondo, cid)
    vera = _attivita(mondo, cid, lead_id=lid)
    errore = _attivita(mondo, cid, lead_id=lid)
    assert _segna(mondo, "activities", errore["id"]).status_code == 200
    api = mondo["api"]("agent_a")
    # A5: lo storico (default) la mostra, marcata; il filtro la isola
    storico = api.get("/api/core/activities", params={"contact_id": cid}).json()["items"]
    voce = [x for x in storico if x["id"] == errore["id"]][0]
    assert voce["metadata"]["mistake"] is True and voce["description"] == "Ha chiesto il prezzo"
    assert _ids(api.get("/api/core/activities", params={"mistakes": "true"})) == {errore["id"]}
    assert _ids(api.get("/api/core/activities", params={"contact_id": cid, "mistakes": "false"})) == {vera["id"]}
    c360 = mondo["api"]("owner_a").get(f"/api/crm/contacts/{cid}/360")
    assert c360.status_code == 200 and errore["id"] in {x["id"] for x in c360.json()["activities"]}
    # A6: i contatori operativi non la contano
    from flow.adapters import load_entity
    assert load_entity("lead", lid)["activity_count"] == 1
    from database_revival import eligibility
    assert "COALESCE((a.metadata->>'mistake')::boolean, FALSE) = FALSE" in eligibility._LAST_ACTIVITY_EXPR_SQL


def test_a7_attivita_generate_rifiutate_attivita_su_immobile_consentita(mondo):
    cid = _contatto(mondo, "Gino Generate")
    for tipo in ("status_change", "system", "valuation"):
        aid = _q(mondo, "INSERT INTO activities (agency_id, activity_type, contact_id, created_by_user_id) "
                        "VALUES (1, %s, %s, %s) RETURNING id", (tipo, cid, mondo["ids"]["agent_a"]))[0][0]
        r = _segna(mondo, "activities", aid)
        assert r.status_code == 409, tipo
        assert not _riga(mondo, "activities", aid)["metadata"].get("mistake")
    # un'interazione d'immobile (storico non cancellabile) si puo' segnare: resta dov'e'
    pid = mondo["api"]().post("/api/property/properties", json={
        "title": "Bilocale", "property_type": "apartment", "city": "Fermo"}).json()["id"]
    mondo["api"]().post(f"/api/property/properties/{pid}/contacts", json={"contact_id": cid, "role": "owner"})
    r = mondo["api"]("owner_a").post(f"/api/property/properties/{pid}/interactions",
                                     json={"interaction_type": "call", "note": "telefonata", "contact_id": cid})
    assert r.status_code == 201, r.text
    aid = _q(mondo, "SELECT id FROM activities WHERE property_id = %s", (pid,))[0][0]
    assert _segna(mondo, "activities", aid, chi="owner_a").status_code == 200
    riga = _riga(mondo, "activities", aid)
    assert riga["property_id"] == pid and riga["metadata"]["mistake"] is True
    # il flag e' del server anche per le attivita'
    r = mondo["api"]("owner_a").post("/api/core/activities", json={
        "contact_id": cid, "activity_type": "note", "metadata": {"mistake": True}})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# SICUREZZA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tipo", ["tasks", "activities"])
def test_s1_s4_scope_ruoli_e_platform(mondo, tipo):
    cid = _contatto(mondo, "Sara Scope")
    crea = _task if tipo == "tasks" else _attivita
    tabella = tipo
    del_collega = crea(mondo, cid, chi="agent_a2")
    prima = _riga(mondo, tabella, del_collega["id"])
    # S1: altra agenzia -> 404, nulla cambia
    assert _segna(mondo, tipo, del_collega["id"], chi="owner_b").status_code == 404
    # S2: un agente non corregge il record di un collega (403); il proprio si'
    assert _segna(mondo, tipo, del_collega["id"], chi="agent_a").status_code == 403
    # S4: platform admin fuori acting -> 403
    assert _segna(mondo, tipo, del_collega["id"], chi="platform_none").status_code == 403
    assert _riga(mondo, tabella, del_collega["id"]) == prima
    assert _segna(mondo, tipo, crea(mondo, cid, chi="agent_a")["id"], chi="agent_a").status_code == 200
    # S3: owner e admin sull'agenzia; S4: platform admin in acting come admin
    for chi in ("owner_a", "admin_a", "platform_acting"):
        rid = crea(mondo, cid, chi="agent_a2")["id"]
        r = _segna(mondo, tipo, rid, chi=chi)
        assert r.status_code == 200, (chi, r.text)
        uid = mondo["ruoli"][chi][0]
        assert _riga(mondo, tabella, rid)["metadata"]["mistake_by_user_id"] == uid    # attore reale
    assert _segna(mondo, tipo, 999999, chi="owner_a").status_code == 404


# ---------------------------------------------------------------------------
# REVIEW 2 - nessuna hard delete via API; metadata server-owned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chi", ["owner_a", "admin_a", "agent_a", "platform_acting"])
def test_r2a_delete_task_e_attivita_405_la_riga_resta(mondo, chi):
    """Anche sui record PROPRI di owner/admin (prima: 204 e riga sparita)."""
    cid = _contatto(mondo, "Dora Delete")
    task = _task(mondo, cid, chi=chi)
    att = _attivita(mondo, cid, chi=chi)
    prima = (_riga(mondo, "tasks", task["id"]), _riga(mondo, "activities", att["id"]))
    totali = _q(mondo, "SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM activities)")[0]
    api = mondo["api"](chi)
    for tipo, rid, azione in (("tasks", task["id"], "Creato per errore"),
                              ("activities", att["id"], "Inserita per errore")):
        r = api.delete(f"/api/core/{tipo}/{rid}")
        assert r.status_code == 405, (tipo, r.text)
        corpo = r.json()
        assert corpo["code"] == "HARD_DELETE_DISABLED" and azione in corpo["detail"]
        assert corpo["use"] == f"/api/core/{tipo}/{rid}/mark-mistake"
    assert api.delete(f"/api/core/tasks/{task['id']}").headers["allow"] == "PATCH"
    assert (_riga(mondo, "tasks", task["id"]), _riga(mondo, "activities", att["id"])) == prima
    assert _q(mondo, "SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM activities)")[0] == totali
    # l'azione indicata funziona sulla stessa riga
    assert api.post(corpo["use"], json={}).status_code == 200
    assert _riga(mondo, "activities", att["id"])["metadata"]["mistake"] is True


def test_r2b_delete_fuori_agenzia_e_inesistente_405_senza_toccare_nulla(mondo):
    cid = _contatto(mondo, "Ezio Estraneo")
    task = _task(mondo, cid, chi="owner_a")
    prima = _riga(mondo, "tasks", task["id"])
    assert mondo["api"]("owner_b").delete(f"/api/core/tasks/{task['id']}").status_code == 405
    assert mondo["api"]("owner_a").delete("/api/core/activities/999999").status_code == 405
    assert _riga(mondo, "tasks", task["id"]) == prima


@pytest.mark.parametrize("chiave", ["mistake", "mistake_at", "mistake_by_user_id", "mistake_note",
                                    "mistake_previous_status"])
def test_r2c_post_con_metadata_server_owned_400(mondo, chiave):
    cid = _contatto(mondo, "Fede Forgia")
    api = mondo["api"]("owner_a")
    prima = _q(mondo, "SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM activities)")[0]
    valore = True if chiave == "mistake" else "x"
    r = api.post("/api/core/activities", json={"contact_id": cid, "activity_type": "note",
                                               "metadata": {chiave: valore}})
    assert r.status_code == 400 and "gestito dal server" in r.json()["detail"], r.text
    r = api.post("/api/core/tasks", json={"contact_id": cid, "title": "X", "metadata": {chiave: valore}})
    assert r.status_code == 400, r.text
    assert _q(mondo, "SELECT (SELECT count(*) FROM tasks), (SELECT count(*) FROM activities)")[0] == prima


def test_r2d_metadata_applicativo_normale_funziona_e_mark_scrive_i_campi_server(mondo):
    cid = _contatto(mondo, "Gaia Giusta")
    api = mondo["api"]("owner_a")
    r = api.post("/api/core/activities", json={"contact_id": cid, "activity_type": "note",
                                               "metadata": {"context": "manuale", "canale": "telefono"}})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert _riga(mondo, "activities", aid)["metadata"] == {"context": "manuale", "canale": "telefono"}
    r = api.post(f"/api/core/activities/{aid}/mark-mistake", json={"note": "doppia"})
    assert r.status_code == 200, r.text
    meta = _riga(mondo, "activities", aid)["metadata"]
    assert meta["context"] == "manuale" and meta["canale"] == "telefono"          # l'applicativo resta
    assert meta["mistake"] is True and meta["mistake_note"] == "doppia" and meta["mistake_at"]
    assert meta["mistake_by_user_id"] == mondo["ids"]["owner_a"]
