"""IMPORTAZIONE RUBRICA (fase B) su PostgreSQL VERO - banco del 4A (fixture
`wa`: migrazioni dal runner compresa la 131, ogni invio esterno e' una
trappola). Il file .vcf e' sintetico e passa dalla rotta multipart vera.

    I1  anteprima e «Importa tutti»: tutti i numeri conservati, contatti riservati a chi importa
        (titolare e amministratore non li vedono), nessun invio; un secondo import identico non cambia nulla
    I2  numero su piu' contatti: lo decide l'operatore (unire / nuovo / saltare); senza scelta si salta
    I3  abbinamento WhatsApp: le chat gia' ricevute si collegano all'import, quelle future all'arrivo
        (anche dal terzo numero); per un collega il contatto riservato resta anonimo
    I4  contatto esistente dell'agenzia: si completa (numeri mancanti, email vuota), nulla si sovrascrive
    I5  isolamento fra agenzie: ogni agenzia importa nella propria rubrica
    I6  «Condividi con l'agenzia»: solo chi ha importato; dopo, tutti lo vedono
    I7  permessi e limiti: anteprima di un altro operatore non confermabile, file senza schede rifiutato
"""
from __future__ import annotations

import pytest

from tests.test_blocco_4a_whatsapp_inbound_postgres import (  # noqa: F401 - fixture
    API,
    NUMBER_A,
    NUMBER_B,
    _change,
    _contact,
    _message,
    _payload,
    _q,
    _rows,
    _sender,
    app,
    banco,
    pytestmark,
    wa,
)

IMPORTS = "/api/contact-imports"
CONTACTS = "/api/core/contacts"


def _vcf(*cards):
    out = []
    for c in cards:
        lines = ["BEGIN:VCARD", "VERSION:3.0"]
        if c.get("n"):
            lines.append(f"N:{c['n']}")
        if c.get("fn"):
            lines.append(f"FN:{c['fn']}")
        if c.get("org"):
            lines.append(f"ORG:{c['org']}")
        for i, tel in enumerate(c.get("tel", [])):
            lines.append(f"TEL;type={'CELL' if i == 0 else 'HOME'}:{tel}")
        for mail in c.get("email", []):
            lines.append(f"EMAIL;type=INTERNET:{mail}")
        lines.append("END:VCARD")
        out.append("\r\n".join(lines))
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def _preview(client, data, name="rubrica.vcf"):
    return client.post(f"{IMPORTS}/preview", files={"file": (name, data, "text/vcard")})


def _commit(client, import_id, decisions=None):
    return client.post(f"{IMPORTS}/{import_id}/commit", json={"decisions": decisions or {}})


def _import(client, data, decisions=None):
    p = _preview(client, data)
    assert p.status_code == 200, p.text
    c = _commit(client, p.json()["import_id"], decisions)
    assert c.status_code == 200, c.text
    return p.json(), c.json()["result"]


def _ledger_count(b):
    return _q(b, "SELECT count(*) FROM communication_messages")[0][0]


def _send(b, sender, body="Ciao", number=NUMBER_A):
    assert b["post"](_payload(_change(number, [_message(sender, body=body)]))).status_code == 200


@pytest.fixture
def ib(wa):
    return wa


# ---------------------------------------------------------------------------
# I1 - anteprima, importazione, riservatezza, idempotenza, nessun invio
# ---------------------------------------------------------------------------

