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
        ("Alba Adriatica", "Nord", 1250),
        ("Alba Adriatica", "Villa Fiore", 1350),
        ("Alba Adriatica", "Zona Basciani", 1200),

        ("Tortoreto", "Lido Sud", 1450),
        ("Tortoreto", "Lido Centro", 1650),
        ("Tortoreto", "Lido Nord", 1500),
        ("Tortoreto", "Alto", 1100),

        ("Martinsicuro", "Centro", 1000),
        ("Martinsicuro", "Villarosa", 900),
        ("Martinsicuro", "Alta", 850),
    ])
    conn.commit()
    cur.close(); conn.close()


# ------------------- MIGRAZIONI -------------------
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
    cur.close(); conn.close()


def migrazione_stime_completa():
    """Aggiunge TUTTI i parametri della stima base nel DB."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE stime
          ADD COLUMN IF NOT EXISTS microzona       VARCHAR(100),

          ADD COLUMN IF NOT EXISTS posizioneMare   VARCHAR(50),
          ADD COLUMN IF NOT EXISTS distanzaMare    VARCHAR(50),
          ADD COLUMN IF NOT EXISTS barrieraMare    VARCHAR(50),
          ADD COLUMN IF NOT EXISTS vistaMareYN     VARCHAR(10),
          ADD COLUMN IF NOT EXISTS vistaMare       VARCHAR(50),

          ADD COLUMN IF NOT EXISTS stato           VARCHAR(40),
          ADD COLUMN IF NOT EXISTS anno            INTEGER,

          ADD COLUMN IF NOT EXISTS mqGiardino      INTEGER,
          ADD COLUMN IF NOT EXISTS mqGarage        INTEGER,
          ADD COLUMN IF NOT EXISTS mqCantina       INTEGER,
          ADD COLUMN IF NOT EXISTS mqPostoAuto     INTEGER,
          ADD COLUMN IF NOT EXISTS mqTaverna       INTEGER,
          ADD COLUMN IF NOT EXISTS mqSoffitta      INTEGER,
          ADD COLUMN IF NOT EXISTS mqTerrazzo      INTEGER,
          ADD COLUMN IF NOT EXISTS numBalconi      INTEGER,

          ADD COLUMN IF NOT EXISTS altroDescrizione TEXT;
    """)
    conn.commit()
    cur.close(); conn.close()


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
            pertinenze VARCHAR(200),
            ascensore VARCHAR(10),
            nome VARCHAR(50),
            cognome VARCHAR(50),
            email VARCHAR(100),
            telefono VARCHAR(30),

            -- nuovi campi aggiunti con migrazione_stime_completa
            microzona VARCHAR(100),

            posizioneMare VARCHAR(50),
            distanzaMare VARCHAR(50),
            barrieraMare VARCHAR(50),
            vistaMareYN VARCHAR(10),
            vistaMare VARCHAR(50),

            stato VARCHAR(40),
            anno INTEGER,

            mqGiardino INTEGER,
            mqGarage INTEGER,
            mqCantina INTEGER,
            mqPostoAuto INTEGER,
            mqTaverna INTEGER,
            mqSoffitta INTEGER,
            mqTerrazzo INTEGER,
            numBalconi INTEGER,

            altroDescrizione TEXT,

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

   
            classe VARCHAR(8),
            riscaldamento VARCHAR(32),
            condizionatore VARCHAR(8),
            spese_cond INTEGER,
            condiz_tipo VARCHAR(50),
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

