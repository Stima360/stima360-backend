"""A30-1/A30-2 - accesso al database per l'Agenda.

Ogni funzione riceve il cursore: la transazione la apre e la chiude il
service, una sola per operazione, cosi' lock, controllo e scrittura stanno
insieme o non stanno.

ORDINE DEI LOCK (unico per tutti i percorsi di scrittura dell'Agenda):

    1. le righe di `appointments` coinvolte (`SELECT ... FOR UPDATE`);
    2. i lock consultivi degli agenti, in ordine di id crescente;
    3. (solo con la proiezione, A30-2P) la riga `stime` e poi la riga
       `stima_inspections`, prese dalle varianti LMC-15.

NON legge MAI `stime_dettagliate` (D10) ne' `property_visits` (A30-2). Di
`stime` legge solo l'agenzia e il riepilogo per il pannello, in sola lettura;
A30-7 vi prende in piu' il lock di riga (`lock_stima`, FOR UPDATE, nessuna
scrittura) che serializza i sopralluoghi della stessa stima.

Nessun percorso prende un lock di riga dopo un lock di agente: due
transazioni non possono aspettarsi a vicenda.
"""
from __future__ import annotations

import json

from .enums import BLOCKING_STATUSES

#: Le colonne restituite al chiamante. `blocked_range` resta interna.
APPOINTMENT_COLUMNS = (
    "id", "agency_id", "assigned_user_id", "appointment_type", "status",
    "start_at", "end_at", "timezone", "buffer_before_minutes", "buffer_after_minutes",
    "stima_id", "contact_id", "lead_id", "property_id", "stima_inspection_id",
    "location_text", "notes", "source", "source_record_id", "test_run_id",
    "rescheduled_from_id",
    "google_calendar_id", "google_event_id", "google_sync_status", "google_last_synced_at",
    "created_by_user_id", "created_at", "updated_at", "version",
    "confirmed_at", "completed_at", "no_show_at", "cancelled_at", "cancelled_reason",
    "rescheduled_at",
)

#: Cio' che un conflitto rivela: quando e che cosa, non chi. Quanto mostrare
#: del cliente di un collega e' una decisione di visibilita' (A30-2).
CONFLICT_COLUMNS = ("id", "appointment_type", "status", "start_at", "end_at")

_AGENT_LOCK_NAMESPACE = "appointments:agent:"

_INSERT_COLUMNS = (
    "agency_id", "assigned_user_id", "appointment_type", "status",
    "start_at", "end_at", "buffer_before_minutes", "buffer_after_minutes",
    "stima_id", "contact_id", "lead_id", "property_id",
    "location_text", "notes", "source", "source_record_id", "test_run_id",
    "rescheduled_from_id", "created_by_user_id", "confirmed_at",
    # A30-2P: solo il backfill da `stima_inspections` inserisce righe gia'
    # collegate o gia' chiuse. Il service non passa mai queste chiavi.
    "stima_inspection_id", "completed_at", "cancelled_at", "cancelled_reason",
)


def _riga(riga):
    return {c: riga[c] for c in APPOINTMENT_COLUMNS}


def record_event(cur, *, agency_id, appointment_id, event_type, from_status,
                 to_status, actor_user_id, changes) -> None:
    """Una riga del registro, nella transazione della modifica.

    La scrive il repository e non un trigger: `appointment_events` e' una
    tabella di tenant, e la certificazione P26-6 (serie 100) pretende che
    ogni scrittura in una tabella di tenant sia nominata in un sorgente
    Python. `changes` contiene solo cio' che e' cambiato.
    """
    cur.execute(
        "INSERT INTO appointment_events "
        "(agency_id, appointment_id, event_type, from_status, to_status, "
        " actor_user_id, changes) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
        (agency_id, appointment_id, event_type, from_status, to_status,
         actor_user_id, json.dumps(changes, default=str, sort_keys=True)),
    )


