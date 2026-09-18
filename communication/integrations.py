"""Cio' che deve accadere NELLA STESSA TRANSAZIONE di una finalizzazione `sent`.

IL PROBLEMA CHE QUESTO MODULO RISOLVE

Prima del cutover, `/api/salva_stima` chiamava `invia_mail` e registrava
l'evento P17 `email_stima_inviata` se e solo se quella funzione ritornava
`True`. Una riga sotto l'altra, nello stesso processo: l'evento significava
esattamente "la mail al cliente e' partita".

Dopo il cutover la mail parte altrove e piu' tardi - la accoda il producer, la
manda il dispatcher - e il momento che corrisponde a quel `True` e' uno solo: la
finalizzazione `sent` che VINCE il compare-and-set. L'evento deve nascere li', e
non prima.

PERCHE' NELLA STESSA TRANSAZIONE, E NON SUBITO DOPO

Un secondo commit "best effort" dopo il primo lascia una finestra in cui il
messaggio e' `sent` e l'evento non esiste. Un processo che muore in quella
finestra lascia i due registri in disaccordo PER SEMPRE: nessuno ripassa, perche'
il messaggio e' terminale e l'automazione non lo rivede. Una transazione sola non
ha quella finestra - o ci sono entrambi, o nessuno dei due.

Il prezzo e' dichiarato e accettato: se l'evento non si scrive, il `sent` torna
indietro e il messaggio resta `sending` con il suo token, mentre la mail e' gia'
partita. Da li' NON si riparte: `recover_stale` porta quel claim a
`indeterminate` - mai a `queued` - quindi nessun reinvio cieco, nessun doppio
invio, e `attempt_count` resta quello del claim. E' la stessa policy fail-safe
che il design ha scelto per lo stale, applicata a un caso in piu'.

PERCHE' UN MODULO A PARTE

`communication/service.py` e' il nucleo DB-safe di P29-2.3: non conosce
provider, non interroga il consenso, non sa che esista un altro dominio, e la
sua superficie pubblica e' fissata da una sentinella. Un hook verso Seller
Intelligence li' dentro sarebbe esattamente la dipendenza che quel confine
esiste per impedire. Qui invece il confine e' dichiarato: questo modulo conosce
entrambi i domini, ed e' l'unico che lo fa.

Nessuna dipendenza circolare: `seller_intelligence` non importa
`communication`, in nessuno dei suoi moduli.

COME SI RICONOSCE LA MAIL DELLA STIMA: DAI DATI, NON DAL TESTO

Non dall'oggetto, non da una sottostringa del corpo, non dal destinatario. Dai
campi strutturati che il producer scrive e che il ledger conserva:
`communication_type='service'`, `reason_code='stima_pdf'`, `stima_id` non nullo
e un `pdf_url` nei metadata. Un'euristica sul testo si romperebbe la prima volta
che qualcuno cambia una parola dell'oggetto - ed e' un cambiamento che nessuno
penserebbe di far passare da qui.
"""

from __future__ import annotations

import logging
from typing import Any

from seller_intelligence import service as seller_intelligence_service

from .enums import REASON_STIMA_PDF, TYPE_SERVICE

logger = logging.getLogger(__name__)

#: L'evento P17 che il cutover deve preservare, parola per parola.
EVENT_EMAIL_STIMA_INVIATA = "email_stima_inviata"

#: La sorgente, invariata rispetto al percorso legacy di `/api/salva_stima`.
EVENT_SOURCE_STIMA360 = "stima360_it"


def _e_la_mail_della_stima(message: dict[str, Any]) -> bool:
    """La mail di servizio con il PDF della stima, riconosciuta DAI DATI.

    Due campi, e sono quelli che il producer scrive e il ledger conserva. Non
    l'oggetto, non il corpo, non il destinatario: un'euristica sul testo si
    romperebbe la prima volta che qualcuno cambia una parola dell'oggetto, e
    quel cambiamento nessuno penserebbe di farlo passare da qui.

    `stima_id` e `metadata.pdf_url` NON stanno qui, e la differenza conta: non
    servono a decidere SE questo e' il messaggio della stima - lo e' gia' - ma
    sono i dati che l'evento richiede, e mancassero sarebbe un guasto, non un
    "allora non si applica". Li pretende `_evento_email_stima`, sollevando.
    Metterli nel riconoscimento vorrebbe dire trasformare un messaggio
    malformato in un messaggio di un altro tipo, cioe' in un silenzio.

    La prima riga non e' difensiva per abitudine: una prima stesura riceveva il
    risultato di `finalize`, che e' `{'message': ..., 'attempt': ...}` e non la
    riga. Ogni `.get()` tornava `None`, il predicato diceva "no", e l'evento non
    nasceva MAI - senza errore, senza log, senza niente.
    """
    if "communication_type" not in message:
        raise KeyError(
            "dopo_invio ha ricevuto qualcosa che non e' una riga di "
            "communication_messages; un predicato che rispondesse 'no' a un "
            "dato della forma sbagliata non creerebbe mai l'evento e non lo "
            "direbbe a nessuno")
    return (message.get("communication_type") == TYPE_SERVICE
            and message.get("reason_code") == REASON_STIMA_PDF)


