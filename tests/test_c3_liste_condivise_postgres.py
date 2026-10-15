"""AGENZIE-TERRITORI C3 - l'elenco «Immobili» unificato: propri + condivisi.

PostgreSQL usa-e-getta, router veri, la stessa rete di prova di C1 (fixture
`rete`): 1 «Uno» competente per Alba Adriatica, 2 «Due» per Giulianova, 3
«Tre» senza Comuni.

  u1  `scope=all` = lista di sempre (`/properties`) UNITO lista condivisa C1
      (`/properties/shared`), senza duplicati, con gli stessi filtri;
  u2  `scope=mine` = lista di sempre; `scope=shared` = lista condivisa C1;
  u3  conteggi delle tre viste coerenti con i filtri (all = mine + shared);
  u4  paginazione: pagine piccole concatenate = la pagina intera, ordinata
      come la lista di sempre (updated_at DESC, id DESC), niente persi o doppi;
  u5  ricerca, Comune, stato commerciale, classificazione, archiviati;
  u6  agenzia non competente: nessuna scheda di terzi; Collaboratore = stessa lettura;
  u7  righe condivise: blocco `access` territoriale, agenzia d'origine, nessun dato
      interno (assegnatario, conteggi di documenti e visite); scritture ancora negate;
  u8  `scope` diverso -> 422; la lista di sempre resta identica (nessuna riga di terzi).
"""
from __future__ import annotations

import pytest

from tests.test_c1_lettura_territoriale_postgres import _immobile, _operatore, rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

URL = "/api/property/properties/accessible"


def _tutte(api, url, **params):
    """Tutte le righe di una lista, una pagina da 200 alla volta."""
    out, offset = [], 0
    while True:
        r = api.get(url, params={**params, "limit": 200, "offset": offset})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        out += items
        if len(items) < 200:
            return out
        offset += 200


def _ordine(righe):
    return sorted(righe, key=lambda x: (x["updated_at"], x["id"]), reverse=True)


