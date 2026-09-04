"""Tests for Promise-to-Pay (PTP) Tracker (app/agents/ptp.py)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.agents import ptp
from app.db import store
from app.db.store import Event, EventCreate, EventStatus, EventType, PTPStatus, RootCause


def test_record_and_evaluate_ptp_honored(session):
    event = store.insert_event(
        session,
        EventCreate(
            event_id="evt_ptp_1",
            event_type=EventType.OVERDUE_INVOICE,
            customer_id="cust_corp",
            amount=Decimal("12000.00"),
        ),
    )
    store.update_event(session, "evt_ptp_1", root_cause=RootCause.INVOICE_FORGOTTEN)

    future_date = datetime.now(timezone.utc) + timedelta(days=3)
    updated = ptp.record_promise_to_pay(
        session,
        event_id="evt_ptp_1",
        promised_date=future_date,
        notes="Customer accountant confirmed payment scheduled for Friday.",
    )
    assert updated.ptp_status == PTPStatus.PROMISED
    assert updated.promised_date == future_date

    # Simulate payment received
    store.update_event(
        session,
        "evt_ptp_1",
        status=EventStatus.RECOVERED,
        recovered_amount=Decimal("12000.00"),
    )

    status = ptp.evaluate_ptp_status(session, store.get_event(session, "evt_ptp_1"))
    assert status == PTPStatus.HONORED

    metrics = ptp.compute_ptp_metrics(session)
    assert metrics["total_ptp_recorded"] == 1
    assert metrics["total_honored"] == 1
    assert metrics["honor_rate"] == 1.0


def test_evaluate_ptp_broken(session):
    event = store.insert_event(
        session,
        EventCreate(
            event_id="evt_ptp_2",
            event_type=EventType.OVERDUE_INVOICE,
            customer_id="cust_delay",
            amount=Decimal("5000.00"),
        ),
    )
    store.update_event(session, "evt_ptp_2", root_cause=RootCause.INVOICE_FORGOTTEN)

    past_date = datetime.now(timezone.utc) - timedelta(days=2)
    ptp.record_promise_to_pay(session, "evt_ptp_2", promised_date=past_date)

    status = ptp.evaluate_ptp_status(session, store.get_event(session, "evt_ptp_2"))
    assert status == PTPStatus.BROKEN
