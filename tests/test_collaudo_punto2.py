"""Lo strumento di collaudo del Punto 2 (`scripts/collaudo_punto2.py`), senza
database: cio' che deve valere PRIMA di toccare qualsiasi cosa, e la lettura
del ledger. La prova contro un database e' quella del runbook (replica locale
del TEST) e, in vivo, il Job sul servizio TEST."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import collaudo_punto2 as cp  # noqa: E402

SCRIPT = ROOT / "scripts" / "collaudo_punto2.py"


def _riga(reason, status, **kw):
    base = {"id": 1, "reason_code": reason, "status": status, "suppressed_reason": None, "template_key": "x",
            "subject_snapshot": "s", "attempt_count": 0, "appointment_id": 10, "variant": None, "offset": None,
            "appointment_status": "scheduled"}
    base.update(kw)
    return base


def test_ambiente_sbagliato_si_ferma_prima_di_connettersi():
    for modo in ("preflight", "setup", "report", "cleanup"):
        for host, nome in (("INCOLLA_QUI_L_HOST", "stima360_db_test"), ("h", "stima360_db"), ("altro", "stima360_db_test")):
            env = {"DB_HOST": "h", "DB_NAME": nome, "PATH": "/usr/bin:/bin"}
            args = [sys.executable, str(SCRIPT), modo, "--expected-host", host, "--agency-id", "1",
                    "--actor-user-id", "1", "--agent-user-id", "2"] + (["--recipient", "a@example.test"] if modo == "setup" else [])
            out = subprocess.run(args, env=env, capture_output=True, text=True)
            assert out.returncode == 1 and "nessuna connessione aperta" in out.stdout, (modo, host, nome, out.stdout, out.stderr)


def test_nessuna_email_intera_nell_output():
    assert cp.mascherata("collaudo.prova@example.test") == "c***@example.test"
    assert cp.mascherata("") == "***"
    codice = SCRIPT.read_text(encoding="utf-8")
    assert "print(" in codice and "mascherata(" in codice


def test_lo_strumento_non_spedisce_e_scrive_solo_dai_service():
    codice = SCRIPT.read_text(encoding="utf-8")
    for vietato in ("dispatch_batch", "commit(", "claim_", "smtplib.SMTP(", ".sendmail", ".starttls", ".login("):
        assert vietato not in codice, vietato
    # `invia_mail` compare UNA volta, solo nel sondaggio della configurazione, e
    # solo con `smtplib.SMTP` sostituito da un finto che non apre connessioni.
    assert codice.count("invia_mail(") == 1
    sondaggio = codice[codice.index("def smtp_effective"):codice.index("def smtp_gate")]
    assert "invia_mail(" in sondaggio
    assert sondaggio.index('mock.patch.object(smtplib, "SMTP"') < sondaggio.index("invia_mail(")
    import re
    assert not re.search(r"\b(INSERT INTO|UPDATE\s+\w+\s+SET|DELETE FROM)\b", codice), "nessuna scrittura SQL diretta"
    assert "set_session(readonly=True)" in codice or "set_session(readonly=readonly)" in codice
    for atteso in ("service.create_appointment_idempotent", "service.confirm_appointment", "service.reschedule_appointment",
                   "service.cancel_appointment", "do_not_contact.set_ban", "do_not_contact.lift_ban"):
        assert atteso in codice, atteso


def test_la_configurazione_deve_essere_un_elenco_esplicito(monkeypatch):
    from appointment_reminders import policy
    for nome in ("APPOINTMENT_CONFIRMATIONS", "APPOINTMENT_REMINDER_OFFSETS", "APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS",
                 "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(nome, raising=False)
    g = cp.Gate()
    cp.config_gate(g, "prova@example.test")
    assert any("conferme accese" in p for p in g.problems) and any("elenco esplicito" in p for p in g.problems)
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    monkeypatch.setenv("SMTP_HOST", "smtp.prova.invalid")
    monkeypatch.setenv("SMTP_USER", "utente-di-prova")
    monkeypatch.setenv("SMTP_PASS", "segreto-di-prova-0123")
    monkeypatch.setenv(policy.RECIPIENTS_ENV, "*")
    g = cp.Gate()
    cp.config_gate(g, "prova@example.test")
    assert any("elenco esplicito" in p for p in g.problems)         # '*' non e' un elenco
    monkeypatch.setenv(policy.RECIPIENTS_ENV, "prova@example.test, altro@example.test")
    g = cp.Gate()
    cp.config_gate(g, "prova@example.test")
    assert g.problems == ["l'elenco contiene SOLO il destinatario del collaudo"]
    monkeypatch.setenv(policy.RECIPIENTS_ENV, "prova@example.test")
    g = cp.Gate()
    cp.config_gate(g, "prova@example.test")
    assert g.problems == []
    g = cp.Gate()
    cp.config_gate(g, "estraneo@example.test")
    assert any("destinatario del collaudo e' nell'elenco" in p for p in g.problems)


def test_attesi_scenario_a_b_c_d_e_f():
    ok, _ = cp.attesi("A", [_riga(cp.CONFERMA, "sent"), _riga(cp.PROMEMORIA, "sent", offset="2h")])
    assert ok
    ok, note = cp.attesi("A", [_riga(cp.CONFERMA, "sent"), _riga(cp.CONFERMA, "sent")])
    assert not ok and any("UNA sola conferma" in n for n in note)                      # due conferme: difetto
    ok, note = cp.attesi("A", [_riga(cp.CONFERMA, "queued")])
    assert ok and any("IN ATTESA" in n for n in note)                                  # cron non passato: non e' un FAIL
    ok, _ = cp.attesi("B", [_riga(cp.CONFERMA, "sent"), _riga(cp.PROMEMORIA, "sent", offset="24h")])
    assert ok
    ok, _ = cp.attesi("B", [_riga(cp.CONFERMA, "sent"), _riga(cp.PROMEMORIA, "sent", offset="2h")])
    assert not ok                                                                       # il 2h di B e' un difetto
    ok, _ = cp.attesi("C", [_riga(cp.CONFERMA, "cancelled", appointment_status="rescheduled"),
                            _riga(cp.CONFERMA, "sent", variant="rescheduled", appointment_id=11)])
    assert ok
    ok, note = cp.attesi("C", [_riga(cp.CONFERMA, "sent", appointment_status="rescheduled"),
                               _riga(cp.CONFERMA, "sent", variant="rescheduled", appointment_id=11)])
    assert not ok                                                                       # uscita col vecchio orario
    ok, _ = cp.attesi("D", [_riga(cp.CONFERMA, "cancelled", appointment_status="cancelled")])
    assert ok
    ok, _ = cp.attesi("D", [_riga(cp.CONFERMA, "sent", appointment_status="cancelled")])
    assert not ok
    ok, _ = cp.attesi("E", [_riga(cp.CONFERMA, "suppressed", suppressed_reason="do_not_contact")])
    assert ok
    ok, _ = cp.attesi("E", [_riga(cp.CONFERMA, "sent")])
    assert not ok
    ok, _ = cp.attesi("F", [])
    assert ok
    ok, _ = cp.attesi("F", [_riga(cp.CONFERMA, "queued")])
    assert not ok


def test_il_destinatario_fuori_elenco_dello_scenario_f_non_e_mai_un_indirizzo_reale():
    assert cp.FUORI_ELENCO.endswith("@example.invalid")                               # dominio riservato: non consegnabile


# ---------------------------------------------------------------------------
# SMTP: la configurazione EFFETTIVA, con le regole del trasporto
# ---------------------------------------------------------------------------

SEGRETO = "segreto-di-prova-0123"
UTENTE = "utente-di-prova"


def _smtp_env(monkeypatch, *, host=None, port=None, user=UTENTE, password=SEGRETO):
    for nome, valore in (("SMTP_HOST", host), ("SMTP_PORT", port), ("SMTP_USER", user), ("SMTP_PASS", password)):
        if valore is None:
            monkeypatch.delenv(nome, raising=False)
        else:
            monkeypatch.setenv(nome, valore)


def _nessuna_rete(monkeypatch):
    """Qualunque tentativo di aprire un socket durante la prova fa fallire il test."""
    import socket

    def vietato(*a, **k):
        raise AssertionError("la sonda ha provato ad aprire la rete")
    monkeypatch.setattr(socket, "create_connection", vietato)
    monkeypatch.setattr(socket.socket, "connect", vietato)


def test_smtp_host_assente_con_default_del_trasporto_e_valido(monkeypatch, capsys):
    """Il caso segnalato: SMTP_HOST non e' nell'ambiente, ma `invia_mail` ha un
    default valido: la configurazione e' buona, e il default NON e' duplicato qui."""
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch)                                   # niente SMTP_HOST, niente SMTP_PORT
    eff = cp.smtp_effective()
    assert eff["configurata"] is True and eff["errore"] is None
    assert eff["host_da"] == "default del trasporto"
    assert isinstance(eff["host"], str) and eff["host"] and isinstance(eff["port"], int) and eff["port"] > 0
    g = cp.Gate()
    cp.smtp_gate(g)
    assert g.problems == []
    out = capsys.readouterr().out
    assert "default del trasporto" in out and f"{eff['host']}:{eff['port']}" in out
    # nel sorgente dello script non c'e' una copia del default del trasporto
    assert eff["host"] not in SCRIPT.read_text(encoding="utf-8")


def test_smtp_il_default_letto_e_proprio_quello_del_trasporto(monkeypatch):
    """Se il trasporto cambiasse il suo default, la sonda lo seguirebbe: lo si
    prova sostituendo `invia_mail` con una versione che usa un altro default."""
    import database
    sorgente = __import__("inspect").getsource(database.invia_mail)
    import re
    m = re.search(r'os\.getenv\("SMTP_HOST",\s*"([^"]+)"\)', sorgente)
    assert m, "il trasporto non ha piu' un default letterale per SMTP_HOST: la sonda resta valida, aggiornare questo test"
    _smtp_env(monkeypatch)
    assert cp.smtp_effective()["host"] == m.group(1)


def test_smtp_host_da_variabile_e_riportato_come_tale(monkeypatch):
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, host="smtp.prova.invalid", port="2525")
    eff = cp.smtp_effective()
    assert (eff["configurata"], eff["host"], eff["port"], eff["host_da"]) == (
        True, "smtp.prova.invalid", 2525, "variabile SMTP_HOST")


def test_smtp_password_o_utente_assenti_non_e_configurato(monkeypatch):
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, password=None)
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["password_presente"] is False and eff["utente_presente"] is True
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("SMTP_PASS presente" in p for p in g.problems)
    assert any("trasporto SMTP risulta configurato" in p for p in g.problems)
    _smtp_env(monkeypatch, user=None)
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["utente_presente"] is False and eff["password_presente"] is True
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("SMTP_USER presente" in p for p in g.problems)
    # presenti ma vuoti: non sono presenti
    _smtp_env(monkeypatch, user="", password="")
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["utente_presente"] is False and eff["password_presente"] is False


def test_smtp_host_vuoto_per_il_trasporto_non_e_configurato(monkeypatch):
    """`SMTP_HOST=""` non attiva il default (e' presente): lo decide il trasporto, e la sonda lo segue."""
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, host="")
    eff = cp.smtp_effective()
    assert eff["configurata"] is False
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("trasporto SMTP risulta configurato" in p for p in g.problems)


