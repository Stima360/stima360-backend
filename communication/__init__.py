"""P29-2 COMMUNICATION - il ledger dei messaggi e la sua unica via di scrittura.

Un modulo additivo. Non sostituisce niente, non riscrive niente, e in questa
fase non conosce ne' provider, ne' template, ne' scheduler, ne' rete.

COSA FA P29-2.2

Mette in coda un messaggio e lo legge. Due operazioni di scrittura - `enqueue`
e `cancel` - e nessuna terza.

`enqueue` scrive UNA riga in `communication_messages` con `status = 'queued'` e
ritorna. Non apre connessioni verso l'esterno, non chiama nessuno, non decide
se il messaggio si possa mandare: decide soltanto che ESISTE l'intenzione di
mandarlo. Puo' stare dentro la transazione del chiamante, esattamente come
`consent.repository.record_decision_with_cursor`, perche' un'intenzione di
comunicazione nata da un atto che poi viene annullato non deve sopravvivere a
quell'atto.

COSA NON FA, E QUANDO LO FARA'

* non reclama e non finalizza nulla        -> P29-2.3
* non interroga `can_send_marketing`       -> P29-2.4
* non parla con un provider                -> P29-2.5
* non rende un template                    -> P29-2.5
* non sostituisce gli invii esistenti      -> P29-2.6
* non proietta niente su `activities`      -> P29-2.7
* non ha UI                                -> P29-2.8

LA RETE NON PASSA DI QUI

E' il primo dei cinque confini del design, ed e' strutturale: in questo
pacchetto non esiste un import di `requests`, di `smtplib` o di un adapter, e
una sentinella lo verifica. Un modulo che puo' scrivere una riga e non puo'
chiamare nessuno non puo' mandare un messaggio per sbaglio.

LO STATO INIZIALE NON E' UN PARAMETRO

`enqueue` scrive sempre `queued`. Non esiste un argomento `status`, e non deve
esistere: un chiamante che potesse accodare un messaggio gia' `sent`
scriverebbe nel registro una comunicazione mai avvenuta. Le transizioni
appartengono al dispatcher, che in questa fase non esiste ancora.
"""
