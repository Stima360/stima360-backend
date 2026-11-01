"""Prova del relay contro il codice REALE del servizio di produzione (`main`).

    python integrations/whatsapp_relay/verify_production.py <checkout di main con la patch> <checkout di main senza patch>

Importa `main.py` di produzione cosi' com'e', con il database sostituito da un
registratore (nessuna connessione) e ogni chiamata HTTP in uscita intercettata.
Confronta il comportamento con e senza patch: stessa risposta a Meta, stesso
salvataggio in `whatsapp_incoming`, stesso invio del template della stima;
con il relay acceso inoltra il corpo firmato (verificato con la verifica del
CRM), con il CRM giu' la produzione risponde e salva lo stesso.
Esce con 0 solo se tutte le prove sono superate. Non stampa segreti.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import sys
from pathlib import Path

CRM_ROOT = Path(__file__).resolve().parents[2]
META_SECRET = "segreto-meta-sintetico-solo-per-la-prova"
RELAY_SECRET = "segreto-sintetico-dell-inoltro-lungo-almeno-32"
TESTER, CUSTOMER = "393000000001", "393999999999"
PROBLEMS: list[str] = []


def need(ok, msg):
    print(("OK   " if ok else "FAIL ") + msg, flush=True)
    if not ok:
        PROBLEMS.append(msg)


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def close(self):
        pass


class FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append(("COMMIT", None))

    def close(self):
        pass


def load(checkout: Path):
    for name in list(sys.modules):
        if name == "main" or name == "whatsapp_relay":
            del sys.modules[name]
    sys.path.insert(0, str(checkout))
    try:
        return importlib.import_module("main")
    finally:
        sys.path.remove(str(checkout))


def payload():
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "393295674589", "phone_number_id": "900000000000001"},
        "contacts": [{"wa_id": TESTER, "profile": {"name": "Prova"}}, {"wa_id": CUSTOMER, "profile": {"name": "Cliente"}}],
        "messages": [{"from": TESTER, "id": "wamid.p1", "timestamp": "1762000000", "type": "text", "text": {"body": "uno"}},
                     {"from": CUSTOMER, "id": "wamid.p2", "timestamp": "1762000000", "type": "text", "text": {"body": "due"}}],
    }}]}]}


def run(checkout: Path, *, relay_env: dict, crm_status=200, crm_raises=False, signature_ok=True):
    """Una consegna Meta al webhook reale. Ritorna (status, inserimenti, inoltri, altre chiamate HTTP)."""
    import requests
    from fastapi.testclient import TestClient

    for k in ("WHATSAPP_RELAY_URL", "WHATSAPP_RELAY_SECRET", "WHATSAPP_RELAY_SENDERS"):
        os.environ.pop(k, None)
    os.environ.update(relay_env)
    os.environ["WHATSAPP_APP_SECRET"] = META_SECRET
    main = load(checkout)
    db: list = []
    forwarded: list = []
    other: list = []
    main.get_connection = lambda: FakeConn(db)
    url = relay_env.get("WHATSAPP_RELAY_URL")

    class R:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

    def fake_post(u, *a, **k):
        if url and u == url:
            forwarded.append(k)
            if crm_raises:
                raise requests.exceptions.ConnectionError("CRM irraggiungibile")
            return R(crm_status)
        other.append(u)
        return R(200)

    real_post = requests.post
    requests.post = fake_post
    relay_mod = sys.modules.get("whatsapp_relay")
    if relay_mod is not None:
        relay_mod.BACKOFF = (0.0, 0.0)
    try:
        body = json.dumps(payload()).encode()
        sig = "sha256=" + hmac.new(META_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not signature_ok:
            sig = "sha256=" + "0" * 64
        r = TestClient(main.app).post("/webhook/whatsapp", content=body,
                                      headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        return r.status_code, r.json(), [x for x in db if x[0].startswith("INSERT")], forwarded, other
    finally:
        requests.post = real_post


def estimate_send(checkout: Path):
    """L'invio del template della stima (`invia_whatsapp`): URL e corpo."""
    import requests

    main = load(checkout)
    sent = []

    class R:
        status_code = 200
        text = "ok"

    real_post = requests.post
    requests.post = lambda u, *a, **k: sent.append((u, k.get("json"))) or R()
    try:
        main.invia_whatsapp("3331234567", "Nome", "Indirizzo", "token")
    finally:
        requests.post = real_post
    return sent


def main_entry(patched: Path, original: Path) -> int:
    sys.path.insert(0, str(CRM_ROOT))
    from inbound_messages import relay as crm_relay

    on = {"WHATSAPP_RELAY_URL": "https://stima360-backend-test.onrender.com/webhook/whatsapp",
          "WHATSAPP_RELAY_SECRET": RELAY_SECRET, "WHATSAPP_RELAY_SENDERS": TESTER}

    base = run(original, relay_env={})
    off = run(patched, relay_env={})
    need(base[0] == off[0] == 200 and base[1] == off[1] == {"ok": True}, "relay spento: stessa risposta 200 a Meta")
    need(base[2] == off[2] and len(off[2]) == 1, "relay spento: stesso salvataggio in whatsapp_incoming")
    need(off[3] == [] and off[4] == [], "relay spento: nessuna chiamata HTTP in uscita")

    act = run(patched, relay_env=on)
    need(act[0] == 200 and act[2] == base[2], "relay acceso: risposta e salvataggio invariati")
    need(len(act[3]) == 1, "relay acceso: un inoltro al CRM")
    if act[3]:
        k = act[3][0]
        body, headers = k["data"], k["headers"]
        os.environ[crm_relay.RELAY_SECRET_ENV] = RELAY_SECRET
        try:
            crm_relay.verify(body, headers)
            verified = True
        except crm_relay.RelayRejected:
            verified = False
        need(verified, "relay acceso: firma accettata dalla verifica del CRM")
        need(CUSTOMER.encode() not in body and b"Cliente" not in body, "relay acceso: il cliente fuori elenco non esce")
        need(k["timeout"] == (3.0, 5.0), "relay acceso: timeout brevi")

    for label, kw in (("CRM irraggiungibile", {"crm_raises": True}), ("CRM in errore 500", {"crm_status": 500}),
                      ("CRM rifiuta 403", {"crm_status": 403})):
        res = run(patched, relay_env=on, **kw)
        need(res[0] == 200 and res[2] == base[2], f"{label}: la produzione risponde 200 e salva")
    res = run(patched, relay_env=on, crm_raises=True)
    need(len(res[3]) == 3, "CRM irraggiungibile: tentativi limitati a 3")

    star = run(patched, relay_env={**on, "WHATSAPP_RELAY_SENDERS": ""})
    need(star[3] == [] and star[0] == 200, "configurazione incompleta: inoltro spento")

    bad = run(patched, relay_env=on, signature_ok=False)
    need(bad[0] == 403 and bad[2] == [] and bad[3] == [], "firma Meta errata: 403, nulla salvato, nulla inoltrato")

    need(estimate_send(original) == estimate_send(patched), "invio del template della stima identico")

    print("ESITO: " + ("TUTTE LE PROVE SUPERATE" if not PROBLEMS else f"{len(PROBLEMS)} PROVE FALLITE"))
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main_entry(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()))
