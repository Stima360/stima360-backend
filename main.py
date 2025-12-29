# backend/main.py — versione ripulita Stima360

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import os, uvicorn, secrets, uuid, requests
from valuation_base import compute_base_from_payload 
from database import get_connection, invia_mail
from pdf_report import genera_pdf_stima
from valuation import compute_from_payload
from valuation import BASE_MQ
from urllib.parse import urlencode
# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
BASE_DIR = Path(__file__).parent
REPORTS_DIR = Path("/var/tmp/reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://stima360-backend.onrender.com")
WHATSAPP_SERVICE_URL = os.getenv("WHATSAPP_SERVICE_URL", "https://stima360-whatsapp-webhook-test.onrender.com/send")

# ---------------------------------------------------------
# APP & CORS
# ---------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://stima360.it",
        "https://www.stima360.it"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    
def invia_whatsapp(numero: str | None, p1: str, p2: str, p3: str, p4: str):
    dest = normalizza_numero_whatsapp(numero)
    if not dest:
        return
    try:
        r = requests.post(
            WHATSAPP_SERVICE_URL,
            json={
                "to": dest,
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4
            },
            timeout=10
        )
        if r.status_code >= 300:
            print("WA ERROR:", r.status_code, r.text)
    except Exception as e:
        print("WA EXC:", e)





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
# CANCELLA STIME (singole o multiple)
# ---------------------------------------------------------
class DeleteRequest(BaseModel):
    ids: list[int]

@app.post("/api/admin/stime/delete")
def admin_delete_stime(payload: DeleteRequest):

    ids = payload.ids
    if not ids:
        raise HTTPException(status_code=400, detail="Nessun ID ricevuto")

    conn = get_connection(); cur = conn.cursor()

    cur.execute("DELETE FROM stime_dettagliate WHERE stima_id = ANY(%s)", (ids,))
    cur.execute("DELETE FROM stime WHERE id = ANY(%s)", (ids,))
    conn.commit()

    cur.close(); conn.close()
    return {"ok": True, "deleted": len(ids)}
# ---------------------------------------------------------
# CANCELLA STIME DETTAGLIATE 
# ---------------------------------------------------------
@app.post("/api/admin/stime_dettagliate/delete")
def admin_delete_stime_dettagliate(payload: DeleteRequest):

    ids = payload.ids
    if not ids:
        raise HTTPException(status_code=400, detail="Nessun ID ricevuto")

    conn = get_connection(); cur = conn.cursor()

    # Cancella ESCLUSIVAMENTE le righe della tabella stime_dettagliate
    cur.execute("DELETE FROM stime_dettagliate WHERE id = ANY(%s)", (ids,))

    conn.commit()
    cur.close(); conn.close()

    return {"ok": True, "deleted": len(ids)}
