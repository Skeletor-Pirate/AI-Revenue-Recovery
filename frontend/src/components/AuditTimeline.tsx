import { useState } from 'react'
import type { AuditRead, AgentName } from '../api/types'
import { AGENT_LABEL, labelAction } from '../api/actionLabels'
import { formatDateTime } from '../lib/format'
import { PayloadViewer } from './PayloadViewer'
import { EmptyState } from './Feedback'

const AGENT_CHIP: Record<AgentName, string> = {
  detection: 'var(--color-series-1)',
  diagnosis: 'var(--color-series-2)',
  recovery: 'var(--color-series-3)',
  audit: 'var(--color-series-4)',
  triage: 'var(--color-status-critical)',
  human: 'var(--color-series-6)',
}

const CRITICAL_ACTIONS = new Set([
  'halted_stopping_rule',
  'halted_fraud_cluster',
  'routed_to_exception',
  'awaiting_human_approval',
  'raised_customer_question',
])

function Node({ node, last }: { node: AuditRead; last: boolean }) {
  const [open, setOpen] = useState(false)
  // A human's own actions are never "critical" red -- they are the resolution,
  // not the alarm.
  const critical =
    node.agent !== 'human' &&
    (node.agent === 'triage' || CRITICAL_ACTIONS.has(node.action))
  const marker = critical ? 'var(--color-status-critical)' : AGENT_CHIP[node.agent]
  const hasPayload =
    node.payload != null &&
    typeof node.payload === 'object' &&
    Object.keys(node.payload as object).length > 0

  return (
    <li className="relative pl-6">
      {!last && (
        <span
          className="absolute left-[5px] top-4 bottom-0 w-px"
          style={{ background: 'var(--color-hairline)' }}
          aria-hidden
        />
      )}
      <span
        className="absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--color-surface-1)]"
        style={{ background: marker }}
        aria-hidden
      />
      <div className="pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="rounded px-1.5 py-0.5 text-[11px] font-medium text-white"
            style={{ background: AGENT_CHIP[node.agent] }}
          >
            {AGENT_LABEL[node.agent]}
          </span>
          <span className="text-sm font-medium text-ink">
            {labelAction(node.action)}
          </span>
          <span className="font-mono text-[11px] text-ink-muted">
            {node.action}
          </span>
          <span className="ml-auto text-xs text-ink-muted tabnum">
            {formatDateTime(node.timestamp)}
          </span>
        </div>
        <p className="mt-1 text-sm text-ink-soft">{node.reasoning}</p>
        {hasPayload && (
          <>
            <button
              type="button"
              className="mt-1 text-xs text-ink-muted hover:text-ink"
              onClick={() => setOpen((o) => !o)}
            >
              {open ? 'Hide payload' : 'Show payload'}
            </button>
            {open && <PayloadViewer payload={node.payload} />}
          </>
        )}
      </div>
    </li>
  )
}

export function AuditTimeline({ trail }: { trail: AuditRead[] }) {
  if (trail.length === 0) {
    return (
      <EmptyState
        title="No decision trail yet"
        hint="This case has not been through the pipeline, or it is still in an early stage. Every agent decision will be listed here."
      />
    )
  }
  const ordered = [...trail].sort((a, b) => a.id - b.id)
  return (
    <ol className="mt-2">
      {ordered.map((n, i) => (
        <Node key={n.id} node={n} last={i === ordered.length - 1} />
      ))}
    </ol>
  )
}
