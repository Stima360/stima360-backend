# backend/pdf_report.py

import os
import sys
import json
import base64
import datetime
import urllib.request
import urllib.error

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

# ---------------------------------------------------------------------

# Import valuation

# ---------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(**file**))
sys.path.insert(0, BASE_DIR)
from valuation import compute_from_payload  # noqa: E402

# ---------------------------------------------------------------------

# CONFIG GITHUB

# ---------------------------------------------------------------------

GITHUB_USER = os.getenv("GITHUB_USER")          # es. "Stima360"
GITHUB_REPO = os.getenv("GITHUB_REPO")          # es. "stima360-pdf"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")        # PAT con permessi repo
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# Base URL raw dei PDF (puoi anche non metterlo e verrà costruito)

GITHUB_PDF_BASE_URL = os.getenv(
"GITHUB_PDF_BASE_URL",
f"[https://raw.githubusercontent.com/{GITHUB_USER](https://raw.githubusercontent.com/{GITHUB_USER) or 'Stima360'}/{GITHUB_REPO or 'stima360-pdf'}/{GITHUB_BRANCH}"
)

# ---------------------------------------------------------------------

# UTILITY LOGO

# ---------------------------------------------------------------------

def _logo_path(base_dir: str):
"""
Cerca il logo in più cartelle e con nomi/estensioni comuni.
"""
nomi = ["stimacentrato", "Stima360Definitiva", "stima360_logo"]
est = [".jpg", ".jpeg", ".png", ".webp"]
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
from reportlab.platypus import Spacer  # import locale per evitare problemi
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

# ---------------------------------------------------------------------

# CHIP KPI

# ---------------------------------------------------------------------

class Chip(Flowable):
"""Pillola KPI semplice e robusta (non va in errore se vuota)."""

```
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
    # misura approssimata
    self.width = max(28, self.pad_w * 2 + len(self.text) * self.size * 0.52)
    self.height = self.pad_h * 2 + self.size * 1.15

def draw(self):
    c = self.canv
    c.setFillColor(self.bg)
    c.setStrokeColor(self.bg)
    c.roundRect(0, 0, self.width, self.height, self.radius, fill=1, stroke=0)
    c.setFillColor(self.fg)
    c.setFont(self.font, self.size)
    tw = stringWidth(self.text, self.font, self.size)
    x = max(self.pad_w, (self.width - tw) / 2.0)  # centro orizzontale
    c.drawString(x, self.pad_h, self.text)
```

def _kpi_row(d: dict):
"""
Pillole KPI a colori (sx→dx):
viola, blu, verde, giallo, arancione, rosso.
Ordine campi: mq, prezzo_mq, stato, anno, piano, classe_energetica.
"""
from reportlab.platypus import Spacer  # import locale

```
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
        bg = palette[min(ci, len(palette) - 1)]
        chips.append(Chip(fmt(str(val)), bg=bg))  # fg default scuro
        ci += 1

if not chips:
    return []

t = Table([chips])
t.setStyle(TableStyle([
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
]))
return [t, Spacer(1, 8)]
```

# ---------------------------------------------------------------------

# QR BLOCK

# ---------------------------------------------------------------------

def _qr_block(url: str, title_style, size_cm: float = 2.8, title_text: str = "Parla con noi"):
"""
QR vettoriale puro: nessun renderPM. Si scala il Drawing.
"""
from reportlab.platypus import Spacer  # import locale

```
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
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
]))
return [tbl, Spacer(1, 10)]
```

# ---------------------------------------------------------------------

# COMPARABILI

# ---------------------------------------------------------------------

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
except Exception:
pass
elif isinstance(it, dict):
for k in ("prezzo_mq", "prezzo", "valore"):
if k in it:
try:
nums.append(float(str(it[k]).replace(",", ".")))
break
except Exception:
pass
return nums or [140, 150, 160, 155, 165]

# ---------------------------------------------------------------------

# UPLOAD SU GITHUB

# ---------------------------------------------------------------------

def _upload_pdf_to_github(local_path: str, filename: str) -> str | None:
"""
Carica il PDF su GitHub (repo stima360-pdf) e ritorna la URL raw.
Se qualcosa va storto, ritorna None e non blocca l'app.
"""
if not (GITHUB_USER and GITHUB_REPO and GITHUB_TOKEN):
print("[GITHUB] Variabili GITHUB_USER / GITHUB_REPO / GITHUB_TOKEN mancanti, salto upload.")
return None

