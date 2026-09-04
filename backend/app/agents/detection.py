"""Detection Agent — build-order step 3.

First stage of the AI Revenue Recovery pipeline. Reads the freshly generated
batch (events at ``status="detected"``) and, for each event, writes exactly one
audit row:

* ``flagged_at_risk``      — confirmed genuine at-risk revenue; the event stays
  ``detected`` for the Diagnosis Agent.
* ``routed_to_exception``  — an obvious non-recoverable (nothing left at risk,
  unsupported event type, non-INR currency, no failure signal, invoice not
  actually overdue); the event moves to ``status="exception"``.

No root-cause analysis, no recovery, no metrics — those are later agents.

Persistence goes only through ``app.db.store``. The agent is deterministic and
idempotent: an event that already has a ``detection`` audit row is examined but
not re-logged, so a second run never double-writes.

Public API:
    run(session, *, settings=None) -> list[str]   # every event_id examined
    classify(event) -> tuple[bool, str]           # pure, no I/O
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.db import store
from app.db.store import Agent, EventStatus, EventType, MONEY

# event types this pipeline knows how to recover
_SUPPORTED_EVENT_TYPES = {t.value for t in EventType}

"""If a payment or recurring subscription fails at the bank, the bank must tell us why (the error code). 
If it doesn't, the AI cannot diagnose the problem.
"""

_NEEDS_FAILURE_SIGNAL = {
    EventType.FAILED_PAYMENT.value,
    EventType.EXPIRED_MANDATE.value,
}

DETECTION_ACTIONS = {"flagged_at_risk", "routed_to_exception"}


def _amount_at_risk(event: Any) -> Decimal:
    amount = Decimal(str(event.amount or 0))
    recovered = Decimal(str(event.recovered_amount or 0))
    return (amount - recovered).quantize(MONEY)


def classify(event: Any) -> tuple[bool, str]:
    """Decide whether *event* is genuine at-risk revenue.

    Returns ``(True, reason)`` when the event should be flagged and left for
    Diagnosis, or ``(False, reason)`` when it is an obvious non-recoverable that
    should be routed straight to ``exception``. ``reason`` is always a non-empty
    phrase describing the decision.
    """
    amount = Decimal(str(event.amount or 0))
    recovered = Decimal(str(event.recovered_amount or 0))
    event_type = str(event.event_type)
    currency = (event.currency or "").upper()
    raw_reason = (event.raw_failure_reason or "").strip()

    if amount <= 0:
        return False, "event carries no positive amount, so there is nothing to recover"

    if recovered >= amount:
        return (
            False,
            "recovered amount already covers the full charge, no revenue remains at risk",
        )

    if event_type not in _SUPPORTED_EVENT_TYPES:
        return False, f"event type {event_type!r} is not supported by the recovery pipeline"

    if currency != "INR":
        return False, "non-INR currency out of scope for test-mode recovery"

    if event_type in _NEEDS_FAILURE_SIGNAL and not raw_reason:
        return False, "no failure signal to diagnose"

    if event_type == EventType.OVERDUE_INVOICE.value and (event.days_overdue or 0) <= 0:
        return False, "invoice is not past its due date, so no revenue is overdue"

    return (
        True,
        "event is confirmed as genuine at-risk revenue and is handed to Diagnosis for root-cause analysis",
    )


def _already_processed(session: store.Session, event_id: str) -> bool:
    trail = store.get_audit_trail(session, event_id)
    return any(row.agent == Agent.DETECTION.value for row in trail)


def run(session: store.Session, *, settings: Any = None) -> list[str]:
    """Process every event at ``status="detected"``.

    Writes one audit row per not-yet-processed event and routes obvious
    non-recoverables to ``exception``. Returns the ids of every event examined
    (including ones skipped because they were already processed).
    """
    examined: list[str] = []

    for event in store.get_events_by_status(session, EventStatus.DETECTED.value):
        examined.append(event.event_id)

        if _already_processed(session, event.event_id):
            continue

        keep, reason = classify(event)

        if keep:
            store.log_action(
                session,
                event_id=event.event_id,
                agent=Agent.DETECTION,
                action="flagged_at_risk",
                reasoning=(
                    f"Confirmed {event.event_type} for customer {event.customer_id} as "
                    f"at-risk revenue of {_amount_at_risk(event)} {event.currency}; "
                    f"leaving it detected for the Diagnosis Agent."
                ),
                payload={
                    "amount_at_risk": str(_amount_at_risk(event)),
                    "event_type": str(event.event_type),
                },
            )
        else:
            store.update_event(
                session,
                event.event_id,
                status=EventStatus.EXCEPTION,
            )
            store.log_action(
                session,
                event_id=event.event_id,
                agent=Agent.DETECTION,
                action="routed_to_exception",
                reasoning=(
                    f"Routed {event.event_type} for customer {event.customer_id} straight "
                    f"to exception: {reason}."
                ),
                payload={"reason": reason},
            )

    return examined
