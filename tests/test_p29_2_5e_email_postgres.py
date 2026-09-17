"""P29-2.5E - l'adapter email reale su PostgreSQL vero. SOLO P29-2.5E.

PERCHE' UN MODULO PROPRIO

P29-2.4 e' CLOSED. Aggiungere test di una fase nuova dentro il suo modulo
avrebbe due effetti sgradevoli: i suoi conteggi certificati non tornerebbero
piu', e un fallimento qui verrebbe letto come una regressione li'. Ogni fase
porta i suoi.

NESSUNA EMAIL PARTE DA QUI

`database.invia_mail` e' sempre sostituita da una spia. Quello che si misura non
e' SMTP - che questa fase non tocca - ma che l'adapter si innesti nel dispatcher
senza che questo cambi forma, e che il suo esito finisca correttamente nel
ledger. Il contratto della funzione reale e' verificato per lettura in
`tests/test_p29_2_5e_email_adapter.py`.

OPT-IN, COME OGNI MODULO POSTGRESQL DI P29

Senza `P29_TEST_DSN` l'intero modulo viene saltato: dal portatile non si
raggiunge nessun database reale per sbaglio.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare P29-2.5E")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

from communication import dispatcher, service  # noqa: E402
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
"""

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

    nome = f"p29_2_5e_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    for modulo in (communication_database, communication_service):
        monkeypatch.setattr(modulo, "communication_cursor", cursore, raising=False)
    monkeypatch.setattr(communication_repository, "communication_cursor", cursore,
                        raising=False)
    for modulo in (consent_database, consent_repository):
        monkeypatch.setattr(modulo, "consent_cursor", cursore, raising=False)

    return {"conn": db, "a1": a1, "c1": c1, "op1": Operatore(a1),
            "cur": lambda: db.cursor(cursor_factory=RealDictCursor)}


def accoda(mondo, *, chiave, destinatario="cliente@example.it",
           oggetto="La tua stima", corpo="<p>ecco la stima</p>"):
    """Un messaggio SERVICE su canale email: il gate del consenso non si applica
    (§13), quindi cio' che si misura qui e' solo il trasporto."""
    from communication.enums import MODE_AUTOMATIC, REASON_STIMA_PDF

    ctx = dispatcher.contesto_di_sistema(mondo["op1"])
    with mondo["cur"]() as cur:
        esito = service.enqueue(
            ctx, cur=cur, contact_id=mondo["c1"], channel="email",
            communication_type="service", mode=MODE_AUTOMATIC,
            reason_code=REASON_STIMA_PDF, rendered_body=corpo,
            destination_snapshot=destinatario, subject_snapshot=oggetto,
            idempotency_key=chiave)
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
# C23-B - gli argomenti REALI, letti da una riga vera del ledger
# ---------------------------------------------------------------------------

def test_C23B_gli_argomenti_vengono_dalla_riga_e_non_da_altro(mondo, spia):
    """La prova che il mapping e' giusto non e' "invia_mail e' stata chiamata":
    e' che i quattro argomenti siano ESATTAMENTE i tre campi della riga, piu'
    l'allegato assente. Qui la riga viene dal database, non da un dizionario
    scritto a mano nel test: se `claim_due` smettesse di restituire una di
    quelle colonne, questo test se ne accorgerebbe."""
    chiamate, _ = spia
    m = accoda(mondo, chiave="c23b", destinatario="mario.rossi@example.it",
               oggetto="La tua stima e' pronta", corpo="<h1>ciao</h1>")

    dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)

    assert len(chiamate) == 1
    destinatario, oggetto, corpo, allegato = chiamate[0]
    assert destinatario == "mario.rossi@example.it"
    assert oggetto == "La tua stima e' pronta"
    assert corpo == "<h1>ciao</h1>"
    assert allegato is None, "il ledger non ha allegati: non se ne inventano"

    # Nessun identificatore e' finito dove va un indirizzo.
    for identificatore in (m["id"], mondo["a1"], mondo["c1"]):
        assert str(identificatore) != destinatario
    assert "@" in destinatario


