# Documentation — AI Revenue Recovery

Detailed reference for every file, class, function, endpoint, and command in the
repo. Companion to [architecture.md](architecture.md) (diagrams + design
rationale) and [CLAUDE.md](CLAUDE.md) (the project brief).

> **Keep this current.** Section 13 of CLAUDE.md requires `documentation.md` and
> `architecture.md` to be updated in the same change that adds/alters a file,
> function, class, endpoint, table, or command.

Last updated: 2026-09-04 — Phase B + C of the agent-team build: all four agent
modules built and merged (`detection` / `diagnosis` / `recovery` / `audit`,
build-order steps 3–6), `app/pipeline.py` chaining them (step 7), `app/api/*`
routers to the frozen contract mounted in `main.py` (step 8), `fixtures.json`
regenerated from a real seed-42 run, React dashboard pages built against it.
113 backend tests green. Prior: 2026-09-03 — Phase A + B0 of the agent-team
build (plan.md §9 steps 3–6 prep): added `RootCause` StrEnum to `store.py` and typed
`EventUpdate.root_cause`; added optional `EventCreate.created_at` (backdated
insert) and taught `insert_event` to honour it; corrected generator
`raw_failure_reason` codes to real Razorpay test-mode strings
(`insufficient_fund`, `authentication_failed`, `payment_timed_out`,
`card_number_invalid`) and spread the batch `created_at` over
`BATCH_SPAN_DAYS=14` with the fraud cluster in one `FRAUD_WINDOW_MINUTES=40`
window; froze `backend/app/agents/AGENTS_CONTRACT.md` (incl. §10 Phase-B0 Q&A
resolutions) and the API response contract (§5); added
`frontend/src/api/fixtures.json`. Prior: 2026-09-03 — merged the four
frontend skills (`scroll-craft`,
`liquid-glass`, `glass-scroll-3d`, `revrec-dashboard`) into one `frontend` skill
with four modes under `.claude/skills/frontend/<mode>/GUIDE.md` (§3.1); no
build-order step (tooling/process only). Prior: 2026-09-03 — added the
`build-workflow` project skill (§3.1), which absorbs and replaces
`razorpay-source-check` and adds a plan.md
check-out/update step. Prior:
2026-08-28 — after CLAUDE.md
Section 9 **step 2** (synthetic data generator) + switch to a local (non-Docker)
PostgreSQL. Build status table at the bottom.

---

## 1. What this is

A four-agent pipeline that detects revenue at risk (failed payments, abandoned
checkouts, overdue invoices, expired mandates), diagnoses the root cause,
executes a **bounded** recovery workflow, and writes every decision to an audit
trail. Submission for the Razorpay AI Buildathon, Track 03. **Test mode only —
no real money.**

Split into a Python/FastAPI **backend** and a React **frontend** (monorepo).

---

## 2. Repository map

```
RAZORPAY BUILDATHON/
├── CLAUDE.md                     project brief / source of truth
├── documentation.md              this file
├── architecture.md               diagrams + design decisions
├── docker-compose.yml            Postgres 16 container
├── .gitignore
├── scripts/
│   └── init-test-db.sql          creates the revrec_test database (once)
├── backend/
│   ├── pyproject.toml            Python deps (uv), pytest config
│   ├── uv.lock
│   ├── .env.example
│   └── app/
│       ├── main.py               FastAPI app
│       ├── config.py             typed settings (pydantic-settings)
│       ├── db/store.py           shared event store + schema models
│       ├── data/generate.py      synthetic batch generator
│       ├── agents/               detection · diagnosis · recovery · audit (empty)
│       ├── api/                  REST routers (empty)
│       └── webhooks/             Razorpay test-mode listener (empty)
│   └── tests/
│       ├── conftest.py           shared fixtures
│       ├── test_store.py         18 tests
│       └── test_generate.py      7 tests
└── frontend/
    ├── package.json              React 19 + Vite + Tailwind v4 + Recharts
    ├── vite.config.ts            dev server + /api,/health proxy → :8000
    └── src/
        ├── main.tsx              React entrypoint
        ├── App.tsx               app shell
        ├── index.css             @import "tailwindcss"
        └── api/client.ts         typed fetch wrapper
```

---

## 3. File reference

### 3.1 Root / infrastructure

