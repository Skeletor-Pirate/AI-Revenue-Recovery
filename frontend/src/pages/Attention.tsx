import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { Card } from '../components/Card'
import { DataTable, type Column } from '../components/DataTable'
import { TicketDrawer } from '../components/TicketDrawer'
import { PriorityPill, TicketStatusPill } from '../components/TicketPills'
import { ReviewerSignIn } from '../components/ReviewerSignIn'
import { Skeleton, ErrorState, EmptyState } from '../components/Feedback'
import { toCsv, downloadCsv } from '../lib/csv'
import {
  TICKET_STATUS_LABEL,
  labelTicketReason,
  priorityBand,
} from '../api/actionLabels'
import { PRIORITY_COLOR } from '../charts/series'
import { formatDateTime, formatINR, toNum } from '../lib/format'
import {
  clearEmployeeEmail,
  getEmployeeEmail,
} from '../lib/session'
import type { EventRead, TicketRead, TicketStatus } from '../api/types'

const STATUSES: TicketStatus[] = ['open', 'under_review', 'resolved', 'unresolved']

/** Hours since a timestamp, rendered the way a reviewer thinks about age. */
function age(iso: string): string {
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5
  if (hours < 1) return 'just now'
  if (hours < 24) return `${Math.floor(hours)}h`
  return `${Math.floor(hours / 24)}d`
}

function buildColumns(
  eventById: Map<string, EventRead>,
): Column<TicketRead>[] {
  return [
    {
      key: 'priority',
      header: 'Priority',
      render: (t) => <PriorityPill priority={t.priority} />,
      sortValue: (t) => t.priority,
      csv: (t) => `${priorityBand(t.priority)} (${t.priority})`,
    },
    {
      key: 'reason',
      header: 'Why a human',
      render: (t) => labelTicketReason(t.reason),
      sortValue: (t) => t.reason,
      csv: (t) => t.reason,
    },
    {
      key: 'ticket_id',
      header: 'Ticket',
      render: (t) => <span className="font-mono text-xs">{t.ticket_id}</span>,
      sortValue: (t) => t.ticket_id,
      csv: (t) => t.ticket_id,
    },
    {
      key: 'event_id',
      header: 'Case',
      render: (t) => <span className="font-mono text-xs">{t.event_id}</span>,
      sortValue: (t) => t.event_id,
      csv: (t) => t.event_id,
    },
    {
      key: 'amount',
      header: '₹ at risk',
      numeric: true,
      render: (t) => {
        const e = eventById.get(t.event_id)
        return e ? formatINR(e.amount) : '—'
      },
      sortValue: (t) => toNum(eventById.get(t.event_id)?.amount ?? 0),
      csv: (t) => eventById.get(t.event_id)?.amount ?? '',
    },
    {
      key: 'status',
      header: 'Ticket status',
      render: (t) => <TicketStatusPill status={t.status} />,
      sortValue: (t) => t.status,
      csv: (t) => t.status,
    },
    {
      key: 'assignee',
      header: 'Reviewer',
      render: (t) =>
        t.assigned_employee_email ? (
          <span className="font-mono text-xs">{t.assigned_employee_email}</span>
        ) : (
          <span className="text-ink-muted">unassigned</span>
        ),
      sortValue: (t) => t.assigned_employee_email ?? '',
      csv: (t) => t.assigned_employee_email ?? '',
    },
    {
      key: 'created_at',
      header: 'Waiting',
      render: (t) => (
        <span className="tabnum" title={formatDateTime(t.created_at)}>
          {age(t.created_at)}
        </span>
      ),
      sortValue: (t) => t.created_at,
      csv: (t) => t.created_at,
    },
  ]
}

