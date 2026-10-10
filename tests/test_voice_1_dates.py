"""STIMA Voice Fase 1 - date e orari italiani (voice/dates.py)."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest

from voice import dates
from voice.dates import ROMA, resolve_date, resolve_time, resolve_when

SABATO = datetime(2026, 10, 10, 16, 0, tzinfo=ROMA)
LUNEDI = datetime(2026, 10, 12, 9, 0, tzinfo=ROMA)


@pytest.mark.parametrize("testo, atteso", [
    ("oggi", date(2026, 10, 10)), ("domani", date(2026, 10, 11)), ("dopodomani", date(2026, 10, 12)),
    ("Domani mattina", date(2026, 10, 11)), ("giovedì", date(2026, 10, 15)), ("giovedi", date(2026, 10, 15)),
    ("lunedì prossimo", date(2026, 10, 12)), ("prossimo lunedì", date(2026, 10, 12)),
    ("sabato", date(2026, 10, 17)),            # oggi e' sabato: la PROSSIMA occorrenza
    ("fra 3 giorni", date(2026, 10, 13)), ("tra due settimane", date(2026, 10, 24)),
    ("il 15", date(2026, 10, 15)), ("il 5", date(2026, 11, 5)),   # il 5 ottobre e' passato
    ("15 ottobre", date(2026, 10, 15)), ("3 marzo", date(2027, 3, 3)), ("15/10", date(2026, 10, 15)),
    ("31 dicembre 2026", date(2026, 12, 31)), ("1/2/27", date(2027, 2, 1)),
])
def test_01_dates(testo, atteso):
    assert resolve_date(testo, SABATO).day == atteso


def test_02_weekday_on_the_same_weekday_means_next_week():
    assert resolve_date("lunedì", LUNEDI).day == date(2026, 10, 19)


def test_03_this_weekday_already_passed_is_ambiguous():
    r = resolve_date("questo venerdì", SABATO)
    assert r.day is None and r.reason == dates.AMBIGUOUS_WEEK
    assert resolve_date("questo mercoledì", LUNEDI).day == date(2026, 10, 14)


@pytest.mark.parametrize("testo", ["boh", "il 32", "30 febbraio", "lunedìssimo", "fra molti giorni"])
def test_04_unrecognized_dates(testo):
    assert resolve_date(testo, SABATO).reason == dates.UNRECOGNIZED_DATE


@pytest.mark.parametrize("testo, atteso", [
    ("alle 15", time(15, 0)), ("15:30", time(15, 30)), ("15.30", time(15, 30)), ("ore 9", time(9, 0)),
    ("alle 3 del pomeriggio", time(15, 0)), ("alle 9 di sera", time(21, 0)), ("alle 9 e mezza", time(9, 30)),
    ("alle 10 e un quarto", time(10, 15)), ("le dieci meno un quarto", time(9, 45)), ("alle 11 e tre quarti", time(11, 45)),
    ("mezzogiorno", time(12, 0)), ("a mezzanotte", time(0, 0)), ("alle 8 di mattina", time(8, 0)),
    ("alle dieci", time(10, 0)), ("alle 3", time(3, 0)),  # in cifre resta com'e'
])
def test_05_times(testo, atteso):
    assert resolve_time(testo).at == atteso


@pytest.mark.parametrize("testo, motivo", [
    ("alle tre", dates.AMBIGUOUS_HOUR), ("pomeriggio", dates.UNRECOGNIZED_TIME), ("di mattina", dates.UNRECOGNIZED_TIME),
    ("alle 25", dates.UNRECOGNIZED_TIME), ("alle 9 e 70", dates.UNRECOGNIZED_TIME), (None, dates.UNRECOGNIZED_TIME),
])
def test_06_ambiguous_or_unrecognized_times(testo, motivo):
    r = resolve_time(testo)
    assert r.at is None and r.reason == motivo


def test_07_when_combines_in_rome_timezone():
    w = resolve_when("domani", "alle 15", SABATO)
    assert w.ok and w.start == datetime(2026, 10, 11, 15, 0, tzinfo=ROMA)
    assert w.start.utcoffset().total_seconds() == 2 * 3600


def test_08_when_past_is_flagged_not_corrected():
    w = resolve_when("oggi", "alle 9", SABATO)
    assert w.start is None and w.reasons == (dates.PAST,)
    assert resolve_when("oggi", "alle 9", SABATO, allow_past=True).ok


def test_09_dst_gap_and_overlap_are_ambiguous():
    marzo = datetime(2026, 3, 20, 10, 0, tzinfo=ROMA)
    assert resolve_when("29 marzo", "alle 2 e mezza", marzo).reasons == (dates.NONEXISTENT_LOCAL,)
    assert resolve_when("29 marzo", "alle 10", marzo).ok
    ottobre = datetime(2026, 10, 10, 10, 0, tzinfo=ROMA)
    assert resolve_when("25 ottobre", "alle 2 e mezza", ottobre).reasons == (dates.DOUBLE_LOCAL,)
    assert resolve_when("25 ottobre", "alle 10", ottobre).start.utcoffset().total_seconds() == 3600


def test_10_task_default_time_and_missing_time():
    w = resolve_when("lunedì", None, SABATO, require_time=False, default_time=time(9, 0))
    assert w.start == datetime(2026, 10, 12, 9, 0, tzinfo=ROMA)
    w = resolve_when("lunedì", None, SABATO, require_time=True)
    assert w.start is None and dates.UNRECOGNIZED_TIME in w.reasons


def test_11_now_must_be_aware():
    with pytest.raises(ValueError):
        resolve_date("domani", datetime(2026, 10, 10, 16, 0))
