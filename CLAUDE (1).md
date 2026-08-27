# CLAUDE.md — Project Brief for Claude Code

This file is the single source of truth for this project. Read it fully before writing code.

---

## 0. ALWAYS CHECK THESE LINKS FIRST

Before starting any work session, and again before finalizing any agent's logic, copy, or the README/pitch — open these. This project is judged by Razorpay engineers on how well it reflects Razorpay's actual product and language, not generic fintech assumptions. Re-checking these regularly is cheap insurance against drifting into generic-hackathon territory.

| Source | Link | Why it matters |
|---|---|---|
| **Buildathon program page** | https://razorpay.com/buildathon/ | Source of truth for tracks, "the bar," and submission requirements — re-read the exact wording before finalizing anything |
| **Razorpay main website** | https://razorpay.com/ | General product surface, positioning, current messaging |
| **Razorpay Developer Docs** | https://razorpay.com/docs/ | API correctness — payment links, subscriptions, webhooks, test mode |
| **Test Mode / test card & UPI details** | https://razorpay.com/docs/payments/payments/test-card-upi-details/ | Exact test credentials and simulated failure scenarios to build against |
| **Webhooks docs** | https://razorpay.com/docs/webhooks/ | Real event types/payloads (`payment.failed`, `subscription.charged`, etc.) |
| **Payment Links API** | https://razorpay.com/docs/payments/payment-links/ | For simulating/triggering payment events |
| **Subscriptions API** | https://razorpay.com/docs/payments/subscriptions/ | Relevant to the "failed-subscription recovery" direction |
| **Agent Studio + Agentic Experience Platform launch (Newsroom)** | https://razorpay.com/newsroom/razorpay-launches-the-worlds-first-ai-native-agent-studio-for-payments-at-ftx26-powered-by-anthropics-claude/ | **Most important non-docs link.** Describes Razorpay's own production agents (Abandoned Cart Conversion Agent, Dispute Responder Agent, Subscription Recovery Agent) — mirror their naming/tone |
| **Razorpay Newsroom (general)** | https://razorpay.com/newsroom/ | Latest official announcements — check for anything published after this file was written |
| **FTX'26 event hub** | https://razorpay.com/ftx26/ | Context on Razorpay's agentic commerce direction, partners, positioning |
| **Razorpay Sprint 2026 product blueprint** | https://razorpay.com/sprint/26 | Real examples: RTO/return-risk-by-pincode scoring, dispute agents — good stretch-feature inspiration |
| **Razorpay LinkedIn company page** | https://www.linkedin.com/company/razorpay/ | Latest company posts, product announcements, hiring/culture signals — *verify this URL resolves before relying on it, LinkedIn company handles occasionally change* |
| **Razorpay GitHub org (SDKs, sample code)** | https://github.com/razorpay | Official Python SDK and integration examples — check for the current recommended SDK version/usage pattern |
| **Razorpay X (Twitter)** | https://twitter.com/Razorpay | Fastest-moving source for very recent announcements |

**Rule of thumb:** if you're about to invent a detail (a failure code, an API field name, an agent name, a metric Razorpay already reports publicly), stop and check the docs/newsroom links above first. Anything that can be verified against a real Razorpay source should be.

---

## 1. What this project is

We're building a submission for the **Razorpay AI Buildathon** (a student hiring program — build a working prototype, get evaluated on a public GitHub repo + 5-min pitch video + architecture writeup + panel interview, no resume screen).

**Track chosen: AI Revenue Recovery.**

> Build an agent that detects revenue at risk, determines the right intervention, and executes a bounded recovery workflow — from payment failures and checkout abandonment to overdue receivables.

**The bar we're graded against (verbatim from the brief):**
> Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail.

### Official problem statement — Track 03: AI Revenue Recovery (verbatim, from razorpay.com/buildathon/)

