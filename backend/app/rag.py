"""RAG knowledge base for the Diagnosis Agent — build-order step 4 (extension).

When the rules classifier can't place a free-text ``raw_failure_reason``, the
Diagnosis Agent embeds the case, retrieves the nearest **already-classified**
cases from the ``resolved_cases`` table (pgvector, HNSW index), and gives them
to the LLM as few-shot examples ("here's how similar failures were diagnosed").

Everything here degrades to a no-op when pgvector is absent (embedded-Postgres
fallback) or no embeddings backend is configured — Diagnosis then behaves
exactly as it did before RAG.

Design notes:
* One interface, ``store.nearest_resolved_cases`` — the only vector search in
  the codebase. Swapping pgvector for a dedicated store is a change there.
* The knowledge base is **curated and bounded**: near-duplicate inserts are
  skipped (``rag_dedup_distance``) and each ``(root_cause, event_type)`` bucket
  is capped (``rag_bucket_cap``) — the same "bounded, with stopping rules"
  discipline the rest of the pipeline uses.
* ``RevRecEmbeddings`` exposes the embedder as a LangChain ``Embeddings`` so a
  LangChain retriever/chain can consume it later without touching this module.
"""

from __future__ import annotations

from typing import Any

from langchain_core.embeddings import Embeddings

from app import llm
from app.config import get_settings
from app.db import store

# Canonical seed examples so the very first pipeline run has something to
# retrieve. Real Razorpay test-mode failure phrasings → confirmed root cause.
_REFERENCE_CASES: list[tuple[str, str | None, str]] = [
    ("failed_payment", "insufficient_fund", "insufficient_funds"),
    ("failed_payment", "not enough balance in the account", "insufficient_funds"),
    ("failed_payment", "low balance, funds insufficient", "insufficient_funds"),
    ("failed_payment", "card_expired", "expired_instrument"),
    ("failed_payment", "the saved card is no longer valid", "expired_instrument"),
    ("failed_payment", "card_number_invalid", "expired_instrument"),
    ("expired_mandate", "mandate_creation_expired", "expired_instrument"),
    ("expired_mandate", "auto-pay authorisation has lapsed", "expired_instrument"),
    ("failed_payment", "bank_not_available", "bank_downtime"),
    ("failed_payment", "issuing bank is down for maintenance", "bank_downtime"),
    ("failed_payment", "gateway_technical_error", "bank_downtime"),
    ("failed_payment", "upstream network timeout at the bank", "bank_downtime"),
    ("failed_payment", "authentication_failed", "auth_failure"),
    ("failed_payment", "customer entered the wrong OTP", "auth_failure"),
    ("failed_payment", "payment_timed_out", "auth_failure"),
    ("failed_payment", "3-D Secure step abandoned", "auth_failure"),
    ("failed_payment", "card_declined", "card_declined"),
    ("failed_payment", "issuer declined the transaction", "card_declined"),
    ("abandoned_checkout", "left the checkout before paying", "checkout_abandoned"),
    ("overdue_invoice", "invoice past due, no dispute raised", "invoice_forgotten"),
]

_MIN_KB_CONFIDENCE = 0.55  # don't teach the KB from shaky classifications


def case_text(event: Any) -> str:
    """The compact text that represents an event for embedding / retrieval."""
    reason = (getattr(event, "raw_failure_reason", None) or "").strip()
    parts = [
        f"event_type={event.event_type}",
        f"failure_reason={reason or 'none'}",
        f"attempts={getattr(event, 'attempts_so_far', 0)}",
    ]
    days = getattr(event, "days_overdue", 0) or 0
    if days:
        parts.append(f"days_overdue={days}")
    return " | ".join(parts)


class RevRecEmbeddings(Embeddings):
    """LangChain ``Embeddings`` backed by :func:`app.llm.embed`."""

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings or get_settings()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return llm.embed(list(texts), settings=self.settings)

    def embed_query(self, text: str) -> list[float]:
        return llm.embed([text], settings=self.settings)[0]


def _rag_on(session: store.Session, settings: Any) -> bool:
    return bool(
        getattr(settings, "rag_enabled", True)
        and store.VECTOR_ENABLED
        and llm.embeddings_available(settings)
    )


