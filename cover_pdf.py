# backend/cover_pdf.py
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader

def genera_cover_pdf(nome_file="cover_stima360.pdf",
                     logo_path="frontend/stimacentrato.jpg",
                     titolo="La valutazione del tuo immobile"):
    """
    COVER minimal e professionale:
    - sfondo bianco
    - logo centrato
    - titolo sotto il logo
    - riga "arcobaleno" sottilissima in basso (accento brand)
    Ritorna: 'reports/...' (per servirla via /reports)
    """
    print("USO FUNZIONE:", genera_cover_pdf.__module__)

    BASE_DIR = os.path.dirname(__file__)
    REPORTS_DIR = os.path.join(BASE_DIR, "reports")
    os.makedirs(REPORTS_DIR, exist_ok=True)

    pdf_fs_path  = os.path.join(REPORTS_DIR, nome_file)
    pdf_web_path = os.path.join("reports", nome_file)

    W, H = A4
    c = canvas.Canvas(pdf_fs_path, pagesize=A4)

    # --- sfondo bianco (di default) ---

    # --- logo centrato
    max_w, max_h = 12*cm, 12*cm
    try:
        img = ImageReader(logo_path if os.path.isabs(logo_path) else os.path.join(BASE_DIR, "..", logo_path))
        iw, ih = img.getSize()
        ratio = min(max_w/iw, max_h/ih)
        dw, dh = iw*ratio, ih*ratio
        x = (W - dw)/2
        y = H*0.60 - dh/2
        c.drawImage(img, x, y, width=dw, height=dh, preserveAspectRatio=True, mask='auto')
    except Exception:
        # placeholder se manca il logo
        dw, dh = max_w, max_h*0.4
        x = (W - dw)/2
        y = H*0.60 - dh/2
        c.setFillColor(HexColor("#eeeeee"))
        c.rect(x, y, dw, dh, stroke=0, fill=1)

    # --- titolo sotto il logo
    c.setFillColor(HexColor("#1f2937"))  # grigio scuro elegante
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(W/2, y - 1.2*cm, titolo)

    # --- riga "arcobaleno" sottile in basso (pastello, 5-6px)
    bar_y = 1.1*cm
    bar_h = 0.18*cm
    colors = ["#f43f5e","#f97316","#f59e0b","#22c55e","#06b6d4","#3b82f6","#a855f7"]  # arcobaleno soft
    seg_w = W/len(colors)
    for i, col in enumerate(colors):
        c.setFillColor(HexColor(col))
        c.rect(i*seg_w, bar_y, seg_w, bar_h, stroke=0, fill=1)

    # metadati
    c.setTitle("Stima360 – Cover")
    c.setAuthor("Stima360")
    c.setSubject("Cover valutazione immobiliare – Stima360")

    c.showPage()
    c.save()
    return pdf_web_path
