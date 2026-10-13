"""CESTINO-GENERALE-1 (CG-1) - il Cestino unico in SOLA LETTURA, su
PostgreSQL VERO (schema completo dal runner; database locale usa-e-getta).

Rotte VERE: `trash/router.py` (`/api/trash`, montato su un'app di prova: in
`main.py` non e' ancora montato) e le rotte del Cestino globale in
`platform_admin/router.py` (`/api/platform/trash`).

  01  parita': per ogni ruolo e ogni categoria, l'elenco unificato contiene
      ESATTAMENTE gli elementi dell'elenco esistente di quella categoria
      (stesse regole di visibilita'); le unita' di un edificio nel Cestino
      stanno nella card dell'edificio;
  02  ricerca, filtri, contatori, paginazione; filtri non validi -> 400;
  03  multiagenzia: l'agenzia e' quella della sessione, mai un parametro;
  04  vista globale: solo il Super Admin designato; tutte le agenzie e filtro
      agenzia; un altro platform admin, un account con l'email designata o un
      ruolo revocato -> 403 (rifiuto registrato); un SA senza agenzia sulla
      superficie tenant -> 403 leggibile;
  05  anteprima delle relazioni: conteggi dal catalogo; fuori perimetro 404;
  06  richieste di eliminazione degli edifici dalla superficie tenant;
  07  sola lettura: nessuna riga cambia, e una scrittura dentro le letture
      viene rifiutata dal database.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, _unita, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_postgres import (  # noqa: F401
    _approvato, _codice, _edificio, _richiedi, banco,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


def _ctx(m, chi):
    from operator_auth.context import OperatorContext
    uid, agenzia, ruolo = m["ruoli"][chi]
    return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False, session_id=None,
                           auth_channel="operator_session")


@pytest.fixture
def cestino(banco):
    """Il router del Cestino unico su un'app di prova, con il contesto per
    ruolo; e un Cestino popolato in due agenzie con i percorsi VERI."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from operator_auth.dependencies import require_operator
    from trash.router import router as cestino_router
    from core import contact_lifecycle
    from buy import lifecycle as buy_lifecycle
    from property import lifecycle

    m = banco
    stato = {"chi": "owner_a"}
    app = FastAPI()
    app.include_router(cestino_router)
    app.dependency_overrides[require_operator] = lambda: stato["ctx"] if "ctx" in stato else _ctx(m, stato["chi"])
    client = TestClient(app)

    def api(chi="owner_a", ctx=None):
        class _Come:
            def __getattr__(self, metodo):
                def invia(*a, **k):
                    stato.pop("ctx", None)
                    stato["chi"] = chi
                    if ctx is not None:
                        stato["ctx"] = ctx
                    return getattr(client, metodo)(*a, **k)
                return invia
        return _Come()

    m["tr"] = api
    sfx = uuid.uuid4().hex[:6]
    o = _ctx(m, "owner_a")
    # immobili: uno spostato dal titolare, uno dall'agente (assegnato a lui)
    p1 = _unita(m, floor="1", internal_number=f"T{sfx}")
    lifecycle.trash_property(o, p1["id"], "duplicate", f"doppione {sfx}")
    p2 = _unita(m, "agent_a", floor="2", internal_number=f"U{sfx}")
    _q(m, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (m["ids"]["agent_a"], p2["id"]))
    lifecycle.trash_property(_ctx(m, "agent_a"), p2["id"], "test_record")
    # contatti
    c1 = _q(m, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email) "
               "VALUES (1, %s, 'Gino', 'Cestino', %s) RETURNING id", (f"Gino Cestino {sfx}", f"gino.{sfx}@x.test"))[0][0]
    contact_lifecycle.trash_contact(o, c1, "invalid_data")
    # richiesta acquirente (il contatto resta attivo)
    c2 = _q(m, "INSERT INTO contacts (agency_id, display_name) VALUES (1, %s) RETURNING id", (f"Bruna Acquista {sfx}",))[0][0]
    r1 = _q(m, "INSERT INTO buy_requests (agency_id, contact_id, title) VALUES (1, %s, %s) RETURNING id",
            (c2, f"Trilocale {sfx}"))[0][0]
    buy_lifecycle.trash_request(o, r1, "created_by_mistake")
    # edificio con due unita', approvato dal Super Admin (le unita' stanno nella sua card)
    e = _edificio(m, city="Grottammare", name=f"Palazzina {sfx}")
    u1 = _unita(m, building_id=e["id"], floor="1")
    u2 = _unita(m, building_id=e["id"], floor="2")
    _approvato(m, e["id"])
    # agenzia B
    pb = _unita(m, "owner_b", floor="1", internal_number=f"B{sfx}")
    lifecycle.trash_property(_ctx(m, "owner_b"), pb["id"], "other")
    m["cg"] = {"sfx": sfx, "p1": p1, "p2": p2, "c1": c1, "c2": c2, "r1": r1, "e": e, "u": [u1["id"], u2["id"]],
               "pb": pb}
    yield m


