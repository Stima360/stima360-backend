#!/usr/bin/env python3
"""STIMA Voice Fase 7 - certificazione live dei tre scenari, su TEST soltanto.

    # sola lettura: guardie e prerequisiti, nessuna scrittura
    python scripts/voice_7_live_cert.py --approved-commit <SHA> --base-url <URL> \\
        --pilot-user-id <ID> --plan-only

    # certificazione completa (dopo l'autorizzazione del passo 9 del runbook)
    python scripts/voice_7_live_cert.py --approved-commit <SHA> --base-url <URL> \\
        --pilot-user-id <ID> [--colleague-user-id <ID>] [--other-agency-user-id <ID>]

    # dopo lo spegnimento: tutto deve rifiutare, nessuna scrittura CRM
    python scripts/voice_7_live_cert.py --approved-commit <SHA> --base-url <URL> \\
        --pilot-user-id <ID> --verify-off

CHE COSA PROVA

Il servizio vero, con il fornitore AI vero, attraverso le API vere, con
l'identita' di un operatore pilota vero:
  * Scenario A «Fabio vuole vendere un trilocale in via Duca d'Aosta»: piu'
    Fabio = scelta esplicita; collegamento proprietario solo dopo conferma;
    unita' commerciale assegnata a chi parla; «Vende» attivato;
  * Scenario B «Censisci una palazzina con 12 appartamenti»: senza indirizzo
    chiede e non scrive; con l'indirizzo UN edificio con units_declared=12 e
    nessuna unita' inventata;
  * Scenario C «Domani alle 15 appuntamento con Fabio»: contatto scelto,
    Agenda di chi parla, 60 minuti, tipo `other`, Europe/Rome; lo stesso
    comando ripetuto trova il conflitto e non lo forza;
  * sicurezza: nessuna rotta senza sessione, cookie inventato, canale Basic,
    corpi chiusi, id iniettati, operatori fuori pilota e di altre agenzie,
    idempotenza (stessa chiave, doppia esecuzione), nessuna scrittura prima
    della conferma, nessun trascritto nelle risposte;
  * annullamento entro 15 minuti di tutto cio' che i comandi hanno creato.

DATI SINTETICI E PULIZIA

Due contatti «Fabio Collaudovoce<run>» e «Fabio Collaudovoce<run>b», senza
email e senza telefono (nessuna comunicazione possibile), creati dalle API del
CRM con la sessione del pilota. Un edificio in «Via Collaudo Voce <Run>».
Nessuna DELETE: alla fine i comandi vengono annullati (Cestino «creato per
errore», appuntamento annullato per errore, «Vende» inserito per errore) e i
due contatti vanno nel Cestino con motivo `test_record`. Restano, per
costruzione: il collegamento proprietario (D-A, mai annullabile con la voce,
su un immobile nel Cestino), le righe del registro vocale (append-only), le
righe nel Cestino. L'inventario finale le elenca tutte, con il loro stato.

REGOLE DI CONDOTTA (come P26-6)

  * Solo `stima360_db_test`, verificato sulla connessione vera; commit del
    servizio uguale a `--approved-commit`.
  * Nessuna scrittura prima che TUTTE le guardie passino. Un residuo di un run
    precedente ferma il run.
  * Il pilota non deve avere Google Calendar collegato (l'appuntamento finirebbe
    in un calendario reale) ne' un impegno domani 15-16: altrimenti BLOCCATO
    prima di scrivere.
  * Sessioni temporanee agganciate a operatori GIA' attivi: nessun utente,
    ruolo o membership toccato; a fine run le sessioni sono REVOCATE.
  * Nessun token, cookie, DSN o nome di contatti reali stampato: dei candidati
    si stampano solo i conteggi; degli id solo quelli sintetici di questo run.
  * Qualunque prova non eseguita e' BLOCKED, mai PASS. Uscita 0 solo con tutte
    le prove PASS.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import ssl
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REQUIRED_DB_NAME = "stima360_db_test"
COOKIE_NAME = "stima360_operator_session"
ROMA = ZoneInfo("Europe/Rome")
SESSION_MINUTES = 45
MARKER = "Collaudovoce"
STREET = "Via Collaudo Voce"

SCENARIO_A = "Fabio vuole vendere un trilocale in via Duca d'Aosta"
SCENARIO_B = "Censisci una palazzina con 12 appartamenti"
SCENARIO_C = "Domani alle 15 appuntamento con Fabio"

PASS, FAIL, BLOCKED, NOTE = "PASS", "FAIL", "BLOCKED", "NOTE"

ROTTE = [("GET", "/api/voice/status"), ("POST", "/api/voice/commands"), ("GET", "/api/voice/commands/1"),
         ("POST", "/api/voice/commands/1/confirm"), ("POST", "/api/voice/commands/1/execute"),
         ("POST", "/api/voice/commands/1/cancel"), ("POST", "/api/voice/commands/1/steps/1/retry"),
         ("GET", "/api/voice/commands/1/undo"), ("POST", "/api/voice/commands/1/undo"),
         ("POST", "/api/voice/commands/1/steps/1/undo"), ("GET", "/api/voice/commands/1/steps/1/reconcile"),
         ("POST", "/api/voice/commands/1/steps/1/reconcile"), ("POST", "/api/voice/commands/1/reply"),
         # V8-5: la sola interpretazione per il dialog dell'Agenda (nessun comando, nessuna scrittura)
         ("POST", "/api/voice/interpret")]


class GuardFailure(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Rapporto
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.righe: list[tuple[str, str, str]] = []

    def _add(self, esito, codice, testo):
        self.righe.append((esito, codice, testo))
        print(f"[{esito:7}] {codice:6} {testo}", flush=True)

    def check(self, codice, condizione, testo):
        self._add(PASS if condizione else FAIL, codice, testo)
        return bool(condizione)

    def fail(self, codice, testo):
        self._add(FAIL, codice, testo)

    def blocked(self, codice, testo):
        self._add(BLOCKED, codice, testo)

    def note(self, codice, testo):
        self._add(NOTE, codice, testo)

    def count(self, esito):
        return sum(1 for r in self.righe if r[0] == esito)

    def summary(self) -> int:
        print("=" * 78)
        print(f"PASS {self.count(PASS)}   FAIL {self.count(FAIL)}   BLOCKED {self.count(BLOCKED)}")
        ok = self.count(FAIL) == 0 and self.count(BLOCKED) == 0 and self.count(PASS) > 0
        print(f"ESITO: {'PASS' if ok else ('FAIL' if self.count(FAIL) else 'INCOMPLETO')}")
        print("=" * 78, flush=True)
        return 0 if ok else 1


# ---------------------------------------------------------------------------
# Database (sola lettura, salvo sessioni)
# ---------------------------------------------------------------------------

class Database:
    def __init__(self):
        from operator_auth.database import operator_cursor
        self._cursor = operator_cursor

    def one(self, sql, params=()):
        with self._cursor() as (conn, cur):
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(sql, params)
            riga = cur.fetchone()
            conn.rollback()
        return dict(riga) if riga else None

    def all(self, sql, params=()):
        with self._cursor() as (conn, cur):
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(sql, params)
            righe = [dict(r) for r in cur.fetchall()]
            conn.rollback()
        return righe

    def write(self, sql, params=()):
        with self._cursor(commit=True) as (_, cur):
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()] if cur.description else []


def assert_certification_database(name: str | None) -> str:
    candidato = (name or "").strip()
    if candidato != REQUIRED_DB_NAME:
        raise GuardFailure(f"BLOCCATO: DB_NAME {candidato!r} non e' {REQUIRED_DB_NAME!r}")
    return candidato


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Http:
    def __init__(self, base: str, *, cafile: str | None = None):
        self.base = base.rstrip("/")
        self.ssl = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    def call(self, metodo, percorso, *, token=None, corpo=None, headers=None):
        richiesta = urllib.request.Request(self.base + percorso, method=metodo)
        if corpo is not None:
            richiesta.data = json.dumps(corpo).encode("utf-8")
            richiesta.add_header("Content-Type", "application/json")
        if token:
            richiesta.add_header("Cookie", f"{COOKIE_NAME}={token}")
        for k, v in (headers or {}).items():
            richiesta.add_header(k, v)
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=self.ssl))
        try:
            with opener.open(richiesta, timeout=60) as r:
                stato, testo = r.status, r.read()
        except urllib.error.HTTPError as exc:
            stato, testo = exc.code, exc.read()
        try:
            dati = json.loads(testo or b"null")
        except ValueError:
            dati = None
        return stato, dati, testo.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Sessioni temporanee (revocate a fine run)
# ---------------------------------------------------------------------------

class Sessions:
    def __init__(self, db: Database):
        self.db = db
        self.ids: list[int] = []

    def open(self, user_id: int) -> str:
        from operator_auth.security import generate_session_token, hash_session_token
        grezzo = generate_session_token()
        riga = self.db.write("INSERT INTO operator_sessions (operator_user_id, token_hash, expires_at) "
                             "VALUES (%s, %s, NOW() + (%s || ' minutes')::interval) RETURNING id",
                             (user_id, hash_session_token(grezzo), str(SESSION_MINUTES)))
        self.ids.append(int(riga[0]["id"]))
        return grezzo

    def revoke(self, report: Report):
        if not self.ids:
            return
        self.db.write("UPDATE operator_sessions SET revoked_at = NOW() WHERE id = ANY(%s) AND revoked_at IS NULL",
                      (self.ids,))
        vive = self.db.one("SELECT count(*) AS n FROM operator_sessions WHERE id = ANY(%s) AND revoked_at IS NULL",
                           (self.ids,))["n"]
        report.check("Z.1", vive == 0, f"sessioni del run revocate: {len(self.ids)} (ancora valide: {vive})")


# ---------------------------------------------------------------------------
# Guardie e prerequisiti (nessuna scrittura)
# ---------------------------------------------------------------------------

def operatore(db, user_id):
    return db.one("SELECT u.id, u.status AS user_status, m.agency_id, m.role, m.status AS membership "
                  "FROM operator_users u JOIN agency_memberships m ON m.operator_user_id = u.id "
                  "WHERE u.id = %s AND m.status = 'active' ORDER BY m.agency_id LIMIT 1", (user_id,))


def guards(args, db: Database, report: Report) -> dict:
    assert_certification_database(os.environ.get("DB_NAME"))
    connesso = db.one("SELECT current_database() AS d")["d"]
    if connesso != REQUIRED_DB_NAME:
        raise GuardFailure(f"BLOCCATO: connesso a {connesso!r}")
    report.check("0.1", True, f"database {REQUIRED_DB_NAME} (variabile e connessione)")
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    if not args.skip_commit_check and not commit.startswith(args.approved_commit):
        raise GuardFailure(f"BLOCCATO: commit in esecuzione {commit[:12]!r} diverso da --approved-commit")
    report.check("0.2", True, "commit del servizio approvato" if not args.skip_commit_check
                 else "commit non verificato (--skip-commit-check, solo prova locale)")
    if args.verify_off:                     # spento: vale con o senza 098/099 (passi 5 e 10)
        pilota = operatore(db, args.pilot_user_id)
        if not pilota or pilota["user_status"] != "active":
            raise GuardFailure("BLOCCATO: il pilota non e' un operatore attivo con una membership attiva")
        voce = db.one("SELECT to_regclass('public.voice_commands') IS NOT NULL AS c")["c"]
        report.note("0.3", "registro vocale " + ("presente (098 applicata)" if voce else "assente (098 non applicata)"))
        return {"pilota": pilota, "impostazioni": None, "domani_15": None, "registro": voce}
    versioni = {r["version"]: r for r in db.all(
        "SELECT version, rolled_back_at FROM schema_migrations WHERE version IN ('098_voice_commands', '099_voice_undo')")}
    for v in ("098_voice_commands", "099_voice_undo"):
        if v not in versioni or versioni[v]["rolled_back_at"] is not None:
            raise GuardFailure(f"BLOCCATO: migrazione {v} non applicata")
    report.check("0.3", True, "migrazioni 098 e 099 applicate")
    pilota = operatore(db, args.pilot_user_id)
    if not pilota or pilota["user_status"] != "active":
        raise GuardFailure("BLOCCATO: il pilota non e' un operatore attivo con una membership attiva")
    report.check("0.4", True, f"pilota attivo: agenzia {pilota['agency_id']}, ruolo {pilota['role']}")
    impostazioni = db.one("SELECT enabled, mode FROM voice_agency_settings WHERE agency_id = %s", (pilota["agency_id"],))
    collegato = db.one("SELECT count(*) AS n FROM calendar_connections WHERE user_id = %s AND status <> 'disconnected'",
                       (args.pilot_user_id,))["n"]
    if collegato:
        raise GuardFailure("BLOCCATO: il pilota ha Google Calendar collegato: l'appuntamento di prova finirebbe "
                           "in un calendario reale")
    report.check("0.5", True, "pilota senza Google Calendar collegato")
    domani = datetime.now(ROMA).date() + timedelta(days=1)
    inizio = datetime(domani.year, domani.month, domani.day, 15, 0, tzinfo=ROMA)
    occupato = db.one("SELECT count(*) AS n FROM appointments WHERE assigned_user_id = %s AND agency_id = %s "
                      "AND status IN ('requested', 'scheduled', 'confirmed') AND start_at < %s AND end_at > %s",
                      (args.pilot_user_id, pilota["agency_id"], inizio + timedelta(minutes=60), inizio))["n"]
    if occupato:
        raise GuardFailure("BLOCCATO: il pilota ha gia' un impegno domani 15-16: lo scenario C non e' certificabile")
    report.check("0.6", True, "agenda del pilota libera domani 15:00-16:00 (Europe/Rome)")
    residui = db.one("SELECT (SELECT count(*) FROM contacts WHERE last_name ILIKE %s AND deleted_at IS NULL) AS c, "
                     "(SELECT count(*) FROM buildings WHERE address ILIKE %s "
                     " AND (to_jsonb(buildings)->>'deleted_at') IS NULL) AS b",
                     (MARKER + "%", STREET + "%"))
    if residui["c"] or residui["b"]:
        raise GuardFailure(f"BLOCCATO: residui di un run precedente (contatti {residui['c']}, edifici {residui['b']}): "
                           "nessuna creazione e nessuna pulizia")
    report.check("0.7", True, "nessun residuo sintetico attivo")
    for chiave, uid in (("colleague", args.colleague_user_id), ("other", args.other_agency_user_id)):
        if uid is None:
            continue
        altro = operatore(db, uid)
        if not altro or altro["user_status"] != "active":
            raise GuardFailure(f"BLOCCATO: --{chiave}-user-id non e' un operatore attivo")
        if chiave == "colleague" and altro["agency_id"] != pilota["agency_id"]:
            raise GuardFailure("BLOCCATO: il collega deve essere della stessa agenzia del pilota")
        if chiave == "other" and altro["agency_id"] == pilota["agency_id"]:
            raise GuardFailure("BLOCCATO: --other-agency-user-id deve essere di un'altra agenzia")
    return {"pilota": pilota, "impostazioni": impostazioni, "domani_15": inizio}


# ---------------------------------------------------------------------------
# Aiuti per i comandi
# ---------------------------------------------------------------------------

def passo(vista, intent):
    return next((s for s in (vista or {}).get("steps", []) if s["intent"] == intent), None)


def codici(s):
    return {r["code"] for r in (s or {}).get("reasons", [])}


class Voice:
    def __init__(self, http: Http, token: str):
        self.http, self.token = http, token

    def submit(self, testo, chiave=None):
        chiave = chiave or str(uuid.uuid4())
        stato, dati, grezzo = self.http.call("POST", "/api/voice/commands", token=self.token,
                                             corpo={"client_command_id": chiave, "text": testo})
        return stato, dati, grezzo, chiave

    def get(self, cid):
        return self.http.call("GET", f"/api/voice/commands/{cid}", token=self.token)

    def confirm(self, cid, steps):
        return self.http.call("POST", f"/api/voice/commands/{cid}/confirm", token=self.token,
                              corpo={"all": False, "steps": steps})

    def execute(self, cid):
        return self.http.call("POST", f"/api/voice/commands/{cid}/execute", token=self.token, corpo={})

    def cancel(self, cid):
        return self.http.call("POST", f"/api/voice/commands/{cid}/cancel", token=self.token, corpo={})

    def undo(self, cid):
        return self.http.call("POST", f"/api/voice/commands/{cid}/undo", token=self.token, corpo={})


def conferme(vista, scelte: dict[str, int] | None = None):
    """Conferma i passi che chiedono conferma, con le scelte indicate per i
    riferimenti ambigui. Mai un id che non e' fra i candidati del passo."""
    out = []
    for s in vista["steps"]:
        if s["state"] != "pending" or s["needs"] != "needs_confirmation":
            continue
        scelta = {}
        for ref, lista in (s.get("candidates") or {}).items():
            ids = {c["id"] for c in lista}
            voluto = (scelte or {}).get(ref)
            if len(lista) > 1:
                if voluto not in ids:
                    raise GuardFailure(f"scelta per {ref} non fra i candidati del passo {s['ordinal']}")
                scelta[ref] = voluto
        out.append({"ordinal": s["ordinal"], "choices": scelta or None})
    return out


