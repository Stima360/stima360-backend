"""Lettura di testo e POSIZIONI dai PDF generati da reportlab (font standard o TrueType incorporati con mappa
ToUnicode), senza dipendenze nuove.

Serve a due cose, entrambe in sola lettura sul PDF gia' archiviato:
  * il collaudo (`scripts/collaudo_visite.py`) legge il testo dei PDF quando `pdftotext` non c'e';
  * `esign.layout` trova dove sono i riquadri firma del foglio visita, per dire al fornitore dove mettere i campi.

Non e' un lettore PDF generico: capisce i PDF di reportlab (`q`/`Q`/`cm` con sole traslazioni, `BT ... Tm ... Tj ... ET`).
Un PDF che non rispetta queste ipotesi produce un errore esplicito, mai una posizione inventata.
"""
from __future__ import annotations

import base64
import re
import zlib

_NUM = rb"-?\d+(?:\.\d+)?"
_STRING = rb"\((?:\\.|[^\\()])*\)"
_TOKEN = re.compile(rb"/([^\s/\[\]()<>]+)\s+" + _NUM + rb"\s+Tf|<([0-9A-Fa-f\s]*)>|(" + _STRING + rb")", re.S)
_TM = re.compile(rb"(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+Tm")
_OPS = re.compile(
    rb"(?P<cm>(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+cm)"
    rb"|(?P<q>(?<![\w/.])q(?![\w]))|(?P<Q>(?<![\w/.])Q(?![\w]))|(?P<bt>BT\b.*?\bET\b)", re.S)


class PdfLayoutError(Exception):
    """Il PDF non e' nella forma che questo lettore sa interpretare."""


