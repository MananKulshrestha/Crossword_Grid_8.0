# ADR 0001 — Query Recovery Tier 2 baseline

## Status

Accepted for the isolated Query Recovery implementation; provider/database activation remains owned by later integration work.

## Decisions

- Implement Tier 1 deterministic approved-lexicon recovery plus Tier 2 constrained generative recovery. Tier 3 multi-hypothesis recovery is not implemented.
- Keep ordinary Python functions and typed ports as the workflow spine. No LangGraph or second persistence/state machine is introduced.
- Use Pydantic v2 strict models at the planner/tool boundary. Planner output is parsed once and then semantically validated against the supplied concept allowlist, compatibility tuple, and hard-filter hash.
- Keep the shared model gateway and configured provider behind `RecoveryPlannerPort`/`SharedGatewayRecoveryPlanner`. The default tests use a deterministic fake and do not require credentials or network access.
- Use a plain parameterized SQLite adapter and migration fragment as a portable contract for the future SQLAlchemy/PostgreSQL owner. No database is provisioned by this branch.
- Persist recovery events as sanitized append-only evidence. Event-write failure cannot change the primary recovery response.

## Open integration decisions

- The primary LLM provider/model alias, credentials owner, structured-output transport, and provider retry implementation are not selected here; the configured adapter must preserve one logical planner call.
- The catalog-language owner must reconcile the documented `lexicon_mappings` columns with the shared lexicon schema before activation.
- The retrieval owner must supply exact tuple-pinned `RetrievalRun` summaries and prove hard-filter/result evidence parity.
- The session/orchestrator owner must store the returned clarification packet as a state-bound pending clarification and commit the event with the turn/outbox transaction.
- Threshold calibration data and approved `recovery_policy_v1` ownership remain to be supplied by evaluation/retrieval owners.
