import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { Card, GlassCard } from '../components/Card'
import { ChartCard, HBar, type HBarDatum } from '../components/ChartCard'
import { DataTable, type Column } from '../components/DataTable'
import { toCsv, downloadCsv } from '../lib/csv'
import { EmptyState, Skeleton, ErrorState } from '../components/Feedback'
import { labelEventType, labelRootCause } from '../api/actionLabels'
import { formatINR, formatINRPrecise, toNum } from '../lib/format'
import type { ExceptionRow } from '../api/types'

const cols: Column<ExceptionRow>[] = [
  {
    key: 'event_id',
    header: 'Case',
    render: (r) => (
      <Link
        to={`/queue?case=${encodeURIComponent(r.event_id)}`}
        className="font-mono text-xs underline decoration-dotted"
      >
        {r.event_id}
      </Link>
    ),
    sortValue: (r) => r.event_id,
    csv: (r) => r.event_id,
  },
  {
    key: 'type',
    header: 'Type',
    render: (r) => labelEventType(r.event_type),
    sortValue: (r) => r.event_type,
    csv: (r) => r.event_type,
  },
  {
    key: 'amount',
    header: '₹ un-recovered',
    numeric: true,
    render: (r) => formatINR(r.amount),
    sortValue: (r) => toNum(r.amount),
    csv: (r) => r.amount,
  },
  {
    key: 'rc',
    header: 'Root cause',
    render: (r) => labelRootCause(r.root_cause),
    sortValue: (r) => r.root_cause ?? 'zzz',
    csv: (r) => r.root_cause ?? '',
  },
  {
    key: 'reason',
    header: 'Why it was not recovered',
    render: (r) => <span className="text-ink-soft">{r.reason}</span>,
    csv: (r) => r.reason,
  },
]

export function Exceptions() {
  const { data: m, loading, error } = useAsync(() => dataSource.getMetrics())

  const byReason = useMemo<HBarDatum[]>(() => {
    if (!m) return []
    const map = new Map<string, number>()
    for (const ex of m.exceptions) {
      map.set(ex.reason, (map.get(ex.reason) ?? 0) + toNum(ex.amount))
    }
    return [...map.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([reason, value]) => ({
        label: reason.length > 40 ? reason.slice(0, 38) + '…' : reason,
        value,
        color: 'var(--color-status-warning)',
        labelText: formatINR(value),
      }))
  }, [m])

  if (loading) return <Skeleton rows={10} />
  if (error) return <ErrorState message={error} />
  if (!m) return null

  const gap = toNum(m.total_at_risk) - toNum(m.total_recovered)
  const fc = m.fraud_cluster

  return (
    <div className="space-y-6">
      <GlassCard className="ring-2">
        <div className="flex items-start gap-3">
          <span
            className="mt-0.5 inline-block h-2.5 w-2.5 rounded-full"
            style={{ background: 'var(--color-status-critical)' }}
            aria-hidden
          />
          <div>
            <h2 className="text-sm font-semibold text-ink">
              Fraud cluster halted — {fc.flagged_event_ids.length} payments held
              for human review
            </h2>
            <p className="mt-1 text-sm text-ink-soft">{fc.reason}</p>
            <p className="mt-2 text-sm text-ink-soft">
              {'Diagnosis re-classified this cluster as '}
              <span className="font-semibold text-ink">suspected fraud</span>
              {'. The Recovery agent refused to act on any of these payments '}
              {'(no retries, no outreach) and escalated them for a human to review.'}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {fc.flagged_event_ids.map((id) => (
                <Link
                  key={id}
                  to={`/queue?case=${encodeURIComponent(id)}`}
                  className="rounded-full px-2 py-0.5 font-mono text-xs ring-1 ring-[var(--color-ring)]"
                >
                  {id}
                </Link>
              ))}
            </div>
          </div>
        </div>
      </GlassCard>

      <ChartCard
        title="₹ un-recovered by reason"
        subtitle="the honest gap, grouped by why"
        tableColumns={[
          {
            key: 'reason',
            header: 'Reason',
            render: (r: HBarDatum) => r.label,
            csv: (r: HBarDatum) => r.label,
          },
          {
            key: 'amount',
            header: '₹',
            numeric: true,
            render: (r: HBarDatum) => formatINR(r.value),
            sortValue: (r: HBarDatum) => r.value,
            csv: (r: HBarDatum) => r.value,
          },
        ]}
        tableRows={byReason}
        rowKey={(r) => r.label}
        footer={
          <>
            Total ₹ at risk {formatINRPrecise(m.total_at_risk)} − ₹ recovered{' '}
            {formatINRPrecise(m.total_recovered)} ={' '}
            <strong>{formatINRPrecise(gap)}</strong> still to recover across{' '}
            {m.status_breakdown.exception ?? 0} exceptions and{' '}
            {m.status_breakdown.flagged ?? 0} halted cases.
          </>
        }
      >
        {byReason.length ? (
          <HBar data={byReason} height={280} />
        ) : (
          <EmptyState
            title="No exceptions in this batch"
            hint="Every at-risk case was either recovered or is still in progress."
          />
        )}
      </ChartCard>

      <Card
        title={`Exception list (${m.exceptions.length})`}
        action={
          m.exceptions.length ? (
            <button
              type="button"
              className="rounded-lg px-2.5 py-1 text-xs text-ink-soft ring-1 ring-[var(--color-ring)]"
              onClick={() =>
                downloadCsv('exceptions.csv', toCsv(cols, m.exceptions))
              }
            >
              Export CSV
            </button>
          ) : undefined
        }
      >
        {m.exceptions.length ? (
          <DataTable
            columns={cols}
            rows={m.exceptions}
            rowKey={(r) => r.event_id}
            initialSort={{ key: 'amount', dir: 'desc' }}
          />
        ) : (
          <EmptyState
            title="Nothing could not be recovered"
            hint="When a case cannot be recovered, it is listed here in full with the reason — never hidden."
          />
        )}
      </Card>
    </div>
  )
}
