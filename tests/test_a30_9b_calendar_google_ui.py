"""A30-9B - §42, la UI del pannello Google Calendar dentro l'Agenda, ESEGUITA.

Come `test_a30_4_agenda_ui.py`: cercare stringhe nei sorgenti (gia' fatto in
`test_07b_*`/`test_08_*` di quel file) dice che una parola c'e', non che il
pannello mostra lo stato giusto, che "Collega Google" usi SOLO l'URL del
server, o che un errore del backend non mostri mai un falso "Collegato".

Girano i moduli VERI - `calendar-sync-api.js`, `agenda-calendar-sync-panel.js`,
`agenda-model.js` (per `errorMessage`) - in node, con lo stub di DOM di P26-4
(riusato, non duplicato: stesso `El`, stesso `fetch` scriptato) e due aggiunte
minime che questo pannello richiede e P26-4 non aveva bisogno di dare:
`window.location.search/pathname/assign` e `window.confirm`.

Senza node le prove sono SKIPPED (BLOCKED, mai PASS) - stessa disciplina del
resto della Agenda.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.test_p26_4_shell_runtime import DOM, FETCH  # noqa: E402 - stub riusato
from tests.test_a30_4_agenda_ui import _senza_commenti  # noqa: E402 - riusato

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
MODEL = ASSETS / "agenda" / "agenda-model.js"
CALENDAR_SYNC_API = ASSETS / "agenda" / "calendar-sync-api.js"
CALENDAR_SYNC_PANEL = ASSETS / "components" / "agenda" / "agenda-calendar-sync-panel.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node non e' disponibile: le prove del pannello Google Calendar non "
           "sono state eseguite. Da riportare come BLOCKED, mai PASS.",
)


def _testo(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _stage(tmp_path: Path) -> Path:
    (tmp_path / "agenda").mkdir()
    (tmp_path / "core").mkdir()
    (tmp_path / "components" / "agenda").mkdir(parents=True)
    (tmp_path / "agenda" / "agenda-model.mjs").write_text(_testo(MODEL), encoding="utf-8")
    api = _testo(CALENDAR_SYNC_API)
    assert api.count("from '../core/auth.js'") == 1
    (tmp_path / "agenda" / "calendar-sync-api.mjs").write_text(
        api.replace("from '../core/auth.js'", "from '../core/auth.mjs'"), encoding="utf-8")
    (tmp_path / "core" / "auth.mjs").write_text(
        "export let expired = 0;\nexport function sessionExpired() { expired += 1; }\n",
        encoding="utf-8")
    pannello = _testo(CALENDAR_SYNC_PANEL)
    assert pannello.count("from '../../agenda/calendar-sync-api.js'") == 1
    assert pannello.count("from '../../agenda/agenda-model.js'") == 1
    pannello = (pannello
                .replace("from '../../agenda/calendar-sync-api.js'",
                        "from '../../agenda/calendar-sync-api.mjs'")
                .replace("from '../../agenda/agenda-model.js'",
                        "from '../../agenda/agenda-model.mjs'"))
    (tmp_path / "components" / "agenda" / "agenda-calendar-sync-panel.mjs").write_text(
        pannello, encoding="utf-8")
    return tmp_path


# window.location del DOM di P26-4 ha solo `hash`: qui servono anche
# `search`/`pathname` (letti da agenda-page.js, non da questo file - qui
# servono solo per `window.location.assign`, che il pannello chiama per
# andare all'URL che il SERVER ha restituito) e `window.confirm` (Disconnetti).
EXTRA_DOM = """
window.location.search = '';
window.location.pathname = '/os/';
window.location.assign = (url) => { globalThis.__assigned = url; };
globalThis.__confirmReturns = true;
window.confirm = () => globalThis.__confirmReturns;
"""


def _node(tmp_path: Path, corpo: str) -> dict:
    cartella = _stage(tmp_path)
    driver = cartella / "driver.mjs"
    driver.write_text(
        DOM + FETCH + EXTRA_DOM + "\n" + textwrap.dedent(corpo) + "\n", encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=cartella)
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _mount(before: str = "", after: str = "") -> str:
    """Codice comune: scripta `/status` (e quanto altro serve) PRIMA di
    montare, monta, aspetta che si assesti, poi esegue eventuali azioni
    dell'operatore (click, `refresh()`) DOPO che il pannello esiste."""
    return f"""
      const mod = await import('./components/agenda/agenda-calendar-sync-panel.mjs');
      {before}
      const container = document.createElement('div');
      const pannello = mod.mountCalendarSyncPanel(container, {{ isStale: () => false }});
      await __settle();
      {after}
    """


