import { useEffect, useRef, useState, type ReactNode } from 'react'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { useLiquidGlass } from '../lib/liquidGlass'
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
    <div className="flex justify-between gap-4 py-1.5 text-xs border-b border-white/[0.04] last:border-0">
      <span className="text-slate-400 font-medium">{label}</span>
      <span className="text-right text-slate-200">{value}</span>
    </div>
  )
}

function CaseSummary({ event }: { event: EventRead }) {
  const human = Number(event.human_recovered_amount ?? 0)
  return (
    <div className="rounded-2xl liquid-glass-card p-4 my-3 text-slate-200">
      <div className="text-[10px] uppercase font-bold tracking-wider text-slate-400 mb-2 flex items-center justify-between pb-1 border-b border-white/[0.06]">
        <span>Associated Event Details</span>
        <span className="font-mono text-indigo-400">{event.event_id}</span>
      </div>
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
  const isResolved = ticket.resolution_outcome === 'resolved'
  return (
    <div className={`rounded-2xl liquid-glass-card p-4 my-3 border ${
      isResolved ? 'border-emerald-500/30 bg-emerald-950/20' : 'border-slate-700/50 bg-slate-900/30'
    }`}>
      <div className="flex items-center justify-between pb-1.5 border-b border-white/[0.06] mb-2">
        <p className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
          <span className={isResolved ? 'text-emerald-400' : 'text-slate-400'}>
            {isResolved ? '✓' : '●'}
          </span>
          {isResolved ? 'Resolved by Reviewer' : 'Closed Unresolved'}
        </p>
        <span className="font-mono text-[11px] text-slate-400">
          {ticket.assigned_employee_email ?? 'unknown'}
        </span>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-300">
        {ticket.resolution_note}
      </p>
      {Number(ticket.recovered_amount) > 0 && (
        <p className="mt-2 text-xs font-semibold text-emerald-400">
          Recovered {formatINRPrecise(ticket.recovered_amount)} on this ticket.
        </p>
      )}
      <p className="mt-2 text-[10px] text-slate-500">
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
  const drawerRef = useRef<HTMLDivElement>(null)

  useLiquidGlass(drawerRef, { scale: -112, chroma: 6, border: 0.05, blur: 4 })

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
          className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity duration-300 cursor-pointer"
          onClick={onClose}
        />
        <div
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label={`Review ticket ${ticketId}`}
          className="relative z-10 flex h-full w-full max-w-lg flex-col overflow-y-auto liquid-glass-drawer p-6 text-slate-100 shadow-2xl animate-in slide-in-from-right duration-300"
        >
          <div className="mb-4 flex items-center justify-between pb-3 border-b border-white/[0.08]">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-sm shadow-inner">
                🎫
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-white">Review Ticket</h2>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-300 border border-blue-500/20">
                    LIQUID GLASS
                  </span>
                </div>
                <p className="text-xs font-mono text-slate-400 mt-0.5">{ticketId}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors cursor-pointer text-sm"
              title="Close drawer"
            >
              ✕
            </button>
          </div>

          {state.loading && <Skeleton rows={8} />}
          {state.error && <ErrorState message={state.error} />}

          {ticket && (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <PriorityPill priority={ticket.priority} />
                <TicketStatusPill status={ticket.status} />
                <span className="text-xs text-slate-400">
                  {labelTicketReason(ticket.reason)}
                </span>
              </div>

              <p className="mb-4 text-xs leading-relaxed text-slate-200">
                {ticket.summary}
              </p>

              {ticket.detail && (
                <blockquote className="mb-4 rounded-2xl liquid-glass-card p-4 border-amber-500/30 bg-amber-950/20">
                  <p className="text-xs font-semibold text-amber-300 flex items-center gap-1.5">
                    <span>💬</span>
                    <span>In the customer&apos;s words</span>
                  </p>
                  <p className="mt-1.5 text-xs italic leading-relaxed text-slate-200">
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
                    className="w-full rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 px-4 py-2.5 text-xs font-bold text-white shadow-lg shadow-blue-600/30 hover:scale-[1.01] active:scale-98 transition-all disabled:opacity-50 cursor-pointer"
                  >
                    Take this ticket
                  </button>
                  {!employeeEmail && (
                    <p className="mt-1.5 text-[11px] text-amber-400/90">
                      Sign in with your work email to take tickets.
                    </p>
                  )}
                </div>
              )}

              {ticket.status === 'under_review' && (
                <div className="mb-4">
                  <p className="mb-2 text-xs text-slate-400">
                    Taken by{' '}
                    <span className="font-mono text-slate-200 font-medium">
                      {ticket.assigned_employee_email}
                    </span>
                    {ticket.assigned_at &&
                      ` · ${formatDateTime(ticket.assigned_at)}`}
                  </p>
                  <button
                    type="button"
                    disabled={!employeeEmail}
                    onClick={() => setResolveOpen(true)}
                    className="w-full rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 px-4 py-2.5 text-xs font-bold text-white shadow-lg shadow-emerald-600/30 hover:scale-[1.01] active:scale-98 transition-all disabled:opacity-50 cursor-pointer"
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

              <h3 className="mt-5 mb-2 text-sm font-semibold text-slate-200">
                Every decision on this case
              </h3>
              <div className="rounded-2xl liquid-glass-card p-4">
                <AuditTimeline trail={state.data?.trail ?? []} />
              </div>
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
