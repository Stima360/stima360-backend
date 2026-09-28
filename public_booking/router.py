"""A30-12 - compatibilita': il router pubblico vive in `public_router.py`
(stessa convenzione di nome di `communication/public_router.py`, cosi' il
glob `*/router.py` del test P26-5 sulla lista dei prefissi tenant non lo
intercetta - questo pacchetto non ha un router operatore proprio: la CRUD
dei link vive dentro `appointments/router.py`, sotto il mount gia'
autenticato dell'Agenda).

Questo file NON dichiara un `APIRouter(prefix=...)` di suo: e' un
ri-export, non un secondo router.
"""
from __future__ import annotations

from .public_router import router  # noqa: F401
