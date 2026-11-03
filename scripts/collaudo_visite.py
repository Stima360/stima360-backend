"""ROADMAP VISITE - collaudo REALE su TEST, con UNA visita sintetica dall'inizio alla fine.

    Prenotazione -> Agenda -> Esito -> schede immobile/contatto -> Foglio visita PDF
    -> Aggiornamento al proprietario -> Portale -> privacy, permessi, duplicati, ritiro

Pensato per un One-Off Job Render del servizio TEST (stesse variabili d'ambiente del servizio).

UN SOLO COMANDO

  python scripts/collaudo_visite.py avvia --expected-host <HOST_DB_TEST_VERIFICATO>

COME LAVORA

Importa l'applicazione vera (`main.app`) e la pilota in-process con un client di test: le
stesse rotte, gli stessi service e lo stesso database del servizio. NON apre porte, NON chiama
il cron, NON invia nulla: la sola cosa che il CRM accoda (la conferma al cliente, se accesa) puo'
partire solo verso gli indirizzi dell'elenco `APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS`, e il
cliente sintetico ha un indirizzo `@example.invalid` che non puo' mai esserci: lo script verifica
che non venga accodato nulla per lui e si ferma se l'elenco e' «tutti» (`*`).

L'identita' dei chiamanti non passa dal login (nessuna password): lo script sostituisce SOLO la
dipendenza che risolve la sessione, e la sostituzione esiste solo dentro questo processo. Permessi
e isolamento tra agenzie sono quelli veri: li decidono i service, sul contesto dell'attore.

GUARDIE (prima di toccare il database; se una manca, si ferma)
  * `DB_NAME` e' `stima360_db_test` e `DB_HOST` e' l'host TEST indicato a mano (`--expected-host`);
  * il database e' un primario (non in recovery) e si chiama davvero `stima360_db_test`;
  * nessuna variabile d'ambiente di ambiente dichiara «production/prod/produzione»;
  * l'elenco dei destinatari delle notifiche non e' assente-per-errore ne' `*`;
  * agenzia, attore (owner/admin) e agente sono attivi; l'agente ha un orario di lavoro.

COSA SCRIVE (tutto identificabile: «COLLAUDO VISITE <run>» nel nome, codice immobile
`COLLAUDO-VIS-<run>`, indirizzo `@example.invalid`):
  un immobile con incarico (percorso ordinario: acquisizione -> sopralluogo -> incarico), un
  proprietario venditore, un collegamento di prenotazione, un acquirente (creato dalla
  prenotazione), una visita, il foglio visita, un aggiornamento al proprietario, e un
  PROPRIETARIO DEL PORTALE sintetico (contatto + account + accesso + sessione di due ore).
  Due soli ritocchi diretti, entrambi su righe appena create dal collaudo e verificate: l'orario
  del sopralluogo e della visita viene spostato nel passato (l'Agenda completa solo cio' che e'
  iniziato; non si aspetta l'orario vero).

AGENTE DI PROVA (`--agente-di-prova`): se sul TEST nessun agente ha un orario di lavoro (la tabella
`agent_working_hours` e' vuota) il collegamento di prenotazione non puo' esistere. Con l'opzione lo
script predispone UN agente sintetico con le regole applicative esistenti, mai con SQL:
  * l'account nasce dalla rotta di piattaforma `POST /api/platform/agencies/{id}/operators` (solo il
    Super Admin designato da `STIMA360_SUPER_ADMIN_USER_ID`, con il suo registro di audit), con una
    password casuale che non viene mai stampata ne' salvata: nessuno potra' accedere;
  * gli orari li scrive l'owner dell'agenzia con `PUT /api/appointments/agents/{id}/working-hours`
    (7 giorni, 09:00-19:00: cosi' un orario libero c'e' sempre);
  * a fine collaudo, in quest'ordine: orari svuotati, membership revocata, account disabilitato ed
    eliminato (eliminazione LOGICA, con audit). Nessuna riga viene cancellata.
  Con l'opzione l'agente reale NON viene usato ne' toccato: orari, appuntamenti e calendari degli
  agenti reali restano come sono (lo script confronta l'impronta dei loro orari prima e dopo).
  `--agente-di-prova` e `--agent-user-id` sono incompatibili.

COSA NON TOCCA: conferme e promemoria (nessuna configurazione, nessun cron), clienti e immobili
reali, pubblicazioni di proprietari reali, `main`, le migrazioni.

PULIZIA (automatica a fine collaudo, rilanciabile con `cleanup`): sessione e accesso del
proprietario sintetico chiusi, collegamento disattivato, pubblicazione ritirata (e provata),
immobile archiviato. Le righe restano, marcate, come storico.

ALTRI MODI: `preflight` (sola lettura) e `cleanup`.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import Request  # noqa: E402  - a livello di modulo: FastAPI risolve le annotazioni differite da qui

import collaudo_punto2 as _p2  # noqa: E402  - guardie e aiuti gia' collaudati sul TEST

EXPECTED_DB = _p2.EXPECTED_DB
ROMA = ZoneInfo("Europe/Rome")
CODE_PREFIX = "COLLAUDO-VIS-"
NAME_PREFIX = "COLLAUDO VISITE"
ATTORE_HEADER = "x-collaudo-attore"
PUB = "/api/public/visit-booking"
OWNER_PORTAL = "/api/owner/portal/properties"
OWNER_COOKIE = "stima360_owner_session"
AGENT_EMAIL_PREFIX = "collaudo.agente."
AGENT_EMAIL_DOMAIN = "@example.invalid"
ORARIO_PROVA = {"inizio": 540, "fine": 1140}                       # 09:00-19:00, tutti i giorni
SEGRETO = "NOTA-INTERNA-COLLAUDO-VISITE"
NOTA_IMMOBILE = "NOTA-RISERVATA-COLLAUDO"
PREZZO_MINIMO = "99999"
TESTO_PUBBLICO = ("La visita è stata effettuata e ha suscitato interesse per l'immobile. "
                  "Prossimo passo: valutare una seconda visita.")
OK, KO, NA = "PASS", "FAIL", "SKIP"


# ---------------------------------------------------------------------------
# Esiti
# ---------------------------------------------------------------------------

class Registro:
    """L'elenco dei controlli: ogni riga e' PASS o FAIL, con il passo a cui appartiene."""

    def __init__(self):
        self.righe: list[tuple[str, str, str, str]] = []

    def controllo(self, passo: str, descrizione: str, condizione: bool, dettaglio: str = "") -> bool:
        stato = OK if condizione else KO
        self.righe.append((passo, descrizione, stato, "" if condizione else dettaglio))
        extra = f"  [{dettaglio}]" if (dettaglio and not condizione) else ""
        print(f"{stato:4} {passo:<3} {descrizione}{extra}", flush=True)
        return bool(condizione)

    def saltato(self, passo: str, descrizione: str, motivo: str) -> None:
        self.righe.append((passo, descrizione, NA, motivo))
        print(f"{NA:4} {passo:<3} {descrizione}  [{motivo}]", flush=True)

    def per_passo(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for passo, _desc, stato, _det in self.righe:
            out.setdefault(passo, []).append(stato)
        return out

    def fallite(self) -> int:
        return sum(1 for r in self.righe if r[2] == KO)


# ---------------------------------------------------------------------------
# Testo di un PDF (pdftotext se c'e', altrimenti un estrattore interno per i PDF di reportlab)
# ---------------------------------------------------------------------------

_STRINGA = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
_TOKEN = re.compile(rb"/([^\s/\[\]()<>]+)\s+[-\d.]+\s+Tf|<([0-9A-Fa-f\s]*)>|(\((?:\\.|[^\\()])*\))", re.S)


def _decodifica_stringa(raw: bytes) -> bytes:
    corpo = raw[1:-1]
    out = bytearray()
    i = 0
    while i < len(corpo):
        c = corpo[i]
        if c == 0x5C and i + 1 < len(corpo):                       # backslash
            n = corpo[i + 1]
            if 0x30 <= n <= 0x37:                                  # \ddd ottale
                j = i + 1
                cifre = b""
                while j < len(corpo) and len(cifre) < 3 and 0x30 <= corpo[j] <= 0x37:
                    cifre += bytes([corpo[j]])
                    j += 1
                out.append(int(cifre, 8) & 0xFF)
                i = j
                continue
            out.extend({0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f"}.get(n, bytes([n])))
            i += 2
            continue
        out.append(c)
        i += 1
    return bytes(out)


def _flussi(pdf: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """numero oggetto -> (dizionario/corpo, dati decodificati del flusso o None)."""
    oggetti: dict[int, tuple[bytes, bytes | None]] = {}
    for m in re.finditer(rb"(\d+) 0 obj(.*?)endobj", pdf, re.S):
        corpo = m.group(2)
        dati = None
        k = re.search(rb"stream\r?\n(.*?)endstream", corpo, re.S)
        if k:
            grezzo = k.group(1).strip()
            try:
                if grezzo.endswith(b"~>"):
                    grezzo = base64.a85decode(grezzo[:-2], adobe=False)
                dati = zlib.decompress(grezzo)
            except Exception:
                dati = None
        oggetti[int(m.group(1))] = (corpo[:k.start()] if k else corpo, dati)
    return oggetti


def _cmap(testo: bytes) -> tuple[int, dict[int, str]]:
    """Mappa ToUnicode: (byte per codice, codice -> testo)."""
    larghezza = 1
    cs = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", testo)
    if cs:
        larghezza = max(1, len(cs.group(1)) // 2)

    def u(h: bytes) -> str:
        return bytes.fromhex(h.decode()).decode("utf-16-be", errors="replace")

    mappa: dict[int, str] = {}
    for blocco in re.finditer(rb"beginbfchar(.*?)endbfchar", testo, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blocco.group(1)):
            mappa[int(src, 16)] = u(dst)
    for blocco in re.finditer(rb"beginbfrange(.*?)endbfrange", testo, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blocco.group(1)):
            base = u(dst)
            for n, codice in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                mappa[codice] = base[:-1] + chr(ord(base[-1]) + n) if base else ""
    return larghezza, mappa


def testo_pdf(pdf: bytes) -> str:
    """Il testo del PDF, spazi normalizzati. Usa `pdftotext` quando e' installato; altrimenti legge
    i PDF di reportlab: font standard (stringhe in chiaro) o TrueType incorporati (glifi + mappa
    ToUnicode). Chi confronta il testo deve ignorare gli spazi (`contiene`)."""
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True)
        return " ".join(out.stdout.decode("utf-8", errors="replace").split())
    oggetti = _flussi(pdf)
    cmap_per_font: dict[int, tuple[int, dict[int, str]]] = {}
    for num, (corpo, _dati) in oggetti.items():
        t = re.search(rb"/ToUnicode\s+(\d+) 0 R", corpo)
        if t and int(t.group(1)) in oggetti and oggetti[int(t.group(1))][1] is not None:
            cmap_per_font[num] = _cmap(oggetti[int(t.group(1))][1])
    nome_font: dict[bytes, int] = {}
    for corpo, _ in oggetti.values():
        for regione in re.finditer(rb"/Font\s*(?:<<(.*?)>>|(\d+) 0 R)", corpo, re.S):
            testo_regione = regione.group(1)
            if testo_regione is None and int(regione.group(2)) in oggetti:
                testo_regione = oggetti[int(regione.group(2))][0]
            for nome, num in re.findall(rb"/([^\s/\[\]()<>]+)\s+(\d+) 0 R", testo_regione or b""):
                nome_font[nome] = int(num)
    pezzi: list[str] = []
    for _num, (_corpo, dati) in sorted(oggetti.items()):
        if not dati or b"BT" not in dati:
            continue
        for blocco in re.finditer(rb"BT(.*?)ET", dati, re.S):
            cmap = None
            for tok in _TOKEN.finditer(blocco.group(1)):
                if tok.group(1) is not None:
                    cmap = cmap_per_font.get(nome_font.get(tok.group(1), -1))
                    continue
                if tok.group(2) is not None:
                    h = re.sub(rb"\s+", b"", tok.group(2))
                    dati_str = bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
                else:
                    dati_str = _decodifica_stringa(tok.group(3))
                if cmap is None:
                    pezzi.append(dati_str.decode("cp1252", errors="replace"))
                else:
                    larghezza, mappa = cmap
                    pezzi.append("".join(mappa.get(int.from_bytes(dati_str[i:i + larghezza], "big"), "")
                                         for i in range(0, len(dati_str), larghezza)))
                pezzi.append(" ")
    return " ".join("".join(pezzi).split())


def contiene(testo: str, frase: str) -> bool:
    """La frase e' nel testo, ignorando gli spazi (l'estrazione puo' spezzare le parole in modo diverso)."""
    return "".join(frase.split()) in "".join(testo.split())


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------

