"""SCRIVANIA (#/oggi) - collaudo NEL BROWSER, sull'interfaccia vera, con PostgreSQL VERO
(banco 2C, usa-e-getta) e l'app avviata nel processo del test. Dati solo sintetici,
nessun messaggio, nessun servizio esterno.

  01  titolare, «Agenzia»: i tre numeri sono quelli dei record; «Da fare prima» ha i soli
      task aperti scaduti o di oggi, in ordine di urgenza, raggruppati per cliente; il
      testo tecnico FLOW-R001 / lead #N non e' mostrato; «Nuovi contatti» non ripete chi
      ha gia' un task; l'agenda di oggi e domani esclude annullati; «Da verificare» ha
      gli appuntamenti passati ancora aperti e le richieste, mai quelli completati; i
      dati dell'altra agenzia non compaiono; nessun «Prendi in carico».
  02  le azioni scrivono davvero e la pagina si aggiorna: Completa (e Annulla) e
      Riprogramma cambiano la riga nel database; un appuntamento si apre nell'Agenda;
      «Nuovo appuntamento» apre la creazione dell'Agenda; «Le mie attivita'» filtra.
  03  agente: niente selettore, solo i propri appuntamenti e lead, nessun nome o
      collegamento di un cliente che non puo' vedere.
  04  smartphone: nessuno scorrimento orizzontale, azioni a portata di dito.
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

ROMA = ZoneInfo("Europe/Rome")


def _roma(giorno, ora, minuto=0):
    return datetime.combine(giorno, time(ora, minuto), tzinfo=ROMA).astimezone(timezone.utc)


def _contatto(app, nome, telefono, agente=None, agenzia="a"):
    nome_, cognome = nome.split(" ", 1)
    return _q(app, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, assigned_agent_id) "
                   "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
              (app["ids"][agenzia], nome, nome_, cognome, telefono, app["ids"][agente] if agente else None))[0][0]


def _lead(app, contatto, stage, *, agente=None, source=None, pipeline="sell", agenzia="a"):
    return _q(app, "INSERT INTO leads (agency_id, contact_id, source, pipeline, stage, status, assigned_agent_id) "
                   "VALUES (%s, %s, %s, %s, %s, 'open', %s) RETURNING id",
              (app["ids"][agenzia], contatto, source, pipeline, stage,
               app["ids"][agente] if agente else None))[0][0]


def _task(app, titolo, due, *, contatto=None, lead=None, priorita="normal", stato="open", descrizione=None,
          metadata=None, agenzia="a"):
    return _q(app, "INSERT INTO tasks (agency_id, contact_id, lead_id, title, description, priority, status, due_at, "
                   "completed_at, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
              (app["ids"][agenzia], contatto, lead, titolo, descrizione, priorita, stato, due,
               due if stato == "completed" else None, json.dumps(metadata or {})))[0][0]


def _appuntamento(app, client, tipo, inizio, minuti, agente, contatto, stato="scheduled", finale=None):
    """Creato con l'API vera nel futuro, poi portato all'orario (e allo stato) del caso."""
    provvisorio = datetime.now(timezone.utc) + timedelta(days=30, hours=len(app.setdefault("_n", [])) * 2)
    app["_n"].append(1)
    r = client.post("/api/appointments", json={
        "appointment_type": tipo, "status": stato if stato in ("requested", "scheduled", "confirmed") else "scheduled",
        "assigned_user_id": app["ids"][agente], "contact_id": contatto,
        "start_at": provvisorio.isoformat(), "end_at": (provvisorio + timedelta(minutes=minuti)).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    stato_finale = finale or stato
    _q(app, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s, "
            "status = %s, cancelled_at = CASE WHEN %s = 'cancelled' THEN NOW() END, "
            "completed_at = CASE WHEN %s = 'completed' THEN %s END WHERE id = %s",
       (inizio, inizio + timedelta(minutes=minuti), stato_finale, stato_finale, stato_finale,
        inizio + timedelta(minutes=minuti), aid))
    return aid


SCRIPT = r"""
const { chromium, devices } = require('playwright');
const [base, shots, emailOwner, emailAgent, password, t1, t2, oggi, emailAdmin, emailSuper, agenziaA] = process.argv.slice(2);
(async () => {
  const out = { errors: [], owner: {}, agent: {}, mobile: {} };
  const browser = await chromium.launch();
  const pausa = (p, ms) => p.waitForTimeout(ms);
  async function entra(ctx, email) {
    const page = await ctx.newPage();
    page.on('pageerror', (e) => out.errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(`${base}/os/`);
    await page.fill('#login-email', email);
    await page.fill('#login-password', password);
    await page.click('#login-form button[type=submit]');
    await page.waitForSelector('[data-scrivania-kpi]', { timeout: 20000 });
    await pausa(page, 500);
    return page;
  }
  async function leggi(page) {
    return page.evaluate(() => {
      const kpi = {};
      document.querySelectorAll('[data-scrivania-kpi]').forEach((b) => {
        kpi[b.dataset.scrivaniaKpi] = b.querySelector('.scrivania-kpi-value').textContent.trim();
      });
      const testo = (sel) => [...document.querySelectorAll(sel)].map((n) => n.innerText.trim());
      return {
        kpi,
        nav: [...document.querySelectorAll('nav button, .sidebar button')].map((b) => b.textContent.trim()).filter(Boolean),
        tabs: testo('[data-sezione-tab]'),
        title: (document.querySelector('.scrivania-title') || {}).textContent,
        date: (document.querySelector('.scrivania-date') || {}).textContent,
        mode: [...document.querySelectorAll('[data-scrivania-mode]')].map((b) => [b.dataset.scrivaniaMode, b.getAttribute('aria-pressed')]),
        groups: [...document.querySelectorAll('[data-scrivania-group]')].map((g) => ({
          key: g.dataset.scrivaniaGroup,
          client: g.querySelector('.scrivania-client').textContent.trim(),
          tasks: [...g.querySelectorAll('[data-scrivania-task]')].map((t) => t.dataset.scrivaniaTask),
          hrefs: [...g.querySelectorAll('.scrivania-group-head a')].map((a) => [a.textContent.trim(), a.getAttribute('href')]),
        })),
        taskText: testo('[data-scrivania-task]'),
        leads: [...document.querySelectorAll('[data-scrivania-lead]')].map((r) => ({
          id: r.dataset.scrivaniaLead, text: r.innerText,
          hrefs: [...r.querySelectorAll('a')].map((a) => [a.textContent.trim(), a.getAttribute('href')]) })),
        agenda: [...document.querySelectorAll('[data-scrivania-appointment]')].map((a) => ({
          id: a.dataset.scrivaniaAppointment, href: a.getAttribute('href'), text: a.innerText })),
        tomorrow: testo('.scrivania-tomorrow a.scrivania-mini'),
        tomorrowHrefs: [...document.querySelectorAll('.scrivania-tomorrow a.scrivania-mini')].map((a) => a.getAttribute('href')),
        verify: [...document.querySelectorAll('[data-scrivania-verify]')].map((r) => ({
          id: r.dataset.scrivaniaVerify, text: r.innerText })),
        buttons: testo('.scrivania button, .scrivania a.btn'),
        body: document.querySelector('.scrivania').innerText,
        scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth,
      };
    });
  }
  try {
    // ---- 01 titolare, desktop
    const ctxO = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
    let page = await entra(ctxO, emailOwner);
    if (!(await page.$('[data-scrivania-mode="agency"][aria-pressed="true"]'))) {
      await page.click('[data-scrivania-mode="agency"]');
      await pausa(page, 300);
    }
    out.owner.prima = await leggi(page);
    await page.screenshot({ path: `${shots}/scrivania-desktop.png`, fullPage: true });

    // storico, a richiesta, senza operazioni tecniche
    await page.click('[data-scrivania-section="storico"] button');
    await pausa(page, 1200);
    out.owner.storico = await page.$eval('[data-scrivania-section="storico"]', (n) => n.innerText);

    // ---- 02 completa e annulla
    await page.click(`[data-scrivania-complete="${t2}"]`);
    await page.waitForSelector('.scrivania-success', { timeout: 10000 });
    out.owner.completato = { msg: await page.$eval('.scrivania-success', (n) => n.innerText),
      presente: !!(await page.$(`[data-scrivania-task="${t2}"]`)),
      kpi: (await leggi(page)).kpi };
    console.log('PASSO completato');
    await page.waitForTimeout(300);
    out.owner.dbDopoCompleta = 'vedi python';
    await page.evaluate(() => { window.__fermo = true; });
    console.log('ATTENDI completa');
    await new Promise((r) => process.stdin.once('data', r));
    await page.click('[data-scrivania-undo]');
    await page.waitForSelector(`[data-scrivania-task="${t2}"]`, { timeout: 10000 });
    out.owner.riaperto = (await leggi(page)).kpi;
    console.log('ATTENDI annulla');
    await new Promise((r) => process.stdin.once('data', r));

    // riprogramma: fra una settimana alle 9
    await page.click(`[data-scrivania-reschedule="${t1}"]`);
    await page.waitForSelector('dialog.scrivania-dialog[open]', { timeout: 5000 });
    await page.screenshot({ path: `${shots}/scrivania-riprogramma.png` });
    await page.click('dialog.scrivania-dialog[open] .scrivania-quick button:has-text("Fra una settimana")');
    await page.click('dialog.scrivania-dialog[open] button[type=submit]');
    await page.waitForSelector('.scrivania-success:has-text("Nuova scadenza")', { timeout: 10000 });
    out.owner.riprogrammato = { msg: await page.$eval('.scrivania-success', (n) => n.innerText),
      presente: !!(await page.$(`[data-scrivania-task="${t1}"]`)), kpi: (await leggi(page)).kpi };
    console.log('ATTENDI riprogramma');
    await new Promise((r) => process.stdin.once('data', r));

    // le mie attivita': il titolare non ha clienti ne' appuntamenti propri
    await page.click('[data-scrivania-mode="mine"]');
    await pausa(page, 300);
    out.owner.mie = await leggi(page);
    await page.click('[data-scrivania-mode="agency"]');
    await pausa(page, 300);

    // un appuntamento si apre nell'Agenda
    const primo = out.owner.prima.agenda[out.owner.prima.agenda.length - 1];
    await page.click(`[data-scrivania-appointment="${primo.id}"]`);
    await page.waitForSelector('dialog.agenda-drawer[open] .agenda-drawer-body', { timeout: 15000 });
    await pausa(page, 600);
    out.owner.agenda = { url: page.url(), drawer: await page.$eval('dialog.agenda-drawer[open]', (n) => n.innerText) };
    await page.screenshot({ path: `${shots}/scrivania-apre-agenda.png` });
    await page.keyboard.press('Escape');

    // nuovo appuntamento: la creazione dell'Agenda
    await page.goto(`${base}/os/#/oggi`);
    await page.waitForSelector('[data-scrivania-new-appointment]', { timeout: 15000 });
    await pausa(page, 800);
    await page.click('[data-scrivania-new-appointment]');
    await page.waitForSelector('dialog[open] form', { timeout: 15000 });
    await pausa(page, 500);
    out.owner.nuovo = { url: page.url(), dialog: await page.$eval('dialog[open]', (n) => n.innerText.slice(0, 200)) };
    await ctxO.close();

    // ---- 03 agente, amministratore, Superadmin dentro l'agenzia A
    const ctxA = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
    page = await entra(ctxA, emailAgent);
    out.agent = await leggi(page);
    await page.screenshot({ path: `${shots}/scrivania-agente.png`, fullPage: true });
    await ctxA.close();
    const ctxD = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    page = await entra(ctxD, emailAdmin);
    out.admin = await leggi(page);
    await ctxD.close();
    const ctxS = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    page = await ctxS.newPage();
    page.on('pageerror', (e) => out.errors.push(String(e)));
    await page.goto(`${base}/os/`);
    await page.fill('#login-email', emailSuper);
    await page.fill('#login-password', password);
    await page.click('#login-form button[type=submit]');
    await pausa(page, 1500);
    out.superEnter = await page.evaluate(async (a) => (await fetch(`/api/platform/agencies/${a}/enter`,
      { method: 'POST', credentials: 'include' })).status, agenziaA);
    await page.goto(`${base}/os/#/oggi`);
    await page.reload();
    await pausa(page, 1500);
    // al caricamento la Shell porta il Superadmin sulla Rete: si va sulla Scrivania
    await page.evaluate(() => { window.location.hash = '#/oggi'; });
    try {
      await page.waitForSelector('[data-scrivania-kpi]', { timeout: 20000 });
    } catch (e) {
      out.superDebug = { url: page.url(), text: (await page.evaluate(() => document.body.innerText)).slice(0, 1500) };
      await page.screenshot({ path: `${shots}/debug-super.png` });
      throw e;
    }
    await pausa(page, 800);
    out.super = await leggi(page);
    out.superExit = await page.evaluate(async () => (await fetch('/api/platform/agency-context/exit',
      { method: 'POST', credentials: 'include' })).status);
    await ctxS.close();

    // ---- 04 smartphone (titolare)
    const ctxM = await browser.newContext({ ...devices['iPhone 13'] });
    page = await entra(ctxM, emailOwner);
    out.mobile = await leggi(page);
    await page.screenshot({ path: `${shots}/scrivania-mobile.png`, fullPage: true });
    out.mobile.tap = await page.$$eval('.scrivania-task .scrivania-actions .btn', (b) => b.map((x) => Math.round(x.getBoundingClientRect().height)));
    await ctxM.close();
  } catch (e) {
    out.errors.push('SCRIPT: ' + String(e && e.stack || e));
  }
  await browser.close();
  console.log('RISULTATO ' + JSON.stringify(out));
})();
"""


def test_la_scrivania_nel_browser(app, server, tmp_path):
    attrezzi = _node_con_playwright()
    if attrezzi is None:
        pytest.skip("Node o Playwright non disponibili")
    node, env = attrezzi
    ids = app["ids"]
    now = datetime.now(timezone.utc)
    oggi = now.astimezone(ROMA).date()
    domani = oggi + timedelta(days=1)
    fine_oggi = _roma(oggi, 23, 59)
    if fine_oggi - now < timedelta(minutes=40) or now - _roma(oggi, 0, 0) < timedelta(minutes=40):
        pytest.skip("troppo vicino alla mezzanotte di Roma: i casi di «oggi» non sarebbero stabili")

    owner = app["login"]("owner_a")
    pid = _property(app)
    c1 = _contatto(app, "Mario Scaduto", "+39 333 111 2222", "agent_a")
    c2 = _contatto(app, "Lucia Oggi", "333 222 3333")
    c3 = _contatto(app, "Paolo Nuovo", "333-444-5555", "agent_a")
    c4 = _contatto(app, "Sara Doppione", "3335556666")
    cb = _contatto(app, "Bianca Altraagenzia", "3339990000", agenzia="b")
    l1 = _lead(app, c1, "contacted", agente="agent_a")
    l3 = _lead(app, c3, "new", agente="agent_a", source="public_stima", pipeline="general")
    l4 = _lead(app, c4, "new")

    t1 = _task(app, "Primo contatto lead", now - timedelta(days=3), contatto=c1, lead=l1, priorita="high",
               descrizione=f"Task generato da FLOW-R001 per lead #{l1}",
               metadata={"source": "flow", "flow_rule_code": "FLOW-R001"})
    t2 = _task(app, "Richiamare per i documenti", now - timedelta(hours=5), contatto=c1, priorita="urgent",
               metadata={"property_id": pid})
    t3 = _task(app, "Inviare la valutazione", now + (fine_oggi - now) / 2, contatto=c2)
    t4 = _task(app, "Attivita futura", now + timedelta(days=3), contatto=c2, priorita="urgent")
    t5 = _task(app, "Senza scadenza", None, contatto=c2)
    t6 = _task(app, "Gia fatta", now - timedelta(days=1), contatto=c2, stato="completed", priorita="urgent")
    t7 = _task(app, "Sentire Sara", now - timedelta(days=1), contatto=c4)
    tb = _task(app, "Task di Beta", now - timedelta(days=2), contatto=cb, priorita="urgent", agenzia="b")

    a_passato_oggi = _appuntamento(app, owner, "call", _roma(oggi, 0, 5), 15, "agent_a", c1)
    a_sera = _appuntamento(app, owner, "video_call", _roma(oggi, 23, 30), 20, "agent_a", c2, "confirmed")
    a_domani = _appuntamento(app, owner, "seller_meeting", _roma(domani, 10), 60, "agent2_a", c4)
    a_annullato = _appuntamento(app, owner, "call", _roma(domani, 12), 15, "agent_a", c1, finale="cancelled")
    a_vecchio = _appuntamento(app, owner, "call", _roma(oggi - timedelta(days=3), 10), 15, "agent_a", c1)
    a_fatto = _appuntamento(app, owner, "call", _roma(oggi - timedelta(days=2), 10), 15, "agent2_a", c2,
                            finale="completed")
    a_richiesta = _appuntamento(app, owner, "call", _roma(oggi + timedelta(days=5), 10), 15, "agent_a", c3,
                                "requested")
    _q(app, "INSERT INTO activities (agency_id, contact_id, activity_type, direction, description, occurred_at) "
            "VALUES (%s, %s, 'call', 'out', 'Telefonata sintetica con Mario', NOW()), "
            "(%s, %s, 'system', 'internal', 'Task generato da FLOW-R001 per lead #1', NOW())",
       (ids["a"], c1, ids["a"], c1))

    foto = Path(os.environ.get("SCRIVANIA_SHOTS") or tmp_path)
    foto.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "scrivania_browser.js"
    script.write_text(SCRIPT, encoding="utf-8")
    proc = subprocess.Popen([node, str(script), server, str(foto), "b2c.owner_a@example.test",
                             "b2c.agent_a@example.test", PASSWORD, str(t1), str(t2), oggi.isoformat(),
                             "b2c.admin_a@example.test", "b2c.super@example.test", str(ids["a"])],
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stato_db = {}

    def stato(task_id):
        return _q(app, "SELECT status, completed_at IS NOT NULL, due_at FROM tasks WHERE id = %s", (task_id,))[0]

    righe = []
    for riga in proc.stdout:
        righe.append(riga)
        if riga.startswith("ATTENDI "):
            passo = riga.split()[1]
            stato_db[passo] = {"t1": stato(t1), "t2": stato(t2)}
            proc.stdin.write("ok\n")
            proc.stdin.flush()
        if riga.startswith("RISULTATO "):
            break
    proc.stdin.close()   # lo script resta in ascolto sull'ingresso: chiuderlo lo lascia finire
    proc.wait(timeout=60)
    errori = proc.stderr.read()
    (foto / "node-output.txt").write_text("".join(righe) + "\n--- stderr ---\n" + errori, encoding="utf-8")
    riga = next((r for r in righe if r.startswith("RISULTATO ")), None)
    assert riga, ("".join(righe)[-2000:], errori[-2000:])
    out = json.loads(riga[len("RISULTATO "):])
    assert out["errors"] == [], out["errors"]

    # ---- 01 titolare, Agenzia
    p = out["owner"]["prima"]
    assert p["title"] == "La tua scrivania" and p["date"].strip()
    assert "Scrivania" in p["nav"] and "Oggi" not in p["nav"], p["nav"]
    assert p["tabs"][:2] == ["Scrivania", "Attività"], p["tabs"]
    assert p["mode"] == [["mine", "false"], ["agency", "true"]]
    assert p["kpi"] == {"recuperare": "3", "agenda": "2", "nuovi": "1"}, p["kpi"]
    # in ordine di urgenza, raggruppati per cliente, ogni task distinto
    assert [g["tasks"] for g in p["groups"]] == [[str(t2), str(t1)], [str(t7)], [str(t3)]], p["groups"]
    assert [g["client"] for g in p["groups"]] == ["Mario Scaduto", "Sara Doppione", "Lucia Oggi"]
    mario = dict(p["groups"][0]["hrefs"])
    assert mario == {"Chiama": "tel:+393331112222", "Apri contatto": f"#/contatti/{c1}",
                     "Apri immobile": f"#/immobili/{pid}"}, mario
    testo = "\n".join(p["taskText"])
    assert "FLOW-R001" not in p["body"] and f"lead #{l1}" not in p["body"] and "system" not in p["body"]
    assert "Primo contatto con il cliente" in testo and "Creato automaticamente" in testo
    assert "Priorità urgente" in testo and "Scaduto da 3 giorni" in testo
    assert "Responsabile: Aldo Alfa" in testo, "il responsabile viene dal lead o dal contatto (server)"
    # Lucia e Sara non hanno un agente: «Da assegnare», e l'avviso per il titolare
    assert testo.count("Da assegnare") == 2 * 2, testo   # etichetta + riga del responsabile, per due task
    # tutte le aperte senza agente: Lucia (oggi, futura, senza scadenza) e Sara
    assert "4 attività aperte non hanno un responsabile" in p["body"]
    for assente in (str(t4), str(t5), str(t6), str(tb)):
        assert assente not in [t for g in p["groups"] for t in g["tasks"]], assente
    assert "Bianca" not in p["body"] and "Task di Beta" not in p["body"], "nessun dato dell'altra agenzia"
    assert "Un’attività aperta non ha una scadenza" in p["body"]
    assert "Prendi in carico" not in p["buttons"]
    # nuovi contatti: Paolo si', Sara no (ha gia' un task)
    assert [x["id"] for x in p["leads"]] == [str(l3)], p["leads"]
    assert "Paolo Nuovo" in p["leads"][0]["text"] and "Richiesta di valutazione dal sito" in p["leads"][0]["text"]
    assert dict(p["leads"][0]["hrefs"]) == {"Chiama": "tel:3334445555", "Apri contatto": f"#/contatti/{c3}"}
    # agenda di oggi per orario; domani senza l'annullato
    assert [a["id"] for a in p["agenda"]] == [str(a_passato_oggi), str(a_sera)], p["agenda"]
    assert p["agenda"][0]["href"] == f"#/agenda/giorno/{oggi.isoformat()}/{a_passato_oggi}"
    assert "Da verificare" in p["agenda"][0]["text"] and "Confermato" in p["agenda"][1]["text"]
    assert "Agente: Aldo Alfa" in p["agenda"][0]["text"]
    assert p["tomorrowHrefs"] == [f"#/agenda/giorno/{domani.isoformat()}/{a_domani}"], p["tomorrowHrefs"]
    tutti_gli_appuntamenti = [a["id"] for a in p["agenda"]] + [h.rsplit("/", 1)[1] for h in p["tomorrowHrefs"]] \
        + [v["id"] for v in p["verify"]]
    assert str(a_annullato) not in tutti_gli_appuntamenti, "un annullato non e' un impegno"
    # da verificare: il vecchio aperto, quello di stanotte, la richiesta; mai il completato
    verifiche = {v["id"]: v["text"] for v in p["verify"]}
    assert set(verifiche) == {str(a_vecchio), str(a_passato_oggi), str(a_richiesta)}, verifiche
    assert "Appuntamento passato senza esito" in verifiche[str(a_vecchio)]
    assert "Richiesta da confermare" in verifiche[str(a_richiesta)]
    assert str(a_fatto) not in verifiche
    # storico: l'attivita' dell'operatore, non quella tecnica (a richiesta)
    storico = out["owner"]["storico"]
    assert "Telefonata sintetica con Mario" in storico and "FLOW-R001" not in storico
    assert "Mostra anche 1 operazioni automatiche" in storico

    # ---- 02 azioni vere
    assert out["owner"]["completato"]["presente"] is False
    assert out["owner"]["completato"]["kpi"]["recuperare"] == "2"
    assert stato_db["completa"]["t2"][:2] == ("completed", True)
    assert stato_db["annulla"]["t2"][:2] == ("open", False), "Annulla riapre il task"
    assert out["owner"]["riaperto"]["recuperare"] == "3"
    nuova = stato_db["riprogramma"]["t1"][2].astimezone(ROMA)
    assert (nuova.date(), nuova.hour, nuova.minute) == (oggi + timedelta(days=7), 9, 0), nuova
    assert stato_db["riprogramma"]["t1"][0] == "open", "riprogrammare non cambia lo stato"
    assert out["owner"]["riprogrammato"]["presente"] is False and out["owner"]["riprogrammato"]["kpi"]["recuperare"] == "2"
    assert out["owner"]["mie"]["kpi"] == {"recuperare": "0", "agenda": "0", "nuovi": "0"}, out["owner"]["mie"]["kpi"]
    assert out["owner"]["agenda"]["url"].endswith(f"#/agenda/giorno/{oggi.isoformat()}"), out["owner"]["agenda"]["url"]
    assert "Lucia Oggi" in out["owner"]["agenda"]["drawer"]
    assert out["owner"]["nuovo"]["url"].endswith(f"#/agenda/giorno/{oggi.isoformat()}")
    assert "appuntamento" in out["owner"]["nuovo"]["dialog"].lower()

    # ---- 03 agente: niente selettore, SOLO i suoi task (restringimento nel server)
    g = out["agent"]
    assert g["mode"] == []
    assert [x["tasks"] for x in g["groups"]] == [[str(t2)]], g["groups"]   # t1 riprogrammato, t3/t7 non suoi
    assert g["kpi"] == {"recuperare": "1", "agenda": "2", "nuovi": "1"}, g["kpi"]
    testo_agente = "\n".join(g["taskText"])
    for altrui in ("Inviare la valutazione", "Sentire Sara", "Sara Doppione", "Da assegnare"):
        assert altrui not in testo_agente, altrui
    assert "attività aperte non hanno un responsabile" not in g["body"]
    assert [a["id"] for a in g["agenda"]] == [str(a_passato_oggi), str(a_sera)]
    assert g["tomorrowHrefs"] == [], "l'appuntamento di domani e' di un collega"
    assert [x["id"] for x in g["leads"]] == [str(l3)]
    assert {v["id"] for v in g["verify"]} == {str(a_vecchio), str(a_passato_oggi), str(a_richiesta)}
    # amministratore: come il titolare
    d = out["admin"]
    assert d["mode"] == [["mine", "false"], ["agency", "true"]]
    assert [x["tasks"] for x in d["groups"]] == [[str(t2)], [str(t7)], [str(t3)]], d["groups"]
    # Superadmin entrato nell'agenzia A: tutta A, niente di B; poi esce
    assert out["superEnter"] == 200 and out["superExit"] == 204
    s_ = out["super"]
    assert [x["tasks"] for x in s_["groups"]] == [[str(t2)], [str(t7)], [str(t3)]], s_["groups"]
    assert "Bianca" not in s_["body"] and "Task di Beta" not in s_["body"]

    # ---- 04 smartphone
    m = out["mobile"]
    assert m["scrollW"] <= m["clientW"], (m["scrollW"], m["clientW"])
    assert m["kpi"]["recuperare"] == "2"
    assert m["tap"] and min(m["tap"]) >= 40, m["tap"]
