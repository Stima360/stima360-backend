"""BLOCCO-1B - dati dell'agenzia per i report e logo.

Offline. `agency_profile.service.core_cursor` e' sostituito da un database in
memoria che interpreta le sole statement del repository REALE e rispetta le
transazioni: una eccezione dentro il blocco annulla tutto, come in
PostgreSQL. Le route sono quelle vere, montate su un'app FastAPI con il
contesto dell'operatore iniettato al posto della sessione.

Coverage map:

    A  salvataggio e rilettura; le altre impostazioni restano
    B  agenzie senza dati: nessun errore, nessun dato inventato
    C  permessi: owner/admin/Super Admin in acting si', agent no, scope lato server
    D  isolamento tra due agenzie
    E  logo: caricamento, sostituzione, rimozione; si salvano i byte normalizzati
    F  logo: file non decodificabili o troppo grandi; il vecchio resta
    G  validazione dei campi (lunghezze, email, URL)
    H  il punto di lettura condiviso per i report
    I  Platform: la scrittura della configurazione blocca la stessa riga
    J  normalizzazione: colore, trasparenza, orientamento EXIF, metadati, limiti
    K  mount, migrazione 115 e interfaccia
    L  decodifica completa, multiframe, difetti tollerati da Pillow, nessuna scrittura
    Q  migrazione 115 non applicata
"""
from __future__ import annotations

import base64
import copy
import io
import re
import struct
import zlib
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agency_profile import router as profile_router
from agency_profile import service
from agency_profile.enums import LOGO_MAX_BYTES, SETTINGS_KEY
from agency_profile.image_validation import InvalidLogo, normalize_logo
from operator_auth.context import OperatorContext

ROOT = Path(__file__).resolve().parents[1]

AGENCY_A = 71
AGENCY_B = 72
LEGACY_SETTINGS = {"timezone": "Europe/Rome", "locale": "it-IT", "legacy_x": 123}


# ---------------------------------------------------------------------------
# immagini sintetiche
# ---------------------------------------------------------------------------

def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def make_png(width=3, height=2, colour=6, depth=8, raw=None, ihdr_size=None) -> bytes:
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    row = 1 + (width * channels * depth + 7) // 8
    if raw is None:
        raw = b"".join(b"\x00" + bytes(range(row - 1)) for _ in range(height))
    w, h = ihdr_size or (width, height)
    ihdr = struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


# Un JPEG baseline reale, 64x24, prodotto da Chromium (canvas.toDataURL).
JPEG_64x24 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdC"
    "IFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAA"
    "AADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlk"
    "ZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAA"
    "ABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAA"
    "AAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAA"
    "AABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEA"
    "AAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAA"
    "ACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAYEBQYFBAYGBQYHBwYI"
    "ChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcH"
    "BwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgo"
    "KCgoKCj/wAARCAAYAEADASIAAhEBAxEB/8QAFwABAQEBAAAAAAAAAAAAAAAAAAgGB//EABoQAAEF"
    "AQAAAAAAAAAAAAAAAAADBhdVpNL/xAAZAQACAwEAAAAAAAAAAAAAAAAABQIDBAb/xAArEQAABAMF"
    "BwUAAAAAAAAAAAAAAQIDBBESBRNBUVIUFRYxMpHRU2GBkvD/2gAMAwEAAhEDEQA/AOfG6iV7UutD"
    "swpdR3ts2k7AUXREdU+c8Je5ZhHBwyX6qsBJ8Sval1odiJXtS60OysAI+JYrSnsfkbd3NZn++BJ8"
    "Sval1odiJXtS60OysAHEsVpT2PyDdzWZ/vgSfEr2pdaHZhS6iFR5Y1pOx9d6RFTLlPGfueQxRkMl"
    "imnEDdS09rrIhwANnYdp6V6klSzIjGRLikdJyCWntdZEOBLT2usiHABTsEL6SfqXgTv3dR9wlp7X"
    "WRDgS09rrIhwAGwQvpJ+peAX7uo+4S09rrIhwYUAuah2mZ3SSTPIiIQU4pfUcx//2Q=="
)

PNG_OK = make_png()
REAL_PNG = (ROOT / "static" / "os_shell" / "assets" / "brand" / "stima360-icon.png").read_bytes()


# ---------------------------------------------------------------------------
# database in memoria, transazionale
# ---------------------------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.agencies = {
            AGENCY_A: {"id": AGENCY_A, "name": "Agenzia Alfa Srl", "settings": copy.deepcopy(LEGACY_SETTINGS)},
            AGENCY_B: {"id": AGENCY_B, "name": "Agenzia Beta", "settings": {}},
        }
        self.logos = {}
        self.locks = []
        self.fail_on = None          # prefisso SQL che fa fallire la statement
        self.logos_installed = True  # False = migrazione 115 non applicata
        self.statements = []
        self.opened = 0
        self.opened_for_write = 0