def lock_agents(cur, user_ids) -> None:
    """Serializza le prenotazioni DELLO STESSO agente, in ordine crescente.

    Il vincolo EXCLUDE vede solo `appointments`. Questo lock copre anche le
    fonti che il vincolo non vede (visite acquirente, impegni Google nelle
    fasi successive): chi controlla e poi scrive lo fa da solo, per
    quell'agente, fino al commit.
    """
    for user_id in sorted({int(u) for u in user_ids if u is not None}):
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
            (f"{_AGENT_LOCK_NAMESPACE}{user_id}",),
        )


def find_conflicts(cur, *, assigned_user_id, start_at, end_at,
                   buffer_before_minutes=0, buffer_after_minutes=0,
                   exclude_appointment_id=None) -> list[dict]:
    """Gli appuntamenti che bloccano `assigned_user_id` nell'intervallo dato.

    Stessa regola del vincolo EXCLUDE, per costruzione: stessi stati,
    stesso intervallo semiaperto con i buffer, calcolato dal DATABASE con
    la stessa espressione del trigger. Nessun filtro di agenzia: un agente e'
    una persona sola, e due appuntamenti non possono occuparla insieme.
    """
    cur.execute(
        f"""
        SELECT {", ".join(CONFLICT_COLUMNS)}
          FROM appointments
         WHERE assigned_user_id = %(user)s
           AND status = ANY(%(bloccanti)s)
           AND blocked_range && tstzrange(
                   %(start)s::timestamptz - make_interval(mins => %(before)s),
                   %(end)s::timestamptz   + make_interval(mins => %(after)s),
                   '[)')
           AND (%(escluso)s::bigint IS NULL OR id <> %(escluso)s::bigint)
         ORDER BY start_at, id
        """,
        {"user": assigned_user_id, "bloccanti": list(BLOCKING_STATUSES),
         "start": start_at, "end": end_at,
         "before": int(buffer_before_minutes), "after": int(buffer_after_minutes),
         "escluso": exclude_appointment_id},
    )
    return [{c: r[c] for c in CONFLICT_COLUMNS} for r in cur.fetchall()]


def lock_stima(cur, agency_id: int, stima_id: int) -> bool:
    """A30-7 D5: `stime` FOR UPDATE dentro l'agenzia - la stessa riga e lo
    stesso lock che la proiezione LMC-15 prende poco dopo
    (`acquisition.repository._blocca_stima`). Serializza due pianificazioni di
    sopralluogo sulla stessa stima. Nell'ORDINE DEI LOCK viene dopo la riga
    `appointments` e gli agenti. Vero se la stima c'e'."""
    cur.execute("SELECT id FROM stime WHERE id = %s AND agency_id = %s FOR UPDATE",
                (stima_id, agency_id))
    return cur.fetchone() is not None


def open_inspection_for_stima(cur, agency_id: int, stima_id: int, *, statuses,
                              exclude_appointment_id=None):
    """A30-7 D5: un altro sopralluogo della stessa stima ancora aperto (uno
    degli stati `statuses`, quelli NON terminali della macchina a stati), o
    None. Da chiamare DOPO `lock_stima`."""
    cur.execute(
        """
        SELECT id, assigned_user_id, status
          FROM appointments
         WHERE agency_id = %(agency)s
           AND stima_id = %(stima)s
           AND appointment_type = 'inspection'
           AND status = ANY(%(stati)s)
           AND (%(escluso)s::bigint IS NULL OR id <> %(escluso)s::bigint)
         ORDER BY id
         LIMIT 1
        """,
        {"agency": agency_id, "stima": stima_id, "stati": list(statuses),
         "escluso": exclude_appointment_id},
    )
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def stima_agency(cur, stima_id: int):
    """L'agenzia della stima, o None se la stima non esiste.

    Il controllo di appartenenza di una stima: il database non la referenzia
    (nessuna FK, decisione Q-A6b), quindi l'appartenenza si verifica qui, una
    volta, quando il riferimento viene scritto. L'altra lettura di `stime`
    dell'Agenda e' la ricerca di `lookup_stime` (A30-5), in sola lettura.
    """
    cur.execute("SELECT agency_id FROM stime WHERE id = %s", (stima_id,))
    riga = cur.fetchone()
    return None if riga is None else riga["agency_id"]


