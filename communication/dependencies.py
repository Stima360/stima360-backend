"""P29-2.6E - chi puo' chiedere un giro di dispatch.

IL CONFINE NON E' L'AUTENTICAZIONE

La rotta di dispatch non legge e non scrive UN record: svuota la coda di
UN'AGENZIA INTERA, su tutti i contatti, assegnati e non. Lo fa di proposito -
`dispatch_batch` costruisce un `SystemAgencyContext` (D1) proprio perche' un
dispatcher che ereditasse il restringimento per `assigned_agent_id` salterebbe
in silenzio i messaggi dei contatti non assegnati a chi lo invoca.

Ne segue che "sessione valida" non e' la soglia giusta. Con la sola
autenticazione, QUALUNQUE agente umano dell'agenzia potrebbe far partire
l'intera coda email - e non gliene serve il permesso di leggere quei contatti,
perche' il dispatcher non li legge con il suo scope. L'account dedicato al cron
non risolve niente: se la rotta ammette `agent`, la capacita' ce l'hanno tutti
gli `agent`.

LA SOGLIA E' GIA' SCRITTA, E NON SI CHIAMA RBAC NUOVO

`operator_auth/permissions.py` e' la matrice approvata in P26-1. La riga che
descrive esattamente cio' che il dispatch fa e':

    See all agency records   platform_admin YES | agency_owner YES |
                             agency_admin YES | agent NO

`OperatorContext.sees_all_agency_records` e' gia' la proprieta' che la legge.
Un giro di dispatch agisce su tutti i record dell'agenzia: chi non li vede
tutti non puo' nemmeno farli partire tutti. Non c'e' nessuna regola nuova da
inventare - c'e' una regola esistente da applicare.

Perche' `sees_all_agency_records` e non `require_owner_admin_context`: quello e'
lo scope di OWNER Admin, e la sua soglia e' `agency_owner` perche' li' si
gestiscono i conti dei proprietari ("Manage agency users"). Il dispatch non
gestisce persone; opera sui record dell'agenzia. Applicare la soglia di OWNER
Admin escluderebbe `agency_admin`, che quei record li vede tutti - sarebbe piu'
stretta del necessario e, soprattutto, sarebbe una soglia presa da un'altra
riga della matrice.

IL PLATFORM ADMIN NON GUADAGNA NIENTE DA QUI

`sees_all_agency_records` e' True per un platform admin, ma l'agenzia continua
a venire da dove veniva: `ctx.require_agency()`, cioe' la membership o il
contesto di acting di P28. Un platform admin senza agenzia vincolata prende
403 da `PlatformAdminAgencyRequired` come prima. Nessun ramo cross-tenant nasce
qui, e `agency_id` non e' accettato dal payload in nessun caso (422).

403 E NON 401: un agente e' autenticato benissimo. Non e' autorizzato.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context

#: Il messaggio del rifiuto. Nomina la capacita', non i ruoli: se un giorno la
#: matrice cambiasse riga, il testo resterebbe vero.
DISPATCH_FORBIDDEN_MESSAGE = (
    "this operation acts on every record of the agency and requires an "
    "operator who sees them all"
)


def require_dispatch_context(
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
) -> OperatorContext:
    """La sessione, con in piu' la sola riga di matrice che il dispatch tocca.

    L'autenticazione resta identica - solo sessione, nessun Basic, agenzia
    derivata sul server (P26-5). Qui si aggiunge l'autorizzazione, che prima
    non c'era.
    """
    if ctx.sees_all_agency_records:
        return ctx
    raise HTTPException(status_code=403, detail=DISPATCH_FORBIDDEN_MESSAGE)
