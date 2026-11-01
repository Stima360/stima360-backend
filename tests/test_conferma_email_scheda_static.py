"""La scheda del contatto si aggiorna da sola quando il cliente conferma
l'email del consenso: solo mentre c'e' una conferma in attesa, senza toccare
un modulo aperto, e smettendo quando la scheda non c'e' piu'."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "os_shell" / "assets" / "components" / "communications.js"


def test_01_aggiornamento_automatico_solo_con_una_conferma_in_attesa():
    codice = JS.read_text(encoding="utf-8")
    blocco = codice[codice.index("async function aggiornaSeInAttesa()"):codice.index("  return ridisegna();\n}")]
    assert "[data-consent-pending]" in blocco and "document.hidden" in blocco
    assert "details[open]" in blocco, "mai mentre l'operatore compila un modulo"
    assert "setInterval(aggiornaSeInAttesa, 30000)" in blocco
    assert "'visibilitychange'" in blocco and "removeEventListener('visibilitychange'" in blocco
    assert "clearInterval(orologio)" in blocco and "document.body.contains(mount)" in blocco
    assert "renderCommunications({ status: 'loading' })" not in blocco, "nessuna schermata di caricamento"
    esito = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert esito.returncode == 0, esito.stderr


def test_02_la_pagina_finale_dice_consenso_email_confermato():
    from communication import consent_page
    pagina = consent_page.confirm_result_page({"status": "confirmed"})
    assert "<h1>Consenso email confermato</h1>" in pagina and 'data-esito="confirmed"' in pagina
    assert "Consenso email confermato" not in consent_page.confirm_result_page({"status": "expired"})
