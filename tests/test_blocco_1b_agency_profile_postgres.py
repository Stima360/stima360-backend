"""BLOCCO-1B - dati agenzia e logo su PostgreSQL VERO.

Gira solo con `P29_TEST_DSN`, che deve puntare al cluster di TEST (per esempio
il servizio che ospita `stima360_db_test`), E con
`BLOCCO_1B_TEST_CLUSTER_VERIFIED=1`: la creazione di un database temporaneo non
prova da sola che il cluster sia di test, quindi la verifica e' una precondizione
dichiarata da chi lancia le prove (runbook BLOCCO-1B, passo 0). Come le altre prove `_postgres`
(A30-1, LMC-15) NON scrive nel database indicato dal DSN: crea un database
usa-e-getta `blocco_1b_probe_*` nello stesso cluster, ci applica le migrazioni
REALI 027 (agenzie) e 115 (loghi), lavora solo con dati sintetici e alla fine
lo elimina. Un DSN che nomina un database di PRODUZIONE viene rifiutato prima
di qualunque connessione.

Copre:

    M  la migrazione 115: valida per il runner, applicata, vincoli, down che
       rifiuta di perdere loghi e che, a tabella vuota, si annulla e si riapplica
    N  logo: inserimento, sostituzione, rimozione con lo SQL reale; isolamento
       tra due agenzie; il logo precedente resta se la scrittura fallisce
    O  `report_profile` resta quando la Platform aggiorna timezone/locale, e
       timezone/locale restano quando si aggiornano i dati agenzia
    P  scritture concorrenti sulla stessa riga `agencies`: nessuna perde
       l'altra (sincronizzazione esplicita su pg_stat_activity, niente sleep)
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")
#: Creare un database temporaneo nello stesso cluster NON prova che il cluster
#: sia di test. Queste prove partono solo se chi le lancia ha verificato il
#: cluster (runbook BLOCCO-1B, passo 0) e lo dichiara con questa variabile.
CLUSTER_VERIFIED = os.getenv("BLOCCO_1B_TEST_CLUSTER_VERIFIED") == "1"

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare BLOCCO-1B"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster di test non dichiarato verificato "
                       "(BLOCCO_1B_TEST_CLUSTER_VERIFIED=1, runbook BLOCCO-1B passo 0)"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "115_agency_logos"
SU = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
#: I nomi di PRODUZIONE: un DSN che li nomina non viene nemmeno aperto.
PROD = ("stima360_db", "stima360")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _png(width=4, height=3) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.frombytes("RGBA", (width, height), os.urandom(width * height * 4)).save(out, "PNG")
    return out.getvalue()


def _jpeg(width=16, height=8) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.frombytes("RGB", (width, height), os.urandom(width * height * 3)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN).get("dbname") not in PROD, "P29_TEST_DSN nomina un database di produzione"
    nome = f"blocco_1b_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((MIGRAZIONI / "027_p26_agency_identity.sql").read_text(encoding="utf-8"))
            cur.execute(SU)
        conn.close()
        yield {"dsn": dsn, "nome": nome}
    finally:
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _q(db, sql, params=(), *, commit=False):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            # Senza parametri lo SQL va al server cosi' com'e': i `%` dei RAISE delle
            # migrazioni non sono segnaposto (con `()` psycopg2 li interpreterebbe).
            cur.execute(sql, params or None)
            rows = cur.fetchall() if cur.description else []
        if commit:
            conn.commit()
        return rows
    finally:
        conn.close()


def _waiting_on_lock(db, fragment, timeout=10) -> bool:
    """Vero quando una sessione del database di prova, con `fragment` nella
    query corrente, e' ferma in attesa di un lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _q(db, "SELECT 1 FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock' "
                  "AND query LIKE %s", (db["nome"], f"%{fragment}%")):
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def world(db, monkeypatch):
    """Due agenzie sintetiche, e il service puntato al database di prova con
    lo stesso contratto di `core.database.core_cursor`."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from agency_profile import service
    from operator_auth.context import OperatorContext

    _q(db, "DELETE FROM agency_logos", commit=True)
    _q(db, "DELETE FROM agencies WHERE slug LIKE 'b1b-%%'", commit=True)
    a = _q(db, "INSERT INTO agencies (name, slug, settings) VALUES ('Alfa Prova', 'b1b-alfa', "
               "'{\"timezone\": \"Europe/Rome\", \"locale\": \"it-IT\", \"legacy_x\": 1}') RETURNING id",
           commit=True)[0]["id"]
    b = _q(db, "INSERT INTO agencies (name, slug) VALUES ('Beta Prova', 'b1b-beta') RETURNING id",
           commit=True)[0]["id"]

    @contextmanager
    def cursor(*, commit=False):
        conn = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
        cur = conn.cursor()
        try:
            yield conn, cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    monkeypatch.setattr(service, "core_cursor", cursor)

    def ctx(agency_id, role="agency_owner", user_id=901):
        return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=False,
                               session_id=1, auth_channel="operator_session")

    return {"a": a, "b": b, "ctx": ctx, "service": service, "cursor": cursor}


def _platform_configuration(world, monkeypatch):
    """La PATCH di configurazione della Platform VERA (merge, lock della riga,
    SQL del repository), con l'audit e il lock del Super Admin sostituiti: le
    loro tabelle non sono nel database di prova e non sono l'oggetto di qui."""
    from platform_admin import configuration_service, super_admin

    @contextmanager
    def operation_cursor():
        with world["cursor"]() as pair:          # nessun commit automatico, come l'originale
            yield pair

    monkeypatch.setattr(configuration_service, "platform_operation_cursor", operation_cursor)
    monkeypatch.setattr(super_admin, "lock_super_admin", lambda *a, **k: None)
    monkeypatch.setattr(configuration_service, "audit_then_commit", lambda conn, *a, **k: conn.commit())
    return configuration_service


