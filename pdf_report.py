# backend/pdf_report.py
from github_upload import upload_pdf_to_github

import os, datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, Flowable
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.barcode import qr
from reportlab.pdfbase.pdfmetrics import stringWidth

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from valuation import compute_from_payload


# -----------------------------------------------------------------------------
# UTIL LOGO
# -----------------------------------------------------------------------------
def _logo_path(base_dir: str):
    nomi = ["stimacentrato", "Stima360Definitiva", "stima360_logo"]
    est  = [".jpg", ".jpeg", ".png", ".webp"]
    cartelle = [
        os.path.join(base_dir, "..", "frontend"),
        os.path.join(base_dir, "frontend"),
        base_dir,
    ]
    for cart in cartelle:
        for n in nomi:
            for e in est:
                p = os.path.join(cart, f"{n}{e}")
                if os.path.exists(p):
                    return p
    return None


def _logo_flowable(logo_path: str, target_h_cm: float = 6.0):
    if not logo_path or not os.path.exists(logo_path):
        return Spacer(0, target_h_cm * cm)
    try:
        ir = ImageReader(logo_path)
        iw, ih = ir.getSize()
        h = target_h_cm * cm
        w = (iw / ih) * h
        return Image(logo_path, width=w, height=h)
    except Exception:
        return Spacer(0, target_h_cm * cm)


# -----------------------------------------------------------------------------
# CHIP KPI
# -----------------------------------------------------------------------------
class Chip(Flowable):
    def __init__(self, text, pad_h=5, pad_w=10, font="Helvetica", size=9,
                 bg="#eef6ff", fg="#1f2937", radius=4):
        super().__init__()
        self.text = text or "—"
        self.pad_h = pad_h
        self.pad_w = pad_w
        self.font = font
        self.size = size
        self.bg = colors.HexColor(bg)
        self.fg = colors.HexColor(fg)
        self.radius = radius
        self.width  = max(28, self.pad_w * 2 + len(self.text) * self.size * 0.52)
        self.height = self.pad_h * 2 + self.size * 1.15

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg)
        c.roundRect(0, 0, self.width, self.height, self.radius, fill=1, stroke=0)
        c.setFillColor(self.fg)
        c.setFont(self.font, self.size)
        tw = stringWidth(self.text, self.font, self.size)
        x = (self.width - tw) / 2.0
        c.drawString(x, self.pad_h, self.text)


def _kpi_row(d):
    order = [
        ("mq", lambda v: f"{v} mq"),
        ("prezzo_mq", lambda v: f"{v} €/mq"),
        ("stato", lambda v: f"Stato: {v}"),
        ("anno", lambda v: f"Anno: {v}"),
        ("piano", lambda v: f"Piano: {v}"),
        ("classe_energetica", lambda v: f"Classe: {v}"),
    ]
    palette = ["#f3e8ff", "#dbeafe", "#dcfce7", "#fef9c3", "#ffedd5", "#fee2e2"]

    chips = []
    i = 0
    for key, fmt in order:
        val = d.get(key)
        if val not in (None, "", "—"):
            chips.append(Chip(fmt(str(val)), bg=palette[i]))
            i += 1

    if not chips:
        return []

    table = Table([chips])
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return [table, Spacer(1, 8)]


# -----------------------------------------------------------------------------
# QR
# -----------------------------------------------------------------------------
def _qr_block(url: str, title_style, size_cm=2.8, title_text="Parla con noi"):
    qrw = qr.QrCodeWidget(url or "https://stima360.it/contatti")
    b = qrw.getBounds()
    w = b[2] - b[0]
    h = b[3] - b[1]
    size = size_cm * cm

    dqr = Drawing(w, h)
    dqr.add(qrw)
    dqr.scale(size / w, size / h)
    dqr.width = size
    dqr.height = size

    tbl = Table([[Paragraph(title_text, title_style), dqr]], colWidths=[None, size])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [tbl, Spacer(1, 10)]


# -----------------------------------------------------------------------------
# COMPARABILI
# -----------------------------------------------------------------------------
def _parse_comparabili(raw):
    if raw is None:
        return [140, 150, 160, 155, 165]  # default
    nums = []
    seq = raw if isinstance(raw, (list, tuple)) else [raw]
    for it in seq:
        try:
            if isinstance(it, (int, float)):
                nums.append(float(it))
            elif isinstance(it, str):
                nums.append(float(it.replace(",", ".")))
            elif isinstance(it, dict):
                for k in ("prezzo_mq", "prezzo", "valore"):
                    if k in it:
                        nums.append(float(str(it[k]).replace(",", ".")))
                        break
        except:
            pass
    return nums or [140, 150, 160, 155, 165]


