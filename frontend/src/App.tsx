import { useEffect, useState } from 'react'
import { api } from './api/client'

export default function App() {
  const [health, setHealth] = useState<string>('checking…')

  useEffect(() => {
    api
      .health()
      .then((r) => setHealth(r.status))
      .catch((e) => setHealth(`error: ${e.message}`))
  }, [])

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-6 py-4">
          <h1 className="text-lg font-semibold">AI Revenue Recovery</h1>
          <p className="text-sm text-slate-500">
            Detect revenue at risk → diagnose root cause → run a bounded recovery
            workflow
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <p className="text-sm">
          backend health:{' '}
          <span className="font-mono font-medium">{health}</span>
        </p>
        {/* pages land here: at-risk queue, per-case decision trail, metrics, exceptions */}
      </main>
    </div>
  )
}
