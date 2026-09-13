"""P27-4 - configurazione agenzia.

`agencies.settings` esiste dalla 027 come JSONB libero, e la 027 stessa lo
dichiarava un contenitore in attesa: "P26-1 defines no key in it and reads it
nowhere". L'audit di P27-4 lo conferma - nessun modulo legge una chiave la'
dentro, e le uniche che comparivano erano valori di prova nei test di P27-2.

P27-4 non aggiunge una colonna: da' un contratto a quella che c'e'.

Mappa:

    A   lo schema: cosa si puo' scrivere, e cosa no
    B   la lettura: default applicativi, nessun backfill
    C   la scrittura: merge, mai sostituzione
    D   legacy: chiavi sconosciute ne' esposte ne' distrutte
    E   transazione e audit
    F   HTTP e sicurezza
    G   perimetro: nessuna migration, nessuna seconda strada verso il JSONB
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from operator_auth.context import OperatorContext
from platform_admin import audit as platform_audit
from platform_admin import configuration_service
from platform_admin import dependencies as platform_deps
from platform_admin.enums import (
    ACTION_AGENCY_CONFIGURATION_UPDATE,
    AGENCY_LOCALES,
    CONFIGURATION_DEFAULT_LOCALE,
    CONFIGURATION_DEFAULT_TIMEZONE,
    CONFIGURATION_FIELDS,
    RESULT_ERROR,
    RESULT_SUCCESS,
    ROUTER_PREFIX,
    TARGET_TYPE_AGENCY,
)
from platform_admin.exceptions import (
    AgencyConfigurationCorrupted,
    AgencyNotFound,
    PlatformAuditUnavailable,
)
from platform_admin.router import router as platform_router

ROOT = Path(__file__).resolve().parents[1]

PLATFORM_USER = 77
AGENCY = 1
EXPIRES = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)

DEFAULTS = {"timezone": CONFIGURATION_DEFAULT_TIMEZONE,
            "locale": CONFIGURATION_DEFAULT_LOCALE}


def _ctx(**overrides) -> OperatorContext:
    base = dict(
        user_id=PLATFORM_USER, agency_id=None, role=None,
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


class FakeConn:
    def __init__(self, *, commit_fails=False, store=None, order=None):
        self.events: list[str] = []
        self.commit_fails = commit_fails
        self.store = store
        self.order = order if order is not None else []

    def commit(self):
        self.events.append("commit")
        self.order.append("commit")
        if self.commit_fails:
            raise RuntimeError("il commit e' fallito")
        if self.store is not None:
            self.store.commit()

    def rollback(self):
        self.events.append("rollback")
        self.order.append("rollback")
        if self.store is not None:
            self.store.restore()


class Store:
    """Una agenzia, con la sua riga `settings` e una transazione simulata."""

    def __init__(self, settings=None, status="active"):
        self.rows = {
            AGENCY: {
                "id": AGENCY, "name": "Agenzia A", "slug": "agenzia-a",
                "status": status, "settings": dict(settings or {}),
                "created_at": CREATED, "updated_at": CREATED,
            }
        }
        self._snapshot = None

    def snapshot(self):
        self._snapshot = {k: {**v, "settings": dict(v["settings"])}
                          for k, v in self.rows.items()}

    def restore(self):
        if self._snapshot is None:
            return
        self.rows = self._snapshot
        self._snapshot = None

    def commit(self):
        self._snapshot = None


@pytest.fixture
def service(monkeypatch):
    state = {
        "conn": FakeConn(), "store": Store(), "audit": [],
        "audit_fails": False, "order": [], "session": _ctx(),
    }

    @contextmanager
    def _cursor():
        state["order"].append("open")
        conn = state["conn"]
        conn.store = state["store"]
        conn.order = state["order"]
        state["store"].snapshot()
        try:
            yield conn, object()
        except Exception:
            conn.rollback()
            raise
        else:
            state["store"].restore()

    monkeypatch.setattr(configuration_service, "platform_operation_cursor", _cursor)

    repo = configuration_service.agencies_repository

    def _get(cur, agency_id):
        row = state["store"].rows.get(agency_id)
        return {**row, "settings": dict(row["settings"])} if row else None

    def _update(cur, agency_id, fields):
        # Il guardrail del repository VERO, applicato anche qui: senza, un
        # campo non aggiornabile passerebbe e il test proverebbe il contrario
        # di quel che dice.
        from platform_admin import operators_repository  # noqa: F401
        unknown = [k for k in fields if k not in repo.UPDATABLE_COLUMNS]
        if unknown or not fields:
            raise ValueError(f"colonne non aggiornabili: {sorted(unknown)}")
        state["order"].append("write")
        row = state["store"].rows.get(agency_id)
        if row is None:
            return None
        row.update(fields)
        row["updated_at"] = LATER
        return {**row, "settings": dict(row["settings"])}

    monkeypatch.setattr(repo, "get_agency", _get)
    monkeypatch.setattr(repo, "update_agency", _update)

    def _record(**kwargs):
        is_admission = kwargs.get("action") == "platform.admission"
        is_compensating = kwargs.get("result") == RESULT_ERROR
        if state["audit_fails"] and not is_admission and not is_compensating:
            state["order"].append("audit-failed")
            raise PlatformAuditUnavailable("indisponibile")
        state["order"].append(
            "admission" if is_admission
            else "audit-error" if is_compensating else "audit"
        )
        state["audit"].append(kwargs)
        return len(state["audit"])

    monkeypatch.setattr(platform_audit, "record", _record)
    state["operations"] = lambda: [
        e for e in state["audit"] if e["action"] != "platform.admission"
    ]
    return state


# ===========================================================================
# A - LO SCHEMA
# ===========================================================================

def test_a1_the_contract_declares_exactly_two_fields():
    from platform_admin.schemas import (
        AgencyConfigurationInput,
        AgencyConfigurationResponse,
    )

    assert set(AgencyConfigurationResponse.model_fields) == {"timezone", "locale"}
    assert set(AgencyConfigurationInput.model_fields) == {"timezone", "locale"}
    assert set(CONFIGURATION_FIELDS) == {"timezone", "locale"}


def test_a1_the_public_contract_is_not_a_free_dict():
    """Nessun `dict[str, Any]` come contratto finale: e' precisamente cio' che
    P27-4 esiste per togliere."""
    from platform_admin.schemas import (
        AgencyConfigurationInput,
        AgencyConfigurationResponse,
    )

    for model in (AgencyConfigurationResponse, AgencyConfigurationInput):
        for name, field in model.model_fields.items():
            assert field.annotation in (str, str | None), (name, field.annotation)


@pytest.mark.parametrize("value", [
    "Europe/Rome", "Europe/Paris", "America/New_York", "UTC", "Asia/Tokyo",
])
def test_a2_a_real_iana_timezone_is_accepted(value):
    from platform_admin.schemas import AgencyConfigurationInput

    assert AgencyConfigurationInput(timezone=value).timezone == value


@pytest.mark.parametrize("value", [
    "Mars/Olympus", "Europa/Roma", "GMT+2", "", "europe/rome",
    "../../etc/passwd", "Europe/Rome/Extra",
])
def test_a2_an_invalid_timezone_is_refused(value):
    """Validato con `zoneinfo`, non con un elenco tenuto a mano: il database
    dei fusi cambia piu' volte l'anno e una lista nel codice sarebbe sbagliata
    entro pochi mesi senza che nessuno se ne accorga."""
    import pydantic
    from platform_admin.schemas import AgencyConfigurationInput

    with pytest.raises(pydantic.ValidationError):
        AgencyConfigurationInput(timezone=value)


def test_a2_the_timezone_is_not_validated_against_a_hand_kept_list():
    source = (ROOT / "platform_admin" / "schemas.py").read_text()
    assert "from zoneinfo import" in source
    assert "available_timezones" not in source, "elenco materializzato"
    enums_source = (ROOT / "platform_admin" / "enums.py").read_text()
    assert "Europe/Paris" not in enums_source, "elenco di fusi in enums"


def test_a3_only_the_declared_locales_are_accepted():
    import pydantic
    from platform_admin.schemas import AgencyConfigurationInput

    for value in AGENCY_LOCALES:
        assert AgencyConfigurationInput(locale=value).locale == value
    for value in ("fr-FR", "it", "IT", "it_IT", ""):
        with pytest.raises(pydantic.ValidationError):
            AgencyConfigurationInput(locale=value)


def test_a4_an_unknown_field_is_refused():
    import pydantic
    from platform_admin.schemas import AgencyConfigurationInput

    with pytest.raises(pydantic.ValidationError):
        AgencyConfigurationInput(citta="Alba")


def test_a5_an_empty_patch_is_refused_but_an_empty_post_settings_is_not():
    """La differenza e' sostanziale: su una POST `{}` significa "nessuna
    configurazione, usa i default"; su una PATCH significa una richiesta che
    non dice cosa fare."""
    import pydantic
    from platform_admin.schemas import (
        AgencyConfigurationInput,
        AgencyConfigurationUpdateRequest,
    )

    assert AgencyConfigurationInput().supplied() == {}
    with pytest.raises(pydantic.ValidationError):
        AgencyConfigurationUpdateRequest()


def test_a6_a_null_value_is_refused():
    import pydantic
    from platform_admin.schemas import AgencyConfigurationUpdateRequest

    with pytest.raises(pydantic.ValidationError):
        AgencyConfigurationUpdateRequest(timezone=None)


# ===========================================================================
# B - LA LETTURA: DEFAULT APPLICATIVI, NESSUN BACKFILL
# ===========================================================================

def test_b1_an_empty_row_reads_as_the_application_defaults(service):
    """La 027 ha messo `DEFAULT '{}'` su ogni agenzia: applicare i default in
    lettura evita un backfill e lascia una sola sorgente di verita' su cosa
    significhi "non configurato"."""
    assert service["store"].rows[AGENCY]["settings"] == {}
    assert configuration_service.get_configuration(AGENCY) == DEFAULTS


def test_b1_the_defaults_are_never_written_into_the_row(service):
    configuration_service.get_configuration(AGENCY)
    assert service["store"].rows[AGENCY]["settings"] == {}, "la lettura ha scritto"


def test_b2_a_partial_row_is_completed_with_the_defaults(service):
    service["store"] = Store({"timezone": "Europe/Paris"})
    assert configuration_service.get_configuration(AGENCY) == {
        "timezone": "Europe/Paris",
        "locale": CONFIGURATION_DEFAULT_LOCALE,
    }


@pytest.mark.parametrize("stored,corrotto", [
    ({"timezone": "Mars/Olympus"}, "timezone"),
    ({"locale": "fr-FR"}, "locale"),
    ({"timezone": "Mars/Olympus", "locale": "fr-FR"}, "entrambi"),
    ({"timezone": 42}, "timezone non e' una stringa"),
    ({"timezone": "Europe/Rome", "locale": "de-DE"}, "uno valido e uno no"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_b3_a_known_field_holding_an_invalid_value_is_corruption(
    service, stored, corrotto
):
    """Era: si applicava il default in silenzio.

    L'argomento di allora - sollevare rende 500 anche la GET e la PATCH legge
    prima di scrivere, quindi non ci sarebbe via d'uscita - era sbagliato in un
    punto: la PATCH che SOSTITUISCE il campo corrotto funziona, perche' il
    valore viene rimpiazzato prima della validazione. Vedi il gruppo H.

    E il prezzo del default era troppo alto: rispondere `Europe/Rome` mentre
    nel database c'e' `Mars/Olympus` significa che l'agenzia lavora su un fuso
    e la riga ne dice un altro, per sempre e senza che nessuno se ne accorga.
    Un errore rumoroso si nota e si ripara; un valore inventato no.
    """
    service["store"] = Store(stored)
    with pytest.raises(AgencyConfigurationCorrupted):
        configuration_service.get_configuration(AGENCY)


def test_b3_the_corrupted_value_is_left_in_the_row(service):
    """Leggere non ripara e non distrugge: il valore resta li' finche' una
    PATCH non lo sostituisce."""
    service["store"] = Store({"timezone": "Mars/Olympus"})
    with pytest.raises(AgencyConfigurationCorrupted):
        configuration_service.get_configuration(AGENCY)
    assert service["store"].rows[AGENCY]["settings"]["timezone"] == "Mars/Olympus"


def test_b3_a_valid_row_alongside_legacy_keys_is_not_corruption(service):
    """Controllo negativo: una chiave SCONOSCIUTA con qualunque valore non
    rende corrotta la configurazione - non e' del contratto, quindi il
    contratto non ha nulla da dire su di essa."""
    service["store"] = Store({"timezone": "Europe/Paris", "legacy_x": 123,
                              "roba": {"annidata": None}})
    assert configuration_service.get_configuration(AGENCY) == {
        "timezone": "Europe/Paris", "locale": CONFIGURATION_DEFAULT_LOCALE
    }


def test_b4_a_missing_agency_is_not_found(service):
    with pytest.raises(AgencyNotFound):
        configuration_service.get_configuration(999999)


def test_b5_a_read_writes_no_audit_row_and_never_commits(service):
    configuration_service.get_configuration(AGENCY)
    assert service["operations"]() == []
    assert "commit" not in service["order"], service["order"]


# ===========================================================================
# C - LA SCRITTURA: MERGE, MAI SOSTITUZIONE
# ===========================================================================

def _update(service, **supplied):
    return configuration_service.update_configuration(_ctx(), AGENCY, supplied)


def test_c1_a_patch_writes_only_the_field_it_names(service):
    service["store"] = Store({"timezone": "Europe/Paris", "locale": "it-IT"})
    assert _update(service, timezone="Asia/Tokyo") == {
        "timezone": "Asia/Tokyo", "locale": "it-IT"
    }


def test_c2_the_other_known_field_keeps_its_value(service):
    """MERGE e non sostituzione: `{**stored, **supplied}`."""
    service["store"] = Store({"timezone": "Europe/Paris", "locale": "it-IT"})
    _update(service, timezone="UTC")
    assert service["store"].rows[AGENCY]["settings"] == {
        "timezone": "UTC", "locale": "it-IT"
    }


def test_c3_a_patch_on_an_empty_row_adds_only_what_it_names(service):
    """Non riempie la riga con i default: quelli vivono nell'applicazione."""
    _update(service, timezone="UTC")
    assert service["store"].rows[AGENCY]["settings"] == {"timezone": "UTC"}
    assert configuration_service.get_configuration(AGENCY) == {
        "timezone": "UTC", "locale": CONFIGURATION_DEFAULT_LOCALE
    }


