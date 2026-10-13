"""CESTINO-GENERALE-3 (CG-3) - UI della cancellazione definitiva nel Cestino
unico (Shell OS), senza database.

  M  funzioni pure di building-deletion/building-deletion.js (il client
     condiviso della cancellazione definitiva): zona del comando per categoria
     (l'edificio resta sul suo flusso), riepilogo pronto / bloccato con «come
     risolvere», testi d'errore e d'esito, sempre escaped;
  S  statici: il client del Cestino (trash/*.js, views/cestino.js) resta senza
     parole e regole della cancellazione definitiva; il comando si aggancia
     SOLO nella vista globale; nessuna rotta letterale nuova; nessuna regola di
     permesso nel client; STIMA Voice non la conosce; nell'app vera sotto
     /api/platform/trash solo GET e il POST di conferma, nessuna rotta tenant;
  D  Shell VERA nello stub DOM:
     01 Super Admin, vista globale: zona su immobile e contatto, sull'edificio
        solo il collegamento a Richieste edifici; riepilogo, conferma
        disabilitata finche' il testo non e' esatto, POST con testo e impronta,
        messaggio d'esito, elenco ricaricato;
     02 blocchi: motivi, «come risolvere», collegamento; nessuna conferma,
        nessuna scrittura;
     03 riepilogo superato (409 PURGE_STALE): nuovo controllo, avviso, poi la
        conferma con la NUOVA impronta; 409 con blocchi: riepilogo bloccato;
     04 vista dell'agenzia (anche per un amministratore): nessuna zona, nessuna
        chiamata di cancellazione definitiva.

Senza node le prove M/D sono SKIPPED (BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_cestino_generale_1_ui as cg1
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
MODULO = ASSETS / "building-deletion" / "building-deletion.js"
CESTINO = ASSETS / "views" / "cestino.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
rt = a30_5._rt()
_codice = t2b3._codice


def _modello(expr):
    return cg1._modello(expr, MODULO)


PRONTO = {"kind": "property", "id": 31, "agency_id": 7, "label": "IMM-31 <b>", "in_trash": True, "eligible": True,
          "blockers": [], "will_delete": [{"table": "property_contacts", "label": "Collegamenti ai proprietari", "count": 1}],
          "preserved": [{"code": "OWNER_LINKS", "label": "Collegamenti ai proprietari (copiati nel registro)", "count": 1,
                         "items": []}],
          "fingerprint": "a" * 64, "confirm_text": "ELIMINA DEFINITIVAMENTE"}
BLOCCATO = {**PRONTO, "kind": "contact", "id": 41, "label": "Gino", "eligible": False, "will_delete": [], "preserved": [],
            "blockers": [{"code": "LEGAL_RECORDS", "label": "Consensi e messaggi da conservare", "count": 2, "items": [],
                          "help": "Serve la procedura di anonimizzazione"},
                         {"code": "BUILDING_IN_TRASH", "label": "Unità di un edificio nel Cestino", "count": 1, "items": [],
                          "href": "#/cestino/richieste-edifici", "help": "Elimina l'edificio"},
                         {"code": "X", "label": "Esterno", "count": 1, "items": [], "href": "https://x.test"}]}


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_zona_per_categoria():
    out = _modello("""(() => ({
      kinds: m.ITEM_PURGE_KINDS,
      immobile: m.itemPurgeZoneHtml('property', 31),
      contatto: m.itemPurgeZoneHtml('contact', 41),
      richiesta: m.itemPurgeZoneHtml('buy_request', 51),
      edificio: m.itemPurgeZoneHtml('building', 9),
      ignota: m.itemPurgeZoneHtml('lead', 1),
    }))()""")
    assert out["kinds"] == ["property", "contact", "buy_request"]
    for chiave, kind, eid in (("immobile", "property", 31), ("contatto", "contact", 41), ("richiesta", "buy_request", 51)):
        z = out[chiave]
        assert f'data-item-purge-zone="{kind}:{eid}"' in z and "data-item-purge>" in z and "Elimina definitivamente…" in z
        assert "diverso da «Ripristina»" in z and "data-item-purge-host" in z and 'role="alert"' in z
        assert "Ripristina</button>" not in z                                     # nessun ripristino nella zona
    e = out["edificio"]
    assert 'href="#/cestino/richieste-edifici"' in e and "data-item-purge>" not in e   # edificio: il suo flusso
    assert out["ignota"] == ""


@node
def test_m02_riepilogo_pronto_e_bloccato():
    out = _modello(f"""(() => ({{
      pronto: m.itemPurgeSummaryHtml({json.dumps(PRONTO)}),
      contatto: m.itemPurgeSummaryHtml({json.dumps({**PRONTO, "kind": "contact", "label": "Gino"})}),
      bloccato: m.itemPurgeSummaryHtml({json.dumps(BLOCCATO)}),
      nulla: m.itemPurgeSummaryHtml(null),
    }}))()""")
    p = out["pronto"]
    assert "data-item-purge-ready" in p and "non si può annullare" in p and "IMM-31 &lt;b&gt;" in p and "<b>\"" not in p
    assert 'data-will-delete="property_contacts"' in p and "(1)" in p
    assert 'data-preserved-code="OWNER_LINKS"' in p
    assert "data-item-purge-confirm disabled" in p and "ELIMINA DEFINITIVAMENTE" in p and "data-item-purge-text" in p
    assert "senza dati personali" in out["contatto"] and "senza dati personali" not in p
    b = out["bloccato"]
    assert "data-item-purge-blocked" in b and "Nulla viene cancellato" in b and "data-item-purge-confirm" not in b
    assert 'data-blocker-code="LEGAL_RECORDS"' in b and "Come risolvere: Serve la procedura di anonimizzazione" in b
    assert 'href="#/cestino/richieste-edifici" data-item-blocker-link' in b
    assert "https://x.test" not in b                                              # solo collegamenti interni
    assert out["nulla"] == ""


@node
def test_m03_testi_di_errore_e_di_esito():
    out = _modello("""(() => ({
      e404: m.itemPurgeErrorText({ status: 404 }),
      e403: m.itemPurgeErrorText({ status: 403 }),
      e409: m.itemPurgeErrorText({ status: 409, message: 'Qualcosa è cambiato' }),
      nessuno: m.itemPurgeErrorText(null),
      esito: m.finalDeletionDoneText({ label: 'IMM-31' }),
      senza: m.finalDeletionDoneText({}),
    }))()""")
    assert "già stato eliminato definitivamente" in out["e404"]
    assert "Super Admin generale designato" in out["e403"]
    assert out["e409"] == "Qualcosa è cambiato"
    assert "Nessun dato è stato cancellato" in out["nessuno"]
    assert "«IMM-31»" in out["esito"] and "registro del Cestino" in out["esito"] and "«" not in out["senza"]


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

def test_s01_client_del_cestino_senza_cancellazione_definitiva():
    cestino = "\n".join(_codice(p) for p in (*(ASSETS / "trash").glob("*.js"), CESTINO))
    for vietata in ("purge", "Elimina definitivamente", "Svuota", "Cancella", "Annulla", "apiDelete", "'DELETE'",
                    "is_platform_admin", "getSession", ".role", "agency_id"):
        assert vietata not in cestino, vietata
    testo = _codice(CESTINO)
    # il comando si aggancia SOLO nella vista globale (che legge solo il designato)
    assert testo.count("bindFinalDeletion(") == 1
    blocco = testo[testo.index("bindFinalDeletion(") - 200:testo.index("bindFinalDeletion(")]
    assert "if (card && stato.scope === 'platform') {" in blocco


def test_s02_client_condiviso_senza_regole_e_senza_rotte_letterali():
    modulo = _codice(MODULO)
    assert not re.search(r"/api/(platform/)?trash\b", modulo)                   # il percorso: unifiedTrashPath
    assert "unifiedTrashPath(`/${encodeURIComponent(kind)}/${Number(id)}/${coda}`, {}, { scope: 'platform' })" in modulo
    for regola in ("is_platform_admin", "getSession", ".role", "agency_owner", "deleted_by_user_id", "agency_id =",
                   "apiDelete", "'DELETE'"):
        assert regola not in modulo, regola
    assert "testo.value.trim() !== PURGE_CONFIRM_TEXT" in modulo and "check.fingerprint" in modulo
    voce = "\n".join(_codice(p) for p in (ASSETS / "voice").glob("*.js"))
    assert "purge" not in voce and "bindFinalDeletion" not in voce


def test_s03_app_reale_rotte_del_cestino():
    """Sotto /api/trash e /api/platform/trash: solo GET, piu' il SOLO POST di
    conferma della vista globale; nessuna rotta tenant di cancellazione."""
    import main
    rotte = sorted((m.upper(), p) for p, ops in main.app.openapi()["paths"].items()
                   if p == "/api/trash" or p.startswith(("/api/trash/", "/api/platform/trash")) for m in ops)
    scritture = [(m, p) for m, p in rotte if m != "GET"]
    assert scritture == [("POST", "/api/platform/trash/{kind}/{entity_id}/purge")], scritture
    assert ("GET", "/api/platform/trash/{kind}/{entity_id}/purge-check") in rotte
    assert not [p for _m, p in rotte if p.startswith("/api/trash") and "purge" in p]


def test_s04_css_zona_separata_e_tocco():
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* CESTINO-GENERALE-3"):]
    assert ".trash-final {" in blocco and "border-top" in blocco and "min-height: 44px" in blocco
    assert "@media (max-width: 640px)" in blocco and "width: 100%" in blocco
    assert "overflow-x: hidden" not in blocco


@node
def test_s05_node_check():
    for p in (MODULO, CESTINO, ASSETS / "trash" / "trash-unified.js", ASSETS / "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(p)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (p.name, esito.stderr)


# ---------------------------------------------------------------------------
# D - Shell vera nello stub DOM
# ---------------------------------------------------------------------------

VOCI = cg1.VOCI
LISTA = rt.ok({"items": VOCI, "has_more": False})


def _globale(*extra):
    return cg1._rotte("platform", extra=[
        ("GET", "/api/platform/me", [rt.ok(rt.PLATFORM_ME)]),
        ("GET", "/api/platform/agencies", [rt.ok(rt.AGENZIE)]),
        ("GET", "/api/platform/trash/counts", [rt.ok({**cg1.CONTI, "scope": "platform"})]),
        *extra,
        ("GET", "/api/platform/trash", [LISTA]),
    ])


HELPERS = r"""
const carta = (k) => C().querySelector(`[data-unified-item="${k}"]`);
const zona = (k) => carta(k) && carta(k).querySelector('[data-item-purge-zone]');
const getUrls = () => __calls.map((c) => c.url);
async function apri(k) { carta(k).querySelector('[data-item-purge]').dispatch('click'); await wait(); await wait(); }
function scrivi(k, v) { const t = carta(k).querySelector('[data-item-purge-text]'); t.value = v; t.dispatch('input'); }
const confermaDi = (k) => carta(k).querySelector('[data-item-purge-confirm]');
"""


def _run(staged, scenario, rotte, hash="#/cestino"):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"], c["body"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_super_admin_riepilogo_conferma_esito(staged):  # noqa: F811
    rotte = _globale(
        ("GET", "/api/platform/trash/property/31/purge-check", [rt.ok(PRONTO)]),
        ("POST", "/api/platform/trash/property/31/purge", [rt.ok({"kind": "property", "id": 31, "purged": True})]))
    scenario = r"""
      await wait(); await wait(); await wait();
      const zone = C().querySelectorAll('[data-item-purge-zone]').map((z) => z.dataset.itemPurgeZone);
      const edificio = zona('building:9').visibleText();
      const azioni = carta('property:31').querySelector('.trash-card-actions').visibleText();
      await apri('property:31');
      const riepilogo = carta('property:31').querySelector('[data-item-purge-host]').visibleText();
      const nascosto = carta('property:31').querySelector('[data-item-purge]').hidden;
      const primaDisabilitata = confermaDi('property:31').disabled;
      scrivi('property:31', 'elimina definitivamente');
      const minuscolo = confermaDi('property:31').disabled;
      scrivi('property:31', ' ELIMINA DEFINITIVAMENTE ');
      const esatto = confermaDi('property:31').disabled;
      const prima = __calls.length;
      confermaDi('property:31').dispatch('click'); await wait(); await wait(); await wait();
      const esito = toast();
      report({ zone, edificio, azioni, riepilogo, nascosto, primaDisabilitata, minuscolo, esatto, esito,
               dopo: getUrls().slice(prima) });
    """
    out = _run(staged, scenario, rotte)
    assert out["zone"] == ["property:31", "contact:41", "building:9"], out["zone"]
    assert "Richieste edifici" in out["edificio"]
    assert "Elimina definitivamente" not in out["azioni"]                         # separata dalle azioni della card
    assert "Eliminazione definitiva dell’immobile «IMM-31 <b>»" in out["riepilogo"]
    assert "Collegamenti ai proprietari" in out["riepilogo"] and out["nascosto"] is True
    assert out["primaDisabilitata"] is True and out["minuscolo"] is True and out["esatto"] is False
    assert _scritture(out) == [("POST", "/api/platform/trash/property/31/purge",
                                {"confirm_text": " ELIMINA DEFINITIVAMENTE ", "fingerprint": "a" * 64})]
    assert "Eliminazione definitiva completata «IMM-31 <b>»" in (out["esito"] or "")
    assert any(u.startswith("/api/platform/trash?") for u in out["dopo"])        # l'elenco si ricarica
    assert any(u.startswith("/api/platform/trash/counts") for u in out["dopo"])


@node
def test_d02_blocchi_come_risolvere_nessuna_scrittura(staged):  # noqa: F811
    rotte = _globale(("GET", "/api/platform/trash/contact/41/purge-check", [rt.ok(BLOCCATO)]))
    scenario = r"""
      await wait(); await wait(); await wait();
      await apri('contact:41');
      const host = carta('contact:41').querySelector('[data-item-purge-host]');
      const testo = host.visibleText();
      const confermaB = host.querySelector('[data-item-purge-confirm]');
      host.querySelector('[data-item-purge-close]').dispatch('click'); await wait();
      report({ testo, conferma: !!confermaB, chiuso: host.innerHTML === '',
               bottone: !carta('contact:41').querySelector('[data-item-purge]').hidden });
    """
    out = _run(staged, scenario, rotte)
    assert "Non si può eliminare definitivamente «Gino»" in out["testo"] and "Nulla viene cancellato" in out["testo"]
    assert "Consensi e messaggi da conservare" in out["testo"]
    assert "Come risolvere: Serve la procedura di anonimizzazione" in out["testo"]
    assert out["conferma"] is False and out["chiuso"] is True and out["bottone"] is True
    assert _scritture(out) == []


@node
def test_d03_riepilogo_superato_e_blocco_al_momento(staged):  # noqa: F811
    stale = {"status": 409, "body": {"detail": "Qualcosa è cambiato dopo il riepilogo", "code": "PURGE_STALE",
                                     "fingerprint": "b" * 64}}
    bloccato = {"status": 409, "body": {"detail": "Non si può eliminare definitivamente", "code": "NOT_PURGEABLE",
                                        "blockers": BLOCCATO["blockers"][:1]}}
    rotte = _globale(
        ("GET", "/api/platform/trash/property/31/purge-check", [rt.ok(PRONTO), rt.ok({**PRONTO, "fingerprint": "b" * 64})]),
        ("POST", "/api/platform/trash/property/31/purge", [stale, bloccato]))
    scenario = r"""
      await wait(); await wait(); await wait();
      await apri('property:31');
      scrivi('property:31', 'ELIMINA DEFINITIVAMENTE');
      confermaDi('property:31').dispatch('click'); await wait(); await wait(); await wait();
      const avviso = carta('property:31').querySelector('[data-item-purge-error]').visibleText();
      const dinuovo = !!confermaDi('property:31') && confermaDi('property:31').disabled;
      scrivi('property:31', 'ELIMINA DEFINITIVAMENTE');
      confermaDi('property:31').dispatch('click'); await wait(); await wait(); await wait();
      const host = carta('property:31').querySelector('[data-item-purge-host]').visibleText();
      report({ avviso, dinuovo, host, presente: !!carta('property:31') });
    """
    out = _run(staged, scenario, rotte)
    assert "Qualcosa è cambiato" in out["avviso"] and out["dinuovo"] is True    # nuovo riepilogo, testo da riscrivere
    posts = _scritture(out)
    assert [p[2]["fingerprint"] for p in posts] == ["a" * 64, "b" * 64]          # la seconda con la NUOVA impronta
    assert "Non si può eliminare definitivamente" in out["host"] and "Consensi e messaggi" in out["host"]
    assert out["presente"] is True


@node
def test_d04_vista_agenzia_nessuna_zona(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      report({ zone: C().querySelectorAll('[data-item-purge-zone]').length,
               carte: C().querySelectorAll('[data-unified-item]').length, urls: getUrls() });
    """
    out = _run(staged, scenario, cg1._rotte())
    assert out["carte"] == 3 and out["zone"] == 0
    assert not [u for u in out["urls"] if "purge" in u or u.startswith("/api/platform")]
    # amministratore di piattaforma in acting, scheda dell'agenzia: nessuna zona
    out = _run(staged, scenario, cg1._rotte("supreme", extra=[("GET", "/api/platform/agencies", [rt.ok(rt.AGENZIE)])]))
    assert out["zone"] == 0 and not [u for u in out["urls"] if "purge" in u]
    assert _scritture(out) == []
