"""SCRIVANIA VIVA (Fase A) - le regole del benvenuto, del radar, del briefing e dei
consigli, e i confini della pagina, senza database.

  01  saluto nel fuso di Roma (13 e 18 come confini), anche a cavallo dell'ora legale.
  02  frase del giorno: 30 frasi distinte, la prima esatta; stessa frase per tutto il
      giorno di Roma, cambia il giorno dopo.
  03  il nome solo se affidabile (mai la parte di un'email); con nome e cognome, il nome.
  04  radar: ricontatti scaduti, senza prossima azione, acquisizioni ferme (soglia e
      appuntamento futuro), valutazioni dal sito senza agente; a turno, massimo 5.
  05  briefing e consigli: solo conteggi e record, in ordine fisso.
  06  i confini: nessuna lettura vietata (valutazioni, WhatsApp, ricalcolo dei suggerimenti,
      punteggio di intenzione), nessuna scrittura nuova, nessun servizio esterno, nessun
      innerHTML; le funzioni operative di prima restano.
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
VIVA = ASSETS / "scrivania" / "viva-model.js"
FRASI = ASSETS / "scrivania" / "frasi.js"
VIEW = ASSETS / "views" / "oggi.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")


def _node(corpo: str) -> dict:
    codice = (f"import * as m from {json.dumps(VIVA.as_uri())};\n"
              f"import {{ FRASI }} from {json.dumps(FRASI.as_uri())};\nconst out = {{}};\n{corpo}\n"
              "console.log(JSON.stringify(out));")
    r = subprocess.run(["node", "--input-type=module", "-e", codice], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_01_il_saluto_segue_l_ora_di_roma():
    out = _node("""
const at = (iso) => m.greeting(new Date(iso));
out.inverno = [at('2026-11-05T11:59:00Z'), at('2026-11-05T12:00:00Z'), at('2026-11-05T16:59:00Z'), at('2026-11-05T17:00:00Z'),
               at('2026-11-05T23:30:00Z')];
out.estate = [at('2026-07-01T10:59:00Z'), at('2026-07-01T11:00:00Z'), at('2026-07-01T15:59:00Z'), at('2026-07-01T16:00:00Z')];
""")
    # inverno: Roma = UTC+1; estate: UTC+2
    assert out["inverno"] == ["Buongiorno", "Buon pomeriggio", "Buon pomeriggio", "Buonasera", "Buongiorno"]
    assert out["estate"] == ["Buongiorno", "Buon pomeriggio", "Buon pomeriggio", "Buonasera"]


def test_02_la_frase_del_giorno():
    out = _node("""
out.n = FRASI.length; out.distinte = new Set(FRASI).size; out.prima = FRASI[0];
const q = (iso) => m.quoteOfDay(new Date(iso));
out.stessoGiorno = [q('2026-11-04T23:30:00Z'), q('2026-11-05T12:00:00Z'), q('2026-11-05T22:59:00Z')];
out.giornoDopo = q('2026-11-05T23:01:00Z');
out.trenta = Array.from({ length: 30 }, (_, i) => m.quoteOfDay(new Date(Date.UTC(2026, 10, 5 + i, 12))));
""")
    assert out["n"] == 30 and out["distinte"] == 30
    assert out["prima"] == ("È vero che dobbiamo guadagnare, ma se per guadagnare devi essere triste, licenziati. "
                            "Meglio felici che ricchi!")
    # 23:30 UTC del 4 e' gia' il 5 a Roma: stessa frase per tutto il 5; il 6 cambia
    assert len(set(out["stessoGiorno"])) == 1 and out["giornoDopo"] != out["stessoGiorno"][0]
    assert len(set(out["trenta"])) == 30, "in trenta giorni consecutivi tutte e trenta"


def test_03_il_nome_solo_se_affidabile():
    out = _node("""
out.n = ['Olga Alfa', 'Anna Maria Rossi', 'Giorgio', "D'Angelo Rossi", 'giorgio.larasa', 'mario', 'Mario2',
         'info@stima360.it', '', null, 'Luca  Bianchi', 'MARIO ROSSI'].map(m.reliableName);
""")
    assert out["n"] == ["Olga", "Anna Maria Rossi", "Giorgio", "D'Angelo", "", "", "", "", "", "", "Luca", "MARIO"]


def test_04_le_regole_del_radar():
    out = _node("""
const now = new Date('2026-11-05T12:00:00Z');
const giorniFa = (n) => new Date(now.getTime() - n * 86400000).toISOString();
const venditore = (id, extra = {}) => ({ lead_id: id, assigned_agent_id: 7, contact: { id: 100 + id, name: `V${id}`, phone: '333' },
  property: { id: 200 + id, address: 'Via Roma', civic_number: '1', city: 'Teramo' }, next_action: null, last_interaction: null, ...extra });
