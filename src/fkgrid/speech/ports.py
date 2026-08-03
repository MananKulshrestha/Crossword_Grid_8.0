"""Ports for the isolated speech-to-text adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .contracts import SpeechTranscription


class SpeechToTextPort(Protocol):
    """Transcribe one user-recorded audio blob when explicitly requested."""

    def transcribe(
        self,
        audio: bytes,
        content_type: str,
        *,
        language: str = "en",
        deadline_ms: int = 8_000,
    ) -> SpeechTranscription: ...


class SpeechTransport(Protocol):
    """HTTP transport seam, kept injectable so tests never call DeepInfra."""

    def post_multipart(
        self,
        *,
        fields: Mapping[str, str],
        audio: bytes,
        filename: str,
        content_type: str,
        timeout_ms: int,
    ) -> Mapping[str, Any]: ...
