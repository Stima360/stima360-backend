"""FASE A - da un corpo di TESTO SEMPLICE agli argomenti di spedizione.

Le sequenze e gli invii manuali producono testo: a capo, elenchi, un link per
riga. Spedito com'era, dentro una parte html, perdeva tutto. Qui il testo
diventa html (ogni carattere passa da `html.escape`, gli URL diventano link)
e resta anche come parte testo. Nessuna rete, nessun database, nessuna
configurazione: solo una riga del ledger in ingresso.
"""
from __future__ import annotations

import html
import re
from typing import Any, Mapping

from ..enums import BODY_FORMAT_KEY, BODY_FORMAT_TEXT, REPLY_TO_KEY, UNSUBSCRIBE_URL_KEY

_URL = re.compile(r"https?://[^\s<>\"']+")
_PUNTEGGIATURA_FINALE = ".,;:!?)]"

STILE = ('font-family:Arial,Helvetica,sans-serif;color:#222;line-height:1.6;'
         'max-width:640px;margin:0 auto;')


def _riga(testo: str) -> str:
    """La riga con escape e link: l'escape viene PRIMA, su ogni pezzo."""
    pezzi: list[str] = []
    posizione = 0
    for trovato in _URL.finditer(testo):
        url = trovato.group(0)
        coda = ""
        while url and url[-1] in _PUNTEGGIATURA_FINALE:
            coda = url[-1] + coda
            url = url[:-1]
        pezzi.append(html.escape(testo[posizione:trovato.start()]))
        pezzi.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
        pezzi.append(html.escape(coda))
        posizione = trovato.end()
    pezzi.append(html.escape(testo[posizione:]))
    return "".join(pezzi)


def testo_in_html(testo: str) -> str:
    """Paragrafi sulle righe vuote, `<br>` sugli a capo, tutto con escape."""
    paragrafi = [p for p in re.split(r"\n\s*\n", (testo or "").replace("\r\n", "\n").strip())]
    blocchi = ["<p>" + "<br>\n".join(_riga(r) for r in p.split("\n")) + "</p>"
               for p in paragrafi if p.strip()]
    return f'<div style="{STILE}">\n' + "\n".join(blocchi) + "\n</div>"


def _url_valido(valore: Any) -> str | None:
    if not isinstance(valore, str):
        return None
    valore = valore.strip()
    if not valore.startswith(("https://", "http://")) or re.search(r"[\s<>]", valore):
        return None
    return valore


def e_testo(metadata: Any) -> bool:
    return isinstance(metadata, Mapping) and metadata.get(BODY_FORMAT_KEY) == BODY_FORMAT_TEXT


def argomenti_di_invio(corpo: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Gli argomenti a parola chiave per la primitiva email, da una riga di testo.

    `List-Unsubscribe-Post` (RFC 8058, one-click) si aggiunge SOLO insieme a un
    URL valido: la rotta pubblica accetta il POST standard con il token
    firmato nella query.
    """
    argomenti: dict[str, Any] = {"corpo_testo": corpo}
    risposta = metadata.get(REPLY_TO_KEY)
    if isinstance(risposta, str) and risposta.strip():
        argomenti["reply_to"] = risposta.strip()
    url = _url_valido(metadata.get(UNSUBSCRIBE_URL_KEY))
    if url is not None:
        argomenti["intestazioni"] = {"List-Unsubscribe": f"<{url}>",
                                     "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return argomenti
