"""A30-5 - `GET /api/appointments/lookups/stime`: la ricerca delle stime per il
collegamento manuale di un appuntamento. SOLA LETTURA, solo l'agenzia della
sessione.

Due famiglie:

* senza database: la rotta (solo GET, nel router dell'Agenda, prima di
  `/{appointment_id}`), l'SQL (predicato d'agenzia sempre presente, relazione
  CORE `lead_stime` ristretta alla stessa agenzia, nessun recapito, nessuna
  `stime_dettagliate`, nessuna scrittura) e il service (niente criteri ->
  niente elenco, limiti, agenzia dalla sessione);
* su PostgreSQL usa-e-getta (opt-in `P29_TEST_DSN`, fixture di A30-2): due
  agenzie con stime omonime, nessuna fuga fra le due, nessuna scrittura
  durante la ricerca, e la creazione che accetta la stima propria e risponde
  404 a quella dell'altra agenzia.
"""
from __future__ import annotations

import re

import pytest

from appointments import repository, router, service
from core.exceptions import ValidationError


# ---------------------------------------------------------------------------
# A - la rotta
# ---------------------------------------------------------------------------

def test_a1_la_rotta_e_solo_get_nel_router_agenda_prima_del_dettaglio():
    percorsi = [(r.path, sorted(r.methods)) for r in router.router.routes]
    assert ("/api/appointments/lookups/stime", ["GET"]) in percorsi
    solo = [p for p, _m in percorsi if p == "/api/appointments/lookups/stime"]
    assert len(solo) == 1
    nomi = [p for p, _m in percorsi]
    assert nomi.index("/api/appointments/lookups/stime") < nomi.index(
        "/api/appointments/{appointment_id}")


def test_a2_la_rotta_dipende_da_require_operator():
    from operator_auth.dependencies import require_operator

    (rotta,) = [r for r in router.router.routes if r.path == "/api/appointments/lookups/stime"]
    assert any(d.call is require_operator for d in rotta.dependant.dependencies)


# ---------------------------------------------------------------------------
# B - l'SQL
# ---------------------------------------------------------------------------

class _Cursore:
    def __init__(self, righe=()):
        self.eseguite = []
        self._righe = list(righe)

    def execute(self, sql, parametri=None):
        self.eseguite.append((sql, parametri))

    def fetchall(self):
        return self._righe


def _sql(**kw):
    cur = _Cursore()
    repository.lookup_stime(cur, 7, **kw)
    ((sql, parametri),) = cur.eseguite
    return sql, parametri


def test_b1_il_predicato_d_agenzia_c_e_sempre():
    for kw in ({"search": "Rossi"}, {"lead_id": 3}, {"contact_id": 4},
               {"search": "x", "lead_id": 3, "contact_id": 4}):
        sql, parametri = _sql(**kw)
        assert "s.agency_id = %(agenzia)s" in sql and parametri["agenzia"] == 7


def test_b2_lead_e_cliente_passano_da_lead_stime_della_stessa_agenzia():
    sql, parametri = _sql(lead_id=3)
    assert "FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id" in sql
    assert "l.id = %(lead)s AND l.agency_id = %(agenzia)s" in sql and parametri["lead"] == 3
    sql, parametri = _sql(contact_id=4)
    assert "l.contact_id = %(contatto)s AND l.agency_id = %(agenzia)s" in sql
    assert parametri["contatto"] == 4


def test_b3_solo_colonne_di_riconoscimento_nessun_recapito_nessuna_scrittura():
    sql, _p = _sql(search="Rossi")
    colonne = re.search(r"SELECT (.*?) FROM stime s", sql).group(1)
    assert colonne == ", ".join(f"s.{c}" for c in repository.STIMA_LOOKUP_COLUMNS)
    assert repository.STIMA_LOOKUP_COLUMNS == (
        "id", "data", "nome", "cognome", "comune", "microzona", "via", "civico",
        "tipologia", "mq")
    minuscolo = sql.lower()
    for vietato in ("email", "telefono", "consenso", "stime_dettagliate", "insert", "update ",
                    "delete", "for update", "lead_status", "note_internal"):
        assert vietato not in minuscolo, vietato
    assert sql.rstrip().endswith("LIMIT %(limite)s")


