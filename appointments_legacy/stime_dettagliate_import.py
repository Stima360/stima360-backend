"""A30-6 - import dei sopralluoghi legacy `stime_dettagliate.sopralluogo` -> Agenda.

COSA E' IL DATO LEGACY (audit A30-6, dal codice reale)

`stime_dettagliate.sopralluogo` lo scrive SOLO il form pubblico
`stima_dettagliata.html` (`<input type="datetime-local">`, "Disponibilita' per
sopralluogo") attraverso `POST /api/salva_stima_dettagliata`, senza
validazione, senza slot, senza conferma. Nessun codice lo aggiorna. E' una
PREFERENZA DEL CLIENTE non confermata: nell'Agenda diventa una richiesta da
smistare (`requested`), mai un appuntamento fissato ne' un esito.

REGOLE (decisioni vincolanti del gate A30-6)

* SOLO LETTURA su `stime_dettagliate`, `stime`, `lead_stime`, `leads`,
  `contacts`. Scrive solo `appointments` e il suo evento `created`, attraverso
  `appointments.repository.insert_appointment` - lo stesso contratto del
  backfill A30-2P: INSERT ... ON CONFLICT (source, source_record_id) DO
  NOTHING RETURNING, e l'evento SOLO se la riga e' stata davvero inserita,
  nella stessa transazione. Nessun `stima_inspections`, nessuna timeline,
  nessuna notifica, nessun job di comunicazione, nessuna proiezione: il
  service di creazione NON e' usato.
* IDEMPOTENTE: `source = 'legacy_stime_dettagliate'`,
  `source_record_id = 'stime_dettagliate:<id>'`. L'indice unico
  `uq_appointments_source_record` della 072 e' la protezione autorevole anche
  fra due corse concorrenti (nessun lock consultivo: il conflitto lo decide
  il database). Una chiave annullata dal rollback resta OCCUPATA.
* INSERT-ONCE: un appuntamento gia' importato non viene mai modificato, anche
  se il legacy cambia o sparisce. Il census misura la deriva, l'import no.
* STATO: sempre `requested`, anche nel passato (D2). Mai completed,
  cancelled o no_show: una data trascorsa non dimostra l'esito.
* ORFANI (D3): senza una stima valida DELLA STESSA AGENZIA il record non si
  importa; si conta come `orphan`.
* D6: `location_text` NULL. `property_id`, `assigned_user_id`,
  `created_by_user_id` NULL: il legacy non li conserva e non si inferiscono.
* LEAD: collegato solo se esiste ESATTAMENTE UN lead della stessa agenzia
  legato alla stima; il contatto solo se e' quello di quel lead ed e' della
  stessa agenzia. 0 o piu' lead: NULL, nessuna scelta arbitraria.
* ORA (regola DST): il valore e' un TIMESTAMP naive letto come ora a parete
  Europe/Rome. La conversione la fa Python con `zoneinfo`, controllando
  ESPLICITAMENTE che l'ora esista e non sia ambigua; PostgreSQL non converte
  nulla (AT TIME ZONE normalizzerebbe in silenzio). Ora inesistente
  (`dst_nonexistent`) o ambigua (`dst_ambiguous`): il record NON si importa.
* DURATA: il legacy non la conserva. `end_at = start_at + 60 minuti` e' una
  POLICY DI IMPORT (`IMPORT_DURATION_MINUTES`), non un dato storico.

Il chiamante possiede la transazione (cursore `RealDictCursor`): nessuna
funzione qui fa commit o rollback della transazione.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from appointments import repository

SOURCE = "legacy_stime_dettagliate"
KEY_PREFIX = "stime_dettagliate:"
APPOINTMENT_TYPE = "inspection"
STATUS = "requested"
TIMEZONE = "Europe/Rome"
ROMA = ZoneInfo(TIMEZONE)

#: POLICY DI IMPORT A30-6, non un dato del legacy: il form del sito non ha
#: durata, slot ne' ora di fine.
IMPORT_DURATION_MINUTES = 60

#: Nessun dato personale: solo la provenienza, ricostruibile dalla chiave.
NOTES_TEMPLATE = ("Disponibilita' indicata dal cliente nel modulo stima dettagliata "
                  "(legacy stime_dettagliate #{id}). Orario non confermato: da fissare.")

ROLLBACK_REASON = "A30-6 rollback import legacy"
ROLLBACK_ACTION = "a30_6_rollback_import"

#: Le categorie del report, in ordine. Solo conteggi: nessun dato personale.
REPORT_KEYS = (
    "with_sopralluogo", "without_sopralluogo",
    "eligible", "inserted", "already_imported",
    "past", "today", "future",
    "orphan", "dst_nonexistent", "dst_ambiguous", "missing_agency",
    "zero_lead", "one_lead", "multiple_leads", "contact_not_linked",
    "errors",
)


class LegacySchemaError(RuntimeError):
    """Lo schema di `stime_dettagliate` non e' quello che l'import presuppone."""


def source_key(record_id: int) -> str:
    """La chiave d'idempotenza: stabile, deterministica, unica per record."""
    return f"{KEY_PREFIX}{int(record_id)}"


