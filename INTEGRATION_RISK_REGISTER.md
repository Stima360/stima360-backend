# INTEGRATION 0.1 — Registro rischi preliminare

## Bloccanti

1. **Assenza di evidenza certa dell’applicazione storica delle migrazioni**, se nel DB TEST non esiste un ledger. La presenza delle tabelle non basta.
2. **Divergenza tra snapshot locali e branch TEST reale**: l’inventario definitivo deve essere eseguito sul codice effettivamente deployato.

## Importanti

1. `main.py` cumulativo integrato manualmente: rischio di omissioni o collisioni future.
2. Suite test mista tra runtime, packaging e documentazione: rischio di falsi negativi.
3. Namespace Python `core`: conflitti di import già osservati nella suite globale.
4. Pydantic V1 deprecated: non bloccante oggi, debito tecnico verso Pydantic 3.
5. `pytest` non persistente su Render se installato solo dalla Shell.
6. Test E2E interrotti possono lasciare residui senza teardown completo.
7. OWNER richiede verifica live rigorosa dell’allowlist dei campi esposti.

## Accettabili nel Pacchetto 1

1. Warning Pydantic non bloccanti.
2. Assenza dei file documentali nel deploy runtime.
3. Mancanza di cleanup automatico: vietato dal perimetro del Pacchetto 1.
4. Route statiche senza prova HTTP: saranno coperte nel Pacchetto 2.
