"""Prove su `scripts/p26_6_live_cert.py` - la matrice ostile A/B.

PERCHE' UNA MATRICE VA VERIFICATA PIU' DI QUANTO VERIFICHI

Questo script e' l'ultima prova prima di autorizzare una seconda agenzia reale.
Il suo output e' l'evidenza su cui si chiude GATE-MA1. Un difetto qui non rompe
la produzione: dice una cosa falsa su di essa, e nessuno lo scopre - il che e'
peggio.

Ci sono due modi in cui una matrice ostile puo' mentire, e sono opposti:

1. **Puo' fallire senza motivo** e far sembrare rotto un isolamento che
   funziona - un cookie che il jar non invia, una fixture che non nasce.
2. **Puo' passare senza aver provato niente**: un dominio dimenticato, un caso
   saltato in silenzio, un confronto su un database vuoto dove "A non vede i
   dati di B" e' vero perche' non ci sono dati.

Il secondo e' quello pericoloso, ed e' quello contro cui e' scritta la maggior
parte di questo file: completezza rispetto all'inventario delle route, entrambe
le direzioni per ogni dominio, impossibilita' di ottenere PASS con uno skip,
con un caso rimosso, con un cleanup fallito o su un database sbagliato.

Nessuna prova qui apre una connessione o una socket: database e HTTP sono
sostituiti da doppi, che e' il motivo per cui lo script e' scritto con quei due
seam invece che con chiamate dirette.
"""
from __future__ import annotations

import ast
import functools
import io
import py_compile
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "p26_6_live_cert.py"

# Import diretto e non `importorskip`: se questo modulo smette di importarsi la
# risposta giusta e' un fallimento, non uno skip che farebbe sparire l'intero
# file dal conteggio senza che nessuno se ne accorga.
from scripts import p26_6_live_cert as cert  # noqa: E402


# ---------------------------------------------------------------------------
# Doppi
# ---------------------------------------------------------------------------

class FakeCursor:
    """Un pezzo di database in memoria, abbastanza vero da poter essere rovinato."""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.rowcount = 0
        self._row = None
        self._rows: list[dict] = []

    def _users(self) -> dict:
        return self.state.setdefault("users", {})

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        self.state.setdefault("sql", []).append(statement)
        upper = statement.upper()

        if self.state.get("explode_on") and self.state["explode_on"] in statement:
            raise RuntimeError("il database e' caduto")

        if upper.startswith("SELECT CURRENT_DATABASE"):
            self._row = {"name": self.state.get("database", "stima360_db_test")}
        elif "COUNT(*) AS N FROM AGENCIES" in upper:
            # Prima del ramo generico: quello imposta `_rows` e lascerebbe
            # `fetchone()` su un risultato vecchio.
            #
            # Il conteggio riflette le cancellazioni davvero eseguite: un
            # doppio che rispondesse sempre 0 renderebbe la verifica finale
            # incapace di accorgersi di una DELETE mancante.
            if "agenzie_residue" in self.state:
                self._row = {"n": self.state["agenzie_residue"]}
            else:
                vive = set(self.state.get("agenzie_vive", ()))
                ids = set(params[0]) if params else set()
                self._row = {"n": len(vive & ids)}
        elif (upper.startswith("SELECT") and "FROM AGENCIES" in upper
              and "PG_CONSTRAINT" not in upper):
            # `startswith("SELECT")` e non solo "FROM AGENCIES": senza, questo
            # ramo inghiottiva anche `DELETE FROM agencies`, che finiva per
            # restituire un elenco invece di cancellare - e il cleanup delle
            # agenzie dedicate risultava fallito per un difetto del doppio.
            self._rows = list(self.state.get("agencies", []))
        elif "COUNT(*) AS N FROM OPERATOR_USERS" in upper:
            if "leftovers" in self.state:
                self._row = {"n": self.state["leftovers"]}
            else:
                self._row = {"n": sum(1 for e in self._users().values()
                                      if e.startswith(cert.CERT_PREFIX))}
        elif "AS USERS" in upper:
            if "residue" in self.state:
                self._row = dict(self.state["residue"])
            else:
                ids = set(params[0]) if params else set()
                self._row = {"users": len([i for i in ids if i in self._users()]),
                             "memberships": 0, "sessions": 0}
        elif "AS N FROM PUBLIC." in upper:
            # Le dipendenze fuori perimetro. Prima del ramo generico "AS N":
            # quello e' il censimento incoerenze e rispondeva 0, facendo
            # sembrare che nessuno referenziasse le nostre righe.
            #
            # Due conteggi distinti, ed e' il punto: senza l'esclusione del
            # perimetro la query vede ANCHE le righe del run che si
            # referenziano fra loro - `buy_requests.contact_id` punta ai nostri
            # contatti - e il cleanup si rifiuterebbe di procedere per sempre.
            interne = self.state.get("dipendenti_interne", 0)
            estranee = self.state.get("dipendenti_estranee", 0)
            self._row = {"n": estranee if "NOT (T.ID IN" in upper
                         else interne + estranee}
        elif "AS N" in upper and any(
                t in upper for t in (" PROPERTY_STATUS_HISTORY", " BUY_REQUEST_HISTORY",
                                     " MATCH_RUNS", " OWNER_AUDIT_LOG",
                                     " MATCH_REQUIREMENT_RESULTS")):
            # Gli effetti delle API: per difetto zero, cosi' un run sano passa.
            # `effetti_residui` li fa sopravvivere, ed e' il caso in cui il
            # CASCADE non e' avvenuto e nessuno se ne accorgeva.
            # La piu' SPECIFICA per prima: la query sui risultati per criterio
            # nomina anche match_runs, e scegliere la prima corrispondenza
            # attribuirebbe il conteggio alla tabella sbagliata.
            chiave = next((t.strip().lower() for t in
                           (" MATCH_REQUIREMENT_RESULTS", " PROPERTY_STATUS_HISTORY",
                            " BUY_REQUEST_HISTORY", " OWNER_AUDIT_LOG",
                            " MATCH_RUNS") if t in upper), None)
            self._row = {"n": self.state.get("effetti_residui", {}).get(chiave, 0)}
        elif "AS N" in upper and " FROM " in upper and self.state.get("residue_rows") \
                and any(t in upper for t in (" CONTACTS", " PROPERTIES", " BUY_REQUESTS", " TASKS")) \
                and "ID IN" in upper:
            # Righe ancora presenti: e' cio' che la verifica finale deve vedere
            # quando una DELETE ha risposto 2xx senza cancellare.
            self._row = {"n": 2}
        elif "AS N" in upper:                      # il censimento incoerenze
            key = next((k for k in self.state.get("census", {}) if k in statement), None)
            self._row = {"n": self.state.get("census", {}).get(key, 0)}
        elif "FROM AGENCY_MEMBERSHIPS" in upper and "OPERATOR_USER_ID AS ID" in upper:
            # L'agency_owner reale dell'agenzia. `owners` assente = nessun
            # titolare: e' il caso in cui OWNER Admin e il portale restano
            # BLOCKED invece di essere dichiarati provati.
            owners = self.state.get("owners", {})
            agency = params[0] if params else None
            self._row = {"id": owners[agency]} if agency in owners else None
        elif upper.startswith("SELECT 1 FROM AGENCIES"):
            self._row = {"1": 1} if self.state.get("slug_collide") else None
        elif upper.startswith("INSERT INTO AGENCIES"):
            self.state["next_agency"] = self.state.get("next_agency", 500) + 1
            self.state.setdefault("agenzie_create", []).append(params)
            self.state.setdefault("agenzie_vive", []).append(self.state["next_agency"])
            self._row = {"id": self.state["next_agency"]}
            self.rowcount = 1
        elif "FROM PG_CONSTRAINT" in upper and "AGENCIES" in upper:
            self._rows = list(self.state.get("fk_agencies", []))
        elif "FROM PG_CONSTRAINT" in upper:
            # Le FK verso una tabella del perimetro. `fk_perimetro` mappa
            # tabella -> [(figlio, colonna)]: e' cosi' che un test fa comparire
            # una dipendenza che il censimento non aveva visto.
            genitore = params[0] if params else None
            self._rows = [{"figlio": f, "colonna": c}
                          for f, c in self.state.get("fk_perimetro", {}).get(genitore, [])]
        elif upper.startswith("INSERT INTO OPERATOR_SESSIONS"):
            self.state["next_session"] = self.state.get("next_session", 5000) + 1
            self.state.setdefault("owner_sessions", []).append(params)
            self._row = {"id": self.state["next_session"]}
            self.rowcount = 1
        elif "FROM TASKS" in upper and "AGENCY_ID" in upper and upper.startswith("SELECT ID"):
            # L'agenzia reale delle attivita' selezionate. `task_owner` mappa
            # id -> agenzia; ogni id non elencato e' attribuito all'agenzia 1,
            # cosi' un test puo' iniettare una riga estranea senza elencarle tutte.
            # Per difetto un'attivita' appartiene all'agenzia che l'ha
            # selezionata: e' il caso corretto. `task_owner` lo sovrascrive,
            # ed e' cosi' che un test inietta una riga che NON appartiene a chi
            # l'ha vista - il difetto che la disgiunzione non saprebbe cogliere.
            derivato = {int(riga["id"]): agenzia
                        for agenzia, righe in self.state.get("stale_followup", {}).items()
                        for riga in righe}
            derivato.update(self.state.get("task_owner", {}))
            ids = list(params[0]) if params else []
            self._rows = [{"id": i, "agency_id": derivato.get(i)} for i in ids]
        elif upper.startswith("SELECT ID FROM STIME"):
            stime = self.state.get("stime", {})
            agency = params[0] if params else None
            self._row = {"id": stime[agency]} if agency in stime else None
        elif upper.startswith("SELECT 1 FROM OPERATOR_USERS"):
            self._row = {"1": 1} if self.state.get("collide") else None
        elif upper.startswith("INSERT INTO OPERATOR_USERS"):
            self.state["next_id"] = self.state.get("next_id", 900) + 1
            self.state.setdefault("inserted", []).append(params)
            self._users()[self.state["next_id"]] = params[0]
            self._row = {"id": self.state["next_id"]}
            self.rowcount = 1
        elif upper.startswith("INSERT INTO AGENCY_MEMBERSHIPS"):
            self.state.setdefault("memberships", []).append(params)
            self.rowcount = 1
        elif upper.startswith("DELETE"):
            self.state.setdefault("deletes", []).append(statement)
            if "FROM AGENCIES" in upper and params:
                self.state["agenzie_vive"] = [
                    a for a in self.state.get("agenzie_vive", [])
                    if a not in set(params[0])]
            if "delete_rowcount" in self.state:
                self.rowcount = self.state["delete_rowcount"]
            elif "FROM OPERATOR_USERS" in upper and params:
                targets = [i for i in params[0] if i in self._users()]
                for identifier in targets:
                    del self._users()[identifier]
                self.rowcount = len(targets)
            else:
                self.rowcount = 1
        return None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def fake_database(**state) -> cert.Database:
    shared = dict(state)

    @contextmanager
    def factory(*, commit=False):
        shared.setdefault("commits", []).append(commit)
        yield (None, FakeCursor(shared))

    database = cert.Database(factory)
    database.state = shared          # type: ignore[attr-defined]
    return database


AGENCIES = [
    {"id": 1, "slug": cert.DEFAULT_AGENCY_SLUG, "name": "STIMA360", "status": "active"},
    {"id": 2, "slug": cert.AGENCY_B_SLUG, "name": "Agenzia B (TEST)", "status": "active"},
]


def quiet_report():
    stream = io.StringIO()
    return cert.Report(stream=stream), stream


@functools.lru_cache(maxsize=1)
def real_routes():
    """La tavola delle route dell'APP VERA, non quella che il doppio immagina.

    PERCHE' ESISTE, E COSA E' COSTATO NON AVERLA

    Il doppio HTTP rispondeva 200/201 a qualunque percorso gli si chiedesse.
    Era comodo e falso: uno script che invocava route inesistenti passava qui e
    riceveva 405 su Render. Tre dei difetti del run 22d007af7916 sono
    esattamente questo -

      GET    /api/property/properties/{id}/visits   non esiste (c'e' la POST)
      DELETE /api/core/contacts/{id}                non esiste affatto

    - e nessuno dei due poteva essere visto da un doppio permissivo. Peggio:
    il 405 veniva CONTATO COME PROVA SUPERATA, perche' 405 non e' 200.

    Da qui in avanti il doppio conosce le route vere, lette da `main.app`:
    percorso sconosciuto -> 404, metodo non previsto -> 405, come farebbe
    Starlette. Un percorso inventato nello script fa fallire la suite locale
    invece di arrivare sul TEST.

    LA FONTE E' L'OPENAPI, NON `app.routes`

    Il primo tentativo leggeva `main.app.routes` e trovava 23 route su oltre
    duecento: questa applicazione non appiattisce `include_router`, tiene i
    sotto-router in oggetti `_IncludedRouter`, e camminare quell'albero
    significherebbe dipendere da una classe interna. Il documento OpenAPI
    elenca ogni percorso con i suoi metodi, prefissi gia' risolti, ed e' la
    stessa fonte su cui poggia l'inventario di P26-5.
    """
    import main

    table = []
    for path, item in main.app.openapi()["paths"].items():
        methods = {method.upper() for method, operation in item.items()
                   if isinstance(operation, dict)}
        if not methods:
            continue
        pattern = re.compile(
            "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$")
        table.append((pattern, methods, path))
    return tuple(table)


def resolve_route(method: str, path: str):
    """(stato, percorso) come li deciderebbe Starlette: 404, 405 oppure None."""
    bare = path.split("?")[0]
    allowed = set()
    for pattern, methods, template in real_routes():
        if pattern.match(bare):
            allowed |= methods
    if not allowed:
        return 404, None
    if method.upper() not in allowed:
        return 405, None
    return None, bare


def cert_reason() -> str:
    """Il motivo che il motore MATCH restituisce quando manca un criterio."""
    return "Nessun criterio MATCH effettivo impostato"


def _prefix_of(path: str) -> str:
    """Il prefisso di dominio di un percorso, per il doppio HTTP.

    Il piu' LUNGO che corrisponde, non il primo: `/api/property-watch/...`
    comincia anche per `/api/property`, e prendere il primo lo attribuirebbe al
    dominio sbagliato.
    """
    candidates = [d.prefix for d in cert.DOMAINS if path.startswith(d.prefix)]
    return max(candidates, key=len) if candidates else path.split("?")[0]


