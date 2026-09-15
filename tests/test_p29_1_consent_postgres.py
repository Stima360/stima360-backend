"""P29-1.1 - le tre migration applicate a un PostgreSQL VERO.

Questo file, a differenza di test_p29_1_consent_migrations.py, NON legge testo:
applica 061, 062 e 063 a un database reale e prova che i vincoli si comportano
come il commento dice.

Esiste per una ragione precisa. La decisione C1 - la cancellazione fisica come
purge eccezionale - dipende da un dettaglio di PostgreSQL che non si puo'
dedurre leggendo lo schema: un trigger BEFORE DELETE di riga scatta ANCHE sulla
cancellazione generata da una FK ON DELETE CASCADE. Se scatta e solleva, il
purge fallisce, e il CASCADE non serve a niente. Le due prove che contano -
DELETE diretta rifiutata, DELETE via purge consentita - si fanno solo qui.

COME SI ESEGUE

    P29_TEST_DSN='postgresql://utente@host:porta/db' python -m pytest \\
        tests/test_p29_1_consent_postgres.py

Senza `P29_TEST_DSN` l'intero modulo viene SALTATO, con la ragione scritta: la
suite resta eseguibile dove un PostgreSQL non c'e' - che e' il caso normale
sulle macchine di sviluppo di questo progetto - senza fingere di aver provato
qualcosa.

Il database indicato NON viene toccato: serve solo come connessione di
servizio per creare un DATABASE usa-e-getta (`p29_probe_<...>`), dove le
migration vengono applicate nel loro `public` e che viene distrutto alla fine.

Il database separato, e non uno schema separato, e' obbligato: le prove che le
migration fanno su se stesse leggono il catalogo con `'public.<tabella>'
::regclass` e `n.nspname = 'public'` - come devono, perche' e' li' che le
tabelle vivranno davvero. Applicarle altrove le farebbe fallire per un motivo
che non e' loro.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui applicare 061/062/063",
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

NOTICES = "061_p29_consent_notices"
EVENTS = "062_p29_consent_events"
PROJECTION = "063_p29_contacts_consent_projection"

# Il minimo di schema P26 su cui le tre migration si appoggiano: le due tabelle
# che nominano, con le sole colonne che toccano. Non e' una riproduzione di
# CORE - non serve - ed e' deliberatamente piccola, cosi' un cambiamento a
# `contacts` altrove non fa fallire questo file per un motivo che non e' suo.
SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id     BIGSERIAL PRIMARY KEY,
    slug   VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE contacts (
    id                    BIGSERIAL PRIMARY KEY,
    agency_id             BIGINT REFERENCES agencies(id) ON DELETE RESTRICT,
    status                VARCHAR(20) NOT NULL DEFAULT 'active',
    archived_at           TIMESTAMPTZ,
    marketing_consent     BOOLEAN,
    marketing_consent_at  TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@pytest.fixture(scope="module")
def conn():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"

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
            for version in (NOTICES, EVENTS, PROJECTION):
                sql = (MIGRATIONS / f"{version}.sql").read_text(encoding="utf-8")
                cur.execute("BEGIN")
                cur.execute(sql)
                cur.execute("COMMIT")
        yield connection
    finally:
        connection.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def scenario(conn):
    """Un'agenzia, un contatto, un evento di consenso. Ripulito a ogni test."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM contacts WHERE TRUE")
        cur.execute("DELETE FROM agencies WHERE TRUE")
        cur.execute(
            "INSERT INTO agencies(slug) VALUES (%s) RETURNING id",
            (f"a-{uuid.uuid4().hex[:8]}",),
        )
        agency_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO contacts(agency_id) VALUES (%s) RETURNING id", (agency_id,)
        )
        contact_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO consent_events
               (agency_id, contact_id, purpose, decision, decided_at, source, actor_type)
               VALUES (%s, %s, 'marketing', 'granted', NOW(), 'public_stima', 'subject')
               RETURNING id""",
            (agency_id, contact_id),
        )
        event_id = cur.fetchone()[0]
    return {"agency_id": agency_id, "contact_id": contact_id, "event_id": event_id}


def esegui(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def conta(conn, tabella, dove="TRUE", params=None):
    return esegui(conn, f"SELECT count(*) FROM {tabella} WHERE {dove}", params)[0][0]


# ===========================================================================
# C1 - le due prove che la decisione richiede
# ===========================================================================
def test_c1_delete_diretta_di_un_evento_e_rifiutata(conn, scenario):
    import psycopg2

    with pytest.raises(psycopg2.errors.RaiseException) as errore:
        esegui(conn, "DELETE FROM consent_events WHERE id = %s", (scenario["event_id"],))
    assert "append-only" in str(errore.value)
    assert conta(conn, "consent_events") == 1


def test_c1_delete_del_contatto_porta_via_lo_storico(conn, scenario):
    """IL PURGE. Se questo fallisce, il cleanup di agenzia di P26-6 fallisce."""
    esegui(conn, "DELETE FROM contacts WHERE id = %s", (scenario["contact_id"],))
    assert conta(conn, "consent_events") == 0
    assert conta(conn, "contacts") == 0


def test_c1_delete_dell_agenzia_porta_via_lo_storico(conn, scenario):
    esegui(conn, "DELETE FROM contacts WHERE id = %s", (scenario["contact_id"],))
    esegui(conn, "DELETE FROM agencies WHERE id = %s", (scenario["agency_id"],))
    assert conta(conn, "consent_events") == 0


def test_c1_un_evento_di_un_altro_contatto_sopravvive_al_purge(conn, scenario):
    """Il purge e' mirato: porta via il soggetto cancellato, non la tabella."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO contacts(agency_id) VALUES (%s) RETURNING id",
            (scenario["agency_id"],),
        )
        altro = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO consent_events
               (agency_id, contact_id, purpose, decision, decided_at, source, actor_type, actor_ref)
               VALUES (%s, %s, 'marketing', 'granted', NOW(), 'crm', 'operator', 'operator:1')""",
            (scenario["agency_id"], altro),
        )

    esegui(conn, "DELETE FROM contacts WHERE id = %s", (scenario["contact_id"],))
    assert conta(conn, "consent_events") == 1
    assert conta(conn, "consent_events", "contact_id = %s", (altro,)) == 1


def test_c1_session_replication_role_non_apre_la_porta(conn, scenario):
    """Il buco che questa fase ha trovato, e che ENABLE ALWAYS chiude.

    Un trigger ordinario (tgenabled='O') NON scatta quando
    session_replication_role vale 'replica': misurato, la riga spariva. I
    trigger di 061 e 062 sono ENABLE ALWAYS proprio per questo, e questo test
    e' la guardia contro il giorno in cui qualcuno li ricreasse senza.
    """
    import psycopg2

    with conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SET LOCAL session_replication_role = 'replica'")
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "DELETE FROM consent_events WHERE id = %s", (scenario["event_id"],)
            )
        cur.execute("ROLLBACK")

    assert conta(conn, "consent_events") == 1


def test_c1_i_trigger_sono_enable_always(conn):
    righe = esegui(
        conn,
        """SELECT t.tgname, t.tgenabled
             FROM pg_trigger t
            WHERE t.tgrelid IN ('public.consent_events'::regclass,
                                'public.consent_notices'::regclass)
              AND NOT t.tgisinternal
            ORDER BY t.tgname""",
    )
    assert righe, "nessun trigger installato"
    for nome, abilitato in righe:
        assert abilitato == "A", (
            f"{nome} ha tgenabled={abilitato!r}: session_replication_role lo spegnerebbe"
        )


def test_c1_update_non_ha_alcuna_eccezione(conn, scenario):
    import psycopg2

    with pytest.raises(psycopg2.errors.RaiseException) as errore:
        esegui(
            conn,
            "UPDATE consent_events SET decision = 'revoked' WHERE id = %s",
            (scenario["event_id"],),
        )
    assert "append-only" in str(errore.value)


def test_c1_truncate_resta_vietato(conn, scenario):
    import psycopg2

    with pytest.raises(psycopg2.errors.RaiseException):
        esegui(conn, "TRUNCATE consent_events")
    assert conta(conn, "consent_events") == 1


def test_c1_le_fk_sono_davvero_cascade(conn):
    righe = esegui(
        conn,
        """SELECT a.attname, c.confdeltype
             FROM pg_constraint c
             JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            WHERE c.conrelid = 'public.consent_events'::regclass
              AND c.contype = 'f'
            ORDER BY a.attname""",
    )
    assert {nome: tipo for nome, tipo in righe} == {
        # 'c' = CASCADE: il purge del soggetto porta via il suo storico (C1).
        "agency_id": "c",
        "contact_id": "c",
        # 'r' = RESTRICT: il registro delle notice NON e' un soggetto e non e'
        # una tabella che il cleanup cancella. Una notice accettata da qualcuno
        # non deve poter sparire lasciando l'evento a puntare al nulla.
        "notice_id": "r",
    }


# ===========================================================================
# 061 - il registro
# ===========================================================================
def test_061_il_contenuto_non_si_riscrive(conn):
    import psycopg2

    notice = esegui(
        conn,
        """INSERT INTO consent_notices
           (purpose, version, content, content_hash, context, valid_from, created_by)
           VALUES ('marketing', %s, 'testo', repeat('a',64), 'probe', NOW(), 'test')
           RETURNING id""",
        (f"v-{uuid.uuid4().hex[:8]}",),
    )[0][0]

    with pytest.raises(psycopg2.errors.RaiseException):
        esegui(conn, "UPDATE consent_notices SET content = 'altro' WHERE id = %s", (notice,))
    with pytest.raises(psycopg2.errors.RaiseException):
        esegui(conn, "DELETE FROM consent_notices WHERE id = %s", (notice,))

    esegui(conn, "UPDATE consent_notices SET valid_to = NOW() WHERE id = %s", (notice,))

    with pytest.raises(psycopg2.errors.RaiseException):
        esegui(conn, "UPDATE consent_notices SET valid_to = NULL WHERE id = %s", (notice,))


def test_061_una_sola_versione_corrente_per_purpose(conn):
    import psycopg2

    esegui(conn, "UPDATE consent_notices SET valid_to = NOW() WHERE valid_to IS NULL")
    esegui(
        conn,
        """INSERT INTO consent_notices
           (purpose, version, content, content_hash, context, valid_from, created_by)
           VALUES ('privacy_terms', %s, 't', repeat('b',64), 'probe', NOW(), 'test')""",
        (f"v-{uuid.uuid4().hex[:8]}",),
    )
    with pytest.raises(psycopg2.errors.UniqueViolation):
        esegui(
            conn,
            """INSERT INTO consent_notices
               (purpose, version, content, content_hash, context, valid_from, created_by)
               VALUES ('privacy_terms', %s, 't', repeat('c',64), 'probe', NOW(), 'test')""",
            (f"v-{uuid.uuid4().hex[:8]}",),
        )
    esegui(conn, "UPDATE consent_notices SET valid_to = NOW() WHERE valid_to IS NULL")


# ===========================================================================
# 062 - i vincoli sugli eventi
# ===========================================================================
@pytest.mark.parametrize(
    "decisione,attore,ref",
    [
        ("revoked", "system", None),
        ("granted", "operator", None),
        ("not_given", "subject", None),
    ],
)
def test_062_i_vincoli_mordono(conn, scenario, decisione, attore, ref):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        esegui(
            conn,
            """INSERT INTO consent_events
               (agency_id, contact_id, purpose, decision, decided_at, source, actor_type, actor_ref)
               VALUES (%s, %s, 'marketing', %s, NOW(), 'crm', %s, %s)""",
            (scenario["agency_id"], scenario["contact_id"], decisione, attore, ref),
        )


def test_062_idempotency_key_unica(conn, scenario):
    import psycopg2

    chiave = f"k-{uuid.uuid4().hex[:8]}"
    for _ in range(1):
        esegui(
            conn,
            """INSERT INTO consent_events
               (agency_id, contact_id, purpose, decision, decided_at, source, actor_type, idempotency_key)
               VALUES (%s, %s, 'marketing', 'granted', NOW(), 'crm', 'subject', %s)""",
            (scenario["agency_id"], scenario["contact_id"], chiave),
        )
    with pytest.raises(psycopg2.errors.UniqueViolation):
        esegui(
            conn,
            """INSERT INTO consent_events
               (agency_id, contact_id, purpose, decision, decided_at, source, actor_type, idempotency_key)
               VALUES (%s, %s, 'marketing', 'revoked', NOW(), 'crm', 'subject', %s)""",
            (scenario["agency_id"], scenario["contact_id"], chiave),
        )


# ===========================================================================
# 063 - la proiezione
# ===========================================================================
def test_063_concesso_e_revocato_insieme_e_rifiutato(conn, scenario):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        esegui(
            conn,
            "UPDATE contacts SET marketing_consent = TRUE, marketing_revoked_at = NOW() WHERE id = %s",
            (scenario["contact_id"],),
        )


def test_063_privacy_accettata_senza_istante_e_rifiutata(conn, scenario):
    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        esegui(
            conn,
            "UPDATE contacts SET privacy_terms_accepted = TRUE WHERE id = %s",
            (scenario["contact_id"],),
        )


def test_063_la_revoca_legittima_passa(conn, scenario):
    esegui(
        conn,
        "UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() WHERE id = %s",
        (scenario["contact_id"],),
    )
    assert conta(conn, "contacts", "marketing_revoked_at IS NOT NULL") == 1


def test_063_le_colonne_storiche_esistono_ancora(conn):
    righe = esegui(
        conn,
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'contacts'
              AND column_name IN ('marketing_consent','marketing_consent_at')""",
    )
    assert {r[0] for r in righe} == {"marketing_consent", "marketing_consent_at"}
