"""``/pay/:token`` — the fake-gateway payment page's backend surface.

Not mounted here; team-lead mounts this router in ``app/main.py``
(``app.include_router(payment_router)``). This is the batch pipeline's real
payment surface, UI-fronted: ``GET`` returns display data, ``POST .../attempt``
is a genuine DB write via ``payment.resolve_fake_capture`` +
``payment.apply_capture`` — never a coin flip of its own.

The token **is** ``payment_link_id`` itself (AGENTS_CONTRACT.md §13 P2) — the
fake gateway's own ``fake_...`` id already doubles as the URL-safe token, so
no separate token table/column is needed. The attempt counter is likewise
derived, not stored: it is the count of prior ``payment_capture_failed`` audit
rows for the event (AGENTS_CONTRACT.md §11 / §13 attempt-count resolution).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.agents import payment
from app.config import get_settings
from app.db import store
from app.db.store import Event, PaymentLinkStatus

router = APIRouter(prefix="/api/pay", tags=["payment"])

MAX_ATTEMPTS = 3


def _find_by_token(session: store.Session, token: str) -> Event | None:
    """No dedicated index on ``payment_link_id`` -- the batch is small
    (~74 rows) so a linear scan via the existing ``all_events`` store call is
    fine and keeps persistence entirely inside ``store.py``."""
    for event in store.all_events(session):
        if event.payment_link_id == token:
            return event
    return None


def _mask_name(name: str | None, customer_id: str) -> str:
    base = (name or customer_id or "").strip()
    if len(base) <= 2:
        return (base[:1] + "*") if base else "***"
    return base[0] + "*" * (len(base) - 2) + base[-1]


def _failed_attempts(session: store.Session, event_id: str) -> int:
    return sum(
        1
        for row in store.get_audit_trail(session, event_id)
        if row.action == "payment_capture_failed"
    )


@router.get("/{token}")
def get_payment_page(token: str) -> dict[str, Any]:
    """Display data for the standalone PayCheckout page: masked customer
    name, amount, current link status. Never leaks the real customer_id."""
    with store.get_session() as session:
        event = _find_by_token(session, token)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such payment link: {token}")
        failed = _failed_attempts(session, event.event_id)
        return {
            "token": token,
            "event_id": event.event_id,
            "customer_name": _mask_name(event.customer_name, event.customer_id),
            "amount": str(Decimal(str(event.amount)).quantize(store.MONEY)),
            "currency": event.currency,
            "payment_link_status": event.payment_link_status,
            "attempts_made": failed,
            "attempts_remaining": max(0, MAX_ATTEMPTS - failed),
        }


@router.post("/{token}/attempt")
def attempt_payment(token: str):
    """Resolve one fake-gateway capture attempt against *token*.

    Success: ``apply_capture``'s own ``payment_captured`` log row is the
    only write; this route does not additionally log. Failure:
    ``apply_capture``'s ``payment_capture_failed`` row **is** the
    failed-attempt record. Bounded to ``MAX_ATTEMPTS`` (3) per token — the
    4th+ attempt is HTTP 409 (AGENTS_CONTRACT.md §13 P7), matching this
    codebase's existing "guard violation given current object state"
    convention (see the ticket routes' 409s).
    """
    settings = get_settings()
    with store.get_session() as session:
        event = _find_by_token(session, token)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no such payment link: {token}")

        if event.payment_link_status == PaymentLinkStatus.CAPTURED:
            return {"captured": True, "reason": "already_captured", "attempts_remaining": 0}

        failed = _failed_attempts(session, event.event_id)
        if failed >= MAX_ATTEMPTS:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error",
                    "reason": "max_attempts_exceeded",
                    "attempts_remaining": 0,
                },
            )

        capture = payment.resolve_fake_capture(
            event, token, settings=settings, attempt=failed + 1,
        )
        payment.apply_capture(session, event, capture, source="fake_gateway")
        remaining = max(0, MAX_ATTEMPTS - (failed + 1))
        return {
            "captured": capture["captured"],
            "reason": capture["reason"],
            "attempts_remaining": remaining,
        }
