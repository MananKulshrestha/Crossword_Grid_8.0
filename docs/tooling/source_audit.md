# Integration audit

## Existing worktrees

The specialist worktrees were inspected without modifying their source:

- `repo/worktrees/manan-chat-agentic-workflow`
- `repo/worktrees/manan-query-expansion-agent`
- `repo/worktrees/manan-query-recovery`
- `repo/worktrees/quality-sentinel`

Their branches, uncommitted changes, source files, and local contracts remain
isolated. The final branch owns the adapter and composition code below.

## Final worktree

The integrated implementation and reports were added only in:

`repo/worktrees/manan-final-agentic-chat`

The branch is `manan/final-agentic-chat`. Git branch names cannot contain the
requested spaces, so `Manan/final agentic chat` was normalized to this
hyphenated name. It was based on the speech-to-text branch and then merged with
the latest agentic chat workflow.

## Compatibility changes that were intentionally not applied elsewhere

The existing worktrees use local Pydantic models and ports. Rather than editing
their source, this branch adds `src/fkgrid/tools/compat.py` and
`src/fkgrid/tools/workflow_adapters.py`, which expose the same method names and
translate/project shared models at the adapter boundary. The exact mapping is
documented in `detailed_report.md`. Existing owners can adopt the adapters or
translate the shared models in their own worktree.

`src/fkgrid/tools/integration.py` builds the adapters and 73-tool registry from
the chat fixture records. `src/fkgrid/api/runtime.py` uses that composition for
catalog, reference, recovery, research, and suggestion ports; the existing
shopper workflow continues to own cart behavior.

## Cart boundary

No cart tool was registered. The final branch intentionally keeps the existing
shopper-owned cart model, target resolver, and revalidation path in place.
