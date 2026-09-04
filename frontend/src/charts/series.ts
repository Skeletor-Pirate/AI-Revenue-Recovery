// Fixed colour roles. Assign in order, NEVER cycle, NEVER reorder by value.

import type { EventStatus, RootCause } from '../api/types'

export const SERIES = [
  'var(--color-series-1)',
  'var(--color-series-2)',
  'var(--color-series-3)',
  'var(--color-series-4)',
  'var(--color-series-5)',
  'var(--color-series-6)',
  'var(--color-series-7)',
  'var(--color-series-8)',
] as const

// One hue per root cause, so the colour follows the entity across every chart.
export const ROOT_CAUSE_COLOR: Record<RootCause, string> = {
  insufficient_funds: 'var(--color-series-1)',
  expired_instrument: 'var(--color-series-2)',
  bank_downtime: 'var(--color-series-3)',
  auth_failure: 'var(--color-series-4)',
  card_declined: 'var(--color-series-8)',
  checkout_abandoned: 'var(--color-series-5)',
  invoice_forgotten: 'var(--color-series-6)',
  suspected_fraud: 'var(--color-status-critical)',
  unknown: 'var(--color-ink-muted)',
}

export const rootCauseColor = (rc: RootCause | null | undefined): string =>
  rc ? ROOT_CAUSE_COLOR[rc] ?? 'var(--color-ink-muted)' : 'var(--color-ink-muted)'

export const STATUS_COLOR: Record<EventStatus, string> = {
  detected: 'var(--color-ink-muted)',
  diagnosed: 'var(--color-ink-muted)',
  action_taken: 'var(--color-series-1)',
  recovered: 'var(--color-status-good)',
  exception: 'var(--color-status-warning)',
  flagged: 'var(--color-status-critical)',
}

// Human-review queue priority bands. Reuses the status palette so "critical"
// means the same thing here as it does on an event.
export const PRIORITY_COLOR: Record<string, string> = {
  critical: 'var(--color-status-critical)',
  high: 'var(--color-status-serious)',
  medium: 'var(--color-status-warning)',
  low: 'var(--color-ink-muted)',
}

export const TICKET_STATUS_COLOR: Record<string, string> = {
  open: 'var(--color-status-serious)',
  under_review: 'var(--color-series-1)',
  resolved: 'var(--color-status-good)',
  unresolved: 'var(--color-status-warning)',
}

export const AXIS = {
  grid: 'var(--color-hairline)',
  baseline: 'var(--color-ink-muted)',
  tick: 'var(--color-ink-muted)',
}
