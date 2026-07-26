# INTEGRATION 0.1 — Pacchetto 2 operativo, non eseguito

Stato: **correzioni preparatorie completate; E2E non autorizzati e non eseguiti**.

Correzioni incluse:

1. confronto OpenAPI normalizzato che ignora esclusivamente il nome dei parametri path;
2. teardown transazionale con aggiornamento `removed=true` soltanto dopo commit riuscito;
3. test E2E basati sul manifest reale, senza variabili `INTEGRATION_SCENARIO_*_DONE`;
4. matrice HTTP stateful implementata nell’orchestratore;
5. verifica reale degli orfani per ciascuna foreign key;
6. privacy OWNER estesa a chiavi e valori;
7. verifica DB del token OWNER grezzo/hash;
8. ricerca residui `run_id` con escape esplicito di `_`, `%` e `\\`.

Nessuna richiesta HTTP, scrittura DB, migrazione, pulizia o esecuzione E2E è stata effettuata durante la preparazione.
Produzione, `main.py` e moduli congelati restano invariati.