#: A30-5 - le SOLE colonne di `stime` che la ricerca restituisce: cio' che
#: serve a riconoscere una stima (chi, dove, cosa, quando). Niente email,
#: telefono o consensi; niente `stime_dettagliate`. Tutte presenti su TEST
#: (docs/P26_BASELINE_CERTIFICATE_TEST.md §3.0.2: di `stime` mancano solo
#: `lead_status` e `note_internal`).
STIMA_LOOKUP_COLUMNS = ("id", "data", "nome", "cognome", "comune", "microzona", "via",
                        "civico", "tipologia", "mq")


def _like(testo: str) -> str:
    """Il testo dell'operatore come sottostringa letterale: `%` e `_` non
    diventano caratteri jolly."""
    return "%" + testo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def lookup_stime(cur, agency_id: int, *, search=None, lead_id=None, contact_id=None,
                 limit: int = 10):
    """A30-5: le stime DI QUESTA AGENZIA per il collegamento manuale di un
    appuntamento. Sola lettura.

    `lead_id` / `contact_id` restringono alle stime che la relazione CORE
    autorevole (`lead_stime`) lega a quel lead o ai lead di quel cliente - e il
    lead deve essere della stessa agenzia: un id di un'altra agenzia non
    restringe a niente, restituisce zero righe.
    """
    condizioni = ["s.agency_id = %(agenzia)s"]
    parametri = {"agenzia": agency_id, "limite": limit}
    if search:
        condizioni.append(
            "(concat_ws(' ', s.nome, s.cognome) ILIKE %(testo)s"
            " OR concat_ws(' ', s.cognome, s.nome) ILIKE %(testo)s"
            " OR s.comune ILIKE %(testo)s OR s.microzona ILIKE %(testo)s"
            " OR concat_ws(' ', s.via, s.civico) ILIKE %(testo)s)")
        parametri["testo"] = _like(search)
    if lead_id is not None:
        condizioni.append(
            "EXISTS (SELECT 1 FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id"
            " WHERE ls.stima_id = s.id AND l.id = %(lead)s AND l.agency_id = %(agenzia)s)")
        parametri["lead"] = lead_id
    if contact_id is not None:
        condizioni.append(
            "EXISTS (SELECT 1 FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id"
            " WHERE ls.stima_id = s.id AND l.contact_id = %(contatto)s"
            " AND l.agency_id = %(agenzia)s)")
        parametri["contatto"] = contact_id
    colonne = ", ".join(f"s.{c}" for c in STIMA_LOOKUP_COLUMNS)
    cur.execute(
        f"SELECT {colonne} FROM stime s WHERE {' AND '.join(condizioni)}"
        " ORDER BY s.data DESC NULLS LAST, s.id DESC LIMIT %(limite)s",
        parametri,
    )
    return [{c: r[c] for c in STIMA_LOOKUP_COLUMNS} for r in cur.fetchall()]


def active_membership(cur, agency_id: int, user_id: int) -> bool:
    cur.execute(
        "SELECT 1 FROM agency_memberships "
        "WHERE agency_id = %s AND operator_user_id = %s AND status = 'active'",
        (agency_id, user_id),
    )
    return cur.fetchone() is not None


