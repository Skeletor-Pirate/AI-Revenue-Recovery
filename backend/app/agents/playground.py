"""Simulate / Playground — a live, sandboxed rehearsal of a recovery outreach.

The four core agents plus Triage handle the *real* batch. This module answers
a different question: **can a person actually probe the AI and see how it
responds?** The existing Hinglish Voice script (``app/agents/voice.py``) is a
prerecorded transcript — one LLM call writes an entire scripted dialogue up
front, which is why it always reads the same way for a given case. Here, two
independently-prompted chat roles carry on a real turn-by-turn conversation:

* the **Resolver** — the Recovery Agent persona, grounded in the case's real
  amount / root cause / stopping rules, always the one deciding whether the
  conversation is ``resolved`` / ``escalated`` / ``halted``.
* the **Customer / Business** — a synthetic counterparty persona (an
  individual for B2C cases, an accounts-payable contact for B2B invoices),
  either played by a human tester (**interactive** mode) or by a second LLM
  call with its own system prompt (**auto** mode — "watch two AIs talk").

**This module is stateless and read-only against the store.** Every function
takes an already-fetched ``Event`` and a ``history`` list the caller passes
back in; nothing here calls ``insert_ticket``, ``update_event``, or
``log_action``. That is what makes the session a safe rehearsal: a judge
play-testing "yes I'll pay" can never move the real ``events``/``tickets``
tables or the batch's ``MetricsBlock`` — see AGENTS_CONTRACT.md and plan.md
§12. The frontend holds the transcript in memory and resends it each call.

Public API:
    pick_channel(event) -> "call" | "message"
    build_persona(event) -> dict                    # display context
    start_session(event, *, mode, settings) -> dict
    send_message(event, history, message, channel, *, settings) -> dict   # interactive
    advance_conversation(event, history, channel, *, settings) -> dict    # auto
"""

from __future__ import annotations

import json
import random
import re
from typing import Any, Literal

from app import llm
from app.agents import voice_tts
from app.config import Settings, get_settings
from app.db.store import Event, RootCause

Mode = Literal["interactive", "auto"]
Channel = Literal["call", "message"]
Outcome = Literal["ongoing", "resolved", "escalated", "halted"]

_VALID_OUTCOMES = ("ongoing", "resolved", "escalated", "halted")

# root_cause -> channel. Payment-failure causes suit a phone call (higher
# urgency, needs a real-time yes/no); nudges and B2B chasers suit a message.
# Mirrors the plain intervention-selection style already in recovery.py.
_CALL_CAUSES = {
    RootCause.INSUFFICIENT_FUNDS,
    RootCause.EXPIRED_INSTRUMENT,
    RootCause.BANK_DOWNTIME,
    RootCause.AUTH_FAILURE,
    RootCause.CARD_DECLINED,
    RootCause.SUSPECTED_FRAUD,  # a verification call, not a nudge
}

# root_cause -> a short character sketch for the Customer/Business persona,
# so different cases produce genuinely different conversations rather than a
# handful of templates repeating.
_DISPOSITIONS: dict[str, str] = {
    RootCause.INSUFFICIENT_FUNDS: "cooperative but a little embarrassed; short on cash until salary lands",
    RootCause.EXPIRED_INSTRUMENT: "willing but confused about how to update payment details",
    RootCause.BANK_DOWNTIME: "mildly annoyed, assumes it's a technical glitch on your side",
    RootCause.AUTH_FAILURE: "unsure what went wrong, needs simple guidance",
    RootCause.CARD_DECLINED: "a bit defensive, doesn't want to admit the card might be maxed out",
    RootCause.CHECKOUT_ABANDONED: "distracted, was comparing prices and got interrupted",
    RootCause.INVOICE_FORGOTTEN: "a business accounts-payable contact — busy, procedural, wants written confirmation",
    RootCause.SUSPECTED_FRAUD: "evasive, gives inconsistent details, doesn't recognise the transaction",
    RootCause.UNKNOWN: "neutral, waiting to hear what this is about",
}

_MASK_TAIL = 4


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    tail = value[-_MASK_TAIL:]
    return f"{'•' * max(len(value) - _MASK_TAIL, 4)}{tail}"


def pick_channel(event: Event) -> Channel:
    """Which outreach channel this case would realistically use."""
    return "call" if event.root_cause in _CALL_CAUSES else "message"


