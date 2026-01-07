# backend/main.py — Stima360 (o4-mini-high)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import uuid
from datetime import datetime, date, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from datetime import datetime, date, timedelta
from fastapi import Depends
import psycopg2 
from pathlib import Path
from traceback import format_exc
from typing import Optional
import os, secrets, uvicorn
import requests
from fastapi import Form
from database import get_connection, invia_mail
from pdf_report import genera_pdf_stima
from cover_pdf import genera_cover_pdf
# --- regole di stima esatte ---
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from valuation import compute_from_payload
from pydantic import BaseModel
from whatsapp import send_template_stima

# ---------------- PATH & CONFIG ----------------
BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
# ---------------- WHATSAPP CLOUD API ----------------
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")          # token di accesso Meta
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")    # phone_number_id di WhatsApp Business
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v18.0")

def normalizza_numero_whatsapp(raw: str | None) -> str | None:
    """
    Pulizia veloce:
    - tiene solo le cifre
    - se manca il prefisso internazionale aggiunge +39 (Italia)
    """
    if not raw:
        return None
    s = "".join(ch for ch in str(raw) if ch.isdigit())
    if not s:
        return None

    # se già inizia con 39 la teniamo così
    if s.startswith("39") and len(s) > 2:
        return s

    # togli eventuale 0 iniziale e aggiungi prefisso Italia
    s = s.lstrip("0")
    return "39" + s if s else None



def web_to_fs(web_path: str) -> str:
    """
    Converte 'reports/xxx.pdf' o '/reports/xxx.pdf' in path assoluto su disco.
    """
    name = web_path.replace("\\", "/").split("/")[-1]
    return str((REPORTS_DIR / name).resolve())

app = FastAPI()

