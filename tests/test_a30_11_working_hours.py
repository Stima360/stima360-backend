"""A30-11 - il domain puro degli orari di lavoro/eccezioni/chiusure: nessun
database, nessun FastAPI. Stesso spirito di `test_a30_2_appointments.py`
(sezione A) per `appointments/availability.py`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from appointments import working_hours as wh

ROMA = ZoneInfo("Europe/Rome")


def _t(anno, mese, giorno, ora, minuto=0):
    return datetime(anno, mese, giorno, ora, minuto, tzinfo=ROMA).astimezone(timezone.utc)


def _weekly(day_of_week, start_minute, end_minute):
    return {"day_of_week": day_of_week, "start_minute": start_minute, "end_minute": end_minute}


def _exc(giorno: date, start_minute, end_minute, is_available):
    return {"exception_date": giorno, "start_minute": start_minute, "end_minute": end_minute,
            "is_available": is_available}


def _closure(giorno: date, start_minute, end_minute):
    return {"closure_date": giorno, "start_minute": start_minute, "end_minute": end_minute}


# ---------------------------------------------------------------------------
# A - LEGACY (D1)
# ---------------------------------------------------------------------------

def test_01_legacy_nessuna_configurazione_nessun_vincolo():
    """Un agente senza righe settimanali: `effective_windows` e' `None`, e
    `is_within` e' sempre vero, qualunque orario - la stessa cosa che
    `test_12_nessun_orario_lavorativo_d7` prova per `availability.py`."""
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=False,
        weekly_rows=[], exception_rows=[], closure_rows=[
            _closure(date(2026, 10, 5), 0, 1440)])  # anche una chiusura non conta
    assert finestra is None
    assert wh.is_within(_t(2026, 10, 5, 23), _t(2026, 10, 6, 1), finestra)
    assert wh.has_weekly_configuration([]) is False
    assert wh.has_weekly_configuration([_weekly(1, 540, 780)]) is True


# ---------------------------------------------------------------------------
# B - ORARIO SETTIMANALE
# ---------------------------------------------------------------------------

def test_02_fascia_singola():
    # Lunedi' 5/10/2026, 9:00-13:00 (540-780 minuti).
    settimanale = [_weekly(1, 540, 780)]
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=[], closure_rows=[])
    assert finestra == [(_t(2026, 10, 5, 9), _t(2026, 10, 5, 13))]
    assert wh.is_within(_t(2026, 10, 5, 9), _t(2026, 10, 5, 10), finestra)
    assert wh.is_within(_t(2026, 10, 5, 9), _t(2026, 10, 5, 13), finestra)
    assert not wh.is_within(_t(2026, 10, 5, 12), _t(2026, 10, 5, 14), finestra)
    assert not wh.is_within(_t(2026, 10, 5, 8), _t(2026, 10, 5, 9, 1), finestra)


def test_03_due_fasce_stesso_giorno_pausa_pranzo():
    # 9-13 e 14-18: la pausa (13-14) non e' effective availability.
    settimanale = [_weekly(1, 540, 780), _weekly(1, 840, 1080)]
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=[], closure_rows=[])
    assert finestra == [(_t(2026, 10, 5, 9), _t(2026, 10, 5, 13)),
                        (_t(2026, 10, 5, 14), _t(2026, 10, 5, 18))]
    assert not wh.is_within(_t(2026, 10, 5, 13), _t(2026, 10, 5, 13, 30), finestra)
    assert wh.is_within(_t(2026, 10, 5, 9), _t(2026, 10, 5, 13), finestra)
    assert wh.is_within(_t(2026, 10, 5, 14), _t(2026, 10, 5, 18), finestra)


def test_04_giorno_senza_fascia():
    # Solo lunedi' configurato: martedi' 6/10/2026 non ha alcuna finestra.
    settimanale = [_weekly(1, 540, 780)]
    finestra = wh.effective_windows(
        _t(2026, 10, 6, 0), _t(2026, 10, 7, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=[], closure_rows=[])
    assert finestra == []
    assert not wh.is_within(_t(2026, 10, 6, 10), _t(2026, 10, 6, 11), finestra)


def test_05_adiacenti_non_si_sovrappongono_e_si_fondono():
    # 9-13 e 13-18 adiacenti: si fondono in un solo intervallo continuo.
    settimanale = [_weekly(1, 540, 780), _weekly(1, 780, 1080)]
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=[], closure_rows=[])
    assert finestra == [(_t(2026, 10, 5, 9), _t(2026, 10, 5, 18))]


def test_06_intervallo_semiaperto_ai_bordi():
    settimanale = [_weekly(1, 540, 780)]  # 9:00-13:00
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=[], closure_rows=[])
    # [9,13): le 13:00 esatte non sono piu' dentro.
    assert wh.is_within(_t(2026, 10, 5, 12, 59), _t(2026, 10, 5, 13), finestra)
    assert not wh.is_within(_t(2026, 10, 5, 12, 59), _t(2026, 10, 5, 13, 1), finestra)


# ---------------------------------------------------------------------------
# C - ECCEZIONI (D4: positive/negative, precedenza)
# ---------------------------------------------------------------------------

def test_07_ferie_full_day_toglie_tutto_il_giorno():
    settimanale = [_weekly(1, 540, 1080)]  # lunedi' 9-18
    eccezioni = [_exc(date(2026, 10, 5), 0, 1440, False)]  # ferie tutto il giorno
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=eccezioni, closure_rows=[])
    assert finestra == []


def test_08_assenza_parziale():
    settimanale = [_weekly(1, 540, 1080)]  # 9-18
    eccezioni = [_exc(date(2026, 10, 5), 720, 840, False)]  # 12:00-14:00 assente
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=eccezioni, closure_rows=[])
    assert finestra == [(_t(2026, 10, 5, 9), _t(2026, 10, 5, 12)),
                        (_t(2026, 10, 5, 14), _t(2026, 10, 5, 18))]


def test_09_apertura_straordinaria_giorno_senza_fascia():
    # Sabato (6) normalmente senza fascia: un'apertura straordinaria lo apre.
    eccezioni = [_exc(date(2026, 10, 10), 600, 720, True)]  # sabato 10:00-12:00
    finestra = wh.effective_windows(
        _t(2026, 10, 10, 0), _t(2026, 10, 11, 0), has_weekly_config=True,
        weekly_rows=[], exception_rows=eccezioni, closure_rows=[])
    assert finestra == [(_t(2026, 10, 10, 10), _t(2026, 10, 10, 12))]


def test_10_negative_precede_positive_sulla_stessa_fascia():
    # Un'apertura straordinaria E un'assenza sulla stessa fascia: l'assenza
    # (negative) vince sempre (D4: "le chiusure prevalgono sempre" - e le
    # assenze sono sottratte prima delle chiusure, ma dopo l'unione con le
    # positive: quindi negative > positive in ogni caso).
    eccezioni = [_exc(date(2026, 10, 10), 600, 720, True),
                _exc(date(2026, 10, 10), 600, 720, False)]
    finestra = wh.effective_windows(
        _t(2026, 10, 10, 0), _t(2026, 10, 11, 0), has_weekly_config=True,
        weekly_rows=[], exception_rows=eccezioni, closure_rows=[])
    assert finestra == []


# ---------------------------------------------------------------------------
# D - CHIUSURE AGENZIA (D5: prevalgono sempre)
# ---------------------------------------------------------------------------

def test_11_chiusura_agenzia_prevale_su_apertura_straordinaria():
    eccezioni = [_exc(date(2026, 12, 25), 600, 720, True)]  # apertura straordinaria natale
    chiusure = [_closure(date(2026, 12, 25), 0, 1440)]      # ma l'agenzia e' chiusa
    finestra = wh.effective_windows(
        _t(2026, 12, 25, 0), _t(2026, 12, 26, 0), has_weekly_config=True,
        weekly_rows=[], exception_rows=eccezioni, closure_rows=chiusure)
    assert finestra == []


def test_12_chiusura_agenzia_parziale():
    settimanale = [_weekly(1, 540, 1080)]  # lunedi' 9-18 (5/10/2026 e' lunedi')
    chiusure = [_closure(date(2026, 10, 5), 1020, 1080)]  # chiusura 17-18
    finestra = wh.effective_windows(
        _t(2026, 10, 5, 0), _t(2026, 10, 6, 0), has_weekly_config=True,
        weekly_rows=settimanale, exception_rows=[], closure_rows=chiusure)
    assert finestra == [(_t(2026, 10, 5, 9), _t(2026, 10, 5, 17))]


# ---------------------------------------------------------------------------
# E - DST (D7: marzo/ottobre, Europe/Rome)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("giorno", [date(2026, 3, 29), date(2026, 10, 25)])
def test_13_dst_orario_settimanale_resta_coerente(giorno):
    """Nei due giorni di cambio d'ora una fascia 9:00-18:00 locale resta
    9:00-18:00 in ORARIO LOCALE (l'ampiezza assoluta in UTC cambia di
    un'ora, ma il confronto locale - fatto in minuti, non in timedelta - non
    se ne accorge): niente naive datetime, nessuna sfasatura."""
    dow = giorno.isoweekday()
    settimanale = [_weekly(dow, 540, 1080)]
    finestra = wh.effective_windows(
        datetime(giorno.year, giorno.month, giorno.day, 0, tzinfo=ROMA).astimezone(timezone.utc),
        datetime(giorno.year, giorno.month, giorno.day, 23, 59, tzinfo=ROMA).astimezone(timezone.utc)
        + timedelta(minutes=1),
        has_weekly_config=True, weekly_rows=settimanale, exception_rows=[], closure_rows=[])
    assert len(finestra) == 1
    s, e = finestra[0]
    assert s.astimezone(ROMA).strftime("%H:%M") == "09:00"
    assert e.astimezone(ROMA).strftime("%H:%M") == "18:00"
    assert s.tzinfo is not None and e.tzinfo is not None


def test_14_finestra_non_valida():
    with pytest.raises(ValueError):
        wh.effective_windows(_t(2026, 10, 5, 10), _t(2026, 10, 5, 9),
                             has_weekly_config=True, weekly_rows=[], exception_rows=[],
                             closure_rows=[])