# -----------------------------------------------------------------------------
# MAIN PDF
# -----------------------------------------------------------------------------
def genera_pdf_stima(dati: dict, nome_file="stima360.pdf"):

    base_dir = os.path.dirname(__file__)
    logo = _logo_flowable(_logo_path(base_dir))

    REPORTS_DIR = "/var/tmp/reports"
    os.makedirs(REPORTS_DIR, exist_ok=True)

    pdf_fs_path = os.path.join(REPORTS_DIR, nome_file)
    pdf_web_path = f"reports/{nome_file}"

    ss = getSampleStyleSheet()
    H2 = ParagraphStyle('H2', parent=ss['Heading2'], fontName='Helvetica-Bold',
                        fontSize=13, leading=16)
    P  = ParagraphStyle('P', parent=ss['BodyText'], fontSize=10.5, leading=14)
    BIG= ParagraphStyle('BIG', parent=ss['BodyText'], fontName='Helvetica-Bold',
                        fontSize=22, leading=28, alignment=TA_CENTER,
                        textColor=colors.HexColor("#0077cc"))

    doc = SimpleDocTemplate(
        pdf_fs_path, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=1.8*cm
    )
    flow = []

    # ---------------------------------------------------
    # VALORI DI STIMA
    # ---------------------------------------------------
    eur_mq_finale     = dati.get("eur_mq_finale")
    price_exact       = dati.get("price_exact")
    valore_pertinenze = dati.get("valore_pertinenze")
    base_mq           = dati.get("base_mq")

    if any(v is None for v in [eur_mq_finale, price_exact, valore_pertinenze, base_mq]):
        calc = compute_from_payload(dati)
        eur_mq_finale     = calc["eur_mq_finale"]
        price_exact       = calc["price_exact"]
        valore_pertinenze = calc["valore_pertinenze"]
        base_mq           = calc["base_mq"]

    # ---------------------------------------------------
    # LOGO GRANDE
    # ---------------------------------------------------
    flow += [logo, Spacer(1, 12)]

    # HERO PREZZO
    flow += [
        Paragraph(
            f"Valore totale: <b>€ {price_exact:,.0f}</b><br/>€/mq finale: € {eur_mq_finale:,.2f}",
            BIG
        ),
        Spacer(1, 8)
    ]

    # KPI
    flow += _kpi_row(dati)

    # ---------------------------------------------------
    # FATTORI MARE
    # ---------------------------------------------------
    posizione_mare = dati.get("posizioneMare", "—")
    distanza_mare  = dati.get("distanzaMare", "—")
    barriera_mare  = dati.get("barrieraMare", "—")
    barriera_tipo  = dati.get("barrieraTipo", "")

    if str(barriera_mare).lower() == "si" and barriera_tipo:
        barriera_mare = f"Sì — {barriera_tipo}"

    vista_yn   = str(dati.get("vistaMareYN", "")).lower().strip()
    vista_raw  = str(dati.get("vistaMareDettaglio", "")).lower().strip()

    if vista_yn == "no":
        vista_txt = "No"
    elif vista_yn == "si":
        vista_txt = vista_raw.capitalize() if vista_raw else "Sì (non specificata)"
    else:
        vista_txt = "—"

    # ---------------------------------------------------
    # RIEPILOGO IMMOBILE
    # ---------------------------------------------------
    indirizzo = f"{dati.get('via','')} {dati.get('civico','')}, {dati.get('comune','')}"

    def _fmt_eur(v):
        try:
            return f"{float(v):,.0f} €/mq".replace(",", ".")
        except:
            return "—"

    coeff_txt = "—"
    try:
        delta = ((eur_mq_finale / float(base_mq)) - 1.0) * 100
        coeff_txt = f"{delta:+.0f}%"
    except:
        pass

    riepilogo = [
        ["Indirizzo", indirizzo],
        ["Comune", dati.get("comune", "—")],
        ["Microzona", dati.get("microzona", "—")],
        ["Fascia mare", (dati.get("fascia_mare") or "—").replace("_", " ")],
        ["Posizione rispetto al mare", posizione_mare],
        ["Distanza dalla spiaggia", distanza_mare],
        ["Ferrovia / strada principale", barriera_mare],
        ["Vista mare", vista_txt],
        ["Tipologia", dati.get("tipologia","—")],
        ["Pertinenze", dati.get("pertinenze","—")],
        ["Prezzo base (€/mq)", _fmt_eur(base_mq)],
        ["Correttivo", coeff_txt],
        ["Prezzo finale (€/mq)", _fmt_eur(eur_mq_finale)],
    ]

    tbl = Table(riepilogo, colWidths=[6*cm, None])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f3f4f6")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, "#fafafa"]),
        ("INNERGRID", (0,0), (-1,-1), 0.25, "#e5e7eb"),
        ("BOX", (0,0), (-1,-1), 0.75, "#e5e7eb"),
    ]))
    flow += [
        Paragraph("Riepilogo immobile", H2),
        Spacer(1,4),
        tbl,
        Spacer(1,12)
    ]

    # ---------------------------------------------------
    # PERTINENZE DETTAGLIATE
    # ---------------------------------------------------
    def add_row(name, key):
        val = dati.get(key)
        if val and str(val) not in ("0", "", None):
            rows.append([name, f"{val} mq"])

    rows = []
    add_row("Giardino", "mqGiardino")
    add_row("Garage", "mqGarage")
    add_row("Cantina", "mqCantina")
    add_row("Posto Auto", "mqPostoAuto")
    add_row("Taverna", "mqTaverna")
    add_row("Soffitta", "mqSoffitta")
    add_row("Terrazzo", "mqTerrazzo")

    nb = dati.get("numBalconi")
    if nb not in (None, "", "0"):
        rows.append(["Balconi", nb])

    altro = dati.get("altroDescrizione")
    if altro:
        rows.append(["Altro", altro])

    if rows:
        tbl_p = Table(rows, colWidths=[6*cm, None])
        tbl_p.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(1,0), "#f3f4f6"),
            ("FONTNAME",(0,0),(1,0),"Helvetica-Bold"),
            ("INNERGRID",(0,0),(-1,-1),0.25,"#e5e7eb"),
            ("BOX",(0,0),(-1,-1),0.75,"#e5e7eb"),
        ]))
        flow += [
            Paragraph("Pertinenze dettagliate", H2),
            Spacer(1,4),
            tbl_p,
            Spacer(1,12)
        ]

    # ---------------------------------------------------
    # COMPARABILI (grafico demo)
    # ---------------------------------------------------
    comps = _parse_comparabili(dati.get("comparabili"))
    d = Drawing(400, 130)
    bc = VerticalBarChart()
    bc.data = [comps]
    bc.x = 30
    bc.y = 20
    bc.height = 90
    bc.width = 340
    bc.bars[0].fillColor = colors.HexColor("#e5e7eb")

    d.add(bc)
    flow += [
        Paragraph("Confronto comparabili (demo)", H2),
        Spacer(1,4),
        d,
        Spacer(1,12)
    ]

    # ---------------------------------------------------
    # QR
    # ---------------------------------------------------
    flow += _qr_block(
        url=dati.get("qr_url", "https://stima360.it/contatti"),
        title_style=H2,
        size_cm=2.8,
        title_text="Parla con noi"
    )

    # ---------------------------------------------------
    # NOTE LEGALI
    # ---------------------------------------------------
    nota = ("Questa stima è indicativa e non costituisce perizia. "
            "Valori e range dipendono dai dati inseriti e dal mercato locale. "
            "Per una valutazione professionale completa, contatta Stima360.")
    flow += [Paragraph(nota, P)]

    # FOOTER
    def _footer(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor("#6b7280")
        canvas.setFont("Helvetica", 8)
        today = datetime.date.today().strftime("%d/%m/%Y")
        canvas.drawString(2*cm, 1.2*cm, f"Stima360 • Generato il {today}")
        canvas.drawRightString(w-2*cm, 1.2*cm, f"Pagina {doc.page}")
        canvas.restoreState()

    # BUILD PDF
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)

    # UPLOAD GITHUB
    try:
        github_url = upload_pdf_to_github(pdf_fs_path, nome_file)
        return github_url or pdf_web_path
    except:
        return pdf_web_path
