"""BLOCCO-2B - impaginazione del PDF di una valutazione dell'agente approvata.

Funzione pura: riceve la versione approvata (contenuto, fotografia
dell'immobile, approvatore) e l'intestazione dell'agenzia, restituisce i byte
del PDF. Nessun accesso al database, nessuna rete: gli URL dei comparabili
sono stampati come testo, mai aperti.

Regole:
* i testi degli utenti sono SEMPRE testo: escape XML prima di `Paragraph`, a
  capo convertiti, caratteri che il font non ha sostituiti con "?";
* nessun dato inventato: un campo mancante non compare, una sezione mancante
  e' "Non valutata";
* importi in formato italiano con unita' e periodo espliciti;
* font Bitstream Vera (incluso in ReportLab, quindi presente su ogni server
  che ha ReportLab), incorporato nel PDF;
* `invariant=1`: a parita' di ingresso, gli stessi byte.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from .enums import (
    COMPARABLE_KINDS,
    COMPARABLE_PRICE_TYPES,
    COMPARABLE_RENT_PERIODS,
    METHOD_LABEL,
    RENT_EXPENSES,
    RENT_FURNISHING,
    RENT_SEGMENTS,
    SURFACE_KINDS,
    SURFACE_SOURCES,
)

TEMPLATE_VERSION = "valutazione_agente_pdf_v1"

DISCLAIMER = ("Valutazione indicativa elaborata dall'agente sulla base dei dati e delle fonti riportati in "
              "questo documento. Non è una perizia certificata né una quotazione automatica OMI e non "
              "costituisce un'offerta o un impegno commerciale.")

ROME = ZoneInfo("Europe/Rome")

_FONTS = {"regular": "StimaVera", "bold": "StimaVera-Bold", "italic": "StimaVera-Italic"}
_FONT_FILES = {"regular": "Vera.ttf", "bold": "VeraBd.ttf", "italic": "VeraIt.ttf"}
_SUPPORTED: set[int] = set()


class PdfLogoError(Exception):
    """Il logo presente non si puo' usare: errore tecnico, non "logo assente"."""


def _register_fonts() -> None:
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _SUPPORTED and _FONTS["regular"] in pdfmetrics.getRegisteredFontNames():
        return
    folder = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    for key, name in _FONTS.items():
        font = TTFont(name, os.path.join(folder, _FONT_FILES[key]))
        pdfmetrics.registerFont(font)
        if key == "regular":
            _SUPPORTED.update(font.face.charToGlyph.keys())
    pdfmetrics.registerFontFamily(_FONTS["regular"], normal=_FONTS["regular"], bold=_FONTS["bold"],
                                  italic=_FONTS["italic"], boldItalic=_FONTS["bold"])


# ---------------------------------------------------------------------------
# formattazione
# ---------------------------------------------------------------------------

def plain_text(value) -> str:
    """Testo con i soli glifi che il font possiede ("?" al posto degli altri)."""
    _register_fonts()
    return "".join(ch if (ch == "\n" or ord(ch) in _SUPPORTED) else "?" for ch in str(value))


def clean_text(value) -> str:
    """Testo dell'utente pronto per `Paragraph`: glifi, escape XML, a capo."""
    return escape(plain_text(value)).replace("\n", "<br/>")


def euro(value, period: str | None = None) -> str | None:
    """'195000.5' -> '195.000,50 €'; con periodo: '780,00 €/mese'."""
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    integer, _, decimals = f"{amount:,.2f}".partition(".")
    text = f"{integer.replace(',', '.')},{decimals} €"
    return f"{text}/{period}" if period else text


def square_metres(value) -> str | None:
    if value in (None, ""):
        return None
    amount = Decimal(str(value)).normalize()
    text = f"{amount:f}".replace(".", ",")
    return f"{text} m²"


def italian_datetime(value) -> str | None:
    if not value:
        return None
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is not None:
        moment = moment.astimezone(ROME)
    return moment.strftime("%d/%m/%Y alle %H:%M")


def italian_date(value) -> str | None:
    if not value:
        return None
    parts = str(value)[:10].split("-")
    return "/".join(reversed(parts)) if len(parts) == 3 else str(value)


# ---------------------------------------------------------------------------
# impaginazione
# ---------------------------------------------------------------------------

