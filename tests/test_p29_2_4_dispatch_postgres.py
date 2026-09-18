"""P29-2.4 - il dispatcher completo contro un PostgreSQL VERO.

Il criterio di chiusura del design e' uno e si prova solo qui:

    revoca fra `enqueue` e dispatch -> `suppressed` con la reason ESATTA,
    e con il suo compare-and-set

Perche' serva un database vero: il consenso e' uno storico append-only in
`consent_events` con una proiezione su `contacts`, la guardia lo legge con un
LEFT JOIN LATERAL, e la soppressione e' una finalizzazione fenced. Un fake
proverebbe solo di essere stato scritto per passare.

Il 422 sul payload gira invece su una app FastAPI AUTONOMA: la rotta non e'
montata in main.py (P29-2.4 la dichiara soltanto), e provarla cosi' non tocca
la superficie pubblica dell'applicazione.

COME SI ESEGUE

    P29_TEST_DSN='postgresql://utente@host:porta/db' python -m pytest \\
        tests/test_p29_2_4_dispatch_postgres.py

Senza `P29_TEST_DSN` l'intero modulo viene SALTATO.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare P29-2.4")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

from communication import dispatcher, service  # noqa: E402
from communication.providers import base as provider_base  # noqa: E402
from communication.providers import null as provider_null  # noqa: E402

# Lo schema P26/P29-1 minimo su cui il dominio si appoggia. Le tre migration del
# consenso servono davvero: la guardia legge `consent_events` e le colonne di
# proiezione su `contacts`.
SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    assigned_agent_id BIGINT, status VARCHAR(20) NOT NULL DEFAULT 'active',
    archived_at TIMESTAMPTZ,
    marketing_consent BOOLEAN, marketing_consent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id));
CREATE TABLE leads      (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime      (id SERIAL    PRIMARY KEY, agency_id BIGINT);
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
-- P29 cutover: la finalizzazione `sent` di una mail service/stima_pdf scrive
-- QUI, nella sua stessa transazione. Senza questa tabella il ramo `sent` non
-- committa e ogni messaggio finisce `lost` - un guasto che sembrerebbe del
-- trasporto e non lo e'. Fedele a 017 + 044: indice parziale di idempotenza e
-- `agency_id` NOT NULL.
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    contact_id BIGINT, lead_id BIGINT, stima_id INTEGER, property_id BIGINT,
    event_type VARCHAR(50) NOT NULL,
    event_source VARCHAR(30),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(255),
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE UNIQUE INDEX idx_seller_timeline_events_idempotency_key
    ON seller_timeline_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""


#: Il `pdf_url` che una mail service/stima_pdf deve portare: il cutover P29
#: lo pretende, perche' l'evento `email_stima_inviata` non puo' nascere con
#: un payload senza il documento di cui parla.
PDF_DI_PROVA = {"pdf_url": "https://example.it/stima.pdf"}
VERSIONI = ("061_p29_consent_notices", "062_p29_consent_events",
            "063_p29_contacts_consent_projection", "064_p29_communication_foundation")


class Operatore:
    """Chi invoca il dispatcher. Deliberatamente un AGENTE: se il dispatcher
    ereditasse il suo restringimento, salterebbe i messaggi dei contatti non
    assegnati a lui - ed e' il buco che D1 esiste per chiudere."""

    def __init__(self, agency_id, user_id=42, role="agent"):
        self.agency_id, self.user_id, self.role = agency_id, user_id, role
        self.is_platform_admin = False

    def require_agency(self):
        return self.agency_id

    @property
    def sees_all_agency_records(self):
        """Letta DALLA MATRICE, non decisa qui.

        P29-2.6E: la rotta chiede questa capacita'. Un doppio di prova che la
        rispondesse a modo suo potrebbe far passare la rotta a un ruolo che in
        produzione prende 403, ed e' esattamente il genere di finta che rende
        inutile un test di confine.
        """
        from operator_auth import permissions

        return permissions.sees_all_agency_records(self.role, self.is_platform_admin)


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db(monkeypatch_module=None):
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_4_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    conn = psycopg2.connect(_dsn_per(nome))
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            for versione in VERSIONI:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
        conn.commit()
        yield conn
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def mondo(db, monkeypatch):
    """Due agenzie con un contatto ciascuna, e i cursori del dominio dirottati
    sulla connessione di prova.

    `communication_cursor` e `consent_cursor` aprirebbero una connessione loro
    tramite `database.get_connection`: qui vengono sostituiti, cosi' il
    dispatcher gira davvero end-to-end su questo database usa-e-getta senza che
    nessun modulo apra una seconda connessione verso un ambiente reale.
    """
    from contextlib import contextmanager

    from psycopg2.extras import RealDictCursor

    from communication import database as communication_database
    from communication import dispatcher as communication_dispatcher
    from communication import repository as communication_repository
    from communication import service as communication_service
    from consent import database as consent_database
    from consent import repository as consent_repository

    with db.cursor() as cur:
        cur.execute("ALTER TABLE communication_attempts DISABLE TRIGGER USER")
        cur.execute("ALTER TABLE communication_messages DISABLE TRIGGER USER")
        cur.execute("ALTER TABLE consent_events DISABLE TRIGGER USER")
        cur.execute("DELETE FROM communication_attempts")
        cur.execute("DELETE FROM communication_messages")
        cur.execute("DELETE FROM consent_events")
        for t, g in (("communication_messages", "trg_communication_messages_guard"),
                     ("communication_messages", "trg_communication_messages_no_truncate"),
                     ("communication_attempts", "trg_communication_attempts_guard"),
                     ("communication_attempts", "trg_communication_attempts_no_truncate"),
                     ("consent_events", "trg_consent_events_append_only"),
                     ("consent_events", "trg_consent_events_no_truncate")):
            cur.execute(f"ALTER TABLE {t} ENABLE ALWAYS TRIGGER {g}")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno'), ('a-due')")
        cur.execute("SELECT id FROM agencies ORDER BY id")
        a1, a2 = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a1,))
        c1 = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a2,))
        c2 = cur.fetchone()[0]
        # P29 cutover: una mail service/stima_pdf ha per definizione una stima,
        # e la finalizzazione `sent` la usa per la chiave dell'evento P17.
        cur.execute("INSERT INTO stime (agency_id) VALUES (%s) RETURNING id", (a1,))
        s1 = cur.fetchone()[0]
    db.commit()

    @contextmanager
    def cursore(*, commit: bool = False):
        cur = db.cursor(cursor_factory=RealDictCursor)
        try:
            yield db, cur
            if commit:
                db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            cur.close()

    # `dispatcher` e' nell'elenco perche' dal cutover P29 e' LUI ad aprire la
    # transazione del ramo `sent`: il compare-and-set e il seguito devono stare
    # nello stesso commit. Lasciarlo fuori manderebbe quel solo commit al DSN di
    # default, e nessun messaggio arriverebbe mai a `sent`.
    for modulo in (communication_database, communication_service,
                   communication_dispatcher):
        monkeypatch.setattr(modulo, "communication_cursor", cursore, raising=False)
    monkeypatch.setattr(communication_repository, "communication_cursor", cursore,
                        raising=False)
    for modulo in (consent_database, consent_repository):
        monkeypatch.setattr(modulo, "consent_cursor", cursore, raising=False)

    return {"conn": db, "a1": a1, "a2": a2, "c1": c1, "c2": c2, "s1": s1,
            "op1": Operatore(a1), "op2": Operatore(a2),
            "cur": lambda: db.cursor(cursor_factory=RealDictCursor)}


