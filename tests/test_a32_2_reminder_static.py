"""A32-2 - le regole strutturali, lette dal codice (nessun database).

Il solo percorso verso un invio e':

    appointment_reminders -> communication.service.enqueue
                          -> communication dispatcher (claim, revalida) -> provider

e queste sentinelle lo tengono tale: nessun provider o SMTP nel package, il
planner scrive solo con `enqueue`, l'origin e' esattamente il suo, la rotta ha
la soglia del dispatch e nessun input che decida, `location_text` non si legge,
nessuna 080, il cron chiama i promemoria prima del dispatch e mai per WhatsApp.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "appointment_reminders"


def _sorgente(relativo: str) -> str:
    return (ROOT / relativo).read_text(encoding="utf-8")


def _codice(relativo: str) -> str:
    """Il sorgente senza docstring ne' commenti (le stringhe SQL restano)."""
    sorgente = _sorgente(relativo)
    righe = sorgente.splitlines()
    via: set[int] = set()
    for nodo in ast.walk(ast.parse(sorgente)):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and nodo.body and isinstance(nodo.body[0], ast.Expr) \
                and isinstance(nodo.body[0].value, ast.Constant) \
                and isinstance(nodo.body[0].value.value, str):
            via |= set(range(nodo.body[0].lineno - 1, nodo.body[0].end_lineno))
    return "\n".join(r.split("  #", 1)[0] if not r.lstrip().startswith("#") else ""
                     for i, r in enumerate(righe) if i not in via)


def _import(relativo: str) -> set[str]:
    nomi = set()
    for nodo in ast.walk(ast.parse(_sorgente(relativo))):
        if isinstance(nodo, ast.Import):
            nomi |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            nomi.add(("." * nodo.level) + (nodo.module or ""))
    return nomi


def test_s01_il_package_ha_esattamente_i_moduli_attesi():
    assert {p.name for p in PACCHETTO.glob("*.py")} == {
        "__init__.py", "policy.py", "template.py",                       # A32-1
        "repository.py", "planner.py", "revalidation.py", "router.py"}   # A32-2


def test_s02_nessun_provider_nessuno_smtp_nessuna_rete_nel_package():
    for file in PACCHETTO.glob("*.py"):
        relativo = f"appointment_reminders/{file.name}"
        importati = _import(relativo)
        for nome in importati:
            assert "providers" not in nome and "email_smtp" not in nome, (file.name, nome)
            assert nome.split(".")[0] not in {"smtplib", "requests", "socket", "httpx",
                                              "urllib", "psycopg2"}, (file.name, nome)
        codice = _codice(relativo)
        for vietato in ("smtplib", "invia_mail", "providers", ".send(", "get_connection",
                        "psycopg2" + ".connect", "commit(", "dispatch_batch", "adapter_per"):
            assert vietato not in codice, (file.name, vietato)


def test_s03_il_provider_si_raggiunge_solo_dal_dispatcher():
    """Nessun modulo fuori da `communication/` importa un provider (A32-2 non
    ne aggiunge nessuno); e nel dispatcher la revalida precede il provider."""
    for file in PACCHETTO.glob("*.py"):
        assert "communication.providers" not in _sorgente(f"appointment_reminders/{file.name}")
    codice = _codice("communication/dispatcher.py")
    gate = codice.index("revalida_promemoria.revalidate(ctx, message)")
    assert gate < codice.index("risultato = provider.send(message)")
    assert codice.count("revalida_promemoria.revalidate(") == 1


def test_s04_il_ramo_del_dispatcher_e_stretto():
    codice = _codice("communication/dispatcher.py")
    assert ('if ragione is None and message.get("reason_code") == REASON_APPOINTMENT_REMINDER:'
            in codice)
    # il gate del consenso e' quello di prima, e resta il primo
    assert codice.index("ragione = _consenso_nega(ctx, message)") < codice.index(
        "revalida_promemoria.revalidate(")
    assert "if message[\"communication_type\"] != TYPE_MARKETING:" in codice
    # nessun'altra parte del dispatcher conosce i promemoria
    assert codice.count("REASON_APPOINTMENT_REMINDER") == 2        # import + ramo
    assert codice.count("revalida_promemoria") == 2                # import + chiamata


