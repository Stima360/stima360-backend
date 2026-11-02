"""ROADMAP PUNTO 2 - collaudo su TEST con dati SINTETICI delle conferme e dei
promemoria degli appuntamenti. Pensato per un One-Off Job Render del servizio
TEST (stesse variabili d'ambiente del servizio: DB_*, SMTP_*, APPOINTMENT_*).

UN SOLO COMANDO

  python scripts/collaudo_punto2.py avvia --expected-host <HOST_DB_TEST_VERIFICATO>

`avvia` si puo' lanciare a QUALUNQUE ora (ora, alle 19, alle 20, di notte) e si
puo' rilanciare: riparte da dove il collaudo e' arrivato.

  1. controlla l'ambiente (solo TEST, host atteso, 127, configurazione, SMTP
     effettivo, destinatario UNICO info@stima360.it, agenzia servita dal cron);
  2. se non c'e' un collaudo aperto, ne crea uno: contatti e appuntamenti
     SINTETICI scritti attraverso i service dell'Agenda;
  3. aspetta qualche minuto il cron esistente per le prove immediate;
  4. stampa il report e distingue PROVE CONCLUSE, PROVE IN ATTESA (con l'ora a
     cui diventano dovute e il limite oltre il quale sarebbero fallite) e PROVE
     FALLITE;
  5. se tutte le prove sono concluse e nessuna e' fallita, annulla da solo gli
     appuntamenti sintetici (pulizia). Se qualcosa e' fallito, lascia i dati per
     ispezionarli: poi `cleanup`.

PROVE IMMEDIATE, a qualunque ora: la conferma (una sola, anche dopo doppio
clic e «Conferma»), lo spostamento, l'annullamento, il divieto di contatto, il
destinatario fuori elenco. Le conferme non dipendono dalla fascia oraria.

PROVE PROGRAMMATE, per la PROSSIMA FASCIA UTILE (08:00-20:00 di Roma): il
promemoria 2h (scenario A) e il 24h (scenario B). Se la fascia e' aperta e ne
restano almeno 30 minuti, la prossima fascia utile e' ADESSO; altrimenti e'
l'apertura successiva. Gli orari degli appuntamenti di A e B si ricavano
simulando le regole VERE della policy (`appointment_reminders.policy`) un
giro di cron ogni 5 minuti: A deve ricevere il 2h e mai il 24h, B il 24h e mai
il 2h, entrambi al primo giro utile. Nessuna copia delle regole, nessun
orologio cambiato, nessuna regola di invio modificata.

NON SPEDISCE NULLA DA SOLO. La spedizione la fa il cron esistente
(`POST /api/internal/communication/cron/run`), e solo verso l'unico indirizzo
dell'allowlist. Questo strumento non puo' spedire per errore i messaggi in coda
di altri flussi.

ALTRI MODI (per usarli a pezzi): preflight (sola lettura), setup, report (sola
lettura), cleanup (idempotente). Tutti si fermano prima di toccare il database
se `DB_NAME` non e' `stima360_db_test` o `DB_HOST` non e' quello atteso. Nessun
indirizzo email viene stampato per intero.

SCENARI (ogni scenario ha un contatto sintetico "Collaudo P2 <lettera>")
  A  conferma unica (doppio clic + «Conferma») e promemoria 2h; mai il 24h
  B  conferma unica e promemoria 24h; mai il 2h
  C  appuntamento tra 3 giorni, subito SPOSTATO di un'ora: la conferma del
     vecchio orario e' annullata, parte solo il «Nuovo orario»
  D  appuntamento tra 4 giorni, subito ANNULLATO: nessuna email
  E  contatto con DIVIETO DI CONTATTO: conferma accodata e soppressa
  F  contatto con email FUORI dall'allowlist: nessuna conferma accodata
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPECTED_DB = "stima360_db_test"
MARKER = "collaudo_punto2"
ROMA = ZoneInfo("Europe/Rome")
#: L'UNICO destinatario del collaudo: scritto qui, non passato da riga di comando.
DESTINATARIO = "info@stima360.it"
FUORI_ELENCO = "collaudo-fuori-elenco@example.invalid"
SCENARI = ("A", "B", "C", "D", "E", "F")

#: Fascia utile ancora "aperta" solo se ne restano almeno cosi' tanti minuti: il
#: cron deve avere il tempo di passare.
MARGINE_FASCIA = timedelta(minutes=30)
PASSO = timedelta(minutes=5)
#: A fine fascia il cron puo' arrivare in ritardo: la prova deve reggere anche cosi'.
TOLLERANZA_CRON = timedelta(minutes=25)

CONFERMA, PROMEMORIA = "appointment_confirmation", "appointment_reminder"
CONCLUSA, ATTESA, FALLITA = "conclusa", "in attesa", "fallita"


class Gate:
    def __init__(self):
        self.problems: list[str] = []

    def need(self, ok: bool, text: str) -> bool:
        print(f"{'OK  ' if ok else 'FAIL'} {text}", flush=True)
        if not ok:
            self.problems.append(text)
        return ok

    def stop_if_failed(self, message: str) -> None:
        if self.problems:
            print(f"STOP {message}", flush=True)
            raise SystemExit(1)


def mascherata(email: str) -> str:
    nome, _, dominio = (email or "").partition("@")
    return f"{nome[:1]}***@{dominio}" if dominio else "***"


def _connect(readonly: bool = True):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
                            cursor_factory=RealDictCursor)
    conn.set_session(readonly=readonly)
    return conn


def _destinatari() -> list[str]:
    from appointment_reminders import policy
    raw = os.getenv(policy.RECIPIENTS_ENV)
    return sorted(policy.allowed_recipients_strict(raw) or [])


def environment_gate(g: Gate, expected_host: str) -> None:
    g.need(os.getenv("DB_NAME") == EXPECTED_DB, f"DB_NAME del servizio = {EXPECTED_DB}")
    g.need(bool(expected_host) and "INCOLLA" not in expected_host, "host TEST atteso indicato")
    g.need((os.getenv("DB_HOST") or "") == expected_host, "DB_HOST del servizio = host TEST atteso")
    g.stop_if_failed("ambiente non verificato, nessuna connessione aperta")


def smtp_effective() -> dict:
    """La configurazione SMTP EFFETTIVA, con le stesse regole del trasporto.

    Non le ripete: chiede al trasporto stesso (`database.invia_mail`) con
    `smtplib.SMTP` sostituito per la durata della prova da un finto che registra
    host e porta e poi rifiuta. Nessuna connessione, nessun login, nessuna email,
    e nessun valore duplicato qui: default dell'host, default della porta, utente
    e password obbligatori sono quelli di `invia_mail`, oggi e domani. Se il
    trasporto non arriva a costruire la connessione, la configurazione non e'
    valida. L'output del trasporto viene catturato e scartato.

    Restituisce solo cio' che e' lecito mostrare: configurata si'/no, host e
    porta (non sono segreti), da dove viene l'host (variabile o default del
    trasporto), PRESENZA (mai il valore) di utente e password.
    """
    import contextlib
    import io
    import smtplib
    from unittest import mock

    import database

    visto: dict = {}

    class _Sonda(Exception):
        pass

    def _finto(host, port=0, *args, **kwargs):
        visto["host"], visto["port"] = host, port
        raise _Sonda("sonda: nessuna connessione aperta")

    esito = {"configurata": False, "host": None, "port": None, "host_da": None,
             "utente_presente": bool(os.getenv("SMTP_USER")), "password_presente": bool(os.getenv("SMTP_PASS")),
             "errore": None}
    muto = io.StringIO()
    with mock.patch.object(smtplib, "SMTP", _finto), contextlib.redirect_stdout(muto):
        try:
            database.invia_mail("sonda@example.invalid", "sonda", "<p>sonda</p>")
        except Exception as exc:  # noqa: BLE001 - es. SMTP_PORT non numerica: il trasporto stesso fallirebbe
            esito["errore"] = type(exc).__name__
    if "host" in visto:
        esito.update(configurata=True, host=visto["host"], port=visto["port"],
                     host_da="variabile SMTP_HOST" if os.getenv("SMTP_HOST") else "default del trasporto")
    return esito


def smtp_gate(g: Gate) -> None:
    eff = smtp_effective()
    g.need(eff["utente_presente"], "SMTP_USER presente")
    g.need(eff["password_presente"], "SMTP_PASS presente (solo la presenza, mai il valore)")
    if eff["errore"]:
        g.need(False, f"il trasporto SMTP non e' utilizzabile: {eff['errore']} (es. SMTP_PORT non numerica)")
    else:
        g.need(eff["configurata"], "il trasporto SMTP risulta configurato (stesse regole di invia_mail, senza connettersi)")
    if eff["configurata"]:
        print(f"INFO  server SMTP effettivo: {eff['host']}:{eff['port']} ({eff['host_da']})", flush=True)


# ---------------------------------------------------------------------------
# Pianificazione degli orari: PURA, sulle regole vere della policy
# ---------------------------------------------------------------------------

def _policy():
    from appointment_reminders import policy
    return policy


def _su_griglia(dt: datetime) -> datetime:
    """Il primo multiplo di 5 minuti >= dt (secondi azzerati)."""
    dt = dt.replace(second=0, microsecond=0)
    resto = dt.minute % 5
    return dt + timedelta(minutes=(5 - resto) % 5)


def prossima_fascia(now: datetime, margine: timedelta = MARGINE_FASCIA) -> tuple[datetime, bool]:
    """(istante, immediata): quando comincia la prossima fascia UTILE per
    inviare, e se e' adesso. Utile = aperta E con almeno `margine` ancora davanti."""
    policy = _policy()
    if policy.in_window(now) and policy.in_window(now + margine):
        return now, True
    base = now + margine if policy.in_window(now) else now
    return policy.next_window_instant(base), False


