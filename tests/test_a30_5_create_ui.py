"""A30-5 - creazione manuale di un appuntamento, ESEGUITA.

`main.js` VERO, router, sessione, pagina Agenda, dialog Nuovo appuntamento,
`components/contact-picker.js` e `agenda/agenda-lookup.js` veri, dentro lo stub
di DOM di P26-4/P27-7 (riusato, non copiato). Il `fetch` e' INSTRADATO per
URL invece che scriptato in sequenza: le ricerche con attesa (debounce) non
hanno un ordine fisso, e cio' che si prova e' QUALI richieste partono e con
quale corpo, non in che millisecondo.

Cosa si prova, e perche' serve eseguirlo:

* il collegamento CRM si fa cercando e scegliendo: nessun ID digitato;
* il lead offerto e' solo quello del cliente scelto, e cambia con lui;
* il corpo del POST e' quello del contratto (appointments/schemas.py);
* il controllo di disponibilita' precede SEMPRE la scrittura con un agente, e
  un orario occupato non scrive nulla;
* un'alternativa aggiorna data/ora e il prossimo invio ricontrolla;
* la chiave di idempotenza resta la stessa tra un tentativo e l'altro;
* dopo la creazione l'Agenda si rilegge (o va al giorno dell'appuntamento);
* 409, 422, 404 e l'ora legale inesistente restano leggibili.

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove A30-5 NON eseguite (BLOCKED)")

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DST_MESSAGE = "Questo orario non esiste a causa del cambio dell'ora legale. Scegli un altro orario."


def _rt():
    from tests import test_p27_7_network_runtime as rt
    return rt


# `isConnected` come nel DOM: un nodo e' nel documento se discende da <main>.
# La pagina Agenda lo usa per scartare le risposte arrivate dopo che il router
# l'ha tolta; lo stub di P26-4 non ne aveva bisogno.
IS_CONNECTED = r"""
Object.defineProperty(__dom.El.prototype, 'isConnected', {
  get() { for (let n = this; n; n = n.parentNode) if (n === __dom.main) return true; return false; },
});
"""

# fetch instradato: ogni rotta e' (metodo, prefisso, risposte...). L'ultima
# risposta di una rotta si ripete; le precedenti si consumano una alla volta.
ROUTED_FETCH = r"""
const __routes = [];
globalThis.__route = (method, prefix, ...risposte) => __routes.push({ method, prefix, risposte });
globalThis.fetch = async (url, options = {}) => {
  calls.push({ url, options });
  const m = String(options.method || 'GET').toUpperCase();
  const r = __routes.find((x) => x.method === m && (url === x.prefix || url.startsWith(x.prefix)));
  if (!r) return reply({ status: 500, body: { detail: `non previsto ${m} ${url}` } });
  const spec = r.risposte.length > 1 ? r.risposte.shift() : r.risposte[0];
  if (spec.throw) throw new TypeError('rete non raggiungibile');
  return reply(spec);
};
"""

HELPERS = r"""
const C = () => __dom.byId['content'];
const dlg = () => C().querySelector('dialog.agenda-dialog');
const f = (sel) => dlg().querySelector(sel);
const ff = (sel) => dlg().querySelectorAll(sel);
const wait = async (ms = 0) => { await new Promise((r) => setTimeout(r, ms)); await __settle(40); };
const bottone = (root, testo) => root.querySelectorAll('button').find((b) => b.textContent.trim() === testo);
const set = (sel, v, ev = 'input') => { const e = f(sel); e.value = v; e.dispatch(ev); };
async function apriNuovo() { bottone(C(), '+ Nuovo appuntamento').dispatch('click'); await wait(); }
async function scegliCliente(i = 0) {
  set('.contact-picker-search', 'Mario'); await wait(350);
  ff('.contact-picker-result')[i].dispatch('click'); await wait();
}
async function cercaStima(testo = 'Rossi') {
  set('[data-stima-search]', testo); await wait(350);
}
async function scegliStima(i = 0) {
  f('[data-stima-results]').querySelectorAll('.agenda-lookup-item')[i].dispatch('click'); await wait();
}
async function scegliImmobile() {
  set('[data-property-search]', 'trilo'); await wait(350);
  f('[data-property-results]').querySelector('.agenda-lookup-item').dispatch('click'); await wait();
}
function orario(data, inizio, fine) {
  f('[data-field="date"]').value = data;
  set('[data-field="start"]', inizio);
  if (fine) set('[data-field="end"]', fine);
}
async function conferma() { f('form').dispatch('submit'); await wait(); }
function chiamate() {
  return __calls.map((c) => ({
    m: String(c.options.method || 'GET').toUpperCase(), url: c.url,
    body: c.options.body ? JSON.parse(c.options.body) : null,
    headers: Object.keys(c.options.headers || {}), cred: c.options.credentials || null,
  }));
}
function report(extra = {}) {
  console.log(JSON.stringify({
    calls: chiamate(), open: !!(dlg() && dlg()._open),
    error: dlg() && f('[data-error]') ? f('[data-error]').textContent : null,
    status: dlg() && f('[data-availability-status]') ? f('[data-availability-status]').visibleText() : null,
    alt: dlg() && f('[data-alternatives]')
      ? f('[data-alternatives]').querySelectorAll('button').map((b) => b.textContent) : [],
    duration: dlg() && f('[data-duration-text]') ? f('[data-duration-text]').textContent : null,
    hash: window.location.hash, content: C().visibleText(), ...extra,
  }));
}
"""

AGENTI = {"items": [{"id": 3, "name": "Anna Agente"}, {"id": 4, "name": "Bruno Collega"}]}
CONTATTI = {"items": [
    {"id": 41, "display_name": "Mario Rossi", "phone": "333 111", "email": "mario@example.test"},
    {"id": 42, "display_name": "Mario Rossi", "phone": "334 222", "email": None},
]}
LEADS_41 = {"items": [
    {"id": 501, "contact_id": 41, "pipeline": "seller", "stage": "nuovo", "status": "open",
     "created_at": "2026-09-01T10:00:00Z"},
    {"id": 502, "contact_id": 41, "pipeline": "buyer", "stage": "qualificato", "status": "closed",
     "created_at": "2026-05-02T10:00:00Z"},
]}
IMMOBILI = {"items": [{"id": 77, "title": "Trilocale vista mare", "code": "GIU-12",
                       "address": "Via Roma 1", "city": "Giulianova"}]}
STIME = {"items": [
    {"id": 3001, "data": "2026-09-20T10:15:00", "nome": "Mario", "cognome": "Rossi",
     "comune": "Giulianova", "microzona": "Lido", "via": "Via Roma", "civico": "1",
     "tipologia": "Appartamento", "mq": 85},
    {"id": 3002, "data": "2026-08-02T09:00:00", "nome": "Mario", "cognome": "Rossi",
     "comune": "Teramo", "microzona": None, "via": None, "civico": None,
     "tipologia": None, "mq": None},
]}
LIBERO = {"available": True, "conflicts": [], "alternatives": []}
OCCUPATO = {"available": False,
            "conflicts": [{"start_at": "2026-09-28T10:00:00+02:00",
                           "end_at": "2026-09-28T11:00:00+02:00", "label": "Occupato"}],
            "alternatives": [{"start_at": "2026-09-28T11:30:00+02:00",
                              "end_at": "2026-09-28T12:30:00+02:00"},
                             {"start_at": "2026-09-29T09:00:00+02:00",
                              "end_at": "2026-09-29T10:00:00+02:00"}]}


def _creato(**extra):
    riga = {"id": 900, "appointment_type": "seller_meeting", "status": "scheduled",
            "start_at": "2026-09-28T10:00:00+02:00", "end_at": "2026-09-28T11:00:00+02:00",
            "assigned_user_id": 3, "version": 1, "contact_id": 41}
    riga.update(extra)
    return riga


def _rotte(*, check=(LIBERO,), create=None, lista=({"items": []},), leads=LEADS_41):
    rt = _rt()
    create = create or ({"status": 201, "body": _creato()},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(rt.TENANT)]),
        ("GET", "/api/appointments/agents", [rt.ok(AGENTI)]),
        ("POST", "/api/appointments/availability/check", [rt.ok(c) for c in check]),
        ("POST", "/api/appointments", list(create)),
        ("GET", "/api/appointments?", [rt.ok(x) for x in lista]),
        ("GET", "/api/core/contacts?search=", [rt.ok(CONTATTI)]),
        ("GET", "/api/core/leads?contact_id=41", [rt.ok(leads)]),
        ("GET", "/api/core/leads?contact_id=42", [rt.ok({"items": []})]),
        ("GET", "/api/property/properties?search=", [rt.ok(IMMOBILI)]),
        ("GET", "/api/appointments/lookups/stime?", [rt.ok(STIME)]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    return _rt()._stage(tmp_path_factory.mktemp("a30-5"))


# Lo stub di P26-4 legge solo attributi `nome="valore"`. Il markup dei dialog
# Agenda (A30-4 e A30-5) usa anche attributi senza valore - `data-title`,
# `data-availability hidden`, `disabled` - come ogni HTML vero: qui, e SOLO in
# questo driver, il parser li accetta. Il sostituto e' controllato: se lo stub
# cambia, il test lo dice invece di girare su un parser diverso.
_ATTR_OLD = """for (const [, name, value] of (attributes || '').matchAll(/([A-Za-z-]+)=["']([^"']*)["']/g)) {"""
_ATTR_NEW = """for (const [, name, value = ''] of (attributes || '').matchAll(/([A-Za-z-]+)(?:=["']([^"']*)["'])?/g)) {
      if (name === 'hidden') node.hidden = true;
      if (name === 'disabled') node.disabled = true;"""


def _dom():
    dom = _rt().DOM
    assert dom.count(_ATTR_OLD) == 1, "lo stub DOM di P26-4 e' cambiato"
    return dom.replace(_ATTR_OLD, _ATTR_NEW)


def run(staged: Path, scenario: str, rotte: str, hash: str = "#/agenda/lista/2026-09-26") -> dict:
    rt = _rt()
    driver = staged.parent / "driver-a30-5.mjs"
    driver.write_text(
        _dom() + rt.FETCH + rt.EXTRA_DOM + IS_CONNECTED + ROUTED_FETCH
        + f"\n{rotte}\n"
        + f"window.location.hash = '{hash}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle(40);\n"
        + HELPERS + scenario + "\n",
        encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _scritture(out, url="/api/appointments"):
    return [c for c in out["calls"] if c["m"] == "POST" and c["url"] == url]


def _controlli(out):
    return _scritture(out, "/api/appointments/availability/check")


# ---------------------------------------------------------------------------
# 1-3 - apertura, ricerca e selezione, nessun ID digitato
# ---------------------------------------------------------------------------

def test_01_il_dialog_si_apre_con_tutti_i_campi_del_flusso(staged):
    out = run(staged, """
      await apriNuovo();
      report({
        campi: ['type', 'agent', 'date', 'start', 'end', 'duration', 'lead', 'location', 'notes']
          .map((n) => !!f(`[data-field="${n}"]`)),
        picker: !!f('.contact-picker-search'), immobile: !!f('[data-property-search]'),
        verifica: !!bottone(dlg(), 'Verifica disponibilità'),
        leadDisabled: f('[data-field="lead"]').disabled,
        testo: dlg().visibleText(),
      });
    """, _rotte())
    assert out["open"] is True
    assert all(out["campi"]) and out["picker"] and out["immobile"] and out["verifica"]
    assert out["leadDisabled"] is True                 # prima si sceglie il cliente
    assert "Collegamento CRM (facoltativo)" in out["testo"]
    assert "Durata: 1 h" in out["duration"]              # seller_meeting / default visibile


def test_02_03_cliente_lead_e_immobile_si_cercano_e_si_scelgono_senza_id(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliCliente();
      const opzioniLead = f('[data-field="lead"]').children.map((o) => o.textContent);
      await scegliImmobile();
      report({
        opzioniLead, leadDisabled: f('[data-field="lead"]').disabled,
        cliente: f('.contact-picker-selected').visibleText(),
        immobile: f('[data-property-selected]').visibleText(),
        inputs: dlg().querySelectorAll('input').map((i) => i.dataset.field || i.className),
        testo: dlg().visibleText(),
      });
    """, _rotte())
    urls = [c["url"] for c in out["calls"] if c["m"] == "GET"]
    assert "/api/core/contacts?search=Mario&limit=10" in urls
    assert "/api/core/leads?contact_id=41&limit=20" in urls          # solo i lead DEL cliente
    assert "/api/property/properties?search=trilo&limit=10" in urls
    assert out["opzioniLead"] == ["Nessun lead", "seller · nuovo · aperto · dal 01/09/2026",
                                  "buyer · qualificato · chiuso · dal 02/05/2026"]
    assert out["leadDisabled"] is False
    assert "Mario Rossi" in out["cliente"] and "333 111" in out["cliente"]
    assert "Trilocale vista mare" in out["immobile"] and "GIU-12" in out["immobile"]
    # nessun campo per un ID, nessun ID mostrato
    for campo in out["inputs"]:
        assert not re.search(r"(?i)(^|_|-)id$", str(campo)), campo
    for identificativo in ("41", "501", "502", "77", "3001", "3002"):
        assert not re.search(rf"(?<![\\d]){identificativo}(?![\\d])", out["testo"]), identificativo
    assert not re.search(r"(?i)\\bid\\b", out["testo"])


def test_02b_cambiare_cliente_azzera_il_lead(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliCliente(0);
      f('[data-field="lead"]').value = '501';
      bottone(f('.contact-picker-selected'), 'Cambia').dispatch('click'); await wait();
      await scegliCliente(1);                      // l'omonimo senza lead
      f('[data-field="agent"]').value = '';
      await conferma();
      report({ opzioni: f('[data-field="lead"]') ? f('[data-field="lead"]').children.map((o) => o.textContent) : [],
               disabled: f('[data-field="lead"]').disabled });
    """, _rotte())
    corpo = _scritture(out)[0]["body"]
    assert corpo["contact_id"] == 42 and "lead_id" not in corpo
    assert out["opzioni"] == ["Nessun lead"] and out["disabled"] is True


# ---------------------------------------------------------------------------
# 4-7 - il corpo del POST, la chiave, con e senza agente
# ---------------------------------------------------------------------------

def test_04_07_08_payload_completo_con_agente_dopo_il_controllo(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="type"]').value = 'seller_meeting';
      await scegliCliente();
      f('[data-field="lead"]').value = '501';
      await scegliImmobile();
      f('[data-field="agent"]').value = '3'; f('[data-field="agent"]').dispatch('change');
      orario('2026-09-28', '10:00', '11:00');
      f('[data-field="location"]').value = 'Via Roma 1';
      await conferma();
      report();
    """, _rotte())
    scritture = [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]
    assert [c["url"] for c in scritture] == ["/api/appointments/availability/check",
                                             "/api/appointments"]      # prima il controllo
    assert scritture[0]["body"] == {"assigned_user_id": 3,
                                    "start_at": "2026-09-28T10:00:00+02:00",
                                    "end_at": "2026-09-28T11:00:00+02:00"}
    corpo = scritture[1]["body"]
    chiave = corpo.pop("client_request_id")
    assert UUID4.match(chiave), chiave
    assert corpo == {"appointment_type": "seller_meeting", "status": "scheduled",
                     "start_at": "2026-09-28T10:00:00+02:00", "end_at": "2026-09-28T11:00:00+02:00",
                     "assigned_user_id": 3, "contact_id": 41, "lead_id": 501, "property_id": 77,
                     "location_text": "Via Roma 1"}
    assert out["open"] is False                                          # 12: dialog chiuso


def test_06_senza_agente_nasce_richiesta_e_non_si_controlla_nessuna_agenda(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="type"]').value = 'call';
      orario('2026-09-28', '15:00', '15:30');
      await conferma();
      report();
    """, _rotte(create=({"status": 201, "body": _creato(status="requested", assigned_user_id=None)},)))
    assert _controlli(out) == []
    corpo = _scritture(out)[0]["body"]
    assert corpo["status"] == "requested" and corpo["assigned_user_id"] is None
    assert "contact_id" not in corpo and "lead_id" not in corpo and "property_id" not in corpo


def test_05_la_chiave_resta_la_stessa_tra_un_tentativo_e_l_altro(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();                    // 500: nessuna chiusura
      const primoErrore = f('[data-error]').textContent;
      await conferma();                    // secondo tentativo
      report({ primoErrore });
    """, _rotte(create=({"status": 500, "body": {"detail": "boom"}},
                        {"status": 201, "body": _creato()})))
    creazioni = _scritture(out)
    assert len(creazioni) == 2
    assert creazioni[0]["body"]["client_request_id"] == creazioni[1]["body"]["client_request_id"]
    assert out["primoErrore"] == "Errore del server (500). Riprova."
    assert out["open"] is False


