"""A30-10B (decisione D2) - il TERZO auth_channel di OperatorContext, e l'unico
punto che lo puo' produrre.

Nessuna sessione finta, nessun `SystemAgencyContext` (quello esprime "questa
agenzia" e nulla di piu': qui serve un vero attore, per l'audit di
`appointment_events`), nessun terzo tipo di contesto: solo il modello
esistente, con un `auth_channel` che nessuna route HTTP puo' produrre.

Chi chiama questa factory (il worker inbound di `calendar_sync`) ha GIA'
derivato `agency_id`/`user_id` dalla propria mappa (`calendar_connections`,
scoped per costruzione a UNA agenzia e UN operatore): questa funzione non
riceve mai quei valori da una richiesta, un payload o un header. Il suo unico
compito e' RILEGGERE dal database, al momento dell'uso (non fidarsi di una
lettura precedente), che l'operatore, l'agenzia e la membership sono ancora
validi - esattamente come l'uso di una connessione Google si rilegge ad ogni
chiamata (`calendar_sync.repository`, colonna `usable`).

`is_platform_admin` e' SEMPRE `False` qui, anche se l'operatore lo e' altrove:
l'inbound Calendar e' sempre agency-bound (D2), non un varco per la superficie
cross-agenzia di un platform admin. `session_id` e' sempre `None`: non esiste
una sessione HTTP dietro questa mutazione.

Nessuna route, nessuna dependency FastAPI, nessun `require_operator` /
`optional_session` importa o richiama questo modulo: la sentinella statica
(A30-10, `tests/test_a30_10_calendar_inbound_static.py`) lo dimostra scansendo
il codice sorgente.
"""
from __future__ import annotations

from .context import OperatorContext
from .enums import AGENCY_STATUS_ACTIVE

#: Il valore, e nessun altro modulo lo scrive in un OperatorContext.
AUTH_CHANNEL_CALENDAR_INBOUND = "calendar_inbound"

_OPERATOR_ACTIVE = "active"
_MEMBERSHIP_ACTIVE = "active"


class CalendarInboundContextUnavailable(Exception):
    """L'operatore, l'agenzia o la membership non sono (piu') validi: nessun
    OperatorContext viene costruito. Il chiamante non forza mai la mutazione
    in questo caso (D1): la riga resta cosi' com'e', il CRM vince."""


def calendar_inbound_context(cur, *, agency_id: int, user_id: int) -> OperatorContext:
    """L'OperatorContext per UNA mutazione CRM originata da Google, per
    l'operatore titolare della connessione (D1: l'actor e'
    `calendar_connections.user_id`).

    Rilegge, in quest'ordine, dentro la transazione del chiamante (nessuna
    connessione propria: usa il cursore che riceve):

      1. `operator_users.status = 'active'`;
      2. `agencies.status = 'active'`;
      3. `agency_memberships.status = 'active'` per QUESTA coppia
         (agency_id, user_id), da cui prende il ruolo REALE (mai inventato).

    Solleva `CalendarInboundContextUnavailable` se una qualunque di queste
    non e' vera: mai un contesto "abbassato" o "di default", perche' un
    contesto sbagliato scriverebbe un `appointment_events` con un attore che
    non ha piu' titolo su quella riga.
    """
    cur.execute("SELECT status FROM operator_users WHERE id = %s", (user_id,))
    operatore = cur.fetchone()
    if operatore is None or operatore["status"] != _OPERATOR_ACTIVE:
        raise CalendarInboundContextUnavailable(
            f"operatore {user_id} non attivo per l'inbound calendar")

    cur.execute("SELECT status FROM agencies WHERE id = %s", (agency_id,))
    agenzia = cur.fetchone()
    if agenzia is None or agenzia["status"] != AGENCY_STATUS_ACTIVE:
        raise CalendarInboundContextUnavailable(
            f"agenzia {agency_id} non attiva per l'inbound calendar")

    cur.execute(
        "SELECT role FROM agency_memberships "
        " WHERE agency_id = %s AND operator_user_id = %s AND status = %s",
        (agency_id, user_id, _MEMBERSHIP_ACTIVE),
    )
    membership = cur.fetchone()
    if membership is None:
        raise CalendarInboundContextUnavailable(
            f"membership di {user_id} in agenzia {agency_id} non attiva per l'inbound calendar")

    return OperatorContext(
        user_id=user_id,
        agency_id=agency_id,
        role=membership["role"],
        is_platform_admin=False,
        session_id=None,
        auth_channel=AUTH_CHANNEL_CALENDAR_INBOUND,
    )