out.scaduto = m.overdueSellerItem(venditore(1, { next_action: { task_id: 9, title: 'Richiamare', due_at: giorniFa(3), overdue: true, source: 'task' } }), now);
out.scadutoLead = m.overdueSellerItem(venditore(2, { next_action: { task_id: null, title: 'Prossima azione', due_at: giorniFa(0.2), overdue: true, source: 'lead' } }), now);
out.nonScaduto = m.overdueSellerItem(venditore(3, { next_action: { due_at: giorniFa(-1), overdue: false } }), now);
out.senza = m.noActionSellerItem(venditore(4, { last_interaction: { occurred_at: giorniFa(20) } }), now);
out.mai = m.noActionSellerItem(venditore(5), now);
const acq = (id, extra) => ({ id, status: 'valuation_presented', assigned_agent_id: 7, owner_name: `P${id}`, property_id: 50,
  property_address: 'Via Po', property_civic_number: '2', property_city: 'Teramo', last_activity_at: giorniFa(9),
  appointment_start_at: giorniFa(15), appointment_status: 'completed', ...extra });
out.acq = [
  m.idleAcquisitionItem(acq(1, {}), now),
  m.idleAcquisitionItem(acq(2, { last_activity_at: giorniFa(6) }), now),                        // troppo recente
  m.idleAcquisitionItem(acq(3, { status: 'acquired' }), now),                                    // chiusa
  m.idleAcquisitionItem(acq(4, { status: 'appointment_set', appointment_status: 'scheduled',
                                 appointment_start_at: giorniFa(-2) }), now),                    // appuntamento in arrivo
  m.idleAcquisitionItem(acq(5, { status: 'appointment_set', appointment_status: 'scheduled' }), now),  // passato, mai chiuso
].map((x) => x && [x.key, x.reason, x.since, x.href]);
out.val = [
  m.unassignedValuationItem({ id: 1, status: 'open', source: 'public_stima', assigned_agent_id: null, contact_id: 5, created_at: giorniFa(1) }, { display_name: 'Vera' }, now),
  m.unassignedValuationItem({ id: 2, status: 'open', source: 'public_stima', assigned_agent_id: 7, contact_id: 5 }, null, now),
  m.unassignedValuationItem({ id: 3, status: 'open', source: 'property_seller', assigned_agent_id: null, contact_id: 5 }, null, now),
].map((x) => x && [x.title, x.since]);
const f = (k, n) => Array.from({ length: n }, (_, i) => ({ key: `${k}${i}`, sortDays: i }));
const r = m.buildRadar({ overdue: f('o', 3), valuations: f('v', 1), acquisitions: f('a', 2), noAction: f('n', 4) });
out.radar = [r.items.map((x) => x.key), r.total, r.counts];
out.vuoto = m.buildRadar({});
""")
    s = out["scaduto"]
    assert (s["reason"], s["since"], s["href"], s["propertyHref"], s["place"]) == (
        "Ricontatto previsto: «Richiamare»", "in ritardo di 3 giorni", "#/contatti/101", "#/immobili/201", "Via Roma 1, Teramo")
    assert out["scadutoLead"]["reason"] == "Ricontatto previsto e non ancora fatto" and out["scadutoLead"]["since"] == "scaduto oggi"
    assert out["nonScaduto"] is None
    assert (out["senza"]["reason"], out["senza"]["since"]) == ("Nessuna prossima azione definita", "ultimo contatto 20 giorni fa")
    assert out["mai"]["since"] == "nessun contatto registrato"
    assert out["acq"] == [
        ["acquisition:1", "Valutazione presentata: nessun aggiornamento", "fermo da 9 giorni", "#/acquisizioni/1"],
        None, None, None,
        ["acquisition:5", "Appuntamento fissato: nessun aggiornamento", "fermo da 9 giorni", "#/acquisizioni/5"]]
    assert out["val"] == [["Vera", "arrivata ieri"], None, None]
    # a turno fra le famiglie, la situazione piu' vecchia prima, massimo 5
    assert out["radar"][0] == ["o2", "v0", "a1", "n3", "o1"]
    assert out["radar"][1] == 10 and out["radar"][2] == {"overdue": 3, "valuations": 1, "acquisitions": 2, "noAction": 4}
    assert out["vuoto"]["items"] == [] and out["vuoto"]["total"] == 0


def test_05_briefing_e_consigli():
    out = _node("""
