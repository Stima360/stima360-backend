"""P27-7 - E2E della sequenza minima, con uno stato che sopravvive al ricarico.

LA SEQUENZA, PER INTERO E IN UN SOLO PERCORSO:

    accedi come amministratore di piattaforma
      -> apri Rete
      -> crea un'agenzia
      -> configura il fuso orario
      -> aggiungi un operatore
      -> assegna un territorio
      -> dichiara un alias
      -> RICARICA la pagina
      -> i dati sono ancora li'

PERCHE' NON SU RENDER, E PERCHE' QUESTO E' COMUNQUE UN E2E

Il brief chiede un E2E "se l'infrastruttura esistente lo permette", e non
permette: non c'e' un PostgreSQL locale con lo schema dell'applicazione (meta'
delle tabelle storiche nasce fuori dalle migration, vedi
`tests/test_p27_6_postgres_real.py`), e il database TEST remoto non si tocca -
non da un test, non con dati di prova.

Quel che questo file fa e' comunque un percorso completo e non una somma di
pezzi: gira il codice VERO della Shell - `main.js`, il router, le tre view -
contro un backend in memoria che si comporta come quello vero su UN punto
decisivo, ed e' il punto che l'E2E deve provare: **lo stato sta dalla parte del
server**. La UI non tiene niente per se', e il "ricarico" e' letteralmente un
secondo boot della Shell, con il DOM buttato via e ricostruito da zero.

Se una qualunque view mostrasse dati propri invece di rileggerli - il difetto
classico di un frontend che "aggiorna lo stato locale" dopo una scrittura -
dopo il ricarico quei dati non ci sarebbero, e questo file fallirebbe.

COSA QUESTO E2E NON PROVA, dichiarato invece che lasciato credere: che il
backend vero risponda come il finto. Quello lo provano le batterie di
P27-1..P27-6 sulle loro API, e la certificazione su Render.

NESSUN DATO DI PROVA VIENE SCRITTO DA NESSUNA PARTE: non c'e' una connessione,
non c'e' una fixture da ripulire, e il "database" e' un oggetto JavaScript che
muore con il processo node.
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
        "node non e' disponibile: l'E2E della Rete non e' stato eseguito. "
        "Questo va riportato come BLOCKED, mai come PASS."
    ),
)

from tests.test_p26_4_shell_runtime import DOM  # noqa: E402
from tests.test_p27_7_network_runtime import EXTRA_DOM  # noqa: E402

# Un backend in memoria: poche tabelle e le sole regole che questa sequenza
# tocca. Non imita il backend vero nei dettagli - non e' il suo compito - ma
# TIENE LO STATO, che e' l'unica cosa che un E2E deve poter verificare dopo un
# ricarico.
BACKEND = r"""
const db = {
  agencies: [{ id: 1, name: 'STIMA360', slug: 'stima360', status: 'active',
               settings: {}, created_at: '2026-01-01T10:00:00Z',
               updated_at: '2026-01-01T10:00:00Z' }],
  configurations: { 1: { timezone: 'Europe/Rome', locale: 'it-IT' } },
  operators: [],
  memberships: [],
  territories: [{ id: 30, kind: 'municipality', canonical_key: '067001',
                  label: 'Alba Adriatica', created_at: '2026-01-01T10:00:00Z',
                  updated_at: '2026-01-01T10:00:00Z' }],
  assignments: [],
  aliases: [],
  seq: 100,
};
globalThis.__db = db;

const risposta = (status, body) => ({
  status,
  ok: status >= 200 && status < 300,
  async json() { return body; },
});

function assegnazioneAttiva(territoryId) {
  return db.assignments.find((a) => a.territory_id === territoryId && a.status === 'active') || null;
}

