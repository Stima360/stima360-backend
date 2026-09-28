"""A30-12 - PUBLIC BOOKING LINK: unita' pure, senza database.

Cio' che si puo' provare senza PostgreSQL: token/hash/IP privacy (D6/D8),
gli schemi Pydantic (D3/D4), l'impronta del payload (D7), gli allowlist di
privacy delle risposte pubbliche/operatore (nessun campo extra), il guard
`create_public_booking_appointment` PRIMA di aprire un cursore, la forma di
`SYSTEM_CONTEXT_ORIGINS`, e l'invarianza "nessuna riga `router.py` con
`APIRouter(prefix=...)` propria" che dodgia il glob del certificatore live.

La suite PostgreSQL reale (migrazione, concorrenza, TOCTOU, idempotenza,
disponibilita' HARD end-to-end) e' in `tests/test_a30_12_public_booking_postgres.py`.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "public_booking"


# ---------------------------------------------------------------------------
# A - security.py (D6, D7, D8)
# ---------------------------------------------------------------------------

def test_a1_generate_token_e_urlsafe_e_diverso_ogni_volta():
    from public_booking import security
    a, b = security.generate_token(), security.generate_token()
    assert a != b
    assert all(c.isalnum() or c in "-_" for c in a)
    assert len(a) >= 32  # secrets.token_urlsafe(32) produce >= 32 caratteri


def test_a2_hash_token_e_sha256_esadecimale_deterministico():
    from public_booking import security
    raw = "un-token-di-prova"
    atteso = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert security.hash_token(raw) == atteso
    assert len(security.hash_token(raw)) == 64
    assert security.hash_token(raw) == security.hash_token(raw)


def test_a3_hash_ip_senza_pepper_solleva_e_non_produce_un_default_debole():
    from public_booking import security
    with pytest.raises(security.IpPepperNotConfigured):
        security.hash_ip("1.2.3.4", pepper=None)


def test_a4_hash_ip_e_hmac_non_sha256_semplice():
    """D8: MAI SHA256(ip) semplice - lo spazio IPv4 e' reversibile per
    dizionario. Il digest deve dipendere dal pepper, non solo dall'IP."""
    from public_booking import security
    ip = "203.0.113.42"
    pepper = "un-pepper-di-prova"
    atteso = hmac.new(pepper.encode("utf-8"), ip.encode("utf-8"),
                       hashlib.sha256).hexdigest()
    assert security.hash_ip(ip, pepper=pepper) == atteso
    # non e' il semplice SHA256 dell'IP:
    assert security.hash_ip(ip, pepper=pepper) != hashlib.sha256(ip.encode()).hexdigest()
    # peppers diversi -> digest diversi per lo stesso IP:
    assert security.hash_ip(ip, pepper="altro-pepper") != security.hash_ip(ip, pepper=pepper)


def test_a5_hash_ip_legge_la_env_var_dedicata_se_il_pepper_non_e_iniettato(monkeypatch):
    from public_booking import security
    monkeypatch.setenv(security.IP_PEPPER_ENV_VAR, "pepper-da-env")
    assert security.hash_ip("9.9.9.9") == security.hash_ip("9.9.9.9", pepper="pepper-da-env")


def test_a6_nessuna_funzione_di_security_scrive_lip_grezzo_altrove():
    """Lettura statica: nessuna chiamata a `hashlib.sha256` diretta su un IP
    (solo tramite `hmac.new`), e nessun `print`/`log` in questo file."""
    codice = (PACCHETTO / "security.py").read_text(encoding="utf-8")
    assert "sha256(ip" not in codice.replace(" ", "")
    assert "print(" not in codice and "logging" not in codice


# ---------------------------------------------------------------------------
# B - schemas.py (D3, D4)
# ---------------------------------------------------------------------------

