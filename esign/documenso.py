"""Adattatore Documenso (API v2, BUSTE) per la firma dei fogli visita.

SCRITTO SUL CONTRATTO UFFICIALE DELL'SDK (documenso-sdk-python 0.6.0, generato dalla specifica OpenAPI v2):

    POST /envelope/create               multipart: `payload` (JSON) + `files` -> {id}
    POST /envelope/distribute           {envelopeId, meta} -> {success, id, recipients[{id,email,token,signingUrl}]}
    GET  /envelope/{id}                 stato (DRAFT | PENDING | COMPLETED | REJECTED), destinatari, voci
    GET  /envelope                      ricerca (query, page, perPage, status, ...)
    GET  /envelope/{id}/audit-log       registro eventi del fornitore (paginato)
    GET  /envelope/item/{itemId}/download?version=signed|original
    POST /envelope/delete               {envelopeId}
    Autenticazione: intestazione `Authorization: <token API>` (senza prefisso).

COSA FA E COSA NON FA

* NESSUN INVIO. La busta e' creata e «distribuita» con `distributionMethod = NONE` e TUTTE le email del fornitore
  spente (`emailSettings`): Documenso non scrive a nessuno. Il collegamento di firma personale di ogni firmatario
  (`signingUrl`) torna nella risposta e lo mostra il CRM all'operatore, che lo apre o lo fa leggere con il QR.
  Non esiste un'impostazione che accenda l'invio da Documenso: servirebbe un'autorizzazione e un cambio di codice.
* LIVELLO DI FIRMA: sempre `ses` (firma elettronica SEMPLICE). Documenso, in questo uso, non identifica il firmatario
  con un documento: un collegamento personale non e' una firma avanzata ne' qualificata, e nulla qui lo dichiara tale.
* DUE FIRMATARI DISTINTI (cliente e agente), ciascuno con il proprio campo firma e il proprio collegamento.
* IDEMPOTENZA: la chiave della richiesta e' l'`externalId` della busta. Prima di creare si cerca una busta con quella
  chiave: se il nostro INSERT fallisce dopo la creazione, il tentativo successivo ritrova la stessa busta.
* PROVENIENZA: prima di dare per buono il PDF firmato, si scarica anche l'originale e se ne confronta l'impronta con
  quella del foglio visita che abbiamo caricato (`DOCUMENSO_VERIFY_ORIGINAL`, acceso di default).
* WEBHOOK: Documenso manda un SEGRETO CONDIVISO in `X-Documenso-Secret`, non una firma HMAC del corpo. Si confronta a
  tempo costante; poiche' un segreto non protegge il contenuto, l'evento non decide mai nulla da solo: lo stato si
  RILEGGE con `GET /envelope/{id}` (vedi `esign.service.handle_provider_event`). Senza segreto configurato ogni
  evento e' rifiutato.

CONFIGURAZIONE (solo variabili d'ambiente, mai nel repository)

    ESIGN_PROVIDER             = documenso
    DOCUMENSO_API_URL          = https://app.documenso.com/api/v2   (Cloud; per l'auto-ospitato: https://<host>/api/v2)
    DOCUMENSO_API_TOKEN        = token API (segreto)
    DOCUMENSO_WEBHOOK_SECRET   = segreto del webhook (stessa stringa configurata in Documenso)
    DOCUMENSO_ENVIRONMENT      = sandbox (default) | production
    DOCUMENSO_TIMEOUT_SECONDS  = 20 (default)
    DOCUMENSO_VERIFY_ORIGINAL  = 1 (default)

`sandbox` e `production` non cambiano le chiamate: cambiano come il CRM CONTA la firma. Solo `production` puo'
risultare «valida»; con `sandbox` (default) una firma resta di prova, anche se e' stata eseguita davvero.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse

from . import providers
from .providers import (InvalidEventSignature, ProviderDocument, ProviderError, ProviderEvent, ProviderNotConfigured,
                        ProviderRequest, ProviderSigner, ProviderStatus, SignerSpec, SignerStatus)

NAME = "documenso"
DEFAULT_API_URL = "https://app.documenso.com/api/v2"
ENV_URL, ENV_TOKEN, ENV_SECRET = "DOCUMENSO_API_URL", "DOCUMENSO_API_TOKEN", "DOCUMENSO_WEBHOOK_SECRET"
ENV_ENVIRONMENT, ENV_TIMEOUT, ENV_VERIFY = "DOCUMENSO_ENVIRONMENT", "DOCUMENSO_TIMEOUT_SECONDS", "DOCUMENSO_VERIFY_ORIGINAL"
SECRET_HEADER = "x-documenso-secret"
MIN_SECRET_LENGTH = 16
MAX_PDF_BYTES = 20 * 1024 * 1024
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

#: Tutte le email del fornitore spente: nessun messaggio parte da Documenso.
EMAIL_OFF = {"recipientSigningRequest": False, "recipientRemoved": False, "recipientSigned": False,
             "documentPending": False, "documentCompleted": False, "documentDeleted": False,
             "ownerDocumentCompleted": False}
DISTRIBUTION_METHOD = "NONE"

STATUS_MAP = {"DRAFT": "pending", "PENDING": "pending", "COMPLETED": "signed", "REJECTED": "declined"}
SIGNER_MAP = {"NOT_SIGNED": "pending", "SIGNED": "signed", "REJECTED": "declined"}
KNOWN_EVENTS = ("DOCUMENT_CREATED", "DOCUMENT_SENT", "DOCUMENT_OPENED", "DOCUMENT_SIGNED", "DOCUMENT_COMPLETED",
                "DOCUMENT_REJECTED", "DOCUMENT_CANCELLED", "DOCUMENT_RECIPIENT_COMPLETED")


def _parse_time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _checked_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ProviderNotConfigured("DOCUMENSO_API_URL non valido")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise ProviderNotConfigured("DOCUMENSO_API_URL deve essere https")
    return value.rstrip("/")


def _public_https(url: str) -> bool:
    """https e mai un indirizzo locale o privato: l'URL arriva dal fornitore, non lo seguiamo verso la nostra rete."""
    parts = urlparse(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host or parts.username or parts.password or host == "localhost":
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified)


