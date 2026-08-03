# Latency reduction — crisp version

Target: repo/worktrees/manan-chat-agentic-workflow/

## Verdict

Hashes are mostly trace fingerprints. They are safe to remove for a hackathon,
but they are not the main latency problem. The dominant cost is the hosted
Gemma intent call: the repository records about 2.4 seconds, above its 1.8 s
budget.

## Best cuts

| Change | Estimated saving |
| --- | ---: |
| Try deterministic catalog/cart grammar before Gemma | **1.8-2.4 s** per recognized demo message |
| Cache Pydantic output schemas | **3.1-3.5 ms** per model call |
| Remove all non-functional hashes | **about 1.6 ms p50** locally; model hashes alone about 0.25-0.6 ms |
| Compact/disable verbose trace | **about 1.1 ms p50** plus 44.7 KB less response data |
| Stop copying full trace history | **about 0.75 ms p50** per API turn |
| Use current-session-only enhancement | **about 1-2 ms p50** locally; variable provider prefill saving |
| Use 100 instead of 600 fixture records | **about 3 ms** per catalog search |
| Remove suggestions/markdown handoff | **0-0.1 ms**; negligible |

raw_output_hash is especially redundant: the code hashes canonical JSON for
both raw_output_hash and output_hash. Remove it first.

## Hashes to remove or defer

- ModelResponse.input_hash, output_hash, raw_output_hash.
- TraceEvent.input_hash and output_hash.
- EnhancedQueryEnvelope.context_hash and IntentContextProjectionV1.projection_hash.
- PendingClarification.state_hash and unused preserved_state_hash.
- message_hash, extract_hash, and selected_variant_hash if research, variant
  revalidation, and executable suggestions are out of scope.
- hard_filter_hash only if bounded recovery/planner is removed.

Keep request_hash if retries or cart mutations are possible; it costs only about
0.007 ms and protects idempotency.

## Keep these

Keep exact product/SKU/offer IDs, hard filtering, deterministic ranking,
reference resolution, Pydantic model-output validation, eligibility checks,
cart atomicity, and the model-free SHOW_CART path. They prevent wrong demo
results and cost very little locally.

## Measured totals

Current direct fake-model path: **12.3 ms p50** local orchestration, excluding
the live provider. A combined compact model path measured **6.5 ms p50**:

    local saving while keeping Gemma: about 5.8 ms p50

A combined deterministic-bypass path measured **5.4 ms p50**:

    local saving: about 6.9 ms p50
    provider saving: about 1.8-2.4 s per recognized turn

The detailed trace is about 44.7 KB. Removing it saves approximately 3.6 ms at
100 Mbps, 35.7 ms at 10 Mbps, or 357 ms at 1 Mbps, before TLS/compression.

### Total practical hackathon win

For a recognized demo message on a 10 Mbps connection: approximately
**1.84-2.44 seconds** user-visible latency reduction, with the provider call
avoidance doing nearly all the work. Hash removal alone is only a small
cleanup, not a multi-second optimization.
