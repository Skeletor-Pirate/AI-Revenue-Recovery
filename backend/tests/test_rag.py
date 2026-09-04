"""RAG knowledge base + retrieve-then-classify. pgvector-backed.

`_offline_embeddings` (conftest, autouse) disables real embeddings; these tests
override it with a deterministic fake embedder. Skipped if pgvector is absent.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app import llm, rag
from app.agents import diagnosis
from app.db import store
from app.db.store import RootCause

pytestmark = pytest.mark.usefixtures("_require_postgres")


def _fake_embed(texts, *, settings=None):
    """Deterministic 384-d unit vectors — identical text -> identical vector."""
    out = []
    for t in texts:
        v = [0.0] * store.EMBED_DIM
        for i, ch in enumerate(t[: store.EMBED_DIM]):
            v[i] = (ord(ch) % 17) / 17.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


@pytest.fixture()
def fake_embeddings(monkeypatch):
    monkeypatch.setattr("app.llm.embed", _fake_embed)


@pytest.fixture()
def rag_settings():
    return SimpleNamespace(
        rag_enabled=True, rag_top_k=5, rag_bucket_cap=200, rag_dedup_distance=0.02,
        openai_api_key=None,
    )


def _mk_event(**kw):
    base = dict(
        event_id="evt_x", event_type="failed_payment", customer_id="c",
        amount=1000, raw_failure_reason="weird free text reason", attempts_so_far=1,
        days_overdue=0, status="detected",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_vector_enabled_on_test_db(session):
    assert store.VECTOR_ENABLED, "pgvector must be enabled for the RAG tests"


def test_rag_degrades_when_embeddings_unavailable(session, rag_settings):
    # _offline_embeddings is active (no fake) -> llm.embed raises
    assert rag.retrieve_similar(session, _mk_event(), settings=rag_settings) == []
    assert rag.seed_reference_cases(session, settings=rag_settings) == 0
    assert rag.index_resolved_cases(session, settings=rag_settings) == 0


def test_seed_reference_cases_is_idempotent(session, fake_embeddings, rag_settings):
    n = rag.seed_reference_cases(session, settings=rag_settings)
    assert n == len(rag._REFERENCE_CASES) > 0
    assert rag.seed_reference_cases(session, settings=rag_settings) == 0
    assert store.resolved_case_count(session) == n


def test_retrieve_similar_returns_hits_filtered_by_type(session, fake_embeddings, rag_settings):
    rag.seed_reference_cases(session, settings=rag_settings)
    hits = rag.retrieve_similar(
        session, _mk_event(event_type="failed_payment"), settings=rag_settings, k=3
    )
    assert 1 <= len(hits) <= 3
    assert all(h["event_type"] == "failed_payment" for h in hits)
    assert set(hits[0]) == {
        "event_id", "event_type", "raw_failure_reason", "case_text",
        "root_cause", "source", "similarity",
    }
    assert hits[0]["similarity"] >= hits[-1]["similarity"]  # sorted nearest-first


def test_add_and_nearest_roundtrip(session, fake_embeddings, rag_settings):
    vec = _fake_embed(["event_type=failed_payment | failure_reason=x"])[0]
    store.add_resolved_case(
        session, event_id="k1", event_type="failed_payment",
        raw_failure_reason="x", case_text="event_type=failed_payment | failure_reason=x",
        root_cause="bank_downtime", embedding=vec,
    )
    near = store.nearest_resolved_cases(session, vec, k=1)
    assert near and near[0][0].event_id == "k1"
    assert near[0][1] < 1e-6  # identical vector -> ~zero distance


def test_index_resolved_cases_dedups(session, fake_embeddings, rag_settings):
    store.insert_event(
        session, event_id="evt_a", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=500, raw_failure_reason="mystery",
    )
    store.update_event(
        session, "evt_a", status=store.EventStatus.DIAGNOSED,
        root_cause=RootCause.BANK_DOWNTIME, diagnosis_confidence=0.8,
    )
    first = rag.index_resolved_cases(session, settings=rag_settings)
    assert first == 1
    second = rag.index_resolved_cases(session, settings=rag_settings)
    assert second == 0  # identical case_text -> deduped
    assert store.resolved_case_count(session, root_cause="bank_downtime") == 1


def test_index_skips_unknown_and_low_confidence(session, fake_embeddings, rag_settings):
    for eid, rc, conf in [
        ("evt_u", RootCause.UNKNOWN, 0.9),
        ("evt_low", RootCause.BANK_DOWNTIME, 0.4),
        ("evt_fraud", RootCause.SUSPECTED_FRAUD, 0.9),
    ]:
        store.insert_event(
            session, event_id=eid, event_type=store.EventType.FAILED_PAYMENT,
            customer_id="c", amount=500, raw_failure_reason="x",
        )
        store.update_event(
            session, eid, status=store.EventStatus.DIAGNOSED,
            root_cause=rc, diagnosis_confidence=conf,
        )
    assert rag.index_resolved_cases(session, settings=rag_settings) == 0


def test_bucket_cap_trims_oldest(session, fake_embeddings):
    s = SimpleNamespace(rag_enabled=True, rag_top_k=5, rag_bucket_cap=3,
                        rag_dedup_distance=0.0, openai_api_key=None)
    for i in range(6):
        v = _fake_embed([f"case number {i}"])[0]
        store.add_resolved_case(
            session, event_id=f"c{i}", event_type="failed_payment",
            raw_failure_reason="x", case_text=f"case number {i}",
            root_cause="bank_downtime", embedding=v,
        )
        store.trim_resolved_bucket(
            session, root_cause="bank_downtime", event_type="failed_payment", cap=3
        )
    assert store.resolved_case_count(session, root_cause="bank_downtime") == 3


def test_revrec_embeddings_wrapper(fake_embeddings, rag_settings):
    emb = rag.RevRecEmbeddings(rag_settings)
    assert len(emb.embed_query("hi")) == store.EMBED_DIM
    docs = emb.embed_documents(["a", "b"])
    assert len(docs) == 2 and len(docs[0]) == store.EMBED_DIM


def test_diagnosis_feeds_rag_context_to_the_llm(session, fake_embeddings, monkeypatch):
    rag_settings = SimpleNamespace(
        rag_enabled=True, rag_top_k=5, rag_bucket_cap=200, rag_dedup_distance=0.02,
        openai_api_key=None, anthropic_api_key="sk-test", anthropic_model="x",
    )
    monkeypatch.setattr(diagnosis, "get_settings", lambda: rag_settings)
    monkeypatch.setattr("app.rag.get_settings", lambda: rag_settings)
    rag.seed_reference_cases(session, settings=rag_settings)

    seen_prompts = []

    def fake_chat(system, user, *, settings, max_tokens=300):
        seen_prompts.append(user)
        return '{"root_cause": "bank_downtime", "confidence": 0.8, "reasoning": "rag says so"}'

    monkeypatch.setattr("app.llm.chat", fake_chat)
    monkeypatch.setattr("app.llm.available", lambda s: True)

    store.insert_event(
        session, event_id="evt_ft", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c", amount=800, raw_failure_reason="totally novel phrasing here",
    )
    diagnosis.run(session, settings=rag_settings)

    assert seen_prompts and "Similar past cases" in seen_prompts[0]
    ev = store.get_event(session, "evt_ft")
    assert ev.root_cause == "bank_downtime"
    row = [r for r in store.get_audit_trail(session, "evt_ft")
           if r.action == "llm_classified_root_cause"][0]
    assert row.payload["rag_examples"] > 0
    assert row.payload["similar_case_ids"]
