"""Provider-agnostic LLM client for the Diagnosis and Recovery agents.

Both agents use an LLM only as an *optional* enhancement (Diagnosis: classify a
free-text failure reason the rules missed; Recovery: draft outreach copy) and
both degrade to deterministic behaviour when no key is configured. This module
is the one place that talks to a model provider.

Providers (auto-detected in this order, or forced with ``LLM_PROVIDER``):

* ``anthropic``  — the native ``anthropic`` SDK, model ``ANTHROPIC_MODEL``.
* ``openrouter`` — OpenAI-compatible REST (``https://openrouter.ai/api/v1``),
  model ``OPENROUTER_MODEL`` (default a Claude model, keeping the "built on
  Claude" story). Uses ``httpx`` — no extra SDK.
* ``openai``     — OpenAI-compatible REST (``https://api.openai.com/v1``),
  model ``OPENAI_MODEL``.

``chat()`` returns plain text and raises ``LLMUnavailable`` when nothing is
configured; callers catch that and fall back.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import httpx

# --- embeddings (for the RAG knowledge base, app/rag.py) -----------------
EMBED_DIM = 384
_OPENAI_EMBED_MODEL = "text-embedding-3-small"        # supports `dimensions`
_LOCAL_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384-d, via fastembed
_local_embedder: Any = None


class LLMUnavailable(RuntimeError):
    """No LLM provider is configured (no API key)."""


def _get(settings: Any, name: str, default: Any = None) -> Any:
    return getattr(settings, name, default) if settings is not None else default


def resolve_provider(settings: Any) -> str | None:
    """Which provider to use, or ``None`` if no key is configured."""
    forced = _get(settings, "llm_provider")
    if forced:
        return str(forced).lower()
    if _get(settings, "anthropic_api_key"):
        return "anthropic"
    if _get(settings, "openrouter_api_key"):
        return "openrouter"
    if _get(settings, "openai_api_key"):
        return "openai"
    return None


def available(settings: Any) -> bool:
    provider = resolve_provider(settings)
    if provider is None:
        return False
    key_field = {
        "anthropic": "anthropic_api_key",
        "openrouter": "openrouter_api_key",
        "openai": "openai_api_key",
    }.get(provider)
    return bool(key_field and _get(settings, key_field))


def _openai_compatible(
    *, base_url: str, api_key: str, model: str, system: str | None,
    turns: list[dict[str, str]], max_tokens: int,
    extra_headers: dict[str, str] | None = None,
) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(turns)
    headers = {"Authorization": f"Bearer {api_key}"}
    if extra_headers:
        headers.update(extra_headers)
    resp = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "max_tokens": max_tokens},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _complete(
    system: str | None, turns: list[dict[str, str]], *, settings: Any, max_tokens: int
) -> str:
    """Shared provider dispatch for both `chat()` and `chat_turns()`.

    `turns` is the full conversation as ``[{"role": "user"|"assistant",
    "content": str}, ...]`` in chronological order; `system` is always passed
    separately (every provider here supports that split).
    """
    provider = resolve_provider(settings)
    if provider is None:
        raise LLMUnavailable("no LLM provider configured")

    if provider == "anthropic":
        api_key = _get(settings, "anthropic_api_key")
        if not api_key:
            raise LLMUnavailable("anthropic selected but no anthropic_api_key")
        import anthropic  # lazy

        client = anthropic.Anthropic(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": _get(settings, "anthropic_model", "claude-sonnet-5"),
            "max_tokens": max_tokens,
            "messages": turns,
        }
        if system:
            kwargs["system"] = system
        message = client.messages.create(**kwargs)
        return "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        ).strip()

    if provider == "openrouter":
        api_key = _get(settings, "openrouter_api_key")
        if not api_key:
            raise LLMUnavailable("openrouter selected but no openrouter_api_key")
        return _openai_compatible(
            base_url=_get(
                settings, "openrouter_base_url", "https://openrouter.ai/api/v1"
            ),
            api_key=api_key,
            model=_get(
                settings, "openrouter_model", "anthropic/claude-3.7-sonnet"
            ),
            system=system,
            turns=turns,
            max_tokens=max_tokens,
            extra_headers={
                "HTTP-Referer": "https://github.com/Space-Fighter/AI-Revenue-Recovery",
                "X-Title": "AI Revenue Recovery",
            },
        )

    if provider == "openai":
        api_key = _get(settings, "openai_api_key")
        if not api_key:
            raise LLMUnavailable("openai selected but no openai_api_key")
        return _openai_compatible(
            base_url=_get(settings, "openai_base_url", "https://api.openai.com/v1"),
            api_key=api_key,
            model=_get(settings, "openai_model", "gpt-4o-mini"),
            system=system,
            turns=turns,
            max_tokens=max_tokens,
        )

    raise LLMUnavailable(f"unknown LLM provider: {provider!r}")


def chat(
    system: str | None, user: str, *, settings: Any, max_tokens: int = 512
) -> str:
    """One-shot completion. Returns the model's text. Raises on no provider."""
    return _complete(
        system, [{"role": "user", "content": user}], settings=settings,
        max_tokens=max_tokens,
    )


