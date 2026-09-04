"""Tests for the Simulate / Playground agent (app/agents/playground.py).

Run from backend/:  uv run pytest -q tests/test_playground.py

No DB session is needed for most of these -- the module is stateless and
read-only against the store, taking an already-constructed `Event`. The
sandboxing-guarantee tests use the `session` fixture specifically to prove
that a full session, in either mode, never touches events/tickets/audit_log.
"""

from decimal import Decimal

import pytest

from app.agents import playground as pg
from app.config import Settings
from app.db import store
from app.db.store import Event, EventType, RootCause


def _event(**overrides) -> Event:
    defaults = dict(
        event_id="evt_pg1",
        event_type=EventType.FAILED_PAYMENT,
        customer_id="cust_1",
        amount=Decimal("1500.00"),
        customer_name="Aryan Maharaj",
        customer_phone="+917890779946",
        customer_bank_account="41414442605456",
        customer_upi_vpa="aryanm@okhdfcbank",
        root_cause=RootCause.INSUFFICIENT_FUNDS,
    )
    defaults.update(overrides)
    return Event(**defaults)


_NO_LLM = Settings(
    anthropic_api_key=None, openrouter_api_key=None, openai_api_key=None,
    sarvam_api_key=None,  # keep these tests offline -- no live TTS calls either
)


# --- pick_channel ------------------------------------------------------

@pytest.mark.parametrize(
    "root_cause,expected",
    [
        (RootCause.INSUFFICIENT_FUNDS, "call"),
        (RootCause.EXPIRED_INSTRUMENT, "call"),
        (RootCause.BANK_DOWNTIME, "call"),
        (RootCause.AUTH_FAILURE, "call"),
        (RootCause.CARD_DECLINED, "call"),
        (RootCause.SUSPECTED_FRAUD, "call"),
        (RootCause.CHECKOUT_ABANDONED, "message"),
        (RootCause.INVOICE_FORGOTTEN, "message"),
        (RootCause.UNKNOWN, "message"),
        (None, "message"),
    ],
)
def test_pick_channel_mapping(root_cause, expected):
    assert pg.pick_channel(_event(root_cause=root_cause)) == expected


# --- persona -------------------------------------------------------------

def test_persona_masks_contact_details():
    persona = pg.build_persona(_event())
    assert persona["name"] == "Aryan Maharaj"
    assert persona["phone_masked"].endswith("9946")
    assert "•" in persona["phone_masked"]
    assert persona["bank_account_masked"].endswith("5456")  # last 4 digits of 41414442605456
    assert "•" in persona["bank_account_masked"]
    assert persona["upi_vpa"] == "aryanm@okhdfcbank"
    assert persona["is_business"] is False


def test_persona_flags_overdue_invoice_as_business():
    persona = pg.build_persona(_event(event_type=EventType.OVERDUE_INVOICE, root_cause=RootCause.INVOICE_FORGOTTEN))
    assert persona["is_business"] is True
    assert "accounts-payable" in persona["disposition"] or "business" in persona["disposition"]


def test_persona_never_exposes_raw_bank_account():
    persona = pg.build_persona(_event())
    assert "41414442605456" not in str(persona)


# --- deterministic offline fallback (no LLM key) --------------------------

def test_start_session_works_without_an_llm_key():
    session = pg.start_session(_event(), mode="interactive", settings=_NO_LLM)
    assert session["mode"] == "interactive"
    assert session["channel"] == "call"
    assert session["ticket_ref"].startswith("SIM-")
    assert session["opening_turn"]["speaker"] == "agent"
    assert session["opening_turn"]["text"]
    assert session["outcome"] == "ongoing"
    assert session["history"] == [session["opening_turn"]]


def test_interactive_fallback_reaches_ptp():
    event = _event()
    session = pg.start_session(event, mode="interactive", settings=_NO_LLM)
    result = pg.send_message(
        event, session["history"], "Haan theek hai, main abhi pay karta hoon", "call",
        settings=_NO_LLM,
    )
    assert result["outcome"] == "ptp_promised"
    assert result["turn"]["speaker"] == "agent"
    assert result["history"][-1] == result["turn"]
    assert result["history"][-2] == {"speaker": "customer", "text": "Haan theek hai, main abhi pay karta hoon"}

    # Now simulate customer clicking link and completing payment (payment.captured)
    paid = pg.simulate_payment(event, result["history"], "call", settings=_NO_LLM)
    assert paid["outcome"] == "resolved"
    assert "pay_sim_" in paid["payment_id"]
    assert paid["history"][-1] == paid["turn"]