const now = new Date('2026-11-05T09:00:00Z');   // 10:00 a Roma
const tl = (v) => new Date(v).toISOString().slice(11, 16);
const ty = (t) => ({ buyer_visit: 'Visita immobile', call: 'Telefonata' })[t] || '';
const agenda = [
  { id: 1, type: 'call', status: 'completed', start_at: '2026-11-05T08:00:00Z', contact_name: 'Ugo' },
  { id: 2, type: 'buyer_visit', status: 'scheduled', start_at: '2026-11-05T10:00:00Z', contact_name: 'Ada', place: 'Teramo' },
  { id: 3, type: 'buyer_visit', status: 'confirmed', start_at: '2026-11-05T15:00:00Z', contact_name: 'Bea' },
];
const radar = { items: [{ title: 'Carla', reason: 'Ricontatto previsto e non ancora fatto', since: 'in ritardo di 5 giorni',
  href: '#/contatti/9', hrefLabel: 'Apri contatto', kind: 'seller_overdue' }], total: 4 };
out.b = m.briefing({ agendaToday: agenda, radar, overdue: 2, today: 1, toVerify: 0, now, typeLabel: ty, timeLabel: tl });
out.vuoto = m.briefing({ now });
out.agenzia = m.briefing({ agendaToday: agenda.slice(0, 1), agency: true, now, typeLabel: ty, timeLabel: tl });
out.c = m.advice({ agendaToday: agenda, radar, now, typeLabel: ty, timeLabel: tl, agendaHref: (a) => `#/agenda/giorno/x/${a.id}`,
  urgentTask: { title: 'Richiamare', reason: 'Scaduto da 2 giorni', href: '' },
  verify: { title: 'Telefonata con Ugo', reason: 'x', href: '#/agenda/1' } });
out.solo = m.advice({ now, verify: { title: 'Telefonata', reason: 'x', href: '#/agenda/1' } });
out.niente = m.advice({ now });
""")
    assert out["b"] == [
        "Oggi hai 3 appuntamenti, di cui 2 visite: il prossimo è alle 10:00, visita immobile con Ada.",
        "Il radar segnala 4 opportunità da seguire: la prima è Carla (ricontatto previsto e non ancora fatto).",
        "Più in basso trovi 2 attività scadute e 1 attività di oggi."]
    assert out["vuoto"] == ["Oggi non hai appuntamenti in agenda.",
                            "Il radar non segnala clienti in attesa: è un buon momento per coltivare nuovi contatti.",
                            "Nessuna attività scaduta e niente da verificare."]
    assert out["agenzia"][0] == "Oggi in agenzia c’è 1 appuntamento: per oggi non ne restano altri."
    assert [c["kind"] for c in out["c"]] == ["appointment", "opportunity", "task"]
    assert out["c"][0]["href"] == "#/agenda/giorno/x/2" and out["c"][1]["href"] == "#/contatti/9"
    assert out["c"][2]["href"] == "" and out["c"][2]["label"] == "Mostra le attività"
    assert [c["kind"] for c in out["solo"]] == ["verify"] and out["niente"] == []


def _codice(p: Path) -> str:
    return re.sub(r"^\s*//.*$", "", p.read_text(encoding="utf-8"), flags=re.M)


def test_06_i_confini_della_pagina():
    vista, modello, frasi = _codice(VIEW), _codice(VIVA), _codice(FRASI)
    for js in (VIEW, VIVA, FRASI):
        assert subprocess.run(["node", "--check", str(js)], capture_output=True).returncode == 0, js
    # letture vietate in questa fase
    for vietato in ("/api/crm/stime", "/api/inbound-messages", "/api/next-best-action/refresh", "/api/seller-intent",
                    "/refresh", "localStorage", "innerHTML", "insertAdjacentHTML"):
        assert vietato not in vista, vietato
    assert "intent" not in modello and ".intent" not in vista, "il punteggio di intenzione di vendita non si usa"
    # nessuna scrittura nuova: le sole PATCH di prima
    assert re.findall(r"api(Post|Put|Delete)\(", vista) == []
    assert sorted(set(re.findall(r"apiPatch\([^,]+,\s*(\{[^}]*\})", vista))) == [
        "{ due_at: quando }", "{ status: 'completed' }", "{ status: prima }"]
    # nessun servizio esterno, nessuna rete nel modello e nelle frasi
    for testo in (modello, frasi):
        for vietato in ("fetch(", "http://", "https://", "apiGet", "document.", "window."):
            assert vietato not in testo, vietato
    # le funzioni operative restano: Chiama, Apri contatto, Completa, Riprogramma, Apri Agenda, Da verificare
    for resta in ("'Chiama'", "'Apri contatto'", "button('Completa'", "button('Riprogramma'", "'Apri Agenda'",
                  "'Da verificare'", "mountAvvisiFirme(firme, superato)", "data-scrivania-toggle-tasks"):
        assert resta in vista, resta
    # le valutazioni senza agente: solo per chi vede l'agenzia
    assert "valuations: vedeAgenzia && !mie" in vista
