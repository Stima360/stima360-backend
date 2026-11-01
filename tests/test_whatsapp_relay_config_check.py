"""Configurazione del collaudo WhatsApp: un solo mittente di prova, mai il
numero business, Phone ID come chiave di instradamento, nessun invio. E
autocontrollo dell'inoltro di produzione. Nessun valore viene stampato."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "integrations" / "whatsapp_relay"))
import check_whatsapp_relay_config as chk  # noqa: E402
import forwarder  # noqa: E402

SECRET = "segreto-sintetico-dell-inoltro-lungo-almeno-32"
SENDER = "393000000001"                      # mittente di prova sintetico
PHONE_ID = "900000000000001"                 # Phone ID sintetico
GOOD = {"WHATSAPP_RELAY_SECRET": SECRET, "WHATSAPP_INBOUND_ALLOWED_SENDERS": f"+{SENDER}",
        "WHATSAPP_INBOUND_ROUTES": f"{PHONE_ID}:2"}


def _run(env, **kw):
    lines = []
    return chk.run(env, out=lines.append, **kw), "\n".join(lines)


def test_a_safe_configuration_passes_and_prints_no_value():
    problems, text = _run(GOOD, expected_phone_number_id=PHONE_ID)
    assert problems == [] and "PRONTA" in text
    for value in (SECRET, SENDER, PHONE_ID):
        assert value not in text


@pytest.mark.parametrize("change, fragment", [
    ({"WHATSAPP_RELAY_SECRET": "corto"}, "WHATSAPP_RELAY_SECRET"),
    ({"WHATSAPP_INBOUND_ALLOWED_SENDERS": "*"}, "esattamente un mittente"),
    ({"WHATSAPP_INBOUND_ALLOWED_SENDERS": f"{SENDER},393000000002"}, "esattamente un mittente"),
    ({"WHATSAPP_INBOUND_ALLOWED_SENDERS": ""}, "esattamente un mittente"),
    ({"WHATSAPP_INBOUND_ALLOWED_SENDERS": "+39 329 567 4589"}, "NON e' il numero business"),
    ({"WHATSAPP_INBOUND_ROUTES": "393295674589:2"}, "non il numero di telefono business"),
    ({"WHATSAPP_INBOUND_ROUTES": "3295674589:2"}, "non il numero di telefono business"),
    ({"WHATSAPP_INBOUND_ROUTES": f"{SENDER}:2"}, "non il numero di telefono business"),
    ({"WHATSAPP_INBOUND_ROUTES": f"{PHONE_ID}:2,900000000000002:3"}, "esattamente una voce"),
    ({"WHATSAPP_INBOUND_ROUTES": f"{PHONE_ID}:alfa"}, "formato"),
    ({"WHATSAPP_OUTBOUND_MODE": "meta"}, "risposta manuale"),
    ({"WHATSAPP_AUTO_REPLY_MODE": "send"}, "risposte automatiche"),
    ({"COMMUNICATION_AUTO_REPLY_TICK": "1"}, "giro"),
    ({"WHATSAPP_SERVICE_URL": "https://servizio.example/send"}, "template della stima"),
])
def test_each_unsafe_or_confused_setting_stops(change, fragment):
    problems, _ = _run({**GOOD, **change})
    assert any(fragment in p for p in problems), problems


def test_a_different_phone_id_than_the_confirmed_one_stops():
    problems, _ = _run(GOOD, expected_phone_number_id="900000000000009")
    assert problems == ["Phone ID uguale a quello confermato in Meta"]


def test_the_script_exits_1_on_an_empty_environment_and_reads_nothing_else():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_whatsapp_relay_config.py")],
                         env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert out.returncode == 1 and "STOP" in out.stdout and "ESITO" in out.stdout


# --- autocontrollo dell'inoltro di produzione ---------------------------------

def _self(env):
    lines = []
    return forwarder.self_check(env, out=lines.append), "\n".join(lines)


def test_production_self_check_off_by_default():
    code, text = _self({})
    assert code == 0 and "SPENTO" in text


def test_production_self_check_with_one_test_sender():
    env = {forwarder.URL_ENV: "https://stima360-backend-test.onrender.com/webhook/whatsapp",
           forwarder.SECRET_ENV: SECRET, forwarder.SENDERS_ENV: SENDER}
    code, text = _self(env)
    assert code == 0 and "ACCESO" in text and "1 mittente" in text
    assert SECRET not in text and SENDER not in text


@pytest.mark.parametrize("senders", ["*", "393295674589", f"{SENDER},393000000002"])
def test_production_self_check_refuses_unsafe_test_relays(senders):
    env = {forwarder.URL_ENV: "https://stima360-backend-test.onrender.com/webhook/whatsapp",
           forwarder.SECRET_ENV: SECRET, forwarder.SENDERS_ENV: senders}
    assert _self(env)[0] == 1


def test_production_self_check_flags_a_half_configuration():
    code, text = _self({forwarder.URL_ENV: "https://stima360-backend-test.onrender.com/webhook/whatsapp"})
    assert code == 1 and "in parte" in text
