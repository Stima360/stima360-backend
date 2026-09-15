"""P29-2.2 - repository e service contro un PostgreSQL VERO.

Qui stanno i tre criteri di chiusura che il design chiede per questa fase, e
nessuno dei tre si puo' provare con un falso in memoria:

  1. `enqueue` e' componibile nella transazione del chiamante - cioe' un
     rollback del chiamante porta via anche il messaggio;
  2. doppio `enqueue` con la stessa chiave -> UN messaggio solo;
  3. stessa chiave in DUE agenzie -> due messaggi, nessun conflitto.

Il secondo e il terzo dipendono dalla semantica di ON CONFLICT su un indice
unico composto, il primo dal confine di transazione: un fake che li simulasse
proverebbe solo di essere stato scritto per passare.

COME SI ESEGUE

    P29_TEST_DSN='postgresql://utente@host:porta/db' python -m pytest \\
        tests/test_p29_2_2_communication_postgres.py

Senza `P29_TEST_DSN` l'intero modulo viene SALTATO. Nessuna connessione
automatica a nessun ambiente, come per P29-1.1 e P29-2.1.

Il database indicato NON viene toccato: serve solo come connessione di servizio
per creare un DATABASE usa-e-getta, dove la 064 viene applicata e che viene
distrutto alla fine.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare P29-2.2",
)

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONE = ROOT / "migrations" / "064_p29_communication_foundation.sql"

from communication import repository, service  # noqa: E402
from communication.enums import (  # noqa: E402
    CHANNEL_EMAIL,
    CHANNEL_WHATSAPP,
    MODE_AUTOMATIC,
    MODE_MANUAL,
    REASON_OPERATOR_MANUAL,
    REASON_STIMA_PDF,
    STATUS_CANCELLED,
    STATUS_QUEUED,
    STATUS_SENDING,
    TYPE_MARKETING,
    TYPE_SERVICE,
)
from communication.exceptions import ConflictError, NotFoundError  # noqa: E402

# Lo schema P26 minimo su cui la 064 si appoggia. Piccolo di proposito: un
# cambiamento a `contacts` altrove non deve far fallire questo file per un motivo
# che non e' suo.
SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id     BIGSERIAL PRIMARY KEY,
    slug   VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE contacts (
    id                BIGSERIAL PRIMARY KEY,
    agency_id         BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    assigned_agent_id BIGINT,
    status            VARCHAR(20) NOT NULL DEFAULT 'active',
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id)
);

CREATE TABLE leads      (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime      (id SERIAL    PRIMARY KEY, agency_id BIGINT);
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
"""


class Ctx:
    """Un operatore vero di un'agenzia. `sees_all_agency_records` a True: il ramo
    dell'agente lo prova il test dedicato, con il proprio contesto."""

    def __init__(self, agency_id, user_id=42, role="agency_admin"):
        self.agency_id = agency_id
        self.user_id = user_id
        self.role = role
        self.is_platform_admin = False

    def require_agency(self):
        return self.agency_id


@pytest.fixture(scope="module")
def conn():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_2_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"

    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    if "?" in DSN:
        base, query = DSN.split("?", 1)
        prova_dsn = base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    else:
        prova_dsn = DSN.rsplit("/", 1)[0] + "/" + nome

    connection = psycopg2.connect(prova_dsn)
    try:
        with connection.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            cur.execute(MIGRAZIONE.read_text(encoding="utf-8"))
        connection.commit()
        yield connection
    finally:
        connection.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def mondo(conn):
    """Due agenzie, un contatto per ciascuna. Ripulito a ogni test.

    La connessione NON e' in autocommit: questo modulo prova proprio il confine
    di transazione, e un autocommit lo renderebbe invisibile.
    """
    from psycopg2.extras import RealDictCursor

    with conn.cursor() as cur:
        cur.execute("ALTER TABLE communication_attempts DISABLE TRIGGER USER")
        cur.execute("ALTER TABLE communication_messages DISABLE TRIGGER USER")
        cur.execute("DELETE FROM communication_attempts")
        cur.execute("DELETE FROM communication_messages")
        cur.execute("ALTER TABLE communication_messages ENABLE ALWAYS TRIGGER trg_communication_messages_guard")
        cur.execute("ALTER TABLE communication_messages ENABLE ALWAYS TRIGGER trg_communication_messages_no_truncate")
        cur.execute("ALTER TABLE communication_attempts ENABLE ALWAYS TRIGGER trg_communication_attempts_guard")
        cur.execute("ALTER TABLE communication_attempts ENABLE ALWAYS TRIGGER trg_communication_attempts_no_truncate")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno'), ('a-due') RETURNING id")
        cur.execute("SELECT id FROM agencies ORDER BY id")
        a1, a2 = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a1,))
        c1 = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a2,))
        c2 = cur.fetchone()[0]
    conn.commit()
    return {
        "a1": a1, "a2": a2, "c1": c1, "c2": c2,
        "ctx1": Ctx(a1), "ctx2": Ctx(a2),
        "cur": lambda: conn.cursor(cursor_factory=RealDictCursor),
    }


