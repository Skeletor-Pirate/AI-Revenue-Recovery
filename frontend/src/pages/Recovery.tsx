import { useMemo } from 'react'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { ChartCard, HBar, type HBarDatum } from '../components/ChartCard'
import { Card } from '../components/Card'
import { Skeleton, ErrorState } from '../components/Feedback'
import type { Column } from '../components/DataTable'
import { labelIntervention, labelRootCause } from '../api/actionLabels'
import { rootCauseColor } from '../charts/series'
import { formatINR, formatPct, toNum } from '../lib/format'
import type { ByIntervention, ByRootCause, EventRead } from '../api/types'

const ivCols: Column<ByIntervention>[] = [
  { key: 'iv', header: 'Intervention', render: (r) => labelIntervention(r.intervention), sortValue: (r) => r.intervention, csv: (r) => r.intervention },
  { key: 'rate', header: 'Recovery rate', numeric: true, render: (r) => formatPct(r.recovery_rate), sortValue: (r) => r.recovery_rate, csv: (r) => r.recovery_rate },
  { key: 'count', header: 'Cases', numeric: true, render: (r) => r.count, sortValue: (r) => r.count, csv: (r) => r.count },
  { key: 'recovered_count', header: 'Recovered', numeric: true, render: (r) => r.recovered_count, sortValue: (r) => r.recovered_count, csv: (r) => r.recovered_count },
  { key: 'recovered', header: '₹ recovered', numeric: true, render: (r) => formatINR(r.recovered), sortValue: (r) => toNum(r.recovered), csv: (r) => r.recovered },
  { key: 'at_risk', header: '₹ at risk', numeric: true, render: (r) => formatINR(r.at_risk), sortValue: (r) => toNum(r.at_risk), csv: (r) => r.at_risk },
]

const rcCols: Column<ByRootCause>[] = [
  { key: 'rc', header: 'Root cause', render: (r) => labelRootCause(r.root_cause), sortValue: (r) => r.root_cause, csv: (r) => r.root_cause },
  { key: 'count', header: 'Cases', numeric: true, render: (r) => r.count, sortValue: (r) => r.count, csv: (r) => r.count },
  { key: 'recovered_count', header: 'Recovered', numeric: true, render: (r) => r.recovered_count, sortValue: (r) => r.recovered_count, csv: (r) => r.recovered_count },
  { key: 'rate', header: 'Recovery rate', numeric: true, render: (r) => formatPct(r.recovery_rate), sortValue: (r) => r.recovery_rate, csv: (r) => r.recovery_rate },
]

interface AttemptBucket {
  label: string
  count: number
  overLimit: boolean
}

export function Recovery() {
  const metrics = useAsync(() => dataSource.getMetrics())
  const events = useAsync(() => dataSource.getEvents())

  const attemptBuckets = useMemo<AttemptBucket[]>(() => {
    const evs: EventRead[] = events.data?.events ?? []
    const buckets = [0, 1, 2, 3, 4]
    return buckets.map((b) => ({
      label: b === 4 ? '4+' : String(b),
      count: evs.filter((e) =>
        b === 4 ? e.attempts_so_far >= 4 : e.attempts_so_far === b,
      ).length,
      overLimit: b >= 3,
    }))
  }, [events.data])

  if (metrics.loading) return <Skeleton rows={10} />
  if (metrics.error) return <ErrorState message={metrics.error} />
  if (!metrics.data) return null
  const m = metrics.data

  const rateByIv: HBarDatum[] = [...m.by_intervention]
    .sort((a, b) => b.recovery_rate - a.recovery_rate)
    .map((r) => ({
      label: labelIntervention(r.intervention),
      value: Math.round(r.recovery_rate * 100),
      color: 'var(--color-series-1)',
      labelText: formatPct(r.recovery_rate),
    }))

  const amountByIv: HBarDatum[] = [...m.by_intervention]
    .sort((a, b) => toNum(b.recovered) - toNum(a.recovered))
    .map((r) => ({
      label: labelIntervention(r.intervention),
      value: toNum(r.recovered),
      color: 'var(--color-series-3)',
      labelText: formatINR(r.recovered),
    }))

  const rateByRc: HBarDatum[] = [...m.by_root_cause]
    .sort((a, b) => b.recovery_rate - a.recovery_rate)
    .map((r) => ({
      label: labelRootCause(r.root_cause),
      value: Math.round(r.recovery_rate * 100),
      color: rootCauseColor(r.root_cause),
      labelText: formatPct(r.recovery_rate),
    }))

  const maxAttempts = Math.max(1, ...attemptBuckets.map((b) => b.count))

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="Recovery rate by intervention"
          subtitle="benchmark = overall rate"
          tableColumns={ivCols}
          tableRows={m.by_intervention}
          rowKey={(r) => r.intervention}
        >
          <HBar
            data={rateByIv}
            height={300}
            benchmark={Math.round(m.overall_recovery_rate * 100)}
            benchmarkLabel="overall"
          />
        </ChartCard>

        <ChartCard
          title="₹ recovered by intervention"
          tableColumns={ivCols}
          tableRows={m.by_intervention}
          rowKey={(r) => r.intervention}
        >
          <HBar data={amountByIv} height={300} />
        </ChartCard>

        <ChartCard
          title="Recovery rate by root cause"
          tableColumns={rcCols}
          tableRows={m.by_root_cause}
          rowKey={(r) => r.root_cause}
        >
          <HBar data={rateByRc} height={340} />
        </ChartCard>

        <Card title="Attempts per case">
          {events.loading ? (
            <Skeleton rows={5} />
          ) : (
            <div className="space-y-2">
              {attemptBuckets.map((b) => (
                <div key={b.label} className="flex items-center gap-3 text-sm">
                  <span className="w-8 text-right text-ink-muted tabnum">
                    {b.label}
                  </span>
                  <div className="h-5 flex-1 rounded bg-surface-2">
                    <div
                      className="h-5 rounded"
                      style={{
                        width: `${(b.count / maxAttempts) * 100}%`,
                        background: b.overLimit
                          ? 'var(--color-status-warning)'
                          : 'var(--color-series-1)',
                      }}
                    />
                  </div>
                  <span className="w-8 tabnum text-ink-soft">{b.count}</span>
                </div>
              ))}
              <p className="pt-1 text-xs text-ink-muted">
                Bars at 3+ attempts (amber) hit the stopping rule — no more
                automatic retries, the case is handed to a human.
              </p>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
