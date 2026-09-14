"""P28 - la barra del Superadmin, ESEGUITA.

Stessa disciplina di `tests/test_p27_7_network_runtime.py`: i moduli VERI
girano in node, dentro uno stub di DOM, con `fetch` scriptato risposta per
risposta. Cercare stringhe nei sorgenti direbbe che la parola "SUPERADMIN"
compare da qualche parte; non direbbe che la barra si accende quando /me
dichiara un'impersonazione, che si spegne all'uscita, e - la cosa che conta
davvero - che i dati dell'agenzia precedente non restano nel documento.

IL TEST CHE GIUSTIFICA QUESTO FILE

`test_c1`. Entrare in un'agenzia cambia l'agenzia effettiva SENZA che la
sessione finisca, ed e' una situazione che prima di P28 non esisteva: fino a
ieri si cambiava agenzia solo facendo logout, e `clearApplicationSurface` si
occupava del resto. Adesso si puo' passare da A a B restando collegati, e se la
Shell si limitasse a ridisegnare, i contatti di A resterebbero sotto quelli di
B - o peggio, resterebbero e basta, se la nuova vista fallisse.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason=(
        "node non e' disponibile: le prove di runtime della barra Superadmin "
        "non sono state eseguite. Questo va riportato come BLOCKED, mai come "
        "PASS."
    ),
)

from tests.test_p26_4_shell_runtime import DOM, FETCH  # noqa: E402
from tests.test_p27_7_network_runtime import EXTRA_DOM  # noqa: E402

# I tre nodi della barra stanno nello stub condiviso di P26-4, insieme a tutti
# gli altri id che `index.html` porta: e' li' che lo stub rispecchia il
# documento vero, e tenerne una copia qui ne creerebbe una seconda definizione
# destinata a divergere.


def _stage(tmp_path: Path) -> Path:
    staged = tmp_path / "assets"
    shutil.copytree(ASSETS, staged)
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return staged


def run(staged: Path, scenario: str, script: str, hash: str = "#/contatti") -> dict:
    driver = staged.parent / "driver-acting.mjs"
    driver.write_text(
        DOM
        + FETCH
        + EXTRA_DOM
        + f"\nwindow.location.hash = '{hash}';\n"
        + f"\n__script({script});\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle();\n"
        + scenario
        + "\n",
        encoding="utf-8",
    )
    esito = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=60,
        cwd=staged.parent,
    )
    if esito.returncode != 0:
        raise AssertionError(
            f"il driver node e' fallito ({esito.returncode}):\n"
            f"{esito.stderr[-3000:]}\n--- stdout ---\n{esito.stdout[-2000:]}"
        )
    return json.loads(esito.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def staged(tmp_path_factory) -> Path:
    return _stage(tmp_path_factory.mktemp("acting"))


REPORT = """
console.log(JSON.stringify({
  barraNascosta: __dom.byId['acting-bar'].hidden === true,
  barraTesto: __dom.byId['acting-text'].textContent,
  content: __dom.byId['content'].visibleText(),
  html: __dom.deepHtml(__dom.byId['content']),
  urls: __calls.map((c) => c.url),
  methods: __calls.map((c) => (c.options.method || 'GET')),
  annunci: __inviati('stima360-acting'),
  hash: window.location.hash,
  navRoutes: __dom.byId['nav'].children.map((b) => b.dataset.route || ''),
}));
"""


def ok(body):
    return {"status": 200, "body": body}


def script(*voci):
    return json.dumps(list(voci))


SUPERADMIN = {
    "user_id": 1, "agency_id": None, "agency_name": None, "role": None,
    "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z",
    "acting": None, "home_agency_id": None, "home_agency_name": None,
}

DENTRO = {
    "user_id": 1, "agency_id": 34, "agency_name": "Agenzia Ospite", "role": None,
    "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z",
    "acting": {
        "agency_id": 34, "agency_name": "Agenzia Ospite",
        "entered_at": "2026-09-14T10:00:00Z",
    },
    "home_agency_id": None, "home_agency_name": None,
}

TENANT = {
    "user_id": 3, "agency_id": 7, "agency_name": "Agenzia A", "role": "agent",
    "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z",
    "acting": None, "home_agency_id": 7, "home_agency_name": "Agenzia A",
}

CONTATTI_A = {"items": [{"id": 111, "full_name": "#111", "email": None,
                         "phone": None, "status": "active", "created_at":
                         "2026-01-01T10:00:00Z"}], "total": 1}
CONTATTI_B = {"items": [{"id": 222, "full_name": "#222", "email": None,
                         "phone": None, "status": "active", "created_at":
                         "2026-01-01T10:00:00Z"}], "total": 1}


# ---------------------------------------------------------------------------
# A - LA BARRA
# ---------------------------------------------------------------------------

def test_a1_no_bar_for_an_ordinary_tenant(staged):
    out = run(staged, REPORT, script(ok(TENANT), ok(CONTATTI_A)))
    assert out["barraNascosta"] is True
    assert out["barraTesto"] == ""


def test_a2_no_bar_for_a_platform_admin_who_has_not_entered(staged):
    """Essere Superadmin non accende la barra: esserlo DENTRO un'agenzia si'."""
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(CONTATTI_A)))
    assert out["barraNascosta"] is True


def test_a3_the_bar_appears_while_acting(staged):
    out = run(staged, REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["barraNascosta"] is False


def test_a4_the_bar_names_the_agency_being_operated(staged):
    out = run(staged, REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert "Agenzia Ospite" in out["barraTesto"], out["barraTesto"]


def test_a5_the_bar_has_no_close_control(staged):
    """Si esce dall'agenzia, non si chiude un avviso."""
    sorgente = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    barra = sorgente.split('id="acting-bar"')[1].split("</div>")[0]
    for parola in ("chiudi", "close", "dismiss", "×"):
        assert parola not in barra.lower(), parola


