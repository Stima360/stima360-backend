"""STIMA Voice V8-1 - riconoscimento intelligente: ruoli, sinonimi e motivo.

  01  le frasi obbligatorie dell'autorizzazione V8, una per una
  02  il motivo detto prevale sul ruolo
  03  acquirente senza motivo = domanda (dati, non confermabile), mai una visita
  04  il modello non basta: `buyer_visit` e `seller_meeting` li dicono le parole
  05  il ruolo detto finisce nel riferimento al contatto (per il risolutore)
  06  il contratto: `contact.role` e' chiuso sui ruoli del CRM
  07  parole chiave e ruoli sono fissati (sentinella)
  08  il messaggio della domanda e' nel catalogo del service
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from core.enums import CONTACT_ROLES
from voice import planner, policy, service
from voice.dates import ROMA
from voice.planner import build_plan
from voice.policy import Facts, RefFacts, Settings, decide
from voice.schemas import parse_plan_output

NOW = datetime(2026, 10, 10, 16, 0, tzinfo=ROMA)   # sabato


def _plan(transcript, comando):
    return build_plan(parse_plan_output({"commands": [comando]}), transcript, NOW)


def _appt(quote, *, when=("domani", "alle 10"), contact=None, tipo=None, prop=None):
    c = {"intent": "create_appointment", "quote": quote, "when": {"date_text": when[0], "time_text": when[1]}}
    if contact:
        c["contact"] = contact
    if tipo:
        c["appointment_type"] = tipo
    if prop:
        c["property"] = prop
    return c


OBBLIGATORIE = [
    ("Appuntamento con Francesco Rossi venditore domani alle 10",
     {"first_name": "Francesco", "last_name": "Rossi", "role": "seller"}, ("domani", "alle 10"), "seller_meeting"),
    ("Devo fare un sopralluogo con Francesco venerdì alle 11",
     {"first_name": "Francesco"}, ("venerdì", "alle 11"), "inspection"),
    ("Mario Rossi vuole vedere casa in Via Roma 15 giovedì alle 16",
     {"first_name": "Mario", "last_name": "Rossi"}, ("giovedì", "alle 16"), "buyer_visit"),
    ("Ho il compromesso con Mario lunedì alle 15",
     {"first_name": "Mario"}, ("lunedì", "alle 15"), "preliminary_contract"),
    ("Rogito dal notaio venerdì alle 12", None, ("venerdì", "alle 12"), "notary"),
]


@pytest.mark.parametrize("testo, contatto, quando, atteso", OBBLIGATORIE)
def test_01_le_frasi_obbligatorie(testo, contatto, quando, atteso):
    piano = _plan(testo, _appt(testo, when=quando, contact=contatto))
    passo = piano.step(1)
    assert passo.payload["appointment_type"] == atteso
    assert not passo.has(planner.APPOINTMENT_PURPOSE_MISSING)
    assert passo.start_at is not None and passo.start_at.tzinfo is not None


def test_01b_acquirente_senza_motivo_chiede():
    testo = "Appuntamento con Mario Rossi acquirente"
    piano = _plan(testo, _appt(testo, contact={"first_name": "Mario", "last_name": "Rossi", "role": "buyer"}))
    passo = piano.step(1)
    assert passo.payload["appointment_type"] == planner.DEFAULT_APPOINTMENT_TYPE
    assert passo.has(planner.APPOINTMENT_PURPOSE_MISSING)
    esito = decide(piano, Facts(refs={(1, "contact"): RefFacts(1, True)}), Settings(mode="auto"))
    assert esito.of(1).decision == "ask" and planner.APPOINTMENT_PURPOSE_MISSING in esito.of(1).reasons
    # domanda di DATI: non si supera con una conferma
    from voice.executor import CONFIRMABLE
    assert planner.APPOINTMENT_PURPOSE_MISSING not in CONFIRMABLE


@pytest.mark.parametrize("testo, atteso", [
    ("Firma dell'incarico con Mario Rossi venditore domani alle 10", "mandate_signing"),
    ("Telefonata con Luca Bianchi acquirente domani alle 10", "call"),
    ("Proposta d'acquisto con Luca Bianchi acquirente domani alle 10", "proposal"),
    ("Domani alle 10 mostro l'appartamento di via Roma a Luca Bianchi acquirente", "buyer_visit"),
    ("Presentazione della stima a Francesco Rossi proprietario domani alle 10", "valuation_presentation"),
    ("Atto notarile con Mario Rossi venditore domani alle 10", "notary"),
])
def test_02_il_motivo_detto_prevale_sul_ruolo(testo, atteso):
    piano = _plan(testo, _appt(testo, contact={"first_name": "x"}))
    assert piano.step(1).payload["appointment_type"] == atteso
    assert not piano.step(1).has(planner.APPOINTMENT_PURPOSE_MISSING)


def test_03_il_modello_non_inventa_una_visita_ne_un_acquisizione():
    # il modello dice buyer_visit, l'agente no: resta il motivo da chiedere
    testo = "Appuntamento con Mario Rossi acquirente domani alle 10"
    piano = _plan(testo, _appt(testo, contact={"first_name": "Mario", "last_name": "Rossi"}, tipo="buyer_visit"))
    assert piano.step(1).payload["appointment_type"] == "other" and piano.step(1).has(planner.APPOINTMENT_PURPOSE_MISSING)
    # il modello dice seller_meeting senza che l'agente parli di venditore/proprietario: generico
    testo = "Appuntamento con Mario Rossi domani alle 10"
    piano = _plan(testo, _appt(testo, contact={"first_name": "Mario", "last_name": "Rossi"}, tipo="seller_meeting"))
    assert piano.step(1).payload["appointment_type"] == "other" and not piano.step(1).has(planner.APPOINTMENT_PURPOSE_MISSING)
    # il ruolo proposto dal modello, non detto: non fissa un'acquisizione
    piano = _plan(testo, _appt(testo, contact={"first_name": "Mario", "last_name": "Rossi", "role": "seller"}))
    assert piano.step(1).payload["appointment_type"] == "other"
    # ma con un acquirente proposto dal modello la domanda si fa comunque
    piano = _plan(testo, _appt(testo, contact={"first_name": "Mario", "last_name": "Rossi", "role": "buyer"}))
    assert piano.step(1).has(planner.APPOINTMENT_PURPOSE_MISSING)
    # il tipo esplicito del modello per gli altri tipi vale ancora (Fase 1)
    piano = _plan(testo, _appt(testo, contact={"first_name": "Mario"}, tipo="call"))
    assert piano.step(1).payload["appointment_type"] == "call"


def test_04_senza_contatto_e_senza_ruolo_resta_generico_senza_domanda():
    testo = "Appuntamento domani alle 10"
    piano = _plan(testo, _appt(testo))
    assert piano.step(1).payload["appointment_type"] == "other" and not piano.step(1).has(planner.APPOINTMENT_PURPOSE_MISSING)


def test_05_il_ruolo_detto_arriva_al_riferimento():
    testo = "Appuntamento con Francesco Rossi venditore domani alle 10"
    piano = _plan(testo, _appt(testo, contact={"first_name": "Francesco", "last_name": "Rossi"}))
    assert piano.step(1).refs["contact"].description["role"] == "seller"
    testo = "Sopralluogo con Francesco Rossi domani alle 10"
    piano = _plan(testo, _appt(testo, contact={"first_name": "Francesco", "last_name": "Rossi"}))
    assert "role" not in piano.step(1).refs["contact"].description
    # il ruolo non cambia l'impronta: e' una descrizione, non un dato scritto
    assert piano.fingerprint


def test_06_il_contratto_role_e_chiuso():
    parse_plan_output({"commands": [_appt("x", contact={"first_name": "A", "role": "buyer"})]})
    with pytest.raises(ValidationError):
        parse_plan_output({"commands": [_appt("x", contact={"first_name": "A", "role": "inquilino"})]})
    with pytest.raises(ValidationError):   # il ruolo da solo non e' un riferimento
        parse_plan_output({"commands": [_appt("x", contact={"role": "buyer"})]})
    from voice.schemas import plan_json_schema
    enum = plan_json_schema()["$defs"]["ContactRef"]["properties"]["role"]
    valori = set(enum.get("enum") or [e for a in enum.get("anyOf", []) for e in a.get("enum", [])])
    assert valori == set(CONTACT_ROLES)


def test_07_parole_chiave_e_ruoli_fissati():
    assert planner.ROLE_KEYWORDS == (
        ("venditor", "seller"), ("venditric", "seller"), ("proprietari", "owner"), ("padron", "owner"),
        ("acquirent", "buyer"), ("comprator", "buyer"), ("compratric", "buyer"),
        # SENTINELLA AGGIORNATA DAL COLLAUDO REALE DEL 21/10: «Giorgio Censori compra ...»
        (" compra ", "buyer"), (" acquista ", "buyer"),
        ("cerca casa", "buyer"), ("cercano casa", "buyer"))
    assert planner.SELLER_ROLES == ("owner", "seller") and planner.BUYER_ROLES == ("buyer",)
    # SENTINELLA AGGIORNATA DAL COLLAUDO REALE (21/10): «appuntamento di vendita» e' una visita.
    # SENTINELLA AGGIORNATA DAL COLLAUDO IPHONE (21/10): «di vendita» trascritto «rivendita».
    assert planner.BUYER_VISIT_EVIDENCE == ("visit", "veder", "mostr", "visionar", "appuntamento di vendita",
                                            "appuntamento rivendita", "appuntamento vendita")
    assert planner.SELLER_MEETING_EVIDENCE == ("acquisizion", "proprietari", "venditor", "venditric", "padron")
    assert dict(planner.APPOINTMENT_KEYWORDS)["compromesso"] == "preliminary_contract"
    assert dict(planner.APPOINTMENT_KEYWORDS)["atto notarile"] == "notary"
    assert dict(planner.APPOINTMENT_KEYWORDS)["stima"] == "valuation_presentation"
    assert set(dict(planner.APPOINTMENT_KEYWORDS).values()) <= set(__import__("appointments.enums", fromlist=["x"]).APPOINTMENT_TYPES)
    assert planner.role_from("francesco rossi venditore") == "seller"
    assert planner.role_from("mario che cerca casa") == "buyer"
    assert planner.role_from("telefonata con mario") is None
    assert planner.APPOINTMENT_PURPOSE_MISSING in policy.ASKING_ISSUES


def test_08_il_messaggio_e_nel_catalogo():
    assert "acquirente" in service.MESSAGES[planner.APPOINTMENT_PURPOSE_MISSING]
