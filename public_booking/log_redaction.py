"""P30-HARDENING C - il token del link di prenotazione NON finisce negli
access log dell'applicazione.

CHI SCRIVE IL LOG (verificato sul codice di uvicorn, non supposto): il
protocollo HTTP di uvicorn (`h11_impl`/`httptools_impl`), al momento della
risposta, chiama

    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', client, method, <path?query>, http_version, status)

con il path preso da `scope["path"]` - lo STESSO che il router usa per
instradare. Un middleware FastAPI/Starlette non puo' quindi oscurarlo senza
riscrivere lo scope (prima del routing: la rotta si rompe; dopo: fragile e
fuori contratto ASGI). Il punto giusto e' un `logging.Filter` sul logger
`uvicorn.access`: riscrive SOLO il terzo argomento del record prima che
qualunque handler/formatter (quello di default di uvicorn o uno di
`--log-config`) lo formatti.

Cosa si oscura: il segmento subito dopo `/api/public/booking/` e dopo
`/prenota/` (tranne `/prenota/assets/...`, i file statici della pagina),
sostituito da `[REDACTED]`. Niente hash (nemmeno troncato), niente
lunghezza, niente prefisso: nulla che aiuti a ricostruirlo. Il resto del
path (`/slots`, `/submit`) e la query restano leggibili. Gli altri path non
sono toccati. La URL del client e il routing non cambiano.

Cosa NON si puo' oscurare da qui: i log HTTP della piattaforma (es. il
request log di Render sui workspace Pro), scritti fuori dal processo.
"""
from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

#: Il logger che uvicorn usa per l'access log (h11 e httptools).
ACCESS_LOGGER = "uvicorn.access"

_TOKEN_NEL_PATH = re.compile(r"^(/api/public/booking/|/prenota/)(?!assets(?:/|$))[^/?#]+")


def redact_path(path: str) -> str:
    """Il path con il segmento del token sostituito; invariato altrimenti."""
    return _TOKEN_NEL_PATH.sub(lambda m: m.group(1) + REDACTED, path, count=1)


class AccessLogTokenRedaction(logging.Filter):
    """Riscrive il path (terzo argomento) dei record di `uvicorn.access`.
    Non scarta mai un record."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            oscurato = redact_path(args[2])
            if oscurato != args[2]:
                record.args = args[:2] + (oscurato,) + args[3:]
        return True


def install_access_log_redaction(logger_name: str = ACCESS_LOGGER) -> None:
    """Idempotente: un solo filtro per logger, anche con import ripetuti."""
    logger = logging.getLogger(logger_name)
    if not any(isinstance(f, AccessLogTokenRedaction) for f in logger.filters):
        logger.addFilter(AccessLogTokenRedaction())