# ---------------------------------------------------------------------------
# B - USCIRE
# ---------------------------------------------------------------------------

USCITA = """
  __script([
    { status: 204 },
    %s,
    %s
  ]);
  __dom.byId['acting-exit-btn'].dispatch('click');
  await __settle();
""" % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(CONTATTI_A)))


def test_b1_pressing_the_exit_button_calls_the_platform_route(staged):
    out = run(staged, USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert "/api/platform/agency-context/exit" in out["urls"], out["urls"]


def test_b2_the_exit_is_a_post(staged):
    out = run(staged, USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    indice = out["urls"].index("/api/platform/agency-context/exit")
    assert out["methods"][indice] == "POST"


def test_b3_the_session_is_re_read_from_the_server_after_the_exit(staged):
    """Non si aggiorna lo stato locale: si richiede /me. Il server decide."""
    out = run(staged, USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    uscita = out["urls"].index("/api/platform/agency-context/exit")
    assert "/api/operator-auth/me" in out["urls"][uscita + 1:], out["urls"]


def test_b4_the_bar_disappears_after_the_exit(staged):
    out = run(staged, USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["barraNascosta"] is True
    assert out["barraTesto"] == ""


# ---------------------------------------------------------------------------
# C - IL RESIDUO NEL DOM
#
# La proprieta' per cui questo file esiste.
# ---------------------------------------------------------------------------

def test_c1_the_previous_agency_data_is_not_left_in_the_document(staged):
    """Si esce da un'agenzia: cio' che si vedeva li' dentro sparisce dal DOM.

    Non nascosto: ASSENTE. E' la stessa lezione di P26-4 - dove commutare
    `hidden` lasciava i contatti dell'operatore precedente a un `hidden=false`
    di distanza - applicata al caso nuovo che P28 introduce: l'agenzia cambia
    mentre la sessione resta viva.

    Lo scenario tiene l'ultima risposta VUOTA di proposito. Se la Shell si
    limitasse a ridisegnare senza svuotare, la lista dell'agenzia ospite
    resterebbe li' sotto - ed e' precisamente il caso in cui il residuo fa
    danno: la vista nuova non ha niente da mettere al suo posto.
    """
    scenario = """
      __script([{ status: 204 }, %s, %s]);
      __dom.byId['acting-exit-btn'].dispatch('click');
      await __settle();
    """ % (json.dumps(ok(SUPERADMIN)), json.dumps({"status": 403, "body": {"detail": "no"}}))
    out = run(staged, scenario + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert "#222" not in out["content"], out["content"][:600]


def test_c2_the_incoming_agency_data_is_the_only_thing_on_screen(staged):
    out = run(staged, USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert "#222" not in out["content"]


def test_c3_a_logout_still_clears_everything(staged):
    """La regressione di P26-4: quel che funzionava prima funziona ancora."""
    scenario = """
      __script([{ status: 204 }, { status: 401, body: { detail: 'no' } }]);
      const { logout } = await import('%s');
      await logout();
      await __settle();
    """ % (staged / "core" / "auth.js").as_posix()
    out = run(staged, scenario + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert "#222" not in out["content"]
    assert out["barraNascosta"] is True


# ---------------------------------------------------------------------------
# D - LE ALTRE SCHEDE DELLO STESSO BROWSER
#
# Due schede NON sono due sessioni: condividono il cookie, quindi la stessa
# riga di `operator_sessions` e quindi lo stesso contesto di agenzia. Se una
# entra o esce, l'altra sta mostrando una barra sbagliata e - molto peggio - i
# dati di un'agenzia che il server non le servirebbe piu'.
#
# Qui si recita la parte della scheda che RICEVE: `__broadcast` invoca il
# gestore che `auth.js` ha registrato, esattamente come farebbe il browser
# quando l'altra scheda chiama `postMessage`.
# ---------------------------------------------------------------------------

RICEVE_USCITA = """
  __script([%s, %s]);
  __broadcast('stima360-acting');
  await __settle();
""" % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(CONTATTI_A)))


def test_d1_entering_announces_the_change_to_the_other_tabs(staged):
    """Chi entra avvisa. Senza, le altre schede non saprebbero mai."""
    scenario = """
      __script([{ status: 200, body: { acting_agency_id: 34,
                  acting_agency_name: 'Agenzia Ospite',
                  acting_agency_slug: 'ospite' } }, %s, %s]);
      const { enterAgency } = await import('%s');
      await enterAgency(34);
      await __settle();
    """ % (json.dumps(ok(DENTRO)), json.dumps(ok(CONTATTI_B)),
           (staged / "core" / "auth.js").as_posix())
    out = run(staged, scenario + REPORT, script(ok(SUPERADMIN), ok(CONTATTI_A)))
    assert out["annunci"], "nessun annuncio: le altre schede restano indietro"


def test_d2_exiting_announces_the_change_to_the_other_tabs(staged):
    out = run(staged, USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["annunci"], "nessun annuncio dopo l'uscita"


def test_d3_a_receiving_tab_re_reads_the_session_from_the_server(staged):
    """Il messaggio non porta dati e non viene creduto: si richiede /me.

    E' la stessa regola di enter/exit - il server decide in quale agenzia si
    sta - applicata al canale: se la scheda si fidasse del contenuto del
    messaggio, esisterebbe un modo di cambiare agenzia senza passare dal
    server, cioe' esattamente cio' che P26-1 ha eliminato.
    """
    out = run(staged, RICEVE_USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    dopo_il_boot = out["urls"][1:]
    assert "/api/operator-auth/me" in dopo_il_boot, out["urls"]


def test_d4_the_bar_disappears_in_the_receiving_tab(staged):
    """La scheda ferma non resta con la barra di un'agenzia da cui si e' usciti."""
    out = run(staged, RICEVE_USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["barraNascosta"] is True
    assert out["barraTesto"] == ""


def test_d5_no_data_of_the_previous_agency_survives_in_the_receiving_tab(staged):
    """LA PROPRIETA' CHE CONTA.

    La scheda mostrava i contatti dell'agenzia ospite. Un'altra scheda esce.
    Questa deve svuotarsi, non aspettare che l'operatore ci clicchi sopra.

    L'ultima risposta scriptata e' un 403 di proposito: se la Shell si
    limitasse a ridisegnare senza svuotare, l'elenco di prima resterebbe li'
    sotto - ed e' precisamente il caso in cui il residuo fa danno, perche' la
    vista nuova non ha niente da metterci al posto.
    """
    scenario = """
      __script([%s, %s]);
      __broadcast('stima360-acting');
      await __settle();
    """ % (json.dumps(ok(SUPERADMIN)),
           json.dumps({"status": 403, "body": {"detail": "no"}}))
    out = run(staged, scenario + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert "#222" not in out["content"], out["content"][:600]


def test_d6_the_receiving_tab_advances_the_session_epoch(staged):
    """L'epoch avanza: una vista in volo non dipinge piu' dopo il cambio.

    E' il meccanismo di P26-4, e qui serve per la ragione nuova di P28: fra la
    richiesta e la risposta puo' essere cambiata l'AGENZIA, non solo la
    sessione, e una risposta dell'agenzia precedente non deve raggiungere lo
    schermo.
    """
    scenario = """
      const { sessionEpoch } = await import('%s');
      const prima = sessionEpoch();
      __script([%s, %s]);
      __broadcast('stima360-acting');
      await __settle();
      console.log(JSON.stringify({ prima, dopo: sessionEpoch() }));
    """ % ((staged / "core" / "auth.js").as_posix(),
           json.dumps(ok(SUPERADMIN)), json.dumps(ok(CONTATTI_A)))
    out = run(staged, scenario, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["dopo"] > out["prima"], out


def test_d7_a_tab_that_receives_nothing_is_left_alone(staged):
    """Nessun messaggio, nessuna rilettura: il canale non fa polling."""
    out = run(staged, REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["urls"].count("/api/operator-auth/me") == 1, out["urls"]


def test_d8_the_receiving_handler_does_not_rebroadcast(staged):
    """Chi riceve rilegge e basta: due schede che si rimbalzano il messaggio
    sarebbero un anello senza fine."""
    out = run(staged, RICEVE_USCITA + REPORT, script(ok(DENTRO), ok(CONTATTI_B)))
    assert out["annunci"] == [], out["annunci"]


# ---------------------------------------------------------------------------
# E - IL CANALE NON DEVE TENERE VIVO IL PROCESSO
#
# Regressione trovata in review, e la sua guardia.
#
# `new BroadcastChannel(...)` a livello di modulo e' una risorsa ATTIVA. In un
# browser non cambia nulla; in node tiene vivo l'event loop, e QUALUNQUE
# processo che importi questo modulo - anche solo di rimbalzo, via
# `api-client.js` - non termina mai. Si e' fatto scoprire appendendo a tempo
# indeterminato `tests/test_p17b3_seller_timeline_ui.py`, che con l'acting non
# c'entra niente.
#
# Qui NON si usa lo stub: serve il `BroadcastChannel` VERO di node, perche' e'
# quello che ha il comportamento da verificare. Lo stub, essendo un oggetto
# qualunque, non tratterrebbe nulla e il test passerebbe per il motivo
# sbagliato.
# ---------------------------------------------------------------------------

def test_e1_importing_auth_does_not_keep_a_node_process_alive(staged, tmp_path):
    """Il processo deve USCIRE DA SOLO, senza che nessuno lo interrompa."""
    import subprocess as sp

    driver = tmp_path / "solo-import.mjs"
    driver.write_text(
        # `fetch` non viene mai chiamato - non si fa nessuna richiesta - ma il
        # modulo lo cattura all'import in alcuni percorsi: definirlo evita che
        # il processo esca per un ReferenceError, cioe' che il test passi
        # perche' e' fallito.
        "globalThis.fetch = async () => ({ status: 401, ok: false });\n"
        f"await import('{(staged / 'core' / 'auth.js').as_posix()}');\n"
        "console.log('importato');\n",
        encoding="utf-8",
    )
    esito = sp.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=20,
    )
    assert esito.returncode == 0, esito.stderr[-2000:]
    assert "importato" in esito.stdout


def test_e2_the_channel_is_unreferenced_when_the_runtime_offers_it(staged):
    """La guardia e' condizionale, e deve restarlo.

    `unref` non esiste nel browser: chiamarlo senza controllare romperebbe la
    Shell vera per far passare un test.
    """
    sorgente = (staged / "core" / "auth.js").read_text(encoding="utf-8")
    assert "typeof canale.unref === 'function'" in sorgente


# ---------------------------------------------------------------------------
# F - MODALITA' PLATFORM: il Superadmin SENZA acting non sta in una view tenant
#
# IL BUG, COM'E' STATO VISTO DAL VIVO
#
# Un platform admin senza membership ha `agency_id = null`: non ha nessuna
# agenzia, quindi `/api/core/*` gli risponde 403 - ed e' giusto, e' la
# decisione D1 di P27-1. Sbagliata era la Shell, che lo lasciava su "Contatti"
# e faceva partire la richiesta lo stesso, per poi mostrargli un errore che
# non descrive un guasto ma uno stato normale.
#
# La correzione e' UNA guardia, in UN punto: il router, prima che una view
# venga scelta. Non un `if` sparso in ogni vista - sarebbero venti posti da
# ricordare, e il ventunesimo arriverebbe senza.
#
# `RETE` sotto e' cio' che la sezione Rete chiede quando si apre: `/platform/me`
# e poi l'elenco delle agenzie. Se una di queste due comparisse in uno scenario
# che si aspetta zero chiamate, vorrebbe dire che la Rete NON e' stata
# disegnata.
# ---------------------------------------------------------------------------

AGENZIE = [
    {"id": 1, "name": "STIMA360", "slug": "stima360", "status": "active",
     "settings": {}, "created_at": "2026-01-01T10:00:00Z",
     "updated_at": "2026-01-01T10:00:00Z"},
]
PLATFORM_ME = {"user_id": 1, "is_platform_admin": True, "agency_id": None,
               "session_expires_at": "2030-01-01T00:00:00Z",
               "acting_agency_id": None, "acting_entered_at": None}

TENANT_ENDPOINTS = ("/api/core/", "/api/crm/", "/api/properties", "/api/buy",
                    "/api/match", "/api/proposals", "/api/sales",
                    "/api/flow", "/api/next-best-action")


def _chiamate_tenant(urls):
    return [u for u in urls if any(p in u for p in TENANT_ENDPOINTS)]


def test_f1_a_platform_admin_without_acting_lands_on_rete_not_on_contatti(staged):
    """T1 - login: la view precedente era Contatti, si finisce sulla Rete."""
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert out["hash"] == "#/rete", out["hash"]


def test_f2_and_no_tenant_request_is_ever_made(staged):
    """T1 - la richiesta tenant non viene nascosta: non parte proprio."""
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert _chiamate_tenant(out["urls"]) == [], out["urls"]


def test_f3_a_direct_refresh_on_a_tenant_route_redirects_too(staged):
    """T2 - ricaricare il browser su una rotta tenant finisce sulla Rete."""
    for rotta in ("#/contatti", "#/immobili", "#/acquirenti", "#/abbinamenti",
                  "#/attivita", "#/automazioni", "#/oggi"):
        out = run(staged, REPORT,
                  script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)), hash=rotta)
        assert out["hash"] == "#/rete", (rotta, out["hash"])
        assert _chiamate_tenant(out["urls"]) == [], (rotta, out["urls"])


def test_f4_the_context_error_never_reaches_the_screen(staged):
    """Il messaggio del bug non deve comparire, perche' la chiamata non parte."""
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert "contesto di agenzia" not in out["content"], out["content"][:400]


def test_f5_the_tenant_navigation_is_absent_in_platform_mode(staged):
    """Assente, non disabilitata: un bottone che non c'e' non si clicca."""
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert out["navRoutes"] == ["rete"], out["navRoutes"]


def test_f6_rete_stays_reachable_in_platform_mode(staged):
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/rete")
    assert "rete" in out["navRoutes"]
    assert out["hash"] == "#/rete"


def test_f7_exiting_from_contatti_lands_on_rete(staged):
    """T3 - il caso B del bug, dall'inizio alla fine."""
    scenario = """
      __script([{ status: 204 }, %s, %s, %s]);
      __dom.byId['acting-exit-btn'].dispatch('click');
      await __settle();
    """ % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(PLATFORM_ME)),
           json.dumps(ok(AGENZIE)))
    out = run(staged, scenario + REPORT,
              script(ok(DENTRO), ok(CONTATTI_B)), hash="#/contatti")
    assert out["hash"] == "#/rete", out["hash"]
    assert out["barraNascosta"] is True
    assert "#222" not in out["content"]
    assert out["navRoutes"] == ["rete"], out["navRoutes"]


def test_f8_no_tenant_request_is_made_after_the_exit(staged):
    """T3 - nessuna NUOVA richiesta tenant dopo l'uscita."""
    scenario = """
      __script([{ status: 204 }, %s, %s, %s]);
      __dom.byId['acting-exit-btn'].dispatch('click');
      await __settle();
    """ % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(PLATFORM_ME)),
           json.dumps(ok(AGENZIE)))
    out = run(staged, scenario + REPORT,
              script(ok(DENTRO), ok(CONTATTI_B)), hash="#/contatti")
    uscita = out["urls"].index("/api/platform/agency-context/exit")
    assert _chiamate_tenant(out["urls"][uscita:]) == [], out["urls"][uscita:]


def test_f9_a_receiving_tab_on_contatti_also_lands_on_rete(staged):
    """T4 - multi-tab: la scheda ferma su Contatti non mostra l'errore."""
    scenario = """
      __script([%s, %s, %s]);
      __broadcast('stima360-acting');
      await __settle();
    """ % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(PLATFORM_ME)),
           json.dumps(ok(AGENZIE)))
    out = run(staged, scenario + REPORT,
              script(ok(DENTRO), ok(CONTATTI_B)), hash="#/contatti")
    assert out["hash"] == "#/rete", out["hash"]
    assert "#222" not in out["content"]
    assert "contesto di agenzia" not in out["content"]


def test_f10_and_that_tab_makes_no_tenant_request_after_the_broadcast(staged):
    scenario = """
      const prima = __calls.length;
      __script([%s, %s, %s]);
      __broadcast('stima360-acting');
      await __settle();
      globalThis.__dopo = __calls.slice(prima).map((c) => c.url);
    """ % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(PLATFORM_ME)),
           json.dumps(ok(AGENZIE)))
    riporto = """
      console.log(JSON.stringify({ urls: globalThis.__dopo }));
    """
    out = run(staged, scenario + riporto,
              script(ok(DENTRO), ok(CONTATTI_B)), hash="#/contatti")
    assert _chiamate_tenant(out["urls"]) == [], out["urls"]