def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    ink, muted, accent = colors.HexColor("#1f2937"), colors.HexColor("#6b7280"), colors.HexColor("#1e3a8a")
    base = dict(fontName=_FONTS["regular"], textColor=ink, fontSize=9.5, leading=13)
    return {
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8, "leading": 10.5, "textColor": muted}),
        "label": ParagraphStyle("label", **{**base, "fontSize": 8.5, "leading": 11.5, "textColor": muted}),
        "value": ParagraphStyle("value", **{**base, "fontName": _FONTS["bold"]}),
        "agency": ParagraphStyle("agency", **{**base, "fontName": _FONTS["bold"], "fontSize": 14, "leading": 17}),
        "title": ParagraphStyle("title", **{**base, "fontName": _FONTS["bold"], "fontSize": 19, "leading": 23,
                                            "textColor": accent, "spaceBefore": 4, "spaceAfter": 2}),
        "subtitle": ParagraphStyle("subtitle", **{**base, "fontSize": 11, "leading": 14}),
        # keepWithNext: un titolo di sezione non resta mai da solo in fondo alla pagina.
        "h2": ParagraphStyle("h2", **{**base, "fontName": _FONTS["bold"], "fontSize": 11.5, "leading": 15,
                                       "textColor": accent, "spaceBefore": 12, "spaceAfter": 5,
                                       "keepWithNext": 1}),
        "note": ParagraphStyle("note", **{**base, "fontName": _FONTS["italic"], "fontSize": 8.5, "leading": 11.5,
                                          "textColor": muted}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.5, "leading": 11}),
        "cellhead": ParagraphStyle("cellhead", **{**base, "fontName": _FONTS["bold"], "fontSize": 8, "leading": 10,
                                                  "textColor": muted}),
        "colors": {"ink": ink, "muted": muted, "accent": accent, "line": colors.HexColor("#d1d5db"),
                   "band": colors.HexColor("#f3f4f6")},
    }


def _logo_flowable(logo: dict, max_width: float, max_height: float):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image

    try:
        reader = ImageReader(io.BytesIO(logo["data"]))
        width, height = reader.getSize()
    except Exception as exc:                       # un logo c'e' ma non si legge
        raise PdfLogoError("il logo dell'agenzia non e' leggibile") from exc
    if width <= 0 or height <= 0:
        raise PdfLogoError("il logo dell'agenzia ha dimensioni non valide")
    scale = min(max_width / width, max_height / height)
    return Image(io.BytesIO(logo["data"]), width=width * scale, height=height * scale)


def _kv_table(rows, styles, width):
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph(clean_text(k), styles["label"]), Paragraph(v, styles["body"])]
            for k, v in rows if v not in (None, "")]
    if not data:
        return None
    table = Table(data, colWidths=[5.0 * 28.3465, width - 5.0 * 28.3465])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, styles["colors"]["line"]),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _header(identity: dict, styles, width):
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    lines = [Paragraph(clean_text(identity.get("header_name") or identity.get("agency_name") or ""), styles["agency"])]
    if identity.get("legal_name"):
        lines.append(Paragraph(clean_text(identity["legal_name"]), styles["small"]))
    if identity.get("legal_address"):
        lines.append(Paragraph(clean_text(f"Sede legale: {identity['legal_address']}"), styles["small"]))
    fiscal = [f"P.IVA {identity['vat_number']}" if identity.get("vat_number") else None,
              f"C.F. {identity['tax_code']}" if identity.get("tax_code") else None]
    if any(fiscal):
        lines.append(Paragraph(clean_text(" · ".join(x for x in fiscal if x)), styles["small"]))
    contacts = [identity.get(k) for k in ("phone", "email", "website") if identity.get(k)]
    if contacts:
        lines.append(Paragraph(clean_text(" · ".join(contacts)), styles["small"]))
    logo = identity.get("logo")
    if logo:
        cells = [[_logo_flowable(logo, 5.2 * 28.3465, 2.2 * 28.3465), lines]]
        table = Table(cells, colWidths=[5.6 * 28.3465, width - 5.6 * 28.3465])
    else:
        table = Table([[lines]], colWidths=[width])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, styles["colors"]["accent"]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [table, Spacer(1, 10)]


def _property_reference(snapshot: dict) -> str:
    place = ", ".join(x for x in (
        " ".join(x for x in (snapshot.get("address"), snapshot.get("civic_number")) if x),
        " ".join(x for x in (snapshot.get("city"), f"({snapshot['province']})" if snapshot.get("province") else None) if x),
    ) if x)
    return place or (snapshot.get("title") or "")


