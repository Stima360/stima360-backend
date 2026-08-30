# INTEGRATION P2 — SCHEMA RESULTS

**NON ESEGUITO.**

La suite ora costruisce per ogni foreign key una query `LEFT JOIN` fra colonne figlie e colonne parent e conta realmente gli orfani, invece di limitarsi alla presenza di `conrelid/confrelid` nel catalogo PostgreSQL.
