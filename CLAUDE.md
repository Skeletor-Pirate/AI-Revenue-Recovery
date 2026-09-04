# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Submission for the **Razorpay AI Buildathon, Track 03: AI Revenue Recovery**. A
four-agent pipeline that detects revenue at risk, diagnoses root cause, runs a
**bounded** recovery workflow, and writes every decision to an audit trail.
**Razorpay test mode only — no real money, ever.**

## Source-of-truth documents (read before non-trivial work)

| File | Role |
|---|---|
| `plan.md` | The project brief. Track wording, "the bar," build order (§9), stopping rules (§6), data model (§5), approved deviations (§12). This is the file the other docs call "CLAUDE.md" — it was renamed; treat `plan.md` as that brief. |
| `documentation.md` | Exhaustive file/function/endpoint/column/test reference + runbook + build-status table. |
| `architecture.md` | Mermaid diagrams (pipeline, topology, ERD, lifecycle, sequence) + design-decision log. |

**Documentation upkeep is non-negotiable (plan.md §13):** in the *same change*
that adds or alters a file, function, class, endpoint, DB column, enum value,
config key, or command, update **both** `documentation.md` and `architecture.md`
— bump their "Last updated" line and note which build-order step it maps to.
Record any contradiction of the brief under `plan.md` §12 "approved deviations".

**Before planning or building anything for the submission**, invoke the
`build-workflow` skill — it runs the mandatory per-task workflow: check out and
update `plan.md`, verify work against Razorpay's real product / APIs / failure
codes (this project is judged by Razorpay engineers), keep `architecture.md`
diagrams current as you work, then update `documentation.md` and log a brief in
`history.md`.

## Architecture

```
event sources ─▶ Detection ─▶ Diagnosis ─▶ Recovery ─▶ Audit/Reporting
(synthetic batch;   (all four agents read/write ONE Postgres store
 Razorpay webhooks    via backend/app/db/store.py — that store IS the audit trail)
 = stretch)
                          │
              FastAPI (backend/) ─▶ React dashboard (frontend/)
```

- **`backend/app/db/store.py` is the only interface to Postgres.** No raw SQL
  anywhere else. `log_action()` is the *only* way to write `audit_log`, and its
  `reasoning` field is never empty. Input is validated through SQLModel schema
  models (`EventCreate` / `EventUpdate` / `AuditCreate`, all `extra="forbid"`);
  thin `table=True` models don't validate on their own.
- **Event lifecycle:** `detected → diagnosed → action_taken → recovered |
  exception | flagged`. `exception` = honest "couldn't recover, here's why";
  `flagged` = deliberately halted (suspected fraud cluster), never retried.
- **Recovery is root-cause-differentiated and bounded** — each root cause maps to
  a different intervention, and every money action must be explainable (audit
  `reasoning`), bounded (a stopping rule: max attempts, max escalation stage,
  cooldown, amount gate), and gated (human-approval flag above a ₹ threshold).
  See plan.md §2 and §6.
- **The fraud-cluster triage halt** (plan.md §6) is the deliberate "one failure
  handled gracefully" demo moment: Diagnosis re-classifies a matching-signature
  cluster to `flagged` and Recovery refuses to act. The synthetic generator
  seeds this cluster (`build_fraud_cluster`, `event_id = fraud_NN`).
- Metrics (Audit agent) are computed over the **full batch, exceptions
  included** — never cherry-picked.
- Money is `Decimal` / `NUMERIC(14,2)`, quantised to paise. The synthetic
  generator is deterministic per `seed`; `raw_failure_reason` values are **real
  Razorpay error codes**, not invented.

Current build position: steps 1–8 done (four agents, `pipeline.py`, `app/api/*`,
React dashboard) + a multi-provider LLM client and a pgvector RAG knowledge base.
See `documentation.md` §13.

## Commands

Postgres runs as the `pgvector/pgvector` **Docker container** (Docker Desktop +
WSL2 work on this machine — earlier docs saying otherwise were wrong). Start it
at the beginning of every session.

```bash
# repo root
docker compose up -d          # pgvector/pgvector:pg17 on :5432
docker compose down           # stop (keeps data);  down -v wipes the volume
```

`scripts/pg.ps1` (zonky embedded Postgres 17) is a **fallback** for machines
without Docker — it has no extensions, so the RAG layer is disabled there.

```bash
# backend/ (uv-managed; needs Python 3.11+)
uv sync                                          # install deps
cp .env.example .env                             # first time
uv run python -m app.db.store                    # create schema
uv run python -m app.data.generate --reset       # seed synthetic batch (+ fraud cluster)
uv run python -m app.pipeline --reset            # run all four agents -> metrics
uv run uvicorn app.main:app --reload             # API on :8000/docs
uv run pytest -q                                 # all tests (run in chunks on low-RAM boxes)
uv run pytest tests/test_store.py::test_insert_event_defaults   # one test
```

```bash
# frontend/ (React 19 + Vite + Tailwind v4)
npm install
npm run dev                                       # :5173, proxies /api & /health → :8000
npm run build                                     # tsc -b && vite build
npm run lint                                      # oxlint
```

## Environment / gotchas

- No schema migrations (no Alembic). Apply model changes with `reset_db` — i.e.
  re-run `uv run python -m app.data.generate --reset`.
- `docker-compose.yml` (`pgvector/pgvector:pg17`) is the **active** datastore.
  `scripts/pg.ps1` (embedded binary, no extensions → RAG disabled) is the
  fallback. `scripts/init-db.sql` creates the test DBs + `CREATE EXTENSION
  vector` on first container start.
- Container Postgres uses password auth (`revrec`/`revrec`); never expose 5432.
- RAG needs an embeddings backend: `OPENAI_API_KEY` (best) or the bundled local
  `fastembed` model; with neither, retrieval is a no-op and Diagnosis falls
  back to rules + LLM only.
- Tests that call `reset_db` must `session.close()` first (a live session's
  locks block `DROP TABLE` on Postgres).
- Approved deviations from the brief: PostgreSQL (not SQLite), `uv` (not
  pip/requirements.txt), FastAPI + React monorepo (not Streamlit).
