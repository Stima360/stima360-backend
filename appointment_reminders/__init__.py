"""A32 - PROMEMORIA AUTOMATICI DEGLI APPUNTAMENTI. A32-1: SOLO LA FOUNDATION.

Gate A32-0 / A32-0B (design e infrastruttura certificati). Questo package, in
A32-1, contiene soltanto logica PURA:

  * `policy`   - chi riceve un promemoria, quando (Europe/Rome, stessa ora
                 civile del giorno prima, finestra 08:00-20:00, soglie 3h/12h)
                 e con quale identita' di occorrenza;
  * `template` - l'email HTML italiana (oggetto + corpo) con escape di ogni
                 valore dinamico.

Regole del package in questa fase (e verificate da test statici):
  * nessun accesso al database, nessuna connessione, nessun commit;
  * nessun import del dominio COMMUNICATION (dispatcher, provider, service) ne'
    di `appointments/`: riceve dati gia' letti e sanitizzati;
  * nessuna rete, nessun router FastAPI.

Il planner, la revalida finale prima del provider, la rotta e il passo del
cron sono le fasi successive (A32-2/A32-3), con i loro gate.
"""
