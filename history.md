# History — AI Revenue Recovery

Running changelog. Newest entry first. Each entry is a short brief; the full
reference lives in [documentation.md](documentation.md) and
[architecture.md](architecture.md).

---

## 2026-09-04 — RAG knowledge base (pgvector + HNSW) for the Diagnosis Agent

- **Did:**
  - **Infra:** discovered Docker Desktop + WSL2 actually work here (the docs
    saying otherwise were wrong). Switched Postgres from the embedded binary to
    the `pgvector/pgvector:pg17` container (`docker-compose.yml`,
    `scripts/init-db.sql` creates the test DBs + `CREATE EXTENSION vector`).
    `scripts/pg.ps1` kept as a no-Docker fallback (RAG disabled there).
  - **`app/llm.py`:** added `embed()` — OpenAI `text-embedding-3-small`
    (`dimensions=384`) if `OPENAI_API_KEY`, else local `fastembed`
    (`all-MiniLM-L6-v2`, 384-d), else `LLMUnavailable`. Plus
    `embeddings_available` / `resolve_embed_provider` / `embed_label`.
  - **`store.py`:** `ResolvedCase` table (`vector(384)` + HNSW cosine index),
    `add_resolved_case` / `nearest_resolved_cases` / `resolved_case_count` /
    `trim_resolved_bucket`; `_enable_vector` sets `VECTOR_ENABLED` and the
    table is skipped when pgvector is absent.
  - **`app/rag.py`:** `retrieve_similar` (embed → nearest → few-shot block),
    `seed_reference_cases` (~20 canonical examples, first run), 
    `index_resolved_cases` (append confidently-classified events; dedup +
    per-bucket cap), `RevRecEmbeddings` (LangChain `Embeddings` wrapper).
  - **`diagnosis.py`:** `run()` retrieves similar cases before the LLM
    fallback and passes them as `rag_context`; payload gains `rag_examples` +
    `similar_case_ids`.
  - **`pipeline.py`:** seed KB before Diagnosis, grow it after Audit.
  - **API:** `GET /api/events/{id}/similar`. **Frontend:** `SimilarCases`
    panel in the decision-trail drawer; `getSimilar` in the data-source
    adapter; `SimilarCase` / `EventSimilarResponse` types.
  - **Tests:** `test_rag.py` (10), `test_api.py` (6); `_offline_embeddings`
    autouse fixture keeps the real model out of the suite; fixed a
    `reset_db` table-ordering bug surfaced by the new table. **134 green.**
  - Deps: `pgvector`, `fastembed`, `numpy`, `langchain-core`.
  - **Not done:** LangGraph (pipeline stays a linear state machine), FAISS /
    dedicated vector store (premature at demo scale), `langchain-postgres`
    PGVector (opaque tables, breaks the store.py-only rule).
- **Verified:** real end-to-end run with `fastembed` — a novel phrasing
  ("the customers bank was completely unreachable during the charge") retrieved
  `bank_downtime` as the top match (0.89). No public source names Razorpay's
  own vector DB; pgvector is the defensible, right-sized choice and we don't
  claim otherwise in the pitch.
- **Docs:** plan.md §12 (RAG deviation + Docker correction); CLAUDE.md
  (commands + gotchas); architecture.md (pipeline diagram, ERD, topology,
  decision log, tech stack); documentation.md (§2/§3/§4/§5/§6/§7/§9/§12/§13 +
  runbook + known issues); AGENTS_CONTRACT.md §5/§8; this entry.
- **Next:** browser pass of the RAG panel on live API; step 9 webhook listener;
  pitch video.

---

## 2026-09-04 — Provider-agnostic LLM (Anthropic / OpenRouter / OpenAI)

