"""Human Review Triage — the bridge between the automation and a person.

The four core agents can carry a case only so far. Some outcomes are, honestly,
a human's job:

* a matching-signature cluster the Diagnosis Agent halted as suspected fraud,
* a failure with **no gateway error code** to reason from,
* a money action parked above the human-approval gate (plan.md §6),
* an invoice escalation ladder that reached its final "human handoff" rung,
* retries/escalation that ran to exhaustion with no customer response,
* a customer asking, mid-call or in a message, something the AI cannot answer.

This module opens one **ticket** per such case, scores it so the queue is
priority-ordered, and gives a person a bounded, audited way to take it, record
what they did, and close it ``resolved`` or ``unresolved``.

Two important design points:

* **"Tried 3x, no response" is not an automation failure.** The Recovery Agent
  did exactly what its stopping rules allow. But it is still lost revenue, and a
  human may choose one more *personal* attempt — compliant precisely because a
  person, not the automation, is now deciding, and it is logged. Those tickets
  therefore open at the **lowest** priority band, visible but never crowding out
  fraud or approval-gated work.
* **Human money is attributed as human money.** Resolving a ticket may record an
  amount recovered; it lands in ``Event.human_recovered_amount`` so the batch
  metrics can report AI-recovered and human-recovered separately, honestly.

Entry points:

* ``run(session, *, settings=None) -> list[str]`` — the pipeline stage. Opens
  tickets for terminal events that don't already have one. Idempotent.
* ``assign_ticket`` / ``resolve_ticket`` / ``raise_customer_question`` — the
  three human actions, each writing an ``agent="human"`` audit row.
* ``compute_ticket_metrics(session) -> dict`` — the queue block the Audit Agent
  folds into the ``MetricsBlock``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlmodel import Session

from app.db import store
from app.db.store import (
    Agent,
    Event,
    EventStatus,
    EventUpdate,
    MONEY,
    RootCause,
    Ticket,
    TicketCreate,
    TicketReason,
    TicketStatus,
    TicketUpdate,
)

_ZERO = Decimal("0.00")

# --- priority scoring ------------------------------------------------------
# Higher = more urgent. The base is the *reason*; a bounded amount weight
# breaks ties so a Rs 40,000 case outranks a Rs 400 one with the same reason.
# These are deliberate, visible constants — the queue's ordering is a product
# decision, not an emergent one.

PRIORITY_BASE: dict[str, int] = {
    TicketReason.SUSPECTED_FRAUD: 90,      # money may be leaving; halt is already in force
    TicketReason.CUSTOMER_QUESTION: 80,    # a real person is waiting on an answer
    TicketReason.AWAITING_APPROVAL: 75,    # recovery is blocked until someone signs off
    TicketReason.EXCEPTION_NO_ERROR: 65,   # the AI has no signal; needs human eyes
    TicketReason.INVOICE_HANDOFF: 55,      # ladder ended where it was designed to end
    TicketReason.OTHER: 40,
    TicketReason.STALLED_NO_RESPONSE: 25,  # automation behaved correctly; optional follow-up
}

AMOUNT_WEIGHT_CAP = 15          # max points the amount can contribute
AMOUNT_WEIGHT_DIVISOR = 5000    # Rs per point, capped above

# Priority bands the dashboard labels. Kept here so backend and UI agree.
PRIORITY_BANDS = (("critical", 85), ("high", 60), ("medium", 40), ("low", 0))

VALID_OUTCOMES = ("resolved", "unresolved")
VALID_CHANNELS = ("voice_call", "whatsapp_message", "email", "dashboard")


def priority_band(priority: int) -> str:
    """The label for a numeric priority score."""
    for name, floor in PRIORITY_BANDS:
        if priority >= floor:
            return name
    return "low"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _money(value: Decimal) -> str:
    return str(Decimal(value).quantize(MONEY))


# --- classification --------------------------------------------------------

def _classify(event: Event, trail: list[Any]) -> TicketReason:
    """Why does this event need a human? First match wins, most urgent first.

    Reads the event's own audit trail rather than guessing — the reason a
    reviewer sees is the reason the pipeline actually recorded.
    """
    actions = {row.action for row in trail}

    if (
        str(event.status) == EventStatus.FLAGGED.value
        or "halted_fraud_cluster" in actions
        or str(event.root_cause) == RootCause.SUSPECTED_FRAUD.value
    ):
        return TicketReason.SUSPECTED_FRAUD

    if "raised_customer_question" in actions:
        return TicketReason.CUSTOMER_QUESTION

    if "awaiting_human_approval" in actions:
        return TicketReason.AWAITING_APPROVAL

    for row in reversed(trail):
        if row.action == "routed_to_exception" and row.payload:
            reason = str(row.payload.get("reason", "")).lower()
            if "human handoff" in reason:
                return TicketReason.INVOICE_HANDOFF

    no_error = not (event.raw_failure_reason or "").strip()
    no_cause = event.root_cause is None or (
        str(event.root_cause) == RootCause.UNKNOWN.value
    )
    if no_error and no_cause:
        return TicketReason.EXCEPTION_NO_ERROR

    if "halted_stopping_rule" in actions:
        return TicketReason.STALLED_NO_RESPONSE

    for row in reversed(trail):
        if row.action == "routed_to_exception" and row.payload:
            reason = str(row.payload.get("reason", "")).lower()
            if "no response" in reason or "exhausted" in reason:
                return TicketReason.STALLED_NO_RESPONSE

    return TicketReason.OTHER


def _priority(reason: TicketReason, event: Event) -> int:
    """Reason base + a bounded weight for the money at stake."""
    base = PRIORITY_BASE.get(reason, PRIORITY_BASE[TicketReason.OTHER])
    weight = min(
        AMOUNT_WEIGHT_CAP, float(event.amount or 0) / AMOUNT_WEIGHT_DIVISOR
    )
    return int(base + weight)


def _summarize(reason: TicketReason, event: Event, trail: list[Any]) -> str:
    """One plain-English line telling the reviewer why this landed on their desk."""
    amount = f"Rs {_money(event.amount)}"
    etype = str(event.event_type).replace("_", " ")

    if reason == TicketReason.SUSPECTED_FRAUD:
        return (
            f"{etype} of {amount} halted as part of a matching-signature cluster. "
            f"Recovery refused to act; confirm genuine or abuse before anything else."
        )
    if reason == TicketReason.AWAITING_APPROVAL:
        proposed = "an aggressive recovery action"
        for row in reversed(trail):
            if row.action == "awaiting_human_approval" and row.payload:
                proposed = str(row.payload.get("proposed_action", proposed))
                break
        return (
            f"{proposed} on {amount} is above the human-approval gate. "
            f"Not executed; needs your sign-off."
        )
    if reason == TicketReason.EXCEPTION_NO_ERROR:
        return (
            f"{etype} of {amount} failed with no gateway error code and no root "
            f"cause the classifier could stand behind. Needs human eyes."
        )
    if reason == TicketReason.INVOICE_HANDOFF:
        return (
            f"Invoice for {amount} reached the final rung of the escalation "
            f"ladder ({event.days_overdue} days overdue). Automation stops here."
        )
    if reason == TicketReason.STALLED_NO_RESPONSE:
        return (
            f"{etype} of {amount} ran out of bounded attempts "
            f"({event.attempts_so_far} made) with no customer response. "
            f"Optional personal follow-up."
        )
    if reason == TicketReason.CUSTOMER_QUESTION:
        return f"Customer asked something the AI could not answer on a {etype} of {amount}."
    return f"{etype} of {amount} ended without recovery and needs a human decision."


# --- pipeline stage --------------------------------------------------------

def run(session: Session, *, settings: Any = None) -> list[str]:
    """Open a review ticket for every terminal event that doesn't have one.

    Runs after Recovery and before Audit, when every event has reached
    ``recovered`` / ``exception`` / ``flagged``. Idempotent: an event that
    already carries an open ticket — or one a human already closed — is left
    alone, so re-running the pipeline never duplicates or reopens work.

    Returns the ids of the tickets it opened.
    """
    events = store.get_events_by_status(
        session, [EventStatus.FLAGGED, EventStatus.EXCEPTION]
    )
    opened: list[str] = []

    for event in events:
        if store.tickets_for_event(session, event.event_id):
            continue  # already triaged (open, or closed by a human)

        trail = store.get_audit_trail(session, event.event_id)
        reason = _classify(event, trail)
        priority = _priority(reason, event)
        ticket = store.insert_ticket(
            session,
            TicketCreate(
                ticket_id=store.next_ticket_id(session),
                event_id=event.event_id,
                reason=reason,
                priority=priority,
                summary=_summarize(reason, event, trail),
            ),
        )
        store.log_action(
            session,
            event_id=event.event_id,
            agent=Agent.TRIAGE,
            action="opened_review_ticket",
            reasoning=(
                f"Automation could take this no further ({reason.value}); "
                f"opened {ticket.ticket_id} for human review at priority "
                f"{priority} ({priority_band(priority)})."
            ),
            payload={
                "ticket_id": ticket.ticket_id,
                "reason": reason.value,
                "priority": priority,
                "priority_band": priority_band(priority),
            },
        )
        opened.append(ticket.ticket_id)

    return opened


# --- human actions ---------------------------------------------------------

def assign_ticket(
    session: Session, ticket_id: str, *, employee_email: str
) -> Ticket:
    """An employee takes a ticket: open -> under_review.

    Raises ``KeyError`` for an unknown ticket and ``ValueError`` if the ticket
    is not open (already taken, or already closed) — a ticket has exactly one
    owner, and closed work is never silently reopened.
    """
    ticket = store.get_ticket(session, ticket_id)
    if ticket is None:
        raise KeyError(f"no such ticket: {ticket_id!r}")

    email = (employee_email or "").strip()
    if not email:
        raise ValueError("employee_email is required to take a ticket")

    if str(ticket.status) != TicketStatus.OPEN.value:
        raise ValueError(
            f"ticket {ticket_id} is {ticket.status}, not open; "
            f"only an open ticket can be taken"
        )

    now = datetime.now(timezone.utc)
    updated = store.update_ticket(
        session,
        ticket_id,
        TicketUpdate(
            status=TicketStatus.UNDER_REVIEW,
            assigned_employee_email=email,
            assigned_at=now,
        ),
    )
    store.log_action(
        session,
        event_id=ticket.event_id,
        agent=Agent.HUMAN,
        action="assigned_review_ticket",
        reasoning=f"{email} took ticket {ticket_id} for human review.",
        payload={
            "ticket_id": ticket_id,
            "employee_email": email,
            "reason": str(ticket.reason),
            "priority": ticket.priority,
        },
    )
    return updated


def resolve_ticket(
    session: Session,
    ticket_id: str,
    *,
    employee_email: str,
    outcome: str,
    note: str,
    recovered_amount: Decimal | str | None = None,
) -> Ticket:
    """Close a ticket with the reviewer's own account of what they did.

    ``outcome`` is ``"resolved"`` or ``"unresolved"`` — an honest "I couldn't
    fix this" is a first-class outcome, not a failure to record.

    ``recovered_amount`` is optional money the human brought in. It is bounded:
    it can never push the event's total recovery above the amount that was at
    risk. When supplied it updates ``Event.human_recovered_amount`` (and the
    total), so the dashboard can separate AI-recovered from human-recovered
    money, and flips the event to ``recovered`` once nothing is outstanding.

    Raises ``KeyError`` (unknown ticket) or ``ValueError`` (bad outcome, empty
    note, ticket not under review, or an amount above what was at risk).
    """
    ticket = store.get_ticket(session, ticket_id)
    if ticket is None:
        raise KeyError(f"no such ticket: {ticket_id!r}")

    email = (employee_email or "").strip()
    if not email:
        raise ValueError("employee_email is required to resolve a ticket")

    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {VALID_OUTCOMES}, got {outcome!r}"
        )

    note_text = (note or "").strip()
    if not note_text:
        raise ValueError("a resolution note is required — say what you did")

    if str(ticket.status) != TicketStatus.UNDER_REVIEW.value:
        raise ValueError(
            f"ticket {ticket_id} is {ticket.status}; take it for review before "
            f"resolving it"
        )

    amount = Decimal(str(recovered_amount)) if recovered_amount else _ZERO
    amount = amount.quantize(MONEY)
    if amount < _ZERO:
        raise ValueError("recovered_amount cannot be negative")

    event = store.get_event(session, ticket.event_id)
    if event is None:  # pragma: no cover - FK makes this unreachable
        raise KeyError(f"no such event: {ticket.event_id!r}")

    if amount > _ZERO:
        if outcome != "resolved":
            raise ValueError(
                "recovered_amount can only be recorded on a resolved ticket"
            )
        already = Decimal(event.recovered_amount or 0)
        headroom = Decimal(event.amount) - already
        if amount > headroom:
            raise ValueError(
                f"recovered_amount {_money(amount)} exceeds the "
                f"{_money(headroom)} still at risk on {event.event_id}"
            )

    updated = store.update_ticket(
        session,
        ticket_id,
        TicketUpdate(
            status=(
                TicketStatus.RESOLVED
                if outcome == "resolved"
                else TicketStatus.UNRESOLVED
            ),
            resolution_note=note_text,
            resolution_outcome=outcome,
            recovered_amount=amount,
        ),
    )
    store.log_action(
        session,
        event_id=ticket.event_id,
        agent=Agent.HUMAN,
        action="resolved_review_ticket",
        reasoning=note_text,
        payload={
            "ticket_id": ticket_id,
            "employee_email": email,
            "outcome": outcome,
            "reason": str(ticket.reason),
            "recovered_amount": _money(amount),
        },
    )

    if amount > _ZERO:
        new_total = (Decimal(event.recovered_amount or 0) + amount).quantize(MONEY)
        new_human = (
            Decimal(event.human_recovered_amount or 0) + amount
        ).quantize(MONEY)
        patch_fields: dict[str, Any] = {
            "recovered_amount": new_total,
            "human_recovered_amount": new_human,
        }
        if new_total >= Decimal(event.amount):
            # nothing left outstanding — the case really is recovered now
            patch_fields["status"] = EventStatus.RECOVERED
        store.update_event(session, event.event_id, EventUpdate(**patch_fields))
        store.log_action(
            session,
            event_id=event.event_id,
            agent=Agent.HUMAN,
            action="human_recovered",
            reasoning=(
                f"{email} recovered Rs {_money(amount)} on {event.event_id} by "
                f"working ticket {ticket_id}; counted as human-recovered, not "
                f"automation-recovered."
            ),
            payload={
                "ticket_id": ticket_id,
                "employee_email": email,
                "amount": _money(amount),
                "event_recovered_total": _money(new_total),
                "human_recovered_total": _money(new_human),
            },
        )

    return updated


def raise_customer_question(
    session: Session,
    event_id: str,
    *,
    question: str,
    channel: str = "voice_call",
    employee_email: str | None = None,
) -> Ticket:
    """Escalate a question the AI cannot answer into the review queue.

    The live case: mid-call, or in a reply to an outreach message, a customer
    asks something outside what the recovery agent can handle. Rather than
    improvise, the conversation is handed to a person with the question
    recorded verbatim.

    Raises ``KeyError`` for an unknown event and ``ValueError`` for an empty
    question or an unknown channel.
    """
    event = store.get_event(session, event_id)
    if event is None:
        raise KeyError(f"no such event: {event_id!r}")

    text = (question or "").strip()
    if not text:
        raise ValueError("the customer's question is required")
    if channel not in VALID_CHANNELS:
        raise ValueError(
            f"channel must be one of {VALID_CHANNELS}, got {channel!r}"
        )

    priority = _priority(TicketReason.CUSTOMER_QUESTION, event)
    ticket = store.insert_ticket(
        session,
        TicketCreate(
            ticket_id=store.next_ticket_id(session),
            event_id=event_id,
            reason=TicketReason.CUSTOMER_QUESTION,
            priority=priority,
            summary=_summarize(TicketReason.CUSTOMER_QUESTION, event, []),
            detail=text,
        ),
    )
    store.log_action(
        session,
        event_id=event_id,
        agent=Agent.HUMAN,
        action="raised_customer_question",
        reasoning=(
            f"Customer asked something the recovery agent cannot answer over "
            f"{channel.replace('_', ' ')}; handed to a human as "
            f"{ticket.ticket_id}. Question: {text}"
        ),
        payload={
            "ticket_id": ticket.ticket_id,
            "channel": channel,
            "question": text,
            "raised_by": employee_email,
            "priority": priority,
        },
    )
    return ticket


# --- metrics ---------------------------------------------------------------

def compute_ticket_metrics(session: Session) -> dict[str, Any]:
    """The human-review queue block folded into the batch ``MetricsBlock``.

    Counts every ticket, open and closed alike — the same honesty rule the
    exception list follows.
    """
    tickets = store.get_tickets(session)
    counts = {s.value: 0 for s in TicketStatus}
    by_reason = {r.value: 0 for r in TicketReason}
    for t in tickets:
        counts[str(t.status)] = counts.get(str(t.status), 0) + 1
        by_reason[str(t.reason)] = by_reason.get(str(t.reason), 0) + 1

    recovered_by_humans = sum((t.recovered_amount for t in tickets), _ZERO)

    open_tickets = [
        t
        for t in tickets
        if str(t.status)
        in (TicketStatus.OPEN.value, TicketStatus.UNDER_REVIEW.value)
    ]
    now = datetime.now(timezone.utc)
    oldest_hours = 0.0
    if open_tickets:
        oldest = min(_aware(t.created_at) for t in open_tickets)
        oldest_hours = round((now - oldest).total_seconds() / 3600, 2)

    closed = counts[TicketStatus.RESOLVED.value] + counts[TicketStatus.UNRESOLVED.value]

    return {
        "total": len(tickets),
        "open": counts[TicketStatus.OPEN.value],
        "under_review": counts[TicketStatus.UNDER_REVIEW.value],
        "resolved": counts[TicketStatus.RESOLVED.value],
        "unresolved": counts[TicketStatus.UNRESOLVED.value],
        "needs_attention": len(open_tickets),
        "by_reason": by_reason,
        "oldest_open_hours": oldest_hours,
        "resolution_rate": (
            round(counts[TicketStatus.RESOLVED.value] / closed, 2) if closed else 0.0
        ),
        "human_recovered": _money(recovered_by_humans),
    }
