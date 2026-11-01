"""Riconciliazione retroattiva delle conversazioni WhatsApp con la rubrica
Contatti, per UN'agenzia (Render One-Off Job o shell dell'operatore).

    python scripts/reconcile_whatsapp_contacts.py --agency 1 [--dry-run]

Stessa regola della ricezione (`inbound_messages/contact_match.py`): le
conversazioni instradate senza contatto, non decise a mano, prendono il
contatto se la rubrica dell'agenzia ne ha UNO solo con quel numero intero
(tutti i campi telefonici); con piu' contatti restano da scegliere; le
associazioni manuali non si toccano. Nessun immobile o stima viene collegato.
Stampa solo conteggi. Con --dry-run la transazione viene annullata.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agency", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from core.database import core_cursor
    from inbound_messages import contact_match, repository

    with core_cursor(commit=not args.dry_run) as (conn, cur):
        cur.execute("SELECT id FROM agencies WHERE id = %s", (args.agency,))
        if cur.fetchone() is None:
            print(f"STOP agenzia {args.agency} inesistente")
            return 1
        if not repository.installed(cur):
            print("STOP ricezione WhatsApp non installata (migrazione 118)")
            return 1
        summary = contact_match.reconcile(cur, args.agency)
        if args.dry_run:
            conn.rollback()
    print(("PROVA (nessuna modifica): " if args.dry_run else "ESEGUITA: ") + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
