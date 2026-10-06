"""FIX-MANDATE-1 - Shell: la motivazione dell'incarico e i dati da completare.

Stesso harness della UI del Cestino (tests/test_delete_arch_2b3_ui.py: Shell
VERA nello stub DOM, fetch instradata):

  1  il foglio «Elimina…» mostra la motivazione del backend e il suo
     collegamento («Apri incarico» -> #/incarichi/<id>); un collegamento
     esterno non viene mai reso;
  2  la sezione Incarichi segnala un incarico con dati incompleti.
"""
from __future__ import annotations

import json

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_delete_arch_2b3_ui import _modello, _rotte, _run, node, rt

ETICHETTA = "Incarico in corso. È nella sezione Incarichi. Dati dell'incarico incompleti: completali dalla scheda dell'incarico."


def _blocco(link):
    return {"can_trash": False, "blockers": [{"code": "MANDATE_PRESENT", "label": ETICHETTA,
                                               "items": [{"property_id": 30, "origin": "acquisition", "state": "active"}],
                                               "link": link}]}


@node
def test_m01_solo_collegamenti_interni():
    out = _modello("""[
      m.blockerView([{ code: 'MANDATE_PRESENT', label: 'x', items: [{}], link: { href: '#/incarichi/30', label: 'Apri incarico' } }])[0].link,
      m.blockerView([{ code: 'MANDATE_PRESENT', label: 'x', items: [{}], link: { href: 'https://evil.test/', label: 'x' } }])[0].link,
      m.blockerView([{ code: 'MANDATE_PRESENT', label: 'x', items: [{}] }])[0].link,
    ]""")
    assert out == [{"href": "#/incarichi/30", "label": "Apri incarico"}, None, None]


@node
def test_01_foglio_elimina_motivazione_e_apri_incarico(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#property-trash-btn').dispatch('click'); await wait(); await wait();
      const TDx = C().querySelector('#property-trash-dialog');
      const li = TDx.querySelector('[data-blocker="MANDATE_PRESENT"]');
      const a = li.querySelector('[data-blocker-link]');
      report({ testo: li.visibleText(), href: a ? a.getAttribute('href') : null, link: a ? a.textContent : null,
               disabilitata: TDx.querySelector('[data-trash-confirm]').disabled });
    """
    out = _run(staged, scenario, _rotte(check=(_blocco({"href": "#/incarichi/30", "label": "Apri incarico"}),)),
               "#/immobili/30")
    assert ETICHETTA in out["testo"]
    assert (out["href"], out["link"]) == ("#/incarichi/30", "Apri incarico")
    assert out["disabilitata"] is True
    esterno = _run(staged, scenario, _rotte(check=(_blocco({"href": "https://evil.test/", "label": "x"}),)),
                   "#/immobili/30")
    assert esterno["href"] is None


INCARICO = {"property_id": 30, "code": "IMM-30", "title": "Trilocale", "address": "Via Roma", "civic_number": "1",
            "city": "Giulianova", "mandate_type": None, "mandate_start": "2026-09-01", "mandate_end": None,
            "days_to_expiry": None, "expiry_state": "open_ended", "commercial_status": "mandate", "asking_price": None,
            "minimum_price": None, "agent_name": "Anna Agente", "main_owner_name": "Mario Rossi", "owners": [],
            "other_owners": [], "acquisition": {"id": 501, "visible": True}, "last_interaction": None,
            "missing_fields": ["mandate_type"]}


@node
def test_02_incarichi_segnala_i_dati_da_completare(staged):  # noqa: F811
    lista = {"items": [INCARICO, {**INCARICO, "property_id": 31, "code": "IMM-31", "mandate_type": "Esclusiva",
                                  "missing_fields": []}],
             "mandate_types": ["Esclusiva"], "expiry_filters": [], "sorts": ["expiry"]}
    rotte = f"__route('GET', '/api/property/mandates', ...{json.dumps([rt.ok(lista)])});\n" + _rotte()
    scenario = r"""
      await wait(); await wait();
      report({ righe: C().querySelectorAll('tr').map((r) => r.visibleText()) });
    """
    out = _run(staged, scenario, rotte, "#/incarichi")
    righe = [r for r in out["righe"] if "IMM-3" in r]
    assert len(righe) == 2
    assert "Dati da completare" in righe[0] and "Dati da completare" not in righe[1]
