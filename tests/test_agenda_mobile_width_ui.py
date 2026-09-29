"""Agenda mobile: la pagina entra davvero nel viewport (320-414px).

Causa certificata in Chromium (viewport 390px, pagina larga 556px): sotto i
768px `.agenda-toolbar` passa a `flex-direction: column` ma eredita dalla
regola base `flex-wrap: wrap`. In un contenitore flessibile che va a capo ogni
riga prende come larghezza quella del figlio piu' largo, e `.agenda-commands`
(pulsanti su una sola riga) era largo 450-540px: la barra, il titolo del
periodo e i pulsanti sporgevano dal viewport e allargavano il documento.

Correzione (solo regole aggiunte in fondo ad app.css, solo mobile): la barra in
colonna non va a capo; vanno a capo i gruppi di pulsanti. Nessun
`overflow-x: hidden`, nessuno `scale`/`zoom`, nessuna larghezza fissa.

Questi test leggono la cascata CSS reale (regole base + media query mobile, in
ordine di file): una regressione che reintroduce la causa li fa fallire. La
misura di layout vera (scrollWidth <= innerWidth) e' fatta in Chromium dalla
certificazione esterna.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[1] / "static" / "os_shell" / "assets" / "app.css"
MOBILE_MAX = 767            # la soglia Agenda: `@media (max-width: 767px)`
VIEWPORT_MINIMO = 320


def _regole(testo: str):
    """[(media, selettori, {proprieta: valore})] in ordine di file; media=None fuori da @media."""
    testo = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    out, i, n = [], 0, len(testo)
    media = None
    profondita_media = None
    profondita = 0
    buf = ""
    while i < n:
        c = testo[i]
        if c == "{":
            testa = buf.strip()
            buf = ""
            profondita += 1
            if testa.startswith("@media"):
                media, profondita_media = testa, profondita
            else:
                fine = testo.index("}", i)
                corpo = testo[i + 1:fine]
                decl = {}
                for d in corpo.split(";"):
                    if ":" in d:
                        k, v = d.split(":", 1)
                        decl[k.strip().lower()] = v.strip().lower()
                sel = [s.strip() for s in testa.split(",")]
                out.append((media, sel, decl))
                i = fine
                profondita -= 1
        elif c == "}":
            if profondita_media is not None and profondita == profondita_media:
                media, profondita_media = None, None
            profondita -= 1
            buf = ""
        else:
            buf += c
        i += 1
    return out


def _media_mobile(media: str | None, larghezza: int) -> bool:
    if media is None:
        return True
    massimo = re.search(r"max-width:\s*(\d+)px", media)
    minimo = re.search(r"min-width:\s*(\d+)px", media)
    if massimo and larghezza > int(massimo.group(1)):
        return False
    if minimo and larghezza < int(minimo.group(1)):
        return False
    return bool(massimo or minimo)


def _valore(selettore: str, proprieta: str, larghezza: int, regole=None):
    """Valore finale della proprieta' per un selettore esatto a quella larghezza."""
    valore = None
    for media, sel, decl in (regole if regole is not None else _regole(CSS.read_text(encoding="utf-8"))):
        if selettore in sel and proprieta in decl and _media_mobile(media, larghezza):
            valore = decl[proprieta]
    return valore


@pytest.fixture(scope="module")
def regole():
    return _regole(CSS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("larghezza", [320, 360, 375, 390, 414, MOBILE_MAX])
def test_barra_in_colonna_non_va_a_capo_su_mobile(regole, larghezza):
    # la causa: colonna + wrap = riga larga quanto il figlio piu' largo
    assert _valore(".agenda-toolbar", "flex-direction", larghezza, regole) == "column"
    assert _valore(".agenda-toolbar", "flex-wrap", larghezza, regole) == "nowrap"
    assert _valore(".agenda-toolbar", "align-items", larghezza, regole) == "stretch"


@pytest.mark.parametrize("larghezza", [320, 360, 375, 390, 414, MOBILE_MAX])
@pytest.mark.parametrize("gruppo", [".agenda-commands", ".agenda-views", ".agenda-nav"])
def test_gruppi_di_pulsanti_vanno_a_capo_su_mobile(regole, larghezza, gruppo):
    assert _valore(gruppo, "display", larghezza, regole) == "flex"
    assert _valore(gruppo, "flex-wrap", larghezza, regole) == "wrap"


def test_desktop_invariato(regole):
    # Sopra la soglia nulla cambia: barra in riga che va a capo, pulsanti in riga.
    assert _valore(".agenda-toolbar", "flex-direction", 1280, regole) is None
    assert _valore(".agenda-toolbar", "flex-wrap", 1280, regole) == "wrap"
    assert _valore(".agenda-commands", "flex-wrap", 1280, regole) is None
    assert _valore(".agenda-views", "flex-wrap", 1280, regole) is None


def _px(valore: str | None) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)px", valore or "")
    return float(m.group(1)) if m else 0.0


BARRA = (".agenda-page", ".agenda-toolbar", ".agenda-nav", ".agenda-period", ".agenda-tz",
         ".agenda-views", ".agenda-commands", ".agenda-filters", ".agenda-filter",
         ".agenda-legend", ".agenda-notice", ".agenda-commands .btn", ".agenda-views .btn",
         ".agenda-nav .btn", ".agenda-view-btn")


@pytest.mark.parametrize("larghezza", [320, 390])
def test_nessuna_larghezza_fissa_nella_barra(regole, larghezza):
    for sel in BARRA:
        for prop in ("width", "min-width", "flex-basis"):
            v = _valore(sel, prop, larghezza, regole)
            assert _px(v) < VIEWPORT_MINIMO - 32, (sel, prop, v)   # 16px di padding per lato
        assert _valore(sel, "white-space", larghezza, regole) in (None, "normal"), sel


def test_nessun_trucco_che_nasconde_il_contenuto(regole):
    vietati = []
    for media, sel, decl in regole:
        for s in sel:
            globale = s in ("html", "body", "#app", "#app-view", ".main", ".content", "#content") \
                or s.startswith(".agenda-page") or s.startswith(".agenda-toolbar")
            if not globale:
                continue
            for prop in ("overflow", "overflow-x"):
                if prop in decl and ("hidden" in decl[prop] or "clip" in decl[prop]):
                    vietati.append((s, prop, decl[prop]))
            if "zoom" in decl or "scale" in decl or "scale(" in decl.get("transform", ""):
                vietati.append((s, "zoom/scale", decl))
    assert vietati == []
