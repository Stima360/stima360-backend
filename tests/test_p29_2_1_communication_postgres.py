"""P29-2.1 - la migration 064 applicata a un PostgreSQL VERO.

Questo file, a differenza di test_p29_2_1_communication_foundation.py, NON legge
testo: applica 064 a un database reale e prova che i vincoli si comportano come
il commento dice.

Esiste per le stesse ragioni di test_p29_1_consent_postgres.py, e per due in
piu' che sono specifiche di questa fase:

  * la catena di CASCADE e' a DUE livelli - contacts -> communication_messages
    -> communication_attempts - e passa attraverso DUE guardiani BEFORE DELETE
    che rifiutano la cancellazione. Che il purge attraversi entrambi non si
    deduce dallo schema: si misura.
  * lo UNIQUE a tre colonne (message_id, attempt_no, late_result) e' cio' che
    rende registrabile un risultato tardivo senza riscrivere il tentativo
    originale. Che ammetta esattamente due righe - una regolare e una tardiva -
    e non tre, si prova solo provandolo.

COME SI ESEGUE

    P29_TEST_DSN='postgresql://utente@host:porta/db' python -m pytest \\
        tests/test_p29_2_1_communication_postgres.py

Senza `P29_TEST_DSN` l'intero modulo viene SALTATO, con la ragione scritta: la
suite resta eseguibile dove un PostgreSQL non c'e' - che e' il caso normale
sulle macchine di sviluppo di questo progetto - senza fingere di aver provato
qualcosa. Nessuna connessione automatica a nessun ambiente.

Il database indicato NON viene toccato: serve solo come connessione di servizio
per creare un DATABASE usa-e-getta (`p29_2_probe_<...>`), dove la migration
viene applicata nel suo `public` e che viene distrutto alla fine.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui applicare 064",
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
VERSIONE = "064_p29_communication_foundation"

# Il minimo di schema P26 su cui 064 si appoggia: le tabelle che nomina, con le
# sole colonne e i soli vincoli che tocca. In particolare
# `contacts_agency_scope_unq`, senza il quale la FK composita non sta in piedi -
# ed e' il punto 2 del gate R4.
SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id     BIGSERIAL PRIMARY KEY,
    slug   VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE contacts (
    id        BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    status    VARCHAR(20) NOT NULL DEFAULT 'active',
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id)
);

CREATE TABLE leads      (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime      (id SERIAL    PRIMARY KEY, agency_id BIGINT);
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
"""

TOKEN = "11111111-1111-4111-8111-111111111111"
ALTRO_TOKEN = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(scope="module")
def conn():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"

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
    connection.autocommit = True
    try:
        with connection.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            # Le migration dell'era 027+ non si brackettano: la transazione e'
            # del runner. Qui il runner siamo noi.
            cur.execute("BEGIN")
            cur.execute((MIGRATIONS / f"{VERSIONE}.sql").read_text(encoding="utf-8"))
            cur.execute("COMMIT")
        yield connection
    finally:
        connection.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def scenario(conn):
    """Due agenzie, un contatto nella prima. Ripulito a ogni test."""
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
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        agenzia = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-due') RETURNING id")
        altra = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (agenzia,))
        contatto = cur.fetchone()[0]
    return {"agenzia": agenzia, "altra": altra, "contatto": contatto}


def messaggio(conn, scenario, *, chiave="k1", agency=None, contact=None, channel="email"):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO communication_messages (
                agency_id, contact_id, channel, direction, communication_type,
                mode, reason_code, subject_snapshot, rendered_body,
                destination_snapshot, actor_type, idempotency_key
            ) VALUES (%s, %s, %s, 'outbound', 'service', 'automatic', 'stima_pdf',
                      %s, 'corpo reso', 'destinatario@example.invalid', 'system', %s)
            RETURNING id
            """,
            (agency or scenario["agenzia"], contact or scenario["contatto"], channel,
             "oggetto" if channel == "email" else None, chiave),
        )
        return cur.fetchone()[0]


def reclama(conn, message_id, *, token=TOKEN, attempt_no=1):
    """Il claim del design 8.1: messaggio e tentativo nello stesso commit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE communication_messages
               SET status = 'sending', claimed_at = NOW(), claim_token = %s,
                   attempt_count = %s, last_attempt_at = NOW()
             WHERE id = %s
            """,
            (token, attempt_no, message_id),
        )
        cur.execute(
            """
            INSERT INTO communication_attempts (
                agency_id, message_id, attempt_no, claim_token, provider,
                started_at, outcome
            )
            SELECT agency_id, id, %s, %s, 'probe', NOW(), 'in_progress'
              FROM communication_messages WHERE id = %s
            RETURNING id
            """,
            (attempt_no, token, message_id),
        )
        return cur.fetchone()[0]


def errore(conn, sql, parametri=()):
    """Esegue e restituisce l'eccezione, oppure None se e' passata."""
    import psycopg2
    try:
        with conn.cursor() as cur:
            cur.execute(sql, parametri)
    except psycopg2.Error as exc:
        return exc
    return None


# ---------------------------------------------------------------------------
# La migration si applica
# ---------------------------------------------------------------------------

def test_le_due_tabelle_esistono(conn, scenario):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name LIKE 'communication%'
             ORDER BY table_name
        """)
        assert [r[0] for r in cur.fetchall()] == [
            "communication_attempts", "communication_messages"
        ]