| File | Purpose |
|---|---|
| `CLAUDE.md` | Project brief. Track, "the bar," architecture, tech-stack decisions, build order (Section 9), stopping rules (Section 6), data model (Section 5). Read first. |
| `documentation.md` | This file. |
| `architecture.md` | Mermaid diagrams (pipeline, ERD, lifecycle, runtime), component responsibilities, design-decision log. |
| `scripts/pg.ps1` | **Active Postgres path.** Manages a self-contained PostgreSQL 17 (zonky embedded-postgres binaries from Maven Central) under `%LOCALAPPDATA%\revrec-pg` — no Docker, no admin. Subcommands: `install` (download + `initdb` + create `revrec` & `revrec_test`), `start`, `stop`, `restart`, `status` (via `pg_ctl`). Port 5432, superuser `revrec`, `trust` auth (localhost dev only). |
| `docker-compose.yml` | **Inactive here** (Docker needs WSL2, not installed; Win 11 Home has no Hyper-V). Kept for machines where Docker works: service `db` = `postgres:16-alpine`, container `revrec_db`, creds `revrec`, port 5432, volume `revrec_pgdata`, healthcheck. |
| `scripts/init-test-db.sql` | Only used by the Docker path — `CREATE DATABASE revrec_test`. `pg.ps1 install` does the same for the local path. |
| `.gitignore` | Ignores `__pycache__`, `.venv`, `.pytest_cache`, `.env`, `node_modules`, `frontend/dist`, `**/data/synthetic_events.csv`. |
| `.claude/skills/` | Claude Code project skills (dev tooling, not shipped): `build-workflow` (mandatory per-task workflow wrapper: check out/update plan.md → verify against Razorpay sources (link list + how-to embedded in its Step 1) → keep architecture.md diagrams current → documentation.md → history.md brief; absorbed the former `razorpay-source-check` skill), `scroll-craft` + `liquid-glass` (vendored UI skills), `glass-scroll-3d` (composite scroll+R3F+glass, for the welcome page), `frontend` (one skill, four modes — `scroll-craft` + `liquid-glass` vendored UI guides, `glass-scroll-3d` composite scroll+R3F+glass for the welcome page, `revrec-dashboard` dashboard UI: tokens + chart catalog + data-table/timeline components, combines dataviz + liquid-glass; each mode is a `GUIDE.md` under `.claude/skills/frontend/<mode>/`). |
| `plan.md` | The project brief (formerly `CLAUDE.md`; renamed). `CLAUDE.md` is now a short operational guide pointing here. |

### 3.2 `backend/` — packaging & config

| File | Purpose |
|---|---|
| `pyproject.toml` | Project `razorpay-revenue-recovery-backend`, `requires-python >=3.11`. Deps: `anthropic`, `sqlmodel`, `psycopg[binary]`, `razorpay`, `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `pandas`, `python-dotenv`, `faker`. Dev: `pytest`, `httpx`. `[tool.uv] package = false`. `[tool.pytest.ini_options] pythonpath = ["."]` so `import app.*` works from `backend/`. |
| `uv.lock` | Resolved dependency lockfile (committed). |
| `.env.example` | Template for `backend/.env`. Keys below. |

**`backend/.env` keys**

| Key | Default | Used by |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://revrec:revrec@localhost:5432/revrec` | `store.get_engine`, `config.Settings` |
| `TEST_DATABASE_URL` | `…/revrec_test` | `tests/conftest.py` |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS in `main.py` |
| `ANTHROPIC_API_KEY` | — | Diagnosis / Recovery agents (later) |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | same |
| `RAZORPAY_KEY_ID` / `_KEY_SECRET` / `_WEBHOOK_SECRET` | — | webhook listener (later) |

### 3.3 `backend/app/` — application code

