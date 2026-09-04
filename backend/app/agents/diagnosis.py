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

from app import llm, rag
from app.config import get_settings
from app.db import store
from app.db.store import MONEY, Agent, EventStatus, RootCause

# --- rules map: real Razorpay failure code -> (RootCause, rules confidence) ----
_REASON_MAP: dict[str, tuple[RootCause, float]] = {
    "insufficient_fund": (RootCause.INSUFFICIENT_FUNDS, 0.95),
    "insufficient_funds": (RootCause.INSUFFICIENT_FUNDS, 0.95),
    "card_expired": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "card_inactive": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "card_number_invalid": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "mandate_creation_expired": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "mandate_creation_failed": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "mandate_cancelled": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "subscription_halted": (RootCause.EXPIRED_INSTRUMENT, 0.9),
    "bank_not_available": (RootCause.BANK_DOWNTIME, 0.9),
    "bank_technical_error": (RootCause.BANK_DOWNTIME, 0.9),
    "bank_server_error": (RootCause.BANK_DOWNTIME, 0.9),
    "gateway_technical_error": (RootCause.BANK_DOWNTIME, 0.9),
    "gateway_error": (RootCause.BANK_DOWNTIME, 0.9),
    "authentication_failed": (RootCause.AUTH_FAILURE, 0.9),
    "payment_timed_out": (RootCause.AUTH_FAILURE, 0.9),
    "incorrect_otp": (RootCause.AUTH_FAILURE, 0.9),
    "invalid_otp": (RootCause.AUTH_FAILURE, 0.9),
    "upi_pin_invalid": (RootCause.AUTH_FAILURE, 0.9),
    "upi_timed_out": (RootCause.AUTH_FAILURE, 0.9),
    "card_declined": (RootCause.CARD_DECLINED, 0.8),
    "card_limit_exceeded": (RootCause.CARD_DECLINED, 0.8),
    "card_disabled_for_online_payments": (RootCause.CARD_DECLINED, 0.8),
    "payment_cancelled": (RootCause.CHECKOUT_ABANDONED, 0.7),
}

# fallback when there is no gateway reason at all
# In simple layman terms, a Gateway Reason is the official error message sent 
# back by the Payment Gateway (Razorpay) or Bank explaining why a transaction failed.
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

# Multi-signal velocity and risk thresholds
_VELOCITY_WINDOW = timedelta(minutes=15)
_VELOCITY_MIN_ATTEMPTS = 5

_PROBE_MAX_AMOUNT = Decimal("10.00")
_PROBE_WINDOW = timedelta(minutes=30)
_PROBE_MIN_ATTEMPTS = 3

_CARD_HOP_WINDOW = timedelta(minutes=30)
_CARD_HOP_MIN_ATTEMPTS = 3
_CARD_DECLINE_REASONS = {
    "card_declined",
    "card_number_invalid",
    "card_limit_exceeded",
    "card_disabled_for_online_payments",
}

