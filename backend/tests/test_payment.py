"""Tests for the unified payment-capture engine (app/agents/payment.py).

Run from backend/ with Postgres up:  uv run pytest -q tests/test_payment.py
The `session` fixture (tests/conftest.py) resets the test DB per test and is
skipped automatically when Postgres is unreachable.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.agents import payment, recovery
from app.db import store
from app.db.store import EventStatus, EventType, PaymentLinkStatus
from app.webhooks import listener


@pytest.fixture()
def engine_on_test_db(session):
    """`listener._handle_capture_webhook` opens its own `store.get_session()`
    (no session param in its signature -- it's an HTTP-endpoint helper, not an
    agent function). Point the module-level engine at the test database for
    the duration of the test, same pattern as tests/test_webhooks.py's
    `client` fixture."""
    from tests.conftest import TEST_DATABASE_URL

    orig = store._engine
    store._engine = create_engine(TEST_DATABASE_URL)
    yield
    store._engine = orig


# --- helpers --------------------------------------------------------------

def _settings(**overrides):
    base = dict(
        razorpay_key_id=None,
        razorpay_key_secret=None,
        use_real_razorpay_payment_links=False,
        payment_engine_base_url="http://localhost:5173",
        fake_gateway_success_rate=0.65,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mk(
    session,
    event_id,
    root_cause="insufficient_funds",
    *,
    amount="1000.00",
    customer_fake_balance=None,
    payment_link_id=None,
    payment_link_status=PaymentLinkStatus.NONE,
    customer_name=None,
    customer_phone=None,
):
    store.insert_event(
        session,
        event_id=event_id,
        event_type=EventType.FAILED_PAYMENT,
        customer_id=f"c_{event_id}",
        amount=Decimal(amount),
        raw_failure_reason="insufficient_fund",
        customer_fake_balance=customer_fake_balance,
        payment_link_id=payment_link_id,
        payment_link_status=payment_link_status,
        customer_name=customer_name,
        customer_phone=customer_phone,
    )
    store.update_event(
        session,
        event_id,
        status=EventStatus.DIAGNOSED,
        root_cause=root_cause,
        diagnosis_confidence=0.9,
    )
    return store.get_event(session, event_id)


# --- razorpay_configured ----------------------------------------------------

def test_razorpay_configured_requires_both_keys():
    assert payment.razorpay_configured(_settings()) is False
    assert payment.razorpay_configured(_settings(razorpay_key_id="rzp_test_1")) is False
    assert payment.razorpay_configured(
        _settings(razorpay_key_id="rzp_test_1", razorpay_key_secret="secret")
    ) is True


def test_should_use_real_razorpay_requires_explicit_opt_in():
    # Keys alone are NOT enough -- the fake gateway is the default even when
    # both keys are configured, since Razorpay's test-mode 30-link cap makes
    # auto-attempting a trap (AGENTS_CONTRACT.md P-series follow-up).
    both_keys = _settings(razorpay_key_id="rzp_test_1", razorpay_key_secret="secret")
    assert payment._should_use_real_razorpay(both_keys) is False
    assert payment._should_use_real_razorpay(
        _settings(razorpay_key_id="rzp_test_1", razorpay_key_secret="secret",
                   use_real_razorpay_payment_links=True)
    ) is True
    # Opt-in flag alone, without both keys, still does not enable it.
    assert payment._should_use_real_razorpay(
        _settings(razorpay_key_id="rzp_test_1", use_real_razorpay_payment_links=True)
    ) is False


def test_create_payment_link_never_calls_real_api_when_opt_in_is_false(session, monkeypatch):
    """Even with valid-looking keys configured, create_payment_link must not
    attempt the real Razorpay HTTP call unless use_real_razorpay_payment_links
    is explicitly True -- this is the fix for the 30-link test-mode cap trap."""
    event = _mk(session, "evt_pl_optin")
    calls = []
    monkeypatch.setattr(payment.httpx, "post", lambda *a, **k: calls.append((a, k)))
    settings = _settings(razorpay_key_id="rzp_test_x", razorpay_key_secret="secret_x")
    result = payment.create_payment_link(session, event, settings=settings)
    assert calls == []
    assert result["source"] == "fake_gateway"


# --- create_payment_link: fake path -----------------------------------------

def test_create_payment_link_fake_shape(session):
    event = _mk(session, "evt_pl_1")
    result = payment.create_payment_link(session, event, settings=_settings())
    assert result["source"] == "fake_gateway"
    assert result["status"] == "created"
    assert result["link_id"] == f"fake_{event.event_id}"
    assert result["link_url"] == f"http://localhost:5173/pay/fake_{event.event_id}"


def test_create_payment_link_real_http_failure_degrades_to_fake(session, monkeypatch):
    event = _mk(session, "evt_pl_2")

    def _boom(*_a, **_k):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(payment.httpx, "post", _boom)
    settings = _settings(razorpay_key_id="rzp_test_x", razorpay_key_secret="secret_x",
                          use_real_razorpay_payment_links=True)
    result = payment.create_payment_link(session, event, settings=settings)
    assert result["source"] == "fake_gateway"
    assert result["link_id"] == f"fake_{event.event_id}"


def test_create_payment_link_real_4xx_degrades_to_fake(session, monkeypatch):
    event = _mk(session, "evt_pl_3")

    class _Resp:
        status_code = 400

        def raise_for_status(self):
            import httpx as _httpx
            raise _httpx.HTTPStatusError("bad request", request=None, response=self)

        def json(self):
            return {}

    monkeypatch.setattr(payment.httpx, "post", lambda *a, **k: _Resp())
    settings = _settings(razorpay_key_id="rzp_test_x", razorpay_key_secret="secret_x",
                          use_real_razorpay_payment_links=True)
    result = payment.create_payment_link(session, event, settings=settings)
    assert result["source"] == "fake_gateway"


def test_create_payment_link_real_success_shape(session, monkeypatch):
    event = _mk(session, "evt_pl_4")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "plink_ABC123", "short_url": "https://rzp.io/i/abc123"}

    captured = {}

    def _fake_post(url, *, auth, json, timeout):
        captured["url"] = url
        captured["auth"] = auth
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(payment.httpx, "post", _fake_post)
    settings = _settings(razorpay_key_id="rzp_test_x", razorpay_key_secret="secret_x",
                          use_real_razorpay_payment_links=True)
    result = payment.create_payment_link(session, event, settings=settings)
    assert result == {
        "link_id": "plink_ABC123",
        "link_url": "https://rzp.io/i/abc123",
        "status": "created",
        "source": "razorpay",
    }
    assert captured["url"].endswith("/payment_links")
    assert captured["json"]["amount"] == 100000  # paise
    assert captured["json"]["reference_id"] == event.event_id
    assert captured["json"]["notes"] == {"event_id": event.event_id}


# --- resolve_fake_capture: purity + determinism -----------------------------

def test_resolve_fake_capture_is_pure_and_deterministic(session):
    event = _mk(session, "evt_cap_1", root_cause="insufficient_funds")
    settings = _settings()
    r1 = payment.resolve_fake_capture(event, "fake_evt_cap_1", settings=settings, attempt=1)
    r2 = payment.resolve_fake_capture(event, "fake_evt_cap_1", settings=settings, attempt=1)
    assert r1 == r2
    assert set(r1.keys()) == {"captured", "reason", "amount"}
    assert r1["amount"] == Decimal("1000.00")


def test_resolve_fake_capture_different_attempts_can_differ_in_principle(session):
    # Not every event/attempt pair necessarily flips outcome, but the salt
    # must include `attempt` -- verify by hashing directly.
    event = _mk(session, "evt_cap_2")
    a1 = recovery._stable_hash(f"{event.event_id}:linkX:1") % 100
    a2 = recovery._stable_hash(f"{event.event_id}:linkX:2") % 100
    assert a1 != a2  # sanity: our chosen event_id doesn't collide for this salt


def test_resolve_fake_capture_uses_success_rates_table(session):
    # root_cause with SUCCESS_RATES=100 would always capture; use the real
    # table and just confirm the roll is derived from it deterministically.
    event = _mk(session, "evt_cap_3", root_cause="bank_downtime")
    settings = _settings()
    result = payment.resolve_fake_capture(event, "fake_evt_cap_3", settings=settings, attempt=1)
    salt = f"{event.event_id}:fake_evt_cap_3:1"
    roll = recovery._stable_hash(salt) % 100
    expect_captured = roll < recovery.SUCCESS_RATES["bank_downtime"]
    assert result["captured"] is expect_captured


def test_resolve_fake_capture_insufficient_balance_forces_reason(session):
    event = _mk(
        session, "evt_cap_4", root_cause="bank_downtime",
        amount="1000.00", customer_fake_balance="100.00",
    )
    settings = _settings()
    for attempt in (1, 2, 3):
        result = payment.resolve_fake_capture(
            event, "fake_evt_cap_4", settings=settings, attempt=attempt,
        )
        assert result["captured"] is False
        assert result["reason"] == "insufficient_funds"


def test_resolve_fake_capture_sufficient_balance_does_not_force_failure(session):
    event = _mk(
        session, "evt_cap_5", root_cause="insufficient_funds",
        amount="1000.00", customer_fake_balance="5000.00",
    )
    settings = _settings()
    result = payment.resolve_fake_capture(event, "fake_evt_cap_5", settings=settings, attempt=1)
    # With sufficient balance the outcome follows the success-rate roll, not
    # a forced insufficient_funds.
    salt = "evt_cap_5:fake_evt_cap_5:1"
    roll = recovery._stable_hash(salt) % 100
    if roll < recovery.SUCCESS_RATES["insufficient_funds"]:
        assert result["captured"] is True
    else:
        assert result["reason"] in ("wrong_otp", "user_cancelled")


def test_resolve_fake_capture_never_touches_session():
    """Purity contract: no `session` kwarg exists on the signature at all."""
    import inspect
    params = inspect.signature(payment.resolve_fake_capture).parameters
    assert "session" not in params


# --- apply_capture -----------------------------------------------------------

def test_apply_capture_success_sets_recovered(session):
    event = _mk(session, "evt_ac_1", payment_link_id="fake_evt_ac_1",
                payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE)
    capture = {"captured": True, "reason": "captured", "amount": Decimal("1000.00")}
    payment.apply_capture(session, event, capture, source="fake_gateway")

    updated = store.get_event(session, event.event_id)
    assert updated.status == EventStatus.RECOVERED
    assert updated.recovered_amount == Decimal("1000.00")
    assert updated.payment_link_status == PaymentLinkStatus.CAPTURED
    assert updated.payment_capture_source == "fake_gateway"

    trail = store.get_audit_trail(session, event.event_id)
    row = next(r for r in trail if r.action == "payment_captured")
    assert row.reasoning
    assert row.payload["source"] == "fake_gateway"


def test_apply_capture_failure_leaves_status_untouched(session):
    event = _mk(session, "evt_ac_2", payment_link_id="fake_evt_ac_2",
                payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE)
    original_status = event.status
    capture = {"captured": False, "reason": "wrong_otp", "amount": Decimal("1000.00")}
    payment.apply_capture(session, event, capture, source="fake_gateway")

    updated = store.get_event(session, event.event_id)
    assert updated.status == original_status
    assert updated.payment_link_status == PaymentLinkStatus.FAILED
    assert updated.payment_capture_source == "fake_gateway"

    trail = store.get_audit_trail(session, event.event_id)
    row = next(r for r in trail if r.action == "payment_capture_failed")
    assert row.reasoning
    assert row.payload["reason"] == "wrong_otp"


def test_apply_capture_reasoning_never_empty(session):
    event = _mk(session, "evt_ac_3")
    for capture in (
        {"captured": True, "reason": "captured", "amount": Decimal("500.00")},
        {"captured": False, "reason": "user_cancelled", "amount": Decimal("500.00")},
    ):
        payment.apply_capture(session, event, capture, source="fake_gateway")
    trail = store.get_audit_trail(session, event.event_id)
    for row in trail:
        assert row.reasoning and row.reasoning.strip()


# --- webhook wiring ----------------------------------------------------------

def test_webhook_captures_via_notes_event_id(session, engine_on_test_db):
    event = _mk(session, "evt_wh_1", payment_link_id="plink_wh1",
                payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE)
    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_1", "notes": {"event_id": event.event_id},
        }}},
    }
    result = listener._handle_capture_webhook(payload, "payment.captured")
    assert result["status"] == "captured"
    session.expire_all()  # the webhook wrote via its own Session
    updated = store.get_event(session, event.event_id)
    assert updated.status == EventStatus.RECOVERED
    assert updated.payment_capture_source == "razorpay_webhook"


def test_webhook_captures_via_reference_id_fallback(session, engine_on_test_db):
    event = _mk(session, "evt_wh_2", payment_link_id="plink_wh2",
                payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE)
    payload = {
        "event": "payment_link.paid",
        "payload": {"payment_link": {"entity": {
            "id": "plink_wh2", "reference_id": event.event_id,
        }}},
    }
    result = listener._handle_capture_webhook(payload, "payment_link.paid")
    assert result["status"] == "captured"
    session.expire_all()  # the webhook wrote via its own Session
    updated = store.get_event(session, event.event_id)
    assert updated.status == EventStatus.RECOVERED


def test_webhook_unmatched_event_id_is_ignored(session, engine_on_test_db):
    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_x", "notes": {"event_id": "evt_does_not_exist"},
        }}},
    }
    result = listener._handle_capture_webhook(payload, "payment.captured")
    assert result["status"] == "ignored"


def test_webhook_duplicate_delivery_is_idempotent(session, engine_on_test_db):
    event = _mk(session, "evt_wh_3", payment_link_id="plink_wh3",
                payment_link_status=PaymentLinkStatus.CAPTURED)
    store.update_event(session, event.event_id, status=EventStatus.RECOVERED,
                        recovered_amount=Decimal("1000.00"))
    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_1", "notes": {"event_id": event.event_id},
        }}},
    }
    result = listener._handle_capture_webhook(payload, "payment.captured")
    assert result["status"] == "ignored"
    assert result["reason"] == "already captured"


def test_webhook_no_event_id_in_payload_is_ignored(monkeypatch):
    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_1"}}},
    }
    result = listener._handle_capture_webhook(payload, "payment.captured")
    assert result["status"] == "ignored"


# --- /pay/:token router (app/api/payment_routes.py) -------------------------

@pytest.fixture()
def pay_client(engine_on_test_db, monkeypatch):
    """A standalone FastAPI app mounting only payment_routes.router -- this
    router isn't mounted in app/main.py yet (team-lead's job), so we exercise
    it in isolation, same DB-pointing trick as `engine_on_test_db`."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import payment_routes

    monkeypatch.setattr(payment_routes, "get_settings", lambda: _settings())

    app = FastAPI()
    app.include_router(payment_routes.router)
    with TestClient(app) as c:
        yield c


