"""
preference_extraction.py
--------------------------
Extracts DURABLE user preferences from past conversation transcripts
so they can be saved via rotation_logic.save_user_preferences() and
re-applied to every future response for that user (see README.md for
the full pipeline diagram).

This is a separate file from rotation_logic.py on purpose: rotation
logic stays a pure, dependency-free module (easy to unit test), while
extraction is the one piece that actually needs to call an LLM.

Two ways to trigger extraction -- use either or both:

  1. EXPLICIT signal: user directly states a preference in a message
     ("just show me a table next time", "keep it short", "no emojis").
     This can often be caught with cheap keyword/regex rules -- no LLM
     call needed. See `extract_explicit_preferences()`.

  2. IMPLICIT signal: preference is inferred from behavior across a
     transcript (e.g. user always clicks/responds well to table-format
     answers, never engages with grouped-paragraph answers). This
     needs an LLM pass over the transcript. See
     `extract_implicit_preferences_via_llm()`.

Run extraction as a background/end-of-session job, NOT on every turn --
it's a summarization task, not a per-turn one.
"""

import json
import re

# Keywords that map fairly reliably to explicit preferences without
# needing an LLM call at all -- cheap first pass.
FORMAT_KEYWORDS = {
    "comparison_table": [r"\btable\b", r"\bcompare\b.*\btable\b"],
    "ranked_list": [r"\bnumbered list\b", r"\branked list\b"],
    "grouped_short": [r"\bparagraph\b", r"\bshort summary\b"],
}
TONE_KEYWORDS = {
    "concise": [r"\bkeep it short\b", r"\bbe brief\b", r"\btoo long\b", r"\bshorter\b"],
    "detailed": [r"\bmore detail\b", r"\bexplain more\b", r"\btell me more\b"],
}
AVOID_KEYWORDS = {
    "emojis": [r"\bno emojis?\b", r"\bstop (?:using|with).*\bemojis?\b", r"\btoo many emojis?\b"],
    "exclamation marks": [r"\btoo many exclamation\b", r"\bstop being so enthusiastic\b"],
}


def extract_explicit_preferences(user_message: str) -> dict:
    """
    Cheap, deterministic, no LLM call. Run this on every user message --
    if it matches, merge the result into stored preferences immediately
    rather than waiting for an end-of-session LLM pass.
    """
    text = user_message.lower()
    found = {}

    for fmt, patterns in FORMAT_KEYWORDS.items():
        if any(re.search(p, text) for p in patterns):
            found["preferred_format"] = fmt

    for tone, patterns in TONE_KEYWORDS.items():
        if any(re.search(p, text) for p in patterns):
            found["tone"] = tone

    avoid = [label for label, patterns in AVOID_KEYWORDS.items()
             if any(re.search(p, text) for p in patterns)]
    if avoid:
        found["avoid"] = avoid

    return found


EXTRACTION_SYSTEM_PROMPT = """You analyze a shopping-assistant conversation
transcript and identify DURABLE user preferences -- things likely to hold
true in future conversations, not just this one.

Only extract a preference if there is clear signal (explicit statement,
or a repeated pattern across multiple turns). Do not guess from a single
ambiguous turn.

Return ONLY valid JSON matching this shape, with null/[] for anything
with no signal:
{
  "preferred_format": "comparison_table" | "ranked_list" | "grouped_short" | "grouped_by_feature" | "single_spotlight" | null,
  "tone": "concise" | "detailed" | null,
  "avoid": ["..."],
  "language_notes": "short freeform note, or null"
}
No preamble, no explanation, no markdown fences -- JSON only."""


def extract_implicit_preferences_via_llm(conversation_log: list[dict], client) -> dict:
    """
    conversation_log: list of {"role": "user"|"assistant", "content": str}
    client: any object exposing .messages.create(...) in the Anthropic
    Messages API shape -- passed in so this file has no hard SDK
    dependency and stays swappable/testable.

    Run this at end-of-session (or periodically), not per-turn.
    """
    transcript_text = "\n".join(f"{turn['role']}: {turn['content']}" for turn in conversation_log)

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Transcript:\n\n{transcript_text}"}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json|```$", "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fail safe: no preference update rather than a crash/bad write
        return {}


def merge_preference_updates(*updates: dict) -> dict:
    """
    Combines multiple preference dicts (e.g. explicit + implicit passes)
    into one update. Later dicts win on scalar fields; `avoid` lists are
    unioned rather than overwritten.
    """
    merged = {}
    for update in updates:
        for key, value in update.items():
            if key == "avoid":
                merged["avoid"] = list(set(merged.get("avoid", [])) | set(value or []))
            elif value not in (None, ""):
                merged[key] = value
    return merged


if __name__ == "__main__":
    # Explicit-preference pass needs no LLM/network -- runs standalone.
    examples = [
        "can you just show me a table next time instead of paragraphs",
        "keep it short please, that was too long",
        "stop using so many emojis",
    ]
    for msg in examples:
        print(msg, "->", extract_explicit_preferences(msg))
