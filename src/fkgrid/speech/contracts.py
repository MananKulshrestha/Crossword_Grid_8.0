"""Provider-neutral speech transcription result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SpeechStatus(StrEnum):
    OK = "OK"
    INVALID_AUDIO = "INVALID_AUDIO"
    EMPTY_TRANSCRIPTION = "EMPTY_TRANSCRIPTION"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class SpeechTranscription:
    """Safe transcription result; provider error bodies never cross this boundary."""

    status: SpeechStatus
    text: str = ""
    language: str = "en"
    model_alias: str = ""
    provider_name: str = ""
    latency_ms: int = 0
    prompt_id: str = "shopping_transcription_v1"
    prompt_version: str = "1"
    error_code: str | None = None
