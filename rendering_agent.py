"""
rendering_agent.py
--------------------
Makes the PREFERENCE-LOADING step of Track 7 (Rendering/Response)
agentic, instead of a static dict lookup.

Previously (rotation_logic.load_user_preferences): preferences were
pre-extracted offline (end of session, via preference_extraction.py)
and saved to a store, then just read back with a plain dict.get().
That's fast, but it can only ever know what a PAST background job
already decided was worth saving.

Now: before rendering the current response, a small agent is given a
tool to search this user's past conversation history directly, decide
IF and WHAT it needs to look up (given the current query), pull
relevant snippets, and extract preferences from them live -- so a
preference expressed two chats ago that was never explicitly saved
can still surface now, if it's relevant to the current request.

This sits ON TOP of rotation_logic.py -- it does not replace
build_prompt()/select_format(), it just produces a better-informed
`user_preferences` dict to hand into build_prompt().
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rotation_logic import build_prompt, DEFAULT_USER_PREFERENCES
from preference_extraction import merge_preference_updates

MEMORY_AGENT_SYSTEM_PROMPT = """You resolve a shopping-assistant user's
preferences before a response is rendered. You have one tool:
search_user_history(query) -- searches this specific user's past
conversations for relevant snippets.

Steps:
1. Given the current request, decide if past-conversation context could
   plausibly matter (e.g. they may have stated a format/tone preference
   before, even if it wasn't saved).
2. If so, call search_user_history with a short, targeted query.
3. Read the returned snippets. Only extract a preference if there is
   clear signal -- do not invent one from a vague or unrelated snippet.
4. Once done (with or without search), output ONLY this JSON, nothing
   else, no markdown fences:
{
  "preferred_format": "comparison_table" | "ranked_list" | "grouped_short" | "grouped_by_feature" | "single_spotlight" | null,
  "tone": "concise" | "detailed" | null,
  "avoid": ["..."],
  "language_notes": "short freeform note, or null"
}"""

MEMORY_SEARCH_TOOL_SCHEMA = {
    "name": "search_user_history",
    "description": "Search this user's past conversation history for relevant snippets (e.g. stated preferences, past complaints, phrasing style).",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}


class RenderingAgent:
    """
    Wraps rotation_logic.build_prompt with an agentic preference-
    resolution step. `history_backend` is any callable
    (user_id, query) -> list[str] of relevant past snippets --
    swap the mock for real search over your conversation-history store
    (e.g. a vector search over past transcripts, or Claude's own
    conversation-search tool if running inside that product).
    """

    def __init__(self, client, history_backend, model: str = "claude-sonnet-4-5",
                 max_search_calls: int = 3):
        self.client = client
        self.history_backend = history_backend
        self.model = model
        self.max_search_calls = max_search_calls

    def resolve_preferences(self, user_id: str, current_query: str,
                             cached_preferences: dict | None = None) -> dict:
        """
        Runs the agentic search loop, merges any newly-surfaced signal
        with whatever was already cached from prior offline extraction
        (cache wins on conflict for fields the agent didn't touch;
        freshly-surfaced signal is treated as at least as current).
        """
        cached_preferences = cached_preferences or dict(DEFAULT_USER_PREFERENCES)

        messages = [{"role": "user", "content":
            f"Current user request: {current_query!r}\n"
            f"Already-known preferences (may be stale/incomplete): "
            f"{json.dumps(cached_preferences)}"}]

        search_calls_made = []
        for _ in range(self.max_search_calls + 1):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                system=MEMORY_AGENT_SYSTEM_PROMPT,
                tools=[MEMORY_SEARCH_TOOL_SCHEMA],
                messages=messages,
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                text = " ".join(b.text for b in response.content if b.type == "text").strip()
                text = text.removeprefix("```json").removesuffix("```").strip()
                try:
                    fresh = json.loads(text)
                except json.JSONDecodeError:
                    fresh = {}
                return merge_preference_updates(cached_preferences, fresh)

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_use_blocks:
                query = block.input.get("query", "")
                snippets = self.history_backend(user_id, query)
                search_calls_made.append(query)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(snippets),
                })
            messages.append({"role": "user", "content": tool_results})

        # Hit max_search_calls without a final answer -- fall back to cache only
        return cached_preferences

    def render(self, products: list, intent: dict, user_id: str, current_query: str,
               confidence: float = 1.0, missing_slots: list | None = None,
               cached_preferences: dict | None = None) -> dict:
        """One-shot convenience: resolve preferences agentically, then
        build the render prompt via the existing deterministic rotation
        logic. This is the function your final pipeline calls."""
        resolved_prefs = self.resolve_preferences(user_id, current_query, cached_preferences)
        result = build_prompt(
            products=products, intent=intent, confidence=confidence,
            missing_slots=missing_slots, user_preferences=resolved_prefs,
        )
        result["resolved_preferences"] = resolved_prefs
        return result


# ---------------------------------------------------------------------------
# Mock history backend (stand-in for real conversation-history search)
# ---------------------------------------------------------------------------

MOCK_CONVERSATION_HISTORY = {
    "user_42": [
        "can you just show me a table next time instead of paragraphs",
        "yeah that shirt was fine, add it",
        "keep it short please, that was too long",
        "do you have this in blue?",
    ],
}


def mock_history_backend(user_id: str, query: str) -> list[str]:
    """Dumb keyword-overlap search over a mock transcript store.
    Swap for real semantic search over your actual chat history DB."""
    history = MOCK_CONVERSATION_HISTORY.get(user_id, [])
    query_words = set(query.lower().split())
    scored = [(len(query_words & set(turn.lower().split())), turn) for turn in history]
    scored.sort(key=lambda x: -x[0])
    return [turn for score, turn in scored if score > 0][:3]


# ---------------------------------------------------------------------------
# Mock-client harness -- proves the agentic search->extract loop works
# without a live API key.
# ---------------------------------------------------------------------------

class _Block:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class _Response:
    def __init__(self, content):
        self.content = content


class MockMemoryClient:
    """Scripts: turn 1 -> search_user_history('preferred format'),
    turn 2 -> final JSON extracted from the returned snippets."""

    def __init__(self):
        self._step = 0

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer._step += 1
            if self.outer._step == 1:
                return _Response([_Block(
                    "tool_use", id="call_1", name="search_user_history",
                    input={"query": "preferred response format and tone"},
                )])
            else:
                return _Response([_Block(
                    "text",
                    text=json.dumps({
                        "preferred_format": "comparison_table",
                        "tone": "concise",
                        "avoid": [],
                        "language_notes": None,
                    }),
                )])

    @property
    def messages(self):
        return self._Messages(self)


if __name__ == "__main__":
    agent = RenderingAgent(client=MockMemoryClient(), history_backend=mock_history_backend)

    products = [
        {"id": "p1", "name": "Van Heusen Cotton Formal Shirt", "price": 1299,
         "rating": 4.3, "tags": ["cotton", "formal"]},
        {"id": "p2", "name": "Allen Solly Slim Fit Shirt", "price": 1450,
         "rating": 4.1, "tags": ["cotton", "formal"]},
    ]
    intent = {"category": "shirts", "price_max": 1500, "size": "M"}

    result = agent.render(
        products=products, intent=intent, user_id="user_42",
        current_query="show me formal shirts under 1500",
    )

    print("Resolved preferences (agentically, from past-chat search):")
    print(json.dumps(result["resolved_preferences"], indent=2))
    print("\nFormat selected:", result["format"])
    print("\n--- Final prompt (with agent-surfaced preferences applied) ---\n")
    print(result["prompt"])
