"""CRM-OPS-4 - INCARICHI + STORICO INTERAZIONI: le prove senza database.

  K. Catalogo e confini: la 082 e' valida per il runner e l'ultima; i tipi
     d'interazione sono un sottoinsieme ESATTO del catalogo `activities`;
     nessuna tabella `mandates`, nessuna scrittura di incarichi da qui; le
     rotte stanno sotto `/api/property` (nessun mount nuovo in main.py).
  L. Shell eseguita (stub DOM, nessun database): menu, elenco con filtri,
     scheda Incarico, storico condiviso con la scheda Immobile, dialog rapido,
     data/ora di Roma, mobile.

Il giro su PostgreSQL vero e' in `tests/test_crm_ops_4_mandates_postgres.py`.
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from core.enums import ACTIVITY_TYPES
from property import interactions, mandates
from tests import test_crm_ops_3_acquisitions as base
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
SU = (ROOT / "migrations" / "082_crm_ops_4_property_interactions.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "082_crm_ops_4_property_interactions_down.sql").read_text(encoding="utf-8")
VISTE = {n: (ASSETS / "views" / n).read_text(encoding="utf-8")
         for n in ("incarichi.js", "incarico-dettaglio.js")}
COMPONENTE = (ASSETS / "components" / "property-interactions.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# K - catalogo, migration, confini
# ---------------------------------------------------------------------------

def test_k02_i_tipi_sono_il_catalogo_activities_non_uno_nuovo():
    assert set(interactions.INTERACTION_TYPES) <= ACTIVITY_TYPES
    assert set(interactions.INTERACTION_TYPES) == {"call", "meeting", "note", "email", "whatsapp"}
    assert interactions.INTERACTION_LABELS_IT["meeting"] == "Incontro"
    assert interactions.INTERACTION_LABELS_IT["call"] == "Telefonata"
    # i tipi automatici non si registrano a mano
    for automatico in ("valuation", "status_change", "system"):
        assert automatico not in interactions.INTERACTION_TYPES
    # e le etichette condivise del CRM dicono "Incontro", non "Appuntamento"
    for f in ("components/activity-task-dialogs.js", "views/attivita.js"):
        assert "meeting: 'Incontro'" in (ASSETS / f).read_text(encoding="utf-8"), f


def test_k03_nessuna_entita_incarico_e_nessuna_scrittura_dagli_incarichi():
    sorgente = (ROOT / "property" / "mandates.py").read_text(encoding="utf-8")
    for vietato in ("INSERT", "UPDATE ", "DELETE", "core_cursor(commit=True)", "CREATE TABLE"):
        assert vietato not in sorgente, vietato
    assert "p.acquisition_id IS NOT NULL AND p.mandate_type IS NOT NULL" in mandates.E_UN_INCARICO
    assert not (ROOT / "mandates").exists()
    assert not any("mandates" in p.name for p in (ROOT / "migrations").glob("*.sql"))
    # le note non toccano audit ne' Agenda
    note = (ROOT / "property" / "interactions.py").read_text(encoding="utf-8")
    for vietato in ("acquisition_events", "appointment_events", "property_status_history",
                    "INSERT INTO appointments", "seller_timeline_events"):
        assert vietato not in note.split('"""', 2)[2], vietato
    # e la UI non crea incarichi: nessun POST /mandate, nessun "Nuovo incarico"
    for nome, testo in VISTE.items():
        testo = "\n".join(r for r in testo.splitlines() if not r.lstrip().startswith("//"))
        assert "/mandate`" not in testo and "/mandate'" not in testo, nome
        assert "Nuovo incarico" not in testo and "Crea incarico" not in testo, nome
        assert "apiPost" not in testo, nome


def test_k04_rotte_sotto_api_property_nessun_mount_nuovo():
    from property.router import router
    nuove = {(r.path, m) for r in router.routes for m in r.methods
             if "mandates" in r.path or "interactions" in r.path}
    assert nuove == {("/api/property/mandates", "GET"), ("/api/property/mandates/{property_id}", "GET"),
                     ("/api/property/properties/{property_id}/interactions", "GET"),
                     ("/api/property/properties/{property_id}/interactions", "POST")}
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "mandates" not in main and "interactions" not in main
    # stessa dipendenza di sessione di ogni rotta Immobili
    albero = ast.parse((ROOT / "property" / "router.py").read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.FunctionDef) and nodo.name in (
                "list_mandates", "get_mandate", "list_interactions", "create_interaction"):
            assert "legacy_basic_agency_context" in ast.unparse(nodo.args), nodo.name


