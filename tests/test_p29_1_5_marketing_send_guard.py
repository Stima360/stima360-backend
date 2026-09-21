"""P29-1.5 - la guardia centrale del consenso marketing.

`can_send_marketing(ctx, contact_id)` risponde a una domanda sola: questo
contatto possiede ORA un consenso marketing valido? Non decide se sia
opportuno scrivergli, non guarda il lead, non guarda i canali.

Nessun database: un falso in memoria serve le due letture che la guardia fa -
il contatto nello scope e l'evento piu' recente per `marketing` - e registra
ogni istruzione, cosi' le proprieta' "read only", "senza cache" e "tenant
scoped" si provano guardando l'SQL emesso e non fidandosi del codice.

Mappa:

    E   eventi espliciti: grant, revoke, re-grant
    O   ordine degli eventi e arrivi fuori ordine
    L   legacy: nessun evento, vale la proiezione
    X   stati incoerenti -> fail closed
    N   notice e source non bloccano
    T   tenancy
    R   read-only e assenza di cache
    C   il contratto: la guardia e' l'unica sorgente di autorizzazione
"""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from consent import repository as consent_repository
from consent.enums import (
    ALLOWING_REASONS,
    PURPOSE_MARKETING,
    REASON_ALLOW_EXPLICIT_GRANT,
    REASON_ALLOW_LEGACY_GRANT,
    REASON_DENY_INCONSISTENT_STATE,
    REASON_DENY_NEVER_GIVEN,
    REASON_DENY_REVOKED,
    SEND_DECISION_REASONS,
    SOURCE_PUBLIC_STIMA,
    STATUS_GRANTED,
    STATUS_NEVER_GIVEN,
    STATUS_REVOKED,
)
from consent.exceptions import NotFoundError
from consent.guard import MarketingSendDecision, can_send_marketing

ROOT = Path(__file__).resolve().parents[1]

AGENZIA = 7
ALTRA_AGENZIA = 9
ORA = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

COLONNE_CONSENSO = (
    "marketing_consent",
    "marketing_consent_at",
    "marketing_revoked_at",
    "marketing_consent_source",
    "marketing_consent_notice_id",
)


class Ctx:
    is_platform_admin = False

    def __init__(self, agency_id=AGENZIA, role=None, user_id=None):
        self.agency_id = agency_id
        self.role = role
        self.user_id = user_id

    def require_agency(self):
        return self.agency_id


# ===========================================================================
# Il falso
# ===========================================================================
class Stato:
    def __init__(self):
        self.contacts: list[dict] = []
        self.events: list[dict] = []
        self.next_contact_id = 1
        self.next_event_id = 1

    def contatto(self, *, agency_id=AGENZIA, **valori):
        riga = {
            "id": self.next_contact_id,
            "agency_id": agency_id,
            "assigned_agent_id": None,
            "status": "active",
            **{colonna: None for colonna in COLONNE_CONSENSO},
        }
        riga.update(valori)
        self.next_contact_id += 1
        self.contacts.append(riga)
        return riga

    def evento(self, *, contact_id, decision, decided_at, agency_id=AGENZIA,
               source=SOURCE_PUBLIC_STIMA, notice_id=None, purpose=PURPOSE_MARKETING):
        riga = {
            "id": self.next_event_id,
            "agency_id": agency_id,
            "contact_id": contact_id,
            "purpose": purpose,
            "decision": decision,
            "decided_at": decided_at,
            "source": source,
            "notice_id": notice_id,
            "actor_type": "subject",
            "actor_ref": None,
            "evidence_type": "stima",
            "evidence_ref": "1",
            "note": None,
            "idempotency_key": f"k{self.next_event_id}",
        }
        self.next_event_id += 1
        self.events.append(riga)
        return riga


