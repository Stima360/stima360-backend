# BLOCCO-2B — runbook TEST: migrazione 117 e PDF delle valutazioni

Solo **TEST** (`stima360_db_test`, servizio Render TEST). PROD esclusa.

## Stato di partenza

| Cosa | Stato |
|---|---|
| Migrazioni su `stima360_db_test` | da verificare al passo 1 (la **115** di BLOCCO-1B e la **116** di BLOCCO-2A potrebbero essere ancora pendenti) |
| **117** (`property_valuation_pdfs`) | **preparata, non applicata** |
| Codice BLOCCO-2B | **commit locale**, nessun push |
| Prove PostgreSQL `tests/test_blocco_2b_valuation_pdfs_postgres.py` | **preparate, non eseguite** |
| Funzione online | **NON pronta**: dipende dalle tabelle `property_valuations` (116) e `property_valuation_pdfs` (117), che non esistono ancora |

## L'ordine: migrazioni PRIMA dell'attivazione

Il branch puo' avere l'auto-deploy. Ordine aggiornato prima del rilascio in TEST: **115 → 116 → 117** (il runner le applica in ordine), poi si pubblica il codice. Se l'ordine si inverte per errore:

- senza la 116 la scheda «Valutazioni» risponde **503 `VALUATIONS_UNAVAILABLE`**;
- con la 116 ma senza la 117 le valutazioni funzionano, i comandi PDF non compaiono («Il PDF delle valutazioni non è ancora attivo su questo ambiente») e le rotte `/pdf` rispondono **503 `PDF_UNAVAILABLE`**.

Nessun dato perso, ma la funzione non va dichiarata pronta.

## 0. Prove offline e PostgreSQL

```bash
python -m pytest -q tests/test_blocco_2b_valuation_pdfs.py tests/test_blocco_2a_property_valuations.py
```

Le prove PostgreSQL creano ed eliminano un database `blocco_2b_probe_*` e non scrivono in `stima360_db_test`. Creare un database temporaneo nello stesso cluster **non prova che il cluster sia di test**: prima verificare (dashboard Render, `SELECT datname FROM pg_database;` con `stima360_db_test` presente e `stima360_db` assente, utente senza accesso a PROD), poi dichiararlo:

```bash
P29_TEST_DSN="postgresql://<utente>@<host>/<database di servizio>" BLOCCO_2B_TEST_CLUSTER_VERIFIED=1 \
  python -m pytest -q tests/test_blocco_2b_valuation_pdfs_postgres.py
```

**Atteso:** 9 passati, 0 saltati.

## 1. Fotografia prima (sola lettura)

```sql
BEGIN READ ONLY;
SELECT current_database();
SELECT version FROM schema_migrations WHERE version >= '114' ORDER BY version;
SELECT to_regclass('agency_logos') AS loghi, to_regclass('property_valuations') AS valutazioni,
       to_regclass('property_valuation_pdfs') AS pdf_valutazioni;
ROLLBACK;
```

## 2-3. Runner (solo su decisione esplicita)

```bash
python scripts/p26_migrate.py plan  --operator "<operatore>"
python scripts/p26_migrate.py apply --operator "<operatore>"
```

**Atteso:** pendenti, nell'ordine, quelle fra `115_agency_logos`, `116_property_valuations` e `117_property_valuation_pdfs` non ancora applicate; dopo `apply`, tutte registrate.

## 4. Collaudo live (dopo il push, dati di prova)

1. «Dati agenzia e logo»: dati e logo di prova (o nessun logo: il PDF usa il nome dell'agenzia);
2. scheda di un immobile di prova → tab «Valutazioni» → una versione approvata: compare «Genera PDF»;
3. «Genera PDF» → «Scarica PDF»: il file riporta intestazione, versione, data e autore dell'approvazione, sezioni valutate o «Non valutata», motivazione, fonti, comparabili e la dicitura «Valutazione dell'agente… non è una perizia certificata né una quotazione automatica OMI»;
4. scaricare due volte: file identici (stessa impronta, intestazione `ETag`);
5. cambiare nome o logo dell'agenzia e riscaricare: il PDF già generato non cambia;
6. «Crea nuova versione» e approvarla: la v2 offre il suo «Genera PDF»; la v1, superata, mostra «Documento storico» e resta scaricabile;
7. una bozza non offre il PDF; un agente non assegnato all'immobile può scaricare ma non generare; un'altra agenzia non vede né il documento né la valutazione.

## Rientro

Il codice precedente ignora la tabella. La down `117_property_valuation_pdfs_down.sql` **rifiuta** se esistono documenti (nessuna cancellazione di PDF): conservare la tabella o ripristinare da un backup verificato.
