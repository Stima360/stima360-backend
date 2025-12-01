# backend/main.py — versione ripulita Stima360

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import os, uvicorn, secrets, uuid, requests

from database import get_connection, invia_mail
from pdf_report import genera_pdf_stima
from valuation import compute_from_payload

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
BASE_DIR = Path(__file__).parent
REPORTS_DIR = Path("/var/tmp/reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://stima360-backend.onrender.com")

# WhatsApp Cloud API
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")

# ---------------------------------------------------------
# APP & CORS
# ---------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"], allow_credentials=True
)

# Static (PDF)
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# ---------------------------------------------------------
# UTILS
# ---------------------------------------------------------
def normalizza_numero_whatsapp(raw: str | None) -> str | None:
    if not raw:
        return None
    s = "".join(ch for ch in raw if ch.isdigit())
    if not s:
        return None
    if s.startswith("39"):
        return s
    return "39" + s.lstrip("0")


def invia_whatsapp(numero: str | None, messaggio: str):
    if not (WHATSAPP_TOKEN and WHATSAPP_PHONE_ID):
        return

    dest = normalizza_numero_whatsapp(numero)
    if not dest:
        return

    url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": dest,
        "type": "text",
        "text": {"body": messaggio}
    }
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

    try:
        requests.post(url, json=payload, headers=headers, timeout=10)
    except:
        pass


def to_int(v): 
    try: return int(v)
    except: return None

def to_float(v):
    try: return float(str(v).replace(",", "."))
    except: return None

def to_bool(v):
    if isinstance(v, bool): return v
    if v is None: return None
    s = str(v).lower()
    return True if s in {"si","sì","true","1","y"} else False if s in {"no","false","0"} else None

def format_indirizzo(via, civico, comune):
    via_civ = " ".join(p for p in [via or "", civico or ""] if p).strip()
    return ", ".join([via_civ, comune]) if comune else via_civ

def web_to_fs(path: str) -> str:
    name = path.split("/")[-1]
    return str((REPORTS_DIR / name).resolve())

def normalizza_comune(v: str | None) -> str | None:
    if not v:
        return None
    v = " ".join(w.capitalize() for w in v.replace("_", " ").split())
    return v if v in {"Alba Adriatica", "Martinsicuro", "Tortoreto"} else None

# ---------------------------------------------------------
# LOGIN ADMIN
# ---------------------------------------------------------
security = HTTPBasic()
def verifica_login(credentials: HTTPBasicCredentials):
    u = os.getenv("ADMIN_USER", "admin")
    p = os.getenv("ADMIN_PASS", "password")
    if not (secrets.compare_digest(credentials.username, u) and secrets.compare_digest(credentials.password, p)):
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    return True

