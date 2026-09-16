"""La forma di un esito di provider. Nessuna rete, nessun adapter.

TRE VALORI DI ESITO, NON DUE

`accepted`, `rejected`, `unknown`. Il terzo e' quello che di solito manca, ed e'
il motivo per cui questo tipo esiste invece di un booleano: quando la
connessione cade prima della risposta, il messaggio PUO' essere partito.
Costringere quel caso dentro "fallito" produce un doppione al primo retry o una
perdita silenziosa.

`ProviderCapabilities` DICE COSA UN TRASPORTO NON SA DIRE

`database.invia_mail` ritorna `True`/`False`, ingoia le eccezioni e non
restituisce alcun id: non sa distinguere un rifiuto certo da un timeout a esito
ignoto. Dichiararlo in una capability - invece di ricordarselo - e' cio' che
impedisce al dispatcher di mappare quel `False` su `failed`: con
`distinguishes_failure_class = False` l'unica mappatura onesta e' `unknown`, e
il dispatcher la applica perche' la capability lo dice, non per prudenza sparsa
nel codice.

IL CONTRATTO DI UN ADAPTER - C22, RAMO A

    send(message) -> ProviderResult

Un adapter NORMALIZZA da se' gli errori operativi PREVEDIBILI in un
`ProviderResult`, e non li lascia propagare come eccezioni:

    timeout noto                 -> outcome = unknown
    errore HTTP / del provider   -> unknown, oppure rejected se la capability
                                    `distinguishes_failure_class` lo consente
    rifiuto esplicito            -> rejected
    risposta ambigua o illeggibile -> unknown
    qualunque guasto operativo previsto -> unknown

La ragione e' che `ProviderResult` + `ProviderCapabilities` sono la SORGENTE
DI VERITA' per distinguere `failed` da `indeterminate`. Un'eccezione non porta
con se' una capability: non dice se quel trasporto sapesse distinguere un
rifiuto certo da un esito ignoto, e quindi non permette di decidere. Un esito
normalizzato lo dice.

Il ramo B - il `try/except` del dispatcher - non sostituisce questo contratto:
lo protegge. Copre l'adapter che lo viola e l'imprevisto che nessuno aveva
previsto, e finalizza sempre `indeterminate`, che e' l'unica lettura onesta di
"non sappiamo se sia partito". Un adapter che si affida a quella rete invece di
normalizzare perde informazione: `rejected` certo e timeout ignoto arrivano al
ledger indistinguibili.

Il provider finto di questa fase rispetta lo stesso contratto.
"""

from __future__ import annotations

from dataclasses import dataclass

#: I tre esiti che un trasporto puo' riportare.
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_UNKNOWN = "unknown"
PROVIDER_OUTCOMES = frozenset({OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_UNKNOWN})


@dataclass(frozen=True)
class ProviderCapabilities:
    """Cosa questo trasporto sa dire di se stesso.

    Congelata: una capability che il chiamante puo' cambiare dopo averla
    ricevuta non e' una dichiarazione, e' un suggerimento.
    """

    returns_message_id: bool
    distinguishes_failure_class: bool
    reports_delivery: bool


@dataclass(frozen=True)
class ProviderResult:
    """Cio' che un trasporto ha osservato. Non decide niente.

    Non sa cosa sia un contatto, un'agenzia o un consenso: prende una
    destinazione e un corpo, e riporta com'e' andata. La traduzione in uno stato
    del messaggio appartiene al dispatcher.
    """

    outcome: str
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in PROVIDER_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {', '.join(sorted(PROVIDER_OUTCOMES))}; "
                f"got {self.outcome!r}"
            )
        if self.outcome != OUTCOME_ACCEPTED and self.provider_message_id is not None:
            raise ValueError(
                "provider_message_id belongs to an accepted result: a transport that "
                "rejected or does not know cannot also have handed back an id"
            )