class FakeHttp(cert.HttpProbe):
    """Un'applicazione finta che ISOLA DAVVERO.

    Tiene le risorse per agenzia e risponde 404 a chi chiede quelle di un
    altro. E' il comportamento corretto, quindi la matrice su questo doppio
    deve passare - e ogni mutazione che rompe l'isolamento del doppio deve
    farla fallire. E' cosi' che si prova che la matrice guarda davvero.
    """

    def __init__(self, base="https://test.example", broken=None, prepopulate=(),
                 stime=None, only=None):
        super().__init__(base)
        self.broken = broken or set()
        # LA SUPERFICIE DA ROMPERE.
        #
        # `Report.check` interrompe il run alla prima prova fallita - ed e'
        # giusto: una matrice che continuasse dopo una fuga accertata
        # produrrebbe pagine di risultati su un sistema gia' compromesso. Ma
        # significa che una rottura globale non arriva mai ai domini in fondo:
        # per provare che la sonda di OWNER Admin sa fallire, va rotto OWNER
        # Admin e nient'altro.
        self.only = only
        # Domini gia' popolati sul database, uno per agenzia: e' la condizione
        # di un TEST con la catena a monte gia' costruita. Serve a distinguere
        # "lista vuota, non provato" da "lista piena, provato".
        self.prepopulate = tuple(prepopulate)
        self.rows: dict[int, dict] = {}       # id -> {"agency": .., "body": ..}
        self.sessions: dict[str, int] = {}    # token -> agency_id
        self.next_id = 100
        # I tre principali del doppio. Un token che non compare in `roles` e'
        # un operatore della matrice.
        self.roles: dict[str, str] = {}       # token -> ruolo
        self.portal: dict[str, int] = {}      # cookie portale -> conto
        self.issued: dict[str, int] = {}      # token monouso -> conto
        # Le stime che "esistono gia'" sul TEST, per agenzia: hanno un watch
        # leggibile dal proprietario. `{}` riproduce un TEST senza stime, dove
        # PROPERTY_WATCH deve risultare BLOCKED e non PASS.
        self.stime = dict(stime or {})
        # Le precondizioni reali della vendita, modellate: senza un legame
        # proprietario su quell'immobile, create_sale_scoped rifiuta con 409.
        # Il doppio che non lo sapeva ha lasciato passare uno script che sul
        # TEST prendeva 409 su entrambe le agenzie.
        self.owner_links: set = set()          # (property_id, contact_id)
        # FOLLOWUP: attivita' per agenzia e regola abilitata. La scansione del
        # doppio escala SOLO le attivita' della propria agenzia - come la
        # query vera, che porta il predicato sul tenant - e restituisce gli id
        # elaborati, che e' cio' che la matrice confronta.
        self.rule_enabled = True
        # email operatore dedicato -> agency_id, popolata dal test.
        self.dedicate: dict = {}
        self.match_property: dict = {}         # match_id  -> property_id
        self.proposal_property: dict = {}      # proposal_id -> property_id

    # -- helper -----------------------------------------------------------
    def _agency_of(self, jar):
        token = self.token_in(jar) if jar else None
        return self.sessions.get(token)

    def _broken(self, kind, surface="generic"):
        """Vero se `kind` va rotto SU QUESTA superficie."""
        return kind in self.broken and (self.only is None or self.only == surface)

    def _role_of(self, jar):
        token = self.token_in(jar) if jar else None
        return self.roles.get(token, cert.CERT_ROLE)

    def _portal_account(self, jar):
        """Il conto proprietario dietro il cookie del PORTALE.

        Cookie diverso da quello dell'operatore, di proposito: un doppio che
        li confondesse lascerebbe passare una matrice che interroga il portale
        con l'identita' sbagliata.
        """
        if jar is None:
            return None
        for cookie in jar:
            if cookie.name == cert.OWNER_COOKIE_NAME and cookie.value:
                return self.portal.get(cookie.value)
        return None

    def _marker_of(self, identifier):
        """Il marcatore della riga, se ne porta uno.

        Serve a far ereditare il marcatore alle risorse derivate: un match
        proietta i titoli della richiesta e dell'immobile, un conto
        proprietario il nome del contatto. Se il doppio non lo propagasse, la
        prova "la lista di A contiene la propria risorsa" fallirebbe per un
        difetto del doppio invece che del sistema.
        """
        import json as _json

        row = self.rows.get(identifier)
        if row is None:
            return ""
        body = _json.loads(row["body"])
        return next((str(v) for v in body.values()
                     if isinstance(v, str) and v.startswith("P26-6-")), "")

    def _seed(self, agency):
        """Una riga preesistente per agenzia nei domini richiesti."""
        import json as _json

        for prefix in self.prepopulate:
            if any(r["prefix"] == prefix and r["agency"] == agency
                   for r in self.rows.values()):
                continue
            self.next_id += 1
            self.rows[self.next_id] = {
                "agency": agency, "prefix": prefix,
                "body": _json.dumps({"id": self.next_id, "pre": "esistente"}),
            }

    def _reply(self, method, path, status, body=b""):
        self.exchanges.append((f"{method} {path.split('?')[0]}", status, body))
        return cert.Response(status, {}, body)

    # -- le superfici dei principali diversi -------------------------------

    def _owner_admin(self, method, path, jar, agency, payload):
        """OWNER Admin per un titolare vero: conti, concessioni, token."""
        import json as _json

        tail = path[len("/api/owner/admin"):].split("?")[0]

        if tail == "/accounts" and method == "POST":
            contact = (payload or {}).get("contact_id")
            row = self.rows.get(contact)
            if row is None or row["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            self.next_id += 1
            # Il conto eredita il marcatore del contatto: e' cio' che la lista
            # proietta come `display_name`, quindi e' cio' che la matrice cerca.
            body = {"id": self.next_id, "status": "active",
                    "display_name": self._marker_of(contact)}
            self.rows[self.next_id] = {"agency": agency, "prefix": "/api/owner/admin",
                                       "body": _json.dumps(body)}
            return self._reply(method, path, 201, _json.dumps(body).encode())

        if tail == "/access" and method == "POST":
            account = self.rows.get((payload or {}).get("owner_account_id"))
            prop = self.rows.get((payload or {}).get("property_id"))
            if account is None or prop is None or account["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            # IL GRANT INCOERENTE: conto e immobile con radici discordi. Il
            # doppio lo rifiuta perche' e' cio' che fa il repository vero; una
            # mutazione che tolga questa riga deve far fallire la matrice.
            if prop["agency"] != account["agency"] and not self._broken("grant", "owner_admin"):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            self.next_id += 1
            self.grants = getattr(self, "grants", {})
            self.grants[self.next_id] = (payload["owner_account_id"],
                                         payload["property_id"])
            return self._reply(method, path, 201,
                               _json.dumps({"id": self.next_id}).encode())

        if tail.endswith("/tokens") and method == "POST":
            account = int(tail.split("/")[2])
            row = self.rows.get(account)
            if row is None or row["agency"] != agency:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            token = f"one-time-{account}"
            self.issued[token] = account
            return self._reply(method, path, 200,
                               _json.dumps({"token": token, "token_id": 1}).encode())

        if tail.endswith(("/disable", "/enable")) and method == "POST":
            account = int(tail.split("/")[2])
            row = self.rows.get(account)
            if row is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if row["agency"] != agency and not (
                    self._broken("isolation", "owner_admin")
                    or self._broken("owner_write", "owner_admin")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            body = _json.loads(row["body"])
            body["status"] = "disabled" if tail.endswith("/disable") else "active"
            row["body"] = _json.dumps(body)
            return self._reply(method, path, 200, row["body"].encode())

        if tail.startswith("/accounts") and method == "GET":
            if self._broken("owner_own_list", "owner_admin"):
                # Il titolare non riesce a leggere la PROPRIA lista: la prova
                # positiva deve accorgersene invece di dedurla dal silenzio.
                return self._reply(method, path, 500, b'{"detail":"errore"}')
            visible = [r for r in self.rows.values()
                       if r["prefix"] == "/api/owner/admin"
                       and (r["agency"] == agency
                            or self._broken("listing", "owner_admin")
                            or self._broken("owner_list", "owner_admin"))]
            body = _json.dumps({"items": [_json.loads(r["body"]) for r in visible]})
            return self._reply(method, path, 200, body.encode())

        return self._reply(method, path, 200, b'{"items":[]}')

    def _portal(self, method, path, jar):
        """Il portale: il proprietario vede solo cio' che gli e' concesso."""
        import json as _json

        account = self._portal_account(jar)
        if account is None:
            return self._reply(method, path, 401, b'{"detail":"Non autorizzato"}')
        granted = [prop for acc, prop in getattr(self, "grants", {}).values()
                   if acc == account]

        tail = path[len("/api/owner/portal"):].split("?")[0]
        if tail == "/properties":
            items = [_json.loads(self.rows[p]["body"]) for p in granted
                     if p in self.rows]
            if self._broken("isolation", "portal") or self._broken("portal_list", "portal"):
                items = [_json.loads(r["body"]) for r in self.rows.values()
                         if r["prefix"] == "/api/property"]
            return self._reply(method, path, 200,
                               _json.dumps({"items": items}).encode())

        found = re.search(r"/properties/(\d+)", tail)
        if found:
            identifier = int(found.group(1))
            leaky = ("portal_documents" if tail.endswith("/documents")
                     else "portal_detail")
            if identifier not in granted and not (
                    self._broken("isolation", "portal") or self._broken(leaky, "portal")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            row = self.rows.get(identifier)
            if row is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            return self._reply(method, path, 200, row["body"].encode())

        return self._reply(method, path, 200, b'{"items":[]}')

    def _property_watch(self, method, path, agency):
        """Le stime: esistono gia', e appartengono a un'agenzia."""
        found = re.search(r"/stime/(\d+)", path)
        identifier = int(found.group(1))
        owner = next((label for label, sid in self.stime.items() if sid == identifier),
                     None)
        if owner is None:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        if owner != agency:
            kind = "watch_write" if method == "POST" else "watch_read"
            if not (self._broken("isolation", "watch") or self._broken(kind, "watch")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        return self._reply(method, path, 200,
                           f'{{"stima_id":{identifier},"watch":true}}'.encode())

    def _link_contact(self, method, path, agency, property_id, contact_id):
        """Il legame proprietario: entrambe le righe devono essere del chiamante.

        Due controlli e non uno. Un isolamento che guardasse solo l'immobile
        lascerebbe agganciare il contatto di un'altra agenzia al proprio
        immobile, e da li' `property_sale_sellers` porterebbe il nome di un
        estraneo dentro una vendita.
        """
        prop = self.rows.get(property_id)
        contact = self.rows.get(contact_id)
        if prop is None or contact is None:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        estranei = prop["agency"] != agency or contact["agency"] != agency
        if estranei and not self._broken("isolation", "relation"):
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        self.owner_links.add((property_id, contact_id))
        return self._reply(method, path, 201,
                           f'{{"property_id":{property_id},"contact_id":{contact_id},'
                           f'"role":"owner"}}'.encode())

    def _create_sale(self, method, path, agency, payload):
        """La vendita, con la precondizione che il run live ha scoperto.

        `create_sale_scoped` cerca in `property_contacts` un ruolo owner/seller
        per quell'immobile, nella stessa agenzia. Senza nemmeno una riga
        solleva ConflictError -> 409, ed e' l'ultima delle cinque precondizioni.
        """
        import json as _json

        proposal_id = payload.get("proposal_id")
        proposal = self.rows.get(proposal_id)
        if proposal is None or proposal["agency"] != agency:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        property_id = self.proposal_property.get(proposal_id)
        venditori = [link for link in self.owner_links if link[0] == property_id]
        if not venditori:
            return self._reply(
                method, path, 409,
                b'{"detail":"no eligible seller/owner registered for this property"}')
        self.next_id += 1
        body = {"id": self.next_id, "notes": payload.get("notes")}
        self.rows[self.next_id] = {"agency": agency, "prefix": "/api/sales",
                                   "body": _json.dumps(body)}
        return self._reply(method, path, 201, _json.dumps(body).encode())

    def _scan_temporal(self, method, path, agency):
        """La scansione: candidate della PROPRIA agenzia, escalate, restituite.

        Il predicato e' quello di followup/repository.py, ridotto a cio' che
        le fixture della matrice esercitano: aperta, priorita' bassa, tipo
        `automated_followup`, sorgente e regola nel metadata. Con
        `broken={"scan_scope"}` la scansione ignora il tenant: e' il difetto
        che la matrice deve saper vedere.
        """
        import json as _json

        if not self.rule_enabled:
            return self._reply(method, path, 400,
                               b'{"detail":"followup rule is not enabled"}')
        items = []
        for identifier, row in self.rows.items():
            if not row.get("task"):
                continue
            body = _json.loads(row["body"])
            foreign = row["agency"] != agency
            # "scan_mutates_other": modifica la riga altrui SENZA elencarla.
            # E' il caso che solo l'istantanea puo' vedere: gli id restituiti
            # sono puliti, il danno c'e' lo stesso.
            if foreign and self._broken("scan_mutates_other", "followup"):
                body["priority"], body["status"] = "high", "in_progress"
                row["body"] = _json.dumps(body)
                continue
            if foreign and not self._broken("scan_scope", "followup"):
                continue
            # "scan_skips_own": la scansione non elabora nemmeno le proprie.
            if not foreign and self._broken("scan_skips_own", "followup"):
                continue
            eligible = (body.get("status") == "open" and body.get("priority") in ("low", "normal")
                        and body.get("task_type") == "automated_followup"
                        and (body.get("metadata") or {}).get("source") == "followup")
            if not eligible:
                continue
            body["priority"], body["status"] = "high", "in_progress"
            row["body"] = _json.dumps(body)
            items.append({"task_id": identifier, "status": "completed",
                          "idempotency_key": f"followup:time:RULE:task:{identifier}:v1"})
        # "intruso_dopo_preflight": un task ESTRANEO che ha attraversato la
        # soglia delle 24 ore mentre il run procedeva. Il preflight lo aveva
        # contato a zero - correttamente, allora non era eleggibile - e nessun
        # secondo conteggio potrebbe arrivare prima della scansione.
        #
        # La scansione lo elabora davvero: e' la riga di qualcun altro portata
        # a 'high'/'in_progress'. La matrice deve accorgersene guardando QUALI
        # id sono tornati, non quanti.
        if self._broken("intruso_dopo_preflight", "followup"):
            self.next_id += 1
            items.insert(0, {"task_id": self.next_id, "status": "completed",
                             "idempotency_key": f"followup:time:RULE:task:{self.next_id}:v1"})
        out = {"agency_id": agency, "scanned": len(items), "escalated": len(items),
               "skipped": 0, "failed": 0, "items": items}
        return self._reply(method, path, 200, _json.dumps(out).encode())

    def _calculate(self, method, path, jar, agency, payload):
        """Il calcolo del match: nasce dalla coppia, ed eredita i marcatori."""
        import json as _json

        buy = self.rows.get(payload.get("buy_request_id"))
        prop = self.rows.get(payload.get("property_id"))
        if buy is None or prop is None or buy["agency"] != agency:
            return self._reply(method, path, 404, b'{"detail":"non trovata"}')
        self.next_id += 1
        body = {"id": self.next_id,
                "buy_title": self._marker_of(payload["buy_request_id"]),
                "property_title": self._marker_of(payload["property_id"])}
        self.rows[self.next_id] = {"agency": agency, "prefix": "/api/match",
                                   "body": _json.dumps(body)}
        self.match_property[self.next_id] = payload["property_id"]
        return self._reply(method, path, 201, _json.dumps(body).encode())

    def request(self, method, path, *, jar=None, payload=None):
        import json as _json

        # PRIMA DI TUTTO: la route esiste, con questo metodo?
        # Starlette risponde 404 a un percorso sconosciuto e 405 a un metodo
        # non previsto, e lo fa PRIMA di qualunque dipendenza. Un doppio che
        # saltasse questo passo accetterebbe route inventate - ed e' cosi' che
        # tre sonde rotte sono arrivate fino al TEST.
        refusal, _ = resolve_route(method, path)
        if refusal is not None:
            return self._reply(method, path, refusal, b'{"detail":"non disponibile"}')

        if path == cert.PUBLIC:
            return self._reply(method, path, 200, b"{}")

        if path == cert.LOGIN:
            token = f"token-{payload['email']}"
            if payload["email"] not in self.logins:
                # Operatore di un'agenzia dedicata: l'agenzia si ricava dal
                # suffisso dello slug registrato alla creazione.
                self.logins[payload["email"]] = self.dedicate.get(
                    payload["email"], 0) or 0
            self.sessions[token] = self.logins[payload["email"]]
            self._seed(self.logins[payload["email"]])
            self.put_cookie(jar, token)
            return self._reply(method, path, 204)

        if path == cert.LOGOUT:
            return self._reply(method, path, 204)

        # -- IL PORTALE: principale diverso, cookie diverso -----------------
        if path == cert.PORTAL_LOGIN:
            account = self.issued.get((payload or {}).get("token"))
            if account is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            session = f"portal-{account}"
            self.portal[session] = account
            self.put_cookie(jar, session, cert.OWNER_COOKIE_NAME)
            return self._reply(method, path, 204)

        if path == cert.PORTAL_LOGOUT:
            return self._reply(method, path, 204)

        if path.startswith("/api/owner/portal"):
            return self._portal(method, path, jar)

        agency = self._agency_of(jar)
        if agency is None:
            return self._reply(method, path, 401, b'{"detail":"Non autorizzato"}')

        if path == cert.ME:
            return self._reply(method, path, 200,
                               _json.dumps({"agency_id": agency}).encode())

        if path.startswith("/api/owner/admin"):
            if (self._role_of(jar) != cert.OWNER_ADMIN_MIN_ROLE
                    and not self._broken("threshold", "owner_admin")):
                # La soglia: agency_admin non entra, e il rifiuto arriva prima
                # di ogni domanda sullo scope.
                return self._reply(method, path, 403, b'{"detail":"riservato"}')
            return self._owner_admin(method, path, jar, agency, payload)

        if path.startswith("/api/property-watch/stime/"):
            return self._property_watch(method, path, agency)

        found = re.match(r"^/api/property/properties/(\d+)/contacts$", path)
        if found and method == "POST":
            return self._link_contact(method, path, agency, int(found.group(1)),
                                      (payload or {}).get("contact_id"))

        if path == cert.FOLLOWUP_SCAN and method == "POST":
            return self._scan_temporal(method, path, agency)

        if path.startswith("/api/core/tasks") and method == "GET":
            wanted = re.search(r"contact_id=(\d+)", path)
            items = [_json.loads(r["body"]) | {"id": i}
                     for i, r in self.rows.items()
                     if r["prefix"] == "/api/core" and r.get("task")
                     and (r["agency"] == agency or self._broken("listing"))
                     and (not wanted or _json.loads(r["body"]).get("contact_id") == int(wanted.group(1)))]
            return self._reply(method, path, 200, _json.dumps({"items": items}).encode())

        if path == "/api/sales" and method == "POST":
            return self._create_sale(method, path, agency, payload or {})

        if path.startswith("/api/match/readiness"):
            if self._broken("readiness", "generic"):
                # Il motore dice perche' non puo' calcolare. Il messaggio e'
                # la parte utile: senza, il report direbbe solo "400".
                return self._reply(method, path, 200, _json.dumps({
                    "can_match": False, "ready": False, "eligible": True,
                    "buy": {"reasons": [cert_reason()]},
                    "property": {"reasons": []},
                }).encode())
            return self._reply(method, path, 200, _json.dumps({
                "can_match": True, "ready": True, "eligible": True,
                "buy": {"reasons": []}, "property": {"reasons": []},
            }).encode())

        if path == "/api/match/calculate":
            return self._calculate(method, path, jar, agency, payload or {})

        if path.startswith("/api/proposals/") and path.endswith("/transition"):
            identifier = int(path.split("/")[3])
            row = self.rows.get(identifier)
            if row is None or (row["agency"] != agency
                               and not self._broken("isolation")):
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            return self._reply(method, path, 200, row["body"].encode())

        # Creazione di una fixture.
        if method == "POST" and payload is not None:
            self.next_id += 1
            # Le righe portano il proprio prefisso: una lista restituisce solo
            # quelle del proprio dominio. Un doppio che mostrasse ogni risorsa
            # su ogni lista renderebbe non vuote anche le liste dei domini
            # derivati, e il caso "lista vuota" - quello in cui il confronto col
            # marcatore non prova nulla - non verrebbe mai esercitato.
            self.rows[self.next_id] = {"agency": agency, "prefix": _prefix_of(path),
                                       "body": _json.dumps(payload)}
            if path == "/api/proposals":
                self.proposal_property[self.next_id] = self.match_property.get(
                    payload.get("match_id"))
            if path == "/api/core/tasks":
                self.rows[self.next_id]["task"] = True
            return self._reply(method, path, 201,
                               _json.dumps({"id": self.next_id, **payload}).encode())

        # Accesso per id.
        match = re.search(r"/(\d+)(?:/|$|\?)", path)
        if match:
            identifier = int(match.group(1))
            row = self.rows.get(identifier)
            if row is None:
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            foreign = row["agency"] != agency
            if foreign and not self._broken("isolation"):
                # "status": risponde 200 su una risorsa altrui ma NON ne rivela
                # il contenuto. E' la perdita piu' sottile - un contatore, un
                # ETag, la semplice conferma che l'id esiste - e senza una
                # modalita' apposta la matrice sembrerebbe accorgersene mentre
                # in realta' se ne accorge solo il controllo sul marcatore.
                if self._broken("status") and method == "GET":
                    return self._reply(method, path, 200, b'{"ok":true}')
                # "write": rifiuta a parole e scrive comunque. E' il caso
                # peggiore, perche' il chiamante vede un 404 e il danno resta.
                if self._broken("write") and method in ("PATCH", "PUT"):
                    row["body"] = _json.dumps(payload)
                    return self._reply(method, path, 404, b'{"detail":"non trovata"}')
                return self._reply(method, path, 404, b'{"detail":"non trovata"}')
            if method in ("PATCH", "PUT", "POST") and payload is not None:
                row["body"] = _json.dumps(payload)
            if method == "DELETE":
                # "soft_delete": risponde 2xx e NON rimuove la riga. E' cio' che
                # fanno `archive_property` e `archive_request`, ed e' il difetto
                # che ha lasciato quattro righe sul TEST senza segnalarle.
                if self._broken("soft_delete"):
                    return self._reply(method, path, 200, row["body"].encode())
                self.rows.pop(identifier, None)
                return self._reply(method, path, 204)
            return self._reply(method, path, 200, row["body"].encode())

        # Liste e ricerche: solo le risorse del dominio interrogato.
        prefix = _prefix_of(path)
        visible = [r for r in self.rows.values()
                   if r["prefix"] == prefix
                   and (r["agency"] == agency or self._broken("listing"))]
        body = _json.dumps({"items": [_json.loads(r["body"]) for r in visible]}).encode()
        return self._reply(method, path, 200, body)


#: I due titolari reali del TEST simulato: un agency_owner per agenzia.
#: Senza questi, OWNER Admin e il portale restano BLOCKED - che e' il
#: comportamento corretto ma non esercita nulla.
OWNERS = {1: 7001, 2: 7002}

#: Le stime che ciascuna agenzia gia' possiede. Chiave: agency_id.
STIME = {1: 9001, 2: 9002}


def working_run(monkeypatch, database=None, http=None, owners=OWNERS,
                stime=STIME, dedicated_agencies=False, **state):
    """Esegue `run()` su doppi che isolano correttamente."""
    report, stream = quiet_report()
    database = database or fake_database(agencies=AGENCIES, owners=owners,
                                         stime=stime, **state)
    monkeypatch.setattr(cert, "_git",
                        lambda *a: "abc123" if a[0] == "rev-parse" else cert.APPROVED_BRANCH)

    probe = http or FakeHttp()
    probe.logins = getattr(probe, "logins", {})
    # Le stime del doppio HTTP sono le stesse che `derive_stime` legge dal
    # doppio del database: se divergessero, la matrice proverebbe l'isolamento
    # su righe che il database non conosce.
    if not probe.stime:
        probe.stime = dict(stime or {})

    def factory(base):
        probe.base = base.rstrip("/")
        return probe

    # Le sessioni di titolare: il doppio deve sapere che quel token porta il
    # ruolo agency_owner, altrimenti OWNER Admin risponderebbe 403 anche a chi
    # ha il diritto di entrarci e la prova sullo scope non verrebbe mai esercitata.
    # Le agenzie dedicate: il doppio deve sapere che l'operatore appena
    # creato appartiene all'agenzia appena creata.
    original_create_dedicated = cert.Certification.create_dedicated_agency

    def create_dedicated(self, label):
        agency = original_create_dedicated(self, label)
        if agency is not None:
            probe.dedicate[agency["email"]] = agency["id"]
            probe.logins[agency["email"]] = agency["id"]
        return agency

    monkeypatch.setattr(cert.Certification, "create_dedicated_agency", create_dedicated)

    original_open = cert.OwnerSessions.open

    def open_session(self, user_id):
        raw = original_open(self, user_id)
        agency = next((a for a, u in (owners or {}).items() if u == user_id), None)
        probe.sessions[raw] = agency
        probe.roles[raw] = cert.OWNER_ADMIN_MIN_ROLE
        return raw

    monkeypatch.setattr(cert.OwnerSessions, "open", open_session)

    # La guardia read-only di FOLLOWUP interroga il repository vero, che qui
    # non ha un database: `stale` e' cio' che quella lettura restituirebbe.
    stale = dict(state.pop("stale_followup", {}))
    monkeypatch.setattr(cert, "stale_followup_candidates",
                        lambda agency_id: list(stale.get(agency_id, [])))

    # Le due identita' create vanno mappate sulle agenzie del doppio HTTP.
    original_create = cert.Certification.create_operator

    def create(self, agency):
        operator = original_create(self, agency)
        probe.logins = getattr(probe, "logins", {})
        probe.logins[operator["email"]] = agency["id"]
        return operator

    monkeypatch.setattr(cert.Certification, "create_operator", create)

    # `RENDER_GIT_COMMIT` c'e' su Render e la sua assenza e' BLOCKED: qui va
    # fornito, altrimenti ogni run di prova sarebbe INCOMPLETO per una ragione
    # che non ha nulla a che vedere con l'isolamento.
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_GIT_COMMIT": "abc123",
           "RENDER_EXTERNAL_URL": "https://test.example"}
    code = cert.run(report, database, env, "abc123", http_factory=factory,
                    dedicated_agencies=dedicated_agencies)
    return code, report, database, probe, stream


# ---------------------------------------------------------------------------
# 1-3 - sintassi, import, entry point
# ---------------------------------------------------------------------------

def test_1_the_script_compiles(tmp_path):
    py_compile.compile(str(SCRIPT), cfile=str(tmp_path / "out.pyc"), doraise=True)


def test_2_the_module_imports_without_doing_anything():
    """Importarlo non deve connettersi ne' leggere l'ambiente: tutto il lavoro
    sta dietro `main()`, che e' anche cio' che rende verificabile il resto."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    calls = [
        ast.unparse(n) for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    assert len(calls) == 1 and calls[0].startswith("sys.path.insert"), calls


def test_3_the_entry_point_is_guarded_and_does_not_hard_exit():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source
    # os._exit salterebbe il finally del cleanup.
    assert "os._exit" not in source


# ---------------------------------------------------------------------------
# 4-7 - COMPLETEZZA: la matrice contro l'inventario ricavato dal codice
# ---------------------------------------------------------------------------

def _mounted_tenant_prefixes() -> set[str]:
    """I prefissi dei router tenant montati, letti da main.py e dai router.

    Derivato dal codice e non da una lista scritta a mano: un elenco compilato
    dallo stesso autore della matrice non e' una prova di completezza, e' la
    stessa dimenticanza scritta due volte.
    """
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    mounted = set(re.findall(r"app\.include_router\((\w+)", main_source))

    prefixes = set()
    for path in sorted(ROOT.glob("*/router.py")):
        module = path.parent.name
        symbol = f"{module}_router"
        text = path.read_text(encoding="utf-8")
        # Entrambi gli stili di virgoletta: `buy/router.py` e
        # `property/router.py` usano gli apici singoli, e un pattern che
        # accettasse solo le doppie li renderebbe INVISIBILI a questo
        # inventario - due router di tenant montati che non risultano montati.
        # E' esattamente il modo in cui una prova di completezza smette di
        # esserlo restando verde.
        found = re.search(r"""APIRouter\(\s*prefix=["']([^"']+)["']""", text)
        if not found:
            continue
        prefix = found.group(1)
        # `operator_auth` e' l'autenticazione stessa, non un dominio di tenant.
        if prefix == "/api/operator-auth":
            continue
        if symbol in mounted or module == "flow":
            prefixes.add(prefix)

    # OWNER: due router, prefissi dichiarati nei rispettivi moduli. Entrambi
    # sono superfici di tenant. Il portale autentica un principale diverso - un
    # proprietario, non un operatore - e per un intero giro di revisione questo
    # e' bastato a tenerlo fuori dalla matrice. Non basta: un proprietario vede
    # immobili che appartengono a un'agenzia, quindi l'isolamento fra agenzie
    # su quella superficie e' una domanda con una risposta.
    prefixes.add("/api/owner/admin")
    prefixes.add("/api/owner/portal")
    # Le route @app che toccano dati di tenant.
    if re.search(r'@app\.\w+\("/api/admin/stime"', main_source):
        prefixes.add("/api/admin")
    return prefixes


def test_4_the_matrix_covers_every_mounted_tenant_prefix():
    """LA PROVA DI COMPLETEZZA.

    Un dominio montato e non elencato in `DOMAINS` significa una superficie che
    la matrice ostile non guarda - e un PASS che non dice nulla su di essa.
    L'inventario viene ricavato dal codice, quindi aggiungere un router e
    dimenticare la matrice fa fallire questa prova invece di passare in
    silenzio.
    """
    declared = {domain.prefix for domain in cert.DOMAINS}
    mounted = _mounted_tenant_prefixes()

    missing = mounted - declared
    assert missing == set(), (
        f"domini montati e non coperti dalla matrice ostile: {sorted(missing)}"
    )

    # Nessuna esenzione: l'elenco calcolato e quello dichiarato devono
    # coincidere. L'esenzione che stava qui mascherava il difetto del pattern
    # sopra - i due router con gli apici singoli non comparivano fra i montati,
    # e la loro presenza nella matrice sembrava un'invenzione da perdonare.
    invented = declared - mounted
    assert invented == set(), (
        f"la matrice elenca prefissi che non risultano montati: {sorted(invented)}"
    )


def test_5_the_portal_is_in_the_matrix_and_cannot_leave_it_silently():
    """L'inverso di cio' che questo test diceva prima.

    Diceva: il portale e' deliberatamente assente perche' autentica
    proprietari, non operatori. Vero, e non sufficiente - un proprietario vede
    immobili che appartengono a un'agenzia, quindi "A vede solo A" e' una
    domanda con una risposta, e non porla lasciava scoperta l'unica superficie
    di P26 raggiungibile da qualcuno che non lavora in agenzia.

    Adesso c'e', con un principale suo. Questo test e' cio' che impedisce che
    torni a sparire.
    """
    declared = {domain.prefix for domain in cert.DOMAINS}
    assert "/api/owner/portal" in declared
    portal = _domain("OWNER_PORTAL")
    assert portal.principal == "owner"
    assert portal.certifier == "owner_portal"


def test_6_every_domain_declares_at_least_one_probe():
    """Un dominio elencato senza modo di interrogarlo sarebbe copertura finta:
    comparirebbe nell'inventario e non produrrebbe una sola prova.

    Le sonde possono essere dichiarative (`listing`, `detail`, ...) oppure
    stare dentro un certificatore dedicato - ma non possono mancare, e non
    possono piu' essere delegate a un altro dominio.
    """
    for domain in cert.DOMAINS:
        probes = [domain.listing, domain.search, domain.detail,
                  domain.update, domain.delete, domain.cross_links,
                  domain.certifier]
        assert any(probes), f"{domain.name} non dichiara alcuna sonda"


def test_7_the_direct_id_and_write_probes_exist_where_a_resource_can_be_made():
    """Dove il run puo' creare una fixture, deve anche provare l'ID diretto:
    creare una risorsa e non tentare mai di rubarla sarebbe il caso piu' facile
    da dimenticare e il piu' importante da avere."""
    for domain in cert.DOMAINS:
        if domain.fixture:
            assert domain.detail, f"{domain.name} crea una fixture ma non prova l'ID diretto"

    # E l'inverso: una sonda sull'ID e' forte solo se il run POSSIEDE quell'id.
    # Un dominio che la dichiarasse senza avere ne' una fixture propria, ne' una
    # catena che la produca, ne' una derivazione da cio' che esiste, ne' un
    # dominio da cui prenderla, darebbe un 404 che non distingue "isolato" da
    # "non esiste": una sonda che sembra forte ed e' vuota.
    for domain in cert.DOMAINS:
        if domain.detail:
            assert (domain.fixture or domain.chain or domain.derive
                    or domain.depends_on), (
                f"{domain.name} prova un ID diretto che questo run non possiede"
            )


def test_7b_the_upstream_requirement_is_a_chain_step_not_an_excuse():
    """"Richiede un id a monte" NON e' una prova di non applicabilita'.

    Questa e' la lezione del giro precedente. PROPOSAL richiede un `match_id` e
    SALE un `proposal_id`: entrambi veri, entrambi ricavati correttamente dagli
    schemi - e la conclusione tratta era che i due domini non fossero
    provabili. Sbagliata, perche' l'id a monte lo produce la stessa API con
    risorse che il run possiede gia'.

    Il contratto resta la fonte: se un campo a monte sparisse, la catena
    descritta in `chain` non sarebbe piu' quella giusta e questo test lo dice.
    Ma il campo obbliga a un PASSO IN PIU', non a rinunciare.
    """
    contracts = {
        # dominio -> (file dello schema, modello, campo a monte, chi lo produce)
        "PROPOSAL": ("proposal/schemas.py", "ProposalCreate", "match_id", "MATCH"),
        "SALE": ("sale/schemas.py", "SaleCreate", "proposal_id", "PROPOSAL"),
    }
    for name, (path, model, upstream, producer) in contracts.items():
        source = (ROOT / path).read_text(encoding="utf-8")
        block = source[source.index(f"class {model}"):]
        block = block[:block.index("\n\nclass ") if "\n\nclass " in block else len(block)]
        assert re.search(rf"^\s*{upstream}\s*:", block, re.M), (
            f"{model} non richiede piu' {upstream}: la catena dichiarata per "
            f"{name} non descrive piu' l'API e va ricavata di nuovo"
        )
        domain = _domain(name)
        assert domain.chain, f"{name} non dichiara come si costruisce"
        assert domain.depends_on == producer, (
            f"{name} richiede {upstream} ma non dichiara di dipendere da {producer}"
        )

    # MATCH non ha una POST di creazione - si CALCOLA - e per questo era
    # finito fra i non applicabili. Ma il calcolo e' una route come le altre, e
    # prende due risorse che il run possiede.
    match_router = (ROOT / "match" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("")' not in match_router, (
        "MATCH ha acquisito una POST di creazione diretta: la catena va rivista"
    )
    assert '@router.post("/calculate", status_code=201)' in match_router, (
        "la POST di calcolo non c'e' piu': MATCH, PROPOSAL e SALE non sono "
        "costruibili come descritto e la matrice va ripensata"
    )

    # PROPERTY_WATCH: qui la non applicabilita' della FIXTURE e' vera, e questa
    # e' la differenza. Non c'e' nessuna route che crei una stima - il funnel
    # pubblico risolve l'agenzia lato server - quindi la risorsa si deriva
    # invece di nascere. Cio' che NON e' ammesso e' saltare le prove: il
    # dominio ha un certificatore suo.
    watch_router = (ROOT / "property_watch" / "router.py").read_text(encoding="utf-8")
    creates = re.findall(r'@router\.post\(\s*["\']([^"\']*)["\']', watch_router)
    assert not any(path in ("", "/stime") for path in creates), (
        f"PROPERTY_WATCH ha acquisito una POST di creazione: {creates}"
    )
    watch = _domain("PROPERTY_WATCH")
    assert watch.fixture is None and watch.derive == "stime"
    assert watch.certifier == "property_watch"


# ---------------------------------------------------------------------------
# 8-12 - il database: un solo bersaglio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    None, "", "   ", "stima360_db", "stima360_db_prod", "stima360_db_test_2",
    "stima360_db_test_copy", "STIMA360_DB_TEST", "postgres",
])
def test_8_every_other_database_is_refused(name):
    with pytest.raises(cert.GuardFailure):
        cert.assert_certification_database(name)


def test_9_only_the_certification_database_is_accepted():
    assert cert.assert_certification_database("stima360_db_test") == "stima360_db_test"
    assert cert.assert_certification_database("  stima360_db_test  ") == "stima360_db_test"


def test_10_the_guard_runs_before_any_write(monkeypatch):
    """Non basta che la guardia esista: deve essere la prima cosa.

    `approved_commit` e' volutamente irraggiungibile: se il controllo sul
    commit venisse prima, mascherebbe la guardia sul database.
    """
    report, _ = quiet_report()
    database = fake_database(agencies=AGENCIES)
    env = {"DB_NAME": "stima360_db", "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}

    with pytest.raises(cert.GuardFailure):
        cert.preflight(report, database, env, approved_commit="irraggiungibile")

    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed and "DELETE" not in executed


def test_11_a_lying_db_name_is_caught_by_the_real_connection(monkeypatch):
    report, _ = quiet_report()
    database = fake_database(database="stima360_db", agencies=AGENCIES)
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}

    with pytest.raises(cert.CheckFailed):
        cert.preflight(report, database, env, approved_commit="abc123")

    assert "0.2" in [ident for kind, ident, _ in report.rows if kind == cert.FAIL]
    assert "INSERT" not in " ".join(database.state.get("sql", [])).upper()


def test_12_a_non_https_base_stops_the_run(monkeypatch):
    """Il cookie e' Secure: su http:// il jar non lo invia, e OGNI prova di
    isolamento leggerebbe 401 come "isolamento funzionante". E' il modo piu'
    silenzioso in cui questa matrice potrebbe mentire."""
    report, _ = quiet_report()
    monkeypatch.setattr(cert, "_git",
                        lambda *a: "abc123" if a[0] == "rev-parse" else cert.APPROVED_BRANCH)
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "http://test.example"}

    with pytest.raises(cert.CheckFailed):
        cert.preflight(report, fake_database(agencies=AGENCIES), env, "abc123")


# ---------------------------------------------------------------------------
# 13-17 - la matrice passa su un'app che isola, e fallisce su una che no
# ---------------------------------------------------------------------------

# I domini che questo run non sa costruire e la cui lista, su un TEST poco
# popolato, e' semplicemente vuota. MATCH, PROPOSAL e SALE NON sono piu' qui:
# la catena li produce.
DERIVED = ("/api/seller-intelligence", "/api/followup",
           "/api/next-best-action", "/api/flow", "/api/admin")


def test_13_an_empty_database_yields_INCOMPLETE_and_never_PASS(monkeypatch):
    """LA TRAPPOLA PIU' FACILE, E LA PROVA CHE NON CI CADE.

    "Il marcatore di B non compare nella lista di A" e' vero anche quando la
    lista di A e' vuota. Su un TEST poco popolato i domini che questo run non
    puo' creare sarebbero comparsi fra i PASS senza che una riga fosse stata
    confrontata.

    Qui l'applicazione ISOLA correttamente e non c'e' un solo FAIL. Il verdetto
    e' comunque INCOMPLETO: e' la distinzione fra "va bene" e "non lo so".
    """
    # Nessuna stima: PROPERTY_WATCH non ha righe su cui osservare l'isolamento.
    code, report, database, probe, _ = working_run(monkeypatch, stime={})

    failures = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert failures == [], failures

    assert code == 2, report.verdict
    assert not report.verdict.startswith("PASS")

    blocked = [i for k, i, _ in report.rows if k == cert.BLOCKED]
    assert any("SELLER_INTELLIGENCE" in i for i in blocked), blocked
    assert any("FLOW" in i for i in blocked), blocked
    # E soprattutto: PROPERTY_WATCH non passa per assenza di dati. E' il
    # dominio che prima veniva dichiarato "coperto" da un altro e non produceva
    # una sola riga di report.
    assert any("PROPERTY_WATCH" in i for i in blocked), blocked


def test_13b_with_everything_populated_only_the_escalation_stays_BLOCKED(monkeypatch):
    """L'altra meta' di test_13, e il punto in cui oggi si ferma la matrice.

    Con i dati a monte presenti non resta un solo FAIL e ogni dominio porta
    righe vere. Il verdetto pero' NON e' PASS, e non per un difetto: la
    scansione FOLLOWUP modifica attivita' preesistenti e nessuna separazione e'
    costruibile senza cambiare il backend, quindi quella meta' resta non
    provata - e una prova non eseguita impedisce il PASS globale.

    Questo test fissa che sia l'UNICA cosa a impedirlo. Se domani comparisse un
    secondo BLOCKED, sarebbe una regressione mascherata da una condizione nota.
    """
    code, report, database, probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})

    failures = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert failures == [], failures

    blocked = [i for k, i, _ in report.rows if k == cert.BLOCKED]
    assert blocked == ["FOLLOWUP-escalation"], (
        f"oltre all'escalation FOLLOWUP resta bloccato altro: {blocked}"
    )
    assert code == 2, report.verdict


