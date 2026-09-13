"""Eccezioni di dominio della superficie Platform.

Tipi puri: nessun import di framework web. La traduzione in uno status HTTP
appartiene al livello router, come in operator_auth/exceptions.py.
"""
from __future__ import annotations


class PlatformAuditUnavailable(Exception):
    """La riga di audit non e' stata scritta.

    Sollevata dal writer quando la scrittura fallisce per qualunque ragione -
    database irraggiungibile, vincolo violato, transazione rifiutata.

    Non viene inghiottita dal writer di proposito. Chi chiama deve DECIDERE
    cosa farne, e le due decisioni in P27-1 sono diverse:

    * sul percorso di ammissione riuscita l'operazione non procede (503): un
      atto amministrativo non registrato non deve avvenire;
    * sul percorso di rifiuto il 403 resta comunque, perche' l'operazione non
      stava avvenendo in nessun caso e trasformare un rifiuto in un 500
      direbbe al chiamante qualcosa sullo stato interno del server.

    Un writer che loggasse e basta renderebbe impossibile la prima delle due.
    """


class AgencyNotFound(Exception):
    """L'agenzia richiesta non esiste. Il router la traduce in 404.

    Su questa superficie il 404 e' letterale. Nelle superfici di tenant un 404
    e' anche uno schermo - il record di un'altra agenzia e' byte-identico a uno
    inesistente, per non farne dedurre l'esistenza - ma un platform admin vede
    tutta la rete, quindi qui non c'e' nulla da nascondere a nessuno: se dice
    "non trovata", non c'e'.
    """


class AgencySlugConflict(Exception):
    """Lo slug e' gia' di un'altra agenzia. Il router la traduce in 409.

    Sollevata da due punti diversi del service, di proposito:

    * dal controllo esplicito prima della scrittura, che e' quello che produce
      il messaggio buono nel caso normale;
    * dalla cattura di `UniqueViolation`, che e' l'unico modo di coprire la
      corsa fra due creazioni simultanee dello stesso slug - il controllo e la
      INSERT non sono un'operazione sola, e fra i due c'e' una finestra.

    Il secondo non e' ridondante: senza, una corsa persa diventerebbe un 500
    con dentro il nome del vincolo.
    """


class OperatorNotFound(Exception):
    """L'operatore richiesto non esiste. 404."""


class MembershipNotFound(Exception):
    """L'operatore non ha una membership in quell'agenzia. 404.

    Distinta da `OperatorNotFound`: l'operatore puo' esistere benissimo e
    lavorare altrove. E' la RELAZIONE con questa agenzia a non esserci, ed e'
    l'unica cosa che le route sotto `/agencies/{id}/operators/...` possono
    amministrare.
    """


class PasswordRequired(Exception):
    """Manca la credenziale per creare una persona nuova. 422.

    Tipo suo e non `PlatformConflict`, perche' lo status e' diverso e la
    differenza e' sostanziale: un 409 dice "lo stato del sistema impedisce
    questa richiesta", e qui non c'e' nessuno stato con cui confliggere -
    manca un dato, e chi ha chiamato deve aggiungerlo.

    Non e' un 422 che lo schema possa produrre: `password` e' obbligatoria solo
    quando l'email identifica una persona nuova, e quale dei due percorsi si
    prenda lo si sa dopo aver interrogato `operator_users`.
    """


class PlatformConflict(Exception):
    """Un conflitto di stato del dominio Network. 409.

    Un tipo solo per sei conflitti diversi - email gia' presa, membership gia'
    esistente, membership attiva altrove, secondo titolare, riattivazione
    incompatibile, titolare non membro - perche' la differenza fra loro sta nel
    MESSAGGIO, che e' quello che il chiamante legge, non nel modo in cui il
    router deve reagire: sono tutti 409.

    Sei classi produrrebbero sei `except` identici in ogni route, e la prima
    dimenticata diventerebbe un 500. Il messaggio arriva sempre dalle costanti
    in enums.py, mai da psycopg2: il nome di un vincolo racconta al chiamante
    com'e' fatto lo schema.
    """
