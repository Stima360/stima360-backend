"""P29-2.6E OPS - la catena operativa completa, su PostgreSQL reale e via HTTP.

COSA PROVA QUESTO MODULO, E PERCHE' NON BASTAVANO GLI ALTRI

Gli altri moduli di P29-2 provano il dominio: il claim, il fencing, il gate, il
trasporto. Nessuno prova che qualcuno POSSA INVOCARLO da fuori. Fino a P29-2.5E
la rotta esisteva ma non era montata, e il cron che il design indica come
modello - `run_followup_p18d_cron.py` - autentica in HTTP Basic, che dopo P26-5
prende 401. Una coda che nessuno svuota non e' un dettaglio operativo: e' il
cutover che non si puo' fare.

Qui si mette in fila tutto quello che serve perche' un'email parta davvero:

    operatore `agent` reale, con la sua password        (account least-privilege)
    POST /api/operator-auth/login                       -> 204 + Set-Cookie
    POST /api/communication/dispatch {channel, limit}   -> con quel cookie
    POST /api/operator-auth/logout

L'app e' quella vera - `main.app`, con i router montati - e il database e' un
PostgreSQL usa-e-getta. `database.invia_mail` e' sempre una spia: nessuna email
parte da un test.

Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare P29-2.6E OPS")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(200), status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE operator_users (
    id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL,
    email_normalized VARCHAR(320) NOT NULL UNIQUE, password_hash TEXT NOT NULL,
    first_name VARCHAR(100), last_name VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE, last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE agency_memberships (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    operator_user_id BIGINT NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT agency_memberships_unq UNIQUE (agency_id, operator_user_id));
CREATE TABLE operator_sessions (
    id BIGSERIAL PRIMARY KEY,
    operator_user_id BIGINT NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    -- P28: il contesto di "acting agency" del platform admin. Qui resta sempre
    -- NULL - il cron e' un agente, non un platform admin - ma la query di
    -- risoluzione della sessione lo legge, e uno schema minimo che non lo
    -- avesse farebbe fallire il login con un errore di colonna mancante.
    acting_agency_id BIGINT REFERENCES agencies(id),
    acting_entered_at TIMESTAMPTZ);
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    assigned_agent_id BIGINT, status VARCHAR(20) NOT NULL DEFAULT 'active',
    archived_at TIMESTAMPTZ, marketing_consent BOOLEAN, marketing_consent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id));
CREATE TABLE leads (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100), telefono VARCHAR(30));
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
-- P29 cutover: la finalizzazione `sent` di una mail di stima scrive QUI, nella
-- sua stessa transazione. Senza questa tabella il giro completo non e' il giro
-- completo - e la prima stesura, che non la aveva, lo ha scoperto con un
-- UndefinedTable proprio dentro il commit finale.
--
-- Fedele a 017 + 044: chiavi, indice parziale di idempotenza, `agency_id` NOT
-- NULL. Le FK sono ON DELETE SET NULL come in 017, perche' Seller Intelligence
-- non deve mai poter bloccare una cancellazione che CORE permetterebbe.
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL,
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

VERSIONI = ("061_p29_consent_notices", "062_p29_consent_events",
            "063_p29_contacts_consent_projection", "064_p29_communication_foundation",
            "065_p29_service_lifecycle_parent")

PASSWORD = "dispatch-cron-password-di-prova"


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_6e_ops_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    """Due agenzie, un contatto, una stima, e UN CONTO PER RUOLO.

    Il conto del cron e' l'`agency_admin`: il minimo che la rotta autorizza
    (blocker 1). Accanto ci sono un `agency_owner` e un `agent` della stessa
    agenzia, perche' la matrice di autorizzazione si misura cambiando SOLO il
    ruolo, e un `agency_owner` della SECONDA agenzia, perche' "ha il ruolo
    giusto" non deve bastare a toccare la coda di un'altra.

    Il contatto non e' assegnato a nessuno, ed e' di proposito: se il
    dispatcher ereditasse lo scope di chi lo invoca, un giorno in cui quel
    qualcuno fosse ristretto per `assigned_agent_id` salterebbe il messaggio in
    silenzio. Lo misura `ops_5`, guardando il contesto invece dell'effetto.
    """
    from contextlib import contextmanager

    from psycopg2.extras import RealDictCursor

    from core.normalization import normalize_email
    from operator_auth import database as operator_database
    from operator_auth import dependencies as operator_dependencies
    from operator_auth import repository as operator_repository
    from operator_auth import service as operator_service
    from operator_auth.security import hash_password
    from communication import database as communication_database
    from communication import dispatcher as communication_dispatcher
    from communication import repository as communication_repository
    from communication import service as communication_service
    from consent import database as consent_database
    from consent import repository as consent_repository

    # La connessione e' condivisa con l'app: un test che finisce con una
    # richiesta rifiutata la lascia in una transazione abortita, e il primo
    # DELETE qui sotto morirebbe con InFailedSqlTransaction invece di dire cosa
    # e' andato storto davvero.
    db.rollback()

    suffisso = uuid.uuid4().hex[:8]

    def indirizzo(ruolo: str) -> str:
        return f"{ruolo}-{suffisso}@example.it"

    # L'account del cron e' l'`agency_admin`: e' il RUOLO MINIMO che la rotta
    # autorizza davvero (P29-2.6E, blocker 1). `agent` non basta piu', e non
    # perche' il cron sia speciale - perche' un giro di dispatch agisce su
    # tutti i record dell'agenzia, e un agente non li vede tutti.
    email = indirizzo("agency_admin")
    with db.cursor() as cur:
        # I guardiani rifiutano il DELETE diretto, ed e' il loro mestiere: per
        # ripulire fra un test e l'altro si spengono e si RIACCENDONO con
        # ENABLE ALWAYS, come fanno gli altri moduli PostgreSQL di P29.
        cur.execute("ALTER TABLE communication_attempts DISABLE TRIGGER USER")
        cur.execute("ALTER TABLE communication_messages DISABLE TRIGGER USER")
        cur.execute("DELETE FROM communication_attempts")
        cur.execute("DELETE FROM communication_messages")
        for tabella, guardiano in (
                ("communication_messages", "trg_communication_messages_guard"),
                ("communication_messages", "trg_communication_messages_no_truncate"),
                ("communication_attempts", "trg_communication_attempts_guard"),
                ("communication_attempts", "trg_communication_attempts_no_truncate")):
            cur.execute(f"ALTER TABLE {tabella} ENABLE ALWAYS TRIGGER {guardiano}")
        cur.execute("DELETE FROM seller_timeline_events")
        cur.execute("DELETE FROM operator_sessions")
        cur.execute("DELETE FROM agency_memberships")
        cur.execute("DELETE FROM operator_users")
        cur.execute("DELETE FROM stime")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id, assigned_agent_id) "
                    "VALUES (%s, NULL) RETURNING id", (a,))
        k = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id, nome, email) "
                    "VALUES (%s, 'Mario', 'mario@example.it') RETURNING id", (a,))
        st = cur.fetchone()[0]
        # UNA persona per ruolo, tutte nella stessa agenzia: la matrice di
        # autorizzazione si misura cambiando SOLO il ruolo.
        impronta = hash_password(PASSWORD)

        def operatore(ruolo: str, agenzia: int, *, platform_admin: bool = False) -> int:
            indir = indirizzo(ruolo if agenzia == a else f"{ruolo}-b")
            cur.execute(
                """INSERT INTO operator_users
                       (email, email_normalized, password_hash, status, is_platform_admin)
                   VALUES (%s, %s, %s, 'active', %s) RETURNING id""",
                (indir, normalize_email(indir), impronta, platform_admin))
            uid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO agency_memberships (agency_id, operator_user_id, role, status)
                   VALUES (%s, %s, %s, 'active')""", (agenzia, uid, ruolo))
            return uid

        u = operatore("agency_admin", a)
        u_owner = operatore("agency_owner", a)
        # L'agente NON e' assegnatario del contatto, ed e' voluto: se il
        # dispatcher ereditasse il suo scope salterebbe quel messaggio in
        # silenzio. Adesso quell'agente non arriva nemmeno a chiamare la rotta.
        u_agent = operatore("agent", a)

        # La seconda agenzia esiste per una domanda sola: un owner di B puo'
        # far partire la coda di A? Ha il ruolo giusto, e non deve bastare.
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-due') RETURNING id")
        b = cur.fetchone()[0]
        u_owner_b = operatore("agency_owner", b)
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
    # default, e il messaggio non arriverebbe mai a `sent`.
    for modulo in (communication_database, communication_service,
                   communication_repository, communication_dispatcher):
        monkeypatch.setattr(modulo, "communication_cursor", cursore, raising=False)
    for modulo in (consent_database, consent_repository):
        monkeypatch.setattr(modulo, "consent_cursor", cursore, raising=False)
    # `operator_cursor` e' importato per NOME in tre moduli: sostituirlo solo
    # su `database` lascerebbe gli altri due legati alla funzione originale, che
    # aprirebbe una connessione verso il DSN di default. Il primo tentativo l'ha
    # fatto davvero, e il test e' fallito con "connection to server on socket
    # /var/run/postgresql" - il modo giusto di scoprirlo.
    for modulo in (operator_database, operator_repository, operator_service,
                   operator_dependencies):
        monkeypatch.setattr(modulo, "operator_cursor", cursore, raising=False)

    return {"conn": db, "a": a, "b": b, "k": k, "st": st, "email": email,
            "user_id": u,
            "email_owner": indirizzo("agency_owner"),
            "email_agent": indirizzo("agent"),
            "email_owner_b": indirizzo("agency_owner-b"),
            "user_owner": u_owner, "user_agent": u_agent, "user_owner_b": u_owner_b,
            "cur": lambda: db.cursor(cursor_factory=RealDictCursor)}


