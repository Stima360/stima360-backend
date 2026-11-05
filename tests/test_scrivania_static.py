"""SCRIVANIA (#/oggi) - le regole di presentazione e i confini, senza database.

  01  task: scaduto / oggi / dopo nel giorno di ROMA (anche a cavallo della mezzanotte
      UTC), ordine per urgenza, gruppi per cliente, chiusi e senza scadenza esclusi.
  02  testo tecnico: FLOW-R001, «lead #N», system non sono testo dell'agente; il titolo
      automatico diventa leggibile; il dato resta quello del server.
  03  nuovi contatti: lead aperti in fase «Nuovo», senza un task aperto per lo stesso
      lead o cliente.
  04  agenda e verifiche: per giorno di Roma e per orario; annullati, spostati, creati per
      errore e «Occupato» dei colleghi fuori; passati ancora aperti e richieste in «Da
      verificare»; mai i completati.
  05  responsabile, «le mie», telefono.
  06  i confini: nessun innerHTML, nessuna chiamata diretta a /api/appointments fuori da
      agenda-api.js, solo PATCH sui task (stato, scadenza), nessun «Prendi in carico»,
      voce «Scrivania» sulla rotta #/oggi, l'Agenda apre un appuntamento dall'indirizzo.
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
MODEL = ASSETS / "scrivania" / "scrivania-model.js"
VIEW = ASSETS / "views" / "oggi.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")


def _node(corpo: str) -> dict:
    codice = f"import * as m from {json.dumps(MODEL.as_uri())};\nconst out = {{}};\n{corpo}\nconsole.log(JSON.stringify(out));"
    r = subprocess.run(["node", "--input-type=module", "-e", codice], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_01_scadenze_nel_giorno_di_roma_e_ordine():
    out = _node("""
const now = new Date('2026-11-05T22:30:00Z');   // 23:30 a Roma
const T = (id, due, priority = 'normal', status = 'open', extra = {}) => ({ id, due_at: due, priority, status, ...extra });
const tasks = [
  T(1, '2026-11-05T22:45:00Z'),                 // 23:45 a Roma: oggi
  T(2, '2026-11-05T23:15:00Z'),                 // 00:15 del 6 a Roma: domani, anche se in UTC e' il 5
  T(3, '2026-11-01T09:00:00Z', 'low'),
  T(4, '2026-11-04T09:00:00Z', 'urgent'),
  T(5, '2026-11-04T09:00:00Z', 'high', 'in_progress'),
  T(6, '2026-11-03T09:00:00Z', 'urgent', 'completed'),
  T(7, null, 'urgent'),
  T(8, '2026-11-02T09:00:00Z', 'urgent', 'cancelled'),
];
out.stati = tasks.map((t) => m.dueState(t, now));
out.daFare = m.tasksToDo(tasks, now).map((t) => t.id);
out.scaduti = m.overdueTasks(tasks, now).map((t) => t.id);
out.senza = m.tasksWithoutDue(tasks).map((t) => t.id);
const g = m.groupByClient([
  T(10, null, 'normal', 'open', { contact_id: 5 }), T(11, null, 'normal', 'open', { lead_id: 9 }),
  T(12, null, 'normal', 'open', { contact_id: 5 }), T(13, null)]);
out.gruppi = g.map((x) => [x.key, x.tasks.map((t) => t.id)]);
""")
    assert out["stati"] == ["today", "later", "overdue", "overdue", "overdue", "overdue", "none", "overdue"]
    assert out["daFare"] == [4, 5, 3, 1], "scaduti per priorita', poi oggi"
    assert out["scaduti"] == [3, 4, 5]
    assert out["senza"] == [7]
    assert out["gruppi"] == [["c5", [10, 12]], ["l9", [11]], ["t13", [13]]]


def test_02_il_testo_tecnico_non_e_testo_dell_agente():
    out = _node("""
const flow = { title: 'Primo contatto lead', description: 'Task generato da FLOW-R001 per lead #87',
               metadata: { source: 'flow', flow_rule_code: 'FLOW-R001' } };
const mano = { title: 'Richiamare Mario', description: 'Vuole parlare del prezzo', metadata: {} };
out.titoli = [m.friendlyTaskTitle(flow), m.friendlyTaskTitle(mano), m.friendlyTaskTitle({ title: '  ' })];
out.desc = [m.readableDescription(flow), m.readableDescription(mano)];
out.auto = [m.isAutomaticTask(flow), m.isAutomaticTask(mano)];
out.attivita = [
  m.isTechnicalActivity({ activity_type: 'system', description: 'x' }),
  m.isTechnicalActivity({ activity_type: 'note', description: 'Task generato da FLOW-R004 per buy_request #3' }),
  m.isTechnicalActivity({ activity_type: 'call', description: 'Telefonata con Mario' }),
];
out.dato = flow.description;
""")
    assert out["titoli"] == ["Primo contatto con il cliente", "Richiamare Mario", "Attività da svolgere"]
    assert out["desc"] == ["", "Vuole parlare del prezzo"]
    assert out["auto"] == [True, False]
    assert out["attivita"] == [True, True, False]
    assert out["dato"] == "Task generato da FLOW-R001 per lead #87", "il dato non si modifica"


def test_03_nuovi_contatti_senza_doppioni_dei_task():
    out = _node("""
