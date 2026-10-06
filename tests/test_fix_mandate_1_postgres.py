"""FIX-MANDATE-1 - una sola definizione di incarico (core/property_mandate.py)
per Cestino, Incarichi, rimozione proprietario, scadenze e FLOW.

PostgreSQL VERO (fixture `mondo` di DELETE-ARCH Fase 0: tutte le migration,
081-086 comprese, router veri). Casi del modello reale:

  01  il difetto: incarico generato da un'acquisizione, poi «Tipo incarico»
      svuotato dalla scheda immobile (PATCH null, permesso fino a oggi) ->
      il Cestino diceva «Incarico presente», la sezione Incarichi non lo
      elencava piu'. Dopo: e' un incarico (dati incompleti) in entrambi;
  02  la scheda non svuota piu' tipo/inizio di un incarico da acquisizione;
  03  dati parziali (senza acquisizione: solo tipo, solo scadenza, solo lo
      stato `mandate`) non sono un incarico: nessun blocco assoluto, ma un
      agente deve passare da un amministratore; fuori da scadenze e alert;
  04  incarico storico (pre-081, tipo + inizio, senza acquisizione):
      protetto anche se scaduto, motivazione «storico»/«scaduto il»;
  05  incarico archiviato: protetto, motivazione con il filtro Incarichi;
  06  separazione fra agenzie;
  07  rimozione dell'ultimo proprietario: stessa definizione;
  08  incarico firmato registrato nel ponte LMC-15 (`stima_acquisitions.
      mandate_signed_at`, percorso reale `POST /api/acquisition/links/{id}/
      mandate`) su un immobile senza dati d'incarico: protetto anche per il
      titolare, anche dopo la revoca del collegamento; fuori da Incarichi
      (scelta CRM-OPS-4); separazione fra agenzie; ultimo proprietario;
  09  un'acquisizione AVVIATA (senza «Genera incarico») non e' un incarico:
      `acquisition_id` resta NULL, nessun blocco d'incarico.

Prima di FIX-MANDATE-1 01-05, 07 e 08 falliscono (fail-before); 06 e 09
sono di conservazione.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _acquisizione, _codice, _collega, _contatto, _immobile, _q, completo, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

BASE = "/api/property/properties"


def _con_proprietario(m, chi="owner_a"):
    p = _immobile(m, chi)
    cid = _contatto(m, "Olga Proprietaria")
    _collega(m, p["id"], cid, "owner", True)
    return p["id"], cid


def _incarico_vero(m, pid, cid, **kw):
    """Acquisizione -> sopralluogo -> «Genera incarico» (il percorso reale)."""
    acq = _acquisizione(m, pid, cid)
    _q(m, "UPDATE acquisitions SET status = 'inspection_done' WHERE id = %s", (acq["id"],))
    versione = _q(m, "SELECT version FROM acquisitions WHERE id = %s", (acq["id"],))[0][0]
    corpo = {"version": versione, "mandate_type": "Esclusiva", "mandate_start": date.today().isoformat(),
             "mandate_end": (date.today() + timedelta(days=180)).isoformat(), **kw}
    _codice(m["api"]().post(f"/api/acquisitions/{acq['id']}/mandate", json=corpo), 200)
    return acq["id"]


def _sql_senza_guardia(m, sql, params):
    """Dati scritti PRIMA della 081 (o da percorsi legacy): la guardia d'origine
    si spegne solo per questa UPDATE, come nelle suite CRM-OPS-3/4 e 2B1."""
    _q(m, "ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    try:
        _q(m, sql, params)
    finally:
        _q(m, "ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")


def _incarico_blocco(m, pid, chi="owner_a"):
    controllo = _codice(m["api"](chi).get(f"{BASE}/{pid}/deletion-check"), 200)
    return controllo, next((b for b in controllo["blockers"] if b["code"] == "MANDATE_PRESENT"), None)


def _incarichi(m, chi="owner_a", **filtri):
    corpo = _codice(m["api"](chi).get("/api/property/mandates", params={"limit": 200, **filtri}), 200)
    return {r["property_id"]: r for r in corpo["items"]}


def test_01_incarico_da_acquisizione_con_dati_azzerati_resta_un_incarico_ovunque(mondo):
    m = mondo
    pid, cid = _con_proprietario(m)
    _incarico_vero(m, pid, cid)
    assert pid in _incarichi(m)
    # lo stato che c'e' su TEST: «Tipo incarico» svuotato dalla scheda (o
    # da property_admin) quando la PATCH null era ancora accettata
    _q(m, "UPDATE properties SET mandate_type = NULL WHERE id = %s", (pid,))
    controllo, blocco = _incarico_blocco(m, pid)
    assert controllo["can_trash"] is False and blocco is not None
    # ... e la sezione Incarichi lo elenca, segnalando cosa manca
    voce = _incarichi(m).get(pid)
    assert voce is not None, "bloccato nel Cestino come incarico ma assente da Incarichi"
    assert voce["missing_fields"] == ["mandate_type"]
    assert m["api"]().get(f"/api/property/mandates/{pid}").status_code == 200
    # la motivazione dice dove trovarlo, con il collegamento esistente
    assert "Incarichi" in blocco["label"] and "incomplet" in blocco["label"]
    assert blocco["link"] == {"href": f"#/incarichi/{pid}", "label": "Apri incarico"}
    assert blocco["items"][0]["origin"] == "acquisition" and blocco["items"][0]["state"] == "active"


def test_02_la_scheda_non_svuota_tipo_ne_inizio_di_un_incarico_da_acquisizione(mondo):
    m = mondo
    pid, cid = _con_proprietario(m)
    _incarico_vero(m, pid, cid)
    for corpo in ({"mandate_type": None}, {"mandate_type": "   "}, {"mandate_start": None}):
        r = m["api"]().patch(f"{BASE}/{pid}", json=corpo)
        assert r.status_code == 400, (corpo, r.status_code, r.text)
    riga = _q(m, "SELECT mandate_type, mandate_start FROM properties WHERE id = %s", (pid,))[0]
    assert riga[0] == "Esclusiva" and riga[1] == date.today()
    # modificarli, o togliere la sola scadenza, resta possibile
    _codice(m["api"]().patch(f"{BASE}/{pid}", json={"mandate_type": "Non esclusiva", "mandate_end": None}), 200)


@pytest.mark.parametrize("parziale", [
    "mandate_type = 'Esclusiva'",
    "mandate_end = CURRENT_DATE + 10",
    "commercial_status = 'mandate'",
    "mandate_start = CURRENT_DATE - 30, mandate_end = CURRENT_DATE + 5",
], ids=["solo_tipo", "solo_scadenza", "solo_stato", "date_senza_tipo"])
def test_03_dati_parziali_non_sono_un_incarico(mondo, parziale):
    m = mondo
    pid, _ = _con_proprietario(m, "owner_a")
    _q(m, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (m["ids"]["agent_a"], pid))
    _sql_senza_guardia(m, f"UPDATE properties SET {parziale} WHERE id = %s", (pid,))
    controllo, blocco = _incarico_blocco(m, pid)
    assert blocco is None, controllo
    assert pid not in _incarichi(m) and pid not in _incarichi(m, commercial_status="mandate")
    # nessuna scadenza d'incarico inventata: KPI e avvisi
    alert = _codice(m["api"]().get("/api/property/alerts"), 200)
    righe = alert if isinstance(alert, list) else alert.get("items", alert)
    assert not any(a.get("alert_type") == "mandate" and a.get("property_id") == pid for a in righe)
    ids = {x["id"] for x in _codice(m["api"]().get(BASE, params={"mandate_expiring": "true", "limit": 200}), 200)["items"]}
    assert pid not in ids
    # un agente non li sposta da solo nel Cestino: sono storia (amministratore)
    controllo_agente, _ = _incarico_blocco(m, pid, "agent_a")
    storia = {h["code"] for b in controllo_agente["blockers"] for h in b.get("items", [])}
    assert controllo_agente["can_trash"] is False and "MANDATE_DATA" in storia
    # il titolare si': e il ripristino li restituisce intatti
    prima = _q(m, "SELECT mandate_type, mandate_start, mandate_end, commercial_status FROM properties WHERE id = %s", (pid,))
    _codice(m["api"]().post(f"{BASE}/{pid}/trash", json={"reason_code": "created_by_mistake"}), 200)
    _codice(m["api"]().post(f"{BASE}/{pid}/restore"), 200)
    assert _q(m, "SELECT mandate_type, mandate_start, mandate_end, commercial_status FROM properties WHERE id = %s", (pid,)) == prima


def test_04_incarico_storico_scaduto_resta_protetto_e_lo_dice(mondo):
    m = mondo
    pid, _ = _con_proprietario(m)
    _sql_senza_guardia(m, "UPDATE properties SET mandate_type = 'Esclusiva', mandate_start = DATE '2025-01-10', "
                          "mandate_end = DATE '2025-07-10', commercial_status = 'mandate' WHERE id = %s", (pid,))
    controllo, blocco = _incarico_blocco(m, pid)
    assert controllo["can_trash"] is False and blocco is not None
    assert "storico" in blocco["label"] and "scaduto il 10/07/2025" in blocco["label"]
    assert "Non compare in Incarichi" in blocco["label"] and "link" not in blocco
    assert blocco["items"][0]["origin"] == "historical" and blocco["items"][0]["state"] == "expired"
    # la vista Incarichi elenca quelli nati da un'acquisizione (filtro di vista, invariato)
    assert pid not in _incarichi(m)
    _codice(m["api"]().post(f"{BASE}/{pid}/trash", json={"reason_code": "other"}), 409, "TRASH_BLOCKED")
    # nessun dato toccato
    assert tuple(_q(m, "SELECT mandate_type, mandate_end FROM properties WHERE id = %s", (pid,))[0]) == ("Esclusiva", date(2025, 7, 10))


def test_05_incarico_archiviato_resta_protetto_con_il_filtro_giusto(mondo):
    m = mondo
    pid, cid = _con_proprietario(m)
    _incarico_vero(m, pid, cid)
    # archiviato (l'appuntamento futuro dell'acquisizione impedirebbe «Archivia»: qui interessa lo stato)
    _q(m, "UPDATE properties SET archived_at = NOW(), commercial_status = 'archived' WHERE id = %s", (pid,))
    _, blocco = _incarico_blocco(m, pid)
    assert blocco is not None and "archiviato" in blocco["label"] and "«Archiviato»" in blocco["label"]
    assert blocco["link"]["href"] == f"#/incarichi/{pid}"
    assert pid not in _incarichi(m) and pid in _incarichi(m, commercial_status="archived")


def test_06_separazione_fra_agenzie(mondo):
    m = mondo
    pid, cid = _con_proprietario(m)
    _incarico_vero(m, pid, cid)
    _q(m, "UPDATE properties SET mandate_type = NULL WHERE id = %s", (pid,))
    assert m["api"]("owner_b").get(f"{BASE}/{pid}/deletion-check").status_code == 404
    assert pid not in _incarichi(m, "owner_b")
    assert m["api"]("owner_b").get(f"/api/property/mandates/{pid}").status_code == 404
    # un incarico storico dell'altra agenzia non entra nelle scadenze di questa
    altro = _q(m, "INSERT INTO properties (agency_id, title, city) VALUES (2, 'Altra agenzia', 'Fermo') RETURNING id")[0][0]
    _sql_senza_guardia(m, "UPDATE properties SET mandate_type = 'Esclusiva', mandate_start = CURRENT_DATE - 100, "
                          "mandate_end = CURRENT_DATE + 3 WHERE id = %s", (altro,))
    alert = _codice(m["api"]().get("/api/property/alerts"), 200)
    righe = alert if isinstance(alert, list) else alert.get("items", alert)
    assert not any(a.get("property_id") == altro for a in righe)
    assert any(a.get("property_id") == altro and a.get("alert_type") == "mandate"
               for a in (lambda x: x if isinstance(x, list) else x.get("items", x))(
                   _codice(m["api"]("owner_b").get("/api/property/alerts"), 200)))


def test_07_ultimo_proprietario_stessa_definizione(mondo):
    m = mondo
    # dati parziali (solo il tipo): non e' un incarico, il proprietario si toglie
    pid, cid = _con_proprietario(m)
    _sql_senza_guardia(m, "UPDATE properties SET mandate_type = 'Esclusiva' WHERE id = %s", (pid,))
    assert m["api"]().delete(f"{BASE}/{pid}/contacts/{cid}/owner").status_code == 204
    # incarico da acquisizione con tipo azzerato: e' un incarico, l'ultimo proprietario resta
    pid2, cid2 = _con_proprietario(m)
    _incarico_vero(m, pid2, cid2)
    _q(m, "UPDATE properties SET mandate_type = NULL WHERE id = %s", (pid2,))
    _codice(m["api"]().delete(f"{BASE}/{pid2}/contacts/{cid2}/owner"), 409, "LAST_OWNER_WITH_MANDATE")


def _ponte(m):
    """Il router LMC-15 vero (`acquisition.router`, montato in main.py), con lo
    stesso contesto operatore di `mondo`."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acquisition.router import router as ponte
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(ponte)
    app.dependency_overrides[require_operator] = m["ctx"]
    client = TestClient(app, raise_server_exceptions=False)

    def post(path, corpo, chi="owner_a"):
        m["stato"]["chi"] = chi
        return client.post(path, json=corpo)
    return post


