"""A30-12 - token, hash, IP privacy. Nessuna query qui: solo funzioni pure
sul valore, cosi' i test non hanno bisogno di un database per verificarle.

D6 (token del link) e D7 (submission_token) condividono la stessa forma:
`secrets.token_urlsafe(32)` generato, SOLO il suo SHA-256 esadecimale (64
caratteri) persistito. Il valore grezzo non attraversa MAI un log - nessuna
funzione qui lo scrive in un formato diverso da quello che il chiamante gli
passa, e nessuna lo espone in un'eccezione.

D8 (IP): HMAC-SHA256(pepper, ip_canonico), mai SHA256(ip) semplice - lo
spazio IPv4 (2^32) e' troppo piccolo e un digest deterministico sarebbe
reversibile per dizionario in pochi minuti su hardware comune. Il pepper
viene da una env var dedicata (mai un default in questo file: senza pepper
configurato, l'assenza deve fermare l'avvio del flusso, non produrre un
hash debole in silenzio).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

#: D6/D7: la stessa lunghezza per entrambi i token opachi.
TOKEN_BYTES = 32

#: D8: il nome della env var dedicata al pepper dell'HMAC IP. Nessun
#: default: `hash_ip` rifiuta esplicitamente se non e' configurata.
IP_PEPPER_ENV_VAR = "PUBLIC_BOOKING_IP_PEPPER"


def generate_token() -> str:
    """D6/D7: un token opaco, urlsafe, 32 byte di entropia."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    """SHA-256 esadecimale (64 caratteri) - la SOLA forma persistita."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class IpPepperNotConfigured(RuntimeError):
    """D8: senza pepper non esiste un modo sicuro di derivare
    `client_ip_hash`. Fallire e' la sola risposta corretta - MAI degradare a
    SHA256(ip) semplice, e MAI persistere l'IP in chiaro come ripiego."""


def _canonical_ip(ip: str) -> str:
    """Normalizzazione minima: spazi ai bordi via, nient'altro. Non
    proviamo a comprimere/espandere IPv6 qui - la canonicalizzazione seria
    (es. `ipaddress.ip_address(ip).compressed`) e' compito del chiamante, se
    la vuole; questa funzione accetta cio' che le viene passato com'e', per
    restare pura e testabile senza rete."""
    return ip.strip()


def hash_ip(ip: str, *, pepper: str | None = None) -> str:
    """D8: HMAC-SHA256(pepper, ip_canonico) esadecimale (64 caratteri).

    `pepper` e' iniettabile per i test; in produzione viene letto da
    `PUBLIC_BOOKING_IP_PEPPER`. Nessun IP grezzo esce da questa funzione in
    nessuna forma diversa dal suo digest.
    """
    if pepper is None:
        pepper = os.environ.get(IP_PEPPER_ENV_VAR)
    if not pepper:
        raise IpPepperNotConfigured(
            f"A30-12: {IP_PEPPER_ENV_VAR} non configurata - "
            "impossibile derivare client_ip_hash in modo sicuro")
    canonico = _canonical_ip(ip)
    return hmac.new(pepper.encode("utf-8"), canonico.encode("utf-8"),
                     hashlib.sha256).hexdigest()
