"""End-to-end AI Revenue Recovery pipeline runner — build-order step 7.

Chains the four agents over the seeded batch:

    Detection -> Diagnosis -> Recovery -> Audit

and returns the full-batch ``MetricsBlock`` (AGENTS_CONTRACT.md §8 / plan.md §7).
Every event ends in a terminal status (``recovered`` | ``exception`` |
``flagged``); the fraud cluster is halted; metrics are computed over the whole
batch with the honest exception list.

CLI:
    uv run python -m app.pipeline                # run over the current DB
    uv run python -m app.pipeline --reset --count 70 --seed 42   # reseed first
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app import rag
from app.agents import audit, detection, diagnosis, recovery
from app.config import get_settings
from app.data import generate
from app.db import store


def run(
    database_url: str | None = None,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Run the four agents in order over whatever batch is currently seeded in
    the store, then return the Audit agent's ``MetricsBlock``.

    Assumes the batch has already been generated (call :func:`app.data.generate`
    or pass ``--reset`` on the CLI). Each agent commits its own writes through
    ``app.db.store``; nothing here touches Postgres directly.
    """
    if settings is None:
        settings = get_settings()

    with store.get_session(database_url) as session:
        detection.run(session, settings=settings)
        rag.seed_reference_cases(session, settings=settings)  # first run only
        diagnosis.run(session, settings=settings)
        recovery.run(session, settings=settings)
        audit.run(session, settings=settings)
        rag.index_resolved_cases(session, settings=settings)  # grow the KB
        return audit.compute_metrics(session)


def _format_summary(metrics: dict[str, Any]) -> str:
    lines = [
        "AI Revenue Recovery — batch run",
        "=" * 40,
        f"events in batch         : {metrics['event_count']}",
        f"total at risk           : Rs {metrics['total_at_risk']}",
        f"total recovered         : Rs {metrics['total_recovered']}",
        f"overall recovery rate   : {metrics['overall_recovery_rate']:.0%}",
        f"avg hours to recovery   : {metrics['avg_hours_to_recovery']}",
        "",
        "status breakdown:",
        *(
            f"  {name:<14} {count}"
            for name, count in metrics["status_breakdown"].items()
        ),
        "",
        "recovered by root cause:",
        *(
            f"  {row['root_cause']:<20} Rs {row['recovered']:>12} / "
            f"Rs {row['at_risk']:>12}  ({row['recovered_count']}/{row['count']}, "
            f"{row['recovery_rate']:.0%})"
            for row in metrics["by_root_cause"]
        ),
        "",
        "recovery rate by intervention:",
        *(
            f"  {row['intervention']:<28} {row['recovered_count']}/{row['count']} "
            f"({row['recovery_rate']:.0%})"
            for row in metrics["by_intervention"]
        ),
        "",
        f"fraud cluster halted    : {metrics['fraud_cluster']['flagged_event_ids']}",
        f"  {metrics['fraud_cluster']['reason']}",
        "",
        f"exception list ({len(metrics['exceptions'])} — honest, not cherry-picked):",
        *(
            f"  {row['event_id']:<10} {row['event_type']:<18} "
            f"Rs {row['amount']:>12}  {row['root_cause'] or '-':<20} {row['reason']}"
            for row in metrics["exceptions"]
        ),
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true",
        help="regenerate the synthetic batch (drops + reseeds the store) first",
    )
    parser.add_argument("--count", type=int, default=70)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--json", action="store_true", help="print the raw MetricsBlock as JSON"
    )
    args = parser.parse_args()

    if args.reset:
        generate.generate(count=args.count, seed=args.seed, reset=True)

    metrics = run()

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(_format_summary(metrics))


if __name__ == "__main__":
    main()
