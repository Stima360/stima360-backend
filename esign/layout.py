"""Dove mettere i campi firma sul foglio visita, ricavato dal PDF GIA' ARCHIVIATO (sola lettura).

Il foglio non cambia: le posizioni si leggono dal PDF stesso, cosi' valgono per qualunque versione gia' generata e non
dipendono da come sarebbe reso oggi (logo, lunghezza dei testi, interruzioni di pagina).

Il modello (`visit_sheets.pdf_render`) disegna una tabella con le etichette «Firma del visitatore» e «Firma dell'agente»
sopra due righe di firma. Le costanti qui sotto SPECCHIANO quel modello (colonne 44% / 12% / 44% della larghezza utile,
riga di firma alta 1,6 cm + 3 pt di margine). Un test genera un foglio vero e verifica che i riquadri stiano dove devono:
se il modello cambia, il test fallisce e questo file si aggiorna con lui.
"""
from __future__ import annotations

from .pdf_text import PdfLayoutError, text_runs

CM = 28.3465
SIGNATURE_ROW = 1.6 * CM + 3.0                  # altezza della riga di firma (cella) in punti
FIELD_INSET = 4.0                               # il campo sta dentro la cella, sopra la riga
COLUMN_FRACTION = 0.44
LABELS = {"visitor": "Firma del visitatore", "agent": "Firma dell'agente"}


def _norm(text: str) -> str:
    return "".join(text.split()).lower().replace("’", "'")


def locate_signature_boxes(pdf: bytes) -> dict:
    """{page_width, page_height, boxes: {role: {page, x, y, width, height}}} in punti PDF (origine in basso a sinistra).
    Solleva `PdfLayoutError` se le etichette non sono trovate ESATTAMENTE una volta, o se cadono fuori pagina."""
    runs = text_runs(pdf)
    boxes, size = {}, None
    for role, label in LABELS.items():
        hit = [r for r in runs if _norm(r["text"]) == _norm(label)]
        if len(hit) != 1:
            raise PdfLayoutError(f"etichetta «{label}» trovata {len(hit)} volte (ne serve una)")
        r = hit[0]
        # larghezza utile = larghezza pagina - 2 * margine; il margine sinistro e' la x della colonna del visitatore
        boxes[role] = {"page": r["page"], "x": r["context_x"], "top": r["context_y"], "page_width": r["page_width"],
                       "page_height": r["page_height"]}
        size = (r["page_width"], r["page_height"])
    left = boxes["visitor"]["x"]
    useful = size[0] - 2 * left
    # larghezza: quella del modello, mai oltre lo spazio fino alla colonna accanto (o al margine destro)
    room = {"visitor": boxes["agent"]["x"] - left - 8.0, "agent": size[0] - left - boxes["agent"]["x"]}
    out = {}
    for role, b in boxes.items():
        box = {"page": b["page"], "x": b["x"] + FIELD_INSET, "y": b["top"] - SIGNATURE_ROW + FIELD_INSET,
               "width": min(useful * COLUMN_FRACTION, room[role]) - 2 * FIELD_INSET, "height": SIGNATURE_ROW - 2 * FIELD_INSET}
        if (box["x"] < 0 or box["y"] < 0 or box["x"] + box["width"] > size[0] or box["y"] + box["height"] > size[1]):
            raise PdfLayoutError("il riquadro di firma cade fuori pagina")
        out[role] = box
    if out["visitor"]["page"] != out["agent"]["page"] or abs(out["visitor"]["y"] - out["agent"]["y"]) > 1:
        raise PdfLayoutError("le due firme non sono sulla stessa riga")
    return {"page_width": size[0], "page_height": size[1], "boxes": out}
