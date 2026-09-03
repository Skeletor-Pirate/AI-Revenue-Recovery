---
name: frontend
description: >
  Every frontend build for this project, in one skill. Four modes:
  (1) scroll-craft — a premium scroll-driven interactive landing / welcome page
  where scroll is the timeline (video scrub, pinned sections, side rails,
  assembling headlines, a signature move); (2) liquid-glass — the Apple-style
  refraction effect on cards, navs, modals, buttons (iOS 26 glass, frosted glass
  with real edge bending); (3) glass-scroll-3d — a React scroll page that adds
  real-time 3D / React Three Fiber scenes behind liquid-glass chrome; (4)
  revrec-dashboard — the data-dense dashboard pages that display the pipeline
  output (KPI tiles, recovery charts, at-risk queue, per-case decision trails,
  exception list) in React 19 + Vite + TS + Tailwind v4 + Recharts. Use for
  scrollytelling, "Apple-style landing page", "make my brand a scroll
  experience", glassmorphism, "liquid glass over a 3D scene",
  react-three-fiber landing pages, and any dashboard page, chart, stat tile,
  data table, status pill, detail drawer, or audit-trail timeline.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion, WebFetch
---

# frontend

One skill for the whole frontend. It bundles four previously separate skills as
sub-guides; pick the mode that matches the request, then follow that guide.

| Mode | Read | Use it for |
|---|---|---|
| **scroll-craft** | [scroll-craft/GUIDE.md](scroll-craft/GUIDE.md) | The marketing / welcome / landing / pitch page. Scroll-as-timeline choreography, feeling curve, page grammar, generated assets, screenshot verification. |
| **liquid-glass** | [liquid-glass/GUIDE.md](liquid-glass/GUIDE.md) | The refraction effect on any single component (nav, card, modal, button, HUD panel). A primitive the other three modes call. |
| **glass-scroll-3d** | [glass-scroll-3d/GUIDE.md](glass-scroll-3d/GUIDE.md) | A React scroll page that needs real-time 3D / WebGL (React Three Fiber) behind glass chrome. Composes scroll-craft + liquid-glass + R3F. |
| **revrec-dashboard** | [revrec-dashboard/GUIDE.md](revrec-dashboard/GUIDE.md) | Every dashboard page that displays pipeline output: charts, tables, KPI tiles, the queue, per-case audit trails, the exception list. |

## Routing

1. **A dashboard page** (reads and filters data — queue, charts, KPI row,
   case detail, exceptions) → **revrec-dashboard**. Never use scroll-craft here;
   a dashboard is a document you read, not a scroll story.
2. **The welcome / landing / pitch page** → **scroll-craft**. If that page also
   needs a live Three.js / R3F scene → **glass-scroll-3d** (it invokes
   scroll-craft's process and owns the scroll↔3D seam).
3. **Just the glass effect on a component**, anywhere → **liquid-glass**.
   revrec-dashboard and glass-scroll-3d already reference it for their chrome.

When a build spans modes, the sub-guides say how they combine — follow the
"How this combines" / "Order of work" section in the guide you land on. Sub-guides
refer to each other as sibling directories (`../liquid-glass/GUIDE.md`), not as
separate skills.

## Buildathon rule

For any page that is part of the submission, **run the `build-workflow` skill
first** (plan.md check → Razorpay source-check → keep architecture.md diagrams
current → documentation.md → history.md). Then come back here. Dashboard work
also updates `documentation.md` §3.5 and `architecture.md` §7 in the same change
(plan.md §13).

## Bundled resources

```
frontend/
  scroll-craft/    GUIDE.md, engine/, scripts/, references/, templates/, CHANGELOG.md
  liquid-glass/    GUIDE.md, liquid-glass.js, README.md, demo/
  glass-scroll-3d/ GUIDE.md, references/react-three-fiber.md
  revrec-dashboard/ GUIDE.md, references/ (tokens.css, charts.md, components.md)
```

Copy engine / helper files (`scroll-craft/engine/*`, `liquid-glass/liquid-glass.js`)
into the build unedited; theme with tokens and write your own markup.
