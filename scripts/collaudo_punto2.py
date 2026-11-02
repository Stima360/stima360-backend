"""ROADMAP PUNTO 2 - collaudo su TEST con dati SINTETICI delle conferme e dei
promemoria degli appuntamenti. Pensato per un One-Off Job Render del servizio
TEST (stesse variabili d'ambiente del servizio: DB_*, SMTP_*, APPOINTMENT_*).

NON SPEDISCE NULLA DA SOLO. Scrive solo appuntamenti e contatti sintetici
attraverso i service del CRM (gli stessi dell'interfaccia: gli agganci delle
conferme sono quelli veri); la spedizione la fa il giro del cron esistente
(`POST /api/internal/communication/cron/run`), che spedisce SOLO verso gli
indirizzi dell'allowlist. Cosi' questo script non puo' spedire per errore i
messaggi in coda di altri flussi.

MODI (un comando per volta, nell'ordine):

  preflight   sola lettura. Ambiente, database, registro della 127, variabili
              di collaudo, agenzia/agente/attore, e cosa altro e' in coda per
              l'agenzia (il cron spedirebbe anche quello).
  setup       crea i sei scenari sintetici (vedi SCENARI). Rifa' il preflight,
              rifiuta se qualcosa non va. Si lancia in fascia 08:00-20:00 (Roma):
              il 2h e' valido solo li'.
  report      sola lettura. Per ogni scenario: accodata / accettata dal provider
              / sopressa / annullata, l'esito atteso, e l'elenco delle email che
              devono essere ARRIVATE (la ricezione la conferma una persona).
  cleanup     annulla gli appuntamenti sintetici ancora aperti, toglie il
              divieto di contatto del contatto sintetico, prova a mettere i
              contatti sintetici nel Cestino. Idempotente.

Tutti i modi si fermano prima di toccare il database se `DB_NAME` non e'
`stima360_db_test` o `DB_HOST` non e' quello atteso (`--expected-host`).
Nessun indirizzo email viene stampato per intero.

SCENARI (ogni scenario ha un contatto sintetico "Collaudo P2 <lettera>")
  A  appuntamento tra ~1h50 (il target 2h e' gia' passato: dovuto subito): UNA conferma + il promemoria 2h (non il 24h:
     prenotato meno di 12h prima). Poi «Conferma» e un secondo clic sulla stessa
     richiesta: ancora UNA sola conferma.
  B  appuntamento tra ~20h: UNA conferma + il promemoria 24h (non il 2h).
  C  appuntamento tra 3 giorni, subito SPOSTATO di un'ora: la conferma del
     vecchio orario e' annullata, parte solo il «Nuovo orario».
  D  appuntamento tra 4 giorni, subito ANNULLATO: nessuna email.
  E  contatto con DIVIETO DI CONTATTO: la conferma e' accodata e soppressa.
  F  contatto con email FUORI dall'allowlist: nessuna conferma accodata.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPECTED_DB = "stima360_db_test"
MARKER = "collaudo_punto2"
ROMA = ZoneInfo("Europe/Rome")
FUORI_ELENCO = "collaudo-fuori-elenco@example.invalid"
SCENARI = ("A", "B", "C", "D", "E", "F")
FINESTRA = (8, 20)

CONFERMA, PROMEMORIA = "appointment_confirmation", "appointment_reminder"


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


def config_gate(g: Gate, recipient: str | None) -> None:
    """Le tre impostazioni del collaudo, e SOLO indirizzi elencati a mano."""
    from appointment_reminders import policy, settings
    raw = os.getenv(policy.RECIPIENTS_ENV)
    g.need(settings.confirmations_enabled(), "APPOINTMENT_CONFIRMATIONS=1 (conferme accese)")
    g.need("2h" in settings.offsets() and "24h" in settings.offsets(),
           "APPOINTMENT_REMINDER_OFFSETS contiene 24h e 2h")
    g.need(raw is not None and raw.strip() != policy.WILDCARD,
           "APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS e' un elenco esplicito (non assente, non '*')")
    elenco = _destinatari()
    g.need(len(elenco) >= 1, f"l'elenco ha {len(elenco)} indirizzo/i: " + ", ".join(mascherata(e) for e in elenco))
    if recipient is not None:
        g.need(recipient.strip().lower() in elenco, "il destinatario del collaudo e' nell'elenco")
        g.need(len(elenco) == 1, "l'elenco contiene SOLO il destinatario del collaudo")
    g.need(bool(os.getenv("SMTP_HOST")) and bool(os.getenv("SMTP_USER")), "SMTP configurato (host e utente presenti)")


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


def altro_in_coda(cur, agency_id: int) -> None:
    """Cio' che il cron spedirebbe in questo giro oltre al collaudo."""
    cur.execute("SELECT reason_code, count(*) AS n FROM communication_messages WHERE agency_id = %s AND status = 'queued' "
                "AND COALESCE(metadata->>'collaudo', '') <> %s GROUP BY 1 ORDER BY 1", (agency_id, MARKER))
    righe = cur.fetchall()
    cur.execute("SELECT count(*) AS n FROM appointments WHERE agency_id = %s AND status IN ('scheduled','confirmed') "
                "AND start_at > NOW() AND start_at < NOW() + INTERVAL '48 hours'", (agency_id,))
    app = cur.fetchone()["n"]
    print("INFO  messaggi gia' in coda per l'agenzia (il cron li spedira' comunque): "
          + (", ".join(f"{r['reason_code']}={r['n']}" for r in righe) or "nessuno"), flush=True)
    print(f"INFO  appuntamenti reali nelle prossime 48h dell'agenzia: {app} "
          "(con l'elenco esplicito ricevono notifiche solo se il loro contatto e' nell'elenco)", flush=True)


