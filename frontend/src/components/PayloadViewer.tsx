import { useState } from 'react'

export function PayloadViewer({ payload }: { payload: unknown }) {
  const [copied, setCopied] = useState(false)
  const text = JSON.stringify(payload, null, 2)

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-ink-muted">Decision payload</span>
        <button
          type="button"
          className="text-xs text-ink-muted hover:text-ink"
          onClick={() => {
            void navigator.clipboard?.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 1200)
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="mt-1 max-h-56 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-xs tabnum text-ink-soft">
        {text}
      </pre>
    </div>
  )
}
