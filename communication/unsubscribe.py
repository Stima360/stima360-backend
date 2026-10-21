"""P29-3B.0 - la disiscrizione dal marketing, pubblica e firmata.

PERCHE' ESISTE PRIMA DELLE JOURNEY

Un'email di marketing senza un modo funzionante di dire "basta" non si manda.
Il registro del consenso (P29-1) sa gia' rappresentare la revoca da un link -
`source='unsubscribe_link'` e' nell'insieme ammesso dalla 062 - ma nessuna
rotta la scriveva: questo modulo e' quella rotta, e nient'altro.

IL LINK NON DICE CHI SEI, LO PROVA

`?contact_id=123` sarebbe una enumerazione: chiunque potrebbe disiscrivere
chiunque, e contare i contatti di un'agenzia. Il token porta agenzia e
contatto in chiaro - non e' un segreto, e' un indirizzo - ma li FIRMA con
HMAC-SHA256 su una chiave che sta solo sul server. Un token alterato non e'
"un contatto che non esiste": e' una firma che non torna, e non produce
nessuna lettura del database.

IDEMPOTENTE PER COSTRUZIONE

La chiave di idempotenza dell'evento di consenso include `issued_at`, cioe'
l'istante in cui QUEL link e' stato generato: cliccarlo dieci volte scrive un
evento solo. Un link generato dopo una nuova concessione ha un `issued_at`
diverso, e la sua revoca e' un evento nuovo - che e' giusto, perche' e' una
decisione nuova.

LA RISPOSTA E' NEUTRA, SEMPRE

Token valido, token gia' usato, token alterato, contatto sparito: la pagina
dice la stessa cosa. Non e' scortesia: una risposta diversa direbbe a chi
prova a caso quale token era vero.

COSA NON FA

Non tocca il consenso di SERVIZIO (la mail con il PDF della stima resta
dovuta), non ferma da solo una journey in corso - quello e' il tick, che
legge il consenso e chiude - e non manda niente.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any

from consent import service as consent_service
from consent.enums import (
    CHANNEL_EMAIL,
    ACTOR_SUBJECT,
    PURPOSE_MARKETING,
    SOURCE_UNSUBSCRIBE_LINK,
)
from consent import exceptions as consent_exceptions
from core import exceptions as core_exceptions
from operator_auth.context import SystemAgencyContext

logger = logging.getLogger(__name__)

ORIGIN = "public_unsubscribe"
SECRET_ENV = "COMMUNICATION_UNSUBSCRIBE_SECRET"
TOKEN_VERSION = "1"
#: 108 (Fase B): il token con AMBITO. `agency` e' il link di sempre (revoca
#: per questa agenzia); `platform` e' il link delle email della sequenza
#: sul contatto: "basta marketing STIMA360", registrato anche nella lista di
#: piattaforma. L'ambito e' NEL token firmato: nessuna pagina lo decide.
TOKEN_VERSION_SCOPED = "2"
SCOPE_AGENCY, SCOPE_PLATFORM = "agency", "platform"
SCOPES = frozenset({SCOPE_AGENCY, SCOPE_PLATFORM})
PUBLIC_PATH = "/api/public/communication/unsubscribe"
EVIDENCE_TYPE = "unsubscribe_token"

#: Il testo che ogni esito produce. Uno solo, di proposito.
NEUTRAL_MESSAGE = (
    "Se questo indirizzo era iscritto alle comunicazioni commerciali, "
    "non ne ricevera' altre. Le comunicazioni di servizio richieste "
    "restano attive."
)
#: Cio' che la pagina dice PRIMA del click, per ambito: chi clicca deve
#: sapere cosa sta chiedendo.
SCOPE_QUESTIONS = {
    SCOPE_AGENCY: "Vuoi smettere di ricevere comunicazioni commerciali da questa agenzia?",
    SCOPE_PLATFORM: ("Vuoi smettere di ricevere tutte le comunicazioni commerciali "
                     "STIMA360, da qualunque agenzia della rete?"),
}


class UnsubscribeNotConfigured(RuntimeError):
    """Manca la chiave di firma: non si emettono link che nessuno potra' verificare."""


def _secret() -> bytes | None:
    valore = os.getenv(SECRET_ENV, "")
    if len(valore) < 32:
        return None
    return valore.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(testo: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))
    except (ValueError, TypeError):
        return None


def _firma(chiave: bytes, payload: bytes) -> bytes:
    return hmac.new(chiave, payload, hashlib.sha256).digest()


def issue(agency_id: int, contact_id: int, *, issued_at: datetime | None = None,
          scope: str = SCOPE_AGENCY) -> str:
    """Il token per QUESTO contatto di QUESTA agenzia. Solleva senza chiave.

    Con `scope=agency` il token e' quello di sempre (versione 1, byte per
    byte); con `platform` e' la versione 2, che porta l'ambito firmato.
    """
    chiave = _secret()
    if chiave is None:
        raise UnsubscribeNotConfigured(
            f"{SECRET_ENV} is missing or shorter than 32 characters: refusing to "
            "issue an unsubscribe link that could not be verified"
        )
    if scope not in SCOPES:
        raise ValueError(f"unknown unsubscribe scope {scope!r}")
    quando = int((issued_at or datetime.now(timezone.utc)).timestamp())
    if scope == SCOPE_AGENCY:
        payload = f"{TOKEN_VERSION}:{int(agency_id)}:{int(contact_id)}:{quando}".encode("ascii")
    else:
        payload = (f"{TOKEN_VERSION_SCOPED}:{int(agency_id)}:{int(contact_id)}:{quando}:{scope}"
                   .encode("ascii"))
    return f"{_b64(payload)}.{_b64(_firma(chiave, payload))}"


