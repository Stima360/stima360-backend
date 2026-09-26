"""A30-7 - la sincronizzazione ESPLICITA delle richieste di sopralluogo dal sito.

`POST /api/appointments/legacy-requests/sync` porta nell'Agenda, come
richieste (`requested`), le preferenze di sopralluogo che il form pubblico ha
salvato in `stime_dettagliate` e che non sono ancora state importate. Riusa
l'import idempotente di A30-6 (`run_import`), limitato all'agenzia della
SESSIONE.

REGOLE (decisione D2 del gate A30-7)

* Solo su richiesta: nessun cron, nessun hook nel form pubblico, nessuna
  sincronizzazione al caricamento della pagina.
* Tenant: l'agenzia viene SOLO da `ctx.require_agency()`. La rotta non ha
  parametri ne' corpo da leggere: un `agency_id` mandato dal browser non ha
  dove entrare.
* Ruoli: e' gestione della coda delle richieste, quindi la stessa permission
  che nel dominio decide chi smista e assegna (`ctx.may_assign_records`:
  owner, admin, platform admin in acting). Un `agent` riceve 403.
* Idempotente per costruzione (UNIQUE `(source, source_record_id)` + ON
  CONFLICT DO NOTHING): un record gia' importato non genera ne' righe ne'
  eventi. Nessuna `stima_inspections`, nessuna notifica, nessuna scrittura su
  `stime_dettagliate`.
* Risposta: soli contatori, nessun dato personale.

Montata in `main.py` come il router dell'Agenda: ammissione al mount con
`require_authenticated_operator`, scope sulla rotta da `require_operator`.
Gli errori escono nella stessa forma `{"detail", "code"}` dell'Agenda.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from appointments import errors
from appointments.router import _x
from core.database import core_cursor
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator

from . import stime_dettagliate_import as legacy

#: Lo STESSO prefisso dell'Agenda (e' una sua rotta, servita da un altro
#: package): gli inventari dei domini montati (P26-5, matrice P26-6) lo
#: vedono sotto `/api/appointments` senza un dominio nuovo.
router = APIRouter(prefix="/api/appointments", tags=["appointments"])

#: Le categorie che l'import scarta, sommate nel contatore "escluse".
ESCLUSE = ("orphan", "dst_nonexistent", "dst_ambiguous", "missing_agency", "errors")


def sync_for_session(ctx) -> dict:
    """Importa le richieste legacy della SOLA agenzia della sessione."""
    agency_id = ctx.require_agency()
    if getattr(ctx, "user_id", None) is None:
        raise errors.SessionRequired(
            "La sincronizzazione delle richieste richiede una sessione operatore")
    if not getattr(ctx, "may_assign_records", False):
        raise errors.ForbiddenRole(
            "Solo titolare e amministratori possono aggiornare le richieste dal sito")
    with core_cursor(commit=True) as (_, cur):
        esito = legacy.run_import(cur, apply=True, agency_id=agency_id)
    return {
        "imported": esito["inserted"],
        "already_present": esito["already_imported"],
        "excluded": sum(esito[k] for k in ESCLUSE),
        "counts": {k: esito[k] for k in (
            "eligible", "inserted", "already_imported", "past", "today", "future",
            "orphan", "dst_nonexistent", "dst_ambiguous", "missing_agency",
            "zero_lead", "one_lead", "multiple_leads", "errors")},
    }


@router.post("/legacy-requests/sync")
def sync_legacy_requests(ctx: OperatorContext = Depends(require_operator)):
    return _x(sync_for_session, ctx)
