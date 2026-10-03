"""CENSIMENTO-1 Fase 2 - CATALOGHI (progetto CENSIMENTO-0 §10 Fase 2, §1 dec. 1, §5).

Perimetro: il catalogo delle categorie catastali (52 voci) con i
suggerimenti per tipologia, la tipologia nuova `storage` (Cantina / Deposito)
in enums/etichette, form-options esteso e la riga di compatibilita' in
property_admin. NESSUNA rotta, NESSUN campo nuovo negli schemi, NESSUNA UI:
sono le fasi successive.

Livelli:
  A. catalogo - regole pure, coerenza con il CHECK di formato della 083;
  B. service senza database - form-options;
  C. property_admin (file legacy) - la lista delle tipologie e' la stessa del
     backend, `node --check`;
  D. perimetro - gli schemi NON cambiano in questa fase.
Il comportamento su PostgreSQL vero (schema completo ricostruito col runner)
e' in `tests/test_censimento_2_catalog_postgres.py`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from property import catalog, enums
from property import service as property_service
from property.schemas import PropertyCreate, PropertyUpdate
from tests.test_crm_ops_2_property_form import FakeRepo, _ctx

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONE_083 = ROOT / "migrations" / "083_censimento_1_buildings_units.sql"
PROPERTY_ADMIN_JS = ROOT / "static" / "property_admin" / "assets" / "app.js"
NODE = shutil.which("node")


# ---------------------------------------------------------------------------
# A. Catalogo
# ---------------------------------------------------------------------------

def _check_di_formato_della_083():
    """La regex del CHECK `properties_cadastral_category_chk`, letta dal file
    della migration applicata: il catalogo deve starci dentro tutto."""
    testo = MIGRAZIONE_083.read_text(encoding="utf-8")
    m = re.search(r"cadastral_category ~ '([^']+)'", testo)
    assert m, "CHECK di formato della 083 non trovato"
    return re.compile(m.group(1))


def test_a01_le_52_voci_nei_gruppi_dichiarati():
    voci = catalog.CADASTRAL_CATEGORIES
    assert len(voci) == 52
    per_gruppo = {}
    for v in voci:
        per_gruppo[v["group"]] = per_gruppo.get(v["group"], 0) + 1
    assert per_gruppo == {"A": 11, "B": 8, "C": 7, "D": 10, "E": 9, "F": 7}
    codici = [v["code"] for v in voci]
    assert len(set(codici)) == 52 and set(codici) == catalog.CADASTRAL_CATEGORY_CODES
    # ordine del quadro: gruppo per gruppo, numeri crescenti
    assert codici == sorted(codici, key=lambda c: (c[0], int(c.split("/")[1])))
    assert all(v["group"] == v["code"][0] for v in voci)
    assert all(v["label"].strip() for v in voci)
    # le discrepanze del progetto: B/8 resta, D/11 e D/12 non entrano
    assert "B/8" in codici and "D/11" not in codici and "D/12" not in codici


def test_a02_storiche_e_senza_rendita_secondo_le_fonti_ufficiali():
    """A/5 e A/6: soppresse dalla circ. Min. Finanze - Dir. Gen. Catasto n. 5
    del 14/03/1992, restano selezionabili come "storiche" (piano approvato).
    Gruppo F: TUTTE senza rendita - F/1-F/5 per il D.M. 28/1998 art. 3 c. 2,
    F/6 per la circ. Agenzia del Territorio 1/T dell'8/05/2009, F/7 per la
    circ. Agenzia delle Entrate 18/E dell'8/06/2017 ("senza attribuzione di
    rendita")."""
    storiche = {v["code"] for v in catalog.CADASTRAL_CATEGORIES if v["historical"]}
    senza_rendita = {v["code"] for v in catalog.CADASTRAL_CATEGORIES if v["no_income"]}
    gruppo_f = {v["code"] for v in catalog.CADASTRAL_CATEGORIES if v["group"] == "F"}
    assert storiche == {"A/5", "A/6"}
    assert gruppo_f == {"F/1", "F/2", "F/3", "F/4", "F/5", "F/6", "F/7"}
    assert senza_rendita == gruppo_f                 # F/6 e F/7 comprese
    # fuori dal gruppo F nessuna voce e' "senza rendita"; nessuna storica fuori da A/5-A/6
    assert not any(v["no_income"] for v in catalog.CADASTRAL_CATEGORIES if v["group"] != "F")
    assert not any(v["historical"] for v in catalog.CADASTRAL_CATEGORIES if v["code"] not in ("A/5", "A/6"))


