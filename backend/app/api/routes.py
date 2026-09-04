"""API routes to the frozen response contract (AGENTS_CONTRACT.md §8).

| Method | Path                       | Response                              |
|--------|----------------------------|---------------------------------------|
| GET    | /api/events                | {events: EventRead[], count: int}     |
| GET    | /api/events/{id}/audit     | {event: EventRead, trail: AuditRead[]}|
| POST   | /api/pipeline/run          | {metrics: MetricsBlock, ran_at: str}  |
| GET    | /api/metrics               | MetricsBlock                          |

All persistence via ``app.db.store``; metrics via ``app.agents.audit``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app import rag
from app.agents import audit
from app.config import get_settings
from app.data import generate
from app.db import store
from app.db.store import AuditRead, EventRead
from app.pipeline import run as run_pipeline

router = APIRouter(prefix="/api", tags=["revenue-recovery"])


@router.get("/events")
def list_events() -> dict[str, Any]:
    with store.get_session() as session:
        events = store.all_events(session)
        return {
            "events": [EventRead.model_validate(e, from_attributes=True) for e in events],
            "count": len(events),
        }


@router.get("/events/{event_id}/audit")
def event_audit(event_id: str) -> dict[str, Any]:
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        trail = store.get_audit_trail(session, event_id)
        return {
            "event": EventRead.model_validate(event, from_attributes=True),
            "trail": [AuditRead.model_validate(r, from_attributes=True) for r in trail],
        }


@router.get("/events/{event_id}/similar")
def event_similar(event_id: str) -> dict[str, Any]:
    """RAG: the nearest already-classified cases to this event (may be empty
    when the knowledge base / embeddings are unavailable)."""
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        similar = rag.retrieve_similar(session, event, settings=get_settings())
        return {"event_id": event_id, "similar": similar}


@router.get("/metrics")
def get_metrics() -> dict[str, Any]:
    with store.get_session() as session:
        return audit.compute_metrics(session)


@router.post("/pipeline/run")
def pipeline_run(reset: bool = False, count: int = 70, seed: int = 42) -> dict[str, Any]:
    if reset:
        generate.generate(count=count, seed=seed, reset=True)
    metrics = run_pipeline(settings=get_settings())
    return {"metrics": metrics, "ran_at": datetime.now(timezone.utc).isoformat()}
