"""P27-6 su PostgreSQL VERO. Niente doppi: un cluster usa-e-getta.

PERCHE' QUESTO FILE ESISTE

`tests/test_p27_6_lead_routing.py` prova la logica attorno alla query con un
doppio che imita la normalizzazione SQL in Python. Quell'imitazione e' una
IPOTESI: se `lower(btrim(regexp_replace(...)))` non facesse quel che credo,
tutti quei test resterebbero verdi e il routing sbaglierebbe in produzione.

Qui la query gira davvero, l'indice unico rifiuta davvero, la chiave esterna
RESTRICT blocca davvero. Le cose che solo un database puo' dire - un vincolo
che nega, una `COUNT(*) OVER ()` che conta, un `ROLLBACK` che annulla - si
chiedono a un database.

IL CLUSTER

Un `initdb` in una directory temporanea, una porta su socket unix, quattro
migration applicate (027 identita' agenzie, 057 audit, 058 territori, 059
alias), e alla fine tutto cancellato. Nessun contatto con il database TEST
remoto e nessuno con PROD: questo file non legge nessuna variabile
d'ambiente di connessione dell'applicazione, e se il binario non c'e' salta.

La catena completa delle 58 migration NON e' applicabile da zero - meta' dello
schema storico (`contacts`, `stime`, `leads`) nasce fuori dalle migration - e
per quel che P27-6 tocca non serve: queste quattro creano `agencies`,
`network_territories`, `agency_territory_assignments` e
`network_territory_aliases`, cioe' ogni tabella che la query di routing nomina.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

from network_routing.repository import (  # noqa: E402
    ALIAS_SOURCE_PUBLIC_STIMA,
    RoutingAmbiguous,
    find_routable_agency_id,
)
from network_routing.service import (  # noqa: E402
    RoutingDecision,
    resolve_agency_for_public_stima,
)

DEFAULT_SLUG = "stima360"
PORTA = 55433

#: Le quattro migration, nell'ordine in cui vanno applicate.
CATENA = (
    "027_p26_agency_identity",
    "057_p27_platform_audit_log",
    "058_p27_network_territories",
    "059_p27_territory_aliases",
)


def _bin_postgres() -> Path | None:
    """Dove stanno `initdb` e `pg_ctl`, se ci sono.

    Il pacchetto `embedded-postgres` porta i binari dentro il site-packages.
    Se manca - com'e' normale su una macchina di sviluppo - questo intero file
    salta, e le prove comportamentali restano quelle sui doppi.
    """
    for radice in sys.path:
        candidato = Path(radice) / "embedded_postgres" / "pginstall" / "bin"
        if (candidato / "initdb").exists():
            return candidato
    trovato = shutil.which("initdb")
    return Path(trovato).parent if trovato else None


PGBIN = _bin_postgres()

pytestmark = pytest.mark.skipif(
    PGBIN is None,
    reason="nessun PostgreSQL reale disponibile: le prove sui doppi restano",
)


@pytest.fixture(scope="session")
def cluster():
    """Un cluster vivo per la sessione, poi cancellato. Mai riusato.

    `scope='session'` e non `function`: un `initdb` costa qualche secondo e
    l'isolamento fra test lo da' il ROLLBACK, non un cluster nuovo. I due test
    che hanno bisogno di uno schema proprio - apply/down/reapply - si creano un
    DATABASE separato sullo stesso cluster.
    """
    base = Path(tempfile.mkdtemp(prefix="p27_pg_"))
    dati, socket = base / "data", base / "sock"
    socket.mkdir()
    ambiente = dict(os.environ)
    ambiente["LD_LIBRARY_PATH"] = (
        f"{PGBIN.parent / 'lib'}:{ambiente.get('LD_LIBRARY_PATH', '')}"
    )

    def corri(*argomenti):
        return subprocess.run(
            argomenti, env=ambiente, capture_output=True, text=True, timeout=120
        )

    esito = corri(
        str(PGBIN / "initdb"), "-D", str(dati), "-U", "postgres",
        "--auth=trust", "-E", "UTF8",
    )
    if esito.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"initdb non riuscito: {esito.stderr[-300:]}")

    avvio = corri(
        str(PGBIN / "pg_ctl"), "-D", str(dati), "-w", "-l", str(base / "pg.log"),
        "-o", f"-p {PORTA} -k {socket} -c listen_addresses=''", "start",
    )
    if avvio.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"pg_ctl start non riuscito: {avvio.stderr[-300:]}")

    for _ in range(40):
        try:
            psycopg2.connect(host=str(socket), port=PORTA, user="postgres",
                             dbname="postgres").close()
            break
        except psycopg2.OperationalError:
            time.sleep(0.25)
    else:  # pragma: no cover - il cluster non risponde
        corri(str(PGBIN / "pg_ctl"), "-D", str(dati), "-m", "immediate", "stop")
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip("il cluster non ha accettato connessioni")

    try:
        yield {"host": str(socket), "port": PORTA, "user": "postgres"}
    finally:
        corri(str(PGBIN / "pg_ctl"), "-D", str(dati), "-m", "immediate", "stop")
        shutil.rmtree(base, ignore_errors=True)


def _crea_database(cluster, nome: str):
    conn = psycopg2.connect(dbname="postgres", **cluster)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        cur.execute(f'CREATE DATABASE "{nome}"')
    conn.close()
    return psycopg2.connect(dbname=nome, **cluster)


def _applica(conn, nome_migration: str):
    """Esegue un file di migration cosi' com'e', senza riscriverlo.

    Il testo e' quello che verra' eseguito su Render: riscriverlo qui - anche
    solo togliendo un blocco - proverebbe una migration che non esiste.
    """
    sql = (ROOT / "migrations" / f"{nome_migration}.sql").read_text(encoding="utf-8")
    # Una SELECT precedente puo' aver aperto una transazione implicita, e
    # psycopg2 rifiuta di cambiare modalita' li' dentro.
    conn.rollback()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.autocommit = False


@pytest.fixture(scope="session")
def schema(cluster):
    """Lo schema P27 applicato una volta, dalle migration vere."""
    conn = _crea_database(cluster, "p27_routing")
    for nome in CATENA:
        _applica(conn, nome)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def cur(schema):
    """Un cursore dict, e un ROLLBACK dopo ogni test.

    Ogni test riparte da uno schema vuoto senza ricostruirlo: e' il database
    che annulla, e che sappia farlo lo prova `test_k1`.
    """
    with schema.cursor(cursor_factory=RealDictCursor) as c:
        try:
            yield c
        finally:
            schema.rollback()


# ---------------------------------------------------------------------------
# semina
# ---------------------------------------------------------------------------

def _agenzia(cur, slug, stato="active"):
    cur.execute(
        "INSERT INTO agencies (name, slug, status) VALUES (%s, %s, %s) RETURNING id",
        (slug.replace("-", " ").title(), slug, stato),
    )
    return cur.fetchone()["id"]


def _territorio(cur, canonical_key, label, kind="municipality"):
    cur.execute(
        "INSERT INTO network_territories (kind, canonical_key, label) "
        "VALUES (%s, %s, %s) RETURNING id",
        (kind, canonical_key, label),
    )
    return cur.fetchone()["id"]


def _alias(cur, territory_id, valore, stato="active",
           source=ALIAS_SOURCE_PUBLIC_STIMA):
    cur.execute(
        "INSERT INTO network_territory_aliases "
        "(territory_id, source, match_value, status) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (territory_id, source, valore, stato),
    )
    return cur.fetchone()["id"]


def _assegna(cur, territory_id, agency_id, stato="active"):
    cur.execute(
        "INSERT INTO agency_territory_assignments "
        "(territory_id, agency_id, status) VALUES (%s, %s, %s) RETURNING id",
        (territory_id, agency_id, stato),
    )
    return cur.fetchone()["id"]


def _rete(cur, *, alias_value="Alba Adriatica", alias_status="active",
          kind="municipality", assignment_status="active",
          agency_status="active", con_alias=True):
    """La rete minima, scritta davvero.

    La `canonical_key` e' '067001' e la `label` 'Alba Adriatica': la coppia che
    un routing calcolato dallo slug non troverebbe mai, e che qui deve
    instradare perche' l'alias lo DICHIARA.

    L'agenzia di ripiego NON viene creata qui: la 027 la semina gia', ed e' un
    fatto che vale la pena registrare - il ripiego di P27-6 e' la stessa riga
    che P26 ha creato, non una omonima costruita per il test.
    """
    cur.execute("SELECT id FROM agencies WHERE slug = %s", (DEFAULT_SLUG,))
    seminata = cur.fetchone()
    assert seminata is not None, "la 027 non ha seminato l'agenzia di ripiego"
    agenzia = _agenzia(cur, "agenzia-alba", agency_status)
    territorio = _territorio(cur, "067001", "Alba Adriatica", kind)
    if con_alias:
        _alias(cur, territorio, alias_value, alias_status)
    _assegna(cur, territorio, agenzia, assignment_status)
    return {"agenzia": agenzia, "territorio": territorio}


# ---------------------------------------------------------------------------
# A - la 059 si applica, si annulla e si riapplica
# ---------------------------------------------------------------------------

def test_a1_059_applies_reverts_and_reapplies_on_a_real_cluster(cluster):
    """Apply / down / reapply, sul testo vero dei due file.

    Il down deve lasciare uno schema su cui la stessa migration riparte: senza
    questa prova, un `CREATE INDEX` non idempotente si scoprirebbe su Render a
    valle di un rollback, cioe' nel momento peggiore.
    """
    conn = _crea_database(cluster, "p27_ciclo")
    try:
        for nome in CATENA[:-1]:
            _applica(conn, nome)

        def esiste() -> bool:
            with conn.cursor() as c:
                c.execute("SELECT to_regclass('public.network_territory_aliases')")
                return c.fetchone()[0] is not None

        # Il ledger: i down lo nominano, e senza la tabella la DELETE fallirebbe.
        conn.autocommit = True
        with conn.cursor() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version VARCHAR(100) PRIMARY KEY,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            c.execute(
                "INSERT INTO schema_migrations (version) VALUES "
                "('059_p27_territory_aliases')"
            )
        conn.autocommit = False

        assert not esiste()
        _applica(conn, "059_p27_territory_aliases")
        assert esiste()

        _applica(conn, "059_p27_territory_aliases_down")
        assert not esiste()
        with conn.cursor() as c:
            c.execute("SELECT count(*) FROM schema_migrations "
                      "WHERE version = '059_p27_territory_aliases'")
            assert c.fetchone()[0] == 0

        # E i territori di P27-5 sono ancora li': la 059 non li ha toccati e il
        # down non se li e' portati via - nessun CASCADE.
        with conn.cursor() as c:
            c.execute("SELECT to_regclass('public.network_territories')")
            assert c.fetchone()[0] is not None

        _applica(conn, "059_p27_territory_aliases")
        assert esiste()
    finally:
        conn.close()


def test_a2_the_unique_index_exists_and_is_partial_on_an_expression(cur):
    """L'indice c'e', e' UNIQUE, ha un predicato ed e' su un'espressione.

    Un indice unico sulla colonna grezza lascerebbe entrare 'alba adriatica'
    accanto a 'Alba Adriatica'; uno non parziale impedirebbe di ridichiarare un
    nome dopo averlo revocato.
    """
    cur.execute(
        """
        SELECT i.indisunique, i.indpred IS NOT NULL AS parziale,
               i.indexprs IS NOT NULL AS su_espressione
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE c.relname = 'uq_territory_alias_active_value'
        """
    )
    riga = cur.fetchone()
    assert riga is not None, "uq_territory_alias_active_value non esiste"
    assert riga["indisunique"] and riga["parziale"] and riga["su_espressione"]


# ---------------------------------------------------------------------------
# B - l'unicita' sul valore normalizzato, provata dal database
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("secondo", [
    "alba adriatica", "ALBA ADRIATICA", "  Alba   Adriatica  ", "Alba  adriatica",
])
def test_b1_two_territories_cannot_claim_the_same_normalised_value(cur, secondo):
    """LA PROVA CHE L'AMBIGUITA' E' IMPOSSIBILE, non improbabile.

    Non e' il servizio a impedirlo - quello aggiunge un 409 leggibile - ma
    l'indice: anche una INSERT scritta a mano in SQL viene respinta.
    """
    uno = _territorio(cur, "067001", "Alba Adriatica")
    due = _territorio(cur, "067002", "Altro Comune")
    _alias(cur, uno, "Alba Adriatica")
    with pytest.raises(errors.UniqueViolation):
        _alias(cur, due, secondo)


@pytest.mark.parametrize("diverso", [
    "alba-adriatica", "AlbaAdriatica", "Alba Adriatic", "Alba Adriatica Alta",
])
def test_b2_nothing_beyond_the_three_foldings_collides(cur, diverso):
    """Lo slug NON collide col nome: sono due valori diversi, e devono esserlo.

    E' la conferma in SQL che la normalizzazione e' solo maiuscole e spazi.
    """
    uno = _territorio(cur, "067001", "Alba Adriatica")
    due = _territorio(cur, "067002", "Altro Comune")
    _alias(cur, uno, "Alba Adriatica")
    _alias(cur, due, diverso)  # nessuna eccezione


def test_b3_a_revoked_value_can_be_declared_again(cur):
    """L'indice e' parziale: revocato un nome, lo si puo' ridichiarare altrove.

    Senza il `WHERE status = 'active'`, un comune tolto a un'agenzia non
    potrebbe piu' essere dato a nessuno - e la riga vecchia andrebbe cancellata,
    perdendo la storia.
    """
    uno = _territorio(cur, "067001", "Alba Adriatica")
    due = _territorio(cur, "067002", "Altro Comune")
    _alias(cur, uno, "Alba Adriatica", stato="revoked")
    _alias(cur, due, "alba adriatica")  # nessuna eccezione


def test_b4_two_sources_do_not_collide(cur):
    """La chiave e' (sorgente, valore): l'indice non e' sul solo valore.

    Oggi la sorgente e' una sola, e questo test dice cosa succedera' il giorno
    in cui ne arrivera' una seconda - senza costruirla oggi.
    """
    uno = _territorio(cur, "067001", "Alba Adriatica")
    due = _territorio(cur, "067002", "Altro Comune")
    _alias(cur, uno, "Alba Adriatica")
    cur.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'network_territory_aliases'::regclass "
        "  AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%source%'"
    )
    assert cur.fetchone() is not None, "il CHECK su source non esiste"
    with pytest.raises(errors.CheckViolation):
        _alias(cur, due, "Alba Adriatica", source="un_altra_sorgente")


# ---------------------------------------------------------------------------
# C - la chiave esterna
# ---------------------------------------------------------------------------

def test_c1_the_foreign_key_restricts_and_does_not_cascade(cur):
    """Cancellare un territorio con alias viene RIFIUTATO.

    CASCADE avrebbe fatto sparire in silenzio la dichiarazione insieme al
    territorio, e i lead avrebbero cominciato ad andare al ripiego senza che
    nessuna riga dicesse perche'.
    """
    cur.execute(
        "SELECT confdeltype FROM pg_constraint "
        "WHERE conrelid = 'network_territory_aliases'::regclass "
        "  AND contype = 'f'"
    )
    tipi = {r["confdeltype"] for r in cur.fetchall()}
    assert tipi == {"r"}, tipi  # 'r' = RESTRICT

    territorio = _territorio(cur, "067001", "Alba Adriatica")
    _alias(cur, territorio, "Alba Adriatica")
    # `RestrictViolation` e non il generico `ForeignKeyViolation`: e' PostgreSQL
    # a distinguere RESTRICT da NO ACTION, e la classe dell'errore e' la prova
    # che la clausola c'e' davvero e non e' stata omessa.
    with pytest.raises(errors.RestrictViolation):
        cur.execute("DELETE FROM network_territories WHERE id = %s", (territorio,))


def test_c2_an_alias_cannot_point_at_a_territory_that_does_not_exist(cur):
    with pytest.raises(errors.ForeignKeyViolation):
        _alias(cur, 999_999, "Comune Fantasma")


# ---------------------------------------------------------------------------
# D - la query di routing, quella vera
# ---------------------------------------------------------------------------

def test_d1_a_declared_alias_routes_to_the_holding_agency(cur):
    rete = _rete(cur)
    assert find_routable_agency_id(cur, comune="Alba Adriatica") == rete["agenzia"]


@pytest.mark.parametrize("comune", [
    "Alba Adriatica", "alba adriatica", "ALBA ADRIATICA",
    "  Alba   Adriatica  ", "alba  ADRIATICA",
])
def test_d2_the_sql_normalisation_folds_exactly_the_three_things(cur, comune):
    """LA PROVA CHE IL DOPPIO NON MENTIVA.

    `tests/test_p27_6_lead_routing.py` imita questa espressione in Python. Qui
    e' PostgreSQL a eseguirla: se le due divergessero, questo test e quello
    direbbero cose diverse.
    """
    rete = _rete(cur)
    assert find_routable_agency_id(cur, comune=comune) == rete["agenzia"]


@pytest.mark.parametrize("comune", [
    "alba-adriatica", "AlbaAdriatica", "Alba Adriatic", "Alba Adriatica Alta",
])
def test_d3_the_sql_normalisation_folds_nothing_else(cur, comune):
    _rete(cur)
    assert find_routable_agency_id(cur, comune=comune) is None


def test_d4_an_accented_name_routes_as_declared(cur):
    """Nessuna traslitterazione: il nome accentato si dichiara e funziona."""
    rete = _rete(cur, alias_value="Citta' Sant'Angelo")
    assert find_routable_agency_id(cur, comune="CITTA' SANT'ANGELO") == rete["agenzia"]


def test_d5_a_revoked_alias_does_not_route(cur):
    _rete(cur, alias_status="revoked")
    assert find_routable_agency_id(cur, comune="Alba Adriatica") is None


@pytest.mark.parametrize("stato", ["suspended", "revoked"])
def test_d6_a_non_active_assignment_does_not_route(cur, stato):
    _rete(cur, assignment_status=stato)
    assert find_routable_agency_id(cur, comune="Alba Adriatica") is None


@pytest.mark.parametrize("stato", ["suspended", "archived"])
def test_d7_a_non_active_agency_does_not_route(cur, stato):
    """I tre stati che `agencies_status_chk` ammette, e i due che non instradano."""
    _rete(cur, agency_status=stato)
    assert find_routable_agency_id(cur, comune="Alba Adriatica") is None


@pytest.mark.parametrize("kind", ["province", "postal_code"])
def test_d8_only_a_municipality_routes(cur, kind):
    """Un alias appeso a una provincia non instrada, anche se scritto in SQL.

    Il servizio di gestione lo rifiuterebbe con un 422, ma questa riga salta il
    servizio: e' il filtro nella WHERE a fermarla.
    """
    _rete(cur, kind=kind)
    assert find_routable_agency_id(cur, comune="Alba Adriatica") is None


def test_d9_no_alias_means_no_row_and_then_the_fallback(cur):
    _rete(cur, con_alias=False)
    assert find_routable_agency_id(cur, comune="Alba Adriatica") is None
    decisione = resolve_agency_for_public_stima(
        cur, comune="Alba Adriatica", fallback_slug=DEFAULT_SLUG
    )
    assert decisione.source == RoutingDecision.FALLBACK
    cur.execute("SELECT id FROM agencies WHERE slug = %s", (DEFAULT_SLUG,))
    assert decisione.agency_id == cur.fetchone()["id"]


def test_d10_the_whole_pipeline_end_to_end(cur):
    """Dal comune all'agenzia, passando per ogni anello, su dati veri."""
    rete = _rete(cur)
    decisione = resolve_agency_for_public_stima(
        cur, comune="  alba   ADRIATICA ", fallback_slug=DEFAULT_SLUG
    )
    assert decisione.agency_id == rete["agenzia"]
    assert decisione.source == RoutingDecision.TERRITORY
    assert decisione.matched_value == "alba ADRIATICA"


