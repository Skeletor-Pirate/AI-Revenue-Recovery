// Typed fetch wrapper around the FastAPI backend.
// In dev, Vite proxies /api and /health to http://localhost:8000 (vite.config.ts).

import type {
  EventAuditResponse,
  EventsResponse,
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
  getMetrics: () => request<MetricsBlock>('/api/metrics'),
  runPipeline: () =>
    request<PipelineRunResponse>('/api/pipeline/run', { method: 'POST' }),
}
