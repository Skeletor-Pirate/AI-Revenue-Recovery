import type { PropsWithChildren, ReactNode } from 'react'

interface CardProps {
  title?: ReactNode
  action?: ReactNode
  className?: string
}

export function Card({
  title,
  action,
  className = '',
  children,
}: PropsWithChildren<CardProps>) {
  return (
    <section
      className={`rounded-2xl bg-surface-1 ring-1 ring-[var(--color-ring)] p-5 ${className}`}
    >
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

// Lightweight frosted surface for focus chrome (KPI tiles, drawer, alert card).
// Uses backdrop-blur only — the full liquid-glass refraction lib is a Phase-C
// polish item; meaning never rides on the effect (revrec-dashboard GUIDE rule).
export function GlassCard({
  className = '',
  children,
}: PropsWithChildren<{ className?: string }>) {
  return (
    <div
      className={`rounded-[var(--glass-radius)] p-5 ring-1 ring-[var(--color-ring)] backdrop-blur-md ${className}`}
      style={{
        background:
          'linear-gradient(180deg, var(--glass-tint-from), var(--glass-tint-to))',
        boxShadow: '0 12px 32px rgba(0,0,0,0.10)',
      }}
    >
      {children}
    </div>
  )
}
