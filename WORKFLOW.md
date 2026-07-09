# Workflow — how to use this scaffolding

Companion to `CLAUDE.md`. `CLAUDE.md` is what the agent reads every turn
(the rules); this file is the human's step-by-step — what to run, in
order, with a one-line reason for each step.

The whole thing is five phases: **Spec → Plan → Test-first → Implement →
Verify.** Each slash command runs one phase and stops, so you stay in
control. New to the terms? A *spec* is a short design note; a *subagent*
is a fresh agent with its own clean context.

## Day zero (once per project)

1. **Run bootstrap.** `bash path/to/agentic-scaffold/python/bootstrap.sh`
   (wherever you cloned this repo) — drops the default `--python-core`
   scaffolding into your repo. Use `--minimal` for a thinner starter,
   `--full` for the author's full workflow bundle, and `--strict-hooks`
   only if you want edit hooks to run lint/type checks and the Stop gate.
2. **Fill the placeholders.** `rg '\{\{' .`, then replace every `{{...}}`
   — the agent reads `CLAUDE.md` every turn, so a leftover placeholder
   misleads it. One placeholder is the starter package directory
   `src/{{PACKAGE_NAME}}/` — rename it to your package name. Hand-edit the
   `CLAUDE.md` content yourself (description, don't-touch list,
   conventions); don't have the agent regenerate it — AI-written context
   files measurably hurt agent performance (see `python/README.md` →
   "Don't"). Mechanical fills like the project name in `pyproject.toml`
   are fine to delegate.
3. **Set up git identity and GitHub.** `git config user.email` must be
   your GitHub noreply address — it is baked into the first commit
   forever. Then add the README AI-acknowledgement line and fill the
   GitHub "About" sidebar. (A fuller checklist, including the
   private→public hygiene scrub, lives in the agentic-scaffold repo as
   `new-project-checklist.md` — it is not copied into your project.)
4. **Decide opt-in reviewers now.** Security and performance reviewers
   are off by default; turn them on if the project needs them (see
   `python/README.md` → "Opt-in subagents").
5. **Install the dev tools.** `uv sync && uv run pre-commit install` —
   dependencies plus the commit guard that keeps work off `main`.
6. **Create the GitHub issue labels** the issue forms use: `feature`,
   `bug`, `spec-needed`, `triage` (e.g. `gh label create spec-needed`).
   Skip this in local-only mode; specs then use the next local number
   instead of a GitHub issue number.
7. **Optional — `/product-spec` if installed.** Interviews you and writes
   `docs/specs/0000-product.md`, the product-level "what is this, and who
   is it for." Skip it for a small project. See "Authoring the product
   spec (PRD): the interview" below for when and how.

## Every feature (the loop)

Run these in order. Steps marked *optional* are skippable when the answer
is already obvious.

1. **Create a GitHub issue** unless the repo is explicitly local-only.
   An issue is a work item — like a Jira or Linear ticket, not just a bug
   report (`feature` is one of its labels). Its number names the spec,
   the branch, and the PR — one id ties them together. In local-only mode,
   `/spec` uses the next local `docs/specs/NNNN-*.md` number instead.
2. **`/spec <name>`** — write a short spec (goal, success criteria,
   non-goals), or — if you have already discussed the feature in this
   session — have `/spec` draft it from that discussion. Either way you
   review and edit it before moving on; this is the source of truth every
   later step checks against.
   - *Optional, if installed:* `/scope-check` before (fuzzy goal),
     `/clarify` after (open questions) to sharpen it.
   - See "Authoring a spec: three styles" below for the write-it-yourself,
     discuss-then-draft, and let-the-agent-interview-you flows.
3. **Make a branch** named `<issue#>-<slug>` in GitHub-backed mode, or
   `spec-NNNN-<slug>` / `<type>/<slug>` in local-only mode. Never build on
   `main`.
4. **`/plan`** — the agent lists the files to touch and the order.
   Review it before any code; a wrong approach is cheap to fix here.
5. **`/test-first`** — writes failing tests from the spec. Tests written
   *after* the code just rubber-stamp whatever you built.
   - *Optional, if installed:* `/analyze` confirms every success criterion has a test.
6. **Implement.** Write the minimum code to make the tests pass.
7. **`/review-check`** — runs ruff + mypy + pytest. Must be green before
   moving on.
8. **`/review`** (and `/review-adversarial` on bigger changes, if
   installed) — a fresh agent reads the diff against the spec, catching
   what the gate can't.
   - *Optional:* `/security` and `/performance` if you installed them.
   - *LLM/AI-surface projects only, if installed:* `/eval` runs the eval
     suite — the quality gate for non-deterministic output that
     `/review-check` can't assert. Most projects ship no LLM surface and
     skip this entirely; install the full/advanced docs for the detailed
     decision rule.
9. **Commit, then open the PR.** You write the commit message. In
   GitHub-backed mode, the PR body says `Closes #<issue>` so merging
   closes the issue; local-only mode omits the closing keyword.

