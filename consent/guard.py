"""P29-1.5 - la guardia centrale del consenso marketing.

UNA domanda, e una sola:

    questo contatto possiede ORA un consenso marketing valido?

NON risponde a "e' opportuno mandargli M2", "il lead e' ancora aperto", "ha
risposto", "ha un appuntamento", "ha un'email", "ha un numero WhatsApp".
Quelle sono decisioni del motore di comunicazione, e tenerle fuori di qui e' il
motivo per cui questa funzione puo' restare corta, deterministica e leggibile.

UN CONSENSO PER CANALE (109, C2)

La domanda ha un CANALE: email, sms, whatsapp o phone. Non esiste un consenso
"per tutti i canali" e non esiste una chiamata senza canale: `channel` e'
obbligatorio, per nome, e un valore fuori dall'insieme e' un errore di
programmazione, non un rifiuto. La guardia legge la proiezione PER CANALE
(`contact_channel_consents`) e l'ultimo evento DI QUEL CANALE; il vecchio
flag `contacts.marketing_consent` non entra nella decisione: e' un
riassunto per chi legge la scheda, e un flag storico senza un evento sul
canale e' `deny_never_given` con `legacy=True` - si vede che c'era una
spunta, non si manda niente. Nessuna autorizzazione presunta.

LE PROPRIETA', E COME SONO OTTENUTE

* tenant scoped  - il contatto e l'evento passano dai predicati di agenzia gia'
                   esistenti (`core.scope` per `contacts`, `consent.scope` per
                   `consent_events`). Non c'e' un secondo sistema di tenancy.
* fail closed    - ogni stato che non torna produce un rifiuto. Non esiste un
                   ramo che, nel dubbio, autorizzi.
* senza cache    - nessuno stato di modulo, nessuna memoizzazione: ogni
                   chiamata legge il database. Una revoca deve avere effetto al
                   controllo successivo, non al prossimo riavvio.
* read only      - nessun INSERT, nessun UPDATE, nessun commit, nessun
                   FOR UPDATE. La guardia non ripara niente: se trova uno stato
                   impossibile lo DICE e rifiuta, e la correzione resta una
                   decisione di una persona.
* deterministica - stessi dati, stessa risposta.

PRECEDENZA: EVENTI, POI PROIEZIONE

Se esiste anche un solo evento P29 per `marketing`, quell'evento e' l'autorita'
e la proiezione deve essergli coerente. Se non ne esiste nessuno, la proiezione
legacy e' l'unica cosa che c'e' e vale come tale - i contatti censiti in
P29-1.0 (12 concessi senza storico) continuano a ricevere, senza che nessuno
fabbrichi per loro un evento che non e' mai accaduto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import repository
from .enums import (
    ALLOWING_REASONS,
    CHANNELS,
    DECISION_GRANTED,
    PROJECTION_COLUMNS,
    PURPOSE_MARKETING,
    REASON_ALLOW_EXPLICIT_GRANT,
    REASON_DENY_INCONSISTENT_STATE,
    REASON_DENY_NEVER_GIVEN,
    REASON_DENY_PLATFORM_OPTOUT,
    REASON_DENY_REVOKED,
    STATUS_GRANTED,
    STATUS_NEVER_GIVEN,
    STATUS_REVOKED,
)
from .scope import ProgrammingError


@dataclass(frozen=True)
class MarketingSendDecision:
    """L'esito della guardia. Piccolo di proposito.

    `allowed` e' la risposta; `reason` e' il perche', con un valore stabile che
    puo' finire in un log o sulla riga di un messaggio non partito. Il resto
    serve a diagnosticare senza dover rifare la query.

    Congelato: una decisione che il chiamante puo' modificare dopo averla
    ricevuta non e' una decisione.
    """

    allowed: bool
    reason: str
    state: str
    contact_id: int
    agency_id: int | None
    legacy: bool
    event_id: int | None = None
    decided_at: Any = None
    source: str | None = None
    #: 109: il canale su cui la decisione e' stata presa.
    channel: str | None = None

    def __post_init__(self) -> None:
        # `allowed` e `reason` non possono raccontare due storie diverse.
        if self.allowed != (self.reason in ALLOWING_REASONS):
            raise AssertionError(
                f"decisione incoerente: allowed={self.allowed} reason={self.reason!r}"
            )

    def __bool__(self) -> bool:
        """Rifiuta la verifica implicita. Sempre.

        `if can_send_marketing(...)` e' la riga che non deve poter esistere:
        legge come un controllo di autorizzazione e non lo e'. Un giorno
        qualcuno la scriverebbe attorno a una funzione che restituisce
        un'eccezione, un None o un oggetto diverso, e continuerebbe a
        sembrare giusta.

        Non basta TOGLIERE `__bool__`: un dataclass senza sarebbe sempre
        vero, e un rifiuto diventerebbe silenziosamente un permesso. Quindi
        solleva, e dice dove guardare.
        """
        raise TypeError(
            "MarketingSendDecision must be checked via .allowed "
            "(es. `decision = can_send_marketing(ctx, contact_id); "
            "if decision.allowed: ...`)"
        )


def _canale(channel) -> str:
    """Il canale chiesto, o un errore di programmazione: mai un default."""
    if channel not in CHANNELS:
        raise ProgrammingError(
            f"can_send_marketing needs a channel in {sorted(CHANNELS)}, got {channel!r}")
    return channel


def _optout_di_piattaforma_dopo(contact: dict[str, Any], granted_at) -> bool:
    """109: l'indirizzo DEL CANALE ha detto "basta STIMA360" DOPO questa
    concessione? Un opt-out piu' vecchio della concessione non la blocca: una
    nuova manifestazione documentata vince su un vecchio rifiuto, mentre un
    rifiuto successivo vince su qualunque concessione, di qualunque agenzia.
    """
    optout_at = contact.get("platform_optout_at")
    if optout_at is None:
        return False
    if granted_at is None:
        return True
    return optout_at >= granted_at


def _coerente_con_riga(riga: dict[str, Any] | None, event: dict[str, Any]) -> bool:
    """La proiezione per canale rappresenta davvero l'ultimo evento del canale?"""
    if riga is None:
        return False
    if event["decision"] == DECISION_GRANTED:
        return riga["status"] == STATUS_GRANTED and riga["granted_at"] is not None and riga["revoked_at"] is None
    return riga["status"] == STATUS_REVOKED and riga["revoked_at"] is not None


