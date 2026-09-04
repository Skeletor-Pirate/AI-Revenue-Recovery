"""REST API contract tests (AGENTS_CONTRACT.md §8/§12).

One module-scoped real pipeline run against the test DB; all endpoints read it.
"""

from __future__ import annotations

from decimal import Decimal

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
        "ai_recovered", "human_recovered", "tickets",
    ):
        assert key in m
    assert m["status_breakdown"]["detected"] == 0  # pipeline drained the batch
    assert all(r["reason"] for r in m["exceptions"])
    # the recovery split adds up to the honest total
    assert (
        Decimal(m["ai_recovered"]) + Decimal(m["human_recovered"])
        == Decimal(m["total_recovered"])
    )


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


# --- human review queue ----------------------------------------------------
# These mutate state, so they run last (pytest keeps file order) and each takes
# its own ticket off the queue.


def _first_open_ticket(client) -> dict:
    tickets = client.get("/api/tickets").json()["tickets"]
    return next(t for t in tickets if t["status"] == "open")


def test_list_tickets_is_priority_ordered(client):
    r = client.get("/api/tickets")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["tickets"]) > 0

    priorities = [t["priority"] for t in body["tickets"]]
    assert priorities == sorted(priorities, reverse=True)

    t = body["tickets"][0]
    assert {"ticket_id", "event_id", "reason", "priority", "status",
            "summary", "recovered_amount"} <= t.keys()
    assert isinstance(t["recovered_amount"], str)  # money as decimal string
    assert t["summary"]  # a reviewer is always told why

    # the pipeline's fraud cluster is the most urgent thing in the batch
    assert body["tickets"][0]["reason"] == "suspected_fraud"
    assert body["open_count"] > 0

    filtered = client.get("/api/tickets?status=open").json()
    assert all(t["status"] == "open" for t in filtered["tickets"])


def test_get_ticket_carries_full_reviewer_context(client):
    ticket_id = _first_open_ticket(client)["ticket_id"]
    r = client.get(f"/api/tickets/{ticket_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"]["ticket_id"] == ticket_id
    assert body["event"]["event_id"] == body["ticket"]["event_id"]
    assert isinstance(body["trail"], list) and body["trail"]
    assert client.get("/api/tickets/tkt_9999").status_code == 404


def test_assign_then_resolve_a_ticket(client):
    ticket_id = _first_open_ticket(client)["ticket_id"]

    r = client.post(f"/api/tickets/{ticket_id}/assign",
                    json={"employee_email": "asha@acme.com"})
    assert r.status_code == 200
    assert r.json()["ticket"]["status"] == "under_review"
    assert r.json()["ticket"]["assigned_employee_email"] == "asha@acme.com"

    # a ticket has exactly one owner
    again = client.post(f"/api/tickets/{ticket_id}/assign",
                        json={"employee_email": "bhavin@acme.com"})
    assert again.status_code == 409

    r = client.post(
        f"/api/tickets/{ticket_id}/resolve",
        json={"employee_email": "asha@acme.com", "outcome": "unresolved",
              "note": "Confirmed card testing; account blocked, no money to recover."},
    )
    assert r.status_code == 200
    ticket = r.json()["ticket"]
    assert ticket["status"] == "unresolved"
    assert "card testing" in ticket["resolution_note"]

    trail = client.get(f"/api/events/{ticket['event_id']}/audit").json()["trail"]
    actions = {row["action"]: row for row in trail}
    assert actions["resolved_review_ticket"]["agent"] == "human"


def test_resolving_with_money_credits_the_human(client):
    ticket_id = _first_open_ticket(client)["ticket_id"]
    client.post(f"/api/tickets/{ticket_id}/assign",
                json={"employee_email": "asha@acme.com"})

    before = client.get("/api/metrics").json()
    event = client.get(f"/api/tickets/{ticket_id}").json()["event"]
    outstanding = Decimal(event["amount"]) - Decimal(event["recovered_amount"])

    r = client.post(
        f"/api/tickets/{ticket_id}/resolve",
        json={"employee_email": "asha@acme.com", "outcome": "resolved",
              "note": "Called the customer; paid on the call via a fresh UPI link.",
              "recovered_amount": str(outstanding)},
    )
    assert r.status_code == 200

    after = client.get("/api/metrics").json()
    assert Decimal(after["human_recovered"]) == (
        Decimal(before["human_recovered"]) + outstanding
    )
    assert Decimal(after["ai_recovered"]) == Decimal(before["ai_recovered"])
    assert after["tickets"]["resolved"] > before["tickets"]["resolved"]


