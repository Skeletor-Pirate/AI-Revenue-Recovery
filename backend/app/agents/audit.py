"""Audit / Reporting Agent — final stage of the AI Revenue Recovery pipeline.

Pure read + aggregate. This agent writes **no event rows**: it reads the whole
finished batch through ``app.db.store`` and computes the honest, full-batch
``MetricsBlock`` (AGENTS_CONTRACT.md §8) — exceptions included, never
cherry-picked.

Two entry points (AGENTS_CONTRACT.md §9):

* ``compute_metrics(session) -> dict`` — the ``MetricsBlock``. Pure read; this
  is what ``pipeline.py`` and the API return. Deterministic, safe to repeat.
* ``run(session, *, settings=None) -> list[str]`` — calls ``compute_metrics``,
  appends exactly one ``batch_metrics`` audit row (``agent="audit"``,
  ``event_id`` = the earliest event in the batch), and returns
  ``[sentinel_event_id]``. An empty batch writes nothing and returns ``[]``.

Money fields are quantised decimal strings; rates are floats in ``[0, 1]``
rounded to two places.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.db import store
from app.db.store import Agent, EventStatus, MONEY, RootCause

_ZERO = Decimal("0.00")

DEFAULT_FRAUD_REASON = "matching-signature cluster halted for human review"
NO_REASON_RECORDED = "not recovered; no reason recorded"


def _money(value: Decimal) -> str:
    return str(Decimal(value).quantize(MONEY))


def _rate(numerator: float, denominator: float) -> float:
    """Count- or money-based rate as a float in [0, 1], rounded to 2 dp.

    Guards division by zero (returns ``0.0``)."""
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 2)


def _empty_block() -> dict[str, Any]:
    return {
        "total_at_risk": "0.00",
        "total_recovered": "0.00",
        "overall_recovery_rate": 0.0,
        "event_count": 0,
        "by_root_cause": [],
        "by_intervention": [],
        "avg_hours_to_recovery": 0.0,
        "status_breakdown": {s.value: 0 for s in EventStatus},
        "exceptions": [],
        "fraud_cluster": {"flagged_event_ids": [], "reason": DEFAULT_FRAUD_REASON},
    }


def _exception_reason(rows: list[Any]) -> str:
    """Pull the stated non-recovery reason for one event from its audit trail.

    Priority: an explicit ``routed_to_exception`` reason, else a synthesised
    line from ``halted_stopping_rule``, else an honest placeholder.
    """
    for row in reversed(rows):
        if row.action == "routed_to_exception" and row.payload:
            reason = row.payload.get("reason")
            if reason:
                return str(reason)
    for row in reversed(rows):
        if row.action == "halted_stopping_rule" and row.payload:
            rule = row.payload.get("rule")
            if rule:
                attempts = row.payload.get("attempts_so_far")
                if attempts is not None:
                    return (
                        f"halted by stopping rule {rule!r} after {attempts} "
                        f"attempt(s); not recovered"
                    )
                return f"halted by stopping rule {rule!r}; not recovered"
    for row in reversed(rows):
        if row.action == "awaiting_human_approval" and row.payload:
            proposed = row.payload.get("proposed_action", "an aggressive action")
            threshold = row.payload.get("threshold")
            return (
                f"{proposed} needs human sign-off (amount above Rs {threshold}); "
                f"flagged for review and not executed"
            )
    return NO_REASON_RECORDED


def compute_metrics(session: store.Session) -> dict[str, Any]:
    """Compute the full-batch ``MetricsBlock`` over every event — recovered,
    exception and flagged alike. Pure read; writes nothing."""
    events = store.all_events(session)
    if not events:
        return _empty_block()

    trail = store.get_audit_trail(session)
    trail_by_event: dict[str, list[Any]] = {}
    for row in trail:
        trail_by_event.setdefault(row.event_id, []).append(row)

    event_by_id = {e.event_id: e for e in events}

    total_at_risk = sum((e.amount for e in events), _ZERO)
    total_recovered = sum((e.recovered_amount for e in events), _ZERO)

    # --- status breakdown (all six keys, 0-filled) ---
    status_breakdown = {s.value: 0 for s in EventStatus}
    for e in events:
        status_breakdown[str(e.status)] = status_breakdown.get(str(e.status), 0) + 1

    def _recovered(evs: list[Any]) -> list[Any]:
        return [e for e in evs if str(e.status) == EventStatus.RECOVERED.value]

    # --- by root cause (enum order, includes suspected_fraud / unknown) ---
    rc_order = {rc.value: i for i, rc in enumerate(RootCause)}
    rc_groups: dict[str, list[Any]] = {}
    for e in events:
        if e.root_cause is None:
            continue
        rc_groups.setdefault(str(e.root_cause), []).append(e)

    by_root_cause = []
    for rc in sorted(rc_groups, key=lambda k: rc_order.get(k, len(rc_order))):
        evs = rc_groups[rc]
        rec = _recovered(evs)
        by_root_cause.append(
            {
                "root_cause": rc,
                "at_risk": _money(sum((e.amount for e in evs), _ZERO)),
                "recovered": _money(sum((e.recovered_amount for e in evs), _ZERO)),
                "count": len(evs),
                "recovered_count": len(rec),
                "recovery_rate": _rate(len(rec), len(evs)),
            }
        )

    # --- by intervention (from each event's intervention_selected payload) ---
    intervention_of: dict[str, str] = {}
    intervention_order: list[str] = []
    for row in trail:
        if row.action == "intervention_selected" and row.payload:
            iv = row.payload.get("intervention")
            if iv is None:
                continue
            intervention_of[row.event_id] = iv
            if iv not in intervention_order:
                intervention_order.append(iv)

    iv_groups: dict[str, list[Any]] = {}
    for event_id, iv in intervention_of.items():
        e = event_by_id.get(event_id)
        if e is not None:
            iv_groups.setdefault(iv, []).append(e)

    by_intervention = []
    for iv in intervention_order:
        evs = iv_groups.get(iv, [])
        if not evs:
            continue
        rec = _recovered(evs)
        by_intervention.append(
            {
                "intervention": iv,
                "count": len(evs),
                "recovered_count": len(rec),
                "recovery_rate": _rate(len(rec), len(evs)),
                "at_risk": _money(sum((e.amount for e in evs), _ZERO)),
                "recovered": _money(sum((e.recovered_amount for e in evs), _ZERO)),
            }
        )

    # --- average simulated time-to-recovery ---
    hours = [
        row.payload["simulated_hours_to_recovery"]
        for row in trail
        if row.action == "marked_recovered"
        and row.payload
        and "simulated_hours_to_recovery" in row.payload
    ]
    avg_hours = round(sum(hours) / len(hours), 2) if hours else 0.0

    # --- exception list (complete, created_at order, never truncated) ---
    exceptions = [
        {
            "event_id": e.event_id,
            "event_type": str(e.event_type),
            "amount": _money(e.amount),
            "root_cause": str(e.root_cause) if e.root_cause is not None else None,
            "reason": _exception_reason(trail_by_event.get(e.event_id, [])),
        }
        for e in events
        if str(e.status) == EventStatus.EXCEPTION.value
    ]

    # --- fraud cluster ---
    flagged_ids = [
        e.event_id for e in events if str(e.status) == EventStatus.FLAGGED.value
    ]
    fraud_reason = DEFAULT_FRAUD_REASON
    for row in trail:
        if row.action == "halted_fraud_cluster" and row.reasoning:
            fraud_reason = row.reasoning
            break

    return {
        "total_at_risk": _money(total_at_risk),
        "total_recovered": _money(total_recovered),
        "overall_recovery_rate": _rate(total_recovered, total_at_risk),
        "event_count": len(events),
        "by_root_cause": by_root_cause,
        "by_intervention": by_intervention,
        "avg_hours_to_recovery": avg_hours,
        "status_breakdown": status_breakdown,
        "exceptions": exceptions,
        "fraud_cluster": {"flagged_event_ids": flagged_ids, "reason": fraud_reason},
    }


def run(session: store.Session, *, settings: Any = None) -> list[str]:
    """Compute the batch metrics and append one ``batch_metrics`` audit row.

    Returns ``[sentinel_event_id]`` (the earliest event, whose FK anchors the
    row). An empty batch writes nothing and returns ``[]``.
    """
    events = store.all_events(session)
    metrics = compute_metrics(session)
    if not events:
        return []

    sentinel = events[0].event_id
    flagged = len(metrics["fraud_cluster"]["flagged_event_ids"])
    store.log_action(
        session,
        event_id=sentinel,
        agent=Agent.AUDIT,
        action="batch_metrics",
        reasoning=(
            f"End-of-run metrics over all {metrics['event_count']} events: "
            f"Rs {metrics['total_recovered']} recovered of "
            f"Rs {metrics['total_at_risk']} at risk "
            f"(overall {metrics['overall_recovery_rate']}); "
            f"{len(metrics['exceptions'])} exception(s), "
            f"{flagged} flagged for fraud review."
        ),
        payload=metrics,
    )
    return [sentinel]