def _report(extra=""):
    return f"""
      console.log(JSON.stringify({{
        text: container.visibleText(),
        html: container.outerHTML,
        calls: __calls.map((c) => ({{url: c.url, method: (c.options||{{}}).method || 'GET',
          credentials: (c.options||{{}}).credentials}})),
        assigned: globalThis.__assigned || null,
        {extra}
      }}));
    """


# ---------------------------------------------------------------------------
# 1-4: I QUATTRO STATI (§28)
# ---------------------------------------------------------------------------

def test_01_non_configurato(tmp_path):
    o = _node(tmp_path, _mount(
        "__script([{status: 200, body: {configured: false, enabled: false, "
        "connection_status: 'not_configured'}}]);") + _report())
    assert "Non configurato" in o["text"]
    # nessun'azione possibile: non c'e' nulla da fare dall'Agenda
    assert "Collega Google" not in o["text"] and "Ricollega" not in o["text"]
    assert len(o["calls"]) == 1
    assert o["calls"][0]["url"] == "/api/calendar/google/status"
    assert o["calls"][0]["credentials"] == "include"


def test_02_non_collegato_offre_collega(tmp_path):
    o = _node(tmp_path, _mount(
        "__script([{status: 200, body: {configured: true, enabled: true, "
        "connection_status: 'not_connected', pending_sync_count: 0, failed_sync_count: 0}}]);"
    ) + _report())
    assert "Non collegato" in o["text"]
    assert "Collega Google" in o["text"]
    assert "Disconnetti" not in o["text"] and "Riprova sincronizzazione" not in o["text"]


def test_03_collegato_offre_resync_e_disconnetti(tmp_path):
    o = _node(tmp_path, _mount(
        "__script([{status: 200, body: {configured: true, enabled: true, "
        "connection_status: 'connected', pending_sync_count: 2, failed_sync_count: 1}}]);"
    ) + _report())
    assert "Collegato" in o["text"]
    assert "Riprova sincronizzazione" in o["text"] and "Disconnetti" in o["text"]
    assert "Collega Google" not in o["text"] and "Ricollega" not in o["text"]
    # i contatori compaiono, ma nessun provider_subject/segreto
    assert "2 in coda" in o["text"] and "1 da riprovare" in o["text"]


def test_04_richiede_nuovo_accesso_offre_ricollega(tmp_path):
    o = _node(tmp_path, _mount(
        "__script([{status: 200, body: {configured: true, enabled: true, "
        "connection_status: 'needs_reauth', pending_sync_count: 0, failed_sync_count: 0}}]);"
    ) + _report())
    assert "Richiede nuovo accesso" in o["text"]
    assert "Ricollega" in o["text"]
    assert "Riprova sincronizzazione" not in o["text"] and "Disconnetti" not in o["text"]


# ---------------------------------------------------------------------------
# 5: ERRORE DEL BACKEND - MAI UN FALSO "COLLEGATO" (§28)
# ---------------------------------------------------------------------------

def test_05_errore_di_rete_sullo_status_non_mostra_mai_falso_successo(tmp_path):
    o = _node(tmp_path, _mount("__script([{throw: true}]);") + _report())
    assert "Collegato" not in o["text"] and "Non collegato" not in o["text"]
    assert "Errore" in o["text"]
    assert "Impossibile contattare il server" in o["text"]
    assert "Riprova" in o["text"]


def test_05b_errore_5xx_sullo_status_non_mostra_mai_falso_successo(tmp_path):
    o = _node(tmp_path, _mount(
        "__script([{status: 500, body: {detail: 'Errore interno'}}]);") + _report())
    assert "Errore" in o["text"]
    assert "Collegato" not in o["text"]
    assert "Errore interno" not in o["text"]              # mai il dettaglio grezzo del server


# ---------------------------------------------------------------------------
# 6-7: COLLEGA/RICOLLEGA - SOLO L'URL DEL SERVER (§29)
# ---------------------------------------------------------------------------

def test_06_collega_usa_solo_l_url_restituito_dal_server(tmp_path):
    o = _node(tmp_path, _mount(
        before="__script([{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'not_connected', pending_sync_count: 0, failed_sync_count: 0}}, "
               "{status: 200, body: {authorization_url: "
               "'https://accounts.google.com/o/oauth2/v2/auth?FINTO=1'}}]);",
        after="container.querySelectorAll('button').find((b) => b.textContent === "
              "'Collega Google').dispatch('click');\nawait __settle();",
    ) + _report())
    assert o["assigned"] == "https://accounts.google.com/o/oauth2/v2/auth?FINTO=1"
    assert o["calls"][-1]["url"] == "/api/calendar/google/connect"
    assert o["calls"][-1]["method"] == "POST"


