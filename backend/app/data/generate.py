"""Synthetic at-risk-revenue batch generator (CLAUDE.md Section 7).

Produces a deterministic batch of 50-100 events across all four EventTypes,
seeded into Postgres through the validated `store.insert_event` path, plus two
deliberate awkward cases:

* a small fraud-like cluster (CLAUDE.md Section 6) the Diagnosis Agent's triage
  check is meant to catch, and
* a couple of "silent failures" -- charges that failed with no gateway error
  code at all, which end as honest exceptions and land in the human review
  queue (`build_silent_failures`).

Failure reasons are real Razorpay error codes (razorpay.com/docs/errors), not
invented ones.

CLI:
    uv run python -m app.data.generate --count 70 --seed 42 --reset
"""

from __future__ import annotations

import argparse
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
from faker import Faker

from app.db import store
from app.db.store import EventCreate, EventType

# --- real Razorpay failure codes, per event type -------------------------
# Verified 2026-09-03 against the test-mode failed-card-payment code list
# (razorpay.com/docs/payments/payments/test-card-details): BAD_REQUEST_ERROR
# codes `payment_timed_out`, `insufficient_fund`, `payment_cancelled`,
# `card_declined`, `card_disabled_for_online_payments`, `card_number_invalid`;
# GATEWAY_ERROR codes `gateway_technical_error`, `authentication_failed`.
# `card_expired` and the `bank_*` codes are real gateway/netbanking decline
# reasons kept for root-cause spread.
RAZORPAY_FAILURE_REASONS: dict[EventType, list[str | None]] = {
    EventType.FAILED_PAYMENT: [
        "insufficient_fund",        # customer -- wait + retry near a salary window
        "card_expired",             # customer -- needs a fresh instrument
        "authentication_failed",    # customer -- guided retry (OTP/3DS)
        "payment_timed_out",        # customer -- guided retry
        "card_declined",            # gateway  -- try an alternate method
        "card_number_invalid",      # customer -- needs a fresh instrument
        "bank_not_available",       # gateway  -- retry after a delay
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

# --- time spread -------------------------------------------------------------
# The batch is backdated over BATCH_SPAN_DAYS so events have a realistic
# `created_at` spread (the Diagnosis fraud-cluster check needs a real time
# axis). `_epoch()` is the "now" the span ends at -- taken once per build so a
# single build is internally consistent; not seed-deterministic in wall-clock
# terms, but all schema/id/amount tests are.
BATCH_SPAN_DAYS = 14
FRAUD_WINDOW_MINUTES = 40          # cluster falls inside one < 60-min window
FRAUD_DAYS_AGO = 3                 # where in the span the cluster sits


def _epoch() -> datetime:
    return datetime.now(timezone.utc)


# fraud cluster shape
FRAUD_REASON = "card_declined"
FRAUD_AMOUNT_LOW = Decimal("4980")
FRAUD_AMOUNT_HIGH = Decimal("5020")
FRAUD_ID_PREFIX = "fraud_"

# "silent failure" shape -- a charge that failed with NO gateway error code.
# Real and awkward: the classifier has nothing to reason from, so the case ends
# as an honest exception and Triage routes it to a human (`exception_no_error`).
SILENT_ID_PREFIX = "silent_"
SILENT_COUNT = 2


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


# Common Indian UPI PSP handles -- cosmetic realism only. NOT a Razorpay
# feature: Razorpay's test mode has no customer/contact simulator (verified
# against the test-card/UPI docs), so this is entirely our own invented data,
# same posture as `customer_id` below.
_UPI_HANDLES = ["okhdfcbank", "ybl", "oksbi", "paytm", "ibl", "axl"]


def _fake_contact(fake: Faker, rng: random.Random) -> dict[str, str]:
    """A synthetic name + phone + bank account + UPI VPA for one customer.

    Entirely invented (see module docstring / _UPI_HANDLES comment above) --
    good enough to make a case read like a real record and to give the
    Playground / Simulate feature a persona to role-play against.
    """
    name = fake.name()
    phone = f"+91{rng.choice('6789')}{rng.randint(10**8, 10**9 - 1)}"
    bank_account = str(fake.unique.random_number(digits=14, fix_len=True))
    slug = name.lower().replace(" ", "").replace(".", "")[:12]
    vpa = f"{slug}@{rng.choice(_UPI_HANDLES)}"
    return {
        "customer_name": name,
        "customer_phone": phone,
        "customer_bank_account": bank_account,
        "customer_upi_vpa": vpa,
    }


def build_batch(count: int = 70, seed: int = 42) -> list[EventCreate]:
    """`count` ordinary events. Deterministic for a given `seed`.

    Every record is constructed as an `EventCreate`, so a generator that drifts
    out of the schema's constraints fails loudly here rather than at insert.
    """
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)

    span_start = _epoch() - timedelta(days=BATCH_SPAN_DAYS)
    span_seconds = BATCH_SPAN_DAYS * 24 * 3600

    batch: list[EventCreate] = []
    for i in range(count):
        etype = _pick_type(rng)
        reason = rng.choice(RAZORPAY_FAILURE_REASONS[etype])
        attempts = rng.choices([0, 1, 2], weights=[0.7, 0.2, 0.1], k=1)[0]
        days_overdue = (
            rng.randint(1, 90) if etype is EventType.OVERDUE_INVOICE else 0
        )
        created_at = span_start + timedelta(seconds=rng.uniform(0, span_seconds))
        batch.append(
            EventCreate(
                event_id=f"evt_{i:03d}",
                event_type=etype,
                customer_id=f"cust_{fake.unique.random_number(digits=6, fix_len=True)}",
                **_fake_contact(fake, rng),
                amount=_rupees(rng),
                raw_failure_reason=reason,
                attempts_so_far=attempts,
                days_overdue=days_overdue,
                created_at=created_at,
            )
        )
    return batch


def build_fraud_cluster(size: int = 4, seed: int = 42) -> list[EventCreate]:
    """A tight cluster of near-identical failed payments across distinct
    'customers' -- looks like card testing / abuse, not a recoverable failure.
    CLAUDE.md Section 6: the Diagnosis Agent must reclassify these as `flagged`.
    """
    rng = random.Random(seed + 1)
    fake = Faker("en_IN")
    fake.seed_instance(seed + 1)

    low, high = float(FRAUD_AMOUNT_LOW), float(FRAUD_AMOUNT_HIGH)
    cluster_start = _epoch() - timedelta(days=FRAUD_DAYS_AGO)
    return [
        EventCreate(
            event_id=f"{FRAUD_ID_PREFIX}{i:02d}",
            event_type=EventType.FAILED_PAYMENT,
            customer_id=f"cust_{fake.unique.random_number(digits=6, fix_len=True)}",
            **_fake_contact(fake, rng),
            amount=_money(rng.uniform(low, high)),
            raw_failure_reason=FRAUD_REASON,
            attempts_so_far=rng.randint(2, 3),   # already retried hard
            days_overdue=0,
            # all members inside one tight (< 60 min) window
            created_at=cluster_start
            + timedelta(minutes=rng.uniform(0, FRAUD_WINDOW_MINUTES)),
        )
        for i in range(size)
    ]


def build_silent_failures(
    size: int = SILENT_COUNT, seed: int = 42
) -> list[EventCreate]:
    """Failed payments the gateway gave us **no error code** for.

    Nothing invented here -- gateways really do return a bare failure with no
    usable reason. The pipeline handles it honestly: Detection finds no failure
    signal, so the case becomes an `exception` with no root cause, and the
    Triage agent opens an `exception_no_error` ticket because a human is the
    only one who can find out what happened.
    """
    rng = random.Random(seed + 2)
    fake = Faker("en_IN")
    fake.seed_instance(seed + 2)

    span_start = _epoch() - timedelta(days=BATCH_SPAN_DAYS)
    span_seconds = BATCH_SPAN_DAYS * 24 * 3600
    return [
        EventCreate(
            event_id=f"{SILENT_ID_PREFIX}{i:02d}",
            event_type=EventType.FAILED_PAYMENT,
            customer_id=f"cust_{fake.unique.random_number(digits=6, fix_len=True)}",
            **_fake_contact(fake, rng),
            amount=_rupees(rng),
            raw_failure_reason=None,          # the whole point
            attempts_so_far=rng.randint(1, 2),
            days_overdue=0,
            created_at=span_start + timedelta(seconds=rng.uniform(0, span_seconds)),
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
    records = (
        build_batch(count, seed)
        + build_fraud_cluster(seed=seed)
        + build_silent_failures(seed=seed)
    )

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
    silent = [r.event_id for r in records if r.event_id.startswith(SILENT_ID_PREFIX)]
    lines = [
        f"{len(records)} events, total at risk Rs {total:,.2f}",
        *(f"  {etype:<20} {n}" for etype, n in sorted(by_type.items())),
        f"  fraud cluster: {fraud}",
        f"  silent failures (no error code): {silent}",
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

    records = (
        build_batch(args.count, args.seed)
        + build_fraud_cluster(seed=args.seed)
        + build_silent_failures(seed=args.seed)
    )
    print(_summary(records))
    generate(args.count, args.seed, args.reset)
    print(f"seeded -> {store.DEFAULT_DATABASE_URL}")
    print(f"csv    -> {CSV_PATH}")


if __name__ == "__main__":
    main()