def test_c4_repeating_the_same_value_is_a_valid_patch(service):
    """`changed_fields` descrive i campi SCRITTI, non un diff semantico."""
    service["store"] = Store({"timezone": "Europe/Rome"})
    assert _update(service, timezone="Europe/Rome")["timezone"] == "Europe/Rome"
    assert service["operations"]()[-1]["metadata"] == {
        "changed_fields": ["timezone"]
    }


def test_c5_updated_at_is_bumped(service):
    prima = service["store"].rows[AGENCY]["updated_at"]
    _update(service, timezone="UTC")
    assert service["store"].rows[AGENCY]["updated_at"] != prima


def test_c6_the_agency_identity_is_never_touched(service):
    """`name`, `slug` e `status` restano proprieta' di P27-2."""
    prima = {k: service["store"].rows[AGENCY][k] for k in ("name", "slug", "status")}
    _update(service, timezone="UTC", locale="it-IT")
    dopo = {k: service["store"].rows[AGENCY][k] for k in ("name", "slug", "status")}
    assert dopo == prima


def test_c7_a_missing_agency_is_not_found_and_writes_nothing(service):
    with pytest.raises(AgencyNotFound):
        configuration_service.update_configuration(_ctx(), 999999, {"timezone": "UTC"})
    assert "write" not in service["order"], service["order"]


