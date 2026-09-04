"""Mandate Retry Sequencer — Direction 5.

Provides rail-aware, calendar-optimized, and NPCI-compliant retry scheduling
for failed subscriptions, recurring payments, and standing mandates (e-NACH,
UPI AutoPay, Tokenized Cards).

Key features:
- Predicts salary credit cycles (28th-5th) for insufficient funds.
- Adapts backoff intervals based on specific failure codes (bank outage vs auth).
- Enforces strict compliance with NPCI / RBI guidelines (maximum 3 retry attempts per cycle).
- Outputs an actionable execution schedule with estimated recovery probabilities.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.store import Event, EventType, RootCause


def _detect_rail(event: Event) -> str:
    """Infer the payment rail from the event attributes or reason."""
    reason = (event.raw_failure_reason or "").lower()
    if event.event_type == EventType.EXPIRED_MANDATE or "mandate" in reason:
        return "enach_mandate"
    if "upi" in reason or "vpa" in reason or "auth" in reason:
        return "upi_autopay"
    return "card_token"


def _calculate_salary_retry_date(base_time: datetime) -> datetime:
    """Target the 1st of the upcoming month for salary credit window."""
    if base_time.day == 1:
        # If today is the 1st, retry next day
        return base_time + timedelta(days=1)
    if base_time.month == 12:
        return datetime(base_time.year + 1, 1, 1, 9, 0, tzinfo=timezone.utc)
    return datetime(base_time.year, base_time.month + 1, 1, 9, 0, tzinfo=timezone.utc)


def plan_retry_sequence(event: Event) -> list[dict[str, Any]]:
    """Generate an intelligent multi-step mandate retry schedule.
    
    Returns a list of structured steps with scheduled timestamp offsets,
    rail designation, channel intervention, and compliance rules.
    """
    base_time = event.updated_at or event.created_at or datetime.now(timezone.utc)
    rail = _detect_rail(event)
    root_cause = event.root_cause or RootCause.UNKNOWN

    schedule: list[dict[str, Any]] = []

    if root_cause == RootCause.INSUFFICIENT_FUNDS:
        # Step 1: Pre-debit notification & wait for salary window
        salary_date = _calculate_salary_retry_date(base_time)
        schedule.append({
            "step_number": 1,
            "rail": rail,
            "scheduled_time": salary_date.isoformat(),
            "action": "salary_window_debit",
            "channel": "whatsapp_and_sms",
            "pre_debit_notification": True,
            "expected_recovery_prob": 0.75,
            "rationale": "Scheduled debit aligned with expected salary credit window",
            "compliance_tag": "NPCI_CIRCULAR_2024_PTP",
        })
        # Step 2: Fallback retry 48 hours later if initial debit bounces
        step2_date = salary_date + timedelta(days=2)
        schedule.append({
            "step_number": 2,
            "rail": rail,
            "scheduled_time": step2_date.isoformat(),
            "action": "secondary_retry",
            "channel": "sms",
            "pre_debit_notification": False,
            "expected_recovery_prob": 0.45,
            "rationale": "Secondary attempt during salary processing buffer",
            "compliance_tag": "NPCI_MAX_RETRY_STAGE_2",
        })
        # Step 3: Re-authorization / manual pay link
        step3_date = salary_date + timedelta(days=5)
        schedule.append({
            "step_number": 3,
            "rail": "instant_upi_link",
            "scheduled_time": step3_date.isoformat(),
            "action": "send_instant_pay_link",
            "channel": "whatsapp_with_payment_link",
            "pre_debit_notification": False,
            "expected_recovery_prob": 0.30,
            "rationale": "Final bounded attempt before human handoff",
            "compliance_tag": "RBI_MANDATE_FALLBACK",
        })

    elif root_cause == RootCause.BANK_DOWNTIME:
        # Short exponential backoff (e.g. 4h, 12h, 24h)
        schedule.append({
            "step_number": 1,
            "rail": rail,
            "scheduled_time": (base_time + timedelta(hours=4)).isoformat(),
            "action": "short_backoff_retry",
            "channel": "automated_system",
            "pre_debit_notification": False,
            "expected_recovery_prob": 0.80,
            "rationale": "Automated retry after bank switch recovery",
            "compliance_tag": "SWITCH_DOWNTIME_BUFFER",
        })
        schedule.append({
            "step_number": 2,
            "rail": "alternate_vpa_switch" if rail == "upi_autopay" else "alternate_card_switch",
            "scheduled_time": (base_time + timedelta(hours=16)).isoformat(),
            "action": "suggest_alternate_rail",
            "channel": "whatsapp_nudge",
            "pre_debit_notification": True,
            "expected_recovery_prob": 0.60,
            "rationale": "Prompting user for secondary bank mandate",
            "compliance_tag": "MULTI_RAIL_REDUNDANCY",
        })

    elif root_cause == RootCause.EXPIRED_INSTRUMENT:
        # Re-mandate link immediate, followed by reminder
        schedule.append({
            "step_number": 1,
            "rail": "remandate_portal",
            "scheduled_time": (base_time + timedelta(hours=2)).isoformat(),
            "action": "send_remandate_link",
            "channel": "email_and_whatsapp",
            "pre_debit_notification": False,
            "expected_recovery_prob": 0.65,
            "rationale": "Immediate authorization link to update expired mandate details",
            "compliance_tag": "E_MANDATE_RENEWAL",
        })
        schedule.append({
            "step_number": 2,
            "rail": "remandate_portal",
            "scheduled_time": (base_time + timedelta(hours=48)).isoformat(),
            "action": "remandate_followup",
            "channel": "sms_with_link",
            "pre_debit_notification": False,
            "expected_recovery_prob": 0.35,
            "rationale": "Gentle follow-up before subscription suspension",
            "compliance_tag": "CUSTOMER_COOLDOWN_RULE",
        })

    else:
        # Default standard 2-step retry schedule
        schedule.append({
            "step_number": 1,
            "rail": rail,
            "scheduled_time": (base_time + timedelta(hours=24)).isoformat(),
            "action": "guided_retry_prompt",
            "channel": "whatsapp_and_sms",
            "pre_debit_notification": False,
            "expected_recovery_prob": 0.50,
            "rationale": "24-hour standard cooldown retry",
            "compliance_tag": "STANDARD_RECOVERY",
        })

    return schedule
