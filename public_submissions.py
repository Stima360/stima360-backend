"""Durable public-request receipts, independent of business/commercial state.

A session advisory mutex spans repositories' independent transactions. The
first business INSERT and receipt parent attachment must commit together.
Domain checkpoints may replay only operations already idempotent by parent ID.
An external send is claimed BEFORE dispatch: an uncertain attempt never replays.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import uuid
from contextlib import contextmanager

from fastapi import HTTPException
from psycopg2.extras import Json, RealDictCursor

from core.property_trash import live as _property_live  # STIMA-CRM-AGENDA-1: Cestino Immobili

log = logging.getLogger(__name__)
TRANSPORT_KEYS = {"request_id", "client_request_id", "receipt_key"}
# Accessory deliveries: their outcome never decides whether a stored result
# can be read. Every other checkpoint is part of the result itself.
NOTIFICATION_STEPS = frozenset({"admin_email", "whatsapp"})
# Quick-result checkpoints that must have concluded before the page may show
# the value, the private PDF and the detailed form.
QUICK_RESULT_STEPS = ("bridge", "owner", "property", "event_requested", "followup",
                      "fields_token", "valuation", "event_completed", "watch",
                      "pdf_snapshot", "pdf", "email_queue")
DONE_STATES = {"succeeded", "not_applicable"}


class PartialSubmission(Exception):
    pass


def _json(value):
    return json.loads(json.dumps(value, default=lambda v: v.isoformat()))


def identity(raw, headers):
    rid = headers.get("idempotency-key") or raw.get("request_id")
    proof = headers.get("x-receipt-key") or raw.get("receipt_key")
    try:
        rid = str(uuid.UUID(str(rid)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Identità invio mancante o non valida") from None
    for key in ("request_id", "client_request_id"):
        if raw.get(key) not in (None, "") and str(raw[key]) != rid:
            raise HTTPException(409, "Identità invio incoerente")
    if not isinstance(proof, str) or not re.fullmatch(r"[a-f0-9]{64}", proof):
        raise HTTPException(400, "Chiave ricevuta mancante o non valida")
    return rid, hashlib.sha256(proof.encode("ascii")).hexdigest()


def payload(raw):
    return {k: v for k, v in raw.items() if k not in TRANSPORT_KEYS}


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


class Receipt:
    def __init__(self, conn, request_id, agency_id=None):
        self.conn, self.request_id = conn, request_id
        self.operator_agency = agency_id
        self.reload()

    def reload(self):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT *, expires_at > clock_timestamp() AS live FROM public_submission_receipts WHERE request_id=%s",
                        (self.request_id,))
            self.row = dict(cur.fetchone())
        self.conn.commit()
        if self.row.get("voided_at") is not None:
            raise HTTPException(410, "Invio rimosso: avvia una nuova valutazione")
        return self.row

    def _save(self, column, value):
        # column is one of this module's constants; never public input.
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE public_submission_receipts SET {column}=%s, updated_at=NOW() WHERE request_id=%s",
                        (Json(_json(value)) if column != "status" else value, self.request_id))
        self.conn.commit()
        self.reload()

    def frozen(self, name, factory):
        values = self.row["frozen"]
        if name not in values:
            self._save("frozen", {**values, name: _json(factory())})
        return self.row["frozen"][name]

    def checkpoint(self, name, state, result=None, error=None):
        steps = self.row["checkpoints"]
        self._save("checkpoints", {**steps, name: {"state": state, "result": _json(result),
                                                 "error_type": error}})

    def step(self, name, operation, *, allow_none=False, accepts=None, not_applicable=None):
        prior = self.row["checkpoints"].get(name, {})
        if prior.get("state") in {"succeeded", "not_applicable"}:
            return prior.get("result")
        result = None
        try:
            result = operation()
            if not_applicable is not None and not_applicable(result):
                # A domain's deliberate exclusion is not a technical failure.
                # Keep its permission/business state; never force the operation.
                self.checkpoint(name, "not_applicable", result)
                return result
            if (result is None and not allow_none) or (accepts is not None and not accepts(result)):
                raise ValueError("Incomplete domain operation")
            self.checkpoint(name, "succeeded", result)
            return result
        except Exception as exc:
            # A failure after a domain commit can replay its own idempotent API.
            self.conn.rollback()
            self.reload()
            self.checkpoint(name, "failed", result, error=type(exc).__name__)
            self._save("status", "partial")
            log.warning("public_submission_step_failed request_id=%s step=%s error_type=%s",
                        self.request_id, name, type(exc).__name__)
            raise PartialSubmission() from None

    def direct_once(self, name, operation, *, preflight=None):
        prior = self.row["checkpoints"].get(name, {})
        if prior.get("state") in {"succeeded", "indeterminate"}:
            return
        if prior.get("state") == "sending":
            # Previous process may have died after remote acceptance.
            self.checkpoint(name, "indeterminate", error="InterruptedDelivery")
            return
        # Pure local validation belongs before the durable transport claim.
        # A failure here proves no provider was contacted and can be recovered
        # after configuration/destination is repaired. Other channels continue.
        if preflight is not None:
            try:
                ready = preflight()
            except Exception as exc:
                self.checkpoint(name, "failed", {"before_transport": True}, error=type(exc).__name__)
                return
            if ready is not True:
                self.checkpoint(name, "failed", {"before_transport": True}, error="PreflightFailed")
                return
        self.checkpoint(name, "sending")
        try:
            accepted = operation()
        except Exception as exc:
            self.checkpoint(name, "indeterminate", error=type(exc).__name__)
        else:
            # SMTP False and timeout/HTTP error are not proof of non-delivery.
            self.checkpoint(name, "succeeded" if accepted is True else "indeterminate",
                            {"provider_accepted": accepted is True})

    def attach_on_cursor(self, cur, stima_id, agency_id, detail_id=None):
        cur.execute("""UPDATE public_submission_receipts SET stima_id=%s, agency_id=%s,
                       detail_id=%s, updated_at=NOW() WHERE request_id=%s AND stima_id IS NULL""",
                    (stima_id, agency_id, detail_id, self.request_id))
        if cur.rowcount != 1:
            raise ValueError("Receipt already attached")

    def finish(self, response):
        checkpoints = dict(self.row["checkpoints"])
        checkpoints.pop("pipeline", None)
        self._save("checkpoints", checkpoints)
        self._save("response_payload", response)
        uncertain = any(v.get("state") in {"sending", "indeterminate"}
                        for v in self.row["checkpoints"].values())
        failed = any(v.get("state") == "failed" for v in self.row["checkpoints"].values())
        self._save("status", "partial" if failed else "attention" if uncertain else "completed")
        return self.envelope()

    def _result_available(self, row, result):
        """Server certification that the stored quick result can be consumed.

        Notifications are deliberately excluded: an uncertain or failed
        accessory delivery stays visible in the receipt but never hides a
        stima whose CRM links, capability and private PDF are ready. Any other
        unfinished or failed checkpoint keeps the explicit recovery path.
        """
        if row["kind"] != "quick" or row["status"] not in {"completed", "attention", "partial"}:
            return False
        stima_id, agency_id = row["stima_id"], row["agency_id"]
        response = row.get("response_payload")
        if stima_id is None or agency_id is None or not isinstance(response, dict):
            return False
        token = response.get("token")
        if response.get("id") != stima_id or response.get("pdf_status") != "ready" \
                or not isinstance(token, str) or not token:
            return False
        checkpoints = row["checkpoints"]
        if any(not isinstance(value, dict) or value.get("state") not in DONE_STATES
               for name, value in checkpoints.items() if name not in NOTIFICATION_STEPS):
            return False
        if any(checkpoints.get(name, {}).get("state") not in DONE_STATES for name in QUICK_RESULT_STEPS):
            return False
        receipt = result["receipt"]
        if not (receipt.get("contact_id") and receipt.get("lead_id") and receipt.get("property_id")):
            return False
        try:
            token = str(uuid.UUID(token))
        except (ValueError, TypeError, AttributeError):
            return False
        with self.conn.cursor() as cur:
            # Same capability rule as the PDF/detail endpoints: live and unique.
            cur.execute("""SELECT 1 FROM stime s WHERE s.id=%s AND s.agency_id=%s AND s.token=%s
                             AND s.token_expires > clock_timestamp()
                             AND NOT EXISTS (SELECT 1 FROM stime other WHERE other.token=s.token AND other.id<>s.id)""",
                        (stima_id, agency_id, token))
            capability = cur.fetchone() is not None
            pdf_ready = False
            if capability:
                cur.execute("SELECT to_regclass('stima_pdf_artifacts')")
                if cur.fetchone()[0] is not None:
                    cur.execute("""SELECT 1 FROM stima_pdf_artifacts WHERE stima_id=%s AND agency_id=%s
                                     AND status='ready' AND pdf_bytes IS NOT NULL""", (stima_id, agency_id))
                    pdf_ready = cur.fetchone() is not None
        self.conn.commit()
        return capability and pdf_ready

    def envelope(self):
        row = self.reload()
        steps = {name: value["state"] for name, value in row["checkpoints"].items()}
        status = row["status"]
        result = dict(row.get("response_payload") or {})
        if row["stima_id"] is not None:
            result.setdefault("id", row["stima_id"])
        result["receipt"] = {"request_id": self.request_id, "kind": row["kind"], "status": status,
                             "resumable": status in {"received", "partial"}, "steps": steps,
                             "not_applicable": {name: (value.get("result") or {}).get("status")
                                                for name, value in row["checkpoints"].items()
                                                if value["state"] == "not_applicable"},
                             "errors": {name: {"error_type": value.get("error_type"),
                                               "domain_status": (value.get("result") or {}).get("status") if isinstance(value.get("result"), dict) else None,
                                               "before_transport": bool((value.get("result") or {}).get("before_transport")) if isinstance(value.get("result"), dict) else False}
                                        for name, value in row["checkpoints"].items()
                                        if value["state"] in {"failed", "indeterminate", "sending"}},
                             "stima_id": row["stima_id"], "detail_id": row["detail_id"],
                             "contact_id": (row["checkpoints"].get("bridge", {}).get("result") or {}).get("contact_id"),
                             "lead_id": (row["checkpoints"].get("bridge", {}).get("result") or {}).get("lead_id"),
                             "property_id": (row["checkpoints"].get("property", {}).get("result") or
                                             row["checkpoints"].get("property_detail", {}).get("result") or {}).get("property_id") }
        # The proof authorizes this parent, never arbitrary IDs from a browser.
        if row["stima_id"] is not None:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1 FROM stime WHERE id=%s AND agency_id=%s", (row["stima_id"], row["agency_id"]))
                if cur.fetchone() is None:
                    raise HTTPException(404, "Ricevuta non disponibile")
                cur.execute("SELECT to_regclass('lead_stime'), to_regclass('property_site_sources')")
                lead_table, property_table = cur.fetchone()
                result["receipt"]["contact_id"] = result["receipt"]["lead_id"] = result["receipt"]["property_id"] = None
                if lead_table is not None:
                    cur.execute("""SELECT l.contact_id, l.id FROM lead_stime ls JOIN leads l ON l.id=ls.lead_id
                        WHERE ls.stima_id=%s AND l.agency_id=%s ORDER BY l.id""", (row["stima_id"], row["agency_id"]))
                    links = cur.fetchall()
                    result["receipt"]["contact_id"] = links[0][0] if len(links) == 1 else None
                    result["receipt"]["lead_id"] = links[0][1] if len(links) == 1 else None
                if property_table is not None:
                    # STIMA-CRM-AGENDA-1: una scheda nel Cestino Immobili non e' un
                    # collegamento vivo (stesso predicato di property/site_sync).
                    cur.execute(f"""SELECT pss.property_id FROM property_site_sources pss JOIN properties p ON p.id=pss.property_id
                        WHERE pss.stima_id=%s AND pss.agency_id=%s AND p.agency_id=%s AND pss.status='active'
                          AND {_property_live('p')}
                        ORDER BY pss.id""", (row["stima_id"], row["agency_id"], row["agency_id"]))
                    properties = cur.fetchall()
                    result["receipt"]["property_id"] = properties[0][0] if len(properties) == 1 else None
            self.conn.commit()
        result["receipt"]["notifications"] = {name: steps[name] for name in sorted(NOTIFICATION_STEPS)
                                              if name in steps}
        result["receipt"]["result_available"] = self._result_available(row, result)
        if status != "completed":
            result["success"] = False
            result["ok"] = False
        return result


def authorize_detail(conn, raw):
    try:
        token = str(uuid.UUID(str(raw.get("token", "")).strip()))
    except (ValueError, TypeError):
        raise HTTPException(403, "Token non valido o scaduto") from None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT s.id, s.agency_id FROM stime s WHERE s.token=%s
          AND s.token_expires > clock_timestamp()
          AND NOT EXISTS (SELECT 1 FROM stime other WHERE other.token=s.token AND other.id<>s.id)
          FOR SHARE OF s""", (token,))
        parent = cur.fetchone()
    if parent is None:
        raise HTTPException(403, "Token non valido o scaduto")
    for key in ("stima_id", "id"):
        if raw.get(key) not in (None, ""):
            try:
                matches = int(raw[key]) == parent["id"]
            except (ValueError, TypeError):
                matches = False
            if not matches:
                raise HTTPException(403, "Token non valido o scaduto")
    return dict(parent)


