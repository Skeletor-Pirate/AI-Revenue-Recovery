"""Sarvam AI text-to-speech for the Hinglish Voice Recovery Agent.

The Voice agent (``app/agents/voice.py``) writes a Hinglish call *script*; this
module turns each dialogue turn into natural Indian-accented speech via Sarvam
AI's ``bulbul`` TTS model. Sarvam is purpose-built for code-mixed Hindi/English,
so the recovery call sounds like a real Razorpay support agent instead of a
robotic browser voice.

Optional by design: with no ``SARVAM_API_KEY`` set, :func:`available` is
``False`` and :func:`synthesize_script` returns ``audio=[]`` — the dashboard
then falls back to the browser ``SpeechSynthesis`` voice. Test mode only; no
real calls are placed.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings, get_settings

_ENDPOINT = "https://api.sarvam.ai/text-to-speech"
_MAX_CHARS = 1500  # safe cap across bulbul:v2/v3


class SarvamTTSError(RuntimeError):
    """Sarvam TTS request failed."""


def available(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(s.sarvam_api_key)


def _speaker_for(speaker: str, s: Settings) -> str:
    return (
        s.sarvam_tts_speaker_agent
        if speaker.lower() == "agent"
        else s.sarvam_tts_speaker_customer
    )


def synthesize_turn(text: str, speaker: str, *, settings: Settings | None = None) -> str:
    """Return a base64 WAV clip for one dialogue turn. Raises on failure."""
    s = settings or get_settings()
    if not s.sarvam_api_key:
        raise SarvamTTSError("no SARVAM_API_KEY configured")

    payload: dict[str, Any] = {
        "text": text[:_MAX_CHARS],
        "target_language_code": s.sarvam_tts_language_code,
        "speaker": _speaker_for(speaker, s).lower(),
        "model": s.sarvam_tts_model,
        "speech_sample_rate": s.sarvam_tts_sample_rate,
        "output_audio_codec": "wav",
    }
    try:
        resp = httpx.post(
            _ENDPOINT,
            headers={"api-subscription-key": s.sarvam_api_key},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        audios = resp.json().get("audios") or []
    except (httpx.HTTPError, ValueError) as exc:  # network / JSON
        raise SarvamTTSError(str(exc)) from exc
    if not audios:
        raise SarvamTTSError("Sarvam returned no audio")
    return audios[0]


def synthesize_script(
    script: dict[str, Any], *, settings: Settings | None = None
) -> dict[str, Any]:
    """Synthesize every turn of a voice script.

    Returns ``{"available", "audio_format", "sample_rate", "provider", "audio"}``
    where ``audio`` is a list of ``{"index", "speaker", "audio_base64"}``. On any
    provider failure the call degrades to ``available=False`` with ``audio=[]``
    rather than raising — the caller (and dashboard) then use the browser voice.
    """
    s = settings or get_settings()
    turns = script.get("dialogue_turns") or []
    result: dict[str, Any] = {
        "available": False,
        "provider": "sarvam",
        "audio_format": "wav",
        "sample_rate": s.sarvam_tts_sample_rate,
        "audio": [],
        "reason": None,
    }
    if not available(s):
        result["reason"] = "no SARVAM_API_KEY configured"
        return result
    if not turns:
        result["reason"] = "script has no dialogue turns"
        return result

    clips: list[dict[str, Any]] = []
    try:
        for i, turn in enumerate(turns):
            clip = synthesize_turn(
                str(turn.get("text", "")), str(turn.get("speaker", "Agent")), settings=s
            )
            clips.append(
                {"index": i, "speaker": turn.get("speaker", "Agent"), "audio_base64": clip}
            )
    except SarvamTTSError as exc:
        # Diagnosable from the API response instead of only a silent
        # available=false -- a wrong speaker/model name for the configured
        # provider still degrades gracefully, but now says why.
        result["reason"] = str(exc)
        return result

    result["available"] = True
    result["audio"] = clips
    return result
