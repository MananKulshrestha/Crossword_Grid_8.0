# FK GRiD deterministic tool layer

This branch combines the agentic chat and speech-to-text workflow with a
deterministic, provider-neutral implementation of the committed non-cart tool
contracts. It is an in-memory integration and adapter seam, not a claim that
the production database, hybrid retrieval index, live research provider,
review queue, or deployment image already exists.

The static allowlist is in `src/fkgrid/tools/registry.py`; it contains 73 tools
across shopper runtime, Query Recovery Tier 2, Catalog Language Tier 1/Tier 2
supporting operations, Catalog Operations Tier 2, and Quality Sentinel Tier 1.
Cart tools are intentionally absent. No cart module, cart state, cart mutation,
cart target resolver, or cart revalidation code was added.

The implementation guarantees locally:

- strict Pydantic boundary models with unknown-field rejection;
- canonical IDs, compatibility tuples, evidence references, immutable result-set
  IDs, stale ordinal rejection, hard-filter-first retrieval, and stable ties;
- bounded deterministic query enhancement, intent, recovery, research, and
  signed suggestion behavior;
- review-gated lexicon/catalog publication with atomic active-version switching;
- redacted Quality Sentinel evidence and human-review checks;
- a model gateway that accepts structured JSON only and never receives tools;
- adapter method shapes matching the chat, recovery, language, research,
  suggestion, and quality ports;
- explicit runtime composition in `src/fkgrid/tools/integration.py`, wired into
  `ApiRuntime` while the existing shopper-owned cart port remains the cart
  boundary.

Run the one-by-one contract audit with the project Python runtime:

```text
python scripts/contract_audit.py
```

The audit prints one PASS line per tool and finishes with `AUDIT PASS: 73/73`.
The integrated branch also passes the full local pytest suite and the static
compile check. Ruff still reports pre-existing formatting/unused-import work
in the imported reference tool files and is listed as follow-up cleanup rather
than being represented as green here.

See `crisp_report.md` for the ranked handoff and `detailed_report.md` for the
tool-by-tool input/output reconciliation. The copied source audit is preserved
in `source_audit.md`; the final runtime composition is in
`src/fkgrid/tools/integration.py`.
