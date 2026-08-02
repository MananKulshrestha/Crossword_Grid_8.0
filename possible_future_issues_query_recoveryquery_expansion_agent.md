# Possible future issues — query recovery/query expansion agent

This is a living risk register. Update it whenever a loop changes a contract,
query, validator, workflow budget, artifact format, or provider boundary.

## Fragile and sensitive invariants

- A mapping target must come from the pinned canonical vocabulary. Any model- or
  SQL-derived ID that bypasses that set can create category leakage or stale actions.
- The complete catalog/taxonomy/schema/normalizer compatibility tuple must stay
  attached to proposals, regression, shadow, review, activation, and runtime lookup.
  Mixing versions can make a valid alias point at a retired attribute/value.
- `expansion_action` is safety-critical. A synonym/attribute paraphrase must never
  silently become a hard filter; `EXPLICIT_FILTER_AFTER_CONFIRMATION` is only a
  clarification candidate until the shopper confirms it.
- Scope collisions and polysemy must abstain. A global mapping for a term with
  category-specific senses can silently harm ranking even when zero-result recovery
  improves.
- Runtime output is bounded (three mappings per term, five applied mappings/query).
  Any unbounded loop, recursive expansion, or bidirectional edge would create
  latency and query-drift risk.
- Quoted/negated shopper text must not be expanded. A small normalizer/protected
  span mistake can reverse an explicit exclusion.
- Evidence aggregates must remain privacy-safe and k-anonymous. Raw session text,
  profile IDs, purchases, memory, and stable user identifiers must never enter the
  Tier 2 workflow or model payload.
- Source concentration and exposure bias can make frequent language look correct.
  Clicks/zero results alone are not truth; source diversity and protected replay are
  required before review.
- Proposer and critic failures are abstentions/concerns, not permissions to retry
  recursively or broaden targets.
- Review and activation are separate authorized operations. No fake/default review
  adapter may be used in production, and stale-base activation must fail closed.

## Future risks and likely bugs

- DB adapter row ordering or JSON serialization could make candidate/artifact checksums
  change across SQLite/PostgreSQL; use canonical JSON and explicit sort keys.
- A vocabulary materialized view may lag the active catalog and return target IDs that
  are valid historically but not in the pinned version; enforce version joins at the
  query and port boundary.
- Levenshtein-one clustering can merge short false friends; keep it limited to
  spelling-equivalent review partitions and protect seeded false-friend cases.
- A critic may over-reject colloquial language or under-detect polysemy. Track critic
  concern rates by category/locale and require held-out review labels.
- Source concentration can be computed over event counts instead of independent
  groups, inflating confidence and causing batch review of one noisy source.
- Regression fixtures can become the tuning set, hiding failures on new categories,
  locale/code-switch terms, negative phrases, and long-tail attributes.
- Candidate versions with many mappings can make review and artifact writes slow;
  cap proposals and split batches without changing the active pointer.
- Runtime lookup caches must key on lexicon version, locale, scope, and normalizer
  version. A cache that omits one key can leak a scoped expansion into another query.
- Filesystem activation can fail after writing a candidate but before pointer swap;
  retain the last-known-good pointer and make recovery idempotent.
- Model/provider tokenization and timeout behavior may differ from local estimates;
  enforce the parent deadline and record input/output hashes without storing prompts.
- SQL query plans may scan aggregate/recovery tables at scale; add indexes and inspect
  query plans before enabling large evidence windows.
- Partial human approvals need deterministic mapping-level status so one rejected
  proposal cannot accidentally activate with its batch.
- Query expansion can improve zero-result counts while lowering precision/NDCG. Shadow
  evaluation must measure irrelevant-result increase and protected-query regressions.
- Strict Pydantic models reject provider enum strings when passed as Python dictionaries;
  every configured adapter must parse provider JSON through the JSON boundary before
  semantic validation, and never construct domain enums by permissive coercion.
- Candidate mappings must be rewritten to the candidate lexicon version before checksum,
  review, artifact, or activation. Leaving the parent version on a new mapping creates
  a runtime compatibility mismatch immediately after a successful activation.
- Morphological clusters may contain multiple normalized forms. The proposer must return
  the normalized form of the exact supplied source phrase, while the cluster representative
  is only grouping context; conflating these can reject safe spelling variants or merge
  their Boolean meaning.
- Review evidence IDs and target IDs are allowlisted input data. A model response that
  cites a valid-looking but unsupplied evidence/target ID must be rejected even if the
  rest of the JSON is schema-valid.

## Open handoff questions

- Which database owner supplies the canonical vocabulary materialization and transaction
  runner for activation CAS?
- Which model/provider and structured-output schema adapter is approved, and what cost
  budget/circuit policy applies to proposer and critic calls?
- Which retrieval/evaluation owner supplies regression and shadow fixtures, labels, and
  release thresholds for each launch category and locale?
- Which reviewer roles/identity service enforce independent approval and publication?
