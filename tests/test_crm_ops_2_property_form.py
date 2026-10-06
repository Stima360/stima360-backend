"""CRM-OPS-2 - form Immobili: modifica, codice e descrizione generati,
territorio a cascata dal catalogo del portale, classe energetica, agente.

Livelli:
  A. catalogo - confronto con l'estrazione del portale e con ISTAT, regole
     pure (validazione, descrizione, codice);
  B. service senza database - compatibilita' property_admin, valori storici,
     regole di assegnazione, form-options;
  C. contratto HTTP - la route nuova e' dietro la sessione, gli schemi restano
     chiusi;
  D. PostgreSQL VERO (opt-in `P29_TEST_DSN`, SOLO database locale: un DSN che
     non punta a un socket Unix o a localhost fa FALLIRE il test) - migration
     080 su e giu', creazione, modifica, persistenza, codice univoco e stabile,
     agenti della stessa agenzia, assegnazioni conservate;
  E. Shell eseguita - `main.js` vero nello stub DOM della Shell (stesso
     harness di A30-5/A31-4): creazione senza titolo ne' codice, cascata,
     modifica precompilata, valori storici, permessi del menu agenti.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest

from core.exceptions import PermissionDenied, ValidationError
from operator_auth.context import OperatorContext
from property import catalog
from property import repository as property_repository
from property import service as property_service
from property.schemas import PropertyCreate, PropertyUpdate

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
ASSETS = ROOT / "static" / "os_shell" / "assets"

# ---------------------------------------------------------------------------
# A. Catalogo
# ---------------------------------------------------------------------------

#: Estrazione dal portale https://www.stima360.it del 2026-10-01: lo script
#: inline del form di stima (`comuniAbruzzo`, `comuniMarche`, `zone`), copiato
#: come JSON dal browser senza modifiche. Coincide con `zoneMini` di
#: /PianoVenditaStima360.html e con le 80 pagine della sitemap.
PORTALE = json.loads(
    '{"regioni":["Abruzzo","Marche"],"comuni":{"Abruzzo":["Alba Adriatica","Ancarano","Civitella del Tronto",'
    '"Colonnella","Controguerra","Corropoli","Martinsicuro","Nereto","Sant’Egidio alla Vibrata","Sant’Omero",'
    '"Torano Nuovo","Tortoreto"],"Marche":["San Benedetto del Tronto","Grottammare","Cupra Marittima",'
    '"Massignano","Pedaso","Campofilone","Altidona","Fermo","Porto San Giorgio","Porto Sant’Elpidio",'
    '"Potenza Picena","Porto Recanati","Civitanova Marche"]},"zone":{"Alba Adriatica":["Nord","Villa Fiore",'
    '"Zona Basciani"],"Tortoreto":["Lido Sud","Lido Centro","Lido Nord","Alto"],"Martinsicuro":["Centro",'
    '"Villarosa","Alto"],"Colonnella":["Bivio","Centro storico","Contrade"],"Controguerra":["Centro storico",'
    '"Contrade"],"Corropoli":["Centro storico","Bivio","Contrade"],"Nereto":["Centro storico","Bivio",'
    '"Contrade"],"Ancarano":["Centro storico","Contrade"],"Civitella del Tronto":["Centro storico","Contrade"],'
    '"Sant’Egidio alla Vibrata":["Centro","Bivio","Contrade"],"Sant’Omero":["Centro storico","Contrade"],'
    '"Torano Nuovo":["Centro storico","Contrade"],"San Benedetto del Tronto":["Sentina","Porto d’Ascoli",'
    '"Centro / Lungomare","Paese Alto","Agraria","Ponterotto"],"Grottammare":["Centro / Lungomare","Ascolani",'
    '"Valtesino","Vecchio Incasato"],"Cupra Marittima":["Marina / Lungomare","Centro","Castello"],'
    '"Massignano":["Marina di Massignano","Centro / Collina"],"Pedaso":["Centro-Mare / Lungomare","Collina"],'
    '"Campofilone":["Marina di Campofilone","Borgo"],"Altidona":["Marina","Borgo"],"Fermo":["Marina Palmense",'
    '"Lido di Fermo","Casabianca","Lido Tre Archi","San Tommaso","Torre di Palme","Ponte Nina","Tre Camini",'
    '"Santa Maria a Mare"],"Porto San Giorgio":["Centro","Lungomare Nord","Lungomare Sud","Ovest"],'
    '"Porto Sant’Elpidio":["Centro","Faleriense","Corva","Lungomare"],"Potenza Picena":["Porto Potenza Picena '
    '(zona mare)","Centro"],"Porto Recanati":["Centro / Lungomare","Scossicci","Montarice"],"Civitanova Marche":'
    '["Sud","Centro","Nord / Fontespina","San Marone","Civitanova Alta"]}}')

#: ISTAT, Elenco comuni italiani (last-modified 2024-01-26): comune -> (sigla, codice).
ISTAT = {
    "Alba Adriatica": ("TE", "067001"), "Ancarano": ("TE", "067002"),
    "Civitella del Tronto": ("TE", "067017"), "Colonnella": ("TE", "067019"),
    "Controguerra": ("TE", "067020"), "Corropoli": ("TE", "067021"),
    "Martinsicuro": ("TE", "067047"), "Nereto": ("TE", "067031"),
    "Sant’Egidio alla Vibrata": ("TE", "067038"), "Sant’Omero": ("TE", "067039"),
    "Torano Nuovo": ("TE", "067042"), "Tortoreto": ("TE", "067044"),
    "San Benedetto del Tronto": ("AP", "044066"), "Grottammare": ("AP", "044023"),
    "Cupra Marittima": ("AP", "044017"), "Massignano": ("AP", "044029"),
    "Pedaso": ("FM", "109030"), "Campofilone": ("FM", "109004"), "Altidona": ("FM", "109001"),
    "Fermo": ("FM", "109006"), "Porto San Giorgio": ("FM", "109033"),
    "Porto Sant’Elpidio": ("FM", "109034"), "Potenza Picena": ("MC", "043043"),
    "Porto Recanati": ("MC", "043042"), "Civitanova Marche": ("MC", "043013"),
}


def _catalogo_piatto():
    out = {}
    for region, provinces in catalog.TERRITORY.items():
        for code, province in provinces.items():
            for name, (istat, zones) in province["municipalities"].items():
                out[name] = (region, code, istat, zones)
    return out


def test_a01_comuni_e_microzone_coincidono_con_il_portale():
    piatto = _catalogo_piatto()
    assert set(catalog.TERRITORY) == set(PORTALE["regioni"])
    for region, comuni in PORTALE["comuni"].items():
        assert {c for c, v in piatto.items() if v[0] == region} == set(comuni), region
    assert {c: v[3] for c, v in piatto.items()} == PORTALE["zone"]   # stesso ordine delle microzone
    assert len(piatto) == 25
    assert sum(len(v[3]) for v in piatto.values()) == 80


def test_a02_province_e_codici_istat():
    piatto = _catalogo_piatto()
    assert {c: (v[1], v[2]) for c, v in piatto.items()} == ISTAT
    nomi = {code: p["name"] for provinces in catalog.TERRITORY.values() for code, p in provinces.items()}
    assert nomi == {"TE": "Teramo", "AP": "Ascoli Piceno", "FM": "Fermo", "MC": "Macerata"}
    assert set(catalog.TERRITORY["Abruzzo"]) == {"TE"}
    assert set(catalog.TERRITORY["Marche"]) == {"AP", "FM", "MC"}


def test_a03_albero_del_form():
    albero = catalog.territory_tree()
    assert [r["name"] for r in albero] == ["Abruzzo", "Marche"]
    marche = albero[1]
    assert [p["code"] for p in marche["provinces"]] == ["AP", "FM", "MC"]
    ap = [m["name"] for m in marche["provinces"][0]["municipalities"]]
    assert ap == sorted(ap, key=str.casefold)
    fermo = next(m for p in marche["provinces"] for m in p["municipalities"] if m["name"] == "Fermo")
    assert fermo["microzones"][0] == "Marina Palmense" and len(fermo["microzones"]) == 9


@pytest.mark.parametrize("args", [
    (None, None, None, None),
    ("Abruzzo", None, None, None),
    ("Abruzzo", "TE", None, None),
    ("Abruzzo", "TE", "Tortoreto", "Lido Sud"),
    ("Marche", "FM", "Porto Sant’Elpidio", "Corva"),
    (None, None, "Fermo", "Lido di Fermo"),
])
def test_a04_combinazioni_valide(args):
    catalog.validate_location(*args)


@pytest.mark.parametrize("args,match", [
    (("Lazio", None, None, None), "Regione"),
    (("Abruzzo", "AP", None, None), "non appartiene alla regione"),
    (("Abruzzo", "teramo", None, None), "Provincia"),
    (("Abruzzo", "TE", "Giulianova", None), "Comune non presente"),
    (("Marche", "AP", "Fermo", None), "non appartiene alla provincia"),
    (("Abruzzo", None, "Fermo", None), "non appartiene alla regione"),
    (("Abruzzo", "TE", "Tortoreto", "Nord"), "non appartiene al comune"),
    (("Abruzzo", "TE", None, "Nord"), "richiede il comune"),
])
def test_a05_combinazioni_incompatibili(args, match):
    with pytest.raises(ValueError, match=match):
        catalog.validate_location(*args)


def test_a06_classi_energetiche_nell_ordine_e_niente_d_ufficio():
    assert catalog.ENERGY_CLASSES == ("A4", "A3", "A2", "A1", "B", "C", "D", "E", "F", "G")
    catalog.validate_energy_class(None)
    for bad in ("g", "A+", "APE in corso", "Esente", ""):
        with pytest.raises(ValueError):
            catalog.validate_energy_class(bad)


def test_a07_descrizione_e_codice():
    assert catalog.generated_title({"property_type": "apartment", "city": "Tortoreto",
                                    "microzone": "Lido Sud", "address": "Via Roma",
                                    "civic_number": "12"}) == "Appartamento · Tortoreto (Lido Sud) · Via Roma 12"
    assert catalog.generated_title({}) == "Appartamento"
    assert catalog.generated_title({"property_type": "villa", "city": "Fermo"}) == "Villa · Fermo"
    assert len(catalog.generated_title({"address": "x" * 400})) == catalog.TITLE_MAX
    assert set(catalog.PROPERTY_TYPE_LABELS) == __import__("property.enums", fromlist=["x"]).PROPERTY_TYPES
    assert catalog.generated_code(30) == "IMM-30"
    assert catalog.generated_code(30, 1) == "IMM-30-2"


def test_a08_gli_schemi_non_chiedono_titolo_e_accettano_i_campi_storici():
    assert PropertyCreate().title is None
    # property_admin manda ancora tutto questo a testo libero: lo schema lo accetta.
    legacy = PropertyCreate(title="Casa", code="X-1", city="Giulianova", province="teramo",
                            microzone="Lungomare", energy_class="g", assigned_to="Mario")
    assert legacy.city == "Giulianova" and legacy.energy_class == "g"
    with pytest.raises(Exception):
        PropertyCreate(agency_id=1)
    with pytest.raises(Exception):
        PropertyCreate(assigned_agent_id=0)


# ---------------------------------------------------------------------------
# B. Service senza database
# ---------------------------------------------------------------------------

def _ctx(role="agency_owner", user_id=1, platform=False, agency_id=35):
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role,
                           is_platform_admin=platform, session_id=user_id,
                           auth_channel="operator_session")


class FakeRepo:
    def __init__(self, current=None, agents=None):
        self.current = current or {}
        self.agents = agents if agents is not None else [
            {"id": 7, "role": "agent", "name": "Anna Agente"},
            {"id": 8, "role": "agency_admin", "name": "Bruno Admin"},
        ]
        self.calls = []

    def create_property(self, ctx, data, **kw):
        self.calls.append(("create", data, kw))
        return {"id": 1, **data}

    def update_property(self, ctx, i, data, **kw):
        self.calls.append(("update", data, kw))
        return {"id": i, **data}

    def get_property(self, ctx, i):
        return dict(self.current)

    def list_assignable_agents(self, ctx):
        return list(self.agents)

    def assignable_agent_name(self, ctx, uid):
        return next((a["name"] for a in self.agents if a["id"] == uid), None)


@pytest.fixture
def fake(monkeypatch):
    def make(**kw):
        repo = FakeRepo(**kw)
        monkeypatch.setattr(property_service, "repository", repo)
        return repo
    return make


def test_b01_creazione_chiede_titolo_e_codice_al_backend(fake):
    repo = fake()
    property_service.create_property(_ctx(), PropertyCreate(city=None))
    kind, data, kw = repo.calls[0]
    assert kw == {"generate_identity": True}
    assert data["title"] is None and data["code"] is None


def test_b02_creazione_dal_form_os_valida_il_territorio(fake):
    fake()
    ok = PropertyCreate(region="Abruzzo", province="TE", city="Tortoreto", microzone="Lido Sud")
    property_service.create_property(_ctx(), ok)
    with pytest.raises(ValidationError, match="microzona"):
        property_service.create_property(_ctx(), PropertyCreate(region="Abruzzo", province="TE",
                                                                city="Tortoreto", microzone="Nord"))
    with pytest.raises(ValidationError, match="Comune"):
        property_service.create_property(_ctx(), PropertyCreate(region="Abruzzo", city="Giulianova"))


def test_b03_property_admin_crea_e_modifica_a_testo_libero_come_prima(fake):
    """Senza `region` (che property_admin non conosce) il territorio non e'
    giudicato: comune, provincia e microzona liberi restano accettati."""
    repo = fake(current={"city": "Giulianova", "province": "teramo", "microzone": "Lungomare",
                         "energy_class": "g", "assigned_agent_id": 7, "code": "IMM-5"})
    property_service.create_property(_ctx("agent"), PropertyCreate(
        title="Casa", city="Giulianova", province="teramo", microzone="Lungomare", assigned_to="Mario"))
    # Il PATCH di property_admin rimanda TUTTI i campi, anche quelli invariati.
    legacy_patch = PropertyUpdate(code=None, title="Casa", city="Giulianova", province="teramo",
                                  microzone="Lungomare", energy_class="g", assigned_to="Anna Agente",
                                  internal_notes="nota nuova")
    property_service.update_property(_ctx("agent"), 5, legacy_patch)
    kind, data, kw = repo.calls[-1]
    assert kw == {"derive_identity": True}
    assert "code" not in data                      # un codice nullo non cancella quello stabile
    assert "assigned_agent_id" not in data         # l'assegnazione non si tocca
    assert data["energy_class"] == "g" and data["city"] == "Giulianova"


def test_b04_classe_energetica_solo_i_valori_nuovi_si_giudicano(fake):
    fake(current={"energy_class": "g"})
    property_service.update_property(_ctx(), 5, PropertyUpdate(energy_class="g"))     # invariata: ok
    property_service.update_property(_ctx(), 5, PropertyUpdate(energy_class="A4"))    # nuova valida
    property_service.update_property(_ctx(), 5, PropertyUpdate(energy_class=None))    # "Non indicata"
    with pytest.raises(ValidationError, match="Classe energetica"):
        property_service.update_property(_ctx(), 5, PropertyUpdate(energy_class="classe b"))
    with pytest.raises(ValidationError, match="Classe energetica"):
        property_service.create_property(_ctx(), PropertyCreate(energy_class="g"))


def test_b05_territorio_in_modifica_si_giudica_sul_risultato(fake):
    fake(current={"region": "Abruzzo", "province": "TE", "city": "Tortoreto", "microzone": "Alto"})
    property_service.update_property(_ctx(), 5, PropertyUpdate(region="Marche", province="FM",
                                                               city="Fermo", microzone="Casabianca"))
    with pytest.raises(ValidationError):
        property_service.update_property(_ctx(), 5, PropertyUpdate(region="Marche"))   # comune TE rimasto
    # Un valore storico mai toccato non e' giudicato: nessun campo territoriale inviato.
    fake(current={"region": None, "province": "teramo", "city": "Toretoreto Alto"})
    property_service.update_property(_ctx(), 5, PropertyUpdate(asking_price=100000))


def test_b06_assegnazione_regole(fake):
    repo = fake(current={"assigned_agent_id": 7})
    # invariata: nessun permesso richiesto, il campo esce dal payload
    property_service.update_property(_ctx("agent"), 5, PropertyUpdate(assigned_agent_id=7))
    assert "assigned_agent_id" not in repo.calls[-1][1]
    # cambiata da un agent: 403
    with pytest.raises(PermissionDenied):
        property_service.update_property(_ctx("agent"), 5, PropertyUpdate(assigned_agent_id=8))
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0 (D19): un agent che crea un
    # immobile lo crea assegnato a se' - il payload non comanda (un altro id
    # nel corpo non e' un 403: viene sostituito dal proprio), e il nome e'
    # l'istantanea del server. Owner/admin/platform restano come prima.
    property_service.create_property(_ctx("agent", user_id=7), PropertyCreate(assigned_agent_id=8))
    assert repo.calls[-1][1]["assigned_agent_id"] == 7
    assert repo.calls[-1][1]["assigned_to"] == "Anna Agente"
    # owner/admin/platform admin acting: si', con istantanea del nome
    for ctx in (_ctx("agency_owner"), _ctx("agency_admin"), _ctx(None, platform=True)):
        property_service.update_property(ctx, 5, PropertyUpdate(assigned_agent_id=8))
        assert repo.calls[-1][1]["assigned_to"] == "Bruno Admin"
    # bersaglio non attivo o di altra agenzia: 400
    with pytest.raises(ValidationError, match="membro attivo"):
        property_service.update_property(_ctx(), 5, PropertyUpdate(assigned_agent_id=99))
    # rimozione esplicita: azzera anche l'istantanea
    property_service.update_property(_ctx(), 5, PropertyUpdate(assigned_agent_id=None))
    assert repo.calls[-1][1]["assigned_agent_id"] is None and repo.calls[-1][1]["assigned_to"] is None


def test_b08_con_id_assegnato_il_testo_legacy_non_cambia_il_nome(fake):
    """property_admin rimanda "Assegnato a" a ogni salvataggio: con un ID
    assegnato quel testo e' ignorato, gli altri campi passano."""
    repo = fake(current={"assigned_agent_id": 7, "assigned_to": "Anna Agente"})
    property_service.update_property(_ctx("agent"), 5, PropertyUpdate(
        assigned_to="Un altro nome", internal_notes="nota"))
    data = repo.calls[-1][1]
    assert "assigned_to" not in data and "assigned_agent_id" not in data
    assert data["internal_notes"] == "nota"
    # anche un testo vuoto non "toglie" l'agente: per rimuoverlo serve l'ID
    property_service.update_property(_ctx("agent"), 5, PropertyUpdate(assigned_to=None))
    assert "assigned_to" not in repo.calls[-1][1]
    # ID invariato + testo diverso: stesso esito, nessun permesso richiesto
    property_service.update_property(_ctx("agent"), 5, PropertyUpdate(
        assigned_agent_id=7, assigned_to="Un altro nome"))
    assert "assigned_to" not in repo.calls[-1][1] and "assigned_agent_id" not in repo.calls[-1][1]
    # una vera riassegnazione insieme a un testo: vince l'istantanea del server
    property_service.update_property(_ctx("agency_owner"), 5, PropertyUpdate(
        assigned_agent_id=8, assigned_to="Testo libero"))
    assert repo.calls[-1][1]["assigned_to"] == "Bruno Admin"


