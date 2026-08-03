# Latency-reduction audit: manan-chat-agentic-workflow

Date: 2026-08-03  
Target: repo/worktrees/manan-chat-agentic-workflow/

## Executive answer

The input/output hashes are mostly observability fingerprints, not part of the
shopper decision. They can be removed from a hackathon build. The biggest
latency win is elsewhere: the current code calls Gemma before trying its
deterministic catalog/cart grammar. Moving that grammar before the model can
avoid the model call for common demo messages.

The repository's own integration note says a hosted Gemma 4 26B intent call
returned valid JSON in about 2.4 seconds, above the configured 1.8-second
intent budget (possible_future_issues.md:12). That is the dominant cost. The
hashes and other local bookkeeping are measured in fractions of a millisecond
to a few milliseconds.

Recommended hackathon direction:

1. Keep deterministic catalog truth, exact result references, cart/eligibility
   checks, and one final model-output validation.
2. Cache the static prompt text and output schemas.
3. Remove telemetry-only fingerprints and make the trace compact or disabled
   on the normal response path.
4. Use current-message plus current-session state for the demo; skip the full
   enhancement envelope when memory/history are not being demonstrated.
5. Try deterministic catalog/cart parsing before Gemma. Use Gemma only for
   messages that the bounded grammar cannot safely understand.
6. Replace the second repair call with the existing deterministic fallback for
   the demo, or retain it only behind a flag.

## What was inspected

The implementation is a compact Python/FastAPI prototype. A normal free-text
turn currently runs approximately:

    reserve idempotency
      -> load in-memory snapshot
      -> deterministic query enhancement
      -> build model request and schema
      -> Gemma intent call
      -> optional repair call
      -> semantic validation and state merge
      -> deterministic catalog/reference/cart/research work
      -> optimistic commit
      -> deterministic suggestions
      -> markdown handoff
      -> detailed trace returned and copied into trace history

The main evidence is in:

- src/fkgrid/agentic/orchestrator.py:151 — turn state machine;
  :241, :478, :1324, and :1462 show enhancement, model resolution, commit,
  and post-commit suggestions.
- src/fkgrid/agentic/gateway.py:62 — prompt registry;
  :135 regenerates output schemas and :231 creates model-response hashes.
- src/fkgrid/agentic/validation.py:43 and :71 — canonical JSON and SHA-256
  helpers.
- src/fkgrid/agentic/fakes.py:218 — deterministic enhancement; its returned
  latency_ms is hard-coded to 0 at :450, so its trace timing is not a real
  measurement.
- src/fkgrid/api/main.py:237 and :728 — API JSON encoding and per-turn trace
  deep-copy/history retention.
- src/fkgrid/api/catalog.py:659 — the synthetic catalog's O(N) scan.

The current ApiRuntime wires empty memory/history ports and an in-memory
session. Qdrant, LightRAG, Graph RAG, a database, and a real research provider
are not on the normal current path; deleting those seams reduces code and
maintenance, not normal-turn latency (README.md:35-38).

## Measurement method and limitations

I ran the existing 47-test suite under the available Python 3.10.13 runtime
with a process-local compatibility shim for datetime.UTC, because the project
declares Python 3.12 and no 3.12 interpreter is available in this environment.
The unshimmed run is blocked by that interpreter mismatch; no source code was
changed to hide it.

The local measurements used the fake model, in-memory state, the 600-record
fixture, and four retained recent turns. They exclude live provider/network
latency. Results are Python 3.10 measurements and should be treated as
directional ranges, not release SLAs.

| Measurement | Result |
| --- | ---: |
| Full direct free-text turn, current code, 600 records | 12.3 ms p50 / 14.6 ms p95 |
| Query enhancement alone, current session only | 0.68 ms p50 |
| PromptRegistry.build_request | 3.5 ms p50; output-schema generation about 3.8 ms p50 |
| Full current hash removal, measured end-to-end | about 1.6 ms p50 saved |
| Compact trace/no-event construction, measured end-to-end | about 1.1 ms p50 saved |
| Full API trace deep copy | 0.75 ms p50 |
| FastAPI jsonable_encoder for the full turn | 3.9 ms p50 |
| Full TurnResult JSON size | about 81.5 KB |
| Trace portion of that response | about 44.7 KB |

The fake model is intentionally too fast to represent Gemma. Provider savings
below are therefore based on the repository's documented 2.4-second smoke
observation, with a conservative 1.8-second lower bound when the configured
deadline fires first.