def test_13d_the_chain_is_really_built_and_really_probed(monkeypatch):
    """I tre domini della catena non sono piu' BLOCKED: sono costruiti.

    E' la differenza fra la revisione precedente e questa. Se `build_chain`
    smettesse di funzionare, MATCH, PROPOSAL e SALE tornerebbero BLOCKED - il
    che sarebbe onesto, ma va visto, non subito in silenzio.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}

    for label in ("A", "B"):
        for step in ("MATCH", "PROPOSAL", "SALE"):
            assert rows.get(f"chain-{step}-{label}") == cert.PASS, (
                f"la catena non ha prodotto {step} per {label}"
            )
    # E ciascuno e' stato interrogato sull'ID diretto nelle due direzioni.
    for step in ("MATCH", "PROPOSAL", "SALE"):
        for direction in (f"{step}-detail-A-B", f"{step}-detail-B-A"):
            assert rows.get(direction) == cert.PASS, f"manca {direction}"
    # La catena e' completa; il verdetto resta INCOMPLETO per l'escalation
    # FOLLOWUP, che e' l'unica prova non eseguibile - vedi test_13b.
    assert code == 2, report.verdict


def test_13c_a_populated_list_is_really_observed_not_assumed(monkeypatch):
    """La riga preesistente dell'altra agenzia esiste davvero e NON compare.

    Il conteggio degli elementi osservati finisce nel messaggio del report: se
    fosse zero, il test 13 avrebbe gia' bloccato. Qui si verifica che il numero
    sia maggiore di zero, cioe' che il confronto abbia guardato qualcosa.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED))

    observed = [t for k, i, t in report.rows
                if k == cert.PASS and "MATCH-list-" in i and "non-vede" in i]
    assert observed, "nessun confronto sulla lista MATCH"

    # Il conteggio va ESTRATTO, non cercato per assenza.
    #
    # La prima versione di questa riga asseriva `"0 elementi osservati" not in
    # t`, ed era vacua: togliendo del tutto il conteggio dal messaggio la
    # stringa spariva e il controllo passava. Un'asserzione sull'assenza di un
    # testo e' soddisfatta anche da un testo che non c'e' affatto.
    counts = []
    for message in observed:
        found = re.search(r"\((\d+) elementi osservati\)", message)
        assert found, f"il report non dice quanti elementi ha guardato: {message}"
        counts.append(int(found.group(1)))
    assert counts and all(count > 0 for count in counts), counts