def _ids(pagina, kind=None):
    return {(x["kind"], x["id"]) for x in pagina["items"] if kind is None or x["kind"] == kind}


def _tutti(m, chi="owner_a", **params):
    return _codice(m["tr"](chi).get("/api/trash", params={"limit": 200, **params}), 200)


# ---------------------------------------------------------------------------
# 01 parita' con gli elenchi esistenti
# ---------------------------------------------------------------------------

def test_01_parita_con_gli_elenchi_di_ogni_categoria(cestino):
    m = cestino
    from buy import lifecycle as buy_lifecycle
    from core import contact_lifecycle
    from property import building_deletion as bd
    from property import lifecycle

    for chi in ("owner_a", "agent_a", "agent_a2", "owner_b"):
        ctx = _ctx(m, chi)
        unificato = _tutti(m, chi)
        attesi = {
            "property": {r["id"] for r in lifecycle.list_trash(ctx, limit=200)["items"]},
            "contact": {r["id"] for r in contact_lifecycle.list_trash(ctx, limit=200)["items"]},
            "building": {r["id"] for r in bd.list_trash_groups(ctx, limit=200)["items"]},
            "buy_request": {r["id"] for r in buy_lifecycle.list_trash(ctx, limit=200)["items"]},
        }
        # le unita' di un edificio nel Cestino non sono una voce a parte: stanno nella sua card
        unita_edifici = {r[0] for r in _q(m, "SELECT p.id FROM properties p JOIN buildings b ON b.id = p.building_id "
                                             "WHERE b.deleted_at IS NOT NULL")}
        attesi["property"] -= unita_edifici
        for kind, ids in attesi.items():
            assert {i for k, i in _ids(unificato, kind)} == ids, (chi, kind)
    # il titolare vede tutto cio' che ha preparato la fixture, l'agente solo il suo
    cg = m["cg"]
    tutto = _ids(_tutti(m))
    assert {("property", cg["p1"]["id"]), ("property", cg["p2"]["id"]), ("contact", cg["c1"]),
            ("buy_request", cg["r1"]), ("building", cg["e"]["id"])} <= tutto
    assert not {("property", u) for u in cg["u"]} & tutto
    agente = _ids(_tutti(m, "agent_a"))
    assert ("property", cg["p2"]["id"]) in agente and ("property", cg["p1"]["id"]) not in agente
    # la card dell'edificio porta il numero delle unita'; il contatto della richiesta resta attivo
    voci = {(x["kind"], x["id"]): x for x in _tutti(m)["items"]}
    assert voci[("building", cg["e"]["id"])]["extra"]["units_total"] == 2
    assert voci[("buy_request", cg["r1"])]["extra"]["contact_in_trash"] is False
    assert voci[("contact", cg["c1"])]["href"] == f"#/contatti/{cg['c1']}"
    assert voci[("property", cg["p1"]["id"])]["href"] is None
    assert voci[("property", cg["p1"]["id"])]["deleted_note"] == f"doppione {cg['sfx']}"
    assert voci[("property", cg["p1"]["id"])]["deleted_by_name"] == "Olga"


