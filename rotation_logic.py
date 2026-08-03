"""
rotation_logic.py
------------------
Deterministic markdown-format rotation for the shopping assistant's
Rendering/Response layer (Track 7 in the architecture doc).

Purpose:
    Prevents the LLM from producing visually/structurally repetitive
    markdown across turns by selecting the RESPONSE SHAPE in code
    (testable, versionable) rather than hoping the model varies itself.

This module does NOT call any LLM. It only decides:
    1. which format to use (select_format)
    2. what instruction text + prompt to hand to whichever
       generation model you plug in next (build_prompt)

Any of generate_pegasus.py / generate_qwen.py / generate_ollama.py
can import build_prompt() from here so all three are compared on the
exact same instruction set -- the fair-comparison requirement for the
benchmark in evaluate_diversity.py.
"""

import json
import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 1. Format catalogue
# ---------------------------------------------------------------------------

FORMAT_INSTRUCTIONS = {
    "single_spotlight": """
Present this one product as a short 2-3 sentence paragraph followed by a
compact spec table (price, rating, key tags). Do not use a greeting or
generic intro. End with a specific reason it matches the user's query --
not a generic closing question.
""".strip(),

    "comparison_table": """
Present these products as a markdown table with columns: Name, Price,
Rating, Best For. After the table, add one line per product highlighting
its single most distinguishing feature relative to the others. No intro
greeting.
""".strip(),

    "grouped_short": """
Group these products into 1-2 short paragraphs based on shared traits
(e.g. price tier, use case). Mention product names inline, not as a
list. Avoid repeating the same sentence structure across paragraphs.
""".strip(),

    "grouped_by_feature": """
Group these products under 2-3 short bolded subheadings based on what
differentiates them (e.g. "Best value", "Best rated", "Best for [use
case]"). 1-2 products per group, one line each.
""".strip(),

    "ranked_list": """
Present as a numbered list ranked by relevance to the query. Each item:
name + one distinguishing detail. No repeated sentence templates across
items. No generic closing line.
""".strip(),

    "no_results": """
State plainly that no exact matches were found. Suggest 1-2 concrete
ways to adjust the query (based on the intent JSON) rather than a
generic "try again" message.
""".strip(),

    "clarification": """
Do NOT list any products yet. Ask exactly one concise clarifying question
that resolves the single most important missing attribute (see
`missing_slots` in the intent JSON). Do not apologize or over-explain why
you're asking.
""".strip(),
}

STANDING_RULES = """
Rules that always apply:
- Never start with a greeting or filler phrase like "Great choice!" or "Here's what I found".
- Never end with a generic question unless it adds real value to this specific case.
- Vary sentence openers across items -- don't start every product line the same way.
- Base every claim only on the provided JSON. Do not invent specs, prices, or reviews.
- Never alter numeric values (price, rating, stock count) from the source JSON.
""".strip()


# ---------------------------------------------------------------------------
# 2. Format selector (deterministic given the same inputs -> testable)
# ---------------------------------------------------------------------------

def select_format(products: list, intent: dict, confidence: float = 1.0,
                   missing_slots: list | None = None, seed: int | None = None) -> str:
    """
    Chooses a presentation format based on real signal:
      - confidence / missing slots (Track 3 & 4 outputs) gate clarification
      - product count drives table vs. list vs. grouped layout
      - light randomization only within formats that are equally valid,
        so repeated identical queries don't always render identically

    seed: pass a fixed int in tests for reproducibility.
    """
    rng = random.Random(seed)
    missing_slots = missing_slots or []

    # Gate 1: low confidence or missing required info -> ask, don't list
    if missing_slots or confidence < 0.5:
        return "clarification"

    n = len(products)

    if n == 0:
        return "no_results"
    elif n == 1:
        return "single_spotlight"
    elif n <= 4:
        return rng.choice(["comparison_table", "grouped_short"])
    else:
        return rng.choice(["grouped_by_feature", "ranked_list"])


# ---------------------------------------------------------------------------
# 3. Prompt assembly (shared across all three model backends)
# ---------------------------------------------------------------------------

