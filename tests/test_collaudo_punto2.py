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
    for vietato in ("dispatch_batch", "invia_mail", "smtplib", "commit(", "claim_"):
        assert vietato not in codice, vietato
    import re
    assert not re.search(r"\b(INSERT INTO|UPDATE\s+\w+\s+SET|DELETE FROM)\b", codice), "nessuna scrittura SQL diretta"
    assert "set_session(readonly=True)" in codice or "set_session(readonly=readonly)" in codice
    for atteso in ("service.create_appointment_idempotent", "service.confirm_appointment", "service.reschedule_appointment",
                   "service.cancel_appointment", "do_not_contact.set_ban", "do_not_contact.lift_ban"):
        assert atteso in codice, atteso


def test_la_configurazione_deve_essere_un_elenco_esplicito(monkeypatch):
    from appointment_reminders import policy
    for nome in ("APPOINTMENT_CONFIRMATIONS", "APPOINTMENT_REMINDER_OFFSETS", "APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS",
                 "SMTP_HOST", "SMTP_USER"):
        monkeypatch.delenv(nome, raising=False)
    g = cp.Gate()
    cp.config_gate(g, "prova@example.test")
    assert any("conferme accese" in p for p in g.problems) and any("elenco esplicito" in p for p in g.problems)
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    monkeypatch.setenv("SMTP_HOST", "x")
    monkeypatch.setenv("SMTP_USER", "y")
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
