import React from 'react'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'

interface Props {
  eventId: string
}

export const SequencerTimeline: React.FC<Props> = ({ eventId }) => {
  const { data, loading } = useAsync(
    () => (eventId ? dataSource.getSequencerSchedule(eventId) : Promise.resolve({ event_id: '', schedule: [], rail: 'upi_autopay' })),
    [eventId],
  )
  const schedule = data?.schedule || []
  const rail = data?.rail || 'upi_autopay'

  if (loading) {
    return (
      <div className="py-6 flex items-center justify-center text-xs text-slate-500">
        Calculating optimized retry schedule...
      </div>
    )
  }

  if (!schedule.length) {
    return (
      <div className="py-4 text-xs text-slate-500 text-center">
        No active mandate retry sequence generated for this event.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between pb-2 border-b border-slate-800">
        <span className="text-xs font-semibold text-slate-300">
          Mandate Retry Sequencer Plan
        </span>
        <span className="text-[10px] px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 uppercase font-mono">
          Rail: {rail.replace('_', ' ')}
        </span>
      </div>

      <div className="relative pl-6 space-y-4 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-slate-800">
        {schedule.map((step) => {
          const dt = new Date(step.scheduled_time)
          const formattedTime = isNaN(dt.getTime())
            ? step.scheduled_time
            : dt.toLocaleString('en-IN', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
              })

          return (
            <div key={step.step_number} className="relative group">
              {/* Dot */}
              <div className="absolute -left-6 top-1 w-2.5 h-2.5 rounded-full bg-blue-500 ring-4 ring-slate-900" />

              <div className="p-3 rounded-xl bg-slate-850 border border-slate-800 hover:border-slate-700 transition-all text-xs">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-white">
                    Step {step.step_number}: {step.action.replace(/_/g, ' ')}
                  </span>
                  <span className="text-[10px] text-slate-400 font-mono">
                    {formattedTime}
                  </span>
                </div>

                <p className="text-slate-400 text-[11px] mb-2 leading-relaxed">
                  {step.rationale}
                </p>

                <div className="flex items-center justify-between text-[10px] pt-1.5 border-t border-slate-800/60">
                  <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                    {step.compliance_tag}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-400">Success Prob:</span>
                    <span className="text-emerald-400 font-semibold">
                      {Math.round(step.expected_recovery_prob * 100)}%
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
