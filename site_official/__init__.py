"""C3.5 - le versioni UFFICIALI dei testi del sito, conservate lato server.

Il browser manda con la richiesta il testo della casella marketing e
l'impronta SHA-256 di `privacy.html`. Da soli non provano niente: li ha scritti
il browser. Valgono solo se coincidono con una versione ufficiale conservata
qui, in `site_official/versions/versions.json`:

    * `marketing_checkbox` - il testo della casella, ricavato da una copia
      della pagina del sito conservata qui, di cui e' registrata l'impronta
      (`source`, `source_sha256`);
    * `privacy` - i file di `privacy.html` pubblicati, ognuno con la sua
      impronta.

Ogni voce e' verificata a ogni avvio: un file mancante o con un'impronta
diversa, o un testo che non e' quello della copia citata, fa sparire la voce.
Solo le voci con `accepted: true` valgono per le richieste; le altre restano
come documentazione storica (per esempio la privacy precedente, che citava
anche il telefono).

Pacchetto autonomo (nessuna dipendenza dal resto del CRM): gli stessi file
servono al backend del sito (`main`, `/api/salva_stima`) e al CRM.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DIRECTORY = Path(__file__).resolve().parent / "versions"
VERSIONS = DIRECTORY / "versions.json"
_LABEL = re.compile(r'<label for="marketing">(.*?)</label>', re.S)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def normalize_text(testo: str | None) -> str:
    """Spazi compressi, maiuscole ignorate (come `consent.site_evidence`)."""
    return re.sub(r"\s+", " ", (testo or "")).strip().casefold()


def _impronta(percorso: Path) -> str | None:
    try:
        return hashlib.sha256(percorso.read_bytes()).hexdigest()
    except OSError:
        return None


def _casella(voce: dict[str, Any]) -> dict[str, Any] | None:
    sorgente = DIRECTORY / str(voce.get("source") or "-")
    if not voce.get("source") or _impronta(sorgente) != voce.get("source_sha256"):
        return None
    m = _LABEL.search(sorgente.read_text(encoding="utf-8", errors="replace"))
    testo = str(voce.get("text") or "")
    if not m or normalize_text(re.sub(r"<[^>]+>", " ", m.group(1))) != normalize_text(testo) or not testo:
        return None
    return {"version": str(voce["version"]), "text": testo, "channels": list(voce.get("channels") or []),
            "accepted": voce.get("accepted") is True}


def _privacy(voce: dict[str, Any]) -> dict[str, Any] | None:
    impronta = str(voce.get("sha256") or "")
    if not _SHA256.match(impronta) or _impronta(DIRECTORY / str(voce.get("file") or "-")) != impronta:
        return None
    return {"version": str(voce["version"]), "sha256": impronta, "file": str(voce["file"]),
            "accepted": voce.get("accepted") is True}


@lru_cache(maxsize=1)
def official_versions() -> dict[str, list[dict[str, Any]]]:
    """Le versioni verificate (anche quelle solo storiche)."""
    try:
        dati = json.loads(VERSIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"marketing_checkbox": [], "privacy": []}
    caselle = [c for c in (_casella(v) for v in dati.get("marketing_checkbox") or [] if isinstance(v, dict)) if c]
    privacy = [p for p in (_privacy(v) for v in dati.get("privacy") or [] if isinstance(v, dict)) if p]
    return {"marketing_checkbox": caselle, "privacy": privacy}


def accepted_checkbox(testo: str | None) -> dict[str, Any] | None:
    """La versione ufficiale e accettata della casella con QUESTO testo, o None."""
    if not testo:
        return None
    n = normalize_text(testo)
    return next((c for c in official_versions()["marketing_checkbox"]
                 if c["accepted"] and normalize_text(c["text"]) == n), None)


def accepted_privacy(impronta: str | None) -> dict[str, Any] | None:
    """La versione ufficiale e accettata di privacy.html con QUESTA impronta, o None."""
    if not impronta:
        return None
    return next((p for p in official_versions()["privacy"] if p["accepted"] and p["sha256"] == impronta), None)


def accepted_texts() -> list[str]:
    return [normalize_text(c["text"]) for c in official_versions()["marketing_checkbox"] if c["accepted"]]


def accepted_privacy_hashes() -> list[str]:
    return [p["sha256"] for p in official_versions()["privacy"] if p["accepted"]]


__all__ = ["normalize_text", "official_versions", "accepted_checkbox", "accepted_privacy", "accepted_texts",
           "accepted_privacy_hashes"]
