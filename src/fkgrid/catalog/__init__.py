"""Real, RA-backed CatalogSearchPort implementation.

Kept as its own top-level package (sibling to agentic/, api/, cart/, db/,
tools/), mirroring cart/'s own layout, per AGENTIC_INTEGRATION.md section 5's
recommendation: a new adapter implementing CatalogSearchPort directly,
bypassing tools/compat.py's CatalogSearchPortAdapter -> DeterministicCatalog
fixture chain the same way cart.adapter.DatabaseCartAdapter already bypasses
FixtureCartPort/tools/.
"""

from __future__ import annotations

from .adapter import RetrievalCatalogAdapter

__all__ = ["RetrievalCatalogAdapter"]
