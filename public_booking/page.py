"""P30 - la PAGINA pubblica del link di prenotazione: `/prenota/{token}`.

Non e' una rotta API e non e' un sistema nuovo: e' un mount `StaticFiles`
come tutte le altre pagine servite da questo backend (`/owner`, `/os`, i
pannelli legacy), con due sole differenze, entrambe dichiarate qui.

1. QUALUNQUE token ben formato riceve lo STESSO file `index.html`, byte per
   byte: la pagina e' statica, non contiene il token e non lo valida. E' il
   JavaScript della pagina a chiamare l'API A30-12 (`/api/public/booking/...`),
   che resta l'unica autorita' su validita', agenzia, agente e disponibilita'.
   Il server della pagina quindi non tocca il database e non puo' rivelare
   nulla (nessuna enumerazione: token valido, scaduto, revocato o inventato
   producono la stessa risposta).
2. Ogni risposta porta intestazioni di privacy: `no-store` (la pagina e i suoi
   file non finiscono in cache), `no-referrer` (il token nella URL non esce mai
   come Referer), `noindex`, e una Content-Security-Policy che ammette solo
   risorse della stessa origine: niente script, font o analytics di terzi.

Tutto il resto (`/prenota`, `/prenota/`, percorsi con punti o sotto-cartelle
diverse da `assets/`) e' 404, con le stesse intestazioni.
"""
from __future__ import annotations

import re
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .log_redaction import install_access_log_redaction

# P30-HARDENING C: `/prenota/<token>` e' nell'access log di uvicorn come ogni
# path; stesso filtro (idempotente) di `public_router.py`.
install_access_log_redaction()

#: Il percorso pubblico della pagina. La Booking Links UI costruisce l'URL
#: cliente con lo stesso valore (`PUBLIC_BOOKING_PATH` in agenda-model.js).
PAGE_PREFIX = "/prenota"

PAGE_DIR = Path(__file__).resolve().parents[1] / "static" / "public_booking"

#: La forma di un token (`secrets.token_urlsafe`: alfabeto URL-safe). Solo la
#: FORMA: se il token esiste lo decide l'API, mai questo file.
TOKEN_SEGMENT = re.compile(r"[A-Za-z0-9_-]{16,200}")

#: Intestazioni di ogni risposta della pagina (HTML, asset e 404).
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; font-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


class PublicBookingPage(StaticFiles):
    """`StaticFiles` su `static/public_booking/`: `assets/...` come file,
    `<token>` come `index.html`, tutto il resto 404."""

    def __init__(self, directory: str | Path = PAGE_DIR) -> None:
        super().__init__(directory=str(directory), html=False)

    async def get_response(self, path: str, scope: Scope) -> Response:
        segmenti = path.replace("\\", "/").split("/")
        try:
            if len(segmenti) >= 2 and segmenti[0] == "assets":
                risposta = await super().get_response(path, scope)
            elif len(segmenti) == 1 and TOKEN_SEGMENT.fullmatch(segmenti[0]):
                risposta = await super().get_response("index.html", scope)
            else:
                risposta = _non_trovata()
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            risposta = _non_trovata()
        risposta.headers.update(PAGE_HEADERS)
        return risposta


def _non_trovata() -> Response:
    return PlainTextResponse("Pagina non trovata", status_code=404)
