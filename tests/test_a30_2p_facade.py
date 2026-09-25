"""A30-2P - facade LMC-15: sentinella strutturale sugli scrittori legacy.

Dopo la facade l'unico scrittore logico dei sopralluoghi e' l'Agenda
(`appointments` -> `stima_inspections`). Le funzioni pubbliche di scrittura
del repository LMC-15 restano per compatibilita' e per i test, ma NESSUN
codice runtime deve chiamarle direttamente: altrimenti tornerebbe un secondo
scrittore che scrive `stima_inspections` senza `appointments`.

Unica eccezione ammessa: `acquisition/service.py` (oggi delega alla facade e
non le chiama piu'; se tornasse a farlo, resterebbe l'unico punto). Esclusi:
`tests/` e la definizione stessa in `acquisition/repository.py`.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY = frozenset({"create_inspection", "create_completed_inspection",
                    "complete_inspection", "cancel_inspection"})
MODULO = "acquisition.repository"
AMMESSI = frozenset({"acquisition/service.py"})
ESCLUSI_PREFISSI = ("tests/", ".git/", ".venv/", "venv/", "node_modules/")


def _modulo_di(percorso: Path) -> str:
    rel = percorso.relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)


def _risolvi(nodo: ast.ImportFrom, modulo_corrente: str) -> str | None:
    if nodo.level == 0:
        return nodo.module
    pacchetto = modulo_corrente.split(".")[:-nodo.level]
    return ".".join(pacchetto + ([nodo.module] if nodo.module else []))


def chiamate_legacy(sorgente: str, modulo_corrente: str) -> list[str]:
    """Le chiamate dirette alle scritture legacy nel sorgente dato."""
    albero = ast.parse(sorgente)
    alias_modulo, nomi_importati = set(), set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            for a in nodo.names:
                if a.name == MODULO:
                    alias_modulo.add(a.asname or a.name)
        elif isinstance(nodo, ast.ImportFrom):
            base = _risolvi(nodo, modulo_corrente)
            for a in nodo.names:
                if base == MODULO and a.name in LEGACY:
                    nomi_importati.add(a.asname or a.name)
                if base and f"{base}.{a.name}" == MODULO:
                    alias_modulo.add(a.asname or a.name)
    trovate = []
    for nodo in ast.walk(albero):
        if not isinstance(nodo, ast.Call):
            continue
        f = nodo.func
        if (isinstance(f, ast.Attribute) and f.attr in LEGACY
                and isinstance(f.value, ast.Name) and f.value.id in alias_modulo):
            trovate.append(f"{f.value.id}.{f.attr}")
        elif isinstance(f, ast.Name) and f.id in nomi_importati:
            trovate.append(f.id)
    return trovate


def test_01_il_rilevatore_riconosce_ogni_forma_di_chiamata():
    casi = {
        "from acquisition import repository\nrepository.create_inspection(1)": 1,
        "from acquisition import repository as r\nr.cancel_inspection(1)": 1,
        "import acquisition.repository\nacquisition.repository.x": 0,
        "from acquisition.repository import complete_inspection as c\nc(1)": 1,
        "from acquisition import repository as r\nr.complete_inspection_in(cur, 1)": 0,
        "from acquisition import repository as r\nr.create_completed_inspection(1)": 1,
    }
    for sorgente, attese in casi.items():
        assert len(chiamate_legacy(sorgente, "pacchetto.modulo")) == attese, sorgente
    # import relativo dentro il pacchetto acquisition
    assert chiamate_legacy("from . import repository\nrepository.create_inspection(1)",
                           "acquisition.altro") == ["repository.create_inspection"]


def test_02_nessun_codice_runtime_chiama_le_scritture_legacy():
    violazioni = {}
    for percorso in ROOT.rglob("*.py"):
        rel = percorso.relative_to(ROOT).as_posix()
        if rel.startswith(ESCLUSI_PREFISSI) or rel == "acquisition/repository.py":
            continue
        if rel in AMMESSI:
            continue
        try:
            sorgente = percorso.read_text(encoding="utf-8")
            trovate = chiamate_legacy(sorgente, _modulo_di(percorso))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if trovate:
            violazioni[rel] = trovate
    assert violazioni == {}, violazioni


def test_03_oggi_neanche_il_service_lmc15_le_chiama():
    """Il service delega alla facade: l'eccezione ammessa oggi e' vuota."""
    sorgente = (ROOT / "acquisition" / "service.py").read_text(encoding="utf-8")
    assert chiamate_legacy(sorgente, "acquisition.service") == []
    assert "from appointments import lmc15_facade" in sorgente


def test_04_le_api_legacy_esistono_ancora_con_la_stessa_firma():
    import inspect

    from acquisition import repository
    for nome in LEGACY:
        firma = inspect.signature(getattr(repository, nome))
        assert list(firma.parameters)[0] == "agency_id", nome