class FakeCursor:
    def __init__(self, db, state):
        self.db, self.s = db, state
        self._row, self.rowcount = None, 0

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.db.statements.append(q)
        if self.db.fail_on and q.startswith(self.db.fail_on):
            raise RuntimeError("database non disponibile")
        if q.startswith("SELECT to_regclass('agency_logos')"):
            self._row = {"installed": self.db.logos_installed}
        elif q.startswith("SELECT id, name, settings FROM agencies WHERE id = %s"):
            if q.endswith("FOR UPDATE"):
                self.db.locks.append(params[0])
            row = self.s["agencies"].get(params[0])
            self._row = copy.deepcopy(row) if row else None
        elif q.startswith("UPDATE agencies SET settings = %s"):
            settings, agency_id = params
            self.s["agencies"][agency_id]["settings"] = copy.deepcopy(getattr(settings, "adapted", settings))
        elif "agency_logos" in q and not self.db.logos_installed:
            raise RuntimeError('relation "agency_logos" does not exist')
        elif q.startswith("SELECT content_type") and "FROM agency_logos" in q:
            row = self.s["logos"].get(params[0])
            if row:
                out = {k: v for k, v in row.items() if k != "image_bytes"}
                out["size_bytes"] = len(row["image_bytes"])
                if "image_bytes FROM" in q:
                    out["image_bytes"] = row["image_bytes"]
                self._row = out
            else:
                self._row = None
        elif q.startswith("INSERT INTO agency_logos"):
            agency_id, ct, w, h, data, sha, by = params
            self.s["logos"][agency_id] = {"content_type": ct, "width": w, "height": h, "image_bytes": bytes(data),
                                          "sha256": sha, "uploaded_by": by, "uploaded_at": "2026-10-28T10:00:00+00:00"}
            row = self.s["logos"][agency_id]
            self._row = {**{k: v for k, v in row.items() if k != "image_bytes"}, "size_bytes": len(data)}
        elif q.startswith("DELETE FROM agency_logos"):
            self.rowcount = 1 if self.s["logos"].pop(params[0], None) else 0
        else:
            raise AssertionError(f"statement inattesa: {q}")

    def fetchone(self):
        return self._row


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()

    @contextmanager
    def _core_cursor(*, commit=False):
        fake.opened += 1
        fake.opened_for_write += bool(commit)
        state = {"agencies": copy.deepcopy(fake.agencies), "logos": copy.deepcopy(fake.logos)}
        yield None, FakeCursor(fake, state)
        if commit:                       # come PostgreSQL: solo un blocco riuscito resta
            fake.agencies, fake.logos = state["agencies"], state["logos"]

    monkeypatch.setattr(service, "core_cursor", _core_cursor)
    return fake


def ctx(role="agency_owner", agency_id=AGENCY_A, user_id=501, platform=False):
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=platform,
                           session_id=9, auth_channel="operator_session")


OWNER, ADMIN, AGENT = ctx(), ctx("agency_admin", user_id=502), ctx("agent", user_id=503)
ACTING_SUPER_ADMIN = ctx(role=None, user_id=1, platform=True)
UNBOUND_SUPER_ADMIN = ctx(role=None, agency_id=None, user_id=1, platform=True)
OWNER_B = ctx(agency_id=AGENCY_B, user_id=601)


@pytest.fixture
def client_for(db):
    def _make(context):
        app = FastAPI()
        app.include_router(profile_router.router)
        app.dependency_overrides[profile_router.require_operator] = lambda: context
        return TestClient(app)
    return _make


FULL = {
    "display_name": "Alfa Immobiliare",
    "legal_name": "Agenzia Alfa Srl",
    "vat_number": "01234567890",
    "tax_code": "01234567890",
    "legal_address": "Via Roma 1, 64011 Alba Adriatica (TE)",
    "phone": "+39 0861 000000",
    "email": "info@alfa.example",
    "website": "https://alfa.example",
}


# ---------------------------------------------------------------------------
# A - salvataggio e rilettura
# ---------------------------------------------------------------------------

def test_a_save_and_read_back_all_fields(client_for, db):
    c = client_for(OWNER)
    saved = c.put("/api/agency-profile", json=FULL)
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"] == FULL
    again = c.get("/api/agency-profile").json()
    assert again["configured"] is True and again["profile"] == FULL and again["can_edit"] is True
    assert db.agencies[AGENCY_A]["settings"][SETTINGS_KEY]["updated_by"] == "operator:501"


def test_a_other_settings_are_preserved(client_for, db):
    client_for(OWNER).put("/api/agency-profile", json={"display_name": "Alfa"})
    settings = db.agencies[AGENCY_A]["settings"]
    for key, value in LEGACY_SETTINGS.items():
        assert settings[key] == value
    assert settings[SETTINGS_KEY]["display_name"] == "Alfa"


def test_a_write_locks_the_agency_row(client_for, db):
    client_for(OWNER).put("/api/agency-profile", json={"display_name": "Alfa"})
    assert db.locks == [AGENCY_A]


