"""Shared event store for the AI Revenue Recovery pipeline.

Every agent (detection, diagnosis, recovery, audit) reads and writes state
through this module -- never with raw SQL of its own. The `audit_log` table
IS the audit trail the buildathon brief requires: every money-related agent
action is written here via `log_action()` as it happens.

Schema mirrors CLAUDE.md Section 5.
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
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, create_engine, select

load_dotenv()  # read .env into os.environ if present

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://revrec:revrec@localhost:5432/revrec",
)

MONEY = Decimal("0.01")  # quantum for rounding rupee amounts to paise


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


class Agent(StrEnum):
    DETECTION = "detection"
    DIAGNOSIS = "diagnosis"
    RECOVERY = "recovery"
    TRIAGE = "triage"        # the fraud-cluster check; can force status -> flagged
    AUDIT = "audit"


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
    root_cause: str | None = None
    diagnosis_confidence: float | None = Field(default=None, ge=0, le=1)
    recovered_amount: Decimal | None = Field(default=None, ge=0, max_digits=14)

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


def init_db(database_url: str | None = None) -> None:
    """CREATE TABLE for every SQLModel table that doesn't exist yet."""
    SQLModel.metadata.create_all(get_engine(database_url))


def reset_db(database_url: str | None = None) -> None:
    """Drop every table and recreate -- fresh batch per demo run / per test."""
    engine = get_engine(database_url)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


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
    event = Event(**payload.model_dump())
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


if __name__ == "__main__":
    init_db()
    print(f"initialised event store at {DEFAULT_DATABASE_URL}")
