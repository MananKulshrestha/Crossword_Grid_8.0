"""FastAPI delivery layer for the shopper-facing agentic workflow."""

from .main import app, create_app

__all__ = ["app", "create_app"]
