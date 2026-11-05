"""SCRIVANIA - PRIVACY FRA OPERATORI E AGENZIE, sull'API vera e PostgreSQL VERO
(banco 2C, usa-e-getta). Dati solo sintetici.

La regola (core/scope.py, AGENT_DERIVED): un agente legge un task o un'attivita'
solo se ne e' il RESPONSABILE (agente del lead, altrimenti del contatto) o
l'AUTORE (`created_by_user_id`, scritto dal server). Titolare e amministratore
leggono l'agenzia; il Superadmin solo dentro l'agenzia in cui e' entrato.

  01  letture: elenco, filtri per contatto/lead, contatori, descrizioni.
  02  scritture: un agente non completa, non riprogramma, non segna come errore il
      task di un collega (404, la riga resta com'era); il suo si'.
  03  il responsabile restituito dal server: lead prima del contatto; nessuno se
      lead e contatto non sono assegnati; il testo libero non conta.
  04  titolare, amministratore, Superadmin dentro A: tutta A, niente di B;
      Superadmin fuori da ogni agenzia: nessun dato.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]


def _contatto(app, nome, agente=None, agenzia="a"):
    return _q(app, "INSERT INTO contacts (agency_id, display_name, phone, assigned_agent_id) VALUES (%s, %s, %s, %s) "
                   "RETURNING id", (app["ids"][agenzia], nome, "3330000000",
                                    app["ids"][agente] if agente else None))[0][0]


def _lead(app, contatto, agente=None, agenzia="a"):
    return _q(app, "INSERT INTO leads (agency_id, contact_id, pipeline, stage, status, assigned_agent_id) "
                   "VALUES (%s, %s, 'sell', 'new', 'open', %s) RETURNING id",
              (app["ids"][agenzia], contatto, app["ids"][agente] if agente else None))[0][0]


def _task(app, titolo, *, contatto=None, lead=None, autore=None, assegnato=None, agenzia="a"):
    return _q(app, "INSERT INTO tasks (agency_id, contact_id, lead_id, title, description, priority, status, due_at, "
                   "assigned_to, created_by_user_id, metadata) VALUES (%s, %s, %s, %s, %s, 'high', 'open', %s, %s, %s, "
                   "%s::jsonb) RETURNING id",
              (app["ids"][agenzia], contatto, lead, titolo, f"Nota riservata di {titolo}",
               datetime.now(timezone.utc) - timedelta(days=1), assegnato,
               app["ids"][autore] if autore else None, json.dumps({})))[0][0]


def _attivita(app, testo, *, contatto=None, lead=None, autore=None, agenzia="a"):
    return _q(app, "INSERT INTO activities (agency_id, contact_id, lead_id, activity_type, direction, description, "
                   "occurred_at, created_by_user_id) VALUES (%s, %s, %s, 'note', 'internal', %s, NOW(), %s) RETURNING id",
              (app["ids"][agenzia], contatto, lead, testo, app["ids"][autore] if autore else None))[0][0]


@pytest.fixture
def mondo(app):
    c_mio = _contatto(app, "Cliente di Aldo", "agent_a")
    c_collega = _contatto(app, "Cliente di Ada", "agent2_a")
    c_libero = _contatto(app, "Cliente senza agente")
    c_b = _contatto(app, "Cliente di Beta", agenzia="b")
    l_mio = _lead(app, c_mio, "agent_a")
    l_collega_su_mio = _lead(app, c_mio, "agent2_a")       # lead di Ada sul cliente di Aldo
    l_libero_su_collega = _lead(app, c_collega)            # lead senza agente: vale il contatto (Ada)
    t = {
        "mio_lead": _task(app, "T-mio-lead", contatto=c_mio, lead=l_mio),
        "mio_contatto": _task(app, "T-mio-contatto", contatto=c_mio),
        "collega_lead_su_mio": _task(app, "T-collega-lead", contatto=c_mio, lead=l_collega_su_mio),
        "collega": _task(app, "T-collega", contatto=c_collega, assegnato="Aldo Alfa"),
        "collega_via_contatto": _task(app, "T-collega-contatto", contatto=c_collega, lead=l_libero_su_collega),
        "scritto_da_me": _task(app, "T-scritto-da-me", contatto=c_collega, autore="agent_a"),
        "libero": _task(app, "T-libero", contatto=c_libero),
        "libero_mio": _task(app, "T-libero-mio", contatto=c_libero, autore="agent_a"),
        "beta": _task(app, "T-beta", contatto=c_b, agenzia="b"),
    }
    a = {
        "mia": _attivita(app, "A-mia", contatto=c_mio),
        "collega": _attivita(app, "A-collega", contatto=c_collega),
        "libera": _attivita(app, "A-libera", contatto=c_libero),
        "scritta_da_me": _attivita(app, "A-scritta-da-me", contatto=c_collega, autore="agent_a"),
        "beta": _attivita(app, "A-beta", contatto=c_b, agenzia="b"),
    }
    return {"t": t, "a": a, "c": {"mio": c_mio, "collega": c_collega, "libero": c_libero, "b": c_b},
            "l": {"mio": l_mio, "collega_su_mio": l_collega_su_mio}}


def _titoli(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    # il banco e' condiviso fra i test del modulo: ogni test ricrea il suo mondo,
    # quindi si confrontano INSIEMI di titoli (stessi nomi, stesse regole)
    return sorted({x.get("title") or x.get("description") for x in r.json()["items"]})


def test_01_un_agente_legge_solo_i_suoi_task_e_le_sue_attivita(app, mondo):
    aldo = app["login"]("agent_a")
    assert _titoli(aldo, "/api/core/tasks?status=open&limit=200") == sorted(
        ["T-mio-lead", "T-mio-contatto", "T-scritto-da-me", "T-libero-mio"])
    testo = aldo.get("/api/core/tasks?limit=200").text
    for segreto in ("T-collega", "Nota riservata di T-collega", "T-libero\"", "T-beta"):
        assert segreto not in testo, segreto
    # i filtri non aprono nulla: il contatto di Ada, il lead di Ada sul cliente di Aldo
    assert _titoli(aldo, f"/api/core/tasks?contact_id={mondo['c']['collega']}") == ["T-scritto-da-me"]
    assert _titoli(aldo, f"/api/core/tasks?lead_id={mondo['l']['collega_su_mio']}") == []
    assert _titoli(aldo, f"/api/core/tasks?contact_id={mondo['c']['b']}") == []
    assert _titoli(aldo, "/api/core/activities?limit=200&mistakes=false") == sorted(["A-mia", "A-scritta-da-me"])
    # la collega, simmetrica
    ada = app["login"]("agent2_a")
    assert _titoli(ada, "/api/core/tasks?status=open&limit=200") == sorted(
        ["T-collega-lead", "T-collega", "T-collega-contatto", "T-scritto-da-me"])
    assert _titoli(ada, "/api/core/activities?limit=200") == sorted(["A-collega", "A-scritta-da-me"])


def test_02_un_agente_non_scrive_sui_task_di_un_collega(app, mondo):
    aldo = app["login"]("agent_a")
    altrui = mondo["t"]["collega"]
    prima = _q(app, "SELECT status, due_at, updated_at FROM tasks WHERE id = %s", (altrui,))[0]
    for r in (aldo.patch(f"/api/core/tasks/{altrui}", json={"status": "completed"}),
              aldo.patch(f"/api/core/tasks/{altrui}", json={"due_at": "2030-01-01T09:00:00+01:00"}),
              aldo.post(f"/api/core/tasks/{altrui}/mark-mistake", json={"note": "prova"})):
        assert r.status_code == 404, (r.request.method, r.request.url, r.status_code, r.text)
    assert _q(app, "SELECT status, due_at, updated_at FROM tasks WHERE id = %s", (altrui,))[0] == prima
    # il proprio si', e la Scrivania puo' annullare
    mio = mondo["t"]["mio_lead"]
    assert aldo.patch(f"/api/core/tasks/{mio}", json={"status": "completed"}).status_code == 200
    assert aldo.patch(f"/api/core/tasks/{mio}", json={"status": "open"}).status_code == 200
    # un task nuovo su un cliente del collega: rifiutato, nessuna riga scritta
    righe = _q(app, "SELECT count(*) FROM tasks")[0][0]
    r = aldo.post("/api/core/tasks", json={"contact_id": mondo["c"]["collega"], "title": "Intrusione"})
    assert r.status_code in (404, 422), r.text
    assert _q(app, "SELECT count(*) FROM tasks")[0][0] == righe


def test_03_il_responsabile_e_quello_del_server(app, mondo):
    owner = app["login"]("owner_a")
    ids = app["ids"]
    r = owner.get("/api/core/tasks?status=open&limit=200")
    assert r.status_code == 200
    per_titolo = {x["title"]: x for x in r.json()["items"]}
    atteso = {
        "T-mio-lead": ids["agent_a"], "T-mio-contatto": ids["agent_a"],
        "T-collega-lead": ids["agent2_a"],          # il lead vince sul contatto
        "T-collega": ids["agent2_a"],               # il testo «Aldo Alfa» non conta
        "T-collega-contatto": ids["agent2_a"],      # lead senza agente: vale il contatto
        "T-scritto-da-me": ids["agent2_a"],         # l'autore non e' il responsabile
        "T-libero": None, "T-libero-mio": None,     # da assegnare
    }
    assert {k: per_titolo[k]["responsible_agent_id"] for k in atteso} == atteso
    assert per_titolo["T-libero-mio"]["created_by_user_id"] == ids["agent_a"]
    assert "T-beta" not in per_titolo


def test_04_titolare_amministratore_e_superadmin(app, mondo):
    tutti_a = sorted(["T-mio-lead", "T-mio-contatto", "T-collega-lead", "T-collega", "T-collega-contatto",
                      "T-scritto-da-me", "T-libero", "T-libero-mio"])
    for chi in ("owner_a", "admin_a"):
        client = app["login"](chi)
        assert _titoli(client, "/api/core/tasks?status=open&limit=200") == tutti_a, chi
        assert "A-beta" not in client.get("/api/core/activities?limit=200").text, chi
    beta = app["login"]("owner_b")
    assert _titoli(beta, "/api/core/tasks?status=open&limit=200") == ["T-beta"]
    sa = app["login"]("super")
    fuori = sa.get("/api/core/tasks?limit=200")
    assert fuori.status_code in (403, 409), (fuori.status_code, fuori.text)
    assert "T-" not in fuori.text
    assert sa.post(f"/api/platform/agencies/{app['ids']['a']}/enter").status_code == 200
    try:
        assert _titoli(sa, "/api/core/tasks?status=open&limit=200") == tutti_a
        assert "T-beta" not in sa.get("/api/core/tasks?limit=200").text
    finally:
        assert sa.post("/api/platform/agency-context/exit").status_code == 204


# ---------------------------------------------------------------------------
# I lettori con SQL proprio, corretti con la stessa regola (inventario della Scrivania)
# ---------------------------------------------------------------------------

def test_05_richieste_acquirenti_nessun_task_altrui(app, mondo):
    owner, ada, aldo = app["login"]("owner_a"), app["login"]("agent2_a"), app["login"]("agent_a")
    r = owner.post("/api/buy/requests", json={"contact_id": mondo["c"]["collega"], "title": "Ricerca trilocale"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = ada.post(f"/api/buy/requests/{rid}/tasks", json={"title": "Richiamare per la visita di Ada"})
    assert r.status_code == 201, r.text
    # la collega e il titolare vedono il task, il titolo nello storico e il contatore
    assert [t["title"] for t in ada.get(f"/api/buy/requests/{rid}/tasks").json()["items"]] == ["Richiamare per la visita di Ada"]
    assert "Richiamare per la visita di Ada" in owner.get(f"/api/buy/requests/{rid}/workflow").text
    conta = lambda c: next(x["open_tasks_count"] for x in c.get("/api/buy/requests?limit=200").json()["items"] if x["id"] == rid)
    assert conta(owner) == 1
    # Aldo vede la richiesta (le richieste sono dell'agenzia) ma nessuna traccia del task di Ada
    assert aldo.get(f"/api/buy/requests/{rid}/tasks").json()["items"] == []
    flusso = aldo.get(f"/api/buy/requests/{rid}/workflow")
    assert flusso.status_code == 200 and "Richiamare per la visita di Ada" not in flusso.text
    assert conta(aldo) == 0
    # il Cestino della richiesta: bloccato dal task aperto di Ada, senza titolo ne' quantita'
    controllo = aldo.get(f"/api/buy/requests/{rid}/deletion-check")
    assert controllo.status_code == 200, controllo.text
    assert controllo.json()["can_trash"] is False
    assert "Richiamare per la visita di Ada" not in controllo.text and '"count"' not in controllo.text
    assert "Ci sono attività o opportunità seguite da un altro operatore" in controllo.text
    assert "Richiamare per la visita di Ada" in owner.get(f"/api/buy/requests/{rid}/deletion-check").text


def test_06_suggerimenti_solo_dei_propri_clienti(app, mondo):
    ids = app["ids"]
    for lead, contatto, motivo in ((mondo["l"]["mio"], mondo["c"]["mio"], "S-di-Aldo"),
                                   (mondo["l"]["collega_su_mio"], mondo["c"]["mio"], "S-di-Ada"),
                                   (None, mondo["c"]["libero"], "S-senza-agente")):
        _q(app, "INSERT INTO next_best_actions (agency_id, subject_type, subject_id, contact_id, lead_id, action_type, "
                "priority, reason, source_signal) VALUES (%s, 'lead', %s, %s, %s, 'call', 'high', %s, "
                "'next_action_overdue') ON CONFLICT (subject_type, subject_id) DO NOTHING",
           (ids["a"], lead or (10**9 + contatto), contatto, lead, motivo))
    motivi = lambda c: {x["reason"] for x in c.get("/api/next-best-action?limit=50").json()["items"]}
    assert {"S-di-Aldo"} <= motivi(app["login"]("agent_a")) and not ({"S-di-Ada", "S-senza-agente"} & motivi(app["login"]("agent_a")))
    assert {"S-di-Ada"} <= motivi(app["login"]("agent2_a")) and "S-di-Aldo" not in motivi(app["login"]("agent2_a"))
    assert {"S-di-Aldo", "S-di-Ada", "S-senza-agente"} <= motivi(app["login"]("owner_a"))
    assert not ({"S-di-Aldo", "S-di-Ada"} & motivi(app["login"]("owner_b")))


def test_07_il_controllo_di_eliminazione_non_mostra_titoli_altrui(app, mondo):
    aldo = app["login"]("agent_a")
    r = aldo.get(f"/api/core/contacts/{mondo['c']['mio']}/deletion-check")
    assert r.status_code == 200, r.text
    blocco = next(b for b in r.json()["blockers"] if b["code"] == "TASK_OPEN")
    etichette = [i["label"] for i in blocco["items"]]
    assert any(e.startswith("T-mio-lead") for e in etichette) and any(e.startswith("T-mio-contatto") for e in etichette)
    assert not any("T-collega" in e for e in etichette), etichette
    assert etichette[-1].startswith("Ci sono attività o opportunità seguite da un altro operatore"), etichette
    assert not any(ch.isdigit() for ch in etichette[-1]), "nessun numero sui task altrui"
    # il lead della collega sul cliente di Aldo: blocco presente, nessun dettaglio
    lead = next(b for b in r.json()["blockers"] if b["code"] == "LEAD_OPEN")
    assert [i["label"] for i in lead["items"]][-1].startswith("Ci sono attività o opportunità")
    assert f"Lead #{mondo['l']['collega_su_mio']}" not in r.text
    assert f"Lead #{mondo['l']['mio']}" in r.text
    # il titolare li vede tutti, il blocco resta identico per tutti
    tutto = app["login"]("owner_a").get(f"/api/core/contacts/{mondo['c']['mio']}/deletion-check").json()
    assert any("T-collega-lead" in i["label"] for b in tutto["blockers"] if b["code"] == "TASK_OPEN" for i in b["items"])


def test_08_venditori_l_ultima_interazione_e_leggibile(app, mondo):
    from tests.test_blocco_5a_visit_booking_postgres import _property
    pid = _property(app)
    _q(app, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'seller')",
       (pid, mondo["l"]["mio"]))
    _q(app, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner') "
            "ON CONFLICT DO NOTHING", (pid, mondo["c"]["mio"]))
    _q(app, "INSERT INTO activities (agency_id, contact_id, lead_id, property_id, activity_type, direction, description, "
            "occurred_at, created_by_user_id) VALUES (%s, %s, %s, %s, 'note', 'internal', 'Nota riservata di Ada', "
            "NOW(), %s)", (app["ids"]["a"], mondo["c"]["mio"], mondo["l"]["collega_su_mio"], pid, app["ids"]["agent2_a"]))
    def ultima(chi):
        r = app["login"](chi).get("/api/crm/sellers?limit=200")
        assert r.status_code == 200, r.text
        voce = next(x for x in r.json()["items"] if x["lead_id"] == mondo["l"]["mio"])
        return (voce["last_interaction"] or {}).get("note")
    assert ultima("owner_a") == "Nota riservata di Ada"
    assert ultima("agent_a") != "Nota riservata di Ada"


def test_09_lo_storico_protetto_e_generico_per_un_agente(app, mondo):
    """La protezione decide su TUTTO lo storico; l'agente legge una sola voce, senza
    etichette di dettaglio ne' quantita'. Il titolare vede il dettaglio."""
    cid = _contatto(app, "Cliente con storico", "agent_a")
    _attivita(app, "Nota riservata di Ada sullo storico", contatto=cid, autore="agent2_a")
    for n in range(3):
        _attivita(app, f"Nota di Aldo {n}", contatto=cid, autore="agent_a")
    aldo = app["login"]("agent_a")
    r = aldo.get(f"/api/core/contacts/{cid}/deletion-check")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["can_trash"] is False, "la protezione resta: l'agente non puo' eliminare"
    assert corpo["history"] == [{"code": "HISTORY_PRESENT", "label": corpo["history"][0]["label"]}]
    assert "count" not in r.text and "Nota riservata" not in r.text and "Attività o interazioni" not in r.text
    blocco = corpo["blockers"][0]
    assert blocco["code"] == "HISTORY_REQUIRES_ADMIN" and blocco["items"] == corpo["history"]
    # anche il tentativo di eliminazione risponde senza dettagli, e non elimina
    t = aldo.post(f"/api/core/contacts/{cid}/trash", json={"reason_code": "duplicate"})
    assert t.status_code == 403, t.text
    assert "count" not in t.text and "Attività o interazioni" not in t.text
    assert _q(app, "SELECT to_jsonb(c)->>'deleted_at' FROM contacts c WHERE id = %s", (cid,))[0][0] is None
    # il titolare vede il dettaglio con le quantita'
    owner = app["login"]("owner_a").get(f"/api/core/contacts/{cid}/deletion-check").json()
    attivita = next(v for v in owner["history"] if v["code"] == "CONTACT_ACTIVITY")
    assert attivita["count"] == 4