def migrazione_stime_dettagliate_completa():
    conn = get_connection(); cur = conn.cursor()
    cur.execute("""
        ALTER TABLE stime_dettagliate
          ADD COLUMN IF NOT EXISTS nome VARCHAR(50),
          ADD COLUMN IF NOT EXISTS cognome VARCHAR(50),
          ADD COLUMN IF NOT EXISTS email VARCHAR(100),
          ADD COLUMN IF NOT EXISTS telefono VARCHAR(30),

          ADD COLUMN IF NOT EXISTS indirizzo TEXT,
          ADD COLUMN IF NOT EXISTS tipologia VARCHAR(50),
          ADD COLUMN IF NOT EXISTS mq INTEGER,
          ADD COLUMN IF NOT EXISTS piano VARCHAR(30),
          ADD COLUMN IF NOT EXISTS locali VARCHAR(50),
          ADD COLUMN IF NOT EXISTS bagni INTEGER,
          ADD COLUMN IF NOT EXISTS ascensore VARCHAR(10),

          ADD COLUMN IF NOT EXISTS stato VARCHAR(40),
          ADD COLUMN IF NOT EXISTS anno INTEGER,

          ADD COLUMN IF NOT EXISTS microzona VARCHAR(100),
          ADD COLUMN IF NOT EXISTS posizioneMare VARCHAR(50),
          ADD COLUMN IF NOT EXISTS distanzaMare VARCHAR(50),
          ADD COLUMN IF NOT EXISTS barrieraMare VARCHAR(50),
          ADD COLUMN IF NOT EXISTS vistaMare VARCHAR(80),
          ADD COLUMN IF NOT EXISTS mqGiardino INTEGER,
          ADD COLUMN IF NOT EXISTS mqGarage INTEGER,
          ADD COLUMN IF NOT EXISTS mqCantina INTEGER,
          ADD COLUMN IF NOT EXISTS mqPostoAuto INTEGER,
          ADD COLUMN IF NOT EXISTS mqTaverna INTEGER,
          ADD COLUMN IF NOT EXISTS mqSoffitta INTEGER,
          ADD COLUMN IF NOT EXISTS mqTerrazzo INTEGER,
          ADD COLUMN IF NOT EXISTS numBalconi INTEGER,
          ADD COLUMN IF NOT EXISTS altroDescrizione TEXT,
          ADD COLUMN IF NOT EXISTS pertinenze VARCHAR(200);
    """)
    conn.commit()
    cur.close(); conn.close()
# ------------------- EMAIL -------------------
def invia_mail(destinatario, oggetto, corpo_html, allegato=None):
    smtp_host = os.getenv("SMTP_HOST", "mail.stima360.it")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    email_from = smtp_user

    if not smtp_host or not smtp_user or not smtp_pass:
        print("❌ SMTP non configurato!")
        return False

    print(f"📧 Invio email tramite {smtp_host}:{smtp_port} a {destinatario}")

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = destinatario
    msg["Subject"] = oggetto
    msg.attach(MIMEText(corpo_html, "html"))

    if allegato:
        try:
            with open(allegato, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(allegato))
                part['Content-Disposition'] = f'attachment; filename="%s"' % os.path.basename(allegato)
                msg.attach(part)
        except Exception as e:
            print("⚠️ Errore allegato:", e)

    try:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.ehlo()
        server.starttls()
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
        SELECT * FROM stime
        WHERE id = %s
    """, (stima_id,))

    row = cur.fetchone()
    colonne = [d[0] for d in cur.description]

    cur.close()
    conn.close()

    if not row:
        return None

    return dict(zip(colonne, row))


# ------------------- MAIN -------------------
def migrazione_condiz_tipo():
    """Aggiunge il campo condiz_tipo nella tabella stime_dettagliate se manca."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE stime_dettagliate
        ADD COLUMN IF NOT EXISTS condiz_tipo VARCHAR(50);
    """)
    conn.commit()
    cur.close()
    conn.close()


# ------------------- MAIN -------------------
if __name__ == "__main__":
    crea_tabella_stime()
    crea_tabella_stime_dettagliate()
    crea_tabella_zone_valori()
    migrazione_allinea_stime()
    migrazione_gestionale_stime()
    migrazione_stime_completa()
    migrazione_condiz_tipo()   # <-- CORRETTO
    migrazione_stime_dettagliate_completa()
