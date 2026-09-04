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

_ROOT_CAUSE_EXPLANATIONS: dict[str, str] = {
    RootCause.INSUFFICIENT_FUNDS: "Aapke bank account mein transaction ke waqt insufficient balance tha, is wajah se bank ne request decline kar di thi.",
    RootCause.EXPIRED_INSTRUMENT: "Aapka registered card ya autopay mandate expire ho chuka hai, isliye automatic charge fail ho gaya.",
    RootCause.BANK_DOWNTIME: "Aapke bank ke server mein temporary technical downtime tha, is wajah se gateway timeout ho gaya.",
    RootCause.AUTH_FAILURE: "OTP verification ya 3D Secure bank authentication timeout ho gaya tha.",
    RootCause.CARD_DECLINED: "Aapke card issuing bank ne security limits ya online payment permissions ki wajah se transaction decline kiya tha.",
    RootCause.CHECKOUT_ABANDONED: "Aap checkout page par the par payment capture hone se pehle browser session close/drop ho gaya tha.",
    RootCause.INVOICE_FORGOTTEN: "Aapki B2B invoice due date cross ho chuki hai aur system mein unpaid mark hui hai.",
    RootCause.SUSPECTED_FRAUD: "Kuch unusual activity patterns detect hone par payment risk check par hold ho gayi thi.",
    RootCause.UNKNOWN: "Payment gateway par ek temporary error aaya tha aur transaction verify nahi ho paya.",
}

_FRAUD_WORDS = {"fraud", "scam", "nahi kiya", "wrong", "block", "galat", "hacked", "police"}
_UPSET_WORDS = {"angry", "gussa", "complaint", "manager", "escalate", "bad service", "consumer court"}
_QUESTION_WORDS = {"kyu", "why", "kaise", "reason", "kya hua", "fail", "problem", "issue", "batao", "bataiye", "detail", "explain", "samjhao"}
_AGREE_PHRASES = {"pay kar", "link bhej", "bhej do", "bhejo", "kar deta hoon", "karta hoon", "kar dunga", "ready to pay", "sure send", "yes send", "send link", "paid", "payment kar"}


def _fallback_agent_reply(persona: dict[str, Any], message: str, turn_index: int) -> dict[str, Any]:
    text = message.lower().strip()

    # 1. Fraud or security dispute -> escalate immediately to human review
    if any(w in text for w in _FRAUD_WORDS):
        return {
            "reply": f"Samajh gaya {persona['name']} ji. Main is transaction ko turant hold par daalkar human fraud verification team ko escalate kar raha hoon.",
            "outcome": "escalated",
            "reasoning": "Customer disputes transaction / suspects fraud; halted for human review.",
        }

    # 2. Hostile / Manager demand -> escalate
    if any(w in text for w in _UPSET_WORDS):
        return {
            "reply": "Bilkul {persona['name']} ji, main is case ko senior review team ko forward kar raha hoon jo aapse directly connect karenge.",
            "outcome": "escalated",
            "reasoning": "Customer requested human supervisor / expressed dissatisfaction.",
        }

    # 3. Questions asking why it failed / inquiry ("kyu hua", "fail kyu hua", "why")
    has_question = any(w in text for w in _QUESTION_WORDS) or "?" in text
    if has_question:
        rc = persona.get("root_cause") or RootCause.UNKNOWN
        explanation = _ROOT_CAUSE_EXPLANATIONS.get(rc, "Gateway par temporary network error aaya tha.")
        return {
            "reply": f"{explanation} Kya main aapko ek secure Razorpay payment link bhej doon taaki aap ise easily settle kar sakein?",
            "outcome": "ongoing",
            "reasoning": f"Explained failure root cause ({rc}) in response to customer inquiry.",
        }

    # 4. Genuine agreement to pay / proceed
    has_agreement = any(p in text for p in _AGREE_PHRASES) or (
        text in {"haan", "yes", "theek hai", "ok", "okay", "sure", "done"} and not has_question
    )
    if has_agreement:
        return {
            "reply": f"Shukriya {persona['name']} ji! Maine Rs {persona['amount']} ka secure Razorpay payment link send kar diya hai: https://rzp.io/i/rec_{persona.get('amount', 0)}. Payment complete hote hi receipt mil jayegi.",
            "outcome": "resolved",
            "reasoning": "Customer agreed to pay / requested payment link.",
        }

    # 5. Stalled without resolution after multiple turns
    if turn_index >= 3:
        return {
            "reply": f"Koi baat nahi {persona['name']} ji, main is case ko human review queue mein daal deta hoon taaki hamari accounts team aapse call par connect kar sake.",
            "outcome": "escalated",
            "reasoning": "No resolution reached after multi-turn exchange; handing off to human queue.",
        }

    return {
        "reply": f"Samajh sakta hoon {persona['name']} ji. Aapka Rs {persona['amount']} ka payment pending hai. Kya aap abhi UPI ya card se retry karna chahenge?",
        "outcome": "ongoing",
        "reasoning": "",
    }


def _fallback_customer_reply(persona: dict[str, Any], turn_index: int) -> str:
    lines = [
        "Haan bataiye, transaction fail kyu hua tha?",
        "Achha theek hai, ab samajh aaya. Kya aap link bhej sakte hain?",
        "Haan theek hai, main abhi payment link se pay kar deta hoon.",
    ]
    return lines[min(turn_index, len(lines) - 1)]


def _fallback_opening(persona: dict[str, Any], channel: Channel) -> str:
    greeting = "Namaste" if not persona["is_business"] else "Namaste, Razorpay Recovery se bol raha hoon"
    medium_desc = "call" if channel == "call" else "WhatsApp notification"
    return (
        f"{greeting} {persona['name']} ji! Yeh aapke Rs {persona['amount']} ke "
        f"{persona['event_type'].replace('_', ' ')} ke regarding {medium_desc} hai. Kya hum iske baare mein 2 minute baat kar sakte hain?"
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
    event: Event,
    *,
    mode: Mode,
    channel: Channel | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Open a rehearsal session. Never writes to the store."""
    s = settings or get_settings()
    active_channel = channel if channel in ("call", "message") else pick_channel(event)
    persona = build_persona(event)

    opening_text = _fallback_opening(persona, active_channel)
    if llm.available(s):
        try:
            opening_text = llm.chat(
                _agent_system_prompt(event, persona, active_channel),
                _opening_instruction(persona, active_channel),
                settings=s,
                max_tokens=200,
            ).strip() or opening_text
        except Exception:
            pass

    opening_turn = {"speaker": "agent", "text": opening_text}
    return {
        "mode": mode,
        "channel": active_channel,
        "ticket_ref": _ticket_ref(event),
        "persona": persona,
        # audio only on the turn shown to the caller right now; `history`
        # (resent on every later call) stays plain text -- no point paying to
        # regenerate/re-transmit audio for lines already spoken.
        "opening_turn": _with_audio(opening_turn, active_channel, s),
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
