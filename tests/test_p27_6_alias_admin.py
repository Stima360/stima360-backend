"""P27-6 - la superficie che DICHIARA gli alias, su /api/platform.

Tre operazioni e nessuna DELETE: elencare, dichiarare, ripuntare-o-revocare.
Sono il "qualcuno" che dice al routing quale testo in ingresso appartiene a
quale territorio - senza il quale P27-6 dovrebbe indovinarlo, che e' esattamente
l'errore che ha fatto fermare la prima versione.

LO STORE RIPRODUCE I VINCOLI VERI

`uq_territory_alias_active_value` esiste qui dentro, altrimenti nessun test fuori
da PostgreSQL potrebbe vederlo e una mutazione che toglie il controllo di
conflitto dal service passerebbe perche' non c'e' nessun altro a dire di no.
E registra l'ordine delle scritture TENTATE: uno store che fa rispettare il
vincolo puo' mascherare un codice sbagliato, perche' il risultato finale resta
corretto - l'ordine no.

Che la normalizzazione dello store coincida con quella dell'indice lo prova
`tests/test_p27_6_postgres_real.py`, dove l'indice e' quello vero.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg2 import errors

ROOT = Path(__file__).resolve().parents[1]

from operator_auth.context import OperatorContext  # noqa: E402
from platform_admin import aliases_service  # noqa: E402
from platform_admin.enums import (  # noqa: E402
    ACTION_TERRITORY_ALIAS_CREATE,
    ACTION_TERRITORY_ALIAS_UPDATE,
    ALIAS_ACTIVE,
    ALIAS_MATCH_VALUE_MAX,
    ALIAS_REVOKED,
    ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
    TARGET_TYPE_TERRITORY_ALIAS,
)
from platform_admin.exceptions import (  # noqa: E402
    AliasKindRefused,
    AliasNotFound,
    PlatformConflict,
    TerritoryNotFound,
)
from platform_admin.schemas import (  # noqa: E402
    TerritoryAliasCreateRequest,
    TerritoryAliasUpdateRequest,
)

CREATO = datetime(2026, 1, 1, tzinfo=timezone.utc)
DOPO = datetime(2026, 2, 1, tzinfo=timezone.utc)
COMUNE, PROVINCIA, CAP = 100, 200, 300


def _ctx(**sovrascritture) -> OperatorContext:
    base = dict(
        user_id=9, agency_id=None, role=None, is_platform_admin=True,
        session_id=1, auth_channel="operator_session",
    )
    base.update(sovrascritture)
    return OperatorContext(**base)


class FakeUnique(errors.UniqueViolation):
    """Sottoclasse vera: il service cattura `errors.UniqueViolation`."""

    def __init__(self, vincolo: str):
        super().__init__(vincolo)
        self._vincolo = vincolo

    @property
    def diag(self):
        return SimpleNamespace(constraint_name=self._vincolo)


def _norm(valore: str) -> str:
    return " ".join(str(valore).split()).lower()


class Store:
    """Territori e alias, con l'indice unico parziale riprodotto."""

    def __init__(self):
        self.territori = {
            COMUNE: {"id": COMUNE, "kind": "municipality",
                     "canonical_key": "067001", "label": "Alba Adriatica"},
            PROVINCIA: {"id": PROVINCIA, "kind": "province",
                        "canonical_key": "te", "label": "Teramo"},
            CAP: {"id": CAP, "kind": "postal_code",
                  "canonical_key": "64011", "label": "64011"},
        }
        self.alias: dict[int, dict] = {}
        self.scritture: list[str] = []
        self._prossimo = 500
        self._istantanea = None

    # -- transazione simulata ------------------------------------------------
    def snapshot(self):
        self._istantanea = {k: dict(v) for k, v in self.alias.items()}

    def restore(self):
        if self._istantanea is not None:
            self.alias = self._istantanea
            self._istantanea = None

    def commit(self):
        self._istantanea = None

    # -- il vincolo ----------------------------------------------------------
    def _guardia(self, source, match_value, escluso=None):
        for riga in self.alias.values():
            if (riga["source"] == source
                    and riga["status"] == ALIAS_ACTIVE
                    and riga["id"] != escluso
                    and _norm(riga["match_value"]) == _norm(match_value)):
                raise FakeUnique("uq_territory_alias_active_value")

    # -- scritture -----------------------------------------------------------
    def create(self, *, territory_id, source, match_value):
        self.scritture.append("create-alias")
        self._guardia(source, match_value)
        self._prossimo += 1
        riga = {
            "id": self._prossimo, "territory_id": territory_id, "source": source,
            "match_value": match_value, "status": ALIAS_ACTIVE,
            "created_at": CREATO, "updated_at": CREATO,
        }
        self.alias[riga["id"]] = riga
        return dict(riga)

    def update(self, alias_id, *, territory_id=None, status=None):
        self.scritture.append(f"update-alias:{status}")
        riga = self.alias.get(alias_id)
        if riga is None:
            return None
        if territory_id is not None:
            riga["territory_id"] = territory_id
        if status is not None:
            riga["status"] = status
        riga["updated_at"] = DOPO
        return dict(riga)

    def attivo_per(self, source, match_value):
        for riga in self.alias.values():
            if (riga["source"] == source and riga["status"] == ALIAS_ACTIVE
                    and _norm(riga["match_value"]) == _norm(match_value)):
                return dict(riga)
        return None

    def semina(self, territory_id=COMUNE, valore="Alba Adriatica",
               stato=ALIAS_ACTIVE):
        riga = self.create(
            territory_id=territory_id,
            source=ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
            match_value=valore,
        )
        if stato != ALIAS_ACTIVE:
            self.alias[riga["id"]]["status"] = stato
            riga = dict(self.alias[riga["id"]])
        return riga


