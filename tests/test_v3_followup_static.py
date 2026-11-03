"""V3 - FOLLOW-UP PROPRIETARI 180 GIORNI: le regole lette senza database.

  01  il calendario e gli oggetti sono ESATTAMENTE quelli approvati: 16 email,
      giorno 4 -> 180, sequenza lineare (nessuna ripartenza).
  02  i 16 corpi approvati sono nel codice e nel registro: i testi definitivi
      del 2026-11-03 con la firma «Il team STIMA360»; l'email 6 con il link
      della guida e nessun allegato promesso (adattamento approvato); un corpo
      entra solo se valido ({{link_guida}} solo nella 6).
  03  ogni email v3 e' marketing, chiede il link di disiscrizione e lo stampa
      in fondo; l'email 6 chiede anche il link della guida.
  04  il limite dei destinatari TEST: assente = nessun limite; presente =
      solo gli indirizzi elencati (vuoto = nessuno); solo marketing.
  05  la guida PDF ufficiale: titolo, impronta, file nel repository.
  06  il filtro dei proprietari verificati e l'attivita' dell'email 15.
  07  l'interfaccia: la scheda «Sequenze email» legge solo il quadro.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

APPROVATO = [
    (4, 'Vendi a 200.000 €: quanto ti rimane davvero?'),
    (8, 'Le 7 foto da non dimenticare nel tuo annuncio'),
    (12, 'Hai scelto il prezzo guardando le altre case in vendita?'),
    (18, 'Quale acquirente potrebbe scegliere casa tua?'),
    (25, 'Trovi l’acquirente. Ma i documenti sono pronti?'),
    (33, 'La tua guida per preparare la vendita di casa'),
    (42, '“Terrazzo di 40 m²”: cosa significa per chi compra?'),
    (52, 'Fare lavori prima di vendere: ne vale la pena?'),
    (63, 'Meglio un’offerta da 200.000 € o da 195.000 €?'),
    (75, 'Il tuo annuncio è online, ma non arrivano chiamate?'),
    (88, 'Hai spostato una parete? Controlla questo prima di vendere'),
    (102, 'Stessa via, stessa metratura. Perché due prezzi diversi?'),
    (117, 'Cinque cose da fare prima di una visita'),
    (135, 'Prima di firmare una proposta, verifica questi 5 punti'),
    (157, 'A che punto sei con la vendita di casa?'),
    (180, 'I primi 30 giorni per mettere in vendita casa'),
]


def test_01_calendario_e_oggetti_approvati():
    from communication import followup_v3_texts as t
    from communication import journey_catalog as c
    assert list(t.CALENDARIO) == APPROVATO
    passi = c.passi_followup_v3()
    assert len(passi) == 16 and [p["step_no"] for p in passi] == list(range(1, 17))
    assert [p["delay_seconds"] // 86400 for p in passi] == [g for g, _ in APPROVATO]
    assert max(p["delay_seconds"] for p in passi) == 180 * 86400
    assert all(p["delay_from"] == "trigger" and p["default_mode"] == "automatic"
               and p["channel"] == "email" and p["communication_type"] == "marketing" for p in passi)
    assert c.FOLLOWUP_V3_NAME == "Follow-up proprietari 180 giorni"
    assert t.EMAIL_GUIDA == 6 and t.EMAIL_RISPOSTE == 15


def test_02_nessun_testo_inventato_e_regole_dei_testi():
    from communication import followup_v3_texts as t
    from communication import templates
    assert sorted(t.TESTI) == list(range(1, 17)), "i 16 corpi consegnati dal titolare"
    assert all(not t.problemi(n, t.TESTI[n]) for n in t.TESTI)
    for n in t.TESTI:
        assert templates.get(f"followup_v3_m{n}", 3).subject({}) == t.CALENDARIO[n - 1][1]
    assert all(t.TESTI[n].startswith("Ciao!") for n in t.TESTI)
    # decisioni del titolare: la firma «Il team STIMA360» chiude ogni testo
    # definitivo (la disiscrizione la mette il CRM), niente sintassi markdown;
    # l'email 6: la guida e' un link e non un allegato
    assert [n for n in t.TESTI if t.TESTI[n].endswith("\n\nIl team STIMA360")] == list(range(1, 17))
    assert all("[" not in t.TESTI[n] and "](" not in t.TESTI[n] for n in t.TESTI)
    assert "che puoi scaricare qui: {{link_guida}}.\n" in t.TESTI[6]
    assert not [n for n in t.TESTI if "allegat" in t.TESTI[n].lower()]
    reale = templates.get("followup_v3_m6", 3).body(
        {"contact_first_name": "Anna", "agency_name": "Agenzia Prova",
         "unsubscribe_url": "https://crm.example.invalid/u?t=x",
         "guide_url": "https://crm.example.invalid/api/public/communication/guida-vendere-casa-2026.pdf"})
    assert "qui: https://crm.example.invalid/api/public/communication/guida-vendere-casa-2026.pdf.\n" in reale
    assert reale.rstrip().endswith("Se non vuoi piu' ricevere questi messaggi: https://crm.example.invalid/u?t=x")
    assert "\nAgenzia Prova\nsu Stima360\n" in reale
    assert t.problemi(1, "") == ["testo mancante"]
    assert t.problemi(1, "Ciao {{nome}}") == []
    assert t.problemi(6, "Ciao {{nome}}") != [], "l'email 6 deve portare il link della guida"
    assert t.problemi(6, "Ciao {{nome}}, {{link_guida}}") == []
    assert t.problemi(6, "In allegato la guida: {{link_guida}}") != [], "nessun allegato promesso"
    assert t.problemi(2, "Ciao {{nome}}, {{link_guida}}") != []
    assert t.problemi(3, "Ciao {{cognome}}") != []
    validi = {n: "Ciao {{nome}}" + (" {{link_guida}}" if n == 6 else "") for n in range(1, 17)}
    assert len(templates.templates_v3(validi)) == 16
    assert len(templates.templates_v3({**validi, 6: "Ciao {{nome}}"})) == 15, "un testo non valido non si registra"


def test_03_disiscrizione_e_guida_nel_corpo():
    from communication import templates
    validi = {n: "Ciao {{nome}}" + (" ecco: {{link_guida}}" if n == 6 else "") for n in range(1, 17)}
    registro = templates.templates_v3(validi)
    base = {"contact_first_name": "Anna", "agency_name": "Agenzia Prova",
            "unsubscribe_url": "https://crm.example.invalid/u?t=x"}
    for (chiave, versione), t in registro.items():
        assert t.communication_type == "marketing" and templates.UNSUBSCRIBE_FIELD in t.required_fields
        corpo = t.body({**base, "guide_url": "https://crm.example.invalid/guida.pdf"})
        assert corpo.startswith("Ciao Anna") and "Se non vuoi piu' ricevere questi messaggi: https://crm.example.invalid/u?t=x" in corpo
    sesta = registro[("followup_v3_m6", 3)]
    assert templates.GUIDE_FIELD in sesta.required_fields
    assert "https://crm.example.invalid/guida.pdf" in sesta.body({**base, "guide_url": "https://crm.example.invalid/guida.pdf"})
    assert templates.GUIDE_FIELD not in registro[("followup_v3_m5", 3)].required_fields


def test_04_limite_destinatari(monkeypatch):
    from communication import test_recipients as r
    monkeypatch.delenv(r.ENV, raising=False)
    assert r.allowed() is None and not r.blocked({"communication_type": "marketing", "destination_snapshot": "x@y.it"}, None)
    monkeypatch.setenv(r.ENV, " A@Example.it; b@example.it,  non-un-indirizzo ")
    consentiti = r.allowed()
    assert consentiti == frozenset({"a@example.it", "b@example.it"})
    assert not r.blocked({"communication_type": "marketing", "destination_snapshot": "a@example.IT "}, consentiti)
    assert r.blocked({"communication_type": "marketing", "destination_snapshot": "c@example.it"}, consentiti)
    assert not r.blocked({"communication_type": "service", "destination_snapshot": "c@example.it"}, consentiti)
    monkeypatch.setenv(r.ENV, "")
    assert r.allowed() == frozenset() and r.blocked({"communication_type": "marketing", "destination_snapshot": "a@example.it"}, r.allowed())


def test_05_guida_pdf_ufficiale():
    from communication import guida_pdf
    assert guida_pdf.FILE.is_file()
    assert hashlib.sha256(guida_pdf.FILE.read_bytes()).hexdigest() == guida_pdf.SHA256
    assert guida_pdf.disponibile() and guida_pdf.url("https://crm.example.invalid/") == \
        "https://crm.example.invalid/api/public/communication/guida-vendere-casa-2026.pdf"
    assert guida_pdf.url("") is None
    assert b"Vendere un immobile senza errori" in guida_pdf.FILE.read_bytes()


def test_06_proprietari_verificati_e_attivita_15():
    from communication import audience, integrations
    filtro = audience.PROPRIETARIO_VERIFICATO
    assert "contact_roles" in filtro and "property_contacts" in filtro and "deleted_at IS NULL" in filtro
    assert "'owner', 'seller'" in filtro and "'contact'" not in filtro and "'buyer'" not in filtro
    # Decisione del titolare: entra anche chi ha chiesto una valutazione (stima
    # collegata tramite lead, o mail di servizio con il PDF della stima).
    valutazione = audience.RICHIESTA_VALUTAZIONE
    assert "lead_stime" in valutazione and "'stima_pdf'" in valutazione
    assert "l.agency_id = c.agency_id" in valutazione and "m.agency_id = c.agency_id" in valutazione
    assert integrations._e_l_email_15_v3({"enrollment_id": 1, "template_key": "followup_v3_m15"})
    assert not integrations._e_l_email_15_v3({"enrollment_id": 1, "template_key": "followup_v3_m14"})
    assert not integrations._e_l_email_15_v3({"enrollment_id": None, "template_key": "followup_v3_m15"})


def test_07_scheda_sequenze_in_sola_lettura():
    js = (ROOT / "static" / "os_shell" / "assets" / "views" / "automazioni.js").read_text(encoding="utf-8")
    assert "{ key: 'sequenze', label: 'Sequenze email' }" in js
    assert "apiGet('/api/communication/journeys/overview')" in js
    blocco = js[js.index("function renderSequenzeTab"):js.index("export async function renderAutomazioni")]
    assert "apiPost" not in blocco and "<button" not in blocco
    esito = subprocess.run(["node", "--check", str(ROOT / "static/os_shell/assets/views/automazioni.js")],
                           capture_output=True, text=True)
    assert esito.returncode == 0, esito.stderr