def test_a_cleared_optional_fields_are_absent_not_invented(client_for, db):
    c = client_for(OWNER)
    c.put("/api/agency-profile", json=FULL)
    r = c.put("/api/agency-profile", json={"display_name": "Alfa", "phone": "  ", "email": None})
    assert r.status_code == 200
    stored = db.agencies[AGENCY_A]["settings"][SETTINGS_KEY]
    assert "phone" not in stored and "email" not in stored and "legal_name" not in stored
    assert r.json()["profile"]["phone"] is None


# ---------------------------------------------------------------------------
# B - agenzie senza dati
# ---------------------------------------------------------------------------

def test_b_unconfigured_agency_reads_without_error(client_for):
    body = client_for(OWNER_B).get("/api/agency-profile").json()
    assert body["configured"] is False and body["profile"] == {} and body["logo"] is None
    assert body["agency_name"] == "Agenzia Beta"


def test_b_corrupted_stored_profile_is_a_constant_500(client_for, db):
    db.agencies[AGENCY_A]["settings"][SETTINGS_KEY] = {"display_name": "", "nonsense": 1}
    r = client_for(OWNER).get("/api/agency-profile")
    assert r.status_code == 500 and "nonsense" not in r.text


# ---------------------------------------------------------------------------
# C - permessi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("context", [OWNER, ADMIN, ACTING_SUPER_ADMIN], ids=["owner", "admin", "acting"])
def test_c_editors_may_save(client_for, context):
    assert client_for(context).put("/api/agency-profile", json={"display_name": "Alfa"}).status_code == 200


def test_c_agent_reads_but_cannot_write(client_for, db):
    c = client_for(AGENT)
    body = c.get("/api/agency-profile").json()
    assert body["can_edit"] is False
    before = copy.deepcopy(db.agencies)
    assert c.put("/api/agency-profile", json={"display_name": "Hack"}).status_code == 403
    assert c.put("/api/agency-profile/logo", content=PNG_OK).status_code == 403
    assert c.delete("/api/agency-profile/logo").status_code == 403
    assert db.agencies == before and db.logos == {}
    # rifiutato prima di aprire qualunque transazione di scrittura
    assert not any(s.startswith(("UPDATE", "INSERT", "DELETE")) for s in db.statements)


def test_c_unknown_role_grants_nothing(client_for):
    assert client_for(ctx(role="platform_admin")).put("/api/agency-profile", json={"display_name": "x"}).status_code == 403


def test_c_unbound_super_admin_is_refused(client_for, db):
    c = client_for(UNBOUND_SUPER_ADMIN)
    assert c.get("/api/agency-profile").status_code == 403
    assert c.put("/api/agency-profile", json={"display_name": "x"}).status_code == 403
    assert db.opened == 0


def test_c_agency_id_from_the_browser_is_refused_or_ignored(client_for, db):
    c = client_for(OWNER)
    r = c.put("/api/agency-profile", json={"display_name": "x", "agency_id": AGENCY_B})
    assert r.status_code == 422
    assert c.put(f"/api/agency-profile?agency_id={AGENCY_B}", json={"display_name": "Alfa"}).status_code == 200
    assert SETTINGS_KEY not in db.agencies[AGENCY_B]["settings"]
    assert db.agencies[AGENCY_A]["settings"][SETTINGS_KEY]["display_name"] == "Alfa"


def test_c_read_only_consultation_blocks_writes_on_this_api():
    """ADM-5B: il blocco in sola lettura vale anche qui (nessuna esenzione)."""
    from fastapi import HTTPException

    from operator_auth.dependencies import READ_ONLY_EXEMPT_PREFIXES, refuse_writes_in_read_only

    assert not "/api/agency-profile".startswith(READ_ONLY_EXEMPT_PREFIXES)
    read_only = OperatorContext(user_id=1, agency_id=AGENCY_A, role=None, is_platform_admin=True,
                                session_id=9, auth_channel="operator_session", read_only=True)

    class _Req:
        method = "PUT"

        class url:
            path = "/api/agency-profile/logo"

    with pytest.raises(HTTPException) as exc:
        refuse_writes_in_read_only(_Req(), read_only)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# D - isolamento tra due agenzie
# ---------------------------------------------------------------------------

def test_d_two_agencies_never_see_each_other(client_for, db):
    a, b = client_for(OWNER), client_for(OWNER_B)
    a.put("/api/agency-profile", json={"display_name": "Alfa"})
    a.put("/api/agency-profile/logo", content=PNG_OK)
    b.put("/api/agency-profile", json={"display_name": "Beta"})

    assert b.get("/api/agency-profile").json()["profile"]["display_name"] == "Beta"
    assert b.get("/api/agency-profile").json()["logo"] is None
    assert b.get("/api/agency-profile/logo").status_code == 404        # nessun logo di ripiego
    assert a.get("/api/agency-profile").json()["profile"]["display_name"] == "Alfa"
    assert b.delete("/api/agency-profile/logo").status_code == 404
    assert AGENCY_A in db.logos


# ---------------------------------------------------------------------------
# E - logo: caricamento, sostituzione, rimozione (byte NORMALIZZATI)
# ---------------------------------------------------------------------------

