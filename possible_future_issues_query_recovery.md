# Possible future issues — Query Recovery Agent

This is a handoff risk register for the Tier 2 implementation. It is intentionally explicit about fragile assumptions so a later database, retrieval, catalog-language, or model-gateway owner does not accidentally turn a safe fallback into an invisible query rewrite.

## Fragile contracts and small-mistake hazards

1. **Hard-filter hash semantics.** The hash must include every semantic hard clause, canonical operator, typed value, negation mode, and schema version, while excluding provenance-only fields. Adding a field to `HardConstraint` without updating `canonical_clause()` can either allow a filter mutation or create false conflicts. Any hash algorithm change requires a new contract/policy version and replay fixtures.
2. **Product/SKU/offer identity.** Recovery only changes query concepts. It must never be allowed to return product facts, cart targets, offer IDs, price, or availability. A retrieval adapter that turns a concept ID into a result without the exact catalog/SKU/offer/evidence contract can create cross-variant grounding bugs.
3. **Version tuple drift.** Lexicon, taxonomy, category schema, catalog, index, gate, rank, recovery policy, prompt, and model versions must remain compatible for the whole turn. A database query that omits one version predicate can silently use an old mapping or concept allowlist.
4. **Lexicon scope and collision ordering.** Exact category scope must beat global scope; equal-priority incompatible mappings must abstain. A tie-breaker based on insertion order, database row order, or runtime hash ordering can make recovery nondeterministic or choose the wrong meaning.
5. **Planner allowlist completeness.** The planner may select only IDs returned in the bounded context. If a future adapter passes a complete catalog, raw taxonomy dump, stable session IDs, or arbitrary tool names into the prompt, the privacy and least-privilege boundary is broken.
6. **Clarification option authority.** Every option must come from active version-pinned concepts and be bound to the preserved hard-filter/state hash. Do not accept a label or option ID reparsed from a later free-text answer; use the normal intent/clarification state machine.
7. **Untrusted labels and terms.** Shopper terms, catalog labels, lexicon phrases, and planner labels are data. UI/API layers must escape them as text. Never render them as HTML/Markdown or use them to choose SQL, tools, URLs, or policies.
8. **Baseline-first ordering.** Calling recovery before the normal retrieval run, or allowing a model to replace a confident baseline, defeats the central confidence gate. Keep an integration test that proves confident queries make zero lexicon/planner calls.
9. **Attempt budget.** The permitted maximum is baseline + one Tier 1 rerun + one Tier 2 rewrite rerun, with at most one planner call. A retry wrapper around the planner, a generic provider retry, or a recursive orchestrator call can exceed the budget and the 1.8-second recovery deadline.
10. **Comparator quality.** More results are not proof of improvement. Comparator thresholds are policy artifacts, not universal confidence. A retrieval/index/model upgrade can change score distributions; reuse of old thresholds can cause false recovery or silent relevance regressions.
11. **DB transaction placement.** Event recording should be in the authoritative turn/outbox transaction when possible, but provider and retrieval work must never hold a write transaction. If event writes are retried, they need event/idempotency ownership and must not duplicate shopper state.
12. **SQLite/PostgreSQL parity.** The included adapter uses parameterized SQLite SQL as an executable contract. Dialect migration must preserve null/scope/version predicates, enum strings, ordering, and bounded limits. Do not replace it with string-built SQL or rely on SQLite’s permissive typing.
13. **Model gateway boundary.** Provider SDK types must stop at the shared gateway adapter. Structured JSON validation is not authorization: semantic ID, version, hash, scope, capability, and hard-filter validation must still run after provider parsing.
14. **Circuit and deadline clocks.** Recovery must use the injected monotonic clock, not wall time or provider timestamps. A circuit-open or deadline-exhausted response must preserve baseline/no-match safely, never begin a late planner call.
15. **Event privacy.** Recovery events may include opaque IDs, hashes, versions, counts, and validation codes. They must not include raw messages, memory/purchase context, bearer tokens, prompts, provider errors, secrets, or chain of thought.
16. **Provider response content.** DeepInfra uses the OpenAI-compatible `choices[0].message.content` shape, but model/provider upgrades can return a content-part list, empty content, or an error-shaped 200 response. Keep extraction bounded and pass only answer text to the strict union parser; never persist reasoning content.
17. **DeepInfra provider latency.** The OpenAI-compatible endpoint is remote and model queue time can exceed the 1.8-second recovery budget. The workflow must preserve its timeout/no-safe fallback instead of adding retries or silently increasing the shopper-turn budget. Measure P95/P99 before enabling the live path broadly.
18. **Deadline bookkeeping margin.** Provider cancellation, socket cleanup, event creation, and response serialization happen after the model timeout. Keep an explicit post-call reserve; using the full remaining deadline can make an apparently bounded request exceed the turn budget.
19. **Provider JSON mode is not semantic validation.** DeepInfra's `json_object` response format controls JSON syntax, not allowed IDs or action semantics. Keep the local Pydantic parse plus hard-filter, scope, version, and allowlist validation.
20. **Credential source precedence.** `FKGRID_DEEPINFRA_API_KEY`, `DEEPINFRA_API_KEY`, and `DEEPINFRA_TOKEN` are process inputs only. Never persist, log, include in events, put any value into a prompt, or commit an environment file containing a credential.
21. **Model identifier casing.** DeepInfra model IDs are provider catalog identifiers. Changing `google/gemma-4-26B-A4B-it` casing, punctuation, or provider prefix can produce an authorization/model-not-found failure or split compatibility metrics. Treat the model alias as a versioned contract and update the prompt manifest and fixtures together.
22. **OpenAI compatibility drift.** DeepInfra documents an OpenAI-compatible interface but not every model supports every parameter equally. Keep the payload minimal, pin `response_format` behavior with a fake contract test, and classify 4xx responses without exposing provider bodies.

