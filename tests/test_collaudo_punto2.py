"""Lo strumento di collaudo del Punto 2 (`scripts/collaudo_punto2.py`), senza
database: cio' che deve valere PRIMA di toccare qualsiasi cosa, la pianificazione
degli orari sulle regole VERE della policy, la valutazione delle prove
(concluse / in attesa / fallite) e il comando unico `avvia`. La prova contro un
database e' quella del runbook (replica locale del TEST) e, in vivo, il Job."""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import collaudo_punto2 as cp  # noqa: E402

SCRIPT = ROOT / "scripts" / "collaudo_punto2.py"
ROMA = ZoneInfo("Europe/Rome")


def R(*a):
    return datetime(*a, tzinfo=ROMA)


def _riga(reason, status, **kw):
    base = {"id": 1, "reason_code": reason, "status": status, "suppressed_reason": None, "template_key": "x",
            "subject_snapshot": "s", "attempt_count": 0, "appointment_id": 10, "variant": None, "offset": None,
            "appointment_status": "scheduled"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# sicurezza: solo TEST, solo info@stima360.it, nessuna spedizione, nessun segreto
# ---------------------------------------------------------------------------

def test_ambiente_sbagliato_si_ferma_prima_di_connettersi():
    for modo in ("avvia", "preflight", "setup", "report", "cleanup"):
        for host, nome in (("INCOLLA_QUI_L_HOST", "stima360_db_test"), ("h", "stima360_db"), ("altro", "stima360_db_test")):
            env = {"DB_HOST": "h", "DB_NAME": nome, "PATH": "/usr/bin:/bin"}
            out = subprocess.run([sys.executable, str(SCRIPT), modo, "--expected-host", host],
                                 env=env, capture_output=True, text=True)
            assert out.returncode == 1 and "nessuna connessione aperta" in out.stdout, (modo, host, nome, out.stdout, out.stderr)


def test_il_destinatario_e_uno_solo_ed_e_scritto_nello_script():
    assert cp.DESTINATARIO == "info@stima360.it"
    codice = SCRIPT.read_text(encoding="utf-8")
    assert "--recipient" not in codice                       # non si passa da riga di comando
    assert codice.count("info@stima360.it") == 3             # intestazione (comando unico), costante, commento
    assert cp.FUORI_ELENCO.endswith("@example.invalid")      # dominio riservato: non consegnabile


def test_nessuna_email_intera_nell_output():
    assert cp.mascherata("info@stima360.it") == "i***@stima360.it"
    assert cp.mascherata("") == "***"
    codice = SCRIPT.read_text(encoding="utf-8")
    assert "mascherata(DESTINATARIO)" in codice


def test_lo_strumento_non_spedisce_e_scrive_solo_dai_service():
    codice = SCRIPT.read_text(encoding="utf-8")
    for vietato in ("dispatch_batch", "commit(", "claim_", "smtplib.SMTP(", ".sendmail", ".starttls", ".login(",
                    "set_clock", "freezegun", "monkeypatch"):
        assert vietato not in codice, vietato
    assert not re.search(r"\b(INSERT INTO|UPDATE\s+\w+\s+SET|DELETE FROM)\b", codice), "nessuna scrittura SQL diretta"
    assert "set_session(readonly=True)" in codice or "set_session(readonly=readonly)" in codice
    # `invia_mail` compare UNA volta, solo nel sondaggio con `smtplib.SMTP` sostituito
    assert codice.count("invia_mail(") == 1
    sondaggio = codice[codice.index("def smtp_effective"):codice.index("def smtp_gate")]
    assert sondaggio.index('mock.patch.object(smtplib, "SMTP"') < sondaggio.index("invia_mail(")
    for atteso in ("service.create_appointment_idempotent", "service.confirm_appointment", "service.reschedule_appointment",
                   "service.cancel_appointment", "do_not_contact.set_ban", "do_not_contact.lift_ban"):
        assert atteso in codice, atteso
    # non cambia l'orologio, il cron ne' le regole: legge solo la policy
    assert "policy.send_decision" in codice and "COMMUNICATION_CRON" not in codice.replace("COMMUNICATION_CRON_AGENCY_IDS", "")


def test_la_configurazione_ammette_solo_info_stima360_it(monkeypatch):
    from appointment_reminders import policy
    for nome in ("APPOINTMENT_CONFIRMATIONS", "APPOINTMENT_REMINDER_OFFSETS", "APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS",
                 "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(nome, raising=False)
    g = cp.Gate()
    cp.config_gate(g)
    assert any("conferme accese" in p for p in g.problems) and any("elenco esplicito" in p for p in g.problems)
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    monkeypatch.setenv("SMTP_USER", "utente-di-prova")
    monkeypatch.setenv("SMTP_PASS", "segreto-di-prova-0123")
    for elenco, ok in (("*", False), ("", False), ("altro@example.test", False),
                       ("info@stima360.it, altro@example.test", False), ("info@stima360.it", True),
                       ("INFO@Stima360.it", True)):
        monkeypatch.setenv(policy.RECIPIENTS_ENV, elenco)
        g = cp.Gate()
        cp.config_gate(g)
        assert (g.problems == []) is ok, (elenco, g.problems)


# ---------------------------------------------------------------------------
# la prossima fascia utile (08:00-20:00 di Roma)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ora,immediata,fascia", [
    (R(2026, 11, 2, 7, 30), False, R(2026, 11, 2, 8, 0)),         # prima dell'apertura: oggi alle 8
    (R(2026, 11, 2, 8, 0), True, R(2026, 11, 2, 8, 0)),
    (R(2026, 11, 2, 14, 10), True, R(2026, 11, 2, 14, 10)),       # a meta' fascia: adesso
    (R(2026, 11, 2, 19, 0), True, R(2026, 11, 2, 19, 0)),         # "alle 19": 60 minuti davanti
    (R(2026, 11, 2, 19, 29), True, R(2026, 11, 2, 19, 29)),
    (R(2026, 11, 2, 19, 31), False, R(2026, 11, 3, 8, 0)),        # troppo tardi: il cron non fa in tempo
    (R(2026, 11, 2, 20, 0), False, R(2026, 11, 3, 8, 0)),         # "alle 20": fascia chiusa, domani alle 8
    (R(2026, 11, 2, 23, 59), False, R(2026, 11, 3, 8, 0)),
])
def test_prossima_fascia(ora, immediata, fascia):
    w, imm = cp.prossima_fascia(ora.astimezone(timezone.utc))
    assert imm is immediata and w == fascia


