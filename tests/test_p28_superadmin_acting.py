"""P28 - il Superadmin che ENTRA in un'agenzia, senza bypassare nulla.

LA REGOLA CHE QUESTO FILE DIFENDE

    il Platform Superadmin non vede i dati di un'agenzia perche' e' platform
    admin. Li vede perche' ha DICHIARATO, con un atto registrato, di stare
    operando dentro quell'agenzia - e mentre lo fa e' scopato dallo stesso
    predicate che scopa chiunque altro.

Non esiste, e questo file lo prova, nessun ramo `if is_platform_admin: passa
tutto`. Il contesto di acting cambia QUALE agenzia il predicate riceve, mai
SE il predicate viene applicato.

LE TRE PROPRIETA' CHE SOLO L'ESECUZIONE MOSTRA

1. L'acting vive nella riga di sessione, quindi il client non puo' sceglierlo:
   nessun header, nessun corpo, nessun parametro lo influenza. I test della
   sezione F lo provano mandando davvero quei tre vettori.

2. L'acting non e' "ignorato quando non e' piu' valido": viene CANCELLATO.
   Togliere `is_platform_admin` o sospendere l'agenzia azzera le due colonne
   alla prima richiesta successiva, e la sezione C lo verifica guardando le
   scritture, non solo il contesto restituito.

3. L'uscita non puo' fallire. L'ingresso si', e deve: un ingresso senza audit
   non e' avvenuto. Ma un'uscita bloccata da un audit indisponibile lascerebbe
   il Superadmin dentro l'agenzia, che e' il peggiore dei due esiti. La
   sezione E prova l'asimmetria in entrambi i versi.

L'ORDINE DEI DOPPI

Lo store riproduce cio' che il database garantisce - la riga di sessione, lo
stato dell'agenzia - e REGISTRA l'ordine delle scritture tentate. Uno store che
fa rispettare i vincoli puo' mascherare un service sbagliato, perche' lo stato
finale resta corretto; l'ordine no.
"""

from __future__ import annotations

import re
import textwrap
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from core import scope as core_scope  # noqa: E402
from core.scope import scoped_predicate  # noqa: E402
from operator_auth import repository as auth_repository  # noqa: E402
from operator_auth import service as auth_service  # noqa: E402
from operator_auth.context import OperatorContext  # noqa: E402
from operator_auth.exceptions import PlatformAdminAgencyRequired  # noqa: E402
from platform_admin import acting_service  # noqa: E402
from platform_admin.enums import (  # noqa: E402
    ACTING_AGENCY_NOT_ACTIVE_MESSAGE,
    ACTING_ALREADY_ACTIVE_MESSAGE,
    ACTING_NEEDS_SESSION_MESSAGE,
    ACTION_ACTING_ENTER,
    ACTION_ACTING_EXIT,
    AGENCY_NOT_FOUND_MESSAGE,
    TARGET_TYPE_AGENCY,
)
from platform_admin.exceptions import (  # noqa: E402
    AgencyNotFound,
    PlatformAuditUnavailable,
    PlatformConflict,
)

SCADENZA = datetime(2030, 1, 1, tzinfo=timezone.utc)
ENTRATO = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)

GIORGIO = 9
CASA = 1          # l'agenzia di appartenenza, quando ne ha una
OSPITE = 34       # l'agenzia in cui entra
ALTRA = 77        # l'agenzia in cui NON e' entrato
SESSIONE = 500


def _ctx(**sovrascritture) -> OperatorContext:
    base = dict(
        user_id=GIORGIO, agency_id=None, role=None, is_platform_admin=True,
        session_id=SESSIONE, auth_channel="operator_session",
    )
    base.update(sovrascritture)
    return OperatorContext(**base)


# ---------------------------------------------------------------------------
# A - LA MIGRATION 060
#
# Il testo, non il comportamento: il comportamento su un cluster vero lo prova
# tests/test_p28_acting_postgres_real.py. Qui si difendono le scelte che una
# rilettura distratta cambierebbe senza accorgersene - la FK non distruttiva e
# il CHECK che tiene appaiate le due colonne.
# ---------------------------------------------------------------------------

MIGRAZIONE = ROOT / "migrations" / "060_p28_acting_agency_context.sql"
MIGRAZIONE_DOWN = ROOT / "migrations" / "060_p28_acting_agency_context_down.sql"


def test_a1_the_migration_and_its_down_both_exist():
    assert MIGRAZIONE.exists(), MIGRAZIONE
    assert MIGRAZIONE_DOWN.exists(), MIGRAZIONE_DOWN


def test_a2_it_adds_exactly_the_two_columns_the_design_approved():
    testo = MIGRAZIONE.read_text(encoding="utf-8")
    assert "acting_agency_id" in testo
    assert "acting_entered_at" in testo


def test_a3_the_foreign_key_restricts_and_does_not_cascade():
    """Una FK CASCADE cancellerebbe sessioni insieme a un'agenzia.

    E SET NULL farebbe sparire in silenzio l'unica traccia in linea del fatto
    che qualcuno stava operando li' dentro.
    """
    testo = MIGRAZIONE.read_text(encoding="utf-8")
    assert "ON DELETE RESTRICT" in testo
    assert "ON DELETE CASCADE" not in testo
    assert "ON DELETE SET NULL" not in testo


def test_a4_the_two_columns_are_kept_paired_by_a_check():
    """Un acting senza istante di ingresso e' uno stato che nessuno sa leggere."""
    testo = MIGRAZIONE.read_text(encoding="utf-8").lower()
    assert "check" in testo
    assert "acting_entered_at is null" in testo


def test_a5_the_down_removes_the_ledger_row_by_stem():
    testo = MIGRAZIONE_DOWN.read_text(encoding="utf-8")
    assert "060_p28_acting_agency_context" in testo
    assert "DELETE FROM schema_migrations" in testo


def _istruzioni_sql(testo: str) -> list[str]:
    """Le tabelle nominate da un'ISTRUZIONE, ignorando i commenti.

    Il commento che spiega perche' una tabella non viene toccata e' il posto in
    cui quella decisione sopravvive; un test che lo vietasse premierebbe il
    silenzio. Cio' che deve restare assente e' il nome dentro un'istruzione.
    """
    righe = [
        r for r in testo.splitlines()
        if r.strip() and not r.strip().startswith("--")
    ]
    return re.findall(
        r"(?:FROM|UPDATE|INTO|JOIN|TABLE)\s+([a-z_]+)", "\n".join(righe)
    )