def _client_isolato(mondo, monkeypatch):
    """Il corpo della fixture `client`, come generatore.

    E' una funzione e non solo una fixture perche' `test_ops_13` deve entrarci
    DOPO aver sporcato `dependency_overrides` a mano: una fixture verrebbe
    risolta prima del corpo del test, e non proverebbe niente.
    """
    import tests.conftest  # noqa: F401  (installa lo shim quando serve)
    import main as main_module

    inviate: list[tuple] = []
    from communication.providers import email_smtp

    def finta_invia_mail(destinatario, oggetto, corpo_html, allegato=None):
        inviate.append((destinatario, oggetto, corpo_html, allegato))
        return True

    monkeypatch.setattr(email_smtp, "invia_mail", finta_invia_mail)
    # COLLISIONE SEGNALATA, NON MODIFICATA (vedi audit P29-2.6E OPS, punto
    # "dependency_overrides"): `tests/test_p26_6c_backend_gate_closure.py`
    # installa `main.app.dependency_overrides[legacy_basic_agency_context]` e
    # non lo rimuove mai. E' la STESSA dipendenza che la route di dispatch
    # dichiara, quindi in una suite intera quell'override decide l'agenzia al
    # posto della sessione e il dispatch trova zero righe: `claimed=0`, non un
    # 401, cioe' il genere di guasto che dipende dall'ordine dei file.
    #
    # Qui NON si tocca quel modulo, che appartiene a lavoro gia' certificato.
    # Si isola questo: si mette da parte la mappa degli override per la durata
    # del test e si rimette identica alla fine - il comportamento della suite
    # fuori di qui resta quello di prima, riga per riga.
    override_altrui = dict(main_module.app.dependency_overrides)
    main_module.app.dependency_overrides.clear()
    # HTTPS, e non e' un dettaglio del test: `set_cookie` mette `secure=True`
    # senza condizioni - "TEST e PROD servono entrambi su HTTPS, e un flag
    # condizionale e' un piede nella trappola della configurazione". Su
    # `http://testserver` il client non rimanderebbe il cookie e il dispatch
    # prenderebbe 401: e' successo davvero al primo giro. Il base_url HTTPS
    # riproduce la produzione, non aggira il vincolo.
    try:
        with TestClient(main_module.app, base_url="https://testserver") as c:
            yield c, inviate
    finally:
        main_module.app.dependency_overrides.clear()
        main_module.app.dependency_overrides.update(override_altrui)


