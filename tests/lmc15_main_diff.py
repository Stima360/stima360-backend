"""LMC-15 - `main.py` resta sorvegliato, ma riga per riga.

Quattro sentinelle di fasi precedenti - LMC-2, LMC-7, LMC-8, LMC-9 - tenevano
`main.py` in un elenco di percorsi che non dovevano avere NESSUNA modifica.
LMC-15 ve ne fa una, legittima e dichiarata: monta il router del ponte di
acquisizione, come ogni dominio operatore prima di lui.

Togliere `main.py` da quegli elenchi sarebbe la risposta comoda e sbagliata:
da quel momento qualunque fase potrebbe cambiarlo senza che nessuna sentinella
se ne accorga. Invece di rimuovere il guardiano lo si rende preciso - il diff
di `main.py` puo' contenere SOLO quelle righe - e la garanzia che ognuna di
quelle quattro fasi voleva (nessuna di loro ha toccato `main.py`) resta
intera, perche' nessuna di quelle righe appartiene a loro.

Scritto una volta e importato dalle quattro: quattro copie della stessa
regola diventerebbero prima o poi quattro regole diverse.
"""
from __future__ import annotations

import subprocess

#: Le righe che LMC-15 e' autorizzata ad aggiungere, per intero e non come
#: frammenti: un confronto su un pezzo di riga lascerebbe passare una riga
#: piu' lunga che quel pezzo lo contiene.
RIGHE_LMC15 = {
    "from acquisition.router import router as acquisition_router",
    "# LMC-15: il ponte di acquisizione. Superficie operatore, montata come gli",
    "# altri domini; lo scope vero lo prende ogni rotta da `require_operator`.",
    "app.include_router(acquisition_router, "
    "dependencies=[Depends(require_authenticated_operator)])",
}


def righe_impreviste_in_main(root) -> list[str]:
    """Le righe del diff di `main.py` che LMC-15 non giustifica.

    Vuota significa "nessuno ha toccato `main.py` se non LMC-15, e solo per
    montare il suo router". Qualunque RIMOZIONE e' impervista per
    definizione: LMC-15 aggiunge e non toglie.

    P29-3B (collisione autorizzata, dichiarata): LMC-15 e' committata, e
    P29-3B monta a sua volta il router pubblico dell'unsubscribe. Le sue
    righe sono dichiarate UNA volta, in `p29_3b_diff`, e qui si ammettono
    per importazione: la regola resta la stessa - `main.py` puo' contenere
    SOLO righe che una fase ha dichiarato per nome.
    """
    from tests.p29_3b_diff import RIGHE_PER_FILE
    # MOUNT A30 (collisione dichiarata): l'API Agenda si monta come LMC-15, e
    # le sue righe sono dichiarate per intero in `a30_mount_diff`.
    from tests.a30_mount_diff import RIGHE_MAIN as RIGHE_A30_MOUNT
    # A30-7 (collisione dichiarata): il mount della sincronizzazione delle
    # richieste dal sito, dichiarato per intero in `a30_7_diff`.
    from tests.a30_7_diff import RIGHE_MAIN as RIGHE_A30_7
    # A30-9B (collisione dichiarata): il mount del router Google Calendar,
    # dichiarato per intero in `a30_9b_diff` (stessa regola di A30-7/mount).
    from tests.a30_9b_diff import RIGHE_MAIN as RIGHE_A30_9B
    # A30-12 (collisione dichiarata): il mount del router PUBBLICO del
    # booking, dichiarato per intero in `a30_12_diff` (stessa regola di
    # A30-7/9B/mount).
    from tests.a30_12_diff import RIGHE_MAIN as RIGHE_A30_12
    # P30 (collisione dichiarata, autorizzata): il mount statico della pagina
    # pubblica `/prenota/{token}`, dichiarato per intero in `p30_diff`.
    from tests.p30_diff import RIGHE_MAIN as RIGHE_P30
    # SENTINELLA AGGIORNATA DA A32-2 (collisione dichiarata, autorizzata): il
    # mount della rotta del giro
    # dei promemoria, dichiarato per intero in `a32_2_diff`.
    from tests.a32_2_diff import RIGHE_MAIN as RIGHE_A32_2
    # SENTINELLA AGGIORNATA DA CRM-OPS-3 (collisione dichiarata, autorizzata):
    # il mount delle Acquisizioni (`/api/acquisitions`), dichiarato per intero
    # in `crm_ops_3_diff`.
    from tests.crm_ops_3_diff import RIGHE_MAIN as RIGHE_CRM_OPS_3
    ammesse = (RIGHE_LMC15 | RIGHE_PER_FILE["main.py"] | RIGHE_A30_MOUNT | RIGHE_A30_7
              | RIGHE_A30_9B | RIGHE_A30_12 | RIGHE_P30 | RIGHE_A32_2 | RIGHE_CRM_OPS_3)
    diff = subprocess.run(
        ["git", "--no-optional-locks", "diff", "--unified=0", "--", "main.py"],
        cwd=root, capture_output=True, text=True).stdout.splitlines()
    fuori = []
    for riga in diff:
        if riga.startswith(("+++", "---")):
            continue
        if riga.startswith("-"):
            fuori.append(riga)
        elif riga.startswith("+") and riga[1:].strip() not in ammesse:
            fuori.append(riga)
    return fuori