def test_a03_ogni_codice_sta_nel_check_di_formato_della_083():
    formato = _check_di_formato_della_083()
    for v in catalog.CADASTRAL_CATEGORIES:
        assert formato.fullmatch(v["code"]), v["code"]
        assert len(v["code"]) <= 5                     # VARCHAR(5) della 083
        assert v["code"] == v["code"].strip().upper()  # gia' nella forma che il DB salva


def test_a04_suggerimenti_per_tipologia_dentro_il_catalogo():
    sugg = catalog.CADASTRAL_SUGGESTIONS
    assert set(sugg) == enums.PROPERTY_TYPES
    for tipo, voce in sugg.items():
        assert set(voce) == {"suggested", "secondary"}, tipo
        tutti = voce["suggested"] + voce["secondary"]
        assert set(tutti) <= catalog.CADASTRAL_CATEGORY_CODES, tipo
        assert len(set(tutti)) == len(tutti), tipo       # mai la stessa voce due volte
    # la tabella del progetto (§5), voce per voce dove e' decisiva
    assert sugg["apartment"]["suggested"] == ("A/2", "A/3", "A/4")
    assert sugg["villa"]["suggested"] == ("A/7", "A/8")           # villa != A/8 automatico
    assert sugg["garage"]["suggested"] == ("C/6",)
    assert sugg["storage"]["suggested"] == ("C/2",)
    assert sugg["office"]["suggested"] == ("A/10",)
    assert sugg["building"]["suggested"] == () and sugg["land"]["suggested"] == ()
    assert sugg["other"] == {"suggested": (), "secondary": ()}


@pytest.mark.parametrize("valore,atteso", [
    (None, None), ("", None), ("   ", None),
    ("A/3", "A/3"), (" a/3 ", "A/3"), ("c/6", "C/6"), ("A/10", "A/10"), ("f/7", "F/7"),
])
def test_a05_validazione_contro_il_catalogo_nella_forma_del_db(valore, atteso):
    assert catalog.validate_cadastral_category(valore) == atteso


@pytest.mark.parametrize("fuori", ["A/99", "A/0", "X/1", "D/11", "D/12", "A3", "A/", "A/02", "casa"])
def test_a06_fuori_catalogo_rifiutato_anche_se_il_formato_passa(fuori):
    """Il DB ha solo un CHECK di FORMATO (A/99 lo passerebbe): l'appartenenza
    al catalogo e' responsabilita' del service, qui."""
    with pytest.raises(ValueError, match="Categoria catastale"):
        catalog.validate_cadastral_category(fuori)


def test_a07_storage_e_una_tipologia_a_tutti_gli_effetti():
    assert "storage" in enums.PROPERTY_TYPES
    assert catalog.PROPERTY_TYPE_LABELS["storage"] == "Cantina / Deposito"
    assert set(catalog.PROPERTY_TYPE_LABELS) == enums.PROPERTY_TYPES
    assert catalog.generated_title({"property_type": "storage", "city": "Fermo"}) == "Cantina / Deposito · Fermo"
    assert PropertyCreate(property_type="storage").property_type == "storage"
    assert PropertyUpdate(property_type="storage").property_type == "storage"
    with pytest.raises(ValueError):
        PropertyCreate(property_type="cantina")


# ---------------------------------------------------------------------------
# B. form-options
# ---------------------------------------------------------------------------

