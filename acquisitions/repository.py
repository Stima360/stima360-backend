"""CRM-OPS-3 - SQL delle Acquisizioni, solo a livello di cursore.

Nessuna connessione, nessun commit: la transazione e' sempre del chiamante
(il service, oppure l'Agenda quando chiama gli hook di `integration.py`).
Ogni lettura per id porta `agency_id` nel WHERE: un id di un'altra agenzia
non esiste.

ORDINE DEI LOCK (nessuna attesa circolare con l'Agenda):
  * creazione:      immobile -> agenti (dentro l'Agenda) -> INSERT acquisizione
  * incarico:       immobile -> acquisizione
  * nuovo appunt.:  appuntamento vecchio -> acquisizione -> agenti
  * hook Agenda:    appuntamento -> agenti -> acquisizione
  * patch/stato:    acquisizione
"""
from __future__ import annotations

import json

from core.scope import scoped_source
from core.property_trash import PropertyInTrash

#: Le colonne di `acquisitions` restituite al chiamante.
COLUMNS = (
    "id", "agency_id", "property_id", "owner_contact_id", "assigned_agent_id",
    "appointment_id", "lead_id", "status", "lost_reason", "lost_notes", "lost_at",
    "acquired_at", "asking_price", "valuation_price", "sale_timing", "source", "notes",
    "created_by_user_id", "created_at", "updated_at", "version",
)

_INSERT_COLUMNS = (
    "agency_id", "property_id", "owner_contact_id", "assigned_agent_id",
    "appointment_id", "lead_id", "status", "asking_price", "valuation_price",
    "sale_timing", "source", "notes", "created_by_user_id",
)

_NOME_OPERATORE = ("COALESCE(NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                   " split_part({a}.email, '@', 1))")
_NOME_CONTATTO = ("COALESCE({a}.display_name,"
                  " NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), ''),"
                  " {a}.company_name)")


def _riga(r):
    return None if r is None else {c: r[c] for c in COLUMNS}


def _json(valore) -> str:
    return json.dumps(valore, default=str, sort_keys=True)


# ---------------------------------------------------------------------------
# IMMOBILE E PROPRIETARI
# ---------------------------------------------------------------------------

def lock_property(cur, agency_id: int, property_id: int):
    cur.execute(
        # CENSIMENTO-1 Fase 3: `record_kind` letto dal JSON della riga, cosi' la
        # statement resta eseguibile anche dove la 083 non e' applicata (NULL):
        # la guardia del censimento scatta solo quando la colonna c'e'.
        "SELECT id, agency_id, code, title, commercial_status, acquisition_id, archived_at, "
        "       asking_price, assigned_agent_id, to_jsonb(properties) ->> 'record_kind' AS record_kind, "
        "       to_jsonb(properties) ->> 'deleted_at' AS deleted_at "
        "  FROM properties WHERE id = %s AND agency_id = %s FOR UPDATE",
        (property_id, agency_id))
    r = cur.fetchone()
    if r is None:
        return None
    r = dict(r)
    # DELETE-ARCH Fase 2B2: nessuna acquisizione (ne' incarico) su un immobile
    # nel Cestino: 409 PROPERTY_IN_TRASH (la 086 lo ripete nel database).
    if r.pop("deleted_at") is not None:
        raise PropertyInTrash()
    return r


def property_owners(cur, agency_id: int, property_id: int, roles) -> list[dict]:
    """I proprietari REALI dell'immobile (`property_contacts`), uno per
    contatto: se un contatto e' sia owner sia seller compare una volta.
    CESTINO-CONTATTI-1: un contatto nel Cestino non e' un proprietario
    selezionabile (ne' referente)."""
    cur.execute(
        f"""
        SELECT c.id AS contact_id,
               {_NOME_CONTATTO.format(a='c')} AS display_name,
               c.phone, c.email,
               array_agg(DISTINCT pc.role ORDER BY pc.role) AS roles,
               bool_or(pc.is_primary) AS is_primary
          FROM property_contacts pc
          JOIN contacts c ON c.id = pc.contact_id AND c.agency_id = %s
         WHERE pc.property_id = %s AND pc.role = ANY(%s)
           AND (to_jsonb(c)->>'deleted_at') IS NULL
         GROUP BY c.id
         ORDER BY bool_or(pc.is_primary) DESC, c.id
        """,
        (agency_id, property_id, list(roles)))
    return [dict(r) for r in cur.fetchall()]


