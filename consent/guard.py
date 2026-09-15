"""P29-1.5 - la guardia centrale del consenso marketing.

UNA domanda, e una sola:

    questo contatto possiede ORA un consenso marketing valido?

NON risponde a "e' opportuno mandargli M2", "il lead e' ancora aperto", "ha
risposto", "ha un appuntamento", "ha un'email", "ha un numero WhatsApp".
Quelle sono decisioni del motore di comunicazione, e tenerle fuori di qui e' il
motivo per cui questa funzione puo' restare corta, deterministica e leggibile.

UN SOLO CONSENSO PER DUE CANALI

Email e WhatsApp condividono lo stesso consenso, quindi la guardia non ha un
parametro `channel` e non deve averlo: se un giorno servisse distinguere, la
differenza dovrebbe nascere nel modello del consenso, non in un ramo qui.

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
    DECISION_GRANTED,
    DECISION_REVOKED,
    PROJECTION_COLUMNS,
    PURPOSE_MARKETING,
    REASON_ALLOW_EXPLICIT_GRANT,
    REASON_ALLOW_LEGACY_GRANT,
    REASON_DENY_INCONSISTENT_STATE,
    REASON_DENY_NEVER_GIVEN,
    REASON_DENY_REVOKED,
    STATUS_GRANTED,
    STATUS_NEVER_GIVEN,
    STATUS_REVOKED,
)
from .service import state_from_projection


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


def _coerente_con_evento(contact: dict[str, Any], event: dict[str, Any], columns) -> bool:
    """La proiezione rappresenta davvero l'ultimo evento?

    Si controlla il minimo che il percorso di scrittura garantisce sempre, e
    non un di piu' che renderebbe fragile la guardia:

      * `notice_id` NON e' richiesto - il funnel pubblico non ha una notice
        certificata e i grant validi non devono essere bloccati per quello;
      * `source` NON e' richiesta - il modello legacy non la garantiva;
      * `marketing_consent_at` non deve COINCIDERE con l'evento, deve esserci:
        confrontare due timestamp al microsecondo trasformerebbe un dettaglio
        di precisione in un rifiuto.
    """
    flag = contact.get(columns["flag"])
    granted_at = contact.get(columns["granted_at"])
    revoked_at = contact.get(columns["revoked_at"])

    if event["decision"] == DECISION_GRANTED:
        return flag is True and revoked_at is None and granted_at is not None
    if event["decision"] == DECISION_REVOKED:
        return flag is not True and revoked_at is not None
    # Una terza decisione non e' rappresentabile nel database (CHECK della 062).
    # Se arriva qui, qualcosa di piu' grande e' rotto: fail closed.
    return False


def can_send_marketing(ctx, contact_id: int) -> MarketingSendDecision:
    """Questo contatto possiede ORA un consenso marketing valido?

    L'unica funzione pubblica di questo modulo, e l'unica implementazione della
    decisione: non esistono varianti, scorciatoie o versioni "veloci". Da P29
    in avanti ogni invio marketing - email o WhatsApp - la interroga
    IMMEDIATAMENTE PRIMA del dispatch, mai all'inizio di una sequenza e mai su
    un valore conservato.

    Solleva `NotFoundError` per un contatto che non esiste O che appartiene a
    un'altra agenzia: e' lo stesso comportamento del resto del dominio, ed e'
    quello che impedisce di scoprire l'esistenza dei contatti altrui chiedendo
    il loro consenso.
    """
    columns = PROJECTION_COLUMNS[PURPOSE_MARKETING]
    contact, event = repository.read_send_decision_inputs(
        ctx, contact_id, PURPOSE_MARKETING
    )

    proiezione = state_from_projection(contact, PURPOSE_MARKETING)
    agency_id = contact.get("agency_id")

    def decisione(allowed, reason, *, state, legacy):
        return MarketingSendDecision(
            allowed=allowed,
            reason=reason,
            state=state,
            contact_id=contact["id"],
            agency_id=agency_id,
            legacy=legacy,
            event_id=event["id"] if event else None,
            decided_at=event["decided_at"] if event else proiezione["granted_at"],
            # La provenienza REALE, o niente.
            #
            # `state_from_projection` sostituisce "legacy" quando un contatto
            # risulta concesso senza source: e' comodo per la scheda contatto,
            # ma qui sarebbe una provenienza inventata su una decisione di
            # autorizzazione. Il segnale che non esiste un evento autoritativo
            # e' gia' `legacy=True`, e basta: se la colonna e' NULL, `source`
            # e' None.
            source=(event["source"] if event else contact.get(columns["source"])),
        )

    # ------------------------------------------------------------------
    # CON EVENTI: l'evento piu' recente e' l'autorita'.
    #
    # "Piu' recente" e' quello che il dominio ha gia' deciso - `decided_at`
    # decrescente, `id` come spareggio - e non MAX(id): un evento deciso prima
    # ma arrivato dopo non deve far arretrare lo stato, ed e' la ragione per
    # cui il percorso di scrittura rilegge l'ultimo evento invece di proiettare
    # quello appena inserito.
    # ------------------------------------------------------------------
    if event is not None:
        if not _coerente_con_evento(contact, event, columns):
            return decisione(
                False, REASON_DENY_INCONSISTENT_STATE,
                state=proiezione["status"], legacy=False,
            )
        if event["decision"] == DECISION_GRANTED:
            return decisione(
                True, REASON_ALLOW_EXPLICIT_GRANT,
                state=STATUS_GRANTED, legacy=False,
            )
        return decisione(
            False, REASON_DENY_REVOKED, state=STATUS_REVOKED, legacy=False,
        )

    # ------------------------------------------------------------------
    # SENZA EVENTI: vale la proiezione legacy, letta con la derivazione che il
    # dominio usa gia' (`state_from_projection`). Nessuna reinterpretazione,
    # nessun evento fabbricato: cio' che c'e' scritto, letto come e' sempre
    # stato letto.
    #
    # `granted` senza provenienza e' esattamente il caso dei 12 contatti del
    # censimento P29-1.0: acconsentirono prima che esistesse il registro, e
    # bloccarli sarebbe stato inventare una revoca che nessuno ha chiesto.
    # ------------------------------------------------------------------
    if proiezione["status"] == STATUS_GRANTED:
        # Concesso e revocato insieme: impossibile per il CHECK della 063, ma
        # se il database lo mostrasse la guardia non e' il posto in cui
        # indovinare quale dei due e' vero.
        if contact.get(columns["revoked_at"]) is not None:
            return decisione(
                False, REASON_DENY_INCONSISTENT_STATE,
                state=proiezione["status"], legacy=True,
            )
        return decisione(
            True, REASON_ALLOW_LEGACY_GRANT, state=STATUS_GRANTED, legacy=True,
        )

    if proiezione["status"] == STATUS_REVOKED:
        return decisione(
            False, REASON_DENY_REVOKED, state=STATUS_REVOKED, legacy=True,
        )

    return decisione(
        False, REASON_DENY_NEVER_GIVEN, state=STATUS_NEVER_GIVEN, legacy=True,
    )
