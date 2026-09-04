"""Tests for Hinglish Voice Recovery Agent (app/agents/voice.py)."""

from decimal import Decimal

from app.agents import voice
from app.db.store import Event, EventType, RootCause


def test_fallback_hinglish_voice_script():
    event = Event(
        event_id="evt_test_voice1",
        event_type=EventType.FAILED_PAYMENT,
        customer_id="cust_rahul",
        amount=Decimal("1499.00"),
        root_cause=RootCause.INSUFFICIENT_FUNDS,
    )
    script = voice.generate_hinglish_voice_script(event)
    assert "dialogue_turns" in script
    assert len(script["dialogue_turns"]) >= 3
    assert "Namaste cust_rahul ji" in script["dialogue_turns"][0]["text"]
    assert "whatsapp_followup_hinglish" in script
    assert "https://rzp.io/i/evt_test_voice1" in script["whatsapp_followup_hinglish"]


def test_expired_mandate_voice_script():
    event = Event(
        event_id="evt_test_voice2",
        event_type=EventType.EXPIRED_MANDATE,
        customer_id="cust_priya",
        amount=Decimal("3500.00"),
        root_cause=RootCause.EXPIRED_INSTRUMENT,
    )
    script = voice.generate_hinglish_voice_script(event)
    assert len(script["dialogue_turns"]) >= 2
    assert "mandate" in script["dialogue_turns"][0]["text"].lower()
