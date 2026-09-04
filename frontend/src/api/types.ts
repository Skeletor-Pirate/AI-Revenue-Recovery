// Typed shapes for the frozen API contract (AGENTS_CONTRACT.md §8, documentation.md §5).
// All money fields are decimal strings; rates are floats 0-1.

export type EventStatus =
  | 'detected'
  | 'diagnosed'
  | 'action_taken'
  | 'recovered'
  | 'exception'
  | 'flagged'

export type RootCause =
  | 'insufficient_funds'
  | 'expired_instrument'
  | 'bank_downtime'
  | 'auth_failure'
  | 'card_declined'
  | 'checkout_abandoned'
  | 'invoice_forgotten'
  | 'suspected_fraud'
  | 'unknown'

export type AgentName = 'detection' | 'diagnosis' | 'recovery' | 'triage' | 'audit'

export interface EventRead {
  event_id: string
  event_type: string
  customer_id: string
  amount: string
  currency: string
  raw_failure_reason: string | null
  attempts_so_far: number
  days_overdue: number
  created_at: string
  updated_at: string
  status: EventStatus
  root_cause: RootCause | null
  diagnosis_confidence: number | null
  recovered_amount: string
}

export interface AuditRead {
  id: number
  event_id: string
  agent: AgentName
  action: string
  reasoning: string
  payload: unknown
  timestamp: string
}

export interface EventsResponse {
  count: number
  events: EventRead[]
}

export interface EventAuditResponse {
  event: EventRead
  trail: AuditRead[]
}

export interface ByRootCause {
  root_cause: RootCause
  at_risk: string
  recovered: string
  count: number
  recovered_count: number
  recovery_rate: number
}

export interface ByIntervention {
  intervention: string
  count: number
  recovered_count: number
  recovery_rate: number
  at_risk: string
  recovered: string
}

export interface ExceptionRow {
  event_id: string
  event_type: string
  amount: string
  root_cause: RootCause | null
  reason: string
}

export interface FraudCluster {
  flagged_event_ids: string[]
  reason: string
}

export interface MetricsBlock {
  total_at_risk: string
  total_recovered: string
  overall_recovery_rate: number
  event_count: number
  by_root_cause: ByRootCause[]
  by_intervention: ByIntervention[]
  avg_hours_to_recovery: number
  status_breakdown: Record<string, number>
  exceptions: ExceptionRow[]
  fraud_cluster: FraudCluster
}

export interface PipelineRunResponse {
  ran_at: string
  metrics: MetricsBlock
}
