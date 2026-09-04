import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { Card } from '../components/Card'
import { DataTable, type Column } from '../components/DataTable'
import { StatusPill } from '../components/StatusPill'
import { SimulateSession } from '../components/SimulateSession'
import { Skeleton, ErrorState } from '../components/Feedback'
import { labelEventType, labelRootCause } from '../api/actionLabels'
import { formatINR, toNum } from '../lib/format'
import type { EventRead } from '../api/types'

const columns: Column<EventRead>[] = [
  {
    key: 'event_id',
    header: 'Case',
    render: (e) => <span className="font-mono text-xs">{e.event_id}</span>,
    sortValue: (e) => e.event_id,
    csv: (e) => e.event_id,
  },
  {
    key: 'customer_name',
    header: 'Customer',
    render: (e) => e.customer_name ?? <span className="font-mono text-xs">{e.customer_id}</span>,
    sortValue: (e) => e.customer_name ?? e.customer_id,
    csv: (e) => e.customer_name ?? e.customer_id,
  },
  {
    key: 'event_type',
    header: 'Type',
    render: (e) => labelEventType(e.event_type),
    sortValue: (e) => e.event_type,
    csv: (e) => e.event_type,
  },
  {
    key: 'root_cause',
    header: 'Root cause',
    render: (e) => labelRootCause(e.root_cause),
    sortValue: (e) => e.root_cause ?? '',
    csv: (e) => e.root_cause ?? '',
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
    csv: (e) => e.status,
  },
]

export function Playground() {
  const { data, loading, error } = useAsync(() => dataSource.getEvents())
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')

  const events = useMemo(() => data?.events ?? [], [data])
  const simulateId = params.get('simulate')

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return events
    return events.filter(
      (e) =>
        e.event_id.toLowerCase().includes(q) ||
        e.customer_id.toLowerCase().includes(q) ||
        (e.customer_name ?? '').toLowerCase().includes(q),
    )
  }, [events, search])

  if (loading) return <Skeleton rows={12} />
  if (error) return <ErrorState message={error} />

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-base font-semibold text-ink">Playground</h1>
        <p className="mt-0.5 max-w-2xl text-xs text-ink-muted">
          Pick any case and talk to the AI yourself — as the customer, or watch two
          AIs role-play the whole outreach. A rehearsal: nothing you do here is saved
          to the dashboard or counted in the metrics.
        </p>
      </div>

      <Card
        title="Pick a case to simulate"
        action={
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search case, customer…"
            className="rounded-lg bg-surface-2 px-2.5 py-1 text-xs text-ink ring-1 ring-[var(--color-ring)] focus:outline-none focus:ring-2"
          />
        }
      >
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(e) => e.event_id}
          initialSort={{ key: 'amount', dir: 'desc' }}
          onRowClick={(e) => setParams({ simulate: e.event_id })}
          emptyLabel="No cases match this search"
        />
      </Card>

      <SimulateSession
        eventId={simulateId ?? ''}
        isOpen={!!simulateId}
        onClose={() => {
          const next = new URLSearchParams(params)
          next.delete('simulate')
          setParams(next)
        }}
      />
    </div>
  )
}
