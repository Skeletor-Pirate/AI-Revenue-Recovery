"""The four-agent AI Revenue Recovery pipeline.

Runtime order: Detection -> Diagnosis -> Recovery -> Audit. Each agent reads the
``status`` the previous one wrote and persists only through ``app.db.store``.
The frozen cross-agent contract is ``AGENTS_CONTRACT.md`` in this package.

Each stage module exposes ``run(session, *, settings=None) -> list[str]``.
``audit`` additionally exposes ``compute_metrics(session) -> dict`` (the
``MetricsBlock`` that ``app.pipeline`` and the API return).
"""

from app.agents import audit, detection, diagnosis, recovery

__all__ = ["detection", "diagnosis", "recovery", "audit"]
