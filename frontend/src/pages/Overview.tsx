import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { StatTile } from '../components/StatTile'
import { ChartCard, HBar, type HBarDatum } from '../components/ChartCard'
import { Skeleton, ErrorState } from '../components/Feedback'
import type { Column } from '../components/DataTable'
import { rootCauseColor } from '../charts/series'
import { labelIntervention, labelRootCause } from '../api/actionLabels'
import {
  formatHours,
  formatINR,
  formatPct,
  toNum,
} from '../lib/format'
import type { ByIntervention, ByRootCause } from '../api/types'

const rcCols: Column<ByRootCause>[] = [
  {
    key: 'rc',
    header: 'Root cause',
    render: (r) => labelRootCause(r.root_cause),
    sortValue: (r) => r.root_cause,
    csv: (r) => r.root_cause,
  },
  {
    key: 'recovered',
    header: '₹ recovered',
    numeric: true,
    render: (r) => formatINR(r.recovered),
    sortValue: (r) => toNum(r.recovered),
    csv: (r) => r.recovered,
  },
  {
    key: 'at_risk',
    header: '₹ at risk',
    numeric: true,
    render: (r) => formatINR(r.at_risk),
    sortValue: (r) => toNum(r.at_risk),
    csv: (r) => r.at_risk,
  },
  {
    key: 'rate',
    header: 'Recovery rate',
    numeric: true,
    render: (r) => formatPct(r.recovery_rate),
    sortValue: (r) => r.recovery_rate,
    csv: (r) => r.recovery_rate,
  },
]

const ivCols: Column<ByIntervention>[] = [
  {
    key: 'iv',
    header: 'Intervention',
    render: (r) => labelIntervention(r.intervention),
    sortValue: (r) => r.intervention,
    csv: (r) => r.intervention,
  },
  {
    key: 'rate',
    header: 'Recovery rate',
    numeric: true,
    render: (r) => formatPct(r.recovery_rate),
    sortValue: (r) => r.recovery_rate,
    csv: (r) => r.recovery_rate,
  },
  {
    key: 'count',
    header: 'Cases',
    numeric: true,
    render: (r) => r.count,
    sortValue: (r) => r.count,
    csv: (r) => r.count,
  },
  {
    key: 'recovered',
    header: '₹ recovered',
    numeric: true,
    render: (r) => formatINR(r.recovered),
    sortValue: (r) => toNum(r.recovered),
    csv: (r) => r.recovered,
  },
]

export function Overview() {
  const { data: m, loading, error } = useAsync(() => dataSource.getMetrics())

  if (loading) return <Skeleton rows={10} />
  if (error) return <ErrorState message={error} />
  if (!m) return null

  const atRisk = toNum(m.total_at_risk)
  const recovered = toNum(m.total_recovered)
  const sb = m.status_breakdown

  const recoveredByCause: HBarDatum[] = [...m.by_root_cause]
    .sort((a, b) => toNum(b.recovered) - toNum(a.recovered))
    .map((r) => ({
      label: labelRootCause(r.root_cause),
      value: toNum(r.recovered),
      color: rootCauseColor(r.root_cause),
      labelText: formatINR(r.recovered),
    }))

  const rateByIntervention: HBarDatum[] = [...m.by_intervention]
    .sort((a, b) => b.recovery_rate - a.recovery_rate)
    .map((r) => ({
      label: labelIntervention(r.intervention),
      value: Math.round(r.recovery_rate * 100),
      color: 'var(--color-series-1)',
      labelText: formatPct(r.recovery_rate),
    }))

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label="₹ at risk"
          value={formatINR(m.total_at_risk)}
          sub={`${m.event_count} cases this batch`}
        />
        <StatTile
          label="₹ recovered"
          value={formatINR(m.total_recovered)}
          sub={
            m.human_recovered && Number(m.human_recovered) > 0
              ? `${formatINR(m.ai_recovered ?? 0)} agents · ${formatINR(m.human_recovered)} humans`
              : atRisk
                ? `${Math.round((recovered / atRisk) * 100)}% of ₹ at risk`
                : undefined
          }
          accent="good"
        />
        <StatTile
          label="Recovery rate"
          value={formatPct(m.overall_recovery_rate)}
          sub="₹ recovered ÷ ₹ at risk"
        />
        <StatTile
          label="Avg time to recover"
          value={formatHours(m.avg_hours_to_recovery)}
          sub="simulated"
        />
        <StatTile
          label="Needs a human"
          value={String(m.tickets?.needs_attention ?? sb.flagged ?? 0)}
          sub={
            m.tickets
              ? `${m.tickets.open} open · ${m.tickets.under_review} under review`
              : 'held for human review'
          }
          accent="critical"
          href="/attention"
        />
        <StatTile
          label="Exceptions"
          value={String(sb.exception ?? 0)}
          sub="see the honest list"
          accent="warning"
          href="/exceptions"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard
          title="₹ recovered by root cause"
          subtitle="which problems we actually claw money back from"
          tableColumns={rcCols}
          tableRows={m.by_root_cause}
          rowKey={(r) => r.root_cause}
        >
          <HBar data={recoveredByCause} height={320} />
        </ChartCard>

        <ChartCard
          title="Recovery rate by intervention"
          subtitle="how often each recovery approach works"
          tableColumns={ivCols}
          tableRows={m.by_intervention}
          rowKey={(r) => r.intervention}
          footer={`Benchmark line = overall recovery rate, ${formatPct(m.overall_recovery_rate)}.`}
        >
          <HBar
            data={rateByIntervention}
            height={320}
            benchmark={Math.round(m.overall_recovery_rate * 100)}
            benchmarkLabel="overall"
          />
        </ChartCard>
      </div>
    </div>
  )
}
