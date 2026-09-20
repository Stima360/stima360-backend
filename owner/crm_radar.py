"""LMC-8 - il radar del proprietario, letto dal CRM.

PERCHE' QUESTO FILE ESISTE.

Il Contact 360 e' un aggregatore: chiede a ogni sottosistema la propria
parte e le mette insieme. Per le case pre-incarico quella parte non esisteva,
perche' fino a LMC-7 nessuno le guardava dal lato dell'operatore. Invece di
far conoscere al CRM la catena `owner_stima_access -> owner_accounts ->
contacts`, i valori di PROPERTY WATCH e la proiezione dell'interesse - tre
domini diversi - c'e' questa cucitura sola, che il CRM chiama e basta.

COSA NON FA, CHE E' LA PARTE IMPORTANTE.

Non decide. Il livello di interesse arriva da `interest_service` cosi' com'e':
qui non c'e' una soglia, non c'e' un confronto fra giorni, non c'e' una
seconda tabella di fasce. Se domani la regola di LMC-7 cambiasse, questo file
non se ne accorgerebbe, ed e' esattamente il comportamento voluto - due posti
che calcolano lo stesso livello sono due posti che possono dare risposte
diverse allo stesso operatore.

Non traduce nemmeno. `high` resta `high`: ALTO e' una parola della scheda, e
la scheda e' l'unico posto che deve saperlo.

Non scrive. Nessun task, nessuna attivita', nessun follow-up: LMC-8 rende
visibile un segnale, e chi decide cosa farne e' chi lo legge.

COSA NON ESCE DI QUI.

Il CRM vede SE il proprietario ha aperto la sezione della domanda, non cosa
c'era scritto dentro: la rilevazione di Buyer Pressure - punteggi, budget
medi, conteggi di acquirenti - resta in PROPERTY WATCH e in questo file non
viene nemmeno letta. Ogni casa e' costruita campo per campo da un elenco
chiuso, quindi cio' che non e' scritto qui sotto non arriva nella scheda.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import home_profile

from . import home_service, interest_service
from . import repository as owner_repository

#: I campi descrittivi che il CRM vede di una casa. Elenco CHIUSO: la stessa
#: disciplina di `HOME_STIMA_COLUMNS`, un livello piu' in alto.
HOME_FIELDS = ("stima_id", "address", "tipologia", "mq", "created_at",
               "initial_value", "current_value", "current_value_computed_at",
               "interest")


def _address(stima: dict[str, Any]) -> str | None:
    via = " ".join(str(stima.get(c) or "").strip()
                   for c in ("via", "civico")).strip()
    pezzi = [p for p in (via or None, stima.get("microzona"), stima.get("comune"))
             if p not in (None, "")]
    return ", ".join(str(p) for p in pezzi) if pezzi else None


def _valori_casa(agency_id: int, stima_id: int) -> dict[str, Any]:
    """Valore iniziale, valore monitorato e data, dagli stessi dati del portale.

    Si riusano le funzioni pure di LMC-2/LMC-3 invece di rileggere la regola:
    il valore iniziale e' la baseline di PROPERTY WATCH con ripiego
    sull'evento `stima_completata`, il valore monitorato e' l'ultimo
    `valuation_snapshot` reale. Se la definizione cambiasse, cambierebbe in
    un posto solo e questa funzione la seguirebbe.
    """
    watch = owner_repository.home_watch_summary(agency_id, stima_id)
    baseline = watch["baseline_payload"] if watch else None
    iniziale_c_e = home_service.initial_value(
        baseline_payload=baseline, completed_payload=None)[0] is not None
    completato = (None if iniziale_c_e
                  else owner_repository.home_completed_valuation(agency_id, stima_id))

    # Si chiama il compositore pubblico invece di rifare i tre calcoli: e'
    # letteralmente la stessa vista che il proprietario riceve, quindi la
    # scheda contatto e il portale non possono divergere. `buyer_pressure`
    # resta `None` di proposito - al CRM serve sapere SE il proprietario ha
    # aperto la domanda, e quello lo dice il radar; la rilevazione non viene
    # nemmeno caricata.
    vista = home_service.build_home_detail(
        stima={}, watch=watch, observations=(watch["observations"] if watch else []),
        baseline_payload=baseline, completed_payload=completato,
        buyer_pressure=None)
    valutazione = vista["valuation"]
    return {chiave: valutazione[chiave] for chiave in
            ("initial_value", "current_value", "current_value_computed_at")}


def _as_datetime(valore) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if isinstance(valore, str):
        try:
            momento = datetime.fromisoformat(valore.replace("Z", "+00:00"))
        except ValueError:
            return None
        return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
    return None


#: Il fondo della scala temporale, per chi non ha ne' attivita' ne' data.
_MAI = datetime.min.replace(tzinfo=timezone.utc)


def _ordine(home: dict[str, Any]) -> tuple:
    """La chiave di ordinamento, e la ragione per cui e' fatta cosi'.

    Prima chi ha dato segno di vita, dal piu' recente: e' l'unica cosa che
    questo riquadro serve a far vedere. Poi, fra chi non ne ha dato, la stima
    piu' recente - non perche' valga di piu', ma perche' e' l'ordine che un
    operatore si aspetta quando non c'e' niente da ordinare.

    Il primo elemento separa i due gruppi, cosi' una casa vista ieri viene
    prima di una stima creata stamattina.

    `stima_id` NON e' in questa chiave: si ordina in due passate, perche' a
    parita' di tutto il resto l'id deve crescere mentre le date calano, e una
    sola `sort(reverse=True)` li farebbe calare entrambi.
    """
    attivita = _as_datetime(home["interest"]["last_activity_at"])
    return (attivita is not None, attivita or _MAI,
            _as_datetime(home["created_at"]) or _MAI)


def contact_homes_block(ctx, contact_id: int) -> dict[str, Any]:
    """Le case pre-incarico di un contatto, ognuna con il proprio radar.

    UN CONTATTO PUO' AVERE PIU' CASE. Non e' un caso limite: chi ha chiesto
    una stima per la casa al mare e una per quella in citta' ha due storie
    diverse, e mediarle direbbe una cosa falsa su entrambe. Ogni casa ha il
    proprio livello, i propri giorni e le proprie motivazioni.

    L'ORDINE. Prima le case con attivita' del proprietario, dalla piu'
    recente; poi, in assenza di attivita', la stima piu' recente. Chi ha dato
    segno di vita precede sempre chi non ne ha dato.

    Il tenant arriva da `ctx` e da nessun'altra parte: `require_agency()` e'
    la prima istruzione, e la stessa agenzia limita sia la lettura delle case
    sia quella degli eventi.
    """
    agency_id = ctx.require_agency()
    case = owner_repository.contact_homes(agency_id, contact_id)
    if not case:
        # Nessuna casa: il riquadro non ha niente da dire e lo dichiara,
        # invece di comparire vuoto nella scheda.
        return {"available": False, "homes": []}

    homes = []
    for originale in case:
        stima_id = originale["id"]
        # LMC-10: la scheda contatto mostra il PROFILO EFFETTIVO, con lo
        # stesso composer del portale. Non c'e' una regola di merge qui: se
        # ce ne fosse una, la scheda e il portale potrebbero dire due `mq`
        # diversi della stessa casa, che e' precisamente cio' che LMC-8
        # esiste per evitare.
        stima = home_profile.effective_home(
            originale, owner_repository.home_override(agency_id, stima_id))
        valori = _valori_casa(agency_id, stima_id)
        radar = interest_service.interest_for_stima_scoped(ctx, stima_id)["interest"]
        homes.append({
            "stima_id": stima_id,
            "address": _address(stima),
            "tipologia": stima.get("tipologia"),
            "mq": stima.get("mq"),
            "created_at": stima.get("data"),
            "initial_value": valori["initial_value"],
            "current_value": valori["current_value"],
            "current_value_computed_at": valori["current_value_computed_at"],
            "interest": radar,
        })

    # Due passate, e l'ordine conta: prima l'id crescente, poi il criterio
    # vero al contrario. `sort` e' stabile, quindi a parita' di attivita' e
    # di data le case restano in ordine di id.
    homes.sort(key=lambda home: home["stima_id"])
    homes.sort(key=_ordine, reverse=True)
    return {"available": True, "homes": homes}