def test_un_messaggio_legittimo_passa(conn, scenario):
    assert messaggio(conn, scenario) > 0


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def test_un_messaggio_per_il_contatto_di_unaltra_agenzia_e_rifiutato(conn, scenario):
    exc = errore(conn, """
        INSERT INTO communication_messages (
            agency_id, contact_id, channel, direction, communication_type, mode,
            reason_code, subject_snapshot, rendered_body, destination_snapshot,
            actor_type, idempotency_key
        ) VALUES (%s, %s, 'email', 'outbound', 'service', 'automatic', 'stima_pdf',
                  'o', 'c', 'd@e.it', 'system', 'cross')
    """, (scenario["altra"], scenario["contatto"]))
    assert exc is not None, "un messaggio cross-tenant e' stato accettato"
    assert "communication_messages_contact_same_agency_fk" in str(exc)


def test_un_tentativo_per_il_messaggio_di_unaltra_agenzia_e_rifiutato(conn, scenario):
    m = messaggio(conn, scenario)
    exc = errore(conn, """
        INSERT INTO communication_attempts
            (agency_id, message_id, attempt_no, claim_token, provider, started_at, outcome)
        VALUES (%s, %s, 1, %s, 'probe', NOW(), 'in_progress')
    """, (scenario["altra"], m, TOKEN))
    assert exc is not None, "un tentativo cross-tenant e' stato accettato"
    assert "communication_attempts_message_same_agency_fk" in str(exc)


# ---------------------------------------------------------------------------
# Idempotenza
# ---------------------------------------------------------------------------

def test_la_stessa_intenzione_due_volte_nella_stessa_agenzia_e_rifiutata(conn, scenario):
    messaggio(conn, scenario, chiave="uguale")
    exc = errore(conn, """
        INSERT INTO communication_messages (
            agency_id, contact_id, channel, direction, communication_type, mode,
            reason_code, subject_snapshot, rendered_body, destination_snapshot,
            actor_type, idempotency_key
        ) VALUES (%s, %s, 'email', 'outbound', 'service', 'automatic', 'stima_pdf',
                  'o', 'c', 'd@e.it', 'system', 'uguale')
    """, (scenario["agenzia"], scenario["contatto"]))
    assert exc is not None
    assert "uq_communication_messages_idempotency" in str(exc)


