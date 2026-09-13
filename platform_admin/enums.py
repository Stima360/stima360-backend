"""Costanti della superficie Platform.

Valori, non regole: la regola sta nella dipendenza e nel writer, qui ci sono
solo i nomi che entrambi devono usare uguali.
"""
from __future__ import annotations

# Il prefisso del router. Dichiarato qui perche' anche l'audit lo usa - per
# riconoscere una richiesta di superficie platform - e due letterali che devono
# restare identici sono un letterale solo.
ROUTER_PREFIX = "/api/platform"

# Gli esiti registrabili. Corrispondono uno a uno al CHECK di
# platform_audit_log.result nella migration 057: se questa tupla e quel CHECK
# divergono, un esito valido per l'applicazione viene rifiutato dal database.
# tests/test_p27_1_migration_057.py li confronta.
RESULT_SUCCESS = "success"
RESULT_DENIED = "denied"
RESULT_ERROR = "error"
AUDIT_RESULTS = (RESULT_SUCCESS, RESULT_DENIED, RESULT_ERROR)

# L'azione registrata da `require_platform_admin`: l'ammissione alla superficie.
#
# In P27-1 l'ammissione E' l'operazione - non c'e' altro da fare su questa
# superficie - quindi una riga per richiesta e' esattamente la traccia giusta.
# Dalle azioni di P27-2 in poi ogni operazione aggiungera' la PROPRIA riga con
# il proprio nome e il proprio esito, e questa restera' quello che e': la
# registrazione di chi e' entrato.
ACTION_ADMISSION = "platform.admission"

# Lo spazio dei nomi delle azioni. Prefisso obbligatorio, cosi' una riga di
# questa tabella si riconosce dalla sua azione anche fuori contesto.
ACTION_NAMESPACE = "platform."

# L'attore quando non c'e' un operatore identificato. Non e' un caso previsto in
# P27-1 - la superficie non registra nulla per un chiamante non autenticato -
# ma il writer e' una funzione pubblica e deve avere una risposta per ogni
# input, non una NULL silenziosa.
ANONYMOUS_ACTOR = "anonymous"

# 403, non 404: l'endpoint esiste e il chiamante lo sa. Non stiamo nascondendo
# una risorsa, stiamo rifiutando un'operazione. La stessa scelta che CORE fa in
# `AGENCY_CONTEXT_REQUIRED_MESSAGE` e OWNER Admin in `require_owner_admin_context`.
FORBIDDEN_MESSAGE = "Superficie riservata all'amministrazione di piattaforma."

# 503. Un'operazione amministrativa che non si riesce a registrare non avviene:
# vedi `dependencies.require_platform_admin`.
AUDIT_UNAVAILABLE_MESSAGE = (
    "Audit di piattaforma non disponibile: operazione non eseguita."
)
