# Architecture — AI Revenue Recovery

Diagrams and design rationale. Companion to
[documentation.md](documentation.md) (exhaustive file/function reference) and
[CLAUDE.md](CLAUDE.md) (project brief).

> **Keep this current.** CLAUDE.md Section 13 requires this file to be updated in
> the same change that alters the architecture, data model, agent flow, or
> runtime topology.

Last updated: 2026-08-28 — after Section 9 step 2 (synthetic data generator);
datastore switched from Docker to a local PostgreSQL process.

---

## 1. One-paragraph summary

Synthetic (and later, real Razorpay test-mode webhook) events flow through four
sequential agents — **Detection → Diagnosis → Recovery → Audit** — that all read
and write one shared Postgres store via `app/db/store.py`. Every money-related
decision is written to the `audit_log` table *as it happens*; that table **is**
the audit trail. Recovery is root-cause-differentiated and bounded by explicit
stopping rules; a fraud-like cluster is deliberately re-classified as `flagged`
and left alone. A FastAPI backend exposes the store and the pipeline over REST;
a React dashboard renders the at-risk queue, per-case decision trails, recovery
metrics, and an honest exception list.

---

## 2. Agent pipeline

```mermaid
flowchart TD
    subgraph sources [Event sources]
        SYN[Synthetic batch<br/>app/data/generate.py]
        WH[Razorpay test-mode<br/>webhooks — stretch]
    end

    SYN --> DET
    WH -.-> DET

    subgraph agents [Agent pipeline]
        DET[Detection Agent<br/>flag at-risk revenue]
        DIA[Diagnosis Agent<br/>root cause + fraud triage]
        REC[Recovery Agent<br/>route intervention · enforce stopping rules]
        AUD[Audit / Reporting Agent<br/>metrics · exception list]
        DET --> DIA --> REC --> AUD
    end

    DET <--> STORE
    DIA <--> STORE
    REC <--> STORE
    AUD <--> STORE

    STORE[(Postgres<br/>events + audit_log<br/>via app/db/store.py)]

    AUD --> API
    STORE --> API
    API[FastAPI<br/>app/main.py + app/api/*] --> UI[React dashboard<br/>frontend/]

    classDef done fill:#d3f9d8,stroke:#2b8a3e;
    classDef todo fill:#fff3bf,stroke:#e67700;
    class SYN,STORE done;
    class DET,DIA,REC,AUD,API,UI,WH todo;
```

Green = built. Amber = not yet built.

---

## 3. Runtime topology

```mermaid
flowchart LR
    subgraph dev [Developer machine]
        subgraph fe [frontend/ — Vite dev server :5173]
            REACT[React 19 + Tailwind v4<br/>src/App.tsx]
            CLIENT[src/api/client.ts]
            REACT --> CLIENT
        end
        subgraph be [backend/ — uvicorn :8000]
            FASTAPI[FastAPI app<br/>app/main.py]
            STOREPY[app/db/store.py<br/>SQLModel + psycopg 3]
            FASTAPI --> STOREPY
        end
        subgraph localpg [Local PostgreSQL 17 — scripts/pg.ps1]
            PG[("postgres.exe :5432<br/>%LOCALAPPDATA%\revrec-pg")]
            DB1[(revrec)]
            DB2[(revrec_test)]
            PG --- DB1
            PG --- DB2
        end
    end

    CLIENT -- "/api, /health (Vite proxy)" --> FASTAPI
    STOREPY -- "postgresql+psycopg://…@localhost:5432" --> PG

    note["Docker path (docker-compose.yml) kept but inactive:<br/>Docker Desktop needs WSL2, not installed on this box"]
    localpg -.- note
```

---

## 4. Data model

```mermaid
erDiagram
    EVENTS ||--o{ AUDIT_LOG : "has decisions"

    EVENTS {
        text        event_id PK
        text        event_type "failed_payment | abandoned_checkout | overdue_invoice | expired_mandate"
        text        customer_id
        numeric     amount "14,2 — money at risk"
        text        currency "default INR"
        text        raw_failure_reason "nullable — gateway words pre-diagnosis"
        int         attempts_so_far "stopping-rule counter"
        int         days_overdue "B2B invoices"
        timestamptz created_at
        timestamptz updated_at
        text        status "detected → diagnosed → action_taken → recovered | exception | flagged"
        text        root_cause "nullable — set by Diagnosis"
        float       diagnosis_confidence "nullable 0..1"
        numeric     recovered_amount "14,2 — default 0"
    }

    AUDIT_LOG {
        bigint      id PK
        text        event_id FK
        text        agent "detection | diagnosis | recovery | triage | audit"
        text        action
        text        reasoning "human-readable WHY — never empty"
        jsonb       payload "nullable — drafted message / metrics"
        timestamptz timestamp
    }
```

---

## 5. Event lifecycle

```mermaid
stateDiagram-v2
    [*] --> detected : generator / Detection Agent
    detected --> diagnosed : Diagnosis Agent sets root_cause
    detected --> exception : obvious non-recoverable
    diagnosed --> flagged : Triage — fraud-like cluster (HALT)
    diagnosed --> action_taken : Recovery Agent runs an intervention
    action_taken --> recovered : money clawed back
    action_taken --> exception : stopping rule hit / gave up (with reason)
    action_taken --> action_taken : retry within limits (attempts_so_far++)
    recovered --> [*]
    exception --> [*]
    flagged --> [*]
```

`recovered` / `exception` / `flagged` are terminal. `exception` = honest
"couldn't recover, here's why"; `flagged` = deliberately stopped (suspected
abuse), never retried.

