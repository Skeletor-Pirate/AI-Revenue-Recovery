"""Provider selection for the shared LLM client. No network, no DB."""

from types import SimpleNamespace

import pytest

from app import llm


def test_no_provider_when_no_keys():
    s = SimpleNamespace()
    assert llm.resolve_provider(s) is None
    assert llm.available(s) is False
    assert llm.model_label(s) == "none"
    with pytest.raises(llm.LLMUnavailable):
        llm.chat(None, "hi", settings=s)


def test_auto_detects_in_priority_order():
    assert llm.resolve_provider(SimpleNamespace(anthropic_api_key="a")) == "anthropic"
    assert llm.resolve_provider(SimpleNamespace(openrouter_api_key="o")) == "openrouter"
    assert llm.resolve_provider(SimpleNamespace(openai_api_key="p")) == "openai"
    both = SimpleNamespace(anthropic_api_key="a", openrouter_api_key="o")
    assert llm.resolve_provider(both) == "anthropic"


def test_explicit_provider_override_wins():
    s = SimpleNamespace(llm_provider="openrouter", anthropic_api_key="a", openrouter_api_key="o")
    assert llm.resolve_provider(s) == "openrouter"


def test_model_label_per_provider():
    assert llm.model_label(
        SimpleNamespace(openrouter_api_key="o", openrouter_model="anthropic/claude-3.7-sonnet")
    ) == "anthropic/claude-3.7-sonnet"
    assert llm.model_label(
        SimpleNamespace(openai_api_key="p", openai_model="gpt-4o-mini")
    ) == "openai/gpt-4o-mini"


def test_available_false_when_forced_provider_has_no_key():
    s = SimpleNamespace(llm_provider="openai")  # no openai_api_key
    assert llm.resolve_provider(s) == "openai"
    assert llm.available(s) is False


def test_chat_turns_raises_llmunavailable_with_no_provider():
    with pytest.raises(llm.LLMUnavailable):
        llm.chat_turns(None, [{"role": "user", "content": "hi"}], settings=SimpleNamespace())


def test_chat_turns_sends_the_full_turn_list_openai_compatible(monkeypatch):
    captured = {}

    def _fake_post(url, headers, json, timeout):  # noqa: A002
        captured["url"] = url
        captured["json"] = json

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": " next line "}}]}

        return _Resp()

    monkeypatch.setattr(llm.httpx, "post", _fake_post)
    s = SimpleNamespace(openai_api_key="k", openai_model="gpt-4o-mini")
    turns = [
        {"role": "user", "content": "Namaste"},
        {"role": "assistant", "content": "Namaste ji"},
        {"role": "user", "content": "Kaise ho?"},
    ]
    out = llm.chat_turns("system prompt", turns, settings=s)

    assert out == "next line"
    assert captured["json"]["messages"][0] == {"role": "system", "content": "system prompt"}
    assert captured["json"]["messages"][1:] == turns
    assert "chat/completions" in captured["url"]


def test_chat_still_one_shot_after_refactor(monkeypatch):
    """chat() must keep working unchanged for its existing callers (voice.py, recovery.py)."""
    captured = {}

    def _fake_post(url, headers, json, timeout):  # noqa: A002
        captured["json"] = json

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "hello"}}]}

        return _Resp()

    monkeypatch.setattr(llm.httpx, "post", _fake_post)
    s = SimpleNamespace(openai_api_key="k", openai_model="gpt-4o-mini")
    assert llm.chat("sys", "user msg", settings=s) == "hello"
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user msg"},
    ]
