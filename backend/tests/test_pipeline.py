"""End-to-end pipeline integration test — build-order step 7.

reset -> generate -> run all four agents -> assert the whole batch is terminal,
the fraud cluster is flagged, metrics are computed over the full batch, and the
exception list is populated with a stated reason for every entry.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents import audit
from app.data import generate
from app.db import store
from app.db.store import EventStatus

pytestmark = pytest.mark.usefixtures("_require_postgres")

TERMINAL = {
    EventStatus.RECOVERED.value,
    EventStatus.EXCEPTION.value,
    EventStatus.FLAGGED.value,
}


@pytest.fixture()
def seeded(test_database_url):
    generate.generate(count=70, seed=42, reset=True, database_url=test_database_url)
    return test_database_url


def _run(database_url):
    from app import pipeline

    return pipeline.run(database_url)


def test_every_event_reaches_a_terminal_status(seeded):
    metrics = _run(seeded)
    with store.get_session(seeded) as session:
        events = store.all_events(session)
    assert events
    assert all(str(e.status) in TERMINAL for e in events)
    # status_breakdown has nothing left mid-pipeline
    for non_terminal in ("detected", "diagnosed", "action_taken"):
        assert metrics["status_breakdown"][non_terminal] == 0


def test_fraud_cluster_is_flagged_and_not_recovered(seeded):
    metrics = _run(seeded)
    flagged = set(metrics["fraud_cluster"]["flagged_event_ids"])
    assert {"fraud_00", "fraud_01", "fraud_02", "fraud_03"} <= flagged
    with store.get_session(seeded) as session:
        for eid in ("fraud_00", "fraud_01", "fraud_02", "fraud_03"):
            ev = store.get_event(session, eid)
            assert str(ev.status) == EventStatus.FLAGGED.value
            assert str(ev.root_cause) == "suspected_fraud"
            assert ev.recovered_amount == Decimal("0.00")
            trail = store.get_audit_trail(session, eid)
            assert any(r.action == "halted_fraud_cluster" for r in trail)
            # Recovery never acted on it
            assert not any(r.agent == "recovery" for r in trail)


def test_metrics_cover_the_full_batch(seeded):
    metrics = _run(seeded)
    with store.get_session(seeded) as session:
        events = store.all_events(session)
        total = sum((e.amount for e in events), Decimal("0"))
    assert metrics["event_count"] == len(events)
    assert Decimal(metrics["total_at_risk"]) == total.quantize(Decimal("0.01"))
    assert sum(metrics["status_breakdown"].values()) == len(events)
    # recovered total equals the sum of recovered_amount over the batch
    with store.get_session(seeded) as session:
        rec = sum(
            (e.recovered_amount for e in store.all_events(session)), Decimal("0")
        )
    assert Decimal(metrics["total_recovered"]) == rec.quantize(Decimal("0.01"))
    assert 0.0 <= metrics["overall_recovery_rate"] <= 1.0


def test_exception_list_is_populated_and_every_entry_has_a_reason(seeded):
    metrics = _run(seeded)
    exceptions = metrics["exceptions"]
    assert exceptions, "a realistic batch always has some honest exceptions"
    for row in exceptions:
        assert row["reason"] and row["reason"].strip()
        assert row["event_id"] and row["amount"]
    # the exception list matches the events actually in `exception` status
    with store.get_session(seeded) as session:
        exc_ids = {
            e.event_id
            for e in store.all_events(session)
            if str(e.status) == EventStatus.EXCEPTION.value
        }
    assert {r["event_id"] for r in exceptions} == exc_ids


def test_pipeline_is_rerunnable_and_stable(seeded):
    first = _run(seeded)
    second = _run(seeded)
    # agents are idempotent (nothing left to process) so metrics are unchanged
    assert first["total_recovered"] == second["total_recovered"]
    assert first["status_breakdown"] == second["status_breakdown"]


def test_batch_metrics_audit_row_written(seeded):
    _run(seeded)
    with store.get_session(seeded) as session:
        trail = store.get_audit_trail(session)
    batch_rows = [r for r in trail if r.action == "batch_metrics"]
    assert batch_rows
    assert batch_rows[0].agent == "audit"
    assert "total_at_risk" in batch_rows[0].payload
