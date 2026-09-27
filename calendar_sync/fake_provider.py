"""A30-9A - un provider di calendario FINTO, in memoria, per i test.

Implementa il contratto di `provider.py` con la semantica che il provider vero
dovra' avere (idempotenza per event_id, ricreazione di un evento sparito,
DELETE di un assente = successo) e permette di SIMULARE i guasti:

    fake.fail_next("ensure", "rate_limited")            # 429
    fake.fail_next("ensure", "forbidden", reason="insufficientPermissions")
    fake.crash_after_success_next("ensure")             # successo remoto, poi
                                                         # il processo "muore"
    fake.vanish(connection_id, calendar_id, event_id)    # cancellato a mano
                                                         # sul remoto (404)

NESSUNA RETE: nessun import di librerie HTTP, nessun socket.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .provider import (
    CalendarProviderError,
    DeleteResult,
    EnsureResult,
    EventPayload,
    ProviderAuth,
)
from .provider import RemoteEvent as _ContrattoRemoteEvent


class SimulatedCrash(BaseException):
    """Il processo "muore" DOPO che il remoto ha applicato la modifica e PRIMA
    che il DB la registri. BaseException: nessun `except Exception` del
    dominio la intercetta, esattamente come un kill del processo."""


@dataclass
class RemoteEvent:
    payload: EventPayload
    status: str          # confirmed | cancelled
    etag: str
    updated: datetime


class FakeCalendarProvider:
    name = "fake"

    def __init__(self):
        # (connection_id, calendar_id) -> {event_id: RemoteEvent}
        self.calendars = {}
        # (connection_id, calendar_id) -> {event_id} spariti del tutto (404)
        self.calls = []
        self._fallimenti = []
        self._crash = []
        self._versione = 0
        self.insert_conflicts = 0

    # -- simulazione -------------------------------------------------------

    def fail_next(self, operation, kind, *, times=1, http_status=None, reason=None):
        for _ in range(times):
            self._fallimenti.append((operation, kind, http_status, reason))

    def crash_after_success_next(self, operation):
        self._crash.append(operation)

    def vanish(self, connection_id, calendar_id, event_id):
        """L'evento sparisce del tutto dal remoto (GET/PATCH -> 404)."""
        self.calendars.get((connection_id, calendar_id), {}).pop(event_id, None)

    # -- A30-10: simulazioni per l'inbound -----------------------------------

    def simulate_remote_reschedule(self, connection_id, calendar_id, event_id, *,
                                   start_at, end_at):
        """"Qualcuno" ha spostato l'evento direttamente su Google."""
        cal = self.calendars[(connection_id, calendar_id)]
        ev = cal[event_id]
        nuovo = replace(ev.payload, start_at=start_at, end_at=end_at)
        cal[event_id] = RemoteEvent(nuovo, ev.status, self._etag(), datetime.now(timezone.utc))

    def simulate_remote_content_drift(self, connection_id, calendar_id, event_id, *,
                                      summary=None, description=None):
        """Solo `summary`/`description` cambiati su Google: orari intatti."""
        cal = self.calendars[(connection_id, calendar_id)]
        ev = cal[event_id]
        nuovo = replace(
            ev.payload,
            summary=ev.payload.summary if summary is None else summary,
            description=ev.payload.description if description is None else description,
        )
        cal[event_id] = RemoteEvent(nuovo, ev.status, self._etag(), datetime.now(timezone.utc))

    def simulate_remote_private_properties(self, connection_id, calendar_id, event_id, **valori):
        """Le proprieta' private tecniche manomesse/incoerenti su Google."""
        cal = self.calendars[(connection_id, calendar_id)]
        ev = cal[event_id]
        nuove = dict(ev.payload.private_properties)
        nuove.update(valori)
        nuovo = replace(ev.payload, private_properties=nuove)
        cal[event_id] = RemoteEvent(nuovo, ev.status, self._etag(), datetime.now(timezone.utc))

    def simulate_remote_cancelled_tombstone(self, connection_id, calendar_id, event_id):
        """HTTP 200 con `status: cancelled` (l'evento resta, ma e' un
        tombstone): diverso da `vanish`, che simula un 404/410."""
        cal = self.calendars.get((connection_id, calendar_id), {})
        ev = cal.get(event_id)
        if ev is not None:
            ev.status = "cancelled"
            ev.etag = self._etag()
            ev.updated = datetime.now(timezone.utc)

    # -- letture per i test -------------------------------------------------

    def active_events(self, connection_id, calendar_id="primary"):
        cal = self.calendars.get((connection_id, calendar_id), {})
        return {eid: ev for eid, ev in cal.items() if ev.status == "confirmed"}

    def all_active(self):
        return {(conn, cal, eid): ev for (conn, cal), eventi in self.calendars.items()
                for eid, ev in eventi.items() if ev.status == "confirmed"}

    # -- contratto ------------------------------------------------------------

    def _guasto(self, operation):
        for i, (op, kind, status, reason) in enumerate(self._fallimenti):
            if op == operation:
                del self._fallimenti[i]
                raise CalendarProviderError(kind, http_status=status, reason=reason,
                                            operation=operation)

    def _forse_crash(self, operation):
        if operation in self._crash:
            self._crash.remove(operation)
            raise SimulatedCrash(f"crash dopo il successo remoto di {operation}")

    def _etag(self):
        self._versione += 1
        return f'"{self._versione}"'

    def get_event(self, auth: ProviderAuth, calendar_id: str,
                  event_id: str) -> _ContrattoRemoteEvent | None:
        self.calls.append(("get", auth.connection_id, calendar_id, event_id))
        self._guasto("get")
        cal = self.calendars.get((auth.connection_id, calendar_id), {})
        ev = cal.get(event_id)
        if ev is None:
            return None
        self._forse_crash("get")
        if ev.status == "cancelled":
            return _ContrattoRemoteEvent(status="cancelled", start_at=None, end_at=None,
                                         timezone=None, etag=ev.etag, updated_at=ev.updated,
                                         private_properties={})
        return _ContrattoRemoteEvent(
            status=ev.status,
            start_at=ev.payload.start_at,
            end_at=ev.payload.end_at,
            timezone=ev.payload.timezone,
            etag=ev.etag,
            updated_at=ev.updated,
            private_properties=dict(ev.payload.private_properties),
        )

    def ensure_event(self, auth: ProviderAuth, calendar_id: str,
                     payload: EventPayload) -> EnsureResult:
        self.calls.append(("ensure", auth.connection_id, calendar_id, payload.event_id))
        self._guasto("ensure")
        cal = self.calendars.setdefault((auth.connection_id, calendar_id), {})
        adesso = datetime.now(timezone.utc)
        esistente = cal.get(payload.event_id)
        if esistente is None:
            # evento mai esistito o sparito (404): INSERT con lo stesso id
            cal[payload.event_id] = RemoteEvent(payload, "confirmed", self._etag(), adesso)
            esito = "created"
        else:
            # l'id esiste gia' (anche cancellato): un INSERT darebbe 409 ->
            # l'implementazione ripiega su un aggiornamento (e riattiva)
            self.insert_conflicts += 1
            if esistente.status == "confirmed" and esistente.payload == payload:
                self._forse_crash("ensure")
                return EnsureResult("unchanged", esistente.etag, esistente.updated)
            cal[payload.event_id] = RemoteEvent(payload, "confirmed", self._etag(), adesso)
            esito = "updated"
        self._forse_crash("ensure")
        ev = cal[payload.event_id]
        return EnsureResult(esito, ev.etag, ev.updated)

    def delete_event(self, auth: ProviderAuth, calendar_id: str,
                     event_id: str) -> DeleteResult:
        self.calls.append(("delete", auth.connection_id, calendar_id, event_id))
        self._guasto("delete")
        cal = self.calendars.setdefault((auth.connection_id, calendar_id), {})
        ev = cal.get(event_id)
        if ev is None or ev.status == "cancelled":
            return DeleteResult("absent")          # 404 / 410: gia' assente
        ev.status = "cancelled"
        ev.etag = self._etag()
        self._forse_crash("delete")
        return DeleteResult("deleted")
