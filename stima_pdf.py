"""Private stima reports. Parent authorization precedes every artifact access.

The durable rendering snapshot is created before generation; retries never
re-submit the public form or recalculate a valuation. Lock order is always
stime (SHARE), then its artifact (UPDATE for generation, SHARE for reads).
"""
from __future__ import annotations

import copy
import hashlib
import logging
import uuid

from fastapi import HTTPException
from psycopg2.extras import Json

from database import get_connection
from pdf_report import genera_pdf_stima

log = logging.getLogger(__name__)
UNAVAILABLE = "PDF non disponibile: riprova dal collegamento della stima"


def _cleanup(conn, method):
    # A disconnected connection can also fail rollback/close. Preserve the
    # authorized result or generic HTTP error instead of masking it.
    if conn is None:
        return
    try:
        getattr(conn, method)()
    except Exception as exc:
        log.warning("Private PDF cleanup failed action=%s error_type=%s", method, type(exc).__name__)


def _authorize(cur, stima_id, token, agency_id):
    if token is not None:
        try:
            token = str(uuid.UUID(token.strip()))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=404, detail="PDF non disponibile")
        cur.execute("""
            SELECT s.agency_id FROM stime s
            WHERE s.id=%s AND s.token=%s AND s.token_expires > clock_timestamp()
              AND NOT EXISTS (SELECT 1 FROM stime other
                              WHERE other.token=s.token AND other.id<>s.id)
            FOR SHARE OF s
        """, (stima_id, token))
    elif agency_id is not None:
        cur.execute("SELECT agency_id FROM stime WHERE id=%s AND agency_id=%s FOR SHARE",
                    (stima_id, agency_id))
    else:
        raise HTTPException(status_code=404, detail="PDF non disponibile")
    parent = cur.fetchone()
    if parent is None:
        raise HTTPException(status_code=404, detail="PDF non disponibile")
    return parent[0]


def _valid_pdf(pdf, digest=None):
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF"):
        return False
    return digest is None or hashlib.sha256(pdf).hexdigest() == digest


def prepare(stima_id, agency_id, payload, connection_factory=get_connection):
    """Commit the original server-calculated rendering input, once only."""
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            bound_agency = _authorize(cur, stima_id, None, agency_id)
            cur.execute("""
                INSERT INTO stima_pdf_artifacts (stima_id, agency_id, render_payload)
                VALUES (%s,%s,%s) ON CONFLICT (stima_id) DO NOTHING
            """, (stima_id, bound_agency, Json(payload)))
            cur.execute("SELECT agency_id, render_payload FROM stima_pdf_artifacts WHERE stima_id=%s FOR SHARE", (stima_id,))
            stored = cur.fetchone()
            if stored is None or stored[0] != bound_agency or stored[1] != payload:
                raise ValueError("PDF snapshot mismatch")
        conn.commit()
    except Exception:
        _cleanup(conn, "rollback")
        raise
    finally:
        _cleanup(conn, "close")


def _artifact(cur, stima_id, agency_id, lock):
    # lock is an internal constant, never client-controlled.
    cur.execute("""
        SELECT render_payload, status, pdf_bytes, sha256
        FROM stima_pdf_artifacts WHERE stima_id=%s AND agency_id=%s
        FOR """ + lock, (stima_id, agency_id))
    artifact = cur.fetchone()
    if artifact is None:
        raise HTTPException(status_code=404, detail="PDF non disponibile")
    return artifact


def download(stima_id, token=None, agency_id=None, connection_factory=get_connection):
    conn = None
    try:
        conn = connection_factory()
        with conn.cursor() as cur:
            bound_agency = _authorize(cur, stima_id, token, agency_id)
            _, status, stored, digest = _artifact(cur, stima_id, bound_agency, "SHARE")
            _authorize(cur, stima_id, token, agency_id)
            pdf = bytes(stored) if stored is not None else None
            if status != "ready" or not _valid_pdf(pdf, digest):
                raise HTTPException(status_code=503, detail=UNAVAILABLE)
        conn.commit()
        return pdf
    except HTTPException:
        if conn is not None:
            _cleanup(conn, "rollback")
        raise
    except Exception as exc:
        if conn is not None:
            _cleanup(conn, "rollback")
        log.warning("Private PDF read failed stima_id=%s error_type=%s", stima_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from None
    finally:
        if conn is not None:
            _cleanup(conn, "close")


def generate(stima_id, renderer=genera_pdf_stima, token=None, agency_id=None,
             connection_factory=get_connection):
    conn = None
    try:
        conn = connection_factory()
        with conn.cursor() as cur:
            bound_agency = _authorize(cur, stima_id, token, agency_id)
            payload, status, stored, digest = _artifact(cur, stima_id, bound_agency, "UPDATE")
            # A request may have waited on a concurrent render past expiry.
            _authorize(cur, stima_id, token, agency_id)
            pdf = bytes(stored) if stored is not None else None
            if status == "ready" and _valid_pdf(pdf, digest):
                conn.commit()
                return pdf
            try:
                pdf = renderer(copy.deepcopy(payload), nome_file=f"stima_{stima_id}.pdf")
                if not _valid_pdf(pdf):
                    raise ValueError("Invalid rendered PDF")
            except Exception as exc:
                cur.execute("""
                    UPDATE stima_pdf_artifacts SET status='failed', pdf_bytes=NULL,
                      sha256=NULL, attempts=attempts+1, last_error=%s, updated_at=NOW()
                    WHERE stima_id=%s
                """, (type(exc).__name__[:120], stima_id))
                conn.commit()
                log.warning("Private PDF generation failed stima_id=%s error_type=%s", stima_id, type(exc).__name__)
                raise HTTPException(status_code=503, detail=UNAVAILABLE) from None
            # Rendering itself may have outlived the public capability.
            _authorize(cur, stima_id, token, agency_id)
            cur.execute("""
                UPDATE stima_pdf_artifacts SET status='ready', pdf_bytes=%s,
                  sha256=%s, attempts=attempts+1, last_error=NULL, updated_at=NOW()
                WHERE stima_id=%s
            """, (pdf, hashlib.sha256(pdf).hexdigest(), stima_id))
        conn.commit()
        return pdf
    except HTTPException:
        if conn is not None:
            _cleanup(conn, "rollback")
        raise
    except Exception as exc:
        if conn is not None:
            _cleanup(conn, "rollback")
        log.warning("Private PDF save failed stima_id=%s error_type=%s", stima_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from None
    finally:
        if conn is not None:
            _cleanup(conn, "close")
