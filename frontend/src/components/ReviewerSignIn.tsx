import { useState } from 'react'
import { looksLikeEmail, setEmployeeEmail } from '../lib/session'

/**
 * Identify the reviewer. Not authentication -- the email is stamped on every
 * ticket the person takes or closes so the audit trail names a real accountable
 * human. Real deployment puts SSO in front of the dashboard.
 */
export function ReviewerSignIn({ onSignIn }: { onSignIn: (email: string) => void }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const email = value.trim()
    if (!looksLikeEmail(email)) {
      setError('Enter a valid work email address.')
      return
    }
    setEmployeeEmail(email)
    onSignIn(email)
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-3 rounded-xl bg-surface-2 px-6 py-8"
    >
      <div>
        <p className="text-sm font-medium text-ink">Sign in to review tickets</p>
        <p className="mt-1 max-w-lg text-xs text-ink-muted">
          Your work email is recorded on every ticket you take or close, so the
          audit trail names the person who made each call. You can still read the
          queue without signing in.
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-2">
        <label className="sr-only" htmlFor="reviewer-email">
          Work email
        </label>
        <input
          id="reviewer-email"
          type="email"
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setError(null)
          }}
          placeholder="you@company.com"
          className="w-64 rounded-lg bg-surface-1 px-3 py-2 text-sm text-ink ring-1 ring-[var(--color-ring)] focus:outline-none focus:ring-2"
        />
        <button
          type="submit"
          className="rounded-lg bg-surface-1 px-3 py-2 text-sm font-medium text-ink ring-1 ring-[var(--color-ring)] hover:bg-surface-2"
        >
          Start reviewing
        </button>
      </div>
      {error && (
        <p className="text-xs" style={{ color: 'var(--color-status-critical)' }}>
          {error}
        </p>
      )}
    </form>
  )
}