# ---------------------------------------------------------------------------
# gli orari di A e B: simulati sulle regole vere della policy
# ---------------------------------------------------------------------------

def _prova_piano(ora):
    now = ora.astimezone(timezone.utc)
    piano = cp.programma(now)
    creato = now.replace(second=0, microsecond=0) + timedelta(minutes=2)
    prev_a = cp.previsti(piano["A"], creato)
    prev_b = cp.previsti(piano["B"], creato)
    return piano, prev_a, prev_b


@pytest.mark.parametrize("ora", [R(2026, 11, 2, 19, 0), R(2026, 11, 2, 19, 45), R(2026, 11, 2, 20, 0),
                                 R(2026, 11, 2, 20, 45), R(2026, 11, 2, 7, 30), R(2026, 11, 2, 12, 5),
                                 R(2026, 11, 2, 23, 50), R(2026, 10, 24, 20, 0), R(2026, 3, 28, 19, 0)])
def test_il_piano_da_a_a_il_2h_e_a_b_il_24h_al_primo_giro_utile(ora):
    piano, prev_a, prev_b = _prova_piano(ora)
    w = piano["fascia"]
    assert "2h" in prev_a and "24h" in prev_b
    for prev, scopo in ((prev_a, "2h"), (prev_b, "24h")):
        primo, ultimo = prev[scopo]
        assert w <= primo <= w + cp.PASSO                    # parte al primo giro utile
        assert ultimo >= primo + cp.TOLLERANZA_CRON          # regge un cron in ritardo
    # e quando la fascia e' chiusa NON parte nulla prima dell'apertura
    if not piano["immediata"]:
        assert all(t[0] >= w for t in prev_a.values() if t[0] >= w) and w.astimezone(ROMA).hour == 8
    # l'appuntamento e' nel futuro rispetto alla creazione e sul passo di 5 minuti
    assert piano["A"] > ora and piano["B"] > ora and piano["A"].minute % 5 == 0 and piano["B"].minute % 5 == 0