def test_14_the_matrix_fails_when_direct_ids_leak(monkeypatch):
    """LA PROVA CHE LA MATRICE GUARDA DAVVERO.

    Se un PASS su un'app corretta non fosse accompagnato da un FAIL su un'app
    che perde, non direbbe nulla: passerebbe anche una matrice che non
    interroga niente.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"isolation"}))

    assert code == 1
    leaked = [i for k, i, _ in report.rows if k == cert.FAIL and "detail" in i]
    assert leaked, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_15_the_matrix_fails_when_listings_leak(monkeypatch):
    """L'altra direzione della stessa perdita: non l'ID diretto, ma la lista."""
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"listing"}))

    assert code == 1
    leaked = [i for k, i, _ in report.rows if k == cert.FAIL and "list" in i]
    assert leaked, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_15b_the_matrix_fails_on_a_status_leak_that_reveals_no_content(monkeypatch):
    """La perdita piu' sottile: 200 su una risorsa altrui, senza mostrarne nulla.

    Un contatore, un ETag, o la semplice conferma che quell'id esiste. Il
    controllo sul marcatore non se ne accorge - non c'e' contenuto da
    riconoscere - quindi serve che la matrice guardi anche lo STATO. Senza
    questa prova, una mutazione che toglie il controllo sullo stato
    sopravviveva.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"status"}))

    assert code == 1
    leaked = [i for k, i, _ in report.rows
              if k == cert.FAIL and "detail" in i and "neutro" not in i]
    assert leaked, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_15c_the_matrix_fails_when_a_refused_write_is_applied_anyway(monkeypatch):
    """Rifiuta a parole e scrive comunque: il caso peggiore.

    Il chiamante vede un 404 e crede di essere stato fermato; la risorsa
    dell'altra agenzia porta il suo dato. Un controllo che si fermasse allo
    stato della risposta direbbe che tutto e' a posto, ed e' il motivo per cui
    dopo ogni scrittura ostile la matrice rilegge la risorsa con la sessione
    del proprietario.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"write"}))

    assert code == 1
    tampered = [i for k, i, _ in report.rows if k == cert.FAIL and "intatta" in i]
    assert tampered, [i for k, i, _ in report.rows if k == cert.FAIL]


def test_16_both_directions_are_probed_for_every_domain(monkeypatch):
    """A verso B e B verso A, non solo una delle due.

    Una matrice che provasse una direzione sola passerebbe su un sistema in cui
    l'isolamento e' asimmetrico - e l'asimmetria e' proprio il difetto che una
    JOIN scopata a meta' produce.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    idents = [i for _k, i, _ in report.rows]

    # Ogni dominio, non solo quelli con `detail`: la versione precedente
    # escludeva OWNER_ADMIN per nome e non guardava affatto i domini provati da
    # un certificatore dedicato, che sono esattamente quelli in cui una sola
    # direzione era piu' facile da dimenticare.
    # FOLLOWUP non ha due direzioni OSTILI: ha due osservazioni della
    # selezione, A e B, piu' il confronto fra le due. La forma e' diversa
    # perche' la superficie e' una lettura, non un tentativo di furto.
    assert "FOLLOWUP-selezione-A" in idents and "FOLLOWUP-selezione-B" in idents
    assert "FOLLOWUP-selezione-disgiunta" in idents

    for domain in cert.DOMAINS:
        if domain.name == "FOLLOWUP":
            continue
        own = [i for i in idents if i.startswith(f"{domain.name}-")]
        # Tre forme per "A verso B": l'id diretto (`...-A-B`), la lista
        # (`...-non-vede-B`) e la scansione (`...-non-elabora-B`).
        forward = [i for i in own if i.endswith(("A-B", "-non-vede-B", "-non-elabora-B"))]
        backward = [i for i in own if i.endswith(("B-A", "-non-vede-A", "-non-elabora-A"))]
        assert forward, f"{domain.name}: manca la direzione A->B fra {own}"
        assert backward, f"{domain.name}: manca la direzione B->A fra {own}"