@contextmanager
def open_receipt(connection_factory, request_id, proof_hash, *, kind=None, raw=None, agency_id=None):
    conn = None
    try:
        conn = connection_factory()
        with conn.cursor() as cur:
            # No row lock spans another repository's transaction.
            cur.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", ("public-submission:" + request_id,))
        conn.commit()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM public_submission_receipts WHERE request_id=%s", (request_id,))
            existing = cur.fetchone()
            if existing is None:
                if kind is None or raw is None or agency_id is not None:
                    raise HTTPException(404, "Ricevuta non disponibile")
                if kind == "detail":
                    authorize_detail(conn, raw)  # do not persist forged detail payloads
                clean = payload(raw)
                cur.execute("""INSERT INTO public_submission_receipts
                  (request_id,kind,proof_sha256,payload_sha256,request_payload)
                  VALUES (%s,%s,%s,%s,%s)""", (request_id, kind, proof_hash, _hash(clean), Json(clean)))
            else:
                if agency_id is not None:
                    if existing["agency_id"] != agency_id or existing["stima_id"] is None:
                        raise HTTPException(404, "Ricevuta non disponibile")
                    cur.execute("SELECT 1 FROM stime WHERE id=%s AND agency_id=%s", (existing["stima_id"], agency_id))
                    if cur.fetchone() is None:
                        raise HTTPException(404, "Ricevuta non disponibile")
                elif not hmac.compare_digest(existing["proof_sha256"], proof_hash):
                    raise HTTPException(404, "Ricevuta non disponibile")
                if kind is not None and (existing["kind"] != kind or existing["payload_sha256"] != _hash(payload(raw))):
                    raise HTTPException(409, "Richiesta già ricevuta con dati differenti: avvia una nuova valutazione")
        conn.commit()
        receipt = Receipt(conn, request_id, agency_id=agency_id)
        if receipt.row["stima_id"] is not None:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM stime WHERE id=%s AND agency_id=%s",
                            (receipt.row["stima_id"], receipt.row["agency_id"]))
                if cur.fetchone() is None:
                    raise HTTPException(404, "Ricevuta non disponibile")
            conn.commit()
        if agency_id is None:
            if not receipt.row["live"]:
                raise HTTPException(403, "Ricevuta scaduta")
            if receipt.row["kind"] == "detail":
                authorize_detail(conn, receipt.row["request_payload"])
                conn.commit()
        yield receipt
    except HTTPException:
        raise
    except PartialSubmission:
        raise
    except Exception as exc:
        log.warning("public_submission_unavailable request_id=%s error_type=%s", request_id, type(exc).__name__)
        raise HTTPException(503, "Ricevuta temporaneamente non disponibile; conserva l'identità dell'invio") from None
    finally:
        if conn is not None:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", ("public-submission:" + request_id,))
                conn.commit()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
