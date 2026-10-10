"""STIMA Voice - le impostazioni dell'agenzia (tabella voice_agency_settings).

Solo lettura. Senza una riga per l'agenzia STIMA Voice e' SPENTO e, se
acceso senza altre indicazioni, parte in modalita' `review`: il comportamento
finale `auto` si raggiunge solo per scelta esplicita, dopo il collaudo.
Nessuna funzione qui accende STIMA Voice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.database import core_cursor

from .policy import Settings

DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = False
    policy: Settings = field(default_factory=lambda: Settings(mode="review"))
    max_commands_per_day: int = 50
    max_audio_seconds_month: int = 3600
    transcript_retention_days: int = DEFAULT_RETENTION_DAYS


DISABLED = VoiceSettings()


def load(ctx) -> VoiceSettings:
    agency_id = ctx.require_agency()
    with core_cursor() as (conn, cur):
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SELECT * FROM voice_agency_settings WHERE agency_id = %s", (agency_id,))
        riga = cur.fetchone()
        conn.rollback()
    if riga is None:
        return DISABLED
    return VoiceSettings(
        enabled=bool(riga["enabled"]),
        policy=Settings(mode=riga["mode"], auto_intents=frozenset(riga["auto_intents"] or ()),
                        hidden_duplicate_check=bool(riga["hidden_duplicate_check"])),
        max_commands_per_day=int(riga["max_commands_per_day"]),
        max_audio_seconds_month=int(riga["max_audio_seconds_month"]),
        transcript_retention_days=int(riga["transcript_retention_days"]),
    )
