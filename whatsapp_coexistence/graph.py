"""WHATSAPP COEXISTENCE - le poche chiamate alla Graph API di Meta.

Ogni chiamata restituisce `(ok, body, error)` e non solleva mai: un errore di
Meta o di rete diventa un codice tecnico (`meta_<code>`, `http_<status>`,
`timeout`, `network`) senza dati personali e senza token. La versione dell'API
e' sempre quella dichiarata (`WHATSAPP_GRAPH_VERSION`), mai un valore
predefinito: la stessa regola dell'invio (message_replies.transport).

Il token passato qui non viene mai registrato, restituito o scritto nei log.
"""
from __future__ import annotations

import requests

from .enums import REQUEST_TIMEOUT

BASE = "https://graph.facebook.com"


class Graph:
    def __init__(self, version: str, session=None):
        self.version = version
        self.session = session or requests

    def _url(self, path: str) -> str:
        return f"{BASE}/{self.version}/{path.lstrip('/')}"

    def _call(self, method: str, path: str, *, token: str | None = None, params=None, json=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self.session.request(method, self._url(path), params=params, json=json, headers=headers,
                                            timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout:
            return False, None, "timeout"
        except requests.exceptions.RequestException:
            return False, None, "network"
        try:
            body = response.json()
        except ValueError:
            body = None
        if 200 <= response.status_code < 300 and not (isinstance(body, dict) and body.get("error")):
            return True, body if isinstance(body, dict) else {}, None
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        sub = error.get("error_subcode") if isinstance(error, dict) else None
        detail = f"meta_{code}" if code is not None else f"http_{response.status_code}"
        if sub is not None:
            detail += f"_{sub}"
        return False, None, detail

    def exchange_code(self, app_id: str, app_secret: str, code: str):
        """Il codice della procedura in un token aziendale (usato subito e poi dimenticato)."""
        return self._call("GET", "oauth/access_token",
                          params={"client_id": app_id, "client_secret": app_secret, "code": code})

    def waba_phone_numbers(self, token: str, waba_id: str):
        return self._call("GET", f"{waba_id}/phone_numbers", token=token,
                          params={"fields": "id,display_phone_number,verified_name,platform_type,"
                                            "code_verification_status,is_on_biz_app"})

    def phone_number(self, token: str, phone_number_id: str):
        return self._call("GET", phone_number_id, token=token,
                          params={"fields": "id,display_phone_number,verified_name,platform_type,status,"
                                            "is_on_biz_app,quality_rating"})

    def subscribe_app(self, token: str, waba_id: str, *, callback_url: str | None = None,
                      verify_token: str | None = None):
        body = None
        if callback_url and verify_token:
            body = {"override_callback_uri": callback_url, "verify_token": verify_token}
        return self._call("POST", f"{waba_id}/subscribed_apps", token=token, json=body)

    def request_sync(self, token: str, phone_number_id: str, sync_type: str):
        return self._call("POST", f"{phone_number_id}/smb_app_data", token=token,
                          json={"messaging_product": "whatsapp", "sync_type": sync_type})
