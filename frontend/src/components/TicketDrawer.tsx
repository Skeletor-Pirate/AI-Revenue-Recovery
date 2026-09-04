import { useEffect, useState, type ReactNode } from 'react'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import {
  labelEventType,
  labelRootCause,
  labelTicketReason,
} from '../api/actionLabels'
import { formatDateTime, formatINRPrecise } from '../lib/format'
import { AuditTimeline } from './AuditTimeline'
import { StatusPill } from './StatusPill'
import { PriorityPill, TicketStatusPill } from './TicketPills'
import { Skeleton, ErrorState } from './Feedback'
import { AssignTicketModal, ResolveTicketModal } from './TicketActionModals'
import type { EventRead, TicketRead } from '../api/types'

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1 text-sm">
      <span className="text-ink-muted">{label}</span>
      <span className="text-right text-ink-soft">{value}</span>
    </div>
  )
}

function CaseSummary({ event }: { event: EventRead }) {
  const human = Number(event.human_recovered_amount ?? 0)
  return (
    <div className="rounded-xl bg-surface-2 p-4">
      <Row label="Case" value={<span className="font-mono">{event.event_id}</span>} />
      <Row
        label="Customer"
        value={<span className="font-mono">{event.customer_id}</span>}
      />
      <Row label="Type" value={labelEventType(event.event_type)} />
      <Row label="Amount at risk" value={formatINRPrecise(event.amount)} />
      <Row label="Recovered so far" value={formatINRPrecise(event.recovered_amount)} />
      {human > 0 && (
        <Row label="…of which by a human" value={formatINRPrecise(human)} />
      )}
      <Row label="Root cause" value={labelRootCause(event.root_cause)} />
      <Row label="Attempts made" value={event.attempts_so_far} />
      <Row
        label="Failure reason (raw)"
        value={
          <span className="font-mono">
            {event.raw_failure_reason ?? 'none recorded'}
          </span>
        }
      />
      <Row label="Case status" value={<StatusPill status={event.status} />} />
    </div>
  )
}

function Resolution({ ticket }: { ticket: TicketRead }) {
  return (
    <div className="rounded-xl bg-surface-2 p-4">
      <p className="text-xs font-medium text-ink-soft">
        {ticket.resolution_outcome === 'resolved'
          ? 'Resolved by'
          : 'Closed unresolved by'}{' '}
        <span className="font-mono text-ink">
          {ticket.assigned_employee_email ?? 'unknown'}
        </span>
      </p>
      <p className="mt-2 text-sm leading-relaxed text-ink">
        {ticket.resolution_note}
      </p>
      {Number(ticket.recovered_amount) > 0 && (
        <p className="mt-2 text-xs text-ink-muted">
          Recovered {formatINRPrecise(ticket.recovered_amount)} on this ticket.
        </p>
      )}
      <p className="mt-2 text-[11px] text-ink-muted">
        Closed {formatDateTime(ticket.updated_at)}
      </p>
    </div>
  )
}

export function TicketDrawer({
  ticketId,
  employeeEmail,
  onClose,
  onChanged,
}: {
  ticketId: string | null
  employeeEmail: string | null
  onClose: () => void
  /** Refresh the queue behind the drawer after a mutation. */
  onChanged: () => void
}) {
  const [assignOpen, setAssignOpen] = useState(false)
  const [resolveOpen, setResolveOpen] = useState(false)

  useEffect(() => {
    if (!ticketId) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ticketId, onClose])

  const state = useAsync(
    () => dataSource.getTicket(ticketId as string),
    [ticketId],
  )

  if (!ticketId) return null

  const ticket = state.data?.ticket
  const event = state.data?.event ?? null
  const closed =
    ticket?.status === 'resolved' || ticket?.status === 'unresolved'

  // Both mutations refresh the drawer AND the queue behind it.
  const afterChange = () => {
    state.reload()
    onChanged()
  }

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
          aria-label={`Review ticket ${ticketId}`}
          className="relative z-10 flex h-full w-full max-w-md flex-col overflow-y-auto bg-surface-1 p-5 ring-1 ring-[var(--color-ring)]"
        >
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">
              Review ticket{' '}
              <span className="font-mono text-ink-soft">{ticketId}</span>
            </h2>
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

          {ticket && (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <PriorityPill priority={ticket.priority} />
                <TicketStatusPill status={ticket.status} />
                <span className="text-xs text-ink-muted">
                  {labelTicketReason(ticket.reason)}
                </span>
              </div>

              <p className="mb-4 text-sm leading-relaxed text-ink">
                {ticket.summary}
              </p>

              {ticket.detail && (
                <blockquote className="mb-4 rounded-xl bg-surface-2 p-4">
                  <p className="text-xs font-medium text-ink-soft">
                    In the customer&apos;s words
                  </p>
                  <p className="mt-1.5 text-sm italic leading-relaxed text-ink">
                    “{ticket.detail}”
                  </p>
                </blockquote>
              )}

              {/* --- what a reviewer can do right now --- */}
              {ticket.status === 'open' && (
                <div className="mb-4">
                  <button
                    type="button"
                    disabled={!employeeEmail}
                    onClick={() => setAssignOpen(true)}
                    className="w-full rounded-lg bg-surface-2 px-3 py-2 text-xs font-medium text-ink ring-1 ring-[var(--color-ring)] hover:bg-surface-1 disabled:opacity-50"
                  >
                    Take this ticket
                  </button>
                  {!employeeEmail && (
                    <p className="mt-1.5 text-[11px] text-ink-muted">
                      Sign in with your work email to take tickets.
                    </p>
                  )}
                </div>
              )}

              {ticket.status === 'under_review' && (
                <div className="mb-4">
                  <p className="mb-2 text-xs text-ink-muted">
                    Taken by{' '}
                    <span className="font-mono text-ink-soft">
                      {ticket.assigned_employee_email}
                    </span>
                    {ticket.assigned_at &&
                      ` · ${formatDateTime(ticket.assigned_at)}`}
                  </p>
                  <button
                    type="button"
                    disabled={!employeeEmail}
                    onClick={() => setResolveOpen(true)}
                    className="w-full rounded-lg bg-surface-2 px-3 py-2 text-xs font-medium text-ink ring-1 ring-[var(--color-ring)] hover:bg-surface-1 disabled:opacity-50"
                  >
                    Record what you did
                  </button>
                </div>
              )}

              {closed && (
                <div className="mb-4">
                  <Resolution ticket={ticket} />
                </div>
              )}

              {event && <CaseSummary event={event} />}

              <h3 className="mt-5 mb-1 text-sm font-semibold text-ink">
                Every decision on this case
              </h3>
              <AuditTimeline trail={state.data?.trail ?? []} />
            </>
          )}
        </div>
      </div>

      {ticket && employeeEmail && (
        <>
          <AssignTicketModal
            ticket={ticket}
            employeeEmail={employeeEmail}
            isOpen={assignOpen}
            onClose={() => setAssignOpen(false)}
            onSuccess={afterChange}
          />
          <ResolveTicketModal
            ticket={ticket}
            event={event}
            employeeEmail={employeeEmail}
            isOpen={resolveOpen}
            onClose={() => setResolveOpen(false)}
            onSuccess={afterChange}
          />
        </>
      )}
    </>
  )
}
