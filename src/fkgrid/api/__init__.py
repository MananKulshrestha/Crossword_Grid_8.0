"""FastAPI delivery layer for the existing query-recovery workflow."""

from .main import app, create_app

__all__ = ["app", "create_app"]