def test_nessun_promemoria_dentro_la_fascia_di_chiusura_se_non_e_previsto_dalla_policy():
    """Alle 20:00 la fascia e' chiusa: l'unico invio utile e' domattina, e per A
    e B la policy prevede esattamente uno scopo (niente email in piu')."""
    piano, prev_a, prev_b = _prova_piano(R(2026, 11, 2, 20, 0))
    assert set(prev_a) == {"2h"} and set(prev_b) == {"24h"}
    assert piano["fascia"] == R(2026, 11, 3, 8, 0).astimezone(timezone.utc)


def test_i_promemoria_previsti_dipendono_solo_dai_dati_dell_appuntamento():
    """Rilanciando `avvia` le prove si ricalcolano uguali: stesse date, stesso esito."""
    inizio, creato = R(2026, 11, 3, 9, 55), R(2026, 11, 2, 20, 2)
    assert cp.previsti(inizio, creato) == cp.previsti(inizio, creato)
    primo, ultimo = cp.previsti(inizio, creato)["2h"]
    # la griglia dei giri parte dalla creazione (20:02) a passi di 5 minuti
    assert R(2026, 11, 3, 8, 0) <= primo <= R(2026, 11, 3, 8, 5)
    assert R(2026, 11, 3, 8, 50) <= ultimo <= R(2026, 11, 3, 8, 55)               # l'ultimo utile e' inizio - 1h


# ---------------------------------------------------------------------------
# la valutazione: prove concluse / in attesa / fallite
# ---------------------------------------------------------------------------

def _dati(ora, *, a_conf="sent", a_prom=(), b_conf="sent", b_prom=(), cancel_a=False):
    """Il collaudo creato alle 20:00 (fascia chiusa): A e B per domattina."""
    piano, _, _ = _prova_piano(ora)
    creato = ora.astimezone(timezone.utc) + timedelta(minutes=2)

    def app(i, inizio):
        return {"id": i, "version": 1, "status": "cancelled" if cancel_a else "scheduled", "start_at": inizio, "created_at": creato}

    return {
        "A": {"contact_id": 1, "appuntamenti": [app(10, piano["A"])],
              "righe": [_riga(cp.CONFERMA, a_conf, id=1)] + [_riga(cp.PROMEMORIA, st, id=2 + i, offset=o) for i, (o, st) in enumerate(a_prom)]},
        "B": {"contact_id": 2, "appuntamenti": [app(11, piano["B"])],
              "righe": [_riga(cp.CONFERMA, b_conf, id=5)] + [_riga(cp.PROMEMORIA, st, id=6 + i, offset=o) for i, (o, st) in enumerate(b_prom)]},
        "C": {"contact_id": 3, "appuntamenti": [],
              "righe": [_riga(cp.CONFERMA, "cancelled", id=7, appointment_status="rescheduled"),
                        _riga(cp.CONFERMA, "sent", id=8, variant="rescheduled", appointment_id=11)]},
        "D": {"contact_id": 4, "appuntamenti": [], "righe": [_riga(cp.CONFERMA, "cancelled", id=9, appointment_status="cancelled")]},
        "E": {"contact_id": 5, "appuntamenti": [], "righe": [_riga(cp.CONFERMA, "suppressed", id=10, suppressed_reason="do_not_contact")]},
        "F": {"contact_id": 6, "appuntamenti": [], "righe": []},
    }


def _per_stato(prove):
    r = {cp.CONCLUSA: [], cp.ATTESA: [], cp.FALLITA: []}
    for p in prove:
        r[p.stato].append(p.id)
    return r