def _literal(raw: bytes) -> bytes:
    body, out, i = raw[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i]
        if c == 0x5C and i + 1 < len(body):
            n = body[i + 1]
            if 0x30 <= n <= 0x37:
                j, digits = i + 1, b""
                while j < len(body) and len(digits) < 3 and 0x30 <= body[j] <= 0x37:
                    digits += bytes([body[j]])
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
                continue
            out.extend({0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f"}.get(n, bytes([n])))
            i += 2
            continue
        out.append(c)
        i += 1
    return bytes(out)


def objects(pdf: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """numero oggetto -> (dizionario, dati decodificati del flusso o None)."""
    found: dict[int, tuple[bytes, bytes | None]] = {}
    for m in re.finditer(rb"(\d+) 0 obj(.*?)endobj", pdf, re.S):
        body, data = m.group(2), None
        k = re.search(rb"stream\r?\n(.*?)endstream", body, re.S)
        if k:
            raw = k.group(1).strip()
            try:
                if raw.endswith(b"~>"):
                    raw = base64.a85decode(raw[:-2], adobe=False)
                data = zlib.decompress(raw)
            except Exception:
                data = None
        found[int(m.group(1))] = (body[:k.start()] if k else body, data)
    return found


def _cmap(text: bytes) -> tuple[int, dict[int, str]]:
    width = 1
    cs = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", text)
    if cs:
        width = max(1, len(cs.group(1)) // 2)

    def u(h: bytes) -> str:
        return bytes.fromhex(h.decode()).decode("utf-16-be", errors="replace")

    mapping: dict[int, str] = {}
    for block in re.finditer(rb"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)):
            mapping[int(src, 16)] = u(dst)
    for block in re.finditer(rb"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block.group(1)):
            base = u(dst)
            for n, code in enumerate(range(int(lo, 16), int(hi, 16) + 1)):
                mapping[code] = base[:-1] + chr(ord(base[-1]) + n) if base else ""
    return width, mapping


def _fonts(objs) -> tuple[dict[int, tuple[int, dict[int, str]]], dict[bytes, int]]:
    cmaps: dict[int, tuple[int, dict[int, str]]] = {}
    for num, (body, _data) in objs.items():
        t = re.search(rb"/ToUnicode\s+(\d+) 0 R", body)
        if t and objs.get(int(t.group(1)), (b"", None))[1] is not None:
            cmaps[num] = _cmap(objs[int(t.group(1))][1])
    names: dict[bytes, int] = {}
    for body, _ in objs.values():
        for region in re.finditer(rb"/Font\s*(?:<<(.*?)>>|(\d+) 0 R)", body, re.S):
            text = region.group(1)
            if text is None and int(region.group(2)) in objs:
                text = objs[int(region.group(2))][0]
            for name, num in re.findall(rb"/([^\s/\[\]()<>]+)\s+(\d+) 0 R", text or b""):
                names[name] = int(num)
    return cmaps, names


def _pages(objs) -> list[dict]:
    """Le pagine nell'ordine del documento: contenuti (numeri di flusso) e MediaBox."""
    def media(body, parent_chain=0):
        m = re.search(rb"/MediaBox\s*\[\s*(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s+(" + _NUM + rb")\s*\]", body)
        if m:
            return tuple(float(x) for x in m.groups())
        p = re.search(rb"/Parent\s+(\d+) 0 R", body)
        if p and parent_chain < 5 and int(p.group(1)) in objs:
            return media(objs[int(p.group(1))][0], parent_chain + 1)
        return None

    def kids(num, depth=0):
        body = objs[num][0]
        if re.search(rb"/Type\s*/Pages\b", body) and depth < 6:
            k = re.search(rb"/Kids\s*\[(.*?)\]", body, re.S)
            for ref in re.findall(rb"(\d+) 0 R", k.group(1) if k else b""):
                yield from kids(int(ref), depth + 1)
        elif re.search(rb"/Type\s*/Page\b", body):
            yield num

    roots = [n for n, (b, _) in objs.items() if re.search(rb"/Type\s*/Pages\b", b) and not re.search(rb"/Parent\s", b)]
    if len(roots) != 1:
        raise PdfLayoutError("albero delle pagine non riconosciuto")
    pages = []
    for num in kids(roots[0]):
        body = objs[num][0]
        c = re.search(rb"/Contents\s*(?:\[(.*?)\]|(\d+) 0 R)", body, re.S)
        refs = re.findall(rb"(\d+) 0 R", c.group(1)) if c and c.group(1) else ([c.group(2)] if c else [])
        box = media(body)
        if box is None:
            raise PdfLayoutError("MediaBox mancante")
        pages.append({"contents": [int(r) for r in refs], "box": box})
    return pages


def text_runs(pdf: bytes) -> list[dict]:
    """Ogni stringa di testo con pagina (da 1), posizione assoluta della linea di base in punti PDF (origine in basso
    a sinistra), contesto (traslazione del contenitore) e dimensioni della pagina."""
    objs = objects(pdf)
    cmaps, names = _fonts(objs)
    runs = []
    for page_no, page in enumerate(_pages(objs), start=1):
        width, height = page["box"][2] - page["box"][0], page["box"][3] - page["box"][1]
        for stream in page["contents"]:
            data = objs.get(stream, (b"", None))[1]
            if data is None:
                continue
            stack, tx, ty = [], 0.0, 0.0
            for op in _OPS.finditer(data):
                if op.group("cm"):
                    a, b, c, d, e, f = (float(x) for x in re.findall(_NUM, op.group("cm"))[:6])
                    if (a, b, c, d) != (1.0, 0.0, 0.0, 1.0):
                        raise PdfLayoutError("trasformazione non supportata")
                    tx, ty = tx + e, ty + f
                elif op.group("q"):
                    stack.append((tx, ty))
                elif op.group("Q"):
                    if stack:
                        tx, ty = stack.pop()
                elif op.group("bt"):
                    block = op.group("bt")
                    tm = _TM.search(block)
                    ox, oy = (float(tm.group(5)), float(tm.group(6))) if tm else (0.0, 0.0)
                    cmap = None
                    parts = []
                    for tok in _TOKEN.finditer(block):
                        if tok.group(1) is not None:
                            cmap = cmaps.get(names.get(tok.group(1), -1))
                            continue
                        if tok.group(2) is not None:
                            h = re.sub(rb"\s+", b"", tok.group(2))
                            raw = bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
                        else:
                            raw = _literal(tok.group(3))
                        if cmap is None:
                            parts.append(raw.decode("cp1252", errors="replace"))
                        else:
                            w, mapping = cmap
                            parts.append("".join(mapping.get(int.from_bytes(raw[i:i + w], "big"), "")
                                                 for i in range(0, len(raw), w)))
                    if parts:
                        runs.append({"page": page_no, "text": "".join(parts), "x": tx + ox, "y": ty + oy,
                                     "context_x": tx, "context_y": ty, "page_width": width, "page_height": height})
    return runs


def extract_text(pdf: bytes) -> str:
    """Il testo, spazi normalizzati (per i confronti: ignorare gli spazi)."""
    return " ".join(" ".join(r["text"] for r in text_runs(pdf)).split())
