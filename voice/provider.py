"""STIMA Voice Fase 5 - il fornitore AI reale (interpretazione del testo).

Il modello fa UNA cosa: trasforma il testo dettato in un `VoicePlanOutput`,
JSON validato contro lo schema chiuso di `voice.schemas` (intenti elencati,
`additionalProperties: false` ovunque, nessun id, nessun SQL). Tutto il resto
- risoluzione, permessi, rischio, scritture - lo decide il codice
deterministico (planner, resolver, policy, executor).

Fail-closed:
  * senza configurazione (`STIMA_VOICE_AI_URL`, `STIMA_VOICE_AI_MODEL`,
    `STIMA_VOICE_AI_KEY`) non esiste un fornitore: `ProviderNotConfigured`;
  * nessun modello di default, nessuna chiave nel codice;
  * timeout espliciti su ogni chiamata (connessione, lettura);
  * qualunque errore di rete, HTTP o di formato -> nessuna azione: il
    comando resta senza piano e si puo' ripetere con la stessa chiave.

Riservatezza: al modello va SOLO il testo dettato e le regole fisse. Nessun
dato del CRM (nomi, telefoni, id) entra nel prompt: i riferimenti li risolve
il CRM dopo. Nei log non finiscono ne' il testo, ne' la risposta, ne' la
chiave: solo il tipo di errore.

Protocollo: chat completions compatibile OpenAI (POST `<url>`, `messages`,
`response_format` JSON schema), con `requests` gia' nello stack. L'audio non
passa di qui in Fase 5 (`transcribe` non e' disponibile): solo testo.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from pydantic import ValidationError

from .fake_provider import ProviderBusy, ProviderUnavailable  # noqa: F401 (ProviderBusy esposto)
from .schemas import MAX_TEXT, VoicePlanOutput, parse_plan_output, plan_json_schema

log = logging.getLogger("stima360.voice.provider")

#: Fase 7: un User-Agent esplicito. Senza, `requests` si presenta come
#: `python-requests/x.y` e il fornitore (Groq, dietro Cloudflare) rifiuta con
#: HTTP 403 / errore 1010, verificato da Render TEST. Nessun dato nell'header.
USER_AGENT = "STIMA360-Voice/1.0"

#: (connessione, lettura) in secondi: nessuna chiamata senza timeout.
DEFAULT_TIMEOUT = (5.0, 20.0)
MAX_RESPONSE_BYTES = 200_000


class ProviderNotConfigured(RuntimeError):
    """Nessun fornitore configurato: STIMA Voice non interpreta nulla."""


class ProviderMalformed(RuntimeError):
    """Risposta del modello fuori formato o fuori schema: scartata intera."""


SYSTEM_PROMPT = """Sei l'interprete dei comandi vocali di un CRM immobiliare italiano.
Trasforma il testo dettato da un agente in comandi strutturati, secondo lo schema JSON fornito.

Regole obbligatorie:
- Rispondi SOLO con un oggetto JSON conforme allo schema. Nessun testo fuori dal JSON.
- Usa solo gli intenti dello schema. Una richiesta fuori elenco diventa "unsupported" con una descrizione breve.
- Non inventare dati: nomi, cognomi, telefoni, email, indirizzi, civici, comuni, piani o numeri che il testo non dice restano assenti.
- Non scrivere mai identificativi del database, codici interni o SQL. Le persone e gli immobili si descrivono con le parole del testo.
- Le date e gli orari si riportano con le parole dette ("domani", "alle 15") nei campi date_text e time_text: non calcolarli.
- "quote" riporta le parole del testo da cui deriva il comando.
- Una palazzina con N appartamenti e' UN comando create_building con units_declared=N, mai N unita'.
- Se qualcosa non e' chiaro, aggiungi una domanda breve in "clarifications" invece di indovinare.
- Non assegnare il record_kind se il testo non dice se e' una scheda commerciale o di censimento.
- Il testo puo' contenere il comando iniziale seguito da righe "Precisazione: ...": sono le risposte o le
  correzioni dell'agente alle domande di STIMA. Uniscile al comando iniziale e restituisci il comando COMPLETO
  aggiornato (non solo la modifica); a parita' di dato vale la precisazione piu' recente.