def test_alle_20_le_conferme_sono_concluse_e_i_promemoria_sono_in_attesa_con_il_limite():
    ora = R(2026, 11, 2, 20, 5)
    prove = cp.valuta(_dati(R(2026, 11, 2, 20, 0)), ora.astimezone(timezone.utc))
    per = _per_stato(prove)
    assert per[cp.FALLITA] == []
    assert set(per[cp.CONCLUSA]) == {"A1", "B1", "C1", "C2", "D1", "E1", "F1"}
    assert set(per[cp.ATTESA]) == {"A-2h", "B-24h"}
    nota = {p.id: p.nota for p in prove}["A-2h"]
    assert re.search(r"limite 03/11/2026 08:5[0-5]", nota) and re.search(r"dal giro del cron delle 03/11/2026 08:0[0-5]", nota)
    # di notte la fascia e' chiusa: nessuna attesa "breve" (non vale la pena aspettare il cron)
    assert not any(p.breve for p in prove if p.stato == cp.ATTESA)


def test_il_giorno_dopo_con_i_promemoria_inviati_tutto_e_concluso():
    ora = R(2026, 11, 3, 8, 30)
    prove = cp.valuta(_dati(R(2026, 11, 2, 20, 0), a_prom=[("2h", "sent")], b_prom=[("24h", "sent")]), ora.astimezone(timezone.utc))
    per = _per_stato(prove)
    assert per[cp.FALLITA] == [] and per[cp.ATTESA] == [] and len(per[cp.CONCLUSA]) == 9


def test_in_fascia_i_promemoria_attesi_sono_brevi_da_aspettare():
    ora = R(2026, 11, 3, 8, 3)
    prove = cp.valuta(_dati(R(2026, 11, 2, 20, 0)), ora.astimezone(timezone.utc))
    assert [p.id for p in prove if p.stato == cp.ATTESA and p.breve] == ["A-2h", "B-24h"]


def test_limite_scaduto_senza_invio_e_un_fallimento_non_un_attesa():
    ora = R(2026, 11, 3, 9, 30)                                    # oltre le 08:55: il 2h di A non e' piu' possibile
    prove = cp.valuta(_dati(R(2026, 11, 2, 20, 0)), ora.astimezone(timezone.utc))
    p = {x.id: x for x in prove}["A-2h"]
    assert p.stato == cp.FALLITA and "il cron non e' passato in tempo" in p.nota


def test_messaggio_non_previsto_o_scartato_o_doppio_e_un_fallimento():
    ora = R(2026, 11, 3, 8, 30).astimezone(timezone.utc)
    base = R(2026, 11, 2, 20, 0)
    per = _per_stato(cp.valuta(_dati(base, a_prom=[("2h", "sent"), ("24h", "queued")]), ora))
    assert "A-24h" in per[cp.FALLITA]                              # il 24h di A non e' previsto
    per = _per_stato(cp.valuta(_dati(base, a_prom=[("2h", "suppressed")]), ora))
    assert "A-2h" in per[cp.FALLITA]
    dati = _dati(base)
    dati["A"]["righe"].append(_riga(cp.CONFERMA, "sent", id=99))   # due conferme: il doppio clic ha prodotto un duplicato
    assert "A1" in _per_stato(cp.valuta(dati, ora))[cp.FALLITA]
    dati = _dati(base)
    dati["C"]["righe"][0]["status"] = "sent"                       # il vecchio orario e' uscito
    assert "C1" in _per_stato(cp.valuta(dati, ora))[cp.FALLITA]
    dati = _dati(base)
    dati["D"]["righe"][0]["status"] = "sent"
    assert "D1" in _per_stato(cp.valuta(dati, ora))[cp.FALLITA]
    dati = _dati(base)
    dati["E"]["righe"][0].update(status="sent", suppressed_reason=None)
    assert "E1" in _per_stato(cp.valuta(dati, ora))[cp.FALLITA]
    dati = _dati(base)
    dati["F"]["righe"].append(_riga(cp.CONFERMA, "queued", id=50))
    assert "F1" in _per_stato(cp.valuta(dati, ora))[cp.FALLITA]