def test_pay_get_returns_masked_display_data(session, pay_client):
    event = _mk(
        session, "evt_router_1", root_cause="bank_downtime",
        payment_link_id="fake_evt_router_1",
        payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE,
        customer_name="Rahul Sharma",
    )
    resp = pay_client.get(f"/api/pay/{event.payment_link_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event.event_id
    assert body["customer_name"] != "Rahul Sharma"
    assert body["customer_name"].startswith("R") and body["customer_name"].endswith("a")
    assert body["amount"] == "1000.00"
    assert body["attempts_remaining"] == 3


def test_pay_get_unknown_token_404s(pay_client):
    resp = pay_client.get("/api/pay/does_not_exist")
    assert resp.status_code == 404


def test_pay_attempt_success_sets_recovered(session, pay_client, monkeypatch):
    event = _mk(
        session, "evt_router_2", root_cause="bank_downtime",
        payment_link_id="fake_evt_router_2",
        payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE,
    )
    from app.api import payment_routes
    monkeypatch.setattr(
        payment_routes.payment, "resolve_fake_capture",
        lambda *a, **k: {"captured": True, "reason": "captured", "amount": Decimal("1000.00")},
    )
    resp = pay_client.post(f"/api/pay/{event.payment_link_id}/attempt")
    assert resp.status_code == 200
    body = resp.json()
    assert body["captured"] is True
    session.expire_all()
    updated = store.get_event(session, event.event_id)
    assert updated.status == EventStatus.RECOVERED


def test_pay_attempt_bounded_to_three_then_409(session, pay_client, monkeypatch):
    event = _mk(
        session, "evt_router_3", root_cause="bank_downtime",
        payment_link_id="fake_evt_router_3",
        payment_link_status=PaymentLinkStatus.AWAITING_CAPTURE,
    )
    from app.api import payment_routes
    monkeypatch.setattr(
        payment_routes.payment, "resolve_fake_capture",
        lambda *a, **k: {"captured": False, "reason": "wrong_otp", "amount": Decimal("1000.00")},
    )
    for _ in range(3):
        resp = pay_client.post(f"/api/pay/{event.payment_link_id}/attempt")
        assert resp.status_code == 200
        assert resp.json()["captured"] is False

    resp = pay_client.post(f"/api/pay/{event.payment_link_id}/attempt")
    assert resp.status_code == 409
    body = resp.json()
    assert body == {
        "status": "error",
        "reason": "max_attempts_exceeded",
        "attempts_remaining": 0,
    }
