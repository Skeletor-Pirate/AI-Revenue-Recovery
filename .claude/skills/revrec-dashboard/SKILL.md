---
name: revrec-dashboard
description: >
  Build the AI Revenue Recovery dashboard frontend — the data-dense pages that
  display the pipeline output: KPI tiles, recovery charts, the at-risk queue,
  per-case decision trails, and the honest exception list. React 19 + Vite +
  TypeScript + Tailwind v4 + Recharts. Enforces one shared design-token layer so
  every chart, table, and liquid-glass panel reads as one UI. Use for any
  dashboard page, chart, stat tile, data table, status pill, detail drawer, or
  audit-trail timeline in this project. Do NOT use scroll-craft here — that skill
  is for the marketing/welcome page only; dashboards are for reading data, not
  scrolling through a story.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion, WebFetch
---

# revrec-dashboard

The dashboard shows what the four agents did: money at risk, money recovered by
root cause, every bounded decision, and every case that was *not* recovered with
the reason why. It must look like one product, not a pile of widgets.

## How this combines with the other skills

| Skill | Role here | Do |
|---|---|---|
| **`dataviz`** | chart method — form choice, color-by-job, the six checks, anti-patterns | Read it before writing any chart. Its palette values are already baked into `references/tokens.css`. |
| **`liquid-glass`** | the elevated surfaces — KPI cards, the top nav, the per-case detail drawer, modals | Glass for *chrome and focus surfaces only*. Follow its CSS recipe + `liquidGlass(ref.current)` in `useEffect`. |
| **`scroll-craft`** | **not used on dashboard pages** | Only the welcome/landing route. A dashboard is a document you read and filter, not a scroll timeline. |

### Where liquid glass goes — and where it must not

Glass **yes:** top nav bar, the KPI/stat-tile row, the per-case detail drawer
that slides over the queue, confirmation modals, the fraud-cluster alert card.
These are small (< 800px per side — the SVG-filter GPU cost rule), sit above a
tinted page plane, and benefit from the depth cue.

Glass **no:** data tables, chart plot areas, long scrollable lists, anything
where refraction would smear the numbers. Tables and charts sit on the **solid
`--surface-1`** card, hairline-ringed, never glass. Legibility of data beats the
effect every time (this is the `liquid-glass` skill's own "keep the interior
legible" rule applied hard).

If a glass KPI card sits over a busy background and its number smears: raise the
glass `blur` option, raise the tint-gradient alpha, or drop a local scrim behind
the number — never an opaque panel.

## Order of work

1. **Install the token layer.** Merge `references/tokens.css` into
   `frontend/src/index.css` (it uses Tailwind v4 `@theme` + a `.viz-root` scope).
   Every colour on the dashboard comes from these tokens — no raw hex in
   components, no arbitrary Tailwind colours (`bg-slate-*` etc. are out).
2. **Add deps:** `npm i recharts` is already in `package.json`. Add nothing else
   unless a chart in `references/charts.md` says so.
3. **Build the shell** — see `references/components.md` (`AppShell`, `PageHeader`,
   `Card`, `GlassCard`, `StatTile`, `StatusPill`, `DataTable`, `DetailDrawer`,
   `AuditTimeline`, `EmptyState`, `TableViewToggle`).
4. **Build each page** from the page map below, pulling charts from
   `references/charts.md` (each entry names the Recharts component, the data
   shape, the colour role, and the anti-pattern it avoids).
5. **Accessibility pass** (`dataviz` check 6): every chart with ≥ 2 series has a
   legend; every chart has a table-view toggle; status is never colour-alone
   (icon + label); dark mode works via the tokens (not an auto-flip).

## Page map

| Route | Purpose | Key components |
|---|---|---|
| `/` (Overview) | the headline: is revenue being recovered? | KPI row (6 tiles), Recovered-by-root-cause bar, Recovery funnel, At-risk vs recovered over time |
| `/queue` (At-risk queue) | every live case, filterable | `DataTable` of events, filter row (status, type, root cause), row → `DetailDrawer` |
| `/case/:id` (or drawer) | one case, end to end | event summary card, `AuditTimeline` of every agent decision, drafted-message / payload viewer |
| `/recovery` (Recovery analytics) | how well each intervention works | Recovery-rate-by-intervention bar, Root-cause × outcome heatmap, Attempts-per-case histogram |
| `/exceptions` (Exception list) | honest "not recovered, here's why" | `DataTable` (reason column never truncated), the fraud-cluster alert card, ₹ un-recovered by reason bar |

The exception list and the fraud-cluster halt are the judged moments (plan.md
§6, §10) — build them to be read, not skimmed. Never hide or paginate the
exception reasons away.

## Non-negotiables (on top of dataviz's)

| Never | Instead |
|---|---|
| A pie/donut for status or root-cause share | One stacked horizontal bar, or a plain bar per category |
| A dual-axis chart (₹ and % on one plot) | Two charts, or index both to a common base |
| Raw hex / `bg-slate-*` / arbitrary colours in a component | A token from `tokens.css`, referenced by role |
| Recharts' default rainbow `fill` cycling | The fixed `SERIES` array from `references/charts.md`, assigned in order, never cycled |
| Liquid glass on a table or chart plot | Solid `--surface-1` card |
| A status shown by colour only | `StatusPill`: dot + label, colour from the status palette |
| `recovered` / `exception` / `flagged` rendered the same | `exception` = amber "couldn't", `flagged` = red "halted, do not retry", `recovered` = green |
| Money as a float, or `₹` glued to the digits with no `tabular-nums` | `formatINR()` helper, `font-variant-numeric: tabular-nums` in table/axis |
| A chart with no loading and no empty state | `Skeleton` while fetching, `EmptyState` when the batch is empty |

## Output

The token layer merged, the shared components, the five pages wired to the API
client (`frontend/src/api/client.ts` — add typed methods as backend routers land:
`listEvents`, `getMetrics`, `getAuditTrail`, `runPipeline`). Update
`documentation.md` §3.5 and `architecture.md` §7 (Dashboard row) in the same
change — plan.md §13.
