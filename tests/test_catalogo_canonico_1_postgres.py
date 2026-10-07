"""CATALOGO-CANONICO-1 (FASE D) - dal sito alla scheda immobile, su PostgreSQL VERO.

Gli endpoint VERI del sito (`main.salva_stima`, `main.salva_stima_dettagliata`)
girano sul database usa-e-getta (schema completo, migration 087 e 088 comprese) con il
bridge CORE vero; si sostituiscono solo PDF, mail/WhatsApp, gli eventi
accessori gia' certificati e la decisione di routing (per scegliere l'agenzia
della stima). Le rotte della scheda sono quelle vere (router Immobili).

  01  stima completa -> UNA scheda di censimento, provenienza Stima360, senza
      incarico ne' agente; valori dichiarati (mai i default di `stime`), mq
      con i decimali, pertinenze con superficie e numero; lead `origin` e
      contatto con ruolo neutro; motore invariato sugli stessi input;
  02  form leggero: solo cio' che e' stato inviato (piano, locali, ascensore,
      anno... restano NULL); 0 nelle superfici = non dichiarato;
  03  stima dettagliata: la STESSA scheda; campi vuoti completati, valori del
      sito non toccati aggiornati, dato corretto dall'agente -> conflitto;
      dettaglio ripetuto = nessun effetto; dettaglio orfano = nessuna scheda;
  04  ritentativo con la stessa identita' della richiesta, anche dopo 30
      giorni -> la stessa scheda; identita' diverse con dati uguali -> due
      schede, doppione segnalato; client senza identita' -> mai unito, solo
      segnalato; stesso indirizzo di un altro contatto -> segnalato;
  04b richieste concorrenti con la stessa identita' -> una scheda;
  05  «Applica» / «Ignora» dalla scheda; l'ignorato non torna;
  06  «Collega a IMM-x»: la stima, il lead e il contatto seguono; sull'altra
      scheda solo i campi vuoti; un'opportunita' Venditore blocca;
  07  alias e valori sconosciuti: conservati e segnalati, mai "Altro";
  08  separazione fra agenzie e permessi (agente: lettura si', gestione no);
  09  niente contatto/lead -> niente scheda; scheda nel Cestino -> il
      dettaglio non la tocca; fail-open;
  10  la scheda: campi nuovi salvati e riletti, catalogo validato, accessorio
      con quantita', form-options con le liste del catalogo;
  11  rapida -> /api/prefill -> dettagliata: decimali e campi assenti non si
      perdono ne' si inventano; modifica voluta; `campi_dichiarati`;
  12  trasferimento fallito: l'invio resta (`failed`), la dettagliata aspetta,
      il recupero e' osservabile e idempotente;
  13  down / up della 088 (le colonne della dettagliata restano);
  99  codice nuovo su un database senza la 087.
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


class JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def _persona(**kw):
    tag = uuid.uuid4().hex[:8]
    return {"nome": "Mario", "cognome": "Rossi", "email": f"mario.{tag}@example.test",
            "telefono": f"+39 333 {int(tag[:6], 16) % 10000000:07d}", **kw}


COMPLETA = {
    "comune": "Tortoreto", "microzona": "Lido Sud", "via": "Via del Mare", "civico": "12",
    "tipologia": "Appartamento", "mq": "85,5", "piano": "3", "locali": "Trilocale", "bagni": "2",
    "ascensore": "si", "anno": "1998", "stato": "ristrutturato",
    "posizioneMare": "seconda", "distanzaMare": "100-300", "barrieraMare": "no",
    "vistaMareYN": "si", "vistaMareDettaglio": "laterale",
    "pertinenze": "garage, cantina, balconi", "mqGarage": "18", "mqCantina": "0", "numBalconi": "2",
    "altroDescrizione": "Finemente arredato", "consenso_marketing": False,
}


@pytest.fixture
def sito(mondo, monkeypatch):
    m = mondo
    # `import main` con i moduli `core` VERI gia' caricati dalla fixture (e
    # patchati sul database usa-e-getta): `import_project_module` ricarica una
    # copia di `core` e lascerebbe i router importati da `main` legati a quella
    # copia anche per le prove che vengono dopo.
    import importlib
    main = importlib.import_module("main")
    psycopg2, dsn = m["psycopg2"], m["dsn"]
    monkeypatch.setattr(main, "get_connection", lambda: psycopg2.connect(dsn))
    # Le 28 colonne della stima dettagliata le crea la 088 (sul TEST mancano:
    # snapshot P26-0 del 2026-09-05): qui NON si esegue `database.py`.
    # il bridge CORE vero, sul database usa-e-getta (stesso modulo che la
    # fixture ha gia' puntato li': qui lo si dichiara, non lo si cambia)
    monkeypatch.setitem(main.core_service.repository.core_cursor.__wrapped__.__globals__, "get_connection",
                        lambda: psycopg2.connect(dsn))
    monkeypatch.setattr(main, "genera_pdf_stima", lambda *a, **k: "reports/stima_test.pdf")
    monkeypatch.setattr(main, "invia_mail", lambda *a, **k: True)
    monkeypatch.setattr(main, "invia_whatsapp", lambda *a, **k: None)
    monkeypatch.setattr(main.communication_service, "enqueue", lambda *a, **k: None)
    monkeypatch.setattr(main.seller_intelligence_service, "safe_record_event", lambda **k: None)
    monkeypatch.setattr(main.followup_service, "safe_run_followup", lambda **k: None)
    monkeypatch.setattr(main.property_watch_service, "safe_ensure_watch_for_stima", lambda *a, **k: None)
    monkeypatch.setattr(main.owner_provisioning, "safe_provision_for_public_stima", lambda *a, **k: None)
    agenzia = {"id": 1}
    motore = []
    originale = main.compute_from_payload

    def calcolo(payload):
        motore.append(dict(payload))
        return originale(payload)

    monkeypatch.setattr(main, "compute_from_payload", calcolo)

    def instradamento(conn, *, comune):
        a = agenzia["id"]
        return (SimpleNamespace(agency_id=a, require_agency=lambda: a),
                SimpleNamespace(agency_id=a, source="test", matched_value=comune))

    monkeypatch.setattr(main, "_routed_public_stima_system_context", instradamento)

    def stima(payload, *, agency=1):
        agenzia["id"] = agency
        return asyncio.run(main.salva_stima(JsonRequest(dict(payload))))

    def dettaglio(payload):
        return asyncio.run(main.salva_stima_dettagliata(JsonRequest(dict(payload))))

    def prefill(stima_id):
        token = _q(m, "SELECT token FROM stime WHERE id = %s", (stima_id,))[0][0]
        return asyncio.run(main.prefill(t=str(token)))

    def dettaglio_dal_prefill(stima_id, **modifiche):
        """Cio' che il form della dettagliata invia quando il cliente non
        tocca i campi precompilati: i valori di /api/prefill (senza i dati di
        contatto), con eventuali modifiche."""
        base = {k: v for k, v in prefill(stima_id).items() if k not in ("id", "nome", "cognome", "email", "telefono")}
        return dettaglio({**{k: ("" if v is None else v) for k, v in base.items()}, "stima_id": stima_id, **modifiche})

    def invio(kind, chiave):
        colonna = "stima_id" if kind == "quick" else "detail_id"
        r = _q(m, f"SELECT status, reason, attempts, property_id, declared, prefill, last_error FROM site_submissions "
                  f"WHERE kind = %s AND {colonna} = %s", (kind, chiave))
        return dict(zip(("status", "reason", "attempts", "property_id", "declared", "prefill", "last_error"), r[0])) if r else None

    def scheda_di(stima_id):
        r = _q(m, "SELECT property_id FROM property_site_sources WHERE stima_id = %s AND status = 'active'", (stima_id,))
        return r[0][0] if r else None

    def scheda(pid):
        return dict(zip(*_riga(m, "SELECT * FROM properties WHERE id = %s", (pid,))))

    yield SimpleNamespace(m=m, main=main, stima=stima, dettaglio=dettaglio, scheda_di=scheda_di, scheda=scheda,
                          motore=motore, api=m["api"], prefill=prefill, dettaglio_dal_prefill=dettaglio_dal_prefill,
                          invio=invio)
    # Pulizia PRIMA di quella della fixture `mondo` (DELETE di tutte le schede
    # di prova in un solo statement): nella 087 la provenienza `relinked` ha
    # `relinked_to_property_id` ON DELETE SET NULL ma il CHECK pretende il
    # collegamento, quindi la cancellazione FISICA della scheda di destinazione
    # fallisce se la provenienza c'e' ancora (dipende dall'ordine delle righe).
    # Nessun percorso dell'applicazione cancella fisicamente una scheda (il
    # Cestino e' logico): limite documentato nel report, non corretto qui.
    _q(m, "DELETE FROM property_site_sources")


def _riga(m, sql, params):
    with m["conn"].cursor() as cur:
        cur.execute(sql, params)
        riga = cur.fetchone()
        return [d[0] for d in cur.description], list(riga)


def _accessori(m, pid):
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in
            _q(m, "SELECT kind, surface_sqm, quantity, cadastral_status, source FROM property_accessories "
                  "WHERE property_id = %s ORDER BY id", (pid,))}


def _fonti(s, pid, chi="owner_a"):
    r = s.api(chi).get(f"/api/property/properties/{pid}/site-sources")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------

def test_01_stima_completa_crea_una_scheda_di_censimento(sito):
    s = sito
    risposta = s.stima({**COMPLETA, **_persona()})
    sid = risposta["id"]
    pid = s.scheda_di(sid)
    assert pid is not None
    p = s.scheda(pid)
    assert (p["record_kind"], p["commercial_status"], p["source"]) == ("census", "draft", "stima360")
    assert p["assigned_agent_id"] is None and p["mandate_type"] is None and p["acquisition_id"] is None
    assert p["code"].startswith("IMM-") and p["building_id"] is None and p["parent_property_id"] is None
    attesi = {"city": "Tortoreto", "region": "Abruzzo", "province": "TE", "microzone": "Lido Sud",
              "address": "Via del Mare", "civic_number": "12", "property_type": "apartment",
              "surface_sqm": Decimal("85.50"), "floor": "3", "rooms": 3, "bathrooms": 2, "elevator": True,
              "year_built": 1998, "condition": "ristrutturato", "sea_position": "seconda",
              "sea_distance": "100-300", "sea_barrier": False, "sea_view": True, "sea_view_detail": "laterale",
              "other_features": "Finemente arredato", "commercial_surface_sqm": None, "energy_class": None}
    assert {k: p[k] for k in attesi} == attesi
    # mq della cantina "0" = non dichiarati (NULL), non zero; balconi: 2, compresi
    assert _accessori(s.m, pid) == {"box": (Decimal("18.00"), None, "unknown", "stima360"),
                                    "cantina": (None, None, "unknown", "stima360"),
                                    "balcone": (None, 2, "included", "stima360")}
    # lead `origin`, contatto con ruolo neutro (mai proprietario d'ufficio)
    lead = _q(s.m, "SELECT lead_id FROM lead_stime WHERE stima_id = %s", (sid,))[0][0]
    assert _q(s.m, "SELECT relation_type FROM property_leads WHERE property_id = %s AND lead_id = %s", (pid, lead)) == [["origin"]]
    assert [r[0] for r in _q(s.m, "SELECT role FROM property_contacts WHERE property_id = %s", (pid,))] == ["contact"]
    # la stima resta quella di sempre (default compresi), la scheda no
    assert _q(s.m, "SELECT mq, mqcantina FROM stime WHERE id = %s", (sid,))[0][:] == [86, 0]
    # motore invariato: stessi input -> stessi numeri della base 3d3c3c7
    assert (risposta["price_exact"], risposta["eur_mq_finale"], risposta["valore_pertinenze"], risposta["base_mq"]) \
        == (244126.0, 2504.4, 30000.0, 1650.0)
    fonti = _fonti(s, pid)
    assert fonti["installed"] and len(fonti["items"]) == 1
    f = fonti["items"][0]
    assert (f["origin"], f["status"], f["stima_id"]) == ("auto", "active", sid)
    assert f["declared"]["rooms"] == {"value": 3, "raw": "Trilocale"}
    assert f["declared"]["surface_sqm"] == {"value": "85.50", "raw": "85,5"}
    assert "email" not in str(f["declared"]) and "nome" not in f["declared"]
    storico = _q(s.m, "SELECT description FROM activities WHERE property_id = %s", (pid,))
    assert any("creata dalla stima Stima360" in r[0] for r in storico)


def test_02_form_leggero_solo_cio_che_e_stato_inviato(sito):
    s = sito
    risposta = s.stima({"comune": "Tortoreto", "microzona": "Alto", "mq": 70, "mqGarage": 0, **_persona()})
    pid = s.scheda_di(risposta["id"])
    p = s.scheda(pid)
    assert (p["city"], p["microzone"], p["surface_sqm"]) == ("Tortoreto", "Alto", Decimal("70.00"))
    for campo in ("floor", "rooms", "bathrooms", "elevator", "year_built", "condition", "sea_position",
                  "sea_distance", "sea_barrier", "sea_view", "address", "civic_number"):
        assert p[campo] is None, campo                     # i default di `stime` non diventano dati
    # tipologia non inviata: valore tecnico `other` (NOT NULL) ma «Da verificare», non «Altro»
    assert p["property_type"] == "other"
    assert p["metadata"]["site_unverified"]["property_type"] == {"raw": None, "reason": "Tipologia non dichiarata dal sito"}
    assert p["title"].startswith("Tipologia da verificare") and "Altro" not in p["title"]
    assert _accessori(s.m, pid) == {}
    assert _q(s.m, "SELECT piano, locali, anno, via FROM stime WHERE id = %s", (risposta["id"],))[0][:] \
        == ["1", 3, 2000, "Zona"]
    assert (risposta["price_exact"], risposta["eur_mq_finale"]) == (81620.0, 1166.0)     # motore invariato
    voce = _fonti(s, pid)["items"][0]
    assert {"site_field": "tipologia", "raw": None, "reason": "Tipologia non dichiarata dal sito"} in voce["unmapped"]
    # scegliere la tipologia chiude il «Da verificare» e rigenera la descrizione
    r = s.api().patch(f"/api/property/properties/{pid}", json={"property_type": "apartment"})
    assert r.status_code == 200, r.text
    p = s.scheda(pid)
    assert "site_unverified" not in (p["metadata"] or {}) and p["title"].startswith("Appartamento")
    # tipologia sconosciuta dalla rapida, poi riconosciuta nella dettagliata: scritta sopra il valore tecnico
    sid = s.stima({"comune": "Tortoreto", "mq": 60, "tipologia": "Loft", **_persona()})["id"]
    pid = s.scheda_di(sid)
    assert s.scheda(pid)["metadata"]["site_unverified"]["property_type"]["raw"] == "Loft"
    s.dettaglio_dal_prefill(sid)                                  # «Loft» rimandato com'era: resta da verificare
    assert s.scheda(pid)["metadata"]["site_unverified"]["property_type"]["raw"] == "Loft"
    s.dettaglio_dal_prefill(sid, tipologia="Villa")
    p = s.scheda(pid)
    assert p["property_type"] == "villa" and "site_unverified" not in (p["metadata"] or {})
    assert p["title"].startswith("Villa")


def test_03_dettagliata_aggiorna_la_stessa_scheda_senza_toccare_le_correzioni(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    # l'agente corregge i locali e la superficie del garage
    r = s.api().patch(f"/api/property/properties/{pid}", json={"rooms": 4})
    assert r.status_code == 200, r.text
    garage = _q(s.m, "SELECT id FROM property_accessories WHERE property_id = %s AND kind = 'box'", (pid,))[0][0]
    assert s.api().patch(f"/api/property/properties/{pid}/accessories/{garage}", json={"surface_sqm": 20}).status_code == 200
    prima = _q(s.m, "SELECT count(*) FROM properties")[0][0]
    # il form arriva precompilato da /api/prefill; il cliente cambia stato, locali, garage e aggiunge il resto
    esito = s.dettaglio_dal_prefill(sid, stato="nuovo", locali="2", classe="c", riscaldamento="Autonomo a metano",
                                    condizionatore="si", condiz_tipo="Split", spese_cond="60", esposizione="Sud-Est",
                                    arredo="Arredato", pertinenze="garage, cantina, balconi, terrazzo", mqGarage="19",
                                    mqTerrazzo="12,5", note="Chiamare dopo le 18", indirizzo="Via del Mare 12, Tortoreto")
    assert esito == {"ok": True}
    assert _q(s.m, "SELECT count(*) FROM properties")[0][0] == prima        # nessuna scheda nuova
    p = s.scheda(pid)
    assert p["rooms"] == 4                                                   # corretto dall'agente: resta
    assert p["condition"] == "nuovo"                                         # cambiato dal cliente, non toccato dall'agente
    assert p["surface_sqm"] == Decimal("85.50")                              # 86 precompilato e rimandato: i decimali restano
    assert (p["energy_class"], p["heating"], p["air_conditioning"], p["air_conditioning_type"],
            p["condo_fees"], p["exposure"], p["furnishing"]) == (
        "C", "Autonomo a metano", "si", "Split", Decimal("60.00"), "Sud-Est", "Arredato")
    acc = _accessori(s.m, pid)
    assert acc["box"][0] == Decimal("20.00")                                 # corretto dall'agente: resta
    assert acc["terrazzo"] == (Decimal("12.50"), None, "unknown", "stima360")
    voce = _fonti(s, pid)["items"][0]
    aperti = {c["field"]: c for c in voce["conflicts"] if c["status"] == "open"}
    assert set(aperti) == {"rooms", "accessory:box"}
    assert (aperti["rooms"]["site_value"], aperti["rooms"]["current_value"]) == (2, 4)
    assert {"surface_sqm", "floor", "bathrooms", "elevator", "year_built"} <= set(voce["declared"]["prefilled_unchanged"])
    assert any(u["site_field"] == "indirizzo" for u in voce["unmapped"])     # testo libero: da riportare a mano
    assert len(voce["detail_ids"]) == 1
    detail_id = voce["detail_ids"][0]
    invio = s.invio("detail", detail_id)
    assert invio["status"] == "synced" and invio["prefill"]["mq"] == 86 and "nome" not in invio["declared"]
    assert "note" not in invio["declared"] and "note" in invio["declared"]["_altre_chiavi"]
    # lo stesso dettaglio ripetuto (stessa riga) non fa nulla; un dettaglio orfano non tocca schede
    assert s.main.property_site_sync.sync_detail(stima_id=sid, detail_id=detail_id, raw={"locali": "5"})["status"] == "replica"
    assert s.dettaglio({"locali": "5", "nome": "Orfano"}) == {"ok": True}
    orfano = _q(s.m, "SELECT max(id) FROM stime_dettagliate")[0][0]
    assert s.invio("detail", orfano)["reason"] == "orphan_detail"
    assert s.scheda(pid)["rooms"] == 4


def test_04_ritentativi_con_identita_della_richiesta_e_invii_distinti(sito):
    s = sito
    persona = _persona()
    chiave = str(uuid.uuid4())
    primo = s.stima({**COMPLETA, **persona, "client_request_id": chiave})["id"]
    pid = s.scheda_di(primo)
    # la STESSA richiesta oltre 24 ore (la riga viene invecchiata di 30 giorni)
    _q(s.m, "UPDATE site_submissions SET created_at = NOW() - interval '30 days' WHERE stima_id = %s", (primo,))
    _q(s.m, "UPDATE property_site_sources SET created_at = NOW() - interval '30 days' WHERE stima_id = %s", (primo,))
    secondo = s.stima({**COMPLETA, **persona, "client_request_id": chiave})["id"]
    assert s.scheda_di(secondo) == pid
    assert [r[0] for r in _q(s.m, "SELECT origin FROM property_site_sources WHERE property_id = %s ORDER BY id", (pid,))] \
        == ["auto", "retry"]
    assert sorted(r[0] for r in _q(s.m, "SELECT relation_type FROM property_leads WHERE property_id = %s", (pid,))) \
        == ["origin", "related"]
    assert s.invio("quick", secondo)["reason"] == "same_request"
    # due richieste DISTINTE con dati uguali (chiavi diverse): due schede, doppione segnalato, mai unito
    terzo = s.stima({**COMPLETA, **persona, "client_request_id": str(uuid.uuid4())})["id"]
    altra = s.scheda_di(terzo)
    assert altra != pid
    dup = {d["property_id"]: d["reasons"] for d in _fonti(s, altra)["items"][0]["duplicates"]}
    assert dup[pid][0] == "same_submission_data"
    # client attuale (senza identita'): dati e contatto uguali NON provano un ritentativo
    persona2 = _persona()
    a = s.stima({**COMPLETA, **persona2, "via": "Via dei Pini", "civico": "4"})["id"]
    b = s.stima({**COMPLETA, **persona2, "via": "Via dei Pini", "civico": "4"})["id"]
    assert s.scheda_di(a) != s.scheda_di(b)
    dup = {d["property_id"]: d["reasons"] for d in _fonti(s, s.scheda_di(b))["items"][0]["duplicates"]}
    assert dup[s.scheda_di(a)] == ["same_submission_data", "same_contact", "same_address"]
    # stesso indirizzo, contatto diverso: segnalato per indirizzo
    quarto = s.stima({**COMPLETA, **_persona(), "stato": "buono"})["id"]
    quarta = s.scheda_di(quarto)
    dup = _fonti(s, quarta)["items"][0]["duplicates"]
    assert (pid, ["same_address"]) in [(d["property_id"], d["reasons"]) for d in dup]
    # «Non e' lo stesso»: la segnalazione si chiude
    fonte = _fonti(s, quarta)["items"][0]["id"]
    r = s.api().post(f"/api/property/properties/{quarta}/site-sources/{fonte}/duplicates/{pid}/dismiss")
    assert r.status_code == 200 and all(d["dismissed"] for d in r.json()["duplicates"] if d["property_id"] == pid)


def test_04b_richieste_concorrenti_con_la_stessa_identita(sito):
    s = sito
    from tests.test_censimento_3_backend_postgres import _in_parallelo
    persona, chiave = _persona(), str(uuid.uuid4())
    esiti = _in_parallelo([lambda: s.stima({**COMPLETA, **persona, "client_request_id": chiave}) for _ in range(3)])
    schede = {s.scheda_di(e["id"]) for e in esiti}
    assert len(schede) == 1 and None not in schede
    assert _q(s.m, "SELECT count(*) FROM site_submissions WHERE client_request_id = %s AND status = 'synced'",
              (chiave,))[0][0] == 3


def test_05_applica_e_ignora(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    assert s.api().patch(f"/api/property/properties/{pid}", json={"rooms": 4, "year_built": 2001}).status_code == 200
    s.dettaglio_dal_prefill(sid, locali="2", anno="1997")
    voce = _fonti(s, pid)["items"][0]
    aperti = {c["field"]: c for c in voce["conflicts"] if c["status"] == "open"}
    base = f"/api/property/properties/{pid}/site-sources/{voce['id']}/conflicts"
    r = s.api().post(base, json={"conflict_id": aperti["rooms"]["id"], "action": "apply"})
    assert r.status_code == 200, r.text
    r = s.api().post(base, json={"conflict_id": aperti["year_built"]["id"], "action": "ignore"})
    assert r.status_code == 200, r.text
    assert s.api().post(base, json={"conflict_id": aperti["rooms"]["id"], "action": "apply"}).json()["code"] \
        == "CONFLICT_ALREADY_RESOLVED"
    p = s.scheda(pid)
    assert (p["rooms"], p["year_built"]) == (2, 2001)
    # lo stesso valore ignorato non torna; i locali ora seguono di nuovo il sito
    s.dettaglio_dal_prefill(sid, locali="5", anno="1997")
    voce = _fonti(s, pid)["items"][0]
    assert [c for c in voce["conflicts"] if c["status"] == "open"] == []
    assert s.scheda(pid)["rooms"] == 5


def test_06_collega_a_un_altro_immobile(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    auto = s.scheda_di(sid)
    # l'immobile vero, gia' in archivio (commerciale), con dati propri
    r = s.api().post("/api/property/properties", json={"property_type": "apartment", "city": "Tortoreto",
                                                        "address": "Via del Mare", "civic_number": "12",
                                                        "rooms": 4, "surface_sqm": 90})
    assert r.status_code == 201, r.text
    vero = r.json()["id"]
    fonte = _fonti(s, auto)["items"][0]["id"]
    r = s.api().post(f"/api/property/properties/{auto}/site-sources/{fonte}/relink", json={"target_code": r.json()["code"]})
    assert r.status_code == 200, r.text
    p = s.scheda(vero)
    assert (p["rooms"], p["surface_sqm"], p["condition"], p["floor"]) == (4, Decimal("90.00"), "ristrutturato", "3")
    assert {c["field"] for c in r.json()["conflicts"]} == {"rooms", "surface_sqm"}
    assert s.scheda_di(sid) == vero
    lead = _q(s.m, "SELECT lead_id FROM lead_stime WHERE stima_id = %s", (sid,))[0][0]
    assert _q(s.m, "SELECT property_id, relation_type FROM property_leads WHERE lead_id = %s", (lead,)) == [[vero, "origin"]]
    assert _q(s.m, "SELECT count(*) FROM property_contacts WHERE property_id = %s", (auto,))[0][0] == 0
    assert _q(s.m, "SELECT role FROM property_contacts WHERE property_id = %s", (vero,)) == [["contact"]]
    # la scheda nata in automatico NON si cancella: resta, con la stima segnata come spostata
    assert s.scheda(auto)["deleted_at"] is None
    vecchia = _fonti(s, auto)["items"][0]
    assert (vecchia["status"], vecchia["relinked_to"]["id"]) == ("relinked", vero)
    # il dettaglio successivo aggiorna la scheda collegata
    s.dettaglio({"stima_id": sid, "classe": "B"})
    assert s.scheda(vero)["energy_class"] == "B" and s.scheda(auto)["energy_class"] is None
    # un'opportunita' Venditore sul lead blocca un nuovo spostamento
    _q(s.m, "UPDATE property_leads SET relation_type = 'seller' WHERE lead_id = %s", (lead,))
    nuova = _fonti(s, vero)["items"][0]["id"]
    r = s.api().post(f"/api/property/properties/{vero}/site-sources/{nuova}/relink", json={"target_property_id": auto})
    assert (r.status_code, r.json()["code"]) == (409, "SELLER_LINK_ACTIVE")


def test_07_alias_e_valori_sconosciuti_conservati(sito):
    s = sito
    sid = s.stima({"comune": "Tortoreto", "microzona": "Lido Nord ", "mq": "64", "tipologia": " villa ",
                   "piano": "terra", "locali": "Bilocale", "ascensore": "no", "stato": "abitabile",
                   "posizioneMare": "fronte", "distanzaMare": "300 - 500 m", "vistaMareYN": "no",
                   "pertinenze": "Garage; Posto auto| posto barca, piscina condominiale", "mqPostoAuto": "12.5",
                   **_persona()})["id"]
    pid = s.scheda_di(sid)
    p = s.scheda(pid)
    assert (p["property_type"], p["floor"], p["rooms"], p["elevator"], p["microzone"], p["sea_distance"], p["sea_view"]) \
        == ("villa", "T", 2, False, "Lido Nord", "300-500", False)
    assert p["condition"] is None and p["sea_position"] is None              # non indovinati
    assert _accessori(s.m, pid) == {"box": (None, None, "unknown", "stima360"),
                                    "posto_auto": (Decimal("12.50"), None, "unknown", "stima360"),
                                    "piscina": (None, None, "unknown", "stima360")}
    ignoti = {(u["site_field"], u["raw"]) for u in _fonti(s, pid)["items"][0]["unmapped"]}
    assert {("stato", "abitabile"), ("posizioneMare", "fronte"), ("pertinenze", "posto barca")} <= ignoti
    assert "altro" not in _accessori(s.m, pid)                                # mai "Altro" in silenzio


def test_08_agenzie_separate_e_permessi(sito):
    s = sito
    sid = s.stima({**COMPLETA, **_persona()}, agency=2)["id"]
    pid = s.scheda_di(sid)
    assert _q(s.m, "SELECT agency_id FROM properties WHERE id = %s", (pid,))[0][0] == 2
    assert s.api("owner_a").get(f"/api/property/properties/{pid}/site-sources").status_code == 404
    assert s.api("owner_b").get(f"/api/property/properties/{pid}/site-sources").status_code == 200
    fonte = _fonti(s, pid, "owner_b")["items"][0]["id"]
    # collegamento verso una scheda di un'altra agenzia: inesistente
    altra = s.api("owner_a").post("/api/property/properties", json={"property_type": "apartment"}).json()["id"]
    r = s.api("owner_b").post(f"/api/property/properties/{pid}/site-sources/{fonte}/relink",
                              json={"target_property_id": altra})
    assert r.status_code == 404
    # agente: legge la provenienza, non la gestisce (scheda non sua)
    sid1 = s.stima({**COMPLETA, **_persona()})["id"]
    pid1 = s.scheda_di(sid1)
    lettura = _fonti(s, pid1, "agent_a")
    assert lettura["can_manage"] is False
    voce = lettura["items"][0]
    r = s.api("agent_a").post(f"/api/property/properties/{pid1}/site-sources/{voce['id']}/duplicates/1/dismiss")
    assert r.status_code == 403


def test_09_senza_lead_niente_scheda_cestino_e_fail_open(sito, monkeypatch):
    s = sito
    prima = _q(s.m, "SELECT count(*) FROM properties")[0][0]
    risposta = s.stima({**COMPLETA, "nome": None, "cognome": None, "email": None, "telefono": None})
    assert risposta["success"] is True
    assert s.scheda_di(risposta["id"]) is None and _q(s.m, "SELECT count(*) FROM properties")[0][0] == prima
    assert s.invio("quick", risposta["id"])["reason"] == "no_contact_lead"      # conservato, saltato con motivo
    # scheda nel Cestino: il dettaglio non la tocca (l'invio resta, con il motivo)
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    pid = s.scheda_di(sid)
    r = s.api().post(f"/api/property/properties/{pid}/trash", json={"reason_code": "duplicate"})
    assert r.status_code == 200, r.text
    s.dettaglio_dal_prefill(sid, classe="A4")
    dettaglio = _q(s.m, "SELECT max(id) FROM stime_dettagliate")[0][0]
    assert s.invio("detail", dettaglio)["reason"] == "property_in_trash"
    assert s.scheda(pid)["energy_class"] is None
    # fail-open: un errore della sincronizzazione non cambia la risposta del sito
    def guasto(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(s.main.property_site_sync, "sync_public_stima", guasto)
    assert s.stima({**COMPLETA, **_persona()})["success"] is True


def test_10_scheda_campi_nuovi_catalogo_e_accessori(sito):
    s = sito
    opzioni = s.api().get("/api/property/form-options").json()
    assert [o["value"] for o in opzioni["conditions"]] == ["nuovo", "ristrutturato", "buono", "scarso", "grezzo"]
    assert [o["value"] for o in opzioni["sea_positions"]] == ["frontemare", "seconda", "oltre"]
    assert "taverna" in [o["value"] for o in opzioni["accessory_kinds"]]
    r = s.api().post("/api/property/properties", json={"property_type": "apartment", "sea_position": "frontemare",
                                                        "sea_view": True, "condo_fees": "0", "heating": "Pompa di calore"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = s.api().patch(f"/api/property/properties/{pid}", json={"sea_view": False, "sea_view_detail": None,
                                                               "other_features": "Doppio ingresso"})
    assert r.status_code == 200, r.text
    p = s.api().get(f"/api/property/properties/{pid}").json()
    assert (p["sea_position"], p["sea_view"], p["condo_fees"], p["heating"], p["other_features"]) \
        == ("frontemare", False, 0.0, "Pompa di calore", "Doppio ingresso")
    assert s.api().patch(f"/api/property/properties/{pid}", json={"sea_position": "fronte"}).status_code == 400
    r = s.api().post(f"/api/property/properties/{pid}/accessories",
                     json={"kind": "balcone", "cadastral_status": "included", "quantity": 3})
    assert r.status_code == 201 and (r.json()["quantity"], r.json()["source"]) == (3, "manual")
    assert s.api().post(f"/api/property/properties/{pid}/accessories",
                        json={"kind": "posto_auto", "quantity": 0}).status_code == 422


def test_11_prefill_decimali_campi_assenti_e_modifiche_volute(sito):
    """rapida (85,5 mq, piano/locali/anno non inviati) -> /api/prefill (86 e i
    default di `stime`) -> dettagliata rimandata tale e quale -> scheda: niente
    perso, niente inventato. Poi una modifica voluta e una conferma esplicita."""
    s = sito
    sid = s.stima({"comune": "Tortoreto", "microzona": "Lido Sud", "tipologia": "Appartamento", "mq": "85,5",
                   **_persona()})["id"]
    pid = s.scheda_di(sid)
    servito = s.prefill(sid)
    assert (servito["mq"], servito["piano"], servito["locali"], servito["anno"], servito["via"]) == (86, "1", 3, 2000, "Zona")
    # 1. dettagliata lasciata com'era (piu' un dato nuovo): i default non diventano dichiarati, i decimali restano
    assert s.dettaglio_dal_prefill(sid, classe="D") == {"ok": True}
    p = s.scheda(pid)
    assert p["surface_sqm"] == Decimal("85.50") and p["energy_class"] == "D"
    assert (p["floor"], p["rooms"], p["year_built"], p["address"]) == (None, None, None, None)
    voce = _fonti(s, pid)["items"][0]
    assert [c for c in voce["conflicts"] if c["status"] == "open"] == []
    assert {"surface_sqm", "floor", "rooms", "year_built"} <= set(voce["declared"]["prefilled_unchanged"])
    # 2. modifica voluta del cliente: un valore DIVERSO dal precompilato e' una correzione esplicita
    s.dettaglio_dal_prefill(sid, mq="90", piano="2")
    p = s.scheda(pid)
    assert (p["surface_sqm"], p["floor"], p["rooms"]) == (Decimal("90.00"), "2", None)
    # 3. il prefill legge ancora `stime` (86, piano 1): rimandarlo non annulla la correzione
    s.dettaglio_dal_prefill(sid)
    assert (s.scheda(pid)["surface_sqm"], s.scheda(pid)["floor"]) == (Decimal("90.00"), "2")
    # 4. il sito dichiara che il cliente ha CONFERMATO locali e anno (uguali al precompilato)
    s.dettaglio_dal_prefill(sid, campi_dichiarati=["locali", "anno"])
    p = s.scheda(pid)
    assert (p["rooms"], p["year_built"], p["floor"]) == (3, 2000, "2")
    dettaglio = _q(s.m, "SELECT max(id) FROM stime_dettagliate")[0][0]
    assert _q(s.m, "SELECT declared_fields FROM site_submissions WHERE detail_id = %s", (dettaglio,))[0][0] == ["anno", "locali"]


def test_12_trasferimento_fallito_resta_visibile_e_si_recupera(sito, monkeypatch):
    s = sito
    sync = s.main.property_site_sync
    originale = sync._census._inserisci_unita

    def guasto(*a, **k):
        raise RuntimeError("guasto simulato")

    monkeypatch.setattr(sync._census, "_inserisci_unita", guasto)
    risposta = s.stima({**COMPLETA, **_persona()})
    assert risposta["success"] is True                                      # il sito risponde come sempre
    sid = risposta["id"]
    assert s.scheda_di(sid) is None
    rapida = s.invio("quick", sid)
    assert (rapida["status"], rapida["reason"], rapida["attempts"], rapida["last_error"]) == ("failed", "error", 1, "RuntimeError")
    assert rapida["declared"]["mq"] == "85,5" and "email" not in rapida["declared"]   # il valore COME inviato
    # la dettagliata arriva prima del recupero: conservata, in attesa della rapida
    s.dettaglio_dal_prefill(sid, classe="B", locali="4")
    dettaglio = _q(s.m, "SELECT max(id) FROM stime_dettagliate")[0][0]
    assert (s.invio("detail", dettaglio)["status"], s.invio("detail", dettaglio)["reason"]) == ("pending", "waiting_quick")
    monkeypatch.setattr(sync._census, "_inserisci_unita", originale)
    conteggi = {(r["kind"], r["status"], r["reason"]): r["n"] for r in sync.census()["submissions"]}
    assert conteggi[("quick", "failed", "error")] >= 1 and conteggi[("detail", "pending", "waiting_quick")] >= 1
    # gli invii appena arrivati non si toccano (potrebbero essere in corso)
    assert sid not in [v["stima_id"] for v in sync.recover(apply=False)["items"]]
    elenco = [v for v in sync.recover(apply=False, min_age_seconds=0)["items"] if v["stima_id"] == sid]
    assert [v["kind"] for v in elenco] == ["quick", "detail"] and s.scheda_di(sid) is None     # solo l'elenco
    esito = [v for v in sync.recover(apply=True, min_age_seconds=0)["items"] if v["stima_id"] == sid]
    assert [(v["kind"], v["outcome"]) for v in esito] == [("quick", "created"), ("detail", "updated")]
    pid = s.scheda_di(sid)
    p = s.scheda(pid)
    # i valori DICHIARATI (85,5 e i locali corretti), non quelli di `stime` (86, default)
    assert (p["surface_sqm"], p["rooms"], p["energy_class"], p["floor"]) == (Decimal("85.50"), 4, "B", "3")
    assert (s.invio("quick", sid)["status"], s.invio("detail", dettaglio)["status"]) == ("synced", "synced")
    # idempotente: ripetere non trova nulla e non duplica
    assert sid not in [v["stima_id"] for v in sync.recover(apply=True, min_age_seconds=0)["items"]]
    assert _q(s.m, "SELECT count(*) FROM property_site_sources WHERE stima_id = %s", (sid,))[0][0] == 1
    # lo script: census in sola lettura, --apply solo con il database confermato
    import importlib
    script = importlib.import_module("scripts.site_sync_recover")
    monkeypatch.setenv("DB_NAME", "stima360_db_test")
    assert script.main(["--apply"]) == 2
    assert script.main(["--census"]) == 0


def test_13_down_e_up_della_088(sito, monkeypatch):
    """La down si ferma con invii non trasferiti; senza, toglie la ricezione e
    LASCIA le colonne della dettagliata (che continua a salvarsi); la up si
    riapplica."""
    from pathlib import Path
    s = sito
    sync = s.main.property_site_sync
    cartella = Path(__file__).resolve().parents[1] / "migrations"
    su = (cartella / "088_catalogo_canonico_1b_site_inbox.sql").read_text(encoding="utf-8")
    giu = (cartella / "088_catalogo_canonico_1b_site_inbox_down.sql").read_text(encoding="utf-8")
    originale = sync._census._inserisci_unita
    monkeypatch.setattr(sync._census, "_inserisci_unita", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    monkeypatch.setattr(sync._census, "_inserisci_unita", originale)
    assert s.invio("quick", sid)["status"] == "failed"
    with s.m["conn"].cursor() as cur:
        with pytest.raises(Exception, match="088 down"):
            cur.execute(giu)
        cur.execute("ROLLBACK")
    sync.recover(apply=True, min_age_seconds=0)
    assert _q(s.m, "SELECT count(*) FROM site_submissions WHERE status IN ('pending', 'failed')")[0][0] == 0
    _q(s.m, giu)
    try:
        assert _q(s.m, "SELECT to_regclass('public.site_submissions')")[0][0] is None
        colonne = {r[0] for r in _q(s.m, "SELECT column_name FROM information_schema.columns "
                                         "WHERE table_name = 'stime_dettagliate'", ())}
        assert {"tipologia", "mq", "pertinenze", "numbalconi", "altrodescrizione"} <= colonne
        prima = _q(s.m, "SELECT count(*) FROM properties")[0][0]
        nuova = s.stima({**COMPLETA, **_persona()})
        assert nuova["success"] is True and _q(s.m, "SELECT count(*) FROM properties")[0][0] == prima
        assert s.dettaglio({"stima_id": nuova["id"], "classe": "A1", "mq": "70"}) == {"ok": True}
        assert sync.census() == {"installed": False}
        # la provenienza gia' scritta si legge ancora (basta la 087)
        assert _fonti(s, s.scheda_di(sid))["installed"] is True
    finally:
        with s.m["conn"].cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(su)
            cur.execute("COMMIT")
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    assert s.scheda_di(sid) is not None and s.invio("quick", sid)["status"] == "synced"


def test_99_ordine_di_rilascio_down_e_up_della_087(sito):
    """Codice nuovo su un database SENZA la 087 (ordine DB-first non
    rispettato): la stima del sito risponde come sempre e non scrive schede;
    le schede si creano e si leggono come prima. La down si ferma con dati dei
    tipi nuovi; senza, torna alla 086; la up si riapplica."""
    from pathlib import Path
    s = sito
    cartella = Path(__file__).resolve().parents[1] / "migrations"
    su = (cartella / "087_catalogo_canonico_1_site_attributes.sql").read_text(encoding="utf-8")
    giu = (cartella / "087_catalogo_canonico_1_site_attributes_down.sql").read_text(encoding="utf-8")
    pid = s.api().post("/api/property/properties", json={"property_type": "apartment"}).json()["id"]
    assert s.api().post(f"/api/property/properties/{pid}/accessories",
                        json={"kind": "taverna", "cadastral_status": "included"}).status_code == 201
    with s.m["conn"].cursor() as cur:
        with pytest.raises(Exception, match="087 down"):
            cur.execute(giu)
        cur.execute("ROLLBACK")                        # la down apre la sua transazione: si chiude
    assert _q(s.m, "SELECT to_regclass('public.property_site_sources') IS NOT NULL")[0][0] is True
    _q(s.m, "DELETE FROM property_accessories WHERE kind = 'taverna'")
    _q(s.m, giu)
    try:
        assert _q(s.m, "SELECT to_regclass('public.property_site_sources')")[0][0] is None
        prima = _q(s.m, "SELECT count(*) FROM properties")[0][0]
        risposta = s.stima({**COMPLETA, **_persona()})
        assert risposta["success"] is True and _q(s.m, "SELECT count(*) FROM properties")[0][0] == prima
        r = s.api().post("/api/property/properties", json={"property_type": "villa", "rooms": 5})
        assert r.status_code == 201, r.text
        assert s.api().get(f"/api/property/properties/{r.json()['id']}").status_code == 200
        rifiuto = s.api().patch(f"/api/property/properties/{r.json()['id']}", json={"sea_position": "seconda"})
        assert rifiuto.status_code == 409 and "087" in rifiuto.json()["detail"]      # leggibile, non un 500
        assert _fonti(s, r.json()["id"]) == {"installed": False, "items": [], "can_manage": False}
        assert s.api().post(f"/api/property/properties/{r.json()['id']}/accessories",
                            json={"kind": "cantina", "cadastral_status": "included"}).status_code == 201
    finally:
        with s.m["conn"].cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(su)
            cur.execute("COMMIT")
    assert _q(s.m, "SELECT to_regclass('public.property_site_sources') IS NOT NULL")[0][0] is True
    sid = s.stima({**COMPLETA, **_persona()})["id"]
    assert s.scheda_di(sid) is not None