```
api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{filename}"

try:
    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")
except Exception as e:
    print(f"[GITHUB] Errore lettura file {local_path}: {e}")
    return None

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "stima360-backend"
}

# 1) Controllo se il file esiste già per recuperare la SHA (update invece di create)
sha = None
req_get = urllib.request.Request(api_url, headers=headers, method="GET")
try:
    resp = urllib.request.urlopen(req_get)
    info = json.loads(resp.read().decode("utf-8"))
    sha = info.get("sha")
except urllib.error.HTTPError as e:
    if e.code == 404:
        sha = None  # file non esiste, faremo create
    else:
        print(f"[GITHUB] Errore GET ({e.code}): {e}")
        return None
except Exception as e:
    print(f"[GITHUB] Errore GET generico: {e}")
    # posso continuare senza sha e tentare create

# 2) PUT (create o update)
payload = {
    "message": f"Add report {filename}",
    "content": content_b64,
    "branch": GITHUB_BRANCH,
}
if sha:
    payload["sha"] = sha

data_bytes = json.dumps(payload).encode("utf-8")
req_put = urllib.request.Request(api_url, data=data_bytes, headers=headers, method="PUT")

try:
    resp = urllib.request.urlopen(req_put)
    _ = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"[GITHUB] Errore PUT ({e.code}): {e.read().decode('utf-8', errors='ignore')}")
    return None
except Exception as e:
    print(f"[GITHUB] Errore PUT generico: {e}")
    return None

# 3) Costruisco URL RAW da usare nel sito/email/whatsapp
raw_base = GITHUB_PDF_BASE_URL or f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}"
url = f"{raw_base.rstrip('/')}/{filename}"
print(f"[GITHUB] Upload OK → {url}")
return url
```

# ---------------------------------------------------------------------

# FUNZIONE PRINCIPALE

# ---------------------------------------------------------------------

def genera_pdf_stima(dati: dict, nome_file: str = "stima360.pdf"):
"""
Report professionale e compatto.

```
- Genera il file fisicamente in /var/tmp/reports/<nome_file>
  (per poterlo allegare alle email con web_to_fs).
- Tenta l'upload su GitHub (repo configurata via env).
- Se upload OK → ritorna URL RAW GitHub.
- Se upload KO → ritorna path relativo 'reports/<nome_file>'.
  (Compatibile con la logica attuale di main.py)
"""
from reportlab.platypus import Spacer  # import locale

base_dir = BASE_DIR
logo_path = _logo_path(base_dir)

# 🔹 CARTELLA REPORT (compatibile Render e StaticFiles in main.py)
REPORTS_DIR = "/var/tmp/reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

pdf_fs_path = os.path.join(REPORTS_DIR, nome_file)

# stili
ss = getSampleStyleSheet()
H2 = ParagraphStyle(
    'H2',
    parent=ss['Heading2'],
    fontName='Helvetica-Bold',
    fontSize=13,
    leading=17,
    textColor=colors.HexColor("#1f2937")
)
P = ParagraphStyle(
    'P',
    parent=ss['BodyText'],
    fontSize=10.5,
    leading=14,
    textColor=colors.HexColor("#374151")
)
BIG = ParagraphStyle(
    'BIG',
    parent=ss['BodyText'],
    fontName='Helvetica-Bold',
    fontSize=22,
    leading=28,
    alignment=TA_CENTER,
    textColor=colors.HexColor("#0077cc")
)

# documento
doc = SimpleDocTemplate(
    pdf_fs_path,
    pagesize=A4,
    rightMargin=2 * cm,
    leftMargin=2 * cm,
    topMargin=1.8 * cm,
    bottomMargin=1.8 * cm
)
flow = []

# -------------------------------------------------------------
# Calcolo valori di stima (usa quelli già calcolati se presenti;
# altrimenti ricalcola con compute_from_payload)
# -------------------------------------------------------------
eur_mq_finale = dati.get("eur_mq_finale")
price_exact = dati.get("price_exact")
valore_pertinenze = dati.get("valore_pertinenze")
base_mq = dati.get("base_mq")

if any(v is None for v in [eur_mq_finale, price_exact, valore_pertinenze, base_mq]):
    try:
        calc = compute_from_payload(dati)
        eur_mq_finale = calc["eur_mq_finale"]
        price_exact = calc["price_exact"]
        valore_pertinenze = calc["valore_pertinenze"]
        base_mq = calc["base_mq"]
    except Exception as e:
        print(f"[STIMA] Errore compute_from_payload: {e}")

print(f"[STIMA] base_mq={base_mq} eur_mq_finale={eur_mq_finale} tot={price_exact} pertinenze={valore_pertinenze}")

# -------------------------------------------------------------
# LOGO GRANDE CENTRALE
# -------------------------------------------------------------
img_big = _logo_flowable(logo_path, target_h_cm=6.0)  # ~3x più alto
logo_center = Table([[img_big]])  # 1x1, si centra da sola
logo_center.setStyle(TableStyle([
    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
]))
flow += [logo_center, Spacer(1, 12)]

# -------------------------------------------------------------
# HERO PREZZO
# -------------------------------------------------------------
try:
    _val_tot = f"€ {float(price_exact):,.0f}".replace(",", ".")
except Exception:
    _val_tot = "—"
try:
    _val_mq = f"€ {float(eur_mq_finale):,.2f}".replace(",", ".")
except Exception:
    _val_mq = "—"