def _section_absent(story, styles, title):
    from reportlab.platypus import Paragraph

    story.append(Paragraph(clean_text(title), styles["h2"]))
    story.append(Paragraph("Non valutata", styles["note"]))


def _comparables_table(comparables, styles, width):
    from reportlab.platypus import Paragraph, Table, TableStyle

    cm = 28.3465
    widths = [2.0 * cm, 2.4 * cm, 2.9 * cm, 2.3 * cm, 3.1 * cm]
    widths.append(width - sum(widths))
    head = ["Tipo", "Prezzo", "Importo", "Superficie", "Zona", "Fonte e data del dato"]
    data = [[Paragraph(h, styles["cellhead"]) for h in head]]
    spans = []
    for cp in comparables:
        period = None
        if cp.get("kind") == "locazione":
            period = "mese" if cp.get("rent_period") == "mensile" else "anno"
        surface = square_metres(cp.get("surface"))
        if surface and cp.get("surface_kind"):
            kind = SURFACE_KINDS.get(cp["surface_kind"], cp["surface_kind"]).lower()
            surface = f"{surface} {kind.removeprefix('superficie ').split(' (')[0]}"
        source = cp.get("source") or ""
        if cp.get("data_date"):
            source = f"{source}, {italian_date(cp['data_date'])}"
        data.append([
            Paragraph(clean_text(COMPARABLE_KINDS.get(cp.get("kind"), cp.get("kind") or "")), styles["cell"]),
            Paragraph(clean_text(COMPARABLE_PRICE_TYPES.get(cp.get("price_type"), cp.get("price_type") or "")), styles["cell"]),
            Paragraph(clean_text(euro(cp.get("amount"), period) or ""), styles["cell"]),
            Paragraph(clean_text(surface or "—"), styles["cell"]),
            Paragraph(clean_text(cp.get("zone") or "—"), styles["cell"]),
            Paragraph(clean_text(source), styles["cell"]),
        ])
        details = []
        if cp.get("kind") == "locazione":
            details.append(f"Periodo di riferimento dell'importo: {COMPARABLE_RENT_PERIODS.get(cp.get('rent_period'), '—').lower()}")
        for label, key in (("Caratteristiche", "features"), ("Differenze rispetto all'immobile", "differences"),
                           ("Indirizzo web (riportato, non consultato)", "url")):
            if cp.get(key):
                details.append(f"{label}: {cp[key]}")
        if details:
            row = len(data)
            data.append([Paragraph(clean_text("\n".join(details)), styles["small"])] + [""] * 5)
            spans.append(("SPAN", (0, row), (-1, row)))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), styles["colors"]["band"]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, styles["colors"]["line"]),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        *spans,
    ]))
    return table