@pytest.fixture
def client(mondo, monkeypatch):
    """L'app VERA, con i router montati come in produzione."""
    yield from _client_isolato(mondo, monkeypatch)


def accoda(mondo, *, chiave, contact_id=None, tipo="service",
           destinatario="mario@example.it", oggetto="La tua stima",
           corpo="<p>ecco la stima</p>"):
    from communication import service
    from communication.enums import MODE_AUTOMATIC, REASON_M2, REASON_STIMA_PDF
    from operator_auth.context import SystemAgencyContext
    from psycopg2.extras import RealDictCursor

    ctx = SystemAgencyContext(agency_id=mondo["a"], origin="communication_dispatch")
    cur = mondo["conn"].cursor(cursor_factory=RealDictCursor)
    try:
        esito = service.enqueue(
            ctx, cur=cur, contact_id=contact_id, channel="email",
            communication_type=tipo, mode=MODE_AUTOMATIC,
            reason_code=REASON_M2 if tipo == "marketing" else REASON_STIMA_PDF,
            rendered_body=corpo, destination_snapshot=destinatario,
            subject_snapshot=oggetto, idempotency_key=chiave,
            stima_id=mondo["st"], metadata={"pdf_url": "https://example.it/x.pdf"})
    finally:
        cur.close()
        mondo["conn"].commit()
    return esito["message"]


