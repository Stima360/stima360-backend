"""C3.4 - la PROVA STORICA del testo della casella marketing del sito.

Il repository conserva una copia datata e firmata della pagina del sito che
contiene la casella: `site-preview/baseline/dati_personali.html`, con la sua
impronta SHA-256 e la data della verifica in `site-preview/html-provenance.json`
(copia del 7 ottobre 2026, byte per byte uguale al pacchetto originale
consegnato dal titolare, mai modificata da allora nello storico Git).

COSA PROVA, E PER QUALI DATE. Solo prove documentali effettive, e ognuna
solo per le date che documenta davvero:

    * `site_copy`  - la copia firmata qui sopra: prova il SUO giorno;
    * `backup`     - un backup datato della pagina (dell'hosting o altro
                     archivio affidabile), con la stessa impronta: prova il
                     giorno del backup;
    * `publication_history` - uno storico affidabile delle pubblicazioni
                     (log dei caricamenti dell'hosting, storico versioni del
                     file) che dichiara la pagina con QUESTA impronta in linea
                     senza modifiche da `from` a `to`: prova l'intervallo.

NON sono prove, e vengono ignorate:

    * due copie o backup identici in date diverse: provano i due giorni, non
      l'intervallo fra loro (la pagina potrebbe essere cambiata e tornata);
    * un'intestazione `Last-Modified` o una data del filesystem: dichiarate
      dal server, modificabili, non dicono cosa c'era nel file;
    * l'ipotesi che il sito "non sia cambiato".

Le prove successive alla copia si registrano nella voce della pagina in
`html-provenance.json`, come `evidence: [...]`, ognuna con il riferimento al
documento da cui viene (`reference`), per esempio:

    {"kind": "backup", "date": "2026-10-22", "sha256": "...",
     "reference": "backup giornaliero hosting 22/10/2026 03:00"}
    {"kind": "publication_history", "from": "2026-10-07", "to": "2026-10-23",
     "sha256": "...", "reference": "log FTP dell'hosting, nessun upload del file"}

Una voce con impronta diversa non prova il testo della copia: il testo era
un altro, e per quello serve un'altra informativa.

Un'informativa marketing che riporta ESATTAMENTE quel testo e che il Super
Admin dichiara valida dalla copia in poi puo' cosi' essere applicata a una
spunta precedente alla sua registrazione nel CRM, ma SOLO se la data della
spunta cade in un giorno o intervallo provato. La riga del CRM non e' una
prova.

La copia va verificata ogni volta: se l'impronta non corrisponde piu' a quella
registrata, non c'e' prova e questa via si chiude da sola.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "site-preview" / "html-provenance.json"
PAGE_URL_SUFFIX = "/dati_personali.html"
_LABEL = re.compile(r'<label for="marketing">(.*?)</label>', re.S)

#: Le sole prove accettate. Qualunque altro `kind` e' ignorato.
KIND_SITE_COPY = "site_copy"
KIND_BACKUP = "backup"
KIND_PUBLICATION_HISTORY = "publication_history"
KIND_LABELS = {
    KIND_SITE_COPY: "copia firmata del sito",
    KIND_BACKUP: "backup datato della pagina",
    KIND_PUBLICATION_HISTORY: "storico delle pubblicazioni",
}


def normalize_text(testo: str | None) -> str:
    """Spazi compressi, maiuscole ignorate: la stessa frase, non lo stesso HTML."""
    return re.sub(r"\s+", " ", (testo or "")).strip().casefold()


def _giorno(valore: Any) -> datetime | None:
    try:
        return datetime.strptime(str(valore), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fine_giorno(giorno: datetime) -> datetime:
    return giorno + timedelta(days=1) - timedelta(microseconds=1)


def _finestra(voce: dict[str, Any], impronta: str) -> dict[str, Any] | None:
    """La finestra provata da una voce di prova, o None se non e' una prova
    accettata (tipo sconosciuto, impronta diversa, date mancanti, nessun
    riferimento al documento)."""
    tipo = voce.get("kind")
    if voce.get("sha256") != impronta or not str(voce.get("reference") or "").strip():
        return None
    if tipo == KIND_BACKUP:
        g = _giorno(voce.get("date"))
        if g is None:
            return None
        return {"kind": tipo, "from": g, "to": _fine_giorno(g), "reference": str(voce["reference"])}
    if tipo == KIND_PUBLICATION_HISTORY:
        da, a = _giorno(voce.get("from")), _giorno(voce.get("to"))
        if da is None or a is None or a < da:
            return None
        return {"kind": tipo, "from": da, "to": _fine_giorno(a), "reference": str(voce["reference"])}
    return None


@lru_cache(maxsize=1)
def site_checkbox_evidence() -> dict[str, Any] | None:
    """Il testo della casella marketing del sito con le sue prove, o None se
    la copia manca o non corrisponde all'impronta registrata."""
    try:
        prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pagina = next((p for p in prov.get("pages", []) if str(p.get("url", "")).endswith(PAGE_URL_SUFFIX)), None)
    if not pagina:
        return None
    percorso = PROVENANCE.parent / str(pagina.get("baseline_path", ""))
    try:
        grezzo = percorso.read_bytes()
    except OSError:
        return None
    impronta = hashlib.sha256(grezzo).hexdigest()
    if impronta != pagina.get("sha256"):
        return None
    m = _LABEL.search(grezzo.decode("utf-8", errors="replace"))
    if not m:
        return None
    testo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
    copia = _giorno(prov.get("verified_date"))
    if copia is None:
        return None
    finestre = [{"kind": KIND_SITE_COPY, "from": copia, "to": _fine_giorno(copia),
                 "reference": f"site-preview/{pagina.get('baseline_path')} (GET del {copia:%Y-%m-%d})"}]
    for voce in pagina.get("evidence") or []:
        f = _finestra(voce if isinstance(voce, dict) else {}, impronta)
        if f is not None:
            finestre.append(f)
    finestre.sort(key=lambda f: (f["from"], f["to"]))
    return {
        "text": testo,
        "captured_on": copia.strftime("%Y-%m-%d"),
        "windows": finestre,
        "proven_since": copia,
        "sha256": impronta,
        "url": str(pagina.get("url")),
        "source": f"site-preview/{pagina.get('baseline_path')}",
    }


def proven_window(evidence: dict[str, Any] | None, when: datetime) -> dict[str, Any] | None:
    """La prova documentale che copre `when`, o None. Mai un intervallo fra
    due prove: solo le date che ciascuna documenta."""
    if not evidence:
        return None
    for f in evidence.get("windows") or []:
        if f["from"] <= when <= f["to"]:
            return f
    return None


def describe_windows(evidence: dict[str, Any] | None) -> str:
    """Le date provate, per dire alla scheda cosa c'e' e cosa manca."""
    parti = []
    for f in (evidence or {}).get("windows") or []:
        da, a = f["from"].strftime("%d/%m/%Y"), f["to"].strftime("%d/%m/%Y")
        parti.append(f"{KIND_LABELS[f['kind']]} {da}" if da == a else f"{KIND_LABELS[f['kind']]} {da}-{a}")
    return ", ".join(parti) or "nessuna"


__all__ = ["site_checkbox_evidence", "proven_window", "describe_windows", "normalize_text",
           "KIND_SITE_COPY", "KIND_BACKUP", "KIND_PUBLICATION_HISTORY", "KIND_LABELS"]
