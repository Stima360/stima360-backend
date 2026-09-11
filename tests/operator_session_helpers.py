"""Helper condivisi per autenticare un test con una sessione operatore.

PERCHE' ESISTONO

Fino a P26-4 un test si autenticava scrivendo `auth=("giorgio", "test-secret")`.
Quella riga diceva tutto: quale credenziale, e implicitamente quale agenzia (la
Default, sempre) e quale ruolo (`agency_owner`, sempre, perche' era l'unico che
la credenziale condivisa sapesse impersonare).

P26-5 toglie quel canale. Un test adesso deve dire **chi** sta chiamando, e i
tre pezzi che prima erano impliciti diventano espliciti: identita', ruolo,
agenzia. E' piu' lungo da scrivere, ed e' il punto: su una piattaforma
multi-agenzia "l'amministratore" non e' piu' una risposta sufficiente.

COSA QUESTI HELPER NON FANNO

Non scavalcano l'autenticazione e non scavalcano lo scope. Non c'e' nessun
`dependency_overrides` qui dentro, nessuna fixture `autouse`, e nessun modo di
ottenere una risposta 200 senza che il codice di produzione abbia risolto una
sessione vera. Quello che sostituiscono e' UN SOLO punto: la lettura della riga
in `operator_sessions`, che senza PostgreSQL non e' eseguibile.

Tutto il resto - `optional_session`, la costruzione dello `OperatorContext`, le
dipendenze di scope, i predicati di ruolo, le query - e' il codice vero. Un test
che passa qui passa perche' la catena di autenticazione ha detto di si', non
perche' qualcuno l'ha aggirata.

COME SI USA

    def test_qualcosa(monkeypatch):
        client = TestClient(app)
        with operator_session(monkeypatch, client, agency_id=1, role="agency_owner"):
            assert client.get("/api/core/contacts").status_code == 200

oppure, per un controllo piu' fine sulle transizioni:

    sessions = SessionDouble(monkeypatch)
    client.cookies.set(COOKIE_NAME, "token")
    sessions.login(agency_id=7, role="agent")
    ...
    sessions.revoke()          # da qui in poi ogni richiesta e' 401
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from operator_auth.context import OperatorContext
from operator_auth.enums import COOKIE_NAME

# Il token e' opaco per costruzione: il server ne conserva solo l'hash, e nulla
# in un test deve dipendere dal suo valore. Una costante leggibile rende
# evidente che non e' una credenziale ma un segnaposto.
TEST_TOKEN = "test-operator-session-token"

# L'agenzia Default, quella che la vecchia credenziale condivisa risolveva
# sempre. Molti test la assumono senza dirlo, ed e' comodo che abbia un nome.
DEFAULT_AGENCY_ID = 1


def operator_context(
    *,
    agency_id: int | None = DEFAULT_AGENCY_ID,
    role: str | None = "agency_owner",
    user_id: int | None = 1,
    is_platform_admin: bool = False,
    session_id: int | None = 1,
) -> OperatorContext:
    """Lo scope che una sessione viva produrrebbe.

    I valori di default riproducono cio' che il canale Basic dava
    implicitamente - Default Agency, `agency_owner` - cosi' un test migrato che
    non ha opinioni sull'identita' continua a provare la stessa cosa di prima.
    Un test che invece HA un'opinione la scrive.
    """
    return OperatorContext(
        user_id=user_id,
        agency_id=agency_id,
        role=role,
        is_platform_admin=is_platform_admin,
        session_id=session_id,
        auth_channel="operator_session",
    )


def session_row(context: OperatorContext, *, agency_name: str = "Agenzia di prova") -> dict:
    """La mappa che `service.session_from_token` restituisce.

    Deliberatamente questa forma e non un `AuthenticatedSession` gia' pronto:
    il punto di sostituzione e' il piu' profondo possibile, cosi' che
    `optional_session` - che e' codice di produzione - faccia comunque il
    proprio lavoro di trasformazione.
    """
    return {
        "context": context,
        "agency_name": agency_name,
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


class SessionDouble:
    """Sostituisce la sola lettura di `operator_sessions`.

    `service.session_from_token` e' l'unico punto in cui l'autenticazione tocca
    il database. Sostituendo quello - e nient'altro - restano vere tutte le
    domande che contano: il cookie e' stato inviato? la sessione risolve? che
    agenzia porta? il ruolo basta per questa superficie?

    Senza cookie risponde None comunque, perche' `optional_session` non arriva
    nemmeno a chiamarla: e' cosi' che un test puo' ancora provare il 401.
    """

    def __init__(self, monkeypatch) -> None:
        self._session: dict | None = None
        monkeypatch.setattr(
            "operator_auth.dependencies.service.session_from_token",
            lambda raw_token: self._session,
        )

    def login(self, **kwargs) -> OperatorContext:
        """Rende viva una sessione con l'identita' descritta dai parametri."""
        context = operator_context(**kwargs)
        self._session = session_row(context)
        return context

    def revoke(self) -> None:
        """Da qui in poi il cookie non risolve piu'. Il cookie resta inviato:
        e' esattamente il caso "sessione revocata", che deve dare 401 e non
        ricadere su nessun altro canale."""
        self._session = None

    @property
    def context(self) -> OperatorContext | None:
        return self._session["context"] if self._session else None


@contextmanager
def operator_session(monkeypatch, client, **kwargs):
    """Un client autenticato come l'operatore descritto, per la durata del blocco.

    Imposta il cookie sul client e rende viva la sessione. All'uscita il cookie
    viene rimosso, cosi' un test che continua dopo il blocco torna anonimo
    invece di restare autenticato per inerzia.
    """
    sessions = SessionDouble(monkeypatch)
    sessions.login(**kwargs)
    client.cookies.set(COOKIE_NAME, TEST_TOKEN)
    try:
        yield sessions
    finally:
        client.cookies.delete(COOKIE_NAME)


def authenticate(monkeypatch, client, **kwargs) -> SessionDouble:
    """Come `operator_session`, ma senza blocco: per i test che autenticano una
    volta all'inizio e restano autenticati fino alla fine."""
    sessions = SessionDouble(monkeypatch)
    sessions.login(**kwargs)
    client.cookies.set(COOKIE_NAME, TEST_TOKEN)
    return sessions