def accoda(mondo, *, chiave, tipo="marketing", contact_id=None, agency=None, op=None):
    from communication.enums import MODE_AUTOMATIC, REASON_M2, REASON_STIMA_PDF

    ctx = dispatcher.contesto_di_sistema(op or mondo["op1"])
    with mondo["cur"]() as cur:
        esito = service.enqueue(
            ctx, cur=cur, contact_id=contact_id or mondo["c1"], channel="email",
            communication_type=tipo, mode=MODE_AUTOMATIC,
            reason_code=REASON_M2 if tipo == "marketing" else REASON_STIMA_PDF,
            rendered_body="corpo", destination_snapshot="a@b.it",
            subject_snapshot="oggetto", idempotency_key=chiave,
            stima_id=mondo["s1"], metadata=PDF_DI_PROVA)
    mondo["conn"].commit()
    return esito["message"]


def concedi(mondo, contact_id, *, agency=None):
    from consent import service as consent_service
    from consent.enums import (ACTOR_SUBJECT, PURPOSE_MARKETING, SOURCE_CRM)

    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    with mondo["cur"]() as cur:
        consent_service.record_grant(
            ctx, contact_id=contact_id, purpose=PURPOSE_MARKETING, source=SOURCE_CRM,
            actor_type=ACTOR_SUBJECT, cur=cur)
    mondo["conn"].commit()