def test_b1_submit_body_non_ha_duration_user_id_agency_id_buffer():
    from public_booking.schemas import PublicBookingSubmitBody
    campi = set(PublicBookingSubmitBody.model_fields)
    assert campi == {"submission_token", "start_at", "name", "phone", "email"}
    assert "duration_minutes" not in campi
    assert "user_id" not in campi
    assert "agency_id" not in campi
    assert "buffer_before_minutes" not in campi
    assert "buffer_after_minutes" not in campi


def test_b2_submit_body_non_ha_una_nota_libera():
    from public_booking.schemas import PublicBookingSubmitBody
    assert "notes" not in PublicBookingSubmitBody.model_fields
    assert "note" not in PublicBookingSubmitBody.model_fields


def test_b3_submit_body_rifiuta_campi_extra():
    from pydantic import ValidationError
    from public_booking.schemas import PublicBookingSubmitBody
    with pytest.raises(ValidationError):
        PublicBookingSubmitBody(submission_token="x", start_at="2026-01-01T10:00:00+00:00",
                                name="Mario", phone="333", agency_id=1)


def test_b4_create_link_body_rifiuta_appointment_type_non_valido():
    from pydantic import ValidationError
    from public_booking.schemas import BookingLinkCreateBody
    with pytest.raises(ValidationError):
        BookingLinkCreateBody(assigned_user_id=1, appointment_type="not_a_real_type",
                              duration_minutes=30)


def test_b5_create_link_body_accetta_ogni_tipo_della_072():
    from public_booking.schemas import BookingLinkCreateBody
    from appointments.enums import APPOINTMENT_TYPES
    # La lista nota da migration 072.
    assert APPOINTMENT_TYPES == (
        "call", "video_call", "seller_meeting", "inspection", "buyer_visit",
        "valuation_presentation", "mandate_signing", "proposal",
        "preliminary_contract", "notary", "technical", "other")
    for tipo in APPOINTMENT_TYPES:
        corpo = BookingLinkCreateBody(assigned_user_id=1, appointment_type=tipo,
                                      duration_minutes=30)
        assert corpo.appointment_type == tipo


def test_b6_create_link_body_rifiuta_campi_extra():
    from pydantic import ValidationError
    from public_booking.schemas import BookingLinkCreateBody
    with pytest.raises(ValidationError):
        BookingLinkCreateBody(assigned_user_id=1, appointment_type="call",
                              duration_minutes=30, user_can_pick_agent=True)


def test_b7_enums_riusano_appointment_types_dellagenda_senza_duplicarli():
    from public_booking.enums import APPOINTMENT_TYPES as PB
    from appointments.enums import APPOINTMENT_TYPES as AG
    assert PB is AG


# ---------------------------------------------------------------------------
# C - impronta del payload (D7)
# ---------------------------------------------------------------------------

def test_c1_payload_fingerprint_e_deterministica():
    from datetime import datetime, timezone
    from public_booking.service import _payload_fingerprint
    quando = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    a = _payload_fingerprint(start_at=quando, name="Mario Rossi",
                             phone_normalized="393331234567", email_normalized=None)
    b = _payload_fingerprint(start_at=quando, name="Mario Rossi",
                             phone_normalized="393331234567", email_normalized=None)
    assert a == b
    assert len(a) == 64


@pytest.mark.parametrize("campo,valore", [
    ("name", "Altro Nome"),
    ("phone_normalized", "393339999999"),
    ("email_normalized", "diverso@example.it"),
])
def test_c2_payload_fingerprint_cambia_con_ciascun_campo(campo, valore):
    from datetime import datetime, timezone
    from public_booking.service import _payload_fingerprint
    quando = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    base = dict(start_at=quando, name="Mario Rossi", phone_normalized="393331234567",
                email_normalized="mario@example.it")
    a = _payload_fingerprint(**base)
    diverso = dict(base, **{campo: valore})
    b = _payload_fingerprint(**diverso)
    assert a != b


