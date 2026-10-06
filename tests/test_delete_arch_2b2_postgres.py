"""DELETE-ARCH Fase 2B2 - l'immobile nel Cestino sparisce dal CRM e non riceve
nuovi collegamenti. PostgreSQL VERO (fixture `completo`: tutte le migration,
086 compresa, applicate dal runner) e router VERI.

  A  BLACK-BOX: un immobile vivo, collegato a tutto, e' visibile su ogni
     superficie; dopo il trash non compare piu' su nessuna. In piu' una
     scansione di TUTTE le GET senza parametri dei router montati: una nuova
     superficie che lo mostrasse farebbe fallire il test.
  B  censimento: client_request_id di un immobile nel Cestino riusabile
  C  guardie: nuovi collegamenti rifiutati (API e database) con
     PROPERTY_IN_TRASH; le relazioni esistenti restano
  D  storico comunicazioni: agent 403 HISTORY_REQUIRES_ADMIN, admin passa;
     nessun nuovo messaggio verso un immobile nel Cestino (409)
  E  multi-agenzia: nessun leak
  M  migration 086: guardie presenti, down/up

Prima della Fase 2B2 i test di questo modulo falliscono (fail-before).
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _acquisizione, _catena_vendita, _codice, _collega, _contatto, _futuro, _immobile, _lead,
    _property_lead, _q, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

BASE = "/api/property/properties"


# ---------------------------------------------------------------------------
# banco: piu' router, stessi contesti di `mondo`
# ---------------------------------------------------------------------------

@pytest.fixture
def banco(mondo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acquisitions.router import router as acquisizioni
    from appointments.router import router as agenda
    from buy.router import router as acquirenti
    from core.router import router as core_router
    from crm.router import router as crm_router
    from match.router import router as abbinamenti
    from operator_auth.dependencies import (legacy_basic_agency_context, require_operator,
                                            require_owner_admin_context)
    from owner import dependencies as owner_deps
    from owner.router_admin import router as owner_admin
    from owner.router_admin_lookups import router as owner_lookups
    from owner.router_portal import router as owner_portal
    from property.router import router as immobili
    from proposal.router import router as proposte
    from sale.router import router as vendite

    stato_portale = {"account": None}
    app = FastAPI()
    routers = (immobili, acquisizioni, acquirenti, core_router, crm_router, abbinamenti, proposte, vendite,
               agenda, owner_admin, owner_portal)
    for r in routers:
        app.include_router(r)
    app.include_router(owner_lookups, prefix="/api/owner/admin")
    app.dependency_overrides[legacy_basic_agency_context] = mondo["ctx"]
    app.dependency_overrides[require_operator] = mondo["ctx"]
    app.dependency_overrides[require_owner_admin_context] = mondo["ctx"]
    app.dependency_overrides[owner_deps.current_owner] = lambda: {"owner_account_id": stato_portale["account"]}
    client = TestClient(app, raise_server_exceptions=False)

    def get(path, chi="owner_a", **params):
        mondo["stato"]["chi"] = chi
        return client.get(path, params=params)

    def post(path, json_body=None, chi="owner_a"):
        mondo["stato"]["chi"] = chi
        return client.post(path, json=json_body)

    montati = [("", r) for r in routers] + [("/api/owner/admin", owner_lookups)]
    return {**mondo, "app": app, "get": get, "post": post, "portale": stato_portale, "routers": montati}


def _trash(b, pid, chi="owner_a", reason="other"):
    return b["post"](f"{BASE}/{pid}/trash", {"reason_code": reason}, chi)


def _restore(b, pid, chi="owner_a"):
    return b["post"](f"{BASE}/{pid}/restore", None, chi)


def _riga(b, pid):
    r = _q(b, "SELECT to_jsonb(p) FROM properties p WHERE id = %s", (pid,))
    return r[0][0] if r else None


def _nel_cestino(b, pid):
    _codice(_trash(b, pid), 200)
    assert _riga(b, pid)["deleted_at"] is not None


# ---------------------------------------------------------------------------
# A - BLACK-BOX
# ---------------------------------------------------------------------------

def _scena(b):
    """Un immobile vivo collegato a ogni dominio, con un marcatore unico nel
    titolo, nella citta' e nel codice."""
    marca = f"Cestino2B2{uuid.uuid4().hex[:10]}"
    p = _immobile(b, title=marca)
    pid = p["id"]
    _q(b, "UPDATE properties SET city = %s, code = %s WHERE id = %s", (marca, f"T-{marca}", pid))
    proprietario = _contatto(b, "Piero Proprietario")
    _collega(b, pid, proprietario, "owner", True)
    # documento mancante + visita fra tre giorni -> dashboard, avvisi, visite
    _q(b, "INSERT INTO property_documents (property_id, document_type, title, status) VALUES (%s, 'apl', 'APE', 'missing')", (pid,))
    _q(b, "INSERT INTO property_visits (property_id, contact_id, scheduled_at, status) VALUES (%s, %s, NOW() + INTERVAL '3 days', 'scheduled')",
       (pid, proprietario))
    # acquirente: richiesta + abbinamento + proposta accettata + vendita annullata
    compratore = _contatto(b, "Bruno Compratore")
    catena = _catena_vendita(b, pid, compratore, proposta="accepted", vendita="cancelled")
    _q(b, "UPDATE buy_requests SET status = 'active' WHERE id = %s", (catena["buy_request_id"],))
    # acquisizione chiusa (persa) con il suo appuntamento annullato
    acq = _acquisizione(b, pid, proprietario)
    r = b["api"]().post(f"/api/acquisitions/{acq['id']}/lost",
                        json={"version": acq["version"], "lost_reason": "commission", "cancel_appointment": True})
    assert r.status_code == 200, r.text
    # appuntamento passato ancora aperto (Agenda)
    inizio = _futuro(-2, 9)
    _q(b, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, property_id, contact_id) "
          "VALUES (1, %s, 'seller_meeting', 'scheduled', %s, %s, %s, %s)",
       (b["ids"]["owner_a"], inizio, inizio + timedelta(hours=1), pid, proprietario))
    # Venditori: opportunita' chiusa (non blocca, compare con status=all)
    _property_lead(b, pid, _lead(b, proprietario, pipeline="sell", status="closed"))
    # interazione sull'immobile
    _q(b, "INSERT INTO activities (agency_id, property_id, contact_id, activity_type, description, created_by_user_id) "
          "VALUES (1, %s, %s, 'call', 'Telefonata al proprietario', %s)", (pid, proprietario, b["ids"]["owner_a"]))
    # portale proprietario: conto, accesso, pubblicazione, riscontro
    conto = _q(b, "INSERT INTO owner_accounts (contact_id, status) VALUES (%s, 'active') RETURNING id", (proprietario,))[0][0]
    _q(b, "INSERT INTO owner_property_access (owner_account_id, property_id, access_status) VALUES (%s, %s, 'active')", (conto, pid))
    _q(b, "INSERT INTO owner_publications (property_id, publication_type, title, body, status, published_at) "
          "VALUES (%s, 'general_update', %s, 'Aggiornamento', 'published', NOW())", (pid, f"Pubblicazione {marca}"))
    _q(b, "INSERT INTO owner_feedback (owner_account_id, property_id, feedback_type, subject, message) "
          "VALUES (%s, %s, 'general_message', %s, 'Ciao')", (conto, pid, f"Riscontro {marca}"))
    b["portale"]["account"] = conto
    return {"pid": pid, "marca": marca, "proprietario": proprietario, "compratore": compratore,
            "catena": catena, "acquisizione": acq["id"], "conto": conto}