def test_C23B_il_ledger_porta_davvero_i_tre_campi(mondo):
    """Il presupposto del mapping, verificato sulla riga reclamata invece che
    assunto: se mancasse uno dei tre, l'adapter non sarebbe costruibile e la
    fase andrebbe fermata."""
    accoda(mondo, chiave="c23b-campi")
    ctx = dispatcher.contesto_di_sistema(mondo["op1"])

    reclamati = service.claim_due(ctx, provider=email_smtp.NAME, channel="email", limit=1)
    message = reclamati[0]["message"]

    for campo in ("destination_snapshot", "subject_snapshot", "rendered_body"):
        assert campo in message, campo
        assert message[campo], f"{campo} e' vuoto"


# ---------------------------------------------------------------------------
# L'adapter dentro il dispatcher
# ---------------------------------------------------------------------------

def test_ladapter_email_percorre_il_dispatcher(mondo, spia):
    _, comportamento = spia
    comportamento["ritorna"] = True
    m = accoda(mondo, chiave="p295e-ok")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)

    assert conteggi["sent"] == 1
    riga_m = riga(mondo, m["id"])
    assert riga_m["status"] == "sent" and riga_m["sent_at"] is not None
    assert riga_m["provider"] == "email_smtp"
    assert riga_m["provider_message_id"] is None, "id inventato"

    tent = tentativi(mondo, m["id"])
    assert len(tent) == 1
    assert tent[0]["provider"] == "email_smtp" and tent[0]["outcome"] == "accepted"


def test_un_insuccesso_email_non_diventa_mai_failed(mondo, spia):
    """Il criterio di chiusura della fase, misurato sul ledger: con
    `distinguishes_failure_class=False` un `False` di `invia_mail` finisce in
    `indeterminate`, e `indeterminate` non rientra in coda da solo."""
    _, comportamento = spia
    comportamento["ritorna"] = False
    m = accoda(mondo, chiave="p295e-ko")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)

    assert conteggi == {"claimed": 1, "sent": 0, "suppressed": 0, "failed": 0,
                        "indeterminate": 1, "lost": 0}

    riga_m = riga(mondo, m["id"])
    assert riga_m["status"] == "indeterminate"
    assert riga_m["failure_class"] == "indeterminate"
    assert riga_m["error_code"] == "unknown"
    assert riga_m["error_detail"] == email_smtp.DETTAGLIO_RIFIUTO
    assert riga_m["claim_token"] is None, "il compare-and-set non ha chiuso il claim"
    assert riga_m["sent_at"] is None and riga_m["provider_message_id"] is None

    assert dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)["claimed"] == 0
    assert len(tentativi(mondo, m["id"])) == 1


def test_una_eccezione_della_primitiva_finisce_in_indeterminate(mondo, spia):
    """Ramo A di C22 su una riga vera: l'eccezione e' normalizzata dall'adapter,
    quindi nel ledger arriva `unknown` - non `outcome_unknown`, che e' il codice
    della rete di sicurezza del dispatcher. Le due strade restano distinguibili
    a posteriori, ed e' il punto."""
    _, comportamento = spia
    comportamento["solleva"] = ConnectionResetError("connessione caduta")
    m = accoda(mondo, chiave="p295e-boom")

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)

    assert conteggi["indeterminate"] == 1 and conteggi["sent"] == 0
    riga_m = riga(mondo, m["id"])
    assert riga_m["status"] == "indeterminate"
    assert riga_m["error_code"] == "unknown"
    assert riga_m["error_detail"] == "builtins.ConnectionResetError"
    assert "example.it" not in (riga_m["error_detail"] or "")


def test_un_batch_email_misto_non_si_ferma_al_primo_insuccesso(mondo, spia, monkeypatch):
    """Isolamento, con l'adapter reale al posto del finto: il secondo messaggio
    parte anche se il primo non e' andato."""
    chiamate, _ = spia
    primo = accoda(mondo, chiave="p295e-b1", destinatario="uno@example.it")
    secondo = accoda(mondo, chiave="p295e-b2", destinatario="due@example.it")

    def a_fasi(destinatario, oggetto, corpo_html, allegato=None):
        chiamate.append((destinatario, oggetto, corpo_html, allegato))
        return destinatario != "uno@example.it"

    monkeypatch.setattr(email_smtp, "invia_mail", a_fasi)

    conteggi = dispatcher.dispatch_batch(mondo["op1"], channel="email", provider=email_smtp)

    assert len(chiamate) == 2, "il secondo messaggio non e' stato provato"
    assert conteggi["sent"] == 1 and conteggi["indeterminate"] == 1
    assert riga(mondo, primo["id"])["status"] == "indeterminate"
    assert riga(mondo, secondo["id"])["status"] == "sent"
