"""C3 - la pagina pubblica del consenso, l'email di conferma, il link con QR.

Le DECISIONI vivono in `consent.capture`; qui c'e' cio' che appartiene alle
comunicazioni: l'HTML delle pagine pubbliche, l'email di conferma accodata nel
ledger come comunicazione di SERVIZIO, l'URL pubblico e il suo QR.

LE PAGINE
  * generate dal server, senza JavaScript, ogni valore passato da `escape`;
  * mai in cache, mai indicizzate, mai incorniciabili in un'altra pagina,
    nessun `Referer` verso l'esterno (il token e' nell'URL);
  * una GET mostra e basta: ogni scelta parte da un POST esplicito, perche' i
    client di posta e gli antispam aprono i link da soli;
  * un token sconosciuto, alterato o scaduto riceve la stessa pagina neutra.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from html import escape
from typing import Any

from consent import capture

from . import email_metadata
from . import service as comm_service
from .enums import CHANNEL_EMAIL, MODE_AUTOMATIC, REASON_CONSENT_CONFIRMATION, TYPE_SERVICE

PUBLIC_PREFIX = "/api/public/communication"
TEMPLATE_KEY = "consent_confirmation"
TEMPLATE_VERSION = 1

CANALI = {"email": "Email", "sms": "SMS", "whatsapp": "WhatsApp", "phone": "Telefonate commerciali"}

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
}

NEUTRAL = "Il link non è valido o non è più disponibile. Se hai bisogno di aiuto, contatta l'agenzia che te lo ha inviato."


# ---------------------------------------------------------------------------
# URL, QR
# ---------------------------------------------------------------------------

def _base() -> str:
    from .journey_tick import _base_pubblica
    return _base_pubblica()


def page_url(token: str) -> str:
    return f"{_base()}{PUBLIC_PREFIX}/consent/{token}"


def confirm_url(token: str) -> str:
    return f"{_base()}{PUBLIC_PREFIX}/consent/confirm/{token}"


def notice_url() -> str:
    return f"{_base()}{PUBLIC_PREFIX}/consent/informativa"


def qr_data_uri(url: str) -> str:
    """Il QR come immagine SVG in un data URI (nessun file, nessuna rete)."""
    import segno
    return segno.make(url, error="m").svg_data_uri(scale=6, border=2)


def generate_link(ctx, contact_id: int) -> dict[str, Any]:
    """CRM: un link personale nuovo, il suo URL e il suo QR. Il token compare
    SOLO in questa risposta: il database ne conserva l'impronta."""
    _base()  # senza un URL pubblico configurato non si genera nulla
    esito = capture.create_link(ctx, contact_id)
    url = page_url(esito["token"])
    return {"url": url, "qr_svg_data_uri": qr_data_uri(url),
            "expires_at": esito["link"]["expires_at"], "link_id": esito["link"]["id"]}


# ---------------------------------------------------------------------------
# l'email di conferma (servizio)
# ---------------------------------------------------------------------------

def _corpo_conferma(nome: str | None, url: str, versione: str | None) -> tuple[str, str]:
    saluto = f"Ciao {nome.strip()}," if nome and nome.strip() else "Ciao,"
    oggetto = "Conferma la tua email per gli aggiornamenti di STIMA360"
    righe = [
        saluto,
        "",
        "hai chiesto di ricevere via email analisi di mercato e aggiornamenti da STIMA360.",
        f"Per attivare l'iscrizione conferma il tuo indirizzo entro {capture.CONFIRM_TTL.days} giorni:",
        "",
        url,
        "",
        "Se non sei stato tu, ignora questa email: senza conferma non riceverai comunicazioni commerciali.",
    ]
    if versione:
        righe += ["", f"Informativa privacy, versione {versione}: {notice_url()}"]
    return oggetto, "\n".join(righe) + "\n"


def enqueue_confirmation(cur, ctx, invio: dict[str, Any], raw_token: str) -> int:
    """Accoda l'email di conferma nella STESSA transazione dell'invio della
    pagina: o tutte e due, o nessuna. La spedisce il dispatcher."""
    cur.execute("SELECT first_name FROM contacts WHERE id = %s AND agency_id = %s",
                [invio["contact_id"], invio["agency_id"]])
    riga = cur.fetchone()
    cur.execute("SELECT version FROM consent_notices WHERE id = %s", [invio["notice_id"]])
    informativa = cur.fetchone()
    oggetto, corpo = _corpo_conferma(riga["first_name"] if riga else None, confirm_url(raw_token),
                                     informativa["version"] if informativa else None)
    esito = comm_service.enqueue(
        ctx, contact_id=invio["contact_id"], stima_id=invio.get("stima_id"),
        channel=CHANNEL_EMAIL, communication_type=TYPE_SERVICE, mode=MODE_AUTOMATIC,
        reason_code=REASON_CONSENT_CONFIRMATION, template_key=TEMPLATE_KEY,
        template_version=TEMPLATE_VERSION, destination_snapshot=invio["email_address"],
        subject_snapshot=oggetto, rendered_body=corpo,
        idempotency_key=f"{REASON_CONSENT_CONFIRMATION}:{invio['id']}",
        metadata={**email_metadata.metadata_testo(communication_type=TYPE_SERVICE),
                  "consent_submission_id": invio["id"],
                  "confirm_expires_at": invio["confirm_expires_at"].isoformat()},
        cur=cur)
    return esito["message"]["id"]