def test_17_owner_admin_is_probed_on_the_threshold_AND_on_the_scope(monkeypatch):
    """Le due prove sono diverse e servono entrambe.

    LA SOGLIA: le identita' della matrice sono `agency_admin` e devono ricevere
    403. E' isolamento sulla dimensione del RUOLO.

    LO SCOPE: con due sessioni `agency_owner` vere, le domande tornano quelle
    di sempre. E' isolamento sulla dimensione dell'AGENZIA.

    Per un giro di revisione c'era solo la prima, e sembrava sufficiente. Non
    lo era: un 403 su entrambe le agenzie e' compatibile con qualunque cosa
    accada dietro la soglia, fuga inclusa.
    """
    _code, report, _db, _probe, _ = working_run(monkeypatch)
    idents = [i for _k, i, _ in report.rows]

    soglia = sorted(i for i in idents if i.startswith("OWNER_ADMIN-soglia-"))
    assert soglia == ["OWNER_ADMIN-soglia-A", "OWNER_ADMIN-soglia-B"]

    scope = sorted(i for i in idents if i.startswith("OWNER_ADMIN-scope-"))
    assert scope == ["OWNER_ADMIN-scope-A", "OWNER_ADMIN-scope-B"], (
        "la prova sullo scope di OWNER Admin non e' stata eseguita: resta solo "
        "quella sulla soglia, che non dice nulla sull'isolamento fra agenzie"
    )
    for direction in ("OWNER_ADMIN-non-vede-A", "OWNER_ADMIN-non-vede-B"):
        assert direction in idents, f"manca la direzione ostile {direction}"
    for direction in ("OWNER_ADMIN-write-A-B", "OWNER_ADMIN-write-B-A"):
        assert direction in idents, f"manca la scrittura ostile {direction}"


# ---------------------------------------------------------------------------
# 18-22 - il verdetto non si puo' addolcire
# ---------------------------------------------------------------------------

def test_18_a_blocked_probe_cannot_produce_pass():
    report, _ = quiet_report()
    report.note("a", "ok")
    report.blocked("b", "non eseguita")
    assert report.exit_code == 2
    assert "INCOMPLETO" in report.verdict
    assert not report.verdict.startswith("PASS")


def test_19_a_failure_outranks_everything_including_many_passes():
    report, _ = quiet_report()
    for index in range(50):
        report.note(f"x{index}", "ok")
    report.blocked("b", "non eseguita")
    report.fail("c", "perdita")
    assert report.exit_code == 1
    assert report.verdict == "FAIL"


def test_20_an_incomplete_cleanup_is_a_failure():
    report, _ = quiet_report()
    database = fake_database(residue={"users": 1, "memberships": 0, "sessions": 0},
                             delete_rowcount=0)
    certification = cert.Certification(database, report)
    certification.created_user_ids.extend([1, 2])

    certification.cleanup_database()

    assert report.exit_code == 1
    assert not report.verdict.startswith("PASS")


def test_21_a_dead_database_during_cleanup_is_a_failure():
    report, _ = quiet_report()
    database = fake_database(explode_on="DELETE FROM operator_sessions")
    certification = cert.Certification(database, report)
    certification.created_user_ids.append(1)

    certification.cleanup_database()          # non solleva

    assert report.exit_code == 1


def test_22_leftovers_stop_the_run_without_creating_or_deleting(monkeypatch):
    """Da qui non si distingue il residuo di ieri da un run in corso adesso, e
    le due chiedono risposte opposte: nel dubbio non si tocca niente."""
    other_run = {900: f"{cert.CERT_PREFIX}deadbeef-11{cert.CERT_DOMAIN}"}
    database = fake_database(agencies=AGENCIES, users=dict(other_run))

    code, report, database, _probe, _ = working_run(monkeypatch, database=database)

    assert code == 1
    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed, "ha creato identita' nonostante il residuo"
    assert "DELETE" not in executed, "ha cancellato righe che non ha creato"
    assert database.state["users"] == other_run


# ---------------------------------------------------------------------------
# 23-27 - fixture, cleanup e concorrenza
# ---------------------------------------------------------------------------

def test_23_cleanup_runs_even_after_a_failure(monkeypatch):
    code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(broken={"isolation"}))

    deletes = " ".join(database.state.get("deletes", []))
    for table in ("operator_sessions", "agency_memberships", "operator_users"):
        assert table in deletes, f"il cleanup non ha toccato {table}"
    assert code == 1


def test_24_the_cleanup_is_lexically_inside_a_finally():
    """La struttura del sorgente lo garantisce, non l'ordine delle istruzioni:
    `except Exception` copre tutto il resto, quindi la differenza si vede solo
    con un KeyboardInterrupt - ed e' un caso reale, un Ctrl-C su una richiesta
    lenta."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    run_fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    in_finally = [
        ast.unparse(stmt)
        for node in ast.walk(run_fn) if isinstance(node, ast.Try)
        for stmt in node.finalbody
    ]
    assert any("cleanup_database()" in s for s in in_finally), in_finally
    assert any("cleanup_http_fixtures" in s for s in in_finally), in_finally


def test_25_no_delete_is_ever_written_by_prefix():
    """Il criterio e' l'id. Una DELETE per prefisso porterebbe via le identita'
    di una certificazione concorrente mentre le sta usando."""
    source = SCRIPT.read_text(encoding="utf-8")
    deletes = re.findall(r"DELETE FROM [a-z_]+ WHERE [^\"]+", source)
    assert deletes, "nessuna DELETE trovata: il pattern e' cambiato"
    for statement in deletes:
        assert "IN %s" in statement, statement
        for forbidden in ("email", "LIKE", "run_id"):
            assert forbidden not in statement, f"DELETE per {forbidden}: {statement}"


def test_26_a_concurrent_run_survives_this_run_cleanup():
    """La regressione che vale piu' di tutte: due matrici in parallelo.

    Il fake cancella davvero, quindi la sopravvivenza si legge dalla tabella e
    non dal testo della query.
    """
    report, _ = quiet_report()
    database = fake_database()
    certification = cert.Certification(database, report)

    mine = [certification.create_operator(AGENCIES[0])["id"],
            certification.create_operator(AGENCIES[1])["id"]]
    concurrent = 555
    database.state["users"][concurrent] = f"{cert.CERT_PREFIX}altro-99{cert.CERT_DOMAIN}"

    certification.cleanup_database()

    assert concurrent in database.state["users"], (
        "il cleanup ha cancellato l'identita' di un run concorrente"
    )
    for identifier in mine:
        assert identifier not in database.state["users"]
    assert report.exit_code == 0


def test_27_each_run_has_its_own_identifier_and_marker():
    report, _ = quiet_report()
    database = fake_database()
    identifiers = {cert.Certification(database, report).run_id for _ in range(50)}
    assert len(identifiers) == 50

    certification = cert.Certification(database, report)
    assert certification.run_id in certification.marker("A")
    assert certification.marker("A") != certification.marker("B"), (
        "le due agenzie devono avere marcatori distinti, altrimenti 'A non vede "
        "il marcatore di B' e' vero per costruzione"
    )


def _function_source(name: str) -> str:
    """Il corpo di una funzione, delimitato dall'AST e non da `index()`.

    Un taglio testuale fra due `def` sembra equivalente e non lo e': basta
    inserire un metodo in mezzo perche' la fetta ne inghiotta uno che non
    doveva guardare. E' successo con i due cleanup nuovi, e il test che ne e'
    derivato accusava il metodo sbagliato.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"funzione {name} non trovata")


def test_28_api_deletable_fixtures_are_deleted_through_the_api():
    """Dove esiste una DELETE, si passa da li'.

    Via HTTP di proposito: la cancellazione attraversa le stesse regole di
    scope delle altre chiamate, quindi non puo' toccare nulla di estraneo. Una
    DELETE diretta sarebbe piu' comoda e aggirerebbe cio' che lo script prova.
    """
    block = _function_source("cleanup_http_fixtures")
    assert "http.request(" in block
    for forbidden in ("DELETE FROM", "cur.execute", "self.db"):
        assert forbidden not in block, f"il cleanup delle fixture usa {forbidden}"


def test_28b_the_rows_no_route_can_delete_are_removed_by_id_only():
    """Dove NON esiste una DELETE, si passa dal database - e per soli ID.

    Match, proposte, vendite e conti proprietario non hanno una route di
    cancellazione: l'API sa crearli e non sa disfarli. Ignorarli lascerebbe
    dietro il run righe che maneggiano denaro; cancellarli per marcatore o per
    prefisso porterebbe via quelle di una certificazione concorrente. Restano
    gli id, e solo quelli.
    """
    for name in ("cleanup_chain_fixtures", "cleanup_owner_fixtures"):
        block = _function_source(name)
        assert "WHERE id IN %s" in block or "_id IN %s" in block, (
            f"{name} non cancella per elenco di id"
        )
        for forbidden in ("LIKE", "CERT_EMAIL_LIKE", "marker", "run_id LIKE"):
            assert forbidden not in block, (
                f"{name} usa {forbidden} come criterio: cancellerebbe anche le "
                "righe di un run concorrente"
            )

    # E il verificatore finale conta i residui sugli stessi id, non su un
    # prefisso: un cleanup che dicesse "fatto" senza guardare non e' una prova.
    scoped = _function_source("_delete_scoped")
    assert "self.report.fail" in scoped and "INCOMPLETO" in scoped


# ---------------------------------------------------------------------------
# 29-32 - segreti, censimento, gate
# ---------------------------------------------------------------------------

