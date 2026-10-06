"""CATALOGO-CANONICO-1 (FASE D) - catalogo canonico, migration 087, aggancio al sito.

Senza database: il catalogo (`property/site_catalog.py`) contro le sue fonti
nel codice (motore, `home_profile`, `owner/home_update`, schemi), la
traduzione del payload del sito nei quattro stati (non dichiarato / non so /
zero / no), la 087 per il runner e l'aggancio fail-open in `main.py`. Il
percorso end-to-end su PostgreSQL vero e' tests/test_catalogo_canonico_1_postgres.py.
"""
from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations" / "087_catalogo_canonico_1_site_attributes.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / "087_catalogo_canonico_1_site_attributes_down.sql").read_text(encoding="utf-8")


def _cat():
    from property import site_catalog
    return site_catalog


# --- un solo catalogo, allineato alle sue fonti --------------------------------

def test_c01_il_catalogo_coincide_con_motore_schemi_e_owner():
    cat = _cat()
    from owner.home_update import STATO_VALORI
    from property.catalog import ACCESSORY_KIND_LABELS
    from property.schemas import ACCESSORY_KINDS
    import valuation
    assert tuple(cat.CONDITIONS) == STATO_VALORI
    for stato in cat.CONDITIONS:                       # ogni stato e' una parola che il motore distingue
        assert valuation.coeff_stato(stato) != 1.00 or stato == "buono"
    assert set(cat.ACCESSORY_KINDS) == set(ACCESSORY_KINDS) == set(ACCESSORY_KIND_LABELS)
    assert cat.engine_tokens_covered()                 # ogni token del motore ha un tipo di accessorio
    assert valuation._posizione_coeff("frontemare") > 1 and valuation._posizione_coeff("seconda") > 1
    for fascia in cat.SEA_DISTANCES:
        assert valuation._distanza_coeff(fascia) >= 1.00
    # garage e posto auto restano DISTINTI; il garage del sito e' il box del censimento
    assert "garage" in cat.ACCESSORY_KINDS["box"]["site_tokens"]
    assert "garage" not in cat.ACCESSORY_KINDS["posto_auto"]["site_tokens"]
    assert ACCESSORY_KIND_LABELS["box"] == "Garage / box" and ACCESSORY_KIND_LABELS["posto_auto"] == "Posto auto"


def test_c02_form_options_porta_le_liste_del_catalogo():
    cat = _cat()
    liste = cat.labels_for_form()
    assert [x["value"] for x in liste["conditions"]] == list(cat.CONDITIONS)
    assert [x["value"] for x in liste["sea_positions"]] == ["frontemare", "seconda", "oltre"]
    assert [x["value"] for x in liste["sea_distances"]] == ["0-100", "100-300", "300-500", "500-1000"]
    sorgente = (ROOT / "property" / "service.py").read_text(encoding="utf-8")
    assert "**_site_catalog.labels_for_form()" in sorgente


# --- payload del sito -> scheda ----------------------------------------------------

def test_p01_quattro_stati_non_dichiarato_non_so_zero_no():
    cat = _cat()
    e = cat.map_site_payload({"comune": "Tortoreto", "mq": "0", "ascensore": "no", "vistaMareYN": "non so",
                              "barrieraMare": "", "pertinenze": "garage, balconi", "mqGarage": 0, "numBalconi": "0",
                              "spese_cond": "0"}, comune="Tortoreto")
    assert "surface_sqm" not in e.fields                  # 0 m2 = non compilato
    assert e.fields["elevator"] is False                  # "no" esplicito
    assert "sea_view" not in e.fields and "sea_barrier" not in e.fields   # non so / vuoto: niente
    assert e.fields["condo_fees"] == Decimal("0.00")     # spese 0: dichiarate
    assert e.accessories["box"] == {"present": True, "surface_sqm": None, "quantity": None, "raw": "garage"}
    assert e.accessories["balcone"]["quantity"] is None
    assert "posto_auto" not in e.accessories              # non spuntata: nessuna riga, nessun "no"
    for campo in ("floor", "rooms", "bathrooms", "year_built", "condition", "address"):
        assert campo not in e.fields, campo               # i default di `stime` non esistono qui


def test_p02_alias_precisione_e_valori_sconosciuti():
    cat = _cat()
    e = cat.map_site_payload({"comune": "sant'omero", "microzona": "contrade", "via": "Zona", "tipologia": "Loft",
                              "mq": "1.250,5", "piano": "Piano terra", "locali": "Quadrilocale", "stato": "Abitabile",
                              "posizioneMare": "fronte", "distanzaMare": "500–1000 m", "classe": "a4",
                              "pertinenze": "Posto Auto; posto barca | Posto moto coperto"}, comune="sant'omero")
    assert (e.fields["city"], e.fields["province"], e.fields["microzone"]) == ("Sant’Omero", "TE", "Contrade")
    assert "address" not in e.fields                      # "Zona" e' il segnaposto del backend
    assert e.fields["surface_sqm"] == Decimal("1250.50") and e.fields["floor"] == "T" and e.fields["rooms"] == 4
    assert e.fields["sea_distance"] == "500-1000" and e.fields["energy_class"] == "A4"
    assert "property_type" not in e.fields and "condition" not in e.fields and "sea_position" not in e.fields
    ignoti = {(u["site_field"], u["raw"]) for u in e.unmapped}
    assert {("tipologia", "Loft"), ("stato", "Abitabile"), ("posizioneMare", "fronte"), ("pertinenze", "posto barca")} <= ignoti
    assert set(e.accessories) == {"posto_auto", "posto_moto"}


