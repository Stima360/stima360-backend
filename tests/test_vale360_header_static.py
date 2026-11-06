"""VALE360 - la fascia alta del CRM porta il solo nuovo marchio, centrato.

  * l'asset `assets/brand/vale360-banner.png` esiste (PNG 1400x309, ritagliato solo
    del margine bianco esterno);
  * desktop: `index.html` apre `#app-view` con la fascia `[data-brand-band]` e il
    banner; nella barra laterale non c'e' piu' il vecchio logo ne' `#env-badge`;
  * smartphone: l'intestazione compatta porta il banner al centro (ricerca a
    sinistra, profilo a destra), nessun badge d'ambiente;
  * la Shell non monta piu' il badge d'ambiente; l'ambiente resta indicato, discreto,
    nel menu del profilo (riga «Ambiente», stessa regola dall'hostname);
  * CSS: fascia centrata su computer, nascosta su smartphone (dove il marchio sta
    nell'intestazione compatta).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "static" / "os_shell"
INDEX = SHELL / "index.html"
MAIN = SHELL / "assets" / "main.js"
MOBILE = SHELL / "assets" / "core" / "mobile-shell.js"
CSS = SHELL / "assets" / "app.css"
BANNER = SHELL / "assets" / "brand" / "vale360-banner.png"


def _app_view(html: str) -> str:
    inizio = html.index('<div id="app-view"')
    return html[inizio:html.index('<script type="module"', inizio)]


def test_01_asset_del_banner():
    dati = BANNER.read_bytes()
    assert dati[:8] == b"\x89PNG\r\n\x1a\n"
    larghezza, altezza = int.from_bytes(dati[16:20], "big"), int.from_bytes(dati[20:24], "big")
    assert (larghezza, altezza) == (1400, 309)


def test_02_desktop_solo_il_nuovo_marchio_nella_fascia_alta():
    app = _app_view(INDEX.read_text(encoding="utf-8"))
    # la fascia e' il PRIMO elemento di #app-view, prima della barra laterale
    assert re.search(r'<div id="app-view" hidden>\s*<!--[^>]*-->\s*<header class="brand-band" data-brand-band>', app)
    assert app.index("data-brand-band") < app.index('<aside class="sidebar">')
    assert app.count('src="/os/assets/brand/vale360-banner.png"') == 1
    assert 'alt="VALE360 — Real Estate Intelligence System, powered by STIMA360"' in app
    for vecchio in ("VendereConStima360.png", "stima360-logo.png", 'class="brand"', "brand-logo", "env-badge"):
        assert vecchio not in app, vecchio


def test_03_smartphone_marchio_al_centro_senza_badge():
    js = MOBILE.read_text(encoding="utf-8")
    assert "logo.setAttribute('src', '/os/assets/brand/vale360-banner.png');" in js
    assert "testata.append(cerca, marchio, azioni);" in js                     # ricerca | marchio | profilo
    for vecchio in ("stima360-logo.png", "VendereConStima360", "mountEnvBadge", "env-badge", "mobile-env"):
        assert vecchio not in js, vecchio


def test_04_la_shell_non_monta_piu_il_badge():
    js = MAIN.read_text(encoding="utf-8")
    for vecchio in ("mountEnvBadge", "env-badge", "envBadgeEl"):
        assert vecchio not in js, vecchio


def test_05_css_fascia_centrata_e_marchio_centrato_su_smartphone():
    css = CSS.read_text(encoding="utf-8")
    blocco = css[css.index("/* VALE360: il marchio nella fascia alta."):]
    assert "flex: 0 0 100%; display: flex; justify-content: center;" in blocco
    # il wrap che porta la fascia sopra la barra laterale vale SOLO su computer (su smartphone e' una colonna)
    assert "@media (min-width: 861px) { #app-view { flex-wrap: wrap; } }" in blocco
    assert "\n#app-view { flex-wrap: wrap; }" not in blocco
    assert "object-fit: contain;" in blocco                                      # mai schiacciato ne' tagliato
    mobile = blocco[blocco.index("@media (max-width: 860px)"):]
    assert ".brand-band { display: none; }" in mobile
    assert "grid-template-columns: 42px minmax(0, 1fr) 42px;" in mobile        # colonne laterali uguali


USER_MENU = SHELL / "assets" / "components" / "user-menu.js"


def test_06_ambiente_discreto_nel_menu_profilo():
    js = USER_MENU.read_text(encoding="utf-8")
    assert "import { computeEnvLabel } from '../core/env-badge.js';" in js
    assert "riga('Ambiente', { test: 'Test', prod: 'Produzione', local: 'Locale' }[env])" in js
    assert "righe.push(rigaAmbiente());" in js
    css = CSS.read_text(encoding="utf-8")
    assert '.user-menu-env[data-env="test"] dd' in css
