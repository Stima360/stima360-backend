# INTEGRATION P2 — SECURITY RESULTS

**NON ESEGUITO.**

Verifiche predisposte:

- cookie statico e runtime HTTPS separati;
- token OWNER grezzo assente dal record DB;
- `token_hash` presente e diverso dal token;
- hash SHA-256 coerente;
- token non recuperabile dal record;
- nessun localStorage/sessionStorage;
- ambiente TEST centralizzato.
