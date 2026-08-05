"""Small provider adapter for Sarvam translation and text-to-speech.

The rest of the shopper pipeline works in English so catalog and intent
behaviour stays deterministic. Sarvam is used at the language boundary only:
incoming text is detected and translated to English, generated text is
translated back to the detected language, and TTS returns decoded audio bytes.

API credentials are read from the process environment and are never included
in exceptions, traces, or response models.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

DEFAULT_SARVAM_ENDPOINT = "https://api.sarvam.ai"
DEFAULT_SARVAM_TIMEOUT_S = 8.0
DEFAULT_TTS_MODEL = "bulbul:v3"
DEFAULT_TTS_SPEAKER = "shubh"
ENGLISH_LANGUAGE_CODE = "en-IN"
MAX_TRANSLATION_CHARS = 2_000
MAX_TTS_CHARS = 2_500


class SarvamError(RuntimeError):
    """A safe, provider-neutral Sarvam failure code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
    ) -> dict[str, Any]: ...


class UrllibJsonTransport:
    """Minimal JSON transport with no dependency on the Sarvam SDK."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise SarvamError(f"SARVAM_HTTP_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SarvamError("SARVAM_UNAVAILABLE") from exc

        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SarvamError("SARVAM_MALFORMED_RESPONSE") from exc
        if not isinstance(decoded, dict):
            raise SarvamError("SARVAM_MALFORMED_RESPONSE")
        return decoded


@dataclass(frozen=True)
class TranslationResult:
    text: str
    source_language_code: str
    target_language_code: str


@dataclass(frozen=True)
class SpeechAudio:
    content: bytes
    content_type: str
    language_code: str


def normalize_language_code(value: str) -> str:
    """Normalize short language names to the BCP-47 codes Sarvam expects."""

    normalized = value.strip().replace("_", "-")
    short_codes = {
        "as": "as-IN",
        "bn": "bn-IN",
        "en": ENGLISH_LANGUAGE_CODE,
        "gu": "gu-IN",
        "hi": "hi-IN",
        "kn": "kn-IN",
        "ml": "ml-IN",
        "mr": "mr-IN",
        "ne": "ne-IN",
        "od": "od-IN",
        "pa": "pa-IN",
        "ta": "ta-IN",
        "te": "te-IN",
        "ur": "ur-IN",
    }
    return short_codes.get(normalized.casefold(), normalized)


def is_english_language(language_code: str) -> bool:
    return normalize_language_code(language_code).casefold() in {"en", "en-in"}


def is_probably_english(text: str) -> bool:
    """Safe local fallback used only when Sarvam is not configured.

    This deliberately errs toward requiring Sarvam for non-Latin input. It is
    not used when the provider is configured, where Sarvam performs detection.
    """

    allowed_symbols = set("₹$€£¥₽₩,.!?;:'\"()[]{}+-*/%&@#_\n\r\t ")
    return all(char.isascii() or char in allowed_symbols for char in text)


class SarvamClient:
    """Translation and TTS adapter backed by Sarvam's REST API."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = DEFAULT_SARVAM_ENDPOINT,
        timeout_s: float = DEFAULT_SARVAM_TIMEOUT_S,
        tts_model: str = DEFAULT_TTS_MODEL,
        tts_speaker: str = DEFAULT_TTS_SPEAKER,
        transport: JsonTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("SARVAM_API_KEY_REQUIRED")
        if timeout_s <= 0:
            raise ValueError("FKGRID_SARVAM_TIMEOUT_S_INVALID")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s
        self.tts_model = tts_model
        self.tts_speaker = tts_speaker
        self.transport = transport or UrllibJsonTransport()

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        transport: JsonTransport | None = None,
    ) -> SarvamClient | None:
        values = os.environ if environment is None else environment
        api_key = values.get("FKGRID_SARVAM_API_KEY") or values.get("SARVAM_API_KEY")
        if not api_key:
            return None
        try:
            timeout_s = float(values.get("FKGRID_SARVAM_TIMEOUT_S", str(DEFAULT_SARVAM_TIMEOUT_S)))
        except ValueError as exc:
            raise ValueError("FKGRID_SARVAM_TIMEOUT_S_INVALID") from exc
        return cls(
            api_key,
            endpoint=values.get("FKGRID_SARVAM_ENDPOINT", DEFAULT_SARVAM_ENDPOINT),
            timeout_s=timeout_s,
            tts_model=values.get("FKGRID_SARVAM_TTS_MODEL", DEFAULT_TTS_MODEL),
            tts_speaker=values.get("FKGRID_SARVAM_TTS_SPEAKER", DEFAULT_TTS_SPEAKER),
            transport=transport,
        )

    def _post(self, path: str, payload: Mapping[str, object]) -> dict[str, Any]:
        return self.transport.post_json(
            f"{self.endpoint}{path}",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "api-subscription-key": self.api_key,
                "User-Agent": "fkgrid-chat/1.0",
            },
            payload=payload,
            timeout_s=self.timeout_s,
        )

    def translate(
        self,
        text: str,
        *,
        source_language_code: str = "auto",
        target_language_code: str = ENGLISH_LANGUAGE_CODE,
    ) -> TranslationResult:
        value = text.strip()
        if not value:
            raise SarvamError("SARVAM_TRANSLATION_INPUT_EMPTY")
        if len(value) > MAX_TRANSLATION_CHARS:
            raise SarvamError("SARVAM_TRANSLATION_INPUT_TOO_LONG")

        source = (
            source_language_code
            if source_language_code == "auto"
            else normalize_language_code(source_language_code)
        )
        target = normalize_language_code(target_language_code)
        payload = {
            "input": value,
            "source_language_code": source,
            "target_language_code": target,
        }
        response = self._post("/translate", payload)
        translated_text = response.get("translated_text")
        detected_source = response.get("source_language_code")
        if not isinstance(translated_text, str) or not translated_text.strip():
            raise SarvamError("SARVAM_TRANSLATION_EMPTY")
        if not isinstance(detected_source, str) or not detected_source.strip():
            if source == "auto":
                raise SarvamError("SARVAM_LANGUAGE_NOT_DETECTED")
            detected_source = source
        return TranslationResult(
            text=translated_text.strip(),
            source_language_code=normalize_language_code(detected_source),
            target_language_code=target,
        )

    def synthesize(
        self,
        text: str,
        *,
        language_code: str = ENGLISH_LANGUAGE_CODE,
        speaker: str | None = None,
    ) -> SpeechAudio:
        value = text.strip()
        if not value:
            raise SarvamError("SARVAM_TTS_INPUT_EMPTY")
        if len(value) > MAX_TTS_CHARS:
            raise SarvamError("SARVAM_TTS_INPUT_TOO_LONG")
        target = normalize_language_code(language_code)
        response = self._post(
            "/text-to-speech",
            {
                "text": value,
                "target_language_code": target,
                "model": self.tts_model,
                "speaker": speaker or self.tts_speaker,
            },
        )
        audios = response.get("audios")
        if (
            not isinstance(audios, list)
            or not audios
            or not all(isinstance(item, str) for item in audios)
        ):
            raise SarvamError("SARVAM_TTS_AUDIO_MISSING")
        try:
            audio = b"".join(base64.b64decode(item, validate=True) for item in audios)
        except (binascii.Error, ValueError) as exc:
            raise SarvamError("SARVAM_TTS_AUDIO_INVALID") from exc
        if not audio:
            raise SarvamError("SARVAM_TTS_AUDIO_EMPTY")
        return SpeechAudio(content=audio, content_type="audio/wav", language_code=target)


@lru_cache(maxsize=1)
def default_client() -> SarvamClient | None:
    """Return the process-wide client without importing or storing a secret."""

    return SarvamClient.from_environment()


__all__ = [
    "DEFAULT_SARVAM_ENDPOINT",
    "DEFAULT_TTS_MODEL",
    "ENGLISH_LANGUAGE_CODE",
    "JsonTransport",
    "MAX_TRANSLATION_CHARS",
    "MAX_TTS_CHARS",
    "SarvamClient",
    "SarvamError",
    "SpeechAudio",
    "TranslationResult",
    "default_client",
    "is_english_language",
    "is_probably_english",
    "normalize_language_code",
]