def field_geometry(box: dict) -> dict:
    """Un riquadro in PUNTI PDF (origine in basso a sinistra) -> percentuali della pagina con origine IN ALTO a sinistra
    (la convenzione dei campi di Documenso: percentuali della pagina). ASSUNZIONE da confermare con la prima firma reale:
    e' isolata in questa funzione."""
    pw, ph = float(box["page_width"]), float(box["page_height"])
    x, y, w, h = float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"])
    return {"page": int(box["page"]), "positionX": round(x / pw * 100, 4), "positionY": round((ph - (y + h)) / ph * 100, 4),
            "width": round(w / pw * 100, 4), "height": round(h / ph * 100, 4)}


class DocumensoProvider:
    name = NAME
    is_simulation = False
    supports_signers = True

    def __init__(self, *, http=None, env=None):
        env = os.environ if env is None else env
        token = (env.get(ENV_TOKEN) or "").strip()
        if not token:
            raise ProviderNotConfigured("DOCUMENSO_API_TOKEN assente")
        self.api_url = _checked_url((env.get(ENV_URL) or DEFAULT_API_URL).strip())
        self._token = token
        self._secret = (env.get(ENV_SECRET) or "").strip()
        self.environment = (env.get(ENV_ENVIRONMENT) or "sandbox").strip().lower()
        if self.environment not in ("sandbox", "production"):
            raise ProviderNotConfigured("DOCUMENSO_ENVIRONMENT deve essere sandbox o production")
        try:
            self.timeout = float(env.get(ENV_TIMEOUT) or 20)
        except ValueError as exc:
            raise ProviderNotConfigured("DOCUMENSO_TIMEOUT_SECONDS non valido") from exc
        self.verify_original = (env.get(ENV_VERIFY) or "1").strip() not in ("0", "false", "no")
        if http is None:
            import requests
            http = requests.Session()
        self._http = http

    def __repr__(self) -> str:                                     # mai il token, mai il segreto
        return f"<DocumensoProvider {self.api_url} environment={self.environment}>"

    # ---- trasporto ------------------------------------------------------------------------------------------------

    def _call(self, method: str, path: str, *, expect=(200,), **kwargs):
        headers = {"Authorization": self._token, "Accept": "application/json"}
        try:
            response = self._http.request(method, self.api_url + path, headers=headers, timeout=self.timeout,
                                          allow_redirects=False, **kwargs)
        except Exception as exc:                                    # rete, DNS, timeout
            raise ProviderError("PROVIDER_UNREACHABLE", f"Documenso non raggiungibile ({type(exc).__name__}).") from exc
        if response.status_code not in expect:
            raise ProviderError("PROVIDER_REJECTED", f"Documenso ha risposto {response.status_code} a {method} {path.split('?')[0]}.",
                                status=response.status_code)
        return response

    def _json(self, response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("PROVIDER_BAD_RESPONSE", "Risposta di Documenso non valida.") from exc
        if not isinstance(data, dict):
            raise ProviderError("PROVIDER_BAD_RESPONSE", "Risposta di Documenso non valida.")
        return data

    def _signing_url(self, token: str) -> str:
        """Ripiego: il collegamento personale e' `<sito>/sign/<token>` (il sito e' l'origine dell'URL dell'API)."""
        parts = urlparse(self.api_url)
        return f"{parts.scheme}://{parts.netloc}/sign/{token}"

    # ---- creazione ------------------------------------------------------------------------------------------------

    def _find_by_external_id(self, key: str) -> dict | None:
        data = self._json(self._call("GET", "/envelope", params={"query": key[:8], "perPage": 50}))
        for item in data.get("data") or []:
            if isinstance(item, dict) and item.get("externalId") == key and not item.get("deletedAt"):
                return item
        return None

    def _payload(self, *, title, signers, layout, key) -> dict:
        recipients = []
        for spec in signers:
            box = (layout or {}).get("boxes", {}).get(spec.role)
            fields = []
            if box:
                fields.append({"type": "SIGNATURE", **field_geometry({**box, "page_width": layout["page_width"],
                                                                     "page_height": layout["page_height"]})})
            recipients.append({"email": spec.email, "name": spec.name, "role": "SIGNER", "signingOrder": spec.order,
                               "fields": fields})
        return {"title": title, "type": "DOCUMENT", "externalId": key, "recipients": recipients,
                "meta": {"subject": title, "timezone": "Europe/Rome", "dateFormat": "dd/MM/yyyy HH:mm", "language": "it",
                         "distributionMethod": DISTRIBUTION_METHOD, "signingOrder": "PARALLEL",
                         "typedSignatureEnabled": False, "uploadSignatureEnabled": False, "drawSignatureEnabled": True,
                         "emailSettings": dict(EMAIL_OFF)}}

    def create_request(self, *, document, document_sha256, signer_name, signer_contact, expires_at, idempotency_key,
                       signers=None, layout=None, title=None) -> ProviderRequest:
        if not signers or len(signers) < 1:
            raise ProviderError("NO_SIGNERS", "Servono i firmatari.")
        if len({s.email.lower() for s in signers}) != len(signers):
            raise ProviderError("DUPLICATE_SIGNER", "Cliente e agente devono avere indirizzi diversi.")
        title = (title or "Foglio visita")[:200]
        found = self._find_by_external_id(idempotency_key)
        if found is None:
            payload = self._payload(title=title, signers=signers, layout=layout, key=idempotency_key)
            response = self._call("POST", "/envelope/create", data={"payload": json.dumps(payload)},
                                  files={"files": ("foglio-visita.pdf", document, "application/pdf")})
            envelope_id = self._json(response).get("id")
            status = "DRAFT"
        else:
            envelope_id, status = found.get("id"), str(found.get("status") or "")
        if not isinstance(envelope_id, str) or not envelope_id:
            raise ProviderError("PROVIDER_BAD_RESPONSE", "Documenso non ha restituito l'identificativo della busta.")
        if status not in ("DRAFT", "PENDING"):
            raise ProviderError("PROVIDER_STATE", "Esiste gia' una busta chiusa con questa chiave.")
        if status == "DRAFT":
            try:
                out = self._json(self._call("POST", "/envelope/distribute", json={
                    "envelopeId": envelope_id, "meta": {"distributionMethod": DISTRIBUTION_METHOD, "language": "it",
                                                        "timezone": "Europe/Rome", "emailSettings": dict(EMAIL_OFF)}}))
            except ProviderError:
                self._discard(envelope_id)                          # una bozza che non parte non resta orfana
                raise
            recipients = out.get("recipients") or []
        else:                                                       # gia' distribuita da un tentativo precedente
            recipients = (self._json(self._call("GET", f"/envelope/{envelope_id}")).get("recipients") or [])
        return ProviderRequest(reference=envelope_id, signers=self._match_signers(signers, recipients))

    def _discard(self, envelope_id: str) -> None:
        try:
            self.cancel_request(envelope_id)
        except ProviderError:
            pass

    def _match_signers(self, specs, recipients) -> tuple[ProviderSigner, ...]:
        by_email = {str(r.get("email", "")).lower(): r for r in recipients if isinstance(r, dict)}
        out = []
        for spec in specs:
            r = by_email.get(spec.email.lower())
            if r is None:
                raise ProviderError("PROVIDER_BAD_RESPONSE", "Documenso non ha restituito tutti i firmatari.")
            url = r.get("signingUrl") or (self._signing_url(r["token"]) if r.get("token") else None)
            out.append(ProviderSigner(role=spec.role, recipient_id=str(r.get("id")) if r.get("id") is not None else None,
                                      signing_url=url))
        return tuple(out)

    # ---- stato, documento, prove, annullamento --------------------------------------------------------------------

    def cancel_request(self, reference: str) -> None:
        response = self._call("POST", "/envelope/delete", json={"envelopeId": reference}, expect=(200, 404))
        del response                                                # 404: gia' sparita, l'effetto voluto c'e'

    def fetch_status(self, reference: str) -> ProviderStatus:
        data = self._json(self._call("GET", f"/envelope/{reference}"))
        if data.get("deletedAt"):
            return ProviderStatus("cancelled")
        recipients = tuple(SignerStatus(recipient_id=str(r.get("id")) if r.get("id") is not None else None,
                                        email=r.get("email"), status=SIGNER_MAP.get(str(r.get("signingStatus")), "pending"),
                                        signed_at=_parse_time(r.get("signedAt")))
                           for r in data.get("recipients") or [] if isinstance(r, dict) and r.get("role") == "SIGNER")
        status = STATUS_MAP.get(str(data.get("status")), "pending")
        if status == "signed":
            when = _parse_time(data.get("completedAt")) or max((r.signed_at for r in recipients if r.signed_at), default=None)
            return ProviderStatus("signed", signed_at=when, signature_level="ses", signers=recipients)
        return ProviderStatus(status, signers=recipients)

    def _item_id(self, reference: str) -> str:
        items = self._json(self._call("GET", f"/envelope/{reference}")).get("envelopeItems") or []
        if len(items) != 1 or not isinstance(items[0], dict) or not items[0].get("id"):
            raise ProviderError("PROVIDER_BAD_RESPONSE", "La busta non contiene un solo documento.")
        return str(items[0]["id"])

    def _download(self, item_id: str, version: str) -> bytes:
        response = self._call("GET", f"/envelope/item/{item_id}/download", params={"version": version})
        content_type = (response.headers.get("content-type") or "").lower()
        data = response.content
        if "json" in content_type:                                  # la specifica dichiara JSON: puo' essere un URL
            body = self._json(response)
            url = next((body[k] for k in ("downloadUrl", "url", "href") if isinstance(body.get(k), str)), None)
            if not url or not _public_https(url):
                raise ProviderError("PROVIDER_BAD_RESPONSE", "Formato del download non riconosciuto.")
            try:                                                    # URL firmato: niente Authorization
                data = self._http.request("GET", url, timeout=self.timeout, allow_redirects=False).content
            except Exception as exc:
                raise ProviderError("PROVIDER_UNREACHABLE", "Download del documento non riuscito.") from exc
        if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
            raise ProviderError("PROVIDER_BAD_RESPONSE", "Il fornitore non ha restituito un PDF valido.")
        return data

    def fetch_document(self, reference: str, *, document_sha256: str, signer_name: str) -> ProviderDocument:
        item = self._item_id(reference)
        if self.verify_original:
            original = self._download(item, "original")
            if hashlib.sha256(original).hexdigest() != document_sha256:
                raise ProviderError("ORIGINAL_MISMATCH", "Il documento firmato non deriva dal foglio visita caricato.")
        return ProviderDocument(content=self._download(item, "signed"), media_type="application/pdf")

    def fetch_audit_trail(self, reference: str) -> ProviderDocument | None:
        events, page = [], 1
        while page <= 20:
            data = self._json(self._call("GET", f"/envelope/{reference}/audit-log", params={"page": page, "perPage": 100}))
            events.extend(data.get("data") or [])
            if page >= int(data.get("totalPages") or 1):
                break
            page += 1
        body = json.dumps({"provider": NAME, "envelope_id": reference, "events": events}, sort_keys=True,
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return ProviderDocument(content=body, media_type="application/json")

    # ---- webhook --------------------------------------------------------------------------------------------------

    def verify_event(self, headers: dict, raw_body: bytes) -> ProviderEvent:
        if len(self._secret) < MIN_SECRET_LENGTH:
            raise InvalidEventSignature("not_configured")
        received = next((v for k, v in headers.items() if str(k).lower() == SECRET_HEADER), None)
        if not received or not hmac.compare_digest(received.strip().encode("utf-8"), self._secret.encode("utf-8")):
            raise InvalidEventSignature("mismatch")
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise InvalidEventSignature("malformed") from exc
        if not isinstance(body, dict) or not isinstance(body.get("event"), str):
            raise InvalidEventSignature("malformed")
        payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
        envelope = next((payload[k] for k in ("envelopeId", "id") if isinstance(payload.get(k), str)
                         and re.fullmatch(r"envelope_[A-Za-z0-9]+", payload[k])), None)
        external = payload.get("externalId") if isinstance(payload.get("externalId"), str) else None
        # Documenso non manda un id evento: lo stesso corpo, riconsegnato, e' lo stesso evento
        return ProviderEvent(event_id=hashlib.sha256(raw_body).hexdigest(), event_type=body["event"][:80],
                             reference=envelope, external_id=external)