def retrieve_similar(
    session: store.Session, event: Any, *, settings: Any = None, k: int | None = None
) -> list[dict[str, Any]]:
    """Nearest already-classified cases for `event`. ``[]`` when RAG is off or
    on any error — the caller must treat an empty list as "no RAG context"."""
    settings = settings or get_settings()
    if not _rag_on(session, settings):
        return []
    k = k or getattr(settings, "rag_top_k", 5)
    try:
        vec = llm.embed([case_text(event)], settings=settings)[0]
        hits = store.nearest_resolved_cases(
            session, vec, k=k, event_type=str(event.event_type)
        )
    except Exception:
        return []
    return [
        {
            "event_id": row.event_id,
            "event_type": row.event_type,
            "raw_failure_reason": row.raw_failure_reason,
            "case_text": row.case_text,
            "root_cause": row.root_cause,
            "source": row.source,
            "similarity": round(1.0 - dist, 4),
        }
        for row, dist in hits
    ]


def format_for_prompt(similar: list[dict[str, Any]]) -> str:
    """Render retrieved cases as a few-shot block for the classifier prompt."""
    lines = ["Similar past cases already classified (most similar first):"]
    for c in similar:
        lines.append(
            f"- [{c['similarity']:.2f}] {c['case_text']} -> root_cause={c['root_cause']}"
        )
    return "\n".join(lines)


def seed_reference_cases(session: store.Session, *, settings: Any = None) -> int:
    """One-time: load the canonical reference examples. No-op if already seeded
    or RAG is off. Returns how many were inserted."""
    settings = settings or get_settings()
    if not _rag_on(session, settings):
        return 0
    if store.resolved_case_count(session) > 0:
        return 0
    texts = [
        f"event_type={et} | failure_reason={rr or 'none'} | attempts=0"
        for et, rr, _ in _REFERENCE_CASES
    ]
    try:
        vectors = llm.embed(texts, settings=settings)
    except Exception:
        return 0
    n = 0
    for (event_type, reason, root_cause), t, vec in zip(_REFERENCE_CASES, texts, vectors):
        store.add_resolved_case(
            session,
            event_id=f"ref_{n:03d}",
            event_type=event_type,
            raw_failure_reason=reason,
            case_text=t,
            root_cause=root_cause,
            embedding=vec,
            confidence=1.0,
            source="reference",
        )
        n += 1
    return n


def index_resolved_cases(session: store.Session, *, settings: Any = None) -> int:
    """After a pipeline run, add this batch's confidently-classified events to
    the knowledge base — deduped and bucket-capped. Returns rows inserted."""
    settings = settings or get_settings()
    if not _rag_on(session, settings):
        return 0

    cap = getattr(settings, "rag_bucket_cap", 200)
    dedup = getattr(settings, "rag_dedup_distance", 0.05)

    candidates = [
        e
        for e in store.all_events(session)
        if e.root_cause
        and str(e.root_cause) not in {"unknown", "suspected_fraud"}
        and (e.diagnosis_confidence or 0) >= _MIN_KB_CONFIDENCE
    ]
    if not candidates:
        return 0

    texts = [case_text(e) for e in candidates]
    try:
        vectors = llm.embed(texts, settings=settings)
    except Exception:
        return 0

    inserted = 0
    for e, t, vec in zip(candidates, texts, vectors):
        near = store.nearest_resolved_cases(
            session, vec, k=1, event_type=str(e.event_type)
        )
        if near and near[0][1] < dedup:
            continue  # a near-identical case is already in the KB
        store.add_resolved_case(
            session,
            event_id=e.event_id,
            event_type=str(e.event_type),
            raw_failure_reason=e.raw_failure_reason,
            case_text=t,
            root_cause=str(e.root_cause),
            embedding=vec,
            confidence=float(e.diagnosis_confidence or 1.0),
            source="pipeline",
        )
        store.trim_resolved_bucket(
            session, root_cause=str(e.root_cause), event_type=str(e.event_type), cap=cap
        )
        inserted += 1
    return inserted