def _superfici(b, s):
    """Le superfici operative principali: nome -> testo della risposta."""
    pid, br = s["pid"], s["catena"]["buy_request_id"]
    oggi = _futuro(0, 0)
    calendario = {"from": (oggi - timedelta(days=10)).isoformat(), "to": (oggi + timedelta(days=30)).isoformat()}
    chiamate = {
        "property_list": lambda: b["get"](BASE, limit=200),
        "property_list_all": lambda: b["get"](BASE, limit=200, include_archived="true", record_kind="all"),
        "property_search": lambda: b["get"](BASE, search=s["marca"]),
        "property_by_contact": lambda: b["get"](BASE, contact_id=s["proprietario"]),
        "property_dashboard": lambda: b["get"]("/api/property/dashboard"),
        "property_alerts": lambda: b["get"]("/api/property/alerts"),
        "property_visits": lambda: b["get"]("/api/property/visits"),
        "contact_360": lambda: b["get"](f"/api/crm/contacts/{s['proprietario']}/360"),
        "contact_360_buyer": lambda: b["get"](f"/api/crm/contacts/{s['compratore']}/360"),
        "sellers": lambda: b["get"]("/api/crm/sellers", status="all"),
        "buy_matches": lambda: b["get"](f"/api/buy/requests/{br}/matches"),
        "buy_workflow": lambda: b["get"](f"/api/buy/requests/{br}/workflow"),
        "match_list": lambda: b["get"]("/api/match/matches"),
        "match_dashboard": lambda: b["get"]("/api/match/dashboard"),
        "proposals": lambda: b["get"]("/api/proposals"),
        "sales": lambda: b["get"]("/api/sales"),
        "acquisitions": lambda: b["get"]("/api/acquisitions", statuses="lost"),
        "acquisition_detail": lambda: b["get"](f"/api/acquisitions/{s['acquisizione']}"),
        "agenda_calendar": lambda: b["get"]("/api/appointments/calendar", **calendario),
        "owner_admin_access": lambda: b["get"]("/api/owner/admin/access"),
        "owner_admin_publications": lambda: b["get"]("/api/owner/admin/publications"),
        "owner_admin_feedback": lambda: b["get"]("/api/owner/admin/feedback"),
        "owner_lookup_properties": lambda: b["get"](f"/api/owner/admin/lookups/accounts/{s['conto']}/properties"),
        "owner_portal_properties": lambda: b["get"]("/api/owner/portal/properties"),
        "owner_portal_property": lambda: b["get"](f"/api/owner/portal/properties/{pid}"),
    }
    return {nome: f() for nome, f in chiamate.items()}


def _mostra(risposta, s) -> bool:
    if risposta.status_code != 200:
        return False
    testo = risposta.text
    return s["marca"] in testo or f'"property_id":{s["pid"]},' in testo.replace(" ", "")