| File | Purpose | Status |
|---|---|---|
| `app/__init__.py` … `app/webhooks/__init__.py` | Package markers. `agents/`, `api/`, `webhooks/` are empty placeholders. | scaffold |
| `app/main.py` | FastAPI application. See §5. | minimal (health only) |
| `app/config.py` | `Settings` (pydantic-settings) + cached `get_settings()`. See §4. | done |
| `app/db/store.py` | The shared event store: table models, Pydantic schema models, engine/session helpers, CRUD + audit functions. See §6–§9. | **done, step 1** |
| `app/data/generate.py` | Deterministic synthetic batch generator incl. fraud cluster. See §10. | **done, step 2** |
| `app/agents/AGENTS_CONTRACT.md` | Frozen cross-agent contract: per-stage I/O, `RootCause`→intervention map, audit `action` registry, stopping-rule constants, fraud-cluster signature, audit `payload` shapes, Claude-usage rules, API response contract, file boundaries, §10 Phase-B0 Q&A resolutions. | **done** |
| `app/agents/__init__.py` | Imports the four stage modules; documents the uniform `run(session, *, settings=None) -> list[str]` entry point (audit also `compute_metrics`). | **done, step 7** |
| `app/agents/detection.py` | Detection Agent. `run(session, *, settings=None) -> list[str]` + pure `classify(event) -> (bool, str)`. Flags at-risk revenue (`flagged_at_risk`); routes obvious non-recoverables to `exception` (`routed_to_exception`). Idempotent. Details: `AGENTS_CONTRACT.md` §1/§3/§7. | **done, step 3** |
| `app/agents/diagnosis.py` | Diagnosis Agent. `run()`, pure `classify(event) -> (RootCause, conf, matched_reason, reasoning)`, `find_fraud_clusters(events)`, isolated `claude_classify(event, settings)`. Fraud triage first (→ `flagged`/`suspected_fraud`), then rules map, then Claude fallback when rules conf ≤ 0.5 on free text. Details: `AGENTS_CONTRACT.md` §2/§5/§6. | **done, step 4** |
| `app/agents/recovery.py` | Recovery Agent. `run()`, `draft_outreach(intervention, event, *, settings)`, `_stable_hash(event_id)`; constants `MAX_RETRY_ATTEMPTS=3`, `MAX_ESCALATION_STAGE=3`, `COOLDOWN_HOURS=24`, `HUMAN_APPROVAL_THRESHOLD_INR=Decimal("5000")`, `SUCCESS_RATES`, `HOURS_TO_RECOVERY`, `INTERVENTIONS`. Reads `diagnosed` only. Deterministic recovered/exception; human-approval gate logs + does not execute; escalation never past stage 3. Details: `AGENTS_CONTRACT.md` §4/§7/§10. | **done, step 5** |
| `app/agents/audit.py` | Audit Agent. `compute_metrics(session) -> dict` (the MetricsBlock — pure read, what the API returns) + `run(session, *, settings=None) -> list[str]` (writes one `batch_metrics` row on the earliest event). Full-batch metrics + complete honest exception list. Details: `AGENTS_CONTRACT.md` §7/§8. | **done, step 6** |
| `app/pipeline.py` | `run(database_url=None, *, settings=None) -> dict` chains Detection→Diagnosis→Recovery→Audit over the seeded batch and returns the MetricsBlock; argparse CLI (`--reset`, `--count`, `--seed`, `--json`) with a printed summary. | **done, step 7** |
| `app/api/__init__.py`, `app/api/routes.py` | REST routers to the frozen contract (`/api/events`, `/api/events/{id}/audit`, `/api/metrics`, `/api/pipeline/run`), mounted in `main.py`. See §5. | **done, step 8** |

### 3.4 `backend/tests/`

| File | Purpose |
|---|---|
| `conftest.py` | Fixtures: `_require_postgres` (session-scoped, non-autouse, `pytest.skip` if DB unreachable, 3s connect timeout), `test_database_url`, `session` (per-test `reset_db` + `Session`, depends on `_require_postgres`). |
| `test_store.py` | 18 tests — schema DDL, insert/update lifecycle, status queries, audit trail, FK enforcement, and the Pydantic schema layer (bounds, `extra="forbid"`, normalisation). 13 need Postgres, 5 don't. |
| `test_generate.py` | 7 tests — batch size/coverage, validity, amount & days_overdue bounds, no-gateway-reason invariants, determinism, fraud-cluster signature, and DB seeding (1 needs Postgres). |

### 3.5 `frontend/`

