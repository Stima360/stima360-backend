"""La superficie Platform: amministrazione della rete, non di un'agenzia.

P27-1 costruisce il livello SOPRA le agenzie, e lo costruisce separato di
proposito. Un platform admin non e' un operatore di agenzia con piu' permessi:
e' un principale che opera su un oggetto diverso - la rete - e che per arrivare
ai dati di una specifica agenzia dovra' passare, quando servira', da un accesso
esplicito e progettato.

Il package si chiama ``platform_admin`` e non ``platform`` perche' ``platform``
e' un modulo della standard library: un package con quel nome verrebbe risolto
al posto suo da qualunque import assoluto eseguito con la radice del repository
sul path, e romperebbe dipendenze che non sanno di essere coinvolte.

Cosa contiene P27-1, e nient'altro:

* ``dependencies.require_platform_admin`` - l'ammissione alla superficie.
* ``router``                              - ``/api/platform``, un solo endpoint.
* ``audit``                               - il writer di ``platform_audit_log``.
* ``repository``                          - l'unica INSERT su quella tabella.
* ``database``                            - il confine transazionale del writer.

Cosa NON contiene, e non deve contenere finche' la fase corrispondente non lo
chiede: CRUD agenzie (P27-2), gestione operatori e ruoli (P27-3), impostazioni
agenzia (P27-4), territori (P27-5), routing dei lead (P27-6), UI (P27-7).
"""
