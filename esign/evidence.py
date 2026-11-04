"""FIRMA OTP - il REGISTRO PROBATORIO: append-only, concatenato, sigillato.

Ogni voce e' `{request_id, seq, event_type, occurred_at, actor_kind, actor_ref, signer_id, details}` in forma canonica
(JSON ordinato, senza spazi). `entry_hash = SHA-256(prev_hash || canonica)`; `seal = HMAC-SHA256(chiave, entry_hash)`
con `ESIGN_EVIDENCE_KEY` fuori dal database. Toccare, togliere o inserire una voce rompe la catena; rifarla tutta richiede
la chiave del servizio, che un accesso al solo database non ha. Non e' una marca temporale qualificata e non lo dichiara:
la data e' l'orologio del server, scritta nella voce e nel sigillo.

I `details` sono MINIMI e non contengono mai codici, token, password o testi liberi del cliente oltre a cio' che il
rapporto deve riportare (impronte, versioni, esiti, indirizzo email del firmatario).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from psycopg2.extras import Json

from . import otp_security

COLUMNS = "id, request_id, agency_id, seq, event_type, occurred_at, actor_kind, actor_ref, signer_id, details, prev_hash, entry_hash, seal"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def canonical(*, request_id: int, seq: int, event_type: str, occurred_at: datetime, actor_kind: str,
              actor_ref: str | None, signer_id: int | None, details: dict) -> str:
    return json.dumps({"request_id": int(request_id), "seq": int(seq), "event_type": event_type,
                       "occurred_at": _iso(occurred_at), "actor_kind": actor_kind, "actor_ref": actor_ref,
                       "signer_id": signer_id, "details": details},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(prev_hash: str | None, canonical_text: str) -> str:
    return hashlib.sha256(((prev_hash or "") + canonical_text).encode("utf-8")).hexdigest()


def seal(digest: str, *, key: str | None = None) -> str:
    return hmac.new((key or otp_security.evidence_key()).encode("utf-8"), digest.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def append(cur, *, request_id: int, agency_id: int, event_type: str, actor_kind: str, actor_ref: str | None = None,
           signer_id: int | None = None, details: dict | None = None, occurred_at: datetime | None = None) -> dict:
    """Una voce nuova in coda alla catena della richiesta. Il chiamante tiene il lock della richiesta."""
    details = dict(details or {})
    occurred_at = occurred_at or datetime.now(timezone.utc)
    cur.execute("SELECT seq, entry_hash FROM signature_evidence WHERE request_id = %s ORDER BY seq DESC LIMIT 1",
                (request_id,))
    last = cur.fetchone()
    seq = (last["seq"] + 1) if last else 1
    prev = last["entry_hash"] if last else None
    text = canonical(request_id=request_id, seq=seq, event_type=event_type, occurred_at=occurred_at,
                     actor_kind=actor_kind, actor_ref=actor_ref, signer_id=signer_id, details=details)
    digest = entry_hash(prev, text)
    cur.execute(f"INSERT INTO signature_evidence (request_id, agency_id, seq, event_type, occurred_at, actor_kind, "
                "actor_ref, signer_id, details, prev_hash, entry_hash, seal) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                f"%s, %s, %s) RETURNING {COLUMNS}",
                (request_id, agency_id, seq, event_type, occurred_at, actor_kind, actor_ref, signer_id, Json(details),
                 prev, digest, seal(digest)))
    return dict(cur.fetchone())


def entries(cur, request_id: int) -> list[dict]:
    cur.execute(f"SELECT {COLUMNS} FROM signature_evidence WHERE request_id = %s ORDER BY seq", (request_id,))
    return [dict(r) for r in cur.fetchall()]


def verify(cur, request_id: int) -> dict:
    """Ricalcola catena e sigilli. `ok` solo se ogni voce regge; `problems` dice quale e perche'."""
    rows = entries(cur, request_id)
    problems: list[str] = []
    prev = None
    try:
        key = otp_security.evidence_key()
    except otp_security.NotConfigured:
        key = None
        problems.append("chiave delle prove non configurata: sigilli non verificabili")
    for expected_seq, row in enumerate(rows, start=1):
        if row["seq"] != expected_seq:
            problems.append(f"sequenza interrotta alla voce {row['seq']} (attesa {expected_seq})")
        if row["prev_hash"] != prev:
            problems.append(f"voce {row['seq']}: impronta precedente non corrisponde")
        text = canonical(request_id=row["request_id"], seq=row["seq"], event_type=row["event_type"],
                         occurred_at=row["occurred_at"], actor_kind=row["actor_kind"], actor_ref=row["actor_ref"],
                         signer_id=row["signer_id"], details=row["details"])
        digest = entry_hash(row["prev_hash"], text)
        if digest != row["entry_hash"]:
            problems.append(f"voce {row['seq']}: contenuto alterato")
        if key is not None and not hmac.compare_digest(seal(digest, key=key), row["seal"]):
            problems.append(f"voce {row['seq']}: sigillo non valido")
        prev = row["entry_hash"]
    return {"ok": not problems and bool(rows), "count": len(rows), "head": prev, "problems": problems}


def public_view(row: dict) -> dict:
    """La voce come la vede il CRM e il rapporto: niente che non sia gia' minimizzato."""
    return {"seq": row["seq"], "event_type": row["event_type"], "occurred_at": _iso(row["occurred_at"]),
            "actor_kind": row["actor_kind"], "actor_ref": row["actor_ref"], "signer_id": row["signer_id"],
            "details": row["details"], "entry_hash": row["entry_hash"]}
