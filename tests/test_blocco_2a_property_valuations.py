"""BLOCCO-2A - valutazioni dell'agente su un immobile (vendita e locazione).

Offline. `property_valuations.service.core_cursor` e' sostituito da un database
in memoria che interpreta le statement del repository REALE, rispetta le
transazioni (un errore annulla tutto) e riproduce i vincoli della migrazione
116 che contano qui: una sola bozza e una sola approvata per immobile,
versione unica per immobile, immobile della stessa agenzia. Le route sono
quelle vere, con il contesto dell'operatore al posto della sessione.

Coverage map:

    A  creazione senza incarico; autore dal server; prezzo dell'immobile invariato
    B  vendita sola, locazione sola, entrambe; "Non valutata" mai zero
    C  bozza incompleta salvabile; approvazione rifiutata se incompleta
    D  permessi e isolamento tra agenzie
    E  versioni: immutabilita', nuova bozza che non supera l'approvata
    F  concorrenza: doppie approvazioni, versione attesa, corsa simulata
    G  importi, intervalli, superfici, comparabili, date, URL non validi
    H  migrazione 116 assente: 503; migrazione valida per il runner
    I  interfaccia: scheda «Valutazioni», testo sicuro, avviso sul metodo
"""
from __future__ import annotations

import copy
import itertools
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg2 import errors

from operator_auth.context import OperatorContext
from property_valuations import router as valuations_router
from property_valuations import pdf_service, service
from property_valuations.schemas import ValuationContent, approval_issues

ROOT = Path(__file__).resolve().parents[1]

AGENCY_A, AGENCY_B = 71, 72
OWNER_ID, ADMIN_ID, AGENT_ID, OTHER_AGENT_ID = 501, 502, 503, 504
P_ASSIGNED, P_UNASSIGNED, P_TRASH, P_OTHER_AGENCY = 10, 11, 12, 20


# ---------------------------------------------------------------------------
# database in memoria
# ---------------------------------------------------------------------------

def _properties():
    base = {"code": None, "title": "Trilocale", "property_type": "apartment", "record_kind": "crm",
            "commercial_status": "evaluation", "address": "Via Prova", "civic_number": "1", "city": "Alba Adriatica",
            "surface_sqm": Decimal("80.00"), "asking_price": Decimal("199000.00"), "mandate_type": None,
            "mandate_start": None, "mandate_end": None, "deleted_at": None, "acquisition_id": None}
    return {
        P_ASSIGNED: {**base, "id": P_ASSIGNED, "agency_id": AGENCY_A, "assigned_agent_id": AGENT_ID},
        P_UNASSIGNED: {**base, "id": P_UNASSIGNED, "agency_id": AGENCY_A, "assigned_agent_id": None},
        P_TRASH: {**base, "id": P_TRASH, "agency_id": AGENCY_A, "assigned_agent_id": AGENT_ID,
                  "deleted_at": "2026-10-01T10:00:00+00:00"},
        P_OTHER_AGENCY: {**base, "id": P_OTHER_AGENCY, "agency_id": AGENCY_B, "assigned_agent_id": None},
    }


class FakeDB:
    def __init__(self):
        self.properties = _properties()
        self.valuations = {}
        self.ids = itertools.count(1)
        self.installed = True
        self.statements = []
        self.locks = []
        self.on_lock = None          # funzione(state, sql, params) chiamata a ogni FOR UPDATE su una valutazione
        self.opened_for_write = 0
        # BLOCCO-2B: artefatti PDF (migrazione 117) e nomi degli operatori
        self.pdfs = {}
        self.pdfs_installed = True
        self.on_pdf_insert = None    # funzione(state) chiamata prima di ogni INSERT di un PDF
        self.operators = {OWNER_ID: ("Giulia", "Rossi"), ADMIN_ID: ("Marco", "Bianchi"),
                          AGENT_ID: ("Luca", "Verdi")}


