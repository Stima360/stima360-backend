"""P29-3B - la collisione dichiarata con i guardiani delle fasi precedenti.

Sei sentinelle - LMC-2, LMC-7, LMC-8, LMC-9, LMC-12, LMC-15 - tenevano
`communication/` (e LMC-2 e LMC-15 anche `operator_auth/`) in un elenco di
percorsi che non dovevano avere NESSUNA modifica nel working tree, e quattro
di loro sorvegliano `main.py` riga per riga attraverso `lmc15_main_diff`.
Nessuna di quelle fasi ha toccato quei percorsi, ed e' ancora vero.

P29-3B vi fa tre modifiche, legittime e dichiarate dal suo mandato:

* `communication/repository.py` e `communication/service.py`: `enqueue`
  accetta la PROVENIENZA di un messaggio di journey (`enrollment_id`,
  `step_no`, `run_no`), tre colonne insieme o nessuna. Estensione additiva:
  un messaggio manuale non le porta e il dispatcher non le legge.
* `operator_auth/context.py`: `public_unsubscribe` entra nell'insieme chiuso
  delle origini di sistema, perche' il link di disiscrizione scrive senza
  operatore e con l'agenzia presa dal token firmato.
* `main.py`: il router pubblico dell'unsubscribe viene montato, senza
  dipendenze, di proposito.

Togliere quei percorsi dagli elenchi sarebbe la risposta comoda e sbagliata.
Invece di rimuovere il guardiano lo si rende preciso, come LMC-15 fece con
`main.py`: il diff di quei file puo' contenere SOLO queste righe, nessun
altro file di quei domini puo' essere toccato, e la garanzia che ognuna di
quelle fasi voleva resta intera, perche' nessuna di queste righe e' loro.

Scritto una volta e importato dalle sentinelle: piu' copie della stessa regola
diventerebbero prima o poi regole diverse. Dopo il commit di P29-3B il diff
e' vuoto e ogni controllo qui torna a dire "nessuno ha toccato niente".
"""
from __future__ import annotations

import subprocess

#: Percorsi di dominio sorvegliati IN BLOCCO dalle sentinelle, dentro i quali
#: P29-3B ammette modifiche solo ai file di `RIGHE_PER_FILE`.
DOMINI_SORVEGLIATI = ("communication/", "operator_auth/", "main.py")

#: Le righe che P29-3B e' autorizzata ad AGGIUNGERE, per file e per intero
#: (una riga vuota aggiunta non porta niente ed e' sempre ammessa).
RIGHE_PER_FILE: dict[str, frozenset[str]] = {
    "main.py": frozenset({
        "from communication.public_router import router as communication_public_router",
        "# P29-3B.0: la disiscrizione dal marketing. PUBBLICA per natura - chi clicca",
        "# e' l'interessato, non un operatore - e autorizzata dalla firma del token,",
        "# non da una sessione. Nessuna dipendenza qui, di proposito.",
        "app.include_router(communication_public_router)",
    }),
    "communication/repository.py": frozenset({
        "# P29-3B: la PROVENIENZA di un messaggio di journey. Tre colonne insieme o",
        "# nessuna (CHECK della 071), scritte all'INSERT e mai piu' (guardia).",
        '"enrollment_id", "step_no", "run_no",',
        "#: Le tre colonne di provenienza vengono NOMINATE nella INSERT solo quando il",
        "#: messaggio le porta: un messaggio manuale o di sistema non le scrive, e non",
        "#: le nomina. Cosi' un ledger senza la 071 - il codice si distribuisce prima",
        "#: che la migration venga applicata - continua ad accettare ogni messaggio",
        "#: che accettava prima, e solo un messaggio di journey richiede lo schema.",
        'PROVENANCE_COLUMNS = ("enrollment_id", "step_no", "run_no")',
        "attive = [c for c in INSERTABLE_COLUMNS",
        "if c not in PROVENANCE_COLUMNS or prepared.get(c) is not None]",
        'colonne = ", ".join(attive)',
        'segnaposti = ", ".join(f"%({c})s" for c in attive)',
    }),
    "communication/service.py": frozenset({
        'provenienza = (dati.get("enrollment_id"), dati.get("step_no"), dati.get("run_no"))',
        "if any(v is not None for v in provenienza) and not all(",
        "isinstance(v, int) and v >= 1 for v in provenienza):",
        "raise ValidationError(",
        '"enrollment_id, step_no and run_no go together, as positive integers, "',
        '"or not at all"',
        ")",
        '"enrollment_id": provenienza[0], "step_no": provenienza[1], "run_no": provenienza[2],',
        "enrollment_id: int | None = None,",
        "step_no: int | None = None,",
        "run_no: int | None = None,",
        "P29-3B: `enrollment_id`, `step_no` e `run_no` dicono DA QUALE passo di",
        "quale iscrizione nasce il messaggio. Vanno insieme o non vanno affatto; un",
        "messaggio manuale non li porta. Il dispatcher non li legge: non sa cosa",
        "sia una journey, e non deve saperlo.",
        '"enrollment_id": enrollment_id, "step_no": step_no, "run_no": run_no,',
    }),
    "operator_auth/context.py": frozenset({
        "# P29-3B.0: `public_unsubscribe` e' il link di disiscrizione dal marketing.",
        "# Come `owner_login` non ha un operatore dietro - c'e' l'interessato, che",
        "# clicca - e come `public_stima` la sua agenzia non viene da una sessione ma",
        "# da un dato verificato lato server: qui la firma HMAC del token.",
        'SYSTEM_CONTEXT_ORIGINS = ("public_stima", "communication_dispatch", "owner_login",',
        '"public_unsubscribe")',
    }),
}

