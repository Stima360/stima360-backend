"""SITE-IMPORT-1 - un giro di importazione dal sito al CRM.

UN GIRO

    1. guardie: la sorgente non e' un CRM, il CRM non e' la sorgente;
    2. lock: un solo giro alla volta (advisory lock nel database del CRM);
    3. `stime`: gli id "fermi" del sito MENO quelli gia' nel registro, piu'
       quelli del registro da ritentare -> uno per uno;
    4. `stime_dettagliate`: lo stesso, dopo le stime (servono i genitori).

NESSUN CURSORE "ultimo id": si confronta l'INSIEME degli id del sito con il
registro. Una riga committata fuori ordine, un giro interrotto, un cron fermo
per giorni: la riga mancante e' comunque nella differenza al giro dopo.

UNA RIGA, PIU' PASSI, OGNUNO IDEMPOTENTE

La riga del CRM (`stime` o `stime_dettagliate`, con lo STESSO id del sito) e il
registro nascono nella stessa transazione. Poi i passi: bridge, immobile,
evento, task, PDF (per la stima); immobile, Agenda (per la dettagliata). Ogni
passo e' una funzione gia' esistente e idempotente del CRM; il suo esito va in
`steps`. Un passo fallito lascia la riga `partial` e si ritenta al giro dopo,
fino a `max_attempts` (PDF indisponibile: retry senza limite). Nessun passo viene ripetuto se e' gia' riuscito.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg2.extras import Json, RealDictCursor

from database import get_connection

from .config import Config
from .pdf_archive import Esito

log = logging.getLogger(__name__)

#: Il lock del giro, nel database del CRM. Una costante: nessun altro lo usa.
LOCK_KEY = 0x53495445  # "SITE"

RETRY_STATUSES = ("pending", "partial", "orphan", "failed")
#: Colonne del sito che NON si copiano: l'agenzia la decide il CRM; il token e'
#: la capability pubblica del SITO e non deve aprire una seconda porta nel CRM.
SKIP_COLUMNS = {
    "stime": {"id", "agency_id", "token", "token_expires"},
    "stime_dettagliate": {"id", "agency_id"},
}
#: Dati personali che non servono alla scheda immobile.
PII = {"nome", "cognome", "email", "telefono"}

STIMA_STEPS = ("bridge", "property", "event", "followup", "pdf")
DETAIL_STEPS = ("property", "agenda")
#: Esiti di passo che non si ritentano.
FINAL = {"done", "blocked", "skipped", "missing", "unverified", "invalid", "not_applicable"}


class ImportRefused(RuntimeError):
    pass


def _utc(valore):
    if valore is None:
        return None
    if isinstance(valore, datetime) and valore.tzinfo is None:
        return valore.replace(tzinfo=timezone.utc)
    return valore


def _impronta(riga: dict) -> str:
    return hashlib.sha256(json.dumps(riga, sort_keys=True, default=str).encode()).hexdigest()


def _colonne(cur, table: str) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s", (table,))
    return {r["column_name"] for r in cur.fetchall()}


def _numero_legacy(valore):
    """Converte i numeri che il sito PROD salva come testo.

    Valori non numerici (es. "Non specificato") diventano NULL nel CRM.
    Il dato originale del sito rimane invariato.
    """
    if valore is None:
        return None
    if isinstance(valore, int) and not isinstance(valore, bool):
        return valore
    if isinstance(valore, str):
        valore = valore.strip()
        if valore.isascii() and valore.isdecimal():
            return int(valore)
    return None


def _valori_per_crm(tabella: str, colonne: list[str], riga: dict) -> list:
    interi = {"locali", "anno"} if tabella == "stime" else (
        {"anno"} if tabella == "stime_dettagliate" else set())
    return [_numero_legacy(riga[c]) if c in interi else riga[c] for c in colonne]


def _raw_stima(riga: dict) -> dict:
    """Il payload che il catalogo immobile sa leggere, dalle colonne del sito."""
    from property.site_catalog import PREFILL_KEYS
    raw = {k: riga[c] for k, c in PREFILL_KEYS if c in riga and riga[c] is not None}
    if riga.get("fascia_mare") is not None:
        raw["fascia_mare"] = riga["fascia_mare"]
    return raw


def _raw_dettaglio(riga: dict) -> dict:
    from property.site_catalog import PREFILL_KEYS
    raw = {k: v for k, v in riga.items()
           if v is not None and k not in PII and k not in ("id", "agency_id", "stima_id", "data")}
    for chiave, colonna in PREFILL_KEYS:
        if riga.get(colonna) is not None:
            raw[chiave] = riga[colonna]
    return raw


# ---------------------------------------------------------------------------
# registro
# ---------------------------------------------------------------------------

class Ledger:
    def __init__(self, conn, source: str):
        self.conn, self.source = conn, source

    def cur(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)

    def all(self, table: str) -> dict[int, dict]:
        with self.cur() as cur:
            cur.execute("SELECT source_id, status, attempts, steps FROM site_import_records "
                        "WHERE source = %s AND source_table = %s", (self.source, table))
            return {r["source_id"]: dict(r) for r in cur.fetchall()}

    def claim(self, cur, table: str, source_id: int) -> dict:
        cur.execute("""INSERT INTO site_import_records (source, source_table, source_id)
                       VALUES (%s, %s, %s) ON CONFLICT (source, source_table, source_id) DO NOTHING""",
                    (self.source, table, source_id))
        cur.execute("""SELECT * FROM site_import_records
                        WHERE source = %s AND source_table = %s AND source_id = %s FOR UPDATE""",
                    (self.source, table, source_id))
        return dict(cur.fetchone())

    def save(self, cur, row_id: int, **campi) -> None:
        if "steps" in campi:
            campi["steps"] = Json(campi["steps"])
        assegna = ", ".join(f"{k} = %s" for k in campi)
        cur.execute(f"UPDATE site_import_records SET {assegna}, updated_at = NOW() WHERE id = %s",
                    (*campi.values(), row_id))


# ---------------------------------------------------------------------------
# il giro
# ---------------------------------------------------------------------------

class Importer:
    def __init__(self, config: Config, *, source, archive, connect=None, now=None):
        self.config, self.source, self.archive = config, source, archive
        self.connect = connect or get_connection
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.counts = {"stime_imported": 0, "dettagliate_imported": 0, "partial": 0, "orphan": 0,
                       "conflict": 0, "failed": 0, "skipped_already": 0, "pdf_ready": 0,
                       "pdf_missing": 0, "agenda_requests": 0, "pending_after_batch": 0}

    # -- guardie ------------------------------------------------------------

    def guard(self, conn) -> None:
        sito = self.source.identity()
        if sito.get("has_import_ledger"):
            raise ImportRefused("the configured site database contains a CRM import ledger: "
                                "SITE_DB_URL points to a CRM, refusing")
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db, inet_server_addr()::text AS addr, "
                        "inet_server_port() AS port, "
                        "to_regclass('public.site_import_records') IS NOT NULL AS ledger, "
                        "to_regclass('public.site_import_baselines') IS NOT NULL AS baseline")
            crm = dict(cur.fetchone())
        conn.rollback()
        if not crm["ledger"]:
            raise ImportRefused("site_import_records is missing: apply migration 095 first")
        if not crm["baseline"]:
            raise ImportRefused("site_import_baselines is missing: apply migration 096 first")
        if (crm["db"], crm["addr"], crm["port"]) == (sito["db"], sito["addr"], sito["port"]):
            raise ImportRefused("SITE_DB_URL and the CRM database are the same database: refusing")
        for table in ("stime", "stime_dettagliate"):
            if not self.source.table_present(table):
                raise ImportRefused(f"the site database has no {table} table")

    # -- entrata -------------------------------------------------------------

    def run(self, *, limit: int | None = None, dry_run: bool = False,
            initialize_baseline: bool = False) -> dict[str, Any]:
        limite = limit or self.config.batch
        lock_conn = self.connect()
        try:
            self.guard(lock_conn)
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
                if not cur.fetchone()[0]:
                    return {"status": "skipped_overlap", **self.counts}
            try:
                conn = self.connect()
                try:
                    ledger = Ledger(conn, self.config.source)
                    with ledger.cur() as cur:
                        cur.execute("SELECT ids FROM site_import_baselines WHERE source = %s",
                                    (self.config.source,))
                        baseline = cur.fetchone()
                        if initialize_baseline:
                            if dry_run:
                                raise ImportRefused("baseline initialization cannot be a dry run")
                            if baseline is not None:
                                return {"status": "baseline_already_initialized"}
                            ids = self.source.baseline_ids()
                            cur.execute("INSERT INTO site_import_baselines (source, ids) VALUES (%s, %s)",
                                        (self.config.source, Json(ids)))
                            conn.commit()
                            return {"status": "baseline_initialized",
                                    **{t: len(v) for t, v in ids.items()}}
                        if baseline is None:
                            raise ImportRefused("baseline missing: run --initialize-baseline first")
                        self.baseline = baseline["ids"]
                    self._baseline_dependencies(conn, ledger)
                    piano = {t: self._da_fare(ledger, t) for t in ("stime", "stime_dettagliate")}
                    conn.rollback()
                    if dry_run:
                        return {"status": "dry_run",
                                "stime_to_process": len(piano["stime"]),
                                "dettagliate_to_process": len(piano["stime_dettagliate"])}
                    restanti = limite
                    for table, metodo in (("stime", self._stima), ("stime_dettagliate", self._dettaglio)):
                        ids = piano[table][:restanti]
                        self.counts["pending_after_batch"] += len(piano[table]) - len(ids)
                        righe = self.source.fetch(table, ids)
                        for source_id in ids:
                            try:
                                metodo(conn, ledger, source_id, righe.get(source_id))
                            except Exception as exc:  # noqa: BLE001 - un record non ferma il giro
                                conn.rollback()
                                self.counts["failed"] += 1
                                self._fallito(conn, ledger, table, source_id, exc)
                        restanti -= len(ids)
                    return {"status": "completed", **self.counts}
                finally:
                    conn.close()
            finally:
                with lock_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                lock_conn.commit()
        finally:
            lock_conn.close()

    def _baseline_dependencies(self, conn, ledger):
        """Authorize only missing historical parents of post-baseline details."""
        historical = set(self.baseline["stime"])
        detail_history = set(self.baseline["stime_dettagliate"])
        ids = [i for i in self.source.settled_ids("stime_dettagliate", self.config.settle_minutes)
               if i not in detail_history]
        parents = {r.get("stima_id") for r in self.source.fetch("stime_dettagliate", ids).values()}
        parents &= historical
        records = ledger.all("stime")
        retained = {i for i, r in records.items()
                    if r["steps"].get("baseline_dependency")}
        with ledger.cur() as cur:
            cur.execute("SELECT id FROM stime WHERE id = ANY(%s)", (list(parents),))
            existing = {r["id"] for r in cur.fetchall()}
        self.baseline_parents = (parents - existing) | retained

    def _da_fare(self, ledger: Ledger, table: str) -> list[int]:
        presenti = ledger.all(table)
        sito = self.source.settled_ids(table, self.config.settle_minutes)
        excluded = set(self.baseline[table])
        if table == "stime":
            excluded -= self.baseline_parents
        nuovi = [i for i in sito if i not in presenti and i not in excluded]
        ritenta = [i for i, r in presenti.items()
                   if i not in excluded and r["status"] in RETRY_STATUSES
                   and (r["attempts"] < self.config.max_attempts
                        or (r["steps"].get("pdf") or {}).get("state") == "error")]
        return sorted(set(nuovi) | set(ritenta))

    def _fallito(self, conn, ledger, table, source_id, exc) -> None:
        log.error("site_import_failed table=%s source_id=%s error_type=%s", table, source_id,
                  type(exc).__name__)
        try:
            with ledger.cur() as cur:
                riga = ledger.claim(cur, table, source_id)
                if riga["status"] != "imported":
                    ledger.save(cur, riga["id"], status="failed", attempts=riga["attempts"] + 1,
                                last_error=type(exc).__name__[:200])
            conn.commit()
        except Exception:  # noqa: BLE001 - resta il log
            conn.rollback()

    # -- passi comuni ------------------------------------------------------

    def _passo(self, conn, ledger, riga_id, steps, nome, azione) -> Any:
        """Esegue `azione` se il passo non e' gia' concluso; salva l'esito."""
        precedente = steps.get(nome) or {}
        if precedente.get("state") in FINAL:
            return precedente
        try:
            esito = azione()
        except Exception as exc:  # noqa: BLE001 - il passo resta da ritentare
            conn.rollback()
            esito = {"state": "error", "error": type(exc).__name__[:120]}
            log.warning("site_import_step_failed step=%s ledger_id=%s error_type=%s",
                        nome, riga_id, type(exc).__name__)
        steps[nome] = esito
        with ledger.cur() as cur:
            ledger.save(cur, riga_id, steps=steps)
        conn.commit()
        return esito

    def _chiudi(self, conn, ledger, riga, steps, *, passi, attempts) -> str:
        errori = [p for p in passi if (steps.get(p) or {}).get("state") not in FINAL]
        if not errori:
            stato, fatto = "imported", self.now()
        elif attempts >= self.config.max_attempts:
            stato, fatto = "partial", None
        else:
            stato, fatto = "partial", None
        with ledger.cur() as cur:
            campi = {"status": stato, "steps": steps,
                     "last_error": (",".join(errori)[:200] or None)}
            if fatto is not None:
                campi["imported_at"] = fatto
            ledger.save(cur, riga["id"], **campi)
        conn.commit()
        return stato

    # -- la stima ----------------------------------------------------------

    def _stima(self, conn, ledger: Ledger, source_id: int, sito: dict | None) -> None:
        from network_routing.service import (system_context_for_persisted_public_stima,
                                             system_context_for_routed_public_stima)

        with ledger.cur() as cur:
            riga = ledger.claim(cur, "stime", source_id)
            if riga["status"] in ("imported", "conflict"):
                conn.rollback()
                self.counts["skipped_already"] += 1
                return
            attempts = riga["attempts"] + 1
            steps = dict(riga["steps"] or {})
            if source_id in self.baseline_parents:
                steps["baseline_dependency"] = {"state": "done"}
            if sito is None:
                ledger.save(cur, riga["id"], status="failed", attempts=attempts,
                            last_error="source_row_missing")
                conn.commit()
                self.counts["failed"] += 1
                return
            if (steps.get("stima") or {}).get("state") != "done":
                cur.execute("SELECT 1 FROM stime WHERE id = %s", (source_id,))
                if cur.fetchone() is not None:
                    # Una stima con questo id esiste gia' nel CRM e NON viene da qui:
                    # non si sovrascrive, si segnala.
                    ledger.save(cur, riga["id"], status="conflict", attempts=attempts,
                                last_error="crm_id_already_used")
                    conn.commit()
                    self.counts["conflict"] += 1
                    return
                ctx, decisione = system_context_for_routed_public_stima(cur, comune=sito.get("comune"))
                colonne = sorted((_colonne(cur, "stime") & set(sito)) - SKIP_COLUMNS["stime"])
                valori = _valori_per_crm("stime", colonne, sito)
                cur.execute(
                    f"INSERT INTO stime (id, agency_id, {', '.join(colonne)}) "
                    f"VALUES (%s, %s{', %s' * len(colonne)})",
                    (source_id, ctx.require_agency(), *valori))
                steps["stima"] = {"state": "done", "routing": decisione.source}
                ledger.save(cur, riga["id"], crm_id=source_id, agency_id=ctx.require_agency(),
                            steps=steps, attempts=attempts, source_sha256=_impronta(sito),
                            source_created_at=sito.get("data"))
            else:
                ledger.save(cur, riga["id"], attempts=attempts)
            cur.execute("SELECT agency_id FROM stime WHERE id = %s", (source_id,))
            if cur.fetchone() is None:
                # Cancellata dal CRM dopo l'importazione: il registro la ricorda.
                ledger.save(cur, riga["id"], status="imported", imported_at=self.now(),
                            last_error="deleted_in_crm")
                conn.commit()
                return
            ctx = system_context_for_persisted_public_stima(cur, stima_id=source_id)
        conn.commit()
        self._passi_stima(conn, ledger, riga, steps, sito, ctx, source_id)
        stato = self._chiudi(conn, ledger, riga, steps, passi=STIMA_STEPS, attempts=attempts)
        self.counts["stime_imported" if stato == "imported" else "partial"] += 1

    def _passi_stima(self, conn, ledger, riga, steps, sito, ctx, stima_id) -> None:
        from core import service as core_service
        from property import site_sync
        from seller_intelligence import service as seller_intelligence_service

        def bridge():
            esito = core_service.bridge_public_stima(
                stima_id, first_name=sito.get("nome"), last_name=sito.get("cognome"),
                email=sito.get("email"), phone=sito.get("telefono"),
                marketing_consent=bool(sito.get("consenso_marketing")),
                marketing_consent_at=_utc(sito.get("consenso_marketing_at")) if sito.get("consenso_marketing") else None,
                system_ctx=ctx)
            ok = esito.get("status") in ("linked", "already_linked") and esito.get("contact_id") and esito.get("lead_id")
            return {"state": "done" if ok else "blocked", "status": esito.get("status"),
                    "contact_id": esito.get("contact_id"), "lead_id": esito.get("lead_id"),
                    "reason": esito.get("reason")}

        ponte = self._passo(conn, ledger, riga["id"], steps, "bridge", bridge)
        collegato = ponte.get("state") == "done"

        def immobile():
            if not collegato:
                return {"state": "not_applicable", "reason": "no_contact_lead"}
            esito = site_sync.sync_public_stima(
                ctx, stima_id=stima_id, raw=_raw_stima(sito),
                bridge_result={"contact_id": ponte["contact_id"], "lead_id": ponte["lead_id"]})
            if esito.get("property_id"):
                return {"state": "done", "status": esito.get("status"), "property_id": esito["property_id"]}
            return {"state": "blocked", "status": esito.get("status"), "reason": esito.get("reason")}

        self._passo(conn, ledger, riga["id"], steps, "property", immobile)

        def evento():
            seller_intelligence_service.record_event(
                event_type="stima_richiesta", event_source="stima360_it", stima_id=stima_id,
                contact_id=ponte.get("contact_id"), lead_id=ponte.get("lead_id"),
                payload={"comune": sito.get("comune"), "tipologia": sito.get("tipologia"),
                         "mq": sito.get("mq"), "origin": "site_import"},
                idempotency_key=f"stima_richiesta:{stima_id}")
            return {"state": "done"}

        self._passo(conn, ledger, riga["id"], steps, "event", evento)

        self._passo(conn, ledger, riga["id"], steps, "followup",
                    lambda: {"state": "skipped", "reason": "site_import_no_commercial_followup"})
        self._passo(conn, ledger, riga["id"], steps, "pdf",
                    lambda: self._pdf(conn, stima_id, _utc(sito.get("data"))))

    def _pdf(self, conn, stima_id: int, creata) -> dict:
        esito: Esito = self.archive.fetch(stima_id, creata)
        if esito.status == "unavailable":
            # Archivio non raggiungibile: si ritenta, senza scrivere niente.
            return {"state": "error", "reason": esito.reason}
        provenienza = esito.provenance or {"origin": "site_archive", "reason": esito.reason}
        provenienza["origin"] = "site_archive"
        with conn.cursor() as cur:
            cur.execute("SELECT agency_id FROM stime WHERE id = %s", (stima_id,))
            agenzia = cur.fetchone()[0]
            if esito.status == "ready":
                cur.execute("""
                    INSERT INTO stima_pdf_artifacts (stima_id, agency_id, render_payload, status, pdf_bytes, sha256)
                    VALUES (%s, %s, %s, 'ready', %s, %s)
                    ON CONFLICT (stima_id) DO NOTHING""",
                            (stima_id, agenzia, Json(provenienza), esito.pdf, provenienza["sha256"]))
                self.counts["pdf_ready"] += 1
                stato = "done"
            else:
                cur.execute("""
                    INSERT INTO stima_pdf_artifacts (stima_id, agency_id, render_payload, status, last_error)
                    VALUES (%s, %s, %s, 'failed', %s)
                    ON CONFLICT (stima_id) DO NOTHING""",
                            (stima_id, agenzia, Json(provenienza), (esito.reason or esito.status)[:120]))
                self.counts["pdf_missing"] += 1
                stato = esito.status
                log.warning("site_import_pdf_not_attached stima_id=%s status=%s reason=%s",
                            stima_id, esito.status, esito.reason)
        conn.commit()
        return {"state": stato, "reason": esito.reason,
                "sha256": (esito.provenance or {}).get("sha256")}

    # -- la dettagliata ----------------------------------------------------

    def _dettaglio(self, conn, ledger: Ledger, source_id: int, sito: dict | None) -> None:
        from appointments_legacy.site_hook import safe_import_for_detail
        from property import site_sync

        with ledger.cur() as cur:
            riga = ledger.claim(cur, "stime_dettagliate", source_id)
            if riga["status"] in ("imported", "conflict"):
                conn.rollback()
                self.counts["skipped_already"] += 1
                return
            attempts = riga["attempts"] + 1
            steps = dict(riga["steps"] or {})
            if sito is None:
                ledger.save(cur, riga["id"], status="failed", attempts=attempts,
                            last_error="source_row_missing")
                conn.commit()
                self.counts["failed"] += 1
                return
            stima_id = sito.get("stima_id")
            # Il genitore deve essere la stima importata DA QUI (crm_id valorizzato
            # solo da un nostro INSERT): mai una stima del CRM con lo stesso id.
            cur.execute("""SELECT s.agency_id FROM stime s JOIN site_import_records r
                             ON r.source = %s AND r.source_table = 'stime' AND r.crm_id = s.id
                            WHERE s.id = %s""", (self.config.source, stima_id))
            genitore = cur.fetchone()
            if (steps.get("detail") or {}).get("state") != "done":
                if genitore is None:
                    # Il genitore non e' (ancora) nel CRM: si riprova ai giri dopo.
                    ledger.save(cur, riga["id"], status="orphan", attempts=attempts,
                                last_error="parent_stima_not_imported")
                    conn.commit()
                    self.counts["orphan"] += 1
                    return
                cur.execute("SELECT 1 FROM stime_dettagliate WHERE id = %s", (source_id,))
                if cur.fetchone() is not None:
                    ledger.save(cur, riga["id"], status="conflict", attempts=attempts,
                                last_error="crm_id_already_used")
                    conn.commit()
                    self.counts["conflict"] += 1
                    return
                colonne = sorted((_colonne(cur, "stime_dettagliate") & set(sito))
                                 - SKIP_COLUMNS["stime_dettagliate"])
                cur.execute(
                    f"INSERT INTO stime_dettagliate (id, agency_id, {', '.join(colonne)}) "
                    f"VALUES (%s, %s{', %s' * len(colonne)})",
                    (source_id, genitore["agency_id"], *_valori_per_crm("stime_dettagliate", colonne, sito)))
                steps["detail"] = {"state": "done"}
                ledger.save(cur, riga["id"], crm_id=source_id, agency_id=genitore["agency_id"],
                            steps=steps, attempts=attempts, source_sha256=_impronta(sito),
                            source_created_at=sito.get("data"))
            else:
                ledger.save(cur, riga["id"], attempts=attempts)
        conn.commit()

        def immobile():
            esito = site_sync.sync_detail(stima_id=stima_id, detail_id=source_id, raw=_raw_dettaglio(sito))
            if esito.get("status") in ("updated", "replica"):
                return {"state": "done", "status": esito["status"], "property_id": esito.get("property_id")}
            if esito.get("status") == "pending":
                return {"state": "error", "reason": esito.get("reason")}   # si ritenta
            return {"state": "blocked", "status": esito.get("status"), "reason": esito.get("reason")}

        self._passo(conn, ledger, riga["id"], steps, "property", immobile)

        def agenda():
            rapporto = safe_import_for_detail(source_id)
            if rapporto is None:
                return {"state": "error", "reason": "agenda_import_failed"}
            inseriti = int(rapporto.get("inserted") or 0)
            self.counts["agenda_requests"] += inseriti
            return {"state": "done", "inserted": inseriti,
                    "already_present": int(rapporto.get("already_imported") or 0)
                    + int(rapporto.get("open_request_exists") or 0)}

        self._passo(conn, ledger, riga["id"], steps, "agenda", agenda)
        stato = self._chiudi(conn, ledger, riga, steps, passi=DETAIL_STEPS, attempts=attempts)
        self.counts["dettagliate_imported" if stato == "imported" else "partial"] += 1


def run_once(config: Config | None = None, *, source=None, archive=None, connect=None,
             limit: int | None = None, dry_run: bool = False, now=None,
             initialize_baseline: bool = False) -> dict[str, Any]:
    """Un giro completo. `source`/`archive`/`connect` si iniettano nei test."""
    from .pdf_archive import GitHubArchive
    from .source import SiteSource

    config = config or Config.from_env()
    archive = archive or GitHubArchive(config.pdf_repo, config.pdf_branch, config.pdf_token,
                                       window_hours=config.pdf_match_window_hours)
    proprio = source is None
    source = source or SiteSource(config.site_db_url)
    try:
        return Importer(config, source=source, archive=archive, connect=connect, now=now).run(
            limit=limit, dry_run=dry_run, initialize_baseline=initialize_baseline)
    finally:
        if proprio:
            source.close()
