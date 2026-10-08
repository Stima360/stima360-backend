"""Il database del SITO, in sola lettura.

Due difese indipendenti:

1. la sessione e' aperta con `default_transaction_read_only=on`: il server
   stesso rifiuta qualunque INSERT/UPDATE/DELETE/DDL;
2. questo modulo emette solo SELECT (una sentinella dei test lo verifica sul
   testo del file).

Nessuna credenziale viene stampata o loggata.
"""
from __future__ import annotations

from typing import Any

TABLES = ("stime", "stime_dettagliate")


class SiteSource:
    def __init__(self, url: str, *, connect=None):
        if connect is None:
            import psycopg2
            connect = psycopg2.connect
        self._conn = connect(
            url, options="-c default_transaction_read_only=on -c statement_timeout=60000")
        self._conn.autocommit = False
        with self._conn.cursor() as cur:
            cur.execute("SHOW default_transaction_read_only")
            if cur.fetchone()[0] != "on":
                self.close()
                raise RuntimeError("the site session is not read-only: refusing")
        self._conn.rollback()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        from psycopg2.extras import RealDictCursor
        try:
            with self._conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            self._conn.rollback()

    def identity(self) -> dict[str, Any]:
        """Chi e' questo database: serve a rifiutare il CRM come sorgente."""
        riga = self._rows("SELECT current_database() AS db, inet_server_addr()::text AS addr, "
                          "inet_server_port() AS port, "
                          "to_regclass('public.site_import_records') IS NOT NULL AS has_import_ledger, "
                          "to_regclass('public.schema_migrations') IS NOT NULL AS has_crm_ledger")[0]
        return riga

    def table_present(self, table: str) -> bool:
        assert table in TABLES
        return bool(self._rows("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table}",))[0]["ok"])

    def settled_ids(self, table: str, settle_minutes: int) -> list[int]:
        """Gli id delle righe "ferme": scritte da piu' di `settle_minutes`.

        Solo gli id: sono interi, anche migliaia costano poco, e permettono al
        CRM di calcolare cosa manca SENZA un cursore che salterebbe le righe
        committate fuori ordine.
        """
        assert table in TABLES
        righe = self._rows(
            f"SELECT id FROM {table} WHERE COALESCE(data, LOCALTIMESTAMP) <= "
            "LOCALTIMESTAMP - make_interval(mins => %s) ORDER BY id",
            (int(settle_minutes),))
        return [int(r["id"]) for r in righe]

    def fetch(self, table: str, ids: list[int]) -> dict[int, dict[str, Any]]:
        assert table in TABLES
        if not ids:
            return {}
        righe = self._rows(f"SELECT * FROM {table} WHERE id = ANY(%s)", (list(ids),))
        return {int(r["id"]): r for r in righe}
