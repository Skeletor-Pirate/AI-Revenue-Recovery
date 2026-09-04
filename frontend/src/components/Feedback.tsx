import type { ReactNode } from 'react'

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-8 animate-pulse rounded-md bg-surface-2"
        />
      ))}
    </div>
  )
}

export function EmptyState({
  title = 'Nothing here yet',
  hint,
  action,
}: {
  title?: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl bg-surface-2 px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="max-w-sm text-xs text-ink-muted">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-xl px-4 py-6 text-sm ring-1 ring-[var(--color-ring)]">
      <p className="font-medium" style={{ color: 'var(--color-status-critical)' }}>
        Couldn&apos;t load this data
      </p>
      <p className="mt-1 text-xs text-ink-muted break-words">{message}</p>
    </div>
  )
}
