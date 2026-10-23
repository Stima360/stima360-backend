"""STIMA VOICE V9 - incarichi a voce: regole pure (nessun database).

  m1  i prezzi detti: «185.000 euro», «185 mila», «1,2 milioni»; sotto i mille o
      non chiari -> None (si chiede)
  m2  le regole sulla situazione reale: incarico gia' esistente (nessun doppione),
      censimento, venduto, acquisizione assente (percorso guidato), di un collega,
      prima del sopralluogo (solo il prezzo dell'acquisizione), pronta (genera),
      dati obbligatori mancanti; mai una firma dedotta
  m3  il prezzo: dell'immobile con incarico, dell'acquisizione, valutazione; se non e'
      chiaro quale, si chiede; uguale = nessuna modifica; chi non gestisce l'immobile no
  m4  l'acquisizione: proprietario mancante o ambiguo, gia' aperta, nessun sopralluogo finto
  m5  il planner: tipo e prezzo dalle parole, date e durata, una scrittura sull'incarico
      si conferma da sola; quale prezzo dalle parole
  m6  le risposte semplici senza modello: prezzo, inizio, tipo, quale prezzo, giorno e
      ora per iniziare l'acquisizione; il resto al modello
  m7  la politica: scrittura sempre con conferma e con l'operazione fra i motivi;
      domande non confermabili; sola lettura senza conferma; piu' immobili = domanda
  u1  la scheda nell'interfaccia: righe, prezzi distinti, collegamenti, «Portami»
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from tests.test_voice_6_ui import ASSETS, _node, node
from voice import mandate_rules as R
from voice import planner, policy
from voice.dates import ROMA
from voice.planner import build_plan
from voice.schemas import parse_plan_output

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=ROMA)
MODEL = (ASSETS / "voice" / "voice-model.js").as_posix()


@pytest.mark.parametrize("testo, atteso", [
    ("185.000 euro", "185000.00"), ("185 mila", "185000.00"), ("185mila €", "185000.00"),
    ("1,2 milioni", "1200000.00"), ("175000", "175000.00"), ("€ 150.000,00", "150000.00"),
    ("a 175 mila euro", "175000.00"), ("185.5 mila", "185500.00"), ("150,000", "150000.00"),
    ("185", None), ("999", None), ("centomila", None), ("", None), (None, None), ("abc", None),
])
def test_m1_prezzi_detti(testo, atteso):
    valore = R.price_from_text(testo)
    assert (str(valore) if valore is not None else None) == atteso


def _sit(**kw):
    prop = {"id": 7, "record_kind": "crm", "commercial_status": "draft", "archived_at": None, "asking_price": None,
            "acquisition_id": None, "mandate_type": None, "mandate_start": None, "mandate_end": None,
            "signed_mandate": None, **kw.pop("prop", {})}
    acq = kw.pop("acq", "assente")
    if acq == "assente":
        acq = None
    elif acq is not None:
        acq = {"id": 3, "status": "inspection_done", "version": 1, "asking_price": None, "valuation_price": None,
               "visible": True, "owner_contact_id": 11, **acq}
    return {"property": prop, "acquisition": acq, "owners": [{"contact_id": 11, "name": "Francesco Ruvolo"}],
            "owner_choice": 11, "manageable": True, **kw}


PRONTO = {"mandate_type": "Vendita", "mandate_start": "2026-10-23", "agreed_price": "185000.00"}


def test_m2_le_regole_sulla_situazione_reale():
    v = R.evaluate("mandate_prepare", PRONTO, _sit(acq={}))
    assert v.mode == R.WILL_GENERATE and not v.blocks and not v.asks and v.values["agreed_price"] == Decimal("185000.00")
    assert v.missing == []
    # gia' un incarico (anche storico o firmato nel ponte): nessun doppione
    for prop in ({"acquisition_id": 3}, {"mandate_type": "Vendita", "mandate_start": date(2026, 1, 1)},
                 {"signed_mandate": {"id": 1}}):
        assert R.evaluate("mandate_prepare", PRONTO, _sit(prop=prop, acq={})).blocks == [R.ALREADY_EXISTS]
    assert R.evaluate("mandate_prepare", PRONTO, _sit(prop={"record_kind": "census"}, acq={})).blocks == [R.PROPERTY_CENSUS]
    assert R.evaluate("mandate_prepare", PRONTO, _sit(prop={"commercial_status": "sold"}, acq={})).blocks == [R.PROPERTY_CLOSED]
    assert R.evaluate("mandate_prepare", PRONTO, _sit()).asks == [R.NO_ACQUISITION]
    assert R.evaluate("mandate_prepare", PRONTO, _sit(acq={"visible": False})).blocks == [R.ACQUISITION_OTHER_AGENT]
    # prima del sopralluogo: solo il prezzo sull'acquisizione, mai l'incarico
    v = R.evaluate("mandate_prepare", PRONTO, _sit(acq={"status": "appointment_set"}))
    assert v.mode == R.WILL_UPDATE_ACQUISITION and v.values["asking_price"] == Decimal("185000.00")
    v = R.evaluate("mandate_prepare", {}, _sit(acq={"status": "appointment_set"}))
    assert v.mode is None and v.asks == [R.PRICE_MISSING, R.ACQUISITION_NOT_READY]
    assert R.evaluate("mandate_prepare", PRONTO, _sit(acq={"status": "appointment_set", "asking_price": 185000})).blocks \
        == [R.ACQUISITION_NOT_READY]
    # dati obbligatori mancanti: si chiedono, nessuna operazione
    v = R.evaluate("mandate_prepare", {}, _sit(acq={}))
    assert v.mode is None and v.asks == [R.TYPE_MISSING, R.START_MISSING, R.PRICE_MISSING]
    assert set(v.missing) == {"Tipo di incarico", "Data di inizio", "Prezzo"}
    # il prezzo dell'acquisizione si propone come prezzo concordato, e lo si dice
    v = R.evaluate("mandate_prepare", {**PRONTO, "agreed_price": None}, _sit(acq={"asking_price": 150000}))
    assert v.mode == R.WILL_GENERATE and v.values == {"agreed_price": Decimal("150000.00"), "price_from_acquisition": True}
    # gli stati da cui si genera sono quelli del CRM, non altri
    for stato in ("inspection_done", "valuation_presented", "mandate_negotiation"):
        assert R.evaluate("mandate_prepare", PRONTO, _sit(acq={"status": stato})).mode == R.WILL_GENERATE


def test_m3_il_prezzo():
    incarico = {"acquisition_id": 3, "mandate_type": "Vendita", "mandate_start": date(2026, 1, 1), "asking_price": 200000}
    v = R.evaluate("mandate_price", {"price": "175000.00"}, _sit(prop=incarico))
    assert v.mode == R.WILL_SET_PROPERTY_PRICE and v.values["previous"] == Decimal("200000.00")
    assert R.evaluate("mandate_price", {"price": "200000.00"}, _sit(prop=incarico)).blocks == [R.PRICE_UNCHANGED]
    assert R.evaluate("mandate_price", {"price": "175000.00"}, _sit(prop=incarico, manageable=False)).blocks \
        == [R.NOT_MANAGEABLE]
    aperta = _sit(acq={"status": "appointment_set", "asking_price": 100000})
    assert R.evaluate("mandate_price", {"price": "95000.00"}, aperta).asks == [R.PRICE_KIND_UNCLEAR]
    v = R.evaluate("mandate_price", {"price": "95000.00", "price_kind": "acquisition"}, aperta)
    assert v.mode == R.WILL_SET_ACQUISITION_PRICE and v.values["column"] == "asking_price"
    assert R.evaluate("mandate_price", {"price": "95000.00", "price_kind": "valuation"}, aperta).mode == R.WILL_SET_VALUATION
    assert R.evaluate("mandate_price", {"price": "95000.00"}, _sit()).blocks == [R.NOTHING_TO_PRICE]
    assert R.evaluate("mandate_price", {"price": "1.00", "price_kind": "acquisition"}, _sit()).blocks \
        == [R.NO_OPEN_ACQUISITION]


def test_m4_l_acquisizione():
    v = R.evaluate("acquisition_start", {"asking_price": "210000.00"}, _sit())
    assert v.mode == R.WILL_START_ACQUISITION and v.values["owner_contact_id"] == 11
    assert "Sopralluogo svolto" in " ".join(v.missing)                          # nessun sopralluogo finto
    assert R.evaluate("acquisition_start", {}, _sit(acq={})).blocks == [R.ACQUISITION_ALREADY_OPEN]
    assert R.evaluate("acquisition_start", {}, _sit(owners=[])).blocks == [R.ACQUISITION_OWNER_MISSING]
    assert R.evaluate("acquisition_start", {}, _sit(owner_choice=None)).blocks == [R.ACQUISITION_OWNER_AMBIGUOUS]
    assert R.evaluate("acquisition_start", {}, _sit(prop={"acquisition_id": 3})).blocks == [R.ALREADY_EXISTS]


def _passo(comando, testo=None):
    return build_plan(parse_plan_output({"commands": [comando]}), testo or comando["quote"], NOW).step(1)


def test_m5_il_planner():
    frase = "Crea un incarico di vendita per l'appartamento di Francesco Ruvolo, prezzo 185.000 euro"
    s = _passo({"intent": "mandate_prepare", "quote": frase, "owner": {"first_name": "Francesco", "last_name": "Ruvolo"},
                "price_text": "185.000 euro"})
    assert s.payload == {"mandate_type": "Vendita", "agreed_price": "185000.00", "mandate_start": None,
                         "mandate_end": None, "owner_said": {"first_name": "Francesco", "last_name": "Ruvolo"}}
    assert s.refs["property"].description == {"mandate": "mandate_prepare",
                                              "owner": {"first_name": "Francesco", "last_name": "Ruvolo"}}
    s = _passo({"intent": "mandate_prepare", "quote": "incarico in esclusiva", "property": {"address": "Via Roma"},
                "start_text": "oggi", "end_text": "6 mesi", "price_text": "duecento"})
    assert (s.payload["mandate_start"], s.payload["mandate_end"]) == ("2026-10-23", "2027-04-23")
    assert s.payload["mandate_type"] == "In esclusiva" and s.has(planner.MANDATE_PRICE_UNCLEAR)
    s = _passo({"intent": "mandate_prepare", "quote": "q", "property": {"address": "Via Roma"},
                "start_text": "1 dicembre", "end_text": "1 novembre"})
    assert s.has(planner.MANDATE_DATES_INVALID)
    s = _passo({"intent": "mandate_price", "quote": "cambia la valutazione di via Roma a 150 mila",
                "property": {"address": "Via Roma"}, "price_text": "150 mila"})
    assert s.payload["price_kind"] == "valuation"
    s = _passo({"intent": "mandate_price", "quote": "cambia il prezzo di via Roma a 150 mila",
                "property": {"address": "Via Roma"}, "price_text": "150 mila"})
    assert s.payload["price_kind"] is None                                    # non e' detto: lo si chiede
    piano = build_plan(parse_plan_output({"commands": [
        {"intent": "mandate_prepare", "quote": "q", "property": {"address": "Via Roma"}},
        {"intent": "add_note", "quote": "q", "text": "x", "contact": {"last_name": "Rossi"}}]}), "q", NOW)
    assert piano.step(1).has(planner.MANDATE_CHANGE_ALONE) and not piano.step(2).has(planner.MANDATE_CHANGE_ALONE)


def _uscita(**kw):
    return parse_plan_output({"commands": [{"intent": "mandate_prepare", "quote": "q",
                                            "owner": {"first_name": "Francesco", "last_name": "Ruvolo"}, **kw}]})


def test_m6_risposte_semplici_senza_modello():
    out = _uscita()
    assert planner.apply_local_mandate_reply(out, {1: {R.PRICE_MISSING}}, "185 mila", NOW).commands[0].price_text == "185 mila"
    assert planner.apply_local_mandate_reply(out, {1: {R.START_MISSING}}, "da oggi", NOW).commands[0].start_text == "oggi"
    assert planner.apply_local_mandate_reply(out, {1: {R.TYPE_MISSING}}, "vendita in esclusiva", NOW).commands[0] \
        .mandate_type == "vendita in esclusiva"
    nuovo = planner.apply_local_mandate_reply(out, {1: {R.NO_ACQUISITION}}, "domani alle 10", NOW).commands[0]
    assert nuovo.intent == "acquisition_start" and nuovo.when.date_text == "domani" and nuovo.owner == out.commands[0].owner
    assert planner.apply_local_mandate_reply(out, {1: {R.NO_ACQUISITION}}, "sì", NOW) is None
    prezzo = parse_plan_output({"commands": [{"intent": "mandate_price", "quote": "q", "property": {"address": "Via Roma"},
                                              "price_text": "95 mila"}]})
    assert planner.apply_local_mandate_reply(prezzo, {1: {R.PRICE_KIND_UNCLEAR}}, "quello dell'acquisizione", NOW) \
        .commands[0].price_kind == "acquisition"
    for risposta, chiesto in (("Mario Rossi", {R.PRICE_MISSING}), ("il cliente e' un altro", {R.START_MISSING}),
                              ("185 mila", {R.TYPE_MISSING}), ("boh", {R.PRICE_KIND_UNCLEAR})):
        assert planner.apply_local_mandate_reply(out if chiesto != {R.PRICE_KIND_UNCLEAR} else prezzo,
                                                 {1: chiesto}, risposta, NOW) is None, risposta


def _fatti(n=1, **kw):
    return policy.Facts(refs={(n, "property"): policy.RefFacts(1, exact=False)}, **kw)


def test_m7_la_politica():
    s = _passo({"intent": "mandate_prepare", "quote": "q", "property": {"address": "Via Roma"}})
    d = policy.decide_step(s, _fatti(mandate_mode={1: R.WILL_GENERATE}), policy.Settings(mode="auto"))
    assert d.decision == "ask" and d.reasons == (policy.REF_FUZZY, R.WILL_GENERATE)      # sempre con conferma
    d = policy.decide_step(s, _fatti(mandate_asks={1: (R.START_MISSING,)}), policy.Settings(mode="auto"))
    assert d.decision == "ask" and R.START_MISSING in d.reasons and R.WILL_GENERATE not in d.reasons
    d = policy.decide_step(s, _fatti(mandate_blocks={1: (R.ALREADY_EXISTS,)}), policy.Settings())
    assert d.decision == "blocked" and d.reasons == (R.ALREADY_EXISTS,)
    d = policy.decide_step(s, policy.Facts(refs={(1, "property"): policy.RefFacts(2)},
                                           mandate_asks={1: (R.PROPERTY_CHOICE,)}), policy.Settings())
    assert d.decision == "ask" and policy.REF_AMBIGUOUS not in d.reasons and R.PROPERTY_CHOICE in d.reasons
    d = policy.decide_step(s, _fatti(), policy.Settings())                   # difensivo: mai senza operazione
    assert R.PROPERTY_CHOICE in d.reasons
    lettura = _passo({"intent": "mandate_info", "quote": "quali dati mancano", "owner": {"last_name": "Ruvolo"}})
    assert policy.decide_step(lettura, _fatti(), policy.Settings(mode="review")).decision == "auto"
    from voice.executor import CONFIRMABLE
    assert set(R.MODES) <= CONFIRMABLE and not (set(R.ASKS) | set(R.BLOCKS)) & CONFIRMABLE


@node
def test_u1_la_scheda_nell_interfaccia():
    out = _node(f"""
      const m = await import('{MODEL}');
      const passo = {{ intent: 'mandate_prepare', state: 'pending', summary: {{ kind: 'mandate', property: 'Via Sanzio 14, Tortoreto',
        owner: 'Francesco Ruvolo', proposed_price: '185000.00', proposed_price_label: 'Prezzo concordato (incarico)',
        mandate_type: 'Vendita', mandate_start: '2026-10-23', state: 'Acquisizione: Sopralluogo effettuato (incarico non ancora generato)',
        missing: [], mode: 'mandate_will_generate', links: {{ acquisition: '#/acquisizioni/3', property: '#/immobili/7' }} }} }};
      const fatto = {{ ...passo, state: 'succeeded', result: {{ mode: 'mandate_will_generate' }},
        summary: {{ ...passo.summary, links: {{ mandate: '#/incarichi/7', property: '#/immobili/7' }} }} }};
      const apri = {{ steps: [{{ intent: 'mandate_info', state: 'succeeded', summary: {{ kind: 'mandate', purpose: 'open',
        links: {{ mandate: '#/incarichi/9', property: '#/immobili/9' }} }} }}] }};
      console.log(JSON.stringify({{ righe: m.mandateSummary(passo), link: m.mandateLinks(fatto), dove: m.mandateDestination(apri),
        nessuno: m.mandateDestination({{ steps: [{{ ...apri.steps[0], summary: {{ ...apri.steps[0].summary, purpose: 'missing' }} }}] }}),
        appuntamento: m.appointmentSummary(passo), risultato: m.resultText(fatto), euro: m.formatEuro('185000.00'),
        data: m.formatDateIt('2026-10-23'), etichetta: m.intentLabel('acquisition_start') }}));
    """)
    righe = dict(out["righe"])
    assert righe["Proprietario"] == "Francesco Ruvolo" and righe["Prezzo concordato (incarico)"] == "185.000 €"
    assert righe["Tipologia"] == "Vendita" and righe["Decorrenza"] == "23/10/2026" and righe["Dati mancanti"] == "nessuno"
    assert "Stato effettivo" in righe and "firm" not in " ".join(righe.values()).lower()
    assert [l["label"] for l in out["link"]] == ["Apri incarico", "Apri immobile"]
    assert out["dove"] == "#/incarichi/9" and out["nessuno"] == "" and out["appuntamento"] == []
    assert out["risultato"] == "Incarico generato." and out["euro"] == "185.000 €" and out["data"] == "23/10/2026"
    assert out["etichetta"] == "Nuova acquisizione"