def operatore(agency_id: int, user_id: int, role: str):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=False,
                           session_id=None, auth_channel="session")


def _arrotonda(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0) + timedelta(minutes=(5 - dt.minute % 5) % 5)  # per B


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def cmd_preflight(args, *, recipient: str | None = None) -> Gate:
    g = Gate()
    environment_gate(g, args.expected_host)
    config_gate(g, recipient)
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


def _crea_appuntamento(ctx, agent: int, contact_id: int, start: datetime, durata=30, tipo="seller_meeting",
                       property_id=None, request_id=None):
    from appointments import errors, service
    from appointments.schemas import AppointmentCreate
    ultimo = None
    for tentativo in range(6):                                  # un conflitto di agenda: si scorre di 30 minuti
        inizio = start + timedelta(minutes=30 * tentativo)
        try:
            riga, replica = service.create_appointment_idempotent(ctx, AppointmentCreate(
                appointment_type=tipo, status="scheduled", start_at=inizio, end_at=inizio + timedelta(minutes=durata),
                assigned_user_id=agent, contact_id=contact_id, property_id=property_id,
                client_request_id=request_id or str(uuid.uuid4())))
            return riga, replica
        except errors.AppointmentConflict as exc:
            ultimo = exc
    raise SystemExit(f"STOP agenda occupata per l'agente: nessuno slot libero provato ({type(ultimo).__name__})")


