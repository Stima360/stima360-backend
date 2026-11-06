"""IMPORTAZIONE RUBRICA e WHATSAPP DUE CASELLE - prove offline.

    V  lettura vCard: 3.0 (iPhone), 2.1 con QUOTED-PRINTABLE, 4.0 con `tel:`, righe ripiegate,
       foto ignorata, scheda annidata ignorata, caratteri scappati, gruppi `item1.`
    S  servizio: unione delle schede con lo stesso numero, nomi, numeri scartati, statistiche
    M  montaggio: rotte dietro la sessione, migrazioni 130/131 valide e additive con down
    U  interfaccia: «Importa rubrica» dal file al server (multipart), mai innerHTML con dati;
       WhatsApp con `?inbox=`, chip della casella, scheda Impostazioni «Caselle WhatsApp»;
       node --check dei file toccati
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from contact_imports import service, vcard

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
NODE = shutil.which("node")


def _vcf(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode("utf-8")


# ---------------------------------------------------------------------------
# V - lettura
# ---------------------------------------------------------------------------

def test_v_iphone_30_card_with_groups_folding_and_photo():
    data = _vcf("""BEGIN:VCARD
VERSION:3.0
N:Rossi;Mario;Giuseppe;;
FN:Mario Rossi
ORG:Rossi Immobiliare\\, Srl;
item1.TEL;type=CELL;type=VOICE;type=pref:+39 333 123 4567
item1.X-ABLabel:cellulare
TEL;type=HOME;type=VOICE:0861 123456
TEL;type=WORK;type=FAX:0861 654321
EMAIL;type=INTERNET;type=HOME;type=pref:mario@example.test
PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD
 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
NOTE:Riga uno\\nriga due
END:VCARD
""")
    [e] = vcard.parse(data)
    assert e["fn"] == "Mario Rossi" and e["first_name"] == "Mario Giuseppe" and e["last_name"] == "Rossi"
    assert e["org"] == "Rossi Immobiliare, Srl" and e["emails"] == ["mario@example.test"]
    assert e["phones"] == [{"raw": "+39 333 123 4567", "label": "cellulare"}, {"raw": "0861 123456", "label": "casa"},
                           {"raw": "0861 654321", "label": "lavoro"}]


def test_v_android_21_quoted_printable_and_40_tel_uri_and_nested_agent():
    data = _vcf("""BEGIN:VCARD
