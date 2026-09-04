// Plain-business-English labels — mirror Razorpay's Agent Studio tone (plan.md §11).
// No ML jargon, no raw enum strings shown to a user.

import type { AgentName, EventStatus, RootCause } from './types'

export const AGENT_LABEL: Record<AgentName, string> = {
  detection: 'Detection',
  diagnosis: 'Diagnosis',
  recovery: 'Recovery',
  triage: 'Fraud triage',
  audit: 'Reporting',
}

export const STATUS_LABEL: Record<EventStatus, string> = {
  detected: 'Detected',
  diagnosed: 'Diagnosed',
  action_taken: 'Action taken',
  recovered: 'Recovered',
  exception: "Couldn't recover",
  flagged: 'Halted — do not retry',
}

export const ROOT_CAUSE_LABEL: Record<RootCause, string> = {
  insufficient_funds: 'Insufficient funds',
  expired_instrument: 'Expired card or mandate',
  bank_downtime: 'Bank downtime',
  auth_failure: 'Authentication failed',
  card_declined: 'Card declined',
  checkout_abandoned: 'Checkout abandoned',
  invoice_forgotten: 'Invoice forgotten',
  suspected_fraud: 'Suspected fraud',
  unknown: 'Unclassified',
}

export const INTERVENTION_LABEL: Record<string, string> = {
  scheduled_retry: 'Scheduled retry',
  sent_reauth_link: 'Re-authorization link',
  suggested_alternate_method: 'Suggested another method',
  prompted_guided_retry: 'Guided retry prompt',
  sent_nudge: 'Personalized nudge',
  escalation_stage_advanced: 'Invoice escalation ladder',
}

export const ACTION_LABEL: Record<string, string> = {
  flagged_at_risk: 'Confirmed at risk',
  routed_to_exception: 'Routed to exceptions',
  classified_root_cause: 'Identified the root cause',
  llm_classified_root_cause: 'Identified the root cause (assisted)',
  halted_fraud_cluster: 'Halted — matches a fraud cluster',
  intervention_selected: 'Chose a recovery approach',
  scheduled_retry: 'Scheduled a retry',
  sent_reauth_link: 'Sent a re-authorization link',
  suggested_alternate_method: 'Suggested another payment method',
  prompted_guided_retry: 'Prompted a guided retry',
  sent_nudge: 'Sent a nudge',
  escalation_stage_advanced: 'Advanced the escalation ladder',
  awaiting_human_approval: 'Waiting on human approval',
  halted_stopping_rule: 'Stopped — a safety rule was reached',
  marked_recovered: 'Marked recovered',
  batch_metrics: 'Computed batch metrics',
}

export const labelAction = (action: string): string =>
  ACTION_LABEL[action] ?? action.replace(/_/g, ' ')

export const labelRootCause = (rc: RootCause | null): string =>
  rc ? ROOT_CAUSE_LABEL[rc] ?? rc : 'Not yet diagnosed'

export const labelIntervention = (i: string): string =>
  INTERVENTION_LABEL[i] ?? i.replace(/_/g, ' ')

export const EVENT_TYPE_LABEL: Record<string, string> = {
  failed_payment: 'Failed payment',
  overdue_invoice: 'Overdue invoice',
  abandoned_checkout: 'Abandoned checkout',
  expired_mandate: 'Expired mandate',
}

export const labelEventType = (t: string): string =>
  EVENT_TYPE_LABEL[t] ?? t.replace(/_/g, ' ')