def insert_appointment(cur, values: dict, *, actor_user_id) -> dict | None:
    """Inserisce e registra l'evento `created`.

    Con `source_record_id` l'INSERT e' IDEMPOTENTE (A30-2 §6): se la chiave
    esiste gia' non inserisce nulla e restituisce None. L'indice unico
    parziale della 072 decide, anche fra due transazioni concorrenti.
    """
    colonne = [c for c in _INSERT_COLUMNS if c in values]
    conflitto = ""
    if values.get("source_record_id") is not None:
        conflitto = (" ON CONFLICT (source, source_record_id) "
                     "WHERE source_record_id IS NOT NULL DO NOTHING")
    cur.execute(
        f"INSERT INTO appointments ({', '.join(colonne)}) "
        f"VALUES ({', '.join('%s' for _ in colonne)}){conflitto} RETURNING *",
        [values[c] for c in colonne],
    )
    trovata = cur.fetchone()
    if trovata is None:
        return None
    riga = _riga(trovata)
    record_event(
        cur, agency_id=riga["agency_id"], appointment_id=riga["id"],
        event_type="created", from_status=None, to_status=riga["status"],
        actor_user_id=actor_user_id,
        changes={c: riga[c] for c in colonne if riga[c] is not None})
    return riga


def lock_appointment(cur, agency_id: int, appointment_id: int):
    """La riga, bloccata, solo se e' di questa agenzia; altrimenti None."""
    cur.execute(
        "SELECT * FROM appointments WHERE id = %s AND agency_id = %s FOR UPDATE",
        (appointment_id, agency_id),
    )
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def mark_rescheduled(cur, appointment_id: int, *, from_status, actor_user_id) -> None:
    cur.execute(
        "UPDATE appointments SET status = 'rescheduled', rescheduled_at = NOW() "
        "WHERE id = %s RETURNING agency_id, rescheduled_at",
        (appointment_id,),
    )
    riga = cur.fetchone()
    record_event(
        cur, agency_id=riga["agency_id"], appointment_id=appointment_id,
        event_type="status_changed", from_status=from_status, to_status="rescheduled",
        actor_user_id=actor_user_id,
        changes={"status": {"da": from_status, "a": "rescheduled"},
                 "rescheduled_at": {"a": riga["rescheduled_at"]}})


# ---------------------------------------------------------------------------
# A30-2 - IDEMPOTENZA
# ---------------------------------------------------------------------------

def find_by_source_key(cur, source: str, source_record_id: str):
    """La riga con quella chiave, IN QUALUNQUE AGENZIA: l'indice e' globale.
    Chi la usa deve confrontare agenzia e attore prima di rivelare qualcosa."""
    cur.execute(
        "SELECT * FROM appointments WHERE source = %s AND source_record_id = %s",
        (source, source_record_id),
    )
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def created_event_changes(cur, appointment_id: int) -> dict | None:
    """I valori inseriti, dall'evento `created`: l'impronta della richiesta
    originale, senza una colonna in piu' (A30-2 §6)."""
    cur.execute(
        "SELECT changes FROM appointment_events "
        "WHERE appointment_id = %s AND event_type = 'created' ORDER BY id LIMIT 1",
        (appointment_id,),
    )
    riga = cur.fetchone()
    return None if riga is None else dict(riga["changes"])


# ---------------------------------------------------------------------------
# A30-2 - SCRITTURE SU UNA RIGA ESISTENTE
# ---------------------------------------------------------------------------

def update_appointment(cur, appointment_id: int, changes: dict, *, actor_user_id,
                       event_type: str, from_status: str, azione: str,
                       event_extra: dict | None = None) -> dict:
    """UPDATE di `changes` sulla riga (gia' bloccata dal chiamante) e il suo
    evento, nella stessa transazione. `version` e `updated_at` li scrive il
    trigger della 072.

    A30-8: `event_extra` aggiunge all'evento dati che NON sono colonne (la
    nota di esito, l'id del task di follow-up); non puo' riscrivere le chiavi
    delle colonne cambiate ne' `azione`."""
    colonne = sorted(changes)
    cur.execute("SELECT * FROM appointments WHERE id = %s", (appointment_id,))
    prima = dict(cur.fetchone())
    cur.execute(
        f"UPDATE appointments SET {', '.join(f'{c} = %s' for c in colonne)} "
        "WHERE id = %s RETURNING *",
        [changes[c] for c in colonne] + [appointment_id],
    )
    riga = _riga(cur.fetchone())
    diff = {c: {"da": prima[c], "a": riga[c]} for c in colonne if prima[c] != riga[c]}
    diff["azione"] = azione
    for chiave, valore in (event_extra or {}).items():
        if chiave in diff:
            raise ValueError(f"event_extra non puo' riscrivere {chiave!r}")
        diff[chiave] = valore
    record_event(
        cur, agency_id=riga["agency_id"], appointment_id=appointment_id,
        event_type=event_type, from_status=from_status, to_status=riga["status"],
        actor_user_id=actor_user_id, changes=diff)
    return riga


