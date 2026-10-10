"""STIMA Voice - date e orari detti in italiano, interpretati in modo
deterministico rispetto al momento della registrazione (`now`), nel fuso
`Europe/Rome` dell'Agenda (`appointments.enums.DEFAULT_TIMEZONE`).

Il modello linguistico consegna le parole ("domani", "giovedi' prossimo",
"alle 15 e mezza"); questo modulo le trasforma in istanti. Quello che non
riconosce NON lo indovina: restituisce un motivo e la politica chiede.

Convenzioni (deterministiche, documentate, provate dai test):
  * "oggi" = la data di `now`; "domani" = +1; "dopodomani" = +2;
  * un giorno della settimana da solo = la PROSSIMA occorrenza strettamente
    dopo oggi (oggi lunedi' + "lunedi'" = fra sette giorni); "prossimo/a"
    non cambia il risultato; "questo/a" significa la prima occorrenza dopo
    oggi entro la settimana corrente, altrimenti e' ambiguo;
  * "fra N giorni/settimane" = somma; "il 15" = il prossimo giorno 15
    (questo mese se non e' passato, altrimenti il mese dopo);
  * "15 ottobre" = quest'anno se non e' passato, altrimenti il prossimo;
  * ore: "alle 15", "15:30", "15.30", "le tre" (ambiguo senza mattina /
    pomeriggio se <= 7), "alle 3 del pomeriggio" = 15, "alle 9 di sera" = 21,
    "alle 9 e mezza" = 9:30, "e un quarto" = :15, "meno un quarto" = -:15,
    "mezzogiorno" = 12, "mezzanotte" = 0; "mattina", "pomeriggio", "sera"
    da soli NON sono un orario;
  * ora locale inesistente (marzo) o doppia (ottobre) = ambiguo: la stessa
    regola dell'import legacy A30-6, mai una correzione silenziosa.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from appointments.enums import DEFAULT_TIMEZONE

ROMA = ZoneInfo(DEFAULT_TIMEZONE)

WEEKDAYS = ("lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica")
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre")
NUMBER_WORDS = {
    "una": 1, "uno": 1, "un": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
    "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12,
    "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16, "diciassette": 17,
    "diciotto": 18, "diciannove": 19, "venti": 20, "ventuno": 21, "ventidue": 22,
    "ventitre": 23, "trenta": 30, "quarantacinque": 45,
}

#: Motivi di ambiguita' (codici stabili, usati dalla politica e dai test).
UNRECOGNIZED_DATE = "date_unrecognized"
UNRECOGNIZED_TIME = "time_unrecognized"
AMBIGUOUS_HOUR = "time_ambiguous_hour"           # "alle tre" senza mattina/pomeriggio
AMBIGUOUS_WEEK = "date_ambiguous_this_week"      # "questo venerdi'" quando e' gia' passato
NONEXISTENT_LOCAL = "time_nonexistent_dst"       # 02:30 nella notte di marzo
DOUBLE_LOCAL = "time_double_dst"                 # 02:30 nella notte di ottobre
PAST = "datetime_in_the_past"


@dataclass(frozen=True)
class DateResolution:
    day: date | None
    reason: str | None = None


@dataclass(frozen=True)
class TimeResolution:
    at: time | None
    reason: str | None = None


@dataclass(frozen=True)
class WhenResolution:
    """`start` e' un datetime con fuso Europe/Rome, oppure None con `reasons`."""
    start: datetime | None
    day: date | None
    at: time | None
    reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.start is not None and not self.reasons


def fold(text: str) -> str:
    """Minuscolo, senza accenti, spazi singoli: "Giovedì" -> "giovedi"."""
    senza = unicodedata.normalize("NFKD", text or "")
    senza = "".join(c for c in senza if not unicodedata.combining(c))
    senza = senza.lower().replace("'", " ").replace("’", " ")
    return re.sub(r"\s+", " ", senza).strip()


def local_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ROMA)


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

def _prossimo_giorno(oggi: date, weekday: int, *, questa_settimana: bool) -> DateResolution:
    delta = (weekday - oggi.weekday()) % 7
    if delta == 0:
        delta = 7
    if questa_settimana and oggi.weekday() + delta > 6:
        return DateResolution(None, AMBIGUOUS_WEEK)
    return DateResolution(oggi + timedelta(days=delta))


