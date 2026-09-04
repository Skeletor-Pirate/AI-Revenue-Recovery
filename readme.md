# AI Revenue Recovery — Razorpay AI Buildathon, Track 03

**Find revenue that's slipping away and win it back.** A four-agent pipeline that
detects revenue at risk, diagnoses the root cause, runs a **bounded** recovery
workflow, and writes every decision to an audit trail — with **measured money
recovered across a batch**, compliant escalation, stopping rules, and an honest
exception list.

**Razorpay test mode only. No real money, ever.**

---

## The bar, and where we meet it

> *"Don't just identify the problem. Show measured money recovered across a
> batch, with compliant escalation, stopping rules, and an audit trail."*

| The bar asks for | Where it lives |
|---|---|
| **Measured money recovered across a batch** | `app/agents/audit.py` → `compute_metrics()`; `GET /api/metrics`; dashboard Overview. Computed over the **full batch**, exceptions included. |
| **Determines the right intervention** (not a generic retry) | `app/agents/diagnosis.py` maps each failure to one of 9 root causes; `app/agents/recovery.py` routes each to a **different** intervention (see table below). |
| **Bounded recovery workflow / stopping rules** | `recovery.py`: `MAX_RETRY_ATTEMPTS=3`, `MAX_ESCALATION_STAGE=3`, 24h cooldown, ₹5,000 human-approval gate. |
| **Compliant escalation** | Overdue invoices: reminder → formal notice → human handoff. Never further — no auto-legal, no auto-suspension. |
| **Audit trail** | Every agent action is a row in `audit_log`, written via the single `store.log_action()`; `reasoning` is never empty. `GET /api/events/{id}/audit`. |
| **Honest exception list** | Every non-recovered case with its stated reason — never truncated, never cherry-picked. Dashboard `/exceptions`. |
| **One failure handled gracefully** | The fraud-cluster triage halt (below). |

---

## Root cause → intervention

| Root cause | Trigger (real Razorpay test-mode codes) | Intervention |
|---|---|---|
| `insufficient_funds` | `insufficient_fund` | Wait, retry near the next salary-credit window |
| `expired_instrument` | `card_expired`, `card_number_invalid`, `mandate_creation_*` | Send a re-authorization / re-mandate link |
| `bank_downtime` | `bank_not_available`, `gateway_technical_error` | Suggest an alternate payment method + short retry |
| `auth_failure` | `authentication_failed`, `payment_timed_out` | Prompt a fresh guided retry |
| `card_declined` | `card_declined`, `card_disabled_for_online_payments` | One cautious retry, then exception |
| `checkout_abandoned` | abandoned checkout (no gateway error) | Personalized nudge; bounded discount only **above** the human-approval gate |
| `invoice_forgotten` | overdue invoice | Escalation ladder: reminder → notice → human handoff |
| `suspected_fraud` | set by triage only | **Recovery refuses to act** |
| `unknown` | classifier + LLM both low-confidence | Honest exception |

