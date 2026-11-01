"""V3 - LA GRAFICA DELLE 16 EMAIL DEL FOLLOW-UP (decisione del titolare).

Un solo modello HTML, colore parametrico sul numero della tappa, logo
ufficiale STIMA360 in testa, titolo, corpo arioso, riquadri per elenchi e
"regali", pulsante CTA, piede con sito e link di disiscrizione.

DOVE SI APPLICA. Solo nell'adapter email, al momento dell'invio, e solo ai
messaggi dei passi `followup_v3_m1..m16`. Il messaggio in coda resta TESTO
(e' quello che viaggia come parte `text/plain`, identico a prima): niente
cambia nel ledger, nelle sequenze, nei tempi, nei consensi; le email gia'
spedite non vengono toccate. I testi approvati sono riportati integralmente:
ogni riga del corpo compare nell'html, con escape, nello stesso ordine.

COMPATIBILITA'. Tabelle, stili inline, larghezza massima 600px, immagini con
`alt`: Gmail, Outlook, Apple Mail e smartphone. Il logo e' un'immagine
servita dal CRM (rotta pubblica); se il client non la carica resta il nome
in chiaro. L'indirizzo base del CRM si legge dal link di disiscrizione gia'
presente nei metadati: questo modulo non legge l'ambiente e non conosce il
dominio.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Mapping

from . import email_format
from ..marchio import LOGO_ALT, LOGO_FILE, LOGO_PERCORSO, LOGO_PERCORSO_RELATIVO, SITO

PREFISSO_TEMPLATE = "followup_v3_m"
PASSI = 16

#: I sei colori approvati, a rotazione: (nome, colore, tinta chiara, testo sul pulsante).
COLORI = (
    ("verde", "#2E8B57", "#E8F4EC", "#FFFFFF"),
    ("blu", "#1F5FBF", "#E7EEF9", "#FFFFFF"),
    ("arancione", "#E8821A", "#FDF0E3", "#FFFFFF"),
    ("viola", "#6A3FA0", "#EFE9F7", "#FFFFFF"),
    ("bordeaux", "#8B1E3F", "#F5E6EB", "#FFFFFF"),
    ("giallo", "#F2C300", "#FFF8D6", "#1A1A1A"),
)

#: La CTA di ogni email: (etichetta, dove). `guida` = il link della guida nel
#: corpo; `risposta` = rispondi all'email (Reply-To); `sito` = stima360.it.
CTA = {
    1: ("Scopri il modello STIMA360", "sito"),
    2: ("Visita www.stima360.it", "sito"),
    3: ("Richiedi una valutazione gratuita", "sito"),
    4: ("Rispondi a questa email", "risposta"),
    5: ("Visita www.stima360.it", "sito"),
    6: ("Scarica la guida gratuita", "guida"),
    7: ("Rispondi a questa email", "risposta"),
    8: ("Visita www.stima360.it", "sito"),
    9: ("Visita www.stima360.it", "sito"),
    10: ("Rispondi a questa email", "risposta"),
    11: ("Visita www.stima360.it", "sito"),
    12: ("Richiedi una valutazione gratuita", "sito"),
    13: ("Visita www.stima360.it", "sito"),
    14: ("Visita www.stima360.it", "sito"),
    15: ("Rispondi con A, B, C o D", "risposta"),
    16: ("Richiedi una valutazione gratuita", "sito"),
}

_DISISCRIZIONE = "Se non vuoi piu' ricevere questi messaggi:"
_ELENCO = re.compile(r"^(\*\s|-\s|☐\s|\d+\.\s|🟢|🟡|🔴|[A-D]\s—\s)")
_URL = re.compile(r"https?://[^\s<>\"']+")

_FONT = "font-family:Arial,Helvetica,sans-serif;"


def passo(message: Mapping[str, Any]) -> int | None:
    """Il numero della tappa, se il messaggio e' una delle 16 email del V3."""
    chiave = str(message.get("template_key") or "")
    if not chiave.startswith(PREFISSO_TEMPLATE):
        return None
    resto = chiave[len(PREFISSO_TEMPLATE):]
    if not resto.isdigit() or not 1 <= int(resto) <= PASSI:
        return None
    return int(resto)


def si_applica(message: Mapping[str, Any]) -> bool:
    return passo(message) is not None and email_format.e_testo(message.get("metadata"))


def colore(n: int) -> tuple[str, str, str, str]:
    return COLORI[(n - 1) % len(COLORI)]


def _base(metadata: Mapping[str, Any] | None) -> str | None:
    url = (metadata or {}).get("unsubscribe_url")
    if isinstance(url, str) and "/api/public/communication/" in url:
        return url.split("/api/public/communication/", 1)[0]
    return None