def primo_invio(offset: str, *, da: datetime, fino: datetime, start_at: datetime, created_at: datetime) -> datetime | None:
    """Simula un cron che gira ogni 5 minuti fra `da` e `fino` con le regole
    VERE della policy (stesso criterio del planner: se non c'e' un motivo di
    scarto ed e' dovuto, il promemoria parte). Il primo istante in cui parte,
    o None."""
    policy = _policy()
    t = da
    while t <= fino:
        d = policy.send_decision(now=t, start_at=start_at, created_at=created_at, offset=offset)
        if d.reason is None and d.due:
            return t
        t += PASSO
    return None


def _eleggibile(offset: str, t: datetime, start_at: datetime, created_at: datetime) -> tuple[bool, bool]:
    """(parte_se_il_cron_passa_ora, e_ancora_possibile) con le regole VERE della policy."""
    d = _policy().send_decision(now=t, start_at=start_at, created_at=created_at, offset=offset)
    return (d.reason is None and d.due), d.reason is None


def previsti(start_at: datetime, created_at: datetime) -> dict[str, tuple[datetime, datetime]]:
    """I promemoria che la policy PREVEDE per questo appuntamento, creato a
    `created_at`, con un cron che gira ogni 5 minuti: {offset: (primo, ultimo)}.
    `primo` e' il primo giro in cui parte; `ultimo` l'ultimo istante utile oltre il
    quale non partirebbe piu' (finestra chiusa, anticipo troppo corto). Dipende
    solo dai dati dell'appuntamento: si ricalcola uguale a ogni rilancio."""
    risultato = {}
    for offset in _policy().OFFSETS:
        primo = primo_invio(offset, da=created_at, fino=start_at, start_at=start_at, created_at=created_at)
        if primo is None:
            continue
        ultimo, t = primo, primo
        while t <= start_at:
            if _eleggibile(offset, t, start_at, created_at)[0]:
                ultimo = t
            t += PASSO
        risultato[offset] = (primo, ultimo)
    return risultato