def test_01b_contatti_lo_scope_core_dell_agente_resta(cestino):
    """Un contatto spostato dall'agente e poi assegnato a un altro (forzato in
    SQL: dall'app la riga nel Cestino e' congelata) non e' piu' nel suo Cestino,
    come in `contact_lifecycle.list_trash`: lo scope CORE vale anche qui."""
    m = cestino
    from core import contact_lifecycle
    sfx = m["cg"]["sfx"]
    c = _q(m, "INSERT INTO contacts (agency_id, display_name, assigned_agent_id) VALUES (1, %s, %s) RETURNING id",
           (f"Assegnato Altrove {sfx}", m["ids"]["agent_a"]))[0][0]
    contact_lifecycle.trash_contact(_ctx(m, "agent_a"), c, "duplicate")
    assert ("contact", c) in _ids(_tutti(m, "agent_a"))
    _q(m, "ALTER TABLE contacts DISABLE TRIGGER trg_contacts_trash_freeze")
    try:
        _q(m, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (m["ids"]["agent_a2"], c))
    finally:
        _q(m, "ALTER TABLE contacts ENABLE TRIGGER trg_contacts_trash_freeze")
    assert c not in {r["id"] for r in contact_lifecycle.list_trash(_ctx(m, "agent_a"), limit=200)["items"]}
    assert ("contact", c) not in _ids(_tutti(m, "agent_a"))
    assert ("contact", c) in _ids(_tutti(m))                                     # il titolare lo vede sempre


# ---------------------------------------------------------------------------
# 02 ricerca, filtri, contatori, paginazione
# ---------------------------------------------------------------------------

def test_02_ricerca_filtri_contatori(cestino):
    m = cestino
    cg = m["cg"]
    sfx = cg["sfx"]
    # ricerca per email del contatto, per nome del contatto della richiesta, per citta' dell'edificio
    assert _ids(_tutti(m, q=f"gino.{sfx}")) == {("contact", cg["c1"])}
    assert _ids(_tutti(m, q=f"bruna acquista {sfx}")) == {("buy_request", cg["r1"])}
    assert ("building", cg["e"]["id"]) in _ids(_tutti(m, q="GROTTAMMARE"))
    assert _ids(_tutti(m, q=f"palazzina {sfx}")) == {("building", cg["e"]["id"])}
    # caratteri jolly trattati come testo
    assert _ids(_tutti(m, q="%")) == set() and _ids(_tutti(m, q="_")) == set()
    # filtri: categoria (anche multipla), motivo, autore, date
    assert {k for k, _ in _ids(_tutti(m, type="contact,buy_request"))} <= {"contact", "buy_request"}
    assert _ids(_tutti(m, reason="test_record")) == {("property", cg["p2"]["id"])}
    assert ("property", cg["p2"]["id"]) in _ids(_tutti(m, deleted_by=m["ids"]["agent_a"]))
    assert ("property", cg["p1"]["id"]) not in _ids(_tutti(m, deleted_by=m["ids"]["agent_a"]))
    oggi = _q(m, "SELECT CURRENT_DATE")[0][0].isoformat()
    assert _ids(_tutti(m, date_from=oggi, date_to=oggi)) == _ids(_tutti(m))
    assert _ids(_tutti(m, date_to="2000-01-01")) == set()
    for errato in ({"type": "stime"}, {"reason": "x"}, {"date_from": "ieri"},
                   {"date_from": "2026-02-01", "date_to": "2026-01-01"}, {"q": "x" * 121}):
        assert m["tr"]().get("/api/trash", params=errato).status_code == 400, errato
    # contatori: coerenti con l'elenco, anche con i filtri
    conti = _codice(m["tr"]().get("/api/trash/counts"), 200)
    per = {c["kind"]: c["count"] for c in conti["categories"]}
    assert conti["total"] == sum(per.values()) == len(_tutti(m)["items"])
    for kind, n in per.items():
        assert n == len(_tutti(m, type=kind)["items"]), kind
    assert all(c["installed"] for c in conti["categories"])
    assert {c["kind"] for c in conti["categories"]} == {"property", "contact", "building", "buy_request"}
    filtrati = _codice(m["tr"]().get("/api/trash/counts", params={"q": f"gino.{sfx}"}), 200)
    assert filtrati["total"] == 1
    # paginazione stabile (deleted_at, categoria, id)
    tutti = _tutti(m)["items"]
    pagine = []
    for offset in range(0, len(tutti), 2):
        pagina = _codice(m["tr"]().get("/api/trash", params={"limit": 2, "offset": offset}), 200)
        pagine += pagina["items"]
    assert [(x["kind"], x["id"]) for x in pagine] == [(x["kind"], x["id"]) for x in tutti]