def chat_turns(
    system: str | None,
    turns: list[dict[str, str]],
    *,
    settings: Any,
    max_tokens: int = 400,
) -> str:
    """Multi-turn completion for a live conversation (the Playground agent).

    `turns` is the conversation so far from *this* speaker's point of view:
    its own prior lines are ``"assistant"``, the other side's are ``"user"``,
    in chronological order — e.g. the Recovery Agent and the Customer/Business
    personas each call this with the same transcript relabelled from their own
    perspective, so each genuinely reacts to what the other just said instead
    of one model pre-writing both halves. Same provider auto-detection /
    `LLMUnavailable` as `chat()`.
    """
    return _complete(system, turns, settings=settings, max_tokens=max_tokens)


def model_label(settings: Any) -> str:
    """A short provider/model string for audit payloads."""
    provider = resolve_provider(settings)
    if provider == "anthropic":
        return f"anthropic/{_get(settings, 'anthropic_model', 'claude-sonnet-5')}"
    if provider == "openrouter":
        return _get(settings, "openrouter_model", "anthropic/claude-3.7-sonnet")
    if provider == "openai":
        return f"openai/{_get(settings, 'openai_model', 'gpt-4o-mini')}"
    return "none"


# --- embeddings ---------------------------------------------------------

def resolve_embed_provider(settings: Any) -> str | None:
    """Which embeddings backend to use, or ``None`` if none is available.

    OpenRouter has **no** embeddings endpoint, so an OpenRouter-only setup
    falls through to the local model. Order: OpenAI key -> local fastembed.
    """
    if _get(settings, "openai_api_key"):
        return "openai"
    if importlib.util.find_spec("fastembed") is not None:
        return "local"
    return None


def embeddings_available(settings: Any) -> bool:
    return resolve_embed_provider(settings) is not None


def embed_label(settings: Any) -> str:
    provider = resolve_embed_provider(settings)
    if provider == "openai":
        return f"openai/{_OPENAI_EMBED_MODEL}"
    if provider == "local":
        return f"fastembed/{_LOCAL_EMBED_MODEL}"
    return "none"


def _local_embed(texts: list[str]) -> list[list[float]]:
    global _local_embedder
    if _local_embedder is None:
        import os

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from fastembed import TextEmbedding  # lazy; downloads the model once

        _local_embedder = TextEmbedding(model_name=_LOCAL_EMBED_MODEL)
    return [list(map(float, v)) for v in _local_embedder.embed(list(texts))]


def embed(texts: list[str], *, settings: Any) -> list[list[float]]:
    """Embed `texts` into `EMBED_DIM`-dimensional vectors. Raises
    ``LLMUnavailable`` when no embeddings backend is configured."""
    provider = resolve_embed_provider(settings)
    if provider is None:
        raise LLMUnavailable("no embeddings backend (need OPENAI_API_KEY or fastembed)")

    if provider == "openai":
        api_key = _get(settings, "openai_api_key")
        base_url = _get(settings, "openai_base_url", "https://api.openai.com/v1")
        resp = httpx.post(
            f"{base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": _get(settings, "openai_embed_model", _OPENAI_EMBED_MODEL),
                "input": list(texts),
                "dimensions": EMBED_DIM,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    return _local_embed(texts)