def riga(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_messages WHERE id = %s", (message_id,))
        return cur.fetchone()


def accedi(client, mondo, chiave="email"):
    """Il login. `chiave` sceglie QUALE dei conti di `mondo` si presenta."""
    client.cookies.clear()
    return client.post("/api/operator-auth/login",
                       json={"email": mondo[chiave], "password": PASSWORD})


# ---------------------------------------------------------------------------
# La rotta e' montata, e non e' aperta
# ---------------------------------------------------------------------------

def test_ops_1_la_rotta_e_montata_nella_app_vera():
    import tests.conftest  # noqa: F401
    import main as main_module

    assert "/api/communication/dispatch" in main_module.app.openapi()["paths"]


def test_ops_2_senza_sessione_la_rotta_rifiuta(client, mondo):
    c, _ = client
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10})
    assert risposta.status_code == 401, risposta.text


def test_ops_3_il_basic_da_solo_non_basta_piu(client, mondo):
    """La ragione per cui questo runner non e' una copia di quello di P18-D2."""
    c, _ = client
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10},
                      auth=(mondo["email"], PASSWORD))
    assert risposta.status_code == 401, risposta.text


# ---------------------------------------------------------------------------
# La catena completa: login -> dispatch -> logout
# ---------------------------------------------------------------------------

def test_ops_4_login_dispatch_logout_manda_il_messaggio(client, mondo):
    """IL test operativo della fase: da `queued` a `sent` passando dall'HTTP."""
    c, inviate = client
    m = accoda(mondo, chiave="ops-4", destinatario="mario@example.it",
               oggetto="La tua stima e' pronta", corpo="<h1>ciao</h1>")
    assert riga(mondo, m["id"])["status"] == "queued"

    accesso = accedi(c, mondo)
    assert accesso.status_code == 204, accesso.text

    dispatch = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10})
    assert dispatch.status_code == 200, dispatch.text
    conteggi = dispatch.json()
    assert conteggi["claimed"] == 1 and conteggi["sent"] == 1
    assert conteggi["lost"] == 0

    assert inviate == [("mario@example.it", "La tua stima e' pronta",
                        "<h1>ciao</h1>", None)]

    finale = riga(mondo, m["id"])
    assert finale["status"] == "sent" and finale["sent_at"] is not None
    assert finale["provider"] == "email_smtp"
    assert finale["provider_message_id"] is None

    uscita = c.post("/api/operator-auth/logout")
    assert uscita.status_code == 204

    # E dopo il logout quel cookie non apre piu' niente.
    dopo = c.post("/api/communication/dispatch", json={"channel": "email", "limit": 10})
    assert dopo.status_code == 401