| File | Purpose |
|---|---|
| `package.json` | React 19, `react-dom`, `recharts`; dev: `@tailwindcss/vite`, `tailwindcss`, `vite`, `typescript`, `@vitejs/plugin-react`, `oxlint`, `@types/*`. Scripts: `dev`, `build` (`tsc -b && vite build`), `lint`, `preview`. |
| `vite.config.ts` | Plugins `react()`, `tailwindcss()`. Dev server on `5173`, proxies `/api` and `/health` → `http://localhost:8000`. |
| `src/index.css` | `@import "tailwindcss";` — Tailwind v4 single-line setup. |
| `src/main.tsx` | Mounts `<App/>` into `#root` under `<StrictMode>`. |
| `src/App.tsx` | Shell: header + a line that calls `api.health()` and shows the backend status. Placeholder for the dashboard pages. |
| `src/api/client.ts` | `request<T>()` fetch wrapper (JSON, throws on non-2xx). `api.health()`. Base URL from `VITE_API_BASE_URL` (blank in dev → proxy). |
| `src/api/fixtures.json` | Sample of every API response shape (`events`, `eventAudit`, `pipelineRun`, `metrics`) per `AGENTS_CONTRACT.md` §8. **Regenerated from a real seed-42 pipeline run** (74 events, 40 exceptions). Default data source until `VITE_DATA_SOURCE=live`. |
| `src/api/types.ts` | TypeScript types for the contract (`EventRead`, `AuditRead`, `MetricsBlock`, `ByRootCause`, `ByIntervention`, `ExceptionRow`, `FraudCluster`, response wrappers). |
| `src/api/dataSource.ts` | The adapter every page calls: `listEvents` / `getAuditTrail` / `getMetrics` / `runPipeline`. Reads `fixtures.json` by default; `VITE_DATA_SOURCE=live` routes to `client.ts` → `/api`. One env var, no component changes. |
| `src/api/actionLabels.ts` | Plain-business-English labels for agent / status / root cause / intervention / audit action / event type. |
| `src/api/client.ts` | fetch wrapper; now also `listEvents`, `getAuditTrail`, `getMetrics`, `runPipeline`. |
| `src/components/*` | `AppShell` (nav + theme toggle), `Card` / `GlassCard` (frosted `backdrop-blur`), `StatTile`, `StatusPill`, `DataTable`, `ChartCard` + `HBar` (Recharts bar only), `AuditTimeline` + `PayloadViewer`, `DetailDrawer`, `Feedback` (`Skeleton` / `EmptyState` / `ErrorState`). |
| `src/pages/*` | `Overview` (KPI tiles + 2 charts), `Queue` (at-risk table + deep-linkable `?case=` decision-trail drawer), `Recovery` (analytics), `Exceptions` (fraud-cluster alert card + honest exception table + CSV export). |
| `src/lib/*`, `src/charts/series.ts`, `src/hooks/useAsync.ts` | `format.ts` (Indian ₹ grouping), `csv.ts`, chart series colours, async loading hook. |
| `tsconfig.app.json` | + `resolveJsonModule` (fixtures import). `package.json` + `react-router-dom`. |
| `tsconfig*.json`, `.oxlintrc.json`, `index.html`, `public/*` | Vite/TS defaults. |
| `.env.example` | `VITE_API_BASE_URL=` (blank in dev). |

---

## 4. `app/config.py`

| Symbol | Kind | Notes |
|---|---|---|
| `Settings` | `pydantic_settings.BaseSettings` | `model_config`: `env_file=".env"`, `extra="ignore"`. Fields: `database_url`, `test_database_url`, `frontend_origin`, `anthropic_api_key: str \| None`, `anthropic_model`, `razorpay_key_id/secret/webhook_secret: str \| None`. Each read from the same-named env var (case-insensitive). |
| `get_settings()` | function, `@lru_cache` | Returns the process-wide `Settings` singleton. |

---

## 5. `app/main.py` — FastAPI app

| Symbol | Kind | Notes |
|---|---|---|
| `settings` | module global | `get_settings()` |
| `lifespan(app)` | async context manager | On startup calls `store.init_db(settings.database_url)` (CREATE TABLE IF NOT EXISTS). |
| `app` | `FastAPI` | `title="AI Revenue Recovery"`, `version="0.1.0"`, `lifespan=lifespan`. |
| CORS | middleware | `allow_origins=[settings.frontend_origin]`, all methods/headers. |
| `health()` | `GET /health` | `→ {"status": "ok"}` |

**Endpoint inventory**

| Method | Path | Handler | Response | Status |
|---|---|---|---|---|
| GET | `/health` | `health` | `{"status":"ok"}` | done |
| GET | `/docs`, `/redoc`, `/openapi.json` | FastAPI built-in | Swagger / ReDoc | done |
| GET | `/api/events` | `routes.list_events` | `{events: EventRead[], count: int}` | **done** |
| GET | `/api/events/{event_id}/audit` | `routes.event_audit` | `{event: EventRead, trail: AuditRead[]}` — 404 if unknown | **done** |
| POST | `/api/pipeline/run` | `routes.pipeline_run` | `{metrics: MetricsBlock, ran_at: str}` — query params `reset`, `count`, `seed` | **done** |
| GET | `/api/metrics` | `routes.get_metrics` | `MetricsBlock` (computed from current DB state) | **done** |