class FakeConn:
    def __init__(self, store, ordine, commit_fallisce=False):
        self.store, self.ordine = store, ordine
        self.commit_fallisce = commit_fallisce

    def commit(self):
        self.ordine.append("commit")
        if self.commit_fallisce:
            raise RuntimeError("il commit e' fallito")
        self.store.commit()

    def rollback(self):
        self.ordine.append("rollback")
        self.store.restore()


@pytest.fixture
def service(monkeypatch):
    stato = {"store": Store(), "audit": [], "ordine": [], "commit_fallisce": False,
             "audit_fallisce": False, "sessione": _ctx()}

    @contextmanager
    def _cursore():
        stato["ordine"].append("open")
        conn = FakeConn(stato["store"], stato["ordine"], stato["commit_fallisce"])
        stato["store"].snapshot()
        try:
            yield conn, object()
        except Exception:
            conn.rollback()
            raise

    monkeypatch.setattr(aliases_service, "platform_operation_cursor", _cursore)

    store = stato["store"]
    monkeypatch.setattr(
        aliases_service.territories_repository, "get_territory",
        lambda cur, tid: dict(store.territori[tid]) if tid in store.territori else None,
    )

    repo = aliases_service.aliases_repository
    monkeypatch.setattr(
        repo, "get_alias",
        lambda cur, aid: dict(store.alias[aid]) if aid in store.alias else None,
    )
    monkeypatch.setattr(
        repo, "active_alias_for_value",
        lambda cur, *, source, match_value: store.attivo_per(source, match_value),
    )
    monkeypatch.setattr(
        repo, "create_alias",
        lambda cur, *, territory_id, source, match_value: store.create(
            territory_id=territory_id, source=source, match_value=match_value
        ),
    )
    monkeypatch.setattr(
        repo, "update_alias",
        lambda cur, aid, *, territory_id=None, status=None: store.update(
            aid, territory_id=territory_id, status=status
        ),
    )
    monkeypatch.setattr(
        repo, "list_territory_aliases",
        lambda cur, tid: sorted(
            (dict(r) for r in store.alias.values() if r["territory_id"] == tid),
            key=lambda r: (r["status"], r["match_value"], r["id"]),
        ),
    )

    def _registra(**kwargs):
        if stato["audit_fallisce"] and kwargs.get("action") != "platform.admission":
            stato["ordine"].append("audit-failed")
            from platform_admin.audit import PlatformAuditUnavailable

            raise PlatformAuditUnavailable("indisponibile")
        stato["ordine"].append("audit")
        stato["audit"].append(kwargs)
        return len(stato["audit"])

    from platform_admin import audit as platform_audit

    monkeypatch.setattr(platform_audit, "record", _registra)
    return stato


