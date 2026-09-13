"""L'ammissione alla superficie Platform. P27-1.

`require_platform_admin` e' l'unica porta di `/api/platform`, ed e' una porta
DIVERSA da quelle del tenant - non una di quelle con una condizione in piu'.

PERCHE' NON RIUSARE `require_operator` E CONTROLLARE IL FLAG NELLA ROUTE

Perche' e' esattamente il modo in cui le due superfici tornerebbero a essere
una sola. Una dipendenza tenant che ammette e poi lascia alla route il compito
di verificare il flag produce due difetti che si vedono solo dopo:

* la route che dimentica il controllo e' aperta a qualunque operatore
  autenticato, e non c'e' nulla di strutturale che lo impedisca;
* il contesto che la route riceve e' uno scope di agenzia, quindi la
  tentazione successiva - passarlo a un repository tenant - e' a una riga di
  distanza.

Qui l'ammissione e' una dipendenza sua, dichiarata sul mount in `main.py`, e
cio' che restituisce e' un contesto che P27-1 non consegna a nessun costruttore
di query tenant.

COSA AMMETTE, ESATTAMENTE (decisione D4)

Il flag `is_platform_admin` basta. Non e' richiesto che l'operatore sia privo
di membership: la stessa identita' puo' essere platform admin su
`/api/platform` e `agency_owner` sulla propria agenzia nella superficie tenant,
e a separarle e' il CONTESTO - l'endpoint da cui si entra - non l'obbligo di
avere due account.

Questo NON riapre nulla, perche' la decisione D1 vive altrove: in
`core/scope.py` non esiste piu' un ramo cross-agency, quindi un platform admin
senza membership non ottiene dati tenant da nessuna parte, e uno CON membership
ottiene quelli della sua agenzia e solo quelli. Le due decisioni si reggono a
vicenda: D4 senza D1 sarebbe un'escalation.

LE TRE RISPOSTE, E PERCHE' SOLO UNA DELLE TRE SCRIVE UN AUDIT

* **401, nessuna riga.** Nessuna sessione viva. Non c'e' un amministratore che
  abbia tentato qualcosa: c'e' una richiesta anonima. Registrarla darebbe a
  chiunque, senza autenticarsi, un modo per scrivere righe in una tabella
  append-only - cioe' per riempirla. La traccia di questo traffico appartiene
  al log di accesso HTTP, non a un registro di atti amministrativi.
* **403, una riga `denied`.** Un operatore autenticato che NON e' platform
  admin ha chiesto la superficie di rete. Questo e' l'evento di sicurezza che
  il registro esiste per conservare (D2), e c'e' un attore reale a cui
  attribuirlo.
* **Ammesso, una riga `success`.** Chi e' entrato, quando, e da quale route.

Se la riga `success` non si riesce a scrivere, la richiesta si ferma con 503:
un atto amministrativo che non si riesce a registrare non avviene. La regola si
stabilisce adesso, mentre l'unica operazione della superficie e' innocua,
proprio perche' dopo P27-2 non sara' piu' il momento di discuterla.

Se non si riesce a scrivere la riga `denied`, il 403 resta comunque. Il rifiuto
non stava concedendo niente, e convertirlo in un 503 racconterebbe al chiamante
respinto qualcosa sullo stato interno del server.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from operator_auth.context import OperatorContext
from operator_auth.dependencies import (
    NOT_AUTHENTICATED_MESSAGE,
    AuthenticatedSession,
    optional_session,
)

from . import audit
from .enums import (
    ACTION_ADMISSION,
    AUDIT_UNAVAILABLE_MESSAGE,
    FORBIDDEN_MESSAGE,
    RESULT_DENIED,
    RESULT_SUCCESS,
)
from .exceptions import PlatformAuditUnavailable

logger = logging.getLogger(__name__)


def _admission_metadata(request: Request) -> dict:
    """Il path e il metodo, e nient'altro.

    `request.url.path` e non `str(request.url)`: la query string puo' portare
    filtri, identificatori e - un giorno, per errore di qualcun altro - un
    token. Un registro append-only e' il posto peggiore in cui far finire una
    stringa che nessuno ha guardato.
    """
    return {"path": request.url.path, "method": request.method}


def require_platform_admin(
    request: Request,
    session: AuthenticatedSession | None = Depends(optional_session),
) -> OperatorContext:
    """Il contesto del platform admin, oppure 401 / 403 / 503.

    Dipende da `optional_session`, che e' la sola cosa in tutto il progetto che
    trasforma un cookie in uno scope, ed e' cached per richiesta da FastAPI:
    dichiarare questa dipendenza sul mount E nella route costa una sola
    risoluzione di sessione, e quindi una sola riga di audit. E' una proprieta'
    su cui si appoggia il test
    `test_p27_1_platform_admission.py::test_the_admission_is_audited_once`.
    """
    if session is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_MESSAGE)

    context = session.context

    if not context.is_platform_admin:
        try:
            audit.record(
                action=ACTION_ADMISSION,
                actor=context,
                result=RESULT_DENIED,
                metadata=_admission_metadata(request),
            )
        except PlatformAuditUnavailable:
            # Il rifiuto resta. Vedi il docstring del modulo.
            logger.error(
                "platform_audit_log non scrivibile: rifiuto di %s su %s non registrato",
                audit.actor_label(context),
                request.url.path,
            )
        raise HTTPException(status_code=403, detail=FORBIDDEN_MESSAGE)

    try:
        audit.record(
            action=ACTION_ADMISSION,
            actor=context,
            result=RESULT_SUCCESS,
            metadata=_admission_metadata(request),
        )
    except PlatformAuditUnavailable as exc:
        logger.error(
            "platform_audit_log non scrivibile: ammissione di %s su %s rifiutata",
            audit.actor_label(context),
            request.url.path,
        )
        raise HTTPException(
            status_code=503, detail=AUDIT_UNAVAILABLE_MESSAGE
        ) from exc

    return context