def test_ops_5_il_dispatcher_non_gira_sullo_scope_del_chiamante(
        client, mondo, monkeypatch):
    """D1, per via HTTP, e con la prova DIRETTA invece che per assenza.

    Il contatto non e' assegnato a nessuno. Prima questo test si accontentava
    di vedere il messaggio partire: adesso il chiamante e' un `agency_admin`,
    che quel contatto lo vedrebbe comunque, quindi "e' partito" non proverebbe
    piu' niente. Si guarda allora il contesto che il dispatcher riceve
    davvero: dev'essere un `SystemAgencyContext` - `role is None`,
    `user_id is None`, `is_platform_admin` False - costruito dalla sola
    agenzia. Se un giorno qualcuno gli passasse il contesto dell'operatore, il
    restringimento per `assigned_agent_id` tornerebbe a mordere in silenzio, e
    questo test lo direbbe prima.
    """
    from communication import dispatcher as dispatcher_module
    from communication import service as service_module
    from operator_auth.context import SystemAgencyContext

    # La spia sta su `service.claim_due`, cioe' UN GRADINO SOTTO
    # `dispatch_batch`: e' li' che si vede il contesto con cui il dominio
    # interroga davvero la coda. Spiare `dispatch_batch` mostrerebbe solo il
    # contesto dell'operatore, che e' l'ingresso, non lo scope di lavoro.
    visti = []
    vero = dispatcher_module.service.claim_due

    def spia(ctx, **kwargs):
        visti.append(ctx)
        return vero(ctx, **kwargs)

    monkeypatch.setattr(service_module, "claim_due", spia)

    c, inviate = client
    m = accoda(mondo, chiave="ops-5", contact_id=mondo["k"])

    assert accedi(c, mondo).status_code == 204
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10})

    assert risposta.status_code == 200 and risposta.json()["sent"] == 1
    assert riga(mondo, m["id"])["status"] == "sent"
    assert len(inviate) == 1

    assert len(visti) == 1
    ctx = visti[0]
    assert isinstance(ctx, SystemAgencyContext)
    assert ctx.agency_id == mondo["a"]
    assert ctx.origin == "communication_dispatch"
    assert ctx.role is None and ctx.user_id is None
    assert ctx.is_platform_admin is False


def test_ops_6_agency_id_nel_payload_resta_un_422(client, mondo):
    c, _ = client
    assert accedi(c, mondo).status_code == 204
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10, "agency_id": 999})
    assert risposta.status_code == 422, risposta.text


def test_ops_7_il_canale_e_obbligatorio_anche_via_http(client, mondo):
    c, _ = client
    assert accedi(c, mondo).status_code == 204
    assert c.post("/api/communication/dispatch", json={"limit": 10}).status_code == 422
    assert c.post("/api/communication/dispatch",
                  json={"channel": "sms", "limit": 10}).status_code == 422


def test_ops_8_un_canale_senza_trasporto_reale_e_rifiutato(client, mondo):
    """`whatsapp` non ha un adapter: P29-2.5W e' deferita e R3 e' OPEN.

    La rotta deve RIFIUTARE, non servire quel canale con il provider finto -
    che direbbe `sent` senza aver mandato niente. 501 e non 422: la richiesta
    e' ben formata, e' il server a non avere ancora quel pezzo.
    """
    c, inviate = client
    m = accoda(mondo, chiave="ops-8")

    assert accedi(c, mondo).status_code == 204
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "whatsapp", "limit": 10})

    assert risposta.status_code == 501, risposta.text
    assert "no real transport" in risposta.text
    assert inviate == []
    assert riga(mondo, m["id"])["status"] == "queued", "la coda email e' stata toccata"


def test_ops_9_un_secondo_giro_non_manda_niente(client, mondo):
    """Nessun doppio invio: il messaggio e' terminale."""
    c, inviate = client
    accoda(mondo, chiave="ops-9")
    assert accedi(c, mondo).status_code == 204

    primo = c.post("/api/communication/dispatch", json={"channel": "email", "limit": 10})
    secondo = c.post("/api/communication/dispatch", json={"channel": "email", "limit": 10})

    assert primo.json()["sent"] == 1
    assert secondo.json() == {"claimed": 0, "sent": 0, "suppressed": 0, "failed": 0,
                              "indeterminate": 0, "lost": 0}
    assert len(inviate) == 1


# ---------------------------------------------------------------------------
# Il runner, contro l'app vera
# ---------------------------------------------------------------------------