def orario_valido(lettera: str, now: datetime, w: datetime, start_at: datetime, *, pulito: bool) -> bool:
    """A deve ricevere il 2h e B il 24h, al primo giro utile (entro 5 minuti dalla
    fascia) e con margine per un cron in ritardo. `pulito`: l'altro promemoria
    non deve partire affatto (meno email); se non esiste un orario cosi', si ripiega
    su quelli in cui la policy prevede anche l'altro, e il report lo prevede."""
    creato = now + timedelta(minutes=2)            # l'appuntamento nasce un attimo dopo il calcolo
    voluto, altro = ("2h", "24h") if lettera == "A" else ("24h", "2h")
    attesi = previsti(start_at, creato)
    if voluto not in attesi:
        return False
    primo, ultimo = attesi[voluto]
    if not (w <= primo <= w + PASSO) or ultimo < primo + TOLLERANZA_CRON:
        return False
    return not (pulito and altro in attesi)


def orari_candidati(lettera: str, now: datetime, w: datetime, massimo: int = 400):
    """Gli orari di inizio validi per A o B, dal piu' vicino: prima quelli in cui
    parte SOLO il promemoria voluto; se non ce ne sono, quelli in cui la policy
    ne prevede anche un altro. Un generatore: se l'agenda dell'agente e' occupata
    si passa al successivo."""
    if lettera == "A":
        inizio = _su_griglia(w + timedelta(hours=1, minutes=55))
    else:
        inizio = _su_griglia(max(w + timedelta(hours=3, minutes=5), now + timedelta(hours=12, minutes=10)))
    for pulito in (True, False):
        s = inizio
        for _ in range(massimo):
            if orario_valido(lettera, now, w, s, pulito=pulito):
                yield s
            s += PASSO


def programma(now: datetime, margine: timedelta = MARGINE_FASCIA) -> dict:
    """Il piano completo per `now`: la fascia utile e il primo orario valido di A e B."""
    now = now.replace(second=0, microsecond=0)
    w, immediata = prossima_fascia(now, margine)
    a = next(orari_candidati("A", now, w), None)
    b = next(orari_candidati("B", now, w), None)
    if a is None or b is None:
        raise SystemExit("STOP nessun orario valido trovato per i promemoria: nessun dato scritto")
    return {"fascia": w, "immediata": immediata, "A": a, "B": b}


def descrivi(dt: datetime) -> str:
    return dt.astimezone(ROMA).strftime("%d/%m/%Y %H:%M")



def config_gate(g: Gate) -> None:
    """Le tre impostazioni del collaudo, e SOLO l'indirizzo info@stima360.it."""
    from appointment_reminders import policy, settings
    raw = os.getenv(policy.RECIPIENTS_ENV)
    g.need(settings.confirmations_enabled(), "APPOINTMENT_CONFIRMATIONS=1 (conferme accese)")
    g.need("2h" in settings.offsets() and "24h" in settings.offsets(),
           "APPOINTMENT_REMINDER_OFFSETS contiene 24h e 2h")
    g.need(raw is not None and raw.strip() != policy.WILDCARD,
           "APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS e' un elenco esplicito (non assente, non '*')")
    elenco = _destinatari()
    g.need(DESTINATARIO in elenco, f"il destinatario del collaudo ({mascherata(DESTINATARIO)}) e' nell'elenco")
    g.need(elenco == [DESTINATARIO], f"l'elenco contiene SOLO il destinatario del collaudo ({len(elenco)} indirizzo/i)")
    smtp_gate(g)