@pytest.mark.parametrize("status", ["active", "suspended", "archived"])
def test_c8_the_configuration_is_writable_in_every_agency_status(service, status):
    """E' il momento in cui serve di piu': si sospende un affiliato proprio
    quando qualcosa non va. Non allenta nulla sul tenant, dove `operator_auth`
    continua a pretendere `agency_status='active'`."""
    service["store"] = Store(status=status)
    assert _update(service, timezone="UTC")["timezone"] == "UTC"
    assert configuration_service.get_configuration(AGENCY)["timezone"] == "UTC"


# ===========================================================================
# D - LEGACY: NE' ESPOSTE NE' DISTRUTTE
# ===========================================================================

LEGACY = {"chiave_dimenticata": "un valore", "vecchia": {"annidata": True}}


def test_d1_unknown_legacy_keys_are_not_exposed(service):
    """La risposta e' il contratto, non un'eco della riga."""
    service["store"] = Store({**LEGACY, "timezone": "Europe/Paris"})
    letta = configuration_service.get_configuration(AGENCY)
    assert set(letta) == {"timezone", "locale"}
    for chiave in LEGACY:
        assert chiave not in letta


def test_d2_unknown_legacy_keys_survive_a_patch(service):
    """Una PATCH che sostituisse l'intero JSON le porterebbe via in silenzio, e
    in una colonna che nessuno guarda sarebbe una perdita che nessuno nota."""
    service["store"] = Store({**LEGACY, "timezone": "Europe/Paris"})
    _update(service, timezone="UTC")
    stored = service["store"].rows[AGENCY]["settings"]
    assert stored["chiave_dimenticata"] == "un valore"
    assert stored["vecchia"] == {"annidata": True}
    assert stored["timezone"] == "UTC"


