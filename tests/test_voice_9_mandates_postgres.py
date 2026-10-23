"""STIMA VOICE V9 - incarichi a voce sul server vero (PostgreSQL, fornitore finto).

Ogni scrittura passa dai servizi originali (generate_mandate, patch_acquisition,
create_acquisition, PATCH dell'immobile con lo storico prezzi), solo dopo una
conferma esplicita; le risposte semplici si capiscono senza il modello.

  g1  «Crea un incarico di vendita per l'appartamento di Francesco Ruvolo, prezzo
      185.000 euro»: due immobili suoi -> «quale?» -> «via Sanzio» (locale) ->
      «da quando?» -> «oggi» (locale) -> scheda con l'operazione «INCARICO
      EFFETTIVO» -> conferma -> incarico REALE (immobile in incarico, acquisizione
      «Acquisita», evento, storico prezzi); nessuna chiamata al modello dopo la prima
  g2  nessun doppione: lo stesso comando ora e' bloccato; una seconda esecuzione e
      una seconda conferma non generano nulla (un solo evento mandate_created)
  g3  la conferma vale per QUELLA situazione: se cambia prima dell'esecuzione,
      nulla si scrive e serve una nuova conferma
  g4  dati mancanti: tipo chiesto e dato a voce (locale); prezzo proposto
      dall'acquisizione e detto nella scheda
  p1  acquisizione prima del sopralluogo + prezzo: si aggiorna SOLO il prezzo
      richiesto dell'acquisizione (stato invariato, nessun incarico); senza prezzo:
      domanda e spiegazione, l'incarico non e' disponibile
  a1  nessuna acquisizione: STIMA lo dice e propone il percorso -> «domani alle 10»
      (locale) -> acquisizione + appuntamento in Agenda dopo la conferma, stato
      «Appuntamento fissato» (nessun sopralluogo finto); l'incarico resta non disponibile
  s1  permessi: l'acquisizione di un collega e' bloccata per un agente, non per il titolare
  s2  il proprietario detto non risulta collegato all'indirizzo detto: bloccato,
      nessun collegamento creato
  r1  prezzo di un incarico esistente: storico prezzi del servizio originale, motivo
      e autore; un agente non assegnato all'immobile e' bloccato
  r2  quale prezzo? se non e' chiaro si chiede -> «quello dell'acquisizione» (locale)
  i1  «Quali dati mancano...»: sola lettura, eseguita senza conferma, elenco dei dati;
      «Portami alla scheda...»: il collegamento all'incarico
  c1  un incarico si conferma da solo: insieme a un altro comando e' bloccato
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, conferma_tutto, q  # noqa: F401
from tests.test_voice_5_api_postgres import api, detta  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL")

FRASE = "Crea un incarico di vendita per l'appartamento di Francesco Ruvolo, prezzo 185.000 euro"
RUVOLO = {"first_name": "Francesco", "last_name": "Ruvolo"}


@pytest.fixture(scope="module", autouse=True)
def _seme(db):  # noqa: F811
    ids = db["ids"]

    def contatto(chiave, nome, cognome, agente):
        ids[chiave] = q(db, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name, "
                            "assigned_agent_id, status) VALUES (1, 'person', %s, %s, %s, %s, 'active') RETURNING id",
                        (f"{nome} {cognome}", nome, cognome, ids[agente]))[0][0]

    def immobile(chiave, codice, via, civico, agente, prezzo=None, proprietario=None):
        ids[chiave] = q(db, "INSERT INTO properties (code, title, property_type, agency_id, address, civic_number, "
                            "city, record_kind, commercial_status, assigned_agent_id, asking_price) VALUES "
                            "(%s, %s, 'apartment', 1, %s, %s, 'Tortoreto', 'crm', 'draft', %s, %s) RETURNING id",
                        (codice, f"Immobile {codice}", via, civico, ids[agente], prezzo))[0][0]
        if proprietario:
            q(db, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')",
              (ids[chiave], ids[proprietario]))

    giorni = iter(range(60, 90))       # un appuntamento per giorno: l'Agenda vieta le sovrapposizioni

    def acquisizione(chiave, immobile_, proprietario, agente, stato, prezzo=None):
        fra = f"{next(giorni)} days"
        incontro = q(db, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, "
                         "end_at, property_id, contact_id, source) VALUES (1, %s, 'seller_meeting', 'scheduled', "
                         "NOW() + %s::interval, NOW() + %s::interval + interval '1 hour', %s, %s, "
                         "'crm_manual') RETURNING id",
                     (ids[agente], fra, fra, ids[immobile_], ids[proprietario]))[0][0]
        ids[chiave] = q(db, "INSERT INTO acquisitions (agency_id, property_id, owner_contact_id, assigned_agent_id, "
                            "appointment_id, created_by_user_id, status, asking_price) VALUES "
                            "(1, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (ids[immobile_], ids[proprietario], ids[agente], incontro, ids[agente], stato, prezzo))[0][0]

    contatto("ruvolo", "Francesco", "Ruvolo", "agent_a1")
    immobile("sanzio", "TS14", "Via Sanzio", "14", "agent_a1", proprietario="ruvolo")
    acquisizione("acq_sanzio", "sanzio", "ruvolo", "agent_a1", "inspection_done", 180000)
    immobile("dante", "TD3", "Via Dante", "3", "agent_a1", proprietario="ruvolo")
    acquisizione("acq_dante", "dante", "ruvolo", "agent_a1", "appointment_set")
    contatto("bianchi", "Anna", "Bianchi", "agent_a1")
    immobile("verdi", "TV7", "Via Verdi", "7", "agent_a1", proprietario="bianchi")
    contatto("neri", "Paolo", "Neri", "agent_a2")
    immobile("po", "TP1", "Via Po", "1", "agent_a2", proprietario="neri")
    acquisizione("acq_po", "po", "neri", "agent_a2", "inspection_done", 120000)
    contatto("gialli", "Luca", "Gialli", "agent_a1")
    immobile("mare", "TM9", "Via Mare", "9", "agent_a1", prezzo=200000, proprietario="gialli")
    acquisizione("acq_mare", "mare", "gialli", "agent_a1", "inspection_done")
    q(db, "UPDATE properties SET acquisition_id = %s, mandate_type = 'Vendita', mandate_start = CURRENT_DATE - 10, "
          "commercial_status = 'mandate' WHERE id = %s", (ids["acq_mare"], ids["mare"]))
    q(db, "UPDATE acquisitions SET status = 'acquired', acquired_at = NOW() WHERE id = %s", (ids["acq_mare"],))
    contatto("rosa", "Carla", "Rosa", "agent_a1")
    immobile("fiume", "TF2", "Via Fiume", "2", "agent_a1", prezzo=110000, proprietario="rosa")
    acquisizione("acq_fiume", "fiume", "rosa", "agent_a1", "appointment_set", 100000)
    contatto("blu", "Sara", "Blu", "agent_a1")
    immobile("arno", "TA6", "Via Arno", "6", "agent_a1", proprietario="blu")
    acquisizione("acq_arno", "arno", "blu", "agent_a1", "valuation_presented", 150000)
    contatto("viola", "Giulio", "Viola", "agent_a1")
    immobile("tevere", "TT4", "Via Tevere", "4", "agent_a1", proprietario="viola")
    yield


def _comando(intent, quote, **campi):
    return {"intent": intent, "quote": quote, **campi}


def _rispondi(api, chi, cid, testo):  # noqa: F811
    r = api(chi).post(f"/api/voice/commands/{cid}/reply", json={"client_command_id": str(uuid.uuid4()), "text": testo})
    assert r.status_code == 200, r.text
    return r.json()


def _codici(passo):
    return [m["code"] for m in passo["reasons"]]


def _domande(passo):
    """Le domande VERE (cio' che una conferma non scioglie)."""
    return [m["code"] for m in passo["reasons"] if not m["confirmable"]]


def _esegui(api, db, chi, vista):  # noqa: F811
    conferma_tutto(db, chi, vista["command_id"])
    return api(chi).post(f"/api/voice/commands/{vista['command_id']}/execute").json()


def _prepara_ruvolo(api):  # noqa: F811
    vista = detta(api, "agent_a1", FRASE, [_comando("mandate_prepare", FRASE, owner=RUVOLO,
                                                     price_text="185.000 euro")]).json()
    return vista


def test_g1_g2_incarico_reale_dalla_voce_senza_doppioni(api, db):  # noqa: F811
    ids = db["ids"]
    vista = _prepara_ruvolo(api)
    p = vista["steps"][0]
    assert "mandate_property_choice" in _codici(p) and p["needs"] == "needs_answer"
    assert {c["id"] for c in p["candidates"]["property"]} == {ids["sanzio"], ids["dante"]}
    chiamate = len(api.finto.calls)
    vista = _rispondi(api, "agent_a1", vista["command_id"], "quello di via Sanzio")
    p = vista["steps"][0]
    assert _domande(p) == ["mandate_start_missing"], _codici(p)          # tipo e prezzo detti: solo l'inizio
    assert "Vuoi utilizzare questo immobile?" in next(m["message"] for m in p["reasons"]
                                                      if m["code"] == "reference_fuzzy")
    vista = _rispondi(api, "agent_a1", vista["command_id"], "oggi")
    assert len(api.finto.calls) == chiamate                              # due risposte, zero chiamate
    p = vista["steps"][0]
    s = p["summary"]
    assert p["needs"] == "needs_confirmation" and "mandate_will_generate" in _codici(p)
    assert "INCARICO EFFETTIVO" in next(m["message"] for m in p["reasons"] if m["code"] == "mandate_will_generate")
    assert "firma" in s["operation"] and s["owner"] == "Francesco Ruvolo" and s["property"].startswith("Via Sanzio 14")
    assert s["proposed_price"] == "185000.00" and s["mandate_type"] == "Vendita"
    assert s["mandate_start"] == date.today().isoformat() and s["missing"] == []
    assert q(db, "SELECT acquisition_id FROM properties WHERE id = %s", (ids["sanzio"],))[0][0] is None   # nulla prima
    fatto = _esegui(api, db, "agent_a1", vista)
    assert fatto["status"] == "completed", fatto
    riga = q(db, "SELECT acquisition_id, mandate_type, mandate_start, commercial_status, asking_price FROM properties "
                 "WHERE id = %s", (ids["sanzio"],))[0]
    assert list(riga) == [ids["acq_sanzio"], "Vendita", date.today(), "mandate", Decimal("185000.00")]
    assert q(db, "SELECT status FROM acquisitions WHERE id = %s", (ids["acq_sanzio"],))[0][0] == "acquired"
    assert q(db, "SELECT count(*) FROM acquisition_events WHERE acquisition_id = %s AND event_type = 'mandate_created'",
             (ids["acq_sanzio"],))[0][0] == 1
    assert q(db, "SELECT count(*) FROM property_price_history WHERE property_id = %s AND new_price = 185000",
             (ids["sanzio"],))[0][0] == 1
    dopo = api("agent_a1").get(f"/api/voice/commands/{vista['command_id']}").json()["steps"][0]
    assert dopo["summary"]["links"]["mandate"] == f"#/incarichi/{ids['sanzio']}"
    # g2: seconda esecuzione e seconda conferma: nulla di nuovo
    api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/execute")
    r = api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/confirm", json={"all": True, "steps": []})
    assert r.status_code in (200, 409, 422)
    # g2: lo stesso comando, di nuovo: l'incarico c'e' gia'
    di_nuovo = detta(api, "agent_a1", "Crea un incarico di vendita per via Sanzio 14, prezzo 185 mila",
                     [_comando("mandate_prepare", "Crea un incarico di vendita per via Sanzio 14, prezzo 185 mila",
                               property={"address": "Via Sanzio", "civic_number": "14"},
                               price_text="185 mila")]).json()["steps"][0]
    assert di_nuovo["decision"] == "blocked" and "mandate_already_exists" in _codici(di_nuovo)
    assert q(db, "SELECT count(*) FROM acquisition_events WHERE acquisition_id = %s AND event_type = 'mandate_created'",
             (ids["acq_sanzio"],))[0][0] == 1


def test_g3_la_conferma_vale_per_quella_situazione(api, db):  # noqa: F811
    ids = db["ids"]
    testo = "Crea un incarico di vendita per via Arno 6 dal primo novembre"
    vista = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo,
                                                    property={"address": "Via Arno", "civic_number": "6"},
                                                    start_text="1 novembre")]).json()
    p = vista["steps"][0]
    assert p["needs"] == "needs_confirmation" and p["summary"]["price_from_acquisition"] is True
    conferma_tutto(db, "agent_a1", vista["command_id"])
    # prima di eseguire, un collega cambia il prezzo dell'acquisizione (quello proposto nella scheda)
    q(db, "UPDATE acquisitions SET asking_price = 170000, version = version + 1 WHERE id = %s", (ids["acq_arno"],))
    try:
        fatto = api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/execute").json()
        assert fatto["steps"][0]["state"] == "pending" and fatto["steps"][0]["needs"] == "needs_confirmation"
        assert q(db, "SELECT acquisition_id FROM properties WHERE id = %s", (ids["arno"],))[0][0] is None
        rivista = api("agent_a1").get(f"/api/voice/commands/{vista['command_id']}").json()["steps"][0]
        assert rivista["summary"]["proposed_price"] == "170000.00" and rivista["needs"] == "needs_confirmation"
    finally:
        q(db, "UPDATE acquisitions SET asking_price = 150000 WHERE id = %s", (ids["acq_arno"],))
        api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/cancel")


