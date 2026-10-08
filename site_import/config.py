"""La configurazione del collegamento, letta UNA volta dall'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(ValueError):
    pass


def _intero(nome: str, predefinito: int, minimo: int, massimo: int) -> int:
    grezzo = (os.getenv(nome) or "").strip()
    if not grezzo:
        return predefinito
    try:
        valore = int(grezzo)
    except ValueError:
        raise ConfigurationError(f"{nome} must be an integer") from None
    if not minimo <= valore <= massimo:
        raise ConfigurationError(f"{nome} must be between {minimo} and {massimo}")
    return valore


@dataclass(frozen=True)
class Config:
    #: DSN del database del SITO. Solo lettura: vedi `source.SiteSource`.
    site_db_url: str
    source: str = "stima360_site"
    #: Una riga del sito si importa solo quando e' "ferma": il backend del sito
    #: la scrive con piu' UPDATE in transazioni separate (dati, token, PDF).
    settle_minutes: int = 10
    batch: int = 100
    max_attempts: int = 5
    #: Il task "Contattare proprietario" nasce solo per stime piu' recenti di
    #: cosi'; lo storico non riempie la lista "Oggi" di task gia' scaduti.
    followup_max_age_hours: int = 72
    #: Archivio dei PDF del sito (il backend PROD li carica su GitHub).
    pdf_repo: str | None = None
    pdf_branch: str = "main"
    pdf_token: str | None = None
    #: Un PDF si associa a una stima solo se il suo commit cade in questa
    #: finestra intorno alla data della stima: il solo nome `stima_<id>.pdf`
    #: non prova la relazione.
    pdf_match_window_hours: int = 48

    @classmethod
    def from_env(cls) -> "Config":
        url = (os.getenv("SITE_DB_URL") or "").strip()
        if not url:
            raise ConfigurationError("SITE_DB_URL is not set: the site database is required")
        repo = (os.getenv("SITE_PDF_GITHUB_REPO") or "").strip() or None
        if repo is not None and repo.count("/") != 1:
            raise ConfigurationError("SITE_PDF_GITHUB_REPO must be 'owner/repository'")
        return cls(
            site_db_url=url,
            settle_minutes=_intero("SITE_IMPORT_SETTLE_MINUTES", 10, 1, 1440),
            batch=_intero("SITE_IMPORT_BATCH", 100, 1, 5000),
            max_attempts=_intero("SITE_IMPORT_MAX_ATTEMPTS", 5, 1, 50),
            followup_max_age_hours=_intero("SITE_IMPORT_FOLLOWUP_MAX_AGE_HOURS", 72, 0, 24 * 365),
            pdf_repo=repo,
            pdf_branch=(os.getenv("SITE_PDF_GITHUB_BRANCH") or "main").strip(),
            pdf_token=(os.getenv("SITE_PDF_GITHUB_TOKEN") or "").strip() or None,
            pdf_match_window_hours=_intero("SITE_PDF_MATCH_WINDOW_HOURS", 48, 1, 24 * 30),
        )