def accoda(conn, mondo, ctx=None, **override):
    """`enqueue` sul cursore della connessione di prova, senza commit."""
    parametri = {
        "contact_id": mondo["c1"], "channel": CHANNEL_EMAIL,
        "communication_type": TYPE_SERVICE, "mode": MODE_AUTOMATIC,
        "reason_code": REASON_STIMA_PDF, "rendered_body": "corpo reso",
        "destination_snapshot": "mario@example.invalid",
        "idempotency_key": "k1", "subject_snapshot": "oggetto",
    }
    parametri.update(override)
    with mondo["cur"]() as cur:
        return service.enqueue(ctx or mondo["ctx1"], cur=cur, **parametri)


# ---------------------------------------------------------------------------
# Criterio di chiusura 1: componibile nella transazione del chiamante
# ---------------------------------------------------------------------------

def test_chiusura1_enqueue_vive_nella_transazione_del_chiamante(conn, mondo):
    """Un'intenzione di comunicazione nata da un atto poi annullato non deve
    sopravvivere a quell'atto: se la stima fallisce dopo il commit del messaggio,
    resta in coda una mail che parla di qualcosa che non e' successo."""
    esito = accoda(conn, mondo, idempotency_key="rollback")
    assert esito["created"] is True

    with mondo["cur"]() as cur:
        cur.execute("SELECT count(*) AS n FROM communication_messages")
        assert cur.fetchone()["n"] == 1, "non visibile nella propria transazione"

    conn.rollback()

    with mondo["cur"]() as cur:
        cur.execute("SELECT count(*) AS n FROM communication_messages")
        assert cur.fetchone()["n"] == 0, "il messaggio e' sopravvissuto al rollback del chiamante"
    conn.commit()


def test_chiusura1b_enqueue_non_committa_per_conto_suo(conn, mondo):
    """Se `enqueue` committasse, il rollback qui sopra non avrebbe potuto
    funzionare. Lo si afferma anche direttamente."""
    accoda(conn, mondo, idempotency_key="nocommit")
    assert conn.status != 0, "la connessione non e' piu' in transazione: qualcuno ha committato"
    conn.rollback()


# ---------------------------------------------------------------------------
# Criterio di chiusura 2: doppio enqueue, stessa chiave -> un messaggio solo
# ---------------------------------------------------------------------------

def test_chiusura2_doppio_enqueue_con_la_stessa_chiave_da_un_messaggio_solo(conn, mondo):
    primo = accoda(conn, mondo, idempotency_key="uguale")
    secondo = accoda(conn, mondo, idempotency_key="uguale")

    assert primo["created"] is True
    assert secondo["created"] is False, "il secondo enqueue ha creato un messaggio"
    assert secondo["message"]["id"] == primo["message"]["id"]

    with mondo["cur"]() as cur:
        cur.execute("SELECT count(*) AS n FROM communication_messages WHERE idempotency_key = 'uguale'")
        assert cur.fetchone()["n"] == 1
    conn.commit()


def test_chiusura2b_e_ripetibile_anche_con_contenuto_diverso(conn, mondo):
    """La chiave identifica un'INTENZIONE. Un secondo enqueue con lo stesso
    proposito e un corpo diverso non deve produrre un secondo invio: restituisce
    il messaggio che c'e' gia', com'e'."""
    primo = accoda(conn, mondo, idempotency_key="intenzione", rendered_body="primo corpo")
    secondo = accoda(conn, mondo, idempotency_key="intenzione", rendered_body="secondo corpo")

    assert secondo["created"] is False
    assert secondo["message"]["rendered_body"] == "primo corpo"
    conn.commit()


