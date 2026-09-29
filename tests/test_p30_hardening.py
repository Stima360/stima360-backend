"""P30-HARDENING C - il token del link NON finisce nell'access log dell'app.

Chi scrive il log e' uvicorn (`logging.getLogger("uvicorn.access")`, path da
`scope["path"]`): vedi `public_booking/log_redaction.py`. Qui si prova:

  * la funzione di oscuramento sui path reali (L, M) e su quelli da NON
    toccare (N);
  * il record costruito ESATTAMENTE come lo costruisce uvicorn, formattato
    dal VERO `uvicorn.logging.AccessFormatter`;
  * un VERO processo uvicorn avviato da riga di comando (`python -m uvicorn`,
    logging configurato da uvicorn PRIMA di importare l'app, come in
    produzione), con richieste HTTP vere: nell'output non c'e' il token;
  * mutazione: senza filtro il token ricompare (la prova se ne accorge).

L'API con il database (201/404/409 veri) e i log applicativi sono in
`tests/test_p30_hardening_postgres.py`.

Limite dichiarato: il request log della PIATTAFORMA (es. Render, workspace
Pro) e' scritto fuori dal processo e non e' oscurabile da qui.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

TOKEN = "RAWtokenDiProva_" + "q" * 27            # forma di secrets.token_urlsafe(32)


@pytest.mark.parametrize("path,atteso", [
    # L - API pubblica: il segmento del token, qualunque sia il resto
    (f"/api/public/booking/{TOKEN}", "/api/public/booking/[REDACTED]"),
    (f"/api/public/booking/{TOKEN}/slots?from=2026-10-05T06%3A00%3A00.000Z&to=x",
     "/api/public/booking/[REDACTED]/slots?from=2026-10-05T06%3A00%3A00.000Z&to=x"),
    (f"/api/public/booking/{TOKEN}/submit", "/api/public/booking/[REDACTED]/submit"),
    ("/api/public/booking/x", "/api/public/booking/[REDACTED]"),       # anche se malformato
    # M - pagina
    (f"/prenota/{TOKEN}", "/prenota/[REDACTED]"),
    (f"/prenota/{TOKEN}/", "/prenota/[REDACTED]/"),
    (f"/prenota/{TOKEN}?utm=1", "/prenota/[REDACTED]?utm=1"),
    # N - tutto il resto invariato
    ("/prenota/assets/booking-page.js", "/prenota/assets/booking-page.js"),
    ("/prenota/assets", "/prenota/assets"),
    ("/prenota/", "/prenota/"),
    ("/prenota", "/prenota"),
    ("/api/public/booking/", "/api/public/booking/"),
    ("/api/appointments/booking-links/12/rotate", "/api/appointments/booking-links/12/rotate"),
    ("/api/public/communication/unsubscribe?t=abc", "/api/public/communication/unsubscribe?t=abc"),
    ("/os/", "/os/"),
    (f"/altro/prenota/{TOKEN}", f"/altro/prenota/{TOKEN}"),
])
def test_redact_path(path, atteso):
    from public_booking.log_redaction import redact_path
    assert redact_path(path) == atteso


def _record_come_uvicorn(path: str) -> logging.LogRecord:
    """Gli stessi argomenti di `h11_impl`/`httptools_impl`:
    '%s - "%s %s HTTP/%s" %d' % (client, method, path, http_version, status)."""
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0,
                             '%s - "%s %s HTTP/%s" %d',
                             ("127.0.0.1:5555", "GET", path, "1.1", 200), None)


def test_il_filtro_e_installato_sul_logger_vero_una_sola_volta():
    import public_booking.page  # noqa: F401
    import public_booking.public_router  # noqa: F401
    from public_booking.log_redaction import AccessLogTokenRedaction, install_access_log_redaction
    install_access_log_redaction()
    filtri = [f for f in logging.getLogger("uvicorn.access").filters
              if isinstance(f, AccessLogTokenRedaction)]
    assert len(filtri) == 1


@pytest.mark.parametrize("formatter", ["default", "custom"])
def test_formatter_vero_di_uvicorn_non_vede_il_token(formatter):
    """Il filtro agisce sul record PRIMA di qualunque formatter: quello di
    default di uvicorn e uno qualunque di `--log-config` (qui un formato
    semplice su %(message)s)."""
    from uvicorn.logging import AccessFormatter

    from public_booking.log_redaction import AccessLogTokenRedaction
    fmt = (AccessFormatter('%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                           use_colors=False)
           if formatter == "default" else logging.Formatter("%(message)s"))
    for path in (f"/api/public/booking/{TOKEN}/submit", f"/prenota/{TOKEN}"):
        record = _record_come_uvicorn(path)
        assert AccessLogTokenRedaction().filter(record) is True      # mai scartato
        riga = fmt.format(record)
        assert TOKEN not in riga and "[REDACTED]" in riga, riga
    senza = fmt.format(_record_come_uvicorn(f"/prenota/{TOKEN}"))
    assert TOKEN in senza                        # mutazione: senza filtro il token c'e'


def _porta_libera() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


APP_DI_PROVA = """
from fastapi import FastAPI
from public_booking.page import PAGE_PREFIX, PublicBookingPage
app = FastAPI()
app.mount(PAGE_PREFIX, PublicBookingPage(), name="public-booking-page")

