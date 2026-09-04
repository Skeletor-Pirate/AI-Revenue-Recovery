"""REST API routers for the AI Revenue Recovery backend.

One router, mounted in ``app.main``. Response shapes are frozen in
``app/agents/AGENTS_CONTRACT.md`` §8 and mirrored by
``frontend/src/api/fixtures.json``.
"""

from app.api.routes import router

__all__ = ["router"]
