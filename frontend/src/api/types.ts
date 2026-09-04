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

export type AgentName =
  | 'detection'
  | 'diagnosis'
  | 'recovery'
  | 'triage'
  | 'audit'
  | 'human' // a real employee acting on a review ticket

export type PTPStatus = 'none' | 'promised' | 'honored' | 'broken'

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
  /** Of `recovered_amount`, how much a human brought in working a review ticket. */
  human_recovered_amount?: string
  promised_date?: string | null
  ptp_status?: PTPStatus
  retry_schedule?: Array<Record<string, unknown>> | null
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

export interface SimilarCase {
  event_id: string
  event_type: string
  raw_failure_reason: string | null
  case_text: string
  root_cause: RootCause
  source: string // "pipeline" | "reference"
  similarity: number // cosine similarity 0..1
}

export interface EventSimilarResponse {
  event_id: string
  similar: SimilarCase[]
}

export interface DialogueTurn {
  speaker: string
  text: string
  emotion?: string
}

export interface VoiceScript {
  script_summary: string
  dialogue_turns: DialogueTurn[]
  estimated_duration_sec: number
  whatsapp_followup_hinglish: string
}

export interface EventVoiceResponse {
  event_id: string
  script: VoiceScript
}

// --- human review queue (backend: app/agents/triage.py) --------------------

export type TicketStatus = 'open' | 'under_review' | 'resolved' | 'unresolved'

export type TicketReason =
  | 'suspected_fraud'
  | 'customer_question'
  | 'awaiting_approval'
  | 'exception_no_error'
  | 'invoice_handoff'
  | 'stalled_no_response'
  | 'other'

export interface TicketRead {
  ticket_id: string
  event_id: string
  reason: TicketReason
  /** Higher = more urgent. Bands: >=85 critical, >=60 high, >=40 medium, else low. */
  priority: number
  status: TicketStatus
  summary: string
  detail: string | null
  assigned_employee_email: string | null
  assigned_at: string | null
  resolution_note: string | null
  resolution_outcome: 'resolved' | 'unresolved' | null
  recovered_amount: string
  created_at: string
  updated_at: string
}

export interface TicketsResponse {
  tickets: TicketRead[]
  count: number
  open_count: number
  under_review_count: number
}

export interface TicketDetailResponse {
  ticket: TicketRead
  event: EventRead | null
  trail: AuditRead[]
}

export interface TicketMutationResponse {
  status: string
  ticket: TicketRead
}

export interface TicketMetrics {
  total: number
  open: number
  under_review: number
  resolved: number
  unresolved: number
  needs_attention: number
  by_reason: Record<TicketReason, number>
  oldest_open_hours: number
  resolution_rate: number
  human_recovered: string
}

export interface VoiceAudioClip {
  index: number
  speaker: string
  audio_base64: string
}

export interface EventVoiceAudioResponse {
  event_id: string
  available: boolean
  provider: string
  audio_format: string
  sample_rate: number
  audio: VoiceAudioClip[]
}

export interface RetryStep {
  step_number: number
  rail: string
  scheduled_time: string
  action: string
  channel: string
  pre_debit_notification: boolean
  expected_recovery_prob: number
  rationale: string
  compliance_tag: string
}

export interface EventSequencerResponse {
  event_id: string
  rail: string
  schedule: RetryStep[]
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

export interface PTPMetrics {
  total_ptp_recorded: number
  total_honored: number
  total_broken: number
  active_promised: number
  honor_rate: number
  amount_recovered_ptp: string
}

export interface MetricsBlock {
  total_at_risk: string
  /** The honest total. `ai_recovered + human_recovered` always equals this. */
  total_recovered: string
  ai_recovered?: string
  human_recovered?: string
  overall_recovery_rate: number
  event_count: number
  by_root_cause: ByRootCause[]
  by_intervention: ByIntervention[]
  avg_hours_to_recovery: number
  status_breakdown: Record<EventStatus, number>
  exceptions: ExceptionRow[]
  fraud_cluster: FraudCluster
  ptp_metrics?: PTPMetrics
  tickets?: TicketMetrics
}

export interface PipelineRunResponse {
  ran_at: string
  metrics: MetricsBlock
}