def render_valuation_pdf(valuation: dict, identity: dict, *, approver_name: str, generated_at) -> bytes:
    """I byte del PDF. `valuation` e' una versione APPROVATA o superata
    serializzata (contenuto, fotografia, versione, approvazione); `identity`
    e' l'intestazione dell'agenzia (con `logo` None se non configurato)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

    _register_fonts()
    styles = _styles()
    cm = 28.3465
    margin = 2.0 * cm
    # La cornice di SimpleDocTemplate ha 6 punti di margine interno per lato:
    # le tabelle devono stare in quella larghezza, non nell'intera pagina.
    width = A4[0] - 2 * margin - 12
    content = valuation.get("content") or {}
    snapshot = valuation.get("property_snapshot") or {}
    footer_left = plain_text(f"{METHOD_LABEL} · {identity.get('header_name') or identity.get('agency_name') or ''} "
                             f"· versione {valuation['version']}").replace("\n", " ")

    class NumberedCanvas(rl_canvas.Canvas):
        """Pagina X di Y: il totale si conosce solo alla fine."""

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
    story.append(Paragraph(clean_text(METHOD_LABEL), styles["title"]))
    reference = _property_reference(snapshot)
    if reference:
        story.append(Paragraph(clean_text(reference), styles["subtitle"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(clean_text(DISCLAIMER), styles["note"]))

    story.append(Paragraph("Versione della valutazione", styles["h2"]))
    story.append(_kv_table([
        ("Versione", clean_text(str(valuation["version"]))),
        ("Stato", "Approvata" if valuation.get("status") == "approved" else "Superata da una versione successiva"),
        ("Approvata il", clean_text(italian_datetime(valuation.get("approved_at")) or "")),
        ("Approvata da", clean_text(approver_name)),
        ("Metodo", clean_text(METHOD_LABEL)),
        ("Documento generato il", clean_text(italian_datetime(generated_at) or "")),
    ], styles, width))

    story.append(Paragraph("Immobile", styles["h2"]))
    surface = content.get("surface") or {}
    kv = _kv_table([
        ("Riferimento", clean_text(snapshot.get("title") or "")),
        ("Indirizzo", clean_text(reference)),
        ("Codice interno", clean_text(snapshot.get("code") or "")),
        ("Caratteristiche rilevanti", clean_text(content.get("characteristics") or "")),
        ("Superficie utilizzata", clean_text(square_metres(surface.get("value")) or "")),
        ("Tipo di superficie", clean_text(SURFACE_KINDS.get(surface.get("kind"), "") if surface.get("kind") else "")),
        ("Provenienza del dato", clean_text(SURFACE_SOURCES.get(surface.get("source"), "") if surface.get("source") else "")),
        ("Nota sulla superficie", clean_text(surface.get("note") or "")),
    ], styles, width)
    if kv:
        story.append(kv)

    sale = content.get("sale")
    if sale:
        story.append(KeepTogether([Paragraph("Vendita", styles["h2"]), _kv_table([
            ("Intervallo di valore", clean_text(f"da {euro(sale.get('min')) or '—'} a {euro(sale.get('max')) or '—'}")),
            ("Prezzo di proposta consigliato", f"<b>{clean_text(euro(sale.get('recommended')) or '—')}</b>"),
            ("Motivo del consigliato fuori intervallo", clean_text(sale.get("outside_range_reason") or "")),
        ], styles, width)]))
    else:
        _section_absent(story, styles, "Vendita")

    rent = content.get("rent")
    if rent:
        story.append(KeepTogether([Paragraph("Locazione", styles["h2"]), _kv_table([
            ("Canone mensile", clean_text(f"da {euro(rent.get('min'), 'mese') or '—'} a {euro(rent.get('max'), 'mese') or '—'}")),
            ("Canone mensile consigliato", f"<b>{clean_text(euro(rent.get('recommended'), 'mese') or '—')}</b>"),
            ("Segmento", clean_text(RENT_SEGMENTS.get(rent.get("segment"), ""))),
            ("Arredo", clean_text(RENT_FURNISHING.get(rent.get("furnishing"), ""))),
            ("Spese", clean_text(RENT_EXPENSES.get(rent.get("expenses"), ""))),
            ("Motivo del consigliato fuori intervallo", clean_text(rent.get("outside_range_reason") or "")),
            ("Note", clean_text(rent.get("notes") or "")),
        ], styles, width)]))
    else:
        _section_absent(story, styles, "Locazione")

    story.append(Paragraph("Motivazione", styles["h2"]))
    story.append(Paragraph(clean_text(content.get("rationale") or ""), styles["body"]))
    story.append(Paragraph("Fonti e base informativa", styles["h2"]))
    story.append(Paragraph(clean_text(content.get("sources") or ""), styles["body"]))

    story.append(Paragraph("Comparabili", styles["h2"]))
    comparables = content.get("comparables") or []
    if comparables:
        story.append(Paragraph("Dati inseriti dall'agente. «Prezzo richiesto» indica un prezzo di offerta; "
                               "«Prezzo concluso» un prezzo di una trattativa chiusa.", styles["small"]))
        story.append(Spacer(1, 4))
        story.append(_comparables_table(comparables, styles, width))
        if content.get("comparables_note"):
            story.append(Spacer(1, 4))
            story.append(Paragraph(clean_text(content["comparables_note"]), styles["small"]))
    else:
        note = content.get("comparables_note")
        story.append(Paragraph(clean_text(f"Nessun comparabile. Motivo dichiarato dall'agente: {note}" if note
                                          else "Nessun comparabile."), styles["body"]))

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=2.2 * cm, invariant=1,
                            title=f"{METHOD_LABEL} - versione {valuation['version']}",
                            author=identity.get("header_name") or identity.get("agency_name") or "",
                            subject=METHOD_LABEL, creator="STIMA360")
    doc.build(story, canvasmaker=NumberedCanvas)
    return out.getvalue()
