import React, { useEffect, useRef, useState } from 'react'
import { dataSource } from '../api/dataSource'
import { formatINR } from '../lib/format'
import { labelEventType, labelRootCause } from '../api/actionLabels'
import type {
  PlaygroundChannel,
  PlaygroundMode,
  PlaygroundOutcome,
  PlaygroundPersona,
  PlaygroundTurn,
} from '../api/types'

interface Props {
  eventId: string
  isOpen: boolean
  onClose: () => void
}

type Phase = 'setup' | 'connecting' | 'live' | 'ended'

const OUTCOME_LABEL: Record<PlaygroundOutcome, string> = {
  ongoing: 'In progress',
  resolved: 'Resolved',
  escalated: 'Escalated to a human',
  halted: 'Halted',
}

const OUTCOME_COLOR: Record<PlaygroundOutcome, string> = {
  ongoing: 'text-slate-300 bg-slate-800/60 border-slate-700',
  resolved: 'text-emerald-300 bg-emerald-950/40 border-emerald-800/50',
  escalated: 'text-amber-300 bg-amber-950/40 border-amber-800/50',
  halted: 'text-red-300 bg-red-950/40 border-red-800/50',
}

type TurnWithAudio = PlaygroundTurn & { audio_base64?: string }

export const SimulateSession: React.FC<Props> = ({ eventId, isOpen, onClose }) => {
  const [phase, setPhase] = useState<Phase>('setup')
  const [mode, setMode] = useState<PlaygroundMode>('interactive')
  const [channel, setChannel] = useState<PlaygroundChannel>('message')
  const [ticketRef, setTicketRef] = useState('')
  const [persona, setPersona] = useState<PlaygroundPersona | null>(null)
  const [history, setHistory] = useState<PlaygroundTurn[]>([])
  const [outcome, setOutcome] = useState<PlaygroundOutcome>('ongoing')
  const [reasoning, setReasoning] = useState('')
  const [takenOver, setTakenOver] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [speakingIndex, setSpeakingIndex] = useState<number | null>(null)

  const audioRef = useRef<HTMLAudioElement | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)

  const reset = () => {
    setPhase('setup')
    setHistory([])
    setOutcome('ongoing')
    setReasoning('')
    setTakenOver(false)
    setInput('')
    setError(null)
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
  }

  useEffect(() => {
    if (!isOpen) reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, eventId])

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  const playIfAudio = (turn: TurnWithAudio, index: number) => {
    if (!turn.audio_base64) return
    const el = new Audio(`data:audio/wav;base64,${turn.audio_base64}`)
    audioRef.current = el
    setSpeakingIndex(index)
    el.onended = () => setSpeakingIndex(null)
    el.onerror = () => setSpeakingIndex(null)
    el.play().catch(() => setSpeakingIndex(null))
  }

  const begin = async () => {
    setPhase('connecting')
    setError(null)
    try {
      const res = await dataSource.startPlayground(eventId, mode)
      setChannel(res.channel)
      setTicketRef(res.ticket_ref)
      setPersona(res.persona)
      setHistory(res.history)
      setOutcome(res.outcome)
      setPhase('live')
      playIfAudio(res.opening_turn as TurnWithAudio, 0)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the simulation')
      setPhase('setup')
    }
  }

  const applyOutcome = (next: PlaygroundOutcome, why: string) => {
    setOutcome(next)
    setReasoning(why)
    if (next !== 'ongoing') setPhase('ended')
  }

  const send = async () => {
    const message = input.trim()
    if (!message || busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await dataSource.sendPlaygroundMessage(eventId, history, message, channel)
      setHistory(res.history)
      setInput('')
      playIfAudio(res.turn as TurnWithAudio, res.history.length - 1)
      applyOutcome(res.outcome, res.reasoning)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The agent could not reply')
    } finally {
      setBusy(false)
    }
  }

  const advance = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await dataSource.advancePlayground(eventId, history, channel)
      setHistory(res.history)
      playIfAudio(res.customer_turn as TurnWithAudio, res.history.length - 2)
      applyOutcome(res.outcome, res.reasoning)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The conversation could not advance')
    } finally {
      setBusy(false)
    }
  }

  const playToResolution = async () => {
    let current = history
    for (let i = 0; i < 8; i++) {
      setBusy(true)
      try {
        const res = await dataSource.advancePlayground(eventId, current, channel)
        current = res.history
        setHistory(res.history)
        applyOutcome(res.outcome, res.reasoning)
        if (res.outcome !== 'ongoing') break
      } catch (err) {
        setError(err instanceof Error ? err.message : 'The conversation could not advance')
        break
      } finally {
        setBusy(false)
      }
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-xl bg-slate-900 border-l border-slate-800 p-6 flex flex-col h-full shadow-2xl overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <span className="p-2 rounded-lg bg-purple-500/10 text-purple-400">🧪</span>
            <div>
              <h2 className="text-lg font-semibold text-white">Simulate</h2>
              <p className="text-xs text-slate-400">Talk to the AI yourself · {eventId}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white p-1 rounded-md hover:bg-slate-800">
            ✕
          </button>
        </div>

        {/* Rehearsal banner — persistent throughout */}
        <div className="mt-3 px-3 py-2 rounded-lg bg-purple-950/30 border border-purple-800/40 text-[11px] text-purple-300">
          🧪 Rehearsal — nothing here is saved to the dashboard or counted in the metrics.
        </div>

        {error && (
          <div className="mt-3 px-3 py-2 rounded-lg bg-red-950/40 border border-red-800/40 text-xs text-red-300">
            {error}
          </div>
        )}

        {phase === 'setup' && (
          <div className="flex-1 py-6 flex flex-col gap-5">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
                Who plays the customer?
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => setMode('interactive')}
                  className={`flex-1 px-3 py-3 rounded-xl text-left border text-xs transition-colors ${
                    mode === 'interactive'
                      ? 'bg-blue-950/40 border-blue-700 text-blue-200'
                      : 'bg-slate-800/60 border-slate-700 text-slate-300'
                  }`}
                >
                  <div className="font-semibold mb-1">🎤 I&apos;ll play the customer</div>
                  <div className="text-slate-400">Type your own lines, ask it anything.</div>
                </button>
                <button
                  onClick={() => setMode('auto')}
                  className={`flex-1 px-3 py-3 rounded-xl text-left border text-xs transition-colors ${
                    mode === 'auto'
                      ? 'bg-blue-950/40 border-blue-700 text-blue-200'
                      : 'bg-slate-800/60 border-slate-700 text-slate-300'
                  }`}
                >
                  <div className="font-semibold mb-1">▶ Watch two AIs talk</div>
                  <div className="text-slate-400">A second AI plays the customer/business.</div>
                </button>
              </div>
            </div>

            <button
              onClick={begin}
              className="px-4 py-3 rounded-xl font-medium text-sm bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-600/20"
            >
              Start the rehearsal →
            </button>
          </div>
        )}

        {phase === 'connecting' && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500" />
            <p className="text-xs text-slate-400">Opening the rehearsal…</p>
          </div>
        )}

        {(phase === 'live' || phase === 'ended') && persona && (
          <div className="flex-1 py-4 flex flex-col gap-4">
            {/* Persona / ticket card */}
            <div className="p-4 rounded-xl bg-slate-800/60 border border-slate-700/50">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-200">
                  {channel === 'call' ? '📞 Simulated call' : '💬 Simulated WhatsApp message'}
                </span>
                <span className="text-[10px] font-mono text-slate-500">{ticketRef}</span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-slate-400">
                <div>
                  Customer: <span className="text-slate-200">{persona.name}</span>
                </div>
                <div>
                  Amount: <span className="text-slate-200">{formatINR(persona.amount)}</span>
                </div>
                {persona.phone_masked && (
                  <div>
                    Phone: <span className="font-mono text-slate-300">{persona.phone_masked}</span>
                  </div>
                )}
                {persona.bank_account_masked && (
                  <div>
                    Bank a/c: <span className="font-mono text-slate-300">{persona.bank_account_masked}</span>
                  </div>
                )}
                {persona.upi_vpa && (
                  <div>
                    UPI: <span className="font-mono text-slate-300">{persona.upi_vpa}</span>
                  </div>
                )}
                <div>
                  Root cause: <span className="text-slate-200">{labelRootCause(persona.root_cause)}</span>
                </div>
              </div>
              <p className="mt-2 text-[10px] text-slate-500">
                {labelEventType(persona.event_type)} · synthetic test data, not a real record
              </p>
            </div>

            {/* Transcript */}
            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Transcript</h3>
              <div className="space-y-3">
                {history.map((turn, i) => {
                  const isAgent = turn.speaker === 'agent'
                  const isSpeaking = speakingIndex === i
                  return (
                    <div
                      key={i}
                      className={`p-3.5 rounded-xl text-sm transition-all ${
                        isAgent
                          ? 'bg-blue-950/40 border border-blue-800/40 text-blue-100 ml-4'
                          : 'bg-slate-800/80 border border-slate-700/60 text-slate-200 mr-4'
                      } ${isSpeaking ? 'ring-2 ring-blue-400 scale-[1.01]' : ''}`}
                    >
                      <div className="text-[11px] font-semibold mb-1 capitalize">
                        <span className={isAgent ? 'text-blue-400' : 'text-slate-400'}>
                          {isAgent ? 'Resolver' : persona.is_business ? 'Business' : 'Customer'}
                        </span>
                      </div>
                      <p className="leading-relaxed">{turn.text}</p>
                    </div>
                  )
                })}
                <div ref={transcriptEndRef} />
              </div>
            </div>

            {/* Outcome banner */}
            {outcome !== 'ongoing' && (
              <div className={`p-4 rounded-xl border text-xs ${OUTCOME_COLOR[outcome]}`}>
                <p className="font-semibold mb-1">{OUTCOME_LABEL[outcome]}</p>
                {reasoning && <p className="text-[11px] opacity-90">{reasoning}</p>}
              </div>
            )}

            {/* Controls */}
            {phase === 'live' && (mode === 'interactive' || takenOver) && (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  send()
                }}
                className="flex items-end gap-2"
              >
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      send()
                    }
                  }}
                  rows={2}
                  placeholder={`Type as ${persona.is_business ? 'the business contact' : 'the customer'}…`}
                  className="flex-1 px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-white text-xs focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
                <button
                  type="submit"
                  disabled={busy || !input.trim()}
                  className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-medium"
                >
                  {busy ? '…' : 'Send'}
                </button>
              </form>
            )}

            {phase === 'live' && mode === 'auto' && !takenOver && (
              <div className="flex flex-col gap-2">
                <div className="flex gap-2">
                  <button
                    onClick={advance}
                    disabled={busy}
                    className="flex-1 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 text-xs font-medium border border-slate-700"
                  >
                    {busy ? '…' : '▶ Play next exchange'}
                  </button>
                  <button
                    onClick={playToResolution}
                    disabled={busy}
                    className="flex-1 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-medium"
                  >
                    {busy ? '…' : '▶▶ Play to resolution'}
                  </button>
                </div>
                <button
                  onClick={() => setTakenOver(true)}
                  disabled={busy}
                  className="text-[11px] text-slate-400 hover:text-slate-200 underline self-start"
                >
                  🎤 Take over as the customer
                </button>
              </div>
            )}

            {phase === 'ended' && (
              <button
                onClick={reset}
                className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700"
              >
                ↺ Simulate again
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