def _scansione_get(b):
    """Tutte le GET senza parametri di percorso dei router montati."""
    from fastapi.routing import APIRoute
    esiti = {}
    for prefisso, router in b["routers"]:
        for route in router.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods or "{" in route.path:
                continue
            percorso = prefisso + route.path
            if percorso.endswith(("/download", "/health")):
                continue
            r = b["get"](percorso)
            if r.status_code == 200:
                esiti[percorso] = r.text
    assert len(esiti) >= 20, sorted(esiti)          # la scansione vede davvero le superfici
    return esiti


def test_a_black_box_sparisce_da_ogni_superficie(banco):
    b = banco
    s = _scena(b)
    prima = _superfici(b, s)
    invisibili = sorted(n for n, r in prima.items() if not _mostra(r, s))
    assert invisibili == [], f"superfici che non mostrano l'immobile VIVO (fixture debole): {invisibili}"
    scansione_prima = _scansione_get(b)
    assert any(s["marca"] in t for t in scansione_prima.values())

    _nel_cestino(b, s["pid"])

    dopo = _superfici(b, s)
    visibili = sorted(n for n, r in dopo.items() if _mostra(r, s))
    assert visibili == [], f"l'immobile nel Cestino compare ancora in: {visibili}"
    rotte = sorted(p for p, t in _scansione_get(b).items() if s["marca"] in t)
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B3: la pagina «Cestino»
    # (GET /api/property/trash) e' l'UNICA superficie che deve mostrare
    # l'immobile nel Cestino; ogni altra GET resta senza.
    assert rotte == ["/api/property/trash"], f"GET che mostrano ancora l'immobile nel Cestino: {rotte}"
    # dettagli per id: 404
    assert dopo["owner_portal_property"].status_code == 404
    assert dopo["acquisition_detail"].status_code == 404
    assert b["get"](f"{BASE}/{s['pid']}").status_code == 404
    assert b["get"](f"{BASE}/{s['pid']}/census").status_code == 404
    assert b["get"](f"{BASE}/{s['pid']}/interactions").status_code == 404
    # l'Agenda tiene l'appuntamento (storia del contatto) ma non l'immobile
    cal = dopo["agenda_calendar"].json()
    righe = cal["items"] if isinstance(cal, dict) else cal
    assert righe and all(r.get("property_id") != s["pid"] for r in righe)

    # ...e torna su tutte con il restore
    _codice(_restore(b, s["pid"]), 200)
    ancora = _superfici(b, s)
    assert sorted(n for n, r in ancora.items() if not _mostra(r, s)) == []


def test_a2_superfici_non_http(banco):
    """Motori interni: candidati FLOW (documenti mancanti), offerta interna
    della zona (property_watch)."""
    b = banco
    s = _scena(b)
    from core.database import core_cursor
    from flow import adapters
    from property_watch import repository as watch

    _q(b, "UPDATE properties SET microzone = %s WHERE id = %s", (s["marca"], s["pid"]))
    _q(b, "UPDATE properties SET commercial_status = 'active' WHERE id = %s", (s["pid"],))

    def candidati():
        return {i for _, i in adapters.scan_candidates_for_agency(1, "FLOW-R003", {}, 500)}

    def offerta():
        with core_cursor() as (_, cur):
            return watch.count_internal_supply_for_agency(cur, s["marca"], s["marca"], 1)

    assert s["pid"] in candidati() and offerta() == 1
    _nel_cestino(b, s["pid"])
    assert s["pid"] not in candidati() and offerta() == 0


# ---------------------------------------------------------------------------
# B - censimento: client_request_id riusabile, edifici e unita'
# ---------------------------------------------------------------------------

def _unita_censimento(b, chiave, **extra):
    corpo = {"property_type": "apartment", "city": "Fermo", "address": "Via Censita",
             "civic_number": "9", "client_request_id": chiave, **extra}
    return b["post"]("/api/property/census/units", corpo)


def test_b_censimento_client_request_riusabile_dopo_il_cestino(banco):
    b = banco
    chiave = str(uuid.uuid4())
    a = _codice(_unita_censimento(b, chiave), 201)
    assert a["replica"] is False
    replica = _unita_censimento(b, chiave)
    assert replica.status_code in (200, 201) and replica.json()["id"] == a["id"] and replica.json()["replica"] is True
    _nel_cestino(b, a["id"])
    # A nel Cestino: la stessa chiave crea B; mai A come replica, mai 409
    r = _unita_censimento(b, chiave)
    assert r.status_code == 201, r.text
    nuova = r.json()
    assert nuova["id"] != a["id"] and nuova["replica"] is False
    assert _q(b, "SELECT count(*) FROM properties WHERE client_request_id = %s", (chiave,))[0][0] == 2
    # un retry di B e' replica di B
    assert _unita_censimento(b, chiave).json()["id"] == nuova["id"]
    # A non si ripristina sopra B (2B1, RESTORE_CONFLICT)
    _codice(_restore(b, a["id"]), 409, "RESTORE_CONFLICT")


