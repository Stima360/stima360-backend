"""A30-9B - configurazione RUNTIME di Google Calendar, letta solo a richiesta.

Nessuna funzione qui legge l'ambiente al momento dell'IMPORT: il CRM si avvia
anche senza una sola variabile Google impostata (§1 del gate). Due livelli:

  * `is_enabled()` / `hook_deployment_namespace()` - usati dall'hook
    appointment -> sync (`calendar_sync.integration`) e dal worker: non
    solleveranno MAI un'eccezione. Se Google e' disabilitato o il namespace
    di deployment non e' configurato, il chiamante fa un NO-OP controllato:
    un appuntamento CRM deve sempre riuscire (§17).
  * `require_config()` - usato dalle rotte OAuth (`calendar_sync.router`):
    solleva `GoogleCalendarConfigError`, un errore controllato e sanitizzato
    (mai un segreto nel messaggio), se Google e' abilitato ma la
    configurazione e' incompleta.

Il namespace di deployment (TEST: stima360-test; PROD: stima360-prod, §1)
arriva SOLO dall'ambiente: nessun nome di ambiente e' scritto qui.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

ENV_ENABLED = "GOOGLE_CALENDAR_ENABLED"
ENV_CLIENT_ID = "GOOGLE_CALENDAR_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_CALENDAR_CLIENT_SECRET"
ENV_REDIRECT_URI = "GOOGLE_CALENDAR_REDIRECT_URI"
ENV_NAMESPACE = "GOOGLE_CALENDAR_DEPLOYMENT_NAMESPACE"

_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class GoogleCalendarConfigError(Exception):
    """Configurazione Google Calendar assente o incompleta. Il messaggio non
    contiene mai un valore di configurazione o un segreto."""


def _get(environ, nome: str) -> str:
    return (environ.get(nome) or "").strip()


def is_enabled(environ=None) -> bool:
    """Vero solo se `GOOGLE_CALENDAR_ENABLED` vale, letteralmente, "true"."""
    environ = os.environ if environ is None else environ
    return _get(environ, ENV_ENABLED).lower() == "true"


def hook_deployment_namespace(environ=None) -> str | None:
    """SOLO per l'hook dell'Agenda e per il worker: il namespace se Google e'
    abilitato E il namespace e' configurato e valido, altrimenti None - MAI
    un'eccezione. Chi chiama con None fa un NO-OP controllato (§17): la
    mutazione CRM non deve mai dipendere da questa funzione."""
    environ = os.environ if environ is None else environ
    if not is_enabled(environ):
        return None
    ns = _get(environ, ENV_NAMESPACE)
    if not ns or not _NAMESPACE_RE.match(ns):
        return None
    return ns


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    deployment_namespace: str


def require_config(environ=None) -> GoogleConfig:
    """Per le rotte OAuth e per il worker reale. Solleva
    `GoogleCalendarConfigError` (sanitizzato) se Google non e' abilitato o la
    configurazione e' incompleta/non valida."""
    environ = os.environ if environ is None else environ
    if not is_enabled(environ):
        raise GoogleCalendarConfigError("Google Calendar non e' abilitato per questo ambiente")
    client_id = _get(environ, ENV_CLIENT_ID)
    client_secret = _get(environ, ENV_CLIENT_SECRET)
    redirect_uri = _get(environ, ENV_REDIRECT_URI)
    namespace = _get(environ, ENV_NAMESPACE)
    mancanti = [nome for nome, valore in (
        ("client_id", client_id), ("client_secret", client_secret),
        ("redirect_uri", redirect_uri), ("deployment_namespace", namespace),
    ) if not valore]
    if mancanti:
        raise GoogleCalendarConfigError(
            "Google Calendar e' abilitato ma la configurazione e' incompleta")
    if not _NAMESPACE_RE.match(namespace):
        raise GoogleCalendarConfigError(
            "Google Calendar: il namespace di deployment non e' valido")
    return GoogleConfig(client_id=client_id, client_secret=client_secret,
                        redirect_uri=redirect_uri, deployment_namespace=namespace)