def test_g4_tipo_mancante_chiesto_e_prezzo_dall_acquisizione(api, db):  # noqa: F811
    testo = "Prepara l'incarico per l'immobile di Sara Blu da oggi"
    vista = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo,
                                                    owner={"first_name": "Sara", "last_name": "Blu"},
                                                    start_text="oggi")]).json()
    p = vista["steps"][0]
    assert _domande(p) == ["mandate_type_missing"] and p["summary"]["proposed_price"] == "150000.00"
    assert "Tipo di incarico" in p["summary"]["missing"]
    chiamate = len(api.finto.calls)
    vista = _rispondi(api, "agent_a1", vista["command_id"], "vendita in esclusiva")
    assert len(api.finto.calls) == chiamate
    p = vista["steps"][0]
    assert p["summary"]["mandate_type"] == "Vendita in esclusiva" and "mandate_will_generate" in _codici(p)
    api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/cancel")


def test_p1_prima_del_sopralluogo_solo_il_prezzo_dell_acquisizione(api, db):  # noqa: F811
    ids = db["ids"]
    testo = "Prepara l'incarico di via Dante 3, prezzo 160 mila"
    vista = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo,
                                                    property={"address": "Via Dante", "civic_number": "3"},
                                                    price_text="160 mila")]).json()
    p = vista["steps"][0]
    assert "mandate_will_update_acquisition" in _codici(p) and "mandate_will_generate" not in _codici(p)
    assert any("Sopralluogo" in m for m in p["summary"]["missing"])
    fatto = _esegui(api, db, "agent_a1", vista)
    assert fatto["status"] == "completed", fatto
    assert list(q(db, "SELECT status, asking_price FROM acquisitions WHERE id = %s", (ids["acq_dante"],))[0]) == \
        ["appointment_set", Decimal("160000.00")]
    assert q(db, "SELECT acquisition_id FROM properties WHERE id = %s", (ids["dante"],))[0][0] is None
    # senza prezzo: domanda e spiegazione, nessuna operazione
    testo = "Crea l'incarico di via Dante 3"
    p = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo,
                                                property={"address": "Via Dante", "civic_number": "3"})]).json()["steps"][0]
    assert set(_codici(p)) >= {"mandate_acquisition_not_ready"} and p["needs"] == "needs_answer"


