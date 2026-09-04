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

from pydantic import BaseModel, Field

from app import rag
from app.agents import audit, ptp, sequencer, voice
from app.config import get_settings
from app.data import generate
from app.db import store
from app.db.store import AuditRead, EventRead
from app.pipeline import run as run_pipeline

router = APIRouter(prefix="/api", tags=["revenue-recovery"])


class PTPRequest(BaseModel):
    promised_date: datetime = Field(description="Target date by which customer committed to pay")
    notes: str | None = Field(default=None, description="Optional call notes or conversation summary")


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


@router.get("/events/{event_id}/voice")
def event_voice_script(event_id: str) -> dict[str, Any]:
    """Hinglish Voice Recovery: Generate or fetch conversational phone script."""
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        script = voice.generate_hinglish_voice_script(event, settings=get_settings())
        return {"event_id": event_id, "script": script}


@router.get("/events/{event_id}/sequencer")
def event_retry_sequencer(event_id: str) -> dict[str, Any]:
    """Mandate Retry Sequencer: Multi-step rail-adaptive retry schedule."""
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        schedule = event.retry_schedule or sequencer.plan_retry_sequence(event)
        return {
            "event_id": event_id,
            "rail": sequencer._detect_rail(event),
            "schedule": schedule,
        }


@router.post("/events/{event_id}/ptp")
def record_event_ptp(event_id: str, body: PTPRequest) -> dict[str, Any]:
    """Promise-to-Pay (PTP) Tracker: Record promise-to-pay date and pause escalation."""
    with store.get_session() as session:
        try:
            updated_event = ptp.record_promise_to_pay(
                session,
                event_id=event_id,
                promised_date=body.promised_date,
                notes=body.notes,
            )
            return {
                "status": "ok",
                "event": EventRead.model_validate(updated_event, from_attributes=True),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")


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