def test_b01_form_options_porta_il_catalogo_e_i_suggerimenti(monkeypatch):
    monkeypatch.setattr(property_service, "repository", FakeRepo())
    opzioni = property_service.form_options(_ctx("agency_owner"))
    # il contratto di CRM-OPS-2 resta intero...
    assert {"territory", "territory_sources", "energy_classes", "property_types",
            "can_assign", "agents"} <= set(opzioni)
    assert {t["value"] for t in opzioni["property_types"]} == enums.PROPERTY_TYPES
    assert {"value": "storage", "label": "Cantina / Deposito"} in opzioni["property_types"]
    # ...e si estende con il catalogo catastale
    assert opzioni["cadastral_categories"] == catalog.cadastral_categories_for_form()
    assert len(opzioni["cadastral_categories"]) == 52
    assert opzioni["cadastral_categories"][0] == {"code": "A/1", "group": "A",
                                                  "label": "Abitazioni di tipo signorile",
                                                  "historical": False, "no_income": False}
    assert opzioni["cadastral_categories"][-1] == {"code": "F/7", "group": "F",
                                                   "label": "Infrastrutture di reti pubbliche di comunicazione (circ. 18/E)",
                                                   "historical": False, "no_income": True}
    assert opzioni["cadastral_suggestions"]["apartment"] == {"suggested": ["A/2", "A/3", "A/4"],
                                                             "secondary": ["A/1", "A/11", "A/5", "A/6"]}
    assert set(opzioni["cadastral_suggestions"]) == enums.PROPERTY_TYPES
    json.dumps(opzioni)                                # serializzabile tal quale
    # il catalogo restituito e' una copia: chi lo riceve non muta la costante
    opzioni["cadastral_categories"][0]["label"] = "x"
    assert catalog.CADASTRAL_CATEGORIES[0]["label"] == "Abitazioni di tipo signorile"


def test_b02_form_options_uguale_per_chi_non_assegna(monkeypatch):
    monkeypatch.setattr(property_service, "repository", FakeRepo())
    agente = property_service.form_options(_ctx("agent"))
    assert agente["can_assign"] is False and agente["agents"] == []
    assert len(agente["cadastral_categories"]) == 52 and "storage" in agente["cadastral_suggestions"]


# ---------------------------------------------------------------------------
# C. property_admin: la riga di compatibilita' (decisione 1)
# ---------------------------------------------------------------------------

def _tipologie_di_property_admin():
    testo = PROPERTY_ADMIN_JS.read_text(encoding="utf-8")
    m = re.search(r"^const PROPERTY_TYPES=\[([^\]]*)\];", testo, re.M)
    assert m, "PROPERTY_TYPES non trovata in property_admin"
    return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()], testo


def test_c01_property_admin_conosce_le_stesse_tipologie_del_backend():
    """Il rischio verificato nel progetto (§1 dec. 1): senza `storage` nella
    lista, aprire in modifica un immobile storage farebbe ricadere la tendina
    sulla prima voce e un salvataggio cambierebbe la tipologia in silenzio."""
    tipologie, testo = _tipologie_di_property_admin()
    assert set(tipologie) == enums.PROPERTY_TYPES
    assert "storage" in tipologie and len(tipologie) == len(set(tipologie))
    # l'option della tendina e la validazione del payload leggono la stessa lista
    assert "PROPERTY_TYPES.map(x=>`<option value=\"${x}\"" in testo
    assert "if(!PROPERTY_TYPES.includes(propertyType))throw new Error('property_type non valido')" in testo


@pytest.mark.skipif(NODE is None, reason="node non disponibile: node --check NON eseguito (BLOCKED)")
def test_c02_property_admin_node_check():
    subprocess.run([NODE, "--check", str(PROPERTY_ADMIN_JS)], check=True, timeout=60)


# ---------------------------------------------------------------------------
# D. Perimetro: niente di Fase 3 in questa fase
# ---------------------------------------------------------------------------

def test_d01_gli_schemi_non_cambiano_in_fase_2():
    for model in (PropertyCreate, PropertyUpdate):
        fields = set(getattr(model, "model_fields", None) or model.__fields__)
        assert not fields & {"cadastral_category", "cadastral_municipality_code", "cadastral_section",
                             "cadastral_sheet", "cadastral_parcel", "cadastral_subunit", "record_kind",
                             "building_id", "parent_property_id", "client_request_id"}, model.__name__
