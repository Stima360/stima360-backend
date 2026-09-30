"""A31-3 - le regole del PATCH/DELETE legacy di `property_visits` (D5-D7 +
regola congelata A31-1). Funzioni pure: nessun SQL, nessuna transazione. Il
repository PROPERTY le chiama dopo aver bloccato la riga, nella sua
transazione, PRIMA di scrivere.

Riga PROIETTATA (`appointment_id` valorizzato):
  * il PATCH legacy scrive solo i campi legacy-owned: esito, feedback, voto
    (D4: la proiezione non li tocca mai, restano del percorso legacy);
  * data, stato, contatto, lead, immobile, `assigned_to`, `created_by` sono
    dell'Agenda: cambiarli e' un 409 "gestire dall'Agenda". Lo stesso valore
    gia' salvato NON e' un cambiamento (i form di modifica rimandano tutto il
    record: rifiutarlo romperebbe la registrazione dell'esito). La data si
    confronta al minuto, la precisione del campo `datetime-local` dei form;
  * DELETE: 409, "annulla dall'Agenda" (D7). Nessuna cancellazione fisica.

Riga LEGACY (`appointment_id` NULL): comportamento storico, tranne che un
PATCH non puo' renderla una visita FUTURA APERTA (`scheduled`/`confirmed`
nel futuro) cambiando data o stato: si crea/programma dall'Agenda. Le
visite passate e concluse restano modificabili come prima. Nessun backfill.
"""
from __future__ import annotations

from datetime import datetime

from . import errors

#: D4: i soli campi che il PATCH legacy scrive su una visita proiettata.
CAMPI_LEGACY = ("outcome", "feedback", "rating")
STATI_APERTI = ("scheduled", "confirmed")

_MESSAGGIO_AGENDA = ("Questa visita e' gestita dall'Agenda: data, stato, agente, contatto e "
                     "immobile si cambiano dall'Agenda. Qui si registrano solo esito, "
                     "feedback e valutazione")
_MESSAGGIO_ELIMINA = ("Questa visita e' collegata all'Agenda e non si elimina: "
                      "annullala dall'Agenda")
_MESSAGGIO_RIAPERTURA = ("Una visita registrata qui non puo' diventare una visita futura "
                         "da svolgere: crea o programma la visita tramite l'Agenda")


def _al_minuto(valore):
    if not isinstance(valore, datetime):
        return valore
    if valore.tzinfo is None or valore.utcoffset() is None:
        # senza fuso non e' confrontabile con un istante salvato: diverso
        return ("naive", valore.replace(second=0, microsecond=0))
    # due istanti con fuso si confrontano come istanti (i fusi sono a minuti)
    return valore.replace(second=0, microsecond=0)


def stesso_valore(campo, attuale, richiesto) -> bool:
    if campo == "scheduled_at":
        return _al_minuto(attuale) == _al_minuto(richiesto)
    return attuale == richiesto


def check_projected_patch(current: dict, data: dict) -> None:
    for campo, valore in data.items():
        if campo in CAMPI_LEGACY:
            continue
        if not stesso_valore(campo, current.get(campo), valore):
            raise errors.BuyerVisitManagedByAgenda(_MESSAGGIO_AGENDA, field=campo)


def _futura_aperta(stato, quando, adesso) -> bool:
    if stato not in STATI_APERTI or not isinstance(quando, datetime):
        return False
    if quando.tzinfo is None or quando.utcoffset() is None:
        # un orario senza fuso: il database lo legge nel fuso della sessione;
        # qui non si indovina, lo si tratta come futuro (rifiuto prudente)
        return True
    return quando > adesso


def check_legacy_patch(current: dict, data: dict, adesso: datetime) -> None:
    stato = data.get("status", current.get("status"))
    quando = data.get("scheduled_at", current.get("scheduled_at"))
    if not _futura_aperta(stato, quando, adesso):
        return
    cambia = (("status" in data and not stesso_valore("status", current.get("status"),
                                                      data["status"]))
              or ("scheduled_at" in data
                  and not stesso_valore("scheduled_at", current.get("scheduled_at"),
                                        data["scheduled_at"])))
    if cambia:
        raise errors.BuyerVisitLegacyReopen(_MESSAGGIO_RIAPERTURA)


def check_patch(current: dict, data: dict, adesso: datetime) -> None:
    if current.get("appointment_id") is not None:
        check_projected_patch(current, data)
    else:
        check_legacy_patch(current, data, adesso)


def check_delete(current: dict) -> None:
    if current.get("appointment_id") is not None:
        raise errors.BuyerVisitManagedByAgenda(_MESSAGGIO_ELIMINA)