class FakeCursor:
    """Serve l'UNICA query decisionale: contatto + ultimo evento, insieme.

    Il falso replica il LEFT JOIN LATERAL della query reale - incluso il suo
    ordinamento e i suoi due predicati di agenzia - perche' e' esattamente cio'
    che i test devono poter sbagliare.
    """

    def __init__(self, stato, log):
        self.stato = stato
        self.log = log
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.log.append((sql, copy.deepcopy(params)))

        if "from contacts c" not in sql or "left join lateral" not in sql:
            raise AssertionError(f"SQL inatteso nella guardia: {sql}")

        # params: [agency_evento, purpose, contact_id, agency_contatto, (agente)]
        agency_evento, purpose, contact_id, agency_contatto = params[0], params[1], params[2], params[3]

        contatti = [
            c for c in self.stato.contacts
            if c["id"] == contact_id and c["agency_id"] == agency_contatto
        ]
        if "assigned_agent_id" in sql:
            contatti = [c for c in contatti if c["assigned_agent_id"] == params[4]]
        if not contatti:
            self.rows = []
            return
        contatto = contatti[0]

        eventi = [
            e for e in self.stato.events
            if e["agency_id"] == agency_evento
            and e["contact_id"] == contatto["id"]
            and e["purpose"] == purpose
        ]
        eventi.sort(key=lambda e: (e["decided_at"], e["id"]), reverse=True)
        evento = eventi[0] if eventi else None

        riga = dict(contatto)
        riga.update({
            "consent_event_id": evento["id"] if evento else None,
            "consent_event_decision": evento["decision"] if evento else None,
            "consent_event_decided_at": evento["decided_at"] if evento else None,
            "consent_event_source": evento["source"] if evento else None,
            "consent_event_notice_id": evento["notice_id"] if evento else None,
        })
        self.rows = [riga]

    def fetchone(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None

    def fetchall(self):
        return copy.deepcopy(self.rows)

    def close(self):
        pass


class FakeDatabase:
    def __init__(self):
        self.stato = Stato()
        self.sql: list = []
        self.cursori_aperti = 0
        self.commit_richiesti = 0

    @contextmanager
    def cursor(self, *, commit: bool = False):
        self.cursori_aperti += 1
        if commit:
            self.commit_richiesti += 1
        yield None, FakeCursor(self.stato, self.sql)


@pytest.fixture
def db(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(consent_repository, "consent_cursor", database.cursor)
    return database


@pytest.fixture
def ctx():
    return Ctx()


# ===========================================================================
# E - eventi espliciti
# ===========================================================================
def test_e1_grant_esplicito_autorizza(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA,
                          marketing_consent_source=SOURCE_PUBLIC_STIMA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is True
    assert esito.reason == REASON_ALLOW_EXPLICIT_GRANT
    assert esito.state == STATUS_GRANTED
    assert esito.legacy is False
    assert esito.contact_id == c["id"] and esito.agency_id == AGENZIA


def test_e2_revoca_esplicita_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA,
                          marketing_revoked_at=ORA + timedelta(hours=1))
    db.stato.evento(contact_id=c["id"], decision="revoked",
                    decided_at=ORA + timedelta(hours=1))

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is False
    assert esito.reason == REASON_DENY_REVOKED
    assert esito.state == STATUS_REVOKED


def test_e3_grant_poi_revoca_nega_nonostante_il_grant_precedente(db, ctx):
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA,
                          marketing_revoked_at=ORA + timedelta(hours=2))
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="revoked",
                    decided_at=ORA + timedelta(hours=2))

    esito = can_send_marketing(ctx, c["id"])

    assert esito.reason == REASON_DENY_REVOKED
    assert esito.allowed is False


def test_e4_regrant_dopo_revoca_autorizza(db, ctx):
    c = db.stato.contatto(marketing_consent=True,
                          marketing_consent_at=ORA + timedelta(hours=5),
                          marketing_revoked_at=None)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="revoked", decided_at=ORA + timedelta(hours=2))
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA + timedelta(hours=5))

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is True
    assert esito.reason == REASON_ALLOW_EXPLICIT_GRANT
    assert esito.legacy is False


# ===========================================================================
# O - ordine degli eventi
# ===========================================================================
def test_o1_l_evento_corrente_e_quello_con_decided_at_maggiore(db, ctx):
    """Non MAX(id): l'id e' solo lo spareggio."""
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA,
                          marketing_revoked_at=ORA + timedelta(hours=3))
    db.stato.evento(contact_id=c["id"], decision="revoked", decided_at=ORA + timedelta(hours=3))
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)  # id maggiore, data minore

    esito = can_send_marketing(ctx, c["id"])

    assert esito.reason == REASON_DENY_REVOKED, (
        "l'evento con id piu' alto e' piu' VECCHIO: non deve vincere"
    )


