import { useEffect, useState, type ReactNode } from 'react'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import {
  labelEventType,
  labelRootCause,
} from '../api/actionLabels'
import { formatDateTime, formatINRPrecise, formatPct } from '../lib/format'
import { SimilarCases } from './SimilarCases'
import { AuditTimeline } from './AuditTimeline'
import { StatusPill } from './StatusPill'
import { Skeleton, ErrorState } from './Feedback'
import { VoiceCallDrawer } from './VoiceCallDrawer'
import { PTPModal } from './PTPModal'
import { SequencerTimeline } from './SequencerTimeline'
import type { EventRead } from '../api/types'

function SummaryRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 text-sm">
      <span className="text-ink-muted">{label}</span>
      <span className="text-right text-ink-soft">{value}</span>
    </div>
  )
}

function Summary({ event }: { event: EventRead }) {
  return (
    <div className="rounded-xl bg-surface-2 p-4">
      <SummaryRow label="Case" value={<span className="font-mono">{event.event_id}</span>} />
      <SummaryRow label="Customer" value={<span className="font-mono">{event.customer_id}</span>} />
      <SummaryRow label="Type" value={labelEventType(event.event_type)} />
      <SummaryRow label="Amount at risk" value={formatINRPrecise(event.amount)} />
      <SummaryRow label="Recovered" value={formatINRPrecise(event.recovered_amount)} />
      <SummaryRow label="Root cause" value={labelRootCause(event.root_cause)} />
      <SummaryRow
        label="Diagnosis confidence"
        value={
          event.diagnosis_confidence == null
            ? '—'
            : formatPct(event.diagnosis_confidence)
        }
      />
      {event.ptp_status && event.ptp_status !== 'none' && (
        <SummaryRow
          label="Promise-to-Pay (PTP)"
          value={
            <span className="capitalize font-medium text-indigo-400">
              {event.ptp_status} {event.promised_date ? `(${formatDateTime(event.promised_date)})` : ''}
            </span>
          }
        />
      )}
      <SummaryRow label="Attempts so far" value={event.attempts_so_far} />
      <SummaryRow label="Days overdue" value={event.days_overdue} />
      <SummaryRow
        label="Failure reason (raw)"
        value={<span className="font-mono">{event.raw_failure_reason ?? '—'}</span>}
      />
      <SummaryRow label="Last updated" value={formatDateTime(event.updated_at)} />
    </div>
  )
}

export function DetailDrawer({
  caseId,
  onClose,
}: {
  caseId: string | null
  onClose: () => void
}) {
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [ptpOpen, setPtpOpen] = useState(false)

  useEffect(() => {
    if (!caseId) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [caseId, onClose])

  const state = useAsync(
    () => dataSource.getEventAudit(caseId as string),
    [caseId],
  )

  if (!caseId) return null

  return (
    <>
      <div className="fixed inset-0 z-40 flex justify-end">
        <button
          type="button"
          aria-label="Close"
          className="absolute inset-0 bg-black/30"
          onClick={onClose}
        />
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`Decision trail for ${caseId}`}
          className="relative z-10 flex h-full w-full max-w-md flex-col overflow-y-auto bg-surface-1 p-5 ring-1 ring-[var(--color-ring)]"
        >
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Decision trail</h2>
            <button
              type="button"
              onClick={onClose}
              className="text-sm text-ink-muted hover:text-ink"
            >
              Close
            </button>
          </div>

          {state.loading && <Skeleton rows={8} />}
          {state.error && <ErrorState message={state.error} />}
          {state.data && (
            <>
              <div className="mb-3 flex items-center justify-between">
                <StatusPill status={state.data.event.status} />
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setVoiceOpen(true)}
                    className="px-2.5 py-1 text-xs font-medium rounded-lg bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 border border-blue-500/30 transition-colors"
                  >
                    🎙️ Voice Script
                  </button>
                  <button
                    type="button"
                    onClick={() => setPtpOpen(true)}
                    className="px-2.5 py-1 text-xs font-medium rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 transition-colors"
                  >
                    🤝 Record PTP
                  </button>
                </div>
              </div>

              <Summary event={state.data.event} />

              {/* Mandate Retry Sequencer Plan */}
              <div className="mt-5">
                <SequencerTimeline eventId={caseId} />
              </div>

              <h3 className="mt-5 mb-1 text-sm font-semibold text-ink">
                Every agent decision
              </h3>
              <AuditTimeline trail={state.data.trail} />
              <SimilarCases caseId={caseId} />
            </>
          )}
        </div>
      </div>

      <VoiceCallDrawer
        eventId={caseId}
        isOpen={voiceOpen}
        onClose={() => setVoiceOpen(false)}
      />

      <PTPModal
        eventId={caseId}
        isOpen={ptpOpen}
        onClose={() => setPtpOpen(false)}
        onSuccess={() => state.reload()}
      />
    </>
  )
}