## Hash audit

### 1. Model input_hash, output_hash, and raw_output_hash

Where: contracts.py:708-724, gateway.py:231-255, and
orchestrator.py:516-570 / :1827-1847.

Current behavior: _response() computes:

- raw_output_hash from canonical_json(output);
- input_hash from canonical_hash(request.input_payload);
- output_hash from canonical_hash(output).

raw_output_hash and output_hash are currently the same digest because both
hash the canonical JSON representation. The first one is not a hash of the
provider's raw response bytes, despite its name. None of the three controls
routing, authorization, catalog eligibility, state commit, or cart mutation;
they are emitted into trace metadata.

Recommendation: remove all three for the demo, or keep one optional
debug-only model_fingerprint if troubleshooting is important. At minimum,
remove raw_output_hash as a duplicate.

Latency: roughly 0.25-0.6 ms per model call for the model-response trio,
depending on input size. Removing every non-functional hash in the normal
turn, including context and suggestion hashes, measured about 1.6 ms p50.
This does not remove the canonical JSON serialization needed to send the
prompt to the provider.

### 2. context_hash and projection_hash

Where: contracts.py:401-439, built in fakes.py:423-440, displayed by
orchestrator.py:252-261 and :523-568.

Current behavior: the enhancer hashes a projection and then hashes an envelope
containing that projection hash. The code does not compare either hash at a
later boundary. They are trace fingerprints only.

Recommendation: remove both fields and the surrounding canonical
serialization for the hackathon. Keep the typed projection itself.

Latency: approximately 0.3-0.9 ms per free-text turn for the current context
sizes; this is part of the measured 1.6 ms all-hash saving, so it must not be
added again to that total.

### 3. Request/idempotency request_hash

Where: orchestrator.py:157-169 and ports.py:77-84.

Current behavior: it prevents the same session_id/idempotency_key from being
reused for a different canonical request. It is functional, not merely
telemetry.

Recommendation: keep it if the browser can retry, double-submit, or add a
cart item. A single-user, single-tab demo can replace the reservation system
with a simpler set of used keys, but do not claim that the hash itself is a
meaningful optimization.

Latency: measured canonical hashing of the small reservation payload at about
0.007 ms p50. Removing only this hash is not worth the correctness loss.

### 4. hard_filter_hash / preserved_hard_filter_hash

Where: validation.py:81-86, contracts.py:198-208 and :633, and
catalog.py:757.

Current behavior: the fixture computes it and the fake recovery adapter copies
it. The active fake path does not compare a recovery output against the hash,
and the recovery prompt is not invoked by the current orchestrator.

Recommendation: remove it if Tier-2 recovery/planner work is out of the demo.
If recovery is demonstrated, keep a hard-filter identity check; it is a cheap
guard against a model relaxing an explicit constraint.

Latency: below 0.05 ms for the small current constraint list per search;
effectively zero compared with a model or retrieval call.

### 5. state_hash / preserved_state_hash

Where: contracts.py:263-268 and the clarification contract around the later
model definitions; orchestrator.py:1368 writes state_hash.

Current behavior: state_hash is stored when a clarification is created, but
there is no consumer that verifies it. preserved_state_hash is a contract
field with no active producer/use in this worktree.

Recommendation: remove both for the hackathon.

Latency: below 0.1 ms on clarification turns only; zero on normal search. The
real benefit is removing dead contract surface.

### 6. selected_variant_hash

Where: contracts.py:919-926 and orchestrator.py:1278.

Current behavior: when the model omits it, the orchestrator hashes the
already-resolved ProductBinding. The fixture cart adapter does not consume the
field; the binding itself is already present and exact.

Recommendation: remove it if the demo does not support a user changing
variant attributes between display and cart add. Keep exact
product_id/sku_id/offer_id binding.

Latency: below 0.05 ms per add operation and 64 hex characters less in the
cart operation.

### 7. message_hash and extract_hash

Where: orchestrator.py:1093 and contracts.py:1007-1015.

message_hash is research trace metadata only. extract_hash is required by the
research-source contract but is not computed or checked by the current
runtime; the test fixture supplies a literal value. Remove both if research
citations are not a core demo feature.

Latency: below 0.02 ms for the small current values and zero on normal
catalog/cart turns. The larger gain is fewer fields and less research code.

### 8. Suggestion signed-action hash

Where: fakes.py:766-826.

