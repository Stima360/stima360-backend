"""CENSIMENTO-2 - prove statiche: migration 100, classi, schemi, rotte.

Nessun database. Il comportamento su PostgreSQL vero e' in
`tests/test_censimento_2_composition_postgres.py`.
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
VERSIONE = "100_censimento_2_composition"
SU = (ROOT / "migrations" / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")


def _eseguibile(testo):
    return "\n".join(r for r in testo.splitlines() if not r.lstrip().startswith("--"))


def test_m01_la_100_e_valida_per_il_runner_e_l_ultima():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA ELIMINAZIONE-EDIFICIO-1: la 100 resta valida, seguita dalla sola 101
    # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO T4: poi la sola 102 (verifica della posizione degli edifici)
    # SENTINELLA AGGIORNATA DA STIMA360 ZONA-3 (C1): poi la sola 103 (verifica della posizione degli
    # immobili indipendenti, stesso modello della 102), additiva, ora l'ultima
    # SENTINELLA AGGIORNATA DA AGENZIE-TERRITORI C2: poi la sola 104 (proposte di modifica territoriali
    # e loro registro), additiva
    # SENTINELLA AGGIORNATA DA AGENZIE-AMMINISTRAZIONE ADM D15: poi la sola 105 (nessuna cancellazione
    # fisica delle entita' della Rete: trigger, nessuna colonna e nessun dato)
    # SENTINELLA AGGIORNATA DA APPUNTAMENTI DI VENDITA: poi la sola 106 (la tipologia
    # `sale_appointment` e il suo vincolo, additiva)
    # SENTINELLA AGGIORNATA DA SEMPLIFICAZIONE-CRM-1: poi la sola 107 (la guardia della
    # visita immobile su un incarico: un trigger, nessuna colonna e nessun dato)
    # SENTINELLA AGGIORNATA DA FOLLOW-UP FASE B: poi la sola 108 (journey sul
    # contatto, pausa di sistema, opt-out di piattaforma; additiva)
    # SENTINELLA AGGIORNATA DA C1/C2: poi la sola 109 (consensi marketing per
    # canale, opt-out per indirizzo; additiva, nessun backfill)
    # SENTINELLA AGGIORNATA DA C3: poi la sola 110 (link personali, invii della
    # pagina di consenso, double opt-in; additiva, nessun backfill), ora l'ultima
    # SENTINELLA AGGIORNATA DA STIMA VOICE V9: poi la sola 111 (l'elenco degli intenti del registro
    # della Voce per gli incarichi; additiva, nessun dato), ora l'ultima
    # SENTINELLA AGGIORNATA DA C3.5: poi la sola 112 (la prova del consenso marketing
    # scritta con la richiesta: due colonne nullable di `stime`, nessun backfill), ora l'ultima
    assert tutte[-1].version == "112_stime_consenso_marketing_prova"
    assert tutte[-2].version == "111_voice_mandate_intents"
    assert tutte[-3].version == "110_consent_capture"
    assert tutte[-4].version == "109_consent_channels"
    assert tutte[-5].version == "108_followup_contact_journey"
    assert tutte[-6].version == "107_visita_immobile_incarico"
    assert tutte[-7].version == "106_sale_appointments"
    assert tutte[-8].version == "105_adm_d15_no_delete_admin_entities"
    assert tutte[-9].version == "104_agenzie_c2_territorial_change_requests"
    assert tutte[-10].version == "103_zona_3_property_position_verification"
    assert tutte[-11].version == "102_territorio_4_position_verification"
    assert tutte[-12].version == "101_eliminazione_edificio_1"
    m100 = tutte[-13]
    assert m100.version == VERSIONE and tutte[-14].version == "099_voice_undo"
    assert m100.down_available and not m100.non_transactional
    assert runner.validate_migration(m100) == []
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", _eseguibile(SU), re.M)
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)
    assert "DELETE FROM schema_migrations" not in GIU


def test_m02_additiva_senza_backfill_e_down_protetta():
    e = _eseguibile(SU)
    for vietato in ("UPDATE properties", "UPDATE buildings", "INSERT INTO", "DELETE FROM", "TRUNCATE",
                    "DROP COLUMN", "SET NOT NULL", "ALTER COLUMN", "DEFAULT", "CREATE TABLE"):
        assert vietato not in e, vietato
    colonne = re.findall(r"ADD COLUMN IF NOT EXISTS (\w+) (\w+)", e)
    assert colonne == [("surface_source", "VARCHAR"), ("cadastral_category_source", "VARCHAR"),
                       ("cadastral_evidence_type", "VARCHAR"), ("cadastral_evidence_date", "DATE"),
                       ("cadastral_evidence_ref", "VARCHAR"), ("cadastral_evidence_identity", "TEXT"),
                       ("units_declared_residential", "INTEGER"), ("units_declared_commercial", "INTEGER"),
                       ("units_declared_other", "INTEGER"), ("units_declared_pertinenze", "INTEGER")]
    # il trigger della provenienza gira DOPO la normalizzazione catastale della 083
    assert "trg_properties_z_census_source" > "trg_properties_cadastral_normalize"
    assert "trg_properties_z_census_source" > "trg_properties_census_guard"
    # le conferme della provenienza (UPDATE OF <provenienza>) girano prima di chi decide
    assert "trg_properties_y1_census_surface_source" < "trg_properties_y2_census_category_source" \
        < "trg_properties_z_census_source"
    assert "BEFORE UPDATE OF surface_source ON properties" in e
    assert "BEFORE UPDATE OF cadastral_category_source ON properties" in e
    # nessun reset legato all'identita' catastale (campi non correlati)
    # i riferimenti catastali riguardano SOLO una «verificata» non riconfermata (stimata/dichiarata restano)
    corpo = e[e.index("CREATE OR REPLACE FUNCTION properties_census_source_follow"):e.index("trg_properties_y1")]
    blocco = corpo[corpo.index("IF identita_cambiata THEN"):corpo.index("IF (NEW.surface_source = 'verified' AND superficie_confermata)")]
    assert blocco.count("= 'verified' AND NOT") == 2 and "'declared'" not in blocco and "'estimated'" not in blocco
    # «verificata»: identita' (sezione facoltativa) + fonte registrata (tipo e data) legata a quell'identita'
    vincolo = e[e.index("properties_verified_source_evidence_chk CHECK"):e.index("properties_census_source_mark")]
    for richiesto in ("census_identity_key(", "cadastral_evidence_type IS NOT NULL", "cadastral_evidence_date IS NOT NULL",
                      "cadastral_evidence_identity ="):
        assert richiesto in vincolo, richiesto
    chiave = e[e.index("CREATE OR REPLACE FUNCTION census_identity_key"):e.index("properties_cadastral_evidence_type_chk")]
    assert "nullif(btrim(sezione)" not in chiave and "IMMUTABLE" in chiave
    # la down si ferma con dati presenti
    assert "100 down: esistono unita'' con la provenienza" in GIU and "100 down: esistono edifici" in GIU


def test_c01_classi_d1():
    from property import census_classes as c
    assert c.unit_class({"property_type": "apartment"}) == "residential"
    assert c.unit_class({"property_type": "villa"}) == "residential"
    assert c.unit_class({"property_type": "rustic"}) == "other"                       # mai implicito
    assert c.unit_class({"property_type": "rustic", "metadata": {"census_class": "residential"}}) == "residential"
    assert c.unit_class({"property_type": "garage"}) == "other"                       # box autonomo
    assert c.unit_class({"property_type": "storage"}) == "other"                      # cantina autonoma
    assert c.unit_class({"property_type": "garage", "is_pertinenza": True}) == "pertinenze"
    assert c.unit_class({"property_type": "apartment", "parent_property_id": 3}) == "pertinenze"
    assert c.unit_class({"property_type": "office"}) == "commercial"
    assert c.unit_class({"property_type": "land"}) == "other"
    # piano, scala e interno non servono alla completezza (precisazione 1)
    assert c.missing_data({"surface_sqm": 50, "surface_source": "declared", "cadastral_category": "A/2",
                           "cadastral_category_source": "declared", "floor": " "}) == []
    assert c.missing_data({"surface_sqm": 50}) == ["surface_source", "cadastral_category", "cadastral_category_source"]
    # verifica: indicatore separato
    ident = {"cadastral_municipality_code": "L307", "cadastral_section": None, "cadastral_sheet": "5",
             "cadastral_parcel": "61", "cadastral_subunit": "2"}
    assert c.identity_key(ident) == "L307||5|61|2" and c.identity_key({**ident, "cadastral_subunit": ""}) is None
    # AGGIORNATO DA CENSIMENTO-2 FASE A (A1): tre condizioni (verificata, parziale, da verificare/riconfermare)
    assert c.verification_state({**ident, "cadastral_category_source": "verified"}) == "partial"
    assert c.verification_state({**ident, "cadastral_category_source": "verified", "surface_source": "verified"}) == "verified"
    assert c.verification_state({**ident, "cadastral_evidence_identity": "L307||5|61|1"}) == "to_verify"
    assert c.needs_reconfirm({**ident, "cadastral_evidence_identity": "L307||5|61|1"}) is True
    assert c.verification_state({**ident, "cadastral_evidence_identity": "L307||5|61|2"}) == "to_verify"
    assert c.needs_reconfirm({**ident, "cadastral_evidence_identity": "L307||5|61|2"}) is False
    sql = c.class_sql("u")
    assert "'rustic'" in sql and "census_class" in sql and "is_pertinenza" in sql


def _gruppo(**kw):
    return {"unit_class": "residential", "count": 2, "property_type": "apartment", **kw}


@pytest.mark.parametrize("errato", [
    _gruppo(unit_class="nessuna"), _gruppo(property_type="garage"), _gruppo(count=0), _gruppo(count=301),
    _gruppo(surface_sqm="60"), _gruppo(surface_source="declared"),
    _gruppo(cadastral_category="A/2", cadastral_category_source="verified"),
    _gruppo(surface_sqm="60", surface_source="verified"), _gruppo(pertinenza_kind="cantina"),
    {"unit_class": "pertinenze", "count": 1, "property_type": "garage", "pertinenza_kind": "cantina"},
    _gruppo(property_type="building"), _gruppo(agency_id=1), _gruppo(record_kind="crm"),
    _gruppo(parent_property_id=4), _gruppo(cadastral_subunit="3"),
])
def test_s01_gruppi_non_validi(errato):
    from property.schemas import UnitsGenerate
    with pytest.raises(ValidationError):
        UnitsGenerate(client_request_id=uuid.uuid4(), groups=[errato])


def test_s02_lotto_conferma_e_limiti():
    from property.schemas import MAX_BATCH_UNITS, UnitsBulkUpdate, UnitsGenerate
    assert MAX_BATCH_UNITS == 300
    chiave = uuid.uuid4()
    assert UnitsGenerate(client_request_id=chiave, groups=[_gruppo()]).dry_run is True        # default: anteprima
    with pytest.raises(ValidationError):
        UnitsGenerate(client_request_id=chiave, dry_run=False, groups=[_gruppo()])            # conferma senza conteggi
    with pytest.raises(ValidationError):
        UnitsGenerate(client_request_id=chiave, groups=[_gruppo(count=200), _gruppo(count=101)])
    with pytest.raises(ValidationError):
        UnitsGenerate(client_request_id=chiave, dry_run=False, groups=[_gruppo()],
                      expected_existing={"residential": 0})
    with pytest.raises(ValidationError):
        UnitsGenerate(groups=[_gruppo()])                                                     # chiave obbligatoria
    ok = UnitsGenerate(client_request_id=chiave, dry_run=False, groups=[_gruppo()],
                       expected_existing={"residential": 0, "commercial": 0, "other": 0, "pertinenze": 0})
    assert ok.groups[0].count == 2
    for errato in ({"property_ids": [1, 1], "changes": {"floor": "1"}}, {"property_ids": [1], "changes": {}},
                   {"property_ids": [1], "changes": {"record_kind": "crm"}},
                   {"property_ids": [1], "changes": {"census_class": "commercial"}},
                   {"property_ids": list(range(1, 302)), "changes": {"floor": "1"}},
                   {"property_ids": [1], "changes": {"surface_source": "measured"}}):
        with pytest.raises(ValidationError):
            UnitsBulkUpdate(**errato)


def test_r01_rotte_sotto_api_property_con_lo_stesso_contesto():
    from operator_auth.dependencies import legacy_basic_agency_context
    from property.router import router
    attese = {("GET", "/api/property/buildings/{building_id}/composition"),
              ("PUT", "/api/property/buildings/{building_id}/composition"),
              ("POST", "/api/property/buildings/{building_id}/units/generate"),
              ("POST", "/api/property/buildings/{building_id}/units/generate/{batch_key}/undo"),
              ("POST", "/api/property/buildings/{building_id}/units/bulk-update")}
    trovate = {}
    for r in router.routes:
        for metodo in r.methods:
            if (metodo, r.path) in attese:
                trovate[(metodo, r.path)] = r
    assert set(trovate) == attese
    for rotta in trovate.values():
        assert legacy_basic_agency_context in [d.call for d in rotta.dependant.dependencies]


def test_r02_nessun_file_protetto_ne_dipendenze_nuove():
    testo = (ROOT / "property" / "composition.py").read_text(encoding="utf-8")
    importati = set(re.findall(r"^\s*(?:from|import) ([\w.]+)", testo, re.M))
    assert importati <= {"__future__", "uuid", "psycopg2", "psycopg2.extras", "core.database", "core.exceptions", "core",
                         ".", ".catalog"}, importati
    assert "voice" not in testo and "acquisitions" not in testo
