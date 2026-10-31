"""BLOCCO-6A - il PDF del foglio visita.

Stessi caratteri, stili e intestazione dell'agenzia dei PDF delle valutazioni
(`property_valuations.pdf_render`). Il testo e' una BOZZA DI MODELLO da
revisionare: dichiara chi, cosa, dove e quando, e lascia gli spazi per le
firme. Non contiene clausole provvigionali, impegni o obblighi del visitatore,
e non attesta una visita avvenuta finche' l'operatore non ne ha registrato
l'esito (`visit_state = completed`).
"""
from __future__ import annotations

import io

from property_valuations.pdf_render import _header, _kv_table, _register_fonts, _styles, clean_text, plain_text

TEMPLATE_VERSION = "foglio_visita_bozza_v1"
TITLE = "Foglio visita"
DRAFT_NOTICE = ("Bozza di modello da revisionare prima dell'uso. Il documento registra soltanto la visita "
                "all'immobile indicato: non contiene clausole provvigionali né impegni del visitatore.")
STATE_TEXT = {
    "booked": "Visita prenotata: l'esito non è ancora stato registrato. Questo foglio non attesta che la visita "
              "sia avvenuta.",
    "completed": "Visita svolta: esito registrato dall'operatore in agenda il {completed}.",
}


def render_visit_sheet_pdf(snapshot: dict, identity: dict, *, generated_at_text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_fonts()
    styles = _styles()
    cm = 28.3465
    margin = 2.0 * cm
    width = A4[0] - 2 * margin
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=1.6 * cm,
                            bottomMargin=1.8 * cm, title=TITLE, author=plain_text(identity.get("header_name") or ""))
    prop, visit = snapshot["property"], snapshot["visit"]
    story = list(_header(identity, styles, width))
    story.append(Paragraph(TITLE, styles["title"]))
    story.append(Paragraph(clean_text(f"Versione {snapshot['sheet_version']} · generato il {generated_at_text}"),
                           styles["small"]))
    story.append(Spacer(1, 6))
    state = STATE_TEXT[snapshot["visit_state"]].format(completed=visit.get("completed_at_text") or "")
    story.append(Paragraph(clean_text(state), styles["note"]))

    story.append(Paragraph("Immobile", styles["h2"]))
    story.append(_kv_table([
        ("Riferimento", clean_text(prop.get("code") or "")),
        ("Tipologia", clean_text(prop.get("type_label") or "")),
        ("Indirizzo", clean_text(prop.get("address") or "")),
    ], styles, width))
    mandate = snapshot.get("mandate") or {}
    story.append(Paragraph("Incarico", styles["h2"]))
    rows = [("Incarico", "Presente" if mandate.get("exists") else "Non registrato")]
    if mandate.get("exists"):
        rows += [("Tipo", clean_text(mandate.get("type") or "")), ("Dal", clean_text(mandate.get("start") or "")),
                 ("Al", clean_text(mandate.get("end") or ""))]
    story.append(_kv_table(rows, styles, width))

    story.append(Paragraph("Visita", styles["h2"]))
    story.append(_kv_table([
        ("Data", clean_text(visit["date_text"])),
        ("Orario", clean_text(visit["time_text"])),
        ("Agente", clean_text(visit.get("agent_name") or "")),
        ("Visitatore", clean_text(visit.get("visitor_name") or "")),
        ("Altri partecipanti", clean_text(", ".join(visit.get("participants") or []))),
    ], styles, width))

    firme = Table([[Paragraph("Firma del visitatore", styles["label"]), "", Paragraph("Firma dell'agente", styles["label"])],
                   ["", "", ""]], colWidths=[width * 0.44, width * 0.12, width * 0.44], rowHeights=[None, 1.6 * cm])
    firme.setStyle(TableStyle([("LINEBELOW", (0, 1), (0, 1), 0.6, styles["colors"]["ink"]),
                               ("LINEBELOW", (2, 1), (2, 1), 0.6, styles["colors"]["ink"]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([Paragraph("Firme", styles["h2"]), firme, Spacer(1, 4),
                               Paragraph("Spazi per la firma: il foglio stampato o generato non è un documento "
                                         "sottoscritto finché non lo firmano le parti.", styles["small"])]))
    story.append(Spacer(1, 14))
    story.append(Paragraph(clean_text(DRAFT_NOTICE), styles["note"]))
    story.append(Paragraph(clean_text(f"Modello {TEMPLATE_VERSION} · generato da {snapshot.get('author_name') or ''}"),
                           styles["small"]))
    doc.build(story)
    return buffer.getvalue()
