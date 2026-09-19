"""LMC-1B - il corpo dell'email con il magic link. Nient'altro.

UNA EMAIL CHE NON DICE NIENTE DI PIU' DEL NECESSARIO

Chi la riceve potrebbe non averla chiesta: l'indirizzo puo' essere sbagliato,
oppure qualcuno puo' averlo inserito al posto del proprio. Per questo il
messaggio non contiene NULLA del patrimonio di chi lo riceve - niente valore
della casa, niente indirizzo dell'immobile, niente domanda di mercato, niente
dati CRM - e dice esplicitamente di ignorarlo se la richiesta non e' partita da
loro. Il link e' l'unico contenuto sensibile, ed e' a scadenza breve e monouso.

Il token viaggia percent-encoded nella query string: e' un segreto generato con
`secrets.token_urlsafe`, quindi in pratica gia' sicuro per una URL, ma
codificarlo e' cio' che rende il link corretto per costruzione invece che per
fortuna.
"""

from __future__ import annotations

import html
import os
from urllib.parse import quote

#: La base pubblica del portale. Stessa variabile che `main.py` legge per i
#: propri link; il valore di ripiego e' quello del backend di test.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://stima360-backend.onrender.com")

SUBJECT = "Accedi a La Mia Casa"


def login_url(token: str, *, base_url: str | None = None) -> str:
    """Il magic link: la home del portale owner con il token in query string."""
    base = (base_url or PUBLIC_BASE_URL).rstrip("/")
    return f"{base}/owner/?token={quote(token, safe='')}"


def render(*, token: str, minutes: int, base_url: str | None = None) -> tuple[str, str]:
    """Ritorna `(oggetto, corpo_html)`.

    Nessun dato del destinatario entra nel corpo, quindi non c'e' niente da
    interpolare oltre al link e alla durata: l'unico valore che viene da fuori
    e' il token, e passa da `quote` prima di entrare nell'attributo href.
    """
    url = login_url(token, base_url=base_url)
    sicuro = html.escape(url, quote=True)
    corpo = f"""
    <div style="font-family:Arial,Helvetica,sans-serif; color:#222; line-height:1.6; max-width:640px; margin:0 auto;">
      <h2 style="margin:0 0 14px 0; color:#0b6bff;">Accedi a La Mia Casa</h2>
      <p style="margin:0 0 14px 0;">
        Hai chiesto di accedere alla tua area riservata su <b>STIMA360</b>.
        Usa il pulsante qui sotto: ti porta dentro senza password.
      </p>
      <p style="margin:0 0 18px 0;">
        <a href="{sicuro}" style="display:inline-block; padding:12px 22px; background-color:#0b6bff; color:#ffffff; text-decoration:none; border-radius:6px; font-weight:bold;">Entra in La Mia Casa</a>
      </p>
      <p style="margin:0 0 14px 0; font-size:13px; color:#555;">
        Se il pulsante non funziona, copia questo indirizzo nel browser:<br>
        <span style="word-break:break-all;">{sicuro}</span>
      </p>
      <p style="margin:0 0 14px 0;">
        Il collegamento vale <b>{int(minutes)} minuti</b> e si puo' usare una sola volta.
        Scaduto quello, basta richiederne un altro.
      </p>
      <hr style="border:none; border-top:1px solid #e6e6e6; margin:22px 0;">
      <p style="margin:0; font-size:13px; color:#555;">
        Se non hai richiesto tu questo accesso, ignora questa email: senza aprire
        il collegamento non succede nulla, e nessuno entra al posto tuo.
      </p>
    </div>
    """
    return SUBJECT, corpo