def test_conferme_ancora_in_coda_sono_in_attesa_breve():
    ora = R(2026, 11, 2, 20, 5).astimezone(timezone.utc)
    dati = _dati(R(2026, 11, 2, 20, 0), a_conf="queued", b_conf="queued")
    dati["C"]["righe"][1]["status"] = "queued"
    dati["E"]["righe"][0].update(status="queued", suppressed_reason=None)
    prove = {p.id: p for p in cp.valuta(dati, ora)}
    for sigla in ("A1", "B1", "C2", "E1"):
        assert prove[sigla].stato == cp.ATTESA and prove[sigla].breve, sigla
    assert prove["C1"].stato == cp.CONCLUSA and prove["D1"].stato == cp.CONCLUSA and prove["F1"].stato == cp.CONCLUSA


def test_appuntamento_annullato_dalla_pulizia_lascia_la_prova_non_conclusa():
    ora = R(2026, 11, 3, 8, 30).astimezone(timezone.utc)
    p = {x.id: x for x in cp.valuta(_dati(R(2026, 11, 2, 20, 0), cancel_a=True), ora)}
    assert p["A-2h"].stato == cp.FALLITA and "prova non conclusa" in p["A-2h"].nota


# ---------------------------------------------------------------------------
# il comando unico `avvia`
# ---------------------------------------------------------------------------

class _Conn:
    def cursor(self):
        return object()

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def avvia(monkeypatch):
    """`avvia` con DB, setup, pulizia, tempo e report finti: si osserva la SEQUENZA."""
    chiamate = []
    stato = {"dati": {}, "prove": [], "aperto": False, "sequenza": []}
    monkeypatch.setattr(cp, "environment_gate", lambda g, host: chiamate.append("ambiente"))
    monkeypatch.setattr(cp, "risolvi_ids", lambda a: setattr(a, "agency_id", 2))
    monkeypatch.setattr(cp, "_connect", lambda readonly=True: _Conn())
    monkeypatch.setattr(cp, "config_gate", lambda g: chiamate.append("config"))
    monkeypatch.setattr(cp, "leggi", lambda cur, ag, run: ("r1", stato["dati"]))
    monkeypatch.setattr(cp, "run_aperto", lambda dati: stato["aperto"])
    monkeypatch.setattr(cp, "valuta", lambda dati, ora: stato["sequenza"].pop(0) if len(stato["sequenza"]) > 1 else stato["sequenza"][0])
    monkeypatch.setattr(cp, "report", lambda cur, a, ora: ({}, stato["sequenza"][0], (
        sum(p.stato == cp.CONCLUSA for p in stato["sequenza"][0]), sum(p.stato == cp.ATTESA for p in stato["sequenza"][0]),
        sum(p.stato == cp.FALLITA for p in stato["sequenza"][0]))))
    monkeypatch.setattr(cp, "cmd_setup", lambda a: chiamate.append("setup"))
    monkeypatch.setattr(cp, "cmd_cleanup", lambda a: chiamate.append("cleanup") or 0)
    monkeypatch.setattr(cp.time, "sleep", lambda s: chiamate.append("attesa"))

    class A:
        expected_host, agency_id, actor_user_id, agent_user_id, property_id, run_id = "h", None, None, None, None, None
        attesa_minuti = 20
    return A, chiamate, stato


def _p(stato, breve=False):
    return cp.Prova("X", "x", stato, breve=breve)


def test_avvia_senza_collaudo_aperto_lo_crea_e_se_tutto_e_concluso_pulisce(avvia):
    A, chiamate, stato = avvia
    stato["sequenza"] = [[_p(cp.CONCLUSA), _p(cp.CONCLUSA)]]
    assert cp.cmd_avvia(A()) == 0
    assert chiamate == ["ambiente", "setup", "cleanup"]


def test_avvia_con_prove_in_attesa_non_pulisce_e_non_ricrea(avvia):
    A, chiamate, stato = avvia
    stato["aperto"] = True
    stato["dati"] = {"A": {"righe": [], "appuntamenti": []}}
    stato["sequenza"] = [[_p(cp.CONCLUSA), _p(cp.ATTESA, breve=False)]]
    assert cp.cmd_avvia(A()) == 0
    assert chiamate == ["ambiente", "config"]                     # riprende: nessun setup, nessuna pulizia