def test_p03_impronta_stabile_e_sensibile():
    cat = _cat()
    a = cat.map_site_payload({"mq": "85", "locali": "3", "pertinenze": "garage"}, comune="Tortoreto")
    b = cat.map_site_payload({"mq": 85, "locali": "Trilocale", "pertinenze": "Garage"}, comune="Tortoreto")
    c = cat.map_site_payload({"mq": "86", "locali": "3", "pertinenze": "garage"}, comune="Tortoreto")
    assert cat.fingerprint(a) == cat.fingerprint(b) != cat.fingerprint(c)
    assert re.fullmatch(r"[0-9a-f]{64}", cat.fingerprint(a))


# --- migration 087 ---------------------------------------------------------------------

def test_m01_la_087_e_valida_per_il_runner_additiva_e_l_ultima():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    m087 = tutte[-1]
    assert m087.version == "087_catalogo_canonico_1_site_attributes"
    assert m087.down_available and not m087.non_transactional and runner.validate_migration(m087) == []
    eseguibile = "\n".join(r for r in SU.splitlines() if not r.strip().startswith("--"))
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", eseguibile, re.M)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|\bTRUNCATE\b", eseguibile, re.I)
    for colonna in re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)\s+([A-Z]+[^;]*);", eseguibile):
        assert "NOT NULL" not in colonna[1] or "DEFAULT" in colonna[1], colonna
    # il CHECK dei tipi si ALLARGA: ogni valore della 083 resta ammesso
    for kind in ("cantina", "soffitta", "posto_auto", "giardino", "terrazzo", "box", "deposito", "altro"):
        assert f"'{kind}'" in eseguibile.split("property_accessories_kind_chk CHECK")[1].split(";")[0]
    assert "delete_arch_property_trash_guard" not in eseguibile          # la funzione della 086 resta sua
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)
    assert "RAISE EXCEPTION" in GIU and "taverna" in GIU                 # la down si ferma con dati nuovi


# --- aggancio al sito: fail-open, dopo il bridge, solo `raw` ------------------------

def _handler(nome):
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    corpo = sorgente[sorgente.index(f'@app.post("/api/{nome}")'):]
    return corpo[: corpo.index("@app.", 10)]


def test_h01_salva_stima_chiama_solo_il_wrapper_dopo_il_bridge_con_raw():
    h = _handler("salva_stima")
    codice = "\n".join(r for r in h.splitlines() if not r.strip().startswith("#"))
    bridge = codice.index("bridge_result = core_service.bridge_public_stima")
    provisioning = codice.index("owner_provisioning.safe_provision_for_public_stima(")
    hook = codice.index("property_site_sync.safe_sync_public_stima(")
    p17 = codice.index("safe_record_event(")
    assert bridge < provisioning < hook < p17
    chiamata = codice[hook:codice.index(")", hook) + 1]
    assert "raw=raw" in chiamata and "bridge_result=bridge_result" in chiamata and "bridge_ctx" in chiamata
    assert "agency_id" not in chiamata and "data[" not in chiamata
    assert "sync_public_stima(" not in codice.replace("safe_sync_public_stima(", "")


def test_h02_dettagliata_dopo_il_commit_con_l_id_della_riga():
    h = _handler("salva_stima_dettagliata")
    codice = "\n".join(r for r in h.splitlines() if not r.strip().startswith("#"))
    assert "RETURNING id" in codice
    assert codice.index("conn.commit()") < codice.index("property_site_sync.safe_sync_detail(")
    assert codice.index("property_site_sync.safe_sync_detail(") < codice.index('return {"ok": True}')
    assert "sync_detail(" not in codice.replace("safe_sync_detail(", "")


def test_h03_i_wrapper_non_lasciano_uscire_eccezioni(monkeypatch):
    from property import site_sync
    def guasto(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(site_sync, "sync_public_stima", guasto)
    monkeypatch.setattr(site_sync, "sync_detail", guasto)
    assert site_sync.safe_sync_public_stima(None, stima_id=1, raw={}, bridge_result={"status": "linked"}) is None
    assert site_sync.safe_sync_detail(stima_id=1, detail_id=2, raw={}) is None


def test_h04_senza_contatto_e_lead_nessuna_scheda():
    from property import site_sync
    for esito in (None, {"status": "conflict"}, {"status": "skipped", "contact_id": 3},
                  {"status": "linked", "contact_id": 3, "lead_id": None}):
        assert site_sync.sync_public_stima(None, stima_id=1, raw={}, bridge_result=esito) == {
            "status": "skipped", "reason": "no_contact_lead"}


def test_h05_il_motore_non_cambia():
    """Stessi input, stessi numeri della base 3d3c3c7 (valuation.py non e' toccato)."""
    import valuation
    completo = {"comune": "Tortoreto", "microzona": "Lido Sud", "tipologia": "Appartamento", "mq": 85.5, "piano": "3",
                "locali": "Trilocale", "bagni": 2, "ascensore": "Sì", "anno": 1998, "stato": "ristrutturato",
                "posizioneMare": "seconda", "distanzaMare": "100-300", "barrieraMare": "no", "vistaMareYN": "si",
                "vistaMareDettaglio": "laterale", "vistaMare": "", "pertinenze": "garage, cantina, balconi",
                "mqGiardino": 0, "mqGarage": "18", "mqCantina": "0", "mqPostoAuto": 0, "mqTaverna": 0, "mqSoffitta": 0,
                "mqTerrazzo": 0, "numBalconi": "2", "via": "Via del Mare", "altroDescrizione": "Finemente arredato"}
    assert valuation.compute_from_payload(completo) == {
        "base_mq": 1650.0, "eur_mq_finale": 2504.4, "valore_pertinenze": 30000.0, "price_exact": 244126.0,
        "mq_calcolati": 86.0}
