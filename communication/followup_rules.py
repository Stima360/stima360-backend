"""Fase B - LE REGOLE della journey sul contatto: calendario, pausa, stop.

La sequenza non e' sulla stima: e' sulla persona. Quindi un incarico, un
sopralluogo o un lead chiuso non la FERMANO (il consenso resta): la mettono
in PAUSA finche' la relazione commerciale e' viva, e la fanno riprendere dal
prossimo passo in calendario, mai dalle email arretrate. La fermano solo la
base giuridica (consenso, disiscrizione), l'indirizzo e la persona.

TUTTO E' STATELESS. `pause_state` guarda i FATTI di adesso e risponde "in
pausa per questo, fino a quando". Il tick confronta la risposta con lo stato
dell'iscrizione: se e' attiva e i fatti dicono pausa, la mette in pausa; se e'
in pausa e i fatti dicono niente, la riprende. Non esiste una macchina a
stati da tenere allineata a mano con gli appuntamenti.

Le regole (decisioni del proprietario, Fase B):

  appuntamento aperto (requested/scheduled/confirmed, QUALUNQUE tipo:
     anche una visita da acquirente e' una relazione viva)     -> pausa
  appuntamento svolto                        -> pausa fino a +14 giorni
  appuntamento annullato o mancato           -> pausa fino a  +2 giorni
  consulenza richiesta dal portale           -> pausa fino a +14 giorni
  incarico / acquisizione / trattativa viva  -> pausa finche' dura
     (acquisizione non persa; immobile del contatto in mandate/active/
      reserved/under_offer; vendita pending; proposta aperta da acquirente)
  acquisizione persa, immobile ritirato/archiviato -> pausa fino a +2 giorni
  immobile venduto                           -> pausa fino a +60 giorni
  oltre il giorno 180                        -> completata, senza invii

Due passi non partono a meno di 48 ore l'uno dall'altro.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from . import send_window
from .journey_enums import PAUSED_BY_APPOINTMENT, PAUSED_BY_COMMERCIAL, PAUSED_BY_CONSULTATION

GIORNO = timedelta(days=1)
DOPO_APPUNTAMENTO_SVOLTO = 14 * GIORNO
DOPO_APPUNTAMENTO_ANNULLATO = 2 * GIORNO
DOPO_CONSULENZA = 14 * GIORNO
DOPO_RELAZIONE_FINITA = 2 * GIORNO
DOPO_VENDITA = 60 * GIORNO
DISTANZA_MINIMA = timedelta(hours=48)
#: Il calendario finisce qui: un passo oltre non parte, una ripresa oltre
#: completa.
ORIZZONTE = 180 * GIORNO

#: Una email a cui si puo' scrivere: una chiocciola, un dominio con un punto,
#: nessuno spazio. Non e' una validazione RFC: e' il minimo per non accodare
#: un messaggio che il server rifiuterebbe.
EMAIL_VALIDA = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

_STATI_APERTI = ("requested", "scheduled", "confirmed")
_COMMERCIALE_VIVO = ("mandate", "active", "reserved", "under_offer")


def email_valida(valore: str | None) -> bool:
    return bool(valore) and bool(EMAIL_VALIDA.match(valore.strip()))


def _tabella(cur, nome: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS presente", (f"public.{nome}",))
    return bool(cur.fetchone()["presente"])


def pause_state(cur, agency_id: int, contact_id: int, now: datetime) -> tuple[str | None, datetime | None, str | None]:
    """`(fonte, resume_after, riferimento)`: in pausa per cosa, fino a quando.

    `(None, None, None)` = nessun fatto che sospenda. `resume_after=None` con
    una fonte = "finche' il fatto dura". Fra piu' fatti vince quello che
    tiene ferma la sequenza piu' a lungo (una pausa senza data batte una
    con data; fra due date, la piu' lontana).
    """
    candidati: list[tuple[str, datetime | None, str]] = []

    # --- appuntamenti: aperti, poi i chiusi di recente -----------------------
    if _tabella(cur, "appointments"):
        cur.execute(
            """SELECT a.id, a.status, a.end_at, a.completed_at, a.cancelled_at, a.no_show_at,
                      a.updated_at
                 FROM appointments a
                 LEFT JOIN leads l ON l.id = a.lead_id AND l.agency_id = a.agency_id
                WHERE a.agency_id = %s AND (a.contact_id = %s OR l.contact_id = %s)
                  AND a.status IN ('requested', 'scheduled', 'confirmed', 'completed',
                                   'cancelled', 'no_show')
                ORDER BY a.id""",
            (agency_id, contact_id, contact_id))
        for a in cur.fetchall():
            if a["status"] in _STATI_APERTI:
                candidati.append((PAUSED_BY_APPOINTMENT, None, f"appointment:{a['id']}"))
            elif a["status"] == "completed":
                fine = max(x for x in (a["end_at"], a["completed_at"]) if x is not None)
                candidati.append((PAUSED_BY_APPOINTMENT, fine + DOPO_APPUNTAMENTO_SVOLTO,
                                  f"appointment:{a['id']}:completed"))
            else:
                quando = a["cancelled_at"] or a["no_show_at"] or a["updated_at"]
                candidati.append((PAUSED_BY_APPOINTMENT, quando + DOPO_APPUNTAMENTO_ANNULLATO,
                                  f"appointment:{a['id']}:{a['status']}"))

    # --- consulenza richiesta dal portale ------------------------------------
    if _tabella(cur, "seller_timeline_events"):
        cur.execute(
            """SELECT e.id, e.occurred_at FROM seller_timeline_events e
                 LEFT JOIN leads l ON l.id = e.lead_id AND l.agency_id = e.agency_id
                WHERE e.agency_id = %s AND e.event_type = 'owner_consultation_requested'
                  AND (e.contact_id = %s OR l.contact_id = %s)
                  AND e.occurred_at > %s""",
            (agency_id, contact_id, contact_id, now - DOPO_CONSULENZA))
        for e in cur.fetchall():
            candidati.append((PAUSED_BY_CONSULTATION, e["occurred_at"] + DOPO_CONSULENZA,
                              f"consultation:{e['id']}"))

    # --- relazione commerciale: acquisizioni e immobili del contatto ---------
    if _tabella(cur, "acquisitions"):
        cur.execute(
            """SELECT a.id, a.status, a.updated_at, p.id AS property_id, p.commercial_status,
                      p.updated_at AS property_updated_at
                 FROM acquisitions a JOIN properties p ON p.id = a.property_id
                WHERE a.agency_id = %s AND a.owner_contact_id = %s""",
            (agency_id, contact_id))
        for a in cur.fetchall():
            _relazione(candidati, now, ref=f"acquisition:{a['id']}", status=a["status"],
                       commercial_status=a["commercial_status"],
                       finita_il=max(a["updated_at"], a["property_updated_at"]))
    if _tabella(cur, "property_contacts"):
        cur.execute(
            """SELECT p.id, p.commercial_status, p.updated_at FROM property_contacts pc
                 JOIN properties p ON p.id = pc.property_id
                WHERE p.agency_id = %s AND pc.contact_id = %s AND pc.role IN ('owner', 'seller')""",
            (agency_id, contact_id))
        for p in cur.fetchall():
            _relazione(candidati, now, ref=f"property:{p['id']}", status=None,
                       commercial_status=p["commercial_status"], finita_il=p["updated_at"])
    if _tabella(cur, "property_sales"):
        cur.execute(
            """SELECT s.id, s.status, s.completed_at, s.updated_at FROM property_sales s
                 JOIN properties p ON p.id = s.property_id
                WHERE p.agency_id = %s AND (
                      EXISTS (SELECT 1 FROM acquisitions a WHERE a.property_id = p.id
                                 AND a.agency_id = p.agency_id AND a.owner_contact_id = %s)
                   OR EXISTS (SELECT 1 FROM property_contacts pc WHERE pc.property_id = p.id
                                 AND pc.contact_id = %s AND pc.role IN ('owner', 'seller')))""",
            (agency_id, contact_id, contact_id))
        for v in cur.fetchall():
            if v["status"] == "pending":
                candidati.append((PAUSED_BY_COMMERCIAL, None, f"sale:{v['id']}"))
            elif v["status"] == "completed":
                quando = v["completed_at"] or v["updated_at"]
                candidati.append((PAUSED_BY_COMMERCIAL, quando + DOPO_VENDITA, f"sale:{v['id']}:completed"))
    # --- trattativa da acquirente: una proposta aperta ------------------------
    if _tabella(cur, "property_proposals"):
        cur.execute(
            """SELECT pp.id FROM property_proposals pp
                 JOIN matches m ON m.id = pp.match_id
                 JOIN buy_requests b ON b.id = m.buy_request_id
                WHERE b.agency_id = %s AND b.contact_id = %s AND pp.status IN ('draft', 'submitted')""",
            (agency_id, contact_id))
        for pp in cur.fetchall():
            candidati.append((PAUSED_BY_COMMERCIAL, None, f"proposal:{pp['id']}"))

    vive = [c for c in candidati if c[1] is None]
    if vive:
        return vive[0]
    future = [c for c in candidati if c[1] is not None and c[1] > now]
    if not future:
        return None, None, None
    return max(future, key=lambda c: c[1])


def service_relationship(cur, agency_id: int, contact_id: int) -> str | None:
    """C2 (req. 9): la RICHIESTA DELL'INTERESSATO che un messaggio di servizio
    esegue, o None. Le stesse tabelle che `pause_state` legge, piu' la stima:
    una stima richiesta, un appuntamento (anche passato), un incarico o una
    relazione sull'immobile, una vendita, una richiesta d'acquisto. Il
    riferimento restituito finisce nel ledger del messaggio.

    Un contatto con cui l'agenzia non ha nessuno di questi fatti non ha
    chiesto niente: scrivergli e' marketing e passa dal consenso.
    """
    verifiche = [
        # la stima mandata a QUESTO contatto (la mail di servizio con il PDF):
        # la prova piu' diretta che l'ha chiesta lui
        ("stima_inviata", """SELECT m.stima_id AS ref FROM communication_messages m
                              WHERE m.agency_id = %s AND m.contact_id = %s AND m.stima_id IS NOT NULL
                                AND m.communication_type = 'service' AND m.reason_code = 'stima_pdf'
                              ORDER BY m.id DESC LIMIT 1""", "communication_messages"),
        ("stima", """SELECT s.id AS ref FROM stime s
                       JOIN leads l ON l.contact_id = %s AND l.agency_id = %s
                       JOIN lead_stime ls ON ls.lead_id = l.id AND ls.stima_id = s.id
                      ORDER BY s.id DESC LIMIT 1""", "lead_stime"),
        ("appointment", """SELECT a.id AS ref FROM appointments a
                             LEFT JOIN leads l ON l.id = a.lead_id AND l.agency_id = a.agency_id
                            WHERE a.agency_id = %s AND (a.contact_id = %s OR l.contact_id = %s)
                            ORDER BY a.id DESC LIMIT 1""", "appointments"),
        ("acquisition", """SELECT a.id AS ref FROM acquisitions a
                            WHERE a.agency_id = %s AND a.owner_contact_id = %s
                            ORDER BY a.id DESC LIMIT 1""", "acquisitions"),
        ("property", """SELECT p.id AS ref FROM property_contacts pc JOIN properties p ON p.id = pc.property_id
                         WHERE p.agency_id = %s AND pc.contact_id = %s
                         ORDER BY p.id DESC LIMIT 1""", "property_contacts"),
        ("buy_request", """SELECT b.id AS ref FROM buy_requests b
                            WHERE b.agency_id = %s AND b.contact_id = %s
                            ORDER BY b.id DESC LIMIT 1""", "buy_requests"),
    ]
    parametri = {
        "stima_inviata": (agency_id, contact_id),
        "stima": (contact_id, agency_id), "appointment": (agency_id, contact_id, contact_id),
        "acquisition": (agency_id, contact_id), "property": (agency_id, contact_id),
        "buy_request": (agency_id, contact_id),
    }
    for nome, sql, tabella in verifiche:
        if not _tabella(cur, tabella):
            continue
        cur.execute(sql, parametri[nome])
        riga = cur.fetchone()
        if riga is not None:
            return f"{'stima' if nome == 'stima_inviata' else nome}:{riga['ref']}"
    return None


def _relazione(candidati, now, *, ref, status, commercial_status, finita_il) -> None:
    if status == "lost":
        candidati.append((PAUSED_BY_COMMERCIAL, finita_il + DOPO_RELAZIONE_FINITA, f"{ref}:lost"))
        return
    if commercial_status in _COMMERCIALE_VIVO or (status is not None and status != "lost"
                                                   and commercial_status not in ("sold", "withdrawn", "archived")):
        candidati.append((PAUSED_BY_COMMERCIAL, None, ref))
    elif commercial_status == "sold":
        candidati.append((PAUSED_BY_COMMERCIAL, finita_il + DOPO_VENDITA, f"{ref}:sold"))
    elif commercial_status in ("withdrawn", "archived"):
        candidati.append((PAUSED_BY_COMMERCIAL, finita_il + DOPO_RELAZIONE_FINITA, f"{ref}:{commercial_status}"))


def prossimo_passo(passi: list[dict[str, Any]], *, trigger_sent_at: datetime, now: datetime,
                   ultimo_invio: datetime | None, timezone_name: str) -> tuple[dict[str, Any] | None, datetime | None]:
    """Il primo passo del calendario non ancora scaduto, e quando parte.

    Mai un passo gia' passato (niente arretrati), mai prima di 48 ore
    dall'ultimo invio, sempre dentro la finestra. `None` = il calendario e'
    finito: l'iscrizione si completa.
    """
    non_prima = now
    if ultimo_invio is not None and ultimo_invio + DISTANZA_MINIMA > non_prima:
        non_prima = ultimo_invio + DISTANZA_MINIMA
    for passo in sorted(passi, key=lambda p: p["step_no"]):
        if not passo.get("active", True):
            continue
        dovuto = trigger_sent_at + timedelta(seconds=passo["delay_seconds"])
        if dovuto < now:
            continue
        if dovuto - trigger_sent_at > ORIZZONTE:
            return None, None
        quando = send_window.next_allowed(max(dovuto, non_prima), passo["send_window"], timezone_name)
        return passo, quando
    return None, None


def oltre_orizzonte(trigger_sent_at: datetime, now: datetime) -> bool:
    return now - trigger_sent_at > ORIZZONTE


def oltre_orizzonte_passo(passo: dict[str, Any]) -> bool:
    """Il GIORNO DI CALENDARIO del passo e' oltre i 180? Si guarda il passo,
    non l'orologio: la finestra di invio puo' spostare l'ultima email al
    lunedi' successivo, e quella email e' dovuta."""
    return timedelta(seconds=passo["delay_seconds"]) > ORIZZONTE