def test_s05_il_planner_scrive_solo_con_enqueue_e_i_campi_sono_quelli():
    codice = _codice("appointment_reminders/planner.py")
    assert codice.count("communication_service.enqueue(") == 1
    for vietato in ("INSERT", "UPDATE", "DELETE", "insert_message", "finalize_",
                    "claim_due", "commit=True"):
        assert vietato not in codice, vietato
    for atteso in ("channel=CHANNEL_EMAIL", "communication_type=TYPE_SERVICE",
                   "mode=MODE_AUTOMATIC", "reason_code=REASON_APPOINTMENT_REMINDER",
                   "idempotency_key=chiave", "template_key=template.TEMPLATE_KEY",
                   "template_version=template.TEMPLATE_VERSION",
                   'destination_snapshot=contatto["email"]'):
        assert atteso in codice, atteso
    from communication import enums
    assert (enums.CHANNEL_EMAIL, enums.TYPE_SERVICE, enums.MODE_AUTOMATIC,
            enums.REASON_APPOINTMENT_REMINDER) == ("email", "service", "automatic",
                                                    "appointment_reminder")
    # nessun consenso marketing interrogato per un servizio
    assert "consent" not in codice and "can_send_marketing" not in codice


def test_s06_l_origin_e_esattamente_appointment_reminder():
    from appointment_reminders import planner
    from operator_auth import context
    assert planner.REMINDER_ORIGIN == "appointment_reminder"
    assert "appointment_reminder" in context.SYSTEM_CONTEXT_ORIGINS
    assert context.SYSTEM_CONTEXT_ORIGINS[-1] == "appointment_reminder"
    assert context.SYSTEM_CONTEXT_ORIGINS.count("appointment_reminder") == 1
    assert "appointment_reminders" not in context.SYSTEM_CONTEXT_ORIGINS
    codice = _codice("appointment_reminders/planner.py")
    assert "SystemAgencyContext(agency_id=ctx_operatore.require_agency()," in codice
    assert "communication_dispatch" not in codice


def test_s07_la_rotta_ha_la_soglia_del_dispatch_e_nessun_input_che_decida():
    from appointment_reminders.router import ReminderTickRequest
    codice = _codice("appointment_reminders/router.py")
    assert 'ctx: OperatorContext = Depends(require_dispatch_context)' in codice
    assert '@router.post("/reminders/tick")' in codice
    assert 'prefix="/api/communication"' in codice
    assert set(ReminderTickRequest.model_fields) == {"limit"}
    assert ReminderTickRequest.model_config.get("extra") == "forbid"
    for vietato in ("agency_id", "now=", "appointment_id", "recipient", "Query(", "Path("):
        assert vietato not in codice, vietato
    riga = [r for r in _sorgente("main.py").splitlines()
            if "include_router(appointment_reminders_router" in r]
    assert riga == ["app.include_router(appointment_reminders_router, "
                    "dependencies=[Depends(require_authenticated_operator)])"]


def test_s08_location_text_note_telefono_mai_letti():
    from appointment_reminders import repository
    for colonne in (repository.APPOINTMENT_COLUMNS, repository.CONTACT_COLUMNS,
                    repository.PROPERTY_COLUMNS):
        for vietata in ("location_text", "notes", "outcome_note", "phone"):
            assert vietata not in colonne
    for file in ("repository.py", "planner.py", "revalidation.py", "router.py"):
        codice = _codice(f"appointment_reminders/{file}")
        for vietato in ("location_text", "outcome_note", "phone", ".notes", "SELECT *",
                        "a.*", "c.*", "p.*"):
            assert vietato not in codice, (file, vietato)
    assert repository.PROPERTY_COLUMNS == ("address", "civic_number", "city")
    from appointment_reminders import template
    assert tuple(template.ADDRESS_FIELDS) == repository.PROPERTY_COLUMNS


