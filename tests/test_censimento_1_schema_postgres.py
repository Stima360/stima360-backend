"""CENSIMENTO-1 / migration 083 su PostgreSQL vero.

Cosa prova, e come:

  * la 083 viene applicata DAL RUNNER (`scripts/p26_migrate.py apply`), non
    eseguendo il file a mano: banco con il registro vero (026 tramite
    `apply_baseline`, righe 027..082 registrate con i checksum reali dei file
    su disco, cosi' il piano del runner contiene SOLO la 083);
  * lo storico resta identico riga per riga (impronta di ogni riga prima/dopo
    sulle colonne preesistenti, conteggi per tipologia/stato, codici, stati,
    collegamenti), su una fixture che ricalca le proporzioni del Passo 0 su
    TEST (25 immobili: 23 apartment, 2 villa; 9 sold, 8 archived, 3 draft,
    2 under_offer, 2 active, 1 mandate; 1 codice IMM-, 4 codici NULL; 23
    senza via; 22 con mq; 3 con mq commerciali; 15 con proprietario; 12 con
    incarico; 1 con acquisizione) - dati SINTETICI, nessun dato personale;
  * i vincoli: stessa agenzia su edificio e pertinenza - verificata anche
    cambiando il lato GENITORE (agenzia dell'edificio, agenzia dell'unita'
    con pertinenze) - nessun auto-collegamento o ciclo, profondita' 1,
    pertinenza anche in un altro edificio, NESSUN UNIQUE su edifici ne' su
    scala/piano/interno, identita'
    catastale univoca solo quando completa (sezione NULL = sconosciuta ->
    nessun blocco; '' = assente -> entra nella chiave), normalizzazione;
  * la guardia censimento sui percorsi SQL consentiti e vietati, e il fatto
    che lo storico 'crm' continua a fare tutto cio' che faceva;
  * concorrenza: due connessioni che collegano A->B e B->A nello stesso
    istante, che creano insieme una catena di profondita' 2, o che cambiano
    l'agenzia di un edificio mentre vi si collega un'unita' - lo stato finale
    rispetta sempre le regole e nessuna delle due muore di deadlock; piu'
    l'unicita' della chiave di idempotenza sotto gara;
  * l'immutabilita' di chiave e impronta di idempotenza, imposta da un
    trigger (livello database), non dall'assenza di chi le modifica;
  * la down: consentita senza dati nuovi, bloccata quando li perderebbe
    (anche per un solo valore scritto su un immobile storico).

Gira solo con P29_TEST_DSN su un cluster locale (stessa guardia dei banchi
A30/CRM-OPS). Cluster locale usato per la certificazione: PostgreSQL 16;
TEST e' PostgreSQL 18 - nessuna sintassi usata e' specifica di versione.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

from tests.test_crm_ops_3_acquisitions_postgres import _dsn_locale

DSN = os.getenv("P29_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "083_censimento_1_buildings_units"
OPERATORE = "censimento.test"


def _dsn_per(nome):
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _runner():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate
    return p26_migrate


#: Lo scheletro che le FK della 002 e delle colonne successive richiedono.
SCHEMA_BASE = """
CREATE TABLE agencies (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE operator_users (id BIGSERIAL PRIMARY KEY, name TEXT);
CREATE TABLE agency_memberships (agency_id BIGINT REFERENCES agencies(id),
    operator_user_id BIGINT REFERENCES operator_users(id), UNIQUE (agency_id, operator_user_id));
CREATE TABLE contacts (id BIGSERIAL PRIMARY KEY, agency_id BIGINT NOT NULL REFERENCES agencies(id));
CREATE TABLE leads (id BIGSERIAL PRIMARY KEY, agency_id BIGINT NOT NULL REFERENCES agencies(id));
"""

#: Le colonne che le migration 034/036, 080, 081 e 082 hanno aggiunto davvero
#: (stesse definizioni), piu' gli scheletri delle tabelle collegate.
COLONNE_SUCCESSIVE = """
ALTER TABLE properties ADD COLUMN agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT;
ALTER TABLE properties ADD COLUMN region VARCHAR(50);
ALTER TABLE properties ADD COLUMN assigned_agent_id BIGINT;
ALTER TABLE properties ADD CONSTRAINT properties_agent_same_agency_fk
    FOREIGN KEY (agency_id, assigned_agent_id) REFERENCES agency_memberships (agency_id, operator_user_id);
ALTER TABLE properties ADD COLUMN acquisition_id BIGINT;   -- 081 (FK alle acquisizioni non sul banco)
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY, agency_id BIGINT NOT NULL REFERENCES agencies(id),
    property_id BIGINT REFERENCES properties(id) ON DELETE RESTRICT, description TEXT);
