"""Sanity tests for the shared SQLModel/Postgres event store.

Prereq:  docker compose up -d
Run:     uv run pytest -q

The `session` fixture (tests/conftest.py) gives each test a freshly reset,
empty database and a Session bound to it.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.db import store
from app.db.store import AuditCreate, AuditLog, Event, EventCreate, EventUpdate


def test_init_creates_both_tables(session):
    tables = set(store.SQLModel.metadata.tables)
    assert {"events", "audit_log"} <= tables


def test_insert_and_read_back(session):
    store.insert_event(
        session,
        event_id="evt_1",
        event_type=store.EventType.FAILED_PAYMENT,
        customer_id="cust_1",
        amount=2500,
        raw_failure_reason="insufficient funds",
    )
    row = store.get_event(session, "evt_1")
    assert isinstance(row, Event)
    assert row.status == "detected"                 # default applied
    assert row.currency == "INR"                    # default applied
    assert row.attempts_so_far == 0                 # default applied
    assert row.recovered_amount == Decimal("0")     # default applied
    assert row.amount == Decimal("2500.00")         # NUMERIC(14,2)


def test_get_event_missing_returns_none(session):
    assert store.get_event(session, "nope") is None


def test_insert_rejects_unknown_event_type(session):
    with pytest.raises(ValueError):
        store.insert_event(
            session, event_id="x", event_type="failed_payments",  # typo
            customer_id="c", amount=1,
        )


def test_update_event_diagnosis_step(session):
    store.insert_event(
        session, event_id="evt_2", event_type=store.EventType.OVERDUE_INVOICE,
        customer_id="cust_2", amount=40000, days_overdue=45,
    )
    updated = store.update_event(
        session, "evt_2",
        status=store.EventStatus.DIAGNOSED,
        root_cause="forgotten_invoice",
        diagnosis_confidence=0.9,
    )
    assert updated.status == "diagnosed"
    assert updated.root_cause == "forgotten_invoice"
    assert updated.diagnosis_confidence == 0.9
    assert updated.updated_at >= updated.created_at


def test_update_event_rejects_unknown_column(session):
    store.insert_event(
        session, event_id="evt_3", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=1,
    )
    with pytest.raises(ValueError):
        store.update_event(session, "evt_3", roott_cause="x")  # typo


def test_update_event_rejects_bad_status(session):
    store.insert_event(
        session, event_id="evt_3b", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=1,
    )
    with pytest.raises(ValueError):
        store.update_event(session, "evt_3b", status="fixed")


def test_update_missing_event_raises(session):
    with pytest.raises(KeyError):
        store.update_event(session, "ghost", status=store.EventStatus.RECOVERED)


def test_get_events_by_status_single_and_multiple(session):
    for i, st in enumerate(["detected", "diagnosed", "detected", "recovered"]):
        store.insert_event(
            session, event_id=f"e{i}", event_type=store.EventType.FAILED_PAYMENT,
            customer_id="c", amount=100, status=st,
        )
    assert len(store.get_events_by_status(session, "detected")) == 2
    assert len(store.get_events_by_status(session, ["recovered", "diagnosed"])) == 2


def test_log_action_writes_trail_and_returns_id(session):
    store.insert_event(
        session, event_id="evt_4", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=100,
    )
    row_id = store.log_action(
        session, event_id="evt_4", agent=store.Agent.DIAGNOSIS,
        action="classified_root_cause",
        reasoning="raw_failure_reason matched the 'insufficient funds' rule",
        payload={"root_cause": "insufficient_funds", "confidence": 0.95},
    )
    assert isinstance(row_id, int)
    trail = store.get_audit_trail(session, "evt_4")
    assert len(trail) == 1
    assert isinstance(trail[0], AuditLog)
    assert trail[0].agent == "diagnosis"
    assert trail[0].reasoning.startswith("raw_failure_reason matched")
    # JSONB round-trips as a dict -- no manual json.loads
    assert trail[0].payload["root_cause"] == "insufficient_funds"


def test_log_action_rejects_unknown_agent(session):
    store.insert_event(
        session, event_id="evt_5", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=100,
    )
    with pytest.raises(ValueError):
        store.log_action(
            session, event_id="evt_5", agent="marketing",
            action="x", reasoning="y",
        )


def test_audit_foreign_key_is_enforced(session):
    # No event 'ghost' exists -> Postgres itself must refuse the audit row.
    with pytest.raises(IntegrityError):
        store.log_action(
            session, event_id="ghost", agent=store.Agent.DETECTION,
            action="flagged_at_risk", reasoning="should never persist",
        )
    session.rollback()


def test_reset_db_clears_everything(session, test_database_url):
    store.insert_event(
        session, event_id="evt_6", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=100,
    )
    assert len(store.all_events(session)) == 1

    # release this session's locks/transaction before DROP TABLE, or the
    # drop_all() inside reset_db blocks on them
    session.close()
    store.reset_db(test_database_url)

    with store.get_session(test_database_url) as fresh:
        assert store.all_events(fresh) == []


# --- Pydantic schema layer (no DB needed) --------------------------------

def test_event_create_rejects_bad_values():
    base = dict(event_id="e", event_type="failed_payment", customer_id="c")
    with pytest.raises(ValidationError):
        EventCreate(**base, amount=0)                    # gt=0
    with pytest.raises(ValidationError):
        EventCreate(**base, amount=100, attempts_so_far=-1)   # ge=0
    with pytest.raises(ValidationError):
        EventCreate(**base, amount=100, currency="RUPEE")     # 3 chars
    with pytest.raises(ValidationError):
        EventCreate(**base, amount=100, status="paid")        # not an EventStatus


def test_event_create_normalises():
    ec = EventCreate(
        event_id="e", event_type="failed_payment", customer_id="c",
        amount="2500.999", currency="inr",
    )
    assert ec.amount == Decimal("2501.00")   # quantised to paise
    assert ec.currency == "INR"              # upper-cased
    assert ec.model_dump()["status"] == "detected"   # enum -> plain str


def test_event_update_forbids_unknown_and_bounds_confidence():
    with pytest.raises(ValidationError):
        EventUpdate(root_caus="typo")                 # extra="forbid"
    with pytest.raises(ValidationError):
        EventUpdate(diagnosis_confidence=1.5)         # le=1
    assert EventUpdate(status="recovered").model_dump(exclude_unset=True) == {
        "status": "recovered"
    }


def test_audit_create_requires_nonempty_reasoning():
    with pytest.raises(ValidationError):
        AuditCreate(event_id="e", agent="diagnosis", action="x", reasoning="")
    with pytest.raises(ValidationError):
        AuditCreate(event_id="e", agent="nobody", action="x", reasoning="y")


def test_insert_accepts_prebuilt_schema(session):
    store.insert_event(
        session, event_id="evt_7", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=100,
    )
    store.update_event(
        session, "evt_7",
        EventUpdate(status=store.EventStatus.RECOVERED, recovered_amount=100),
    )
    row = store.get_event(session, "evt_7")
    assert row.status == "recovered"
    assert row.recovered_amount == Decimal("100.00")
