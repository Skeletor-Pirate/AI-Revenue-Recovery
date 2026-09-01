# Documentation — AI Revenue Recovery

Detailed reference for every file, class, function, endpoint, and command in the
repo. Companion to [architecture.md](architecture.md) (diagrams + design
rationale) and [CLAUDE.md](CLAUDE.md) (the project brief).

> **Keep this current.** Section 13 of CLAUDE.md requires `documentation.md` and
> `architecture.md` to be updated in the same change that adds/alters a file,
> function, class, endpoint, table, or command.

Last updated: 2026-08-28 — after CLAUDE.md Section 9 **step 2** (synthetic data
generator) + switch to a local (non-Docker) PostgreSQL. Build status table at
the bottom.

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
| — | `/api/events`, `/api/pipeline/run`, `/api/metrics`, `/api/events/{id}/audit` | — | — | **planned** (routers not built) |

---

## 6. `app/db/store.py` — controlled vocabulary (enums)

All are `enum.StrEnum` (members compare/serialize as plain strings).

| Enum | Members (value) |
|---|---|
| `EventType` | `failed_payment`, `abandoned_checkout`, `overdue_invoice`, `expired_mandate` |
| `EventStatus` | `detected` → `diagnosed` → `action_taken` → `recovered` \| `exception` \| `flagged` |
| `Agent` | `detection`, `diagnosis`, `recovery`, `triage`, `audit` |

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
| `EventCreate` | `SQLModel` | input to `insert_event`; future `POST /api/events` body | `event_id`/`customer_id` `min_length=1`; `event_type: EventType`; `amount` `gt=0`, `max_digits=14`; `currency` exactly 3 chars → upper-cased; `attempts_so_far`/`days_overdue` `ge=0`; `status: EventStatus = detected`. Validators: `_upper` (currency), `_round_money` (quantise amount to `0.01`). |
| `EventUpdate` | `SQLModel` | partial patch for `update_event` | every field `Optional`; `extra="forbid"` rejects unknown keys; `diagnosis_confidence` `ge=0,le=1`; `recovered_amount` `ge=0`; money quantised. Consumed via `model_dump(exclude_unset=True)`. |
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
| `insert_event(session, data=None, /, **kwargs)` | `data: EventCreate \| None` | `Event` | validates via `EventCreate` (pre-built or from kwargs), `add`+`commit`+`refresh` |
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
| `RAZORPAY_FAILURE_REASONS` | `dict[EventType, list[str\|None]]` — real Razorpay error codes: `insufficient_funds`, `card_expired`, `incorrect_otp`, `card_declined`, `bank_not_available`, `bank_technical_error`, `gateway_technical_error` (failed_payment); `mandate_creation_expired/failed` (expired_mandate); `None` for abandoned_checkout & overdue_invoice |
| `_TYPE_WEIGHTS` | 45 % failed_payment, 20 % abandoned_checkout, 20 % overdue_invoice, 15 % expired_mandate |
| `_MIN_AMOUNT` / `_MAX_AMOUNT` | `₹200` / `₹50000` |
| `CSV_PATH` | `backend/data/synthetic_events.csv` |
| `FRAUD_REASON` | `card_declined` |
| `FRAUD_AMOUNT_LOW` / `_HIGH` | `₹4980` / `₹5020` |
| `FRAUD_ID_PREFIX` | `fraud_` |

**Functions**

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `_money(value)` | `float -> Decimal` | 2-dp Decimal via `str()` (avoids float-binary expansion that breaks `max_digits`) |
| `_rupees(rng)` | `random.Random -> Decimal` | log-normal amount, clamped to [200, 50000] |
| `_pick_type(rng)` | `-> EventType` | weighted choice |
| `build_batch(count=70, seed=42)` | `-> list[EventCreate]` | deterministic; each record is a validated `EventCreate`; `event_id = evt_NNN` |
| `build_fraud_cluster(size=4, seed=42)` | `-> list[EventCreate]` | all `failed_payment`, same `card_declined` reason, amounts in a ±₹40 band, `attempts_so_far` 2–3, distinct customers, `event_id = fraud_NN` |
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
| Run API (dev) | `uv run uvicorn app.main:app --reload` → `:8000/docs` | `backend/` |
| Run tests | `uv run pytest -q` | `backend/` |
| Install frontend deps | `npm install` | `frontend/` |
| Run frontend (dev) | `npm run dev` → `:5173` | `frontend/` |
| Build frontend | `npm run build` | `frontend/` |

---

## 12. Test inventory

| Suite | Count | Needs Postgres | Covers |
|---|---|---|---|
| `test_store.py` — DDL & CRUD | 13 | yes (skip if down) | tables exist, insert/read defaults, update lifecycle + `updated_at`, status queries (single/multi), audit trail + JSONB round-trip, FK enforcement, `reset_db` |
| `test_store.py` — schema layer | 5 | no | `EventCreate` bounds & rejections, normalisation (currency upper, money quantise, enum→str), `EventUpdate` `extra="forbid"` + confidence bound, `AuditCreate` non-empty reasoning, prebuilt-schema path |
| `test_generate.py` | 7 | 1 of 7 | size/coverage, validity, amount & days_overdue bounds, no-gateway-reason invariant, determinism, fraud-cluster signature, DB seeding |

Current run against the local Postgres: **25 passed** (`uv run pytest -q` from
`backend/`). With Postgres stopped: 10 passed, 15 skipped.

---

## 13. Build status vs CLAUDE.md Section 9

| Step | Deliverable | Status |
|---|---|---|
| 1 | `store.py` shared event store | ✅ done (SQLModel + Pydantic on Postgres) |
| 2 | synthetic data generator | ✅ done (`app/data/generate.py`) |
| 3 | `agents/detection.py` | ⬜ next |
| 4 | `agents/diagnosis.py` (+ fraud triage) | ⬜ |
| 5 | `agents/recovery.py` (+ stopping rules) | ⬜ |
| 6 | `agents/audit.py` (metrics) | ⬜ |
| 7 | `pipeline.py` (single entrypoint) | ⬜ |
| 8 | dashboard | ⬜ (React frontend — shell only) |
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