def test_la_stessa_chiave_in_due_agenzie_diverse_e_ammessa(conn, scenario):
    """C3: con uno UNIQUE globale l'agenzia B non potrebbe accodare un messaggio
    perche' A ha gia' usato quella stringa - un fallimento incomprensibile, e una
    fuga di informazione."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id",
                    (scenario["altra"],))
        altro_contatto = cur.fetchone()[0]
    messaggio(conn, scenario, chiave="condivisa")
    secondo = messaggio(conn, scenario, chiave="condivisa",
                        agency=scenario["altra"], contact=altro_contatto)
    assert secondo > 0


# ---------------------------------------------------------------------------
# I CHECK mordono
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("colonna,valore,vincolo", [
    ("channel", "sms", "channel_chk"),
    ("direction", "sideways", "direction_chk"),
    ("communication_type", "transactional", "type_chk"),
    ("mode", "auto", "mode_chk"),
    ("reason_code", "admin_lead_alert", "reason_code_chk"),
])
def test_gli_insiemi_chiusi_rifiutano_un_valore_fuori(conn, scenario, colonna, valore, vincolo):
    campi = {
        "channel": "email", "direction": "outbound",
        "communication_type": "service", "mode": "automatic",
        "reason_code": "stima_pdf",
    }
    campi[colonna] = valore
    exc = errore(conn, f"""
        INSERT INTO communication_messages (
            agency_id, contact_id, channel, direction, communication_type, mode,
            reason_code, subject_snapshot, rendered_body, destination_snapshot,
            actor_type, idempotency_key
        ) VALUES (%s, %s, '{campi["channel"]}', '{campi["direction"]}',
                  '{campi["communication_type"]}', '{campi["mode"]}',
                  '{campi["reason_code"]}', 'o', 'c', 'd@e.it', 'system', 'chk-{colonna}')
    """, (scenario["agenzia"], scenario["contatto"]))
    assert exc is not None, f"{colonna}={valore} e' stato accettato"
    assert vincolo in str(exc)


@pytest.mark.parametrize("stato", ["scheduled", "delivered", "draft", "pending"])
def test_gli_stati_rimandati_non_sono_ammessi(conn, scenario, stato):
    m = messaggio(conn, scenario, chiave=f"stato-{stato}")
    exc = errore(conn, "UPDATE communication_messages SET status = %s WHERE id = %s",
                 (stato, m))
    assert exc is not None, f"lo stato {stato} e' stato accettato"
    assert "status_chk" in str(exc)


def test_un_messaggio_marketing_senza_operatore_dichiarato(conn, scenario):
    """Un atto di un operatore porta il nome dell'operatore."""
    exc = errore(conn, """
        INSERT INTO communication_messages (
            agency_id, contact_id, channel, direction, communication_type, mode,
            reason_code, subject_snapshot, rendered_body, destination_snapshot,
            actor_type, idempotency_key
        ) VALUES (%s, %s, 'email', 'outbound', 'marketing', 'manual', 'operator_manual',
                  'o', 'c', 'd@e.it', 'operator', 'senza-operatore')
    """, (scenario["agenzia"], scenario["contatto"]))
    assert exc is not None
    assert "actor_user_chk" in str(exc)


def test_un_whatsapp_con_un_oggetto_e_rifiutato(conn, scenario):
    """L'oggetto esiste per le email e non esiste per WhatsApp."""
    exc = errore(conn, """
        INSERT INTO communication_messages (
            agency_id, contact_id, channel, direction, communication_type, mode,
            reason_code, subject_snapshot, rendered_body, destination_snapshot,
            actor_type, idempotency_key
        ) VALUES (%s, %s, 'whatsapp', 'outbound', 'service', 'automatic', 'stima_pdf',
                  'un oggetto', 'c', '393331234567', 'system', 'wa-oggetto')
    """, (scenario["agenzia"], scenario["contatto"]))
    assert exc is not None
    assert "subject_chk" in str(exc)


def test_un_insuccesso_deve_dire_quale(conn, scenario):
    m = messaggio(conn, scenario, chiave="senza-classe")
    reclama(conn, m)
    exc = errore(conn, """
        UPDATE communication_messages
           SET status = 'failed', failed_at = NOW(), claim_token = NULL, claimed_at = NULL
         WHERE id = %s
    """, (m,))
    assert exc is not None, "un fallimento senza failure_class e' stato accettato"
    assert "failure_class_coherence_chk" in str(exc)


# ---------------------------------------------------------------------------
# Claim e fencing (C13): lo schema li regge
# ---------------------------------------------------------------------------

def test_un_messaggio_non_reclamato_non_puo_portare_un_token(conn, scenario):
    m = messaggio(conn, scenario, chiave="token-orfano")
    exc = errore(conn, """
        UPDATE communication_messages SET claim_token = %s, claimed_at = NOW() WHERE id = %s
    """, (TOKEN, m))
    assert exc is not None
    assert "claim_status_chk" in str(exc)


def test_un_messaggio_reclamato_deve_portare_un_token(conn, scenario):
    m = messaggio(conn, scenario, chiave="sending-senza-token")
    exc = errore(conn, "UPDATE communication_messages SET status = 'sending' WHERE id = %s", (m,))
    assert exc is not None
    assert "claim_status_chk" in str(exc)


