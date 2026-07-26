# INTEGRATION P2 — HTTP MATRIX

## Read-only

- risorsa inesistente;
- token OWNER non valido;
- payload respinto prima della persistenza;
- assenza di 500/502 e dettagli SQL.

## Stateful, solo su entità del run

- duplicato logico → 409;
- stato incompatibile → 409;
- pubblicazione `published` immutabile → 409;
- quarto retry FLOW → 409;
- payload invalido → 400/422;
- nessun dettaglio SQL;
- nessun 500/502 inatteso.

**Non eseguito.**