def _crea(service, territory_id=COMUNE, valore="Alba Adriatica"):
    return aliases_service.create_alias(
        service["sessione"], territory_id,
        source=ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
        match_value=valore,
        created_fields=["source", "match_value"],
    )


# ---------------------------------------------------------------------------
# A - dichiarare
# ---------------------------------------------------------------------------

def test_a1_an_alias_is_declared_active_and_audited(service):
    alias = _crea(service)
    assert alias["status"] == ALIAS_ACTIVE
    assert alias["territory_id"] == COMUNE
    assert alias["match_value"] == "Alba Adriatica"

    (registrato,) = service["audit"]
    assert registrato["action"] == ACTION_TERRITORY_ALIAS_CREATE
    assert registrato["target_type"] == TARGET_TYPE_TERRITORY_ALIAS
    assert registrato["target_id"] == alias["id"]
    assert registrato["metadata"]["territory_id"] == COMUNE


def test_a2_the_audit_precedes_the_commit(service):
    """L'ordine condiviso: apri, scrivi, audita, committa.

    Se il commit venisse prima, un audit fallito lascerebbe un alias dichiarato
    e nessuna riga che dica chi lo ha dichiarato.
    """
    _crea(service)
    assert service["ordine"] == ["open", "audit", "commit"], service["ordine"]


def test_a3_a_failing_audit_leaves_no_alias_behind(service):
    service["audit_fallisce"] = True
    from platform_admin.audit import PlatformAuditUnavailable

    with pytest.raises(PlatformAuditUnavailable):
        _crea(service)
    assert service["store"].alias == {}
    assert "rollback" in service["ordine"]
    assert "commit" not in service["ordine"]


def test_a4_a_failing_commit_leaves_no_alias_behind(service):
    service["commit_fallisce"] = True
    with pytest.raises(RuntimeError):
        _crea(service)
    assert service["store"].alias == {}


def test_a5_the_audit_never_carries_an_agency(service):
    """`target_agency_id` e' None, come per la creazione di un territorio.

    Un alias dice a quale TERRITORIO appartiene un nome. Chi presidia quel
    territorio e' un fatto separato che cambiera' - inciderlo dentro questo
    atto lo renderebbe una mezza verita' il giorno del primo trasferimento.
    """
    _crea(service)
    assert service["audit"][0]["target_agency_id"] is None


# ---------------------------------------------------------------------------
# B - il territorio deve esistere ed essere un comune
# ---------------------------------------------------------------------------

def test_b1_an_unknown_territory_is_404(service):
    with pytest.raises(TerritoryNotFound):
        _crea(service, territory_id=999_999)
    assert service["store"].scritture == []


@pytest.mark.parametrize("territorio", [PROVINCIA, CAP])
def test_b2_a_non_municipality_territory_is_refused(service, territorio):
    """422, e nessuna scrittura tentata.

    La query di routing filtra comunque su `kind = 'municipality'` - e' quella
    la difesa, perche' vale anche su una riga scritta in SQL. Questo rifiuto
    esiste perche' un errore che nomina il campo vale piu' di un alias che non
    instrada mai e non dice perche'.
    """
    with pytest.raises(AliasKindRefused):
        _crea(service, territory_id=territorio)
    assert service["store"].scritture == []


