"""Link prenotazione dell'Agenda (UI delle API operatore A30-12), ESEGUITA.

`main.js` VERO, router, sessione, pagina Agenda e il pannello "Link
prenotazione" di `components/agenda/agenda-dialogs.js`, dentro lo stub di DOM
di P26-4/P27-7 con il driver di A30-13B.1 (riusato, non copiato).

Cosa si prova:

* elenco con agente, tipo, durata, buffer, stato; nessun id interno; un link
  disattivato non offre azioni (l'API non lo riattiva e ruotarlo non serve);
* owner/admin/Supreme scelgono l'agente, un agent crea solo per se' (nessun
  selettore); l'agente di un link esistente non si modifica;
* crea, modifica (solo i campi cambiati), ruota e disattiva - questi due solo
  dopo conferma - e l'elenco si rilegge dopo ogni scrittura;
* il TOKEN GREZZO: compare solo dopo create/rotate, in un riquadro con
  l'avviso "mostrato solo ora"; si copia; non finisce MAI in localStorage,
  sessionStorage, console, attributi o `data-*`; chiudere il riquadro o il
  pannello lo toglie dal DOM;
* errori leggibili, stato vuoto, validazioni che non fanno partire nulla.

Il token dei test e' una costante finta, non un segreto. Il permesso VERO e'
del server: `tests/test_agenda_booking_links_postgres.py`.

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
# Fixture riusata: la copia degli asset in una cartella temporanea.
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove Link prenotazione NON eseguite (BLOCKED)")

B = "/api/appointments/booking-links"
ORIGINE = "https://crm.stima360.test"
TOKEN = "TOKEN-FINTO-creato-0123456789abcdef"
TOKEN_RUOTATO = "TOKEN-FINTO-ruotato-fedcba9876543210"
URL = f"{ORIGINE}/api/public/booking/{TOKEN}"
URL_RUOTATO = f"{ORIGINE}/api/public/booking/{TOKEN_RUOTATO}"


def _link(id_, agente, stato="active", **extra):
    base = {"id": id_, "agency_id": 7, "assigned_user_id": agente, "label": None, "status": stato,
            "appointment_type": "inspection", "duration_minutes": 60,
            "buffer_before_minutes": 15, "buffer_after_minutes": 30,
            "created_at": "2026-09-20T10:00:00+02:00", "updated_at": "2026-09-20T10:00:00+02:00",
            "expires_at": None, "revoked_at": "2026-09-25T10:00:00+02:00" if stato == "disabled" else None}
    base.update(extra)
    return base


ELENCO = {"items": [_link(501, 3, label="Sito web"), _link(502, 4, stato="disabled")]}
VUOTO = {"items": []}


def _rotte(sessione="agency_owner", *, elenco=(ELENCO,), crea=None, ruota=None, patch=None):
    rt = a30_5._rt()
    crea = crea or ({"status": 201, "body": {**_link(503, 3), "token": TOKEN}},)
    ruota = ruota or (rt.ok({**_link(501, 3), "token": TOKEN_RUOTATO}),)
    patch = patch or (rt.ok(_link(501, 3)),)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", B, [rt.ok(e) for e in elenco]),
        # le rotte piu' specifiche PRIMA di POST /booking-links
        ("POST", f"{B}/501/rotate", list(ruota)),
        ("POST", f"{B}/501/disable", [rt.ok(_link(501, 3, stato="disabled"))]),
        ("PATCH", f"{B}/501", list(patch)),
        ("POST", B, list(crea)),
        ("GET", "/api/appointments/agents", [rt.ok(a30_13b.AGENTI)]),
        ("GET", "/api/appointments/calendar?", [rt.ok(VUOTO)]),
        ("GET", "/api/appointments?", [rt.ok(VUOTO)]),
    ]
    righe = [f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci]
    # Spie installate PRIMA che l'app parta: storage, console, appunti.
    righe.append(r"""
window.location.origin = %s;
globalThis.__persistito = [];
const __storage = (nome) => ({
  setItem: (k, v) => __persistito.push(`${nome}:${k}=${v}`), getItem: () => null,
  removeItem: () => {}, clear: () => {}, key: () => null, length: 0 });