def test_b2_edificio_e_unita_senza_l_immobile_nel_cestino(banco):
    b = banco
    edificio = _codice(b["post"]("/api/property/buildings", {
        "city": "Fermo", "address": "Via Cestino", "civic_number": "2", "units_declared": 4,
        "units_declared_source": "survey", "name": "Palazzina Cestino"}), 201)
    marca = f"Unita2B2{uuid.uuid4().hex[:8]}"
    u = _codice(b["post"]("/api/property/census/units", {"building_id": edificio["id"], "floor": "1",
                                                         "internal_number": marca[-4:]}), 201)
    _q(b, "UPDATE properties SET title = %s WHERE id = %s", (marca, u["id"]))
    prima = b["get"](f"/api/property/buildings/{edificio['id']}")
    assert prima.status_code == 200 and marca in prima.text
    _nel_cestino(b, u["id"])
    dopo = b["get"](f"/api/property/buildings/{edificio['id']}")
    assert dopo.status_code == 200 and marca not in dopo.text
    elenco = b["get"]("/api/property/buildings").json()["items"]
    assert [e for e in elenco if e["id"] == edificio["id"]][0]["units_census"] == 0
    assert b["get"](f"{BASE}/{u['id']}/census").status_code == 404


# ---------------------------------------------------------------------------
# C - guardie: nessun nuovo collegamento
# ---------------------------------------------------------------------------

def _conflitto_trash(r):
    """409 nella forma standard `{"detail": "...", "code": "PROPERTY_IN_TRASH"}`
    (R4: stessa forma da ogni router che rifiuta il collegamento)."""
    assert r.status_code == 409, (r.status_code, r.text)
    corpo = r.json()
    assert corpo.get("code") == "PROPERTY_IN_TRASH", corpo
    assert isinstance(corpo.get("detail"), str) and corpo["detail"], corpo


def test_c_guardie_api(banco):
    b = banco
    p = _immobile(b)
    pid = p["id"]
    proprietario = _contatto(b, "Olga Proprietaria")
    _collega(b, pid, proprietario, "owner", True)
    altro = _contatto(b, "Nuovo Contatto")
    _nel_cestino(b, pid)
    prima = _q(b, "SELECT (SELECT count(*) FROM property_contacts WHERE property_id=%(p)s), "
                  "(SELECT count(*) FROM activities WHERE property_id=%(p)s)", {"p": pid})[0]
    # nuovo proprietario / collegamento lead
    _conflitto_trash(b["post"](f"{BASE}/{pid}/contacts", {"contact_id": altro, "role": "owner"}))
    _conflitto_trash(b["post"](f"{BASE}/{pid}/leads", {"lead_id": _lead(b, altro), "relation_type": "related"}))
    # foto, documento, visita
    _conflitto_trash(b["post"](f"{BASE}/{pid}/photos", {"url": "https://x.test/a.jpg"}))
    _conflitto_trash(b["post"](f"{BASE}/{pid}/documents", {"document_type": "apl", "title": "APE", "status": "missing"}))
    # interazione (activity)
    _conflitto_trash(b["post"](f"{BASE}/{pid}/interactions", {"interaction_type": "call", "note": "x"}))
    # appuntamento
    inizio = _futuro(9, 11)
    _conflitto_trash(b["post"]("/api/appointments", {
        "appointment_type": "seller_meeting", "assigned_user_id": b["ids"]["owner_a"], "property_id": pid,
        "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(hours=1)).isoformat(),
        "client_request_id": str(uuid.uuid4())}))
    # acquisizione
    _conflitto_trash(b["post"]("/api/acquisitions", {
        "property_id": pid, "owner_contact_id": proprietario,
        "appointment": {"start_at": _futuro(12).isoformat(), "assigned_user_id": b["ids"]["owner_a"],
                        "client_request_id": str(uuid.uuid4())}}))
    # «Vende»
    _conflitto_trash(b["post"]("/api/crm/sellers", {"contact_id": proprietario, "property_id": pid, "new_lead": True}))
    # nulla e' stato scritto, le relazioni esistenti restano
    assert _q(b, "SELECT (SELECT count(*) FROM property_contacts WHERE property_id=%(p)s), "
                 "(SELECT count(*) FROM activities WHERE property_id=%(p)s)", {"p": pid})[0] == prima


