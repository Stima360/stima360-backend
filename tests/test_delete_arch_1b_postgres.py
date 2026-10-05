"""DELETE-ARCH Fase 1B - Vende «Inserito per errore», su PostgreSQL VERO.

Stesso banco di VENDITORI-1 (rotte reali di property, core, crm, acquisitions).
Sezioni del brief:

  A  lead CREATO da Vende -> closed/lost/created_by_mistake, relazione seller resta
  B  lead RIUSATO -> stato precedente ripristinato, relazione seller tolta
  C  lead RIAPERTO -> stato precedente ripristinato, mai created_by_mistake
  D  lead vero da stima -> ne' chiuso ne' distrutto; stima e relazione origin restano
  E  acquisizione aperta -> 409 ACQUISITION_OPEN, nessuna modifica parziale
  F  acquisizione terminale -> consentito
  G  worklist
  H  idempotenza
  I  multi-agenzia
  J  agente su lead non suo
  K  platform admin fuori acting
  L  metadata dell'attivazione (created / reused / reopened, previous)
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_venditori_1_postgres import (  # noqa: F401  (fixture e helper riusati)
    DSN, _attiva, _contatto, _coppie, _disattiva, _immobile, _link_seller, _proprietario, _q, _worklist,
    base, completo, m,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _lead(m, lead_id):
    return _q(m, "SELECT stage, status, lost_reason, closed_at FROM leads WHERE id = %s", (lead_id,))[0]


def _meta(m, lead_id, evento):
    righe = _q(m, "SELECT metadata FROM activities WHERE lead_id = %s AND metadata->>'context' = 'seller' "
                  "AND metadata->>'event' = %s ORDER BY id", (lead_id, evento))
    return [r[0] for r in righe]


def _relazione(m, property_id, lead_id):
    righe = _q(m, "SELECT relation_type FROM property_leads WHERE property_id = %s AND lead_id = %s",
               (property_id, lead_id))
    return righe[0][0] if righe else None


def _conta(m, tabella, where="TRUE", par=None):
    return _q(m, f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


def _lead_sell(m, contact_id, stage="qualified", status="open", source="manual", lost_reason=None, agente=None):
    return _q(m, "INSERT INTO leads (agency_id, contact_id, pipeline, stage, status, source, lost_reason, "
                 "closed_at, assigned_agent_id) VALUES (1, %s, 'sell', %s, %s, %s, %s, "
                 "CASE WHEN %s = 'closed' THEN NOW() - interval '30 days' END, %s) RETURNING id",
              (contact_id, stage, status, source, lost_reason, status, agente))[0][0]


def _scena(m, nome="Mario Errore"):
    p = _immobile(m, address=f"Via {uuid.uuid4().hex[:6]}")
    mario = _contatto(m, nome)
    _proprietario(m, p["id"], mario)
    return p, mario


_GIORNO = iter(range(1, 28))


def _acquisizione(m, p, mario, lead):
    g = next(_GIORNO)
    r = m["api"]().post("/api/acquisitions", json={
        "property_id": p["id"], "owner_contact_id": mario, "lead_id": lead, "source": "seller_lead",
        "appointment": {"start_at": f"2031-04-{g:02d}T10:00:00+01:00", "end_at": f"2031-04-{g:02d}T11:00:00+01:00",
                        "assigned_user_id": m["ids"]["owner_a"], "client_request_id": str(uuid.uuid4())}})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# A / L - lead creato da Vende
# ---------------------------------------------------------------------------

def test_a_lead_creato_da_vende_chiuso_come_errore_relazione_resta(m):
    p, mario = _scena(m)
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 201 and r.json()["created"] is True
    lead = r.json()["lead_id"]
    # L: metadata di attivazione
    [att] = _meta(m, lead, "activated")
    assert att["origin"] == "created" and att["lead_source"] == "property_seller"
    assert att["previous"] == {"stage": None, "status": None, "lost_reason": None, "closed_at": None,
                               "relation_type": None}
    r = _disattiva(m, p["id"], mario, "mistake", note="doppio click")
    assert r.status_code == 200, r.text
    stage, status, motivo, chiuso = _lead(m, lead)
    assert (stage, status, motivo) == ("lost", "closed", "created_by_mistake") and chiuso is not None
    assert _relazione(m, p["id"], lead) == "seller"                       # prova storica dell'errore
    # REVIEW 2: la scheda immobile riceve il motivo, cosi' la tab Proprietari
    # distingue l'errore da una chiusura «Non vende»
    dettaglio = m["api"]().get(f"/api/property/properties/{p['id']}").json()
    [voce] = [x for x in dettaglio["leads"] if x["lead_id"] == lead]
    assert voce["status"] == "closed" and voce["lost_reason"] == "created_by_mistake"
    [ev] = _meta(m, lead, "mistake")
    assert ev["origin"] == "created" and ev["action"] == "closed" and ev["unlinked"] is False
    assert ev["lead_id"] == lead and ev["property_id"] == p["id"] and ev["note"] == "doppio click"
    testo = _q(m, "SELECT description FROM activities WHERE lead_id = %s AND metadata->>'event' = 'mistake'",
               (lead,))[0][0]
    assert testo.startswith("Vende: inserito per errore")
    # G: fuori da tutte le viste normali, dentro solo «Inseriti per errore»
    for stato in ("active", "paused", "closed", "all"):
        assert (mario, p["id"]) not in _coppie(_worklist(m, status=stato)), stato
    errori = _worklist(m, status="mistakes")
    assert _coppie(errori) == {(mario, p["id"])}
    assert errori["items"][0]["lost_reason_label"] == "Inserito per errore"
    assert "mistakes" in {s["value"] for s in errori["statuses"]}


# ---------------------------------------------------------------------------
# B / L - lead riusato
# ---------------------------------------------------------------------------

def test_b_lead_riusato_ripristinato_e_scollegato_storico_intatto(m):
    p, mario = _scena(m)
    vero = _lead_sell(m, mario, stage="qualified")
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 200 and r.json()["lead_id"] == vero and r.json()["reused"] is True
    [att] = _meta(m, vero, "activated")
    assert att["origin"] == "reused" and att["lead_source"] == "manual"
    assert att["previous"] == {"stage": "qualified", "status": "open", "lost_reason": None, "closed_at": None,
                               "relation_type": None}
    # nel frattempo: un'interazione sull'immobile e un task
    m["api"]().post(f"/api/property/properties/{p['id']}/interactions",
                    json={"interaction_type": "call", "note": "telefonata", "contact_id": mario,
                          "lead_id": vero, "context": "seller"})
    m["api"]().post("/api/core/tasks", json={"lead_id": vero, "contact_id": mario, "title": "Richiamare",
                                             "due_at": "2030-01-10T09:00:00+01:00"})
    _q(m, "UPDATE leads SET stage = 'contacted' WHERE id = %s", (vero,))
    attivita = _conta(m, "activities", "property_id = %s", (p["id"],))
    task = _conta(m, "tasks", "lead_id = %s", (vero,))
    r = _disattiva(m, p["id"], mario, "mistake")
    assert r.status_code == 200, r.text
    assert r.json()["unlinked"] is True and r.json()["origin"] == "reused"
    assert _lead(m, vero) == ["qualified", "open", None, None]           # stato di PRIMA, non chiuso
    assert _relazione(m, p["id"], vero) is None                           # relazione sbagliata tolta
    assert _conta(m, "activities", "property_id = %s", (p["id"],)) == attivita + 1   # niente spostato/cancellato
    assert _conta(m, "activities", "property_id = %s AND lead_id = %s AND activity_type = 'call'",
                  (p["id"], vero)) == 1
    assert _conta(m, "tasks", "lead_id = %s", (vero,)) == task
    [ev] = _meta(m, vero, "mistake")
    assert ev["action"] == "restored" and ev["relation"] == "unlinked" and ev["unlinked"] is True
    assert ev["restored"] == {"stage": "qualified", "status": "open", "lost_reason": None}
    # G: non compare piu' su quell'immobile
    for stato in ("active", "paused", "closed", "all", "mistakes"):
        assert (mario, p["id"]) not in _coppie(_worklist(m, status=stato)), stato
    # il lead e' di nuovo libero: si riusa su un altro immobile
    p2 = _immobile(m, address="Via Libera", civic_number="3")
    _proprietario(m, p2["id"], mario)
    r = _attiva(m, p2["id"], mario)
    assert r.status_code == 200 and r.json()["lead_id"] == vero


# ---------------------------------------------------------------------------
# C / L - lead riaperto
# ---------------------------------------------------------------------------

def test_c1_lead_sospeso_riaperto_torna_sospeso_mai_created_by_mistake(m):
    p, mario = _scena(m)
    sospeso = _lead_sell(m, mario, stage="contacted", status="paused")
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 200 and r.json()["lead_id"] == sospeso
    [att] = _meta(m, sospeso, "activated")
    assert att["origin"] == "reopened"
    assert att["previous"]["status"] == "paused" and att["previous"]["stage"] == "contacted"
    assert _disattiva(m, p["id"], mario, "mistake").status_code == 200
    assert _lead(m, sospeso) == ["contacted", "paused", None, None]
    assert _relazione(m, p["id"], sospeso) is None


def test_c2_opportunita_chiusa_riattivata_torna_chiusa_col_motivo_vero(m):
    """Riapertura della STESSA opportunita' seller (relazione gia' esistente):
    torna chiusa con il suo motivo reale, la relazione (gia' seller prima)
    resta."""
    p, mario = _scena(m)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    assert _disattiva(m, p["id"], mario, "not_selling", note="affitta").status_code == 200
    chiuso_prima = _lead(m, lead)
    assert chiuso_prima[2] == "Non vende più: affitta"
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 200 and r.json()["reopened"] is True
    att = _meta(m, lead, "activated")[-1]
    assert att["origin"] == "reopened" and att["previous"]["status"] == "closed"
    assert att["previous"]["lost_reason"] == "Non vende più: affitta" and att["previous"]["relation_type"] == "seller"
    assert att["previous"]["closed_at"] is not None
    r = _disattiva(m, p["id"], mario, "mistake")
    assert r.status_code == 200, r.text
    dopo = _lead(m, lead)
    assert dopo[:3] == ["lost", "closed", "Non vende più: affitta"] and dopo[2] != "created_by_mistake"
    assert dopo[3] == chiuso_prima[3]                                     # closed_at di prima
    assert _relazione(m, p["id"], lead) == "seller"
    assert (mario, p["id"]) in _coppie(_worklist(m, status="closed"))


# ---------------------------------------------------------------------------
# D - lead vero da stima
# ---------------------------------------------------------------------------

def test_d_lead_da_stima_ne_chiuso_ne_distrutto(m):
    p, mario = _scena(m)
    stima = _q(m, "INSERT INTO stime (agency_id, comune, nome, cognome, email) "
                  "VALUES (1, 'Fermo', 'Mario', 'Stima', 'm@x.test') RETURNING id")[0][0]
    try:
        vero = _lead_sell(m, mario, stage="contacted", source="stima")
        _q(m, "INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s, %s)", (vero, stima))
        _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'origin')",
           (p["id"], vero))
        r = _attiva(m, p["id"], mario)
        assert r.status_code == 200 and r.json()["lead_id"] == vero, r.text
        assert _relazione(m, p["id"], vero) == "seller"
        [att] = _meta(m, vero, "activated")
        assert att["origin"] == "reused" and att["previous"]["relation_type"] == "origin"
        assert att["lead_source"] == "stima"
        r = _disattiva(m, p["id"], mario, "mistake")
        assert r.status_code == 200, r.text
        assert _lead(m, vero) == ["contacted", "open", None, None]
        assert _relazione(m, p["id"], vero) == "origin"                   # torna com'era, non cancellata
        assert _q(m, "SELECT stima_id FROM lead_stime WHERE lead_id = %s", (vero,)) == [[stima]]
        assert _meta(m, vero, "mistake")[0]["relation"] == "restored:origin"
    finally:
        _q(m, "DELETE FROM lead_stime WHERE stima_id = %s", (stima,))
        _q(m, "DELETE FROM stime WHERE id = %s", (stima,))


# ---------------------------------------------------------------------------
# E / F - acquisizioni
# ---------------------------------------------------------------------------

def _istantanea(m, p, lead):
    return (_lead(m, lead), _q(m, "SELECT relation_type FROM property_leads WHERE lead_id = %s ORDER BY id", (lead,)),
            _conta(m, "activities"), _conta(m, "acquisitions", "status NOT IN ('acquired', 'lost')"))


def test_e_acquisizione_aperta_409_nessuna_modifica(m):
    for origine in ("created", "reused"):
        p, mario = _scena(m, f"Mario {origine}")
        if origine == "reused":
            _lead_sell(m, mario)
        lead = _attiva(m, p["id"], mario).json()["lead_id"]
        acq = _acquisizione(m, p, mario, lead)
        prima = _istantanea(m, p, lead)
        r = _disattiva(m, p["id"], mario, "mistake")
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "ACQUISITION_OPEN" and r.json()["acquisition_id"] == acq["id"]
        assert r.json()["detail"] == "Segna prima l'acquisizione come creata per errore."
        assert _istantanea(m, p, lead) == prima, origine
        assert _q(m, "SELECT status FROM acquisitions WHERE id = %s", (acq["id"],)) == [["appointment_set"]]


def test_f_acquisizione_terminale_consente_e_resta(m):
    p, mario = _scena(m)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    acq = _acquisizione(m, p, mario, lead)
    r = m["api"]().post(f"/api/acquisitions/{acq['id']}/lost", json={"version": acq["version"], "lost_reason": "other"})
    assert r.status_code == 200, r.text
    eventi = _conta(m, "acquisition_events", "acquisition_id = %s", (acq["id"],))
    r = _disattiva(m, p["id"], mario, "mistake")
    assert r.status_code == 200, r.text
    assert _lead(m, lead)[2] == "created_by_mistake"
    assert _q(m, "SELECT status, lead_id FROM acquisitions WHERE id = %s", (acq["id"],)) == [["lost", lead]]
    assert _conta(m, "acquisition_events", "acquisition_id = %s", (acq["id"],)) == eventi


# ---------------------------------------------------------------------------
# H - idempotenza
# ---------------------------------------------------------------------------

def test_h_idempotenza_created_e_reused(m):
    p, mario = _scena(m)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    assert _disattiva(m, p["id"], mario, "mistake").status_code == 200
    stato, attivita = _lead(m, lead), _conta(m, "activities")
    r = _disattiva(m, p["id"], mario, "mistake")
    assert r.status_code == 200 and r.json()["already"] is True
    assert _lead(m, lead) == stato and _conta(m, "activities") == attivita

    p2, anna = _scena(m, "Anna Riuso")
    vero = _lead_sell(m, anna)
    altro_p = _immobile(m, address="Via Altra", civic_number="9")
    altro_lead = _lead_sell(m, anna, stage="new")
    _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'related')",
       (altro_p["id"], altro_lead))
    assert _attiva(m, p2["id"], anna, lead_id=vero).status_code == 200
    assert _disattiva(m, p2["id"], anna, "mistake").status_code == 200
    stato, attivita = _lead(m, vero), _conta(m, "activities")
    relazioni = _conta(m, "property_leads")
    for _ in range(2):
        r = _disattiva(m, p2["id"], anna, "mistake")
        assert r.status_code == 200 and r.json()["already"] is True and r.json()["lead_id"] == vero, r.text
    assert _lead(m, vero) == stato and stato[1] == "open"                # mai chiuso
    assert _conta(m, "activities") == attivita and _conta(m, "property_leads") == relazioni
    assert _relazione(m, altro_p["id"], altro_lead) == "related"          # altre relazioni intatte


def test_h2_attivazione_precedente_alla_fase_senza_origin(m):
    """Attivazioni scritte prima della 1B (nessun `origin`): un lead nato da
    Vende si chiude come errore; un lead vero NON si chiude (409)."""
    p, mario = _scena(m)
    nato = _attiva(m, p["id"], mario).json()["lead_id"]
    _q(m, "UPDATE activities SET metadata = metadata - 'origin' - 'previous' - 'lead_source' WHERE lead_id = %s", (nato,))
    assert _disattiva(m, p["id"], mario, "mistake").status_code == 200
    assert _lead(m, nato)[2] == "created_by_mistake"

    p2, anna = _scena(m, "Anna Legacy")
    vero = _lead_sell(m, anna)
    _attiva(m, p2["id"], anna)
    _q(m, "UPDATE activities SET metadata = metadata - 'origin' - 'previous' - 'lead_source' WHERE lead_id = %s", (vero,))
    prima = _lead(m, vero)
    r = _disattiva(m, p2["id"], anna, "mistake")
    assert r.status_code == 409 and r.json()["code"] == "SELLER_MISTAKE_ORIGIN_UNKNOWN"
    assert _lead(m, vero) == prima and _relazione(m, p2["id"], vero) == "seller"


# ---------------------------------------------------------------------------
# I / J / K - scope e permessi
# ---------------------------------------------------------------------------

def test_i_multi_agenzia(m):
    p, mario = _scena(m)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    prima = _lead(m, lead)
    r = _disattiva(m, p["id"], mario, "mistake", chi="owner_b")
    assert r.status_code == 404
    assert _lead(m, lead) == prima and _relazione(m, p["id"], lead) == "seller"


def test_j_agente_su_lead_non_suo_404_sul_proprio_ok(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Agente", agente=m["ids"]["agent_a"])
    _proprietario(m, p["id"], mario)
    lead = _attiva(m, p["id"], mario, chi="agent_a").json()["lead_id"]
    prima = _lead(m, lead)
    r = _disattiva(m, p["id"], mario, "mistake", chi="agent_a2")
    assert r.status_code == 404
    assert _lead(m, lead) == prima
    r = _disattiva(m, p["id"], mario, "mistake", chi="agent_a")
    assert r.status_code == 200, r.text
    assert _lead(m, lead)[2] == "created_by_mistake"
    ev = _q(m, "SELECT created_by_user_id FROM activities WHERE lead_id = %s AND metadata->>'event' = 'mistake'",
            (lead,))[0][0]
    assert ev == m["ids"]["agent_a"]                                      # attore reale


def test_k_platform_admin_fuori_acting_rifiutato(m):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from crm.router import router as crm
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context

    p, mario = _scena(m)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    prima = _lead(m, lead)
    app = FastAPI()
    app.include_router(crm)
    app.dependency_overrides[legacy_basic_agency_context] = lambda: OperatorContext(
        user_id=m["ids"]["owner_a"], agency_id=None, role=None, is_platform_admin=True,
        session_id=None, auth_channel="operator_session")
    client = TestClient(app)
    r = client.post("/api/crm/sellers/deactivate",
                    json={"property_id": p["id"], "contact_id": mario, "outcome": "mistake"})
    assert r.status_code == 403, r.text
    assert client.get("/api/crm/sellers", params={"status": "mistakes"}).status_code == 403
    assert _lead(m, lead) == prima
    # in acting nell'agenzia: come un admin, con l'attore reale
    app.dependency_overrides[legacy_basic_agency_context] = lambda: OperatorContext(
        user_id=m["ids"]["owner_b"], agency_id=1, role=None, is_platform_admin=True,
        session_id=None, auth_channel="operator_session")
    r = client.post("/api/crm/sellers/deactivate",
                    json={"property_id": p["id"], "contact_id": mario, "outcome": "mistake"})
    assert r.status_code == 200, r.text
    assert _q(m, "SELECT created_by_user_id FROM activities WHERE lead_id = %s AND metadata->>'event' = 'mistake'",
              (lead,)) == [[m["ids"]["owner_b"]]]


# ---------------------------------------------------------------------------
# REVIEW 2 - l'attivazione da annullare e' quella giusta
# ---------------------------------------------------------------------------

def _attivazioni(m, lead_id):
    return [r[0] for r in _q(m, "SELECT id FROM activities WHERE lead_id = %s AND metadata->>'event' = 'activated' "
                                "ORDER BY id", (lead_id,))]


def test_r2a_cross_property_usa_solo_l_attivazione_di_b(m):
    """Stesso contatto, stesso lead: Vende su A, errore su A (scollegato), il
    lead cambia, Vende su B, errore su B -> torna allo stato di PRIMA DI B; i
    metadata di A non contano e A non viene toccato."""
    a, mario = _scena(m, "Mario Cross")
    vero = _lead_sell(m, mario, stage="qualified")
    assert _attiva(m, a["id"], mario).json()["lead_id"] == vero
    assert _disattiva(m, a["id"], mario, "mistake").status_code == 200       # scollegato da A
    assert _relazione(m, a["id"], vero) is None
    _q(m, "UPDATE leads SET stage = 'appointment' WHERE id = %s", (vero,))  # il lead vive: stato diverso da quello salvato per A
    b = _immobile(m, address="Via B", civic_number="2")
    _proprietario(m, b["id"], mario)
    r = _attiva(m, b["id"], mario)
    assert r.status_code == 200 and r.json()["lead_id"] == vero
    att_a, att_b = _attivazioni(m, vero)[:2]
    meta_b = _q(m, "SELECT metadata FROM activities WHERE id = %s", (att_b,))[0][0]
    assert meta_b["property_id"] == b["id"] and meta_b["previous"]["stage"] == "appointment"
    # esche PIU' RECENTI dell'attivazione di B, che la ricerca deve ignorare:
    #  - un'attivazione dello STESSO lead su A (altro immobile);
    #  - un'attivazione di un ALTRO lead su B (stesso immobile).
    esca = {"context": "seller", "event": "activated", "origin": "reused", "lead_source": "manual",
            "previous": {"stage": "won", "status": "closed", "lost_reason": "esca", "closed_at": None,
                         "relation_type": None}}
    altro = _lead_sell(m, mario, stage="new")
    for lead_esca, immobile in ((vero, a["id"]), (altro, b["id"])):
        _q(m, "INSERT INTO activities (agency_id, contact_id, lead_id, property_id, activity_type, description, metadata) "
              "VALUES (1, %s, %s, %s, 'status_change', 'esca', %s::jsonb)",
           (mario, lead_esca, immobile, __import__("json").dumps({**esca, "property_id": immobile})))
    storia_a = _q(m, "SELECT id, metadata FROM activities WHERE (metadata->>'property_id')::bigint = %s ORDER BY id",
                  (a["id"],))
    _q(m, "UPDATE leads SET stage = 'contacted' WHERE id = %s", (vero,))
    r = _disattiva(m, b["id"], mario, "mistake")
    assert r.status_code == 200, r.text
    assert _lead(m, vero) == ["appointment", "open", None, None]           # prima di B, non 'qualified' di A
    ev = [x for x in _meta(m, vero, "mistake") if x["property_id"] == b["id"]]
    assert len(ev) == 1 and ev[0]["activation_activity_id"] == att_b and ev[0]["activation_activity_id"] != att_a
    assert _relazione(m, b["id"], vero) is None and _relazione(m, a["id"], vero) is None
    assert _q(m, "SELECT id, metadata FROM activities WHERE (metadata->>'property_id')::bigint = %s ORDER BY id",
              (a["id"],)) == storia_a                                       # A intatto


def test_r2b_secondo_ciclo_stessa_coppia_usa_la_riattivazione(m):
    """Attivazione (lead riusato, aperto/qualified) -> chiusura reale ->
    riattivazione -> errore: si torna allo stato PRIMA DELLA RIATTIVAZIONE
    (chiuso «Non vende più»), non a quello prima della prima attivazione."""
    p, mario = _scena(m, "Mario Cicli")
    vero = _lead_sell(m, mario, stage="qualified")
    assert _attiva(m, p["id"], mario).json()["lead_id"] == vero
    assert _disattiva(m, p["id"], mario, "not_selling", note="ci ripensa").status_code == 200
    chiuso = _lead(m, vero)
    assert _attiva(m, p["id"], mario).json()["reopened"] is True
    prima_att, seconda_att = _attivazioni(m, vero)
    r = _disattiva(m, p["id"], mario, "mistake")
    assert r.status_code == 200, r.text
    assert r.json()["origin"] == "reopened"
    assert _lead(m, vero) == chiuso and chiuso[:3] == ["lost", "closed", "Non vende più: ci ripensa"]
    [ev] = _meta(m, vero, "mistake")
    assert ev["activation_activity_id"] == seconda_att != prima_att
    assert _relazione(m, p["id"], vero) == "seller"                       # esisteva gia' prima della riattivazione
