// Typed fetch wrapper around the FastAPI backend.
// In dev, Vite proxies /api and /health to http://localhost:8000 (vite.config.ts).

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
  // endpoints land here as the backend routers are built:
  // listEvents: () => request<EventOut[]>('/api/events'),
  // runPipeline: () => request<PipelineResult>('/api/pipeline/run', { method: 'POST' }),
  // metrics: () => request<Metrics>('/api/metrics'),
}
