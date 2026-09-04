import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { Card } from '../components/Card'
import { DataTable, type Column } from '../components/DataTable'
import { toCsv, downloadCsv } from '../lib/csv'
import { DetailDrawer } from '../components/DetailDrawer'
import { StatusPill } from '../components/StatusPill'
import { Skeleton, ErrorState } from '../components/Feedback'
import {
  labelEventType,
  labelRootCause,
  STATUS_LABEL,
} from '../api/actionLabels'
import { formatDateTime, formatINR, toNum } from '../lib/format'
import type { EventRead, EventStatus } from '../api/types'

const STATUSES: EventStatus[] = [
  'detected',
  'diagnosed',
  'action_taken',
  'recovered',
  'exception',
  'flagged',
]

const columns: Column<EventRead>[] = [
  {
    key: 'event_id',
    header: 'Case',
    render: (e) => <span className="font-mono text-xs">{e.event_id}</span>,
    sortValue: (e) => e.event_id,
    csv: (e) => e.event_id,
  },
  {
    key: 'customer_id',
    header: 'Customer',
    render: (e) => <span className="font-mono text-xs">{e.customer_id}</span>,
    csv: (e) => e.customer_id,
  },
  {
    key: 'event_type',
    header: 'Type',
    render: (e) => labelEventType(e.event_type),
    sortValue: (e) => e.event_type,
    csv: (e) => e.event_type,
  },
  {
    key: 'amount',
    header: '₹ at risk',
    numeric: true,
    render: (e) => formatINR(e.amount),
    sortValue: (e) => toNum(e.amount),
    csv: (e) => e.amount,
  },
  {
    key: 'status',
    header: 'Status',
    render: (e) => <StatusPill status={e.status} />,
    sortValue: (e) => e.status,
    csv: (e) => STATUS_LABEL[e.status],
  },
  {
    key: 'root_cause',
    header: 'Root cause',
    render: (e) => labelRootCause(e.root_cause),
    sortValue: (e) => e.root_cause ?? 'zzz',
    csv: (e) => e.root_cause ?? '',
  },
  {
    key: 'attempts_so_far',
    header: 'Attempts',
    numeric: true,
    render: (e) => e.attempts_so_far,
    sortValue: (e) => e.attempts_so_far,
    csv: (e) => e.attempts_so_far,
  },
  {
    key: 'days_overdue',
    header: 'Days overdue',
    numeric: true,
    render: (e) => e.days_overdue,
    sortValue: (e) => e.days_overdue,
    csv: (e) => e.days_overdue,
  },
  {
    key: 'updated_at',
    header: 'Updated',
    render: (e) => formatDateTime(e.updated_at),
    sortValue: (e) => e.updated_at,
    csv: (e) => e.updated_at,
  },
]

export function Queue() {
  const { data, loading, error } = useAsync(() => dataSource.getEvents())
  const [params, setParams] = useSearchParams()

  const statusFilter = params.getAll('status')
  const typeFilter = params.get('type') ?? ''
  const rcFilter = params.get('rc') ?? ''
  const minAmt = params.get('min') ?? ''
  const caseId = params.get('case')

  const events = useMemo(() => data?.events ?? [], [data])

  const types = useMemo(
    () => [...new Set(events.map((e) => e.event_type))].sort(),
    [events],
  )
  const rootCauses = useMemo(
    () =>
      [...new Set(events.map((e) => e.root_cause).filter(Boolean))].sort() as string[],
    [events],
  )

  const filtered = useMemo(
    () =>
      events.filter((e) => {
        if (statusFilter.length && !statusFilter.includes(e.status)) return false
        if (typeFilter && e.event_type !== typeFilter) return false
        if (rcFilter && e.root_cause !== rcFilter) return false
        if (minAmt && toNum(e.amount) < Number(minAmt)) return false
        return true
      }),
    [events, statusFilter, typeFilter, rcFilter, minAmt],
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
      const nextSet = cur.includes(s)
        ? cur.filter((x) => x !== s)
        : [...cur, s]
      nextSet.forEach((x) => p.append('status', x))
    })

  if (loading) return <Skeleton rows={12} />
  if (error) return <ErrorState message={error} />

  return (
    <div className="space-y-4">
      <Card
        title="At-risk queue"
        action={
          <button
            type="button"
            className="rounded-lg px-2.5 py-1 text-xs text-ink-soft ring-1 ring-[var(--color-ring)]"
            onClick={() =>
              downloadCsv('at-risk-queue.csv', toCsv(columns, filtered))
            }
          >
            Export CSV
          </button>
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <div className="flex flex-wrap gap-1">
            {STATUSES.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => toggleStatus(s)}
                aria-pressed={statusFilter.includes(s)}
                className={`rounded-full px-2 py-0.5 ring-1 ring-[var(--color-ring)] ${
                  statusFilter.includes(s)
                    ? 'bg-surface-2 text-ink font-medium'
                    : 'text-ink-muted'
                }`}
              >
                {STATUS_LABEL[s]}
              </button>
            ))}
          </div>
          <select
            className="rounded-lg bg-surface-1 px-2 py-1 ring-1 ring-[var(--color-ring)] text-ink-soft"
            value={typeFilter}
            onChange={(e) =>
              patch((p) =>
                e.target.value ? p.set('type', e.target.value) : p.delete('type'),
              )
            }
          >
            <option value="">All types</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {labelEventType(t)}
              </option>
            ))}
          </select>
          <select
            className="rounded-lg bg-surface-1 px-2 py-1 ring-1 ring-[var(--color-ring)] text-ink-soft"
            value={rcFilter}
            onChange={(e) =>
              patch((p) =>
                e.target.value ? p.set('rc', e.target.value) : p.delete('rc'),
              )
            }
          >
            <option value="">All root causes</option>
            {rootCauses.map((r) => (
              <option key={r} value={r}>
                {labelRootCause(r as EventRead['root_cause'])}
              </option>
            ))}
          </select>
          <input
            type="number"
            placeholder="Min ₹"
            className="w-24 rounded-lg bg-surface-1 px-2 py-1 ring-1 ring-[var(--color-ring)] text-ink-soft"
            value={minAmt}
            onChange={(e) =>
              patch((p) =>
                e.target.value ? p.set('min', e.target.value) : p.delete('min'),
              )
            }
          />
          <span className="text-ink-muted">
            {filtered.length} of {events.length} cases
          </span>
        </div>

        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(e) => e.event_id}
          initialSort={{ key: 'amount', dir: 'desc' }}
          onRowClick={(e) => patch((p) => p.set('case', e.event_id))}
          rowAccent={(e) =>
            e.status === 'flagged' ? 'var(--color-status-critical)' : undefined
          }
          emptyLabel="No cases match these filters"
        />
      </Card>

      <DetailDrawer
        caseId={caseId}
        onClose={() => patch((p) => p.delete('case'))}
      />
    </div>
  )
}