#: Le SOLE rimozioni ammesse: le due righe della INSERT che ora nominano le
#: colonne attive, e la tupla delle origini riscritta su due righe per
#: accogliere la quarta. Ogni altra rimozione e' imprevista.
RIMOZIONI_PER_FILE: dict[str, frozenset[str]] = {
    "communication/repository.py": frozenset({
        'colonne = ", ".join(INSERTABLE_COLUMNS)',
        'segnaposti = ", ".join(f"%({c})s" for c in INSERTABLE_COLUMNS)',
    }),
    "operator_auth/context.py": frozenset({
        'SYSTEM_CONTEXT_ORIGINS = ("public_stima", "communication_dispatch", "owner_login")',
    }),
}

#: L'inventario approvato di P29-3B, scritto a mano e non derivato da `git`.
FILE_MODIFICATI = frozenset({
    "main.py", "communication/repository.py", "communication/service.py",
    "operator_auth/context.py",
    "docs/P26_DB_ENTRYPOINTS.md", "tests/test_p26_db_entrypoints.py",
    "tests/test_p26_5_basic_containment.py",
    # La governance del cleanup di certificazione: le dieci FK non-CASCADE
    # della 071 esaminate, e `seller_timeline_events` fotografata.
    "scripts/p26_6_live_cert.py", "tests/test_p26_6_live_cert_script.py",
    "tests/test_p26_6c_backend_gate_closure.py",
    # Le sentinelle di fase che nominano cio' che ammettono.
    "tests/lmc15_main_diff.py",
    "tests/test_lmc1b_owner_login_link.py", "tests/test_lmc2_owner_homes.py",
    "tests/test_lmc3_valuation_snapshot.py", "tests/test_lmc7_owner_radar.py",
    "tests/test_lmc8_crm_radar.py", "tests/test_lmc9_consultation_request.py",
    "tests/test_lmc11_valuation_cron.py", "tests/test_lmc12_home_notifications.py",
    "tests/test_lmc13_home_metrics.py",
    "tests/test_lmc15_acquisition_bridge.py",
    "tests/test_p26_1_operator_auth.py",
    # P27-5: la sentinella `test_j6` trattava una migration NUOVA in stage
    # come una migration storica modificata. Collisione dichiarata, garanzia
    # invariata (M/D/R su `migrations/` resta vietato).
    "tests/test_p27_5_territories.py",
    "tests/test_p27_6_lead_routing.py",
    "tests/test_p29_1_consent_migrations.py",
    "tests/test_p29_2_1_communication_foundation.py",
    "tests/test_p29_2_2_communication_service.py",
    "tests/test_p29_2_3_claim_sentinels.py",
    "tests/test_p29_2_4_dispatch_sentinels.py",
    "tests/test_p29_2_5e_email_adapter.py",
    "tests/test_p29_2_6e_channel_routing.py",
})
FILE_NUOVI = frozenset({
    "migrations/071_p29_3_journey_automation.sql",
    "migrations/071_p29_3_journey_automation_down.sql",
    "communication/journey_enums.py", "communication/journey_repository.py",
    "communication/journey_service.py", "communication/public_router.py",
    "communication/templates.py", "communication/unsubscribe.py",
    "tests/p29_3b_diff.py",
    "tests/test_p29_3_journey_foundation.py",
    "tests/test_p29_3_journey_foundation_postgres.py",
})