def _stored(db, agency=AGENCY_A) -> bytes:
    return db.logos[agency]["image_bytes"]


def _open(data):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_e_upload_replace_read_and_remove(client_for, db):
    c = client_for(OWNER)
    first = c.put("/api/agency-profile/logo", content=PNG_OK, headers={"Content-Type": "image/png"})
    assert first.status_code == 200, first.text
    assert first.json()["content_type"] == "image/png" and (first.json()["width"], first.json()["height"]) == (3, 2)

    got = c.get("/api/agency-profile/logo")
    assert got.content == _stored(db) and got.headers["content-type"] == "image/png"
    assert _open(got.content).size == (3, 2)
    assert got.headers["x-content-type-options"] == "nosniff" and "no-store" in got.headers["cache-control"]

    second = c.put("/api/agency-profile/logo", content=JPEG_64x24)
    assert second.status_code == 200 and second.json()["content_type"] == "image/jpeg"
    image = _open(c.get("/api/agency-profile/logo").content)
    assert image.format == "JPEG" and image.size == (64, 24)
    assert second.json()["url"] != first.json()["url"]
    assert db.logos[AGENCY_A]["uploaded_by"] == "operator:501"

    assert c.delete("/api/agency-profile/logo").status_code == 204
    assert c.get("/api/agency-profile/logo").status_code == 404
    assert c.get("/api/agency-profile").json()["logo"] is None


def test_e_the_declared_mime_is_not_trusted(client_for):
    """Un PNG dichiarato come JPEG resta un PNG: conta il contenuto."""
    r = client_for(OWNER).put("/api/agency-profile/logo", content=REAL_PNG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 200 and r.json()["content_type"] == "image/png"


def test_e_the_original_file_is_never_stored_and_the_fingerprint_matches_the_stored_bytes(client_for, db):
    import hashlib

    with_metadata = _pillow_bytes("RGB", "JPEG", exif=_exif(orientation=1, comment="segreto"))
    body = client_for(OWNER).put("/api/agency-profile/logo", content=with_metadata).json()
    stored = _stored(db)
    assert stored != with_metadata
    assert body["sha256"] == hashlib.sha256(stored).hexdigest() == db.logos[AGENCY_A]["sha256"]
    assert body["size_bytes"] == len(stored)
    assert b"segreto" not in stored and not _open(stored).getexif()


# ---------------------------------------------------------------------------
# F - file rifiutati; il vecchio logo resta
# ---------------------------------------------------------------------------

BAD_FILES = {
    "gif": b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,"
           b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
    "svg": b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    "text_named_png": b"non sono un'immagine",
    "png_cut_in_the_pixel_data": (lambda d: d[:len(d) // 2])(make_png(64, 64)),
    "png_signature_only": PNG_OK[:8],
    "jpeg_truncated": JPEG_64x24[:400],
    "jpeg_header_only": JPEG_64x24[:JPEG_64x24.index(b"\xff\xda")],
}


@pytest.mark.parametrize("name", sorted(BAD_FILES))
def test_f_undecodable_files_are_refused_and_the_old_logo_stays(client_for, db, name):
    c = client_for(OWNER)
    c.put("/api/agency-profile/logo", content=PNG_OK)
    before = _stored(db)
    writes = db.opened_for_write
    r = c.put("/api/agency-profile/logo", content=BAD_FILES[name])
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] in ("unsupported", "corrupted")
    assert _stored(db) == before and db.opened_for_write == writes      # nessuna scrittura tentata


def test_f_too_large_is_413_and_the_old_logo_stays(client_for, db):
    c = client_for(OWNER)
    c.put("/api/agency-profile/logo", content=PNG_OK)
    before = _stored(db)
    r = c.put("/api/agency-profile/logo", content=PNG_OK + b"\x00" * LOGO_MAX_BYTES)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"
    assert _stored(db) == before


def test_f_empty_body_is_refused(client_for):
    r = client_for(OWNER).put("/api/agency-profile/logo", content=b"")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "empty"


def test_f_huge_declared_dimensions_are_refused_before_decoding(client_for, db):
    huge = make_png(ihdr_size=(20000, 20000))
    r = client_for(OWNER).put("/api/agency-profile/logo", content=huge)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "dimensions"
    assert db.logos == {}


def test_f_a_database_failure_during_replacement_keeps_the_old_logo(client_for, db):
    c = client_for(OWNER)
    c.put("/api/agency-profile/logo", content=PNG_OK)
    before = _stored(db)
    db.fail_on = "INSERT INTO agency_logos"
    with pytest.raises(RuntimeError):
        c.put("/api/agency-profile/logo", content=JPEG_64x24)
    db.fail_on = None
    assert _stored(db) == before


# ---------------------------------------------------------------------------
# G - validazione dei campi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body,field", [
    ({}, "display_name"),
    ({"display_name": "   "}, "display_name"),
    ({"display_name": "x" * 121}, "display_name"),
    ({"display_name": "Alfa", "legal_name": "y" * 201}, "legal_name"),
    ({"display_name": "Alfa", "email": "non-una-email"}, "email"),
    ({"display_name": "Alfa", "email": "a@b"}, "email"),
    ({"display_name": "Alfa", "website": "javascript:alert(1)"}, "website"),
    ({"display_name": "Alfa", "website": "ftp://alfa.example"}, "website"),
    ({"display_name": "Alfa", "website": "https://utente:pw@alfa.example"}, "website"),
    ({"display_name": "Alfa", "legal_address": "riga\nseconda"}, "legal_address"),
])
def test_g_invalid_fields_are_422_and_nothing_is_written(client_for, db, body, field):
    r = client_for(OWNER).put("/api/agency-profile", json=body)
    assert r.status_code == 422
    assert field in r.text
    assert SETTINGS_KEY not in db.agencies[AGENCY_A]["settings"]