class FakeCursor:
    def __init__(self, db, st):
        self.db, self.st = db, st
        self._one, self._all = None, []

    def _set(self, rows):
        self._all = [copy.deepcopy(r) for r in rows]
        self._one = self._all[0] if self._all else None

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.db.statements.append(q)
        P, V = self.st["properties"], self.st["valuations"]
        if q.startswith("SELECT to_regclass('property_valuations')"):
            return self._set([{"installed": self.db.installed}])
        if q.startswith("SELECT to_regclass('property_valuation_pdfs')"):
            return self._set([{"installed": self.db.pdfs_installed}])
        if "property_valuation_pdfs" in q:
            return self._pdf_statement(q, params)
        if q.startswith("SELECT first_name, last_name FROM operator_users WHERE id = %s"):
            name = self.db.operators.get(params[0])
            return self._set([{"first_name": name[0], "last_name": name[1]}] if name else [])
        if "property_valuations" in q and not self.db.installed:
            raise RuntimeError('relation "property_valuations" does not exist')
        if q.startswith("SELECT * FROM properties WHERE id = %s AND agency_id = %s"):
            if q.endswith("FOR UPDATE"):
                self.db.locks.append(("property", params[0]))
            if q.endswith("FOR SHARE"):
                self.db.locks.append(("property_share", params[0]))
            p = P.get(params[0])
            return self._set([p] if p and p["agency_id"] == params[1] else [])
        if q.startswith("SELECT * FROM property_valuations WHERE agency_id = %s AND property_id = %s ORDER BY"):
            rows = [v for v in V.values() if v["agency_id"] == params[0] and v["property_id"] == params[1]]
            return self._set(sorted(rows, key=lambda v: -v["version"]))
        if q.startswith("SELECT * FROM property_valuations WHERE id = %s AND agency_id = %s"):
            if q.endswith("FOR UPDATE"):
                self.db.locks.append(("valuation", params[0]))
                if self.db.on_lock:
                    self.db.on_lock(self.st, params[0])
            if q.endswith("FOR SHARE"):
                self.db.locks.append(("valuation_share", params[0]))
            v = V.get(params[0])
            return self._set([v] if v and v["agency_id"] == params[1] else [])
        if q.startswith("SELECT 1 FROM property_valuations WHERE agency_id = %s AND property_id = %s AND status = %s"):
            return self._set([{"x": 1}] if any(v["agency_id"] == params[0] and v["property_id"] == params[1]
                                               and v["status"] == params[2] for v in V.values()) else [])
        if q.startswith("SELECT COALESCE(MAX(version), 0) + 1"):
            versions = [v["version"] for v in V.values() if v["agency_id"] == params[0] and v["property_id"] == params[1]]
            return self._set([{"next": max(versions, default=0) + 1}])
        if q.startswith("INSERT INTO property_valuations"):
            (agency_id, property_id, version, previous, status, method, content, snapshot,
             s_min, s_max, s_rec, r_min, r_max, r_rec, created_by, updated_by) = params
            prop = P[property_id]
            assert prop["agency_id"] == agency_id, "trigger 116: agency mismatch"
            if any(v["property_id"] == property_id and (v["version"] == version or v["status"] == "draft")
                   for v in V.values()):
                raise errors.UniqueViolation("uq_property_valuations_one_draft / version")
            vid = next(self.db.ids)
            V[vid] = {"id": vid, "agency_id": agency_id, "property_id": property_id, "version": version,
                      "previous_valuation_id": previous, "status": status, "method": method,
                      "content": copy.deepcopy(content.adapted), "property_snapshot": copy.deepcopy(snapshot.adapted),
                      "sale_min": s_min, "sale_max": s_max, "sale_recommended": s_rec,
                      "rent_min": r_min, "rent_max": r_max, "rent_recommended": r_rec,
                      "created_by_user_id": created_by, "created_at": "2026-10-28T09:00:00+00:00",
                      "updated_by_user_id": updated_by, "updated_at": "2026-10-28T09:00:00+00:00",
                      "approved_by_user_id": None, "approved_at": None, "superseded_at": None,
                      "superseded_by_valuation_id": None, "row_version": 1}
            return self._set([V[vid]])
        if q.startswith("UPDATE property_valuations SET content = %s"):
            content, s_min, s_max, s_rec, r_min, r_max, r_rec, user, vid, agency_id, status, row_version = params
            v = V.get(vid)
            if not v or v["agency_id"] != agency_id or v["status"] != status or v["row_version"] != row_version:
                return self._set([])
            v.update(content=copy.deepcopy(content.adapted), sale_min=s_min, sale_max=s_max, sale_recommended=s_rec,
                     rent_min=r_min, rent_max=r_max, rent_recommended=r_rec, updated_by_user_id=user,
                     updated_at="2026-10-28T10:00:00+00:00", row_version=v["row_version"] + 1)
            return self._set([v])
        if q.startswith("UPDATE property_valuations SET status = %s, superseded_at"):
            status, by, agency_id, property_id, approved, not_id = params
            hit = [v for v in V.values() if v["agency_id"] == agency_id and v["property_id"] == property_id
                   and v["status"] == approved and v["id"] != not_id]
            for v in hit:
                v.update(status=status, superseded_at="2026-10-28T11:00:00+00:00", superseded_by_valuation_id=by,
                         row_version=v["row_version"] + 1)
            return self._set([{"id": v["id"]} for v in hit])
        if q.startswith("UPDATE property_valuations SET status = %s, approved_by_user_id"):
            status, user, snapshot, vid, agency_id, draft = params
            v = V.get(vid)
            if not v or v["agency_id"] != agency_id or v["status"] != draft:
                return self._set([])
            if any(o["property_id"] == v["property_id"] and o["status"] == "approved" for o in V.values()):
                raise errors.UniqueViolation("uq_property_valuations_one_approved")
            v.update(status=status, approved_by_user_id=user, approved_at="2026-10-28T11:00:00+00:00",
                     property_snapshot=copy.deepcopy(snapshot.adapted), row_version=v["row_version"] + 1)
            return self._set([v])
        raise AssertionError(f"statement inattesa: {q}")

    def _pdf_statement(self, q, params):
        """BLOCCO-2B: le statement della 117, con il trigger che esige la
        versione approvata e vieta UPDATE/DELETE."""
        if not self.db.pdfs_installed:
            raise RuntimeError('relation "property_valuation_pdfs" does not exist')
        D, V = self.st["pdfs"], self.st["valuations"]

        def meta(row, with_bytes=False):
            out = {k: v for k, v in row.items() if k != "pdf_bytes"}
            out["size_bytes"] = len(row["pdf_bytes"])
            if with_bytes:
                out["pdf_bytes"] = bytearray(row["pdf_bytes"])   # psycopg2 da' un buffer, non bytes
            return out

        if q.startswith("SELECT valuation_id,") and q.endswith("WHERE agency_id = %s AND property_id = %s"):
            return self._set([meta(r) for r in D.values() if r["agency_id"] == params[0]
                              and r["property_id"] == params[1]])
        if q.startswith("SELECT valuation_id,") and q.endswith("WHERE valuation_id = %s AND agency_id = %s"):
            r = D.get(params[0])
            return self._set([meta(r, "pdf_bytes" in q)] if r and r["agency_id"] == params[1] else [])
        if q.startswith("INSERT INTO property_valuation_pdfs"):
            if self.db.on_pdf_insert:
                self.db.on_pdf_insert(self.st)
            vid, agency_id, property_id, version, template, header, data, sha, user, generated_at = params
            v = V.get(vid)
            if not (v and v["agency_id"] == agency_id and v["property_id"] == property_id
                    and v["version"] == version and v["status"] == "approved"):
                raise RuntimeError("trigger 117: only an approved valuation of the same agency")
            if vid in D:
                return self._set([])
            D[vid] = {"valuation_id": vid, "agency_id": agency_id, "property_id": property_id,
                      "valuation_version": version, "template_version": template,
                      "header_snapshot": copy.deepcopy(header.adapted), "pdf_bytes": bytes(data), "sha256": sha,
                      "generated_by_user_id": user, "generated_at": generated_at}
            return self._set([meta(D[vid])])
        raise AssertionError(f"statement inattesa: {q}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()

    @contextmanager
    def _core_cursor(*, commit=False):
        fake.opened_for_write += bool(commit)
        st = {"properties": copy.deepcopy(fake.properties), "valuations": copy.deepcopy(fake.valuations),
              "pdfs": copy.deepcopy(fake.pdfs)}
        yield None, FakeCursor(fake, st)
        if commit:
            fake.properties, fake.valuations, fake.pdfs = st["properties"], st["valuations"], st["pdfs"]

    monkeypatch.setattr(service, "core_cursor", _core_cursor)
    monkeypatch.setattr(pdf_service, "core_cursor", _core_cursor)
    return fake


def ctx(role="agency_owner", user_id=OWNER_ID, agency_id=AGENCY_A, platform=False):
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=platform,
                           session_id=1, auth_channel="operator_session")