def test_a6_the_migration_never_writes_a_membership():
    """P28 non passa MAI da `agency_memberships`. Nemmeno in migration.

    Se ci passasse, uscire da un'agenzia vorrebbe dire cancellare una riga di
    appartenenza, e la differenza fra "ha lavorato qui un pomeriggio" e "e'
    stato dei nostri" sparirebbe dal database.
    """
    for percorso in (MIGRAZIONE, MIGRAZIONE_DOWN):
        tabelle = _istruzioni_sql(percorso.read_text(encoding="utf-8"))
        assert "agency_memberships" not in tabelle, (percorso.name, tabelle)


# ---------------------------------------------------------------------------
# B - LA QUERY DI RISOLUZIONE
#
# `resolve_session` e' l'unico punto in cui un cookie diventa uno scope. Se
# l'acting non comparisse li', comparirebbe da qualche altra parte - cioe' in
# un secondo posto che decide l'agenzia, che e' precisamente cio' che P26-1 ha
# eliminato.
# ---------------------------------------------------------------------------

class SpiaCursore:
    """Registra le query eseguite e risponde con la riga che gli viene data."""

    def __init__(self, riga=None):
        self.sql: list[str] = []
        self.parametri: list[tuple] = []
        self._riga = riga
        # I due writer di P28 restituiscono `cur.rowcount`: senza, il doppio
        # fallirebbe per un attributo mancante invece che per cio' che il test
        # sta guardando.
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.parametri.append(params)

    def fetchone(self):
        return self._riga


def test_b1_resolve_session_reads_the_acting_agency():
    cur = SpiaCursore()
    auth_repository.resolve_session(cur, "hash", 240)
    sql = cur.sql[0]
    assert "acting_agency_id" in sql
    assert "acting_entered_at" in sql


def test_b2_resolve_session_also_reads_the_acting_agency_status():
    """Senza lo stato, un'agenzia sospesa resterebbe impersonabile."""
    cur = SpiaCursore()
    auth_repository.resolve_session(cur, "hash", 240)
    sql = cur.sql[0].lower()
    assert "acting_agency_status" in sql


def test_b3_the_home_membership_is_still_read():
    """L'acting non sostituisce la membership nella query: la affianca."""
    cur = SpiaCursore()
    auth_repository.resolve_session(cur, "hash", 240)
    sql = cur.sql[0]
    assert "agency_memberships" in sql


def test_b4_set_acting_context_writes_both_columns_for_one_session():
    cur = SpiaCursore()
    auth_repository.set_acting_context(cur, SESSIONE, OSPITE)
    sql = cur.sql[0].lower()
    assert "update operator_sessions" in " ".join(sql.split())
    assert "acting_agency_id" in sql
    assert "acting_entered_at" in sql
    assert "where id = %s" in " ".join(sql.split())


def test_b5_clear_acting_context_nulls_both_columns():
    cur = SpiaCursore()
    auth_repository.clear_acting_context(cur, SESSIONE)
    sql = " ".join(cur.sql[0].lower().split())
    assert "acting_agency_id = null" in sql
    assert "acting_entered_at = null" in sql


def test_b6_neither_writer_touches_agency_memberships():
    for scrivi in (
        lambda cur: auth_repository.set_acting_context(cur, SESSIONE, OSPITE),
        lambda cur: auth_repository.clear_acting_context(cur, SESSIONE),
    ):
        cur = SpiaCursore()
        scrivi(cur)
        assert "agency_memberships" not in cur.sql[0]


# ---------------------------------------------------------------------------
# C - LA SESSIONE RISOLTA
#
# Qui si prova la regola centrale: chi e' l'attore, quale agenzia lo scopa, e
# cosa succede quando l'acting smette di essere legittimo.
# ---------------------------------------------------------------------------

def _riga(**sovrascritture):
    base = {
        "session_id": SESSIONE,
        "expires_at": SCADENZA,
        "last_seen_at": ENTRATO,
        "user_id": GIORGIO,
        "user_status": "active",
        "is_platform_admin": True,
        "agency_id": None,
        "role": None,
        "membership_status": None,
        "agency_name": None,
        "agency_status": None,
        "acting_agency_id": None,
        "acting_entered_at": None,
        "acting_agency_name": None,
        "acting_agency_status": None,
    }
    base.update(sovrascritture)
    return base


@pytest.fixture
def sessione(monkeypatch):
    """`session_from_token` con il database sostituito, e le scritture contate."""
    stato = {"riga": _riga(), "scritture": []}

    @contextmanager
    def _cursore(commit=False):
        yield object(), object()

    monkeypatch.setattr(auth_service, "operator_cursor", _cursore)
    monkeypatch.setattr(
        auth_service.repository, "resolve_session",
        # `dict(...)`: il service riceve una copia, come la riceverebbe dal
        # driver. Le scritture del doppio vanno sulla riga VERA, che il
        # test rilegge - altrimenti si verificherebbe la copia.
        lambda cur, token_hash, idle: dict(stato["riga"]),
    )
    monkeypatch.setattr(
        auth_service.repository, "touch_session",
        lambda cur, sid: stato["scritture"].append(f"touch:{sid}"),
    )
    def _clear(cur, sid):
        # Il doppio SCRIVE davvero, sulla riga che il test poi rilegge.
        #
        # Registrare la sola chiamata proverebbe che il service ha deciso di
        # cancellare; non proverebbe che la riga sia rimasta pulita - e la riga
        # e' cio' che la richiesta successiva rileggera'. La UPDATE vera, sullo
        # stesso identico effetto, la esegue
        # tests/test_p28_acting_postgres_real.py sezione F.
        stato["scritture"].append(f"clear-acting:{sid}")
        stato["riga"]["acting_agency_id"] = None
        stato["riga"]["acting_entered_at"] = None
        stato["riga"]["acting_agency_name"] = None
        stato["riga"]["acting_agency_status"] = None
        return 1

    monkeypatch.setattr(
        auth_service.repository, "clear_acting_context", _clear,
    )
    monkeypatch.setattr(
        auth_service.security, "hash_session_token", lambda raw: "hash",
    )
    return stato


def test_c1_without_acting_the_scope_is_the_membership(sessione):
    sessione["riga"] = _riga(
        agency_id=CASA, role="agency_owner", membership_status="active",
        agency_name="Casa", agency_status="active",
    )
    risolta = auth_service.session_from_token("token")
    assert risolta["context"].agency_id == CASA
    assert risolta["context"].role == "agency_owner"
    assert risolta["acting_agency_id"] is None


