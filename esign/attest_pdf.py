"""FIRMA OTP - il DOCUMENTO SOTTOSCRITTO: il PDF originale, byte per byte nelle sue pagine, con la dicitura di
sottoscrizione negli spazi firma e una pagina finale di attestazione del procedimento.

Non e' una firma crittografica PAdES e non lo dichiara: e' un'attestazione visiva del procedimento OTP, verificabile
con il rapporto delle prove (impronta dell'originale, impronta di questo file, codice del procedimento). Le pagine
originali vengono copiate con `pypdf` (nessuna rigenerazione del contenuto); la dicitura e' un livello sovrapposto.
"""
from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo

from .layout import locate_signature_boxes
from .pdf_text import PdfLayoutError

ROMA = ZoneInfo("Europe/Rome")
NOTICE = "Sottoscrizione elettronica mediante conferma OTP — STIMA360"
LEVEL_NOTICE = ("Firma elettronica semplice (FES, art. 25 par. 1 Reg. UE 910/2014; art. 20 D.Lgs. 82/2005). Attestazione "
                "del procedimento: non è una firma digitale né una firma elettronica qualificata.")


def _quando(dt: datetime) -> str:
    return dt.astimezone(ROMA).strftime("%d/%m/%Y %H:%M:%S") + " (ora italiana)"


def _overlay(layout: dict, signers: list[dict]) -> bytes:
    """Un PDF della stessa pagina con il testo di sottoscrizione dentro i riquadri firma."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(layout["page_width"], layout["page_height"]))
    for s in signers:
        box = layout["boxes"].get(s["role"])
        if box is None or s.get("status") != "signed":
            continue
        x, y, w, h = box["x"], box["y"], box["width"], box["height"]
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 2, y + h - 10, "Sottoscritto elettronicamente (OTP)")
        c.setFont("Helvetica", 7.5)
        c.drawString(x + 2, y + h - 20, (s["name"] or "")[:60])
        c.drawString(x + 2, y + h - 29, _quando(s["signed_at"]))
        c.drawString(x + 2, y + h - 38, f"Procedura {s['procedure']}")
    c.showPage()
    c.save()
    return buffer.getvalue()


def _attestation_page(identity: dict, info: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from property_valuations.pdf_render import _header, _kv_table, _register_fonts, _styles, clean_text

    _register_fonts()
    styles = _styles()
    cm = 28.3465
    margin = 2.0 * cm
    width = A4[0] - 2 * margin
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=1.6 * cm,
                            bottomMargin=1.8 * cm, title="Attestazione di sottoscrizione")
    story = list(_header(identity, styles, width))
    story.append(Paragraph(clean_text(NOTICE), styles["title"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(clean_text(LEVEL_NOTICE), styles["note"]))
    story.append(Spacer(1, 8))
    rows = [("Codice del procedimento", info["procedure"]), ("Documento", info["title"]),
            ("Impronta SHA-256 dell'originale", info["original_sha256"]),
            ("Rapporto delle prove", f"Procedura {info['procedure']}: conservato nel CRM e scaricabile dall'agenzia")]
    story.append(_kv_table([(k, clean_text(v)) for k, v in rows], styles, width))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Firmatari", styles["h2"]))
    for s in info["signers"]:
        story.append(_kv_table([
            ("Ruolo", s["role_label"]), ("Nominativo", clean_text(s["name"])),
            ("Metodo", s["method"]),
            ("Data e ora della conferma", _quando(s["signed_at"]) if s.get("signed_at") else "—"),
        ], styles, width))
        story.append(Spacer(1, 4))
    story.append(Spacer(1, 6))
    story.append(Paragraph(clean_text(
        "Le pagine precedenti riproducono il documento sottoposto ai firmatari nella versione identificata "
        "dall'impronta sopra indicata; la dicitura negli spazi firma e questa pagina sono aggiunte dal CRM al termine del "
        "procedimento. L'integrità si verifica confrontando le impronte con il rapporto delle prove."), styles["small"]))
    doc.build(story)
    return buffer.getvalue()


def build(original: bytes, *, identity: dict, info: dict) -> bytes:
    """`info`: procedure, title, original_sha256, signers[{role, role_label, name, method, status, signed_at, procedure}]."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(original))
    writer = PdfWriter()
    writer.append(reader)
    try:
        layout = locate_signature_boxes(original)
    except PdfLayoutError:
        layout = None
    if layout is not None:
        page_no = layout["boxes"]["visitor"]["page"] - 1
        overlay = PdfReader(io.BytesIO(_overlay(layout, info["signers"])))
        writer.pages[page_no].merge_page(overlay.pages[0])
    writer.append(PdfReader(io.BytesIO(_attestation_page(identity, info))))
    writer.add_metadata({"/Title": f"{info['title']} — sottoscritto (OTP)", "/Producer": "STIMA360 CRM"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