def test_d3_legacy_keys_survive_several_patches(service):
    service["store"] = Store(dict(LEGACY))
    _update(service, timezone="UTC")
    _update(service, locale="it-IT")
    _update(service, timezone="Europe/Paris")
    stored = service["store"].rows[AGENCY]["settings"]
    assert stored["chiave_dimenticata"] == "un valore"
    assert stored["timezone"] == "Europe/Paris"
    assert stored["locale"] == "it-IT"


def test_d4_a_legacy_key_cannot_be_written_through_the_api(service):
    """Preservare non vuol dire riaprire: quelle chiavi sopravvivono, non si
    possono aggiungere."""
    import pydantic
    from platform_admin.schemas import AgencyConfigurationUpdateRequest

    with pytest.raises(pydantic.ValidationError):
        AgencyConfigurationUpdateRequest(chiave_dimenticata="x")


# ===========================================================================
# E - TRANSAZIONE E AUDIT
# ===========================================================================

def test_e1_the_patch_audits_before_it_commits(service):
    _update(service, timezone="UTC")
    assert service["order"] == ["open", "write", "audit", "commit"]


def test_e2_a_failed_audit_prevents_the_commit_and_the_change(service):
    service["store"] = Store({"timezone": "Europe/Rome"})
    service["audit_fails"] = True
    with pytest.raises(PlatformAuditUnavailable):
        _update(service, timezone="UTC")
    assert service["conn"].events == ["rollback"]
    assert service["store"].rows[AGENCY]["settings"] == {"timezone": "Europe/Rome"}


def test_e3_a_failed_commit_writes_a_compensating_error_row(service):
    service["conn"] = FakeConn(commit_fails=True)
    with pytest.raises(RuntimeError, match="commit"):
        _update(service, timezone="UTC")
    assert [e["result"] for e in service["operations"]()] == [
        RESULT_SUCCESS, RESULT_ERROR
    ]
    assert service["operations"]()[-1]["metadata"] == {"commit_failed": True}


def test_e4_the_audit_names_the_agency_as_its_target(service):
    _update(service, timezone="UTC", locale="it-IT")
    entry = service["operations"]()[-1]
    assert entry["action"] == ACTION_AGENCY_CONFIGURATION_UPDATE
    assert entry["target_type"] == TARGET_TYPE_AGENCY
    assert entry["target_id"] == AGENCY
    assert entry["target_agency_id"] == AGENCY
    assert entry["actor"].user_id == PLATFORM_USER