`main.py` mounts `app.api.router` (prefix `/api`). `EventRead` / `AuditRead`
serialise money as decimal strings (matches `fixtures.json`).

### 5.1 Frozen API response contract (Phase A)

Full shapes and the `MetricsBlock` definition live in
`backend/app/agents/AGENTS_CONTRACT.md` §8; a realistic sample of each is
committed at `frontend/src/api/fixtures.json` (keys `events`, `eventAudit`,
`pipelineRun`, `metrics`). `MetricsBlock` = the plan.md §7 metric block:
`total_at_risk`, `total_recovered`, `overall_recovery_rate`, `event_count`,
`by_root_cause[]`, `by_intervention[]` (each carries `at_risk` + `recovered`
decimal strings so ₹-recovered-by-intervention is available per plan.md §7),
`avg_hours_to_recovery`, `status_breakdown{}`, `exceptions[]` (complete honest
list — never truncated), `fraud_cluster{}`. All money fields are decimal
strings; rates are floats 0–1. Produced by `audit.compute_metrics(session)`;
`audit.run()` additionally writes the one `batch_metrics` audit row. Contract
Q&A resolutions are logged in `AGENTS_CONTRACT.md` §10.

---

## 6. `app/db/store.py` — controlled vocabulary (enums)

All are `enum.StrEnum` (members compare/serialize as plain strings).

| Enum | Members (value) |
|---|---|
| `EventType` | `failed_payment`, `abandoned_checkout`, `overdue_invoice`, `expired_mandate` |
| `EventStatus` | `detected` → `diagnosed` → `action_taken` → `recovered` \| `exception` \| `flagged` |
| `Agent` | `detection`, `diagnosis`, `recovery`, `triage`, `audit` |
| `RootCause` | `insufficient_funds`, `expired_instrument`, `bank_downtime`, `auth_failure`, `card_declined`, `checkout_abandoned`, `invoice_forgotten`, `suspected_fraud`, `unknown` — Diagnosis Agent output; one Recovery intervention each (see `backend/app/agents/AGENTS_CONTRACT.md` §2). DB column `root_cause` stays `str \| None`; the enum types `EventUpdate.root_cause`. |

---

## 7. `app/db/store.py` — table models (persistence)

### `Event` → table `events`

| Column | Type | Default | Notes |
|---|---|---|---|
| `event_id` | `str` | — | **PK** |
| `event_type` | `str` | — | indexed; one of `EventType` |
| `customer_id` | `str` | — | |
| `amount` | `Decimal` | — | `NUMERIC(14,2)` — money at risk |
| `currency` | `str` | `"INR"` | |
| `raw_failure_reason` | `str \| None` | `None` | gateway's words, pre-diagnosis |
| `attempts_so_far` | `int` | `0` | stopping-rule counter |
| `days_overdue` | `int` | `0` | for B2B invoices |
| `created_at` | `datetime` (tz-aware) | `_utcnow()` | `TIMESTAMPTZ` |
| `updated_at` | `datetime` (tz-aware) | `_utcnow()` | bumped on every `update_event` |
| `status` | `str` | `detected` | indexed; one of `EventStatus` |
| `root_cause` | `str \| None` | `None` | filled by Diagnosis Agent |
| `diagnosis_confidence` | `float \| None` | `None` | 0.0–1.0 |
| `recovered_amount` | `Decimal` | `0` | `NUMERIC(14,2)` |

### `AuditLog` → table `audit_log`

| Column | Type | Default | Notes |
|---|---|---|---|
| `id` | `int` | identity | **PK**, autoincrement |
| `event_id` | `str` | — | **FK → events.event_id**, indexed |
| `agent` | `str` | — | one of `Agent` |
| `action` | `str` | — | e.g. `classified_root_cause` |
| `reasoning` | `str` | — | human-readable justification (never empty) |
| `payload` | `dict \| None` | `None` | `JSONB` — round-trips as a dict |
| `timestamp` | `datetime` (tz-aware) | `_utcnow()` | `TIMESTAMPTZ` |

---

## 8. `app/db/store.py` — Pydantic schema models (validation + API shapes)