flow += [
    Paragraph(f"Valore totale: <b>{_val_tot}</b><br/>€/mq finale: {_val_mq}", BIG),
    Spacer(1, 8)
]

# KPI chips (usa i dati così come sono)
flow += _kpi_row(dati)

# -------------------------------------------------------------
# RIEPILOGO IMMOBILE
# -------------------------------------------------------------
def _fmt_eur_mq(v):
    try:
        return f"{float(v):,.0f} €/mq".replace(",", ".")
    except Exception:
        return "—"

indirizzo = dati.get("indirizzo") or f"{dati.get('via', '')} {dati.get('civico', '')}, {dati.get('comune', '')}".strip()
comune = dati.get("comune") or "—"
microzona = dati.get("microzona") or "—"
prezzo_base = base_mq

# coefficiente (solo per mostrare una %)
coeff_txt = "—"
try:
    if prezzo_base:
        ratio = (float(eur_mq_finale) / float(prezzo_base)) if float(prezzo_base) else 1.0
        delta = (ratio - 1.0) * 100.0
        coeff_txt = f"{'+' if delta >= 0 else ''}{delta:.0f}%"
except Exception:
    pass

prezzo_finale = eur_mq_finale

riepilogo = [
    ["Indirizzo", indirizzo or "—"],
    ["Comune", comune],
    ["Microzona", microzona],
    ["Fascia mare", (dati.get("fascia_mare") or "—").replace("_", " ")],
    ["Tipologia", (dati.get("tipologia") or "—")],
    ["Pertinenze", (dati.get("pertinenze") or "—")],
    ["Prezzo base (€/mq)", _fmt_eur_mq(prezzo_base)],
    ["Correttivo", coeff_txt],
    ["Prezzo finale (€/mq)", _fmt_eur_mq(prezzo_finale)],
]

tbl = Table(riepilogo, colWidths=[5 * cm, None])
tbl.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#e5e7eb")),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
flow += [Paragraph("Riepilogo immobile", H2), Spacer(1, 4), tbl, Spacer(1, 12)]

# Valori calcolati precisi
if base_mq is not None:
    flow.append(Paragraph(f"Base €/mq microzona: € {base_mq:,.2f}", P))
if eur_mq_finale is not None:
    flow.append(Paragraph(f"€/mq finale: € {eur_mq_finale:,.2f}", P))
if valore_pertinenze is not None:
    flow.append(Paragraph(f"Valore pertinenze: € {valore_pertinenze:,.0f}", P))
if price_exact is not None:
    flow.append(Paragraph(f"Valore totale immobile: € {price_exact:,.0f}", P))
flow.append(Spacer(1, 12))

# -------------------------------------------------------------
# GRAFICO COMPARABILI
# -------------------------------------------------------------
safe = _parse_comparabili(dati.get("comparabili"))
d = Drawing(400, 130)
bc = VerticalBarChart()
bc.x = 30
bc.y = 20
bc.height = 90
bc.width = 340
bc.data = [safe]
bc.strokeColor = colors.HexColor("#e5e7eb")
bc.valueAxis.strokeColor = colors.HexColor("#e5e7eb")
bc.categoryAxis.strokeColor = colors.HexColor("#e5e7eb")
bc.barWidth = 14
bc.groupSpacing = 8
bc.bars[0].fillColor = colors.HexColor("#e5e7eb")
d.add(bc)
flow += [Paragraph("Confronto comparabili (demo)", H2), Spacer(1, 4), d, Spacer(1, 12)]

# -------------------------------------------------------------
# QR
# -------------------------------------------------------------
flow += _qr_block(
    url=dati.get("qr_url", "https://stima360.it/contatti"),
    title_style=H2,
    size_cm=2.8,
    title_text="Parla con noi"
)

# nota legale
nota = (
    "Questa stima è indicativa e non costituisce perizia. "
    "Valori e range dipendono dai dati inseriti e dal mercato locale. "
    "Per una valutazione professionale completa, contatta Stima360."
)
flow += [Paragraph(nota, P)]

# footer
def _footer(canvas, doc_obj):
    canvas.saveState()
    w, h = A4
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    today = datetime.date.today().strftime("%d/%m/%Y")
    canvas.drawString(2 * cm, 1.2 * cm, f"Stima360 • Generato il {today}")
    canvas.drawRightString(w - 2 * cm, 1.2 * cm, f"Pagina {doc_obj.page}")
    canvas.restoreState()

# build PDF
try:
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
except Exception as e:
    # Log minimale, ma non blocca il flusso dell'app
    print({"detail": f"Errore generazione REPORT: {e}"})

# -------------------------------------------------------------
# Upload su GitHub (se possibile)
# -------------------------------------------------------------
github_url = _upload_pdf_to_github(pdf_fs_path, nome_file)

if github_url:
    # main.py: in /api/salva_stima farà:
    #   if pdf_web_path.startswith("http"): usa così com'è
    return github_url

# fallback: vecchio comportamento → path relativo servito da /reports
return f"reports/{nome_file}"
```
