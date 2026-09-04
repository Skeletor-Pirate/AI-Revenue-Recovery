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
  EventsResponse,
  MetricsBlock,
  PipelineRunResponse,
} from './types'

const SOURCE = (import.meta.env.VITE_DATA_SOURCE ?? 'fixtures').toLowerCase()
export const IS_LIVE = SOURCE === 'live' || SOURCE === 'api'

interface FixturesShape {
  events: EventsResponse
  eventAudit: EventAuditResponse
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

  async runPipeline(): Promise<PipelineRunResponse> {
    if (IS_LIVE) return api.runPipeline()
    return settle(fx.pipelineRun)
  },
}
