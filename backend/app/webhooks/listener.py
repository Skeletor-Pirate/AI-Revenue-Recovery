"""Razorpay **test-mode** webhook listener — build-order step 9.

An alternative event source to the synthetic generator: real Razorpay test-mode
webhook deliveries (`payment.failed`, `payment_link.expired`, `invoice.expired`,
`subscription.halted`, ...) are verified, mapped to an ``EventCreate``, and
inserted into the same store the Detection Agent reads. From there the pipeline
runs unchanged — it never cares whether the generator or a webhook produced the
event.

**Test mode only. No real money.**

Signature: Razorpay signs each POST with HMAC-SHA256 over the *raw* request body,
keyed by the dashboard webhook secret, in the ``X-Razorpay-Signature`` header
(razorpay.com/docs/webhooks/validate-test). We verify it ourselves rather than
pull in the SDK's `utility.verify_webhook_signature`.

Amounts in Razorpay payloads are integer **paise** — divide by 100 for rupees.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from datetime import datetime, timezone

from app.config import get_settings
from app.db import store
from app.db.store import EventCreate, EventType

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_ID_PREFIX = "rzp_"  # keep synthetic (evt_/fraud_) and webhook ids distinct

# Razorpay event -> (our EventType, the payload key holding the entity).
# Only *at-risk* events are ingested; success events are acknowledged and
# ignored (reconciliation is out of scope).
AT_RISK_EVENTS: dict[str, tuple[EventType, str]] = {
    "payment.failed": (EventType.FAILED_PAYMENT, "payment"),
    "payment_link.expired": (EventType.ABANDONED_CHECKOUT, "payment_link"),
    "invoice.expired": (EventType.OVERDUE_INVOICE, "invoice"),
    "subscription.halted": (EventType.EXPIRED_MANDATE, "subscription"),
    "subscription.pending": (EventType.FAILED_PAYMENT, "subscription"),
}

# events we accept (200) but take no action on
SUCCESS_EVENTS = {
    "payment.captured", "payment.authorized", "order.paid", "payment_link.paid",
    "subscription.charged", "invoice.paid", "refund.processed",
}


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """HMAC-SHA256 of the raw body, keyed by the webhook secret, hex-compared
    in constant time. Matches razorpay.com/docs/webhooks/validate-test."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _entity(event: dict[str, Any], key: str) -> dict[str, Any]:
    return (event.get("payload", {}).get(key, {}) or {}).get("entity", {}) or {}


def _customer_id(entity: dict[str, Any]) -> str:
    """A stable, non-PII customer key. A real Razorpay ``cust_...`` id is used
    as-is; an email / phone is hashed so no PII lands in the store."""
    cid = entity.get("customer_id")
    if isinstance(cid, str) and cid.startswith("cust_"):
        return f"{_ID_PREFIX}{cid}"
    notes = entity.get("notes") or {}
    if isinstance(notes, dict) and notes.get("customer_id"):
        return f"{_ID_PREFIX}{notes['customer_id']}"
    customer = entity.get("customer") or {}
    for v in (cid, entity.get("customer_email"), entity.get("email"),
              customer.get("email") if isinstance(customer, dict) else None,
              entity.get("contact")):
        if v:
            return f"{_ID_PREFIX}cust_{abs(hash(str(v))) % 1_000_000:06d}"
    return f"{_ID_PREFIX}cust_unknown"


def _failure_reason(entity: dict[str, Any]) -> str | None:
    error_obj = entity.get("error")
    if isinstance(error_obj, dict):
        nested_reason = (
            error_obj.get("reason")
            or error_obj.get("code")
            or error_obj.get("description")
        )
        if nested_reason:
            return nested_reason
    return (
        entity.get("error_reason")
        or entity.get("error_code")
        or entity.get("error_description")
        or None
    )


def razorpay_event_to_eventcreate(event: dict[str, Any]) -> EventCreate | None:
    """Map a Razorpay webhook event to an ``EventCreate``. ``None`` for events
    we don't ingest (success events, unknown types, zero amount)."""
    name = event.get("event", "")
    mapping = AT_RISK_EVENTS.get(name)
    if mapping is None:
        return None

    event_type, key = mapping
    entity = _entity(event, key)

    paise = entity.get("amount") or entity.get("amount_due") or 0
    try:
        amount = (Decimal(int(paise)) / 100).quantize(Decimal("0.01"))
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None

    raw_id = entity.get("id") or f"{name}_{event.get('created_at', '')}"
    reason = _failure_reason(entity) if event_type is EventType.FAILED_PAYMENT else None
    if event_type is EventType.EXPIRED_MANDATE and not reason:
        reason = "mandate_creation_failed"

    days_overdue = 0
    if event_type is EventType.OVERDUE_INVOICE:
        expire_by = entity.get("expire_by")
        if expire_by:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            diff = (now_ts - int(expire_by)) // 86400
            days_overdue = max(1, diff)
        else:
            days_overdue = 1  # invoice.expired signifies at least 1 day past expiration

    created_at_val = entity.get("created_at") or event.get("created_at")
    created_at_dt = None
    if created_at_val and isinstance(created_at_val, (int, float)):
        created_at_dt = datetime.fromtimestamp(created_at_val, tz=timezone.utc)

    return EventCreate(
        event_id=f"{_ID_PREFIX}{raw_id}",
        event_type=event_type,
        customer_id=_customer_id(entity),
        amount=amount,
        currency=(entity.get("currency") or "INR"),
        raw_failure_reason=reason,
        attempts_so_far=int(entity.get("attempts") or 0),
        days_overdue=days_overdue,
        created_at=created_at_dt,
    )


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Ingest one Razorpay test-mode webhook delivery.

    - 503 if no webhook secret is configured
    - 401 if the signature doesn't verify
    - 200 ``{"status": "accepted", ...}`` for an at-risk event now in the store
    - 200 ``{"status": "ignored", ...}`` for success / unknown / duplicate events
    """
    settings = get_settings()
    secret = settings.razorpay_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    body = await request.body()
    if not verify_signature(body, x_razorpay_signature, secret):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    event = await request.json()
    name = event.get("event", "")

    record = razorpay_event_to_eventcreate(event)
    if record is None:
        reason = "success event" if name in SUCCESS_EVENTS else f"not an at-risk event: {name!r}"
        return {"status": "ignored", "event": name, "reason": reason}

    with store.get_session() as session:
        if store.get_event(session, record.event_id) is not None:
            return {"status": "ignored", "event": name, "reason": "duplicate delivery",
                    "event_id": record.event_id}
        store.insert_event(session, record)
        store.log_action(
            session,
            event_id=record.event_id,
            agent=store.Agent.DETECTION,
            action="ingested_webhook_event",
            reasoning=(
                f"Ingested Razorpay test-mode webhook {name!r} for "
                f"{record.customer_id}: {record.event_type} of "
                f"{record.amount} {record.currency}. Enters the pipeline as "
                f"'detected'; the Detection Agent triages it on the next run."
            ),
            payload={"source": "razorpay_webhook", "event": name,
                     "amount_at_risk": str(record.amount)},
        )

    return {"status": "accepted", "event": name, "event_id": record.event_id,
            "event_type": record.event_type}