def _git(root, *argomenti) -> list[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", *argomenti],
        cwd=root, capture_output=True, text=True).stdout.splitlines()


def righe_impreviste(root, percorso: str) -> list[str]:
    """Le righe del diff di `percorso` che P29-3B non giustifica."""
    ammesse = RIGHE_PER_FILE.get(percorso, frozenset())
    if percorso == "main.py":
        # MOUNT A30 (collisione dichiarata): le righe del mount dell'API
        # Agenda, per intero, da `a30_mount_diff`. Nessun'altra riga.
        from tests.a30_mount_diff import RIGHE_MAIN
        ammesse = ammesse | RIGHE_MAIN
    rimovibili = RIMOZIONI_PER_FILE.get(percorso, frozenset())
    fuori = []
    for riga in _git(root, "diff", "--unified=0", "--", percorso):
        if riga.startswith(("+++", "---")):
            continue
        contenuto = riga[1:].strip()
        if riga.startswith("-") and contenuto not in rimovibili:
            fuori.append(riga)
        elif riga.startswith("+") and contenuto and contenuto not in ammesse:
            fuori.append(riga)
    return fuori


def _dichiarati_da_fasi_successive() -> frozenset[str]:
    """I file che una fase SUCCESSIVA dichiara nei domini sorvegliati.

    P29-3C riscrive parti intere di `communication/` - il motore, le sue
    query, le tre rotte, la timeline dell'invio - e pinnarne ogni riga qui
    vorrebbe dire tenere due copie dello stesso diff. La garanzia resta la
    stessa di prima, un gradino piu' grossa per quei file: nessun file di
    quei domini puo' essere toccato senza che UNA fase lo abbia dichiarato
    per nome, e quale riga sia lecita lo verifica l'inventario di quella
    fase (`tests/p29_3c_diff.py`, e la sentinella che lo confronta con
    `git status` in entrambe le direzioni).

    P29-3D (collisione dichiarata) sta nella stessa posizione: i testi reali
    della sequenza, "invia ora" sul ledger, le rotte del Contact 360 e la
    sonda dello schema toccano ancora `communication/`. Si aggiunge il suo
    inventario accanto a quello di P29-3C invece di allargare la maglia:
    l'elenco delle fasi e' esplicito, e una fase che non si dichiara non
    passa.
    """
    dichiarati: set[str] = set()
    for modulo in ("tests.p29_3c_diff", "tests.p29_3d_diff"):
        try:
            inventario = __import__(modulo, fromlist=["FILE_MODIFICATI"])
        except ImportError:  # la fase non esiste ancora: nulla da ammettere
            continue
        dichiarati |= set(inventario.FILE_MODIFICATI) | set(inventario.FILE_NUOVI)
    return frozenset(dichiarati)


def diff_imprevisto_nei_domini(root) -> list[str]:
    """Tutto cio' che, in `DOMINI_SORVEGLIATI`, nessuna fase giustifica.

    Vuota significa "nessuno ha toccato quei domini se non le fasi che lo
    hanno dichiarato". Per i tre file di P29-3B il controllo resta riga per
    riga; per i file che una fase successiva dichiara, il controllo e' che
    li abbia dichiarati.
    """
    ammessi_da_dopo = _dichiarati_da_fasi_successive()
    fuori = []
    for nome in _git(root, "diff", "--name-only", "--", *DOMINI_SORVEGLIATI):
        nome = nome.strip()
        if nome in ammessi_da_dopo:
            continue
        if nome not in RIGHE_PER_FILE:
            fuori.append(f"{nome}: file non dichiarato da nessuna fase")
        else:
            fuori.extend(f"{nome}: {riga}" for riga in righe_impreviste(root, nome))
    return fuori
