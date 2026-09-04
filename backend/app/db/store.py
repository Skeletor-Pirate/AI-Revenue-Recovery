"""Shared event store for the AI Revenue Recovery pipeline.

Every agent (detection, diagnosis, recovery, audit) reads and writes state
through this module -- never with raw SQL of its own. The `audit_log` table
IS the audit trail the buildathon brief requires: every money-related agent
action is written here via `log_action()` as it happens.

Schema mirrors CLAUDE.md Section 5. Controlled vocabularies live here as
`StrEnum`s: `EventType`, `EventStatus`, `Agent`, and `RootCause` (the
Diagnosis Agent's output vocabulary, one intervention each -- see
backend/app/agents/AGENTS_CONTRACT.md).
Stack: SQLModel (SQLAlchemy + Pydantic) on PostgreSQL 16, psycopg 3 driver.

Two layers of models:
  * table models   (`Event`, `AuditLog`)          -- persistence only
  * schema models  (`EventCreate` / `EventUpdate` / `EventRead`, `AuditCreate` /
                    `AuditRead`)                   -- Pydantic validation +
                    the request/response shapes the FastAPI layer reuses
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterable

from dotenv import load_dotenv
from pydantic import ConfigDict, field_validator
from sqlalchemy import Column, DateTime, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, create_engine, select

try:  # pgvector is optional — RAG is disabled on a Postgres without it
    from pgvector.sqlalchemy import Vector

    _HAVE_PGVECTOR_PKG = True
except Exception:  # pragma: no cover
    Vector = None  # type: ignore
    _HAVE_PGVECTOR_PKG = False

load_dotenv()  # read .env into os.environ if present

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://revrec:revrec@localhost:5432/revrec",
)

MONEY = Decimal("0.01")  # quantum for rounding rupee amounts to paise
EMBED_DIM = 384  # embedding dimensionality for the RAG knowledge base (app/rag.py)

# Set by init_db()/reset_db() once we know whether the target Postgres has the
# `vector` extension. When False the `resolved_cases` table is skipped and the
# RAG layer degrades to a no-op.
VECTOR_ENABLED = False


# --- controlled vocabulary -------------------------------------------------
# StrEnum members ARE strings ("EventType.FAILED_PAYMENT" == "failed_payment").
# Used as field types on the *schema* models, so Pydantic rejects any value
# outside the set with a ValidationError (a subclass of ValueError).

class EventType(StrEnum):
    FAILED_PAYMENT = "failed_payment"        # a charge the bank / network rejected
    ABANDONED_CHECKOUT = "abandoned_checkout"  # left mid-payment; never captured
    OVERDUE_INVOICE = "overdue_invoice"      # B2B bill past its due date
    EXPIRED_MANDATE = "expired_mandate"      # standing auto-charge permission lapsed


class EventStatus(StrEnum):
    # forward-only lifecycle:
    #   detected -> diagnosed -> action_taken -> recovered
    #                                         \-> exception  (gave up, with a reason)
    #                                         \-> flagged    (looks like fraud; halt)
    DETECTED = "detected"
    DIAGNOSED = "diagnosed"
    ACTION_TAKEN = "action_taken"
    RECOVERED = "recovered"
    EXCEPTION = "exception"
    FLAGGED = "flagged"


class PTPStatus(StrEnum):
    """Promise-To-Pay commitment lifecycle states."""
    NONE = "none"
    PROMISED = "promised"
    HONORED = "honored"
    BROKEN = "broken"


class Agent(StrEnum):
    DETECTION = "detection"
    DIAGNOSIS = "diagnosis"
    RECOVERY = "recovery"
    TRIAGE = "triage"        # the fraud-cluster check; can force status -> flagged
    AUDIT = "audit"


class RootCause(StrEnum):
    """Controlled vocabulary the Diagnosis Agent writes to `Event.root_cause`.
    Each member maps to exactly one Recovery intervention (plan.md Section 2).
    The DB column stays `str | None`; this enum types the schema layer and the
    agents' shared contract (backend/app/agents/AGENTS_CONTRACT.md).
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"      # wait + retry near a salary-credit window
    EXPIRED_INSTRUMENT = "expired_instrument"      # send re-authorization / re-mandate link
    BANK_DOWNTIME = "bank_downtime"                # suggest an alternate payment method
    AUTH_FAILURE = "auth_failure"                  # prompt a fresh guided retry
    CARD_DECLINED = "card_declined"                # one cautious retry, then exception
    CHECKOUT_ABANDONED = "checkout_abandoned"      # personalized nudge, bounded discount above a gate
    INVOICE_FORGOTTEN = "invoice_forgotten"        # escalation ladder: reminder -> notice -> human handoff
    SUSPECTED_FRAUD = "suspected_fraud"            # set by triage only; Recovery refuses to act
    UNKNOWN = "unknown"                            # classifier could not decide -> honest exception


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- table models ---------------------------------------------------------
# `table=True` makes the class a real DB table. Pydantic validation does NOT
# run on these at construction time -- callers go through the schema models
# below, which do validate.

