"""P29-2.3 - claim, fencing, tentativi e recovery, contro un PostgreSQL VERO.

Questo file esiste perche' NIENTE di cio' che prova si puo' provare con un
falso in memoria:

  * `FOR UPDATE SKIP LOCKED` e' un comportamento del lock manager. Un fake che
    lo simulasse proverebbe soltanto di essere stato scritto per passare.
  * il compare-and-set e' il `rowcount` di un UPDATE con quattro colonne nel
    WHERE: senza database non c'e' rowcount.
  * il trigger che vieta di riaprire un tentativo chiuso vive nella 064.
  * lo UNIQUE a tre colonne che ammette un solo risultato tardivo e' un indice.

I sette test obbligatori del design (§9.6, A-G) sono marcati nel nome.

COME SI ESEGUE

    P29_TEST_DSN='postgresql://utente@host:porta/db' python -m pytest \\
        tests/test_p29_2_3_claim_postgres.py

Senza `P29_TEST_DSN` l'intero modulo viene SALTATO. Nessuna connessione
automatica a nessun ambiente.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare P29-2.3",
)

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONE = ROOT / "migrations" / "064_p29_communication_foundation.sql"

from communication import repository, service  # noqa: E402
from communication.enums import (  # noqa: E402
    OUTCOME_ACCEPTED,
    OUTCOME_INDETERMINATE,
    OUTCOME_IN_PROGRESS,
    OUTCOME_REJECTED,
    STATUS_INDETERMINATE,
    STATUS_QUEUED,
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_SUPPRESSED,
)

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    assigned_agent_id BIGINT, status VARCHAR(20) NOT NULL DEFAULT 'active',
    CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id));
CREATE TABLE leads      (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE stime      (id SERIAL    PRIMARY KEY, agency_id BIGINT);
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
"""


class Ctx:
    def __init__(s, ag, uid=42, role="agency_admin"):
        s.agency_id, s.user_id, s.role, s.is_platform_admin = ag, uid, role, False

    def require_agency(s):
        return s.agency_id


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    """Il database usa-e-getta, e il modo di aprirci sopra piu' connessioni.

    Piu' connessioni sono il punto: due worker concorrenti non si simulano su
    una connessione sola, perche' un lock non blocca mai se stesso.
    """
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p29_2_3_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    principale = psycopg2.connect(_dsn_per(nome))
    aperte = [principale]
    try:
        with principale.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            cur.execute(MIGRAZIONE.read_text(encoding="utf-8"))
        principale.commit()

        def connessione():
            c = psycopg2.connect(_dsn_per(nome))
            aperte.append(c)
            return c

        yield {"conn": principale, "connessione": connessione}
    finally:
        for c in aperte:
            try:
                c.close()
            except Exception:
                pass
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def mondo(db):
    from psycopg2.extras import RealDictCursor

    conn = db["conn"]
    with conn.cursor() as cur:
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
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno'), ('a-due')")
        cur.execute("SELECT id FROM agencies ORDER BY id")
        a1, a2 = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a1,))
        c1 = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id) VALUES (%s) RETURNING id", (a2,))
        c2 = cur.fetchone()[0]
    conn.commit()
    return {
        "conn": conn, "connessione": db["connessione"],
        "a1": a1, "a2": a2, "c1": c1, "c2": c2,
        "ctx1": Ctx(a1), "ctx2": Ctx(a2),
        "cur": lambda: conn.cursor(cursor_factory=RealDictCursor),
    }


def accoda(mondo, *, chiave, ctx=None, contact_id=None, **override):
    parametri = dict(
        channel="email", communication_type="service", mode="automatic",
        reason_code="stima_pdf", rendered_body="corpo",
        destination_snapshot="a@b.it", subject_snapshot="ogg")
    parametri.update(override)
    with mondo["cur"]() as cur:
        esito = service.enqueue(ctx or mondo["ctx1"], cur=cur,
                                contact_id=contact_id or mondo["c1"],
                                idempotency_key=chiave, **parametri)
    mondo["conn"].commit()
    return esito["message"]


