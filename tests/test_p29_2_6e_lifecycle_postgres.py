"""P29-2.6E / 065 - il genitore di lifecycle, misurato su PostgreSQL reale.

La 065 applicata sopra la 064, e poi la matrice dei quindici casi. Quello che
si verifica qui non e' che i vincoli esistano - lo dice il catalogo, e la
migration se lo rilegge da sola - ma che il COMPORTAMENTO sia quello promesso:
in particolare le due semantiche condizionali, che una FK sola non sa esprimere.

    contatto presente -> cancellare la stima AZZERA stima_id, il messaggio resta
    contatto assente  -> cancellare la stima ELIMINA il messaggio

Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la 065")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    assigned_agent_id BIGINT, status VARCHAR(20) NOT NULL DEFAULT 'active',
    archived_at TIMESTAMPTZ, marketing_consent BOOLEAN, marketing_consent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id));
CREATE TABLE leads      (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100), telefono VARCHAR(30));
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
"""

VERSIONI = ("064_p29_communication_foundation", "065_p29_service_lifecycle_parent")


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
def mondo(db):
    """Un'agenzia, un contatto, una stima. Ricostruiti a ogni test, nell'ordine
    che i vincoli impongono: le stime prima dei contatti, i contatti prima
    dell'agenzia."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM stime")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a,))
        k = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id, nome, email) "
                    "VALUES (%s, 'Mario', 'mario@example.it') RETURNING id", (a,))
        st = cur.fetchone()[0]
    db.commit()
    return {"conn": db, "a": a, "k": k, "st": st}


def accoda(mondo, *, contact_id, stima_id, chiave, tipo="service"):
    with mondo["conn"].cursor() as cur:
        cur.execute("""
            INSERT INTO communication_messages
                (agency_id, contact_id, stima_id, channel, direction,
                 communication_type, mode, reason_code, rendered_body,
                 destination_snapshot, subject_snapshot, idempotency_key, actor_type)
            VALUES (%s, %s, %s, 'email', 'outbound', %s, 'automatic', 'stima_pdf',
                    'corpo', 'mario@example.it', 'oggetto', %s, 'system')
            RETURNING id
        """, (mondo["a"], contact_id, stima_id, tipo, chiave))
        message_id = cur.fetchone()[0]
    mondo["conn"].commit()
    return message_id


def riga(mondo, message_id):
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT contact_id, stima_id FROM communication_messages "
                    "WHERE id = %s", (message_id,))
        return cur.fetchone()


def prova(mondo, sql, params=()):
    """Esegue e riporta l'esito senza lasciare la transazione sporca."""
    try:
        with mondo["conn"].cursor() as cur:
            cur.execute(sql, params)
        mondo["conn"].commit()
        return None
    except Exception as exc:
        mondo["conn"].rollback()
        return exc


# ---------------------------------------------------------------------------
# 1-2  Il DELETE diretto resta vietato, con e senza contatto
# ---------------------------------------------------------------------------

def test_01_delete_diretto_con_contatto_e_stima_e_rifiutato(mondo):
    m = accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c01")
    errore = prova(mondo, "DELETE FROM communication_messages WHERE id = %s", (m,))
    assert errore is not None and "DELETE is refused" in str(errore)
    assert riga(mondo, m) is not None


def test_02_delete_diretto_senza_contatto_e_rifiutato(mondo):
    """IL caso del blocker 1: `contact_id` NULL e stima VIVA.

    La formula del guard, da sola, direbbe "genitore sparito" - `WHERE id =
    NULL` non trova nulla. A salvarci e' `pg_trigger_depth() > 1`, che per un
    DELETE diretto vale 1. Si misura, invece di fidarsi.
    """
    m = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c02")
    errore = prova(mondo, "DELETE FROM communication_messages WHERE id = %s", (m,))
    assert errore is not None and "DELETE is refused" in str(errore)
    assert riga(mondo, m) is not None, "il messaggio e' stato cancellato"


# ---------------------------------------------------------------------------
# 3-5  Le tre semantiche di lifecycle
# ---------------------------------------------------------------------------

