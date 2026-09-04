import { useState } from 'react'
import { dataSource } from '../api/dataSource'
import { labelTicketReason } from '../api/actionLabels'
import { formatINR } from '../lib/format'
import type { EventRead, TicketRead } from '../api/types'

interface Shell {
  title: string
  subtitle: string
  error: string | null
  submitting: boolean
  submitLabel: string
  pendingLabel: string
  onClose: () => void
  onSubmit: (e: React.FormEvent) => void
  children?: React.ReactNode
}

function ModalShell({
  title,
  subtitle,
  error,
  submitting,
  submitLabel,
  pendingLabel,
  onClose,
  onSubmit,
  children,
}: Shell) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
    >
      <div className="flex w-full max-w-lg flex-col gap-4 rounded-2xl bg-surface-1 p-5 ring-1 ring-[var(--color-ring)] shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-hairline)] pb-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg px-2 py-1 text-sm text-ink-soft ring-1 ring-[var(--color-ring)] hover:text-ink"
          >
            Esc
          </button>
        </header>

        {error && (
          <p
            className="rounded-lg bg-surface-2 px-3 py-2 text-xs"
            style={{ color: 'var(--color-status-critical)' }}
            role="alert"
          >
            {error}
          </p>
        )}

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          {children}
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-3 py-2 text-xs font-medium text-ink-soft ring-1 ring-[var(--color-ring)] hover:text-ink"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-surface-2 px-3 py-2 text-xs font-medium text-ink ring-1 ring-[var(--color-ring)] disabled:opacity-50"
            >
              {submitting ? pendingLabel : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

const fieldClass =
  'w-full rounded-lg bg-surface-2 px-3 py-2 text-sm text-ink ring-1 ring-[var(--color-ring)] focus:outline-none focus:ring-2'

// --- take a ticket ---------------------------------------------------------

export function AssignTicketModal({
  ticket,
  employeeEmail,
  isOpen,
  onClose,
  onSuccess,
}: {
  ticket: TicketRead
  employeeEmail: string
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
}) {
  const [email, setEmail] = useState(employeeEmail)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) return null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await dataSource.assignTicket(ticket.ticket_id, email)
      onSuccess()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not take this ticket')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell
      title="Take this ticket"
      subtitle={`${ticket.ticket_id} · ${labelTicketReason(ticket.reason)}`}
      error={error}
      submitting={submitting}
      submitLabel="Take ticket"
      pendingLabel="Taking..."
      onClose={onClose}
      onSubmit={submit}
    >
      <div>
        <label
          htmlFor="assign-email"
          className="mb-1 block text-xs font-medium text-ink-soft"
        >
          Reviewing as
        </label>
        <input
          id="assign-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={fieldClass}
          required
        />
      </div>
      <p className="rounded-lg bg-surface-2 px-3 py-2 text-[11px] text-ink-muted">
        The ticket moves to <span className="text-ink-soft">Under review</span> and
        becomes yours. One reviewer per ticket — nobody else can take it until you
        close it.
      </p>
    </ModalShell>
  )
}

// --- close a ticket --------------------------------------------------------

