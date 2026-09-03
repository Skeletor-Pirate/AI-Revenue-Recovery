# Chart catalog — AI Revenue Recovery

Read the `dataviz` skill first. This file maps *this project's data* to specific
Recharts components, with the colour role and the anti-pattern each avoids.

Data comes from two tables (see `documentation.md` §7): **`events`** (one row per
at-risk case) and **`audit_log`** (one row per agent decision). Metrics come from
the Audit agent / `GET /api/metrics` (plan.md §7).

## Shared setup

```ts
// frontend/src/charts/series.ts — assign in order, NEVER cycle, NEVER reorder by value
export const SERIES = [
  'var(--color-series-1)','var(--color-series-2)','var(--color-series-3)',
  'var(--color-series-4)','var(--color-series-5)','var(--color-series-6)',
  'var(--color-series-7)','var(--color-series-8)',
] as const;

// fixed hue per ROOT CAUSE so the colour follows the entity across every chart
export const ROOT_CAUSE_COLOR: Record<string,string> = {
  insufficient_funds:   'var(--color-series-1)',
  expired_mandate:      'var(--color-series-2)',
  bank_downtime:        'var(--color-series-3)',
  auth_otp_failure:     'var(--color-series-4)',
  abandoned_checkout:   'var(--color-series-5)',
  overdue_invoice:      'var(--color-series-6)',
  card_expired:         'var(--color-series-7)',
  suspected_fraud:      'var(--color-status-critical)', // status, not a series
};

export const STATUS_COLOR = {
  recovered:    'var(--color-status-good)',
  exception:    'var(--color-status-warning)',
  flagged:      'var(--color-status-critical)',
  action_taken: 'var(--color-series-1)',
  diagnosed:    'var(--color-ink-muted)',
  detected:     'var(--color-ink-muted)',
} as const;
```

Every chart:
- wrapped in `<ResponsiveContainer>` inside a solid `Card` (never `GlassCard`).
- `<CartesianGrid stroke="var(--color-hairline)" vertical={false} />` — recessive.
- axes: `stroke="var(--color-baseline)"`, tick `fill="var(--color-ink-muted)"`, `fontSize={12}`.
- custom `<Tooltip content={<VizTooltip/>} />` themed to `--color-surface-1` + `--color-ring`.
- `<Legend/>` whenever ≥ 2 series; omit for one series (the title names it).
- **one Y axis, always.** No `yAxisId` pairs.
- a `TableViewToggle` beside the title that swaps the plot for a `<DataTable>`.
- `isAnimationActive={false}` (deterministic screenshots for the demo).

`formatINR(n)` → `₹1,23,456` (Indian grouping), used in every money axis/tooltip/label.

---

## Overview page (`/`)

### 1. KPI row — 6 `StatTile`s (not a chart; `dataviz` "is it even a chart?")
| Tile | Value | Sub / delta |
|---|---|---|
| Total ₹ at risk | `formatINR(metrics.at_risk_total)` | count of open cases |
| ₹ recovered | `formatINR(metrics.recovered_total)` | `--color-delta-up` % of at-risk |
| Recovery rate | `metrics.recovery_rate` % | recovered ÷ actioned |
| Avg time-to-recovery | `metrics.avg_ttr_hours` h | simulated |
| Flagged (halted) | `metrics.flagged_count` | `--color-status-critical` dot |
| Exceptions | `metrics.exception_count` | link → `/exceptions` |

Hero number in system sans, `tabular-nums` off for the big figure, on for the sub.

### 2. ₹ recovered by root cause — horizontal bar
`<BarChart layout="vertical">`, `<Bar dataKey="recovered" />` with per-cell
`fill={ROOT_CAUSE_COLOR[d.root_cause]}`. Sort descending by value. Direct-label
each bar end with `formatINR`. Job: **magnitude by identity**. Avoids: pie chart,
rainbow fill.

### 3. Recovery funnel — ordinal bar (`detected → diagnosed → action_taken → recovered`)
`<BarChart>` with one `<Bar>`, cells shaded from `--color-seq-300` → `--color-seq-600`
(ordinal ramp, not categorical). Label each stage with count + % of `detected`.
`flagged` and `exception` shown as a separate small stacked bar of "left the
funnel", not mixed into the stages. Job: **stage drop-off**. Avoids: funnel
plugin, implying flagged cases are failures.

### 4. ₹ at risk vs ₹ recovered over time (simulated) — line
`<LineChart>`, two lines: `at_risk` (`--color-series-1`), `recovered`
(`--color-series-3`), 2px, `dot={false}`, crosshair tooltip. One Y axis (both are
₹). Job: **change over time**. Avoids: dual axis, area fill hiding the lower line.