def db_gate(g: Gate, cur, agency_id: int, actor: int, agent: int) -> dict:
    cur.execute("SELECT current_database() AS db, pg_is_in_recovery() AS rec")
    riga = cur.fetchone()
    g.need(riga["db"] == EXPECTED_DB and riga["rec"] is False, "database TEST primario")
    cur.execute("SELECT checksum_up, rolled_back_at FROM schema_migrations WHERE version = '127_appointment_confirmations'")
    r127 = cur.fetchone()
    g.need(r127 is not None and r127["rolled_back_at"] is None, "migrazione 127 registrata e non annullata")
    from communication import repository as comm_repo
    g.need(comm_repo.reason_code_supported(cur, "appointment_confirmation"), "il vincolo dei motivi ammette appointment_confirmation")
    cur.execute("SELECT status FROM agencies WHERE id = %s", (agency_id,))
    ag = cur.fetchone()
    g.need(ag is not None and ag["status"] == "active", f"agenzia {agency_id} attiva")
    cur.execute("SELECT role, status FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agency_id, actor))
    att = cur.fetchone()
    g.need(att is not None and att["status"] == "active" and att["role"] in ("agency_owner", "agency_admin"),
           "l'attore e' owner/admin attivo dell'agenzia")
    cur.execute("SELECT role, status FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s", (agency_id, agent))
    agt = cur.fetchone()
    g.need(agt is not None and agt["status"] == "active", "l'agente e' attivo nell'agenzia")
    from communication import cron_router
    consentita = agency_id in cron_router.agenzie_consentite()
    g.need(consentita, f"l'agenzia {agency_id} e' fra quelle servite dal cron (COMMUNICATION_CRON_AGENCY_IDS)")
    return {"role": att["role"] if att else None}


def risolvi_ids(args) -> None:
    """Completa agenzia, attore e agente quando NON sono ambigui (le ricava dal
    servizio: agenzia servita dal cron, owner attivo, agente attivo); se lo sono,
    si ferma ed elenca i candidati (solo id e ruolo, mai nomi o email)."""
    if args.agency_id and args.actor_user_id and args.agent_user_id:
        return
    from communication import cron_router
    conn = _connect(True)
    try:
        cur = conn.cursor()
        if not args.agency_id:
            servite = sorted(cron_router.agenzie_consentite())
            if len(servite) != 1:
                print(f"STOP agenzia non univoca: servite dal cron {servite or 'nessuna'}; indicare --agency-id", flush=True)
                raise SystemExit(1)
            args.agency_id = servite[0]
        cur.execute("SELECT operator_user_id AS id, role FROM agency_memberships WHERE agency_id = %s AND status = 'active' "
                    "ORDER BY operator_user_id", (args.agency_id,))
        membri = cur.fetchall()
        if not args.actor_user_id:
            owners = [m["id"] for m in membri if m["role"] == "agency_owner"]
            if len(owners) != 1:
                print("STOP attore non univoco: " + ", ".join(f"{m['id']}={m['role']}" for m in membri) + "; indicare --actor-user-id", flush=True)
                raise SystemExit(1)
            args.actor_user_id = owners[0]
        if not args.agent_user_id:
            agenti = [m["id"] for m in membri if m["role"] == "agent"]
            args.agent_user_id = agenti[0] if len(agenti) == 1 else args.actor_user_id
        conn.rollback()
    finally:
        conn.close()
    print(f"INFO  agenzia {args.agency_id}, attore {args.actor_user_id}, agente {args.agent_user_id} (ricavati dal servizio)", flush=True)


def altro_in_coda(cur, agency_id: int) -> None:
    """Cio' che il cron spedirebbe in questo giro oltre al collaudo."""
    cur.execute("SELECT m.reason_code, count(*) AS n FROM communication_messages m "
                "LEFT JOIN contacts c ON c.id = m.contact_id AND c.agency_id = m.agency_id "
                "WHERE m.agency_id = %s AND m.status = 'queued' AND COALESCE(c.source, '') NOT LIKE %s "
                "GROUP BY 1 ORDER BY 1", (agency_id, f"{MARKER}:%"))
    righe = cur.fetchall()
    cur.execute("SELECT count(*) AS n FROM appointments WHERE agency_id = %s AND status IN ('scheduled','confirmed') "
                "AND start_at > NOW() AND start_at < NOW() + INTERVAL '48 hours'", (agency_id,))
    app = cur.fetchone()["n"]
    print("INFO  messaggi gia' in coda per l'agenzia, NON del collaudo (il cron li spedira' comunque): "
          + (", ".join(f"{r['reason_code']}={r['n']}" for r in righe) or "nessuno"), flush=True)
    print(f"INFO  appuntamenti reali nelle prossime 48h dell'agenzia: {app} "
          "(ricevono notifiche solo se il loro contatto e' nell'elenco: qui e' solo il destinatario del collaudo)", flush=True)


def operatore(agency_id: int, user_id: int, role: str):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=False,
                           session_id=None, auth_channel="session")


def cmd_preflight(args) -> Gate:
    g = Gate()
    environment_gate(g, args.expected_host)
    risolvi_ids(args)
    config_gate(g)
    conn = _connect(True)
    try:
        cur = conn.cursor()
        db_gate(g, cur, args.agency_id, args.actor_user_id, args.agent_user_id)
        altro_in_coda(cur, args.agency_id)
        conn.rollback()
    finally:
        conn.close()
    return g



# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def _crea_contatto(ctx, run_id: str, lettera: str, email: str) -> int:
    from core import service as core_service
    from core.schemas import ContactCreate
    c = core_service.create_contact(ctx, ContactCreate(
        contact_type="person", first_name="Collaudo", last_name=f"P2 {lettera}", email=email,
        source=f"{MARKER}:{run_id}:{lettera}", status="active",
        notes="Contatto SINTETICO del collaudo del Punto 2: da annullare/cestinare a fine prova."))
    return int(c["id"])


def _crea(ctx, agent: int, contact_id: int, start: datetime, *, request_id=None, tipo="seller_meeting", property_id=None):
    from appointments import service
    from appointments.schemas import AppointmentCreate
    return service.create_appointment_idempotent(ctx, AppointmentCreate(
        appointment_type=tipo, status="scheduled", start_at=start, end_at=start + timedelta(minutes=30),
        assigned_user_id=agent, contact_id=contact_id, property_id=property_id,
        client_request_id=request_id or str(uuid.uuid4())))


def _crea_su_orari_validi(ctx, agent: int, contact_id: int, candidati, **kw):
    """Crea sul primo orario valido libero dell'agenda: l'ORARIO NON SI SPOSTA a
    caso (si scorre solo fra quelli gia' verificati sulle regole della policy)."""
    from appointments import errors
    ultimo = None
    for tentativo, s in enumerate(candidati):
        if tentativo >= 10:
            break
        try:
            riga, replica = _crea(ctx, agent, contact_id, s, **kw)
            return riga, replica, s
        except errors.AppointmentConflict as exc:
            ultimo = exc
    raise SystemExit("STOP agenda occupata per l'agente su tutti gli orari validi provati"
                     + (f" ({type(ultimo).__name__})" if ultimo else ""))