def unsubscribe_url(base_url: str, agency_id: int, contact_id: int, *,
                    issued_at: datetime | None = None, scope: str = SCOPE_AGENCY) -> str:
    """L'URL da mettere nel template. `base_url` e' quello pubblico dell'app."""
    return (f"{base_url.rstrip('/')}{PUBLIC_PATH}?t="
            f"{issue(agency_id, contact_id, issued_at=issued_at, scope=scope)}")


def verify_scoped(token: str | None) -> dict[str, Any] | None:
    """`{agency_id, contact_id, issued_at, scope}` se la firma torna, altrimenti None.

    Nessuna lettura del database qui: un token invalido viene respinto prima
    di toccare qualunque cosa, e in tempo costante rispetto alla firma.
    """
    chiave = _secret()
    if chiave is None or not token or "." not in token or len(token) > 400:
        return None
    parte_payload, _, parte_firma = token.partition(".")
    payload, firma = _unb64(parte_payload), _unb64(parte_firma)
    if payload is None or firma is None:
        return None
    if not hmac.compare_digest(_firma(chiave, payload), firma):
        return None
    try:
        parti = payload.decode("ascii").split(":")
        if parti[0] == TOKEN_VERSION and len(parti) == 4:
            _, agenzia, contatto, quando = parti
            scope = SCOPE_AGENCY
        elif parti[0] == TOKEN_VERSION_SCOPED and len(parti) == 5 and parti[4] in SCOPES:
            _, agenzia, contatto, quando, scope = parti
        else:
            return None
        return {"agency_id": int(agenzia), "contact_id": int(contatto),
                "issued_at": int(quando), "scope": scope}
    except (ValueError, UnicodeDecodeError):
        return None


def verify(token: str | None) -> tuple[int, int, int] | None:
    """`(agency_id, contact_id, issued_at)` se la firma torna, altrimenti None."""
    esito = verify_scoped(token)
    if esito is None:
        return None
    return esito["agency_id"], esito["contact_id"], esito["issued_at"]


def idempotency_key(agency_id: int, contact_id: int, issued_at: int) -> str:
    return f"unsubscribe_link:{agency_id}:{contact_id}:{issued_at}"


def process(token: str | None) -> bool:
    """Esegue la disiscrizione. Restituisce se la firma era valida.

    Il valore di ritorno serve ai test e ai log, MAI alla risposta HTTP, che
    resta neutra. Un contatto sparito o di un'altra agenzia non e' un errore
    da raccontare: e' un token che non porta piu' a nessuno.
    """
    esito = verify_scoped(token)
    if esito is None:
        return False
    agency_id, contact_id, quando = esito["agency_id"], esito["contact_id"], esito["issued_at"]
    ctx = SystemAgencyContext(agency_id=agency_id, origin=ORIGIN)
    try:
        # 109: il link arriva in una EMAIL e disiscrive dal canale email:
        # non decide niente su SMS, WhatsApp o telefonate.
        revoca = consent_service.record_revocation(
            ctx,
            contact_id=contact_id,
            purpose=PURPOSE_MARKETING,
            channel=CHANNEL_EMAIL,
            source=SOURCE_UNSUBSCRIBE_LINK,
            actor_type=ACTOR_SUBJECT,
            evidence_type=EVIDENCE_TYPE,
            evidence_ref=f"{agency_id}:{contact_id}:{quando}",
            idempotency_key=idempotency_key(agency_id, contact_id, quando),
        )
        if esito["scope"] == SCOPE_PLATFORM:
            # 108: l'ambito era dichiarato NEL link firmato: "basta STIMA360".
            # La revoca locale sopra resta quella di sempre; in piu'
            # l'indirizzo entra nella lista di piattaforma. Nessun evento di
            # consenso viene scritto per conto di altre agenzie.
            consent_service.register_platform_optout(
                ctx, contact_id=contact_id, channel=CHANNEL_EMAIL, source=SOURCE_UNSUBSCRIBE_LINK,
                actor_type=ACTOR_SUBJECT,
                consent_event_id=(revoca.get("event") or {}).get("id"),
                idempotency_key=f"optout:{idempotency_key(agency_id, contact_id, quando)}",
            )
    except (consent_exceptions.ConsentError, core_exceptions.CoreError) as exc:
        # Contatto assente nello scope, o chiave gia' usata da un altro tenant
        # (impossibile per costruzione, ma fail closed): niente da scrivere.
        logger.info("unsubscribe: nothing recorded (%s)", type(exc).__name__)
    return True
