"""LMC-1B - "Accedi a La Mia Casa": dall'indirizzo email alla owner session.

    POST /api/owner/portal/auth/request-link  {"email": "..."}
      -> normalizzazione
      -> lookup degli account eleggibili (uno per agenzia)
      -> per ciascuno, in UNA transazione: token di login + messaggio nel ledger
      -> 204, sempre uguale
    ... il proprietario apre il link
      -> POST /api/owner/portal/auth/token  (rotta esistente, invariata)
      -> owner_sessions + cookie, il flusso di autenticazione owner di sempre

QUESTO MODULO NON CREA UN SECONDO SISTEMA DI AUTENTICAZIONE. Emette una
credenziale con la fabbrica che gia' esiste (`owner_access_tokens`, sha256,
`token_type='login'`) e si ferma li': chi la consuma, come nasce la sessione e
come vive il cookie sono decisioni gia' prese dall'Owner Portal, e restano
dove sono. `stime.token` - il token pubblico della stima dettagliata - non
c'entra e non viene mai usato per autenticare nessuno.

LA RISPOSTA NON DICE NIENTE

La route risponde 204 a corpo vuoto in OGNI caso: indirizzo sconosciuto,
account disabilitato, nessun accesso valido, rate limit raggiunto, ledger
irraggiungibile, database rotto. Chi prova indirizzi altrui non impara se
esistono. Cio' che il server sa lo scrive nel log, dove nessun estraneo guarda,
e senza l'indirizzo: un log che riporta l'email trasformerebbe l'enumerazione
in un problema di chi legge i log invece che risolverla.

Fail-CLOSED sull'accesso, fail-NEUTRAL sulla risposta: un dubbio non produce
mai un token, e non produce mai nemmeno una risposta diversa.

TOKEN E MESSAGGIO, UNA TRANSAZIONE SOLA

`communication_service.enqueue` accetta il cursore del chiamante e non
committa: il token e la riga del ledger nascono quindi insieme e cadono
insieme. Se l'accodamento fallisce, il token non e' mai esistito - niente
credenziali inutilizzabili che occupano il rate limit di chi riprova - e il
ledger non ha bisogno di nessuna cancellazione, che sarebbe l'unica
alternativa e che questo dominio non ammette. Se invece a fallire e' il
dispatch (dopo il commit) il messaggio resta in coda con la sua storia, che e'
esattamente il mestiere di P29.

MULTI-AGENZIA

Un account = un tenant = un link. La stessa email puo' essere il contatto di
piu' agenzie: ognuna riceve il proprio token e il proprio messaggio nel proprio
ledger, con il proprio `SystemAgencyContext`. Nessun account globale, nessun
link che attraversa due agenzie, e un fallimento su una non ferma le altre.
"""

from __future__ import annotations

import logging
from typing import Any

from communication import service as communication_service
from core.database import core_cursor
from core.normalization import normalize_email
from operator_auth.context import SystemAgencyContext

from . import login_email
from . import repository as owner_repository

logger = logging.getLogger(__name__)

#: L'origine del contesto di sistema con cui questo flusso accoda (P26-1).
LOGIN_ORIGIN = "owner_login"

#: Quanto vive il magic link.
TOKEN_TTL_MINUTES = 30

#: Quanti link un account puo' CHIEDERE, e in quanti minuti. Si contano le
#: richieste fatte, non i link ancora aperti: usarne uno o revocarlo non
#: libera un posto, solo il passare del tempo lo libera.
RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW_MINUTES = 15

#: Chi ha chiesto il token, scritto in `owner_access_tokens.created_by`.
LOGIN_ACTOR = "LMC_LOGIN"

#: Il motivo e il template del messaggio, per il ledger P29.
REASON_CODE = "owner_login_link"
TEMPLATE_KEY = "owner_login_link"
TEMPLATE_VERSION = 1


def _esito(requested=0, sent=0, rate_limited=0, failed=0) -> dict[str, int]:
    return {"requested": requested, "sent": sent,
            "rate_limited": rate_limited, "failed": failed}