globalThis.fetch = async (url, options = {}) => {
  const metodo = (options.method || 'GET').toUpperCase();
  const corpo = options.body ? JSON.parse(options.body) : null;
  const percorso = url.split('?')[0];
  globalThis.__calls.push({ url, options });

  if (percorso === '/api/operator-auth/me') {
    return risposta(200, { user_id: 1, agency_id: null, agency_name: null,
                           role: null, is_platform_admin: true,
                           expires_at: '2030-01-01T00:00:00Z' });
  }
  if (percorso === '/api/platform/me') {
    return risposta(200, { user_id: 1, is_platform_admin: true, agency_id: null,
                           session_expires_at: '2030-01-01T00:00:00Z' });
  }

  if (percorso === '/api/platform/agencies' && metodo === 'GET') {
    return risposta(200, db.agencies);
  }
  if (percorso === '/api/platform/agencies' && metodo === 'POST') {
    if (db.agencies.some((a) => a.slug === corpo.slug)) {
      return risposta(409, { detail: 'slug gia\' assegnato' });
    }
    const riga = {
      id: (db.seq += 1), name: corpo.name, slug: corpo.slug,
      status: corpo.status || 'active', settings: {},
      created_at: '2026-05-01T10:00:00Z', updated_at: '2026-05-01T10:00:00Z',
    };
    db.agencies.push(riga);
    db.configurations[riga.id] = {
      timezone: (corpo.settings && corpo.settings.timezone) || 'Europe/Rome',
      locale: (corpo.settings && corpo.settings.locale) || 'it-IT',
    };
    return risposta(201, riga);
  }

  let m = percorso.match(/^\/api\/platform\/agencies\/(\d+)$/);
  if (m) {
    const agenzia = db.agencies.find((a) => a.id === Number(m[1]));
    if (!agenzia) return risposta(404, { detail: 'non trovata' });
    if (metodo === 'PATCH') Object.assign(agenzia, corpo);
    return risposta(200, agenzia);
  }

  m = percorso.match(/^\/api\/platform\/agencies\/(\d+)\/configuration$/);
  if (m) {
    const id = Number(m[1]);
    if (metodo === 'PATCH') Object.assign(db.configurations[id], corpo);
    return risposta(200, db.configurations[id]);
  }

  m = percorso.match(/^\/api\/platform\/agencies\/(\d+)\/operators$/);
  if (m) {
    const agencyId = Number(m[1]);
    if (metodo === 'POST') {
      let operatore = db.operators.find((o) => o.email === corpo.email);
      if (!operatore) {
        if (!corpo.password) return risposta(422, { detail: 'password richiesta' });
        operatore = {
          id: (db.seq += 1), email: corpo.email,
          first_name: corpo.first_name || null, last_name: corpo.last_name || null,
          status: 'active', is_platform_admin: false, last_login_at: null,
          created_at: '2026-05-01T10:00:00Z', updated_at: '2026-05-01T10:00:00Z',
        };
        db.operators.push(operatore);
      } else if (corpo.password) {
        return risposta(409, { detail: 'credenziale esistente' });
      }
      const membership = {
        id: (db.seq += 1), agency_id: agencyId, operator_user_id: operatore.id,
        role: corpo.role, status: 'active',
        created_at: '2026-05-01T10:00:00Z', updated_at: '2026-05-01T10:00:00Z',
      };
      db.memberships.push(membership);
      return risposta(201, { operator: operatore, membership });
    }
    const righe = db.memberships
      .filter((mb) => mb.agency_id === agencyId)
      .map((mb) => ({
        operator: db.operators.find((o) => o.id === mb.operator_user_id),
        membership: mb,
      }));
    return risposta(200, righe);
  }

  m = percorso.match(/^\/api\/platform\/agencies\/(\d+)\/territories$/);
  if (m) {
    const agencyId = Number(m[1]);
    if (metodo === 'POST') {
      if (assegnazioneAttiva(corpo.territory_id)) {
        return risposta(409, { detail: 'gia\' assegnato' });
      }
      const riga = {
        id: (db.seq += 1), territory_id: corpo.territory_id, agency_id: agencyId,
        status: 'active', created_at: '2026-05-01T10:00:00Z',
        updated_at: '2026-05-01T10:00:00Z',
      };
      db.assignments.push(riga);
      return risposta(201, riga);
    }
    const righe = db.assignments
      .filter((a) => a.agency_id === agencyId)
      .map((a) => {
        const t = db.territories.find((x) => x.id === a.territory_id);
        return { ...a, territory_kind: t.kind, territory_canonical_key: t.canonical_key,
                 territory_label: t.label };
      });
    return risposta(200, righe);
  }

  if (percorso === '/api/platform/territories' && metodo === 'GET') {
    return risposta(200, db.territories.map((t) => {
      const a = assegnazioneAttiva(t.id);
      return { ...t, active_agency_id: a ? a.agency_id : null,
               active_assignment_id: a ? a.id : null };
    }));
  }

  m = percorso.match(/^\/api\/platform\/territories\/(\d+)$/);
  if (m) {
    const t = db.territories.find((x) => x.id === Number(m[1]));
    if (!t) return risposta(404, { detail: 'non trovato' });
    return risposta(200, { ...t, active_assignment: assegnazioneAttiva(t.id) });
  }

  m = percorso.match(/^\/api\/platform\/territories\/(\d+)\/aliases$/);
  if (m) {
    const territoryId = Number(m[1]);
    if (metodo === 'POST') {
      // L'unicita' vera della 059: sul valore NORMALIZZATO (maiuscole e spazi)
      // e solo fra gli alias attivi.
      const chiave = corpo.match_value.trim().replace(/\s+/g, ' ').toLowerCase();
      const scontro = db.aliases.some(
        (a) => a.status === 'active' && a.source === corpo.source
          && a.match_value.trim().replace(/\s+/g, ' ').toLowerCase() === chiave,
      );
      if (scontro) return risposta(409, { detail: 'alias duplicato' });
      const riga = {
        id: (db.seq += 1), territory_id: territoryId, source: corpo.source,
        match_value: corpo.match_value, status: 'active',
      };
      db.aliases.push(riga);
      return risposta(201, riga);
    }
    return risposta(200, db.aliases.filter((a) => a.territory_id === territoryId));
  }

  return risposta(404, { detail: 'rotta non prevista dal backend di prova' });
};

