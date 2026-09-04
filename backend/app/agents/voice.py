"""Hinglish Voice Recovery Agent — Direction 6.

Generates culturally natural, empathetic, and action-oriented Hinglish phone-call
dialogues and multi-channel outreach scripts for Indian merchants and buyers.

Features:
- Multi-turn interactive voice response (IVR) / AI agent phone call scripts.
- Natural code-switching (Hindi + English) reflecting everyday Razorpay merchant communication.
- Provider-agnostic LLM generation (Anthropic Claude / OpenRouter / OpenAI) with deterministic offline fallbacks.
- Structured script output: dialogue turns, estimated call duration, and actionable call outcomes.
"""

from __future__ import annotations

from typing import Any

from app import llm
from app.config import Settings, get_settings
from app.db.store import Event, RootCause


def _build_hinglish_prompt(event: Event) -> tuple[str, str]:
    """Construct system and user prompts for Hinglish recovery dialogue generation."""
    system = (
        "You are an empathetic, polite AI recovery agent calling an Indian customer on behalf of Razorpay. "
        "Speak in natural, conversational Hinglish (Hindi written in Roman script mixed with English business terms). "
        "Keep the tone respectful, solution-oriented, and reassuring. Avoid robotic translations. "
        "Output ONLY a JSON object with keys: "
        "'script_summary', 'dialogue_turns' (list of {'speaker': 'Agent'|'Customer', 'text': str, 'emotion': str}), "
        "'estimated_duration_sec' (int), and 'whatsapp_followup_hinglish' (str)."
    )

    amount_str = f"Rs {event.amount:,.2f}"
    cause_str = event.root_cause or "payment issue"
    user = (
        f"Generate a brief phone call script for customer {event.customer_id} regarding a failed payment of {amount_str}. "
        f"Root cause: {cause_str}. Event ID: {event.event_id}. "
        f"Goal: Inform politely, explain why it happened, and offer to send an instant payment link over WhatsApp or record a Promise to Pay date."
    )
    return system, user


def _fallback_hinglish_script(event: Event) -> dict[str, Any]:
    """Deterministic, high-quality Hinglish call script template for offline use."""
    amount_str = f"₹{event.amount:,.2f}"
    root_cause = event.root_cause or RootCause.UNKNOWN
    customer = event.customer_id

    if root_cause == RootCause.INSUFFICIENT_FUNDS:
        turns = [
            {
                "speaker": "Agent",
                "text": f"Namaste {customer} ji! Main Razorpay Support se bol raha hoon. Aapka {amount_str} ka subscription auto-debit process nahi ho paya tha.",
                "emotion": "polite",
            },
            {
                "speaker": "Customer",
                "text": "Haan ji, month end ki wajah se balance low tha. Kab tak retry hoga?",
                "emotion": "inquisitive",
            },
            {
                "speaker": "Agent",
                "text": f"Koi baat nahi ji! Humne aapka next retry salary date yaani 1st ko schedule kar diya hai. Kya aap chahenge ki hum abhi WhatsApp par direct payment link bhej dein?",
                "emotion": "helpful",
            },
            {
                "speaker": "Customer",
                "text": "Theek hai, mujhe WhatsApp par link bhej dijiye, main kal tak clear kar dunga.",
                "emotion": "reassured",
            },
            {
                "speaker": "Agent",
                "text": "Shukriya {customer} ji! Link bhej diya gaya hai. Have a wonderful day!",
                "emotion": "pleasant",
            },
        ]
        wa = f"Namaste {customer} ji! Aapka {amount_str} ka pending payment complete karne ke liye direct link: https://rzp.io/i/{event.event_id}. Instant & safe via UPI."

    elif root_cause == RootCause.EXPIRED_INSTRUMENT:
        turns = [
            {
                "speaker": "Agent",
                "text": f"Namaste {customer} ji, Razorpay team se. Aapka card ya mandate expiry date par pahunch gaya hai, isliye {amount_str} ka renewal ruk gaya tha.",
                "emotion": "professional",
            },
            {
                "speaker": "Customer",
                "text": "Oh, mera naya card aaya hai. Ise update kaise karun?",
                "emotion": "interested",
            },
            {
                "speaker": "Agent",
                "text": "Aap bas 1 minute mein link open karke naya card ya UPI AutoPay setup kar sakte hain. Main turant SMS aur WhatsApp par re-authorization link send kar raha hoon.",
                "emotion": "supportive",
            },
            {
                "speaker": "Customer",
                "text": "Great, send kar dijiye please.",
                "emotion": "satisfied",
            },
        ]
        wa = f"Namaste {customer} ji, apna mandate update karke service seamless rakhne ke liye yahan click karein: https://rzp.io/m/{event.event_id}. Thank you!"

    else:
        turns = [
            {
                "speaker": "Agent",
                "text": f"Namaste {customer} ji! Razorpay se quick update. Aapka {amount_str} ka transaction technical issue ki wajah se complete nahi hua tha.",
                "emotion": "attentive",
            },
            {
                "speaker": "Customer",
                "text": "Mera amount deduct toh nahi hua na?",
                "emotion": "concerned",
            },
            {
                "speaker": "Agent",
                "text": "Ji bilkul safe hai, koi deduction nahi hua. Bank server ab theek hai, aap alternate UPI ya NetBanking se retry kar sakte hain. Kya main payment link share karun?",
                "emotion": "reassuring",
            },
            {
                "speaker": "Customer",
                "text": "Haan ji, WhatsApp par bhej dijiye.",
                "emotion": "relieved",
            },
        ]
        wa = f"Namaste {customer} ji! {amount_str} ka transaction retry karne ke liye safe Razorpay link: https://rzp.io/pay/{event.event_id}. 100% secure payment."

    return {
        "script_summary": f"Empathetic Hinglish recovery call for {amount_str} ({root_cause})",
        "dialogue_turns": turns,
        "estimated_duration_sec": 45,
        "whatsapp_followup_hinglish": wa,
    }


def generate_hinglish_voice_script(
    event: Event,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Generate a natural Hinglish call dialogue and follow-up message.
    
    Uses LLM if configured; falls back safely to deterministic templates.
    """
    s = settings or get_settings()
    if not llm.available(s):
        return _fallback_hinglish_script(event)

    sys_prompt, user_prompt = _build_hinglish_prompt(event)
    try:
        reply = llm.chat(sys_prompt, user_prompt, settings=s)
        # Parse JSON if returned cleanly
        import json
        text = reply.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict) and "dialogue_turns" in parsed:
            return parsed
    except Exception:
        pass

    return _fallback_hinglish_script(event)
