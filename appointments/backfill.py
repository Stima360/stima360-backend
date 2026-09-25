"""A30-2P - il backfill di `stima_inspections` (LMC-15) verso `appointments`.

Porta nell'Agenda i sopralluoghi che LMC-15 ha gia' registrato, cosi' che
`appointments` diventi la fonte autorevole anche per il passato. Regole:

* SOLO LETTURA su `stima_inspections` e `stime`: il backfill non modifica,
  non cancella e non inserisce mai una riga LMC-15 (obiettivo 4). Scrive solo
  `appointments` e il loro evento `created` in `appointment_events`.
* IDEMPOTENTE: la chiave e' `source = 'stima_inspections_backfill'`,
  `source_record_id = 'stima_inspections:<id>'`, deterministica e stabile.
  L'indice unico `(source, source_record_id)` della 072 e quello su
  `stima_inspection_id` rendono impossibile un doppione anche tra due corse
  concorrenti; in piu' un lock consultivo mette le corse in fila.
* Nessuna inferenza (Q4): l'agente resta NULL. Per questo una riga LMC-15
  `scheduled` diventa `requested` (la 072 pretende l'agente per `scheduled`):
  e' collegata alla sua riga LMC-15 e, quando qualcuno la fissa con un
  agente, la proiezione riusa QUELLA riga (projection.on_schedule).
* `created_by_user_id` NULL (= import di sistema): chi aveva creato la riga
  LMC-15 resta scritto nella riga LMC-15, e ripeterlo come autore
  dell'appuntamento fallirebbe per un operatore oggi non piu' attivo.
* Nessun accesso a `stime_dettagliate`, `property_visits` o Google.

Il chiamante possiede la transazione: `run_backfill(cur, apply=False)` non
scrive nulla e restituisce il piano; con `apply=True` inserisce, e il commit
(o il rollback) resta al chiamante.
"""
from __future__ import annotations

from datetime import timedelta

from . import repository
from .enums import default_duration_minutes

SOURCE = "stima_inspections_backfill"
KEY_PREFIX = "stima_inspections:"
APPOINTMENT_TYPE = "inspection"
LOCK_KEY = "appointments:backfill:stima_inspections"

#: Lo stato LMC-15 -> lo stato dell'Agenda.
STATUS_MAP = {
    "scheduled": "requested",   # nessun agente noto: la 072 non ammette scheduled
    "completed": "completed",
    "cancelled": "cancelled",
}


def source_key(inspection_id: int) -> str:
    """La chiave d'idempotenza: stabile, deterministica, leggibile."""
    return f"{KEY_PREFIX}{int(inspection_id)}"


def map_inspection(row: dict) -> dict:
    """Riga LMC-15 (+ `agency_id` della stima) -> valori di `appointments`.

    Pura: nessun accesso al database. `start_reconstructed` dice se l'inizio
    e' stato preso da `completed_at` (sopralluogo registrato a posteriori,
    mai fissato: LMC-15 non ha `scheduled_for`) - l'Agenda pretende un inizio.
    """
    stato = STATUS_MAP.get(row["status"])
    if stato is None:
        raise ValueError(f"stato LMC-15 sconosciuto: {row['status']!r}")
    inizio = row["scheduled_for"]
    ricostruito = False
    if inizio is None:
        inizio = row["completed_at"]
        ricostruito = True
    if inizio is None:
        raise ValueError(f"sopralluogo {row['id']}: nessun istante da cui partire")
    valori = {
        "agency_id": row["agency_id"],
        "assigned_user_id": None,
        "appointment_type": APPOINTMENT_TYPE,
        "status": stato,
        "start_at": inizio,
        "end_at": inizio + timedelta(minutes=default_duration_minutes(APPOINTMENT_TYPE)),
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "stima_id": row["stima_id"],
        "stima_inspection_id": row["id"],
        "source": SOURCE,
        "source_record_id": source_key(row["id"]),
        "created_by_user_id": None,
    }
    if stato == "completed":
        valori["completed_at"] = row["completed_at"]
    elif stato == "cancelled":
        valori["cancelled_at"] = row["cancelled_at"]
        valori["cancelled_reason"] = row["cancelled_reason"]
    return {"values": valori, "start_reconstructed": ricostruito}


#: Le righe LMC-15 ancora da portare nell'Agenda: con una stima (quindi con
#: un'agenzia) e non ancora rappresentate - ne' dal backfill (chiave) ne'
#: da una proiezione gia' scritta dall'Agenda (collegamento).
_CANDIDATE = """
SELECT i.id, i.stima_id, i.status, i.scheduled_for, i.completed_at,
       i.cancelled_at, i.cancelled_reason, s.agency_id
  FROM stima_inspections i
  JOIN stime s ON s.id = i.stima_id
 WHERE s.agency_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.stima_inspection_id = i.id)
   AND NOT EXISTS (SELECT 1 FROM appointments a
                    WHERE a.source = %s AND a.source_record_id = %s || i.id::text)
 ORDER BY i.id
"""


