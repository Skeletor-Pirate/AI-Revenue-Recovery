export type ChartView = 'chart' | 'table'

export function TableViewToggle({
  view,
  onChange,
}: {
  view: ChartView
  onChange: (v: ChartView) => void
}) {
  return (
    <div
      className="inline-flex rounded-lg ring-1 ring-[var(--color-ring)] text-xs"
      role="group"
      aria-label="Chart or table view"
    >
      {(['chart', 'table'] as const).map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onChange(v)}
          aria-pressed={view === v}
          className={`px-2.5 py-1 capitalize first:rounded-l-lg last:rounded-r-lg ${
            view === v ? 'bg-surface-2 text-ink font-medium' : 'text-ink-muted'
          }`}
        >
          {v}
        </button>
      ))}
    </div>
  )
}