export function Attention() {
  const tickets = useAsync(() => dataSource.getTickets())
  const events = useAsync(() => dataSource.getEvents())
  const [params, setParams] = useSearchParams()
  const [email, setEmail] = useState<string | null>(() => getEmployeeEmail())

  const statusFilter = params.getAll('status')
  const reasonFilter = params.get('reason') ?? ''
  const ticketId = params.get('ticket')

  const rows = useMemo(() => tickets.data?.tickets ?? [], [tickets.data])
  const eventById = useMemo(
    () => new Map((events.data?.events ?? []).map((e) => [e.event_id, e])),
    [events.data],
  )
  const columns = useMemo(() => buildColumns(eventById), [eventById])

  const reasons = useMemo(
    () => [...new Set(rows.map((t) => t.reason))],
    [rows],
  )

  const filtered = useMemo(
    () =>
      rows.filter((t) => {
        if (statusFilter.length && !statusFilter.includes(t.status)) return false
        if (reasonFilter && t.reason !== reasonFilter) return false
        return true
      }),
    [rows, statusFilter, reasonFilter],
  )

  const patch = (mut: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params)
    mut(next)
    setParams(next, { replace: false })
  }

  const toggleStatus = (s: string) =>
    patch((p) => {
      const cur = p.getAll('status')
      p.delete('status')
      const nextSet = cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]
      nextSet.forEach((x) => p.append('status', x))
    })

  if (tickets.loading) return <Skeleton rows={12} />
  if (tickets.error) return <ErrorState message={tickets.error} />

  const waiting = rows.filter(
    (t) => t.status === 'open' || t.status === 'under_review',
  ).length
  const topBand = filtered.length ? priorityBand(filtered[0].priority) : 'low'

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-base font-semibold text-ink">
            Urgent human attention
          </h1>
          <p className="mt-0.5 max-w-2xl text-xs text-ink-muted">
            Cases the automation could not carry further, most urgent first. A
            suspected-fraud halt or a waiting customer always outranks a retry
            that simply ran out of attempts.
          </p>
        </div>
        {email ? (
          <p className="text-xs text-ink-muted">
            Reviewing as <span className="font-mono text-ink-soft">{email}</span>{' '}
            <button
              type="button"
              onClick={() => {
                clearEmployeeEmail()
                setEmail(null)
              }}
              className="ml-1 underline hover:text-ink"
            >
              sign out
            </button>
          </p>
        ) : null}
      </div>

      {!email && <ReviewerSignIn onSignIn={setEmail} />}

      <Card
        title={
          <span className="flex items-center gap-2">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: PRIORITY_COLOR[topBand] }}
              aria-hidden
            />
            {waiting} awaiting attention
            <span className="font-normal text-ink-muted">
              of {rows.length} ticket{rows.length === 1 ? '' : 's'}
            </span>
          </span>
        }
        action={
          <button
            type="button"
            className="rounded-lg px-2.5 py-1 text-xs text-ink-soft ring-1 ring-[var(--color-ring)]"
            onClick={() =>
              downloadCsv('human-review-queue.csv', toCsv(columns, filtered))
            }
          >
            Export CSV
          </button>
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <div className="flex flex-wrap gap-1">
            {STATUSES.map((s) => {
              const on = statusFilter.includes(s)
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggleStatus(s)}
                  aria-pressed={on}
                  className={`rounded-full px-2.5 py-1 ring-1 ring-[var(--color-ring)] ${
                    on ? 'bg-surface-2 font-medium text-ink' : 'text-ink-soft'
                  }`}
                >
                  {TICKET_STATUS_LABEL[s]}
                </button>
              )
            })}
          </div>
          <select
            value={reasonFilter}
            onChange={(e) =>
              patch((p) =>
                e.target.value ? p.set('reason', e.target.value) : p.delete('reason'),
              )
            }
            className="rounded-lg bg-surface-2 px-2 py-1 text-ink-soft ring-1 ring-[var(--color-ring)]"
          >
            <option value="">All reasons</option>
            {reasons.map((r) => (
              <option key={r} value={r}>
                {labelTicketReason(r)}
              </option>
            ))}
          </select>
          {(statusFilter.length > 0 || reasonFilter) && (
            <button
              type="button"
              onClick={() =>
                patch((p) => {
                  p.delete('status')
                  p.delete('reason')
                })
              }
              className="text-ink-muted underline hover:text-ink"
            >
              Clear filters
            </button>
          )}
        </div>

        {rows.length === 0 ? (
          <EmptyState
            title="Nothing needs a human right now"
            hint="Every case in this batch was either recovered or closed by the automation within its stopping rules."
          />
        ) : (
          <DataTable
            columns={columns}
            rows={filtered}
            rowKey={(t) => t.ticket_id}
            initialSort={{ key: 'priority', dir: 'desc' }}
            onRowClick={(t) => patch((p) => p.set('ticket', t.ticket_id))}
            rowAccent={(t) =>
              t.status === 'open' || t.status === 'under_review'
                ? PRIORITY_COLOR[priorityBand(t.priority)]
                : undefined
            }
            emptyLabel="No tickets match these filters"
          />
        )}
      </Card>

      <TicketDrawer
        ticketId={ticketId}
        employeeEmail={email}
        onClose={() => patch((p) => p.delete('ticket'))}
        onChanged={() => tickets.reload()}
      />
    </div>
  )
}
