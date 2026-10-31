"""BLOCCO-4C - prove offline dei suggerimenti di risposta.

Permessi, agenzie, piu' immobili, riservati e nessun invio sono provati su
PostgreSQL vero in `test_blocco_4c_reply_suggestions_postgres.py`. Qui: le
regole e i testi con dati sintetici, i confini del codice e l'interfaccia.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from reply_suggestions import fields, rules
from reply_suggestions.fields import AVAILABLE_STATUS

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "static" / "os_shell" / "assets"

PROPERTY = {"id": 4, "code": "IMM-4", "title": "Trilocale", "asking_price": 185000, "surface_sqm": 80,
            "commercial_surface_sqm": None, "rooms": 3, "bedrooms": 2, "bathrooms": 1, "floor": "2",
            "total_floors": 4, "elevator": False, "energy_class": "C", "commercial_status": "active"}
ACCESSORIES = [{"kind": "balcone", "quantity": 2}, {"kind": "box", "quantity": None}, {"kind": "altro", "quantity": 1}]


def _decide(text, prop=PROPERTY, accessories=ACCESSORIES, problem=None, kind="text"):
    return rules.decide({"message_type": kind, "text_body": text}, prop, accessories, reference_problem=problem)


# ---------------------------------------------------------------------------
# regole e testi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Quanto costa?", "il prezzo richiesto è € 185.000"),
    ("Quanti metri quadri?", "la superficie è di 80 mq"),
    ("Quanti locali e quanti bagni?", "ha 3 locali; ha 1 bagno"),
    ("A che piano è?", "è al piano 2 su 4"),
    ("C'è l'ascensore?", "non c'è l'ascensore"),                            # un «no» registrato si dice
    ("Classe energetica?", "la classe energetica è C"),
    ("Ha il balcone?", "tra le pertinenze risultano: balcone (2)"),
    ("Che pertinenze ha?", "tra le pertinenze risultano: balcone (2), garage / box"),   # «altro» non si nomina
])
def test_simple_questions_are_answered_from_the_card(text, expected):
    s = _decide(text)
    assert s.outcome == rules.ANSWER and s.reason_code is None
    # BLOCCO-4D: nessun codice interno nel testo per il cliente
    assert s.text == f"Buongiorno, grazie per il messaggio. Per l'immobile di cui ci scrive: {expected}."
    assert s.facts and "IMM-4" not in s.text and "Trilocale" not in s.text


def test_availability_is_never_affirmed_from_the_commercial_status():
    """BLOCCO-4D: `active` e' un'etichetta manuale senza verifica: non prova
    che l'immobile sia disponibile oggi."""
    s = _decide("È ancora disponibile?")
    assert s.outcome == rules.HANDOFF and s.reason_code == "missing_data" and s.facts == []
    assert "disponibile" not in s.text and s.missing == ["availability"]


def test_no_promise_of_a_callback_without_an_assigned_task():
    for text in ("Il prezzo è trattabile?", "Posso vederlo domani?", "Ciao"):
        plain = rules.decide({"message_type": "text", "text_body": text}, PROPERTY, ACCESSORIES, reference_problem=None)
        assert "ricontatt" not in plain.text, text
        assigned = rules.decide({"message_type": "text", "text_body": text}, PROPERTY, ACCESSORIES,
                                reference_problem=None, followup_assigned=True)
        assert "ricontatterà" in assigned.text, text
    s = _decide("Quanto costa e che classe energetica ha?", prop={**PROPERTY, "energy_class": None})
    assert "ricontatt" not in s.text


def test_the_surface_always_carries_its_type():
    s = _decide("Quanti mq?", prop={**PROPERTY, "commercial_surface_sqm": 92.5})
    assert "la superficie commerciale è di 92,5 mq e la superficie è di 80 mq" in s.text
    assert [f.label for f in s.facts] == ["Superficie commerciale", "Superficie"]


