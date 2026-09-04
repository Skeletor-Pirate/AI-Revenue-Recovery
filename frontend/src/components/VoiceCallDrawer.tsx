import React, { useState, useEffect, useRef } from 'react'
import { dataSource } from '../api/dataSource'
import { useAsync } from '../hooks/useAsync'
import { RaiseQuestionModal } from './TicketActionModals'
import { getEmployeeEmail } from '../lib/session'
import type { VoiceScript, VoiceAudioClip } from '../api/types'

interface Props {
  eventId: string
  isOpen: boolean
  onClose: () => void
}

export const VoiceCallDrawer: React.FC<Props> = ({ eventId, isOpen, onClose }) => {
  const { data, loading } = useAsync(
    () => (isOpen && eventId ? dataSource.getVoiceScript(eventId) : Promise.resolve({ event_id: '', script: null as unknown as VoiceScript })),
    [isOpen, eventId],
  )
  const scriptData: VoiceScript | null = data?.script ?? null

  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTurn, setCurrentTurn] = useState<number | null>(null)
  const [copied, setCopied] = useState(false)
  const [voiceMode, setVoiceMode] = useState<'sarvam' | 'browser' | null>(null)
  const [questionOpen, setQuestionOpen] = useState(false)
  const [raisedTicket, setRaisedTicket] = useState<string | null>(null)

  const audioElRef = useRef<HTMLAudioElement | null>(null)
  const clipsRef = useRef<VoiceAudioClip[] | null>(null)
  const stoppedRef = useRef(false)

  const stopPlayback = () => {
    stoppedRef.current = true
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    if (audioElRef.current) {
      audioElRef.current.pause()
      audioElRef.current.src = ''
      audioElRef.current = null
    }
    setIsPlaying(false)
    setCurrentTurn(null)
  }

  useEffect(() => {
    return () => {
      stoppedRef.current = true
      if ('speechSynthesis' in window) window.speechSynthesis.cancel()
      if (audioElRef.current) {
        audioElRef.current.pause()
        audioElRef.current = null
      }
    }
  }, [])

  const handleClose = () => {
    stopPlayback()
    clipsRef.current = null
    setVoiceMode(null)
    onClose()
  }

  const playSarvamClips = (clips: VoiceAudioClip[]) => {
    setVoiceMode('sarvam')
    let i = 0
    const next = () => {
      if (stoppedRef.current || i >= clips.length) {
        setIsPlaying(false)
        setCurrentTurn(null)
        return
      }
      const clip = clips[i]
      setCurrentTurn(clip.index)
      const el = new Audio(`data:audio/wav;base64,${clip.audio_base64}`)
      audioElRef.current = el
      el.onended = () => {
        i++
        next()
      }
      el.onerror = () => stopPlayback()
      el.play().catch(() => stopPlayback())
    }
    next()
  }

  const playBrowserVoice = () => {
    if (!('speechSynthesis' in window) || !scriptData) {
      setIsPlaying(false)
      return
    }
    setVoiceMode('browser')
    const turns = scriptData.dialogue_turns
    let index = 0
    const speakNext = () => {
      if (stoppedRef.current || index >= turns.length) {
        setIsPlaying(false)
        setCurrentTurn(null)
        return
      }
      setCurrentTurn(index)
      const turn = turns[index]
      const utterance = new SpeechSynthesisUtterance(turn.text)
      utterance.rate = 1.0
      utterance.pitch = turn.speaker === 'Agent' ? 1.05 : 0.95
      utterance.onend = () => {
        index++
        speakNext()
      }
      utterance.onerror = () => stopPlayback()
      window.speechSynthesis.speak(utterance)
    }
    speakNext()
  }

  const handlePlayAudio = async () => {
    if (!scriptData) return
    if (isPlaying) {
      stopPlayback()
      return
    }

    stoppedRef.current = false
    setIsPlaying(true)

    try {
      if (!clipsRef.current) {
        const res = await dataSource.getVoiceAudio(eventId)
        clipsRef.current = res.available ? res.audio : []
      }
    } catch {
      clipsRef.current = []
    }

    if (stoppedRef.current) return
    if (clipsRef.current && clipsRef.current.length > 0) {
      playSarvamClips(clipsRef.current)
    } else {
      playBrowserVoice()
    }
  }

  const handleCopyWhatsApp = () => {
    if (!scriptData) return
    navigator.clipboard.writeText(scriptData.whatsapp_followup_hinglish)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="w-full max-w-xl bg-slate-900 border-l border-slate-800 p-6 flex flex-col h-full shadow-2xl overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <span className="p-2 rounded-lg bg-blue-500/10 text-blue-400">
              🎙️
            </span>
            <div>
              <h2 className="text-lg font-semibold text-white">
                Hinglish Voice Recovery Agent
              </h2>
              <p className="text-xs text-slate-400">
                Conversational Recovery Dialogue · {eventId}
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="text-slate-400 hover:text-white p-1 rounded-md hover:bg-slate-800"
          >
            ✕
          </button>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
          </div>
        ) : scriptData ? (
          <div className="flex-1 py-4 flex flex-col gap-6">
            {/* Action Bar */}
            <div className="flex items-center justify-between p-4 rounded-xl bg-slate-800/60 border border-slate-700/50">
              <div>
                <p className="text-xs font-medium text-slate-300">
                  Est. Call Duration: {scriptData.estimated_duration_sec}s
                </p>
                <p className="text-[11px] text-slate-400">
                  Natural Code-Switched Hindi/English Call
                </p>
                {voiceMode && (
                  <p className="text-[10px] mt-1 text-slate-500">
                    {voiceMode === 'sarvam'
                      ? '🔊 Sarvam AI (bulbul) neural voice'
                      : '🔉 Browser fallback voice — set SARVAM_API_KEY for neural TTS'}
                  </p>
                )}
              </div>
              <button
                onClick={handlePlayAudio}
                className={`px-4 py-2 rounded-lg font-medium text-xs flex items-center gap-2 transition-all ${
                  isPlaying
                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse'
                    : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/20'
                }`}
              >
                {isPlaying ? '⏹ Stop Audio Call' : '▶ Play Simulated Call'}
              </button>
            </div>

            {/* Transcript Dialogue */}
            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Call Transcript
              </h3>
              <div className="space-y-3">
                {scriptData.dialogue_turns.map((turn, i) => {
                  const isAgent = turn.speaker === 'Agent'
                  const isActive = currentTurn === i
                  return (
                    <div
                      key={i}
                      className={`p-3.5 rounded-xl text-sm transition-all ${
                        isAgent
                          ? 'bg-blue-950/40 border border-blue-800/40 text-blue-100 ml-4'
                          : 'bg-slate-800/80 border border-slate-700/60 text-slate-200 mr-4'
                      } ${isActive ? 'ring-2 ring-blue-400 scale-[1.01]' : ''}`}
                    >
                      <div className="flex items-center justify-between text-[11px] font-semibold mb-1">
                        <span className={isAgent ? 'text-blue-400' : 'text-slate-400'}>
                          {turn.speaker}
                        </span>
                        {turn.emotion && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-900/60 text-slate-400 capitalize">
                            {turn.emotion}
                          </span>
                        )}
                      </div>
                      <p className="leading-relaxed">{turn.text}</p>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* WhatsApp Follow-up preview */}
            <div className="p-4 rounded-xl bg-emerald-950/30 border border-emerald-800/40">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-emerald-400 flex items-center gap-1.5">
                  💬 Automated WhatsApp Nudge
                </span>
                <button
                  onClick={handleCopyWhatsApp}
                  className="text-[11px] px-2 py-1 rounded bg-emerald-900/50 hover:bg-emerald-800 text-emerald-200"
                >
                  {copied ? '✓ Copied' : 'Copy Text'}
                </button>
              </div>
              <p className="text-xs text-emerald-100/90 leading-relaxed font-mono">
                {scriptData.whatsapp_followup_hinglish}
              </p>
            </div>

            {/* The AI stops where it should: a question it cannot answer goes
                to a person rather than getting improvised on the call. */}
            <div className="p-4 rounded-xl bg-slate-800/60 border border-slate-700/50">
              {raisedTicket ? (
                <p className="text-xs text-slate-300">
                  Handed to a human as{' '}
                  <span className="font-mono text-white">{raisedTicket}</span>. It
                  is now near the top of the review queue.
                </p>
              ) : (
                <>
                  <p className="text-[11px] text-slate-400 mb-2">
                    If the customer asks something outside this script, don&apos;t
                    improvise — hand it to a person.
                  </p>
                  <button
                    type="button"
                    onClick={() => setQuestionOpen(true)}
                    className="text-xs px-3 py-1.5 rounded-lg bg-slate-900/60 hover:bg-slate-900 text-slate-200 border border-slate-700"
                  >
                    Customer asked something we can&apos;t answer
                  </button>
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="text-center py-12 text-slate-400 text-sm">
            Voice script could not be loaded.
          </div>
        )}
      </div>

      <RaiseQuestionModal
        eventId={eventId}
        employeeEmail={getEmployeeEmail()}
        channel="voice_call"
        isOpen={questionOpen}
        onClose={() => setQuestionOpen(false)}
        onSuccess={setRaisedTicket}
      />
    </div>
  )
}