def _settings(db, agency_id):
    return _q(db, "SELECT settings FROM agencies WHERE id = %s", (agency_id,))[0]["settings"]


# ---------------------------------------------------------------------------
# M - la migrazione 115
# ---------------------------------------------------------------------------

def test_m_migration_is_valid_for_the_runner():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA BLOCCO-2C: dopo la 115 vengono la 116 e la 117.
    m115 = next(m for m in tutte if m.version == VERSIONE)
    assert runner.validate_migration(m115) == []


def test_m_table_and_constraints_exist(db):
    colonne = {r["column_name"]: r for r in _q(
        db, "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
            "WHERE table_name = 'agency_logos'")}
    assert set(colonne) == {"agency_id", "content_type", "width", "height", "image_bytes",
                            "sha256", "uploaded_by", "uploaded_at"}
    assert colonne["image_bytes"]["data_type"] == "bytea"
    vincoli = {r["contype"] for r in _q(
        db, "SELECT contype FROM pg_constraint WHERE conrelid = 'agency_logos'::regclass")}
    assert {"p", "f", "c"} <= vincoli


@pytest.mark.parametrize("colonna,valore", [
    ("content_type", "'image/gif'"), ("width", "5000"), ("height", "0"), ("sha256", "'xyz'"),
])
def test_m_check_constraints_refuse_bad_rows(world, db, colonna, valore):
    import psycopg2

    riga = {"agency_id": str(world["a"]), "content_type": "'image/png'", "width": "1", "height": "1",
            "image_bytes": "'\\x00'::bytea", "sha256": f"'{'a' * 64}'"}
    riga[colonna] = valore
    with pytest.raises(psycopg2.errors.CheckViolation):
        _q(db, f"INSERT INTO agency_logos ({', '.join(riga)}) VALUES ({', '.join(riga.values())})", commit=True)


def test_m_down_refuses_with_logos_and_round_trips_when_empty(world, db):
    import psycopg2

    world["service"].put_logo(world["ctx"](world["a"]), _png())
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, GIU, commit=True)
    assert _q(db, "SELECT count(*) AS n FROM agency_logos")[0]["n"] == 1
    _q(db, "DELETE FROM agency_logos", commit=True)
    _q(db, GIU, commit=True)
    assert not _q(db, "SELECT 1 FROM pg_tables WHERE tablename = 'agency_logos'")

    # Senza la 115: i dati agenzia si leggono e si SALVANO (la transazione non
    # viene annullata da una query sulla tabella assente), il logo risponde
    # "non attivo" e il report esce con il solo nome.
    from agency_profile.schemas import AgencyProfile

    s, owner = world["service"], world["ctx"](world["a"])
    assert s.get_profile(owner)["logo_available"] is False
    assert s.update_profile(owner, AgencyProfile(display_name="Senza 115"))["logo_available"] is False
    assert _settings(db, world["a"])["report_profile"]["display_name"] == "Senza 115"
    with pytest.raises(s.LogoUnavailable):
        s.put_logo(owner, _png())
    assert s.report_identity(owner)["logo"] is None

    _q(db, SU, commit=True)
    assert _q(db, "SELECT 1 FROM pg_tables WHERE tablename = 'agency_logos'")


