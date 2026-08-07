"""Translation agent, run at the edges of a turn.

Goes through the same DeepInfra chat-completions endpoint as the query
extractor (see llm.py) - a Gemini or Gemma model alias, never Gemini's own
API directly. Detects whether the shopper's message is non-English or
code-mixed (e.g. Hinglish) and, if so, translates it to English before it
reaches the query extractor. Any user-facing reply text is translated back
to the shopper's original language/mix at the end of the turn.

One call in, one call out. On any failure (timeout, bad HTTP, malformed
response) this is best-effort: it raises TranslateError, and callers must
fall back to the original text rather than fail the whole turn - a
shopper writing in English must never be blocked by a translation outage.
"""

from __future__ import annotations

import os

from .config import ConfigError
from .llm import LLMError, _chat_completion

_MODEL_ALIAS = os.environ.get("FKGRID_TRANSLATE_MODEL_ALIAS")


class TranslateError(RuntimeError):
    """Raised on any translation-model failure. Callers must treat this as best-effort."""


_TO_ENGLISH_PROMPT = """\
You are a translation agent for a shopping chat assistant. Given the
shopper's raw message, decide its language and produce an English version.

- If the message is already fully in English, set "language" to "en" and
  "translated" to the message unchanged.
- If the message is in another language, or is code-mixed (e.g. Hinglish -
  Hindi written in Latin script mixed with English), set "language" to the
  BCP-47 code of the shopper's dominant non-English language (e.g. "hi" for
  Hindi/Hinglish, "ta" for Tamil, "bn" for Bengali) and "translated" to a
  natural, fluent English translation that preserves the shopper's intent
  exactly (product names, numbers, sizes, brand names) - do not add or drop
  meaning.

Output ONLY a JSON object matching this schema:
{"language": string, "translated": string}
"""

_TO_ENGLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "translated": {"type": "string"},
    },
    "required": ["language", "translated"],
    "additionalProperties": False,
}

_FROM_ENGLISH_PROMPT = """\
You are a translation agent for a shopping chat assistant. Translate the
given English text into the target language, matching the shopper's own
style (if the target language is "hi", prefer natural Hinglish - Hindi
meaning in Latin script mixed with common English shopping/product words -
to match how the shopper wrote to us). Preserve numbers, prices, sizes,
brand names, and product names exactly. Output ONLY a JSON object matching
this schema:
{"translated": string}
"""

_FROM_ENGLISH_SCHEMA = {
    "type": "object",
    "properties": {"translated": {"type": "string"}},
    "required": ["translated"],
    "additionalProperties": False,
}


def to_english(message: str) -> tuple[str, str]:
    """Returns (english_text, source_language). source_language is "en" when
    the message was already English (english_text == message in that case)."""

    try:
        payload = _chat_completion(_TO_ENGLISH_PROMPT, {"message": message}, _TO_ENGLISH_SCHEMA, _MODEL_ALIAS)
    except (LLMError, ConfigError) as exc:
        raise TranslateError(str(exc)) from exc

    language = payload.get("language")
    translated = payload.get("translated")
    if not isinstance(language, str) or not language:
        raise TranslateError("TRANSLATE_SCHEMA_INVALID")
    if not isinstance(translated, str) or not translated.strip():
        raise TranslateError("TRANSLATE_SCHEMA_INVALID")
    return translated, language


def from_english(text: str, target_language: str) -> str:
    """Translates English text into target_language. Callers should skip
    calling this entirely when target_language == "en"."""

    try:
        payload = _chat_completion(
            _FROM_ENGLISH_PROMPT,
            {"target_language": target_language, "text": text},
            _FROM_ENGLISH_SCHEMA,
            _MODEL_ALIAS,
        )
    except (LLMError, ConfigError) as exc:
        raise TranslateError(str(exc)) from exc

    translated = payload.get("translated")
    if not isinstance(translated, str) or not translated.strip():
        raise TranslateError("TRANSLATE_SCHEMA_INVALID")
    return translated
