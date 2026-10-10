"""Il corpus di STIMA Voice: dettati sintetici con l'output che un modello
corretto deve produrre e l'esito atteso dal pianificatore e dalla politica.

Ogni caso e' indipendente dal codice sotto prova: le attese sono scritte
dai template, non calcolate eseguendo il pianificatore. Il generatore e'
deterministico (seme fisso): lo stesso corpus a ogni esecuzione.

Gruppi:
  * `clear`     - un intento, dati completi: tutto `auto`;
  * `multi`     - piu' operazioni in un vocale: tutto `auto`, nell'ordine;
  * `ambiguous` - manca qualcosa o c'e' un dubbio: il passo giusto e' `ask`;
  * `blocked`   - fuori elenco, inventato, non permesso: `blocked`;
  * `mistake`   - il modello sbaglia e il pianificatore deve accorgersene.

I trascritti reali degli agenti (D7) si aggiungeranno in `real.py` nel
collaudo, con lo stesso formato.
"""
from __future__ import annotations

import random
from typing import Any

NOW = "2026-10-10T16:00:00+02:00"   # sabato 10 ottobre 2026, ore 16 a Roma

FIRST = ["Fabio", "Marco", "Giulia", "Luca", "Sara", "Paolo", "Elena", "Andrea", "Chiara", "Davide",
         "Francesca", "Matteo", "Laura", "Simone", "Valentina"]
LAST = ["Rossi", "Bianchi", "Verdi", "Neri", "Galli", "Conti", "Ricci", "Marino", "Greco", "Bruno",
        "Romano", "Gallo", "Costa", "Fontana", "Caruso"]
STREETS = ["via Duca d'Aosta", "via Roma", "viale Mazzini", "via Nazionale", "corso Garibaldi",
           "via Trieste", "lungomare Marconi", "via Dante", "via Cavour", "via Verdi"]
CITIES = ["Alba Adriatica", "Giulianova", "Tortoreto", "Martinsicuro", "Pineto", "Roseto"]
DAYS = [("domani", "2026-10-11"), ("dopodomani", "2026-10-12"), ("lunedì", "2026-10-12"),
        ("giovedì", "2026-10-15"), ("venerdì prossimo", "2026-10-16"), ("il 20", "2026-10-20"),
        ("il 3 novembre", "2026-11-03"), ("fra tre giorni", "2026-10-13")]
TIMES = [("alle 15", "15:00"), ("alle 10 e mezza", "10:30"), ("alle 9", "09:00"), ("alle 17:30", "17:30"),
         ("alle 4 del pomeriggio", "16:00"), ("a mezzogiorno", "12:00"), ("alle 11 e un quarto", "11:15")]
ROOMS = [("bilocale", 2), ("trilocale", 3), ("quadrilocale", 4)]


