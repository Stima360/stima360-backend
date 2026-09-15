"""P29-1.4 - il consenso della stima pubblica passa dal dominio.

Era l'ultimo write-site della proiezione fuori da `consent/`: il bridge
scriveva `contacts.marketing_consent` nella propria INSERT, senza evento,
senza provenienza, senza prova. Da P29-1.4 la casella spuntata diventa un
evento `granted` registrato dal dominio DENTRO la transazione del bridge.

Nessun database: un falso in memoria modella l'intera transazione - contatto,
lead, collegamento alla stima, evento di consenso e proiezione - e la riversa
sullo stato solo al commit, perche' l'atomicita' e' il requisito e un falso che
applicasse subito le scritture non potrebbe dimostrarla.

Mappa:

    A  nuovo contatto + TRUE          -> un grant
    B  nuovo contatto + FALSE/NULL    -> zero eventi
    C  contatto TRUE + stima FALSE    -> resta TRUE, nessun evento
    D  contatto NULL/FALSE + TRUE     -> grant
    E  contatto revocato + TRUE       -> re-grant
    F  contatto revocato + FALSE      -> resta revocato
    G  retry della stessa stima       -> nessun duplicato
    H  decided_at, source, evidence, notice
    I  tenancy
    L  il bridge non scrive piu' la proiezione
    M  linkage, dedup e conflitti invariati
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from consent.enums import (
    PURPOSE_MARKETING,
    SOURCE_PUBLIC_STIMA,
    STATUS_GRANTED,
    STATUS_NEVER_GIVEN,
    STATUS_REVOKED,
)
from consent.service import state_from_projection
from core import repository as core_repository
from core import service as core_service
from core.scope import ProgrammingError
from operator_auth.context import SystemAgencyContext

AGENZIA = 7
ALTRA_AGENZIA = 9
DECISO_IL = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)

CTX = SystemAgencyContext(agency_id=AGENZIA, origin="public_stima")
CTX_ALTRA = SystemAgencyContext(agency_id=ALTRA_AGENZIA, origin="public_stima")

COLONNE_CONSENSO = (
    "marketing_consent",
    "marketing_consent_at",
    "marketing_revoked_at",
    "marketing_consent_source",
    "marketing_consent_notice_id",
)


# ===========================================================================
# Il falso
# ===========================================================================
class Stato:
    def __init__(self):
        self.contacts: list[dict] = []
        self.leads: list[dict] = []
        self.links: list[dict] = []
        self.consent_events: list[dict] = []
        self.next_contact_id = 1
        self.next_lead_id = 1
        self.next_event_id = 1

    def aggiungi_contatto(self, **valori):
        riga = {
            "id": self.next_contact_id,
            "agency_id": AGENZIA,
            "status": "active",
            "archived_at": None,
            "assigned_agent_id": None,
            "email_normalized": None,
            "phone_normalized": None,
            "updated_at": None,
            **{colonna: None for colonna in COLONNE_CONSENSO},
        }
        riga.update(valori)
        self.next_contact_id += 1
        self.contacts.append(riga)
        return riga


class FakeCursor:
    def __init__(self, stato: Stato, log: list):
        self.stato = stato
        self.log = log
        self.rows: list = []
        self.rowcount = 0

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.log.append((sql, copy.deepcopy(params)))

        if "pg_advisory_xact_lock" in sql:
            self.rows = [{"locked": True}]
            return
        if "from lead_stime ls" in sql:
            agency = params[-1]
            trovati = [
                {"lead_id": l["lead_id"], "contact_id": self._lead(l["lead_id"])["contact_id"]}
                for l in self.stato.links
                if l["stima_id"] == params[0]
                and self._lead(l["lead_id"])["agency_id"] == agency
            ]
            self.rows = trovati[:1]
            return

        # --- consenso: il lock del contatto, prima dell'evento --------------
        if "from contacts c where c.id" in sql and "for update" in sql:
            self.rows = [
                c for c in self.stato.contacts
                if c["id"] == params[0] and c["agency_id"] == params[1]
            ][:1]
            return

        if "from contacts c" in sql or "from contacts" in sql and "c.email_normalized" in sql:
            if "c.email_normalized" in sql or "c.phone_normalized" in sql:
                colonna = "email_normalized" if "c.email_normalized" in sql else "phone_normalized"
                agency, valore = params[0], params[-1]
                self.rows = [
                    c for c in self.stato.contacts
                    if c.get(colonna) == valore and c["agency_id"] == agency
                ]
                return

        if "insert into contacts" in sql:
            riga = {
                **{colonna: None for colonna in COLONNE_CONSENSO},
                **copy.deepcopy(params),
                "id": self.stato.next_contact_id,
                "archived_at": None,
                "assigned_agent_id": None,
                "updated_at": None,
            }
            self.stato.next_contact_id += 1
            self.stato.contacts.append(riga)
            self.rows = [copy.deepcopy(riga)]
            self.rowcount = 1
            return

        if "insert into consent_events" in sql:
            chiave = params["idempotency_key"]
            if chiave is not None and any(
                e["idempotency_key"] == chiave for e in self.stato.consent_events
            ):
                self.rows = []  # ON CONFLICT DO NOTHING
                return
            riga = {**copy.deepcopy(params), "id": self.stato.next_event_id}
            self.stato.next_event_id += 1
            self.stato.consent_events.append(riga)
            self.rows = [copy.deepcopy(riga)]
            self.rowcount = 1
            return

        if "from consent_events ce" in sql and "idempotency_key" in sql:
            agency, chiave = params[0], params[1]
            trovato = next(
                (
                    e for e in self.stato.consent_events
                    if e["idempotency_key"] == chiave and e["agency_id"] == agency
                ),
                None,
            )
            self.rows = [copy.deepcopy(trovato)] if trovato else []
            return

        if "from consent_events ce" in sql:
            agency, contact_id, purpose = params[0], params[1], params[2]
            trovati = [
                e for e in self.stato.consent_events
                if e["agency_id"] == agency
                and e["contact_id"] == contact_id
                and e["purpose"] == purpose
            ]
            trovati.sort(key=lambda e: (e["decided_at"], e["id"]), reverse=True)
            self.rows = [copy.deepcopy(trovati[0])] if trovati else []
            return

        if sql.startswith("update contacts c set"):
            contact_id, agency = params[-2], params[-1]
            riga = next(
                (
                    c for c in self.stato.contacts
                    if c["id"] == contact_id and c["agency_id"] == agency
                ),
                None,
            )
            if riga is None:
                self.rows = []
                return
            concesso = "marketing_consent = true" in sql
            riga["marketing_consent"] = concesso
            if concesso:
                riga["marketing_consent_at"] = params[0]
                riga["marketing_revoked_at"] = None
            else:
                riga["marketing_revoked_at"] = params[0]
            riga["marketing_consent_source"] = params[1]
            riga["marketing_consent_notice_id"] = params[2]
            self.rows = [copy.deepcopy(riga)]
            self.rowcount = 1
            return

        if "insert into leads" in sql:
            riga = {**copy.deepcopy(params), "id": self.stato.next_lead_id}
            self.stato.next_lead_id += 1
            self.stato.leads.append(riga)
            self.rows = [copy.deepcopy(riga)]
            self.rowcount = 1
            return

        if "insert into lead_stime" in sql:
            lead_id, stima_id, relazione = params
            esistente = next(
                (
                    l for l in self.stato.links
                    if l["lead_id"] == lead_id and l["stima_id"] == stima_id
                ),
                None,
            )
            if esistente is None:
                esistente = {"lead_id": lead_id, "stima_id": stima_id, "relation_type": relazione}
                self.stato.links.append(esistente)
                self.rowcount = 1
                self.rows = [copy.deepcopy(esistente)]
            else:
                self.rows = []
            return

        raise AssertionError(f"SQL inatteso: {sql}")

    def _lead(self, lead_id):
        return next(l for l in self.stato.leads if l["id"] == lead_id)

    def fetchone(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None

    def fetchall(self):
        return copy.deepcopy(self.rows)

    def close(self):
        pass


class FakeDatabase:
    """Stato committato e copia di lavoro: il commit riversa, il rollback butta."""

    def __init__(self):
        self.stato = Stato()
        self.sql: list = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def cursor(self, *, commit: bool = False):
        lavoro = copy.deepcopy(self.stato)
        try:
            yield None, FakeCursor(lavoro, self.sql)
            if commit:
                self.stato = lavoro
                self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise


@pytest.fixture
def db(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(core_repository, "core_cursor", database.cursor)
    return database


def bridge(stima_id, *, granted, decided_at=DECISO_IL, ctx=CTX, email="mario@example.test", phone=None):
    return core_service.bridge_public_stima(
        stima_id,
        first_name="Mario",
        last_name="Rossi",
        email=email,
        phone=phone,
        marketing_consent=granted,
        marketing_consent_at=decided_at if granted else None,
        system_ctx=ctx,
    )


def stato_consenso(db):
    return state_from_projection(db.stato.contacts[0], PURPOSE_MARKETING)


# ===========================================================================
# A - nuovo contatto + TRUE
# ===========================================================================
def test_a_nuovo_contatto_con_casella_spuntata_produce_un_grant(db):
    esito = bridge(501, granted=True)

    assert esito["status"] == "linked"
    assert len(db.stato.consent_events) == 1
    evento = db.stato.consent_events[0]
    assert evento["decision"] == "granted"
    assert evento["purpose"] == PURPOSE_MARKETING
    assert evento["contact_id"] == db.stato.contacts[0]["id"]
    assert stato_consenso(db)["status"] == STATUS_GRANTED


def test_a_il_contatto_nasce_senza_consenso_e_lo_riceve_dall_evento(db):
    """La INSERT non scrive piu' il consenso: lo scrive la proiezione."""
    bridge(501, granted=True)

    insert = next(s for s, _ in db.sql if "insert into contacts" in s)
    for colonna in COLONNE_CONSENSO:
        assert colonna not in insert, f"la INSERT del bridge nomina ancora {colonna}"

    assert any(s.startswith("update contacts c set") for s, _ in db.sql)