Shared config `_STRICT = ConfigDict(extra="forbid", use_enum_values=True, validate_default=True)`.

| Model | Base | Role | Key rules |
|---|---|---|---|
| `EventCreate` | `SQLModel` | input to `insert_event`; future `POST /api/events` body | `event_id`/`customer_id` `min_length=1`; `event_type: EventType`; `amount` `gt=0`, `max_digits=14`; `currency` exactly 3 chars → upper-cased; `attempts_so_far`/`days_overdue` `ge=0`; `status: EventStatus = detected`; `created_at: datetime \| None = None` (optional backdate — the synthetic generator sets it; omitted → table default `_utcnow`). Validators: `_upper` (currency), `_round_money` (quantise amount to `0.01`). |
| `EventUpdate` | `SQLModel` | partial patch for `update_event` | every field `Optional`; `extra="forbid"` rejects unknown keys; `root_cause: RootCause \| None` (bad value → `ValidationError`); `diagnosis_confidence` `ge=0,le=1`; `recovered_amount` `ge=0`; money quantised. Consumed via `model_dump(exclude_unset=True)`. |
| `EventRead` | `SQLModel` | response shape for `GET /api/events` | mirrors all `events` columns. |
| `AuditCreate` | `SQLModel` | input to `log_action` | `event_id`/`action`/`reasoning` `min_length=1`; `agent: Agent`; `payload: dict \| None`. |
| `AuditRead` | `SQLModel` | response shape for audit endpoints | mirrors all `audit_log` columns. |

`ValidationError` is a subclass of `ValueError`.

---

## 9. `app/db/store.py` — functions

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `_utcnow()` | `() -> datetime` | tz-aware UTC now | private |
| `get_engine(database_url=None)` | | SQLAlchemy `Engine` | caches a singleton for the default URL; an explicit URL returns a fresh engine |
| `get_session(database_url=None)` | | `Session` | new unit-of-work |
| `init_db(database_url=None)` | | `None` | `SQLModel.metadata.create_all` — idempotent |
| `reset_db(database_url=None)` | | `None` | `drop_all` + `create_all` — fresh slate |
| `insert_event(session, data=None, /, **kwargs)` | `data: EventCreate \| None` | `Event` | validates via `EventCreate` (pre-built or from kwargs), `add`+`commit`+`refresh`. If `created_at` is supplied it is honoured and `updated_at` is set to match; otherwise both fall to the table default. |
| `update_event(session, event_id, data=None, /, **fields)` | `data: EventUpdate \| None` | `Event` | `model_dump(exclude_unset=True)` → `setattr` each; bumps `updated_at`; missing event → `KeyError`; unknown key / bad value → `ValidationError` |
| `get_event(session, event_id)` | | `Event \| None` | by PK |
| `get_events_by_status(session, status)` | `status: str \| Iterable[str]` | `list[Event]` | `WHERE status IN (...)`, ordered by `created_at` |
| `all_events(session)` | | `list[Event]` | ordered by `created_at` |
| `log_action(session, data=None, /, **kwargs)` | `data: AuditCreate \| None` | `int` (new row id) | the ONLY way to write the audit trail; FK-checked (phantom `event_id` → `IntegrityError`) |
| `get_audit_trail(session, event_id=None)` | | `list[AuditLog]` | whole batch or one event, ordered by `id` |
| `main` (`__name__=="__main__"`) | | | `init_db()` + print |

Module constants: `DEFAULT_DATABASE_URL`, `MONEY = Decimal("0.01")`.

---

## 10. `app/data/generate.py` — synthetic data generator

**Constants**

| Name | Value / meaning |
|---|---|
| `RAZORPAY_FAILURE_REASONS` | `dict[EventType, list[str\|None]]` — real Razorpay test-mode failed-card-payment codes (verified 2026-09-03 against `razorpay.com/docs/payments/payments/test-card-details`): `insufficient_fund`, `card_expired`, `authentication_failed`, `payment_timed_out`, `card_declined`, `card_number_invalid`, `bank_not_available`, `gateway_technical_error` (failed_payment); `mandate_creation_expired/failed` (expired_mandate); `None` for abandoned_checkout & overdue_invoice |
| `_TYPE_WEIGHTS` | 45 % failed_payment, 20 % abandoned_checkout, 20 % overdue_invoice, 15 % expired_mandate |
| `_MIN_AMOUNT` / `_MAX_AMOUNT` | `₹200` / `₹50000` |
| `CSV_PATH` | `backend/data/synthetic_events.csv` |
| `FRAUD_REASON` | `card_declined` |
| `FRAUD_AMOUNT_LOW` / `_HIGH` | `₹4980` / `₹5020` |
| `FRAUD_ID_PREFIX` | `fraud_` |
| `BATCH_SPAN_DAYS` | `14` — batch `created_at` is backdated/spread over this window (gives the Diagnosis fraud check a real time axis) |
| `FRAUD_WINDOW_MINUTES` | `40` — the seeded fraud cluster falls inside one sub-60-minute window |
| `FRAUD_DAYS_AGO` | `3` — where in the span the cluster sits |