def test_b09_senza_id_il_testo_storico_resta_modificabile(fake):
    repo = fake(current={"assigned_agent_id": None, "assigned_to": "Mario Rossi"})
    property_service.update_property(_ctx("agent"), 5, PropertyUpdate(assigned_to="Luigi Verdi"))
    assert repo.calls[-1][1]["assigned_to"] == "Luigi Verdi"


def test_b07_form_options_agenti_solo_a_chi_assegna(fake):
    fake()
    owner = property_service.form_options(_ctx("agency_owner"))
    agent = property_service.form_options(_ctx("agent"))
    assert owner["can_assign"] is True and [a["id"] for a in owner["agents"]] == [7, 8]
    assert agent["can_assign"] is False and agent["agents"] == []
    assert owner["energy_classes"] == list(catalog.ENERGY_CLASSES)
    assert owner["territory"] == catalog.territory_tree()
    assert {t["value"] for t in owner["property_types"]} == set(catalog.PROPERTY_TYPE_LABELS)


# ---------------------------------------------------------------------------
# C. Contratto HTTP
# ---------------------------------------------------------------------------

def test_c01_route_form_options_dietro_la_sessione():
    from fastapi.testclient import TestClient
    from integration_p2_support import import_main_app
    from property import router as property_router
    paths = {(r.path, tuple(sorted(r.methods))) for r in property_router.router.routes}
    assert ("/api/property/form-options", ("GET",)) in paths
    client = TestClient(import_main_app())
    assert client.get("/api/property/form-options").status_code == 401


