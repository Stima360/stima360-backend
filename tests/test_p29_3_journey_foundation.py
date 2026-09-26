"""P29-3B.0 / 3B.2A senza database: forma, insiemi chiusi, registry, sentinelle.

Il comportamento vero sta in `test_p29_3_journey_foundation_postgres.py`. Qui:
la migration come TESTO, il registry dei template (immutabile e senza
fallback), gli insiemi chiusi delle enum, la firma dell'unsubscribe come pura
funzione, e le sentinelle - nessun invio marketing, nessun cron nuovo, nessun
tick, `P29_2_0` intoccato.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "071_p29_3_journey_automation.sql"
DOWN = ROOT / "migrations" / "071_p29_3_journey_automation_down.sql"
SU = re.sub(r"--[^\n]*", "", UP.read_text(encoding="utf-8"))
GIU = re.sub(r"--[^\n]*", "", DOWN.read_text(encoding="utf-8"))


def _codice(modulo) -> str:
    albero = ast.parse(inspect.getsource(modulo))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            corpo = nodo.body
            if corpo and isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant) \
                    and isinstance(corpo[0].value.value, str):
                corpo[0].value.value = ""
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - LA MIGRATION COME TESTO
# ---------------------------------------------------------------------------

def test_01_la_071_e_valida_e_in_coda():
    from scripts import p26_migrate as runner
    trovate = {m.version: m for m in runner.discover_migrations()}
    m = trovate["071_p29_3_journey_automation"]
    assert runner.validate_migration(m) == [] and m.down_available
    numeri = sorted(x.number for x in trovate.values())
    # SENTINELLA AGGIORNATA DA A30-1: la 072 e' il modello dell'Agenda CRM
    # (`appointments`, `appointment_events`), approvato dal GATE A30-0. Si
    # nomina invece di smettere di guardare: qualunque ALTRA migration
    # comparisse farebbe ancora fallire questo test.
    # SENTINELLA AGGIORNATA DA A30-2P: la 073 ridefinisce due CHECK di
    # `appointments` per la facade LMC-15 (fonte `lmc15_facade`), approvata
    # dal GATE A30-2P FACADE DESIGN. Si nomina invece di smettere di
    # guardare: qualunque ALTRA migration comparisse farebbe ancora fallire.
    assert numeri[numeri.index(71) - 1] == 70 and numeri[-1] == 73
    assert len(numeri) == len(set(numeri))


def test_02_la_071_non_tocca_reason_code_ne_il_consenso():
    for vietato in ("communication_messages_reason_code_chk", "consent_events", "consent_notices",
                    "ALTER TABLE contacts"):
        assert vietato not in SU, vietato
    assert "reason_code_chk" not in GIU and "consent" not in GIU.lower().replace("-- non tocca", "")
    assert "INSERT INTO communication_journeys" not in SU.upper() and "INSERT INTO" not in SU.upper()


def test_03_la_down_ripristina_il_testo_della_065_non_della_064():
    def corpo(sql):
        i = sql.index("CREATE OR REPLACE FUNCTION communication_messages_guard")
        return re.search(r"AS \$fn\$(.*?)\$fn\$;", sql[i:], re.S).group(1)
    g065 = corpo((ROOT / "migrations" / "065_p29_service_lifecycle_parent.sql").read_text(encoding="utf-8"))
    g064 = corpo((ROOT / "migrations" / "064_p29_communication_foundation.sql").read_text(encoding="utf-8"))
    g_down = corpo(DOWN.read_text(encoding="utf-8"))
    assert g_down == g065 and g_down != g064


def test_04_la_matrice_dell_enrollment_e_scritta_nel_ddl():
    blocco = SU.split("comm_enroll_matrix_chk")[1].split("CONSTRAINT")[0]
    for stato in ("'active'", "'paused'", "'completed'", "'stopped'"):
        assert f"status = {stato}" in blocco
    assert "comm_enroll_awaiting_chk" in SU and "comm_enroll_stop_actor_chk" in SU
    assert "uq_comm_enroll_open_per_contact" in SU
    assert "UNIQUE (journey_id, trigger_message_id)" in SU
    assert "UNIQUE (agency_id, journey_id, stima_id)" in SU
    assert "UNIQUE (trigger_message_id)" not in SU.replace("UNIQUE (journey_id, trigger_message_id)", "")
    assert "send_timezone" in SU and "'Europe/Rome'" in SU
    assert "created_by_type" in SU and "activated_by_type" in SU


def test_05_il_legame_col_ledger_e_a_terne_e_immutabile():
    assert "num_nonnulls(enrollment_id, step_no, run_no) IN (0, 3)" in SU
    assert "uq_communication_messages_step_run" in SU and "uq_communication_messages_step_alive" in SU
    assert "status <> 'cancelled'" in SU
    for col in ("enrollment_id", "step_no", "run_no"):
        assert f"NEW.{col}" in SU and f"OLD.{col}" in SU


# ---------------------------------------------------------------------------
# B - ENUM E PRIORITA'
# ---------------------------------------------------------------------------

def test_06_la_priorita_degli_stop_e_deterministica_e_fissata():
    from communication import journey_enums as e
    assert e.STOP_PRIORITY == (
        "mandate_signed", "acquisition_linked", "inspection", "consultation_requested",
        "lead_closed", "contact_inactive", "consent_revoked", "consent_not_granted",
        "consent_inconsistent", "expired_on_resume", "operator")
    assert e.choose_stop_reason({"operator", "consent_revoked", "inspection"}) == "inspection"
    assert e.choose_stop_reason(["operator", "mandate_signed"]) == "mandate_signed"
    assert e.choose_stop_reason([]) is None
    # e coincidono con il CHECK della 071
    blocco = SU.split("comm_enroll_stop_reason_chk")[1].split("CONSTRAINT")[0]
    for r in e.STOP_PRIORITY:
        assert f"'{r}'" in blocco, r


def test_07_le_tre_ragioni_del_consenso_mappano_la_guardia():
    from communication.journey_enums import CONSENT_STOP_BY_GUARD_REASON
    from consent import enums as c
    assert CONSENT_STOP_BY_GUARD_REASON == {
        c.REASON_DENY_REVOKED: "consent_revoked",
        c.REASON_DENY_NEVER_GIVEN: "consent_not_granted",
        c.REASON_DENY_INCONSISTENT_STATE: "consent_inconsistent"}


# ---------------------------------------------------------------------------
# C - IL REGISTRY DEI TEMPLATE
# ---------------------------------------------------------------------------

#: LE IMPRONTE DELLE VERSIONI PUBBLICATE. Cambiare un renderer gia' registrato
#: fa fallire qui; aggiungere una versione nuova richiede una riga nuova.
#:
#: SENTINELLA AGGIORNATA DA P29-3D (collisione dichiarata). P29-3B non doveva
#: portare testo commerciale, e non ne ha portato: il registro conteneva il
#: solo template di prova. I cinque testi della sequenza della stima arrivano
#: con la fase che li ha approvati, e la garanzia che questo test da' non
#: cambia di natura - il registro e' CHIUSO e ogni renderer dentro e'
#: IMMUTABILE - guadagna cinque righe. Che quei testi dicano il vero, e cosa
#: non dicano, lo verifica `test_p29_3d_crm_journey.py`.
IMPRONTE = {
    ("registry_probe", 1): "87a60835b9a0afd9",
    ("stima_lead_m1", 1): "1d0616599b645684",
    ("stima_lead_m2", 1): "1e1c92f6b0faaa0c",
    ("stima_lead_m3", 1): "5a2adfdb541ec526",
    ("stima_lead_m4", 1): "b632a89875c4b924",
    ("stima_lead_m5", 1): "9cb8d18094d34f07",
}


def test_08_il_registry_e_versionato_immutabile_e_senza_fallback():
    from communication import templates
    from communication.exceptions import ValidationError
    assert set(templates.REGISTRY) == set(IMPRONTE)
    for (k, v), t in templates.REGISTRY.items():
        src = inspect.getsource(t.subject) + inspect.getsource(t.body)
        assert hashlib.sha256(src.encode()).hexdigest()[:16] == IMPRONTE[(k, v)], (k, v)
        assert (t.key, t.version) == (k, v)
    with pytest.raises(ValidationError):
        templates.get("registry_probe", 2)
    with pytest.raises(ValidationError):
        templates.get("m1", 1)
    assert isinstance(templates.REGISTRY, dict)


def test_09_un_template_marketing_richiede_unsubscribe_url():
    from communication import templates
    from communication.exceptions import ValidationError
    t = templates.get("registry_probe", 1)
    assert t.communication_type == "marketing" and "unsubscribe_url" in t.required_fields
    with pytest.raises(ValidationError) as info:
        templates.render("registry_probe", 1, {"contact_first_name": "Mario", "agency_name": "A"})
    assert "unsubscribe_url" in str(info.value)
    s, b = templates.render("registry_probe", 1, {"contact_first_name": "Mario", "agency_name": "A",
                                                   "unsubscribe_url": "https://x/u?t=1"})
    assert "https://x/u?t=1" in b and s
    with pytest.raises(ValueError):
        templates.Template(key="x", version=1, channel="email", communication_type="marketing",
                           required_fields=frozenset({"agency_name"}), subject=lambda c: "s", body=lambda c: "b")


def test_10_nessun_testo_commerciale_definitivo_in_questo_blocco():
    from communication import templates
    chiavi = {k for k, _ in templates.REGISTRY}
    assert not chiavi & {"m1", "m2", "m3", "m4", "m5"}


# ---------------------------------------------------------------------------
# D - L'UNSUBSCRIBE COME FUNZIONE PURA
# ---------------------------------------------------------------------------

def test_11_il_token_e_firmato_e_non_enumerabile(monkeypatch):
    from communication import unsubscribe as u
    monkeypatch.setenv(u.SECRET_ENV, "una-chiave-di-prova-lunga-almeno-trentadue-caratteri")
    quando = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t = u.issue(7, 42, issued_at=quando)
    assert u.verify(t) == (7, 42, int(quando.timestamp()))
    assert "contact_id=" not in u.unsubscribe_url("https://app", 7, 42, issued_at=quando)
    assert u.unsubscribe_url("https://app/", 7, 42, issued_at=quando).startswith(
        "https://app/api/public/communication/unsubscribe?t=")
    payload, firma = t.split(".")
    assert u.verify(f"{payload}.{firma[:-1]}A") is None
    # un byte in mezzo al payload (non l'ultimo carattere base64, i cui bit
    # di coda non portano informazione): la firma non torna piu'
    meta = len(payload) // 2
    alterato = payload[:meta] + ("A" if payload[meta] != "A" else "B") + payload[meta + 1:]
    assert u.verify(f"{alterato}.{firma}") is None
    # chiave diversa, firma diversa
    monkeypatch.setenv(u.SECRET_ENV, "un-altra-chiave-di-prova-lunga-almeno-trentadue-car")
    assert u.verify(t) is None
    monkeypatch.setenv(u.SECRET_ENV, "corta")
    with pytest.raises(u.UnsubscribeNotConfigured):
        u.issue(7, 42)


def test_12_public_unsubscribe_e_un_origin_ammesso_e_l_insieme_resta_chiuso():
    from operator_auth import context
    assert context.SYSTEM_CONTEXT_ORIGINS == (
        "public_stima", "communication_dispatch", "owner_login", "public_unsubscribe")
    ctx = context.SystemAgencyContext(agency_id=3, origin="public_unsubscribe")
    assert ctx.require_agency() == 3 and ctx.user_id is None
    with pytest.raises(ValueError):
        context.SystemAgencyContext(agency_id=3, origin="public_journey")


def test_13_la_rotta_pubblica_non_e_dietro_l_autenticazione_operatore():
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    riga = [r for r in sorgente.splitlines() if "include_router(communication_public_router" in r]
    assert len(riga) == 1 and "Depends" not in riga[0]
    from communication.public_router import router
    assert {(m, r.path) for r in router.routes for m in r.methods if m != "HEAD"} == {
        ("GET", "/api/public/communication/unsubscribe"),
        ("POST", "/api/public/communication/unsubscribe")}


# ---------------------------------------------------------------------------
# E - IL SERVICE: forma
# ---------------------------------------------------------------------------

def test_14_nessuna_funzione_del_service_accetta_agency_id_e_l_attore_viene_da_ctx():
    from communication import journey_service as s
    for nome, f in inspect.getmembers(s, inspect.isfunction):
        if f.__module__ != s.__name__:
            continue
        parametri = inspect.signature(f).parameters
        assert "agency_id" not in parametri, nome
        assert not [p for p in parametri if p.endswith("operator_user_id") or p == "actor_user_id"], nome
    codice = _codice(s)
    assert "getattr(ctx, 'user_id'" in codice


def test_15_enqueue_accetta_la_terna_e_nessun_altro_campo_di_dispatch():
    from communication import repository, service
    p = inspect.signature(service.enqueue).parameters
    assert {"enrollment_id", "step_no", "run_no"} <= set(p)
    assert "status" not in p and "agency_id" not in p and "actor_type" not in p
    assert repository.INSERTABLE_COLUMNS[-3:] == ("enrollment_id", "step_no", "run_no")


# ---------------------------------------------------------------------------
# F - SENTINELLE
# ---------------------------------------------------------------------------

def test_16_nessun_invio_marketing_reale_e_nessun_tick():
    """Il blocco crea il posto delle journey, non le journey: nessun percorso
    applicativo accoda `m1..m5`, nessuno scandisce, nessuno avanza."""
    from communication import journey_repository, journey_service, templates, unsubscribe
    codice = "".join(_codice(m) for m in (journey_repository, journey_service, templates, unsubscribe))
    assert "dispatch_batch" not in codice and "provider" not in codice
    assert "reason_code='m1'" not in codice and 'reason_code="m1"' not in codice
    # SENTINELLA AGGIORNATA DA P29-3C: il tick ORA esiste, ed e' il mandato
    # di quella fase. Cio' che questa continua a garantire e' DOVE vive - in
    # `journey_tick.py`, un modulo suo - e che i quattro moduli della
    # fondazione restino quello che erano: nessuno di loro scandisce, avanza
    # o spedisce. `advance_enrollment` e' una UPDATE condizionata nel
    # repository, chiamata dal motore: il verbo sta qui, la decisione no.
    for vietato in ("def tick", "def scan", "claim_due", "advisory_lock"):
        assert vietato not in codice, vietato
    assert "def advance" not in _codice(journey_service)
    from communication import journey_tick
    assert "def tick" in _codice(journey_tick)
    # l'unico `enqueue` di journey_service e' quello del ledger, e non c'e'
    assert "enqueue(" not in _codice(journey_service)


def test_17_nessun_cron_nuovo_e_il_runner_toccato_e_quello_dichiarato():
    """SENTINELLA AGGIORNATA DA P29-3E (collisione dichiarata).

    P29-3B non doveva toccare nessun runner, e non ne tocco' nessuno. P29-3E
    ne tocca uno, dichiarato per nome, per far girare il motore prima del
    dispatch nello stesso giro. La garanzia resta: nessun runner cambia
    senza che una fase lo abbia dichiarato, e di cron nuovi non ne nasce
    nessuno.
    """
    from tests.p29_3e_diff import RUNNER_TOCCATO

    righe = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain",
                            "--", "run_*.py"],
                           cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    toccati = {r[3:].strip() for r in righe}
    assert toccati <= {RUNNER_TOCCATO}, sorted(toccati)


def test_18_il_dispatcher_non_conosce_le_journey():
    from communication import dispatcher
    codice = _codice(dispatcher)
    for vietato in ("enrollment", "journey", "assisted", "await_operator"):
        assert vietato not in codice, vietato


def test_19_i_file_toccati_sono_quelli_dichiarati_e_P29_2_0_resta_fuori():
    """L'inventario di P29-3B e' scritto a mano in `p29_3b_diff` e vale
    PRIMA e DOPO il commit: prima, il working tree non puo' contenere nulla
    oltre l'inventario; dopo, ogni file dell'inventario e' nell'indice.
    `P29_2_0_COMMUNICATION_DESIGN.md` non entra nell'indice in nessun caso."""
    from tests.p29_3b_diff import FILE_MODIFICATI, FILE_NUOVI
    # SENTINELLA AGGIORNATA DA P29-3C: una fase successiva ha il suo
    # inventario, dichiarato allo stesso modo. Questo test continua a
    # pretendere che nel working tree non ci sia NIENTE che nessuna delle due
    # fasi abbia dichiarato - non smette di guardare, guarda l'unione.
    # E DA P29-3D, che si dichiara allo stesso modo: l'unione cresce di una
    # fase, il verso del controllo resta identico in entrambe le direzioni.
    from tests.p29_3c_diff import FILE_MODIFICATI as MOD_3C, FILE_NUOVI as NUOVI_3C
    from tests.p29_3d_diff import FILE_MODIFICATI as MOD_3D, FILE_NUOVI as NUOVI_3D
    # P29-3G si dichiara allo stesso modo: l'unione cresce di una fase,
    # il verso del controllo no.
    from tests.p29_3e_diff import FILE_MODIFICATI as MOD_3E, FILE_NUOVI as NUOVI_3E
    from tests.p29_3g_diff import FILE_MODIFICATI as MOD_3G, FILE_NUOVI as NUOVI_3G
    # FLOW GLOBAL SECURITY si dichiara allo stesso modo: l'unione cresce
    # di una fase, il verso del controllo no. Non e' una fase di P29 - e'
    # il catalogo globale di FLOW - ma questa sentinella guarda il working
    # tree intero, quindi la collisione c'e' e va nominata.
    from tests.flow_global_security_diff import (
        FILE_MODIFICATI as MOD_FGS, FILE_NUOVI as NUOVI_FGS)
    # A30-1 si dichiara allo stesso modo: l'unione cresce di una fase, il
    # verso del controllo no. Non e' una fase di P29 - e' l'Agenda CRM - ma
    # questa sentinella guarda il working tree intero.
    # SENTINELLA AGGIORNATA DA A30-2: l'Agenda si dichiara in due file
    # (A30-1 + A30-2), letti come un'unica fase.
    from tests.a30_1_diff import FILE_MODIFICATI as MOD_A30_1, FILE_NUOVI as NUOVI_A30_1
    from tests.a30_2_diff import FILE_MODIFICATI as MOD_A30_2, FILE_NUOVI as NUOVI_A30_2
    # SENTINELLA AGGIORNATA DA A30-2P: terza dichiarazione dell'Agenda.
    from tests.a30_2p_diff import FILE_MODIFICATI as MOD_A30_2P, FILE_NUOVI as NUOVI_A30_2P
    # SENTINELLA AGGIORNATA DAL MOUNT A30: quarta dichiarazione dell'Agenda.
    from tests.a30_mount_diff import FILE_MODIFICATI as MOD_A30_M, FILE_NUOVI as NUOVI_A30_M
    # A30-4: la UI Agenda (OS Shell), sesta dichiarazione.
    from tests.a30_4_diff import FILE_MODIFICATI as MOD_A30_4, FILE_NUOVI as NUOVI_A30_4
    NUOVI_A30 = NUOVI_A30_1 | NUOVI_A30_2 | NUOVI_A30_2P | NUOVI_A30_M | NUOVI_A30_4
    MOD_A30 = MOD_A30_1 | MOD_A30_2 | MOD_A30_2P | MOD_A30_M | MOD_A30_4

    righe = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain"],
                           cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    # Un file NUOVO e' tale sia quando e' ancora `??` sia quando e' gia' in
    # stage (`A`): distinguere i due stati farebbe cambiare verdetto a questo
    # test fra `git add` e `git commit`, che e' uno stato normale e non un
    # difetto. Cio' che resta sorvegliato e' l'insieme, non il momento.
    nuovi = {r[3:].strip() for r in righe if r[:2].strip() in ("??", "A")}
    modificati = {r[3:].strip() for r in righe if r[:2].strip() not in ("??", "A")}
    tracciati = set(subprocess.run(["git", "--no-optional-locks", "ls-files"],
                                   cwd=ROOT, capture_output=True, text=True).stdout.split())

    dichiarati_nuovi = (FILE_NUOVI | NUOVI_3C | NUOVI_3D | NUOVI_3E | NUOVI_3G
                        | NUOVI_FGS | NUOVI_A30)
    dichiarati_modificati = (FILE_MODIFICATI | MOD_3C | NUOVI_3C | MOD_3D | NUOVI_3D
                             | MOD_3E | NUOVI_3E | MOD_3G | NUOVI_3G
                             | MOD_FGS | NUOVI_FGS | MOD_A30 | NUOVI_A30)
    assert nuovi - dichiarati_nuovi == {"P29_2_0_COMMUNICATION_DESIGN.md"}, \
        sorted(nuovi - dichiarati_nuovi)
    assert modificati <= dichiarati_modificati, sorted(modificati - dichiarati_modificati)
    for nome in FILE_NUOVI:
        assert (ROOT / nome).exists() and (nome in tracciati or nome in nuovi), nome
    for nome in FILE_MODIFICATI:
        assert nome in tracciati, nome
    assert "P29_2_0_COMMUNICATION_DESIGN.md" not in tracciati
    assert (ROOT / "P29_2_0_COMMUNICATION_DESIGN.md").exists()