def cmd_setup(args) -> None:
    recipient = args.recipient.strip().lower()
    g = cmd_preflight(args, recipient=recipient)
    adesso = datetime.now(timezone.utc)
    ora_roma = adesso.astimezone(ROMA)
    g.need(FINESTRA[0] <= ora_roma.hour < FINESTRA[1] - 3,
           f"ora di Roma {ora_roma:%H:%M}: il collaudo si lancia fra le {FINESTRA[0]:02d}:00 e le {FINESTRA[1] - 3:02d}:00 "
           "(il 2h e il 24h partono solo nella fascia 08:00-20:00)")
    g.stop_if_failed("controlli non superati: nessun dato sintetico creato")

    from appointments import service
    from appointments.schemas import CancelBody, ConfirmBody, RescheduleBody
    from consent import do_not_contact
    conn = _connect(True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT role FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s",
                    (args.agency_id, args.actor_user_id))
        ruolo = cur.fetchone()["role"]
    finally:
        conn.close()
    ctx = operatore(args.agency_id, args.actor_user_id, ruolo)
    run_id = args.run_id or adesso.strftime("%Y%m%d%H%M%S")
    print(f"== setup, run {run_id} (ora di Roma {ora_roma:%d/%m/%Y %H:%M})", flush=True)

    contatti = {lettera: _crea_contatto(ctx, run_id, lettera, FUORI_ELENCO if lettera == "F" else recipient)
                for lettera in SCENARI}
    print(f"OK   {len(contatti)} contatti sintetici creati (sorgente {MARKER}:{run_id}:<lettera>)", flush=True)

    tipo_a = "buyer_visit" if args.property_id else "seller_meeting"
    # A - tra ~1h50: il target del 2h (inizio-2h) e' gia' passato, quindi il 2h e'
    # dovuto subito, con 1h50 di anticipo (>= 1h). Conferma + 2h; doppio clic e
    # «Conferma» non ne aggiungono.
    inizio_a = (adesso + timedelta(hours=1, minutes=50)).astimezone(ROMA).replace(second=0, microsecond=0)
    rid_a = str(uuid.uuid4())
    riga_a, _ = _crea_appuntamento(ctx, args.agent_user_id, contatti["A"], inizio_a, tipo=tipo_a,
                                   property_id=args.property_id, request_id=rid_a)
    _, replica = _crea_appuntamento(ctx, args.agent_user_id, contatti["A"], inizio_a, tipo=tipo_a,
                                    property_id=args.property_id, request_id=rid_a)
    print(f"OK   A creato; secondo clic con la stessa richiesta riconosciuto come replica: {replica}", flush=True)
    service.confirm_appointment(ctx, riga_a["id"], ConfirmBody(version=riga_a["version"]))
    # B - tra ~20h: conferma + 24h
    inizio_b = _arrotonda((adesso + timedelta(hours=20)).astimezone(ROMA))
    _crea_appuntamento(ctx, args.agent_user_id, contatti["B"], inizio_b)
    # C - tra 3 giorni, subito spostato di un'ora
    inizio_c = (adesso + timedelta(days=3)).astimezone(ROMA).replace(hour=10, minute=0, second=0, microsecond=0)
    riga_c, _ = _crea_appuntamento(ctx, args.agent_user_id, contatti["C"], inizio_c)
    nuovo_c = riga_c["start_at"] + timedelta(hours=1)
    service.reschedule_appointment(ctx, riga_c["id"], RescheduleBody(
        version=riga_c["version"], start_at=nuovo_c, end_at=nuovo_c + timedelta(minutes=30)))
    # D - tra 4 giorni, subito annullato
    inizio_d = (adesso + timedelta(days=4)).astimezone(ROMA).replace(hour=10, minute=0, second=0, microsecond=0)
    riga_d, _ = _crea_appuntamento(ctx, args.agent_user_id, contatti["D"], inizio_d)
    service.cancel_appointment(ctx, riga_d["id"], CancelBody(version=riga_d["version"], reason="collaudo sintetico"))
    # E - divieto di contatto
    do_not_contact.set_ban(ctx, contatti["E"], source="agency_decision", reason="collaudo sintetico del Punto 2")
    inizio_e = (adesso + timedelta(days=5)).astimezone(ROMA).replace(hour=10, minute=0, second=0, microsecond=0)
    _crea_appuntamento(ctx, args.agent_user_id, contatti["E"], inizio_e)
    # F - email fuori dall'allowlist
    inizio_f = (adesso + timedelta(days=6)).astimezone(ROMA).replace(hour=10, minute=0, second=0, microsecond=0)
    _crea_appuntamento(ctx, args.agent_user_id, contatti["F"], inizio_f)
    print("OK   scenari A-F scritti attraverso i service dell'Agenda", flush=True)
    print("\nPROSSIMO PASSO: lasciare girare il cron (o lanciare un giro) e poi eseguire `report`.\n"
          f"  report   --expected-host <HOST> --agency-id {args.agency_id} --actor-user-id {args.actor_user_id} "
          f"--agent-user-id {args.agent_user_id}\n"
          f"Il promemoria 24h di B e il 2h di A sono dovuti ADESSO: il giro del cron li accoda e li spedisce.", flush=True)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _scenari(cur, agency_id: int, run_id: str | None):
    pattern = f"{MARKER}:{run_id}:%" if run_id else f"{MARKER}:%"
    cur.execute("SELECT id, last_name, source FROM contacts WHERE agency_id = %s AND source LIKE %s ORDER BY id DESC",
                (agency_id, pattern))
    mappa: dict[str, dict] = {}
    for r in cur.fetchall():
        lettera = r["source"].rsplit(":", 1)[-1]
        run = r["source"].split(":")[1]
        if lettera in SCENARI and lettera not in mappa:
            mappa[lettera] = {"contact_id": r["id"], "run": run}
    return mappa


