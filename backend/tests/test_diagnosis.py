"""Tests for the Diagnosis Agent (app.agents.diagnosis).

Covers: every reason -> root-cause rule, the event-type fallback, the
low-confidence Claude fallback path (monkeypatched — never hits the network),
the ``claude_classify`` no-API-key degrade, and a full fraud cluster ending
``flagged``. Uses the real Postgres ``session`` fixture from conftest.
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.agents import diagnosis
from app.db import store
from app.db.store import EventCreate, EventType, RootCause

pytestmark = pytest.mark.usefixtures("_require_postgres")


# --- helpers --------------------------------------------------------------

def _ns(**kw):
    kw.setdefault("event_id", "evt_x")
    kw.setdefault("event_type", EventType.FAILED_PAYMENT.value)
    kw.setdefault("raw_failure_reason", None)
    kw.setdefault("amount", Decimal("1000.00"))
    kw.setdefault("attempts_so_far", 0)
    kw.setdefault("days_overdue", 0)
    kw.setdefault("status", "detected")
    kw.setdefault("customer_id", "cust_1")
    kw.setdefault("created_at", datetime.now(timezone.utc))
    return types.SimpleNamespace(**kw)


def _insert(session, **kw):
    kw.setdefault("event_id", "evt_000")
    kw.setdefault("event_type", EventType.FAILED_PAYMENT)
    kw.setdefault("customer_id", "cust_000")
    kw.setdefault("amount", Decimal("1234.00"))
    kw.setdefault("created_at", datetime.now(timezone.utc) - timedelta(days=1))
    return store.insert_event(session, EventCreate(**kw))


def _fraud_cluster(session, *, reason="card_declined", size=4):
    """Mirror app.data.generate.build_fraud_cluster: same reason, tight amount
    band (<=50), 4 distinct customers, attempts 2-3, created_at within 40 min."""
    base = datetime.now(timezone.utc) - timedelta(days=3)
    amounts = [Decimal("4990.00"), Decimal("5000.00"), Decimal("5010.00"), Decimal("5020.00")]
    attempts = [2, 3, 2, 3]
    minutes = [0, 12, 25, 38]
    ids = []
    for i in range(size):
        ev = _insert(
            session,
            event_id=f"fraud_{i:02d}",
            event_type=EventType.FAILED_PAYMENT,
            customer_id=f"cust_fraud_{i}",
            amount=amounts[i],
            raw_failure_reason=reason,
            attempts_so_far=attempts[i],
            created_at=base + timedelta(minutes=minutes[i]),
        )
        ids.append(ev.event_id)
    return ids


# --- rules classifier ----------------------------------------------------

@pytest.mark.parametrize(
    "reason,expected,conf",
    [
        ("insufficient_fund", RootCause.INSUFFICIENT_FUNDS, 0.95),
        ("card_expired", RootCause.EXPIRED_INSTRUMENT, 0.9),
        ("card_number_invalid", RootCause.EXPIRED_INSTRUMENT, 0.9),
        ("mandate_creation_expired", RootCause.EXPIRED_INSTRUMENT, 0.9),
        ("mandate_creation_failed", RootCause.EXPIRED_INSTRUMENT, 0.9),
        ("bank_not_available", RootCause.BANK_DOWNTIME, 0.9),
        ("gateway_technical_error", RootCause.BANK_DOWNTIME, 0.9),
        ("authentication_failed", RootCause.AUTH_FAILURE, 0.9),
        ("payment_timed_out", RootCause.AUTH_FAILURE, 0.9),
        ("card_declined", RootCause.CARD_DECLINED, 0.8),
        ("card_disabled_for_online_payments", RootCause.CARD_DECLINED, 0.8),
        ("payment_cancelled", RootCause.CHECKOUT_ABANDONED, 0.7),
    ],
)
def test_classify_reason_rules(reason, expected, conf):
    rc, got_conf, matched, reasoning = diagnosis.classify(_ns(raw_failure_reason=reason))
    assert rc is expected
    assert got_conf == conf
    assert matched == reason
    assert reasoning


def test_payment_cancelled_stays_rules_only(session):
    """payment_cancelled -> checkout_abandoned @ 0.7 and never calls Claude."""
    _insert(session, event_id="evt_pc", raw_failure_reason="payment_cancelled")

    def _boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("claude_classify called for payment_cancelled")

    orig = diagnosis.claude_classify
    diagnosis.claude_classify = _boom
    try:
        diagnosis.run(session)
    finally:
        diagnosis.claude_classify = orig

    ev = store.get_event(session, "evt_pc")
    assert ev.status == "diagnosed"
    assert ev.root_cause == RootCause.CHECKOUT_ABANDONED.value
    assert ev.diagnosis_confidence == 0.7


@pytest.mark.parametrize(
    "etype,expected,conf",
    [
        (EventType.ABANDONED_CHECKOUT, RootCause.CHECKOUT_ABANDONED, 0.85),
        (EventType.OVERDUE_INVOICE, RootCause.INVOICE_FORGOTTEN, 0.85),
        (EventType.EXPIRED_MANDATE, RootCause.EXPIRED_INSTRUMENT, 0.9),
    ],
)
def test_classify_event_type_fallback(etype, expected, conf):
    rc, got_conf, matched, reasoning = diagnosis.classify(
        _ns(event_type=etype.value, raw_failure_reason=None)
    )
    assert rc is expected
    assert got_conf == conf
    assert matched is None
    assert reasoning


def test_classify_unmatched_free_text_is_low_confidence_unknown():
    rc, conf, matched, reasoning = diagnosis.classify(
        _ns(raw_failure_reason="some_new_gateway_gremlin")
    )
    assert rc is RootCause.UNKNOWN
    assert conf <= 0.5
    assert matched == "some_new_gateway_gremlin"
    assert reasoning


# --- run: rules path ---------------------------------------------------

def test_run_rules_path_diagnoses_and_logs(session):
    _insert(session, event_id="evt_if", raw_failure_reason="insufficient_fund")
    _insert(
        session,
        event_id="evt_inv",
        event_type=EventType.OVERDUE_INVOICE,
        raw_failure_reason=None,
        days_overdue=40,
    )

    returned = diagnosis.run(session)
    assert returned == ["evt_if", "evt_inv"]

    ev = store.get_event(session, "evt_if")
    assert ev.status == "diagnosed"
    assert ev.root_cause == RootCause.INSUFFICIENT_FUNDS.value
    assert ev.diagnosis_confidence == 0.95

    trail = store.get_audit_trail(session, "evt_if")
    assert len(trail) == 1
    row = trail[0]
    assert row.agent == "diagnosis"
    assert row.action == "classified_root_cause"
    assert row.reasoning
    assert row.payload == {
        "root_cause": "insufficient_funds",
        "confidence": 0.95,
        "matched_reason": "insufficient_fund",
    }


def test_run_is_idempotent(session):
    _insert(session, event_id="evt_if", raw_failure_reason="insufficient_fund")
    diagnosis.run(session)
    second = diagnosis.run(session)
    assert second == []
    assert len(store.get_audit_trail(session, "evt_if")) == 1


# --- run: Claude fallback path (monkeypatched) ------------------------

def test_low_confidence_triggers_claude_fallback(session, monkeypatch):
    _insert(session, event_id="evt_ft", raw_failure_reason="weird_unmapped_reason")

    calls = []

    def fake_claude(event, settings=None):
        calls.append(event.event_id)
        return (RootCause.BANK_DOWNTIME, 0.72, "looks like a gateway blip", False)

    monkeypatch.setattr(diagnosis, "claude_classify", fake_claude)

    diagnosis.run(session)
    assert calls == ["evt_ft"]

    ev = store.get_event(session, "evt_ft")
    assert ev.status == "diagnosed"
    assert ev.root_cause == RootCause.BANK_DOWNTIME.value
    assert ev.diagnosis_confidence == 0.72

    trail = store.get_audit_trail(session, "evt_ft")
    assert len(trail) == 1
    row = trail[0]
    assert row.action == "llm_classified_root_cause"
    assert row.payload["root_cause"] == "bank_downtime"
    assert row.payload["confidence"] == 0.72
    assert row.payload["used_fallback"] is False
    assert "model" in row.payload
    assert row.reasoning


def test_high_confidence_rule_never_calls_claude(session, monkeypatch):
    _insert(session, event_id="evt_if", raw_failure_reason="insufficient_fund")
    monkeypatch.setattr(
        diagnosis,
        "claude_classify",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")),
    )
    diagnosis.run(session)
    assert store.get_event(session, "evt_if").root_cause == "insufficient_funds"


def test_claude_classify_no_api_key_degrades():
    settings = types.SimpleNamespace(
        anthropic_api_key=None, anthropic_model="claude-sonnet-5"
    )
    rc, conf, reasoning, used_fallback = diagnosis.claude_classify(_ns(), settings)
    assert rc is RootCause.UNKNOWN
    assert conf == 0.3
    assert used_fallback is True
    assert reasoning


def test_claude_fallback_coerced_to_unknown_when_used_in_run(session, monkeypatch):
    _insert(session, event_id="evt_ft", raw_failure_reason="weird_unmapped_reason")
    monkeypatch.setattr(
        diagnosis,
        "claude_classify",
        lambda e, s=None: (RootCause.UNKNOWN, 0.3, "no api key", True),
    )
    diagnosis.run(session)
    ev = store.get_event(session, "evt_ft")
    assert ev.root_cause == "unknown"
    assert ev.diagnosis_confidence == 0.3
    row = store.get_audit_trail(session, "evt_ft")[0]
    assert row.payload["used_fallback"] is True


# --- fraud cluster triage -------------------------------------------

def test_find_fraud_clusters_pure(session):
    ids = _fraud_cluster(session)
    events = store.all_events(session)
    clusters = diagnosis.find_fraud_clusters(events)
    assert len(clusters) == 1
    sig = clusters[0]["signature"]
    assert sig["raw_failure_reason"] == "card_declined"
    assert sig["customer_count"] == 4
    assert sig["amount_band"] == ["4990.00", "5020.00"]
    assert 0 <= sig["window_minutes"] <= 60
    assert {e.event_id for e in clusters[0]["members"]} == set(ids)


def test_run_flags_fraud_cluster(session):
    ids = _fraud_cluster(session)
    # an ordinary same-reason event that must NOT be swept in (low attempts)
    _insert(
        session,
        event_id="evt_ok",
        raw_failure_reason="card_declined",
        attempts_so_far=1,
        amount=Decimal("5000.00"),
    )

    returned = diagnosis.run(session)

    for eid in ids:
        ev = store.get_event(session, eid)
        assert ev.status == "flagged"
        assert ev.root_cause == RootCause.SUSPECTED_FRAUD.value
        trail = store.get_audit_trail(session, eid)
        assert len(trail) == 1
        row = trail[0]
        assert row.agent == "triage"
        assert row.action == "halted_fraud_cluster"
        assert row.reasoning
        assert row.payload["signature"]["raw_failure_reason"] == "card_declined"
        assert row.payload["signature"]["customer_count"] == 4
        assert set(row.payload["cluster_event_ids"]) == set(ids)

    ok = store.get_event(session, "evt_ok")
    assert ok.status == "diagnosed"
    assert ok.root_cause == RootCause.CARD_DECLINED.value

    assert set(returned) == set(ids) | {"evt_ok"}


def test_same_reason_without_signature_not_flagged(session):
    # 3 events, same reason, but spread over days and only 2 customers
    base = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(3):
        _insert(
            session,
            event_id=f"evt_s{i}",
            raw_failure_reason="card_declined",
            attempts_so_far=2,
            customer_id=f"cust_s{i % 2}",
            amount=Decimal("5000.00"),
            created_at=base + timedelta(days=i),
        )
    diagnosis.run(session)
    for i in range(3):
        assert store.get_event(session, f"evt_s{i}").status == "diagnosed"