def test_o2_a_parita_di_istante_vince_l_id_piu_alto(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="revoked", decided_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.reason == REASON_ALLOW_EXPLICIT_GRANT


def test_o3_la_query_usa_l_ordine_del_dominio(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    can_send_marketing(ctx, c["id"])

    query = db.sql[0][0]
    assert "order by ce.decided_at desc, ce.id desc" in query
    assert "limit 1" in query
    assert "max(" not in query


def test_o4_un_evento_in_ritardo_non_fa_arretrare_la_decisione(db, ctx):
    """Deciso prima, scritto dopo: lo stato corrente non cambia."""
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA,
                          marketing_revoked_at=ORA + timedelta(hours=4))
    db.stato.evento(contact_id=c["id"], decision="revoked", decided_at=ORA + timedelta(hours=4))
    prima = can_send_marketing(ctx, c["id"])

    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA - timedelta(days=1))
    dopo = can_send_marketing(ctx, c["id"])

    assert prima.reason == dopo.reason == REASON_DENY_REVOKED


def test_o5_gli_eventi_di_un_altro_purpose_non_contano(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="revoked",
                    decided_at=ORA + timedelta(hours=9), purpose="privacy_terms")

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is True, "una revoca di privacy_terms non tocca il marketing"


# ===========================================================================
# L - legacy: nessun evento
# ===========================================================================
def test_l1_legacy_true_senza_eventi_autorizza(db, ctx):
    """I 12 contatti del censimento P29-1.0: concessi prima del registro."""
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=None)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is True
    assert esito.reason == REASON_ALLOW_LEGACY_GRANT
    assert esito.legacy is True
    assert esito.state == STATUS_GRANTED
    assert esito.event_id is None
    # C8: la guardia non inventa una source sintetica "legacy".
    assert esito.source is None


def test_l1b_legacy_true_con_source_reale_restituisce_quella_source(db, ctx):
    """C8: se la proiezione porta una source vera, la guardia la riporta tale e quale."""
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=None,
                          marketing_consent_source=SOURCE_PUBLIC_STIMA)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is True
    assert esito.reason == REASON_ALLOW_LEGACY_GRANT
    assert esito.legacy is True
    assert esito.event_id is None
    assert esito.source == SOURCE_PUBLIC_STIMA


def test_l2_legacy_false_senza_eventi_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=False)
    esito = can_send_marketing(ctx, c["id"])
    assert esito.allowed is False
    assert esito.reason == REASON_DENY_NEVER_GIVEN
    assert esito.state == STATUS_NEVER_GIVEN


def test_l3_legacy_null_senza_eventi_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=None)
    esito = can_send_marketing(ctx, c["id"])
    assert esito.allowed is False
    assert esito.reason == REASON_DENY_NEVER_GIVEN


def test_l4_il_contatto_legacy_non_viene_reinterpretato(db, ctx):
    """La guardia non migra niente e non scrive niente su di lui."""
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=None,
                          marketing_consent_source=None)
    prima = copy.deepcopy(c)

    can_send_marketing(ctx, c["id"])

    assert db.stato.contacts[0] == prima, "il contatto legacy e' rimasto identico"
    assert db.stato.events == [], "nessun evento fabbricato"


# ===========================================================================
# X - stati incoerenti: fail closed
# ===========================================================================
def test_x1_evento_granted_con_proiezione_non_concessa_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is False
    assert esito.reason == REASON_DENY_INCONSISTENT_STATE


def test_x2_evento_revoked_con_proiezione_concessa_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="revoked", decided_at=ORA + timedelta(hours=1))

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is False
    assert esito.reason == REASON_DENY_INCONSISTENT_STATE


def test_x3_grant_con_revoked_at_ancora_valorizzato_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA,
                          marketing_revoked_at=ORA - timedelta(days=1))
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    esito = can_send_marketing(ctx, c["id"])
    assert esito.reason == REASON_DENY_INCONSISTENT_STATE


def test_x4_grant_senza_istante_di_concessione_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=None)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    esito = can_send_marketing(ctx, c["id"])
    assert esito.reason == REASON_DENY_INCONSISTENT_STATE


