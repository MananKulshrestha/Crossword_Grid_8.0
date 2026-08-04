# Rotation Logic & User Preference Memory

Documentation for `rotation_logic.py` and `preference_extraction.py` --
the Track 7 (Rendering/Response) module that (a) rotates markdown
format so responses don't sound templated, and (b) remembers what a
specific user has previously preferred so that gets applied
automatically in future conversations, without them repeating
themselves.

## The two layers, and why they're separate

| | Scope | Lives in | Resets when? |
|---|---|---|---|
| **Recent openers** | Within one session | `RecentOpenersLog` in `rotation_logic.py` | New session |
| **User preferences** | Across all sessions, per user | `preference_extraction.py` + a persistent store you plug in | Never (until explicitly changed) |

These solve different problems. Recent-openers stops turn 3 from
sounding like turn 1 *in the same chat*. User preferences is what
lets a returning user get a table-format response by default because
they said "I prefer tables" three conversations ago -- that's the part
you asked about.

## How a past-conversation preference gets into a future instruction

```
┌─────────────────┐   ┌───────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ Past conversation│──▶│ Preference         │──▶│ Persistent store  │──▶│ build_prompt()   │
│ (this session)    │   │ extraction         │   │ (per user_id)     │   │ (next session)   │
└─────────────────┘   └───────────────────┘   └──────────────────┘   └─────────────────┘
   user says              explicit: regex           {"user_123":         STANDING_RULES +
   "just show me            (no LLM, instant)         {"preferred_format":  user-specific rules
   a table next             implicit: LLM pass          "comparison_table", get appended to
   time"                    (end of session)            "tone": "concise"}}  every prompt
```

### Step 1 — Capture the signal (two tiers)

**Explicit (cheap, instant, no LLM call)** — run on every user message:
```python
from preference_extraction import extract_explicit_preferences

update = extract_explicit_preferences("can you just show me a table next time")
# -> {"preferred_format": "comparison_table"}
```
This catches direct statements immediately, so a user doesn't even
need to wait until their next session to see the effect.

**Implicit (needs an LLM pass, run once per session/periodically)** —
for preferences that show up as a *pattern* across a transcript rather
than one explicit sentence (e.g. the user consistently re-asks for
shorter answers without ever using the word "short"):
```python
from preference_extraction import extract_implicit_preferences_via_llm

update = extract_implicit_preferences_via_llm(conversation_log, client)
```
Run this as an end-of-session background job, not per-turn -- it's a
summarization task over the whole transcript, so it doesn't need to
block the live response.

### Step 2 — Merge and persist

```python
from preference_extraction import merge_preference_updates
from rotation_logic import save_user_preferences

combined = merge_preference_updates(explicit_update, implicit_update)
save_user_preferences(user_id="user_123", preferences=combined, store=my_db)
```
`store` is any dict-like backend -- swap in Redis, a SQLite table
keyed by `user_id`, or a column on your existing user profile table.
`save_user_preferences` merges rather than overwrites, so an update to
`tone` doesn't wipe a previously-saved `preferred_format`.

### Step 3 — Load and inject on the user's NEXT message (any future session)

```python
from rotation_logic import load_user_preferences, build_prompt

prefs = load_user_preferences(user_id="user_123", store=my_db)
result = build_prompt(products, intent, confidence=0.9, user_preferences=prefs)
```

Two things happen automatically inside `build_prompt`:
1. If `prefs["preferred_format"]` is set and still valid for the
   current situation (there are products, confidence is high enough,
   no missing slots), it **overrides** the normal count-based format
   selection.
2. `tone`, `avoid`, and `language_notes` get appended to the prompt as
   a `User-specific preferences (this user only)` block, right after
   the global `STANDING_RULES` -- so the model sees both the
   universal rules and this user's personal ones in the same prompt.

## `rotation_logic.py` reference

| Function | Purpose |
|---|---|
| `select_format(products, intent, confidence, missing_slots, seed)` | Deterministic format choice based on product count + confidence/missing-slot gating. |
| `build_prompt(products, intent, confidence, missing_slots, seed, user_preferences)` | Assembles the full prompt: standing rules + user rules + format instruction + JSON context. |
| `RecentOpenersLog` | Rolling log of recent response openers, for within-session anti-repetition (separate from user preferences). |
| `load_user_preferences(user_id, store)` | Reads a user's saved preference profile, falling back to defaults for new users. |
| `save_user_preferences(user_id, preferences, store)` | Persists a preference update, merging with existing values. |
| `format_user_preference_rules(preferences)` | Turns a preference dict into the extra prompt text injected into `build_prompt`. |