def test_i1_import_keeps_every_number_creates_private_contacts_and_repeats_without_duplicates(ib):
    ids = ib["ids"]
    owner, admin, agent = ib["login"]("owner_a"), ib["login"]("admin_a"), ib["login"]("agent_a")
    s1, s2, s3, s4 = _sender(), _sender(), _sender(), _sender()
    data = _vcf({"n": "Rossi;Mario;;;", "fn": "Mario Rossi", "tel": [f"+39 {s1[2:5]} {s1[5:]}", f"0039{s2[2:]}", s3[2:]],
                 "email": ["mario@example.test"]},
                {"fn": "Mario (lavoro)", "tel": [f"+39{s2[2:]}"]},                         # stesso numero: si unisce
                {"fn": "Luisa Bianchi", "tel": [f"+39 {s4[2:]}"]},
                {"fn": "Senza Numero"},
                {"fn": "Numero Rotto", "tel": ["12"]})
    before = _ledger_count(ib)
    p = _preview(agent, data)
    assert p.status_code == 200, p.text
    preview = p.json()
    assert preview["summary"] == {"entries": 4, "merged_in_file": 1, "invalid_phones": 1, "new": 2, "existing": 0,
                                  "ambiguous": 0, "no_phone": 2}
    mario = next(e for e in preview["entries"] if e["display_name"] == "Mario Rossi")
    assert [x["normalized"] for x in mario["phones"]] == [s1, s2, s3] and mario["merged"] == 1 and mario["status"] == "new"
    assert mario["first_name"] == "Mario" and mario["last_name"] == "Rossi"
    # nulla scritto dall'anteprima
    assert _q(ib, "SELECT count(*) FROM contacts WHERE phone_normalized = %s", (s1,))[0][0] == 0
    c = _commit(agent, preview["import_id"])
    assert c.status_code == 200, c.text
    result = c.json()["result"]
    assert result == {"created": 2, "updated": 0, "unchanged": 0, "skipped": 0, "undecided": 0, "phones_added": 0,
                      "no_phone": 2, "conversations_linked": 0}
    row = _q(ib, "SELECT id, display_name, first_name, last_name, email, phone, secondary_phone, phone_normalized, "
                 "source, private_user_id, assigned_agent_id, created_by_user_id, agency_id FROM contacts "
                 "WHERE phone_normalized = %s", (s1,))[0]
    cid = row[0]
    assert row[1:] == ("Mario Rossi", "Mario", "Rossi", "mario@example.test", f"+39 {s1[2:5]} {s1[5:]}", f"0039{s2[2:]}",
                       s1, "rubrica_import", ids["agent_a"], ids["agent_a"], ids["agent_a"], ids["a"])
    assert _q(ib, "SELECT phone_normalized, label, position FROM contact_phones WHERE contact_id = %s ORDER BY position",
              (cid,)) == [(s1, "cellulare", 0), (s2, "casa", 1), (s3, "casa", 2)]
    # riservato all'agente che ha importato: titolare e amministratore non lo vedono
    assert agent.get(f"{CONTACTS}/{cid}").status_code == 200
    detail = agent.get(f"{CONTACTS}/{cid}").json()
    assert detail["private_user_id"] == ids["agent_a"] and [p["phone_normalized"] for p in detail["phones"]] == [s1, s2, s3]
    for client in (owner, admin):
        assert client.get(f"{CONTACTS}/{cid}").status_code == 404
        assert client.get(f"{CONTACTS}?search={s3}").json()["items"] == []
        assert client.get(f"/api/crm/contacts/{cid}/360").status_code == 404
    assert [c["id"] for c in agent.get(f"{CONTACTS}?search={s3}").json()["items"]] == [cid]   # anche il terzo numero
    # nessun invio, nessuna comunicazione
    assert _ledger_count(ib) == before
    # il registro: riepilogo si', voci (dati personali) no
    imp = _q(ib, "SELECT status, entries IS NULL, summary->'result'->>'created' FROM contact_imports WHERE id = %s",
             (preview["import_id"],))[0]
    assert imp == ("done", True, "2")
    assert _commit(agent, preview["import_id"]).status_code == 409                       # gia' importata
    # secondo import identico: tutto «gia' in rubrica», nessun nuovo contatto o numero
    preview2, result2 = _import(agent, data)
    assert preview2["summary"]["existing"] == 2 and preview2["summary"]["new"] == 0
    assert result2["created"] == 0 and result2["unchanged"] == 2 and result2["phones_added"] == 0
    assert _q(ib, "SELECT count(*) FROM contacts WHERE phone_normalized IN (%s, %s)", (s1, s4))[0][0] == 2
    assert _q(ib, "SELECT count(*) FROM contact_phones WHERE contact_id = %s", (cid,))[0][0] == 3
    assert _ledger_count(ib) == before


# ---------------------------------------------------------------------------
# I2 - numero su piu' contatti: decide l'operatore
# ---------------------------------------------------------------------------