def _pdf_url_obbligatorio(message: dict[str, Any]) -> str:
    """Il `pdf_url` della mail della stima: stringa non vuota, o si solleva.

    PERCHE' UN RIFIUTO E NON UN VALORE MANCANTE

    L'evento `email_stima_inviata` ha sempre avuto un payload con dentro il
    `pdf_url`: e' cio' che rende utile quella riga in una timeline - dice QUALE
    documento la persona ha ricevuto. Un evento con payload vuoto avrebbe il
    nome giusto e nessun contenuto, e nessuno se ne accorgerebbe fino al giorno
    in cui qualcuno apre quella timeline cercando il PDF.

    Sollevare qui significa che la transazione non committa e il messaggio non
    risulta `sent`: un rifiuto rumoroso al posto di una riga muta. Il messaggio
    resta `sending` e la recovery lo chiude `indeterminate` - vedi il modulo
    del dispatcher - quindi il costo del rifiuto e' un messaggio da guardare,
    non una mail mandata due volte.

    `strip()` e non solo la verita' della stringa: `"   "` e' una stringa vera
    per Python e un URL per nessuno.
    """
    metadata = message.get("metadata") or {}
    pdf_url = metadata.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip():
        raise ValueError(
            f"communication_messages(id={message.get('id')}) e' una mail "
            f"{TYPE_SERVICE}/{REASON_STIMA_PDF} senza un metadata.pdf_url "
            f"utilizzabile (trovato: {pdf_url!r}); l'evento "
            f"{EVENT_EMAIL_STIMA_INVIATA} non puo' nascere con un payload "
            "senza il documento di cui parla")
    return pdf_url


def _evento_email_stima(cur, message: dict[str, Any]) -> None:
    """L'evento P17, identico a quello che scriveva `/api/salva_stima`.

    `event_type`, `event_source`, `payload.pdf_url` e la chiave di idempotenza
    `email_stima_inviata:{stima_id}` sono quelli di prima, letterali. I
    riferimenti vengono dal messaggio, che li porta scritti dal producer:
    ricalcolarli qui vorrebbe dire ridecidere, e una seconda decisione e' una
    seconda possibilita' di sbagliare.

    `stima_id` e' pretenuto come il `pdf_url`: e' la chiave di idempotenza
    dell'evento, e senza non esiste nemmeno una chiave da scrivere.
    """
    pdf_url = _pdf_url_obbligatorio(message)
    stima_id = message.get("stima_id")
    if stima_id is None:
        raise ValueError(
            f"communication_messages(id={message.get('id')}) e' una mail "
            f"{TYPE_SERVICE}/{REASON_STIMA_PDF} senza stima_id; la chiave di "
            f"idempotenza di {EVENT_EMAIL_STIMA_INVIATA} non esisterebbe")

    seller_intelligence_service.record_event_on_cursor(
        cur,
        event_type=EVENT_EMAIL_STIMA_INVIATA,
        event_source=EVENT_SOURCE_STIMA360,
        stima_id=stima_id,
        contact_id=message.get("contact_id"),
        lead_id=message.get("lead_id"),
        payload={"pdf_url": pdf_url},
        idempotency_key=f"{EVENT_EMAIL_STIMA_INVIATA}:{stima_id}",
    )


#: Gli hook, nell'ordine. Ciascuno riceve `(cur, message)` e scrive sul cursore
#: della finalizzazione. Un hook che solleva annulla il `sent`: e' voluto, ed e'
#: la ragione per cui questa lista resta corta e ogni voce e' una decisione.
HOOK_DOPO_INVIO = ((_e_la_mail_della_stima, _evento_email_stima),)


def dopo_invio(cur, message: dict[str, Any]) -> None:
    """Il seguito di un `sent`, sul cursore della sua transazione.

    Il dispatcher la chiama nel ramo `sent`, dentro il `with` che committa e
    SOLO se il compare-and-set ha restituito un esito. Un CAS perso non arriva
    qui: il messaggio non e' nostro, e scrivere un evento che dice "l'ho
    mandato io" sarebbe una bugia nel registro di un altro worker.

    `message` e' la riga, non il risultato della finalizzazione: chi chiama
    passa `esito["message"]`, e il predicato qui sotto si accerta che sia
    davvero quella.

    Non apre nessuna transazione e non committa: quella e' del chiamante, ed e'
    esattamente il punto.
    """
    for si_applica, esegui in HOOK_DOPO_INVIO:
        if si_applica(message):
            esegui(cur, message)