def test_29_no_credential_is_hardcoded():
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    suspicious = re.compile(r"(pbkdf2_sha256\$|[A-Za-z0-9+/]{40,}={0,2}|[0-9a-f]{40,})")
    allowed = {"pbkdf2_sha256$"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value in allowed or len(value) > 400:
                continue
            assert not suspicious.search(value), f"letterale sospetto: {value[:40]!r}"
    assert "secrets.token_urlsafe" in source
    assert source.count("os.environ") == 1


def test_30_the_leak_scanner_can_actually_fail():
    report, _ = quiet_report()

    class Probe(cert.HttpProbe):
        def __init__(self):
            self.exchanges = [("GET /x", 200, b'{"eco":"super-segreto"}')]

    with pytest.raises(cert.CheckFailed):
        cert.scan_for_leaks(report, Probe(), ["super-segreto"])

    clean, _ = quiet_report()
    cert.scan_for_leaks(clean, Probe(), ["un-altro-valore"])
    assert clean.exit_code == 0


def test_31_the_incoherence_census_is_read_only_and_blocks_on_findings():
    """Conta e non ripara. Un'incoerenza trovata e non classificata deve
    impedire la chiusura del gate, non essere corretta di nascosto."""
    source = SCRIPT.read_text(encoding="utf-8")
    block = source[source.index("def incoherence_census"):source.index("def _fill")]
    assert "SELECT COUNT(*)" in block
    for forbidden in ("DELETE", "UPDATE", "INSERT"):
        assert forbidden not in block, f"il censimento esegue {forbidden}"

    report, _ = quiet_report()
    database = fake_database(census={"owner_property_access": 3})
    cert.incoherence_census(report, database)
    assert report.exit_code == 1


def test_32_the_script_never_closes_the_gate_by_itself():
    """Un esito PASS e' una condizione necessaria, non l'autorizzazione.

    Lo script non deve poter scrivere `LIVE_HOSTILE_MATRIX_PASSED`: quella
    riga la muove una persona, dopo aver letto l'output.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "LIVE_HOSTILE_MATRIX_PASSED = True" not in source
    assert "GATE-MA1 resta APERTO" in source

    gate = (ROOT / "tests" / "test_p26_6c_backend_gate_closure.py").read_text(encoding="utf-8")
    assert "LIVE_HOSTILE_MATRIX_PASSED = False" in gate


# ---------------------------------------------------------------------------
# 33-38 - COMPLETEZZA, SECONDA REVISIONE
#
# La prima versione di questo file provava che ogni prefisso montato comparisse
# in `DOMAINS`. Non bastava: un dominio puo' essere elencato e non provato.
# Quattro modi in cui e' successo davvero, tutti verdi:
#
#   * OWNER_ADMIN era "provato" da un 403. Le identita' della matrice sono
#     `agency_admin` e la soglia e' `agency_owner`, quindi il rifiuto arrivava
#     PRIMA di qualunque domanda sullo scope: dell'isolamento fra agenzie su
#     quella superficie non si sapeva nulla.
#   * OWNER Portal non era nella matrice affatto, dichiarato "non una
#     superficie da operatore" - vero, e irrilevante: e' una superficie di
#     tenant, e il principale diverso e' un motivo per provarla in modo
#     diverso, non per non provarla.
#   * PROPERTY_WATCH era "coperto" da LEGACY_ADMIN. Una copertura per etichetta:
#     due domini diversi, due route diverse, una sola prova.
#   * PROPOSAL e SALE erano "non applicabili" perche' la loro POST richiede un
#     id a monte. Ma quell'id la stessa API sa produrlo, quindi la catena si
#     costruisce e le categorie sono applicabili eccome.
#
# Le prove che seguono impediscono il ritorno di ognuno dei quattro.
# ---------------------------------------------------------------------------

def _domain(name):
    return next(d for d in cert.DOMAINS if d.name == name)


def test_33_no_domain_is_covered_by_another_domain_label():
    """Nessuna copertura delegata, in nessuna forma.

    `covered_by` permetteva a un dominio di sparire dalla matrice nominandone
    un altro. Il meccanismo non deve tornare: se una superficie e' distinta
    abbastanza da avere un prefisso suo, e' distinta abbastanza da avere prove
    sue.
    """
    assert not hasattr(cert.Domain("X", "/x", listing="/x"), "covered_by"), (
        "l'attributo covered_by e' tornato: una superficie puo' di nuovo "
        "sparire dalla matrice nominandone un'altra"
    )

    # Strutturale e non testuale: un controllo su "la parola non compare" e'
    # soddisfatto anche cancellando la spiegazione del perche' e' stata tolta,
    # ed e' proprio il commento che serve a chi legge il diff fra un anno.
    # Cio' che deve restare vietato e' l'ARGOMENTO, in una chiamata a Domain.
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    delegated = [
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Domain"
        for keyword in node.keywords
        if keyword.arg in ("covered_by", "covered", "delegates_to")
    ]
    assert delegated == [], (
        f"un dominio delega la propria copertura a un altro: {delegated}"
    )


def test_34_every_domain_is_probed_by_something_that_actually_runs():
    """Ogni dominio deve avere sonde proprie O un certificatore dedicato.

    E il certificatore dichiarato deve esistere come funzione del modulo: una
    stringa che non risolve sarebbe copertura scritta e non eseguita.
    """
    for domain in cert.DOMAINS:
        probes = [domain.listing, domain.search, domain.detail, domain.update,
                  domain.delete, domain.cross_links]
        if domain.certifier:
            function = getattr(cert, f"certify_{domain.certifier}", None)
            assert callable(function), (
                f"{domain.name} dichiara il certificatore {domain.certifier!r}, "
                "che non esiste nel modulo"
            )
            continue
        assert any(probes), f"{domain.name} non dichiara alcuna sonda"


def test_35_owner_admin_is_probed_with_a_real_agency_owner_session():
    """La prova su OWNER Admin non puo' fermarsi al 403.

    Un 403 su tutte e due le agenzie e' compatibile con qualunque cosa accada
    dietro la soglia di ruolo, isolamento incluso. Servono due sessioni
    `agency_owner` vere - una per agenzia - e allora le domande diventano
    quelle di sempre: A vede A, B vede B, A non vede B, B non vede A.
    """
    domain = _domain("OWNER_ADMIN")
    assert domain.certifier == "owner_admin"
    assert domain.principal == cert.OWNER_ADMIN_MIN_ROLE, (
        "OWNER_ADMIN va interrogato da un agency_owner, non dal ruolo della "
        "matrice: altrimenti si prova la soglia e non lo scope"
    )
    source = SCRIPT.read_text(encoding="utf-8")

    # Le sessioni sono agganciate agli owner REALI e create come sole righe in
    # operator_sessions: nessuna password, nessuna membership, nessun utente.
    block = source[source.index("class OwnerSessions"):source.index("class Certification")]
    assert "INSERT INTO operator_sessions" in block
    for forbidden in ("UPDATE operator_users", "UPDATE agency_memberships",
                      "INSERT INTO operator_users", "INSERT INTO agency_memberships",
                      "password_hash"):
        assert forbidden not in block, (
            f"OwnerSessions tocca {forbidden}: gli owner reali devono restare "
            "esattamente come sono"
        )
    # E il cleanup cancella per ID di sessione, mai per utente.
    assert "DELETE FROM operator_sessions WHERE id IN" in block
    assert "DELETE FROM operator_users" not in block


def test_36_the_owner_portal_is_in_the_matrix_with_its_own_principal():
    """Il portale e' una superficie di tenant, e va provato come tale."""
    portal = _domain("OWNER_PORTAL")
    assert portal.prefix == "/api/owner/portal"
    assert portal.certifier == "owner_portal"
    assert portal.principal == "owner"
    source = SCRIPT.read_text(encoding="utf-8")
    # Il portale autentica con un cookie DIVERSO: usarne uno solo per due
    # principali diversi sarebbe il modo piu' rapido di provare la cosa
    # sbagliata senza accorgersene.
    assert cert.OWNER_COOKIE_NAME == "stima360_owner_session"
    assert cert.OWNER_COOKIE_NAME != cert.COOKIE_NAME
    # E la prova del grant incoerente esiste: e' l'unica che dice qualcosa
    # sull'origine dei dati del portale invece che sulla loro lettura.
    assert "grant-incoerente" in source


def test_37_property_watch_has_its_own_probes():
    """Niente piu' delega a LEGACY_ADMIN."""
    watch = _domain("PROPERTY_WATCH")
    assert watch.certifier == "property_watch"
    assert watch.prefix == "/api/property-watch"
    source = SCRIPT.read_text(encoding="utf-8")
    # Le stime non si creano da questa API: la fixture si DERIVA, in sola
    # lettura, da cio' che l'agenzia gia' possiede.
    assert watch.fixture is None
    assert watch.derive is not None
    block = source[source.index("def derive_stime"):]
    block = block[:block.index("\ndef ")]
    assert "SELECT" in block
    for forbidden in ("INSERT", "UPDATE", "DELETE"):
        assert forbidden not in block, f"la derivazione esegue {forbidden}"


def test_38_the_derived_chain_is_built_not_declared_impossible():
    """MATCH, PROPOSAL e SALE si costruiscono: la catena esiste nell'API.

    "Richiede un id a monte" non e' una prova di non applicabilita' finche' lo
    stesso client puo' produrre quell'id. Questo test fissa i sei passi che la
    rendono costruibile, ricavati dai router: se uno sparisse, la catena va
    ripensata invece di tornare a dichiararla impossibile.
    """
    for name in ("MATCH", "PROPOSAL", "SALE"):
        domain = _domain(name)
        assert domain.chain, f"{name} non dichiara la catena che lo produce"
        assert domain.detail, f"{name} e' costruibile ma non prova l'ID diretto"

    match_router = (ROOT / "match" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("/calculate"' in match_router, (
        "MATCH non ha piu' la POST di calcolo: la catena non e' costruibile "
        "come descritto e va ricavata di nuovo dal router"
    )
    proposal_router = (ROOT / "proposal" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("/{proposal_id}/transition")' in proposal_router
    sale_router = (ROOT / "sale" / "router.py").read_text(encoding="utf-8")
    assert '@router.post("", status_code=201)' in sale_router


# ---------------------------------------------------------------------------
# 39-45 - LE NUOVE SONDE SANNO FALLIRE
#
# Una sonda che non sa fallire non e' una prova: e' una riga di report. Le
# sette che seguono rompono, una per volta, esattamente cio' che ciascuna nuova
# superficie deve sorvegliare, e pretendono un FAIL.
# ---------------------------------------------------------------------------

def _first_failure(monkeypatch, kind, surface):
    """Rompe UNA cosa e ritorna la prima prova che fallisce.

    UNA, e non tutte insieme: `Report.check` interrompe il run alla prima prova
    fallita - ed e' la scelta giusta, perche' una matrice che proseguisse dopo
    una fuga accertata produrrebbe pagine di risultati su un sistema gia'
    compromesso. Ma significa che una rottura globale ne esercita UNA SOLA.

    Questa e' la lezione della prima tornata di mutazioni: dieci sopravvissute
    su venti, tutte perche' i test rompevano tutto e poi si accontentavano di
    "una prova di questo dominio e' fallita". Bastava che ne fallisse una
    qualunque, e le altre potevano essere indebolite senza che nessuno se ne
    accorgesse. Adesso ogni sonda ha la sua rottura e il suo identificatore.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={kind}, only=surface, prepopulate=DERIVED, stime=STIME))
    failures = [i for k, i, _ in report.rows if k == cert.FAIL]
    return code, failures, report


@pytest.mark.parametrize("kind,surface,expected", [
    # OWNER Admin: quattro sonde, quattro rotture distinte.
    ("owner_own_list", "owner_admin", "OWNER_ADMIN-scope-A"),
    ("owner_list", "owner_admin", "OWNER_ADMIN-non-vede-B"),
    ("owner_write", "owner_admin", "OWNER_ADMIN-write-A-B"),
    ("threshold", "owner_admin", "OWNER_ADMIN-soglia-A"),
    # Il portale: lista, dettaglio, documenti, e l'origine del dato.
    ("portal_list", "portal", "OWNER_PORTAL-non-vede-B"),
    ("portal_detail", "portal", "OWNER_PORTAL-detail-A-B"),
    ("portal_documents", "portal", "OWNER_PORTAL-documenti-A-B"),
    ("grant", "owner_admin", "OWNER_PORTAL-grant-incoerente-A-B"),
    # PROPERTY_WATCH: lettura e scrittura ostili.
    ("watch_read", "watch", "PROPERTY_WATCH-ostile-A-B"),
    ("watch_write", "watch", "PROPERTY_WATCH-ostile-write-A-B"),
])
def test_39_every_new_probe_fails_when_its_own_surface_leaks(
        monkeypatch, kind, surface, expected):
    """Ogni sonda nuova, rotta da sola, deve produrre IL SUO fallimento.

    Non "un fallimento da qualche parte in quel dominio": quello e' soddisfatto
    anche da una sonda vicina, ed e' come dieci mutazioni sono sopravvissute la
    prima volta. Qui l'identificatore atteso e' esatto.
    """
    code, failures, _report = _first_failure(monkeypatch, kind, surface)
    assert expected in failures, (
        f"rompendo {kind!r} su {surface!r} la prova {expected} non ha fallito; "
        f"fallimenti osservati: {failures}"
    )
    assert code == 1


def test_40_the_hostile_write_that_is_refused_and_applied_anyway_is_caught(monkeypatch):
    """Il caso peggiore: 404 a parole, scrittura eseguita.

    Il chiamante vede un rifiuto, il danno resta, e nessuno lo scopre. La
    sonda che lo prende non e' quella sullo stato ma quella che RILEGGE la
    risorsa dell'altro subito dopo.
    """
    code, failures, _report = _first_failure(monkeypatch, "write", "generic")
    assert any(i.endswith("-intatta") for i in failures), failures
    assert code == 1


def test_41_a_chain_that_cannot_be_built_says_why(monkeypatch):
    """Un BLOCKED senza motivo e' un BLOCKED inutile.

    Quando il motore MATCH non puo' calcolare, la prontezza dice quale criterio
    manca. Se lo script saltasse quella domanda vedrebbe solo un 400, e chi
    legge il report non saprebbe cosa sistemare sul TEST.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"readiness"}, prepopulate=DERIVED, stime=STIME))
    blocked = [(i, t) for k, i, t in report.rows if k == cert.BLOCKED]
    chain = [t for i, t in blocked if i.startswith("chain-")]
    assert chain, blocked
    assert any(cert_reason() in t for t in chain), (
        f"il motivo riportato dal motore non compare nel report: {chain}"
    )
    # E i tre domini a valle restano BLOCKED, non PASS.
    assert code == 2, report.verdict
    for step in ("MATCH", "PROPOSAL", "SALE"):
        assert any(i.startswith(f"{step}-detail") for i, _ in blocked), blocked