# ---------------------------------------------------------
# ENDPOINT: SALVA STIMA
# ---------------------------------------------------------
@app.post("/api/salva_stima")
async def salva_stima(request: Request):

    # --- 1. Leggi body ---
    try:
        if "application/json" in (request.headers.get("content-type") or ""):
            raw = await request.json()
        else:
            raw = dict(await request.form())
    except:
        raw = {}

    # --- 2. Normalizza ---
    data = {
        "comune": raw.get("comune"),
        "microzona": raw.get("microzona"),
        "fascia_mare": (raw.get("fascia_mare") or "").lower().strip(),
        "via": raw.get("via"),
        "civico": raw.get("civico"),
        "tipologia": raw.get("tipologia"),
        "mq": to_float(raw.get("mq")),
        "piano": raw.get("piano"),
        "locali": to_int(raw.get("locali")),
        "bagni": to_int(raw.get("bagni")),
        "pertinenze": raw.get("pertinenze"),
        "ascensore": to_bool(raw.get("ascensore")),
        "nome": raw.get("nome"),
        "cognome": raw.get("cognome"),
        "email": raw.get("email"),
        "telefono": raw.get("telefono"),
        "prezzo_mq_base": to_float(raw.get("prezzo_mq_base")),
        "anno": to_int(raw.get("anno")),
        "stato": raw.get("stato"),
    }

    # --- 3. Se €mq base non presente → leggi DB ---
    if not data["prezzo_mq_base"]:
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("""
                SELECT prezzo_mq_base FROM zone_valori
                WHERE comune=%s AND microzona=%s LIMIT 1
            """, (data["comune"], data["microzona"]))
            row = cur.fetchone()
            data["prezzo_mq_base"] = float(row[0]) if row else 0.0
        except:
            data["prezzo_mq_base"] = 0.0
        finally:
            try: cur.close(); conn.close()
            except: pass

    # --- 4. Salva stima base ---
    conn = get_connection(); cur = conn.cursor()
    try:
        comune_db = normalizza_comune(data["comune"])
        cur.execute("""
             INSERT INTO stime
              (comune, microzona, fascia_mare, via, civico, tipologia, mq, piano, locali,
               bagni, pertinenze, ascensore, nome, cognome, email, telefono)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id
        """, (
            comune_db, data["microzona"], data["fascia_mare"],
            data["via"], data["civico"], data["tipologia"],
            data["mq"], data["piano"], data["locali"], data["bagni"],
            data["pertinenze"], data["ascensore"],
            data["nome"], data["cognome"], data["email"], data["telefono"]
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore INSERT DB: {e}")
    finally:
        try: cur.close(); conn.close()
        except: pass

    # --- 5. TOKEN e prezzo base ---
    token = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(days=7)

    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE stime SET token=%s, token_expires=%s, prezzo_mq_base=%s
            WHERE id=%s
        """, (token, expires, data["prezzo_mq_base"], new_id))
        conn.commit()
    except:
        pass
    finally:
        try: cur.close(); conn.close()
        except: pass

    # --- 6. Stima completa (engine ufficiale) ---
    payload_rules = {
        "comune": data["comune"],
        "microzona": data["microzona"],
        "tipologia": data["tipologia"],
        "mq": data["mq"],
        "piano": data["piano"],
        "locali": data["locali"],
        "bagni": data["bagni"],
        "ascensore": "Sì" if data["ascensore"] else "No",
        "anno": data["anno"],
        "stato": data["stato"],
        "posizioneMare": raw.get("posizioneMare"),
        "distanzaMare": raw.get("distanzaMare"),
        "barrieraMare": raw.get("barrieraMare"),
        "vistaMare": raw.get("vistaMare"),
        "pertinenze": data["pertinenze"] or "",
        "mqGiardino": raw.get("mqGiardino"),
        "mqGarage": raw.get("mqGarage"),
    }

    calc = compute_from_payload(payload_rules)

    price_exact = calc["price_exact"]
    eur_mq_finale = calc["eur_mq_finale"]
    valore_pertinenze = calc["valore_pertinenze"]
    base_mq = calc["base_mq"]

    indirizzo = format_indirizzo(data["via"], data["civico"], data["comune"])

    # --- 7. PDF ---
    try:
        pdf_web_path = genera_pdf_stima({
            "id_stima": new_id,
            "indirizzo": indirizzo,
            "comune": data["comune"],
            "microzona": data["microzona"],
            "tipologia": data["tipologia"],
            "mq": data["mq"],
            "piano": data["piano"],
            "locali": data["locali"],
            "bagni": data["bagni"],
            "ascensore": "Sì" if data["ascensore"] else "No",
            "pertinenze": data["pertinenze"],
            "stima": f"{price_exact:,.0f} €".replace(",", "."),
            "price_exact": price_exact,
            "eur_mq_finale": eur_mq_finale,
            "valore_pertinenze": valore_pertinenze,
            "base_mq": base_mq,
        }, nome_file=f"stima_{new_id}.pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore PDF: {e}")

    pdf_link = f"{PUBLIC_BASE_URL}/{pdf_web_path.lstrip('/')}"
    det_link = f"{PUBLIC_BASE_URL}/static/dati_personali.html?t={token}"

    # --- 8. Email ---
    try:
        corpo = f"""
        <h2>🏡 La tua stima Stima360 è pronta!</h2>
        <p>Ciao <b>{data['nome']}</b>, ecco la valutazione del tuo immobile.</p>
        <p>📄 <a href="{pdf_link}">Apri il PDF</a></p>
        <p>🧩 <a href="{det_link}">Richiedi stima dettagliata</a></p>
        """
        invia_mail(data["email"], f"Stima360 – {indirizzo}", corpo)
    except:
        pass

    # --- 9. WhatsApp ---
    try:
        msg = (
            f"Ciao {data['nome']}! 🏡 La tua stima per {indirizzo} è pronta.\n\n"
            f"PDF: {pdf_link}\nStima dettagliata: {det_link}"
        )
        invia_whatsapp(data["telefono"], msg)
    except:
        pass

    # --- 10. Risposta frontend ---
    return {
        "success": True,
        "id": new_id,
        "pdf_url": f"/{pdf_web_path}",
        "price_exact": price_exact,
        "eur_mq_finale": eur_mq_finale,
        "valore_pertinenze": valore_pertinenze,
        "base_mq": base_mq,
    }

# ---------------------------------------------------------
# PREFILL TOKEN
# ---------------------------------------------------------
@app.get("/api/prefill")
async def prefill(t: str):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT id,nome,cognome,email,telefono,comune,microzona,via,civico,tipologia,
                   mq,piano,locali,bagni,pertinenze,ascensore
            FROM stime
            WHERE token=%s AND (token_expires IS NULL OR token_expires > NOW())
            LIMIT 1
        """, (t,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except:
        raise HTTPException(status_code=500, detail="Errore prefill")

    if not row:
        raise HTTPException(status_code=404, detail="Token non valido")

    keys = ["id","nome","cognome","email","telefono","comune","microzona","via","civico","tipologia",
            "mq","piano","locali","bagni","pertinenze","ascensore"]
    return dict(zip(keys, row))

# ---------------------------------------------------------
# SALVA STIMA DETTAGLIATA
# ---------------------------------------------------------
@app.post("/api/salva_stima_dettagliata")
async def salva_stima_dettagliata(request: Request):
    data = await request.json()

    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO stime_dettagliate(
                stima_id, comune, via, civico, tipologia, mq, piano, locali, bagni, ascensore,
                pertinenze, stato, anno, classe, riscaldamento, condizionatore, spese_cond,
                balcone, giardino, posto_auto, esposizione, arredo, note, contatto, sopralluogo
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data.get("stima_id"), data.get("comune"), data.get("via"), data.get("civico"),
            data.get("tipologia"), data.get("mq"), data.get("piano"), data.get("locali"),
            to_int(data.get("bagni")), to_bool(data.get("ascensore")), data.get("pertinenze"),
            data.get("stato"), data.get("anno"), data.get("classe"),
            data.get("riscaldamento"), data.get("condizionatore"),
            data.get("spese_cond"), data.get("balcone"), data.get("giardino"),
            data.get("posto_auto"), data.get("esposizione"), data.get("arredo"),
            data.get("note"), data.get("contatto"), data.get("sopralluogo")
        ))
        conn.commit()
        cur.close(); conn.close()
    except:
        raise HTTPException(status_code=500, detail="Errore salvataggio dettagliata")

    # PDF dettagliata
    try:
        pdf_path = genera_pdf_stima(data, nome_file=f"stima_dettagliata_{data.get('stima_id')}.pdf")
    except:
        raise HTTPException(status_code=500, detail="Errore PDF dettagliata")

    # email
    try:
        invia_mail(
            data.get("email"), "Stima360 – Stima dettagliata pronta!",
            "<p>In allegato trovi la valutazione completa.</p>",
            allegato=web_to_fs(pdf_path)
        )
    except:
        pass

    return {"status": "ok", "pdf": f"/{pdf_path}"}

# ---------------------------------------------------------
# ADMIN
# ---------------------------------------------------------

class LeadUpdate(BaseModel):
    lead_status: str | None = None
    note_internal: str | None = None

@app.get("/api/admin/stime")
def admin_lista_stime(
    day: str = "oggi",
    dal: date | None = None,
    al: date | None = None,
    credentials: HTTPBasicCredentials = Depends(security)
):
    verifica_login(credentials)

    if dal and al:
        start = datetime.combine(dal, datetime.min.time())
        end   = datetime.combine(al + timedelta(days=1), datetime.min.time())
    else:
        base = date.today() - timedelta(days=1) if day == "ieri" else date.today()
        start = datetime.combine(base, datetime.min.time())
        end   = datetime.combine(base + timedelta(days=1), datetime.min.time())

    conn = get_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.data, s.comune, s.microzona, s.via, s.civico, s.tipologia,
               s.mq, s.piano, s.locali, s.bagni, s.pertinenze, s.ascensore,
               s.nome, s.cognome, s.email, s.telefono, s.lead_status, s.note_internal,
               sd.stato AS stato_dettaglio, sd.data AS data_dettaglio
        FROM stime s
        LEFT JOIN stime_dettagliate sd ON sd.stima_id = s.id
        WHERE s.data >= %s AND s.data < %s
        ORDER BY s.data DESC
    """, (start, end))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    cur.close(); conn.close()

    return {"items": [dict(zip(cols, r)) for r in rows]}

@app.post("/api/admin/stime/{stima_id}/update")
def admin_update_stima(
    stima_id: int,
    payload: LeadUpdate,
    credentials: HTTPBasicCredentials = Depends(security)
):
    verifica_login(credentials)

    updates = []
    values = []

    if payload.lead_status is not None:
        updates.append("lead_status=%s")
        values.append(payload.lead_status)
    if payload.note_internal is not None:
        updates.append("note_internal=%s")
        values.append(payload.note_internal)

    if not updates:
        return {"ok": True}

    values.append(stima_id)

    conn = get_connection(); cur = conn.cursor()
    cur.execute(f"""
        UPDATE stime SET {",".join(updates)} WHERE id=%s
    """, tuple(values))
    conn.commit()
    cur.close(); conn.close()

    return {"ok": True}

# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------




if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