# ---------------------------------------------------------
# STIMA BASE
# ---------------------------------------------------------    
@app.post("/api/stima_base")
async def stima_base(request: Request):
    try:
        raw = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Payload non valido")

    comune    = raw.get("comune")
    microzona = raw.get("microzona")
    mq        = raw.get("mq")
    anno      = raw.get("anno")
    tipologia = raw.get("tipologia")   # ✅

    if not comune or not microzona or not mq or not anno:
        raise HTTPException(status_code=400, detail="Dati mancanti")

    try:
        mq   = float(mq)
        anno = int(anno)
    except:
        raise HTTPException(status_code=400, detail="MQ o anno non validi")

    result = compute_base_from_payload({
        "comune": comune,
        "microzona": microzona,
        "tipologia": tipologia,   # ✅
        "mq": mq,
        "anno": anno,
    })

    return {
        "success": True,
        "comune": comune,
        "microzona": microzona,
        "mq": result["mq"],
        "anno": anno,
        "base_mq": result["base_mq"],
        "eur_mq_base": result["eur_mq_base"],
        "eur_mq_visuale": result["eur_mq_visuale"],
        "valore_riferimento": result["price_base"],
        
    }


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
    # --------------------------
    # CONSENSO MARKETING (GDPR)
    # --------------------------
    consenso_marketing = bool(raw.get("consenso_marketing", False))
    consenso_marketing_at = datetime.now(timezone.utc) if consenso_marketing else None
        
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

        # Campi extra dal frontend
        "posizioneMare": raw.get("posizioneMare"),
        "distanzaMare": raw.get("distanzaMare"),
        "barrieraMare": raw.get("barrieraMare"),
        "vistaMareYN": raw.get("vistaMareYN"),
        "vistaMare": raw.get("vistaMare"),
        "vistaMareDettaglio": raw.get("vistaMareDettaglio"),
        "mqGiardino": raw.get("mqGiardino"),
        "mqGarage": raw.get("mqGarage"),
        "mqCantina": raw.get("mqCantina"),
        "mqPostoAuto": raw.get("mqPostoAuto"),
        "mqTaverna": raw.get("mqTaverna"),
        "mqSoffitta": raw.get("mqSoffitta"),
        "mqTerrazzo": raw.get("mqTerrazzo"),
        "numBalconi": raw.get("numBalconi"),
        "altroDescrizione": raw.get("altroDescrizione"),
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
              bagni, pertinenze, ascensore, nome, cognome, email, telefono,
              consenso_marketing, consenso_marketing_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id
        """, (
            comune_db, data["microzona"], data["fascia_mare"],
            data["via"], data["civico"], data["tipologia"],
            data["mq"], data["piano"], data["locali"], data["bagni"],
            data["pertinenze"], data["ascensore"],
            data["nome"], data["cognome"], data["email"], data["telefono"],
            consenso_marketing,
            consenso_marketing_at
        ))
        new_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore INSERT DB: {e}")
    finally:
        try: cur.close(); conn.close()
        except: pass

    # --- 5. TOKEN e prezzo base ---
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""
    UPDATE stime SET
      anno=%s,
      stato=%s,
    
      posizionemare=%s,
      distanzamare=%s,
      barrieramare=%s,
    
      vistamareyn=%s,
      vistamaredettaglio=%s,
      vistamare=%s,
    
      mqgiardino=%s,
      mqgarage=%s,
      mqcantina=%s,
      mqpostoauto=%s,
      mqtaverna=%s,
      mqsoffitta=%s,
      mqterrazzo=%s,
      numbalconi=%s,
    
      altrodescrizione=%s
    WHERE id=%s
    """, (
      data["anno"],
      data["stato"],
    
      data["posizioneMare"],
      data["distanzaMare"],
      data["barrieraMare"],
    
      data["vistaMareYN"],
      data["vistaMareDettaglio"],
      data["vistaMare"],
    
      to_int(data["mqGiardino"]),
      to_int(data["mqGarage"]),
      to_int(data["mqCantina"]),
      to_int(data["mqPostoAuto"]),
      to_int(data["mqTaverna"]),
      to_int(data["mqSoffitta"]),
      to_int(data["mqTerrazzo"]),
      to_int(data["numBalconi"]),
    
      data["altroDescrizione"],
      new_id
    ))
    conn.commit()
    cur.close(); conn.close()
    
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
    link_token = f"https://www.stima360.it/stima_dettagliata.html?token={token}"
      # --- 6. Stima completa (engine ufficiale) ---
    # Usa i valori "grezzi" del form dove serve (es. locali in testo)
    locali_raw = raw.get("locali")  # es. "Trilocale" oppure "3"

    payload_rules = {
        "comune": data["comune"],
        "microzona": data["microzona"],

        "tipologia": data["tipologia"],
        "mq": data["mq"],
        "piano": data["piano"],

        # 👇 per il motore usiamo la versione raw (può essere "Trilocale")
        "locali": locali_raw if locali_raw is not None else data["locali"],
        "bagni": data["bagni"],

        # ascensore come stringa "Sì"/"No" per i coefficienti
        "ascensore": "Sì" if data["ascensore"] else "No",

        "anno": data["anno"],
        "stato": data["stato"],

        # Mare
        "posizioneMare": data["posizioneMare"],
        "distanzaMare":  data["distanzaMare"],
        "barrieraMare":  data["barrieraMare"],

        # 👇 passa TUTTI i campi vista che valuation.py sa usare
        "vistaMareYN":        data["vistaMareYN"],
        "vistaMareDettaglio": data["vistaMareDettaglio"],
        "vistaMare":          data["vistaMare"],

        # Pertinenze + mq
        "pertinenze":  data["pertinenze"] or "",
        "mqGiardino":  data["mqGiardino"],
        "mqGarage":    data["mqGarage"],
        "mqCantina":   data["mqCantina"],
        "mqPostoAuto": data["mqPostoAuto"],
        "mqTaverna":   data["mqTaverna"],
        "mqSoffitta":  data["mqSoffitta"],
        "mqTerrazzo":  data["mqTerrazzo"],
        "numBalconi":  data["numBalconi"],

        # 👇 nuovi coefficienti che abbiamo aggiunto nel motore
        "via":              data["via"],
        "altroDescrizione": data["altroDescrizione"],
    }

    calc = compute_from_payload(payload_rules)

    price_exact = calc["price_exact"]
    eur_mq_finale = calc["eur_mq_finale"]
    valore_pertinenze = calc["valore_pertinenze"]
    base_mq = calc["base_mq"]

    indirizzo = format_indirizzo(data["via"], data["civico"], data["comune"])
    
    # --- Vista mare finale per PDF ---
    vista_mare_finale = None
    if data.get("vistaMareYN") and str(data["vistaMareYN"]).lower() in {"si","sì","yes","true","1"}:
        vista_mare_finale = data.get("vistaMareDettaglio") or "Sì"

    # --- 7. PDF ---
    try:
        pdf_web_path = genera_pdf_stima({
            "id_stima": new_id,
        
            # CLIENTE
            "nome": data["nome"],
            "cognome": data["cognome"],
            "telefono": data["telefono"],
            "email": data["email"],
        
            # INDIRIZZO
            "indirizzo": indirizzo,
            "comune": data["comune"],
            "microzona": data["microzona"],
        
            # IMMOBILE
            "tipologia": data["tipologia"],
            "mq": data["mq"],
            "piano": data["piano"],
            "locali": raw.get("locali"),   # <-- TESTUALE (Trilocale)
            "bagni": data["bagni"],
            "ascensore": "Sì" if data["ascensore"] else "No",
            "anno": data["anno"],
            "stato": data["stato"],
        
            # MARE
            "posizioneMare": data["posizioneMare"],
            "distanzaMare": data["distanzaMare"],
            "barrieraMare": data["barrieraMare"],
            "vistaMare": vista_mare_finale,
        
            # PERTINENZE
            "pertinenze": data["pertinenze"],
        
            # VALORI
            "stima": f"{price_exact:,.0f} €".replace(",", "."),
            "price_exact": price_exact,
            "eur_mq_finale": eur_mq_finale,
            "valore_pertinenze": valore_pertinenze,
            "base_mq": base_mq,
        
        }, nome_file=f"stima_{new_id}.pdf")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore PDF: {e}")

    # --- 8. URL PDF finale ---
    if pdf_web_path.startswith("http"):
        pdf_url_finale = pdf_web_path
    else:
        pdf_url_finale = f"{PUBLIC_BASE_URL}/{pdf_web_path.lstrip('/')}"

    # URL intermedio con pagina "La tua stima è in arrivo..."
    loader_url = (
        "https://www.stima360.it/pdf_redirect.html?"
        + urlencode({"pdf": pdf_url_finale})
    )



    det_link = f"{PUBLIC_BASE_URL}/static/dati_personali.html?t={token}"

    # Link stima completa sul sito (usato sia in email che in WhatsApp)
    clean = {k: v for k, v in data.items() if v not in (None, "", "None")}

    # 👉 Forza "locali" testuale se arriva dal form (es. "Trilocale")
    if raw.get("locali"):
        clean["locali"] = raw.get("locali")

    # 👉 Assicura che passi anche "vistaMareDettaglio" al link
    if raw.get("vistaMareDettaglio"):
        clean["vistaMareDettaglio"] = raw.get("vistaMareDettaglio")

    url_stima_completa = (
      "https://www.stima360.it/stima_dettagliata.html?"
      + urlencode({"token": token})
    )


   
    # --- 9. Email ---
    try:
        corpo = f"""
        <div style="font-family:Arial,Helvetica,sans-serif; color:#222; line-height:1.6;">
          <h2>🏡 La tua stima Stima360 è pronta</h2>
    
          <p>Ciao <b>{data['nome']}</b>,</p>
    
          <p>
            ricevi questa email perché hai richiesto una valutazione immobiliare tramite Stima360.
          </p>
    
          <p>
            📄 <b>PDF della stima</b><br>
            <a href="{loader_url}">Apri il PDF</a>
          </p>
    
          <p>
            🔍 <b>Vuoi una valutazione professionale più approfondita?</b><br>
            Con <b>Stima Pro</b> puoi richiedere un’analisi completa e personalizzata.
            <br>
            🧩 <a href="{link_token}"><b>Richiedi Stima Pro</b></a>
          </p>
    
          <hr style="border:none; border-top:1px solid #e6e6e6; margin:18px 0;">
    
          <p style="font-size:12px; color:#666; margin:0;">
            <b>Stima360 di Giorgio Censori</b><br>
            Contatto: <a href="mailto:info@stima360.it">info@stima360.it</a>
          </p>
    
          <p style="font-size:12px; color:#666; margin:10px 0 0 0;">
            Informative:
            <a href="https://stima360.it/privacy.html">Privacy</a> ·
            <a href="https://stima360.it/termini.html">Termini e Condizioni</a> ·
            <a href="https://stima360.it/eliminazionedati.html">Eliminazione dei dati</a>
          </p>
    
          <p style="font-size:12px; color:#777; margin:10px 0 0 0;">
            Questa comunicazione è inviata esclusivamente per finalità di servizio connesse alla tua richiesta.
          </p>
        </div>
        """
    
        invia_mail(data["email"], f"Stima360 – {indirizzo}", corpo)
    
    except Exception as e:
        print("MAIL EXC:", e)
    
    
        

    # --- 10. WhatsApp ---
    try:
        invia_whatsapp(
            data["telefono"],
            data["nome"],          # p1
            indirizzo,             # p2
            loader_url,            # p3
            link_token     # p4
        )
    except Exception as e:
        print("WA EXC:", e)



    # --- 11. Risposta JSON al frontend ---
    return {
        "success": True,
        "id": new_id,
        "pdf_url": pdf_url_finale,
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
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT
              s.id,
              s.nome, s.cognome, s.email, s.telefono,
              s.comune, s.microzona, s.via, s.civico, s.tipologia,
              s.mq, s.piano, s.locali, s.bagni,
              s.pertinenze, s.ascensore,
            
              s.anno,
              s.stato,
            
              s.posizionemare,
              s.distanzamare,
              s.barrieramare,
            
              s.vistamareyn,
              s.vistamaredettaglio,
              s.vistamare,
            
              s.mqgiardino,
              s.mqgarage,
              s.mqcantina,
              s.mqpostoauto,
              s.mqtaverna,
              s.mqsoffitta,
              s.mqterrazzo,
              s.numbalconi,
            
              s.altrodescrizione
            FROM stime s
            WHERE s.token = %s
            AND (s.token_expires IS NULL OR s.token_expires > NOW())
            LIMIT 1;
        """, (t,))

        row = cur.fetchone()
        cur.close()
        conn.close()

    except Exception as e:
        print("PREFILL ERROR:", e)
        raise HTTPException(status_code=500, detail="Errore prefill")

    if not row:
        raise HTTPException(status_code=404, detail="Token non valido")

    keys = [
      "id","nome","cognome","email","telefono",
      "comune","microzona","via","civico","tipologia",
      "mq","piano","locali","bagni",
      "pertinenze","ascensore",
    
      "anno","stato",
      "posizioneMare","distanzaMare","barrieraMare",
      "vistaMareYN","vistaMareDettaglio","vistaMare",
    
      "mqGiardino","mqGarage","mqCantina","mqPostoAuto",
      "mqTaverna","mqSoffitta","mqTerrazzo","numBalconi",
    
      "altroDescrizione"
    ]




    return dict(zip(keys, row))


# ---------------------------------------------------------
# SALVA STIMA DETTAGLIATA
# ---------------------------------------------------------
@app.post("/api/salva_stima_dettagliata")
async def salva_stima_dettagliata(request: Request):

    try:
        if "application/json" in (request.headers.get("content-type") or ""):
            data = await request.json()
        else:
            data = dict(await request.form())
    except:
        raise HTTPException(status_code=400, detail="Payload non valido")

    def to_int_safe(v):
        if v in (None, "", " "):
            return None
        try:
            return int(v)
        except:
            return None

    conn = get_connection(); cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO stime_dettagliate (
                stima_id,
                nome, cognome, email, telefono,
                indirizzo, stato, anno,
                classe, riscaldamento, condizionatore, condiz_tipo, spese_cond,
                esposizione, arredo, note, contatto, sopralluogo,
                ascensore, pertinenze,
                tipologia, mq, piano, locali, bagni,
                microzona, posizionemare, distanzamare, barrieramare,
                mqgiardino, mqgarage, vistamare, altrodescrizione,
                mqcantina, mqpostoauto, mqtaverna, mqsoffitta, mqterrazzo,
                numbalconi
            )
            VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,
                %s,%s,
                %s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,%s,
                %s
            )
        """, (
            # stima_id
            to_int_safe(data.get("stima_id")),

            # anagrafica
            data.get("nome") or None,
            data.get("cognome") or None,
            data.get("email") or None,
            data.get("telefono") or None,

            # immobile base
            data.get("indirizzo") or None,
            data.get("stato") or None,
            data.get("anno") or None,  # anno è TEXT nel DB

            # impianti / classe
            data.get("classe") or None,
            data.get("riscaldamento") or None,
            data.get("condizionatore") or None,
            data.get("condiz_tipo") or None,
            to_int_safe(data.get("spese_cond")),

            data.get("esposizione") or None,
            data.get("arredo") or None,
            data.get("note") or None,
            data.get("contatto") or None,
            data.get("sopralluogo") or None,  # stringa ISO o None

            # ascensore e pertinenze (testuali)
            data.get("ascensore") or None,
            data.get("pertinenze") or None,

            # dati tecnici
            data.get("tipologia") or None,
            to_int_safe(data.get("mq")),
            data.get("piano") or None,
            to_int_safe(data.get("locali")),
            to_int_safe(data.get("bagni")),

            data.get("microzona") or None,
            data.get("posizioneMare") or data.get("posizionemare") or None,
            data.get("distanzaMare") or data.get("distanzamare") or None,
            data.get("barrieraMare") or data.get("barrieramare") or None,

            # QUI gestisco sia mqGiardino che mqgiardino
            to_int_safe(data.get("mqGiardino") or data.get("mqgiardino")),
            to_int_safe(data.get("mqGarage") or data.get("mqgarage")),
            data.get("vistaMare") or data.get("vistamare") or None,
            data.get("altroDescrizione") or data.get("altrodescrizione") or None,

            to_int_safe(data.get("mqCantina") or data.get("mqcantina")),
            to_int_safe(data.get("mqPostoAuto") or data.get("mqpostoauto")),
            to_int_safe(data.get("mqTaverna") or data.get("mqtaverna")),
            to_int_safe(data.get("mqSoffitta") or data.get("mqsoffitta")),
            to_int_safe(data.get("mqTerrazzo") or data.get("mqterrazzo")),
            to_int_safe(data.get("numBalconi") or data.get("numbalconi")),
        ))

        conn.commit()

    except Exception as e:
        # QUI, SE VUOI DEBUG SERIO:
        print("ERRORE /api/salva_stima_dettagliata:", e)
        raise HTTPException(status_code=500, detail=f"Errore INSERT: {e}")

    finally:
        try:
            cur.close()
            conn.close()
        except:
            pass

    return {"ok": True}