def test_c6_forma_errore_su_ogni_router(banco):
    """R4: i router che traducevano `ConflictError` con il solo `detail`
    (acquirenti, vendite, portale admin, ponte stima-immobile) restituiscono il
    nuovo errore 2B2 con `code`; un errore legacy resta com'era."""
    b = banco
    from acquisition.router import router as ponte
    b["app"].include_router(ponte)
    pid = _immobile(b)["id"]
    cliente = _contatto(b, "Ugo Forma")
    br = _q(b, "INSERT INTO buy_requests (agency_id, contact_id, title, status) VALUES (1, %s, 'Ricerca R4', 'active') RETURNING id", (cliente,))[0][0]
    conto = _q(b, "INSERT INTO owner_accounts (contact_id, status) VALUES (%s, 'active') RETURNING id", (cliente,))[0][0]
    stima = _q(b, "INSERT INTO stime (agency_id, nome, cognome, email, telefono, comune) "
                  "VALUES (1, 'Ugo', 'Forma', 'r4@x.test', '333', 'Giulianova') RETURNING id")[0][0]
    collegamento = _q(b, "INSERT INTO stima_acquisitions (stima_id, stima_id_snapshot, property_id, linked_by_operator_user_id) "
                         "VALUES (%s, %s, %s, %s) RETURNING id", (stima, stima, pid, b["ids"]["owner_a"]))[0][0]
    venduto = _immobile(b)["id"]
    _collega(b, venduto, _contatto(b, "Vera Venditrice"), "owner", True)
    accettata = _catena_vendita(b, venduto, cliente, proposta="accepted")
    try:
        _nel_cestino(b, venduto)
        _conflitto_trash(b["post"]("/api/sales", {"proposal_id": accettata["proposal_id"],
                                                  "idempotency_key": str(uuid.uuid4())}))
        assert _q(b, "SELECT count(*) FROM property_sales WHERE property_id = %s", (venduto,))[0][0] == 0
        _nel_cestino(b, pid)
        _conflitto_trash(b["post"](f"/api/buy/requests/{br}/interactions",
                                   {"property_id": pid, "interaction_type": "proposed", "notes": "x"}))
        _conflitto_trash(b["post"]("/api/owner/admin/access", {"owner_account_id": conto, "property_id": pid}))
        _conflitto_trash(b["post"](f"/api/acquisition/stime/{stima}/links", {"property_id": pid}))
        # R2: l'incarico non si registra su un collegamento verso un immobile nel Cestino
        _conflitto_trash(b["post"](f"/api/acquisition/links/{collegamento}/mandate",
                                   {"mandate_signed_at": _futuro(-1).isoformat()}))
        assert _q(b, "SELECT mandate_signed_at FROM stima_acquisitions WHERE id = %s", (collegamento,))[0][0] is None
        # l'esclusione di un abbinamento non vede l'immobile: 404, nessuna riga
        assert b["post"]("/api/match/exclusions", {"buy_request_id": br, "property_id": pid}).status_code == 404
        assert _q(b, "SELECT count(*) FROM match_exclusions WHERE property_id = %s", (pid,))[0][0] == 0
        _codice(_restore(b, pid), 200)
        _codice(b["post"](f"/api/acquisition/links/{collegamento}/mandate",
                          {"mandate_signed_at": _futuro(-1).isoformat()}), 200)
        # un ConflictError legacy (non 2B2) resta com'era: solo `detail`
        gia = b["post"](f"/api/acquisition/links/{collegamento}/mandate", {"mandate_signed_at": _futuro(-1).isoformat()})
        assert gia.status_code == 409 and set(gia.json()) == {"detail"}, gia.text
    finally:
        _q(b, "DELETE FROM stima_acquisitions WHERE stima_id = %s", (stima,))


def test_r3_storico_del_contatto_resta_intatto(banco):
    """R3 (comportamento INTENZIONALE): le attivita' con property_id restano
    nello storico del contatto; l'immobile non torna come immobile operativo,
    il dettaglio resta 404, nessuna attivita' cancellata o riscritta."""
    b = banco
    marca = f"Storico{uuid.uuid4().hex[:8]}"
    pid = _immobile(b, title=marca)["id"]
    cid = _contatto(b, "Teo Storico")
    _collega(b, pid, cid, "owner", True)
    att = _q(b, "INSERT INTO activities (agency_id, property_id, contact_id, activity_type, description, created_by_user_id) "
                "VALUES (1, %s, %s, 'call', 'Telefonata storica', %s) RETURNING id", (pid, cid, b["ids"]["owner_a"]))[0][0]
    prima = _q(b, "SELECT to_jsonb(a) FROM activities a WHERE a.property_id = %s ORDER BY a.id", (pid,))
    _nel_cestino(b, pid)
    # lo storico del contatto conserva l'attivita', con il suo property_id
    storia = _codice(b["get"]("/api/core/activities", contact_id=cid), 200)["items"]
    assert [(a["id"], a["property_id"]) for a in storia if a["id"] == att] == [(att, pid)]
    vista = _codice(b["get"](f"/api/crm/contacts/{cid}/360"), 200)
    assert att in {a["id"] for a in vista["activities"]}
    # ... ma l'immobile non e' un immobile operativo del contatto
    assert pid not in {x.get("property_id", x.get("id")) for x in vista["properties"]}
    assert marca not in b["get"](BASE, contact_id=cid).text
    assert b["get"](f"{BASE}/{pid}").status_code == 404
    # nessuna attivita' cancellata o riscritta (nemmeno dal restore)
    assert _q(b, "SELECT to_jsonb(a) FROM activities a WHERE a.property_id = %s ORDER BY a.id", (pid,)) == prima
    _codice(_restore(b, pid), 200)
    assert _q(b, "SELECT to_jsonb(a) FROM activities a WHERE a.property_id = %s ORDER BY a.id", (pid,)) == prima