def test_s09_il_repository_e_sempre_dell_agenzia():
    codice = _codice("appointment_reminders/repository.py")
    assert codice.count("a.agency_id = %(agency_id)s") == 2
    assert "c.agency_id = a.agency_id" in codice and "p.agency_id = a.agency_id" in codice
    assert "ORDER BY a.start_at ASC, a.id ASC" in codice


def test_s10_nessuna_migration_080():
    migrazioni = sorted(p.name for p in (ROOT / "migrations").glob("*.sql"))
    # SENTINELLA AGGIORNATA DA CRM-OPS-2: A32-2 non ha creato migration, e
    # resta vero. La 080 che ora esiste e' quella di CRM-OPS-2 (form
    # Immobili), nominata qui per intero: qualunque ALTRA 080, o qualunque
    # migration oltre, farebbe ancora fallire.
    assert [m for m in migrazioni if m.startswith("080")] == [
        "080_crm_ops_2_property_form.sql", "080_crm_ops_2_property_form_down.sql"]
    # SENTINELLA AGGIORNATA DA CRM-OPS-3: la 081 (Acquisizioni) e' ora l'ultima,
    # nominata per intero: qualunque ALTRA 081, o qualunque migration oltre,
    # farebbe ancora fallire.
    assert [m for m in migrazioni if m.startswith("081")] == [
        "081_crm_ops_3_acquisitions.sql", "081_crm_ops_3_acquisitions_down.sql"]
    # SENTINELLA AGGIORNATA DA CRM-OPS-4: la 082 (storico interazioni
    # dell'immobile, `activities.property_id`) e' ora l'ultima, nominata per
    # intero: qualunque ALTRA 082, o qualunque migration oltre, farebbe ancora
    # fallire.
    assert [m for m in migrazioni if m.startswith("082")] == [
        "082_crm_ops_4_property_interactions.sql", "082_crm_ops_4_property_interactions_down.sql"]
    # SENTINELLA AGGIORNATA DA CENSIMENTO-1: la 083 (edifici e unita' censite)
    # e' ora l'ultima, nominata per intero: qualunque ALTRA 083, o qualunque
    # migration oltre, farebbe ancora fallire.
    assert [m for m in migrazioni if m.startswith("083")] == [
        "083_censimento_1_buildings_units.sql", "083_censimento_1_buildings_units_down.sql"]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: la 084 (cancelled_kind,
    # created_by_mistake), additiva, e' ora l'ultima, nominata per intero.
    assert [m for m in migrazioni if m.startswith("084")] == [
        "084_delete_arch_1a_mistakes.sql", "084_delete_arch_1a_mistakes_down.sql"]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: la 085 (Cestino Immobili), additiva, e' ora
    # l'ultima, nominata per intero.
    assert [m for m in migrazioni if m.startswith("085")] == [
        "085_delete_arch_2b1_property_trash.sql", "085_delete_arch_2b1_property_trash_down.sql"]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B2: la 086 (guardie del Cestino), additiva, e' ora l'ultima.
    assert [m for m in migrazioni if m.startswith("086")] == [
        "086_delete_arch_2b2_property_trash_guards.sql", "086_delete_arch_2b2_property_trash_guards_down.sql"]
    assert migrazioni[-1] == "086_delete_arch_2b2_property_trash_guards_down.sql"
    assert [m for m in migrazioni if m.startswith("079")] == [
        "079_a32_1_appointment_reminders.sql", "079_a32_1_appointment_reminders_down.sql"]


def test_s11_la_chiave_non_contiene_l_email():
    import inspect

    from appointment_reminders import planner, policy
    assert list(inspect.signature(policy.occurrence_key).parameters) == [
        "appointment_id", "start_at", "offset"]
    assert "idempotency_key=chiave" in _codice("appointment_reminders/planner.py")
    assert "chiave = policy.occurrence_key(appuntamento[\"id\"], appuntamento[\"start_at\"])" \
        in _codice("appointment_reminders/planner.py")
    meta = planner.metadata_for(5, __import__("datetime").datetime(
        2031, 1, 1, 10, tzinfo=__import__("datetime").timezone.utc))
    assert set(meta) == {"kind", "appointment_id", "offset", "occurrence_key", "start_epoch"}


def test_s12_il_cron_chiama_i_promemoria_prima_del_dispatch_e_solo_per_email():
    codice = _codice("run_communication_dispatch_cron.py")
    run_once = codice[codice.index("def run_once"):codice.index("def main")]
    assert (run_once.index("_journey_tick(config, sessione)")
            < run_once.index("_reminder_tick(config, sessione)")
            < run_once.index("/api/communication/dispatch"))
    assert "if config.channel == CANALE_PROMEMORIA:" in run_once
    assert 'CANALE_PROMEMORIA = "email"' in codice
    assert codice.count("/api/communication/reminders/tick") == 1
    # nessun segreto nuovo
    assert set(re.findall(r'"(COMMUNICATION_[A-Z_]+)"', codice)) <= {
        "COMMUNICATION_DISPATCH_BASE_URL", "COMMUNICATION_DISPATCH_EMAIL",
        "COMMUNICATION_DISPATCH_PASSWORD", "COMMUNICATION_DISPATCH_CHANNEL",
        "COMMUNICATION_DISPATCH_LIMIT", "COMMUNICATION_CONNECT_TIMEOUT_SECONDS",
        "COMMUNICATION_READ_TIMEOUT_SECONDS", "COMMUNICATION_JOURNEY_TICK_LIMIT"}
    # resta un client HTTP: nessun import del dominio
    for vietato in ("appointment_reminders", "communication.", "psycopg2"):
        assert vietato not in "\n".join(
            r for r in codice.splitlines() if r.startswith(("import", "from")))


def test_s13_la_revalida_copre_ogni_controllo_nell_ordine():
    codice = _codice("appointment_reminders/revalidation.py")
    decide = codice[codice.index("def decide"):codice.index("def revalidate")]
    ordine = ["_metadata_validi(message)", 'message.get("agency_id") != agency_id',
              "letto is None", "REASON_OCCURRENCE_CHANGED", "REASON_CONTACT_CHANGED",
              "policy.ineligibility_reason(", "REASON_DESTINATION_CHANGED",
              "policy.send_decision(", "policy.MIN_LEAD", "policy.in_window(now)",
              'message.get("idempotency_key") != chiave']
    posizioni = [decide.index(p) for p in ordine]
    assert posizioni == sorted(posizioni)


def test_s14_inventario_chiuso_contro_git_status():
    from tests import a32_2_diff
    righe = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain",
                            "--untracked-files=all"], cwd=ROOT, capture_output=True,
                           text=True).stdout.splitlines()
    protetti = {"P29_2_0_COMMUNICATION_DESIGN.md", "mac_hashes_tmp.txt"}
    toccati = {r[3:] for r in righe} - protetti
    if not toccati:            # albero pulito (dopo il commit): nulla da confrontare
        return
    nuovi = {r[3:] for r in righe if r.startswith("??")} - protetti
    modificati = {r[3:] for r in righe if not r.startswith("??")}
    assert nuovi <= a32_2_diff.FILE_NUOVI, nuovi - a32_2_diff.FILE_NUOVI
    assert modificati <= a32_2_diff.FILE_MODIFICATI, modificati - a32_2_diff.FILE_MODIFICATI


def test_s15_main_aggiunge_solo_le_righe_dichiarate():
    from tests import a32_2_diff
    diff = subprocess.run(["git", "--no-optional-locks", "diff", "--unified=0", "--",
                           "main.py"], cwd=ROOT, capture_output=True, text=True).stdout
    righe = [r for r in diff.splitlines() if r[:1] in "+-" and not r.startswith(("+++", "---"))]
    if not righe:
        return
    assert not [r for r in righe if r.startswith("-")]
    assert {r[1:].strip() for r in righe if r[1:].strip()} == a32_2_diff.RIGHE_MAIN


def test_s16_i_file_protetti_restano_fuori():
    righe = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain"],
                           cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    for protetto in ("P29_2_0_COMMUNICATION_DESIGN.md", "mac_hashes_tmp.txt"):
        assert not [r for r in righe if r.endswith(protetto) and not r.startswith("??")]
