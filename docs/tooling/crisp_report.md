# Crisp report

Rating scale: 1/10 is the worst remaining implementation risk; 10/10 is the
most complete deterministic local implementation. Ranking is worst to least
worst.

## 1. Production persistence, orchestration, and publication — 5/10

The ingest, identity, normalization, validation, diff, risk, review routing,
publication, index smoke test, and rollback tools now exist. The remaining risk
is production ownership: persistence, authenticated human review, real artifact
staging, checksum publication, the full orchestrator CAS/idempotency path, and
the real index builder are still external integrations. The local stores are
intentionally in-memory.

## 2. Shopper catalog/retrieval boundary — 6/10

Search, hard eligibility, exact references, details, compare, and availability
are implemented with stable deterministic lexical ranking. A production hybrid
retrieval adapter, database, catalog artifact activation, and evaluation corpus
still need to be connected through the same contracts.

## 3. Shared model gateway and intent — 6/10

The provider-neutral structured gateway and deterministic intent/repair stubs
exist. Provider SDK integration, versioned prompt resources, cost accounting,
and production model calibration remain open. The gateway cannot register or
invoke tools.

## 4. Quality Sentinel — 7/10

Signal intake, redaction, qualification, evidence assembly, deterministic
classification, validation, routing, human decision, and case lifecycle exist.
The production SQL store, catalog snapshot owner, queue, RBAC, and audit sink
remain to be integrated.

## 5. Query Recovery — 7/10

Confidence gating, approved expansions, recovery constraints, immutable-filter
plan validation, plan application, run comparison, and a port-compatible
adapter exist. A real retrieval owner, persistent approved lexicon, planner
provider, circuit breaker, cache, and event sink remain integration work.

## 6. Catalog Language — 7/10

Normalization, mining, target retrieval, safe deterministic mapping, validation,
candidate assembly, regression, shadow evaluation, human review, atomic
activation, and runtime lookup exist. Model proposer/critic integration and
durable review/artifact storage remain external.

## 7. Research — 8/10

Research is explicit-only, bounded, allowlist-aware, cited, and provider-extract
based. The local adapter deliberately has no arbitrary HTTP client. A live
search provider and optional allowlisted direct-fetch adapter must be added later
behind the existing isolated port and 6.5-second deadline.

## 8. Suggestions — 8/10

Candidates are deterministic, capped at three, signed, expiring, session-bound,
and selected by stored action ID. Cart actions are not accepted or executed.
Durable storage and optional asynchronous phrasing remain to be wired in.

## 9. Query enhancement, clarification, and reference safety — 8/10

Current text wins over soft memory/history context, clarification is validated,
and stale/missing references abstain. A durable session/profile implementation
and full orchestrator CAS/idempotency path remain outside this branch.

## 10. Cross-worktree contract adapters — 8/10

The chat, recovery, research, suggestions, Catalog Language, Catalog
Operations, and Quality Sentinel seams are explicit and have deterministic
smoke coverage. The remaining risk is that each existing worktree still owns
its own Pydantic classes; production integration must validate those classes at
the projection seam and must not treat these reference adapters as durable
service implementations.

## Result

- Final branch: `manan/final-agentic-chat`
- Final isolated worktree: `repo/worktrees/manan-final-agentic-chat`
- Agentic chat and speech-to-text workflow: integrated from their latest branch
- Deterministic non-cart registry: wired into the API runtime
- Non-cart tools in the static allowlist: 73
- Existing specialist worktrees modified: none
- Shopping-cart tools registered: none; shopper cart remains workflow-owned
- One-by-one contract audit: 73/73 passed
- Static compile check: passed
- Full pytest suite: passed
- Ruff: follow-up cleanup remains for imported reference-file formatting and unused imports
- Tier 3 feature paths remain deferred as required by the binding plans.