Failure codes were verified against Razorpay's
[test-card details](https://razorpay.com/docs/payments/payments/test-card-details).
Agent names and outreach tone mirror Razorpay's own Agent Studio agents
(Abandoned Cart Conversion, Subscription Recovery, Dispute Responder).

---

## The fraud-cluster triage halt — "one failure handled gracefully"

The synthetic generator seeds a deliberate cluster (`fraud_00..03`): four failed
payments, identical `card_declined` reason, amounts in a ₹4,980–5,020 band, four
distinct customers, all inside one ~40-minute window, each already retried 2–3
times.

A naive pipeline would keep retrying them. Ours doesn't: the Diagnosis Agent's
**triage runs before per-event classification**, matches the five-part signature
(identical reason · ±₹50 band · ≥3 distinct customers · ≤60-min window ·
`attempts_so_far ≥ 2`), re-classifies the whole cluster to `flagged` /
`suspected_fraud`, and logs `halted_fraud_cluster` with the matched signature.
The Recovery Agent only ever reads `diagnosed` events, so it never touches them.
The dashboard surfaces this as a red alert card on `/exceptions`.

---

## Architecture

```
synthetic batch ─▶ Detection ─▶ Diagnosis ─▶ Recovery ─▶ Audit
(app/data/generate.py)   │          │ (+ fraud    │ (+ stopping   │ (metrics +
                         │          │   triage)   │   rules)      │  exceptions)
                         ▼          ▼             ▼               ▼
              one Postgres store — backend/app/db/store.py — IS the audit trail
                         │
              FastAPI (app/api/*) ─▶ React dashboard (frontend/)
```

- **`store.py` is the only interface to Postgres.** No raw SQL anywhere else.
  `log_action()` is the only audit write.
- **Event lifecycle:** `detected → diagnosed → action_taken → recovered |
  exception | flagged`. `exception` = honest "couldn't recover, here's why";
  `flagged` = deliberately halted, never retried.
- **Deterministic** — the generator is seeded and the recovery outcome is
  `sha256(event_id) % 100 < p` against a per-intervention success rate, so the
  demo and the tests are stable, not random.
- Full detail: [`architecture.md`](architecture.md) ·
  [`documentation.md`](documentation.md) ·
  [`backend/app/agents/AGENTS_CONTRACT.md`](backend/app/agents/AGENTS_CONTRACT.md).

---

## Run it

Postgres is a local process (Docker needs WSL2, absent on the build machine).

```powershell
# repo root — once, then start each session
powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 install
powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 start
```

```bash
# backend/ (uv, Python 3.11+)
uv sync
cp .env.example .env
uv run python -m app.data.generate --reset     # seed 74 events + the fraud cluster
uv run python -m app.pipeline                   # run all four agents → printed metrics
uv run pytest -q                                # 113 tests (run in chunks on low-RAM boxes)
uv run uvicorn app.main:app --reload            # API on :8000/docs
```

```bash
# frontend/ (React 19 + Vite + Tailwind v4 + Recharts)
npm install
npm run dev                                      # :5173 — renders the bundled sample run
# live data: set VITE_DATA_SOURCE=live in frontend/.env, with the backend running
```

`ANTHROPIC_API_KEY` is optional — Diagnosis falls back to `unknown` and Recovery
uses plain-English templates when it is absent, so everything runs offline.

### Example output (`uv run python -m app.pipeline`, seed 42)

```
events in batch         : 74
total at risk           : Rs 184,480.53
total recovered         : Rs 79,558.16   (43%)
fraud cluster halted    : ['fraud_00','fraud_01','fraud_02','fraud_03']
exception list (40 — honest, not cherry-picked): …
```

---

## What broke, and how we fixed it

| # | What broke | Fix |
|---|---|---|
| 1 | **Docker was unavailable** (Docker Desktop needs WSL2; Win 11 Home has no Hyper-V). `winget`/EDB Postgres install returned 403. | `scripts/pg.ps1` — a self-contained PostgreSQL 17 from zonky's embedded-postgres binaries under `%LOCALAPPDATA%`, no admin, no service. |
| 2 | **`Decimal(<float>)` blew the `NUMERIC(14,2)` bound** — binary-float expansion produced 15+ digit amounts in the generator. | Quantise via `Decimal(str(round(value, 2)))` everywhere; money is `Decimal` end-to-end, quantised to paise. |
| 3 | **Fraud "tight time clustering" had nothing to test against** — the generator inserts the whole batch in one pass, so every `created_at` was identical and the ≤60-min clause was vacuous. | Added optional `EventCreate.created_at`; the generator now backdates the batch over 14 days and places the cluster in one 40-minute window. The full five-part signature is now real. |
| 4 | **Real Razorpay failure codes ≠ our first guesses** — we'd used `insufficient_funds` / `incorrect_otp`. | Verified against the test-card-details docs; switched to `insufficient_fund`, `authentication_failed`, `payment_timed_out`, `card_number_invalid`, etc. |
| 5 | **Parallel agent builds stomped the test DB** — five builders each running pytest, whose per-test fixture does `DROP TABLE`. | Each builder ran against a dedicated database (`revrec_test_diag` / `_rec` / `_aud`); merge + CI use `revrec_test`. |
| 6 | **Human-approval-gated events showed "no reason recorded"** in the exception list — the Audit agent only read `routed_to_exception` / `halted_stopping_rule`. | Audit now also derives the reason from the `awaiting_human_approval` payload (`proposed_action` + `threshold`). |
| 7 | **The full pytest run OOM-killed** a single process (batch-reseeding integration tests). | Documented running the suite in two chunks; a real fix (transaction-rollback fixtures / a smaller integration batch) is on the list below. |

## What we'd do next

- **Real Razorpay test-mode webhooks** (`payment.failed`, `subscription.charged`)
  into Detection, replacing pure synthetic replay — the pipeline is already
  source-agnostic.
- **Partial recovery** — today an attempt recovers the full amount or nothing.
- **Pincode / segment risk** in the exception list, echoing Razorpay Sprint 2026's
  RTO-by-pincode scoring.
- **Faster tests** — transaction-rollback fixtures instead of `reset_db` per test;
  a 12-event integration batch.
- **Live LLM demo** — a recorded run with `ANTHROPIC_API_KEY` set so the Claude
  fallback classification and drafted outreach copy are visible.
- **The liquid-glass refraction layer** on the dashboard chrome (currently a
  frosted `backdrop-blur` fallback).
