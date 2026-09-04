import React, { useState } from 'react'
import { dataSource } from '../api/dataSource'

interface Props {
  eventId: string
  isOpen: boolean
  onClose: () => void
  onSuccess: () => void
}

export const PTPModal: React.FC<Props> = ({ eventId, isOpen, onClose, onSuccess }) => {
  const [promisedDate, setPromisedDate] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() + 3)
    return d.toISOString().split('T')[0]
  })
  const [notes, setNotes] = useState('Customer committed to clear dues via UPI')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isOpen) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const dt = new Date(`${promisedDate}T09:00:00Z`).toISOString()
      await dataSource.recordPTP(eventId, dt, notes)
      onSuccess()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to record Promise-to-Pay')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fade-in p-4">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl flex flex-col gap-4">
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <span className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
              🤝
            </span>
            <div>
              <h2 className="text-base font-semibold text-white">
                Record Promise-to-Pay (PTP)
              </h2>
              <p className="text-xs text-slate-400">
                Pause automated escalation for {eventId}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded-md"
          >
            ✕
          </button>
        </div>

        {error && (
          <div className="p-3 rounded-lg bg-red-950/40 border border-red-800/40 text-red-300 text-xs">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">
              Promised Payment Date
            </label>
            <input
              type="date"
              value={promisedDate}
              onChange={(e) => setPromisedDate(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">
              Conversation Notes & Context
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="e.g. Account manager confirmed clearance after board meeting..."
            />
          </div>

          <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-700/50 text-[11px] text-slate-400">
            ℹ️ Recording a PTP pauses automated aggressive retries until the promised date (+24h grace window). If unpaid after grace, escalation resumes automatically.
          </div>

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium shadow-lg shadow-indigo-600/20"
            >
              {submitting ? 'Recording...' : 'Confirm Promise-to-Pay'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
