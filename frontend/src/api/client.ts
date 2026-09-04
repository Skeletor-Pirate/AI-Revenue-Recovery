import type {
  EventAuditResponse,
  EventRead,
  EventSequencerResponse,
  EventSimilarResponse,
  EventsResponse,
  EventVoiceResponse,
  EventVoiceAudioResponse,
  MetricsBlock,
  PipelineRunResponse,
  TicketDetailResponse,
  TicketMutationResponse,
  TicketsResponse,
  PlaygroundAdvanceResponse,
  PlaygroundChannel,
  PlaygroundMessageResponse,
  PlaygroundMode,
  PlaygroundStartResponse,
  PlaygroundTurn,
} from './types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>('/health'),
  listEvents: () => request<EventsResponse>('/api/events'),
  getAuditTrail: (id: string) =>
    request<EventAuditResponse>(`/api/events/${encodeURIComponent(id)}/audit`),
  getSimilar: (id: string) =>
    request<EventSimilarResponse>(`/api/events/${encodeURIComponent(id)}/similar`),
  getVoiceScript: (id: string) =>
    request<EventVoiceResponse>(`/api/events/${encodeURIComponent(id)}/voice`),
  getVoiceAudio: (id: string) =>
    request<EventVoiceAudioResponse>(`/api/events/${encodeURIComponent(id)}/voice/audio`),
  getSequencerSchedule: (id: string) =>
    request<EventSequencerResponse>(`/api/events/${encodeURIComponent(id)}/sequencer`),
  recordPTP: (id: string, promisedDate: string, notes?: string) =>
    request<{ status: string; event: EventRead }>(`/api/events/${encodeURIComponent(id)}/ptp`, {
      method: 'POST',
      body: JSON.stringify({ promised_date: promisedDate, notes }),
    }),
  // --- human review queue ---
  listTickets: (status?: string) =>
    request<TicketsResponse>(
      `/api/tickets${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),
  getTicket: (id: string) =>
    request<TicketDetailResponse>(`/api/tickets/${encodeURIComponent(id)}`),
  assignTicket: (id: string, employeeEmail: string) =>
    request<TicketMutationResponse>(
      `/api/tickets/${encodeURIComponent(id)}/assign`,
      { method: 'POST', body: JSON.stringify({ employee_email: employeeEmail }) },
    ),
  resolveTicket: (
    id: string,
    body: {
      employee_email: string
      outcome: 'resolved' | 'unresolved'
      note: string
      recovered_amount?: string | null
    },
  ) =>
    request<TicketMutationResponse>(
      `/api/tickets/${encodeURIComponent(id)}/resolve`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  raiseQuestion: (
    eventId: string,
    body: { question: string; channel?: string; employee_email?: string | null },
  ) =>
    request<TicketMutationResponse>(
      `/api/events/${encodeURIComponent(eventId)}/raise-question`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  // --- Simulate / Playground (sandboxed rehearsal) ---
  startPlayground: (eventId: string, mode: PlaygroundMode, channel?: PlaygroundChannel) =>
    request<PlaygroundStartResponse>(
      `/api/events/${encodeURIComponent(eventId)}/playground/start`,
      { method: 'POST', body: JSON.stringify({ mode, channel }) },
    ),
  sendPlaygroundMessage: (
    eventId: string,
    history: PlaygroundTurn[],
    message: string,
    channel: string,
  ) =>
    request<PlaygroundMessageResponse>(
      `/api/events/${encodeURIComponent(eventId)}/playground/message`,
      { method: 'POST', body: JSON.stringify({ history, message, channel }) },
    ),
  advancePlayground: (eventId: string, history: PlaygroundTurn[], channel: string) =>
    request<PlaygroundAdvanceResponse>(
      `/api/events/${encodeURIComponent(eventId)}/playground/advance`,
      { method: 'POST', body: JSON.stringify({ history, channel }) },
    ),

  getMetrics: () => request<MetricsBlock>('/api/metrics'),
  runPipeline: () =>
    request<PipelineRunResponse>('/api/pipeline/run', { method: 'POST' }),
}