def test_c3_payload_fingerprint_e_insensibile_al_fuso_dello_stesso_istante():
    from datetime import datetime, timedelta, timezone
    from public_booking.service import _payload_fingerprint
    utc = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    roma = utc.astimezone(timezone(timedelta(hours=2)))
    a = _payload_fingerprint(start_at=utc, name="Mario", phone_normalized="1", email_normalized=None)
    b = _payload_fingerprint(start_at=roma, name="Mario", phone_normalized="1", email_normalized=None)
    assert a == b


# ---------------------------------------------------------------------------
# D - allowlist di privacy delle risposte (D privacy)
# ---------------------------------------------------------------------------

def test_d1_prenotazione_pubblica_espone_solo_i_quattro_campi_ammessi():
    from datetime import datetime, timezone
    from public_booking.service import _prenotazione_pubblica
    riga = {
        "id": 999, "agency_id": 1, "assigned_user_id": 2, "contact_id": 3,
        "status": "scheduled",
        "start_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        "end_at": datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
        "appointment_type": "call", "notes": "segreto", "location_text": "ufficio",
        "source_record_id": "abc", "created_by_user_id": None,
    }
    esposta = _prenotazione_pubblica(riga)
    assert set(esposta) == {"status", "start_at", "end_at", "appointment_type"}


def test_d2_link_pubblica_operatore_non_espone_mai_il_token_hash():
    from public_booking.service import _link_publica_operatore
    riga = {
        "id": 1, "agency_id": 1, "assigned_user_id": 2, "label": "Prova",
        "status": "active", "appointment_type": "call", "duration_minutes": 30,
        "buffer_before_minutes": 0, "buffer_after_minutes": 0,
        "created_at": None, "updated_at": None, "expires_at": None, "revoked_at": None,
        "token_hash": "x" * 64, "created_by_user_id": 9,
    }
    esposta = _link_publica_operatore(riga)
    assert "token_hash" not in esposta
    assert "created_by_user_id" not in esposta


def test_d3_public_names_legge_solo_name_e_nomi_operatore(monkeypatch):
    """Lettura statica delle query SQL eseguite (non del docstring): SOLO
    `agencies.name` e i due nomi dell'operatore, mai email/telefono/slug."""
    import re
    codice = inspect.getsource(__import__(
        "public_booking.repository", fromlist=["public_names"]).public_names)
    query = " ".join(re.findall(r'cur\.execute\("([^"]*)"', codice))
    assert "email" not in query
    assert "phone" not in query
    assert "slug" not in query
    assert "SELECT name FROM agencies" in query


# ---------------------------------------------------------------------------
# E - il guard di `create_public_booking_appointment` (D architetturale)
# ---------------------------------------------------------------------------

def test_e1_ctx_sbagliato_rifiutato_prima_di_apparire_un_cursore(monkeypatch):
    from datetime import datetime, timezone
    from appointments import service as appt_service
    from operator_auth.context import OperatorContext

    def _boom(*a, **k):
        raise AssertionError("non deve aprire un cursore prima del guard sul ctx")
    monkeypatch.setattr(appt_service, "_in_transazione", _boom)

    ctx_sbagliato = OperatorContext(user_id=1, agency_id=1, role="agent",
                                    is_platform_admin=False, session_id=None,
                                    auth_channel="session")
    with pytest.raises(TypeError):
        appt_service.create_public_booking_appointment(
            ctx_sbagliato, link={}, submission_hash="x",
            start_at=datetime(2026, 1, 1, tzinfo=timezone.utc), contact_data={})


def test_e2_ctx_con_origin_sbagliato_rifiutato(monkeypatch):
    from datetime import datetime, timezone
    from appointments import service as appt_service
    from operator_auth.context import SystemAgencyContext

    def _boom(*a, **k):
        raise AssertionError("non deve aprire un cursore prima del guard sul ctx")
    monkeypatch.setattr(appt_service, "_in_transazione", _boom)

    ctx_sbagliato = SystemAgencyContext(agency_id=1, origin="public_stima")
    with pytest.raises(TypeError):
        appt_service.create_public_booking_appointment(
            ctx_sbagliato, link={}, submission_hash="x",
            start_at=datetime(2026, 1, 1, tzinfo=timezone.utc), contact_data={})