def _when(day: tuple[str, str], at: tuple[str, str] | None) -> tuple[dict[str, Any], str]:
    """L'atteso in ora di Roma con l'offset VERO di quel giorno (dal 25
    ottobre vale +01:00): lo calcola la libreria standard, non il codice
    sotto prova."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ref = {"date_text": day[0]}
    if at:
        ref["time_text"] = at[0]
    istante = datetime.fromisoformat(f"{day[1]}T{at[1] if at else '09:00'}:00").replace(tzinfo=ZoneInfo("Europe/Rome"))
    return ref, istante.isoformat()


def _phone(r: random.Random) -> str:
    return f"3{r.randint(20, 99)} {r.randint(1000000, 9999999)}"


def _case(id_, group, transcript, commands, expect, *, facts=None, settings=None, clarifications=()):
    return {"id": id_, "group": group, "transcript": transcript, "now": NOW,
            "output": {"commands": commands, "clarifications": list(clarifications)},
            "facts": facts or {}, "settings": settings or {"mode": "auto"}, "expect": expect}


def _end(start: str, minutes: int = 60) -> str:
    from datetime import datetime, timedelta
    d = datetime.fromisoformat(start) + timedelta(minutes=minutes)
    return d.isoformat()


# ---------------------------------------------------------------------------
# Generatori per gruppo
# ---------------------------------------------------------------------------

def _clear(r: random.Random) -> list[dict]:
    out = []
    for i in range(20):  # contatto completo
        f, l, ph = r.choice(FIRST), r.choice(LAST), _phone(r)
        t = f"Nuovo contatto {f} {l}, telefono {ph}"
        out.append(_case(f"clear-contact-{i:02d}", "clear", t,
                         [{"intent": "create_contact", "quote": t, "first_name": f, "last_name": l, "phone": ph}],
                         {"decisions": ["auto"], "fields": {1: {"first_name": f, "last_name": l}}}))
    for i in range(20):  # vendita: unita' crm + proprietario + Vende, contatto esistente
        f, l, s, c = r.choice(FIRST), r.choice(LAST), r.choice(STREETS), r.choice(CITIES)
        nome, stanze = r.choice(ROOMS)
        t = f"{f} {l} vuole vendere un {nome} in {s} a {c}"
        out.append(_case(f"clear-sell-{i:02d}", "clear", t, [
            {"intent": "create_unit", "quote": f"{nome} in {s} a {c}", "record_kind": "crm", "rooms": stanze,
             "address": s, "city": c, "owner": {"first_name": f, "last_name": l}},
            {"intent": "link_owner", "quote": t, "contact": {"first_name": f, "last_name": l}, "property": {"step": 1}},
            {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"first_name": f, "last_name": l}, "property": {"step": 1}},
        ], {"decisions": ["auto", "auto", "auto"], "record_kind": {1: "crm"}, "fields": {1: {"rooms": stanze, "city": c}}},
            facts={"refs": {"1:owner": [1, True], "2:contact": [1, True], "3:contact": [1, True]}}))
    for i in range(15):  # palazzina censita: solo il contenitore
        s, c, n = r.choice(STREETS), r.choice(CITIES), r.randint(4, 24)
        civ = str(r.randint(1, 120))
        t = f"Censisci una palazzina in {s} {civ} a {c} con {n} appartamenti"
        out.append(_case(f"clear-building-{i:02d}", "clear", t,
                         [{"intent": "create_building", "quote": t, "address": s, "civic_number": civ, "city": c, "units_declared": n}],
                         {"decisions": ["auto"], "fields": {1: {"units_declared": n, "building_type": "condominio"}}, "count": 1}))
    for i in range(20):  # appuntamento con un contatto esistente
        f, l = r.choice(FIRST), r.choice(LAST)
        day, at = r.choice(DAYS), r.choice(TIMES)
        when, start = _when(day, at)
        parola, tipo = r.choice([("appuntamento", "other"), ("sopralluogo", "inspection"), ("telefonata", "call"),
                                 ("firma incarico", "mandate_signing"), ("visita", "buyer_visit")])
        t = f"{day[0].capitalize()} {at[0]} {parola} con {f} {l}"
        out.append(_case(f"clear-appt-{i:02d}", "clear", t,
                         [{"intent": "create_appointment", "quote": t, "when": when, "contact": {"first_name": f, "last_name": l}}],
                         {"decisions": ["auto"], "start_at": {1: start}, "fields": {1: {"appointment_type": tipo, "end_at": _end(start)}}},
                         facts={"refs": {"1:contact": [1, True]}}))
    for i in range(15):  # nota su un contatto esistente, risolto in modo esatto
        f, l = r.choice(FIRST), r.choice(LAST)
        t = f"Nota per {f} {l}: ha chiesto una valutazione aggiornata"
        out.append(_case(f"clear-note-{i:02d}", "clear", t,
                         [{"intent": "add_note", "quote": t, "text": "Ha chiesto una valutazione aggiornata",
                           "contact": {"first_name": f, "last_name": l}}],
                         {"decisions": ["auto"]}, facts={"refs": {"1:contact": [1, True]}}))
    for i in range(10):  # promemoria su un contatto
        f, l, day = r.choice(FIRST), r.choice(LAST), r.choice(DAYS)
        when, start = _when(day, None)
        t = f"Ricordami {day[0]} di richiamare {f} {l}"
        out.append(_case(f"clear-task-{i:02d}", "clear", t,
                         [{"intent": "add_task", "quote": t, "title": f"Richiamare {f} {l}", "due": when,
                           "contact": {"first_name": f, "last_name": l}}],
                         {"decisions": ["auto"], "start_at": {1: start}}, facts={"refs": {"1:contact": [1, True]}}))
    return out


def _multi(r: random.Random) -> list[dict]:
    out = []
    for i in range(15):  # il vocale completo: contatto nuovo + unita' + proprietario + Vende + appuntamento + task
        f, l, ph, s, c = r.choice(FIRST), r.choice(LAST), _phone(r), r.choice(STREETS), r.choice(CITIES)
        nome, stanze = r.choice(ROOMS)
        day, at = r.choice(DAYS), r.choice(TIMES)
        when, start = _when(day, at)
        t = (f"Ho visto {f} {l}, {ph}, vuole vendere il {nome} di {s} a {c}. "
             f"{day[0].capitalize()} {at[0]} sopralluogo con {f} e ricordami di mandargli la stima dopodomani")
        out.append(_case(f"multi-full-{i:02d}", "multi", t, [
            {"intent": "create_contact", "quote": f"{f} {l}, {ph}", "first_name": f, "last_name": l, "phone": ph},
            {"intent": "create_unit", "quote": f"{nome} di {s} a {c}", "rooms": stanze, "address": s, "city": c, "owner": {"step": 1}},
            {"intent": "link_owner", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}},
            {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}},
            {"intent": "create_appointment", "quote": f"{day[0]} {at[0]} sopralluogo con {f}", "when": when, "contact": {"step": 1}, "property": {"step": 2}},
            {"intent": "add_task", "quote": "ricordami di mandargli la stima dopodomani", "title": "Mandare la stima",
             "due": {"date_text": "dopodomani"}, "contact": {"step": 1}},
        ], {"decisions": ["auto"] * 6, "record_kind": {2: "crm"}, "start_at": {5: start, 6: "2026-10-12T09:00:00+02:00"},
            "fields": {5: {"appointment_type": "inspection"}}, "order": [1, 2, 3, 4, 5, 6]}))
    for i in range(10):  # palazzina + una unita' dettata davvero + nota
        s, c, n = r.choice(STREETS), r.choice(CITIES), r.randint(6, 20)
        piano, interno = str(r.randint(1, 6)), str(r.randint(1, 12))
        t = (f"Censisci la palazzina di {s} a {c}, {n} unità. Aggiungi l'appartamento al piano {piano} interno {interno}, "
             f"tre stanze. Nota sull'immobile: citofono rotto")
        out.append(_case(f"multi-census-{i:02d}", "multi", t, [
            {"intent": "create_building", "quote": f"palazzina di {s} a {c}, {n} unità", "address": s, "city": c, "units_declared": n},
            {"intent": "create_unit", "quote": f"appartamento al piano {piano} interno {interno}, tre stanze", "record_kind": "census",
             "floor": piano, "internal_number": interno, "rooms": 3, "building": {"step": 1}},
            {"intent": "add_note", "quote": "citofono rotto", "text": "Citofono rotto", "property": {"step": 2}},
        ], {"decisions": ["auto", "auto", "auto"], "record_kind": {2: "census"}, "order": [1, 2, 3]}))
    return out


def _ambiguous(r: random.Random) -> list[dict]:
    out = []
    for i in range(6):  # solo il nome
        f = r.choice(FIRST)
        t = f"Nuovo contatto {f}"
        out.append(_case(f"amb-weak-contact-{i}", "ambiguous", t,
                         [{"intent": "create_contact", "quote": t, "first_name": f}],
                         {"decisions": ["ask"], "reasons": {1: ["contact_identity_weak"]}}))
    for i in range(6):  # scheda senza evidenza commerciale ne' di censimento
        s, c = r.choice(STREETS), r.choice(CITIES)
        t = f"Aggiungi un appartamento in {s} a {c}"
        out.append(_case(f"amb-kind-{i}", "ambiguous", t,
                         [{"intent": "create_unit", "quote": t, "address": s, "city": c}],
                         {"decisions": ["ask"], "reasons": {1: ["record_kind_unknown"]}}))
    for i in range(5):  # palazzina senza indirizzo ne' nome
        n = r.randint(5, 30)
        t = f"Censisci una palazzina con {n} appartamenti"
        out.append(_case(f"amb-building-where-{i}", "ambiguous", t,
                         [{"intent": "create_building", "quote": t, "units_declared": n}],
                         {"decisions": ["ask"], "reasons": {1: ["building_location_missing"]}, "fields": {1: {"units_declared": n}}}))
    for i in range(5):  # "alle tre" senza fascia
        f, l = r.choice(FIRST), r.choice(LAST)
        t = f"Domani alle tre appuntamento con {f} {l}"
        out.append(_case(f"amb-hour-{i}", "ambiguous", t,
                         [{"intent": "create_appointment", "quote": t, "when": {"date_text": "domani", "time_text": "alle tre"},
                           "contact": {"first_name": f, "last_name": l}}],
                         {"decisions": ["ask"], "reasons": {1: ["time_ambiguous_hour"]}}, facts={"refs": {"1:contact": [1, True]}}))
    for i in range(3):  # giorno della settimana gia' passato "questo"
        f, l = r.choice(FIRST), r.choice(LAST)
        t = f"Questo venerdì alle 10 sopralluogo con {f} {l}"
        out.append(_case(f"amb-week-{i}", "ambiguous", t,
                         [{"intent": "create_appointment", "quote": t, "when": {"date_text": "questo venerdì", "time_text": "alle 10"},
                           "contact": {"first_name": f, "last_name": l}}],
                         {"decisions": ["ask"], "reasons": {1: ["date_ambiguous_this_week"]}}, facts={"refs": {"1:contact": [1, True]}}))
    for i in range(6):  # riferimento ambiguo o non trovato
        f = r.choice(FIRST)
        t = f"Nota per {f}: richiamare la prossima settimana"
        n = r.choice([0, 2, 3])
        out.append(_case(f"amb-ref-{i}", "ambiguous", t,
                         [{"intent": "add_note", "quote": t, "text": "Richiamare la prossima settimana", "contact": {"first_name": f}}],
                         {"decisions": ["ask"], "reasons": {1: ["reference_not_found" if n == 0 else "reference_ambiguous"]}},
                         facts={"refs": {"1:contact": [n, False]}}))
    for i in range(5):  # doppione nascosto (D1) o visibile
        f, l, ph = r.choice(FIRST), r.choice(LAST), _phone(r)
        t = f"Nuovo contatto {f} {l}, {ph}"
        nascosto = i % 2 == 0
        out.append(_case(f"amb-dup-{i}", "ambiguous", t,
                         [{"intent": "create_contact", "quote": t, "first_name": f, "last_name": l, "phone": ph}],
                         {"decisions": ["ask"], "reasons": {1: ["possible_duplicate_hidden" if nascosto else "possible_duplicate_contact"]}},
                         facts={"duplicate_hidden": [1]} if nascosto else {"duplicate_visible": {"1": 1}}))
    for i in range(4):  # conflitto d'agenda
        f, l = r.choice(FIRST), r.choice(LAST)
        day, at = r.choice(DAYS), r.choice(TIMES)
        when, _ = _when(day, at)
        t = f"{day[0].capitalize()} {at[0]} visita con {f} {l}"
        out.append(_case(f"amb-agenda-{i}", "ambiguous", t,
                         [{"intent": "create_appointment", "quote": t, "when": when, "contact": {"first_name": f, "last_name": l}}],
                         {"decisions": ["ask"], "reasons": {1: ["agenda_conflict"]}},
                         facts={"refs": {"1:contact": [1, True]}, "agenda_conflict": [1]}))
    for i in range(3):  # edificio simile gia' presente
        s, c = r.choice(STREETS), r.choice(CITIES)
        t = f"Censisci la palazzina di {s} 10 a {c}, 8 unità"
        out.append(_case(f"amb-similar-{i}", "ambiguous", t,
                         [{"intent": "create_building", "quote": t, "address": s, "civic_number": "10", "city": c, "units_declared": 8}],
                         {"decisions": ["ask"], "reasons": {1: ["similar_building_found"]}}, facts={"similar_building": [1]}))
    # cambio d'ora
    out.append(_case("amb-dst-march", "ambiguous", "Il 29 marzo alle 2 e mezza sopralluogo con Marco Rossi",
                     [{"intent": "create_appointment", "quote": "29 marzo alle 2 e mezza", "when": {"date_text": "29 marzo", "time_text": "alle 2 e mezza"},
                       "contact": {"first_name": "Marco", "last_name": "Rossi"}}],
                     {"decisions": ["ask"], "reasons": {1: ["time_nonexistent_dst"]}}, facts={"refs": {"1:contact": [1, True]}}))
    out[-1]["now"] = "2026-03-20T10:00:00+01:00"
    out.append(_case("amb-dst-october", "ambiguous", "Il 25 ottobre alle 2 e mezza sopralluogo con Marco Rossi",
                     [{"intent": "create_appointment", "quote": "25 ottobre alle 2 e mezza", "when": {"date_text": "25 ottobre", "time_text": "alle 2 e mezza"},
                       "contact": {"first_name": "Marco", "last_name": "Rossi"}}],
                     {"decisions": ["ask"], "reasons": {1: ["time_double_dst"]}}, facts={"refs": {"1:contact": [1, True]}}))
    # il passato
    out.append(_case("amb-past", "ambiguous", "Oggi alle 9 appuntamento con Marco Rossi",
                     [{"intent": "create_appointment", "quote": "oggi alle 9", "when": {"date_text": "oggi", "time_text": "alle 9"},
                       "contact": {"first_name": "Marco", "last_name": "Rossi"}}],
                     {"decisions": ["ask"], "reasons": {1: ["datetime_in_the_past"]}}, facts={"refs": {"1:contact": [1, True]}}))
    # task senza contatto: il CRM lo esige
    out.append(_case("amb-task-nocontact", "ambiguous", "Ricordami lunedì di comprare le buste",
                     [{"intent": "add_task", "quote": "ricordami lunedì di comprare le buste", "title": "Comprare le buste",
                       "due": {"date_text": "lunedì"}}],
                     {"decisions": ["ask"], "reasons": {1: ["task_without_contact"]}}))
    # la domanda del modello stesso
    out.append(_case("amb-model-clarification", "ambiguous", "Nuovo contatto... non si sente il cognome, telefono 333 4455667",
                     [{"intent": "create_contact", "quote": "Nuovo contatto", "first_name": "Luca", "phone": "333 4455667"}],
                     {"decisions": ["auto"], "clarifications": 1}, clarifications=["Non ho capito il cognome del contatto"]))
    # dipendenza da una domanda: la vendita aspetta il contatto
    out.append(_case("amb-chain", "ambiguous", "Giulia vuole vendere il bilocale di via Roma a Giulianova",
                     [{"intent": "create_contact", "quote": "Giulia", "first_name": "Giulia"},
                      {"intent": "create_unit", "quote": "bilocale di via Roma", "rooms": 2, "address": "via Roma", "city": "Giulianova", "owner": {"step": 1}},
                      {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}}],
                     {"decisions": ["ask", "ask", "ask"], "reasons": {1: ["contact_identity_weak"], 2: ["depends_on_question"], 3: ["depends_on_question"]},
                      "record_kind": {2: "crm"}}))
    return out


def _blocked(r: random.Random) -> list[dict]:
    out = []
    for i, desc in enumerate(["cancella il contatto Rossi", "sposta l'incarico a Marco", "elimina l'immobile di via Roma",
                              "crea l'incarico per via Trieste", "riassegna tutti i lead a Giulia", "svuota il cestino"]):
        out.append(_case(f"blk-unsupported-{i}", "blocked", desc.capitalize(),
                         [{"intent": "unsupported", "quote": desc, "description": desc}],
                         {"decisions": ["blocked"], "reasons": {1: ["unsupported_intent"]}}))
    for i in range(3):  # unita' inventate dal numero dichiarato
        s, c, n = r.choice(STREETS), r.choice(CITIES), 4
        t = f"Censisci una palazzina in {s} a {c} con {n} appartamenti"
        comandi = [{"intent": "create_building", "quote": t, "address": s, "city": c, "units_declared": n}]
        comandi += [{"intent": "create_unit", "quote": t, "record_kind": "census", "building": {"step": 1}} for _ in range(n)]
        out.append(_case(f"blk-invented-{i}", "blocked", t, comandi,
                         {"decisions": ["auto"] + ["blocked"] * n, "reasons": {2: ["units_invented"]}, "count": 1}))
    for i in range(3):  # affidato a un altro agente, da chi non puo' assegnare
        f, l, ag = r.choice(FIRST), r.choice(LAST), r.choice(FIRST)
        t = f"Lunedì alle 10 sopralluogo con {f} {l}, lo fa {ag}"
        out.append(_case(f"blk-assign-{i}", "blocked", t,
                         [{"intent": "create_appointment", "quote": t, "when": {"date_text": "lunedì", "time_text": "alle 10"},
                           "contact": {"first_name": f, "last_name": l}, "agent_name": ag}],
                         {"decisions": ["blocked"], "reasons": {1: ["agent_cannot_assign"]}},
                         facts={"refs": {"1:contact": [1, True]}, "actor_may_assign": False}))
    # lo stesso, ma chi parla puo' assegnare: resta da risolvere il nome dell'agente -> domanda
    out.append(_case("blk-assign-owner", "ambiguous", "Lunedì alle 10 sopralluogo con Marco Rossi, lo fa Giulia",
                     [{"intent": "create_appointment", "quote": "lo fa Giulia", "when": {"date_text": "lunedì", "time_text": "alle 10"},
                       "contact": {"first_name": "Marco", "last_name": "Rossi"}, "agent_name": "Giulia"}],
                     {"decisions": ["ask"], "reasons": {1: ["reference_not_found"]}},
                     facts={"refs": {"1:contact": [1, True]}, "actor_may_assign": True}))
    return out


def _mistakes(r: random.Random) -> list[dict]:
    out = []
    # il modello dichiara crm senza che il vocale parli di vendita
    out.append(_case("mis-kind-unverified", "mistake", "Aggiungi un trilocale in via Dante a Pineto",
                     [{"intent": "create_unit", "quote": "trilocale in via Dante a Pineto", "record_kind": "crm", "rooms": 3, "address": "via Dante", "city": "Pineto"}],
                     {"decisions": ["ask"], "reasons": {1: ["record_kind_unverified"]}}))
    # il modello dice census, ma c'e' un Vende sulla stessa scheda
    out.append(_case("mis-kind-conflict", "mistake", "Censisci il bilocale di via Roma di Marco Rossi, che vuole venderlo",
                     [{"intent": "create_unit", "quote": "bilocale di via Roma", "record_kind": "census", "rooms": 2, "address": "via Roma"},
                      {"intent": "activate_seller", "quote": "vuole venderlo", "contact": {"first_name": "Marco", "last_name": "Rossi"}, "property": {"step": 1}}],
                     {"decisions": ["ask", "ask"], "reasons": {1: ["record_kind_conflict"], 2: ["depends_on_question"]}},
                     facts={"refs": {"2:contact": [1, True]}}))
    # evidenze opposte nel trascritto
    out.append(_case("mis-kind-both", "mistake", "Censisci l'appartamento di via Cavour, è in vendita",
                     [{"intent": "create_unit", "quote": "appartamento di via Cavour", "address": "via Cavour"}],
                     {"decisions": ["ask"], "reasons": {1: ["record_kind_conflict"]}}))
    # il modello inventa un tipo di immobile: lo schema lo rifiuta
    out.append({"id": "mis-schema-type", "group": "mistake", "transcript": "Aggiungi un attico in via Roma", "now": NOW,
                "output": {"commands": [{"intent": "create_unit", "quote": "attico", "property_type": "attico", "address": "via Roma"}]},
                "facts": {}, "settings": {"mode": "auto"}, "expect": {"schema_error": True}})
    # campo in piu': lo schema e' chiuso
    out.append({"id": "mis-schema-extra", "group": "mistake", "transcript": "Nuovo contatto Marco Rossi", "now": NOW,
                "output": {"commands": [{"intent": "create_contact", "quote": "Marco Rossi", "first_name": "Marco", "last_name": "Rossi", "agency_id": 7}]},
                "facts": {}, "settings": {"mode": "auto"}, "expect": {"schema_error": True}})
    # riferimento a un passo successivo
    out.append({"id": "mis-schema-forward", "group": "mistake", "transcript": "Vende e poi il contatto", "now": NOW,
                "output": {"commands": [{"intent": "activate_seller", "quote": "vende", "contact": {"step": 2}, "property": {"address": "via Roma"}},
                                        {"intent": "create_contact", "quote": "contatto", "first_name": "Marco", "last_name": "Rossi"}]},
                "facts": {}, "settings": {"mode": "auto"}, "expect": {"schema_error": True}})
    # il modello calcola una data invece di riportare le parole: non e' ammesso
    out.append({"id": "mis-schema-isodate", "group": "mistake", "transcript": "Domani alle 15 appuntamento con Marco Rossi", "now": NOW,
                "output": {"commands": [{"intent": "create_appointment", "quote": "domani alle 15", "when": {"date_text": "2026-10-11", "time_text": "15:00"},
                                        "contact": {"first_name": "Marco", "last_name": "Rossi"}}]},
                "facts": {"refs": {"1:contact": [1, True]}}, "settings": {"mode": "auto"},
                "expect": {"decisions": ["ask"], "reasons": {1: ["date_unrecognized"]}}})
    return out


def _modes() -> list[dict]:
    """Le modalita' del collaudo: `review` chiede sempre, `assisted` solo per gli intenti ammessi."""
    base = [{"intent": "add_note", "quote": "nota", "text": "Richiamare", "contact": {"first_name": "Marco", "last_name": "Rossi"}},
            {"intent": "create_contact", "quote": "nuovo", "first_name": "Luca", "last_name": "Neri", "phone": "333 1112233"}]
    facts = {"refs": {"1:contact": [1, True]}}
    return [
        _case("mode-review", "modes", "Nota per Marco Rossi: richiamare. Nuovo contatto Luca Neri 333 1112233", base,
              {"decisions": ["ask", "ask"], "reasons": {1: ["mode_review"], 2: ["mode_review"]}}, facts=facts, settings={"mode": "review"}),
        _case("mode-assisted", "modes", "Nota per Marco Rossi: richiamare. Nuovo contatto Luca Neri 333 1112233", base,
              {"decisions": ["auto", "ask"], "reasons": {2: ["mode_assisted"]}}, facts=facts, settings={"mode": "assisted"}),
        _case("mode-auto", "modes", "Nota per Marco Rossi: richiamare. Nuovo contatto Luca Neri 333 1112233", base,
              {"decisions": ["auto", "auto"]}, facts=facts, settings={"mode": "auto"}),
        _case("mode-auto-no-hidden-check", "modes", "Nuovo contatto Luca Neri 333 1112233", base[1:],
              {"decisions": ["auto"]}, facts={"duplicate_hidden": [1]}, settings={"mode": "auto", "hidden_duplicate_check": False}),
    ]


def cases() -> list[dict]:
    r = random.Random(2026)
    tutti = _clear(r) + _multi(r) + _ambiguous(r) + _blocked(r) + _mistakes(r) + _modes()
    ids = [c["id"] for c in tutti]
    assert len(ids) == len(set(ids)), "id duplicati nel corpus"
    return tutti
