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

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse

from . import unsubscribe

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
    """La pagina di conferma. Non scrive niente: un prefetch non disiscrive."""
    return _pagina("Vuoi smettere di ricevere comunicazioni commerciali?", token=t)


@router.post("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_submit(t: str = Form(default="", max_length=400)) -> HTMLResponse:
    """Esegue. La risposta e' identica per token valido, ripetuto o alterato."""
    unsubscribe.process(t)
    return _pagina(unsubscribe.NEUTRAL_MESSAGE)