def test_b3_repointing_to_a_non_municipality_is_refused_too(service):
    alias = _crea(service)
    with pytest.raises(AliasKindRefused):
        aliases_service.update_alias(
            service["sessione"], alias["id"],
            territory_id=PROVINCIA, updated_fields=["territory_id"],
        )
    assert service["store"].alias[alias["id"]]["territory_id"] == COMUNE


# ---------------------------------------------------------------------------
# C - il conflitto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("secondo", [
    "Alba Adriatica", "alba adriatica", "  ALBA   ADRIATICA  ",
])
def test_c1_the_same_normalised_value_twice_is_a_conflict(service, secondo):
    _crea(service)
    with pytest.raises(PlatformConflict):
        _crea(service, valore=secondo)


def test_c2_the_race_is_a_conflict_and_not_a_500(service, monkeypatch):
    """Chi perde la corsa fra il controllo e la INSERT riceve lo stesso 409.

    Il controllo preventivo non vede niente - e' il vincolo a rifiutare - e
    senza la cattura il cliente riceverebbe un 500 col nome di un indice.
    """
    monkeypatch.setattr(
        aliases_service.aliases_repository, "active_alias_for_value",
        lambda cur, *, source, match_value: None,
    )
    service["store"].semina()
    with pytest.raises(PlatformConflict):
        _crea(service, valore="alba adriatica")


def test_c3_a_revoked_value_can_be_declared_again(service):
    """Revocato un nome, lo si puo' ridichiarare - altrove o qui.

    E' la meta' parziale dell'indice: senza, un comune tolto a un'agenzia non
    potrebbe piu' essere dato a nessuno.
    """
    alias = _crea(service)
    aliases_service.update_alias(
        service["sessione"], alias["id"],
        status=ALIAS_REVOKED, updated_fields=["status"],
    )
    nuovo = _crea(service, valore="alba adriatica")
    assert nuovo["id"] != alias["id"]
    assert nuovo["status"] == ALIAS_ACTIVE


def test_c4_two_territories_cannot_hold_the_same_value(service):
    """Il conflitto e' sul VALORE, non sulla coppia territorio-valore.

    E' questo che rende impossibile l'ambiguita' che il routing dovrebbe
    altrimenti risolvere scegliendo.
    """
    _crea(service)
    service["store"].territori[101] = {
        "id": 101, "kind": "municipality", "canonical_key": "067002",
        "label": "Altro Comune",
    }
    with pytest.raises(PlatformConflict):
        _crea(service, territory_id=101, valore="ALBA ADRIATICA")


# ---------------------------------------------------------------------------
# D - ripuntare e revocare
# ---------------------------------------------------------------------------

def test_d1_an_alias_can_be_repointed(service):
    alias = _crea(service)
    service["store"].territori[101] = {
        "id": 101, "kind": "municipality", "canonical_key": "067002",
        "label": "Altro Comune",
    }
    aggiornato = aliases_service.update_alias(
        service["sessione"], alias["id"],
        territory_id=101, updated_fields=["territory_id"],
    )
    assert aggiornato["territory_id"] == 101
    assert aggiornato["status"] == ALIAS_ACTIVE
    assert service["audit"][-1]["metadata"]["from_territory_id"] == COMUNE


def test_d2_an_alias_can_be_revoked(service):
    alias = _crea(service)
    aggiornato = aliases_service.update_alias(
        service["sessione"], alias["id"],
        status=ALIAS_REVOKED, updated_fields=["status"],
    )
    assert aggiornato["status"] == ALIAS_REVOKED
    assert service["audit"][-1]["action"] == ACTION_TERRITORY_ALIAS_UPDATE
    assert service["audit"][-1]["metadata"]["from_status"] == ALIAS_ACTIVE


@pytest.mark.parametrize("verso", [ALIAS_ACTIVE, ALIAS_REVOKED])
def test_d3_revoked_is_final(service, verso):
    """revoked -> qualunque cosa: 409.

    Riportare in vita una riga revocata cancellerebbe il periodo in cui quel
    nome non valeva - e in quel periodo i lead sono andati altrove, che e'
    precisamente cio' che il registro serve a poter ricostruire.
    """
    alias = service["store"].semina(stato=ALIAS_REVOKED)
    with pytest.raises(PlatformConflict):
        aliases_service.update_alias(
            service["sessione"], alias["id"],
            status=verso, updated_fields=["status"],
        )


