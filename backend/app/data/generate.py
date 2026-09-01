"""Synthetic at-risk-revenue batch generator (CLAUDE.md Section 7).

Produces a deterministic batch of 50-100 events across all four EventTypes,
seeded into Postgres through the validated `store.insert_event` path, plus a
deliberate small fraud-like cluster (CLAUDE.md Section 6) that the Diagnosis
Agent's triage check is meant to catch.

Failure reasons are real Razorpay error codes (razorpay.com/docs/errors), not
invented ones.

CLI:
    uv run python -m app.data.generate --count 70 --seed 42 --reset
"""

from __future__ import annotations

import argparse
import random
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pandas as pd
from faker import Faker

from app.db import store
from app.db.store import EventCreate, EventType

# --- real Razorpay failure codes, per event type -------------------------
# https://razorpay.com/docs/errors/payments/list/
RAZORPAY_FAILURE_REASONS: dict[EventType, list[str | None]] = {
    EventType.FAILED_PAYMENT: [
        "insufficient_funds",       # customer -- wait + retry near a salary window
        "card_expired",             # customer -- needs a fresh instrument
        "incorrect_otp",            # customer -- guided retry
        "card_declined",            # gateway  -- try an alternate method
        "bank_not_available",       # gateway  -- retry after a delay
        "bank_technical_error",     # gateway  -- retry after a delay
        "gateway_technical_error",  # gateway  -- retry after a delay
    ],
    EventType.EXPIRED_MANDATE: [
        "mandate_creation_expired",
        "mandate_creation_failed",
    ],
    EventType.ABANDONED_CHECKOUT: [None],   # dropped off mid-flow, no gateway error
    EventType.OVERDUE_INVOICE: [None],      # nothing failed; the bill is just late
}

# rough mix of the batch (must sum to 1.0)
_TYPE_WEIGHTS: dict[EventType, float] = {
    EventType.FAILED_PAYMENT: 0.45,
    EventType.ABANDONED_CHECKOUT: 0.20,
    EventType.OVERDUE_INVOICE: 0.20,
    EventType.EXPIRED_MANDATE: 0.15,
}

_MIN_AMOUNT = Decimal("200")
_MAX_AMOUNT = Decimal("50000")

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "synthetic_events.csv"

# fraud cluster shape
FRAUD_REASON = "card_declined"
FRAUD_AMOUNT_LOW = Decimal("4980")
FRAUD_AMOUNT_HIGH = Decimal("5020")
FRAUD_ID_PREFIX = "fraud_"


def _money(value: float) -> Decimal:
    """Clean 2-dp Decimal. Going via str() avoids the binary-float expansion
    that Decimal(<float>) would produce (and that fails max_digits)."""
    return Decimal(str(round(value, 2)))


def _rupees(rng: random.Random) -> Decimal:
    """A rupee amount in [200, 50000], skewed towards smaller values."""
    raw = rng.lognormvariate(7.4, 0.9)  # median ~ e**7.4 ~ 1636
    clamped = max(float(_MIN_AMOUNT), min(float(_MAX_AMOUNT), raw))
    return _money(clamped)


def _pick_type(rng: random.Random) -> EventType:
    types = list(_TYPE_WEIGHTS)
    return rng.choices(types, weights=[_TYPE_WEIGHTS[t] for t in types], k=1)[0]


def build_batch(count: int = 70, seed: int = 42) -> list[EventCreate]:
    """`count` ordinary events. Deterministic for a given `seed`.

    Every record is constructed as an `EventCreate`, so a generator that drifts
    out of the schema's constraints fails loudly here rather than at insert.
    """
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)

    batch: list[EventCreate] = []
    for i in range(count):
        etype = _pick_type(rng)
        reason = rng.choice(RAZORPAY_FAILURE_REASONS[etype])
        attempts = rng.choices([0, 1, 2], weights=[0.7, 0.2, 0.1], k=1)[0]
        days_overdue = (
            rng.randint(1, 90) if etype is EventType.OVERDUE_INVOICE else 0
        )
        batch.append(
            EventCreate(
                event_id=f"evt_{i:03d}",
                event_type=etype,
                customer_id=f"cust_{fake.unique.random_number(digits=6, fix_len=True)}",
                amount=_rupees(rng),
                raw_failure_reason=reason,
                attempts_so_far=attempts,
                days_overdue=days_overdue,
            )
        )
    return batch


def build_fraud_cluster(size: int = 4, seed: int = 42) -> list[EventCreate]:
    """A tight cluster of near-identical failed payments across distinct
    'customers' -- looks like card testing / abuse, not a recoverable failure.
    CLAUDE.md Section 6: the Diagnosis Agent must reclassify these as `flagged`.
    """
    rng = random.Random(seed + 1)
    fake = Faker()
    fake.seed_instance(seed + 1)

    low, high = float(FRAUD_AMOUNT_LOW), float(FRAUD_AMOUNT_HIGH)
    return [
        EventCreate(
            event_id=f"{FRAUD_ID_PREFIX}{i:02d}",
            event_type=EventType.FAILED_PAYMENT,
            customer_id=f"cust_{fake.unique.random_number(digits=6, fix_len=True)}",
            amount=_money(rng.uniform(low, high)),
            raw_failure_reason=FRAUD_REASON,
            attempts_so_far=rng.randint(2, 3),   # already retried hard
            days_overdue=0,
        )
        for i in range(size)
    ]


def _to_frame(records: list[EventCreate]) -> pd.DataFrame:
    return pd.DataFrame(r.model_dump() for r in records)


def generate(
    count: int = 70,
    seed: int = 42,
    reset: bool = True,
    database_url: str | None = None,
) -> list[str]:
    """Build the full batch, seed it into Postgres, dump a CSV. Returns the
    inserted event_ids. `database_url` overrides the target DB (used by tests)."""
    records = build_batch(count, seed) + build_fraud_cluster(seed=seed)

    if reset:
        store.reset_db(database_url)

    with store.get_session(database_url) as session:
        for record in records:
            store.insert_event(session, record)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _to_frame(records).to_csv(CSV_PATH, index=False)

    return [r.event_id for r in records]


def _summary(records: list[EventCreate]) -> str:
    by_type = Counter(r.event_type for r in records)
    total = sum((r.amount for r in records), Decimal("0"))
    fraud = [r.event_id for r in records if r.event_id.startswith(FRAUD_ID_PREFIX)]
    lines = [
        f"{len(records)} events, total at risk Rs {total:,.2f}",
        *(f"  {etype:<20} {n}" for etype, n in sorted(by_type.items())),
        f"  fraud cluster: {fraud}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=70)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--reset", action=argparse.BooleanOptionalAction, default=True,
        help="drop + recreate all tables before seeding",
    )
    args = parser.parse_args()

    records = build_batch(args.count, args.seed) + build_fraud_cluster(seed=args.seed)
    print(_summary(records))
    generate(args.count, args.seed, args.reset)
    print(f"seeded -> {store.DEFAULT_DATABASE_URL}")
    print(f"csv    -> {CSV_PATH}")


if __name__ == "__main__":
    main()
