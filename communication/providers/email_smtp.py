"""P29-2.5E - l'adapter email reale. AVVOLGE `database.invia_mail`, non la sostituisce.

PERCHE' AVVOLGERE E NON RISCRIVERE

`database.invia_mail` e' la sola primitiva email del sistema e oggi manda davvero
le email dei clienti. Riscriverla dentro il dominio significherebbe cambiare, in
un solo diff, sia il trasporto sia chi lo governa - e un difetto in quel diff
sarebbe una email non partita a una persona vera. Qui il trasporto resta
esattamente quello che e' gia' certificato dall'uso; cio' che si aggiunge e' la
TRADUZIONE del suo esito nel vocabolario del ledger. `database.py` non si tocca,
ed e' un criterio di chiusura della fase, non una preferenza.

COSA `invia_mail` SA DIRE, E COSA NON SA DIRE

Ritorna `True` o `False`. Ingoia le proprie eccezioni. Non restituisce alcun id.
Non distingue un rifiuto certo del server da un timeout a esito ignoto: entrambi
diventano `False`. Le `CAPABILITIES` qui sotto dichiarano esattamente questo, ed
e' la dichiarazione - non una prudenza sparsa nel codice - a impedire al
dispatcher di scrivere `failed` su un esito che nessuno ha dimostrato.

IL CONTRATTO DEL RAMO A (C22), RISPETTATO QUI

Gli errori operativi prevedibili non escono da `send` come eccezioni: diventano
un `ProviderResult`. `invia_mail` ingoia gia' quasi tutto, ma il `try` resta
perche' "quasi" non e' "tutto" - `int(os.getenv("SMTP_PORT"))` con un valore non
numerico solleva prima ancora di arrivare a `smtplib`, e la rete di sicurezza
del dispatcher e' l'ultima difesa, non la prima.

NESSUNA RETE DICHIARATA QUI DENTRO

Questo file non nomina nessuna libreria di rete e non conosce
`SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS`: la configurazione la legge
`invia_mail`, a ogni chiamata. E' anche cio' che tiene vera la sentinella P1 di
P29-2.4 - dentro `communication/` non compare nessun nome di libreria di rete.

E NESSUN WHATSAPP

P29-2.5E e' solo email. Il trasporto WhatsApp e' P29-2.5W, deferita e bloccata
da R3, che resta OPEN (§23.4).
"""

from __future__ import annotations

from typing import Any

from database import invia_mail

from .base import (OUTCOME_ACCEPTED, OUTCOME_UNKNOWN, ProviderCapabilities,
                   ProviderResult)

#: L'identita' che finisce nella colonna `provider` del messaggio e del
#: tentativo. Il dispatcher la legge DA QUI (`provider.NAME`) e la passa al
#: claim: un valore solo, mai due che possono divergere.
NAME = "email_smtp"

CAPABILITIES = ProviderCapabilities(
    # `invia_mail` non restituisce nessun id: non se ne inventa uno.
    returns_message_id=False,
    # `True`/`False` non distingue un rifiuto certo da un esito ignoto.
    distinguishes_failure_class=False,
    # Nessuna conferma di consegna: SMTP accettato non e' SMTP recapitato.
    reports_delivery=False,
)

#: Cio' che si scrive in `error_detail` quando la primitiva dice solo "no".
DETTAGLIO_RIFIUTO = "invia_mail returned False"


def send(message: dict[str, Any]) -> ProviderResult:
    """Manda il messaggio gia' renderizzato, e riporta com'e' andata.

    Prende una destinazione, un oggetto e un corpo. Non sa cosa sia un contatto,
    un'agenzia o un consenso - il terzo confine del design - e non tocca il
    database.

    LA MAPPATURA, E PERCHE' NON C'E' `rejected`

        True   -> accepted, senza id
        False  -> unknown
        boom   -> unknown

    `False` NON diventa `rejected`. Sarebbe comunque tradotto in `indeterminate`
    dal dispatcher, perche' `distinguishes_failure_class` e' False; ma
    dichiararlo `rejected` qui significherebbe scrivere nel ledger una certezza
    che `invia_mail` non possiede, e basterebbe alzare quella capability un
    giorno per trasformare quella bugia in un `failed` - cioe' in un messaggio
    che §7.3 autorizza a rientrare in coda. Si dice `unknown` perche' e'
    `unknown`.

    `error_code` resta None di proposito: i codici del ledger sono vocabolario
    del dominio, e il dispatcher scrive gia' `unknown` quando l'adapter non ne
    propone uno. `error_detail` distingue le due strade senza inventare codici.
    """
    destinatario = message["destination_snapshot"]
    oggetto = message["subject_snapshot"]
    corpo = message["rendered_body"]

    try:
        accettata = invia_mail(destinatario, oggetto, corpo)
    except Exception as exc:  # noqa: BLE001 - ramo A di C22: si normalizza
        return ProviderResult(
            outcome=OUTCOME_UNKNOWN,
            error_detail=f"{type(exc).__module__}.{type(exc).__qualname__}",
        )

    if accettata is True:
        # Nessun `provider_message_id`: la primitiva non ne restituisce uno, e
        # `ProviderResult` rifiuterebbe comunque un id su un esito non accettato.
        return ProviderResult(outcome=OUTCOME_ACCEPTED)

    return ProviderResult(outcome=OUTCOME_UNKNOWN, error_detail=DETTAGLIO_RIFIUTO)