def test_k05_lo_schema_rifiuta_data_ora_agenzia_e_autore():
    from pydantic import ValidationError
    from property.schemas import InteractionCreate
    InteractionCreate(interaction_type="call", note="ok")
    for extra in ({"occurred_at": "2026-01-01T10:00:00Z"}, {"agency_id": 2},
                  {"created_by_user_id": 3}, {"context": "acquisizione"}, {"note": ""}):
        with pytest.raises(ValidationError):
            InteractionCreate(**{"interaction_type": "call", "note": "ok", **extra})


# ---------------------------------------------------------------------------
# L - Shell eseguita
# ---------------------------------------------------------------------------

node = base.node
rt = base.a30_5._rt()
OWNERS = [
    {"contact_id": 41, "display_name": "Mario Rossi", "phone": "333 111", "email": "mario@example.test",
     "roles": ["owner"], "is_primary": True, "is_main": True},
    {"contact_id": 42, "display_name": "Bruno Bianchi", "phone": None, "email": None,
     "roles": ["seller"], "is_primary": False, "is_main": False},
]
INCARICO = {
    "property_id": 30, "code": "IMM-30", "title": "Trilocale", "address": "Via Roma", "civic_number": "1",
    "city": "Giulianova", "province": "TE", "microzone": None, "property_type": "apartment",
    "commercial_status": "mandate", "archived_at": None, "mandate_type": "Esclusiva",
    "mandate_start": "2026-10-02", "mandate_end": "2026-10-09", "days_to_expiry": 7,
    "expiry_state": "expiring", "asking_price": "245000.00", "minimum_price": "230000.00",
    "agent_id": 3, "agent_name": "Anna Agente", "valuation_price": "240000.00",
    "main_owner_id": 41, "main_owner_name": "Mario Rossi", "main_owner_phone": "333 111",
    "owners": OWNERS, "other_owners": OWNERS[1:],
    "acquisition": {"id": 501, "status": "acquired", "acquired_at": "2026-10-02T14:37:00Z",
                    "agent_id": 3, "agent_name": "Anna Agente", "visible": True},
    "last_interaction_at": None, "last_interaction": None,
    "agreed_price": "245000.00",
    "interaction_options": interactions.options(),
}
CHIAMATA = {"id": 900, "interaction_type": "call", "type_label": "Telefonata",
            "note": "Vuole aspettare ancora una settimana", "occurred_at": "2026-10-02T14:37:00Z",
            "created_at": "2026-10-02T14:37:00Z", "created_by_user_id": 1, "contact_id": 41,
            "contact_name": "Mario Rossi", "author_name": "Giorgio Censori", "context": "mandate"}
NOTA = {"id": 899, "interaction_type": "note", "type_label": "Nota", "note": "Documentazione completa",
        "occurred_at": "2026-09-30T16:10:00Z", "created_at": "2026-09-30T16:10:00Z",
        "created_by_user_id": 1, "contact_id": None, "contact_name": None,
        "author_name": "Giorgio Censori", "context": "property"}


def _rotte(*, elenco=(INCARICO,), incarico=INCARICO, storico=((NOTA,), (CHIAMATA, NOTA)),
           creata=CHIAMATA, sessione="agency_owner", immobile=None, opzioni_form=None):
    tipi = interactions.options()["types"]
    letture = [rt.ok({"items": list(s), "types": tipi, "contexts": ["property", "mandate"]}) for s in storico]
    dopo = {**incarico, "last_interaction": {"occurred_at": creata["occurred_at"], "type": creata["interaction_type"],
                                             "type_label": creata["type_label"], "author_name": creata["author_name"]}}
    voci = [
        ("GET", "/api/property/mandates?", [rt.ok({"items": list(elenco), "mandate_types": ["Esclusiva", "Non esclusiva"],
                                                  "expiry_filters": list(mandates.EXPIRY_FILTERS),
                                                  "sorts": list(mandates.SORTS)})]),
        ("GET", "/api/property/mandates/30", [rt.ok(incarico), rt.ok(dopo)]),
        ("GET", "/api/property/properties/30/interactions", letture),
        ("POST", "/api/property/properties/30/interactions", [{"status": 201, "body": creata}]),
        ("PATCH", "/api/property/properties/30", [rt.ok({**base.IMMOBILE, "mandate_end": "2027-03-31"})]),
        ("GET", "/api/property/form-options", [rt.ok(opzioni_form or {
            "territory": [], "energy_classes": [], "property_types": [], "can_assign": True,
            "agents": [{"id": 3, "role": "agent", "name": "Anna Agente"}, {"id": 4, "role": "agent", "name": "Bruno Collega"}]})]),
    ]
    davanti = "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)
    return davanti + "\n" + base._rotte(sessione, immobile=immobile or base.IMMOBILE)