def _sembra_produzione() -> list[str]:
    nomi = ("ENVIRONMENT", "APP_ENV", "ENV", "STIMA360_ENV", "RENDER_ENV", "DEPLOY_ENV")
    return [n for n in nomi if re.search(r"prod", os.getenv(n) or "", re.I)]


def gate_ambiente(g, args) -> None:
    _p2.environment_gate(g, args.expected_host)
    g.need(not _sembra_produzione(), "nessuna variabile d'ambiente dichiara «production»")
    g.stop_if_failed("ambiente non verificato, nessuna connessione aperta")


def gate_configurazione(g) -> dict:
    from appointment_reminders import policy, settings
    raw = os.getenv(policy.RECIPIENTS_ENV)
    elenco = sorted(policy.allowed_recipients_strict(raw) or [])
    g.need(raw is None or raw.strip() != policy.WILDCARD,
           "APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS non e' «*» (nessun cliente raggiungibile per sbaglio)")
    g.need("@example.invalid" not in "".join(elenco), "l'elenco dei destinatari non contiene indirizzi sintetici")
    g.need(bool(os.getenv("PUBLIC_BOOKING_IP_PEPPER")), "PUBLIC_BOOKING_IP_PEPPER configurata (prenotazione pubblica attiva)")
    print(f"INFO  conferme {'accese' if settings.confirmations_enabled() else 'spente'}; "
          f"destinatari ammessi: {len(elenco)} (indirizzi non stampati)", flush=True)
    return {"conferme": settings.confirmations_enabled(), "destinatari": len(elenco)}