def cmd_setup(args) -> None:
    from appointments import service
    from appointments.schemas import CancelBody, ConfirmBody, RescheduleBody
    from consent import do_not_contact
    g = cmd_preflight(args)
    g.stop_if_failed("controlli non superati: nessun dato sintetico creato")
    adesso = datetime.now(timezone.utc)
    piano = programma(adesso)
    conn = _connect(True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT role FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s",
                    (args.agency_id, args.actor_user_id))
        ruolo = cur.fetchone()["role"]
        conn.rollback()
    finally:
        conn.close()
    ctx = operatore(args.agency_id, args.actor_user_id, ruolo)
    run_id = args.run_id or adesso.strftime("%Y%m%d%H%M%S")
    print(f"== setup, run {run_id}, ora di Roma {descrivi(adesso)}", flush=True)
    print(f"INFO  prossima fascia utile per i promemoria: {descrivi(piano['fascia'])} "
          f"({'ADESSO: prove immediate' if piano['immediata'] else 'prove programmate: in attesa dell apertura'})", flush=True)

    contatti = {lettera: _crea_contatto(ctx, run_id, lettera, FUORI_ELENCO if lettera == "F" else DESTINATARIO)
                for lettera in SCENARI}
    print(f"OK   {len(contatti)} contatti sintetici creati (sorgente {MARKER}:{run_id}:<lettera>)", flush=True)

    tipo_a = "buyer_visit" if args.property_id else "seller_meeting"
    # A - 2h al primo giro utile; doppio clic e «Conferma» non aggiungono conferme
    rid_a = str(uuid.uuid4())
    riga_a, _, inizio_a = _crea_su_orari_validi(ctx, args.agent_user_id, contatti["A"],
                                                orari_candidati("A", adesso.replace(second=0, microsecond=0), piano["fascia"]),
                                                request_id=rid_a, tipo=tipo_a, property_id=args.property_id)
    _, replica = _crea(ctx, args.agent_user_id, contatti["A"], inizio_a, request_id=rid_a, tipo=tipo_a, property_id=args.property_id)
    print(f"OK   A creato per {descrivi(inizio_a)}; secondo clic con la stessa richiesta riconosciuto come replica: {replica}", flush=True)
    service.confirm_appointment(ctx, riga_a["id"], ConfirmBody(version=riga_a["version"]))
    # B - 24h al primo giro utile
    _, _, inizio_b = _crea_su_orari_validi(ctx, args.agent_user_id, contatti["B"],
                                           orari_candidati("B", adesso.replace(second=0, microsecond=0), piano["fascia"]))
    print(f"OK   B creato per {descrivi(inizio_b)}", flush=True)
    for lettera, inizio in (("A", inizio_a), ("B", inizio_b)):
        prev = previsti(inizio, adesso + timedelta(minutes=2))
        print(f"INFO  {lettera}: la policy prevede " + (", ".join(f"{o} dalle {descrivi(t[0])} (limite {descrivi(t[1])})"
                                                          for o, t in prev.items()) or "nessun promemoria"), flush=True)

    def giorno(n):
        return (adesso + timedelta(days=n)).astimezone(ROMA).replace(hour=10, minute=0, second=0, microsecond=0)

    # C - spostato subito di un'ora
    riga_c, _ = _crea(ctx, args.agent_user_id, contatti["C"], giorno(3))
    nuovo_c = riga_c["start_at"] + timedelta(hours=1)
    service.reschedule_appointment(ctx, riga_c["id"], RescheduleBody(
        version=riga_c["version"], start_at=nuovo_c, end_at=nuovo_c + timedelta(minutes=30)))
    # D - annullato subito
    riga_d, _ = _crea(ctx, args.agent_user_id, contatti["D"], giorno(4))
    service.cancel_appointment(ctx, riga_d["id"], CancelBody(version=riga_d["version"], reason="collaudo sintetico"))
    # E - divieto di contatto
    do_not_contact.set_ban(ctx, contatti["E"], source="agency_decision", reason="collaudo sintetico del Punto 2")
    _crea(ctx, args.agent_user_id, contatti["E"], giorno(5))
    # F - email fuori dall'allowlist
    _crea(ctx, args.agent_user_id, contatti["F"], giorno(6))
    print("OK   scenari A-F scritti attraverso i service dell'Agenda", flush=True)



# ---------------------------------------------------------------------------
# lettura e valutazione: prove concluse / in attesa / fallite
# ---------------------------------------------------------------------------

@dataclass
class Prova:
    id: str
    titolo: str
    stato: str
    nota: str = ""
    breve: bool = False          # il cron la chiude in pochi minuti: vale la pena aspettarla
    scadenza: datetime | None = None


def _run_corrente(cur, agency_id: int, run_id: str | None):
    """(run, {lettera: contact_id}) dell'ultimo collaudo (o di `run_id`)."""
    cur.execute("SELECT id, source FROM contacts WHERE agency_id = %s AND source LIKE %s ORDER BY id DESC",
                (agency_id, f"{MARKER}:%"))
    per_run: dict[str, dict[str, int]] = {}
    for r in cur.fetchall():
        _, run, lettera = r["source"].split(":", 2)
        if lettera in SCENARI and (run_id is None or run == run_id):
            per_run.setdefault(run, {}).setdefault(lettera, r["id"])
    if not per_run:
        return None, {}
    run = max(per_run, key=lambda k: max(per_run[k].values()))
    return run, per_run[run]


def _righe_ledger(cur, agency_id: int, contact_id: int):
    cur.execute(
        "SELECT m.id, m.reason_code, m.status, m.suppressed_reason, m.template_key, m.subject_snapshot, m.attempt_count, "
        "       (m.metadata->>'appointment_id')::bigint AS appointment_id, m.metadata->>'variant' AS variant, "
        "       m.metadata->>'offset' AS offset, a.status AS appointment_status "
        "FROM communication_messages m LEFT JOIN appointments a ON a.id = (m.metadata->>'appointment_id')::bigint "
        "WHERE m.agency_id = %s AND m.contact_id = %s ORDER BY m.id", (agency_id, contact_id))
    return cur.fetchall()


def _appuntamenti(cur, agency_id: int, contact_id: int):
    cur.execute("SELECT id, version, status, start_at, created_at FROM appointments WHERE agency_id = %s AND contact_id = %s ORDER BY id",
                (agency_id, contact_id))
    return cur.fetchall()


def leggi(cur, agency_id: int, run_id: str | None):
    run, contatti = _run_corrente(cur, agency_id, run_id)
    dati = {lettera: {"contact_id": cid, "righe": _righe_ledger(cur, agency_id, cid),
                      "appuntamenti": _appuntamenti(cur, agency_id, cid)} for lettera, cid in contatti.items()}
    return run, dati


def _aperto(appuntamenti):
    return [a for a in appuntamenti if a["status"] in ("requested", "scheduled", "confirmed")]


def run_aperto(dati: dict) -> bool:
    return any(_aperto(sc["appuntamenti"]) for sc in dati.values())


