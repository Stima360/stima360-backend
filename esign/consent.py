"""FIRMA OTP - la DICHIARAZIONE che il firmatario accetta, con versione e impronta.

Il testo e' fisso nel codice (non dipende da dati del cliente), riferito alla VERSIONE ESATTA del PDF tramite la sua
impronta SHA-256 inserita al momento: la pagina mostra il testo completo, il firmatario lo accetta esplicitamente (nessuna
casella preselezionata), il server registra testo, versione e impronta nel registro delle prove e nel rapporto.
Il testo e' una bozza da far rivedere a un legale prima dell'uso commerciale.
"""
from __future__ import annotations

import hashlib

VERSION = "dichiarazione_firma_otp_v1"
TEMPLATE = (
    "Dichiaro di aver letto integralmente il documento «{title}» (impronta SHA-256 {sha256}) e di volerlo "
    "sottoscrivere con firma elettronica semplice, confermando la mia volontà con il codice OTP che riceverò "
    "all'indirizzo email indicato. Sono consapevole che la sottoscrizione riguarda esclusivamente questa versione del "
    "documento e che i dati del procedimento (data e ora, indirizzo email, esito della verifica del codice, impronta "
    "dell'indirizzo IP e tipo di dispositivo) saranno conservati da STIMA360 e dall'agenzia come prova della firma."
)
PRIVACY_NOTE = (
    "I dati sono trattati dall'agenzia titolare dell'immobile e da STIMA360 al solo fine di raccogliere e conservare la "
    "sottoscrizione del foglio visita e le sue prove. Il codice OTP non viene mai conservato in chiaro. Per esercitare "
    "i tuoi diritti rivolgiti all'agenzia indicata nel documento."
)


def text(*, title: str, sha256: str) -> str:
    return TEMPLATE.format(title=title, sha256=sha256)


def digest(declaration: str) -> str:
    return hashlib.sha256(declaration.encode("utf-8")).hexdigest()
