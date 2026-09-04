import { useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card } from './Card'
import { DataTable, type Column } from './DataTable'
import { TableViewToggle, type ChartView } from './TableViewToggle'
import { AXIS } from '../charts/series'

interface ChartCardProps<T> {
  title: ReactNode
  subtitle?: ReactNode
  tableColumns: Column<T>[]
  tableRows: T[]
  rowKey: (row: T) => string
  children: ReactNode
  footer?: ReactNode
}

export function ChartCard<T>({
  title,
  subtitle,
  tableColumns,
  tableRows,
  rowKey,
  children,
  footer,
}: ChartCardProps<T>) {
  const [view, setView] = useState<ChartView>('chart')
  return (
    <Card
      title={
        <span>
          {title}
          {subtitle && (
            <span className="ml-2 font-normal text-ink-muted">{subtitle}</span>
          )}
        </span>
      }
      action={<TableViewToggle view={view} onChange={setView} />}
    >
      {view === 'chart' ? (
        children
      ) : (
        <DataTable columns={tableColumns} rows={tableRows} rowKey={rowKey} />
      )}
      {footer && <div className="mt-3 text-xs text-ink-muted">{footer}</div>}
    </Card>
  )
}

export interface HBarDatum {
  label: string
  value: number
  color: string
  labelText?: string
}

// Single-series horizontal bar. Colour is supplied per row (identity colour),
// never cycled by Recharts.
export function HBar({
  data,
  height = 260,
  benchmark,
  benchmarkLabel,
}: {
  data: HBarDatum[]
  height?: number
  benchmark?: number
  benchmarkLabel?: string
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        layout="vertical"
        data={data}
        margin={{ left: 8, right: 48, top: 4, bottom: 4 }}
      >
        <CartesianGrid stroke={AXIS.grid} horizontal={false} />
        <XAxis
          type="number"
          stroke={AXIS.baseline}
          tick={{ fill: AXIS.tick, fontSize: 12 }}
        />
        <YAxis
          type="category"
          dataKey="label"
          width={150}
          stroke={AXIS.baseline}
          tick={{ fill: AXIS.tick, fontSize: 12 }}
        />
        <Tooltip
          cursor={{ fill: 'var(--color-surface-2)' }}
          contentStyle={{
            background: 'var(--color-surface-1)',
            border: '1px solid var(--color-ring)',
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        {benchmark != null && (
          <ReferenceLine
            x={benchmark}
            stroke="var(--color-ink-muted)"
            strokeDasharray="3 3"
            label={{ value: benchmarkLabel, fontSize: 11, fill: 'var(--color-ink-muted)' }}
          />
        )}
        <Bar dataKey="value" isAnimationActive={false} radius={[0, 4, 4, 0]}>
          {data.map((d) => (
            <Cell key={d.label} fill={d.color} />
          ))}
          <LabelList
            dataKey="labelText"
            position="right"
            style={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