def _stato_messaggio(r) -> str:
    return CONCLUSA if r["status"] == "sent" else ATTESA if r["status"] == "queued" else FALLITA


def _prova_conferma(sigla: str, titolo: str, righe, *, variante=None, vecchia=False) -> Prova:
    conf = [r for r in righe if r["reason_code"] == CONFERMA]
    if vecchia:
        giuste = [r for r in conf if r["appointment_status"] == "rescheduled"]
    elif variante:
        giuste = [r for r in conf if r["variant"] == variante and r["appointment_status"] != "rescheduled"]
    else:
        giuste = conf
    if len(giuste) != 1:
        return Prova(sigla, titolo, FALLITA, f"attesa UNA conferma, trovate {len(giuste)}")
    r = giuste[0]
    return Prova(sigla, titolo, _stato_messaggio(r), f"conferma #{r['id']}: " + ("accettata dal provider" if r["status"] == "sent" else r["status"]),
                 breve=r["status"] == "queued")


def _prove_promemoria(lettera: str, scopo: str, dati: dict, ora: datetime) -> list[Prova]:
    """Una prova per OGNI promemoria che la policy prevede per l'appuntamento
    (simulato dai suoi dati: orario di inizio e di creazione), piu' il controllo
    che non ne sia partito nessuno di non previsto. CONCLUSA = accettata dal
    provider; IN ATTESA = non ancora accodata/inviata ma ancora possibile, con il
    limite oltre il quale fallirebbe; FALLITA = finestra chiusa senza invio, o
    scartata/fallita/non prevista."""
    policy = _policy()
    righe = [r for r in dati.get(lettera, {}).get("righe", []) if r["reason_code"] == PROMEMORIA]
    aperti = _aperto(dati.get(lettera, {}).get("appuntamenti", []))
    if not aperti:
        return [Prova(f"{lettera}-{scopo}", f"{lettera}  promemoria {scopo}", FALLITA,
                      "appuntamento non aperto (annullato dalla pulizia?): prova non conclusa")]
    a = aperti[0]
    attesi = previsti(a["start_at"], a["created_at"])
    prove: list[Prova] = []
    for offset in policy.OFFSETS:
        if offset not in attesi:
            continue
        primo, ultimo = attesi[offset]
        titolo = f"{lettera}  promemoria {offset}" + (" (lo scopo della prova)" if offset == scopo else " (previsto dalla policy)")
        sigla = f"{lettera}-{offset}"
        mess = [r for r in righe if r["offset"] == offset]
        if mess:
            r = mess[0]
            prove.append(Prova(sigla, titolo, _stato_messaggio(r),
                               f"promemoria {offset} #{r['id']}: " + ("accettato dal provider" if r["status"] == "sent" else r["status"]),
                               breve=r["status"] == "queued"))
        elif ora > ultimo:
            prove.append(Prova(sigla, titolo, FALLITA,
                               f"nessun promemoria {offset} entro {descrivi(ultimo)}: il cron non e' passato in tempo"))
        else:
            dal = max(primo, ora)
            prove.append(Prova(sigla, titolo, ATTESA,
                               f"dovuto dal giro del cron delle {descrivi(dal)}, limite {descrivi(ultimo)}",
                               breve=policy.in_window(ora) and primo <= ora + timedelta(minutes=15), scadenza=ultimo))
    for r in righe:
        if r["offset"] not in attesi:
            prove.append(Prova(f"{lettera}-{r['offset']}", f"{lettera}  promemoria {r['offset']} NON previsto dalla policy", FALLITA,
                               f"promemoria {r['offset']} #{r['id']} accodato ma la policy non lo prevede per questo appuntamento"))
    return prove


def valuta(dati: dict, ora: datetime) -> list[Prova]:
    """Le prove, ognuna CONCLUSA, IN ATTESA o FALLITA, sul ledger letto adesso.
    In attesa non e' un fallimento: e' una prova che il cron (o la fascia
    oraria) non ha ancora potuto chiudere, con il limite oltre il quale fallirebbe."""
    policy = _policy()
    prove: list[Prova] = []

    def righe(l):
        return dati.get(l, {}).get("righe", [])

    def start_aperto(l):
        a = _aperto(dati.get(l, {}).get("appuntamenti", []))
        return a[0]["start_at"] if a else None

    # --- A: conferma unica + i promemoria che la policy prevede (il 2h, e' lo scopo)
    prove.append(_prova_conferma("A1", "A  una sola conferma, anche dopo doppio clic e «Conferma»", righe("A")))
    prove += _prove_promemoria("A", "2h", dati, ora)
    # --- B: conferma unica + i promemoria che la policy prevede (il 24h, e' lo scopo)
    prove.append(_prova_conferma("B1", "B  una sola conferma", righe("B")))
    prove += _prove_promemoria("B", "24h", dati, ora)

    # --- C: spostamento
    titolo_c1 = "C  spostamento: la conferma del VECCHIO orario e' annullata e non e' mai partita"
    vecchie = [r for r in righe("C") if r["reason_code"] == CONFERMA and r["appointment_status"] == "rescheduled"]
    if len(vecchie) != 1:
        prove.append(Prova("C1", titolo_c1, FALLITA, f"attesa UNA conferma del vecchio orario, trovate {len(vecchie)}"))
    else:
        r = vecchie[0]
        prove.append(Prova("C1", titolo_c1, CONCLUSA if r["status"] == "cancelled" else FALLITA,
                           f"conferma del vecchio orario #{r['id']}: {r['status']}"
                           + ("" if r["status"] == "cancelled" else " (doveva essere annullata, mai inviata)")))
    prove.append(_prova_conferma("C2", "C  spostamento: parte UN SOLO «Nuovo orario»", righe("C"), variante="rescheduled"))

    # --- D: annullamento
    conf = [r for r in righe("D") if r["reason_code"] == CONFERMA]
    if len(conf) == 1 and conf[0]["status"] == "cancelled" and not any(r["status"] == "sent" for r in righe("D")):
        prove.append(Prova("D1", "D  annullamento: la conferma e' annullata, nessuna email", CONCLUSA, f"conferma #{conf[0]['id']}: cancelled"))
    else:
        prove.append(Prova("D1", "D  annullamento: la conferma e' annullata, nessuna email", FALLITA,
                           f"stati trovati: {[r['status'] for r in righe('D')]}"))

    # --- E: divieto di contatto
    conf = [r for r in righe("E") if r["reason_code"] == CONFERMA]
    if len(conf) != 1:
        prove.append(Prova("E1", "E  divieto di contatto: conferma soppressa", FALLITA, f"attesa UNA conferma, trovate {len(conf)}"))
    elif conf[0]["status"] == "suppressed":
        ok = conf[0]["suppressed_reason"] == "do_not_contact"
        prove.append(Prova("E1", "E  divieto di contatto: conferma soppressa", CONCLUSA if ok else FALLITA,
                           f"conferma #{conf[0]['id']}: soppressa ({conf[0]['suppressed_reason']})"))
    elif conf[0]["status"] == "queued":
        prove.append(Prova("E1", "E  divieto di contatto: conferma soppressa", ATTESA, "ancora in coda: il cron non e' passato", breve=True))
    else:
        prove.append(Prova("E1", "E  divieto di contatto: conferma soppressa", FALLITA, f"stato {conf[0]['status']} (nessuna email doveva uscire)"))

    # --- F: fuori elenco
    prove.append(Prova("F1", "F  email fuori elenco: nessuna conferma accodata",
                       CONCLUSA if not righe("F") else FALLITA,
                       "nessun messaggio" if not righe("F") else f"trovati {len(righe('F'))} messaggi"))
    return prove


