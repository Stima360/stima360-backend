# backend/migrate_add_token.py
# Scopo: aggiunge 2 colonne (token, token_expires) alla tabella "stima"
# e crea un indice. Una volta e basta.

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Carica variabili da backend/.env (qui hai già i dati del DB)
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Puoi usare DATABASE_URL (es. postgresql://user:pass@host:5432/dbname)
# oppure i singoli PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGPORT
db_url = os.getenv("DATABASE_URL")
conn = None

try:
    if db_url:
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    else:
        conn = psycopg2.connect(
            host=os.getenv("PGHOST", "127.0.0.1"),
            user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", ""),
            dbname=os.getenv("PGDATABASE", "stima360"),
            port=os.getenv("PGPORT", "5432"),
            cursor_factory=RealDictCursor,
        )

    cur = conn.cursor()
    sql = """
    ALTER TABLE stima ADD COLUMN IF NOT EXISTS token UUID;
    ALTER TABLE stima ADD COLUMN IF NOT EXISTS token_expires TIMESTAMPTZ;
    CREATE INDEX IF NOT EXISTS idx_stima_token ON stima(token);
    """
    cur.execute(sql)
    conn.commit()
    print("✅ Migrazione OK: colonne e indice creati (o già presenti).")
except Exception as e:
    print("❌ Errore migrazione:", e)
    if conn:
        conn.rollback()
finally:
    if conn:
        conn.close()