CREATE TABLE appointments (id BIGSERIAL PRIMARY KEY, agency_id BIGINT NOT NULL REFERENCES agencies(id),
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL);
"""

#: Le colonne di `properties` che esistevano PRIMA della 083: l'impronta di
#: ogni riga si calcola su queste, e deve essere identica dopo.
COLONNE_STORICHE = (
    "id", "code", "title", "property_type", "commercial_status", "classification", "address",
    "civic_number", "city", "province", "postal_code", "microzone", "latitude", "longitude",
    "surface_sqm", "commercial_surface_sqm", "rooms", "bedrooms", "bathrooms", "floor",
    "total_floors", "elevator", "year_built", "condition", "energy_class", "asking_price",
    "minimum_price", "mandate_type", "mandate_start", "mandate_end", "assigned_to", "source",
    "public_notes", "internal_notes", "metadata", "created_at", "updated_at", "archived_at",
    "agency_id", "region", "assigned_agent_id", "acquisition_id",
)


def _fixture_p0(cur):
    """25 immobili con le proporzioni del Passo 0 (dati sintetici)."""
    cur.execute("INSERT INTO agencies (name) VALUES ('Agenzia Uno'), ('Agenzia Due')")
    cur.execute("INSERT INTO operator_users (name) VALUES ('op1'), ('op2')")
    cur.execute("INSERT INTO agency_memberships VALUES (1, 1), (2, 2)")
    cur.execute("INSERT INTO contacts (agency_id) SELECT 1 FROM generate_series(1, 15)")
    cur.execute("INSERT INTO contacts (agency_id) VALUES (2)")
    stati = (["sold"] * 9 + ["archived"] * 8 + ["draft"] * 3 + ["under_offer"] * 2
             + ["active"] * 2 + ["mandate"])
    tipi = ["apartment"] * 23 + ["villa"] * 2
    for i, (stato, tipo) in enumerate(zip(stati, tipi), start=1):
        codice = "IMM-1" if i == 1 else (None if i <= 5 else f"ST-{i:03d}")
        via = f"Via Sintetica {i}" if i <= 2 else None          # 23 senza via
        mq = None if i <= 3 else 60 + i                           # 22 con mq
        mq_comm = 70 + i if i in (4, 5, 6) else None              # 3 con mq commerciali
        incarico = "esclusiva" if i <= 12 else None               # 12 con incarico
        cur.execute(
            """INSERT INTO properties (code, title, property_type, commercial_status, address, city,
                                       surface_sqm, commercial_surface_sqm, mandate_type,
                                       archived_at, agency_id, acquisition_id)
               VALUES (%s, %s, %s, %s, %s, 'Comune Sintetico', %s, %s, %s,
                       CASE WHEN %s = 'archived' THEN NOW() END, 1, %s)""",
            (codice, f"Immobile {i}", tipo, stato, via, mq, mq_comm, incarico, stato,
             7 if i == 7 else None))
    # 15 con proprietario (owner/seller), 10 senza contatti
    for i in range(1, 16):
        cur.execute("INSERT INTO property_contacts (property_id, contact_id, role, is_primary) "
                    "VALUES (%s, %s, 'owner', TRUE)", (i, i))
    cur.execute("INSERT INTO activities (agency_id, property_id, description) VALUES (1, 1, 'nota')")
    cur.execute("INSERT INTO property_photos (property_id, url) SELECT id, 'x' FROM properties WHERE id <= 8")
    cur.execute("INSERT INTO property_documents (property_id, document_type, title, url) "
                "SELECT id, 'd', 't', 'u' FROM properties WHERE id <= 9")
    cur.execute("INSERT INTO property_visits (property_id, scheduled_at) SELECT id, NOW() FROM properties WHERE id <= 4")
    cur.execute("INSERT INTO appointments (agency_id, property_id) VALUES (1, 1), (1, 2)")


def _impronte(cur):
    cols = ", ".join(COLONNE_STORICHE)
    cur.execute(f"SELECT id, md5(row_to_json(t)::text) FROM (SELECT {cols} FROM properties ORDER BY id) t")
    return dict(cur.fetchall())


def _conteggi(cur):
    out = {}
    cur.execute("SELECT property_type, count(*) FROM properties GROUP BY 1")
    out["tipi"] = dict(cur.fetchall())
    cur.execute("SELECT commercial_status, count(*) FROM properties GROUP BY 1")
    out["stati"] = dict(cur.fetchall())
    cur.execute("SELECT count(*) FILTER (WHERE code LIKE 'IMM-%'), count(*) FILTER (WHERE code IS NULL), "
                "count(*) FILTER (WHERE address IS NULL), count(*) FILTER (WHERE surface_sqm IS NOT NULL), "
                "count(*) FILTER (WHERE mandate_type IS NOT NULL), count(*) FILTER (WHERE acquisition_id IS NOT NULL) "
                "FROM properties")
    out["campi"] = cur.fetchone()
    for tabella in ("property_contacts", "activities", "property_photos", "property_documents",
                    "property_visits", "appointments"):
        cur.execute(f"SELECT count(*) FROM {tabella}")
        out[tabella] = cur.fetchone()[0]
    return out


@pytest.fixture(scope="module")
def banco():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor
    _dsn_locale(DSN)
    runner = _runner()
    nome = f"censimento1_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"   # 'test' nel nome: il runner lo accetta
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_BASE)
            cur.execute((MIGRAZIONI / "002_property_01.sql").read_text(encoding="utf-8"))
            cur.execute(COLONNE_SUCCESSIVE)
            _fixture_p0(cur)
        # Il registro VERO: la 026 dal percorso del runner, poi 027..082
        # registrate con i checksum reali dei file su disco (lo schema che
        # servono e' gia' sul banco): il piano del runner conterra' solo la 083.
        tutte = runner.discover_migrations()
        conn.autocommit = False
        with conn.cursor() as cur:
            args = argparse.Namespace(
                baseline_version=runner.BASELINE_VERSION_LABEL,
                baseline_fingerprint="0" * 64,
                baseline_artifact="reports/p26_baseline_TEST_20260905T170601Z.json")
            runner.apply_baseline(cur, tutte[0], OPERATORE, args)
            for m in tutte[1:]:
                if m.version == VERSIONE:
                    continue
                runner.register(cur, m, OPERATORE, 0)
        conn.commit()
        conn.autocommit = True
        yield {"conn": conn, "dsn": dsn, "nome": nome, "psycopg2": psycopg2}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _env_runner(monkeypatch, banco):
    from urllib.parse import parse_qs, urlparse
    p = urlparse(banco["dsn"])
    host = p.hostname or (parse_qs(p.query).get("host") or [""])[0]
    monkeypatch.setenv("DB_HOST", host)
    monkeypatch.setenv("DB_PORT", str(p.port or 5432))
    monkeypatch.setenv("DB_USER", p.username or getpass.getuser())   # peer auth sul socket locale
    monkeypatch.setenv("DB_PASSWORD", p.password or "")
    monkeypatch.setenv("DB_NAME", banco["nome"])


def _errore(banco, sql, params=None):
    """Esegue in una transazione a parte e restituisce il messaggio d'errore (o None)."""
    psycopg2 = banco["psycopg2"]
    conn = psycopg2.connect(banco["dsn"])
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, params)
                conn.commit()
                return None
            except psycopg2.Error as exc:
                conn.rollback()
                return str(exc)
    finally:
        conn.close()