def test_07_ricollega_needs_reauth_chiama_connect_come_collega(tmp_path):
    o = _node(tmp_path, _mount(
        before="__script([{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'needs_reauth', pending_sync_count: 0, failed_sync_count: 0}}, "
               "{status: 200, body: {authorization_url: 'https://esempio.test/via-server'}}]);",
        after="container.querySelectorAll('button').find((b) => b.textContent === "
              "'Ricollega').dispatch('click');\nawait __settle();",
    ) + _report())
    assert o["assigned"] == "https://esempio.test/via-server"
    assert o["calls"][-1]["url"] == "/api/calendar/google/connect"


def test_07b_connect_falso_del_client_non_esiste_mai_url_costruito():
    """Statico, non eseguito: il pannello non contiene NESSUNA stringa verso
    Google - lo si e' provato anche a runtime sopra (l'unico URL che finisce
    in `window.location.assign` e' quello che il fetch scriptato ha
    restituito), ma qui si blinda che il codice non potrebbe farlo nemmeno
    con un altro scenario."""
    codice = _senza_commenti(_testo(CALENDAR_SYNC_PANEL))
    assert "accounts.google.com" not in codice
    assert "client_id" not in codice and "client_secret" not in codice
    assert codice.count("window.location.assign(") == 1
    assert "window.location.assign(esito.authorization_url)" in codice


# ---------------------------------------------------------------------------
# 8: RESYNC (§26, §29)
# ---------------------------------------------------------------------------

def test_08_riprova_sincronizzazione_mostra_il_conteggio(tmp_path):
    o = _node(tmp_path, _mount(
        before="__script([{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'connected', pending_sync_count: 0, failed_sync_count: 0}}, "
               "{status: 200, body: {status: 'ok', requeued: 3}}]);",
        after="container.querySelectorAll('button').find((b) => b.textContent === "
              "'Riprova sincronizzazione').dispatch('click');\nawait __settle();",
    ) + _report())
    assert "3 appuntamenti rimessi in coda" in o["text"]
    assert o["calls"][-1]["url"] == "/api/calendar/google/resync"
    assert o["calls"][-1]["method"] == "POST"


# ---------------------------------------------------------------------------
# 9-10: DISCONNETTI - CONFERMA NATIVA, POI RILETTURA (§25)
# ---------------------------------------------------------------------------

def test_09_disconnetti_chiede_conferma_e_poi_richiama_lo_status(tmp_path):
    o = _node(tmp_path, _mount(
        before="__script([{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'connected', pending_sync_count: 0, failed_sync_count: 0}}, "
               "{status: 200, body: {status: 'disconnected'}}, "
               "{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'not_connected', pending_sync_count: 0, failed_sync_count: 0}}]);",
        after="container.querySelectorAll('button').find((b) => b.textContent === "
              "'Disconnetti').dispatch('click');\nawait __settle();",
    ) + _report())
    assert [c["url"] for c in o["calls"]] == [
        "/api/calendar/google/status", "/api/calendar/google/disconnect",
        "/api/calendar/google/status"]
    assert "Non collegato" in o["text"]
    assert "Collega Google" in o["text"]


def test_10_disconnetti_annullato_non_chiama_il_backend(tmp_path):
    o = _node(tmp_path, "globalThis.__confirmReturns = false;\n" + _mount(
        before="__script([{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'connected', pending_sync_count: 0, failed_sync_count: 0}}]);",
        after="container.querySelectorAll('button').find((b) => b.textContent === "
              "'Disconnetti').dispatch('click');\nawait __settle();",
    ) + _report())
    assert len(o["calls"]) == 1                 # solo lo /status iniziale
    assert "Collegato" in o["text"]


# ---------------------------------------------------------------------------
# 11: NESSUN BADGE PER-CARD, NESSUN provider_subject (§28)
# ---------------------------------------------------------------------------

def test_11_provider_subject_non_arriva_mai_in_pagina(tmp_path):
    o = _node(tmp_path, _mount(
        "__script([{status: 200, body: {configured: true, enabled: true, "
        "connection_status: 'connected', provider_subject: 'sub-segreto-123', "
        "pending_sync_count: 0, failed_sync_count: 0}}]);"
    ) + _report())
    assert "sub-segreto-123" not in o["text"]
    assert "sub-segreto-123" not in o["html"]


# ---------------------------------------------------------------------------
# 12: UNA RILETTURA (REFRESH) NON RIPETE STATI VECCHI (isStale)
# ---------------------------------------------------------------------------

def test_12_refresh_esposto_rilegge_lo_status(tmp_path):
    o = _node(tmp_path, _mount(
        before="__script([{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'not_connected', pending_sync_count: 0, failed_sync_count: 0}}, "
               "{status: 200, body: {configured: true, enabled: true, "
               "connection_status: 'connected', pending_sync_count: 0, failed_sync_count: 0}}]);",
        after="await pannello.refresh();\nawait __settle();",
    ) + _report())
    assert len(o["calls"]) == 2
    assert "Collegato" in o["text"]