globalThis.__calls = [];
globalThis.__settle = async (rounds = 14) => {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
};
"""


def _stage(tmp_path: Path) -> Path:
    staged = tmp_path / "assets"
    shutil.copytree(ASSETS, staged)
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return staged


@pytest.fixture(scope="module")
def staged(tmp_path_factory) -> Path:
    return _stage(tmp_path_factory.mktemp("rete-e2e"))


# La sequenza. Ogni passo e' un clic vero su un elemento vero, e fra un passo e
# l'altro non viene mai toccato `__db` da fuori: tutto quel che finisce li'
# dentro ci arriva perche' la UI ha fatto una richiesta.
SEQUENZA = r"""
const contenuto = __dom.byId['content'];
const passi = [];

// 1. La Rete e' raggiungibile e la voce di menu esiste.
passi.push({ passo: 'menu', rete: __dom.byId['nav'].children.some((b) => b.dataset.route === 'rete') });

// 2. Crea l'agenzia.
contenuto.querySelector('#agenzia-nuova').dispatch('click');
await __settle();
contenuto.querySelector('#ag-name').value = 'Agenzia Adriatica';
contenuto.querySelector('#ag-slug').value = 'agenzia-adriatica';
contenuto.querySelector('#ag-timezone').value = 'Europe/Rome';
contenuto.querySelector('#form-agenzia').dispatch('submit');
await __settle();
const nuova = __db.agencies.find((a) => a.slug === 'agenzia-adriatica');
passi.push({ passo: 'agenzia', id: nuova ? nuova.id : null, nome: nuova ? nuova.name : null });

// La creazione porta sulla scheda: da li' in poi si lavora sull'agenzia nuova.
window.location.hash = '#/rete/agenzie/' + nuova.id;
await window._fire('hashchange');
await __settle();

// 3. Configura il fuso orario.
contenuto.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'configurazione').dispatch('click');
await __settle();
contenuto.querySelector('#cf-timezone').value = 'Europe/Berlin';
contenuto.querySelector('#form-config').dispatch('submit');
await __settle();
passi.push({ passo: 'configurazione', timezone: __db.configurations[nuova.id].timezone });

// 4. Aggiungi un operatore nuovo (email mai vista: serve la password).
contenuto.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'operatori').dispatch('click');
await __settle();
contenuto.querySelector('#op-nuovo').dispatch('click');
await __settle();
contenuto.querySelector('#no-email').value = 'agente@example.test';
contenuto.querySelector('#no-nome').value = 'Anna';
contenuto.querySelector('#no-cognome').value = 'Verdi';
contenuto.querySelector('#no-password').value = 'una-password-lunga';
contenuto.querySelector('#dialogo-form').dispatch('submit');
await __settle();
passi.push({ passo: 'operatore', operatori: __db.operators.length,
             membership: __db.memberships.length,
             email: (__db.operators[0] || {}).email });

// 5. Assegna il territorio.
contenuto.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'territori').dispatch('click');
await __settle();
contenuto.querySelector('#te-assegna').dispatch('click');
await __settle();
contenuto.querySelector('#dialogo-form').dispatch('submit');
await __settle();
passi.push({ passo: 'assegnazione', assegnazioni: __db.assignments.length,
             agenzia: (__db.assignments[0] || {}).agency_id,
             stato: (__db.assignments[0] || {}).status });

// 6. Dichiara l'alias sul territorio.
window.location.hash = '#/rete/territori/30';
await window._fire('hashchange');
await __settle();
contenuto.querySelector('#alias-nuovo').dispatch('click');
await __settle();
contenuto.querySelector('#al-valore').value = 'Alba Adriatica';
contenuto.querySelector('#form-alias').dispatch('submit');
await __settle();
passi.push({ passo: 'alias', alias: __db.aliases.length,
             valore: (__db.aliases[0] || {}).match_value,
             source: (__db.aliases[0] || {}).source });

// 7. IL RICARICO. Il DOM viene svuotato e la Shell riparte da zero: tutto cio'
//    che compare dopo questa riga e' stato riletto dal server.
contenuto.innerHTML = '';
contenuto.children = [];
window.location.hash = '#/rete/territori/30';
await window._fire('hashchange');
await __settle();
passi.push({ passo: 'ricarico-territorio', testo: contenuto.visibleText() });