def _q(banco, sql, params=None):
    with banco["conn"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _fp(testo):
    return hashlib.sha256(testo.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 1. Applicazione tramite il runner e preservazione dello storico
# ---------------------------------------------------------------------------

def test_01_il_runner_applica_solo_la_083_e_lo_storico_resta_identico(banco, monkeypatch, capsys):
    runner = _runner()
    with banco["conn"].cursor() as cur:
        prima_impronte, prima_conteggi = _impronte(cur), _conteggi(cur)
        cur.execute("SELECT count(*) FROM schema_migrations")
        righe_prima = cur.fetchone()[0]
    assert len(prima_impronte) == 25 and prima_conteggi["tipi"] == {"apartment": 23, "villa": 2}

    _env_runner(monkeypatch, banco)
    args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {"operator": OPERATORE}))
    assert runner.command_plan(args) == 0
    piano = capsys.readouterr().out
    assert VERSIONE in piano and "static validation: OK" in piano
    assert runner.command_status(args) == 0
    stato = capsys.readouterr().out
    assert f"pending      {VERSIONE}" in stato and "PROBLEM" not in stato and stato.count("pending") == 1
    assert runner.command_apply(args) == 0
    uscita = capsys.readouterr().out
    assert f"applied {VERSIONE}" in uscita and uscita.count("applied ") == 1

    with banco["conn"].cursor() as cur:
        assert _impronte(cur) == prima_impronte
        assert _conteggi(cur) == prima_conteggi
        cur.execute("SELECT count(*) FROM schema_migrations")
        assert cur.fetchone()[0] == righe_prima + 1
        cur.execute("SELECT checksum_up, rolled_back_at, applied_by_operator FROM schema_migrations WHERE version = %s",
                    (VERSIONE,))
        riga = cur.fetchone()
        m = [x for x in runner.discover_migrations() if x.version == VERSIONE][0]
        assert riga[0] == m.checksum_up and riga[1] is None and riga[2] == OPERATORE
        # lo storico: tutto 'crm', nessun edificio, nessuna categoria, codici intatti
        cur.execute("SELECT count(*) FILTER (WHERE record_kind = 'crm'), count(*) FILTER (WHERE building_id IS NULL), "
                    "count(*) FILTER (WHERE cadastral_category IS NULL), count(*) FILTER (WHERE NOT whole_building), "
                    "count(*) FILTER (WHERE NOT address_inherited) FROM properties")
        assert tuple(cur.fetchone()) == (25, 25, 25, 25, 25)
    # un secondo apply non ha nulla da fare
    assert runner.command_apply(args) == 0
    assert "nothing to apply" in capsys.readouterr().out


def test_02_lo_storico_crm_continua_a_fare_tutto(banco):
    """Le righe storiche non ricevono restrizioni nuove: stati, incarico,
    acquisizione, archiviazione, modifica dell'indirizzo come prima."""
    assert _errore(banco, "UPDATE properties SET commercial_status = 'active' WHERE id = 3") is None
    assert _errore(banco, "UPDATE properties SET mandate_type = 'esclusiva', mandate_start = CURRENT_DATE WHERE id = 20") is None
    assert _errore(banco, "UPDATE properties SET acquisition_id = 9 WHERE id = 21") is None
    assert _errore(banco, "UPDATE properties SET commercial_status = 'archived', archived_at = NOW() WHERE id = 22") is None
    assert _errore(banco, "UPDATE properties SET address = 'Via Nuova', civic_number = '1' WHERE id = 23") is None
    assert _errore(banco, "INSERT INTO properties (title, agency_id) VALUES ('storico nuovo', 1)") is None
    assert _q(banco, "SELECT record_kind FROM properties WHERE title = 'storico nuovo'")[0][0] == "crm"


# ---------------------------------------------------------------------------
# 2. Collegamenti: stessa agenzia, nessun ciclo, profondita' 1
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def edifici(banco):
    _q(banco, "INSERT INTO buildings (agency_id, name, city, address, civic_number) VALUES "
              "(1, 'Palazzina via Roma 10', 'Alba Adriatica', 'Via Roma', '10'), "
              "(1, 'Palazzina di fronte', 'Alba Adriatica', 'Via Roma', '11'), "
              "(2, 'Edificio altrui', 'Tortoreto', 'Via Mare', '1')")
    return {r[1]: r[0] for r in _q(banco, "SELECT id, name FROM buildings")}


def _unita(banco, **kw):
    kw.setdefault("agency_id", 1)
    kw.setdefault("record_kind", "census")
    kw.setdefault("title", "unita'")
    cols = ", ".join(kw)
    return _q(banco, f"INSERT INTO properties ({cols}) VALUES ({', '.join(['%s'] * len(kw))}) RETURNING id",
              list(kw.values()))[0][0]


def test_03_edificio_e_pertinenza_solo_nella_stessa_agenzia(banco, edifici):
    assert "tenancy" in _errore(banco, "INSERT INTO properties (title, agency_id, building_id) VALUES ('x', 1, %s)",
                                (edifici["Edificio altrui"],))
    principale = _unita(banco, building_id=edifici["Palazzina via Roma 10"], floor="2", internal_number="2")
    assert "tenancy" in _errore(banco, "INSERT INTO properties (title, agency_id, parent_property_id) VALUES ('g', 2, %s)",
                                (principale,))
    # la pertinenza puo' stare in un ALTRO edificio della stessa agenzia
    garage = _unita(banco, property_type="garage", parent_property_id=principale,
                    building_id=edifici["Palazzina di fronte"])
    assert _q(banco, "SELECT building_id FROM properties WHERE id = %s", (garage,))[0][0] == edifici["Palazzina di fronte"]