# ---------------------------------------------------------------------------
# Criterio di chiusura 3: stessa chiave in due agenzie -> due messaggi
# ---------------------------------------------------------------------------

def test_chiusura3_la_stessa_chiave_in_due_agenzie_non_e_un_conflitto(conn, mondo):
    """C3. Con uno UNIQUE globale l'agenzia B non potrebbe accodare un messaggio
    perche' A ha gia' usato quella stringa: un fallimento incomprensibile, e una
    fuga di informazione."""
    primo = accoda(conn, mondo, idempotency_key="condivisa")
    secondo = accoda(conn, mondo, ctx=mondo["ctx2"], contact_id=mondo["c2"],
                     idempotency_key="condivisa")

    assert primo["created"] is True and secondo["created"] is True
    assert primo["message"]["id"] != secondo["message"]["id"]
    assert primo["message"]["agency_id"] == mondo["a1"]
    assert secondo["message"]["agency_id"] == mondo["a2"]
    conn.commit()


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def test_un_contatto_di_unaltra_agenzia_non_e_accodabile(conn, mondo):
    with pytest.raises(NotFoundError):
        accoda(conn, mondo, contact_id=mondo["c2"], idempotency_key="cross")
    conn.rollback()


def test_un_messaggio_di_unaltra_agenzia_non_e_leggibile(conn, mondo):
    esito = accoda(conn, mondo, idempotency_key="mio")
    with mondo["cur"]() as cur:
        with pytest.raises(NotFoundError):
            service.get_message(mondo["ctx2"], esito["message"]["id"], cur=cur)
    conn.rollback()


def test_un_messaggio_di_unaltra_agenzia_non_e_annullabile(conn, mondo):
    esito = accoda(conn, mondo, idempotency_key="mio2")
    with mondo["cur"]() as cur:
        with pytest.raises(NotFoundError):
            service.cancel(mondo["ctx2"], esito["message"]["id"], cur=cur)
    with mondo["cur"]() as cur:
        assert service.get_message(mondo["ctx1"], esito["message"]["id"],
                                   cur=cur)["status"] == STATUS_QUEUED
    conn.rollback()


def test_un_agente_non_accoda_a_un_contatto_che_non_e_suo(conn, mondo):
    """Il restringimento viene da `core.scope`, non da un secondo predicato
    scritto qui."""
    agente = Ctx(mondo["a1"], user_id=99, role="agent")
    with pytest.raises(NotFoundError):
        accoda(conn, mondo, ctx=agente, idempotency_key="agente")
    conn.rollback()


def test_un_agente_accoda_al_proprio_contatto(conn, mondo):
    agente = Ctx(mondo["a1"], user_id=99, role="agent")
    with mondo["cur"]() as cur:
        cur.execute("UPDATE contacts SET assigned_agent_id = 99 WHERE id = %s", (mondo["c1"],))
    esito = accoda(conn, mondo, ctx=agente, idempotency_key="agente-ok")
    assert esito["created"] is True
    conn.rollback()


# ---------------------------------------------------------------------------
# Lo stato iniziale, e cio' che enqueue NON scrive
# ---------------------------------------------------------------------------

def test_ogni_messaggio_nasce_queued_e_non_reclamato(conn, mondo):
    riga = accoda(conn, mondo, idempotency_key="iniziale")["message"]
    assert riga["status"] == STATUS_QUEUED
    assert riga["attempt_count"] == 0
    for vuoto in ("claim_token", "claimed_at", "sent_at", "failed_at",
                  "failure_class", "provider", "provider_message_id",
                  "error_code", "suppressed_reason"):
        assert riga[vuoto] is None, f"{vuoto} non deve essere valorizzato dall'accodamento"
    conn.rollback()


def test_lattore_finisce_davvero_sulla_riga(conn, mondo):
    automatico = accoda(conn, mondo, idempotency_key="sys")["message"]
    assert (automatico["actor_type"], automatico["actor_user_id"]) == ("system", None)

    manuale = accoda(conn, mondo, idempotency_key="op", mode=MODE_MANUAL,
                     communication_type=TYPE_MARKETING,
                     reason_code=REASON_OPERATOR_MANUAL)["message"]
    assert (manuale["actor_type"], manuale["actor_user_id"]) == ("operator", 42)
    conn.rollback()