def test_e5_the_audit_metadata_carries_field_names_and_no_values(service):
    """Il fuso orario di un affiliato non e' un segreto, ma una tabella
    append-only non e' il posto in cui far entrare il contenuto di una
    configurazione per comodita' di lettura - e la regola vale prima che
    qualcuno ci aggiunga un campo per cui conta."""
    service["store"] = Store(dict(LEGACY))
    _update(service, timezone="Asia/Tokyo", locale="it-IT")
    entry = service["operations"]()[-1]
    assert entry["metadata"] == {"changed_fields": ["locale", "timezone"]}
    blob = repr(entry)
    for leak in ("Asia/Tokyo", "it-IT", "chiave_dimenticata", "un valore"):
        assert leak not in blob, (leak, blob)


def test_e6_the_order_still_has_one_implementation(service):
    """P27-4 riusa `transaction.py` e non ne scrive una copia."""
    source = (ROOT / "platform_admin" / "configuration_service.py").read_text()
    assert "from .transaction import audit_then_commit" in source
    assert "conn.commit()" not in source
    assert "audit.record" not in source


# ===========================================================================
# F - HTTP E SICUREZZA
# ===========================================================================

@pytest.fixture
def client(service, monkeypatch):
    from operator_auth import dependencies as operator_deps

    def _resolve(_token):
        if service["session"] is None:
            return None
        return {"context": service["session"], "agency_name": "Agenzia",
                "expires_at": EXPIRES}

    monkeypatch.setattr(operator_deps.service, "session_from_token", _resolve)

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    http = TestClient(app, raise_server_exceptions=False)
    http.cookies.set("stima360_operator_session", "un-token")
    return http


CONFIG = f"{ROUTER_PREFIX}/agencies/{AGENCY}/configuration"


def test_f1_get_returns_the_defaults_for_an_unconfigured_agency(client):
    response = client.get(CONFIG)
    assert response.status_code == 200, response.text
    assert response.json() == DEFAULTS


def test_f2_patch_timezone(client):
    response = client.patch(CONFIG, json={"timezone": "Europe/Paris"})
    assert response.status_code == 200, response.text
    assert response.json() == {"timezone": "Europe/Paris",
                               "locale": CONFIGURATION_DEFAULT_LOCALE}
    assert client.get(CONFIG).json()["timezone"] == "Europe/Paris"


def test_f2_patch_locale(client):
    response = client.patch(CONFIG, json={"locale": CONFIGURATION_DEFAULT_LOCALE})
    assert response.status_code == 200, response.text
    assert response.json()["locale"] == CONFIGURATION_DEFAULT_LOCALE


