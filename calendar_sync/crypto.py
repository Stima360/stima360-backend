"""A30-9A - cifratura applicativa dei segreti OAuth (refresh token, verifier PKCE).

Fernet (AES-128-CBC + HMAC-SHA256, `cryptography`) con un PORTACHIAVI letto da
una sola variabile d'ambiente:

    GOOGLE_TOKEN_FERNET_KEYS = "<key_id>:<chiave fernet>,<key_id>:<chiave>,..."

La PRIMA chiave e' quella corrente: cifra sempre lei. Le altre servono solo a
decifrare i ciphertext vecchi finche' non sono ri-cifrati (`rotate`,
MultiFernet). Ogni ciphertext viaggia con il suo `token_key_id` (colonna della
074), cosi' si sa sempre con quale chiave e' nato.

REGOLE
  * Nessuna chiave nel codice. Variabile assente o vuota: la funzione Google
    e' semplicemente "non configurata" (`is_configured()` False); l'import
    di questo modulo non legge l'ambiente e non fallisce mai, quindi il CRM
    si avvia comunque. Chi prova a cifrare/decifrare senza configurazione
    riceve `CalendarCryptoNotConfigured`, un errore controllato.
  * Nessun segreto in chiaro, nessun ciphertext e nessuna chiave in messaggi
    d'errore, `repr` o log: gli errori dicono COSA e' andato storto, mai CON
    COSA.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

ENV_KEYS = "GOOGLE_TOKEN_FERNET_KEYS"
_KEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class CalendarCryptoError(Exception):
    """Errore della cifratura. Il messaggio non contiene mai materiale segreto."""


class CalendarCryptoNotConfigured(CalendarCryptoError):
    """La variabile `GOOGLE_TOKEN_FERNET_KEYS` manca o e' vuota."""


@dataclass(frozen=True)
class Ciphertext:
    """Un segreto cifrato e l'id della chiave che lo ha cifrato."""
    value: bytes = field(repr=False)
    key_id: str

    def __repr__(self) -> str:  # mai il contenuto
        return f"Ciphertext(key_id={self.key_id!r}, bytes={len(self.value)})"


class Keyring:
    """Il portachiavi: coppie (key_id, Fernet), la prima e' la corrente."""

    def __init__(self, entries):
        from cryptography.fernet import Fernet

        entries = list(entries)
        if not entries:
            raise CalendarCryptoNotConfigured(f"{ENV_KEYS} non contiene chiavi")
        self._fernets = {}
        ordine = []
        for key_id, chiave in entries:
            if not isinstance(key_id, str) or not _KEY_ID.match(key_id):
                raise CalendarCryptoError(f"{ENV_KEYS}: id di chiave non valido")
            if key_id in self._fernets:
                raise CalendarCryptoError(f"{ENV_KEYS}: id di chiave duplicato")
            try:
                self._fernets[key_id] = Fernet(chiave)
            except Exception:  # noqa: BLE001 - il dettaglio conterrebbe la chiave
                raise CalendarCryptoError(
                    f"{ENV_KEYS}: la chiave {key_id!r} non e' una chiave Fernet valida") from None
            ordine.append(key_id)
        self._ordine = tuple(ordine)

    def __repr__(self) -> str:
        return f"Keyring(key_ids={list(self._ordine)!r})"

    @property
    def primary_key_id(self) -> str:
        return self._ordine[0]

    @property
    def key_ids(self) -> tuple:
        return self._ordine

    def encrypt(self, plaintext) -> Ciphertext:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        if not isinstance(plaintext, (bytes, bytearray)) or not plaintext:
            raise CalendarCryptoError("niente da cifrare")
        valore = self._fernets[self.primary_key_id].encrypt(bytes(plaintext))
        return Ciphertext(value=valore, key_id=self.primary_key_id)

    def decrypt(self, ciphertext) -> str:
        from cryptography.fernet import InvalidToken

        valore, key_id = _parti(ciphertext)
        fernet = self._fernets.get(key_id)
        if fernet is None:
            raise CalendarCryptoError(
                f"chiave {key_id!r} non disponibile: il segreto va ri-autorizzato")
        try:
            return fernet.decrypt(valore).decode("utf-8")
        except InvalidToken:
            raise CalendarCryptoError("decifratura non riuscita") from None

    def rotate(self, ciphertext) -> Ciphertext:
        """Ri-cifra con la chiave corrente (MultiFernet). Un ciphertext gia'
        della chiave corrente torna com'e'."""
        from cryptography.fernet import InvalidToken, MultiFernet

        valore, key_id = _parti(ciphertext)
        if key_id == self.primary_key_id:
            return Ciphertext(value=valore, key_id=key_id)
        if key_id not in self._fernets:
            raise CalendarCryptoError(
                f"chiave {key_id!r} non disponibile: il segreto va ri-autorizzato")
        multi = MultiFernet([self._fernets[k] for k in self._ordine])
        try:
            nuovo = multi.rotate(valore)
        except InvalidToken:
            raise CalendarCryptoError("decifratura non riuscita") from None
        return Ciphertext(value=nuovo, key_id=self.primary_key_id)


def _parti(ciphertext):
    if isinstance(ciphertext, Ciphertext):
        return ciphertext.value, ciphertext.key_id
    if isinstance(ciphertext, (tuple, list)) and len(ciphertext) == 2:
        valore, key_id = ciphertext
        return bytes(valore), str(key_id)
    raise CalendarCryptoError("ciphertext non valido")


def parse_keyring(raw: str) -> Keyring:
    """`id:chiave,id:chiave` -> Keyring. Le chiavi Fernet sono base64 urlsafe:
    non contengono ne' ':' ne' ','."""
    if raw is None or not raw.strip():
        raise CalendarCryptoNotConfigured(f"{ENV_KEYS} non impostata")
    voci = []
    for pezzo in raw.split(","):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        if ":" not in pezzo:
            raise CalendarCryptoError(f"{ENV_KEYS}: ogni voce e' <key_id>:<chiave>")
        key_id, chiave = pezzo.split(":", 1)
        voci.append((key_id.strip(), chiave.strip()))
    return Keyring(voci)


def load_keyring(environ=None):
    """Il portachiavi dall'ambiente, o None se la funzione non e' configurata.
    Una configurazione PRESENTE ma sbagliata e' un errore (fail closed)."""
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV_KEYS)
    if raw is None or not raw.strip():
        return None
    return parse_keyring(raw)


def require_keyring(environ=None) -> Keyring:
    keyring = load_keyring(environ)
    if keyring is None:
        raise CalendarCryptoNotConfigured(
            "Sincronizzazione calendario non configurata: manca la chiave di cifratura")
    return keyring


def is_configured(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    raw = environ.get(ENV_KEYS)
    return raw is not None and bool(raw.strip())
