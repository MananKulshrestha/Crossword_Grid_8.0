# Chat failure-cascade research

This document records the investigation of whether one failed shopper turn makes later turns fail because the failed query and answer are added to chat memory. The conclusion is mixed: before the fix, failed turns were stored in the four-turn recent context, but the strongest repeat-failure patterns were also caused by authoritative session state. The implementation now filters failed turns from general model memory while retaining state and trace evidence.

## 1. Issue Breakdown

### Reported symptom

After a turn returns an interpretation failure, clarification, or action failure, subsequent shopper messages can appear to fail repeatedly. The suspected cause is that the failed query and assistant response are fed back into the next model call.

### What the current implementation actually stores

The process-local session snapshot stores at most four `RecentTurnContext` records. The record contains:

- the user query;
- the assistant summary;
- the action and terminal state;
- hard constraints, soft preferences, and query terms at commit time;
- result-set and referenced-result IDs;
- warnings and a clarification reason, when present.

Before the fix, `TurnOrchestrator._commit_and_finish()` appended a `RecentTurnContext` for every response that reached the commit path. The current implementation records only successful terminal states in recent conversational memory: grounded answers, verified details/compare/availability, cart updates/shows, and successful external research. `INTERPRETATION_UNAVAILABLE`, `CLARIFICATION_REQUIRED`, `NO_ELIGIBLE_MATCH`, `ACTION_FAILED_WITH_REASON`, and other non-success outcomes remain committed where needed for authoritative state and idempotency, but do not enter the next model's recent-turn context.

The next enhancement projection passes those recent turns to the intent stage together with the authoritative current state and active result bindings. Therefore, the failed assistant summary is available to the next model call. The trace history also retains safe per-turn traces, including validated structured model output or redacted invalid-output diagnostics, but trace history is not sent to the next model as prompt context.

### Evidence from controlled sequences

The experiments used the current FastAPI/TestClient runtime with deterministic model-unavailable and fake-model paths.

| Sequence | Result | What it shows |
| --- | --- | --- |
| `blorf qzx` → `INTERPRETATION_UNAVAILABLE`; then `red tshirt size L` | The second turn returned grounded results. Its trace reported `memory_used: true` and included the failed turn ID. | Failed memory is present, but it does not automatically cause the next turn to fail. Current explicit catalog terms and deterministic fallback can recover. |
| `show product details` → `CLARIFICATION_REQUIRED`; then `what about it` | The second turn returned `CLARIFICATION_REQUIRED` again. | A pending clarification is authoritative session state. An underspecified follow-up is intentionally blocked; this is a real cascade-like behavior, but not merely failed transcript memory. |
| `show product details` → clarification; then `blue shoes` | The explicit search succeeded and cleared the pending clarification. | Fresh explicit search/refine turns are already allowed to supersede stale clarification state. |
| `Blue shoes size UK 9` → `NO_ELIGIBLE_MATCH`; then `shoes` | The second turn also returned `NO_ELIGIBLE_MATCH` because hard `size_9` remained active. `Blue shoes size UK 7` then returned grounded results. | Sticky hard filters, not the failed answer text, can produce repeated no-match results. The shopper must replace or clear the constraint. |

### Main conclusion

The analysis is not biased or wrong, but it identifies only one layer of the problem:

1. **Confirmed before the fix:** failed/clarification turns were included in general recent model context.
2. **Plausible risk:** a model can anchor on a previous failure summary or treat a failed interpretation as conversational guidance, increasing the chance of another bad interpretation.
3. **Not sufficient by itself:** an explicit follow-up can recover while failed memory is present.
4. **Confirmed independent cascades:** pending clarification and sticky hard constraints can block or repeat later turns even if failed turns are removed from memory.
5. **Not the immediate cause:** lack of a database does not explain same-process cascades. The current memory and state are in-process; a restart clears them rather than preserving a bad failure chain.
6. **Implemented:** failure outcomes remain visible in bounded trace history, while the recent conversational memory passed to the model contains only memory-eligible successful turns.

### Important distinction

The system has three different kinds of retained information and they should not share one policy:

```text
authoritative session state  -> controls routing, filters, pending clarification, cart, references
recent conversational memory -> helps the model understand follow-ups
trace/failure history        -> debugging and audit; should not steer the model
```

The current implementation keeps authoritative state, model memory, and trace history separate. Failed responses can still be inspected through the trace endpoint without steering the next intent call.

## 2. Repository Areas Requiring Deep Research

### `src/fkgrid/agentic/orchestrator.py`

- `TurnOrchestrator.handle()` determines which outcomes reach commit.
- `_resolve_intent()` decides whether provider failure, repair failure, or deterministic fallback is used.
- `_blocking_clarification()` can convert a later turn into another clarification based on persisted pending state.
- `_route()` distinguishes grounded search, no-match, cart policy failure, and other action failures.
- `_commit_and_finish()` separates recent-memory eligibility from state commit: it can commit state and trace a failure without adding that failure to recent model memory.
- `_build_recent_turn_context()` defines exactly which failure data is exposed to the next enhancement/model call.

### `src/fkgrid/agentic/contracts.py`

- `RecentTurnContext` is the prompt-memory contract and currently allows every `TerminalState`.
- `TurnSnapshot` owns `recent_turns`, `pending_clarification`, the authoritative `QueryState`, and acknowledged result bindings.
- `QueryState` hard constraints are intentionally sticky until explicitly replaced or cleared; this is separate from memory filtering.