- **Did:** New `backend/app/llm.py` — `chat()` / `available()` / `resolve_provider()`
  / `model_label()`. Providers auto-detected `anthropic → openrouter → openai`
  (or forced with `LLM_PROVIDER`); OpenRouter + OpenAI over the OpenAI-compatible
  REST endpoint via `httpx`, Anthropic via its SDK; OpenRouter default model is
  `anthropic/claude-3.7-sonnet`. `diagnosis.claude_classify` and
  `recovery._claude_draft` now route through it — same function names (tests
  still monkeypatch them), same offline fallbacks. `config.py` gained the
  `openrouter_*` / `openai_*` / `llm_provider` settings; `httpx` moved to runtime
  deps. `tests/test_llm.py` (+5). **118 backend tests green.**
- **Why:** the user has an OpenRouter key, not an Anthropic one.
- **Docs:** `AGENTS_CONTRACT.md` §5; documentation.md §3.2/§3.3/§4/§12;
  architecture.md tech-stack + decision log; plan.md §12; `.env.example`.
- **Next:** open the PR (branch pushed; `gh` not installed on this box).

---

## 2026-09-04 — Phase B + C: built and integrated the four-agent pipeline (steps 3–8)

- **Did:** Five builders (parallel, isolated test DBs) implemented against the
  frozen contract; team-lead reviewed each diff and merged.
  - `app/agents/detection.py` (12 tests), `diagnosis.py` (26), `recovery.py`
    (29), `audit.py` (11); wired `app/agents/__init__.py`.
  - `app/pipeline.py` — `run()` chains DET→DIA→REC→AUD, returns the MetricsBlock;
    argparse CLI. `tests/test_pipeline.py` (6): every event terminal, fraud
    cluster flagged + not recovered, metrics over the full batch, honest
    exception list.
  - `app/api/routes.py` — `/api/events`, `/api/events/{id}/audit`, `/api/metrics`,
    `/api/pipeline/run`; mounted in `main.py`. Smoke-tested via `TestClient`.
  - `frontend/src/api/fixtures.json` regenerated from a real seed-42 run (74
    events, 40 exceptions); React dashboard pages (`Overview` / `Queue` /
    `Recovery` / `Exceptions`) built against it via a `dataSource.ts` adapter
    (`VITE_DATA_SOURCE=live` flips to `/api`). `npm run build` + `lint` clean.
  - Generator now backdates `created_at` over 14 days with the fraud cluster in
    one 40-min window (restores the ≤60-min clause in the fraud signature);
    `EventCreate.created_at` optional, `insert_event` honours it.
  - Merge-time contract refinements in `AGENTS_CONTRACT.md` §10: audit derives
    the exception reason from `awaiting_human_approval`; R7 (stage-3 handoff →
    exception).
  - **113 backend tests green** (run in chunks — the batch-reseeding pipeline
    tests OOM a single process on this box).
- **Verified:** Razorpay test-card-details error codes (generator), Agent Studio
  tone (Recovery outreach templates), buildathon "the bar" (metrics block +
  honest exception list + stopping rules + audit trail all present).
- **Docs:** documentation.md §3/§5/§5.1/§12/§13 + runbook; architecture.md §2
  (nodes → done), §7, §8 decision log; plan.md §9 status + §12; this entry.
- **Then (same session):** step 10 done — rewrote `readme.md` as the submission
  README (the-bar mapping, root-cause→intervention table, fraud-cluster demo
  moment, run instructions + example output, "what broke / what we'd do next").
  Committed steps 3–8 as six logical commits on `feat/four-agent-pipeline`.
  API-level end-to-end verified (all four endpoints return correct live pipeline
  data through the Vite proxy); browser DOM pass still owed.
- **Next:** step 9 (Razorpay webhook listener, stretch); browser DOM pass of the
  dashboard on the live API; pitch video.

---

## 2026-09-03 — Phase A: froze the cross-agent contract (pipeline steps 3–6)

