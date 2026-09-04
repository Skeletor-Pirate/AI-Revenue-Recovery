# AI Revenue Recovery — Razorpay AI Buildathon, Track 03

[![CI](https://github.com/Space-Fighter/AI-Revenue-Recovery/actions/workflows/ci.yml/badge.svg)](https://github.com/Space-Fighter/AI-Revenue-Recovery/actions/workflows/ci.yml)

**Find revenue that's slipping away and win it back.** A four-agent pipeline that
detects revenue at risk, diagnoses the root cause, runs a **bounded** recovery
workflow, and writes every decision to an audit trail — with **measured money
recovered across a batch**, compliant escalation, stopping rules, and an honest
exception list.

**Razorpay test mode only. No real money, ever.**

---

## Quickstart

Prereqs: **Docker**, **Python 3.11+** with [`uv`](https://docs.astral.sh/uv/),
**Node 20+**.

```bash
# 1. database (PostgreSQL 17 + pgvector)
docker compose up -d

# 2. backend
cd backend
uv sync
cp .env.example .env                       # works as-is; add API keys for the full demo
uv run python -m app.pipeline --reset       # seed 74 events + fraud cluster, run all 4 agents
uv run uvicorn app.main:app --reload        # API + Swagger on http://localhost:8000/docs

# 3. dashboard (new terminal)
cd frontend
npm install
printf 'VITE_DATA_SOURCE=live\n' > .env.local
npm run dev                                 # http://localhost:5173
```

```bash
# 4. tests (run in two chunks — the pipeline suite is slow / memory-heavy)
cd backend
uv run pytest -q --ignore=tests/test_pipeline.py
uv run pytest -q tests/test_pipeline.py
# → 146 passed.  Frontend:  cd frontend && npm run build && npm run lint
```

CI runs exactly this on every push and PR — a `pgvector/pgvector:pg17` service
container, `uv sync --frozen`, both pytest chunks, and the frontend
build + lint ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). No API
keys — every LLM / embedding call is mocked in the suite.

Full run / test / deploy detail is in [**Running, testing & deploying**](#running-testing--deploying) below.

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

### Retrieval-augmented diagnosis (RAG)

When the rules classifier can't place a free-text failure reason, the Diagnosis
Agent doesn't just guess. It embeds the case, retrieves the nearest
**already-classified** cases from a pgvector knowledge base (`resolved_cases`,
HNSW index), and gives them to the LLM as few-shot examples. The knowledge base
is **curated and bounded** — near-duplicate inserts are skipped and each
`(root_cause, event_type)` bucket is capped, the same discipline as the stopping
rules. All vector search sits behind one function (`store.nearest_resolved_cases`),
so it lifts to a dedicated store if the curated KB ever outgrows one Postgres
instance. Retrieved cases also surface in the dashboard's decision-trail drawer.
RAG is optional: with no pgvector or no embeddings backend it's a no-op and
Diagnosis falls back to rules + LLM.

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

## Event sources

The pipeline is source-agnostic — it only reads `status` from the store. Two
sources feed it:

- **Synthetic batch** (`app/data/generate.py`) — a deterministic 74-event batch
  (real Razorpay failure codes) plus the seeded fraud cluster. The demo default.
- **Razorpay test-mode webhooks** (`app/webhooks/listener.py`, `POST
  /webhooks/razorpay`) — signed `payment.failed` / `payment_link.expired` /
  `invoice.expired` / `subscription.halted` deliveries are HMAC-SHA256-verified,
  mapped to an `EventCreate`, and inserted as `detected`. Set
  `RAZORPAY_WEBHOOK_SECRET`, expose `:8000` (e.g. `ngrok http 8000`), register
  the URL in the Razorpay **test-mode** dashboard. Idempotent; no PII stored.

## Architecture

```
synthetic batch ─▶ Detection ─▶ Diagnosis ─▶ Recovery ─▶ Audit
(app/data/generate.py)   │       │ (+ fraud     │ (+ stopping  │ (metrics +
                         │       │   triage,    │   rules)     │  exceptions)
                         │       │   + RAG)     │              │
                         ▼       ▼              ▼              ▼
        one Postgres store (pgvector) — backend/app/db/store.py — IS the audit trail
                         │              │
              FastAPI (app/api/*)   resolved_cases  ◀── app/rag.py (retrieve /
                         │          (HNSW index)          grow the knowledge base)
                         ▼
              React dashboard (frontend/)

        app/llm.py — one client for chat + embeddings (Anthropic / OpenRouter /
        OpenAI / local); every LLM/embedding call degrades to a deterministic
        fallback when unconfigured.
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

## Running, testing & deploying

### Configuration (`backend/.env`)

Copy `backend/.env.example`. **Everything runs with no keys set** — the LLM
falls back to deterministic templates and RAG uses a bundled local embedding
model. For the full demo:

| Key | Effect |
|---|---|
| `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | LLM classification of unrecognised failure reasons + drafted outreach copy. Auto-detected in that order (`app/llm.py`). OpenRouter's default model is a Claude model. |
| `OPENAI_API_KEY` | Also used for RAG embeddings (`text-embedding-3-small`); otherwise the local `fastembed` model (`all-MiniLM-L6-v2`, ~90 MB, downloads once). OpenRouter has no embeddings endpoint. |
| `RAZORPAY_WEBHOOK_SECRET` | Required only to accept live Razorpay test-mode webhooks. |
| `DATABASE_URL` | Defaults to the Docker container. |

### Run

```bash
docker compose up -d                            # repo root — Postgres 17 + pgvector on :5432

cd backend && uv sync
uv run python -m app.pipeline --reset            # seed + run all 4 agents → printed metrics
uv run python -m app.pipeline --reset --json     # same, raw MetricsBlock as JSON
uv run uvicorn app.main:app --reload             # API + Swagger on :8000/docs

cd ../frontend && npm install
npm run dev                                      # :5173 (fixtures mode — no backend needed)
printf 'VITE_DATA_SOURCE=live\n' > .env.local && npm run dev   # live mode (needs the API)
```

Dashboard routes: `/` KPIs + charts · `/queue` at-risk table → decision-trail
drawer (audit timeline + RAG "similar cases") · `/recovery` analytics ·
`/exceptions` fraud-cluster alert + honest exception list.

**No Docker?** `powershell -File scripts/pg.ps1 install` then `… start` runs an
embedded Postgres — everything works *except* RAG (no `vector` extension there).

**Live Razorpay webhooks:** set `RAZORPAY_WEBHOOK_SECRET`, run `ngrok http 8000`,
register `https://<id>.ngrok.io/webhooks/razorpay` in the Razorpay **test-mode**
dashboard, and trigger a test `payment.failed` — it flows into the same pipeline.

### Test

```bash
cd backend
uv run pytest -q --ignore=tests/test_pipeline.py   # 140 — fast
uv run pytest -q tests/test_pipeline.py            # 6 — reseeds the batch per test (~3 min)
# → 146 passed total. test_rag / test_api / 5 webhook tests need the pgvector container.

cd ../frontend
npm run build          # tsc -b && vite build
npm run lint           # oxlint
```

### Deploy

Not deployed for the buildathon (it's a local prototype), but it's a standard
containerisable stack:

| Component | Deploy shape |
|---|---|
| **Database** | Managed Postgres 16+ with the `vector` extension — Amazon **Aurora PostgreSQL** / RDS, Supabase, Neon, or the `pgvector/pgvector` image on any container host. Run `uv run python -m app.db.store` once to create the schema. |
| **Backend** | `uv run uvicorn app.main:app` (or `gunicorn -k uvicorn.workers.UvicornWorker`) behind a reverse proxy on any Python host — Fly.io, Render, Railway, ECS/Cloud Run. Set `DATABASE_URL`, `FRONTEND_ORIGIN`, and any LLM / `RAZORPAY_WEBHOOK_SECRET` keys as env vars. Stateless — scale horizontally. |
| **Frontend** | `npm run build` → static `frontend/dist/`, served from any CDN / static host (Vercel, Netlify, S3+CloudFront, GitHub Pages). Set `VITE_DATA_SOURCE=live` and `VITE_API_BASE_URL=https://<your-api>` at build time. |
| **Pipeline runs** | `uv run python -m app.pipeline` as a scheduled job (cron / ECS scheduled task), or drive it via `POST /api/pipeline/run`. |
| **Webhooks** | Point the Razorpay test-mode webhook at `https://<your-api>/webhooks/razorpay`; no tunnel needed once the API is public. |

CORS is locked to `FRONTEND_ORIGIN`; the webhook endpoint verifies every
signature; no secrets are committed.

### Example pipeline output (seed 42)

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
| 1 | **We believed Docker was unavailable** (early notes said Docker Desktop needs WSL2 and Win 11 Home can't run it); `winget`/EDB Postgres install returned 403. | Built `scripts/pg.ps1` — a self-contained PostgreSQL 17 from zonky's embedded-postgres binaries, no admin. *Later corrected — see #8.* |
| 2 | **`Decimal(<float>)` blew the `NUMERIC(14,2)` bound** — binary-float expansion produced 15+ digit amounts in the generator. | Quantise via `Decimal(str(round(value, 2)))` everywhere; money is `Decimal` end-to-end, quantised to paise. |
| 3 | **Fraud "tight time clustering" had nothing to test against** — the generator inserts the whole batch in one pass, so every `created_at` was identical and the ≤60-min clause was vacuous. | Added optional `EventCreate.created_at`; the generator now backdates the batch over 14 days and places the cluster in one 40-minute window. The full five-part signature is now real. |
| 4 | **Real Razorpay failure codes ≠ our first guesses** — we'd used `insufficient_funds` / `incorrect_otp`. | Verified against the test-card-details docs; switched to `insufficient_fund`, `authentication_failed`, `payment_timed_out`, `card_number_invalid`, etc. |
| 5 | **Parallel agent builds stomped the test DB** — five builders each running pytest, whose per-test fixture does `DROP TABLE`. | Each builder ran against a dedicated database (`revrec_test_diag` / `_rec` / `_aud`); merge + CI use `revrec_test`. |
| 6 | **Human-approval-gated events showed "no reason recorded"** in the exception list — the Audit agent only read `routed_to_exception` / `halted_stopping_rule`. | Audit now also derives the reason from the `awaiting_human_approval` payload (`proposed_action` + `threshold`). |
| 7 | **The full pytest run OOM-killed** a single process (batch-reseeding integration tests). | Documented running the suite in two chunks; a real fix (transaction-rollback fixtures / a smaller integration batch) is on the list below. |
| 8 | **The "Docker is unavailable" assumption (#1) was wrong.** When RAG needed pgvector we re-checked — Docker Desktop *was* installed and WSL2 works. | Switched Postgres to the `pgvector/pgvector` container so pgvector is native; kept `scripts/pg.ps1` as a no-Docker fallback (RAG disabled there). Lesson: re-verify environment assumptions when a new requirement appears. |
| 9 | **pgvector / hnswlib wouldn't install** on the embedded Postgres / without MS C++ Build Tools. | pgvector is a Postgres *extension*, not a pip package — solved by #8 (container image ships it). |
| 10 | **A `reset_db` table-ordering bug** surfaced when the new `resolved_cases` table joined the metadata — an explicit reversed drop list broke intermittently across the pipeline tests. | `reset_db` now passes `tables=None` (all, dependency-ordered) when pgvector is on; the explicit list is only used to *exclude* `resolved_cases` on the fallback path. |

## What we'd do next

- **Partial recovery** — today an attempt recovers the full amount or nothing.
- **Pincode / segment risk** in the exception list, echoing Razorpay Sprint 2026's
  RTO-by-pincode scoring.
- **Scale the RAG store** — pgvector on Aurora read replicas, then a dedicated
  vector store (Vespa / OpenSearch kNN) once the *curated* knowledge base
  crosses ~10M entries. One function changes (`store.nearest_resolved_cases`).
- **Faster tests** — transaction-rollback fixtures instead of `reset_db` per test;
  a 12-event integration batch.
- **Live LLM demo** — a recorded run with an LLM key set so the free-text
  fallback classification and drafted outreach copy are visible.
- **The liquid-glass refraction layer** on the dashboard chrome (currently a
  frosted `backdrop-blur` fallback).
