"""A31-3 - la facade verso l'Agenda per chi programma una visita acquirente
fuori dall'Agenda (BUY "Visita programmata" sul match, PROPERTY "Programma
visita").

Una visita `scheduled`/`confirmed` nasce SEMPRE come appuntamento
`buyer_visit` (`crm_manual`): la crea `appointments.service`, con la sua
macchina a stati, i suoi permessi, il suo controllo conflitti (lock
d'agente + EXCLUDE), il suo evento, il suo mark dirty Google e la
proiezione A31-2. Qui non c'e' una seconda logica di creazione: solo il
corpo `AppointmentCreate` costruito dai dati del chiamante e la chiamata a
`create_appointment_with_cursor` sul cursore di chi chiama.

Regole del package valide anche qui: nessuna connessione, nessun commit.
La transazione e' del chiamante (BUY: appuntamento + proiezione +
interazione + match + storico in UN commit).

AGENTE (D2, A31-3 §4) - mai inferito:
  * `assigned_user_id` esplicito -> quello (l'Agenda verifica chi puo'
    assegnare a chi e che sia un membro attivo);
  * senza, un operatore che NON assegna (ruolo `agent`) e' identificato con
    certezza dalla sessione -> se stesso;
  * senza, owner/admin/Supreme -> None: la facade NON si attiva e il
    chiamante resta sul percorso legacy dichiarato (attivazione in A31-4,
    con il selettore agente in UI). Mai il testo `assigned_to`, un nome,
    un'email, `created_by`, il proprietario dell'immobile o il match.
"""
from __future__ import annotations

from datetime import timedelta

from pydantic import ValidationError as _SchemaError

from appointments import service as _agenda
from appointments.enums import default_duration_minutes
from appointments.schemas import AppointmentCreate

from . import errors, projection

TIPO = "buyer_visit"
#: Gli stati in cui una visita nasce dall'Agenda (D1: mai `requested` qui).
STATI_APERTI = ("scheduled", "confirmed")
#: Limite di sicurezza nel seguire una catena di spostamenti.
_MAX_SPOSTAMENTI = 100


def resolve_agent(ctx, requested):
    """L'agente della visita, o None se la facade non si puo' attivare."""
    if requested is not None:
        return int(requested)
    if getattr(ctx, "may_assign_records", False):
        return None
    user_id = getattr(ctx, "user_id", None)
    return None if user_id is None else int(user_id)


def durata() -> timedelta:
    return timedelta(minutes=default_duration_minutes(TIPO))


def _corpo(*, property_id, contact_id, lead_id, start_at, assigned_user_id, status,
           client_request_id):
    try:
        return AppointmentCreate(
            appointment_type=TIPO, status=status, start_at=start_at,
            end_at=start_at + durata(), assigned_user_id=assigned_user_id,
            contact_id=contact_id, lead_id=lead_id, property_id=property_id,
            client_request_id=client_request_id)
    except _SchemaError as exc:
        primo = (exc.errors() or [{}])[0]
        raise errors.BuyerVisitInvalid(
            primo.get("msg") or "Dati della visita non validi") from exc
    except TypeError as exc:
        # un orario senza fuso non si somma/confronta: stessa risposta
        raise errors.BuyerVisitInvalid(
            "l'orario deve indicare il fuso (es. 2026-10-01T10:00:00+02:00)") from exc


def _proiezione(cur, appointment_id):
    """La proiezione di un appuntamento; per una replica il cui appuntamento
    e' stato poi spostato, quella del suo successore (la stessa riga segue il
    reschedule, A31-2)."""
    corrente = appointment_id
    for _ in range(_MAX_SPOSTAMENTI):
        visita = projection.for_appointment(cur, corrente)
        if visita is not None:
            return visita
        cur.execute("SELECT id FROM appointments WHERE rescheduled_from_id = %s", (corrente,))
        successore = cur.fetchone()
        if successore is None:
            return None
        corrente = successore["id"]
    return None


def schedule(cur, ctx, *, property_id, contact_id, lead_id, start_at, assigned_user_id,
             status="scheduled", client_request_id=None):
    """Crea (o ritrova, per la stessa `client_request_id`) l'appuntamento e
    restituisce `(appuntamento, visita_proiettata, replica)`."""
    corpo = _corpo(property_id=property_id, contact_id=contact_id, lead_id=lead_id,
                   start_at=start_at, assigned_user_id=assigned_user_id, status=status,
                   client_request_id=client_request_id)
    riga, replica = _agenda.create_appointment_with_cursor(ctx, cur, corpo)
    visita = _proiezione(cur, riga["id"])
    if visita is None:
        raise errors.BuyerVisitProjectionInvalid(
            "La visita non e' stata registrata sull'immobile: l'operazione e' stata annullata")
    return riga, visita, replica
