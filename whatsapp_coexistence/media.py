"""WHATSAPP COEXISTENCE - aprire un allegato ricevuto (o scritto dall'app), a richiesta.

Vive qui, con il client Graph, e non in `inbound_messages`: la ricezione non
apre mai connessioni verso Meta (BLOCCO-4A). La visibilita' la decide pero' la
ricezione (`inbound_messages.service.visible_message_row`).

L'allegato NON viene scaricato alla ricezione ne' conservato: quando chi vede
la conversazione lo apre, il server chiede a Meta l'indirizzo temporaneo
(`GET /{media_id}`, token della casella) e lo inoltra al browser in UNA
risposta, senza scriverlo su disco o nel database.

Difese:
  * visibilita': la stessa del messaggio (agenzia, agente sui suoi contatti,
    caselle riservate) - prima di qualsiasi chiamata a Meta;
  * dimensione massima (`WHATSAPP_MEDIA_MAX_MB`, predefinito 25 MB), controllata
    sia sul valore dichiarato da Meta sia durante la lettura;
  * il tipo servito e' uno di una lista chiusa (immagini, audio, video, PDF);
    tutto il resto esce come `application/octet-stream` da salvare, mai come
    pagina (niente HTML o SVG in linea), con `nosniff` e senza cache;
  * l'indirizzo temporaneo di Meta non esce mai dal server.
L'esito e' uno di: allegato, 404 (messaggio non visibile o senza allegato),
410 (Meta non ha piu' l'allegato), 413 (troppo grande), 503 (invio/lettura
verso Meta non configurati), 502 (Meta non risponde).
"""
from __future__ import annotations

import logging
import os
import re

import requests

from message_replies import transport

log = logging.getLogger(__name__)

MAX_MB_ENV = "WHATSAPP_MEDIA_MAX_MB"
DEFAULT_MAX_MB = 25
TIMEOUT = (3.05, 20)

INLINE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "audio/ogg", "audio/mpeg", "audio/mp4", "audio/aac", "audio/amr",
    "video/mp4", "video/3gpp",
}
DOWNLOAD_TYPES = {"application/pdf"}


class MediaError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def max_bytes() -> int:
    try:
        mb = int((os.getenv(MAX_MB_ENV) or "").strip() or DEFAULT_MAX_MB)
    except ValueError:
        mb = DEFAULT_MAX_MB
    return max(1, min(mb, 100)) * 1024 * 1024


def _filename(meta: dict, mime: str) -> str:
    name = re.sub(r"[^0-9A-Za-z._ -]", "_", str(meta.get("filename") or ""))[:80].strip(" .")
    if name:
        return name
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "audio/ogg": "ogg",
           "audio/mpeg": "mp3", "audio/mp4": "m4a", "video/mp4": "mp4", "application/pdf": "pdf"}.get(mime, "bin")
    return f"allegato-whatsapp.{ext}"


def fetch(row: dict, *, session=None) -> tuple[bytes, str, str, bool]:
    """(contenuto, tipo servito, nome del file, in linea) per il messaggio gia'
    verificato come visibile. Solleva MediaError."""
    meta = row.get("metadata") or {}
    media_id = meta.get("media_id")
    if not media_id:
        raise MediaError(404, "MEDIA_NOT_FOUND", "Questo messaggio non ha un allegato apribile.")
    version = transport.graph_version()
    token = transport.token_for(row.get("phone_number_id"))
    if version is None or not token:
        raise MediaError(503, "MEDIA_UNAVAILABLE",
                         "Gli allegati non si possono aprire su questo ambiente: manca la configurazione di Meta.")
    http = session or requests
    headers = {"Authorization": f"Bearer {token}"}
    limit = max_bytes()
    try:
        info = http.get(f"https://graph.facebook.com/{version}/{media_id}", headers=headers, timeout=TIMEOUT)
    except requests.exceptions.RequestException:
        raise MediaError(502, "MEDIA_PROVIDER_UNAVAILABLE", "Meta non risponde: riprova fra poco.") from None
    if info.status_code in (400, 404, 410):
        raise MediaError(410, "MEDIA_GONE", "Meta non conserva più questo allegato.")
    if info.status_code != 200:
        raise MediaError(502, "MEDIA_PROVIDER_UNAVAILABLE", "Meta non ha restituito l'allegato: riprova fra poco.")
    try:
        body = info.json()
    except ValueError:
        body = {}
    url = body.get("url") if isinstance(body, dict) else None
    if not url or not str(url).startswith("https://"):
        raise MediaError(502, "MEDIA_PROVIDER_UNAVAILABLE", "Meta non ha restituito l'allegato: riprova fra poco.")
    declared = body.get("file_size")
    if isinstance(declared, int) and declared > limit:
        raise MediaError(413, "MEDIA_TOO_LARGE", "L'allegato è troppo grande per essere aperto dal CRM.")
    try:
        data = http.get(url, headers=headers, timeout=TIMEOUT, stream=True)
    except requests.exceptions.RequestException:
        raise MediaError(502, "MEDIA_PROVIDER_UNAVAILABLE", "Meta non risponde: riprova fra poco.") from None
    if data.status_code != 200:
        raise MediaError(410 if data.status_code in (403, 404, 410) else 502, "MEDIA_GONE",
                         "Meta non conserva più questo allegato.")
    chunks, size = [], 0
    for chunk in data.iter_content(chunk_size=64 * 1024):
        size += len(chunk)
        if size > limit:
            data.close()
            raise MediaError(413, "MEDIA_TOO_LARGE", "L'allegato è troppo grande per essere aperto dal CRM.")
        chunks.append(chunk)
    declared_type = str(body.get("mime_type") or meta.get("mime_type") or "").split(";")[0].strip().lower()
    if declared_type in INLINE_TYPES:
        served, inline = declared_type, True
    elif declared_type in DOWNLOAD_TYPES:
        served, inline = declared_type, False
    else:
        served, inline = "application/octet-stream", False
    log.info("whatsapp_media_opened message=%s bytes=%s type=%s", row.get("id"), size, served)
    return b"".join(chunks), served, _filename(meta, served), inline
