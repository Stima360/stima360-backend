"""SEMPLIFICAZIONE-CRM-1 - prove statiche (senza database, senza browser).

  01  la 107 e' valida per il runner, additiva (solo trigger), ha la sua down ed e' l'ultima
  02  catalogo dei tipi: la visita immobile e' `buyer_visit`; `sale_appointment` resta
      nel catalogo ma non si crea piu' dall'interfaccia; etichette italiane specchiate
  03  il service esige incarico, cliente e agente per ogni tipo di MANDATE_REQUIRED_TYPES,
      alla creazione e in modifica solo quando cambiano i collegamenti
  04  nessun percorso alternativo: link pubblici (schema, risoluzione, creazione),
      facade BUY/PROPERTY (stesso service), i quattro «Programma visita» verificano
      l'incarico prima di aprire il dialog
  05  il dialog: campi per tipo, lead nelle opzioni avanzate, agente preselezionato,
      `sale_appointment` fuori dal selettore e dai filtri
  06  i codici IMM-<id> non compaiono nel markup ordinario: elenchi, schede, selettori
  07  la scheda Incarico ha la sezione Visite dall'Agenda (una sola fonte)
  08  la scheda Immobile ha la tab Acquirenti compatibili sul motore MATCH esistente,
      con ricerca esplicita (mai all'apertura) e spiegazione dell'idoneita'
  09  i promemoria A32 non cambiano
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
SU = (ROOT / "migrations" / "107_visita_immobile_incarico.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "107_visita_immobile_incarico_down.sql").read_text(encoding="utf-8")


def _js(*parti):
    return (ASSETS.joinpath(*parti)).read_text(encoding="utf-8")


def _senza_commenti(testo):
    return "\n".join(r for r in testo.splitlines() if not r.strip().startswith("//"))


def _eseguibile(sql):
    return "\n".join(r for r in sql.splitlines() if not r.strip().startswith("--"))


def test_01_la_107_e_valida_additiva_e_l_ultima():
    from scripts import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA FOLLOW-UP FASE B: dopo la 107 c'e' la 108
    # (journey sul contatto, opt-out di piattaforma); la 107 resta quella qui
    # descritta, penultima, e si continua a verificarla com'e'.
    assert tutte[-1].version == "108_followup_contact_journey"
    ultima = tutte[-2]
    assert ultima.version == "107_visita_immobile_incarico"
    assert ultima.down_path is not None and not ultima.non_transactional
    assert runner.validate_migration(ultima) == []
    e = _eseguibile(SU)
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", e, re.M)
    for vietato in ("UPDATE ", "INSERT INTO", "DELETE FROM", "TRUNCATE", "DROP COLUMN",
                    "ADD COLUMN", "CREATE TABLE", "ADD CONSTRAINT", "public_booking_links"):
        assert vietato not in e, vietato
    assert "BEFORE INSERT ON appointments" in e
    assert "NEW.rescheduled_from_id IS NULL" in e and "NEW.source = 'crm_manual'" in e
    for colonna in ("NEW.property_id IS NULL", "NEW.contact_id IS NULL", "NEW.assigned_user_id IS NULL"):
        assert colonna in e, colonna
    giu = _eseguibile(GIU)
    assert re.search(r"^\s*BEGIN\s*;", giu, re.M) and re.search(r"^\s*COMMIT\s*;", giu, re.M)
    assert "DROP TRIGGER IF EXISTS trg_appointments_visit_links_guard" in giu
    assert "DELETE" not in giu and "UPDATE" not in giu


def test_02_catalogo_ed_etichette():
    from appointments import enums
    assert enums.VISIT_TYPE == "buyer_visit"
    assert enums.MANDATE_REQUIRED_TYPES == ("buyer_visit", "sale_appointment")
    assert enums.CREATE_HIDDEN_TYPES == ("sale_appointment",)
    assert "sale_appointment" in enums.APPOINTMENT_TYPES          # le righe esistenti restano leggibili
    attese = {
        "call": "Telefonata", "video_call": "Videochiamata", "seller_meeting": "Appuntamento proprietario",
        "inspection": "Sopralluogo", "buyer_visit": "Visita immobile",
        "valuation_presentation": "Presentazione valutazione", "mandate_signing": "Firma incarico",
        "proposal": "Proposta d’acquisto", "preliminary_contract": "Preliminare di compravendita",
        "notary": "Rogito notarile", "technical": "Appuntamento tecnico", "other": "Altro",
    }
    for k, v in attese.items():
        assert enums.APPOINTMENT_TYPE_LABELS_IT[k] == v, k
    modello = _js("agenda", "agenda-model.js")
    for k, v in attese.items():
        assert f"{k}: '{v}'," in modello, k
    assert "export const VISIT_TYPE = 'buyer_visit';" in modello
    assert "export const MANDATE_REQUIRED_TYPES = Object.freeze(['buyer_visit', 'sale_appointment']);" in modello
    assert "export const CREATE_HIDDEN_TYPES = Object.freeze(['sale_appointment']);" in modello
    from calendar_sync.service import _ETICHETTE
    assert _ETICHETTE["buyer_visit"] == "Visita immobile"


def test_03_il_service_esige_l_incarico_per_la_visita():
    service = (ROOT / "appointments" / "service.py").read_text(encoding="utf-8")
    controllo = service[service.index("def _controlla_vendita"):]
    controllo = controllo[:controllo.index("\n# ----")]
    assert "if appointment_type not in MANDATE_REQUIRED_TYPES:" in controllo
    assert "mandate_state(immobile) not in SALE_MANDATE_STATES" in controllo
    # creazione: sempre; modifica: solo quando cambiano i collegamenti
    assert len(re.findall(r"^\s+_controlla_vendita\(cur, agency_id,", service, re.M)) == 2
    patch = service[service.index("def patch_appointment"):]
    patch = patch[:patch.index("\n# ----")]
    assert 'if {"property_id", "contact_id"} & cambi.keys():' in patch
    # «Apri incarico» anche per la visita
    assert 'if row["appointment_type"] in MANDATE_REQUIRED_TYPES and row["property_id"] is not None:' in service
    # il lookup per un solo immobile (il controllo prima di «Programma visita»)
    assert "def lookup_sale_mandates(ctx, *, search=None, limit=10, property_id=None):" in service
    router = (ROOT / "appointments" / "router.py").read_text(encoding="utf-8")
    assert "property_id: int | None = None," in router[router.index('@router.get("/lookups/mandates")'):]


def test_04_nessun_percorso_alternativo():
    from pydantic import ValidationError
    import pytest
    from public_booking.enums import NOT_BOOKABLE_TYPES
    from public_booking.schemas import BookingLinkCreateBody
    assert NOT_BOOKABLE_TYPES == ("buyer_visit", "sale_appointment")
    with pytest.raises(ValidationError):
        BookingLinkCreateBody(assigned_user_id=1, appointment_type="buyer_visit", duration_minutes=60)
    pb = (ROOT / "public_booking" / "service.py").read_text(encoding="utf-8")
    assert 'if link["appointment_type"] in NOT_BOOKABLE_TYPES:\n        raise LinkNotBookable("type")' in pb
    service = (ROOT / "appointments" / "service.py").read_text(encoding="utf-8")
    booking = service[service.index("def create_public_booking_appointment"):]
    assert 'if link["appointment_type"] in MANDATE_REQUIRED_TYPES:' in booking
    # la facade BUY/PROPERTY passa dallo STESSO service (nessuna seconda creazione)
    facade = (ROOT / "buyer_visits" / "facade.py").read_text(encoding="utf-8")
    assert "create_appointment_with_cursor" in facade and "INSERT" not in facade
    # i quattro «Programma visita» verificano l'incarico prima di aprire il dialog
    for vista in ("immobile-dettaglio", "acquirente-dettaglio", "abbinamento-dettaglio", "incarico-dettaglio"):
        codice = _senza_commenti(_js("views", f"{vista}.js"))
        corpo = codice[codice.index("openCreateDialog(dialogEl, {"):]
        prima = codice[:codice.index("openCreateDialog(dialogEl, {")]
        assert "lookupMandates({ propertyId:" in prima, vista
        assert "NO_MANDATE_FOR_VISIT" in prima, vista
        assert "appointmentType: 'buyer_visit'" in corpo, vista
    # il modal legacy della scheda Immobile non crea visite future (invariato)
    assert "if (!isEdit && isFutureOpenVisit(payload)) {" in _js("views", "immobile-dettaglio.js")


def test_05_il_dialog_segue_il_tipo():
    dialoghi = _senza_commenti(_js("components", "agenda", "agenda-dialogs.js"))
    assert "const CAMPI_TIPO = Object.freeze({" in dialoghi
    assert "buyer_visit: ['contact', 'mandate', 'notes']," in dialoghi
    assert "proposal: ['contact', 'mandate', 'notes']," in dialoghi
    assert "notary: ['contact', 'mandate', 'location', 'notes']," in dialoghi
    assert "const incaricoObbligatorio = () => MANDATE_REQUIRED_TYPES.includes(tipo.value);" in dialoghi
    assert "!CREATE_HIDDEN_TYPES.includes(t) || t === appointmentType" in dialoghi
    assert '<details class="agenda-advanced" data-campo="lead" data-advanced>' in dialoghi
    assert "selezionato: me ? me.id : undefined," in dialoghi
    assert "if (NOT_BOOKABLE_TYPES.includes(t)) continue;" in dialoghi
    pagina = _js("views", "agenda", "agenda-page.js")
    assert "filter(([t]) => !CREATE_HIDDEN_TYPES.includes(t))" in pagina
    lookup = _js("agenda", "agenda-lookup.js")
    assert "LEAD_PIPELINE_LABELS[lead.pipeline]" in lookup and "propertyLookupLabel(property, types)" in lookup
    etichette = _js("components", "lead-labels.js")
    assert "sell: 'Vendita'" in etichette and "lost: 'Da recuperare'" in etichette


def test_06_nessun_codice_imm_nel_markup_ordinario():
    helper = _js("components", "property-label.js")
    for nome in ("propertyShortLabel", "unitLabel", "propertyRowLabel", "propertyLookupLabel", "floorText"):
        assert f"export function {nome}(" in helper, nome
    assert "code" not in _senza_commenti(helper).replace("code", "", 0) or "p.code" not in _senza_commenti(helper)
    viste = {
        "views/immobili.js": ("propertyShortLabel(p, tipologie, tipiPertinenza)",),
        "views/edificio-dettaglio.js": ("unitLabel(u, tipi, kinds)",),
        "views/incarichi.js": ("propertyShortLabel(r)",),
        "views/incarico-dettaglio.js": ("Incarico · ${escapeHtml(propertyShortLabel(inc))}",),
        "views/acquisizioni.js": ("propertyRowLabel(r)",),
        "views/abbinamenti.js": ("propertyRowLabel(m)",),
        "views/abbinamento-dettaglio.js": ("propertyRowLabel(m)",),
        "views/acquirente-dettaglio.js": ("propertyRowLabel(m)",),
    }
    for vista, attese in viste.items():
        codice = _senza_commenti(_js(*vista.split("/")))
        for a in attese:
            assert a in codice, (vista, a)
        # nessuna resa del codice tecnico nel markup
        assert not re.search(r"\$\{escapeHtml\((p|r|u|m|inc)\.code\b", codice), vista
        assert "property_code ||" not in codice and "|| p.code" not in codice, vista
    immobile = _senza_commenti(_js("views", "immobile-dettaglio.js"))
    assert "['Codice', p.code]" not in immobile and "${property.code" not in immobile
    assert "propertyTypeText(property), property.region, property.province" in immobile
    for vista in ("views/immobili.js", "views/incarichi.js", "views/acquisizioni.js", "views/abbinamenti.js"):
        assert "Cerca per codice" not in _js(*vista.split("/")) and "codice, indirizzo" not in _js(*vista.split("/")), vista
    censimento = _senza_commenti(_js("census", "census-model.js"))
    assert "Simile a un’unità già censita" in censimento and "Pertinenza di un’unità principale" in censimento
    assert "unitLabel(unit, propertyTypes, accessoryKinds) || 'Unità'} aggiunta" in censimento


def test_07_la_scheda_incarico_legge_le_visite_dall_agenda():
    codice = _senza_commenti(_js("views", "incarico-dettaglio.js"))
    assert "getList({ propertyId: inc.property_id, types: [...MANDATE_REQUIRED_TYPES], limit: 200 })" in codice
    assert "export function renderVisiteIncarico(righe, session)" in codice
    for colonna in ("'Data'", "'Ora'", "'Cliente'", "'Agente'", "'Stato'", "Apri in Agenda"):
        assert colonna in codice, colonna
    assert "/api/property/visits" not in codice            # nessuna seconda fonte, nessuna copia
    assert "property_visits" not in codice
    assert "session.role === 'agent'" in codice            # la visibilita' resta quella dell'Agenda
    # la lista dell'Agenda porta i nomi (stesse espressioni del calendario)
    repo = (ROOT / "appointments" / "repository.py").read_text(encoding="utf-8")
    lista = repo[repo.index("def list_appointments("):repo.index("def cancelled_kind_installed")]
    assert "AS agent_name" in lista and "AS contact_name" in lista
    assert "LEFT JOIN contacts c" in lista and "LEFT JOIN operator_users u" in lista
    api = _js("agenda", "agenda-api.js")
    assert "property_id: propertyId ? id(propertyId) : undefined," in api


def test_08_acquirenti_compatibili_sul_motore_match():
    codice = _senza_commenti(_js("views", "immobile-dettaglio.js"))
    assert "{ key: 'acquirenti', label: 'Acquirenti compatibili' }," in codice
    assert "{ key: 'abbinamenti', label: 'Abbinamenti' }," not in codice     # una sola esperienza
    assert "case 'acquirenti':\n        case 'abbinamenti': {" in codice      # la vecchia rotta resta valida
    assert "Funzione disponibile nella fase Abbinamenti" not in codice
    # lettura dei match persistiti, idoneita', ricerca ESPLICITA (mai all'apertura)
    assert "`/api/match/matches?property_id=${propertyId}`" in codice
    assert "`/api/match/readiness?property_id=${propertyId}`" in codice
    assert "apiPost(`/api/match/properties/${property.id}/calculate`, {})" in codice
    corpo = codice[codice.index("async function runMatchCalculate("):]
    corpo = corpo[:corpo.index("\n  }\n")]
    assert "calculate" in corpo
    apertura = codice[codice.index("async function showTab(key)"):codice.index("function bindIncaricoSection")]
    assert "calculate" not in apertura
    for atteso in ("data-buyers-filter=\"compatible\"", "data-buyers-filter=\"all\"", "Apri acquirente",
                   "Apri abbinamento", "buyer-visit-btn", "readinessText(readiness)", "e salvati"):
        assert atteso in codice, atteso
    etichette = _js("components", "match-labels.js")
    assert "incompatible: 'Incompatibile'" in etichette
    # il match porta il contatto dell'acquirente per «Programma visita»
    repo = (ROOT / "match" / "repository.py").read_text(encoding="utf-8")
    assert repo.count("b.contact_id AS buyer_contact_id") == 4


def test_09_i_promemoria_non_cambiano():
    from appointment_reminders import policy
    assert policy.ALLOWED_TYPES == {"buyer_visit", "inspection", "seller_meeting",
                                    "valuation_presentation", "mandate_signing"}