def test_il_token_e_listante_stanno_insieme(conn, scenario):
    m = messaggio(conn, scenario, chiave="coppia")
    exc = errore(conn, """
        UPDATE communication_messages SET status = 'sending', claim_token = %s WHERE id = %s
    """, (TOKEN, m))
    assert exc is not None
    assert "claim_pair_chk" in str(exc)


def test_la_finalizzazione_azzera_il_token(conn, scenario):
    """9.3: e' cio' che rende falso per costruzione il WHERE di una seconda
    finalizzazione."""
    m = messaggio(conn, scenario, chiave="finalizza")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE communication_messages
               SET status = 'sent', sent_at = NOW(), claim_token = NULL, claimed_at = NULL
             WHERE id = %s AND status = 'sending' AND claim_token = %s
        """, (m, TOKEN))
        assert cur.rowcount == 1
        # la stessa finalizzazione, ripetuta: il token non c'e' piu'
        cur.execute("""
            UPDATE communication_messages
               SET status = 'sent', sent_at = NOW()
             WHERE id = %s AND status = 'sending' AND claim_token = %s
        """, (m, TOKEN))
        assert cur.rowcount == 0, "una seconda finalizzazione ha trovato la riga"


def test_un_worker_con_il_token_sbagliato_non_finalizza(conn, scenario):
    """Il compare-and-set di C13, provato a livello di schema: e' il rowcount a
    dire che l'ownership non c'e' piu'."""
    m = messaggio(conn, scenario, chiave="fencing")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE communication_messages SET status = 'sent', sent_at = NOW(),
                   claim_token = NULL, claimed_at = NULL
             WHERE id = %s AND status = 'sending' AND claim_token = %s
        """, (m, ALTRO_TOKEN))
        assert cur.rowcount == 0
        cur.execute("SELECT status, claim_token FROM communication_messages WHERE id = %s", (m,))
        stato, token = cur.fetchone()
        assert stato == "sending" and str(token) == TOKEN


# ---------------------------------------------------------------------------
# Il tentativo chiuso e il risultato tardivo (C13/C14)
# ---------------------------------------------------------------------------

def test_un_tentativo_chiuso_non_si_riapre(conn, scenario):
    m = messaggio(conn, scenario, chiave="chiuso")
    tentativo = reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE communication_attempts
               SET outcome = 'indeterminate', failure_class = 'indeterminate',
                   error_code = 'outcome_unknown', finished_at = NOW(), recovered_at = NOW()
             WHERE id = %s
        """, (tentativo,))
    exc = errore(conn, """
        UPDATE communication_attempts SET outcome = 'accepted', failure_class = NULL WHERE id = %s
    """, (tentativo,))
    assert exc is not None, "un tentativo chiuso e' stato riaperto"
    assert "cannot be reopened" in str(exc)


def test_il_risultato_tardivo_entra_come_riga_nuova(conn, scenario):
    """9.4: due righe raccontano la verita' - abbiamo dichiarato ignoto, e poi e'
    arrivata questa risposta - mentre una riga riscritta racconterebbe che lo
    sapevamo da sempre."""
    m = messaggio(conn, scenario, chiave="tardivo")
    tentativo = reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE communication_attempts
               SET outcome = 'indeterminate', failure_class = 'indeterminate',
                   finished_at = NOW(), recovered_at = NOW()
             WHERE id = %s
        """, (tentativo,))
        cur.execute("""
            INSERT INTO communication_attempts (
                agency_id, message_id, attempt_no, claim_token, provider,
                started_at, finished_at, outcome, provider_message_id, late_result
            ) VALUES (%s, %s, 1, %s, 'probe', NOW(), NOW(), 'accepted', 'wamid.X', TRUE)
        """, (scenario["agenzia"], m, TOKEN))
        cur.execute("""
            SELECT attempt_no, outcome, late_result FROM communication_attempts
             WHERE message_id = %s ORDER BY late_result
        """, (m,))
        righe = cur.fetchall()
    assert righe == [(1, "indeterminate", False), (1, "accepted", True)]


