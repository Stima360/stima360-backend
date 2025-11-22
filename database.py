# --- IN CIMA ---
import os
BASE_DIR = os.path.dirname(__file__)
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

import psycopg2
from dotenv import load_dotenv

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pathlib import Path
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

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

# ------------------- TABELLE VALORI -------------------
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
    cur.executemany("""
        INSERT INTO zone_valori (comune, microzona, prezzo_mq_base)
        VALUES (%s,%s,%s)
        ON CONFLICT (comune, microzona) DO UPDATE
        SET prezzo_mq_base = EXCLUDED.prezzo_mq_base
    """, [
    # 🌊 ALBA ADRIATICA
    ("Alba Adriatica", "Nord", 1250),
    ("Alba Adriatica", "Villa Fiore", 1350),
    ("Alba Adriatica", "Zona Basciani", 1200),

    # 🌴 TORTORETO
    # B5 Via Indipendenza ≈ min 1450
    ("Tortoreto", "Lido Sud", 1450),
    # B4 Lungomare Sirena ≈ min 1650
    ("Tortoreto", "Lido Centro", 1650),
    # fascia intermedia tra B4 e B5
    ("Tortoreto", "Lido Nord", 1500),
    # Alto ≈ 1097 → arrotondato 1100
    ("Tortoreto", "Alto", 1100),

    # 🏖️ MARTINSICURO
    ("Martinsicuro", "Centro", 1000),
    ("Martinsicuro", "Villarosa", 900),
    ("Martinsicuro", "Alta", 850),
]
)
    conn.commit()
    cur.close(); conn.close()

def migrazione_allinea_stime():
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

# ------------------- NUOVA FUNZIONE INVIA MAIL -------------------
def invia_mail(destinatario, oggetto, corpo_html, allegato=None):
    # Config SMTP Netsons
    smtp_host = os.getenv("SMTP_HOST", "mail.stima360.it")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))   # 🔥 Netsons vuole 587 STARTTLS
    smtp_user = os.getenv("SMTP_USER")               # es. info@stima360.it
    smtp_pass = os.getenv("SMTP_PASS")
    email_from = smtp_user

    if not smtp_host or not smtp_user or not smtp_pass:
        print("❌ SMTP non configurato!")
        return False

    print(f"📧 Invio email tramite {smtp_host}:{smtp_port} a {destinatario}")

    # --- Costruzione email ---
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = destinatario
    msg["Subject"] = oggetto
    msg.attach(MIMEText(corpo_html, "html"))

    # --- Allegato PDF eventuale ---
    if allegato:
        try:
            with open(allegato, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(allegato))
                part['Content-Disposition'] = f'attachment; filename="%s"' % os.path.basename(allegato)
                msg.attach(part)
        except Exception as e:
            print("⚠️ Errore allegato:", e)

    # --- INVIO STARTTLS (Netsons richiede questo, NON SSL diretto!) ---
    try:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.ehlo()
        server.starttls()   # 🔥 fondamentale per Netsons
        server.login(smtp_user, smtp_pass)
        server.sendmail(email_from, destinatario, msg.as_string())
        server.quit()
        print("✅ Email inviata correttamente!")
        return True

    except Exception as e:
        print("❌ Errore invio email:", e)
        return False

# ------------------- JOIN COMPLETO -------------------
def ottieni_stima_completa(stima_id):
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

    colonne = [desc[0] for desc in cur.description]
    return dict(zip(colonne, row))

# ------------------- MAIN -------------------
if __name__ == "__main__":
    crea_tabella_stime()
    crea_tabella_stime_dettagliate()
    crea_tabella_zone_valori()
    migrazione_allinea_stime()
    migrazione_gestionale_stime()