# ===========================================================================
# B - nuovo contatto + FALSE/NULL
# ===========================================================================
@pytest.mark.parametrize("valore", [False, None, 0, ""])
def test_b_nuovo_contatto_senza_casella_non_produce_eventi(db, valore):
    esito = bridge(501, granted=valore)

    assert esito["status"] == "linked"
    assert db.stato.consent_events == []
    assert stato_consenso(db)["status"] == STATUS_NEVER_GIVEN
    assert db.stato.contacts[0]["marketing_consent"] is None
    assert db.stato.contacts[0]["marketing_revoked_at"] is None


def test_b_senza_casella_il_dominio_non_emette_alcun_sql(db):
    bridge(501, granted=False)
    for sql, _ in db.sql:
        assert "consent_events" not in sql
        assert not sql.startswith("update contacts c set")


# ===========================================================================
# C - contatto gia' TRUE + nuova stima FALSE
# ===========================================================================
def test_c_una_casella_vuota_non_revoca_un_consenso_precedente(db):
    bridge(501, granted=True, decided_at=DECISO_IL)
    eventi_prima = len(db.stato.consent_events)
    consenso_at_prima = db.stato.contacts[0]["marketing_consent_at"]

    bridge(502, granted=False)

    assert len(db.stato.consent_events) == eventi_prima, "nessun evento nuovo"
    contatto = db.stato.contacts[0]
    assert contatto["marketing_consent"] is True, "il consenso resta concesso"
    assert contatto["marketing_revoked_at"] is None, "nessuna revoca"
    assert contatto["marketing_consent_at"] == consenso_at_prima, "timestamp intatto"
    assert stato_consenso(db)["status"] == STATUS_GRANTED


