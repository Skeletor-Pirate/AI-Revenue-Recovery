"""Tests for Hinglish Voice Recovery Agent (app/agents/voice.py, voice_tts.py)."""

from decimal import Decimal

from app.agents import voice, voice_tts
from app.config import Settings
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


_SCRIPT = {
    "dialogue_turns": [
        {"speaker": "Agent", "text": "Namaste ji", "emotion": "polite"},
        {"speaker": "Customer", "text": "Haan ji", "emotion": "receptive"},
    ],
}


def test_tts_unavailable_without_key():
    s = Settings(sarvam_api_key=None)
    assert voice_tts.available(s) is False
    out = voice_tts.synthesize_script(_SCRIPT, settings=s)
    assert out["available"] is False
    assert out["audio"] == []


def test_tts_synthesizes_every_turn(monkeypatch):
    s = Settings(sarvam_api_key="test-key")
    calls: list[dict] = []

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"audios": ["QkFTRTY0"]}

    def _fake_post(url, headers, json, timeout):  # noqa: A002
        calls.append(json)
        assert headers["api-subscription-key"] == "test-key"
        return _Resp()

    monkeypatch.setattr(voice_tts.httpx, "post", _fake_post)
    out = voice_tts.synthesize_script(_SCRIPT, settings=s)

    assert out["available"] is True
    assert [c["index"] for c in out["audio"]] == [0, 1]
    assert calls[0]["speaker"] == s.sarvam_tts_speaker_agent
    assert calls[1]["speaker"] == s.sarvam_tts_speaker_customer


def test_tts_degrades_on_provider_error(monkeypatch):
    s = Settings(sarvam_api_key="test-key")

    def _boom(*a, **k):
        raise voice_tts.httpx.HTTPError("nope")

    monkeypatch.setattr(voice_tts.httpx, "post", _boom)
    out = voice_tts.synthesize_script(_SCRIPT, settings=s)
    assert out["available"] is False
    assert out["audio"] == []