def test_ops_10_il_runner_esegue_la_catena_contro_lapp_vera(client, mondo, monkeypatch):
    """`run_once` con la sessione del TestClient al posto di `requests`.

    Non si simula il runner: si esegue la sua funzione, con le sue chiamate e i
    suoi controlli sulla risposta. Cio' che si sostituisce e' solo il trasporto
    HTTP - `TestClient` espone `post` con la stessa firma - perche' il runner
    gira contro un server, e qui il server e' in processo.
    """
    import importlib

    runner = importlib.import_module("run_communication_dispatch_cron")
    c, inviate = client
    m = accoda(mondo, chiave="ops-10")

    config = runner.Config(base_url="", email=mondo["email"], password=PASSWORD,
                           channel="email", limit=10, timeout=(5.0, 60.0))
    dati = runner.run_once(config, sessione=c)

    assert dati["sent"] == 1 and dati["lost"] == 0
    assert riga(mondo, m["id"])["status"] == "sent"
    assert len(inviate) == 1
    # Il logout del `finally` ha davvero chiuso la sessione.
    assert c.post("/api/communication/dispatch",
                  json={"channel": "email", "limit": 10}).status_code == 401


def test_ops_11_il_runner_fallisce_in_modo_leggibile_su_credenziali_sbagliate():
    """SENZA TestClient, DI PROPOSITO.

    Il runner mappa gli errori su `requests.RequestException`, perche' e'
    `requests` che usa in produzione. `TestClient` parla httpx e solleva
    `httpx.HTTPStatusError`, che quella clausola non cattura: usarlo qui
    proverebbe il comportamento di una libreria che il runner non usa. Il
    primo tentativo l'ha fatto, e l'errore httpx e' sfuggito al runner - il
    modo giusto di scoprire che il test stava misurando la cosa sbagliata.

    Qui la sessione e' una finta minima che solleva cio' che `requests`
    solleverebbe davvero su un 401.
    """
    import importlib

    import requests

    runner = importlib.import_module("run_communication_dispatch_cron")

    class RispostaRifiutata:
        status_code = 401

        def raise_for_status(self):
            raise requests.HTTPError("401 Client Error")

    class SessioneFinta:
        def __init__(self):
            self.chiamate = []

        def post(self, url, **kwargs):
            self.chiamate.append(url)
            return RispostaRifiutata()

    sessione = SessioneFinta()
    config = runner.Config(base_url="https://esempio", email="x@y.it",
                           password="sbagliata", channel="email", limit=10,
                           timeout=(5.0, 60.0))

    with pytest.raises(runner.TechnicalError) as exc:
        runner.run_once(config, sessione=sessione)

    assert "login" in str(exc.value)
    # Non ha provato a dispacciare dopo un login rifiutato...
    assert not any("dispatch" in u for u in sessione.chiamate)
    # ...ma ha comunque tentato il logout, che e' nel `finally`.
    assert any("logout" in u for u in sessione.chiamate)


def test_ops_12_il_runner_non_registra_una_sessione_per_giro(client, mondo):
    """Il logout nel `finally`: dopo tre giri, zero sessioni vive."""
    import importlib

    runner = importlib.import_module("run_communication_dispatch_cron")
    c, _ = client
    config = runner.Config(base_url="", email=mondo["email"], password=PASSWORD,
                           channel="email", limit=10, timeout=(5.0, 60.0))

    for giro in range(3):
        accoda(mondo, chiave=f"ops-12-{giro}")
        runner.run_once(config, sessione=c)

    with mondo["cur"]() as cur:
        cur.execute("SELECT count(*) AS vive FROM operator_sessions "
                    "WHERE revoked_at IS NULL")
        assert cur.fetchone()["vive"] == 0, "una sessione per giro resta viva"


# ---------------------------------------------------------------------------
# L'ordine dei file non decide l'esito
# ---------------------------------------------------------------------------