def test_e3_public_booking_non_e_membro_del_set_chiuso_system_context_functions():
    """CORE resta `bridge_public_stima`-only (test_c7 in
    `test_p26_1_scope_enforcement.py`): il booking pubblico non ci entra,
    scrive tramite il repository dell'Agenda."""
    from core.scope import SYSTEM_CONTEXT_FUNCTIONS
    assert SYSTEM_CONTEXT_FUNCTIONS == frozenset({"bridge_public_stima"})


# ---------------------------------------------------------------------------
# F - il set chiuso delle origini di sistema (SYSTEM_CONTEXT_ORIGINS)
# ---------------------------------------------------------------------------

def test_f1_public_booking_e_nel_set_chiuso_delle_origini_di_sistema():
    from operator_auth.context import SYSTEM_CONTEXT_ORIGINS
    assert "public_booking" in SYSTEM_CONTEXT_ORIGINS


# ---------------------------------------------------------------------------
# G - il router pubblico dodgia il glob di completezza dei prefissi tenant
# ---------------------------------------------------------------------------

def test_g1_router_py_e_un_re_export_senza_una_propria_apirouter():
    """`public_booking/router.py` non deve contenere un letterale
    `APIRouter(prefix=...)`: e' cio' che lo rende invisibile al glob
    `*/router.py` dei certificatori di completezza (test_p26_5/test_p26_6),
    esattamente come `communication/public_router.py`."""
    codice = (PACCHETTO / "router.py").read_text(encoding="utf-8")
    albero = ast.parse(codice)
    chiamate_apirouter = [
        nodo for nodo in ast.walk(albero)
        if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", None) == "APIRouter"
    ]
    assert chiamate_apirouter == []


def test_g2_router_py_re_esporta_il_router_pubblico_vero():
    from public_booking.router import router as shim
    from public_booking.public_router import router as vero
    assert shim is vero


def test_g3_public_router_ha_il_prefisso_pubblico_e_nessuna_dipendenza_operatore():
    from public_booking.public_router import router
    assert router.prefix == "/api/public/booking"
    for rotta in router.routes:
        assert rotta.dependencies == []


# ---------------------------------------------------------------------------
# H - il mount in main.py (nessuna dipendenza da operatore)
# ---------------------------------------------------------------------------

def test_h1_main_monta_il_router_pubblico_senza_require_authenticated_operator():
    codice = (ROOT / "main.py").read_text(encoding="utf-8")
    riga = next(r for r in codice.splitlines() if "public_booking_router" in r
                and "include_router" in r)
    assert "require_authenticated_operator" not in riga
    assert "dependencies=" not in riga


# ---------------------------------------------------------------------------
# I - D10: la stessa guardia HARD nei due percorsi (lettura slot e scrittura)
# ---------------------------------------------------------------------------

def test_i1_get_public_slots_controlla_has_weekly_config_prima_di_is_within():
    codice = inspect.getsource(__import__(
        "public_booking.service", fromlist=["get_public_slots"]).get_public_slots)
    assert 'not ingressi["has_weekly_config"]' in codice
    assert codice.index('has_weekly_config"]') < codice.index("effective_windows")


def test_i2_create_public_booking_appointment_controlla_has_weekly_config():
    codice = inspect.getsource(__import__(
        "appointments.service", fromlist=["create_public_booking_appointment"]
    ).create_public_booking_appointment)
    assert 'not ingressi["has_weekly_config"]' in codice


# ---------------------------------------------------------------------------
# J - la migration: enum letterali coerenti col resto del codice
# ---------------------------------------------------------------------------

def test_j1_migration_077_esiste_con_la_sua_down():
    su = ROOT / "migrations" / "077_a30_12_public_booking.sql"
    giu = ROOT / "migrations" / "077_a30_12_public_booking_down.sql"
    assert su.exists() and giu.exists()