The fake suggestion store hashes each candidate into a token, but its select()
implementation always returns SUGGESTION_STALE. If suggestions are only visual
demo buttons and are not executable, remove the token and possibly the
suggestion subsystem.

Latency: about 0.03-0.1 ms per suggestion build; under 1 KB response size in
the measured search response. This is not a meaningful CPU win.

## Other removable or simplifiable work

| Item | Why it is overbuilt for a hackathon | Recommended change | Expected saving |
| --- | --- | --- | ---: |
| Static output-schema generation | model_json_schema() runs for every model request even though the schema is immutable per call type. | Cache schemas with lru_cache or precompute them at startup. | **3.1-3.5 ms p50 per model call** locally; also avoids repeated provider request construction. |
| Prompt file read | PromptRegistry.text() reads a prompt file on every live completion. | Cache prompt text at registry construction. | About **0.05 ms** locally per model call; more useful as I/O hygiene. |
| Full query-enhancement envelope | Default API sessions have no memory/history configured, yet the code still builds and validates the envelope, projection, IDs, and hashes. | For the demo use current message + current query state + active result bindings. | **1-2 ms p50** end-to-end locally in the measured minimal-projection variant; provider prefill savings are variable because the current model payload is about 11 KB after four turns. |
| Deterministic grammar after model | orchestrator.py:478-614 calls Gemma first and only tries deterministic_catalog_intent() / deterministic_cart_intent() after model failure. | Try safe recognized grammar first; fall back to Gemma for unsupported language. | **1.8-2.4 s per bypassed turn** using the documented provider observation; the local fake path also fell from 12.3 to about 7.0 ms p50 before other changes. |
| One repair call | _resolve_intent() makes a second provider call after invalid output. | Disable repair for the demo and use deterministic fallback; keep behind a flag if malformed-output behavior is being shown. | **0 ms on a valid first call; 1.8-2.4 s on an invalid first call**. |
| Verbose trace payload | Every tool event stores full model_dump() output, safe structured output, shape summaries, and hashes. The trace is returned in every turn. | Keep stage/status/counts only; expose detailed trace only through a debug switch. | About **1.1 ms p50** for event construction in the local differential test, plus **44.7 KB** less response data. |
| Trace history deep copy | The API copies the complete trace into a 100-item process-local history after every turn (main.py:728). | Do not retain history, or retain only the last compact trace. | About **0.75 ms p50** per API turn, plus lower memory pressure. |
| Full card evidence in list responses | Five result cards include many facts and field-level evidence objects. | For a visual-only demo, send title/price/category and keep evidence on a detail view. | Response fell from **36.7 KB to 21.0 KB** when evidence refs were removed: 15.7 KB less transfer. This is a grounding trade-off, not a free optimization. |
| Synthetic catalog size | The fixture scans all records on every search. | Use 100 records for the demo if 600 are not needed. | Search p50 fell from **4.29 ms at 600 records to 1.28 ms at 100**, about 3.0 ms per search. |
| Suggestion generation | Current suggestions are deterministic and not an extra model call. | Remove only if the demo does not need them. | **0-0.1 ms** local; low value. |
| Markdown handoff | The fake handoff only appends a response and returns a reference (fakes.py:836). | Skip it if no renderer is attached. | About **0.001 ms** locally; no material value. |
| API/domain double validation | ApiTurnRequest is validated, then a new strict TurnRequest is built. | Keep domain validation; if desired, make the API adapter pass already-validated fields through one constructor. | Usually **sub-millisecond**; not worth weakening boundaries. |
| JSON round-trip validation | canonical_json(...) followed by model_validate_json(...) is used for already-parsed dictionaries in several places. | Use model_validate(dict) internally after one trusted boundary validation. | Roughly **0.03-0.2 ms** per small model output; keep one provider-boundary validation. |

### Unused runtime seams

These are safe to delete from a hackathon branch if their demos are not in
scope, but they do not currently cost normal-turn latency:

- GENERATE_CLARIFYING_QUESTION, PLAN_CONSTRAINED_REPAIR, and
  GENERATE_FOLLOW_UP_SUGGESTIONS prompt registrations and prompt files. The
  normal orchestrator uses deterministic clarification, uses the intent call
  for its repair, and uses deterministic fake suggestions.
- GraphContextPort and its fake; no graph lookup is called by the shopper turn.
- Memory/history ports and persistent projection machinery if the demo has no
  same-profile memory. ApiRuntime constructs DeterministicEnhancer without
  those ports.
