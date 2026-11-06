"""IMPORTAZIONE RUBRICA - lettura di un file vCard (.vcf) esportato da iPhone
(iCloud.com → Esporta vCard), Android o Google Contatti. Nessuna dipendenza
esterna: si leggono solo nomi, organizzazione, numeri ed email; foto, note,
indirizzi e tutto il resto si ignorano e non si conservano.

Formati coperti: vCard 2.1 (righe QUOTED-PRINTABLE, parametri senza nome),
3.0 e 4.0 (`TEL;VALUE=uri:tel:+39...`), righe ripiegate (RFC 6350), gruppi
`item1.TEL`, caratteri scappati (`\\,` `\;` `\\n`), piu' schede nello stesso
file, schede annidate (AGENT) ignorate.
"""
from __future__ import annotations

import quopri
import re
from dataclasses import dataclass, field

_LABELS = {"cell": "cellulare", "mobile": "cellulare", "iphone": "cellulare", "home": "casa", "work": "lavoro",
           "fax": "fax", "main": "principale", "other": "altro", "pager": "cercapersone"}


@dataclass
class Entry:
    fn: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    org: str | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[dict] = field(default_factory=list)      # {"raw": ..., "label": ...}

    def as_dict(self) -> dict:
        return {"fn": self.fn, "first_name": self.first_name, "last_name": self.last_name, "org": self.org,
                "emails": list(self.emails), "phones": [dict(p) for p in self.phones]}


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "utf-16"):
        try:
            text = data.decode(enc)
            if enc == "utf-16" and "BEGIN:VCARD" not in text.upper():
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _unfold(text: str) -> list[str]:
    """Righe ripiegate (spazio o tab iniziale) e, per il 2.1, le righe
    QUOTED-PRINTABLE che finiscono con `=` (continuazione morbida)."""
    out: list[str] = []
    pending_qp = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if out and (raw.startswith(" ") or raw.startswith("\t")):
            out[-1] += raw[1:]
            continue
        if out and pending_qp:
            out[-1] = out[-1][:-1] + raw           # togli `=` finale e continua
            pending_qp = out[-1].endswith("=") and "QUOTED-PRINTABLE" in out[-1].split(":", 1)[0].upper()
            continue
        out.append(raw)
        head = raw.split(":", 1)[0].upper()
        pending_qp = raw.endswith("=") and "QUOTED-PRINTABLE" in head
    return [line for line in out if line.strip()]


def _split_line(line: str) -> tuple[str, dict, str] | None:
    """`gruppo.NOME;PARAM=valore;ALTRO:valore` -> (NOME, {param: [valori]}, valore)."""
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    parts = head.split(";")
    name = parts[0].split(".")[-1].strip().upper()
    params: dict[str, list[str]] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params.setdefault(k.strip().upper(), []).extend(x.strip().strip('"').lower() for x in v.split(","))
        elif p.strip():
            params.setdefault("TYPE", []).append(p.strip().lower())   # vCard 2.1: `TEL;CELL;VOICE:`
    return name, params, value


def _value(value: str, params: dict) -> str:
    if any(e.startswith("quoted-printable") for e in params.get("ENCODING", [])):
        charset = (params.get("CHARSET") or ["utf-8"])[0]
        try:
            value = quopri.decodestring(value.encode("latin-1", "replace")).decode(charset, "replace")
        except (LookupError, ValueError):
            value = quopri.decodestring(value.encode("latin-1", "replace")).decode("utf-8", "replace")
    return value


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\;", ";")
            .replace("\\\\", "\\")).strip()


def _components(value: str) -> list[str]:
    """Divide su `;` non scappato (per N e ORG)."""
    return [_unescape(x) for x in re.split(r"(?<!\\);", value)]


def _label(params: dict) -> str | None:
    for t in params.get("TYPE", []):
        if t in _LABELS:
            return _LABELS[t]
    return None


def _clean_phone(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("tel:"):
        value = value[4:]
    return value.split(";")[0].strip()                 # `tel:+39...;ext=12` -> senza estensione


def parse(data: bytes) -> list[dict]:
    """Le schede del file, nell'ordine. Una scheda senza nome e senza numero
    viene comunque restituita (la decide il servizio)."""
    entries: list[Entry] = []
    current: Entry | None = None
    depth = 0
    for line in _unfold(_decode(data)):
        parsed = _split_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN" and value.strip().upper() == "VCARD":
            depth += 1
            if depth == 1:
                current = Entry()
            continue
        if name == "END" and value.strip().upper() == "VCARD":
            if depth == 1 and current is not None:
                entries.append(current)
                current = None
            depth = max(0, depth - 1)
            continue
        if name == "AGENT" and value.strip().upper().startswith("BEGIN:VCARD"):
            depth += 1                                   # vCard 2.1: scheda annidata in linea, ignorata
            continue
        if current is None or depth != 1:
            continue
        value = _value(value, params)
        if name == "FN":
            current.fn = current.fn or _unescape(value) or None
        elif name == "N":
            parts = _components(value) + [""] * 5
            current.last_name = current.last_name or parts[0] or None
            current.first_name = current.first_name or " ".join(x for x in (parts[1], parts[2]) if x) or None
        elif name == "ORG":
            current.org = current.org or (_components(value) + [""])[0] or None
        elif name == "EMAIL":
            email = _unescape(value).strip().lower()
            if email and "@" in email and email not in current.emails:
                current.emails.append(email)
        elif name == "TEL":
            raw = _clean_phone(_unescape(value))
            if raw and raw not in [p["raw"] for p in current.phones]:
                current.phones.append({"raw": raw, "label": _label(params)})
    return [e.as_dict() for e in entries]