def _righe_ledger(cur, agency_id: int, contact_id: int):
    cur.execute(
        "SELECT m.id, m.reason_code, m.status, m.suppressed_reason, m.template_key, m.subject_snapshot, m.attempt_count, "
        "       (m.metadata->>'appointment_id')::bigint AS appointment_id, m.metadata->>'variant' AS variant, "
        "       m.metadata->>'offset' AS offset, a.status AS appointment_status "
        "FROM communication_messages m LEFT JOIN appointments a ON a.id = (m.metadata->>'appointment_id')::bigint "
        "WHERE m.agency_id = %s AND m.contact_id = %s ORDER BY m.id", (agency_id, contact_id))
    return cur.fetchall()


def attesi(lettera: str, righe) -> tuple[bool, list[str]]:
    """(superato, note) per lo scenario, sul ledger letto adesso. Se il cron non
    e' ancora passato (conferme ancora in coda, promemoria non ancora accodati)
    l'esito non e' un fallimento ma e' IN ATTESA: `superato` resta vero e la
    nota lo dice."""
    conf = [r for r in righe if r["reason_code"] == CONFERMA]
    prom = [r for r in righe if r["reason_code"] == PROMEMORIA]
    note: list[str] = []
    ok = True
    cron_atteso = any(r["status"] == "queued" for r in conf)       # conferma non ancora spedita

    def chiedi(cond: bool, testo: str):
        nonlocal ok
        ok = ok and cond
        note.append(("ok   " if cond else "FAIL ") + testo)

    if lettera == "A":
        vive = [r for r in conf if r["status"] != "cancelled"]
        chiedi(len(conf) == 1, f"UNA sola conferma in totale (trovate {len(conf)}), anche dopo doppio clic e «Conferma»")
        if not prom and cron_atteso:
            note.append("IN ATTESA: il promemoria 2h verra' accodato dal prossimo giro del cron")
        else:
            chiedi(sorted(r["offset"] for r in prom) == ["2h"], f"un solo promemoria, il 2h (trovati {[r['offset'] for r in prom]})")
        chiedi(all(r["status"] in ("sent", "queued") for r in vive + prom), "nessun messaggio soppresso o fallito")
    elif lettera == "B":
        chiedi(len(conf) == 1, f"UNA sola conferma (trovate {len(conf)})")
        if not prom and cron_atteso:
            note.append("IN ATTESA: il promemoria 24h verra' accodato dal prossimo giro del cron")
        else:
            chiedi(sorted(r["offset"] for r in prom) == ["24h"], f"un solo promemoria, il 24h (trovati {[r['offset'] for r in prom]})")
        chiedi(all(r["status"] in ("sent", "queued") for r in conf + prom), "nessun messaggio soppresso o fallito")
    elif lettera == "C":
        vecchie = [r for r in conf if r["appointment_status"] == "rescheduled"]
        nuove = [r for r in conf if r["appointment_status"] != "rescheduled"]
        chiedi(len(vecchie) == 1 and vecchie[0]["status"] == "cancelled",
               "la conferma del vecchio orario e' ANNULLATA (non e' mai partita)")
        chiedi(len(nuove) == 1 and nuove[0]["variant"] == "rescheduled" and nuove[0]["status"] in ("sent", "queued"),
               "una sola conferma «Nuovo orario» per l'orario nuovo")
        chiedi(not any(r["status"] == "sent" for r in vecchie), "nessuna email con il vecchio orario e' uscita")
    elif lettera == "D":
        chiedi(len(conf) == 1 and conf[0]["status"] == "cancelled", "la conferma e' ANNULLATA")
        chiedi(not any(r["status"] == "sent" for r in righe), "nessuna email e' uscita")
    elif lettera == "E":
        chiedi(len(conf) == 1 and conf[0]["status"] in ("suppressed", "queued"),
               "la conferma e' accodata e poi SOPPRESSA (divieto di contatto)")
        chiedi(all(r["suppressed_reason"] == "do_not_contact" for r in righe if r["status"] == "suppressed"),
               "motivo della soppressione: do_not_contact")
        chiedi(not any(r["status"] == "sent" for r in righe), "nessuna email e' uscita")
    elif lettera == "F":
        chiedi(len(righe) == 0, f"nessun messaggio accodato per l'indirizzo fuori elenco (trovati {len(righe)})")
    return ok, note