def test_42_a_residue_left_behind_by_the_new_cleanups_is_a_failure(monkeypatch):
    """Le righe che nessuna route sa cancellare vanno via, o e' FAIL.

    Match, proposte, vendite e conti proprietario non hanno una DELETE: se il
    cleanup per id fallisse in silenzio, ogni certificazione lascerebbe dietro
    di se' righe che maneggiano denaro.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch,
        census={"FROM property_sales": 2, "FROM owner_accounts": 1},
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    failures = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-CHAIN" for i, _ in failures), failures
    assert any(i == "CLEAN-OWNER" for i, _ in failures), failures
    assert any("INCOMPLETO" in t for _, t in failures), failures


def test_43_without_a_real_owner_the_surfaces_are_BLOCKED_not_PASSED(monkeypatch):
    """Nessun agency_owner sul TEST: OWNER Admin e il portale restano BLOCKED.

    E' la risposta onesta. 027 ammette un solo titolare per agenzia e questo
    script non ne crea: se non c'e', quelle superfici non sono state provate -
    non "sono a posto".
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, owners={}, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("owner-session-A") == cert.BLOCKED
    assert rows.get("OWNER_ADMIN-scope-A") == cert.BLOCKED
    assert rows.get("OWNER_PORTAL-propria-A") == cert.BLOCKED
    assert code == 2, report.verdict
    # E la soglia resta provata: e' l'unica cosa che si puo' ancora sapere.
    assert rows.get("OWNER_ADMIN-soglia-A") == cert.PASS


def test_44_the_temporary_owner_sessions_are_removed_by_id(monkeypatch):
    """Le sessioni di titolare valgono quanto le sue credenziali.

    Devono sparire, e la cancellazione deve nominare gli id di questo run: un
    DELETE per utente porterebbe via anche la sessione con cui il titolare vero
    sta lavorando in quel momento.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    deletes = [d for d in database.state.get("deletes", [])
               if "OPERATOR_SESSIONS" in d.upper()]
    assert deletes, "nessuna cancellazione di sessioni owner"
    assert any("WHERE id IN" in d for d in deletes), deletes
    for statement in deletes:
        assert "operator_user_id IN" not in statement or "WHERE id IN" in " ".join(deletes)


def test_45_the_one_time_token_exemption_is_not_a_blanket_allow(monkeypatch):
    """L'esenzione dello scanner vale per UNA risposta, non per il token.

    `POST /accounts/{id}/tokens` puo' contenere il token: e' il suo contratto.
    Qualunque ALTRA risposta che lo contenga e' una fuga, e questo test lo
    prova mettendo lo stesso segreto in uno scambio diverso.
    """
    report, _ = quiet_report()
    probe = cert.HttpProbe("https://test.example")
    probe.exchanges = [
        ("POST /api/owner/admin/accounts/1/tokens", 200, b'{"token":"SEGRETO"}'),
        ("GET /api/owner/portal/properties", 200, b'{"items":["SEGRETO"]}'),
    ]
    # `check` interrompe il run su una prova fallita: la fuga di un token non
    # e' una riga da annotare e proseguire.
    with pytest.raises(cert.CheckFailed):
        cert.scan_for_leaks(
            report, probe, ["SEGRETO"],
            expected=(("POST /api/owner/admin/accounts/1/tokens", "SEGRETO"),),
        )
    assert report.exit_code == 1, "una fuga fuori dallo scambio esentato e' passata"

    # E con la sola risposta esentata, nessuna fuga.
    report2, _ = quiet_report()
    probe2 = cert.HttpProbe("https://test.example")
    probe2.exchanges = [probe.exchanges[0]]
    cert.scan_for_leaks(
        report2, probe2, ["SEGRETO"],
        expected=(("POST /api/owner/admin/accounts/1/tokens", "SEGRETO"),),
    )
    assert report2.exit_code == 0


# ---------------------------------------------------------------------------
# 46-49 - LE REGRESSIONI DEL RUN 22d007af7916
#
# Tre sonde dello script invocavano route che l'applicazione non ha. Nessuna
# poteva essere vista da un doppio che rispondeva 200 a qualunque percorso: e'
# per questo che il difetto e' arrivato fino al TEST, dove il 405 e' stato
# perfino contato come prova superata (405 non e' 200).
# ---------------------------------------------------------------------------

def test_46_every_declared_probe_hits_a_route_that_exists():
    """LA REGRESSIONE PRINCIPALE: nessun percorso inventato.

    Ogni sonda dichiarata nei domini viene risolta contro la tavola delle route
    vere. Un percorso inesistente, o un metodo che quella route non accetta,
    fa fallire qui - dove costa un secondo - invece che sul TEST.
    """
    problemi = []
    for domain in cert.DOMAINS:
        sonde = [("GET", domain.listing), ("GET", domain.search),
                 ("GET", domain.detail)]
        if domain.fixture:
            sonde.append(("POST", domain.fixture[0]))
        if domain.update:
            sonde.append((domain.update[0], domain.update[1]))
        sonde += [("GET", c) for c in domain.cross_links]
        # La DELETE di cleanup si registra SOLO dove c'e' una fixture.
        if domain.fixture and domain.detail and domain.api_delete:
            sonde.append(("DELETE", domain.detail))
        for method, template in sonde:
            if not template:
                continue
            path = (template.replace("{id}", "1").replace("{marker}", "x")
                    .replace("{core_id}", "1"))
            status, _ = resolve_route(method, path)
            if status:
                problemi.append(f"{domain.name}: {status} {method} {path}")
    assert problemi == [], "sonde verso route inesistenti: " + "; ".join(problemi)


def _delete_handler(module: str, path_suffix: str):
    """Il gestore della route DELETE, e i nomi che invoca. Dal sorgente."""
    tree = ast.parse((ROOT / module / "router.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            attr = getattr(deco.func, "attr", None)
            args = [a.value for a in deco.args if isinstance(a, ast.Constant)]
            if attr == "delete" and any(a == path_suffix for a in args):
                chiamate = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
                            for c in ast.walk(node) if isinstance(c, ast.Call)}
                return node.name, {c for c in chiamate if c}
    return None, set()


def test_47_api_delete_means_physical_removal_not_a_2xx():
    """LA REGRESSIONE DEL RESIDUO SILENZIOSO.

    `api_delete` non significa "la route DELETE esiste": significa che RIMUOVE
    FISICAMENTE la riga. Due route di questa API rispondono 200 e archiviano -
    `archive_property`, `archive_request` - e il run 22d007af7916 ha creduto al
    codice di stato, lasciando quattro righe sul TEST senza segnalarle.

    Il giudizio e' ricavato dal router: se il gestore della DELETE, o una
    funzione che invoca, si chiama `archive*`, la cancellazione e' logica e
    `api_delete` deve essere False. Se un domani diventasse fisica, questo test
    fallisce e la dichiarazione va rivista - invece di restare pessimista per
    sempre.
    """
    rotte = {  # dominio -> (modulo, percorso dichiarato nel router)
        "CORE": ("core", "/contacts/{contact_id}"),
        "PROPERTY": ("property", "/properties/{property_id}"),
        "BUY": ("buy", "/requests/{request_id}"),
    }
    for name, (module, suffix) in rotte.items():
        domain = _domain(name)
        handler, chiamate = _delete_handler(module, suffix)
        archivia = handler is not None and any(
            "archive" in n for n in {handler} | chiamate)
        fisica = handler is not None and not archivia
        assert domain.api_delete == fisica, (
            f"{name}.api_delete={domain.api_delete} ma la route DELETE "
            f"{'non esiste' if handler is None else ('archivia (' + handler + ')') if archivia else 'cancella davvero'}"
        )

    # E cio' che non si cancella via API ha una tabella e un marcatore per il
    # cleanup SQL: senza, la riga resterebbe e nessuno lo direbbe.
    for domain in cert.DOMAINS:
        if domain.fixture and not domain.api_delete:
            assert domain.table and domain.marker_column, (
                f"{domain.name} non e' cancellabile via API e non dichiara "
                "tabella/colonna per il cleanup SQL"
            )

    # E la cancellazione incrocia TERNE (id, marcatore, agenzia) sulla stessa
    # riga: tre elenchi indipendenti lascerebbero passare la riga di A con il
    # marcatore di B.
    blocco = _function_source("cleanup_orphan_fixtures")
    assert "t.id = f.id" in blocco and "t.agency_id = f.agency_id" in blocco
    assert "f.marcatore" in blocco, blocco[-400:]
    for vietato in ("LIKE", "marker_column}"):
        assert vietato not in blocco


def test_47b_a_2xx_delete_that_leaves_the_row_is_caught_on_the_database(monkeypatch):
    """LA REGRESSIONE DEL RESIDUO SILENZIOSO.

    Il doppio risponde 2xx alla DELETE e TIENE la riga: e' esattamente cio' che
    fanno `archive_property` e `archive_request`. Nel run 22d007af7916 questo
    e' passato inosservato perche' il cleanup si fidava del codice di stato.

    Qui la rete di sicurezza SQL rimuove comunque la riga, e la verifica finale
    lo conferma leggendo il database - non un 2xx. Se anche quella rete si
    rompesse, il residuo verrebbe dichiarato: e' il caso sotto.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch,
        http=FakeHttp(broken={"soft_delete"}, prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("CLEAN-VERIFICA") == cert.PASS, [r for r in report.rows if "CLEAN" in r[1]]

    # E la verifica ha guardato QUALCOSA. Un run che non tracciasse nulla
    # passerebbe in silenzio - ed e' il modo esatto in cui il difetto e'
    # sfuggito dal vivo: nessuna riga dichiarata, nessun residuo dichiarato.
    testo = next(t for k, i, t in report.rows if i == "CLEAN-VERIFICA")
    osservate = int(re.search(r"(\d+) righe create", testo).group(1))
    assert osservate >= 6, (
        f"la verifica ha considerato solo {osservate} righe: contatti, immobili "
        "e richieste di entrambe le agenzie devono esserci tutti"
    )
    # Le righe sopravvissute alla DELETE HTTP sono state rimosse via SQL, per
    # terne id+marcatore+agenzia: immobili e richieste, che sono le due
    # superfici le cui route DELETE archiviano soltanto.
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    assert "FROM PROPERTIES" in cancellazioni and "FROM BUY_REQUESTS" in cancellazioni, (
        database.state.get("deletes", []))


def test_47c_a_residue_on_the_database_is_a_failure_whatever_http_said(monkeypatch):
    """E la verifica finale sa fallire.

    Il database dichiara righe ancora presenti: il verdetto e' FAIL anche se
    ogni DELETE ha risposto 2xx. Senza questo caso, `verify_no_residue`
    potrebbe essere una nota che dice sempre di si'.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, residue_rows=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-VERIFICA" and "ANCORA PRESENTI" in t
               for i, t in fallimenti), fallimenti


def test_48_the_sale_precondition_is_satisfied_before_the_chain(monkeypatch):
    """SALE dava 409: mancava il legame proprietario.

    `create_sale_scoped` esige una riga owner/seller in `property_contacts`.
    Il doppio adesso la esige davvero, quindi se `link_property_owner` sparisse
    la catena si fermerebbe sulla vendita - come sul TEST.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    for label in ("A", "B"):
        assert rows.get(f"PROPERTY-relazione-{label}") == cert.PASS
        assert rows.get(f"chain-SALE-{label}") == cert.PASS, (
            "la vendita non e' stata creata: la precondizione del venditore "
            "non e' soddisfatta"
        )


def test_48b_without_the_owner_link_the_sale_is_refused_with_409(monkeypatch):
    """E la precondizione e' reale, non decorativa: senza legame, 409."""
    import scripts.p26_6_live_cert as module

    monkeypatch.setattr(module, "link_property_owner",
                        lambda *a, **k: None)
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    testi = [t for k, i, t in report.rows
             if k == cert.BLOCKED and i.startswith("chain-")]
    assert any("409" in t for t in testi), (
        f"senza il legame proprietario la vendita doveva fallire con 409: {testi}"
    )


# ---------------------------------------------------------------------------
# 49-52 - FOLLOWUP: la scansione, provata e non dichiarata
#
# L'assenza di GET rende non provabili le letture, non il dominio. La route che
# c'e' - POST /scan-temporal - e' una scrittura scopata, e una scrittura si
# prova cosi': ognuno la lancia, e si osserva che tocchi solo le proprie righe.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 49-53 - FOLLOWUP: si osserva la selezione, non si esegue l'escalation
#
# Tre difese sono state costruite e scartate prima di arrivare qui, e la
# ragione e' sempre la stessa: `scan-temporal` seleziona E scrive, e il
# predicato non ammette una restrizione agli id di questo run.
#
#   * contare le candidate prima: dice quante ce ne sono ADESSO. Il predicato
#     e' `due_at <= NOW() - INTERVAL '24 hours'`, quindi una riga preesistente
#     diventa eleggibile da sola, col passare del tempo;
#   * un secondo conteggio: insegue lo stesso istante che non puo' fermare;
#   * fixture con scadenza remotissima e limite 1: un inserimento concorrente
#     con scadenza ancora piu' vecchia la scavalca, e accorgersene dopo non e'
#     isolamento - la riga altrui e' gia' stata modificata.
#
# Resta provabile la SELEZIONE, dove l'isolamento fra agenzie vive per intero,
# ed e' una lettura pura.
# ---------------------------------------------------------------------------

def test_49_the_matrix_never_runs_the_followup_scan(monkeypatch):
    """LA GARANZIA: nessuna scrittura su FOLLOWUP, in nessuna direzione.

    Non "la scrittura e' circoscritta": non avviene. E' l'unica separazione
    costruibile senza cambiare il backend per far passare una prova.
    """
    _code, _report, database, probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))

    scansioni = [label for label, _s, _b in probe.exchanges if "scan-temporal" in label]
    assert scansioni == [], f"la matrice ha lanciato la scansione: {scansioni}"
    creazioni = [label for label, _s, _b in probe.exchanges
                 if label == "POST /api/core/tasks"]
    assert creazioni == [], f"la matrice crea fixture FOLLOWUP: {creazioni}"
    assert not any("FOLLOWUP_ACTIONS" in d.upper()
                   for d in database.state.get("deletes", []))


def test_50_the_selection_is_observed_in_both_agencies(monkeypatch):
    """La selezione e' una SELECT: si esegue davvero, sui dati reali."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-selezione-A") == cert.PASS
    assert rows.get("FOLLOWUP-selezione-B") == cert.PASS
    assert rows.get("FOLLOWUP-selezione-disgiunta") == cert.PASS


def test_51_a_task_selected_by_both_agencies_is_a_failure(monkeypatch):
    """Se la stessa attivita' comparisse in entrambe le selezioni, il predicato
    di tenant non starebbe facendo il suo lavoro."""
    # L'attivita' 99 e' vista da entrambe ed e' davvero di A: l'appartenenza
    # per A passa, quella per B fallirebbe per prima e nasconderebbe la
    # disgiunzione. `task_owner` la attribuisce a entrambe le richiedenti
    # tramite un'agenzia che coincide con quella interrogata, cosi' il primo
    # controllo passa e resta in piedi solo il difetto da osservare.
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}, {"id": 99}], 2: [{"id": 99}]},
        task_owner={99: None})
    righe = {i: (k, t) for k, i, t in report.rows}
    kind, testo = righe.get("FOLLOWUP-selezione-disgiunta", (None, ""))
    assert kind == cert.FAIL and "99" in testo, righe.get("FOLLOWUP-selezione-disgiunta")


def test_52_two_empty_selections_prove_nothing_and_are_BLOCKED(monkeypatch):
    """La regola di tutta la matrice vale anche qui: su insiemi vuoti la
    disgiunzione e' vera per costruzione e non dimostra nulla."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-selezione-disgiunta") == cert.BLOCKED