# ---------------------------------------------------------------------------
# 9-11 - occupato, alternative, scelta di un'alternativa
# ---------------------------------------------------------------------------

def test_09_10_11_occupato_non_scrive_mostra_alternative_e_la_scelta_ricontrolla(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      const dopoConflitto = { status: f('[data-availability-status]').visibleText(),
        alt: f('[data-alternatives]').querySelectorAll('button').map((b) => b.textContent),
        error: f('[data-error]').textContent, open: dlg()._open };
      f('[data-alternatives]').querySelectorAll('button')[0].dispatch('click'); await wait();
      const dopoScelta = { date: f('[data-field="date"]').value, start: f('[data-field="start"]').value,
        end: f('[data-field="end"]').value, durata: f('[data-duration-text]').textContent,
        nascosto: f('[data-availability]').hidden };
      await conferma();
      report({ dopoConflitto, dopoScelta });
    """, _rotte(check=(OCCUPATO, LIBERO),
                create=({"status": 201, "body": _creato(start_at="2026-09-28T11:30:00+02:00",
                                                         end_at="2026-09-28T12:30:00+02:00")},)))
    d = out["dopoConflitto"]
    assert d["status"].startswith("ORARIO NON DISPONIBILE")
    assert d["open"] is True and "Orario non disponibile" in d["error"]
    assert d["alt"] == ["28/09/2026, 11:30–12:30", "29/09/2026, 09:00–10:00"], d["alt"]
    s = out["dopoScelta"]
    assert (s["date"], s["start"], s["end"]) == ("2026-09-28", "11:30", "12:30")
    assert s["durata"] == "Durata: 1 h" and s["nascosto"] is True
    controlli = _controlli(out)
    assert len(controlli) == 2                                # la scelta NON scrive, ricontrolla
    assert controlli[1]["body"]["start_at"] == "2026-09-28T11:30:00+02:00"
    creazioni = _scritture(out)
    assert len(creazioni) == 1                                # l'occupato non ha scritto
    assert creazioni[0]["body"]["start_at"] == "2026-09-28T11:30:00+02:00"


def test_08b_verifica_disponibilita_senza_scrivere(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      bottone(dlg(), 'Verifica disponibilità').dispatch('click'); await wait();
      report();
    """, _rotte(check=(OCCUPATO,)))
    assert len(_controlli(out)) == 1 and _scritture(out) == []
    assert out["status"].startswith("ORARIO NON DISPONIBILE") and len(out["alt"]) == 2
    assert out["open"] is True