def _emetti(candidato: dict[str, Any]) -> str:
    """Token e messaggio per UN account, in una transazione. Ritorna l'esito.

    'sent' qui significa accodato: l'invio vero e' del dispatcher P29.
    """
    account_id = candidato["owner_account_id"]
    agency_id = candidato["agency_id"]
    destinazione = candidato["email"]

    with core_cursor(commit=True) as (_conn, cur):
        token_row, raw = owner_repository.issue_login_token_with_cursor(
            cur,
            owner_account_id=account_id,
            agency_id=agency_id,
            minutes=TOKEN_TTL_MINUTES,
            created_by=LOGIN_ACTOR,
            max_recent=RATE_LIMIT_MAX,
            window_minutes=RATE_LIMIT_WINDOW_MINUTES,
        )
        if token_row is None:
            return "rate_limited"

        oggetto, corpo = login_email.render(token=raw, minutes=TOKEN_TTL_MINUTES)
        communication_service.enqueue(
            SystemAgencyContext(agency_id=agency_id, origin=LOGIN_ORIGIN),
            contact_id=candidato["contact_id"],
            channel="email",
            communication_type="service",
            mode="automatic",
            reason_code=REASON_CODE,
            template_key=TEMPLATE_KEY,
            template_version=TEMPLATE_VERSION,
            destination_snapshot=destinazione,
            subject_snapshot=oggetto,
            rendered_body=corpo,
            idempotency_key=f"{REASON_CODE}:{token_row['id']}",
            metadata={"owner_access_token_id": token_row["id"],
                      "expires_at": str(token_row["expires_at"])},
            cur=cur,
        )
        owner_repository.audit_with_cursor(
            cur, "login_link_requested", account_id,
            etype="owner_token", eid=token_row["id"],
            meta={"token_type": "login", "created_by": LOGIN_ACTOR},
        )
    return "sent"


def request_login_link(email) -> dict[str, int]:
    """Emette un magic link per ogni account eleggibile con quell'indirizzo.

    Ritorna un riepilogo NUMERICO per il log e per i test. Non lo vede nessun
    client: la route risponde 204 comunque sia andata.

    Un fallimento su un account non ferma gli altri: ogni emissione ha la sua
    transazione e il suo `except`.
    """
    normalizzata = normalize_email(email if isinstance(email, str) else None)
    if not normalizzata:
        return _esito()

    candidati = owner_repository.find_login_candidates(normalizzata)
    esito = _esito(requested=len(candidati))
    for candidato in candidati:
        try:
            risultato = _emetti(candidato)
        except Exception as exc:  # noqa: BLE001 - un tenant rotto non ferma gli altri
            esito["failed"] += 1
            logger.error(
                "owner_login_link_failed owner_account_id=%s agency_id=%s error_type=%s",
                candidato.get("owner_account_id"), candidato.get("agency_id"),
                type(exc).__name__,
            )
            continue
        if risultato == "rate_limited":
            esito["rate_limited"] += 1
            logger.info(
                "owner_login_link_rate_limited owner_account_id=%s agency_id=%s",
                candidato.get("owner_account_id"), candidato.get("agency_id"),
            )
        else:
            esito["sent"] += 1
            logger.info(
                "owner_login_link_queued owner_account_id=%s agency_id=%s",
                candidato.get("owner_account_id"), candidato.get("agency_id"),
            )
    return esito


def safe_request_login_link(email) -> dict[str, int] | None:
    """`request_login_link` che non solleva mai.

    E' l'unica funzione che la route chiama. Anche un guasto prima del ciclo -
    il lookup, la normalizzazione, il database irraggiungibile - deve produrre
    la stessa risposta neutra di un indirizzo sconosciuto, altrimenti il tempo
    o il codice di errore diventano essi stessi un canale di enumerazione.
    """
    try:
        return request_login_link(email)
    except Exception as exc:  # noqa: BLE001 - la risposta non cambia mai
        logger.error("owner_login_link_lookup_failed error_type=%s", type(exc).__name__)
        return None