# ---------------------------------------------------------------------------
# 03 multiagenzia
# ---------------------------------------------------------------------------

def test_03_multiagenzia(cestino):
    m = cestino
    cg = m["cg"]
    a, b = _ids(_tutti(m)), _ids(_tutti(m, "owner_b"))
    assert ("property", cg["pb"]["id"]) in b and ("property", cg["pb"]["id"]) not in a
    assert not {x for x in b if x[0] != "property"} & a
    # un parametro d'agenzia sulla superficie tenant non esiste: viene ignorato
    assert _ids(_tutti(m, agency_id=2)) == a
    assert all(x["agency_id"] == 2 for x in _tutti(m, "owner_b")["items"])


# ---------------------------------------------------------------------------
# 04 vista globale del Super Admin
# ---------------------------------------------------------------------------

def test_04_vista_globale_solo_super_admin_designato(cestino):
    m = cestino
    cg = m["cg"]
    from operator_auth.context import OperatorContext
    sa = m["sa"]
    tutto = _codice(sa.get("/api/platform/trash", params={"limit": 200}), 200)
    ids = _ids(tutto)
    assert ("property", cg["pb"]["id"]) in ids and ("contact", cg["c1"]) in ids and ("building", cg["e"]["id"]) in ids
    assert {x["agency_name"] for x in tutto["items"]} >= {_q(m, "SELECT name FROM agencies WHERE id = 1")[0][0],
                                                           _q(m, "SELECT name FROM agencies WHERE id = 2")[0][0]}
    solo_b = _codice(sa.get("/api/platform/trash", params={"agency": 2, "limit": 200}), 200)
    assert solo_b["items"] and all(x["agency_id"] == 2 for x in solo_b["items"])
    conti = _codice(sa.get("/api/platform/trash/counts"), 200)
    assert conti["total"] == len(tutto["items"])
    assert _codice(sa.get("/api/platform/trash/counts", params={"agency": 2}), 200)["total"] == len(solo_b["items"])
    rel = _codice(sa.get(f"/api/platform/trash/property/{cg['pb']['id']}/relations"), 200)
    assert rel["agency_id"] == 2
    # rifiuti: altro platform admin, email designata senza designazione, ruolo revocato
    altro = _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, is_platform_admin) "
                  "VALUES ('altro.cg@x.test', 'altro.cg@x.test', 'pbkdf2_sha256$1$x$y', TRUE) "
                  "ON CONFLICT (email_normalized) DO UPDATE SET is_platform_admin = TRUE RETURNING id")[0][0]
    info = _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, is_platform_admin) "
                 "VALUES ('info@stima360.it', 'info@stima360.it', 'pbkdf2_sha256$1$x$y', TRUE) "
                 "ON CONFLICT (email_normalized) DO UPDATE SET is_platform_admin = TRUE RETURNING id")[0][0]
    prima = _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.trash.denied'")[0][0]
    for uid in (altro, info):
        m["sa_ctx"]["ctx"] = OperatorContext(user_id=uid, agency_id=None, role=None, is_platform_admin=True,
                                             session_id=None, auth_channel="operator_session")
        for url in ("/api/platform/trash", "/api/platform/trash/counts",
                    f"/api/platform/trash/property/{cg['pb']['id']}/relations"):
            assert _codice(sa.get(url), 403)["code"] == "SUPER_ADMIN_REQUIRED", url
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.trash.denied'")[0][0] == prima + 6
    m["sa_ctx"]["ctx"] = OperatorContext(user_id=m["ids"]["super"], agency_id=None, role=None, is_platform_admin=True,
                                         session_id=None, auth_channel="operator_session")
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (m["ids"]["super"],))
    try:
        assert _codice(sa.get("/api/platform/trash"), 403)["code"] == "SUPER_ADMIN_REQUIRED"
    finally:
        _q(m, "UPDATE operator_users SET is_platform_admin = TRUE WHERE id = %s", (m["ids"]["super"],))
    # account designato NON attivo (status 'disabled'), con il flag ancora vero: negato
    stato = _q(m, "SELECT status FROM operator_users WHERE id = %s", (m["ids"]["super"],))[0][0]
    _q(m, "UPDATE operator_users SET status = 'disabled' WHERE id = %s", (m["ids"]["super"],))
    try:
        for url in ("/api/platform/trash", "/api/platform/trash/counts",
                    f"/api/platform/trash/property/{cg['pb']['id']}/relations"):
            assert _codice(sa.get(url), 403)["code"] == "SUPER_ADMIN_REQUIRED", url
    finally:
        _q(m, "UPDATE operator_users SET status = %s WHERE id = %s", (stato, m["ids"]["super"]))
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (info,))
    assert _codice(sa.get("/api/platform/trash"), 200)["items"]
    # senza configurazione: negato in sicurezza
    import os
    from platform_admin.super_admin import SUPER_ADMIN_ENV
    valore = os.environ.pop(SUPER_ADMIN_ENV)
    try:
        assert _codice(sa.get("/api/platform/trash"), 403)["code"] == "SUPER_ADMIN_NOT_CONFIGURED"
    finally:
        os.environ[SUPER_ADMIN_ENV] = valore
    # il Super Admin senza agenzia sulla superficie tenant: 403 leggibile, nessun dato
    ctx_sa = OperatorContext(user_id=m["ids"]["super"], agency_id=None, role=None, is_platform_admin=True,
                             session_id=None, auth_channel="operator_session")
    assert _codice(m["tr"]("owner_a", ctx=ctx_sa).get("/api/trash"), 403)["code"] == "AGENCY_REQUIRED"
    # i ruoli d'agenzia non raggiungono la vista globale (il servizio rifiuta anche senza l'ammissione Platform)
    from trash import service
    from platform_admin.super_admin import SuperAdminDenied
    for chi in ("owner_a", "agent_a"):
        with pytest.raises(SuperAdminDenied):
            service.list_items(_ctx(m, chi), scope="platform")