def test_f11_with_an_active_acting_contatti_still_works(staged):
    """T5 - nessuna regressione: dentro un'agenzia il CRM funziona."""
    out = run(staged, REPORT, script(ok(DENTRO), ok(CONTATTI_B)), hash="#/contatti")
    assert out["hash"] == "#/contatti", out["hash"]
    assert "#222" in out["content"], out["content"][:300]
    assert "contatti" in out["navRoutes"], out["navRoutes"]
    assert "rete" in out["navRoutes"], out["navRoutes"]


def test_f12_an_ordinary_tenant_is_never_redirected(staged):
    """T6 - un agente con la sua agenzia resta dov'e'. Nessuna Rete."""
    out = run(staged, REPORT, script(ok(TENANT), ok(CONTATTI_A)), hash="#/contatti")
    assert out["hash"] == "#/contatti", out["hash"]
    assert "#111" in out["content"], out["content"][:300]
    assert "rete" not in out["navRoutes"], out["navRoutes"]


def test_f13_going_back_to_a_tenant_route_after_the_exit_is_refused(staged):
    """T7 - history/back: la guardia vale anche li', e prima del fetch."""
    scenario = """
      __script([{ status: 204 }, %s, %s, %s]);
      __dom.byId['acting-exit-btn'].dispatch('click');
      await __settle();
      // L'utente preme "indietro": il browser rimette la rotta precedente e
      // annuncia il cambio. La guardia deve rispondere prima della vista.
      const prima = __calls.length;
      __script([%s, %s]);
      window.location.hash = '#/contatti';
      await window._fire('hashchange');
      await __settle();
      globalThis.__dopo = __calls.slice(prima).map((c) => c.url);
    """ % (json.dumps(ok(SUPERADMIN)), json.dumps(ok(PLATFORM_ME)),
           json.dumps(ok(AGENZIE)), json.dumps(ok(PLATFORM_ME)),
           json.dumps(ok(AGENZIE)))
    riporto = """
      console.log(JSON.stringify({
        hash: window.location.hash,
        urls: globalThis.__dopo,
        html: __dom.deepHtml(__dom.byId['content']),
      }));
    """
    out = run(staged, scenario + riporto,
              script(ok(DENTRO), ok(CONTATTI_B)), hash="#/contatti")
    assert out["hash"] == "#/rete", out["hash"]
    assert _chiamate_tenant(out["urls"]) == [], out["urls"]