def test_d4_an_unknown_alias_is_404(service):
    with pytest.raises(AliasNotFound):
        aliases_service.update_alias(
            service["sessione"], 999_999,
            status=ALIAS_REVOKED, updated_fields=["status"],
        )


def test_d5_repointing_and_revoking_together_is_one_act(service):
    """"Questo nome non e' piu' di nessuno, e non era di chi credevi".

    Due richieste separate darebbero due righe di registro per un atto solo.
    """
    alias = _crea(service)
    service["store"].territori[101] = {
        "id": 101, "kind": "municipality", "canonical_key": "067002",
        "label": "Altro Comune",
    }
    aggiornato = aliases_service.update_alias(
        service["sessione"], alias["id"], territory_id=101,
        status=ALIAS_REVOKED, updated_fields=["territory_id", "status"],
    )
    assert (aggiornato["territory_id"], aggiornato["status"]) == (101, ALIAS_REVOKED)
    assert len([a for a in service["audit"]
                if a["action"] == ACTION_TERRITORY_ALIAS_UPDATE]) == 1


# ---------------------------------------------------------------------------
# E - elencare
# ---------------------------------------------------------------------------

def test_e1_the_list_shows_active_and_revoked(service):
    attivo = _crea(service)
    revocato = service["store"].semina(valore="Alba", stato=ALIAS_REVOKED)
    elenco = aliases_service.list_territory_aliases(service["sessione"], COMUNE)
    assert [r["id"] for r in elenco] == [attivo["id"], revocato["id"]]


def test_e2_reading_writes_no_audit_row(service):
    """Una lettura non e' un atto: l'ammissione di P27-1 e' gia' la traccia."""
    _crea(service)
    service["audit"].clear()
    aliases_service.list_territory_aliases(service["sessione"], COMUNE)
    assert service["audit"] == []


def test_e3_listing_an_unknown_territory_is_404(service):
    with pytest.raises(TerritoryNotFound):
        aliases_service.list_territory_aliases(service["sessione"], 999_999)


# ---------------------------------------------------------------------------
# F - lo schema della richiesta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grezzo,atteso", [
    ("  Alba Adriatica  ", "Alba Adriatica"),
    ("Alba   Adriatica", "Alba Adriatica"),
    ("\tAlba\n Adriatica ", "Alba Adriatica"),
])
def test_f1_the_request_trims_and_collapses_and_nothing_else(grezzo, atteso):
    """Le due pieghe che un valore SCRITTO subisce, e nessuna terza.

    Le maiuscole restano: sono come l'amministratore lo ha scritto, ed e' quel
    che l'interfaccia gli rimostrera'. A confrontare senza badarci ci pensa
    l'SQL, dove il confronto avviene davvero.
    """
    richiesta = TerritoryAliasCreateRequest(
        source=ALIAS_SOURCE_PUBLIC_STIMA_COMUNE, match_value=grezzo
    )
    assert richiesta.match_value == atteso


@pytest.mark.parametrize("valore", ["", "   ", "\t\n"])
def test_f2_an_empty_value_is_refused(valore):
    with pytest.raises(ValueError):
        TerritoryAliasCreateRequest(
            source=ALIAS_SOURCE_PUBLIC_STIMA_COMUNE, match_value=valore
        )


def test_f3_a_value_longer_than_the_column_is_refused():
    with pytest.raises(ValueError):
        TerritoryAliasCreateRequest(
            source=ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
            match_value="a" * (ALIAS_MATCH_VALUE_MAX + 1),
        )