# ---------------------------------------------------------------------------
# N - logo
# ---------------------------------------------------------------------------

def test_n_insert_replace_remove_with_the_real_sql(world, db):
    from agency_profile.image_validation import normalize_logo

    s, owner = world["service"], world["ctx"](world["a"])
    first = _png()
    meta = s.put_logo(owner, first)
    row = _q(db, "SELECT * FROM agency_logos WHERE agency_id = %s", (world["a"],))[0]
    stored = bytes(row["image_bytes"])
    # Si salva la versione normalizzata; l'impronta e' quella dei byte salvati.
    assert stored == normalize_logo(first).data and row["sha256"] == hashlib.sha256(stored).hexdigest()
    assert row["uploaded_by"] == "operator:901" and meta["size_bytes"] == len(stored)

    second = _jpeg()
    s.put_logo(owner, second)
    rows = _q(db, "SELECT content_type, image_bytes FROM agency_logos WHERE agency_id = %s", (world["a"],))
    assert len(rows) == 1 and rows[0]["content_type"] == "image/jpeg"
    assert bytes(rows[0]["image_bytes"]) == normalize_logo(second).data
    assert s.get_logo(owner)["data"] == bytes(rows[0]["image_bytes"])

    s.delete_logo(owner)
    assert not _q(db, "SELECT 1 FROM agency_logos WHERE agency_id = %s", (world["a"],))
    with pytest.raises(s.LogoNotFound):
        s.get_logo(owner)


def test_n_two_agencies_are_isolated(world, db):
    s = world["service"]
    s.put_logo(world["ctx"](world["a"]), _png())
    with pytest.raises(s.LogoNotFound):
        s.get_logo(world["ctx"](world["b"]))           # nessun logo di ripiego
    with pytest.raises(s.LogoNotFound):
        s.delete_logo(world["ctx"](world["b"]))        # non tocca il logo di A
    assert s.report_identity(world["ctx"](world["b"]))["logo"] is None
    assert _q(db, "SELECT count(*) AS n FROM agency_logos WHERE agency_id = %s", (world["a"],))[0]["n"] == 1


def test_n_a_failed_replacement_keeps_the_previous_logo(world, db, monkeypatch):
    """Il database rifiuta la sostituzione (vincolo): la riga resta quella di prima."""
    import psycopg2

    from agency_profile import service as service_module
    from agency_profile.image_validation import NormalizedLogo

    s, owner = world["service"], world["ctx"](world["a"])
    s.put_logo(owner, _png())
    previous = bytes(_q(db, "SELECT image_bytes FROM agency_logos WHERE agency_id = %s",
                        (world["a"],))[0]["image_bytes"])
    monkeypatch.setattr(service_module, "normalize_logo",
                        lambda data: NormalizedLogo("image/png", 5000, 1, b"\x89PNG"))
    with pytest.raises(psycopg2.errors.CheckViolation):
        s.put_logo(owner, _png())
    row = _q(db, "SELECT image_bytes FROM agency_logos WHERE agency_id = %s", (world["a"],))[0]
    assert bytes(row["image_bytes"]) == previous


