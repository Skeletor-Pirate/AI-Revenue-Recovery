"""Razorpay test-mode webhook listener (build-order step 9)."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db import store
from app.db.store import EventType
from app.webhooks import listener

pytestmark = pytest.mark.usefixtures("_require_postgres")

SECRET = "whsec_test_123"


def _payment_failed(amount_paise=250000, error_reason="payment_failed"):
    return {
        "entity": "event",
        "account_id": "acc_TEST",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_TESTFAIL001",
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": "order_TEST",
                    "method": "card",
                    "email": "buyer@example.com",
                    "contact": "+919000000000",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed",
                    "error_source": "customer",
                    "error_step": "payment_authentication",
                    "error_reason": error_reason,
                }
            }
        },
        "created_at": 1700000000,
    }


def _link_expired():
    return {
        "entity": "event",
        "event": "payment_link.expired",
        "contains": ["payment_link"],
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_TESTEXP",
                    "amount": 99900,
                    "currency": "INR",
                    "status": "expired",
                    "customer": {"email": "shopper@example.com"},
                    "customer_email": "shopper@example.com",
                }
            }
        },
        "created_at": 1700000100,
    }


def _invoice_expired():
    return {
        "entity": "event",
        "event": "invoice.expired",
        "contains": ["invoice"],
        "payload": {
            "invoice": {
                "entity": {
                    "id": "inv_TESTEXP",
                    "amount": 4500000,
                    "amount_due": 4500000,
                    "currency": "INR",
                    "status": "expired",
                    "customer_id": "cust_ABC123",
                }
            }
        },
        "created_at": 1700000200,
    }


def _captured():
    return {
        "entity": "event",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {"payment": {"entity": {"id": "pay_OK", "amount": 500, "currency": "INR"}}},
        "created_at": 1700000300,
    }


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# --- pure: signature ---------------------------------------------------

def test_verify_signature_roundtrip():
    body = b'{"event":"payment.failed"}'
    assert listener.verify_signature(body, _sign(body), SECRET) is True
    assert listener.verify_signature(body, "deadbeef", SECRET) is False
    assert listener.verify_signature(body, None, SECRET) is False
    assert listener.verify_signature(body + b" ", _sign(body), SECRET) is False  # tampered


# --- pure: mapping ---------------------------------------------------

def test_map_payment_failed():
    rec = listener.razorpay_event_to_eventcreate(_payment_failed())
    assert rec.event_type == EventType.FAILED_PAYMENT.value
    assert rec.amount == Decimal("2500.00")  # paise -> rupees
    assert rec.raw_failure_reason == "payment_failed"
    assert rec.event_id == "rzp_pay_TESTFAIL001"
    assert rec.currency == "INR"


def test_map_payment_failed_falls_back_to_error_code():
    rec = listener.razorpay_event_to_eventcreate(_payment_failed(error_reason=None))
    assert rec.raw_failure_reason == "BAD_REQUEST_ERROR"


def test_map_link_expired_is_abandoned_checkout():
    rec = listener.razorpay_event_to_eventcreate(_link_expired())
    assert rec.event_type == EventType.ABANDONED_CHECKOUT.value
    assert rec.amount == Decimal("999.00")
    assert rec.raw_failure_reason is None


def test_map_invoice_expired_is_overdue_invoice():
    rec = listener.razorpay_event_to_eventcreate(_invoice_expired())
    assert rec.event_type == EventType.OVERDUE_INVOICE.value
    assert rec.amount == Decimal("45000.00")
    assert rec.customer_id == "rzp_cust_ABC123"


def test_map_success_and_unknown_events_return_none():
    assert listener.razorpay_event_to_eventcreate(_captured()) is None
    assert listener.razorpay_event_to_eventcreate({"event": "foo.bar", "payload": {}}) is None


def test_map_zero_amount_returns_none():
    ev = _payment_failed(amount_paise=0)
    assert listener.razorpay_event_to_eventcreate(ev) is None


# --- endpoint -------------------------------------------------------

@pytest.fixture()
def client(session, monkeypatch):
    monkeypatch.setattr(
        "app.webhooks.listener.get_settings",
        lambda: SimpleNamespace(razorpay_webhook_secret=SECRET),
    )
    from tests.conftest import TEST_DATABASE_URL

    orig = store._engine
    store._engine = create_engine(TEST_DATABASE_URL)
    from app.main import app

    with TestClient(app) as c:
        yield c
    store._engine = orig


def _post(client, payload):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
    )


def test_endpoint_accepts_signed_payment_failed(client, session):
    r = _post(client, _payment_failed())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"
    ev = store.get_event(session, body["event_id"])
    assert ev is not None and ev.status == "detected"
    trail = store.get_audit_trail(session, ev.event_id)
    assert trail[0].action == "ingested_webhook_event"
    assert trail[0].payload["source"] == "razorpay_webhook"


def test_endpoint_rejects_bad_signature(client):
    body = json.dumps(_payment_failed()).encode()
    r = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "not-valid"},
    )
    assert r.status_code == 401


def test_endpoint_503_without_secret(session, monkeypatch):
    monkeypatch.setattr(
        "app.webhooks.listener.get_settings",
        lambda: SimpleNamespace(razorpay_webhook_secret=None),
    )
    from tests.conftest import TEST_DATABASE_URL

    orig = store._engine
    store._engine = create_engine(TEST_DATABASE_URL)
    from app.main import app

    with TestClient(app) as c:
        body = json.dumps(_payment_failed()).encode()
        r = c.post("/webhooks/razorpay", content=body,
                   headers={"X-Razorpay-Signature": _sign(body)})
    store._engine = orig
    assert r.status_code == 503


def test_endpoint_ignores_success_event(client):
    r = _post(client, _captured())
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_endpoint_is_idempotent(client, session):
    first = _post(client, _payment_failed())
    second = _post(client, _payment_failed())
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "ignored"
    assert second.json()["reason"] == "duplicate delivery"
    assert len(store.all_events(session)) == 1
