"""REPORT-PROPRIETARIO-1 - impaginazione del report attivita' per il proprietario.

Stessa base tipografica e stessa intestazione agenzia (logo, nome, recapiti
configurati) del PDF delle valutazioni (BLOCCO-2B): font con € e accenti,
testo dell'utente sempre neutralizzato, nessun indirizzo web aperto, A4 con
«Pagina X di Y». Byte deterministici a parita' di dati (`invariant=1`).
"""
from __future__ import annotations

import io

from property_valuations.pdf_render import (
    _FONTS,
    _header,
    _kv_table,
    _register_fonts,
    _styles,
    clean_text,
    euro,
    italian_date,
    italian_datetime,
    plain_text,
)

from .enums import AGENT_NOTES_MAX, TITLE


def _table(rows, styles, widths):
    """Tabella con la prima riga d'intestazione, ripetuta sulle pagine successive."""
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph(clean_text(cell), styles["cellhead"] if i == 0 else styles["cell"]) for cell in row]
            for i, row in enumerate(rows)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, styles["colors"]["line"]),
        ("BACKGROUND", (0, 0), (-1, 0), styles["colors"]["band"]),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _period_text(period: dict) -> str:
    return f"dal {italian_date(period['from'])} al {italian_date(period['to'])}"


def render_owner_report_pdf(report: dict, identity: dict, *, agent_notes: str | None, generated_at) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    _register_fonts()
    styles = _styles()
    cm = 28.3465
    margin = 2.0 * cm
    width = A4[0] - 2 * margin - 12
    prop, period, mandate = report["property"], report["period"], report["mandate"]
    agency = identity.get("header_name") or identity.get("agency_name") or ""
    footer_left = plain_text(f"{TITLE} · {agency} · {_period_text(period)}").replace("\n", " ")

    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(_FONTS["regular"], 7.5)
                self.setFillColor(styles["colors"]["muted"])
                self.setStrokeColor(styles["colors"]["line"])
                self.line(margin, 1.45 * cm, A4[0] - margin, 1.45 * cm)
                self.drawString(margin, 1.05 * cm, footer_left)
                self.drawRightString(A4[0] - margin, 1.05 * cm, f"Pagina {self._pageNumber} di {total}")
                super().showPage()
            super().save()

    story = list(_header(identity, styles, width))
    story.append(Paragraph(clean_text(TITLE), styles["title"]))
    reference = prop.get("title") or prop.get("address") or f"Immobile n. {prop['id']}"
    story.append(Paragraph(clean_text(reference), styles["subtitle"]))
    story.append(Paragraph(clean_text(f"Periodo {_period_text(period)} · documento generato il "
                                      f"{italian_datetime(generated_at)}"), styles["note"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Immobile e incarico", styles["h2"]))
    story.append(_kv_table([
        ("Immobile", clean_text(prop.get("title") or "")),
        ("Indirizzo", clean_text(prop.get("address") or "")),
        ("Codice", clean_text(prop.get("code") or "")),
        ("Stato commerciale", clean_text(prop.get("commercial_status_label") or "")),
        ("Prezzo richiesto", clean_text(euro(prop.get("asking_price")) or "Non indicato")),
        ("Incarico", clean_text(mandate["state_label"] if mandate["exists"] else "Nessun incarico registrato")),
        ("Inizio incarico", clean_text(italian_date(mandate.get("start")) or "")),
        ("Scadenza incarico", clean_text(italian_date(mandate.get("end")) or "")),
    ], styles, width))

    story.append(Paragraph("Attività nel periodo", styles["h2"]))
    proposals = report["proposals"]
    summary = [
        ("Richieste ricevute", str(report["inquiries"]["total"])),
        ("Visite effettuate", str(report["visits"]["completed"])),
        ("Visite già in programma", str(report["visits"]["upcoming"])),
        ("Proposte di acquisto", str(proposals["total"])),
    ]
    summary += [(f"  di cui {p['label'].lower()}", str(p["count"])) for p in proposals["by_status"]]
    story.append(_kv_table([(k, clean_text(v)) for k, v in summary], styles, width))

    story.append(Paragraph("Andamento mese per mese", styles["h2"]))
    rows = [("Mese", "Richieste ricevute", "Visite effettuate")]
    rows += [(m["label"], str(m["inquiries"]), str(m["visits"])) for m in report["monthly"]]
    story.append(_table(rows, styles, [width * 0.5, width * 0.25, width * 0.25]))

    story.append(Paragraph("Prezzo richiesto", styles["h2"]))
    if report["price_history"]:
        rows = [("Data", "Prezzo precedente", "Nuovo prezzo")]
        rows += [(italian_date(p["date"]), euro(p["old_price"]) or "—", euro(p["new_price"]) or "—")
                 for p in report["price_history"]]
        story.append(_table(rows, styles, [width * 0.3, width * 0.35, width * 0.35]))
    else:
        story.append(Paragraph(clean_text("Nessuna variazione del prezzo richiesto nel periodo."), styles["body"]))

    story.append(Paragraph("Stato commerciale", styles["h2"]))
    if report["status_history"]:
        rows = [("Data", "Da", "A")]
        rows += [(italian_date(s["date"]), s["old_label"] or "—", s["new_label"] or "—")
                 for s in report["status_history"]]
        story.append(_table(rows, styles, [width * 0.3, width * 0.35, width * 0.35]))
    else:
        story.append(Paragraph(clean_text("Nessun cambio di stato commerciale nel periodo."), styles["body"]))

    notes = (agent_notes or "").strip()[:AGENT_NOTES_MAX]
    story.append(KeepTogether([
        Paragraph("Indicazioni dell'agente", styles["h2"]),
        Paragraph(clean_text(notes) if notes else clean_text("Nessuna indicazione aggiunta dall'agente."),
                  styles["body"] if notes else styles["note"]),
    ]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Come sono contati i dati", styles["h2"]))
    for line in report["definitions"]:
        story.append(Paragraph(clean_text(line), styles["small"]))

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=2.2 * cm, invariant=1, title=TITLE, author=agency, subject=TITLE,
                            creator="STIMA360")
    doc.build(story, canvasmaker=NumberedCanvas)
    return out.getvalue()
