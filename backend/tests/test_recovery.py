"""Tests for the Recovery Agent (app/agents/recovery.py).

Run from backend/ with Postgres up:  uv run pytest -q tests/test_recovery.py
The `session` fixture (tests/conftest.py) resets the test DB per test and is
skipped automatically when Postgres is unreachable. Claude is never called —
`recovery._claude_draft` is monkeypatched or the no-API-key template path runs.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.agents import recovery
from app.db import store
from app.db.store import EventStatus, EventType, RootCause


# --- helpers ------------------------------------------------------------

def _mk(
    session,
    event_id,
    root_cause,
    *,
    amount="1000.00",
    attempts=0,
    event_type=EventType.FAILED_PAYMENT,
    reason="card_declined",
    days_overdue=0,
    customer=None,
):
    store.insert_event(
        session,
        event_id=event_id,
        event_type=event_type,
        customer_id=customer or f"c_{event_id}",
        amount=Decimal(amount),
        raw_failure_reason=reason,
        attempts_so_far=attempts,
        days_overdue=days_overdue,
    )
    store.update_event(
        session,
        event_id,
        status=EventStatus.DIAGNOSED,
        root_cause=root_cause,
        diagnosis_confidence=0.9,
    )
    return store.get_event(session, event_id)


def _actions(session, event_id):
    return [r.action for r in store.get_audit_trail(session, event_id)]


def _row(session, event_id, action):
    return next(
        r for r in store.get_audit_trail(session, event_id) if r.action == action
    )


def _expect_recovered(event_id, rc):
    """Mirrors `payment.resolve_fake_capture`'s exact deterministic formula
    (AGENTS_CONTRACT.md §11/§13 P5): the salt is
    f"{event_id}:{link_id}:{attempt}", not just `event_id` -- `_resolve_outcome`
    always calls the fake gateway with `link_id=f"fake_{event_id}"` and
    `attempt=1` when no Razorpay keys are configured (the default test
    settings=None path, forced by the `_no_real_razorpay` conftest fixture).
    """
    link_id = f"fake_{event_id}"
    salt = f"{event_id}:{link_id}:1"
    return (recovery._stable_hash(salt) % 100) < recovery.SUCCESS_RATES[rc]


# --- module constants -------------------------------------------------

def test_module_constants():
    assert recovery.MAX_RETRY_ATTEMPTS == 3
    assert recovery.MAX_ESCALATION_STAGE == 3
    assert recovery.COOLDOWN_HOURS == 24
    assert recovery.HUMAN_APPROVAL_THRESHOLD_INR == Decimal("5000")
    assert recovery.SALARY_WINDOW_DAY == 1
    assert recovery.RETRY_BACKOFF_HOURS == 6
    assert recovery.SUCCESS_RATES["card_declined"] == 20
    assert recovery.HOURS_TO_RECOVERY["bank_downtime"] == 6.0


# --- pure outreach helpers ------------------------------------------

def test_outreach_template_is_first_person_and_warm():
    ev = SimpleNamespace(customer_id="cust_9", amount=Decimal("500.00"))
    msg = recovery.draft_outreach("sent_nudge", ev, settings=None)
    assert msg.startswith("Hi cust_9,")
    assert "noticed you left" in msg


def test_outreach_never_raises_on_unknown_intervention():
    ev = SimpleNamespace(customer_id="c", amount=Decimal("1.00"))
    msg = recovery.draft_outreach("bogus_intervention", ev, settings=None)
    assert isinstance(msg, str) and msg


def test_channel_by_amount():
    assert recovery._channel(Decimal("1999.99")) == "sms"
    assert recovery._channel(Decimal("2000.00")) == "email"


def test_claude_used_when_api_key_present(monkeypatch):
    monkeypatch.setattr(recovery, "_claude_draft", lambda *a, **k: "CLAUDE COPY")
    settings = SimpleNamespace(anthropic_api_key="sk-test", anthropic_model="x")
    ev = SimpleNamespace(customer_id="c", amount=Decimal("10.00"))
    assert recovery.draft_outreach("sent_nudge", ev, settings=settings) == "CLAUDE COPY"


def test_claude_failure_falls_back_to_template(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(recovery, "_claude_draft", boom)
    settings = SimpleNamespace(anthropic_api_key="sk-test")
    ev = SimpleNamespace(customer_id="cust_1", amount=Decimal("10.00"))
    msg = recovery.draft_outreach("sent_nudge", ev, settings=settings)
    assert msg.startswith("Hi cust_1,")


# --- R1: refusal on suspected fraud --------------------------------

def test_refuses_suspected_fraud_and_flags(session):
    _mk(session, "fr1", RootCause.SUSPECTED_FRAUD)
    examined = recovery.run(session)

    assert examined == ["fr1"]
    ev = store.get_event(session, "fr1")
    assert ev.status == EventStatus.FLAGGED.value

    trail = store.get_audit_trail(session, "fr1")
    assert [r.action for r in trail] == ["halted_stopping_rule"]
    assert trail[0].payload == {"rule": "suspected_fraud_refusal"}
    assert trail[0].reasoning


def test_never_reads_flagged_events(session):
    _mk(session, "fl1", RootCause.INSUFFICIENT_FUNDS)
    store.update_event(session, "fl1", status=EventStatus.FLAGGED)

    examined = recovery.run(session)

    assert examined == []
    assert store.get_audit_trail(session, "fl1") == []
    assert store.get_event(session, "fl1").status == EventStatus.FLAGGED.value


# --- each intervention route ----------------------------------------

_ROUTES = [
    ("insufficient_funds", RootCause.INSUFFICIENT_FUNDS, EventType.FAILED_PAYMENT, 0, "scheduled_retry"),
    ("expired_instrument", RootCause.EXPIRED_INSTRUMENT, EventType.FAILED_PAYMENT, 0, "sent_reauth_link"),
    ("bank_downtime", RootCause.BANK_DOWNTIME, EventType.FAILED_PAYMENT, 0, "suggested_alternate_method"),
    ("auth_failure", RootCause.AUTH_FAILURE, EventType.FAILED_PAYMENT, 0, "prompted_guided_retry"),
    ("card_declined", RootCause.CARD_DECLINED, EventType.FAILED_PAYMENT, 0, "scheduled_retry"),
    ("checkout_abandoned", RootCause.CHECKOUT_ABANDONED, EventType.ABANDONED_CHECKOUT, 0, "sent_nudge"),
    ("invoice_forgotten", RootCause.INVOICE_FORGOTTEN, EventType.OVERDUE_INVOICE, 10, "escalation_stage_advanced"),
]


@pytest.mark.parametrize("rc,rc_enum,etype,overdue,intervention", _ROUTES)
def test_intervention_route(session, rc, rc_enum, etype, overdue, intervention):
    eid = f"rt_{rc}"
    _mk(session, eid, rc_enum, event_type=etype, days_overdue=overdue, amount="1000.00")

    recovery.run(session)

    actions = _actions(session, eid)
    assert actions[0] == "intervention_selected"
    sel = _row(session, eid, "intervention_selected")
    assert sel.payload == {"root_cause": rc, "intervention": intervention}
    assert intervention in actions

    ev = store.get_event(session, eid)
    if _expect_recovered(eid, rc):
        assert ev.status == EventStatus.RECOVERED.value
        assert ev.recovered_amount == Decimal("1000.00")
        mr = _row(session, eid, "marked_recovered")
        assert mr.payload["recovered_amount"] == "1000.00"
        assert mr.payload["simulated_hours_to_recovery"] == recovery.HOURS_TO_RECOVERY[rc]
    else:
        assert ev.status == EventStatus.EXCEPTION.value
        assert _row(session, eid, "routed_to_exception").payload["reason"]
    for r in store.get_audit_trail(session, eid):
        assert r.reasoning


def test_scheduled_retry_targets_next_salary_window(session):
    _mk(session, "sw1", RootCause.INSUFFICIENT_FUNDS)
    recovery.run(session)
    row = _row(session, "sw1", "scheduled_retry")
    assert row.payload["attempt"] == 1
    retry_at = datetime.fromisoformat(row.payload["retry_at"])
    assert retry_at.day == recovery.SALARY_WINDOW_DAY
    anchor = store.get_event(session, "sw1").created_at
    assert retry_at > anchor


def test_bank_downtime_channel_and_backoff(session):
    _mk(session, "bd1", RootCause.BANK_DOWNTIME, amount="500.00")
    recovery.run(session)
    row = _row(session, "bd1", "suggested_alternate_method")
    assert row.payload["channel"] == "sms"
    assert row.payload["message"].startswith("Hi c_bd1,")
    assert "contact_at" in row.payload


# --- stopping rule: max retry attempts -> exception ----------------

def test_max_attempts_routes_to_exception(session):
    _mk(session, "mx1", RootCause.INSUFFICIENT_FUNDS, attempts=3)
    recovery.run(session)

    ev = store.get_event(session, "mx1")
    assert ev.status == EventStatus.EXCEPTION.value
    assert _actions(session, "mx1") == ["intervention_selected", "halted_stopping_rule"]
    halt = _row(session, "mx1", "halted_stopping_rule")
    assert halt.payload == {"rule": "max_retry_attempts", "attempts_so_far": 3}
    assert "scheduled_retry" not in _actions(session, "mx1")


# --- stopping rule: escalation stage cap ---------------------------

def test_escalation_never_advances_past_stage_three(session):
    _mk(
        session, "ec1", RootCause.INVOICE_FORGOTTEN,
        event_type=EventType.OVERDUE_INVOICE, days_overdue=30, attempts=3,
        amount="1000.00",
    )
    recovery.run(session)

    ev = store.get_event(session, "ec1")
    assert ev.status == EventStatus.EXCEPTION.value
    assert _actions(session, "ec1") == ["intervention_selected", "halted_stopping_rule"]
    halt = _row(session, "ec1", "halted_stopping_rule")
    assert halt.payload == {"rule": "max_escalation_stage", "attempts_so_far": 3}
    assert "escalation_stage_advanced" not in _actions(session, "ec1")


def test_escalation_stage_one_advances_and_bumps_attempts(session):
    _mk(
        session, "es1", RootCause.INVOICE_FORGOTTEN,
        event_type=EventType.OVERDUE_INVOICE, days_overdue=5, attempts=0,
        amount="1000.00",
    )
    recovery.run(session)
    row = _row(session, "es1", "escalation_stage_advanced")
    assert row.payload["stage"] == 1
    assert row.payload["stage_name"] == "reminder"
    assert store.get_event(session, "es1").attempts_so_far == 1


def test_escalation_stage_three_hands_off_to_human(session):
    _mk(
        session, "es3", RootCause.INVOICE_FORGOTTEN,
        event_type=EventType.OVERDUE_INVOICE, days_overdue=60, attempts=2,
        amount="1000.00",
    )
    recovery.run(session)
    actions = _actions(session, "es3")
    assert "escalation_stage_advanced" in actions
    assert _row(session, "es3", "escalation_stage_advanced").payload["stage_name"] == "human_handoff"
    assert store.get_event(session, "es3").status == EventStatus.EXCEPTION.value
    assert "marked_recovered" not in actions


# --- approval gate flag (not executed) ----------------------------

def test_discount_above_threshold_is_gated_not_executed(session):
    _mk(
        session, "ap1", RootCause.CHECKOUT_ABANDONED,
        event_type=EventType.ABANDONED_CHECKOUT, amount="10000.00", reason=None,
    )
    recovery.run(session)

    ev = store.get_event(session, "ap1")
    assert ev.status == EventStatus.EXCEPTION.value
    assert ev.recovered_amount == Decimal("0")

    actions = _actions(session, "ap1")
    assert "sent_nudge" in actions
    assert "awaiting_human_approval" in actions
    assert "marked_recovered" not in actions

    gate = _row(session, "ap1", "awaiting_human_approval")
    assert gate.payload["amount"] == "10000.00"
    assert gate.payload["threshold"] == "5000"
    assert gate.payload["awaiting_human_approval"] is True
    assert "discount" in gate.payload["proposed_action"]


def test_escalation_above_threshold_is_gated(session):
    _mk(
        session, "ap2", RootCause.INVOICE_FORGOTTEN,
        event_type=EventType.OVERDUE_INVOICE, days_overdue=20, attempts=1,
        amount="9000.00",
    )
    recovery.run(session)

    ev = store.get_event(session, "ap2")
    assert ev.status == EventStatus.EXCEPTION.value
    assert "escalation_stage_advanced" not in _actions(session, "ap2")
    gate = _row(session, "ap2", "awaiting_human_approval")
    assert "stage 2" in gate.payload["proposed_action"]


def test_gate_not_triggered_at_exactly_threshold(session):
    _mk(
        session, "ap3", RootCause.CHECKOUT_ABANDONED,
        event_type=EventType.ABANDONED_CHECKOUT, amount="5000.00", reason=None,
    )
    recovery.run(session)
    assert "awaiting_human_approval" not in _actions(session, "ap3")


# --- cooldown only delays, never excepts --------------------------

def test_cooldown_delays_second_contact_same_customer(session):
    _mk(session, "cd1", RootCause.BANK_DOWNTIME, customer="shared", amount="500.00")
    _mk(session, "cd2", RootCause.BANK_DOWNTIME, customer="shared", amount="500.00")

    recovery.run(session)

    c1 = datetime.fromisoformat(
        _row(session, "cd1", "suggested_alternate_method").payload["contact_at"]
    )
    c2 = datetime.fromisoformat(
        _row(session, "cd2", "suggested_alternate_method").payload["contact_at"]
    )
    assert c2 - c1 >= timedelta(hours=recovery.COOLDOWN_HOURS) - timedelta(minutes=1)
    # neither event turned into an exception because of the cooldown
    assert store.get_event(session, "cd2").status in (
        EventStatus.RECOVERED.value,
        EventStatus.EXCEPTION.value,
    )


# --- orchestration: skeleton, return value, idempotency ----------

def test_run_walks_action_taken_then_terminal(session):
    _mk(session, "sk1", RootCause.AUTH_FAILURE)
    recovery.run(session)
    actions = _actions(session, "sk1")
    assert actions[0] == "intervention_selected"
    assert actions[-1] in ("marked_recovered", "routed_to_exception")
    assert store.get_event(session, "sk1").status in (
        EventStatus.RECOVERED.value, EventStatus.EXCEPTION.value
    )


def test_run_is_idempotent(session):
    _mk(session, "id1", RootCause.INSUFFICIENT_FUNDS)
    _mk(session, "id2", RootCause.CARD_DECLINED)

    first = recovery.run(session)
    counts = {e: len(store.get_audit_trail(session, e)) for e in ("id1", "id2")}

    second = recovery.run(session)

    assert set(first) == {"id1", "id2"}
    assert second == []
    assert {e: len(store.get_audit_trail(session, e)) for e in ("id1", "id2")} == counts


def test_run_returns_every_examined_id(session):
    _mk(session, "rv1", RootCause.INSUFFICIENT_FUNDS)
    _mk(session, "rv2", RootCause.SUSPECTED_FRAUD)
    _mk(session, "rv3", RootCause.UNKNOWN)
    store.insert_event(
        session, event_id="rv4", event_type=EventType.FAILED_PAYMENT,
        customer_id="c", amount=Decimal("10.00"), raw_failure_reason="x",
    )  # stays 'detected' -> not examined

    examined = recovery.run(session)
    assert set(examined) == {"rv1", "rv2", "rv3"}


def test_unknown_root_cause_routes_to_exception(session):
    _mk(session, "un1", RootCause.UNKNOWN)
    recovery.run(session)
    ev = store.get_event(session, "un1")
    assert ev.status == EventStatus.EXCEPTION.value
    assert _row(session, "un1", "routed_to_exception").payload == {
        "reason": "unclassified root cause"
    }
