"""Track 6 -- the real, database-backed CartPort implementation.

Kept as its own top-level package (sibling to agentic/, api/, db/, tools/)
rather than folded into fkgrid.tools: cart is deliberately wired directly
into TurnOrchestrator as its own `cart=` argument, not through
tools.integration.RuntimeTooling -- see that module's own docstring
("the static registry is kept separate from the cart port owned by the
shopper workflow").
"""

from __future__ import annotations

from .adapter import DatabaseCartAdapter

__all__ = ["DatabaseCartAdapter"]