def test_ticket_endpoint_guards(client):
    ticket_id = _first_open_ticket(client)["ticket_id"]

    # cannot resolve before taking it
    r = client.post(f"/api/tickets/{ticket_id}/resolve",
                    json={"employee_email": "a@b.com", "outcome": "resolved",
                          "note": "skipped assignment"})
    assert r.status_code == 409

    assert client.post("/api/tickets/tkt_9999/assign",
                       json={"employee_email": "a@b.com"}).status_code == 404

    client.post(f"/api/tickets/{ticket_id}/assign", json={"employee_email": "a@b.com"})
    over = client.post(f"/api/tickets/{ticket_id}/resolve",
                       json={"employee_email": "a@b.com", "outcome": "resolved",
                             "note": "typo", "recovered_amount": "999999.00"})
    assert over.status_code == 409
    assert "at risk" in over.json()["detail"]


def test_raise_customer_question_opens_a_ticket(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    r = client.post(
        f"/api/events/{eid}/raise-question",
        json={"question": "Mera pichla refund kab tak aayega?",
              "channel": "voice_call", "employee_email": "asha@acme.com"},
    )
    assert r.status_code == 200
    ticket = r.json()["ticket"]
    assert ticket["reason"] == "customer_question"
    assert ticket["status"] == "open"
    assert "refund kab tak" in ticket["detail"]

    assert client.post("/api/events/nope/raise-question",
                       json={"question": "hi"}).status_code == 404
    assert client.post(f"/api/events/{eid}/raise-question",
                       json={"question": "hi", "channel": "pigeon"}).status_code == 422


# --- Simulate / Playground ---------------------------------------------
# The core guarantee: a full rehearsal, in either mode, changes nothing in
# the real store and nothing in MetricsBlock.

def _db_snapshot():
    with store.get_session() as s:
        return (
            len(store.all_events(s)),
            len(store.get_tickets(s)),
            len(store.get_audit_trail(s)),
        )


def _speaker_text(turn: dict) -> dict:
    """Drop any optional audio_base64 (present for real when SARVAM_API_KEY is
    live) so history-vs-turn comparisons aren't sensitive to whether TTS ran."""
    return {"speaker": turn["speaker"], "text": turn["text"]}


def test_playground_start_interactive(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    r = client.post(f"/api/events/{eid}/playground/start", json={"mode": "interactive"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "interactive"
    assert body["channel"] in ("call", "message")
    assert body["ticket_ref"].startswith("SIM-")
    assert body["opening_turn"]["speaker"] == "agent"
    assert body["outcome"] == "ongoing"
    assert body["history"] == [_speaker_text(body["opening_turn"])]
    assert {"name", "phone_masked", "bank_account_masked", "upi_vpa"} <= body["persona"].keys()

    assert client.post("/api/events/nope/playground/start", json={}).status_code == 404


def test_playground_interactive_message_reaches_an_outcome(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    started = client.post(f"/api/events/{eid}/playground/start", json={"mode": "interactive"}).json()

    r = client.post(
        f"/api/events/{eid}/playground/message",
        json={"history": started["history"], "message": "Haan theek hai, main abhi pay karta hoon",
              "channel": started["channel"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["turn"]["speaker"] == "agent"
    assert body["outcome"] in ("ongoing", "resolved", "escalated", "halted")
    assert body["history"][-1] == _speaker_text(body["turn"])

    assert client.post("/api/events/nope/playground/message",
                       json={"history": [], "message": "hi"}).status_code == 404


def test_playground_auto_mode_advances_two_ai_turns(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    started = client.post(f"/api/events/{eid}/playground/start", json={"mode": "auto"}).json()

    r = client.post(
        f"/api/events/{eid}/playground/advance",
        json={"history": started["history"], "channel": started["channel"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_turn"]["speaker"] == "customer"
    assert body["agent_turn"]["speaker"] == "agent"
    assert body["history"] == [
        *started["history"], _speaker_text(body["customer_turn"]), _speaker_text(body["agent_turn"]),
    ]

    assert client.post("/api/events/nope/playground/advance",
                       json={"history": []}).status_code == 404


def test_playground_never_touches_the_real_store_or_metrics(client):
    eid = client.get("/api/events").json()["events"][0]["event_id"]
    before_db = _db_snapshot()
    before_metrics = client.get("/api/metrics").json()

    started = client.post(f"/api/events/{eid}/playground/start", json={"mode": "auto"}).json()
    history = started["history"]
    for _ in range(3):
        adv = client.post(f"/api/events/{eid}/playground/advance",
                          json={"history": history, "channel": started["channel"]}).json()
        history = adv["history"]
        if adv["outcome"] != "ongoing":
            break
    client.post(f"/api/events/{eid}/playground/message",
               json={"history": history, "message": "haan theek hai", "channel": started["channel"]})

    assert _db_snapshot() == before_db
    assert client.get("/api/metrics").json() == before_metrics