def test_c3_lista_unificata(rete):
    m = rete
    api = m["api"]
    p1 = _immobile(m, "owner_a", "Giulianova", classification="A")      # di 1, condiviso con 2
    p1b = _immobile(m, "agent_a", "Giulianova")                          # di 1 (inserito dal Collaboratore), condiviso con 2
    p2 = _immobile(m, "owner_b", "Alba Adriatica")                       # di 2, condiviso con 1
    p_mart = _immobile(m, "owner_a", "Martinsicuro")                     # Comune senza assegnazione: mai condiviso
    p_casa = _immobile(m, "owner_b", "Giulianova", commercial_status="active")   # di 2 nel proprio Comune
    archiviato = _immobile(m, "owner_a", "Giulianova")
    assert api("owner_a").post(f"/api/property/properties/{archiviato['id']}/archive").status_code == 200

    for chi in ("owner_b", "agent_b", "owner_a", "agent_a", "owner_c"):
        c = api(chi)
        # u1 / u2 - stessi insiemi delle due liste esistenti
        miei = _tutte(c, "/api/property/properties")
        condivisi = _tutte(c, "/api/property/properties/shared")
        tutti = _tutte(c, URL)
        id_tutti = [x["id"] for x in tutti]
        assert len(id_tutti) == len(set(id_tutti)), chi                                    # nessun duplicato
        assert set(id_tutti) == {x["id"] for x in miei} | {x["id"] for x in condivisi}, chi
        assert {x["id"] for x in _tutte(c, URL, scope="mine")} == {x["id"] for x in miei}, chi
        assert {x["id"] for x in _tutte(c, URL, scope="shared")} == {x["id"] for x in condivisi}, chi
        # u3 - conteggi
        corpo = c.get(URL, params={"limit": 1}).json()
        assert corpo["counts"] == {"all": len(tutti), "mine": len(miei), "shared": len(condivisi)}, chi
        assert corpo["total"] == len(tutti) and corpo["scope"] == "all"
        for scope in ("mine", "shared"):
            assert c.get(URL, params={"scope": scope, "limit": 1}).json()["total"] == corpo["counts"][scope]
        # u4 - ordinamento e pagine piccole
        assert [x["id"] for x in _ordine(tutti)] == id_tutti, chi
        a_pezzi, offset = [], 0
        while offset <= len(tutti):
            pagina = c.get(URL, params={"limit": 2, "offset": offset}).json()
            assert pagina["counts"] == corpo["counts"]
            a_pezzi += [x["id"] for x in pagina["items"]]
            offset += 2
        assert a_pezzi == id_tutti, chi

    # chi vede che cosa
    def ids(chi, **params):
        return {x["id"] for x in _tutte(api(chi), URL, **params)}

    for chi in ("owner_b", "agent_b"):
        assert {p1["id"], p1b["id"], p2["id"], p_casa["id"]} <= ids(chi)
        assert p_mart["id"] not in ids(chi) and archiviato["id"] not in ids(chi)
        assert {p1["id"], p1b["id"]} <= ids(chi, scope="shared") and p2["id"] not in ids(chi, scope="shared")
        assert p1["id"] not in ids(chi, scope="mine")
    for chi in ("owner_a", "agent_a"):
        assert {p1["id"], p1b["id"], p_mart["id"], p2["id"]} <= ids(chi)
        assert ids(chi, scope="shared").isdisjoint({p1["id"], p1b["id"], p_mart["id"], p_casa["id"]})
    # u6 - agenzia non competente
    assert ids("owner_c").isdisjoint({p1["id"], p1b["id"], p2["id"], p_mart["id"], p_casa["id"]})
    assert api("owner_c").get(URL).json()["counts"]["shared"] == 0

    # u5 - filtri (stesso significato della lista di sempre)
    c = api("owner_b")
    assert ids("owner_b", search=p1["title"]) == {p1["id"]}
    assert ids("owner_b", city="Giulianova") >= {p1["id"], p1b["id"], p_casa["id"]}
    assert p2["id"] not in ids("owner_b", city="Giulianova")
    giu = c.get(URL, params={"city": "Giulianova", "limit": 1}).json()["counts"]
    assert giu["shared"] >= 2 and giu["mine"] >= 1 and giu["all"] == giu["mine"] + giu["shared"]
    assert ids("owner_b", status="active") >= {p_casa["id"]}
    assert ids("owner_b", status="active").isdisjoint({p1["id"], p1b["id"]})
    assert p1["id"] in ids("owner_b", classification="A") and p1b["id"] not in ids("owner_b", classification="A")
    assert archiviato["id"] in ids("owner_b", include_archived="true")
    assert archiviato["id"] in ids("owner_b", status="archived")
    for filtri in ({"search": p1["title"]}, {"city": "Giulianova"}, {"status": "active"}, {"classification": "A"}):
        for scope, lista in (("mine", "/api/property/properties"), ("shared", "/api/property/properties/shared")):
            if scope == "shared" and "classification" in filtri:
                continue                                       # la lista C1 non ha questo filtro: verificato sopra
            atteso = {x["id"] for x in _tutte(c, lista, **filtri)}
            assert ids("owner_b", scope=scope, **filtri) == atteso, (scope, filtri)

    # u7 - righe condivise: solo i dati autorizzati
    riga = next(x for x in _tutte(c, URL) if x["id"] == p1["id"])
    assert riga["access"] == {"mode": "territorial", "reason": "territorial_competence", "origin_agency_id": 1,
                              "origin_agency_name": riga["origin_agency_name"], "read_only": True}
    assert riga["origin_agency_name"] and riga["city"] == "Giulianova"
    assert riga["assigned_agent_id"] is None
    for chiave in ("document_issues", "upcoming_visits", "readiness_score", "agency_id", "owner_notes", "private_notes"):
        assert chiave not in riga
    propria = next(x for x in _tutte(c, URL) if x["id"] == p_casa["id"])
    assert propria["access"]["mode"] == "origin" and propria["access"]["read_only"] is False
    for chi in ("owner_b", "agent_b"):
        assert api(chi).patch(f"/api/property/properties/{p1['id']}", json={"asking_price": 1}).status_code == 404
    assert _q(m, "SELECT agency_id, asking_price FROM properties WHERE id = %s", (p1["id"],))[0] == [1, None]

    # u8
    assert c.get(URL, params={"scope": "tutti"}).status_code == 422
    assert {p1["id"], p1b["id"]}.isdisjoint({x["id"] for x in _tutte(c, "/api/property/properties")})