def test_c_il_secondo_lead_esiste_comunque(db):
    """Il caso D del design non blocca il resto del bridge."""
    bridge(501, granted=True)
    esito = bridge(502, granted=False)

    assert esito["status"] == "linked"
    assert len(db.stato.leads) == 2
    assert len(db.stato.contacts) == 1, "stesso contatto, dedup per email"


# ===========================================================================
# D - contatto NULL/FALSE + nuova stima TRUE
# ===========================================================================
@pytest.mark.parametrize("iniziale", [None, False])
def test_d_una_casella_spuntata_concede_a_chi_non_aveva_consenso(db, iniziale):
    db.stato.aggiungi_contatto(
        email_normalized="mario@example.test",
        display_name="Mario Rossi",
        marketing_consent=iniziale,
    )

    bridge(501, granted=True)

    assert len(db.stato.contacts) == 1, "contatto riusato, non duplicato"
    assert len(db.stato.consent_events) == 1
    assert db.stato.contacts[0]["marketing_consent"] is True
    assert stato_consenso(db)["status"] == STATUS_GRANTED


# ===========================================================================
# E / F - dopo una revoca
# ===========================================================================
def test_e_un_contatto_revocato_torna_granted_con_una_nuova_stima_spuntata(db):
    db.stato.aggiungi_contatto(
        email_normalized="mario@example.test",
        display_name="Mario Rossi",
        marketing_consent=False,
        marketing_consent_at=DECISO_IL - timedelta(days=10),
        marketing_revoked_at=DECISO_IL - timedelta(days=5),
    )
    assert stato_consenso(db)["status"] == STATUS_REVOKED

    bridge(501, granted=True, decided_at=DECISO_IL)

    assert len(db.stato.consent_events) == 1
    assert db.stato.consent_events[0]["decision"] == "granted"
    contatto = db.stato.contacts[0]
    assert contatto["marketing_consent"] is True
    assert contatto["marketing_revoked_at"] is None, (
        "lo stato corrente non e' piu' revocato; la revoca resta nello storico"
    )
    assert stato_consenso(db)["status"] == STATUS_GRANTED