def cmd_report(args) -> None:
    g = Gate()
    environment_gate(g, args.expected_host)
    conn = _connect(True)
    esito_globale = True
    attese_in_casella: list[str] = []
    in_attesa = False
    try:
        cur = conn.cursor()
        mappa = _scenari(cur, args.agency_id, args.run_id)
        if not mappa:
            print("STOP nessuno scenario sintetico trovato (eseguire prima `setup`)", flush=True)
            raise SystemExit(1)
        for lettera in SCENARI:
            sc = mappa.get(lettera)
            print(f"\n== scenario {lettera} (run {sc['run'] if sc else '-'})", flush=True)
            if sc is None:
                print("FAIL contatto sintetico non trovato", flush=True)
                esito_globale = False
                continue
            righe = _righe_ledger(cur, args.agency_id, sc["contact_id"])
            for r in righe:
                accettata = "accettata dal provider" if r["status"] == "sent" else r["status"]
                extra = f" ({r['suppressed_reason']})" if r["suppressed_reason"] else ""
                print(f"     #{r['id']} {r['reason_code']}{'/' + r['offset'] if r['offset'] else ''}"
                      f"{'/' + r['variant'] if r['variant'] else ''}: {accettata}{extra}, tentativi {r['attempt_count']}",
                      flush=True)
                if r["status"] == "sent":
                    attese_in_casella.append(f"{lettera}: «{r['subject_snapshot']}»")
            ok, note = attesi(lettera, righe)
            for n in note:
                print("     " + n, flush=True)
            in_coda = [r for r in righe if r["status"] == "queued"]
            in_attesa = in_attesa or bool(in_coda) or any("IN ATTESA" in n for n in note)
            if in_coda:
                print(f"     PENDENTE: {len(in_coda)} messaggio/i ancora in coda (il cron non e' ancora passato)", flush=True)
            esito_globale = esito_globale and ok
        conn.rollback()
    finally:
        conn.close()
    print("\n== distinzione degli stati", flush=True)
    print("   accodata               = riga nel registro comunicazioni (questo report la vede)", flush=True)
    print("   accettata dal provider = `sent` nel registro: il server SMTP ha accettato il messaggio", flush=True)
    print("   ricevuta               = NON verificabile da qui: la conferma una persona guardando la casella", flush=True)
    print(f"\n== email che devono essere ARRIVATE nella casella autorizzata ({len(attese_in_casella)}):", flush=True)
    for riga in attese_in_casella:
        print("   - " + riga, flush=True)
    print("   Per ciascuna controllare: giorno e ora italiani, indirizzo (se visita), riferimenti dell'agenzia,", flush=True)
    print("   lettura da telefono senza scorrimento orizzontale. Nessuna email per D, E, F e per il vecchio orario di C.", flush=True)
    if esito_globale and in_attesa:
        print("\nESITO: IN ATTESA DEL CRON (nessun FAIL, ma ci sono messaggi ancora in coda): rilanciare il report dopo il giro", flush=True)
    else:
        print("\nESITO: " + ("TUTTI GLI SCENARI COERENTI COL LEDGER (la ricezione resta da confermare a mano)" if esito_globale
                             else "ALMENO UNO SCENARIO NON COERENTE: vedere i FAIL sopra"), flush=True)
    raise SystemExit(0 if esito_globale else 1)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

