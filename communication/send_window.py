"""P29-3C - la finestra di invio: quando un passo PUO' partire.

UNA FUNZIONE PURA, E IL MOTIVO PER CUI LO E'

`next_allowed(istante, finestra, fuso)` non legge il database, non guarda
l'orologio e non ha stato: riceve un istante UTC e restituisce il primo
istante UTC in cui quel passo e' spedibile. Tutto cio' che decide sta negli
argomenti, quindi la si puo' provare su un anno intero di casi - DST compresi
- senza un database e senza aspettare.

IL FUSO E' QUELLO DELLA JOURNEY, MAI QUELLO DEL SERVER

`communication_journeys.send_timezone` (071, default `Europe/Rome`) e' l'unica
autorita'. Un processo che girasse in UTC - come girano i container - con
`datetime.now()` locale manderebbe le mail alle otto di sera credendo fossero
le nove del mattino. Qui il fuso e' un parametro obbligatorio: non esiste un
ramo che possa cadere su quello del sistema.

DST, E COSA SUCCEDE NELLE DUE ORE CHE NON ESISTONO O ESISTONO DUE VOLTE

L'ora legale rompe l'aritmetica ingenua sui `timedelta`: fra le 00:00 e le
24:00 di un giorno di transizione non ci sono sempre 24 ore. Per questo il
calcolo si fa in DUE spazi distinti e mai mescolati: il confronto "questo
istante e' dentro la finestra?" avviene sull'ORA LOCALE (quella che
l'interessato legge sul suo orologio), mentre l'avanzamento al giorno
successivo avviene sui GIORNI DI CALENDARIO locali, non aggiungendo 86400
secondi.

  * ora inesistente (l'ultima domenica di marzo, 02:00 -> 03:00): la finestra
    che comincia alle 02:30 quel giorno non esiste. `ZoneInfo` risolve quel
    wall time con l'offset PRIMA della transizione, cioe' con il primo istante
    reale dopo il salto. E' esattamente cio' che serve: "appena possibile".
  * ora doppia (l'ultima domenica di ottobre, 03:00 -> 02:00): le 02:30
    esistono due volte. Si sceglie la PRIMA (`fold=0`), che e' la piu' vicina
    e non fa aspettare un'ora in piu' chi era gia' dentro la finestra.

LA FORMA DELLA FINESTRA

`communication_journey_steps.send_window` e' JSONB e questo modulo ne e' il
solo interprete:

    {"days": [1, 2, 3, 4, 5], "from": "09:00", "to": "19:00"}

`days` sono i giorni ISO (1 = lunedi', 7 = domenica); assenti o vuoti
significa "tutti i giorni". `from`/`to` sono ore locali `HH:MM`; assenti
significa "tutta la giornata". L'intervallo e' chiuso a sinistra e aperto a
destra: `to` e' il primo istante NON piu' valido, cosi' che due finestre
adiacenti non si sovrappongano di un minuto. Una finestra che scavalca la
mezzanotte non e' rappresentabile ed e' rifiutata alla creazione della
journey, invece di essere interpretata a indovinare.

`None` o `{}` significa NESSUNA restrizione, e la funzione restituisce
l'istante ricevuto: un passo senza finestra parte quando e' dovuto.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .exceptions import ValidationError

#: I giorni ISO ammessi in `days`.
ISO_DAYS = (1, 2, 3, 4, 5, 6, 7)

#: Quanti giorni al massimo si guarda avanti prima di dichiarare la finestra
#: vuota. Con `days` non vuoto la risposta arriva entro sette; il limite
#: esiste perche' un ciclo infinito su una finestra impossibile sarebbe un
#: processo che non torna, e un errore chiaro e' meglio di un tick fermo.
MAX_GIORNI_AVANTI = 8


def _orario(valore: Any, campo: str) -> time:
    if not isinstance(valore, str):
        raise ValidationError(f"send_window.{campo} must be a 'HH:MM' string, got {valore!r}")
    try:
        ore, minuti = valore.split(":")
        return time(int(ore), int(minuti))
    except (ValueError, TypeError):
        raise ValidationError(f"send_window.{campo} is not a valid 'HH:MM' time: {valore!r}") from None


def validate(window: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """La finestra, normalizzata, o un errore. Chiamata quando si CREA una
    journey: una finestra sbagliata deve fermare il provisioning, non il tick
    di un mercoledi' notte."""
    if window is None or window == {}:
        return None
    if not isinstance(window, Mapping):
        raise ValidationError(f"send_window must be an object, got {type(window).__name__}")

    sconosciute = set(window) - {"days", "from", "to"}
    if sconosciute:
        raise ValidationError(f"send_window has unknown keys {sorted(sconosciute)}")

    giorni = window.get("days")
    if giorni is None or giorni == []:
        giorni_norm: tuple[int, ...] = ISO_DAYS
    else:
        if not isinstance(giorni, (list, tuple)):
            raise ValidationError("send_window.days must be a list of ISO weekdays (1=Mon..7=Sun)")
        try:
            giorni_norm = tuple(sorted({int(g) for g in giorni}))
        except (TypeError, ValueError):
            raise ValidationError("send_window.days must contain integers 1..7") from None
        if not set(giorni_norm) <= set(ISO_DAYS):
            raise ValidationError(f"send_window.days must be within 1..7, got {sorted(giorni_norm)}")

    da = _orario(window["from"], "from") if window.get("from") is not None else time(0, 0)
    a = _orario(window["to"], "to") if window.get("to") is not None else None
    if a is not None and a <= da:
        raise ValidationError(
            f"send_window.to ({a.isoformat()}) must be after send_window.from "
            f"({da.isoformat()}): a window across midnight is not representable"
        )
    return {"days": list(giorni_norm), "from": da.strftime("%H:%M"),
            **({"to": a.strftime("%H:%M")} if a is not None else {})}


