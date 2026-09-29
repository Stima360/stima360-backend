"""P30 - la dichiarazione delle righe che la pagina pubblica di prenotazione
aggiunge a `main.py` (collisione dichiarata e autorizzata, stessa regola di
LMC-15, P29-3B, mount A30, A30-7, A30-9B e A30-12).

`tests/lmc15_main_diff.righe_impreviste_in_main` ammette in `main.py` SOLO
righe che una fase ha dichiarato per nome, per intero: questo file e' la
dichiarazione di P30. Nessuna riga vuota, nessun frammento, nessuna regex.
"""
from __future__ import annotations

#: Le 6 righe di P30 in `main.py`: l'import della pagina, il commento che la
#: spiega e il mount statico `/prenota` (vedi `public_booking/page.py`).
RIGHE_MAIN = frozenset({
    "from public_booking.page import PAGE_PREFIX as PUBLIC_BOOKING_PAGE_PREFIX, PublicBookingPage",
    "# P30 - la pagina pubblica del link di prenotazione A30-12 (`/prenota/{token}`).",
    "# Un mount statico come `/owner`, non una rotta API: la pagina e' la stessa per",
    "# qualunque token e parla solo con `/api/public/booking/...` (vedi",
    "# `public_booking/page.py`).",
    'app.mount(PUBLIC_BOOKING_PAGE_PREFIX, PublicBookingPage(), name="public-booking-page")',
})