# ---------------------------------------------------------------------------
# A30-2 - LETTURE
# ---------------------------------------------------------------------------

_NOME_OPERATORE = ("NULLIF(BTRIM(CONCAT_WS(' ', {a}.first_name, {a}.last_name)), '')")


def agents(cur, agency_id: int) -> list[dict]:
    """I membri ATTIVI dell'agenzia, per filtro e assegnazione."""
    cur.execute(
        f"""
        SELECT u.id, m.role,
               COALESCE({_NOME_OPERATORE.format(a='u')}, split_part(u.email, '@', 1)) AS name
          FROM agency_memberships m
          JOIN operator_users u ON u.id = m.operator_user_id
         WHERE m.agency_id = %s AND m.status = 'active'
         ORDER BY name, u.id
        """,
        (agency_id,),
    )
    return [dict(r) for r in cur.fetchall()]


_CALENDARIO_SQL = f"""
    SELECT a.id, a.status, a.appointment_type, a.start_at, a.end_at,
           a.assigned_user_id, a.version, a.source, a.test_run_id,
           a.stima_id, a.contact_id, a.lead_id, a.property_id, a.location_text,
           a.buffer_before_minutes, a.buffer_after_minutes,
           COALESCE({_NOME_OPERATORE.format(a='u')}, split_part(u.email, '@', 1)) AS agent_name,
           COALESCE(c.display_name,
                    NULLIF(BTRIM(CONCAT_WS(' ', c.first_name, c.last_name)), ''),
                    c.company_name) AS contact_name,
           p.city AS property_city
      FROM appointments a
      LEFT JOIN operator_users u ON u.id = a.assigned_user_id
      LEFT JOIN contacts c       ON c.id = a.contact_id
      LEFT JOIN properties p     ON p.id = a.property_id
     WHERE a.agency_id = %(agency)s
       AND a.start_at < %(to)s AND a.end_at > %(from)s
       AND a.status = ANY(%(statuses)s)
       AND (%(types)s::text[] IS NULL OR a.appointment_type = ANY(%(types)s::text[]))
       AND (%(agents)s::bigint[] IS NULL OR a.assigned_user_id = ANY(%(agents)s::bigint[]))
       AND (%(viewer)s::bigint IS NULL OR a.assigned_user_id = %(viewer)s::bigint)
     ORDER BY a.start_at, a.id
"""

_OCCUPATO_SQL = f"""
    SELECT a.assigned_user_id, a.start_at, a.end_at,
           COALESCE({_NOME_OPERATORE.format(a='u')}, split_part(u.email, '@', 1)) AS agent_name
      FROM appointments a
      JOIN operator_users u ON u.id = a.assigned_user_id
     WHERE a.agency_id = %(agency)s
       AND a.start_at < %(to)s AND a.end_at > %(from)s
       AND a.status = ANY(%(bloccanti)s)
       AND a.assigned_user_id <> %(viewer)s
       AND (%(agents)s::bigint[] IS NULL OR a.assigned_user_id = ANY(%(agents)s::bigint[]))
     ORDER BY a.start_at, a.assigned_user_id
"""


def calendar_rows(cur, *, agency_id, date_from, date_to, statuses, types, agent_ids,
                  only_agent_id):
    """UNA query per l'intervallo: nessun N+1. `only_agent_id` limita alle
    righe di quell'agente (visibilita' dell'agent, D4/D6)."""
    cur.execute(_CALENDARIO_SQL, {
        "agency": agency_id, "from": date_from, "to": date_to,
        "statuses": list(statuses), "types": list(types) if types else None,
        "agents": list(agent_ids) if agent_ids else None, "viewer": only_agent_id,
    })
    return [dict(r) for r in cur.fetchall()]