def test_avvia_con_una_prova_fallita_esce_1_e_lascia_i_dati(avvia):
    A, chiamate, stato = avvia
    stato["sequenza"] = [[_p(cp.CONCLUSA), _p(cp.FALLITA), _p(cp.ATTESA)]]
    assert cp.cmd_avvia(A()) == 1
    assert "cleanup" not in chiamate and "setup" in chiamate


def test_avvia_aspetta_il_cron_per_le_prove_brevi_poi_conclude(avvia):
    A, chiamate, stato = avvia
    stato["sequenza"] = [[_p(cp.ATTESA, breve=True)], [_p(cp.ATTESA, breve=True)], [_p(cp.CONCLUSA)]]
    assert cp.cmd_avvia(A()) == 0
    assert chiamate.count("attesa") == 2 and chiamate[-1] == "cleanup"


def test_avvia_non_aspetta_all_infinito(avvia, monkeypatch):
    A, chiamate, stato = avvia
    stato["sequenza"] = [[_p(cp.ATTESA, breve=True)]]
    a = A()
    a.attesa_minuti = 0
    assert cp.cmd_avvia(a) == 0
    assert "attesa" not in chiamate and "cleanup" not in chiamate


def test_ids_ambigui_si_fermano_con_i_candidati(monkeypatch, capsys):
    class Cur:
        def execute(self, *a):
            pass

        def fetchall(self):
            return [{"id": 1, "role": "agency_owner"}, {"id": 2, "role": "agency_owner"}]

    class C(_Conn):
        def cursor(self):
            return Cur()
    monkeypatch.setattr(cp, "_connect", lambda readonly=True: C())
    from communication import cron_router
    monkeypatch.setattr(cron_router, "agenzie_consentite", lambda: frozenset({7}))

    class A:
        agency_id = actor_user_id = agent_user_id = None
    with pytest.raises(SystemExit) as e:
        cp.risolvi_ids(A())
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "attore non univoco" in out and "1=agency_owner" in out and "2=agency_owner" in out


# ---------------------------------------------------------------------------
# SMTP: la configurazione EFFETTIVA, con le regole del trasporto
# ---------------------------------------------------------------------------

SEGRETO = "segreto-di-prova-0123"
UTENTE = "utente-di-prova"


def _smtp_env(monkeypatch, *, host=None, port=None, user=UTENTE, password=SEGRETO):
    for nome, valore in (("SMTP_HOST", host), ("SMTP_PORT", port), ("SMTP_USER", user), ("SMTP_PASS", password)):
        if valore is None:
            monkeypatch.delenv(nome, raising=False)
        else:
            monkeypatch.setenv(nome, valore)


def _nessuna_rete(monkeypatch):
    """Qualunque tentativo di aprire un socket durante la prova fa fallire il test."""
    import socket

    def vietato(*a, **k):
        raise AssertionError("la sonda ha provato ad aprire la rete")
    monkeypatch.setattr(socket, "create_connection", vietato)
    monkeypatch.setattr(socket.socket, "connect", vietato)


def test_smtp_host_assente_con_default_del_trasporto_e_valido(monkeypatch, capsys):
    """Il caso segnalato: SMTP_HOST non e' nell'ambiente, ma `invia_mail` ha un
    default valido: la configurazione e' buona, e il default NON e' duplicato qui."""
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch)                                   # niente SMTP_HOST, niente SMTP_PORT
    eff = cp.smtp_effective()
    assert eff["configurata"] is True and eff["errore"] is None
    assert eff["host_da"] == "default del trasporto"
    assert isinstance(eff["host"], str) and eff["host"] and isinstance(eff["port"], int) and eff["port"] > 0
    g = cp.Gate()
    cp.smtp_gate(g)
    assert g.problems == []
    out = capsys.readouterr().out
    assert "default del trasporto" in out and f"{eff['host']}:{eff['port']}" in out
    # nel sorgente dello script non c'e' una copia del default del trasporto
    assert eff["host"] not in SCRIPT.read_text(encoding="utf-8")