**Functions**

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `_money(value)` | `float -> Decimal` | 2-dp Decimal via `str()` (avoids float-binary expansion that breaks `max_digits`) |
| `_rupees(rng)` | `random.Random -> Decimal` | log-normal amount, clamped to [200, 50000] |
| `_pick_type(rng)` | `-> EventType` | weighted choice |
| `_epoch()` | `-> datetime` | tz-aware "now" the backdating span ends at; taken once per build |
| `build_batch(count=70, seed=42)` | `-> list[EventCreate]` | deterministic (ids/amounts/types); each record a validated `EventCreate`; `event_id = evt_NNN`; `created_at` spread over `BATCH_SPAN_DAYS` |
| `build_fraud_cluster(size=4, seed=42)` | `-> list[EventCreate]` | all `failed_payment`, same `card_declined` reason, amounts in a ±₹40 band, `attempts_so_far` 2–3, distinct customers, all `created_at` inside one `FRAUD_WINDOW_MINUTES` window ~`FRAUD_DAYS_AGO` back, `event_id = fraud_NN` |
| `_to_frame(records)` | `-> pandas.DataFrame` | one row per record (`model_dump`) |
| `generate(count=70, seed=42, reset=True, database_url=None)` | `-> list[str]` | build batch + cluster, optional `reset_db`, `insert_event` each, write CSV, return event_ids |
| `_summary(records)` | `-> str` | count-by-type + total ₹ + fraud ids |
| `main()` | | | argparse CLI: `--count`, `--seed`, `--reset/--no-reset` |

**CLI**

```
uv run python -m app.data.generate --count 70 --seed 42 --reset
```
Example output: `74 events, total at risk Rs 199,558.65` + per-type counts +
`fraud cluster: ['fraud_00','fraud_01','fraud_02','fraud_03']`.

---

## 11. Commands / runbook

| Task | Command | From |
|---|---|---|
| One-time Postgres setup | `powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 install` | repo root |
| Start Postgres | `powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 start` | repo root |
| Stop Postgres | `powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 stop` | repo root |
| Postgres status | `powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 status` | repo root |
| _(Docker alternative)_ | `docker compose up -d` (only where Docker/WSL2 works) | repo root |
| Install backend deps | `uv sync` | `backend/` |
| Create local env | `cp .env.example .env` | `backend/` |
| Init schema manually | `uv run python -m app.db.store` | `backend/` |
| Seed synthetic batch | `uv run python -m app.data.generate --reset` | `backend/` |
| Run the full pipeline | `uv run python -m app.pipeline --reset` (add `--json` for the raw MetricsBlock) | `backend/` |
| Run API (dev) | `uv run uvicorn app.main:app --reload` → `:8000/docs` | `backend/` |
| Dashboard against live API | set `VITE_DATA_SOURCE=live` in `frontend/.env`, run backend + `npm run dev` | `frontend/` |
| Run tests | `uv run pytest -q` | `backend/` |
| Install frontend deps | `npm install` | `frontend/` |
| Run frontend (dev) | `npm run dev` → `:5173` | `frontend/` |
| Build frontend | `npm run build` | `frontend/` |

---

## 12. Test inventory

