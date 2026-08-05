from __future__ import annotations

import base64
import unittest
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from fkgrid import api, orchestrator
from fkgrid.contracts import Action, QueryExtraction, Role, TurnRequest, TurnStatus
from fkgrid.memory import create_session, get_session
from fkgrid.sarvam import (
    ENGLISH_LANGUAGE_CODE,
    SarvamClient,
    SpeechAudio,
    TranslationResult,
)


class RecordingTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeout_s": timeout_s,
            }
        )
        return self.responses.pop(0)


class FakeSarvam:
    def __init__(self) -> None:
        self.translation_calls: list[dict[str, str]] = []
        self.tts_calls: list[dict[str, str | None]] = []

    def translate(
        self,
        text: str,
        *,
        source_language_code: str = "auto",
        target_language_code: str = ENGLISH_LANGUAGE_CODE,
    ) -> TranslationResult:
        self.translation_calls.append(
            {
                "text": text,
                "source_language_code": source_language_code,
                "target_language_code": target_language_code,
            }
        )
        if source_language_code == "auto":
            return TranslationResult(
                text="find a shirt",
                source_language_code="hi-IN",
                target_language_code=ENGLISH_LANGUAGE_CODE,
            )
        return TranslationResult(
            text="यह एक अच्छा विकल्प है।",
            source_language_code=ENGLISH_LANGUAGE_CODE,
            target_language_code="hi-IN",
        )

    def synthesize(
        self,
        text: str,
        *,
        language_code: str = ENGLISH_LANGUAGE_CODE,
        speaker: str | None = None,
    ) -> SpeechAudio:
        self.tts_calls.append({"text": text, "language_code": language_code, "speaker": speaker})
        return SpeechAudio(
            content=b"RIFF-test",
            content_type="audio/wav",
            language_code=language_code,
        )


class SarvamClientTests(unittest.TestCase):
    def test_translation_and_tts_use_the_rest_contract_without_exposing_key(self) -> None:
        audio = base64.b64encode(b"RIFF-test").decode("ascii")
        transport = RecordingTransport(
            [
                {"translated_text": "find a shirt", "source_language_code": "hi-IN"},
                {"audios": [audio]},
            ]
        )
        client = SarvamClient("test-only-key", transport=transport)

        translated = client.translate("शर्ट ढूंढो")
        speech = client.synthesize("यह एक शर्ट है।", language_code="hi-IN")

        self.assertEqual(translated.text, "find a shirt")
        self.assertEqual(translated.source_language_code, "hi-IN")
        self.assertEqual(speech.content, b"RIFF-test")
        self.assertEqual(transport.calls[0]["url"], "https://api.sarvam.ai/translate")
        self.assertEqual(transport.calls[1]["url"], "https://api.sarvam.ai/text-to-speech")
        self.assertEqual(transport.calls[0]["headers"]["api-subscription-key"], "test-only-key")
        self.assertNotIn("test-only-key", str(translated))

    def test_environment_factory_does_not_require_a_key_for_import_or_store_one_in_defaults(
        self,
    ) -> None:
        self.assertIsNone(SarvamClient.from_environment({}))
        client = SarvamClient.from_environment({"FKGRID_SARVAM_API_KEY": "test-only-key"})
        assert client is not None
        self.assertEqual(client.endpoint, "https://api.sarvam.ai")


class SarvamLanguageBoundaryTests(unittest.TestCase):
    def test_non_english_turn_is_translated_stored_and_localized_back(self) -> None:
        session_id = "sarvam-language-boundary"
        create_session(session_id)
        provider = FakeSarvam()

        with (
            patch.object(orchestrator, "default_client", return_value=provider),
            patch.object(
                orchestrator.llm,
                "extract_query",
                return_value=QueryExtraction(
                    action=Action.CHITCHAT, reply="This is a good option."
                ),
            ),
        ):
            result = orchestrator.handle_turn(
                TurnRequest(session_id=session_id, message="मुझे एक शर्ट चाहिए"),
            )

        self.assertEqual(result.status, TurnStatus.OK)
        self.assertEqual(result.language_code, "hi-IN")
        self.assertEqual(result.message, "यह एक अच्छा विकल्प है।")
        self.assertEqual(
            provider.translation_calls[0]["target_language_code"],
            ENGLISH_LANGUAGE_CODE,
        )
        self.assertEqual(provider.translation_calls[1]["target_language_code"], "hi-IN")
        state = get_session(session_id)
        assert state is not None
        self.assertEqual(state.language_code, "hi-IN")
        self.assertEqual(state.chat_history[0].language_code, "hi-IN")
        self.assertEqual(state.chat_history[0].canonical_content, "find a shirt")
        self.assertEqual(state.chat_history[0].role, Role.USER)
        self.assertEqual(state.chat_history[1].content, "यह एक अच्छा विकल्प है।")
        self.assertEqual(state.chat_history[1].canonical_content, "This is a good option.")


class SpeechSynthesisApiTests(unittest.TestCase):
    def test_synthesis_endpoint_returns_audio_bytes_in_requested_language(self) -> None:
        provider = FakeSarvam()
        with patch.object(api, "default_client", return_value=provider):
            response = TestClient(api.app).post(
                "/v1/speech/synthesize",
                json={"text": "यह एक अच्छा विकल्प है।", "language_code": "hi-IN"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"RIFF-test")
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["x-sarvam-language-code"], "hi-IN")
        self.assertEqual(provider.tts_calls[0]["language_code"], "hi-IN")

    def test_synthesis_endpoint_is_explicit_when_provider_is_not_configured(self) -> None:
        with patch.object(api, "default_client", return_value=None):
            response = TestClient(api.app).post(
                "/v1/speech/synthesize",
                json={"text": "Hello", "language_code": "en-IN"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "SARVAM_API_KEY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
