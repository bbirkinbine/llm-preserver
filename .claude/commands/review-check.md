---
description: Run the full local quality gate (ruff lint, ruff format, mypy, pytest) before invoking /review. Refuses to declare pass on any failure.
---

This is the pre-`/review` quality gate. It does not declare a feature
"done" — only that the local checks pass. The human still runs `/review`
(and `/security` / `/performance` if relevant) before commit.

Run the steps in order and report results:

1. `uv run ruff check .` — lint
2. `uv run ruff format .` — apply formatting in place (the PostToolUse hook
   formats on every edit, so this is usually a no-op; we run it anyway
   so the gate is reproducible from a fresh clone)
3. `uv run mypy src/` — type check
4. `uv run pytest -x --tb=short` — tests (fail-fast, short tracebacks)
5. `./.claude/hooks/closeout-check.sh` — close-out bookkeeping. Silent
   on any branch that is not `spec-NNNN-<slug>`; on a spec branch it
   refuses while the spec still says `draft`, TODO has no `## Shipped`
   entry for it, `CLAUDE.md` carries no session note, or `docs/cli.md`
   is untouched while `src/llm_preserver/cli/` changed. These belong on
   the feature branch **before** the PR opens — spec 0017 cost a second
   PR for pure bookkeeping because they did not.

If any step fails:

- Do NOT declare the gate passed.
- Show the failing output verbatim (the failing test names, the type
  errors, the lint findings).
- Stop. The human decides whether to fix or accept.

If all steps pass:

- Summarize: which tools ran, how long it took, test count.
- Suggest the next step explicitly:
  - `/review` — always, for an independent code review.
  - `/security` — if the project opted into `security-reviewer` AND the
    diff touches any of the security triggers listed in this project's
    `README.md` ("Opt-in subagents" → `security-reviewer.md`).
  - `/performance` — if the project opted into `performance-reviewer`
    AND the diff touches any of the performance triggers listed in
    `README.md` ("Opt-in subagents" → `performance-reviewer.md`).
- Do NOT commit. The human commits.

If the project doesn't have `src/` and `tests/` directories yet
(early-stage repo), surface that and skip the steps that would error.
