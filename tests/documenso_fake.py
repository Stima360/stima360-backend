"""Un Documenso FINTO, in-process, per provare l'adattatore sul filo (HTTP vero, su 127.0.0.1).

Implementa SOLO gli endpoint v2 dell'SDK ufficiale che l'adattatore usa e registra ogni richiesta, cosi' i test
possono verificare cosa e' stato davvero mandato (nessuna email, due firmatari, campi, segreto). Non e' Documenso:
non prova nulla sul comportamento reale del servizio, solo che il nostro codice parla come l'SDK dice.
"""
from __future__ import annotations

import email.parser
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "api_token-sintetico-di-prova-0001"
WEBHOOK_SECRET = "segreto-webhook-sintetico-0001"


class FakeDocumenso:
    def __init__(self, token: str = TOKEN):
        self.token = token
        self.envelopes: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.fail: dict[str, int] = {}          # "METODO /percorso-prefisso" -> codice HTTP da restituire una volta
        self.download_as_json = False
        self.tamper_original = False
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):          # silenzio
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                url = urlparse(self.path)
                path = url.path[len("/api/v2"):] if url.path.startswith("/api/v2") else url.path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                entry = {"method": method, "path": path, "query": parse_qs(url.query), "auth": self.headers.get("Authorization"),
                         "ctype": self.headers.get("Content-Type"), "raw": raw}
                with fake._lock:
                    fake.requests.append(entry)
                    for pattern, code in list(fake.fail.items()):
                        m, _, prefix = pattern.partition(" ")
                        if m == method and path.startswith(prefix):
                            fake.fail.pop(pattern)
                            return self._send(code, {"message": "errore finto"})
                    if self.headers.get("Authorization") != fake.token:
                        return self._send(401, {"message": "unauthorized"})
                    return fake._route(self, method, path, url, raw, entry)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/api/v2"
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ---- routing ----
    def _route(self, h, method, path, url, raw, entry):
        if method == "POST" and path == "/envelope/create":
            msg = email.parser.BytesParser().parsebytes(b"Content-Type: " + entry["ctype"].encode() + b"\r\n\r\n" + raw)
            parts = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.get_payload()}
            payload = json.loads(parts["payload"])
            entry["payload"] = payload
            eid = "envelope_" + uuid.uuid4().hex[:12]
            recipients = []
            for i, r in enumerate(payload.get("recipients", []), start=1):
                recipients.append({"id": 1000 + len(self.envelopes) * 10 + i, "email": r["email"], "name": r["name"],
                                   "role": r["role"], "token": uuid.uuid4().hex[:20], "signingOrder": r.get("signingOrder"),
                                   "signingStatus": "NOT_SIGNED", "signedAt": None})
            self.envelopes[eid] = {"id": eid, "externalId": payload.get("externalId"), "title": payload["title"],
                                   "status": "DRAFT", "recipients": recipients, "pdf": parts["files"], "deletedAt": None,
                                   "completedAt": None, "payload": payload, "items": [{"id": "envelope_item_" + uuid.uuid4().hex[:8]}]}
            return h._send(200, {"id": eid})
        if method == "GET" and path == "/envelope":
            data = [{"id": e["id"], "externalId": e["externalId"], "status": e["status"], "title": e["title"],
                     "deletedAt": e["deletedAt"]} for e in self.envelopes.values()
                    if (parse_qs(url.query).get("query", [""])[0] in (e["title"] + " " + str(e["externalId"])))]
            return h._send(200, {"data": data, "count": len(data), "currentPage": 1, "perPage": 50, "totalPages": 1})
        if method == "POST" and path == "/envelope/distribute":
            body = json.loads(raw)
            entry["payload"] = body
            e = self.envelopes.get(body["envelopeId"])
            if e is None:
                return h._send(404, {"message": "not found"})
            e["status"] = "PENDING"
            return h._send(200, {"success": True, "id": e["id"], "recipients": [
                {"id": r["id"], "name": r["name"], "email": r["email"], "token": r["token"], "role": r["role"],
                 "signingOrder": r["signingOrder"], "signingUrl": f"{self.origin}/sign/{r['token']}"} for r in e["recipients"]]})
        if method == "POST" and path == "/envelope/delete":
            body = json.loads(raw)
            e = self.envelopes.get(body["envelopeId"])
            if e is None:
                return h._send(404, {"message": "not found"})
            e["deletedAt"] = "2026-11-03T10:00:00.000Z"
            return h._send(200, {"success": True})
        if method == "GET" and path.startswith("/envelope/item/") and path.endswith("/download"):
            item = path.split("/")[3]
            e = next((x for x in self.envelopes.values() if any(i["id"] == item for i in x["items"])), None)
            if e is None:
                return h._send(404, {"message": "not found"})
            version = parse_qs(url.query).get("version", ["signed"])[0]
            body = e["pdf"] if version == "original" else e["pdf"] + b"\n%FIRMATO-DAL-FINTO"
            if version == "original" and self.tamper_original:
                body = b"%PDF-1.4 altro documento"
            if self.download_as_json:
                self.blobs = getattr(self, "blobs", {})
                key = uuid.uuid4().hex
                self.blobs[key] = body
                return h._send(200, {"downloadUrl": f"http://invalid.example/{key}"})
            return h._send(200, body, "application/pdf")
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 3 and parts[0] == "envelope" and parts[2] == "audit-log":
            e = self.envelopes.get(parts[1])
            if e is None:
                return h._send(404, {"message": "not found"})
            return h._send(200, {"data": [{"type": "DOCUMENT_CREATED", "id": "a1"}] + [
                {"type": "DOCUMENT_RECIPIENT_COMPLETED", "id": f"s{r['id']}"} for r in e["recipients"] if r["signingStatus"] == "SIGNED"],
                "count": 1, "currentPage": 1, "perPage": 100, "totalPages": 1})
        if method == "GET" and len(parts) == 2 and parts[0] == "envelope":
            e = self.envelopes.get(parts[1])
            if e is None:
                return h._send(404, {"message": "not found"})
            return h._send(200, {"id": e["id"], "status": e["status"], "externalId": e["externalId"], "title": e["title"],
                                 "deletedAt": e["deletedAt"], "completedAt": e["completedAt"],
                                 "recipients": e["recipients"], "envelopeItems": e["items"]})
        return h._send(404, {"message": "route sconosciuta nel finto"})

    # ---- azioni di test (il firmatario che firma, rifiuta) ----
    def sign(self, envelope_id: str, email_address: str, when: str = "2026-11-03T10:30:00.000Z"):
        e = self.envelopes[envelope_id]
        for r in e["recipients"]:
            if r["email"].lower() == email_address.lower():
                r["signingStatus"], r["signedAt"] = "SIGNED", when
        if all(r["signingStatus"] == "SIGNED" for r in e["recipients"]):
            e["status"], e["completedAt"] = "COMPLETED", when

    def reject(self, envelope_id: str, email_address: str):
        e = self.envelopes[envelope_id]
        for r in e["recipients"]:
            if r["email"].lower() == email_address.lower():
                r["signingStatus"] = "REJECTED"
        e["status"] = "REJECTED"

    def only(self, method: str, path_prefix: str) -> list[dict]:
        return [r for r in self.requests if r["method"] == method and r["path"].startswith(path_prefix)]


def webhook_body(event: str, envelope: dict, *, with_external: bool = True) -> bytes:
    """Un corpo di webhook come lo descrive la documentazione: {event, payload, createdAt, webhookEndpoint}."""
    payload = {"id": 123, "status": envelope["status"], "title": envelope["title"]}
    if with_external:
        payload["externalId"] = envelope["externalId"]
    return json.dumps({"event": event, "payload": payload, "createdAt": "2026-11-03T10:30:00.000Z",
                       "webhookEndpoint": "https://crm.invalid/hook"}).encode()