def test_interactive_fallback_escalates_on_fraud_dispute():
    event = _event()
    session = pg.start_session(event, mode="interactive", settings=_NO_LLM)
    result = pg.send_message(
        event, session["history"], "Ye fraud hai, maine kuch nahi kiya", "call", settings=_NO_LLM,
    )
    assert result["outcome"] == "escalated"


def test_interactive_fallback_never_loops_forever():
    """After a few ambiguous turns the deterministic fallback hands off rather
    than looping -- an offline demo must still reach a real conclusion."""
    event = _event()
    history = pg.start_session(event, mode="interactive", settings=_NO_LLM)["history"]
    outcome = "ongoing"
    for _ in range(6):
        result = pg.send_message(event, history, "hmm not sure", "call", settings=_NO_LLM)
        history = result["history"]
        outcome = result["outcome"]
        if outcome != "ongoing":
            break
    assert outcome != "ongoing"


def test_auto_mode_fallback_produces_both_turns():
    event = _event()
    history = pg.start_session(event, mode="auto", settings=_NO_LLM)["history"]
    result = pg.advance_conversation(event, history, "call", settings=_NO_LLM)
    assert result["customer_turn"]["speaker"] == "customer"
    assert result["agent_turn"]["speaker"] == "agent"
    assert result["history"] == [*history, result["customer_turn"], result["agent_turn"]]


def test_auto_mode_fallback_reaches_a_terminal_outcome():
    event = _event()
    history = pg.start_session(event, mode="auto", settings=_NO_LLM)["history"]
    outcome = "ongoing"
    for _ in range(6):
        result = pg.advance_conversation(event, history, "call", settings=_NO_LLM)
        history = result["history"]
        outcome = result["outcome"]
        if outcome != "ongoing":
            break
    assert outcome != "ongoing"


# --- LLM path (monkeypatched, no network) ---------------------------------

def test_interactive_uses_distinct_agent_prompt(monkeypatch):
    """The Agent persona is a real chat_turns() call with its own system prompt."""
    event = _event()
    captured = {}

    def _fake_chat(system, user, *, settings, max_tokens=512):
        return "opening line"

    def _fake_chat_turns(system, turns, *, settings, max_tokens=400):
        captured["system"] = system
        captured["turns"] = turns
        return '{"reply": "Theek hai, main samajh gaya", "outcome": "resolved", "reasoning": "customer agreed"}'

    monkeypatch.setattr(pg.llm, "available", lambda s: True)
    monkeypatch.setattr(pg.llm, "chat", _fake_chat)
    monkeypatch.setattr(pg.llm, "chat_turns", _fake_chat_turns)

    session = pg.start_session(event, mode="interactive", settings=_NO_LLM)
    result = pg.send_message(event, session["history"], "Haan pay kar dunga", "call", settings=_NO_LLM)

    assert result["turn"]["text"] == "Theek hai, main samajh gaya"
    assert result["outcome"] == "resolved"
    assert result["reasoning"] == "customer agreed"
    assert "Recovery Agent" in captured["system"]
    # first turn sent to the provider must be role=user (the opening line was
    # dropped) -- Anthropic requires the conversation to start with "user"
    assert captured["turns"][0]["role"] == "user"


def test_auto_mode_calls_two_distinctly_prompted_llms(monkeypatch):
    event = _event()
    system_prompts_seen = []

    def _fake_chat(system, user, *, settings, max_tokens=512):
        return "opening line"

    def _fake_chat_turns(system, turns, *, settings, max_tokens=400):
        system_prompts_seen.append(system)
        if "Recovery Agent" in system:
            return '{"reply": "Agent line", "outcome": "ongoing", "reasoning": ""}'
        return "Customer line"

    monkeypatch.setattr(pg.llm, "available", lambda s: True)
    monkeypatch.setattr(pg.llm, "chat", _fake_chat)
    monkeypatch.setattr(pg.llm, "chat_turns", _fake_chat_turns)

    history = pg.start_session(event, mode="auto", settings=_NO_LLM)["history"]
    result = pg.advance_conversation(event, history, "call", settings=_NO_LLM)

    assert result["customer_turn"]["text"] == "Customer line"
    assert result["agent_turn"]["text"] == "Agent line"
    # two genuinely different system prompts were used, one per persona
    assert len(system_prompts_seen) == 2
    assert system_prompts_seen[0] != system_prompts_seen[1]


