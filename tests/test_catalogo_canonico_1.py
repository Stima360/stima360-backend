"""CATALOGO-CANONICO-1 (FASE D) - catalogo canonico, migration 087 e 088, aggancio al sito.

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
SU88 = (ROOT / "migrations" / "088_catalogo_canonico_1b_site_inbox.sql").read_text(encoding="utf-8")
GIU88 = (ROOT / "migrations" / "088_catalogo_canonico_1b_site_inbox_down.sql").read_text(encoding="utf-8")


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

def test_m01_la_087_e_valida_per_il_runner_e_additiva():
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 087 non e' piu' l'ultima (088)
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    m087 = next(m for m in tutte if m.version.startswith("087_"))
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


def test_h04_senza_contatto_e_lead_nessuna_scheda(monkeypatch):
    """Il bridge senza contatto o lead: l'invio si conserva comunque (prima di
    ogni trasferimento) e il contatto/lead si cerca in `lead_stime`; se non
    c'e', nessuna scheda (`no_contact_lead`, verificato su PostgreSQL nel 09)."""
    from property import site_sync

    class Cursore:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params=None):
            self.sql.append(sql)

        def fetchone(self):
            return None

    for esito in ({"status": "linked", "contact_id": 3, "lead_id": None}, {"status": "skipped", "contact_id": 3}, {}):
        cur = Cursore()
        sub = {"stima_id": 1, "agency_id": 1, "contact_id": esito.get("contact_id"), "lead_id": esito.get("lead_id")}
        assert site_sync._contatto_e_lead(cur, sub) == (None, None) and "lead_stime" in cur.sql[0]
    # senza 087/088 (o senza la stima) nessun trasferimento, e nessuna eccezione
    monkeypatch.setattr(site_sync, "record_public_stima", lambda **k: {"status": "not_installed"})
    assert site_sync.sync_public_stima(None, stima_id=1, raw={}, bridge_result=None) == {
        "status": "skipped", "reason": "not_installed"}


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


# --- completamento: prefill, identita' della richiesta, tipologia da verificare ---------

def test_s01_dettagliata_precompilata_uguale_non_e_una_dichiarazione():
    cat = _cat()
    rapida = {"comune": "Tortoreto", "mq": "85,5", "tipologia": "Appartamento"}
    # cio' che /api/prefill serve: `stime`, con mq intero e i default del backend
    servito = {"comune": "Tortoreto", "mq": 86, "tipologia": "Appartamento", "piano": "1", "locali": 3,
               "anno": 2000, "via": "Zona", "pertinenze": "garage", "mqGarage": 18, "numBalconi": 0}
    assert "piano" not in cat.map_site_payload(rapida).fields
    pre = cat.map_site_payload(servito, detailed=True)
    # rimandato tale e quale: nulla da dichiarare
    d = cat.map_site_payload(dict(servito), detailed=True)
    tolti = cat.separate_prefilled(d, pre, None)
    assert d.fields == {} or set(d.fields) <= {"city", "region", "province"}
    assert {"surface_sqm", "floor", "rooms", "year_built", "accessory:box"} <= set(tolti)
    assert d.pertinenze_declared is False                     # elenco invariato: nessuna pertinenza tolta
    # cambiato dal cliente: correzione esplicita
    d = cat.map_site_payload({**servito, "mq": "90", "piano": "2", "mqGarage": "20"}, detailed=True)
    cat.separate_prefilled(d, pre, None)
    assert (d.fields["surface_sqm"], d.fields["floor"]) == (Decimal("90.00"), "2")
    assert d.accessories["box"]["surface_sqm"] == Decimal("20.00") and "rooms" not in d.fields
    # confermato (campi_dichiarati): vale anche se uguale; lista o stringa
    for dichiarati in (["locali", "anno"], cat.declared_fields({"campi_dichiarati": "locali, anno"})):
        d = cat.map_site_payload(dict(servito), detailed=True)
        cat.separate_prefilled(d, pre, dichiarati)
        assert (d.fields["rooms"], d.fields["year_built"]) == (3, 2000) and "floor" not in d.fields
    # senza prefill conservato (stima senza riga `stime`): nulla si toglie
    d = cat.map_site_payload(dict(servito), detailed=True)
    assert cat.separate_prefilled(d, None, None) == [] and d.fields["floor"] == "1"


def test_s02_invio_conservato_senza_dati_di_contatto_e_identita_della_richiesta():
    cat = _cat()
    chiave = "4b0f3a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
    valori, altre = cat.declared_payload({"mq": "85,5", "nome": "Mario", "email": "m@example.test",
                                          "telefono": "333", "consenso_marketing": True, "note": "privata",
                                          "client_request_id": chiave, "campi_dichiarati": ["mq"], "stima_id": 5,
                                          "pertinenze": "garage", "mqGarage": "18,5"})
    assert valori == {"mq": "85,5", "pertinenze": "garage", "mqGarage": "18,5"}       # COME inviati
    assert altre == ["consenso_marketing", "email", "nome", "note", "telefono"]       # solo i nomi
    assert str(cat.request_id({"client_request_id": chiave})) == chiave
    for non_valida in (None, "", "abc", 12):
        assert cat.request_id({"client_request_id": non_valida}) is None
    assert cat.declared_fields({}) is None and cat.declared_fields({"campi_dichiarati": "mq;piano anno"}) == ["anno", "mq", "piano"]


def test_s03_le_chiavi_del_prefill_coincidono_con_main():
    """`PREFILL_KEYS` e' la copia di cio' che `/api/prefill` restituisce: se
    il sorgente cambia, questa sentinella lo dice."""
    cat = _cat()
    sorgente = (ROOT / "main.py").read_text(encoding="utf-8")
    corpo = sorgente[sorgente.index("async def prefill(t: str):"):]
    corpo = corpo[: corpo.index("return dict(zip(keys, row))")]
    chiavi = re.findall(r'"(\w+)"', corpo[corpo.index("keys = ["):])
    colonne = re.findall(r"s\.(\w+)", corpo[corpo.index("SELECT"): corpo.index("FROM stime s")])
    coppie = dict(zip(chiavi, colonne))
    attese = {k: c for k, c in coppie.items() if k not in ("id", "nome", "cognome", "email", "telefono")}
    assert dict(cat.PREFILL_KEYS) == attese


def test_s04_tipologia_sconosciuta_da_verificare_mai_altro():
    cat = _cat()
    from property import catalog
    e = cat.map_site_payload({"tipologia": "Loft"})
    assert "property_type" not in e.fields
    assert e.unverified["property_type"] == {"raw": "Loft", "reason": "Tipologia non presente nel catalogo"}
    # «Altro» scelto esplicitamente resta una scelta: nessun «da verificare»
    assert cat.map_site_payload({"tipologia": "Altro"}).fields["property_type"] == "other"
    assert cat.map_site_payload({"tipologia": "Altro"}).unverified == {}
    da_verificare = {"property_type": "other", "city": "Tortoreto",
                     "metadata": {"site_unverified": {"property_type": {"raw": "Loft"}}}}
    assert catalog.type_to_verify(da_verificare)
    assert catalog.generated_title(da_verificare) == "Tipologia da verificare · Tortoreto"
    assert catalog.generated_title({"property_type": "other", "city": "Tortoreto"}).startswith("Altro")
    assert not catalog.type_to_verify({**da_verificare, "property_type": "villa"})


def test_m02_la_088_e_l_ultima_additiva_e_con_i_tipi_di_database_py():
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la 088 e' l'ultima
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: la 088 non e' piu' l'ultima (089)
    m088 = next(m for m in tutte if m.version.startswith("088_"))
    assert m088.version == "088_catalogo_canonico_1b_site_inbox"
    assert m088.down_available and not m088.non_transactional and runner.validate_migration(m088) == []
    eseguibile = "\n".join(r for r in SU88.splitlines() if not r.strip().startswith("--"))
    assert not re.search(r"^\s*(BEGIN|COMMIT)\s*;", eseguibile, re.M)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bDROP\s+TABLE\b|\bDROP\s+COLUMN\b|"
                         r"\bTRUNCATE\b|\bALTER\s+COLUMN\b", eseguibile, re.I)
    # le 28 colonne: le stesse, con gli STESSI tipi, di database.py (mai eseguito sul TEST)
    legacy = (ROOT / "database.py").read_text(encoding="utf-8")
    legacy = legacy[legacy.index("def migrazione_stime_dettagliate_completa"):]
    legacy = legacy[: legacy.index('""")')]
    attese = {n.lower(): t for n, t in re.findall(r"ADD COLUMN IF NOT EXISTS (\w+) ([A-Z]+(?:\(\d+\))?)", legacy)}
    blocco = eseguibile[eseguibile.index("ALTER TABLE stime_dettagliate"): eseguibile.index("CREATE TABLE")]
    trovate = {n: t for n, t in re.findall(r"ADD COLUMN IF NOT EXISTS (\w+) ([A-Z]+(?:\(\d+\))?)", blocco)}
    assert len(attese) == 28 and trovate == attese
    assert "CREATE TABLE IF NOT EXISTS site_submissions" in eseguibile
    assert "uq_site_submissions_quick" in eseguibile and "uq_site_submissions_detail" in eseguibile
    # la down: transazione propria, si ferma con invii non trasferiti, NON toglie le colonne
    assert re.search(r"^\s*BEGIN\s*;", GIU88, re.M) and re.search(r"^\s*COMMIT\s*;", GIU88, re.M)
    assert "RAISE EXCEPTION" in GIU88 and "'pending', 'failed'" in GIU88
    eseguibile_giu = "\n".join(r for r in GIU88.splitlines() if not r.strip().startswith("--"))
    assert "stime_dettagliate" not in eseguibile_giu and "DROP COLUMN" not in eseguibile_giu


def test_r01_lo_script_di_recupero_e_chiuso_fuori_dal_test(monkeypatch, capsys):
    import importlib
    script = importlib.import_module("scripts.site_sync_recover")
    for nome in (None, "", "stima360_db", "stima360_db_prod", "altro_test_qualsiasi"):
        if nome is None:
            monkeypatch.delenv("DB_NAME", raising=False)
        else:
            monkeypatch.setenv("DB_NAME", nome)
        assert script.main(["--census"]) == 2
    monkeypatch.setenv("DB_NAME", "stima360_db_test")
    assert script.main(["--apply"]) == 2
    assert script.main(["--apply", "--confirm-database", "stima360_db"]) == 2
    assert "BLOCKED" in capsys.readouterr().err