Object.defineProperty(globalThis, 'localStorage', { value: __storage('local'), configurable: true });
Object.defineProperty(globalThis, 'sessionStorage', { value: __storage('session'), configurable: true });
window.localStorage = globalThis.localStorage;
window.sessionStorage = globalThis.sessionStorage;
globalThis.__log = [];
for (const livello of ['info', 'warn', 'error', 'debug', 'trace']) {
  console[livello] = (...a) => __log.push(a.map(String).join(' '));
}
const __logOriginale = console.log;
globalThis.__stampa = (...a) => __logOriginale(...a);
console.log = (...a) => __log.push(a.map(String).join(' '));
globalThis.__appunti = [];
globalThis.__appuntiRotti = false;
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
  clipboard: { writeText: async (t) => { if (__appuntiRotti) throw new Error('negato'); __appunti.push(t); } },
} });
""" % json.dumps(ORIGINE))
    return "\n".join(righe)


LINK = r"""
// Il `report` del driver A30-5 usa console.log: qui console.log e' spiato, quindi
// l'esito si stampa con la funzione originale.
function esito(extra = {}) {
  __stampa(JSON.stringify({
    calls: chiamate(), open: !!(dlg() && dlg()._open),
    error: dlg() && f('[data-error]') ? f('[data-error]').textContent : null, ...extra,
  }));
}
async function apriLink() { bottone(C(), 'Link prenotazione').dispatch('click'); await wait(); }
const pan = () => dlg();
const righe = () => pan().querySelectorAll('[data-row]');
const modulo = () => pan().querySelector('[data-form]');
async function click(el) { el.dispatch('click'); await wait(); }
async function siConferma() { await click(pan().querySelector('[data-confirm-yes]')); }
function statoLink() {
  const once = pan().querySelector('[data-once]');
  const campo = once.querySelector('input');
  return {
    righe: righe().map((r) => ({
      testo: r.visibleText(),
      azioni: r.querySelectorAll('button').map((b) => b.textContent),
    })),
    avviso: pan().querySelector('[data-notice]').visibleText(),
    errore: f('[data-error]').textContent,
    unaVolta: once.visibleText(),
    campoUrl: campo ? campo.value : null,
    campoEtichetta: campo ? campo.getAttribute('aria-label') : null,
    notaEndpoint: once.querySelector('[data-endpoint-note]')
      ? once.querySelector('[data-endpoint-note]').textContent : null,
    vuoto: pan().querySelectorAll('[data-empty]').length,
    modulo: modulo().visibleText(),
    selAgente: modulo().querySelector('[data-field="agent"]')
      ? modulo().querySelector('[data-field="agent"]').querySelectorAll('option').map((o) => o.textContent)
      : null,
    testo: pan().visibleText(),
  };
}
// Il token e' ovunque nel DOM? testo, valori, attributi, data-*.
function tokenNelDom(t) {
  const trovati = [];
  const visita = (n) => {
    if (!n || typeof n !== 'object') return;
    if (typeof n.textContent === 'string' && n.children && !n.children.length && n.textContent.includes(t)) trovati.push('testo');
    if (n._value !== undefined && String(n._value).includes(t)) trovati.push('valore');
    for (const v of Object.values(n.attributes || {})) if (String(v).includes(t)) trovati.push('attributo');
    for (const v of Object.values(n.dataset || {})) if (String(v).includes(t)) trovati.push('data-*');
    for (const c of n.children || []) visita(c);
  };
  visita(__dom.main);
  return trovati;
}
function perdite(t) {
  return { storage: __persistito.filter((s) => s.includes(t)).length,
           log: __log.filter((s) => s.includes(t)).length };
}
async function compila({ tipo, durata, prima, dopo, etichetta } = {}) {
  const m = pan().querySelector('[data-form]');
  const imposta = (campo, v, ev = 'input') => { const e = m.querySelector(`[data-field="${campo}"]`); e.value = v; e.dispatch(ev); };
  if (tipo !== undefined) imposta('type', tipo, 'change');
  if (durata !== undefined) imposta('duration', durata);
  if (prima !== undefined) imposta('buffer-before', prima);
  if (dopo !== undefined) imposta('buffer-after', dopo);
  if (etichetta !== undefined) imposta('label', etichetta);
  await wait();
}
async function salva() { await click(modulo().querySelector('[data-save]')); }
"""


def run(staged, scenario, rotte):  # noqa: F811
    return a30_13b.run(staged, LINK + scenario, rotte, hash="#/agenda/settimana/2026-09-28")


def _scritture(out):
    return [(c["m"], c["url"], c["body"]) for c in out["calls"] if c["m"] in ("POST", "PATCH")]


def _letture_elenco(out):
    return [c for c in out["calls"] if c["m"] == "GET" and c["url"] == B]


# ---------------------------------------------------------------------------
# A - ELENCO E RUOLI
# ---------------------------------------------------------------------------

ENDPOINT_TECNICO = "Endpoint tecnico — la pagina pubblica cliente sarà disponibile successivamente."

def test_01_elenco_con_i_campi_reali_e_nessun_id(staged):
    out = run(staged, "await apriLink(); esito({ s: statoLink() });", _rotte())
    [attivo, spento] = out["s"]["righe"]
    for atteso in ("Sito web", "Anna Agente", "Sopralluogo", "Durata 60 min",
                   "Buffer prima 15 min · dopo 30 min", "Attivo"):
        assert atteso in attivo["testo"], atteso
    assert attivo["azioni"] == ["Modifica", "Ruota link", "Disattiva"]
    assert "Bruno Collega" in spento["testo"] and "Disattivato" in spento["testo"]
    assert spento["azioni"] == []                 # niente riattivazione, niente rotazione
    tutto = attivo["testo"] + spento["testo"]
    for interno in ("501", "502", "agency", "token"):
        assert interno not in tutto
    assert out["open"] is True
    assert _scritture(out) == []                   # aprire non scrive


def test_02_stato_vuoto_ed_errore_leggibile(staged):
    vuoto = run(staged, "await apriLink(); esito({ s: statoLink() });", _rotte(elenco=(VUOTO,)))
    assert vuoto["s"]["vuoto"] == 1 and "Nessun link di prenotazione." in vuoto["s"]["testo"]
    rt = a30_5._rt()
    rotte = _rotte().replace(f'__route("GET", "{B}", ...[{json.dumps(rt.ok(ELENCO))}]);',
                             f'__route("GET", "{B}", ...[{json.dumps({"status": 500, "body": {"detail": "boom"}})}]);')
    assert rotte != _rotte()
    errore = run(staged, "await apriLink(); esito({ s: statoLink() });", rotte)
    assert errore["s"]["errore"] and errore["s"]["righe"] == []


@pytest.mark.parametrize("sessione", ["agency_owner", "agency_admin", "supreme"])
def test_03_chi_assegna_sceglie_l_agente(staged, sessione):
    out = run(staged, """
      await apriLink();
      await click(pan().querySelector('[data-create]'));
      esito({ s: statoLink() });
    """, _rotte(sessione))
    assert out["s"]["selAgente"] == ["Anna Agente", "Bruno Collega"]


def test_04_agent_crea_solo_per_se_senza_selettore(staged):
    out = run(staged, """
      await apriLink();
      await click(pan().querySelector('[data-create]'));
      const prima = statoLink();
      await compila({ tipo: 'call' });
      await salva();
      esito({ prima });
    """, _rotte("agent"))
    assert out["prima"]["selAgente"] is None
    assert "Io — Anna Agente" in out["prima"]["modulo"]
    [(metodo, url, corpo)] = _scritture(out)
    assert (metodo, url, corpo["assigned_user_id"]) == ("POST", B, 3)


# ---------------------------------------------------------------------------
# B - CREA, MOSTRA UNA VOLTA, COPIA, DIMENTICA
# ---------------------------------------------------------------------------

def test_05_crea_con_i_default_reali_e_mostra_il_link_una_volta(staged):
    out = run(staged, """
      await apriLink();
      await click(pan().querySelector('[data-create]'));
      await compila({ tipo: 'inspection', dopo: '30', etichetta: '  Sito web  ' });
      const durataProposta = modulo().querySelector('[data-field="duration"]').value;
      await salva();
      esito({ durataProposta, s: statoLink() });
    """, _rotte(elenco=(ELENCO, ELENCO)))
    assert out["durataProposta"] == "60"                  # default reale del tipo (enums)
    assert _scritture(out) == [("POST", B, {
        "assigned_user_id": 3, "appointment_type": "inspection", "duration_minutes": 60,
        "buffer_before_minutes": 0, "buffer_after_minutes": 30, "label": "Sito web"})]
    s = out["s"]
    assert "Questo link viene mostrato solo ora. Se lo perdi, dovrai rigenerarlo." in s["unaVolta"]
    assert s["campoUrl"] == URL
    # l'URL e' l'endpoint JSON A30-12, NON una pagina pronta per il cliente
    assert s["notaEndpoint"] == ENDPOINT_TECNICO and ENDPOINT_TECNICO in s["unaVolta"]
    assert s["campoEtichetta"] == ENDPOINT_TECNICO
    assert "Indirizzo pubblico" not in s["unaVolta"] and "pagina per il cliente" not in s["unaVolta"]
    assert len(_letture_elenco(out)) == 2                 # rilettura dopo la scrittura
    assert s["modulo"] == ""                              # il modulo si chiude


def test_06_validazioni_non_fanno_partire_nulla(staged):
    out = run(staged, """
      await apriLink();
      await click(pan().querySelector('[data-create]'));
      await salva();
      const senzaTipo = statoLink().errore;
      await compila({ tipo: 'call', durata: '0' });
      await salva();
      const durataZero = statoLink().errore;
      await compila({ durata: '30', prima: '-5' });
      await salva();
      esito({ senzaTipo, durataZero, bufferNegativo: statoLink().errore });
    """, _rotte())
    assert _scritture(out) == []
    assert out["senzaTipo"] == "Scegli il tipo di appuntamento."
    assert out["durataZero"].startswith("La durata deve essere")
    assert out["bufferNegativo"].startswith("I buffer devono essere")


def test_07_copia_il_link_e_ripiego_se_gli_appunti_non_ci_sono(staged):
    out = run(staged, """
      await apriLink();
      await click(pan().querySelector('[data-create]'));
      await compila({ tipo: 'call' });
      await salva();
      await click(pan().querySelector('[data-copy]'));
      const ok = { appunti: __appunti.slice(), avviso: statoLink().avviso };
      __appuntiRotti = true;
      await click(pan().querySelector('[data-copy]'));
      esito({ ok, ripiego: statoLink().errore });
    """, _rotte())
    assert out["ok"] == {"appunti": [URL], "avviso": "Link copiato."}
    assert out["ripiego"].startswith("Copia automatica non disponibile")


def test_08_il_token_non_viene_mai_persistito_e_si_dimentica(staged):
    out = run(staged, """
      await apriLink();
      await click(pan().querySelector('[data-create]'));
      await compila({ tipo: 'call' });
      await salva();
      const mostrato = tokenNelDom(%(t)s);
      const durante = perdite(%(t)s);
      await click(pan().querySelector('[data-done]'));          // "Ho copiato il link"
      const dopoFatto = tokenNelDom(%(t)s);
      // di nuovo, poi chiusura del pannello
      await click(pan().querySelector('[data-create]'));
      await compila({ tipo: 'call' });
      await salva();
      bottone(pan(), 'Chiudi').dispatch('click');
      await wait();
      esito({ mostrato, durante, dopoFatto, dopoChiudi: tokenNelDom(%(t)s), fine: perdite(%(t)s) });
    """ % {"t": json.dumps(TOKEN)}, _rotte())
    # durante: SOLO come valore del campo del riquadro (proprieta', non attributo)
    assert out["mostrato"] == ["valore"]
    assert out["durante"] == {"storage": 0, "log": 0}
    assert out["dopoFatto"] == [] and out["dopoChiudi"] == []
    assert out["fine"] == {"storage": 0, "log": 0}


# ---------------------------------------------------------------------------
# C - RUOTA, DISATTIVA, MODIFICA
# ---------------------------------------------------------------------------

def test_09_ruota_solo_dopo_conferma_e_mostra_il_nuovo_link(staged):
    out = run(staged, """
      await apriLink();
      await click(righe()[0].querySelectorAll('button').find((b) => b.textContent === 'Ruota link'));
      const testoConferma = pan().visibleText();
      await click(pan().querySelector('[data-confirm-no]'));
      const dopoAnnulla = __calls.filter((c) => c.url.endsWith('/rotate')).length;
      await click(righe()[0].querySelectorAll('button').find((b) => b.textContent === 'Ruota link'));
      await siConferma();
      esito({ testoConferma, dopoAnnulla, s: statoLink(), leak: perdite(%s) });
    """ % json.dumps(TOKEN_RUOTATO), _rotte(elenco=(ELENCO, ELENCO)))
    assert "smetterà subito di funzionare" in out["testoConferma"]
    assert out["dopoAnnulla"] == 0
    assert _scritture(out) == [("POST", f"{B}/501/rotate", None)]
    assert "Nuovo link generato" in out["s"]["unaVolta"] and out["s"]["campoUrl"] == URL_RUOTATO
    assert len(_letture_elenco(out)) == 2
    assert out["leak"] == {"storage": 0, "log": 0}


def test_10_disattiva_solo_dopo_conferma_e_rilegge(staged):
    spento = {"items": [_link(501, 3, stato="disabled", label="Sito web"), ELENCO["items"][1]]}
    out = run(staged, """
      await apriLink();
      await click(righe()[0].querySelector('[data-disable]'));
      const prima = __calls.filter((c) => c.url.endsWith('/disable')).length;
      const testo = pan().visibleText();
      await siConferma();
      esito({ prima, testo, s: statoLink() });
    """, _rotte(elenco=(ELENCO, spento)))
    assert out["prima"] == 0
    assert "non potrà essere riattivato" in out["testo"]
    assert _scritture(out) == [("POST", f"{B}/501/disable", None)]
    assert out["s"]["avviso"] == "Link disattivato."
    assert out["s"]["righe"][0]["azioni"] == [] and "Disattivato" in out["s"]["righe"][0]["testo"]


def test_11_modifica_manda_solo_i_campi_cambiati_e_non_cambia_l_agente(staged):
    out = run(staged, """
      await apriLink();
      await click(righe()[0].querySelector('[data-edit]'));
      const inModifica = statoLink();
      await salva();                                            // nulla di cambiato
      const nessuna = statoLink().avviso;
      await compila({ durata: '45', etichetta: 'Sito e social' });
      await salva();
      esito({ inModifica, nessuna, s: statoLink() });
    """, _rotte(elenco=(ELENCO, ELENCO)))
    assert out["inModifica"]["selAgente"] is None and "Anna Agente" in out["inModifica"]["modulo"]
    assert out["nessuna"] == "Nessuna modifica da salvare."
    assert _scritture(out) == [("PATCH", f"{B}/501",
                                {"duration_minutes": 45, "label": "Sito e social"})]
    assert out["s"]["avviso"] == "Link aggiornato."


def test_12_l_etichetta_non_si_svuota(staged):
    out = run(staged, """
      await apriLink();
      await click(righe()[0].querySelector('[data-edit]'));
      await compila({ etichetta: '' });
      await salva();
      esito({ s: statoLink() });
    """, _rotte())
    assert _scritture(out) == []
    assert out["s"]["errore"].startswith("L’etichetta non si può svuotare")


def test_13_errore_del_server_resta_leggibile(staged):
    rifiuto = {"status": 422, "body": {"code": "VALIDATION_ERROR", "detail": "duration_minutes: valore non valido"}}
    out = run(staged, """
      await apriLink();
      await click(righe()[0].querySelector('[data-edit]'));
      await compila({ durata: '30' });
      await salva();
      esito({ s: statoLink() });
    """, _rotte(patch=(rifiuto,)))
    assert "valore non valido" in out["s"]["errore"]
    assert out["s"]["avviso"] == ""


def test_14_comandi_da_tastiera_sono_pulsanti(staged):
    out = run(staged, """
      await apriLink();
      esito({ tipi: pan().querySelectorAll('button').map((b) => [b.tagName, b.type]) });
    """, _rotte())
    assert out["tipi"] and all(t == ["BUTTON", "button"] for t in out["tipi"])