---

## 6. Request / data flow (target, once API + agents exist)

```mermaid
sequenceDiagram
    participant UI as React dashboard
    participant API as FastAPI
    participant PIPE as pipeline.py
    participant STORE as store.py
    participant PG as Postgres
    participant LLM as Claude (Anthropic)

    UI->>API: POST /api/pipeline/run
    API->>PIPE: run(batch)
    loop each event
        PIPE->>STORE: get_events_by_status("detected")
        PIPE->>STORE: update_event(... status="diagnosed", root_cause)
        PIPE->>STORE: log_action(agent="diagnosis", reasoning, payload)
        opt ambiguous free-text reason
            PIPE->>LLM: classify root cause
        end
        PIPE->>STORE: update_event(... status="recovered" | "exception" | "flagged")
        PIPE->>STORE: log_action(agent="recovery", reasoning)
        STORE->>PG: INSERT / UPDATE (committed per action)
    end
    PIPE->>STORE: all_events(), get_audit_trail()
    PIPE-->>API: metrics + exception list
    API-->>UI: JSON (₹ recovered by cause, recovery rate, exceptions)
```

---

## 7. Component responsibilities

| Component | File(s) | Responsibility | Must guarantee |
|---|---|---|---|
| Event store | `app/db/store.py` | single interface to Postgres; table + schema models; CRUD + audit | no raw SQL elsewhere; `log_action` is the only audit write; FK-checked; validated input |
| Synthetic generator | `app/data/generate.py` | deterministic 50–100 event batch + fraud cluster; real Razorpay failure codes | reproducible per seed; every record schema-valid; fraud cluster has a detectable shared signature |
| Detection Agent | `app/agents/detection.py` (todo) | flag genuinely at-risk events; route obvious non-recoverables to `exception` | one audit row per decision |
| Diagnosis Agent | `app/agents/diagnosis.py` (todo) | rules-first root-cause classification; Claude fallback for free-text; **fraud-cluster triage → `flagged`** | confidence recorded; triage reasoning explicit |
| Recovery Agent | `app/agents/recovery.py` (todo) | root-cause-specific intervention; draft outreach; **enforce stopping rules** (max attempts, max escalation, cooldown, amount gate) | bounded; refuses to act on `flagged`; human-approval flag above threshold |
| Audit / Reporting | `app/agents/audit.py` (todo) | roll up `audit_log` + `events` into metrics | computed over the full batch; exceptions never hidden |
| Pipeline | `app/pipeline.py` (todo) | wire agents 3–6 into one run | clean summary output |
| API | `app/main.py`, `app/api/*` (partial) | REST over store + pipeline | CORS to frontend only |
| Dashboard | `frontend/src/*` (shell) | at-risk queue, decision trail, charts, exception list | mirrors Razorpay's plain-English tone |
| Webhook listener | `app/webhooks/listener.py` (stretch) | ingest Razorpay test-mode events into Detection | signature-verified |

---

## 8. Key design decisions

| Decision | Choice | Why |
|---|---|---|
| Datastore | PostgreSQL 17, local process via `scripts/pg.ps1` (zonky embedded binaries) | owner preference for Postgres; real types (`NUMERIC`, `TIMESTAMPTZ`, `JSONB`); FK always on. Docker was the plan but needs WSL2 (absent on Win 11 Home); `winget`/EDB install 403'd — so a self-contained binary install, no admin |
| ORM / validation | SQLModel (SQLAlchemy 2 + Pydantic) | one model stack for tables *and* API request/response shapes |
| Validation split | thin table models + `*Create` / `*Update` / `*Read` schema models | `table=True` disables Pydantic validation; schema models restore it (`extra="forbid"`, bounds, `field_validator`s) and double as API contracts |
| Money type | `Decimal` / `NUMERIC(14,2)`, quantised to paise | no float rounding drift in financial figures |
| Audit trail | `audit_log` table, written via `log_action` only, `reasoning` NOT NULL | "the bar" demands explainable + auditable; make silent actions impossible |
| Backend deps | `uv` + `pyproject.toml` + committed `uv.lock` | fast, reproducible, no `requirements.txt` |
| App shape | FastAPI backend + React/Vite/Tailwind frontend monorepo | owner chose a real API + JS UI over the brief's single Streamlit app |
| Determinism | generator seeded (`random.Random(seed)` + `Faker.seed_instance`) | repeatable demo & tests; fraud cluster reproducible |
| Failure codes | real Razorpay error codes from `razorpay.com/docs/errors` | judged by Razorpay engineers — data must mirror their real system |
| Fraud handling | re-classify to `flagged`, Recovery refuses to act | the brief's required "one failure handled gracefully" moment |

---

## 9. Tech stack

| Layer | Tech |
|---|---|
| Language (backend) | Python 3.11 |
| Web framework | FastAPI + uvicorn |
| ORM / models | SQLModel · SQLAlchemy 2 · Pydantic 2 · pydantic-settings |
| DB driver | psycopg 3 (`postgresql+psycopg://`) |
| Database | PostgreSQL 17 — local process (`scripts/pg.ps1`); `docker-compose.yml` kept for Docker-capable machines |
| Data / synthetic | pandas · Faker |
| LLM | Anthropic Claude (`anthropic` SDK) — Diagnosis & Recovery, later |
| Payments | `razorpay` SDK — **test mode only**, later |
| Backend tooling | uv · pytest · httpx |
| Frontend | React 19 · Vite · TypeScript · Tailwind CSS v4 · Recharts |
| Frontend tooling | npm · oxlint |