def test_due_risultati_tardivi_per_lo_stesso_tentativo_sono_rifiutati(conn, scenario):
    m = messaggio(conn, scenario, chiave="due-tardivi")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO communication_attempts (
                agency_id, message_id, attempt_no, claim_token, provider,
                started_at, finished_at, outcome, late_result
            ) VALUES (%s, %s, 1, %s, 'probe', NOW(), NOW(), 'accepted', TRUE)
        """, (scenario["agenzia"], m, TOKEN))
    exc = errore(conn, """
        INSERT INTO communication_attempts (
            agency_id, message_id, attempt_no, claim_token, provider,
            started_at, finished_at, outcome, late_result
        ) VALUES (%s, %s, 1, %s, 'probe', NOW(), NOW(), 'accepted', TRUE)
    """, (scenario["agenzia"], m, TOKEN))
    assert exc is not None
    # `accepted` e non `rejected`: un `rejected` senza failure_class sarebbe
    # rifiutato dal CHECK prima ancora di arrivare allo UNIQUE, e il test
    # direbbe di aver provato una cosa mentre ne provava un'altra.
    assert "communication_attempts_no_unq" in str(exc)


def test_due_claim_con_lo_stesso_numero_sono_rifiutati(conn, scenario):
    """Lo UNIQUE a tre colonne continua a impedire cio' che impediva a due."""
    m = messaggio(conn, scenario, chiave="due-claim")
    reclama(conn, m)
    exc = errore(conn, """
        INSERT INTO communication_attempts
            (agency_id, message_id, attempt_no, claim_token, provider, started_at, outcome)
        VALUES (%s, %s, 1, %s, 'probe', NOW(), 'in_progress')
    """, (scenario["agenzia"], m, ALTRO_TOKEN))
    assert exc is not None
    assert "communication_attempts_no_unq" in str(exc)


def test_un_tentativo_aperto_non_ha_una_fine(conn, scenario):
    m = messaggio(conn, scenario, chiave="aperto")
    exc = errore(conn, """
        INSERT INTO communication_attempts
            (agency_id, message_id, attempt_no, claim_token, provider,
             started_at, finished_at, outcome)
        VALUES (%s, %s, 1, %s, 'probe', NOW(), NOW(), 'in_progress')
    """, (scenario["agenzia"], m, TOKEN))
    assert exc is not None
    assert "finished_chk" in str(exc)


# ---------------------------------------------------------------------------
# Immutabilita' e guardiani
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("colonna,valore", [
    ("rendered_body", "'un altro corpo'"),
    ("destination_snapshot", "'altro@example.invalid'"),
    ("subject_snapshot", "'un altro oggetto'"),
    ("idempotency_key", "'un-altra-chiave'"),
    ("reason_code", "'m1'"),
    ("communication_type", "'marketing'"),
])
def test_gli_snapshot_e_lidentita_non_si_riscrivono(conn, scenario, colonna, valore):
    """C12: il template puo' cambiare domani, la riga storica no."""
    m = messaggio(conn, scenario, chiave=f"imm-{colonna}")
    exc = errore(conn, f"UPDATE communication_messages SET {colonna} = {valore} WHERE id = %s", (m,))
    assert exc is not None, f"{colonna} e' stata riscritta"
    assert "immutable" in str(exc)


def test_lo_stato_invece_si_scrive(conn, scenario):
    """Non append-only: un guardiano che vietasse ogni UPDATE renderebbe
    impossibile il dispatch."""
    m = messaggio(conn, scenario, chiave="mutabile")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempt_count FROM communication_messages WHERE id = %s", (m,))
        assert cur.fetchone() == ("sending", 1)


def test_il_delete_diretto_e_rifiutato_su_entrambe(conn, scenario):
    m = messaggio(conn, scenario, chiave="no-delete")
    reclama(conn, m)
    exc = errore(conn, "DELETE FROM communication_attempts WHERE message_id = %s", (m,))
    assert exc is not None and "DELETE is refused" in str(exc)
    exc = errore(conn, "DELETE FROM communication_messages WHERE id = %s", (m,))
    assert exc is not None and "DELETE is refused" in str(exc)


def test_session_replication_role_non_apre_i_guardiani(conn, scenario):
    """ENABLE ALWAYS. Un trigger ordinario sarebbe stato spento da questo
    parametro, e la riga sarebbe sparita senza che nulla si lamentasse."""
    m = messaggio(conn, scenario, chiave="replica")
    try:
        with conn.cursor() as cur:
            cur.execute("SET session_replication_role = 'replica'")
        exc = errore(conn, "DELETE FROM communication_messages WHERE id = %s", (m,))
        assert exc is not None, "la riga e' stata cancellata con il trigger disattivato"
        assert "DELETE is refused" in str(exc)
    finally:
        with conn.cursor() as cur:
            cur.execute("SET session_replication_role = 'origin'")


