"""L'unica INSERT su platform_audit_log.

Riceve un cursore gia' aperto e non ne apre mai uno: il confine transazionale
lo decide `database.platform_audit_cursor`, e il fatto che sia una connessione
separata da quella dell'operazione e' una decisione di prodotto (D2) che deve
stare in un posto solo.

Non esiste una funzione di lettura, di UPDATE o di DELETE in questo modulo, e
non e' una dimenticanza:

* la lettura dell'audit e' una superficie amministrativa che P27-1 non
  consegna - arrivera' con la Network Admin UI in P27-7;
* UPDATE e DELETE non esistono a livello di applicazione perche' non esistono a
  livello di database: la migration 057 li rifiuta con un trigger (D3). Una
  correzione si fa con una riga compensativa, mai riscrivendo la storia.
"""
from __future__ import annotations

from typing import Any

from psycopg2.extras import Json


def insert_audit_entry(
    cur,
    *,
    actor_user_id: int | None,
    actor_label: str,
    action: str,
    result: str,
    target_type: str | None,
    target_id: str | None,
    target_agency_id: int | None,
    metadata: dict[str, Any],
) -> int:
    """Scrive una riga e restituisce il suo id.

    Tutti i parametri sono keyword-only. Sono nove valori di cui cinque
    opzionali e tre stringhe adiacenti: in posizionale, scambiare `action` e
    `result` o `target_type` e `target_id` produrrebbe una riga plausibile e
    sbagliata, che nessun vincolo intercetta.

    `metadata` viaggia come `Json(...)` e non come stringa: e' cosi' che
    owner/repository.py scrive gia' il proprio audit, ed e' quello che tiene il
    valore fuori dal testo dell'istruzione.
    """
    cur.execute(
        """
        INSERT INTO platform_audit_log (
            actor_user_id, actor_label, action, result,
            target_type, target_id, target_agency_id, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            actor_user_id,
            actor_label,
            action,
            result,
            target_type,
            target_id,
            target_agency_id,
            Json(metadata),
        ),
    )
    return cur.fetchone()["id"]