# ---------------------------------------------------------------------------
# il funnel di stima360.it
# ---------------------------------------------------------------------------

def request_from_public_stima(ctx, *, raw: dict[str, Any], stima_id: int,
                              contact_id: int | None) -> dict[str, Any]:
    """La casella email del sito. `not_requested` se non e' spuntata (o se il
    sito vecchio manda solo `consenso_marketing`, che resta una spunta senza
    canale e non autorizza niente)."""
    if raw.get("consenso_email") is not True or not contact_id:
        return {"status": "not_requested"}
    try:
        notice_id = int(raw.get("informativa_marketing_id"))
    except (TypeError, ValueError):
        return {"status": "notice_missing"}
    try:
        _base()
    except Exception:  # noqa: BLE001 - senza URL pubblico la conferma non avrebbe un link
        return {"status": "not_configured"}
    return capture.request_from_public_stima(
        ctx, contact_id=contact_id, stima_id=stima_id, notice_id=notice_id,
        on_email_pending=enqueue_confirmation)


#: C3.1 - l'interruttore del double opt-in per la casella del sito pubblicato.
#: SPENTO salvo il valore esplicito "on": l'import porta nel CRM richieste di
#: persone reali, e la prima email non deve partire per distrazione.
SITE_DOUBLE_OPT_IN_ENV = "SITE_CONSENT_DOUBLE_OPT_IN"


def site_double_opt_in_enabled() -> bool:
    return (os.getenv(SITE_DOUBLE_OPT_IN_ENV) or "").strip().lower() == "on"


def request_from_site_checkbox(ctx, *, stima_id: int, contact_id: int | None,
                               consenso_marketing, consenso_marketing_at: datetime | None) -> dict[str, Any]:
    """C3.1 - la casella del sito pubblicato («email e WhatsApp»), cosi' come
    il sito la manda oggi: `consenso_marketing` e l'istante registrato dal
    server del sito. Prepara SOLO la conferma via email (double opt-in);
    WhatsApp non e' toccato: resta nella spunta senza canale del bridge, prova
    e nessuna autorizzazione. L'informativa la sceglie il server per data
    (`capture.notice_at`): mai un id dal client, mai una versione registrata
    dopo la decisione."""
    if not site_double_opt_in_enabled():
        return {"status": "disabled"}
    if consenso_marketing is not True or not contact_id:
        return {"status": "not_requested"}
    if consenso_marketing_at is None:
        return {"status": "no_decision_time"}
    try:
        _base()
    except Exception:  # noqa: BLE001 - senza URL pubblico la conferma non avrebbe un link
        return {"status": "not_configured"}
    return capture.request_from_public_stima(
        ctx, contact_id=contact_id, stima_id=stima_id, notice_id=None,
        decided_at=consenso_marketing_at, on_email_pending=enqueue_confirmation)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_STILE = """
body{margin:0;background:#f4f6fb;color:#1d2433;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.5rem;margin:0 0 8px}h2{font-size:1.1rem;margin:24px 0 8px}
.muted{color:#5b6475;font-size:.95rem}.avviso{background:#fff7e0;border:1px solid #f0d58a;border-radius:10px;padding:12px 14px}
.ok{background:#e8f6ec;border:1px solid #a9d9b6;border-radius:10px;padding:12px 14px}
.errore{background:#fdecea;border:1px solid #f5c2bd;border-radius:10px;padding:12px 14px}
.informativa{background:#fff;border:1px solid #d9deea;border-radius:10px;padding:14px;max-height:50vh;overflow:auto;
white-space:pre-wrap;overflow-wrap:anywhere;font-size:.95rem}
fieldset{border:1px solid #d9deea;border-radius:10px;background:#fff;padding:8px 14px;margin:16px 0}
legend{font-weight:600;padding:0 6px}
.canale{border-bottom:1px solid #eef1f6;padding:12px 0}.canale:last-child{border-bottom:0}
.canale strong{display:block}.stato{font-size:.9rem;color:#5b6475}
label.scelta{display:flex;gap:10px;align-items:flex-start;margin-top:6px;cursor:pointer}
label.scelta input{width:22px;height:22px;flex:0 0 auto;margin-top:2px}
button{background:#1f4fd1;color:#fff;border:0;border-radius:10px;padding:14px 18px;font-size:1rem;width:100%;cursor:pointer}
"""


