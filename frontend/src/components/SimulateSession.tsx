import React, { useEffect, useRef, useState } from 'react'
import { dataSource } from '../api/dataSource'
import { formatINR } from '../lib/format'
import { labelRootCause } from '../api/actionLabels'
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
  ptp_promised: '🤝 Promise to Pay Recorded · Awaiting Payment Settlement',
  resolved: '✅ Verified Resolved · Payment Captured (Revenue Recovered)',
  escalated: '⚠️ Escalated to Human Review (/attention)',
  halted: '🛑 Halted · Security / Risk Check',
}

const OUTCOME_COLOR: Record<PlaygroundOutcome, string> = {
  ongoing: 'text-slate-300 bg-slate-800/60 border-slate-700',
  ptp_promised: 'text-amber-300 bg-amber-950/60 border-amber-500/60 shadow-lg shadow-amber-950/40',
  resolved: 'text-emerald-300 bg-emerald-950/60 border-emerald-500/60 shadow-lg shadow-emerald-950/40',
  escalated: 'text-rose-300 bg-rose-950/60 border-rose-700/60 shadow-lg shadow-rose-950/40',
  halted: 'text-red-300 bg-red-950/60 border-red-700/60 shadow-lg shadow-red-950/40',
}

type TurnWithAudio = PlaygroundTurn & { audio_base64?: string }

interface SpeechRecognitionResultItem {
  transcript: string
}

interface SpeechRecognitionResult {
  [index: number]: SpeechRecognitionResultItem
  length: number
}

interface SpeechRecognitionResults {
  [index: number]: SpeechRecognitionResult
  length: number
}

interface SpeechRecognitionEvent {
  resultIndex: number
  results: SpeechRecognitionResults
}

interface SpeechRecognitionInstance {
  continuous: boolean
  interimResults: boolean
  lang: string
  onstart: () => void
  onresult: (event: SpeechRecognitionEvent) => void
  onerror: (event: { error: string }) => void
  onend: () => void
  start: () => void
  stop: () => void
  abort: () => void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance

const getSpeechRecognition = (): SpeechRecognitionConstructor | null => {
  if (typeof window === 'undefined') return null
  const win = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor
    webkitSpeechRecognition?: SpeechRecognitionConstructor
  }
  return win.SpeechRecognition || win.webkitSpeechRecognition || null
}