def test_04_nessun_auto_collegamento_ciclo_o_profondita_2(banco, edifici):
    a = _unita(banco, title="A")
    b = _unita(banco, title="B", parent_property_id=a)
    c = _unita(banco, title="C")
    assert "parent_not_self" in _errore(banco, "UPDATE properties SET parent_property_id = id WHERE id = %s", (c,))
    # profondita' 2: C sotto B (che e' gia' pertinenza)
    assert "cannot be a parent" in _errore(banco, "INSERT INTO properties (title, agency_id, parent_property_id) VALUES ('D', 1, %s)", (b,))
    # ciclo: A (che ha B) non puo' diventare pertinenza di B (rifiutato gia' perche' B e' una pertinenza)
    assert "cannot be a parent" in _errore(banco, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (b, a))
    # ne' pertinenza di un'altra unita': chi ha pertinenze resta un'unita' principale
    e = _unita(banco, title="E")
    assert "cannot become a pertinenza" in _errore(banco, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (e, a))
    # scollegare e ricollegare altrove e' libero
    assert _errore(banco, "UPDATE properties SET parent_property_id = NULL WHERE id = %s", (b,)) is None


def test_04b_il_lato_genitore_non_cambia_agenzia_se_ha_collegamenti(banco, edifici):
    """Edificio con unita' collegate e unita' con pertinenze: l'agenzia non si
    cambia (le righe collegate resterebbero nell'agenzia di prima)."""
    b = edifici["Palazzina via Roma 10"]
    assert "has linked properties and cannot change agency" in _errore(
        banco, "UPDATE buildings SET agency_id = 2 WHERE id = %s", (b,))
    # un edificio SENZA unita' collegate cambia agenzia liberamente
    vuoto = _q(banco, "INSERT INTO buildings (agency_id, city) VALUES (1, 'Vuoto') RETURNING id")[0][0]
    assert _errore(banco, "UPDATE buildings SET agency_id = 2 WHERE id = %s", (vuoto,)) is None
    # unita' principale con pertinenza: non cambia agenzia; scollegata la pertinenza, si'
    p = _unita(banco, title="P")
    c = _unita(banco, title="C-di-P", parent_property_id=p)
    assert "has linked pertinenze and cannot change agency" in _errore(
        banco, "UPDATE properties SET agency_id = 2 WHERE id = %s", (p,))
    # ne' la pertinenza puo' andarsene da sola in un'altra agenzia
    assert "tenancy" in _errore(banco, "UPDATE properties SET agency_id = 2 WHERE id = %s", (c,))
    # un'unita' collegata a un edificio non cambia agenzia (l'edificio resterebbe nell'altra)
    u = _unita(banco, building_id=b)
    assert "tenancy" in _errore(banco, "UPDATE properties SET agency_id = 2 WHERE id = %s", (u,))
    assert _errore(banco, "UPDATE properties SET parent_property_id = NULL WHERE id = %s", (c,)) is None
    assert _errore(banco, "UPDATE properties SET agency_id = 2 WHERE id = %s", (p,)) is None


def _gara(banco, azioni, ripetizioni=4):
    """Esegue le `azioni` (callable(cur)) in connessioni separate, sincronizzate
    da una barriera, ciascuna nella propria transazione. Restituisce la lista
    degli esiti per ripetizione: 'ok' oppure lo SQLSTATE dell'errore."""
    psycopg2 = banco["psycopg2"]
    tutti = []
    for _ in range(ripetizioni):
        esiti = [None] * len(azioni)
        pronti = threading.Barrier(len(azioni))

        def corri(i, azione):
            conn = psycopg2.connect(banco["dsn"])
            try:
                with conn.cursor() as cur:
                    pronti.wait(timeout=10)
                    try:
                        azione(cur)
                        conn.commit()
                        esiti[i] = "ok"
                    except psycopg2.Error as exc:
                        conn.rollback()
                        esiti[i] = exc.pgcode
            finally:
                conn.close()

        fili = [threading.Thread(target=corri, args=(i, a)) for i, a in enumerate(azioni)]
        for f in fili: f.start()
        for f in fili: f.join(timeout=30)
        tutti.append(esiti)
    return tutti


def test_04c_concorrenza_ciclo_a_b_b_a(banco):
    """Due transazioni collegano A->B e B->A nello stesso istante: una sola
    passa, nessun deadlock (40P01), nessun ciclo nello stato finale."""
    for _ in range(4):
        a = _unita(banco, title="ciclo-A"); b = _unita(banco, title="ciclo-B")
        [esiti] = _gara(banco, [
            lambda cur: cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", (b, a)),
            lambda cur: cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", (a, b)),
        ], ripetizioni=1)
        assert sorted(esiti) == ["23514", "ok"], esiti          # una passa, l'altra e' check_violation
        pa, pb = _q(banco, "SELECT (SELECT parent_property_id FROM properties WHERE id = %s), "
                           "(SELECT parent_property_id FROM properties WHERE id = %s)", (a, b))[0]
        assert not (pa == b and pb == a) and (pa == b) != (pb == a)


def test_04d_concorrenza_catena_di_profondita_2(banco):
    """B->A e C->B insieme: una sola passa; mai una catena C->B->A."""
    for _ in range(4):
        a = _unita(banco, title="cat-A"); b = _unita(banco, title="cat-B"); c = _unita(banco, title="cat-C")
        [esiti] = _gara(banco, [
            lambda cur: cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", (a, b)),
            lambda cur: cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", (b, c)),
        ], ripetizioni=1)
        assert sorted(esiti) == ["23514", "ok"], esiti
        assert _q(banco, "SELECT count(*) FROM properties f JOIN properties g ON g.id = f.parent_property_id "
                         "WHERE g.parent_property_id IS NOT NULL")[0][0] == 0


