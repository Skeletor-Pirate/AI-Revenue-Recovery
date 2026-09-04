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
  TicketDetailResponse,
  TicketRead,
  TicketsResponse,
} from './types'

const SOURCE = (import.meta.env.VITE_DATA_SOURCE ?? 'fixtures').toLowerCase()
export const IS_LIVE = SOURCE === 'live' || SOURCE === 'api'

interface FixturesShape {
  events: EventsResponse
  eventAudit: EventAuditResponse
  eventSimilar: EventSimilarResponse
  pipelineRun: PipelineRunResponse
  metrics: MetricsBlock
  tickets: TicketsResponse
  ticketDetail: TicketDetailResponse
}

const fx = fixturesJson as unknown as FixturesShape

// Simulate a network hop so loading/skeleton states are exercised in fixture mode.
const settle = <T>(value: T): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), 120))

// Fixture-mode tickets are held in memory and DO persist for the session, unlike
// the read-only event fixtures. The review workflow is a sequence -- take a
// ticket, then resolve it -- so a demo where the first step silently reverts
// would misrepresent how the feature behaves against the live API.
const ticketsMem: TicketRead[] = fx.tickets.tickets.map((t) => ({ ...t }))

const ticketsResponse = (): TicketsResponse => ({
  tickets: [...ticketsMem].sort(
    (a, b) => b.priority - a.priority || a.created_at.localeCompare(b.created_at),
  ),
  count: ticketsMem.length,
  open_count: ticketsMem.filter((t) => t.status === 'open').length,
  under_review_count: ticketsMem.filter((t) => t.status === 'under_review').length,
})

const patchTicket = (id: string, patch: Partial<TicketRead>): TicketRead => {
  const i = ticketsMem.findIndex((t) => t.ticket_id === id)
  if (i < 0) throw new Error(`no such ticket: ${id}`)
  ticketsMem[i] = { ...ticketsMem[i], ...patch, updated_at: new Date().toISOString() }
  return ticketsMem[i]
}

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

  async getVoiceAudio(id: string) {
    if (IS_LIVE) return api.getVoiceAudio(id)
    // No TTS provider in fixture mode — dashboard uses the browser voice.
    return settle({
      event_id: id,
      available: false,
      provider: 'sarvam',
      audio_format: 'wav',
      sample_rate: 22050,
      audio: [],
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

  // --- human review queue ---

  async getTickets(status?: string): Promise<TicketsResponse> {
    if (IS_LIVE) return api.listTickets(status)
    const all = ticketsResponse()
    if (!status) return settle(all)
    const tickets = all.tickets.filter((t) => t.status === status)
    return settle({ ...all, tickets, count: tickets.length })
  },

  async getTicket(id: string): Promise<TicketDetailResponse> {
    if (IS_LIVE) return api.getTicket(id)
    const ticket = ticketsMem.find((t) => t.ticket_id === id)
    if (!ticket) return settle(fx.ticketDetail)
    // the fixture ships one full trail; other tickets reuse their event's
    const canned = fx.ticketDetail
    const event =
      fx.events.events.find((e) => e.event_id === ticket.event_id) ?? canned.event
    return settle({
      ticket,
      event,
      trail: canned.ticket.event_id === ticket.event_id ? canned.trail : [],
    })
  },

  async assignTicket(id: string, employeeEmail: string) {
    if (IS_LIVE) return api.assignTicket(id, employeeEmail)
    const current = ticketsMem.find((t) => t.ticket_id === id)
    if (current && current.status !== 'open') {
      throw new Error(`ticket ${id} is ${current.status}, not open`)
    }
    return settle({
      status: 'ok',
      ticket: patchTicket(id, {
        status: 'under_review',
        assigned_employee_email: employeeEmail,
        assigned_at: new Date().toISOString(),
      }),
    })
  },

  async resolveTicket(
    id: string,
    body: {
      employee_email: string
      outcome: 'resolved' | 'unresolved'
      note: string
      recovered_amount?: string | null
    },
  ) {
    if (IS_LIVE) return api.resolveTicket(id, body)
    return settle({
      status: 'ok',
      ticket: patchTicket(id, {
        status: body.outcome,
        resolution_outcome: body.outcome,
        resolution_note: body.note,
        recovered_amount: body.recovered_amount ?? '0.00',
      }),
    })
  },

  async raiseQuestion(
    eventId: string,
    body: { question: string; channel?: string; employee_email?: string | null },
  ) {
    if (IS_LIVE) return api.raiseQuestion(eventId, body)
    const now = new Date().toISOString()
    const ticket: TicketRead = {
      ticket_id: `tkt_${String(ticketsMem.length + 1).padStart(4, '0')}`,
      event_id: eventId,
      reason: 'customer_question',
      priority: 80,
      status: 'open',
      summary: 'Customer asked something the AI could not answer.',
      detail: body.question,
      assigned_employee_email: null,
      assigned_at: null,
      resolution_note: null,
      resolution_outcome: null,
      recovered_amount: '0.00',
      created_at: now,
      updated_at: now,
    }
    ticketsMem.push(ticket)
    return settle({ status: 'ok', ticket })
  },

  async runPipeline(): Promise<PipelineRunResponse> {
    if (IS_LIVE) return api.runPipeline()
    return settle(fx.pipelineRun)
  },
}