def _documento(titolo: str, corpo: str) -> str:
    return ("<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex,nofollow\">"
            f"<title>{escape(titolo)}</title><style>{_STILE}</style></head>"
            f"<body><main>{corpo}</main></body></html>")


def neutral_page() -> str:
    return _documento("Preferenze di contatto", f"<h1>Preferenze di contatto</h1><p class=\"errore\">{escape(NEUTRAL)}</p>")


def limited_page() -> str:
    return _documento("Preferenze di contatto", "<h1>Preferenze di contatto</h1>"
                      "<p class=\"errore\">Troppe richieste in poco tempo. Riprova più tardi.</p>")


def _data(valore) -> str:
    if not isinstance(valore, datetime):
        return ""
    return valore.astimezone(timezone.utc).strftime("%d/%m/%Y")


def _riga_canale(canale: str, v: dict[str, Any], *, raccoglie: bool, email: str | None,
                 telefono: str | None) -> str:
    nome = escape(CANALI[canale])
    if v["active"]:
        stato = f"Attivo dal {escape(_data(v['granted_at']))}"
        scelta = (f"<label class=\"scelta\"><input type=\"checkbox\" name=\"revoke_{canale}\" value=\"1\"> "
                  "Revoco questo consenso</label>")
    elif v["pending_confirmation"]:
        stato = (f"In attesa di conferma: controlla la tua email entro il "
                 f"{escape(_data(v['pending_confirmation']['expires_at']))}")
        scelta = (f"<label class=\"scelta\"><input type=\"checkbox\" name=\"revoke_{canale}\" value=\"1\"> "
                  "Annullo la richiesta</label>")
    elif v["declared_unverified"]:
        stato = "Scelta registrata: non sarà usata finché il numero non sarà verificato"
        scelta = (f"<label class=\"scelta\"><input type=\"checkbox\" name=\"revoke_{canale}\" value=\"1\"> "
                  "Ritiro questa scelta</label>")
    elif raccoglie and v["covered"] and v["has_address"]:
        stato = "Non autorizzato"
        if canale == "email":
            nota = f"Riceverai un'email a {escape(email or '')} per confermare."
        else:
            nota = (f"Numero {escape(telefono or '')}. Nessuna comunicazione commerciale sarà inviata "
                    "su questo canale finché il numero non sarà verificato.")
        scelta = (f"<label class=\"scelta\"><input type=\"checkbox\" name=\"grant_{canale}\" value=\"1\"> "
                  f"<span>Autorizzo<br><span class=\"stato\">{nota}</span></span></label>")
    elif raccoglie and v["covered"]:
        stato = "Nessun indirizzo o numero registrato: rivolgiti all'agenzia"
        scelta = ""
    else:
        stato = "Non autorizzato"
        scelta = ""
    return (f"<div class=\"canale\" data-canale=\"{canale}\"><strong>{nome}</strong>"
            f"<span class=\"stato\">{stato}</span>{scelta}</div>")


def consent_page(stato: dict[str, Any]) -> str:
    raccoglie = stato["mode"] == "collect" and stato["notice"] is not None
    agenzia = escape(stato.get("agency_name") or "la tua agenzia")
    parti = ["<h1>Le tue preferenze di contatto</h1>",
             f"<p class=\"muted\">Link personale inviato da {agenzia}, rete STIMA360. Non inoltrarlo.</p>"]
    if stato["mode"] != "collect":
        parti.append("<p class=\"avviso\" data-solo-revoca>Questo link non raccoglie più nuovi consensi. "
                     "Puoi comunque revocare quelli già dati.</p>")
    elif stato["notice"] is None:
        parti.append("<p class=\"avviso\">In questo momento non è possibile raccogliere nuovi consensi. "
                     "Puoi comunque revocare quelli già dati.</p>")
    if stato["notice"] is not None:
        n = stato["notice"]
        parti.append(f"<h2>Informativa privacy — versione {escape(n['version'])}</h2>"
                     f"<div class=\"informativa\" data-informativa>{escape(n['content'])}</div>")
    righe = "".join(_riga_canale(c, stato["channels"][c], raccoglie=raccoglie,
                                 email=stato.get("email_masked"), telefono=stato.get("phone_masked"))
                    for c in CANALI)
    parti.append(
        "<form method=\"post\" data-consenso-form>"
        "<fieldset><legend>Come possiamo contattarti per comunicazioni commerciali</legend>"
        f"{righe}</fieldset>"
        "<p class=\"muted\">Nessuna casella è selezionata in partenza. Ogni canale è indipendente: "
        "puoi revocare in qualsiasi momento da questa pagina o dal link presente nelle nostre email.</p>"
        "<button type=\"submit\">Confermo le mie scelte</button></form>")
    return _documento("Preferenze di contatto", "".join(parti))


