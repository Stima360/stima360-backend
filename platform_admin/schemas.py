"""Il contratto di risposta della superficie Platform.

Una proiezione esplicita, campo per campo, come `operator_auth.schemas`: una
colonna nuova su `operator_users` o su `operator_sessions` non deve poter
raggiungere un client per il solo fatto di esistere.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PlatformMeResponse(BaseModel):
    """Chi e' il chiamante sulla superficie Platform.

    Quattro campi, e nessuno di essi appartiene a un'agenzia diversa da quella
    del chiamante:

    * `user_id`            - la sua identita'.
    * `is_platform_admin`  - sempre True qui: se fosse False la dipendenza
                             avrebbe gia' risposto 403. Presente lo stesso,
                             perche' un client che lo legge non deve dedurlo
                             dallo status code.
    * `agency_id`          - la SUA membership, se ne ha una (D4 la permette),
                             None se non ne ha. E' lo stesso valore che
                             `/api/operator-auth/me` gli restituisce gia': non
                             e' un dato di tenant e non allarga nulla.
    * `session_expires_at` - la scadenza della sessione.

    Deliberatamente assenti: il nome dell'agenzia, l'email, il ruolo, il numero
    di agenzie della rete, qualunque conteggio. P27-1 non consegna una
    dashboard: consegna la prova che la superficie esiste ed e' chiusa.
    """

    user_id: int | None
    is_platform_admin: bool
    agency_id: int | None
    session_expires_at: datetime