const leads = [
  { id: 1, contact_id: 11, status: 'open', stage: 'new', created_at: '2026-11-01T10:00:00Z' },
  { id: 2, contact_id: 12, status: 'open', stage: 'new', created_at: '2026-11-03T10:00:00Z' },
  { id: 3, contact_id: 13, status: 'open', stage: 'new', created_at: '2026-11-02T10:00:00Z' },
  { id: 4, contact_id: 14, status: 'open', stage: 'contacted' },
  { id: 5, contact_id: 15, status: 'closed', stage: 'new' },
];
const tasks = [
  { id: 90, lead_id: 2, status: 'open' },        // stesso lead
  { id: 91, contact_id: 13, status: 'in_progress' },  // stesso cliente
  { id: 92, contact_id: 11, status: 'completed' },    // chiuso: non conta
];
out.nuovi = m.newContacts(leads, tasks).map((x) => x.lead.id);
out.origini = [m.leadOrigin({ source: 'public_stima' }), m.leadOrigin({ pipeline: 'buy' }), m.leadOrigin({}, { property: {} })];
""")
    assert out["nuovi"] == [1]
    assert out["origini"] == ["Richiesta di valutazione dal sito", "Acquisto", "Proprietario che vende"]


def test_04_agenda_e_verifiche():
    out = _node("""
const now = new Date('2026-11-05T12:00:00Z');
const A = (id, start, mins, status, extra = {}) => ({ kind: 'appointment', id, status, start_at: start,
  end_at: new Date(Date.parse(start) + mins * 60000).toISOString(), ...extra });
const voci = [
  A(1, '2026-11-05T15:00:00Z', 30, 'scheduled'),
  A(2, '2026-11-05T07:00:00Z', 30, 'confirmed'),
  A(3, '2026-11-05T09:00:00Z', 30, 'cancelled'),
  A(4, '2026-11-05T10:00:00Z', 30, 'cancelled', { cancelled_kind: 'mistake' }),
  A(5, '2026-11-05T23:30:00Z', 30, 'scheduled'),   // 00:30 del 6 a Roma
  A(6, '2026-11-06T09:00:00Z', 30, 'completed'),
  A(7, '2026-11-06T10:00:00Z', 30, 'rescheduled'),
  { kind: 'busy', start_at: '2026-11-05T11:00:00Z', end_at: '2026-11-05T12:00:00Z' },
];
out.oggi = m.appointmentsOfDay(voci, '2026-11-05').map((x) => x.id);
out.domani = m.upcomingOfDay(voci, '2026-11-06').map((x) => x.id);
out.stati = [m.effectiveAppointmentState(voci[1], now), m.effectiveAppointmentState(voci[0], now),
             m.effectiveAppointmentState({ status: 'requested', start_at: '2026-11-09T10:00:00Z' }, now)];
const L = (id, start, status, tipo = 'call', extra = {}) => ({ id, status, appointment_type: tipo, start_at: start,
  end_at: new Date(Date.parse(start) + 1800000).toISOString(), ...extra });
const verifica = m.toVerify(
  [L(20, '2026-11-01T09:00:00Z', 'scheduled'), L(21, '2026-11-02T09:00:00Z', 'confirmed', 'buyer_visit'),
   L(22, '2026-11-03T09:00:00Z', 'completed'), L(23, '2026-11-05T11:45:00Z', 'scheduled'),
   L(24, '2026-11-04T09:00:00Z', 'cancelled', 'call', { cancelled_kind: 'mistake' })],
  [L(30, '2026-11-09T09:00:00Z', 'requested'), L(20, '2026-11-01T09:00:00Z', 'scheduled')], now);
