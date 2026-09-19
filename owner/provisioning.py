"""LMC-1A - il collegamento automatico PRE-INCARICO fra proprietario e stima.

Il flusso che questo modulo serve:

    /api/salva_stima
    -> bridge_public_stima riuscito (linked | already_linked, con un contact_id)
    -> contact
    -> owner_account esistente, oppure creato
    -> owner_stima_access

Nessun operatore lo avvia: e' il funnel pubblico, subito dopo il bridge CRM,
e come ogni altro sottosistema che il funnel tocca dopo aver scritto la stima
(Seller Intelligence, Property Watch) e' fail-open. Il punto
d'ingresso per `main.py` e' `safe_provision_for_public_stima`, che non lascia
uscire nessuna eccezione: un errore OWNER non puo' costare una stima.

DA DOVE VIENE L'AGENZIA

Dal `SystemAgencyContext` che `main.py` ha RILETTO dalla riga `stime` appena
committata (`_persisted_public_stima_system_context`, P27-6): lo stesso oggetto
che e' andato al bridge, quindi contatto, lead e grant non possono finire in
agenzie diverse. Il contesto e' ammesso solo se e' esattamente quel tipo con
quell'origine - lo stesso guardiano di `core.repository.bridge_public_stima` -
e la sua agenzia viene CONFRONTATA con quella del contatto e della stima nel
repository, dentro la transazione che scrive. Nessun `agency_id` transita
come parametro di questo modulo.

QUANDO SI PROCEDE

Solo con un collegamento CRM reale: `bridge_result` presente, con `status` in
`PROVISIONABLE_BRIDGE_STATUSES` e un `contact_id`. `conflict` (identita'
ambigua), `skipped` (contatto archiviato o identita' insufficiente), un bridge
che ha sollevato (`bridge_result` None): nessuna riga. Un account owner senza
un contatto certo sarebbe un accesso concesso a nessuno in particolare.

`already_linked` provisiona come `linked`: e' la stessa stima ripassata di
qui, e il grant e' idempotente.
"""

from __future__ import annotations

import logging
from typing import Any

from operator_auth.context import SystemAgencyContext

from . import repository

logger = logging.getLogger(__name__)

#: L'origine del solo contesto di sistema che questo modulo accetta.
PUBLIC_STIMA_ORIGIN = "public_stima"

#: Gli esiti del bridge CRM che dimostrano un contatto reale e collegato.
PROVISIONABLE_BRIDGE_STATUSES = frozenset({"linked", "already_linked"})

#: Il valore tecnico scritto in `owner_stima_access.granted_by` e nell'audit:
#: dice che il grant l'ha dato il provisioning automatico, non un operatore.
PROVISIONING_ACTOR = "LMC_PROVISIONING"


class ProgrammingError(Exception):
    """Un contesto che questo modulo non deve accettare.

    Non e' un errore del chiamante pubblico - il funnel non sceglie il contesto
    - ma un difetto di chi ha cablato la chiamata. Deve emergere come guasto in
    `provision_for_public_stima`; `safe_*` lo inghiotte come ogni altro, perche'
    la stima pubblica non deve pagarlo.
    """


def _skipped(stima_id: int, reason: str) -> dict[str, Any]:
    return {"status": "skipped", "stima_id": stima_id, "reason": reason,
            "owner_account_id": None, "access_id": None,
            "account_created": False, "access_created": False}


def provision_for_public_stima(bridge_ctx, *, stima_id: int, bridge_result) -> dict[str, Any]:
    """Collega il contatto del bridge alla stima attraverso un account owner.

    Ritorna un dict con `status`:

        skipped              nessun collegamento CRM valido; niente scritto
        provisioned          grant creato (account creato o riusato)
        already_provisioned  grant gia' presente; niente scritto
        account_disabled     account esistente ma disabilitato; nessun grant

    Solleva `ProgrammingError` per un contesto non ammesso e propaga gli
    errori del repository (tenant incoerente, contatto o stima assenti,
    database). E' `safe_provision_for_public_stima` a non sollevare mai.
    """
    if type(bridge_ctx) is not SystemAgencyContext:
        raise ProgrammingError(
            "provision_for_public_stima requires a SystemAgencyContext, "
            f"received {type(bridge_ctx).__name__}"
        )
    if bridge_ctx.origin != PUBLIC_STIMA_ORIGIN:
        raise ProgrammingError(
            f"provision_for_public_stima refuses a context with origin {bridge_ctx.origin!r}"
        )

    if not bridge_result:
        return _skipped(stima_id, "no_bridge_result")
    status = bridge_result.get("status")
    if status not in PROVISIONABLE_BRIDGE_STATUSES:
        return _skipped(stima_id, f"bridge_{status}")
    contact_id = bridge_result.get("contact_id")
    if contact_id is None:
        return _skipped(stima_id, "no_contact_id")

    esito = repository.provision_stima_access(
        bridge_ctx.require_agency(),
        contact_id=int(contact_id),
        stima_id=int(stima_id),
        granted_by=PROVISIONING_ACTOR,
    )
    return {**esito, "stima_id": int(stima_id)}


def safe_provision_for_public_stima(bridge_ctx, *, stima_id: int, bridge_result) -> dict[str, Any] | None:
    """`provision_for_public_stima` che non solleva mai.

    E' l'UNICA funzione di questo modulo che `main.py` deve chiamare. Qualunque
    eccezione - tenant incoerente, database irraggiungibile, contesto sbagliato
    - finisce nel log applicativo con lo stesso formato degli altri wrapper del
    funnel (`property_watch_initialization_failed`) e
    la funzione ritorna None. Nessun dato personale nel log: id e tipo
    dell'errore.
    """
    try:
        esito = provision_for_public_stima(bridge_ctx, stima_id=stima_id, bridge_result=bridge_result)
    except Exception as exc:  # noqa: BLE001 - intentional public-flow isolation
        logger.error(
            "owner_provisioning_failed stima_id=%s error_type=%s",
            stima_id, type(exc).__name__,
        )
        return None
    logger.info(
        "owner_provisioning stima_id=%s status=%s owner_account_id=%s access_id=%s "
        "account_created=%s access_created=%s reason=%s",
        stima_id, esito.get("status"), esito.get("owner_account_id"), esito.get("access_id"),
        esito.get("account_created"), esito.get("access_created"), esito.get("reason"),
    )
    return esito