## The planning artifacts, broad to narrow

Three documents plan the work, and they nest — broadest to narrowest:

1. **Product spec (PRD)** — `docs/specs/0000-product.md`. The product
   itself: who it is for, what success looks like, what would kill it.
   Standing context that everything below links up to.
2. **ADR** — `docs/adr/NNNN-*.md`. A cross-cutting technical decision that
   several features inherit and is costly to reverse. Sits between the
   product and the feature work that lives under the decision.
3. **Spec** — `docs/specs/NNNN-*.md`. One unit of work. Links up to the
   product spec, and to any ADR its approach depends on.

This is a *hierarchy*, not a mandatory pipeline. Most features are just
"product spec as standing context → spec → build" with **no ADR at all** —
the product spec is written once (day zero, or when the backlog outgrows
your head) and refreshed rarely, ADRs appear only on Large cross-cutting
work, and specs are the everyday artifact. The map form of this nesting is
in [`docs/workflow-diagram.md`](docs/workflow-diagram.md). The three
authoring guides below run in that broad-to-narrow order.

### Authoring the product spec (PRD): the interview

**When to reach for it.** The product spec (`docs/specs/0000-product.md`)
is the PRD-level layer — the problem and who it is *for* and *not for*,
success metrics, kill criteria, product non-goals, constraints. Feature
specs link up to it instead of restating product rationale. Write it when
the backlog outgrows your head, or before any multi-spec autonomous run; a
small project can lean on the README purpose paragraph until then. One
file, reserved number `0000`, status `evergreen` — revised in place, never
shipped or closed.

**The flow is an interview, by design.** Unlike `/spec` and `/adr` (below),
`/product-spec` does not draft from inference — a blank PRD template gets
ignored and an AI-guessed one is worse than none, so it asks and you
answer:

```text
/product-spec        # create mode: 7 questions, one at a time — you answer
                     # → writes docs/specs/0000-product.md from your answers
...
/product-spec        # later: refresh mode — asks only what has gone stale (max 3),
                     #        or says "product spec is current" and stops
```

Two modes: **create** (file absent — the seven questions) and **refresh**
(file exists — it compares against the open issues and asks only about
sections that are missing, vague, or contradicted). You can hand-write the
file instead, but the interview exists because that is what reliably gets
it written in practice. `/product-spec` installs with `--python-core` and `--full`.

### Authoring an ADR: when, and two styles

**When to reach for an ADR.** An ADR (`docs/adr/NNNN-<slug>.md`) records a
*cross-cutting technical decision* — one that several features inherit and
that is costly to reverse (a storage engine, an async/sync boundary, a
public API shape, an auth model, a serialization format). If a decision
affects only the feature in front of you, it belongs in that feature
spec's `## Sketch`, not an ADR. Mostly **Large** work. ADRs are numbered
independently of issues (the next number, not an issue number). The full
spec-vs-ADR decision table is in
[`docs/adr/README.md`](docs/adr/README.md).

`/adr` is one-shot and stops for your review — there is no separate
interview command, because the design discussion *is* the interview. Two
styles:

**1. Write it yourself.** Run `/adr "<decision title>"`, get the Context /
Decision / Consequences / Alternatives skeleton, fill it in.

**2. Discuss, then draft.** Weigh the options with the agent — constraints,
trade-offs, what argues against each — then run `/adr`. It drafts the four
sections from that discussion and marks anything it guessed with
`<!-- assumption -->`.

```text
You:  Postgres or SQLite for the event store? ... (weigh durability,
      concurrency, ops cost)
...   (back-and-forth)
You:  /adr "event store engine"
AI:   writes docs/adr/00NN-event-store-engine.md from the discussion, then stops
```

Either way you review and edit the rationale before the decision is acted
on. An accepted ADR changes later by being *superseded*, not edited — a
new ADR that links back to it (see [`docs/adr/README.md`](docs/adr/README.md)).

### Authoring a spec: three styles

The spec is the one artifact worth getting right — every later step checks
against it. `/spec` is one-shot (it writes the file and stops for your
review); it is not itself an interview. Pick the style that matches how
much you have already thought the feature through:

**1. You write it.** You already know what you want. Run `/spec <name>`,
get the skeleton, and fill `## Goal` / `## Success criteria` /
`## Non-goals` by hand.

**2. Discuss, then draft.** Talk the feature through with the agent first
— this is ordinary conversation, not a command — then run `/spec`. It
drafts the spec from that discussion and marks anything it had to guess
with `<!-- assumption -->`.

```text
You:  Add per-account user prefs — theme + default timezone.
      Skip cross-device sync for now.
...   (back-and-forth as needed)
You:  /spec add-user-prefs
AI:   writes docs/specs/00NN-add-user-prefs.md from the discussion, then stops
```

**3. Let the agent interview you.** The goal is fuzzy and you want the
questions driven for you. `/scope-check` and `/clarify` are the turn-by-turn
Q&A passes (one question at a time); `/spec` sits between them:

```text
/scope-check add-user-prefs   # AI asks 5 forcing questions, you answer
/spec add-user-prefs          # AI drafts the spec from those answers
/clarify                      # AI asks up to 5 more, folds each answer back in
/plan
```

(`/scope-check` and `/clarify` install with `--python-core` and `--full`,
not `--minimal`.) Whichever style you use, `/spec` stops after writing —
you review and edit before `/plan`. The spec stays human-owned; drafting
just gets you to a reviewable first pass faster.

## Scale to the task

Don't run the full loop on tiny work.

| Task | Do |
| --- | --- |
| Trivial — rename, typo, ≤10 lines | Just do it. Skip spec/plan; branch optional. |
| Small — one function | Branch + one-sentence spec; `/test-first`; skip `/plan`. |
| Medium — 3–10 files | The full loop above. |
| Large — new subsystem | Capture the cross-cutting technical decision in an ADR (`/adr`) first, then split into medium pieces, one issue + spec each. |

A throwaway script needs none of this — just write the code.

## Phase handoff (multi-day features)

A feature that spans sessions reviews badly when one session carries all
the spec, plan, test, and implementation context. At a phase boundary,
append a `## Phase handoff` block to the spec, run `/clear`, and resume
fresh. Boundaries worth a reset: after `/plan` is approved, and after
`/review-check` is green. Section shape: `docs/specs/README.md`.

## Session hygiene

Context is the resource the whole loop runs on; treat it deliberately.

- **Audit what loads by default.** Once per project (and after adding
  any MCP server or skill), open a fresh empty session and run
  `/context` — it shows what is consuming context before you have done
  anything. A stale MCP server or rarely-used skill that loads every
  session is pure overhead; scope it to the projects that use it.
- **Watch the percentage, not the limit.** The status line shows context
  usage every turn. Output quality degrades well before the hard limit
  — practitioners commonly report a soft zone around 40–50% — so treat
  a rising number as the cue to reach a phase boundary, not as budget
  still available.
- **Compact deliberately, and not forever.** Prefer a manual `/compact`
  at a natural boundary over waiting for automatic compaction at the
  limit. But repeated compaction of one long thread accumulates drift:
  after a compact or two, write the `## Phase handoff` block into the
  spec and `/clear` instead. The spec is the durable memory; the
  conversation is not.
- **Audit memories occasionally.** `/memory` lists what Claude has
  auto-remembered about the project. A stale entry steers every future
  session; prune it like you would a wrong line in `CLAUDE.md`.

## The completion ladder

"The agent said done, but it wasn't" has layered fixes — use more the
longer nobody is watching:

1. Success criteria written as a runnable command.
2. `/goal` — a completion check run by a separate evaluator.
3. The Stop hook, if `--strict-hooks` is enabled — blocks ending a turn on a red gate.
4. A fresh-context `/review` — the only rung that catches "gate green but
   feature wrong."

Detail and the autonomy tiers: `docs/parallel-agents.md` (installed with
`--full` / `--advanced-docs`). (`/goal`, `/loop`, and `/sandbox` are
Claude Code built-ins, not commands in `.claude/commands/`.)

## Good to know

- **`CLAUDE.md` is re-read every turn** — edit it mid-feature to
  course-correct (e.g. add a path to the don't-touch list).
- **Subagents don't see your chat.** Put anything the reviewer needs in
  the spec, not in a message.
- **Specs are permanent** — they are the design log, not deleted after a
  feature ships.
- **CI is the gate you can't skip.** Local hooks can be bypassed; a red
  PR is not done.

## Going deeper

Some files below depend on the bootstrap profile. `--minimal` installs the
core loop; `--python-core` adds ADR/status/workflow docs; `--full` or
`--advanced-docs` adds the advanced doctrine docs.

- [`docs/project-types.md`](docs/project-types.md) — the orientation map:
  flavors, profiles, the capability matrix, and when to reach for each
  agent, skill, and command. Start here if you are new to the scaffolding.
- [`docs/workflow-diagram.md`](docs/workflow-diagram.md) — the same loop as
  a visual map (Mermaid diagrams).
- `docs/specs/README.md` — spec numbering, local-only mode, the product spec, section shapes.
- `docs/adr/README.md` — architecture decision records: when a choice is
  cross-cutting and costly to reverse, log the decision and its rationale
  (spec-vs-ADR table, numbering, template). Mostly for Large work.
- `docs/evals.md` — what "eval" means (two senses): the review/analyze
  work you already do for any project, plus the opt-in product-eval layer
  for a product that contains an LLM/AI surface.
- `docs/llm-product.md` — building that LLM/AI surface: the single call
  seam, testing without live API calls, prompt versioning, model pinning.
- `docs/parallel-agents.md` — autonomy tiers, worktrees, unattended runs.
- `docs/agent-handoff.md` — operational runbook: risks, rollback, "when X breaks."
- `CLAUDE.md` + `.claude/rules/` — the rules the agent follows every turn.