def test_08_incarico_firmato_nel_ponte_lmc15_e_protetto(mondo):
    m = mondo
    post = _ponte(m)
    pid, cid = _con_proprietario(m)
    stima = _q(m, "INSERT INTO stime (agency_id, nome, cognome, email, telefono, comune) "
                  "VALUES (1, 'Lia', 'Firma', 'fm1@x.test', '333', 'Giulianova') RETURNING id")[0][0]
    try:
        link = _codice(post(f"/api/acquisition/stime/{stima}/links", {"property_id": pid}), 201)["id"]
        # prima della firma il collegamento da solo non e' un incarico
        assert _incarico_blocco(m, pid)[1] is None
        _codice(post(f"/api/acquisition/links/{link}/mandate",
                     {"mandate_signed_at": "2025-03-01T10:00:00+01:00", "mandate_reference": "INC-77"}), 200)
        # nessun dato d'incarico sulla scheda: la firma sta solo nel ponte
        riga = _q(m, "SELECT acquisition_id, mandate_type, mandate_start, commercial_status FROM properties WHERE id = %s", (pid,))[0]
        assert riga[0] is None and riga[1] is None and riga[2] is None
        controllo, blocco = _incarico_blocco(m, pid)
        assert controllo["can_trash"] is False and blocco is not None, controllo
        assert "firmato il 01/03/2025" in blocco["label"] and f"stima n. {stima}" in blocco["label"]
        assert "rif. INC-77" in blocco["label"] and "non compare in Incarichi" in blocco["label"]
        assert "link" not in blocco
        voce = blocco["items"][0]
        assert (voce["origin"], voce["stima_acquisition_id"], voce["link_status"]) == ("signed_link", link, "active")
        # blocco assoluto: anche il titolare si ferma
        _codice(m["api"]().post(f"{BASE}/{pid}/trash", json={"reason_code": "other"}), 409, "TRASH_BLOCKED")
        # la sezione Incarichi resta quella delle acquisizioni (CRM-OPS-4)
        assert pid not in _incarichi(m)
        # altra agenzia: l'immobile non esiste
        assert m["api"]("owner_b").get(f"{BASE}/{pid}/deletion-check").status_code == 404
        # ultimo proprietario: stessa definizione
        _codice(m["api"]().delete(f"{BASE}/{pid}/contacts/{cid}/owner"), 409, "LAST_OWNER_WITH_MANDATE")
        # revocare il collegamento corregge l'attribuzione, non annulla la firma (070)
        _codice(post(f"/api/acquisition/links/{link}/revoke", {"revoked_reason": "attribuzione errata"}), 200)
        _, blocco = _incarico_blocco(m, pid)
        assert blocco is not None and "collegamento poi revocato" in blocco["label"]
        assert blocco["items"][0]["link_status"] == "revoked"
        # nessun dato toccato
        firma = _q(m, "SELECT mandate_signed_at IS NOT NULL, mandate_reference FROM stima_acquisitions WHERE id = %s", (link,))[0]
        assert tuple(firma) == (True, "INC-77")
        assert _q(m, "SELECT deleted_at FROM properties WHERE id = %s", (pid,))[0][0] is None
    finally:
        _q(m, "DELETE FROM stima_acquisitions WHERE property_id = %s", (pid,))


def test_09_acquisizione_avviata_senza_incarico_non_e_un_incarico(mondo):
    m = mondo
    pid, cid = _con_proprietario(m)
    acq = _acquisizione(m, pid, cid)
    # solo «Genera incarico» scrive acquisition_id (acquisitions/service.py::generate_mandate)
    assert _q(m, "SELECT acquisition_id FROM properties WHERE id = %s", (pid,))[0][0] is None
    controllo, blocco = _incarico_blocco(m, pid)
    assert blocco is None, controllo
    # l'acquisizione aperta blocca come processo aperto, non come incarico
    assert controllo["can_trash"] is False
    assert pid not in _incarichi(m)
    assert _q(m, "SELECT status FROM acquisitions WHERE id = %s", (acq["id"],))[0][0] not in ("acquired",)