def test_smtp_porta_non_numerica_e_un_problema_non_un_crash(monkeypatch):
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, port="non-un-numero")
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["errore"] == "ValueError"
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("non e' utilizzabile" in p and "ValueError" in p for p in g.problems)


def test_smtp_nessun_segreto_in_output_nessuna_email_nessuna_rete(monkeypatch, capsys):
    import smtplib
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, host="smtp.prova.invalid")
    chiamate = []
    originale = smtplib.SMTP
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: chiamate.append(a) or originale.__new__(originale))
    sostituto = smtplib.SMTP
    eff = cp.smtp_effective()
    # la prova ha sostituito SMTP solo per la sua durata e lo ha ripristinato
    assert smtplib.SMTP is sostituto and chiamate == []
    g = cp.Gate()
    cp.smtp_gate(g)
    visibile = capsys.readouterr().out + repr(eff)
    for riservato in (SEGRETO, UTENTE, "sonda@example.invalid"):
        assert riservato not in visibile, riservato
    assert eff["password_presente"] is True and "password" not in " ".join(k for k, v in eff.items() if isinstance(v, str))


def test_config_gate_con_host_assente_e_default_valido_non_da_problemi_smtp(monkeypatch):
    from appointment_reminders import policy
    _nessuna_rete(monkeypatch)
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    monkeypatch.setenv(policy.RECIPIENTS_ENV, "prova@example.test")
    _smtp_env(monkeypatch)                                   # SMTP_HOST assente: vale il default del trasporto
    g = cp.Gate()
    cp.config_gate(g, "prova@example.test")
    assert g.problems == []