_ESITI = {
    "nothing": ("avviso", "Non hai selezionato nessuna casella: non è stato registrato nulla."),
    "collect_closed": ("avviso", "Questo link non raccoglie più nuovi consensi. Le revoche restano possibili."),
    "not_allowed": ("errore", "Una delle scelte non è più valida. Ricarica la pagina e riprova."),
    "invalid": ("errore", "Richiesta non valida."),
    "limited": ("errore", "Troppe richieste in poco tempo. Riprova più tardi."),
    "unavailable": ("errore", NEUTRAL),
}


def result_page(esito: dict[str, Any]) -> str:
    parti = ["<h1>Le tue preferenze di contatto</h1>"]
    if esito["status"] == "recorded":
        voci = []
        if esito.get("granted_pending_email"):
            voci.append("<li>Email: ti abbiamo inviato un messaggio. Il consenso sarà attivo solo dopo "
                        f"che avrai cliccato il link di conferma (entro {capture.CONFIRM_TTL.days} giorni).</li>")
        for c in esito.get("declared_phone") or ():
            voci.append(f"<li>{escape(CANALI[c])}: scelta registrata. Non riceverai comunicazioni "
                        "commerciali su questo canale finché il numero non sarà verificato.</li>")
        for c in esito.get("revoked") or ():
            voci.append(f"<li>{escape(CANALI[c])}: revocato da adesso.</li>")
        parti.append(f"<div class=\"ok\" data-esito=\"recorded\"><p>Le tue scelte sono state registrate.</p>"
                     f"<ul>{''.join(voci)}</ul></div>")
    else:
        classe, testo = _ESITI.get(esito["status"], ("errore", NEUTRAL))
        parti.append(f"<p class=\"{classe}\" data-esito=\"{escape(esito['status'])}\">{escape(testo)}</p>")
    return _documento("Preferenze di contatto", "".join(parti))


def confirm_page(invio: dict[str, Any] | None, *, now: datetime) -> str:
    if invio is None or invio["email_status"] != "pending" or invio["confirm_expires_at"] <= now:
        if invio is not None and invio["email_status"] == "confirmed":
            return _documento("Conferma email", "<h1>Conferma email</h1>"
                              "<p class=\"ok\" data-esito=\"already_confirmed\">Il tuo indirizzo è già confermato.</p>")
        return _documento("Conferma email", f"<h1>Conferma email</h1><p class=\"errore\">{escape(NEUTRAL)}</p>")
    return _documento(
        "Conferma email",
        "<h1>Conferma il tuo indirizzo email</h1>"
        f"<p>Indirizzo: <strong>{escape(capture.mask_email(invio['email_address']) or '')}</strong></p>"
        "<p class=\"muted\">Confermando, autorizzi STIMA360 a inviarti via email analisi di mercato e "
        "aggiornamenti. Puoi revocare in qualsiasi momento dal link presente in ogni email.</p>"
        "<form method=\"post\" data-conferma-form><button type=\"submit\">Confermo il mio indirizzo email</button></form>")


_CONFERME = {
    "confirmed": ("ok", "Grazie: il tuo indirizzo email è confermato."),
    "already_confirmed": ("ok", "Il tuo indirizzo è già confermato."),
    "expired": ("errore", "Il link di conferma è scaduto. Puoi chiederne uno nuovo all'agenzia."),
    "cancelled": ("errore", "Questa richiesta non è più valida."),
    "limited": ("errore", "Troppe richieste in poco tempo. Riprova più tardi."),
}


def confirm_result_page(esito: dict[str, Any]) -> str:
    classe, testo = _CONFERME.get(esito["status"], ("errore", NEUTRAL))
    return _documento("Conferma email", f"<h1>Conferma email</h1>"
                      f"<p class=\"{classe}\" data-esito=\"{escape(esito['status'])}\">{escape(testo)}</p>")


def notice_page(informativa: dict[str, Any] | None) -> str:
    if informativa is None:
        return _documento("Informativa privacy", "<h1>Informativa privacy</h1>"
                          "<p class=\"errore\">Nessuna informativa disponibile in questo momento.</p>")
    canali = ", ".join(CANALI[c] for c in CANALI if c in (informativa.get("channels") or ()))
    return _documento(
        "Informativa privacy",
        f"<h1>Informativa privacy — versione {escape(informativa['version'])}</h1>"
        f"<p class=\"muted\">Canali descritti: {escape(canali)}</p>"
        f"<div class=\"informativa\">{escape(informativa['content'])}</div>")
