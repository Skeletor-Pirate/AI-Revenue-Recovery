"""Diagnosis Agent — build-order step 4.

Second stage of the AI Revenue Recovery pipeline. Reads events the Detection
Agent left at ``status="detected"`` and, for each, decides a ``RootCause``:

1. **Fraud-cluster triage (first).** Scan every event in ``detected`` /
   ``diagnosed`` for a matching-signature cluster (AGENTS_CONTRACT.md §6). Every
   member is re-classified to ``status="flagged"``,
   ``root_cause="suspected_fraud"`` and gets a ``halted_fraud_cluster`` audit row
   (``agent="triage"``). Recovery never touches these.
2. **Rules classifier.** Deterministic map from the real Razorpay
   ``raw_failure_reason`` (falling back to ``event_type``) to a ``RootCause`` with
   an explainable confidence.
3. **Claude fallback.** Only when the rules confidence is ``<= 0.5`` *and*
   ``raw_failure_reason`` is non-null free text. Isolated behind
   :func:`claude_classify`, which is monkeypatched in tests and degrades to
   ``unknown`` @ 0.3 with no API key or on any error — it never raises.

Every non-fraud event ends ``diagnosed`` with ``root_cause`` +
``diagnosis_confidence`` and one ``classified_root_cause`` /
``llm_classified_root_cause`` audit row. ``reasoning`` is never empty.

Public API:
    run(session, *, settings=None) -> list[str]        # flagged + diagnosed ids, created_at order
    classify(event) -> tuple[RootCause, float, str | None, str]   # pure
    find_fraud_clusters(events) -> list[dict]          # pure
    claude_classify(event, settings=None) -> tuple[RootCause, float, str, bool]
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.config import get_settings
from app.db import store
from app.db.store import MONEY, Agent, EventStatus, RootCause

# --- rules map: real Razorpay failure code -> (RootCause, rules confidence) ----
_REASON_MAP: dict[str, tuple[RootCause, float]] = {
    "insufficient_fund": (RootCause.INSUFFICIENT_FUNDS, 0.95),
    "card_expired": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "card_number_invalid": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "mandate_creation_expired": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "mandate_creation_failed": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "bank_not_available": (RootCause.BANK_DOWNTIME, 0.9),
    "bank_technical_error": (RootCause.BANK_DOWNTIME, 0.9),
    "gateway_technical_error": (RootCause.BANK_DOWNTIME, 0.9),
    "authentication_failed": (RootCause.AUTH_FAILURE, 0.9),
    "payment_timed_out": (RootCause.AUTH_FAILURE, 0.9),
    "incorrect_otp": (RootCause.AUTH_FAILURE, 0.9),
    "invalid_otp": (RootCause.AUTH_FAILURE, 0.9),
    "card_declined": (RootCause.CARD_DECLINED, 0.8),
    "card_disabled_for_online_payments": (RootCause.CARD_DECLINED, 0.8),
    "payment_cancelled": (RootCause.CHECKOUT_ABANDONED, 0.7),
}

# fallback when there is no gateway reason at all
_TYPE_MAP: dict[str, tuple[RootCause, float]] = {
    "abandoned_checkout": (RootCause.CHECKOUT_ABANDONED, 0.85),
    "overdue_invoice": (RootCause.INVOICE_FORGOTTEN, 0.85),
    "expired_mandate": (RootCause.EXPIRED_INSTRUMENT, 0.9),
}

_LOW_CONFIDENCE = 0.5
_UNKNOWN_CONFIDENCE = 0.4

# fraud-cluster signature thresholds (AGENTS_CONTRACT.md §6)
_FRAUD_AMOUNT_BAND = Decimal("50")
_FRAUD_WINDOW = timedelta(minutes=60)
_FRAUD_MIN_ATTEMPTS = 2
_FRAUD_MIN_EVENTS = 3
_FRAUD_MIN_CUSTOMERS = 3
_CLUSTER_STATUSES = {EventStatus.DETECTED.value, EventStatus.DIAGNOSED.value}

DIAGNOSIS_ACTIONS = {
    "classified_root_cause",
    "llm_classified_root_cause",
    "halted_fraud_cluster",
}

_SYSTEM_PROMPT = (
    "You are a payments revenue-recovery analyst. Classify a single failed "
    "payment / abandoned checkout / overdue invoice into exactly one root "
    "cause from this set: insufficient_funds, expired_instrument, "
    "bank_downtime, auth_failure, card_declined, checkout_abandoned, "
    "invoice_forgotten, unknown. Never return suspected_fraud. Respond with "
    'ONLY compact JSON: {"root_cause": <member>, "confidence": <0..1>, '
    '"reasoning": <short string>}.'
)


# --- helpers ----------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def classify(event: Any) -> tuple[RootCause, float, str | None, str]:
    """Rules-first root-cause classification. Pure, deterministic, no I/O.

    Returns ``(root_cause, confidence, matched_reason, reasoning)``.
    ``matched_reason`` is the ``raw_failure_reason`` string when a reason was
    present (matched or not), else ``None``. ``reasoning`` is never empty.
    """
    reason = (event.raw_failure_reason or "").strip()
    etype = str(event.event_type)
    key = reason.lower()

    if key in _REASON_MAP:
        rc, conf = _REASON_MAP[key]
        return (
            rc,
            conf,
            reason,
            f"Razorpay gateway reason {reason!r} maps to {rc.value} "
            f"via the rules classifier (confidence {conf}).",
        )

    if not reason:
        if etype in _TYPE_MAP:
            rc, conf = _TYPE_MAP[etype]
            return (
                rc,
                conf,
                None,
                f"No gateway failure reason; event type {etype} maps to "
                f"{rc.value} via the rules classifier (confidence {conf}).",
            )
        return (
            RootCause.UNKNOWN,
            _UNKNOWN_CONFIDENCE,
            None,
            f"No gateway failure reason and event type {etype} has no rule; "
            f"left unknown for human review.",
        )

    return (
        RootCause.UNKNOWN,
        _UNKNOWN_CONFIDENCE,
        reason,
        f"Gateway reason {reason!r} matched no rule; low confidence, "
        f"eligible for the Claude fallback classifier.",
    )


def _signature(members: list[Any], reason: str) -> dict[str, Any]:
    amounts = sorted(_dec(e.amount).quantize(MONEY) for e in members)
    times = [_aware(e.created_at) for e in members]
    window_minutes = int(round((max(times) - min(times)).total_seconds() / 60))
    return {
        "members": members,
        "signature": {
            "raw_failure_reason": reason,
            "amount_band": [str(amounts[0]), str(amounts[-1])],
            "customer_count": len({e.customer_id for e in members}),
            "window_minutes": window_minutes,
        },
    }


def find_fraud_clusters(events: list[Any]) -> list[dict[str, Any]]:
    """Find matching-signature fraud clusters among *events*. Pure, no I/O.

    Each result is ``{"members": [Event, ...], "signature": {...}}`` where the
    signature dict matches the ``halted_fraud_cluster`` payload spec.
    """
    pool: dict[str, list[Any]] = {}
    for e in events:
        if str(e.status) not in _CLUSTER_STATUSES:
            continue
        if not (e.raw_failure_reason or "").strip():
            continue
        if (e.attempts_so_far or 0) < _FRAUD_MIN_ATTEMPTS:
            continue
        pool.setdefault(e.raw_failure_reason, []).append(e)

    clusters: list[dict[str, Any]] = []
    for reason, group in pool.items():
        group = sorted(group, key=lambda e: _aware(e.created_at))
        n = len(group)
        match: list[Any] | None = None
        # prefer the largest contiguous-by-time window that satisfies every rule
        for i in range(n):
            for j in range(n, i + _FRAUD_MIN_EVENTS - 1, -1):
                window = group[i:j]
                if len(window) < _FRAUD_MIN_EVENTS:
                    continue
                amounts = [_dec(e.amount) for e in window]
                if max(amounts) - min(amounts) > _FRAUD_AMOUNT_BAND:
                    continue
                times = [_aware(e.created_at) for e in window]
                if max(times) - min(times) > _FRAUD_WINDOW:
                    continue
                if len({e.customer_id for e in window}) < _FRAUD_MIN_CUSTOMERS:
                    continue
                match = window
                break
            if match is not None:
                break
        if match is not None:
            clusters.append(_signature(match, reason))
    return clusters


# --- Claude fallback (isolated; monkeypatched in tests) --------------------

def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in model response")
    return text[start : end + 1]


def _coerce_root_cause(value: Any) -> RootCause:
    try:
        rc = RootCause(str(value))
    except ValueError:
        return RootCause.UNKNOWN
    if rc is RootCause.SUSPECTED_FRAUD:
        return RootCause.UNKNOWN
    return rc


def claude_classify(
    event: Any, settings: Any = None
) -> tuple[RootCause, float, str, bool]:
    """Classify a low-confidence free-text failure via Claude.

    Returns ``(root_cause, confidence, reasoning, used_fallback)``. Never raises.
    No API key or any exception → ``(UNKNOWN, 0.3, <reason>, True)``. Any
    non-enum / ``suspected_fraud`` answer from Claude is coerced to ``unknown``.
    """
    try:
        if settings is None:
            settings = get_settings()
        api_key = getattr(settings, "anthropic_api_key", None)
        model = getattr(settings, "anthropic_model", "claude-sonnet-5")
        if not api_key:
            return (
                RootCause.UNKNOWN,
                0.3,
                "No Anthropic API key configured; degraded to a best-effort "
                "'unknown' classification at low confidence.",
                True,
            )

        import anthropic  # lazy: tests never reach here

        client = anthropic.Anthropic(api_key=api_key)
        user = (
            f"event_type: {event.event_type}\n"
            f"raw_failure_reason: {event.raw_failure_reason}\n"
            f"amount: {event.amount}\n"
            f"attempts_so_far: {event.attempts_so_far}\n"
            f"days_overdue: {getattr(event, 'days_overdue', 0)}"
        )
        message = client.messages.create(
            model=model,
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        data = json.loads(_extract_json(text))
        rc = _coerce_root_cause(data.get("root_cause"))
        try:
            conf = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        reasoning = str(data.get("reasoning") or "").strip() or (
            f"Claude classified this event as {rc.value}."
        )
        return (rc, conf, reasoning, False)
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return (
            RootCause.UNKNOWN,
            0.3,
            f"Claude fallback failed ({exc.__class__.__name__}); degraded to "
            f"'unknown' at low confidence.",
            True,
        )


# --- entrypoint -----------------------------------------------------------

def _fraud_reasoning(event: Any, sig: dict[str, Any]) -> str:
    return (
        f"Event {event.event_id} matches a fraud-signature cluster: "
        f"reason {sig['raw_failure_reason']!r}, {sig['customer_count']} distinct "
        f"customers in a ~{sig['window_minutes']}-minute window within a "
        f"{sig['amount_band'][0]}–{sig['amount_band'][1]} amount band, each "
        f"already retried hard. Halting the whole cluster for human review "
        f"rather than attempting recovery."
    )


def run(session: store.Session, *, settings: Any = None) -> list[str]:
    """Diagnose every event at ``status="detected"``.

    Fraud-cluster triage runs first (over all events in detected/diagnosed),
    then the rules classifier, then the Claude fallback for low-confidence
    free-text reasons. Returns the flagged + diagnosed event ids in
    ``created_at`` order. Idempotent: a second run finds nothing to do.
    """
    if settings is None:
        settings = get_settings()

    ordered = store.all_events(session)
    order_index = {e.event_id: i for i, e in enumerate(ordered)}

    flagged: set[str] = set()
    diagnosed: set[str] = set()

    # 1. fraud-cluster triage
    for cluster in find_fraud_clusters(ordered):
        sig = cluster["signature"]
        member_ids = sorted(
            (e.event_id for e in cluster["members"]),
            key=lambda eid: order_index.get(eid, 0),
        )
        for member in cluster["members"]:
            if str(member.status) not in _CLUSTER_STATUSES:
                continue
            store.update_event(
                session,
                member.event_id,
                status=EventStatus.FLAGGED,
                root_cause=RootCause.SUSPECTED_FRAUD,
            )
            store.log_action(
                session,
                event_id=member.event_id,
                agent=Agent.TRIAGE,
                action="halted_fraud_cluster",
                reasoning=_fraud_reasoning(member, sig),
                payload={"signature": sig, "cluster_event_ids": member_ids},
            )
            flagged.add(member.event_id)

    # 2. per-event rules + 3. Claude fallback
    for event in store.get_events_by_status(session, EventStatus.DETECTED.value):
        if event.event_id in flagged:
            continue

        rc, conf, matched_reason, reasoning = classify(event)
        used_llm = False
        used_fallback = False

        has_free_text = bool((event.raw_failure_reason or "").strip())
        if conf <= _LOW_CONFIDENCE and has_free_text:
            rc, conf, reasoning, used_fallback = claude_classify(event, settings)
            used_llm = True

        conf = max(0.0, min(1.0, float(conf)))

        store.update_event(
            session,
            event.event_id,
            status=EventStatus.DIAGNOSED,
            root_cause=rc,
            diagnosis_confidence=conf,
        )

        if used_llm:
            store.log_action(
                session,
                event_id=event.event_id,
                agent=Agent.DIAGNOSIS,
                action="llm_classified_root_cause",
                reasoning=(
                    f"Rules classifier was not confident enough for "
                    f"{event.event_id}; Claude assigned {rc.value} at "
                    f"confidence {conf}. {reasoning}"
                ),
                payload={
                    "root_cause": rc.value,
                    "confidence": conf,
                    "model": getattr(settings, "anthropic_model", "claude-sonnet-5"),
                    "used_fallback": used_fallback,
                },
            )
        else:
            store.log_action(
                session,
                event_id=event.event_id,
                agent=Agent.DIAGNOSIS,
                action="classified_root_cause",
                reasoning=reasoning,
                payload={
                    "root_cause": rc.value,
                    "confidence": conf,
                    "matched_reason": matched_reason,
                },
            )
        diagnosed.add(event.event_id)

    touched = flagged | diagnosed
    return [e.event_id for e in ordered if e.event_id in touched]