window.location.hash = '#/rete/agenzie/' + nuova.id;
await window._fire('hashchange');
await __settle();
const panoramica = contenuto.visibleText();
contenuto.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'operatori').dispatch('click');
await __settle();
const organico = contenuto.visibleText();
contenuto.querySelectorAll('[data-tab]').find((b) => b.dataset.tab === 'configurazione').dispatch('click');
await __settle();
// Il fuso configurato e' il VALORE del campo, non testo della pagina: si
// legge da li', come lo leggerebbe chi apre la scheda.
const configurazione = contenuto.querySelector('#cf-timezone').value;

console.log(JSON.stringify({
  passi, panoramica, organico, configurazione,
  metodi: __calls.map((c) => (c.options.method || 'GET')),
  urls: __calls.map((c) => c.url),
}));
"""


@pytest.fixture(scope="module")
def esito(staged) -> dict:
    driver = staged.parent / "driver-e2e.mjs"
    driver.write_text(
        DOM
        + BACKEND
        + EXTRA_DOM
        + "\nwindow.location.hash = '#/rete';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle();\n"
        + SEQUENZA
        + "\n",
        encoding="utf-8",
    )
    risultato = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=90,
        cwd=staged.parent,
    )
    if risultato.returncode != 0:
        raise AssertionError(
            f"la sequenza E2E e' fallita ({risultato.returncode}):\n"
            f"{risultato.stderr[-3000:]}\n--- stdout ---\n{risultato.stdout[-2000:]}"
        )
    return json.loads(risultato.stdout.strip().splitlines()[-1])


def _passo(esito, nome):
    return next(p for p in esito["passi"] if p["passo"] == nome)


def test_1_the_network_section_is_reachable(esito):
    assert _passo(esito, "menu")["rete"] is True


def test_2_the_agency_is_created_through_the_ui(esito):
    passo = _passo(esito, "agenzia")
    assert passo["id"], esito["passi"]
    assert passo["nome"] == "Agenzia Adriatica"


def test_3_the_timezone_is_configured_on_the_new_agency(esito):
    assert _passo(esito, "configurazione")["timezone"] == "Europe/Berlin"


def test_4_the_operator_is_added_with_a_membership(esito):
    passo = _passo(esito, "operatore")
    assert passo["operatori"] == 1
    assert passo["membership"] == 1
    assert passo["email"] == "agente@example.test"


def test_5_the_territory_is_assigned_to_the_new_agency(esito):
    passo = _passo(esito, "assegnazione")
    assert passo["assegnazioni"] == 1
    assert passo["stato"] == "active"
    assert passo["agenzia"] == _passo(esito, "agenzia")["id"]


def test_6_the_alias_is_declared_with_the_backend_source(esito):
    passo = _passo(esito, "alias")
    assert passo["alias"] == 1
    assert passo["valore"] == "Alba Adriatica"
    assert passo["source"] == "public_stima_comune"


def test_7_everything_survives_a_reload(esito):
    """IL PUNTO DELL'E2E: dopo il ricarico i dati ci sono ancora.

    Il DOM e' stato svuotato e le view sono ripartite da zero. Se una di loro
    avesse mostrato dati propri - "aggiornati" a mano dopo la scrittura invece
    che riletti - qui non ci sarebbe niente.
    """
    territorio = _passo(esito, "ricarico-territorio")["testo"]
    assert "Alba Adriatica" in territorio
    assert "Comune ricevuto dal modulo Stima360" in territorio
    assert "Agenzia Adriatica" in territorio

    assert "Agenzia Adriatica" in esito["panoramica"]
    assert "agenzia-adriatica" in esito["panoramica"]
    assert "Anna Verdi" in esito["organico"]
    assert "agente@example.test" in esito["organico"]
    assert esito["configurazione"] == "Europe/Berlin"


def test_8_the_routing_chain_is_complete_after_the_sequence(esito):
    """E la catena di instradamento, ricostruita dai dati veri, e' completa.

    Alias attivo -> territorio Comune -> assegnazione attiva -> agenzia attiva.
    Nessuno dei quattro anelli e' calcolato qui: sono i campi che il server ha
    restituito dopo il ricarico.
    """
    assert "Instradamento completo" in _passo(esito, "ricarico-territorio")["testo"]


def test_9_no_delete_was_ever_issued(esito):
    assert "DELETE" not in esito["metodi"]


def test_10_every_mutation_was_followed_by_a_reread(esito):
    """Dopo ogni scrittura la UI rilegge: non finge il successo.

    Si guarda la chiamata SUBITO DOPO ciascuna mutazione: deve essere una GET.
    """
    for indice, metodo in enumerate(esito["metodi"]):
        if metodo in {"POST", "PATCH", "PUT"}:
            seguenti = esito["metodi"][indice + 1:]
            assert seguenti and "GET" in seguenti[:3], (
                indice, esito["urls"][indice], seguenti[:3]
            )