def _numero(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def resolve_date(text: str, now: datetime) -> DateResolution:
    oggi = local_now(now).date()
    t = fold(text)
    t = re.sub(r"^(il|lo|la|l|per|di|entro|a|al)\s+", "", t)
    t = re.sub(r"\s+(mattina|pomeriggio|sera|notte)$", "", t)

    if t in ("oggi", "stasera", "stamattina", "oggi pomeriggio"):
        return DateResolution(oggi)
    if t in ("domani", "domattina", "domani mattina", "domani pomeriggio", "domani sera"):
        return DateResolution(oggi + timedelta(days=1))
    if t in ("dopodomani", "dopo domani"):
        return DateResolution(oggi + timedelta(days=2))

    m = re.fullmatch(r"(fra|tra)\s+(\w+)\s+(giorn[oi]|settiman[ae])", t)
    if m:
        n = _numero(m.group(2))
        if n is None:
            return DateResolution(None, UNRECOGNIZED_DATE)
        giorni = n * (7 if m.group(3).startswith("settiman") else 1)
        return DateResolution(oggi + timedelta(days=giorni))

    m = re.fullmatch(r"(quest[oa]\s+|prossim[oa]\s+)?(\w+?)(\s+prossim[oa])?", t)
    if m and m.group(2) in WEEKDAYS:
        return _prossimo_giorno(oggi, WEEKDAYS.index(m.group(2)),
                                questa_settimana=bool(m.group(1) and m.group(1).startswith("quest")))

    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
    if m:
        giorno, mese, anno = int(m.group(1)), int(m.group(2)), m.group(3)
        return _data_esplicita(oggi, giorno, mese, int(anno) if anno else None)

    m = re.fullmatch(r"(\d{1,2})(?:\s+(\w+))?(?:\s+(\d{4}))?", t)
    if m:
        giorno = int(m.group(1))
        mese_nome, anno = m.group(2), m.group(3)
        if mese_nome is None:
            if not 1 <= giorno <= 31:
                return DateResolution(None, UNRECOGNIZED_DATE)
            candidato = _prossimo_giorno_del_mese(oggi, giorno)
            return DateResolution(candidato) if candidato else DateResolution(None, UNRECOGNIZED_DATE)
        if mese_nome in MONTHS:
            return _data_esplicita(oggi, giorno, MONTHS.index(mese_nome) + 1, int(anno) if anno else None)
        return DateResolution(None, UNRECOGNIZED_DATE)

    return DateResolution(None, UNRECOGNIZED_DATE)


def _prossimo_giorno_del_mese(oggi: date, giorno: int) -> date | None:
    anno, mese = oggi.year, oggi.month
    for _ in range(3):
        try:
            candidato = date(anno, mese, giorno)
        except ValueError:
            candidato = None
        if candidato is not None and candidato >= oggi:
            return candidato
        mese += 1
        if mese > 12:
            mese, anno = 1, anno + 1
    return None


def _data_esplicita(oggi: date, giorno: int, mese: int, anno: int | None) -> DateResolution:
    if anno is not None and anno < 100:
        anno += 2000
    anni = [anno] if anno is not None else [oggi.year, oggi.year + 1]
    for a in anni:
        try:
            candidato = date(a, mese, giorno)
        except ValueError:
            continue
        if anno is not None or candidato >= oggi:
            return DateResolution(candidato)
    return DateResolution(None, UNRECOGNIZED_DATE)


# ---------------------------------------------------------------------------
# Orari
# ---------------------------------------------------------------------------

def resolve_time(text: str | None) -> TimeResolution:
    if text is None:
        return TimeResolution(None, UNRECOGNIZED_TIME)
    t = fold(text)
    t = re.sub(r"^(alle|all|a|le|per le|verso le|ore|intorno alle)\s+", "", t)
    if t in ("mezzogiorno", "a mezzogiorno"):
        return TimeResolution(time(12, 0))
    if t in ("mezzanotte", "a mezzanotte"):
        return TimeResolution(time(0, 0))
    if t in ("mattina", "pomeriggio", "sera", "di mattina", "di pomeriggio", "di sera", "in mattinata"):
        return TimeResolution(None, UNRECOGNIZED_TIME)

    fascia = None
    m = re.search(r"\s+(di|del|della)\s+(mattina|mattino|pomeriggio|sera|notte)$", t)
    if m:
        fascia = m.group(2)
        t = t[: m.start()]

    m = re.fullmatch(r"(\w+)(?:[:.h](\d{2}))?(?:\s+e\s+(mezza|mezzo|un quarto|tre quarti|\w+))?(?:\s+meno\s+(un quarto|\w+))?", t)
    if not m:
        return TimeResolution(None, UNRECOGNIZED_TIME)
    ora = _numero(m.group(1))
    if ora is None or not 0 <= ora <= 24:
        return TimeResolution(None, UNRECOGNIZED_TIME)
    minuti = int(m.group(2)) if m.group(2) else 0
    if m.group(3):
        parola = m.group(3)
        minuti = {"mezza": 30, "mezzo": 30, "un quarto": 15, "tre quarti": 45}.get(parola) or _numero(parola)
        if minuti is None:
            return TimeResolution(None, UNRECOGNIZED_TIME)
    if m.group(4):
        parola = m.group(4)
        meno = 15 if parola == "un quarto" else _numero(parola)
        if meno is None:
            return TimeResolution(None, UNRECOGNIZED_TIME)
        ora, minuti = ora - 1, 60 - meno
    if not 0 <= minuti < 60:
        return TimeResolution(None, UNRECOGNIZED_TIME)

    esplicito = bool(m.group(2)) or m.group(1).isdigit()
    if fascia in ("pomeriggio", "sera", "notte") and ora < 12:
        ora += 12
    elif fascia in ("mattina", "mattino") and ora == 12:
        ora = 0
    elif fascia is None and not esplicito and 1 <= ora <= 7:
        # "alle tre": mattina o pomeriggio? Detto a parole, senza fascia, e'
        # ambiguo; "alle 3" in cifre resta 03:00 come scritto.
        return TimeResolution(None, AMBIGUOUS_HOUR)
    if ora == 24:
        ora = 0
    return TimeResolution(time(ora, minuti))


# ---------------------------------------------------------------------------
# Insieme
# ---------------------------------------------------------------------------

def combine(day: date, at: time, now: datetime, *, allow_past: bool = False) -> WhenResolution:
    naive = datetime.combine(day, at)
    reasons: list[str] = []
    prima = naive.replace(tzinfo=ROMA, fold=0)
    dopo = naive.replace(tzinfo=ROMA, fold=1)
    utc = ZoneInfo("UTC")
    if prima.astimezone(utc).astimezone(ROMA).replace(tzinfo=None) != naive:
        # Ora inesistente (marzo): il fuso la "salta", il viaggio di andata e
        # ritorno non torna sullo stesso orario.
        reasons.append(NONEXISTENT_LOCAL)
    elif prima.utcoffset() != dopo.utcoffset():
        # Ora doppia (ottobre): due istanti diversi per lo stesso orario.
        reasons.append(DOUBLE_LOCAL)
    if not reasons and not allow_past and prima <= local_now(now):
        reasons.append(PAST)
    return WhenResolution(None if reasons else prima, day, at, tuple(reasons))


def resolve_when(date_text: str, time_text: str | None, now: datetime, *,
                 require_time: bool = True, default_time: time | None = None,
                 allow_past: bool = False) -> WhenResolution:
    """Data + orario -> istante. Senza orario: `default_time` se c'e' (i task),
    altrimenti e' un motivo (`require_time`, gli appuntamenti)."""
    giorno = resolve_date(date_text, now)
    reasons: list[str] = []
    if giorno.day is None:
        reasons.append(giorno.reason or UNRECOGNIZED_DATE)
    if time_text is None and default_time is not None:
        orario = TimeResolution(default_time)
    elif time_text is None and not require_time:
        orario = TimeResolution(None)
    else:
        orario = resolve_time(time_text)
        if orario.at is None:
            reasons.append(orario.reason or UNRECOGNIZED_TIME)
    if reasons:
        return WhenResolution(None, giorno.day, orario.at, tuple(reasons))
    if orario.at is None:
        return WhenResolution(None, giorno.day, None, ())
    return combine(giorno.day, orario.at, now, allow_past=allow_past)