def test_ops_13_un_override_lasciato_da_un_altro_modulo_non_decide_lagenzia(
        mondo, monkeypatch):
    """La collisione segnalata, riprodotta apposta e neutralizzata.

    `tests/test_p26_6c_backend_gate_closure.py` installa
    `main.app.dependency_overrides[legacy_basic_agency_context]` e non lo
    rimuove: in una suite intera quell'override decide l'agenzia al posto della
    sessione, e il dispatch trova zero righe - `claimed=0` con 200, cioe' un
    silenzio, non un rifiuto. Qui l'override viene installato A MANO, con
    un'agenzia che non esiste, prima che la fixture `client` entri in scena: se
    l'isolamento non ci fosse, questo test fallirebbe esattamente come
    fallivano ops_4, ops_5, ops_9 e ops_10 nella suite intera.
    """
    import tests.conftest  # noqa: F401
    import main as main_module
    from operator_auth.context import SystemAgencyContext

    agenzia_di_nessuno = mondo["a"] + 9_000_000
    prima = dict(main_module.app.dependency_overrides)
    main_module.app.dependency_overrides[main_module.legacy_basic_agency_context] = (
        lambda: SystemAgencyContext(agency_id=agenzia_di_nessuno,
                                    origin="communication_dispatch"))
    try:
        # La fixture `client` si prende la scena adesso, con l'override gia' li'.
        for c, inviate in _client_isolato(mondo, monkeypatch):
            m = accoda(mondo, chiave="ops-13")
            assert accedi(c, mondo).status_code == 204

            risposta = c.post("/api/communication/dispatch",
                              json={"channel": "email", "limit": 10})
            assert risposta.status_code == 200, risposta.text
            assert risposta.json()["claimed"] == 1, (
                "un override lasciato da un altro modulo ha deciso l'agenzia")
            assert riga(mondo, m["id"])["status"] == "sent"
            assert len(inviate) == 1
    finally:
        main_module.app.dependency_overrides.clear()
        main_module.app.dependency_overrides.update(prima)


# ---------------------------------------------------------------------------
# P29-2.6E blocker 1 - LA MATRICE DI AUTORIZZAZIONE DELLA ROTTA
#
# "Sessione valida" non e' la soglia. Un giro di dispatch svuota la coda di
# un'agenzia INTERA, su tutti i contatti: la riga della matrice P26-1 che lo
# descrive e' "See all agency records", YES per owner e admin, NO per agent.
# Se la rotta ammettesse `agent`, quella capacita' ce l'avrebbe ogni agente
# umano dell'agenzia - l'account dedicato al cron non c'entra niente.
# ---------------------------------------------------------------------------

def test_ops_14_un_agent_e_autenticato_ma_non_autorizzato(client, mondo):
    """403, non 401: la differenza e' la diagnosi che legge chi guarda i log."""
    c, inviate = client
    m = accoda(mondo, chiave="ops-14")

    assert accedi(c, mondo, "email_agent").status_code == 204
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10})

    assert risposta.status_code == 403, risposta.text
    # E non ha toccato la coda: il rifiuto e' PRIMA del claim.
    assert riga(mondo, m["id"])["status"] == "queued"
    assert inviate == []


@pytest.mark.parametrize("chiave", ["email", "email_owner"])
def test_ops_15_admin_e_owner_dispacciano(client, mondo, chiave):
    """`agency_admin` (il conto del cron) e `agency_owner`: 200, e parte."""
    c, inviate = client
    m = accoda(mondo, chiave=f"ops-15-{chiave}")

    assert accedi(c, mondo, chiave).status_code == 204
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10})

    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["sent"] == 1
    assert riga(mondo, m["id"])["status"] == "sent"
    assert len(inviate) == 1


def test_ops_16_il_ruolo_giusto_nellagenzia_sbagliata_non_apre_niente(client, mondo):
    """L'owner dell'agenzia B ha il ruolo che serve. Non basta, e non deve.

    Prende 200 - e' autorizzato, nella SUA agenzia - ma la coda che tocca e' la
    sua, che e' vuota. Il messaggio di A resta `queued` e nessuna email parte.
    Un 200 con `claimed=0` qui e' la risposta giusta: non esiste una richiesta
    con cui B possa nominare A.
    """
    c, inviate = client
    m = accoda(mondo, chiave="ops-16")

    assert accedi(c, mondo, "email_owner_b").status_code == 204
    risposta = c.post("/api/communication/dispatch",
                      json={"channel": "email", "limit": 10})

    assert risposta.status_code == 200, risposta.text
    assert risposta.json()["claimed"] == 0, "la coda di un'altra agenzia"
    assert riga(mondo, m["id"])["status"] == "queued"
    assert inviate == []


