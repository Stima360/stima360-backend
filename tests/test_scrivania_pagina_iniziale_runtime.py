"""SCRIVANIA VIVA - la pagina iniziale del CRM, sul banco della Shell (main.js vero,
DOM finto di P26-4, nessun server).

  01  chi accede dal modulo arriva sulla Scrivania (#/oggi), anche se l'indirizzo
      era rimasto su un'altra pagina (sessione scaduta mentre si lavorava altrove).
  02  un accesso rifiutato non sposta l'indirizzo: resta la schermata di accesso.
  03  senza accesso dal modulo (sessione gia' viva, ricarica) si resta dove si e':
      la regola non tocca il ripristino della sessione ne' la navigazione.
"""
from __future__ import annotations

import json

from tests.test_p26_4_shell_runtime import ANONYMOUS, BOOTED, ME, run_shell, staged  # noqa: F401

VUOTI = ", ".join(["{ status: 200, body: { items: [] } }"] * 12)

STATO = """
console.log(JSON.stringify({
  hash: window.location.hash,
  title: __dom.byId['page-title'].textContent,
  appHidden: __dom.byId['app-view'].hidden,
  loginHidden: __dom.byId['login-view'].hidden,
  urls: __calls.map((c) => c.url),
}));
"""


def test_01_l_accesso_dal_modulo_porta_alla_scrivania(staged):
    scenario = f"""
window.location.hash = '#/contatti';
__script([{{ status: 204 }}, {{ status: 200, body: ME_LIVE }}, {VUOTI}]);
__dom.byId['login-email'].value = 'operatore@example.test';
__dom.byId['login-password'].value = 'una-password';
__dom.byId['login-form'].dispatch('submit');
await __settle(60);
""".replace("ME_LIVE", json.dumps(ME)) + STATO
    out = run_shell(staged, scenario, ANONYMOUS)
    assert out["appHidden"] is False and out["loginHidden"] is True
    assert out["hash"] == "#/oggi", out["hash"]
    assert out["title"] == "Scrivania", out["title"]
    # la pagina dei contatti non e' stata nemmeno chiesta: si disegna solo la Scrivania
    assert not any(u.startswith("/api/contacts") for u in out["urls"]), out["urls"]


def test_02_un_accesso_rifiutato_non_sposta_l_indirizzo(staged):
    scenario = """
window.location.hash = '#/contatti';
__script([{ status: 401, body: { detail: 'Credenziali non valide.' } }]);
__dom.byId['login-email'].value = 'x@y.test';
__dom.byId['login-password'].value = 'sbagliata';
__dom.byId['login-form'].dispatch('submit');
await __settle(30);
""" + STATO
    out = run_shell(staged, scenario, ANONYMOUS)
    assert out["appHidden"] is True and out["loginHidden"] is False
    assert out["hash"] == "#/contatti", out["hash"]


def test_03_senza_accesso_dal_modulo_si_resta_dove_si_e(staged):
    scenario = f"""
__script([{VUOTI}]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(40);
""" + STATO
    out = run_shell(staged, scenario, BOOTED)
    assert out["appHidden"] is False
    assert out["hash"] == "#/contatti" and out["title"] == "Contatti", out
