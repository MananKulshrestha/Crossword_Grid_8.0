# ADR 0001 — Catalog Language Tier 2 boundary

## Decision

Implement Tier 2 as an offline, bounded Python workflow with Pydantic contracts,
provider-neutral ports, deterministic fakes, and an immutable flat runtime lookup.
Do not add LangGraph, online browsing, raw-log ingestion, database startup, or
automatic lexicon activation.

## Defaults and ownership

- Locale: `en-IN`; normalization is NFKC/casefold plus conservative reviewed rules.
- Evidence gate: five distinct privacy-safe source groups, two source classes,
  seven-day-capable evidence windows, and no source concentration above 40%.
- Proposer and critic are separate tool-less structured calls. The critic emits
  concern codes only.
- The default FastAPI assembly uses Gemma for both calls through a provider-neutral
  adapter. DeepInfra is the default transport (`google/gemma-4-26B-A4B-it`); Ollama
  and other OpenAI-compatible transports can be selected through environment settings.
- Canonical target IDs come only from `CanonicalVocabularyPort`.
- DB schema/queries are a handoff to the database owner; this branch does not run
  migrations or create a connection.
- Regression/shadow gold fixtures remain owned by the evaluation/retrieval team;
  this branch provides typed harness contracts and deterministic checks.

## Deferred decisions

Gemma hosting alias/credentials/cost cap, SQLAlchemy transaction implementation,
review UI/authentication, catalog vocabulary materialization, and deployment
artifact storage are intentionally left behind ports.
