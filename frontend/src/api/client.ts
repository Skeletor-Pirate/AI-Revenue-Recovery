import type {
  EventAuditResponse,
  EventRead,
  EventSequencerResponse,
  EventSimilarResponse,
  EventsResponse,
  EventVoiceResponse,
  MetricsBlock,
  PipelineRunResponse,
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
  getSequencerSchedule: (id: string) =>
    request<EventSequencerResponse>(`/api/events/${encodeURIComponent(id)}/sequencer`),
  recordPTP: (id: string, promisedDate: string, notes?: string) =>
    request<{ status: string; event: EventRead }>(`/api/events/${encodeURIComponent(id)}/ptp`, {
      method: 'POST',
      body: JSON.stringify({ promised_date: promisedDate, notes }),
    }),
  getMetrics: () => request<MetricsBlock>('/api/metrics'),
  runPipeline: () =>
    request<PipelineRunResponse>('/api/pipeline/run', { method: 'POST' }),
}