def test_04e_concorrenza_agenzia_edificio_e_collegamento(banco):
    """L'edificio cambia agenzia mentre un'unita' vi si collega: mai un'unita'
    collegata a un edificio di un'altra agenzia."""
    for _ in range(4):
        e = _q(banco, "INSERT INTO buildings (agency_id, city) VALUES (1, 'gara') RETURNING id")[0][0]
        [esiti] = _gara(banco, [
            lambda cur: cur.execute("UPDATE buildings SET agency_id = 2 WHERE id = %s", (e,)),
            lambda cur: cur.execute("INSERT INTO properties (title, agency_id, record_kind, building_id) "
                                    "VALUES ('u', 1, 'census', %s)", (e,)),
        ], ripetizioni=1)
        assert "40P01" not in esiti and esiti.count("ok") >= 1, esiti
        assert _q(banco, "SELECT count(*) FROM properties p JOIN buildings b ON b.id = p.building_id "
                         "WHERE p.agency_id <> b.agency_id")[0][0] == 0


def test_04f_repeatable_read_con_snapshot_gia_preso_rifiuto_esplicito(banco):
    """Gli advisory lock NON rinnovano lo snapshot: in REPEATABLE READ le letture
    del trigger sarebbero quelle di prima dell'attesa. Percio' un collegamento,
    o un cambio di agenzia, fuori da READ COMMITTED e' rifiutato con SQLSTATE
    25000 - deterministicamente, con due transazioni REPEATABLE READ che hanno
    GIA' preso lo snapshot. Letture, inserimenti e modifiche estranee ai
    collegamenti restano permessi a qualunque isolamento."""
    psycopg2 = banco["psycopg2"]
    a = _unita(banco, title="rr-A"); b = _unita(banco, title="rr-B"); c = _unita(banco, title="rr-C")
    e = _q(banco, "INSERT INTO buildings (agency_id, city) VALUES (1, 'rr') RETURNING id")[0][0]
    t1 = psycopg2.connect(banco["dsn"]); t2 = psycopg2.connect(banco["dsn"])
    try:
        for t in (t1, t2):
            t.set_session(isolation_level="REPEATABLE READ")
            with t.cursor() as cur:
                cur.execute("SELECT count(*) FROM properties")      # snapshot acquisito QUI
                cur.fetchone()

        def rifiutato(conn, sql, params):
            with conn.cursor() as cur:
                try:
                    cur.execute(sql, params)
                except psycopg2.Error as exc:
                    conn.rollback()          # la transazione e' abortita: si riparte con uno snapshot nuovo
                    assert exc.pgcode == "25000" and "READ COMMITTED" in str(exc), (exc.pgcode, str(exc))
                    with conn.cursor() as c2:
                        c2.execute("SELECT 1"); c2.fetchone()   # nuovo snapshot REPEATABLE READ
                    return
            raise AssertionError("doveva essere rifiutato")

        # ciclo A->B / B->A: rifiutati ENTRAMBI, prima ancora di tentare
        rifiutato(t1, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (b, a))
        rifiutato(t2, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (a, b))
        # profondita' (C sotto B), collegamento a edificio, insert gia' collegata
        rifiutato(t1, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (b, c))
        rifiutato(t2, "UPDATE properties SET building_id = %s WHERE id = %s", (e, c))
        rifiutato(t1, "INSERT INTO properties (title, agency_id, parent_property_id) VALUES ('rr-D', 1, %s)", (a,))
        # separazione fra agenzie: cambio agenzia di unita' e di edificio
        rifiutato(t2, "UPDATE properties SET agency_id = 2 WHERE id = %s", (c,))
        rifiutato(t1, "UPDATE buildings SET agency_id = 2 WHERE id = %s", (e,))
        # ...mentre il resto resta permesso in REPEATABLE READ
        with t2.cursor() as cur:
            cur.execute("UPDATE properties SET title = 'rr-rinominata', surface_sqm = 70 WHERE id = %s", (a,))
            cur.execute("INSERT INTO properties (title, agency_id, record_kind) VALUES ('rr-libera', 1, 'census') RETURNING id")
            nuova = cur.fetchone()[0]
            cur.execute("UPDATE buildings SET name = 'rr-rinominato', units_declared = 4 WHERE id = %s", (e,))
            cur.execute("INSERT INTO property_accessories (property_id, kind) VALUES (%s, 'cantina')", (nuova,))
            cur.execute("SELECT count(*) FROM properties WHERE id IN (%s, %s, %s)", (a, b, c)); assert cur.fetchone()[0] == 3
        t2.commit()
        with t1.cursor() as cur:
            cur.execute("SELECT title FROM properties WHERE id = %s", (a,))
        t1.commit()
    finally:
        t1.close(); t2.close()
    # nessun collegamento e' passato; le modifiche estranee si'
    assert _q(banco, "SELECT parent_property_id, building_id, agency_id, title FROM properties WHERE id = %s", (a,))[0] == [None, None, 1, "rr-rinominata"]
    assert _q(banco, "SELECT parent_property_id FROM properties WHERE id IN (%s, %s) AND parent_property_id IS NOT NULL", (b, c)) == []
    assert _q(banco, "SELECT agency_id, name FROM buildings WHERE id = %s", (e,))[0] == [1, "rr-rinominato"]
    # e in READ COMMITTED lo stesso collegamento passa
    assert _errore(banco, "UPDATE properties SET parent_property_id = %s WHERE id = %s", (b, a)) is None