def test_customer_disposition_varies_by_root_cause(monkeypatch):
    seen = []
    monkeypatch.setattr(pg.llm, "available", lambda s: True)
    monkeypatch.setattr(pg.llm, "chat", lambda *a, **k: "opening")

    def _fake_chat_turns(system, turns, *, settings, max_tokens=400):
        if "Recovery Agent" not in system:
            seen.append(system)
        return '{"reply": "x", "outcome": "ongoing", "reasoning": ""}'

    monkeypatch.setattr(pg.llm, "chat_turns", _fake_chat_turns)

    for rc in (RootCause.INSUFFICIENT_FUNDS, RootCause.SUSPECTED_FRAUD, RootCause.INVOICE_FORGOTTEN):
        event = _event(root_cause=rc, event_type=(
            EventType.OVERDUE_INVOICE if rc == RootCause.INVOICE_FORGOTTEN else EventType.FAILED_PAYMENT
        ))
        history = pg.start_session(event, mode="auto", settings=_NO_LLM)["history"]
        pg.advance_conversation(event, history, pg.pick_channel(event), settings=_NO_LLM)

    assert len(set(seen)) == 3  # three distinct root causes -> three distinct prompts


def test_malformed_llm_json_degrades_to_fallback(monkeypatch):
    """A bad/partial LLM response must never raise -- same contract as voice.py."""
    event = _event()
    monkeypatch.setattr(pg.llm, "available", lambda s: True)
    monkeypatch.setattr(pg.llm, "chat", lambda *a, **k: "opening")
    monkeypatch.setattr(pg.llm, "chat_turns", lambda *a, **k: "not json at all")

    session = pg.start_session(event, mode="interactive", settings=_NO_LLM)
    result = pg.send_message(event, session["history"], "haan theek hai", "call", settings=_NO_LLM)
    assert result["outcome"] in ("ongoing", "resolved")  # fallback text used, never raises
    assert result["turn"]["text"]


def test_llm_call_raising_degrades_to_fallback(monkeypatch):
    event = _event()
    monkeypatch.setattr(pg.llm, "available", lambda s: True)

    def _boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(pg.llm, "chat", _boom)
    monkeypatch.setattr(pg.llm, "chat_turns", _boom)

    session = pg.start_session(event, mode="interactive", settings=_NO_LLM)
    assert session["opening_turn"]["text"]  # fell back to the deterministic opening
    result = pg.send_message(event, session["history"], "haan", "call", settings=_NO_LLM)
    assert result["turn"]["text"]


# --- sandboxing guarantee ---------------------------------------------------
# The single most important property of this module: a rehearsal, in either
# mode, must never touch the real events / tickets / audit_log tables.

def _snapshot(session):
    return (
        len(store.all_events(session)),
        len(store.get_tickets(session)),
        len(store.get_audit_trail(session)),
    )


def test_interactive_session_never_writes_to_the_store(session):
    store.insert_event(
        session, event_id="evt_sim1", event_type=store.EventType.FAILED_PAYMENT,
        customer_id="c1", amount=Decimal("500.00"),
    )
    store.update_event(session, "evt_sim1", root_cause=store.RootCause.INSUFFICIENT_FUNDS)
    event = store.get_event(session, "evt_sim1")
    before = _snapshot(session)

    s = pg.start_session(event, mode="interactive", settings=_NO_LLM)
    history = s["history"]
    for msg in ("kya hua?", "haan theek hai main pay kar dunga"):
        result = pg.send_message(event, history, msg, s["channel"], settings=_NO_LLM)
        history = result["history"]

    assert _snapshot(session) == before


def test_auto_session_never_writes_to_the_store(session):
    store.insert_event(
        session, event_id="evt_sim2", event_type=store.EventType.OVERDUE_INVOICE,
        customer_id="c2", amount=Decimal("9000.00"),
    )
    store.update_event(session, "evt_sim2", root_cause=store.RootCause.INVOICE_FORGOTTEN)
    event = store.get_event(session, "evt_sim2")
    before = _snapshot(session)

    s = pg.start_session(event, mode="auto", settings=_NO_LLM)
    history = s["history"]
    for _ in range(4):
        result = pg.advance_conversation(event, history, s["channel"], settings=_NO_LLM)
        history = result["history"]
        if result["outcome"] != "ongoing":
            break

    assert _snapshot(session) == before
