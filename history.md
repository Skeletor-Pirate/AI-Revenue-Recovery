# History — AI Revenue Recovery

Running changelog. Newest entry first. Each entry is a short brief; the full
reference lives in [documentation.md](documentation.md) and
[architecture.md](architecture.md).

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