def test_ops_17_la_soglia_e_la_matrice_p26_1_non_una_lista_di_ruoli():
    """La dipendenza NON deve contenere un elenco di ruoli scritto a mano.

    Un elenco si sfalsa il giorno in cui la matrice cambia. Il confine deve
    passare da `permissions.sees_all_agency_records`, che e' la matrice.
    """
    import ast
    import inspect

    from communication import dependencies as comm_dependencies

    sorgente = inspect.getsource(comm_dependencies.require_dispatch_context)
    # Si guarda il CODICE, non il testo: le stringhe dei ruoli nei commenti e
    # nel docstring sono prosa, e una regex le confonderebbe con una regola.
    albero = ast.parse(inspect.cleandoc(sorgente))
    letterali = {n.value for n in ast.walk(albero)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for ruolo in ("agent", "agency_admin", "agency_owner"):
        assert ruolo not in letterali, (
            f"{ruolo!r} e' scritto a mano nella dipendenza invece di venire "
            "dalla matrice")

    assert "sees_all_agency_records" in sorgente


def test_ops_18_la_rotta_dichiara_la_dipendenza_autorizzante(client, mondo):
    """Il mount non deve poter tornare alla sola autenticazione per distrazione."""
    import inspect

    from communication import router as communication_router
    from communication.dependencies import require_dispatch_context

    firma = inspect.signature(communication_router.dispatch)
    assert firma.parameters["ctx"].default.dependency is require_dispatch_context

    sorgente = inspect.getsource(communication_router.dispatch)
    assert "legacy_basic_agency_context" not in sorgente, (
        "la rotta e' tornata alla sola autenticazione")


# ---------------------------------------------------------------------------
# P29-2.6E blocker 2 - SMTP CHE NON C'E', SULLA CATENA VERA
# ---------------------------------------------------------------------------

def test_ops_19_smtp_non_configurato_non_e_un_giro_riuscito(client, mondo, monkeypatch):
    """Il guasto piu' silenzioso che questo sistema possa avere.

    `database.invia_mail` non solleva: senza `SMTP_USER`/`SMTP_PASS` stampa e
    ritorna `False`. L'adapter lo traduce in `unknown` - e non in `rejected`,
    perche' `distinguishes_failure_class` e' False e "non lo so" non e' "e'
    stato rifiutato" - il dispatcher lo registra come `indeterminate`, la rotta
    risponde 200 e il ledger racconta tutto a chi lo guarda.

    Qui si pretende che il RUNNER non dica verde: il messaggio non e' `sent`, e
    l'uscita e' 2.
    """
    import importlib

    from communication.providers import email_smtp

    runner = importlib.import_module("run_communication_dispatch_cron")
    c, inviate = client

    # `invia_mail` come si comporta davvero quando l'SMTP non e' configurato:
    # nessuna eccezione, `False`.
    monkeypatch.setattr(email_smtp, "invia_mail",
                        lambda *args, **kwargs: False)

    m = accoda(mondo, chiave="ops-19")
    config = runner.Config(base_url="", email=mondo["email"], password=PASSWORD,
                           channel="email", limit=10, timeout=(5.0, 60.0))

    dati = runner.run_once(config, sessione=c)

    assert dati["claimed"] == 1
    assert dati["sent"] == 0, "dice di aver mandato qualcosa che non e' partito"
    assert dati["indeterminate"] == 1
    assert riga(mondo, m["id"])["status"] != "sent"
    assert inviate == []

    assert runner._application_failure(dati) is True
    # E il codice di uscita che ne segue: 2, non 0.
    monkeypatch.setattr(runner, "run_once", lambda config, **kw: dati)
    monkeypatch.setenv("COMMUNICATION_DISPATCH_BASE_URL", "https://esempio.it")
    monkeypatch.setenv("COMMUNICATION_DISPATCH_EMAIL", mondo["email"])
    monkeypatch.setenv("COMMUNICATION_DISPATCH_PASSWORD", PASSWORD)
    assert runner.main() == 2