def test_03_cancellare_il_contatto_elimina_il_messaggio(mondo):
    """Comportamento della 064, invariato."""
    m = accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c03")
    assert prova(mondo, "DELETE FROM contacts WHERE id = %s", (mondo["k"],)) is None
    assert riga(mondo, m) is None


def test_04_cancellare_la_stima_non_tocca_un_messaggio_con_contatto(mondo):
    """CASO A. La FK `SET NULL` della 064 resta, con la sua motivazione: la
    sparizione del contesto commerciale non cancella una comunicazione
    realmente scambiata con una persona."""
    m = accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c04")
    assert prova(mondo, "DELETE FROM stime WHERE id = %s", (mondo["st"],)) is None
    contact_id, stima_id = riga(mondo, m)
    assert contact_id == mondo["k"], "il contatto e' cambiato"
    assert stima_id is None, "stima_id non e' stato azzerato"


def test_05_cancellare_la_stima_elimina_un_messaggio_senza_contatto(mondo):
    """CASO B, ed e' la ragione per cui la 065 esiste."""
    m = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c05")
    assert prova(mondo, "DELETE FROM stime WHERE id = %s", (mondo["st"],)) is None
    assert riga(mondo, m) is None, "il messaggio e' sopravvissuto al suo unico genitore"