# ---------------------------------------------------------------------------
# Il run
# ---------------------------------------------------------------------------

class Run:
    def __init__(self, args, db: Database, http: Http, report: Report, contesto: dict):
        self.args, self.db, self.http, self.r, self.ctx = args, db, http, report, contesto
        self.run = "".join(secrets.choice(string.ascii_lowercase) for _ in range(6))
        self.inizio = None
        self.creati: list[tuple[str, int]] = []           # (entita', id) sintetici o creati dai comandi
        self.comandi: list[int] = []
        self.sessioni = Sessions(db)
        self.agenzia = contesto["pilota"]["agency_id"]

    # -- dati sintetici ------------------------------------------------------
    def contatti(self, token):
        ids = []
        for suffisso in ("", "b"):
            stato, dati, _ = self.http.call("POST", "/api/core/contacts", token=token, corpo={
                "contact_type": "person", "first_name": "Fabio", "last_name": f"{MARKER}{self.run}{suffisso}",
                "source": "voice_7_cert", "notes": f"STIMA Voice Fase 7 - dato sintetico del run {self.run}"})
            if stato != 201:
                raise GuardFailure(f"creazione del contatto sintetico rifiutata ({stato})")
            ids.append(int(dati["id"]))
            self.creati.append(("contact", int(dati["id"])))
        self.r.note("F.1", f"contatti sintetici creati: {ids} (cognome {MARKER}{self.run}, senza email/telefono)")
        return ids

    def scritture_crm(self):
        """Le righe CRM nate dall'inizio del run per mano del pilota (le fixture escluse). Limitate al
        pilota perche' il TEST e' condiviso: chi lavora nel frattempo non deve falsare la prova. Gli
        edifici non hanno un autore: si contano quelli dell'agenzia (raro che ne nasca uno in quei minuti)."""
        fissi = [i for e, i in self.creati if e == "contact"]
        return self.db.one(
            "SELECT (SELECT count(*) FROM properties WHERE agency_id = %(a)s AND created_at >= %(t)s "
            "        AND assigned_agent_id = %(u)s) AS properties, "
            "(SELECT count(*) FROM buildings WHERE agency_id = %(a)s AND created_at >= %(t)s) AS buildings, "
            "(SELECT count(*) FROM appointments WHERE agency_id = %(a)s AND created_at >= %(t)s "
            "        AND (created_by_user_id = %(u)s OR assigned_user_id = %(u)s)) AS appointments, "
            "(SELECT count(*) FROM leads WHERE agency_id = %(a)s AND created_at >= %(t)s "
            "        AND (created_by_user_id = %(u)s OR assigned_agent_id = %(u)s)) AS leads, "
            "(SELECT count(*) FROM contacts WHERE agency_id = %(a)s AND created_at >= %(t)s "
            "        AND assigned_agent_id = %(u)s AND NOT (id = ANY(%(f)s))) AS contacts",
            {"a": self.agenzia, "t": self.inizio, "f": fissi, "u": self.args.pilot_user_id})

    # -- sicurezza senza sessione -------------------------------------------
    def senza_sessione(self):
        prima = self.db.one("SELECT count(*) AS n FROM voice_commands")["n"]
        esiti = []
        for metodo, percorso in ROTTE:
            corpo = {"client_command_id": str(uuid.uuid4()), "text": "Nota"} if metodo == "POST" else None
            a = self.http.call(metodo, percorso, corpo=corpo)[0]
            b = self.http.call(metodo, percorso, corpo=corpo, token=secrets.token_urlsafe(32))[0]
            c = self.http.call(metodo, percorso, corpo=corpo,
                               headers={"Authorization": "Basic " + "Y2VydGlmaWNhemlvbmU6ZmFsc2E="})[0]
            esiti.append((percorso, a, b, c))
        self.r.check("S.1", all(a == 401 for _, a, _, _ in esiti), f"{len(ROTTE)} rotte senza sessione: tutte 401")
        self.r.check("S.2", all(b == 401 for _, _, b, _ in esiti), f"{len(ROTTE)} rotte con cookie inventato: tutte 401")
        self.r.check("S.3", all(c == 401 for _, _, _, c in esiti), f"{len(ROTTE)} rotte con credenziali Basic finte: tutte 401")
        self.r.check("S.4", self.db.one("SELECT count(*) AS n FROM voice_commands")["n"] == prima,
                     "nessun comando registrato senza sessione")

    # -- Scenario A ----------------------------------------------------------
    def scenario_a(self, voce: Voice, fabio_a: int, fabio_b: int):
        prima = self.scritture_crm()
        chiave = str(uuid.uuid4())
        stato, vista, grezzo, _ = voce.submit(SCENARIO_A, chiave)
        if stato != 200 or not vista:
            self.r.fail("A.1", f"interpretazione non riuscita ({stato}, {(vista or {}).get('code')})")
            return
        cid = vista["command_id"]
        self.comandi.append(cid)
        intents = [s["intent"] for s in vista["steps"]]
        self.r.check("A.1", {"create_unit", "link_owner", "activate_seller"} <= set(intents),
                     f"piano: {intents}")
        self.r.check("A.2", SCENARIO_A not in grezzo, "la risposta non riporta il testo dettato")
        self.r.check("A.3", self.scritture_crm() == prima, "nessuna scrittura CRM prima della conferma")
        collegamento = passo(vista, "link_owner")
        unita = passo(vista, "create_unit")
        if not collegamento or not unita:
            self.r.blocked("A.4", "il modello non ha prodotto unita' e collegamento: scenario non certificabile")
            voce.cancel(cid)
            return
        candidati = {c["id"] for c in (collegamento.get("candidates") or {}).get("contact", [])}
        self.r.check("A.4", {fabio_a, fabio_b} <= candidati and len(candidati) >= 2,
                     f"piu' «Fabio»: scelta richiesta ({len(candidati)} candidati, i 2 sintetici inclusi)")
        self.r.check("A.5", collegamento["needs"] == "needs_confirmation"
                     and "owner_link_confirmation_required" in codici(collegamento),
                     "collegamento proprietario: solo con conferma esplicita")
        # idempotenza: stessa chiave e stesso testo = stesso comando; testo diverso = conflitto
        s2, v2, _, _ = voce.submit(SCENARIO_A, chiave)
        self.r.check("S.5", s2 == 200 and v2 and v2["command_id"] == cid, "stessa chiave: stesso comando")
        s3, v3, _, _ = voce.submit(SCENARIO_A + " subito", chiave)
        self.r.check("S.6", s3 == 409 and (v3 or {}).get("code") == "VOICE_IDEMPOTENCY_KEY_REUSED",
                     "stessa chiave con testo diverso: rifiutata (409)")
        # corpi chiusi e scelte convalidate
        s4, v4, _ = self.http.call("POST", "/api/voice/commands", token=voce.token,
                                   corpo={"client_command_id": str(uuid.uuid4()), "text": "x", "agency_id": 999})
        self.r.check("S.7", s4 == 422, "campo agency_id nel corpo: 422")
        senza = conferme(vista, {"contact": fabio_a})
        senza_scelta = [{**c, "choices": None} for c in senza]
        s5, v5, _ = voce.confirm(cid, senza_scelta)
        self.r.check("A.6", s5 == 422 and (v5 or {}).get("code") == "VOICE_CHOICE_REQUIRED",
                     "conferma senza scegliere il Fabio: rifiutata")
        estraneo = self.id_estraneo()
        iniettata = [{**c, "choices": {k: estraneo for k in (c["choices"] or {})} or None} for c in senza]
        s6, v6, _ = voce.confirm(cid, iniettata)
        self.r.check("S.8", s6 == 422 and (v6 or {}).get("code") == "VOICE_CHOICE_INVALID",
                     "id di contatto fuori dai candidati (iniettato): rifiutato")
        self.r.check("A.7", self.scritture_crm() == prima, "ancora nessuna scrittura CRM")
        s7, v7, _ = voce.confirm(cid, senza)
        if s7 != 200:
            self.r.fail("A.8", f"conferma rifiutata ({s7}, {(v7 or {}).get('code')})")
            return
        s8, v8, _ = voce.execute(cid)
        s9, v9, _ = voce.execute(cid)                                  # doppio tocco
        fatto = v9 if s9 == 200 else v8
        self.r.check("A.8", s8 == 200 and fatto["status"] == "completed", f"esecuzione: {fatto.get('status')}")
        u, l, v = passo(fatto, "create_unit"), passo(fatto, "link_owner"), passo(fatto, "activate_seller")
        uid = (u or {}).get("result", {}).get("id")
        if uid:
            self.creati.append(("property", uid))
            p = self.db.one("SELECT record_kind, assigned_agent_id, agency_id FROM properties WHERE id = %s", (uid,))
            self.r.check("A.9", p and p["record_kind"] == "crm" and p["assigned_agent_id"] == self.args.pilot_user_id
                         and p["agency_id"] == self.agenzia,
                         f"unita' commerciale #{uid} dell'agenzia, assegnata a chi parla")
            proprietari = self.db.all("SELECT contact_id FROM property_contacts WHERE property_id = %s AND role = 'owner'",
                                      (uid,))
            self.r.check("A.10", [r["contact_id"] for r in proprietari] == [fabio_a],
                         "proprietario collegato: il Fabio scelto, uno solo")
        else:
            self.r.fail("A.9", "unita' non creata")
        lead = (v or {}).get("result", {}).get("lead_id")
        if lead:
            self.creati.append(("seller_lead", lead))
        self.r.check("A.11", bool(lead) and (v or {}).get("result", {}).get("origin") == "created", "«Vende» attivato")
        dopo = self.scritture_crm()
        self.r.check("S.9", dopo["properties"] == prima["properties"] + 1 and dopo["leads"] == prima["leads"] + 1
                     and dopo["contacts"] == prima["contacts"], "doppia esecuzione: una sola unita' e un solo «Vende»")
        self.r.check("A.12", (l or {}).get("undo") == "not_undoable", "collegamento proprietario: non annullabile (D-A)")
        return cid

    def id_estraneo(self):
        """Un id di contatto che il pilota non puo' scegliere: di un'altra agenzia se esiste, se no inesistente."""
        riga = self.db.one("SELECT id FROM contacts WHERE agency_id <> %s ORDER BY id LIMIT 1", (self.agenzia,))
        if riga:
            return int(riga["id"])
        return int(self.db.one("SELECT COALESCE(max(id), 0) + 100000 AS n FROM contacts")["n"])

    # -- Scenario B ----------------------------------------------------------
    def scenario_b(self, voce: Voice):
        prima = self.scritture_crm()
        stato, vista, _, _ = voce.submit(SCENARIO_B)
        if stato != 200:
            self.r.fail("B.1", f"interpretazione non riuscita ({stato}, {(vista or {}).get('code')})")
            return
        self.comandi.append(vista["command_id"])
        edificio = passo(vista, "create_building")
        self.r.check("B.1", edificio is not None and [s["intent"] for s in vista["steps"]].count("create_unit") == 0,
                     "un solo create_building, nessuna unita' inventata")
        self.r.check("B.2", (edificio or {}).get("needs") == "needs_answer"
                     and "building_location_missing" in codici(edificio), "chiede l'indirizzo")
        s, v, _ = voce.confirm(vista["command_id"], [{"ordinal": (edificio or {}).get("ordinal", 1), "choices": None}])
        self.r.check("B.3", 400 <= s < 500 and (v or {}).get("code") == "VOICE_CONFIRMATION_NOT_ALLOWED",
                     "una domanda di dati non si sblocca con una conferma")
        self.r.check("B.4", self.scritture_crm() == prima, "nessuna scrittura")
        voce.cancel(vista["command_id"])
        via = f"{STREET} {self.run.capitalize()}"
        testo = f"Censisci una palazzina in {via} 1 a Tortoreto con 12 appartamenti"
        stato, vista, _, _ = voce.submit(testo)
        if stato != 200:
            self.r.fail("B.5", f"interpretazione non riuscita ({stato}, {(vista or {}).get('code')})")
            return
        cid = vista["command_id"]
        self.comandi.append(cid)
        if vista["status"] != "completed":
            vista = self._conferma_ed_esegui(voce, vista)
        e = passo(vista, "create_building")
        bid = (e or {}).get("result", {}).get("id")
        self.r.check("B.5", vista.get("status") == "completed" and bid, f"edificio creato: {vista.get('status')}")
        if bid:
            self.creati.append(("building", bid))
            b = self.db.one("SELECT units_declared, agency_id, address FROM buildings WHERE id = %s", (bid,))
            n = self.db.one("SELECT count(*) AS n FROM properties WHERE building_id = %s", (bid,))["n"]
            self.r.check("B.6", b["units_declared"] == 12 and b["agency_id"] == self.agenzia,
                         "units_declared = 12, agenzia del pilota")
            self.r.check("B.7", n == 0, "nessuna delle 12 unita' generata")
        return cid

    def _conferma_ed_esegui(self, voce, vista):
        s, v, _ = voce.confirm(vista["command_id"], conferme(vista))
        if s != 200:
            return v or {}
        s, v, _ = voce.execute(vista["command_id"])
        return v or {}

    # -- Scenario C ----------------------------------------------------------
    def scenario_c(self, voce: Voice, fabio_a: int, fabio_b: int):
        stato, vista, _, _ = voce.submit(SCENARIO_C)
        if stato != 200:
            self.r.fail("C.1", f"interpretazione non riuscita ({stato}, {(vista or {}).get('code')})")
            return
        cid = vista["command_id"]
        self.comandi.append(cid)
        app = passo(vista, "create_appointment")
        if not app:
            self.r.blocked("C.1", "il modello non ha prodotto l'appuntamento")
            voce.cancel(cid)
            return
        candidati = {c["id"] for c in (app.get("candidates") or {}).get("contact", [])}
        self.r.check("C.1", {fabio_a, fabio_b} <= candidati, f"contatto da scegliere ({len(candidati)} candidati)")
        atteso = self.ctx["domani_15"]
        inizio = datetime.fromisoformat(app["start_at"]) if app.get("start_at") else None
        fine = datetime.fromisoformat(app["end_at"]) if app.get("end_at") else None
        self.r.check("C.2", inizio == atteso and fine and (fine - inizio) == timedelta(minutes=60),
                     f"domani 15:00 Europe/Rome, 60 minuti ({app.get('start_at')})")
        prima = self.scritture_crm()
        self.r.check("C.2b", vista["status"] != "completed" and prima["appointments"] == 0,
                     "nessun appuntamento prima della scelta del contatto")
        s, v, _ = voce.confirm(cid, conferme(vista, {"contact": fabio_a}))     # il Fabio sintetico, esplicito
        vista = voce.execute(cid)[1] if s == 200 else (v or {})
        a = passo(vista, "create_appointment")
        aid = (a or {}).get("result", {}).get("id")
        self.r.check("C.3", vista.get("status") == "completed" and aid, f"appuntamento creato: {vista.get('status')}")
        if aid:
            self.creati.append(("appointment", aid))
            r = self.db.one("SELECT assigned_user_id, appointment_type, contact_id, start_at, end_at, agency_id, status "
                            "FROM appointments WHERE id = %s", (aid,))
            self.r.check("C.4", r["assigned_user_id"] == self.args.pilot_user_id and r["agency_id"] == self.agenzia,
                         "nell'Agenda di chi parla")
            self.r.check("C.5", r["appointment_type"] == "other" and r["contact_id"] == fabio_a
                         and r["status"] == "scheduled", "tipo other, contatto scelto, stato scheduled")
            self.r.check("C.6", r["start_at"].astimezone(ROMA) == atteso
                         and r["end_at"] - r["start_at"] == timedelta(minutes=60), "15:00-16:00 Europe/Rome")
        # conflitto: lo stesso comando con una chiave nuova non si forza
        stato, vista2, _, _ = voce.submit(SCENARIO_C)
        if stato == 200:
            self.comandi.append(vista2["command_id"])
            a2 = passo(vista2, "create_appointment")
            self.r.check("C.7", "agenda_conflict" in codici(a2) and vista2["status"] != "completed",
                         "stesso orario: conflitto segnalato, nessuna esecuzione")
            voce.cancel(vista2["command_id"])
        else:
            self.r.fail("C.7", f"secondo comando non interpretato ({stato})")
        return cid

    # -- operatori fuori pilota / altre agenzie ------------------------------
    def estranei(self, cid_a):
        for codice, uid, descr in (("S.10", self.args.colleague_user_id, "collega della stessa agenzia fuori pilota"),
                                   ("S.11", self.args.other_agency_user_id, "operatore di un'altra agenzia")):
            if uid is None:
                self.r.blocked(codice, f"{descr}: non indicato (--{'colleague' if codice == 'S.10' else 'other-agency'}"
                                       "-user-id)")
                continue
            token = self.sessioni.open(uid)
            stato = self.http.call("GET", "/api/voice/status", token=token)[1]
            vede = self.http.call("GET", f"/api/voice/commands/{cid_a}", token=token)[0] if cid_a else None
            prima = self.db.one("SELECT count(*) AS n FROM voice_commands")["n"]
            invia = self.http.call("POST", "/api/voice/commands", token=token,
                                   corpo={"client_command_id": str(uuid.uuid4()), "text": "Nota per Fabio: prova"})[0]
            dopo = self.db.one("SELECT count(*) AS n FROM voice_commands")["n"]
            self.r.check(codice, stato == {"enabled": False, "speech_input": False} and vede in (403, 404)
                         and invia == 403 and dopo == prima,
                         f"{descr}: Voice spento, comando del pilota non visibile ({vede}), invio rifiutato ({invia})")

    # -- annullamento e pulizia ---------------------------------------------
    def annulla(self, voce: Voice, cid, codice, attesi: dict[str, str]):
        if not cid:
            self.r.blocked(codice, "nessun comando da annullare")
            return
        s, v, _ = voce.undo(cid)
        stati = {}
        if s == 200 and v is not None:
            vista = voce.get(cid)[1] or {}
            stati = {p["intent"]: p["undo"] for p in vista.get("steps", [])}
        self.r.check(codice, all(stati.get(k) == x for k, x in attesi.items()),
                     f"annullamento entro 15 minuti: {stati}")

    def pulizia(self, token):
        for entita, eid in self.creati:
            if entita != "contact":
                continue
            s, v, _ = self.http.call("POST", f"/api/core/contacts/{eid}/trash", token=token,
                                     corpo={"reason_code": "test_record", "note": f"STIMA Voice Fase 7 run {self.run}"})
            self.r.check("Z.2", s == 200, f"contatto sintetico #{eid} nel Cestino (test_record): {s} "
                                          f"{(v or {}).get('code') or ''}".rstrip())

    def inventario(self):
        print("-" * 78)
        print(f"INVENTARIO DEGLI EFFETTI (run {self.run}, comandi vocali {self.comandi}):")
        tabelle = {"contact": "contacts", "property": "properties", "building": "buildings",
                   "appointment": "appointments", "seller_lead": "leads"}
        for entita, eid in self.creati:
            riga = self.db.one(f"SELECT to_jsonb(t) AS j FROM {tabelle[entita]} t WHERE id = %s", (eid,))
            j = (riga or {}).get("j") or {}
            stato = {k: j.get(k) for k in ("status", "deleted_at", "deleted_reason", "archived_at", "cancel_kind",
                                           "cancellation_kind", "closed_reason") if j.get(k) is not None}
            print(f"  {entita:12} #{eid:<8} {json.dumps(stato, default=str)}")
        legami = self.db.all("SELECT pc.property_id, pc.contact_id FROM property_contacts pc "
                             "WHERE pc.property_id = ANY(%s) AND pc.role = 'owner'",
                             ([i for e, i in self.creati if e == "property"],))
        for l in legami:
            print(f"  owner_link   immobile #{l['property_id']} - contatto #{l['contact_id']} (resta: D-A, immobile nel Cestino)")
        print(f"  registro vocale: {len(self.comandi)} comandi (append-only, non si cancellano)")
        print("-" * 78, flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approved-commit", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--pilot-user-id", type=int, required=True)
    parser.add_argument("--colleague-user-id", type=int)
    parser.add_argument("--other-agency-user-id", type=int)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--plan-only", action="store_true", help="solo guardie e prerequisiti, nessuna scrittura")
    modo.add_argument("--verify-off", action="store_true", help="verifica che STIMA Voice sia spento")
    parser.add_argument("--skip-commit-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cafile", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    report = Report()
    db = Database()
    http = Http(args.base_url, cafile=args.cafile)
    try:
        contesto = guards(args, db, report)
    except GuardFailure as exc:
        report.blocked("0.X", str(exc))
        return report.summary()
    if args.plan_only:
        report.note("P", f"impostazioni Voice dell'agenzia: {contesto['impostazioni']}")
        report.note("P", "--plan-only: nessuna scrittura eseguita")
        return report.summary()

    run = Run(args, db, http, report, contesto)
    try:
        _esegui(args, db, http, report, contesto, run)
    except GuardFailure as exc:
        report.fail("X", str(exc))
    except Exception as exc:                                      # noqa: BLE001 - mai un segreto stampato
        report.fail("X", f"errore inatteso: {type(exc).__name__}")
    finally:
        try:
            run.inventario()
        except Exception as exc:                                  # noqa: BLE001
            report.fail("Z.3", f"inventario non riuscito: {type(exc).__name__}")
        run.sessioni.revoke(report)
        print(f"Per il controllo dei log: cercare nei log del servizio il marcatore «{MARKER}{run.run}» e il "
              f"testo «{SCENARIO_A}»: non devono comparire.")
    return report.summary()


def _esegui(args, db, http, report, contesto, run):
    run.inizio = db.one("SELECT NOW() AS t")["t"]
    token = run.sessioni.open(args.pilot_user_id)
    stato = http.call("GET", "/api/voice/status", token=token)[1]
    if args.verify_off:
        conta = (lambda: db.one("SELECT count(*) AS n FROM voice_commands")["n"]) if contesto["registro"] \
            else (lambda: 0)
        prima = conta()
        invio = http.call("POST", "/api/voice/commands", token=token,
                          corpo={"client_command_id": str(uuid.uuid4()), "text": "Nota per Fabio: spento?"})
        report.check("K.1", stato == {"enabled": False, "speech_input": False}, f"stato: {stato}")
        report.check("K.2", invio[0] in (403, 404), f"invio rifiutato: {invio[0]} {(invio[1] or {}).get('code')}")
        report.check("K.3", conta() == prima, "nessun comando registrato")
        crm = http.call("GET", "/api/core/contacts?limit=1", token=token)[0]
        report.check("K.4", crm == 200, f"CRM raggiungibile con la stessa sessione: {crm}")
        return
    if not report.check("0.8", stato == {"enabled": True, "speech_input": False},
                        f"Voice acceso per il pilota, microfono del browser spento: {stato}"):
        report.blocked("0.X", "STIMA Voice non risulta acceso solo in modalita' testo per il pilota")
        return
    run.senza_sessione()
    fabio_a, fabio_b = run.contatti(token)
    voce = Voice(http, token)
    cid_a = run.scenario_a(voce, fabio_a, fabio_b)
    cid_b = run.scenario_b(voce)
    cid_c = run.scenario_c(voce, fabio_a, fabio_b)
    run.estranei(cid_a)
    run.annulla(voce, cid_c, "U.1", {"create_appointment": "undone"})
    run.annulla(voce, cid_b, "U.2", {"create_building": "undone"})
    run.annulla(voce, cid_a, "U.3", {"activate_seller": "undone", "link_owner": "not_undoable",
                                     "create_unit": "undone"})
    run.pulizia(token)


if __name__ == "__main__":
    sys.exit(main())
