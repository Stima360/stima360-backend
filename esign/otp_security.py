"""FIRMA OTP - i segreti del procedimento e la loro protezione. Nessun accesso al database qui.

COLLEGAMENTO PERSONALE   `secrets.token_urlsafe(32)` (256 bit): nel database solo lo SHA-256 esadecimale. Chi conosce
                         l'impronta non risale al token; il token sta solo nell'email e nel browser del firmatario.
CODICE OTP               6 cifre da `secrets.randbelow` (uniforme). Un codice a bassa entropia non si protegge con un
                         semplice SHA-256 (un milione di tentativi offline sono niente): si deriva con PBKDF2-HMAC-SHA256
                         (sale casuale, le stesse iterazioni delle password degli operatori) PIU' un PEPE del servizio
                         (`ESIGN_OTP_PEPPER`, variabile d'ambiente, mai nel database): senza il pepe la derivazione non
                         si ricostruisce nemmeno con il database in mano. Verifica a tempo costante; mai nei log.
DESTINATARI              nessun elenco di indirizzi ammessi: l'agente invia a qualsiasi email valida del cliente. Contro
                         abusi restano i limiti di frequenza, i tentativi, l'autenticazione e il blocco «non contattare».
IP                       mai in chiaro: SHA-256 di pepe + IP (`ip_hash`), come le altre pagine pubbliche del CRM.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

from operator_auth.security import hash_password, verify_password

PEPPER_ENV = "ESIGN_OTP_PEPPER"
EVIDENCE_KEY_ENV = "ESIGN_EVIDENCE_KEY"
MIN_SECRET_LENGTH = 32
TOKEN_BYTES = 32
OTP_DIGITS = 6
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OTP = re.compile(r"^\d{6}$")

#: Parametri del procedimento, con i valori iniziali richiesti; ogni variabile e' facoltativa.
DEFAULTS = {
    "ESIGN_OTP_TTL_MINUTES": 10,            # scadenza del codice
    "ESIGN_OTP_MAX_ATTEMPTS": 5,            # tentativi errati prima dell'invalidazione
    "ESIGN_OTP_RESEND_SECONDS": 60,         # attesa minima fra due codici allo stesso firmatario
    "ESIGN_OTP_MAX_PER_HOUR_SIGNER": 5,     # codici per firmatario e ora
    "ESIGN_OTP_MAX_PER_HOUR_IP": 20,        # codici per IP (impronta) e ora
    "ESIGN_LINK_MAX_PER_HOUR_REQUEST": 6,   # collegamenti nuovi (reinvii) per richiesta e ora
}


class NotConfigured(Exception):
    """Manca una configurazione necessaria: il procedimento non parte (mai un ripiego silenzioso)."""


def setting(name: str) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return DEFAULTS[name]
    try:
        value = int(raw)
    except ValueError as exc:
        raise NotConfigured(f"{name} non e' un intero") from exc
    if value < 1:
        raise NotConfigured(f"{name} deve essere positivo")
    return value


def _secret(env: str) -> str:
    value = (os.getenv(env) or "").strip()
    if len(value) < MIN_SECRET_LENGTH:
        raise NotConfigured(f"{env} assente o piu' corta di {MIN_SECRET_LENGTH} caratteri")
    return value


def pepper() -> str:
    return _secret(PEPPER_ENV)


def evidence_key() -> str:
    return _secret(EVIDENCE_KEY_ENV)


def require_configuration() -> None:
    """Tutto cio' che serve prima di creare una richiesta: pepe, chiave delle prove, parametri validi."""
    pepper()
    evidence_key()
    for name in DEFAULTS:
        setting(name)


# ---- destinatari ------------------------------------------------------------------------------------------------------

def is_valid_email(value) -> bool:
    return isinstance(value, str) and value == value.strip() and len(value) <= 320 and _EMAIL.match(value) is not None


# ---- collegamento personale -------------------------------------------------------------------------------------------

def new_access_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_access_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def looks_like_access_token(raw: str) -> bool:
    return isinstance(raw, str) and 16 <= len(raw) <= 200 and re.fullmatch(r"[A-Za-z0-9_-]+", raw) is not None


# ---- codice OTP -------------------------------------------------------------------------------------------------------

def new_otp() -> str:
    return f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"


def looks_like_otp(raw) -> bool:
    return isinstance(raw, str) and _OTP.fullmatch(raw) is not None


def _material(code: str, challenge_salt: str) -> str:
    return f"{pepper()}:{challenge_salt}:{code}"


def hash_otp(code: str, *, challenge_salt: str) -> str:
    """La derivazione lenta con pepe e sale di sfida (`pbkdf2_sha256$iter$salt$dk`)."""
    return hash_password(_material(code, challenge_salt))


def verify_otp(code: str, *, challenge_salt: str, stored: str) -> bool:
    if not looks_like_otp(code):
        return False
    try:
        return verify_password(_material(code, challenge_salt), stored)
    except NotConfigured:
        return False


def new_salt() -> str:
    return secrets.token_hex(16)


# ---- contesto minimizzato ---------------------------------------------------------------------------------------------

def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        key = pepper()
    except NotConfigured:
        return None
    return hmac.new(key.encode("utf-8"), ip.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def user_agent_short(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split())[:200]