@pytest.mark.parametrize("body,perche", [
    ({"timezone": "Mars/Olympus"}, "timezone inesistente"),
    ({"timezone": "GMT+2"}, "timezone che non e' un identificatore IANA"),
    ({"timezone": ""}, "timezone vuota"),
    ({"locale": "fr-FR"}, "locale non ammesso"),
    ({"locale": "it"}, "locale senza regione"),
    ({"citta": "Alba"}, "campo sconosciuto"),
    ({}, "PATCH vuota"),
    ({"timezone": None}, "timezone null"),
    ({"locale": None}, "locale null"),
    ({"timezone": 42}, "timezone non e' una stringa"),
    ({"name": "Altro nome"}, "campo che appartiene a P27-2"),
    ({"slug": "altro-slug"}, "slug: immutabile e non di questa route"),
    ({"status": "suspended"}, "status: appartiene a P27-2"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_f3_a_malformed_patch_is_422(client, body, perche):
    assert client.patch(CONFIG, json=body).status_code == 422, perche


def test_f3_a_refused_patch_changes_nothing(client, service):
    client.patch(CONFIG, json={"timezone": "Europe/Paris"})
    assert client.patch(CONFIG, json={"timezone": "Mars/Olympus"}).status_code == 422
    assert client.get(CONFIG).json()["timezone"] == "Europe/Paris"


@pytest.mark.parametrize("method", ["get", "patch"])
def test_f4_an_absent_agency_is_404(client, method):
    path = f"{ROUTER_PREFIX}/agencies/999999/configuration"
    kwargs = {"json": {"timezone": "UTC"}} if method == "patch" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 404


@pytest.mark.parametrize("status", ["suspended", "archived"])
def test_f5_a_suspended_or_archived_agency_stays_configurable(client, service, status):
    service["store"] = Store(status=status)
    assert client.get(CONFIG).status_code == 200
    response = client.patch(CONFIG, json={"timezone": "UTC"})
    assert response.status_code == 200, response.text
    assert response.json()["timezone"] == "UTC"


@pytest.mark.parametrize("method,body", [("get", None), ("patch", {"timezone": "UTC"})])
def test_f6_a_tenant_operator_is_403(client, service, method, body):
    service["session"] = _ctx(agency_id=AGENCY, role="agency_owner",
                              is_platform_admin=False)
    kwargs = {"json": body} if body else {}
    assert getattr(client, method)(CONFIG, **kwargs).status_code == 403


@pytest.mark.parametrize("method,body", [("get", None), ("patch", {"timezone": "UTC"})])
def test_f6_an_anonymous_caller_is_401(client, method, body):
    client.cookies.clear()
    kwargs = {"json": body} if body else {}
    assert getattr(client, method)(CONFIG, **kwargs).status_code == 401


def test_f7_an_unwritable_audit_answers_503_and_changes_nothing(client, service):
    client.patch(CONFIG, json={"timezone": "Europe/Paris"})
    service["audit_fails"] = True
    assert client.patch(CONFIG, json={"timezone": "UTC"}).status_code == 503
    service["audit_fails"] = False
    assert client.get(CONFIG).json()["timezone"] == "Europe/Paris"


def test_f8_there_is_no_delete_on_the_configuration(client):
    assert client.delete(CONFIG).status_code == 405


def test_f8_the_real_application_exposes_the_two_configuration_routes():
    import main

    spec = main.app.openapi()
    path = f"{ROUTER_PREFIX}/agencies/{{agency_id}}/configuration"
    assert set(spec["paths"][path]) == {"get", "patch"}
    assert not [
        p for p, ops in spec["paths"].items()
        if p.startswith(ROUTER_PREFIX) and "delete" in ops
    ]


# ===========================================================================
# G - PERIMETRO
# ===========================================================================

def test_g1_p27_4_added_no_migration():
    """`agencies.settings` esiste dalla 027. P27-4 le da' un contratto, non una
    colonna."""
    versions = sorted(
        int(path.name[:3]) for path in (ROOT / "migrations").glob("*.sql")
        if not path.name.endswith("_down.sql") and path.name[:3].isdigit()
    )
    assert versions[-1] == 57, versions[-3:]


def test_g2_there_is_only_one_route_to_the_settings_column():
    """LA CHIUSURA DELLA SUPERFICIE LIBERA.

    P27-2 accettava `settings` come `dict[str, Any]` sia nella POST sia nella
    PATCH generica. Due strade verso lo stesso JSONB, una validata e una
    libera, significano che quella libera e' il contratto vero.
    """
    from platform_admin.schemas import (
        AgencyConfigurationInput,
        AgencyCreateRequest,
        AgencyUpdateRequest,
    )

    # La PATCH generica non lo conosce piu'.
    assert "settings" not in AgencyUpdateRequest.model_fields

    # La POST lo accetta solo attraverso lo STESSO modello validato.
    assert AgencyCreateRequest.model_fields["settings"].annotation is (
        AgencyConfigurationInput
    )


def test_g2_the_generic_patch_cannot_reach_the_settings_column(client):
    client.patch(CONFIG, json={"timezone": "Europe/Paris"})
    response = client.patch(
        f"{ROUTER_PREFIX}/agencies/{AGENCY}",
        json={"settings": {"timezone": "UTC"}},
    )
    assert response.status_code == 422, response.text
    assert client.get(CONFIG).json()["timezone"] == "Europe/Paris"


def test_g3_no_free_dict_remains_in_the_public_write_contract():
    """Nessun `dict[str, Any]` scrivibile resta nella superficie Platform.

    `AgencyResponse.settings` e' ancora un dict: e' la LETTURA della riga
    grezza, che P27-2 restituisce e che nessuno scrive. Il contratto di
    scrittura e' il modello.
    """
    from platform_admin import schemas

    for name in dir(schemas):
        model = getattr(schemas, name)
        if not (isinstance(model, type) and issubclass(model, schemas.PlatformModel)):
            continue
        for field_name, field in model.model_fields.items():
            if field_name != "settings":
                continue
            assert field.annotation is schemas.AgencyConfigurationInput, (
                name, field_name, field.annotation
            )


def test_g4_p27_4_introduces_no_territory_routing_or_role():
    """Il contenitore e' validato, non riempito di roba delle fasi successive."""
    from platform_admin.schemas import (
        AgencyConfigurationInput,
        AgencyConfigurationResponse,
        AgencyConfigurationUpdateRequest,
    )

    # La prova che conta e' sui CAMPI DICHIARATI: sono due, e restano due.
    # Un controllo a parole sul testo del file era la prima stesura di questo
    # test, ed era fragile - `schemas.py` contiene legittimamente commenti che
    # nominano le fasi successive ("P27-5 sui territori"), e il test spezzava
    # il file su un marcatore che poi si e' spostato.
    for model in (AgencyConfigurationResponse, AgencyConfigurationInput,
                  AgencyConfigurationUpdateRequest):
        assert set(model.model_fields) == {"timezone", "locale"}, model.__name__

    # E il service di P27-4, che e' interamente di questa fase, non nomina
    # niente delle altre.
    source = (ROOT / "platform_admin" / "configuration_service.py").read_text()
    for parola in ("territor", "routing", "comune", "microzona",
                   "permission", "api_key", "token", "secret"):
        assert parola not in source.lower(), (parola, "configuration_service")


def test_g5_the_configuration_holds_no_secret_by_construction():
    """Due campi dichiarati, entrambi stringhe di presentazione. Non c'e' un
    posto in cui una chiave API possa entrare senza che qualcuno lo scriva."""
    from platform_admin.schemas import AgencyConfigurationInput

    assert set(AgencyConfigurationInput.model_fields) == {"timezone", "locale"}
    assert AgencyConfigurationInput.model_config["extra"] == "forbid"


def test_g6_the_service_reuses_the_existing_repository():
    """Nessun repository nuovo: `agencies_repository.update_agency` sa gia'
    scrivere `settings`, e P27-4 non ha ragione di duplicarlo."""
    tree = ast.parse(
        (ROOT / "platform_admin" / "configuration_service.py").read_text()
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        if isinstance(node, ast.ImportFrom) and node.module is None:
            imported.update(a.name for a in node.names)
    assert "agencies_repository" in imported or any(
        "agencies_repository" in m for m in imported
    ), imported
    assert not (ROOT / "platform_admin" / "configuration_repository.py").exists()


def test_g7_the_real_application_exposes_exactly_the_platform_surface():
    """L'elenco ESAUSTIVO, che appartiene sempre alla fase piu' recente.

    Tenuto in un file solo e spostato a ogni fase: un perno sulla dimensione
    totale dentro il file di una fase vecchia si romperebbe a ogni fase che la
    allarga, e diventerebbe rumore invece che sorveglianza.
    """
    import main

    spec = main.app.openapi()
    found = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        if path.startswith(ROUTER_PREFIX)
        for method in operations
    }
    assert found == {
        ("GET", f"{ROUTER_PREFIX}/me"),
        # P27-2
        ("GET", f"{ROUTER_PREFIX}/agencies"),
        ("POST", f"{ROUTER_PREFIX}/agencies"),
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}"),
        # P27-3
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"),
        ("POST", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"),
        ("GET", f"{ROUTER_PREFIX}/operators/{{operator_user_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/operators/{{operator_user_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"
                  "/{operator_user_id}/membership"),
        ("PUT", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/owner"),
        # P27-4
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/configuration"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/configuration"),
    }, sorted(found)


def test_g8_every_mutation_in_the_package_goes_through_the_shared_order():
    """UNA copia dell'ordine, e SETTE mutazioni che ci passano tutte.

    L'uguaglianza esatta e' il perno: un `>=` lascerebbe passare una mutazione
    nuova che committa per conto suo, che e' precisamente il difetto che questo
    test esiste per intercettare. Anche questo si sposta con la fase.
    """
    package = ROOT / "platform_admin"

    commits = {
        path.name: path.read_text(encoding="utf-8").count("conn.commit()")
        for path in sorted(package.glob("*.py"))
        if path.name != "database.py"
    }
    assert sum(commits.values()) == 1, commits
    assert commits["transaction.py"] == 1, commits

    callers = []
    for path in sorted(package.glob("*_service.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callers += [
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "audit_then_commit"
                for inner in ast.walk(node)
            )
        ]
    assert set(callers) == {
        "create_agency", "update_agency",                       # P27-2
        "create_agency_operator", "update_operator",            # P27-3
        "update_membership", "transfer_owner",                  # P27-3
        "update_configuration",                                 # P27-4
    }, sorted(callers)


# ===========================================================================
# H - CONFIGURAZIONE PERSISTITA CORROTTA
#
# Una chiave CONOSCIUTA presente con un valore fuori contratto non e' una
# chiave assente: e' uno stato persistito illeggibile. Non si sostituisce con
# un default - l'agenzia lavorerebbe su un valore e la riga ne direbbe un
# altro, per sempre - e non e' colpa di chi chiama.
#
# Resta pero' RIPARABILE, ed e' la meta' che rende la regola sostenibile.
# ===========================================================================

CORROTTO = {"timezone": "Mars/Olympus", "legacy_x": 123}


def test_h1_a_patch_that_replaces_the_corrupted_field_succeeds(service):
    """LA RIPARAZIONE.

    Il campo corrotto e' proprio quello sostituito, quindi il risultato del
    merge e' leggibile e la scrittura avviene.
    """
    service["store"] = Store(dict(CORROTTO))
    risultato = _update(service, timezone="Europe/Rome")
    assert risultato == {"timezone": "Europe/Rome",
                         "locale": CONFIGURATION_DEFAULT_LOCALE}


def test_h1_the_repair_preserves_the_legacy_keys(service):
    service["store"] = Store(dict(CORROTTO))
    _update(service, timezone="Europe/Rome")
    stored = service["store"].rows[AGENCY]["settings"]
    assert stored == {"timezone": "Europe/Rome", "legacy_x": 123}


def test_h1_the_agency_is_readable_again_after_the_repair(service):
    service["store"] = Store(dict(CORROTTO))
    _update(service, timezone="Europe/Rome")
    assert configuration_service.get_configuration(AGENCY)["timezone"] == "Europe/Rome"


def test_h1_the_repair_audit_carries_only_the_field_name(service):
    service["store"] = Store(dict(CORROTTO))
    _update(service, timezone="Europe/Rome")
    entry = service["operations"]()[-1]
    assert entry["metadata"] == {"changed_fields": ["timezone"]}
    blob = repr(entry)
    for leak in ("Mars/Olympus", "Europe/Rome", "legacy_x", "123"):
        assert leak not in blob, (leak, blob)


def test_h2_a_patch_on_another_field_fails_while_a_known_key_stays_corrupted(service):
    """Scriverebbe una riga che contiene ancora un timezone illeggibile, e la
    GET successiva continuerebbe a dare 500. Si ferma prima."""
    service["store"] = Store(dict(CORROTTO))
    with pytest.raises(AgencyConfigurationCorrupted):
        _update(service, locale=CONFIGURATION_DEFAULT_LOCALE)


def test_h2_nothing_is_written_when_the_patch_would_leave_corruption(service):
    service["store"] = Store(dict(CORROTTO))
    with pytest.raises(AgencyConfigurationCorrupted):
        _update(service, locale=CONFIGURATION_DEFAULT_LOCALE)
    assert service["store"].rows[AGENCY]["settings"] == CORROTTO
    assert "write" not in service["order"], service["order"]
    assert service["conn"].events == ["rollback"], service["conn"].events


def test_h2_no_audit_row_is_written_for_a_refused_patch(service):
    service["store"] = Store(dict(CORROTTO))
    with pytest.raises(AgencyConfigurationCorrupted):
        _update(service, locale=CONFIGURATION_DEFAULT_LOCALE)
    assert service["operations"]() == []


def test_h3_a_patch_that_repairs_both_fields_at_once_succeeds(service):
    service["store"] = Store({"timezone": "Mars/Olympus", "locale": "fr-FR",
                              "legacy_x": 123})
    assert _update(service, timezone="UTC", locale=CONFIGURATION_DEFAULT_LOCALE) == {
        "timezone": "UTC", "locale": CONFIGURATION_DEFAULT_LOCALE
    }
    assert service["store"].rows[AGENCY]["settings"]["legacy_x"] == 123


def test_h4_the_validation_happens_before_the_write_not_after(service):
    """Una scrittura poi annullata dipenderebbe dal rollback per una cosa che
    si puo' semplicemente non fare."""
    service["store"] = Store(dict(CORROTTO))
    with pytest.raises(AgencyConfigurationCorrupted):
        _update(service, locale=CONFIGURATION_DEFAULT_LOCALE)
    assert service["order"] == ["open", "rollback"], service["order"]


# --- HTTP -------------------------------------------------------------------

def test_h5_a_corrupted_get_is_500_with_a_generic_message(client, service):
    from platform_admin.enums import CONFIGURATION_CORRUPTED_MESSAGE

    service["store"] = Store(dict(CORROTTO))
    response = client.get(CONFIG)
    assert response.status_code == 500, response.text
    assert response.json() == {"detail": CONFIGURATION_CORRUPTED_MESSAGE}


@pytest.mark.parametrize("stored", [
    {"timezone": "Mars/Olympus"},
    {"locale": "fr-FR"},
])
def test_h5_the_corrupted_value_never_appears_in_the_response(client, service, stored):
    """Ne' il valore, ne' il campo, ne' un dettaglio interno."""
    service["store"] = Store({**stored, "legacy_x": 123})
    body = client.get(CONFIG).text
    for leak in ("Mars/Olympus", "fr-FR", "timezone", "locale", "legacy_x",
                 "settings", "agencies", "ValidationError", "Traceback",
                 "zoneinfo", "pydantic"):
        assert leak not in body, (leak, body)


def test_h6_a_repairing_patch_is_200_over_http(client, service):
    service["store"] = Store(dict(CORROTTO))
    response = client.patch(CONFIG, json={"timezone": "Europe/Rome"})
    assert response.status_code == 200, response.text
    assert response.json()["timezone"] == "Europe/Rome"
    assert client.get(CONFIG).status_code == 200


def test_h6_a_partial_patch_on_a_corrupted_row_is_500_over_http(client, service):
    from platform_admin.enums import CONFIGURATION_CORRUPTED_MESSAGE

    service["store"] = Store(dict(CORROTTO))
    response = client.patch(CONFIG, json={"locale": CONFIGURATION_DEFAULT_LOCALE})
    assert response.status_code == 500, response.text
    assert response.json() == {"detail": CONFIGURATION_CORRUPTED_MESSAGE}
    assert service["store"].rows[AGENCY]["settings"] == CORROTTO


def test_h7_corruption_is_never_reported_as_a_client_error(client, service):
    """Non 422 e non 409: chi chiama non ha sbagliato nulla e non puo' farci
    niente. Attribuirglielo lo manderebbe a correggere una richiesta che non ha
    problemi."""
    service["store"] = Store(dict(CORROTTO))
    assert client.get(CONFIG).status_code == 500
    assert client.patch(CONFIG, json={"locale": CONFIGURATION_DEFAULT_LOCALE}
                        ).status_code == 500


def test_h8_the_service_has_no_silent_fallback_left():
    """Strutturale: nessun `except` che inghiotta una validazione e prosegua.

    Era esattamente cosi' che il valore corrotto diventava un default.
    """
    source = (ROOT / "platform_admin" / "configuration_service.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        corpo = ast.unparse(node)
        assert "continue" not in corpo or "corrupted.append" in corpo, corpo
    assert "AgencyConfigurationCorrupted" in source