def test_f_un_contatto_revocato_resta_revocato_con_una_casella_vuota(db):
    db.stato.aggiungi_contatto(
        email_normalized="mario@example.test",
        display_name="Mario Rossi",
        marketing_consent=False,
        marketing_revoked_at=DECISO_IL - timedelta(days=5),
    )

    bridge(501, granted=False)

    assert db.stato.consent_events == []
    assert stato_consenso(db)["status"] == STATUS_REVOKED
    assert db.stato.contacts[0]["marketing_revoked_at"] == DECISO_IL - timedelta(days=5)


# ===========================================================================
# G - idempotenza
# ===========================================================================
def test_g_riprocessare_la_stessa_stima_non_duplica_l_evento(db):
    bridge(501, granted=True)
    assert len(db.stato.consent_events) == 1

    bridge(501, granted=True)

    assert len(db.stato.consent_events) == 1, "la stessa stima non produce due grant"
    assert len(db.stato.leads) == 1


def test_g_la_chiave_di_idempotenza_viene_dalla_stima_non_dall_orologio(db):
    bridge(777, granted=True)
    assert db.stato.consent_events[0]["idempotency_key"] == "public_stima:777:marketing"


def test_g_stime_diverse_hanno_chiavi_diverse(db):
    bridge(501, granted=True, decided_at=DECISO_IL)
    bridge(502, granted=True, decided_at=DECISO_IL + timedelta(hours=1))

    chiavi = {e["idempotency_key"] for e in db.stato.consent_events}
    assert chiavi == {"public_stima:501:marketing", "public_stima:502:marketing"}
    assert len(db.stato.consent_events) == 2


