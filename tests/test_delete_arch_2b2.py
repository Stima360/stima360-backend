"""DELETE-ARCH Fase 2B2 - sentinella statica: ogni lettura di `properties`
nel codice applicativo sa che esiste il Cestino.

Si leggono con `ast` tutte le stringhe SQL del codice applicativo che nominano
`FROM properties`, `JOIN properties`, `USING properties` o `..., properties p`
(le ultime due dalla REVIEW 1). Ognuna deve:

  1. filtrare il Cestino nella stessa istruzione (`deleted_at` o
     `property_trash.live(...)`, anche attraverso una costante del modulo
     interpolata); oppure
  2. stare in una funzione che rifiuta esplicitamente l'immobile nel Cestino
     (`refuse_if_in_trash` / `PropertyInTrash` / lettura di `deleted_at`) o
     che chiama una delle GUARDIE elencate qui sotto (verificate una per una);
     oppure
  3. essere una lettura con lock CERTO (`FOR UPDATE` / `FOR SHARE` nel testo
     fisso, non in un `{... if lock else ''}`): e' il primo
     passo di una scrittura, e una scrittura che creasse un nuovo riferimento
     verso un immobile nel Cestino e' rifiutata dalle guardie della 086 (le
     modifiche alla riga stessa dal congelamento); oppure
  4. essere nell'elenco breve e motivato `CONSAPEVOLI`: le letture che DEVONO
     vedere anche il Cestino.

Una nuova lettura operativa di `properties` senza filtro fa fallire questo
test con il suo file, funzione e riga.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: `FROM properties`, `JOIN properties` e (REVIEW 1) le forme a virgola e
#: `USING` delle UPDATE/DELETE: `FROM stime s, properties p`.
SQL_PROPERTIES = re.compile(r"(\b(FROM|JOIN|USING)\s+properties\b(?!_)|,\s*properties\s+(AS\s+)?[a-z_]+\b)", re.I)
FILTRO = ("deleted_at", "live(")
LOCK = re.compile(r"\bFOR\s+(UPDATE|SHARE)\b", re.I)
RIFIUTO = ("refuse_if_in_trash", "PropertyInTrash", "deleted_at")

#: Funzioni che rifiutano (o filtrano) l'immobile nel Cestino per chi le chiama.
GUARDIE = {
    "property/repository.py": ("ensure_scoped",),
    "buy/repository.py": ("_ensure_match", "_ensure_agency_row"),
    "acquisitions/repository.py": ("lock_property",),
    "appointments/repository.py": ("link_agency",),
    "property/census.py": ("_unita",),
    "property/interactions.py": ("immobile_nello_scope",),
    "crm/sellers.py": ("_immobile",),
    "owner/repository.py": ("_require_property_in_agency", "_property_for_document", "_property_for_visit"),
    "proposal/repository.py": ("_relation", "_scoped_relation"),
}

#: (file, funzione) -> perche' quella lettura vede anche il Cestino.
CONSAPEVOLI = {
    ("property/repository.py", "_free_generated_code"):
        "`properties.code` e' UNIQUE su tutta la tabella, Cestino compreso (D13)",
    ("property/repository.py", "get_property"):
        "lettura di servizio usata anche dalle scritture; il dettaglio operativo la filtra in "
        "property/service.py::get_property (404)",
    ("property/lifecycle.py", "_immobile"):
        "il caricatore del ciclo di vita (trash, restore, deletion-check, archivia/riattiva, scollegamenti) "
        "DEVE vedere anche il Cestino; ogni chiamante rifiuta (refuse_if_in_trash) o tratta in_trash",
    ("property/mandates.py", "_base"):
        "FROM comune: il filtro e' in E_UN_INCARICO, usato da ogni chiamante",
    ("property/mandates.py", "_proprietari"):
        "proprietari di immobili gia' selezionati da list_mandates (filtrati)",
    ("owner/repository.py", "audits"):
        "registro di audit del portale: storia, non superficie operativa",
    ("owner/repository.py", "<module>"):
        "_PROPERTY_TENANT: usata solo da _require_property_in_agency (GUARDIA)",
    ("sale/repository.py", "_scoped_proposal_for_sale"):
        "creazione di una vendita (lock interpolato): la nuova riga property_sales e' rifiutata dalla 086",
    ("sale/repository.py", "create_sale_scoped"):
        "istantanea dei venditori dentro la creazione: la nuova vendita e' rifiutata dalla 086",
    ("sale/repository.py", "complete_sale_scoped"):
        "catena riletta dentro il completamento, che parte da _scoped_sale (filtrato: 404 nel Cestino)",
    ("acquisition/repository.py", "_blocca_stima_dell_acquisizione"):
        "ponte LMC-15: scrittura su un collegamento esistente; il nuovo collegamento e' rifiutato dalla 086",
    ("acquisition/repository.py", "create_acquisition_link"):
        "ponte LMC-15: INSERT del collegamento, rifiutato dalla 086 verso un immobile nel Cestino",
    ("acquisition/repository.py", "revoke_acquisition_link"):
        "ponte LMC-15: revoca di un collegamento esistente (relazione esistente)",
    # REVIEW 1: forme a virgola / USING (UPDATE/DELETE), nessuna riga restituita
    ("match/repository.py", "detect_stale"):
        "manutenzione tecnica (freshness) di abbinamenti esistenti; nessun nuovo riferimento",
    ("match/repository.py", "detect_stale_scoped"):
        "manutenzione tecnica (freshness) di abbinamenti esistenti; nessun nuovo riferimento",
    ("match/repository.py", "delete_exclusion_scoped"):
        "rimozione di una esclusione esistente (relazione esistente); nessun nuovo riferimento",
    ("match/repository.py", "delete_feedback_scoped"):
        "rimozione di un riscontro esistente (relazione esistente); nessun nuovo riferimento",
    # SENTINELLA AGGIORNATA DA CESTINO-RICHIESTE-1: i blocchi del Cestino Richieste elencano TUTTE le righe aperte
    # (proposte, vendite, visite) con il loro immobile; una riga aperta su un immobile
    # nel Cestino non puo' esistere (lo blocca il Cestino Immobili), e il blocco non
    # deve comunque nasconderne nessuna.
    ("buy/lifecycle.py", "trash_blockers"):
        "blocchi del Cestino Richieste: elenco completo dei processi aperti, sola lettura, nessun nuovo riferimento",
}


def _sorgenti():
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "scripts/", "run_", "integration_")) or "__pycache__" in rel:
            continue
        yield rel, p.read_text(encoding="utf-8", errors="replace")


def _costanti(tree) -> dict[str, str]:
    """Le costanti stringa di modulo (anche concatenate o f-string)."""
    valori = {}
    for nodo in tree.body:
        if isinstance(nodo, ast.Assign) and len(nodo.targets) == 1 and isinstance(nodo.targets[0], ast.Name):
            testo = "".join(n.value for n in ast.walk(nodo.value)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str))
            valori[nodo.targets[0].id] = testo
    return valori


def _letture():
    """(file, funzione, riga, testo, sorgente della funzione, nomi interpolati)."""
    for rel, src in _sorgenti():
        tree = ast.parse(src)
        funzioni = [(n.lineno, n.end_lineno, n.name, ast.get_source_segment(src, n) or "")
                    for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        costanti = _costanti(tree)
        visti = set()
        for nodo in ast.walk(tree):
            if isinstance(nodo, ast.JoinedStr):
                testo = ast.get_source_segment(src, nodo) or ""
                nomi = {n.id for v in nodo.values if isinstance(v, ast.FormattedValue)
                        for n in ast.walk(v.value) if isinstance(n, ast.Name)}
                # il testo FISSO: un `{' FOR UPDATE' if lock else ''}` non e' un lock certo
                fisso = "".join(v.value for v in nodo.values if isinstance(v, ast.Constant))
            elif isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
                testo, nomi, fisso = nodo.value, set(), nodo.value
            else:
                continue
            if not SQL_PROPERTIES.search(testo) or (nodo.lineno, nodo.col_offset) in visti:
                continue
            visti.add((nodo.lineno, nodo.col_offset))
            dentro = [f for f in funzioni if f[0] <= nodo.lineno <= f[1]]
            nome, corpo = (max(dentro)[2], max(dentro)[3]) if dentro else ("<module>", "")
            interpolato = "".join(costanti.get(n, "") for n in nomi)
            yield rel, nome, nodo.lineno, testo + interpolato, corpo, fisso


def _guardie_chiamate(corpo: str, rel: str) -> bool:
    """La funzione chiama una GUARDIA del suo stesso modulo."""
    return any(re.search(rf"(?<![\w.]){re.escape(n)}\(", corpo) for n in GUARDIE.get(rel, ()))


def test_01_ogni_lettura_di_properties_conosce_il_cestino():
    scoperte = []
    usate = set()
    for rel, funzione, riga, testo, corpo, fisso in _letture():
        if any(f in testo for f in FILTRO):
            continue
        if any(r in corpo for r in RIFIUTO + FILTRO) or _guardie_chiamate(corpo, rel):
            continue
        if LOCK.search(fisso):
            continue
        if (rel, funzione) in CONSAPEVOLI:
            usate.add((rel, funzione))
            continue
        scoperte.append(f"{rel}:{riga} [{funzione}] {' '.join(testo.split())[:120]}")
    assert scoperte == [], ("letture di `properties` senza il filtro del Cestino (usa "
                            "core.property_trash.live / refuse_if_in_trash, o motiva in CONSAPEVOLI):\n"
                            + "\n".join(scoperte))
    obsolete = sorted(set(CONSAPEVOLI) - usate)
    assert obsolete == [], f"voci CONSAPEVOLI che non servono piu': {obsolete}"


def test_02_le_guardie_rifiutano_davvero():
    import importlib
    for rel, nomi in GUARDIE.items():
        modulo = importlib.import_module(rel[:-3].replace("/", "."))
        for nome in nomi:
            corpo = inspect.getsource(getattr(modulo, nome))
            assert any(r in corpo for r in RIFIUTO), (rel, nome)


def test_03_le_scansioni_flow_sugli_immobili_filtrano_il_cestino():
    from flow import adapters
    for codice, spec in adapters._SCANS.items():
        if not spec:
            continue
        sorgente = " ".join(spec["source"])
        if "properties" in sorgente:
            testo = sorgente + spec["where"] + spec.get("where_scoped", "")
            assert "deleted_at" in testo, codice


def test_04_rifiuto_del_database_tradotto_in_409():
    from core import database, property_trash
    assert "PropertyInTrash" in inspect.getsource(database.core_cursor)
    assert property_trash.PropertyInTrash.code == "PROPERTY_IN_TRASH"
    assert property_trash.live("p") == "(to_jsonb(p)->>'deleted_at') IS NULL"


def test_05_il_ledger_si_interroga_solo_dal_suo_package():
    testo = (ROOT / "property" / "lifecycle.py").read_text(encoding="utf-8")
    assert "communication_messages" not in testo
    assert "property_has_message_history" in testo
    from communication import repository
    firma = inspect.signature(repository.property_has_message_history)
    assert list(firma.parameters) == ["cur", "ctx", "property_id"]
    corpo = re.sub(r'"""[\s\S]*?"""', "", inspect.getsource(repository.property_has_message_history))
    assert not re.search(r"\b(UPDATE|INSERT|DELETE)\b", corpo)        # sola lettura


def test_05b_il_ledger_rifiuta_un_immobile_nel_cestino_all_unico_insert():
    """REVIEW 1 (R1): `insert_message` e' l'unico INSERT nel ledger e chiama la
    guardia di core (con lock) prima della INSERT; nessun altro .py scrive
    nel ledger."""
    from communication import repository
    corpo = inspect.getsource(repository.insert_message)
    guardia = corpo.index('core_refuse_if_property_in_trash(cur, prepared.get("property_id"), lock=True)')
    assert guardia < corpo.index("INSERT INTO communication_messages")
    scrittori = [rel for rel, src in _sorgenti()
                 if re.search(r"INSERT\s+INTO\s+communication_messages\b", src)]
    assert scrittori == ["communication/repository.py"], scrittori


def test_06_migration_086_valida_per_il_runner():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 086 resta valida; la 087 (attributi del sito) la segue.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: poi la 088 (ricezione degli invii del sito), che ora e' l'ultima.
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: poi la 089 (natura di pertinenza), che ora e' l'ultima.
    # SENTINELLA AGGIORNATA DA CESTINO-RICHIESTE-1: finestra spostata di uno, poi la 092 (Cestino richieste), che ora e' l'ultima.
    # SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1: finestra spostata di due, poi la 093 (PDF privato della stima, F07) e la 094 (ricevute degli invii pubblici, F04); la 094 e' ora l'ultima.
    assert tutte[-10].version == "086_delete_arch_2b2_property_trash_guards"
    assert tutte[-9].version == "087_catalogo_canonico_1_site_attributes"
    assert tutte[-9].down_available and not tutte[-9].non_transactional
    assert runner.validate_migration(tutte[-9]) == []
    assert tutte[-8].version == "088_catalogo_canonico_1b_site_inbox"
    # SENTINELLA AGGIORNATA DA CESTINO-CONTATTI-1: poi la 090 (Cestino contatti), che ora e' l'ultima.
    assert tutte[-7].version == "089_pertinenze_1_unit_nature"
    # SENTINELLA AGGIORNATA DA CESTINO-EDIFICI-1: poi la 091 (Cestino edifici), che ora e' l'ultima.
    assert tutte[-6].version == "090_cestino_contatti_1_contact_trash"
    assert tutte[-5].version == "091_cestino_edifici_1_building_trash"
    assert tutte[-4].version == "092_cestino_richieste_1_buy_request_trash"
    assert tutte[-3].version == "093_stima_private_pdf"
    # SENTINELLA AGGIORNATA DA SITE-IMPORT-1: la 095 (registro dell'importazione dal sito, site_import_records), additiva, e' ora l'ultima.
    assert tutte[-1].version == "095_site_import_ledger"
    assert tutte[-2].version == "094_public_submission_receipts"