# ---------------------------------------------------------------------------
# 05 anteprima delle relazioni
# ---------------------------------------------------------------------------

def test_05_relazioni(cestino):
    m = cestino
    cg = m["cg"]
    # il contatto della richiesta nel Cestino: la richiesta e' una sua relazione (il contatto non e' nel Cestino)
    rel = _codice(m["tr"]().get(f"/api/trash/buy_request/{cg['r1']}/relations"), 200)
    assert rel["kind"] == "buy_request" and rel["id"] == cg["r1"]
    edificio = _codice(m["tr"]().get(f"/api/trash/building/{cg['e']['id']}/relations"), 200)
    assert {(r["table"], r["count"]) for r in edificio["relations"]} >= {("properties", 2)}
    assert next(r for r in edificio["relations"] if r["table"] == "properties")["label"] == "Unità / pertinenze collegate"
    contatto = _codice(m["tr"]().get(f"/api/trash/contact/{cg['c1']}/relations"), 200)
    assert all(r["count"] > 0 for r in contatto["relations"])
    # fuori perimetro: un'altra agenzia, un elemento non nel Cestino, una categoria inesistente
    assert m["tr"]().get(f"/api/trash/property/{cg['pb']['id']}/relations").status_code == 404
    assert m["tr"]().get(f"/api/trash/contact/{cg['c2']}/relations").status_code == 404
    assert m["tr"]("agent_a").get(f"/api/trash/property/{cg['p1']['id']}/relations").status_code == 404
    assert m["tr"]().get(f"/api/trash/stime/{cg['c2']}/relations").status_code == 400