@pytest.mark.parametrize("field, question, topic", [
    ("asking_price", "Quanto costa?", "il prezzo"), ("elevator", "C'è l'ascensore?", "l'ascensore"),
    ("floor", "Che piano?", "il piano"), ("energy_class", "Classe energetica?", "la classe energetica"),
])
def test_missing_values_are_never_invented(field, question, topic):
    s = _decide(question, prop={**PROPERTY, field: None})
    assert s.outcome == rules.HANDOFF and s.reason_code == "missing_data" and s.facts == []
    assert s.missing and rules.TOPIC_NAMES[s.missing[0]] == topic


def test_a_partial_answer_says_what_the_agent_will_provide():
    s = _decide("Quanto costa e che classe energetica ha?", prop={**PROPERTY, "energy_class": None})
    assert s.outcome == rules.HANDOFF and s.reason_code == "missing_data"
    assert s.text.endswith("il prezzo richiesto è € 185.000. Al momento non abbiamo un dato da comunicarle su: "
                           "la classe energetica.")


def test_an_accessory_not_registered_is_neither_affirmed_nor_denied():
    s = _decide("Ha la cantina?")
    assert s.outcome == rules.HANDOFF and s.reason_code == "missing_data" and "cantina" not in s.text


@pytest.mark.parametrize("status", ["draft", "evaluation", "mandate", "reserved", "under_offer", "sold",
                                    "withdrawn", "archived"])
def test_only_an_active_property_is_communicated(status):
    assert status != AVAILABLE_STATUS
    s = _decide("Quanto costa? È disponibile?", prop={**PROPERTY, "commercial_status": status})
    assert s.outcome == rules.HANDOFF and s.reason_code == "property_unavailable" and s.facts == []
    assert "185.000" not in s.text and "disponibil" not in s.text


@pytest.mark.parametrize("text, code", [
    ("Posso venire a vederlo sabato alle 10?", "visit"), ("Vorrei visitarlo", "visit"),
    ("Quando posso venire?", "visit"), ("Fissiamo un appuntamento?", "visit"),
    ("Il prezzo è trattabile?", "negotiation"), ("Farei un'offerta", "negotiation"), ("Mi fate uno sconto?", "negotiation"),
    ("È in regola con il catasto?", "technical_legal"), ("Avete la planimetria?", "technical_legal"),
    ("Quanto sono le spese condominiali?", "technical_legal"), ("Serve il notaio per il rogito?", "technical_legal"),
    ("Ciao", "ambiguous"), ("Info", "ambiguous"), ("👍", "ambiguous"),
])
def test_visits_negotiation_technical_and_ambiguous_go_to_the_agent(text, code):
    s = _decide(text)
    assert s.outcome == rules.HANDOFF and s.reason_code == code and s.facts == []
    assert "185.000" not in s.text
    if code == "visit":
        assert s.text == rules.TEXTS["visit"] and "10" not in s.text and "conferm" not in s.text.lower()


def test_a_visit_wins_over_a_data_question_and_is_never_confirmed():
    s = _decide("Quanto costa? Posso vederlo domani alle 18?")
    assert s.reason_code == "visit" and "18" not in s.text and "domani" not in s.text


def test_no_linked_property_asks_for_the_listing_reference():
    for problem in (None, "conflicting_reference", "property_not_found"):
        s = _decide("Quanto costa?", prop=None if problem is None else PROPERTY, problem=problem)
        assert s.outcome == rules.ASK_REFERENCE and s.reason_code == (problem or "no_property")
        assert s.text == rules.TEXTS["ask_reference"] and s.facts == []


def test_stop_and_non_text_messages():
    assert _decide("STOP").outcome == rules.NONE and _decide(" stop. ").text is None
    s = _decide(None, kind="image")
    assert s.outcome == rules.HANDOFF and s.reason_code == "not_text"


def test_cited_references_are_uncertain_when_they_differ_from_the_linked_one():
    assert rules.conflicting_reference("Quanto costa l'IMM-9?", "IMM-4", []) is True        # stesso formato, altro
    assert rules.conflicting_reference("Quanto costa imm 4?", "IMM-4", []) is False         # lo stesso, scritto diverso
    assert rules.conflicting_reference("Al piano 2, 80 mq", "IMM-4", []) is False           # non sono codici
    assert rules.conflicting_reference("Quello in RIF-2?", "IMM-4", ["RIF-2"]) is True      # altro immobile dell'agenzia
    assert rules.cited_codes("imm_4 e Rif 22") == ["IMM-4", "RIF-22"]