def revoca(mondo, contact_id):
    from consent import service as consent_service
    from consent.enums import (ACTOR_SUBJECT, PURPOSE_MARKETING,
                               SOURCE_UNSUBSCRIBE_LINK)

    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    with mondo["cur"]() as cur:
        consent_service.record_revocation(
            ctx, contact_id=contact_id, purpose=PURPOSE_MARKETING,
            source=SOURCE_UNSUBSCRIBE_LINK, actor_type=ACTOR_SUBJECT, cur=cur)
    mondo["conn"].commit()


def riga(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_messages WHERE id = %s", (message_id,))
        return cur.fetchone()


def tentativi(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_attempts WHERE message_id = %s "
                    "ORDER BY attempt_no, late_result, id", (message_id,))
        return cur.fetchall()


# ---------------------------------------------------------------------------
# IL CRITERIO DI CHIUSURA
# ---------------------------------------------------------------------------

def test_chiusura_la_revoca_fra_enqueue_e_dispatch_sopprime(mondo):
    """IL criterio di P29-2.4.

    Il consenso c'e' quando il messaggio viene accodato, e non c'e' piu' quando
    il dispatcher lo prende. Fra i due momenti, nella vita reale, passano
    giorni. Il gate sta immediatamente prima del provider proprio perche' quella
    revoca blocchi il messaggio SUCCESSIVO, senza che nessuno debba ricordarsi
    di svuotare la coda."""
    concedi(mondo, mondo["c1"])
    m = accoda(mondo, chiave="revoca")

    revoca(mondo, mondo["c1"])

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["claimed"] == 1
    assert conteggi["suppressed"] == 1
    assert conteggi["sent"] == 0

    dopo = riga(mondo, m["id"])
    assert dopo["status"] == "suppressed"
    assert dopo["suppressed_reason"] == "deny_revoked", "la reason non e' quella esatta"
    assert dopo["sent_at"] is None
    assert dopo["provider_message_id"] is None, "nessuna chiamata provider e' avvenuta"
    assert dopo["failure_class"] is None
    assert dopo["claim_token"] is None, "la soppressione non ha chiuso il claim"
    assert dopo["claimed_at"] is None


def test_chiusura_la_soppressione_passa_dal_compare_and_set(mondo):
    """"e con il suo compare-and-set": il tentativo e' chiuso e il token
    azzerato, quindi una seconda finalizzazione trova il WHERE falso."""
    concedi(mondo, mondo["c1"])
    m = accoda(mondo, chiave="cas")
    revoca(mondo, mondo["c1"])

    dispatcher.dispatch_batch(mondo["op1"], channel="email")

    righe = tentativi(mondo, m["id"])
    assert len(righe) == 1
    assert righe[0]["outcome"] == "rejected"
    assert righe[0]["failure_class"] == "definite"
    assert righe[0]["finished_at"] is not None

    # Il token e' stato azzerato: nessuno puo' finalizzare di nuovo.
    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    with mondo["cur"]() as cur:
        assert service.finalize_sent(ctx, m["id"], righe[0]["claim_token"], cur=cur) is None
    mondo["conn"].commit()
    assert riga(mondo, m["id"])["status"] == "suppressed"


def test_un_marketing_con_consenso_vivo_viene_inviato(mondo):
    concedi(mondo, mondo["c1"])
    m = accoda(mondo, chiave="ok")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["sent"] == 1 and conteggi["suppressed"] == 0

    dopo = riga(mondo, m["id"])
    assert dopo["status"] == "sent"
    assert dopo["sent_at"] is not None
    assert dopo["provider"] == provider_null.NAME
    assert dopo["provider_message_id"] is None, "il provider finto non inventa un id"
    assert tentativi(mondo, m["id"])[0]["outcome"] == "accepted"


def test_un_marketing_senza_alcun_consenso_e_soppresso(mondo):
    m = accoda(mondo, chiave="mai")
    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["suppressed"] == 1
    assert riga(mondo, m["id"])["suppressed_reason"] == "deny_never_given"


def test_il_servizio_non_passa_dal_gate(mondo):
    """La mail con il PDF della stima esegue una richiesta dell'interessato:
    bloccarla per mancanza di consenso marketing sarebbe assurdo."""
    m = accoda(mondo, chiave="servizio", tipo="service")
    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["sent"] == 1 and conteggi["suppressed"] == 0
    assert riga(mondo, m["id"])["status"] == "sent"


def test_un_contatto_sparito_non_puo_esistere_sotto_un_messaggio(mondo):
    """Fail closed, misurato.

    Il dispatcher ha un ramo che sopprime con `REASON_CONTATTO_ASSENTE` quando il
    contatto non e' interrogabile: e' provato in memoria nei sentinelli. Qui si
    misura PERCHE' quel ramo, su PostgreSQL, non ha modo di scattare - il che e'
    il comportamento voluto, non una lacuna.

    La FK composita di 064 lega il messaggio alla coppia (agency_id, contact_id):
      - spostare il contatto sotto un'altra agenzia e' RIFIUTATO finche' un
        messaggio lo referenzia (nessun ON UPDATE CASCADE);
      - cancellare il contatto porta via il messaggio con se' (ON DELETE CASCADE).
    Non esiste quindi uno stato in cui un messaggio accodato sopravvive a un
    contatto fuori scope. Il ramo resta come difesa, non come percorso atteso.
    """
    import psycopg2.errors

    concedi(mondo, mondo["c1"])
    m = accoda(mondo, chiave="sparito")

    # 1. Il contatto non si puo' spostare fuori dallo scope del messaggio.
    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as exc:
        with mondo["cur"]() as cur:
            cur.execute("UPDATE contacts SET agency_id = %s WHERE id = %s",
                        (mondo["a2"], mondo["c1"]))
    assert "communication_messages_contact_same_agency_fk" in str(exc.value)
    mondo["conn"].rollback()

    # 2. Cancellarlo porta via anche il messaggio: nessun orfano da dispacciare.
    with mondo["cur"]() as cur:
        cur.execute("DELETE FROM contacts WHERE id = %s", (mondo["c1"],))
        cur.execute("SELECT count(*) AS n FROM communication_messages WHERE id = %s",
                    (m["id"],))
        assert cur.fetchone()["n"] == 0, "il messaggio e' sopravvissuto al contatto"
    mondo["conn"].rollback()

    # 3. Dopo il rollback il mondo e' intatto e il messaggio parte normalmente.
    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["sent"] == 1
    assert riga(mondo, m["id"])["status"] == "sent"


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def test_il_dispatcher_non_attraversa_le_agenzie(mondo):
    concedi(mondo, mondo["c1"])
    mio = accoda(mondo, chiave="mio")
    suo = accoda(mondo, chiave="suo", contact_id=mondo["c2"], op=mondo["op2"])

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["claimed"] == 1
    assert riga(mondo, mio["id"])["status"] == "sent"
    assert riga(mondo, suo["id"])["status"] == "queued", "reclamato da un'altra agenzia"


def test_un_agente_non_perde_i_messaggi_dei_contatti_altrui(mondo):
    """D1, provata. L'operatore e' un AGENTE e il contatto non gli e'
    assegnato: con un OperatorContext il claim lo salterebbe in silenzio."""
    concedi(mondo, mondo["c1"])
    m = accoda(mondo, chiave="agente")
    with mondo["cur"]() as cur:
        cur.execute("UPDATE contacts SET assigned_agent_id = 999 WHERE id = %s",
                    (mondo["c1"],))
    mondo["conn"].commit()

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["claimed"] == 1, "il dispatcher ha ereditato il restringimento dell'agente"
    assert riga(mondo, m["id"])["status"] == "sent"


# ---------------------------------------------------------------------------
# Il batch continua
# ---------------------------------------------------------------------------

def test_una_soppressione_non_ferma_il_resto_del_batch(mondo):
    concedi(mondo, mondo["c1"])
    soppresso = accoda(mondo, chiave="b1")
    revoca(mondo, mondo["c1"])
    servizio = accoda(mondo, chiave="b2", tipo="service")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email")
    assert conteggi["claimed"] == 2
    assert conteggi["suppressed"] == 1 and conteggi["sent"] == 1
    assert riga(mondo, soppresso["id"])["status"] == "suppressed"
    assert riga(mondo, servizio["id"])["status"] == "sent"


def test_il_limite_del_batch_e_rispettato(mondo):
    for i in range(5):
        accoda(mondo, chiave=f"lim-{i}", tipo="service")
    assert dispatcher.dispatch_batch(mondo["op1"], channel="email", limit=2)["claimed"] == 2


def test_un_provider_che_non_sa_produce_indeterminate(mondo):
    """Il percorso piu' povero, quello dell'adapter SMTP legacy di P29-2.5."""
    class Ignoto:
        NAME = "ignoto"
        CAPABILITIES = provider_base.ProviderCapabilities(
            returns_message_id=False, distinguishes_failure_class=False,
            reports_delivery=False)

        @staticmethod
        def send(message):
            return provider_base.ProviderResult(outcome="rejected",
                                                error_detail="ha detto no, ma non sa dirlo")

    m = accoda(mondo, chiave="ignoto", tipo="service")
    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=Ignoto)
    assert conteggi["indeterminate"] == 1 and conteggi["failed"] == 0

    dopo = riga(mondo, m["id"])
    assert dopo["status"] == "indeterminate"
    assert dopo["failure_class"] == "indeterminate"
    assert tentativi(mondo, m["id"])[0]["outcome"] == "indeterminate"