---

## Recovery analytics page (`/recovery`)

### 5. Recovery rate by intervention type — horizontal bar with target
`<BarChart layout="vertical">`, `<Bar dataKey="rate" fill="var(--color-series-1)">`,
plus a `<ReferenceLine x={metrics.overall_rate} stroke="var(--color-ink-muted)"
strokeDasharray="3 3" label="overall" />` as the benchmark. Interventions:
`retry_scheduled`, `reauth_link_sent`, `alt_method_suggested`, `guided_retry`,
`nudge`, `discount_offer`, `reminder`, `formal_notice`. Job: **magnitude vs a
benchmark**. Avoids: gauge cluster, dual axis.

### 6. Root cause × outcome — heatmap
Rows = root cause, cols = `recovered / exception / flagged`. Cell fill from the
sequential ramp scaled to **row-normalised %** (or raw count — label says which).
Build with a CSS grid of divs, not Recharts (`dataviz` components.md pattern):
`background: color-mix(in oklab, var(--color-seq-400) <pct>%, var(--color-surface-1))`.
Per-cell hover tooltip. Job: **two-way magnitude**. Avoids: rainbow scale, a hue
at a "neutral" midpoint.

### 7. Attempts per case — histogram
`<BarChart>` of `attempts_so_far` bucketed 0,1,2,3,4+. Bar past the stopping-rule
limit (3) tinted `--color-status-warning` with an icon+label note "auto-flagged
for human review". Job: **distribution + a rule boundary**. Avoids: a smooth KDE,
hiding the stopping rule.

### 8. Days overdue (B2B invoices only) — histogram
Buckets 0–7, 8–30, 31–60, 61–90. Overlay the escalation ladder stages as vertical
`<ReferenceLine>`s (reminder → formal notice → human handoff). Filter to
`event_type = overdue_invoice`. Job: **distribution + process stages**.

---

## Queue / case pages

### 9. At-risk queue — `DataTable` (see components.md), not a chart
Columns: `event_id · customer_id · type · amount (₹, tabular) · status pill ·
root cause · attempts · days overdue · updated_at`. Filter row above:
status (multi), type, root cause, amount range. Row click → `DetailDrawer`.
Default sort: amount desc. Flagged rows get a left `--color-status-critical` rule.

### 10. Per-case decision trail — `AuditTimeline` (see components.md)
Vertical timeline from `audit_log` for one `event_id`, ordered by `id`. Each node:
agent chip (detection/diagnosis/recovery/triage/audit), `action`, `reasoning`
(full, never truncated — this is the judged "explainable" bit), timestamp, and an
expandable `PayloadViewer` for `payload` JSON (drafted message / decision
metrics). Job: **sequence of decisions**. Avoids: a Gantt chart, collapsing the
reasoning.

---

## Exceptions page (`/exceptions`)

### 11. ₹ un-recovered by reason — horizontal bar
`<BarChart layout="vertical">`, single hue `--color-status-warning`, sorted desc,
`formatINR` labels. The total must equal `at_risk_total − recovered_total`
(reconciliation check — show the equation under the chart). Job: **honest
magnitude of the gap**. Avoids: cherry-picking, a share-of-total pie.

### 12. Fraud-cluster alert card — bespoke, on a `GlassCard`
Red-accented. Shows the cluster: shared `card_declined` reason, amounts in the
±₹40 band, tight time window, N distinct "customers". States plainly: *Diagnosis
re-classified this cluster as `flagged`; Recovery Agent refused to act; escalated
for human review.* Links to each `fraud_NN` event's trail. This is the plan.md §6
/ §10 demo moment — make it the most prominent thing on the page.

### 13. Exception `DataTable`
Every non-recovered case: `event_id · type · amount · root cause ·
terminal status (exception|flagged) · reason (full text) · attempts · last action`.
No pagination that hides rows — virtualise if long, but the count is always
visible and the reason column never truncates. CSV export button.

---

## Accessibility / verification (dataviz check 6 + 7)

- Legend present for every ≥2-series chart; ≤4 series also direct-labelled.
- `TableViewToggle` on every chart.
- Status = dot + label, colour second.
- Dark mode: flip `[data-theme]`, confirm every chart re-reads tokens (Recharts
  needs a re-render — key charts on the theme value or read colours in a
  `useMemo(..., [theme])`).
- Screenshot each page light + dark and eyeball for label collisions and overflow
  before calling it done.