class Event(SQLModel, table=True):
    __tablename__ = "events"

    event_id: str = Field(primary_key=True)
    event_type: str = Field(index=True)
    customer_id: str
    amount: Decimal = Field(max_digits=14, decimal_places=2)  # -> NUMERIC(14,2)
    currency: str = "INR"
    raw_failure_reason: str | None = None   # gateway's words, pre-diagnosis
    attempts_so_far: int = 0                # for stopping-rule enforcement
    days_overdue: int = 0                   # for B2B invoices
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    status: str = Field(default=EventStatus.DETECTED, index=True)
    root_cause: str | None = None                 # filled by Diagnosis Agent
    diagnosis_confidence: float | None = None     # 0.0 - 1.0
    recovered_amount: Decimal = Field(
        default=Decimal("0"), max_digits=14, decimal_places=2
    )
    promised_date: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ptp_status: str = Field(default=PTPStatus.NONE, index=True)
    retry_schedule: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)  # IDENTITY
    event_id: str = Field(foreign_key="events.event_id", index=True)
    agent: str
    action: str                       # e.g. 'classified_root_cause'
    reasoning: str                    # human-readable WHY -- never empty
    payload: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB)
    )
    timestamp: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


# --- RAG knowledge base (pgvector) --------------------------------------
# One row per past classified case. The Diagnosis Agent embeds an incoming
# free-text failure and retrieves the nearest rows here as few-shot examples
# (app/rag.py). Only created when the target Postgres has the `vector`
# extension; otherwise skipped and RAG is a no-op.

if _HAVE_PGVECTOR_PKG:

    class ResolvedCase(SQLModel, table=True):
        __tablename__ = "resolved_cases"
        __table_args__ = (
            Index(
                "ix_resolved_cases_embedding_hnsw",
                "embedding",
                postgresql_using="hnsw",
                postgresql_ops={"embedding": "vector_cosine_ops"},
                postgresql_with={"m": 16, "ef_construction": 64},
            ),
        )

        id: int | None = Field(default=None, primary_key=True)
        event_id: str = Field(index=True)
        event_type: str = Field(index=True)
        raw_failure_reason: str | None = None
        case_text: str                      # the exact text that was embedded
        root_cause: str = Field(index=True)
        confidence: float = 1.0
        source: str = Field(default="pipeline")   # "pipeline" | "reference"
        created_at: datetime = Field(
            default_factory=_utcnow,
            sa_column=Column(DateTime(timezone=True), nullable=False),
        )
        embedding: Any = Field(sa_column=Column(Vector(EMBED_DIM)))
else:  # pragma: no cover - only on a Postgres without pgvector
    ResolvedCase = None  # type: ignore


# --- schema models (Pydantic validation) ---------------------------------
# extra="forbid"     -> an unexpected key (typo) raises instead of being ignored
# use_enum_values    -> members are stored/dumped as their plain string value

_STRICT = ConfigDict(extra="forbid", use_enum_values=True, validate_default=True)


class EventCreate(SQLModel):
    """Validated input for `insert_event`. Also the POST /events body."""

    model_config = _STRICT

    event_id: str = Field(min_length=1)
    event_type: EventType
    customer_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, max_digits=14)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    raw_failure_reason: str | None = None
    attempts_so_far: int = Field(default=0, ge=0)
    days_overdue: int = Field(default=0, ge=0)
    status: EventStatus = EventStatus.DETECTED
    created_at: datetime | None = None   # optional backdate; the synthetic
    #   generator sets it to spread the batch over time. When omitted the
    #   table default (_utcnow) applies. `updated_at` follows `created_at`
    #   on insert when this is set.
    promised_date: datetime | None = None
    ptp_status: PTPStatus = PTPStatus.NONE
    retry_schedule: list[dict[str, Any]] | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("amount")
    @classmethod
    def _round_money(cls, v: Decimal) -> Decimal:
        return v.quantize(MONEY)


class EventUpdate(SQLModel):
    """Validated partial patch for `update_event`. Every field optional;
    only the keys actually passed are written (`model_dump(exclude_unset=True)`)."""

    model_config = _STRICT

    event_type: EventType | None = None
    customer_id: str | None = Field(default=None, min_length=1)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    raw_failure_reason: str | None = None
    attempts_so_far: int | None = Field(default=None, ge=0)
    days_overdue: int | None = Field(default=None, ge=0)
    status: EventStatus | None = None
    root_cause: RootCause | None = None
    diagnosis_confidence: float | None = Field(default=None, ge=0, le=1)
    recovered_amount: Decimal | None = Field(default=None, ge=0, max_digits=14)
    promised_date: datetime | None = None
    ptp_status: PTPStatus | None = None
    retry_schedule: list[dict[str, Any]] | None = None

    @field_validator("amount", "recovered_amount")
    @classmethod
    def _round_money(cls, v: Decimal | None) -> Decimal | None:
        return None if v is None else v.quantize(MONEY)