## `preference_extraction.py` reference

| Function | Purpose |
|---|---|
| `extract_explicit_preferences(user_message)` | Regex-based, no LLM call. Run on every message. |
| `extract_implicit_preferences_via_llm(conversation_log, client)` | LLM-based, run at end of session. Returns the same JSON shape as the explicit extractor. |
| `merge_preference_updates(*updates)` | Combines multiple preference dicts; unions `avoid` lists, last-value-wins on scalars. |

## Preference schema

```json
{
  "preferred_format": "comparison_table",
  "tone": "concise",
  "avoid": ["emojis"],
  "language_notes": "mixes Hindi and English, keep replies simple"
}
```

All fields are optional / nullable -- a new user starts with all
`null`/`[]` (see `DEFAULT_USER_PREFERENCES`) and `build_prompt` behaves
identically to having no preference layer at all until something gets
learned.

## Where this fits your architecture

This is downstream of **Track 3 (Intent & Delta)** and feeds directly
into **Track 7 (Rendering/Response)** — it does not touch retrieval
(Track 2) or grounding (Track 4) at all. A user's stored preference
only ever changes *how* an already-grounded answer is presented, never
*what* facts appear in it, so it carries no additional hallucination
risk.

## Upgrade: making preference lookup itself agentic (`rendering_agent.py`)

Everything above (`load_user_preferences` reading a static store) still
works and is the cheap default. `rendering_agent.py` replaces *that one
step* with an agent that can actively search past conversation history
at render time, instead of only knowing what a previous offline
extraction job already decided to save.

**Why this matters:** the static pipeline can only surface a preference
if `extract_implicit_preferences_via_llm` already ran on it in a past
session and saved it. If a user mentioned a preference in passing three
chats ago and it was never flagged, the static store has no idea it
exists. An agent with a search tool can go find it live, on demand,
scoped to what's actually relevant to the *current* request.

```
current query ──▶ RenderingAgent.resolve_preferences()
                        │
                        ▼
            LLM decides: "is past context relevant here?"
                        │
                 ┌──────┴──────┐
                 │ yes          │ no
                 ▼              ▼
     search_user_history(query)   skip straight to cached prefs
                 │
                 ▼
     relevant snippets returned
                 │
                 ▼
     LLM extracts preferences from snippets
     (merged with whatever was already cached)
                 │
                 ▼
     rotation_logic.build_prompt(user_preferences=resolved)
```

```python
from rendering_agent import RenderingAgent, mock_history_backend

agent = RenderingAgent(client=my_anthropic_client, history_backend=mock_history_backend)

result = agent.render(
    products=products,
    intent=intent,
    user_id="user_42",
    current_query="show me formal shirts under 1500",
    cached_preferences=maybe_previously_saved_prefs,  # optional, agent can refine it
)
# result["prompt"] -> ready for your generation model
# result["resolved_preferences"] -> what the agent found this turn
```

`history_backend` is a plug point: swap `mock_history_backend` (dumb
keyword overlap over an in-memory dict) for real semantic search over
your actual stored conversation transcripts — the agent loop and
extraction logic don't change either way.

**Trade-off vs. the static path:** this adds an LLM call (or two, if it
searches) to every render, vs. a free dict lookup. Reasonable default:
use `load_user_preferences` (static, free) as a fast path, and only
invoke `RenderingAgent` when the static store is empty/stale for a
given field, or on some cadence (e.g. first message of a new session)
rather than every single turn.

## A caution worth designing around

Preference extraction, especially the implicit/LLM pass, can be wrong
-- don't let a single misread session lock in a bad preference
permanently. Two guardrails worth adding before shipping:
1. **Confidence threshold** — only persist an implicit preference if
   the pattern shows up across multiple turns/sessions, not one.
2. **Easy override** — a user saying "actually, show me paragraphs
   again" should immediately overwrite the stored preference via the
   explicit (fast) path, not wait for the next implicit extraction
   pass.