def test_un_provider_che_sa_produce_failed(mondo):
    class Sicuro:
        NAME = "sicuro"
        CAPABILITIES = provider_base.ProviderCapabilities(
            returns_message_id=True, distinguishes_failure_class=True,
            reports_delivery=False)

        @staticmethod
        def send(message):
            return provider_base.ProviderResult(
                outcome="rejected", error_code="invalid_destination")

    m = accoda(mondo, chiave="sicuro", tipo="service")
    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=Sicuro)
    assert conteggi["failed"] == 1

    dopo = riga(mondo, m["id"])
    assert dopo["status"] == "failed"
    assert dopo["failure_class"] == "definite"
    assert dopo["error_code"] == "invalid_destination"


# ---------------------------------------------------------------------------
# Il 422, su una app autonoma
# ---------------------------------------------------------------------------

def test_agency_id_nel_payload_e_un_422():
    """La rotta non e' montata in main.py: si prova su una app autonoma, senza
    toccare la superficie pubblica dell'applicazione."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from communication.router import router
    from operator_auth.dependencies import legacy_basic_agency_context

    app = FastAPI()
    app.include_router(router)
    # `agency_admin` e non `agent`: da P29-2.6E la rotta chiede la riga "See
    # all agency records" della matrice, e un agente prende 403 prima ancora
    # che il corpo venga validato - il 422 che questi due test misurano non
    # arriverebbe mai. Il ruolo `agent` resta il default della classe, dove
    # serve davvero: nei test del DISPATCHER, che non passano dalla rotta.
    app.dependency_overrides[legacy_basic_agency_context] = (
        lambda: Operatore(1, role="agency_admin"))

    with TestClient(app, raise_server_exceptions=False) as client:
        risposta = client.post("/api/communication/dispatch",
                               json={"limit": 5, "agency_id": 99})
        assert risposta.status_code == 422, risposta.text
        assert "agency_id" in risposta.text


def test_un_limite_fuori_scala_e_un_422():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from communication.router import router
    from operator_auth.dependencies import legacy_basic_agency_context

    app = FastAPI()
    app.include_router(router)
    # `agency_admin` e non `agent`: da P29-2.6E la rotta chiede la riga "See
    # all agency records" della matrice, e un agente prende 403 prima ancora
    # che il corpo venga validato - il 422 che questi due test misurano non
    # arriverebbe mai. Il ruolo `agent` resta il default della classe, dove
    # serve davvero: nei test del DISPATCHER, che non passano dalla rotta.
    app.dependency_overrides[legacy_basic_agency_context] = (
        lambda: Operatore(1, role="agency_admin"))

    with TestClient(app, raise_server_exceptions=False) as client:
        for cattivo in (0, -1, 51):
            assert client.post("/api/communication/dispatch",
                               json={"limit": cattivo}).status_code == 422


# ---------------------------------------------------------------------------
# C20 - L'identita' del provider, misurata sulla riga del tentativo
# ---------------------------------------------------------------------------

def test_C20_il_provider_del_tentativo_e_quello_delladapter_invocato(mondo):
    """Un valore solo, letto dal database.

    P29-2.3 scrive il `provider` nel regular attempt al momento del claim.
    P29-2.4 deve garantire che quella stringa sia il nome dell'adapter che
    verra' effettivamente invocato: non due valori che possono divergere, ma uno
    derivato dall'altro. Qui si passa un adapter con un nome proprio e si legge
    cosa e' finito nella riga.
    """
    class ProviderConNome:
        NAME = "adapter-con-nome-proprio"
        CAPABILITIES = provider_base.ProviderCapabilities(
            returns_message_id=False, distinguishes_failure_class=False,
            reports_delivery=False)

        def __init__(self):
            self.chiamate = []

        def send(self, message):
            self.chiamate.append(message["id"])
            return provider_base.ProviderResult(
                outcome=provider_base.OUTCOME_ACCEPTED)

    concedi(mondo, mondo["c1"])
    m = accoda(mondo, chiave="identita-provider")
    finto = ProviderConNome()

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=finto)

    assert conteggi["sent"] == 1
    assert finto.chiamate == [m["id"]], "non e' stato invocato quell'oggetto"

    regolari = [t for t in tentativi(mondo, m["id"]) if not t["late_result"]]
    assert len(regolari) == 1
    assert regolari[0]["provider"] == ProviderConNome.NAME, (
        "il claim ha registrato un provider diverso dall'adapter invocato"
    )
    # E il messaggio porta la stessa etichetta: "reclamato per quel provider".
    assert riga(mondo, m["id"])["provider"] == ProviderConNome.NAME


# ---------------------------------------------------------------------------
# C22 - L'isolamento del batch, misurato sul database
# ---------------------------------------------------------------------------

class ProviderCheSolleva:
    """Viola il contratto del ramo A su UN messaggio, e funziona sugli altri."""

    NAME = "adapter-che-solleva"
    CAPABILITIES = provider_base.ProviderCapabilities(
        returns_message_id=False, distinguishes_failure_class=False,
        reports_delivery=False)

    def __init__(self, guasto_su: int):
        self.guasto_su = guasto_su
        self.chiamate: list[int] = []

    def send(self, message):
        self.chiamate.append(message["id"])
        if message["id"] == self.guasto_su:
            raise ConnectionResetError("la connessione e' caduta dopo l'invio")
        return provider_base.ProviderResult(
            outcome=provider_base.OUTCOME_ACCEPTED)


def test_C22_una_eccezione_del_provider_non_ferma_il_batch(mondo):
    """C22 ramo B, misurato dove conta: sulle righe.

    Due messaggi nello stesso giro. Il provider solleva sul primo. Si verifica
    tutto cio' che il contratto promette: il primo non resta `sending`, il suo
    token e' azzerato dal compare-and-set di P29-2.3, il suo tentativo e' chiuso
    coerentemente, il secondo arriva a `sent`, e non nasce nessun tentativo in
    piu' - cioe' nessun retry.
    """
    m1 = accoda(mondo, chiave="c22-primo", tipo="service")
    m2 = accoda(mondo, chiave="c22-secondo", tipo="service")
    finto = ProviderCheSolleva(guasto_su=m1["id"])

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=finto)

    # 8. l'eccezione dopo il claim non interrompe il batch
    assert finto.chiamate == [m1["id"], m2["id"]], (
        "il secondo messaggio non e' stato nemmeno provato"
    )
    assert conteggi == {"claimed": 2, "sent": 1, "suppressed": 0, "failed": 0,
                        "indeterminate": 1, "lost": 0}

    primo = riga(mondo, m1["id"])
    # 1. indeterminate  2. non resta sending  3. token azzerato dal CAS
    assert primo["status"] == "indeterminate"
    assert primo["claim_token"] is None and primo["claimed_at"] is None
    assert primo["failure_class"] == "indeterminate"
    assert primo["error_code"] == "outcome_unknown"
    assert primo["error_detail"] == "builtins.ConnectionResetError"
    assert primo["sent_at"] is None and primo["provider_message_id"] is None

    # 4. il tentativo e' chiuso coerentemente, e ce n'e' UNO SOLO
    tent = tentativi(mondo, m1["id"])
    assert len(tent) == 1, "e' nato un tentativo in piu': e' un retry"
    assert tent[0]["attempt_no"] == 1 and tent[0]["late_result"] is False
    assert tent[0]["outcome"] == "indeterminate"
    assert tent[0]["finished_at"] is not None
    assert tent[0]["provider"] == ProviderCheSolleva.NAME

    # 5. il secondo arriva regolarmente a sent
    secondo = riga(mondo, m2["id"])
    assert secondo["status"] == "sent" and secondo["sent_at"] is not None
    assert len(tentativi(mondo, m2["id"])) == 1

    # 6-7. nessun retry: un secondo giro non ha piu' niente da reclamare
    assert dispatcher.dispatch_batch(mondo["op1"], channel="email")["claimed"] == 0
    assert len(tentativi(mondo, m1["id"])) == 1


def test_C22_unknown_ed_eccezione_finiscono_nello_stesso_stato(mondo):
    """Requisito 9. Due strade, lo stesso stato del messaggio; a distinguerle
    resta il codice d'errore, che e' l'informazione utile."""
    class ProviderCheNonSa:
        NAME = "adapter-che-non-sa"
        CAPABILITIES = provider_base.ProviderCapabilities(
            returns_message_id=False, distinguishes_failure_class=False,
            reports_delivery=False)

        def send(self, message):
            return provider_base.ProviderResult(
                outcome=provider_base.OUTCOME_UNKNOWN, error_code="timeout")

    da_unknown = accoda(mondo, chiave="c22-unknown", tipo="service")
    dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=ProviderCheNonSa())

    da_eccezione = accoda(mondo, chiave="c22-eccezione", tipo="service")
    dispatcher.dispatch_batch(mondo["op1"], channel="email",
                              provider=ProviderCheSolleva(guasto_su=da_eccezione["id"]))

    a, b = riga(mondo, da_unknown["id"]), riga(mondo, da_eccezione["id"])
    assert a["status"] == b["status"] == "indeterminate"
    assert a["failure_class"] == b["failure_class"] == "indeterminate"
    assert a["claim_token"] is None and b["claim_token"] is None
    assert a["error_code"] == "timeout" and b["error_code"] == "outcome_unknown"

    for message_id in (da_unknown["id"], da_eccezione["id"]):
        tent = tentativi(mondo, message_id)
        assert len(tent) == 1 and tent[0]["outcome"] == "indeterminate"