# ---------------------------------------------------------
# ADMIN STIME PRO
# ---------------------------------------------------------

@app.get("/api/admin/stime_pro")
def admin_lista_stime_pro(
    day: str = "oggi",
    dal: date | None = None,
    al: date | None = None
):

    if dal and al:
        start = datetime.combine(dal, datetime.min.time())
        end   = datetime.combine(al + timedelta(days=1), datetime.min.time())
    else:
        base = date.today() if day == "oggi" else date.today() - timedelta(days=1)
        start = datetime.combine(base, datetime.min.time())
        end   = datetime.combine(base + timedelta(days=1), datetime.min.time())

    conn = get_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM stime_dettagliate
        WHERE data >= %s AND data < %s
        ORDER BY data DESC
    """, (start, end))

    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]

    cur.close(); conn.close()

    return {"items": [dict(zip(cols, r)) for r in rows]}



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
):
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
               s.nome, s.cognome, s.email, s.telefono,s.consenso_marketing, s.lead_status, s.note_internal,
            sd.data AS data_dettaglio
            FROM stime s
            LEFT JOIN stime_dettagliate sd ON sd.stima_id = s.id

        WHERE s.data >= %s AND s.data < %s
        ORDER BY s.data DESC
    """, (start, end))
    rows = cur.fetchall()
    cols = [c[0] for c in cur.description]
    cur.close(); conn.close()

    return {"items": [dict(zip(cols, r)) for r in rows]}
# ---------------------------------------------------------
# UPDATE
# ---------------------------------------------------------

@app.post("/api/admin/stime/{stima_id}/update")
def admin_update_stima(stima_id: int, payload: LeadUpdate):

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
