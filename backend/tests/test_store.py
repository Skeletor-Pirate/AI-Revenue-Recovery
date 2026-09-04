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
from app.db.store import (
    AuditCreate,
    AuditLog,
    Event,
    EventCreate,
    EventUpdate,
    TicketCreate,
    TicketReason,
    TicketStatus,
    TicketUpdate,
)


def test_init_creates_every_table(session):
    tables = set(store.SQLModel.metadata.tables)
    assert {"events", "audit_log", "tickets"} <= tables


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
        root_cause=store.RootCause.INVOICE_FORGOTTEN,
        diagnosis_confidence=0.9,
    )
    assert updated.status == "diagnosed"
    assert updated.root_cause == "invoice_forgotten"
    assert updated.diagnosis_confidence == 0.9
    assert updated.updated_at >= updated.created_at


def test_insert_event_honours_backdated_created_at(session):
    from datetime import datetime, timedelta, timezone

    ts = datetime.now(timezone.utc) - timedelta(days=5)
    ev = store.insert_event(
        session, event_id="evt_bd", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="cust_bd", amount=500, created_at=ts,
    )
    assert abs((ev.created_at - ts).total_seconds()) < 1
    assert abs((ev.updated_at - ts).total_seconds()) < 1


def test_update_event_rejects_unknown_root_cause(session):
    store.insert_event(
        session, event_id="evt_rc", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="cust_rc", amount=100,
    )
    with pytest.raises(ValueError):
        store.update_event(session, "evt_rc", root_cause="not_a_cause")


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


# --- human-review tickets ---------------------------------------------------

def _seed_event(session, event_id="evt_t", amount="1000.00"):
    return store.insert_event(
        session, event_id=event_id,
        event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=Decimal(amount),
    )


def test_ticket_insert_defaults_and_read_back(session):
    _seed_event(session)
    ticket = store.insert_ticket(
        session, ticket_id="tkt_0001", event_id="evt_t",
        reason=TicketReason.EXCEPTION_NO_ERROR, priority=70,
        summary="No gateway error to reason from",
    )
    assert ticket.status == TicketStatus.OPEN
    assert ticket.recovered_amount == Decimal("0.00")
    assert ticket.assigned_employee_email is None
    assert ticket.created_at.tzinfo is not None

    assert store.get_ticket(session, "tkt_0001").summary == ticket.summary
    assert store.get_ticket(session, "nope") is None


def test_ticket_ids_are_sequential(session):
    _seed_event(session)
    assert store.next_ticket_id(session) == "tkt_0001"
    store.insert_ticket(session, ticket_id="tkt_0001", event_id="evt_t",
                        reason=TicketReason.OTHER, summary="s")
    assert store.next_ticket_id(session) == "tkt_0002"


def test_get_tickets_orders_by_priority_then_age(session):
    _seed_event(session)
    for i, priority in enumerate((25, 90, 65), start=1):
        store.insert_ticket(session, ticket_id=f"tkt_{i:04d}", event_id="evt_t",
                            reason=TicketReason.OTHER, priority=priority,
                            summary=f"s{i}")
    assert [t.priority for t in store.get_tickets(session)] == [90, 65, 25]
    assert store.get_tickets(session, TicketStatus.RESOLVED) == []


def test_open_ticket_for_event_ignores_closed_work(session):
    _seed_event(session)
    store.insert_ticket(session, ticket_id="tkt_0001", event_id="evt_t",
                        reason=TicketReason.OTHER, summary="s")
    assert store.open_ticket_for_event(session, "evt_t").ticket_id == "tkt_0001"

    store.update_ticket(session, "tkt_0001", status=TicketStatus.RESOLVED)
    assert store.open_ticket_for_event(session, "evt_t") is None
    # ...but the history is still there
    assert len(store.tickets_for_event(session, "evt_t")) == 1


def test_update_ticket_bumps_updated_at_and_rejects_unknown(session):
    _seed_event(session)
    ticket = store.insert_ticket(session, ticket_id="tkt_0001", event_id="evt_t",
                                 reason=TicketReason.OTHER, summary="s")
    before = ticket.updated_at
    updated = store.update_ticket(
        session, "tkt_0001",
        TicketUpdate(status=TicketStatus.UNDER_REVIEW,
                     assigned_employee_email="asha@acme.com"),
    )
    assert updated.updated_at > before
    assert updated.assigned_employee_email == "asha@acme.com"

    with pytest.raises(KeyError):
        store.update_ticket(session, "tkt_9999", status=TicketStatus.RESOLVED)


def test_ticket_schema_validation(session):
    with pytest.raises(ValidationError):
        TicketCreate(ticket_id="t", event_id="e", reason="made_up", summary="s")
    with pytest.raises(ValidationError):
        TicketCreate(ticket_id="t", event_id="e", reason=TicketReason.OTHER,
                     summary="")                       # min_length=1
    with pytest.raises(ValidationError):
        TicketUpdate(assigned_employe_email="typo")     # extra="forbid"
    with pytest.raises(ValidationError):
        TicketUpdate(recovered_amount=-1)               # ge=0

    tc = TicketCreate(ticket_id="t", event_id="e", reason=TicketReason.OTHER,
                      summary="s")
    assert tc.model_dump()["status"] == "open"          # enum -> plain str


def test_ticket_requires_a_real_event(session):
    with pytest.raises(IntegrityError):
        store.insert_ticket(session, ticket_id="tkt_0001", event_id="ghost",
                            reason=TicketReason.OTHER, summary="s")


def test_human_recovered_amount_defaults_and_patches(session):
    _seed_event(session)
    assert store.get_event(session, "evt_t").human_recovered_amount == Decimal("0.00")
    store.update_event(session, "evt_t", human_recovered_amount=Decimal("250.006"))
    assert store.get_event(session, "evt_t").human_recovered_amount == Decimal("250.01")