> **Find revenue that's slipping away and win it back**
>
> Build an agent that detects revenue at risk, determines the right intervention, and executes a bounded recovery workflow: from payment failures and checkout abandonment to overdue receivables.
>
> **Why now:** Revenue loss rarely happens in one clean step. A payment degrades, a checkout gets abandoned, a subscription fails, or an invoice goes overdue. AI can now close the loop from detecting the problem to diagnosing it, choosing the right intervention, and recovering the money.
>
> **Example directions:**
> - Payment degradation → root cause → recovery action
> - Checkout drop-off recovery
> - Failed-subscription recovery
> - B2B receivables chaser
> - Mandate retry sequencer
> - Hinglish voice recovery
> - Promise-to-pay tracker
>
> **The bar:** Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail.

Our project sits squarely in the first example direction ("Payment degradation → root cause → recovery action") with elements of "B2B receivables chaser" (the escalation ladder + stopping rules) and a plausible stretch into "Mandate retry sequencer" if time allows. **Always re-verify this text against the live buildathon page before submission** in case wording is updated.

**Deadline: September 5.** Today is August 27. Target: fully built and demo-ready by **September 3**, leaving Sep 4 for the pitch video and Sep 5 for submission. Treat Sep 3 as a hard feature freeze.

---

## 2. Why this approach (don't build a generic retry bot)

Most entrants in this track will build a single generic "payment failed → send a nudge" retry loop applied identically to every case. That is explicitly NOT what wins here. The differentiator is:

**Root-cause-differentiated recovery.** A failed payment can happen for very different reasons, and each deserves a different intervention:

| Root cause | Right intervention |
|---|---|
| Insufficient funds | Wait + retry near a likely salary-credit window (not immediately) |
| Expired mandate / card | Send a re-authorization link, don't just retry the old charge |
| Bank/network downtime | Suggest an alternate payment method |
| OTP/auth failure | Prompt for a fresh, guided retry |
| Abandoned checkout (mid-flow) | Personalized nudge, possibly a small bounded discount |
| Overdue B2B invoice | Escalation ladder: friendly reminder → formal notice → human handoff (never further) |
| Repeated failures across a cluster of accounts (looks like fraud/abuse, not a genuine recoverable failure) | HALT recovery, flag for human review — do not keep retrying |

That last row is intentional: it's our planned "one failure handled gracefully" moment for the demo (see Section 6).

---

## 3. System architecture — 4 agents + a shared store

```
                     ┌─────────────────────┐
  synthetic events ─▶│  Detection Agent     │  flags at-risk revenue, writes to event store
  (+ optional real   └──────────┬──────────┘
   Razorpay test-mode           │
   webhooks)                    ▼
                     ┌─────────────────────┐
                     │  Diagnosis Agent     │  classifies root cause (rules-first, LLM for
                     └──────────┬──────────┘  ambiguous/free-text cases), includes the
                                │             "is this actually fraud, not a genuine
                                ▼              failure?" triage check
                     ┌─────────────────────┐
                     │  Recovery Agent      │  routes to root-cause-specific intervention,
                     └──────────┬──────────┘  drafts outreach copy, enforces stopping rules
                                │
                                ▼
                     ┌─────────────────────┐
                     │  Audit/Reporting     │  every decision logged; computes ₹ recovered,
                     │  Agent               │  recovery rate by cause, honest exception list
                     └─────────────────────┘

     All four agents read/write through one shared SQLite event store
     (agents/store.py) — this IS the audit trail.
```

**Every agent action must be written to the `audit_log` table before/as it acts.** No action happens silently. This is non-negotiable — it's literally what "the bar" asks for.

---

## 4. Tech stack (decided, don't relitigate)

