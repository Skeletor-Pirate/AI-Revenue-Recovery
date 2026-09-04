"""Tests for the Audit / Reporting Agent (app/agents/audit.py).

Run from backend/ with the dedicated test DB:
  TEST_DATABASE_URL=postgresql+psycopg://revrec:revrec@localhost:5432/revrec_test_aud \
    uv run pytest -q tests/test_audit.py

The `session` fixture (tests/conftest.py) resets the DB per test and is skipped
automatically when Postgres is unreachable. The `batch` fixture below builds a
finished batch by hand -- a mix of recovered / exception / flagged events plus a
seeded fraud cluster -- mirroring what the detection/diagnosis/recovery agents
would have written.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.agents import audit
from app.db import store
from app.db.store import Agent, EventStatus, EventType, RootCause

BASE = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)


def _mk(session, i, **kw):
    kw.setdefault("created_at", BASE + timedelta(minutes=i))
    return store.insert_event(session, **kw)


@pytest.fixture()
def batch(session):
    # --- raw events (insertion order == created_at order) ---
    _mk(session, 0, event_id="evt_if", event_type=EventType.FAILED_PAYMENT,
        customer_id="c1", amount=Decimal("2000.00"),
        raw_failure_reason="insufficient_fund")
    _mk(session, 1, event_id="evt_reauth", event_type=EventType.EXPIRED_MANDATE,
        customer_id="c2", amount=Decimal("3000.00"),
        raw_failure_reason="mandate_creation_failed")
    _mk(session, 2, event_id="evt_cd", event_type=EventType.FAILED_PAYMENT,
        customer_id="c3", amount=Decimal("1000.00"),
        raw_failure_reason="card_declined")
    _mk(session, 3, event_id="evt_unknown", event_type=EventType.FAILED_PAYMENT,
        customer_id="c4", amount=Decimal("500.00"),
        raw_failure_reason="something_weird")
    _mk(session, 4, event_id="evt_ab", event_type=EventType.ABANDONED_CHECKOUT,
        customer_id="c5", amount=Decimal("1500.00"))
    for j, amt in enumerate(
        [Decimal("5000.00"), Decimal("5010.00"), Decimal("4990.00")]
    ):
        _mk(session, 5 + j, event_id=f"fraud_{j}",
            event_type=EventType.FAILED_PAYMENT, customer_id=f"f{j}",
            amount=amt, raw_failure_reason="card_declined", attempts_so_far=3)

    # --- diagnosis + recovery terminal state ---
    store.update_event(session, "evt_if", status=EventStatus.RECOVERED,
                       root_cause=RootCause.INSUFFICIENT_FUNDS,
                       recovered_amount=Decimal("2000.00"))
    store.update_event(session, "evt_reauth", status=EventStatus.RECOVERED,
                       root_cause=RootCause.EXPIRED_INSTRUMENT,
                       recovered_amount=Decimal("3000.00"))
    store.update_event(session, "evt_cd", status=EventStatus.EXCEPTION,
                       root_cause=RootCause.CARD_DECLINED)
    store.update_event(session, "evt_unknown", status=EventStatus.EXCEPTION,
                       root_cause=RootCause.UNKNOWN)
    store.update_event(session, "evt_ab", status=EventStatus.EXCEPTION,
                       root_cause=RootCause.CHECKOUT_ABANDONED)
    for j in range(3):
        store.update_event(session, f"fraud_{j}", status=EventStatus.FLAGGED,
                           root_cause=RootCause.SUSPECTED_FRAUD)

    # --- audit trail (order matters for by_intervention / fraud reason) ---
    store.log_action(session, event_id="evt_if", agent=Agent.RECOVERY,
                     action="intervention_selected", reasoning="if -> retry",
                     payload={"root_cause": "insufficient_funds",
                              "intervention": "scheduled_retry"})
    store.log_action(session, event_id="evt_if", agent=Agent.RECOVERY,
                     action="marked_recovered", reasoning="collected",
                     payload={"recovered_amount": "2000.00",
                              "simulated_hours_to_recovery": 72.0})
    store.log_action(session, event_id="evt_reauth", agent=Agent.RECOVERY,
                     action="intervention_selected", reasoning="reauth link",
                     payload={"root_cause": "expired_instrument",
                              "intervention": "sent_reauth_link"})
    store.log_action(session, event_id="evt_reauth", agent=Agent.RECOVERY,
                     action="marked_recovered", reasoning="collected",
                     payload={"recovered_amount": "3000.00",
                              "simulated_hours_to_recovery": 36.0})
    store.log_action(session, event_id="evt_cd", agent=Agent.RECOVERY,
                     action="intervention_selected", reasoning="one cautious retry",
                     payload={"root_cause": "card_declined",
                              "intervention": "scheduled_retry"})
    store.log_action(session, event_id="evt_cd", agent=Agent.RECOVERY,
                     action="halted_stopping_rule", reasoning="retry limit hit",
                     payload={"rule": "max_retry_attempts", "attempts_so_far": 3})
    store.log_action(session, event_id="evt_unknown", agent=Agent.RECOVERY,
                     action="routed_to_exception", reasoning="cannot classify",
                     payload={"reason": "unclassified root cause"})
    store.log_action(session, event_id="evt_ab", agent=Agent.RECOVERY,
                     action="intervention_selected", reasoning="nudge",
                     payload={"root_cause": "checkout_abandoned",
                              "intervention": "sent_nudge"})
    for j in range(3):
        store.log_action(
            session, event_id=f"fraud_{j}", agent=Agent.TRIAGE,
            action="halted_fraud_cluster",
            reasoning="Three card_declined failures in a tight window; halted for review.",
            payload={
                "signature": {
                    "raw_failure_reason": "card_declined",
                    "amount_band": ["4990.00", "5010.00"],
                    "customer_count": 3,
                    "window_minutes": 40,
                },
                "cluster_event_ids": [f"fraud_{k}" for k in range(3)],
            },
        )
    return session


# --- compute_metrics ----------------------------------------------------------

def test_totals_and_overall_rate(session, batch):
    m = audit.compute_metrics(session)
    assert m["event_count"] == 8
    assert m["total_at_risk"] == "23000.00"
    assert m["total_recovered"] == "5000.00"
    assert m["overall_recovery_rate"] == 0.22  # 5000 / 23000


def test_status_breakdown_all_six_keys(session, batch):
    m = audit.compute_metrics(session)
    assert m["status_breakdown"] == {
        "detected": 0, "diagnosed": 0, "action_taken": 0,
        "recovered": 2, "exception": 3, "flagged": 3,
    }


def test_by_root_cause_enum_order_and_breakdown(session, batch):
    m = audit.compute_metrics(session)
    assert [d["root_cause"] for d in m["by_root_cause"]] == [
        "insufficient_funds", "expired_instrument", "card_declined",
        "checkout_abandoned", "suspected_fraud", "unknown",
    ]
    rc = {d["root_cause"]: d for d in m["by_root_cause"]}
    assert rc["insufficient_funds"] == {
        "root_cause": "insufficient_funds", "at_risk": "2000.00",
        "recovered": "2000.00", "count": 1, "recovered_count": 1,
        "recovery_rate": 1.0,
    }
    assert rc["suspected_fraud"] == {
        "root_cause": "suspected_fraud", "at_risk": "15000.00",
        "recovered": "0.00", "count": 3, "recovered_count": 0,
        "recovery_rate": 0.0,
    }
    assert rc["card_declined"]["recovery_rate"] == 0.0


def test_by_intervention_has_at_risk_and_recovered(session, batch):
    m = audit.compute_metrics(session)
    iv = {d["intervention"]: d for d in m["by_intervention"]}
    assert iv["scheduled_retry"] == {
        "intervention": "scheduled_retry", "count": 2, "recovered_count": 1,
        "recovery_rate": 0.5, "at_risk": "3000.00", "recovered": "2000.00",
    }
    assert iv["sent_reauth_link"]["recovery_rate"] == 1.0
    assert iv["sent_nudge"] == {
        "intervention": "sent_nudge", "count": 1, "recovered_count": 0,
        "recovery_rate": 0.0, "at_risk": "1500.00", "recovered": "0.00",
    }


def test_avg_hours_to_recovery(session, batch):
    m = audit.compute_metrics(session)
    assert m["avg_hours_to_recovery"] == 54.0  # mean(72, 36)


def test_exception_list_is_complete_with_reasons(session, batch):
    m = audit.compute_metrics(session)
    exc = m["exceptions"]

    non_recovered = {
        e.event_id for e in store.all_events(session)
        if e.status == EventStatus.EXCEPTION.value
    }
    assert {e["event_id"] for e in exc} == non_recovered
    assert [e["event_id"] for e in exc] == ["evt_cd", "evt_unknown", "evt_ab"]
    assert all(e["reason"] for e in exc)

    by_id = {e["event_id"]: e for e in exc}
    assert by_id["evt_cd"]["amount"] == "1000.00"
    assert by_id["evt_cd"]["root_cause"] == "card_declined"
    assert by_id["evt_cd"]["reason"].startswith(
        "halted by stopping rule 'max_retry_attempts' after 3"
    )
    assert by_id["evt_unknown"]["reason"] == "unclassified root cause"
    assert by_id["evt_ab"]["reason"] == "not recovered; no reason recorded"


def test_fraud_cluster(session, batch):
    m = audit.compute_metrics(session)
    fc = m["fraud_cluster"]
    assert fc["flagged_event_ids"] == ["fraud_0", "fraud_1", "fraud_2"]
    assert "halted for review" in fc["reason"]


def test_compute_metrics_is_deterministic(session, batch):
    assert audit.compute_metrics(session) == audit.compute_metrics(session)


# --- run --------------------------------------------------------------------

def test_run_writes_exactly_one_batch_metrics_row(session, batch):
    ids = audit.run(session)
    assert ids == ["evt_if"]

    rows = [
        r for r in store.get_audit_trail(session, "evt_if")
        if r.action == "batch_metrics"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row.agent == "audit"
    assert row.reasoning
    assert row.payload["event_count"] == 8
    assert row.payload["total_recovered"] == "5000.00"
    assert row.payload == audit.compute_metrics(session)


def test_run_does_not_mutate_event_rows(session, batch):
    before = {e.event_id: e.status for e in store.all_events(session)}
    audit.run(session)
    after = {e.event_id: e.status for e in store.all_events(session)}
    assert before == after


# --- empty batch ----------------------------------------------------------

def test_empty_batch_zeroed_block_no_write(session):
    m = audit.compute_metrics(session)
    assert m["event_count"] == 0
    assert m["total_at_risk"] == "0.00"
    assert m["total_recovered"] == "0.00"
    assert m["overall_recovery_rate"] == 0.0
    assert m["by_root_cause"] == []
    assert m["by_intervention"] == []
    assert m["exceptions"] == []
    assert m["avg_hours_to_recovery"] == 0.0
    assert m["fraud_cluster"]["flagged_event_ids"] == []
    assert m["status_breakdown"] == {
        "detected": 0, "diagnosed": 0, "action_taken": 0,
        "recovered": 0, "exception": 0, "flagged": 0,
    }

    assert audit.run(session) == []
    assert store.get_audit_trail(session) == []
