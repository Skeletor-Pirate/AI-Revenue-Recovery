---
name: build-workflow
description: Mandatory workflow wrapper for every non-trivial build, design, or planning task on the Razorpay AI Buildathon project. Enforces the order — check out and keep plan.md current, verify against Razorpay sources, keep the architecture + data-flow diagrams current in architecture.md while you work, then document everything in documentation.md and log a brief of the session in history.md. Invoke at the start of any work session and follow it end to end.
---

# Build workflow

This project is judged by Razorpay engineers and the docs are source-of-truth
(CLAUDE.md / plan.md §13). Any task that adds or changes a file, function,
endpoint, DB column, enum, config key, command, agent rule, metric, or
customer-/judge-facing copy MUST run through this workflow in order. Trivial
edits (typo fixes, comment tweaks) are exempt.

## Step 0 — Read and update plan.md every time

`plan.md` (repo root) is the project brief — track wording, "the bar," build
order (§9), stopping rules (§6), data model (§5), approved deviations (§12).
**Always check it out before starting** any task, and **always add onto it** as
work progresses: advance the build-order position (§9), record any approved
deviation under §12, and update the relevant section in the same change that
changes the plan's reality. Never let plan.md drift behind what has been built.

## Step 1 — Verify against real Razorpay sources (before you plan or build)

This project is judged by Razorpay engineers on how well it mirrors Razorpay's
actual product and language. Run this **before** planning/building and **again**
before finalizing any agent classification/intervention logic, outreach copy,
computed metrics/report, `documentation.md`, `architecture.md`, the README, or
the pitch script.

**Rule of thumb:** if a detail can be verified against a real Razorpay source,
verify it — WebFetch it, don't rely on memory. Never ship a made-up failure
code, API field, event type, agent name, or public metric when the docs give the
real one.

### Links to check (WebFetch these)

| Priority | Source | Link | Use it for |
|---|---|---|---|
| Always | Buildathon program page | https://razorpay.com/buildathon/ | Exact wording of Track 03 "AI Revenue Recovery", "the bar", submission requirements — match verbatim where natural |
| Always | Agent Studio / Agentic Experience Platform launch | https://razorpay.com/newsroom/razorpay-launches-the-worlds-first-ai-native-agent-studio-for-payments-at-ftx26-powered-by-anthropics-claude/ | Razorpay's own production agents (Abandoned Cart Conversion, Dispute Responder, Subscription Recovery) — mirror their plain-English naming and tone, not ML jargon |
| API work | Razorpay Developer Docs | https://razorpay.com/docs/ | API correctness — payment links, subscriptions, webhooks, test mode |
| API work | Test card & UPI details | https://razorpay.com/docs/payments/payments/test-card-upi-details/ | Real test credentials and simulated failure scenarios |
| API work | Webhooks docs | https://razorpay.com/docs/webhooks/ | Real event types/payloads (`payment.failed`, `subscription.charged`, etc.) — use for synthetic `raw_failure_reason` values |
| API work | Payment Links API | https://razorpay.com/docs/payments/payment-links/ | Simulating/triggering payment events |
| API work | Subscriptions API | https://razorpay.com/docs/payments/subscriptions/ | Failed-subscription recovery direction |
| API work | Python SDK | https://razorpay.com/docs/api/ and https://github.com/razorpay | Current recommended SDK version/usage |
| Framing | FTX'26 event hub | https://razorpay.com/ftx26/ | Agentic commerce direction, partners, positioning |
| Framing | Razorpay Sprint 2026 blueprint | https://razorpay.com/sprint/26 | RTO/return-risk-by-pincode scoring, dispute agents — stretch-feature inspiration |
| Framing | Razorpay Newsroom | https://razorpay.com/newsroom/ | Anything published recently |
| Framing | Main website | https://razorpay.com/ | Current positioning and messaging |
| Recent news | Razorpay LinkedIn | https://www.linkedin.com/company/razorpay/ | Latest posts |
| Recent news | Razorpay X (Twitter) | https://twitter.com/Razorpay | Fastest-moving announcements |

### How to use the sources

1. **Before diagnosis-rules work:** pull real failure/error codes from the
   test-mode docs so root-cause categories map to actual gateway language.
2. **Before outreach copy:** re-skim the Agent Studio launch page and echo its
   plain business-English tone.
3. **Before finalizing README / pitch:** re-read the buildathon page's "the bar"
   text; ensure every submission sentence maps to a specific phrase in it
   (explainable, bounded, gated; measured money recovered; compliant escalation;
   stopping rules; audit trail; honest exception list).
4. **Stretch feature:** adapt one concrete Sprint 2026 idea (e.g. pincode-level
   return-risk segmentation) into the Revenue Recovery context.

Note anything that changed since `plan.md` was written and reconcile the plan
(Step 0) before proceeding.

## Step 2 — Keep architecture.md current *as you work*

`architecture.md` exists at the repo root. In the **same change** that alters the
pipeline, data model, agent flow, runtime topology, or event lifecycle, update
it — do not defer this to the end.

- Capture the **data flow** end to end: event sources → Detection → Diagnosis →
  Recovery → Audit/Reporting → FastAPI → React, including what each stage reads
  and writes in the Postgres store via `backend/app/db/store.py`.
- Express it with **Mermaid diagrams** (`flowchart`, `sequenceDiagram`,
  `erDiagram`, `stateDiagram-v2` for the event lifecycle). Keep the existing
  numbered-section structure; add or edit the diagram that changed.
- Update the design-decision log for any non-obvious choice or brief deviation
  (also record deviations under plan.md §12).
- Bump the `Last updated:` line with the date and the plan.md §9 build-order step
  the change maps to.

## Step 3 — Document everything in documentation.md (when the work is done)

Update `documentation.md` in the same change: every new/changed file, function,
class, endpoint, DB column, enum value, config key, command, and test — with its
purpose and how to run it. Update the build-status table and the runbook. Bump
its `Last updated:` line and note the build-order step.

## Step 4 — Log a brief in history.md (at the very end)

Append a dated entry to `history.md` at the repo root (create it if missing).
Newest entry first, under the top heading. Each entry is a short brief:

```
## YYYY-MM-DD — <one-line title>

- **Did:** what changed, which files, which build-order step.
- **Verified:** which Razorpay sources were checked and anything that changed.
- **Docs:** architecture.md / documentation.md sections updated.
- **Next:** what the following session should pick up.
```

Keep it to a handful of bullets — history.md is a running changelog, not a
duplicate of documentation.md.

## Checklist before you consider a task complete

0. plan.md read at the start; build-order position (§9) and §12 deviations updated.
1. Razorpay sources (Step 1) checked, findings reconciled into plan.md.
2. architecture.md diagrams + decision log current, `Last updated:` bumped.
3. documentation.md reference + build-status + runbook current, `Last updated:` bumped.
4. history.md has a new dated brief.
5. plan.md §12 updated if the brief was contradicted.