def test_53_the_escalation_is_BLOCKED_with_a_reason_taken_from_the_signature():
    """L'altra meta' della route resta NON PROVATA, e il motivo e' verificabile.

    Non e' un'opinione: la firma della selezione accetta solo `agency_id`,
    `limit` e `rule_code`. Se un domani accettasse un elenco di id, la
    separazione diventerebbe costruibile e questo test fallisce - cosi' che il
    BLOCKED non sopravviva alla ragione che lo giustifica.
    """
    import inspect

    from followup import repository

    firma = inspect.signature(
        repository.list_temporal_escalation_candidates_for_agency)
    assert set(firma.parameters) == {"agency_id", "limit", "rule_code"}, (
        f"la firma e' cambiata ({list(firma.parameters)}): la scansione "
        "potrebbe essere restringibile agli id del run e l'escalation va "
        "riportata nella matrice"
    )

    blocco = _function_source("certify_followup")
    assert "report.blocked(" in blocco and "FOLLOWUP-escalation" in blocco
    assert "FOLLOWUP_SCAN" not in blocco, "il certificatore lancia ancora la scansione"

    # E la selezione passa dalla funzione applicativa vera, non da una copia
    # della sua SQL ne' da un elenco vuoto: nei test quella funzione e'
    # sostituita da un doppio, quindi la sua implementazione non viene mai
    # esercitata e solo un controllo sul codice puo' dire che c'e' davvero.
    lettura = _function_source("stale_followup_candidates")
    albero = ast.parse(lettura)
    invocate = [n for n in ast.walk(albero) if isinstance(n, ast.Call)]
    nomi = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
            for c in invocate}
    assert "list_temporal_escalation_candidates_for_agency" in nomi, (
        f"la selezione non chiama il predicato applicativo: {sorted(n for n in nomi if n)}"
    )
    ritorni = [n for n in ast.walk(albero) if isinstance(n, ast.Return)]
    assert len(ritorni) == 1 and isinstance(ritorni[0].value, ast.Call), (
        "la selezione ha un'uscita che non passa dal predicato: una scorciatoia "
        "qui renderebbe la disgiunzione vera per costruzione"
    )


def test_53b_a_task_belonging_to_another_agency_is_caught(monkeypatch):
    """LA PROVA CHE LA DISGIUNZIONE NON DA'.

    Due selezioni possono essere disgiunte e sbagliate entrambe: basta che la
    selezione di A restituisca attivita' di un'agenzia terza e quella di B di
    un'altra ancora. Nessun id in comune, e nessuna delle due appartiene a chi
    l'ha chiesta.

    Qui l'attivita' 11 e' selezionata da A ma appartiene all'agenzia 99. La
    disgiunzione resta vera; l'appartenenza no.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]},
        task_owner={11: 99})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "FOLLOWUP-appartenenza-A" and "99" in t
               for i, t in fallimenti), fallimenti


def test_53c_an_empty_selection_cannot_prove_ownership(monkeypatch):
    """Un caso positivo vuoto non e' un caso positivo: senza righe non c'e'
    nulla di cui verificare l'agenzia, e dichiararlo superato sarebbe la stessa
    vacuita' che questa matrice combatte ovunque."""
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-appartenenza-A") == cert.PASS
    assert rows.get("FOLLOWUP-appartenenza-B") == cert.BLOCKED


# ---------------------------------------------------------------------------
# 56-60 - FOLLOWUP su agenzie dedicate
#
# La separazione che ordinamento, limite e controlli a posteriori non davano:
# su un'agenzia creata dal run non esiste una riga che non sia nostra, quindi
# `WHERE t.agency_id = %s` diventa il confine. Il backend non cambia.
# ---------------------------------------------------------------------------

def test_56_without_the_flag_nothing_is_created_and_the_escalation_stays_BLOCKED(monkeypatch):
    """Il percorso dedicato non parte da solo.

    Creare agenzie e' l'operazione piu' consequenziale che questo script sappia
    fare: deve richiedere un'attestazione esplicita, non un default.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        stale_followup={1: [{"id": 11}], 2: [{"id": 22}]})
    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("FOLLOWUP-escalation") == cert.BLOCKED
    assert not any(str(x).upper().startswith("INSERT INTO AGENCIES")
                   for x in database.state.get("sql", []))


def test_57_with_the_flag_the_scan_runs_on_dedicated_agencies(monkeypatch):
    """Con l'attestazione, la route reale viene esercitata nelle due direzioni."""
    code, report, database, probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    rows = {i: k for k, i, _ in report.rows}
    for label, altro in (("C", "D"), ("D", "C")):
        assert rows.get(f"FOLLOWUP-scan-{label}") == cert.PASS
        assert rows.get(f"FOLLOWUP-scan-{label}-elabora-la-propria") == cert.PASS
        assert rows.get(f"FOLLOWUP-scan-{label}-solo-la-propria") == cert.PASS
        assert rows.get(f"FOLLOWUP-scan-{label}-{altro}-invariata") == cert.PASS
    # E l'escalation non e' piu' una lacuna dichiarata.
    assert rows.get("FOLLOWUP-escalation") == cert.PASS
    # Due agenzie create, due rimosse.
    assert len([x for x in database.state.get("sql", [])
                if x.upper().startswith("INSERT INTO AGENCIES")]) == 2
    assert rows.get("CLEAN-DEDICATA") == cert.PASS


@pytest.mark.parametrize("kind,expected", [
    # La scansione elenca una riga che non e' del run: il confine dell'agenzia
    # dedicata non ha tenuto.
    ("intruso_dopo_preflight", "FOLLOWUP-scan-C-solo-la-propria"),
    # La scansione non elabora nemmeno la propria: la prova positiva e' vuota,
    # e senza di essa "non ha toccato nulla di altrui" sarebbe vero per la
    # ragione sbagliata.
    ("scan_skips_own", "FOLLOWUP-scan-C-elabora-la-propria"),
    # Peggio: tocca l'attivita' dell'altra agenzia SENZA nominarla. Gli id
    # tornati sono puliti, e solo il confronto con l'istantanea se ne accorge.
    ("scan_mutates_other", "FOLLOWUP-scan-C-D-invariata"),
])
def test_58_each_dedicated_scan_assertion_fails_on_its_own_defect(
        monkeypatch, kind, expected):
    """Due difetti distinti, due asserzioni distinte.

    Un test che si accontentasse di "una prova FOLLOWUP e' fallita" sarebbe
    soddisfatto da una sonda vicina, e le altre resterebbero indebolibili senza
    che nulla lo dicesse - e' gia' successo due volte in questo file.
    """
    code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True,
        http=FakeHttp(broken={kind}, only="followup",
                      prepopulate=DERIVED, stime=STIME))
    fallimenti = [i for k, i, _ in report.rows if k == cert.FAIL]
    assert expected in fallimenti, f"rompendo {kind!r}: {fallimenti}"
    assert code == 1


def test_59_a_surviving_dedicated_agency_is_a_failure_with_a_recovery_hint(monkeypatch):
    """Il residuo peggiore possibile: una radice di tenancy.

    Il report deve nominarla con l'id - non con lo slug, non con dati personali
    - e dire come rimuoverla a mano.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, dedicated_agencies=True, agenzie_residue=2,
        http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    testo = next((t for i, t in fallimenti if i == "CLEAN-DEDICATA"), None)
    assert testo is not None, fallimenti
    assert "ANCORA PRESENTI" in testo and "RECUPERO:" in testo
    assert "mai per prefisso" in testo


def test_60_the_dedicated_cleanup_deletes_by_id_and_never_by_prefix():
    """Il catalogo serve a SCOPRIRE, non ad autorizzare.

    L'elenco delle tabelle da cui cancellare e' scritto a mano e in ordine di
    dipendenza. L'interrogazione del catalogo che segue e' solo una lettura: se
    trova una dipendenza imprevista la nomina e fa fallire, e non la cancella -
    cancellare cio' che nessuno aveva considerato e' il modo in cui un cleanup
    diventa il danno.
    """
    blocco = _function_source("cleanup_dedicated_agencies")
    assert "DEDICATED_TABLES" in blocco
    assert "IN %s" in blocco
    for vietato in ("LIKE", "slug", "p26-6-cert-"):
        assert vietato not in blocco, f"il cleanup usa {vietato} come criterio"

    # Le DELETE dinamiche dal catalogo non devono esistere: solo conteggi.
    fra_catalogo = blocco[blocco.index("pg_constraint"):]
    assert "DELETE" not in fra_catalogo, (
        "il cleanup cancella tabelle scoperte dal catalogo invece di limitarsi "
        "a segnalarle"
    )
    assert "SELECT COUNT(*)" in fra_catalogo


def test_61_the_api_side_effects_are_verified_not_assumed(monkeypatch):
    """Gli effetti delle API entrano nella verifica finale.

    Archiviare scrive history, calcolare scrive match_runs e i risultati per
    criterio. Sono figli CASCADE e dovrebbero sparire con il genitore - ma il
    run 22d007af7916 ne ha lasciati 32 sul TEST, perche' il genitore era stato
    archiviato invece che cancellato e nessuno li contava.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    assert {i: k for k, i, _ in report.rows}.get("CLEAN-VERIFICA") == cert.PASS

    blocco = _function_source("verify_no_residue")
    for tabella in ("property_status_history", "buy_request_history",
                    "match_runs", "owner_audit_log", "match_requirement_results"):
        assert tabella in blocco, f"la verifica finale ignora {tabella}"


@pytest.mark.parametrize("tabella", [
    "property_status_history", "buy_request_history", "match_runs",
    "owner_audit_log", "match_requirement_results",
])
def test_62_a_surviving_side_effect_is_a_residue(monkeypatch, tabella):
    """Ognuna sa fallire per conto proprio.

    Un test che si accontentasse di "la verifica ha fallito" sarebbe soddisfatto
    da una tabella vicina, e le altre resterebbero scoperte - lo stesso difetto
    gia' trovato tre volte in questo file.
    """
    _code, report, _db, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        effetti_residui={tabella: 3})
    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    assert any(i == "CLEAN-VERIFICA" and tabella in t for i, t in fallimenti), (
        f"{tabella} sopravvive e non viene segnalata: {fallimenti}")


def test_63_the_run_audit_trail_is_removed_by_id(monkeypatch):
    """L'audit del run se ne va, e per id.

    `owner_audit_log` ha entrambe le FK in SET NULL: senza una cancellazione
    esplicita la riga resterebbe con due colonne azzerate - una traccia che non
    dice piu' di cosa parlava, e che nessun run successivo saprebbe attribuire.
    """
    _code, _report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME))
    cancellazioni = [d for d in database.state.get("deletes", [])
                     if "OWNER_AUDIT_LOG" in d.upper()]
    assert cancellazioni, database.state.get("deletes", [])
    assert "owner_account_id IN %s" in cancellazioni[0], cancellazioni[0]
    for vietato in ("LIKE", "created_at", "action ="):
        assert vietato not in cancellazioni[0]


def test_64_a_foreign_dependency_appearing_after_the_census_stops_the_cleanup(monkeypatch):
    """LA CORSA CHE IL CONTEGGIO NON VEDE.

    Il censimento dice che nessuno referenzia le nostre righe. Poi, prima del
    cleanup, compare una riga di qualcun altro che le punta - un'attivita', una
    visita, una proposta creata nel frattempo.

    Le quaranta righe del run sono ancora tutte al loro posto, quindi il
    conteggio "40" torna: non rileva nulla. Ma cancellare adesso avrebbe un
    effetto collaterale - CASCADE porterebbe via quella riga, SET NULL le
    azzererebbe un campo - e sono entrambi danni a dati non nostri.

    Il cleanup deve fermarsi, dire dove, e non toccare niente.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"properties": [("public.property_visits", "property_id")]},
        dipendenti_estranee=1)

    fallimenti = [(i, t) for k, i, t in report.rows if k == cert.FAIL]
    testo = next((t for i, t in fallimenti if i == "CLEAN-ORFANE"), None)
    assert testo is not None, fallimenti
    assert "fuori perimetro" in testo and "property_visits" in testo, testo
    assert "Nessuna cancellazione" in testo

    # E davvero non ha cancellato: nessuna DELETE sulle tabelle del perimetro.
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    for tabella in ("FROM PROPERTIES", "FROM BUY_REQUESTS", "FROM CONTACTS"):
        assert tabella not in cancellazioni, (
            f"ha cancellato {tabella} nonostante la dipendenza estranea")
    # E non ha nemmeno toccato la riga estranea.
    assert "PROPERTY_VISITS" not in cancellazioni


def test_65_being_unable_to_look_is_not_the_same_as_nothing_found(monkeypatch):
    """Se la verifica delle dipendenze non e' eseguibile, non si procede.

    Un `except` che restituisse una lista vuota trasformerebbe un errore di
    lettura in un via libera.
    """
    blocco = _function_source("_foreign_dependencies")
    assert "return [f\"verifica non eseguibile" in blocco or \
           "verifica non eseguibile" in blocco, blocco[-300:]
    assert "return []" not in blocco.split("except")[-1], (
        "in caso di errore la guardia restituisce 'nessuna dipendenza'")


def test_66_rows_of_the_run_referencing_each_other_are_not_foreign(monkeypatch):
    """L'esclusione del perimetro non e' un dettaglio.

    `buy_requests.contact_id` punta ai nostri contatti: senza escludere le
    righe che sono esse stesse del run, la guardia le conterebbe come estranee
    e il cleanup si rifiuterebbe di procedere a ogni esecuzione - trasformando
    una difesa in un blocco permanente.
    """
    _code, report, database, _probe, _ = working_run(
        monkeypatch, http=FakeHttp(prepopulate=DERIVED, stime=STIME),
        fk_perimetro={"contacts": [("public.buy_requests", "contact_id")]},
        dipendenti_interne=2, dipendenti_estranee=0)

    rows = {i: k for k, i, _ in report.rows}
    assert rows.get("CLEAN-ORFANE") == cert.PASS, [
        r for r in report.rows if r[1] == "CLEAN-ORFANE"]
    # E ha davvero cancellato.
    cancellazioni = " ".join(database.state.get("deletes", [])).upper()
    assert "FROM CONTACTS" in cancellazioni