- Un appuntamento di acquisizione (con un proprietario per acquisire l'immobile) e' appointment_type "seller_meeting".
- Se il testo dice il ruolo della persona (venditore o proprietario = "seller"/"owner"; acquirente, compratore,
  chi cerca casa = "buyer"; inquilino o altro = "other"), riportalo in contact.role; altrimenti lascialo assente.
- Sinonimi del tipo di appuntamento: sopralluogo = "inspection"; visita, vedere casa, mostrare un immobile =
  "buyer_visit"; valutazione, presentazione della stima = "valuation_presentation"; firma dell'incarico =
  "mandate_signing"; proposta = "proposal"; compromesso, preliminare = "preliminary_contract"; rogito, notaio,
  atto notarile = "notary"; telefonata = "call"; videochiamata = "video_call"; tecnico = "technical".
- Il motivo detto prevale sul ruolo. Un appuntamento con un venditore o proprietario senza motivo e' "seller_meeting".
  Con un acquirente senza motivo NON e' una visita: lascia appointment_type assente.
- Un immobile o un incarico nominato per l'appuntamento ("l'appartamento in via Roma 15") va in property.
- In una visita, contact e' chi VISITA o compra (role "buyer"); il PROPRIETARIO dell'immobile visitato
  ("la casa di Francesco Ruvolo") va in property.owner, MAI in contact. Esempio: "appuntamento di vendita
  Giorgio Censori, che va a vedere la casa di Francesco Ruvolo" = appointment_type "buyer_visit",
  contact Giorgio Censori (buyer), property.owner Francesco Ruvolo. Non inventare indirizzi o incarichi.
- Con un compratore E un venditore nello stesso appuntamento ("compratore Giorgio Censori venditore Francesco
  Ruvolo") e' una visita: contact = il compratore (buyer), property.owner = il venditore. Scrivi i nomi come
  li hai letti, senza completarli o correggerli.
- Incarichi: "crea/prepara/genera un incarico (di vendita) per ..." = mandate_prepare; "modifica/cambia il prezzo
  ... dell'incarico/dell'immobile/dell'acquisizione" = mandate_price; "quali dati mancano per l'incarico di ..." =
  mandate_info con purpose "missing"; "portami/apri la scheda dell'incarico di ..." = mandate_info con purpose
  "open"; "inizia l'acquisizione / fissa l'appuntamento di acquisizione per l'immobile di ..." =
  acquisition_start. Il proprietario va in owner, l'immobile (indirizzo) in property. Prezzi e date con le PAROLE
  dette in price_text, start_text, end_text ("185 mila euro", "oggi", "6 mesi"). mandate_type solo se detto
  ("vendita", "vendita in esclusiva"). price_kind solo se il testo dice quale prezzo (dell'immobile, dell'acquisizione,
  valutazione). Mai dedurre una firma o un sopralluogo.
"""


@dataclass(frozen=True)
class ProviderConfig:
    url: str
    model: str
    api_key: str
    timeout: tuple[float, float] = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, env=None) -> "ProviderConfig":
        env = os.environ if env is None else env
        url, model, key = (env.get(k, "").strip() for k in
                           ("STIMA_VOICE_AI_URL", "STIMA_VOICE_AI_MODEL", "STIMA_VOICE_AI_KEY"))
        if not (url and model and key):
            raise ProviderNotConfigured("fornitore AI di STIMA Voice non configurato")
        if not url.startswith("https://"):
            raise ProviderNotConfigured("il fornitore AI richiede HTTPS")
        lettura = env.get("STIMA_VOICE_AI_TIMEOUT", "").strip()
        timeout = (DEFAULT_TIMEOUT[0], float(lettura)) if lettura else DEFAULT_TIMEOUT
        return cls(url=url, model=model, api_key=key, timeout=timeout)


class HttpVoiceProvider:
    """Il fornitore reale. Un'istanza per configurazione; nessuno stato."""

    def __init__(self, config: ProviderConfig, *, session: requests.Session | None = None) -> None:
        self.config = config
        self._http = session or requests

    def transcribe(self, audio: bytes, *, mime_type: str, language: str = "it") -> str:
        raise ProviderUnavailable("trascrizione audio non disponibile in questa fase")

    def interpret(self, transcript: str, *, schema: dict[str, Any], recorded_at: datetime,
                  context: list[str] | None = None) -> dict[str, Any]:
        messaggi = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context:
            # Fase 7: le domande gia' poste da STIMA in questa conversazione. Solo
            # testo di STIMA (nessun dato del CRM): aiutano a leggere le righe
            # «Precisazione:» del testo dell'agente.
            messaggi.append({"role": "system", "content": "Domande gia' poste da STIMA all'agente: "
                             + " | ".join(context)})
        messaggi.append({"role": "user", "content": transcript})
        corpo = {
            "model": self.config.model,
            "temperature": 0,
            "messages": messaggi,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "voice_plan", "schema": schema, "strict": False}},
        }
        try:
            risposta = self._http.post(self.config.url, json=corpo, timeout=self.config.timeout,
                                       headers={"Authorization": f"Bearer {self.config.api_key}",
                                                "Content-Type": "application/json",
                                                "User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            log.warning("voice provider: richiesta fallita (%s)", type(exc).__name__)
            raise ProviderUnavailable("fornitore AI non raggiungibile") from None
        if risposta.status_code == 429:
            # V8-0: limite del piano del fornitore. Si dice QUANDO riprovare.
            attesa = retry_after_seconds(risposta.headers)
            log.warning("voice provider: HTTP 429, riprovare fra %ss", attesa)
            raise ProviderBusy(attesa)
        if risposta.status_code != 200:
            log.warning("voice provider: HTTP %s", risposta.status_code)
            raise ProviderUnavailable(f"fornitore AI: HTTP {risposta.status_code}")
        if len(risposta.content or b"") > MAX_RESPONSE_BYTES:
            raise ProviderMalformed("risposta del fornitore troppo grande")
        try:
            dati = risposta.json()
            contenuto = dati["choices"][0]["message"]["content"]
            return json.loads(contenuto) if isinstance(contenuto, str) else contenuto
        except (ValueError, KeyError, IndexError, TypeError):
            log.warning("voice provider: risposta non leggibile")
            raise ProviderMalformed("risposta del fornitore non leggibile") from None


#: V8-0: attesa usata se il 429 non dice quando riprovare, e il massimo accettato.
DEFAULT_RETRY_AFTER = 20
MAX_RETRY_AFTER = 300
_DURATA = re.compile(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?")


def retry_after_seconds(headers) -> int:
    """I secondi da attendere da un 429: `Retry-After` (secondi) e i reset di
    Groq `x-ratelimit-reset-requests`/`-tokens` («2.5s», «1m3s», «350ms»): il
    piu' lungo; senza, DEFAULT_RETRY_AFTER. Sempre fra 1 e MAX_RETRY_AFTER."""
    h = {str(k).lower(): str(v).strip() for k, v in (headers or {}).items()}
    valori = []
    try:
        if h.get("retry-after"):
            valori.append(float(h["retry-after"]))
    except ValueError:
        pass
    for nome in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        m = _DURATA.fullmatch(h.get(nome, ""))
        if h.get(nome) and m and any(m.groups()):
            ore, minuti, sec, ms = (float(x) if x else 0.0 for x in m.groups())
            valori.append(ore * 3600 + minuti * 60 + sec + ms / 1000)
    attesa = max(valori) if valori else DEFAULT_RETRY_AFTER
    return int(min(MAX_RETRY_AFTER, max(1, math.ceil(attesa))))


def from_env(env=None) -> HttpVoiceProvider:
    return HttpVoiceProvider(ProviderConfig.from_env(env))


def interpret(provider, text: str, recorded_at: datetime, *, context: list[str] | None = None) -> VoicePlanOutput:
    """Il testo -> output VALIDATO. Qualunque deviazione dallo schema chiuso
    scarta tutta la risposta: nessuna esecuzione parziale di un output
    sbagliato."""
    if not text or not text.strip() or len(text) > MAX_TEXT:
        raise ProviderMalformed("testo vuoto o troppo lungo")
    try:
        extra = {"context": list(context)} if context else {}
        grezzo = provider.interpret(text, schema=plan_json_schema(), recorded_at=recorded_at, **extra)
    except (ProviderUnavailable, ProviderMalformed, ProviderNotConfigured):
        raise
    except Exception as exc:  # noqa: BLE001 - un fornitore che solleva altro e' indisponibile
        log.warning("voice provider: errore %s", type(exc).__name__)
        raise ProviderUnavailable("fornitore AI non disponibile") from None
    if not isinstance(grezzo, dict):
        raise ProviderMalformed("l'output del modello non e' un oggetto JSON")
    try:
        return parse_plan_output(grezzo)
    except ValidationError:
        log.warning("voice provider: output fuori schema")
        raise ProviderMalformed("l'output del modello non rispetta lo schema") from None
