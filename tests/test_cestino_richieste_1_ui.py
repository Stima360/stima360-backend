"""CESTINO-RICHIESTE-1 (FASE H) - UI del Cestino Richieste acquirente (Shell OS), senza database.

  M  funzioni pure di trash/trash-model.js (node): blocchi con voci e
     collegamenti (solo interni), testo dello storico della richiesta, effetti
     espliciti (stato invariato, abbinamenti, contatto e comunicazioni), nome e
     riga, percorso dell'elenco, altre richieste aperte, rifiuto del ripristino
     con il collegamento al contatto;
  S  statici: rotte nominate solo da trash/trash-api.js (l'elenco solo da
     trash-model.js); la scheda richiesta usa il foglio condiviso; nessuna
     regola di permesso nel client; lessico; i fogli degli altri Cestini
     invariati;
  D  Shell VERA (main.js, router, sessione, scheda richiesta, pagina Cestino)
     nello stub DOM di P26-4/A30-5 con fetch instradato:
       d01  «Elimina…» con processi aperti: blocchi e collegamenti, conferma
            disabilitata, nessuna scrittura;
       d02  richiesta senza blocchi: effetti espliciti, motivo -> POST
            .../trash, toast, Acquirenti;
       d03  richiesta nel Cestino: scheda in sola lettura (nessun comando
            nell'intestazione ne' nelle tab), «Ripristina» -> stessa scheda
            ridisegnata, altre richieste aperte segnalate; senza permesso
            nessun bottone; contatto nel Cestino: il rifiuto mostra il motivo e
            il collegamento al contatto;
       d04  pagina Cestino: Immobili resta la predefinita; «Richieste» al tocco
            o con #/cestino/richieste; card, ripristino e rifiuto sulla card.

Senza node le prove M/D sono SKIPPED (BLOCKED), mai passate. La prova in
Chromium e' tests/test_cestino_richieste_1_browser_postgres.py.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_delete_arch_2b3_ui as t2b3
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
TRASH_DIR = ASSETS / "trash"
SCHEDA_JS = ASSETS / "views" / "acquirente-dettaglio.js"

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: livello NON eseguito (BLOCKED)")
_codice = t2b3._codice


def _modello(expr):
    return t2b3._modello(expr)


# ---------------------------------------------------------------------------
# M - funzioni pure
# ---------------------------------------------------------------------------

@node
def test_m01_blocchi_storico_effetti():
    out = _modello("""({
      b: m.buyBlockerView([
        { code: 'PROPOSAL_OPEN', label: 'Proposte in corso', link: { href: '#/acquirenti/16', label: 'Apri la richiesta' },
          items: [{ id: 3, label: 'Proposta #3 · GIU-30 · inviata', href: '#/acquirenti/16' },
                  { id: 4, label: 'Proposta #4', href: 'https://evil.test/' }] },
        { code: 'HISTORY_REQUIRES_ADMIN', label: 'backend', items: [{ code: 'BUYER_INTERACTION_HISTORY', label: 'Interazioni', count: 2 }] }]),
      e1: m.buyEffectsText({ status: 'active', matches: 3, other_open_requests: 1, contact_name: 'Mario Rossi',
                             communications_unchanged: true }),
      e2: m.buyEffectsText({ status: 'closed', matches: 1, other_open_requests: 0, communications_unchanged: true }),
      e3: m.buyEffectsText({ status: 'paused', matches: 0, other_open_requests: 2, contact_name: 'Ada' }),
      err: [m.buyTrashErrorText({ status: 404 }), m.buyTrashErrorText({ status: 403, code: 'HISTORY_REQUIRES_ADMIN' }),
            m.buyTrashErrorText({ status: 409, message: 'dal backend' })],
      toast: [m.BUY_TRASHED_TOAST, m.BUY_RESTORED_TOAST],
    })""")
    p, h = out["b"]
    assert p["count"] == 2 and p["link"] == {"href": "#/acquirenti/16", "label": "Apri la richiesta"}
    assert p["items"] == [{"label": "Proposta #3 · GIU-30 · inviata", "link": {"href": "#/acquirenti/16", "label": "Apri"}},
                          {"label": "Proposta #4", "link": None}]                  # mai un indirizzo esterno
    assert h["label"] == "Questa richiesta ha uno storico operativo. Serve un amministratore per spostarla nel Cestino."
    assert h["history"] == [{"label": "Interazioni", "count": 2}]
    assert out["e1"] == ["Lo stato resta «Attiva»: la richiesta non viene chiusa né sospesa.",
                         "3 abbinamenti escono dalle liste e dai calcoli (restano nella scheda).",
                         "Mario Rossi resta attivo, con la sua altra richiesta aperta.",
                         "Le comunicazioni del contatto non vengono sospese né annullate."]
    assert out["e2"] == ["Lo stato resta «Chiusa»: la richiesta non viene chiusa né sospesa.",
                         "1 abbinamento esce dalle liste e dai calcoli (resta nella scheda).",
                         "Il contatto resta attivo.",
                         "Le comunicazioni del contatto non vengono sospese né annullate."]
    assert out["e3"][1] == "Ada resta attivo, con le sue altre 2 richieste aperte."     # nessun abbinamento: nessuna riga
    assert out["err"] == ["Richiesta non trovata.", "Questa richiesta ha uno storico operativo. Serve un amministratore "
                          "per spostarla nel Cestino.", "dal backend"]
    assert out["toast"] == ["Richiesta spostata nel Cestino", "Richiesta ripristinata"]


@node
def test_m02_nome_riga_percorso_doppioni_rifiuto():
    out = _modello("""({
      nome: [m.buyRequestName({ id: 16, title: 'Cerca bilocale' }), m.buyRequestName({ id: 16 })],
      riga: [m.buyRequestLine({ contact_name: 'Mario Rossi', budget_target: 250000 }),
             m.buyRequestLine({ contact_name: 'Mario Rossi', budget_target: null, budget_max: 300000 }),
             m.buyRequestLine({})],
      percorso: m.buyTrashListPath(50, 50),
      d: m.buyDuplicatesView({ possible_duplicates: [{ id: 17, title: 'Altra', status: 'active' }, { id: 18, status: 'paused' }] }),
      d0: m.buyDuplicatesView({}),
      r1: m.buyRestoreErrorView({ status: 409, code: 'RESTORE_BLOCKED', message: 'Il contatto è nel Cestino',
            data: { blockers: [{ code: 'CONTACT_IN_TRASH', label: 'Il contatto è nel Cestino: ripristinalo prima',
                                 items: [{ id: 41, label: 'Mario Rossi', href: '#/contatti/41' }],
                                 link: { href: '#/cestino/contatti', label: 'Apri il Cestino Contatti' } }] } }),
      r2: m.buyRestoreErrorView({ status: 403, code: 'NOT_DELETED_BY_YOU', message: 'solo le tue' }),
    })""")
    assert out["nome"] == ["Cerca bilocale", "Richiesta #16"]
    assert out["riga"] == ["Mario Rossi · budget 250.000 €", "Mario Rossi · budget 300.000 €", ""]
    assert out["percorso"] == "/api/buy/trash/requests?limit=50&offset=50"
    assert out["d"] == [{"id": 17, "name": "Altra", "reason": "stesso contatto, attiva"},
                        {"id": 18, "name": "Richiesta #18", "reason": "stesso contatto, in pausa"}]
    assert out["d0"] == []
    assert out["r1"]["text"] == "Il contatto è nel Cestino"
    [b] = out["r1"]["blockers"]
    assert b["code"] == "CONTACT_IN_TRASH" and b["items"][0]["link"]["href"] == "#/contatti/41"
    assert b["link"] == {"href": "#/cestino/contatti", "label": "Apri il Cestino Contatti"}
    assert out["r2"] == {"text": "solo le tue", "blockers": []}


# ---------------------------------------------------------------------------
# S - statici
# ---------------------------------------------------------------------------

ROTTA_RICHIESTE = re.compile(r"/api/buy/requests/\$\{[^}]+\}/(deletion-check|trash|restore)")


def test_s01_rotte_solo_nel_client_del_cestino():
    fuori = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js") if ROTTA_RICHIESTE.search(_codice(p)))
    assert fuori == ["trash/trash-api.js"], fuori
    elenco = sorted(p.relative_to(ASSETS).as_posix() for p in ASSETS.rglob("*.js") if "/api/buy/trash/requests" in _codice(p))
    assert elenco == ["trash/trash-model.js"]


def test_s02_scheda_richiesta_usa_il_foglio_condiviso_e_nessuna_regola():
    scheda = _codice(SCHEDA_JS)
    assert "buyTrashButtonHtml()" in scheda
    assert "bindBuyTrashButton(container, data, () => navigate('acquirenti'));" in scheda
    assert "const inTrash = Boolean(data.deleted_at);" in scheda
    assert "if (inTrash) contentEl.querySelectorAll('button, form').forEach((el) => el.remove());" in scheda
    assert "info.can_restore" in scheda and "deletion-check" not in scheda
    assert "{ kind: 'buy' }" in scheda
    for p in [*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"]:
        testo = _codice(p)
        for regola in ("deleted_by_user_id", ".role", "getSession", "agency_owner", "is_platform_admin"):
            assert regola not in testo, (p.name, regola)


def test_s03_lessico_e_nessuna_chiusura():
    tutto = "\n".join(_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js", SCHEDA_JS))
    for parola in ("Elimina…", "Elimina richiesta", "Sposta nel Cestino", "Ripristina", "Richiesta spostata nel Cestino",
                   "Richiesta ripristinata", "Nel Cestino", "Nessuna richiesta è stata unita, chiusa o modificata.",
                   "Le comunicazioni del contatto non vengono sospese né annullate.",
                   "il ripristino non invia messaggi e non riapre nulla"):
        assert parola in tutto, parola
    cestino = "\n".join(_codice(p) for p in (*TRASH_DIR.glob("*.js"), ASSETS / "views" / "cestino.js"))
    for vietata in ("Cancella", "cancella", "Elimina definitivamente", "purge", "Svuota", "Scollega"):
        assert vietata not in cestino, vietata


@node
def test_s04_node_check():
    for rel in ("trash/trash-model.js", "trash/trash-api.js", "trash/trash-dialog.js", "views/cestino.js",
                "views/acquirente-dettaglio.js", "main.js"):
        esito = subprocess.run([a30_5.NODE, "--check", str(ASSETS / rel)], capture_output=True, text=True, timeout=30)
        assert esito.returncode == 0, (rel, esito.stderr)


# ---------------------------------------------------------------------------
# D - Shell eseguita (stub DOM)
# ---------------------------------------------------------------------------

rt = a30_5._rt()

MATCH = {"id": 19, "buy_request_id": 16, "property_id": 30, "property_title": "Trilocale", "property_code": "GIU-30",
         "match_class": "strong", "commercial_status": "interested", "freshness_status": "fresh", "score_total": 80,
         "effective_score": 80}
RICHIESTA = {"id": 16, "title": "Cerca bilocale", "status": "active", "priority": "normal", "urgency": "flexible",
             "contact_id": 41, "lead_id": None, "contact_name": "Mario Rossi", "budget_target": 250000,
             "matches": [MATCH], "interactions": [{"id": 5, "interaction_type": "interested", "match_id": 19,
                                                   "property_id": 30, "occurred_at": "2026-09-01T10:00:00+02:00"}],
             "history": [{"id": 1, "event_type": "request_created", "description": "Richiesta BUY creata",
                          "created_at": "2026-09-01T09:00:00+02:00"}],
             "tasks": [], "locations": [{"id": 2, "location_type": "municipality", "municipality": "Giulianova",
                                         "priority": 1}], "typologies": [], "features": [], "trash": None,
             "deleted_at": None}
NEL_CESTINO = {**RICHIESTA, "deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate",
               "deleted_by_user_id": 3,
               "trash": {"deleted_at": "2026-10-06T08:00:00+02:00", "deleted_reason": "duplicate",
                         "deleted_by_user_id": 3, "deleted_by_name": "Anna Agente", "deleted_note": "Doppione",
                         "contact_in_trash": False, "can_restore": True}}
BLOCCATO = {"can_trash": False, "history": [], "blockers": [
    {"code": "PROPOSAL_OPEN", "label": "Proposte d'acquisto in corso: ritirale o concludile prima",
     "link": {"href": "#/acquirenti/16", "label": "Apri la richiesta"},
     "items": [{"id": 3, "label": "Proposta #3 · GIU-30 · inviata", "href": "#/acquirenti/16"}]},
    {"code": "VISIT_SCHEDULED", "label": "Visite in programma: annullale o registrane l'esito prima (in Agenda)",
     "items": [{"id": 72, "label": "Visita del 10/01/2031 · GIU-30", "href": "#/agenda/giorno/2031-01-10"}]},
    {"code": "TASK_OPEN", "label": "Task aperti: completali o annullali prima in Attività",
     "link": {"href": "#/attivita", "label": "Apri Attività"},
     "items": [{"id": 9, "label": "Richiamare · scade il 12/10/2026", "href": "#/attivita"}]}]}
PUO = {"can_trash": True, "blockers": [],
       "history": [{"code": "BUYER_INTERACTION_HISTORY", "label": "Interazioni con l'acquirente", "count": 1}],
       "effects": {"status": "active", "status_label": "attiva", "matches": 1, "other_open_requests": 1,
                   "contact_id": 41, "contact_name": "Mario Rossi", "communications_unchanged": True}}
RIPRISTINATA = {"status": 200, "body": {**{k: v for k, v in RICHIESTA.items() if k not in ("matches", "interactions")},
                                        "possible_duplicates": [{"id": 17, "title": "Cerca trilocale", "status": "active"}]}}
RIFIUTO_CONTATTO = {"status": 409, "body": {
    "code": "RESTORE_BLOCKED", "detail": "Il contatto della richiesta è nel Cestino: ripristina prima il contatto",
    "blockers": [{"code": "CONTACT_IN_TRASH", "label": "Il contatto è nel Cestino: ripristinalo prima, poi ripristina la richiesta",
                  "items": [{"id": 41, "label": "Mario Rossi", "href": "#/contatti/41"}],
                  "link": {"href": "#/cestino/contatti", "label": "Apri il Cestino Contatti"}}]}}


def _voce(rid, **extra):
    v = {"id": rid, "title": f"Richiesta {rid}", "status": "active", "contact_id": 41, "contact_name": "Mario Rossi",
         "contact_in_trash": False, "budget_target": 250000, "budget_max": None,
         "deleted_at": "2026-10-05T18:30:00+02:00", "deleted_reason": "created_by_mistake", "deleted_by_user_id": 3,
         "deleted_by_name": "Anna Agente", "deleted_note": None}
    v.update(extra)
    return v


ELENCO = {"items": [_voce(16, deleted_note="Inserita due volte"), _voce(18, contact_in_trash=True, status="closed")],
          "has_more": False, "limit": 50, "offset": 0}


def _rotte(sessione="agency_owner", *, scheda=(RICHIESTA,), check=(PUO,), trash=None, restore=(RIPRISTINATA,),
           restore18=({"status": 200, "body": {"id": 18, "possible_duplicates": []}},), lista=(ELENCO,)):
    trash = trash or ({"status": 200, "body": {**NEL_CESTINO}},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/buy/requests/16/deletion-check", [c if "status" in c else rt.ok(c) for c in check]),
        ("POST", "/api/buy/requests/16/trash", list(trash)),
        ("POST", "/api/buy/requests/16/restore", list(restore)),
        ("POST", "/api/buy/requests/18/restore", list(restore18)),
        ("GET", "/api/buy/requests/16/workflow", [rt.ok(s) for s in scheda]),
        ("GET", "/api/buy/trash/requests", [rt.ok(x) for x in lista]),
        ("GET", "/api/buy/requests", [rt.ok({"items": []})]),
        ("GET", "/api/buy/dashboard", [rt.ok({"recent": []})]),
        ("GET", "/api/proposals?", [rt.ok({"items": []})]),
        ("GET", "/api/sales?", [rt.ok({"items": []})]),
        ("GET", "/api/property/trash", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/core/trash/contacts", [rt.ok({"items": [], "has_more": False, "limit": 50, "offset": 0})]),
        ("GET", "/api/", [rt.ok({"items": []})]),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});" for m, p, r in voci)


HELPERS = r"""
const RD = () => C().querySelector('#buy-trash-dialog');
const rq = (s) => RD().querySelector(s);
const rtoast = () => { const t = __dom.main.querySelector('[data-census-toast]'); return t && !t.hidden ? t.visibleText() : null; };
function rScegli(valore) {
  for (const r of RD().querySelectorAll('input[name="trash-reason"]')) {
    r.checked = r.getAttribute('value') === valore;
    if (r.checked) r.dispatch('change');
  }
}
function rFoglio() {
  if (!RD()) return null;
  return { open: !!RD()._open, testo: RD().visibleText(),
           blocchi: RD().querySelectorAll('[data-blocker]').map((li) => li.dataset.blocker),
           voci: RD().querySelectorAll('[data-blocker-item-link]').map((a) => [a.getAttribute('href'), a.textContent]),
           confermaDisabilitata: rq('[data-trash-confirm]').disabled,
           motivi: RD().querySelectorAll('input[name="trash-reason"]').map((r) => r.getAttribute('value')),
           effetti: RD().querySelector('[data-trash-effects]') ? RD().querySelector('[data-trash-effects]').visibleText() : null };
}
function comandiTab() { return C().querySelector('#request-tab-content').querySelectorAll('button, form').length; }
async function tab(chiave) { C().querySelectorAll('.tab-btn').find((b) => b.dataset.tab === chiave).dispatch('click'); await wait(); }
"""


def _run(staged, scenario, rotte, hash):  # noqa: F811
    return t2b3._run(staged, HELPERS + scenario, rotte, hash)


def _scritture(out):
    return [(c["m"], c["url"]) for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


@node
def test_d01_processi_aperti_bloccano_con_collegamenti(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const elimina = C().querySelector('#buy-trash-btn');
      const etichetta = elimina ? elimina.textContent : null;
      elimina.dispatch('click'); await wait(); await wait();
      const statoFoglio = rFoglio();
      rq('[data-trash-form]').dispatch('submit'); await wait();
      report({ etichetta, f: statoFoglio });
    """
    out = _run(staged, scenario, _rotte(check=(BLOCCATO,)), "#/acquirenti/16")
    assert out["etichetta"] == "Elimina…"
    f = out["f"]
    assert f["open"] is True and f["blocchi"] == ["PROPOSAL_OPEN", "VISIT_SCHEDULED", "TASK_OPEN"]
    assert "Elimina richiesta" in f["testo"] and "Non si può spostare nel Cestino" in f["testo"]
    assert f["voci"] == [["#/acquirenti/16", "Proposta #3 · GIU-30 · inviata"],
                         ["#/agenda/giorno/2031-01-10", "Visita del 10/01/2031 · GIU-30"],
                         ["#/attivita", "Richiamare · scade il 12/10/2026"]]
    assert f["confermaDisabilitata"] is True and f["motivi"] == []
    assert _scritture(out) == []                         # nulla si chiude, si scollega o si sposta


