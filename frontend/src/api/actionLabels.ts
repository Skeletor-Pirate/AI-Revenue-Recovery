// Plain-business-English labels — mirror Razorpay's Agent Studio tone (plan.md §11).
// No ML jargon, no raw enum strings shown to a user.

import type {
  AgentName,
  EventStatus,
  RootCause,
  TicketReason,
  TicketStatus,
} from './types'

export const AGENT_LABEL: Record<AgentName, string> = {
  detection: 'Detection',
  diagnosis: 'Diagnosis',
  recovery: 'Recovery',
  triage: 'Triage',
  audit: 'Reporting',
  human: 'Human reviewer',
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
  opened_review_ticket: 'Opened a review ticket',
  assigned_review_ticket: 'Taken for human review',
  resolved_review_ticket: 'Closed by a human reviewer',
  human_recovered: 'Recovered by a human',
  raised_customer_question: 'Customer asked something we cannot answer',
  ptp_recorded: 'Recorded a promise to pay',
  ptp_honored: 'Promise to pay honoured',
  ptp_broken: 'Promise to pay broken',
  ingested_webhook_event: 'Ingested a Razorpay webhook',
}

// --- human review queue ---

export const TICKET_STATUS_LABEL: Record<TicketStatus, string> = {
  open: 'Open',
  under_review: 'Under review',
  resolved: 'Resolved',
  unresolved: "Couldn't resolve",
}

export const TICKET_REASON_LABEL: Record<TicketReason, string> = {
  suspected_fraud: 'Suspected fraud',
  customer_question: 'Customer question',
  awaiting_approval: 'Needs approval',
  exception_no_error: 'No error on file',
  invoice_handoff: 'Invoice handoff',
  stalled_no_response: 'Stalled, no response',
  other: 'Needs a decision',
}

/** Priority bands mirror app/agents/triage.PRIORITY_BANDS. */
export type PriorityBand = 'critical' | 'high' | 'medium' | 'low'

export const priorityBand = (priority: number): PriorityBand =>
  priority >= 85 ? 'critical' : priority >= 60 ? 'high' : priority >= 40 ? 'medium' : 'low'

export const PRIORITY_BAND_LABEL: Record<PriorityBand, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export const labelTicketReason = (r: TicketReason): string =>
  TICKET_REASON_LABEL[r] ?? r.replace(/_/g, ' ')

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