OWNER = ctx()
ADMIN = ctx("agency_admin", ADMIN_ID)
AGENT = ctx("agent", AGENT_ID)
OTHER_AGENT = ctx("agent", OTHER_AGENT_ID)
ACTING = ctx(None, 1, platform=True)
UNBOUND = ctx(None, 1, agency_id=None, platform=True)
OWNER_B = ctx(user_id=601, agency_id=AGENCY_B)


@pytest.fixture
def api(db):
    def _make(context):
        app = FastAPI()
        app.include_router(valuations_router.router)
        app.dependency_overrides[valuations_router.require_operator] = lambda: context
        return TestClient(app)
    return _make


SALE = {"min": "180000", "max": "210000", "recommended": "195000"}
RENT = {"segment": "residenziale_ordinario", "furnishing": "arredato", "expenses": "escluse",
        "min": "700", "max": "850", "recommended": "780"}
BASE = {"characteristics": "Trilocale al secondo piano, ristrutturato nel 2020.",
        "surface": {"value": "85", "kind": "commerciale", "source": "planimetria"},
        "rationale": "Immobile in buono stato, zona servita; valori allineati agli annunci recenti.",
        "sources": "Sopralluogo del 20/10/2026 e annunci di zona consultati dall'agente.",
        "comparables_note": "Nessuna compravendita conclusa nota in zona negli ultimi sei mesi."}