# ---------------------------------------------------------------------------
# TEMPO: ora a parete Europe/Rome, senza normalizzazioni silenziose
# ---------------------------------------------------------------------------

def to_rome_instant(wall):
    """`(esito, istante)` per un valore legacy.

    esito: `ok` (istante offset-aware Europe/Rome), `dst_nonexistent`,
    `dst_ambiguous`, `unexpected_timezone`, `unparseable`. Solo `ok` porta un
    istante. Nessuna correzione automatica dell'orario.
    """
    if not isinstance(wall, datetime):
        return "unparseable", None
    if wall.tzinfo is not None:
        # la colonna e' TIMESTAMP senza fuso: un valore con offset vuol dire
        # uno schema diverso da quello certificato, e non si indovina.
        return "unexpected_timezone", None
    prima = wall.replace(tzinfo=ROMA, fold=0)
    seconda = wall.replace(tzinfo=ROMA, fold=1)
    if prima.utcoffset() != seconda.utcoffset():
        # Il fuso ha due letture per questa ora a parete: o non esiste (salto
        # di primavera) o esiste due volte (ritorno d'autunno).
        andata_ritorno = prima.astimezone(timezone.utc).astimezone(ROMA)
        if andata_ritorno.replace(tzinfo=None) != wall:
            return "dst_nonexistent", None
        return "dst_ambiguous", None
    return "ok", prima


def classify_day(wall: datetime, today: date) -> str:
    giorno = wall.date()
    if giorno < today:
        return "past"
    if giorno > today:
        return "future"
    return "today"


# ---------------------------------------------------------------------------
# LETTURE (solo SELECT)
# ---------------------------------------------------------------------------