def colleague_busy_rows(cur, *, agency_id, date_from, date_to, viewer_id, agent_ids):
    """Gli impegni dei COLLEGHI che bloccano, SENZA nessun dato del cliente:
    solo agente e orario (D4). Nessun tipo, nessuna nota, nessun id."""
    cur.execute(_OCCUPATO_SQL, {
        "agency": agency_id, "from": date_from, "to": date_to,
        "bloccanti": list(BLOCKING_STATUSES), "viewer": viewer_id,
        "agents": list(agent_ids) if agent_ids else None,
    })
    return [dict(r) for r in cur.fetchall()]


def busy_intervals(cur, *, assigned_user_id, date_from, date_to, exclude_appointment_id=None):
    """Gli intervalli BLOCCATI (buffer inclusi) di un agente nella finestra:
    l'ingresso delle funzioni pure di `availability`."""
    cur.execute(
        """
        SELECT lower(blocked_range) AS b_start, upper(blocked_range) AS b_end
          FROM appointments
         WHERE assigned_user_id = %(user)s
           AND status = ANY(%(bloccanti)s)
           AND blocked_range && tstzrange(%(from)s, %(to)s, '[)')
           AND (%(escluso)s::bigint IS NULL OR id <> %(escluso)s::bigint)
         ORDER BY 1
        """,
        {"user": assigned_user_id, "bloccanti": list(BLOCKING_STATUSES),
         "from": date_from, "to": date_to, "escluso": exclude_appointment_id},
    )
    return [(r["b_start"], r["b_end"]) for r in cur.fetchall()]


def get_appointment(cur, agency_id: int, appointment_id: int):
    cur.execute("SELECT * FROM appointments WHERE id = %s AND agency_id = %s",
                (appointment_id, agency_id))
    riga = cur.fetchone()
    return None if riga is None else _riga(riga)


def agent_name(cur, agency_id: int, user_id: int):
    """A30-8: il nome dell'agente come lo mostra l'Agenda (stessa espressione
    di `agents`), per `tasks.assigned_to` del follow-up. Solo informativo."""
    cur.execute(
        f"""SELECT COALESCE({_NOME_OPERATORE.format(a='u')},
                            split_part(u.email, '@', 1)) AS name
              FROM operator_users u
              JOIN agency_memberships m
                ON m.operator_user_id = u.id AND m.agency_id = %s
             WHERE u.id = %s""",
        (agency_id, user_id))
    riga = cur.fetchone()
    return None if riga is None else riga["name"]


def detail_links(cur, row: dict) -> dict:
    """I riepiloghi collegati per il pannello. Letture puntuali per UNA riga
    (il dettaglio si apre uno alla volta): mai nella lista del calendario."""
    esito = {"contact": None, "lead": None, "property": None, "stima": None, "agent": None}
    agenzia = row["agency_id"]
    if row["contact_id"] is not None:
        cur.execute(
            """SELECT id, COALESCE(display_name,
                        NULLIF(BTRIM(CONCAT_WS(' ', first_name, last_name)), ''),
                        company_name) AS display_name,
                      phone, phone_normalized, email
                 FROM contacts WHERE id = %s AND agency_id = %s""",
            (row["contact_id"], agenzia))
        r = cur.fetchone()
        esito["contact"] = None if r is None else dict(r)
    if row["lead_id"] is not None:
        cur.execute("SELECT id, pipeline, stage, status FROM leads "
                    "WHERE id = %s AND agency_id = %s", (row["lead_id"], agenzia))
        r = cur.fetchone()
        esito["lead"] = None if r is None else dict(r)
    if row["property_id"] is not None:
        cur.execute("SELECT id, title, address, civic_number, city FROM properties "
                    "WHERE id = %s AND agency_id = %s", (row["property_id"], agenzia))
        r = cur.fetchone()
        esito["property"] = None if r is None else dict(r)
    if row["stima_id"] is not None:
        # Riferimento morbido: la stima puo' non esserci piu'.
        cur.execute("SELECT id, comune, microzona, via, civico, tipologia, mq, data "
                    "FROM stime WHERE id = %s AND agency_id = %s", (row["stima_id"], agenzia))
        r = cur.fetchone()
        esito["stima"] = None if r is None else dict(r)
    if row["assigned_user_id"] is not None:
        cur.execute(
            f"""SELECT u.id, COALESCE({_NOME_OPERATORE.format(a='u')},
                                      split_part(u.email, '@', 1)) AS name,
                       (m.status = 'active') AS active
                  FROM operator_users u
                  LEFT JOIN agency_memberships m
                         ON m.operator_user_id = u.id AND m.agency_id = %s
                 WHERE u.id = %s""",
            (agenzia, row["assigned_user_id"]))
        r = cur.fetchone()
        esito["agent"] = None if r is None else dict(r)
    return esito