class EventRead(SQLModel):
    """Response shape for GET /events -- every stored column."""

    event_id: str
    event_type: str
    customer_id: str
    amount: Decimal
    currency: str
    raw_failure_reason: str | None
    attempts_so_far: int
    days_overdue: int
    created_at: datetime
    updated_at: datetime
    status: str
    root_cause: str | None
    diagnosis_confidence: float | None
    recovered_amount: Decimal
    promised_date: datetime | None = None
    ptp_status: str = PTPStatus.NONE
    retry_schedule: list[dict[str, Any]] | None = None


class AuditCreate(SQLModel):
    """Validated input for `log_action`."""

    model_config = _STRICT

    event_id: str = Field(min_length=1)
    agent: Agent
    action: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    payload: dict[str, Any] | None = None


class AuditRead(SQLModel):
    """Response shape for GET /events/{id}/audit."""

    id: int
    event_id: str
    agent: str
    action: str
    reasoning: str
    payload: dict[str, Any] | None
    timestamp: datetime


class ResolvedCaseRead(SQLModel):
    """Response shape for the RAG 'similar past cases' surfaces (no embedding)."""

    id: int
    event_id: str
    event_type: str
    raw_failure_reason: str | None
    case_text: str
    root_cause: str
    confidence: float
    source: str
    created_at: datetime


# --- engine + schema ----------------------------------------------------

_engine = None


def get_engine(database_url: str | None = None):
    """Lazily build (and cache) the SQLAlchemy engine -- the connection pool
    every Session borrows from."""
    global _engine
    if database_url is not None:
        return create_engine(database_url)
    if _engine is None:
        _engine = create_engine(DEFAULT_DATABASE_URL)
    return _engine


def get_session(database_url: str | None = None) -> Session:
    """A new Session -- the unit of work you add()/commit() through."""
    return Session(get_engine(database_url))


def _enable_vector(engine) -> bool:
    """Try to enable the pgvector extension. Returns True if `vector` is usable.

    Sets the module-level ``VECTOR_ENABLED`` flag the RAG layer checks.
    """
    global VECTOR_ENABLED
    VECTOR_ENABLED = False
    if not _HAVE_PGVECTOR_PKG:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        VECTOR_ENABLED = True
    except Exception:
        VECTOR_ENABLED = False
    return VECTOR_ENABLED


def _managed_tables() -> list[Any] | None:
    """Tables to create/drop. ``None`` (= all) when pgvector is on; an explicit
    list excluding `resolved_cases` when it's off."""
    if VECTOR_ENABLED:
        return None
    return [
        t for t in SQLModel.metadata.sorted_tables if t.name != "resolved_cases"
    ]


def init_db(database_url: str | None = None) -> None:
    """CREATE TABLE for every SQLModel table that doesn't exist yet.

    Also enables pgvector when available; the `resolved_cases` table is created
    only then (RAG is a no-op otherwise).
    """
    engine = get_engine(database_url)
    _enable_vector(engine)
    SQLModel.metadata.create_all(engine, tables=_managed_tables())


def reset_db(database_url: str | None = None) -> None:
    """Drop every table and recreate -- fresh batch per demo run / per test."""
    engine = get_engine(database_url)
    _enable_vector(engine)
    tables = _managed_tables()
    SQLModel.metadata.drop_all(engine, tables=tables)
    SQLModel.metadata.create_all(engine, tables=tables)


# --- events -----------------------------------------------------------------

def insert_event(
    session: Session,
    data: EventCreate | None = None,
    /,
    **kwargs: Any,
) -> Event:
    """Add one at-risk-revenue event.

    Pass a pre-built `EventCreate` (the API layer does this) or keyword args
    (agents/tests do this). Either way the data is Pydantic-validated before
    it reaches the database.
    """
    payload = data if data is not None else EventCreate(**kwargs)
    values = payload.model_dump()
    if values.get("created_at") is not None:
        # backdated insert (synthetic generator): keep updated_at in step
        values.setdefault("updated_at", values["created_at"])
    else:
        values.pop("created_at", None)  # let the table default (_utcnow) apply
    event = Event(**values)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def update_event(
    session: Session,
    event_id: str,
    data: EventUpdate | None = None,
    /,
    **fields: Any,
) -> Event:
    """Patch selected columns of one event. Always bumps updated_at.

    Unknown keys raise (EventUpdate has extra="forbid"); bad values raise
    (Pydantic constraints). Missing event -> KeyError.
    """
    patch = (data if data is not None else EventUpdate(**fields)).model_dump(
        exclude_unset=True
    )

    event = session.get(Event, event_id)
    if event is None:
        raise KeyError(f"no such event: {event_id!r}")

    for key, value in patch.items():
        setattr(event, key, value)
    event.updated_at = _utcnow()

    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def get_event(session: Session, event_id: str) -> Event | None:
    return session.get(Event, event_id)