COMPARABLE = {"kind": "vendita", "price_type": "richiesto", "amount": "205000", "source": "Annuncio di portale",
              "data_date": "2026-10-01", "surface": "90", "surface_kind": "commerciale", "zone": "Centro",
              "url": "https://esempio.example/annuncio/1", "differences": "Piano piu' alto, senza box."}


def _create(client, property_id=P_ASSIGNED, **content):
    return client.post("/api/property-valuations", json={"property_id": property_id, "content": content})


def _approve(client, v):
    return client.post(f"/api/property-valuations/{v['id']}/approve", json={"row_version": v["row_version"]})


def _approved(api, db, content=None, property_id=P_ASSIGNED):
    c = api(OWNER)
    v = _create(c, property_id, **(content or {**BASE, "sale": SALE})).json()
    r = _approve(c, v)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# A - creazione senza incarico; autore dal server; prezzo invariato
# ---------------------------------------------------------------------------

def test_a_a_draft_is_created_on_a_property_without_mandate(api, db):
    r = _create(api(AGENT), **BASE)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "draft" and v["version"] == 1 and v["previous_valuation_id"] is None
    assert v["method"] == "valutazione_agente_v1" and v["method_label"] == "Valutazione dell'agente"
    assert v["created_by_user_id"] == AGENT_ID and v["agency_id"] == AGENCY_A
    assert db.properties[P_ASSIGNED]["mandate_type"] is None


@pytest.mark.parametrize("extra", [{"agency_id": AGENCY_B}, {"created_by_user_id": 1}, {"approved_by_user_id": 1}])
def test_a_client_supplied_identity_fields_are_refused(api, db, extra):
    r = api(OWNER).post("/api/property-valuations", json={"property_id": P_ASSIGNED, "content": {}, **extra})
    assert r.status_code == 422 and db.valuations == {}


def test_a_property_price_is_never_touched(api, db):
    before = copy.deepcopy(db.properties)
    _approved(api, db, {**BASE, "sale": {**SALE, "recommended": "150000", "outside_range_reason": "Urgenza di vendita"}})
    assert db.properties == before
    assert not any(s.startswith("UPDATE properties") for s in db.statements)


def test_a_property_snapshot_is_kept_with_the_valuation(api, db):
    v = _approved(api, db)
    snap = v["property_snapshot"]
    assert snap["id"] == P_ASSIGNED and snap["asking_price"] == "199000.00" and snap["city"] == "Alba Adriatica"
    assert snap["surface_sqm"] == "80.00"


# ---------------------------------------------------------------------------
# B - vendita sola, locazione sola, entrambe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sections", [{"sale": SALE}, {"rent": RENT}, {"sale": SALE, "rent": RENT}],
                         ids=["vendita", "locazione", "entrambe"])
def test_b_one_or_both_sections_can_be_approved(api, db, sections):
    v = _approved(api, db, {**BASE, **sections})
    assert v["status"] == "approved"
    assert v["sale_evaluated"] is ("sale" in sections) and v["rent_evaluated"] is ("rent" in sections)


def test_b_a_missing_section_is_not_evaluated_never_zero(api, db):
    v = _approved(api, db, {**BASE, "rent": RENT})
    assert v["content"]["sale"] is None
    assert v["sale_min"] is None and v["sale_max"] is None and v["sale_recommended"] is None
    stored = db.valuations[v["id"]]
    assert stored["sale_recommended"] is None and stored["rent_recommended"] == Decimal("780")


def test_b_an_empty_section_object_becomes_not_evaluated(api, db):
    v = _create(api(OWNER), **BASE, sale={"min": None, "max": None}).json()
    assert v["content"]["sale"] is None