def test_a1_senza_acquisizione_percorso_guidato(api, db):  # noqa: F811
    ids = db["ids"]
    testo = "Crea un incarico di vendita per la casa di Anna Bianchi, prezzo 210 mila"
    vista = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo,
                                                    owner={"first_name": "Anna", "last_name": "Bianchi"},
                                                    price_text="210 mila")]).json()
    p = vista["steps"][0]
    assert _domande(p) == ["mandate_no_acquisition"]
    assert "iniziare il percorso" in next(m["message"] for m in p["reasons"] if m["code"] == "mandate_no_acquisition")
    chiamate = len(api.finto.calls)
    vista = _rispondi(api, "agent_a1", vista["command_id"], "domani alle 10")
    assert len(api.finto.calls) == chiamate
    p = vista["steps"][0]
    assert p["intent"] == "acquisition_start" and "mandate_will_start_acquisition" in _codici(p)
    assert "Sopralluogo svolto" in " ".join(p["summary"]["missing"])
    fatto = _esegui(api, db, "agent_a1", vista)
    assert fatto["status"] == "completed", fatto
    acq = q(db, "SELECT a.status, a.asking_price, ap.appointment_type, ap.assigned_user_id, ap.contact_id "
                "FROM acquisitions a JOIN appointments ap ON ap.id = a.appointment_id WHERE a.property_id = %s",
            (ids["verdi"],))
    assert len(acq) == 1 and list(acq[0]) == ["appointment_set", Decimal("210000.00"), "seller_meeting",
                                              ids["agent_a1"], ids["bianchi"]]
    assert q(db, "SELECT acquisition_id FROM properties WHERE id = %s", (ids["verdi"],))[0][0] is None
    # ora l'incarico non e' ancora generabile: manca il sopralluogo
    testo = "Crea l'incarico di Anna Bianchi"
    p = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo,
                                                owner={"first_name": "Anna", "last_name": "Bianchi"})]).json()["steps"][0]
    assert "mandate_will_generate" not in _codici(p) and "mandate_acquisition_not_ready" in _codici(p)


