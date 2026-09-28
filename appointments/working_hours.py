"""A30-11 - la disponibilita' EFFETTIVA di un agente: funzioni PURE su
intervalli, niente database, niente FastAPI, niente Google (D8).

VINCOLO SOFT (D2), NON UNA QUARTA PROTEZIONE

Questo modulo non tocca e non duplica `appointments/availability.py`: quello
resta l'unica verita' su "occupato da un appuntamento" (busy). Questo modulo
risponde a una domanda diversa - "l'agente lavora in quel momento?" - il cui
risultato alimenta `availability_check`/`alternatives` nel service (lettura,
avviso), MAI il percorso di scrittura (`create`/`schedule`/`reschedule`
restano consentiti fuori orario, D2).

LEGACY (D1)

`effective_windows` riceve `has_weekly_config`: se e' `False` (nessuna riga
in `agent_working_hours` per quell'agente, in nessun giorno) restituisce
`None`, che significa "nessun vincolo", esattamente il comportamento D7 di
prima di A30-11. Un agente senza configurazione ignora anche le chiusure
agenzia: la funzione non applica affatto il meccanismo finche' nessuno lo ha
configurato per lui. Questo e' cio' che rende A30-11 additiva: un agente non
toccato oggi resta esattamente come e' oggi.

PRECEDENZA (D4)

    effective = (orario settimanale UNITO alle aperture straordinarie)
                MENO le assenze
                MENO le chiusure agenzia

Le chiusure vincono sempre: sono sottratte per ultime, dopo le assenze.

IL CAMBIO D'ORA

Stesso principio di `availability.py` e di `communication/send_window.py`:
il confronto/l'unione avviene in MINUTI LOCALI dentro un giorno di
calendario di Roma, mai su un `timedelta` assoluto attraverso la mezzanotte;
l'avanzamento e' per giorno di calendario locale (`giorno + timedelta(days=1)`
sulla `date`, mai sull'istante), e la conversione minuto-locale -> istante
UTC usa `datetime.combine(giorno, ora, tzinfo=ROMA).astimezone(utc)` con
`fold` di default (0): l'ora doppia di ottobre risolve alla PRIMA occorrenza,
come in `send_window`.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")

#: Minuti in un giorno. Un `end_minute` di 1440 e' "fino a mezzanotte
#: compresa" (semiaperto: il minuto 1440 stesso non e' incluso).
MINUTI_GIORNO = 24 * 60


def _istante_locale(giorno: date, minuto: int, zona: ZoneInfo = ROMA) -> datetime:
    """Il minuto `minuto` (0..1440, puo' scavalcare in `giorno + 1` solo se
    == 1440) del giorno locale `giorno`, come istante UTC."""
    ore, resto = divmod(int(minuto), 60)
    giorno_reale = giorno
    if ore >= 24:
        giorno_reale = giorno + timedelta(days=ore // 24)
        ore = ore % 24
    return datetime.combine(giorno_reale, time(ore, resto), tzinfo=zona).astimezone(timezone.utc)


def _unisci_minuti(intervalli):
    """Unione di intervalli semiaperti [start,end) in minuti, ordinata e
    senza sovrapposizioni residue. Adiacenti (10-13 e 13-18) si fondono."""
    ordinati = sorted((int(s), int(e)) for s, e in intervalli if e > s)
    if not ordinati:
        return []
    esito = [list(ordinati[0])]
    for s, e in ordinati[1:]:
        if s <= esito[-1][1]:
            esito[-1][1] = max(esito[-1][1], e)
        else:
            esito.append([s, e])
    return [(s, e) for s, e in esito]


def _sottrai_minuti(base, da_togliere):
    """`base` (unito) MENO `da_togliere` (unito), entrambi in minuti."""
    if not da_togliere:
        return list(base)
    togli = _unisci_minuti(da_togliere)
    esito = []
    for s, e in base:
        cursore = s
        for ts, te in togli:
            if te <= cursore or ts >= e:
                continue
            if ts > cursore:
                esito.append((cursore, min(ts, e)))
            cursore = max(cursore, te)
            if cursore >= e:
                break
        if cursore < e:
            esito.append((cursore, e))
    return esito


def _minuti_giorno(giorno: date, weekly_rows, exception_rows, closure_rows):
    """Gli intervalli in minuti EFFETTIVI (D4) del singolo giorno locale
    `giorno`. Nessuna conoscenza di UTC qui: solo aritmetica sui minuti."""
    dow = giorno.isoweekday()
    settimanale = _unisci_minuti(
        (r["start_minute"], r["end_minute"]) for r in weekly_rows if r["day_of_week"] == dow)
    positive = _unisci_minuti(
        (r["start_minute"], r["end_minute"]) for r in exception_rows
        if r["exception_date"] == giorno and r["is_available"])
    negative = [
        (r["start_minute"], r["end_minute"]) for r in exception_rows
        if r["exception_date"] == giorno and not r["is_available"]]
    chiusure = [
        (r["start_minute"], r["end_minute"]) for r in closure_rows
        if r["closure_date"] == giorno]

    disponibile = _unisci_minuti(settimanale + positive)
    disponibile = _sottrai_minuti(disponibile, negative)
    disponibile = _sottrai_minuti(disponibile, chiusure)
    return disponibile


def _unisci_istanti(intervalli):
    """Come `_unisci_minuti`, ma su coppie di `datetime` UTC."""
    ordinati = sorted((s, e) for s, e in intervalli if e > s)
    if not ordinati:
        return []
    esito = [list(ordinati[0])]
    for s, e in ordinati[1:]:
        if s <= esito[-1][1]:
            esito[-1][1] = max(esito[-1][1], e)
        else:
            esito.append([s, e])
    return [(s, e) for s, e in esito]


def has_weekly_configuration(weekly_rows) -> bool:
    """D1: l'agente ha ALMENO una riga di orario settimanale? Se no, A30-11
    non si applica affatto per lui (legacy, nessun vincolo)."""
    return bool(weekly_rows)


def effective_windows(date_from: datetime, date_to: datetime, *, has_weekly_config: bool,
                      weekly_rows=(), exception_rows=(), closure_rows=()):
    """Gli intervalli UTC, dentro `[date_from, date_to)`, in cui l'agente e'
    EFFETTIVAMENTE disponibile secondo D4.

    Restituisce `None` (D1) se `has_weekly_config` e' `False`: nessun
    vincolo, il chiamante deve trattarlo come "sempre disponibile" (il
    comportamento D7 di prima di A30-11).
    """
    if date_to <= date_from:
        raise ValueError("finestra non valida: la fine deve essere dopo l'inizio")
    if not has_weekly_config:
        return None

    esito = []
    giorno = (date_from.astimezone(ROMA) - timedelta(minutes=1)).date()
    ultimo = date_to.astimezone(ROMA).date()
    while giorno <= ultimo:
        for s_min, e_min in _minuti_giorno(giorno, weekly_rows, exception_rows, closure_rows):
            s = _istante_locale(giorno, s_min)
            e = _istante_locale(giorno, e_min)
            if e > date_from and s < date_to:
                esito.append((max(s, date_from), min(e, date_to)))
        giorno = giorno + timedelta(days=1)
    return _unisci_istanti(esito)


def is_within(start_at: datetime, end_at: datetime, effective) -> bool:
    """`[start_at, end_at)` e' interamente dentro l'unione di `effective`?

    `effective is None` (D1, nessuna configurazione): sempre `True`, mai un
    vincolo per un agente non configurato.
    """
    if effective is None:
        return True
    resto = [(start_at, end_at)]
    for s, e in effective:
        nuovo = []
        for rs, re_ in resto:
            if e <= rs or s >= re_:
                nuovo.append((rs, re_))
                continue
            if s > rs:
                nuovo.append((rs, s))
            if e < re_:
                nuovo.append((e, re_))
        resto = nuovo
        if not resto:
            break
    return not resto