def _disposition_for(event: Event) -> str:
    rc = event.root_cause or RootCause.UNKNOWN.value
    return _DISPOSITIONS.get(rc, _DISPOSITIONS[RootCause.UNKNOWN])


def build_persona(event: Event) -> dict[str, Any]:
    """The display context shown before a session starts and fed into both
    system prompts. Every contact field is synthetic (see app/data/generate.py)."""
    is_business = str(event.event_type) == "overdue_invoice"
    return {
        "name": event.customer_name or event.customer_id,
        "phone_masked": _mask(event.customer_phone),
        "bank_account_masked": _mask(event.customer_bank_account),
        "upi_vpa": event.customer_upi_vpa,
        "amount": str(event.amount),
        "root_cause": str(event.root_cause) if event.root_cause else None,
        "event_type": str(event.event_type),
        "is_business": is_business,
        "disposition": _disposition_for(event),
    }


# --- system prompts --------------------------------------------------------

def _agent_system_prompt(event: Event, persona: dict[str, Any], channel: Channel) -> str:
    medium = "a phone call" if channel == "call" else "a WhatsApp message exchange"
    counterparty = "a business accounts-payable contact" if persona["is_business"] else "the customer"
    return (
        f"You are the Razorpay Recovery Agent, speaking with {counterparty} over {medium}. "
        f"This is a REHEARSAL for testing purposes, not a real customer interaction. "
        f"Case: {persona['event_type']} of Rs {persona['amount']}, root cause "
        f"{persona['root_cause'] or 'unknown'}. "
        "Speak in natural, warm Hinglish (Hindi in Roman script mixed with English business terms), "
        "concise, never robotic. "
        "Bounded authority: you may offer at most a 10% discount or a short extension; anything "
        "larger, or any request you're unsure about, needs human sign-off -> outcome 'escalated'. "
        "If the other side is hostile, evasive, or the story doesn't add up -> outcome 'halted'. "
        "If they agree to pay / the matter is settled -> outcome 'resolved'. Otherwise 'ongoing'. "
        "Reply with ONLY a JSON object: "
        '{"reply": "<your next line>", "outcome": "ongoing"|"resolved"|"escalated"|"halted", '
        '"reasoning": "<one short sentence, why this outcome>"}.'
    )


def _customer_system_prompt(event: Event, persona: dict[str, Any], channel: Channel) -> str:
    medium = "a phone call" if channel == "call" else "a WhatsApp chat"
    role = "a business's accounts-payable contact" if persona["is_business"] else "a Razorpay customer"
    return (
        f"You are {persona['name']}, {role}, on {medium} with Razorpay's recovery agent about a "
        f"{persona['event_type']} of Rs {persona['amount']}. "
        f"Your disposition: {persona['disposition']}. "
        "Speak in natural, casual Hinglish (Hindi in Roman script mixed with English), short lines, "
        "like a real person texting or talking, not a script. "
        "React to what the agent just said; don't repeat yourself. After a few exchanges, reach a "
        "natural conclusion (agree, ask to escalate, or push back) rather than dragging on forever. "
        "Reply with ONLY your next line of dialogue as plain text — no JSON, no quotes, no labels."
    )


def _opening_instruction(persona: dict[str, Any], channel: Channel) -> str:
    medium = "Open the call" if channel == "call" else "Open the WhatsApp message"
    return f"{medium}. Greet {persona['name']} by name and explain briefly why you're reaching out."


# --- JSON / text parsing (voice.py's fenced-JSON-with-fallback pattern) ---

def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    if t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _parse_agent_reply(text: str, fallback_reply: str) -> dict[str, Any]:
    try:
        data = json.loads(_strip_fences(text))
        reply = str(data.get("reply", "")).strip()
        outcome = str(data.get("outcome", "ongoing")).strip().lower()
        if outcome not in _VALID_OUTCOMES:
            outcome = "ongoing"
        reasoning = str(data.get("reasoning", "")).strip()
        if reply:
            return {"reply": reply, "outcome": outcome, "reasoning": reasoning}
    except Exception:
        pass
    return {"reply": fallback_reply, "outcome": "ongoing", "reasoning": ""}


# --- provider-turn conversion ----------------------------------------------

