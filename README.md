# FK GRiD Catalog Language — Tier 2

This branch implements the Tier 2 evidence-driven lexicon workflow described by:

- `Agent-Plans/catalog-language-query-expansion-agent.md`
- `Agent-Plans/Workflows/catalog-language/tier-2-evidence-driven-lexicon-operations.md`
- `technical plans/09-QUERY-RECOVERY-AGENT.md`
- `technical plans/10-CATALOG-LANGUAGE-AGENT.md`
- `technical plans/24-AGENTIC-PIPELINE-AND-LLM-CONTRACTS.md`

The implementation is intentionally offline and contract-first. It does not create,
migrate, or connect to a database. `db/catalog_language_schema.sql` and
`fkgrid.adapters.catalog_language.sql` define the tables/queries a database owner
must wire to the ports.

## Workflow

```text
privacy-safe aggregates
  -> threshold gate
  -> deterministic spelling/morphology clustering
  -> pinned canonical vocabulary
  -> bounded allowed-target retrieval
  -> proposer model (select/abstain)
  -> independent critic (concern codes only)
  -> deterministic evidence score + mapping validation
  -> regression + shadow evaluation
  -> human review
  -> compare-and-swap activation
```

The runtime lookup is deterministic, immutable-snapshot based, scoped, compatible-
version checked, and capped at three mappings per term. It never reads memory,
purchase history, raw transcripts, or user logs, and it never creates hard filters.

## Integration handoff

Implementations still required outside this branch are represented by ports:

- DB owner: `EvidenceAggregationPort`, `CanonicalVocabularyPort`, `ActiveLexiconPort`,
  and durable proposal/review/activation persistence behind the SQL query catalog.
- Model owner/provider adapter: `CatalogLanguageModelPort`; the fake gateway and
  versioned prompts are included for contract tests.
- Catalog/retrieval owner: `TargetRetrievalPort` and the regression/shadow fixtures.
- Admin/review owner: `ReviewPort` and `ActivationPort` with role checks and audit.

No active lexicon is changed by a workflow run unless an authorized review decision
and activation CAS both succeed.

## Local checks

```text
python -m pytest
python -m ruff check .
python -m mypy src
```

The repository environment may use `uv`; no database or provider secret is needed
for the included deterministic fakes/tests.