def reclama(mondo, *, limit=10, ctx=None):
    with mondo["cur"]() as cur:
        r = service.claim_due(ctx or mondo["ctx1"], provider="probe", channel="email",
                              limit=limit, cur=cur)
    mondo["conn"].commit()
    return r


def riga(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_messages WHERE id = %s", (message_id,))
        return cur.fetchone()


def tentativi(mondo, message_id):
    with mondo["cur"]() as cur:
        cur.execute("SELECT * FROM communication_attempts WHERE message_id = %s "
                    "ORDER BY attempt_no, late_result, id", (message_id,))
        return cur.fetchall()


def invecchia(mondo, message_id, secondi):
    """Sposta indietro `claimed_at`. Non c'e' altro modo di provare uno stale
    senza aspettare davvero quindici minuti."""
    with mondo["cur"]() as cur:
        cur.execute(
            "UPDATE communication_messages SET claimed_at = NOW() - make_interval(secs => %s) "
            "WHERE id = %s", (secondi, message_id))
    mondo["conn"].commit()


# ---------------------------------------------------------------------------
# A - due worker non reclamano la stessa riga
# ---------------------------------------------------------------------------

def test_A_due_worker_concorrenti_non_reclamano_lo_stesso_messaggio(mondo):
    """SKIP LOCKED, provato con due connessioni VERE e transazioni sovrapposte.

    Il secondo worker gira MENTRE il primo tiene ancora il lock: e' l'unico
    momento in cui `SKIP LOCKED` fa qualcosa. Se il primo avesse gia' committato,
    il secondo non troverebbe righe `queued` e il test passerebbe senza aver
    provato niente.
    """
    from psycopg2.extras import RealDictCursor

    ids = [accoda(mondo, chiave=f"A-{i}")["id"] for i in range(6)]

    conn_a = mondo["connessione"]()
    conn_b = mondo["connessione"]()
    try:
        with conn_a.cursor(cursor_factory=RealDictCursor) as ca:
            presi_a = service.claim_due(mondo["ctx1"], provider="A", channel="email", limit=3, cur=ca)
            # A NON ha ancora committato: i suoi tre sono bloccati.
            with conn_b.cursor(cursor_factory=RealDictCursor) as cb:
                presi_b = service.claim_due(mondo["ctx1"], provider="B", channel="email", limit=3, cur=cb)
            conn_b.commit()
        conn_a.commit()
    finally:
        conn_a.close()
        conn_b.close()

    id_a = {p["message"]["id"] for p in presi_a}
    id_b = {p["message"]["id"] for p in presi_b}
    assert id_a and id_b, (id_a, id_b)
    assert id_a.isdisjoint(id_b), f"stessa riga reclamata due volte: {id_a & id_b}"

    # Nessun messaggio ha due tentativi aperti.
    for message_id in ids:
        aperti = [t for t in tentativi(mondo, message_id)
                  if t["outcome"] == OUTCOME_IN_PROGRESS]
        assert len(aperti) <= 1, f"{message_id} ha {len(aperti)} tentativi aperti"


def test_A2_ogni_messaggio_ha_un_token_diverso(mondo):
    """Un token per messaggio, non per batch: con un token di batch un worker
    che perde UN messaggio per stale li perderebbe tutti."""
    for i in range(4):
        accoda(mondo, chiave=f"A2-{i}")
    presi = reclama(mondo)
    token = [p["message"]["claim_token"] for p in presi]
    assert len(set(token)) == len(token) == 4


# ---------------------------------------------------------------------------
# B - worker con token errato non finalizza
# ---------------------------------------------------------------------------

def test_B_un_token_sbagliato_non_finalizza(mondo):
    m = accoda(mondo, chiave="B")
    reclama(mondo)
    prima = riga(mondo, m["id"])

    with mondo["cur"]() as cur:
        esito = service.finalize_sent(mondo["ctx1"], m["id"],
                                      "99999999-9999-4999-8999-999999999999",
                                      cur=cur)
    mondo["conn"].commit()

    assert esito is None, "un token sbagliato ha finalizzato"
    dopo = riga(mondo, m["id"])
    assert dopo["status"] == STATUS_SENDING
    assert dopo["attempt_count"] == prima["attempt_count"]
    assert dopo["claim_token"] == prima["claim_token"]


# ---------------------------------------------------------------------------
# C - worker vecchio dopo la recovery non finalizza
# ---------------------------------------------------------------------------

def test_C_dopo_la_recovery_il_worker_originale_non_finalizza(mondo):
    """Il caso che C13 esiste per chiudere: A reclama, si blocca, la recovery
    chiude, A torna vivo con una risposta tardiva e prova a scrivere `sent`."""
    m = accoda(mondo, chiave="C")
    preso = reclama(mondo)[0]
    token = preso["message"]["claim_token"]
    attempt_no = preso["attempt"]["attempt_no"]

    invecchia(mondo, m["id"], 3600)
    with mondo["cur"]() as cur:
        recuperati = service.recover_stale(mondo["ctx1"], stale_after_seconds=900, cur=cur)
    mondo["conn"].commit()
    assert len(recuperati) == 1

    # A torna vivo.
    with mondo["cur"]() as cur:
        esito = service.finalize_sent(mondo["ctx1"], m["id"], token,
                                      provider_message_id="wamid.TARDIVO", cur=cur)
    mondo["conn"].commit()
    assert esito is None, "il worker sorpassato ha finalizzato"

    dopo = riga(mondo, m["id"])
    assert dopo["status"] == STATUS_INDETERMINATE
    assert dopo["provider_message_id"] is None, "il messaggio ha preso l'id di un esito tardivo"

    # La finalizzazione tardiva ha registrato da sola cio' che il worker ha
    # visto: una riga nuova, non una riscrittura, e senza che il chiamante
    # possa fabbricarla a mano (C18).
    righe = tentativi(mondo, m["id"])
    assert [(t["attempt_no"], t["outcome"], t["late_result"]) for t in righe] == [
        (attempt_no, OUTCOME_INDETERMINATE, False),
        (attempt_no, OUTCOME_ACCEPTED, True),
    ]
    assert riga(mondo, m["id"])["status"] == STATUS_INDETERMINATE, \
        "il risultato tardivo ha modificato il messaggio"


# ---------------------------------------------------------------------------
# D - il worker corretto finalizza, una volta sola
# ---------------------------------------------------------------------------

def test_D_il_worker_corretto_finalizza(mondo):
    m = accoda(mondo, chiave="D")
    preso = reclama(mondo)[0]
    token = preso["message"]["claim_token"]

    with mondo["cur"]() as cur:
        esito = service.finalize_sent(mondo["ctx1"], m["id"], token, provider_message_id="msg-1", cur=cur)
    mondo["conn"].commit()

    assert esito is not None
    assert esito["message"]["status"] == STATUS_SENT
    assert esito["message"]["sent_at"] is not None
    assert esito["message"]["claim_token"] is None, "il token non e' stato azzerato"
    assert esito["message"]["claimed_at"] is None
    # C15: il provider e' quello del CLAIM (`reclama` usa "probe"), non uno
    # passato alla finalizzazione - quel parametro non esiste piu'.
    assert esito["message"]["provider"] == "probe"
    assert esito["attempt"]["outcome"] == OUTCOME_ACCEPTED
    assert esito["attempt"]["finished_at"] is not None
    assert esito["attempt"]["failure_class"] is None


@pytest.mark.parametrize("finalizza,stato,esito_atteso,classe", [
    ("finalize_failed", "failed", OUTCOME_REJECTED, "definite"),
    ("finalize_indeterminate", "indeterminate", OUTCOME_INDETERMINATE, "indeterminate"),
])
def test_D2_le_altre_finalizzazioni(mondo, finalizza, stato, esito_atteso, classe):
    m = accoda(mondo, chiave=f"D2-{stato}")
    token = reclama(mondo)[0]["message"]["claim_token"]
    with mondo["cur"]() as cur:
        esito = getattr(service, finalizza)(
            mondo["ctx1"], m["id"], token, error_code="provider_unavailable", cur=cur)
    mondo["conn"].commit()
    assert esito["message"]["status"] == stato
    assert esito["message"]["failure_class"] == classe
    assert esito["message"]["claim_token"] is None
    assert esito["attempt"]["outcome"] == esito_atteso


def test_D3_suppressed_chiude_il_tentativo_senza_provider(mondo):
    """Il gate del consenso nega DOPO il claim: il tentativo e' gia' aperto e va
    chiuso. Non c'e' un provider perche' non e' stato chiamato nessuno."""
    m = accoda(mondo, chiave="D3")
    token = reclama(mondo)[0]["message"]["claim_token"]
    with mondo["cur"]() as cur:
        esito = service.finalize_suppressed(mondo["ctx1"], m["id"], token,
                                            reason="deny_revoked", cur=cur)
    mondo["conn"].commit()
    assert esito["message"]["status"] == STATUS_SUPPRESSED
    assert esito["message"]["suppressed_reason"] == "deny_revoked"
    assert esito["message"]["claim_token"] is None
    assert esito["attempt"]["outcome"] == OUTCOME_REJECTED
    assert esito["attempt"]["finished_at"] is not None, "il tentativo e' rimasto aperto"


# ---------------------------------------------------------------------------
# E - seconda finalizzazione: no-op controllato
# ---------------------------------------------------------------------------

def test_E_la_seconda_finalizzazione_e_un_no_op(mondo):
    """Il token azzerato rende il WHERE falso PER COSTRUZIONE: non serve un
    controllo applicativo, e non viene sollevata nessuna eccezione."""
    m = accoda(mondo, chiave="E")
    token = reclama(mondo)[0]["message"]["claim_token"]

    with mondo["cur"]() as cur:
        primo = service.finalize_sent(mondo["ctx1"], m["id"], token, provider_message_id="uno", cur=cur)
    mondo["conn"].commit()
    assert primo is not None
    prima = riga(mondo, m["id"])

    with mondo["cur"]() as cur:
        secondo = service.finalize_sent(mondo["ctx1"], m["id"], token, provider_message_id="due", cur=cur)
    mondo["conn"].commit()

    assert secondo is None
    dopo = riga(mondo, m["id"])
    assert dopo["provider_message_id"] == "uno" == prima["provider_message_id"]
    assert dopo["updated_at"] == prima["updated_at"], "qualcosa e' stato riscritto"


def test_E2_una_finalizzazione_diversa_dopo_la_prima_e_anchessa_no_op(mondo):
    m = accoda(mondo, chiave="E2")
    token = reclama(mondo)[0]["message"]["claim_token"]
    with mondo["cur"]() as cur:
        service.finalize_sent(mondo["ctx1"], m["id"], token, cur=cur)
    mondo["conn"].commit()
    with mondo["cur"]() as cur:
        assert service.finalize_failed(mondo["ctx1"], m["id"], token, error_code="timeout", cur=cur) is None
    mondo["conn"].commit()
    assert riga(mondo, m["id"])["status"] == STATUS_SENT


# ---------------------------------------------------------------------------
# G - un tentativo chiuso non si riapre (rifiutato dal TRIGGER)
# ---------------------------------------------------------------------------

def test_G_il_trigger_rifiuta_di_riaprire_un_tentativo_chiuso(mondo):
    import psycopg2

    m = accoda(mondo, chiave="G")
    token = reclama(mondo)[0]["message"]["claim_token"]
    with mondo["cur"]() as cur:
        service.finalize_sent(mondo["ctx1"], m["id"], token, cur=cur)
    mondo["conn"].commit()

    chiuso = tentativi(mondo, m["id"])[0]
    with pytest.raises(psycopg2.Error) as exc:
        with mondo["cur"]() as cur:
            cur.execute("UPDATE communication_attempts SET outcome = %s WHERE id = %s",
                        (OUTCOME_IN_PROGRESS, chiuso["id"]))
    mondo["conn"].rollback()
    assert "cannot be reopened" in str(exc.value), str(exc.value)


# ---------------------------------------------------------------------------
# Claim: coerenza fra messaggio e tentativo
# ---------------------------------------------------------------------------

def test_messaggio_e_tentativo_nascono_nello_stesso_commit(mondo):
    """Non deve esistere un commit osservabile in cui un messaggio e' `sending`
    e nessun tentativo lo racconta."""
    m = accoda(mondo, chiave="commit")
    conn_b = mondo["connessione"]()
    try:
        with mondo["cur"]() as cur:
            service.claim_due(mondo["ctx1"], provider="p", channel="email", cur=cur)
            # Da FUORI, prima del commit, il messaggio e' ancora in coda.
            with conn_b.cursor() as cb:
                cb.execute("SELECT status FROM communication_messages WHERE id = %s", (m["id"],))
                assert cb.fetchone()[0] == STATUS_QUEUED
                cb.execute("SELECT count(*) FROM communication_attempts WHERE message_id = %s",
                           (m["id"],))
                assert cb.fetchone()[0] == 0
            conn_b.commit()
        mondo["conn"].commit()
        # Dopo il commit ci sono entrambi.
        with conn_b.cursor() as cb:
            cb.execute("SELECT status FROM communication_messages WHERE id = %s", (m["id"],))
            assert cb.fetchone()[0] == STATUS_SENDING
            cb.execute("SELECT count(*) FROM communication_attempts WHERE message_id = %s",
                       (m["id"],))
            assert cb.fetchone()[0] == 1
    finally:
        conn_b.close()


def test_attempt_no_e_attempt_count_non_divergono(mondo):
    m = accoda(mondo, chiave="coerenza")
    preso = reclama(mondo)[0]
    assert preso["attempt"]["attempt_no"] == preso["message"]["attempt_count"] == 1
    assert preso["attempt"]["claim_token"] == preso["message"]["claim_token"]
    assert preso["attempt"]["outcome"] == OUTCOME_IN_PROGRESS
    assert preso["attempt"]["late_result"] is False
    assert preso["attempt"]["finished_at"] is None


def test_il_claim_rispetta_il_limite_del_batch(mondo):
    for i in range(7):
        accoda(mondo, chiave=f"batch-{i}")
    assert len(reclama(mondo, limit=3)) == 3
    assert len(reclama(mondo, limit=2)) == 2


def test_il_claim_e_deterministico_e_fifo(mondo):
    ids = [accoda(mondo, chiave=f"ord-{i}")["id"] for i in range(5)]
    presi = [p["message"]["id"] for p in reclama(mondo, limit=5)]
    assert presi == ids, "l'ordine non e' FIFO deterministico"


def test_il_claim_non_prende_i_messaggi_futuri(mondo):
    from datetime import timedelta
    accoda(mondo, chiave="futuro",
           scheduled_at=repository.utcnow() + timedelta(hours=1))
    accoda(mondo, chiave="adesso")
    presi = reclama(mondo)
    assert len(presi) == 1
    assert presi[0]["message"]["idempotency_key"] == "adesso"


@pytest.mark.parametrize("stato", ["cancelled", "sent", "suppressed"])
def test_il_claim_prende_solo_i_messaggi_in_coda(mondo, stato):
    m = accoda(mondo, chiave=f"stato-{stato}")
    with mondo["cur"]() as cur:
        cur.execute("UPDATE communication_messages SET status = %s WHERE id = %s",
                    (stato, m["id"]))
    mondo["conn"].commit()
    assert reclama(mondo) == []


# ---------------------------------------------------------------------------
# Stale recovery
# ---------------------------------------------------------------------------

def test_la_recovery_chiude_messaggio_e_tentativo(mondo):
    m = accoda(mondo, chiave="stale")
    preso = reclama(mondo)[0]
    invecchia(mondo, m["id"], 3600)

    with mondo["cur"]() as cur:
        recuperati = service.recover_stale(mondo["ctx1"], stale_after_seconds=900, cur=cur)
    mondo["conn"].commit()

    assert len(recuperati) == 1
    messaggio = riga(mondo, m["id"])
    assert messaggio["status"] == STATUS_INDETERMINATE
    assert messaggio["failure_class"] == "indeterminate"
    assert messaggio["error_code"] == "outcome_unknown"
    assert messaggio["claim_token"] is None and messaggio["claimed_at"] is None
    assert messaggio["attempt_count"] == preso["message"]["attempt_count"], \
        "la recovery ha toccato attempt_count"

    righe = tentativi(mondo, m["id"])
    assert len(righe) == 1, "la recovery ha creato un tentativo nuovo"
    assert righe[0]["outcome"] == OUTCOME_INDETERMINATE
    assert righe[0]["recovered_at"] is not None
    assert righe[0]["finished_at"] is not None


def test_la_recovery_non_tocca_un_claim_ancora_fresco(mondo):
    m = accoda(mondo, chiave="fresco")
    reclama(mondo)
    with mondo["cur"]() as cur:
        assert service.recover_stale(mondo["ctx1"], stale_after_seconds=900, cur=cur) == []
    mondo["conn"].commit()
    assert riga(mondo, m["id"])["status"] == STATUS_SENDING


def test_la_recovery_e_un_compare_and_set(mondo):
    """Fra la lettura del candidato e la scrittura, il worker originale puo'
    essere tornato vivo e aver finalizzato: in quel caso la recovery non fa
    nulla."""
    m = accoda(mondo, chiave="cas")
    token = reclama(mondo)[0]["message"]["claim_token"]
    invecchia(mondo, m["id"], 3600)

    with mondo["cur"]() as cur:
        candidati = repository.stale_candidates(cur, mondo["ctx1"],
                                                stale_after_seconds=900, limit=10)
    assert len(candidati) == 1

    # Il worker torna vivo e finalizza PRIMA che la recovery scriva.
    with mondo["cur"]() as cur:
        service.finalize_sent(mondo["ctx1"], m["id"], token, cur=cur)
    mondo["conn"].commit()

    with mondo["cur"]() as cur:
        esito = repository.recover_stale_message(
            cur, mondo["ctx1"], candidati[0]["id"], candidati[0]["claim_token"],
            stale_after_seconds=900)
    mondo["conn"].commit()

    assert esito is None, "la recovery ha sovrascritto una finalizzazione legittima"
    assert riga(mondo, m["id"])["status"] == STATUS_SENT


def test_dopo_la_recovery_il_messaggio_non_rientra_in_coda(mondo):
    """`indeterminate` e' terminale per l'automazione: nessun retry, con nessun
    `attempt_count`."""
    m = accoda(mondo, chiave="noretry")
    reclama(mondo)
    invecchia(mondo, m["id"], 3600)
    with mondo["cur"]() as cur:
        service.recover_stale(mondo["ctx1"], stale_after_seconds=900, cur=cur)
    mondo["conn"].commit()
    assert reclama(mondo) == [], "un messaggio indeterminate e' stato reclamato"


# ---------------------------------------------------------------------------
# Late result
# ---------------------------------------------------------------------------

def test_C17_un_secondo_risultato_tardivo_identico_non_rompe_il_batch(mondo):
    """Lo UNIQUE resta e resta l'autorita'; ma un secondo arrivo dello STESSO
    risultato non deve sollevare, perche' una `unique_violation` abortirebbe la
    transazione e con essa il resto del batch - proprio nel percorso che il
    design descrive come "registra, logga e prosegui"."""
    m = accoda(mondo, chiave="C17")
    token = reclama(mondo)[0]["message"]["claim_token"]
    invecchia(mondo, m["id"], 3600)
    with mondo["cur"]() as cur:
        service.recover_stale(mondo["ctx1"], stale_after_seconds=900, cur=cur)
    mondo["conn"].commit()

    # Il worker sorpassato torna, due volte.
    for _ in range(2):
        with mondo["cur"]() as cur:
            assert service.finalize_sent(mondo["ctx1"], m["id"], token,
                                         provider_message_id="wamid.X", cur=cur) is None
        mondo["conn"].commit()

    tardive = [t for t in tentativi(mondo, m["id"]) if t["late_result"]]
    assert len(tardive) == 1, f"righe tardive: {len(tardive)}"

    # La connessione e' sana: se la seconda avesse sollevato, questa query
    # fallirebbe con InFailedSqlTransaction.
    with mondo["cur"]() as cur:
        cur.execute("SELECT count(*) AS n FROM communication_messages")
        assert cur.fetchone()["n"] >= 1
    mondo["conn"].commit()


def test_C17b_lo_unique_a_tre_colonne_e_ancora_nel_catalogo(mondo):
    """`ON CONFLICT DO NOTHING` non indebolisce il vincolo: lo usa."""
    with mondo["cur"]() as cur:
        cur.execute("""
            SELECT array_length(conkey, 1) AS colonne FROM pg_constraint
             WHERE conname = 'communication_attempts_no_unq' AND contype = 'u'
        """)
        assert cur.fetchone()["colonne"] == 3


# ---------------------------------------------------------------------------
# C15 - il provider di un tentativo e' quello del suo claim
# ---------------------------------------------------------------------------

def test_C15_il_provider_viene_dal_claim_non_dal_chiamante(mondo):
    m = accoda(mondo, chiave="C15")
    with mondo["cur"]() as cur:
        preso = service.claim_due(mondo["ctx1"], provider="smtp", channel="email", cur=cur)[0]
    mondo["conn"].commit()
    token = preso["message"]["claim_token"]
    assert preso["attempt"]["provider"] == "smtp"

    with mondo["cur"]() as cur:
        esito = service.finalize_sent(mondo["ctx1"], m["id"], token,
                                      provider_message_id="msg-1", cur=cur)
    mondo["conn"].commit()

    assert esito["message"]["provider"] == "smtp"
    assert esito["attempt"]["provider"] == "smtp"


def test_C15b_la_finalizzazione_non_puo_dichiarare_un_altro_provider(mondo):
    """Non per un controllo, ma perche' il parametro NON ESISTE: l'API non
    offre il modo di sbagliare."""
    import inspect
    for nome in ("finalize_sent", "finalize_failed", "finalize_indeterminate",
                 "finalize_suppressed"):
        assert "provider" not in inspect.signature(getattr(service, nome)).parameters, nome


def test_C15c_un_token_senza_claim_non_finalizza_e_non_scrive(mondo):
    """Il tentativo regolare e' la prova del possesso. Senza, non c'e' niente da
    finalizzare - e nemmeno da registrare come tardivo (C16)."""
    m = accoda(mondo, chiave="C15c")
    reclama(mondo)
    prima = riga(mondo, m["id"])
    quante_prima = len(tentativi(mondo, m["id"]))

    with mondo["cur"]() as cur:
        assert service.finalize_sent(mondo["ctx1"], m["id"],
                                     "77777777-7777-4777-8777-777777777777",
                                     provider_message_id="msg-X", cur=cur) is None
    mondo["conn"].commit()

    assert riga(mondo, m["id"])["status"] == prima["status"]
    assert len(tentativi(mondo, m["id"])) == quante_prima, \
        "un token inventato ha prodotto una riga di audit"


# ---------------------------------------------------------------------------
# C16 - un risultato tardivo richiede un claim REALE
# ---------------------------------------------------------------------------

def test_C16a_token_reale_sorpassato_produce_il_tardivo(mondo):
    m = accoda(mondo, chiave="C16a")
    token = reclama(mondo)[0]["message"]["claim_token"]
    invecchia(mondo, m["id"], 3600)
    with mondo["cur"]() as cur:
        service.recover_stale(mondo["ctx1"], stale_after_seconds=900, cur=cur)
    mondo["conn"].commit()

    with mondo["cur"]() as cur:
        assert service.finalize_sent(mondo["ctx1"], m["id"], token,
                                     provider_message_id="w.1", cur=cur) is None
    mondo["conn"].commit()
    tardive = [t for t in tentativi(mondo, m["id"]) if t["late_result"]]
    assert len(tardive) == 1
    assert tardive[0]["provider_message_id"] == "w.1"
    assert tardive[0]["provider"] == "probe", "il provider non viene dal claim"


def test_C16b_un_token_inventato_non_produce_nessuna_riga(mondo):
    m = accoda(mondo, chiave="C16b")
    reclama(mondo)
    quante = len(tentativi(mondo, m["id"]))
    with mondo["cur"]() as cur:
        assert service.finalize_sent(mondo["ctx1"], m["id"],
                                     "12121212-1212-4121-8121-121212121212",
                                     cur=cur) is None
    mondo["conn"].commit()
    assert len(tentativi(mondo, m["id"])) == quante


def test_C16c_il_token_di_un_altro_messaggio_non_produce_nessuna_riga(mondo):
    uno = accoda(mondo, chiave="C16c-1")
    due = accoda(mondo, chiave="C16c-2")
    presi = {p["message"]["id"]: p for p in reclama(mondo)}
    token_di_uno = presi[uno["id"]]["message"]["claim_token"]
    quante = len(tentativi(mondo, due["id"]))

    with mondo["cur"]() as cur:
        assert service.finalize_sent(mondo["ctx1"], due["id"], token_di_uno,
                                     cur=cur) is None
    mondo["conn"].commit()
    assert len(tentativi(mondo, due["id"])) == quante
    assert riga(mondo, due["id"])["status"] == STATUS_SENDING


def test_C16d_il_token_di_unaltra_agenzia_non_produce_nessuna_riga(mondo):
    m = accoda(mondo, chiave="C16d")
    token = reclama(mondo)[0]["message"]["claim_token"]
    quante = len(tentativi(mondo, m["id"]))

    with mondo["cur"]() as cur:
        assert service.finalize_sent(mondo["ctx2"], m["id"], token, cur=cur) is None
    mondo["conn"].commit()
    assert len(tentativi(mondo, m["id"])) == quante
    assert riga(mondo, m["id"])["status"] == STATUS_SENDING


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

def test_un_worker_non_reclama_i_messaggi_di_unaltra_agenzia(mondo):
    accoda(mondo, chiave="mio")
    accoda(mondo, chiave="suo", ctx=mondo["ctx2"], contact_id=mondo["c2"])
    presi = reclama(mondo, ctx=mondo["ctx2"])
    assert len(presi) == 1
    assert presi[0]["message"]["agency_id"] == mondo["a2"]


def test_un_worker_non_finalizza_un_messaggio_di_unaltra_agenzia(mondo):
    m = accoda(mondo, chiave="fin-cross")
    token = reclama(mondo)[0]["message"]["claim_token"]
    with mondo["cur"]() as cur:
        assert service.finalize_sent(mondo["ctx2"], m["id"], token,
                                     cur=cur) is None
    mondo["conn"].commit()
    assert riga(mondo, m["id"])["status"] == STATUS_SENDING


def test_un_worker_non_recupera_gli_stale_di_unaltra_agenzia(mondo):
    m = accoda(mondo, chiave="rec-cross")
    reclama(mondo)
    invecchia(mondo, m["id"], 3600)
    with mondo["cur"]() as cur:
        assert service.recover_stale(mondo["ctx2"], stale_after_seconds=900, cur=cur) == []
    mondo["conn"].commit()
    assert riga(mondo, m["id"])["status"] == STATUS_SENDING


def test_i_tentativi_di_unaltra_agenzia_non_sono_leggibili(mondo):
    from communication.exceptions import NotFoundError

    m = accoda(mondo, chiave="att-cross")
    reclama(mondo)
    with mondo["cur"]() as cur:
        assert len(service.list_attempts(mondo["ctx1"], m["id"], cur=cur)) == 1
        with pytest.raises(NotFoundError):
            service.list_attempts(mondo["ctx2"], m["id"], cur=cur)
    mondo["conn"].rollback()
