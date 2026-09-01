"""Tests for the synthetic data generator.

The build_* tests need no database. The seeding test uses the `session`
fixture and auto-skips when Postgres is down.
"""

from decimal import Decimal

from app.data import generate
from app.data.generate import build_batch, build_fraud_cluster
from app.db import store
from app.db.store import EventCreate, EventType


def test_batch_size_and_type_coverage():
    batch = build_batch(count=80, seed=42)
    assert 50 <= len(batch) <= 100
    seen = {r.event_type for r in batch}
    assert seen == {t.value for t in EventType}  # use_enum_values -> plain strings


def test_every_record_is_valid_eventcreate():
    for record in build_batch(count=80, seed=7):
        assert isinstance(record, EventCreate)


def test_amount_and_days_overdue_bounds():
    for r in build_batch(count=100, seed=1):
        assert Decimal("200") <= r.amount <= Decimal("50000")
        if r.event_type != EventType.OVERDUE_INVOICE.value:
            assert r.days_overdue == 0
        else:
            assert 1 <= r.days_overdue <= 90


def test_overdue_and_abandoned_have_no_gateway_reason():
    for r in build_batch(count=100, seed=2):
        if r.event_type in {
            EventType.OVERDUE_INVOICE.value,
            EventType.ABANDONED_CHECKOUT.value,
        }:
            assert r.raw_failure_reason is None


def test_deterministic_for_a_seed():
    a = build_batch(count=60, seed=99)
    b = build_batch(count=60, seed=99)
    assert [r.event_id for r in a] == [r.event_id for r in b]
    assert [r.amount for r in a] == [r.amount for r in b]


def test_fraud_cluster_shares_a_tight_signature():
    cluster = build_fraud_cluster(size=4, seed=42)
    assert len(cluster) == 4
    assert {r.raw_failure_reason for r in cluster} == {generate.FRAUD_REASON}
    assert len({r.customer_id for r in cluster}) == 4  # distinct "customers"
    amounts = [r.amount for r in cluster]
    assert max(amounts) - min(amounts) <= Decimal("40")  # tight band
    assert all(r.attempts_so_far >= 2 for r in cluster)  # already retried hard
    assert all(r.event_id.startswith(generate.FRAUD_ID_PREFIX) for r in cluster)


def test_generate_seeds_database(session, test_database_url):
    ids = generate.generate(
        count=20, seed=5, reset=False, database_url=test_database_url
    )
    rows = store.all_events(session)
    assert len(rows) == len(ids) == 24  # 20 + 4 fraud
    detected = store.get_events_by_status(session, "detected")
    fraud = [e.event_id for e in detected if e.event_id.startswith(generate.FRAUD_ID_PREFIX)]
    assert len(fraud) == 4