- **Python only** — no Go. Reasoning already settled: this project needs agent reasoning quality and clean architecture, not concurrency/throughput; a polyglot split would burn build-time on plumbing with no judging payoff.
- `FastAPI` — optional webhook listener for real Razorpay test-mode events
- `sqlite3` (stdlib) — event store + audit log (see schema below)
- `pandas` — synthetic data generation, metrics rollups
- `anthropic` Python SDK — Diagnosis Agent reasoning on ambiguous cases + Recovery Agent message drafting
- `razorpay` Python SDK — test-mode payment link / subscription / webhook integration
- `streamlit` — dashboard (fastest path to a legible visual deliverable)
- `python-dotenv` — env var loading
- `faker` — synthetic customer data
- `pytest` — sanity tests, so the live demo doesn't break

**Payments: Razorpay TEST MODE only.** No real money, ever. Test API keys (`rzp_test_...`), test cards, test UPI VPAs, test webhook events. This is explicit in the brief ("Razorpay test-mode APIs").

---

## 5. Data model (SQLite, `data/events.db`)

### `events` table
| column | notes |
|---|---|
| `event_id` | PK |
| `event_type` | `failed_payment` \| `abandoned_checkout` \| `overdue_invoice` \| `expired_mandate` |
| `customer_id` | |
| `amount`, `currency` | |
| `raw_failure_reason` | whatever the gateway/synthetic generator gave us, pre-diagnosis |
| `attempts_so_far` | int, used for stopping-rule enforcement |
| `days_overdue` | int, relevant for B2B invoices |
| `created_at`, `updated_at` | |
| `status` | `detected` → `diagnosed` → `action_taken` → `recovered` \| `exception` \| `flagged` |
| `root_cause` | filled by Diagnosis Agent |
| `diagnosis_confidence` | float 0–1 |
| `recovered_amount` | float, 0 until recovered |

### `audit_log` table
| column | notes |
|---|---|
| `id` | PK autoincrement |
| `event_id` | FK |
| `agent` | `detection` \| `diagnosis` \| `recovery` \| `triage` |
| `action` | e.g. `classified_root_cause`, `sent_reminder`, `halted_stopping_rule` |
| `reasoning` | human-readable justification — this is what we show the panel |
| `payload` | JSON blob (e.g. drafted message text, or decision metrics) |
| `timestamp` | |

---

## 6. Stopping rules & guardrails (must be explicit and visible, not implicit)

- **Max retry attempts per case** — e.g. 3, then auto-flag for human review.
- **Max escalation stages** for overdue invoices — e.g. reminder → formal notice → human handoff. Never auto-escalate further (no auto-legal-action, no auto-account-suspension).
- **Cooldown windows** between contacts — don't spam a customer.
- **Amount threshold for human approval** — any discount offer or aggressive escalation above a set ₹ amount requires human sign-off before executing (simulate this as a flag in the audit log, not a UI, if time is short).
- **The fraud-pattern triage halt** — if a case shows signs of being part of a cluster (same failure reason, same amount pattern, tight time clustering across multiple "customers"), the Diagnosis Agent must reclassify it as `flagged` instead of `recoverable`, and the Recovery Agent must refuse to act on it. Log the reasoning clearly. **This is our deliberate "one failure handled gracefully" demo moment** — build a synthetic case that triggers this, and make sure the demo shows the system catching its own initial misclassification and correcting course.

---

## 7. Synthetic dataset requirements

Generate 50–100 synthetic events (`data/generate_synthetic_data.py`, output to `data/synthetic_events.csv` or directly seeded into the DB) with realistic variety:
- Mix of all four `event_type`s
- Mix of root causes represented in `raw_failure_reason` (insufficient funds, bank downtime, expired mandate, OTP failure, generic abandonment, forgotten invoice)
- A deliberate small cluster (3–5 events) with matching failure signatures designed to trigger the fraud-pattern triage halt described above
- A realistic spread of amounts (₹200 to ₹50,000) and days_overdue (0–90) for B2B cases
- Use `faker` for customer_id/names to keep it realistic without real PII

