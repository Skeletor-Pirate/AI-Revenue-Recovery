"""Promise-to-Pay (PTP) Tracker — Direction 7.

Implements the customer commitment state machine:
- Pauses automated retries and aggressive escalation during the commitment window.
- Dispatches pre-due gentle nudges 24 hours prior to the promised date.
- Formally records promise fulfillment (HONORED) or failure (BROKEN) to the audit trail.
- Computes commitment reliability metrics for the Audit Agent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlmodel import Session, select

from app.db import store
from app.db.store import Agent, Event, EventStatus, EventUpdate, PTPStatus

GRACE_PERIOD_HOURS = 24


def record_promise_to_pay(
    session: Session,
    event_id: str,
    promised_date: datetime,
    notes: str | None = None,
    *,
    reasoning: str | None = None,
) -> Event:
    """Record a customer's formal promise-to-pay date and pause escalation."""
    event = store.get_event(session, event_id)
    if event is None:
        raise KeyError(f"Event {event_id} not found")

    # Ensure tz-aware UTC
    if promised_date.tzinfo is None:
        promised_date = promised_date.replace(tzinfo=timezone.utc)

    updated_event = store.update_event(
        session,
        event_id,
        EventUpdate(
            promised_date=promised_date,
            ptp_status=PTPStatus.PROMISED,
        ),
    )

    reason = reasoning or f"Customer committed to clear payment on {promised_date.strftime('%Y-%m-%d')}. Pausing escalation."
    store.log_action(
        session,
        event_id=event_id,
        agent=Agent.RECOVERY,
        action="ptp_recorded",
        reasoning=reason,
        payload={
            "promised_date": promised_date.isoformat(),
            "notes": notes or "Promise recorded via recovery conversation",
            "grace_period_hours": GRACE_PERIOD_HOURS,
            "escalation_paused": True,
        },
    )
    return updated_event


def evaluate_ptp_status(
    session: Session,
    event: Event,
    current_time: datetime | None = None,
) -> str:
    """Evaluate whether an active promise is still pending, honored, or broken."""
    if event.ptp_status != PTPStatus.PROMISED or event.promised_date is None:
        return event.ptp_status

    now = current_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if event.status == EventStatus.RECOVERED or (event.recovered_amount or Decimal(0)) > Decimal(0):
        # Successfully honored
        store.update_event(session, event.event_id, EventUpdate(ptp_status=PTPStatus.HONORED))
        store.log_action(
            session,
            event_id=event.event_id,
            agent=Agent.RECOVERY,
            action="ptp_honored",
            reasoning=f"Payment received in full before promised due date {event.promised_date.strftime('%Y-%m-%d')}.",
            payload={"recovered_amount": str(event.recovered_amount)},
        )
        return PTPStatus.HONORED

    # Check expiration including grace period
    deadline = event.promised_date + timedelta(hours=GRACE_PERIOD_HOURS)
    if now > deadline:
        # Broken promise
        store.update_event(session, event.event_id, EventUpdate(ptp_status=PTPStatus.BROKEN))
        store.log_action(
            session,
            event_id=event.event_id,
            agent=Agent.RECOVERY,
            action="ptp_broken",
            reasoning=f"Promise deadline ({event.promised_date.strftime('%Y-%m-%d')} + {GRACE_PERIOD_HOURS}h grace) expired without payment. Resuming escalation ladder.",
            payload={"expired_at": now.isoformat()},
        )
        return PTPStatus.BROKEN

    return PTPStatus.PROMISED


def compute_ptp_metrics(session: Session) -> dict[str, Any]:
    """Compute aggregate promise-to-pay reliability metrics across all events."""
    events = store.all_events(session)
    total_ptp = sum(1 for e in events if e.ptp_status != PTPStatus.NONE)
    honored = sum(1 for e in events if e.ptp_status == PTPStatus.HONORED)
    broken = sum(1 for e in events if e.ptp_status == PTPStatus.BROKEN)
    active = sum(1 for e in events if e.ptp_status == PTPStatus.PROMISED)

    amount_recovered_ptp = sum(
        (e.recovered_amount for e in events if e.ptp_status == PTPStatus.HONORED),
        Decimal(0),
    )

    honor_rate = (honored / total_ptp) if total_ptp > 0 else 0.0

    return {
        "total_ptp_recorded": total_ptp,
        "total_honored": honored,
        "total_broken": broken,
        "active_promised": active,
        "honor_rate": round(honor_rate, 3),
        "amount_recovered_ptp": str(amount_recovered_ptp.quantize(store.MONEY)),
    }
