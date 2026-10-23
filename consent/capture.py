"""C3 - la raccolta DIGITALE del consenso: link personale, pagina, double opt-in.

Tre ingressi, una sola regola: un consenso nasce soltanto quando la persona lo
esprime, canale per canale, e passa dal percorso di scrittura di sempre
(`consent.service.record_grant` / `record_revocation` -> `consent_events` e
proiezione per canale della 109). Questo modulo non scrive mai la proiezione da
se' e non conosce il modulo delle comunicazioni: l'email di conferma la accoda
chi lo chiama, con la funzione `on_email_pending` che gli passa.

  * IL LINK PERSONALE. Lo genera un operatore per UN contatto (mai in blocco,
    mai da un processo). Si conserva solo l'impronta SHA-256 del token: il
    token esiste solo nella risposta che lo crea. Raccoglie consensi per 30
    giorni; dopo - o se sostituito da un link nuovo, o disattivato - serve
    SOLO a revocare. Revocare deve restare sempre possibile.

  * LA PAGINA. Mostra l'informativa corrente e quattro caselle VUOTE. Niente
    si scrive con una GET: la scelta parte da un POST esplicito. L'email
    scelta resta IN ATTESA (double opt-in, 7 giorni). SMS, WhatsApp e
    telefonate scelti qui sono solo DICHIARATI: nessun evento di consenso,
    quindi la guardia li nega, finche' il numero non sara' verificato.

  * IL FUNNEL DI STIMA360.IT. La casella email facoltativa crea la stessa
    richiesta in attesa, con la stima come contesto.

Nessuna funzione qui modifica un dato del contatto che non sia il consenso.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import repository
from . import service as consent_service
from .database import consent_cursor
from .enums import (
    ACTOR_SUBJECT,
    CHANNEL_EMAIL,
    CHANNEL_ORDER,
    CHANNELS,
    DECISION_REVOKED,
    PHONE_CHANNELS,
    PURPOSE_MARKETING,
    SOURCE_CONSENT_PAGE,
    SOURCE_PUBLIC_STIMA,
)
from .exceptions import ConflictError, ValidationError

#: Quanto raccoglie un link personale (approvato: 30 giorni).
LINK_TTL = timedelta(days=30)
#: Quanto vive la conferma dell'email (approvato: 7 giorni).
CONFIRM_TTL = timedelta(days=7)

#: L'origine del contesto di sistema della pagina pubblica (insieme chiuso in
#: operator_auth/context.py).
SYSTEM_ORIGIN = "public_consent"

ORIGIN_CONSENT_PAGE = "consent_page"
ORIGIN_PUBLIC_STIMA = "public_stima"

EVIDENCE_CONSENT_PAGE = "consent_page"
EVIDENCE_DOUBLE_OPT_IN = "double_opt_in"

#: `secrets.token_urlsafe(32)`: 32 byte di entropia, 43 caratteri urlsafe.
TOKEN_BYTES = 32
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

#: Contro gli abusi: (richieste, secondi) per finestra fissa.
LIMIT_VIEW = (60, 3600)
LIMIT_SUBMIT = (10, 3600)
LIMIT_CONFIRM = (10, 3600)
#: Quante email di conferma puo' generare un contatto in 24 ore.
MAX_CONFIRMATIONS_PER_DAY = 3

#: Il segreto con cui si cifra l'IP nella prova. Senza, l'IP non si registra
#: (mai in chiaro, mai con un hash semplice e quindi reversibile).
IP_PEPPER_ENV = "CONSENT_CAPTURE_IP_PEPPER"
USER_AGENT_MAX = 300


# ---------------------------------------------------------------------------
# token, impronte, IP
# ---------------------------------------------------------------------------

def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_well_formed(raw: str | None) -> bool:
    return isinstance(raw, str) and bool(_TOKEN_RE.match(raw))


def hash_ip(ip: str | None) -> str | None:
    pepper = (os.getenv(IP_PEPPER_ENV) or "").encode("utf-8")
    if not ip or len(pepper) < 16:
        return None
    return hmac.new(pepper, ip.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def _user_agent(valore: str | None) -> str | None:
    testo = (valore or "").strip()
    return testo[:USER_AGENT_MAX] or None


def _ora(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _sistema(agency_id: int, origin: str = SYSTEM_ORIGIN):
    from operator_auth.context import SystemAgencyContext
    return SystemAgencyContext(agency_id=agency_id, origin=origin)


def mask_email(indirizzo: str | None) -> str | None:
    if not indirizzo or "@" not in indirizzo:
        return None
    locale, dominio = indirizzo.split("@", 1)
    return f"{locale[:1]}{'•' * 3}@{dominio}"


def mask_phone(numero: str | None) -> str | None:
    cifre = re.sub(r"\D", "", numero or "")
    if len(cifre) < 6:
        return None
    prefisso = "+39 " if cifre.startswith("39") else "+"
    return f"{prefisso}••• ••• {cifre[-4:]}"


# ---------------------------------------------------------------------------
# letture condivise
# ---------------------------------------------------------------------------

def current_notice(cur, now: datetime) -> dict[str, Any] | None:
    """L'informativa marketing valida adesso, con i canali che copre."""
    cur.execute(
        """SELECT id, version, context, content, content_hash, channels, valid_from, valid_to
             FROM consent_notices
            WHERE purpose = %s AND content_available
              AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)
            ORDER BY valid_from DESC, id DESC LIMIT 1""",
        [PURPOSE_MARKETING, now, now])
    riga = cur.fetchone()
    return dict(riga) if riga is not None else None