def test_g_markup_is_stored_as_text_not_interpreted(client_for, db):
    r = client_for(OWNER).put("/api/agency-profile", json={"display_name": "<img src=x onerror=alert(1)>"})
    assert r.status_code == 200
    assert r.json()["profile"]["display_name"] == "<img src=x onerror=alert(1)>"


# ---------------------------------------------------------------------------
# H - il punto di lettura condiviso
# ---------------------------------------------------------------------------

def test_h_report_identity_without_configuration_uses_the_agency_name(db):
    identity = service.report_identity(OWNER_B)
    assert identity["header_name"] == "Agenzia Beta" and identity["display_name_configured"] is False
    assert identity["logo"] is None and identity["legal_name"] is None and identity["vat_number"] is None


def test_h_report_identity_returns_profile_and_logo_of_the_context_agency(client_for, db):
    client_for(OWNER).put("/api/agency-profile", json=FULL)
    client_for(OWNER).put("/api/agency-profile/logo", content=JPEG_64x24)
    identity = service.report_identity(AGENT)                 # ogni operatore dell'agenzia
    assert identity["header_name"] == "Alfa Immobiliare" and identity["vat_number"] == FULL["vat_number"]
    assert identity["logo"]["data"] == _stored(db) and identity["logo"]["content_type"] == "image/jpeg"
    assert service.report_identity(OWNER_B)["logo"] is None


# ---------------------------------------------------------------------------
# I - Platform: la configurazione blocca la riga
# ---------------------------------------------------------------------------

def test_i_platform_configuration_update_locks_the_agency_row():
    source = (ROOT / "platform_admin" / "configuration_service.py").read_text(encoding="utf-8")
    update = source[source.index("def update_configuration"):]
    assert "agencies_repository.lock_agency(cur, agency_id)" in update
    assert "agencies_repository.get_agency(cur, agency_id)" not in update


def test_i_platform_projection_ignores_and_merge_keeps_the_profile():
    from platform_admin.configuration_service import _projected

    stored = {**LEGACY_SETTINGS, SETTINGS_KEY: {"display_name": "Alfa"}}
    assert _projected(stored) == {"timezone": "Europe/Rome", "locale": "it-IT"}
    assert {**stored, "timezone": "Europe/Rome"}[SETTINGS_KEY] == {"display_name": "Alfa"}


# ---------------------------------------------------------------------------
# J - normalizzazione: formati, colore, trasparenza, orientamento, limiti
# ---------------------------------------------------------------------------

def _pillow_bytes(mode, fmt, size=(33, 17), **save):
    import random

    from PIL import Image

    image = Image.frombytes("RGBA", size, random.Random(99).randbytes(size[0] * size[1] * 4)).convert(mode)
    out = io.BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def _exif(orientation, comment=None):
    from PIL import Image

    exif = Image.Exif()
    exif[0x0112] = orientation
    if comment:
        exif[0x010E] = comment                  # ImageDescription
    return exif.tobytes()


def test_j_real_files_are_recognised_and_normalized():
    png = normalize_logo(REAL_PNG)
    assert png.content_type == "image/png" and _open(png.data).format == "PNG"
    jpeg = normalize_logo(JPEG_64x24)
    assert (jpeg.content_type, jpeg.width, jpeg.height) == ("image/jpeg", 64, 24)
    assert _open(jpeg.data).format == "JPEG"
    # `stimacentrato.jpg` e' un PNG con estensione .jpg: lo dice il contenuto
    assert normalize_logo((ROOT / "stimacentrato.jpg").read_bytes()).content_type == "image/png"


@pytest.mark.parametrize("mode,expected", [("RGBA", "RGBA"), ("LA", "LA"), ("RGB", "RGB"), ("L", "L"), ("1", "1")])
def test_j_png_modes_keep_their_colour_and_alpha(mode, expected):
    out = _open(normalize_logo(_pillow_bytes(mode, "PNG")).data)
    assert out.format == "PNG" and out.mode == expected and out.size == (33, 17)


