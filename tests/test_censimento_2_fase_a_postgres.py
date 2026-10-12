"""CENSIMENTO-2 Fase A - «Dati catastali e superficie», conferma dell'interno e
verifica delle schede, su PostgreSQL VERO (database locale usa-e-getta di
P29_TEST_DSN, schema completo dal runner, rotte vere del router Immobili).

  a01  salvataggio atomico di tutti i campi in UNA transazione, UNA riga di
       storico (agenzia, autore, data, campo, prima, dopo); stessa scheda;
       nessuna propagazione allo stato commerciale; richiesta senza
       modifiche = nessuna scrittura;
  a02  atomicita': errore DOPO l'UPDATE (storico, indice unico) -> nessun dato
       e nessuna attivita'; ogni rifiuto di validazione prima dell'UPDATE;
  a03  duplicato catastale: 409 CADASTRAL_DUPLICATE con il codice esistente;
       sezione «non conosciuta» non blocca (stessa regola della 083);
  a04  isolamento fra agenzie: 404, nessuna modifica, nessuno storico;
  a05  autorizzazioni: elenco chiuso (422), le stesse della PATCH generica
       (D1), archiviata (409), nel Cestino (409);
  a06  storico persistente e non cancellabile;
  a07  numerazione provvisoria e «Conferma interno»: correzione senza
       conferma resta provvisoria; conferma = stessa scheda, collegamenti e
       storico intatti; interno mancante, scale diverse, posizione gia' usata;
  a08  verifica: tre condizioni per scheda e tre indicatori separati;
  a09  gli altri percorsi (PATCH generica, modifica di gruppo, «Applica» del
       sito) non invalidano in silenzio una verifica documentata;
  a10  esito incerto: `expected_updated_at` evita la sovrascrittura
       inconsapevole (409 STALE_DATA, nulla scritto);
  a11  D1: per ogni operatore e ogni scheda la rotta census-data autorizza
       esattamente come la PATCH generica (stesso esito, stesso codice);
  a12  D2: concorrenza deterministica (lock, nessuna attesa a tempo): una
       verifica non si intreccia con la PATCH generica ne' con «Applica» del
       sito, in entrambi gli ordini;
  a13  D3: l'interno non cambia dalla PATCH generica (409, nulla scritto);
       dalla rotta tracciata si', con lo storico.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _edificio, _in_attesa_di_lock, _in_parallelo, _q, _unita, completo, mondo,
)
from tests.test_censimento_2_composition_postgres import _composizione, _conferma, _stato

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

IDENTITA = {"cadastral_municipality_code": "l307", "cadastral_section": "", "cadastral_sheet": "5",
            "cadastral_parcel": "61", "cadastral_subunit": "2"}
VERIFICA = {"cadastral_evidence_type": "visura", "cadastral_evidence_date": "2026-10-01",
            "cadastral_evidence_ref": "prot. 9"}


def _dati(m, pid, chi="owner_a", **corpo):
    return m["api"](chi).patch(f"/api/property/properties/{pid}/census-data", json=corpo)


def _storia(m, pid):
    return _q(m, "SELECT agency_id, created_by_user_id, created_at, description, metadata, activity_type "
                 "FROM activities WHERE property_id = %s ORDER BY id", (pid,))


def _riga(m, pid, *colonne):
    return _q(m, f"SELECT {', '.join(colonne)} FROM properties WHERE id = %s", (pid,))[0]


def _lotto(m, numerazione=True, res=3, scala=None, piano="1"):
    e = _edificio(m, units_declared=None)
    assert _composizione(m, e["id"], residential=res, commercial=0, other=0, pertinenze=1,
                         set_total=True).status_code == 200
    gruppo = {"unit_class": "residential", "count": res, "property_type": "apartment", "floor": piano}
    if scala:
        gruppo["staircase"] = scala
    if numerazione:
        gruppo["numbering_start"] = 1
    r, chiave = _conferma(m, e["id"], [gruppo, {"unit_class": "pertinenze", "count": 1, "property_type": "storage",
                                                "pertinenza_kind": "cantina"}])
    assert r.status_code == 200, r.text
    return e, r.json()["units"], chiave


def _scheda(stato, pid):
    return next(x for x in stato["units"] if x["id"] == pid)


# ---------------------------------------------------------------------------

def test_a01_salvataggio_atomico_con_storico_e_senza_stato_commerciale(mondo):
    m = mondo
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="2", internal_number="4", surface_sqm="70", cadastral_category="A/3")
    prima = _riga(m, u["id"], "id", "code", "commercial_status", "record_kind", "updated_at")
    n_schede = _q(m, "SELECT count(*) FROM properties")[0][0]
    r = _dati(m, u["id"], **IDENTITA, surface_sqm="72.5", surface_source="verified", cadastral_category="a/2",
              cadastral_category_source="verified", internal_number=" 5 ", **VERIFICA)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["id"], corpo["code"]) == (u["id"], u["code"])                          # stessa scheda
    assert corpo["verification"] == "verified" and corpo["verification_reconfirm"] is False
    assert sorted(corpo["changed"]) == sorted(["cadastral_municipality_code", "cadastral_section", "cadastral_sheet",
                                               "cadastral_parcel", "cadastral_subunit", "surface_sqm", "surface_source",
                                               "cadastral_category", "cadastral_category_source",
                                               "cadastral_evidence_type", "cadastral_evidence_date",
                                               "cadastral_evidence_ref", "internal_number"])
    dopo = _riga(m, u["id"], "cadastral_municipality_code", "cadastral_section", "cadastral_subunit", "surface_sqm",
                 "surface_source", "cadastral_category", "cadastral_category_source", "cadastral_evidence_identity",
                 "internal_number", "commercial_status", "record_kind")
    assert dopo == ["L307", "", "2", Decimal("72.50"), "verified", "A/2", "verified", "L307||5|61|2", "5",
                    prima[2], prima[3]]                                                    # mai lo stato commerciale
    assert _q(m, "SELECT count(*) FROM properties")[0][0] == n_schede                      # nessuna scheda nuova
    storia = _storia(m, u["id"])
    assert len(storia) == 1                                                                # UNA riga per salvataggio
    agenzia, autore, quando, testo, meta, tipo = storia[0]
    assert (agenzia, autore, tipo) == (1, m["ids"]["owner_a"], "system") and quando is not None
    assert testo.startswith("Dati catastali e superficie: ")
    assert meta["census_data"] is True and meta["changes"]["surface_sqm"] == ["70.00", "72.50"]
    assert meta["changes"]["cadastral_category"] == ["A/3", "A/2"]
    assert meta["changes"]["cadastral_category_source"] == [None, "verified"]
    assert meta["changes"]["internal_number"] == ["4", "5"]
    assert meta["changes"]["cadastral_evidence_date"] == [None, "2026-10-01"]
    # rimandare gli stessi valori: nessuna scrittura, nessuno storico, updated_at invariato
    aggiornata = _riga(m, u["id"], "updated_at")[0]
    r = _dati(m, u["id"], surface_sqm="72.50", cadastral_category="A/2", cadastral_municipality_code="L307")
    assert r.status_code == 200 and r.json()["changed"] == []
    assert _riga(m, u["id"], "updated_at")[0] == aggiornata and len(_storia(m, u["id"])) == 1
    # la sezione a tre stati: null = non conosciuta (diverso da '' = nessuna)
    r = _dati(m, u["id"], cadastral_section=None, surface_source="verified", cadastral_category_source="verified",
              **VERIFICA)
    assert r.status_code == 200 and _riga(m, u["id"], "cadastral_section")[0] is None
    assert r.json()["changed"] == ["cadastral_section"]


def test_a02_atomicita_nessun_dato_e_nessuno_storico_se_qualcosa_fallisce(mondo, monkeypatch):
    m = mondo
    from property import census
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", surface_sqm="60")
    altra = _unita(m, building_id=e["id"], floor="2", **{k: v for k, v in IDENTITA.items()})
    colonne = ("surface_sqm", "surface_source", "cadastral_category", "cadastral_subunit", "internal_number", "updated_at")
    fotografia = _riga(m, u["id"], *colonne)
    # 1) lo storico fallisce DOPO l'UPDATE: tutto annullato
    def rotto(*_a, **_k):
        raise RuntimeError("storico non scrivibile")
    monkeypatch.setattr(census, "_attivita_sistema", rotto)
    with pytest.raises(RuntimeError):
        _dati(m, u["id"], surface_sqm="61", surface_source="declared", cadastral_category="A/2",
              cadastral_category_source="estimated")
    monkeypatch.undo()
    assert _riga(m, u["id"], *colonne) == fotografia and _storia(m, u["id"]) == []
    # 2) l'indice unico scatta nell'UPDATE (controllo applicativo aggirato): 409 leggibile, nulla scritto
    monkeypatch.setattr(census, "_duplicato_catastale", lambda *a, **k: None)
    r = _dati(m, u["id"], surface_sqm="61", surface_source="declared", **IDENTITA)
    monkeypatch.undo()
    assert (r.status_code, r.json()["code"]) == (409, "CADASTRAL_DUPLICATE"), r.text
    assert _riga(m, u["id"], *colonne) == fotografia and _storia(m, u["id"]) == []
    # 3) ogni rifiuto di validazione: nulla scritto
    for corpo, atteso in (
            ({"surface_sqm": "61", "surface_source": "verified"}, (400, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE")),
            ({"surface_source": "verified", **VERIFICA}, (400, "VERIFIED_NEEDS_CADASTRAL_EVIDENCE")),  # identita' incompleta
            ({"cadastral_category_source": "declared"}, (400, "SOURCE_WITHOUT_VALUE")),
            ({"cadastral_evidence_type": "visura"}, (400, "EVIDENCE_WITHOUT_VERIFIED")),
            ({"cadastral_category": "Z/9", "cadastral_category_source": "declared"}, (400, "VALIDATION_ERROR")),
            ({"cadastral_municipality_code": "123"}, (400, "VALIDATION_ERROR")),
            ({"confirm_internal_number": True}, (400, "INTERNAL_NUMBER_REQUIRED"))):
        r = _dati(m, u["id"], **corpo)
        assert (r.status_code, r.json().get("code")) == atteso, (corpo, r.text)
    assert _riga(m, u["id"], *colonne) == fotografia and _storia(m, u["id"]) == []
    assert _storia(m, altra["id"]) == []


def test_a03_duplicato_catastale(mondo):
    m = mondo
    e = _edificio(m)
    esistente = _unita(m, building_id=e["id"], floor="1", **IDENTITA)
    u = _unita(m, building_id=e["id"], floor="2")
    r = _dati(m, u["id"], **{**IDENTITA, "cadastral_sheet": "0005"})                 # stessa identita' normalizzata
    assert (r.status_code, r.json()["code"]) == (409, "CADASTRAL_DUPLICATE"), r.text
    assert r.json()["existing"] == {"id": esistente["id"], "code": esistente["code"]}
    assert _riga(m, u["id"], "cadastral_subunit")[0] is None and _storia(m, u["id"]) == []
    # sezione «non conosciuta» (null): non e' un'identita' completa per l'indice della 083
    r = _dati(m, u["id"], **{**IDENTITA, "cadastral_section": None})
    assert r.status_code == 200, r.text
    # un altro subalterno: nessun conflitto
    r = _dati(m, u["id"], **{**IDENTITA, "cadastral_subunit": "3"})
    assert r.status_code == 200 and _riga(m, u["id"], "cadastral_subunit")[0] == "3"


def test_a04_isolamento_fra_agenzie(mondo):
    m = mondo
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", surface_sqm="50")
    fotografia = _riga(m, u["id"], "surface_sqm", "surface_source", "updated_at")
    for corpo in ({}, {"surface_source": "declared"}, {"surface_sqm": "1"}, {"confirm_internal_number": True}):
        r = _dati(m, u["id"], chi="owner_b", **corpo)
        assert (r.status_code, r.json()["code"]) == (404, "NOT_FOUND"), (corpo, r.text)
        assert "surface" not in r.text                                                # nessun dato altrui
    assert _riga(m, u["id"], "surface_sqm", "surface_source", "updated_at") == fotografia
    assert _storia(m, u["id"]) == []
    # e una scheda inesistente
    assert _dati(m, 999999, surface_sqm="1").status_code == 404


def test_a05_autorizzazioni_elenco_chiuso_archiviate_e_cestino(mondo):
    m = mondo
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", surface_sqm="50")
    # elenco chiuso: nessun altro campo (mai stato commerciale, proprietario, agenzia...)
    for vietato in ({"commercial_status": "available"}, {"record_kind": "crm"}, {"agency_id": 2},
                    {"building_id": 1}, {"metadata": {}}, {"cadastral_evidence_identity": "x"},
                    {"surface_source": "misurata"}, {"cadastral_evidence_type": "foto"}):
        assert _dati(m, u["id"], **vietato).status_code == 422, vietato
    # una scheda di censimento: ogni operatore dell'agenzia (come il censimento)
    assert _dati(m, u["id"], chi="agent_a", surface_source="declared").status_code == 200
    # D1: una scheda commerciale, anche non assegnata: le autorizzazioni della PATCH
    # generica che il modulo usava prima (ogni operatore dell'agenzia), nessuna regola nuova
    crm = m["api"]().post("/api/property/properties", json={"title": "x", "property_type": "apartment",
                                                            "city": "Fermo", "surface_sqm": "80"})
    assert crm.status_code == 201, crm.text
    pid = crm.json()["id"]
    stato = _riga(m, pid, "commercial_status")[0]
    r = _dati(m, pid, chi="agent_a", surface_source="declared")
    assert r.status_code == 200, r.text
    assert _riga(m, pid, "surface_source", "commercial_status") == ["declared", stato]
    # archiviata: si riattiva prima
    archiviata = _unita(m, building_id=e["id"], floor="4")
    assert m["api"]().post(f"/api/property/properties/{archiviata['id']}/undo-create").status_code == 200
    r = _dati(m, archiviata["id"], surface_sqm="51")
    assert (r.status_code, r.json()["code"]) == (409, "UNIT_ARCHIVED"), r.text
    # nel Cestino: si ripristina prima
    v = _unita(m, building_id=e["id"], floor="3")
    r = m["api"]().post(f"/api/property/properties/{v['id']}/trash", json={"reason_code": "created_by_mistake"})
    assert r.status_code == 200, r.text
    r = _dati(m, v["id"], surface_sqm="51")
    assert (r.status_code, r.json()["code"]) == (409, "PROPERTY_IN_TRASH"), r.text
    assert _riga(m, v["id"], "surface_sqm")[0] is None


def test_a06_storico_persistente(mondo):
    m = mondo
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1")
    assert _dati(m, u["id"], chi="agent_a", surface_sqm="40", surface_source="estimated").status_code == 200
    assert _dati(m, u["id"], surface_sqm="41", surface_source="declared").status_code == 200
    storia = _storia(m, u["id"])
    assert [(s[0], s[1]) for s in storia] == [(1, m["ids"]["agent_a"]), (1, m["ids"]["owner_a"])]
    assert storia[0][4]["changes"] == {"surface_sqm": [None, "40.00"], "surface_source": [None, "estimated"]}
    assert storia[1][4]["changes"] == {"surface_sqm": ["40.00", "41.00"], "surface_source": ["estimated", "declared"]}
    # lo storico dell'immobile non si cancella (CRM-OPS-4, trigger della 082)
    with pytest.raises(m["psycopg2"].Error):
        _q(m, "DELETE FROM activities WHERE property_id = %s", (u["id"],))
    m["conn"].rollback() if not m["conn"].autocommit else None
    # e la scheda lo mostra fra le interazioni
    righe = m["api"]().get(f"/api/property/properties/{u['id']}/interactions").json()["items"]
    assert sum(1 for x in righe if "Dati catastali e superficie" in str(x)) == 2, righe


def test_a07_numerazione_provvisoria_e_conferma_interno(mondo):
    m = mondo
    e, unita, chiave = _lotto(m)
    case = [u for u in unita if u["unit_class"] == "residential"]
    cantina = next(u for u in unita if u["unit_class"] == "pertinenze")
    pid = case[0]["id"]
    assert all(_scheda(_stato(m, e["id"]), u["id"])["provisional_numbering"] for u in case)
    # un collegamento prima della conferma: deve restare
    assert m["api"]().post(f"/api/property/properties/{pid}/pertinenze/link",
                           json={"pertinenza_id": cantina["id"]}).status_code == 200
    meta_prima = _riga(m, pid, "metadata")[0]
    # correggere il numero SENZA conferma: resta provvisorio
    r = _dati(m, pid, internal_number="1A")
    assert r.status_code == 200 and r.json()["provisional_numbering"] is True, r.text
    assert _scheda(_stato(m, e["id"]), pid)["provisional_numbering"] is True
    # posizione gia' usata (stessa scala e piano): avviso superabile
    r = _dati(m, pid, internal_number="2")
    assert (r.status_code, r.json()["code"]) == (409, "SIMILAR_FOUND"), r.text
    assert [x["id"] for x in r.json()["similar"]] == [case[1]["id"]]
    assert _riga(m, pid, "internal_number")[0] == "1A"
    # conferma: stessa scheda, solo «provvisorio» cade; collegamenti e lotto intatti
    n = _q(m, "SELECT count(*) FROM properties")[0][0]
    r = _dati(m, pid, confirm_internal_number=True)
    assert r.status_code == 200 and r.json()["provisional_numbering"] is False, r.text
    assert (r.json()["id"], r.json()["code"]) == (pid, case[0]["code"])
    assert _q(m, "SELECT count(*) FROM properties")[0][0] == n
    meta = _riga(m, pid, "metadata")[0]
    assert meta["census_batch"] == {**meta_prima["census_batch"], "provisional_numbering": False}
    assert _riga(m, cantina["id"], "parent_property_id")[0] == pid
    storia = _storia(m, pid)
    assert storia[-1][4]["changes"] == {"internal_number_status": ["provvisorio", "confermato"]}
    assert storia[-2][4]["changes"] == {"internal_number": ["1", "1A"]}
    assert not _scheda(_stato(m, e["id"]), pid)["provisional_numbering"]
    # confermare di nuovo: nessuna scrittura
    r = _dati(m, pid, confirm_internal_number=True)
    assert r.status_code == 200 and r.json()["changed"] == [] and len(_storia(m, pid)) == len(storia)
    # correzione e conferma insieme, sulla seconda: una riga di storico con entrambe
    r = _dati(m, case[1]["id"], internal_number="2B", confirm_internal_number=True)
    assert r.status_code == 200 and r.json()["provisional_numbering"] is False
    assert set(_storia(m, case[1]["id"])[-1][4]["changes"]) == {"internal_number", "internal_number_status"}
    # stesso interno su un'altra scala: nessun avviso
    altra_scala = _unita(m, building_id=e["id"], floor="1", staircase="B", internal_number="9")
    r = _dati(m, altra_scala["id"], internal_number="3")
    assert r.status_code == 200, r.text
    # nella stessa posizione: «salva comunque» esplicito
    r = _dati(m, case[2]["id"], internal_number="1A")
    assert r.json()["code"] == "SIMILAR_FOUND"
    r = _dati(m, case[2]["id"], internal_number="1A", confirm_similar=True)
    assert r.status_code == 200
    # interno tolto (unita' senza numero ufficiale): niente da mostrare come provvisorio, nessun dato inventato
    r = _dati(m, case[2]["id"], internal_number=None)
    assert r.status_code == 200 and r.json()["provisional_numbering"] is False
    assert _riga(m, case[2]["id"], "internal_number", "cadastral_subunit") == [None, None]
    r = _dati(m, case[2]["id"], confirm_internal_number=True)
    assert (r.status_code, r.json()["code"]) == (400, "INTERNAL_NUMBER_REQUIRED")


def test_a07b_lotto_senza_numerazione(mondo):
    m = mondo
    e, unita, _ = _lotto(m, numerazione=False, res=2)
    pid = next(u["id"] for u in unita if u["unit_class"] == "residential")
    assert _scheda(_stato(m, e["id"]), pid)["provisional_numbering"] is False
    r = _dati(m, pid, confirm_internal_number=True)
    assert (r.status_code, r.json()["code"]) == (400, "INTERNAL_NUMBER_REQUIRED")
    r = _dati(m, pid, internal_number="7")
    assert r.status_code == 200 and r.json()["provisional_numbering"] is False     # mai stato provvisorio


def test_a08_tre_condizioni_e_tre_indicatori_separati(mondo):
    m = mondo
    e, unita, _ = _lotto(m, numerazione=False, res=3)
    a, b, c = [u["id"] for u in unita if u["unit_class"] == "residential"]
    for i, pid in enumerate((a, b, c)):
        r = _dati(m, pid, **{**IDENTITA, "cadastral_subunit": str(10 + i)}, surface_sqm="70", surface_source="declared",
                  cadastral_category="A/2", cadastral_category_source="declared")
        assert r.status_code == 200, r.text
    # a: verificata (entrambe); b: parzialmente (solo la categoria)
    assert _dati(m, a, surface_source="verified", cadastral_category_source="verified", **VERIFICA).json()["verification"] == "verified"
    assert _dati(m, b, cadastral_category_source="verified", **VERIFICA).json()["verification"] == "partial"
    s = _stato(m, e["id"])
    assert s["verification"] == {"units": 4, "verified": 1, "partial": 1, "to_verify": 2, "to_reconfirm": 0}
    assert [_scheda(s, x)["verification"] for x in (a, b, c)] == ["verified", "partial", "to_verify"]
    # tre indicatori distinti: quantita', completezza, attendibilita'
    assert s["units_created"]["present"] == 4 and s["data_completeness"]["complete"] == 3
    assert [x["value"] for x in s["catalog"]["verification"]] == ["verified", "partial", "to_verify"]
    # riferimenti cambiati dal modulo senza riconferma: la verifica non vale piu' -> da riconfermare
    r = _dati(m, a, cadastral_subunit="99")
    assert r.status_code == 200 and (r.json()["verification"], r.json()["verification_reconfirm"]) == ("to_verify", True)
    s = _stato(m, e["id"])
    assert s["verification"] == {"units": 4, "verified": 0, "partial": 1, "to_verify": 3, "to_reconfirm": 1}
    # riconferma esplicita con la fonte: torna verificata
    r = _dati(m, a, surface_source="verified", cadastral_category_source="verified", **VERIFICA)
    assert r.json()["verification"] == "verified" and r.json()["verification_reconfirm"] is False
    # riferimenti cambiati E riconferma nella stessa richiesta: resta verificata, legata alla nuova identita'
    r = _dati(m, a, cadastral_subunit="98", surface_source="verified", cadastral_category_source="verified", **VERIFICA)
    assert r.json()["verification"] == "verified"
    assert _riga(m, a, "cadastral_evidence_identity")[0] == "L307||5|61|98"


def test_a09_gli_altri_percorsi_non_invalidano_una_verifica(mondo):
    m = mondo
    e, unita, _ = _lotto(m, numerazione=False, res=2)
    a, b = [u["id"] for u in unita if u["unit_class"] == "residential"]
    assert _dati(m, a, **IDENTITA, surface_sqm="70", surface_source="verified", cadastral_category="A/2",
                 cadastral_category_source="verified", **VERIFICA).status_code == 200
    fotografia = _riga(m, a, "surface_sqm", "surface_source", "cadastral_category", "cadastral_category_source",
                       "cadastral_subunit", "updated_at")
    api = m["api"]()
    # PATCH generica: valori protetti -> 409, nulla scritto
    for corpo in ({"surface_sqm": "71"}, {"cadastral_category": "A/3"}, {"cadastral_subunit": "3"},
                  {"cadastral_municipality_code": "D542"}, {"cadastral_section": "B"}, {"surface_sqm": None}):
        r = api.patch(f"/api/property/properties/{a}", json=corpo)
        assert (r.status_code, r.json()["code"]) == (409, "VERIFIED_DATA_LOCKED"), (corpo, r.text)
    assert _riga(m, a, "surface_sqm", "surface_source", "cadastral_category", "cadastral_category_source",
                 "cadastral_subunit", "updated_at") == fotografia
    # ... ma lo stesso valore (il form Immobili rimanda tutto) e gli altri campi si salvano
    r = api.patch(f"/api/property/properties/{a}", json={"surface_sqm": 70, "cadastral_category": "a/2", "rooms": 3,
                                                          "cadastral_municipality_code": "l307", "cadastral_sheet": "05"})
    assert r.status_code == 200, r.text
    assert (r.json()["surface_source"], r.json()["cadastral_category_source"], r.json()["rooms"]) == ("verified", "verified", 3)
    # una scheda NON verificata: la PATCH generica resta com'era
    assert api.patch(f"/api/property/properties/{b}", json={"surface_sqm": "55"}).status_code == 200
    # modifica di gruppo: una verificata nella selezione -> 409 per tutte, nessuna modifica
    url = f"/api/property/buildings/{e['id']}/units/bulk-update"
    for cambi in ({"surface_sqm": "80"}, {"cadastral_category": "A/3"}, {"surface_source": "declared"},
                  {"cadastral_category_source": "estimated"}):
        r = api.post(url, json={"property_ids": [a, b], "changes": cambi})
        assert (r.status_code, r.json()["code"], r.json()["property_ids"]) == (409, "VERIFIED_DATA_LOCKED", [a]), r.text
    assert _riga(m, b, "surface_sqm")[0] == Decimal("55.00")
    assert _riga(m, a, "surface_sqm", "surface_source", "cadastral_category", "cadastral_category_source",
                 "cadastral_subunit")[:5] == fotografia[:5]
    # piano e scala in gruppo non toccano la verifica
    r = api.post(url, json={"property_ids": [a, b], "changes": {"floor": "3"}})
    assert r.status_code == 200 and _riga(m, a, "surface_source")[0] == "verified"
    # «Applica» del sito sulla superficie verificata: 409; «Ignora» resta possibile
    stima = _q(m, "INSERT INTO stime (agency_id) VALUES (1) RETURNING id")[0][0]
    conflitti = [{"id": "c1", "field": "surface_sqm", "site_value": 99, "status": "open"},
                 {"id": "c2", "field": "rooms", "site_value": 4, "status": "open"}]
    from psycopg2.extras import Json
    fonte = _q(m, "INSERT INTO property_site_sources (agency_id, property_id, stima_id, origin, fingerprint, conflicts) "
                  "VALUES (1, %s, %s, 'auto', %s, %s) RETURNING id", (a, stima, "0" * 64, Json(conflitti)))[0][0]
    url = f"/api/property/properties/{a}/site-sources/{fonte}/conflicts"
    r = api.post(url, json={"conflict_id": "c1", "action": "apply"})
    assert (r.status_code, r.json()["code"]) == (409, "VERIFIED_DATA_LOCKED"), r.text
    assert _riga(m, a, "surface_sqm", "surface_source")[:2] == fotografia[:2]
    assert api.post(url, json={"conflict_id": "c2", "action": "apply"}).status_code == 200
    assert api.post(url, json={"conflict_id": "c1", "action": "ignore"}).status_code == 200
    assert _riga(m, a, "surface_source")[0] == "verified"


def test_a10_esito_incerto_nessuna_sovrascrittura_inconsapevole(mondo):
    m = mondo
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", surface_sqm="50")
    letto = m["api"]().get(f"/api/property/properties/{u['id']}").json()["updated_at"]
    # il primo invio e' arrivato (la risposta no): un collega, o lo stesso invio, ha gia' scritto
    assert _dati(m, u["id"], surface_sqm="52", surface_source="declared", expected_updated_at=letto).status_code == 200
    r = _dati(m, u["id"], surface_sqm="53", surface_source="estimated", expected_updated_at=letto)
    assert (r.status_code, r.json()["code"]) == (409, "STALE_DATA"), r.text
    assert _riga(m, u["id"], "surface_sqm", "surface_source") == [Decimal("52.00"), "declared"]
    assert len(_storia(m, u["id"])) == 1
    # riletto lo stato, l'operatore decide: con il nuovo riferimento si salva
    nuovo = m["api"]().get(f"/api/property/properties/{u['id']}").json()["updated_at"]
    assert _dati(m, u["id"], surface_sqm="53", surface_source="estimated", expected_updated_at=nuovo).status_code == 200


def test_a11_stesse_autorizzazioni_della_patch_generica(mondo):
    """D1: nessuna autorizzazione nuova o tolta. Per ogni operatore e ogni
    scheda, census-data risponde come la PATCH generica sullo stesso dato."""
    m = mondo
    e = _edificio(m)
    census = _unita(m, building_id=e["id"], floor="1", surface_sqm="50")
    crm = m["api"]().post("/api/property/properties", json={"title": "c", "property_type": "apartment",
                                                            "city": "Fermo", "surface_sqm": "80"}).json()
    assegnata = m["api"]().post("/api/property/properties", json={"title": "d", "property_type": "apartment",
                                                                  "city": "Fermo", "surface_sqm": "90"}).json()
    _q(m, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (m["ids"]["agent_a"], assegnata["id"]))
    for chi in ("owner_a", "agent_a", "owner_b"):
        for scheda in (census, crm, assegnata):
            valore = str(60 + scheda["id"] % 7)
            generica = m["api"](chi).patch(f"/api/property/properties/{scheda['id']}", json={"surface_sqm": valore})
            nuova = _dati(m, scheda["id"], chi=chi, surface_sqm=str(int(valore) + 1))
            assert generica.status_code == nuova.status_code, (chi, scheda["id"], generica.text, nuova.text)
            if nuova.status_code != 200:
                assert generica.json().get("code") == nuova.json().get("code") == "NOT_FOUND"
    # e la PATCH generica stessa resta com'era: nessun lock ne' lettura in piu' sui campi non guardati
    assert m["api"]("agent_a").patch(f"/api/property/properties/{crm['id']}", json={"rooms": 2}).status_code == 200


def _verificabile(m):
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", surface_sqm="70", cadastral_category="A/2", **IDENTITA)
    return u["id"]


def test_a12_concorrenza_la_verifica_attende_la_patch_generica_in_corso(mondo, monkeypatch):
    """Ordine 1: la PATCH generica ha superato il controllo (scheda non
    verificata) e sta scrivendo; una verifica arriva in quel momento. Senza
    lock la verifica si salverebbe prima e la scrittura generica la
    toglierebbe in silenzio. Con il lock la verifica ASPETTA e si applica al
    valore nuovo: nessuna verifica persa, nessuna scrittura cieca."""
    import threading
    from property import service
    m = mondo
    pid = _verificabile(m)
    entrata, via = threading.Event(), threading.Event()
    originale = service.update_property

    def lenta(ctx, i, p):
        entrata.set()
        assert via.wait(20)
        return originale(ctx, i, p)
    monkeypatch.setattr(service, "update_property", lenta)

    def generica():
        return m["api"]().patch(f"/api/property/properties/{pid}", json={"surface_sqm": "75"})

    def verifica():
        assert entrata.wait(20)
        return _dati(m, pid, surface_source="verified", cadastral_category_source="verified", **VERIFICA)

    def sblocca():
        assert entrata.wait(20)
        # la verifica e' davvero ferma sul lock della scheda, non su un'attesa a tempo
        ferma = _in_attesa_di_lock(m, "%pg_advisory_xact_lock%", timeout=3)
        via.set()
        return ferma

    r_gen, r_ver, ferma = _in_parallelo([generica, verifica, sblocca])
    assert r_gen.status_code == 200, r_gen.text
    assert r_ver.status_code == 200 and r_ver.json()["verification"] == "verified", r_ver.text
    assert _riga(m, pid, "surface_sqm", "surface_source", "cadastral_category_source") == [
        Decimal("75.00"), "verified", "verified"]                     # verificata sul valore nuovo, non persa
    assert _storia(m, pid)[-1][4]["changes"]["surface_source"] == [None, "verified"]
    assert ferma is True


def test_a12b_concorrenza_la_patch_generica_attende_la_verifica_in_corso(mondo, monkeypatch):
    """Ordine 2: una verifica e' in corso (UPDATE fatto, non ancora
    committata); la PATCH generica arriva. Senza lock leggerebbe lo stato
    vecchio (non verificata) e scriverebbe dopo il commit, togliendo la
    verifica in silenzio. Con il lock aspetta, rilegge e rifiuta (409)."""
    import threading
    from property import census
    m = mondo
    pid = _verificabile(m)
    entrata, via = threading.Event(), threading.Event()
    originale = census._attivita_sistema

    def lento(*a, **k):
        entrata.set()
        assert via.wait(20)
        return originale(*a, **k)
    monkeypatch.setattr(census, "_attivita_sistema", lento)

    # la differenza del sito si prepara PRIMA (la connessione del test non si usa da due thread)
    stima = _q(m, "INSERT INTO stime (agency_id) VALUES (1) RETURNING id")[0][0]
    from psycopg2.extras import Json
    fonte = _q(m, "INSERT INTO property_site_sources (agency_id, property_id, stima_id, origin, fingerprint, "
                  "conflicts) VALUES (1, %s, %s, 'auto', %s, %s) RETURNING id",
               (pid, stima, "1" * 64, Json([{"id": "s1", "field": "surface_sqm", "site_value": 99,
                                             "status": "open"}])))[0][0]

    def verifica():
        return _dati(m, pid, surface_source="verified", cadastral_category_source="verified", **VERIFICA)

    def generica():
        assert entrata.wait(20)
        return m["api"]().patch(f"/api/property/properties/{pid}", json={"surface_sqm": "75", "rooms": 3})

    def sito():
        assert entrata.wait(20)
        return m["api"]().post(f"/api/property/properties/{pid}/site-sources/{fonte}/conflicts",
                               json={"conflict_id": "s1", "action": "apply"})

    def sblocca():
        assert entrata.wait(20)
        # PATCH generica e «Applica» del sito sono ferme sul lock della scheda
        import time
        scadenza = time.monotonic() + 3
        while time.monotonic() < scadenza:
            n = _q(m, "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock' "
                      "AND query LIKE %s", (m["nome"], "%pg_advisory_lock(%"))[0][0]
            if n >= 2:
                break
            time.sleep(0.02)
        via.set()
        return n

    r_ver, r_gen, r_sito, ferme = _in_parallelo([verifica, generica, sito, sblocca])
    assert r_ver.status_code == 200 and r_ver.json()["verification"] == "verified", r_ver.text
    assert (r_gen.status_code, r_gen.json()["code"]) == (409, "VERIFIED_DATA_LOCKED"), r_gen.text
    assert (r_sito.status_code, r_sito.json()["code"]) == (409, "VERIFIED_DATA_LOCKED"), r_sito.text
    assert _riga(m, pid, "surface_sqm", "surface_source", "cadastral_category_source", "rooms") == [
        Decimal("70.00"), "verified", "verified", None]               # verifica intatta, nulla scritto dalle altre
    assert ferme >= 2, ferme


def test_a12c_nessun_lock_residuo_dopo_errori(mondo, monkeypatch):
    """Il lock di sessione cade anche quando il service fallisce: la scheda
    resta modificabile subito dopo."""
    from property import service
    m = mondo
    pid = _verificabile(m)

    def rotto(ctx, i, p):
        raise RuntimeError("guasto del service")
    monkeypatch.setattr(service, "update_property", rotto)
    with pytest.raises(RuntimeError):
        m["api"]().patch(f"/api/property/properties/{pid}", json={"surface_sqm": "71"})
    monkeypatch.undo()
    assert _q(m, "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")[0][0] == 0
    r = _dati(m, pid, surface_sqm="72", surface_source="declared")
    assert r.status_code == 200, r.text


def test_a13_interno_solo_dalla_rotta_tracciata(mondo):
    m = mondo
    e, unita, _ = _lotto(m, res=2)
    pid = next(u["id"] for u in unita if u["unit_class"] == "residential")
    api = m["api"]()
    prima = _riga(m, pid, "internal_number", "updated_at", "metadata")
    for valore in ("9", None, " 2 "):
        r = api.patch(f"/api/property/properties/{pid}", json={"internal_number": valore, "rooms": 3})
        assert (r.status_code, r.json()["code"]) == (409, "INTERNAL_NUMBER_TRACKED"), (valore, r.text)
    assert _riga(m, pid, "internal_number", "updated_at", "metadata") == prima and _storia(m, pid) == []
    # lo stesso valore (spazi compresi) non e' una modifica: il resto si salva come prima
    r = api.patch(f"/api/property/properties/{pid}", json={"internal_number": " 1 ", "rooms": 3})
    assert r.status_code == 200 and r.json()["rooms"] == 3, r.text
    # dalla rotta tracciata: modifica con lo storico, resta provvisorio
    r = _dati(m, pid, internal_number="9")
    assert r.status_code == 200 and r.json()["provisional_numbering"] is True
    assert _storia(m, pid)[-1][4]["changes"] == {"internal_number": ["1", "9"]}
    # un'altra agenzia: 404 come sempre (nessun indizio sull'interno)
    r = m["api"]("owner_b").patch(f"/api/property/properties/{pid}", json={"internal_number": "5"})
    assert r.status_code == 404

