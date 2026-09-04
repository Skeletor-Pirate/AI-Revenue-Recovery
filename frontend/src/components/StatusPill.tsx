import type { EventStatus } from '../api/types'
import { STATUS_LABEL } from '../api/actionLabels'
import { STATUS_COLOR } from '../charts/series'

export function StatusPill({ status }: { status: EventStatus }) {
  const color = STATUS_COLOR[status]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs ring-1 ring-[var(--color-ring)] whitespace-nowrap">
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{ background: color }}
        aria-hidden
      />
      {status === 'flagged' && <span aria-hidden>&#128737;</span>}
      <span className="text-ink-soft">{STATUS_LABEL[status]}</span>
    </span>
  )
}
