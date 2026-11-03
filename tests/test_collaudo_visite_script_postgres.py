"""ROADMAP VISITE - prova GENERALE dello script `scripts/collaudo_visite.py` su PostgreSQL usa-e-getta
(banco 2C): lo stesso script che si lancia sul TEST reale come One-Off Job, contro un database locale.
Se questa prova non passa, lo script non va lanciato sul TEST."""
from __future__ import annotations

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import vb  # noqa: F401 - fixture

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato"),
]


def _argv(vb, *modo):
    ids = vb["ids"]
    return [*modo, "--expected-host", "127.0.0.1", "--agency-id", str(ids["a"]),
            "--actor-user-id", str(ids["owner_a"]), "--agent-user-id", str(ids["agent_a"])]


def _sintetici(vb):
    return _q(vb, "SELECT count(*) FROM properties WHERE code LIKE 'COLLAUDO-VIS-%'")


@pytest.fixture
def ambiente(vb, monkeypatch):
    monkeypatch.delenv("APPOINTMENT_CONFIRMATIONS", raising=False)
    monkeypatch.delenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", raising=False)
    for nome in ("ENVIRONMENT", "APP_ENV", "ENV", "STIMA360_ENV", "RENDER_ENV", "DEPLOY_ENV"):
        monkeypatch.delenv(nome, raising=False)
    return vb


def test_lo_script_percorre_tutto_il_collaudo_e_non_lascia_messaggi(ambiente, capsys):
    from scripts import collaudo_visite as cv
    vb = ambiente
    prima = _q(vb, "SELECT count(*) FROM communication_messages")[0][0]
    assert cv.main(_argv(vb, "avvia", "--run-id", "CVTEST1")) == 0, capsys.readouterr().out[-3000:]
    out = capsys.readouterr().out
    assert "FAIL" not in out.replace("0 fail", "").replace("0 FAIL", ""), out[-3000:]
    assert "COLLAUDO SUPERATO" in out
    assert _q(vb, "SELECT count(*) FROM communication_messages")[0][0] == prima
    # pulizia: accesso del proprietario chiuso, collegamento disattivato, immobile archiviato
    assert _q(vb, "SELECT count(*) FROM owner_property_access a JOIN properties p ON p.id = a.property_id "
                  "WHERE p.code = 'COLLAUDO-VIS-CVTEST1' AND a.access_status = 'active'") == [(0,)]
    assert _q(vb, "SELECT archived_at IS NOT NULL FROM properties WHERE code = 'COLLAUDO-VIS-CVTEST1'") == [(True,)]
    # il override della sessione non sopravvive allo script
    import main
    assert not main.app.dependency_overrides


def test_lo_script_si_ferma_su_un_host_diverso_da_quello_atteso(ambiente, capsys):
    from scripts import collaudo_visite as cv
    vb = ambiente
    prima = _sintetici(vb)
    argv = _argv(vb, "avvia")
    argv[argv.index("--expected-host") + 1] = "host-di-produzione.invalid"
    with pytest.raises(SystemExit):
        cv.main(argv)
    assert _sintetici(vb) == prima


def test_lo_script_si_ferma_se_l_ambiente_dichiara_produzione(ambiente, monkeypatch):
    from scripts import collaudo_visite as cv
    prima = _sintetici(ambiente)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(SystemExit):
        cv.main(_argv(ambiente, "avvia"))
    assert _sintetici(ambiente) == prima


def test_lo_script_si_ferma_se_i_destinatari_sono_tutti(ambiente, monkeypatch):
    from scripts import collaudo_visite as cv
    prima = _sintetici(ambiente)
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", "*")
    assert cv.main(_argv(ambiente, "avvia")) == 1
    assert _sintetici(ambiente) == prima


def test_il_preflight_non_scrive_nulla(ambiente):
    from scripts import collaudo_visite as cv
    prima = _sintetici(ambiente)
    assert cv.main(_argv(ambiente, "preflight")) == 0
    assert _sintetici(ambiente) == prima


def test_senza_pdftotext_lo_script_legge_il_pdf_con_l_estrattore_interno(ambiente, monkeypatch, capsys):
    """Su Render `pdftotext` di solito non c'e': il collaudo deve leggere comunque il testo dei due PDF."""
    import shutil

    from scripts import collaudo_visite as cv
    reale = shutil.which
    monkeypatch.setattr(shutil, "which", lambda nome, *a, **k: None if nome == "pdftotext" else reale(nome, *a, **k))
    assert cv.main(_argv(ambiente, "avvia", "--run-id", "CVTEST2")) == 0, capsys.readouterr().out[-3000:]
    assert "COLLAUDO SUPERATO" in capsys.readouterr().out


def test_l_estrattore_interno_e_quello_di_poppler_leggono_lo_stesso_testo(monkeypatch):
    import shutil

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from scripts import collaudo_visite as cv
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 700, "Stato della firma: Da firmare (è già così)")
    c.drawString(72, 680, "Parentesi \\(escape\\) e Perché")
    c.save()
    con_poppler = cv.testo_pdf(buf.getvalue())
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    interno = cv.testo_pdf(buf.getvalue())
    assert "Da firmare" in interno and "è già così" in interno and "Perché" in interno
    assert "".join(interno.split()) == "".join(con_poppler.split())


