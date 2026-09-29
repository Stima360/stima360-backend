"""Disponibilita' dell'Agenda (UI delle API A30-11), ESEGUITA.

`main.js` VERO, router, sessione, pagina Agenda e il pannello "Disponibilita'"
di `components/agenda/agenda-dialogs.js`, dentro lo stub di DOM di
P26-4/P27-7 con il driver di A30-13B.1 (riusato, non copiato). Cosa si prova:

* il pulsante "Disponibilita'" apre UN pannello con tre schede; aprirlo fa
  solo letture;
* owner/admin/Supreme: selettore degli agenti del server; un agent: nessun
  selettore, i propri orari; le chiusure per lui sono in sola lettura;
* orari settimanali: lunedi'->domenica, giorni chiusi, piu' fasce nello stesso
  giorno, `24:00` di fine giornata; il salvataggio manda l'intero set del
  contratto e poi RILEGGE; un orario non valido non parte; togliere tutti gli
  orari chiede conferma;
* eccezioni e chiusure: elenco, creazione con i campi reali dello schema,
  eliminazione solo dopo conferma, rilettura dopo ogni scrittura;
* un errore del server resta leggibile e non diventa un successo;
* nessun id interno a schermo; schede navigabili con le frecce.

Il permesso VERO e' del server: `tests/test_agenda_disponibilita_postgres.py`.

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
    NODE is None, reason="node non disponibile: prove Disponibilita' NON eseguite (BLOCKED)")

A = "/api/appointments"
ORARI_ANNA = {"items": [
    {"id": 11, "day_of_week": 1, "start_minute": 540, "end_minute": 780},
    {"id": 12, "day_of_week": 1, "start_minute": 900, "end_minute": 1140},
    {"id": 13, "day_of_week": 3, "start_minute": 480, "end_minute": 1440},
]}
ECCEZIONI = {"items": [
    {"id": 7777, "exception_date": "2026-10-02", "start_minute": 900, "end_minute": 1080,
     "is_available": False, "reason_code": "Visita medica"},
    {"id": 7778, "exception_date": "2026-10-04", "start_minute": 0, "end_minute": 1440,
     "is_available": True, "reason_code": None},
]}
CHIUSURE = {"items": [
    {"id": 8888, "closure_date": "2026-12-24", "start_minute": 0, "end_minute": 1440,
     "reason_code": "Vigilia"},
]}
VUOTO = {"items": []}


def _rotte(sessione="agency_owner", *, orari=(ORARI_ANNA,), salva=None, eccezioni=(ECCEZIONI,),
           chiusure=(CHIUSURE,), crea_eccezione=None, elimina_eccezione=None,
           crea_chiusura=None, elimina_chiusura=None):
    """Le risposte scriptate. `orari`, `eccezioni`, `chiusure` sono corpi di
    200; un elemento che e' gia' una risposta completa (`{"status": ...}` o
    `{"throw": True}`) passa com'e' - serve per gli stati di errore."""
    rt = a30_5._rt()
    salva = salva or ({"status": 200, "body": VUOTO},)
    risposta = (lambda x: x if isinstance(x, dict) and ("status" in x or "throw" in x)
                and "items" not in x else rt.ok(x))
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        # le rotte piu' specifiche PRIMA di /agents
        ("GET", f"{A}/agents/3/working-hours", [risposta(o) for o in orari]),
        ("PUT", f"{A}/agents/3/working-hours", list(salva)),
        ("GET", f"{A}/agents/4/working-hours", [rt.ok(VUOTO)]),
        ("GET", f"{A}/agents/3/availability-exceptions?", [risposta(e) for e in eccezioni]),
        ("POST", f"{A}/agents/3/availability-exceptions",
         list(crea_eccezione or ({"status": 201, "body": {"id": 1}},))),
        ("DELETE", f"{A}/agents/3/availability-exceptions/7777",
         list(elimina_eccezione or (rt.ok({"deleted": True}),))),
        ("GET", f"{A}/closures?", [risposta(c) for c in chiusure]),
        ("POST", f"{A}/closures", list(crea_chiusura or ({"status": 201, "body": {"id": 2}},))),
        ("DELETE", f"{A}/closures/8888", list(elimina_chiusura or (rt.ok({"deleted": True}),))),
        ("GET", f"{A}/agents", [rt.ok(a30_13b.AGENTI)]),
        ("GET", f"{A}/calendar?", [rt.ok(VUOTO)]),
        ("GET", f"{A}?", [rt.ok(VUOTO)]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


DISPO = r"""
async function apriDispo() { bottone(C(), 'Disponibilità').dispatch('click'); await wait(); }
const scheda = (k) => dlg().querySelector(`[data-tab="${k}"]`);
async function vai(k) { scheda(k).dispatch('click'); await wait(); }
const pan = () => dlg().querySelector('[data-tab-panel]');
const giorno = (d) => pan().querySelector(`[data-day="${d}"]`);
function giorni() {
  return pan().querySelectorAll('[data-day]').map((g) => ({
    d: Number(g.dataset.day),
    attivo: g.querySelector('[data-field="active"]').checked === true,
    nascosto: g.querySelector('[data-intervals]').hidden === true,
    fasce: g.querySelectorAll('[data-interval]').map((r) => [
      r.querySelector('[data-field="start"]').value, r.querySelector('[data-field="end"]').value]),
  }));
}
function statoDispo() {
  const sel = f('[data-field="availability-agent"]');
  return {
    open: !!(dlg() && dlg()._open),
    schede: dlg().querySelectorAll('[data-tab]').map((b) => ({
      k: b.dataset.tab, testo: b.textContent, attiva: b.classList.contains('active'),
      role: b.getAttribute('role'), selected: b.getAttribute('aria-selected') })),
    agenti: sel ? sel.querySelectorAll('option').map((o) => o.textContent) : null,
    testo: dlg().visibleText(),
    errore: f('[data-error]').textContent,
    avviso: dlg().querySelector('[data-notice]').visibleText(),
    righe: pan().querySelectorAll('[data-row]').map((r) => r.visibleText()),
    elimina: pan().querySelectorAll('[data-delete]').length,
    salva: pan().querySelectorAll('[data-save]').map((b) => b.textContent),
    vuoto: pan().querySelectorAll('[data-empty]').length,
  };
}
async function siConferma() { pan().querySelector('[data-confirm-yes]').dispatch('click'); await wait(); }
"""


def run(staged, scenario, rotte):  # noqa: F811
    return a30_13b.run(staged, DISPO + scenario, rotte, hash="#/agenda/settimana/2026-09-28")


def _scritture(out):
    return [(c["m"], c["url"], c["body"]) for c in out["calls"] if c["m"] in ("PUT", "POST", "DELETE")]


def _letture(out, prefisso):
    return [c["url"] for c in out["calls"] if c["m"] == "GET" and c["url"].startswith(prefisso)]


# ---------------------------------------------------------------------------
# A - APERTURA E RUOLI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sessione", ["agency_owner", "agency_admin", "supreme"])
def test_01_chi_gestisce_ha_il_selettore_e_tre_schede(staged, sessione):
    out = run(staged, "await apriDispo(); report({ s: statoDispo() });", _rotte(sessione))
    s = out["s"]
    assert s["open"] is True
    assert [(x["k"], x["testo"]) for x in s["schede"]] == [
        ("weekly", "Orari settimanali"), ("exceptions", "Eccezioni"), ("closures", "Chiusure agenzia")]
    assert [x["attiva"] for x in s["schede"]] == [True, False, False]
    assert {x["role"] for x in s["schede"]} == {"tab"}
    assert s["agenti"] == ["Anna Agente", "Bruno Collega"]
    assert "fuori orario" in s["testo"]                # CRM SOFT detto chiaramente
    assert _scritture(out) == []                        # aprire non scrive nulla
    assert _letture(out, f"{A}/agents/3/working-hours") == [f"{A}/agents/3/working-hours"]


def test_02_agent_senza_selettore_e_chiusure_in_sola_lettura(staged):
    out = run(staged, """
      await apriDispo();
      const orari = statoDispo();
      await vai('closures');
      report({ orari, chiusure: statoDispo() });
    """, _rotte("agent"))
    assert out["orari"]["agenti"] is None
    assert "I tuoi orari · Anna Agente" in out["orari"]["testo"]
    c = out["chiusure"]
    assert len(c["righe"]) == 1 and "Vigilia" in c["righe"][0]
    assert c["elimina"] == 0 and c["salva"] == []
    assert "Solo il titolare e gli amministratori" in c["testo"]
    assert _scritture(out) == []


def test_03_cambio_agente_rilegge_i_suoi_orari(staged):
    out = run(staged, """
      await apriDispo();
      const s = f('[data-field="availability-agent"]');
      s.value = '4'; s.dispatch('change');
      await wait();
      report({ s: statoDispo() });
    """, _rotte())
    assert _letture(out, f"{A}/agents/")[-1] == f"{A}/agents/4/working-hours"
    assert out["s"]["vuoto"] == 1                      # Bruno non ha orari


# ---------------------------------------------------------------------------
# B - ORARI SETTIMANALI
# ---------------------------------------------------------------------------

def test_04_settimana_giorni_chiusi_piu_fasce_e_fine_giornata(staged):
    out = run(staged, "await apriDispo(); report({ g: giorni(), s: statoDispo() });", _rotte())
    g = {x["d"]: x for x in out["g"]}
    assert list(g) == [1, 2, 3, 4, 5, 6, 7]
    assert g[1]["attivo"] and g[1]["fasce"] == [["09:00", "13:00"], ["15:00", "19:00"]]
    assert g[3]["fasce"] == [["08:00", "24:00"]]
    assert not g[2]["attivo"] and g[2]["nascosto"] and g[2]["fasce"] == []
    for nome in ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"):
        assert nome in out["s"]["testo"]


def test_05_salva_manda_il_set_intero_e_rilegge(staged):
    dopo = {"items": ORARI_ANNA["items"] + [
        {"id": 14, "day_of_week": 2, "start_minute": 540, "end_minute": 1080}]}
    out = run(staged, """
      await apriDispo();
      const g2 = giorno(2).querySelector('[data-field="active"]');
      g2.checked = true; g2.dispatch('change');          // martedi' aperto: 09:00-18:00
      await wait();
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      report({ g: giorni(), s: statoDispo() });
    """, _rotte(orari=(ORARI_ANNA, dopo), salva=({"status": 200, "body": dopo},)))
    assert _scritture(out) == [("PUT", f"{A}/agents/3/working-hours", {"slots": [
        {"day_of_week": 1, "start_minute": 540, "end_minute": 780},
        {"day_of_week": 1, "start_minute": 900, "end_minute": 1140},
        {"day_of_week": 2, "start_minute": 540, "end_minute": 1080},
        {"day_of_week": 3, "start_minute": 480, "end_minute": 1440},
    ]})]
    assert len(_letture(out, f"{A}/agents/3/working-hours")) == 2   # rilettura dopo il PUT
    assert out["s"]["avviso"] == "Orari salvati."
    assert {x["d"]: x["fasce"] for x in out["g"]}[2] == [["09:00", "18:00"]]


def test_06_orario_non_valido_non_parte(staged):
    out = run(staged, """
      await apriDispo();
      const r = giorno(1).querySelector('[data-interval]');
      r.querySelector('[data-field="start"]').value = '14:00';
      r.querySelector('[data-field="end"]').value = '10:00';
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      report({ s: statoDispo() });
    """, _rotte())
    assert _scritture(out) == []
    assert out["s"]["errore"].startswith("Lunedì: la fine deve essere dopo l'inizio")


def test_07_togliere_tutti_gli_orari_chiede_conferma(staged):
    out = run(staged, """
      await apriDispo();
      for (const d of [1, 3]) {
        const c = giorno(d).querySelector('[data-field="active"]');
        c.checked = false; c.dispatch('change');
      }
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      const primaDellaConferma = __calls.filter((c) => c.options.method === 'PUT').length;
      const testo = pan().visibleText();
      await siConferma();
      report({ primaDellaConferma, testo });
    """, _rotte(orari=(ORARI_ANNA, VUOTO)))
    assert out["primaDellaConferma"] == 0
    assert "Salvare senza orari?" in out["testo"]
    assert _scritture(out) == [("PUT", f"{A}/agents/3/working-hours", {"slots": []})]


def test_08_errore_del_server_leggibile_e_nessun_successo(staged):
    errore = {"status": 422, "body": {"code": "VALIDATION_ERROR",
                                      "detail": "La fascia si sovrappone a un'altra gia' presente per lo stesso giorno/data"}}
    out = run(staged, """
      await apriDispo();
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      report({ s: statoDispo() });
    """, _rotte(salva=(errore,)))
    assert "si sovrappone" in out["s"]["errore"]
    assert out["s"]["avviso"] == ""


# ---------------------------------------------------------------------------
# C - ECCEZIONI
# ---------------------------------------------------------------------------

def test_09_elenco_eccezioni_senza_id_interni(staged):
    out = run(staged, "await apriDispo(); await vai('exceptions'); report({ s: statoDispo() });",
              _rotte())
    righe = out["s"]["righe"]
    assert len(righe) == 2
    assert "2 ottobre" in righe[0] and "15:00–18:00" in righe[0] and "Non disponibile" in righe[0]
    assert "Visita medica" in righe[0] and "Anna Agente" in righe[0]
    assert "Tutto il giorno" in righe[1] and "Disponibile" in righe[1]
    assert "7777" not in out["s"]["testo"] and "7778" not in out["s"]["testo"]
    [lettura] = _letture(out, f"{A}/agents/3/availability-exceptions")
    assert "from=" in lettura and "to=" in lettura


def test_10_crea_eccezione_positiva_parziale_e_rilegge(staged):
    out = run(staged, """
      await apriDispo(); await vai('exceptions');
      set('[data-field="date"]', '2026-10-09');
      f('[data-field="is-available"]').value = 'true';
      const tutto = pan().querySelectorAll('[data-field="all-day"]')[0];
      tutto.checked = false; tutto.dispatch('change');
      const n = pan().querySelectorAll('[data-save]')[0].parentNode;
      n.querySelector('[data-field="start"]').value = '10:00';
      n.querySelector('[data-field="end"]').value = '12:00';
      n.querySelector('[data-field="reason"]').value = '  Apertura sabato ';
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      report({ s: statoDispo() });
    """, _rotte(eccezioni=(ECCEZIONI, ECCEZIONI)))
    assert _scritture(out) == [("POST", f"{A}/agents/3/availability-exceptions", {
        "exception_date": "2026-10-09", "is_available": True, "start_minute": 600,
        "end_minute": 720, "reason_code": "Apertura sabato"})]
    assert len(_letture(out, f"{A}/agents/3/availability-exceptions")) == 2
    assert out["s"]["avviso"] == "Eccezione aggiunta."


def test_11_crea_eccezione_negativa_tutto_il_giorno(staged):
    out = run(staged, """
      await apriDispo(); await vai('exceptions');
      set('[data-field="date"]', '2026-10-12');
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      report();
    """, _rotte())
    assert _scritture(out) == [("POST", f"{A}/agents/3/availability-exceptions", {
        "exception_date": "2026-10-12", "is_available": False, "start_minute": 0, "end_minute": 1440})]


def test_12_elimina_solo_dopo_conferma(staged):
    out = run(staged, """
      await apriDispo(); await vai('exceptions');
      pan().querySelectorAll('[data-delete]')[0].dispatch('click');
      await wait();
      pan().querySelector('[data-confirm-no]').dispatch('click');   // Annulla
      await wait();
      const dopoAnnulla = __calls.filter((c) => c.options.method === 'DELETE').length;
      pan().querySelectorAll('[data-delete]')[0].dispatch('click');
      await wait();
      await siConferma();
      report({ dopoAnnulla, s: statoDispo() });
    """, _rotte(eccezioni=(ECCEZIONI, VUOTO)))
    assert out["dopoAnnulla"] == 0
    assert _scritture(out) == [("DELETE", f"{A}/agents/3/availability-exceptions/7777", None)]
    assert out["s"]["avviso"] == "Eccezione eliminata." and out["s"]["vuoto"] == 1


# ---------------------------------------------------------------------------
# D - CHIUSURE
# ---------------------------------------------------------------------------

def test_13_owner_crea_ed_elimina_una_chiusura(staged):
    out = run(staged, """
      await apriDispo(); await vai('closures');
      set('[data-field="date"]', '2026-12-31');
      const tutto = pan().querySelector('[data-field="all-day"]');
      tutto.checked = false; tutto.dispatch('change');
      pan().querySelector('[data-field="start"]').value = '13:00';
      pan().querySelector('[data-field="end"]').value = '24:00';
      pan().querySelector('[data-field="reason"]').value = 'San Silvestro';
      pan().querySelector('[data-save]').dispatch('click');
      await wait();
      pan().querySelectorAll('[data-delete]')[0].dispatch('click');
      await wait();
      await siConferma();
      report({ s: statoDispo() });
    """, _rotte(chiusure=(CHIUSURE, CHIUSURE, CHIUSURE)))
    assert _scritture(out) == [
        ("POST", f"{A}/closures", {"closure_date": "2026-12-31", "start_minute": 780,
                                  "end_minute": 1440, "reason_code": "San Silvestro"}),
        ("DELETE", f"{A}/closures/8888", None)]
    assert out["s"]["avviso"] == "Chiusura eliminata."


# ---------------------------------------------------------------------------
# E - TASTIERA
# ---------------------------------------------------------------------------

def test_14_frecce_fra_le_schede(staged):
    out = run(staged, """
      await apriDispo();
      dlg().querySelector('[role="tablist"]').dispatch('keydown', { key: 'ArrowRight' });
      await wait();
      const dopoDestra = statoDispo().schede.map((x) => x.selected);
      dlg().querySelector('[role="tablist"]').dispatch('keydown', { key: 'ArrowLeft' });
      dlg().querySelector('[role="tablist"]').dispatch('keydown', { key: 'ArrowLeft' });
      await wait();
      report({ dopoDestra, fine: statoDispo().schede.map((x) => x.selected) });
    """, _rotte())
    assert out["dopoDestra"] == ["false", "true", "false"]
    assert out["fine"] == ["false", "false", "true"]              # da Eccezioni: <- Orari <- Chiusure


# ---------------------------------------------------------------------------
# F - GIORNI CHIUSI, STATI DI ERRORE, VALIDAZIONE (gate A30-11 UI)
# ---------------------------------------------------------------------------

def test_15_un_giorno_senza_fasce_e_scritto_chiuso(staged):
    """Il giorno chiuso si LEGGE ("Chiuso"), non si deduce dalla casella vuota;
    aprendolo la scritta sparisce, richiudendolo torna."""
    out = run(staged, """
      await apriDispo();
      const chiusi = () => pan().querySelectorAll('[data-day]')
        .filter((g) => g.querySelector('[data-closed]').hidden !== true).map((g) => Number(g.dataset.day));
      const prima = chiusi();
      const testoMartedi = giorno(2).visibleText();
      const c = giorno(2).querySelector('[data-field="active"]');
      c.checked = true; c.dispatch('change'); await wait();
      const aperto = chiusi();
      c.checked = false; c.dispatch('change'); await wait();
      report({ prima, testoMartedi, aperto, dopo: chiusi() });
    """, _rotte())
    assert out["prima"] == [2, 4, 5, 6, 7]                 # ORARI_ANNA: solo lunedi' e mercoledi'
    assert "Martedì · Chiuso" in out["testoMartedi"]      # sulla riga del SUO giorno
    assert out["aperto"] == [4, 5, 6, 7] and out["dopo"] == [2, 4, 5, 6, 7]


ERRORI_LETTURA = [
    ({"status": 403, "body": {"code": "FORBIDDEN_ROLE",
                              "detail": "Un agente puo' gestire solo i propri orari di lavoro"}},
     None),                                                # il messaggio del server o quello del codice
    ({"status": 500, "body": {"detail": "boom"}}, "Errore del server (500). Riprova."),
    ({"throw": True}, "Impossibile contattare il server. Verifica la connessione."),
]


@pytest.mark.parametrize("scheda", ["weekly", "exceptions", "closures"])
@pytest.mark.parametrize("errore,atteso", ERRORI_LETTURA,
                         ids=["403", "500", "rete"])
def test_16_errore_in_lettura_leggibile_in_ogni_scheda(staged, scheda, errore, atteso):
    rotte = {"weekly": {"orari": (errore,)}, "exceptions": {"eccezioni": (errore,)},
             "closures": {"chiusure": (errore,)}}[scheda]
    out = run(staged, f"""
      await apriDispo();
      if ('{scheda}' !== 'weekly') await vai('{scheda}');
      report({{ s: statoDispo(), pannello: pan().visibleText(),
               comandi: pan().querySelectorAll('[data-save]').length + pan().querySelectorAll('[data-delete]').length }});
    """, _rotte(**rotte))
    s = out["s"]
    assert s["errore"], s                                   # mai silenzioso
    if atteso:
        assert s["errore"] == atteso
    else:
        assert s["errore"] and "boom" not in s["errore"]
    assert s["avviso"] == "" and out["comandi"] == 0         # nessun successo, nessun comando su dati assenti
    assert "Caricamento" not in out["pannello"]
    assert _scritture(out) == []


@pytest.mark.parametrize("scheda", ["weekly", "exceptions", "closures"])
def test_16b_401_riporta_al_login_come_il_resto_del_crm(staged, scheda):
    """Il meccanismo ESISTENTE del CRM: un 401 chiama `sessionExpired()`
    (core/auth.js) e la Shell torna alla schermata di login; il pannello e i
    dati spariscono, nessuna scrittura parte."""
    errore = {"status": 401, "body": {"detail": "Not authenticated"}}
    rotte = {"weekly": {"orari": (errore,)}, "exceptions": {"eccezioni": (errore,)},
             "closures": {"chiusure": (errore,)}}[scheda]
    out = run(staged, f"""
      await apriDispo();
      if ('{scheda}' !== 'weekly') await vai('{scheda}');
      await wait();
      report({{ login: __dom.byId['login-view'].hidden !== true,
               app: __dom.byId['app-view'].hidden !== true,
               dialogo: !!(dlg() && dlg()._open) }});
    """, _rotte(**rotte))
    assert out["login"] is True and out["app"] is False
    assert out["dialogo"] is False
    assert _scritture(out) == []


def test_17_eccezione_non_valida_non_parte(staged):
    out = run(staged, """
      await apriDispo(); await vai('exceptions');
      const tutto = pan().querySelectorAll('[data-field="all-day"]')[0];
      tutto.checked = false; tutto.dispatch('change');
      const n = pan().querySelectorAll('[data-save]')[0].parentNode;
      n.querySelector('[data-field="start"]').value = '12:00';
      n.querySelector('[data-field="end"]').value = '10:00';
      pan().querySelector('[data-save]').dispatch('click'); await wait();
      const rovesciata = f('[data-error]').textContent;
      n.querySelector('[data-field="end"]').value = '';
      pan().querySelector('[data-save]').dispatch('click'); await wait();
      const senzaFine = f('[data-error]').textContent;
      tutto.checked = true; tutto.dispatch('change');
      set('[data-field="date"]', '');
      pan().querySelector('[data-save]').dispatch('click'); await wait();
      report({ rovesciata, senzaFine, senzaData: f('[data-error]').textContent });
    """, _rotte())
    assert _scritture(out) == []
    assert out["rovesciata"].startswith("L'ora di fine deve essere dopo l'ora di inizio")
    assert out["senzaFine"] == "Indica ora di inizio e di fine."
    assert out["senzaData"] == "Indica la data."


@pytest.mark.parametrize("risposta,atteso", [
    ({"status": 422, "body": {"code": "VALIDATION_ERROR", "detail":
      "La fascia si sovrappone a un'altra gia' presente per lo stesso giorno/data"}}, "si sovrappone"),
    ({"status": 403, "body": {"code": "FORBIDDEN_ROLE",
                              "detail": "Un agente puo' gestire solo i propri orari di lavoro"}}, None),
    ({"status": 422, "body": {"code": "AGENT_NOT_ACTIVE", "detail": "x"}},
     "L'agente selezionato non è un membro attivo dell'agenzia."),
    ({"throw": True}, "Impossibile contattare il server. Verifica la connessione."),
], ids=["sovrapposta-422", "403", "agente-non-attivo", "rete"])
def test_18_eccezione_rifiutata_dal_server_leggibile_e_nessun_successo(staged, risposta, atteso):
    out = run(staged, """
      await apriDispo(); await vai('exceptions');
      set('[data-field="date"]', '2026-10-02');
      pan().querySelector('[data-save]').dispatch('click'); await wait();
      report({ s: statoDispo(), abilitato: pan().querySelector('[data-save]').disabled !== true });
    """, _rotte(crea_eccezione=(risposta,)))
    assert len(_scritture(out)) == 1
    assert out["s"]["avviso"] == "" and out["s"]["errore"]
    if atteso:
        assert atteso in out["s"]["errore"]
    assert out["abilitato"]                                  # si puo' correggere e riprovare
    assert len(_letture(out, f"{A}/agents/3/availability-exceptions")) == 1   # niente rilettura finta


@pytest.mark.parametrize("scheda,rotta,detail", [
    ("exceptions", "elimina_eccezione", "Eccezione non trovata"),
    ("closures", "elimina_chiusura", "Chiusura non trovata"),
])
def test_19_elimina_gia_tolta_404_messaggio_giusto_e_rilettura(staged, scheda, rotta, detail):
    """Un 404 in questo pannello NON e' "Appuntamento non trovato": e' l'elemento
    gia' tolto da un altro operatore. Si mostra il messaggio del server e si rilegge."""
    kw = {rotta: ({"status": 404, "body": {"code": "NOT_FOUND", "detail": detail}},)}
    kw["eccezioni" if scheda == "exceptions" else "chiusure"] = (
        ECCEZIONI if scheda == "exceptions" else CHIUSURE, VUOTO)
    out = run(staged, f"""
      await apriDispo(); await vai('{scheda}');
      pan().querySelectorAll('[data-delete]')[0].dispatch('click'); await wait();
      await siConferma();
      report({{ s: statoDispo() }});
    """, _rotte(**kw))
    assert out["s"]["errore"] == f"{detail}."
    assert "Appuntamento" not in out["s"]["errore"]
    assert out["s"]["avviso"] == ""
    assert out["s"]["righe"] == [] and out["s"]["vuoto"] == 1        # riletto: gia' tolto
    prefisso = f"{A}/agents/3/availability-exceptions" if scheda == "exceptions" else f"{A}/closures"
    assert len(_letture(out, prefisso)) == 2