def assert_legacy_schema(cur) -> None:
    """Fallisce chiuso se `stime_dettagliate` non porta le colonne attese.

    `agency_id` esiste solo dove sono applicate le 049-051 (TEST): senza, il
    tenant non e' certo e l'import non si fa (su PROD serve una review
    separata, D1).
    """
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'stime_dettagliate'
           AND column_name IN ('id', 'stima_id', 'agency_id', 'sopralluogo')
    """)
    tipi = {r["column_name"]: r["data_type"] for r in cur.fetchall()}
    attese = {"id", "stima_id", "agency_id", "sopralluogo"}
    if set(tipi) != attese:
        raise LegacySchemaError(
            f"A30-6: stime_dettagliate senza le colonne attese: "
            f"mancano {sorted(attese - set(tipi))}")
    if tipi["sopralluogo"] != "timestamp without time zone":
        raise LegacySchemaError(
            f"A30-6: stime_dettagliate.sopralluogo e' {tipi['sopralluogo']!r}, "
            "atteso 'timestamp without time zone'")


def rome_today(cur) -> date:
    """La data di oggi a Roma secondo il database (NOW() e' un istante)."""
    cur.execute("SELECT (NOW() AT TIME ZONE %s)::date AS oggi", (TIMEZONE,))
    return cur.fetchone()["oggi"]


#: Ogni record con un sopralluogo, con quanto serve a classificarlo. Il lead
#: si conta SOLO nella stessa agenzia del record.
_CANDIDATI = """
SELECT d.id, d.agency_id, d.stima_id, d.sopralluogo,
       s.id AS stima_trovata, s.agency_id AS stima_agency_id,
       (SELECT count(DISTINCT l.id)
          FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
         WHERE ls.stima_id = d.stima_id AND l.agency_id = d.agency_id) AS n_lead,
       (SELECT min(l.id)
          FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
         WHERE ls.stima_id = d.stima_id AND l.agency_id = d.agency_id) AS primo_lead,
       EXISTS (SELECT 1 FROM appointments a
                WHERE a.source = %s AND a.source_record_id = %s || d.id::text)
           AS gia_importato
  FROM stime_dettagliate d
  LEFT JOIN stime s ON s.id = d.stima_id
 WHERE d.sopralluogo IS NOT NULL
 ORDER BY d.id
"""


def _contatto_del_lead(cur, lead_id: int, agency_id: int):
    """Il contatto del lead, solo se e' della stessa agenzia; altrimenti None."""
    cur.execute(
        "SELECT c.id FROM leads l JOIN contacts c ON c.id = l.contact_id "
        "WHERE l.id = %s AND l.agency_id = %s AND c.agency_id = %s",
        (lead_id, agency_id, agency_id),
    )
    riga = cur.fetchone()
    return None if riga is None else riga["id"]


# ---------------------------------------------------------------------------
# MAPPING (puro)
# ---------------------------------------------------------------------------

def classify(row: dict) -> tuple[str, datetime | None]:
    """`(categoria, istante)`: `already_imported`, `missing_agency`, `orphan`,
    `dst_nonexistent`, `dst_ambiguous`, `error` oppure `eligible`."""
    if row["gia_importato"]:
        return "already_imported", None
    if row["agency_id"] is None:
        return "missing_agency", None
    if (row["stima_id"] is None or row["stima_trovata"] is None
            or row["stima_agency_id"] != row["agency_id"]):
        return "orphan", None
    esito, istante = to_rome_instant(row["sopralluogo"])
    if esito == "ok":
        return "eligible", istante
    if esito in ("dst_nonexistent", "dst_ambiguous"):
        return esito, None
    return "error", None


def map_values(row: dict, start_at: datetime, *, lead_id, contact_id) -> dict:
    """I valori di `appointments` per un record idoneo. Puro."""
    return {
        "agency_id": row["agency_id"],
        "assigned_user_id": None,
        "appointment_type": APPOINTMENT_TYPE,
        "status": STATUS,
        "start_at": start_at,
        "end_at": start_at + timedelta(minutes=IMPORT_DURATION_MINUTES),
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "stima_id": row["stima_id"],
        "contact_id": contact_id,
        "lead_id": lead_id,
        "property_id": None,
        "location_text": None,
        "notes": NOTES_TEMPLATE.format(id=int(row["id"])),
        "source": SOURCE,
        "source_record_id": source_key(row["id"]),
        "created_by_user_id": None,
    }


# ---------------------------------------------------------------------------
# IMPORT
# ---------------------------------------------------------------------------

def _report(apply: bool) -> dict:
    esito = {k: 0 for k in REPORT_KEYS}
    esito.update({"apply": apply, "duration_policy_minutes": IMPORT_DURATION_MINUTES,
                  "inserted_ids": [], "error_details": []})
    return esito


def run_import(cur, *, apply: bool, today: date | None = None) -> dict:
    """Classifica ogni record con un sopralluogo e, con `apply=True`, inserisce
    gli idonei nella transazione del chiamante (nessun commit qui).

    Con `apply=False` e' SOLA LETTURA: nessuna scrittura, solo il piano.
    Ogni INSERT sta in un SAVEPOINT: un errore inatteso su un record si conta
    in `errors` (id del record legacy e classe dell'errore, nessun dato
    personale) e non lascia nulla a meta'; gli altri record proseguono.
    """
    assert_legacy_schema(cur)
    oggi = today if today is not None else rome_today(cur)
    esito = _report(apply)

    cur.execute("SELECT count(*) AS n FROM stime_dettagliate WHERE sopralluogo IS NULL")
    esito["without_sopralluogo"] = cur.fetchone()["n"]
    cur.execute(_CANDIDATI, (SOURCE, KEY_PREFIX))
    righe = [dict(r) for r in cur.fetchall()]
    esito["with_sopralluogo"] = len(righe)

    for riga in righe:
        categoria, istante = classify(riga)
        if categoria == "error":
            esito["errors"] += 1
            esito["error_details"].append(
                {"record_id": riga["id"], "kind": to_rome_instant(riga["sopralluogo"])[0]})
            continue
        if categoria != "eligible":
            esito[categoria] += 1
            continue

        esito["eligible"] += 1
        esito[classify_day(riga["sopralluogo"], oggi)] += 1
        lead_id = contact_id = None
        if riga["n_lead"] == 0:
            esito["zero_lead"] += 1
        elif riga["n_lead"] == 1:
            esito["one_lead"] += 1
            lead_id = riga["primo_lead"]
            contact_id = _contatto_del_lead(cur, lead_id, riga["agency_id"])
            if contact_id is None:
                esito["contact_not_linked"] += 1
        else:
            esito["multiple_leads"] += 1

        if not apply:
            continue
        valori = map_values(riga, istante, lead_id=lead_id, contact_id=contact_id)
        cur.execute("SAVEPOINT a30_6_record")
        try:
            nuova = repository.insert_appointment(cur, valori, actor_user_id=None)
        except Exception as exc:  # noqa: BLE001 - contato e riportato, senza PII
            cur.execute("ROLLBACK TO SAVEPOINT a30_6_record")
            esito["errors"] += 1
            esito["error_details"].append(
                {"record_id": riga["id"], "kind": type(exc).__name__,
                 "sqlstate": getattr(exc, "pgcode", None)})
            continue
        cur.execute("RELEASE SAVEPOINT a30_6_record")
        if nuova is None:
            # un'altra corsa l'ha inserito nel frattempo: l'indice unico ha
            # deciso, nessuna riga e nessun evento nuovi.
            esito["already_imported"] += 1
            continue
        esito["inserted"] += 1
        esito["inserted_ids"].append(nuova["id"])
    return esito


# ---------------------------------------------------------------------------
# CENSUS (solo lettura): il piano piu' la deriva degli import gia' fatti
# ---------------------------------------------------------------------------

_IMPORTATI = """
SELECT a.id, a.status, a.version, a.start_at,
       d.id AS legacy_id, d.sopralluogo AS legacy_sopralluogo
  FROM appointments a
  LEFT JOIN stime_dettagliate d ON a.source_record_id = %s || d.id::text
 WHERE a.source = %s
 ORDER BY a.id
"""


def census(cur, *, today: date | None = None) -> dict:
    """SOLA LETTURA: cosa farebbe l'import, e la deriva di quanto gia' importato.

    La deriva si misura e basta: l'import non modifica mai un appuntamento
    gia' importato (INSERT-ONCE, nessuna sincronizzazione)."""
    piano = run_import(cur, apply=False, today=today)
    cur.execute(_IMPORTATI, (KEY_PREFIX, SOURCE))
    importati = [dict(r) for r in cur.fetchall()]
    deriva = {"imported_total": len(importati),
              "imported_by_status": {},
              "drift_source_deleted": 0, "drift_source_cleared": 0,
              "drift_time_changed": 0}
    for a in importati:
        deriva["imported_by_status"][a["status"]] = \
            deriva["imported_by_status"].get(a["status"], 0) + 1
        if a["legacy_id"] is None:
            deriva["drift_source_deleted"] += 1
        elif a["legacy_sopralluogo"] is None:
            deriva["drift_source_cleared"] += 1
        else:
            esito, istante = to_rome_instant(a["legacy_sopralluogo"])
            if esito != "ok" or istante != a["start_at"]:
                deriva["drift_time_changed"] += 1
    return {"plan": piano, "drift": deriva}


# ---------------------------------------------------------------------------
# ROLLBACK SELETTIVO (D4): annullamento logico, mai DELETE
# ---------------------------------------------------------------------------

#: "Intatto" = mai lavorato da nessuno: ancora la richiesta importata, mai
#: aggiornata (version 1: il trigger della 072 la incrementa a OGNI UPDATE),
#: senza agente, senza sopralluogo LMC-15 collegato, con il solo evento
#: `created` del sistema.
_INTATTI = """
SELECT a.id
  FROM appointments a
 WHERE a.source = %s
   AND a.status = 'requested'
   AND a.version = 1
   AND a.assigned_user_id IS NULL
   AND a.stima_inspection_id IS NULL
   AND a.created_by_user_id IS NULL
   AND (SELECT count(*) FROM appointment_events e
         WHERE e.appointment_id = a.id AND e.agency_id = a.agency_id) = 1
   AND EXISTS (SELECT 1 FROM appointment_events e
                WHERE e.appointment_id = a.id AND e.agency_id = a.agency_id
                  AND e.event_type = 'created' AND e.actor_user_id IS NULL)
 ORDER BY a.id
"""


def rollback_untouched(cur, *, apply: bool) -> dict:
    """Annulla (`cancelled`) SOLO gli appuntamenti importati e mai lavorati.

    Non tocca appuntamenti manuali, altre fonti, righe gia' lavorate da un
    operatore, `stime`, `stime_dettagliate`, `stima_inspections`. Nessuna
    DELETE (la 072 la rifiuta, e non si aggiunge una migration per farla).
    La chiave `source/source_record_id` di una riga annullata resta OCCUPATA:
    quel record legacy non si reimporta.
    """
    cur.execute("SELECT count(*) AS n FROM appointments WHERE source = %s", (SOURCE,))
    totale = cur.fetchone()["n"]
    cur.execute(_INTATTI, (SOURCE,))
    intatti = [r["id"] for r in cur.fetchall()]
    esito = {"apply": apply, "imported_total": totale, "untouched": len(intatti),
             "worked_skipped": totale - len(intatti), "cancelled": 0,
             "cancelled_ids": [], "keys_remain_occupied": True}
    if not apply:
        return esito
    for appuntamento in intatti:
        # ricontrollo sotto lock di riga: se un operatore l'ha toccata fra la
        # lettura e qui, non e' piu' intatta e resta com'e'.
        cur.execute(
            "SELECT id FROM appointments WHERE id = %s AND source = %s "
            "AND status = 'requested' AND version = 1 FOR UPDATE",
            (appuntamento, SOURCE),
        )
        if cur.fetchone() is None:
            esito["worked_skipped"] += 1
            continue
        adesso = repository.db_now(cur)
        repository.update_appointment(
            cur, appuntamento,
            {"status": "cancelled", "cancelled_at": adesso,
             "cancelled_reason": ROLLBACK_REASON},
            actor_user_id=None, event_type="status_changed", from_status="requested",
            azione=ROLLBACK_ACTION)
        esito["cancelled"] += 1
        esito["cancelled_ids"].append(appuntamento)
    return esito