def test_c7_pertinenza_viva_di_un_genitore_nel_cestino(banco):
    """REVIEW 1 (R2, census.detach_on_close): la pertinenza VIVA di un
    immobile nel Cestino resta un immobile operativo. Venderla o archiviarla
    la scollega come sempre; lo storico si scrive sulla pertinenza, non sul
    genitore nel Cestino (congelato: nessuna attivita' nuova)."""
    b = banco
    genitore = _immobile(b)["id"]
    figlie = [_immobile(b)["id"] for _ in range(2)]
    for f in figlie:
        _codice(b["post"](f"{BASE}/{genitore}/pertinenze/link", {"pertinenza_id": f}), 200)
    _nel_cestino(b, genitore)
    storico = _q(b, "SELECT count(*) FROM activities WHERE property_id = %s", (genitore,))[0][0]
    venduta = b["api"]().patch(f"{BASE}/{figlie[0]}", json={"commercial_status": "sold"})
    assert venduta.status_code == 200, venduta.text
    _codice(b["post"](f"{BASE}/{figlie[1]}/archive"), 200)
    for f in figlie:
        assert _riga(b, f)["parent_property_id"] is None
        assert _q(b, "SELECT count(*) FROM activities WHERE property_id = %s AND activity_type = 'system' "
                     "AND description LIKE 'Scollegata da %%'", (f,))[0][0] == 1
    assert _q(b, "SELECT count(*) FROM activities WHERE property_id = %s", (genitore,))[0][0] == storico
    assert _riga(b, genitore)["deleted_at"] is not None


def test_c2_guardie_proposta_e_vendita(banco):
    b = banco
    p = _immobile(b)
    pid = p["id"]
    compratore = _contatto(b, "Carla Compratrice")
    catena = _catena_vendita(b, pid, compratore, proposta="rejected")
    _nel_cestino(b, pid)
    # proposta nuova sullo stesso abbinamento
    r = b["post"]("/api/proposals", {"match_id": catena["match_id"], "amount": 120000,
                                      "expires_at": _futuro(20).isoformat(), "idempotency_key": str(uuid.uuid4())})
    _conflitto_trash(r)
    # nel database: nessuna proposta e nessuna vendita, qualunque percorso
    with pytest.raises(Exception, match="PROPERTY_IN_TRASH"):
        _q(b, "INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, created_by, status) "
              "VALUES (%s, 1, NOW() + INTERVAL '5 days', %s, 'test', 'draft')", (catena["match_id"], str(uuid.uuid4())))
    with pytest.raises(Exception, match="PROPERTY_IN_TRASH"):
        _q(b, "INSERT INTO property_sales (property_id, buy_request_id, proposal_id, sale_price, idempotency_key, created_by, status) "
              "VALUES (%s, %s, %s, 1, %s, 'test', 'pending')", (pid, catena["buy_request_id"], catena["proposal_id"], str(uuid.uuid4())))


TABELLE_DIRETTE = [
    ("property_contacts", "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%(p)s, %(c)s, 'owner')"),
    ("property_leads", "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%(p)s, %(l)s, 'related')"),
    ("property_photos", "INSERT INTO property_photos (property_id, url) VALUES (%(p)s, 'https://x.test/f.jpg')"),
    ("property_documents", "INSERT INTO property_documents (property_id, document_type, title, status) VALUES (%(p)s, 'apl', 'D', 'missing')"),
    ("property_visits", "INSERT INTO property_visits (property_id, scheduled_at, status) VALUES (%(p)s, NOW(), 'completed')"),
    ("property_accessories", "INSERT INTO property_accessories (property_id, kind) VALUES (%(p)s, 'cantina')"),
    ("activities", "INSERT INTO activities (agency_id, property_id, activity_type, description) VALUES (1, %(p)s, 'note', 'x')"),
    ("matches", "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, algorithm_version) "
                "VALUES (%(b)s, %(p)s, 'compatible', 50, 'good', 'test')"),
    ("match_exclusions", "INSERT INTO match_exclusions (buy_request_id, property_id, reason, created_by) VALUES (%(b)s, %(p)s, 'x', 'test')"),
    ("owner_property_access", "INSERT INTO owner_property_access (owner_account_id, property_id) VALUES (%(o)s, %(p)s)"),
    ("owner_publications", "INSERT INTO owner_publications (property_id, publication_type, title, body) VALUES (%(p)s, 'general_update', 'T', 'B')"),
    ("owner_feedback", "INSERT INTO owner_feedback (owner_account_id, property_id, feedback_type, subject, message) "
                       "VALUES (%(o)s, %(p)s, 'general_message', 'S', 'M')"),
]


@pytest.mark.parametrize("tabella,sql", TABELLE_DIRETTE, ids=[t for t, _ in TABELLE_DIRETTE])
def test_c3_guardie_database(banco, tabella, sql):
    b = banco
    pid = _immobile(b)["id"]
    cid = _contatto(b, "Dora Diretta")
    valori = {"p": pid, "c": cid, "l": _lead(b, cid),
              "b": _q(b, "INSERT INTO buy_requests (agency_id, contact_id, title, status) VALUES (1, %s, 'R', 'active') RETURNING id", (cid,))[0][0],
              "o": _q(b, "INSERT INTO owner_accounts (contact_id) VALUES (%s) RETURNING id", (cid,))[0][0]}
    _nel_cestino(b, pid)
    with pytest.raises(Exception) as e:
        _q(b, sql, valori)
    assert "PROPERTY_IN_TRASH" in str(e.value), str(e.value)
    # dopo il restore lo stesso collegamento e' accettato
    _codice(_restore(b, pid), 200)
    _q(b, sql, valori)


