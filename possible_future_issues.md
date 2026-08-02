# Possible future issues

This implementation intentionally stops at the shopper-facing orchestration boundary. The items below are risks that must remain visible when the owning adapters are integrated.

## Fragile contracts

- The complete compatibility tuple must be pinned on every turn, model request, tool request, result entry, research claim, and suggestion. A missing field can mix prompts, catalog versions, rankings, or policy decisions.
- Model-valid JSON is not domain-valid behavior. Semantic validation must continue to reject invented IDs, unsupported hard constraints, fabricated facts, tool names, URLs, and authorization decisions.
- The model gateway and provider adapter must preserve the one-repair-only rule. Retrying arbitrary malformed output or feeding raw invalid output back to the model can create prompt-injection and latency loops.
- Gemma 4 12B is configured as an alias, not a hard-coded business assumption. The serving provider, tokenizer, structured-output support, thinking-mode defaults, context window, quantization, and license must be verified before changing the alias in a release tuple.
- The model API key must be injected through the runtime secret manager/environment and rotated if exposed. A Gemma model alias does not identify a serving endpoint; endpoint authentication, JSON-schema support, rate limits, and provider-specific request fields still require an adapter contract test.
- The protected enhancement envelope and the provider projection must not drift. Stable session, memory, purchase, cart, and private provenance identifiers must remain out of the provider payload unless the contract explicitly allows an already-owned current reference.
- A memory or history adapter must preserve current-message precedence, same-profile ownership, version pinning, and the distinction between verified purchase history and cart/click data.
- `show_cart` must remain model-free. Accidentally routing it through intent extraction or a generic “agent tool loop” adds latency and creates an unnecessary mutation surface.
- Natural-language cart drafts are not cart operations. Exact result-entry/SKU/offer resolution, expected versions, revalidation, confirmation, and atomic commit belong to the future cart owner.
- Research sources are untrusted data. A source must never create a tool call, canonical product fact, price, availability, cart target, or permission.
- Suggestions are signed stored actions. Their labels must never be reparsed, and every click must revalidate session, state, cart, result-set, capability, and expiry versions.
- The markdown handoff is intentionally only a port. A future renderer must escape catalog/model/research text and render facts from typed evidence; it must not turn free-form text into action authority.

## Latency and reliability risks

- The 75 ms enhancement budget includes authorized reads, deterministic selection, token estimation, canonical serialization, and fallback. Memory/history saturation must fall back to the same schema without consuming catalog/cart capacity.
- The 1.8 s intent budget permits one normal call plus one sanitized schema-repair call. Provider timeouts arriving after cancellation must be discarded and must not commit state.
- Recovery, research, and suggestion refinement need separate bulkheads and circuits. Sharing one provider pool can make optional features take down search or cart controls.
- A real Qdrant/hybrid adapter must return exact eligibility and evidence semantics. Approximate retrieval, over-fetching, or post-filtering that changes the hard-filter contract can create false matches.
- A future LightRAG/Graph RAG adapter is advisory only. Graph facts need deterministic identity/evidence validation before they can enter a response; graph traversal must never be selected by model output.
- Commit conflicts must not auto-rebase natural-language deltas against newer state. The safe behavior is a conflict response and explicit retry.
- The cart adapter and session commit owner must agree on one atomic/compensating boundary. If a cart adapter persists a mutation before the outer session CAS can fail, a concurrent state conflict could otherwise leave cart and query snapshots out of sync.
- Idempotency reservations must happen before model, retrieval, or network work. Otherwise duplicate submissions can double-call providers or double-apply cart changes.
- The markdown pipeline must not become a synchronous second model call on the primary path unless its budget, cancellation, and fallback are explicitly versioned.

## Accuracy and safety risks

- “Cheaper,” “instead,” negation, mixed conjunctions, locale-specific sizes, units, and contradictory bounds need held-out fixtures before enabling a provider adapter.
- Unknown and `NOT_MODELED` values must remain distinct from false, unavailable, zero, or out-of-stock. Renderer shortcuts can silently create unsupported claims.
- Product family, SKU/variant, offer, and listing identity must remain separate through details, availability, comparison, research attribution, and cart actions.
- External product identity cannot be established by title similarity alone. Manufacturer/model or another reviewed exact identity rule is required.
- Prompt or catalog text containing fake JSON, tool syntax, URLs, or “ignore constraints” must be treated as data and never as instructions.
- A configured provider must fail closed when its structured-output contract, model alias, prompt checksum, or schema version is incompatible. Silent fallback to a fake provider is unsafe outside explicit test mode.
- Public traces must remain useful without exposing raw prompts, chain of thought, bearer tokens, memory tokens, private purchase context, PII, or full external extracts.

## Integration gates

Before enabling real adapters, add contract tests for timeout, malformed output, arbitrary IDs, prompt injection, version mismatch, cancellation, stale references, cross-session ownership, mixed evidence, research-source conflict, cart revalidation, and stale suggestions. Keep the fake-provider path green with all optional features disabled.