def test_05b_la_stima_porta_via_solo_i_propri_messaggi_senza_contatto(mondo):
    """Il trigger e' mirato: non tocca i messaggi legati, ne' quelli di un'altra
    stima."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO stime (agency_id, nome) VALUES (%s,'Altra') RETURNING id",
                    (mondo["a"],))
        altra = cur.fetchone()[0]
    mondo["conn"].commit()

    orfano = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c05b-1")
    legato = accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c05b-2")
    altrui = accoda(mondo, contact_id=None, stima_id=altra, chiave="c05b-3")

    assert prova(mondo, "DELETE FROM stime WHERE id = %s", (mondo["st"],)) is None
    assert riga(mondo, orfano) is None
    assert riga(mondo, legato) is not None and riga(mondo, legato)[1] is None
    assert riga(mondo, altrui) is not None, "cancellato il messaggio di un'altra stima"


# ---------------------------------------------------------------------------
# 6-7  Il purge tenant, e nessun figlio danglante
# ---------------------------------------------------------------------------

def test_06_il_purge_tenant_non_lascia_residui(mondo):
    """CASO D, nell'ordine di `DEDICATED_TABLES` di P26-6: stime, poi contacts,
    poi agencies."""
    accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c06-1")
    accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c06-2")
    accoda(mondo, contact_id=mondo["k"], stima_id=None, chiave="c06-3")

    assert prova(mondo, "DELETE FROM stime WHERE agency_id = %s", (mondo["a"],)) is None
    assert prova(mondo, "DELETE FROM contacts WHERE agency_id = %s", (mondo["a"],)) is None
    assert prova(mondo, "DELETE FROM agencies WHERE id = %s", (mondo["a"],)) is None

    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM communication_messages")
        assert cur.fetchone()[0] == 0, "il purge tenant ha lasciato residui"


def test_07_nessun_messaggio_sopravvive_alla_propria_agenzia(mondo):
    """Il backstop della FK diretta verso `agencies`.

    Un'agenzia con contatti o stime vivi non si cancella - `contacts.agency_id`
    e `stime.agency_id` sono RESTRICT - quindi la cascata scatta solo quando
    quei due sono gia' spariti. Qui si arriva a quel punto e si misura.
    """
    orfano = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c07")

    errore = prova(mondo, "DELETE FROM agencies WHERE id = %s", (mondo["a"],))
    assert errore is not None, "un'agenzia con contatti vivi si e' lasciata cancellare"
    assert riga(mondo, orfano) is not None

    assert prova(mondo, "DELETE FROM contacts WHERE agency_id = %s", (mondo["a"],)) is None
    assert prova(mondo, "DELETE FROM stime WHERE agency_id = %s", (mondo["a"],)) is None
    # La stima se n'e' andata e si e' portata via il suo orfano.
    assert riga(mondo, orfano) is None

    assert prova(mondo, "DELETE FROM agencies WHERE id = %s", (mondo["a"],)) is None
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM communication_messages "
                    "WHERE agency_id = %s", (mondo["a"],))
        assert cur.fetchone()[0] == 0, "agency_id danglante"


# ---------------------------------------------------------------------------
# 8-12  Cosa il database accetta e cosa rifiuta
# ---------------------------------------------------------------------------

def test_08_marketing_senza_contatto_e_rifiutato(mondo):
    errore = prova(mondo, """
        INSERT INTO communication_messages
            (agency_id, contact_id, stima_id, channel, direction, communication_type,
             mode, reason_code, rendered_body, destination_snapshot, subject_snapshot,
             idempotency_key, actor_type)
        VALUES (%s, NULL, %s, 'email', 'outbound', 'marketing', 'automatic', 'm2',
                'corpo', 'a@b.it', 'oggetto', 'c08', 'system')
    """, (mondo["a"], mondo["st"]))
    assert errore is not None
    assert "communication_messages_marketing_contact_chk" in str(errore)


def test_09_service_senza_contatto_e_senza_stima_e_rifiutato(mondo):
    errore = prova(mondo, """
        INSERT INTO communication_messages
            (agency_id, contact_id, stima_id, channel, direction, communication_type,
             mode, reason_code, rendered_body, destination_snapshot, subject_snapshot,
             idempotency_key, actor_type)
        VALUES (%s, NULL, NULL, 'email', 'outbound', 'service', 'automatic', 'stima_pdf',
                'corpo', 'a@b.it', 'oggetto', 'c09', 'system')
    """, (mondo["a"],))
    assert errore is not None
    assert "communication_messages_lifecycle_parent_chk" in str(errore)


def test_10_service_senza_contatto_ma_con_stima_e_accettato(mondo):
    m = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c10")
    assert riga(mondo, m) == (None, mondo["st"])


def test_11_service_con_contatto_e_senza_stima_e_accettato(mondo):
    m = accoda(mondo, contact_id=mondo["k"], stima_id=None, chiave="c11")
    assert riga(mondo, m) == (mondo["k"], None)


def test_12_una_agenzia_inesistente_e_rifiutata(mondo):
    """La FK diretta verso `agencies` che R4 aveva escluso: adesso c'e', e
    morde."""
    errore = prova(mondo, """
        INSERT INTO communication_messages
            (agency_id, contact_id, stima_id, channel, direction, communication_type,
             mode, reason_code, rendered_body, destination_snapshot, subject_snapshot,
             idempotency_key, actor_type)
        VALUES (999999, NULL, NULL, 'email', 'outbound', 'service', 'automatic',
                'stima_pdf', 'corpo', 'a@b.it', 'oggetto', 'c12', 'system')
    """)
    assert errore is not None
    assert ("communication_messages_agency_fk" in str(errore)
            or "lifecycle_parent_chk" in str(errore))


# ---------------------------------------------------------------------------
# 13-15  Il catalogo, e i trigger che un parametro di sessione non spegne
# ---------------------------------------------------------------------------

def test_13_il_trigger_su_stime_e_enable_always(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute("""
            SELECT tgenabled FROM pg_trigger
             WHERE tgname = 'trg_stime_purge_contactless_messages'
               AND tgrelid = 'public.stime'::regclass
        """)
        riga_catalogo = cur.fetchone()
    assert riga_catalogo is not None, "il trigger non esiste nel catalogo"
    assert riga_catalogo[0] == "A", (
        f"tgenabled = {riga_catalogo[0]!r}: un trigger ordinario non scatta con "
        "session_replication_role = 'replica'"
    )


def test_14_il_delete_diretto_resta_vietato_in_sessione_ordinaria(mondo):
    """Ripetuto qui di proposito, dopo aver toccato il catalogo: la promessa di
    C6 non dipende da come e' configurata la sessione."""
    with mondo["conn"].cursor() as cur:
        cur.execute("SHOW session_replication_role")
        assert cur.fetchone()[0] == "origin"
    m = accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c14")
    errore = prova(mondo, "DELETE FROM communication_messages WHERE id = %s", (m,))
    assert errore is not None and "DELETE is refused" in str(errore)


