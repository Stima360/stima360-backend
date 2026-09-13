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
