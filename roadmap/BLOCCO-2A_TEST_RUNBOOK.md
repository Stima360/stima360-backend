# BLOCCO-2A — runbook TEST: migrazione 116 e scheda «Valutazioni»

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Migrazioni su `stima360_db_test` | da verificare al passo 1 (la **115** di BLOCCO-1B potrebbe essere ancora pendente) |
| **116** (`property_valuations`) | **preparata, non applicata** |
| Codice BLOCCO-2A | **commit locale**, nessun push |
| Prove PostgreSQL `tests/test_blocco_2a_property_valuations_postgres.py` | **preparate, non eseguite** |
| Funzione online | **NON pronta**: dipende dalla tabella `property_valuations`, che non esiste ancora |

## L'ordine: migrazioni PRIMA dell'attivazione

Il branch puo' avere l'auto-deploy. Prima si applicano **115 e 116** (il runner le applica in ordine), poi si pubblica il codice. Se l'ordine si inverte per errore, la scheda «Valutazioni» risponde **503 `VALUATIONS_UNAVAILABLE`** («la migrazione 116 non e' stata applicata»): nessun dato perso, ma la funzione non va dichiarata pronta.

## 0. Prove offline e PostgreSQL

```bash
python -m pytest -q tests/test_blocco_2a_property_valuations.py
```

Le prove PostgreSQL creano ed eliminano un database `blocco_2a_probe_*` e non scrivono in `stima360_db_test`. Creare un database temporaneo nello stesso cluster **non prova che il cluster sia di test**: prima verificare (dashboard Render, `SELECT datname FROM pg_database;` con `stima360_db_test` presente e `stima360_db` assente, utente senza accesso a PROD), poi dichiararlo:

```bash
P29_TEST_DSN="postgresql://<utente>@<host>/<database di servizio>" BLOCCO_2A_TEST_CLUSTER_VERIFIED=1 \
  python -m pytest -q tests/test_blocco_2a_property_valuations_postgres.py
```

**Atteso:** 12 passati, 0 saltati.

## 1. Fotografia prima (sola lettura)

```sql
BEGIN READ ONLY;
SELECT current_database();
SELECT version FROM schema_migrations WHERE version >= '114' ORDER BY version;
SELECT to_regclass('property_valuations') AS valutazioni, to_regclass('agency_logos') AS loghi;
ROLLBACK;
```

## 2-3. Runner (solo su decisione esplicita)

```bash
python scripts/p26_migrate.py plan  --operator "<operatore>"
python scripts/p26_migrate.py apply --operator "<operatore>"
```

**Atteso:** pendenti `115_agency_logos` (se non gia' applicata) e `116_property_valuations`; dopo `apply`, entrambe registrate.

## 4. Collaudo live (dopo il push, dati di prova)

1. scheda di un immobile di prova → tab «Valutazioni»: avviso «Valutazione dell'agente… non e' una perizia»;
2. «Nuova valutazione» con la sola vendita → bozza; «Approva» rifiutato con l'elenco di cio' che manca;
3. completare motivazione, fonti e comparabili (o la dichiarazione della loro assenza) → «Approva»;
4. «Crea nuova versione»: la v1 resta «Approvata» finche' la v2 non viene approvata, poi diventa «Superata»;
5. con un agente non assegnato all'immobile: nessun comando di scrittura; con un agente assegnato: bozza propria, niente «Approva»;
6. il prezzo richiesto dell'immobile resta quello di prima.

## Rientro

Il codice precedente ignora la tabella. La down `116_property_valuations_down.sql` **rifiuta** se esistono valutazioni: conservare la tabella o ripristinare da un backup verificato.
