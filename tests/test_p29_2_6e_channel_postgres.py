"""P29-2.6E - il canale filtra il claim, misurato su PostgreSQL reale.

IL TEST CHE QUESTA FASE DEVE AVERE

Una coda con un messaggio email e uno WhatsApp; un giro di dispatch su
`channel='email'`. L'email parte; il WhatsApp non viene TOCCATO - e "non
toccato" si misura su quattro cose insieme, non su una: `status` ancora
`queued`, `attempt_count` invariato, nessuna riga in `communication_attempts`,
nessun `claim_token`. Se anche una sola cedesse, il messaggio sarebbe passato
per `sending` e avrebbe lasciato dietro di se' il racconto di un invio mai
tentato.

Opt-in come ogni modulo PostgreSQL di P29: senza `P29_TEST_DSN` si salta tutto.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare P29-2.6E")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

from communication import dispatcher, service  # noqa: E402
from communication.exceptions import ValidationError  # noqa: E402
from communication.providers import email_smtp  # noqa: E402

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
    def __init__(self, agency_id, user_id=42, role="agent"):
        self.agency_id, self.user_id, self.role = agency_id, user_id, role
        self.is_platform_admin = False

    def require_agency(self):
        return self.agency_id


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_6e_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    """Un'agenzia, un contatto, e i cursori del dominio dirottati qui: nessun
    modulo apre una seconda connessione verso un ambiente reale."""
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
        cur.execute("DELETE FROM communication_attempts")
        cur.execute("DELETE FROM communication_messages")
        for t, g in (("communication_messages", "trg_communication_messages_guard"),
                     ("communication_messages", "trg_communication_messages_no_truncate"),
                     ("communication_attempts", "trg_communication_attempts_guard"),
                     ("communication_attempts", "trg_communication_attempts_no_truncate")):
            cur.execute(f"ALTER TABLE {t} ENABLE ALWAYS TRIGGER {g}")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a1 = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a1,))
        c1 = cur.fetchone()[0]
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

    return {"conn": db, "a1": a1, "c1": c1, "s1": s1, "op1": Operatore(a1),
            "cur": lambda: db.cursor(cursor_factory=RealDictCursor)}


def accoda(mondo, *, chiave, canale="email"):
    """Un messaggio SERVICE sul canale richiesto.

    `subject_snapshot` esiste se e solo se il canale e' email: e' il
    bicondizionale della 064, e passare un oggetto a un WhatsApp farebbe
    fallire il CHECK prima ancora di arrivare al claim.
    """
    from communication.enums import MODE_AUTOMATIC, REASON_STIMA_PDF

    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    with mondo["cur"]() as cur:
        esito = service.enqueue(
            ctx, cur=cur, contact_id=mondo["c1"], channel=canale,
            communication_type="service", mode=MODE_AUTOMATIC,
            reason_code=REASON_STIMA_PDF, rendered_body="corpo",
            destination_snapshot="a@b.it" if canale == "email" else "393331112222",
            subject_snapshot="oggetto" if canale == "email" else None,
            idempotency_key=chiave, stima_id=mondo["s1"],
            metadata=PDF_DI_PROVA)
    mondo["conn"].commit()
    return esito["message"]


def riga(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_messages WHERE id = %s", (message_id,))
        return cur.fetchone()


def tentativi(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_attempts WHERE message_id = %s "
                    "ORDER BY attempt_no, late_result, id", (message_id,))
        return cur.fetchall()


@pytest.fixture
def spia(monkeypatch):
    chiamate: list[tuple] = []
    comportamento = {"ritorna": True, "solleva": None}

    def finta_invia_mail(destinatario, oggetto, corpo_html, allegato=None):
        chiamate.append((destinatario, oggetto, corpo_html, allegato))
        if comportamento["solleva"] is not None:
            raise comportamento["solleva"]
        return comportamento["ritorna"]

    monkeypatch.setattr(email_smtp, "invia_mail", finta_invia_mail)
    return chiamate, comportamento



# ---------------------------------------------------------------------------
# Il test obbligatorio della fase
# ---------------------------------------------------------------------------

def test_un_worker_email_non_reclama_mai_un_whatsapp(mondo, spia):
    """IL test di P29-2.6E, sulle righe."""
    email = accoda(mondo, chiave="c-email", canale="email")
    whatsapp = accoda(mondo, chiave="c-whatsapp", canale="whatsapp")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email",
                                         provider=email_smtp)

    assert conteggi["claimed"] == 1 and conteggi["sent"] == 1
    assert riga(mondo, email["id"])["status"] == "sent"

    intatto = riga(mondo, whatsapp["id"])
    assert intatto["status"] == "queued", "reclamato da un worker di un altro canale"
    assert intatto["attempt_count"] == 0, "ha consumato un tentativo"
    assert intatto["claim_token"] is None and intatto["claimed_at"] is None
    assert tentativi(mondo, whatsapp["id"]) == [], (
        "esiste un tentativo che racconta un invio mai tentato"
    )


def test_il_whatsapp_resta_reclamabile_dal_suo_worker(mondo, spia):
    """La prova che il filtro seleziona invece di escludere: lo stesso messaggio
    che il worker email ha ignorato viene preso da un giro sul suo canale."""
    whatsapp = accoda(mondo, chiave="w-suo", canale="whatsapp")

    dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)
    assert riga(mondo, whatsapp["id"])["status"] == "queued"

    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    reclamati = service.claim_due(ctx, provider="finto", channel="whatsapp", limit=10)
    assert [r["message"]["id"] for r in reclamati] == [whatsapp["id"]]
    assert riga(mondo, whatsapp["id"])["status"] == "sending"


def test_due_worker_di_canali_diversi_lavorano_su_insiemi_disgiunti(mondo, spia):
    """`FOR UPDATE SKIP LOCKED` resta valido dentro il sottoinsieme: il filtro
    restringe l'insieme bloccato, non cambia la meccanica."""
    email = accoda(mondo, chiave="d-email", canale="email")
    whatsapp = accoda(mondo, chiave="d-whatsapp", canale="whatsapp")
    ctx = dispatcher.contesto_di_sistema(mondo["op1"])

    presi_email = service.claim_due(ctx, provider="e", channel="email", limit=10)
    presi_whatsapp = service.claim_due(ctx, provider="w", channel="whatsapp", limit=10)

    id_email = {r["message"]["id"] for r in presi_email}
    id_whatsapp = {r["message"]["id"] for r in presi_whatsapp}
    assert id_email == {email["id"]} and id_whatsapp == {whatsapp["id"]}
    assert id_email.isdisjoint(id_whatsapp)


def test_il_canale_e_validato_prima_di_toccare_il_database(mondo):
    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    accoda(mondo, chiave="v-email", canale="email")
    with pytest.raises(ValidationError):
        service.claim_due(ctx, provider="p", channel="sms", limit=10)
    # Nessun effetto collaterale: la coda e' intatta.
    assert riga(mondo, accoda(mondo, chiave="v2", canale="email")["id"])["status"] == "queued"


def test_il_limite_resta_per_canale(mondo, spia):
    """Il `LIMIT` conta dentro il canale, non attraverso i canali."""
    for i in range(3):
        accoda(mondo, chiave=f"l-email-{i}", canale="email")
    for i in range(3):
        accoda(mondo, chiave=f"l-whatsapp-{i}", canale="whatsapp")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", limit=2,
                                         provider=email_smtp)
    assert conteggi["claimed"] == 2, "il limite ha contato righe di un altro canale"