def gate_database(g, cur, agency_id: int, actor: int, agent: int | None, *, agente_di_prova: bool = False) -> None:
    cur.execute("SELECT current_database() AS db, pg_is_in_recovery() AS rec")
    r = cur.fetchone()
    g.need(r["db"] == EXPECTED_DB and r["rec"] is False, "database TEST primario")
    for tabella in ("visit_sheets", "owner_visit_feedback_publications", "property_visits", "property_visit_booking_links",
                    "acquisitions", "owner_property_access"):
        cur.execute("SELECT to_regclass(%s) AS t", (tabella,))
        g.need(cur.fetchone()["t"] is not None, f"tabella {tabella} presente")
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'property_visits' AND column_name = 'outcome'")
    g.need(cur.fetchone() is not None, "property_visits.outcome presente")
    cur.execute("SELECT status FROM agencies WHERE id = %s", (agency_id,))
    ag = cur.fetchone()
    g.need(ag is not None and ag["status"] == "active", f"agenzia {agency_id} attiva")
    cur.execute("SELECT role, status FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agency_id, actor))
    att = cur.fetchone()
    g.need(att is not None and att["status"] == "active" and att["role"] in ("agency_owner", "agency_admin"),
           "l'attore e' owner/admin attivo dell'agenzia")
    if agente_di_prova:
        gate_agente_di_prova(g, cur, agency_id)
        return
    cur.execute("SELECT role, status FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agency_id, agent))
    agt = cur.fetchone()
    g.need(agt is not None and agt["status"] == "active", "l'agente e' attivo nell'agenzia")
    cur.execute("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s AND user_id = %s", (agency_id, agent))
    g.need(cur.fetchone()["n"] > 0, "l'agente ha un orario di lavoro (serve al collegamento di prenotazione); "
           "se nessun agente ne ha uno, rilanciare con --agente-di-prova")


def gate_agente_di_prova(g, cur, agency_id: int) -> None:
    """Le condizioni per predisporre un agente sintetico con le regole esistenti."""
    from operator_auth.designation import designated_super_admin_id
    from platform_admin import operators_repository
    designato = designated_super_admin_id()
    g.need(designato is not None, "STIMA360_SUPER_ADMIN_USER_ID configurato (solo il Super Admin crea operatori)")
    if designato is not None:
        cur.execute("SELECT is_platform_admin, status FROM operator_users WHERE id = %s", (designato,))
        sa = cur.fetchone()
        g.need(sa is not None and sa["is_platform_admin"] is True and sa["status"] == "active",
               "il Super Admin designato e' un amministratore di piattaforma attivo")
    g.need(operators_repository.elimination_mark_supported(cur),
           "l'eliminazione logica degli operatori e' disponibile (migrazione 113): la pulizia e' garantita")
    cur.execute("SELECT to_regclass('agent_working_hours') AS t")
    g.need(cur.fetchone()["t"] is not None, "tabella agent_working_hours presente")
    cur.execute("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s", (agency_id,))
    print(f"INFO  orari di lavoro oggi nell'agenzia: {cur.fetchone()['n']} righe (non verranno modificate)", flush=True)
    cur.execute("SELECT count(*) AS n FROM operator_users u JOIN agency_memberships m ON m.operator_user_id = u.id "
                "WHERE m.agency_id = %s AND u.email_normalized LIKE %s AND u.deleted_at IS NULL",
                (agency_id, f"{AGENT_EMAIL_PREFIX}%{AGENT_EMAIL_DOMAIN}"))
    residui = cur.fetchone()["n"]
    print(f"INFO  agenti di prova rimasti da collaudi precedenti: {residui}"
          f"{' (si chiudono con `cleanup`)' if residui else ''}", flush=True)
    print("INFO  con --agente-di-prova verra' creato UN account sintetico, senza password nota e senza accesso; "
          "l'agente reale non viene usato ne' modificato", flush=True)


def gate_ruoli(cur, agency_id: int, agent: int | None) -> dict:
    """Ruolo dell'agente e (se esistono) un altro agente e un'altra agenzia REALI per le prove di isolamento.
    Gli agenti di prova di collaudi precedenti non contano come «altro agente»."""
    ruolo = "agent"
    if agent is not None:
        cur.execute("SELECT role FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agency_id, agent))
        ruolo = cur.fetchone()["role"]
    cur.execute("SELECT m.operator_user_id AS id FROM agency_memberships m JOIN operator_users u ON u.id = m.operator_user_id "
                "WHERE m.agency_id = %s AND m.status = 'active' AND m.role = 'agent' AND u.status = 'active' "
                "AND m.operator_user_id <> %s AND u.email_normalized NOT LIKE %s ORDER BY m.operator_user_id LIMIT 1",
                (agency_id, agent or 0, f"{AGENT_EMAIL_PREFIX}%"))
    altro = cur.fetchone()
    cur.execute("SELECT id FROM agencies WHERE id <> %s ORDER BY id LIMIT 1", (agency_id,))
    altra = cur.fetchone()
    return {"ruolo_agente": ruolo, "altro_agente": altro["id"] if altro else None,
            "altra_agenzia": altra["id"] if altra else None}


# ---------------------------------------------------------------------------
# Accesso al database e client
# ---------------------------------------------------------------------------

def _q(sql: str, params=(), *, scrivi: bool = False):
    conn = _p2._connect(readonly=not scrivi)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        righe = cur.fetchall() if cur.description else []
        if scrivi:
            conn.commit()
        else:
            conn.rollback()
        return righe
    finally:
        conn.close()


def _uno(sql: str, params=()):
    righe = _q(sql, params)
    return righe[0] if righe else None


class Client:
    """Il client in-process: l'app vera, con la sola sessione sostituita."""

    def __init__(self, ctx_per_attore: dict):
        from fastapi.testclient import TestClient

        import main
        from operator_auth import dependencies as dep

        self._main = main
        self._dep = dep
        self._TestClient = TestClient
        self._contesti = ctx_per_attore

        def sessione(request: Request):
            ctx = self._contesti.get(request.headers.get(ATTORE_HEADER, ""))
            if ctx is None:
                return None
            return dep.AuthenticatedSession(context=ctx, agency_name=None,
                                            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))

        main.app.dependency_overrides[dep.optional_session] = sessione

    def attore(self, nome: str):
        return self._TestClient(self._main.app, base_url="https://testserver", headers={ATTORE_HEADER: nome})

    def anonimo(self):
        return self._TestClient(self._main.app, base_url="https://testserver")

    def proprietario(self, cookie: str):
        c = self._TestClient(self._main.app, base_url="https://testserver")
        c.cookies.set(OWNER_COOKIE, cookie)
        return c

    def chiudi(self) -> None:
        self._main.app.dependency_overrides.pop(self._dep.optional_session, None)


def contesti(agency_id: int, actor: int, agent: int, ruoli: dict) -> dict:
    ctx = {
        "owner": _p2.operatore(agency_id, actor, "agency_owner"),
        "agente": _p2.operatore(agency_id, agent, ruoli["ruolo_agente"]),
        # chi non e' l'agente della visita: un altro agente vero se c'e', altrimenti un identificativo che non esiste
        "altro_agente": _p2.operatore(agency_id, ruoli["altro_agente"] or 2_000_000_000, "agent"),
        # un'altra agenzia (vera se c'e', altrimenti inesistente): non deve vedere ne' scrivere nulla
        "altra_agenzia": _p2.operatore(ruoli["altra_agenzia"] or 2_000_000_000, 2_000_000_001, "agency_owner"),
    }
    from operator_auth.designation import designated_super_admin_id
    from operator_auth.context import OperatorContext
    super_id = designated_super_admin_id()
    if super_id is not None:                                     # solo per creare e chiudere l'agente di prova
        ctx["super"] = OperatorContext(user_id=super_id, agency_id=None, role=None, is_platform_admin=True,
                                       session_id=None, auth_channel="session")
    return ctx


# ---------------------------------------------------------------------------
# Preparazione dei dati sintetici
# ---------------------------------------------------------------------------

def _corpo_slot(giorni: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=giorni)


def sposta_nel_passato(tabella_appuntamento_id: int, run_id: str, *, durata_minuti: int, partendo_da_giorni: int) -> datetime:
    """Sposta nel passato l'orario di UN appuntamento creato da questo collaudo (verificato dal codice
    dell'immobile). Prova giorni diversi finche' l'Agenda non lo accetta (nessun conflitto con altro)."""
    riga = _uno("SELECT a.id, p.code FROM appointments a JOIN properties p ON p.id = a.property_id AND p.agency_id = a.agency_id "
                "WHERE a.id = %s", (tabella_appuntamento_id,))
    if riga is None or not str(riga["code"]).startswith(f"{CODE_PREFIX}{run_id}"):
        raise RuntimeError("l'appuntamento da spostare non appartiene a questo collaudo")
    ultimo = None
    for giorni in range(partendo_da_giorni, partendo_da_giorni + 40):
        passato = (datetime.now(ROMA) - timedelta(days=giorni)).replace(hour=10, minute=0, second=0, microsecond=0)
        passato = passato.astimezone(timezone.utc)
        conn = _p2._connect(readonly=False)
        try:
            cur = conn.cursor()
            cur.execute("UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
                        (passato, passato + timedelta(minutes=durata_minuti), tabella_appuntamento_id))
            cur.execute("UPDATE property_visits SET scheduled_at = %s WHERE appointment_id = %s", (passato, tabella_appuntamento_id))
            conn.commit()
            return passato
        except Exception as exc:                                   # conflitto o vincolo: si prova il giorno dopo
            conn.rollback()
            ultimo = exc
        finally:
            conn.close()
    raise RuntimeError(f"nessun orario passato libero: {type(ultimo).__name__}")


def prepara(R: Registro, cl: Client, ids: dict, run_id: str) -> dict | None:
    """Immobile con incarico (percorso ordinario), proprietario del portale, collegamento di prenotazione."""
    owner = cl.attore("owner")
    agente = ids["agent"]
    r = owner.post("/api/property/properties", json={
        "code": f"{CODE_PREFIX}{run_id}", "title": f"[{NAME_PREFIX} {run_id}] Trilocale di prova",
        "property_type": "apartment", "commercial_status": "evaluation", "city": "Comune di prova",
        "address": "Via Collaudo Visite", "civic_number": "S77", "asking_price": 190000,
        "minimum_price": int(PREZZO_MINIMO), "internal_notes": NOTA_IMMOBILE, "assigned_agent_id": agente,
        "source": f"collaudo_visite:{run_id}"})
    if not R.controllo("0", "immobile sintetico creato", r.status_code == 201, f"{r.status_code} {r.text[:200]}"):
        return None
    pid = r.json()["id"] if "id" in r.json() else r.json().get("property", {}).get("id")
    venditore = owner.post("/api/core/contacts", json={
        "first_name": NAME_PREFIX, "last_name": f"{run_id} Venditore", "source": f"collaudo_visite:{run_id}"})
    if not R.controllo("0", "proprietario venditore sintetico creato", venditore.status_code == 201, f"{venditore.status_code} {venditore.text[:200]}"):
        return None
    cid_venditore = venditore.json()["id"]
    r = owner.post(f"/api/property/properties/{pid}/contacts", json={"contact_id": cid_venditore, "role": "owner", "is_primary": True})
    R.controllo("0", "venditore collegato all'immobile come proprietario", r.status_code == 201, f"{r.status_code}")

    # incarico dal percorso ordinario: acquisizione -> sopralluogo (Agenda) -> incarico
    acq = None
    for giorni in range(20, 60):
        inizio = (datetime.now(ROMA) + timedelta(days=giorni)).replace(hour=10, minute=0, second=0, microsecond=0)
        r = owner.post("/api/acquisitions", json={
            "property_id": pid, "owner_contact_id": cid_venditore, "asking_price": "190000.00", "sale_timing": "within_3_months",
            "source": "referral", "appointment": {"start_at": inizio.isoformat(), "client_request_id": str(uuid.uuid4()),
                                                  "assigned_user_id": agente}})
        if r.status_code == 201:
            acq = r.json()
            break
        if r.status_code not in (409, 422):
            break
    if not R.controllo("0", "acquisizione e sopralluogo creati dall'Agenda", acq is not None, f"{r.status_code} {r.text[:200]}"):
        return None
    try:
        sposta_nel_passato(acq["appointment_id"], run_id, durata_minuti=60, partendo_da_giorni=2)
    except Exception as exc:
        R.controllo("0", "sopralluogo spostato nel passato (riga sintetica)", False, f"{type(exc).__name__}: {exc}")
        return None
    versione = owner.get(f"/api/appointments/{acq['appointment_id']}").json()["appointment"]["version"]
    r = owner.post(f"/api/appointments/{acq['appointment_id']}/complete", json={"version": versione})
    R.controllo("0", "sopralluogo segnato «effettuato»", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    acq = owner.get(f"/api/acquisitions/{acq['id']}").json()
    r = owner.post(f"/api/acquisitions/{acq['id']}/mandate", json={
        "version": acq["version"], "mandate_type": "Esclusiva", "mandate_start": (date.today() - timedelta(days=10)).isoformat(),
        "mandate_end": (date.today() + timedelta(days=170)).isoformat()})
    if not R.controllo("0", "incarico generato dal percorso ordinario (nessun trigger disattivato)", r.status_code == 200,
                       f"{r.status_code} {r.text[:200]}"):
        return None

    # il proprietario del PORTALE (sintetico) e la sua sessione di due ore
    cp = _q("INSERT INTO contacts (agency_id, display_name, first_name, last_name, source) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (ids["agency"], f"{NAME_PREFIX} {run_id} Proprietario", NAME_PREFIX, f"{run_id} Proprietario", f"collaudo_visite:{run_id}"),
            scrivi=True)[0]["id"]
    account = _q("INSERT INTO owner_accounts (contact_id, status) VALUES (%s, 'active') RETURNING id", (cp,), scrivi=True)[0]["id"]
    _q("INSERT INTO owner_property_access (owner_account_id, property_id, access_role, access_status, is_primary) "
       "VALUES (%s, %s, 'owner', 'active', TRUE)", (account, pid), scrivi=True)
    cookie = secrets.token_urlsafe(32)
    _q("INSERT INTO owner_sessions (owner_account_id, session_token_hash, expires_at) VALUES (%s, %s, NOW() + interval '2 hours')",
       (account, hashlib.sha256(cookie.encode()).hexdigest()), scrivi=True)
    R.controllo("0", "proprietario del portale sintetico con accesso al solo immobile di prova", True)

    r = owner.post("/api/visit-booking-links", json={
        "property_id": pid, "assigned_user_id": agente, "duration_minutes": 45, "step_minutes": 30, "travel_minutes": 15,
        "min_notice_minutes": 120, "horizon_days": 14, "owner_authorization": "preauthorized",
        "owner_authorization_note": f"Assenso sintetico del collaudo {run_id}"})
    if not R.controllo("0", "collegamento di prenotazione creato", r.status_code == 201, f"{r.status_code} {r.text[:200]}"):
        return None
    return {"pid": pid, "link_id": r.json()["id"], "token": r.json()["url"].rsplit("/", 1)[1], "cookie": cookie,
            "account": account, "owner_contact": cp, "seller": cid_venditore}


def telefono_libero(agency_id: int) -> str:
    for _ in range(50):
        numero = "3200" + "".join(secrets.choice("0123456789") for _ in range(6))
        if _uno("SELECT 1 AS x FROM contacts WHERE agency_id = %s AND (phone_normalized LIKE %s OR phone LIKE %s) LIMIT 1",
                (agency_id, f"%{numero}", f"%{numero}")) is None:
            return numero
    raise RuntimeError("nessun numero sintetico libero")


# ---------------------------------------------------------------------------
# Agente di prova e orari di disponibilita' (regole applicative esistenti, mai SQL)
# ---------------------------------------------------------------------------

def impronta_orari(agency_id: int, escludi_utente: int | None = None) -> tuple[int, str]:
    """(righe, impronta) degli orari di lavoro dell'agenzia, esclusi quelli dell'agente di prova."""
    riga = _uno("SELECT count(*) AS n, COALESCE(md5(string_agg(id::text || ':' || user_id::text || ':' || day_of_week::text || ':' || "
                "start_minute::text || ':' || end_minute::text, ',' ORDER BY id)), '') AS h FROM agent_working_hours "
                "WHERE agency_id = %s AND user_id <> %s", (agency_id, escludi_utente or 0))
    return riga["n"], riga["h"]


def _esito_sicuro(r) -> str:
    """Stato e, per un rifiuto di validazione, SOLO campo e messaggio: mai il corpo inviato (c'e' la password)."""
    try:
        det = r.json().get("detail")
    except Exception:
        return str(r.status_code)
    if isinstance(det, list):
        return f"{r.status_code} " + "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in det)
    return f"{r.status_code} {str(det)[:160]}"


def _orari_corpo() -> dict:
    return {"slots": [{"day_of_week": d, "start_minute": ORARIO_PROVA["inizio"], "end_minute": ORARIO_PROVA["fine"]}
                      for d in range(1, 8)]}


def crea_agente_di_prova(R: Registro, cl: Client, ids: dict, run_id: str, contesti_vivi: dict) -> int | None:
    """Un agente sintetico con i suoi orari; ritorna il suo id (o None se qualcosa non va)."""
    agency = ids["agency"]
    if "super" not in contesti_vivi:
        R.controllo("A", "Super Admin designato disponibile", False, "STIMA360_SUPER_ADMIN_USER_ID non configurato")
        return None
    super_, owner = cl.attore("super"), cl.attore("owner")
    righe_prima, hash_prima = impronta_orari(agency)
    email = f"{AGENT_EMAIL_PREFIX}{run_id.lower()}{AGENT_EMAIL_DOMAIN}"
    # password casuale, mai stampata, mai salvata: l'account non e' utilizzabile da nessuno
    r = super_.post(f"/api/platform/agencies/{agency}/operators", json={
        "email": email, "password": secrets.token_urlsafe(24), "first_name": NAME_PREFIX, "last_name": f"{run_id} Agente",
        "role": "agent", "status": "active"})
    corpo = r.json() if r.status_code == 201 else {}
    uid = (corpo.get("operator") or {}).get("id")
    R.controllo("A", "agente di prova creato dalla rotta di piattaforma (Super Admin), con membership «agent» attiva",
                r.status_code == 201 and uid is not None and (corpo.get("membership") or {}).get("role") == "agent"
                and (corpo.get("membership") or {}).get("status") == "active", _esito_sicuro(r))
    if uid is None:
        return None
    contesti_vivi["agente"] = _p2.operatore(agency, uid, "agent")
    ids["agent"] = uid
    ids["agente_di_prova"] = uid
    R.controllo("A", "nome e e-mail dell'agente sono marcati come collaudo (@example.invalid)",
                (_uno("SELECT email_normalized AS e FROM operator_users WHERE id = %s", (uid,)) or {}).get("e") == email)
    ora = _uno("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s AND user_id = %s", (agency, uid))["n"]
    R.controllo("A", "l'agente nasce senza orari (nulla ereditato)", ora == 0, f"{ora}")

    # permessi PRIMA di scrivere gli orari: chi non puo' non deve riuscire
    estraneo_a = cl.attore("altro_agente")
    estranea = cl.attore("altra_agenzia")
    agente = cl.attore("agente")
    percorso_ore = f"/api/appointments/agents/{uid}/working-hours"
    no = {
        "altro agente": estraneo_a.put(percorso_ore, json=_orari_corpo()).status_code,
        "altra agenzia": estranea.put(percorso_ore, json=_orari_corpo()).status_code,
    }
    scritti = _uno("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s AND user_id = %s", (agency, uid))["n"]
    R.controllo("A", "permessi: un altro agente o un'altra agenzia NON possono scrivere gli orari dell'agente di prova "
                "(rifiutati, nessuna riga scritta)", all(c in (403, 404, 422) for c in no.values()) and scritti == 0,
                f"{no}, {scritti} righe")
    sa = agente.put(f"/api/appointments/agents/{ids['actor']}/working-hours", json=_orari_corpo()).status_code
    altrui = _uno("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s AND user_id = %s", (agency, ids["actor"]))["n"]
    R.controllo("A", "permessi: l'agente di prova non puo' scrivere gli orari di un altro operatore (rifiutato, nulla scritto)",
                sa in (403, 404) and altrui == 0, f"{sa}, {altrui} righe")
    nuovi = agente.post(f"/api/platform/agencies/{agency}/operators", json={
        "email": f"{AGENT_EMAIL_PREFIX}x{run_id.lower()}{AGENT_EMAIL_DOMAIN}", "password": secrets.token_urlsafe(24),
        "role": "agent", "status": "active"}).status_code
    nuovi_o = owner.post(f"/api/platform/agencies/{agency}/operators", json={
        "email": f"{AGENT_EMAIL_PREFIX}y{run_id.lower()}{AGENT_EMAIL_DOMAIN}", "password": secrets.token_urlsafe(24),
        "role": "agent", "status": "active"}).status_code
    R.controllo("A", "permessi: ne' l'agente ne' l'owner dell'agenzia possono creare operatori (solo il Super Admin)",
                nuovi in (401, 403) and nuovi_o in (401, 403), f"{nuovi}/{nuovi_o}")
    creati_per_errore = _uno("SELECT count(*) AS n FROM operator_users WHERE email_normalized IN (%s, %s)",
                             (f"{AGENT_EMAIL_PREFIX}x{run_id.lower()}{AGENT_EMAIL_DOMAIN}",
                              f"{AGENT_EMAIL_PREFIX}y{run_id.lower()}{AGENT_EMAIL_DOMAIN}"))["n"]
    R.controllo("A", "i tentativi non autorizzati non hanno creato alcun account", creati_per_errore == 0, f"{creati_per_errore}")

    # gli orari: li scrive l'owner dell'agenzia con la rotta esistente
    w = owner.put(percorso_ore, json=_orari_corpo())
    R.controllo("A", "orari di disponibilita' scritti dall'owner con la rotta esistente (7 giorni, 09:00-19:00)",
                w.status_code == 200 and len(w.json().get("items", [])) == 7, f"{_esito_sicuro(w)}")
    letti = agente.get(percorso_ore)
    R.controllo("A", "l'agente di prova legge i propri orari", letti.status_code == 200 and len(letti.json().get("items", [])) == 7,
                f"{letti.status_code}")
    sovrapposto = owner.put(percorso_ore, json={"slots": [
        {"day_of_week": 1, "start_minute": 540, "end_minute": 800}, {"day_of_week": 1, "start_minute": 700, "end_minute": 900}]})
    R.controllo("A", "le regole esistenti rifiutano fasce sovrapposte (e gli orari restano quelli giusti)",
                sovrapposto.status_code in (400, 409, 422)
                and _uno("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s AND user_id = %s", (agency, uid))["n"] == 7,
                f"{sovrapposto.status_code}")
    righe_dopo, hash_dopo = impronta_orari(agency, uid)
    R.controllo("A", "gli orari di lavoro degli altri agenti non sono stati toccati", (righe_dopo, hash_dopo) == (righe_prima, hash_prima),
                f"{righe_prima}->{righe_dopo}")
    ids["_impronta_orari"] = (righe_prima, hash_prima)
    return uid


def pulizia_agente(R: Registro | None, cl: Client, ids: dict, run_id: str | None) -> None:
    """Chiude gli agenti di prova (di questo collaudo, o tutti): orari svuotati, membership revocata,
    account disabilitato ed eliminato in modo logico. Nessuna riga cancellata."""
    agency = ids["agency"]
    filtro = f"{AGENT_EMAIL_PREFIX}{run_id.lower()}{AGENT_EMAIL_DOMAIN}" if run_id else f"{AGENT_EMAIL_PREFIX}%{AGENT_EMAIL_DOMAIN}"
    agenti = _q("SELECT u.id FROM operator_users u JOIN agency_memberships m ON m.operator_user_id = u.id AND m.agency_id = %s "
                "WHERE u.email_normalized LIKE %s AND u.deleted_at IS NULL ORDER BY u.id", (agency, filtro))
    if not agenti:
        print(f"INFO  pulizia agenti di prova: nessuno da chiudere ({filtro.replace('%', '*')})", flush=True)
        return

    def controllo(passo, testo, ok, det=""):
        if R is not None:
            R.controllo(passo, testo, ok, det)
        else:
            print(f"{OK if ok else KO:4} {passo:<3} {testo}{'' if ok else '  [' + det + ']'}", flush=True)

    super_, owner = cl.attore("super"), cl.attore("owner")
    for ag in agenti:
        uid = ag["id"]
        svuota = owner.put(f"/api/appointments/agents/{uid}/working-hours", json={"slots": []})
        residui = _uno("SELECT count(*) AS n FROM agent_working_hours WHERE agency_id = %s AND user_id = %s", (agency, uid))["n"]
        controllo("Z", "orari dell'agente di prova rimossi", residui == 0, f"{_esito_sicuro(svuota)}; {residui} righe residue")
        m = super_.patch(f"/api/platform/agencies/{agency}/operators/{uid}/membership", json={"status": "revoked"})
        controllo("Z", "membership dell'agente di prova revocata", m.status_code == 200, _esito_sicuro(m))
        d = super_.patch(f"/api/platform/operators/{uid}", json={"status": "disabled"})
        controllo("Z", "account dell'agente di prova disabilitato", d.status_code == 200, _esito_sicuro(d))
        e = super_.post(f"/api/platform/operators/{uid}/delete")
        controllo("Z", "account dell'agente di prova eliminato in modo logico", e.status_code == 200, _esito_sicuro(e))
        stato = _uno("SELECT u.status, u.deleted_at IS NOT NULL AS eliminato, "
                     "(SELECT count(*) FROM operator_sessions s WHERE s.operator_user_id = u.id AND s.revoked_at IS NULL "
                     " AND s.expires_at > NOW()) AS sessioni FROM operator_users u WHERE u.id = %s", (uid,))
        controllo("Z", "agente di prova: disabilitato, eliminato, nessuna sessione attiva",
                  stato is not None and stato["status"] == "disabled" and stato["eliminato"] and stato["sessioni"] == 0, f"{stato}")
    if run_id:
        elenco = [a["id"] for a in agenti]
        altrui = _uno("SELECT count(*) AS n FROM appointments a JOIN properties p ON p.id = a.property_id AND p.agency_id = a.agency_id "
                      "WHERE a.agency_id = %s AND p.code LIKE %s AND NOT (a.assigned_user_id = ANY(%s))",
                      (agency, f"{CODE_PREFIX}{run_id}%", elenco))["n"]
        controllo("Z", "nessun appuntamento del collaudo e' assegnato a un agente reale", altrui == 0, f"{altrui}")
    if "_impronta_orari" in ids:
        prima = ids["_impronta_orari"]
        dopo = impronta_orari(agency)
        controllo("Z", "alla fine gli orari di lavoro degli altri agenti sono identici a prima", dopo == prima, f"{prima[0]}->{dopo[0]}")


# ---------------------------------------------------------------------------
# Il collaudo
# ---------------------------------------------------------------------------

def percorso(R: Registro, cl: Client, ids: dict, run_id: str, dati: dict) -> None:
    pid, token = dati["pid"], dati["token"]
    agente, agency = cl.attore("agente"), ids["agency"]
    owner, altro, estranea = cl.attore("owner"), cl.attore("altro_agente"), cl.attore("altra_agenzia")
    pubblico, portale = cl.anonimo(), cl.proprietario(dati["cookie"])
    nome = f"{NAME_PREFIX} {run_id} Acquirente"
    telefono = telefono_libero(agency)
    email = f"collaudo.visite.{run_id.lower()}@example.invalid"

    prima_dei_messaggi = _uno("SELECT count(*) AS n FROM communication_messages WHERE agency_id = %s", (agency,))["n"]

    # ---- 1. PRENOTAZIONE -> AGENDA -------------------------------------------------------------
    s = pubblico.get(f"{PUB}/{token}/slots", params={
        "date_from": _corpo_slot(1).isoformat(), "date_to": _corpo_slot(3).isoformat()})
    slots = s.json().get("slots", []) if s.status_code == 200 else []
    if not R.controllo("1", "il collegamento propone orari liberi", bool(slots), f"{s.status_code}"):
        return
    start = slots[0]["start_at"]
    chiave = str(uuid.uuid4())
    corpo = {"name": nome, "phone": telefono, "email": email, "start_at": start, "key": chiave, "privacy_accepted": True}
    b1 = pubblico.post(f"{PUB}/{token}/book", json=corpo)
    b2 = pubblico.post(f"{PUB}/{token}/book", json=corpo)                # doppio clic
    R.controllo("1", "prenotazione accettata, anche al secondo invio (doppio clic)",
                b1.status_code == 200 and b2.status_code == 200, f"{b1.status_code}/{b2.status_code} {b1.text[:150]}")
    righe = _q("SELECT id, appointment_type, status, property_id, contact_id, assigned_user_id, source FROM appointments "
               "WHERE agency_id = %s AND property_id = %s AND start_at = %s", (agency, pid, start))
    if not R.controllo("1", "UN solo appuntamento in Agenda per la prenotazione", len(righe) == 1, f"{len(righe)} righe"):
        return
    a = righe[0]
    aid, contatto = a["id"], a["contact_id"]
    R.controllo("1", "tipo visita acquirente, stato «fissato», fonte collegamento di prenotazione",
                (a["appointment_type"], a["status"], a["source"]) == ("buyer_visit", "scheduled", "booking_link"),
                f"{a['appointment_type']}/{a['status']}/{a['source']}")
    R.controllo("1", "immobile e agente corretti", a["property_id"] == pid and a["assigned_user_id"] == ids["agent"])
    cont = _uno("SELECT display_name, first_name, last_name FROM contacts WHERE id = %s AND agency_id = %s", (contatto, agency))
    sintetico = cont is not None and NAME_PREFIX in " ".join(str(v) for v in cont.values())
    R.controllo("1", "il cliente e' il contatto SINTETICO (nessun contatto reale agganciato)", sintetico)
    if not sintetico:
        return
    vis = _q("SELECT id FROM property_visits WHERE appointment_id = %s AND property_id = %s AND contact_id = %s",
             (aid, pid, contatto))
    R.controllo("1", "visita collegata ad appuntamento, immobile e contatto (property_visits)", len(vis) == 1)
    vid = vis[0]["id"] if vis else None
    scheda = agente.get(f"/api/appointments/{aid}")
    sj = scheda.json() if scheda.status_code == 200 else {}
    R.controllo("1", "la scheda Agenda mostra immobile, cliente e agente giusti",
                scheda.status_code == 200 and sj.get("property", {}).get("id") == pid
                and sj.get("contact", {}).get("id") == contatto and sj.get("agent", {}).get("id") == ids["agent"])
    giorno = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ROMA).date()
    elenco = agente.get("/api/appointments", params={"from": f"{giorno}T00:00:00+02:00", "to": f"{giorno + timedelta(days=1)}T23:59:59+02:00",
                                                     "property_id": pid})
    ids_elenco = [x.get("id") for x in (elenco.json().get("items", []) if elenco.status_code == 200 else [])]
    R.controllo("1", "l'appuntamento compare nell'elenco dell'Agenda del giorno", aid in ids_elenco, f"{elenco.status_code}")
    mess = _q("SELECT status FROM communication_messages WHERE agency_id = %s AND (contact_id = %s OR (metadata->>'appointment_id')::bigint = %s)",
              (agency, contatto, aid))
    R.controllo("1", "nessun messaggio accodato o inviato al cliente sintetico", len(mess) == 0, f"{[m['status'] for m in mess]}")
    if vid is None:
        return

    # ---- 2. ESITO e FEEDBACK dalla scheda Agenda -----------------------------------------------
    try:
        sposta_nel_passato(aid, run_id, durata_minuti=45, partendo_da_giorni=3)
        spostata = True
    except Exception as exc:
        spostata = False
        R.controllo("2", "visita sintetica spostata nel passato", False, f"{type(exc).__name__}: {exc}")
    if not spostata:
        return
    stato = _uno("SELECT status FROM appointments WHERE id = %s", (aid,))["status"]
    R.controllo("2", "passato l'orario la visita NON si chiude da sola", stato == "scheduled", stato)
    r = agente.put(f"/api/appointments/{aid}/visit-outcome", json={"feedback": "troppo presto"})
    R.controllo("2", "l'esito non si registra prima di aver segnato la visita", r.status_code == 409, f"{r.status_code}")
    sj = agente.get(f"/api/appointments/{aid}").json()
    r = agente.post(f"/api/appointments/{aid}/complete", json={"version": sj["appointment"]["version"],
                                                                "outcome_note": f"Nota interna. {SEGRETO}"})
    R.controllo("2", "«Visita effettuata» registrata dall'agente assegnato", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    r = agente.put(f"/api/appointments/{aid}/visit-outcome", json={"interest": "medium", "feedback": f"Nota interna. {SEGRETO}"})
    R.controllo("2", "interesse e feedback registrati dalla scheda Agenda", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    riga = _uno("SELECT status, outcome, feedback FROM property_visits WHERE id = %s", (vid,))
    R.controllo("2", "property_visits: completata, interesse «Interessato», feedback salvato",
                riga is not None and (riga["status"], riga["outcome"]) == ("completed", "Interessato")
                and SEGRETO in (riga["feedback"] or ""), f"{dict(riga) if riga else None}")
    sj = agente.get(f"/api/appointments/{aid}").json()
    R.controllo("2", "la scheda Agenda rilegge esito e feedback", (sj.get("visit") or {}).get("outcome") == "Interessato"
                and SEGRETO in ((sj.get("visit") or {}).get("feedback") or ""))

    # ---- 3. SCHEDE IMMOBILE e CONTATTO ---------------------------------------------------------
    p = owner.get(f"/api/property/properties/{pid}")
    visite = p.json().get("visits", []) if p.status_code == 200 else []
    R.controllo("3", "scheda immobile: visita «completed» con esito «Interessato»",
                [(v["status"], v["outcome"]) for v in visite if v.get("appointment_id") == aid] == [("completed", "Interessato")],
                f"{p.status_code}")
    c = owner.get(f"/api/crm/contacts/{contatto}/360")
    mie = [v for v in (c.json().get("visits", []) if c.status_code == 200 else []) if v.get("property_id") == pid]
    R.controllo("3", "scheda contatto: la stessa visita con lo stesso esito",
                [(v["status"], v["outcome"]) for v in mie] == [("completed", "Interessato")], f"{c.status_code}")

    # ---- 4. FOGLIO VISITA PDF -------------------------------------------------------------------
    info = agente.get(f"/api/visit-sheets/appointments/{aid}")
    R.controllo("4", "il foglio si puo' generare dall'appuntamento", info.status_code == 200 and info.json().get("can_generate") is True,
                f"{info.status_code}")
    g1 = agente.post(f"/api/visit-sheets/appointments/{aid}")
    foglio = g1.json() if g1.status_code in (200, 201) else {}
    sheet = foglio.get("sheet") or {}
    R.controllo("4", "foglio generato (versione 1, visita svolta)", foglio.get("created") is True and sheet.get("visit_state") == "completed",
                f"{g1.status_code} {g1.text[:150]}")
    R.controllo("4", "stato firma «Da firmare», nessuna firma simulata",
                sheet.get("signature_label") == "Da firmare" and sheet.get("signed") is False, f"{sheet.get('signature_label')}")
    pdf = agente.get(f"/api/visit-sheets/{sheet.get('id')}/pdf").content if sheet.get("id") else b""
    R.controllo("4", "il PDF e' un PDF vero e la sua impronta SHA-256 coincide con quella archiviata",
                pdf.startswith(b"%PDF") and hashlib.sha256(pdf).hexdigest() == sheet.get("sha256"), f"{len(pdf)} byte")
    testo = testo_pdf(pdf) if pdf else ""
    atteso = [nome, "Via Collaudo Visite", "Stato della firma: Da firmare", "Visita svolta", telefono]
    mancanti = [t for t in atteso if not contiene(testo, t)]
    R.controllo("4", "PDF: cliente, immobile, data, stato visita, spazio firma e «Da firmare»", not mancanti, f"mancano: {mancanti}")
    riga_ag = _uno("SELECT COALESCE(NULLIF(btrim(settings->'report_profile'->>'display_name'), ''), name) AS n FROM agencies WHERE id = %s",
                   (agency,))
    nome_agenzia = riga_ag["n"] if riga_ag else None
    R.controllo("4", "PDF: intestazione con i dati dell'agenzia", bool(nome_agenzia) and contiene(testo, nome_agenzia),
                "nome agenzia non trovato nel profilo" if not nome_agenzia else "")
    nome_agente = (_uno("SELECT btrim(concat_ws(' ', first_name, last_name)) AS n FROM operator_users WHERE id = %s", (ids["agent"],)) or {}).get("n")
    R.controllo("4", "PDF: agente assegnato", bool(nome_agente) and contiene(testo, nome_agente), "nome agente non trovato" if not nome_agente else "")
    R.controllo("4", "PDF: nessun dato riservato (prezzo minimo, nota interna, feedback)",
                not any(contiene(testo, x) for x in (PREZZO_MINIMO, NOTA_IMMOBILE, SEGRETO)))
    g2 = agente.post(f"/api/visit-sheets/appointments/{aid}")
    R.controllo("4", "secondo invio: nessun duplicato (stesso foglio)", (g2.json() if g2.status_code in (200, 201) else {}).get("created") is False)
    arch = _q("SELECT appointment_id, property_id, length(pdf_bytes) AS n FROM visit_sheets WHERE id = %s", (sheet.get("id"),)) \
        if sheet.get("id") else []
    R.controllo("4", "ARCHIVIATO e collegato all'appuntamento e all'immobile (visit_sheets)",
                len(arch) == 1 and (arch[0]["appointment_id"], arch[0]["property_id"]) == (aid, pid) and arch[0]["n"] == len(pdf))
    print(f"INFO  PDF foglio visita: {len(pdf)} byte, sha256 {hashlib.sha256(pdf).hexdigest()}"
          "", flush=True)

    # ---- 5. AGGIORNAMENTO AL PROPRIETARIO -> PORTALE --------------------------------------------
    st = agente.get(f"/api/owner-reports/visits/{aid}")
    sj = st.json() if st.status_code == 200 else {}
    R.controllo("5", "l'aggiornamento si puo' preparare e propone un testo", sj.get("can_prepare") is True and "interesse" in
                ((sj.get("suggestion") or {}).get("public_summary") or ""), f"{st.status_code}")
    sal = agente.put(f"/api/owner-reports/visits/{aid}", json={"category": "general", "sentiment": "positive",
                                                               "public_summary": TESTO_PUBBLICO})
    bozza = (sal.json() if sal.status_code == 200 else {}).get("current") or {}
    R.controllo("5", "bozza salvata (stato «draft»)", bozza.get("status") == "draft", f"{sal.status_code} {sal.text[:150]}")
    vista = portale.get(f"{OWNER_PORTAL}/{pid}")
    R.controllo("5", "la bozza NON e' visibile al proprietario", vista.status_code == 200 and vista.json().get("visit_feedback") == [],
                f"{vista.status_code}")
    sg = agente.post(f"/api/owner-reports/visits/{aid}/approve", json={"publication_id": bozza.get("id", 0)})
    R.controllo("5", "senza approvazione esplicita non si pubblica", sg.status_code == 422, f"{sg.status_code}")
    ap = agente.post(f"/api/owner-reports/visits/{aid}/approve", json={"publication_id": bozza.get("id", 0), "confirm": True})
    R.controllo("5", "approvazione esplicita dell'agente: pubblicato", ap.status_code == 200, f"{ap.status_code} {ap.text[:150]}")
    vista = portale.get(f"{OWNER_PORTAL}/{pid}")
    vf = vista.json().get("visit_feedback", []) if vista.status_code == 200 else []
    R.controllo("5", "il PORTALE del proprietario sintetico mostra il testo approvato",
                [(v["public_summary"], v["category_code"], v["sentiment"]) for v in vf] == [(TESTO_PUBBLICO, "general", "positive")])
    periodo = {"date_from": (date.today() - timedelta(days=90)).isoformat(), "date_to": date.today().isoformat()}
    rap = portale.get(f"{OWNER_PORTAL}/{pid}/report", params=periodo)
    rj = rap.json() if rap.status_code == 200 else {}
    R.controllo("5", "report del portale: 1 visita effettuata, 0 non svolte, 1 riscontro approvato",
                (rj.get("visits") or {}).get("completed") == 1 and ((rj.get("visits") or {}).get("not_held") or {}).get("total") == 0
                and (rj.get("visit_feedback") or {}).get("total") == 1, f"{rap.status_code}")
    rpdf = portale.get(f"{OWNER_PORTAL}/{pid}/report.pdf", params=periodo)
    rtesto = testo_pdf(rpdf.content) if rpdf.status_code == 200 else ""
    R.controllo("5", "report PDF del proprietario: PDF vero con il testo approvato",
                rpdf.status_code == 200 and rpdf.content.startswith(b"%PDF") and contiene(rtesto, "Prossimo passo") and contiene(rtesto, TESTO_PUBBLICO), f"{rpdf.status_code}")
    print(f"INFO  PDF report proprietario: {len(rpdf.content)} byte, sha256 {hashlib.sha256(rpdf.content).hexdigest()}", flush=True)

    # ---- 6. PRIVACY, PERMESSI, DUPLICATI, RITIRO ------------------------------------------------
    esposto = vista.text + rap.text + rtesto
    riservato = [x for x in (nome, telefono, email, SEGRETO, "Nota interna", PREZZO_MINIMO, NOTA_IMMOBILE, "Interessato")
                 if x in esposto]
    R.controllo("6", "al proprietario non arriva nessun dato dell'acquirente ne' alcuna nota interna", not riservato,
                f"esposti: {len(riservato)} elementi")
    chk = agente.post(f"/api/owner-reports/visits/{aid}/check", json={"public_summary": f"Il signor X chiede di essere richiamato al {telefono}."})
    prob = (chk.json() if chk.status_code == 200 else {}).get("problems") or (chk.json() if chk.status_code == 200 else {}).get("issues") or []
    ts = agente.put(f"/api/owner-reports/visits/{aid}", json={"category": "general", "sentiment": "positive",
                                                              "public_summary": f"Richiamare al {telefono}."})
    R.controllo("6", "un testo con un numero di telefono viene bloccato (non salvato)", ts.status_code in (400, 409, 422),
                f"check {chk.status_code} {len(prob)} problemi / put {ts.status_code}")
    corrente = _uno("SELECT count(*) AS n FROM owner_visit_feedback_publications WHERE property_visit_id = %s AND status = 'published'", (vid,))
    R.controllo("6", "dopo la prova di privacy resta pubblicato SOLO il testo approvato", corrente["n"] == 1, f"{corrente['n']}")
    chiusi = {}
    for chi, c in (("altro agente", altro), ("altra agenzia", estranea)):
        esiti = [
            c.get(f"/api/appointments/{aid}").status_code,
            c.put(f"/api/appointments/{aid}/visit-outcome", json={"feedback": "x"}).status_code,
            c.get(f"/api/visit-sheets/appointments/{aid}").status_code,
            c.post(f"/api/visit-sheets/appointments/{aid}").status_code,
            c.get(f"/api/visit-sheets/{sheet.get('id')}/pdf").status_code,
            c.get(f"/api/owner-reports/visits/{aid}").status_code,
            c.put(f"/api/owner-reports/visits/{aid}", json={"public_summary": TESTO_PUBBLICO}).status_code,
            c.post(f"/api/owner-reports/visits/{aid}/withdraw", json={"publication_id": bozza.get("id", 0)}).status_code,
        ]
        chiusi[chi] = esiti
        R.controllo("6", f"permessi: {chi} non vede e non scrive nulla (404/403 su 8 operazioni)",
                    all(e in (403, 404) for e in esiti), f"{esiti}")
    R.controllo("6", "portale: un altro immobile risponde in modo neutro (404)", portale.get(f"{OWNER_PORTAL}/{pid + 100000}").status_code == 404)
    senza = cl.anonimo().get(f"{OWNER_PORTAL}/{pid}")
    R.controllo("6", "il portale senza sessione non mostra nulla (risposta neutra)",
                senza.status_code in (401, 403, 404) and "visit_feedback" not in senza.text, f"{senza.status_code}")
    conteggi = {
        "appuntamenti visita": _uno("SELECT count(*) AS n FROM appointments WHERE property_id = %s AND appointment_type = 'buyer_visit'", (pid,))["n"],
        "property_visits": _uno("SELECT count(*) AS n FROM property_visits WHERE property_id = %s", (pid,))["n"],
        "prenotazioni": _uno("SELECT count(*) AS n FROM property_visit_bookings WHERE appointment_id = %s", (aid,))["n"],
        "fogli": _uno("SELECT count(*) AS n FROM visit_sheets WHERE appointment_id = %s", (aid,))["n"],
        "pubblicazioni": _uno("SELECT count(*) AS n FROM owner_visit_feedback_publications WHERE property_visit_id = %s", (vid,))["n"],
        "contatti acquirente": _uno("SELECT count(*) AS n FROM contacts WHERE agency_id = %s AND display_name = %s", (agency, nome))["n"],
    }
    R.controllo("6", "nessun duplicato: una sola riga per fatto", all(v == 1 for v in conteggi.values()), f"{conteggi}")
    dopo = _uno("SELECT count(*) AS n FROM communication_messages WHERE agency_id = %s", (agency,))["n"]
    mio = _uno("SELECT count(*) AS n FROM communication_messages WHERE agency_id = %s AND (contact_id = %s OR (metadata->>'appointment_id')::bigint = %s)",
               (agency, contatto, aid))["n"]
    R.controllo("6", "nessun messaggio al cliente sintetico in tutto il percorso (pubblicare non invia nulla)", mio == 0,
                f"{mio} messaggi; totale agenzia {prima_dei_messaggi}->{dopo} (il cron reale puo' aggiungerne di altri flussi)")
    wd = agente.post(f"/api/owner-reports/visits/{aid}/withdraw", json={"publication_id": bozza.get("id", 0)})
    R.controllo("6", "ritiro della pubblicazione da parte dell'agente", wd.status_code == 200, f"{wd.status_code} {wd.text[:150]}")
    vista = portale.get(f"{OWNER_PORTAL}/{pid}")
    R.controllo("6", "dopo il ritiro il portale non mostra piu' il testo",
                vista.status_code == 200 and vista.json().get("visit_feedback") == [] and TESTO_PUBBLICO not in vista.text)
    rap = portale.get(f"{OWNER_PORTAL}/{pid}/report", params=periodo)
    R.controllo("6", "dopo il ritiro il report del proprietario non conta il riscontro",
                rap.status_code == 200 and (rap.json().get("visit_feedback") or {}).get("total") == 0)
    storico = _uno("SELECT count(*) AS n FROM owner_visit_feedback_publications WHERE property_visit_id = %s", (vid,))["n"]
    R.controllo("6", "il ritiro conserva lo storico (la riga resta, archiviata)", storico == 1, f"{storico}")


def pulizia(cl: Client, ids: dict, run_id: str | None) -> None:
    """Chiude cio' che il collaudo ha aperto: sessioni e accessi del proprietario sintetico, collegamento, immobile."""
    filtro = f"{CODE_PREFIX}{run_id}" if run_id else f"{CODE_PREFIX}%"
    immobili = _q("SELECT id, archived_at FROM properties WHERE agency_id = %s AND code LIKE %s", (ids["agency"], filtro))
    if not immobili:
        print(f"INFO  pulizia: nessun immobile sintetico ({filtro})", flush=True)
        return
    owner = cl.attore("owner")
    for imm in immobili:
        pid = imm["id"]
        _q("UPDATE owner_sessions SET revoked_at = NOW(), expires_at = LEAST(expires_at, NOW()) WHERE revoked_at IS NULL AND "
           "owner_account_id IN (SELECT owner_account_id FROM owner_property_access WHERE property_id = %s)", (pid,), scrivi=True)
        _q("UPDATE owner_property_access SET access_status = 'revoked', revoked_at = NOW() WHERE property_id = %s AND access_status = 'active'",
           (pid,), scrivi=True)
        for link in _q("SELECT id FROM property_visit_booking_links WHERE property_id = %s AND agency_id = %s AND status = 'active'", (pid, ids["agency"])):
            owner.post(f"/api/visit-booking-links/{link['id']}/disable", json={"reason": "operator", "note": "fine collaudo visite"})
        if imm["archived_at"] is None:
            owner.post(f"/api/property/properties/{pid}/archive")
    print(f"INFO  pulizia: {len(immobili)} immobile/i sintetico/i chiusi (accesso proprietario, sessione, collegamento, archivio)", flush=True)


# ---------------------------------------------------------------------------
# Comandi
# ---------------------------------------------------------------------------

def _controlla_argomenti(args) -> None:
    if args.agente_di_prova and args.agent_user_id:
        print("STOP --agente-di-prova e --agent-user-id sono incompatibili: o l'agente reale, o quello di prova", flush=True)
        raise SystemExit(2)


def risolvi_senza_agente(args) -> None:
    """Agenzia e attore come negli altri collaudi; l'agente reale NON viene cercato ne' usato."""
    import contextlib
    import io
    args.agent_user_id = -1                                        # segnaposto: evita che si cerchi un agente reale
    buffer = io.StringIO()
    uscita = None
    try:
        with contextlib.redirect_stdout(buffer):
            _p2.risolvi_ids(args)
    except SystemExit as exc:                                      # agenzia o attore ambigui: il messaggio va mostrato
        uscita = exc
    finally:
        args.agent_user_id = None
    testo = buffer.getvalue().replace(", agente -1", "").replace("agente -1", "")
    if testo.strip():
        print(testo.rstrip().replace("(ricavati dal servizio)", "(ricavati dal servizio; agente: di prova)"), flush=True)
    if uscita is not None:
        raise uscita


def _preflight(args) -> tuple:
    _controlla_argomenti(args)
    g = _p2.Gate()
    gate_ambiente(g, args)
    risolvi_senza_agente(args) if args.agente_di_prova else _p2.risolvi_ids(args)
    gate_configurazione(g)
    conn = _p2._connect(True)
    try:
        cur = conn.cursor()
        gate_database(g, cur, args.agency_id, args.actor_user_id, args.agent_user_id, agente_di_prova=args.agente_di_prova)
        ruoli = gate_ruoli(cur, args.agency_id, args.agent_user_id) if not g.problems else {}
        conn.rollback()
    finally:
        conn.close()
    return g, ruoli


def cmd_preflight(args) -> int:
    g, _ = _preflight(args)
    print("ESITO: " + ("PREFLIGHT SUPERATO (sola lettura, nulla scritto)" if not g.problems
                       else f"PREFLIGHT NON SUPERATO ({len(g.problems)} problemi)"), flush=True)
    return 0 if not g.problems else 1


def stampa_report(R: Registro, run_id: str, con_agente: bool = False) -> None:
    titoli = {"A": "A. Agente di prova, orari e permessi", "Z": "Z. Pulizia della configurazione di prova",
              "0": "Preparazione dei dati sintetici", "1": "1. Prenotazione -> Agenda", "2": "2. Esito e feedback",
              "3": "3. Schede immobile e contatto", "4": "4. Foglio visita PDF e archivio",
              "5": "5. Aggiornamento al proprietario e portale", "6": "6. Privacy, permessi, duplicati, ritiro"}
    print(f"\n==== REPORT COLLAUDO VISITE {run_id} ====", flush=True)
    for passo, stati in sorted(R.per_passo().items()):
        esito = "FAIL" if KO in stati else ("PASS" if stati and all(s in (OK, NA) for s in stati) else "SKIP")
        print(f"{esito:4}  {titoli.get(passo, passo):<46} {stati.count(OK)} pass, {stati.count(KO)} fail, {stati.count(NA)} skip", flush=True)
    attesi = {"1", "2", "3", "4", "5", "6"} | ({"A", "Z"} if con_agente else set())
    mancanti = sorted(attesi - set(R.per_passo()))
    for m in mancanti:
        print(f"FAIL  {titoli[m]:<46} NON ESEGUITO", flush=True)
    ko = R.fallite() + len(mancanti)
    print(f"\nESITO: {sum(1 for r in R.righe if r[2] == OK)} controlli PASS, {R.fallite()} FAIL"
          f"{', passi non eseguiti: ' + ', '.join(mancanti) if mancanti else ''}", flush=True)
    if ko:
        print("COLLAUDO NON SUPERATO: le righe sintetiche restano per l'ispezione (immobile "
              f"{CODE_PREFIX}{run_id}; accesso del proprietario e collegamento sono gia' chiusi). "
              "Il percorso NON si dichiara chiuso.", flush=True)
    else:
        print("COLLAUDO SUPERATO su TEST con dati sintetici. Verifica reale del PDF (impronta e testo) e della "
              "pubblicazione al proprietario sintetico: eseguita. Restano da guardare a mano, se si vuole, la "
              "grafica del PDF e la schermata del portale.", flush=True)


def cmd_avvia(args) -> int:
    g, ruoli = _preflight(args)
    if g.problems:
        print(f"ESITO: PREFLIGHT NON SUPERATO ({len(g.problems)} problemi): nulla scritto.", flush=True)
        return 1
    ids = {"agency": args.agency_id, "actor": args.actor_user_id, "agent": args.agent_user_id}
    run_id = args.run_id or datetime.now(ROMA).strftime("CV%y%m%d%H%M%S")
    print(f"\nINFO  collaudo {run_id}: agenzia {ids['agency']}, attore {ids['actor']}, agente "
          f"{'di prova (da creare)' if args.agente_di_prova else ids['agent']} "
          f"(altro agente {'reale' if ruoli['altro_agente'] else 'sintetico'}, altra agenzia "
          f"{'reale' if ruoli['altra_agenzia'] else 'sintetica'})\n", flush=True)
    R = Registro()
    vivi = contesti(ids["agency"], ids["actor"], ids["agent"] or 0, ruoli)
    cl = Client(vivi)
    try:
        try:
            if args.agente_di_prova:
                if crea_agente_di_prova(R, cl, ids, run_id, vivi) is None:
                    raise RuntimeError("agente di prova non predisposto")
            dati = prepara(R, cl, ids, run_id)
            if dati is not None:
                percorso(R, cl, ids, run_id, dati)
        except Exception as exc:                                   # nessuna eccezione deve nascondere un FAIL
            R.controllo("X", "il collaudo e' arrivato in fondo", False, f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            try:
                pulizia(cl, ids, run_id)
            except Exception as exc:
                print(f"WARN  pulizia incompleta: {type(exc).__name__}; rilanciare `cleanup`", flush=True)
            if args.agente_di_prova:
                try:
                    pulizia_agente(R, cl, ids, run_id)
                except Exception as exc:
                    R.controllo("Z", "pulizia dell'agente di prova", False, f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        cl.chiudi()
    stampa_report(R, run_id, con_agente=args.agente_di_prova)
    attesi = set("123456") | ({"A", "Z"} if args.agente_di_prova else set())
    return 0 if R.fallite() == 0 and not (attesi - set(R.per_passo())) else 1


def cmd_cleanup(args) -> int:
    _controlla_argomenti(args)
    g = _p2.Gate()
    gate_ambiente(g, args)
    g.need(not _sembra_produzione(), "nessuna variabile d'ambiente dichiara «production»")
    g.stop_if_failed("ambiente non verificato")
    risolvi_senza_agente(args) if args.agente_di_prova else _p2.risolvi_ids(args)
    ids = {"agency": args.agency_id, "actor": args.actor_user_id, "agent": args.agent_user_id}
    conn = _p2._connect(True)
    try:
        ruoli = gate_ruoli(conn.cursor(), ids["agency"], ids["agent"])
        conn.rollback()
    finally:
        conn.close()
    cl = Client(contesti(ids["agency"], ids["actor"], ids["agent"] or 0, ruoli))
    try:
        pulizia(cl, ids, args.run_id)
        pulizia_agente(None, cl, ids, args.run_id)                 # chiude anche gli agenti di prova rimasti
    finally:
        cl.chiudi()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Collaudo sintetico del percorso della visita su TEST")
    p.add_argument("mode", choices=("avvia", "preflight", "cleanup"))
    p.add_argument("--expected-host", required=True, help="l'host del database TEST, verificato a mano")
    p.add_argument("--agency-id", type=int, help="se manca: l'unica agenzia servita dal cron")
    p.add_argument("--actor-user-id", type=int, help="se manca: l'unico owner attivo dell'agenzia")
    p.add_argument("--agent-user-id", type=int, help="se manca: l'unico agente attivo, altrimenti l'attore")
    p.add_argument("--agente-di-prova", action="store_true",
                   help="crea (e a fine collaudo chiude) un agente sintetico con orari di disponibilita': serve quando "
                        "nessun agente reale ha un orario. Incompatibile con --agent-user-id")
    p.add_argument("--run-id")
    args = p.parse_args(argv)
    return {"avvia": cmd_avvia, "preflight": cmd_preflight, "cleanup": cmd_cleanup}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