def test_04g_deadlock_fra_transazioni_con_piu_collegamenti_rollback_e_retry(banco):
    """L'ordine canonico dei lock vale nella SINGOLA istruzione. Due transazioni
    che fanno PIU' collegamenti in ordine speculare possono andare in
    deadlock: PostgreSQL ne abortisce una (40P01), lo stato resta coerente e
    il chiamante ritenta. Qui lo si provoca in modo deterministico con due
    barriere e si verifica il rimedio: rollback + retry."""
    psycopg2 = banco["psycopg2"]
    p1, p2 = _unita(banco, title="dl-P1"), _unita(banco, title="dl-P2")
    x1, x2 = _unita(banco, title="dl-X1"), _unita(banco, title="dl-X2")
    y1, y2 = _unita(banco, title="dl-Y1"), _unita(banco, title="dl-Y2")
    esiti, dopo_prima, dopo_seconda = {}, threading.Barrier(2), threading.Barrier(2)

    def transazione(nome, prima, seconda):
        conn = psycopg2.connect(banco["dsn"])
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", prima)   # lock sui nodi di `prima`
                dopo_prima.wait(timeout=10)
                try:
                    cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", seconda)  # vuole i nodi dell'altra
                    conn.commit()
                    esiti[nome] = "ok"
                except psycopg2.Error as exc:
                    conn.rollback()
                    esiti[nome] = exc.pgcode
        finally:
            conn.close()

    t = [threading.Thread(target=transazione, args=("T1", (p1, x1), (p2, y2))),
         threading.Thread(target=transazione, args=("T2", (p2, x2), (p1, y1)))]
    for f in t: f.start()
    for f in t: f.join(timeout=60)
    assert sorted(esiti.values()) == ["40P01", "ok"], esiti        # una muore di deadlock, l'altra passa
    vittima = [k for k, v in esiti.items() if v == "40P01"][0]
    # stato coerente: solo i collegamenti della transazione sopravvissuta
    collegate = {r[0] for r in _q(banco, "SELECT id FROM properties WHERE parent_property_id IS NOT NULL AND id IN (%s,%s,%s,%s)", (x1, x2, y1, y2))}
    assert collegate == ({x1, y2} if vittima == "T2" else {x2, y1})
    # retry della vittima: l'INTERA transazione viene rieseguita su una connessione nuova,
    # entrambi gli UPDATE, un solo commit finale, rollback se qualcosa fallisce (mai mezzo retry)
    prima, seconda = ((p2, x2), (p1, y1)) if vittima == "T2" else ((p1, x1), (p2, y2))
    retry = psycopg2.connect(banco["dsn"])
    try:
        with retry.cursor() as cur:
            cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", prima)
            cur.execute("UPDATE properties SET parent_property_id = %s WHERE id = %s", seconda)
        retry.commit()
    except psycopg2.Error:
        retry.rollback()
        raise
    finally:
        retry.close()
    assert _q(banco, "SELECT count(*) FROM properties WHERE parent_property_id IS NOT NULL AND id IN (%s,%s,%s,%s)", (x1, x2, y1, y2))[0][0] == 4


# ---------------------------------------------------------------------------
# 3. Identita' catastale, normalizzazione, nessun UNIQUE su edifici/interni
# ---------------------------------------------------------------------------

def test_05_identita_catastale_univoca_solo_se_completa(banco):
    base = dict(cadastral_municipality_code="a125", cadastral_sheet="0012", cadastral_parcel="345")
    # sezione NULL = sconosciuta: due unita' uguali NON si bloccano (sara' un avviso applicativo)
    _unita(banco, cadastral_subunit="4", **base)
    _unita(banco, cadastral_subunit="04", **base)
    # sezione '' = assente, dichiarata: la chiave e' completa e blocca il doppione
    _unita(banco, cadastral_section="", cadastral_subunit="5", **base)
    err = _errore(banco, "INSERT INTO properties (title, agency_id, record_kind, cadastral_municipality_code, "
                         "cadastral_section, cadastral_sheet, cadastral_parcel, cadastral_subunit) "
                         "VALUES ('dup', 1, 'census', 'A125', ' ', '12', '0345', '5')")
    assert err and "uq_properties_cadastral_identity" in err
    # stessa identita' in un'ALTRA agenzia: ammessa (isolamento)
    assert _errore(banco, "INSERT INTO properties (title, agency_id, cadastral_municipality_code, cadastral_section, "
                          "cadastral_sheet, cadastral_parcel, cadastral_subunit) VALUES ('altrui', 2, 'A125', '', '12', '345', '5')") is None
    # incompleta (manca la particella): mai un blocco
    _unita(banco, cadastral_section="", cadastral_subunit="5", cadastral_municipality_code="A125", cadastral_sheet="12")
    _unita(banco, cadastral_section="", cadastral_subunit="5", cadastral_municipality_code="A125", cadastral_sheet="12")


def test_06_normalizzazione_e_forme_canoniche(banco):
    # Belfiore e' CHAR(4): spazi attorno li toglie l'API (qui un valore lungo fallirebbe forte, non in silenzio)
    i = _unita(banco, cadastral_municipality_code="a125", cadastral_section=" ", cadastral_sheet=" 0012 ",
               cadastral_parcel="00345", cadastral_subunit="A 7", cadastral_category="c/6",
               staircase="  ", internal_number=" 3 ")
    riga = _q(banco, "SELECT cadastral_municipality_code, cadastral_section, cadastral_sheet, cadastral_parcel, "
                     "cadastral_subunit, cadastral_category, staircase, internal_number FROM properties WHERE id = %s", (i,))[0]
    assert tuple(riga) == ("A125", "", "12", "345", "A7", "C/6", None, "3")
    # vuoto nei campi non-sezione = NULL (non conosciuto), non ''
    j = _unita(banco, cadastral_sheet="", cadastral_subunit="   ")
    assert tuple(_q(banco, "SELECT cadastral_sheet, cadastral_subunit, cadastral_section FROM properties WHERE id = %s", (j,))[0]) == (None, None, None)


def test_07_la_categoria_e_un_codice_reale_mai_da_verificare(banco):
    # "Da verificare" non entra: troppo lungo per VARCHAR(5) (errore forte) e comunque fuori formato
    assert _errore(banco, "INSERT INTO properties (title, agency_id, cadastral_category) VALUES ('x', 1, 'Da verificare')") is not None
    for sbagliato in ("DAVER", "G/1", "A2", "A/"):
        assert "cadastral_category_chk" in _errore(banco, "INSERT INTO properties (title, agency_id, cadastral_category) VALUES ('x', 1, %s)", (sbagliato,))
    assert "belfiore_chk" in _errore(banco, "INSERT INTO properties (title, agency_id, cadastral_municipality_code) VALUES ('x', 1, '1234')")
    for ok in ("A/2", "A/10", "C/6", "F/3"):
        assert _errore(banco, "INSERT INTO properties (title, agency_id, cadastral_category) VALUES ('x', 1, %s)", (ok,)) is None