def test_15_i_guardiani_restano_attivi_anche_in_modalita_replica(mondo):
    """La sonda in `replica`, dove il permesso lo consente.

    Su PostgreSQL gestito - Render - `SET session_replication_role` richiede un
    privilegio che il ruolo applicativo non ha, e la sonda si salta dichiarando
    perche'. Dove passa, prova cio' che `ENABLE ALWAYS` esiste per garantire.
    """
    psycopg2 = pytest.importorskip("psycopg2")

    try:
        with mondo["conn"].cursor() as cur:
            cur.execute("SET session_replication_role = 'replica'")
    except psycopg2.errors.InsufficientPrivilege:
        mondo["conn"].rollback()
        pytest.skip("SET session_replication_role richiede un privilegio non concesso")

    try:
        m = accoda(mondo, contact_id=mondo["k"], stima_id=mondo["st"], chiave="c15")
        errore = prova(mondo, "DELETE FROM communication_messages WHERE id = %s", (m,))
        assert errore is not None and "DELETE is refused" in str(errore), (
            "in modalita' replica il guard C6 non ha protetto il ledger"
        )
        orfano = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="c15b")
        assert prova(mondo, "DELETE FROM stime WHERE id = %s", (mondo["st"],)) is None
        assert riga(mondo, orfano) is None, (
            "in modalita' replica il trigger di lifecycle su stime non ha agito"
        )
    finally:
        with mondo["conn"].cursor() as cur:
            cur.execute("SET session_replication_role = 'origin'")
        mondo["conn"].commit()


# ---------------------------------------------------------------------------
# La down: rifiuta invece di distruggere
# ---------------------------------------------------------------------------

def test_down_rifiuta_se_esistono_righe_senza_contatto(mondo):
    """E NON MODIFICA NULLA.

    Il down gira su una copia usa-e-getta del database, cosi' che il suo esito
    - qualunque sia - non lasci il modulo con lo schema smontato.
    """
    psycopg2 = pytest.importorskip("psycopg2")

    orfano = accoda(mondo, contact_id=None, stima_id=mondo["st"], chiave="down-1")
    down = (MIGRAZIONI / "065_p29_service_lifecycle_parent_down.sql").read_text(
        encoding="utf-8")

    errore = None
    try:
        with mondo["conn"].cursor() as cur:
            cur.execute(down)
        mondo["conn"].commit()
    except Exception as exc:
        mondo["conn"].rollback()
        errore = exc

    assert errore is not None, "il down ha accettato di distruggere una riga"
    assert "contact_id IS NULL" in str(errore)
    assert "Nothing has been changed" in str(errore) or "nothing has been changed" in str(errore).lower()

    # Nulla e' stato toccato: la riga c'e' ancora, e il vincolo pure.
    assert riga(mondo, orfano) is not None
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT attnotnull FROM pg_attribute "
                    "WHERE attrelid = 'public.communication_messages'::regclass "
                    "AND attname = 'contact_id'")
        assert cur.fetchone()[0] is False, "il down ha rimesso NOT NULL comunque"
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = "
                    "'trg_stime_purge_contactless_messages'")
        assert cur.fetchone()[0] == 1, "il down ha tolto il trigger comunque"


def test_down_passa_quando_non_ci_sono_righe_senza_contatto(db):
    """Su un database suo, perche' smonta davvero lo schema della 065."""
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_6e_down_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
            cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                        "(version TEXT PRIMARY KEY)")
            cur.execute("INSERT INTO schema_migrations (version) VALUES "
                        "('065_p29_service_lifecycle_parent')")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute((MIGRAZIONI / "065_p29_service_lifecycle_parent_down.sql")
                        .read_text(encoding="utf-8"))
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT attnotnull FROM pg_attribute "
                        "WHERE attrelid = 'public.communication_messages'::regclass "
                        "AND attname = 'contact_id'")
            assert cur.fetchone()[0] is True, "contact_id non e' tornato NOT NULL"
            cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = "
                        "'trg_stime_purge_contactless_messages'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname = "
                        "'stime_purge_contactless_messages'")
            assert cur.fetchone()[0] == 0, "la funzione e' rimasta orfana"
            cur.execute("SELECT count(*) FROM pg_constraint WHERE conname IN "
                        "('communication_messages_agency_fk', "
                        " 'communication_messages_marketing_contact_chk', "
                        " 'communication_messages_lifecycle_parent_chk')")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM schema_migrations WHERE version = "
                        "'065_p29_service_lifecycle_parent'")
            assert cur.fetchone()[0] == 0, "la riga del ledger e' rimasta"
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