def test_b_amounts_are_decimals_not_floats(api, db):
    v = _create(api(OWNER), **BASE, sale={"min": "180000.10", "max": "210000.20", "recommended": "195000.15"}).json()
    stored = db.valuations[v["id"]]
    assert stored["sale_recommended"] == Decimal("195000.15") and isinstance(stored["sale_recommended"], Decimal)
    assert v["content"]["sale"]["recommended"] == "195000.15"


def test_b_rent_is_monthly_and_not_derived_from_the_sale(api, db):
    v = _create(api(OWNER), **BASE, sale=SALE).json()
    assert v["content"]["rent"] is None and v["rent_recommended"] is None


# ---------------------------------------------------------------------------
# C - bozza incompleta salvabile; approvazione rifiutata se incompleta
# ---------------------------------------------------------------------------

def test_c_an_empty_draft_can_be_saved_and_lists_what_is_missing(api, db):
    v = _create(api(OWNER)).json()
    assert v["status"] == "draft" and len(v["approval_issues"]) >= 5


@pytest.mark.parametrize("content,needle", [
    ({**BASE}, "almeno una sezione"),
    ({**BASE, "sale": {"min": "1000"}}, "vendita e' incompleta"),
    ({**BASE, "rent": {**RENT, "segment": None}}, "locazione e' incompleta"),
    ({**BASE, "sale": SALE, "rationale": None}, "motivazione"),
    ({**BASE, "sale": SALE, "sources": None}, "fonti"),
    ({**BASE, "sale": SALE, "characteristics": None}, "caratteristiche"),
    ({**BASE, "sale": SALE, "surface": {"value": "85"}}, "superficie"),
    ({**BASE, "sale": SALE, "comparables_note": None}, "comparabile"),
    ({**BASE, "sale": {**SALE, "recommended": "250000"}}, "fuori dall'intervallo"),
])
def test_c_incomplete_valuations_cannot_be_approved(api, db, content, needle):
    c = api(OWNER)
    v = _create(c, **content).json()
    r = _approve(c, v)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INCOMPLETE"
    assert any(needle in i for i in r.json()["detail"]["issues"]), r.json()["detail"]["issues"]
    assert db.valuations[v["id"]]["status"] == "draft"


def test_c_outside_range_with_a_specific_reason_is_approvable(api, db):
    v = _approved(api, db, {**BASE, "sale": {**SALE, "recommended": "250000", "outside_range_reason": "Vista mare unica"}})
    assert v["status"] == "approved"


def test_c_comparables_replace_the_absence_statement(api, db):
    content = {**BASE, "sale": SALE, "comparables_note": None, "comparables": [COMPARABLE]}
    assert _approved(api, db, content)["status"] == "approved"


def test_c_no_comparables_and_no_statement_is_explicitly_refused():
    issues = approval_issues(ValuationContent(**{**BASE, "sale": SALE, "comparables_note": None}))
    assert issues == ["Nessun comparabile: dichiara esplicitamente perche' non ce ne sono."]


# ---------------------------------------------------------------------------
# D - permessi e isolamento
# ---------------------------------------------------------------------------

def test_d_agent_creates_and_edits_own_drafts_on_assigned_properties(api, db):
    c = api(AGENT)
    v = _create(c, **BASE).json()
    r = c.put(f"/api/property-valuations/{v['id']}", json={"row_version": 1, "content": {**BASE, "sale": SALE}})
    assert r.status_code == 200 and r.json()["row_version"] == 2


def test_d_agent_cannot_touch_unassigned_properties(api, db):
    assert _create(api(AGENT), P_UNASSIGNED).status_code == 403
    assert db.valuations == {}


def test_d_agent_cannot_edit_a_colleague_draft(api, db):
    v = _create(api(OWNER), **BASE).json()
    r = api(AGENT).put(f"/api/property-valuations/{v['id']}", json={"row_version": 1, "content": {}})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_OWN_DRAFT"
    assert _create(api(OTHER_AGENT)).status_code == 403


def test_d_agent_never_approves(api, db):
    c = api(AGENT)
    v = _create(c, **BASE, sale=SALE).json()
    assert v["can_approve"] is False
    r = _approve(c, v)
    assert r.status_code == 403 and db.valuations[v["id"]]["status"] == "draft"