# ---------------------------------------------------------------------------
# 12-13 - successo e aggiornamento dell'Agenda
# ---------------------------------------------------------------------------

def test_12_13_dopo_la_creazione_l_agenda_si_rilegge_e_mostra_il_nuovo(staged):
    creato = _creato()
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte(lista=({"items": []}, {"items": [creato]})))
    liste = [c for c in out["calls"] if c["m"] == "GET" and c["url"].startswith("/api/appointments?")]
    assert len(liste) == 2                                   # riletta, senza ricaricare la Shell
    assert out["open"] is False
    assert ("Appuntamento creato: 28/09/2026, 10:00 · Appuntamento proprietario · Fissato."
            in out["content"]), out["content"]
    assert "Appuntamento proprietario" in out["content"]
    assert out["hash"] == "#/agenda/lista/2026-09-26"


def test_13b_fuori_periodo_si_va_al_giorno_dell_appuntamento(staged):
    creato = _creato(start_at="2026-10-20T10:00:00+02:00", end_at="2026-10-20T11:00:00+02:00")
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2026-10-20', '10:00', '11:00');
      await conferma();
      const hashDopo = window.location.hash;
      await window._fire('hashchange'); await wait();
      report({ hashDopo });
    """, _rotte(create=({"status": 201, "body": creato},),
                lista=({"items": []}, {"items": [creato]})))
    assert out["hashDopo"] == "#/agenda/lista/2026-10-20"
    # il messaggio sopravvive alla navigazione, e il nuovo e' nella lista
    assert "Appuntamento creato: 20/10/2026, 10:00" in out["content"], out["content"]
    assert out["content"].count("Appuntamento creato") == 1


# ---------------------------------------------------------------------------
# 14-17 - errori, DST, isolamento
# ---------------------------------------------------------------------------

def test_14_il_409_del_server_mostra_le_alternative_e_non_chiude(staged):
    conflitto = {"status": 409, "body": {"detail": "Orario non disponibile per l'agente",
                                         "code": "APPOINTMENT_CONFLICT",
                                         "conflicts": OCCUPATO["conflicts"],
                                         "alternatives": OCCUPATO["alternatives"]}}
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte(create=(conflitto,)))
    assert out["open"] is True
    assert out["error"] == "Orario non disponibile per l'agente."
    assert out["status"].startswith("ORARIO NON DISPONIBILE") and len(out["alt"]) == 2


def test_15_il_422_mostra_il_detail_leggibile(staged):
    errore = {"status": 422, "body": {"detail": "Il lead indicato appartiene a un altro contatto",
                                      "code": "LINK_MISMATCH"}}
    out = run(staged, """
      await apriNuovo();
      await scegliCliente();
      f('[data-field="lead"]').value = '501';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte(create=(errore,)))
    assert out["open"] is True
    assert out["error"] == "Il lead indicato appartiene a un altro contatto"