def test_f4_an_unknown_source_is_refused():
    """Una sola sorgente esiste. Non si progetta oggi il giorno in cui ce ne
    sara' un'altra: si rifiuta tutto il resto."""
    with pytest.raises(ValueError):
        TerritoryAliasCreateRequest(source="qualcos_altro", match_value="Alba")


def test_f5_the_create_request_cannot_carry_a_status():
    """Un alias nasce attivo. Dichiararne uno gia' revocato sarebbe scrivere
    storia che non e' accaduta."""
    with pytest.raises(ValueError):
        TerritoryAliasCreateRequest(
            source=ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
            match_value="Alba", status=ALIAS_REVOKED,
        )


def test_f6_an_empty_patch_is_refused():
    with pytest.raises(ValueError):
        TerritoryAliasUpdateRequest()


def test_f7_the_patch_cannot_change_the_value(service):
    """Il valore non si corregge: si revoca e si ridichiara.

    Cambiarlo in silenzio riscriverebbe a ritroso quale nome ha instradato i
    lead gia' arrivati.
    """
    with pytest.raises(ValueError):
        TerritoryAliasUpdateRequest(match_value="Alba Adriatica")


def test_f8_the_patch_cannot_set_an_arbitrary_status():
    with pytest.raises(ValueError):
        TerritoryAliasUpdateRequest(status="sospeso")


# ---------------------------------------------------------------------------
# G - LA RISPOSTA, NON SOLO LA DECISIONE
#
# P27-8 ha trovato su TEST un 500 che nessuno dei test qui sopra poteva vedere:
# l'alias veniva CREATO - il tentativo duplicato subito dopo rispondeva 409 -
# ma la risposta HTTP falliva. Tutti i test di questo file chiamano il SERVICE,
# e il service restituisce la riga; nessuno faceva passare quella riga per lo
# schema di risposta, che e' il pezzo che FastAPI esegue subito dopo.
#
# La causa era una sola parola: `TerritoryAliasResponse` ereditava da
# `PlatformModel`, la base dei CORPI DI RICHIESTA, che vieta i campi extra
# apposta - cosi' un client non puo' fingere di impostare `id` o `created_at`.
# Applicata a una RISPOSTA, quella stessa regola rifiuta le colonne che il
# repository legge davvero (`created_at`, `updated_at`) e trasforma una riga
# valida in un 500.
#
# Le altre risposte della superficie - AgencyResponse, TerritoryResponse,
# OperatorResponse, AssignmentResponse - ereditano tutte da `BaseModel`. Questa
# era l'unica fuori riga.
# ---------------------------------------------------------------------------

def test_g1_the_alias_response_accepts_the_row_the_repository_returns():
    """La riga vera, con tutte le sue colonne, deve poter essere serializzata.

    E' esattamente cio' che il router fa: `TerritoryAliasResponse(**row)` su
    quel che il service ha restituito.
    """
    from platform_admin.aliases_repository import ALIAS_FIELDS
    from platform_admin.schemas import TerritoryAliasResponse

    riga = {
        "id": 900, "territory_id": 30, "source": ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
        "match_value": "P27E2E Borgo", "status": ALIAS_ACTIVE,
        "created_at": CREATO, "updated_at": DOPO,
    }
    # Tutte le colonne che il repository dichiara di leggere sono qui dentro:
    # se un domani ne aggiungesse una, questo test la pretende nella riga e la
    # risposta la deve accettare.
    assert set(ALIAS_FIELDS) <= set(riga), set(ALIAS_FIELDS) - set(riga)

    risposta = TerritoryAliasResponse(**riga)
    assert risposta.id == 900
    assert risposta.match_value == "P27E2E Borgo"


def test_g2_no_response_model_of_the_platform_surface_forbids_extra_fields():
    """`PlatformModel` e' per le RICHIESTE. Nessuna risposta deve ereditarla.

    Il divieto di campi extra protegge l'ingresso - un client non deve poter
    nominare `id` o `agency_id` - e sull'uscita fa il danno opposto: rifiuta le
    colonne che il database ha davvero.
    """
    import inspect

    from platform_admin import schemas

    colpevoli = [
        nome for nome, cls in vars(schemas).items()
        if inspect.isclass(cls) and nome.endswith(("Response", "Item"))
        and issubclass(cls, schemas.PlatformModel)
    ]
    assert not colpevoli, colpevoli


