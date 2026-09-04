"""Tests for the Human Review Triage agent (app/agents/triage.py).

Run from backend/:  uv run pytest -q tests/test_triage.py

The `session` fixture (tests/conftest.py) resets the DB per test and is skipped
automatically when Postgres is unreachable. The `queue` fixture builds a
finished batch by hand -- one event per ticket reason -- mirroring what the
detection/diagnosis/recovery agents would have written before Triage runs.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.agents import triage
from app.db import store
from app.db.store import (
    Agent,
    EventStatus,
    EventType,
    RootCause,
    TicketReason,
    TicketStatus,
)

BASE = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)


def _mk(session, i, **kw):
    kw.setdefault("created_at", BASE + timedelta(minutes=i))
    return store.insert_event(session, **kw)


@pytest.fixture()
def queue(session):
    """One terminal event per ticket reason, with the audit trail that produced it."""
    # suspected fraud -- halted by the diagnosis triage check
    _mk(session, 0, event_id="evt_fraud", event_type=EventType.FAILED_PAYMENT,
        customer_id="c1", amount=Decimal("5000.00"),
        raw_failure_reason="card_declined", attempts_so_far=3)
    store.update_event(session, "evt_fraud", status=EventStatus.FLAGGED,
                       root_cause=RootCause.SUSPECTED_FRAUD)
    store.log_action(session, event_id="evt_fraud", agent=Agent.TRIAGE,
                     action="halted_fraud_cluster",
                     reasoning="matching-signature cluster halted",
                     payload={"cluster_event_ids": ["evt_fraud"]})

    # above the human-approval gate -- proposed, not executed
    _mk(session, 1, event_id="evt_gate", event_type=EventType.ABANDONED_CHECKOUT,
        customer_id="c2", amount=Decimal("8200.00"))
    store.update_event(session, "evt_gate", status=EventStatus.EXCEPTION,
                       root_cause=RootCause.CHECKOUT_ABANDONED)
    store.log_action(session, event_id="evt_gate", agent=Agent.RECOVERY,
                     action="awaiting_human_approval",
                     reasoning="above threshold; not executed",
                     payload={"proposed_action": "bounded discount offer",
                              "awaiting_human_approval": True})

    # failed with no gateway error code at all
    _mk(session, 2, event_id="evt_silent", event_type=EventType.FAILED_PAYMENT,
        customer_id="c3", amount=Decimal("900.00"), raw_failure_reason=None)
    store.update_event(session, "evt_silent", status=EventStatus.EXCEPTION)
    store.log_action(session, event_id="evt_silent", agent=Agent.DETECTION,
                     action="routed_to_exception", reasoning="no failure signal",
                     payload={"reason": "no failure signal"})

    # invoice ladder reached its final rung
    _mk(session, 3, event_id="evt_invoice", event_type=EventType.OVERDUE_INVOICE,
        customer_id="c4", amount=Decimal("12000.00"), days_overdue=70,
        attempts_so_far=3)
    store.update_event(session, "evt_invoice", status=EventStatus.EXCEPTION,
                       root_cause=RootCause.INVOICE_FORGOTTEN)
    store.log_action(session, event_id="evt_invoice", agent=Agent.RECOVERY,
                     action="routed_to_exception",
                     reasoning="ladder complete",
                     payload={"reason": "escalated to human handoff, "
                                        "automated recovery stops"})

    # bounded attempts exhausted, customer never responded
    _mk(session, 4, event_id="evt_stalled", event_type=EventType.FAILED_PAYMENT,
        customer_id="c5", amount=Decimal("1500.00"),
        raw_failure_reason="insufficient_fund", attempts_so_far=3)
    store.update_event(session, "evt_stalled", status=EventStatus.EXCEPTION,
                       root_cause=RootCause.INSUFFICIENT_FUNDS)
    store.log_action(session, event_id="evt_stalled", agent=Agent.RECOVERY,
                     action="halted_stopping_rule", reasoning="3 attempts made",
                     payload={"rule": "max_retry_attempts", "attempts_so_far": 3})

    # a happy case Triage must ignore
    _mk(session, 5, event_id="evt_ok", event_type=EventType.FAILED_PAYMENT,
        customer_id="c6", amount=Decimal("700.00"),
        raw_failure_reason="insufficient_fund")
    store.update_event(session, "evt_ok", status=EventStatus.RECOVERED,
                       root_cause=RootCause.INSUFFICIENT_FUNDS,
                       recovered_amount=Decimal("700.00"))
    return session


# --- opening tickets -------------------------------------------------------

def test_run_opens_one_ticket_per_unfinished_case(queue):
    opened = triage.run(queue)
    assert len(opened) == 5  # every terminal-but-not-recovered event

    by_event = {t.event_id: t for t in store.get_tickets(queue)}
    assert "evt_ok" not in by_event, "a recovered case needs no human"
    assert by_event["evt_fraud"].reason == TicketReason.SUSPECTED_FRAUD
    assert by_event["evt_gate"].reason == TicketReason.AWAITING_APPROVAL
    assert by_event["evt_silent"].reason == TicketReason.EXCEPTION_NO_ERROR
    assert by_event["evt_invoice"].reason == TicketReason.INVOICE_HANDOFF
    assert by_event["evt_stalled"].reason == TicketReason.STALLED_NO_RESPONSE
    assert all(str(t.status) == TicketStatus.OPEN.value for t in by_event.values())


def test_queue_is_ordered_most_urgent_first(queue):
    triage.run(queue)
    order = [t.event_id for t in store.get_tickets(queue)]

    assert order[0] == "evt_fraud", "suspected fraud outranks everything"
    assert order[-1] == "evt_stalled", (
        "'tried 3x, no response' is real but lowest priority -- the automation "
        "behaved correctly, so it must never crowd out fraud or approval work"
    )
    priorities = [t.priority for t in store.get_tickets(queue)]
    assert priorities == sorted(priorities, reverse=True)


def test_priority_is_reason_first_then_amount(session):
    _mk(session, 0, event_id="evt_small", event_type=EventType.FAILED_PAYMENT,
        customer_id="c1", amount=Decimal("400.00"), raw_failure_reason="x")
    _mk(session, 1, event_id="evt_big", event_type=EventType.FAILED_PAYMENT,
        customer_id="c2", amount=Decimal("45000.00"), raw_failure_reason="x")
    small = store.get_event(session, "evt_small")
    big = store.get_event(session, "evt_big")

    # same reason -> the bigger amount wins, but only within the cap
    assert triage._priority(TicketReason.STALLED_NO_RESPONSE, big) > triage._priority(
        TicketReason.STALLED_NO_RESPONSE, small
    )
    # a different reason outranks any amount weighting
    assert triage._priority(TicketReason.SUSPECTED_FRAUD, small) > triage._priority(
        TicketReason.STALLED_NO_RESPONSE, big
    )
    assert triage.priority_band(triage._priority(TicketReason.SUSPECTED_FRAUD, small)) == "critical"


def test_run_is_idempotent_and_never_reopens_closed_work(queue):
    first = triage.run(queue)
    assert triage.run(queue) == [], "a second pass must not duplicate tickets"

    ticket_id = first[0]
    triage.assign_ticket(queue, ticket_id, employee_email="asha@acme.com")
    triage.resolve_ticket(queue, ticket_id, employee_email="asha@acme.com",
                          outcome="unresolved", note="Waiting on the bank.")

    assert triage.run(queue) == [], "a closed ticket must never be reopened"
    assert len(store.get_tickets(queue)) == len(first)


def test_opening_a_ticket_writes_an_audit_row(queue):
    opened = triage.run(queue)
    trail = store.get_audit_trail(queue, "evt_fraud")
    row = next(r for r in trail if r.action == "opened_review_ticket")

    assert row.agent == Agent.TRIAGE
    assert row.reasoning  # never empty
    assert row.payload["ticket_id"] in opened
    assert row.payload["reason"] == TicketReason.SUSPECTED_FRAUD.value
    assert row.payload["priority_band"] == "critical"


# --- assignment ------------------------------------------------------------

def test_assign_moves_ticket_to_under_review(queue):
    ticket_id = triage.run(queue)[0]
    ticket = triage.assign_ticket(queue, ticket_id, employee_email="asha@acme.com")

    assert str(ticket.status) == TicketStatus.UNDER_REVIEW.value
    assert ticket.assigned_employee_email == "asha@acme.com"
    assert ticket.assigned_at is not None

    row = next(
        r for r in store.get_audit_trail(queue, ticket.event_id)
        if r.action == "assigned_review_ticket"
    )
    assert row.agent == Agent.HUMAN
    assert row.payload["employee_email"] == "asha@acme.com"


def test_a_ticket_has_exactly_one_owner(queue):
    ticket_id = triage.run(queue)[0]
    triage.assign_ticket(queue, ticket_id, employee_email="asha@acme.com")

    with pytest.raises(ValueError, match="only an open ticket"):
        triage.assign_ticket(queue, ticket_id, employee_email="bhavin@acme.com")


def test_assign_rejects_unknown_ticket_and_blank_email(queue):
    ticket_id = triage.run(queue)[0]
    with pytest.raises(KeyError):
        triage.assign_ticket(queue, "tkt_9999", employee_email="asha@acme.com")
    with pytest.raises(ValueError, match="employee_email"):
        triage.assign_ticket(queue, ticket_id, employee_email="   ")


# --- resolution ------------------------------------------------------------

def _take(session, event_id="evt_gate"):
    triage.run(session)
    ticket = next(t for t in store.get_tickets(session) if t.event_id == event_id)
    return triage.assign_ticket(
        session, ticket.ticket_id, employee_email="asha@acme.com"
    ).ticket_id


def test_resolve_records_what_the_human_did(queue):
    ticket_id = _take(queue)
    ticket = triage.resolve_ticket(
        queue, ticket_id, employee_email="asha@acme.com",
        outcome="resolved", note="Approved the 10% offer; customer completed checkout.",
    )

    assert str(ticket.status) == TicketStatus.RESOLVED.value
    assert ticket.resolution_outcome == "resolved"
    assert "Approved the 10% offer" in ticket.resolution_note

    row = next(
        r for r in store.get_audit_trail(queue, "evt_gate")
        if r.action == "resolved_review_ticket"
    )
    assert row.agent == Agent.HUMAN
    assert row.reasoning == ticket.resolution_note  # the note IS the reasoning
    assert row.payload["outcome"] == "resolved"


def test_unresolved_is_a_first_class_outcome(queue):
    ticket_id = _take(queue)
    ticket = triage.resolve_ticket(
        queue, ticket_id, employee_email="asha@acme.com",
        outcome="unresolved", note="Customer unreachable after three calls.",
    )
    assert str(ticket.status) == TicketStatus.UNRESOLVED.value
    assert ticket.resolution_outcome == "unresolved"


def test_human_recovery_is_attributed_to_the_human(queue):
    ticket_id = _take(queue)
    triage.resolve_ticket(
        queue, ticket_id, employee_email="asha@acme.com", outcome="resolved",
        note="Called the customer, sent a fresh link, payment cleared on call.",
        recovered_amount="8200.00",
    )

    event = store.get_event(queue, "evt_gate")
    assert event.recovered_amount == Decimal("8200.00")
    assert event.human_recovered_amount == Decimal("8200.00")
    assert str(event.status) == EventStatus.RECOVERED.value, (
        "nothing outstanding -> the case really is recovered"
    )

    row = next(
        r for r in store.get_audit_trail(queue, "evt_gate")
        if r.action == "human_recovered"
    )
    assert row.agent == Agent.HUMAN
    assert row.payload["amount"] == "8200.00"


def test_partial_human_recovery_leaves_the_case_open_ended(queue):
    ticket_id = _take(queue)
    triage.resolve_ticket(
        queue, ticket_id, employee_email="asha@acme.com", outcome="resolved",
        note="Customer paid half now, rest next month.", recovered_amount="4000.00",
    )
    event = store.get_event(queue, "evt_gate")
    assert event.recovered_amount == Decimal("4000.00")
    assert event.human_recovered_amount == Decimal("4000.00")
    assert str(event.status) == EventStatus.EXCEPTION.value


def test_cannot_recover_more_than_was_at_risk(queue):
    ticket_id = _take(queue)
    with pytest.raises(ValueError, match="still at risk"):
        triage.resolve_ticket(
            queue, ticket_id, employee_email="asha@acme.com", outcome="resolved",
            note="typo in the amount", recovered_amount="99999.00",
        )


def test_resolve_guards(queue):
    triage.run(queue)
    open_ticket = store.get_tickets(queue)[0].ticket_id

    with pytest.raises(ValueError, match="take it for review"):
        triage.resolve_ticket(queue, open_ticket, employee_email="a@b.com",
                              outcome="resolved", note="skipped assignment")

    ticket_id = _take(queue)
    with pytest.raises(ValueError, match="outcome must be"):
        triage.resolve_ticket(queue, ticket_id, employee_email="a@b.com",
                              outcome="maybe", note="n")
    with pytest.raises(ValueError, match="resolution note is required"):
        triage.resolve_ticket(queue, ticket_id, employee_email="a@b.com",
                              outcome="resolved", note="   ")
    with pytest.raises(ValueError, match="only be recorded on a resolved"):
        triage.resolve_ticket(queue, ticket_id, employee_email="a@b.com",
                              outcome="unresolved", note="gave up",
                              recovered_amount="10.00")
    with pytest.raises(KeyError):
        triage.resolve_ticket(queue, "tkt_9999", employee_email="a@b.com",
                              outcome="resolved", note="n")


# --- customer questions ----------------------------------------------------

def test_raise_customer_question_hands_the_conversation_over(queue):
    ticket = triage.raise_customer_question(
        queue, "evt_stalled",
        question="Mera pichla refund kab aayega? Woh bhi pending hai.",
        channel="voice_call", employee_email="asha@acme.com",
    )

    assert ticket.reason == TicketReason.CUSTOMER_QUESTION
    assert "refund kab aayega" in ticket.detail
    assert str(ticket.status) == TicketStatus.OPEN.value

    row = next(
        r for r in store.get_audit_trail(queue, "evt_stalled")
        if r.action == "raised_customer_question"
    )
    assert row.agent == Agent.HUMAN
    assert row.payload["channel"] == "voice_call"
    assert row.payload["question"] == ticket.detail


def test_a_waiting_customer_outranks_a_stalled_retry(queue):
    triage.run(queue)
    question = triage.raise_customer_question(
        queue, "evt_stalled", question="Kya mera card block ho gaya hai?"
    )
    stalled = next(
        t for t in store.get_tickets(queue)
        if t.reason == TicketReason.STALLED_NO_RESPONSE
    )
    assert question.priority > stalled.priority


def test_raise_customer_question_guards(queue):
    with pytest.raises(KeyError):
        triage.raise_customer_question(queue, "evt_nope", question="hi")
    with pytest.raises(ValueError, match="question is required"):
        triage.raise_customer_question(queue, "evt_stalled", question="  ")
    with pytest.raises(ValueError, match="channel must be"):
        triage.raise_customer_question(queue, "evt_stalled", question="hi",
                                       channel="carrier_pigeon")


# --- metrics ---------------------------------------------------------------

def test_ticket_metrics_count_every_ticket(queue):
    triage.run(queue)
    ticket_id = _take(queue)
    triage.resolve_ticket(
        queue, ticket_id, employee_email="asha@acme.com", outcome="resolved",
        note="Approved and collected.", recovered_amount="8200.00",
    )

    m = triage.compute_ticket_metrics(queue)
    assert m["total"] == 5
    assert m["resolved"] == 1
    assert m["open"] == 4
    assert m["under_review"] == 0
    assert m["needs_attention"] == 4
    assert m["human_recovered"] == "8200.00"
    assert m["resolution_rate"] == 1.0
    assert m["by_reason"][TicketReason.SUSPECTED_FRAUD.value] == 1
    assert m["oldest_open_hours"] >= 0


def test_ticket_metrics_on_an_empty_queue(session):
    m = triage.compute_ticket_metrics(session)
    assert m["total"] == 0
    assert m["needs_attention"] == 0
    assert m["human_recovered"] == "0.00"
    assert m["resolution_rate"] == 0.0
    assert set(m["by_reason"]) == {r.value for r in TicketReason}
