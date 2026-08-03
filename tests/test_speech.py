from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from fkgrid.agentic.gateway import FakeModelGateway, Gemma4ModelAdapter
from fkgrid.api.main import create_app
from fkgrid.api.runtime import ApiRuntime
from fkgrid.speech import (
    DEFAULT_SPEECH_MODEL_ALIAS,
    DeepInfraWhisperAdapter,
    SpeechStatus,
    SpeechTranscription,
)


class RecordingTransport:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {"text": "  Find a black T-shirt in size M  "}
        self.call: dict[str, object] | None = None

    def post_multipart(self, **kwargs: object) -> dict[str, object]:
        self.call = kwargs
        return self.response


class FakeSpeechToText:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(
        self,
        audio: bytes,
        content_type: str,
        *,
        language: str = "en",
        deadline_ms: int = 8_000,
    ) -> SpeechTranscription:
        self.calls += 1
        return SpeechTranscription(
            status=SpeechStatus.OK,
            text="Find a black T-shirt in size M",
            language=language,
            model_alias=DEFAULT_SPEECH_MODEL_ALIAS,
            provider_name="fake-speech",
            latency_ms=2,
        )


class SpeechAdapterTests(unittest.TestCase):
    def test_shopping_prompt_and_transcription_fields_are_sent_to_provider(self) -> None:
        transport = RecordingTransport()
        adapter = DeepInfraWhisperAdapter(transport)

        result = adapter.transcribe(
            b"audio",
            "audio/webm;codecs=opus",
            language="en-IN",
            deadline_ms=1200,
        )

        self.assertEqual(result.status, SpeechStatus.OK)
        self.assertEqual(result.text, "Find a black T-shirt in size M")
        self.assertEqual(result.model_alias, DEFAULT_SPEECH_MODEL_ALIAS)
        assert transport.call is not None
        self.assertEqual(transport.call["filename"], "shopper-voice.webm")
        self.assertEqual(transport.call["content_type"], "audio/webm")
        self.assertEqual(transport.call["timeout_ms"], 1200)
        fields = transport.call["fields"]
        assert isinstance(fields, dict)
        self.assertEqual(fields["language"], "en")
        self.assertEqual(fields["task"], "transcribe")
        self.assertIn("shopping vocabulary", str(fields["prompt"]))
        self.assertIn("cart", str(fields["prompt"]))

    def test_invalid_audio_fails_before_provider_call(self) -> None:
        transport = RecordingTransport()
        adapter = DeepInfraWhisperAdapter(transport, max_audio_bytes=4)

        empty = adapter.transcribe(b"", "audio/webm")
        too_large = adapter.transcribe(b"12345", "audio/webm")
        unsupported = adapter.transcribe(b"audio", "text/plain")

        self.assertEqual(empty.error_code, "AUDIO_EMPTY")
        self.assertEqual(too_large.error_code, "AUDIO_TOO_LARGE")
        self.assertEqual(unsupported.error_code, "AUDIO_CONTENT_TYPE_UNSUPPORTED")
        self.assertIsNone(transport.call)

    def test_environment_factory_requires_a_runtime_secret_and_keeps_model_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "FKGRID_SPEECH_API_KEY_REQUIRED"):
            DeepInfraWhisperAdapter.from_environment(environment={})

        adapter = DeepInfraWhisperAdapter.from_environment(
            environment={"DEEPINFRA_API_KEY": "test-only"}
        )
        self.assertEqual(adapter.model_alias, DEFAULT_SPEECH_MODEL_ALIAS)
        self.assertEqual(
            adapter.transport.endpoint,
            "https://api.deepinfra.com/v1/inference/openai/whisper-large-v3-turbo",
        )  # type: ignore[attr-defined]


class SpeechApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.speech = FakeSpeechToText()
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
            speech_to_text=self.speech,
        )
        self.client = TestClient(create_app(runtime))

    def test_speech_route_returns_transcript_and_normal_text_turn_does_not_call_it(self) -> None:
        response = self.client.post(
            "/v1/speech/transcriptions",
            content=b"audio-bytes",
            headers={"content-type": "audio/webm"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "Find a black T-shirt in size M")
        self.assertEqual(self.speech.calls, 1)

        session = self.client.post("/v1/sessions", json={"session_id": "text-only"})
        self.assertEqual(session.status_code, 201)
        turn = self.client.post(
            "/v1/sessions/text-only/turns",
            json={"message": "show my cart", "idempotency_key": "text-only-key"},
        )
        self.assertEqual(turn.status_code, 200)
        self.assertEqual(self.speech.calls, 1)

    def test_speech_route_validates_raw_audio_without_multipart_dependency(self) -> None:
        missing_type = self.client.post("/v1/speech/transcriptions", content=b"audio")
        self.assertEqual(missing_type.status_code, 415)
        empty = self.client.post(
            "/v1/speech/transcriptions",
            content=b"",
            headers={"content-type": "audio/webm"},
        )
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(self.speech.calls, 0)

    def test_swagger_voice_button_is_opt_in_and_review_first(self) -> None:
        docs = self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("fkgrid-chat-speech", docs.text)
        self.assertIn("/v1/speech/transcriptions", docs.text)
        self.assertIn("review and press Send", docs.text)


if __name__ == "__main__":
    unittest.main()