@pytest.mark.parametrize("context", [OWNER, ADMIN, ACTING], ids=["owner", "admin", "super-admin-acting"])
def test_d_owner_admin_and_acting_super_admin_approve(api, db, context):
    c = api(context)
    v = _create(c, P_UNASSIGNED, **BASE, sale=SALE).json()
    r = _approve(c, v)
    assert r.status_code == 200 and r.json()["approved_by_user_id"] == context.user_id


def test_d_unbound_super_admin_is_refused(api, db):
    assert api(UNBOUND).get(f"/api/property-valuations?property_id={P_ASSIGNED}").status_code == 403
    assert _create(api(UNBOUND)).status_code == 403


def test_d_other_agency_sees_nothing(api, db):
    v = _create(api(OWNER), **BASE).json()
    b = api(OWNER_B)
    assert b.get(f"/api/property-valuations?property_id={P_ASSIGNED}").status_code == 404
    assert b.get(f"/api/property-valuations/{v['id']}").status_code == 404
    assert b.put(f"/api/property-valuations/{v['id']}", json={"row_version": 1, "content": {}}).status_code == 404
    assert b.post(f"/api/property-valuations/{v['id']}/approve", json={"row_version": 1}).status_code == 404
    assert _create(api(OWNER), P_OTHER_AGENCY).status_code == 404


def test_d_reading_is_open_to_every_operator_of_the_agency(api, db):
    _create(api(OWNER), P_UNASSIGNED, **BASE)
    body = api(OTHER_AGENT).get(f"/api/property-valuations?property_id={P_UNASSIGNED}").json()
    assert len(body["items"]) == 1 and body["can_create"] is False and body["items"][0]["can_edit"] is False


def test_d_property_in_trash_refuses_writes(api, db):
    r = _create(api(OWNER), P_TRASH)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PROPERTY_IN_TRASH"


def test_d_read_only_consultation_blocks_writes():
    from fastapi import HTTPException

    from operator_auth.dependencies import READ_ONLY_EXEMPT_PREFIXES, refuse_writes_in_read_only

    assert not "/api/property-valuations".startswith(READ_ONLY_EXEMPT_PREFIXES)
    read_only = OperatorContext(user_id=1, agency_id=AGENCY_A, role=None, is_platform_admin=True,
                                session_id=1, auth_channel="operator_session", read_only=True)

    class _Req:
        method = "POST"

        class url:
            path = "/api/property-valuations/1/approve"

    with pytest.raises(HTTPException) as exc:
        refuse_writes_in_read_only(_Req(), read_only)
    assert exc.value.status_code == 403


def test_d_owner_portal_does_not_read_valuations():
    for path in (ROOT / "owner").glob("*.py"):
        assert "property_valuations" not in path.read_text(encoding="utf-8"), path


# ---------------------------------------------------------------------------
# E - versioni
# ---------------------------------------------------------------------------