def test_08_nessun_unique_su_edifici_ne_su_scala_piano_interno(banco, edifici):
    # due edifici con la stessa particella e lo stesso indirizzo: ammessi (avviso applicativo)
    for _ in range(2):
        assert _errore(banco, "INSERT INTO buildings (agency_id, city, address, civic_number, cadastral_municipality_code, "
                              "cadastral_section, cadastral_sheet, cadastral_parcel) VALUES (1, 'Alba', 'Via Roma', '10', 'A125', '', '12', '345')") is None
    # due unita' con stesso edificio/scala/piano/interno: ammesse
    for _ in range(2):
        _unita(banco, building_id=edifici["Palazzina via Roma 10"], staircase="A", floor="1", internal_number="1")
    assert _q(banco, "SELECT count(*) FROM properties WHERE building_id = %s AND staircase = 'A' AND floor = '1' AND internal_number = '1'",
              (edifici["Palazzina via Roma 10"],))[0][0] == 2


def test_09_whole_building_solo_per_lo_stabile_intero_collegato(banco, edifici):
    assert "whole_building_chk" in _errore(banco, "INSERT INTO properties (title, agency_id, whole_building) VALUES ('x', 1, TRUE)")
    assert "whole_building_chk" in _errore(banco, "INSERT INTO properties (title, agency_id, property_type, whole_building) VALUES ('x', 1, 'building', TRUE)")
    assert _errore(banco, "INSERT INTO properties (title, agency_id, property_type, whole_building, building_id) VALUES ('x', 1, 'building', TRUE, %s)",
                   (edifici["Palazzina via Roma 10"],)) is None


# ---------------------------------------------------------------------------
# 4. Guardia censimento/commerciale
# ---------------------------------------------------------------------------

def test_10_un_record_census_vive_solo_in_draft_o_archived_senza_incarico(banco):
    i = _unita(banco)
    for stato in ("active", "mandate", "evaluation", "sold"):
        assert "take it in charge" in _errore(banco, "UPDATE properties SET commercial_status = %s WHERE id = %s", (stato, i))
    assert "take it in charge" in _errore(banco, "UPDATE properties SET mandate_type = 'esclusiva' WHERE id = %s", (i,))
    assert "take it in charge" in _errore(banco, "UPDATE properties SET acquisition_id = 1 WHERE id = %s", (i,))
    assert "take it in charge" in _errore(banco, "INSERT INTO properties (title, agency_id, record_kind, commercial_status) VALUES ('x', 1, 'census', 'active')")
    # archiviare un record census e' permesso (e' l'"Annulla")
    assert _errore(banco, "UPDATE properties SET commercial_status = 'archived', archived_at = NOW() WHERE id = %s", (i,)) is None


def test_11_crm_non_torna_census_e_la_promozione_non_porta_altri_cambi(banco):
    assert "cannot go back to census" in _errore(banco, "UPDATE properties SET record_kind = 'census' WHERE id = 1")
    i = _unita(banco)
    # aggiramento: record_kind + stato nello stesso UPDATE
    assert "cannot change its commercial fields in the same statement" in _errore(
        banco, "UPDATE properties SET record_kind = 'crm', commercial_status = 'active' WHERE id = %s", (i,))
    assert "cannot change its commercial fields in the same statement" in _errore(
        banco, "UPDATE properties SET record_kind = 'crm', mandate_type = 'esclusiva' WHERE id = %s", (i,))
    assert _q(banco, "SELECT record_kind, commercial_status FROM properties WHERE id = %s", (i,))[0] == ["census", "draft"]
    # il percorso giusto: prima la presa in carico (solo record_kind), poi il lavoro commerciale
    assert _errore(banco, "UPDATE properties SET record_kind = 'crm' WHERE id = %s", (i,)) is None
    assert _errore(banco, "UPDATE properties SET commercial_status = 'active' WHERE id = %s", (i,)) is None
    assert "cannot go back to census" in _errore(banco, "UPDATE properties SET record_kind = 'census' WHERE id = %s", (i,))


# ---------------------------------------------------------------------------
# 5. Accessori e idempotenza
# ---------------------------------------------------------------------------

def test_12_accessori_compresi_o_da_chiarire_mai_unita(banco):
    i = _unita(banco)
    _q(banco, "INSERT INTO property_accessories (property_id, kind, surface_sqm) VALUES (%s, 'cantina', 6)", (i,))
    _q(banco, "INSERT INTO property_accessories (property_id, kind, cadastral_status) VALUES (%s, 'posto_auto', 'unknown')", (i,))
    assert "status_chk" in _errore(banco, "INSERT INTO property_accessories (property_id, kind, cadastral_status) VALUES (%s, 'cantina', 'autonomous')", (i,))
    assert "kind_chk" in _errore(banco, "INSERT INTO property_accessories (property_id, kind) VALUES (%s, 'piscina')", (i,))
    assert _q(banco, "SELECT count(*) FROM property_accessories WHERE property_id = %s", (i,))[0][0] == 2


