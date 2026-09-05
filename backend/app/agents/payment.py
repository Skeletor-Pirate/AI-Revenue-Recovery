"""Unified payment-capture engine — AGENTS_CONTRACT.md §11.

This module is the **single integrity fix** of this round: ``Event.status``
becomes ``RECOVERED`` from a capture only through ``apply_capture`` below.
Nothing else — not ``recovery.py``'s dispatch logic, not a conversation
outcome, not a bare coin flip — may ever write ``status=RECOVERED``.

Four public functions:

    razorpay_configured(settings) -> bool
    create_payment_link(session, event, *, settings) -> PaymentLinkResult
    resolve_fake_capture(event, link_id, *, settings, attempt=1) -> CaptureResult
    apply_capture(session, event, capture, *, source) -> None

``resolve_fake_capture`` is PURE — no session/DB access, reads only
in-memory ``Event`` fields. This is a hard interface contract:
``app/agents/playground.py`` calls it directly, without a session, and must
never gain one. It reuses ``recovery.SUCCESS_RATES`` / ``recovery._stable_hash``
(imported, never duplicated) for the per-root-cause success roll.

Real Razorpay Payment Links (POST /v1/payment_links, test mode) are created
when both ``razorpay_key_id``/``razorpay_key_secret`` are configured. Any HTTP
failure (timeout, 4xx/5xx) never raises — it falls through to the
deterministic fake-gateway path, same posture as ``app/llm.py``'s provider
fallback (see AGENTS_CONTRACT.md §13 P4).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, TypedDict

import httpx

from app.agents import ptp
from app.agents.recovery import SUCCESS_RATES, _stable_hash
from app.db import store
from app.db.store import Agent, Event, EventStatus, MONEY, PaymentLinkStatus

RECOVERY = Agent.RECOVERY

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
_HTTP_TIMEOUT = 5.0

# Deterministic fake-gateway failure reasons other than insufficient_funds
# (which is forced independently by the balance check).
_OTHER_FAILURE_REASONS = ("wrong_otp", "user_cancelled")


class PaymentLinkResult(TypedDict):
    link_id: str
    link_url: str
    status: str
    source: str  # "razorpay" | "fake_gateway"


class CaptureResult(TypedDict):
    captured: bool
    reason: str  # "captured" | "insufficient_funds" | "wrong_otp" | "user_cancelled"
    amount: Decimal


def _q(amount: Any) -> Decimal:
    return Decimal(str(amount or 0)).quantize(MONEY)


def razorpay_configured(settings: Any) -> bool:
    """True iff both razorpay_key_id AND razorpay_key_secret are set.

    Note: this alone does NOT mean the real API will be called -- see
    ``_should_use_real_razorpay``. Keys being present is necessary but not
    sufficient; the operator must also opt in via
    ``use_real_razorpay_payment_links``.
    """
    return bool(
        getattr(settings, "razorpay_key_id", None)
        and getattr(settings, "razorpay_key_secret", None)
    )


def _should_use_real_razorpay(settings: Any) -> bool:
    """True only when keys are configured AND the operator has explicitly
    opted in. The fake gateway is the default even with valid keys present,
    because Razorpay's test mode caps a business account at 30 payment links
    total -- once hit, every real call fails (429 RATE_LIMIT_EXCEEDED) and
    falls back anyway, so auto-attempting just because keys exist is a trap.
    """
    return razorpay_configured(settings) and bool(
        getattr(settings, "use_real_razorpay_payment_links", False)
    )


def _fake_link(event: Event, *, settings: Any) -> PaymentLinkResult:
    """Deterministic fake-gateway link. ``link_id`` doubles as the URL-safe
    ``/pay/:token`` token (AGENTS_CONTRACT.md §11 P2)."""
    token = f"fake_{event.event_id}"
    base = getattr(settings, "payment_engine_base_url", "http://localhost:5173")
    return {
        "link_id": token,
        "link_url": f"{str(base).rstrip('/')}/pay/{token}",
        "status": "created",
        "source": "fake_gateway",
    }


def create_payment_link(
    session: store.Session, event: Event, *, settings: Any,
) -> PaymentLinkResult:
    """Real Razorpay test-mode Payment Link (POST /v1/payment_links) when
    ``_should_use_real_razorpay(settings)`` (keys configured AND explicitly
    opted in via ``use_real_razorpay_payment_links``), else a deterministic
    fake link/token. Keys being merely present is not enough by default --
    see ``_should_use_real_razorpay``'s docstring for why.

    A Razorpay HTTP failure (timeout, 4xx/5xx) NEVER raises — falls through
    to the fake path. Does not write to the DB and does not call
    ``log_action`` itself — the caller stamps the returned fields via
    ``update_event`` and owns the audit row (AGENTS_CONTRACT.md §11 P8).
    ``session`` is currently structurally unused (kept for call-site
    consistency + a future DB-backed idempotency check).
    """
    if _should_use_real_razorpay(settings):
        try:
            amount_paise = int((_q(event.amount) * 100).to_integral_value())
            body: dict[str, Any] = {
                "amount": amount_paise,
                "currency": event.currency or "INR",
                "accept_partial": False,
                "description": f"AI Revenue Recovery — payment for {event.event_id}",
                "reference_id": event.event_id,
                "notes": {"event_id": event.event_id},
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
            }
            if event.customer_name or event.customer_phone:
                body["customer"] = {
                    "name": event.customer_name or event.customer_id,
                    "contact": event.customer_phone or "",
                }
            resp = httpx.post(
                f"{RAZORPAY_API_BASE}/payment_links",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json=body,
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "link_id": data["id"],
                "link_url": data["short_url"],
                "status": "created",
                "source": "razorpay",
            }
        except Exception:
            # Never raise -- degrade to the fake gateway, same posture as
            # app/llm.py's provider fallback (AGENTS_CONTRACT.md §13 P4).
            pass
    return _fake_link(event, settings=settings)


def resolve_fake_capture(
    event: Event, link_id: str, *, settings: Any, attempt: int = 1,
) -> CaptureResult:
    """PURE function — no session/DB access, reads only in-memory ``Event``
    fields (amount, root_cause, customer_fake_balance, event_id).

    Deterministic given the same inputs: hashes
    ``f"{event.event_id}:{link_id}:{attempt}"`` (the ``attempt`` salt is load
    -bearing — AGENTS_CONTRACT.md §13 P5 — without it a bounded retry with the
    same link would replay the identical failure forever).
    ``event.customer_fake_balance < event.amount`` forces
    ``reason="insufficient_funds"`` regardless of the root-cause roll, on
    every attempt (a real balance shortfall doesn't self-cure by retrying).
    """
    amount = _q(event.amount)
    balance = getattr(event, "customer_fake_balance", None)
    if balance is not None and _q(balance) < amount:
        return {"captured": False, "reason": "insufficient_funds", "amount": amount}

    rc = getattr(event, "root_cause", None) or "unknown"
    default_rate = int(round(getattr(settings, "fake_gateway_success_rate", 0.65) * 100))
    p = SUCCESS_RATES.get(rc, default_rate)

    salt = f"{event.event_id}:{link_id}:{attempt}"
    roll = _stable_hash(salt) % 100
    if roll < p:
        return {"captured": True, "reason": "captured", "amount": amount}

    reason = _OTHER_FAILURE_REASONS[_stable_hash(f"{salt}:reason") % len(_OTHER_FAILURE_REASONS)]
    return {"captured": False, "reason": reason, "amount": amount}


def apply_capture(
    session: store.Session, event: Event, capture: CaptureResult, *, source: str,
) -> None:
    """THE ONLY place ``Event.status`` ever becomes ``RECOVERED`` from a
    capture. On failure, ``Event.status`` is left untouched — the caller
    (``recovery.py`` or the ``/pay/:token`` router) decides exception vs. a
    bounded retry (AGENTS_CONTRACT.md §11 / §13 P3).
    """
    if capture["captured"]:
        amount = _q(capture["amount"])
        store.update_event(
            session,
            event.event_id,
            status=EventStatus.RECOVERED,
            recovered_amount=amount,
            payment_link_status=PaymentLinkStatus.CAPTURED,
            payment_capture_source=source,
        )
        store.log_action(
            session,
            event_id=event.event_id,
            agent=RECOVERY,
            action="payment_captured",
            reasoning=(
                f"Captured payment of {amount} for {event.event_id} via "
                f"{source}; marking recovered."
            ),
            payload={
                "recovered_amount": str(amount),
                "source": source,
                "link_id": event.payment_link_id or "",
            },
        )
        refreshed = store.get_event(session, event.event_id)
        if refreshed is not None:
            ptp.evaluate_ptp_status(session, refreshed)
    else:
        reason = capture["reason"]
        store.update_event(
            session,
            event.event_id,
            payment_link_status=PaymentLinkStatus.FAILED,
            payment_capture_source=source,
        )
        store.log_action(
            session,
            event_id=event.event_id,
            agent=RECOVERY,
            action="payment_capture_failed",
            reasoning=(
                f"Capture attempt for {event.event_id} failed via {source}: "
                f"{reason}."
            ),
            payload={
                "reason": reason,
                "source": source,
                "link_id": event.payment_link_id or "",
            },
        )
