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
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from valuation import compute_from_payload



# ---------------------- UTIL ----------------------
def _logo_path(base_dir: str):
    """
    Cerca il logo in più cartelle e con nomi/estensioni comuni.
    """
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


def _logo_flowable(logo_path: str, target_h_cm: float = 2.0):
    """
    Restituisce un Image proporzionato o uno Spacer se il logo manca.
    """
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


class Chip(Flowable):
    """Pillola KPI semplice e robusta (non va in errore se vuota)."""
    def __init__(self, text, pad_h=5, pad_w=10, font="Helvetica", size=9,
                 bg="#eef6ff", fg="#1f2937", radius=4):
        super().__init__()
        self.text=text or "—"; self.pad_h=pad_h; self.pad_w=pad_w
        self.font=font; self.size=size
        self.bg=colors.HexColor(bg); self.fg=colors.HexColor(fg)
        self.radius=radius
        # misura approssimata
        self.width  = max(28, self.pad_w*2 + len(self.text)*self.size*0.52)
        self.height = self.pad_h*2 + self.size*1.15

    def draw(self):
        c=self.canv
        c.setFillColor(self.bg); c.setStrokeColor(self.bg)
        c.roundRect(0, 0, self.width, self.height, self.radius, fill=1, stroke=0)
        c.setFillColor(self.fg); c.setFont(self.font, self.size)
        tw = stringWidth(self.text, self.font, self.size)
        x = max(self.pad_w, (self.width - tw) / 2.0)  # centro orizzontale
        c.drawString(x, self.pad_h, self.text)

