"""P29-1.2 - il percorso unico di scrittura del consenso, come comportamento.

Questo file NON tocca un database. Segue la convenzione gia' usata da
tests/test_followup_repository.py, tests/test_seller_intelligence_repository.py
e tests/test_next6_p2b_flow_automation.py: un falso in memoria che capisce
soltanto le forme SQL che questo repository emette davvero.

Il falso modella ANCHE la transazione - scrive su una copia di lavoro e la
riversa sullo stato solo al commit - perche' l'atomicita' fra evento e
proiezione e' il requisito 1 di P29-1.2 e un falso che applicasse subito le
scritture non potrebbe dimostrarla.

Mappa:

    M1  la derivazione dello stato, e never_given != revoked
    M2  A/C/F: concessione, da ogni stato precedente
    M3  E: revoca, e cosa NON porta via
    M4  D: la casella vuota non revoca - per assenza di API, non per un if
    M5  idempotenza
    M6  tenancy: isolamento e contatto fuori scope
    M7  atomicita' e rollback
    M8  legacy: proiezione senza storico
    M9  nessuna regressione su contacts.marketing_consent, e P24
"""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from consent import repository, service
from consent.enums import (
    PURPOSE_MARKETING,
    PURPOSE_PRIVACY_TERMS,
    SOURCE_CRM,
    SOURCE_PUBLIC_STIMA,
    SOURCE_UNSUBSCRIBE_LINK,
    STATUS_GRANTED,
    STATUS_NEVER_GIVEN,
    STATUS_REVOKED,
)
from consent.exceptions import NotFoundError, ValidationError

NOW = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Contesti
# ---------------------------------------------------------------------------
class Ctx:
    """Il minimo che core.scope.scoped_predicate legge (protocollo AgencyScope)."""

    is_platform_admin = False

    def __init__(self, agency_id, role=None, user_id=None):
        self.agency_id = agency_id
        self.role = role
        self.user_id = user_id

    def require_agency(self):
        return self.agency_id


# ---------------------------------------------------------------------------
# Il falso database
# ---------------------------------------------------------------------------
CONTACT_COLUMNS = {
    "marketing_consent": None,
    "marketing_consent_at": None,
    "marketing_revoked_at": None,
    "marketing_consent_source": None,
    "marketing_consent_notice_id": None,
    "privacy_terms_accepted": None,
    "privacy_terms_accepted_at": None,
    "privacy_terms_revoked_at": None,
    "privacy_terms_source": None,
    "privacy_terms_notice_id": None,
}

ASSIGNMENT_RE = re.compile(r"(\w+)\s*=\s*(TRUE|FALSE|NULL|%s|NOW\(\))", re.IGNORECASE)


class FakeState:
    def __init__(self):
        self.contacts = {}
        self.events = []
        self.next_event_id = 1

    def add_contact(self, contact_id, agency_id, assigned_agent_id=None, **overrides):
        row = {
            "id": contact_id,
            "agency_id": agency_id,
            "assigned_agent_id": assigned_agent_id,
            "status": "active",
            "updated_at": None,
            **CONTACT_COLUMNS,
        }
        row.update(overrides)
        self.contacts[contact_id] = row
        return row

    def check_constraints(self):
        """I due CHECK di migrations/063, applicati come li applicherebbe il DB.

        Un falso che li ignorasse lascerebbe passare proprio lo stato che la
        migration esiste per impedire: concesso e revocato insieme.
        """
        for row in self.contacts.values():
            if row["marketing_consent"] is True and row["marketing_revoked_at"] is not None:
                raise AssertionError(
                    f"contacts_marketing_state_chk violated on contact {row['id']}"
                )
            if row["privacy_terms_accepted"] is True and row["privacy_terms_revoked_at"] is not None:
                raise AssertionError(
                    f"contacts_privacy_terms_state_chk violated on contact {row['id']}"
                )
            if row["privacy_terms_accepted"] is True and row["privacy_terms_accepted_at"] is None:
                raise AssertionError(
                    f"contacts_privacy_terms_accepted_chk violated on contact {row['id']}"
                )