out.verifica = verifica.map((v) => [v.appointment.id, v.kind]);
""")
    assert out["oggi"] == [2, 1], "giorno di Roma, per orario, senza annullati, errori, Occupato"
    assert out["domani"] == [5], "domani: niente completati ne' spostati"
    assert out["stati"] == ["to_verify", "scheduled", "to_confirm"]
    assert out["verifica"] == [[20, "past_open"], [21, "visit_no_outcome"], [30, "to_confirm"]]


def test_05_responsabile_le_mie_e_telefono():
    """Il responsabile e' quello calcolato dal server; il testo libero non decide nulla."""
    out = _node("""
const agentsById = new Map([[7, 'Aldo Alfa'], [8, 'Ada Alfa']]);
out.owner = [
  m.taskOwner({ responsible_agent_id: 7, assigned_to: 'Ada Alfa' }, { agentsById }),
  m.taskOwner({ responsible_agent_id: null, assigned_to: 'Aldo Alfa' }, { agentsById }),
  m.taskOwner({ responsible_agent_id: 99 }, { agentsById }),
];
out.nota = [m.taskAssignedNote({ assigned_to: ' Mario ' }), m.taskAssignedNote({ assigned_to: 'system' })];
out.libero = [m.isUnassignedTask({ responsible_agent_id: null }), m.isUnassignedTask({ responsible_agent_id: 7 })];
out.mie = [
  m.isMyTask({ responsible_agent_id: 7 }, { meId: 7 }),
  m.isMyTask({ responsible_agent_id: 8, created_by_user_id: 7 }, { meId: 7 }),   // di un collega: non mio
  m.isMyTask({ responsible_agent_id: null, created_by_user_id: 7 }, { meId: 7 }), // senza responsabile, creato da me
  m.isMyTask({ responsible_agent_id: null, assigned_to: 'Aldo Alfa' }, { meId: 7 }), // il nome non conta
  m.isMyTask({ responsible_agent_id: 7 }, { meId: null }),
];
out.tel = ['+39 333 111 2222', '333-444-5555', '(0861) 12 34 56', 'nessuno', '', '12'].map(m.telHref);
out.nomi = [m.contactName({ first_name: 'Anna', last_name: 'Bianchi' }), m.contactName({ company_name: 'Srl X' }), m.contactName(null)];
""")
    assert out["owner"] == ["Aldo Alfa", "", ""]
    assert out["nota"] == ["Mario", ""]
    assert out["libero"] == [True, False]
    assert out["mie"] == [True, False, True, False, False]
    assert out["tel"] == ["tel:+393331112222", "tel:3334445555", "tel:0861123456", "", "", ""]
    assert out["nomi"] == ["Anna Bianchi", "Srl X", ""]


def _senza_commenti(testo: str) -> str:
    return re.sub(r"^\s*//.*$", "", testo, flags=re.M)


def test_06_i_confini():
    vista = VIEW.read_text(encoding="utf-8")
    codice = _senza_commenti(vista)
    modello = MODEL.read_text(encoding="utf-8")
    for js in (VIEW, MODEL, ASSETS / "views" / "agenda" / "agenda-page.js"):
        assert subprocess.run(["node", "--check", str(js)], capture_output=True).returncode == 0, js
    # DOM con textContent, nessun html dinamico
    assert "innerHTML" not in codice and "insertAdjacentHTML" not in codice
    # gli appuntamenti passano dal client dell'Agenda; nessuna scrittura sugli appuntamenti
    assert "/api/appointments" not in codice
    assert "from '../agenda/agenda-api.js'" in vista
    importati = re.findall(r"import \{([^}]*)\} from '\.\./agenda/agenda-api\.js'", vista)[0]
    assert [x.strip() for x in importati.split(",")] == ["getCalendar", "getList"], "solo letture dell'Agenda"
    # le sole scritture: PATCH di un task, con stato o scadenza
    assert re.findall(r"api(Post|Put|Delete)\(", codice) == []
    corpi = re.findall(r"apiPatch\([^,]+,\s*(\{[^}]*\})", codice)
    assert sorted(set(corpi)) == ["{ due_at: quando }", "{ status: 'completed' }", "{ status: prima }"], corpi
    assert "Prendi in carico" not in codice
    # nessun dato inventato: niente numeri dimostrativi o nomi fissi nella vista
    assert not re.search(r"\b(Mario|Rossi|demo|esempio)\b", codice, re.I)
    # il modello non fa rete e non tocca il DOM
    for vietato in ("fetch(", "apiGet", "apiPatch", "document.", "window."):
        assert vietato not in modello, vietato
    # le letture che i test storici sorvegliano restano
    assert "/api/core/activities?limit=20&mistakes=false" in vista and "/api/next-best-action" in vista
    # la voce si chiama Scrivania, la rotta resta #/oggi; il pulsante «Oggi» dell'Agenda non cambia
    main = (ASSETS / "main.js").read_text(encoding="utf-8")
    assert "{ name: 'oggi', label: 'Scrivania' }" in main and "registerRoute('oggi'," in main
    agenda = (ASSETS / "views" / "agenda" / "agenda-page.js").read_text(encoding="utf-8")
    assert "el('button', 'btn agenda-today-btn', 'Oggi')" in agenda
    # l'Agenda apre un appuntamento o la creazione dall'indirizzo, poi lo ripulisce
    blocco = agenda.split("const richiesta = params[2];", 1)[1].split("\n}", 1)[0]
    assert "sostituisciIndirizzo(view, key)" in blocco and "apriCreazione({ dateKey: key })" in blocco
    assert "apri(Number(richiesta))" in blocco