# ---------------- CORS ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- STATIC & REPORTS (serve frontend + PDF) ----------------
# /reports = OBBLIGATORIO (per aprire PDF dal browser)
os.makedirs(REPORTS_DIR, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# /static = OPZIONALE (serve la cartella frontend se esiste)
frontend_dir = (BASE_DIR / "../frontend").resolve()
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

# ---------------- LOGIN ----------------
security = HTTPBasic()
def verifica_login(credentials: HTTPBasicCredentials):
    user_env = os.getenv("ADMIN_USER", "admin")
    pass_env = os.getenv("ADMIN_PASS", "password")
    if not (secrets.compare_digest(credentials.username, user_env) and
            secrets.compare_digest(credentials.password, pass_env)):
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    return True

# ---------------- MOTORE STIMA (correttivi Anno/Stato) ----------------
COEFF_STATO = {
    "nuovo": 1.08,
    "ristrutturato": 1.04,
    "buono": 1.00,
    "scarso": 0.92,
    "grezzo": 0.85
}

def coeff_anno(anno: Optional[int]) -> float:
    if not anno:
        return 1.00
    if anno >= 2015: return 1.06
    if anno >= 2005: return 1.03
    if anno >= 1995: return 1.01
    if anno >= 1980: return 0.97
    return 0.93
def _compute_fascia_mare(posizione: str | None, distanza: str | None, barriera: str | None) -> str | None:
    """
    Converte i 3 campi dell'index in una fascia_mare standard:
    prima_fila | entro_300m | 300_800m | oltre_800m | collina (non usata qui)
    Se c'è barriera (ferrovia/strada) declassa di una fascia.
    """
    pos = (posizione or "").lower().strip()
    dist = (distanza or "").lower().strip()
    bar = (barriera or "").lower().strip()

    # 1) base dalla posizione
    if pos == "frontemare":
        fascia = "prima_fila"
    elif pos == "seconda":
        fascia = "entro_300m"
    elif pos == "oltre":
        # decide dalla distanza
        if dist in {"0-100", "100-300"}:
            fascia = "entro_300m"
        elif dist in {"300-500"}:
            fascia = "300_800m"
        elif dist in {"500-1000", ">1000"}:
            fascia = "oltre_800m"
        else:
            fascia = "oltre_800m"
    else:
        fascia = None

    if not fascia:
        return None

    # 2) declassa se barriera = sì
    ordine = ["prima_fila", "entro_300m", "300_800m", "oltre_800m"]
    if bar == "si":
        try:
            i = ordine.index(fascia)
            fascia = ordine[min(i + 1, len(ordine) - 1)]
        except ValueError:
            pass

    return fascia

def calcola_stima(dati: dict) -> dict:
    """
    dati attesi: mq, prezzo_mq_base (opzionale), anno, stato
    """
    mq = float(dati.get("mq", 0) or 0)
    prezzo_mq_base = float(dati.get("prezzo_mq_base", 0) or 0)

    raw_anno = dati.get("anno", None)
    try:
        anno = int(raw_anno) if raw_anno not in (None, "", "null") else None
    except:
        anno = None

    stato = (dati.get("stato") or "buono").lower()
    k = COEFF_STATO.get(stato, 1.0) * coeff_anno(anno)
        # 🔹 Correttivo fascia mare (se presente)
    FASCIA_MARE = {
        "prima_fila": 1.10,
        "entro_300m": 1.06,
        "300_800m": 1.02,
        "oltre_800m": 0.98,
        "collina": 0.95,
    }

    fascia = (dati.get("fascia_mare") or "").lower()
    k *= FASCIA_MARE.get(fascia, 1.0)


    base = mq * prezzo_mq_base
    valore = base * k

    return {
        "mq": mq,
        "prezzo_mq_base": prezzo_mq_base,
        "anno": anno,
        "stato": stato,
        "coeff_finale": round(k, 3),
        "valore_stimato": round(valore, 2)
    }

# ---------------- UTIL ----------------
def to_bool(val):
    if isinstance(val, bool):
        return val
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in {"true","1","si","sì","yes","y"}:
        return True
    if s in {"false","0","no","n"}:
        return False
    return None  # valore sconosciuto

def format_indirizzo(via: str | None, civico: str | int | None, comune: str | None) -> str:
    via_civ = " ".join(p for p in [via or "", str(civico or "").strip()] if p).strip()
    parts = [p for p in [via_civ if via_civ else None, comune] if p]
    return ", ".join(parts)

class StimaIn(BaseModel):
    comune: str | None = None
    microzona: str | None = None 
    fascia_mare: str | None = None         # 👈 nuovo (già presente)
    via: str | None = None
    civico: str | None = None
    tipologia: str | None = None
    mq: float | None = None
    piano: str | None = None
    locali: int | None = None
    bagni: int | None = None
    pertinenze: str | None = None
    ascensore: bool | None = None
    nome: str | None = None
    cognome: str | None = None
    email: str | None = None
    telefono: str | None = None
    prezzo_mq_base: float | None = None
    anno: int | None = None
    stato: str | None = None

def normalizza_comune(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    v = val.strip().replace("_", " ").lower()
    # prima lettera maiuscola di ogni parola
    v = " ".join(w.capitalize() for w in v.split())
    # mappa sinonimi/comandi scritti male
    M = {
        "Alba Adriatica": "Alba Adriatica",
        "Martinsicuro": "Martinsicuro",
        "Tortoreto": "Tortoreto",
    }
    # se coincide con uno valido, ok; altrimenti None (così il DB non esplode)
    return M.get(v, None)

# ---------------- SALVA STIMA (accetta FORM o JSON) ----------------
def to_int(val):
    if val in (None, "", "null"): return None
    try: return int(str(val).strip())
    except: return None

def to_float(val):
    if val in (None, "", "null"): return None
    s = str(val).replace(",", ".").strip()
    try: return float(s)
    except: return None

def parse_locali(val):
    if val is None: return None
    s = str(val).strip().lower()
    if s.isdigit(): return int(s)
    M = {
        "monolocale": 1, "mono": 1,
        "bilocale": 2, "bi": 2,
        "trilocale": 3, "tri": 3,
        "quadrilocale": 4, "quadri": 4,
        "pentalocale": 5, "cinque locali": 5
    }
    return M.get(s, None)

@app.post("/api/salva_stima")
async def salva_stima(request: Request):
    # --- 1) prendi dati, FORM o JSON ---
    try:
        ct = request.headers.get("content-type", "")
        if ct and ct.startswith("application/json"):
            raw = await request.json()
        else:
            form = await request.form()
            raw = dict(form)
    except Exception as e:
        print("❌ Lettura body:", e); raw = {}

    # --- 2) normalizza/coerci ---
    data = {
        "comune": raw.get("comune"),
        "microzona": raw.get("microzona"),
        "fascia_mare": (raw.get("fascia_mare") or "").lower().strip(),  # 👈 nuovo
        "via": raw.get("via"),
        "civico": raw.get("civico"),
        "tipologia": raw.get("tipologia"),
        "mq": to_float(raw.get("mq")),
        "piano": raw.get("piano"),
        "locali": parse_locali(raw.get("locali")),
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

    # --- Prezzo €/mq base dalla tabella zone_valori (solo se non già presente) ---
    if not data.get("prezzo_mq_base"):
        try:
            conn = get_connection(); cur = conn.cursor()
            cur.execute("""
                SELECT prezzo_mq_base
                FROM zone_valori
                WHERE comune = %s AND microzona = %s
                LIMIT 1
            """, (data.get("comune"), data.get("microzona")))
            row = cur.fetchone()
            if row:
                data["prezzo_mq_base"] = float(row[0])
            else:
                data["prezzo_mq_base"] = 0.0
        except Exception as e:
            print("⚠️ Errore lettura prezzo base:", e)
            data["prezzo_mq_base"] = 0.0
        finally:
            try: cur.close(); conn.close()
            except: pass
    print("💶 Prezzo base selezionato:", data.get("prezzo_mq_base"))

    try:
        # --- 3) INSERT DB ---
        try:
            conn = get_connection(); cur = conn.cursor()
            comune_db = normalizza_comune(data.get('comune'))
            cur.execute("""
             INSERT INTO stime
              (comune, microzona, fascia_mare, via, civico, tipologia, mq, piano, locali, bagni, pertinenze, ascensore, nome, cognome, email, telefono)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id
            """, (
            comune_db,
            data.get('microzona'),
            data.get('fascia_mare'),   # 👈 nuovo
            data.get('via'), data.get('civico'), data.get('tipologia'),
            data.get('mq'), data.get('piano'), data.get('locali'), data.get('bagni'),
            data.get('pertinenze'), data.get('ascensore'),
            data.get('nome'), data.get('cognome'), data.get('email'), data.get('telefono')
            ))

            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            print("❌ ERRORE INSERT:", e); print(format_exc())
            raise HTTPException(status_code=500, detail=f"Errore INSERT DB: {e}")
        finally:
            try: cur.close(); conn.close()
            except: pass

                # --- 4) TOKEN + prezzo_mq_base (update in un colpo solo) ---
        token = None
        try:
            token = str(uuid.uuid4())
            expires = datetime.now(timezone.utc) + timedelta(days=7)

            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE stime
                SET token = %s,
                    token_expires = %s,
                    prezzo_mq_base = %s
                WHERE id = %s
            """, (
                token,
                expires,
                data.get("prezzo_mq_base"),
                new_id
            ))
            conn.commit()

        except Exception as e:
            print("❌ ERRORE UPDATE TOKEN/PREZZO_MQ_BASE:", e)
            print(format_exc())
            token = None

        finally:
            try:
                cur.close()
                conn.close()
            except:
                pass
        
        # --- 5) PDF (cover + report) ---
        indirizzo = format_indirizzo(data.get('via'), data.get('civico'), data.get('comune'))
        risultato = calcola_stima({
    "mq": data.get("mq"),
    "prezzo_mq_base": data.get("prezzo_mq_base") or 0,
    "anno": data.get("anno"),
    "stato": data.get("stato"),
    "fascia_mare": data.get("fascia_mare")  # 👈 aggiunto
})
        # === Calcolo "ufficiale" con regole complete ===
        payload_rules = {
            "comune":       data.get("comune"),
            "microzona":    data.get("microzona"),
            "tipologia":    data.get("tipologia"),
            "mq":           data.get("mq"),
            "piano":        data.get("piano"),
            "locali":       data.get("locali"),
            "bagni":        data.get("bagni"),
            # valuation riconosce "Sì/No/true/1": per sicurezza passo 'Sì'/'No'
            "ascensore":    ("Sì" if data.get("ascensore") else "No"),
            "anno":         data.get("anno"),
            "stato":        data.get("stato"),
            # fattore mare (dal form arrivano in raw)
            "posizioneMare": raw.get("posizioneMare"),
            "distanzaMare":  raw.get("distanzaMare"),
            "barrieraMare":  raw.get("barrieraMare"),
            "vistaMare":     raw.get("vistaMare"),
            # pertinenze + metrature opzionali
            "pertinenze":   data.get("pertinenze") or "",
            "mqGiardino":   raw.get("mqGiardino"),
            "mqGarage":     raw.get("mqGarage"),
        }

        calc = compute_from_payload(payload_rules)
        price_exact       = calc["price_exact"]
        eur_mq_finale     = calc["eur_mq_finale"]
        valore_pertinenze = calc["valore_pertinenze"]
        base_mq           = calc["base_mq"]

        dati_pdf = {
    "id_stima": new_id,
    "indirizzo": indirizzo,
    "comune": data.get("comune"),
    "microzona": data.get("microzona"),
    "fascia_mare": data.get("fascia_mare"),
    "via": data.get("via"),
    "civico": data.get("civico"),
    "tipologia": data.get("tipologia"),
    "mq": data.get("mq"),
    "piano": data.get("piano"),
    "locali": data.get("locali"),
    "bagni": data.get("bagni"),
    "ascensore": data.get("ascensore"),
    "pertinenze": data.get("pertinenze"),

    # 🔴 usa SEMPRE i numeri del modello completo:
    "stima": f"{price_exact:,.0f} €".replace(",", "."),
    "price_exact": price_exact,
    "eur_mq_finale": eur_mq_finale,
    "valore_pertinenze": valore_pertinenze,
    "base_mq": base_mq,

    # (se vuoi tenerli per retrocompatibilità, ok,
    #  ma non farli usare dal template per le cifre principali)
    "anno": risultato["anno"],
    "stato": risultato["stato"],
    "correttivo": risultato["coeff_finale"],
    "coefficiente": risultato["coeff_finale"],
    "valore_stimato": risultato["valore_stimato"],
    "prezzo_mq_base": data.get("prezzo_mq_base"),
        # 👉 aggiungi anche i dati mare per il PDF (così non cambia la stima)
    "posizioneMare": raw.get("posizioneMare"),
    "distanzaMare":  raw.get("distanzaMare"),
    "barrieraMare":  raw.get("barrieraMare"),
    "vistaMare":     raw.get("vistaMare"),
    "mqGiardino":    raw.get("mqGiardino"),
    "mqGarage":      raw.get("mqGarage"),
    "ascensore":     ("Sì" if data.get("ascensore") else "No"),

}


        try:
            cover_web_path = genera_cover_pdf(f"cover_stima360_{new_id}.pdf")
        except Exception as e:
            print("❌ ERRORE COVER:", e); print(format_exc())
            raise HTTPException(status_code=500, detail=f"Errore generazione COVER: {e}")

        try:
            pdf_web_path = genera_pdf_stima(dati_pdf, nome_file=f"stima_{new_id}.pdf")
        except Exception as e:
            print("❌ ERRORE REPORT:", e); print(format_exc())
            raise HTTPException(status_code=500, detail=f"Errore generazione REPORT: {e}")
        # --- 6) EMAIL + WHATSAPP TEMPLATE META ---
        try:
            pdf_link = f"{PUBLIC_BASE_URL}/{pdf_web_path.lstrip('/')}"
            det_link = (
                f"{PUBLIC_BASE_URL}/static/dati_personali.html?t={token}"
                if token else None
            )

            corpo_html = f"""
            <h2 style="color:#0077cc;">🏡 La tua stima Stima360 è pronta!</h2>
            <p>Ciao <b>{data.get('nome','')}</b>,</p>
            <p>Ecco la stima per <b>{indirizzo}</b>.</p>
            <p>📄 <a href="{pdf_link}">Apri il PDF della tua stima</a></p>
            {f'<p>📋 <a href="{det_link}">Richiedi la stima dettagliata</a></p>' if det_link else ''}
            <p>Grazie,<br><b>Team Stima360</b></p>
            """

            # Invia EMAIL
            invia_mail(
                data.get("email"),
                f"La tua stima Stima360 – {indirizzo}",
                corpo_html,
                allegato=None
            )
            # 📩 COPIA INTERNA A STIMA360 (ADMIN)
            invia_mail(
                "info@stima360.it",
                f"📩 Nuova stima ricevuta – {indirizzo}",
                corpo_html,
                allegato=None
            )

            # Invia WHATSAPP TEMPLATE META
            try:
                send_template_stima(
                    data.get("telefono"),
                    indirizzo,
                    pdf_link
                )
            except Exception as e_wp:
                print("⚠️ Errore invio WhatsApp template:", e_wp)

        except Exception as e:
            print("❌ ERRORE EMAIL/WHATSAPP:", e)
            print(format_exc())
            return {
                "success": True,
                "status": "ok",
                "id": new_id,
                "pdf_url": f"/{pdf_web_path}",
                "cover_url": f"/{cover_web_path}",
                "price_exact": price_exact,
                "eur_mq_finale": eur_mq_finale,
                "valore_pertinenze": valore_pertinenze,
                "base_mq": base_mq,
                "warning": f"Invio email/WhatsApp fallito: {e}",
            }

                # --- 7) OK ---
        return {
            "success": True,
            "status": "ok",
            "id": new_id,
            "pdf_url": f"/{pdf_web_path}",
            "cover_url": f"/{cover_web_path}",
            "price_exact": price_exact,
            "eur_mq_finale": eur_mq_finale,
            "valore_pertinenze": valore_pertinenze,
            "base_mq": base_mq,
            "mq_calcolati": calc.get("mq_calcolati"),
        }

    except HTTPException:
        raise
    except Exception as e:
        print("❌ ERRORE GENERICO salva_stima:", e); print(format_exc())
        raise HTTPException(status_code=500, detail=f"Errore salvataggio dati: {e}")

# --- PREFILL DA TOKEN ---
@app.get("/api/prefill")
async def prefill(t: str):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            SELECT id, nome, cognome, email, telefono, comune, microzona, via, civico, tipologia, mq, piano, locali, bagni, pertinenze, ascensore
            FROM stime
            WHERE token = %s AND (token_expires IS NULL OR token_expires > NOW())
            LIMIT 1
        """, (t,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Token non valido o scaduto")

        keys = ["id","nome","cognome","email","telefono","comune","microzona","via","civico","tipologia","mq","piano","locali","bagni","pertinenze","ascensore"]
        return {k: v for k, v in zip(keys, row)}
    except HTTPException:
        raise
    except Exception as e:
        print("❌ PREFILL:", e); 
        raise HTTPException(status_code=500, detail="Errore prefill")

# ---------------- SALVA STIMA DETTAGLIATA ----------------
@app.post("/api/salva_stima_dettagliata")
async def salva_stima_dettagliata(request: Request):
    data = await request.json()
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO stime_dettagliate (
                stima_id, comune, via, civico, tipologia, mq, piano, locali, bagni, ascensore, pertinenze,
                stato, anno, classe, riscaldamento, condizionatore,
                spese_cond, balcone, giardino, posto_auto, esposizione,
                arredo, note, contatto, sopralluogo
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data.get('stima_id'), data.get('comune'), data.get('via'), data.get('civico'),
            data.get('tipologia'), data.get('mq'), data.get('piano'), data.get('locali'),
            to_bool(data.get('bagni')), to_bool(data.get('ascensore')), data.get('pertinenze'),
            data.get('stato'), data.get('anno'), data.get('classe'),
            data.get('riscaldamento'), data.get('condizionatore'), data.get('spese_cond'),
            data.get('balcone'), data.get('giardino'), data.get('posto_auto'),
            data.get('esposizione'), data.get('arredo'), data.get('note'),
            data.get('contatto'), data.get('sopralluogo')
        ))
        conn.commit()
        cur.close(); conn.close()

        # opzionale: calcolo correttivi anche qui
        risultato = calcola_stima(data) if ("mq" in data and "prezzo_mq_base" in data) else {
            "coeff_finale": 1.0, "valore_stimato": None, "anno": data.get("anno"), "stato": data.get("stato")
        }

        # genera PDF dettagliato
        data_pdf = dict(data)
        data_pdf.update({
            "correttivo": risultato.get("coeff_finale"),
            "valore_stimato": risultato.get("valore_stimato"),
        })
        pdf_path = genera_pdf_stima(data_pdf, nome_file=f"stima_dettagliata_{data.get('stima_id','new')}.pdf")

        corpo_html = f"""
        <h2 style="color:#0077cc;">🏡 Stima360 – Valutazione completa pronta!</h2>
        <p>Ciao <b>{data.get('nome','')}</b>,</p>
        <p>Grazie per i dettagli aggiuntivi dell'immobile in <b>{data.get('indirizzo','')}</b>.</p>
        <p>⚙️ Correttivo stato/anno: × {risultato.get('coeff_finale')}</p>
        <p>📄 In allegato trovi il PDF con la stima completa.</p>
        <p><b>Il Team Stima360</b></p>
        """

        invia_mail(
            data.get('email'),
            "🏡 Stima360 – La tua stima completa è pronta!",
            corpo_html,
            allegato=web_to_fs(pdf_path)
        )

        return {"status": "ok", "pdf": f"/{pdf_path}"}
    except Exception as e:
        print("Errore stima dettagliata:", e)
        raise HTTPException(status_code=500, detail="Errore salvataggio dati dettagliati")

# ---------------- GENERA PDF (stima base) ----------------
@app.post("/api/genera_pdf")
async def genera_pdf(request: Request):
    data = await request.json()
    try:
        file_pdf = genera_pdf_stima(data, nome_file=f"stima_{data.get('id','new')}.pdf")
        invia_mail(
            data.get('email'),
            "📄 Stima360 – PDF della tua stima",
            "<p>Ciao! In allegato trovi il PDF con la tua valutazione.</p>",
            allegato=web_to_fs(file_pdf)
        )
        return {"success": True, "pdf": f"/{file_pdf}"}
    except Exception as e:
        print("Errore PDF:", e)
        raise HTTPException(status_code=500, detail="Errore generazione PDF")

# ---------------- TEST PDF (comodo per provare subito) ----------------
@app.get("/api/test_pdf")
async def test_pdf():
    try:
        dati_demo = {
            "comune": "Alba Adriatica",
            "microzona": "Villa Fiore",        # 👈 demo
            "via": "Via Roma",
            "civico": "12",
            "tipologia": "Appartamento",
            "mq": 85,
            "piano": "2",
            "locali": 3,
            "bagni": 1,
            "ascensore": True,
            "pertinenze": "Garage",
            "stima": "150.000 – 170.000 €",
            # demo correttivi
            "anno": 2008,
            "stato": "ristrutturato",
            "prezzo_mq_base": 1900
        }
        file_pdf = genera_pdf_stima(dati_demo, nome_file="stima360_test.pdf")  # "reports/.."
        return {"ok": True, "url": f"/{file_pdf}"}
    except Exception as e:
        print("Errore test PDF:", e)
        raise HTTPException(status_code=500, detail="Errore test PDF")
    
@app.get("/stima_dettagliata.html")
async def stima_dettagliata_alias(request: Request):
    qs = request.url.query
    target = "/static/dati_personali.html" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=307)

# ---------------- RUN ----------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
from typing import List, Optional
from datetime import datetime, date, timedelta
from fastapi import Depends
import psycopg2

# ... qui hai già security/verifica_login/ get_connection ecc.

def get_connection():
    from database import get_connection as _gc
    return _gc()

@app.get("/api/admin/stime")
def admin_lista_stime(
    day: Optional[str] = "oggi",    # "oggi", "ieri" oppure range con dal/al
    dal: Optional[date] = None,
    al: Optional[date] = None,
    credentials: HTTPBasicCredentials = Depends(security)
):
    verifica_login(credentials)

    # calcolo intervallo date
    if dal and al:
        start = datetime.combine(dal, datetime.min.time())
        end   = datetime.combine(al + timedelta(days=1), datetime.min.time())
    else:
        oggi = date.today()
        if day == "ieri":
            base = oggi - timedelta(days=1)
        else:
            base = oggi
        start = datetime.combine(base, datetime.min.time())
        end   = datetime.combine(base + timedelta(days=1), datetime.min.time())

    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT
          s.id,
          s.data,
          s.comune,
          s.microzona,
          s.via,
          s.civico,
          s.tipologia,
          s.mq,
          s.piano,
          s.locali,
          s.bagni,
          s.pertinenze,
          s.ascensore,
          s.nome,
          s.cognome,
          s.email,
          s.telefono,
          s.lead_status,
          s.note_internal,
          sd.stato        AS stato_dettaglio,
          sd.data         AS data_dettaglio
        FROM stime s
        LEFT JOIN stime_dettagliate sd
          ON sd.stima_id = s.id
        WHERE s.data >= %s AND s.data < %s
        ORDER BY s.data DESC
    """, (start, end))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    cur.close(); conn.close()

    results = [dict(zip(cols, r)) for r in rows]
    return {"items": results, "from": start, "to": end}
class LeadUpdate(BaseModel):
    lead_status: Optional[str] = None
    note_internal: Optional[str] = None

@app.post("/api/admin/stime/{stima_id}/update")
def admin_update_stima(
    stima_id: int,
    payload: LeadUpdate,
    credentials: HTTPBasicCredentials = Depends(security)
):
    verifica_login(credentials)

    fields = []
    values = []
    if payload.lead_status is not None:
        fields.append("lead_status = %s")
        values.append(payload.lead_status)
    if payload.note_internal is not None:
        fields.append("note_internal = %s")
        values.append(payload.note_internal)

    if not fields:
        return {"ok": True}  # niente da aggiornare

    values.append(stima_id)

    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(f"""
        UPDATE stime
        SET {", ".join(fields)}
        WHERE id = %s
    """, tuple(values))
    conn.commit()
    cur.close(); conn.close()

    return {"ok": True}