def test_15b_il_404_della_creazione_parla_della_selezione(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliImmobile();
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte(create=({"status": 404, "body": {"detail": "Risorsa non trovata", "code": "NOT_FOUND"}},)))
    assert out["open"] is True
    assert out["error"] == ("Il cliente, il lead, la stima o l’immobile scelto non è più "
                            "disponibile in questa agenzia: rifai la selezione.")


def test_16_ora_legale_inesistente_niente_richieste_messaggio_certificato(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="agent"]').value = '3';
      orario('2027-03-28', '02:30', '03:30');
      bottone(dlg(), 'Verifica disponibilità').dispatch('click'); await wait();
      const daVerifica = f('[data-error]').textContent;
      await conferma();
      report({ daVerifica });
    """, _rotte())
    assert out["daVerifica"] == DST_MESSAGE and out["error"] == DST_MESSAGE
    assert _controlli(out) == [] and _scritture(out) == []
    assert out["open"] is True


def test_17_isolamento_nessuna_agenzia_nessun_authorization_solo_cookie(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliCliente();
      f('[data-field="lead"]').value = '501';
      await scegliImmobile();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte())
    for c in out["calls"]:
        assert c["cred"] == "include", c
        assert "Authorization" not in c["headers"], c
        testo = json.dumps(c).lower()
        assert "agency_id" not in testo and "created_by" not in testo, c
    # le sole letture fuori da /api/appointments sono le tre del collegamento CRM
    fuori = {re.sub(r"\?.*", "", c["url"]) for c in out["calls"]
             if not c["url"].startswith(("/api/appointments", "/api/operator-auth"))}
    assert fuori == {"/api/core/contacts", "/api/core/leads", "/api/property/properties"}
    assert all(c["m"] == "GET" for c in out["calls"]
               if not c["url"].startswith("/api/appointments"))


# ---------------------------------------------------------------------------
# A30-5 - STIMA
# ---------------------------------------------------------------------------

def _stime(out):
    return [c for c in out["calls"] if c["url"].startswith("/api/appointments/lookups/stime")]


def test_18_stima_cercata_scelta_senza_id_e_inviata_come_stima_id(staged):
    out = run(staged, """
      await apriNuovo();
      f('[data-field="type"]').value = 'seller_meeting';
      await cercaStima('Rossi');
      const risultati = f('[data-stima-results]').querySelectorAll('.agenda-lookup-item').map((b) => b.textContent);
      await scegliStima(0);
      const scelta = f('[data-stima-selected]').visibleText();
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report({ risultati, scelta });
    """, _rotte())
    assert [c["url"] for c in _stime(out)] == [
        "/api/appointments/lookups/stime?search=Rossi&limit=10"]
    assert out["risultati"] == [
        "Mario Rossi — Via Roma 1, Giulianova · Appartamento 85 m² · stima del 20/09/2026",
        "Mario Rossi — Teramo · stima del 02/08/2026"]
    assert "Mario Rossi" in out["scelta"] and "3001" not in out["scelta"]
    corpo = _scritture(out)[0]["body"]
    assert corpo["stima_id"] == 3001
    assert "contact_id" not in corpo and "lead_id" not in corpo


def test_19_senza_stima_nessun_stima_id(staged):
    out = run(staged, """
      await apriNuovo();
      await cercaStima('Rossi');                   // cercata ma NON scelta
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte())
    assert "stima_id" not in _scritture(out)[0]["body"]


