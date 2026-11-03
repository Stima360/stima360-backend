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