def test_n_an_invalid_file_never_reaches_the_database(world, db):
    from agency_profile.image_validation import InvalidLogo

    s, owner = world["service"], world["ctx"](world["a"])
    s.put_logo(owner, _png())
    previous = bytes(_q(db, "SELECT image_bytes FROM agency_logos WHERE agency_id = %s",
                        (world["a"],))[0]["image_bytes"])
    corrupt = _jpeg(64, 64)
    corrupt = corrupt[:len(corrupt) // 2]          # pixel mancanti: non decodificabile
    with pytest.raises(InvalidLogo):
        s.put_logo(owner, corrupt)
    assert bytes(_q(db, "SELECT image_bytes FROM agency_logos WHERE agency_id = %s",
                    (world["a"],))[0]["image_bytes"]) == previous


# ---------------------------------------------------------------------------
# O - report_profile e timezone/locale non si cancellano a vicenda
# ---------------------------------------------------------------------------

def test_o_platform_configuration_keeps_report_profile(world, db, monkeypatch):
    from agency_profile.schemas import AgencyProfile

    world["service"].update_profile(world["ctx"](world["a"]), AgencyProfile(display_name="Alfa Report"))
    configuration = _platform_configuration(world, monkeypatch)
    configuration.update_configuration(world["ctx"](world["a"]), world["a"], {"timezone": "Europe/London"})
    settings = _settings(db, world["a"])
    assert settings["timezone"] == "Europe/London" and settings["legacy_x"] == 1
    assert settings["report_profile"]["display_name"] == "Alfa Report"


def test_o_profile_update_keeps_timezone_locale_and_legacy_keys(world, db):
    from agency_profile.schemas import AgencyProfile

    world["service"].update_profile(world["ctx"](world["a"]), AgencyProfile(display_name="Alfa Report"))
    settings = _settings(db, world["a"])
    assert (settings["timezone"], settings["locale"], settings["legacy_x"]) == ("Europe/Rome", "it-IT", 1)
    assert settings["report_profile"]["updated_by"] == "operator:901"


# ---------------------------------------------------------------------------
# P - scritture concorrenti
# ---------------------------------------------------------------------------

def _run(target):
    outcome = {}

    def runner():
        try:
            outcome["value"] = target()
        except Exception as exc:     # riportata al test, non persa nel thread
            outcome["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    return thread, outcome


def test_p_profile_waits_for_a_platform_transaction_and_both_survive(world, db, monkeypatch):
    """La Platform tiene la riga (merge in corso); il salvataggio dei dati
    agenzia ASPETTA il lock invece di leggere la riga vecchia, e alla fine
    nella riga ci sono entrambe le modifiche."""
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor

    from agency_profile.schemas import AgencyProfile
    from platform_admin import agencies_repository

    platform = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
    try:
        with platform.cursor() as cur:
            row = agencies_repository.lock_agency(cur, world["a"])
            merged = {**row["settings"], "timezone": "Europe/Paris"}
            thread, outcome = _run(lambda: world["service"].update_profile(
                world["ctx"](world["a"]), AgencyProfile(display_name="Concorrente")))
            assert _waiting_on_lock(db, "FOR UPDATE"), "il salvataggio non ha atteso il lock"
            cur.execute("UPDATE agencies SET settings = %s WHERE id = %s", (Json(merged), world["a"]))
        platform.commit()
    finally:
        platform.close()
    thread.join(10)
    assert "error" not in outcome, outcome
    settings = _settings(db, world["a"])
    assert settings["timezone"] == "Europe/Paris"
    assert settings["report_profile"]["display_name"] == "Concorrente"


def test_p_platform_waits_for_a_profile_transaction_and_both_survive(world, db, monkeypatch):
    """Il verso opposto: il salvataggio dei dati agenzia tiene la riga; la PATCH
    della Platform (ora con `lock_agency`) aspetta e fa il merge sulla riga
    aggiornata, senza cancellare `report_profile`."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from agency_profile import repository

    configuration = _platform_configuration(world, monkeypatch)
    holder = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
    try:
        with holder.cursor() as cur:
            row = repository.get_agency(cur, world["a"], lock=True)
            settings = {**row["settings"], "report_profile": {"display_name": "Tenuto"}}
            thread, outcome = _run(lambda: configuration.update_configuration(
                world["ctx"](world["a"]), world["a"], {"timezone": "Europe/Madrid"}))
            assert _waiting_on_lock(db, "FOR UPDATE"), "la Platform non ha atteso il lock"
            repository.write_settings(cur, world["a"], settings)
        holder.commit()
    finally:
        holder.close()
    thread.join(10)
    assert "error" not in outcome, outcome
    final = _settings(db, world["a"])
    assert final["timezone"] == "Europe/Madrid"
    assert final["report_profile"] == {"display_name": "Tenuto"}


def test_p_concurrent_logo_uploads_leave_one_consistent_row(world, db):
    s, owner = world["service"], world["ctx"](world["a"])
    files = [_png(5, 5), _jpeg(20, 10)]
    barrier = threading.Barrier(len(files))

    def upload(data):
        barrier.wait(5)
        return s.put_logo(owner, data)

    runs = [_run(lambda d=d: upload(d)) for d in files]
    for thread, _ in runs:
        thread.join(15)
    assert all("error" not in outcome for _, outcome in runs), runs
    rows = _q(db, "SELECT image_bytes, sha256, content_type FROM agency_logos WHERE agency_id = %s", (world["a"],))
    assert len(rows) == 1
    from agency_profile.image_validation import normalize_logo

    stored = bytes(rows[0]["image_bytes"])
    assert stored in [normalize_logo(f).data for f in files]
    assert rows[0]["sha256"] == hashlib.sha256(stored).hexdigest()