VERSION:2.1
N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Bianchi;Lu=C3=ACa;;;
FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Lu=C3=ACa Bian=
chi
TEL;CELL:+39-347-0000001
TEL;HOME;VOICE:0039 347 0000002
AGENT:BEGIN:VCARD
FN:Agente Annidato
TEL:+39000
END:VCARD
EMAIL;PREF;INTERNET:luia@example.test
END:VCARD
BEGIN:VCARD
VERSION:4.0
FN:Studio Verdi
ORG:Studio Verdi
TEL;VALUE=uri;TYPE=work:tel:+39-0861-111222;ext=12
TEL;VALUE=uri;TYPE=cell:tel:+393470000003
END:VCARD
""")
    e1, e2 = vcard.parse(data)
    assert e1["first_name"] == "Luìa" and e1["last_name"] == "Bianchi" and e1["fn"] == "Luìa Bianchi"
    assert [p["raw"] for p in e1["phones"]] == ["+39-347-0000001", "0039 347 0000002"] and e1["emails"] == ["luia@example.test"]
    assert e2["fn"] == "Studio Verdi" and e2["org"] == "Studio Verdi" and e2["first_name"] is None
    assert e2["phones"] == [{"raw": "+39-0861-111222", "label": "lavoro"}, {"raw": "+393470000003", "label": "cellulare"}]


def test_v_no_cards_and_odd_encodings():
    assert vcard.parse(b"") == [] and vcard.parse(b"ciao\n") == []
    latin = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Niccolò Bò\r\nTEL:333\r\nEND:VCARD\r\n".encode("latin-1")
    assert vcard.parse(latin)[0]["fn"] == "Niccolò Bò"
    bom = b"\xef\xbb\xbf" + _vcf("BEGIN:VCARD\nFN:Con BOM\nEND:VCARD\n")
    assert vcard.parse(bom)[0]["fn"] == "Con BOM"


# ---------------------------------------------------------------------------
# S - servizio
# ---------------------------------------------------------------------------

def test_s_duplicates_in_file_merge_and_names_and_bad_numbers_are_handled():
    raw = [
        {"fn": "Mario Rossi", "first_name": "Mario", "last_name": "Rossi", "org": None, "emails": ["a@x.test"],
         "phones": [{"raw": "+39 333 1234567", "label": "cellulare"}, {"raw": "12", "label": None}]},
        {"fn": "Mario lavoro", "first_name": None, "last_name": None, "org": None, "emails": ["b@x.test"],
         "phones": [{"raw": "0039 333 1234567", "label": None}, {"raw": "0861 123456", "label": "casa"}]},
        {"fn": None, "first_name": None, "last_name": None, "org": "Studio Verdi", "emails": [], "phones": []},
        {"fn": "Solo Nome", "first_name": None, "last_name": None, "org": None, "emails": [],
         "phones": [{"raw": "+39 347 0000001", "label": None}]},
    ]
    entries = service._merge_duplicates(raw)
    assert [e["display_name"] for e in entries] == ["Mario Rossi", "Studio Verdi", "Solo Nome"]
    mario = entries[0]
    # lo stesso normalizzatore del CRM (core.normalization.normalize_phone): un numero nazionale perde lo 0 iniziale
    assert [p["normalized"] for p in mario["phones"]] == ["393331234567", "39861123456"]
    assert mario["merged"] == 1 and mario["emails"] == ["a@x.test", "b@x.test"] and mario["invalid_phones"] == ["12"]
    assert entries[1]["contact_type"] == "company" and entries[1]["company_name"] == "Studio Verdi"
    assert entries[2]["first_name"] == "Solo" and entries[2]["last_name"] == "Nome"
    service._classify(entries, [], {})
    assert [e["status"] for e in entries] == ["new", "no_phone", "new"]
    assert service._summary(entries) == {"entries": 3, "merged_in_file": 1, "invalid_phones": 1, "new": 2, "existing": 0,
                                         "ambiguous": 0, "no_phone": 1}
    contacts = [{"id": 7, "display_name": "Mario in Rubrica", "status": "active", "private_user_id": None},
                {"id": 8, "display_name": "Altro Mario", "status": "active", "private_user_id": 3}]
    service._classify(entries, contacts, {"393331234567": {7, 8}, "393470000001": {7}})
    assert entries[0]["status"] == "ambiguous" and [c["id"] for c in entries[0]["candidates"]] == [7, 8]
    assert entries[0]["candidates"][1]["private"] is True
    assert entries[2]["status"] == "existing" and entries[2]["candidates"][0]["id"] == 7


def test_s_decisions_follow_the_status():
    new = {"index": 0, "status": "new", "candidates": []}
    existing = {"index": 1, "status": "existing", "candidates": [{"id": 5}]}
    amb = {"index": 2, "status": "ambiguous", "candidates": [{"id": 5}, {"id": 6}]}
    nophone = {"index": 3, "status": "no_phone", "candidates": []}
    assert service._decision({}, new) == ("create", None) and service._decision({"0": {"action": "skip"}}, new) == ("skip", None)
    assert service._decision({}, existing) == ("merge", 5)
    assert service._decision({}, amb) == ("skip", None)
    assert service._decision({"2": {"action": "merge", "contact_id": 6}}, amb) == ("merge", 6)
    assert service._decision({"2": {"action": "merge", "contact_id": 9}}, amb) == ("skip", None)
    assert service._decision({"2": {"action": "create"}}, amb) == ("create", None)
    assert service._decision({"3": {"action": "create"}}, nophone) == ("skip", None)


# ---------------------------------------------------------------------------
# M - montaggio e migrazioni
# ---------------------------------------------------------------------------

def test_m_routes_are_mounted_behind_the_operator_session_and_never_send():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(whatsapp_inboxes_router, dependencies=[Depends(require_authenticated_operator)])" in main_py
    assert "app.include_router(contact_imports_router, dependencies=[Depends(require_authenticated_operator)])" in main_py
    for pkg in ("contact_imports", "whatsapp_inboxes"):
        for path in (ROOT / pkg).glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("requests", "httpx", "graph.facebook", "invia_whatsapp", "WHATSAPP_TOKEN", "enqueue("):
                assert forbidden not in text, (path.name, forbidden)


def test_m_migrations_130_and_131_are_valid_additive_and_reversible():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    for version in ("130_whatsapp_inboxes", "131_contact_import"):
        m = next(x for x in tutte if x.version == version)
        assert m.down_available and runner.validate_migration(m) == []
        up = runner.strip_sql_comments((ROOT / "migrations" / f"{version}.sql").read_text(encoding="utf-8"))
        assert not re.search(r"\b(DROP TABLE|DELETE FROM|TRUNCATE)\b", up), version
    up130 = (ROOT / "migrations" / "130_whatsapp_inboxes.sql").read_text(encoding="utf-8")
    assert "PRIMARY KEY (agency_id, user_id, phone_number_id, sender_phone)" in up130
    assert "UNIQUE (phone_number_id)" in up130
    up131 = (ROOT / "migrations" / "131_contact_import.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN private_user_id" in up131 and "UNIQUE (contact_id, phone_normalized)" in up131
    down131 = (ROOT / "migrations" / "131_contact_import_down.sql").read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in down131


# ---------------------------------------------------------------------------
# U - interfaccia
# ---------------------------------------------------------------------------

def test_u_import_dialog_sends_the_file_to_the_server_and_renders_safely():
    comp = (ASSETS / "components" / "contact-import.js").read_text(encoding="utf-8")
    view = (ASSETS / "views" / "contatti.js").read_text(encoding="utf-8")
    assert "openContactImportDialog" in view and 'id="contatti-import-btn"' in view and "Importa rubrica" in view
    assert "new FormData()" in comp and "`${API}/preview`" in comp and "/api/contact-imports" in comp
    assert "innerHTML" not in comp and "FileReader" not in comp                 # il file non si legge nel browser
    assert "Importa tutti" in comp and "data-contact-import-decision" in comp and "/commit`" in comp
    detail = (ASSETS / "views" / "contatto-dettaglio.js").read_text(encoding="utf-8")
    assert "Altri numeri" in detail and "data-contact-share" in detail and "/api/contact-imports/contacts/" in detail


def test_u_whatsapp_view_speaks_per_inbox_and_settings_have_the_inboxes_tab():
    view = (ASSETS / "views" / "messaggi.js").read_text(encoding="utf-8")
    reply = (ASSETS / "components" / "whatsapp-reply.js").read_text(encoding="utf-8")
    auto = (ASSETS / "components" / "auto-reply.js").read_text(encoding="utf-8")
    for text in (view, reply, auto):
        assert "innerHTML" not in text
    assert "inboxQuery(" in view and "data-messages-inboxes" in view and "wa-inbox-chip" in view
    assert "data-messages-other-inbox" in view and "Contatto riservato" in view
    assert "`?inbox=${encodeURIComponent(inbox)}`" in reply and "Partirà da" in reply
    assert "fetchAutoState(sender, inbox)" in view and "data-auto-inbox-off" in auto
    sezioni = (ASSETS / "views" / "sezioni.js").read_text(encoding="utf-8")
    assert "key: 'whatsapp', label: 'Caselle WhatsApp'" in sezioni
    caselle = (ASSETS / "views" / "caselle-whatsapp.js").read_text(encoding="utf-8")
    assert "/api/whatsapp-inboxes" in caselle and "innerHTML" not in caselle and "data-wa-inbox-save" in caselle
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    assert ".wa-inbox-chip" in css and ".wa-inbox-filters" in css


@pytest.mark.skipif(NODE is None, reason="node non disponibile: controllo di sintassi NON eseguito (BLOCKED)")
@pytest.mark.parametrize("rel", ["views/messaggi.js", "views/contatti.js", "views/contatto-dettaglio.js",
                                 "views/caselle-whatsapp.js", "views/sezioni.js", "components/whatsapp-reply.js",
                                 "components/auto-reply.js", "components/contact-import.js", "main.js"])
def test_u_changed_javascript_parses(rel):
    out = subprocess.run([NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