def _kpi_row(d: dict):
    """
    Pillole KPI a colori (sx→dx):
    viola, blu, verde, giallo, arancione, rosso.
    Ordine campi: mq, prezzo_mq, stato, anno, piano, classe_energetica.
    """
    order = [
        ("mq", lambda v: f"{v} mq"),
        ("prezzo_mq", lambda v: f"{v} €/mq"),
        ("stato", lambda v: f"Stato: {v}"),
        ("anno", lambda v: f"Anno: {v}"),
        ("piano", lambda v: f"Piano: {v}"),
        ("classe_energetica", lambda v: f"Classe: {v}"),
    ]
    # palette pastello (testo scuro si legge bene)
    palette = ["#f3e8ff", "#dbeafe", "#dcfce7", "#fef9c3", "#ffedd5", "#fee2e2"]
    chips = []
    ci = 0
    for key, fmt in order:
        val = d.get(key)
        if val not in (None, "", "—"):
            bg = palette[min(ci, len(palette)-1)]
            chips.append(Chip(fmt(str(val)), bg=bg))  # fg default scuro
            ci += 1

    if not chips:
        return []

    t = Table([chips])
    t.setStyle(TableStyle([
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    return [t, Spacer(1, 8)]

def _qr_block(url: str, title_style, size_cm: float = 2.8, title_text: str = "Parla con noi"):
    """
    QR vettoriale puro: nessun renderPM. Si scala il Drawing.
    """
    qrw = qr.QrCodeWidget(url or "https://stima360.it/contatti")
    b = qrw.getBounds()
    w = max(1.0, b[2] - b[0])
    h = max(1.0, b[3] - b[1])

    size = size_cm * cm

    dqr = Drawing(w, h)
    dqr.add(qrw)

    sx, sy = (size / w), (size / h)
    dqr.scale(sx, sy)
    dqr.width = size
    dqr.height = size

    tbl = Table([[Paragraph(title_text, title_style), dqr]], colWidths=[None, size])
    tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    return [tbl, Spacer(1, 10)]


def _parse_comparabili(raw):
    """
    Accetta list/tuple di numeri, stringhe (anche '150,5') o dict
    con chiavi: prezzo_mq / prezzo / valore. Ritorna lista float.
    """
    if raw is None:
        return [140, 150, 160, 155, 165]
    nums = []
    seq = raw if isinstance(raw, (list, tuple)) else [raw]
    for it in seq:
        if isinstance(it, (int, float)):
            nums.append(float(it))
        elif isinstance(it, str):
            try:
                nums.append(float(it.replace(",", ".")))
            except:
                pass
        elif isinstance(it, dict):
            for k in ("prezzo_mq", "prezzo", "valore"):
                if k in it:
                    try:
                        nums.append(float(str(it[k]).replace(",", ".")))
                        break
                    except:
                        pass
    return nums or [140, 150, 160, 155, 165]


# ---------------------- MAIN ----------------------
def genera_pdf_stima(dati: dict, nome_file: str = "stima360.pdf"):
    """
    Report professionale e compatto.
    Ritorna: URL finale del PDF
    - Se upload GitHub va a buon fine: URL GitHub Releases
    - Altrimenti: path relativo 'reports/...'
    """
    base_dir = os.path.dirname(__file__)
    logo_path = _logo_path(base_dir)

    # 🔹 CARTELLA REPORT (compatibile Render)
    REPORTS_DIR = "/var/tmp/reports"
    os.makedirs(REPORTS_DIR, exist_ok=True)

    pdf_fs_path  = os.path.join(REPORTS_DIR, nome_file)
    pdf_web_path = f"reports/{nome_file}"   # fallback: serve dal backend

    # stili
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle('H1', parent=ss['Heading1'], fontName='Helvetica-Bold',
                        fontSize=18, leading=22, alignment=TA_LEFT,
                        textColor=colors.HexColor("#1f2937"))
    H2 = ParagraphStyle('H2', parent=ss['Heading2'], fontName='Helvetica-Bold',
                        fontSize=13, leading=17, textColor=colors.HexColor("#1f2937"))
    P  = ParagraphStyle('P',  parent=ss['BodyText'], fontSize=10.5, leading=14,
                        textColor=colors.HexColor("#374151"))
    BIG= ParagraphStyle('BIG',parent=ss['BodyText'], fontName='Helvetica-Bold',
                        fontSize=22, leading=28, alignment=TA_CENTER,
                        textColor=colors.HexColor("#0077cc"))

    # documento
    doc = SimpleDocTemplate(
        pdf_fs_path, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=1.8*cm, bottomMargin=1.8*cm
    )
    flow = []

    # Calcolo valori di stima (usa quelli già calcolati se presenti; altrimenti ricalcola)
    eur_mq_finale     = dati.get("eur_mq_finale")
    price_exact       = dati.get("price_exact")
    valore_pertinenze = dati.get("valore_pertinenze")
    base_mq           = dati.get("base_mq")

    if any(v is None for v in [eur_mq_finale, price_exact, valore_pertinenze, base_mq]):
        # Mancano valori -> ricalcolo con compute_from_payload(dati)
        calc = compute_from_payload(dati)
        eur_mq_finale     = calc["eur_mq_finale"]
        price_exact       = calc["price_exact"]
        valore_pertinenze = calc["valore_pertinenze"]
        base_mq           = calc["base_mq"]

    print(f"[STIMA] base_mq={base_mq} eur_mq_finale={eur_mq_finale} tot={price_exact} pertinenze={valore_pertinenze}")

    # --- LOGO GRANDE CENTRALE (nuovo, non tocca l'header) ---
    img_big = _logo_flowable(logo_path, target_h_cm=6.0)  # ~3x più alto
    logo_center = Table([[img_big]])  # 1x1, si centra da sola
    logo_center.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    flow += [logo_center, Spacer(1, 12)]

    # hero prezzo (valore esatto)
    _val_tot = f"€ {price_exact:,.0f}".replace(",", ".")
    _val_mq  = f"€ {eur_mq_finale:,.2f}".replace(",", ".")
    flow += [Paragraph(f"Valore totale: <b>{_val_tot}</b><br/>€/mq finale: {_val_mq}", BIG), Spacer(1, 8)]

    # KPI chips
    flow += _kpi_row(dati)

    # --- RIEPILOGO IMMOBILE (mostra Comune+Microzona+€/mq) ---
    def _fmt_eur_mq(v):
        try:
            return f"{float(v):,.0f} €/mq".replace(",", ".")
        except:
            return "—"

    indirizzo = dati.get("indirizzo") or f"{dati.get('via','')} {dati.get('civico','')}, {dati.get('comune','')}".strip()
    comune     = dati.get("comune") or "—"
    microzona  = dati.get("microzona") or "—"
    prezzo_base = base_mq

    coeff_txt = "—"
    try:
        if prezzo_base:
            _ratio = (eur_mq_finale / float(prezzo_base)) if float(prezzo_base) else 1.0
            delta = (_ratio - 1.0) * 100.0
            coeff_txt = f"{'+' if delta>=0 else ''}{delta:.0f}%"
    except:
        pass

    prezzo_finale = eur_mq_finale

    riepilogo = [
        ["Indirizzo", indirizzo or "—"],
        ["Comune", comune],
        ["Microzona", microzona],
        ["Fascia mare", (dati.get("fascia_mare") or "—").replace("_", " ")],
        ["Tipologia", dati.get("tipologia","") or "—"],
        ["Pertinenze", dati.get("pertinenze","") or "—"],
        ["Prezzo base (€/mq)", _fmt_eur_mq(prezzo_base)],
        ["Correttivo", coeff_txt],
        ["Prezzo finale (€/mq)", _fmt_eur_mq(prezzo_finale)],
    ]

    tbl = Table(riepilogo, colWidths=[5*cm, None])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.HexColor("#f3f4f6")),
        ("FONTNAME",(0,0),(-1,0), "Helvetica-Bold"),
        ("TEXTCOLOR",(0,0),(-1,0), colors.HexColor("#111827")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, colors.HexColor("#fafafa")]),
        ("INNERGRID",(0,0),(-1,-1), 0.25, colors.HexColor("#e5e7eb")),
        ("BOX",(0,0),(-1,-1), 0.75, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING",(0,0),(-1,-1), 6),
        ("RIGHTPADDING",(0,0),(-1,-1), 6),
        ("TOPPADDING",(0,0),(-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    flow += [Paragraph("Riepilogo immobile", H2), Spacer(1,4), tbl, Spacer(1, 12)]

    # --- Valori calcolati precisi ---
    flow.append(Paragraph(f"Base €/mq microzona: € {base_mq:,.2f}", P))
    flow.append(Paragraph(f"€/mq finale: € {eur_mq_finale:,.2f}", P))
    flow.append(Paragraph(f"Valore pertinenze: € {valore_pertinenze:,.0f}", P))
    flow.append(Paragraph(f"Valore totale immobile: € {price_exact:,.0f}", P))
    flow.append(Spacer(1, 12))

    # micro grafico comparabili (robusto)
    safe = _parse_comparabili(dati.get("comparabili"))
    d = Drawing(400, 130)
    bc = VerticalBarChart()
    bc.x = 30; bc.y = 20
    bc.height = 90; bc.width = 340
    bc.data = [safe]
    bc.strokeColor = colors.HexColor("#e5e7eb")
    bc.valueAxis.strokeColor = colors.HexColor("#e5e7eb")
    bc.categoryAxis.strokeColor = colors.HexColor("#e5e7eb")
    bc.barWidth = 14
    bc.groupSpacing = 8
    bc.bars[0].fillColor = colors.HexColor("#e5e7eb")
    d.add(bc)
    flow += [Paragraph("Confronto comparabili (demo)", H2), Spacer(1,4), d, Spacer(1, 12)]

    # QR vettoriale (compatibile)
    flow += _qr_block(
        url=dati.get("qr_url", "https://stima360.it/contatti"),
        title_style=H2,
        size_cm=2.8,
        title_text="Parla con noi"
    )

    # nota legale
    nota = ("Questa stima è indicativa e non costituisce perizia. "
            "Valori e range dipendono dai dati inseriti e dal mercato locale. "
            "Per una valutazione professionale completa, contatta Stima360.")
    flow += [Paragraph(nota, P)]

    # footer
    def _footer(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        today = datetime.date.today().strftime("%d/%m/%Y")
        canvas.drawString(2*cm, 1.2*cm, f"Stima360 • Generato il {today}")
        canvas.drawRightString(w-2*cm, 1.2*cm, f"Pagina {doc.page}")
        canvas.restoreState()

    # build PDF
    try:
        doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    except Exception as e:
        print({"detail": f"Errore generazione REPORT: {e}"})

    # 🔗 Upload su GitHub Releases
    github_url = None
    try:
        github_url = upload_pdf_to_github(pdf_fs_path, nome_file)
        if github_url:
            print(f"[STIMA] PDF caricato su GitHub: {github_url}")
        else:
            print("[STIMA] Upload GitHub non ha restituito URL, uso fallback locale.")
    except Exception as e:
        print({"detail": f"Errore upload GitHub: {e}"})

    # Se upload ok → ritorno URL GitHub, altrimenti il vecchio path relativo
    return github_url or pdf_web_path