def test_20_cliente_e_lead_restringono_le_stime_alla_relazione_core(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliCliente();                        // -> stime dei lead del cliente
      f('[data-field="lead"]').value = '501';
      f('[data-field="lead"]').dispatch('change'); await wait();   // -> stime del lead
      await cercaStima('Giu');                      // ricerca DENTRO il lead
      report({ hint: f('[data-stima-hint]').textContent });
    """, _rotte())
    assert [c["url"] for c in _stime(out)] == [
        "/api/appointments/lookups/stime?contact_id=41&limit=10",
        "/api/appointments/lookups/stime?lead_id=501&limit=10",
        "/api/appointments/lookups/stime?search=Giu&lead_id=501&limit=10",
    ]
    assert out["hint"].startswith("Solo le stime collegate al lead scelto")


def test_20b_cambiare_cliente_o_lead_azzera_la_stima_scelta(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliCliente(0);
      await wait();
      await scegliStima(0);                         // stima collegata al cliente 41
      bottone(f('.contact-picker-selected'), 'Cambia').dispatch('click'); await wait();
      await scegliCliente(1);                       // l'omonimo: la stima non vale piu'
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report({ selezione: f('[data-stima-selected]').hidden });
    """, _rotte())
    corpo = _scritture(out)[0]["body"]
    assert corpo["contact_id"] == 42 and "stima_id" not in corpo
    assert out["selezione"] is True


