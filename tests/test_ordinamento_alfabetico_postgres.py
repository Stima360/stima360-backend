"""ORDINAMENTO ALFABETICO A–Z / Z–A degli elenchi del CRM su PostgreSQL VERO.

Banco: la fixture `mondo` di DELETE-ARCH Fase 0 (tutte le migration, router
veri). I tre elenchi:

    O1  Contatti   (`GET /api/core/contacts?sort=name_asc|name_desc`): il nome come
        l'elenco lo mostra; maiuscole, accenti e punteggiatura iniziale non contano;
        un nome senza lettere va in fondo in entrambe le direzioni; insieme a ricerca
        e paginazione; l'agente vede le stesse schede di prima, nello stesso ordine;
        senza `sort` l'ordine di sempre (i piu' recenti)
    O2  Immobili   (`GET /api/property/properties/accessible?sort=address_asc|...`):
        indirizzo (o il titolo, senza indirizzo), civico come numero («2» prima di
        «10»); insieme a ricerca, filtri (comune, stato) e paginazione; l'altra
        agenzia non vede nulla; senza `sort` l'ordine di sempre
    O3  Incarichi  (`GET /api/property/mandates?sort=address_asc|address_desc`):
        indirizzo dell'immobile collegato, poi il nome del cliente; insieme a
        ricerca e filtri
    O4  valori non ammessi -> 422, mai interpolati nella query
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _codice, _collega, _contatto, _immobile, _q, completo, mondo, operatori,
)
from tests.test_delete_arch_0_postgres import _futuro

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

CONTACTS = "/api/core/contacts"
ACCESSIBLE = "/api/property/properties/accessible"
MANDATES = "/api/property/mandates"
TOKEN = "ordtok"
CITTA = "Ordinopoli"


# ---------------------------------------------------------------------------
# O1 - Contatti
# ---------------------------------------------------------------------------

def _persona(m, display, first=None, last=None, agent=None, company=None, kind="person"):
    mail = f"{TOKEN}-{uuid.uuid4().hex[:10]}@x.test"          # la ricerca per TOKEN isola le schede del test
    return _q(m, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name, company_name, "
                 "email, email_normalized, assigned_agent_id) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
              (kind, display, first, last, company, mail, mail, agent))[0][0]


def _contatti(m, chi="owner_a", **params):
    corpo = _codice(m["api"](chi).get(CONTACTS, params={"search": TOKEN, "limit": 200, **params}), 200)
    return [c["id"] for c in corpo["items"]]


def test_o1_contatti_a_z_e_z_a_ignorano_maiuscole_accenti_e_punteggiatura(mondo):
    m = mondo
    agente = m["ids"]["agent_a"]
    ids = {
        "zoe": _persona(m, "Zoe Alberti", "Zoe", "Alberti"),
        "elena": _persona(m, "Élena Bianchi", "Élena", "Bianchi", agent=agente),
        "anna_v": _persona(m, "anna Verdi", "anna", "Verdi"),
        "anna_r": _persona(m, "ANNA Rossi", "ANNA", "Rossi", agent=agente),
        "studio": _persona(m, None, kind="company", company="«Studio Beta»"),
        "nicolo": _persona(m, "  Nicolò Esposito", "Nicolò", "Esposito"),
        "vuoto": _persona(m, "...", agent=agente),                       # nessuna lettera: sempre in fondo
        "rossi_m": _persona(m, "Rossi Mario", "Mario", "Rossi"),          # ordinato come lo si legge
    }
    atteso_az = [ids[k] for k in ("anna_r", "anna_v", "elena", "nicolo", "rossi_m", "studio", "zoe")] + [ids["vuoto"]]
    assert _contatti(m, sort="name_asc") == atteso_az
    atteso_za = [ids[k] for k in ("zoe", "studio", "rossi_m", "nicolo", "elena", "anna_v", "anna_r")] + [ids["vuoto"]]
    assert _contatti(m, sort="name_desc") == atteso_za

    # insieme alla ricerca: solo le schede che la ricerca trova, nello stesso ordine
    assert _codice(m["api"]().get(CONTACTS, params={"search": "anna", "sort": "name_asc"}), 200)["items"][0]["id"] \
        == ids["anna_r"]
    trovati = [c["id"] for c in _codice(m["api"]().get(CONTACTS, params={"search": "anna", "sort": "name_desc"}),
                                        200)["items"]]
    assert trovati.index(ids["anna_v"]) < trovati.index(ids["anna_r"])

    # paginazione stabile: le pagine ricompongono l'elenco intero, senza buchi ne' doppioni
    pagine = [x for off in range(0, 8, 3) for x in _contatti(m, sort="name_asc", limit=3, offset=off)]
    assert pagine == atteso_az

    # permessi invariati: l'agente vede le stesse schede di prima (solo le sue), nello stesso ordine
    visibili = set(_contatti(m, "agent_a"))
    assert visibili == {ids["elena"], ids["anna_r"], ids["vuoto"]}
    assert _contatti(m, "agent_a", sort="name_asc") == [i for i in atteso_az if i in visibili]
    assert _contatti(m, "agent_a", sort="name_desc") == [i for i in atteso_za if i in visibili]
    # l'altra agenzia non vede nulla, in nessun ordine
    assert _contatti(m, "owner_b", sort="name_asc") == [] == _contatti(m, "owner_b", sort="name_desc")

    # senza `sort`: l'ordine di sempre (i piu' recenti), per la ricerca globale e i selettori
    recenti = [r[0] for r in _q(m, "SELECT id FROM contacts WHERE email_normalized LIKE %s "
                                   "ORDER BY created_at DESC, id DESC", (f"{TOKEN}-%",))]
    assert _contatti(m) == recenti


def test_o1_contatti_con_filtro_di_stato(mondo):
    m = mondo
    a = _persona(m, "Bruno Archivio", "Bruno", "Archivio")
    b = _persona(m, "Aldo Attivo", "Aldo", "Attivo")
    c = _persona(m, "Carla Inattiva", "Carla", "Inattiva")
    _q(m, "UPDATE contacts SET status = 'inactive' WHERE id IN (%s, %s)", (a, c))
    assert _contatti(m, status="inactive", sort="name_asc") == [a, c]
    assert _contatti(m, status="inactive", sort="name_desc") == [c, a]
    assert b not in _contatti(m, status="inactive", sort="name_asc")


# ---------------------------------------------------------------------------
# O2 - Immobili
# ---------------------------------------------------------------------------

def _immobili(m, chi="owner_a", **params):
    corpo = _codice(m["api"](chi).get(ACCESSIBLE, params={"city": CITTA, "limit": 200, **params}), 200)
    return [r["id"] for r in corpo["items"]], corpo


def _crea_immobili(m):
    def crea(titolo, indirizzo, civico):
        pid = _immobile(m, title=titolo, address="Via Provvisoria", civic_number="1", city=CITTA)["id"]
        _q(m, "UPDATE properties SET address = %s, civic_number = %s WHERE id = %s", (indirizzo, civico, pid))
        return pid
    return {
        "roma10": crea("Bilocale", "Via Roma", "10"),
        "roma2": crea("Trilocale", "via ROMA", "2"),
        "ercole": crea("Villa", "Viale Èrcole", "1"),
        "corso": crea("Negozio", "  Corso Alfa", "5"),
        "attico": crea("Attico panoramico", "", None),                 # senza indirizzo: conta il titolo
        "vuoto": crea("...", "...", None),                              # nessuna lettera: sempre in fondo
    }


def test_o2_immobili_per_indirizzo_civico_numerico_vuoti_in_fondo(mondo):
    m = mondo
    ids = _crea_immobili(m)
    az = [ids[k] for k in ("attico", "corso", "roma2", "roma10", "ercole", "vuoto")]
    za = [ids[k] for k in ("ercole", "roma10", "roma2", "corso", "attico", "vuoto")]
    righe, corpo = _immobili(m, sort="address_asc")
    assert righe == az and corpo["sort"] == "address_asc"
    assert _immobili(m, sort="address_desc")[0] == za

    # con la ricerca e con il filtro di stato
    assert _immobili(m, search="roma", sort="address_asc")[0] == [ids["roma2"], ids["roma10"]]
    assert _immobili(m, search="roma", sort="address_desc")[0] == [ids["roma10"], ids["roma2"]]
    _q(m, "UPDATE properties SET commercial_status = 'reserved' WHERE id IN (%s, %s)", (ids["ercole"], ids["corso"]))
    assert _immobili(m, status="reserved", sort="address_asc")[0] == [ids["corso"], ids["ercole"]]
    assert _immobili(m, status="reserved", sort="address_desc")[0] == [ids["ercole"], ids["corso"]]

    # paginazione: conteggi invariati, pagine coerenti
    _, tutto = _immobili(m, sort="address_asc")
    pagine = [x for off in (0, 4) for x in _immobili(m, sort="address_asc", limit=4, offset=off)[0]]
    assert pagine == az and tutto["total"] == len(az) and tutto["counts"]["mine"] == len(az)

    # vista «miei» uguale; l'altra agenzia non vede nulla
    assert _immobili(m, scope="mine", sort="address_asc")[0] == az
    assert _immobili(m, "owner_b", sort="address_asc")[0] == []

    # senza `sort`: l'ordine di sempre (ultima modifica)
    recenti = [r[0] for r in _q(m, "SELECT id FROM properties WHERE city = %s ORDER BY updated_at DESC, id DESC",
                                (CITTA,))]
    righe, corpo = _immobili(m)
    assert righe == recenti and corpo["sort"] is None


# ---------------------------------------------------------------------------
# O3 - Incarichi
# ---------------------------------------------------------------------------

def _incarichi(m, chi="owner_a", **params):
    corpo = _codice(m["api"](chi).get(MANDATES, params={"city": CITTA, "limit": 200, **params}), 200)
    return [r["property_id"] for r in corpo["items"]]


def _incarico(m, indirizzo, civico, cliente, giorno):
    """Acquisizione (sopralluogo in un giorno suo, niente conflitti d'Agenda) ->
    «Genera incarico»: il percorso reale di FIX-MANDATE-1."""
    pid = _immobile(m, title="Incarico", address="Via Provvisoria", civic_number="1", city=CITTA)["id"]
    _q(m, "UPDATE properties SET address = %s, civic_number = %s WHERE id = %s", (indirizzo, civico, pid))
    cid = _contatto(m, cliente)
    _collega(m, pid, cid, "owner", True)
    corpo = {"property_id": pid, "owner_contact_id": cid,
             "appointment": {"start_at": _futuro(30 + giorno).isoformat(), "assigned_user_id": m["ids"]["owner_a"],
                             "client_request_id": str(uuid.uuid4())}}
    acq = _codice(m["api"]().post("/api/acquisitions", json=corpo), 201)
    _q(m, "UPDATE acquisitions SET status = 'inspection_done' WHERE id = %s", (acq["id"],))
    versione = _q(m, "SELECT version FROM acquisitions WHERE id = %s", (acq["id"],))[0][0]
    _codice(m["api"]().post(f"/api/acquisitions/{acq['id']}/mandate", json={
        "version": versione, "mandate_type": "Esclusiva", "mandate_start": date.today().isoformat(),
        "mandate_end": (date.today() + timedelta(days=180)).isoformat()}), 200)
    return pid


def test_o3_incarichi_per_indirizzo_poi_cliente(mondo):
    m = mondo
    ids = {
        "garibaldi_bruno": _incarico(m, "Via Garibaldi", "3", "Bruno Neri", 1),
        "garibaldi_anna": _incarico(m, "via garibaldi", "3", "Anna Zeta", 2),      # stesso indirizzo: decide il cliente
        "garibaldi_12": _incarico(m, "Via Garibaldi", "12", "Carlo Bianchi", 3),
        "duomo": _incarico(m, "Piazza Duomo", "1", "Ètore Rossi", 4),
        "aurora": _incarico(m, "Corso Aurora", "7", "Zeno Verdi", 5),
    }
    az = [ids[k] for k in ("aurora", "duomo", "garibaldi_anna", "garibaldi_bruno", "garibaldi_12")]
    assert _incarichi(m, sort="address_asc") == az
    assert _incarichi(m, sort="address_desc") == list(reversed(az))

    # con la ricerca (anche per nome del cliente) e con il filtro del tipo
    assert _incarichi(m, search="garibaldi", sort="address_asc") == az[2:]
    assert _incarichi(m, search="garibaldi", sort="address_desc") == list(reversed(az[2:]))
    assert _incarichi(m, search="Rossi", sort="address_desc") == [ids["duomo"]]
    assert _incarichi(m, mandate_type="Esclusiva", sort="address_asc") == az

    # gli altri ordinamenti restano e l'altra agenzia non vede nulla
    assert sorted(_incarichi(m, sort="expiry")) == sorted(az)
    assert _incarichi(m, "owner_b", sort="address_asc") == []


# ---------------------------------------------------------------------------
# O4 - valori non ammessi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("percorso,valore", [
    (CONTACTS, "address_asc"), (CONTACTS, "id; DROP TABLE contacts"),
    (ACCESSIBLE, "name_asc"), (ACCESSIBLE, "address_asc, p.id"),
])
def test_o4_ordinamento_non_ammesso_e_rifiutato(mondo, percorso, valore):
    assert mondo["api"]().get(percorso, params={"sort": valore}).status_code == 422


def test_o4_incarichi_ordinamento_non_ammesso(mondo):
    r = mondo["api"]().get(MANDATES, params={"sort": "p.id DESC"})
    assert r.status_code in (400, 422), r.text