def test_un_whatsapp_senza_oggetto_passa_i_check_del_database(conn, mondo):
    riga = accoda(conn, mondo, idempotency_key="wa", channel=CHANNEL_WHATSAPP,
                  subject_snapshot=None, destination_snapshot="393331234567")["message"]
    assert riga["subject_snapshot"] is None
    conn.rollback()


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

def test_cancel_porta_da_queued_a_cancelled(conn, mondo):
    esito = accoda(conn, mondo, idempotency_key="ann")
    with mondo["cur"]() as cur:
        annullato = service.cancel(mondo["ctx1"], esito["message"]["id"], cur=cur)
    assert annullato["status"] == STATUS_CANCELLED
    conn.rollback()


def test_cancel_di_un_messaggio_gia_reclamato_e_un_conflitto(conn, mondo):
    """E' il caso per cui `cancel` e' un compare-and-set: fra la lettura e la
    scrittura un dispatcher puo' aver reclamato il messaggio, e annullarglielo
    mentre parla con un provider sarebbe peggio che rifiutare."""
    esito = accoda(conn, mondo, idempotency_key="reclamato")
    with mondo["cur"]() as cur:
        cur.execute(
            "UPDATE communication_messages SET status = %s, claim_token = %s, "
            "claimed_at = NOW() WHERE id = %s",
            (STATUS_SENDING, "33333333-3333-4333-8333-333333333333", esito["message"]["id"]),
        )
    with mondo["cur"]() as cur:
        with pytest.raises(ConflictError, match="sending"):
            service.cancel(mondo["ctx1"], esito["message"]["id"], cur=cur)
    conn.rollback()


def test_cancel_due_volte_e_un_conflitto_la_seconda(conn, mondo):
    esito = accoda(conn, mondo, idempotency_key="due-volte")
    with mondo["cur"]() as cur:
        service.cancel(mondo["ctx1"], esito["message"]["id"], cur=cur)
    with mondo["cur"]() as cur:
        with pytest.raises(ConflictError, match="cancelled"):
            service.cancel(mondo["ctx1"], esito["message"]["id"], cur=cur)
    conn.rollback()


def test_cancel_di_un_messaggio_inesistente(conn, mondo):
    with mondo["cur"]() as cur:
        with pytest.raises(NotFoundError):
            service.cancel(mondo["ctx1"], 999_999, cur=cur)
    conn.rollback()


# ---------------------------------------------------------------------------
# Letture per contatto
# ---------------------------------------------------------------------------

def test_la_lista_per_contatto_e_dal_piu_recente(conn, mondo):
    ids = [accoda(conn, mondo, idempotency_key=f"lista-{i}")["message"]["id"]
           for i in range(3)]
    with mondo["cur"]() as cur:
        righe = service.list_for_contact(mondo["ctx1"], mondo["c1"], cur=cur)
    assert [r["id"] for r in righe] == list(reversed(ids))
    conn.rollback()


def test_la_lista_non_attraversa_le_agenzie(conn, mondo):
    accoda(conn, mondo, idempotency_key="a1")
    accoda(conn, mondo, ctx=mondo["ctx2"], contact_id=mondo["c2"], idempotency_key="a2")
    with mondo["cur"]() as cur:
        righe = service.list_for_contact(mondo["ctx1"], mondo["c1"], cur=cur)
    assert len(righe) == 1
    assert righe[0]["agency_id"] == mondo["a1"]
    conn.rollback()


def test_la_lista_di_un_contatto_altrui_e_un_not_found(conn, mondo):
    with mondo["cur"]() as cur:
        with pytest.raises(NotFoundError):
            service.list_for_contact(mondo["ctx1"], mondo["c2"], cur=cur)
    conn.rollback()


def test_il_limite_e_rispettato(conn, mondo):
    for i in range(5):
        accoda(conn, mondo, idempotency_key=f"lim-{i}")
    with mondo["cur"]() as cur:
        assert len(service.list_for_contact(mondo["ctx1"], mondo["c1"], limit=2, cur=cur)) == 2
    conn.rollback()
