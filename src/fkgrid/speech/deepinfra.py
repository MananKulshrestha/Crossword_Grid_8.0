"""DeepInfra Whisper large-v3-turbo adapter.

The adapter uses DeepInfra's native multipart speech endpoint.  It is lazy in
the important sense: constructing it performs no network work; only an HTTP
request to the dedicated speech route calls the provider.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import SpeechStatus, SpeechTranscription
from .ports import SpeechTransport

DEFAULT_SPEECH_MODEL_ALIAS = "openai/whisper-large-v3-turbo"
DEFAULT_DEEPINFRA_SPEECH_ENDPOINT = (
    "https://api.deepinfra.com/v1/inference/openai/whisper-large-v3-turbo"
)
DEFAULT_SPEECH_PROMPT_ID = "shopping_transcription_v1"
DEFAULT_SPEECH_PROMPT_VERSION = "1"
DEFAULT_MAX_AUDIO_BYTES = 20 * 1024 * 1024

SUPPORTED_AUDIO_TYPES = frozenset(
    {
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/opus",
        "audio/wav",
        "audio/webm",
        "audio/x-m4a",
        "audio/x-wav",
    }
)


class ShoppingTranscriptionPrompt:
    """Load the immutable, domain-specific Whisper prompt resource."""

    prompt_id = DEFAULT_SPEECH_PROMPT_ID
    prompt_version = DEFAULT_SPEECH_PROMPT_VERSION

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).with_name("prompts") / "transcription_v1.md"

    def text(self) -> str:
        value = self.path.read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError("SPEECH_PROMPT_EMPTY")
        return value


def _base_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().casefold()


def _filename_for_content_type(content_type: str) -> str:
    suffixes = {
        "audio/aac": "aac",
        "audio/flac": "flac",
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/opus": "opus",
        "audio/wav": "wav",
        "audio/webm": "webm",
        "audio/x-m4a": "m4a",
        "audio/x-wav": "wav",
    }
    return f"shopper-voice.{suffixes.get(_base_content_type(content_type), 'webm')}"


class UrllibMultipartTransport:
    """Small dependency-free multipart client for DeepInfra native inference."""

    def __init__(self, endpoint: str, api_key: str) -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    def post_multipart(
        self,
        *,
        fields: Mapping[str, str],
        audio: bytes,
        filename: str,
        content_type: str,
        timeout_ms: int,
    ) -> Mapping[str, object]:
        boundary = f"----fkgrid-speech-{secrets.token_hex(12)}"
        boundary_bytes = boundary.encode("ascii")
        body = bytearray()
        for name, value in fields.items():
            body.extend(b"--" + boundary_bytes + b"\r\n")
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        body.extend(b"--" + boundary_bytes + b"\r\n")
        body.extend(
            f'Content-Disposition: form-data; name="audio"; filename="{filename}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
        body.extend(audio)
        body.extend(b"\r\n--" + boundary_bytes + b"--\r\n")
        request = Request(
            self.endpoint,
            data=bytes(body),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urlopen(request, timeout=max(timeout_ms / 1000, 0.05)) as response:  # noqa: S310 - endpoint is injected
            parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("PROVIDER_RESPONSE_INVALID")
        return parsed


class DeepInfraWhisperAdapter:
    """Transcribe shopper audio with DeepInfra Whisper large-v3-turbo."""

    provider_name = "deepinfra"

    def __init__(
        self,
        transport: SpeechTransport,
        *,
        model_alias: str = DEFAULT_SPEECH_MODEL_ALIAS,
        prompt: ShoppingTranscriptionPrompt | None = None,
        max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
    ) -> None:
        if max_audio_bytes <= 0:
            raise ValueError("SPEECH_MAX_AUDIO_BYTES_INVALID")
        self.transport = transport
        self.model_alias = model_alias
        self.prompt = prompt or ShoppingTranscriptionPrompt()
        self.max_audio_bytes = max_audio_bytes

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        prompt: ShoppingTranscriptionPrompt | None = None,
    ) -> DeepInfraWhisperAdapter:
        values = os.environ if environment is None else environment
        api_key = values.get("DEEPINFRA_API_KEY") or values.get("FKGRID_SPEECH_API_KEY")
        if not api_key:
            raise ValueError("FKGRID_SPEECH_API_KEY_REQUIRED")
        endpoint = values.get("FKGRID_SPEECH_ENDPOINT", DEFAULT_DEEPINFRA_SPEECH_ENDPOINT)
        model_alias = values.get("FKGRID_SPEECH_MODEL_ALIAS", DEFAULT_SPEECH_MODEL_ALIAS)
        try:
            max_audio_bytes = int(
                values.get("FKGRID_SPEECH_MAX_AUDIO_BYTES", str(DEFAULT_MAX_AUDIO_BYTES))
            )
        except ValueError as exc:
            raise ValueError("FKGRID_SPEECH_MAX_AUDIO_BYTES_INVALID") from exc
        return cls(
            UrllibMultipartTransport(endpoint, api_key),
            model_alias=model_alias,
            prompt=prompt,
            max_audio_bytes=max_audio_bytes,
        )

    def _result(
        self,
        status: SpeechStatus,
        *,
        text: str = "",
        language: str = "en",
        latency_ms: int = 0,
        error_code: str | None = None,
    ) -> SpeechTranscription:
        return SpeechTranscription(
            status=status,
            text=text,
            language=language,
            model_alias=self.model_alias,
            provider_name=self.provider_name,
            latency_ms=max(0, latency_ms),
            prompt_id=self.prompt.prompt_id,
            prompt_version=self.prompt.prompt_version,
            error_code=error_code,
        )

    def transcribe(
        self,
        audio: bytes,
        content_type: str,
        *,
        language: str = "en",
        deadline_ms: int = 8_000,
    ) -> SpeechTranscription:
        started = time.perf_counter()
        mime = _base_content_type(content_type)
        if not audio:
            return self._result(SpeechStatus.INVALID_AUDIO, error_code="AUDIO_EMPTY")
        if mime not in SUPPORTED_AUDIO_TYPES:
            return self._result(
                SpeechStatus.INVALID_AUDIO,
                error_code="AUDIO_CONTENT_TYPE_UNSUPPORTED",
            )
        if len(audio) > self.max_audio_bytes:
            return self._result(
                SpeechStatus.INVALID_AUDIO,
                error_code="AUDIO_TOO_LARGE",
            )
        if deadline_ms <= 0:
            return self._result(SpeechStatus.TIMEOUT, error_code="SPEECH_DEADLINE_EXPIRED")
        normalized_language = language.casefold().split("-", 1)[0]
        if not normalized_language.isalpha() or not 2 <= len(normalized_language) <= 3:
            return self._result(
                SpeechStatus.INVALID_AUDIO,
                error_code="SPEECH_LANGUAGE_INVALID",
            )
        try:
            response = self.transport.post_multipart(
                fields={
                    "language": normalized_language,
                    "task": "transcribe",
                    "prompt": self.prompt.text(),
                },
                audio=audio,
                filename=_filename_for_content_type(mime),
                content_type=mime,
                timeout_ms=deadline_ms,
            )
            text = response.get("text")
            if not isinstance(text, str):
                raise ValueError("PROVIDER_RESPONSE_INVALID")
            text = " ".join(text.split())
            latency_ms = int((time.perf_counter() - started) * 1000)
            if not text:
                return self._result(
                    SpeechStatus.EMPTY_TRANSCRIPTION,
                    language=normalized_language,
                    latency_ms=latency_ms,
                    error_code="TRANSCRIPTION_EMPTY",
                )
            return self._result(
                SpeechStatus.OK,
                text=text,
                language=normalized_language,
                latency_ms=latency_ms,
            )
        except TimeoutError:
            return self._result(
                SpeechStatus.TIMEOUT,
                language=normalized_language,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="SPEECH_PROVIDER_TIMEOUT",
            )
        except HTTPError:
            return self._result(
                SpeechStatus.UNAVAILABLE,
                language=normalized_language,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="SPEECH_PROVIDER_HTTP_ERROR",
            )
        except URLError:
            return self._result(
                SpeechStatus.UNAVAILABLE,
                language=normalized_language,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="SPEECH_PROVIDER_UNAVAILABLE",
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._result(
                SpeechStatus.ERROR,
                language=normalized_language,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="SPEECH_PROVIDER_RESPONSE_INVALID",
            )
        except Exception:
            return self._result(
                SpeechStatus.UNAVAILABLE,
                language=normalized_language,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="SPEECH_PROVIDER_UNAVAILABLE",
            )


class UnavailableSpeechToText:
    """Explicit safe state when speech credentials are not configured."""

    provider_name = "unconfigured"

    def __init__(
        self,
        reason_code: str = "FKGRID_SPEECH_API_KEY_REQUIRED",
        model_alias: str = DEFAULT_SPEECH_MODEL_ALIAS,
    ) -> None:
        self.reason_code = reason_code
        self.model_alias = model_alias

    def transcribe(
        self,
        audio: bytes,
        content_type: str,
        *,
        language: str = "en",
        deadline_ms: int = 8_000,
    ) -> SpeechTranscription:
        return SpeechTranscription(
            status=SpeechStatus.UNAVAILABLE,
            language=language,
            model_alias=self.model_alias,
            provider_name=self.provider_name,
            error_code=self.reason_code,
        )