def test_i2_a_number_on_two_contacts_waits_for_the_operator_decision(ib):
    ids = ib["ids"]
    owner = ib["login"]("owner_a")
    s = _sender()
    c1 = _contact(ib, ids["a"], "Primo Omonimo", s)
    c2 = _contact(ib, ids["a"], "Secondo Omonimo", s)
    extra = _sender()
    data = _vcf({"fn": "Omonimo Rubrica", "tel": [f"+{s}", f"+{extra}"]})
    p = _preview(owner, data).json()
    e = p["entries"][0]
    assert e["status"] == "ambiguous" and sorted(c["id"] for c in e["candidates"]) == sorted([c1, c2])
    # senza decisione: si salta, nulla si scrive
    r = _commit(owner, p["import_id"]).json()["result"]
    assert r["undecided"] == 1 and r["created"] == 0 and r["updated"] == 0
    assert _q(ib, "SELECT count(*) FROM contact_phones WHERE phone_normalized = %s", (extra,))[0][0] == 0
    # unire al secondo: il numero in piu' va a lui
    p = _preview(owner, data).json()
    r = _commit(owner, p["import_id"], {str(p["entries"][0]["index"]): {"action": "merge", "contact_id": c2}}).json()["result"]
    assert r["updated"] == 1 and r["phones_added"] == 1
    assert _q(ib, "SELECT contact_id FROM contact_phones WHERE phone_normalized = %s", (extra,)) == [(c2,)]
    assert _q(ib, "SELECT secondary_phone FROM contacts WHERE id = %s", (c2,)) == [(f"+{extra}",)]
    # ora il numero `extra` e' solo di c2 ma `s` e' ancora su due: resta ambiguo; «crea nuovo» crea un terzo
    p = _preview(owner, data).json()
    assert p["entries"][0]["status"] == "ambiguous"
    r = _commit(owner, p["import_id"], {str(p["entries"][0]["index"]): {"action": "create"}}).json()["result"]
    assert r["created"] == 1
    assert _q(ib, "SELECT count(*) FROM contacts WHERE phone_normalized = %s", (s,))[0][0] == 3
    # una decisione su un contatto che non e' candidato non vale: si salta
    p = _preview(owner, data).json()
    r = _commit(owner, p["import_id"], {str(p["entries"][0]["index"]): {"action": "merge", "contact_id": 999999}}).json()["result"]
    assert r["undecided"] == 1 and r["created"] == 0


# ---------------------------------------------------------------------------
# I3 - abbinamento WhatsApp: chat gia' ricevute e future, anche dal terzo numero
# ---------------------------------------------------------------------------

def test_i3_imported_contacts_match_existing_and_future_conversations(ib):
    ids = ib["ids"]
    owner, admin = ib["login"]("owner_a"), ib["login"]("admin_a")
    main, third = _sender(), _sender()
    _send(ib, main, "Prima dell'import")
    assert _rows(ib, "sender_phone = %s", (main,))[0][6:8] == (None, "none")
    data = _vcf({"fn": "Cliente Rubrica", "tel": [f"+{main}", f"+{_sender()}", f"+{third}"]})
    _, result = _import(owner, data)
    assert result["created"] == 1 and result["conversations_linked"] == 1
    cid = _q(ib, "SELECT id FROM contacts WHERE phone_normalized = %s", (main,))[0][0]
    assert _rows(ib, "sender_phone = %s", (main,))[0][6:8] == (cid, "matched")
    conv = owner.get(f"{API}/conversations/{main}").json()
    assert conv["contact"] == {"id": cid, "name": "Cliente Rubrica", "match": "matched", "others_with_number": 0}
    # un messaggio dal TERZO numero del contatto, dopo: si collega da solo
    _send(ib, third, "Dal terzo numero")
    assert _rows(ib, "sender_phone = %s", (third,))[0][6:8] == (cid, "matched")
    # per un collega che non vede il contatto riservato: la chat c'e', il contatto e' anonimo
    other = admin.get(f"{API}/conversations/{third}").json()
    assert other["contact"]["private"] is True and other["contact"]["id"] is None and other["contact"]["name"] is None
    assert other["contact_candidates"] == []
    listed = next(c for c in admin.get(f"{API}/conversations").json()["items"] if c["sender_phone"] == third)
    assert listed["last_message"]["contact"]["private"] is True
    mine = owner.get(f"{API}/conversations/{third}").json()
    assert mine["contact"]["id"] == cid and [c["id"] for c in mine["contact_candidates"]] == [cid]
    # un messaggio STOP a un contatto riservato: nessun invio comunque (trappola della fixture)


# ---------------------------------------------------------------------------
# I4 - contatto esistente dell'agenzia: completato, mai sovrascritto
# ---------------------------------------------------------------------------