# ---------------------------------------------------------------------------
# 06 richieste di eliminazione degli edifici
# ---------------------------------------------------------------------------

def test_06_richieste_edifici(cestino):
    m = cestino
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"], "agent_a"), 201)
    titolare = _codice(m["tr"]().get("/api/trash/building-requests"), 200)
    assert r["id"] in [x["id"] for x in titolare["items"]]
    assert all(x["status"] == "pending" for x in titolare["items"])
    assert "units_fingerprint" not in titolare["items"][0]
    assert r["id"] in [x["id"] for x in _codice(m["tr"]("agent_a").get("/api/trash/building-requests"), 200)["items"]]
    assert r["id"] not in [x["id"] for x in _codice(m["tr"]("agent_a2").get("/api/trash/building-requests"), 200)["items"]]
    assert r["id"] not in [x["id"] for x in _codice(m["tr"]("owner_b").get("/api/trash/building-requests"), 200)["items"]]
    tutte = _codice(m["tr"]().get("/api/trash/building-requests", params={"status": "all"}), 200)["items"]
    assert {x["status"] for x in tutte} >= {"pending", "approved"}
    assert m["tr"]().get("/api/trash/building-requests", params={"status": "boh"}).status_code == 400
    conti = _codice(m["tr"]().get("/api/trash/counts"), 200)
    assert conti["building_requests_pending"] == sum(1 for x in tutte if x["status"] == "pending")
    # una richiesta in attesa NON e' un elemento eliminato
    assert ("building", e["id"]) not in _ids(_tutti(m))


# ---------------------------------------------------------------------------
# 07 sola lettura
# ---------------------------------------------------------------------------

def _impronta(m):
    tabelle = ("properties", "contacts", "buildings", "buy_requests", "record_lifecycle_events",
               "building_deletion_requests")
    return {t: _q(m, f"SELECT md5(string_agg(to_jsonb(x)::text, '|' ORDER BY x.id)) FROM {t} x")[0][0]
            for t in tabelle}


def test_07_sola_lettura(cestino, monkeypatch):
    m = cestino
    cg = m["cg"]
    prima = _impronta(m)
    for chi in ("owner_a", "agent_a", "owner_b"):
        _tutti(m, chi, q="a")
        m["tr"](chi).get("/api/trash/counts")
        m["tr"](chi).get("/api/trash/building-requests", params={"status": "all"})
        m["tr"](chi).get(f"/api/trash/contact/{cg['c1']}/relations")
    m["sa"].get("/api/platform/trash")
    m["sa"].get("/api/platform/trash/counts")
    assert _impronta(m) == prima
    # una scrittura infilata nella lettura e' rifiutata dal database (transazione READ ONLY)
    from trash import service
    vero = service._installate

    def scrive(cur):
        cur.execute("UPDATE contacts SET notes = 'x' WHERE id = %s", (cg["c2"],))
        return vero(cur)
    monkeypatch.setattr(service, "_installate", scrive)
    import psycopg2
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        service.list_items(_ctx(m, "owner_a"))
    assert _impronta(m) == prima