def test_s1_acquisizione_di_un_collega(api, db):  # noqa: F811
    testo = "Crea un incarico di vendita per l'immobile di Paolo Neri da oggi"
    comando = [_comando("mandate_prepare", testo, owner={"first_name": "Paolo", "last_name": "Neri"}, start_text="oggi")]
    p = detta(api, "agent_a1", testo, comando).json()["steps"][0]
    assert p["decision"] == "blocked" and "mandate_acquisition_other_agent" in _codici(p)
    vista = detta(api, "owner_a", testo, comando).json()
    assert "mandate_will_generate" in _codici(vista["steps"][0])               # il titolare vede tutte le acquisizioni
    api("owner_a").post(f"/api/voice/commands/{vista['command_id']}/cancel")


def test_s2_proprietario_non_collegato_all_indirizzo(api, db):  # noqa: F811
    ids = db["ids"]
    prima = q(db, "SELECT count(*) FROM property_contacts")[0][0]
    testo = "Crea un incarico di vendita per Mario Rossi, immobile in via Sanzio 14, prezzo 150 mila"
    p = detta(api, "agent_a1", testo, [_comando("mandate_prepare", testo, owner={"first_name": "Mario", "last_name": "Rossi"},
                                                property={"address": "Via Sanzio", "civic_number": "14"},
                                                price_text="150 mila")]).json()["steps"][0]
    assert p["decision"] == "blocked" and _codici(p) == ["mandate_owner_not_linked"]
    assert q(db, "SELECT count(*) FROM property_contacts")[0][0] == prima
    assert q(db, "SELECT count(*) FROM property_contacts WHERE property_id = %s AND contact_id = %s",
             (ids["sanzio"], ids["mario"]))[0][0] == 0


