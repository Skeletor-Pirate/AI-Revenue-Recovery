"""API routes to the frozen response contract (AGENTS_CONTRACT.md §8).

| Method | Path                        | Response                              |
|--------|-----------------------------|---------------------------------------|
| GET    | /api/events                 | {events: EventRead[], count: int}     |
| GET    | /api/events/{id}/audit      | {event: EventRead, trail: AuditRead[]}|
| GET    | /api/tickets                | {tickets: TicketRead[], count, ...}   |
| GET    | /api/tickets/{id}           | {ticket, event, trail}                |
| POST   | /api/tickets/{id}/assign    | {status: "ok", ticket: TicketRead}    |
| POST   | /api/tickets/{id}/resolve   | {status: "ok", ticket: TicketRead}    |
| POST   | /api/events/{id}/playground/start   | {channel, ticket_ref, persona, opening_turn, outcome, history} |
| POST   | /api/events/{id}/playground/message | {turn, outcome, reasoning, history}   |
| POST   | /api/events/{id}/playground/advance | {customer_turn, agent_turn, outcome, reasoning, history} |
| POST   | /api/pipeline/run           | {metrics: MetricsBlock, ran_at: str}  |
| GET    | /api/metrics                | MetricsBlock                          |

All persistence via ``app.db.store``; metrics via ``app.agents.audit``; the
human review queue via ``app.agents.triage``. The Playground endpoints are a
sandboxed rehearsal (``app.agents.playground``) — they read an event for
context but never write to the store or affect ``MetricsBlock``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field

from app import rag
from app.agents import audit, playground, ptp, sequencer, triage, voice, voice_tts
from app.config import get_settings
from app.data import generate
from app.db import store
from app.db.store import AuditRead, EventRead, TicketRead, TicketStatus
from app.pipeline import run as run_pipeline

router = APIRouter(prefix="/api", tags=["revenue-recovery"])


class PTPRequest(BaseModel):
    promised_date: datetime = Field(description="Target date by which customer committed to pay")
    notes: str | None = Field(default=None, description="Optional call notes or conversation summary")


class AssignTicketRequest(BaseModel):
    employee_email: str = Field(description="Email of the employee taking the ticket")


class ResolveTicketRequest(BaseModel):
    employee_email: str = Field(description="Email of the employee closing the ticket")
    outcome: str = Field(description="'resolved' or 'unresolved' — an honest 'could not fix' is a valid close")
    note: str = Field(description="What the reviewer actually did; written verbatim into the audit trail")
    recovered_amount: str | None = Field(
        default=None,
        description="Optional money the human brought in; bounded by what is still at risk",
    )


class RaiseQuestionRequest(BaseModel):
    question: str = Field(description="The customer's question, verbatim")
    channel: str = Field(default="voice_call", description="voice_call | whatsapp_message | email | dashboard")
    employee_email: str | None = Field(default=None, description="Who escalated it, if known")


class PlaygroundStartRequest(BaseModel):
    mode: str = Field(
        default="interactive",
        description="'interactive' (you play the customer/business) or 'auto' (watch two AIs talk)",
    )


class PlaygroundMessageRequest(BaseModel):
    history: list[dict[str, str]] = Field(description="Transcript so far, [{speaker, text}, ...]")
    message: str = Field(description="The tester's line, played as the customer/business")
    channel: str = Field(default="call", description="'call' or 'message'")


class PlaygroundAdvanceRequest(BaseModel):
    history: list[dict[str, str]] = Field(description="Transcript so far, [{speaker, text}, ...]")
    channel: str = Field(default="call", description="'call' or 'message'")


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


@router.get("/events/{event_id}/voice/audio")
def event_voice_audio(event_id: str) -> dict[str, Any]:
    """Hinglish Voice Recovery: Sarvam AI TTS clips for each dialogue turn.

    ``available: false`` (with ``audio: []``) when no ``SARVAM_API_KEY`` is set or
    the provider errors — the dashboard then falls back to the browser voice.
    """
    settings = get_settings()
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        script = voice.generate_hinglish_voice_script(event, settings=settings)
    tts = voice_tts.synthesize_script(script, settings=settings)
    return {"event_id": event_id, **tts}


# --- Simulate / Playground (app/agents/playground.py) ----------------------
# A sandboxed rehearsal: reads the real event for context but never writes to
# the store. Nothing here should ever call insert_ticket / update_event /
# log_action -- see playground.py's module docstring and plan.md §12.

@router.post("/events/{event_id}/playground/start")
def playground_start(event_id: str, body: PlaygroundStartRequest) -> dict[str, Any]:
    """Open a rehearsal session for this case. Writes nothing to the store."""
    mode = body.mode if body.mode in ("interactive", "auto") else "interactive"
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        return playground.start_session(event, mode=mode, settings=get_settings())


@router.post("/events/{event_id}/playground/message")
def playground_message(event_id: str, body: PlaygroundMessageRequest) -> dict[str, Any]:
    """Interactive mode: the tester's line (as the customer/business), then
    one Agent reply."""
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        return playground.send_message(
            event, body.history, body.message, body.channel, settings=get_settings()
        )


@router.post("/events/{event_id}/playground/advance")
def playground_advance(event_id: str, body: PlaygroundAdvanceRequest) -> dict[str, Any]:
    """Auto mode: one Customer/Business turn, then one Agent reply — two
    distinctly-prompted LLM calls."""
    with store.get_session() as session:
        event = store.get_event(session, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        return playground.advance_conversation(
            event, body.history, body.channel, settings=get_settings()
        )


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


# --- human review queue (app/agents/triage.py) -----------------------------

@router.post("/events/{event_id}/raise-question")
def raise_customer_question(event_id: str, body: RaiseQuestionRequest) -> dict[str, Any]:
    """Escalate a question the AI cannot answer into the human review queue.

    Called mid-call or from an inbound message when the customer asks something
    outside what the recovery agent can handle.
    """
    with store.get_session() as session:
        try:
            ticket = triage.raise_customer_question(
                session,
                event_id,
                question=body.question,
                channel=body.channel,
                employee_email=body.employee_email,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "status": "ok",
            "ticket": TicketRead.model_validate(ticket, from_attributes=True),
        }


@router.get("/tickets")
def list_tickets(status: str | None = None) -> dict[str, Any]:
    """The human review queue, most urgent first (priority desc, then oldest)."""
    with store.get_session() as session:
        tickets = store.get_tickets(session, status)
        rows = [TicketRead.model_validate(t, from_attributes=True) for t in tickets]
        return {
            "tickets": rows,
            "count": len(rows),
            "open_count": sum(1 for t in tickets if str(t.status) == TicketStatus.OPEN.value),
            "under_review_count": sum(
                1 for t in tickets if str(t.status) == TicketStatus.UNDER_REVIEW.value
            ),
        }


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str) -> dict[str, Any]:
    """One ticket plus everything the reviewer needs: the event and its full trail."""
    with store.get_session() as session:
        ticket = store.get_ticket(session, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail=f"no such ticket: {ticket_id}")
        event = store.get_event(session, ticket.event_id)
        trail = store.get_audit_trail(session, ticket.event_id)
        return {
            "ticket": TicketRead.model_validate(ticket, from_attributes=True),
            "event": (
                EventRead.model_validate(event, from_attributes=True)
                if event is not None
                else None
            ),
            "trail": [AuditRead.model_validate(r, from_attributes=True) for r in trail],
        }


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: str, body: AssignTicketRequest) -> dict[str, Any]:
    """An employee takes a ticket: open -> under_review."""
    with store.get_session() as session:
        try:
            ticket = triage.assign_ticket(
                session, ticket_id, employee_email=body.employee_email
            )
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no such ticket: {ticket_id}")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "status": "ok",
            "ticket": TicketRead.model_validate(ticket, from_attributes=True),
        }


@router.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: str, body: ResolveTicketRequest) -> dict[str, Any]:
    """Close a ticket with the reviewer's account of what they did.

    ``outcome`` is ``resolved`` or ``unresolved``; ``recovered_amount`` is
    optional and bounded by what is still at risk on the event.
    """
    with store.get_session() as session:
        try:
            ticket = triage.resolve_ticket(
                session,
                ticket_id,
                employee_email=body.employee_email,
                outcome=body.outcome,
                note=body.note,
                recovered_amount=body.recovered_amount,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no such ticket: {ticket_id}")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "status": "ok",
            "ticket": TicketRead.model_validate(ticket, from_attributes=True),
        }


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