## Likely future bugs and latency risks

- **Tail latency from a slow DB lookup:** missing indexes on `(normalized_phrase, locale, lexicon_version, catalog_version, taxonomy_version, category_schema_version)` can consume the Tier 2 budget before the planner. Add query-plan checks and a separate lexicon circuit.
- **Oversized allowlists:** returning thousands of taxonomy concepts increases prompt tokens and makes ambiguity worse. Keep the materialized projection bounded and rank candidates deterministically before the planner.
- **Provider retries:** generic HTTP retries can turn one planner call into multiple billable/slow calls. The planner adapter must enforce one logical call; any transport retry policy belongs inside the shared gateway with an explicit attempt counter.
- **Score calibration drift:** embedding/rank-policy changes can invalidate `min_improvement_margin`, `max_top_score_drop`, and low-coverage thresholds. Disable calibrated triggers until a new policy artifact and held-out report are activated together.
- **Direct mapping overreach:** an approved alias may be safe in one category but ambiguous globally. A mapping without a compatible scope must not be selected when multiple senses are active.
- **Rerun result mismatch:** a retrieval adapter can return a run with the wrong catalog/index tuple or a hard-filter hash copied from the baseline. Contract tests must compute/verify the run’s state and tuple at the adapter boundary.
- **Clarification loops:** repeatedly asking the same question after an invalid planner response can create a user-visible loop. Store a pending clarification with state/expiry tokens in the owning session workflow; this package only returns a bounded packet.
- **Event-write backpressure:** synchronous event persistence can add response latency or block retrieval during a DB incident. Use the outbox/append-only boundary and make failure visible without changing the primary outcome.
- **Catalog-language churn:** a newly activated lexicon can alter recovery traffic and protected-query relevance. Activation must invalidate recovery caches and replay protected cases before serving it.
- **Concurrent activation:** catalog/index/lexicon activation during a turn can produce mixed runs. Pin one tuple before baseline and reject any candidate that does not match it.
- **Large query terms / Unicode edge cases:** normalization can collapse or change alphanumeric model terms, quoted negation, or transliteration. Reuse the shared tokenizer and protect spans before this package receives terms.
- **Missing offer data downstream:** recovery may appear successful while cards cannot bind an exact offer. The shopper orchestrator must treat this as a catalog/retrieval failure, not as permission to synthesize an offer.
- **Planner label injection in UI:** interpretation text is reduced to deterministic allowed-concept labels in this package, but downstream renderers still need text escaping and claim-ledger rules.
- **Fake/real parity gaps:** in-memory adapters can accidentally accept unversioned or inactive concepts that SQL does not. Run the same contract suite against the DB owner’s adapter before enabling Tier 2.
- **Metrics cardinality:** labels containing raw terms, IDs, or prompts can leak data and create high-cardinality telemetry. Metrics should use reason/policy/version classes and hashed/opaque correlations only.

## Required checks before enabling against real dependencies

- replay normal, Tier 1, Tier 2 rewrite, clarification, no-safe, provider-timeout, circuit-open, stale-version, arbitrary-ID, and prompt-injection fixtures;
- prove zero hard-filter mutation and zero category leakage across all accepted recovery runs;
- prove exactly one planner call and no more than three retrieval runs per turn;
- verify indexes and query plans for lexicon/allowed-concept reads;
- verify event insert/outbox idempotency and redaction;
- compare SQLite and PostgreSQL adapter results for the same versioned fixtures;
- run held-out false-trigger, accepted-recovery, clarification, latency, and protected-query regression evaluation;
- keep Tier 2 kill-switchable so the complete Tier 1 path remains available without a client/schema change.