def test_x5_revoca_senza_istante_di_revoca_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA,
                          marketing_revoked_at=None)
    db.stato.evento(contact_id=c["id"], decision="revoked", decided_at=ORA + timedelta(hours=1))

    esito = can_send_marketing(ctx, c["id"])
    assert esito.reason == REASON_DENY_INCONSISTENT_STATE


def test_x6_legacy_concesso_e_revocato_insieme_nega(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA,
                          marketing_revoked_at=ORA)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is False
    assert esito.reason == REASON_DENY_INCONSISTENT_STATE
    assert esito.legacy is True


def test_x7_la_guardia_non_ripara_lo_stato_incoerente(db, ctx):
    c = db.stato.contatto(marketing_consent=False, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    prima = copy.deepcopy(db.stato.contacts)

    can_send_marketing(ctx, c["id"])

    assert db.stato.contacts == prima, "la correzione e' una decisione di una persona"


# ===========================================================================
# N - notice e source non bloccano
# ===========================================================================
def test_n1_notice_id_nullo_non_blocca_un_grant_valido(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA,
                          marketing_consent_notice_id=None)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA, notice_id=None)

    esito = can_send_marketing(ctx, c["id"])
    assert esito.allowed is True


def test_n2_source_public_stima_autorizza_normalmente(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA,
                          marketing_consent_source=SOURCE_PUBLIC_STIMA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA,
                    source=SOURCE_PUBLIC_STIMA)

    esito = can_send_marketing(ctx, c["id"])
    assert esito.allowed is True
    assert esito.source == SOURCE_PUBLIC_STIMA


def test_n3_source_assente_sulla_proiezione_non_blocca(db, ctx):
    """Il modello legacy non la garantiva: non puo' essere un requisito."""
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA,
                          marketing_consent_source=None)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    assert can_send_marketing(ctx, c["id"]).allowed is True