def test_formats():
    assert fields.euro(185000) == "€ 185.000" and fields.euro("1250000.40") == "€ 1.250.000"
    assert fields.sqm(80) == "80 mq" and fields.sqm("92.50") == "92,5 mq"


# ---------------------------------------------------------------------------
# confini del codice
# ---------------------------------------------------------------------------

def _code(name):
    """Il codice senza docstring e commenti (che nominano i campi esclusi)."""
    text = (ROOT / "reply_suggestions" / name).read_text(encoding="utf-8")
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    return "\n".join(r.split("#")[0] for r in text.splitlines())


def test_only_communicable_columns_are_read():
    repo = _code("repository.py")
    assert "SELECT *" not in repo and "p.*" not in repo
    for column in ("minimum_price", "internal_notes", "public_notes", "address", "civic_number", "mandate",
                   "metadata", "owner", "valuation", "document"):
        for name in ("repository.py", "service.py"):
            assert column not in _code(name), (name, column)
    # le regole leggono dall'immobile soltanto i campi comunicabili
    rules_code = _code("rules.py")
    read = set(re.findall(r'\bp(?:rop)?\.get\("(\w+)"\)', rules_code)) | set(re.findall(r'\bp(?:rop)?\["(\w+)"\]', rules_code))
    assert read and read <= set(fields.PROPERTY_COLUMNS) | {"id", "code", "title"}, read
    assert set(fields.PROPERTY_COLUMNS).isdisjoint(fields.EXCLUDED_COLUMNS)


def test_the_module_never_sends_writes_or_calls_an_external_service():
    for path in (ROOT / "reply_suggestions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("communication", "message_replies", "transport", "requests", "httpx", "urllib",
                          "graph.facebook", "openai", "anthropic", "INSERT", "UPDATE", "DELETE", "commit=True",
                          "enqueue", "@router.post", "@router.put", "@router.patch", "@router.delete"):
            assert forbidden not in text, (path.name, forbidden)


def test_no_migration_for_suggestions():
    names = [p.name for p in (ROOT / "migrations").glob("*.sql")]
    assert not any("suggest" in n for n in names)
    # SENTINELLA AGGIORNATA DA BLOCCO-4D: la 119 e' del motore 4D, non dei suggerimenti
    assert max(n for n in names if re.match(r"\d{3}_", n)).startswith("119_whatsapp_auto_reply")


def test_route_is_mounted_behind_the_operator_session():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "reply_suggestions_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line


# ---------------------------------------------------------------------------
# interfaccia
# ---------------------------------------------------------------------------

def test_ui_prepares_shows_sources_and_hands_over_to_the_manual_composer():
    js = (APP / "components" / "suggested-reply.js").read_text(encoding="utf-8")
    view = (APP / "views" / "messaggi.js").read_text(encoding="utf-8")
    reply = (APP / "components" / "whatsapp-reply.js").read_text(encoding="utf-8")
    for text in (js, view):
        assert "innerHTML" not in text
    assert "apiPost" not in js                                              # il suggerimento non scrive
    assert "Prepara risposta" in js and "Usa nel compositore" in js and "Dati del CRM usati" in js
    assert "s.handoff.reason" in js and "/api/reply-suggestions/messages" in js
    # prima del trasferimento si ricalcola e si confronta l'impronta
    assert "fresh.fingerprint !== s.fingerprint" in js and "I dati sono cambiati" in js
    assert "composer.setDraft(text.value)" in js and "Non è partito nulla" in js
    # il compositore riceve il testo e NON invia: restano Prepara/Conferma
    set_draft = reply[reply.index("setDraft(value)"):]
    assert "apiPost" not in set_draft.split("},\n  };")[0] and "requestId = null" in set_draft
    assert "mountReplySuggestion(card, { messageId: m.id" in view and "composer = await mountWhatsappReply" in view