@pytest.mark.parametrize("risposta,atteso", [
    ({"status": 403, "body": {"code": "FORBIDDEN_ROLE",
                              "detail": "Solo owner/admin possono gestire le chiusure dell'agenzia"}}, None),
    ({"status": 422, "body": {"code": "VALIDATION_ERROR", "detail":
      "La fascia si sovrappone a un'altra gia' presente per lo stesso giorno/data"}}, "si sovrappone"),
    ({"status": 503, "body": {"detail": "x"}}, "Errore del server (503). Riprova."),
], ids=["403", "sovrapposta-422", "503"])
def test_20_chiusura_rifiutata_dal_server_leggibile(staged, risposta, atteso):
    out = run(staged, """
      await apriDispo(); await vai('closures');
      set('[data-field="date"]', '2026-12-24');
      pan().querySelector('[data-save]').dispatch('click'); await wait();
      report({ s: statoDispo() });
    """, _rotte(crea_chiusura=(risposta,)))
    assert _scritture(out) == [("POST", f"{A}/closures", {
        "closure_date": "2026-12-24", "start_minute": 0, "end_minute": 1440})]
    assert out["s"]["avviso"] == "" and out["s"]["errore"]
    if atteso:
        assert out["s"]["errore"] == atteso or atteso in out["s"]["errore"]


