"""P27-6 - a chi va un lead in ingresso.

LA DOMANDA, E DOVE NON VIVE

"Quale agenzia riceve questa stima pubblica" e' una domanda sulla RETE, non sul
tenant: la risposta si legge in `network_territories` e
`agency_territory_assignments`, che sono dati di piattaforma. Ma la domanda la
pone il funnel pubblico, che e' tenant.

Questo pacchetto sta in mezzo di proposito, e non dentro nessuno dei due:

  * non in `platform_admin/`, perche' i test del gruppo J di P27-5 vietano a
    quei moduli di nominare `stime`, `leads`, `contacts` - e giustamente: la
    fase che dichiara di non decidere il routing non deve contenerne la regola;
  * non in `core/`, perche' il routing non e' una regola del CRM: e' una regola
    della rete che il CRM subisce.

Cosa esporta: una funzione che, dato un comune, restituisce l'id dell'agenzia
che lo presidia - oppure quello dell'agenzia di ripiego. Nient'altro. Non apre
transazioni, non scrive, non conosce `stime` ne' `leads`: riceve un cursore e
lo usa in sola lettura dentro la transazione di chi chiama.

COSA QUESTO PACCHETTO NON FA, PER DECISIONE

  * non sceglie un OPERATORE. P27-6 assegna l'agenzia e si ferma li';
  * non bilancia, non punteggia, non ruota, non conosce SLA ne' priorita'
    commerciali. Dato lo stesso comune e lo stesso stato della rete, la
    risposta e' sempre la stessa riga;
  * non deduce la provincia da un comune. Il repository non contiene nessuna
    mappa comune -> provincia (lo dichiara la 058), e inventarla qui
    significherebbe scrivere una geografia a mano dentro il codice di routing.
"""