class FakeCursor:
    def __init__(self, state, log):
        self.state = state
        self.log = log
        self.rows = []

    # -- helpers ----------------------------------------------------------
    def _norm(self, query):
        return " ".join(str(query).split()).lower()

    def execute(self, query, params=None):
        sql = self._norm(query)
        self.log.append((sql, copy.deepcopy(params)))

        if sql.startswith("select c.* from contacts c"):
            return self._select_contact(sql, params)
        if sql.startswith("insert into consent_events"):
            return self._insert_event(params)
        if sql.startswith("select ce.* from consent_events ce") and "idempotency_key" in sql:
            return self._select_event_by_key(params)
        if sql.startswith("select ce.* from consent_events ce"):
            return self._select_events(sql, params)
        if sql.startswith("update contacts c set"):
            return self._update_contact(query, params)
        raise AssertionError(f"unexpected consent SQL: {sql}")

    def fetchone(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None

    def fetchall(self):
        return copy.deepcopy(self.rows)

    def close(self):
        pass

    # -- shapes -----------------------------------------------------------
    def _matches_scope(self, sql, row, params):
        """Il predicato di CORE su contacts, applicato davvero.

        params: [contact_id, agency_id] oppure [contact_id, agency_id, user_id]
        quando il chiamante e' un agente (core.scope aggiunge assigned_agent_id).
        """
        contact_id, agency_id = params[0], params[1]
        if row["id"] != contact_id or row["agency_id"] != agency_id:
            return False
        if "assigned_agent_id" in sql:
            return row["assigned_agent_id"] == params[2]
        return True

    def _select_contact(self, sql, params):
        self.rows = []
        for row in self.state.contacts.values():
            if self._matches_scope(sql, row, params):
                self.rows = [row]
                break

    def _insert_event(self, params):
        key = params["idempotency_key"]
        if key is not None and any(e["idempotency_key"] == key for e in self.state.events):
            self.rows = []  # ON CONFLICT DO NOTHING
            return
        row = {
            "id": self.state.next_event_id,
            "agency_id": params["agency_id"],
            "contact_id": params["contact_id"],
            "purpose": params["purpose"],
            "decision": params["decision"],
            "decided_at": params["decided_at"],
            "source": params["source"],
            "notice_id": params["notice_id"],
            "actor_type": params["actor_type"],
            "actor_ref": params["actor_ref"],
            "evidence_type": params["evidence_type"],
            "evidence_ref": params["evidence_ref"],
            "note": params["note"],
            "idempotency_key": key,
            "created_at": NOW,
        }
        self.state.next_event_id += 1
        self.state.events.append(row)
        self.rows = [row]

    def _select_event_by_key(self, params):
        agency_id, key = params[0], params[1]
        match = next(
            (e for e in self.state.events
             if e["idempotency_key"] == key and e["agency_id"] == agency_id),
            None,
        )
        self.rows = [match] if match else []

    def _select_events(self, sql, params):
        agency_id, contact_id = params[0], params[1]
        rest = list(params[2:])
        purpose = rest.pop(0) if "ce.purpose = %s" in sql else None
        found = [
            e for e in self.state.events
            if e["agency_id"] == agency_id
            and e["contact_id"] == contact_id
            and (purpose is None or e["purpose"] == purpose)
        ]
        found.sort(key=lambda e: (e["decided_at"], e["id"]), reverse=True)
        if "limit %s" in sql and rest:
            found = found[: rest[-1]]
        elif "limit 1" in sql:
            found = found[:1]
        self.rows = found

    def _update_contact(self, query, params):
        raw = str(query)
        set_clause = raw[raw.lower().index(" set ") + 5: raw.lower().index(" where ")]
        assignments = ASSIGNMENT_RE.findall(set_clause)

        values = list(params)
        placeholders = sum(1 for _, token in assignments if token == "%s")
        set_values = values[:placeholders]
        where_values = values[placeholders:]

        target = None
        for row in self.state.contacts.values():
            if self._matches_scope(raw.lower(), row, where_values):
                target = row
                break
        if target is None:
            self.rows = []
            return

        consumed = 0
        for column, token in assignments:
            upper = token.upper()
            if upper == "TRUE":
                target[column] = True
            elif upper == "FALSE":
                target[column] = False
            elif upper == "NULL":
                target[column] = None
            elif upper == "NOW()":
                target[column] = NOW
            else:
                target[column] = set_values[consumed]
                consumed += 1
        self.rows = [target]


class FakeDatabase:
    """Stato committato + copia di lavoro. Il commit riversa, il rollback butta."""

    def __init__(self):
        self.state = FakeState()
        self.sql = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def cursor(self, *, commit: bool = False):
        working = copy.deepcopy(self.state)
        cur = FakeCursor(working, self.sql)
        try:
            yield None, cur
            if commit:
                working.check_constraints()
                self.state = working
                self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise


@pytest.fixture
def db(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(repository, "consent_cursor", database.cursor)
    monkeypatch.setattr(repository, "utcnow", lambda: NOW)
    return database


@pytest.fixture
def ctx():
    return Ctx(agency_id=1, role="agency_owner", user_id=10)


def grant(ctx, contact_id=1, purpose=PURPOSE_MARKETING, **kwargs):
    payload = {
        "contact_id": contact_id,
        "purpose": purpose,
        "source": SOURCE_PUBLIC_STIMA,
        "actor_type": "subject",
    }
    payload.update(kwargs)
    return service.record_grant(ctx, **payload)


def revoke(ctx, contact_id=1, purpose=PURPOSE_MARKETING, **kwargs):
    payload = {
        "contact_id": contact_id,
        "purpose": purpose,
        "source": SOURCE_UNSUBSCRIBE_LINK,
        "actor_type": "subject",
    }
    payload.update(kwargs)
    return service.record_revocation(ctx, **payload)


# ===========================================================================
# M1  la derivazione, e never_given != revoked
# ===========================================================================
def test_m1_never_given_quando_non_ce_niente():
    stato = service.state_from_projection({**CONTACT_COLUMNS}, PURPOSE_MARKETING)
    assert stato["status"] == STATUS_NEVER_GIVEN
    assert stato["granted_at"] is None and stato["revoked_at"] is None


def test_m1_revoked_non_e_never_given():
    revocato = service.state_from_projection(
        {**CONTACT_COLUMNS, "marketing_consent": False, "marketing_revoked_at": NOW},
        PURPOSE_MARKETING,
    )
    mai = service.state_from_projection(
        {**CONTACT_COLUMNS, "marketing_consent": False}, PURPOSE_MARKETING
    )
    assert revocato["status"] == STATUS_REVOKED
    assert mai["status"] == STATUS_NEVER_GIVEN
    assert revocato["status"] != mai["status"], (
        "revoked e never_given devono restare stati distinti: bloccano entrambi "
        "l'invio, ma solo uno dice che qualcuno aveva detto si'"
    )


def test_m1_null_e_false_sono_entrambi_never_given():
    for valore in (None, False):
        stato = service.state_from_projection(
            {**CONTACT_COLUMNS, "marketing_consent": valore}, PURPOSE_MARKETING
        )
        assert stato["status"] == STATUS_NEVER_GIVEN


def test_m1_privacy_e_marketing_sono_indipendenti():
    contatto = {
        **CONTACT_COLUMNS,
        "marketing_consent": True,
        "privacy_terms_accepted": None,
    }
    assert service.state_from_projection(contatto, PURPOSE_MARKETING)["status"] == STATUS_GRANTED
    assert service.state_from_projection(contatto, PURPOSE_PRIVACY_TERMS)["status"] == STATUS_NEVER_GIVEN


def test_m1_gli_stati_sono_tre_e_dichiarati():
    from consent.enums import STATUSES

    assert STATUSES == {STATUS_GRANTED, STATUS_REVOKED, STATUS_NEVER_GIVEN}


def test_m1_current_state_legge_la_proiezione(db, ctx):
    """L'unico punto di lettura del modulo, e passa dallo scope."""
    db.state.add_contact(1, agency_id=1)
    grant(ctx)

    stato = service.current_state(ctx, 1, PURPOSE_MARKETING)
    assert stato["status"] == STATUS_GRANTED
    assert stato["source"] == SOURCE_PUBLIC_STIMA

    with pytest.raises(NotFoundError):
        service.current_state(Ctx(agency_id=2, role="agency_owner"), 1, PURPOSE_MARKETING)


def test_m1_purpose_sconosciuto_rifiutato():
    with pytest.raises(ValidationError):
        service.state_from_projection({**CONTACT_COLUMNS}, "marketing_email")


# ===========================================================================
# M2  A / C / F - la concessione
# ===========================================================================
def test_m2_grant_da_never_given(db, ctx):
    db.state.add_contact(1, agency_id=1)
    esito = grant(ctx)

    assert esito["recorded"] is True and esito["created"] is True
    assert esito["state"]["status"] == STATUS_GRANTED
    assert len(db.state.events) == 1
    evento = db.state.events[0]
    assert evento["decision"] == "granted"
    assert evento["agency_id"] == 1, "agency_id viene dallo scope, non dal chiamante"

    contatto = db.state.contacts[1]
    assert contatto["marketing_consent"] is True
    assert contatto["marketing_consent_at"] == NOW
    assert contatto["marketing_revoked_at"] is None
    assert contatto["marketing_consent_source"] == SOURCE_PUBLIC_STIMA


def test_m2_grant_da_false_preesistente(db, ctx):
    db.state.add_contact(1, agency_id=1, marketing_consent=False)
    esito = grant(ctx)
    assert esito["state"]["status"] == STATUS_GRANTED
    assert db.state.contacts[1]["marketing_consent"] is True


def test_m2_grant_dopo_revoke_torna_granted(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx, decided_at=NOW)
    revoke(ctx, decided_at=NOW + timedelta(hours=1))
    assert db.state.contacts[1]["marketing_consent"] is False

    esito = grant(ctx, decided_at=NOW + timedelta(hours=2))

    assert esito["state"]["status"] == STATUS_GRANTED
    contatto = db.state.contacts[1]
    assert contatto["marketing_consent"] is True
    assert contatto["marketing_revoked_at"] is None, (
        "lo stato corrente non e' piu' revocato; la revoca resta nello storico"
    )
    assert len(db.state.events) == 3, "nessun evento viene sovrascritto o perso"
    assert [e["decision"] for e in db.state.events] == ["granted", "revoked", "granted"]


def test_m2_privacy_terms_scrive_le_proprie_colonne(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx, purpose=PURPOSE_PRIVACY_TERMS)

    contatto = db.state.contacts[1]
    assert contatto["privacy_terms_accepted"] is True
    assert contatto["privacy_terms_accepted_at"] == NOW
    assert contatto["marketing_consent"] is None, "lo scopo marketing non viene toccato"


def test_m2_evento_in_ritardo_non_fa_arretrare_la_proiezione(db, ctx):
    """Un evento piu' vecchio arrivato dopo viene conservato ma non proietta.

    E' il motivo per cui il repository rilegge l'evento piu' recente invece di
    proiettare quello appena inserito.
    """
    db.state.add_contact(1, agency_id=1)
    revoke(ctx, decided_at=NOW + timedelta(hours=5))
    assert db.state.contacts[1]["marketing_consent"] is False

    grant(ctx, decided_at=NOW)  # deciso PRIMA, arrivato DOPO

    assert len(db.state.events) == 2, "l'evento in ritardo viene comunque conservato"
    assert db.state.contacts[1]["marketing_consent"] is False, (
        "la proiezione riflette la decisione piu' recente, non l'ultima scritta"
    )


# ===========================================================================
# M3  E - la revoca
# ===========================================================================
def test_m3_revoke_da_granted(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx, decided_at=NOW)
    esito = revoke(ctx, decided_at=NOW + timedelta(hours=1))

    assert esito["state"]["status"] == STATUS_REVOKED
    contatto = db.state.contacts[1]
    assert contatto["marketing_consent"] is False
    assert contatto["marketing_revoked_at"] == NOW + timedelta(hours=1)


def test_m3_revoke_non_azzera_la_data_di_concessione(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx, decided_at=NOW)
    revoke(ctx, decided_at=NOW + timedelta(hours=1))

    assert db.state.contacts[1]["marketing_consent_at"] == NOW, (
        "svuotarla cancellerebbe cio' che distingue 'ha detto si' e poi no' da "
        "'non ha mai detto si''"
    )


def test_m3_revoke_non_cancella_lo_storico(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx, decided_at=NOW)
    revoke(ctx, decided_at=NOW + timedelta(hours=1))

    assert len(db.state.events) == 2
    assert db.state.events[0]["decision"] == "granted"


def test_m3_revoca_di_system_rifiutata(db, ctx):
    db.state.add_contact(1, agency_id=1)
    with pytest.raises(ValidationError, match="subject or from an operator"):
        revoke(ctx, actor_type="system")
    assert db.state.events == []


def test_m3_operatore_senza_identita_rifiutato(db, ctx):
    db.state.add_contact(1, agency_id=1)
    with pytest.raises(ValidationError, match="actor_ref"):
        revoke(ctx, source=SOURCE_CRM, actor_type="operator")
    assert db.state.events == []


def test_m3_operatore_con_identita_accettato(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx)
    esito = revoke(ctx, source=SOURCE_CRM, actor_type="operator", actor_ref="operator:10")
    assert esito["state"]["status"] == STATUS_REVOKED
    assert db.state.events[-1]["actor_ref"] == "operator:10"


def test_m3_source_legacy_non_e_scrivibile(db, ctx):
    db.state.add_contact(1, agency_id=1)
    with pytest.raises(ValidationError, match="legacy"):
        grant(ctx, source="legacy")
    assert db.state.events == []


# ===========================================================================
# M4  D - la casella vuota NON revoca
# ===========================================================================
def test_m4_optional_grant_false_non_scrive_niente(db, ctx):
    db.state.add_contact(1, agency_id=1, marketing_consent=True, marketing_consent_at=NOW)

    esito = service.record_optional_grant(
        ctx,
        granted=False,
        contact_id=1,
        purpose=PURPOSE_MARKETING,
        source=SOURCE_PUBLIC_STIMA,
        actor_type="subject",
    )

    assert esito["recorded"] is False
    assert esito["reason"] == "no_grant_to_record"
    assert db.state.events == [], "una casella vuota non e' una decisione"
    assert db.state.contacts[1]["marketing_consent"] is True, (
        "CASO D: il consenso precedentemente valido resta valido"
    )
    assert db.state.contacts[1]["marketing_revoked_at"] is None
    assert db.commits == 0, "nessuna transazione viene nemmeno aperta"


def test_m4_optional_grant_true_si_comporta_come_grant(db, ctx):
    db.state.add_contact(1, agency_id=1)
    esito = service.record_optional_grant(
        ctx,
        granted=True,
        contact_id=1,
        purpose=PURPOSE_MARKETING,
        source=SOURCE_PUBLIC_STIMA,
        actor_type="subject",
    )
    assert esito["recorded"] is True
    assert db.state.contacts[1]["marketing_consent"] is True


def test_m4_non_esiste_una_api_che_revochi_con_un_booleano():
    """Il caso D e' garantito dalla FORMA del modulo, non da un controllo.

    Se un giorno comparisse qui una funzione che accetta un booleano e puo'
    produrre una revoca, il caso D tornerebbe a dipendere dalla disciplina di
    chi la chiama. Questo test e' la guardia contro quel giorno.
    """
    scrittori = {
        nome for nome in dir(service)
        if nome.startswith("record_") and callable(getattr(service, nome))
    }
    assert scrittori == {"record_grant", "record_revocation", "record_optional_grant"}

    import inspect

    firma = inspect.signature(service.record_optional_grant)
    assert "granted" in firma.parameters
    sorgente = inspect.getsource(service.record_optional_grant)
    assert "record_revocation" not in sorgente, (
        "record_optional_grant non deve poter revocare in nessun ramo"
    )


# ===========================================================================
# M5  idempotenza
# ===========================================================================
def test_m5_stessa_chiave_non_duplica(db, ctx):
    db.state.add_contact(1, agency_id=1)
    primo = grant(ctx, idempotency_key="stima:77:marketing")
    secondo = grant(ctx, idempotency_key="stima:77:marketing")

    assert primo["created"] is True
    assert secondo["created"] is False
    assert len(db.state.events) == 1
    assert secondo["event"]["id"] == primo["event"]["id"]
    assert secondo["state"]["status"] == STATUS_GRANTED, (
        "la ripetizione resta idempotente anche nel risultato, non solo nella scrittura"
    )


def test_m5_senza_chiave_due_eventi_veri(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx, decided_at=NOW)
    grant(ctx, decided_at=NOW + timedelta(hours=1))
    assert len(db.state.events) == 2, (
        "senza chiave il chiamante non ha dichiarato di voler essere ripetibile"
    )


def test_m5_chiave_di_altra_agenzia_non_restituisce_riga_altrui(db, ctx):
    db.state.add_contact(1, agency_id=1)
    db.state.add_contact(2, agency_id=2)
    altro = Ctx(agency_id=2, role="agency_owner", user_id=20)
    grant(altro, contact_id=2, idempotency_key="condivisa")

    from consent.exceptions import ConflictError

    with pytest.raises(ConflictError):
        grant(ctx, contact_id=1, idempotency_key="condivisa")


# ===========================================================================
# M6  tenancy
# ===========================================================================
def test_m6_contatto_di_altra_agenzia_non_esiste(db, ctx):
    db.state.add_contact(2, agency_id=2)
    with pytest.raises(NotFoundError):
        grant(ctx, contact_id=2)
    assert db.state.events == []


def test_m6_isolamento_fra_agenzie(db):
    db.state.add_contact(1, agency_id=1)
    db.state.add_contact(2, agency_id=2)
    a = Ctx(agency_id=1, role="agency_owner", user_id=10)
    b = Ctx(agency_id=2, role="agency_owner", user_id=20)

    grant(a, contact_id=1)
    grant(b, contact_id=2)

    assert db.state.contacts[1]["marketing_consent"] is True
    assert db.state.contacts[2]["marketing_consent"] is True
    assert {e["agency_id"] for e in db.state.events} == {1, 2}
    for evento in db.state.events:
        atteso = db.state.contacts[evento["contact_id"]]["agency_id"]
        assert evento["agency_id"] == atteso


def test_m6_ogni_statement_porta_il_predicato_di_agenzia(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx)

    assert db.sql, "nessuno statement eseguito"
    for sql, _params in db.sql:
        if sql.startswith("insert into"):
            continue
        assert "agency_id = %s" in sql, f"statement senza predicato di agenzia: {sql}"


def test_m6_un_agente_e_ristretto_ai_propri_contatti(db):
    """Il ramo `agent` di core.scope viene ereditato, non riscritto."""
    db.state.add_contact(1, agency_id=1, assigned_agent_id=99)
    agente = Ctx(agency_id=1, role="agent", user_id=10)

    with pytest.raises(NotFoundError):
        grant(agente, contact_id=1)

    suo = Ctx(agency_id=1, role="agent", user_id=99)
    esito = grant(suo, contact_id=1)
    assert esito["state"]["status"] == STATUS_GRANTED

    letture = [sql for sql, _ in db.sql if "from contacts c" in sql or "update contacts" in sql]
    assert all("assigned_agent_id = %s" in sql for sql in letture)


# ===========================================================================
# M7  atomicita' e rollback
# ===========================================================================
def test_m7_evento_e_proiezione_nella_stessa_transazione(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx)
    assert db.commits == 1, "un solo commit per decisione"
    assert len(db.state.events) == 1
    assert db.state.contacts[1]["marketing_consent"] is True


def test_m7_se_la_proiezione_fallisce_non_resta_l_evento(db, ctx, monkeypatch):
    db.state.add_contact(1, agency_id=1)

    def esplode(*args, **kwargs):
        raise RuntimeError("proiezione fallita")

    monkeypatch.setattr(repository, "project", esplode)

    with pytest.raises(RuntimeError):
        grant(ctx)

    assert db.state.events == [], "nessun evento senza la sua proiezione"
    assert db.state.contacts[1]["marketing_consent"] is None
    assert db.rollbacks == 1
    assert db.commits == 0


def test_m7_se_l_evento_fallisce_non_resta_la_proiezione(db, ctx, monkeypatch):
    db.state.add_contact(1, agency_id=1)

    def esplode(*args, **kwargs):
        raise RuntimeError("insert fallito")

    monkeypatch.setattr(repository, "insert_event", esplode)

    with pytest.raises(RuntimeError):
        grant(ctx)

    assert db.state.contacts[1]["marketing_consent"] is None
    assert db.commits == 0


def test_m7_il_contatto_viene_bloccato_prima_di_scrivere(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx)
    primo = db.sql[0][0]
    assert primo.startswith("select c.* from contacts c")
    assert "for update" in primo, (
        "senza lock due decisioni concorrenti sullo stesso contatto possono "
        "leggere lo stesso 'ultimo evento'"
    )


def test_m7_nessuno_stato_contraddittorio_viene_mai_committato(db, ctx):
    """I CHECK di 063 sono applicati dal falso al commit: se il percorso
    producesse concesso+revocato insieme, questo test fallirebbe."""
    db.state.add_contact(1, agency_id=1)
    grant(ctx, decided_at=NOW)
    revoke(ctx, decided_at=NOW + timedelta(hours=1))
    grant(ctx, decided_at=NOW + timedelta(hours=2))
    db.state.check_constraints()


# ===========================================================================
# M8  legacy
# ===========================================================================
def test_m8_legacy_granted_senza_storico(db, ctx):
    """I 12 contatti del censimento P29-1.0: concessi, senza provenienza."""
    contatto = {**CONTACT_COLUMNS, "marketing_consent": True, "marketing_consent_at": None}
    stato = service.state_from_projection(contatto, PURPOSE_MARKETING)

    assert stato["status"] == STATUS_GRANTED
    assert stato["legacy"] is True
    assert stato["source"] == "legacy"
    assert stato["notice_id"] is None, "nessuna notice inventata"
    assert stato["granted_at"] is None, "nessun timestamp inventato"


def test_m8_un_consenso_registrato_non_e_legacy(db, ctx):
    db.state.add_contact(1, agency_id=1)
    esito = grant(ctx)
    assert esito["state"]["legacy"] is False
    assert esito["state"]["source"] == SOURCE_PUBLIC_STIMA


def test_m8_il_legacy_non_viene_riscritto_dal_modulo(db, ctx):
    """Nessuna funzione di questo modulo tocca un contatto che non riceve una
    decisione: il legacy resta com'e' finche' qualcuno non decide davvero."""
    db.state.add_contact(12, agency_id=1, marketing_consent=True, marketing_consent_at=None)
    db.state.add_contact(1, agency_id=1)

    grant(ctx, contact_id=1)

    legacy = db.state.contacts[12]
    assert legacy["marketing_consent"] is True
    assert legacy["marketing_consent_at"] is None
    assert legacy["marketing_consent_source"] is None


# ===========================================================================
# M9  nessuna regressione su contacts.marketing_consent, e P24
# ===========================================================================
def test_m9_la_colonna_storica_resta_la_colonna_scritta(db, ctx):
    """P24 legge `contacts.marketing_consent IS TRUE`. Questo test e' il patto."""
    db.state.add_contact(1, agency_id=1)

    grant(ctx, decided_at=NOW)
    assert db.state.contacts[1]["marketing_consent"] is True, "P24 vedrebbe il contatto"

    revoke(ctx, decided_at=NOW + timedelta(hours=1))
    assert db.state.contacts[1]["marketing_consent"] is False, (
        "P24 smette di vederlo senza che una sola riga di P24 venga modificata"
    )


def test_m9_p24_legge_ancora_la_colonna_che_scriviamo():
    """Il predicato reale di P24, letto dal sorgente."""
    from pathlib import Path

    sorgente = (
        Path(__file__).resolve().parents[1] / "database_revival" / "eligibility.py"
    ).read_text(encoding="utf-8")
    assert "c.marketing_consent IS TRUE" in sorgente

    from consent.enums import PROJECTION_COLUMNS

    assert PROJECTION_COLUMNS[PURPOSE_MARKETING]["flag"] == "marketing_consent"


def test_m9_marketing_consent_at_resta_il_granted_at():
    from consent.enums import PROJECTION_COLUMNS

    assert PROJECTION_COLUMNS[PURPOSE_MARKETING]["granted_at"] == "marketing_consent_at", (
        "rinominarla romperebbe core/schemas.py e la UI, che la leggono gia'"
    )


def test_m9_il_modulo_non_scrive_in_domini_altrui(db, ctx):
    db.state.add_contact(1, agency_id=1)
    grant(ctx)
    for sql, _ in db.sql:
        for tabella in ("activities", "tasks", "seller_timeline_events",
                        "next_best_actions", "followup_actions", "leads", "stime"):
            assert tabella not in sql, f"P29-1.2 ha toccato {tabella}: {sql}"
