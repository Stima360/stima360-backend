"""BLOCCO-2B - PDF della valutazione approvata, con il logo dell'agenzia.

Offline. Riusa il database in memoria dei test BLOCCO-2A (statement reali del
repository, transazioni, trigger della 116 e della 117 riprodotti) e le route
vere; l'intestazione dell'agenzia (`agency_profile.service.report_identity`)
e' sostituita da un dizionario per agenzia. Il contenuto del PDF e' verificato
sulla storia di ReportLab (paragrafi, celle e piede di pagina disegnati),
perche' i caratteri incorporati non sono leggibili dai byte compressi; la resa
visiva e' stata ispezionata a parte sulle pagine rasterizzate.

Coverage map:

    A  contenuto: solo vendita, solo locazione, entrambe; "Non valutata"; dicitura
    B  bozza rifiutata; versione superata senza PDF rifiutata; Cestino
    C  agenzia senza logo; logo illeggibile o alterato -> errore, nessun artefatto
    D  permessi e isolamento tra agenzie; nessun proprietario, nessun link pubblico
    E  artefatto immutabile: stessi byte, profilo cambiato dopo, nuova versione distinta,
       documento storico della versione superata scaricabile
    F  doppia generazione: in sequenza e in corsa; ricontrollo dello stato al salvataggio
    G  importi, testi lunghi (piu' pagine), caratteri speciali e testo dell'utente sicuro
    H  migrazione 117 assente: 503 e valutazioni utilizzabili; migrazione valida
    I  interfaccia: "Genera PDF" / "Scarica PDF" nella sezione Valutazioni
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from agency_profile import service as agency_profile_service
from property_valuations import pdf_render, pdf_service
from tests.test_blocco_2a_property_valuations import (  # noqa: F401 - fixture
    ACTING,
    ADMIN,
    AGENCY_A,
    AGENCY_B,
    AGENT,
    BASE,
    COMPARABLE,
    OTHER_AGENT,
    OWNER,
    OWNER_B,
    OWNER_ID,
    P_ASSIGNED,
    P_OTHER_AGENCY,
    P_UNASSIGNED,
    RENT,
    SALE,
    _approve,
    _approved,
    _create,
    api,
    db,
)

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "static" / "os_shell" / "assets"


def _png(width=320, height=120, color=(20, 70, 140)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


LOGO = _png()


def _identity(agency_id, name, *, logo=LOGO, **fields):
    data = {"agency_id": agency_id, "agency_name": name, "header_name": fields.pop("display_name", None) or name,
            "display_name_configured": False, "display_name": None, "legal_name": None, "vat_number": None,
            "tax_code": None, "legal_address": None, "phone": None, "email": None, "website": None}
    data.update(fields)
    data["logo"] = None if logo is None else {"content_type": "image/png", "width": 320, "height": 120,
                                              "sha256": hashlib.sha256(logo).hexdigest(), "data": logo}
    return data


@pytest.fixture
def identities(monkeypatch):
    table = {
        AGENCY_A: _identity(AGENCY_A, "Agenzia Alba", legal_name="Alba Immobiliare S.r.l.", vat_number="01234567890",
                            phone="+39 0861 000000", email="info@alba.example"),
        AGENCY_B: _identity(AGENCY_B, "Agenzia Borgo", logo=None),
    }
    calls = []

    def _report_identity(ctx):
        calls.append(ctx.agency_id)
        return table[ctx.agency_id]

    monkeypatch.setattr(agency_profile_service, "report_identity", _report_identity)
    table["calls"] = calls
    return table


@pytest.fixture
def capture(monkeypatch):
    """Il testo messo nel PDF: paragrafi, celle delle tabelle, piede di pagina."""
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate

    seen = {"story": [], "canvas": []}
    original_build = SimpleDocTemplate.build
    original_draw, original_right = Canvas.drawString, Canvas.drawRightString

    def walk(item):
        if item is None:
            return
        if isinstance(item, str):
            seen["story"].append(item)
        elif isinstance(item, (list, tuple)):
            for x in item:
                walk(x)
        elif hasattr(item, "getPlainText"):
            seen["story"].append(item.getPlainText())
        elif hasattr(item, "_cellvalues"):
            walk(item._cellvalues)
        elif hasattr(item, "_content"):
            walk(item._content)

    def build(self, flowables, *args, **kwargs):
        seen["story"].clear()
        seen["canvas"].clear()
        walk(list(flowables))
        return original_build(self, flowables, *args, **kwargs)

    def draw(self, x, y, text, *args, **kwargs):
        seen["canvas"].append(text)
        return original_draw(self, x, y, text, *args, **kwargs)

    def draw_right(self, x, y, text, *args, **kwargs):
        seen["canvas"].append(text)
        return original_right(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(SimpleDocTemplate, "build", build)
    monkeypatch.setattr(Canvas, "drawString", draw)
    monkeypatch.setattr(Canvas, "drawRightString", draw_right)
    seen["text"] = lambda: "\n".join(seen["story"] + seen["canvas"])
    return seen


def _pages(data: bytes) -> int:
    return len(re.findall(rb"/Type /Page[^s]", data))


def _generate(client, v):
    return client.post(f"/api/property-valuations/{v['id']}/pdf")


def _download(client, v):
    return client.get(f"/api/property-valuations/{v['id']}/pdf")


# ---------------------------------------------------------------------------
# A - contenuto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sections", [{"sale": SALE}, {"rent": RENT}, {"sale": SALE, "rent": RENT}],
                         ids=["vendita", "locazione", "entrambe"])
def test_a_one_or_both_sections_are_printed_and_the_missing_one_is_not_evaluated(api, db, identities, capture,
                                                                                 sections):
    v = _approved(api, db, {**BASE, **sections})
    r = _generate(api(OWNER), v)
    assert r.status_code == 201, r.text
    text = capture["text"]()
    assert "Valutazione dell'agente" in text
    assert "Non è una perizia certificata né una quotazione automatica OMI" in text
    assert "Agenzia Alba" in text and "Alba Immobiliare S.r.l." in text and "P.IVA 01234567890" in text
    assert "Alba Adriatica" in text and "Versione" in text and "Approvata" in text and "Giulia Rossi" in text
    if "sale" in sections:
        assert "180.000,00 €" in text and "210.000,00 €" in text and "195.000,00 €" in text
    if "rent" in sections:
        assert "700,00 €/mese" in text and "850,00 €/mese" in text and "780,00 €/mese" in text
        assert "Residenziale ordinario" in text and "Arredato" in text and "Spese escluse" in text
    assert text.count("Non valutata") == 2 - len(sections)
    assert "Pagina 1 di" in text and "versione 1" in text
    pdf = db.pdfs[v["id"]]["pdf_bytes"]
    assert pdf.startswith(b"%PDF-") and _pages(pdf) >= 1
    # la data stampata e' quella salvata con l'artefatto
    assert f"Documento generato il\n{pdf_render.italian_datetime(db.pdfs[v['id']]['generated_at'])}" in text


def test_a_comparables_distinguish_asked_and_closed_prices_and_rent_period(api, db, identities, capture):
    closed = {**COMPARABLE, "price_type": "concluso", "amount": "188000", "zone": "Lungomare"}
    rent = {**COMPARABLE, "kind": "locazione", "amount": "9600", "rent_period": "annuale", "zone": "Villa Fiore"}
    v = _approved(api, db, {**BASE, "sale": SALE, "comparables": [COMPARABLE, closed, rent]})
    assert _generate(api(OWNER), v).status_code == 201
    text = capture["text"]()
    assert "Prezzo richiesto" in text and "Prezzo concluso" in text
    assert "205.000,00 €" in text and "188.000,00 €" in text and "9.600,00 €/anno" in text and "Periodo di riferimento dell'importo: annuale" in text
    assert "(riportato, non consultato)" in text and "https://esempio.example/annuncio/1" in text


def test_a_declared_absence_of_comparables_is_printed(api, db, identities, capture):
    v = _approved(api, db, {**BASE, "sale": SALE})
    assert _generate(api(OWNER), v).status_code == 201
    assert ("Nessun comparabile. Motivo dichiarato dall'agente: Nessuna compravendita conclusa nota in zona"
            in capture["text"]())


def test_a_unconfigured_agency_data_are_omitted_never_invented(api, db, identities, capture):
    v = _approved(api, db, {**BASE, "sale": SALE})
    assert _generate(api(OWNER), v).status_code == 201
    text = capture["text"]()
    assert "C.F." not in text and "Sede legale" not in text and "None" not in text


# ---------------------------------------------------------------------------
# B - solo versioni approvate
# ---------------------------------------------------------------------------

def test_b_a_draft_is_refused_and_nothing_is_saved(api, db, identities):
    v = _create(api(OWNER), **BASE, sale=SALE).json()
    r = _generate(api(OWNER), v)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PDF_NOT_APPROVED"
    assert db.pdfs == {} and identities["calls"] == []
    assert v["can_generate_pdf"] is False and v["pdf"] is None


def test_b_a_superseded_version_without_pdf_cannot_be_generated(api, db, identities):
    old = _approved(api, db)
    c = api(OWNER)
    new = c.post(f"/api/property-valuations/{old['id']}/new-version").json()
    assert _approve(c, new).status_code == 200
    r = _generate(c, old)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PDF_SUPERSEDED"
    assert db.pdfs == {}


def test_b_a_property_in_trash_gets_no_new_pdf(api, db, identities):
    v = _approved(api, db)
    db.properties[P_ASSIGNED]["deleted_at"] = "2026-10-29T08:00:00+00:00"
    r = _generate(api(OWNER), v)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PROPERTY_IN_TRASH"
    assert db.pdfs == {}


# ---------------------------------------------------------------------------
# C - logo
# ---------------------------------------------------------------------------

def test_c_an_agency_without_logo_gets_a_pdf_with_its_name(api, db, identities, capture):
    identities[AGENCY_A] = _identity(AGENCY_A, "Agenzia Alba", logo=None)
    v = _approved(api, db)
    r = _generate(api(OWNER), v)
    assert r.status_code == 201, r.text
    assert "Agenzia Alba" in capture["text"]()
    assert db.pdfs[v["id"]]["header_snapshot"]["logo"] is None


def test_c_the_logo_is_embedded_and_recorded_in_the_snapshot(api, db, identities):
    v = _approved(api, db)
    assert _generate(api(OWNER), v).status_code == 201
    saved = db.pdfs[v["id"]]
    assert b"/Subtype /Image" in saved["pdf_bytes"]
    assert saved["header_snapshot"]["logo"]["sha256"] == hashlib.sha256(LOGO).hexdigest()
    assert "data" not in saved["header_snapshot"]["logo"]


@pytest.mark.parametrize("damage", ["sha", "bytes"])
def test_c_an_unreadable_logo_is_an_error_never_a_silent_pdf_without_it(api, db, identities, damage):
    broken = b"\x89PNG\r\n\x1a\nnon-un-png"
    logo = identities[AGENCY_A]["logo"]
    if damage == "sha":
        logo["data"] = LOGO[:-8]                      # impronta non corrispondente
    else:
        logo.update(data=broken, sha256=hashlib.sha256(broken).hexdigest())
    v = _approved(api, db)
    r = _generate(api(OWNER), v)
    assert r.status_code == 500 and r.json()["detail"]["code"] == "LOGO_UNREADABLE"
    assert db.pdfs == {}


def test_c_a_corrupted_agency_profile_is_an_error(api, db, identities, monkeypatch):
    def _corrupted(ctx):
        raise agency_profile_service.ProfileCorrupted("profilo illeggibile")

    monkeypatch.setattr(agency_profile_service, "report_identity", _corrupted)
    v = _approved(api, db)
    r = _generate(api(OWNER), v)
    assert r.status_code == 500 and r.json()["detail"]["code"] == "AGENCY_PROFILE_CORRUPTED"
    assert db.pdfs == {}


def test_c_render_rejects_an_undecodable_logo():
    identity = _identity(AGENCY_A, "Agenzia Alba", logo=b"GIF89a-rotto")
    valuation = {"version": 1, "status": "approved", "content": {}, "property_snapshot": {}}
    with pytest.raises(pdf_render.PdfLogoError):
        pdf_render.render_valuation_pdf(valuation, identity, approver_name="X", generated_at=None)


# ---------------------------------------------------------------------------
# D - permessi e isolamento
# ---------------------------------------------------------------------------

def test_d_generation_follows_the_write_permissions(api, db, identities):
    assigned = _approved(api, db, property_id=P_ASSIGNED)
    unassigned = _approved(api, db, property_id=P_UNASSIGNED)
    r = _generate(api(OTHER_AGENT), assigned)
    assert r.status_code == 403 and db.pdfs == {}
    assert _generate(api(AGENT), unassigned).status_code == 403
    assert _generate(api(AGENT), assigned).status_code == 201
    assert _generate(api(ADMIN), unassigned).status_code == 201
    assert db.pdfs[assigned["id"]]["generated_by_user_id"] == AGENT.user_id


def test_d_download_follows_the_read_permissions(api, db, identities):
    v = _approved(api, db, property_id=P_UNASSIGNED)
    assert _generate(api(OWNER), v).status_code == 201
    for who in (ADMIN, AGENT, OTHER_AGENT, ACTING):
        r = _download(api(who), v)
        assert r.status_code == 200 and r.content == db.pdfs[v["id"]]["pdf_bytes"]


def test_d_another_agency_never_sees_nor_generates_the_document(api, db, identities):
    v = _approved(api, db)
    assert _generate(api(OWNER), v).status_code == 201
    for call in (_generate, _download):
        r = call(api(OWNER_B), v)
        assert r.status_code == 404 and b"%PDF" not in r.content
    assert identities["calls"] == [AGENCY_A]


def test_d_the_agency_header_comes_from_the_session_agency(api, db, identities):
    vb = api(OWNER_B).post("/api/property-valuations", json={"property_id": P_OTHER_AGENCY,
                                                             "content": {**BASE, "sale": SALE}}).json()
    assert _approve(api(OWNER_B), vb).status_code == 200
    assert _generate(api(OWNER_B), vb).status_code == 201
    assert db.pdfs[vb["id"]]["header_snapshot"]["agency_name"] == "Agenzia Borgo"
    assert db.pdfs[vb["id"]]["agency_id"] == AGENCY_B


def test_d_routes_take_no_identity_from_the_client_and_offer_no_public_link():
    src = (ROOT / "property_valuations" / "router.py").read_text(encoding="utf-8")
    assert "agency_id: int" not in src and "token" not in src.lower()
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "property_valuations_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line
    for owner_file in (ROOT / "owner").rglob("*.py"):
        assert "property_valuation" not in owner_file.read_text(encoding="utf-8"), owner_file


def test_d_download_headers_keep_the_document_private(api, db, identities):
    v = _approved(api, db)
    _generate(api(OWNER), v)
    r = _download(api(OWNER), v)
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="valutazione-immobile-{P_ASSIGNED}-v1.pdf"'
    assert r.headers["cache-control"] == "private, no-store" and r.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# E - artefatto immutabile
# ---------------------------------------------------------------------------

def test_e_download_returns_exactly_the_stored_bytes(api, db, identities):
    v = _approved(api, db, {**BASE, "sale": SALE, "rent": RENT})
    r = _generate(api(OWNER), v)
    meta = r.json()["pdf"]
    saved = db.pdfs[v["id"]]
    assert meta["sha256"] == saved["sha256"] == hashlib.sha256(saved["pdf_bytes"]).hexdigest()
    assert meta["size_bytes"] == len(saved["pdf_bytes"]) and meta["valuation_version"] == 1
    assert meta["template_version"] == pdf_render.TEMPLATE_VERSION
    first, second = _download(api(OWNER), v), _download(api(AGENT), v)
    assert first.content == second.content == saved["pdf_bytes"]
    assert first.headers["etag"] == f'"{saved["sha256"]}"'


def test_e_a_later_profile_or_logo_change_does_not_alter_the_document(api, db, identities):
    v = _approved(api, db)
    _generate(api(OWNER), v)
    before = db.pdfs[v["id"]]["pdf_bytes"]
    identities[AGENCY_A] = _identity(AGENCY_A, "Nuovo Nome", logo=_png(color=(200, 0, 0)), phone="+39 333")
    again = _generate(api(OWNER), v)
    assert again.status_code == 200 and again.json()["created"] is False
    assert _download(api(OWNER), v).content == before
    assert db.pdfs[v["id"]]["header_snapshot"]["agency_name"] == "Agenzia Alba"


def test_e_a_new_approved_version_gets_its_own_document_and_the_old_one_stays(api, db, identities):
    old = _approved(api, db)
    c = api(OWNER)
    _generate(c, old)
    old_bytes = db.pdfs[old["id"]]["pdf_bytes"]
    new = c.post(f"/api/property-valuations/{old['id']}/new-version").json()
    assert _approve(c, new).status_code == 200
    assert _generate(c, new).status_code == 201
    assert db.pdfs[new["id"]]["valuation_version"] == 2 and db.pdfs[new["id"]]["pdf_bytes"] != old_bytes
    assert _download(c, old).content == old_bytes
    listing = c.get("/api/property-valuations", params={"property_id": P_ASSIGNED}).json()["items"]
    by_version = {i["version"]: i for i in listing}
    assert by_version[1]["status"] == "superseded" and by_version[1]["pdf"]["sha256"] == db.pdfs[old["id"]]["sha256"]
    assert by_version[1]["can_generate_pdf"] is False and by_version[2]["pdf"] is not None


def test_e_listing_offers_generation_only_where_it_is_allowed(api, db, identities):
    v = _approved(api, db, property_id=P_UNASSIGNED)
    item = api(OWNER).get(f"/api/property-valuations/{v['id']}").json()
    assert item["pdf_available"] is True and item["pdf"] is None and item["can_generate_pdf"] is True
    assert api(AGENT).get(f"/api/property-valuations/{v['id']}").json()["can_generate_pdf"] is False


def test_e_a_tampered_stored_document_is_not_served(api, db, identities):
    v = _approved(api, db)
    _generate(api(OWNER), v)
    db.pdfs[v["id"]]["pdf_bytes"] += b"%"
    r = _download(api(OWNER), v)
    assert r.status_code == 500 and r.json()["detail"]["code"] == "PDF_INTEGRITY"


def test_e_download_before_generation_is_404(api, db, identities):
    v = _approved(api, db)
    r = _download(api(OWNER), v)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "PDF_NOT_FOUND"


def test_e_rendering_is_deterministic():
    valuation = {"version": 3, "status": "approved", "approved_at": "2026-10-28T11:00:00+00:00",
                 "content": {**BASE, "sale": SALE}, "property_snapshot": {"city": "Alba Adriatica"}}
    identity = _identity(AGENCY_A, "Agenzia Alba")
    args = dict(approver_name="Giulia Rossi", generated_at="2026-10-29T09:00:00+00:00")
    assert (pdf_render.render_valuation_pdf(valuation, identity, **args)
            == pdf_render.render_valuation_pdf(valuation, identity, **args))


# ---------------------------------------------------------------------------
# F - doppia generazione
# ---------------------------------------------------------------------------

def test_f_two_sequential_generations_keep_one_artifact(api, db, identities):
    v = _approved(api, db)
    first, second = _generate(api(OWNER), v), _generate(api(ADMIN), v)
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["pdf"]["sha256"] == second.json()["pdf"]["sha256"]
    assert len(db.pdfs) == 1 and db.pdfs[v["id"]]["generated_by_user_id"] == OWNER_ID


def test_f_a_concurrent_generation_returns_the_artifact_saved_first(api, db, identities):
    v = _approved(api, db)
    winner = {"valuation_id": v["id"], "agency_id": AGENCY_A, "property_id": P_ASSIGNED, "valuation_version": 1,
              "template_version": pdf_render.TEMPLATE_VERSION, "header_snapshot": {"agency_name": "Agenzia Alba"},
              "pdf_bytes": b"%PDF-1.4 vincitore", "sha256": hashlib.sha256(b"%PDF-1.4 vincitore").hexdigest(),
              "generated_by_user_id": 502, "generated_at": "2026-10-29T08:59:59+00:00"}

    def _other_request_commits_first(state):
        state["pdfs"][v["id"]] = dict(winner)          # l'altra transazione e' gia' committata

    db.on_pdf_insert = _other_request_commits_first
    r = _generate(api(OWNER), v)
    assert r.status_code == 200 and r.json()["created"] is False
    assert r.json()["pdf"]["sha256"] == winner["sha256"]
    assert db.pdfs[v["id"]]["pdf_bytes"] == winner["pdf_bytes"]
    assert _download(api(OWNER), v).content == winner["pdf_bytes"]


def test_f_state_and_permissions_are_rechecked_under_lock_at_save(api, db, identities, monkeypatch):
    v = _approved(api, db)
    original = pdf_render.render_valuation_pdf

    def _render_then_supersede(*args, **kwargs):
        data = original(*args, **kwargs)
        db.valuations[v["id"]]["status"] = "superseded"   # approvata una nuova versione nel frattempo
        return data

    monkeypatch.setattr(pdf_service, "render_valuation_pdf", _render_then_supersede)
    r = _generate(api(OWNER), v)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PDF_SUPERSEDED"
    assert db.pdfs == {}
    shares = [k for k, _ in db.locks if k.endswith("_share")]
    assert shares == ["property_share", "valuation_share"]


def test_f_a_failing_render_saves_nothing(api, db, identities, monkeypatch):
    v = _approved(api, db)

    def _boom(*args, **kwargs):
        raise RuntimeError("impaginazione fallita")

    monkeypatch.setattr(pdf_service, "render_valuation_pdf", _boom)
    with pytest.raises(RuntimeError):
        _generate(api(OWNER), v)
    assert db.pdfs == {} and db.opened_for_write == 2   # solo creazione e approvazione


def test_f_the_trigger_refuses_a_non_approved_insert(api, db, identities):
    v = _create(api(OWNER), **BASE, sale=SALE).json()
    from tests.test_blocco_2a_property_valuations import FakeCursor
    from property_valuations import repository
    from psycopg2.extras import Json  # noqa: F401 - usata dal repository
    cur = FakeCursor(db, {"properties": db.properties, "valuations": db.valuations, "pdfs": db.pdfs})
    with pytest.raises(RuntimeError, match="trigger 117"):
        repository.insert_pdf(cur, valuation_id=v["id"], agency_id=AGENCY_A, property_id=P_ASSIGNED,
                              valuation_version=1, template_version="t", header_snapshot={}, pdf_bytes=b"%PDF",
                              sha256="0" * 64, user_id=OWNER_ID, generated_at="2026-10-29T09:00:00+00:00")


# ---------------------------------------------------------------------------
# G - importi, testi lunghi, caratteri speciali
# ---------------------------------------------------------------------------

def test_g_italian_amounts_with_unit_and_period():
    assert pdf_render.euro("195000.5") == "195.000,50 €"
    assert pdf_render.euro("1234567") == "1.234.567,00 €"
    assert pdf_render.euro("780", "mese") == "780,00 €/mese"
    assert pdf_render.euro(None) is None


def test_g_long_texts_and_many_comparables_span_several_pages(api, db, identities, capture):
    long_text = ("Valutazione argomentata con riferimenti puntuali al contesto, allo stato manutentivo "
                 "e alle dinamiche di zona. ") * 35
    comparables = [{**COMPARABLE, "zone": f"Zona {i}", "differences": "Differenze descritte. " * 8}
                   for i in range(30)]
    v = _approved(api, db, {**BASE, "sale": SALE, "rent": RENT, "rationale": long_text[:4000],
                            "sources": long_text[:1000], "comparables": comparables})
    assert _generate(api(OWNER), v).status_code == 201
    pdf = db.pdfs[v["id"]]["pdf_bytes"]
    pages = _pages(pdf)
    assert pages >= 3
    assert f"Pagina {pages} di {pages}" in capture["text"]()
    assert "Zona 29" in capture["text"]()


def test_g_user_text_is_escaped_and_special_characters_survive(api, db, identities, capture):
    tricky = "Cucina <b>abitabile</b> & terrazzo «vista mare» — perché sì: è più caro di 1.000 € l'anno"
    v = _approved(api, db, {**BASE, "sale": SALE, "rationale": tricky})
    r = _generate(api(OWNER), v)
    assert r.status_code == 201, r.text
    text = capture["text"]()
    assert "Cucina <b>abitabile</b> & terrazzo «vista mare» — perché sì: è più caro di 1.000 € l'anno" in text


def test_g_text_helpers_escape_markup_and_replace_unsupported_glyphs():
    # Chiamati anche prima di ogni impaginazione: i font si registrano da soli.
    assert pdf_render.clean_text("<a href='x'>&</a>\nriga") == "&lt;a href='x'&gt;&amp;&lt;/a&gt;<br/>riga"
    assert pdf_render.plain_text("emoji \U0001F600 è € «»") == "emoji ? è € «»"


# ---------------------------------------------------------------------------
# H - migrazione 117
# ---------------------------------------------------------------------------

def test_h_without_migration_117_pdf_routes_answer_503_and_valuations_still_work(api, db, identities):
    v = _approved(api, db)
    db.pdfs_installed = False
    for call in (_generate, _download):
        r = call(api(OWNER), v)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "PDF_UNAVAILABLE"
    item = api(OWNER).get(f"/api/property-valuations/{v['id']}").json()
    assert item["pdf_available"] is False and item["can_generate_pdf"] is False and item["pdf"] is None


def test_h_migration_117_is_valid_additive_and_its_down_keeps_documents():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA BLOCCO-2B
    # SENTINELLA AGGIORNATA DA BLOCCO-4A: la 118 (ricezione WhatsApp) segue la 117.
    m117 = next(m for m in tutte if m.version == "117_property_valuation_pdfs")
    # SENTINELLA AGGIORNATA DA BLOCCO-4D/5A: dopo la 118 vengono la 119 (simulazione delle
    # risposte automatiche) e la 120 (prenotazione delle visite); la catena qui si legge fino alla 118.
    tutte = [m for m in tutte if m.number <= 118]
    assert tutte[-1].version == "118_whatsapp_inbound"
    assert m117.down_available and runner.validate_migration(m117) == []
    up = (ROOT / "migrations" / "117_property_valuation_pdfs.sql").read_text(encoding="utf-8")
    executable = "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--"))
    assert "CREATE TABLE property_valuation_pdfs" in executable
    assert not re.search(r"\b(ALTER TABLE|DROP|DELETE FROM|UPDATE \w+ SET)\b", executable)
    assert "BEGIN;" not in executable and "COMMIT;" not in executable
    assert "status <> 'approved'" in executable or "status = 'approved'" in executable
    down = (ROOT / "migrations" / "117_property_valuation_pdfs_down.sql").read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in down and "LOCK TABLE property_valuation_pdfs" in down
    assert "DELETE" not in down and "TRUNCATE" not in down


# ---------------------------------------------------------------------------
# I - interfaccia
# ---------------------------------------------------------------------------

def test_i_valuations_section_offers_generate_and_download():
    js = (APP / "components" / "property-valuations.js").read_text(encoding="utf-8")
    for needle in ("Genera PDF", "Scarica PDF", "can_generate_pdf", "/pdf", "Documento storico"):
        assert needle in js, needle
    assert "innerHTML" not in js
    assert "credentials: 'include'" in js and "sessionExpired()" in js
    assert "mailto:" not in js and "wa.me" not in js and "whatsapp" not in js.lower()


def test_i_no_new_main_menu_entry():
    shell = (APP / "app.js").read_text(encoding="utf-8") if (APP / "app.js").exists() else ""
    assert "Genera PDF" not in shell and "valuation_pdf" not in shell