@pytest.mark.parametrize("tabella", ["communication_messages", "communication_attempts"])
def test_il_truncate_e_rifiutato(conn, scenario, tabella):
    exc = errore(conn, f"TRUNCATE {tabella} CASCADE")
    assert exc is not None
    assert "TRUNCATE is refused" in str(exc)


# ---------------------------------------------------------------------------
# Purge e cascade
# ---------------------------------------------------------------------------

def test_il_purge_del_contatto_porta_via_messaggi_e_tentativi(conn, scenario):
    """C6, e la prova che solo un PostgreSQL vero puo' dare: il CASCADE
    attraversa DUE guardiani BEFORE DELETE che rifiutano la cancellazione."""
    m = messaggio(conn, scenario, chiave="purge")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM contacts WHERE id = %s", (scenario["contatto"],))
        cur.execute("SELECT count(*) FROM communication_messages WHERE id = %s", (m,))
        assert cur.fetchone()[0] == 0, "il messaggio e' sopravvissuto al purge del contatto"
        cur.execute("SELECT count(*) FROM communication_attempts WHERE message_id = %s", (m,))
        assert cur.fetchone()[0] == 0, "i tentativi sono sopravvissuti al purge"


def test_lagenzia_con_contatti_vivi_non_si_cancella(conn, scenario):
    """RESTRICT su contacts.agency_id, certificato dal gate R4. E' il motivo per
    cui l'ordine del cleanup P26-6 - contatti prima, agenzie poi - non e' un
    dettaglio."""
    messaggio(conn, scenario, chiave="restrict")
    exc = errore(conn, "DELETE FROM agencies WHERE id = %s", (scenario["agenzia"],))
    assert exc is not None
    assert "violates foreign key constraint" in str(exc)


def test_lordine_del_cleanup_p26_6_non_lascia_orfani(conn, scenario):
    """contacts prima, agencies poi: e' l'ordine reale di DEDICATED_TABLES, ed e'
    il motivo per cui queste due tabelle non vanno aggiunte a quell'elenco -
    esattamente come consent_events, che infatti non ci compare."""
    m = messaggio(conn, scenario, chiave="ordine")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM contacts WHERE agency_id = %s", (scenario["agenzia"],))
        cur.execute("DELETE FROM agencies WHERE id = %s", (scenario["agenzia"],))
        cur.execute("SELECT count(*) FROM communication_messages WHERE agency_id = %s",
                    (scenario["agenzia"],))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM communication_attempts WHERE agency_id = %s",
                    (scenario["agenzia"],))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("colonna,tabella", [
    ("lead_id", "leads"), ("stima_id", "stime"), ("property_id", "properties"),
])
def test_cancellare_il_contesto_commerciale_svuota_il_riferimento(conn, scenario, colonna, tabella):
    """SET NULL: il contesto se ne va, la comunicazione con la persona resta."""
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO {tabella} (agency_id) VALUES (%s) RETURNING id",
                    (scenario["agenzia"],))
        contesto = cur.fetchone()[0]
    m = messaggio(conn, scenario, chiave=f"ctx-{colonna}")
    with conn.cursor() as cur:
        cur.execute(f"UPDATE communication_messages SET {colonna} = %s WHERE id = %s",
                    (contesto, m))
        cur.execute(f"DELETE FROM {tabella} WHERE id = %s", (contesto,))
        cur.execute(f"SELECT {colonna} FROM communication_messages WHERE id = %s", (m,))
        riga = cur.fetchone()
    assert riga is not None, "il messaggio e' stato cancellato con il suo contesto"
    assert riga[0] is None


# ---------------------------------------------------------------------------
# Il catalogo, riletto
# ---------------------------------------------------------------------------