# ===========================================================================
# H - decided_at, source, evidence, notice
# ===========================================================================
def test_h_decided_at_e_quello_della_stima(db):
    bridge(501, granted=True, decided_at=DECISO_IL)
    assert db.stato.consent_events[0]["decided_at"] == DECISO_IL
    assert db.stato.contacts[0]["marketing_consent_at"] == DECISO_IL


def test_h_la_provenienza_e_il_funnel_pubblico(db):
    bridge(501, granted=True)
    evento = db.stato.consent_events[0]
    assert evento["source"] == SOURCE_PUBLIC_STIMA
    assert db.stato.contacts[0]["marketing_consent_source"] == SOURCE_PUBLIC_STIMA


def test_h_la_prova_e_la_stima(db):
    bridge(501, granted=True)
    evento = db.stato.consent_events[0]
    assert evento["evidence_type"] == "stima"
    assert evento["evidence_ref"] == "501"


def test_h_l_attore_e_la_persona(db):
    bridge(501, granted=True)
    assert db.stato.consent_events[0]["actor_type"] == "subject"
    assert db.stato.consent_events[0]["actor_ref"] is None


def test_h_nessuna_notice_inventata(db):
    """Il form pubblico non ha oggi ne' versione ne' testo: restano assenti."""
    bridge(501, granted=True)
    assert db.stato.consent_events[0]["notice_id"] is None
    assert db.stato.contacts[0]["marketing_consent_notice_id"] is None
    assert stato_consenso(db)["notice_id"] is None


# ===========================================================================
# I - tenancy
# ===========================================================================
def test_i_l_evento_appartiene_all_agenzia_della_stima(db):
    bridge(501, granted=True, ctx=CTX)
    assert db.stato.consent_events[0]["agency_id"] == AGENZIA
    assert db.stato.contacts[0]["agency_id"] == AGENZIA


def test_i_due_agenzie_non_si_vedono(db):
    bridge(501, granted=True, ctx=CTX)
    bridge(601, granted=True, ctx=CTX_ALTRA)

    assert len(db.stato.contacts) == 2, "stessa email, due agenzie, due contatti"
    per_agenzia = {e["agency_id"]: e for e in db.stato.consent_events}
    assert set(per_agenzia) == {AGENZIA, ALTRA_AGENZIA}
    for agency_id, evento in per_agenzia.items():
        contatto = next(c for c in db.stato.contacts if c["id"] == evento["contact_id"])
        assert contatto["agency_id"] == agency_id, "evento su un contatto di un'altra agenzia"


def test_i_ogni_statement_del_consenso_porta_il_predicato_di_agenzia(db):
    bridge(501, granted=True)
    consenso = [
        sql for sql, _ in db.sql
        if "consent_events" in sql or sql.startswith("update contacts c set")
    ]
    assert consenso
    for sql in consenso:
        if sql.startswith("insert into"):
            continue
        assert "agency_id = %s" in sql, f"statement senza predicato di agenzia: {sql}"


def test_i_il_contesto_deve_restare_quello_del_flusso_pubblico(db):
    """La guardia P26-2B2B-R1 vale anche ora che il consenso passa di qui."""
    class Finto:
        agency_id = AGENZIA
        role = None
        user_id = None
        is_platform_admin = False

        def require_agency(self):
            return AGENZIA

    with pytest.raises(ProgrammingError):
        core_repository.bridge_public_stima(
            501, {}, {}, "related", system_ctx=Finto(), marketing_granted=True
        )
    assert db.stato.consent_events == []