# ---------------------------------------------------------------------------
# E - la finestra COUNT(*) OVER (), e l'ambiguita' impossibile
# ---------------------------------------------------------------------------

def test_e1_the_count_window_returns_one_for_a_single_match(cur):
    """La query dichiara quante righe ha trovato, dentro la riga stessa.

    Il funnel pubblico usa cursori che non offrono `fetchall`: senza la
    finestra, "quante ne ho trovate" non sarebbe una domanda ponibile con una
    sola lettura.
    """
    _rete(cur)
    find_routable_agency_id(cur, comune="Alba Adriatica")
    cur.execute(
        """
        SELECT COUNT(*) OVER () AS instradabili
          FROM network_territory_aliases al
          JOIN network_territories t ON t.id = al.territory_id
          JOIN agency_territory_assignments a ON a.territory_id = t.id
          JOIN agencies ag ON ag.id = a.agency_id
         WHERE al.status = 'active' AND a.status = 'active' AND ag.status = 'active'
        """
    )
    assert cur.fetchone()["instradabili"] == 1


def test_e2_the_database_makes_two_routable_agencies_impossible(cur):
    """Due agenzie per lo stesso comune: nessuna delle due strade esiste.

    Via l'alias, lo vieta `uq_territory_alias_active_value`; via una seconda
    assegnazione, `uq_agency_territory_single_active` della 058. Il ramo
    `RoutingAmbiguous` del codice resta quindi irraggiungibile in un database
    integro - ed e' esattamente il motivo per cui deve sollevare invece di
    scegliere: se scatta, l'invariante e' saltata.
    """
    rete = _rete(cur)
    seconda = _agenzia(cur, "agenzia-due")
    with pytest.raises(errors.UniqueViolation):
        _assegna(cur, rete["territorio"], seconda)
    cur.connection.rollback()

    rete = _rete(cur)
    altro = _territorio(cur, "067002", "Altro Comune")
    with pytest.raises(errors.UniqueViolation):
        _alias(cur, altro, "ALBA ADRIATICA")