def can_send_marketing(ctx, contact_id: int, *, channel: str) -> MarketingSendDecision:
    """Questo contatto possiede ORA un consenso marketing valido SU QUESTO CANALE?

    L'unica funzione pubblica di questo modulo, e l'unica implementazione della
    decisione: non esistono varianti, scorciatoie o versioni "veloci". Ogni
    invio marketing la interroga IMMEDIATAMENTE PRIMA del dispatch, con il
    canale del messaggio, mai all'inizio di una sequenza e mai su un valore
    conservato.

    Solleva `NotFoundError` per un contatto che non esiste O che appartiene a
    un'altra agenzia.
    """
    canale = _canale(channel)
    contact, event, riga = repository.read_send_decision_inputs(
        ctx, contact_id, PURPOSE_MARKETING, channel=canale
    )
    return _decidi(contact, event, riga, canale)


def can_send_marketing_bulk(ctx, contact_ids, *, channel: str):
    """La stessa domanda, per piu' contatti: `{contact_id: MarketingSendDecision}`.

    NON e' una seconda implementazione: legge gli stessi ingressi con la
    stessa query (una riga per contatto) e li passa a `_decidi`. Un contatto
    fuori dallo scope non compare nel risultato.
    """
    canale = _canale(channel)
    ingressi = repository.read_send_decision_inputs_bulk(
        ctx, contact_ids, PURPOSE_MARKETING, channel=canale
    )
    return {cid: _decidi(contact, event, riga, canale)
            for cid, (contact, event, riga) in ingressi.items()}


def _decidi(contact, event, riga, channel: str) -> MarketingSendDecision:
    """LA DECISIONE. Pura: righe gia' lette, nessun accesso al database.

        schema 109 assente                 -> deny_inconsistent_state
        nessun evento sul canale           -> deny_never_given
                                              (legacy=True se il vecchio flag e' TRUE:
                                              una spunta storica, nessuna autorizzazione)
        evento e proiezione in disaccordo  -> deny_inconsistent_state
        ultimo evento: revoca              -> deny_revoked
        ultimo evento: concessione, ma opt-out di piattaforma sul (canale,
        indirizzo) non precedente          -> deny_platform_optout
        ultimo evento: concessione         -> allow_explicit_grant
    """
    columns = PROJECTION_COLUMNS[PURPOSE_MARKETING]
    agency_id = contact.get("agency_id")
    spunta_storica = contact.get(columns["flag"]) is True

    def decisione(allowed, reason, *, state, legacy):
        return MarketingSendDecision(
            allowed=allowed,
            reason=reason,
            state=state,
            contact_id=contact["id"],
            agency_id=agency_id,
            legacy=legacy,
            event_id=event["id"] if event else None,
            decided_at=event["decided_at"] if event else None,
            source=event["source"] if event else None,
            channel=channel,
        )

    if contact.get("consent_schema_ready") is False:
        return decisione(False, REASON_DENY_INCONSISTENT_STATE, state=STATUS_NEVER_GIVEN, legacy=False)
    if event is None:
        if riga is not None:
            # Una riga per canale senza un evento che la spieghi: non si indovina.
            return decisione(False, REASON_DENY_INCONSISTENT_STATE, state=riga["status"], legacy=False)
        return decisione(False, REASON_DENY_NEVER_GIVEN, state=STATUS_NEVER_GIVEN, legacy=spunta_storica)
    if not _coerente_con_riga(riga, event):
        return decisione(False, REASON_DENY_INCONSISTENT_STATE,
                         state=riga["status"] if riga else STATUS_NEVER_GIVEN, legacy=False)
    if event["decision"] == DECISION_GRANTED:
        if _optout_di_piattaforma_dopo(contact, event["decided_at"]):
            return decisione(False, REASON_DENY_PLATFORM_OPTOUT, state=STATUS_GRANTED, legacy=False)
        return decisione(True, REASON_ALLOW_EXPLICIT_GRANT, state=STATUS_GRANTED, legacy=False)
    return decisione(False, REASON_DENY_REVOKED, state=STATUS_REVOKED, legacy=False)