@app.get("/altro/{qualcosa}")
def altro(qualcosa: str):
    return {"ok": True}
"""


def _uvicorn_vero(tmp_path: Path, *, disattiva_filtro: bool = False) -> str:
    """Un processo `python -m uvicorn` VERO (config di logging di default di
    uvicorn, applicata prima dell'import dell'app): richieste HTTP vere,
    restituisce tutto cio' che il processo ha scritto."""
    modulo = tmp_path / "p30_log_app.py"
    testo = APP_DI_PROVA
    if disattiva_filtro:
        testo += ("\nimport logging\nlogging.getLogger('uvicorn.access').filters.clear()\n")
    modulo.write_text(testo, encoding="utf-8")
    porta = _porta_libera()
    env = {**os.environ, "PYTHONPATH": f"{tmp_path}{os.pathsep}{ROOT}"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "p30_log_app:app", "--host", "127.0.0.1",
                             "--port", str(porta), "--no-use-colors"],
                            cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{porta}"
    try:
        limite = time.time() + 20
        while True:
            try:
                urllib.request.urlopen(f"{base}/altro/pronto").read()
                break
            except (urllib.error.URLError, ConnectionError):
                if time.time() > limite:
                    raise RuntimeError("uvicorn non e' partito")
                time.sleep(0.1)
        for percorso in (f"/prenota/{TOKEN}", f"/prenota/{TOKEN}/", "/prenota/assets/booking.css",
                         f"/prenota/{TOKEN}/altro", "/altro/visibile-123"):
            try:
                urllib.request.urlopen(base + percorso).read()
            except urllib.error.HTTPError:
                pass
        time.sleep(0.3)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=20)
    return out.decode("utf-8", "replace")


def test_L_M_N_processo_uvicorn_vero_access_log_oscurato(tmp_path):
    log = _uvicorn_vero(tmp_path)
    righe = [r for r in log.splitlines() if '"GET ' in r]
    assert len(righe) >= 6, log                                  # l'access log e' attivo
    assert TOKEN not in log, log                                 # M: mai il token
    assert '"GET /prenota/[REDACTED] HTTP/1.1" 200' in log
    assert '"GET /prenota/[REDACTED]/ HTTP/1.1" 200' in log
    assert '"GET /prenota/[REDACTED]/altro HTTP/1.1" 404' in log
    assert '"GET /prenota/assets/booking.css HTTP/1.1" 200' in log   # N: asset invariato
    assert '"GET /altro/visibile-123 HTTP/1.1" 200' in log            # N: altri path invariati


def test_mutazione_senza_filtro_il_token_compare_nel_log_vero(tmp_path):
    log = _uvicorn_vero(tmp_path, disattiva_filtro=True)
    assert TOKEN in log                                          # la prova sopra lo vedrebbe