def test_c02_nessun_campo_di_agenzia_negli_schemi():
    for model in (PropertyCreate, PropertyUpdate):
        fields = getattr(model, "model_fields", None) or model.__fields__
        assert "agency_id" not in fields
        assert {"region", "assigned_agent_id"} <= set(fields)


# ---------------------------------------------------------------------------
# D. PostgreSQL VERO, solo locale
# ---------------------------------------------------------------------------

DSN = os.getenv("P29_TEST_DSN")
CATENA = ("027_p26_agency_identity", "001_core_contacts_leads",
          "028_p26_core_agency_columns", "029_p26_core_agency_backfill",
          "030_p26_core_agency_enforce", "002_property_01", "003_property_02",
          "034_p26_property_agency_columns", "035_p26_property_agency_backfill",
          "036_p26_property_agency_enforce")
VERSIONE = "080_crm_ops_2_property_form"

pg = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: livello D NON eseguito")


def _dsn_locale(dsn: str) -> None:
    """Fallisce se il DSN non e' locale: socket Unix o localhost, e nient'altro.
    Le migrazioni di questo file non devono MAI raggiungere TEST o PROD."""
    parsed = urlparse(dsn)
    host = parsed.hostname or (parse_qs(parsed.query).get("host") or [""])[0]
    if not (host == "" or host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")):
        pytest.fail(f"P29_TEST_DSN non locale (host={host!r}): rifiutato")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    _dsn_locale(DSN)
    nome = f"crmops2_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute("SELECT inet_server_addr()")
        assert cur.fetchone()[0] in (None, "127.0.0.1", "::1"), "il server non e' locale"
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # 001 referenzia stime(id): basta la chiave. get_property legge
            # buy_request_interactions in due LATERAL di sola lettura: qui le
            # sole colonne che legge.
            cur.execute("CREATE TABLE stime (id SERIAL PRIMARY KEY)")
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute("""CREATE TABLE buy_request_interactions (
                id BIGSERIAL PRIMARY KEY, buy_request_id BIGINT, match_id BIGINT,
                property_visit_id BIGINT, interaction_type TEXT, occurred_at TIMESTAMPTZ)""")
            cur.execute((MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8"))
        yield {"dsn": dsn, "conn": conn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def mondo(db, monkeypatch):
    psycopg2 = pytest.importorskip("psycopg2")
    from core import database as core_database
    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    conn = db["conn"]
    with conn.cursor() as cur:
        for tabella in ("property_status_history", "property_price_history", "properties",
                        "agency_memberships", "operator_users"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("DELETE FROM agencies WHERE slug <> 'stima360'")
        cur.execute("SELECT id FROM agencies WHERE slug = 'stima360'")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (name, slug, status) VALUES ('B', 'b-due', 'active') RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, nome, agenzia, ruolo, stato="active"):
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                        "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s) RETURNING id", (email, email, nome))
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                        "VALUES (%s, %s, %s, %s)", (agenzia, uid, ruolo, stato))
            return uid

        ids = {
            "owner_a": operatore("o.a@example.test", "Olga", a, "agency_owner"),
            "agent_a": operatore("a.a@example.test", "Anna", a, "agent"),
            "agent_a2": operatore("a2.a@example.test", "Aldo", a, "agent"),
            "sospeso_a": operatore("s.a@example.test", "Sara", a, "agent", "suspended"),
            "owner_b": operatore("o.b@example.test", "Bea", b, "agency_owner"),
        }

    def sql(testo, parametri=None):
        with conn.cursor() as cur:
            cur.execute(testo, parametri)
            return cur.fetchall() if cur.description else None

    ctx = {
        "owner_a": _ctx("agency_owner", ids["owner_a"], agency_id=a),
        "agent_a": _ctx("agent", ids["agent_a"], agency_id=a),
        "owner_b": _ctx("agency_owner", ids["owner_b"], agency_id=b),
    }
    return {"sql": sql, "A": a, "B": b, "ids": ids, "ctx": ctx}


def _riga(mondo, pid):
    righe = mondo["sql"]("SELECT row_to_json(p) FROM properties p WHERE id=%s", (pid,))
    return righe[0][0]


@pg
def test_d01_migrazione_080_colonne_fk_indice(mondo):
    colonne = mondo["sql"]("SELECT column_name, is_nullable FROM information_schema.columns "
                           "WHERE table_name='properties' AND column_name IN ('region','assigned_agent_id') "
                           "ORDER BY 1")
    assert colonne == [("assigned_agent_id", "YES"), ("region", "YES")]
    from psycopg2 import errors
    with pytest.raises(errors.ForeignKeyViolation):     # agente di un'altra agenzia: lo nega il DB
        mondo["sql"]("INSERT INTO properties (title, agency_id, assigned_agent_id) VALUES ('x', %s, %s)",
                     (mondo["A"], mondo["ids"]["owner_b"]))


@pg
def test_d02_creazione_senza_titolo_ne_codice_poi_modifica_e_rilettura(mondo):
    owner = mondo["ctx"]["owner_a"]
    creato = property_service.create_property(owner, PropertyCreate(
        region="Abruzzo", province="TE", city="Tortoreto", microzone="Lido Sud",
        address="Via Roma", civic_number="12", energy_class="A2",
        assigned_agent_id=mondo["ids"]["agent_a"]))
    pid = creato["id"]
    riga = _riga(mondo, pid)
    assert riga["code"] == f"IMM-{pid}"
    assert riga["title"] == "Appartamento · Tortoreto (Lido Sud) · Via Roma 12"
    assert (riga["region"], riga["province"], riga["city"], riga["microzone"]) == \
        ("Abruzzo", "TE", "Tortoreto", "Lido Sud")
    assert riga["energy_class"] == "A2"
    assert riga["assigned_agent_id"] == mondo["ids"]["agent_a"] and riga["assigned_to"] == "Anna"

    # modifica: stesso id, codice stabile, descrizione generata aggiornata
    aggiornato = property_service.update_property(owner, pid, PropertyUpdate(
        region="Marche", province="FM", city="Fermo", microzone="Casabianca"))
    assert aggiornato["id"] == pid
    riga = _riga(mondo, pid)
    assert riga["code"] == f"IMM-{pid}"
    assert riga["title"] == "Appartamento · Fermo (Casabianca) · Via Roma 12"
    assert (riga["region"], riga["province"], riga["city"], riga["microzone"]) == ("Marche", "FM", "Fermo", "Casabianca")
    # modifica di un altro campo: assegnazione, territorio e classe restano
    property_service.update_property(owner, pid, PropertyUpdate(asking_price=250000))
    riga = _riga(mondo, pid)
    assert riga["assigned_agent_id"] == mondo["ids"]["agent_a"] and riga["assigned_to"] == "Anna"
    assert riga["energy_class"] == "A2" and riga["city"] == "Fermo" and riga["code"] == f"IMM-{pid}"
    assert mondo["sql"]("SELECT count(*) FROM properties")[0][0] == 1     # nessun duplicato
    # rilettura come la Shell: GET scheda
    letto = property_service.get_property(owner, pid)
    assert letto["region"] == "Marche" and letto["assigned_agent_id"] == mondo["ids"]["agent_a"]


@pg
def test_d03_codici_univoci_e_codice_storico_occupato(mondo):
    owner = mondo["ctx"]["owner_a"]
    primo = property_service.create_property(owner, PropertyCreate())
    secondo = property_service.create_property(owner, PropertyCreate())
    assert primo["id"] != secondo["id"]
    codici = [r[0] for r in mondo["sql"]("SELECT code FROM properties ORDER BY id")]
    assert len(set(codici)) == 2 and all(c.startswith("IMM-") for c in codici)
    # un codice storico scritto a mano occupa gia' la forma IMM-<prossimo id>
    # (il record storico consuma lui stesso il prossimo id: il codice occupato
    # e' quello dell'immobile DOPO di lui)
    prossimo = mondo["sql"]("SELECT last_value + 2 FROM properties_id_seq")[0][0]
    property_service.create_property(owner, PropertyCreate(title="storico", code=f"IMM-{prossimo}"))
    terzo = property_service.create_property(owner, PropertyCreate())
    assert terzo["id"] == prossimo
    assert _riga(mondo, terzo["id"])["code"] == f"IMM-{terzo['id']}-2"
    # il codice manuale storico resta il suo
    assert mondo["sql"]("SELECT count(*) FROM properties WHERE code=%s", (f"IMM-{prossimo}",))[0][0] == 1


@pg
def test_d04_immobile_storico_resta_apribile_e_modificabile(mondo):
    """Valori come quelli reali di TEST: comune con refuso, provincia per
    esteso, classe minuscola, titolo manuale, nessun codice, agente a testo."""
    owner = mondo["ctx"]["owner_a"]
    pid = mondo["sql"](
        "INSERT INTO properties (title, agency_id, city, province, energy_class, assigned_to) "
        "VALUES ('Villa al mare (titolo manuale)', %s, 'Toretoreto Alto', 'teramo', 'g', 'Mario Rossi') "
        "RETURNING id", (mondo["A"],))[0][0]
    letto = property_service.get_property(owner, pid)
    assert letto["city"] == "Toretoreto Alto"
    property_service.update_property(owner, pid, PropertyUpdate(asking_price=99000, address="Via Mare"))
    riga = _riga(mondo, pid)
    assert (riga["city"], riga["province"], riga["energy_class"]) == ("Toretoreto Alto", "teramo", "g")
    assert riga["title"] == "Villa al mare (titolo manuale)"    # il titolo manuale non si sovrascrive
    assert riga["assigned_to"] == "Mario Rossi"                 # l'indicazione storica resta
    assert riga["code"] == f"IMM-{pid}"                          # il codice mancante viene assegnato
    property_service.update_property(owner, pid, PropertyUpdate(internal_notes="ancora"))
    assert _riga(mondo, pid)["code"] == f"IMM-{pid}"             # e poi non cambia piu'


@pg
def test_d05_salvataggio_come_property_admin(mondo):
    """property_admin rimanda tutti i campi a ogni salvataggio, testo libero."""
    owner = mondo["ctx"]["owner_a"]
    creato = property_service.create_property(owner, PropertyCreate(
        region="Abruzzo", province="TE", city="Nereto", microzone="Bivio",
        assigned_agent_id=mondo["ids"]["agent_a"]))
    pid = creato["id"]
    riga = _riga(mondo, pid)
    agent = mondo["ctx"]["agent_a"]
    property_service.update_property(agent, pid, PropertyUpdate(
        code=None, title=riga["title"], commercial_status="draft", city="Nereto", province="TE",
        microzone="Bivio", energy_class=None, assigned_to=riga["assigned_to"], internal_notes="da admin"))
    dopo = _riga(mondo, pid)
    assert dopo["code"] == riga["code"]
    assert dopo["assigned_agent_id"] == mondo["ids"]["agent_a"]
    assert dopo["region"] == "Abruzzo" and dopo["internal_notes"] == "da admin"
    # e crea a testo libero come prima
    libero = property_service.create_property(agent, PropertyCreate(
        title="Casa libera", city="Giulianova", province="TE", microzone="Annunziata"))
    assert _riga(mondo, libero["id"])["city"] == "Giulianova"


@pg
def test_d06_agenti_solo_attivi_della_stessa_agenzia(mondo):
    owner, agent = mondo["ctx"]["owner_a"], mondo["ctx"]["agent_a"]
    opzioni = property_service.form_options(owner)
    ids = {a["id"] for a in opzioni["agents"]}
    assert ids == {mondo["ids"]["owner_a"], mondo["ids"]["agent_a"], mondo["ids"]["agent_a2"]}
    assert property_service.form_options(agent)["agents"] == []
    pid = property_service.create_property(owner, PropertyCreate())["id"]
    for estraneo in (mondo["ids"]["owner_b"], mondo["ids"]["sospeso_a"], 424242):
        with pytest.raises(ValidationError):
            property_service.update_property(owner, pid, PropertyUpdate(assigned_agent_id=estraneo))
    with pytest.raises(PermissionDenied):
        property_service.update_property(agent, pid, PropertyUpdate(assigned_agent_id=mondo["ids"]["agent_a2"]))
    property_service.update_property(owner, pid, PropertyUpdate(assigned_agent_id=mondo["ids"]["agent_a2"]))
    assert _riga(mondo, pid)["assigned_agent_id"] == mondo["ids"]["agent_a2"]
    # l'agenzia B non vede ne' modifica l'immobile di A
    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        property_service.update_property(mondo["ctx"]["owner_b"], pid, PropertyUpdate(asking_price=1))


@pg
@pytest.mark.parametrize("variante", ["omesso", "null", "vuoto", "spazi"])
def test_d08_codice_mancante_in_ogni_forma_viene_generato(mondo, variante):
    owner = mondo["ctx"]["owner_a"]
    campi = {"omesso": {}, "null": {"code": None}, "vuoto": {"code": ""}, "spazi": {"code": "   "}}[variante]
    creato = property_service.create_property(owner, PropertyCreate(**campi))
    pid = creato["id"]
    assert creato["code"] == f"IMM-{pid}"                  # gia' nella risposta della POST
    assert _riga(mondo, pid)["code"] == f"IMM-{pid}"       # persistito
    property_service.update_property(owner, pid, PropertyUpdate(asking_price=1000, code="  "))
    property_service.update_property(owner, pid, PropertyUpdate(internal_notes="x"))
    assert _riga(mondo, pid)["code"] == f"IMM-{pid}"       # stabile dopo le modifiche


@pg
def test_d09_piu_immobili_con_codice_vuoto_non_collidono(mondo):
    owner = mondo["ctx"]["owner_a"]
    ids = [property_service.create_property(owner, PropertyCreate(code=c))["id"] for c in ("", " ", "")]
    codici = [r[0] for r in mondo["sql"]("SELECT code FROM properties WHERE id = ANY(%s) ORDER BY id", (ids,))]
    assert codici == [f"IMM-{i}" for i in sorted(ids)]
    assert mondo["sql"]("SELECT count(*) FROM properties WHERE code IS NULL OR btrim(code) = ''")[0][0] == 0


@pg
def test_d10_coerenza_agente_assegnato(mondo):
    owner, agent = mondo["ctx"]["owner_a"], mondo["ctx"]["agent_a"]
    agent2 = mondo["ids"]["agent_a2"]
    pid = property_service.create_property(owner, PropertyCreate(
        assigned_agent_id=mondo["ids"]["agent_a"]))["id"]
    # property_admin manda un nome diverso mentre esiste l'ID: ID e nome restano
    property_service.update_property(agent, pid, PropertyUpdate(
        assigned_to="Qualcun altro", internal_notes="salvata da property_admin"))
    riga = _riga(mondo, pid)
    assert (riga["assigned_agent_id"], riga["assigned_to"]) == (mondo["ids"]["agent_a"], "Anna")
    assert riga["internal_notes"] == "salvata da property_admin"
    # modifica di altri campi: ID e nome invariati
    property_service.update_property(owner, pid, PropertyUpdate(asking_price=150000, region="Abruzzo"))
    riga = _riga(mondo, pid)
    assert (riga["assigned_agent_id"], riga["assigned_to"]) == (mondo["ids"]["agent_a"], "Anna")
    # tentativi non autorizzati: agent che riassegna o rimuove -> 403, nulla cambia
    for tentativo in (agent2, None):
        with pytest.raises(PermissionDenied):
            property_service.update_property(agent, pid, PropertyUpdate(assigned_agent_id=tentativo))
    with pytest.raises(ValidationError):                # membro di un'altra agenzia
        property_service.update_property(owner, pid, PropertyUpdate(assigned_agent_id=mondo["ids"]["owner_b"]))
    with pytest.raises(ValidationError):                # membro sospeso
        property_service.update_property(owner, pid, PropertyUpdate(assigned_agent_id=mondo["ids"]["sospeso_a"]))
    riga = _riga(mondo, pid)
    assert (riga["assigned_agent_id"], riga["assigned_to"]) == (mondo["ids"]["agent_a"], "Anna")
    # riassegnazione autorizzata: ID e nome cambiano insieme
    property_service.update_property(owner, pid, PropertyUpdate(assigned_agent_id=agent2))
    riga = _riga(mondo, pid)
    assert (riga["assigned_agent_id"], riga["assigned_to"]) == (agent2, "Aldo")
    # rimozione autorizzata: entrambi a NULL
    property_service.update_property(owner, pid, PropertyUpdate(assigned_agent_id=None))
    riga = _riga(mondo, pid)
    assert (riga["assigned_agent_id"], riga["assigned_to"]) == (None, None)
    # da qui, senza ID, il testo libero torna modificabile
    property_service.update_property(agent, pid, PropertyUpdate(assigned_to="Mario Rossi"))
    assert _riga(mondo, pid)["assigned_to"] == "Mario Rossi"


@pg
def test_d11_testo_storico_senza_id_conservato(mondo):
    owner = mondo["ctx"]["owner_a"]
    pid = mondo["sql"]("INSERT INTO properties (title, agency_id, assigned_to) "
                       "VALUES ('storico', %s, 'Mario Rossi') RETURNING id", (mondo["A"],))[0][0]
    property_service.update_property(owner, pid, PropertyUpdate(asking_price=5, internal_notes="n"))
    riga = _riga(mondo, pid)
    assert riga["assigned_to"] == "Mario Rossi" and riga["assigned_agent_id"] is None


@pg
def test_d07_down_rifiuta_con_dati_poi_scende_e_risale(mondo, db):
    psycopg2 = pytest.importorskip("psycopg2")
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
    owner = mondo["ctx"]["owner_a"]
    property_service.create_property(owner, PropertyCreate(region="Marche"))
    conn = psycopg2.connect(db["dsn"])
    try:
        with conn.cursor() as cur, pytest.raises(psycopg2.errors.RaiseException, match="would lose it"):
            cur.execute(giu)
        conn.rollback()
        mondo["sql"]("UPDATE properties SET region=NULL, assigned_agent_id=NULL")
        with conn.cursor() as cur:
            cur.execute(giu)
        conn.commit()
        assert mondo["sql"]("SELECT count(*) FROM information_schema.columns WHERE table_name='properties' "
                            "AND column_name IN ('region','assigned_agent_id')")[0][0] == 0
        assert mondo["sql"]("SELECT count(*) FROM properties")[0][0] == 1     # nessuna riga persa
        with conn.cursor() as cur:   # risale, e una seconda volta e' un no-op
            cur.execute(su)
            cur.execute(su)
        conn.commit()
        assert mondo["sql"]("SELECT count(*) FROM pg_constraint WHERE conname='properties_agent_same_agency_fk'")[0][0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# E. Shell eseguita (stub DOM, nessun database)
# ---------------------------------------------------------------------------

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_a30_13b_quick_booking_ui as a30_13b  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello E NON eseguito (BLOCKED)")

OPZIONI = {
    "territory": catalog.territory_tree(), "energy_classes": list(catalog.ENERGY_CLASSES),
    "property_types": [{"value": k, "label": v} for k, v in catalog.PROPERTY_TYPE_LABELS.items()],
    "can_assign": True,
    "agents": [{"id": 3, "role": "agent", "name": "Anna Agente", "is_me": True},
               {"id": 4, "role": "agent", "name": "Bruno Collega", "is_me": False}],
}
STORICO = {"id": 30, "code": None, "title": "Villa (titolo manuale)", "property_type": "villa",
           "commercial_status": "active", "classification": None, "region": None, "province": "teramo",
           "city": "Toretoreto Alto", "microzone": None, "address": "Via Mare", "civic_number": None,
           "energy_class": "g", "assigned_to": "Mario Rossi", "assigned_agent_id": None,
           "rooms": 3, "asking_price": "180000.00", "internal_notes": None,
           "contacts": [], "leads": [], "photos": [], "documents": [], "visits": []}
ASSEGNATO = {**STORICO, "id": 31, "code": "IMM-31", "region": "Abruzzo", "province": "TE",
             "city": "Tortoreto", "microzone": "Alto", "energy_class": "B",
             "assigned_agent_id": 4, "assigned_to": "Bruno Collega"}


def _rotte(sessione="agency_owner", *, opzioni=OPZIONI, post=None, patch=None, immobile=STORICO):
    rt = a30_5._rt()
    pid = immobile["id"]
    post = post or ({"status": 201, "body": {**STORICO, "id": 77, "code": "IMM-77"}},)
    patch = patch or ({"status": 200, "body": {**immobile, "asking_price": "200000.00"}},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/property/form-options", [rt.ok(opzioni)]),
        ("POST", "/api/property/properties", list(post)),
        ("PATCH", f"/api/property/properties/{pid}", list(patch)),
        ("GET", f"/api/property/properties/{pid}", [rt.ok(immobile)]),
        ("GET", "/api/property/properties/77", [rt.ok({**STORICO, "id": 77})]),
        ("GET", "/api/property/properties?", [rt.ok({"items": [STORICO, ASSEGNATO]})]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/match/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


E_HELPERS = r"""
const D = () => __dom.byId['content'].querySelectorAll('dialog').find((d) => d._open);
const q = (s) => D().querySelector(s);
const opz = (s) => q(s).querySelectorAll('option').map((o) => o.getAttribute('value'));
const etichette = (s) => q(s).querySelectorAll('option').map((o) => o.textContent);
async function scegli(s, v) {
  const el = q(s);
  if (v === '') { el.querySelectorAll('option').forEach((o) => { o.selected = o.getAttribute('value') === ''; }); el._value = undefined; }
  else el.value = String(v);
  el.dispatch('change'); await wait();
}
async function scrivi(s, v) { const el = q(s); el.value = v; el.dispatch('input'); }
async function salva() { q('#property-form').dispatch('submit'); await wait(); await wait(); }
function scritture() { return chiamate().filter((c) => ['POST', 'PATCH', 'PUT', 'DELETE'].includes(c.m)); }
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    rt = a30_5._rt()
    driver = staged.parent / "driver-crm-ops-2.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + a30_13b._extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n" + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n" + "await __settle(40);\n"
        + a30_5.HELPERS + E_HELPERS + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([a30_5.NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _scritture(out):
    return [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_e01_creazione_senza_titolo_ne_codice_con_cascata(staged):  # noqa: F811
    scenario = r"""
      await wait();
      bottone(__dom.byId['content'], '+ Nuovo immobile').dispatch('click'); await wait(); await wait();
      // SENTINELLA AGGIORNATA DA CREAZIONE-GUIDATA-1: «+ Nuovo immobile» apre la
      // procedura guidata; «Unita' autonoma» porta al form di sempre (POST
      // /properties, scheda commerciale) con il territorio gia' compilato.
      const passo1 = !!q('[data-wizard-step="1"]');
      await scegli('#wz-city', 'Fermo'); await scegli('#wz-microzone', 'Casabianca');
      await scrivi('#wz-address', 'Via Roma'); await scrivi('#wz-civic', '12');
      q('#wz-unknown').checked = true; q('#wz-unknown').dispatch('change');
      q('[data-wizard-step="1"]').dispatch('submit'); await wait();
      q('[data-path="autonomous"]').dispatch('click'); await wait(); await wait();
      const ids = D().querySelectorAll('input').map((i) => i.id);
      const campi = { titolo: ids.some((x) => /title/.test(x)), codice: ids.some((x) => /code/.test(x)), n: ids.length };
      const precompilato = { r: q('#pf-region').value, p: q('#pf-province').value, c: q('#pf-city').value,
                             z: q('#pf-microzone').value, via: q('#pf-address').value, civico: q('#pf-civic').value };
      const provMarche = opz('#pf-province');
      const comuniFM = opz('#pf-city');
      const zoneFermo = opz('#pf-microzone');
      // cambio provincia: comune e microzona incompatibili si azzerano, la regione resta
      await scegli('#pf-province', 'AP');
      const dopoCambio = { r: q('#pf-region').value, p: q('#pf-province').value, c: q('#pf-city').value, z: q('#pf-microzone').value };
      await scegli('#pf-city', 'Grottammare'); await scegli('#pf-microzone', 'Ascolani');
      await scegli('#pf-energy', 'A3'); await scegli('#pf-agent', 4);
      const energia = opz('#pf-energy');
      // doppio invio prima della risposta: una sola POST
      q('#property-form').dispatch('submit'); q('#property-form').dispatch('submit');
      await wait(); await wait();
      report({ campi, passo1, precompilato, provMarche, comuniFM, zoneFermo, dopoCambio, energia });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili")
    assert out["campi"]["titolo"] is False and out["campi"]["codice"] is False
    assert out["campi"]["n"] >= 8                   # il form e' stato davvero letto
    assert out["passo1"] is True
    assert out["precompilato"] == {"r": "Marche", "p": "FM", "c": "Fermo", "z": "Casabianca", "via": "Via Roma", "civico": "12"}
    assert out["provMarche"] == ["", "AP", "FM", "MC"]
    assert out["comuniFM"] == ["", "Altidona", "Campofilone", "Fermo", "Pedaso", "Porto San Giorgio", "Porto Sant’Elpidio"]
    assert out["zoneFermo"][1:] == PORTALE["zone"]["Fermo"]
    assert out["dopoCambio"] == {"r": "Marche", "p": "AP", "c": "", "z": ""}
    assert out["energia"] == ["", *catalog.ENERGY_CLASSES]
    post = _scritture(out)
    assert len(post) == 1 and post[0]["url"] == "/api/property/properties"
    body = post[0]["body"]
    assert "title" not in body and "code" not in body
    assert {k: body[k] for k in ("region", "province", "city", "microzone", "address", "civic_number",
                                 "energy_class", "assigned_agent_id")} == {
        "region": "Marche", "province": "AP", "city": "Grottammare", "microzone": "Ascolani",
        "address": "Via Roma", "civic_number": "12", "energy_class": "A3", "assigned_agent_id": 4}
    assert out["hash"] == "#/immobili/77"           # si apre la scheda del nuovo immobile


@node
def test_e02_modifica_precompilata_valori_storici_non_inviati(staged):  # noqa: F811
    scenario = r"""
      await wait();
      __dom.byId['content'].querySelector('#property-edit-btn').dispatch('click'); await wait(); await wait();
      const iniziale = {
        titolo: q('h2').textContent, provincia: q('#pf-province').value, comune: q('#pf-city').value,
        etProv: etichette('#pf-province'), etComune: etichette('#pf-city'), energia: q('#pf-energy').value,
        etEnergia: etichette('#pf-energy')[1], indirizzo: q('#pf-address').value, locali: q('#pf-rooms').value,
        agente: q('#pf-agent').value, nota: D().visibleText(), stato: !!q('#pf-status'),
      };
      await scrivi('#pf-price', '200000');
      await salva();
      report({ iniziale, header: __dom.byId['content'].querySelector('#property-header-title').textContent });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili/30")
    i = out["iniziale"]
    assert i["titolo"] == "Modifica immobile"
    assert i["provincia"] == "teramo" and "teramo (valore storico)" in i["etProv"]
    assert i["comune"] == "Toretoreto Alto" and "Toretoreto Alto (valore storico)" in i["etComune"]
    assert i["energia"] == "g" and i["etEnergia"] == "g (valore storico)"
    assert i["indirizzo"] == "Via Mare" and i["locali"] == "3"
    assert i["agente"] == "" and "Mario Rossi" in i["nota"]    # indicazione storica mostrata, non persa
    assert i["stato"] is False                                  # lo stato resta nella sua sezione
    patch = _scritture(out)
    assert len(patch) == 1 and patch[0]["url"] == "/api/property/properties/30"
    assert patch[0]["body"] == {"asking_price": 200000}        # SOLO cio' che e' cambiato
    assert out["header"] == "Via Mare, Toretoreto Alto"


@node
def test_e03_modifica_di_altri_campi_non_tocca_l_assegnazione(staged):  # noqa: F811
    scenario = r"""
      await wait();
      __dom.byId['content'].querySelector('#property-edit-btn').dispatch('click'); await wait(); await wait();
      const prima = { agente: q('#pf-agent').value, r: q('#pf-region').value, z: q('#pf-microzone').value };
      await scrivi('#pf-notes', 'nuova nota'); await salva();
      report({ prima });
    """
    out = _run(staged, scenario, _rotte(immobile=ASSEGNATO), "#/immobili/31")
    assert out["prima"] == {"agente": "4", "r": "Abruzzo", "z": "Alto"}
    body = _scritture(out)[0]["body"]
    assert body == {"internal_notes": "nuova nota"}
    assert "assigned_agent_id" not in body and "region" not in body


@node
def test_e04_agent_vede_l_agente_senza_menu(staged):  # noqa: F811
    scenario = r"""
      await wait();
      __dom.byId['content'].querySelector('#property-edit-btn').dispatch('click'); await wait(); await wait();
      report({ menu: !!q('#pf-agent'), testo: q('[data-agent-readonly]').textContent });
    """
    out = _run(staged, scenario, _rotte("agent", opzioni={**OPZIONI, "can_assign": False, "agents": []},
                                       immobile=ASSEGNATO), "#/immobili/31")
    assert out["menu"] is False and out["testo"] == "Bruno Collega"


@node
def test_e05_errore_e_annulla_senza_scritture_doppie(staged):  # noqa: F811
    scenario = r"""
      await wait();
      __dom.byId['content'].querySelector('#property-edit-btn').dispatch('click'); await wait(); await wait();
      await scrivi('#pf-price', '1'); await salva();
      const errore = q('#property-form-error').textContent; const aperto = !!D();
      q('#property-form-cancel').dispatch('click'); await wait();
      report({ errore, aperto, chiuso: !D() });
    """
    out = _run(staged, scenario, _rotte(patch=({"status": 400, "body": {"detail": "Comune non presente nel catalogo: X"}},)),
               "#/immobili/30")
    assert out["errore"] == "Comune non presente nel catalogo: X" and out["aperto"] and out["chiuso"]
    assert len(_scritture(out)) == 1


@node
def test_e06_lista_identifica_l_immobile_dai_dati(staged):  # noqa: F811
    out = _run(staged, "await wait(); report({});", _rotte(), "#/immobili")
    testo = out["content"]
    assert "Via Mare, Toretoreto Alto" in testo and "Tortoreto (Alto)" in testo
    assert "Villa (titolo manuale)" not in testo


def test_e07_layout_smartphone_e_niente_liste_scritte_a_mano():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* CRM-OPS-2"):]
    assert "@media (max-width: 767px)" in blocco
    assert ".property-form-dialog .form-grid-2, .property-form-dialog .form-grid-3 { grid-template-columns: 1fr; }" in blocco
    form = (ASSETS / "components" / "property-form.js").read_text(encoding="utf-8")
    for nome in ("Tortoreto", "Teramo", "Anna", "A4"):
        assert nome not in form, nome          # catalogo e agenti arrivano solo dall'API
    assert "/api/property/form-options" in form
    lista = (ASSETS / "views" / "immobili.js").read_text(encoding="utf-8")
    assert "np-title" not in lista and "Titolo *" not in lista and "np-code" not in lista
    scheda = (ASSETS / "views" / "immobile-dettaglio.js").read_text(encoding="utf-8")
    assert '<h2 id="property-header-title">${escapeHtml(propertyDisplayName(property))}</h2>' in scheda
    assert "const title = property.title" not in scheda
