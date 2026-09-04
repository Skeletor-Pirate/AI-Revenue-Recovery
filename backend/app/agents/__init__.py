"""The AI Revenue Recovery pipeline.

Runtime order: Detection -> Diagnosis -> Recovery -> Triage -> Audit. Each agent
reads the ``status`` the previous one wrote and persists only through
``app.db.store``. The frozen cross-agent contract is ``AGENTS_CONTRACT.md`` in
this package.

The four core agents do the money work; ``triage`` runs once every event is
terminal and opens a human-review ticket for each case the automation could not
carry further (see triage.py).

Each stage module exposes ``run(session, *, settings=None) -> list[str]``.
``audit`` additionally exposes ``compute_metrics(session) -> dict`` (the
``MetricsBlock`` that ``app.pipeline`` and the API return).
"""

from app.agents import audit, detection, diagnosis, recovery, triage

__all__ = ["detection", "diagnosis", "recovery", "triage", "audit"]
