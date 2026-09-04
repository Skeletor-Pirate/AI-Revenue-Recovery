"""Tests for the Mandate Retry Sequencer (app/agents/sequencer.py)."""

from datetime import datetime, timezone
from decimal import Decimal

from app.agents import sequencer
from app.db.store import Event, EventType, RootCause


def test_insufficient_funds_schedule():
    event = Event(
        event_id="evt_test_seq1",
        event_type=EventType.FAILED_PAYMENT,
        customer_id="cust_1",
        amount=Decimal("1500.00"),
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        raw_failure_reason="insufficient_fund",
        created_at=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
    )
    schedule = sequencer.plan_retry_sequence(event)
    assert len(schedule) == 3
    assert schedule[0]["action"] == "salary_window_debit"
    assert schedule[0]["pre_debit_notification"] is True
    assert "2026-10-01" in schedule[0]["scheduled_time"]
    assert schedule[0]["compliance_tag"] == "NPCI_CIRCULAR_2024_PTP"


def test_bank_downtime_schedule():
    event = Event(
        event_id="evt_test_seq2",
        event_type=EventType.FAILED_PAYMENT,
        customer_id="cust_2",
        amount=Decimal("2500.00"),
        root_cause=RootCause.BANK_DOWNTIME,
        raw_failure_reason="bank_not_available",
        created_at=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
    )
    schedule = sequencer.plan_retry_sequence(event)
    assert len(schedule) == 2
    assert schedule[0]["action"] == "short_backoff_retry"
    assert schedule[1]["action"] == "suggest_alternate_rail"


def test_expired_instrument_schedule():
    event = Event(
        event_id="evt_test_seq3",
        event_type=EventType.EXPIRED_MANDATE,
        customer_id="cust_3",
        amount=Decimal("999.00"),
        root_cause=RootCause.EXPIRED_INSTRUMENT,
        raw_failure_reason="mandate_creation_expired",
        created_at=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc),
    )
    schedule = sequencer.plan_retry_sequence(event)
    assert len(schedule) == 2
    assert schedule[0]["action"] == "send_remandate_link"
    assert schedule[0]["rail"] == "remandate_portal"