def _to_provider_turns(
    history: list[dict[str, str]], self_speaker: str
) -> list[dict[str, str]]:
    """`history` (chronological, {"speaker","text"}) from `self_speaker`'s own
    point of view: its own lines are "assistant", the other side's are "user".
    Drops a leading "assistant" turn (this speaker's own opening line, if
    present) -- Anthropic requires the conversation to start with "user"."""
    turns = [
        {"role": "assistant" if h["speaker"] == self_speaker else "user", "content": h["text"]}
        for h in history
    ]
    while turns and turns[0]["role"] == "assistant":
        turns.pop(0)
    return turns


# --- deterministic offline fallback ----------------------------------------
# No LLM key configured -> the feature must still run to a real conclusion,
# same "never raises, always degrades" contract as every other agent here.

_AGREE_WORDS = {"haan", "yes", "theek", "ok", "okay", "sure", "pay", "done", "kar", "thik"}
_FRAUD_WORDS = {"fraud", "scam", "nahi kiya", "wrong", "block", "galat"}
_UPSET_WORDS = {"angry", "gussa", "complaint", "manager", "escalate", "no", "nahi"}


def _fallback_agent_reply(persona: dict[str, Any], message: str, turn_index: int) -> dict[str, Any]:
    text = message.lower()
    if any(w in text for w in _FRAUD_WORDS):
        return {
            "reply": f"Samajh gaya {persona['name']} ji, main isse turant ek human reviewer ko bhej raha hoon.",
            "outcome": "escalated",
            "reasoning": "Customer disputes the transaction; needs human verification.",
        }
    if any(w in text for w in _UPSET_WORDS):
        return {
            "reply": "Bilkul, main ise human team ko forward kar deta hoon jo aapki behtar madad kar payenge.",
            "outcome": "escalated",
            "reasoning": "Customer asked for a person / pushed back beyond the agent's bounded authority.",
        }
    if any(w in text for w in _AGREE_WORDS):
        return {
            "reply": f"Shukriya {persona['name']} ji! Maine confirm kar diya hai, aapka case resolve ho gaya.",
            "outcome": "resolved",
            "reasoning": "Customer agreed to pay / confirmed resolution.",
        }
    if turn_index >= 3:
        return {
            "reply": "Koi baat nahi, main is case ko human review ke liye bhej deta hoon taaki hum aapki sahi madad kar sakein.",
            "outcome": "escalated",
            "reasoning": "No clear resolution after a few exchanges; handing off rather than looping.",
        }
    return {
        "reply": f"Samajh sakta hoon {persona['name']} ji. Kya main aapko turant ek payment link bhej doon?",
        "outcome": "ongoing",
        "reasoning": "",
    }


def _fallback_customer_reply(persona: dict[str, Any], turn_index: int) -> str:
    lines = [
        "Haan bataiye, kya baat hai?",
        "Achha theek hai, thoda samajh nahi aaya, aap detail mein bata sakte hain?",
        "Theek hai, mujhe lagta hai main abhi pay kar sakta hoon.",
    ]
    return lines[min(turn_index, len(lines) - 1)]


def _fallback_opening(persona: dict[str, Any], channel: Channel) -> str:
    greeting = "Namaste" if not persona["is_business"] else "Namaste, Razorpay Recovery se bol raha hoon"
    return (
        f"{greeting} {persona['name']} ji! Aapka Rs {persona['amount']} ka "
        f"{persona['event_type'].replace('_', ' ')} pending hai. Kya hum iske baare mein baat kar sakte hain?"
    )


# --- optional Sarvam speech for "call" channel ------------------------------

def _speak(text: str, speaker: str, channel: Channel, settings: Settings) -> str | None:
    """Base64 WAV for one line, or None. Never raises -- same degrade-gracefully
    contract as everywhere else Sarvam is used (app/agents/voice_tts.py)."""
    if channel != "call" or not voice_tts.available(settings):
        return None
    try:
        return voice_tts.synthesize_turn(text, speaker, settings=settings)
    except voice_tts.SarvamTTSError:
        return None


def _with_audio(turn: dict[str, Any], channel: Channel, settings: Settings) -> dict[str, Any]:
    clip = _speak(turn["text"], "Agent" if turn["speaker"] == "agent" else "Customer", channel, settings)
    if clip:
        turn = {**turn, "audio_base64": clip}
    return turn


