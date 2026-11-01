"""I LINK PUBBLICI (conferma del consenso, disiscrizione, guida) devono
puntare a QUESTO servizio. Se `PUBLIC_BASE_URL` nomina un ALTRO servizio
Render (un host `*.onrender.com` diverso da `RENDER_EXTERNAL_HOSTNAME`, che
Render imposta per il servizio in esecuzione), i link non si costruiscono:
meglio nessuna email che una conferma o una disiscrizione che non arriva qui.
Un dominio proprio (non onrender) o l'assenza di `RENDER_EXTERNAL_HOSTNAME`
(ambiente locale) non si giudicano.
"""
from __future__ import annotations

import pytest


def test_01_un_altro_servizio_render_non_e_ammesso(monkeypatch):
    from communication import journey_tick
    from communication.exceptions import ValidationError
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "stima360-backend-test.onrender.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stima360-backend.onrender.com")
    with pytest.raises(ValidationError, match="altro servizio"):
        journey_tick._base_pubblica()


def test_02_lo_stesso_servizio_o_un_dominio_proprio_passano(monkeypatch):
    from communication import journey_tick
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "stima360-backend-test.onrender.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stima360-backend-test.onrender.com/")
    assert journey_tick._base_pubblica() == "https://stima360-backend-test.onrender.com"
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://crm-test.stima360.it")
    assert journey_tick._base_pubblica() == "https://crm-test.stima360.it"
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://stima360-backend.onrender.com")
    assert journey_tick._base_pubblica() == "https://stima360-backend.onrender.com"