export const SimulateSession: React.FC<Props> = ({ eventId, isOpen, onClose }) => {
  const [phase, setPhase] = useState<Phase>('setup')
  const [mode, setMode] = useState<PlaygroundMode>('interactive')
  const [selectedChannel, setSelectedChannel] = useState<PlaygroundChannel>('call')
  const [channel, setChannel] = useState<PlaygroundChannel>('call')
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
  const [callDuration, setCallDuration] = useState(0)
  const [isMuted, setIsMuted] = useState(false)
  const [activeTab, setActiveTab] = useState<'interface' | 'transcript'>('interface')
  const [isListening, setIsListening] = useState(false)
  const [speechSupported, setSpeechSupported] = useState(false)
  const [isInterrupted, setIsInterrupted] = useState(false)

  const audioRef = useRef<HTMLAudioElement | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)
  const cancelSpeechRef = useRef<(() => void) | null>(null)
  const autoPlayAbortRef = useRef(false)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)

  useEffect(() => {
    setSpeechSupported(!!getSpeechRecognition())
  }, [])

  // Call timer
  useEffect(() => {
    if (phase !== 'live' || channel !== 'call') {
      setCallDuration(0)
      return
    }
    const timer = setInterval(() => setCallDuration((d) => d + 1), 1000)
    return () => clearInterval(timer)
  }, [phase, channel])

  const formatTimer = (secs: number) => {
    const m = Math.floor(secs / 60)
    const s = secs % 60
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }

  // Instant Barge-in: Cut off agent voice instantly when human speaks, types, or interrupts
  const interruptSpeech = () => {
    autoPlayAbortRef.current = true
    if (cancelSpeechRef.current) {
      cancelSpeechRef.current()
    } else {
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current.currentTime = 0
        audioRef.current = null
      }
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel()
      }
      setSpeakingIndex(null)
    }
    setIsInterrupted(true)
    setTimeout(() => setIsInterrupted(false), 3500)
  }

  const startListening = () => {
    // 1. Instant Barge-in: cut off any speaking AI right away
    interruptSpeech()

    const SpeechRec = getSpeechRecognition()
    if (!SpeechRec) {
      setError('Speech recognition is not available in this browser. Please type your reply.')
      return
    }

    try {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort()
        } catch {
          // ignore
        }
      }

      const rec = new SpeechRec()
      rec.continuous = false
      rec.interimResults = true
      rec.lang = 'hi-IN'

      rec.onstart = () => {
        setIsListening(true)
        setError(null)
      }

      rec.onresult = (e: SpeechRecognitionEvent) => {
        let transcript = ''
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const item = e.results[i]?.[0]
          if (item?.transcript) {
            transcript += item.transcript
          }
        }
        if (transcript) {
          setInput(transcript)
        }
      }

      rec.onerror = (e: { error: string }) => {
        setIsListening(false)
        if (e.error === 'not-allowed') {
          setError('Microphone access was denied. Please allow microphone permissions or type your message.')
        } else if (e.error !== 'no-speech') {
          console.warn('Speech recognition warning:', e.error)
        }
      }

      rec.onend = () => {
        setIsListening(false)
      }

      recognitionRef.current = rec
      rec.start()
    } catch (err) {
      console.warn('Speech recognition start failed:', err)
      setIsListening(false)
    }
  }

  const stopListening = () => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop()
      } catch {
        // ignore
      }
    }
    setIsListening(false)
  }

  const reset = () => {
    setPhase('setup')
    setHistory([])
    setOutcome('ongoing')
    setReasoning('')
    setTakenOver(false)
    setInput('')
    setError(null)
    setCallDuration(0)
    setActiveTab('interface')
    setIsListening(false)
    setIsInterrupted(false)
    autoPlayAbortRef.current = true
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort()
      } catch {
        // ignore
      }
      recognitionRef.current = null
    }
    if (cancelSpeechRef.current) {
      cancelSpeechRef.current()
    }
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel()
    }
  }

  useEffect(() => {
    if (!isOpen) reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, eventId])

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  const playTurnVoice = (turn: TurnWithAudio, index: number, onEnd?: () => void) => {
    if (isMuted) {
      setSpeakingIndex(null)
      onEnd?.()
      return
    }

    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      cancelSpeechRef.current = null
      setSpeakingIndex(null)
      onEnd?.()
    }

    cancelSpeechRef.current = () => {
      if (finished) return
      finished = true
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current.currentTime = 0
        audioRef.current = null
      }
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel()
      }
      setSpeakingIndex(null)
      onEnd?.()
    }

    // 1. Neural Sarvam AI Audio
    if (turn.audio_base64) {
      if (audioRef.current) {
        audioRef.current.pause()
      }
      const el = new Audio(`data:audio/wav;base64,${turn.audio_base64}`)
      audioRef.current = el
      setSpeakingIndex(index)
      el.onended = finish
      el.onerror = finish
      el.play().catch(finish)
      return
    }

    // 2. Browser SpeechSynthesis Fallback for Call Channel
    if ('speechSynthesis' in window && (channel === 'call' || selectedChannel === 'call')) {
      window.speechSynthesis.cancel()
      const utterance = new SpeechSynthesisUtterance(turn.text)
      utterance.rate = 1.0
      // Customer voice is lower pitch (0.85); Agent voice is higher pitch (1.05)
      utterance.pitch = turn.speaker === 'agent' ? 1.05 : 0.85
      setSpeakingIndex(index)
      utterance.onend = finish
      utterance.onerror = finish
      window.speechSynthesis.speak(utterance)
      return
    }

    finish()
  }

  const playTurnVoiceAsync = (turn: TurnWithAudio, index: number): Promise<void> => {
    return new Promise((resolve) => {
      playTurnVoice(turn, index, resolve)
    })
  }

  const begin = async () => {
    setPhase('connecting')
    setError(null)
    try {
      const res = await dataSource.startPlayground(eventId, mode, selectedChannel)
      setChannel(res.channel)
      setTicketRef(res.ticket_ref)
      setPersona(res.persona)
      setHistory(res.history)
      setOutcome(res.outcome)
      setPhase('live')
      playTurnVoice(res.opening_turn as TurnWithAudio, 0)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the simulation')
      setPhase('setup')
    }
  }

  const applyOutcome = (next: PlaygroundOutcome, why: string) => {
    setOutcome(next)
    setReasoning(why)
    if (next !== 'ongoing' && next !== 'ptp_promised') {
      setPhase('ended')
    }
  }

  const handleCompletePayment = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await dataSource.simulatePlaygroundPayment(eventId, history, channel)
      setHistory(res.history)
      playTurnVoice(res.turn as TurnWithAudio, res.history.length - 1)
      setOutcome('resolved')
      setReasoning(res.reasoning)
      setPhase('ended')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not complete simulated payment')
    } finally {
      setBusy(false)
    }
  }

  const send = async (overrideMessage?: string) => {
    const message = (overrideMessage ?? input).trim()
    if (!message || busy) return
    interruptSpeech()
    if (isListening) stopListening()
    setBusy(true)
    setError(null)
    try {
      const res = await dataSource.sendPlaygroundMessage(eventId, history, message, channel)
      setHistory(res.history)
      setInput('')
      playTurnVoice(res.turn as TurnWithAudio, res.history.length - 1)
      applyOutcome(res.outcome, res.reasoning)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The agent could not reply')
    } finally {
      setBusy(false)
    }
  }

  const advance = async () => {
    if (busy) return
    autoPlayAbortRef.current = false
    setBusy(true)
    setError(null)
    try {
      const res = await dataSource.advancePlayground(eventId, history, channel)
      setHistory(res.history)

      const customerIdx = res.history.length - 2
      const agentIdx = res.history.length - 1

      // Step 1: Customer speaks out loud first
      playTurnVoice(res.customer_turn as TurnWithAudio, customerIdx, () => {
        if (autoPlayAbortRef.current) return
        // Step 2: Once customer finishes speaking, Agent speaks
        setTimeout(() => {
          if (autoPlayAbortRef.current) return
          playTurnVoice(res.agent_turn as TurnWithAudio, agentIdx, () => {
            if (autoPlayAbortRef.current) return
            applyOutcome(res.outcome, res.reasoning)
          })
        }, 400)
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The conversation could not advance')
    } finally {
      setBusy(false)
    }
  }

  const playToResolution = async () => {
    let current = history
    autoPlayAbortRef.current = false
    setBusy(true)
    setError(null)
    try {
      for (let i = 0; i < 6; i++) {
        if (autoPlayAbortRef.current) break
        const res = await dataSource.advancePlayground(eventId, current, channel)
        if (autoPlayAbortRef.current) break
        current = res.history
        setHistory(res.history)

        const customerIdx = res.history.length - 2
        const agentIdx = res.history.length - 1

        // Play Customer voice, wait for it to finish
        await playTurnVoiceAsync(res.customer_turn as TurnWithAudio, customerIdx)
        if (autoPlayAbortRef.current) break
        await new Promise((r) => setTimeout(r, 400))
        if (autoPlayAbortRef.current) break

        // Play Agent voice, wait for it to finish
        await playTurnVoiceAsync(res.agent_turn as TurnWithAudio, agentIdx)
        if (autoPlayAbortRef.current) break
        applyOutcome(res.outcome, res.reasoning)

        if (res.outcome !== 'ongoing') break
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The conversation could not advance')
    } finally {
      setBusy(false)
    }
  }

  if (!isOpen) return null

  const isCall = channel === 'call'

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-xl bg-slate-900 border-l border-slate-800 flex flex-col h-full shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-900/90 backdrop-blur">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-xl text-lg ${isCall ? 'bg-indigo-500/10 text-indigo-400' : 'bg-emerald-500/10 text-emerald-400'}`}>
              {isCall ? '📞' : '💬'}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-white">
                  {isCall ? 'Live Voice Call Rehearsal' : 'WhatsApp Recovery Chat'}
                </h2>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-300 border border-purple-500/20">
                  SANDBOX
                </span>
              </div>
              <p className="text-xs text-slate-400">
                {ticketRef ? `${ticketRef} · ` : ''}Case {eventId}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-2 rounded-lg hover:bg-slate-800 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Rehearsal Sandbox Notice */}
        <div className="px-6 py-2 bg-purple-950/30 border-b border-purple-800/30 flex items-center justify-between text-[11px] text-purple-300">
          <span>🧪 <strong>Rehearsal Mode:</strong> Test responses live without affecting real batch metrics.</span>
          {phase === 'live' && isCall && (
            <span className="font-mono text-xs text-emerald-400 font-semibold flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              {formatTimer(callDuration)}
            </span>
          )}
        </div>

        {error && (
          <div className="mx-6 mt-3 px-3.5 py-2 rounded-xl bg-red-950/40 border border-red-800/40 text-xs text-red-300 flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-200">✕</button>
          </div>
        )}

        {/* Phase 1: Setup Screen */}
        {phase === 'setup' && (
          <div className="flex-1 p-6 flex flex-col gap-6 overflow-y-auto">
            {/* Communication Channel Selection */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2.5">
                1. Choose Communication Channel
              </label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setSelectedChannel('call')}
                  className={`p-4 rounded-xl border text-left transition-all ${
                    selectedChannel === 'call'
                      ? 'bg-indigo-950/40 border-indigo-600 shadow-lg shadow-indigo-950/50'
                      : 'bg-slate-800/60 border-slate-700 hover:border-slate-600'
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-2xl">📞</span>
                    {selectedChannel === 'call' && (
                      <span className="w-2.5 h-2.5 rounded-full bg-indigo-400 shadow-sm shadow-indigo-400" />
                    )}
                  </div>
                  <div className="font-semibold text-sm text-white mb-1">Live Phone Call</div>
                  <div className="text-xs text-slate-400 leading-relaxed">
                    Spoken Hinglish call with AI voice audio, live phone screen, and speech playback.
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setSelectedChannel('message')}
                  className={`p-4 rounded-xl border text-left transition-all ${
                    selectedChannel === 'message'
                      ? 'bg-emerald-950/40 border-emerald-600 shadow-lg shadow-emerald-950/50'
                      : 'bg-slate-800/60 border-slate-700 hover:border-slate-600'
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-2xl">💬</span>
                    {selectedChannel === 'message' && (
                      <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400" />
                    )}
                  </div>
                  <div className="font-semibold text-sm text-white mb-1">WhatsApp Chat</div>
                  <div className="text-xs text-slate-400 leading-relaxed">
                    Official WhatsApp business chat with verified badge, double checkmarks, and payment link card.
                  </div>
                </button>
              </div>
            </div>

            {/* Participation Mode Selection */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2.5">
                2. Who Plays the Customer?
              </label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setMode('interactive')}
                  className={`p-4 rounded-xl border text-left transition-all ${
                    mode === 'interactive'
                      ? 'bg-blue-950/40 border-blue-600 shadow-lg shadow-blue-950/50'
                      : 'bg-slate-800/60 border-slate-700 hover:border-slate-600'
                  }`}
                >
                  <div className="font-semibold text-sm text-white mb-1">🎤 I&apos;ll play customer</div>
                  <div className="text-xs text-slate-400 leading-relaxed">
                    Test the agent yourself: ask questions, push back, or agree to pay.
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setMode('auto')}
                  className={`p-4 rounded-xl border text-left transition-all ${
                    mode === 'auto'
                      ? 'bg-blue-950/40 border-blue-600 shadow-lg shadow-blue-950/50'
                      : 'bg-slate-800/60 border-slate-700 hover:border-slate-600'
                  }`}
                >
                  <div className="font-semibold text-sm text-white mb-1">▶ Watch two AIs talk</div>
                  <div className="text-xs text-slate-400 leading-relaxed">
                    Autonomous rehearsal: a second AI persona acts as the customer/business.
                  </div>
                </button>
              </div>
            </div>

            <div className="mt-auto pt-6">
              <button
                onClick={begin}
                className="w-full py-3.5 px-5 rounded-xl font-medium text-sm text-white bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 shadow-lg shadow-blue-600/25 transition-all flex items-center justify-center gap-2"
              >
                <span>Start {selectedChannel === 'call' ? 'Phone Call' : 'WhatsApp Chat'} Rehearsal</span>
                <span>→</span>
              </button>
            </div>
          </div>
        )}

        {/* Phase 2: Connecting Spinner */}
        {phase === 'connecting' && (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 py-16">
            <div className="relative">
              <div className="w-16 h-16 rounded-full border-4 border-slate-700 border-t-indigo-500 animate-spin" />
              <div className="absolute inset-0 flex items-center justify-center text-xl">
                {selectedChannel === 'call' ? '📞' : '💬'}
              </div>
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-white">
                {selectedChannel === 'call' ? 'Connecting live call...' : 'Starting WhatsApp conversation...'}
              </p>
              <p className="text-xs text-slate-400 mt-1">Generating persona & opening dialogue</p>
            </div>
          </div>
        )}

        {/* Phase 3 & 4: Active Session (Live or Ended) */}
        {(phase === 'live' || phase === 'ended') && persona && (
          <div className="flex-1 flex flex-col overflow-hidden bg-slate-950/50">
            {/* View Toggle Bar (Interface vs Raw Transcript) */}
            <div className="px-6 py-2 bg-slate-900/60 border-b border-slate-800 flex items-center justify-between text-xs">
              <div className="flex gap-2">
                <button
                  onClick={() => setActiveTab('interface')}
                  className={`px-3 py-1 rounded-lg transition-colors font-medium ${
                    activeTab === 'interface'
                      ? 'bg-slate-800 text-white border border-slate-700'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {isCall ? '📱 Phone Call Screen' : '💬 WhatsApp View'}
                </button>
                <button
                  onClick={() => setActiveTab('transcript')}
                  className={`px-3 py-1 rounded-lg transition-colors font-medium ${
                    activeTab === 'transcript'
                      ? 'bg-slate-800 text-white border border-slate-700'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  📜 Full Transcript ({history.length})
                </button>
              </div>

              {isCall && (
                <button
                  onClick={() => setIsMuted((m) => !m)}
                  className={`text-xs px-2.5 py-1 rounded-md border flex items-center gap-1.5 transition-colors ${
                    isMuted
                      ? 'bg-red-950/40 border-red-700 text-red-300'
                      : 'bg-slate-800/80 border-slate-700 text-slate-300 hover:text-white'
                  }`}
                >
                  <span>{isMuted ? '🔇 Muted' : '🔊 Speaker On'}</span>
                </button>
              )}
            </div>

            {/* Persona Summary Mini-Card */}
            <div className="px-6 py-2.5 bg-slate-900/40 border-b border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400">
              <div className="flex items-center gap-3">
                <span className="text-slate-200 font-medium">{persona.name}</span>
                <span>·</span>
                <span>{persona.phone_masked || persona.upi_vpa}</span>
                <span>·</span>
                <span className="text-amber-300 font-medium">{formatINR(persona.amount)}</span>
              </div>
              <span className="text-slate-400">{labelRootCause(persona.root_cause)}</span>
            </div>

            {/* MAIN CONTENT AREA */}
            <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
              {/* TAB 1: INTERFACE VIEW */}
              {activeTab === 'interface' && (
                <>
                  {/* --- MODE A: LIVE PHONE CALL SCREEN --- */}
                  {isCall && (
                    <div className="flex-1 flex flex-col gap-4 max-w-md mx-auto w-full">
                      {/* Active Call Card */}
                      <div className="p-6 rounded-2xl bg-gradient-to-b from-slate-800/90 to-slate-900/90 border border-slate-700/70 shadow-2xl flex flex-col items-center text-center relative overflow-hidden">
                        {/* Audio Wave Glow when speaking */}
                        {speakingIndex !== null && (
                          <div className="absolute inset-0 bg-indigo-500/10 animate-pulse pointer-events-none" />
                        )}

                        {(() => {
                          const activeSpeaker = speakingIndex !== null && history[speakingIndex] ? history[speakingIndex].speaker : null
                          return (
                            <>
                              <div className="relative mb-3">
                                <div className={`w-20 h-20 rounded-full flex items-center justify-center text-3xl border-2 transition-all duration-300 ${
                                  speakingIndex !== null
                                    ? activeSpeaker === 'customer'
                                      ? 'border-amber-400 shadow-xl shadow-amber-500/40 scale-105 bg-amber-950/80'
                                      : 'border-indigo-400 shadow-xl shadow-indigo-500/40 scale-105 bg-indigo-950/80'
                                    : 'border-slate-700 bg-slate-800'
                                }`}>
                                  {activeSpeaker === 'customer' ? '👤' : '🎧'}
                                </div>
                                {speakingIndex !== null && (
                                  <span className={`absolute -bottom-1 -right-1 p-1 rounded-full text-white text-[10px] animate-bounce ${
                                    activeSpeaker === 'customer' ? 'bg-amber-500' : 'bg-indigo-500'
                                  }`}>
                                    🔊
                                  </span>
                                )}
                              </div>

                              <h3 className="text-base font-semibold text-white">
                                {activeSpeaker === 'agent' ? 'Razorpay Recovery Agent' : persona.name}
                              </h3>
                              <p className="text-xs text-slate-400 mt-0.5">
                                {activeSpeaker === 'agent'
                                  ? 'AI Voice Assistant (Priya)'
                                  : `${persona.phone_masked || '+91 ••••••••••'} · Customer`}
                              </p>

                              <div className="mt-3 flex flex-col items-center gap-2">
                                <span className={`px-2.5 py-1 rounded-full text-xs font-medium flex items-center gap-1.5 transition-colors ${
                                  phase === 'ended'
                                    ? 'bg-slate-800 text-slate-400'
                                    : activeSpeaker === 'customer'
                                    ? 'bg-amber-950 border border-amber-700/60 text-amber-300 shadow-sm shadow-amber-950/50'
                                    : activeSpeaker === 'agent'
                                    ? 'bg-indigo-950 border border-indigo-700/60 text-indigo-300 shadow-sm shadow-indigo-950/50'
                                    : 'bg-emerald-950 border border-emerald-700/60 text-emerald-300'
                                }`}>
                                  <span className={`w-2 h-2 rounded-full ${
                                    phase === 'ended'
                                      ? 'bg-slate-500'
                                      : activeSpeaker === 'customer'
                                      ? 'bg-amber-400 animate-pulse'
                                      : activeSpeaker === 'agent'
                                      ? 'bg-indigo-400 animate-pulse'
                                      : 'bg-emerald-400 animate-pulse'
                                  }`} />
                                  {phase === 'ended'
                                    ? 'Call Ended'
                                    : activeSpeaker === 'customer'
                                    ? `${persona.name} (Customer) Speaking...`
                                    : activeSpeaker === 'agent'
                                    ? 'Resolver (Agent) Speaking...'
                                    : `Connected (${formatTimer(callDuration)})`}
                                </span>

                                {/* ✋ Prominent Barge-In Button when Agent is speaking */}
                                {speakingIndex !== null && activeSpeaker === 'agent' && phase === 'live' && (
                                  <button
                                    type="button"
                                    onClick={interruptSpeech}
                                    className="py-1.5 px-4 rounded-full bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs shadow-lg shadow-rose-600/40 flex items-center gap-2 transition-all hover:scale-105 active:scale-95 cursor-pointer animate-pulse"
                                    title="Interrupt agent speech immediately (Barge-in)"
                                  >
                                    <span>✋ Interrupt Agent</span>
                                    <span className="text-[10px] opacity-90 font-medium">· Speak Now</span>
                                  </button>
                                )}

                                {/* Auto mode Takeover during call */}
                                {mode === 'auto' && !takenOver && phase === 'live' && speakingIndex !== null && (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      interruptSpeech()
                                      setTakenOver(true)
                                    }}
                                    className="py-1 px-3 rounded-full bg-indigo-600/80 hover:bg-indigo-500 text-white text-[11px] font-semibold flex items-center gap-1.5 shadow transition-all hover:scale-105 active:scale-95 cursor-pointer"
                                  >
                                    <span>✋ Interrupt & Take Over Call</span>
                                  </button>
                                )}

                                {/* Interrupted visual confirmation */}
                                {isInterrupted && (
                                  <div className="px-3 py-1 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[11px] font-medium flex items-center gap-1.5 animate-bounce">
                                    <span>⚡ You interrupted Priya</span>
                                    <span className="text-[10px] text-amber-400 font-normal">· Call listening...</span>
                                  </div>
                                )}

                                {/* Listening indicator */}
                                {isListening && (
                                  <div className="px-3 py-1 rounded-full bg-rose-500/20 text-rose-300 border border-rose-500/40 text-[11px] font-medium flex items-center gap-2 animate-pulse">
                                    <span className="w-2 h-2 rounded-full bg-rose-400 animate-ping" />
                                    <span>🎙️ Listening to you... (Speak now)</span>
                                  </div>
                                )}
                              </div>
                            </>
                          )
                        })()}

                        {/* Audio Wave Visualizer Bars */}
                        {phase === 'live' && (
                          <div className="flex items-center gap-1 h-6 mt-4">
                            {[12, 24, 16, 28, 20, 32, 18, 26, 14].map((h, i) => (
                              <span
                                key={i}
                                className={`w-1 rounded-full transition-all duration-200 ${
                                  isListening
                                    ? 'bg-rose-400 animate-pulse'
                                    : speakingIndex !== null
                                    ? 'bg-indigo-400 animate-pulse'
                                    : 'bg-slate-700'
                                }`}
                                style={{
                                  height: isListening || speakingIndex !== null ? `${h}px` : '6px',
                                  animationDelay: `${i * 80}ms`,
                                }}
                              />
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Live Call Microphone Speak Button */}
                      {phase === 'live' && (mode === 'interactive' || takenOver) && (
                        <div className="p-3 rounded-xl bg-slate-900/90 border border-slate-800 flex items-center justify-between gap-3 shadow-md">
                          <button
                            type="button"
                            onClick={isListening ? stopListening : startListening}
                            disabled={busy}
                            className={`flex-1 py-2.5 px-4 rounded-xl font-bold text-xs transition-all flex items-center justify-center gap-2 cursor-pointer ${
                              isListening
                                ? 'bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-600/40 animate-pulse'
                                : 'bg-gradient-to-r from-indigo-600 to-blue-600 hover:from-indigo-500 hover:to-blue-500 text-white shadow-md shadow-indigo-600/25 hover:scale-[1.02] active:scale-95'
                            }`}
                          >
                            <span className="text-base">{isListening ? '🛑' : '🎙️'}</span>
                            <span>
                              {isListening ? 'Stop & Send Spoken Reply' : 'Speak into Microphone (Interrupts Agent)'}
                            </span>
                          </button>
                          {speechSupported && !isListening && (
                            <span className="text-[10px] text-slate-400 font-mono hidden sm:inline-block">
                              VOICE READY
                            </span>
                          )}
                        </div>
                      )}

                      {/* Current Spoken Dialogue */}
                      {history.length > 0 && (() => {
                        const currentTurn = speakingIndex !== null && history[speakingIndex] ? history[speakingIndex] : history[history.length - 1]
                        const isAgent = currentTurn.speaker === 'agent'
                        return (
                          <div className={`p-4 rounded-xl bg-slate-900 border text-slate-200 text-sm shadow-md transition-all ${
                            isAgent ? 'border-indigo-900/40' : 'border-amber-900/40'
                          }`}>
                            <div className="text-[11px] font-semibold uppercase tracking-wider mb-1 flex items-center justify-between">
                              <span className={isAgent ? 'text-indigo-400' : 'text-amber-400'}>
                                {isAgent ? 'Resolver (Agent Spoken on Call)' : `${persona.name} (Customer Spoken)`}
                              </span>
                              {speakingIndex !== null && (
                                <span className="text-emerald-400 text-xs animate-pulse">● Playing Audio</span>
                              )}
                            </div>
                            <p className="leading-relaxed">{currentTurn.text}</p>
                          </div>
                        )
                      })()}

                      {/* Quick Voice Responses (Chips) */}
                      {phase === 'live' && (mode === 'interactive' || takenOver) && (
                        <div>
                          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
                            Quick Responses (Click to speak):
                          </p>
                          <div className="flex flex-wrap gap-1.5">
                            {[
                              'Kyu fail hua tha?',
                              'Haan payment link bhej do',
                              'Main baad mein pay karunga',
                              'Yeh transaction galat hai',
                            ].map((chip) => (
                              <button
                                key={chip}
                                onClick={() => {
                                  interruptSpeech()
                                  send(chip)
                                }}
                                disabled={busy}
                                className="px-2.5 py-1.5 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-xs text-slate-200 border border-slate-700 transition-colors disabled:opacity-50 cursor-pointer"
                              >
                                {chip}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* PTP Action Banner in Call Mode */}
                      {outcome === 'ptp_promised' && (
                        <div className="p-4 rounded-xl bg-amber-950/50 border border-amber-500/50 text-amber-200 text-xs shadow-lg flex flex-col gap-2 animate-fade-in">
                          <div className="flex items-center justify-between">
                            <span className="font-bold flex items-center gap-1.5 text-amber-300">
                              <span>🤝 Promise to Pay (PTP) Recorded</span>
                            </span>
                            <span className="text-[10px] font-mono bg-amber-900/60 px-2 py-0.5 rounded text-amber-200 border border-amber-700/50">
                              SCHEDULED: 1 OF 3 REMINDERS
                            </span>
                          </div>
                          <p className="text-[11px] opacity-90 leading-relaxed text-slate-300">
                            Customer committed to pay. Escalation paused. Max 3 automated reminders with 24h cooldown. If unfulfilled, case auto-escalates to /attention queue.
                          </p>
                          <button
                            onClick={handleCompletePayment}
                            disabled={busy}
                            className="mt-1 py-2.5 px-3 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs shadow-md transition-all hover:scale-[1.02] active:scale-95 flex items-center justify-center gap-2 cursor-pointer"
                          >
                            <span>⚡ Customer Clicks Link & Completes Payment (Simulate Webhook)</span>
                            <span>→</span>
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {/* --- MODE B: WHATSAPP CHAT SCREEN --- */}
                  {!isCall && (
                    <div className="flex-1 flex flex-col max-w-lg mx-auto w-full bg-[#0b141a] rounded-2xl border border-emerald-900/40 shadow-2xl overflow-hidden">
                      {/* WhatsApp Header Bar */}
                      <div className="px-4 py-3 bg-[#202c33] border-b border-[#2a3942] flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-full bg-[#00a884] flex items-center justify-center text-white font-bold text-lg shadow-sm">
                            RZ
                          </div>
                          <div>
                            <div className="flex items-center gap-1.5">
                              <span className="font-semibold text-white text-sm">Razorpay Recovery</span>
                              <span className="text-emerald-400 text-xs">✓</span>
                            </div>
                            <p className="text-[11px] text-emerald-400">Official Business Account</p>
                          </div>
                        </div>
                        <span className="text-[10px] text-slate-400 bg-slate-800 px-2 py-0.5 rounded">WhatsApp</span>
                      </div>

                      {/* Chat Messages Stream */}
                      <div className="flex-1 p-4 overflow-y-auto flex flex-col gap-3 min-h-[300px]">
                        {/* Encryption pill */}
                        <div className="self-center px-3 py-1 rounded-md bg-[#182229] border border-[#222e35] text-[10px] text-[#ffd279] text-center max-w-xs shadow-sm">
                          🔒 Messages are end-to-end encrypted for payment security.
                        </div>

                        {history.map((turn, i) => {
                          const isAgent = turn.speaker === 'agent'
                          return (
                            <div
                              key={i}
                              className={`max-w-[80%] rounded-xl px-3.5 py-2.5 text-xs shadow-md leading-relaxed ${
                                isAgent
                                  ? 'self-start bg-[#202c33] text-[#e9edef] rounded-tl-none border border-[#2a3942]'
                                  : 'self-end bg-[#005c4b] text-[#e9edef] rounded-tr-none'
                              }`}
                            >
                              <div className="text-[10px] font-semibold text-emerald-400 mb-0.5">
                                {isAgent ? 'Razorpay Support' : persona.name}
                              </div>
                              <p className="text-sm">{turn.text}</p>
                              <div className="mt-1 flex items-center justify-end gap-1 text-[10px] text-slate-400">
                                <span>{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                                {!isAgent && <span className="text-sky-400">✓✓</span>}
                              </div>
                            </div>
                          )
                        })}

                        {/* Interactive Payment Link Card when PTP Promised or Resolved */}
                        {(outcome === 'ptp_promised' || outcome === 'resolved') && (
                          <div className={`self-start max-w-[85%] rounded-xl p-3.5 border shadow-xl text-left mt-1 transition-all ${
                            outcome === 'resolved'
                              ? 'bg-gradient-to-br from-[#0c2a20] to-[#081b14] border-emerald-500/70'
                              : 'bg-gradient-to-br from-[#242114] to-[#16140b] border-amber-500/70'
                          }`}>
                            <div className="flex items-center justify-between pb-2 border-b border-slate-700/50">
                              <span className="text-xs font-bold text-white flex items-center gap-1.5">
                                <span className={`w-2.5 h-2.5 rounded-full ${outcome === 'resolved' ? 'bg-emerald-400' : 'bg-amber-400 animate-pulse'}`} />
                                Razorpay Payment Request
                              </span>
                              <span className={`text-[10px] font-mono px-2 py-0.5 rounded ${
                                outcome === 'resolved' ? 'text-emerald-300 bg-emerald-950/60' : 'text-amber-300 bg-amber-950/60'
                              }`}>
                                {outcome === 'resolved' ? 'PAID & CAPTURED' : 'PTP RECORDED'}
                              </span>
                            </div>
                            <div className="py-2.5">
                              <div className="text-xs text-slate-300">Amount Due:</div>
                              <div className="text-lg font-bold text-white tracking-wide">{formatINR(persona.amount)}</div>
                              <p className="text-[11px] text-slate-400 mt-1">
                                {outcome === 'resolved'
                                  ? 'Payment verified via webhook (payment.captured). Case marked RESOLVED.'
                                  : 'Payment link generated. 3 automated reminders scheduled with 24h cooldown.'}
                              </p>
                            </div>
                            <div className="pt-2 border-t border-slate-700/40 flex items-center justify-between gap-2">
                              <span className="text-[11px] text-slate-300">
                                {outcome === 'resolved' ? 'Receipt Generated' : 'Status: Awaiting Settlement'}
                              </span>
                              {outcome === 'ptp_promised' ? (
                                <button
                                  onClick={handleCompletePayment}
                                  disabled={busy}
                                  className="text-xs bg-amber-400 hover:bg-amber-300 text-slate-950 font-bold px-3 py-1.5 rounded-lg shadow cursor-pointer transition-all hover:scale-105 active:scale-95 flex items-center gap-1"
                                >
                                  <span>⚡ Click Link & Complete Payment</span>
                                </button>
                              ) : (
                                <span className="text-xs text-emerald-400 font-bold flex items-center gap-1">
                                  ✓ Paid Successfully
                                </span>
                              )}
                            </div>
                          </div>
                        )}

                        <div ref={transcriptEndRef} />
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* TAB 2: FULL RAW TRANSCRIPT */}
              {activeTab === 'transcript' && (
                <div className="flex flex-col gap-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                    Dialogue Transcript ({history.length} turns)
                  </h3>
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
                          <div className="text-[11px] font-semibold mb-1 capitalize flex items-center justify-between">
                            <span className={isAgent ? 'text-blue-400' : 'text-slate-400'}>
                              {isAgent ? 'Resolver (Agent)' : persona.is_business ? 'Business Contact' : 'Customer'}
                            </span>
                            {isSpeaking && <span className="text-xs text-emerald-400 animate-pulse">● Speaking</span>}
                          </div>
                          <p className="leading-relaxed">{turn.text}</p>
                        </div>
                      )
                    })}
                    <div ref={transcriptEndRef} />
                  </div>
                </div>
              )}

              {/* OUTCOME RESOLUTION BANNER */}
              {outcome !== 'ongoing' && (
                <div className={`p-4 rounded-xl border text-xs animate-fade-in ${OUTCOME_COLOR[outcome]}`}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-base">
                        {outcome === 'resolved' ? '✅' : outcome === 'ptp_promised' ? '🤝' : outcome === 'escalated' ? '⚠️' : '🛑'}
                      </span>
                      <p className="font-bold text-sm">{OUTCOME_LABEL[outcome]}</p>
                    </div>
                    {outcome === 'ptp_promised' && (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-900/60 text-amber-200 border border-amber-600/40">
                        MAX 3 REMINDERS
                      </span>
                    )}
                  </div>
                  {reasoning && <p className="text-xs opacity-90 leading-relaxed pl-6">{reasoning}</p>}
                  {outcome === 'ptp_promised' && (
                    <div className="mt-3 pl-6">
                      <button
                        onClick={handleCompletePayment}
                        disabled={busy}
                        className="py-2 px-3.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs shadow-md transition-all hover:scale-[1.02] active:scale-95 flex items-center gap-2 cursor-pointer"
                      >
                        <span>⚡ Simulate Customer Clicking Link & Completing Payment</span>
                        <span>→</span>
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* CONTROLS / INPUT BAR */}
            <div className="p-4 bg-slate-900 border-t border-slate-800">
              {phase === 'live' && (mode === 'interactive' || takenOver) && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    send()
                  }}
                  className="flex items-end gap-2"
                >
                  {isCall && (
                    <button
                      type="button"
                      onClick={isListening ? stopListening : startListening}
                      className={`p-2.5 rounded-xl border transition-all flex items-center justify-center cursor-pointer ${
                        isListening
                          ? 'bg-rose-600 border-rose-500 text-white animate-pulse shadow-md shadow-rose-600/40'
                          : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-slate-300 hover:text-white'
                      }`}
                      title={isListening ? 'Stop listening' : 'Speak using microphone (interrupts agent)'}
                    >
                      <span className="text-sm">{isListening ? '🛑' : '🎙️'}</span>
                    </button>
                  )}
                  <textarea
                    value={input}
                    onFocus={() => {
                      if (speakingIndex !== null) interruptSpeech()
                    }}
                    onChange={(e) => {
                      if (speakingIndex !== null) interruptSpeech()
                      setInput(e.target.value)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        send()
                      }
                    }}
                    rows={isCall ? 1 : 2}
                    placeholder={
                      isListening
                        ? 'Listening to your voice...'
                        : isCall
                        ? 'Speak or type your reply (interrupts agent)…'
                        : `Message as ${persona.name}…`
                    }
                    className="flex-1 px-3 py-2 rounded-xl bg-slate-800 border border-slate-700 text-white text-xs focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                  />
                  <button
                    type="submit"
                    disabled={busy || !input.trim()}
                    className={`px-4 py-2.5 rounded-xl font-semibold text-xs text-white disabled:opacity-50 transition-all cursor-pointer ${
                      isCall ? 'bg-indigo-600 hover:bg-indigo-500' : 'bg-emerald-600 hover:bg-emerald-500'
                    }`}
                  >
                    {busy ? '…' : isCall ? 'Speak / Send' : 'Send'}
                  </button>
                </form>
              )}

              {phase === 'live' && mode === 'auto' && !takenOver && (
                <div className="flex flex-col gap-2">
                  <div className="flex gap-2">
                    <button
                      onClick={advance}
                      disabled={busy}
                      className="flex-1 px-3 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 text-xs font-medium border border-slate-700 transition-colors"
                    >
                      {busy ? '…' : '▶ Play Next Exchange'}
                    </button>
                    <button
                      onClick={playToResolution}
                      disabled={busy}
                      className="flex-1 px-3 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-semibold shadow-lg shadow-blue-600/20 transition-all"
                    >
                      {busy ? '…' : '▶▶ Auto-Play to Resolution'}
                    </button>
                  </div>
                  <button
                    onClick={() => setTakenOver(true)}
                    disabled={busy}
                    className="text-[11px] text-slate-400 hover:text-slate-200 underline self-center mt-1"
                  >
                    🎤 Take over conversation as customer
                  </button>
                </div>
              )}

              {phase === 'ended' && (
                <button
                  onClick={reset}
                  className="w-full py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold border border-slate-700 transition-colors flex items-center justify-center gap-2"
                >
                  <span>↺ Simulate Another Scenario</span>
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