def build_prompt(products: list, intent: dict, confidence: float = 1.0,
                  missing_slots: list | None = None, seed: int | None = None,
                  user_preferences: dict | None = None) -> dict:
    """
    Returns a dict: {"format": <name>, "prompt": <full prompt string>}
    so downstream scripts can log which format fired for which query.

    user_preferences: output of load_user_preferences() below -- a
    persistent, cross-session profile for this specific user. When
    present, it can (a) override/bias which format gets picked and
    (b) get appended to the prompt as extra standing instructions
    scoped to this one user, on top of the global STANDING_RULES.
    """
    missing_slots = missing_slots or []
    user_preferences = user_preferences or {}

    # A stored format preference takes priority over the normal
    # count-based selection logic, as long as it's still valid for
    # the current situation (e.g. don't force a table for 0 products).
    forced_format = user_preferences.get("preferred_format")
    if forced_format in FORMAT_INSTRUCTIONS and not missing_slots and confidence >= 0.5 and products:
        fmt = forced_format
    else:
        fmt = select_format(products, intent, confidence, missing_slots, seed)

    instruction = FORMAT_INSTRUCTIONS[fmt]

    payload = {
        "intent": intent,
        "missing_slots": missing_slots,
        "confidence": confidence,
        "products": products,
    }

    user_rules = format_user_preference_rules(user_preferences)

    prompt = f"""{STANDING_RULES}
{user_rules}

Format instruction for this response:
{instruction}

Context (JSON -- do not invent data outside this):
{json.dumps(payload, indent=2)}

Output valid markdown only. No preamble, no explanation of what you're doing.
"""
    return {"format": fmt, "prompt": prompt}


# ---------------------------------------------------------------------------
# 5. Cross-session user preference layer
# ---------------------------------------------------------------------------
#
# This is DIFFERENT from RecentOpenersLog above (which is short-term,
# within-session, and about avoiding repeated phrasing). This section
# is about LONG-TERM, cross-conversation preferences a specific user
# has shown -- e.g. "always show tables, never paragraphs", "keep
# responses short", "don't use emojis". See README.md for the full
# extraction -> storage -> injection pipeline this plugs into.

DEFAULT_USER_PREFERENCES = {
    "preferred_format": None,      # e.g. "comparison_table" | "ranked_list" | None
    "tone": None,                  # e.g. "concise" | "detailed" | None
    "avoid": [],                   # e.g. ["emojis", "exclamation marks"]
    "language_notes": None,        # e.g. "mixes Hindi and English, keep replies simple"
}


def load_user_preferences(user_id: str, store: dict | None = None) -> dict:
    """
    Loads a user's persistent preference profile. `store` is any
    dict-like key-value backend (swap for Redis/SQLite/etc. in
    production) -- passed in explicitly here so this stays a pure,
    testable function rather than reaching out to a global DB itself.

    Falls back to DEFAULT_USER_PREFERENCES for a new/unknown user.
    """
    store = store if store is not None else {}
    stored = store.get(user_id, {})
    merged = {**DEFAULT_USER_PREFERENCES, **stored}
    return merged


def save_user_preferences(user_id: str, preferences: dict, store: dict) -> None:
    """Persists an updated preference profile. Merges rather than
    overwrites, so a partial update doesn't wipe unrelated fields."""
    existing = store.get(user_id, {})
    store[user_id] = {**existing, **preferences}


def format_user_preference_rules(user_preferences: dict) -> str:
    """Turns a preference profile into extra prompt instructions,
    scoped to this one user, appended after the global STANDING_RULES."""
    if not user_preferences:
        return ""

    lines = []
    if user_preferences.get("tone"):
        lines.append(f"- This user prefers a {user_preferences['tone']} tone.")
    if user_preferences.get("avoid"):
        avoid_list = ", ".join(user_preferences["avoid"])
        lines.append(f"- Avoid: {avoid_list}.")
    if user_preferences.get("language_notes"):
        lines.append(f"- {user_preferences['language_notes']}")

    if not lines:
        return ""

    return "\nUser-specific preferences (this user only):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Recent-openers tracker (feeds the "diversify pass" scripts)
# ---------------------------------------------------------------------------

@dataclass
class RecentOpenersLog:
    """Rolling log of sentence openers used in recent responses, so a
    rewrite/diversify pass can be told what NOT to repeat."""
    max_size: int = 15
    _log: list = field(default_factory=list)

    def record(self, markdown_text: str) -> None:
        first_line = next((l.strip() for l in markdown_text.splitlines() if l.strip()), "")
        opener = " ".join(first_line.split()[:6])  # first ~6 words
        if opener:
            self._log.append(opener)
            self._log = self._log[-self.max_size:]

    def recent(self) -> list:
        return list(self._log)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_products = [
        {"id": "p1", "name": "Van Heusen Cotton Formal Shirt", "price": 1299, "rating": 4.3,
         "tags": ["cotton", "formal", "slim-fit"]},
        {"id": "p2", "name": "Allen Solly Slim Fit Shirt", "price": 1450, "rating": 4.1,
         "tags": ["cotton", "formal", "wrinkle-free"]},
    ]
    sample_intent = {"category": "shirts", "price_max": 1500, "size": "M", "use_case": "wedding"}

    result = build_prompt(sample_products, sample_intent, confidence=0.9, seed=42)
    print("Selected format:", result["format"])
    print("---")
    print(result["prompt"])