def census(cur) -> dict:
    """Il censimento, SOLO LETTURA: cosa esiste e cosa farebbe il backfill."""
    cur.execute("""
        SELECT count(*) AS totale,
               count(*) FILTER (WHERE i.status = 'scheduled') AS scheduled,
               count(*) FILTER (WHERE i.status = 'completed') AS completed,
               count(*) FILTER (WHERE i.status = 'cancelled') AS cancelled,
               count(*) FILTER (WHERE i.stima_id IS NOT NULL) AS con_stima,
               count(*) FILTER (WHERE i.stima_id IS NULL) AS orfani_senza_stima,
               count(*) FILTER (WHERE i.stima_id IS NOT NULL AND s.agency_id IS NULL)
                   AS stima_senza_agenzia,
               count(*) FILTER (WHERE i.scheduled_for IS NULL) AS senza_scheduled_for,
               count(*) FILTER (WHERE i.status = 'cancelled'
                                  AND i.cancelled_reason = 'no_show') AS cancelled_no_show,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM appointments a
                                               WHERE a.stima_inspection_id = i.id))
                   AS gia_collegati,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM appointments a
                                               WHERE a.source = %s
                                                 AND a.source_record_id = %s || i.id::text))
                   AS gia_backfill
          FROM stima_inspections i
          LEFT JOIN stime s ON s.id = i.stima_id
    """, (SOURCE, KEY_PREFIX))
    esito = dict(cur.fetchone())
    cur.execute(_CANDIDATE, (SOURCE, KEY_PREFIX))
    candidati = [dict(r) for r in cur.fetchall()]
    esito["da_importare"] = len(candidati)
    esito["da_importare_per_stato"] = {
        stato: sum(1 for c in candidati if c["status"] == stato) for stato in STATUS_MAP}
    esito["inizio_ricostruito"] = sum(1 for c in candidati if c["scheduled_for"] is None)
    cur.execute("""
        SELECT count(*) AS n FROM appointments
         WHERE appointment_type = 'inspection' AND stima_id IS NOT NULL
           AND stima_inspection_id IS NULL
           AND status IN ('requested', 'scheduled', 'confirmed')
    """)
    esito["agenda_aperti_non_proiettati"] = cur.fetchone()["n"]
    # DERIVA: finche' la facade non e' attiva LMC-15 resta lo scrittore reale.
    # Una richiesta importata e ancora intatta (version 1) la cui riga LMC-15
    # e' stata chiusa o spostata dopo il backfill non e' piu' allineata.
    cur.execute("""
        SELECT count(*) FILTER (WHERE i.status <> 'scheduled') AS chiuse_dopo,
               count(*) FILTER (WHERE i.status = 'scheduled'
                                  AND i.scheduled_for IS DISTINCT FROM a.start_at)
                   AS spostate_dopo
          FROM appointments a
          JOIN stima_inspections i ON i.id = a.stima_inspection_id
         WHERE a.source = %s AND a.status = 'requested' AND a.version = 1
    """, (SOURCE,))
    deriva = cur.fetchone()
    esito["deriva_chiuse_in_lmc15"] = deriva["chiuse_dopo"]
    esito["deriva_spostate_in_lmc15"] = deriva["spostate_dopo"]
    return esito


def run_backfill(cur, *, apply: bool) -> dict:
    """Porta nell'Agenda le righe LMC-15 mancanti.

    `apply=False`: nessuna scrittura, solo il piano. `apply=True`: INSERT
    idempotenti nella transazione del chiamante. Il lock consultivo mette in
    fila due corse concorrenti; l'indice unico decide comunque.
    """
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (LOCK_KEY,))
    cur.execute(_CANDIDATE, (SOURCE, KEY_PREFIX))
    candidati = [dict(r) for r in cur.fetchall()]
    esito = {"apply": apply, "candidates": len(candidati), "inserted": 0,
             "already_present": 0, "start_reconstructed": 0,
             "by_status": {}, "inserted_ids": []}
    for riga in candidati:
        piano = map_inspection(riga)
        stato = piano["values"]["status"]
        esito["by_status"][stato] = esito["by_status"].get(stato, 0) + 1
        if piano["start_reconstructed"]:
            esito["start_reconstructed"] += 1
        if not apply:
            continue
        nuova = repository.insert_appointment(cur, piano["values"], actor_user_id=None)
        if nuova is None:
            esito["already_present"] += 1
            continue
        esito["inserted"] += 1
        esito["inserted_ids"].append(nuova["id"])
    return esito