def test_c4_esistenti_intatti_e_congelamento(banco):
    b = banco
    pid = _immobile(b)["id"]
    cid = _contatto(b, "Elio Esistente")
    _collega(b, pid, cid, "owner", True)
    aid = _q(b, "INSERT INTO activities (agency_id, property_id, contact_id, activity_type, description) "
                "VALUES (1, %s, %s, 'note', 'prima') RETURNING id", (pid, cid))[0][0]
    figlia = _immobile(b)["id"]
    _nel_cestino(b, pid)
    # una relazione esistente si aggiorna come prima (es. «per errore» su un'attivita')
    _q(b, "UPDATE activities SET metadata = metadata || '{\"x\": 1}'::jsonb WHERE id = %s", (aid,))
    # la riga dell'immobile e' congelata
    with pytest.raises(Exception, match="PROPERTY_IN_TRASH"):
        _q(b, "UPDATE properties SET title = 'Riscritto' WHERE id = %s", (pid,))
    # nessuna pertinenza nuova verso un genitore nel Cestino
    with pytest.raises(Exception, match="PROPERTY_IN_TRASH"):
        _q(b, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (pid, figlia))
    # la FK SET NULL dell'autore resta possibile (operatore eliminato)
    _q(b, "UPDATE properties SET deleted_by_user_id = NULL WHERE id = %s", (pid,))
    # trash/restore restano le sole transizioni
    _codice(_restore(b, pid), 200)
    _q(b, "UPDATE properties SET title = 'Di nuovo vivo' WHERE id = %s", (pid,))


def test_c5_figli_esistenti_congelati_dalle_api_immobile(banco):
    b = banco
    pid = _immobile(b)["id"]
    foto = b["post"](f"{BASE}/{pid}/photos", {"url": "https://x.test/1.jpg"})
    assert foto.status_code == 201, foto.text
    fid = foto.json()["id"]
    cid = _contatto(b, "Fabio Figlio")
    _collega(b, pid, cid, "owner", True)
    _nel_cestino(b, pid)
    mondo_api = b["api"]()
    r = mondo_api.delete(f"/api/property/photos/{fid}")
    assert r.status_code == 409 and "PROPERTY_IN_TRASH" in r.text, (r.status_code, r.text)
    r = mondo_api.delete(f"{BASE}/{pid}/contacts/{cid}/owner")
    assert r.status_code == 409, (r.status_code, r.text)
    assert _q(b, "SELECT count(*) FROM property_photos WHERE id = %s", (fid,))[0][0] == 1


# ---------------------------------------------------------------------------
# D - storico comunicazioni
# ---------------------------------------------------------------------------

def _messaggio(b, pid, cid, status="sent"):
    _q(b, "INSERT INTO communication_messages (agency_id, contact_id, property_id, channel, direction, communication_type, "
          "mode, reason_code, rendered_body, subject_snapshot, destination_snapshot, actor_type, idempotency_key, status) "
          "VALUES (1, %s, %s, 'email', 'outbound', 'service', 'manual', 'operator_manual', 'Corpo', 'Oggetto', 'x@x.test', 'system', %s, %s)",
       (cid, pid, str(uuid.uuid4()), status))


def test_d_storico_comunicazioni_agent_403_admin_passa(banco):
    b = banco
    p = _immobile(b, "agent_a")
    pid = p["id"]
    cid = _contatto(b, "Gino Messaggio")
    _messaggio(b, pid, cid)
    corpo = _codice(_trash(b, pid, "agent_a"), 403, "HISTORY_REQUIRES_ADMIN")
    assert "COMMUNICATION_HISTORY" in {h["code"] for h in corpo["history"]}
    controllo = _codice(b["get"](f"{BASE}/{pid}/deletion-check", "agent_a"), 200)
    assert controllo["can_trash"] is False
    assert _riga(b, pid)["deleted_at"] is None
    corpo_prima = _q(b, "SELECT rendered_body, status FROM communication_messages WHERE property_id = %s", (pid,))
    _codice(_trash(b, pid, "admin_a"), 200)
    assert _q(b, "SELECT rendered_body, status FROM communication_messages WHERE property_id = %s", (pid,)) == corpo_prima


def test_d2_messaggi_annullati_non_sono_storico(banco):
    b = banco
    pid = _immobile(b, "agent_a")["id"]
    cid = _contatto(b, "Ivo Annullato")
    _messaggio(b, pid, cid, status="cancelled")
    _messaggio(b, pid, cid, status="suppressed")
    _codice(_trash(b, pid, "agent_a"), 200)


def _accoda(b, pid, cid, cur, chiave=None):
    """Un messaggio accodato dall'UNICO punto di scrittura del ledger
    (`communication.service.enqueue` -> `repository.insert_message`)."""
    from communication import service
    b["stato"]["chi"] = "owner_a"
    return service.enqueue(
        b["ctx"](), contact_id=cid, channel="email", communication_type="service",
        mode="manual", reason_code="operator_manual", rendered_body="Corpo",
        subject_snapshot="Oggetto", destination_snapshot="x@x.test",
        idempotency_key=chiave or str(uuid.uuid4()), property_id=pid, cur=cur)


def _messaggi_di(b, pid):
    return _q(b, "SELECT count(*) FROM communication_messages WHERE property_id = %s", (pid,))[0][0]