# ---------------------------------------------------------------------------
# H - LE ROUTE VERE, ATTRAVERSO FastAPI
#
# I test G provano lo schema; questi provano il PASSAGGIO che rompeva davvero:
# la risposta che FastAPI costruisce dal valore restituito dal gestore. E' il
# solo modo di vedere un 500 che nasce dopo la scrittura - la riga c'e', il
# service ha fatto il suo, e la richiesta fallisce lo stesso.
#
# Il router e' quello vero, montato come in `main.py` con
# `require_platform_admin` sul mount: cosi' 401/403 sono quelli veri e il
# `response_model` viene applicato come in produzione.
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from platform_admin import dependencies as platform_deps  # noqa: E402
from platform_admin.router import router as platform_router  # noqa: E402

SCADENZA = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def http(service, monkeypatch):
    from operator_auth import dependencies as operator_deps

    def _sessione(_token):
        return {"context": service["sessione"], "agency_name": None,
                "expires_at": SCADENZA}

    monkeypatch.setattr(operator_deps.service, "session_from_token", _sessione)

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("stima360_operator_session", "un-token")
    return client


def test_h1_creating_an_alias_answers_201_and_not_500(http, service):
    """IL BUG DI P27-8, come test HTTP.

    Prima del fix questa POST rispondeva 500 pur avendo scritto la riga: lo
    schema di risposta rifiutava `created_at` e `updated_at`.
    """
    risposta = http.post(
        f"/api/platform/territories/{COMUNE}/aliases",
        json={"source": ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
              "match_value": "P27E2E Borgo"},
    )
    assert risposta.status_code == 201, risposta.text
    corpo = risposta.json()
    assert corpo["match_value"] == "P27E2E Borgo"
    assert corpo["status"] == ALIAS_ACTIVE
    assert corpo["source"] == ALIAS_SOURCE_PUBLIC_STIMA_COMUNE
    # La riga esiste davvero nello store: la risposta non e' una finzione.
    assert len(service["store"].alias) == 1


def test_h2_listing_aliases_answers_200_and_not_500(http, service):
    service["store"].semina(valore="P27E2E Borgo")
    risposta = http.get(f"/api/platform/territories/{COMUNE}/aliases")
    assert risposta.status_code == 200, risposta.text
    elenco = risposta.json()
    assert len(elenco) == 1
    assert elenco[0]["match_value"] == "P27E2E Borgo"


def test_h3_revoking_an_alias_answers_200_and_not_500(http, service):
    alias = service["store"].semina(valore="P27E2E Borgo")
    risposta = http.patch(
        f"/api/platform/aliases/{alias['id']}", json={"status": ALIAS_REVOKED},
    )
    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["status"] == ALIAS_REVOKED


def test_h4_a_duplicate_is_still_a_409_through_the_route(http, service):
    """Il 409 funzionava anche prima del fix - e' cio' che ha svelato il bug.

    Su TEST la POST rispondeva 500 e il duplicato subito dopo rispondeva 409:
    due risposte incompatibili, a meno che la riga non fosse stata scritta.
    """
    service["store"].semina(valore="P27E2E Borgo")
    risposta = http.post(
        f"/api/platform/territories/{COMUNE}/aliases",
        json={"source": ALIAS_SOURCE_PUBLIC_STIMA_COMUNE,
              "match_value": "  p27e2e   borgo "},
    )
    assert risposta.status_code == 409, risposta.text
    assert "gia'" in risposta.json()["detail"]


def test_h5_the_response_never_carries_a_password_hash(http, service):
    service["store"].semina(valore="P27E2E Borgo")
    risposta = http.get(f"/api/platform/territories/{COMUNE}/aliases")
    assert "password" not in risposta.text.lower()
