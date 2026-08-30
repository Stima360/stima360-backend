# INTEGRATION 0.1 — Patch import e test read-only

## Causa tecnica

Il file storico `tests/conftest.py` introdotto dal pacchetto OWNER crea moduli stub tramite `types.ModuleType` e li registra con `sys.modules.setdefault`:

- `core`
- `core.database`
- `core.exceptions`

Lo stub `core.exceptions` espone `NotFoundError` e `ConflictError`, ma non `ValidationError`. Durante la raccolta pytest questo stub precedeva il package reale del progetto. Di conseguenza `flow.router` importava lo stub e falliva su `ValidationError`; inoltre `core.__path__=[]` impediva la risoluzione di `core.router`.

## Correzione limitata alla suite

- il runner read-only usa `pytest --noconftest`;
- gli import critici avvengono in un contesto deterministico che:
  - salva `sys.path` e i moduli `core*`/`main`;
  - rimuove temporaneamente eventuali moduli contaminanti;
  - mette la root del repository al primo posto;
  - verifica `module.__file__`;
  - ripristina lo stato globale all'uscita;
- la diagnostica completa viene stampata solo in caso di errore.

Nessun file applicativo è stato modificato.
