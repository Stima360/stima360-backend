"""Actual email primitive under synthetic SMTP; no network or database used.

For the recorded RED run, STIMA360_SMTP_BASELINE_SOURCE selects only the AST
of invia_mail from the preserved F07 source, executed with these same fakes.
The normal test run executes the current database.invia_mail directly.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

import database
from tests.test_public_submission_delivery import MemoryReceipt


@pytest.fixture
def smtp(monkeypatch):
    events = {"constructors": [], "accepted": [], "lose_response": False}
    for key, value in {
        "SMTP_HOST": "synthetic.smtp.invalid", "SMTP_PORT": "587",
        "SMTP_USER": "synthetic@example.invalid", "SMTP_PASS": "synthetic-local-only",
    }.items():
        monkeypatch.setenv(key, value)

    class SyntheticSMTP:
        def __init__(self, host, port, timeout=None):
            events["constructors"].append({"host": host, "port": port, "timeout": timeout})

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            assert user == "synthetic@example.invalid"
            assert password == "synthetic-local-only"

        def sendmail(self, sender, destination, message):
            events["accepted"].append({"sender": sender, "destination": destination})
            if events["lose_response"]:
                raise TimeoutError("synthetic timeout after SMTP acceptance")

        def quit(self):
            pass

    monkeypatch.setattr(database.smtplib, "SMTP", SyntheticSMTP)
    return events


def sender():
    baseline = os.getenv("STIMA360_SMTP_BASELINE_SOURCE")
    if not baseline:
        return database.invia_mail
    tree = ast.parse(Path(baseline).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "invia_mail")
    module = ast.Module(body=[function], type_ignores=[])
    namespace = dict(database.__dict__)
    exec(compile(module, baseline, "exec"), namespace)
    return namespace["invia_mail"]


def test_actual_email_primitive_bounds_smtp_wait_and_preserves_success(smtp):
    assert sender()("recipient@example.invalid", "Sintetico", "<p>Prova sintetica</p>") is True
    assert smtp["constructors"] == [{"host": "synthetic.smtp.invalid", "port": 587, "timeout": 20}]
    assert len(smtp["accepted"]) == 1


def test_smtp_timeout_after_acceptance_stays_indeterminate_without_resend(smtp, monkeypatch):
    from communication import dispatcher
    from communication.providers import email_smtp

    smtp["lose_response"] = True
    receipt = MemoryReceipt()
    observed = []
    actual_send = sender()

    def send():
        accepted = actual_send("recipient@example.invalid", "Sintetico", "<p>Prova sintetica</p>")
        observed.append(accepted)
        return accepted

    receipt.direct_once("admin_email", send, preflight=lambda: True)
    assert observed == [False]
    assert smtp["constructors"][0]["timeout"] == 20
    assert receipt.row["checkpoints"]["admin_email"]["state"] == "indeterminate"
    receipt.direct_once("admin_email", lambda: pytest.fail("uncertain SMTP was retried"), preflight=lambda: True)
    assert len(smtp["accepted"]) == 1

    # Preserve the P29 interpretation of the primitive's observed False.
    monkeypatch.setattr(email_smtp, "invia_mail", lambda *args, **kwargs: observed[0])
    outcome = email_smtp.send({"destination_snapshot": "recipient@example.invalid",
                               "subject_snapshot": "Sintetico", "rendered_body": "<p>Sintetico</p>"})
    assert dispatcher._esito_del_provider(outcome, email_smtp.CAPABILITIES)[0] == "indeterminate"
    assert email_smtp.CAPABILITIES.distinguishes_failure_class is False
