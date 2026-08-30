# INTEGRATION P2 — POSTCHECK

Il postcheck è bloccante se:

- resta una PK registrata dal test;
- un record preesistente differisce dallo snapshot;
- il teardown contiene errori;
- rimane un residuo del `run_id`.

Il `run_id` è escapato per `LIKE` (`_`, `%`, `\\`) e la query usa una clausola `ESCAPE` esplicita.
I conteggi globali restano soltanto diagnostici.