def test_j2_check_appointment_type_della_077_elenca_i_dodici_tipi_reali():
    from appointments.enums import APPOINTMENT_TYPES
    testo = (ROOT / "migrations" / "077_a30_12_public_booking.sql").read_text(encoding="utf-8")
    for tipo in APPOINTMENT_TYPES:
        assert f"'{tipo}'" in testo, tipo


def test_j3_public_booking_source_e_gia_ammessa_dal_check_di_072_o_073():
    """'booking_link' deve essere un valore ammesso da
    `appointments_source_chk` per far passare l'INSERT reale - lo si
    verifica leggendo entrambe le migration (073 ridefinisce il CHECK)."""
    txt072 = (ROOT / "migrations" / "072_a30_1_appointments.sql").read_text(encoding="utf-8")
    txt073 = (ROOT / "migrations" / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8")
    assert "'booking_link'" in txt072
    assert "'booking_link'" in txt073


def test_j4_public_booking_source_matches_repository_constant():
    from appointments import service as appt_service
    assert appt_service.PUBLIC_BOOKING_SOURCE == "booking_link"


# ---------------------------------------------------------------------------
# K - i limiti di velocita' (D9): default + override, budget separati
# ---------------------------------------------------------------------------

def test_k1_default_rate_limits_ha_i_quattro_budget_separati():
    from public_booking.service import DEFAULT_RATE_LIMITS
    assert set(DEFAULT_RATE_LIMITS) == {"get_link", "get_ip", "post_link", "post_ip"}
    assert all(isinstance(v, int) and v > 0 for v in DEFAULT_RATE_LIMITS.values())


def test_k2_limit_legge_la_env_var_dedicata_se_presente(monkeypatch):
    from public_booking import service
    monkeypatch.setenv("PUBLIC_BOOKING_RATE_LIMIT_POST_IP", "3")
    assert service._limit("post_ip") == 3
    monkeypatch.delenv("PUBLIC_BOOKING_RATE_LIMIT_POST_IP")
    assert service._limit("post_ip") == service.DEFAULT_RATE_LIMITS["post_ip"]


def test_k3_window_start_arrotonda_a_finestre_fisse_di_60_secondi():
    from datetime import datetime, timezone
    from public_booking.service import _window_start, RATE_LIMIT_WINDOW_SECONDS
    assert RATE_LIMIT_WINDOW_SECONDS == 60
    a = datetime(2026, 1, 1, 10, 0, 5, tzinfo=timezone.utc)
    b = datetime(2026, 1, 1, 10, 0, 59, tzinfo=timezone.utc)
    assert _window_start(a) == _window_start(b)
    c = datetime(2026, 1, 1, 10, 1, 0, tzinfo=timezone.utc)
    assert _window_start(a) != _window_start(c)


# ---------------------------------------------------------------------------
# L - i permessi D2 (senza database: sui soli helper puri)
# ---------------------------------------------------------------------------

def test_l1_agente_non_puo_gestire_tutti_ma_owner_admin_si():
    from operator_auth.context import OperatorContext
    from public_booking.service import _puo_gestire_tutti
    agente = OperatorContext(user_id=1, agency_id=1, role="agent", is_platform_admin=False,
                             session_id=None, auth_channel="session")
    owner = OperatorContext(user_id=2, agency_id=1, role="agency_owner", is_platform_admin=False,
                            session_id=None, auth_channel="session")
    assert _puo_gestire_tutti(agente) is False
    assert _puo_gestire_tutti(owner) is True


def test_l2_attore_richiede_un_utente_reale():
    from core.exceptions import PermissionDenied
    from operator_auth.context import OperatorContext
    from public_booking.service import _attore
    anonimo = OperatorContext(user_id=None, agency_id=1, role="agent",
                              is_platform_admin=False, session_id=None,
                              auth_channel="legacy_basic")
    with pytest.raises(PermissionDenied):
        _attore(anonimo)