def test_j_png_transparency_is_kept_pixel_by_pixel():
    from PIL import Image

    source = Image.new("RGBA", (6, 4), (10, 20, 30, 0))
    source.putpixel((2, 1), (200, 100, 50, 128))
    raw = io.BytesIO()
    source.save(raw, "PNG")
    out = _open(normalize_logo(raw.getvalue()).data)
    assert out.mode == "RGBA" and list(out.getdata()) == list(source.getdata())


def test_j_palette_png_with_transparency_becomes_explicit_alpha():
    raw = _pillow_bytes("P", "PNG", transparency=0)
    out = _open(normalize_logo(raw).data)
    assert out.mode == "RGBA" and out.getextrema()[3][0] == 0          # la trasparenza c'e' ancora


def test_j_sixteen_bit_png_is_scaled_not_clipped():
    from PIL import Image

    raw = io.BytesIO()
    Image.new("I;16", (4, 4), 40000).save(raw, "PNG")
    out = _open(normalize_logo(raw.getvalue()).data)
    assert out.mode == "L" and out.getpixel((0, 0)) == 40000 // 256


@pytest.mark.parametrize("mode,expected", [("RGB", "RGB"), ("L", "L"), ("CMYK", "RGB")])
def test_j_jpeg_colour_conversions_are_explicit(mode, expected):
    out = _open(normalize_logo(_pillow_bytes(mode, "JPEG")).data)
    assert out.format == "JPEG" and out.mode == expected


@pytest.mark.parametrize("orientation,size", [(1, (40, 10)), (6, (10, 40)), (8, (10, 40)), (3, (40, 10))])
def test_j_exif_orientation_is_applied_and_removed(orientation, size):
    """Larghezza 40 e altezza 10 nel file; orientamento 6/8 = ruotato di 90 gradi."""
    logo = normalize_logo(_pillow_bytes("RGB", "JPEG", size=(40, 10), exif=_exif(orientation)))
    out = _open(logo.data)
    assert (logo.width, logo.height) == out.size == size
    assert 0x0112 not in out.getexif()                                    # niente da riapplicare


def test_j_proportions_are_kept():
    for size in ((300, 75), (75, 300), (1, 1), (4096, 1)):
        logo = normalize_logo(_pillow_bytes("RGBA", "PNG", size=size))
        assert (logo.width, logo.height) == size


def test_j_metadata_is_not_carried_over():
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "dato riservato")
    raw = io.BytesIO()
    Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(raw, "PNG", pnginfo=info, dpi=(300, 300))
    out_bytes = normalize_logo(raw.getvalue()).data
    assert b"dato riservato" not in out_bytes and b"tEXt" not in out_bytes and b"pHYs" not in out_bytes
    jpeg = normalize_logo(_pillow_bytes("RGB", "JPEG", exif=_exif(1, "dato riservato"), icc_profile=b"x" * 64)).data
    assert b"dato riservato" not in jpeg and "icc_profile" not in _open(jpeg).info


@pytest.mark.parametrize("size", [(4097, 1), (1, 4097), (4097, 4097)])
def test_j_dimensions_over_the_limit_are_refused(size):
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(make_png(ihdr_size=size))
    assert exc.value.code == "dimensions"


def test_j_jpeg_dimensions_over_the_limit_are_refused():
    sof = JPEG_64x24.index(b"\xff\xc0")
    huge = JPEG_64x24[:sof + 5] + struct.pack(">HH", 9000, 9000) + JPEG_64x24[sof + 9:]
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(huge)
    assert exc.value.code == "dimensions"


def test_j_produced_bytes_have_their_own_limit(monkeypatch):
    from agency_profile import image_validation

    monkeypatch.setattr(image_validation, "LOGO_MAX_STORED_BYTES", 10)
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(PNG_OK)
    assert exc.value.code == "output_too_large"


def test_j_produced_bytes_limit_matches_the_database_check():
    from agency_profile.enums import LOGO_MAX_STORED_BYTES

    up = (ROOT / "migrations" / "115_agency_logos.sql").read_text(encoding="utf-8")
    assert f"BETWEEN 1 AND {LOGO_MAX_STORED_BYTES}" in up


# ---------------------------------------------------------------------------
# K - mount, migrazione, interfaccia
# ---------------------------------------------------------------------------

def test_k_router_is_mounted_behind_the_authenticated_operator():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert ("app.include_router(agency_profile_router, dependencies=[Depends(require_authenticated_operator)])"
            in main)


def test_k_no_route_accepts_an_agency_id():
    for route in profile_router.router.routes:
        assert "agency_id" not in route.path
        assert all(p.name != "agency_id" for p in route.dependant.query_params + route.dependant.path_params)


def test_k_migration_115_is_additive_and_has_a_refusing_down():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    ultima = tutte[-1]
    assert ultima.version == "115_agency_logos"
    assert runner.validate_migration(ultima) == []
    up = (ROOT / "migrations" / "115_agency_logos.sql").read_text(encoding="utf-8")
    eseguibile = "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--"))
    assert re.findall(r"\b(CREATE|ALTER|DROP|UPDATE|DELETE|INSERT)\b", eseguibile) == ["CREATE"]
    down = (ROOT / "migrations" / "115_agency_logos_down.sql").read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in down and "DROP TABLE agency_logos" in down