- **Did:** Started the agent-team build of plan.md §9 steps 3–8. Added
  `RootCause` `StrEnum` (9 members) to `backend/app/db/store.py` and typed
  `EventUpdate.root_cause: RootCause | None` (DB column unchanged). Corrected
  `app/data/generate.py` `raw_failure_reason` codes to real Razorpay test-mode
  strings and re-seeded. Wrote `backend/app/agents/AGENTS_CONTRACT.md` (frozen
  per-stage I/O, root-cause→intervention map, audit `action` registry,
  stopping-rule constants, fraud-cluster signature, audit `payload` shapes,
  Claude-usage rules, API response contract, file boundaries). Committed
  `frontend/src/api/fixtures.json` (sample of every API shape). Updated
  `test_store.py` (+1 test, 26 pass).
- **Verified:** buildathon page (Track 03 text + "the bar" unchanged); Agent
  Studio launch page (agent tone: plain business English, e.g. "Hi, I noticed
  you left the headphones in your cart…"; note a 4th agent, Cashflow Forecaster,
  now listed); test-card-details doc (real failed-payment codes —
  `insufficient_fund`, `authentication_failed`, `payment_timed_out`,
  `card_number_invalid`, `card_declined`, `gateway_technical_error`).
- **Docs:** documentation.md §3/§5/§5.1/§6/§8/§10/§12/§13; architecture.md
  §4 ERD + §8 decision log; plan.md §12 (failure-code correction) + status;
  this entry.
- **Next:** Phase B0 — spawn the five builders in plan-only mode, consolidate
  their plans, report to the user for the second sign-off gate.

---

## 2026-09-03 — Merged the four frontend skills into one `frontend` skill

- **Did:** Combined `scroll-craft`, `liquid-glass`, `glass-scroll-3d`, and
  `revrec-dashboard` into a single `.claude/skills/frontend/` skill with a new
  router `SKILL.md` (four modes + routing rules). Each former skill moved to
  `.claude/skills/frontend/<name>/` (`git mv`, history preserved) with its
  `SKILL.md` renamed to `GUIDE.md`; all bundled `engine/`, `scripts/`,
  `references/`, `templates/`, `demo/` files kept in place. Repointed
  cross-references: `glass-scroll-3d` and `revrec-dashboard` guides now cite
  `../scroll-craft/GUIDE.md` / `../liquid-glass/GUIDE.md` as sibling dirs instead
  of "the X skill". Tooling/process only — no plan.md §9 build-order step.
- **Verified:** No external Razorpay lookup needed (no product/API surface
  touched).
- **Docs:** documentation.md §3.1 + header bumped. architecture.md unchanged
  (no diagram touches this). No plan.md §12 deviation.
- **Next:** Resume build-order step 3 — `backend/app/agents/detection.py`.

---

## 2026-09-03 — Added the `build-workflow` skill; absorbed `razorpay-source-check`

- **Did:** Created `.claude/skills/build-workflow/SKILL.md` — a mandatory
  per-task workflow wrapper enforcing the order: (0) check out and update
  `plan.md` every task, (1) verify against real Razorpay sources — the full
  link list + "how to use the sources" from the old `razorpay-source-check`
  skill are now embedded here as Step 1, (2) keep `architecture.md` data-flow +
  Mermaid diagrams and decision log current *in the same change*, (3) update
  `documentation.md` when done, (4) append a dated brief to `history.md`.
  Deleted `.claude/skills/razorpay-source-check/` (`git rm`) and repointed all
  references: `CLAUDE.md`, `plan.md` §0, `readme.md` §0, `glass-scroll-3d`
  SKILL.md, `documentation.md` §3.1. Created this `history.md`. Tooling/process
  only — no plan.md §9 build-order step.
- **Verified:** No external Razorpay lookup needed (no product/API surface
  touched).
- **Docs:** documentation.md §3.1 + header bumped. architecture.md unchanged.
  No plan.md §12 deviation.
- **Next:** Resume build-order step 3 — `backend/app/agents/detection.py`.