def notice_at(cur, when: datetime) -> dict[str, Any] | None:
    """L'informativa marketing valida in `when` E GIA' REGISTRATA in `when`.

    C3.1: per una scelta fatta altrove (la casella del sito) l'informativa si
    associa solo se esisteva nel CRM in quel momento: `created_at` e'
    immutabile (061), quindi una versione registrata o retrodatata dopo non
    viene mai attribuita a una decisione precedente.
    """
    cur.execute(
        """SELECT id, version, context, content, content_hash, channels, valid_from, valid_to
             FROM consent_notices
            WHERE purpose = %s AND content_available AND created_at <= %s
              AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)
            ORDER BY valid_from DESC, id DESC LIMIT 1""",
        [PURPOSE_MARKETING, when, when, when])
    riga = cur.fetchone()
    return dict(riga) if riga is not None else None


def _consuma(cur, bucket: str, limite: tuple[int, int], now: datetime) -> bool:
    """Un colpo nel contatore a finestra fissa. True se ancora entro il limite."""
    massimo, secondi = limite
    inizio = datetime.fromtimestamp(int(now.timestamp()) // secondi * secondi, tz=timezone.utc)
    cur.execute(
        """INSERT INTO consent_page_rate_limits (bucket, window_start) VALUES (%s, %s)
           ON CONFLICT (bucket, window_start)
           DO UPDATE SET request_count = consent_page_rate_limits.request_count + 1
           RETURNING request_count""",
        [bucket[:120], inizio])
    return cur.fetchone()["request_count"] <= massimo


def _submissions(cur, ctx, contact_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT * FROM consent_submissions
            WHERE contact_id = %s AND agency_id = %s
            ORDER BY submitted_at DESC, id DESC LIMIT 100""",
        [contact_id, ctx.require_agency()])
    return [dict(r) for r in cur.fetchall()]


def _revocato_dopo(cur, ctx, contact_id: int, channel: str, quando: datetime) -> bool:
    ultimo = repository.latest_event(cur, ctx, contact_id, PURPOSE_MARKETING, channel)
    return (ultimo is not None and ultimo["decision"] == DECISION_REVOKED
            and ultimo["decided_at"] > quando)


def channel_overview(cur, ctx, contact: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    """Per ogni canale: attivo (proiezione), email in attesa di conferma,
    canale telefonico dichiarato ma non verificato, indirizzo presente."""
    righe = repository.channel_rows(cur, ctx, contact["id"])
    invii = _submissions(cur, ctx, contact["id"])
    stato: dict[str, dict[str, Any]] = {}
    for canale in CHANNEL_ORDER:
        riga = righe.get(canale)
        attivo = riga is not None and riga["status"] == "granted"
        in_attesa = dichiarato = None
        if canale == CHANNEL_EMAIL:
            for s in invii:
                if s["email_status"] == "pending" and s["confirm_expires_at"] > now:
                    in_attesa = {"submission_id": s["id"], "submitted_at": s["submitted_at"],
                                 "expires_at": s["confirm_expires_at"], "origin": s["origin"]}
                    break
        else:
            for s in invii:
                if canale in s["channels_revoked"]:
                    break
                if canale in s["phone_channels_declared"]:
                    if not _revocato_dopo(cur, ctx, contact["id"], canale, s["submitted_at"]):
                        dichiarato = {"submission_id": s["id"], "declared_at": s["submitted_at"]}
                    break
        stato[canale] = {
            "active": attivo,
            "granted_at": riga["granted_at"] if attivo else None,
            "pending_confirmation": in_attesa,
            "declared_unverified": dichiarato,
            "has_address": bool(repository.contact_address(contact, canale)),
        }
    return stato


def _registrabile(contact: dict[str, Any]) -> bool:
    return contact.get("deleted_at") is None and contact.get("status") != "archived"


# ---------------------------------------------------------------------------
# il link personale (CRM)
# ---------------------------------------------------------------------------

#: C3.2 - i canali che il testo della casella marketing del sito pubblicato
#: nomina («... tramite email e WhatsApp»). Servono SOLO a mostrare la scelta
#: registrata dal sito: non autorizzano nulla.
SITE_CHECKBOX_CHANNELS = ("email", "whatsapp")


def site_choices(cur, ctx, contact_id: int) -> list[dict[str, Any]]:
    """C3.2 - le spunte marketing arrivate dal sito (stima360.it) per questo
    contatto: gli eventi SENZA canale che il bridge pubblico scrive (fonte
    `public_stima`, prova `stima:<id>`). Prova conservata, nessuna
    autorizzazione: la guardia li ignora. Tutte, non solo l'ultima: un
    consenso per canale registrato dopo non deve far sparire lo storico."""
    cur.execute(
        """SELECT id, decision, decided_at, created_at, evidence_type, evidence_ref, notice_id
             FROM consent_events
            WHERE agency_id = %s AND contact_id = %s AND purpose = %s AND channel IS NULL
              AND source = 'public_stima'
            ORDER BY decided_at, id""",
        [ctx.require_agency(), contact_id, PURPOSE_MARKETING])
    scelte = []
    for r in cur.fetchall():
        rif = r["evidence_ref"] if r["evidence_type"] == "stima" else None
        scelte.append({
            "event_id": r["id"], "decision": r["decision"], "decided_at": r["decided_at"],
            "recorded_at": r["created_at"], "evidence_type": r["evidence_type"],
            "stima_id": int(rif) if rif and str(rif).isdigit() else None, "notice_id": r["notice_id"],
        })
    return scelte


def _link_aperto(cur, ctx, contact_id: int) -> dict[str, Any] | None:
    cur.execute(
        """SELECT * FROM consent_links
            WHERE contact_id = %s AND agency_id = %s
              AND superseded_at IS NULL AND disabled_at IS NULL""",
        [contact_id, ctx.require_agency()])
    riga = cur.fetchone()
    return dict(riga) if riga is not None else None


def create_link(ctx, contact_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Un link personale nuovo per UN contatto. Restituisce il token in chiaro
    UNA volta: nel database c'e' solo la sua impronta. Il link precedente,
    se aperto, viene sostituito e da quel momento serve solo a revocare."""
    from .operator_service import _operatore
    _operatore(ctx)
    quando = _ora(now)
    with consent_cursor(commit=True) as (_, cur):
        contact = repository.lock_contact(cur, ctx, contact_id)
        if not _registrabile(contact):
            raise ConflictError("the contact is in the trash or archived: no consent link can be generated")
        cur.execute(
            """UPDATE consent_links SET superseded_at = %s
                WHERE contact_id = %s AND agency_id = %s
                  AND superseded_at IS NULL AND disabled_at IS NULL""",
            [quando, contact_id, ctx.require_agency()])
        token = generate_token()
        cur.execute(
            """INSERT INTO consent_links (agency_id, contact_id, token_hash, created_by_user_id,
                                          created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            [ctx.require_agency(), contact_id, hash_token(token), ctx.user_id, quando, quando + LINK_TTL])
        riga = dict(cur.fetchone())
    return {"token": token, "link": riga}


def disable_link(ctx, contact_id: int, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Disattiva il link aperto: da quel momento serve solo a revocare."""
    from .operator_service import _operatore
    _operatore(ctx)
    quando = _ora(now)
    with consent_cursor(commit=True) as (_, cur):
        repository.lock_contact(cur, ctx, contact_id)
        cur.execute(
            """UPDATE consent_links SET disabled_at = %s, disabled_by_user_id = %s
                WHERE contact_id = %s AND agency_id = %s
                  AND superseded_at IS NULL AND disabled_at IS NULL
                RETURNING *""",
            [quando, ctx.user_id, contact_id, ctx.require_agency()])
        riga = cur.fetchone()
    return dict(riga) if riga is not None else None


def capture_state(ctx, contact_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Per la scheda contatto: link aperto (senza token), canali in attesa."""
    quando = _ora(now)
    with consent_cursor() as (_, cur):
        contact = repository.select_contact(cur, ctx, contact_id)
        link = _link_aperto(cur, ctx, contact_id)
        panorama = channel_overview(cur, ctx, contact, quando)
        informativa = current_notice(cur, quando)
    return {
        # C3.2: senza un'informativa marketing corrente il link si apre ma
        # permette solo di revocare; la scheda lo dice PRIMA di generarlo.
        "notice": None if informativa is None else {
            "id": informativa["id"], "version": informativa["version"],
            "channels": list(informativa.get("channels") or [])},
        "link": None if link is None else {
            "id": link["id"], "created_at": link["created_at"], "expires_at": link["expires_at"],
            "created_by_user_id": link["created_by_user_id"],
            "collecting": link["expires_at"] > quando,
        },
        "channels": {c: {"pending_confirmation": v["pending_confirmation"],
                         "declared_unverified": v["declared_unverified"]} for c, v in panorama.items()},
        "link_ttl_days": LINK_TTL.days,
        "confirm_ttl_days": CONFIRM_TTL.days,
    }


# ---------------------------------------------------------------------------
# la pagina pubblica
# ---------------------------------------------------------------------------

def resolve_link(raw_token: str | None, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Il link dietro un token, o None. `mode` e' `collect` (si possono dare
    consensi) o `revoke_only` (scaduto, sostituito o disattivato)."""
    if not token_well_formed(raw_token):
        return None
    quando = _ora(now)
    with consent_cursor() as (_, cur):
        cur.execute("SELECT * FROM consent_links WHERE token_hash = %s", [hash_token(raw_token)])
        riga = cur.fetchone()
    if riga is None:
        return None
    link = dict(riga)
    raccoglie = (link["superseded_at"] is None and link["disabled_at"] is None
                 and link["expires_at"] > quando)
    return {"link": link, "mode": "collect" if raccoglie else "revoke_only"}


def page_state(risolto: dict[str, Any], *, now: datetime | None = None,
               count_view: bool = True) -> dict[str, Any] | None:
    """Cio' che la pagina mostra. None se il contatto non e' piu' raggiungibile.
    `limited=True` se il link ha superato il limite di visualizzazioni."""
    quando = _ora(now)
    link = risolto["link"]
    ctx = _sistema(link["agency_id"])
    with consent_cursor(commit=True) as (_, cur):
        if count_view and not _consuma(cur, f"view:{link['id']}", LIMIT_VIEW, quando):
            return {"limited": True}
        try:
            contact = repository.select_contact(cur, ctx, link["contact_id"])
        except Exception:  # noqa: BLE001 - un contatto sparito e' una pagina neutra
            return None
        if not _registrabile(contact):
            return None
        informativa = current_notice(cur, quando)
        panorama = channel_overview(cur, ctx, contact, quando)
        dal_sito = [s for s in site_choices(cur, ctx, contact["id"]) if s["decision"] == "granted"]
        cur.execute("SELECT name FROM agencies WHERE id = %s", [link["agency_id"]])
        agenzia = cur.fetchone()
    coperti = set((informativa or {}).get("channels") or ())
    return {
        "limited": False,
        "mode": risolto["mode"],
        "agency_name": agenzia["name"] if agenzia else None,
        "notice": informativa,
        "channels": {c: {**v, "covered": c in coperti} for c, v in panorama.items()},
        "email_masked": mask_email(contact.get("email_normalized")),
        "phone_masked": mask_phone(contact.get("phone_normalized")),
        "link_expires_at": link["expires_at"],
        # C3.2: la spunta piu' recente fatta sul sito, solo per ricordarla alla
        # persona; nessuna casella viene preselezionata.
        "site_choice": None if not dal_sito else {"decided_at": dal_sito[-1]["decided_at"]},
    }


def submit(risolto: dict[str, Any], *, grant, revoke, ip: str | None, user_agent: str | None,
           on_email_pending: Callable[..., int | None], now: datetime | None = None) -> dict[str, Any]:
    """Le scelte della persona, in UNA transazione.

    Restituisce `{"status": ..., ...}`; mai un'eccezione per un caso atteso,
    perche' la pagina risponde in modo neutro. Concede solo con il link che
    raccoglie, solo canali coperti dall'informativa corrente e con un
    indirizzo; revoca SEMPRE (anche a link scaduto) i canali attivi, in
    attesa o dichiarati.
    """
    quando = _ora(now)
    link = risolto["link"]
    ctx = _sistema(link["agency_id"])
    concedi = [c for c in CHANNEL_ORDER if c in set(grant or ())]
    revoca = [c for c in CHANNEL_ORDER if c in set(revoke or ())]
    with consent_cursor(commit=True) as (_, cur):
        # Il contatore si consuma PRIMA della validazione: anche gli invii
        # vuoti o malformati contano contro l'abuso del link.
        if not _consuma(cur, f"submit:{link['id']}", LIMIT_SUBMIT, quando):
            return {"status": "limited"}
        if set(grant or ()) - CHANNELS or set(revoke or ()) - CHANNELS:
            return {"status": "invalid"}
        if set(concedi) & set(revoca):
            return {"status": "invalid"}
        if not concedi and not revoca:
            return {"status": "nothing"}
        if concedi and risolto["mode"] != "collect":
            return {"status": "collect_closed"}
        contact = repository.lock_contact(cur, ctx, link["contact_id"])
        if not _registrabile(contact):
            return {"status": "unavailable"}
        panorama = channel_overview(cur, ctx, contact, quando)
        informativa = current_notice(cur, quando) if concedi else None
        coperti = set((informativa or {}).get("channels") or ())
        for canale in concedi:
            v = panorama[canale]
            if canale not in coperti or not v["has_address"]:
                return {"status": "not_allowed", "channel": canale}
            if v["active"] or v["pending_confirmation"] or v["declared_unverified"]:
                return {"status": "not_allowed", "channel": canale}
        for canale in revoca:
            v = panorama[canale]
            if not (v["active"] or v["pending_confirmation"] or v["declared_unverified"]):
                return {"status": "not_allowed", "channel": canale}
        if CHANNEL_EMAIL in concedi:
            cur.execute(
                """SELECT count(*) AS n FROM consent_submissions
                    WHERE contact_id = %s AND agency_id = %s AND email_status <> 'none'
                      AND submitted_at > %s""",
                [contact["id"], ctx.require_agency(), quando - timedelta(days=1)])
            if cur.fetchone()["n"] >= MAX_CONFIRMATIONS_PER_DAY:
                return {"status": "limited"}

        telefonici = [c for c in concedi if c in PHONE_CHANNELS]
        conferma = generate_token() if CHANNEL_EMAIL in concedi else None
        cur.execute(
            """INSERT INTO consent_submissions
                   (agency_id, contact_id, link_id, origin, notice_id, notice_content_hash,
                    channels_requested, channels_revoked, phone_channels_declared, phone_number_snapshot,
                    submitted_at, ip_hash, user_agent, email_status, email_address,
                    confirm_token_hash, confirm_expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            [ctx.require_agency(), contact["id"], link["id"], ORIGIN_CONSENT_PAGE,
             informativa["id"] if informativa else None,
             informativa["content_hash"] if informativa else None,
             concedi, revoca, telefonici,
             contact.get("phone_normalized") if telefonici else None,
             quando, hash_ip(ip), _user_agent(user_agent),
             "pending" if conferma else "none",
             contact.get("email_normalized") if conferma else None,
             hash_token(conferma) if conferma else None,
             quando + CONFIRM_TTL if conferma else None])
        invio = dict(cur.fetchone())

        revocati = []
        for canale in revoca:
            if panorama[canale]["active"]:
                esito = consent_service.record_revocation(
                    ctx, contact_id=contact["id"], purpose=PURPOSE_MARKETING, channel=canale,
                    source=SOURCE_CONSENT_PAGE, actor_type=ACTOR_SUBJECT, decided_at=quando,
                    evidence_type=EVIDENCE_CONSENT_PAGE, evidence_ref=f"consent_submission:{invio['id']}",
                    idempotency_key=f"consent_page:revoke:{invio['id']}:{canale}", cur=cur)
                revocati.append({"channel": canale, "event_id": esito["event"]["id"]})
            if canale == CHANNEL_EMAIL:
                cur.execute(
                    """UPDATE consent_submissions SET email_status = 'cancelled'
                        WHERE contact_id = %s AND agency_id = %s AND email_status = 'pending'""",
                    [contact["id"], ctx.require_agency()])

        messaggio = None
        if conferma:
            messaggio = on_email_pending(cur, ctx, invio, conferma)
            if messaggio is not None:
                cur.execute("UPDATE consent_submissions SET confirmation_message_id = %s WHERE id = %s",
                            [messaggio, invio["id"]])
    return {"status": "recorded", "submission_id": invio["id"], "granted_pending_email": bool(conferma),
            "declared_phone": telefonici, "revoked": revoca, "revoked_events": revocati,
            "confirmation_message_id": messaggio}


# ---------------------------------------------------------------------------
# il double opt-in
# ---------------------------------------------------------------------------

def resolve_confirmation(raw_token: str | None) -> dict[str, Any] | None:
    if not token_well_formed(raw_token):
        return None
    with consent_cursor() as (_, cur):
        cur.execute("SELECT * FROM consent_submissions WHERE confirm_token_hash = %s",
                    [hash_token(raw_token)])
        riga = cur.fetchone()
    return dict(riga) if riga is not None else None


def confirm_email(raw_token: str | None, *, ip: str | None, user_agent: str | None,
                  now: datetime | None = None) -> dict[str, Any]:
    """Il click sul pulsante di conferma (POST). Concede l'email UNA volta.

    Rifiuta se la richiesta e' scaduta (7 giorni), annullata, gia' usata, se
    l'indirizzo del contatto e' cambiato, se dopo la richiesta c'e' stata una
    revoca dell'email o una disiscrizione di piattaforma di quell'indirizzo.
    """
    quando = _ora(now)
    invio = resolve_confirmation(raw_token)
    if invio is None:
        return {"status": "unknown"}
    ctx = _sistema(invio["agency_id"],
                   SYSTEM_ORIGIN if invio["origin"] == ORIGIN_CONSENT_PAGE else ORIGIN_PUBLIC_STIMA)
    with consent_cursor(commit=True) as (_, cur):
        if not _consuma(cur, f"confirm:{invio['id']}", LIMIT_CONFIRM, quando):
            return {"status": "limited"}
        contact = repository.lock_contact(cur, ctx, invio["contact_id"])
        cur.execute("SELECT * FROM consent_submissions WHERE id = %s FOR UPDATE", [invio["id"]])
        invio = dict(cur.fetchone())
        if invio["email_status"] == "confirmed":
            return {"status": "already_confirmed"}
        if invio["email_status"] != "pending":
            return {"status": invio["email_status"]}

        def chiudi(stato: str) -> dict[str, Any]:
            cur.execute("UPDATE consent_submissions SET email_status = %s WHERE id = %s",
                        [stato, invio["id"]])
            return {"status": stato}

        if invio["confirm_expires_at"] <= quando:
            return chiudi("expired")
        if not _registrabile(contact):
            return chiudi("cancelled")
        if contact.get("email_normalized") != invio["email_address"]:
            return chiudi("cancelled") | {"reason": "address_changed"}
        if _revocato_dopo(cur, ctx, contact["id"], CHANNEL_EMAIL, invio["submitted_at"]):
            return chiudi("cancelled") | {"reason": "revoked_after_request"}
        optout = repository.latest_platform_optout(cur, CHANNEL_EMAIL, invio["email_address"])
        if optout is not None and optout["opted_out_at"] > invio["submitted_at"]:
            return chiudi("cancelled") | {"reason": "opted_out_after_request"}

        sorgente = SOURCE_CONSENT_PAGE if invio["origin"] == ORIGIN_CONSENT_PAGE else SOURCE_PUBLIC_STIMA
        esito = consent_service.record_grant(
            ctx, contact_id=contact["id"], purpose=PURPOSE_MARKETING, channel=CHANNEL_EMAIL,
            source=sorgente, actor_type=ACTOR_SUBJECT, decided_at=quando, notice_id=invio["notice_id"],
            evidence_type=EVIDENCE_DOUBLE_OPT_IN, evidence_ref=f"consent_submission:{invio['id']}",
            note=f"richiesta del {invio['submitted_at'].isoformat()}, confermata via email",
            idempotency_key=f"double_opt_in:{invio['id']}", cur=cur)
        cur.execute(
            """UPDATE consent_submissions
                  SET email_status = 'confirmed', confirmed_at = %s, confirm_ip_hash = %s,
                      confirm_user_agent = %s, consent_event_id = %s
                WHERE id = %s""",
            [quando, hash_ip(ip), _user_agent(user_agent), esito["event"]["id"], invio["id"]])
    return {"status": "confirmed", "event_id": esito["event"]["id"], "submission_id": invio["id"]}


# ---------------------------------------------------------------------------
# la casella email del funnel di stima360.it
# ---------------------------------------------------------------------------

def request_from_public_stima(ctx, *, contact_id: int, stima_id: int, notice_id,
                              on_email_pending: Callable[..., int | None],
                              now: datetime | None = None,
                              decided_at: datetime | None = None) -> dict[str, Any]:
    """La casella email spuntata sul sito: una richiesta IN ATTESA di
    conferma, idempotente per stima. Non concede niente da sola.

    C3.1: con `decided_at` (la casella del sito pubblicato, che non manda un
    id di informativa) l'informativa e' quella di `notice_at(decided_at)`,
    scelta dal server; `notice_id` deve essere None."""
    quando = _ora(now)
    if decided_at is None and (not isinstance(notice_id, int) or isinstance(notice_id, bool)
                               or notice_id <= 0):
        return {"status": "notice_missing"}
    with consent_cursor(commit=True) as (_, cur):
        cur.execute("SELECT id FROM consent_submissions WHERE idempotency_key = %s",
                    [f"public_stima:{stima_id}:email"])
        if cur.fetchone() is not None:
            return {"status": "already_requested"}
        if decided_at is not None:
            informativa = notice_at(cur, decided_at)
            if informativa is None or CHANNEL_EMAIL not in (informativa.get("channels") or ()):
                return {"status": "notice_missing"}
        else:
            informativa = current_notice(cur, quando)
            if (informativa is None or informativa["id"] != notice_id
                    or CHANNEL_EMAIL not in (informativa.get("channels") or ())):
                return {"status": "notice_invalid"}
        contact = repository.lock_contact(cur, ctx, contact_id)
        if not _registrabile(contact) or not contact.get("email_normalized"):
            return {"status": "no_email"}
        panorama = channel_overview(cur, ctx, contact, quando)
        if panorama[CHANNEL_EMAIL]["active"]:
            return {"status": "already_granted"}
        if panorama[CHANNEL_EMAIL]["pending_confirmation"]:
            return {"status": "already_pending"}
        cur.execute(
            """SELECT count(*) AS n FROM consent_submissions
                WHERE contact_id = %s AND agency_id = %s AND email_status <> 'none'
                  AND submitted_at > %s""",
            [contact_id, ctx.require_agency(), quando - timedelta(days=1)])
        if cur.fetchone()["n"] >= MAX_CONFIRMATIONS_PER_DAY:
            return {"status": "limited"}
        conferma = generate_token()
        cur.execute(
            """INSERT INTO consent_submissions
                   (agency_id, contact_id, stima_id, origin, notice_id, notice_content_hash,
                    channels_requested, submitted_at, email_status, email_address,
                    confirm_token_hash, confirm_expires_at, idempotency_key)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s) RETURNING *""",
            [ctx.require_agency(), contact_id, stima_id, ORIGIN_PUBLIC_STIMA, informativa["id"],
             informativa["content_hash"], [CHANNEL_EMAIL], quando, contact["email_normalized"],
             hash_token(conferma), quando + CONFIRM_TTL, f"public_stima:{stima_id}:email"])
        invio = dict(cur.fetchone())
        messaggio = on_email_pending(cur, ctx, invio, conferma)
        if messaggio is not None:
            cur.execute("UPDATE consent_submissions SET confirmation_message_id = %s WHERE id = %s",
                        [messaggio, invio["id"]])
    return {"status": "queued", "submission_id": invio["id"], "confirmation_message_id": messaggio}


__all__ = [
    "LINK_TTL", "CONFIRM_TTL", "create_link", "disable_link", "capture_state", "resolve_link",
    "page_state", "submit", "confirm_email", "request_from_public_stima", "current_notice",
    "notice_at", "site_choices", "SITE_CHECKBOX_CHANNELS", "mask_email", "mask_phone", "ValidationError",
]
