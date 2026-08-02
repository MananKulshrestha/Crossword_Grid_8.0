# Possible future issues and operational risks

This register records fragile boundaries in the Tier 1 implementation. These
are not claims that the risks are solved by this worktree. The database,
catalog, model, queue, identity, policy-approval, and API owners must preserve
the controls below when replacing the in-memory adapters.

| Risk | Why a small mistake matters | Required control before integration |
|---|---|---|
| Event-time snapshot lookup uses the current catalog | A later correction can be incorrectly presented as the fact visible when the report happened, changing the assessment. | Snapshot reads must be version- and timestamp-bound; missing history must remain `AWAITING_EVIDENCE`, never fall back to current data. |
| Product/SKU/listing identity is joined loosely | Reports for two variants or listings can become one incident and route the wrong evidence. | Enforce the full identity tuple and version in repository foreign keys and contract tests. |
| Group-window bucketing differs across adapters | One adapter may qualify a cluster while another splits it at a boundary. | Persist the exact `GroupKey` and policy version; replay boundary timestamps in SQLite/PostgreSQL tests. |
| Reporter or correlation pseudonyms are unhashed or exposed | Account/device/network information could leak to the model or reviewers and correlated brigading could inflate qualification. | Accept only privacy-safe IDs, keep them out of model packets/traces, enforce HMAC ownership, and audit access. |
| Correlation metadata is absent or incorrectly populated | A campaign can satisfy recurrence thresholds while looking like independent evidence. | Make independence/correlation derivation an authenticated upstream contract and monitor capped versus raw counts. |
| Poor-review eligibility is weakened | Low ratings alone can turn subjective dislike into a product-defect case. | Keep the low-rating plus objective-issue gate versioned, fixture-backed, and separately evaluated. |
| Urgent signals are downgraded by classifier output | A model may label an urgent safety report as a normal product issue. | Structured urgent policy must own queue/priority/SLA; classifier output may describe but never downgrade it. |
| Invalid model citations are accidentally trusted | A plausible summary can cite another case or an invented evidence ID. | Validate every citation against the packet and abstain to `INSUFFICIENT_EVIDENCE`; never retry recursively. |
| Classifier summary contains unsupported facts | Human reviewers may treat prose as catalog truth. | Render only typed evidence/facts, escape untrusted text, and retain a claim/citation audit. |
| Redaction regexes miss local PII formats | Raw phone, email, address, order, or payment data can enter model prompts/logs. | Add locale-specific PII fixtures, upstream structured minimization, restricted raw storage, and trace snapshots. |
| Redaction is too aggressive | Important issue details can be removed, lowering recall or hiding a safety symptom. | Retain a restricted source checksum and redaction report; measure redaction loss and route uncertain cases to privacy triage. |
| Asset references become fetchable by the agent | A future attachment adapter could introduce SSRF, malware, or prompt-injection content. | Keep references inert in Tier 1; add a separate allowlisted asset service and security review before any fetch/OCR. |
| Near-duplicate evidence is over-merged | Distinct incidents can lose source diversity and recurrence evidence. | Keep append-only signal rows, preserve occurrence/source counts, and tune similarity only inside one exact identity/reason/window group. |
| Near-duplicate evidence is under-merged | One copied report can dominate the packet and mislead reviewers. | Monitor duplicate/correlation rates, retain raw counts for abuse analysis, and cap packet entries deterministically. |
| The policy JSON is edited without approval | Thresholds, queue names, or SLAs can change qualification or urgency without an accountable decision. | Activate immutable policy versions through a separate authorized service and record owner/decision/audit metadata. |
| Queue delivery is non-idempotent | A retry can create duplicate review work or duplicate urgent pages. | Use a unique `(case_id, policy_version, route)` delivery key, outbox semantics, and replay tests. |
| Queue delivery succeeds but the process crashes before case persistence | Reviewers may see a case reference that cannot be reconstructed. | Couple durable case/route/outbox writes transactionally; if unavailable, retain `SAFE_TRIAGE` and retry idempotently. |
| Signal idempotency hash is calculated differently | Replayed intake may duplicate a signal or silently accept a changed request. | Share one canonical serializer and enforce `(scope, idempotency_key, request_hash)` conflict semantics in the DB. |
| Multiple workers create the same case | Duplicate case IDs split evidence and reviewer history. | Unique group key constraint plus compare-and-swap case creation; test concurrent delivery. |
| Append-only event hash chain is not transactional | Incident reconstruction can show impossible lifecycle history. | Insert event, case update, decision, and outbox record in one short transaction; verify the chain on reads/backups. |
| Reviewer authorization is checked only in the API | A direct service call could record an unauthorized decision or catalog correction. | Enforce `QUALITY_REVIEWER` and ownership inside the application service and persistence boundary; audit denials. |
| `CORRECT_CATALOG` is wired directly to publication | A review recommendation could become autonomous catalog mutation. | Keep Catalog Operations proposal creation as a separate authorized service; the Sentinel has no publication port. |
| Human decision fields are not append-only | Rewriting a decision destroys audit and makes reopen history unreliable. | Store immutable decisions/events and create a new state event for reopen/close. |
| Missing snapshots are treated as no defect | Historical data outage could suppress a real safety issue. | Preserve the urgent case and route data repair/manual review; never convert missing evidence into `NO_ACTION`. |
| Missing evidence is treated as verified negative evidence | Unknown catalog fields can be mistaken for proof that a report is false. | Keep `UNKNOWN`/missing information explicit and use `INSUFFICIENT_EVIDENCE` where required. |
| Model/provider timeout blocks urgent handling | A dependency outage can hide safety work behind a model call. | Qualification and urgent route must complete deterministically; model failure becomes insufficient-evidence triage. |
| Queue or catalog latency exhausts worker capacity | Backlogs can hide urgent cases and affect shopper resources if pools are shared. | Separate worker bulkhead, deadlines, queue-age metrics, and urgent backlog alarms. |
| Evidence packet grows with group size | Large incidents can exceed model/token or storage budgets and increase latency. | Enforce structured/prose caps before model work and record selected IDs/counts only in traces. |
| Policy source classes drift between producers | Diversity thresholds become incomparable across channels. | Version the source-class vocabulary and reject unknown producer values into intake correction. |
| Timezone/clock differences alter qualification | Reports near midnight or DST boundaries can move into another policy window. | Require UTC-aware timestamps, inject clocks in tests, and run boundary fixtures on every adapter. |
| Asset/source retention deletes case evidence | A routed case may no longer be reviewable or auditable. | Retention must preserve redacted evidence/checksums referenced by unresolved cases and apply deletion policy separately to restricted raw content. |
| SQL migration changes JSON/check/index semantics | SQLite development can pass while PostgreSQL production behaves differently. | The DB owner must add SQLite/PostgreSQL contract migrations, constraints, indexes, and concurrency tests before activation. |
| Shared schema/version tuple is omitted | A case can mix a report from one catalog/policy version with evidence from another. | Add the repository-wide compatibility tuple to durable case/evidence/assessment/trace rows before production integration. |
| Model prompt or alias changes without evaluation | Classification distribution and citation behavior can shift silently. | Pin prompt/model versions, replay held-out/adversarial quality fixtures, and require an approval record. |
| Reviewer UI renders raw untrusted text as markup | Catalog/report content can execute or deceive reviewers. | Escape all strings, render safe text only, and add hostile HTML/Markdown fixtures. |
| Route names are accepted from model output | Prompt injection could redirect cases to an attacker-controlled queue. | Route only from versioned policy mapping; reject arbitrary model route fields. |
| Tier 2 semantic clustering is added globally | Similar wording across products/listings can violate Tier 1 identity boundaries. | Keep clustering partitioned by exact identity/reason/time and require human-confirmed links. |
| Tier 3 burst signals become enforcement | Popularity or sale season can cause automatic suppression. | Keep bursts priority-only, partition baselines, require human scope confirmation, and preserve the no-enforcement capability test. |

The branch is complete only for the Tier 1 contract and offline workflow. These
items must be revisited when the shared database, catalog snapshot service,
model gateway, review queue, authentication, API, or operations policy is
integrated.