def test_e_an_approved_valuation_is_immutable(api, db):
    v = _approved(api, db)
    before = copy.deepcopy(db.valuations[v["id"]])
    r = api(OWNER).put(f"/api/property-valuations/{v['id']}", json={"row_version": v["row_version"], "content": {}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_DRAFT"
    assert db.valuations[v["id"]] == before


def test_e_new_version_does_not_supersede_until_it_is_approved(api, db):
    c = api(OWNER)
    v1 = _approved(api, db)
    v2 = c.post(f"/api/property-valuations/{v1['id']}/new-version").json()
    assert v2["status"] == "draft" and v2["version"] == 2 and v2["previous_valuation_id"] == v1["id"]
    assert v2["content"] == v1["content"]
    assert db.valuations[v1["id"]]["status"] == "approved"                 # ancora valida

    r = _approve(c, v2)
    assert r.status_code == 200
    old = db.valuations[v1["id"]]
    assert old["status"] == "superseded" and old["superseded_by_valuation_id"] == v2["id"]
    assert old["content"] == v1["content"] and old["approved_by_user_id"] == OWNER_ID
    statuses = sorted(v["status"] for v in db.valuations.values())
    assert statuses == ["approved", "superseded"]


def test_e_only_one_draft_per_property(api, db):
    c = api(OWNER)
    v1 = _approved(api, db)
    assert c.post(f"/api/property-valuations/{v1['id']}/new-version").status_code == 201
    assert c.post(f"/api/property-valuations/{v1['id']}/new-version").json()["detail"]["code"] == "DRAFT_EXISTS"
    assert _create(c).json()["detail"]["code"] == "DRAFT_EXISTS"


def test_e_a_draft_is_not_a_source_for_a_new_version(api, db):
    v = _create(api(OWNER)).json()
    r = api(OWNER).post(f"/api/property-valuations/{v['id']}/new-version")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOURCE_NOT_APPROVED"


def test_e_the_history_lists_every_version_newest_first(api, db):
    c = api(OWNER)
    v1 = _approved(api, db)
    v2 = c.post(f"/api/property-valuations/{v1['id']}/new-version").json()
    _approve(c, v2)
    items = c.get(f"/api/property-valuations?property_id={P_ASSIGNED}").json()["items"]
    assert [(i["version"], i["status"]) for i in items] == [(2, "approved"), (1, "superseded")]


# ---------------------------------------------------------------------------
# F - concorrenza
# ---------------------------------------------------------------------------

def test_f_a_double_approval_request_is_refused(api, db):
    c = api(OWNER)
    v = _create(c, **BASE, sale=SALE).json()
    assert _approve(c, v).status_code == 200
    again = _approve(c, v)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_APPROVED"
    assert [x["status"] for x in db.valuations.values()] == ["approved"]


def test_f_a_stale_row_version_is_refused_on_edit_and_approval(api, db):
    c = api(OWNER)
    v = _create(c, **BASE, sale=SALE).json()
    assert c.put(f"/api/property-valuations/{v['id']}", json={"row_version": 1, "content": {**BASE, "sale": SALE}}).status_code == 200
    stale_edit = c.put(f"/api/property-valuations/{v['id']}", json={"row_version": 1, "content": {}})
    assert stale_edit.status_code == 409 and stale_edit.json()["detail"]["code"] == "VERSION_CONFLICT"
    stale_approve = c.post(f"/api/property-valuations/{v['id']}/approve", json={"row_version": 1})
    assert stale_approve.status_code == 409 and db.valuations[v["id"]]["status"] == "draft"


def test_f_an_approval_that_lost_the_race_is_refused(api, db):
    """Corsa simulata in modo deterministico: mentre questa approvazione
    aspettava il lock, un'altra ha approvato e committato la stessa bozza."""
    c = api(OWNER)
    v = _create(c, **BASE, sale=SALE).json()

    def _winner_committed(state, valuation_id):
        state["valuations"][valuation_id].update(status="approved", approved_by_user_id=ADMIN_ID,
                                                  approved_at="2026-10-28T10:59:00+00:00", row_version=2)

    db.on_lock = _winner_committed
    r = _approve(c, v)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ALREADY_APPROVED"


def test_f_approval_locks_the_property_before_the_valuation(api, db):
    c = api(OWNER)
    v = _create(c, **BASE, sale=SALE).json()
    db.locks.clear()
    _approve(c, v)
    assert db.locks[:2] == [("property", P_ASSIGNED), ("valuation", v["id"])]


def test_f_a_refused_approval_writes_nothing(api, db):
    c = api(OWNER)
    v = _create(c, **BASE).json()
    before = copy.deepcopy(db.valuations)
    assert _approve(c, v).status_code == 422
    assert db.valuations == before


# ---------------------------------------------------------------------------
# G - validazioni
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sale", [
    {"min": "0"}, {"min": "-1"}, {"min": "abc"}, {"min": "NaN"}, {"min": "Infinity"},
    {"min": "1.001"}, {"min": "1000000000000000"}, {"min": "200000", "max": "100000"},
])
def test_g_invalid_sale_amounts_are_refused(api, db, sale):
    assert _create(api(OWNER), sale=sale).status_code == 422 and db.valuations == {}


@pytest.mark.parametrize("rent", [
    {"min": "900", "max": "700"}, {"segment": "turistico"}, {"segment": "giornaliero"},
    {"furnishing": "boh"}, {"expenses": "forse"}, {"recommended": "0"},
])
def test_g_invalid_rent_values_are_refused(api, db, rent):
    assert _create(api(OWNER), rent=rent).status_code == 422


@pytest.mark.parametrize("surface", [{"value": "0"}, {"value": "-5"}, {"kind": "metrica"}, {"source": "sentito dire"}])
def test_g_invalid_surface_is_refused(api, db, surface):
    assert _create(api(OWNER), surface=surface).status_code == 422


@pytest.mark.parametrize("change", [
    {"kind": None}, {"price_type": None}, {"amount": None}, {"amount": "0"}, {"source": "  "},
    {"kind": "locazione"},                                       # manca il periodo del canone
    {"rent_period": "mensile"},                                  # periodo su un comparabile di vendita
    {"kind": "locazione", "rent_period": "giornaliero"},
    {"data_date": (date.today() + timedelta(days=2)).isoformat()},
    {"url": "javascript:alert(1)"}, {"url": "ftp://esempio.example"}, {"url": "https://u:p@esempio.example"},
    {"surface": "0"},
])
def test_g_invalid_comparables_are_refused(api, db, change):
    assert _create(api(OWNER), comparables=[{**COMPARABLE, **change}]).status_code == 422