@node
def test_d02_senza_blocchi_effetti_espliciti_poi_acquirenti(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      C().querySelector('#buy-trash-btn').dispatch('click'); await wait(); await wait();
      const statoFoglio = rFoglio();
      const primaDelMotivo = rq('[data-trash-confirm]').disabled;
      rScegli('duplicate');
      const dopoMotivo = rq('[data-trash-confirm]').disabled;
      rq('[data-trash-note]').value = '  Doppione della 17  ';
      rq('[data-trash-form]').dispatch('submit'); await wait(); await wait();
      report({ f: statoFoglio, primaDelMotivo, dopoMotivo, hashDopo: window.location.hash, t: rtoast() });
    """
    out = _run(staged, scenario, _rotte(), "#/acquirenti/16")
    f = out["f"]
    assert f["blocchi"] == [] and f["motivi"] == ["created_by_mistake", "duplicate", "invalid_data", "test_record", "other"]
    for frase in ("Lo stato resta «Attiva»: la richiesta non viene chiusa né sospesa.",
                  "1 abbinamento esce dalle liste e dai calcoli (resta nella scheda).",
                  "Mario Rossi resta attivo, con la sua altra richiesta aperta.",
                  "Le comunicazioni del contatto non vengono sospese né annullate."):
        assert frase in f["effetti"], (frase, f["effetti"])
    assert "Lo storico resta consultabile" in f["testo"] and "Interazioni con l'acquirente" in f["testo"]
    assert out["primaDelMotivo"] is True and out["dopoMotivo"] is False
    assert _scritture(out) == [("POST", "/api/buy/requests/16/trash")]
    corpo = next(c for c in out["calls"] if c["m"] == "POST")["body"]
    assert corpo == {"reason_code": "duplicate", "note": "Doppione della 17"}
    assert out["hashDopo"] == "#/acquirenti" and out["t"] == "Richiesta spostata nel Cestino"


@node
def test_d03_scheda_nel_cestino_sola_lettura_e_ripristino(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const banner = C().querySelector('[data-buy-in-trash]');
      const prima = { banner: banner ? banner.visibleText() : null,
                      modifica: !!C().querySelector('#request-edit-btn'), elimina: !!C().querySelector('#buy-trash-btn'),
                      attivita: !!C().querySelector('#request-quick-activity'), task: !!C().querySelector('#request-quick-task') };
      const comandi = {};
      for (const t of ['panoramica', 'criteri', 'abbinamenti', 'visite', 'proposte', 'task', 'storico']) { await tab(t); comandi[t] = comandiTab(); }
      await tab('abbinamenti');
      const righe = C().querySelector('#request-tab-content').visibleText();
      await tab('panoramica');
      C().querySelector('[data-buy-restore-btn]').dispatch('click'); await wait(); await wait(); await wait();
      const avviso = C().querySelector('[data-buy-duplicates]');
      report({ prima, comandi, righe, avviso: avviso ? avviso.visibleText() : null,
               link: avviso ? avviso.querySelectorAll('a').map((a) => a.getAttribute('href')) : [],
               t: rtoast(), dopo: { banner: !!C().querySelector('[data-buy-in-trash]'),
                                    elimina: !!C().querySelector('#buy-trash-btn'),
                                    modifica: !!C().querySelector('#request-edit-btn') } });
    """
    out = _run(staged, scenario, _rotte(scheda=(NEL_CESTINO, RICHIESTA)), "#/acquirenti/16")
    p = out["prima"]
    assert "Nel Cestino" in p["banner"] and "Anna Agente" in p["banner"] and "Duplicato — Doppione" in p["banner"]
    assert "Il ripristino non invia messaggi e non riapre nulla." in p["banner"]
    assert (p["modifica"], p["elimina"], p["attivita"], p["task"]) == (False, False, False, False)
    assert set(out["comandi"].values()) == {0}, out["comandi"]           # nessun comando in nessuna tab
    assert "Trilocale" in out["righe"] and "strong" in out["righe"]        # i dati restano
    assert _scritture(out) == [("POST", "/api/buy/requests/16/restore")]
    assert out["t"] == "Richiesta ripristinata"
    assert "Lo stesso contatto ha altre richieste aperte" in out["avviso"] and "Cerca trilocale" in out["avviso"]
    assert "Nessuna richiesta è stata unita, chiusa o modificata." in out["avviso"]
    assert out["link"] == ["#/acquirenti/17"]
    assert out["dopo"] == {"banner": False, "elimina": True, "modifica": True}


@node
def test_d03b_senza_permesso_e_contatto_nel_cestino(staged):  # noqa: F811
    senza = {**NEL_CESTINO, "trash": {**NEL_CESTINO["trash"], "can_restore": False}}
    scenario = r"""
      await wait(); await wait();
      report({ bottone: !!C().querySelector('[data-buy-restore-btn]'),
               testo: C().querySelector('[data-buy-in-trash]').visibleText() });
    """
    out = _run(staged, scenario, _rotte("agent", scheda=(senza,)), "#/acquirenti/16")
    assert out["bottone"] is False and "Può ripristinarla chi l’ha spostata o un amministratore." in out["testo"]

    contatto = {**NEL_CESTINO, "trash": {**NEL_CESTINO["trash"], "contact_in_trash": True}}
    scenario = r"""
      await wait(); await wait();
      const nota = C().querySelector('[data-buy-contact-in-trash]');
      C().querySelector('[data-buy-restore-btn]').dispatch('click'); await wait(); await wait();
      const errore = C().querySelector('[data-buy-restore-error]');
      report({ nota: nota ? [nota.visibleText(), nota.querySelectorAll('a').map((a) => a.getAttribute('href'))] : null,
               errore: errore.visibleText(), link: errore.querySelectorAll('a').map((a) => a.getAttribute('href')),
               banner: !!C().querySelector('[data-buy-in-trash]'),
               bottone: C().querySelector('[data-buy-restore-btn]').textContent });
    """
    out = _run(staged, scenario, _rotte(scheda=(contatto,), restore=(RIFIUTO_CONTATTO,)), "#/acquirenti/16")
    assert "ripristina prima il contatto" in out["nota"][0] and out["nota"][1] == ["#/contatti/41"]
    assert "Il contatto della richiesta è nel Cestino" in out["errore"]
    assert out["link"] == ["#/cestino/contatti", "#/contatti/41"]
    assert out["banner"] is True and out["bottone"] == "Ripristina"            # nulla cambiato


@node
def test_d04_pagina_cestino_scheda_richieste(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const tabs = C().querySelectorAll('[data-trash-tab]').map((b) => b.dataset.trashTab);
      const iniziale = { immobili: !C().querySelector('[data-trash-panel="immobili"]').hidden,
                         richieste: !C().querySelector('[data-trash-panel="richieste"]').hidden,
                         letture: __calls.filter((c) => c.url.startsWith('/api/buy/trash/requests')).length };
      C().querySelector('[data-trash-tab="richieste"]').dispatch('click'); await wait(); await wait();
      const carte = C().querySelectorAll('[data-buy-trash-item]').map((c) => [c.dataset.buyTrashItem, c.visibleText()]);
      const apri = C().querySelectorAll('[data-buy-trash-open]').map((a) => a.getAttribute('href'));
      C().querySelector('[data-buy-restore="16"]').dispatch('click'); await wait(); await wait();
      const avviso = C().querySelector('[data-buy-duplicates]');
      report({ tabs, iniziale, carte, apri, dopo: C().querySelectorAll('[data-buy-trash-item]').map((c) => c.dataset.buyTrashItem),
               avviso: avviso ? avviso.visibleText() : null, t: rtoast() });
    """
    out = _run(staged, scenario, _rotte(), "#/cestino")
    assert out["tabs"] == ["immobili", "contatti", "edifici", "richieste"]
    assert out["iniziale"] == {"immobili": True, "richieste": False, "letture": 0}     # Immobili resta la predefinita
    assert [c[0] for c in out["carte"]] == ["16", "18"]
    uno = out["carte"][0][1]
    assert "Richiesta 16" in uno and "Mario Rossi" in uno and "budget 250.000 €" in uno and "Attiva" in uno
    assert "Creato per errore — Inserita due volte" in uno and "Anna Agente" in uno
    assert "(nel Cestino)" in out["carte"][1][1] and "Chiusa" in out["carte"][1][1]
    assert out["apri"] == ["#/acquirenti/16", "#/acquirenti/18"]
    assert _scritture(out) == [("POST", "/api/buy/requests/16/restore")]
    assert out["dopo"] == ["18"] and out["t"] == "Richiesta ripristinata" and "Cerca trilocale" in out["avviso"]


@node
def test_d04b_hash_diretto_e_rifiuto_sulla_card(staged):  # noqa: F811
    scenario = r"""
      await wait(); await wait();
      const pannello = !C().querySelector('[data-trash-panel="richieste"]').hidden;
      C().querySelector('[data-buy-restore="16"]').dispatch('click'); await wait(); await wait();
      const carta = C().querySelector('[data-buy-trash-item="16"]');
      report({ pannello, letture: __calls.filter((c) => c.url.startsWith('/api/buy/trash')).map((c) => c.url),
               resta: !!carta, errore: carta.querySelector('[data-trash-card-error]').visibleText(),
               link: carta.querySelector('[data-trash-card-error]').querySelectorAll('a').map((a) => a.getAttribute('href')) });
    """
    out = _run(staged, scenario, _rotte(restore=(RIFIUTO_CONTATTO,)), "#/cestino/richieste")
    assert out["pannello"] is True and out["letture"] == ["/api/buy/trash/requests?limit=50&offset=0"]
    assert out["resta"] is True and "ripristina prima il contatto" in out["errore"]
    assert out["link"] == ["#/cestino/contatti", "#/contatti/41"]