- Qdrant/LightRAG/Graph RAG/database adapter interfaces described in the README;
  they are explicitly future seams, not active dependencies.
- Durable trace/session work described in futurefixes.md; it is not present to
  remove and therefore has no current latency cost.

## Things that should not be removed

These are not useless even in a hackathon because they prevent visibly wrong
demo behavior. Their local cost is small and their purpose is correctness:

- hard constraints must be applied before display; do not make unknown fields
  match;
- exact product_id/sku_id/offer_id bindings and acknowledged result-set ordinal
  resolution;
- semantic validation after Pydantic parsing of provider JSON;
- deterministic catalog filtering/ranking and eligibility checks;
- cart atomicity, quantity limits, and price/availability revalidation if cart
  actions are shown;
- the model-free SHOW_CART path (possible_future_issues.md:16);
- explicit research gating and citation/source validation if research is shown;
- one state/version check if two browser requests can overlap;
- the separation between model text and typed catalog facts.

Removing these would save little or no latency but can make a demo claim a
wrong product, accept a stale ordinal, or mutate the wrong cart line.

## Recommended implementation order

### Lowest-risk, immediate

1. Cache prompt text and output schemas.
2. Delete raw_output_hash, input_hash, output_hash, trace hash fields,
   context_hash, and projection_hash, or put them behind DEBUG_TRACE.
3. Replace verbose trace tool_output bodies with counts, IDs, and status; keep
   the detailed trace endpoint only when debugging.
4. Stop copying/storing the full trace in trace_history for the demo.
5. Keep suggestions and markdown only if the UI uses them.

### Largest user-visible win

Run the bounded deterministic grammar before Gemma for messages such as:
hey, show my cart, Find a shirt size m, red T-shirts, and narrow ordinal cart
requests. The grammar is already present in query_lexicon.py:509-556; its
current position after the model call is the avoidable latency mistake. Do not
broaden it to silently interpret unsupported brand, price, material, negation,
or reference language.

### Optional demo-only simplification

Use a current-session-only projection and a 100-record fixture. This reduces
code and local work, but it changes follow-up-memory behavior and should be
called out in the README/demo script.

## Total latency impact

The totals below deliberately separate overlapping bundles.

### If Gemma remains on the path

The combined local benchmark for schema caching + removing all current
non-functional hashes + compact/no-event tracing + no suggestions was about
6.5 ms p50 versus 12.3 ms p50 for the current direct orchestrator path:

    local orchestration saving: about 5.8 ms p50

The API adds approximately 0.75 ms for the current trace-history copy and
3.9 ms for jsonable_encoder; compacting the trace reduces both the payload
and some of that encoding work. Removing the full trace changes the response
from about 81.5 KB to about 38.0 KB in the measured fixture.

At common link speeds, removing the 44.7 KB detailed trace also avoids roughly:

| Sustained client throughput | Transfer time avoided |
| ---: | ---: |
| 100 Mbps | 3.6 ms |
| 10 Mbps | 35.7 ms |
| 1 Mbps | 357 ms |

These are wire-time estimates only; TLS, compression, server queues, and
browser parsing are not included.

### If common demo messages bypass Gemma

The combined local benchmark for deterministic bypass + all current
non-functional hashes removed + compact/no-event tracing + no suggestions was
about 5.4 ms p50. Against the current 12.3 ms local path:

    local orchestration saving: about 6.9 ms p50
    provider call avoided: about 1.8-2.4 seconds per recognized turn

At 10 Mbps, adding the detailed-trace transfer avoided gives an approximate
user-visible saving of:

    about 1.84-2.44 seconds per recognized demo turn

That is the practical total for the hackathon path, subject to the provider
latency actually matching the repository's 2.4-second smoke note. If the
provider is faster, the saving is correspondingly smaller. If the current
1.8-second deadline fires first, 1.8 seconds is the safer lower bound.

### Bottom line

Remove the hashes for simplicity, but expect only about **1-2 ms** locally.
Cache schemas for another **about 3.3 ms**. Compacting/removing the trace saves
about **1-2 ms server-side** and up to **357 ms of wire time** on a 1 Mbps
connection. The only multi-second optimization is to **avoid the model call
for recognized demo utterances**, saving roughly **1.8-2.4 seconds per such
turn**. Do not add all line-item numbers mechanically; the measured bundle
totals above are the non-overlapping estimates to use.