def stampa_prove(prove: list[Prova], ora: datetime) -> tuple[int, int, int]:
    gruppi = {CONCLUSA: [], ATTESA: [], FALLITA: []}
    for p in prove:
        gruppi[p.stato].append(p)
    titoli = {CONCLUSA: "PROVE CONCLUSE", ATTESA: "PROVE IN ATTESA (non ancora concluse, non fallite)", FALLITA: "PROVE FALLITE"}
    for stato in (CONCLUSA, ATTESA, FALLITA):
        print(f"\n== {titoli[stato]}: {len(gruppi[stato])}", flush=True)
        for p in gruppi[stato]:
            print(f"   [{p.id}] {p.titolo}\n        {p.nota}", flush=True)
    return len(gruppi[CONCLUSA]), len(gruppi[ATTESA]), len(gruppi[FALLITA])


def stampa_distinzione(dati: dict) -> list[str]:
    print("\n== distinzione degli stati dei messaggi", flush=True)
    print("   accodata               = riga nel registro comunicazioni", flush=True)
    print("   accettata dal provider = `sent` nel registro: il server SMTP ha accettato il messaggio", flush=True)
    print("   ricevuta               = NON verificabile da qui: la conferma una persona guardando la casella", flush=True)
    attese = []
    for lettera in SCENARI:
        for r in dati.get(lettera, {}).get("righe", []):
            if r["status"] == "sent":
                attese.append(f"{lettera}: «{r['subject_snapshot']}»")
    print(f"\n== email accettate dal provider, da cercare nella casella di {mascherata(DESTINATARIO)} ({len(attese)}):", flush=True)
    for riga in attese:
        print("   - " + riga, flush=True)
    print("   Controllare: giorno e ora italiani, riferimenti dell'agenzia, lettura da telefono senza scorrimento orizzontale.", flush=True)
    return attese


def report(cur, args, ora: datetime):
    run, dati = leggi(cur, args.agency_id, args.run_id)
    if not dati:
        print("STOP nessun collaudo trovato (eseguire prima `avvia`)", flush=True)
        raise SystemExit(1)
    print(f"\n######## REPORT del run {run}, ora di Roma {descrivi(ora)}", flush=True)
    prove = valuta(dati, ora)
    conclusi, attesa, fallite = stampa_prove(prove, ora)
    stampa_distinzione(dati)
    return dati, prove, (conclusi, attesa, fallite)



# ---------------------------------------------------------------------------
# comandi
# ---------------------------------------------------------------------------

def cmd_report(args) -> int:
    g = Gate()
    environment_gate(g, args.expected_host)
    risolvi_ids(args)
    conn = _connect(True)
    try:
        cur = conn.cursor()
        _, _, (conclusi, attesa, fallite) = report(cur, args, datetime.now(timezone.utc))
        conn.rollback()
    finally:
        conn.close()
    esito(conclusi, attesa, fallite)
    return 0 if fallite == 0 else 1


def esito(conclusi: int, attesa: int, fallite: int) -> None:
    print(f"\nESITO: {conclusi} concluse, {attesa} in attesa, {fallite} fallite", flush=True)
    if fallite:
        print("ALMENO UNA PROVA E' FALLITA: i dati sintetici restano per l'ispezione; poi `cleanup`.", flush=True)
    elif attesa:
        print("COLLAUDO NON ANCORA CONCLUSO: ci sono prove in attesa. Rilanciare lo STESSO comando `avvia` "
              "dopo l'ora indicata: riparte da qui, non crea dati nuovi.", flush=True)
    else:
        print("TUTTE LE PROVE SONO CONCLUSE (la ricezione nella casella resta da confermare a mano).", flush=True)


