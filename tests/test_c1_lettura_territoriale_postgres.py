"""AGENZIE-TERRITORI C1 - lettura territoriale condivisa degli immobili.

PostgreSQL usa-e-getta (schema completo dal runner), router veri, dati di
prova isolati (mai il database TEST operativo). Tre agenzie:
  1 «Uno»  competente per Alba Adriatica      (owner_a Supervisore, agent_a Collaboratore)
  2 «Due»  competente per Giulianova          (owner_b Supervisore, agent_b Collaboratore)
  3 «Tre»  nessun Comune assegnato            (owner_c)
La competenza e' configurata con le tabelle di rete esistenti (territorio
`municipality` + assegnazione attiva + alias `public_stima_comune`), come su TEST.

  s1  immobile di 1 a Giulianova: lo legge 1 (origine) e 2 (territoriale);
  s2  immobile di 2 ad Alba Adriatica: lo legge 2 (origine) e 1 (territoriale);
  s3  Collaboratore territoriale (agent_a): lettura si', scritture no;
  s4  Supervisore territoriale (owner_b): lettura si', scritture condivise ancora no;
  s5  agenzia non competente (3): niente, neppure per id, lista condivisa vuota;
  s6  Comune senza assegnazione, assegnazione sospesa, alias revocato: nessuna condivisione;
  s7  venditore collegato: recapiti solo nella scheda autorizzata; la scheda contatto e
      gli altri contatti dell'agenzia d'origine restano chiusi;
  s8  agenzia d'origine: scheda, lista e modifica come prima (piu' il blocco `access`);
  s9  nessuna apertura di edifici, unita', censimento, Zona, interazioni, Voice.
In chiusura: nessuna riga duplicata, `agency_id` invariato, righe di rete ripristinate.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, d15_attiva, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

TE = {"region": "Abruzzo", "province": "TE"}


def _operatore(m, email, nome, agenzia, ruolo):
    riga = _q(m, "SELECT id FROM operator_users WHERE email_normalized = %s", (email,))
    uid = riga[0][0] if riga else _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                                        "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s) RETURNING id", (email, email, nome))[0][0]
    if not _q(m, "SELECT 1 FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agenzia, uid)):
        _q(m, "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) VALUES (%s, %s, %s, 'active')",
           (agenzia, uid, ruolo))
    return uid


def _rete_d15(m, chiave, etichetta, agenzia):
    """Con ADM D15 attiva: territorio, assegnazione e alias della fixture
    riusati e riportati allo stato iniziale con soli UPDATE (nessun DELETE)."""
    riga = _q(m, "SELECT id FROM network_territories WHERE canonical_key = %s", (chiave,))
    t = riga[0][0] if riga else _q(
        m, "INSERT INTO network_territories (kind, canonical_key, label) VALUES ('municipality', %s, %s) RETURNING id",
        (chiave, etichetta))[0][0]
    _q(m, "UPDATE network_territories SET label = %s WHERE id = %s", (etichetta, t))
    _q(m, "UPDATE agency_territory_assignments SET status = 'revoked' "
          "WHERE territory_id = %s AND status <> 'revoked'", (t,))
    a = _q(m, "INSERT INTO agency_territory_assignments (territory_id, agency_id, status) VALUES (%s, %s, 'active') "
              "RETURNING id", (t, agenzia))[0][0]
    _q(m, "UPDATE network_territory_aliases SET status = 'revoked' WHERE territory_id = %s AND status <> 'revoked'", (t,))
    al = _q(m, "INSERT INTO network_territory_aliases (territory_id, source, match_value) "
               "VALUES (%s, 'public_stima_comune', %s) RETURNING id", (t, etichetta))[0][0]
    return {"territory": t, "assignment": a, "alias": al}


@pytest.fixture
def rete(mondo):
    """Agenzia 3, collaboratore di 2 e i due Comuni assegnati, con alias: righe di prova rimosse alla fine."""
    m = mondo
    tre = _q(m, "SELECT id FROM agencies WHERE slug = 'c1-tre'")
    tre = tre[0][0] if tre else _q(m, "INSERT INTO agencies (id, name, slug, status) "
                                    "VALUES ((SELECT max(id) + 1 FROM agencies), 'Tre', 'c1-tre', 'active') RETURNING id")[0][0]
    m["ruoli"]["agent_b"] = (_operatore(m, "c1.agent.b@x.test", "Bruno", 2, "agent"), 2, "agent")
    m["ruoli"]["owner_c"] = (_operatore(m, "c1.owner.c@x.test", "Carla", tre, "agency_owner"), tre, "agency_owner")
    creati = {}
    d15 = d15_attiva(m)
    for chiave, etichetta, agenzia in (("c1-alba-adriatica", "Alba Adriatica", 1), ("c1-giulianova", "Giulianova", 2)):
        if d15:
            creati[etichetta] = _rete_d15(m, chiave, etichetta, agenzia)
            continue
        t = _q(m, "INSERT INTO network_territories (kind, canonical_key, label) VALUES ('municipality', %s, %s) RETURNING id",
               (chiave, etichetta))[0][0]
        a = _q(m, "INSERT INTO agency_territory_assignments (territory_id, agency_id, status) VALUES (%s, %s, 'active') "
                  "RETURNING id", (t, agenzia))[0][0]
        al = _q(m, "INSERT INTO network_territory_aliases (territory_id, source, match_value) "
                   "VALUES (%s, 'public_stima_comune', %s) RETURNING id", (t, etichetta))[0][0]
        creati[etichetta] = {"territory": t, "assignment": a, "alias": al}
    try:
        yield {**m, "tre": tre, "rete": creati}
    finally:
        # Con ADM D15 nel database di prova: le righe di rete non si
        # cancellano piu'; restano e la prossima fixture le riporta allo stato
        # iniziale con UPDATE (`_rete_d15`).
        # Il catalogo si rilegge qui: D15 puo' essere stata applicata dopo
        # l'apertura della fixture (`test_adm_d15_activation_postgres`).
        for v in ([] if d15 or d15_attiva(m) else creati.values()):
            _q(m, "DELETE FROM network_territory_aliases WHERE territory_id = %s", (v["territory"],))
            _q(m, "DELETE FROM agency_territory_assignments WHERE territory_id = %s", (v["territory"],))
            _q(m, "DELETE FROM network_territories WHERE id = %s", (v["territory"],))
        _q(m, "DELETE FROM property_contacts WHERE property_id > 25")
        _q(m, "DELETE FROM contacts WHERE display_name IN ('Riservato Uno', 'Venditore Giulianova')")


def _immobile(m, chi, city, **kw):
    corpo = {"title": f"C1 {city} {uuid.uuid4().hex[:5]}", **TE, "city": city, "address": "Via Prova", "civic_number": "1", **kw}
    r = m["api"](chi).post("/api/property/properties", json=corpo)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _ids(r):
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def test_c1_lettura_territoriale(rete):
    m = rete
    api = m["api"]
    prima_n = _q(m, "SELECT count(*) FROM properties")[0][0]

    p1 = _immobile(m, "owner_a", "Giulianova")            # origine 1, Comune di 2
    p2 = _immobile(m, "owner_b", "Alba Adriatica")        # origine 2, Comune di 1
    p_mart = _immobile(m, "owner_a", "Martinsicuro")      # Comune senza assegnazione
    p_casa = _immobile(m, "owner_a", "Alba Adriatica")    # origine 1 nel proprio Comune: mai condiviso
    altro = _q(m, "INSERT INTO contacts (agency_id, display_name, phone) VALUES (1, 'Riservato Uno', '3330000000') RETURNING id")[0][0]
    vend = _q(m, "INSERT INTO contacts (agency_id, display_name, email, phone) "
                 "VALUES (1, 'Venditore Giulianova', 'v.g@x.test', '3391111111') RETURNING id")[0][0]
    r = api("owner_a").post(f"/api/property/properties/{p1['id']}/contacts", json={"contact_id": vend, "role": "owner"})
    assert r.status_code == 201, r.text

    # s1 - immobile di 1 a Giulianova
    r = api("owner_a").get(f"/api/property/properties/{p1['id']}")
    assert r.status_code == 200 and r.json()["access"]["mode"] == "origin" and r.json()["access"]["read_only"] is False
    for chi in ("owner_b", "agent_b"):
        r = api(chi).get(f"/api/property/properties/{p1['id']}")
        assert r.status_code == 200, (chi, r.text)
        acc = r.json()["access"]
        assert (acc["mode"], acc["reason"], acc["origin_agency_id"], acc["read_only"]) == ("territorial", "territorial_competence", 1, True)
        assert acc["territory"]["label"] == "Giulianova" and acc["origin_agency_name"]
        assert r.json()["agency_id"] == 1
        assert p1["id"] in _ids(api(chi).get("/api/property/properties/shared"))
        assert p1["id"] not in _ids(api(chi).get("/api/property/properties"))       # la lista di sempre non cambia
    assert p1["id"] in _ids(api("owner_a").get("/api/property/properties"))
    assert _ids(api("owner_a").get("/api/property/properties/shared")).isdisjoint({p1["id"], p_casa["id"], p_mart["id"]})

    # s2 - direzione opposta
    for chi in ("owner_a", "agent_a"):
        r = api(chi).get(f"/api/property/properties/{p2['id']}")
        assert r.status_code == 200 and r.json()["access"]["mode"] == "territorial" and r.json()["agency_id"] == 2, r.text
        assert p2["id"] in _ids(api(chi).get("/api/property/properties/shared"))
    assert api("owner_b").get(f"/api/property/properties/{p2['id']}").json()["access"]["mode"] == "origin"
    # filtri, ricerca e paginazione della lista condivisa
    assert _ids(api("owner_a").get("/api/property/properties/shared", params={"search": p2["title"]})) == {p2["id"]}
    assert _ids(api("owner_a").get("/api/property/properties/shared", params={"city": "Giulianova"})) == set()
    assert len(api("owner_a").get("/api/property/properties/shared", params={"limit": 1}).json()["items"]) <= 1
    assert api("owner_a").get("/api/property/properties/shared", params={"offset": 1000}).json()["items"] == []

    # s3 / s4 - scritture negate a Collaboratore e Supervisore territoriali
    for chi in ("agent_b", "owner_b"):
        c = api(chi)
        rifiuti = [
            c.patch(f"/api/property/properties/{p1['id']}", json={"asking_price": 1}),
            c.post(f"/api/property/properties/{p1['id']}/trash", json={"reason_code": "duplicate"}),
            c.post(f"/api/property/properties/{p1['id']}/contacts", json={"contact_id": vend, "role": "seller"}),
            c.delete(f"/api/property/properties/{p1['id']}/contacts/{vend}/owner"),
            c.post(f"/api/property/properties/{p1['id']}/interactions", json={"interaction_type": "note", "note": "x"}),
            c.post(f"/api/property/properties/{p1['id']}/position", json={"latitude": 42.7, "longitude": 13.9}),
        ]
        for r in rifiuti:
            assert r.status_code == 404, (chi, r.request.method, r.request.url, r.status_code, r.text)
            assert r.status_code != 200 and r.status_code != 201
    riga = _q(m, "SELECT agency_id, asking_price, to_jsonb(properties)->>'deleted_at' FROM properties WHERE id = %s", (p1["id"],))[0]
    assert riga[0] == 1 and riga[1] != 1 and riga[2] is None
    assert _q(m, "SELECT count(*) FROM property_contacts WHERE property_id = %s", (p1["id"],))[0][0] == 1

    # s5 - agenzia non competente
    for pid in (p1["id"], p2["id"], p_mart["id"], p_casa["id"]):
        assert api("owner_c").get(f"/api/property/properties/{pid}").status_code == 404
    assert _ids(api("owner_c").get("/api/property/properties/shared")) == set()
    assert _ids(api("owner_c").get("/api/property/properties", params={"record_kind": "all"})).isdisjoint({p1["id"], p2["id"]})
    assert api("owner_c").get("/api/property/properties/999999999").status_code == 404   # stesso esito di un id inesistente

    # s6 - Comune senza assegnazione / assegnazione sospesa / alias revocato
    assert api("owner_b").get(f"/api/property/properties/{p_mart['id']}").status_code == 404
    assert api("owner_b").get(f"/api/property/properties/{p_casa['id']}").status_code == 404      # Comune di 1: B non competente
    giul = m["rete"]["Giulianova"]
    _q(m, "UPDATE agency_territory_assignments SET status = 'suspended' WHERE id = %s", (giul["assignment"],))
    assert api("owner_b").get(f"/api/property/properties/{p1['id']}").status_code == 404
    assert p1["id"] not in _ids(api("owner_b").get("/api/property/properties/shared"))
    _q(m, "UPDATE agency_territory_assignments SET status = 'active' WHERE id = %s", (giul["assignment"],))
    _q(m, "UPDATE network_territory_aliases SET status = 'revoked' WHERE id = %s", (giul["alias"],))
    assert api("owner_b").get(f"/api/property/properties/{p1['id']}").status_code == 404
    _q(m, "UPDATE network_territory_aliases SET status = 'active' WHERE id = %s", (giul["alias"],))
    assert api("owner_b").get(f"/api/property/properties/{p1['id']}").status_code == 200

    # s7 - venditore: recapiti solo nella scheda autorizzata
    scheda = api("owner_b").get(f"/api/property/properties/{p1['id']}").json()
    contatti = {c["contact_id"]: c for c in scheda["contacts"]}
    assert set(contatti) == {vend}
    assert (contatti[vend]["display_name"], contatti[vend]["email"], contatti[vend]["phone"]) == ("Venditore Giulianova", "v.g@x.test", "3391111111")
    assert scheda["documents"] == [] and scheda["visits"] == [] and "building" not in scheda
    from core import service as core_service
    from core.exceptions import NotFoundError
    m["stato"]["chi"] = "owner_b"
    with pytest.raises(NotFoundError):
        core_service.get_contact(m["ctx"](), vend)
    with pytest.raises(NotFoundError):
        core_service.get_contact(m["ctx"](), altro)
    nomi = {c["display_name"] for c in core_service.list_contacts(m["ctx"](), 200, 0, None, None)}
    assert "Venditore Giulianova" not in nomi and "Riservato Uno" not in nomi
    assert _ids(api("owner_b").get("/api/property/properties/shared", params={"record_kind": "all"})) & {p_casa["id"]} == set()

    # s8 - l'origine come prima
    r = api("owner_a").patch(f"/api/property/properties/{p1['id']}", json={"asking_price": 250000})
    assert r.status_code == 200 and float(r.json()["asking_price"]) == 250000.0, r.text
    assert float(api("owner_b").get(f"/api/property/properties/{p1['id']}").json()["asking_price"]) == 250000.0   # B vede l'evoluzione
    r = api("agent_a").get(f"/api/property/properties/{p_casa['id']}")
    assert r.status_code == 200 and r.json()["access"]["mode"] == "origin"

    # s9 - nessuna apertura di edifici, unita', censimento, Zona, interazioni, Voice
    e = _edificio(m, **TE, city="Giulianova", microzone=None, address=f"Via Palazzo {uuid.uuid4().hex[:5]}",
                  civic_number="2", name=None, units_declared=None, units_declared_source=None)
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    b = api("owner_b")
    assert b.get(f"/api/property/buildings/{e['id']}").status_code == 404
    assert e["id"] not in {x["id"] for x in b.get("/api/property/buildings").json()["items"]}
    assert b.get(f"/api/property/buildings/{e['id']}/position").status_code == 404
    assert b.get(f"/api/property/properties/{p1['id']}/position").status_code == 404
    assert b.get(f"/api/property/properties/{p1['id']}/interactions").status_code == 404
    assert b.get(f"/api/property/properties/{u['id']}/census").status_code == 404
    assert p1["id"] not in {x["id"] for x in b.get("/api/property/properties/positions").json()["items"]}
    assert b.get(f"/api/property/buildings/{e['id']}/deletion-request").status_code == 404
    # l'unita' dell'edificio e' un immobile di Giulianova: si legge da sola, senza aprire l'edificio
    r = b.get(f"/api/property/properties/{u['id']}")
    assert r.status_code == 200 and r.json()["access"]["mode"] == "territorial" and "building" not in r.json()
    from core.database import core_cursor
    from voice import repository as voice_repository
    m["stato"]["chi"] = "owner_b"
    with core_cursor() as (_, cur):
        assert voice_repository.properties_by_code(m["ctx"](), cur, p1["code"]) == []

    # chiusura: nessun duplicato, agency_id invariato
    assert _q(m, "SELECT count(*) FROM properties")[0][0] == prima_n + 5
    assert dict(_q(m, "SELECT id, agency_id FROM properties WHERE id = ANY(%s)", ([p1["id"], p2["id"], p_mart["id"], p_casa["id"]],))) == {
        p1["id"]: 1, p2["id"]: 2, p_mart["id"]: 1, p_casa["id"]: 1}


def test_c1_helper_senza_rami_aperti():
    """Il predicato territoriale non ha rami `TRUE`, filtra sempre sull'assegnazione ATTIVA e sul
    tipo `municipality`, e la lettura territoriale non tocca nessuna scrittura."""
    import inspect
    from property import territorial_access as ta
    sql, params = ta.territorial_predicate("p", 7)
    assert params == [7] and "TRUE" not in sql.upper().replace("IS NOT NULL", "")
    for pezzo in ("ata.status = 'active'", "nt.kind = 'municipality'", "nta.status = 'active'",
                  "nta.source = 'public_stima_comune'", "ag.status = 'active'", "ata.agency_id = %s"):
        assert pezzo in sql, pezzo
    sorgente = inspect.getsource(ta)
    for vietato in ("UPDATE ", "INSERT ", "DELETE ", "commit=True"):
        assert vietato not in sorgente, vietato