**Metrics to compute and display at the end of every pipeline run:**
- Total ₹ at risk in the batch
- ₹ recovered, broken down by root cause
- Recovery rate (%) by intervention type
- Average time-to-recovery (simulated)
- **Exception list**: every case NOT recovered, with the stated reason why — do not hide or minimize this list, it's explicitly what the judging bar asks for ("honest exception list", "don't cherry-pick")

---

## 8. Repo structure to build

```
/data
    generate_synthetic_data.py
    synthetic_events.csv          (generated, gitignored if large)
    events.db                     (gitignored)
/agents
    store.py                      (shared SQLite event store — build this FIRST, everything depends on it)
    detection.py
    diagnosis.py
    recovery.py
    audit.py
/webhooks
    listener.py                   (FastAPI app, Razorpay test-mode webhook endpoint — optional/stretch)
/dashboard
    app.py                        (Streamlit dashboard)
/docs
    architecture.md                (diagram + explanation, for submission)
    README.md
run_pipeline.py                    (single entrypoint: loads synthetic batch, runs all 4 agents, prints/report metrics)
requirements.txt
.env.example
.gitignore
tests/
    test_store.py
    test_diagnosis.py
```

---

## 9. Build order (follow this sequence)

1. `agents/store.py` — the shared event store. Everything else depends on this schema. Build and test this first.
2. `data/generate_synthetic_data.py` — produces the batch, seeds the DB via `store.py`.
3. `agents/detection.py` — reads synthetic batch (and later, optionally, real webhook events), marks events `detected`.
4. `agents/diagnosis.py` — rules-based root-cause classification first (fast, deterministic, easy to demo/explain), fall back to a Claude API call for ambiguous free-text `raw_failure_reason` values. Include the fraud-cluster triage check here.
5. `agents/recovery.py` — routes each diagnosed event to its intervention, drafts message copy via Claude, enforces all stopping rules from Section 6, updates status to `recovered` / `exception` / `flagged`.
6. `agents/audit.py` — aggregates `audit_log` + `events` into the metrics from Section 7.
7. `run_pipeline.py` — wires 3–6 together as one runnable script producing a clean printed/markdown summary.
8. `dashboard/app.py` — Streamlit visualization of the pipeline output (at-risk queue, per-case decision trail, recovery charts, exception list).
9. `webhooks/listener.py` — only if time allows; wires real Razorpay test-mode webhooks into `detection.py` instead of pure synthetic replay.
10. `docs/architecture.md` + `README.md` — write last, once the system is stable, including a section titled "What broke and how we fixed it" (this is explicitly requested by the buildathon submission process).

**If short on time, cut in this order (never cut the core 4 agents, stopping rules, or audit trail):**
1. Streamlit dashboard → fall back to a clean printed/markdown report
2. Real Razorpay webhook wiring → fall back to pure synthetic batch replay
3. Claude-drafted outreach copy → fall back to templated text

---

## 10. Non-negotiables (these are literally the judging criteria)

- Every money-related agent action must be **explainable** (has a `reasoning` string in the audit log), **bounded** (respects a stopping rule or threshold), and **gated** (human-approval flag above a set amount).
- Metrics must be **honest** — computed from the full batch, exceptions included, never cherry-picked.
- The demo must include **one real handled failure** — use the fraud-cluster triage halt from Section 6 as this moment.
- Test mode only. No real money at any point.
- Public GitHub repo, clean README, architecture diagram, setup instructions a stranger could follow, and a "what broke and how we fixed it" writeup.

---

## 11. Reference sources — read these before building, and re-check them mid-build

This project is being built *for* Razorpay, evaluated *by* Razorpay engineers, on top of Razorpay's own product surface. Optimizing for their needs means our design choices, terminology, and even our dashboard's visual language should echo what they've already shipped — not read like a generic fintech side project. Treat the links below as required reading, not optional context.

