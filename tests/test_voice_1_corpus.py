"""STIMA Voice Fase 1 - il corpus: fornitore finto -> contratto ->
pianificatore -> politica, caso per caso, con le soglie GO/NO-GO della
roadmap. Stampa il riepilogo per gruppo (`-s`)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import pytest
from pydantic import ValidationError

from voice.fake_provider import FakeVoiceProvider
from voice.planner import build_plan, execution_order
from voice.policy import Facts, RefFacts, Settings, decide
from voice.schemas import parse_plan_output, plan_json_schema

from tests.voice_corpus.corpus import cases

CASI = cases()
SOGLIE = {"clear": 0.95, "multi": 0.95, "ambiguous": 1.0, "blocked": 1.0, "mistake": 1.0, "modes": 1.0}


def _facts(raw: dict) -> Facts:
    return Facts(
        refs={(int(k.split(":")[0]), k.split(":")[1]): RefFacts(v[0], bool(v[1])) for k, v in raw.get("refs", {}).items()},
        duplicate_visible={int(k): v for k, v in raw.get("duplicate_visible", {}).items()},
        duplicate_hidden=frozenset(raw.get("duplicate_hidden", [])),
        similar_building=frozenset(raw.get("similar_building", [])),
        cadastral_duplicate=frozenset(raw.get("cadastral_duplicate", [])),
        agenda_conflict=frozenset(raw.get("agenda_conflict", [])),
        actor_may_assign=bool(raw.get("actor_may_assign", False)),
    )


def _settings(raw: dict) -> Settings:
    return Settings(mode=raw.get("mode", "auto"), hidden_duplicate_check=raw.get("hidden_duplicate_check", True))


def _run(caso: dict) -> list[str]:
    """Esegue un caso; restituisce l'elenco delle discrepanze (vuoto = passa)."""
    provider = FakeVoiceProvider()
    provider.register(caso["transcript"], caso["output"], audio_label=caso["id"])
    now = datetime.fromisoformat(caso["now"])
    atteso = caso["expect"]
    trascritto = provider.transcribe(caso["id"].encode(), mime_type="audio/mp4")
    grezzo = provider.interpret(trascritto, schema=plan_json_schema(), recorded_at=now)
    try:
        output = parse_plan_output(grezzo)
    except ValidationError:
        return [] if atteso.get("schema_error") else ["output rifiutato dallo schema"]
    if atteso.get("schema_error"):
        return ["lo schema doveva rifiutare l'output"]
    piano = build_plan(output, trascritto, now)
    decisione = decide(piano, _facts(caso["facts"]), _settings(caso["settings"]))
    errori = []
    ottenute = [s.decision for s in decisione.steps]
    if ottenute != atteso["decisions"]:
        errori.append(f"decisioni {ottenute} != {atteso['decisions']} ({[s.reasons for s in decisione.steps]})")
    for ordinale, motivi in atteso.get("reasons", {}).items():
        reali = decisione.of(int(ordinale)).reasons
        if not set(motivi) <= set(reali):
            errori.append(f"passo {ordinale}: motivi {reali} non contengono {motivi}")
    for ordinale, kind in atteso.get("record_kind", {}).items():
        if piano.step(int(ordinale)).payload.get("record_kind") != kind:
            errori.append(f"passo {ordinale}: record_kind {piano.step(int(ordinale)).payload.get('record_kind')} != {kind}")
    for ordinale, iso in atteso.get("start_at", {}).items():
        reale = piano.step(int(ordinale)).start_at
        if reale is None or reale != datetime.fromisoformat(iso):
            errori.append(f"passo {ordinale}: start_at {reale} != {iso}")
    for ordinale, campi in atteso.get("fields", {}).items():
        payload = piano.step(int(ordinale)).payload
        for k, v in campi.items():
            reale = payload.get(k)
            if isinstance(reale, datetime):
                reale = reale.isoformat()
            if reale != v:
                errori.append(f"passo {ordinale}: {k}={reale!r} != {v!r}")
    if "order" in atteso and execution_order(piano) != atteso["order"]:
        errori.append("ordine di esecuzione diverso")
    if "count" in atteso and len([s for s in decisione.steps if s.decision != "blocked"]) != atteso["count"]:
        errori.append("numero di passi eseguibili diverso")
    if "clarifications" in atteso and len(piano.clarifications) != atteso["clarifications"]:
        errori.append("chiarimenti del modello persi")
    if provider.calls != [("transcribe", caso["id"]), ("interpret", trascritto)]:
        errori.append("piu' chiamate al fornitore del previsto")
    return errori


@pytest.mark.parametrize("caso", CASI, ids=[c["id"] for c in CASI])
def test_corpus_case(caso):
    errori = _run(caso)
    assert not errori, "\n".join(errori)


def test_corpus_size_and_groups():
    gruppi = defaultdict(int)
    for c in CASI:
        gruppi[c["group"]] += 1
    assert len(CASI) >= 150, len(CASI)
    assert gruppi["multi"] >= 20 and gruppi["ambiguous"] >= 30 and gruppi["blocked"] >= 10


def test_corpus_thresholds_go_no_go(capsys):
    """Le soglie della roadmap (Fase 1): >= 95% sui casi chiari e multipli,
    100% di domande sugli ambigui, 100% di blocchi, 0 output fuori schema
    accettati. Si misura, non si assume."""
    per_gruppo: dict[str, list[bool]] = defaultdict(list)
    for c in CASI:
        per_gruppo[c["group"]].append(not _run(c))
    righe = []
    for gruppo, esiti in sorted(per_gruppo.items()):
        quota = sum(esiti) / len(esiti)
        righe.append(f"{gruppo:10s} {sum(esiti):4d}/{len(esiti):<4d} {quota:6.1%}  soglia {SOGLIE[gruppo]:.0%}")
    with capsys.disabled():
        print("\nSTIMA Voice corpus:\n  " + "\n  ".join(righe))
    for gruppo, esiti in per_gruppo.items():
        assert sum(esiti) / len(esiti) >= SOGLIE[gruppo], gruppo


def test_fake_provider_failure_modes():
    p = FakeVoiceProvider()
    p.register("ciao", {"commands": []}, audio_label="a")
    p.fail_next = True
    with pytest.raises(Exception):
        p.transcribe(b"a", mime_type="audio/mp4")
    p.malformed_next = True
    with pytest.raises(ValidationError):
        parse_plan_output(p.interpret("ciao", schema={}, recorded_at=datetime.now().astimezone()))
    with pytest.raises(KeyError):
        p.interpret("mai registrato", schema={}, recorded_at=datetime.now().astimezone())