def test_smtp_il_default_letto_e_proprio_quello_del_trasporto(monkeypatch):
    """Se il trasporto cambiasse il suo default, la sonda lo seguirebbe: lo si
    prova sostituendo `invia_mail` con una versione che usa un altro default."""
    import database
    sorgente = __import__("inspect").getsource(database.invia_mail)
    import re
    m = re.search(r'os\.getenv\("SMTP_HOST",\s*"([^"]+)"\)', sorgente)
    assert m, "il trasporto non ha piu' un default letterale per SMTP_HOST: la sonda resta valida, aggiornare questo test"
    _smtp_env(monkeypatch)
    assert cp.smtp_effective()["host"] == m.group(1)


def test_smtp_host_da_variabile_e_riportato_come_tale(monkeypatch):
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, host="smtp.prova.invalid", port="2525")
    eff = cp.smtp_effective()
    assert (eff["configurata"], eff["host"], eff["port"], eff["host_da"]) == (
        True, "smtp.prova.invalid", 2525, "variabile SMTP_HOST")


def test_smtp_password_o_utente_assenti_non_e_configurato(monkeypatch):
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, password=None)
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["password_presente"] is False and eff["utente_presente"] is True
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("SMTP_PASS presente" in p for p in g.problems)
    assert any("trasporto SMTP risulta configurato" in p for p in g.problems)
    _smtp_env(monkeypatch, user=None)
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["utente_presente"] is False and eff["password_presente"] is True
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("SMTP_USER presente" in p for p in g.problems)
    # presenti ma vuoti: non sono presenti
    _smtp_env(monkeypatch, user="", password="")
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["utente_presente"] is False and eff["password_presente"] is False


def test_smtp_host_vuoto_per_il_trasporto_non_e_configurato(monkeypatch):
    """`SMTP_HOST=""` non attiva il default (e' presente): lo decide il trasporto, e la sonda lo segue."""
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, host="")
    eff = cp.smtp_effective()
    assert eff["configurata"] is False
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("trasporto SMTP risulta configurato" in p for p in g.problems)


def test_smtp_porta_non_numerica_e_un_problema_non_un_crash(monkeypatch):
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, port="non-un-numero")
    eff = cp.smtp_effective()
    assert eff["configurata"] is False and eff["errore"] == "ValueError"
    g = cp.Gate()
    cp.smtp_gate(g)
    assert any("non e' utilizzabile" in p and "ValueError" in p for p in g.problems)


def test_smtp_nessun_segreto_in_output_nessuna_email_nessuna_rete(monkeypatch, capsys):
    import smtplib
    _nessuna_rete(monkeypatch)
    _smtp_env(monkeypatch, host="smtp.prova.invalid")
    chiamate = []
    originale = smtplib.SMTP
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: chiamate.append(a) or originale.__new__(originale))
    sostituto = smtplib.SMTP
    eff = cp.smtp_effective()
    # la prova ha sostituito SMTP solo per la sua durata e lo ha ripristinato
    assert smtplib.SMTP is sostituto and chiamate == []
    g = cp.Gate()
    cp.smtp_gate(g)
    visibile = capsys.readouterr().out + repr(eff)
    for riservato in (SEGRETO, UTENTE, "sonda@example.invalid"):
        assert riservato not in visibile, riservato
    assert eff["password_presente"] is True and "password" not in " ".join(k for k, v in eff.items() if isinstance(v, str))


def test_config_gate_con_host_assente_e_default_valido_non_da_problemi_smtp(monkeypatch):
    from appointment_reminders import policy
    _nessuna_rete(monkeypatch)
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    monkeypatch.setenv(policy.RECIPIENTS_ENV, "prova@example.test")
    _smtp_env(monkeypatch)                                   # SMTP_HOST assente: vale il default del trasporto
    g = cp.Gate()
    cp.config_gate(g)
    assert [p for p in g.problems if "SMTP" in p or "trasporto" in p] == []
