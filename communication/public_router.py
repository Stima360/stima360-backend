"""P29-3B.0 - la rotta PUBBLICA di disiscrizione.

Un router separato da `communication/router.py`, che e' montato dietro
`require_authenticated_operator`: qui non c'e' un operatore, c'e' l'interessato
con un link. GET mostra la pagina con un pulsante; POST esegue. Non si esegue
sulla GET perche' i client di posta e gli antispam PREFETCHANO i link, e una
disiscrizione avvenuta senza che nessuno l'abbia voluta e' peggio di un click
in piu'.

Entrambe le risposte sono la stessa pagina neutra, qualunque sia il token.
"""
from __future__ import annotations

from html import escape

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import consent_page, unsubscribe

router = APIRouter(prefix="/api/public/communication", tags=["public-communication"])


def _pagina(testo: str, *, token: str | None = None) -> HTMLResponse:
    azione = ""
    if token is not None:
        azione = (
            f'<form method="post" action="{unsubscribe.PUBLIC_PATH}">'
            f'<input type="hidden" name="t" value="{escape(token, quote=True)}">'
            '<button type="submit">Conferma disiscrizione</button></form>'
        )
    corpo = (
        "<!doctype html><html lang=\"it\"><head><meta charset=\"utf-8\">"
        "<title>Comunicazioni commerciali</title></head><body>"
        f"<p>{escape(testo)}</p>{azione}</body></html>"
    )
    return HTMLResponse(corpo, headers={"Cache-Control": "no-store"})


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(t: str = Query(default="", max_length=400)) -> HTMLResponse:
    """La pagina di conferma. Non scrive niente: un prefetch non disiscrive.

    108: la domanda dice l'AMBITO del link (questa agenzia, o tutta la rete
    STIMA360), letto dal token firmato. Un token che non torna mostra la
    domanda generica: la risposta resta neutra comunque.
    """
    esito = unsubscribe.verify_scoped(t)
    domanda = (unsubscribe.SCOPE_QUESTIONS[esito["scope"]] if esito
               else "Vuoi smettere di ricevere comunicazioni commerciali?")
    return _pagina(domanda, token=t)


@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_submit(t: str = Form(default="", max_length=400),
                       t_query: str = Query(default="", max_length=400, alias="t")) -> HTMLResponse:
    """Esegue. La risposta e' identica per token valido, ripetuto o alterato.

    FASE A: il token arriva dal form (il pulsante della pagina) oppure dalla
    query (RFC 8058, one-click: il client di posta fa POST all'URL di
    `List-Unsubscribe`, con il token firmato gia' dentro, e un corpo che non
    contiene `t`). Il form vince se c'e'. La GET resta senza effetti.
    """
    unsubscribe.process(t or t_query)
    return _pagina(unsubscribe.NEUTRAL_MESSAGE)


# ---------------------------------------------------------------------------
# C3 - la pagina personale del consenso e il double opt-in dell'email
#
# Una GET mostra e basta; ogni scelta e ogni conferma partono da un POST. Un
# token sconosciuto, alterato o scaduto riceve la stessa pagina neutra (404),
# senza dire se il link e' esistito. Nessuna rotta qui modifica un dato del
# contatto che non sia il consenso.
# ---------------------------------------------------------------------------

def _html(corpo: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(corpo, status_code=status_code, headers=consent_page.SECURITY_HEADERS)


def _cliente(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/consent/informativa", response_class=HTMLResponse)
def consent_notice_page() -> HTMLResponse:
    """L'informativa marketing corrente, leggibile da chiunque (anche dal sito)."""
    from consent import capture
    from consent.database import consent_cursor
    with consent_cursor() as (_, cur):
        informativa = capture.current_notice(cur, _adesso())
    return _html(consent_page.notice_page(informativa), 200 if informativa else 404)


@router.get("/consent/informativa.json")
def consent_notice_json() -> JSONResponse:
    """Per la casella email di stima360.it: quale informativa citare. Nessun
    dato personale: id, versione, canali coperti, indirizzo della pagina."""
    from consent import capture
    from consent.database import consent_cursor
    with consent_cursor() as (_, cur):
        informativa = capture.current_notice(cur, _adesso())
    if informativa is None:
        corpo = {"available": False}
    else:
        corpo = {"available": True, "id": informativa["id"], "version": informativa["version"],
                 "channels": list(informativa.get("channels") or []),
                 "url": f"{consent_page.PUBLIC_PREFIX}/consent/informativa"}
    return JSONResponse(corpo, headers={"Cache-Control": "no-store"})


@router.get("/consent/confirm/{token}", response_class=HTMLResponse)
def consent_confirm_page(token: str) -> HTMLResponse:
    """La pagina con il pulsante di conferma. NON conferma: un prefetch non
    deve poter concedere niente."""
    from consent import capture
    invio = capture.resolve_confirmation(token)
    corpo = consent_page.confirm_page(invio, now=_adesso())
    return _html(corpo, 200 if invio is not None else 404)


@router.post("/consent/confirm/{token}", response_class=HTMLResponse)
def consent_confirm_submit(token: str, request: Request) -> HTMLResponse:
    from consent import capture
    ip, ua = _cliente(request)
    esito = capture.confirm_email(token, ip=ip, user_agent=ua)
    codici = {"confirmed": 200, "already_confirmed": 200, "limited": 429, "unknown": 404}
    return _html(consent_page.confirm_result_page(esito), codici.get(esito["status"], 409))


@router.get("/consent/{token}", response_class=HTMLResponse)
def consent_page_view(token: str) -> HTMLResponse:
    from consent import capture
    risolto = capture.resolve_link(token)
    if risolto is None:
        return _html(consent_page.neutral_page(), 404)
    stato = capture.page_state(risolto)
    if stato is None:
        return _html(consent_page.neutral_page(), 404)
    if stato.get("limited"):
        return _html(consent_page.limited_page(), 429)
    return _html(consent_page.consent_page(stato))


@router.post("/consent/{token}", response_class=HTMLResponse)
async def consent_page_submit(token: str, request: Request) -> HTMLResponse:
    from consent import capture
    risolto = capture.resolve_link(token)
    if risolto is None:
        return _html(consent_page.neutral_page(), 404)
    modulo = await request.form()
    concedi = [c for c in consent_page.CANALI if modulo.get(f"grant_{c}") == "1"]
    revoca = [c for c in consent_page.CANALI if modulo.get(f"revoke_{c}") == "1"]
    ip, ua = _cliente(request)
    esito = capture.submit(risolto, grant=concedi, revoke=revoca, ip=ip, user_agent=ua,
                           on_email_pending=consent_page.enqueue_confirmation)
    codici = {"recorded": 200, "nothing": 200, "limited": 429, "unavailable": 404}
    return _html(consent_page.result_page(esito), codici.get(esito["status"], 409))
