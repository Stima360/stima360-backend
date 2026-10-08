"""Il PDF ORIGINALE della stima, dall'archivio del sito, in sola lettura.

Il backend PROD del sito carica ogni PDF su un repository GitHub con il nome
`stima_<id>.pdf` (pdf_report._upload_pdf_to_github su `main`). Qui lo si
LEGGE e basta: nessun commit, nessuna modifica, nessuna cancellazione.

IL NOME NON BASTA. Un file `stima_123.pdf` scritto da un database precedente
(id riusati) o sovrascritto dopo non e' il PDF di questa stima. Si accetta
solo la versione del file il cui commit cade entro una finestra intorno alla
data della stima, e si scarica ESATTAMENTE quella versione (per sha del
commit). Fuori finestra: `unverified`, e il PDF non viene associato.

Esiti, mai un documento inventato:
    ready        bytes del PDF originale + provenienza
    missing      il file non esiste nell'archivio
    unverified   esiste, ma nessuna versione cade nella finestra della stima
    invalid      cio' che e' stato scaricato non e' un PDF integro
    unavailable  archivio non configurato o non raggiungibile (si ritenta)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

API = "https://api.github.com"
MAX_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Esito:
    status: str
    pdf: bytes | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


def valid_pdf(pdf: bytes | None) -> bool:
    return (isinstance(pdf, bytes) and 0 < len(pdf) <= MAX_BYTES
            and pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF"))


def _iso(valore: str) -> datetime:
    return datetime.fromisoformat(valore.replace("Z", "+00:00"))


class GitHubArchive:
    def __init__(self, repo: str | None, branch: str = "main", token: str | None = None, *,
                 window_hours: int = 48, http_get: Callable | None = None):
        self.repo, self.branch, self.token = repo, branch, token
        self.window = timedelta(hours=window_hours)
        if http_get is None:
            import requests

            def http_get(url, *, headers, params=None):
                return requests.get(url, headers=headers, params=params, timeout=(5, 30))
        self._get = http_get

    @property
    def configured(self) -> bool:
        return bool(self.repo)

    def _headers(self, accept: str) -> dict[str, str]:
        h = {"Accept": accept, "User-Agent": "stima360-crm-site-import",
             "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def fetch(self, stima_id: int, created_at: datetime | None) -> Esito:
        """`created_at` e' la data della stima nel database del sito, in UTC."""
        if not self.configured:
            return Esito("unavailable", reason="archive_not_configured")
        path = f"stima_{int(stima_id)}.pdf"
        try:
            r = self._get(f"{API}/repos/{self.repo}/commits",
                          headers=self._headers("application/vnd.github+json"),
                          params={"path": path, "sha": self.branch, "per_page": 100})
        except Exception as exc:  # noqa: BLE001 - rete: si ritenta al prossimo giro
            return Esito("unavailable", reason=f"archive_error:{type(exc).__name__}")
        if r.status_code in (404, 409):
            return Esito("missing", reason="archive_path_missing")
        if r.status_code != 200:
            return Esito("unavailable", reason=f"archive_http_{r.status_code}")
        commits = r.json() or []
        if not commits:
            return Esito("missing", reason="archive_path_missing")
        if created_at is None:
            return Esito("unverified", reason="stima_without_date")
        base = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        candidati = []
        for c in commits:
            try:
                quando = _iso(c["commit"]["committer"]["date"])
            except (KeyError, TypeError, ValueError):
                continue
            # Il PDF nasce DOPO la stima (stessa richiesta): -1h di tolleranza
            # sugli orologi, + la finestra configurata.
            if base - timedelta(hours=1) <= quando <= base + self.window:
                candidati.append((quando, c["sha"]))
        if not candidati:
            return Esito("unverified", reason="archive_commit_outside_window")
        quando, sha = min(candidati)  # la PRIMA versione dopo la stima
        try:
            f = self._get(f"{API}/repos/{self.repo}/contents/{path}",
                          headers=self._headers("application/vnd.github.raw"),
                          params={"ref": sha})
        except Exception as exc:  # noqa: BLE001
            return Esito("unavailable", reason=f"archive_error:{type(exc).__name__}")
        if f.status_code == 404:
            return Esito("missing", reason="archive_version_missing")
        if f.status_code != 200:
            return Esito("unavailable", reason=f"archive_http_{f.status_code}")
        pdf = f.content
        if not valid_pdf(pdf):
            return Esito("invalid", reason="archive_not_a_pdf")
        return Esito("ready", pdf=pdf, provenance={
            "origin": "site_archive", "repository": self.repo, "path": path,
            "commit": sha, "committed_at": quando.isoformat(),
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat()})