# --- ticket reference (cosmetic only -- never a DB row) ---------------------

def _ticket_ref(event: Event) -> str:
    suffix = event.event_id[-4:].upper().replace("_", "")
    return f"SIM-{suffix}{random.randint(100, 999)}"


# --- public API --------------------------------------------------------

def start_session(
    event: Event, *, mode: Mode, settings: Settings | None = None
) -> dict[str, Any]:
    """Open a rehearsal session. Never writes to the store."""
    s = settings or get_settings()
    channel = pick_channel(event)
    persona = build_persona(event)

    opening_text = _fallback_opening(persona, channel)
    if llm.available(s):
        try:
            opening_text = llm.chat(
                _agent_system_prompt(event, persona, channel),
                _opening_instruction(persona, channel),
                settings=s,
                max_tokens=200,
            ).strip() or opening_text
        except Exception:
            pass

    opening_turn = {"speaker": "agent", "text": opening_text}
    return {
        "mode": mode,
        "channel": channel,
        "ticket_ref": _ticket_ref(event),
        "persona": persona,
        # audio only on the turn shown to the caller right now; `history`
        # (resent on every later call) stays plain text -- no point paying to
        # regenerate/re-transmit audio for lines already spoken.
        "opening_turn": _with_audio(opening_turn, channel, s),
        "outcome": "ongoing",
        "history": [opening_turn],
    }


def send_message(
    event: Event,
    history: list[dict[str, str]],
    message: str,
    channel: Channel,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Interactive mode: the tester's own line, then one Agent turn."""
    s = settings or get_settings()
    persona = build_persona(event)
    new_history = [*history, {"speaker": "customer", "text": message}]
    turn_index = sum(1 for h in new_history if h["speaker"] == "customer")

    result = _fallback_agent_reply(persona, message, turn_index)
    if llm.available(s):
        try:
            turns = _to_provider_turns(new_history, self_speaker="agent")
            raw = llm.chat_turns(
                _agent_system_prompt(event, persona, channel), turns, settings=s
            )
            result = _parse_agent_reply(raw, result["reply"])
        except Exception:
            pass

    agent_turn = {"speaker": "agent", "text": result["reply"]}
    new_history.append(agent_turn)
    return {
        "turn": _with_audio(agent_turn, channel, s),
        "outcome": result["outcome"],
        "reasoning": result["reasoning"],
        "history": new_history,
    }


def advance_conversation(
    event: Event,
    history: list[dict[str, str]],
    channel: Channel,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Auto mode: one Customer turn, then one Agent turn -- two distinctly
    prompted LLM calls, each reacting to the real transcript so far."""
    s = settings or get_settings()
    persona = build_persona(event)
    turn_index = sum(1 for h in history if h["speaker"] == "customer")

    customer_text = _fallback_customer_reply(persona, turn_index)
    if llm.available(s):
        try:
            turns = _to_provider_turns(history, self_speaker="customer")
            reply = llm.chat_turns(
                _customer_system_prompt(event, persona, channel), turns, settings=s,
                max_tokens=200,
            ).strip()
            if reply:
                customer_text = reply
        except Exception:
            pass

    customer_turn = {"speaker": "customer", "text": customer_text}
    history_with_customer = [*history, customer_turn]

    agent_result = _fallback_agent_reply(persona, customer_text, turn_index + 1)
    if llm.available(s):
        try:
            turns = _to_provider_turns(history_with_customer, self_speaker="agent")
            raw = llm.chat_turns(
                _agent_system_prompt(event, persona, channel), turns, settings=s
            )
            agent_result = _parse_agent_reply(raw, agent_result["reply"])
        except Exception:
            pass

    agent_turn = {"speaker": "agent", "text": agent_result["reply"]}
    new_history = [*history_with_customer, agent_turn]
    return {
        # auto mode speaks BOTH voices when the channel is a call, via
        # sarvam_tts_speaker_agent / _customer -- sounds like the old
        # prerecorded two-voice transcript, just generated live.
        "customer_turn": _with_audio(customer_turn, channel, s),
        "agent_turn": _with_audio(agent_turn, channel, s),
        "outcome": agent_result["outcome"],
        "reasoning": agent_result["reasoning"],
        "history": new_history,
    }