def test_21_la_ricerca_stime_e_solo_lettura_e_solo_nell_agenda(staged):
    out = run(staged, """
      await apriNuovo();
      await cercaStima('Rossi');
      await scegliStima(1);
      await cercaStima('Teramo');
      report();
    """, _rotte())
    assert _stime(out) and all(c["m"] == "GET" for c in _stime(out))
    assert [c for c in out["calls"] if c["m"] != "GET"] == []      # nessuna scrittura
    for c in _stime(out):
        assert c["cred"] == "include" and "Authorization" not in c["headers"]
        assert "agency" not in c["url"]


def test_22_gli_altri_picker_restano_invariati_con_la_stima(staged):
    out = run(staged, """
      await apriNuovo();
      await scegliCliente();
      f('[data-field="lead"]').value = '501';
      f('[data-field="lead"]').dispatch('change'); await wait();
      await scegliStima(0);
      await scegliImmobile();
      f('[data-field="agent"]').value = '3';
      orario('2026-09-28', '10:00', '11:00');
      await conferma();
      report();
    """, _rotte())
    corpo = _scritture(out)[0]["body"]
    corpo.pop("client_request_id")
    assert corpo == {"appointment_type": corpo["appointment_type"], "status": "scheduled",
                     "start_at": "2026-09-28T10:00:00+02:00", "end_at": "2026-09-28T11:00:00+02:00",
                     "assigned_user_id": 3, "contact_id": 41, "lead_id": 501, "stima_id": 3001,
                     "property_id": 77}
    assert out["open"] is False