DIAGNOSIS_ACTIONS = {
    "classified_root_cause",
    "llm_classified_root_cause",
    "halted_fraud_cluster",
    "halted_velocity_flood",
    "halted_card_probe",
    "halted_card_hopping",
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


def find_velocity_floods(events: list[Any]) -> list[dict[str, Any]]:
    """Detect rapid brute-force transaction flooding from a single customer ID.

    Identifies customers attempting >= _VELOCITY_MIN_ATTEMPTS failed transactions
    within a rolling _VELOCITY_WINDOW (15 minutes).
    """
    by_cust: dict[str, list[Any]] = {}
    for e in events:
        if str(e.status) not in _CLUSTER_STATUSES:
            continue
        if not e.customer_id or e.customer_id.endswith("unknown"):
            continue
        by_cust.setdefault(e.customer_id, []).append(e)

    results: list[dict[str, Any]] = []
    for cid, group in by_cust.items():
        if len(group) < _VELOCITY_MIN_ATTEMPTS:
            continue
        group = sorted(group, key=lambda e: _aware(e.created_at))
        n = len(group)
        match: list[Any] | None = None
        for i in range(n):
            for j in range(n, i + _VELOCITY_MIN_ATTEMPTS - 1, -1):
                window = group[i:j]
                if len(window) < _VELOCITY_MIN_ATTEMPTS:
                    continue
                times = [_aware(e.created_at) for e in window]
                if max(times) - min(times) <= _VELOCITY_WINDOW:
                    match = window
                    break
            if match is not None:
                break
        if match is not None:
            times = [_aware(e.created_at) for e in match]
            window_mins = int(round((max(times) - min(times)).total_seconds() / 60))
            results.append({
                "members": match,
                "signature": {
                    "customer_id": cid,
                    "attempt_count": len(match),
                    "window_minutes": window_mins,
                    "risk_type": "velocity_flood",
                },
            })
    return results


def find_probing_attacks(events: list[Any]) -> list[dict[str, Any]]:
    """Detect card-testing micro-transaction probes (<= Rs 10.00).

    Identifies customers testing >= _PROBE_MIN_ATTEMPTS small amounts
    within a rolling _PROBE_WINDOW (30 minutes).
    """
    by_cust: dict[str, list[Any]] = {}
    for e in events:
        if str(e.status) not in _CLUSTER_STATUSES:
            continue
        if _dec(e.amount) > _PROBE_MAX_AMOUNT:
            continue
        if not e.customer_id or e.customer_id.endswith("unknown"):
            continue
        by_cust.setdefault(e.customer_id, []).append(e)

    results: list[dict[str, Any]] = []
    for cid, group in by_cust.items():
        if len(group) < _PROBE_MIN_ATTEMPTS:
            continue
        group = sorted(group, key=lambda e: _aware(e.created_at))
        n = len(group)
        match: list[Any] | None = None
        for i in range(n):
            for j in range(n, i + _PROBE_MIN_ATTEMPTS - 1, -1):
                window = group[i:j]
                if len(window) < _PROBE_MIN_ATTEMPTS:
                    continue
                times = [_aware(e.created_at) for e in window]
                if max(times) - min(times) <= _PROBE_WINDOW:
                    match = window
                    break
            if match is not None:
                break
        if match is not None:
            amounts = [_dec(e.amount).quantize(MONEY) for e in match]
            times = [_aware(e.created_at) for e in match]
            window_mins = int(round((max(times) - min(times)).total_seconds() / 60))
            results.append({
                "members": match,
                "signature": {
                    "customer_id": cid,
                    "attempt_count": len(match),
                    "max_amount": str(max(amounts)),
                    "window_minutes": window_mins,
                    "risk_type": "micro_transaction_probe",
                },
            })
    return results


def find_card_hopping(events: list[Any]) -> list[dict[str, Any]]:
    """Detect attackers cycling through stolen cards (repeated card declines).

    Identifies customers failing >= _CARD_HOP_MIN_ATTEMPTS times with card decline
    reasons within a rolling _CARD_HOP_WINDOW (30 minutes).
    """
    by_cust: dict[str, list[Any]] = {}
    for e in events:
        if str(e.status) not in _CLUSTER_STATUSES:
            continue
        reason = (e.raw_failure_reason or "").lower().strip()
        if reason not in _CARD_DECLINE_REASONS:
            continue
        if not e.customer_id or e.customer_id.endswith("unknown"):
            continue
        by_cust.setdefault(e.customer_id, []).append(e)

    results: list[dict[str, Any]] = []
    for cid, group in by_cust.items():
        if len(group) < _CARD_HOP_MIN_ATTEMPTS:
            continue
        group = sorted(group, key=lambda e: _aware(e.created_at))
        n = len(group)
        match: list[Any] | None = None
        for i in range(n):
            for j in range(n, i + _CARD_HOP_MIN_ATTEMPTS - 1, -1):
                window = group[i:j]
                if len(window) < _CARD_HOP_MIN_ATTEMPTS:
                    continue
                times = [_aware(e.created_at) for e in window]
                if max(times) - min(times) <= _CARD_HOP_WINDOW:
                    match = window
                    break
            if match is not None:
                break
        if match is not None:
            times = [_aware(e.created_at) for e in match]
            reasons = [e.raw_failure_reason for e in match]
            window_mins = int(round((max(times) - min(times)).total_seconds() / 60))
            results.append({
                "members": match,
                "signature": {
                    "customer_id": cid,
                    "attempt_count": len(match),
                    "reasons": sorted(list(set(reasons))),
                    "window_minutes": window_mins,
                    "risk_type": "card_hopping",
                },
            })
    return results


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
    event: Any, settings: Any = None, rag_context: str = ""
) -> tuple[RootCause, float, str, bool]:
    """Classify a low-confidence free-text failure via the configured LLM.

    Provider-agnostic (see ``app.llm``): Anthropic, OpenRouter or OpenAI.
    ``rag_context`` (optional) is a few-shot block of similar past cases
    retrieved from the knowledge base (``app.rag``) — included in the prompt
    when present. Returns ``(root_cause, confidence, reasoning, used_fallback)``.
    Never raises. No provider or any exception → ``(UNKNOWN, 0.3, <reason>,
    True)``. Any non-enum / ``suspected_fraud`` answer is coerced to ``unknown``.
    """
    try:
        if settings is None:
            settings = get_settings()
        if not llm.available(settings):
            return (
                RootCause.UNKNOWN,
                0.3,
                "No LLM provider configured (set ANTHROPIC_API_KEY, "
                "OPENROUTER_API_KEY or OPENAI_API_KEY); degraded to a best-effort "
                "'unknown' classification at low confidence.",
                True,
            )

        user = (
            f"event_type: {event.event_type}\n"
            f"raw_failure_reason: {event.raw_failure_reason}\n"
            f"amount: {event.amount}\n"
            f"attempts_so_far: {event.attempts_so_far}\n"
            f"days_overdue: {getattr(event, 'days_overdue', 0)}"
        )
        if rag_context:
            user = f"{rag_context}\n\nNow classify this case:\n{user}"
        text = llm.chat(_SYSTEM_PROMPT, user, settings=settings, max_tokens=300)
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


