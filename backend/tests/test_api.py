"""REST API contract tests (AGENTS_CONTRACT.md §8/§12).

One module-scoped real pipeline run against the test DB; all endpoints read it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app import llm
from app.data import generate
from app.db import store
from app.pipeline import run as run_pipeline

pytestmark = pytest.mark.usefixtures("_require_postgres")


def _fake_embed(texts, *, settings=None):
    import math

    out = []
    for t in texts:
        v = [((ord(c) % 17) / 17.0) for c in t[: store.EMBED_DIM]]
        v += [0.0] * (store.EMBED_DIM - len(v))
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture(scope="module")
def client(request):
    from tests.conftest import TEST_DATABASE_URL

    # deterministic embeddings for this module (overrides the autouse disable)
    import app.llm as _llm

    orig_embed = _llm.embed
    _llm.embed = _fake_embed  # type: ignore

    orig_engine = store._engine
    store._engine = create_engine(TEST_DATABASE_URL)
    generate.generate(count=40, seed=7, reset=True, database_url=TEST_DATABASE_URL)
    run_pipeline(TEST_DATABASE_URL)

    from app.main import app

    with TestClient(app) as c:
        yield c

    _llm.embed = orig_embed  # type: ignore
    store._engine = orig_engine


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_events(client):
    r = client.get("/api/events")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["events"]) > 0
    e = body["events"][0]
    assert isinstance(e["amount"], str)  # money as decimal string
    assert {"event_id", "status", "root_cause", "recovered_amount"} <= e.keys()


def test_event_audit_and_404(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    r = client.get(f"/api/events/{eid}/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["event"]["event_id"] == eid
    assert isinstance(body["trail"], list) and body["trail"]
    assert client.get("/api/events/nope/audit").status_code == 404


def test_metrics_block_shape(client):
    m = client.get("/api/metrics").json()
    for key in (
        "total_at_risk", "total_recovered", "overall_recovery_rate", "event_count",
        "by_root_cause", "by_intervention", "avg_hours_to_recovery",
        "status_breakdown", "exceptions", "fraud_cluster",
    ):
        assert key in m
    assert m["status_breakdown"]["detected"] == 0  # pipeline drained the batch
    assert all(r["reason"] for r in m["exceptions"])


def test_pipeline_run_endpoint(client):
    r = client.post("/api/pipeline/run")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"metrics", "ran_at"}
    assert body["metrics"]["event_count"] > 0


def test_similar_endpoint(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    r = client.get(f"/api/events/{eid}/similar")
    assert r.status_code == 200
    body = r.json()
    assert body["event_id"] == eid
    assert isinstance(body["similar"], list)
    if body["similar"]:
        c = body["similar"][0]
        assert {"root_cause", "similarity", "case_text", "source"} <= c.keys()
        assert 0.0 <= c["similarity"] <= 1.0
    assert client.get("/api/events/nope/similar").status_code == 404
