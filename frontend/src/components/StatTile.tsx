import { Link } from 'react-router-dom'
import { GlassCard } from './Card'

export interface StatTileData {
  label: string
  value: string
  sub?: string
  accent?: 'good' | 'warning' | 'critical' | 'serious'
  href?: string
}

const ACCENT: Record<NonNullable<StatTileData['accent']>, string> = {
  good: 'var(--color-status-good)',
  warning: 'var(--color-status-warning)',
  serious: 'var(--color-status-serious)',
  critical: 'var(--color-status-critical)',
}

export function StatTile({ label, value, sub, accent, href }: StatTileData) {
  const body = (
    <GlassCard className="h-full">
      <div className="flex items-center gap-2">
        {accent && (
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: ACCENT[accent] }}
            aria-hidden
          />
        )}
        <span className="text-xs uppercase tracking-wide text-ink-soft">
          {label}
        </span>
      </div>
      <div className="mt-2 text-2xl font-semibold text-ink">{value}</div>
      {sub && <div className="mt-1 text-xs text-ink-muted tabnum">{sub}</div>}
    </GlassCard>
  )
  return href ? (
    <Link to={href} className="block focus:outline-none">
      {body}
    </Link>
  ) : (
    body
  )
}
