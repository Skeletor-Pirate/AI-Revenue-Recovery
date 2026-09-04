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