def test_k_the_view_renders_text_safely():
    view = (ROOT / "static" / "os_shell" / "assets" / "views" / "agenzia-dati.js").read_text(encoding="utf-8")
    assert "innerHTML" not in view and "insertAdjacentHTML" not in view
    assert "textContent" in view
    codice = "\n".join(r for r in view.splitlines() if not r.lstrip().startswith("//"))
    assert "agency_id" not in codice
    css = (ROOT / "static" / "os_shell" / "assets" / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* BLOCCO-1B"):]
    assert "object-fit: contain" in blocco and "width: auto; height: auto" in blocco


def test_k_the_view_is_reached_from_the_user_menu_not_the_sidebar():
    main_js = (ROOT / "static" / "os_shell" / "assets" / "main.js").read_text(encoding="utf-8")
    sections = main_js[main_js.index("const SECTIONS = ["):main_js.index("];", main_js.index("const SECTIONS = ["))]
    assert "agenzia" not in sections
    assert "registerRoute('agenzia'" in main_js
    menu = (ROOT / "static" / "os_shell" / "assets" / "components" / "user-menu.js").read_text(encoding="utf-8")
    assert "'#/agenzia'" in menu and "canUseTenantSurface(session)" in menu


# ---------------------------------------------------------------------------
# L - decodifica completa, multiframe, difetti tollerati, nessuna scrittura
# ---------------------------------------------------------------------------

def _pillow_jpeg(size=(256, 128), **kw):
    import random

    from PIL import Image

    # Seme fisso: gli stessi byte a ogni esecuzione, nessun esito che dipenda dal caso.
    im = Image.frombytes("RGB", size, random.Random(1234).randbytes(size[0] * size[1] * 3))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85, **kw)
    return out.getvalue()


def _scan_start(data):
    i = data.index(b"\xff\xda")
    return i + 2 + int.from_bytes(data[i + 2:i + 4], "big")


JPEG_BASELINE = _pillow_jpeg()