def test_g_a_valid_rent_comparable_with_its_period(api, db):
    rent_comp = {**COMPARABLE, "kind": "locazione", "price_type": "concluso", "amount": "750", "rent_period": "mensile"}
    v = _create(api(OWNER), comparables=[rent_comp]).json()
    assert v["content"]["comparables"][0]["rent_period"] == "mensile"


def test_g_control_characters_and_long_texts_are_refused(api, db):
    assert _create(api(OWNER), rationale="x" * 4001).status_code == 422
    assert _create(api(OWNER), rationale="riga\x07").status_code == 422
    assert _create(api(OWNER), rationale="prima riga\nseconda riga").status_code == 201


def test_g_markup_is_stored_as_text(api, db):
    v = _create(api(OWNER), characteristics="<img src=x onerror=alert(1)>").json()
    assert v["content"]["characteristics"] == "<img src=x onerror=alert(1)>"


# ---------------------------------------------------------------------------
# H - migrazione 116
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda c: c.get(f"/api/property-valuations?property_id={P_ASSIGNED}"),
    lambda c: _create(c),
    lambda c: c.post("/api/property-valuations/1/approve", json={"row_version": 1}),
])
def test_h_without_migration_116_every_route_answers_503(api, db, call):
    db.installed = False
    r = call(api(OWNER))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "VALUATIONS_UNAVAILABLE"


def test_h_migration_116_is_valid_for_the_runner_and_additive():
    import re
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA BLOCCO-2B: la 117 segue la 116.
    m116 = next(m for m in tutte if m.version == "116_property_valuations")
    # SENTINELLA AGGIORNATA DA BLOCCO-4A: poi la 118 (ricezione WhatsApp).
    assert tutte[-1].version == "118_whatsapp_inbound"
    assert runner.validate_migration(m116) == []
    up = (ROOT / "migrations" / "116_property_valuations.sql").read_text(encoding="utf-8")
    eseguibile = "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--"))
    assert not re.search(r"\b(ALTER|DROP|DELETE|INSERT)\s", eseguibile.replace("TG_OP = 'DELETE'", "").replace(
        "UPDATE OR DELETE ON", "").replace("BEFORE INSERT", ""))
    assert "uq_property_valuations_one_approved" in up and "uq_property_valuations_one_draft" in up
    down = (ROOT / "migrations" / "116_property_valuations_down.sql").read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in down and "DROP TABLE property_valuations" in down


def test_h_the_router_is_mounted_behind_the_authenticated_operator():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert ("app.include_router(property_valuations_router, dependencies=[Depends(require_authenticated_operator)])"
            in main)


# ---------------------------------------------------------------------------
# I - interfaccia
# ---------------------------------------------------------------------------

def test_i_the_property_sheet_has_a_valuations_tab_not_a_main_nav_entry():
    scheda = (ROOT / "static" / "os_shell" / "assets" / "views" / "immobile-dettaglio.js").read_text(encoding="utf-8")
    assert "{ key: 'valutazioni', label: 'Valutazioni' }" in scheda
    assert ("case 'valutazioni': await mountPropertyValuations(contentEl, { propertyId: property.id, "
            "loadAgents: getAgents }); break;") in scheda
    main_js = (ROOT / "static" / "os_shell" / "assets" / "main.js").read_text(encoding="utf-8")
    sections = main_js[main_js.index("const SECTIONS = ["):main_js.index("];", main_js.index("const SECTIONS = ["))]
    assert "valutaz" not in sections


def test_i_the_component_renders_text_safely_and_states_the_method():
    comp = (ROOT / "static" / "os_shell" / "assets" / "components" / "property-valuations.js").read_text(encoding="utf-8")
    assert "innerHTML" not in comp and "insertAdjacentHTML" not in comp
    assert "agenda/" not in comp                    # nessun import diretto dall'Agenda
    assert "Non è una stima automatica, non è una quotazione OMI e non è una perizia." in comp
    codice = "\n".join(r for r in comp.splitlines() if not r.lstrip().startswith("//"))
    for vietato in ("agency_id", "created_by_user_id:", "approved_by_user_id:", "BASE_MQ"):
        assert vietato not in codice, vietato
