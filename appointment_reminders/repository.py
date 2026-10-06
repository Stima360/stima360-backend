"""A32-2 - le letture dei promemoria. SOLO LETTURE, SOLO NELL'AGENZIA.

Ogni funzione riceve un cursore aperto dal chiamante e un `agency_id` che il
chiamante ha preso da un contesto autenticato (mai da una richiesta). Nessuna
scrittura, nessun commit: le scritture del promemoria sono UNA, ed e'
`communication.service.enqueue`.

COSA NON SI LEGGE, E NON PER DISCIPLINA

Le SELECT qui sotto nominano le colonne una per una. `location_text`, `notes`,
`outcome_note`, il telefono e le note del contatto non compaiono in nessuna:
cio' che non si legge non puo' finire in un messaggio per sbaglio.

IL FILTRO SQL E' GROSSOLANO, LA POLICY E' L'AUTORITA'

La query dei candidati restringe per agenzia, stato, tipo, fonte, inizio
futuro e contatto presente: e' un modo di non leggere righe inutili, non una
decisione. La decisione - idoneita', soglie 12h/3h, finestra, ritardo - la
prende `policy.is_reminder_eligible`, riga per riga.

L'ORIZZONTE DEI CANDIDATI NON E' POLICY

`CANDIDATE_HORIZON` (36h) dice fin dove guardare avanti, e basta. Il promemoria
piu' anticipato possibile e' quello di un appuntamento la cui ora civile del
giorno prima cade dalle 20:00 in poi: la finestra lo porta alle 19:00, cioe'
fino a ~5h prima del target nominale (23:59 -> 19:00), e il target nominale e'
24h +/- 1h (cambio d'ora) prima dell'inizio. Totale: al piu' ~30h prima
dell'inizio. Un appuntamento che inizia oltre 36h da adesso non e' dovuto in
nessun caso; i test lo provano ai bordi (DST, 19:00, 08:00).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import policy

#: Fin dove si guarda avanti. Vedi la docstring: >= ~30h + margine.
CANDIDATE_HORIZON = timedelta(hours=36)

#: Il tetto di righe per giro. Un giro che le raggiunge le conta tutte in
#: `scanned`, e le successive le trova il giro dopo (ordine totale).
MAX_CANDIDATES = 500

#: Le colonne dell'appuntamento che il promemoria puo' leggere. Nessun'altra.
APPOINTMENT_COLUMNS = ("id", "agency_id", "appointment_type", "status", "source",
                       "start_at", "created_at", "contact_id", "property_id")
#: Le colonne del contatto. NON il telefono, NON le note.
CONTACT_COLUMNS = ("id", "status", "email", "first_name")
#: Le colonne dell'immobile: l'indirizzo STRUTTURATO, e solo quello.
PROPERTY_COLUMNS = ("address", "civic_number", "city")

_CANDIDATI = f"""
SELECT {", ".join(f"a.{c}" for c in APPOINTMENT_COLUMNS)},
       {", ".join(f"c.{c} AS contact__{c}" for c in CONTACT_COLUMNS)},
       {", ".join(f"p.{c} AS property__{c}" for c in PROPERTY_COLUMNS)}
  FROM appointments a
  LEFT JOIN contacts c   ON c.id = a.contact_id  AND c.agency_id = a.agency_id
  LEFT JOIN properties p ON p.id = a.property_id AND p.agency_id = a.agency_id
                         AND (to_jsonb(p)->>'deleted_at') IS NULL
 WHERE a.agency_id = %(agency_id)s
   AND a.status = ANY(%(statuses)s)
   AND a.appointment_type = ANY(%(types)s)
   AND a.source = ANY(%(sources)s)
   AND a.contact_id IS NOT NULL
   AND a.start_at > %(now)s
   AND a.start_at <= %(horizon)s
 ORDER BY a.start_at ASC, a.id ASC
 LIMIT %(limit)s
"""

_UNO = f"""
SELECT {", ".join(f"a.{c}" for c in APPOINTMENT_COLUMNS)},
       {", ".join(f"c.{c} AS contact__{c}" for c in CONTACT_COLUMNS)}
  FROM appointments a
  JOIN agencies g        ON g.id = a.agency_id
  LEFT JOIN contacts c   ON c.id = a.contact_id AND c.agency_id = a.agency_id
 WHERE a.id = %(appointment_id)s
   AND a.agency_id = %(agency_id)s
"""


#: La migration che il giro pretende.
MIGRATION_079 = "079_a32_1_appointment_reminders"


def schema_ready(cur) -> bool:
    """La 079 e' applicata - e non rolled back - su QUESTO database?

    UNA query per giro, sul registro delle migration: la riga della 079 deve
    esserci con `rolled_back_at IS NULL`. Il runner scrive quella riga nella
    STESSA transazione del CHECK che ammette `appointment_reminder`, e un down
    la toglie: registro e vincolo non si separano. Nessuna cache: un "no"
    memorizzato sopravviverebbe alla migration.
    """
    cur.execute("SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = %s "
                "AND rolled_back_at IS NULL) AS pronta", (MIGRATION_079,))
    return bool(cur.fetchone()["pronta"])


def agency_name(cur, agency_id: int) -> str | None:
    cur.execute("SELECT name FROM agencies WHERE id = %s", (agency_id,))
    riga = cur.fetchone()
    return None if riga is None else riga["name"]


def _separa(riga: dict[str, Any]) -> dict[str, Any]:
    """Una riga piatta -> {'appointment', 'contact', 'property_address'}."""
    appuntamento = {c: riga[c] for c in APPOINTMENT_COLUMNS}
    # `contacts.status` e' NOT NULL: e' valorizzato se e solo se il LEFT JOIN
    # ha trovato il contatto NELLA STESSA AGENZIA. Altrimenti: nessun contatto.
    contatto = None
    if riga.get("contact__status") is not None:
        contatto = {c: riga[f"contact__{c}"] for c in CONTACT_COLUMNS}
    indirizzo = None
    if any(riga.get(f"property__{c}") for c in PROPERTY_COLUMNS):
        indirizzo = {c: riga[f"property__{c}"] for c in PROPERTY_COLUMNS
                     if riga.get(f"property__{c}") is not None}
    return {"appointment": appuntamento, "contact": contatto, "property_address": indirizzo}


def candidates(cur, agency_id: int, *, now: datetime,
               limit: int = MAX_CANDIDATES) -> list[dict[str, Any]]:
    """Gli appuntamenti dell'agenzia da valutare adesso, in ordine totale."""
    cur.execute(_CANDIDATI, {
        "agency_id": agency_id,
        "statuses": sorted(policy.ALLOWED_STATUSES),
        "types": sorted(policy.ALLOWED_TYPES),
        "sources": sorted(policy.ALLOWED_SOURCES),
        "now": now,
        "horizon": now + CANDIDATE_HORIZON,
        "limit": limit,
    })
    return [_separa(dict(r)) for r in cur.fetchall()]


def appointment_for_revalidation(cur, agency_id: int,
                                 appointment_id: int) -> dict[str, Any] | None:
    """L'appuntamento e il suo contatto, ADESSO, nell'agenzia. None se non c'e'."""
    cur.execute(_UNO, {"appointment_id": appointment_id, "agency_id": agency_id})
    riga = cur.fetchone()
    if riga is None:
        return None
    separata = _separa(dict(riga))
    return {"appointment": separata["appointment"], "contact": separata["contact"]}
