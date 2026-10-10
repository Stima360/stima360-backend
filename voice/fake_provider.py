"""STIMA Voice - il fornitore finto, per i test e per il collaudo offline.

Nessuna rete, nessun modello: trascrizioni e piani sono CANONICI, registrati
dal corpus o dal test. Il contratto (`VoiceProvider`) e' quello che il
fornitore reale (Fase 5) implementera'; qui vive perche' in Fase 1 esiste solo
il finto.

Comportamenti simulabili, come `calendar_sync/fake_provider.py`:
  * `fail_next`: la prossima chiamata fallisce (`ProviderUnavailable`);
  * `malformed_next`: la prossima interpretazione restituisce un output
    fuori schema, per provare che il pianificatore lo rifiuta;
  * `calls`: il registro delle chiamate, per asserire che non se ne facciano
    di piu' del previsto.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Protocol


class ProviderUnavailable(RuntimeError):
    """Il fornitore non risponde: il comando resta senza effetti."""


class VoiceProvider(Protocol):
    def transcribe(self, audio: bytes, *, mime_type: str, language: str = "it") -> str: ...

    def interpret(self, transcript: str, *, schema: dict[str, Any], recorded_at: datetime) -> dict[str, Any]: ...


def audio_key(audio: bytes) -> str:
    return hashlib.sha256(audio).hexdigest()


class FakeVoiceProvider:
    def __init__(self, transcripts: dict[str, str] | None = None,
                 outputs: dict[str, dict[str, Any]] | None = None) -> None:
        #: chiave audio (sha256 dei byte, o un'etichetta) -> trascritto
        self.transcripts: dict[str, str] = dict(transcripts or {})
        #: trascritto -> output grezzo del "modello"
        self.outputs: dict[str, dict[str, Any]] = dict(outputs or {})
        self.calls: list[tuple[str, str]] = []
        self.fail_next = False
        self.malformed_next = False

    def register(self, transcript: str, output: dict[str, Any], *, audio_label: str | None = None) -> None:
        self.outputs[transcript] = output
        if audio_label is not None:
            self.transcripts[audio_label] = transcript

    def _guard(self, kind: str, key: str) -> None:
        self.calls.append((kind, key))
        if self.fail_next:
            self.fail_next = False
            raise ProviderUnavailable("fake provider: unavailable")

    def transcribe(self, audio: bytes, *, mime_type: str, language: str = "it") -> str:
        chiave = audio.decode("utf-8", errors="ignore") if audio in self._labels() else audio_key(audio)
        self._guard("transcribe", chiave)
        if chiave not in self.transcripts:
            raise KeyError(f"fake provider: no transcript registered for {chiave[:16]}")
        return self.transcripts[chiave]

    def _labels(self) -> set[bytes]:
        return {k.encode("utf-8") for k in self.transcripts}

    def interpret(self, transcript: str, *, schema: dict[str, Any], recorded_at: datetime) -> dict[str, Any]:
        self._guard("interpret", transcript)
        if self.malformed_next:
            self.malformed_next = False
            return {"commands": [{"intent": "create_everything", "quote": transcript}]}
        if transcript not in self.outputs:
            raise KeyError(f"fake provider: no output registered for {transcript[:40]!r}")
        return self.outputs[transcript]