def test_21_agent_gestisce_le_proprie_eccezioni_ma_nessun_comando_sulle_chiusure(staged):
    """Permessi (D6): un agent vede e modifica SOLO i propri orari/eccezioni;
    le chiusure dell'agenzia le legge ma non ha comandi per cambiarle."""
    out = run(staged, """
      await apriDispo();
      const orari = { salva: pan().querySelectorAll('[data-save]').length };
      await vai('exceptions');
      const ecc = { salva: pan().querySelectorAll('[data-save]').length,
                    elimina: pan().querySelectorAll('[data-delete]').length };
      await vai('closures');
      report({ orari, ecc, s: statoDispo() });
    """, _rotte("agent"))
    assert out["orari"]["salva"] == 1 and out["ecc"]["salva"] == 1 and out["ecc"]["elimina"] == 2
    assert out["s"]["salva"] == [] and out["s"]["elimina"] == 0     # chiusure: sola lettura
    assert len(out["s"]["righe"]) == 1                               # ...ma le vede
    assert out["s"]["agenti"] is None                                # nessun selettore di agenti
    assert all(c["url"].startswith(f"{A}/agents/3/") or not c["url"].startswith(f"{A}/agents/")
               or c["url"] == f"{A}/agents" for c in out["calls"])   # mai gli orari di altri


def test_22_nessun_metodo_di_array_su_una_nodelist():
    """Regressione trovata dalla certificazione in Chromium: nel browser
    `querySelectorAll` restituisce una NodeList, che NON ha `.map`/`.filter`/
    `.find`...; lo stub di DOM dei test restituisce un array e non se ne
    accorge. "Salva orari" lanciava `querySelectorAll(...).map is not a
    function` e non salvava mai. Qui si vieta il pattern in tutto il frontend
    della Shell e della pagina pubblica (serve `Array.from(...)`)."""
    import re
    from pathlib import Path
    radice = Path(__file__).resolve().parents[1] / "static"
    ammessi = {"forEach", "length", "item", "entries", "keys", "values"}
    trovati = []
    for f in sorted(list((radice / "os_shell").rglob("*.js")) + list((radice / "public_booking").rglob("*.js"))):
        testo = f.read_text(encoding="utf-8")
        for m in re.finditer(r"querySelectorAll\((?:[^()]|\([^()]*\))*\)\s*\.\s*(\w+)", testo):
            if m.group(1) not in ammessi:
                trovati.append(f"{f.relative_to(radice)}:{testo.count(chr(10), 0, m.start()) + 1} .{m.group(1)}")
    assert trovati == [], trovati
