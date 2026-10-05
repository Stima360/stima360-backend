"""DELETE-ARCH Fase 1B - scheda immobile, tab Proprietari: un lead
«Inserito per errore» NON e' una chiusura «Non vende».

Stesso driver DOM di VENDITORI-1 (`tests/test_venditori_1_ui.py`).
"""
from __future__ import annotations

import pytest  # noqa: F401

from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)
from tests.test_venditori_1_ui import IMMOBILE, LEAD_77, _modello, _rotte, _run, node


@node
def test_u01_modello_distingue_errore_da_chiusura():
    out = _modello("""(() => {
      const L = (lead_id, status, extra = {}) => ({ lead_id, status, relation_type: 'seller', pipeline: 'sell', contact_id: 41, stage: 'lost', ...extra });
      return {
        errore: m.sellerState([L(5, 'closed', { lost_reason: 'created_by_mistake' })], 41),
        vera: m.sellerState([L(5, 'closed', { lost_reason: 'Non vende più' })], 41),
        senzaMotivo: m.sellerState([L(5, 'closed')], 41),
        apertoVince: m.sellerState([L(5, 'closed', { lost_reason: 'created_by_mistake' }), L(3, 'open', { stage: 'new' })], 41),
        codice: m.MISTAKE_REASON,
      };
    })()""")
    assert out["errore"] == {"state": "mistake", "leadId": 5, "stage": "lost"}
    assert out["vera"]["state"] == "closed" and out["senzaMotivo"]["state"] == "closed"
    assert out["apertoVince"]["state"] == "open"
    assert out["codice"] == "created_by_mistake"


@node
def test_u02_tab_proprietari_errore_riconoscibile_e_non_non_vende(staged):  # noqa: F811
    leads = [{**LEAD_77, "status": "closed", "stage": "lost", "lost_reason": "created_by_mistake"},
             {**LEAD_77, "lead_id": 78, "id": 2, "contact_id": 42, "status": "closed", "stage": "lost",
              "lost_reason": "Non vende più"}]
    scenario = r"""
      await wait(); await wait();
      const celle = C().querySelectorAll('.seller-cell');
      report({ stati: statiCelle(), testi: celle.map((c) => c.visibleText()),
               bottoni: celle.map((c) => c.querySelectorAll('button').map((b) => b.textContent.trim())) });
    """
    out = _run(staged, scenario, _rotte(immobile={**IMMOBILE, "leads": leads}), "#/immobili/30/proprietari")
    assert out["stati"] == ["mistake", "closed"]
    errore, vera = out["testi"]
    assert "Inserito per errore" in errore and "Non vende" not in errore and "Riattiva" not in errore
    assert "Non vende" in vera and "Inserito per errore" not in vera
    assert out["bottoni"] == [["Vende?"], ["Riattiva"]]


def test_u03_la_scheda_immobile_porta_lost_reason_dei_lead():
    from pathlib import Path
    testo = (Path(__file__).resolve().parents[1] / "property" / "repository.py").read_text(encoding="utf-8")
    # DELETE-ARCH 1B: solo `lost_reason` in piu', letto via to_jsonb (valido anche sugli schemi ridotti)
    assert "SELECT pl.*,l.pipeline,l.stage,l.status,l.contact_id,to_jsonb(l)->>'lost_reason' AS lost_reason FROM property_leads pl" in testo