def test_i4_an_existing_agency_contact_is_completed_not_overwritten(ib):
    ids = ib["ids"]
    owner = ib["login"]("owner_a")
    s, s2 = _sender(), _sender()
    cid = _contact(ib, ids["a"], "Nome Originale", s)
    data = _vcf({"n": "Diverso;Nome;;;", "fn": "Nome Diverso", "tel": [f"+{s}", f"+{s2}"], "email": ["nuova@example.test"]})
    preview, result = _import(owner, data)
    assert preview["entries"][0]["status"] == "existing" and preview["entries"][0]["candidates"][0]["id"] == cid
    assert result == {"created": 0, "updated": 1, "unchanged": 0, "skipped": 0, "undecided": 0, "phones_added": 1,
                      "no_phone": 0, "conversations_linked": 0}
    row = _q(ib, "SELECT display_name, first_name, email, secondary_phone, private_user_id FROM contacts WHERE id = %s", (cid,))[0]
    assert row == ("Nome Originale", "Nome Originale", "nuova@example.test", f"+{s2}", None)   # nomi intatti, email ed s2 aggiunti
    assert _q(ib, "SELECT phone_normalized FROM contact_phones WHERE contact_id = %s", (cid,)) == [(s2,)]
    # il contatto resta dell'agenzia (non diventa riservato) e lo vedono tutti
    assert ib["login"]("admin_a").get(f"{CONTACTS}/{cid}").status_code == 200
    # un agente che importa lo stesso numero: il doppione si evita a livello di AGENZIA (il contatto
    # dell'agenzia esiste gia', anche se non e' assegnato a lui): «gia' in rubrica», nessun duplicato
    agent = ib["login"]("agent_a")
    p = _preview(agent, data).json()
    assert p["entries"][0]["status"] == "existing" and p["entries"][0]["candidates"][0]["id"] == cid
    r = _commit(agent, p["import_id"]).json()["result"]
    assert r["created"] == 0 and r["unchanged"] == 1
    assert _q(ib, "SELECT count(*) FROM contacts WHERE phone_normalized = %s", (s,))[0][0] == 1


# ---------------------------------------------------------------------------
# I5 - isolamento fra agenzie
# ---------------------------------------------------------------------------

def test_i5_each_agency_imports_into_its_own_book(ib):
    ids = ib["ids"]
    s = _sender()
    data = _vcf({"fn": "Cliente Comune", "tel": [f"+{s}"]})
    _, ra = _import(ib["login"]("owner_a"), data)
    _, rb = _import(ib["login"]("owner_b"), data)
    assert ra["created"] == 1 and rb["created"] == 1
    assert _q(ib, "SELECT agency_id, private_user_id FROM contacts WHERE phone_normalized = %s ORDER BY agency_id", (s,)) \
        == [(ids["a"], ids["owner_a"]), (ids["b"], ids["owner_b"])]
    assert ib["login"]("owner_a").get(f"{CONTACTS}?search={s}").json()["items"][0]["agency_id"] == ids["a"]
    assert ib["login"]("owner_b").get(f"{CONTACTS}?search={s}").json()["items"][0]["agency_id"] == ids["b"]


# ---------------------------------------------------------------------------
# I6 - condividere con l'agenzia
# ---------------------------------------------------------------------------

def test_i6_sharing_is_only_for_the_importer_and_then_everyone_sees(ib):
    ids = ib["ids"]
    agent, owner, admin = ib["login"]("agent_a"), ib["login"]("owner_a"), ib["login"]("admin_a")
    s = _sender()
    _import(agent, _vcf({"fn": "Da Condividere", "tel": [f"+{s}"]}))
    cid = _q(ib, "SELECT id FROM contacts WHERE phone_normalized = %s", (s,))[0][0]
    assert owner.get(f"{CONTACTS}/{cid}").status_code == 404
    assert owner.post(f"{IMPORTS}/contacts/{cid}/share").status_code == 404           # non e' il suo
    assert admin.post(f"{IMPORTS}/contacts/{cid}/share").status_code == 404
    assert agent.post(f"{IMPORTS}/contacts/{cid}/share").json() == {"contact_id": cid, "private": False}
    assert owner.get(f"{CONTACTS}/{cid}").json()["private_user_id"] is None
    assert admin.get(f"{CONTACTS}/{cid}").status_code == 200
    assert agent.get(f"{CONTACTS}/{cid}").status_code == 200                        # resta assegnato a lui
    assert agent.post(f"{IMPORTS}/contacts/{cid}/share").status_code == 404           # gia' condiviso


# ---------------------------------------------------------------------------
# I7 - permessi e limiti
# ---------------------------------------------------------------------------

def test_i7_previews_belong_to_their_operator_and_bad_files_are_refused(ib):
    owner, admin = ib["login"]("owner_a"), ib["login"]("admin_a")
    p = _preview(owner, _vcf({"fn": "Di Olga", "tel": [f"+{_sender()}"]})).json()
    assert admin.get(f"{IMPORTS}/{p['import_id']}").status_code == 404
    assert _commit(admin, p["import_id"]).status_code == 404
    assert ib["login"]("owner_b").get(f"{IMPORTS}/{p['import_id']}").status_code == 404
    assert owner.get(f"{IMPORTS}/{p['import_id']}").json()["status"] == "preview"
    r = _preview(owner, b"questo non e' un vcard\n")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "NO_ENTRIES"
    r = owner.post(f"{IMPORTS}/{p['import_id']}/commit", json={"decisions": {"0": {"action": "boom"}}})
    assert r.status_code == 422