def _zona(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError(f"send_timezone {timezone_name!r} is not a known IANA zone") from None


def _istante(giorno: date, ora: time, zona: ZoneInfo) -> datetime:
    """Il wall time locale, come istante UTC. Vedi il DST in testa al modulo."""
    return datetime.combine(giorno, ora, tzinfo=zona).astimezone(timezone.utc)


def next_allowed(instant: datetime, window: Mapping[str, Any] | None,
                 timezone_name: str) -> datetime:
    """Il primo istante UTC >= `instant` che la finestra ammette.

    Senza finestra restituisce `instant`. Con una finestra, se l'istante e'
    gia' dentro lo restituisce IDENTICO - non lo sposta all'inizio della
    finestra successiva, perche' un passo dovuto e' dovuto adesso.
    """
    if instant.tzinfo is None:
        raise ValidationError("next_allowed needs an aware instant; a naive one has no meaning here")
    normalizzata = validate(window)
    if normalizzata is None:
        return instant

    zona = _zona(timezone_name)
    giorni = set(normalizzata["days"])
    da = _orario(normalizzata["from"], "from")
    a = _orario(normalizzata["to"], "to") if "to" in normalizzata else None

    locale = instant.astimezone(zona)
    giorno = locale.date()
    for passo in range(MAX_GIORNI_AVANTI):
        if passo:
            # Giorno di CALENDARIO successivo, e poi la sua apertura: mai
            # `+ timedelta(days=1)` su un istante, che il giorno del cambio
            # d'ora sbaglierebbe di sessanta minuti.
            giorno = giorno + timedelta(days=1)
            candidato = _istante(giorno, da, zona)
            if giorno.isoweekday() in giorni:
                return candidato
            continue

        if giorno.isoweekday() in giorni:
            apertura = _istante(giorno, da, zona)
            if instant < apertura:
                return apertura
            if a is None or locale.time() < a:
                return instant
    raise ValidationError(
        f"send_window {normalizzata} admits no instant within {MAX_GIORNI_AVANTI} days"
    )