# ---------------------------------------------------------------------------
# Il service rifiuta PRIMA del database
# ---------------------------------------------------------------------------

@pytest.fixture
def dominio(db, monkeypatch, mondo):
    """I cursori del dominio dirottati sulla connessione di prova."""
    from contextlib import contextmanager

    from psycopg2.extras import RealDictCursor

    from communication import database as communication_database
    from communication import dispatcher as communication_dispatcher
    from communication import repository as communication_repository
    from communication import service as communication_service

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
                   communication_repository, communication_dispatcher):
        monkeypatch.setattr(modulo, "communication_cursor", cursore, raising=False)
    return communication_service


class Operatore:
    def __init__(self, agency_id):
        self.agency_id, self.user_id, self.role = agency_id, 42, "agent"
        self.is_platform_admin = False

    def require_agency(self):
        return self.agency_id


def _ctx(mondo):
    from operator_auth.context import SystemAgencyContext

    return SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")


def _accoda_dal_service(servizio, mondo, *, contact_id, stima_id, chiave,
                        tipo="service"):
    from communication.enums import MODE_AUTOMATIC, REASON_M2, REASON_STIMA_PDF

    from psycopg2.extras import RealDictCursor

    cur = mondo["conn"].cursor(cursor_factory=RealDictCursor)
    try:
        return servizio.enqueue(
            _ctx(mondo), cur=cur, contact_id=contact_id, channel="email",
            communication_type=tipo, mode=MODE_AUTOMATIC,
            reason_code=REASON_M2 if tipo == "marketing" else REASON_STIMA_PDF,
            rendered_body="corpo", destination_snapshot="mario@example.it",
            subject_snapshot="oggetto", idempotency_key=chiave, stima_id=stima_id)
    finally:
        cur.close()
        mondo["conn"].commit()


def test_service_rifiuta_marketing_senza_contatto(dominio, mondo):
    from communication.exceptions import ValidationError

    with pytest.raises(ValidationError) as exc:
        _accoda_dal_service(dominio, mondo, contact_id=None, stima_id=mondo["st"],
                            chiave="svc-1", tipo="marketing")
    assert "contact_id is required for marketing" in str(exc.value)


def test_service_accetta_service_senza_contatto_con_stima(dominio, mondo):
    esito = _accoda_dal_service(dominio, mondo, contact_id=None,
                                stima_id=mondo["st"], chiave="svc-2")
    assert esito["created"] is True
    assert esito["message"]["contact_id"] is None
    assert esito["message"]["stima_id"] == mondo["st"]


def test_service_rifiuta_service_senza_contatto_e_senza_stima(dominio, mondo):
    from communication.exceptions import ValidationError

    with pytest.raises(ValidationError) as exc:
        _accoda_dal_service(dominio, mondo, contact_id=None, stima_id=None,
                            chiave="svc-3")
    assert "needs a stima_id" in str(exc.value)


def test_service_non_fabbrica_contatti(dominio, mondo):
    """Prima e dopo: il numero di contatti non cambia."""
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM contacts")
        prima = cur.fetchone()[0]

    _accoda_dal_service(dominio, mondo, contact_id=None, stima_id=mondo["st"],
                        chiave="svc-4")

    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM contacts")
        assert cur.fetchone()[0] == prima, "il service ha creato un contatto"


def test_service_con_contatto_continua_a_verificarne_lo_scope(dominio, mondo):
    """La risoluzione del contatto non e' sparita: e' diventata condizionale."""
    from communication.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        _accoda_dal_service(dominio, mondo, contact_id=999999,
                            stima_id=mondo["st"], chiave="svc-5")