def get_events_by_status(
    session: Session, status: str | Iterable[str]
) -> list[Event]:
    statuses = [status] if isinstance(status, str) else list(status)
    statement = (
        select(Event)
        .where(Event.status.in_([str(s) for s in statuses]))
        .order_by(Event.created_at)
    )
    return list(session.exec(statement))


def all_events(session: Session) -> list[Event]:
    return list(session.exec(select(Event).order_by(Event.created_at)))


# --- audit log ---------------------------------------------------------------

def log_action(
    session: Session,
    data: AuditCreate | None = None,
    /,
    **kwargs: Any,
) -> int:
    """Append ONE row to the audit trail. Returns the new row's id.

    The only way any agent records that it did something. `reasoning` is the
    plain-English justification shown to the panel; `payload` (a dict) lands
    in a JSONB column and round-trips as a dict.
    """
    payload = data if data is not None else AuditCreate(**kwargs)
    entry = AuditLog(**payload.model_dump())
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry.id


def get_audit_trail(
    session: Session, event_id: str | None = None
) -> list[AuditLog]:
    statement = select(AuditLog).order_by(AuditLog.id)
    if event_id is not None:
        statement = statement.where(AuditLog.event_id == event_id)
    return list(session.exec(statement))


# --- RAG knowledge base (resolved_cases) ------------------------------------
# All vector search lives behind these three functions -- the only place the
# project does nearest-neighbour lookups. Swapping pgvector for a dedicated
# store later is a change here and nowhere else.

def add_resolved_case(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    raw_failure_reason: str | None,
    case_text: str,
    root_cause: str,
    embedding: list[float],
    confidence: float = 1.0,
    source: str = "pipeline",
) -> Any:
    """Insert one labelled case into the RAG knowledge base."""
    if not VECTOR_ENABLED:
        raise RuntimeError("pgvector not enabled; resolved_cases unavailable")
    row = ResolvedCase(
        event_id=event_id,
        event_type=event_type,
        raw_failure_reason=raw_failure_reason,
        case_text=case_text,
        root_cause=root_cause,
        embedding=embedding,
        confidence=confidence,
        source=source,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def nearest_resolved_cases(
    session: Session,
    embedding: list[float],
    *,
    k: int = 5,
    event_type: str | None = None,
) -> list[tuple[Any, float]]:
    """The `k` nearest knowledge-base rows to `embedding` (cosine distance).

    Returns ``[(ResolvedCase, distance), ...]`` ascending by distance
    (0 = identical). Uses the HNSW index. Empty list when pgvector is off.
    """
    if not VECTOR_ENABLED:
        return []
    dist = ResolvedCase.embedding.cosine_distance(embedding).label("distance")
    stmt = select(ResolvedCase, dist)
    if event_type is not None:
        stmt = stmt.where(ResolvedCase.event_type == event_type)
    stmt = stmt.order_by(dist).limit(k)
    return [(row, float(d)) for row, d in session.exec(stmt).all()]


def resolved_case_count(
    session: Session,
    *,
    root_cause: str | None = None,
    event_type: str | None = None,
) -> int:
    if not VECTOR_ENABLED:
        return 0
    stmt = select(ResolvedCase)
    if root_cause is not None:
        stmt = stmt.where(ResolvedCase.root_cause == root_cause)
    if event_type is not None:
        stmt = stmt.where(ResolvedCase.event_type == event_type)
    return len(session.exec(stmt).all())


def trim_resolved_bucket(
    session: Session, *, root_cause: str, event_type: str, cap: int
) -> int:
    """Delete the oldest rows in a (root_cause, event_type) bucket beyond `cap`.
    Returns how many were removed. Keeps the knowledge base bounded."""
    if not VECTOR_ENABLED:
        return 0
    rows = session.exec(
        select(ResolvedCase)
        .where(ResolvedCase.root_cause == root_cause)
        .where(ResolvedCase.event_type == event_type)
        .order_by(ResolvedCase.created_at.desc())
    ).all()
    removed = 0
    for row in rows[cap:]:
        session.delete(row)
        removed += 1
    if removed:
        session.commit()
    return removed


if __name__ == "__main__":
    init_db()
    print(f"initialised event store at {DEFAULT_DATABASE_URL}")
