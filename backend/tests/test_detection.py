"""Tests for the Detection Agent (app/agents/detection.py).

Run from backend/ with Postgres up:  uv run pytest -q tests/test_detection.py
The `session` fixture (tests/conftest.py) resets the test DB per test and is
skipped automatically when Postgres is unreachable.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.agents import detection
from app.db import store
from app.db.store import EventStatus, EventType


def _fake_event(**over):
    base = dict(
        event_id="e",
        event_type=EventType.FAILED_PAYMENT.value,
        customer_id="c",
        amount=Decimal("1000.00"),
        currency="INR",
        raw_failure_reason="insufficient_fund",
        attempts_so_far=0,
        days_overdue=0,
        recovered_amount=Decimal("0"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _insert(session, **over):
    kw = dict(
        event_id="evt_1",
        event_type=EventType.FAILED_PAYMENT,
        customer_id="cust_1",
        amount=Decimal("1000.00"),
        raw_failure_reason="insufficient_fund",
    )
    kw.update(over)
    return store.insert_event(session, **kw)


# --- pure classify() -------------------------------------------------------

def test_classify_confirms_genuine_risk():
    keep, reason = detection.classify(_fake_event())
    assert keep is True
    assert reason


def test_classify_rejects_fully_recovered():
    keep, reason = detection.classify(
        _fake_event(amount=Decimal("500.00"), recovered_amount=Decimal("500.00"))
    )
    assert keep is False
    assert "at risk" in reason


def test_classify_rejects_non_inr():
    keep, reason = detection.classify(_fake_event(currency="USD"))
    assert keep is False
    assert reason == "non-INR currency out of scope for test-mode recovery"


def test_classify_rejects_missing_failure_signal():
    keep, reason = detection.classify(_fake_event(raw_failure_reason="  "))
    assert keep is False
    assert reason == "no failure signal to diagnose"


def test_classify_rejects_unsupported_event_type():
    keep, reason = detection.classify(_fake_event(event_type="chargeback"))
    assert keep is False
    assert "not supported" in reason


def test_classify_rejects_non_overdue_invoice():
    keep, reason = detection.classify(
        _fake_event(
            event_type=EventType.OVERDUE_INVOICE.value,
            raw_failure_reason=None,
            days_overdue=0,
        )
    )
    assert keep is False


def test_classify_accepts_overdue_invoice_with_days():
    keep, _ = detection.classify(
        _fake_event(
            event_type=EventType.OVERDUE_INVOICE.value,
            raw_failure_reason=None,
            days_overdue=5,
        )
    )
    assert keep is True


# --- run() against the store --------------------------------------------

def test_run_flags_at_risk_event(session):
    _insert(session)
    examined = detection.run(session)
    assert examined == ["evt_1"]

    ev = store.get_event(session, "evt_1")
    assert ev.status == EventStatus.DETECTED.value

    trail = store.get_audit_trail(session, "evt_1")
    assert len(trail) == 1
    row = trail[0]
    assert row.agent == "detection"
    assert row.action == "flagged_at_risk"
    assert row.reasoning
    assert row.payload == {"amount_at_risk": "1000.00", "event_type": "failed_payment"}


def test_run_routes_non_recoverable_to_exception(session):
    _insert(session, raw_failure_reason=None)
    detection.run(session)

    ev = store.get_event(session, "evt_1")
    assert ev.status == EventStatus.EXCEPTION.value

    trail = store.get_audit_trail(session, "evt_1")
    assert len(trail) == 1
    assert trail[0].action == "routed_to_exception"
    assert trail[0].payload == {"reason": "no failure signal to diagnose"}


def test_run_amount_at_risk_is_net_of_recovered(session):
    _insert(session, amount=Decimal("1000.00"))
    store.update_event(session, "evt_1", recovered_amount=Decimal("250.00"))
    detection.run(session)
    trail = store.get_audit_trail(session, "evt_1")
    assert trail[0].payload["amount_at_risk"] == "750.00"


def test_run_is_idempotent(session):
    _insert(session)
    _insert(session, event_id="evt_2", raw_failure_reason=None)

    first = detection.run(session)
    second = detection.run(session)

    assert set(first) == {"evt_1", "evt_2"}
    # evt_2 left "detected" -> only evt_1 still visible on the second pass
    assert second == ["evt_1"]

    assert len(store.get_audit_trail(session, "evt_1")) == 1
    assert len(store.get_audit_trail(session, "evt_2")) == 1


def test_run_only_touches_detected_events(session):
    _insert(session)
    store.update_event(session, "evt_1", status=EventStatus.DIAGNOSED)
    examined = detection.run(session)
    assert examined == []
    assert store.get_audit_trail(session, "evt_1") == []