# ===========================================================================
# L - il bridge non scrive piu' la proiezione
# ===========================================================================
def test_l_il_bridge_rifiuta_un_contact_data_che_porti_il_consenso(db):
    with pytest.raises(ProgrammingError) as errore:
        core_repository.bridge_public_stima(
            501,
            {"display_name": "Mario", "marketing_consent": True},
            {},
            "related",
            system_ctx=CTX,
            marketing_granted=True,
        )
    assert "marketing_consent" in str(errore.value)
    assert "consent.service" in str(errore.value)


def test_l_la_proiezione_la_scrive_solo_il_dominio(db):
    bridge(501, granted=True)
    aggiornamenti = [sql for sql, _ in db.sql if sql.startswith("update contacts")]
    assert len(aggiornamenti) == 1, "una sola scrittura della proiezione"
    assert "marketing_consent = true" in aggiornamenti[0]


# ===========================================================================
# M - linkage, dedup, conflitti: invariati
# ===========================================================================
def test_m_contatto_lead_e_collegamento_alla_stima_funzionano_ancora(db):
    esito = bridge(501, granted=True)

    assert esito["contact_created"] is True and esito["lead_created"] is True
    assert len(db.stato.contacts) == 1
    assert len(db.stato.leads) == 1
    assert db.stato.links == [
        {"lead_id": 1, "stima_id": 501, "relation_type": "related"}
    ]
    assert db.stato.leads[0]["pipeline"] == "sell"
    assert db.stato.leads[0]["source"] == "public_stima"


def test_m_il_dedup_per_email_e_invariato(db):
    bridge(501, granted=True)
    esito = bridge(502, granted=True)

    assert esito["contact_created"] is False
    assert len(db.stato.contacts) == 1
    assert len(db.stato.leads) == 2


def test_m_il_dedup_per_telefono_e_invariato(db):
    bridge(501, granted=True, email=None, phone="+39 333 1234567")
    esito = bridge(502, granted=False, email=None, phone="+39 333 1234567")

    assert esito["contact_created"] is False
    assert len(db.stato.contacts) == 1


def test_m_il_conflitto_di_identita_resta_un_conflitto_e_non_scrive_consenso(db):
    """Email di un contatto, telefono di un altro: nessun consenso da attribuire."""
    db.stato.aggiungi_contatto(email_normalized="mario@example.test", display_name="Mario")
    # La forma normalizzata reale, non una scritta a mano: normalize_phone
    # toglie il '+' e gli spazi (core/normalization.py).
    db.stato.aggiungi_contatto(phone_normalized="393331234567", display_name="Altro")

    esito = bridge(501, granted=True, email="mario@example.test", phone="+39 333 1234567")

    assert esito["status"] == "conflict"
    assert esito["reason"] == "identity_conflict"
    assert db.stato.consent_events == [], (
        "senza un contatto risolto non c'e' nessuno a cui attribuire il consenso"
    )


def test_m_un_contatto_archiviato_resta_saltato_e_non_riceve_consenso(db):
    db.stato.aggiungi_contatto(
        email_normalized="mario@example.test", display_name="Mario", status="archived"
    )

    esito = bridge(501, granted=True)

    assert esito["status"] == "skipped"
    assert esito["reason"] == "archived_contact"
    assert db.stato.consent_events == []


# ===========================================================================
# Atomicita'
# ===========================================================================
def test_atomicita_un_solo_commit_per_stima(db):
    bridge(501, granted=True)
    assert db.commits == 1, "contatto, lead, collegamento e consenso in una transazione"


def test_atomicita_se_il_consenso_fallisce_non_resta_ne_contatto_ne_lead(db, monkeypatch):
    def esplode(*args, **kwargs):
        raise RuntimeError("consenso non scrivibile")

    monkeypatch.setattr(core_repository.consent_service, "record_optional_grant", esplode)

    with pytest.raises(RuntimeError):
        bridge(501, granted=True)

    assert db.stato.contacts == []
    assert db.stato.leads == []
    assert db.stato.links == []
    assert db.stato.consent_events == []
    assert db.commits == 0 and db.rollbacks == 1