export function ResolveTicketModal({
  ticket,
  event,
  employeeEmail,
  isOpen,
  onClose,
  onSuccess,
}: {
  ticket: TicketRead
  event: EventRead | null
  employeeEmail: string
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
}) {
  const [outcome, setOutcome] = useState<'resolved' | 'unresolved'>('resolved')
  const [note, setNote] = useState('')
  const [recovered, setRecovered] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) return null

  const outstanding = event
    ? Number(event.amount) - Number(event.recovered_amount)
    : 0

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await dataSource.resolveTicket(ticket.ticket_id, {
        employee_email: employeeEmail,
        outcome,
        note: note.trim(),
        recovered_amount:
          outcome === 'resolved' && recovered.trim() ? recovered.trim() : null,
      })
      onSuccess()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not close this ticket')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell
      title="Record what you did"
      subtitle={`${ticket.ticket_id} · ${labelTicketReason(ticket.reason)}`}
      error={error}
      submitting={submitting}
      submitLabel="Close ticket"
      pendingLabel="Saving..."
      onClose={onClose}
      onSubmit={submit}
    >
      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-ink-soft">Outcome</legend>
        <div className="inline-flex rounded-lg ring-1 ring-[var(--color-ring)]">
          {(['resolved', 'unresolved'] as const).map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => setOutcome(o)}
              aria-pressed={outcome === o}
              className={`px-3 py-1.5 text-xs first:rounded-l-lg last:rounded-r-lg ${
                outcome === o
                  ? 'bg-surface-2 font-medium text-ink'
                  : 'text-ink-soft hover:text-ink'
              }`}
            >
              {o === 'resolved' ? 'Resolved' : "Couldn't resolve"}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-ink-muted">
          {outcome === 'resolved'
            ? 'You settled it — say how.'
            : "An honest 'couldn't fix this' is a valid close. Say why, so the next person starts ahead."}
        </p>
      </fieldset>

      <div>
        <label
          htmlFor="resolve-note"
          className="mb-1 block text-xs font-medium text-ink-soft"
        >
          What you did
        </label>
        <textarea
          id="resolve-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={4}
          required
          placeholder="e.g. Called the customer, confirmed the charge was genuine, sent a fresh UPI link; paid on the call."
          className={fieldClass}
        />
        <p className="mt-1 text-[11px] text-ink-muted">
          Written verbatim into the case audit trail.
        </p>
      </div>

      {outcome === 'resolved' && (
        <div>
          <label
            htmlFor="resolve-amount"
            className="mb-1 block text-xs font-medium text-ink-soft"
          >
            Money you recovered <span className="text-ink-muted">(optional)</span>
          </label>
          <input
            id="resolve-amount"
            type="number"
            min="0"
            step="0.01"
            max={outstanding > 0 ? outstanding : undefined}
            value={recovered}
            onChange={(e) => setRecovered(e.target.value)}
            placeholder="0.00"
            className={`${fieldClass} tabnum`}
          />
          <p className="mt-1 text-[11px] text-ink-muted">
            {outstanding > 0
              ? `${formatINR(outstanding)} still outstanding on this case. Counted as human-recovered, kept separate from what the automation collected.`
              : 'Nothing outstanding on this case.'}
          </p>
        </div>
      )}
    </ModalShell>
  )
}

// --- escalate a customer question -----------------------------------------

export function RaiseQuestionModal({
  eventId,
  employeeEmail,
  channel = 'voice_call',
  isOpen,
  onClose,
  onSuccess,
}: {
  eventId: string
  employeeEmail: string | null
  channel?: string
  isOpen: boolean
  onClose: () => void
  onSuccess: (ticketId: string) => void
}) {
  const [question, setQuestion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) return null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await dataSource.raiseQuestion(eventId, {
        question: question.trim(),
        channel,
        employee_email: employeeEmail,
      })
      onSuccess(res.ticket.ticket_id)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not raise this question')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell
      title="Hand this question to a human"
      subtitle={`${eventId} · the AI should not improvise an answer`}
      error={error}
      submitting={submitting}
      submitLabel="Raise for review"
      pendingLabel="Raising..."
      onClose={onClose}
      onSubmit={submit}
    >
      <div>
        <label
          htmlFor="raise-question"
          className="mb-1 block text-xs font-medium text-ink-soft"
        >
          What the customer asked
        </label>
        <textarea
          id="raise-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={4}
          required
          placeholder="Record it in the customer's own words."
          className={fieldClass}
        />
      </div>
      <p className="rounded-lg bg-surface-2 px-3 py-2 text-[11px] text-ink-muted">
        This opens a high-priority ticket on the case. A waiting customer outranks
        every stalled retry in the queue.
      </p>
    </ModalShell>
  )
}
