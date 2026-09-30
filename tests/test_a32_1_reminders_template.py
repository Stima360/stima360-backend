"""A32-1 - l'email del promemoria: contenuto (V) ed escape HTML (W)."""
from __future__ import annotations

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from appointment_reminders import template

ROMA = ZoneInfo("Europe/Rome")
INIZIO = datetime(2026, 10, 14, 10, 30, tzinfo=ROMA)
INDIRIZZO = {"address": "Via Roma", "civic_number": "12", "city": "Giulianova"}


def rendi(**kw):
    base = {"appointment_type": "buyer_visit", "start_at": INIZIO,
            "agency_name": "Agenzia Mare", "customer_name": "Mario"}
    base.update(kw)
    return template.render_email(**base)


# ---------------------------------------------------------------------------
# V - oggetto e contenuto
# ---------------------------------------------------------------------------

def test_V_identita_del_template():
    assert (template.TEMPLATE_KEY, template.TEMPLATE_VERSION) == ("appointment_reminder_24h", 1)


def test_V_oggetto():
    oggetto, _ = rendi()
    assert oggetto == "Promemoria: visita all'immobile mercoledì 14 ottobre 2026 alle 10:30"
    assert "\n" not in oggetto and "\r" not in oggetto


@pytest.mark.parametrize("tipo,etichetta", [
    ("buyer_visit", "visita all'immobile"), ("inspection", "sopralluogo"),
    ("seller_meeting", "appuntamento"),
    ("valuation_presentation", "presentazione della valutazione"),
    ("mandate_signing", "firma dell'incarico")])
def test_V_etichetta_cliente_per_ogni_tipo(tipo, etichetta):
    oggetto, corpo = rendi(appointment_type=tipo)
    assert oggetto.startswith(f"Promemoria: {etichetta} ")
    assert etichetta.replace("'", "&#x27;") in corpo


def test_V_corpo_contiene_i_campi_richiesti():
    _, corpo = rendi(property_address=INDIRIZZO)
    for atteso in ("Promemoria appuntamento", "Buongiorno Mario,", "Data",
                   "mercoledì 14 ottobre 2026", "Ora", "10:30", "Appuntamento",
                   "visita all&#x27;immobile", "Agenzia", "Agenzia Mare",
                   "Via Roma 12, Giulianova"):
        assert atteso in corpo, atteso


def test_V_ora_mostrata_in_europe_rome():
    utc = INIZIO.astimezone(ZoneInfo("UTC"))
    oggetto, corpo = rendi(start_at=utc)
    assert "10:30" in oggetto and "10:30" in corpo and "08:30" not in corpo


def test_V_senza_nome_saluto_neutro():
    _, corpo = rendi(customer_name=None)
    assert "Buongiorno," in corpo
    _, corpo = rendi(customer_name="   ")
    assert "Buongiorno," in corpo


def test_V_indirizzo_solo_per_buyer_visit():
    for tipo in ("inspection", "seller_meeting", "valuation_presentation", "mandate_signing"):
        _, corpo = rendi(appointment_type=tipo, property_address=INDIRIZZO)
        assert "Via Roma" not in corpo and "Indirizzo" not in corpo, tipo
    _, corpo = rendi(property_address=None)
    assert "Indirizzo" not in corpo


def test_V_indirizzo_solo_strutturato_mai_testo_libero():
    with pytest.raises(TypeError):
        rendi(property_address="citofono Rossi, chiavi dal vicino")
    with pytest.raises(ValueError):
        rendi(property_address={**INDIRIZZO, "location_text": "chiavi sotto lo zerbino"})


def test_V_tipo_non_ammesso_e_agenzia_obbligatoria():
    with pytest.raises(ValueError):
        rendi(appointment_type="notary")
    with pytest.raises(ValueError):
        rendi(agency_name="  ")


def test_V_la_firma_e_la_lista_chiusa_dei_campi():
    """MAI note, location_text, outcome_note, telefono, lead, fonte, id, token,
    email: non esiste un parametro per farli arrivare nel messaggio."""
    parametri = list(inspect.signature(template.render_email).parameters)
    assert parametri == ["appointment_type", "start_at", "agency_name", "customer_name",
                         "property_address"]


def test_V_nessun_dato_vietato_nel_testo_prodotto():
    oggetto, corpo = rendi(property_address=INDIRIZZO)
    testo = (oggetto + corpo).lower()
    for vietato in ("@", "token", "note", "lead", "source", "crm_manual", "telefono",
                    "http://", "https://"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# W - escape HTML
# ---------------------------------------------------------------------------

def test_W_nome_con_script_esce_escapato():
    _, corpo = rendi(customer_name="<script>alert(1)</script>")
    assert "<script>" not in corpo and "alert(1)</script>" not in corpo
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in corpo


def test_W_indirizzo_e_agenzia_con_html_escono_escapati():
    _, corpo = rendi(agency_name='Agenzia "Mare" <b>Blu</b>',
                     property_address={"address": "Via <img src=x onerror=alert(1)>",
                                       "civic_number": "1'2", "city": "Giuli&nova"})
    for crudo in ("<b>", "<img", 'onerror=alert(1)>', '"Mare"', "1'2", "Giuli&nova"):
        assert crudo not in corpo, crudo
    for sicuro in ("&lt;b&gt;Blu&lt;/b&gt;", "&lt;img src=x onerror=alert(1)&gt;",
                   "&quot;Mare&quot;", "1&#x27;2", "Giuli&amp;nova"):
        assert sicuro in corpo, sicuro


def test_W_l_unico_html_e_quello_del_template():
    _, corpo = rendi(customer_name="<i>x</i>", agency_name="<u>y</u>",
                     property_address={"address": "<s>z</s>", "civic_number": None,
                                       "city": None})
    tag = {t.split()[0].strip("</>") for t in __import__("re").findall(r"</?[a-z0-9]+[^>]*>", corpo)}
    assert tag <= {"div", "h2", "p", "table", "tr", "td", "strong"}, tag


def test_W_a_capo_e_spazi_nei_valori_non_rompono_il_testo():
    _, corpo = rendi(customer_name="Mario\r\nBcc: x@y.it")
    assert "\n" not in corpo and "\r" not in corpo