def test_r1_prezzo_di_un_incarico_esistente(api, db):  # noqa: F811
    ids = db["ids"]
    testo = "Modifica il prezzo richiesto dell'incarico di via Mare 9 a 175 mila euro"
    comando = [_comando("mandate_price", testo, property={"address": "Via Mare", "civic_number": "9"},
                        price_text="175 mila euro")]
    p = detta(api, "agent_a2", testo, comando).json()["steps"][0]
    assert p["decision"] == "blocked" and "mandate_property_not_manageable" in _codici(p)
    vista = detta(api, "agent_a1", testo, comando).json()
    p = vista["steps"][0]
    assert "mandate_will_set_property_price" in _codici(p) and p["summary"]["property_price"] == "200000.00"
    fatto = _esegui(api, db, "agent_a1", vista)
    assert fatto["status"] == "completed", fatto
    assert q(db, "SELECT asking_price FROM properties WHERE id = %s", (ids["mare"],))[0][0] == Decimal("175000.00")
    storia = q(db, "SELECT old_price, new_price, change_reason, changed_by FROM property_price_history "
                   "WHERE property_id = %s ORDER BY id DESC LIMIT 1", (ids["mare"],))[0]
    assert list(storia) == [Decimal("200000.00"), Decimal("175000.00"), "STIMA Voice: prezzo dell'incarico",
                            f"operator:{ids['agent_a1']}"]
    assert fatto["steps"][0]["result"]["previous_price"] == "200000.00"