def test_i_quattro_trigger_sono_enable_always(conn, scenario):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT t.tgname, t.tgenabled
              FROM pg_trigger t
             WHERE t.tgrelid IN ('public.communication_messages'::regclass,
                                 'public.communication_attempts'::regclass)
               AND NOT t.tgisinternal
             ORDER BY t.tgname
        """)
        righe = cur.fetchall()
    assert len(righe) == 4, righe
    for nome, abilitato in righe:
        assert abilitato == "A", f"{nome} ha tgenabled={abilitato}, atteso 'A' (ENABLE ALWAYS)"


def test_le_due_fk_composite_portano_due_colonne(conn, scenario):
    """Una composita degradata a FK semplice continuerebbe a garantire che il
    genitore esiste e smetterebbe di garantire che e' della stessa agenzia."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT conname, array_length(conkey, 1), confdeltype
              FROM pg_constraint
             WHERE conname IN ('communication_messages_contact_same_agency_fk',
                               'communication_attempts_message_same_agency_fk')
             ORDER BY conname
        """)
        righe = cur.fetchall()
    assert len(righe) == 2, righe
    for nome, colonne, deltype in righe:
        assert colonne == 2, f"{nome} porta {colonne} colonne, attese 2"
        assert deltype == "c", f"{nome} non e' ON DELETE CASCADE (confdeltype={deltype})"


def test_communication_attempts_non_ha_metadata(conn, scenario):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM information_schema.columns
             WHERE table_name = 'communication_attempts' AND column_name = 'metadata'
        """)
        assert cur.fetchone()[0] == 0


def test_r10_le_cinque_prove_del_purge_in_sequenza(conn, scenario):
    """R10: le cinque prove richieste, su UNA riga che le porta tutte.

    I test qui sopra provano ciascun pezzo isolatamente. Questo li mette in fila
    su un messaggio che ha davvero un lead, una stima e un immobile, perche' e'
    la combinazione che il cleanup incontra dal vivo - e perche' un SET NULL che
    funziona da solo puo' fallire quando ne scattano tre insieme sulla stessa
    riga, sotto lo stesso trigger BEFORE UPDATE.
    """
    with conn.cursor() as cur:
        cur.execute("INSERT INTO leads (agency_id) VALUES (%s) RETURNING id", (scenario["agenzia"],))
        lead = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id) VALUES (%s) RETURNING id", (scenario["agenzia"],))
        stima = cur.fetchone()[0]
        cur.execute("INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (scenario["agenzia"],))
        immobile = cur.fetchone()[0]

    m = messaggio(conn, scenario, chiave="r10")
    reclama(conn, m)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE communication_messages SET lead_id = %s, stima_id = %s, property_id = %s
             WHERE id = %s
        """, (lead, stima, immobile, m))

    def contesto():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT lead_id, stima_id, property_id FROM communication_messages WHERE id = %s",
                (m,))
            return cur.fetchone()

    assert contesto() == (lead, stima, immobile)

    # 1. DELETE lead -> lead_id diventa NULL
    with conn.cursor() as cur:
        cur.execute("DELETE FROM leads WHERE id = %s", (lead,))
    assert contesto() == (None, stima, immobile)

    # 2. DELETE stima -> stima_id diventa NULL
    with conn.cursor() as cur:
        cur.execute("DELETE FROM stime WHERE id = %s", (stima,))
    assert contesto() == (None, None, immobile)

    # 3. DELETE property -> property_id diventa NULL
    with conn.cursor() as cur:
        cur.execute("DELETE FROM properties WHERE id = %s", (immobile,))
    assert contesto() == (None, None, None)

    # Il messaggio e' ancora li', con i suoi snapshot intatti: e' il punto.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rendered_body, destination_snapshot FROM communication_messages WHERE id = %s",
            (m,))
        assert cur.fetchone() == ("corpo reso", "destinatario@example.invalid")

    # 4. DELETE contact -> messaggi e tentativi spariscono per cascade
    with conn.cursor() as cur:
        cur.execute("DELETE FROM contacts WHERE id = %s", (scenario["contatto"],))
        cur.execute("SELECT count(*) FROM communication_messages WHERE id = %s", (m,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM communication_attempts WHERE message_id = %s", (m,))
        assert cur.fetchone()[0] == 0

    # 5. purge dell'agenzia nell'ordine P26 -> nessun orfano, nessun FK failure
    with conn.cursor() as cur:
        cur.execute("DELETE FROM contacts WHERE agency_id = %s", (scenario["agenzia"],))
        cur.execute("DELETE FROM agencies WHERE id = %s", (scenario["agenzia"],))
        cur.execute("SELECT count(*) FROM communication_messages WHERE agency_id = %s",
                    (scenario["agenzia"],))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM communication_attempts WHERE agency_id = %s",
                    (scenario["agenzia"],))
        assert cur.fetchone()[0] == 0