JPEG_HALF = JPEG_BASELINE[:len(JPEG_BASELINE) // 2]


@pytest.mark.parametrize("cut", ["half", "quarter", "header_only"])
def test_l_truncated_jpeg_is_refused(cut):
    data = {"half": JPEG_HALF, "quarter": JPEG_BASELINE[:len(JPEG_BASELINE) // 4],
            "header_only": JPEG_BASELINE[:_scan_start(JPEG_BASELINE)]}[cut]
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(data)
    assert exc.value.code == "corrupted"


def test_l_jpeg_with_an_undecodable_huffman_table_is_refused():
    broken = JPEG_BASELINE.replace(b"\xff\xc4", b"\xff\xc4\x00\x03\x00", 1)
    with pytest.raises(InvalidLogo):
        normalize_logo(broken)


def test_l_png_with_altered_compressed_data_is_refused():
    """Dati IDAT alterati con CRC ricalcolata: lo stream zlib non si decomprime."""
    pos = PNG_OK.index(b"IDAT")
    length = int.from_bytes(PNG_OK[pos - 4:pos], "big")
    body = bytes(b ^ 0x33 for b in PNG_OK[pos + 4:pos + 4 + length])
    altered = PNG_OK[:pos - 4] + _chunk(b"IDAT", body) + PNG_OK[pos + 8 + length:]
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(altered)
    assert exc.value.code == "corrupted"


def test_l_defects_tolerated_by_pillow_produce_a_decodable_normalized_image():
    """DOCUMENTATO, NON NASCOSTO. libjpeg tratta come avvisi alcuni difetti
    (dati di scansione che finiscono prima del previsto con EOI): Pillow
    restituisce un'immagine decodificata e il logo e' quella, normalizzata.
    Il contratto e' "decodificabile e normalizzato", non "ogni alterazione
    riconosciuta"; il file originale non viene comunque salvato."""
    tolerated = JPEG_BASELINE[:_scan_start(JPEG_BASELINE) + 50] + b"\xff\xd9"
    logo = normalize_logo(tolerated)
    out = _open(logo.data)
    assert out.size == (256, 128) and logo.data != tolerated


def test_l_png_without_iend_but_with_all_pixels_is_tolerated_and_normalized():
    """DOCUMENTATO: un PNG a cui mancano IEND e la somma finale zlib, ma con tutti
    i pixel, viene decodificato da Pillow. Il logo salvato e' il PNG
    ricodificato, completo e valido, non il file troncato."""
    truncated = PNG_OK[:-20]
    logo = normalize_logo(truncated)
    assert _open(logo.data).size == (3, 2) and logo.data.endswith(b"IEND\xaeB`\x82")


def test_l_animated_and_multi_frame_images_are_refused():
    from PIL import Image

    frames = [Image.new("RGBA", (16, 16), c) for c in ((255, 0, 0, 255), (0, 255, 0, 128))]
    apng = io.BytesIO()
    frames[0].save(apng, "PNG", save_all=True, append_images=frames[1:])
    mpo = io.BytesIO()
    frames[0].convert("RGB").save(mpo, "MPO", save_all=True, append_images=[frames[1].convert("RGB")])
    for data in (apng.getvalue(), mpo.getvalue()):
        with pytest.raises(InvalidLogo) as exc:
            normalize_logo(data)
        assert exc.value.code == "animated"


@pytest.mark.parametrize("fmt", ["GIF", "WEBP", "BMP", "TIFF"])
def test_l_other_formats_are_unsupported(fmt):
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(_pillow_bytes("RGB", fmt))
    assert exc.value.code == "unsupported"


def test_l_dimensions_are_checked_before_decoding(monkeypatch):
    """Il limite si applica alle dimensioni dell'intestazione: `load()` non
    parte nemmeno."""
    from PIL import ImageFile

    def _must_not_load(self):
        raise AssertionError("decodifica avviata prima del controllo delle dimensioni")

    monkeypatch.setattr(ImageFile.ImageFile, "load", _must_not_load)
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(make_png(ihdr_size=(4097, 2)))
    assert exc.value.code == "dimensions"


def test_l_pillow_decompression_bomb_guard_is_a_refusal(monkeypatch):
    from PIL import Image

    def _bomb(*_a, **_k):
        raise Image.DecompressionBombError("troppi pixel")

    monkeypatch.setattr(Image, "open", _bomb)
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(PNG_OK)
    assert exc.value.code == "dimensions"


def test_l_decode_errors_are_refusals(monkeypatch):
    from PIL import ImageFile, PngImagePlugin

    def _broken(self):
        raise OSError("broken data stream when reading image file")

    monkeypatch.setattr(ImageFile.ImageFile, "load", _broken)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", _broken, raising=False)
    with pytest.raises(InvalidLogo) as exc:
        normalize_logo(PNG_OK)
    assert exc.value.code == "corrupted"


def test_l_truncated_mode_switched_on_elsewhere_stops_normalization(monkeypatch, client_for, db):
    from PIL import ImageFile

    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(RuntimeError):
        normalize_logo(PNG_OK)
    with pytest.raises(RuntimeError):
        client_for(OWNER).put("/api/agency-profile/logo", content=PNG_OK)
    assert db.logos == {}


def test_l_pillow_global_settings_are_not_changed():
    from PIL import Image, ImageFile

    before = (Image.MAX_IMAGE_PIXELS, ImageFile.LOAD_TRUNCATED_IMAGES)
    normalize_logo(JPEG_BASELINE)
    with pytest.raises(InvalidLogo):
        normalize_logo(JPEG_HALF)
    assert (Image.MAX_IMAGE_PIXELS, ImageFile.LOAD_TRUNCATED_IMAGES) == before


def test_l_a_refused_file_never_opens_a_write_transaction(client_for, db):
    c = client_for(OWNER)
    assert c.put("/api/agency-profile/logo", content=JPEG_64x24).status_code == 200
    before = _stored(db)
    db.statements.clear()
    writes = db.opened_for_write
    r = c.put("/api/agency-profile/logo", content=JPEG_HALF)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "corrupted"
    assert not any(s.startswith("INSERT INTO agency_logos") for s in db.statements)
    assert db.opened_for_write == writes and _stored(db) == before


# ---------------------------------------------------------------------------
# Q - migrazione 115 non ancora applicata: i dati funzionano, il logo no (503)
# ---------------------------------------------------------------------------

def test_q_without_migration_115_profile_still_reads_and_saves(client_for, db):
    db.logos_installed = False
    c = client_for(OWNER)
    body = c.get("/api/agency-profile").json()
    assert body["logo_available"] is False and body["logo"] is None
    saved = c.put("/api/agency-profile", json={"display_name": "Alfa"})
    assert saved.status_code == 200 and saved.json()["logo_available"] is False
    assert db.agencies[AGENCY_A]["settings"][SETTINGS_KEY]["display_name"] == "Alfa"
    assert not any("agency_logos" in s and "to_regclass" not in s for s in db.statements)


@pytest.mark.parametrize("method,path,kwargs", [
    ("put", "/api/agency-profile/logo", {"content": PNG_OK}),
    ("put", "/api/agency-profile/logo", {"content": b"non un'immagine"}),
    ("get", "/api/agency-profile/logo", {}),
    ("delete", "/api/agency-profile/logo", {}),
])
def test_q_without_migration_115_logo_routes_answer_503(client_for, db, method, path, kwargs):
    db.logos_installed = False
    r = getattr(client_for(OWNER), method)(path, **kwargs)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "logo_unavailable"


def test_q_without_migration_115_report_identity_has_no_logo(db):
    db.logos_installed = False
    identity = service.report_identity(OWNER)
    assert identity["logo"] is None and identity["header_name"] == "Agenzia Alfa Srl"


def test_q_with_migration_115_profile_says_logo_available(client_for):
    assert client_for(OWNER).get("/api/agency-profile").json()["logo_available"] is True