def test_13_idempotenza_chiave_e_impronta(banco):
    chiave, fp = str(uuid.uuid4()), _fp('{"title":"x"}')
    assert "client_request_chk" in _errore(banco, "INSERT INTO properties (title, agency_id, client_request_id) VALUES ('x', 1, %s)", (chiave,))
    assert _errore(banco, "INSERT INTO properties (title, agency_id, client_request_id, client_request_fingerprint) VALUES ('x', 1, %s, %s)", (chiave, fp)) is None
    err = _errore(banco, "INSERT INTO properties (title, agency_id, client_request_id, client_request_fingerprint) VALUES ('x', 1, %s, %s)", (chiave, fp))
    assert err and "uq_properties_client_request" in err
    # stessa chiave in un'altra agenzia: e' un'altra richiesta
    assert _errore(banco, "INSERT INTO properties (title, agency_id, client_request_id, client_request_fingerprint) VALUES ('x', 2, %s, %s)", (chiave, fp)) is None
    # chiave e impronta sono IMMUTABILI una volta scritte: lo impone il database
    # (trigger), non il fatto che nessuno le tocchi. Una modifica della scheda
    # che non le riguarda passa; un tentativo di cambiarle e' rifiutato.
    _q(banco, "UPDATE properties SET title = 'modificata' WHERE client_request_id = %s AND agency_id = 1", (chiave,))
    for sql in ("UPDATE properties SET client_request_fingerprint = %s WHERE client_request_id = %s AND agency_id = 1",
                "UPDATE properties SET client_request_id = gen_random_uuid(), client_request_fingerprint = %s "
                "WHERE client_request_id = %s AND agency_id = 1"):
        assert "immutable once set" in _errore(banco, sql, (_fp("altro"), chiave))
    assert "immutable once set" in _errore(banco, "UPDATE properties SET client_request_id = NULL, client_request_fingerprint = NULL "
                                                  "WHERE client_request_id = %s AND agency_id = 1", (chiave,))
    assert _q(banco, "SELECT client_request_fingerprint FROM properties WHERE client_request_id = %s AND agency_id = 1", (chiave,))[0][0] == fp
    # edifici e accessori: stessa regola
    assert _errore(banco, "INSERT INTO buildings (agency_id, city, client_request_id, client_request_fingerprint) VALUES (1, 'c', %s, %s)", (chiave, fp)) is None
    assert "uq_buildings_client_request" in _errore(banco, "INSERT INTO buildings (agency_id, city, client_request_id, client_request_fingerprint) VALUES (1, 'c', %s, %s)", (chiave, fp))
    assert "immutable once set" in _errore(banco, "UPDATE buildings SET client_request_fingerprint = %s WHERE client_request_id = %s", (_fp("x"), chiave))
    acc = _q(banco, "INSERT INTO property_accessories (property_id, kind, client_request_id, client_request_fingerprint) "
                    "SELECT id, 'cantina', %s, %s FROM properties WHERE client_request_id = %s AND agency_id = 1 RETURNING id", (chiave, fp, chiave))[0][0]
    assert "immutable once set" in _errore(banco, "UPDATE property_accessories SET client_request_id = gen_random_uuid() WHERE id = %s", (acc,))


def test_14_concorrenza_due_retry_simultanei_una_sola_riga(banco):
    psycopg2 = banco["psycopg2"]
    chiave, fp = str(uuid.uuid4()), _fp("retry")
    esiti, pronti = {}, threading.Barrier(2)

    def tenta(nome):
        conn = psycopg2.connect(banco["dsn"])
        try:
            with conn.cursor() as cur:
                pronti.wait(timeout=10)
                try:
                    cur.execute("INSERT INTO properties (title, agency_id, client_request_id, client_request_fingerprint) "
                                "VALUES ('retry', 1, %s, %s)", (chiave, fp))
                    conn.commit()
                    esiti[nome] = "ok"
                except psycopg2.Error as exc:
                    conn.rollback()
                    esiti[nome] = "uq_properties_client_request" in str(exc) and "unique"
        finally:
            conn.close()

    t = [threading.Thread(target=tenta, args=(n,)) for n in ("a", "b")]
    for x in t: x.start()
    for x in t: x.join(timeout=30)
    assert sorted(esiti.values(), key=str) == ["ok", "unique"], esiti
    assert _q(banco, "SELECT count(*) FROM properties WHERE client_request_id = %s", (chiave,))[0][0] == 1


# ---------------------------------------------------------------------------
# 6. Down: bloccata con dati, consentita senza
# ---------------------------------------------------------------------------

def _down(banco):
    return _errore(banco, (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8"))


def test_15_la_down_rifiuta_finche_esistono_dati_del_censimento(banco):
    err = _down(banco)
    assert err and "would be lost. Nothing has been changed" in err
    assert _q(banco, "SELECT count(*) FROM information_schema.tables WHERE table_name = 'buildings'")[0][0] == 1


def test_16_la_down_rifiuta_anche_per_un_solo_valore_su_un_immobile_storico(banco):
    # si svuota tutto il censimento, lasciando un'unica categoria su una riga storica
    _q(banco, "DELETE FROM property_accessories")
    _q(banco, "DELETE FROM properties WHERE record_kind = 'census' OR parent_property_id IS NOT NULL OR id > 25")
    _q(banco, "DELETE FROM buildings")
    _q(banco, "UPDATE properties SET cadastral_category = 'A/2' WHERE id = 2")
    err = _down(banco)
    assert err and "1 propert(y/ies) carry census data" in err
    _q(banco, "UPDATE properties SET cadastral_category = NULL WHERE id = 2")


def test_17_la_down_consentita_senza_dati_nuovi_restituisce_lo_schema_di_prima(banco):
    with banco["conn"].cursor() as cur:
        prima = _impronte(cur)
    assert _down(banco) is None
    assert _q(banco, "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('buildings', 'property_accessories')")[0][0] == 0
    assert _q(banco, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'properties' AND column_name IN "
                     "('building_id', 'parent_property_id', 'record_kind', 'cadastral_category', 'client_request_id')")[0][0] == 0
    assert _q(banco, "SELECT count(*) FROM pg_proc WHERE proname IN ('cadastral_norm', 'properties_census_guard', 'properties_links_integrity', "
                     "'censimento_lock_nodes', 'censimento_require_read_committed', 'buildings_agency_guard', 'client_request_immutable')")[0][0] == 0
    with banco["conn"].cursor() as cur:
        assert _impronte(cur) == prima
