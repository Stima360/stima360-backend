"""Il confine transazionale del writer di audit. P27-1, decisione D2.

UNA CONNESSIONE PROPRIA, NON QUELLA DEL CHIAMANTE.

E' la differenza fra due audit log incompatibili, ed e' una decisione di
prodotto presa esplicitamente (D2, opzione A).

Condividendo la transazione dell'operazione, l'audit registrerebbe solo cio'
che e' stato committato: un tentativo rifiutato non lascerebbe traccia, e un
rollback cancellerebbe la riga insieme all'operazione fallita. Il registro
direbbe quindi cosa e' successo, ma non cosa e' stato TENTATO - e su una
superficie di amministrazione della rete il tentativo respinto e' spesso
l'informazione piu' importante che ci sia.

Con una connessione propria, che committa da sola, ogni riga sopravvive
all'esito dell'operazione che descrive. Il prezzo e' che una riga puo'
descrivere un'operazione poi annullata: e' il motivo per cui esiste la colonna
`result`, che distingue `success`, `denied` ed `error`.

Rispecchia operator_auth/database.py: contextmanager locale al package, una
sola apertura di connessione, il repository riceve un cursore gia' aperto.
"""
from __future__ import annotations

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from database import get_connection


@contextmanager
def platform_audit_cursor():
    """Cede ``(connection, cursor)`` per UNA riga di audit, e la committa.

    Committa sempre in uscita normale - non su richiesta come
    `operator_cursor(commit=...)` - perche' questo contextmanager ha un solo
    uso e quell'uso ha una sola politica. Un parametro `commit=False` qui
    sarebbe un modo per scrivere un audit che non viene salvato.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


@contextmanager
def platform_operation_cursor():
    """Cede ``(connection, cursor)`` per UNA mutazione amministrativa.

    NON COMMITTA. E' l'unica ragione per cui esiste accanto a
    `platform_audit_cursor`, e la differenza e' tutto il punto della regola
    che P27-2 deve far rispettare:

        nessuna modifica amministrativa viene committata se il suo audit non
        e' stato scritto.

    Il commit appartiene quindi al service, DOPO che `audit.record()` e'
    tornato. Un `commit=True` qui - o un commit automatico in uscita come fa
    il cursore di audit - toglierebbe al chiamante l'unica leva con cui puo'
    ordinare le due cose, e la regola tornerebbe a essere una speranza.

    Il rollback invece resta qui: e' la reazione a un'eccezione, non una
    decisione, e ripeterlo in ogni chiamante e' il modo di dimenticarlo in uno.

    PERCHE' DUE CONNESSIONI, E PERCHE' NON SI CERCA DI RENDERLE ATOMICHE

    L'audit vive su una connessione sua per la decisione D2: deve registrare il
    TENTATIVO, quindi deve poter sopravvivere al rollback dell'operazione che
    descrive. Due connessioni non si possono committare atomicamente senza un
    coordinatore di transazioni distribuite, e non se ne introduce uno per
    questo.

    Cio' che si sceglie e' l'ORDINE, che decide quale delle due incoerenze
    possibili si accetta:

        audit scritto, operazione non committata  -> ACCETTATA, e registrata
                                                     con una riga compensativa
                                                     result='error'.
        operazione committata, audit non scritto  -> MAI.

    E' l'asimmetria giusta per un registro: una riga che descrive un tentativo
    fallito e' leggibile e vera; una modifica senza traccia non e' recuperabile
    in nessun modo.
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield conn, cur
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