def list_events(cur, appointment_id: int) -> list[dict]:
    cur.execute(
        "SELECT id, event_type, from_status, to_status, actor_user_id, occurred_at, changes "
        "FROM appointment_events WHERE appointment_id = %s ORDER BY id",
        (appointment_id,))
    return [dict(r) for r in cur.fetchall()]


def list_appointments(cur, *, agency_id, statuses, types, stima_id, lead_id, contact_id,
                      property_id, date_from, date_to, only_agent_id, limit, offset):
    cur.execute(
        """
        SELECT * FROM appointments
         WHERE agency_id = %(agency)s
           AND status = ANY(%(statuses)s)
           AND (%(types)s::text[] IS NULL OR appointment_type = ANY(%(types)s::text[]))
           AND (%(stima)s::int IS NULL OR stima_id = %(stima)s::int)
           AND (%(lead)s::bigint IS NULL OR lead_id = %(lead)s::bigint)
           AND (%(contact)s::bigint IS NULL OR contact_id = %(contact)s::bigint)
           AND (%(property)s::bigint IS NULL OR property_id = %(property)s::bigint)
           AND (%(from)s::timestamptz IS NULL OR end_at > %(from)s::timestamptz)
           AND (%(to)s::timestamptz IS NULL OR start_at < %(to)s::timestamptz)
           AND (%(viewer)s::bigint IS NULL OR assigned_user_id = %(viewer)s::bigint)
         ORDER BY start_at, id
         LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"agency": agency_id, "statuses": list(statuses),
         "types": list(types) if types else None, "stima": stima_id, "lead": lead_id,
         "contact": contact_id, "property": property_id, "from": date_from, "to": date_to,
         "viewer": only_agent_id, "limit": limit, "offset": offset})
    return [_riga(r) for r in cur.fetchall()]


def link_agency(cur, table: str, record_id: int):
    """L'agenzia di un contatto, lead o immobile (per errori puliti prima del
    trigger della 072, che resta la garanzia)."""
    if table not in ("contacts", "leads", "properties"):
        raise ValueError(table)
    colonne = "agency_id, contact_id" if table == "leads" else "agency_id"
    cur.execute(f"SELECT {colonne} FROM {table} WHERE id = %s", (record_id,))
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def set_inspection_link(cur, appointment_id: int, inspection_id: int | None) -> None:
    """Il collegamento alla riga LMC-15 (solo proiezione, A30-2P)."""
    cur.execute("UPDATE appointments SET stima_inspection_id = %s WHERE id = %s",
                (inspection_id, appointment_id))


def db_now(cur):
    """NOW() della transazione corrente: l'"adesso" delle mutazioni che
    scrivono `completed_at`, lo stesso istante che LMC-15 registra come
    `completed_recorded_at` (NOW() e' fisso per tutta la transazione)."""
    cur.execute("SELECT NOW() AS adesso")
    return cur.fetchone()["adesso"]
