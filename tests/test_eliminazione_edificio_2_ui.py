"""ELIMINAZIONE-EDIFICIO-2 - UI (Shell OS), senza database.

  M  `preservedHtml` / `purgeSummaryHtml`: i riferimenti storici conservati
     (comandi e «Annulla» di STIMA Voice, storico stati e proprietari copiati
     nel registro) nel riepilogo della cancellazione definitiva, pronta o
     bloccata, con il testo sempre ESCAPED; 403 leggibile;
  D  Shell VERA nello stub DOM: il riepilogo mostra i riferimenti conservati e
     la seconda conferma resta identica; un amministratore di piattaforma che
     NON e' il Super Admin generale designato (403) vede un solo avviso e
     nessun comando, nessuna scrittura.

Senza node le prove sono SKIPPED (BLOCKED), mai passate.
"""
from __future__ import annotations

import json

import pytest

from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_eliminazione_edificio_1_ui as ee1
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = ee1.node
rt = ee1.rt

CONSERVATI = [
    {"code": "VOICE_COMMANDS", "label": "Comandi di STIMA Voice che hanno creato o usato questi dati: restano nello "
                                        "storico dei comandi vocali", "count": 2,
     "items": [{"property_id": None, "label": "Comando vocale n. 12, passo 1 · Crea edificio · completato — edificio"},
               {"property_id": None, "label": "Comando vocale n. 13, passo 1 · Crea unità · completato — <b>IMM-500</b>"}]},
    {"code": "VOICE_UNDOS", "label": "Tentativi di «Annulla» di STIMA Voice: restano nello storico dei comandi vocali",
     "count": 1, "items": [{"property_id": None, "label": "Comando vocale n. 13, passo 1: annullato — IMM-500"}]},
    {"code": "STATUS_HISTORY", "label": "Storico degli stati delle unità: copiato nel registro prima della cancellazione",
     "count": 3, "items": [{"property_id": None, "label": "IMM-500 (2)"}, {"property_id": None, "label": "IMM-501 (1)"}]},
]
CHECK_OK = {**ee1.CHECK_OK, "preserved": CONSERVATI}
CHECK_NO = {**ee1.CHECK_NO, "preserved": CONSERVATI[:1]}


@node
def test_m01_riferimenti_conservati_nel_riepilogo():
    out = ee1._modello(f"""({{
      pronta: m.purgeSummaryHtml({json.dumps(CHECK_OK)}),
      bloccata: m.purgeSummaryHtml({json.dumps(CHECK_NO)}),
      vuota: m.preservedHtml([]),
      assente: m.purgeSummaryHtml({json.dumps(ee1.CHECK_OK)}),
      vietato: m.deletionErrorText({{ status: 403, message: 'Operazione riservata al Super Admin generale designato della piattaforma' }}),
      vietatoSenzaTesto: m.deletionErrorText({{ status: 403 }}),
    }})""")
    p = out["pronta"]
    assert "data-purge-ready" in p and "data-purge-preserved" in p and "Riferimenti storici conservati" in p
    for codice in ("VOICE_COMMANDS", "VOICE_UNDOS", "STATUS_HISTORY"):
        assert f'data-preserved-code="{codice}"' in p
    assert "Comando vocale n. 12, passo 1 · Crea edificio · completato" in p and "(2)" in p
    assert "&lt;b&gt;IMM-500&lt;/b&gt;" in p and "<b>IMM-500</b>" not in p           # sempre escaped
    assert "data-purge-confirm disabled" in p                                       # seconda conferma invariata
    b = out["bloccata"]
    assert "data-purge-blocked" in b and "data-purge-confirm" not in b and 'data-preserved-code="VOICE_COMMANDS"' in b
    assert out["vuota"] == "" and "data-purge-preserved" not in out["assente"]
    assert out["vietato"] == "Operazione riservata al Super Admin generale designato della piattaforma"
    assert out["vietatoSenzaTesto"] == "Operazione riservata al Super Admin generale designato."


@node
def test_d01_riepilogo_con_riferimenti_conservati(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      tabE().dispatch('click'); await wait(); await wait();
      grp(9).querySelector('[data-group-purge]').dispatch('click'); await wait(); await wait();
      const host = grp(9).querySelector('[data-group-purge-host]');
      const riepilogo = host.visibleText();
      const conservati = host.querySelector('[data-purge-preserved]').visibleText();
      const campo = grp(9).querySelector('[data-purge-text]');
      campo.value = 'ELIMINA DEFINITIVAMENTE'; campo.dispatch('input');
      grp(9).querySelector('[data-purge-confirm]').dispatch('click'); await wait(); await wait();
      report({ riepilogo, conservati });
    """
    out = ee1._run(staged, scenario, ee1._rotte(check=(CHECK_OK,)))
    assert "Riferimenti storici conservati" in out["conservati"]
    assert "restano nello storico dei comandi vocali" in out["conservati"]
    assert "copiato nel registro prima della cancellazione" in out["conservati"]
    assert "non si può annullare" in out["riepilogo"]
    assert ee1._scritture(out) == [("POST", "/api/platform/building-deletions/9/purge")]


def _rotte_vietate():
    vietato = {"status": 403, "body": {"detail": "Operazione riservata al Super Admin generale designato della "
                                                 "piattaforma", "code": "SUPER_ADMIN_REQUIRED"}}
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione("supreme"))]),
        ("GET", "/api/platform/me", [rt.ok({"user_id": 2, "is_platform_admin": True})]),
        ("GET", "/api/platform/agencies", [rt.ok([])]),
        ("GET", "/api/platform/building-deletion-requests?", [vietato]),
        ("GET", "/api/platform/building-deletions/trash", [vietato]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


@node
def test_d02_altro_amministratore_di_piattaforma_vede_solo_un_avviso(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      tabE().dispatch('click'); await wait(); await wait(); await wait();
      const sezione = C().querySelector('[data-deletion-admin]');
      report({ testo: sezione.visibleText(), avviso: !!sezione.querySelector('[data-deletion-forbidden]'),
               bottoni: sezione.querySelectorAll('button').length });
    """
    out = ee1._run(staged, scenario, _rotte_vietate())
    assert out["avviso"] is True and out["bottoni"] == 0
    assert "Super Admin generale designato" in out["testo"]
    assert ee1._scritture(out) == []
