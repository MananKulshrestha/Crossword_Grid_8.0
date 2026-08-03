"""Opt-in speech-to-text boundary for the shopper chat UI.

Speech transcription is deliberately separate from the shopper-turn
orchestrator.  A normal text turn never constructs or calls this adapter.
"""

from .contracts import SpeechStatus, SpeechTranscription
from .deepinfra import (
    DEFAULT_DEEPINFRA_SPEECH_ENDPOINT,
    DEFAULT_SPEECH_MODEL_ALIAS,
    DeepInfraWhisperAdapter,
    UnavailableSpeechToText,
)
from .ports import SpeechToTextPort, SpeechTransport

__all__ = [
    "DEFAULT_DEEPINFRA_SPEECH_ENDPOINT",
    "DEFAULT_SPEECH_MODEL_ALIAS",
    "DeepInfraWhisperAdapter",
    "SpeechStatus",
    "SpeechToTextPort",
    "SpeechTranscription",
    "SpeechTransport",
    "UnavailableSpeechToText",
]
