# --- IN CIMA ---
import os
BASE_DIR = os.path.dirname(__file__)
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

import psycopg2
from dotenv import load_dotenv
import yagmail
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pathlib import Path


# Carica variabili ambiente
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
# 👇 DA AGGIUNGERE in database.py (sotto get_connection)

def crea_tabella_zone_valori():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS zone_valori (
            id SERIAL PRIMARY KEY,
            comune VARCHAR(100) NOT NULL,
            microzona VARCHAR(100) NOT NULL,
            prezzo_mq_base NUMERIC(10,2) NOT NULL,
            CONSTRAINT zone_valori_unq UNIQUE (comune, microzona)
        );
        CREATE INDEX IF NOT EXISTS idx_zone_valori_cm ON zone_valori(comune, microzona);
    """)
    # seed ufficiale €/mq (upsert)
    cur.executemany("""
        INSERT INTO zone_valori (comune, microzona, prezzo_mq_base)
        VALUES (%s,%s,%s)
        ON CONFLICT (comune, microzona) DO UPDATE
        SET prezzo_mq_base = EXCLUDED.prezzo_mq_base
    """, [
        ("Alba Adriatica","Nord",1500), ("Alba Adriatica","Villa Fiore",1900), ("Alba Adriatica","Zona Basciani",1400),
        ("Tortoreto","Lido Sud",1900), ("Tortoreto","Lido Centro",2200), ("Tortoreto","Lido Nord",2000), ("Tortoreto","Alto",1300),
        ("Martinsicuro","Centro",1600), ("Martinsicuro","Villarosa",1500), ("Martinsicuro","Alta",1300),
    ])
    conn.commit()
    cur.close(); conn.close()

def migrazione_allinea_stime():
    """Aggiunge le colonne nuove se mancanti (safe)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE stime
          ADD COLUMN IF NOT EXISTS microzona       VARCHAR(100),
          ADD COLUMN IF NOT EXISTS fascia_mare     VARCHAR(32),
          ADD COLUMN IF NOT EXISTS prezzo_mq_base  NUMERIC(10,2),
          ADD COLUMN IF NOT EXISTS token           UUID,
          ADD COLUMN IF NOT EXISTS token_expires   TIMESTAMPTZ;
        CREATE INDEX IF NOT EXISTS idx_stime_token ON stime(token);
    """)
    conn.commit()
    cur.close(); conn.close()
def migrazione_gestionale_stime():
    """Aggiunge campi per gestione interna lead (safe)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE stime
          ADD COLUMN IF NOT EXISTS lead_status   VARCHAR(32) DEFAULT 'nuovo',
          ADD COLUMN IF NOT EXISTS note_internal TEXT;
        
        CREATE INDEX IF NOT EXISTS idx_stime_data ON stime(data);
    """)
    conn.commit()
    cur.close()
    conn.close()

# ------------------- CREAZIONE TABELLE -------------------
def crea_tabella_stime():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stime (
            id SERIAL PRIMARY KEY,
            comune VARCHAR(100),
            via VARCHAR(100),
            civico VARCHAR(20),
            tipologia VARCHAR(50),
            mq INTEGER,
            piano VARCHAR(30),
            locali INTEGER,
            bagni INTEGER,
            pertinenze VARCHAR(100),
            ascensore VARCHAR(10),
            nome VARCHAR(50),
            cognome VARCHAR(50),
            email VARCHAR(100),
            telefono VARCHAR(30),
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def crea_tabella_stime_dettagliate():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stime_dettagliate (
            id SERIAL PRIMARY KEY,
            stima_id INTEGER REFERENCES stime(id),
            stato VARCHAR(40),
            anno INTEGER,
            classe VARCHAR(8),
            riscaldamento VARCHAR(32),
            condizionatore VARCHAR(8),
            spese_cond INTEGER,
            balcone VARCHAR(24),
            giardino VARCHAR(24),
            posto_auto VARCHAR(24),
            esposizione VARCHAR(16),
            arredo VARCHAR(32),
            note TEXT,
            contatto VARCHAR(8),
            sopralluogo TIMESTAMP,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

# ------------------- INVIO EMAIL (HTML, NIENTE CTA AUTO) -------------------
def invia_mail(destinatario, oggetto, corpo_html, allegato=None):
    """
    Invia una mail HTML. NON aggiunge link automaticamente.
    Se vuoi il pulsante/CTA '📄 Scarica il tuo PDF', inseriscilo tu nel corpo_html.
    """
    try:
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "465"))  # default 465 se non presente
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")

        print(f"📧 Invio email HTML tramite {smtp_host}:{smtp_port} a {destinatario}...")

        # ✅ Connessione server SMTP (SSL)
        yag = yagmail.SMTP(user=smtp_user, password=smtp_pass,
                           host=smtp_host, port=smtp_port, smtp_ssl=True)

        # 📎 Allegato fisico (accetta: 'stima_59.pdf' | 'reports/stima_59.pdf' | path assoluto)
        attachments = None
        if allegato:
            p = Path(str(allegato))
            if not p.is_absolute():
                if p.parts and p.parts[0] == "reports":
                    p = Path(BASE_DIR) / p
                else:
                    p = Path(REPORTS_DIR) / p.name
            if p.exists() and p.stat().st_size > 0:
                attachments = [str(p)]
            else:
                print(f"⚠️ Allegato non trovato o vuoto: {p}")

        # ✅ Invio come HTML + eventuale allegato
        yag.send(
            to=destinatario,
            subject=oggetto,
            contents=[yagmail.raw(corpo_html)],
            attachments=attachments
        )

        print("✅ Email inviata con successo (HTML + allegato se presente)")
    except Exception as e:
        print("❌ Errore invio mail:", e)


# ------------------- OTTIENI STIMA (JOIN) -------------------
def ottieni_stima_completa(stima_id):
    """
    Recupera tutti i dati di una stima (base + dettagli) tramite ID.
    Ritorna un dizionario o None se non trovato.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT s.id, s.comune, s.via, s.civico, s.tipologia, s.mq, s.piano, 
               s.locali, s.bagni, s.pertinenze, s.ascensore,
               s.nome, s.cognome, s.email, s.telefono, s.data,
               d.stato, d.anno, d.classe, d.riscaldamento, d.condizionatore,
               d.spese_cond, d.balcone, d.giardino, d.posto_auto, d.esposizione,
               d.arredo, d.note, d.contatto, d.sopralluogo
        FROM stime s
        LEFT JOIN stime_dettagliate d ON s.id = d.stima_id
        WHERE s.id = %s
    """, (stima_id,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None

    # 🔹 Converte in dizionario
    colonne = [desc[0] for desc in cur.description]
    return dict(zip(colonne, row))

if __name__ == "__main__":
    crea_tabella_stime()
    crea_tabella_stime_dettagliate()
    crea_tabella_zone_valori()
    migrazione_allinea_stime()
    migrazione_gestionale_stime()   # 👈 NUOVO
def migrazione_gestionale_stime():
    """Aggiunge campi per gestione lead se mancano."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE stime
          ADD COLUMN IF NOT EXISTS lead_status   VARCHAR(32) DEFAULT 'nuovo',
          ADD COLUMN IF NOT EXISTS note_internal TEXT
    """)
    conn.commit()
    cur.close()
    conn.close()
if __name__ == "__main__":
    crea_tabella_stime()
    crea_tabella_stime_dettagliate()
    # altre funzioni che hai già...
    migrazione_allinea_stime()
    migrazione_gestionale_stime()  # 👈 QUESTA