### `src/fkgrid/agentic/fakes.py`

- `InMemorySessionState.commit()` increments the state version and replaces the snapshot with the supplied `recent_turns` for every committed response.
- `DeterministicEnhancer.enhance()` projects all snapshot recent turns into `recent_turn_context`, which confirms that failed records are not filtered before the model boundary.

### `src/fkgrid/agentic/validation.py`

- `merge_query_state()` persists explicit constraints and pending-state transitions.
- A fresh `SEARCH`/`REFINE` with delta operations clears pending clarification, while an underspecified turn does not.
- Clear this file separately from memory-policy work: hard-filter ownership is a correctness invariant and must not be weakened to hide no-match cascades.

### `src/fkgrid/api/runtime.py` and `src/fkgrid/api/main.py`

- `ManagedSession.trace_history` is process-local and bounded at 100 traces.
- The session snapshot and recent memory are process-local as well; there is no database adapter yet.
- The trace endpoint is useful for determining whether a later failure came from memory use, pending state, provider failure, fallback, or a tool/policy result.

### Existing tests and missing coverage

Current tests cover four-turn memory, trace visibility, deterministic fallbacks, category switching, and cart language. The missing regression matrix is failure-specific:

- failed interpretation followed by a valid explicit search;
- failed interpretation followed by a pronoun/ordinal follow-up;
- clarification followed by an unrelated message;
- clarification followed by a fresh explicit search;
- no-match followed by same-category refinement versus category switch;
- cart policy failure followed by another acknowledged-result cart action;
- provider failure with and without recent failed turns;
- proof that trace history retains failed diagnostics while model memory excludes them after the proposed fix.

## 3. Fix Validation / Testing Checklist

### Recommended memory policy

Do not blindly discard every unsuccessful turn. Instead, separate **prompt-memory eligibility** from **state commit** and **trace retention**:

| Outcome | General recent model memory | Authoritative state | Trace/audit |
| --- | --- | --- | --- |
| Grounded result, cart update, verified details/compare/availability | Include compact success context | Commit normally | Retain |
| `INTERPRETATION_UNAVAILABLE`, provider timeout/schema failure, internal model failure | Exclude from ordinary recent context | Preserve the prior query state; do not invent a delta | Retain safe diagnostics |
| `CLARIFICATION_REQUIRED` | Exclude the generic clarification answer from ordinary memory | Keep the typed pending clarification separately | Retain |
| `NO_ELIGIBLE_MATCH` | Keep only normalized explicit constraints if needed; omit failure wording as model guidance | Commit the explicit filters so the shopper can refine/replace them | Retain |
| `ACTION_FAILED_WITH_REASON` from commerce/policy/availability | Exclude as a completed action; optionally keep a bounded structured failure marker for an explicit retry | Do not mutate cart; preserve acknowledged references | Retain |
| `STATE_CONFLICT`, `IN_PROGRESS`, rejected boundary request | Do not add to conversational memory | Do not commit a new turn | Retain the trace/status where available |

The implemented minimal policy is a deterministic terminal-state eligibility set at the point where `_commit_and_finish()` builds `recent_turns`. It does not remove failure traces, clear authoritative hard constraints, or make a failed cart action look successful. The commit trace now exposes whether a recent-memory record was stored and why it was excluded.

### Required tests before implementing the policy

- Assert that `INTERPRETATION_UNAVAILABLE` is visible in the trace but absent from the next turn's `recent_turn_context`. **Implemented and passing.**
- Assert that a following explicit query succeeds and still sees the current authoritative state and acknowledged result set.
- Assert that a following explicit query does not inherit the failed assistant summary.
- Assert that a pronoun/ordinal follow-up after a failed interpretation does not guess merely because an old failed record remains in trace history.
- Assert that `CLARIFICATION_REQUIRED` remains represented by `pending_clarification`, not by a generic recent-memory assistant summary. **Implemented and passing.**
- Assert that a fresh explicit `SEARCH`/`REFINE` clears stale pending clarification and succeeds.
- Assert that `NO_ELIGIBLE_MATCH` preserves explicit hard constraints, while a new explicit size/color/category replaces or clears the intended field. **Implemented and passing.**
- Assert that an unavailable cart target does not mutate the cart and that a later available acknowledged target can still be added. **Implemented and passing.**
- Assert that `STATE_CONFLICT`, replay, and in-progress requests do not add conversational memory.
- Assert that the four-turn memory cap still applies after filtering and that excluded failures do not silently consume the cap.
- Assert that raw prompts, provider error bodies, secrets, and chain-of-thought remain absent from both memory and traces.
- Run the live Swagger sequence with trace inspection and verify the next turn's `QUERY_ENHANCED` metadata reports only eligible recent-turn IDs.

### Current verification

- The full suite passes with 45 tests.
- A live port-8000 sequence confirmed that an unavailable cart action reports `recent_memory_recorded: false`; the following available cart action succeeds and sees only the preceding successful search turn in `recent_turn_ids`.
- The filtered failure remains available through the session trace, including its action-failure summary and commit metadata.

### Decision

The user’s proposed change was directionally correct. It is now implemented as a typed eligibility policy, not a blanket deletion: failed interpretation and generic failure responses are excluded from ordinary conversational memory; pending clarification and explicit hard filters remain authoritative; and trace history retains failures for debugging. This addresses the model-bias portion of a cascade without masking the independent state causes.
