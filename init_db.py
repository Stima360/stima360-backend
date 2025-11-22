# init_db.py – inizializza le tabelle su Postgres

from database import (
    crea_tabella_stime,
    crea_tabella_stime_dettagliate,
    crea_tabella_zone_valori,
    migrazione_allinea_stime,
)

if __name__ == "__main__":
    print("🔧 Creo tabella stime...")
    crea_tabella_stime()
    print("🔧 Creo tabella stime_dettagliate...")
    crea_tabella_stime_dettagliate()
    print("🔧 Creo tabella zone_valori...")
    crea_tabella_zone_valori()
    print("🔧 Eseguo migrazione allinea_stime...")
    migrazione_allinea_stime()
    print("✅ Inizializzazione DB completata.")