def test_b4_il_testo_e_letterale():
    assert repository._like("50%_a") == "%50\\%\\_a%"
    assert repository._like("a\\b") == "%a\\\\b%"
    _sql_, parametri = _sql(search="Ros")
    assert parametri["testo"] == "%Ros%"


# ---------------------------------------------------------------------------
# C - il service
# ---------------------------------------------------------------------------

class _Ctx:
    def __init__(self, agency_id=7, user_id=3):
        self.agency_id = agency_id
        self.user_id = user_id
        self.may_assign_records = False

    def require_agency(self):
        if self.agency_id is None:
            from operator_auth.exceptions import PlatformAdminAgencyRequired
            raise PlatformAdminAgencyRequired()
        return self.agency_id


@pytest.fixture
def senza_database(monkeypatch):
    def vietato(*_a, **_k):
        raise AssertionError("il service ha aperto il database")
    monkeypatch.setattr(service, "core_cursor", vietato)


def test_c1_senza_criteri_nessun_elenco_e_nessuna_query(senza_database):
    assert service.lookup_stime(_Ctx()) == []
    assert service.lookup_stime(_Ctx(), search="  ") == []
    assert service.lookup_stime(_Ctx(), search="R") == []        # un carattere non basta


def test_c2_limiti(senza_database):
    with pytest.raises(ValidationError):
        service.lookup_stime(_Ctx(), search="x" * 101)
    for limite in (0, 21):
        with pytest.raises(ValidationError):
            service.lookup_stime(_Ctx(), search="Rossi", limit=limite)


def test_c3_l_agenzia_viene_dalla_sessione(monkeypatch):
    from contextlib import contextmanager

    visto = {}

    @contextmanager
    def cursore():
        yield None, "cur"

    def finto(cur, agency_id, **kw):
        visto.update(agency_id=agency_id, **kw)
        return []

    monkeypatch.setattr(service, "core_cursor", cursore)
    monkeypatch.setattr(service.repository, "lookup_stime", finto)
    service.lookup_stime(_Ctx(agency_id=11), search=" Rossi ", lead_id=5)
    assert visto == {"agency_id": 11, "search": "Rossi", "lead_id": 5, "contact_id": None,
                     "limit": 10}


def test_c4_senza_agenzia_o_senza_sessione_si_ferma(senza_database):
    from operator_auth.exceptions import PlatformAdminAgencyRequired

    with pytest.raises(PlatformAdminAgencyRequired):
        service.lookup_stime(_Ctx(agency_id=None), search="Rossi")
    from appointments import errors
    with pytest.raises(errors.SessionRequired):
        service.lookup_stime(_Ctx(user_id=None), search="Rossi")


# ---------------------------------------------------------------------------
# D - PostgreSQL usa-e-getta: isolamento vero
# ---------------------------------------------------------------------------

from tests.test_a30_2_appointments_postgres import (  # noqa: E402,F401 - fixture
    DSN, _nuovo, db, http, mondo)

pg = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL")

LEAD_STIME = """
CREATE TABLE IF NOT EXISTS lead_stime (
    id BIGSERIAL PRIMARY KEY,
    lead_id BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    stima_id INTEGER NOT NULL REFERENCES stime(id) ON DELETE CASCADE,
    relation_type VARCHAR(20) NOT NULL DEFAULT 'related',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT lead_stime_unq UNIQUE (lead_id, stima_id));
"""


@pytest.fixture
def archivio(mondo):
    """Stime omonime nelle due agenzie; una sola collegata al lead di Mario."""
    s = mondo["sql"]
    s(LEAD_STIME)
    s("DELETE FROM lead_stime")
    a, b = mondo["a"], mondo["b"]
    (collegata,), = s("INSERT INTO stime (agency_id, nome, cognome, comune, via, civico, "
                      "tipologia, mq, email, telefono, data) VALUES (%s,'Mario','Rossi',"
                      "'Giulianova','Via Roma','1','Appartamento',85,'m@x.it','333',"
                      "'2026-09-20 10:00') RETURNING id", (a,))
    (sciolta,), = s("INSERT INTO stime (agency_id, nome, cognome, comune, data) VALUES "
                    "(%s,'Mario','Rossi','Teramo','2026-09-10 10:00') RETURNING id", (a,))
    (altrui,), = s("INSERT INTO stime (agency_id, nome, cognome, comune, data) VALUES "
                   "(%s,'Mario','Rossi','Giulianova','2026-09-25 10:00') RETURNING id", (b,))
    (percento,), = s("INSERT INTO stime (agency_id, nome, cognome, comune) VALUES "
                     "(%s,'Anna','50%% Bianchi','Pescara') RETURNING id", (a,))
    s("INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s,%s)",
      (mondo["lead_mario"], collegata))
    (lead_b,), = s("INSERT INTO leads (contact_id, agency_id) VALUES (%s,%s) RETURNING id",
                   (mondo["contatto_b"], b))
    s("INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s,%s)", (lead_b, altrui))
    return {"collegata": collegata, "sciolta": sciolta, "altrui": altrui,
            "percento": percento, "lead_b": lead_b}