| Suite | Count | Needs Postgres | Covers |
|---|---|---|---|
| `test_store.py` — DDL & CRUD | 15 | yes (skip if down) | tables exist, insert/read defaults, update lifecycle + `updated_at`, backdated `created_at` insert, `RootCause` enum enforcement on `update_event`, status queries (single/multi), audit trail + JSONB round-trip, FK enforcement, `reset_db` |
| `test_store.py` — schema layer | 5 | no | `EventCreate` bounds & rejections, normalisation (currency upper, money quantise, enum→str), `EventUpdate` `extra="forbid"` + confidence bound, `AuditCreate` non-empty reasoning, prebuilt-schema path |
| `test_generate.py` | 9 | 1 of 9 | size/coverage, validity, amount & days_overdue bounds, no-gateway-reason invariant, `created_at` spread over the span, fraud cluster inside one sub-60-min window, determinism, fraud-cluster signature, DB seeding |
| `test_detection.py` | 12 | yes | `classify` verdict table, flag vs route-to-exception, net `amount_at_risk`, idempotency, only-touches-detected |
| `test_diagnosis.py` | 26 | yes | rules map (12 params), event-type fallback, low-confidence → Claude (monkeypatched), no-key degrade, fraud-cluster flag + signature, ordinary same-reason not flagged, idempotency |
| `test_recovery.py` | 29 | yes | per-intervention routes (7 params), salary-window retry, bank backoff, max-attempts halt, escalation cap (never stage 4), human-approval gate (executed vs not, boundary), cooldown delay, suspected-fraud refusal, never-reads-flagged, template + Claude draft, idempotency |
| `test_audit.py` | 11 | yes (dedicated `revrec_test_aud`) | totals + money-based overall rate, all-six status keys, by-root-cause enum order, by-intervention `at_risk`/`recovered`, avg hours, complete exception list + all reason-derivation paths, fraud cluster, determinism, one `batch_metrics` row, no event mutation, empty batch |
| `test_pipeline.py` | 6 | yes | reset→generate→run: every event terminal, fraud cluster `flagged` + not recovered, metrics over full batch, exception list populated with reasons, rerunnable/stable, `batch_metrics` row written |

Current run against the local Postgres: **113 passed** (`uv run pytest -q` from
`backend/`; run in chunks on low-RAM boxes — the full suite plus the
batch-reseeding pipeline tests can OOM a single process). With Postgres
stopped: ~15 passed, the rest skipped.

---

## 13. Build status vs CLAUDE.md Section 9

| Step | Deliverable | Status |
|---|---|---|
| 1 | `store.py` shared event store | ✅ done (SQLModel + Pydantic on Postgres) |
| 2 | synthetic data generator | ✅ done (`app/data/generate.py`) |
| 3 | `agents/detection.py` | ✅ done (12 tests) |
| 4 | `agents/diagnosis.py` (+ fraud triage) | ✅ done (26 tests) |
| 5 | `agents/recovery.py` (+ stopping rules) | ✅ done (29 tests) |
| 6 | `agents/audit.py` (metrics) | ✅ done (11 tests) |
| 7 | `pipeline.py` (single entrypoint) | ✅ done (`app/pipeline.py` + 6 integration tests) |
| 8 | dashboard | 🟡 built against fixtures (`frontend/src/pages/*`), + `app/api/*` live; browser end-to-end pass pending |
| 9 | `webhooks/listener.py` | ⬜ (stretch) |
| 10 | `README.md` + `architecture.md` "what broke" | 🟡 architecture.md started |

**Deviations from CLAUDE.md** (approved by the owner): Postgres instead of
SQLite; `uv` instead of `pip`/`requirements.txt`; a FastAPI + React monorepo
(`backend/`, `frontend/`) instead of a single Streamlit app — Streamlit dropped.

---

## 14. Known issues / notes

- **Postgres is a local process, not a service** — it does not auto-start on
  login. Run `scripts\pg.ps1 start` at the beginning of a work session
  (`status` to check). Data lives in `%LOCALAPPDATA%\revrec-pg\data` and
  persists across restarts.
- Docker path is unavailable on this machine (Docker Desktop needs WSL2; not
  installed; Win 11 Home has no Hyper-V). `winget` install of PostgreSQL also
  failed here (EDB CDN returned 403). Hence the zonky-binaries approach in
  `scripts/pg.ps1`.
- `trust` auth: any localhost client can connect as `revrec` with no password.
  Fine for local dev; never expose port 5432.
- A stale `VIRTUAL_ENV` env var may point at a deleted root `.venv` — harmless,
  `uv` ignores it.
- Schema changes are not migrated (no Alembic yet); `reset_db` (or
  `pg.ps1`-nothing / just re-run the generator with `--reset`) is the current
  way to apply model changes.
- `tests/test_store.py::test_reset_db_clears_everything` must `session.close()`
  before `reset_db` — on Postgres a live session's locks block `DROP TABLE`.
