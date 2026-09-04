// Single data-access layer for the dashboard.
//
// Phase A/B: reads the frozen sample payloads in fixtures.json.
// Phase C: set VITE_DATA_SOURCE=live in the environment and every call is
// served by the real FastAPI client (src/api/client.ts) instead — no component
// change required. This is the one switch the team-lead flips.

import fixturesJson from './fixtures.json'
import { api } from './client'
import type {
  EventAuditResponse,
  EventSimilarResponse,
  EventsResponse,
  MetricsBlock,
  PipelineRunResponse,
} from './types'

const SOURCE = (import.meta.env.VITE_DATA_SOURCE ?? 'fixtures').toLowerCase()
export const IS_LIVE = SOURCE === 'live' || SOURCE === 'api'

interface FixturesShape {
  events: EventsResponse
  eventAudit: EventAuditResponse
  eventSimilar: EventSimilarResponse
  pipelineRun: PipelineRunResponse
  metrics: MetricsBlock
}

const fx = fixturesJson as unknown as FixturesShape

// Simulate a network hop so loading/skeleton states are exercised in fixture mode.
const settle = <T>(value: T): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), 120))

export const dataSource = {
  isLive: IS_LIVE,

  async getEvents(): Promise<EventsResponse> {
    if (IS_LIVE) return api.listEvents()
    return settle(fx.events)
  },

  async getMetrics(): Promise<MetricsBlock> {
    if (IS_LIVE) return api.getMetrics()
    return settle(fx.metrics)
  },

  async getEventAudit(id: string): Promise<EventAuditResponse> {
    if (IS_LIVE) return api.getAuditTrail(id)
    const canned = fx.eventAudit
    if (canned.event.event_id === id) return settle(canned)
    // Fixture only ships one trail; synthesize an honest empty trail for the rest
    // so non-terminal / un-audited events still render in the drawer.
    const events = fx.events.events
    const event = events.find((e) => e.event_id === id) ?? canned.event
    return settle({ event, trail: [] })
  },

  async getSimilar(id: string): Promise<EventSimilarResponse> {
    if (IS_LIVE) return api.getSimilar(id)
    // fixture ships one sample; other events get an honest empty list
    if (fx.eventSimilar?.event_id === id) return settle(fx.eventSimilar)
    return settle({ event_id: id, similar: [] })
  },

  async getVoiceScript(id: string) {
    if (IS_LIVE) return api.getVoiceScript(id)
    return settle({
      event_id: id,
      script: {
        script_summary: 'Hinglish conversational recovery call dialogue',
        dialogue_turns: [
          { speaker: 'Agent', text: 'Namaste ji! Razorpay Support se bol rahe hain aapke pending payment ke regarding.', emotion: 'polite' },
          { speaker: 'Customer', text: 'Haan ji, main check karta hoon.', emotion: 'receptive' },
          { speaker: 'Agent', text: 'Aapko instant WhatsApp link send kar diya gaya hai. Thank you!', emotion: 'helpful' },
        ],
        estimated_duration_sec: 35,
        whatsapp_followup_hinglish: `Namaste ji! Aapka transaction complete karne ke liye direct payment link: https://rzp.io/pay/${id}`,
      },
    })
  },

  async getSequencerSchedule(id: string) {
    if (IS_LIVE) return api.getSequencerSchedule(id)
    return settle({
      event_id: id,
      rail: 'upi_autopay',
      schedule: [
        {
          step_number: 1,
          rail: 'upi_autopay',
          scheduled_time: new Date().toISOString(),
          action: 'salary_window_debit',
          channel: 'whatsapp_and_sms',
          pre_debit_notification: true,
          expected_recovery_prob: 0.75,
          rationale: 'Scheduled debit aligned with expected salary credit window',
          compliance_tag: 'NPCI_CIRCULAR_2024_PTP',
        },
      ],
    })
  },

  async recordPTP(id: string, promisedDate: string, notes?: string) {
    if (IS_LIVE) return api.recordPTP(id, promisedDate, notes)
    return settle({
      status: 'ok',
      event: {
        ...(fx.events.events.find((e) => e.event_id === id) ?? fx.eventAudit.event),
        promised_date: promisedDate,
        ptp_status: 'promised' as const,
      },
    })
  },

  async runPipeline(): Promise<PipelineRunResponse> {
    if (IS_LIVE) return api.runPipeline()
    return settle(fx.pipelineRun)
  },
}