@node
def test_l01_voce_di_menu_elenco_colonne_e_filtri(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const nav = __dom.byId['nav'].children.map((b) => b.dataset.route);
      const agente = C().querySelector('#inc-agent');
      C().querySelector('#inc-expiry').value = 'within_7'; C().querySelector('#inc-expiry').dispatch('change'); await wait();
      agente.value = '4'; agente.dispatch('change'); await wait();
      C().querySelector('#inc-sort').value = 'last_interaction'; C().querySelector('#inc-sort').dispatch('change'); await wait();
      const tipi = C().querySelector('#inc-type').querySelectorAll('option').map((o) => o.getAttribute('value'));
      report({ nav, agenteVisibile: !agente.hidden, tipi,
               pulsanti: C().querySelectorAll('button').map((b) => b.textContent.trim()) });
    """
    out = base._run(staged, scenario, _rotte(), "#/incarichi")
    assert out["nav"].index("incarichi") == out["nav"].index("acquisizioni") + 1
    testo = out["content"]
    for atteso in ("IMM-30", "Mario Rossi", "Bruno Bianchi", "Anna Agente", "Esclusiva", "7 giorni",
                   "Mandato", "#501", "Nessuna", "nascono solo da un", "Scadenza entro 7 giorni"):
        assert atteso in testo, atteso
    assert out["agenteVisibile"] is True and out["tipi"] == ["", "Esclusiva", "Non esclusiva"]
    assert not [p for p in out["pulsanti"] if "incarico" in p.lower()]      # niente "Nuovo incarico"
    elenchi = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith("/api/property/mandates?")]
    assert "expiry=within_7" in elenchi[-1] and "agent_id=4" in elenchi[-1] and "sort=last_interaction" in elenchi[-1]
    assert base._scritture(out) == []


@node
def test_l02_scheda_incarico_sezioni_azioni_e_storico(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      const pulsanti = C().querySelectorAll('button').map((b) => b.textContent.trim());
      const link = (s) => C().querySelector(s) ? C().querySelector(s).getAttribute('href') : null;
      report({ pulsanti, immobile: link('#inc-open-property'), acquisizione: link('#inc-open-acquisition'),
               proprietario: link('#inc-open-owner'),
               voci: C().querySelectorAll('.int-item').map((li) => li.dataset.interactionId) });
    """
    out = base._run(staged, scenario, _rotte(), "#/incarichi/30")
    testo = out["content"]
    for atteso in ("Incarico · IMM-30", "Immobile", "Proprietari", "Mario Rossi", "Principale", "Bruno Bianchi",
                   "Tipo incarico", "Esclusiva", "Data inizio", "02/10/2026", "Data scadenza", "09/10/2026",
                   "Giorni residui", "7 giorni", "Prezzo richiesto", "Prezzo minimo", "Stato commerciale",
                   "Acquisizione di origine", "#501", "Ultima interazione", "Storico interazioni",
                   "Documentazione completa", "30 SET 2026 · 18:10"):
        assert atteso in testo, atteso
    assert out["immobile"] == "#/immobili/30" and out["acquisizione"] == "#/acquisizioni/501"
    assert out["proprietario"] == "#/contatti/41"
    assert "Modifica incarico" in out["pulsanti"] and "+ Telefonata" in out["pulsanti"]
    assert "+ Incontro" in out["pulsanti"] and "+ Nota" in out["pulsanti"]
    assert not [p for p in out["pulsanti"] if p in ("Genera incarico", "Crea incarico", "Nuovo incarico")]
    assert out["voci"] == ["899"]
    assert base._scritture(out) == []


@node
def test_l03_acquisizione_non_visibile_non_ha_il_collegamento(staged):  # noqa: F811
    altrui = {**INCARICO, "acquisition": {**INCARICO["acquisition"], "visible": False, "agent_name": "Bruno Collega"}}
    scenario = "await wait(); await wait(); await wait(); report({ link: !!C().querySelector('#inc-open-acquisition') });"
    out = base._run(staged, scenario, _rotte(incarico=altrui, sessione="agent"), "#/incarichi/30")
    assert out["link"] is False and "#501" in out["content"] and "di Bruno Collega" in out["content"]


@node
def test_l04_telefonata_dalla_scheda_incarico_ricarica_storico_e_ultima(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      bottone(C(), '+ Telefonata').dispatch('click'); await wait();
      const tipoProposto = q('#int-type').value;
      const referenti = opz(q('#int-contact'));
      const campiData = !!q('input[type="date"]') || !!q('input[type="datetime-local"]');
      q('#int-contact').value = '41';
      q('#int-note').value = '  Vuole aspettare ancora una settimana  ';
      q('form').dispatch('submit'); await wait(); await wait(); await wait();
      report({ tipoProposto, referenti, campiData,
               voci: C().querySelectorAll('.int-item').map((li) => li.dataset.interactionId),
               ultima: C().querySelector('#inc-last-interaction').textContent,
               feedback: C().querySelector('.success-box') ? C().querySelector('.success-box').textContent : null });
    """
    out = base._run(staged, scenario, _rotte(), "#/incarichi/30")
    assert out["tipoProposto"] == "call" and out["referenti"] == ["", "41", "42"]
    assert out["campiData"] is False                       # data e ora: mai chieste
    scritture = base._scritture(out)
    assert [c["url"] for c in scritture] == ["/api/property/properties/30/interactions"]
    assert scritture[0]["body"] == {"interaction_type": "call", "note": "Vuole aspettare ancora una settimana",
                                    "context": "mandate", "contact_id": 41}
    assert out["voci"] == ["900", "899"]                   # ricaricato dal server, piu' recente prima
    assert "Telefonata" in out["ultima"] and "Giorgio Censori" in out["ultima"]
    assert out["feedback"] == "Interazione registrata."
    letture = [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"] == "/api/property/mandates/30"]
    assert len(letture) == 2                               # la scheda e' riletta dopo la nota


@node
def test_l05_nota_vuota_non_parte(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      bottone(C(), '+ Nota').dispatch('click'); await wait();
      q('#int-note').value = '   ';
      q('form').dispatch('submit'); await wait();
      report({ errore: q('#int-error').textContent, tipo: q('#int-type').value });
    """
    out = base._run(staged, scenario, _rotte(), "#/incarichi/30")
    assert out["errore"] == "Scrivi la nota." and out["tipo"] == "note"
    assert base._scritture(out) == []


@node
def test_l06_scheda_immobile_stesso_storico_stessa_scrittura(staged):  # noqa: F811
    """Il tab "Storico interazioni" della scheda Immobile legge e scrive la
    STESSA fonte della scheda Incarico, con contesto `property`."""
    immobile = {**base.IMMOBILE, "acquisition_id": 501, "commercial_status": "mandate",
                "mandate_type": "Esclusiva", "mandate_start": "2026-10-02", "mandate_end": "2026-10-09"}
    nota = {**NOTA, "id": 901, "context": "property", "note": "Incontro con proprietario"}
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelectorAll('.tab-btn').map((b) => b.textContent.trim());
      const apri = C().querySelector('#incarico-open-mandate');
      C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === 'attivita').dispatch('click');
      await wait(); await wait(); await wait();
      const prima = C().querySelectorAll('.int-item').map((li) => li.dataset.interactionId);
      bottone(C(), '+ Incontro').dispatch('click'); await wait();
      const referenti = opz(q('#int-contact'));
      q('#int-note').value = 'Incontro con proprietario';
      q('form').dispatch('submit'); await wait(); await wait(); await wait();
      report({ tabs, apri: apri ? apri.getAttribute('href') : null, prima, referenti,
               dopo: C().querySelectorAll('.int-item').map((li) => li.dataset.interactionId) });
    """
    out = base._run(staged, scenario, _rotte(immobile=immobile, storico=((NOTA,), (nota, NOTA)), creata=nota),
                    "#/immobili/30")
    assert "Storico interazioni" in out["tabs"] and "Attività" not in out["tabs"]
    assert out["apri"] == "#/incarichi/30"
    assert out["prima"] == ["899"] and out["dopo"] == ["901", "899"]
    assert out["referenti"] == ["", "41", "42", "43"]      # i contatti dell'immobile, uno per contatto
    scritture = base._scritture(out)
    assert [c["url"] for c in scritture] == ["/api/property/properties/30/interactions"]
    assert scritture[0]["body"] == {"interaction_type": "meeting", "note": "Incontro con proprietario",
                                    "context": "property"}


@node
def test_l07_modifica_incarico_usa_la_patch_dell_immobile(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait(); await wait();
      bottone(C(), 'Modifica incarico').dispatch('click'); await wait();
      q('#inc-edit-end').value = '2027-03-31';
      q('form').dispatch('submit'); await wait(); await wait();
      report({});
    """
    out = base._run(staged, scenario, _rotte(), "#/incarichi/30")
    scritture = base._scritture(out)
    assert [c["url"] for c in scritture] == ["/api/property/properties/30"]
    assert scritture[0]["m"] == "PATCH" and scritture[0]["body"] == {"mandate_end": "2027-03-31"}
    assert "Incarico aggiornato." in out["content"]


def test_l08_data_e_ora_di_roma_mai_utc():
    """formatInteractionTime eseguito davvero (node), con un "ora" fisso."""
    if base.a30_5.NODE is None:
        pytest.skip("node non disponibile")
    modulo = (ASSETS / "components" / "property-interactions.js").as_posix()
    codice = f"""
      globalThis.fetch = async () => ({{}});
      const m = await import('file://{modulo}');
      const ora = new Date('2026-10-02T15:00:00Z');                 // 17:00 a Roma
      console.log(JSON.stringify([
        m.formatInteractionTime('2026-10-02T14:37:00Z', ora),        // oggi 16:37 (CEST)
        m.formatInteractionTime('2026-10-01T22:30:00Z', ora),        // 2 ott 00:30 a Roma: oggi
        m.formatInteractionTime('2026-10-01T09:05:00Z', ora),        // ieri 11:05
        m.formatInteractionTime('2026-09-30T16:10:00Z', ora),        // 30 SET 2026 · 18:10
        m.formatInteractionTime('2026-01-15T08:00:00Z', ora),        // inverno: 09:00 (CET)
        m.formatInteractionTime(null, ora),
      ]));
    """
    esito = subprocess.run([base.a30_5.NODE, "--input-type=module", "-e", codice], capture_output=True,
                           text=True, timeout=30, env={"TZ": "UTC", "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    assert json.loads(esito.stdout.strip()) == [
        "Oggi · 16:37", "Oggi · 00:30", "Ieri · 11:05", "30 SET 2026 · 18:10", "15 GEN 2026 · 09:00", "—"]


def test_l09_layout_smartphone():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css.split("/* CRM-OPS-4:")[1]
    assert "@media (max-width: 767px)" in blocco
    for regola in (".inc-toolbar { flex-direction: column", ".inc-detail .detail-grid { grid-template-columns: 1fr",
                   ".inc-actions .btn, .int-actions .btn { width: 100%", ".int-note { margin: 6px 0 4px; white-space: pre-wrap"):
        assert regola in blocco, regola


def test_k06_lo_storico_d_immobile_non_si_cancella():
    """Service: la DELETE non tocca righe con property_id e risponde 409; le
    attivita' senza immobile si cancellano come prima. DB: trigger BEFORE
    DELETE solo WHEN OLD.property_id IS NOT NULL. UI: niente "Elimina" su
    un'interazione d'immobile. Nessun soft-delete."""
    from core import repository as core_repository
    sorgente = (ROOT / "core" / "repository.py").read_text(encoding="utf-8")
    corpo = sorgente.split("def delete_activity(")[1].split("\ndef ")[0]
    assert "AND a.property_id IS NULL" in corpo and "raise ConflictError(ACTIVITY_ON_PROPERTY_NOT_DELETABLE)" in corpo
    assert "raise NotFoundError" in corpo                      # D-6 invariato per il resto
    assert "nuova interazione" in core_repository.ACTIVITY_ON_PROPERTY_NOT_DELETABLE
    eseguibile = "\n".join(r for r in SU.splitlines() if not r.lstrip().startswith("--"))
    assert "BEFORE DELETE ON activities" in eseguibile
    assert "FOR EACH ROW WHEN (OLD.property_id IS NOT NULL)" in eseguibile
    for vietato in ("deleted_at", "is_deleted", "archived_at"):
        assert vietato not in eseguibile, vietato
    assert "DROP TRIGGER IF EXISTS trg_activities_property_history ON activities;" in GIU
    attivita = (ASSETS / "views" / "attivita.js").read_text(encoding="utf-8")
    blocco = attivita.split("function renderCronologiaActions(")[1].split("\nfunction ")[0]
    assert blocco.index("if (item.data.property_id)") < blocco.index("data-delete-activity")
    assert "Storico immobile" in blocco
    # il componente condiviso non cancella mai
    assert "apiDelete" not in COMPONENTE and "DELETE" not in COMPONENTE
