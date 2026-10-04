"""CENSIMENTO-1 Fase 5 - la UI chiede al server il TIPO di schede che le serve,
mai un elenco misto filtrato nel browser.

  d01 ricerca globale: entrambi i tipi (`record_kind=all`, decisione 2 di
      CENSIMENTO-0), la scheda di censimento etichettata
  d02 client del censimento: «Collega esistente» chiede entrambi i tipi, il
      tab Censimento le sole unita' censite
  d03 Shell eseguita: il tab Commerciale legge l'elenco di default
      (operativo); il tab Censimento elenca palazzine E unita' censite
      (`record_kind=census`), una riga apre la scheda; nessuna scrittura
  d04 scheda contatto: le unita' censite collegate si contano a parte e sono
      etichettate; l'elenco per lead chiede entrambi i tipi
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_censimento_4_ui as f4  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)
from tests.test_censimento_4_ui import _rotte, _run, _scritture  # noqa: E402

node = f4.node
ASSETS = f4.ASSETS


def _node(script):
    esito = subprocess.run([a30_5.NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


@node
def test_d01_ricerca_globale_entrambi_i_tipi_e_censimento_etichettato():
    out = _node(f"""
      const {{ searchGlobal }} = await import('{(ASSETS / 'core' / 'global-search.js').as_posix()}');
      const urls = [];
      const api = async (u) => {{
        urls.push(u);
        if (u.startsWith('/api/property/properties')) return {{ items: [
          {{ id: 1, title: 'Trilocale', code: 'IMM-1', record_kind: 'crm', commercial_status: 'active' }},
          {{ id: 2, title: 'Via Roma 10 · 2º', code: 'IMM-2', record_kind: 'census', commercial_status: 'draft' }} ] }};
        return {{ items: [] }};
      }};
      const r = await searchGlobal('roma', api);
      console.log(JSON.stringify({{ urls, items: r.items.filter((x) => x.type === 'property').map((x) => [x.id, x.typeLabel]) }}));
    """)
    assert "/api/property/properties?search=roma&limit=5&record_kind=all" in out["urls"]
    assert out["items"] == [[1, "IMMOBILE"], [2, "IMMOBILE · CENSIMENTO"]]


@node
def test_d02_client_censimento_collega_esistente_e_unita_censite():
    out = _node(f"""
      globalThis.window = {{ location: {{ hash: '' }}, addEventListener() {{}} }};
      globalThis.document = {{ addEventListener() {{}}, querySelector() {{ return null; }} }};
      const urls = [];
      globalThis.fetch = async (u) => {{ urls.push(u); return {{ status: 200, ok: true, async json() {{ return {{ items: [] }}; }} }}; }};
      const api = await import('{(f4.CENSUS_DIR / 'census-api.js').as_posix()}');
      await api.searchProperties('box', 10);
      await api.listCensusUnits({{ search: 'roma' }});
      await api.listCensusUnits();
      console.log(JSON.stringify(urls));
    """)
    assert out == ["/api/property/properties?search=box&limit=10&record_kind=all",
                   "/api/property/properties?search=roma&limit=50&offset=0&record_kind=census",
                   "/api/property/properties?limit=50&offset=0&record_kind=census"]


@node
def test_d03_tab_commerciale_operativo_tab_censimento_palazzine_e_unita(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const commerciale = chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/property/properties?')).map((c) => c.url);
      C().querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').find((b) => b.dataset.mode === 'census').dispatch('click'); await wait(); await wait();
      const tutte = chiamate().filter((c) => c.m === 'GET' && c.url.startsWith('/api/property/properties?')).map((c) => c.url);
      const unita = C().querySelector('#census-units-area').querySelectorAll('tr.row-clickable').map((r) => r.dataset.rowId);
      const testo = C().querySelector('#immobili-census-panel').visibleText();
      C().querySelector('#census-units-area').querySelector('tr.row-clickable').dispatch('click'); await wait();
      report({ commerciale, tutte, unita, testo });
    """
    out = _run(staged, scenario, _rotte(), "#/immobili")
    assert out["commerciale"] and all("record_kind" not in u for u in out["commerciale"])     # default del server = operative
    censite = [u for u in out["tutte"] if "record_kind=census" in u]
    assert censite == ["/api/property/properties?limit=50&offset=0&record_kind=census"]
    assert out["unita"] == ["416"]
    assert "Palazzine" in out["testo"] and "Unità censite" in out["testo"] and "Prendi in carico" in out["testo"]
    assert "restano nell'elenco «Commerciale»" not in out["testo"]
    assert out["hash"] == "#/immobili/416"
    assert _scritture(out) == []


def test_d04_scheda_contatto_unita_censite_contate_a_parte_ed_etichettate():
    testo = (ASSETS / "views" / "contatto-dettaglio.js").read_text(encoding="utf-8")
    assert "['Immobili collegati', (data.properties || []).filter((p) => p.record_kind !== 'census').length]" in testo
    assert "['Unità censite collegate', (data.properties || []).filter((p) => p.record_kind === 'census').length]" in testo
    assert "p.record_kind === 'census' ? ` ${renderBadge('Censimento', 'warn')}` : ''" in testo
    assert "/api/property/properties?lead_id=${lead.id}&limit=50&record_kind=all" in testo
    # i SELETTORI per creare un collegamento nuovo restano operativi (default del server)
    assert "/api/property/properties?search=${encodeURIComponent(term)}&limit=10`" in testo
