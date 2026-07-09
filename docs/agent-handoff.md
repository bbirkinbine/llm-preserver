# Agent handoff — llm-preserver

> **Purpose.** Operational state for the agent (or future-you) opening
> this repo cold. `CLAUDE.md` covers the *rules*; this file covers the
> *situation* — what's risky right now, what's safe to run unsupervised,
> and how to roll back if a change goes wrong.
>
> Update this file when those facts change. A stale handoff is worse
> than no handoff. Date the "Current state" section every edit.

---

## Current state

The canonical "what's in progress / what's blocked" lives in
`CLAUDE.md` → **Open work / current state**. Don't duplicate it here —
that's two places to drift out of sync. This section holds only the
*operational* detail that doesn't belong in CLAUDE.md:

- Branches in flight and their open PRs.
- Anything deployed, recently rolled back, or mid-migration.
- Transient blockers (a flaky external dependency, a pinned-back
  version waiting on an upstream fix).

As of 2026-07-09: baseline pushed to `main` (private GitHub repo), CI
green, no feature branches. Two Dependabot PRs open (CI-green action
bumps) awaiting Brian. The single gating item — ADR 0001 under review
— and the temporary docs-only-to-main exception are tracked in
`CLAUDE.md` → Open work.

---

## Known risks

Things that have bitten in the past, or are fragile right now and need
extra care. Each entry: *what to watch*, *why it bites*, *how to avoid*.

- The model archive the tool manages is irreplaceable data — a bug or a
  careless "cleanup" that deletes archive contents defeats the entire
  point of the project — never point tests or dev runs at a real
  archive; use tmp dirs, and treat any delete/move of archive paths as
  requiring explicit human approval.

---

## Accepted commands (safe to run unsupervised)

Anything not on this list falls back to the default rule in `CLAUDE.md`:
ask first.

**Read-only:** `git status`, `git diff`, `git log`, `git branch --show-current`,
`rg`, `ls`, `cat`, `find` (non-destructive flags), `uv run pytest`,
`uv run ruff check .`, `uv run mypy src/`.

**Mutating but local-only:** `uv sync`, `uv add <pkg>` (subject to the
`dependency-hygiene` skill), `uv run ruff format .`,
`git switch -c <branch>`, `git switch <existing>`, `git stash`,
`git restore <path>`.

**Never on this list** (always require explicit approval):
`git commit`, `git push` — see CLAUDE.md "Commits and pushes require
explicit approval". Anything matched by
`.claude/hooks/block-destructive.sh`. And the project-specific
high-blast-radius commands: anything that deletes or moves files inside
a real model archive, and any bulk download launched against a real
archive path (large disk/bandwidth impact — human decides when).

---

## Rollback playbook

Concrete commands, not principles.

**Local working copy in a bad state:**

```bash
git stash                  # save changes, return to HEAD
git restore <file>         # discard changes in a single file
git restore .              # discard ALL uncommitted changes
```

**Bad commit on a feature branch (not pushed):**

```bash
git reset --soft HEAD~1    # undo last commit, keep changes staged
```

**Bad commit already pushed (any branch):**

```bash
git revert <sha>           # safe — creates a new commit that undoes it
git push                   # (requires explicit approval — see CLAUDE.md)
```

`git reset --hard` is recoverable via the reflog but still blunt — prefer
`git reset --soft` above. Force-push is gated by the explicit-approval
rule in CLAUDE.md; never reach for it to "clean up" a branch.

**Bad merge to main:** open a revert PR with `gh pr create`, or use
GitHub's "Revert" button on the merged PR. Nothing deploys from this
repo, so the revert is the whole rollback.

**Bad dependency change:**

```bash
git restore pyproject.toml uv.lock
uv sync
```

**Out-of-repo side effects:** the tool writes to the model archive root
it is pointed at (out of repo, user-chosen — e.g. a NAS path or
`~/models/`). There is no automated cleanup for archive contents by
design: recovery from a bad archive write is a human decision, never a
scripted delete.

---

## When X breaks (first thing to try)

- **CI red on a PR** — read the failing job; reproduce locally with
  `/review-check`. Flaky twice → file an issue, don't keep re-running.
- **`uv sync` fails** — delete `.venv/` and retry. Still failing → check
  whether `pyproject.toml` was edited without regenerating `uv.lock`.
- **`pre-commit` blocks every commit** — `git branch --show-current`;
  `no-commit-to-branch` blocks `main`. Branch first.
- **PostToolUse hook fights the editor** — editor's ruff settings differ
  from `pyproject.toml`. Align them — `pyproject.toml` wins.
- **Session won't end its turn** — the Stop hook (`gate-on-stop.sh`) is
  holding it: `src/` has pending changes and ruff/mypy/pytest aren't all
  green. Run `/review-check`, fix the failures. A gate that genuinely
  can't pass is let through on the second attempt (with a warning on
  stderr).
- **PreToolUse deny-list blocks a command you actually need** — confirm
  it's truly unrecoverable; if not, the deny-list pattern is wrong (fix
  it). If it is, run outside the agent session.
- **Hugging Face download fails** — check whether the repo is gated
  (needs an accepted license / auth token) vs. network vs. renamed or
  removed upstream. Gated/removed repos are a product-level signal, not
  just an error to retry.

---

## Out-of-repo dependencies

External systems this repo touches that aren't obvious from the code.
Changes here may need coordinated changes out there.

- Hugging Face Hub: primary model source — anonymous for open repos; a
  user token (env var, never committed) is needed for gated repos. Only
  the human creates/rotates tokens.
- The model archive filesystem (local disk / NAS): the tool's output
  target — see "Known risks"; treat as irreplaceable.