def test_n4_privacy_terms_non_e_un_requisito(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    assert can_send_marketing(ctx, c["id"]).allowed is True
    sorgente = (ROOT / "consent" / "guard.py").read_text(encoding="utf-8")
    assert "privacy_terms_accepted" not in sorgente


# ===========================================================================
# T - tenancy
# ===========================================================================
def test_t1_il_tenant_corretto_ottiene_la_decisione(db, ctx):
    c = db.stato.contatto(agency_id=AGENZIA, marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA, agency_id=AGENZIA)

    esito = can_send_marketing(ctx, c["id"])
    assert esito.allowed is True and esito.agency_id == AGENZIA


def test_t2_cross_tenant_non_puo_leggere_ne_autorizzare(db):
    c = db.stato.contatto(agency_id=ALTRA_AGENZIA, marketing_consent=True,
                          marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA,
                    agency_id=ALTRA_AGENZIA)

    with pytest.raises(NotFoundError):
        can_send_marketing(Ctx(agency_id=AGENZIA), c["id"])


def test_t3_il_contatto_inesistente_segue_lo_stesso_pattern(db, ctx):
    with pytest.raises(NotFoundError):
        can_send_marketing(ctx, 999)


def test_t4_ogni_statement_porta_il_predicato_di_agenzia(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    can_send_marketing(ctx, c["id"])

    assert db.sql
    for sql, _ in db.sql:
        assert "agency_id = %s" in sql, f"statement senza predicato di agenzia: {sql}"


def test_t5_un_evento_di_un_altra_agenzia_non_e_visibile(db, ctx):
    """Lo stesso contact_id con un evento stampato su un'altra agenzia."""
    c = db.stato.contatto(agency_id=AGENZIA, marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="revoked",
                    decided_at=ORA + timedelta(days=1), agency_id=ALTRA_AGENZIA)

    esito = can_send_marketing(ctx, c["id"])

    assert esito.reason == REASON_ALLOW_LEGACY_GRANT, (
        "l'evento dell'altra agenzia non deve entrare nella decisione"
    )


def test_t6_un_agente_eredita_il_restringimento_di_core(db):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    c["assigned_agent_id"] = 99

    with pytest.raises(NotFoundError):
        can_send_marketing(Ctx(role="agent", user_id=10), c["id"])

    assert can_send_marketing(Ctx(role="agent", user_id=99), c["id"]).allowed is True


# ===========================================================================
# R - read only e assenza di cache
# ===========================================================================
def test_r1_la_guardia_non_scrive_nulla(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    can_send_marketing(ctx, c["id"])

    for sql, _ in db.sql:
        assert sql.startswith("select"), f"istruzione non di lettura: {sql}"
    assert db.commit_richiesti == 0, "nessun cursore in scrittura"


def test_r2_la_guardia_non_prende_lock(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    can_send_marketing(ctx, c["id"])

    for sql, _ in db.sql:
        assert "for update" not in sql, "decidere non deve serializzare gli invii"
        assert "pg_advisory" not in sql


def test_r3_la_guardia_non_genera_consent_event(db, ctx):
    c = db.stato.contatto(marketing_consent=None)
    can_send_marketing(ctx, c["id"])
    assert db.stato.events == []


def test_r4_la_guardia_non_modifica_contacts(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    prima = copy.deepcopy(db.stato.contacts)
    can_send_marketing(ctx, c["id"])
    assert db.stato.contacts == prima


def test_r5_chiamate_ripetute_non_scrivono_e_non_cambiano_risposta(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    esiti = [can_send_marketing(ctx, c["id"]) for _ in range(3)]

    assert {e.reason for e in esiti} == {REASON_ALLOW_EXPLICIT_GRANT}
    assert db.stato.events == db.stato.events  # nessuna crescita
    assert len(db.stato.events) == 1


def test_r6_NO_CACHE_una_revoca_fra_due_chiamate_ha_effetto_subito(db, ctx):
    """La prova che conta: niente memoizzazione, niente snapshot."""
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    prima = can_send_marketing(ctx, c["id"])
    assert prima.allowed is True

    # La revoca arriva fra le due chiamate, come accadrebbe dal vivo.
    c["marketing_consent"] = False
    c["marketing_revoked_at"] = ORA + timedelta(minutes=1)
    db.stato.evento(contact_id=c["id"], decision="revoked",
                    decided_at=ORA + timedelta(minutes=1))

    dopo = can_send_marketing(ctx, c["id"])

    assert dopo.allowed is False
    assert dopo.reason == REASON_DENY_REVOKED


def test_r7_ogni_chiamata_rilegge_davvero_il_database(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    can_send_marketing(ctx, c["id"])
    assert len(db.sql) == 1
    can_send_marketing(ctx, c["id"])

    assert len(db.sql) == 2, "la seconda chiamata non ha riletto il database"
    assert db.cursori_aperti == 2


def test_r8_UNA_SOLA_QUERY_DECISIONALE(db, ctx):
    """C6: proiezione ed evento dallo stesso statement-level snapshot.

    Sotto READ COMMITTED - l'isolamento predefinito di PostgreSQL - lo
    snapshot e' per STATEMENT: due SELECT consecutive, anche sulla stessa
    connessione, possono vedere due commit diversi. Una revoca che atterrasse
    fra le due produrrebbe `deny_inconsistent_state` su uno stato sano.
    """
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)

    can_send_marketing(ctx, c["id"])

    assert len(db.sql) == 1, f"statement emessi: {len(db.sql)}"
    assert db.cursori_aperti == 1

    query = db.sql[0][0]
    assert "left join lateral" in query
    assert "from contacts c" in query and "from consent_events ce" in query


def test_r8b_la_query_singola_porta_ENTRAMBI_i_predicati_di_agenzia(db, ctx):
    """Una query sola, due autorita' sulla tenancy: nessuna delle due si perde."""
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    can_send_marketing(ctx, c["id"])

    query = db.sql[0][0]
    assert query.count("agency_id = %s") == 2, (
        "servono il predicato su contacts e quello su consent_events"
    )
    assert "ce.agency_id = %s" in query
    assert "c.agency_id = %s" in query


def test_r8c_la_query_singola_non_prende_lock_e_non_scrive(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    db.stato.evento(contact_id=c["id"], decision="granted", decided_at=ORA)
    can_send_marketing(ctx, c["id"])

    query = db.sql[0][0]
    assert query.startswith("select")
    assert "for update" not in query and "for share" not in query
    assert db.commit_richiesti == 0


def test_r9_nessuno_stato_di_modulo(db, ctx):
    import consent.guard as guard

    globali = {
        nome: valore for nome, valore in vars(guard).items()
        if not nome.startswith("_")
        and isinstance(valore, (dict, list, set))
        and nome not in ("PROJECTION_COLUMNS", "ALLOWING_REASONS")
    }
    assert not globali, f"contenitori mutabili a livello di modulo: {sorted(globali)}"

    sorgente = (ROOT / "consent" / "guard.py").read_text(encoding="utf-8")
    for sospetto in ("lru_cache", "cached_property", "functools.cache", "@cache"):
        assert sospetto not in sorgente, f"{sospetto}: la guardia non deve memorizzare"


# ===========================================================================
# C - il contratto
# ===========================================================================
def test_c1_esiste_una_sola_implementazione_della_decisione():
    import consent.guard as guard

    pubbliche = {
        nome for nome in dir(guard)
        if not nome.startswith("_") and callable(getattr(guard, nome))
        and getattr(getattr(guard, nome), "__module__", "") == "consent.guard"
    }
    # SENTINELLA AGGIORNATA DA P29-3C: `can_send_marketing_bulk` e' una
    # seconda LETTURA, non una seconda decisione. Entrambe le porte
    # chiamano `_decidi`, che e' l'unica implementazione - lo verifica
    # `test_23` di P29-3C sull'AST, e una prova su PostgreSQL confronta le
    # due strade stato per stato. Il gate di invio del dispatcher continua a
    # passare da `can_send_marketing`, una decisione alla volta.
    assert pubbliche == {"can_send_marketing", "can_send_marketing_bulk",
                         "MarketingSendDecision"}


def test_c2_la_decisione_e_tipizzata_e_congelata(db, ctx):
    c = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    esito = can_send_marketing(ctx, c["id"])

    assert isinstance(esito, MarketingSendDecision)
    with pytest.raises(Exception):
        esito.allowed = True  # frozen dataclass


def test_c2b_la_decisione_NON_E_USABILE_come_booleano(db, ctx):
    """C7: `if decision:` sarebbe sempre vero e autorizzerebbe di fatto tutto.

    Togliere `__bool__` non basta: un dataclass senza `__bool__` e' truthy
    per default, quindi il buco resterebbe aperto e silenzioso. La decisione
    rifiuta la conversione, cosi' l'errore e' un TypeError al primo giro di
    test e non un invio non autorizzato in produzione.
    """
    c = db.stato.contatto(marketing_consent=False)
    esito = can_send_marketing(ctx, c["id"])

    assert esito.allowed is False
    with pytest.raises(TypeError, match=r"\.allowed"):
        bool(esito)
    with pytest.raises(TypeError, match=r"\.allowed"):
        if esito:  # pragma: no cover - il ramo non deve essere raggiunto
            pass
    with pytest.raises(TypeError, match=r"\.allowed"):
        not esito


def test_c2c_allowed_continua_a_funzionare_normalmente(db, ctx):
    """C7 non deve rendere scomodo l'uso corretto: `.allowed` e' un bool puro."""
    concesso = db.stato.contatto(marketing_consent=True, marketing_consent_at=ORA)
    negato = db.stato.contatto(marketing_consent=False)

    si = can_send_marketing(ctx, concesso["id"])
    no = can_send_marketing(ctx, negato["id"])

    assert si.allowed is True and no.allowed is False
    assert bool(si.allowed) is True and bool(no.allowed) is False
    assert (si.allowed and not no.allowed) is True
    # l'uso corretto passa per l'attributo, e li' il booleano funziona
    inviabili = [d for d in (si, no) if d.allowed]
    assert inviabili == [si]


def test_c3_allowed_e_reason_non_possono_contraddirsi():
    with pytest.raises(AssertionError):
        MarketingSendDecision(
            allowed=True, reason=REASON_DENY_REVOKED, state=STATUS_REVOKED,
            contact_id=1, agency_id=AGENZIA, legacy=False,
        )


def test_c4_i_motivi_sono_un_insieme_chiuso():
    assert SEND_DECISION_REASONS == {
        REASON_ALLOW_EXPLICIT_GRANT,
        REASON_ALLOW_LEGACY_GRANT,
        REASON_DENY_REVOKED,
        REASON_DENY_NEVER_GIVEN,
        REASON_DENY_INCONSISTENT_STATE,
    }
    assert ALLOWING_REASONS < SEND_DECISION_REASONS
    assert ALLOWING_REASONS == {REASON_ALLOW_EXPLICIT_GRANT, REASON_ALLOW_LEGACY_GRANT}


def test_c5_ogni_motivo_restituito_appartiene_all_insieme(db, ctx):
    casi = [
        dict(marketing_consent=True, marketing_consent_at=ORA),
        dict(marketing_consent=False),
        dict(marketing_consent=None),
        dict(marketing_consent=True, marketing_consent_at=ORA, marketing_revoked_at=ORA),
    ]
    for valori in casi:
        c = db.stato.contatto(**valori)
        esito = can_send_marketing(ctx, c["id"])
        assert esito.reason in SEND_DECISION_REASONS
        assert esito.allowed == (esito.reason in ALLOWING_REASONS)


def test_c6_la_guardia_non_ha_un_parametro_di_canale():
    """Un consenso solo per Email e WhatsApp: la differenza non e'
    rappresentabile, quindi non e' dimenticabile."""
    import inspect

    firma = inspect.signature(can_send_marketing)
    assert list(firma.parameters) == ["ctx", "contact_id"]

    # Si guarda il CODICE, non i commenti: il docstring della guardia nomina
    # `channel` proprio per spiegare perche' non esiste, ed e' giusto che lo
    # faccia.
    import ast

    albero = ast.parse((ROOT / "consent" / "guard.py").read_text(encoding="utf-8"))
    nomi = {n.id for n in ast.walk(albero) if isinstance(n, ast.Name)}
    nomi |= {n.arg for n in ast.walk(albero) if isinstance(n, ast.arg)}
    nomi |= {n.attr for n in ast.walk(albero) if isinstance(n, ast.Attribute)}
    for parola in ("channel", "canale", "email_only", "whatsapp_only", "is_email", "is_whatsapp"):
        assert parola not in nomi, f"la guardia distingue i canali: {parola}"


def test_c7_nessun_sender_marketing_decide_leggendo_la_colonna():
    """Il contratto che P29-2 dovra' rispettare.

    Non si vieta genericamente di LEGGERE `marketing_consent`: la UI la mostra,
    P24 la usa per l'eleggibilita', il bridge la proietta. Si registra invece
    l'elenco dei lettori legittimi e la loro categoria, cosi' un file NUOVO che
    la legge - il primo sender marketing, per esempio - non passa inosservato.
    """
    LETTORI_LEGITTIMI = {
        # categoria B - eleggibilita', non autorizzazione all'invio
        "database_revival/eligibility.py",
        # categoria E - il dominio e la sua guardia
        "consent/enums.py", "consent/guard.py", "consent/repository.py",
        "consent/service.py", "consent/__init__.py",
        # categoria E - CORE: la guardia che vieta la scrittura e il bridge
        "core/repository.py", "core/service.py", "core/schemas.py",
        # categoria E - il flusso pubblico: main.py INOLTRA il valore del form
        # al bridge (`marketing_consent=consenso_marketing`) e non autorizza
        # nulla. L'unico invio che fa e' l'email di SERVIZIO con il PDF, che
        # non passa e non deve passare da questa guardia.
        "main.py",
    }

    trovati = set()
    for percorso in ROOT.rglob("*.py"):
        parti = percorso.parts
        if any(p in parti for p in (".venv", "__pycache__", "tests", "migrations", "scripts")):
            continue
        if percorso.name.startswith("run_"):
            continue
        testo = percorso.read_text(encoding="utf-8")
        if re.search(r"\bmarketing_consent\b", testo):
            trovati.add(percorso.relative_to(ROOT).as_posix())

    nuovi = trovati - LETTORI_LEGITTIMI
    assert not nuovi, (
        f"file nuovi che leggono marketing_consent: {sorted(nuovi)}. "
        "Se e' un sender marketing, l'autorizzazione deve venire da "
        "consent.guard.can_send_marketing, non dalla colonna."
    )


def test_c8_la_guardia_non_e_agganciata_a_nessun_sender():
    """P29-1.5 decide e basta: nessun invio e' stato modificato."""
    for percorso in ("main.py", "database.py"):
        testo = (ROOT / percorso).read_text(encoding="utf-8")
        assert "can_send_marketing" not in testo, (
            f"{percorso}: P29-1.5 non deve toccare i sender esistenti"
        )