# ---------------------------------------------------------------------------
# Agente di prova: TEST ha `agent_working_hours` vuota
# ---------------------------------------------------------------------------

@pytest.fixture
def senza_orari(ambiente):
    """Come il TEST reale: nessun orario di lavoro. Un agente «reale» (agent2_a) ne ha uno SUO, che deve restare."""
    vb = ambiente
    ids = vb["ids"]
    _q(vb, "DELETE FROM agent_working_hours")
    _q(vb, "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, start_minute, end_minute) "
           "VALUES (%s, %s, 3, 600, 720)", (ids["a"], ids["agent2_a"]))
    return vb


def _argv_prova(vb, modo, *extra):
    ids = vb["ids"]
    return [modo, "--expected-host", "127.0.0.1", "--agency-id", str(ids["a"]), "--actor-user-id", str(ids["owner_a"]),
            "--agente-di-prova", *extra]


def _orari(vb):
    return _q(vb, "SELECT user_id, count(*), md5(string_agg(id::text || day_of_week::text || start_minute::text || end_minute::text, ',' "
                  "ORDER BY id)) FROM agent_working_hours GROUP BY user_id ORDER BY user_id")


def test_senza_orari_il_preflight_indica_l_agente_di_prova_e_non_scrive(senza_orari, capsys):
    from scripts import collaudo_visite as cv
    prima = _orari(senza_orari)
    # l'agente reale e' agent_a (nessun orario): senza opzione il preflight non passa e dice cosa fare
    assert cv.main(_argv(senza_orari, "preflight")) == 1
    assert "--agente-di-prova" in capsys.readouterr().out
    assert cv.main(_argv_prova(senza_orari, "preflight")) == 0
    assert _orari(senza_orari) == prima
    assert _q(senza_orari, "SELECT count(*) FROM operator_users WHERE email_normalized LIKE 'collaudo.agente.%' AND deleted_at IS NULL") == [(0,)]


def test_l_agente_di_prova_e_gli_orari_nascono_e_si_chiudono_senza_toccare_gli_agenti_reali(senza_orari, monkeypatch, capsys):
    from scripts import collaudo_visite as cv
    vb = senza_orari
    prima = _orari(vb)
    audit_prima = _q(vb, "SELECT count(*) FROM platform_audit_log")[0][0]
    argv = ["avvia", "--run-id", "CVAG1", "--expected-host", "127.0.0.1", "--agency-id", str(vb["ids"]["a"]),
            "--actor-user-id", str(vb["ids"]["owner_a"]), "--agente-di-prova"]
    import secrets
    password = []
    reale = secrets.token_urlsafe
    monkeypatch.setattr(secrets, "token_urlsafe", lambda n=None: password.append(reale(n)) or password[-1])
    rc = cv.main(argv)
    out = capsys.readouterr().out
    assert rc == 0, [r for r in out.splitlines() if r.startswith("FAIL")]
    assert "COLLAUDO SUPERATO" in out and "A. Agente di prova" in out and "Z. Pulizia" in out
    # nulla di reale e' cambiato: stessi orari di prima (quelli di agent2_a), nessun orario dell'agente di prova
    assert _orari(vb) == prima
    # l'agente di prova e' chiuso: membership revocata, account disabilitato ed eliminato, nessuna sessione
    ((stato, eliminato, ruolo, m_stato),) = _q(
        vb, "SELECT u.status, u.deleted_at IS NOT NULL, m.role, m.status FROM operator_users u JOIN agency_memberships m "
            "ON m.operator_user_id = u.id WHERE u.email_normalized = 'collaudo.agente.cvag1@example.invalid'")
    assert (stato, eliminato, ruolo, m_stato) == ("disabled", True, "agent", "revoked")
    assert _q(vb, "SELECT count(*) FROM operator_sessions s JOIN operator_users u ON u.id = s.operator_user_id "
                  "WHERE u.email_normalized LIKE 'collaudo.agente.%'") == [(0,)]
    # le tracce restano nel registro di piattaforma (creazione, membership, account, eliminazione)
    assert _q(vb, "SELECT count(*) FROM platform_audit_log")[0][0] >= audit_prima + 4
    # l'agente assegnato alla visita e' quello di prova, non uno reale
    assert _q(vb, "SELECT count(*) FROM appointments a JOIN properties p ON p.id = a.property_id "
                  "WHERE p.code = 'COLLAUDO-VIS-CVAG1' AND a.assigned_user_id IN (%s, %s, %s)",
              (vb["ids"]["agent_a"], vb["ids"]["agent2_a"], vb["ids"]["owner_a"])) == [(0,)]
    # la password casuale dell'agente (mai stampata) non compare nell'output; ne sono state generate almeno 3 (agente + 2 prove)
    segreti = [p for p in password if len(p) >= 24]
    assert len(segreti) >= 3 and not any(p in out for p in segreti)