def _paragrafi(testo: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", (testo or "").replace("\r\n", "\n").strip()) if p.strip()]


def _righe_html(paragrafo: str) -> str:
    # Solo nell'html: l'asterisco degli elenchi diventa un pallino. Il testo
    # semplice che viaggia accanto resta quello approvato, parola per parola.
    return "<br>\n".join(email_format._riga("\u2022 " + r[2:] if r.startswith("* ") else r)
                         for r in paragrafo.split("\n"))


def _spezza(testo: str) -> tuple[list[str], str | None, str | None]:
    """(paragrafi del corpo, nome agenzia, url di disiscrizione) dal testo in coda."""
    paragrafi = _paragrafi(testo)
    agenzia = url = None
    if paragrafi and paragrafi[-1].startswith(_DISISCRIZIONE):
        trovato = _URL.search(paragrafi[-1])
        url = trovato.group(0) if trovato else None
        paragrafi = paragrafi[:-1]
        if paragrafi and paragrafi[-1].split("\n")[-1].strip() == "su Stima360":
            righe = paragrafi[-1].split("\n")
            agenzia = righe[0].strip() if len(righe) >= 2 else None
            paragrafi = paragrafi[:-1]
    return paragrafi, agenzia, url


def _cta(n: int, testo: str, metadata: Mapping[str, Any] | None) -> tuple[str, str]:
    etichetta, dove = CTA.get(n, ("Visita www.stima360.it", "sito"))
    if dove == "guida":
        link = next((u for u in _URL.findall(testo) if "guida" in u.lower()), None)
        if link:
            return etichetta, link
    if dove == "risposta":
        risposta = (metadata or {}).get("reply_to")
        if isinstance(risposta, str) and "@" in risposta:
            return etichetta, f"mailto:{risposta}"
    return etichetta, SITO


def _blocco(paragrafo: str, tinta: str, col: str) -> str:
    righe = paragrafo.split("\n")
    contenuto = _righe_html(paragrafo)
    if paragrafo.startswith("🎁"):
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0;">'
                f'<tr><td style="background:{tinta};border-left:4px solid {col};border-radius:6px;padding:14px 18px;'
                f'{_FONT}font-size:16px;line-height:1.6;color:#1A1A1A;">{contenuto}</td></tr></table>')
    if len(righe) > 1 and all(_ELENCO.match(r.strip()) for r in righe if r.strip()):
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:14px 0;">'
                f'<tr><td style="border:1px solid {tinta};border-radius:6px;padding:12px 18px;'
                f'{_FONT}font-size:16px;line-height:1.7;color:#1A1A1A;">{contenuto}</td></tr></table>')
    return (f'<p style="margin:0 0 14px 0;{_FONT}font-size:16px;line-height:1.6;color:#1A1A1A;">'
            f'{contenuto}</p>')


def html(message: Mapping[str, Any]) -> str:
    """L'email html con il marchio, dal messaggio del ledger (testo + metadati)."""
    n = passo(message) or 1
    nome, col, tinta, testo_pulsante = colore(n)
    metadata = message.get("metadata") or {}
    testo = message.get("rendered_body") or ""
    titolo = _html.escape(str(message.get("subject_snapshot") or ""))
    paragrafi, agenzia, url_disiscrizione = _spezza(testo)
    base = _base(metadata)
    logo_url = f"{base}{LOGO_PERCORSO}" if base else None
    etichetta_cta, link_cta = _cta(n, testo, metadata)

    if logo_url:
        logo = (f'<img src="{_html.escape(logo_url, quote=True)}" alt="{LOGO_ALT}" width="300" '
                f'style="display:block;max-width:300px;width:100%;height:auto;border:0;margin:0 auto;">')
    else:
        logo = (f'<div style="{_FONT}font-size:26px;letter-spacing:2px;color:#1A1A1A;">STIMA360'
                f'<div style="font-size:11px;letter-spacing:3px;color:#555555;">IMMOBILIARE DI NUOVA GENERAZIONE</div></div>')
    corpo = "\n".join(_blocco(p, tinta, col) for p in paragrafi)
    piede_agenzia = (f'<p style="margin:0 0 6px 0;{_FONT}font-size:13px;color:#555555;">{_html.escape(agenzia)} su Stima360</p>'
                     if agenzia else "")
    disiscrizione = ""
    if url_disiscrizione:
        disiscrizione = (f'<p style="margin:0;{_FONT}font-size:12px;color:#777777;">{_html.escape(_DISISCRIZIONE)} '
                         f'<a href="{_html.escape(url_disiscrizione, quote=True)}" style="color:#555555;">disiscriviti</a></p>')
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{titolo}</title>
<style>
@media only screen and (max-width: 620px) {{
  .contenitore {{ width: 100% !important; }}
  .interno {{ padding: 20px 16px !important; }}
  .pulsante a {{ display: block !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background:#F4F5F7;" data-tappa="{n}" data-colore="{nome}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F5F7;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" class="contenitore" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;background:#FFFFFF;border-radius:10px;overflow:hidden;">
<tr><td align="center" style="background:#FFFFFF;padding:28px 24px 20px 24px;">{logo}</td></tr>
<tr><td style="height:4px;background:{col};font-size:0;line-height:0;">&nbsp;</td></tr>
<tr><td class="interno" style="padding:28px 36px 12px 36px;">
<h1 style="margin:0 0 18px 0;{_FONT}font-size:24px;line-height:1.3;color:#1A1A1A;">{titolo}</h1>
{corpo}
</td></tr>
<tr><td class="pulsante" align="center" style="padding:8px 36px 32px 36px;">
<a href="{_html.escape(link_cta, quote=True)}" style="display:inline-block;background:{col};color:{testo_pulsante};text-decoration:none;{_FONT}font-size:17px;font-weight:bold;padding:16px 34px;border-radius:30px;">{_html.escape(etichetta_cta)}</a>
</td></tr>
<tr><td style="background:#F9FAFB;border-top:1px solid #E5E7EB;padding:20px 36px;text-align:center;">
<p style="margin:0 0 6px 0;{_FONT}font-size:14px;color:#1A1A1A;"><strong>STIMA360</strong> &middot; <a href="{SITO}" style="color:{col};text-decoration:none;">www.stima360.it</a></p>
{piede_agenzia}
{disiscrizione}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


__all__ = ["si_applica", "html", "passo", "colore", "COLORI", "CTA", "LOGO_FILE", "LOGO_PERCORSO",
           "LOGO_PERCORSO_RELATIVO", "LOGO_ALT", "SITO"]