def test_f14_the_guard_lives_in_one_place_and_reads_only_the_server_session(staged):
    """UNA guardia, e decide solo da cio' che `/me` ha detto.

    Se la condizione fosse ricopiata in ogni view sarebbero venti posti da
    ricordare; e se leggesse qualcosa che non viene dal server, sarebbe un
    modo di decidere l'agenzia dal client - cioe' cio' che P26-1 ha eliminato.
    """
    sorgente = (ASSETS / "core" / "auth.js").read_text(encoding="utf-8")
    assert "export function canUseTenantSurface" in sorgente

    viste = [p for p in (ASSETS / "views").glob("*.js")]
    colpevoli = [p.name for p in viste
                 if "canUseTenantSurface" in p.read_text(encoding="utf-8")]
    assert colpevoli == [], colpevoli


# ---------------------------------------------------------------------------
# G - LE DUE PORTE, IN ENTRAMBI I VERSI
#
# `canUseTenantSurface` non puo' limitarsi a "c'e' un'agenzia?". Un platform
# admin PUO' avere una membership - P27-1 decisione D4 lo permette
# esplicitamente - e in quel caso `agency_id` e' valorizzato anche quando non
# sta impersonando nessuno. Se bastasse quello, entrerebbe nel CRM senza
# passare da un ingresso dichiarato, cioe' senza la riga di audit che P28
# esiste per produrre.
#
# La regola e' quindi asimmetrica, e lo e' di proposito:
#
#   operatore normale   l'agenzia e' la sua membership, e basta quella.
#   platform admin      il CRM si apre SOLO con un acting esplicito, anche se
#                       possiede una membership.
#
# E il verso opposto: la Rete e' della piattaforma. Un tenant che ci arriva a
# mano non deve nemmeno chiedere `/api/platform/me` - il 403 arriverebbe, ed e'
# la difesa vera, ma una richiesta che si sa gia' rifiutata non si manda.
# ---------------------------------------------------------------------------

