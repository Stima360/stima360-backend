"""P27-7 - la sezione Rete, ESEGUITA.

Stessa disciplina di `tests/test_p26_4_shell_runtime.py`, e per la stessa
ragione: una UI si prova facendola girare. Cercare stringhe nei sorgenti dice
che una parola c'e', non che il bottone chiami la route giusta, che la lista si
rilegga dopo una scrittura, o che un 409 diventi una frase comprensibile invece
di un nome di indice.

Qui girano i moduli VERI - `main.js`, il router, le tre view della Rete - in
node, dentro uno stub di DOM, con `fetch` scriptato risposta per risposta. Le
asserzioni riguardano tre cose che solo l'esecuzione puo' mostrare:

1. QUALI CHIAMATE partono, in quale ordine, con quale corpo. E' cosi' che si
   prova che lo slug non viene mai rimandato in una PATCH, che `settings` non
   passa dalla PATCH generica, che la password non viene inviata per un'identita'
   esistente, e che dopo ogni mutazione si RILEGGE dal backend invece di
   aggiornare lo stato locale.

2. COSA RESTA SULLO SCHERMO. Un 403 deve produrre un avviso e nessun elenco; su
   una riga revocata il pulsante che la riattiverebbe non deve esistere.

3. COSA NON COMPARE MAI: nomi di vincoli, SQL, tracce interne. Il testo degli
   errori e' scritto in `components/network.js` e scelto per stato HTTP, quindi
   il `detail` del server non raggiunge lo schermo nemmeno quando c'e'.

Il DOM stub e' quello di P26-4 con due aggiunte necessarie a questa sezione:
`<dialog>` (showModal/close) e `insertAdjacentHTML`, che le view usano.
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
        "node non e' disponibile: le prove di runtime della Rete non sono state "
        "eseguite. Questo va riportato come BLOCKED, mai come PASS."
    ),
)

# Il DOM e il fetch scriptato di P26-4, riusati verbatim: sono gia' provati e
# duplicarli con differenze renderebbe incomparabili i due file.
from tests.test_p26_4_shell_runtime import DOM, FETCH  # noqa: E402

# Le due aggiunte che la Rete richiede. `dialog` non esisteva nello stub di
# P26-4 perche' la Shell di allora non ne apriva; qui i dialoghi sono il modo in
# cui si creano agenzie, territori e alias, e una conferma che non si puo'
# aprire e' una conferma che non si puo' provare.
EXTRA_DOM = r"""
El.prototype.showModal = function () { this._open = true; };
El.prototype.close = function () { this._open = false; };
El.prototype.insertAdjacentHTML = function (position, html) {
  const nodi = parseHTML(html);
  if (position === 'afterbegin') { this.children = [...nodi, ...this.children]; }
  else { for (const n of nodi) this.appendChild(n); }
  for (const n of nodi) n.parentNode = this;
};
// `options` e `selectedIndex` di una <select>: le view leggono l'etichetta
// scelta per scriverla nella conferma, e senza questi due la conferma non
// direbbe CHI diventa titolare.
Object.defineProperty(El.prototype, 'options', {
  get() { return this.querySelectorAll('option'); },
});
Object.defineProperty(El.prototype, 'selectedIndex', { get() { return 0; } });
globalThis.URLSearchParams = class {
  constructor(init = {}) { this._m = new Map(Object.entries(init)); }
  set(k, v) { this._m.set(k, String(v)); }
  toString() { return [...this._m].map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&'); }
};
globalThis.__dom.parseHTML = parseHTML;

// Un `value=` scritto nel markup e' il valore iniziale del campo. Lo stub di
// P26-4 teneva attributi e proprieta' separati - la Shell di allora non
// pre-riempiva nessun input - ma qui i form arrivano con dei valori dentro, e
// senza questa riga un test leggerebbe stringa vuota da un campo che l'utente
// vede compilato.
// Il valore di una <select> e' quello dell'opzione selezionata, o della prima:
// e' cosi' che si comporta un browser, ed e' cio' che le view danno per
// scontato quando inviano `select.value` senza che l'utente abbia toccato
// nulla. Senza, ogni form della Rete manderebbe stringa vuota.
Object.defineProperty(El.prototype, 'value', {
  get() {
    if (this._value !== undefined && this._value !== '') return this._value;
    if (this.tagName === 'SELECT') {
      const opzioni = this.querySelectorAll('option');
      const scelta = opzioni.find((o) => o.getAttribute('selected') !== null) || opzioni[0];
      return scelta ? (scelta.getAttribute('value') ?? '') : '';
    }
    return this._value === undefined ? '' : this._value;
  },
  set(v) { this._value = String(v ?? ''); },
});

const _setAttribute = El.prototype.setAttribute;
El.prototype.setAttribute = function (name, value) {
  _setAttribute.call(this, name, value);
  if (name === 'value') this.value = String(value);
  if (name === 'checked') this.checked = true;
};

// Il markup EFFETTIVO dell'intero sottoalbero. `innerHTML` restituisce solo
// l'ultimo template assegnato a quel nodo, quindi una tabella disegnata dentro
// un figlio non comparirebbe: i test che verificano l'ASSENZA di un pulsante
// leggerebbero una stringa in cui quel pulsante non c'e' mai stato, e
// passerebbero per il motivo sbagliato.
globalThis.__dom.deepHtml = (el) => {
  const pezzi = [el._html || ''];
  for (const figlio of el.children) pezzi.push(globalThis.__dom.deepHtml(figlio));
  return pezzi.join('');
};
"""


def _stage(tmp_path: Path) -> Path:
    staged = tmp_path / "assets"
    shutil.copytree(ASSETS, staged)
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return staged


def run(staged: Path, scenario: str, script: str, hash: str = "#/rete") -> dict:
    """Avvia la Shell GIA' sulla rotta indicata.

    Il boot e' l'unico momento in cui la Shell decide da sola cosa disegnare, e
    partire da `#/rete` evita che la vista "Oggi" consumi le risposte
    scriptate: cosi' la sequenza di chiamate che i test leggono e' esattamente
    quella della Rete, senza filtrarla.
    """
    driver = staged.parent / "driver-rete.mjs"
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
    return _stage(tmp_path_factory.mktemp("rete"))


# --- le sessioni ------------------------------------------------------------

PLATFORM_ADMIN = {
    "user_id": 1, "agency_id": None, "agency_name": None, "role": None,
    "is_platform_admin": True, "expires_at": "2030-01-01T00:00:00Z",
}
TENANT = {
    "user_id": 3, "agency_id": 7, "agency_name": "Agenzia A", "role": "agent",
    "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z",
}

PLATFORM_ME = {
    "user_id": 1, "is_platform_admin": True, "agency_id": None,
    "session_expires_at": "2030-01-01T00:00:00Z",
}
VIETATO = {"status": 403, "body": {"detail": "Superficie riservata all'amministrazione di piattaforma."}}

AGENZIE = [
    {"id": 1, "name": "STIMA360", "slug": "stima360", "status": "active",
     "settings": {}, "created_at": "2026-01-01T10:00:00Z", "updated_at": "2026-01-01T10:00:00Z"},
    {"id": 7, "name": "Agenzia Alba", "slug": "agenzia-alba", "status": "suspended",
     "settings": {}, "created_at": "2026-02-01T10:00:00Z", "updated_at": "2026-02-01T10:00:00Z"},
]

TERRITORI = [
    {"id": 30, "kind": "municipality", "canonical_key": "067001",
     "label": "Alba Adriatica", "created_at": "2026-01-01T10:00:00Z",
     "updated_at": "2026-01-01T10:00:00Z", "active_agency_id": 7,
     "active_assignment_id": 500},
    {"id": 31, "kind": "municipality", "canonical_key": "067025",
     "label": "Martinsicuro", "created_at": "2026-01-01T10:00:00Z",
     "updated_at": "2026-01-01T10:00:00Z", "active_agency_id": None,
     "active_assignment_id": None},
]

TERRITORIO_30 = {
    "id": 30, "kind": "municipality", "canonical_key": "067001",
    "label": "Alba Adriatica", "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-01-01T10:00:00Z",
    "active_assignment": {
        "id": 500, "territory_id": 30, "agency_id": 7, "status": "active",
        "created_at": "2026-02-01T10:00:00Z", "updated_at": "2026-02-01T10:00:00Z",
    },
}

ALIAS = [
    {"id": 900, "territory_id": 30, "source": "public_stima_comune",
     "match_value": "Alba Adriatica", "status": "active"},
    {"id": 901, "territory_id": 30, "source": "public_stima_comune",
     "match_value": "Alba Adr.", "status": "revoked"},
]

OPERATORE = {
    "id": 11, "email": "mario@example.test", "first_name": "Mario",
    "last_name": "Rossi", "status": "active", "is_platform_admin": False,
    "last_login_at": None, "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-01-01T10:00:00Z",
}
MEMBERSHIP = {
    "id": 21, "agency_id": 7, "operator_user_id": 11, "role": "agent",
    "status": "active", "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-01-01T10:00:00Z",
}
ORGANICO = [
    {"operator": OPERATORE, "membership": MEMBERSHIP},
    {
        "operator": {**OPERATORE, "id": 12, "email": "lucia@example.test",
                     "first_name": "Lucia", "last_name": "Bianchi"},
        "membership": {**MEMBERSHIP, "id": 22, "operator_user_id": 12,
                       "role": "agency_owner"},
    },
    {
        "operator": {**OPERATORE, "id": 13, "email": "vecchio@example.test",
                     "first_name": "Ex", "last_name": "Collaboratore"},
        "membership": {**MEMBERSHIP, "id": 23, "operator_user_id": 13,
                       "status": "revoked"},
    },
]

ASSEGNAZIONI = [
    {"id": 500, "territory_id": 30, "agency_id": 7, "status": "active",
     "territory_kind": "municipality", "territory_canonical_key": "067001",
     "territory_label": "Alba Adriatica",
     "created_at": "2026-02-01T10:00:00Z", "updated_at": "2026-02-01T10:00:00Z"},
    {"id": 501, "territory_id": 31, "agency_id": 7, "status": "revoked",
     "territory_kind": "municipality", "territory_canonical_key": "067025",
     "territory_label": "Martinsicuro",
     "created_at": "2026-01-05T10:00:00Z", "updated_at": "2026-03-01T10:00:00Z"},
]


def ok(body):
    return {"status": 200, "body": body}


def script(*voci):
    return json.dumps(list(voci))


REPORT = """
console.log(JSON.stringify({
  content: __dom.byId['content'].visibleText(),
  html: __dom.deepHtml(__dom.byId['content']),
  nav: __dom.byId['nav'].children.map((b) => b.textContent),
  navRoutes: __dom.byId['nav'].children.map((b) => b.dataset.route || ''),
  title: __dom.byId['page-title'].textContent,
  urls: __calls.map((c) => c.url),
  methods: __calls.map((c) => (c.options.method || 'GET')),
  bodies: __calls.map((c) => c.options.body || null),
}));
"""

def apri_rete(extra=""):
    return extra + REPORT


AGENZIA_7 = "#/rete/agenzie/7"
TERRITORIO = "#/rete/territori/30"


# ---------------------------------------------------------------------------
# A - ACCESSO
# ---------------------------------------------------------------------------

def test_a1_a_platform_admin_sees_the_network_entry(staged):
    out = run(staged, REPORT, script(ok(PLATFORM_ADMIN)))
    assert "rete" in out["navRoutes"], out["navRoutes"]
    assert "Rete" in out["nav"]


def test_a2_a_normal_tenant_has_no_network_entry(staged):
    """Un operatore di agenzia non vede la voce. Non nascosta: ASSENTE."""
    out = run(staged, REPORT, script(ok(TENANT)))
    assert "rete" not in out["navRoutes"], out["navRoutes"]
    assert "Rete" not in out["nav"]


def test_a3_an_anonymous_visitor_has_no_network_entry(staged):
    out = run(staged, REPORT, script({"status": 401, "body": {"detail": "no"}}))
    assert out["navRoutes"] == ["oggi", "contatti", "immobili", "acquirenti",
                                "abbinamenti", "attivita", "automazioni"]


def test_a4_the_entry_disappears_when_the_session_changes(staged):
    """Cambio di sessione: il bottone di un'altra persona non resta li'.

    E' la stessa lezione di P26-4 - nascondere non basta, si toglie - applicata
    alla voce che apre l'amministrazione della rete.
    """
    scenario = """
      __script([{ status: 204 }, { status: 200, body: %s }]);
      const { logout } = await import('%s');
      await logout();
      await __settle();
    """ % (json.dumps(TENANT), (staged / "core" / "auth.js").as_posix())
    out = run(staged, scenario + REPORT, script(ok(PLATFORM_ADMIN)))
    assert "rete" not in out["navRoutes"], out["navRoutes"]


def test_a5_a_tenant_reaching_the_route_by_hand_gets_a_refusal_and_no_data(staged):
    """La rotta si scrive a mano: il 403 del backend e' quello che la ferma.

    Nessun elenco viene richiesto dopo il rifiuto - la view si ferma alla
    prima chiamata - e sullo schermo compare una frase, non dei dati.
    """
    out = run(staged, apri_rete(), script(ok(TENANT), VIETATO))
    assert out["urls"] == ["/api/operator-auth/me", "/api/platform/me"]
    assert "riservata all" in out["content"]
    assert "Nuova agenzia" not in out["content"]


def test_a6_the_view_asks_platform_me_before_anything_else(staged):
    out = run(staged, apri_rete(), script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE)))
    assert out["urls"][1] == "/api/platform/me"
    assert out["urls"][2] == "/api/platform/agencies"


# ---------------------------------------------------------------------------
# B - AGENZIE
# ---------------------------------------------------------------------------

def test_b1_the_agency_list_shows_name_slug_and_status(staged):
    out = run(staged, apri_rete(), script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE)))
    assert "Agenzia Alba" in out["content"]
    assert "agenzia-alba" in out["content"]
    assert "Sospesa" in out["content"]
    assert "Attiva" in out["content"]


def test_b2_an_empty_network_says_so(staged):
    out = run(staged, apri_rete(), script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok([])))
    assert "Nessuna agenzia" in out["content"]


def test_b3_creating_an_agency_posts_name_slug_and_configuration(staged):
    scenario = """
      const host = __dom.byId['content'];
      host.querySelector('#agenzia-nuova').dispatch('click');
      await __settle();
      host.querySelector('#ag-name').value = 'Agenzia Nuova';
      host.querySelector('#ag-slug').value = 'agenzia-nuova';
      __script([{ status: 201, body: { id: 9, name: 'Agenzia Nuova', slug: 'agenzia-nuova', status: 'active', settings: {}, created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:00:00Z' } },
                { status: 200, body: %s }]);
      host.querySelector('#form-agenzia').dispatch('submit');
      await __settle();
    """ % json.dumps(AGENZIE)
    out = run(staged, apri_rete(scenario), script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE)))
    creazione = out["urls"].index("/api/platform/agencies", 3)
    assert out["methods"][creazione] == "POST"
    corpo = json.loads(out["bodies"][creazione])
    assert corpo["name"] == "Agenzia Nuova"
    assert corpo["slug"] == "agenzia-nuova"
    assert corpo["settings"] == {"timezone": "Europe/Rome", "locale": "it-IT"}
    # Dopo la scrittura si RILEGGE: la lista non viene ritoccata a mano.
    assert out["urls"][creazione + 1] == "/api/platform/agencies"
    assert out["methods"][creazione + 1] == "GET"


def test_b4_a_slug_conflict_becomes_a_sentence_and_not_a_constraint_name(staged):
    scenario = """
      const host = __dom.byId['content'];
      host.querySelector('#agenzia-nuova').dispatch('click');
      await __settle();
      host.querySelector('#ag-name').value = 'X';
      host.querySelector('#ag-slug').value = 'stima360';
      __script([{ status: 409, body: { detail: 'duplicate key value violates unique constraint "agencies_slug_unq"' } }]);
      host.querySelector('#form-agenzia').dispatch('submit');
      await __settle();
      console.log(JSON.stringify({ errore: host.querySelector('#ag-errore').textContent }));
    """
    out = run(staged, scenario, script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE)))
    testo = out["errore"]
    assert "slug" in testo
    assert "agencies_slug_unq" not in testo
    assert "constraint" not in testo.lower()
    assert "duplicate key" not in testo.lower()


def test_b5_the_detail_never_offers_a_slug_field(staged):
    """Lo slug si legge, non si modifica: non esiste un input per farlo."""
    out = run(staged, REPORT,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    assert "agenzia-alba" in out["content"]
    assert 'id="pa-slug"' not in out["html"]
    assert 'name="slug"' not in out["html"]


def test_b6_patching_an_agency_sends_only_what_changed_and_never_settings(staged):
    scenario = """
      const host = __dom.byId['content'];
      host.querySelector('#pa-name').value = 'Agenzia Rinominata';
      __script([{ status: 200, body: %s }, { status: 200, body: %s }]);
      host.querySelector('#form-panoramica').dispatch('submit');
      await __settle();
    """ % (json.dumps({**AGENZIE[1], "name": "Agenzia Rinominata"}),
           json.dumps({**AGENZIE[1], "name": "Agenzia Rinominata"}))
    out = run(
        staged,
        scenario + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7,
    )
    patch = out["methods"].index("PATCH")
    assert out["urls"][patch] == "/api/platform/agencies/7"
    corpo = json.loads(out["bodies"][patch])
    assert corpo == {"name": "Agenzia Rinominata"}, corpo
    assert "settings" not in corpo
    assert "slug" not in corpo


def test_b7_suspending_an_agency_asks_a_confirmation_that_explains_the_effect(staged):
    # L'agenzia di partenza e' ATTIVA: sospenderla e' davvero un cambiamento, e
    # una PATCH manda solo cio' che cambia.
    scenario = """
      const host = __dom.byId['content'];
      host.querySelector('#pa-status').value = 'suspended';
      host.querySelector('#form-panoramica').dispatch('submit');
      await __settle();
      const dialoghi = __dom.byId['content'].querySelectorAll('dialog');
      const testo = dialoghi.map((d) => d.visibleText()).join(' ');
      console.log(JSON.stringify({ testo, chiamate: __calls.map((c) => c.options.method || 'GET') }));
    """
    out = run(
        staged,
        scenario,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME),
               ok({**AGENZIE[1], "status": "active"})), AGENZIA_7,
    )
    assert "Sospendere" in out["testo"]
    # La conferma spiega COSA cambia, non "sei sicuro".
    assert "non potranno piu" in out["testo"]
    assert "Sei sicuro" not in out["testo"]
    # E finche' non si conferma, niente viene scritto.
    assert "PATCH" not in out["chiamate"]


# ---------------------------------------------------------------------------
# C - CONFIGURAZIONE
# ---------------------------------------------------------------------------

def test_c1_the_configuration_tab_uses_the_dedicated_endpoint(staged):
    scenario = """
      const host = __dom.byId['content'];
      __script([{ status: 200, body: { timezone: 'Europe/Rome', locale: 'it-IT' } }]);
      host.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'configurazione').dispatch('click');
      await __settle();
      host.querySelector('#cf-timezone').value = 'Europe/Berlin';
      __script([{ status: 200, body: { timezone: 'Europe/Berlin', locale: 'it-IT' } },
                { status: 200, body: { timezone: 'Europe/Berlin', locale: 'it-IT' } }]);
      host.querySelector('#form-config').dispatch('submit');
      await __settle();
    """
    out = run(
        staged,
        scenario + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7,
    )
    patch = out["methods"].index("PATCH")
    assert out["urls"][patch] == "/api/platform/agencies/7/configuration"
    assert json.loads(out["bodies"][patch]) == {"timezone": "Europe/Berlin"}
    # Rilettura dalla stessa rotta dedicata dopo la scrittura.
    assert out["urls"][patch + 1] == "/api/platform/agencies/7/configuration"
    assert out["methods"][patch + 1] == "GET"


def test_c2_a_broken_stored_configuration_shows_a_generic_message(staged):
    scenario = """
      const host = __dom.byId['content'];
      __script([{ status: 500, body: { detail: 'configurazione persistita non valida: settings->>timezone' } }]);
      host.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'configurazione').dispatch('click');
      await __settle();
    """
    out = run(
        staged,
        scenario + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7,
    )
    assert "Errore interno del server" in out["content"]
    assert "settings" not in out["content"]
    assert "timezone" not in out["content"].replace("Fuso orario", "")


# ---------------------------------------------------------------------------
# D - OPERATORI
# ---------------------------------------------------------------------------

def _apri_operatori(extra=""):
    return (
        "__script([{ status: 200, body: %s }]);" % json.dumps(ORGANICO)
        + "__dom.byId['content'].querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'operatori').dispatch('click');"
        + "await __settle();"
        + extra
    )


def test_d1_the_roster_shows_identity_role_and_both_statuses(staged):
    out = run(
        staged, _apri_operatori() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7,
    )
    assert "Mario Rossi" in out["content"]
    assert "mario@example.test" in out["content"]
    assert "Agente" in out["content"]
    assert "Titolare" in out["content"]


def test_d2_no_password_hash_ever_reaches_the_screen(staged):
    """Il backend non lo restituisce, e la UI non lo chiede da nessuna parte."""
    out = run(
        staged, _apri_operatori() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7,
    )
    assert "password_hash" not in out["html"]
    assert "pbkdf2" not in out["html"].lower()


def test_d3_a_revoked_membership_offers_no_way_back(staged):
    """Su una membership revocata il pulsante che la modificherebbe non esiste.

    Disegnarlo e poi spiegare un 409 insegna che i pulsanti mentono.
    """
    out = run(
        staged, _apri_operatori() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7,
    )
    assert 'data-azione="membership" data-operatore="13"' not in out["html"]
    assert 'data-azione="membership" data-operatore="11"' in out["html"]


def test_d4_creating_a_new_person_requires_a_password_and_sends_it_once(staged):
    scenario = _apri_operatori("""
      const host = __dom.byId['content'];
      host.querySelector('#op-nuovo').dispatch('click');
      await __settle();
      host.querySelector('#no-email').value = 'nuova@example.test';
      host.querySelector('#no-password').value = 'segreta-lunga-1';
      __script([{ status: 201, body: { operator: %s, membership: %s } },
                { status: 200, body: %s }]);
      host.querySelector('#dialogo-form').dispatch('submit');
      await __settle();
    """ % (json.dumps(OPERATORE), json.dumps(MEMBERSHIP), json.dumps(ORGANICO)))
    out = run(staged, scenario + REPORT,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    post = out["methods"].index("POST")
    assert out["urls"][post] == "/api/platform/agencies/7/operators"
    corpo = json.loads(out["bodies"][post])
    assert corpo["email"] == "nuova@example.test"
    assert corpo["password"] == "segreta-lunga-1"
    assert corpo["role"] == "agent"
    # E dopo si rilegge l'organico dal backend.
    assert out["urls"][post + 1] == "/api/platform/agencies/7/operators"


def test_d5_an_existing_identity_never_receives_a_password(staged):
    """L'email gia' nota: si crea la membership, non si tocca la credenziale.

    Inviare una password qui sarebbe un reimposta-password mascherato, e il
    backend risponde 409: la UI non ci prova nemmeno.
    """
    scenario = _apri_operatori("""
      const host = __dom.byId['content'];
      host.querySelector('#op-nuovo').dispatch('click');
      await __settle();
      host.querySelector('#no-email').value = 'mario@example.test';
      host.querySelector('#no-password').value = 'non-deve-partire';
      host.querySelector('#no-esistente').checked = true;
      __script([{ status: 201, body: { operator: %s, membership: %s } },
                { status: 200, body: %s }]);
      host.querySelector('#dialogo-form').dispatch('submit');
      await __settle();
    """ % (json.dumps(OPERATORE), json.dumps(MEMBERSHIP), json.dumps(ORGANICO)))
    out = run(staged, scenario + REPORT,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    post = out["methods"].index("POST")
    corpo = json.loads(out["bodies"][post])
    assert "password" not in corpo, corpo
    assert "non-deve-partire" not in (out["bodies"][post] or "")


def test_d6_a_membership_conflict_is_explained_in_words(staged):
    scenario = _apri_operatori("""
      const host = __dom.byId['content'];
      host.querySelector('#op-nuovo').dispatch('click');
      await __settle();
      host.querySelector('#no-email').value = 'altro@example.test';
      host.querySelector('#no-esistente').checked = true;
      __script([{ status: 409, body: { detail: 'uq_agency_memberships_single_active' } }]);
      host.querySelector('#dialogo-form').dispatch('submit');
      await __settle();
      console.log(JSON.stringify({ errore: host.querySelector('#dialogo-errore').textContent }));
    """)
    out = run(staged, scenario,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    assert "una sola membership attiva" in out["errore"]
    assert "uq_agency_memberships" not in out["errore"]


def test_d7_owner_transfer_only_offers_active_members_and_uses_put(staged):
    scenario = _apri_operatori("""
      const host = __dom.byId['content'];
      host.querySelector('#op-titolare').dispatch('click');
      await __settle();
      const opzioni = host.querySelector('#tt-operatore').querySelectorAll('option').map((o) => o.getAttribute('value'));
      host.querySelector('#dialogo-form').dispatch('submit');
      await __settle();
      const conferma = host.querySelectorAll('dialog').map((d) => d.visibleText()).join(' ');
      __script([{ status: 200, body: { membership: %s, demoted: null } },
                { status: 200, body: %s }]);
      host.querySelectorAll('[data-conferma="si"]').forEach((b) => b.dispatch('click'));
      await __settle();
      console.log(JSON.stringify({
        opzioni, conferma,
        urls: __calls.map((c) => c.url),
        methods: __calls.map((c) => c.options.method || 'GET'),
        bodies: __calls.map((c) => c.options.body || null),
      }));
    """ % (json.dumps(MEMBERSHIP), json.dumps(ORGANICO)))
    out = run(staged, scenario,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    # Solo membership attive e non gia' titolari: l'operatore 13 (revocato) e
    # il 12 (gia' titolare) non compaiono.
    assert out["opzioni"] == ["11"], out["opzioni"]
    assert "retrocesso" in out["conferma"]
    put = out["methods"].index("PUT")
    assert out["urls"][put] == "/api/platform/agencies/7/owner"
    assert json.loads(out["bodies"][put]) == {"operator_user_id": 11}


# ---------------------------------------------------------------------------
# E - TERRITORI
# ---------------------------------------------------------------------------

def _apri_territori_agenzia(extra=""):
    return (
        "__script([{ status: 200, body: %s }]);" % json.dumps(ASSEGNAZIONI)
        + "__dom.byId['content'].querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'territori').dispatch('click');"
        + "await __settle();"
        + extra
    )


def test_e1_the_agency_territories_show_kind_label_key_and_status(staged):
    out = run(staged, _apri_territori_agenzia() + REPORT,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    assert "Alba Adriatica" in out["content"]
    assert "067001" in out["content"]
    assert "Comune" in out["content"]
    assert "Revocata" in out["content"]


def test_e2_a_revoked_assignment_offers_neither_reactivate_nor_suspend(staged):
    """`revoked` e' TERMINALE: per ridare il territorio serve una nuova
    assegnazione, e nessun pulsante suggerisce il contrario."""
    out = run(staged, _apri_territori_agenzia() + REPORT,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    assert 'data-assegnazione="501"' not in out["html"]
    assert 'data-assegnazione="500" data-verso="suspended"' in out["html"]


def test_e3_suspending_an_assignment_patches_and_rereads(staged):
    scenario = _apri_territori_agenzia("""
      const host = __dom.byId['content'];
      __script([{ status: 200, body: %s }, { status: 200, body: %s }]);
      host.querySelectorAll('[data-verso="suspended"]')[0].dispatch('click');
      await __settle();
    """ % (json.dumps({**ASSEGNAZIONI[0], "status": "suspended"}), json.dumps(ASSEGNAZIONI)))
    out = run(staged, scenario + REPORT,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    patch = out["methods"].index("PATCH")
    assert out["urls"][patch] == "/api/platform/agencies/7/territories/500"
    assert json.loads(out["bodies"][patch]) == {"status": "suspended"}
    assert out["urls"][patch + 1] == "/api/platform/agencies/7/territories"


def test_e4_revoking_an_assignment_asks_first_and_says_it_is_final(staged):
    scenario = _apri_territori_agenzia("""
      const host = __dom.byId['content'];
      host.querySelectorAll('[data-verso="revoked"]')[0].dispatch('click');
      await __settle();
      console.log(JSON.stringify({
        conferma: host.querySelectorAll('dialog').map((d) => d.visibleText()).join(' '),
        methods: __calls.map((c) => c.options.method || 'GET'),
      }));
    """)
    out = run(staged, scenario,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    assert "definitiva" in out["conferma"]
    assert "agenzia predefinita" in out["conferma"]
    assert "PATCH" not in out["methods"]


def test_e5_assigning_a_territory_only_offers_unassigned_ones(staged):
    scenario = _apri_territori_agenzia("""
      const host = __dom.byId['content'];
      __script([{ status: 200, body: %s }]);
      host.querySelector('#te-assegna').dispatch('click');
      await __settle();
      const opzioni = host.querySelector('#as-territorio').querySelectorAll('option').map((o) => o.getAttribute('value'));
      __script([{ status: 201, body: %s }, { status: 200, body: %s }]);
      host.querySelector('#dialogo-form').dispatch('submit');
      await __settle();
      console.log(JSON.stringify({
        opzioni,
        urls: __calls.map((c) => c.url),
        methods: __calls.map((c) => c.options.method || 'GET'),
        bodies: __calls.map((c) => c.options.body || null),
      }));
    """ % (json.dumps(TERRITORI), json.dumps(ASSEGNAZIONI[0]), json.dumps(ASSEGNAZIONI)))
    out = run(staged, scenario,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    # Il 30 e' gia' presidiato: non viene offerto.
    assert out["opzioni"] == ["31"], out["opzioni"]
    post = out["methods"].index("POST")
    assert out["urls"][post] == "/api/platform/agencies/7/territories"
    assert json.loads(out["bodies"][post]) == {"territory_id": 31}


def test_e6_an_already_assigned_territory_conflict_is_explained(staged):
    scenario = _apri_territori_agenzia("""
      const host = __dom.byId['content'];
      __script([{ status: 200, body: %s }]);
      host.querySelector('#te-assegna').dispatch('click');
      await __settle();
      __script([{ status: 409, body: { detail: 'uq_agency_territory_single_active' } }]);
      host.querySelector('#dialogo-form').dispatch('submit');
      await __settle();
      console.log(JSON.stringify({ errore: host.querySelector('#dialogo-errore').textContent }));
    """ % json.dumps(TERRITORI))
    out = run(staged, scenario,
              script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE[1])), AGENZIA_7)
    assert "gia" in out["errore"] and "assegnazione attiva" in out["errore"]
    assert "uq_agency_territory" not in out["errore"]


def test_e7_the_catalogue_lists_territories_with_their_canonical_key(staged):
    out = run(
        staged,
        apri_rete("""
          const host = __dom.byId['content'];
          __script([{ status: 200, body: %s }]);
          host.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'territori').dispatch('click');
          await __settle();
        """ % json.dumps(TERRITORI)),
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(AGENZIE)),
    )
    assert "067001" in out["content"]
    assert "Chiave canonica" in out["content"]
    assert "Agenzia Alba" in out["content"]
    assert "Nessuna assegnazione attiva" in out["content"]
    # La paginazione e' quella del backend, non un taglio lato client.
    elenco = [u for u in out["urls"] if u.startswith("/api/platform/territories?")]
    assert elenco and "limit=50" in elenco[0] and "offset=0" in elenco[0]


# ---------------------------------------------------------------------------
# F - ALIAS
# ---------------------------------------------------------------------------

def _apri_territorio(extra=""):
    return extra


TERRITORIO_SCRIPT = None  # documentato sotto: la view fa /me, /territories/30, /agencies, /aliases


def test_f1_the_territory_page_lists_declared_values(staged):
    out = run(
        staged, _apri_territorio() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert "Alba Adriatica" in out["content"]
    assert "Comune ricevuto dal modulo Stima360" in out["content"]
    assert "Revocato" in out["content"]


def test_f2_the_page_says_the_key_is_the_identity_and_the_label_is_not(staged):
    out = run(
        staged, _apri_territorio() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert "067001" in out["content"]
    assert "identita" in out["content"]
    assert "non la usa per instradare" in out["content"]


def test_f3_declaring_an_alias_sends_the_backend_source_value_verbatim(staged):
    """L'etichetta e' italiana, il payload e' `public_stima_comune`.

    E il valore parte COM'E' STATO SCRITTO: nessuno slug, nessuna
    traslitterazione, nessun accento rimosso dal client.
    """
    scenario = _apri_territorio("""
      const host = __dom.byId['content'];
      host.querySelector('#alias-nuovo').dispatch('click');
      await __settle();
      host.querySelector('#al-valore').value = "Citta' Sant'Angelo";
      __script([{ status: 201, body: { id: 902, territory_id: 30, source: 'public_stima_comune', match_value: "Citta' Sant'Angelo", status: 'active' } },
                { status: 200, body: %s }]);
      host.querySelector('#form-alias').dispatch('submit');
      await __settle();
    """ % json.dumps(ALIAS))
    out = run(
        staged, scenario + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    post = out["methods"].index("POST")
    assert out["urls"][post] == "/api/platform/territories/30/aliases"
    corpo = json.loads(out["bodies"][post])
    assert corpo["source"] == "public_stima_comune"
    assert corpo["match_value"] == "Citta' Sant'Angelo"
    # Rilettura dal backend dopo la scrittura.
    assert out["urls"][post + 1] == "/api/platform/territories/30/aliases"


def test_f4_the_alias_value_is_never_derived_from_the_label(staged):
    """Il campo parte VUOTO: l'etichetta del territorio non lo precompila.

    Precompilarlo con "Alba Adriatica" sarebbe la deduzione che la migration
    059 esiste per eliminare - travestita da comodita'.
    """
    scenario = _apri_territorio("""
      const host = __dom.byId['content'];
      host.querySelector('#alias-nuovo').dispatch('click');
      await __settle();
      console.log(JSON.stringify({
        valore: host.querySelector('#al-valore').value,
        html: __dom.deepHtml(host),
      }));
    """)
    out = run(
        staged, scenario,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert out["valore"] == "", out["valore"]
    assert "alba-adriatica" not in out["html"].lower()


def test_f5_a_duplicate_alias_conflict_is_explained(staged):
    scenario = _apri_territorio("""
      const host = __dom.byId['content'];
      host.querySelector('#alias-nuovo').dispatch('click');
      await __settle();
      host.querySelector('#al-valore').value = 'Alba Adriatica';
      __script([{ status: 409, body: { detail: 'uq_territory_alias_active_value' } }]);
      host.querySelector('#form-alias').dispatch('submit');
      await __settle();
      console.log(JSON.stringify({ errore: host.querySelector('#al-errore').textContent }));
    """)
    out = run(
        staged, scenario,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert "un solo territorio" in out["errore"]
    assert "uq_territory_alias" not in out["errore"]


def test_f6_a_revoked_alias_has_no_action_at_all(staged):
    out = run(
        staged, _apri_territorio() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert 'data-alias="901"' not in out["html"]
    assert 'data-alias="900"' in out["html"]
    assert "Definitivo" in out["content"]


def test_f7_revoking_an_alias_confirms_then_patches_status_only(staged):
    scenario = _apri_territorio("""
      const host = __dom.byId['content'];
      host.querySelectorAll('[data-alias="900"]')[0].dispatch('click');
      await __settle();
      const conferma = host.querySelectorAll('dialog').map((d) => d.visibleText()).join(' ');
      __script([{ status: 200, body: { id: 900, territory_id: 30, source: 'public_stima_comune', match_value: 'Alba Adriatica', status: 'revoked' } },
                { status: 200, body: %s }]);
      host.querySelectorAll('[data-conferma="si"]').forEach((b) => b.dispatch('click'));
      await __settle();
      console.log(JSON.stringify({
        conferma,
        urls: __calls.map((c) => c.url),
        methods: __calls.map((c) => c.options.method || 'GET'),
        bodies: __calls.map((c) => c.options.body || null),
      }));
    """ % json.dumps(ALIAS))
    out = run(
        staged, scenario,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert "definitiva" in out["conferma"]
    assert "agenzia predefinita" in out["conferma"]
    patch = out["methods"].index("PATCH")
    assert out["urls"][patch] == "/api/platform/aliases/900"
    assert json.loads(out["bodies"][patch]) == {"status": "revoked"}
    # Nessuna DELETE, in nessun punto del percorso.
    assert "DELETE" not in out["methods"]


# ---------------------------------------------------------------------------
# G - CATENA DI INSTRADAMENTO (sola lettura)
# ---------------------------------------------------------------------------

def test_g1_the_chain_reads_the_four_links_from_backend_data(staged):
    out = run(
        staged, _apri_territorio() + REPORT,
        script(ok(PLATFORM_ADMIN), ok(PLATFORM_ME), ok(TERRITORIO_30), ok(AGENZIE), ok(ALIAS)),
        TERRITORIO,
    )
    assert "Catena di instradamento" in out["content"]
    # L'agenzia 7 e' SOSPESA nei dati: la catena lo dice, e non si dichiara
    # completa.
    assert "Instradamento incompleto" in out["content"]
    assert "non attiva" in out["content"]
    assert "non e" in out["content"] or "Assegnazione attiva" in out["content"]


def test_g2_no_routing_algorithm_is_reimplemented_in_the_browser(staged):
    """La catena e' una lettura: nessuna normalizzazione, nessuna query.

    Se un giorno qualcuno ricostruisse qui il confronto del routing, la sua
    versione divergerebbe da quella SQL senza che nessun test lo dica - a meno
    di questo.
    """
    for nome in ("views/rete.js", "views/rete-agenzia.js",
                 "views/rete-territorio.js", "components/network.js"):
        testo = (ASSETS / nome).read_text(encoding="utf-8")
        codice = "\n".join(
            r for r in testo.split("\n")
            if not r.strip().startswith("//") and not r.strip().startswith("*")
        )
        for vietato in ("toLowerCase()", "normalize(", "replace(/\\s+/", "slugif"):
            assert vietato not in codice, (nome, vietato)


def test_g3_no_delete_request_exists_anywhere_in_the_network_section(staged):
    for nome in ("views/rete.js", "views/rete-agenzia.js",
                 "views/rete-territorio.js", "components/network.js"):
        testo = (ASSETS / nome).read_text(encoding="utf-8")
        assert "apiDelete" not in testo, nome