def _velocity_reasoning(event: Any, sig: dict[str, Any]) -> str:
    return (
        f"Event {event.event_id} flagged for velocity flooding: customer "
        f"{sig['customer_id']} initiated {sig['attempt_count']} failed transactions "
        f"in ~{sig['window_minutes']} minutes. Halting to prevent brute-force abuse."
    )


def _probe_reasoning(event: Any, sig: dict[str, Any]) -> str:
    return (
        f"Event {event.event_id} flagged for card-testing probe: customer "
        f"{sig['customer_id']} initiated {sig['attempt_count']} micro-transactions "
        f"(max {sig['max_amount']} INR) in ~{sig['window_minutes']} minutes. Halting."
    )


def _hopping_reasoning(event: Any, sig: dict[str, Any]) -> str:
    return (
        f"Event {event.event_id} flagged for card-hopping sweep: customer "
        f"{sig['customer_id']} accumulated {sig['attempt_count']} consecutive card "
        f"declines in ~{sig['window_minutes']} minutes. Halting."
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

    # 1. Multi-signal fraud triage
    # 1a. Multi-customer temporal cluster
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

    # 1b. Single-customer velocity floods
    for cluster in find_velocity_floods(ordered):
        sig = cluster["signature"]
        member_ids = sorted(
            (e.event_id for e in cluster["members"]),
            key=lambda eid: order_index.get(eid, 0),
        )
        for member in cluster["members"]:
            if member.event_id in flagged or str(member.status) not in _CLUSTER_STATUSES:
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
                action="halted_velocity_flood",
                reasoning=_velocity_reasoning(member, sig),
                payload={"signature": sig, "cluster_event_ids": member_ids},
            )
            flagged.add(member.event_id)

    # 1c. Micro-transaction probing attacks
    for cluster in find_probing_attacks(ordered):
        sig = cluster["signature"]
        member_ids = sorted(
            (e.event_id for e in cluster["members"]),
            key=lambda eid: order_index.get(eid, 0),
        )
        for member in cluster["members"]:
            if member.event_id in flagged or str(member.status) not in _CLUSTER_STATUSES:
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
                action="halted_card_probe",
                reasoning=_probe_reasoning(member, sig),
                payload={"signature": sig, "cluster_event_ids": member_ids},
            )
            flagged.add(member.event_id)

    # 1d. Multi-card identity hopping
    for cluster in find_card_hopping(ordered):
        sig = cluster["signature"]
        member_ids = sorted(
            (e.event_id for e in cluster["members"]),
            key=lambda eid: order_index.get(eid, 0),
        )
        for member in cluster["members"]:
            if member.event_id in flagged or str(member.status) not in _CLUSTER_STATUSES:
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
                action="halted_card_hopping",
                reasoning=_hopping_reasoning(member, sig),
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
        similar: list[dict[str, Any]] = []
        if conf <= _LOW_CONFIDENCE and has_free_text:
            similar = rag.retrieve_similar(session, event, settings=settings)
            rag_context = rag.format_for_prompt(similar) if similar else ""
            rc, conf, reasoning, used_fallback = claude_classify(
                event, settings, rag_context
            )
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
                    "model": llm.model_label(settings),
                    "used_fallback": used_fallback,
                    "rag_examples": len(similar),
                    "similar_case_ids": [c["event_id"] for c in similar],
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