#: Platform admin CON membership e SENZA acting. Il caso che `agency_id != null`
#: da solo non sa distinguere.
SUPERADMIN_CON_MEMBERSHIP = {
    "user_id": 1, "agency_id": 7, "agency_name": "Casa Sua", "role": "agency_owner",
    "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z",
    "acting": None, "home_agency_id": 7, "home_agency_name": "Casa Sua",
}

OGGI = {"items": [], "total": 0}


def test_g1_a_platform_admin_with_a_membership_but_no_acting_is_platform_only(staged):
    """CASO 1 - la membership non apre il CRM: lo apre l'ingresso dichiarato."""
    out = run(staged, REPORT,
              script(ok(SUPERADMIN_CON_MEMBERSHIP), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert out["hash"] == "#/rete", out["hash"]


def test_g2_and_it_makes_no_tenant_request(staged):
    out = run(staged, REPORT,
              script(ok(SUPERADMIN_CON_MEMBERSHIP), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert _chiamate_tenant(out["urls"]) == [], out["urls"]


def test_g3_and_the_tenant_navigation_is_absent_for_it_too(staged):
    out = run(staged, REPORT,
              script(ok(SUPERADMIN_CON_MEMBERSHIP), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/contatti")
    assert out["navRoutes"] == ["rete"], out["navRoutes"]


def test_g4_entering_an_agency_reopens_the_crm_for_the_same_person(staged):
    """Il verso positivo: con l'acting il CRM torna, e non era mai stato tolto
    a chi ha diritto di starci - solo a chi non lo aveva dichiarato."""
    dentro_con_casa = dict(DENTRO)
    dentro_con_casa["home_agency_id"] = 7
    dentro_con_casa["home_agency_name"] = "Casa Sua"
    out = run(staged, REPORT, script(ok(dentro_con_casa), ok(CONTATTI_B)),
              hash="#/contatti")
    assert out["hash"] == "#/contatti", out["hash"]
    assert "#222" in out["content"], out["content"][:300]
    assert "contatti" in out["navRoutes"], out["navRoutes"]


def test_g5_a_tenant_forcing_the_network_route_is_sent_back(staged):
    """CASO 2 - la Rete e' della piattaforma: un tenant non ci arriva."""
    out = run(staged, REPORT, script(ok(TENANT), ok(OGGI)), hash="#/rete")
    assert out["hash"] == "#/oggi", out["hash"]


def test_g6_and_it_never_asks_the_platform_surface(staged):
    """Una richiesta che si sa gia' rifiutata non si manda.

    Il 403 di `require_platform_admin` resta la difesa vera e non e' stato
    toccato: quello che cambia e' che la Shell non lo va piu' a cercare.
    """
    out = run(staged, REPORT, script(ok(TENANT), ok(OGGI)), hash="#/rete")
    platform = [u for u in out["urls"] if u.startswith("/api/platform")]
    assert platform == [], out["urls"]


def test_g7_and_the_network_view_is_not_rendered(staged):
    out = run(staged, REPORT, script(ok(TENANT), ok(OGGI)), hash="#/rete")
    assert "Nuova agenzia" not in out["content"], out["content"][:300]
    assert "riservata all" not in out["content"], out["content"][:300]


def test_g8_a_platform_admin_can_still_open_the_network(staged):
    """Il verso opposto non deve chiudersi: chi ha diritto passa."""
    out = run(staged, REPORT, script(ok(SUPERADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/rete")
    assert out["hash"] == "#/rete", out["hash"]
    assert "/api/platform/me" in out["urls"], out["urls"]


def test_g9_a_platform_admin_inside_an_agency_can_still_open_the_network(staged):
    """Anche durante l'acting: e' da li' che si rientra."""
    out = run(staged, REPORT, script(ok(DENTRO), ok(PLATFORM_ME), ok(AGENZIE)),
              hash="#/rete")
    assert out["hash"] == "#/rete", out["hash"]
    assert "/api/platform/me" in out["urls"], out["urls"]
