import type { TicketStatus } from '../api/types'
import {
  PRIORITY_BAND_LABEL,
  TICKET_STATUS_LABEL,
  priorityBand,
} from '../api/actionLabels'
import { PRIORITY_COLOR, TICKET_STATUS_COLOR } from '../charts/series'

// Colour is never the only signal (revrec-dashboard GUIDE): every pill carries
// its own words, and the dot is a reinforcement.

export function PriorityPill({ priority }: { priority: number }) {
  const band = priorityBand(priority)
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs ring-1 ring-[var(--color-ring)]">
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: PRIORITY_COLOR[band] }}
        aria-hidden
      />
      <span className="text-ink-soft">{PRIORITY_BAND_LABEL[band]}</span>
      <span className="tabnum text-ink-muted">{priority}</span>
    </span>
  )
}

export function TicketStatusPill({ status }: { status: TicketStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs ring-1 ring-[var(--color-ring)]">
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: TICKET_STATUS_COLOR[status] }}
        aria-hidden
      />
      <span className="text-ink-soft">{TICKET_STATUS_LABEL[status]}</span>
    </span>
  )
}