def test_r2_quale_prezzo_si_chiede(api, db):  # noqa: F811
    ids = db["ids"]
    testo = "Cambia il prezzo di via Fiume 2 a 95 mila"
    vista = detta(api, "agent_a1", testo, [_comando("mandate_price", testo,
                                                    property={"address": "Via Fiume", "civic_number": "2"},
                                                    price_text="95 mila")]).json()
    assert _domande(vista["steps"][0]) == ["mandate_price_kind_unclear"]
    chiamate = len(api.finto.calls)
    vista = _rispondi(api, "agent_a1", vista["command_id"], "quello dell'acquisizione")
    assert len(api.finto.calls) == chiamate and "mandate_will_set_acquisition_price" in _codici(vista["steps"][0])
    fatto = _esegui(api, db, "agent_a1", vista)
    assert fatto["status"] == "completed", fatto
    assert q(db, "SELECT asking_price FROM acquisitions WHERE id = %s", (ids["acq_fiume"],))[0][0] == Decimal("95000.00")
    assert q(db, "SELECT asking_price FROM properties WHERE id = %s", (ids["fiume"],))[0][0] == Decimal("110000.00")


def test_i1_dati_mancanti_e_scheda_sola_lettura(api, db):  # noqa: F811
    ids = db["ids"]
    testo = "Quali dati mancano per completare l'incarico di Giulio Viola?"
    vista = detta(api, "agent_a1", testo, [_comando("mandate_info", testo, owner={"first_name": "Giulio", "last_name": "Viola"},
                                                    purpose="missing")]).json()
    p = vista["steps"][0]
    assert vista["status"] == "completed" and p["state"] == "succeeded"          # nessuna conferma: sola lettura
    assert any("Acquisizione" in m for m in p["summary"]["missing"]) and p["summary"]["mandate_exists"] is False
    testo = "Portami alla scheda dell'incarico di Luca Gialli"
    p = detta(api, "agent_a1", testo, [_comando("mandate_info", testo, owner={"first_name": "Luca", "last_name": "Gialli"},
                                                purpose="open")]).json()["steps"][0]
    assert p["summary"]["purpose"] == "open" and p["summary"]["links"]["mandate"] == f"#/incarichi/{ids['mare']}"


def test_c1_un_incarico_si_conferma_da_solo(api, db):  # noqa: F811
    testo = "Crea l'incarico di via Arno 6 da oggi e aggiungi una nota a Sara Blu"
    vista = detta(api, "agent_a1", testo, [
        _comando("mandate_prepare", testo, property={"address": "Via Arno", "civic_number": "6"}, start_text="oggi",
                 mandate_type="vendita"),
        _comando("add_note", testo, text="chiamare", contact={"first_name": "Sara", "last_name": "Blu"})]).json()
    p = vista["steps"][0]
    assert p["decision"] == "blocked" and "mandate_change_alone" in _codici(p)
