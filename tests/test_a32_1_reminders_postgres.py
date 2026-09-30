"""A32-1 - la migration 079 su PostgreSQL VERO.

Banco: il database usa-e-getta della catena Agenda gia' certificata (072-078,
fixture di A30-2/A30-13B/A31-2: la 078 e' APPLICATA prima), piu' il ledger
P29 preso dai file VERI (064, 065, 067) e il vincolo reale
`contacts_agency_scope_unq` dalla 030, che la FK composita della 064 richiede.
Poi la 079: UP -> il valore nuovo e' accettato e uno inventato no -> DOWN ->
il valore nuovo e' rifiutato -> re-UP -> accettato. E il DOWN rifiuta se il
ledger contiene gia' un promemoria. Nessun DB TEST remoto.
"""
from __future__ import annotations

import os
import re

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_2_appointments_postgres import db, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31  # noqa: F401

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la 079")

MIGRAZIONI = a30_2.MIGRAZIONI
VERSIONE = "079_a32_1_appointment_reminders"
LEDGER = ("064_p29_communication_foundation", "065_p29_service_lifecycle_parent",
          "067_lmc1b_owner_login_reason")


def _testo(versione: str) -> str:
    return (MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8")


def _giu() -> str:
    return (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")


def _unq_030() -> str:
    testo = _testo("030_p26_core_agency_enforce")
    return re.search(r"DO \$do\$\nBEGIN\n    IF NOT EXISTS \(\n        SELECT 1 FROM pg_constraint "
                     r"WHERE conname = 'contacts_agency_scope_unq'.*?\$do\$;", testo, re.S).group(0)


@pytest.fixture(scope="module")
def ledger(schema_a31):  # noqa: F811
    conn = schema_a31["conn"]
    with conn.cursor() as cur:
        cur.execute(_unq_030())
        for versione in LEDGER:
            cur.execute(_testo(versione))
    conn.commit()
    return schema_a31


@pytest.fixture
def m(mondo, ledger):  # noqa: F811
    yield mondo
    mondo["conn"].rollback()


def _definizione(cur) -> str:
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'communication_messages_reason_code_chk'")
    return cur.fetchone()[0]


def _inserisci(cur, m, reason_code, chiave, contatto="mario"):
    cur.execute("SAVEPOINT prova")
    try:
        cur.execute(
            "INSERT INTO communication_messages (agency_id, contact_id, channel, direction, "
            "communication_type, mode, reason_code, template_key, template_version, "
            "rendered_body, destination_snapshot, subject_snapshot, idempotency_key, actor_type, "
            "metadata) VALUES (%s,%s,'email','outbound','service','automatic',%s,"
            "'appointment_reminder_24h',1,'<p>corpo</p>','mario@example.it','Promemoria',%s,"
            "'system','{\"offset\": \"24h\"}')", (m["a"], m[contatto], reason_code, chiave))
    except Exception as exc:  # noqa: BLE001 - l'esito del CHECK e' il dato
        cur.execute("ROLLBACK TO SAVEPOINT prova")
        return exc
    cur.execute("RELEASE SAVEPOINT prova")
    return None


def _up(cur):
    cur.execute(_testo(VERSIONE))
    cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (VERSIONE,))


def test_01_con_la_078_applicata_la_079_up_down_re_up(m):
    conn = m["conn"]
    conn.rollback()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '5s'")
            # la 078 e' applicata (la colonna della proiezione esiste), la 079 no
            cur.execute("SELECT count(*) FROM information_schema.columns WHERE "
                        "table_name='property_visits' AND column_name='appointment_id'")
            assert cur.fetchone()[0] == 1
            prima = _definizione(cur)
            assert "owner_login_link" in prima and "appointment_reminder" not in prima
            cur.execute("BEGIN")
            assert _inserisci(cur, m, "appointment_reminder", "k-prima") is not None
            cur.execute("ROLLBACK")

            # UP
            _up(cur)
            dopo = _definizione(cur)
            for atteso in ("appointment_reminder", "owner_login_link", "stima_pdf", "m5"):
                assert atteso in dopo, atteso
            cur.execute("BEGIN")
            assert _inserisci(cur, m, "appointment_reminder", "k-up") is None
            rifiutato = _inserisci(cur, m, "appointment_reminder_24h", "k-no")
            assert rifiutato is not None and "reason_code_chk" in str(rifiutato)
            assert _inserisci(cur, m, "stima_pdf", "k-storico") is None
            cur.execute("ROLLBACK")                    # il ledger resta vuoto

            # DOWN
            cur.execute(_giu())
            assert "appointment_reminder" not in _definizione(cur)
            assert "owner_login_link" in _definizione(cur)
            cur.execute("SELECT count(*) FROM schema_migrations WHERE version = %s", (VERSIONE,))
            assert cur.fetchone()[0] == 0
            cur.execute("BEGIN")
            assert _inserisci(cur, m, "appointment_reminder", "k-giu") is not None
            cur.execute("ROLLBACK")

            # re-UP
            _up(cur)
            cur.execute("BEGIN")
            assert _inserisci(cur, m, "appointment_reminder", "k-reup") is None
            cur.execute("ROLLBACK")
    finally:
        conn.autocommit = False


def test_02_il_down_rifiuta_se_il_ledger_ha_un_promemoria(m):
    conn = m["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        assert "appointment_reminder" in _definizione(cur)       # dal test precedente
        # Bruno: nessun lead lo referenzia, quindi il suo purge e' possibile
        assert _inserisci(cur, m, "appointment_reminder", "k-vivo", "bruno") is None
    conn.commit()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            with pytest.raises(Exception) as errore:
                cur.execute(_giu())
            assert "appointment_reminder" in str(errore.value)
            cur.execute("ROLLBACK")                  # il BEGIN del down e' ancora aperto
            assert "appointment_reminder" in _definizione(cur)    # nulla e' cambiato
            cur.execute("SELECT count(*) FROM communication_messages "
                        "WHERE reason_code = 'appointment_reminder'")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM schema_migrations WHERE version = %s", (VERSIONE,))
            assert cur.fetchone()[0] == 1
            # pulizia: il ledger si svuota solo col purge del contatto (064)
            cur.execute("DELETE FROM contacts WHERE id = %s", (m["bruno"],))
            cur.execute("SELECT count(*) FROM communication_messages")
            assert cur.fetchone()[0] == 0
    finally:
        conn.autocommit = False
