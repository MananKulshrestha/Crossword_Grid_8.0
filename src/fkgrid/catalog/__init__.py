"""Real, Retrieval-API + MySQL-backed CatalogSearchPort implementation.

Kept as its own top-level package (sibling to agentic/, api/, cart/, db/,
tools/), mirroring fkgrid.cart's own module docstring rationale: this is
wired directly into TurnOrchestrator as its own `catalog=` argument, not
folded into tools.integration.RuntimeTooling or fkgrid.tools at all.
"""

from __future__ import annotations

from .adapter import EXPECTED_CATALOG_VERSION, RETRIEVAL_API_URL, RetrievalCatalogAdapter

__all__ = ["RetrievalCatalogAdapter", "EXPECTED_CATALOG_VERSION", "RETRIEVAL_API_URL"]