def cmd_cleanup(args) -> None:
    g = Gate()
    environment_gate(g, args.expected_host)
    from appointments import service
    from appointments.schemas import CancelBody
    from consent import do_not_contact
    conn = _connect(True)
    try:
        cur = conn.cursor()
        mappa = _scenari(cur, args.agency_id, args.run_id)
        cur.execute("SELECT role FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s",
                    (args.agency_id, args.actor_user_id))
        att = cur.fetchone()
        g.need(att is not None, "l'attore appartiene all'agenzia")
        g.stop_if_failed("attore non valido: nessuna scrittura")
        ctx = operatore(args.agency_id, args.actor_user_id, att["role"])
        aperti = []
        for sc in mappa.values():
            cur.execute("SELECT id, version, status FROM appointments WHERE agency_id = %s AND contact_id = %s "
                        "AND status IN ('requested','scheduled','confirmed') ORDER BY id", (args.agency_id, sc["contact_id"]))
            aperti += cur.fetchall()
        conn.rollback()
    finally:
        conn.close()
    annullati = 0
    for a in aperti:
        service.cancel_appointment(ctx, a["id"], CancelBody(version=a["version"], reason="collaudo sintetico: pulizia"))
        annullati += 1
    print(f"OK   appuntamenti sintetici annullati ora: {annullati}", flush=True)
    for lettera, sc in mappa.items():
        if lettera == "E":
            try:
                do_not_contact.lift_ban(ctx, sc["contact_id"], note="fine del collaudo sintetico")
                print("OK   divieto di contatto del contatto E tolto", flush=True)
            except Exception as exc:  # noqa: BLE001 - gia' tolto: va bene
                print(f"INFO divieto di contatto E non da togliere ({type(exc).__name__})", flush=True)
    from core import contact_lifecycle
    cestinati = 0
    for lettera, sc in mappa.items():
        try:
            contact_lifecycle.trash_contact(ctx, sc["contact_id"], "test_record", "contatto sintetico del collaudo Punto 2")
            cestinati += 1
        except Exception as exc:  # noqa: BLE001 - un contatto con storico puo' non essere cestinabile
            if getattr(exc, "code", None) == contact_lifecycle.ALREADY_DELETED:
                cestinati += 1                                  # gia' nel Cestino: stato raggiunto
                continue
            print(f"INFO contatto {lettera} non cestinato ({type(exc).__name__}): resta, marcato come sintetico", flush=True)
    print(f"OK   contatti sintetici nel Cestino: {cestinati} su {len(mappa)}", flush=True)
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
    raise SystemExit(0 if not g.problems else 1)


def main() -> int:
    p = argparse.ArgumentParser(description="Collaudo sintetico del Punto 2 (conferme e promemoria) su TEST")
    p.add_argument("mode", choices=("preflight", "setup", "report", "cleanup"))
    p.add_argument("--expected-host", required=True)
    p.add_argument("--agency-id", type=int, required=True)
    p.add_argument("--actor-user-id", type=int, required=True, help="owner/admin dell'agenzia che compare nello storico")
    p.add_argument("--agent-user-id", type=int, required=True, help="agente a cui assegnare gli appuntamenti sintetici")
    p.add_argument("--recipient", help="setup: la SOLA email autorizzata che riceve le email del collaudo")
    p.add_argument("--property-id", type=int, help="setup: immobile con incarico per fare di A una visita con indirizzo")
    p.add_argument("--run-id")
    args = p.parse_args()
    if args.mode == "setup":
        if not args.recipient:
            p.error("setup richiede --recipient")
        cmd_setup(args)
    elif args.mode == "preflight":
        g = cmd_preflight(args)
        print("ESITO: " + ("PREFLIGHT SUPERATO (sola lettura, nulla scritto)" if not g.problems else
                           f"PREFLIGHT NON SUPERATO ({len(g.problems)} problemi)"), flush=True)
        return 0 if not g.problems else 1
    elif args.mode == "report":
        cmd_report(args)
    else:
        cmd_cleanup(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