def cmd_cleanup(args) -> int:
    g = Gate()
    environment_gate(g, args.expected_host)
    risolvi_ids(args)
    from appointments import service
    from appointments.schemas import CancelBody
    from consent import do_not_contact
    conn = _connect(True)
    try:
        cur = conn.cursor()
        run, dati = leggi(cur, args.agency_id, args.run_id)
        cur.execute("SELECT role FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s",
                    (args.agency_id, args.actor_user_id))
        att = cur.fetchone()
        g.need(att is not None, "l'attore appartiene all'agenzia")
        g.stop_if_failed("attore non valido: nessuna scrittura")
        ctx = operatore(args.agency_id, args.actor_user_id, att["role"])
        aperti = [a for sc in dati.values() for a in _aperto(sc["appuntamenti"])]
        conn.rollback()
    finally:
        conn.close()
    annullati = 0
    for a in aperti:
        service.cancel_appointment(ctx, a["id"], CancelBody(version=a["version"], reason="collaudo sintetico: pulizia"))
        annullati += 1
    print(f"OK   appuntamenti sintetici annullati ora: {annullati}", flush=True)
    if "E" in dati:
        try:
            do_not_contact.lift_ban(ctx, dati["E"]["contact_id"], note="fine del collaudo sintetico")
            print("OK   divieto di contatto del contatto E tolto", flush=True)
        except Exception as exc:  # noqa: BLE001 - gia' tolto: va bene
            print(f"INFO divieto di contatto E non da togliere ({type(exc).__name__})", flush=True)
    from core import contact_lifecycle
    cestinati = 0
    for lettera, sc in dati.items():
        try:
            contact_lifecycle.trash_contact(ctx, sc["contact_id"], "test_record", "contatto sintetico del collaudo Punto 2")
            cestinati += 1
        except Exception as exc:  # noqa: BLE001 - un contatto con storico puo' non essere cestinabile
            if getattr(exc, "code", None) == contact_lifecycle.ALREADY_DELETED:
                cestinati += 1
                continue
            print(f"INFO contatto {lettera} non cestinato ({type(exc).__name__}): resta, marcato come sintetico", flush=True)
    print(f"OK   contatti sintetici nel Cestino: {cestinati} su {len(dati)}", flush=True)
    conn = _connect(True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) AS n FROM communication_messages m JOIN contacts c ON c.id = m.contact_id "
                    "WHERE m.agency_id = %s AND m.status = 'queued' AND c.source LIKE %s", (args.agency_id, f"{MARKER}:%"))
        residui = cur.fetchone()["n"]
        conn.rollback()
    finally:
        conn.close()
    g.need(residui == 0, f"nessun messaggio sintetico ancora in coda (trovati {residui})")
    print("\nRICORDA di ripristinare la configurazione temporanea del servizio TEST:\n"
          "  APPOINTMENT_CONFIRMATIONS, APPOINTMENT_REMINDER_OFFSETS, APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", flush=True)
    return 0 if not g.problems else 1


def cmd_avvia(args) -> int:
    """Il comando unico: controlla, crea (se serve), aspetta il cron per le prove
    immediate, riferisce, e pulisce solo se tutto e' concluso senza fallimenti."""
    environment_gate(Gate(), args.expected_host)
    risolvi_ids(args)
    conn = _connect(True)
    try:
        cur = conn.cursor()
        _, dati = leggi(cur, args.agency_id, args.run_id)
        conn.rollback()
    finally:
        conn.close()
    if dati and run_aperto(dati):
        print("== collaudo gia' aperto: lo riprendo senza creare dati nuovi", flush=True)
        g = Gate()
        config_gate(g)             # la configurazione deve valere ancora, per i promemoria in attesa
        g.stop_if_failed("configurazione non piu' valida: nessuna scrittura")
    else:
        cmd_setup(args)
    limite = time.monotonic() + max(0, args.attesa_minuti) * 60
    while True:
        conn = _connect(True)
        try:
            cur = conn.cursor()
            ora = datetime.now(timezone.utc)
            run, dati = leggi(cur, args.agency_id, args.run_id)
            prove = valuta(dati, ora)
            conn.rollback()
        finally:
            conn.close()
        da_attendere = [p for p in prove if p.stato == ATTESA and p.breve]
        if not da_attendere or time.monotonic() >= limite or any(p.stato == FALLITA for p in prove):
            break
        print(f"   ... in attesa del cron per: {', '.join(p.id for p in da_attendere)} "
              f"(ancora {int((limite - time.monotonic()) // 60)} min)", flush=True)
        time.sleep(30)
    conn = _connect(True)
    try:
        cur = conn.cursor()
        _, _, (conclusi, attesa, fallite) = report(cur, args, datetime.now(timezone.utc))
        conn.rollback()
    finally:
        conn.close()
    esito(conclusi, attesa, fallite)
    if fallite:
        return 1
    if not attesa:
        print("\n== tutte le prove concluse: pulizia automatica dei dati sintetici", flush=True)
        return cmd_cleanup(args)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Collaudo sintetico del Punto 2 (conferme e promemoria) su TEST")
    p.add_argument("mode", choices=("avvia", "preflight", "setup", "report", "cleanup"))
    p.add_argument("--expected-host", required=True)
    p.add_argument("--agency-id", type=int, help="se manca: l'unica agenzia servita dal cron")
    p.add_argument("--actor-user-id", type=int, help="se manca: l'unico owner attivo dell'agenzia")
    p.add_argument("--agent-user-id", type=int, help="se manca: l'unico agente attivo, altrimenti l'attore")
    p.add_argument("--property-id", type=int, help="immobile con incarico per fare di A una visita con indirizzo")
    p.add_argument("--attesa-minuti", type=int, default=20, help="avvia: quanto aspettare il cron per le prove immediate")
    p.add_argument("--run-id")
    args = p.parse_args()
    if args.mode == "avvia":
        return cmd_avvia(args)
    if args.mode == "preflight":
        g = cmd_preflight(args)
        print("ESITO: " + ("PREFLIGHT SUPERATO (sola lettura, nulla scritto)" if not g.problems else
                           f"PREFLIGHT NON SUPERATO ({len(g.problems)} problemi)"), flush=True)
        return 0 if not g.problems else 1
    if args.mode == "setup":
        cmd_setup(args)
        return 0
    if args.mode == "report":
        return cmd_report(args)
    return cmd_cleanup(args)


if __name__ == "__main__":
    sys.exit(main())