def _ids(risposta):
    assert risposta.status_code == 200, risposta.text
    return [r["id"] for r in risposta.json()["items"]]


@pg
def test_d1_ogni_agenzia_vede_solo_le_sue_stime(http, archivio):
    giorgio = _ids(http("giorgio").get("/api/appointments/lookups/stime?search=Rossi"))
    assert set(giorgio) == {archivio["collegata"], archivio["sciolta"]}
    assert archivio["altrui"] not in giorgio
    estraneo = _ids(http("estraneo").get("/api/appointments/lookups/stime?search=Rossi"))
    assert estraneo == [archivio["altrui"]]


@pg
def test_d2_lead_e_cliente_restringono_alla_relazione_core(http, mondo, archivio):
    c = http("luca")
    assert _ids(c.get(f"/api/appointments/lookups/stime?lead_id={mondo['lead_mario']}")) == [
        archivio["collegata"]]
    assert _ids(c.get(f"/api/appointments/lookups/stime?contact_id={mondo['mario']}")) == [
        archivio["collegata"]]
    # un lead o un cliente dell'altra agenzia non restringe a niente: zero righe
    assert _ids(c.get(f"/api/appointments/lookups/stime?lead_id={archivio['lead_b']}")) == []
    assert _ids(c.get(f"/api/appointments/lookups/stime?contact_id={mondo['contatto_b']}"
                      "&search=Rossi")) == []


@pg
def test_d3_la_risposta_non_porta_recapiti(http, archivio):
    r = http("giorgio").get("/api/appointments/lookups/stime?search=Via%20Roma")
    (riga,) = r.json()["items"]
    assert set(riga) == set(repository.STIMA_LOOKUP_COLUMNS)
    assert riga["id"] == archivio["collegata"] and riga["tipologia"] == "Appartamento"
    assert "m@x.it" not in r.text and "333" not in r.text


@pg
def test_d4_il_percento_e_un_carattere_non_un_jolly(http, archivio):
    assert _ids(http("giorgio").get("/api/appointments/lookups/stime?search=50%25")) == [
        archivio["percento"]]
    assert _ids(http("giorgio").get("/api/appointments/lookups/stime?search=%25%25")) == []


@pg
def test_d5_la_ricerca_non_scrive(http, mondo, archivio):
    conta = ("SELECT (SELECT count(*) FROM appointments), (SELECT count(*) FROM "
             "appointment_events), (SELECT count(*) FROM stime), (SELECT count(*) FROM lead_stime),"
             " (SELECT count(*) FROM stima_inspections)")
    prima = mondo["sql"](conta)
    for q in ("search=Rossi", f"lead_id={mondo['lead_mario']}", f"contact_id={mondo['mario']}"):
        http("giorgio").get(f"/api/appointments/lookups/stime?{q}")
    assert mondo["sql"](conta) == prima


@pg
def test_d6_la_creazione_accetta_la_stima_propria_e_non_quella_altrui(http, mondo, archivio):
    c = http("giorgio")
    r = c.post("/api/appointments", json=_nuovo(status="requested", stima_id=archivio["collegata"],
                                                contact_id=mondo["mario"]))
    assert r.status_code == 201, r.text
    assert r.json()["stima_id"] == archivio["collegata"]
    r = c.post("/api/appointments", json=_nuovo(status="requested", stima_id=archivio["altrui"]))
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert str(archivio["altrui"]) not in r.text