def test_c2_acting_replaces_the_effective_agency(sessione):
    sessione["riga"] = _riga(
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    risolta = auth_service.session_from_token("token")
    assert risolta["context"].agency_id == OSPITE


def test_c3_the_actor_is_still_the_real_person(sessione):
    """L'identita' non viene mai sostituita dall'agenzia in cui si opera."""
    sessione["riga"] = _riga(
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    contesto = auth_service.session_from_token("token")["context"]
    assert contesto.user_id == GIORGIO
    assert contesto.is_platform_admin is True


def test_c4_acting_wins_over_an_existing_membership(sessione):
    """P27-1 D4 permette che il platform admin sia anche membro di un'agenzia.

    Mentre e' dentro un'altra, e' l'acting a decidere: altrimenti "entra
    nell'agenzia 34" non significherebbe niente per chi ha gia' una membership.
    """
    sessione["riga"] = _riga(
        agency_id=CASA, role="agency_owner", membership_status="active",
        agency_name="Casa", agency_status="active",
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    risolta = auth_service.session_from_token("token")
    assert risolta["context"].agency_id == OSPITE
    assert risolta["home_agency_id"] == CASA


def test_c5_the_home_role_does_not_follow_into_the_visited_agency(sessione):
    """Il ruolo di casa non vale altrove, e lasciarlo sarebbe un difetto vero.

    Un platform admin che a casa sua e' `agent` si vedrebbe restringere le
    letture dell'agenzia ospite a `assigned_agent_id = lui` - cioe' a nulla -
    perche' `scoped_predicate` guarda il ruolo. Dentro l'acting il ruolo e'
    assente: l'autorita' viene da `is_platform_admin`, non da una membership
    che li' dentro non esiste.
    """
    sessione["riga"] = _riga(
        agency_id=CASA, role="agent", membership_status="active",
        agency_name="Casa", agency_status="active",
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    contesto = auth_service.session_from_token("token")["context"]
    assert contesto.role is None


def test_c6_losing_platform_admin_erases_the_acting_context(sessione):
    """Non "ignorato": CANCELLATO. La riga torna pulita."""
    sessione["riga"] = _riga(
        is_platform_admin=False,
        agency_id=CASA, role="agency_owner", membership_status="active",
        agency_name="Casa", agency_status="active",
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    risolta = auth_service.session_from_token("token")
    assert risolta["context"].agency_id == CASA
    assert risolta["acting_agency_id"] is None
    assert f"clear-acting:{SESSIONE}" in sessione["scritture"]


@pytest.mark.parametrize("stato_agenzia", ["suspended", "archived"])
def test_c7_a_non_active_agency_erases_the_acting_context(sessione, stato_agenzia):
    sessione["riga"] = _riga(
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status=stato_agenzia,
    )
    risolta = auth_service.session_from_token("token")
    assert risolta["acting_agency_id"] is None
    assert risolta["context"].agency_id is None
    assert f"clear-acting:{SESSIONE}" in sessione["scritture"]


def test_c8_a_valid_acting_is_not_cleared(sessione):
    sessione["riga"] = _riga(
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    auth_service.session_from_token("token")
    assert not [s for s in sessione["scritture"] if s.startswith("clear-acting")]


def test_c9_the_resolved_session_reports_both_agencies_by_name(sessione):
    sessione["riga"] = _riga(
        agency_id=CASA, role="agency_owner", membership_status="active",
        agency_name="Casa", agency_status="active",
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    risolta = auth_service.session_from_token("token")
    assert risolta["agency_name"] == "Ospite"
    assert risolta["home_agency_name"] == "Casa"
    assert risolta["acting_entered_at"] == ENTRATO


def test_c6b_the_session_row_is_null_after_the_flag_was_revoked(sessione):
    """LA PROVA SULLA RIGA, non sul contesto.

    `test_c6` guarda cosa il service restituisce; questo guarda cosa resta
    scritto. Sono due cose diverse, e la seconda e' quella che conta alla
    richiesta dopo: un acting "ignorato" ma ancora presente in riga
    tornerebbe valido appena il flag venisse ridato.
    """
    sessione["riga"] = _riga(
        is_platform_admin=False,
        agency_id=CASA, role="agency_owner", membership_status="active",
        agency_name="Casa", agency_status="active",
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    auth_service.session_from_token("token")
    assert sessione["riga"]["acting_agency_id"] is None
    assert sessione["riga"]["acting_entered_at"] is None


@pytest.mark.parametrize("stato_agenzia", ["suspended", "archived"])
def test_c7b_the_session_row_is_null_after_the_agency_stopped_being_active(
    sessione, stato_agenzia
):
    sessione["riga"] = _riga(
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status=stato_agenzia,
    )
    auth_service.session_from_token("token")
    assert sessione["riga"]["acting_agency_id"] is None
    assert sessione["riga"]["acting_entered_at"] is None


def test_c7c_the_row_stays_null_even_when_that_request_ends_in_403(sessione):
    """La cancellazione NON dipende dall'esito della richiesta che la scopre.

    Un platform admin senza membership che perde l'agenzia visitata resta
    senza scope: la richiesta successiva finisce 403
    (`PlatformAdminAgencyRequired`, che ogni router traduce). La riga deve
    essere gia' pulita PRIMA che quel 403 venga formulato - altrimenti
    basterebbe una richiesta rifiutata per tenere in vita un acting decaduto.
    """
    sessione["riga"] = _riga(
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="suspended",
    )
    risolta = auth_service.session_from_token("token")

    # La richiesta finisce 403: nessuna agenzia, quindi nessuno scope.
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_predicate(risolta["context"], "contacts", "c")

    # E la riga e' pulita lo stesso.
    assert sessione["riga"]["acting_agency_id"] is None
    assert sessione["riga"]["acting_entered_at"] is None


def test_c7d_an_unusable_session_still_loses_its_acting_context(sessione):
    """IL DIFETTO TROVATO IN REVIEW, e la sua prova.

    Togliere `is_platform_admin` a chi NON ha membership non rende soltanto
    l'acting illegittimo: rende inutilizzabile l'intera sessione. Prima della
    correzione la risoluzione usciva subito, e l'acting restava scritto in
    riga - innocuo finche' quella sessione non risolveva, ma pronto a tornare
    in vita il giorno in cui il flag fosse ridato.

    Adesso l'acting viene giudicato PRIMA, e la sua legittimita' include
    l'utilizzabilita' della sessione.
    """
    sessione["riga"] = _riga(
        is_platform_admin=False,
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    assert auth_service.session_from_token("token") is None
    assert sessione["riga"]["acting_agency_id"] is None
    assert sessione["riga"]["acting_entered_at"] is None


def test_c7e_a_restored_flag_does_not_resurrect_the_cleared_acting(sessione):
    """Rimesso il flag, non si rientra da soli: serve un nuovo ingresso.

    E' la conseguenza che rende la correzione sopra necessaria e non estetica:
    rientrare senza un ENTER significherebbe rientrare senza una riga di audit.
    """
    sessione["riga"] = _riga(
        is_platform_admin=False,
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    auth_service.session_from_token("token")
    assert sessione["riga"]["acting_agency_id"] is None

    sessione["riga"]["is_platform_admin"] = True
    risolta = auth_service.session_from_token("token")
    assert risolta is not None
    assert risolta["acting_agency_id"] is None
    assert risolta["context"].agency_id is None


def test_c7f_a_disabled_account_also_loses_its_acting_context(sessione):
    """Non solo il flag: qualunque cosa renda la sessione inservibile."""
    sessione["riga"] = _riga(
        user_status="disabled",
        acting_agency_id=OSPITE, acting_entered_at=ENTRATO,
        acting_agency_name="Ospite", acting_agency_status="active",
    )
    assert auth_service.session_from_token("token") is None
    assert sessione["riga"]["acting_agency_id"] is None


def test_c10_a_dead_session_resolves_to_nothing_acting_or_not(monkeypatch, sessione):
    monkeypatch.setattr(
        auth_service.repository, "resolve_session", lambda cur, t, i: None,
    )
    assert auth_service.session_from_token("token") is None


# ---------------------------------------------------------------------------
# D - LO SCOPING, CHE NON CAMBIA
#
# Il predicate e' quello di P26-1, invariato. Questi test esistono perche' la
# tentazione che P28 crea - "tanto e' platform admin" - si materializzerebbe
# proprio qui.
# ---------------------------------------------------------------------------

def test_d1_inside_the_acting_context_the_predicate_is_the_visited_agency():
    contesto = _ctx(agency_id=OSPITE)
    predicato, parametri = scoped_predicate(contesto, "contacts", "c")
    assert predicato == "c.agency_id = %s"
    assert parametri == [OSPITE]


def test_d2_another_agency_is_not_reachable_from_the_acting_context():
    """Non c'e' nessun valore di contesto che produca due agenzie."""
    contesto = _ctx(agency_id=OSPITE)
    for tabella in sorted(core_scope.SCOPED_TABLES):
        _, parametri = scoped_predicate(contesto, tabella, "t")
        assert ALTRA not in parametri
        assert parametri[0] == OSPITE


def test_d3_after_exit_an_unbound_platform_admin_is_refused():
    """Uscito, il Superadmin non e' scopato da niente: e' rifiutato."""
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_predicate(_ctx(agency_id=None), "contacts", "c")


def test_d4_the_predicate_has_no_platform_admin_branch():
    """Nessuna scorciatoia: il testo della funzione non nomina il flag."""
    import inspect
    sorgente = inspect.getsource(scoped_predicate)
    corpo = sorgente.split('"""')[2] if sorgente.count('"""') >= 2 else sorgente
    righe = [r for r in corpo.splitlines() if r.strip() and not r.strip().startswith("#")]
    assert not [r for r in righe if "is_platform_admin" in r], righe


# ---------------------------------------------------------------------------
# E - ENTER ED EXIT
# ---------------------------------------------------------------------------

class Store:
    """Le agenzie e la riga di sessione. Registra l'ordine delle scritture."""

    def __init__(self):
        self.agenzie = {
            CASA: {"id": CASA, "name": "Casa", "slug": "casa", "status": "active"},
            OSPITE: {"id": OSPITE, "name": "Ospite", "slug": "ospite",
                     "status": "active"},
            ALTRA: {"id": ALTRA, "name": "Altra", "slug": "altra",
                    "status": "suspended"},
        }
        self.sessioni = {SESSIONE: {"id": SESSIONE, "acting_agency_id": None,
                                    "acting_entered_at": None}}
        self.scritture: list[str] = []
        self.membership_toccate = 0
        self._istantanea = None

    def snapshot(self):
        self._istantanea = {k: dict(v) for k, v in self.sessioni.items()}

    def restore(self):
        if self._istantanea is not None:
            self.sessioni = self._istantanea
            self._istantanea = None

    def commit(self):
        self._istantanea = None

    def set_acting(self, session_id, agency_id):
        self.scritture.append(f"set-acting:{session_id}:{agency_id}")
        riga = self.sessioni.get(session_id)
        if riga is None:
            return 0
        riga["acting_agency_id"] = agency_id
        riga["acting_entered_at"] = ENTRATO
        return 1

    def clear_acting(self, session_id):
        self.scritture.append(f"clear-acting:{session_id}")
        riga = self.sessioni.get(session_id)
        if riga is None:
            return 0
        riga["acting_agency_id"] = None
        riga["acting_entered_at"] = None
        return 1


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
    stato = {"store": Store(), "audit": [], "ordine": [],
             "audit_fallisce": False, "commit_fallisce": False,
             # Cio' che `audit.record` solleva. `None` = comportamento normale;
             # un'eccezione = quella viene sollevata al posto della scrittura.
             # Serve a distinguere il guasto PREVISTO (PlatformAuditUnavailable)
             # da tutto il resto, che non deve essere silenziato.
             "audit_solleva": None,
             "sessione": _ctx()}

    @contextmanager
    def _cursore():
        stato["ordine"].append("open")
        conn = FakeConn(stato["store"], stato["ordine"],
                        stato["commit_fallisce"])
        stato["store"].snapshot()
        try:
            yield conn, object()
        except Exception:
            conn.rollback()
            raise

    monkeypatch.setattr(acting_service, "platform_operation_cursor", _cursore)

    store = stato["store"]
    repo = acting_service.acting_repository
    monkeypatch.setattr(
        repo, "get_session_acting",
        lambda cur, session_id: (dict(store.sessioni[session_id])
                                 if session_id in store.sessioni else None),
    )
    monkeypatch.setattr(
        repo, "set_acting",
        lambda cur, *, session_id, agency_id: store.set_acting(session_id, agency_id),
    )
    monkeypatch.setattr(
        repo, "clear_acting",
        lambda cur, *, session_id: store.clear_acting(session_id),
    )
    monkeypatch.setattr(
        acting_service.agencies_repository, "get_agency",
        lambda cur, aid: dict(store.agenzie[aid]) if aid in store.agenzie else None,
    )

    def _audit(**kwargs):
        stato["ordine"].append(f"audit:{kwargs.get('action')}")
        if stato["audit_solleva"] is not None:
            raise stato["audit_solleva"]
        if stato["audit_fallisce"]:
            raise PlatformAuditUnavailable("registro non scrivibile")
        stato["audit"].append(kwargs)
        return len(stato["audit"])

    # UNA sola patch, e copre entrambi gli ordini: quello che si sostituisce e'
    # un attributo del MODULO `audit`, che `transaction` usa sia in
    # `audit_then_commit` sia in `commit_then_audit`. `acting_service` non lo
    # importa affatto - la scrittura del registro non e' compito suo.
    monkeypatch.setattr(acting_service.transaction.audit, "record", _audit)
    return stato


def test_e1_entering_persists_the_acting_agency(service):
    acting_service.enter_agency(_ctx(), OSPITE)
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] == OSPITE


def test_e2_entering_creates_no_membership(service):
    acting_service.enter_agency(_ctx(), OSPITE)
    assert service["store"].membership_toccate == 0
    assert not [s for s in service["store"].scritture if "membership" in s]


def test_e3_the_audit_is_written_before_the_commit(service):
    acting_service.enter_agency(_ctx(), OSPITE)
    ordine = service["ordine"]
    assert ordine.index(f"audit:{ACTION_ACTING_ENTER}") < ordine.index("commit")


def test_e4_an_unwritable_audit_refuses_the_entry(service):
    """Un ingresso che non si riesce a registrare non avviene."""
    service["audit_fallisce"] = True
    with pytest.raises(PlatformAuditUnavailable):
        acting_service.enter_agency(_ctx(), OSPITE)
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None
    assert "rollback" in service["ordine"]


def test_e5_a_missing_agency_is_a_404(service):
    with pytest.raises(AgencyNotFound) as exc:
        acting_service.enter_agency(_ctx(), 9999)
    assert str(exc.value) == AGENCY_NOT_FOUND_MESSAGE
    assert not service["audit"]


@pytest.mark.parametrize("stato_agenzia", ["suspended", "archived"])
def test_e6_a_non_active_agency_cannot_be_entered(service, stato_agenzia):
    service["store"].agenzie[OSPITE]["status"] = stato_agenzia
    with pytest.raises(PlatformConflict) as exc:
        acting_service.enter_agency(_ctx(), OSPITE)
    assert str(exc.value) == ACTING_AGENCY_NOT_ACTIVE_MESSAGE
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_e7_entering_a_second_agency_is_refused_not_swapped(service):
    """Una alla volta, e il cambio e' esplicito: prima si esce."""
    acting_service.enter_agency(_ctx(), OSPITE)
    with pytest.raises(PlatformConflict) as exc:
        acting_service.enter_agency(_ctx(), CASA)
    assert str(exc.value) == ACTING_ALREADY_ACTIVE_MESSAGE
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] == OSPITE


def test_e8_re_entering_the_same_agency_is_also_refused(service):
    """Non e' un no-op silenzioso: sarebbe un secondo ingresso senza uscita."""
    acting_service.enter_agency(_ctx(), OSPITE)
    with pytest.raises(PlatformConflict):
        acting_service.enter_agency(_ctx(), OSPITE)


def test_e9_a_context_without_a_session_cannot_enter(service):
    """L'acting vive in una riga di sessione: senza sessione non ha dove stare."""
    with pytest.raises(PlatformConflict) as exc:
        acting_service.enter_agency(_ctx(session_id=None), OSPITE)
    assert str(exc.value) == ACTING_NEEDS_SESSION_MESSAGE


def test_e10_exit_clears_the_acting_agency(service):
    acting_service.enter_agency(_ctx(), OSPITE)
    acting_service.exit_agency(_ctx(agency_id=OSPITE))
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_e11_exit_is_idempotent(service):
    acting_service.exit_agency(_ctx())
    acting_service.exit_agency(_ctx())
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_e12_exit_without_acting_writes_no_audit_row(service):
    """Non e' successo niente: registrarlo riempirebbe il registro di nulla."""
    acting_service.exit_agency(_ctx())
    assert not [r for r in service["audit"] if r["action"] == ACTION_ACTING_EXIT]


def test_e13_exit_happens_even_when_the_audit_cannot_be_written(service):
    """L'ASIMMETRIA. Un'uscita bloccata lascerebbe il Superadmin dentro.

    E' l'unico punto di P27/P28 in cui un audit indisponibile non ferma
    l'operazione, ed e' una decisione: fra "uscita non registrata" e
    "Superadmin intrappolato in un'agenzia" il secondo e' il danno peggiore.
    """
    acting_service.enter_agency(_ctx(), OSPITE)
    service["audit_fallisce"] = True
    acting_service.exit_agency(_ctx(agency_id=OSPITE))
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_e13b_the_row_is_null_in_the_store_after_a_failed_exit_audit(service):
    """Non solo "l'uscita e' avvenuta": la RIGA e' pulita.

    Verificare il contesto restituito proverebbe che il service ha risposto
    bene; qui si guarda il posto in cui l'acting vive davvero, perche' e'
    quello che la richiesta successiva rileggera'.
    """
    acting_service.enter_agency(_ctx(), OSPITE)
    service["audit_fallisce"] = True
    acting_service.exit_agency(_ctx(agency_id=OSPITE))
    riga = service["store"].sessioni[SESSIONE]
    assert riga["acting_agency_id"] is None
    assert riga["acting_entered_at"] is None


def test_e13c_a_failed_exit_audit_raises_nothing_at_all(service):
    """Nessuna eccezione esce: e' cio' che tiene il 204 al livello HTTP."""
    acting_service.enter_agency(_ctx(), OSPITE)
    service["audit_fallisce"] = True
    assert acting_service.exit_agency(_ctx(agency_id=OSPITE)) is None


def test_e13d_the_inverted_order_is_used_by_the_exit_and_by_nothing_else(service):
    """`commit_then_audit` e' approvato PER L'USCITA SICURA, e per niente altro.

    Una deroga senza un perimetro diventa la regola nuova alla terza volta che
    fa comodo. Qui il perimetro e' un elenco di chiamanti, ed e' lungo uno.
    """
    import ast
    import inspect
    from platform_admin import transaction

    pacchetto = ROOT / "platform_admin"
    chiamanti = []
    for percorso in sorted(pacchetto.glob("*.py")):
        albero = ast.parse(percorso.read_text(encoding="utf-8"))
        chiamanti += [
            nodo.name for nodo in ast.walk(albero)
            if isinstance(nodo, ast.FunctionDef)
            and any(
                isinstance(interno, ast.Call)
                and isinstance(interno.func, ast.Name)
                and interno.func.id == "commit_then_audit"
                for interno in ast.walk(nodo)
            )
        ]
    assert chiamanti == ["exit_agency"], chiamanti

    # E la deroga non si allarga di nascosto: la sua ragione e' scritta dove
    # vive, e nomina il caso che la giustifica.
    sorgente = inspect.getsource(transaction.commit_then_audit)
    assert "TOLGONO un accesso" in sorgente


# --- La deroga non e' un `except Exception` ------------------------------------
#
# `commit_then_audit` ingoia UN errore e uno solo: quello che il writer di
# audit dichiara quando la riga non e' stata scritta. Tutto il resto - un
# difetto di programmazione, un guasto del driver, un commit fallito - deve
# uscire, perche' un `except` largo trasformerebbe "l'uscita e' avvenuta e non
# e' stata registrata" in "e' successo qualcosa e nessuno lo sa".


def test_e13e_a_failed_commit_during_the_exit_is_not_swallowed(service):
    """Il commit sta FUORI dal try, e deve restarci.

    Se fallisse e venisse ingoiato, la risposta direbbe 204 su un'uscita che
    non e' stata scritta: il Superadmin resterebbe dentro l'agenzia
    convinto di esserne uscito. E' il caso peggiore di tutti.
    """
    acting_service.enter_agency(_ctx(), OSPITE)
    service["commit_fallisce"] = True
    with pytest.raises(RuntimeError):
        acting_service.exit_agency(_ctx(agency_id=OSPITE))


@pytest.mark.parametrize("errore", [
    RuntimeError("guasto inatteso"),
    TypeError("firma sbagliata"),
    ValueError("azione fuori dallo spazio dei nomi"),
    KeyError("metadato mancante"),
])
def test_e13f_an_unexpected_error_from_the_audit_writer_is_not_swallowed(
    service, errore
):
    """Solo `PlatformAuditUnavailable` e' prevista. Il resto esce.

    I `ValueError` non sono ipotetici: `audit.record` li solleva PRIMA di
    provare a scrivere, quando l'azione e' fuori dallo spazio dei nomi o
    l'esito non e' ammesso. Sono difetti di programmazione, e devono fallire
    rumorosamente invece di diventare una riga di log che nessuno legge.
    """
    acting_service.enter_agency(_ctx(), OSPITE)
    service["audit_solleva"] = errore
    with pytest.raises(type(errore)):
        acting_service.exit_agency(_ctx(agency_id=OSPITE))


def test_e13g_the_expected_audit_failure_is_the_only_one_absorbed(service):
    """Il verso positivo, accanto ai negativi: quella prevista NON esce."""
    acting_service.enter_agency(_ctx(), OSPITE)
    service["audit_solleva"] = PlatformAuditUnavailable("registro non scrivibile")
    assert acting_service.exit_agency(_ctx(agency_id=OSPITE)) is None
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_e13h_commit_then_audit_catches_one_exception_type_and_not_a_bare_except():
    """Il gestore, letto come testo: nessun `except Exception`, nessun bare.

    Un test sul comportamento prova i casi che gli si passano; questo prova
    che non ne esistano altri - un `except Exception` accanto a quello giusto
    passerebbe ogni test sopra e ingoierebbe tutto.
    """
    import ast
    import inspect
    from platform_admin import transaction

    sorgente = inspect.getsource(transaction.commit_then_audit)
    albero = ast.parse(textwrap.dedent(sorgente))
    catturati = [
        h.type for h in ast.walk(albero) if isinstance(h, ast.ExceptHandler)
    ]
    assert len(catturati) == 1, ast.dump(albero)
    assert catturati[0] is not None, "except nudo: cattura anche KeyboardInterrupt"
    assert isinstance(catturati[0], ast.Name), ast.dump(catturati[0])
    assert catturati[0].id == "PlatformAuditUnavailable", catturati[0].id


def test_e13i_the_commit_is_outside_the_try(service):
    """Strutturale: se il commit finisse dentro il try, `test_e13e` resterebbe
    verde solo finche' l'eccezione non fosse `PlatformAuditUnavailable`."""
    import ast
    import inspect
    from platform_admin import transaction

    albero = ast.parse(
        textwrap.dedent(inspect.getsource(transaction.commit_then_audit))
    )
    corpo = albero.body[0].body
    commit_prima_del_try = any(
        isinstance(nodo, ast.Expr)
        and isinstance(nodo.value, ast.Call)
        and isinstance(nodo.value.func, ast.Attribute)
        and nodo.value.func.attr == "commit"
        for nodo in corpo
    )
    assert commit_prima_del_try, ast.dump(albero)
    dentro_il_try = [
        nodo for nodo in ast.walk(albero)
        if isinstance(nodo, ast.Try)
        for interno in ast.walk(ast.Module(body=nodo.body, type_ignores=[]))
        if isinstance(interno, ast.Attribute) and interno.attr == "commit"
    ]
    assert dentro_il_try == [], "il commit e' finito dentro il try"


def test_e14_the_enter_audit_names_the_actor_and_the_visited_agency(service):
    acting_service.enter_agency(_ctx(), OSPITE)
    riga = service["audit"][0]
    assert riga["action"] == ACTION_ACTING_ENTER
    assert riga["target_type"] == TARGET_TYPE_AGENCY
    assert str(riga["target_id"]) == str(OSPITE)
    assert riga["target_agency_id"] == OSPITE
    assert riga["actor"].user_id == GIORGIO
    assert riga["metadata"]["session_id"] == SESSIONE


def test_e15_the_exit_audit_names_the_agency_being_left(service):
    acting_service.enter_agency(_ctx(), OSPITE)
    acting_service.exit_agency(_ctx(agency_id=OSPITE))
    uscita = [r for r in service["audit"] if r["action"] == ACTION_ACTING_EXIT]
    assert len(uscita) == 1
    assert uscita[0]["target_agency_id"] == OSPITE
    assert uscita[0]["actor"].user_id == GIORGIO
    assert uscita[0]["metadata"]["session_id"] == SESSIONE


def test_e16_the_actions_live_in_the_platform_namespace(service):
    assert ACTION_ACTING_ENTER.startswith("platform.")
    assert ACTION_ACTING_EXIT.startswith("platform.")


# ---------------------------------------------------------------------------
# F - LE ROUTE, ATTRAVERSO FastAPI
# ---------------------------------------------------------------------------

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from operator_auth import dependencies as operator_deps  # noqa: E402
from platform_admin import dependencies as platform_deps  # noqa: E402
from platform_admin.router import router as platform_router  # noqa: E402

ENTER = f"/api/platform/agencies/{OSPITE}/enter"
EXIT = "/api/platform/agency-context/exit"


@pytest.fixture
def http(service, monkeypatch):
    def _sessione(_token):
        contesto = service["sessione"]
        riga = service["store"].sessioni.get(contesto.session_id) or {}
        acting = riga.get("acting_agency_id")
        return {
            "context": contesto,
            "agency_name": None,
            "expires_at": SCADENZA,
            "acting_agency_id": acting,
            "acting_agency_name": None,
            "acting_entered_at": riga.get("acting_entered_at"),
            "home_agency_id": None,
            "home_agency_name": None,
        }

    monkeypatch.setattr(operator_deps.service, "session_from_token", _sessione)

    # L'audit dell'AMMISSIONE deve continuare a riuscire anche mentre si sta
    # provando un registro rotto: se fallisse quello, la richiesta si
    # fermerebbe con 503 in `require_platform_admin` e non arriverebbe mai
    # alla route sotto esame. Quindi il guasto si accende per le sole azioni
    # di acting, che e' esattamente il caso che il requisito descrive.
    def _audit(**kwargs):
        if (service["audit_fallisce"]
                and str(kwargs.get("action", "")).startswith("platform.acting")):
            raise PlatformAuditUnavailable("registro non scrivibile")
        return 1

    monkeypatch.setattr(platform_deps.audit, "record", _audit, raising=False)

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("stima360_operator_session", "un-token")
    return client


def test_f1_a_platform_admin_can_enter(http, service):
    risposta = http.post(ENTER)
    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["acting_agency_id"] == OSPITE


def test_f2_a_normal_tenant_cannot_enter(http, service):
    service["sessione"] = _ctx(is_platform_admin=False, agency_id=CASA,
                               role="agency_owner")
    assert http.post(ENTER).status_code == 403


def test_f3_a_normal_tenant_cannot_exit(http, service):
    service["sessione"] = _ctx(is_platform_admin=False, agency_id=CASA,
                               role="agency_owner")
    assert http.post(EXIT).status_code == 403


def test_f4_an_agency_owner_cannot_change_agency_through_these_routes(http, service):
    """Il titolare resta nella sua: la rotta esiste e lo rifiuta, non lo ignora."""
    service["sessione"] = _ctx(is_platform_admin=False, agency_id=CASA,
                               role="agency_owner")
    assert http.post(f"/api/platform/agencies/{ALTRA}/enter").status_code == 403
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_f5_an_anonymous_caller_gets_401(http, monkeypatch):
    monkeypatch.setattr(
        operator_deps.service, "session_from_token", lambda _t: None,
    )
    assert http.post(ENTER).status_code == 401


def test_f6_a_missing_agency_answers_404(http):
    assert http.post("/api/platform/agencies/9999/enter").status_code == 404


def test_f7_a_suspended_agency_answers_409(http, service):
    assert http.post(f"/api/platform/agencies/{ALTRA}/enter").status_code == 409


def test_f8_entering_twice_answers_409(http):
    assert http.post(ENTER).status_code == 200
    assert http.post(ENTER).status_code == 409


def test_f9_exit_answers_204_and_is_idempotent(http):
    http.post(ENTER)
    assert http.post(EXIT).status_code == 204
    assert http.post(EXIT).status_code == 204


def test_f10_the_acting_agency_comes_from_the_path_not_from_the_body(http, service):
    """Un corpo che prova a nominare un'altra agenzia non la ottiene."""
    http.post(ENTER, json={"agency_id": ALTRA, "acting_agency_id": ALTRA})
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] == OSPITE


def test_f11_a_header_cannot_forge_the_acting_agency(http, service):
    http.post(ENTER, headers={"X-Acting-Agency": str(ALTRA)})
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] == OSPITE


def test_f12_a_query_parameter_cannot_forge_the_acting_agency(http, service):
    http.post(f"{ENTER}?acting_agency_id={ALTRA}&agency_id={ALTRA}")
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] == OSPITE


def test_f13_platform_me_reports_the_acting_agency(http, service):
    http.post(ENTER)
    corpo = http.get("/api/platform/me").json()
    assert corpo["acting_agency_id"] == OSPITE


def test_f14_platform_me_reports_no_acting_before_entering(http):
    assert http.get("/api/platform/me").json()["acting_agency_id"] is None


def test_f15_there_is_no_delete_route_for_the_acting_context(http):
    """Si esce con una POST dichiarata, non cancellando una risorsa."""
    assert http.delete(EXIT).status_code in (404, 405)


def test_f16_an_exit_whose_audit_fails_still_answers_204(http, service):
    """IL REQUISITO DEFINITIVO, al livello in cui l'utente lo vive.

    Un registro non scrivibile non deve tenere il Superadmin dentro
    un'agenzia, e non deve nemmeno lasciarlo nel dubbio: 204, non 503. Il 503
    direbbe "non e' successo niente" mentre l'uscita e' avvenuta, e la UI
    rimetterebbe la barra su una sessione che non sta piu' impersonando
    nessuno.
    """
    assert http.post(ENTER).status_code == 200
    service["audit_fallisce"] = True
    assert http.post(EXIT).status_code == 204


def test_f17_and_the_session_row_is_null_after_that_failed_audit(http, service):
    """Non basta il 204: la RIGA deve essere pulita.

    E' quella che la richiesta successiva rileggera'. Un 204 su una riga
    ancora valorizzata sarebbe la peggiore delle risposte: dice che si e'
    usciti e lascia dentro.
    """
    http.post(ENTER)
    service["audit_fallisce"] = True
    http.post(EXIT)
    riga = service["store"].sessioni[SESSIONE]
    assert riga["acting_agency_id"] is None
    assert riga["acting_entered_at"] is None


def test_f18_an_enter_whose_audit_fails_answers_503_and_writes_nothing(http, service):
    """L'altro verso dell'asimmetria, allo stesso livello.

    Entrare e' concedere: senza traccia non avviene. E' la regola di P27-2, e
    qui resta intatta - la deroga vale per l'uscita e per niente altro.
    """
    service["audit_fallisce"] = True
    assert http.post(ENTER).status_code == 503
    assert service["store"].sessioni[SESSIONE]["acting_agency_id"] is None


def test_f19_a_second_exit_after_a_failed_audit_is_still_204(http, service):
    """L'idempotenza regge anche dopo un guasto: non si resta a meta'."""
    http.post(ENTER)
    service["audit_fallisce"] = True
    assert http.post(EXIT).status_code == 204
    assert http.post(EXIT).status_code == 204


# ---------------------------------------------------------------------------
# G - /me DEL TENANT
#
# La Shell disegna la barra da qui. Se `/me` non distinguesse l'identita'
# reale dall'agenzia effettiva, la barra non potrebbe dire la verita'.
# ---------------------------------------------------------------------------

from operator_auth.router import router as auth_router  # noqa: E402


@pytest.fixture
def tenant_http(monkeypatch):
    stato = {"risolta": None}

    monkeypatch.setattr(
        operator_deps.service, "session_from_token", lambda _t: stato["risolta"],
    )
    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("stima360_operator_session", "un-token")
    return client, stato


def _risolta(**sovrascritture):
    base = {
        "context": _ctx(),
        "agency_name": None,
        "expires_at": SCADENZA,
        "acting_agency_id": None,
        "acting_agency_name": None,
        "acting_entered_at": None,
        "home_agency_id": None,
        "home_agency_name": None,
    }
    base.update(sovrascritture)
    return base


def test_g1_me_separates_the_actor_from_the_agency(tenant_http):
    client, stato = tenant_http
    stato["risolta"] = _risolta(
        context=_ctx(agency_id=OSPITE),
        agency_name="Ospite",
        acting_agency_id=OSPITE,
        acting_agency_name="Ospite",
        acting_entered_at=ENTRATO,
        home_agency_id=CASA,
        home_agency_name="Casa",
    )
    corpo = client.get("/api/operator-auth/me").json()
    assert corpo["user_id"] == GIORGIO
    assert corpo["agency_id"] == OSPITE
    assert corpo["acting"]["agency_id"] == OSPITE
    assert corpo["acting"]["agency_name"] == "Ospite"
    assert corpo["home_agency_id"] == CASA
    assert corpo["home_agency_name"] == "Casa"


def test_g2_me_reports_no_acting_when_there_is_none(tenant_http):
    client, stato = tenant_http
    stato["risolta"] = _risolta(
        context=_ctx(agency_id=CASA, role="agency_owner"),
        agency_name="Casa", home_agency_id=CASA, home_agency_name="Casa",
    )
    corpo = client.get("/api/operator-auth/me").json()
    assert corpo["acting"] is None
    assert corpo["agency_id"] == CASA


def test_g3_me_never_leaks_a_third_agency(tenant_http):
    client, stato = tenant_http
    stato["risolta"] = _risolta(
        context=_ctx(agency_id=OSPITE), agency_name="Ospite",
        acting_agency_id=OSPITE, acting_agency_name="Ospite",
        acting_entered_at=ENTRATO, home_agency_id=CASA, home_agency_name="Casa",
    )
    testo = client.get("/api/operator-auth/me").text
    assert str(ALTRA) not in testo


# ---------------------------------------------------------------------------
# H - IL LOGOUT
# ---------------------------------------------------------------------------

def test_h1_logout_revokes_the_session_and_takes_the_acting_with_it():
    """L'acting sta NELLA riga di sessione: revocarla lo porta via.

    Non serve - e non deve esistere - una seconda cancellazione esplicita nel
    logout: sarebbe un secondo posto che decide quando l'acting finisce.
    """
    import inspect
    sorgente = inspect.getsource(auth_service.logout)
    assert "acting" not in sorgente.lower()


def test_h2_a_revoked_session_resolves_to_nothing(monkeypatch, sessione):
    monkeypatch.setattr(
        auth_service.repository, "resolve_session", lambda cur, t, i: None,
    )
    assert auth_service.session_from_token("token") is None


# ---------------------------------------------------------------------------
# I - IL SIGILLO P26-1
#
# P28 non allarga `OperatorContext`. Se lo facesse, il contratto che P26-1 ha
# congelato diventerebbe negoziabile, e il prossimo campo lo aggiungerebbe
# qualcun altro senza chiedere.
# ---------------------------------------------------------------------------

import dataclasses  # noqa: E402


def test_i1_operator_context_still_has_exactly_six_fields():
    nomi = [f.name for f in dataclasses.fields(OperatorContext)]
    assert nomi == [
        "user_id", "agency_id", "role", "is_platform_admin",
        "session_id", "auth_channel",
    ], nomi


def test_i2_acting_is_not_an_authentication_channel():
    """L'acting non e' un modo di autenticarsi: e' dove si sta operando."""
    from operator_auth.context import AUTH_CHANNELS
    assert AUTH_CHANNELS == ("operator_session", "legacy_basic")
    for canale in AUTH_CHANNELS:
        assert "acting" not in canale


def test_i3_no_context_type_gained_a_mutating_helper():
    from operator_auth.context import SystemAgencyContext
    for tipo in (OperatorContext, SystemAgencyContext):
        for nome in dir(tipo):
            assert not nome.startswith("with_"), f"{tipo.__name__}.{nome}"
            assert not nome.startswith("set_"), f"{tipo.__name__}.{nome}"


def test_i4_the_acting_service_never_names_the_membership_table():
    """La tabella non compare: non e' disciplina, e' assenza di occasione.

    Il testo del modulo puo' benissimo PARLARE di membership - lo fa, e spiega
    perche' non ne crea. Cio' che non deve comparire e' il nome della tabella
    in una istruzione, ed e' quello che questo test cerca.
    """
    import inspect
    sorgente = inspect.getsource(acting_service)
    istruzioni = re.findall(r"(?:FROM|UPDATE|INTO|JOIN)\s+([a-z_]+)", sorgente)
    assert "agency_memberships" not in istruzioni, istruzioni


def test_i5_the_acting_repository_only_touches_operator_sessions():
    from platform_admin import acting_repository
    import inspect
    sorgente = inspect.getsource(acting_repository)
    tabelle = set(re.findall(r"(?:FROM|UPDATE|INTO)\s+([a-z_]+)", sorgente))
    assert tabelle <= {"operator_sessions"}, tabelle