def test_d3_nessun_nuovo_messaggio_su_immobile_nel_cestino(banco):
    """R1: viva -> messaggio creato; nel Cestino -> 409 PROPERTY_IN_TRASH,
    nessuna riga e nessuna scrittura parziale; dopo il restore di nuovo ok."""
    from psycopg2.extras import RealDictCursor

    from core.exceptions import ConflictError, PropertyInTrash
    b = banco
    pid = _immobile(b)["id"]
    cid = _contatto(b, "Rita Ledger")
    with b["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        assert _accoda(b, pid, cid, cur)["created"] is True                 # viva: consentito
    assert _messaggi_di(b, pid) == 1
    _nel_cestino(b, pid)
    totale = _q(b, "SELECT count(*), coalesce(max(id), 0) FROM communication_messages")[0]
    with b["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("BEGIN")
        try:
            with pytest.raises(PropertyInTrash) as preso:
                _accoda(b, pid, cid, cur)
        finally:
            cur.execute("ROLLBACK")
    assert isinstance(preso.value, ConflictError) and preso.value.code == "PROPERTY_IN_TRASH"
    assert _q(b, "SELECT count(*), coalesce(max(id), 0) FROM communication_messages")[0] == totale
    assert _messaggi_di(b, pid) == 1
    # anche senza transazione esterna (autocommit): nulla scritto
    with b["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        with pytest.raises(PropertyInTrash):
            _accoda(b, pid, cid, cur)
    assert _q(b, "SELECT count(*), coalesce(max(id), 0) FROM communication_messages")[0] == totale
    # un messaggio senza immobile allo stesso contatto resta consentito
    with b["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        assert _accoda(b, None, cid, cur)["created"] is True
    _codice(_restore(b, pid), 200)
    with b["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        assert _accoda(b, pid, cid, cur)["created"] is True
    assert _messaggi_di(b, pid) == 2


def test_d4_trash_concorrente_attende_l_accodamento(banco):
    """R1: la verifica tiene la riga dell'immobile (FOR SHARE) fino al commit:
    un trash concorrente non puo' passare fra la verifica e la INSERT."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    b = banco
    pid = _immobile(b)["id"]
    cid = _contatto(b, "Sara Concorrente")
    from core import database as core_database
    altro = core_database.get_connection()      # gia' puntata al database di prova da `completo`
    try:
        with b["conn"].cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("BEGIN")
            try:
                _accoda(b, pid, cid, cur)
                with altro.cursor() as c2:
                    c2.execute("SET lock_timeout = '200ms'")
                    with pytest.raises(psycopg2.errors.LockNotAvailable):
                        c2.execute("UPDATE properties SET deleted_at = now(), deleted_reason = 'other' WHERE id = %s", (pid,))
            finally:
                altro.rollback()
                cur.execute("COMMIT")
    finally:
        altro.close()
    assert _riga(b, pid)["deleted_at"] is None and _messaggi_di(b, pid) == 1


# ---------------------------------------------------------------------------
# E - multi-agenzia
# ---------------------------------------------------------------------------

def test_e_multi_agenzia(banco):
    b = banco
    s = _scena(b)
    _nel_cestino(b, s["pid"])
    # l'altra agenzia non vede ne' l'immobile ne' il suo Cestino
    for path in (f"{BASE}/{s['pid']}", f"{BASE}/{s['pid']}/deletion-check"):
        r = b["get"](path, "owner_b")
        assert r.status_code == 404 and s["marca"] not in r.text, (path, r.status_code)
    assert s["marca"] not in b["get"](BASE, "owner_b", limit=200).text
    assert s["marca"] not in b["get"]("/api/property/dashboard", "owner_b").text
    # e non puo' collegarsi a un immobile di un'altra agenzia (404 prima del Cestino)
    r = b["post"](f"{BASE}/{s['pid']}/photos", {"url": "https://x.test/z.jpg"}, "owner_b")
    assert r.status_code == 404 and s["marca"] not in r.text


# ---------------------------------------------------------------------------
# M - migration 086
# ---------------------------------------------------------------------------

def test_m_086_guardie_presenti_e_down_up(banco):
    from tests.test_censimento_3_backend_postgres import MIGRAZIONI
    b = banco
    attese = {"property_contacts", "property_leads", "property_documents", "property_photos", "property_visits",
              "property_accessories", "activities", "appointments", "acquisitions", "stima_acquisitions", "matches",
              "match_exclusions", "property_sales", "buy_request_interactions", "owner_property_access",
              "owner_publications", "owner_feedback", "owner_notifications", "property_proposals",
              "owner_shared_documents", "owner_visit_feedback_publications"}
    presenti = {r[0] for r in _q(b, "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                    "WHERE t.tgname LIKE 'trg_%%_property_trash_guard'")}
    assert presenti == attese
    assert _q(b, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_properties_trash_freeze'")[0][0] == 1
    giu = (MIGRAZIONI / "086_delete_arch_2b2_property_trash_guards_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / "086_delete_arch_2b2_property_trash_guards.sql").read_text(encoding="utf-8")
    pid = _immobile(b)["id"]
    _nel_cestino(b, pid)
    _q(b, giu)
    assert _q(b, "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%%property_trash_guard' OR tgname = 'trg_properties_trash_freeze'")[0][0] == 0
    assert _riga(b, pid)["deleted_at"] is not None          # la down non tocca i dati
    with b["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(su)
        cur.execute("COMMIT")
    assert {r[0] for r in _q(b, "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                "WHERE t.tgname LIKE 'trg_%%_property_trash_guard'")} == attese
    _codice(_restore(b, pid), 200)