def test_l_agente_di_prova_e_l_agente_reale_sono_incompatibili(senza_orari):
    from scripts import collaudo_visite as cv
    with pytest.raises(SystemExit) as e:
        cv.main(_argv(senza_orari, "avvia", "--agente-di-prova"))
    assert e.value.code == 2
    assert _q(senza_orari, "SELECT count(*) FROM operator_users WHERE email_normalized LIKE 'collaudo.agente.%' AND deleted_at IS NULL") == [(0,)]


def test_senza_super_admin_designato_non_si_crea_nulla(senza_orari, monkeypatch, capsys):
    from operator_auth.designation import SUPER_ADMIN_ENV

    from scripts import collaudo_visite as cv
    monkeypatch.delenv(SUPER_ADMIN_ENV, raising=False)
    argv = ["avvia", "--expected-host", "127.0.0.1", "--agency-id", str(senza_orari["ids"]["a"]),
            "--actor-user-id", str(senza_orari["ids"]["owner_a"]), "--agente-di-prova"]
    assert cv.main(argv) == 1
    assert "STIMA360_SUPER_ADMIN_USER_ID" in capsys.readouterr().out
    assert _q(senza_orari, "SELECT count(*) FROM operator_users WHERE email_normalized LIKE 'collaudo.agente.%' AND deleted_at IS NULL") == [(0,)]
    assert _q(senza_orari, "SELECT count(*) FROM properties WHERE code LIKE 'COLLAUDO-VIS-%'")[0][0] == \
        _q(senza_orari, "SELECT count(*) FROM properties WHERE code LIKE 'COLLAUDO-VIS-%'")[0][0]


def test_in_produzione_o_su_un_host_diverso_l_agente_di_prova_non_nasce(senza_orari, monkeypatch):
    from scripts import collaudo_visite as cv
    ids = senza_orari["ids"]
    base = ["avvia", "--agency-id", str(ids["a"]), "--actor-user-id", str(ids["owner_a"]), "--agente-di-prova"]
    with pytest.raises(SystemExit):
        cv.main([*base, "--expected-host", "host-di-produzione.invalid"])
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit):
        cv.main([*base, "--expected-host", "127.0.0.1"])
    assert _q(senza_orari, "SELECT count(*) FROM operator_users WHERE email_normalized LIKE 'collaudo.agente.%' AND deleted_at IS NULL") == [(0,)]


def test_il_cleanup_chiude_un_agente_di_prova_rimasto_aperto(senza_orari, monkeypatch, capsys):
    """Se il collaudo muore a meta', `cleanup` chiude l'agente di prova: orari, membership, account."""
    from scripts import collaudo_visite as cv
    vb = senza_orari
    argv_avvia = ["avvia", "--run-id", "CVAG2", "--expected-host", "127.0.0.1", "--agency-id", str(vb["ids"]["a"]),
                  "--actor-user-id", str(vb["ids"]["owner_a"]), "--agente-di-prova"]
    with monkeypatch.context() as m:
        m.setattr(cv, "pulizia_agente", lambda *a, **k: None)              # simula un'interruzione prima della chiusura
        cv.main(argv_avvia)
    capsys.readouterr()
    assert _q(vb, "SELECT count(*) FROM agent_working_hours h JOIN operator_users u ON u.id = h.user_id "
                  "WHERE u.email_normalized = 'collaudo.agente.cvag2@example.invalid'")[0][0] == 7
    prima = _orari(vb)
    assert cv.main(["cleanup", "--expected-host", "127.0.0.1", "--agency-id", str(vb["ids"]["a"]),
                    "--actor-user-id", str(vb["ids"]["owner_a"])]) == 0
    assert _q(vb, "SELECT count(*) FROM agent_working_hours h JOIN operator_users u ON u.id = h.user_id "
                  "WHERE u.email_normalized LIKE 'collaudo.agente.%'") == [(0,)]
    assert _q(vb, "SELECT u.status, u.deleted_at IS NOT NULL FROM operator_users u "
                  "WHERE u.email_normalized = 'collaudo.agente.cvag2@example.invalid'") == [("disabled", True)]
    # gli orari dell'agente reale (agent2_a) sono rimasti quelli di prima
    assert [r for r in _orari(vb) if r[0] == vb["ids"]["agent2_a"]] == [r for r in prima if r[0] == vb["ids"]["agent2_a"]]


def test_con_l_agente_di_prova_agenzia_e_attore_si_ricavano_senza_cercare_un_agente_reale(senza_orari, monkeypatch, capsys):
    from scripts import collaudo_visite as cv
    monkeypatch.setenv("COMMUNICATION_CRON_AGENCY_IDS", str(senza_orari["ids"]["a"]))
    rc = cv.main(["preflight", "--expected-host", "127.0.0.1", "--agente-di-prova"])
    out = capsys.readouterr().out
    # nel banco l'agenzia ha piu' owner/admin: lo script si ferma e lo dice, senza nominare un «agente -1»
    assert "agente -1" not in out
    assert rc == 0, out[-1500:]
    assert "agenzia" in out and "agente: di prova" in out and "agente -1" not in out
    assert "PREFLIGHT SUPERATO" in out and "l'agente reale non viene usato" in out
