"""FIRMA OTP - la DICHIARAZIONE che il firmatario accetta, con versione e impronta.

Il testo e' fisso nel codice (non dipende da dati del cliente). Il firmatario lo accetta con un'azione esplicita; il
server registra nel registro delle prove la versione, l'impronta SHA-256 del testo accettato e l'impronta SHA-256 del
documento firmato: e' questa registrazione (sigillata nella catena delle prove) a legare la dichiarazione all'esatta
versione del PDF.

v1 (`dichiarazione_firma_otp_v1`) riportava l'impronta del documento dentro il testo. v2 e' la versione semplificata:
linguaggio piano, nessuna impronta visibile, il documento identificato da titolo, immobile e data della visita come li
vede il cliente; il legame crittografico resta nelle prove (`document_sha256` accanto a `consent_sha256`).
Le dichiarazioni gia' accettate restano quelle: ogni versione ha il suo testo qui, e il rapporto delle prove usa la
versione realmente registrata.

ENTRAMBI I TESTI SONO BOZZE DA FAR VERIFICARE A UN LEGALE PRIMA DELL'USO COMMERCIALE.
"""
from __future__ import annotations

import hashlib

VERSION_V1 = "dichiarazione_firma_otp_v1"
VERSION_V2 = "dichiarazione_firma_otp_v2"
#: La versione proposta oggi a chi firma.
VERSION = VERSION_V2

TEMPLATE_V1 = (
    "Dichiaro di aver letto integralmente il documento «{title}» (impronta SHA-256 {sha256}) e di volerlo "
    "sottoscrivere con firma elettronica semplice, confermando la mia volontà con il codice OTP che riceverò "
    "all'indirizzo email indicato. Sono consapevole che la sottoscrizione riguarda esclusivamente questa versione del "
    "documento e che i dati del procedimento (data e ora, indirizzo email, esito della verifica del codice, impronta "
    "dell'indirizzo IP e tipo di dispositivo) saranno conservati da STIMA360 e dall'agenzia come prova della firma."
)
TEMPLATE_V2 = (
    "Dichiaro di aver letto il documento «{title}»{where} e di volerlo firmare con firma elettronica semplice. "
    "Confermo la mia volontà con il codice che riceverò via email. La firma vale solo per questa versione del "
    "documento. Data e ora, indirizzo email, esito del codice, impronta dell'indirizzo IP e tipo di dispositivo "
    "saranno conservati come prova della firma."
)
TEMPLATES = {VERSION_V1: TEMPLATE_V1, VERSION_V2: TEMPLATE_V2}
PRIVACY_NOTE = (
    "I dati sono trattati dall'agenzia titolare dell'immobile e da STIMA360 al solo fine di raccogliere e conservare la "
    "sottoscrizione del foglio visita e le sue prove. Il codice OTP non viene mai conservato in chiaro. Per esercitare "
    "i tuoi diritti rivolgiti all'agenzia indicata nel documento."
)


def text(*, title: str, sha256: str, address: str | None = None, visit_text: str | None = None,
         document_version: int | None = None, version: str = VERSION) -> str:
    """Il testo della dichiarazione nella versione indicata (di default quella proposta oggi). La v2 identifica il
    documento come lo vede il cliente (immobile, data della visita, versione del foglio): nessun identificativo interno."""
    if version == VERSION_V1:
        return TEMPLATE_V1.format(title=title, sha256=sha256)
    parti = [x for x in (f"immobile {address}" if address else None, f"visita del {visit_text}" if visit_text else None,
                         f"versione {document_version}" if document_version else None) if x]
    where = f" ({', '.join(parti)})" if parti else ""
    return TEMPLATES[version].format(title=title, where=where)


def digest(declaration: str) -> str:
    return hashlib.sha256(declaration.encode("utf-8")).hexdigest()
