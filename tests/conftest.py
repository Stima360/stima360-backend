"""Shared pytest bootstrap for the real project packages.

Historically this file injected synthetic ``core`` modules into ``sys.modules``.
That made OWNER-only tests easier to isolate, but it broke full-suite collection
because the synthetic package hid the real ``core`` package.  The repository
root is all that is needed now; individual tests must monkeypatch the concrete
objects they use.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
root = str(REPOSITORY_ROOT)
if root not in sys.path:
    sys.path.insert(0, root)

# Developer-only fallback: collection and isolated unit tests remain runnable
# even when psycopg2-binary is not installed. Render/production always use the
# real driver because this branch is entered only after ImportError.
try:
    import psycopg2  # noqa: F401
except ImportError:
    import types

    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.connect = lambda *args, **kwargs: None
    psycopg2.errors = types.SimpleNamespace(
        UniqueViolation=type("UniqueViolation", (Exception,), {})
    )

    extras = types.ModuleType("psycopg2.extras")
    extras.Json = lambda value: value
    extras.RealDictCursor = object

    sql = types.ModuleType("psycopg2.sql")

    class _Composable:
        def __init__(self, value=""):
            self.value = value

        def format(self, *args, **kwargs):
            return self

        def join(self, seq):
            return self

        def as_string(self, context=None):
            return str(self.value)

    sql.SQL = _Composable
    sql.Identifier = _Composable
    sql.Literal = _Composable

    psycopg2.extras = extras
    psycopg2.sql = sql
    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.extras"] = extras
    sys.modules["psycopg2.sql"] = sql


# STIMA VOICE V8-0: il freno per operatore (6 interpretazioni al minuto, in memoria)
# conta il tempo reale; le suite Voice inviano molti comandi in pochi secondi con
# lo stesso operatore. Lo si allenta SOLO per quelle suite e lo si azzera a ogni
# test; la suite che prova il freno (`test_voice_8_0_general`) lo lascia com'e'.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _voice_operator_brake(request, monkeypatch):
    nome = request.module.__name__.rsplit(".", 1)[-1]
    if not nome.startswith("test_voice_"):
        yield
        return
    from voice import service as voice_service
    voice_service._per_operatore.clear()
    voice_service._interpretazioni.clear()
    voice_service._fornitore_occupato_fino[0] = None       # la pausa dopo un 429 del fornitore
    if nome != "test_voice_8_0_general":
        monkeypatch.setattr(voice_service, "OPERATOR_PER_MINUTE", 10 ** 6)
    yield
