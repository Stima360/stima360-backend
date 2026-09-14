"""SQL del contesto di agenzia del Superadmin. P28.

Tre istruzioni, tutte su `operator_sessions` e su nient'altro. In particolare
NON su `agency_memberships`: il Superadmin che entra in un'agenzia non ne
diventa membro, e l'unico modo di garantirlo e' che il percorso che lo fa
entrare non abbia a disposizione la tabella che lo renderebbe membro.

PERCHE' UN REPOSITORY DI PLATFORM E NON QUELLO DI `operator_auth`

`operator_auth/repository.py` ha gia' `set_acting_context` e
`clear_acting_context`, e sono le stesse due UPDATE. La differenza non e' il
codice: e' la CONNESSIONE. Una scrittura amministrativa deve stare nella
transazione di `platform_operation_cursor`, che non committa finche' l'audit non
e' passato; le funzioni di `operator_auth` vivono sul cursore della sessione,
che committa per conto suo alla fine di ogni richiesta.

Scriverle una volta sola e chiamarle da entrambi i posti significherebbe che la
regola "nessuna modifica amministrativa senza il suo audit" dipende da quale
cursore il chiamante ha passato - cioe' non e' piu' una regola.

Ogni funzione riceve un cursore gia' aperto e non ne apre nessuno.
"""
from __future__ import annotations

from typing import Any


def get_session_acting(cur, session_id: int) -> dict[str, Any] | None:
    """La riga di sessione, con cio' che sta impersonando. `None` se non c'e'.

    Legge la SESSIONE e non il contesto del chiamante, ed e' deliberato: il
    contesto porta l'agenzia EFFETTIVA, che durante un'impersonazione e' gia'
    quella visitata. Chiedergli "stai impersonando?" sarebbe chiederglielo
    dopo averne cancellato la traccia. La verita' e' nella riga.

    Non filtra su `revoked_at` ne' sulla scadenza: a quelle ha gia' risposto
    `require_platform_admin`, che non avrebbe ammesso il chiamante senza una
    sessione viva. Rifiltrare qui darebbe due definizioni di "sessione valida".
    """
    cur.execute(
        """
        SELECT id, acting_agency_id, acting_entered_at
          FROM operator_sessions
         WHERE id = %s
        """,
        (session_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def set_acting(cur, *, session_id: int, agency_id: int) -> int:
    """Entra: questa sessione opera dentro quell'agenzia. Righe toccate.

    Le due colonne in una istruzione sola, perche' il CHECK della 060 le vuole
    appaiate. `NOW()` del database e non un'ora di Python: e' lo stesso
    orologio che timbra `platform_audit_log.created_at`, e due istanti che
    raccontano lo stesso ingresso devono essere confrontabili.

    Zero righe significa che la sessione e' sparita fra l'ammissione e questa
    scrittura. Il chiamante deve trattarlo come un ingresso NON avvenuto: un
    200 su una riga che non esiste prometterebbe un contesto che nessuno
    rileggera' mai.
    """
    cur.execute(
        """
        UPDATE operator_sessions
           SET acting_agency_id = %s,
               acting_entered_at = NOW()
         WHERE id = %s
        """,
        (agency_id, session_id),
    )
    return cur.rowcount


def clear_acting(cur, *, session_id: int) -> int:
    """Esce. Idempotente: su una sessione gia' pulita riscrive gli stessi NULL.

    L'idempotenza non e' una comodita': e' cio' che permette all'uscita di non
    fallire mai. Un Superadmin che preme "Torna alla Platform" due volte, o che
    lo preme dopo che l'acting e' gia' decaduto da solo, deve ritrovarsi fuori
    in entrambi i casi - non davanti a un errore che gli dice che non era
    dentro.
    """
    cur.execute(
        """
        UPDATE operator_sessions
           SET acting_agency_id = NULL,
               acting_entered_at = NULL
         WHERE id = %s
        """,
        (session_id,),
    )
    return cur.rowcount