### Buildathon program (source of truth for the actual ask)
- **Buildathon site (tracks, "the bar," submission requirements):** https://razorpay.com/buildathon/
  - Re-read the exact wording of the AI Revenue Recovery track and "the bar" before finalizing metrics/report — match their language in the README and pitch (e.g. use their phrase "audit trail," "stopping rules," "measured money recovered" verbatim where natural).

### Razorpay API / test-mode docs (source of truth for integration correctness)
- **Main developer docs:** https://razorpay.com/docs/
- **Test mode / test card & UPI details:** https://razorpay.com/docs/payments/payments/test-card-upi-details/
- **Payment Links API:** https://razorpay.com/docs/payments/payment-links/
- **Subscriptions API (relevant for "failed-subscription recovery" direction):** https://razorpay.com/docs/payments/subscriptions/
- **Webhooks (payloads, event types like `payment.failed`, `subscription.charged`):** https://razorpay.com/docs/webhooks/
- **Python SDK reference:** https://razorpay.com/docs/api/ (check current SDK repo on GitHub too — `razorpay/razorpay-python`)
  - Use these to make sure our synthetic `raw_failure_reason` values and event schema mirror Razorpay's *real* failure codes/error descriptions, not made-up ones — this is an easy, high-signal way to show we understood their actual system.

### Razorpay's own AI agent products (source of truth for "what good looks like" to this panel)
- **Agent Studio + Agentic Experience Platform launch (FTX'26, built on Claude Agent SDK):** https://razorpay.com/newsroom/razorpay-launches-the-worlds-first-ai-native-agent-studio-for-payments-at-ftx26-powered-by-anthropics-claude/
  - This is critical: Razorpay already ships an **Abandoned Cart Conversion Agent**, a **Dispute Responder Agent**, and a **Subscription Recovery Agent** (built with ElevenLabs). Our project should be positioned explicitly as complementary to / inspired by this suite, using consistent naming conventions (e.g. call our agents things like "Recovery Agent," "Diagnosis Agent" the same deliberate, plain-English way Razorpay names theirs — not generic ML jargon).
- **FTX'26 event hub (context on Razorpay's agentic commerce direction, partners, positioning):** https://razorpay.com/ftx26/
- **Razorpay Sprint 2026 product blueprint (RTO/return-risk scoring, dispute agents, AI-native payments messaging):** https://razorpay.com/sprint/26
  - Note the RTO/return-pattern-by-pincode language here — if there's time for a stretch feature, echoing this exact kind of segmented risk analysis (by pincode/product/customer) in our exception list or metrics would directly mirror something they've already built and validated as valuable.

### Ecosystem context (for accurate, credible framing — not to over-engineer against)
- **NPCI UAP context (agentic UPI, consent + spending-limit pattern):** referenced in prior research this session — search "NPCI Unified Agent Protocol UPI 2026" for latest if citing in the pitch. Use this only to correctly frame *why now* (mirrors the brief's own "why now" language) — do not attempt to integrate real UAP infra.

### How to use these sources while building
1. **Before writing `agents/diagnosis.py`'s rules-based classifier:** pull real failure/error codes from the Razorpay docs (test-mode section) so root-cause categories map to actual gateway language, not invented ones.
2. **Before writing `agents/recovery.py`'s intervention copy:** skim the Agent Studio launch page again and consciously echo their tone/framing (plain business English, not ML-speak) in any Claude-drafted messages.
3. **Before finalizing `docs/README.md` and the pitch script:** re-read the buildathon page's exact "the bar" text and make sure every sentence of our submission can be mapped back to a specific phrase in it (explainable, bounded, gated; measured money recovered; compliant escalation; stopping rules; audit trail; honest exception list).
4. **If time allows a stretch feature:** pull one idea directly from Razorpay Sprint 2026 (e.g. pincode-level return-risk segmentation) and adapt it into the Revenue Recovery context — this signals we did real homework on their product, not just the brief.

---

## 12. Current status

Nothing has been built yet in this repo — start from Section 9, step 1.
