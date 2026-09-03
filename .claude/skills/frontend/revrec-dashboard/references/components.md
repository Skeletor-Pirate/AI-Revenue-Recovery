# Dashboard components — build spec

All under `frontend/src/components/`. React 19 + TS + Tailwind v4 utilities
generated from `tokens.css`. No raw hex, no `bg-slate-*`.

## Surfaces: `Card` vs `GlassCard`

```tsx
// Card — the default. Solid. Everything data-dense lives here.
function Card({ title, action, children }: CardProps) {
  return (
    <section className="rounded-2xl bg-surface-1 ring-1 ring-[var(--color-ring)] p-5">
      {title && (
        <header className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
```

```tsx
// GlassCard — liquid-glass skill. ONLY for KPI tiles, nav, drawer, modals, the
// fraud alert. Never wraps a table or a chart plot.
import { liquidGlass } from '../lib/liquid-glass';   // copied verbatim from the liquid-glass skill

function GlassCard({ children, className = '' }: PropsWithChildren<{className?:string}>) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const g = liquidGlass(ref.current, { scale: -90, chroma: 5, blur: 4 });
    return () => g.destroy();
  }, []);
  return (
    <div
      ref={ref}
      className={`glass rounded-[var(--glass-radius)] p-5 ${className}`}
      style={{
        background: `linear-gradient(180deg, var(--glass-tint-from), var(--glass-tint-to))`,
        boxShadow:
          '0 24px 60px rgba(0,0,0,0.35), inset 0 1px 1px rgba(255,255,255,0.5),' +
          'inset 0 -8px 20px rgba(255,255,255,0.06), inset 0 0 0 1px rgba(255,255,255,0.13)',
      }}
    >
      {children}
    </div>
  );
}
```

Rules from the `liquid-glass` skill that bind here:
- Panel < 800px per side. KPI tiles and the drawer qualify; a full-width bar does not.
- `border-radius` on the element drives the displacement map — keep `--glass-radius`.
- Refraction is Chromium-only; Safari/Firefox get frosted blur automatically —
  never let the effect carry meaning.
- If a KPI number smears: raise `blur`, raise tint alpha, or scrim behind the
  number. Never an opaque background.

## `AppShell`

`GlassCard`-style top bar (nav links: Overview · Queue · Recovery · Exceptions),
a theme toggle that sets `document.documentElement.dataset.theme`, and a
`max-w-7xl` content column on `bg-plane`. Add `.viz-root` class to the content
wrapper so charts inherit the viz custom props.

## `StatTile`

```tsx
type StatTile = {
  label: string;
  value: string;              // pre-formatted (formatINR / `${n}%` / `${h}h`)
  delta?: { text: string; dir: 'up' | 'down' | 'flat' };
  accent?: 'good' | 'warning' | 'critical';   // a coloured dot, not a bg
  href?: string;
};
```
Rendered inside a `GlassCard`. Big value in system sans (proportional figures);
label in `text-ink-soft text-xs uppercase tracking-wide`; delta text in
`--color-delta-up` for `up`, `--color-ink-muted` for `flat`. The KPI row is
`grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4`.

## `StatusPill`

The one place status colour is allowed — and always with a label.

```tsx
const PILL = {
  detected:     { label: 'Detected',    color: 'var(--color-ink-muted)' },
  diagnosed:    { label: 'Diagnosed',   color: 'var(--color-ink-muted)' },
  action_taken: { label: 'Action taken',color: 'var(--color-series-1)' },
  recovered:    { label: 'Recovered',   color: 'var(--color-status-good)' },
  exception:    { label: "Couldn't recover", color: 'var(--color-status-warning)' },
  flagged:      { label: 'Halted — do not retry', color: 'var(--color-status-critical)' },
} as const;
```
`<span>` with a 6px dot + label, `ring-1 ring-[var(--color-ring)]`, never
background-filled with the status colour. `flagged` also gets a small
`shield` icon (from `frontend/public/icons.svg`).

## `DataTable`

Generic, columns-driven. Not glass.

- header row `bg-surface-2`, sticky; body rows hover `bg-surface-2`.
- numeric columns: `text-right tabular-nums`; money via `formatINR`.
- sortable columns (click header; single sort key; arrow indicator).
- **filter row directly above the table, one line** (`dataviz` interaction.md):
  status multi-select, type select, root-cause select, amount min/max. Filters
  live in URL query params so a demo link is shareable.
- row click → `onRowClick(row)` (opens `DetailDrawer`).
- `flagged` rows: 3px left border `--color-status-critical`.
- long tables: virtualise (`@tanstack/react-virtual` if added) — but the total
  row count is always shown and nothing is hidden behind pagination on the
  exception list.
- empty → `<EmptyState>`; loading → `<Skeleton rows={8} />`.
- `CSV export` button in the header `action` slot.

## `DetailDrawer`

Slides in from the right over the queue. `GlassCard` surface (it's a focus
layer, < 800px wide). Contains: event summary (all `events` columns), the
`StatusPill`, and the `AuditTimeline` for that `event_id`. `Esc` / backdrop
click closes; focus-trapped; `role="dialog"`.

## `AuditTimeline`

Vertical line, one node per `audit_log` row (ordered by `id`).

```tsx
type AuditNode = {
  agent: 'detection'|'diagnosis'|'recovery'|'triage'|'audit';
  action: string;          // e.g. classified_root_cause, sent_reminder, halted_stopping_rule
  reasoning: string;       // shown IN FULL — never clamped
  payload?: unknown;       // → <PayloadViewer>
  timestamp: string;
};
```
- agent chip colour: detection/diagnosis/recovery/audit → `--color-series-1..4`
  used as a *chip*, triage → `--color-status-critical`.
- `action` in `font-mono text-xs`; `reasoning` in `text-sm text-ink` full width.
- `halted_stopping_rule` / triage nodes get a `--color-status-critical` marker.
- each node with a `payload` has a "show payload" toggle → `PayloadViewer`.

## `PayloadViewer`

Read-only JSON. `<pre>` on `bg-surface-2`, 2-space indent, `font-mono text-xs`,
`tabular-nums`, key in `--color-ink-soft`, string values in `--color-series-3`,
numbers in `--color-series-1`. Copy button. Used for drafted outreach messages
and decision-metric blobs. Max-height with internal scroll.

## `TableViewToggle`

Small segmented control (`Chart | Table`) in a `Card` header `action` slot.
`Table` renders the chart's own data as a plain `DataTable` — this is the
`dataviz` "a table view exists" requirement, not an afterthought.

## `EmptyState` / `Skeleton`

`EmptyState`: centred icon + one line ("No events in this batch yet — run the
pipeline") + optional action button (`runPipeline`). `Skeleton`: shimmer blocks
matching the component's layout; never a spinner for content areas.

## Helpers (`frontend/src/lib/`)

```ts
// format.ts
export const formatINR = (n: number) =>
  '₹' + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(n);
export const formatINRPrecise = (n: number) =>
  '₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2 }).format(n);

// theme.ts — read a token's computed value for Recharts props that can't take var()
export const cssVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
```