def open_for_property(cur, agency_id: int, property_id: int):
    cur.execute(
        "SELECT id FROM acquisitions WHERE agency_id = %s AND property_id = %s "
        "AND status NOT IN ('acquired', 'lost')",
        (agency_id, property_id))
    r = cur.fetchone()
    return None if r is None else r["id"]


# ---------------------------------------------------------------------------
# SCRITTURE
# ---------------------------------------------------------------------------

def record_event(cur, *, agency_id, acquisition_id, event_type, from_status, to_status,
                 actor_user_id, changes) -> None:
    """Una riga del registro append-only, nella transazione della modifica."""
    cur.execute(
        "INSERT INTO acquisition_events "
        "(agency_id, acquisition_id, event_type, from_status, to_status, actor_user_id, changes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
        (agency_id, acquisition_id, event_type, from_status, to_status, actor_user_id,
         _json(changes)))


def insert_acquisition(cur, values: dict) -> dict:
    colonne = [c for c in _INSERT_COLUMNS if c in values]
    cur.execute(
        f"INSERT INTO acquisitions ({', '.join(colonne)}) "
        f"VALUES ({', '.join('%s' for _ in colonne)}) RETURNING *",
        [values[c] for c in colonne])
    return _riga(cur.fetchone())


def mistakes_installed(cur) -> bool:
    """DELETE-ARCH Fase 1A: la 084 e' applicata (il CHECK dei motivi accetta
    `created_by_mistake`)."""
    cur.execute("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                "WHERE conname = 'acquisitions_lost_reason_chk' AND conrelid = 'acquisitions'::regclass")
    riga = cur.fetchone()
    return bool(riga and "created_by_mistake" in riga["d"])


def appointment_happened(cur, agency_id: int, acquisition_id: int) -> bool:
    """Un appuntamento di questa acquisizione e' AVVENUTO: un evento di
    svolgimento o di assenza, o un appuntamento sostituito che era svolto o
    mancato. Dal registro append-only, quindi anche dai predecessori."""
    cur.execute(
        "SELECT 1 FROM acquisition_events WHERE agency_id = %s AND acquisition_id = %s "
        "AND (event_type IN ('appointment_completed', 'appointment_no_show') "
        "     OR changes ->> 'old_appointment_status' IN ('completed', 'no_show') "
        "     OR changes ->> 'appointment_status' IN ('completed', 'no_show')) LIMIT 1",
        (agency_id, acquisition_id))
    return cur.fetchone() is not None


def installed(cur) -> bool:
    """La 081 e' applicata a QUESTO database? Lo chiedono solo gli hook
    dell'Agenda: un database senza `acquisitions` (la 081 non ancora
    applicata, o un banco di prova dell'Agenda che non la porta) non ha
    acquisizioni da seguire, e l'Agenda deve continuare a funzionare come
    prima. Nessuna cache: "assente" non si ricorda, la migration puo'
    arrivare mentre il processo gira."""
    cur.execute("SELECT to_regclass('public.acquisitions') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def lock_acquisition(cur, agency_id: int, acquisition_id: int):
    cur.execute("SELECT * FROM acquisitions WHERE id = %s AND agency_id = %s FOR UPDATE",
                (acquisition_id, agency_id))
    return _riga(cur.fetchone())


def lock_by_appointment(cur, agency_id: int, appointment_id: int):
    cur.execute("SELECT * FROM acquisitions WHERE appointment_id = %s AND agency_id = %s FOR UPDATE",
                (appointment_id, agency_id))
    return _riga(cur.fetchone())


def find_by_appointment(cur, agency_id: int, appointment_id: int):
    cur.execute("SELECT * FROM acquisitions WHERE appointment_id = %s AND agency_id = %s",
                (appointment_id, agency_id))
    return _riga(cur.fetchone())


def successor_appointment(cur, agency_id: int, appointment_id: int):
    """L'appuntamento nato dallo spostamento di questo (A30-1), o None."""
    cur.execute("SELECT id FROM appointments WHERE rescheduled_from_id = %s AND agency_id = %s",
                (appointment_id, agency_id))
    r = cur.fetchone()
    return None if r is None else r["id"]


def property_of(cur, agency_id: int, acquisition_id: int):
    """L'immobile di un'acquisizione, SENZA lock: serve a bloccare prima
    l'immobile (ordine immobile -> acquisizione), poi si ricontrolla."""
    cur.execute("SELECT property_id FROM acquisitions WHERE id = %s AND agency_id = %s",
                (acquisition_id, agency_id))
    r = cur.fetchone()
    return None if r is None else r["property_id"]


def update_acquisition(cur, acquisition_id: int, changes: dict) -> dict:
    """UPDATE sulla riga gia' bloccata; `version` +1 e `updated_at` qui."""
    colonne = sorted(changes)
    assegnazioni = [f"{c} = %s" for c in colonne] + ["version = version + 1", "updated_at = NOW()"]
    cur.execute(
        f"UPDATE acquisitions SET {', '.join(assegnazioni)} WHERE id = %s RETURNING *",
        [changes[c] for c in colonne] + [acquisition_id])
    return _riga(cur.fetchone())


def db_now(cur):
    cur.execute("SELECT NOW() AS adesso")
    return cur.fetchone()["adesso"]


# ---------------------------------------------------------------------------
# INCARICO (sullo STESSO cursore: `property.repository.update_property` apre
# una transazione propria e qui non si puo' usare)
# ---------------------------------------------------------------------------

def write_mandate(cur, agency_id: int, property_id: int, changes: dict) -> dict:
    colonne = sorted(changes)
    cur.execute(
        f"UPDATE properties SET {', '.join(f'{c} = %s' for c in colonne)}, updated_at = NOW() "
        "WHERE id = %s AND agency_id = %s RETURNING *",
        [changes[c] for c in colonne] + [property_id, agency_id])
    r = cur.fetchone()
    return None if r is None else dict(r)


def property_status_history(cur, property_id: int, *, old, new, note, changed_by) -> None:
    cur.execute(
        "INSERT INTO property_status_history"
        "(property_id, field_name, old_value, new_value, note, changed_by) "
        "VALUES (%s, 'commercial_status', %s, %s, %s, %s)",
        (property_id, old, new, note, changed_by))


def property_price_history(cur, property_id: int, *, old, new, reason, changed_by) -> None:
    cur.execute(
        "INSERT INTO property_price_history"
        "(property_id, old_price, new_price, change_reason, changed_by) "
        "VALUES (%s, %s, %s, %s, %s)",
        (property_id, old, new, reason, changed_by))


# ---------------------------------------------------------------------------
# LETTURE
# ---------------------------------------------------------------------------

_ELENCO_SQL = f"""
    SELECT a.id, a.status, a.property_id, a.owner_contact_id, a.assigned_agent_id,
           a.appointment_id, a.asking_price, a.valuation_price, a.updated_at, a.created_at,
           a.version, a.lost_reason, a.acquired_at,
           p.code AS property_code, p.title AS property_title, p.address AS property_address,
           p.civic_number AS property_civic_number, p.city AS property_city,
           {_NOME_CONTATTO.format(a='c')} AS owner_name,
           {_NOME_OPERATORE.format(a='u')} AS agent_name,
           ap.start_at AS appointment_start_at, ap.status AS appointment_status,
           GREATEST(a.updated_at, COALESCE(ev.last_at, a.updated_at)) AS last_activity_at
      FROM acquisitions a
      JOIN properties p        ON p.id = a.property_id AND p.agency_id = a.agency_id
                              AND (to_jsonb(p)->>'deleted_at') IS NULL   -- DELETE-ARCH 2B2
      JOIN contacts c          ON c.id = a.owner_contact_id AND c.agency_id = a.agency_id
      JOIN operator_users u    ON u.id = a.assigned_agent_id
      JOIN appointments ap     ON ap.id = a.appointment_id AND ap.agency_id = a.agency_id
      LEFT JOIN LATERAL (
           SELECT max(e.occurred_at) AS last_at FROM acquisition_events e
            WHERE e.acquisition_id = a.id) ev ON TRUE
     WHERE a.agency_id = %(agency)s
       -- DELETE-ARCH Fase 1A: le «create per errore» solo col filtro esplicito
       AND ((%(mistakes)s AND a.lost_reason = 'created_by_mistake')
            OR (NOT %(mistakes)s AND a.lost_reason IS DISTINCT FROM 'created_by_mistake'))
       AND (%(viewer)s::bigint IS NULL OR a.assigned_agent_id = %(viewer)s::bigint)
       AND (%(statuses)s::text[] IS NULL OR a.status = ANY(%(statuses)s::text[]))
       AND (%(agent)s::bigint IS NULL OR a.assigned_agent_id = %(agent)s::bigint)
       AND (%(from)s::timestamptz IS NULL OR ap.start_at >= %(from)s::timestamptz)
       AND (%(to)s::timestamptz IS NULL OR ap.start_at < %(to)s::timestamptz)
       AND (%(city)s::text IS NULL OR lower(p.city) = lower(%(city)s::text))
       AND (%(search)s::text IS NULL
            OR p.code ILIKE %(search)s OR p.title ILIKE %(search)s
            OR p.address ILIKE %(search)s OR p.city ILIKE %(search)s
            OR {_NOME_CONTATTO.format(a='c')} ILIKE %(search)s
            OR c.phone ILIKE %(search)s OR c.email ILIKE %(search)s)
     ORDER BY last_activity_at DESC, a.id DESC
     LIMIT %(limit)s OFFSET %(offset)s
"""


def _like(testo):
    pulito = testo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{pulito}%"


def list_acquisitions(cur, *, agency_id, only_agent_id, statuses, agent_id, date_from, date_to,
                      city, search, limit, offset, mistakes=False) -> list[dict]:
    cur.execute(_ELENCO_SQL, {
        "agency": agency_id, "viewer": only_agent_id, "mistakes": bool(mistakes),
        "statuses": list(statuses) if statuses else None, "agent": agent_id,
        "from": date_from, "to": date_to, "city": city or None,
        "search": _like(search) if search else None, "limit": limit, "offset": offset})
    return [dict(r) for r in cur.fetchall()]


def get_acquisition(cur, agency_id: int, acquisition_id: int):
    cur.execute("SELECT * FROM acquisitions WHERE id = %s AND agency_id = %s",
                (acquisition_id, agency_id))
    return _riga(cur.fetchone())


def property_summary(cur, agency_id: int, property_id: int):
    cur.execute(
        "SELECT id, code, title, address, civic_number, city, province, microzone, "
        "       property_type, commercial_status, acquisition_id, asking_price, "
        "       mandate_type, mandate_start, mandate_end "
        "  FROM properties WHERE id = %s AND agency_id = %s "
        "   AND (to_jsonb(properties)->>'deleted_at') IS NULL",   # DELETE-ARCH 2B2
        (property_id, agency_id))
    r = cur.fetchone()
    return None if r is None else dict(r)


def appointment_summary(cur, agency_id: int, appointment_id: int):
    cur.execute(
        f"""SELECT ap.id, ap.status, ap.appointment_type, ap.start_at, ap.end_at,
                   ap.assigned_user_id, ap.notes, ap.location_text, ap.version,
                   {_NOME_OPERATORE.format(a='u')} AS agent_name
              FROM appointments ap
              LEFT JOIN operator_users u ON u.id = ap.assigned_user_id
             WHERE ap.id = %s AND ap.agency_id = %s""",
        (appointment_id, agency_id))
    r = cur.fetchone()
    return None if r is None else dict(r)


def operator_name(cur, user_id: int):
    cur.execute(f"SELECT {_NOME_OPERATORE.format(a='u')} AS name FROM operator_users u WHERE u.id = %s",
                (user_id,))
    r = cur.fetchone()
    return None if r is None else r["name"]


def visible_lead(cur, ctx, lead_id: int) -> bool:
    """Il lead e' nello scope del chiamante (CORE: agenzia e, per un
    `agent`, solo i propri). Nessuna sincronizzazione: solo un riferimento."""
    sorgente, parametri = scoped_source(ctx, "leads", "l")
    cur.execute(f"SELECT 1 FROM {sorgente} AND l.id = %s", parametri + [lead_id])
    return cur.fetchone() is not None


def lead_summary(cur, agency_id: int, lead_id: int):
    cur.execute("SELECT id, pipeline, stage, status FROM leads WHERE id = %s AND agency_id = %s",
                (lead_id, agency_id))
    r = cur.fetchone()
    return None if r is None else dict(r)


def list_events(cur, agency_id: int, acquisition_id: int) -> list[dict]:
    cur.execute(
        f"""SELECT e.id, e.event_type, e.from_status, e.to_status, e.actor_user_id,
                   e.occurred_at, e.changes, {_NOME_OPERATORE.format(a='u')} AS actor_name
              FROM acquisition_events e
              LEFT JOIN operator_users u ON u.id = e.actor_user_id
             WHERE e.acquisition_id = %s AND e.agency_id = %s
             ORDER BY e.occurred_at, e.id""",
        (acquisition_id, agency_id))
    return [dict(r) for r in cur.fetchall()]