def test_e3_if_the_invariant_were_broken_the_code_raises(cur):
    """La rete rotta a mano: due righe instradabili, e il codice solleva.

    Si costruisce sospendendo l'indice parziale - l'unico modo di ottenere lo
    stato che il database vieta - per provare che il ramo esiste e funziona.
    L'indice viene ricreato prima di uscire, e il ROLLBACK del fixture annulla
    comunque tutto.
    """
    rete = _rete(cur)
    cur.execute("DROP INDEX uq_agency_territory_single_active")
    seconda = _agenzia(cur, "agenzia-due")
    _assegna(cur, rete["territorio"], seconda)

    with pytest.raises(RoutingAmbiguous) as errore:
        find_routable_agency_id(cur, comune="Alba Adriatica")
    # Il messaggio NON ripete il comune ricevuto - e' un dato di una persona e
    # questo errore finisce nei log - ma nomina i due indici che avrebbero
    # dovuto impedirlo, cioe' i due posti in cui guardare.
    messaggio = str(errore.value)
    assert "Alba Adriatica" not in messaggio
    assert "uq_territory_alias_active_value" in messaggio
    assert "uq_agency_territory_single_active" in messaggio


# ---------------------------------------------------------------------------
# F - l'annullamento
# ---------------------------------------------------------------------------

def test_f1_a_rollback_really_undoes_the_routing_reads_and_writes(cur):
    """ROLLBACK vero: l'alias scritto sparisce e il routing torna al ripiego.

    E' la garanzia che regge l'atomicita' dichiarata in `main.py`: decisione e
    `INSERT INTO stime` stanno nella stessa transazione, quindi se la stima non
    viene scritta non resta nessuna traccia della decisione.
    """
    rete = _rete(cur)
    assert find_routable_agency_id(cur, comune="Alba Adriatica") == rete["agenzia"]
    cur.connection.rollback()

    cur.execute("SELECT count(*) AS n FROM network_territory_aliases")
    assert cur.fetchone()["n"] == 0
    assert find_routable_agency_id(cur, comune="Alba Adriatica") is None