def test_C22_il_fencing_governa_ancora_il_token_perso(mondo):
    """Requisito 10. Il ramo dell'eccezione non e' una scorciatoia: passa dal
    compare-and-set come ogni altra finalizzazione, e se il token non e' piu'
    valido la finalizzazione MANCA il messaggio - non lo riscrive."""
    m = accoda(mondo, chiave="c22-fencing", tipo="service")
    ctx = dispatcher.contesto_di_sistema(mondo["op1"])

    reclamati = service.claim_due(ctx, provider="adapter-che-solleva", channel="email", limit=1)
    token_vero = reclamati[0]["message"]["claim_token"]

    # Un UUID valido ma estraneo: la colonna e' tipizzata, e un token malformato
    # verrebbe respinto dal tipo invece che dal fencing - che non e' la prova.
    token_estraneo = str(uuid.uuid4())
    assert token_estraneo != token_vero

    esito = service.finalize_indeterminate(
        ctx, m["id"], token_estraneo,
        error_code="outcome_unknown", error_detail="builtins.ConnectionResetError")
    assert esito is None, "il CAS ha accettato un token estraneo"
    assert riga(mondo, m["id"])["status"] == "sending", "il messaggio e' stato riscritto"

    # Con il token vero la stessa finalizzazione passa.
    assert service.finalize_indeterminate(
        ctx, m["id"], token_vero, error_code="outcome_unknown",
        error_detail="builtins.ConnectionResetError") is not None
    assert riga(mondo, m["id"])["status"] == "indeterminate"
